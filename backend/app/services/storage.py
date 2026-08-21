"""Wer belegt wie viel Platz - Erfassung und Kontostand.

Stufe 1 von dreien: **nur messen und anzeigen.** Hier wird nichts begrenzt,
nichts blockiert und nichts geloescht. Das kommt erst in Stufe 2 und 3.

Die Rechnung ist eine bewusste Fiktion: Speicherplatz wird nicht *pro Person*
verbraucht, sondern gemeinsam belegt. Damit sie ehrlich bleibt, gilt eine
klare Definition - ein Konto deckt, was jemand ins Haus geholt hat **und noch
beansprucht**. Alles andere ist Hausbestand und zaehlt bei niemandem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    MediaRequest,
    MediaServerLibraryItem,
    MediaType,
    QualityTier,
    RequestStatus,
    StorageEntry,
    StorageState,
    User,
    utcnow,
)
from .arr import ArrError
from .radarr import LibraryEntry as MovieEntry
from .settings_service import AppSettings
from .sonarr import LibraryEntry as SeriesEntry
from . import library

logger = logging.getLogger("nexview.storage")

# Welche Anfragen einen Posten ueberhaupt zugerechnet bekommen koennen.
# Bewusst dieselbe Auswahl wie beim Stueck-Kontingent minus der Zustaende, in
# denen noch gar keine Datei existieren kann.
ZURECHENBAR = (
    RequestStatus.approved,
    RequestStatus.searching,
    RequestStatus.downloaded,
)


def schluessel(
    media_type: MediaType | str,
    tier: QualityTier | str,
    *,
    tmdb_id: int | None = None,
    tvdb_id: int | None = None,
    season: int | None = None,
) -> str | None:
    """Die eindeutige Kennung eines Postens.

    Filme ueber die TMDB-Nummer (die kennt Radarr), Serien ueber die
    TVDB-Nummer (die kennt Sonarr). Nicht mischen: Sonst entstuende derselbe
    Titel zweimal, je nachdem welche Quelle ihn zuerst gemeldet hat.

    Gibt ``None`` zurueck, wenn die noetige Nummer fehlt - dann laesst sich der
    Posten nicht verlaesslich fuehren und wird uebersprungen.
    """
    art = media_type.value if isinstance(media_type, MediaType) else str(media_type)
    stufe = tier.value if isinstance(tier, QualityTier) else str(tier)
    if art == MediaType.movie.value:
        return f"movie:{stufe}:tmdb:{tmdb_id}" if tmdb_id else None
    if not tvdb_id:
        return None
    return f"tv:{stufe}:tvdb:{tvdb_id}:s{season if season is not None else 0}"


@dataclass(frozen=True)
class Kontostand:
    """Was ein Nutzer belegt."""

    used_bytes: int
    items: int
    # Wieviel davon abgegeben ist und auf eine Entscheidung wartet. Zaehlt
    # weiter mit - sonst waere Abgeben ein Freifahrtschein.
    pending_bytes: int = 0


@dataclass(frozen=True)
class Posten:
    """Eine Zeile fuer die Anzeige."""

    id: int
    media_type: str
    tier: str
    tmdb_id: int | None
    tvdb_id: int | None
    season: int | None
    title: str
    size_bytes: int
    state: str
    measured_at: datetime


def kontostand(db: Session, user_id: int) -> Kontostand:
    """Belegter Platz eines Nutzers.

    Hausbestand zaehlt ausdruecklich **nicht** mit - er gehoert niemandem.
    """
    zeilen = db.execute(
        select(
            func.coalesce(func.sum(StorageEntry.size_bytes), 0),
            func.count(StorageEntry.id),
            StorageEntry.state,
        )
        .where(
            StorageEntry.user_id == user_id,
            StorageEntry.state.in_((StorageState.owned, StorageState.pending)),
        )
        .group_by(StorageEntry.state)
    ).all()

    gesamt = sum(int(zeile[0]) for zeile in zeilen)
    anzahl = sum(int(zeile[1]) for zeile in zeilen)
    wartend = sum(
        int(zeile[0]) for zeile in zeilen if zeile[2] == StorageState.pending
    )
    return Kontostand(used_bytes=gesamt, items=anzahl, pending_bytes=wartend)


def posten_fuer(db: Session, user_id: int) -> list[Posten]:
    """Alles, was ein Nutzer belegt - das Groesste zuerst.

    Die Reihenfolge ist der eigentliche Zweck der Liste: Wer Platz schaffen
    soll, muss zuerst sehen, wo der Platz steckt.
    """
    zeilen = db.scalars(
        select(StorageEntry)
        .where(
            StorageEntry.user_id == user_id,
            StorageEntry.state.in_((StorageState.owned, StorageState.pending)),
        )
        .order_by(StorageEntry.size_bytes.desc())
    ).all()
    return [_als_posten(zeile) for zeile in zeilen]


def hausbestand(db: Session) -> Kontostand:
    """Was dem Haus gehoert - also niemandem persoenlich."""
    summe, anzahl = db.execute(
        select(
            func.coalesce(func.sum(StorageEntry.size_bytes), 0),
            func.count(StorageEntry.id),
        ).where(StorageEntry.state == StorageState.house)
    ).one()
    return Kontostand(used_bytes=int(summe), items=int(anzahl))


def verteilung(db: Session) -> list[tuple[int | None, Kontostand]]:
    """Wer belegt wieviel - fuer die Admin-Uebersicht, das Groesste zuerst.

    ``None`` als Nutzer steht fuer den Hausbestand.

    **Jedes aktive Konto steht in der Liste, auch mit null Bytes.** Wer nur
    die Belegten zeigt, laesst den Betrachter raetseln, warum jemand fehlt -
    "hat nichts" und "wird nicht erfasst" saehen gleich aus.
    """
    gemessen = {
        zeile[0]: Kontostand(used_bytes=int(zeile[1]), items=int(zeile[2]))
        for zeile in db.execute(
            select(
                StorageEntry.user_id,
                func.coalesce(func.sum(StorageEntry.size_bytes), 0),
                func.count(StorageEntry.id),
            ).group_by(StorageEntry.user_id)
        ).all()
    }

    ergebnis: list[tuple[int | None, Kontostand]] = []
    for user_id in db.scalars(select(User.id).where(User.is_active.is_(True))).all():
        ergebnis.append((user_id, gemessen.pop(user_id, Kontostand(0, 0))))

    # Was uebrig bleibt: der Hausbestand (None) und Posten geloeschter Konten,
    # deren Nutzer-Id per ON DELETE SET NULL ohnehin schon auf None steht.
    for user_id, stand in gemessen.items():
        ergebnis.append((user_id, stand))

    ergebnis.sort(key=lambda paar: paar[1].used_bytes, reverse=True)
    return ergebnis


def _als_posten(zeile: StorageEntry) -> Posten:
    return Posten(
        id=zeile.id,
        media_type=zeile.media_type.value,
        tier=zeile.tier.value,
        tmdb_id=zeile.tmdb_id,
        tvdb_id=zeile.tvdb_id,
        season=zeile.season,
        title=zeile.title,
        size_bytes=zeile.size_bytes,
        state=zeile.state.value,
        measured_at=zeile.measured_at,
    )


# ---------------------------------------------------------------- Abgleich


@dataclass
class _Gemessen:
    """Ein Posten, wie ihn die Dienste gerade melden."""

    key: str
    media_type: MediaType
    tier: QualityTier
    tmdb_id: int | None
    tvdb_id: int | None
    season: int | None
    title: str
    size_bytes: int


@dataclass(frozen=True)
class Ergebnis:
    """Was ein Abgleich bewirkt hat - fuer Protokoll und Anzeige."""

    neu: int = 0
    aktualisiert: int = 0
    entfernt: int = 0
    gewachsen: int = 0
    erster_lauf: bool = False


async def abgleichen(db: Session, settings: AppSettings) -> Ergebnis:
    """Belegung neu erfassen.

    **Erst holen, dann schreiben.** SQLite laesst genau einen Schreiber zu;
    bliebe eine Schreib-Transaktion offen, waehrend auf Radarr oder Sonarr
    gewartet wird, wartet jede Anfrage eines Nutzers mit. Deshalb laufen alle
    Abfragen zuerst und die Datenbank wird danach in einem kurzen Zug
    angefasst - dasselbe Vorgehen wie im Status-Abgleich.
    """
    gemessen = await _erfassen(db, settings)
    return _schreiben(db, gemessen)


async def _erfassen(db: Session, settings: AppSettings) -> dict[str, _Gemessen]:
    """Alle Groessen einsammeln - reine Leserei, kein Schreiben."""
    gemessen: dict[str, _Gemessen] = {}

    for stufe in (QualityTier.standard, QualityTier.uhd):
        if settings.arr_configured("movie", stufe.value):
            try:
                for tmdb_id, eintrag in (
                    await library.movie_library(settings, stufe.value)
                ).items():
                    _film_aufnehmen(gemessen, stufe, tmdb_id, eintrag)
            except ArrError as fehler:
                logger.warning(
                    "Radarr (%s) nicht erreichbar, Groessen bleiben wie sie waren: %s",
                    stufe.value,
                    fehler.message,
                )

        if settings.arr_configured("tv", stufe.value):
            try:
                nach_tvdb, _ = await library.series_library(settings, stufe.value)
                for tvdb_id, eintrag in nach_tvdb.items():
                    _serie_aufnehmen(gemessen, stufe, tvdb_id, eintrag)
            except ArrError as fehler:
                logger.warning(
                    "Sonarr (%s) nicht erreichbar, Groessen bleiben wie sie waren: %s",
                    stufe.value,
                    fehler.message,
                )

    _aus_media_server(db, gemessen)
    return gemessen


def _film_aufnehmen(
    ziel: dict[str, _Gemessen],
    stufe: QualityTier,
    tmdb_id: int,
    eintrag: MovieEntry,
) -> None:
    if eintrag.size_bytes <= 0:
        return
    kennung = schluessel(MediaType.movie, stufe, tmdb_id=tmdb_id)
    if kennung is None:
        return
    ziel[kennung] = _Gemessen(
        key=kennung,
        media_type=MediaType.movie,
        tier=stufe,
        tmdb_id=tmdb_id,
        tvdb_id=None,
        season=None,
        title=eintrag.title,
        size_bytes=eintrag.size_bytes,
    )


def _serie_aufnehmen(
    ziel: dict[str, _Gemessen],
    stufe: QualityTier,
    tvdb_id: int,
    eintrag: SeriesEntry,
) -> None:
    """Eine Zeile **je Staffel** - nie je Folge.

    Die Staffel ist die feinste Koernung, die Sonarr ohne zusaetzliche Abfrage
    hergibt. Feiner zu rechnen haette eine Abfrage je Serie gekostet, und
    "Staffel 3 belegt 40 GB" ist ohnehin die Aussage, mit der jemand etwas
    anfangen kann.
    """
    for staffel, bytes_ in eintrag.seasons.items():
        if bytes_ <= 0:
            continue
        kennung = schluessel(MediaType.tv, stufe, tvdb_id=tvdb_id, season=staffel)
        if kennung is None:
            continue
        ziel[kennung] = _Gemessen(
            key=kennung,
            media_type=MediaType.tv,
            tier=stufe,
            tmdb_id=None,
            tvdb_id=tvdb_id,
            season=staffel,
            title=eintrag.title,
            size_bytes=bytes_,
        )


def _aus_media_server(db: Session, ziel: dict[str, _Gemessen]) -> None:
    """Filme, die nur noch im Media-Server liegen.

    Der Fall, um den es geht: laden, bis die Qualitaet stimmt, dann den
    Eintrag aus Radarr werfen und die Datei behalten. Danach ist der
    Media-Server die einzige Stelle, die die Groesse ueberhaupt noch kennt.

    Was Radarr bereits gemeldet hat, wird **nicht** ueberschrieben - dessen
    Zahl ist die genauere. Serien bleiben aussen vor: Dort haengen die Dateien
    an den Folgen, der Serien-Eintrag traegt keine Groesse.
    """
    zeilen = db.scalars(
        select(MediaServerLibraryItem).where(
            MediaServerLibraryItem.media_type == MediaType.movie,
            MediaServerLibraryItem.tmdb_id.is_not(None),
        )
    ).all()

    for zeile in zeilen:
        for stufe, bytes_ in (
            (QualityTier.standard, zeile.size_standard),
            (QualityTier.uhd, zeile.size_uhd),
        ):
            if bytes_ <= 0:
                continue
            kennung = schluessel(MediaType.movie, stufe, tmdb_id=zeile.tmdb_id)
            if kennung is None or kennung in ziel:
                continue
            ziel[kennung] = _Gemessen(
                key=kennung,
                media_type=MediaType.movie,
                tier=stufe,
                tmdb_id=zeile.tmdb_id,
                tvdb_id=zeile.tvdb_id,
                season=None,
                title=zeile.title,
                size_bytes=bytes_,
            )


def _schreiben(db: Session, gemessen: dict[str, _Gemessen]) -> Ergebnis:
    """Den gemessenen Stand in die Datenbank uebertragen."""
    vorhanden = {zeile.key: zeile for zeile in db.scalars(select(StorageEntry)).all()}

    # Beim allererste Lauf gehoert alles dem Haus: Was schon da war, hat
    # niemand ueber Nexview angefordert - und niemand soll am Tag der
    # Einfuehrung ueberzogen sein. Erkannt an der leeren Tabelle und nicht an
    # einem Zeitstempel, der ein zurueckgespieltes Backup ueberleben wuerde.
    erster_lauf = not vorhanden
    zuordnung = {} if erster_lauf else _zuordnung(db, gemessen.values())

    neu = aktualisiert = gewachsen = 0
    jetzt = utcnow()

    for kennung, wert in gemessen.items():
        zeile = vorhanden.pop(kennung, None)
        if zeile is None:
            besitzer = zuordnung.get(kennung)
            db.add(
                StorageEntry(
                    key=kennung,
                    user_id=besitzer,
                    media_type=wert.media_type,
                    tier=wert.tier,
                    tmdb_id=wert.tmdb_id,
                    tvdb_id=wert.tvdb_id,
                    season=wert.season,
                    title=wert.title,
                    size_bytes=wert.size_bytes,
                    measured_at=jetzt,
                    state=(
                        StorageState.owned if besitzer else StorageState.house
                    ),
                )
            )
            neu += 1
            continue

        if zeile.size_bytes != wert.size_bytes:
            if wert.size_bytes > zeile.size_bytes:
                gewachsen += 1
            zeile.size_bytes = wert.size_bytes
            aktualisiert += 1
        # Der Titel kann sich aendern (Umbenennung in Radarr), und er ist das
        # Einzige, was den Posten spaeter noch lesbar macht.
        if wert.title and zeile.title != wert.title:
            zeile.title = wert.title
        zeile.measured_at = jetzt

    # Was uebrig bleibt, meldet keine Quelle mehr.
    #
    # Wichtig: "Radarr kennt es nicht mehr" ist **kein** Beweis, dass die Datei
    # weg ist - siehe _aus_media_server. Uebrig bleibt hier nur, was weder
    # Radarr/Sonarr noch der Media-Server meldet. Genau dieselbe Regel wie im
    # Status-Abgleich, bevor er eine Anfrage auf "geloescht" setzt.
    for zeile in vorhanden.values():
        db.delete(zeile)

    db.commit()
    return Ergebnis(
        neu=neu,
        aktualisiert=aktualisiert,
        entfernt=len(vorhanden),
        gewachsen=gewachsen,
        erster_lauf=erster_lauf,
    )


def _zuordnung(db: Session, werte) -> dict[str, int]:
    """Wem gehoert ein neu aufgetauchter Posten?

    Wer den Titel ueber Nexview angefragt hat, traegt ihn. Alles andere ist
    Hausbestand.

    Das gilt ausdruecklich **auch dann**, wenn noch gar keine Grenze
    eingeschaltet ist. Wuerde erst ab dem Einschalten zugeordnet, staende nach
    Wochen des Messens bei jedem eine Null - und die Frage "wer belegt am
    meisten" waere unbeantwortbar, also der Zweck der Messung verfehlt.
    """
    anfragen = db.scalars(
        select(MediaRequest).where(MediaRequest.status.in_(ZURECHENBAR))
    ).all()

    # Ein Admin hat kein Kontingent - ihm etwas zuzurechnen waere folgenlos,
    # wuerde die Uebersicht aber verfaelschen. Er zaehlt trotzdem mit: Sonst
    # verschoebe sich sein Verbrauch stillschweigend ins Haus.
    nach_film: dict[tuple[int, str], int] = {}
    nach_serie: dict[tuple[int, str, int | None], int] = {}
    for anfrage in anfragen:
        stufe = anfrage.tier.value if anfrage.tier else QualityTier.standard.value
        if anfrage.media_type == MediaType.movie:
            nach_film.setdefault((anfrage.tmdb_id, stufe), anfrage.user_id)
        elif anfrage.tvdb_id:
            nach_serie.setdefault(
                (anfrage.tvdb_id, stufe, anfrage.season), anfrage.user_id
            )

    ergebnis: dict[str, int] = {}
    for wert in werte:
        if wert.media_type == MediaType.movie:
            besitzer = nach_film.get((wert.tmdb_id, wert.tier.value))
        else:
            # Erst die genaue Staffel, dann die Anfrage ueber die ganze Serie.
            besitzer = nach_serie.get(
                (wert.tvdb_id, wert.tier.value, wert.season)
            ) or nach_serie.get((wert.tvdb_id, wert.tier.value, None))
        if besitzer is not None:
            ergebnis[wert.key] = besitzer
    return ergebnis


def verbuchen(db: Session, request: MediaRequest, eintrag: object) -> int:
    """Einen gerade fertig gewordenen Titel sofort zurechnen.

    Der stuendliche Abgleich wuerde das auch erledigen - aber bis zu eine
    Stunde spaeter. Wer gerade etwas angefragt hat und nachsieht, was es ihn
    kostet, findet dann eine Null vor und haelt die Anzeige fuer kaputt.

    Aufgerufen aus ``status_poller.check_once``, genau in dem Moment, in dem
    ``has_file`` wahr wird: Dort liegen Anfrage, Nutzer und der frisch geholte
    Bibliothekseintrag samt Groesse gleichzeitig vor.

    Gibt zurueck, wie viele Posten dabei zugerechnet wurden.
    """
    gemessen: dict[str, _Gemessen] = {}
    stufe = request.tier or QualityTier.standard

    if request.media_type == MediaType.movie:
        _film_aufnehmen(gemessen, stufe, request.tmdb_id, eintrag)  # type: ignore[arg-type]
    elif request.tvdb_id:
        _serie_aufnehmen(gemessen, stufe, request.tvdb_id, eintrag)  # type: ignore[arg-type]
        # Eine Anfrage auf **eine** Staffel darf auch nur diese eine belasten.
        if request.season is not None:
            nur = schluessel(
                MediaType.tv, stufe, tvdb_id=request.tvdb_id, season=request.season
            )
            gemessen = {k: v for k, v in gemessen.items() if k == nur}

    if not gemessen:
        return 0

    vorhanden = {
        zeile.key: zeile
        for zeile in db.scalars(
            select(StorageEntry).where(StorageEntry.key.in_(gemessen))
        ).all()
    }

    jetzt = utcnow()
    verbucht = 0
    for kennung, wert in gemessen.items():
        zeile = vorhanden.get(kennung)
        if zeile is None:
            db.add(
                StorageEntry(
                    key=kennung,
                    user_id=request.user_id,
                    media_type=wert.media_type,
                    tier=wert.tier,
                    tmdb_id=wert.tmdb_id or request.tmdb_id,
                    tvdb_id=wert.tvdb_id or request.tvdb_id,
                    season=wert.season,
                    title=wert.title or request.title,
                    size_bytes=wert.size_bytes,
                    measured_at=jetzt,
                    state=StorageState.owned,
                    request_id=request.id,
                )
            )
            verbucht += 1
            continue

        # Vorhandenes nur uebernehmen, wenn es **niemandem** gehoert.
        #
        # Einem anderen Nutzer etwas wegzunehmen waere in jedem Fall falsch;
        # und beim Hausbestand ist die Uebernahme richtig, weil derjenige den
        # Titel ja gerade selbst ins Haus geholt hat.
        if zeile.state == StorageState.house and zeile.user_id is None:
            zeile.user_id = request.user_id
            zeile.state = StorageState.owned
            zeile.request_id = request.id
            verbucht += 1
        if wert.size_bytes > 0:
            zeile.size_bytes = wert.size_bytes
            zeile.measured_at = jetzt

    return verbucht


def zuruecksetzen(db: Session) -> int:
    """Alles ins Haus, alle Konten auf null.

    Gebraucht beim Umschalten der Betriebsart und als Notausgang. **Dateien
    werden dabei nie angefasst** - es wechselt nur, wem sie zugerechnet werden.
    """
    zeilen = db.scalars(
        select(StorageEntry).where(StorageEntry.state != StorageState.house)
    ).all()
    for zeile in zeilen:
        zeile.user_id = None
        zeile.state = StorageState.house
    db.commit()
    return len(zeilen)
