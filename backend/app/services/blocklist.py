"""Die Sperrliste: Titel, die in dieser Bibliothek nicht landen sollen.

Eine Entscheidung des Administrators fuer *alle*, im Gegensatz zur
Altersbeschraenkung, die je Benutzer gilt. Und eine **sichtbare**: gesperrte
Titel bleiben auffindbar und tragen ein Abzeichen. Sie zu verstecken waere hier
falsch - wer sie sucht, soll erfahren, dass es sie nicht geben wird, statt
dreimal vergeblich anzufragen.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Blocked, MediaType, User

# Das Abzeichen, das eine gesperrte Kachel traegt.
BADGE = "blocked"


def eintrag(db: Session, media_type: MediaType, tmdb_id: int) -> Blocked | None:
    return db.scalar(
        select(Blocked).where(
            Blocked.media_type == media_type, Blocked.tmdb_id == tmdb_id
        )
    )


def ist_gesperrt(db: Session, media_type: MediaType, tmdb_id: int) -> bool:
    return eintrag(db, media_type, tmdb_id) is not None


def gesperrte_kennungen(
    db: Session, media_type: MediaType, tmdb_ids: list[int]
) -> set[int]:
    """Welche dieser Titel sind gesperrt? - fuer die Abzeichen auf den Kacheln.

    Eine Abfrage fuer die ganze Seite statt einer je Kachel.
    """
    if not tmdb_ids:
        return set()

    return set(
        db.scalars(
            select(Blocked.tmdb_id).where(
                Blocked.media_type == media_type, Blocked.tmdb_id.in_(tmdb_ids)
            )
        )
    )


def alle(db: Session) -> list[Blocked]:
    """Die ganze Liste, zuletzt Gesperrtes zuerst."""
    return list(db.scalars(select(Blocked).order_by(Blocked.created_at.desc())))


def sperren(
    db: Session,
    *,
    media_type: MediaType,
    tmdb_id: int,
    title: str = "",
    poster_url: str | None = None,
    reason: str | None = None,
    admin: User | None = None,
) -> Blocked:
    """Sperren. Ein zweites Mal zu sperren ist kein Fehler.

    Der vorhandene Eintrag wird dann zurueckgegeben, ohne Datum und Begruendung
    zu ueberschreiben - die erste Entscheidung bleibt die dokumentierte. Ohne
    diese Nachsicht muesste jede Aufrufstelle vorher selbst nachsehen, und beim
    Ablehnen einer bereits gesperrten Anfrage haette es einen Fehler gegeben,
    obwohl das gewuenschte Ergebnis schon eingetreten ist.
    """
    vorhanden = eintrag(db, media_type, tmdb_id)
    if vorhanden is not None:
        return vorhanden

    neu = Blocked(
        media_type=media_type,
        tmdb_id=tmdb_id,
        title=(title or "").strip()[:300],
        poster_url=poster_url,
        reason=(reason or "").strip() or None,
        blocked_by=admin.id if admin is not None else None,
    )
    db.add(neu)
    db.flush()
    return neu


def freigeben(db: Session, media_type: MediaType, tmdb_id: int) -> bool:
    """Wieder freigeben. Gibt zurueck, ob ueberhaupt etwas gesperrt war."""
    vorhanden = eintrag(db, media_type, tmdb_id)
    if vorhanden is None:
        return False
    db.delete(vorhanden)
    return True
