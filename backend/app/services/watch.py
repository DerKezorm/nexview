"""„Sag mir Bescheid" - auf einen Titel warten, ohne ihn anzufragen.

Der Fall, den es bisher nicht gab: Ein Titel ist schon von jemand anderem
angefragt. Anfragen laesst er sich damit nicht mehr - ``requests_service``
antwortet mit „wurde bereits angefragt" -, und danach hoert der Zweite nie
wieder etwas davon. Er erfaehrt nicht, dass der Titel angekommen ist, obwohl
genau das seine Frage war.

Zwei Verhaltensweisen, weil Film und Serie zwei verschiedene Dinge sind:

* Ein **Film** wird einmal gemeldet und ist erledigt. Danach gibt es nichts
  mehr zu sagen, also faellt die Vormerkung weg.
* Eine **Serie** wird verfolgt, dauerhaft und ueber alle Staffeln. Gemeldet
  wird jede neue Folge - meistens wartet man genau darauf, auf die neueste.
  Sie endet erst, wenn jemand sie beendet.

**Immer gebuendelt.** Laedt ein Staffelpaket mit acht Folgen durch, ist das
*eine* Meldung ueber acht Folgen. Acht Meldungen in derselben Minute waeren
der schnellste Weg, jemanden dazu zu bringen, Benachrichtigungen abzuschalten.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    MediaType,
    NotificationType,
    SeasonProgress,
    TitleWatch,
    User,
    utcnow,
)
from . import library, media, notify
from .settings_service import AppSettings
from .tmdb import TmdbError
from . import logs

logger = logging.getLogger("nexview.watch")


# --- Vormerken und beenden --------------------------------------------------


def vorgemerkt(db: Session, user: User, media_type: MediaType, tmdb_id: int) -> bool:
    return db.scalar(
        select(TitleWatch.id).where(
            TitleWatch.user_id == user.id,
            TitleWatch.media_type == media_type,
            TitleWatch.tmdb_id == tmdb_id,
        )
    ) is not None


def vormerken(
    db: Session,
    user: User,
    media_type: MediaType,
    tmdb_id: int,
    *,
    title: str = "",
    poster_url: str | None = None,
) -> TitleWatch:
    """Vormerken - oder die bestehende Vormerkung zurueckgeben.

    Zweimal derselbe Klick ist kein Fehler, sondern ein Doppelklick. Er
    hinterlaesst dieselbe eine Zeile.
    """
    vorhanden = db.scalar(
        select(TitleWatch).where(
            TitleWatch.user_id == user.id,
            TitleWatch.media_type == media_type,
            TitleWatch.tmdb_id == tmdb_id,
        )
    )
    if vorhanden is not None:
        return vorhanden

    eintrag = TitleWatch(
        user_id=user.id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        title=title,
        poster_url=poster_url,
    )
    db.add(eintrag)
    db.flush()
    return eintrag


def beenden(db: Session, user: User, media_type: MediaType, tmdb_id: int) -> int:
    ergebnis = db.execute(
        delete(TitleWatch).where(
            TitleWatch.user_id == user.id,
            TitleWatch.media_type == media_type,
            TitleWatch.tmdb_id == tmdb_id,
        )
    )
    return ergebnis.rowcount or 0


def meine(db: Session, user: User) -> list[TitleWatch]:
    return list(
        db.scalars(
            select(TitleWatch)
            .where(TitleWatch.user_id == user.id)
            .order_by(TitleWatch.created_at.desc())
        )
    )


def wartende(db: Session, media_type: MediaType, tmdb_id: int) -> list[TitleWatch]:
    """Wer wartet auf diesen Titel? - fuer die Meldung und fuer die Freigabe."""
    return list(
        db.scalars(
            select(TitleWatch).where(
                TitleWatch.media_type == media_type, TitleWatch.tmdb_id == tmdb_id
            )
        )
    )


# --- Der Merkposten je Staffel ---------------------------------------------


def _nummern(roh: str) -> set[int]:
    return {int(teil) for teil in roh.split(",") if teil.strip().isdigit()}


def _als_text(nummern: set[int]) -> str:
    return ",".join(str(n) for n in sorted(nummern))


def _spanne(nummern: set[int]) -> str:
    """"1,2,3,7" wird zu "1-3, 7" - lesbar statt vollstaendig.

    Acht Folgen einzeln aufzuzaehlen ergibt eine Meldung, die niemand liest.
    Zusammenhaengendes wird zusammengezogen.
    """
    if not nummern:
        return ""
    sortiert = sorted(nummern)
    gruppen: list[list[int]] = [[sortiert[0]]]
    for nummer in sortiert[1:]:
        if nummer == gruppen[-1][-1] + 1:
            gruppen[-1].append(nummer)
        else:
            gruppen.append([nummer])
    return ", ".join(
        str(g[0]) if len(g) == 1 else f"{g[0]}-{g[-1]}" for g in gruppen
    )


# --- Der Waechter -----------------------------------------------------------


async def pruefen(db: Session, settings: AppSettings) -> int:
    """Einmal nachsehen, ob zu vorgemerkten Titeln etwas angekommen ist.

    Laeuft neben dem Status-Poller und **nicht** in ihm: Der haengt an
    Anfragen und meldet, wenn *deine* Anfrage fertig wird. Eine Vormerkung hat
    keine Anfrage dahinter, also treibt ihn nichts an.

    Gibt zurueck, wie viele Meldungen hinausgegangen sind.
    """
    offen = list(db.scalars(select(TitleWatch)))
    if not offen:
        return 0

    # Einmal alle Empfaenger holen statt je Zeile - zehn Wartende auf denselben
    # Titel waeren sonst zehn Abfragen fuer dieselben Konten.
    konten = {
        konto.id: konto
        for konto in db.scalars(
            select(User).where(User.id.in_({w.user_id for w in offen}))
        )
    }

    filme = {w.tmdb_id for w in offen if w.media_type == MediaType.movie}
    serien = {w.tmdb_id for w in offen if w.media_type == MediaType.tv}

    gemeldet = 0
    if filme:
        gemeldet += await _filme_pruefen(db, settings, offen, konten, filme)
    for tmdb_id in serien:
        gemeldet += await _serie_pruefen(db, settings, offen, konten, tmdb_id)

    # ⚠️ **Immer** festschreiben, nicht nur wenn etwas gemeldet wurde.
    #
    # Der erste Durchgang meldet absichtlich nichts - er haelt nur fest, was
    # gerade dasteht. Wird dieser Ausgangsstand nicht gespeichert, ist beim
    # naechsten Durchgang wieder alles "erste Begegnung", und es gaebe **nie**
    # eine Meldung. Ein Test haelt genau das fest.
    db.commit()
    return gemeldet


async def _filme_pruefen(
    db: Session,
    settings: AppSettings,
    offen: list[TitleWatch],
    konten: dict[int, User],
    tmdb_ids: set[int],
) -> int:
    """Ein vorgemerkter Film ist da - einmal melden, dann ist es erledigt."""
    try:
        bestand = await library.movie_library(settings)
    except Exception as fehler:  # noqa: BLE001 - der Waechter darf nie sterben
        logger.warning("Movie library not available for watches: %s", fehler)
        return 0

    gemeldet = 0
    for tmdb_id in tmdb_ids:
        eintrag = bestand.get(tmdb_id)
        if eintrag is None or not getattr(eintrag, "has_file", False):
            continue

        wartend = [
            w for w in offen if w.media_type == MediaType.movie and w.tmdb_id == tmdb_id
        ]
        for nummer, vormerkung in enumerate(wartend):
            konto = konten.get(vormerkung.user_id)
            if konto is None:
                continue
            notify.create(
                db,
                user=konto,
                kind=NotificationType.watch_ready,
                message_key="notifications.watchReady",
                title=vormerkung.title or getattr(eintrag, "title", ""),
                # Die Kanaele melden das **Ereignis**, nicht den Empfaenger:
                # Warten drei Leute auf denselben Film, soll im Topic einmal
                # stehen "der Film ist da" und nicht dreimal.
                broadcast=nummer == 0,
            )
            db.delete(vormerkung)
            gemeldet += 1
    return gemeldet


async def _serie_pruefen(
    db: Session,
    settings: AppSettings,
    offen: list[TitleWatch],
    konten: dict[int, User],
    tmdb_id: int,
) -> int:
    """Neue Folgen einer vorgemerkten Serie - gebuendelt je Staffel."""
    vormerkungen = [
        w for w in offen if w.media_type == MediaType.tv and w.tmdb_id == tmdb_id
    ]
    if not vormerkungen:
        return 0

    try:
        detail = await media.full_detail(db, settings, "tv", tmdb_id)
    except TmdbError as fehler:
        logger.warning(
            "Series detail not available for watch %s: %s", tmdb_id, logs.kennung(fehler)
        )
        return 0

    try:
        vorhanden = await library.episode_availability(
            settings,
            detail.tvdb_id,
            detail.title,
            jahr=library.jahr_aus(detail.release_date),
        )
    except Exception as fehler:  # noqa: BLE001
        logger.warning("Episode status not available for watch %s: %s", tmdb_id, fehler)
        return 0

    staende = {
        zeile.season_number: zeile
        for zeile in db.scalars(
            select(SeasonProgress).where(SeasonProgress.tmdb_id == tmdb_id)
        )
    }

    # Alles Neue dieser Runde in **einer** Meldung - siehe Modulkopf.
    neu_je_staffel: dict[int, set[int]] = {}
    for staffel, folgen in vorhanden.items():
        zeile = staende.get(staffel)
        if zeile is None:
            # Erste Begegnung: Der heutige Stand ist der Ausgangspunkt, nicht
            # eine Meldung ueber zwanzig Folgen, die laengst dalagen.
            db.add(
                SeasonProgress(
                    tmdb_id=tmdb_id, season_number=staffel, episodes=_als_text(folgen)
                )
            )
            continue

        vorher = _nummern(zeile.episodes)
        dazu = folgen - vorher
        if dazu:
            neu_je_staffel[staffel] = dazu
        if folgen != vorher:
            zeile.episodes = _als_text(folgen)
            zeile.updated_at = utcnow().replace(tzinfo=None)

    if not neu_je_staffel:
        return 0

    text = "; ".join(
        f"S{staffel}: {_spanne(folgen)}"
        for staffel, folgen in sorted(neu_je_staffel.items())
    )

    gemeldet = 0
    for nummer, vormerkung in enumerate(vormerkungen):
        konto = konten.get(vormerkung.user_id)
        if konto is None:
            continue
        notify.create(
            db,
            user=konto,
            kind=NotificationType.watch_episodes,
            message_key="notifications.watchEpisodes",
            # Die Textbausteine tragen bewusst keine Platzhalter, deshalb
            # stehen die Folgen im Titel: "Andor - S2: 1-8". Nur so sieht man
            # in der Glocke, worum es geht, ohne die Meldung zu oeffnen.
            title=f"{vormerkung.title or detail.title} - {text}",
            broadcast=nummer == 0,
        )
        gemeldet += 1
    # Die Vormerkung bleibt: Eine Serie verfolgt man, bis man aufhoert.
    return gemeldet
