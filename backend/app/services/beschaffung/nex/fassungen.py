"""Die Fassungen des NEX-Betriebs: nexcrates ``GET /versions``.

Anders als im ARR-Betrieb stehen sie nicht in den Einstellungen, sondern bei
nexcrate. Die Tabelle ``fassungen`` ist der dauerhafte Stand; gelesen wird sie
beim Start und nach jedem Auffrischen in einen Zwischenspeicher, weil
``AppSettings.fassungen_fuer()`` ohne Sitzung und ohne Netz antworten muss.

⚠️ **Eine neue Fassung ist gesperrt, bis der Betreiber sie freigibt**
(Entscheidung des Betreibers, 22.09.2026): ``offen_fuer_alle`` steht beim Anlegen
auf ``False``. Danach gehoert der Wert dem Betreiber, und kein Abgleich
setzt ihn zurueck.

⚠️ **Eine verschwundene Fassung behaelt ihre Zeile** mit ``aktiv=False``:
Anfragen, Posten und Statistik zeigen weiter ihren Namen.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from ....models import Fassung, utcnow
from ..base import NEX, FassungInfo
from . import mapping

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")

#: Die zuletzt gelesenen Fassungen, in Anzeigereihenfolge. Gefuellt aus der
#: Tabelle (Start, jeder Abgleich) und nach jedem Auffrischen.
_gemerkt: tuple[FassungInfo, ...] = ()


def merken(fassungen: tuple[FassungInfo, ...]) -> None:
    global _gemerkt
    _gemerkt = fassungen


def vergessen() -> None:
    """Nach einer Wiederherstellung oder gewechselten Zugaengen."""
    merken(())


def aus_einstellungen(settings: AppSettings) -> tuple[FassungInfo, ...]:
    """Die bekannten Fassungen - ohne Sitzung, ohne Netz.

    ``settings`` bleibt in der Signatur, weil die Grenze sie so fragt; der
    NEX-Weg holt seine Fassungen aus der Tabelle, nicht aus den Einstellungen.
    """
    return _gemerkt


def aus_tabelle(db: Session) -> tuple[FassungInfo, ...]:
    """Die aktiven NEX-Fassungen aus der Tabelle lesen und merken."""
    zeilen = db.scalars(
        select(Fassung).where(Fassung.quelle == NEX, Fassung.aktiv.is_(True))
    ).all()
    gefunden = tuple(
        FassungInfo(
            kennung=zeile.kennung,
            media_type=zeile.media_type,
            name=zeile.name or zeile.kennung,
            klasse=zeile.klasse,
            reihenfolge=zeile.reihenfolge,
            quelle=NEX,
        )
        for zeile in sorted(zeilen, key=lambda z: (z.media_type, z.reihenfolge, z.kennung))
    )
    merken(gefunden)
    return gefunden


def abgleichen(db: Session, settings: AppSettings) -> None:
    """Den Zwischenspeicher auf den Stand der Tabelle bringen.

    Ohne Netz: Was nexcrate sagt, holt ``auffrischen``. Der Abgleich laeuft
    bei jedem Speichern der Einstellungen und beim Start; eine Abfrage an
    nexcrate an dieser Stelle machte jeden Start von einer fremden Anwendung
    abhaengig.
    """
    aus_tabelle(db)


def schreiben(db: Session, eintraege: list[dict]) -> tuple[FassungInfo, ...]:
    """Nexcrates Antwort in die Tabelle schreiben (ohne Commit).

    Die Reihenfolge kommt aus nexcrate (``order`` je Medienart); Filme stehen
    vor Serien, damit eine Liste ueber alle Fassungen stabil bleibt.
    """
    vorhanden = {zeile.kennung: zeile for zeile in db.scalars(select(Fassung))}
    jetzt = utcnow()
    gesehen: set[str] = set()
    versatz = {"movie": 0, "tv": 100}
    for eintrag in eintraege:
        info = mapping.fassung_info(eintrag, versatz.get(mapping.art(str(eintrag.get("kind"))), 200))
        if not info.kennung or not info.media_type:
            continue
        gesehen.add(info.kennung)
        zeile = vorhanden.get(info.kennung)
        if zeile is None:
            zeile = Fassung(
                kennung=info.kennung,
                media_type=info.media_type,
                quelle=NEX,
                # Gesperrt, bis der Betreiber sie freigibt.
                offen_fuer_alle=False,
            )
            db.add(zeile)
            logger.info("nexcrate has a new version %s (%s)", info.kennung, info.name)
        zeile.name = info.name
        zeile.media_type = info.media_type
        zeile.klasse = info.klasse
        zeile.reihenfolge = info.reihenfolge
        zeile.quelle = NEX
        zeile.aktiv = True
        zeile.bereit = bool(eintrag.get("ready"))
        zeile.gruende = mapping.gruende(eintrag)
        zeile.gesehen_am = jetzt
        zeile.verschwunden_am = None
    for kennung, zeile in vorhanden.items():
        if zeile.quelle == NEX and kennung not in gesehen and zeile.aktiv:
            zeile.aktiv = False
            zeile.bereit = False
            zeile.verschwunden_am = jetzt
            logger.info("nexcrate no longer has the version %s", kennung)
    db.flush()
    return aus_tabelle(db)


async def auffrischen(db: Session, settings: AppSettings) -> tuple[FassungInfo, ...]:
    """Die Fassungen bei nexcrate holen und in die Tabelle schreiben (ohne Commit)."""
    from .weg import client_fuer

    client = client_fuer(settings)
    return schreiben(db, await client.versions())
