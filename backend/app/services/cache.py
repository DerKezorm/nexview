"""Zwischenspeicher fuer TMDB-Antworten.

TMDB-Daten aendern sich langsam. Statt bei jedem Klick neu abzufragen,
landen die Antworten fuer eine Weile in der SQLite-Datenbank. Das haelt die
Oberflaeche schnell und die Zahl der API-Aufrufe klein.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import TmdbCache, utcnow

DISCOVER_TTL = timedelta(hours=3)
DETAIL_TTL = timedelta(days=7)
GENRE_TTL = timedelta(days=30)
SEARCH_TTL = timedelta(minutes=30)


def read(db: Session, key: str) -> Any | None:
    row = db.get(TmdbCache, key)
    if row is None:
        return None
    if row.expires_at <= utcnow().replace(tzinfo=None):
        db.delete(row)
        db.commit()
        return None
    try:
        return json.loads(row.payload)
    except json.JSONDecodeError:
        return None


def write(db: Session, key: str, payload: Any, ttl: timedelta) -> None:
    expires_at = (utcnow() + ttl).replace(tzinfo=None)
    row = db.get(TmdbCache, key)
    if row is None:
        db.add(TmdbCache(cache_key=key, payload=json.dumps(payload), expires_at=expires_at))
    else:
        row.payload = json.dumps(payload)
        row.expires_at = expires_at
    db.commit()


async def cached(
    db: Session, key: str, ttl: timedelta, factory: Callable[[], Awaitable[Any]]
) -> Any:
    """Wert aus dem Zwischenspeicher holen oder neu beschaffen."""
    hit = read(db, key)
    if hit is not None:
        return hit

    value = await factory()
    write(db, key, value, ttl)
    return value


def purge_expired(db: Session) -> None:
    db.execute(delete(TmdbCache).where(TmdbCache.expires_at <= utcnow().replace(tzinfo=None)))
    db.commit()


def clear_all(db: Session) -> None:
    """Wird nach einer Aenderung der Einstellungen aufgerufen - sonst wuerden
    alte Ergebnisse eines anderen API-Keys oder einer anderen Region
    weiterverwendet."""
    db.execute(delete(TmdbCache))
    db.commit()
