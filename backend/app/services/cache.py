"""Zwischenspeicher fuer TMDB-Antworten.

TMDB-Daten aendern sich langsam. Statt bei jedem Klick neu abzufragen,
landen die Antworten fuer eine Weile in der SQLite-Datenbank. Das haelt die
Oberflaeche schnell und die Zahl der API-Aufrufe klein.

⚠️ **Gelesen und geschrieben wird ausserhalb der Ereignisschleife**
(``lesen``, ``lesen_viele``, ``schreiben_viele``, ``cached``). Ein schlanker
Detaileintrag ist die rohe TMDB-Antwort samt Freigaben und Anbietern aller
Laender, rund 50 bis 100 KB, und eine Seite von Entdecken braucht sechzig
davon. Lief das in der Schleife, stand jede andere Anfrage so lange still: Am
16.11.2026 kamen auf der Synology ``requests/quota`` und ``requests/mine``
zusammen mit drei Entdecken-Listen nach 7,2 Sekunden zurueck, obwohl beide nur
eine Zeile lesen. Lokal nachgemessen hielt eine Liste die Schleife bis zu einer
Sekunde am Stueck fest, gut die Haelfte davon in ``read`` (je zur Haelfte
SQLite und ``json.loads``), jeder Eintrag zweimal gelesen.

``read`` und ``write`` bleiben fuer Code ausserhalb der Schleife und fuer Tests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from ..models import TmdbCache, utcnow

DISCOVER_TTL = timedelta(hours=3)
DETAIL_TTL = timedelta(days=7)
GENRE_TTL = timedelta(days=30)
SEARCH_TTL = timedelta(minutes=30)

#: So viele Schluessel stehen hoechstens in einer Abfrage.
STAPEL = 500


def read(db: Session, key: str) -> Any | None:
    """Einen Eintrag lesen - **in der Schleife**. Aus ``async``-Code ``lesen`` nehmen."""
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
    """Einen Eintrag schreiben - **in der Schleife**. Aus ``async``-Code ``schreiben_viele``."""
    expires_at = (utcnow() + ttl).replace(tzinfo=None)
    row = db.get(TmdbCache, key)
    if row is None:
        db.add(TmdbCache(cache_key=key, payload=json.dumps(payload), expires_at=expires_at))
    else:
        row.payload = json.dumps(payload)
        row.expires_at = expires_at
    db.commit()


# --- Ausserhalb der Ereignisschleife -----------------------------------------


def _gueltige_lesen(verbindung: Connection, keys: list[str]) -> dict[str, Any]:
    """Was zu diesen Schluesseln gilt, entschluesselt. Abgelaufenes fehlt."""
    jetzt = utcnow().replace(tzinfo=None)
    gefunden: dict[str, Any] = {}
    for start in range(0, len(keys), STAPEL):
        zeilen = verbindung.execute(
            select(TmdbCache.cache_key, TmdbCache.payload, TmdbCache.expires_at).where(
                TmdbCache.cache_key.in_(keys[start : start + STAPEL])
            )
        )
        for key, payload, expires_at in zeilen:
            # Abgelaufenes gilt als fehlend, wie in ``read``. Geloescht wird es
            # hier nicht; der Aufrufer holt neu und ueberschreibt die Zeile.
            if expires_at <= jetzt:
                continue
            try:
                wert = json.loads(payload)
            except json.JSONDecodeError:
                continue
            # ``null`` heisst bei ``read`` "nichts da" - hier ebenso.
            if wert is not None:
                gefunden[key] = wert
    return gefunden


def _im_faden_lesen(engine: Engine, keys: list[str]) -> dict[str, Any]:
    with engine.connect() as verbindung:
        return _gueltige_lesen(verbindung, keys)


def _im_faden_schreiben(engine: Engine, eintraege: dict[str, Any], expires_at: datetime) -> None:
    zeilen = [
        {"cache_key": key, "payload": json.dumps(payload), "expires_at": expires_at}
        for key, payload in eintraege.items()
    ]
    with engine.begin() as verbindung:
        for start in range(0, len(zeilen), STAPEL):
            anweisung = insert(TmdbCache).values(zeilen[start : start + STAPEL])
            verbindung.execute(
                anweisung.on_conflict_do_update(
                    index_elements=[TmdbCache.cache_key],
                    set_={
                        "payload": anweisung.excluded.payload,
                        "expires_at": anweisung.excluded.expires_at,
                    },
                )
            )


def _eigene_engine(db: Session) -> Engine | None:
    """Die Datenbank der Sitzung, wenn ein Arbeitsfaden sie selbst oeffnen darf.

    ``None`` bei einer Sitzung auf einer festen Verbindung (nur in Tests
    denkbar): Die gehoert ihr und darf nicht in einen zweiten Faden.
    """
    bindung = db.get_bind()
    return bindung if isinstance(bindung, Engine) else None


def _nebenher_schreiben_moeglich(db: Session) -> bool:
    """Darf ein Arbeitsfaden schreiben, ohne dass diese Sitzung etwas verliert?

    ⚠️ Zwei Faelle sprechen dagegen, und in beiden schreibt ``schreiben_viele``
    wie bisher ueber die Sitzung, in der Schleife:

    * **Die Sitzung haelt eine Schreibsperre** (etwas geschrieben, noch nicht
      bestaetigt). Ein zweiter Schreiber muesste auf sie warten, bis
      ``busy_timeout`` (fuenf Sekunden), und die Sitzung gibt sie erst frei,
      wenn der Aufrufer weiterkommt - also nach dem Warten.
    * **Sie hat Ungespeichertes** (``new``, ``dirty``, ``deleted``). ``write``
      bestaetigte bisher die ganze Sitzung mit. Wer sich darauf verlassen hat,
      soll es weiter koennen.
    """
    if db.new or db.dirty or db.deleted:
        return False
    if not db.in_transaction():
        return True
    roh = db.connection().connection.dbapi_connection
    # pysqlite beginnt die Transaktion erst vor dem ersten Schreiben; nur
    # Lesen setzt ``in_transaction`` nicht.
    return not getattr(roh, "in_transaction", True)


async def lesen_viele(db: Session, keys: Iterable[str]) -> dict[str, Any]:
    """Mehrere Eintraege auf einmal, ausserhalb der Ereignisschleife.

    Eine Abfrage fuer alle Schluessel; Lesen und Entschluesseln laufen in einem
    Arbeitsfaden mit eigener Verbindung. Die Sitzung der Anfrage gibt nur an,
    **welche** Datenbank gemeint ist. Benutzt wird sie dort nicht, weil derselbe
    Aufruf sie sonst aus zwei Faeden zugleich anfassen koennte
    (``asyncio.gather`` ueber mehrere Listen mit derselben Sitzung).

    Zurueck kommen nur gueltige Eintraege; was fehlt oder abgelaufen ist,
    steht nicht im Ergebnis.
    """
    eindeutig = list(dict.fromkeys(keys))
    if not eindeutig:
        return {}
    engine = _eigene_engine(db)
    if engine is None:
        return _gueltige_lesen(db.connection(), eindeutig)
    return await asyncio.to_thread(_im_faden_lesen, engine, eindeutig)


async def lesen(db: Session, key: str) -> Any | None:
    """Ein Eintrag, ausserhalb der Ereignisschleife - siehe ``lesen_viele``."""
    return (await lesen_viele(db, [key])).get(key)


async def schreiben_viele(db: Session, eintraege: dict[str, Any], ttl: timedelta) -> None:
    """Mehrere Eintraege in **einem** Commit, wo moeglich ausserhalb der Schleife.

    Bis zum 16.11.2026 bestaetigte jeder Titel einer Liste einzeln: sechzig
    Commits fuer eine Seite von Entdecken, jeder mit einem ``fsync`` auf der
    Platte des NAS, alle in der Schleife.
    """
    if not eintraege:
        return
    expires_at = (utcnow() + ttl).replace(tzinfo=None)
    engine = _eigene_engine(db)
    if engine is not None and _nebenher_schreiben_moeglich(db):
        await asyncio.to_thread(_im_faden_schreiben, engine, eintraege, expires_at)
        return

    texte = {key: json.dumps(payload) for key, payload in eintraege.items()}
    schluessel = list(texte)
    vorhanden = {
        row.cache_key: row
        for start in range(0, len(schluessel), STAPEL)
        for row in db.scalars(
            select(TmdbCache).where(TmdbCache.cache_key.in_(schluessel[start : start + STAPEL]))
        )
    }
    for key, text in texte.items():
        row = vorhanden.get(key)
        if row is None:
            db.add(TmdbCache(cache_key=key, payload=text, expires_at=expires_at))
        else:
            row.payload = text
            row.expires_at = expires_at
    db.commit()


async def cached(
    db: Session, key: str, ttl: timedelta, factory: Callable[[], Awaitable[Any]]
) -> Any:
    """Wert aus dem Zwischenspeicher holen oder neu beschaffen."""
    hit = await lesen(db, key)
    if hit is not None:
        return hit

    value = await factory()
    await schreiben_viele(db, {key: value}, ttl)
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
