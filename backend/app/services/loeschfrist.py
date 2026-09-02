"""Die Schonfrist vor dem Loeschen - ankuendigen statt wegnehmen.

Loeschen ist der einzige Vorgang in Nexview ohne Rueckweg (ausser dem
Papierkorb von Radarr/Sonarr, falls einer eingerichtet ist). Genau deshalb gibt
es zwischen "der Administrator entscheidet" und "die Datei ist weg" eine Frist:

1. Der Administrator merkt einen Posten vor - ``vormerken``.
2. **Alle mit Bezug zum Titel** bekommen Bescheid: wer ihn angefragt,
   bewertet, mit dem Herz markiert oder gesehen hat.
3. Auf der Startseite laeuft die Restzeit sichtbar ab.
4. Nach Ablauf loescht ``faellige_loeschen`` - oder eben nicht, weil jemand
   widersprochen hat.

⚠️ **Wer den Titel in der Frist ansieht, hebt die Vormerkung auf.** Das ist der
beste denkbare Widerspruch: Er beweist genau das, was die Aufraeum-Liste
bestritten hat - dass ihn noch jemand will. Niemand muss dafuer einen Knopf
finden, es genuegt, ihn zu schauen.

⚠️ **Und die Nachricht geht nicht ueber "Sag mir Bescheid".** Der naheliegende
Gedanke war, ``TitleWatch`` zu nehmen - aber das ist die Warteliste fuer Titel,
die es **noch nicht gibt**; bei Filmen faellt die Zeile weg, sobald gemeldet
wurde. Fuer einen vorhandenen Titel steht dort praktisch nie jemand. Die vier
Quellen unten treffen dagegen genau die Leute, denen er etwas bedeutet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Favorite,
    MediaRequest,
    MediaType,
    NotificationType,
    StorageEntry,
    TitleRating,
    User,
    UserWatched,
    utcnow,
)
from . import notify

logger = logging.getLogger("nexview.loeschfrist")

# Die uebliche Schonfrist. Zwei Wochen, weil ein Haushalt einen Urlaub lang
# nicht hineinsieht - eine Woche waere fuer den, der gerade weg ist, dasselbe
# wie gar keine.
FRIST_TAGE = 14

# Laenger geht nicht. Eine Vormerkung, die ein Jahr laeuft, ist keine
# Ankuendigung mehr, sondern ein vergessener Zettel.
FRIST_MAX_TAGE = 90


class Fristfehler(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Vorgemerkt:
    """Ein Posten, der bald verschwindet - fuer die Anzeige."""

    posten_id: int
    media_type: MediaType
    tmdb_id: int | None
    season: int | None
    title: str
    size_bytes: int
    loescht_am: datetime
    #: Wie viele volle Tage noch bleiben. Null heisst "heute noch".
    tage_uebrig: int


def _ohne_zeitzone(wert: datetime) -> datetime:
    return wert.replace(tzinfo=None) if wert.tzinfo else wert


def betroffene(db: Session, zeile: StorageEntry) -> list[User]:
    """Wen dieser Titel etwas angeht.

    Vier Quellen, alle bereits vorhanden: **angefragt**, **bewertet**, **mit
    dem Herz markiert**, **gesehen**. Wer in keiner davon steht, hat den Titel
    nie angefasst - der bekommt auch keine Nachricht ueber seinen Verlust.

    Kinderkonten bleiben draussen: Sie koennen nichts entscheiden, und eine
    Nachricht "dein Film wird geloescht" ohne jede Handlungsmoeglichkeit ist
    keine Information, sondern eine Verstimmung.
    """
    if zeile.tmdb_id is None:
        return []

    kennungen: set[int] = set()
    art = zeile.media_type
    tmdb_id = zeile.tmdb_id

    kennungen.update(
        db.scalars(
            select(MediaRequest.user_id).where(
                MediaRequest.media_type == art, MediaRequest.tmdb_id == tmdb_id
            )
        )
    )
    kennungen.update(
        db.scalars(
            select(TitleRating.user_id).where(
                TitleRating.media_type == art, TitleRating.tmdb_id == tmdb_id
            )
        )
    )
    kennungen.update(
        db.scalars(
            select(Favorite.user_id).where(
                Favorite.media_type == art, Favorite.tmdb_id == tmdb_id
            )
        )
    )
    kennungen.update(
        db.scalars(
            select(UserWatched.user_id).where(
                UserWatched.media_type == art, UserWatched.tmdb_id == tmdb_id
            )
        )
    )
    if not kennungen:
        return []

    return list(
        db.scalars(
            select(User).where(
                User.id.in_(kennungen),
                User.is_active.is_(True),
                User.role != "child",
            )
        )
    )


def vormerken(db: Session, posten_id: int, tage: int = FRIST_TAGE) -> StorageEntry:
    """Zum Loeschen vormerken und alle Betroffenen benachrichtigen.

    ``tage=0`` ist bewusst **nicht** erlaubt: Sofort loeschen ist ein eigener
    Weg (``storage.loeschen``) mit eigener Rueckfrage. Eine "Frist" von null
    Tagen waere eine Ankuendigung, die niemand mehr lesen kann.
    """
    if tage < 1 or tage > FRIST_MAX_TAGE:
        raise Fristfehler(
            f"Die Schonfrist muss zwischen 1 und {FRIST_MAX_TAGE} Tagen liegen.", 422
        )

    zeile = db.get(StorageEntry, posten_id)
    if zeile is None:
        raise Fristfehler("Diesen Posten gibt es nicht.", 404)
    if not zeile.arr_managed:
        # Dieselbe Grenze wie beim sofortigen Loeschen: Nexview loescht
        # ausschliesslich ueber Radarr/Sonarr. Etwas vorzumerken, das dort
        # niemand mehr kennt, waere eine Ankuendigung ohne Deckung.
        raise Fristfehler(
            "Diesen Titel führt weder Radarr noch Sonarr - Nexview kann ihn nicht löschen.",
            409,
        )

    jetzt = _ohne_zeitzone(utcnow())
    zeile.delete_after = jetzt + timedelta(days=tage)
    zeile.delete_marked_at = jetzt

    for person in betroffene(db, zeile):
        notify.create(
            db,
            user=person,
            kind=NotificationType.storage_scheduled,
            message_key="notifications.storageScheduled",
            title=zeile.title,
        )
    return zeile


def aufheben(db: Session, posten_id: int, *, grund: str = "admin") -> StorageEntry:
    """Vormerkung zuruecknehmen und die Betroffenen beruhigen."""
    zeile = db.get(StorageEntry, posten_id)
    if zeile is None:
        raise Fristfehler("Diesen Posten gibt es nicht.", 404)
    if zeile.delete_after is None:
        return zeile

    zeile.delete_after = None
    zeile.delete_marked_at = None
    for person in betroffene(db, zeile):
        notify.create(
            db,
            user=person,
            kind=NotificationType.storage_unscheduled,
            message_key="notifications.storageUnscheduled",
            title=zeile.title,
        )
    logger.info("Deletion of %r cancelled (%s)", zeile.title, grund)
    return zeile


def offene(db: Session, *, grenze: int = 50) -> list[Vorgemerkt]:
    """Was demnaechst verschwindet - fuer die Startseite.

    Die naechste Loeschung zuerst: Was in zwei Tagen weg ist, ist dringender
    als das, was noch zwei Wochen hat.
    """
    jetzt = _ohne_zeitzone(utcnow())
    zeilen = db.scalars(
        select(StorageEntry)
        .where(StorageEntry.delete_after.is_not(None))
        .order_by(StorageEntry.delete_after.asc())
        .limit(grenze)
    )
    return [
        Vorgemerkt(
            posten_id=z.id,
            media_type=z.media_type,
            tmdb_id=z.tmdb_id,
            season=z.season,
            title=z.title,
            size_bytes=z.size_bytes,
            loescht_am=z.delete_after,
            tage_uebrig=max(0, (z.delete_after - jetzt).days),
        )
        for z in zeilen
        if z.delete_after is not None
    ]


def angesehen_hebt_auf(db: Session, benutzer_id: int) -> int:
    """Vormerkungen aufheben, die jemand durch Ansehen widerlegt hat.

    ⚠️ **Der stille Widerspruch, und der wichtigste Teil dieser Datei.** Die
    Aufraeum-Liste behauptet "das sieht niemand mehr an". Wer den Titel in der
    Frist anschaut, hat das widerlegt - besser als jeder Einspruchsknopf, und
    ohne dass er von der Vormerkung ueberhaupt wissen muss.

    Aufgerufen nach dem Abgleich der Sehdaten, fuer das Konto, das gerade
    abgeglichen wurde. Gezaehlt wird nur, was **seit** der Vormerkung gesehen
    wurde - ein Blick von vor drei Jahren widerlegt nichts.
    """
    vorgemerkt = list(
        db.scalars(select(StorageEntry).where(StorageEntry.delete_after.is_not(None)))
    )
    if not vorgemerkt:
        return 0

    # Wann dieses Konto welchen Titel zuletzt gesehen hat.
    gesehen = {
        (art, tmdb_id): wann
        for art, tmdb_id, wann in db.execute(
            select(UserWatched.media_type, UserWatched.tmdb_id, UserWatched.watched_at).where(
                UserWatched.user_id == benutzer_id
            )
        )
    }

    aufgehoben = 0
    for zeile in vorgemerkt:
        if zeile.tmdb_id is None or zeile.delete_after is None:
            continue
        wann = gesehen.get((zeile.media_type, zeile.tmdb_id))
        if wann is None:
            continue
        # ⚠️ Nur was **nach** der Vormerkung gesehen wurde, ist ein
        # Widerspruch. Ohne diese Schranke haette ein Seh-Eintrag von vor drei
        # Jahren jede Vormerkung sofort wieder aufgehoben - und genau der ist
        # ja der Grund, warum sie ueberhaupt entstand.
        if zeile.delete_marked_at is not None and wann >= zeile.delete_marked_at:
            aufheben(db, zeile.id, grund="in der Frist angesehen")
            aufgehoben += 1
    return aufgehoben


def faellig(db: Session) -> list[StorageEntry]:
    """Posten, deren Frist abgelaufen ist."""
    return list(
        db.scalars(
            select(StorageEntry).where(
                StorageEntry.delete_after.is_not(None),
                StorageEntry.delete_after <= _ohne_zeitzone(utcnow()),
            )
        )
    )
