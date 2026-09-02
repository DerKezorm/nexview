"""Bewertungen der Qualität - am Titel, nicht an der Anfrage.

Warum das so ist: siehe ``models.TitleRating``. Kurz - es geht um die
**Datei**, und die beurteilt jeder gleich gut, der sie gesehen hat, nicht nur
der, der sie bestellt hat.

Zwei Dinge grenzt dieses Modul ausdruecklich ab:

* Das **Herz** (``Favorite``) ist Geschmack: "mag ich". Es sagt nichts ueber
  die Datei aus und speist die Empfehlungen.
* Ein **Ticket** ist eine Bitte: "hier stimmt etwas nicht, kuemmere dich". Es
  hat einen Zustand und bleibt offen, bis jemand es schliesst. Eine Bewertung
  verschwindet in einem Durchschnitt; ein Ticket laesst sich nicht
  wegmitteln. Deshalb bietet die Oberflaeche bei einem schwachen Urteil an,
  eines daraus zu machen.

⚠️ **Nicht zu verwechseln mit ``portal_ratings.py``.** Hier steht, was der
**Haushalt** ueber eine Datei sagt. Was **IMDb, Rotten Tomatoes und
Metacritic** sagen, steht nebenan. Beides hiess einmal ``ratings`` - und als
diese Datei in 0.19.0 neu geschrieben wurde, verschwanden die Portal-Wertungen
mit ihr, waehrend ihr Aufrufer stehenblieb. Zwei Versionen lang endete jede
Anfrage an ``/api/ratings/movie`` in einem 500.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SCHEIBE, scheiben
from ..models import (
    MediaType,
    NotificationType,
    TitleRating,
    User,
    utcnow,
)
from . import notify

logger = logging.getLogger("nexview.ratings")

# Ab hier (und darunter) gilt ein Urteil als schwach.
POOR_RATING = 2

# Ab welchem Zuwachs eine Datei als *aufgewertet* gilt.
#
# Ein Zuwachs von ein paar hundert Megabyte kann eine nachgereichte
# Untertitelspur sein. Der Fall, um den es geht, ist der Sprung von 1080p auf
# 2160p - aus 5 GB werden 50.
AUFWERTUNG_AB = 2 * 1024 * 1024 * 1024


def meine(
    db: Session, user: User, media_type: MediaType, tmdb_id: int, season: int | None = None
) -> TitleRating | None:
    return db.scalar(
        select(TitleRating).where(
            TitleRating.user_id == user.id,
            TitleRating.media_type == media_type,
            TitleRating.tmdb_id == tmdb_id,
            TitleRating.season.is_(None) if season is None else TitleRating.season == season,
        )
    )


def fuer_anfragen(
    db: Session, anfragen: list
) -> dict[tuple[int, MediaType, int, int | None], TitleRating]:
    """Die Urteile zu einer Anfrageliste - **eine** Abfrage statt einer je Zeile.

    Die Freigabeliste rief ``meine`` fuer jede Zeile auf; bei 145 Anfragen
    waren das 145 Abfragen fuer eine haushaltskleine Tabelle. Hier holt eine
    IN-Abfrage alles auf einmal, und das Woerterbuch filtert exakt auf den
    Viererschluessel - was das Kreuzprodukt aus Nutzern und Titeln zu viel
    holt, faellt dabei von selbst raus.

    ⚠️ ``order_by(id)`` plus erste-gewinnt ist Absicht: SQLite zaehlt NULLs im
    UNIQUE als verschieden, doppelte staffellose Zeilen sind also moeglich -
    und ``meine`` lieferte dann irgendeine. Hier gewinnt deterministisch die
    aelteste.

    Altbestand ohne Fremdschluessel (moeglich von vor dem checkout-Horcher in
    ``db.py``) kann das stehengebliebene Urteil eines geloeschten Nutzers
    enthalten; anders als die alte ``db.get``-Wache in ``_bewertung_zu``
    liefert diese Abfrage es mit - am Endpunkt unsichtbar, weil beide Wege
    fuer dieselbe verwaiste Zeile vorher an ``request.user`` mit 500
    abstuerzen.
    """
    if not anfragen:
        return {}
    ergebnis: dict[tuple[int, MediaType, int, int | None], TitleRating] = {}
    # In Scheiben wegen der SQLite-Parametergrenze (siehe ``db.scheiben``);
    # zwei IN-Listen teilen sich das Kontingent, darum je die Haelfte. Ein
    # Scheiben**paar** je Abfrage aendert am Ergebnis nichts: Zeilen mit
    # demselben Viererschluessel teilen sich Nutzer UND Titel, landen also
    # immer im selben Paar - erste-gewinnt bleibt erhalten.
    benutzer = list({a.user_id for a in anfragen})
    titel = list({a.tmdb_id for a in anfragen})
    for benutzer_scheibe in scheiben(benutzer, SCHEIBE // 2):
        for titel_scheibe in scheiben(titel, SCHEIBE // 2):
            for zeile in db.scalars(
                select(TitleRating)
                .where(
                    TitleRating.user_id.in_(benutzer_scheibe),
                    TitleRating.tmdb_id.in_(titel_scheibe),
                )
                .order_by(TitleRating.id)
            ):
                ergebnis.setdefault(
                    (zeile.user_id, zeile.media_type, zeile.tmdb_id, zeile.season), zeile
                )
    return ergebnis


def fuer_titel(db: Session, media_type: MediaType, tmdb_id: int) -> list[TitleRating]:
    """Alle Urteile zu diesem Titel - fuer die Uebersicht des Betreibers."""
    return list(
        db.scalars(
            select(TitleRating)
            .where(TitleRating.media_type == media_type, TitleRating.tmdb_id == tmdb_id)
            .order_by(TitleRating.updated_at.desc())
        )
    )


def setzen(
    db: Session,
    user: User,
    media_type: MediaType,
    tmdb_id: int,
    *,
    rating: int,
    comment: str | None = None,
    title: str = "",
    season: int | None = None,
    file_size_bytes: int = 0,
) -> tuple[TitleRating, bool]:
    """Bewerten oder das eigene Urteil aendern.

    Gibt den Eintrag zurueck und ob er **neu** ist. Daran haengt die Meldung
    an die Entscheider: Eine Aenderung soll nicht ein zweites Mal klingeln.

    Ein geaendertes Urteil ist ausdruecklich **nicht mehr veraltet** - wer neu
    bewertet, hat die Datei angesehen, die jetzt dort liegt.
    """
    eintrag = meine(db, user, media_type, tmdb_id, season)
    neu = eintrag is None
    if eintrag is None:
        eintrag = TitleRating(
            user_id=user.id,
            media_type=media_type,
            tmdb_id=tmdb_id,
            season=season,
        )
        db.add(eintrag)

    eintrag.rating = rating
    eintrag.comment = (comment or "").strip() or None
    if title:
        eintrag.title = title
    eintrag.outdated = False
    if file_size_bytes > 0:
        eintrag.file_size_bytes = file_size_bytes
    eintrag.updated_at = utcnow().replace(tzinfo=None)
    db.flush()
    return eintrag, neu


def entwerten(db: Session, media_type: MediaType, tmdb_id: int, groesse: int,
              season: int | None = None) -> int:
    """Radarr hat nachgeladen - alle Urteile zu dieser Datei sind hinfaellig.

    Gibt zurueck, wie viele entwertet wurden.

    ⚠️ Der **erste** gemerkte Stand ist kein Wachstum. Ohne diese Regel gaelte
    beim Einbau jede bestehende Bewertung sofort als veraltet - und alle
    bekaemen auf einmal eine Nachricht ueber etwas, das nie passiert ist.
    """
    if groesse <= 0:
        return 0

    getroffen = 0
    for eintrag in db.scalars(
        select(TitleRating).where(
            TitleRating.media_type == media_type,
            TitleRating.tmdb_id == tmdb_id,
            TitleRating.season.is_(None) if season is None else TitleRating.season == season,
        )
    ):
        vorher = eintrag.file_size_bytes or 0
        eintrag.file_size_bytes = groesse
        if vorher <= 0 or groesse - vorher < AUFWERTUNG_AB or eintrag.outdated:
            continue

        eintrag.outdated = True
        getroffen += 1
        besitzer = db.get(User, eintrag.user_id)
        if besitzer is not None:
            notify.create(
                db,
                user=besitzer,
                kind=NotificationType.rating_outdated,
                message_key="notifications.ratingOutdated",
                title=eintrag.title,
            )
    return getroffen
