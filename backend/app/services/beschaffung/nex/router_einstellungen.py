"""Einstellungen, die nur nexcrate betreffen. Nur fuer Administratoren.

Drei Dinge: die Verbindung pruefen, koppeln (N8) und den Stand zeigen.

⚠️ **Koppeln ist der schoene Weg, nicht der einzige.** Wer den Schluessel in
nexcrate selbst anlegt, traegt ihn wie einen Arr-Schluessel ein. Koppeln
spart das Kopieren: Nexview bittet um einen Schluessel, der Betreiber
bestaetigt in nexcrate, Nexview bekommt ihn **genau einmal**.

⚠️ **Das Geheimnis der Bitte bleibt im Prozess.** Es ist zehn Minuten
gueltig und gehoert weder in die Datenbank noch in eine Antwort; wer die
Bitte startet, fragt mit derselben Sitzung nach. Faellt Nexview dazwischen
aus, koppelt man neu - und in nexcrate steht eine Bitte, die von selbst
verfaellt (nexbeat-Befund 14).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .... import meldungen
from ....deps import AdminUser, DbSession
from ....models import utcnow
from ....routers.settings import TestResult
from ...settings_service import load_settings, save_settings
from . import fassungen, mapping, pruefung, system
from .client import RECHTE, NexcrateClient
from .fehler import NexcrateError
from .weg import APP_NAME

router = APIRouter(prefix="/api", tags=["settings"])

logger = logging.getLogger("nexview.nexcrate")

#: So lange haelt nexcrate eine Bitte ums Koppeln (gemessen: zehn Minuten).
BITTE_GUELTIG = timedelta(minutes=10)

#: Offene Bitten ums Koppeln: Kennung -> (Geheimnis, Adresse, Ablauf).
#: Nur im Prozess - siehe Kopf der Datei.
_bitten: dict[str, tuple[str, str, Any]] = {}


def _aufraeumen() -> None:
    jetzt = utcnow()
    for kennung in [k for k, (_, _, ablauf) in _bitten.items() if ablauf < jetzt]:
        _bitten.pop(kennung, None)


class Verbindungsprobe(BaseModel):
    """Noch nicht gespeicherte Zugangsdaten, um sie vorab zu pruefen."""

    url: str | None = None
    api_key: str | None = None


class BitteStart(BaseModel):
    url: str = Field(min_length=1, max_length=300)


class BitteOffen(BaseModel):
    """Was die Oberflaeche zeigt, bis der Betreiber bestaetigt hat."""

    pairing_id: str
    code: str
    poll_seconds: int
    expires_at: str | None = None


class BitteStand(BaseModel):
    """Der Stand einer Bitte. ``gespeichert`` heisst: Der Schluessel liegt."""

    state: str
    gespeichert: bool = False
    installation_id: str = ""
    version: str = ""
    fassungen: int = 0
    #: ⚠️ **Gleich beim Koppeln geprueft** (7.2). Wer erst am Ende der
    #: Einrichtung erfaehrt, dass diese nexcrate zu alt ist, hat alles
    #: umsonst eingetragen.
    pruefung: list[dict[str, Any]] = Field(default_factory=list)


class NexStand(BaseModel):
    """Was nexcrate ueber sich sagt - fuer die Dienste-Seite."""

    eingerichtet: bool
    erreichbar: bool
    version: str = ""
    vertrag: str = ""
    installation_id: str = ""
    web_url: str = ""
    update_verfuegbar: bool = False
    update_version: str = ""
    anime: bool = False
    fassungen: list[dict[str, Any]] = Field(default_factory=list)
    probleme: list[dict[str, Any]] = Field(default_factory=list)
    #: Die Standpruefung (Bauplan 7.2): Was gegen diese nexcrate spricht.
    #: Leer heisst, sie taugt. ``stufe`` ist ``sperrt`` oder ``warnt``.
    pruefung: list[dict[str, Any]] = Field(default_factory=list)
    fehler: str = ""


def _client(url: str, key: str) -> NexcrateClient:
    return NexcrateClient(url.strip().rstrip("/"), key)


@router.post("/settings/nexcrate/test", response_model=TestResult)
async def verbindung_pruefen(
    payload: Verbindungsprobe, admin: AdminUser, db: DbSession
) -> TestResult:
    """Antwortet dort ein nexcrate, und nimmt es den Schluessel?

    Nimmt die uebergebenen Daten, wo sie da sind - so laesst sich pruefen,
    bevor gespeichert wird.
    """
    settings = load_settings(db)
    url = (payload.url or settings.nexcrate_url).strip().rstrip("/")
    key = payload.api_key or settings.nexcrate_api_key
    if not url:
        return TestResult(ok=False, message="Es ist keine Adresse hinterlegt.")
    if not key:
        return TestResult(ok=False, message="Es ist kein Schlüssel hinterlegt.")
    try:
        daten = await _client(url, key).system()
    except NexcrateError as fehler:
        return TestResult(ok=False, message=fehler.message)
    version = str(daten.get("version") or "?")
    vertrag = (daten.get("contract") or {}).get("stage") or ""
    return TestResult(ok=True, message=f"nexcrate {version} antwortet (Vertrag {vertrag}).")


@router.post("/settings/nexcrate/pairing", response_model=BitteOffen)
async def koppeln_starten(payload: BitteStart, admin: AdminUser, db: DbSession) -> BitteOffen:
    """Um einen Schluessel bitten. Die Adresse allein genuegt - kein Schluessel."""
    _aufraeumen()
    url = payload.url.strip().rstrip("/")
    try:
        antwort = await _client(url, "").pairing_ask(APP_NAME, RECHTE)
    except NexcrateError as fehler:
        raise HTTPException(
            status_code=502, detail=fehler.als_meldung()
        ) from fehler
    kennung = str(antwort.get("pairing_id") or "")
    geheimnis = str(antwort.get("secret") or "")
    if not kennung or not geheimnis:
        raise HTTPException(
            status_code=502,
            detail=meldungen.meldung(
                "nexcrate_unexpected_answer",
                "nexcrate hat auf die Bitte ums Koppeln unerwartet geantwortet.",
            ),
        )
    _bitten[kennung] = (geheimnis, url, utcnow() + BITTE_GUELTIG)
    return BitteOffen(
        pairing_id=kennung,
        code=str(antwort.get("code") or ""),
        poll_seconds=int(antwort.get("poll_seconds") or 2),
        expires_at=antwort.get("expires_at"),
    )


@router.get("/settings/nexcrate/pairing/{pairing_id}", response_model=BitteStand)
async def koppeln_nachfragen(pairing_id: str, admin: AdminUser, db: DbSession) -> BitteStand:
    """Hat der Betreiber bestaetigt? Wenn ja, wird der Schluessel gespeichert.

    ⚠️ **Der Schluessel kommt genau einmal.** Er wird deshalb gespeichert,
    bevor irgendetwas anderes geschieht; erst danach wird ``/system``
    gelesen. Geht das schief, steht der Zugang trotzdem.
    """
    _aufraeumen()
    offen = _bitten.get(pairing_id)
    if offen is None:
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung(
                "nexcrate_pairing_gone",
                "Die Bitte ums Koppeln ist abgelaufen. Starte sie neu.",
            ),
        )
    geheimnis, url, _ = offen
    try:
        antwort = await _client(url, "").pairing_poll(pairing_id, geheimnis)
    except NexcrateError as fehler:
        if fehler.code == "nexcrate_pairing_gone":
            _bitten.pop(pairing_id, None)
            return BitteStand(state="expired")
        raise HTTPException(status_code=502, detail=fehler.als_meldung()) from fehler

    zustand = str(antwort.get("state") or "")
    schluessel = str(antwort.get("key") or "")
    if zustand != "confirmed" or not schluessel:
        return BitteStand(state=zustand or "pending")

    _bitten.pop(pairing_id, None)
    save_settings(db, {"nexcrate_url": url, "nexcrate_api_key": schluessel})
    logger.info("Nexview is paired with nexcrate at %s", url)
    return await _nach_dem_koppeln(db)


async def _nach_dem_koppeln(db: DbSession) -> BitteStand:
    """Gleich lesen, was diese nexcrate ist und was sie fuehrt.

    Der Bestand kommt erst mit dem naechsten Rundgang; die Fassungen aber
    sofort, weil ohne sie kein Formular etwas anzubieten haette.
    """
    settings = load_settings(db, frisch=True)
    stand = BitteStand(state="confirmed", gespeichert=True)
    try:
        daten = await system.auffrischen(settings)
    except NexcrateError as fehler:
        logger.warning("Paired, but nexcrate did not answer /system: %s", fehler.code)
        return stand
    save_settings(
        db,
        {
            "nexcrate_installation_id": str(daten.get("installation_id") or ""),
            "nexcrate_web_url": str(daten.get("web_url") or ""),
        },
    )
    stand.installation_id = str(daten.get("installation_id") or "")
    stand.version = str(daten.get("version") or "")
    stand.pruefung = _pruefung(daten)
    try:
        gefunden = await fassungen.auffrischen(db, settings)
        db.commit()
    except NexcrateError as fehler:
        db.rollback()
        logger.warning("Paired, but nexcrate did not answer /versions: %s", fehler.code)
        return stand
    stand.fassungen = len(gefunden)
    return stand


@router.get("/settings/nexcrate/status", response_model=NexStand)
async def stand_lesen(admin: AdminUser, db: DbSession) -> NexStand:
    """Version, Update, Fassungen und offene Befunde - fuer die Dienste-Seite."""
    settings = load_settings(db)
    if not settings.nexcrate_configured:
        return NexStand(eingerichtet=False, erreichbar=False)
    try:
        daten = await system.auffrischen(settings)
    except NexcrateError as fehler:
        return NexStand(eingerichtet=True, erreichbar=False, fehler=fehler.code or "")

    gemerkt = settings.nexcrate_installation_id
    neu = str(daten.get("installation_id") or "")
    aenderungen: dict[str, object] = {"nexcrate_web_url": str(daten.get("web_url") or "")}
    if neu and neu != gemerkt:
        # Eine andere Installation unter derselben Adresse: Die gemerkten
        # Marken gehoeren ihr nicht (nexbeat-Befund 11).
        aenderungen |= {
            "nexcrate_installation_id": neu,
            "nexcrate_titles_after": "",
            "nexcrate_events_after": "",
        }
        logger.info("nexcrate reports a new installation id; markers are dropped")
    save_settings(db, aenderungen)

    liste: list[dict[str, Any]] = []
    probleme: list[dict[str, Any]] = []
    try:
        eintraege = await _versionen(db, settings)
        liste = [
            {
                "kennung": eintrag.get("version_id"),
                # ⚠️ Nexviews Art (``tv``), nicht nexcrates ``kind``
                # (``series``) - die Oberflaeche uebersetzt nur die eigene.
                "media_type": mapping.art(str(eintrag.get("kind") or "")),
                "name": eintrag.get("name"),
                "klasse": eintrag.get("tier"),
                "bereit": bool(eintrag.get("ready")),
                "gruende": [g.get("code") for g in eintrag.get("reasons") or []],
            }
            for eintrag in eintraege
            if mapping.fuehrt_nexview(eintrag)
        ]
        probleme = [
            {"code": eintrag.get("code"), "level": eintrag.get("level"), "params": eintrag.get("params")}
            for eintrag in await _befunde(settings)
        ]
    except NexcrateError as fehler:
        db.rollback()
        return NexStand(
            eingerichtet=True,
            erreichbar=True,
            version=str(daten.get("version") or ""),
            fehler=fehler.code or "",
        )

    update = daten.get("update") or {}
    return NexStand(
        pruefung=_pruefung(daten),
        eingerichtet=True,
        erreichbar=True,
        version=str(daten.get("version") or ""),
        vertrag=str((daten.get("contract") or {}).get("stage") or ""),
        installation_id=neu,
        web_url=str(daten.get("web_url") or ""),
        update_verfuegbar=bool(update.get("available")),
        update_version=str(update.get("latest") or ""),
        anime=system.faehigkeiten().anime,
        fassungen=liste,
        probleme=probleme,
    )


async def _versionen(db: DbSession, settings: Any) -> list[dict[str, Any]]:
    """Die Fassungen lesen und gleich in die Tabelle schreiben."""
    from .weg import client_fuer

    eintraege = await client_fuer(settings).versions()
    fassungen.schreiben(db, eintraege)
    db.commit()
    return eintraege


async def _befunde(settings: Any) -> list[dict[str, Any]]:
    from .weg import client_fuer

    return await client_fuer(settings).health()


def _pruefung(daten: dict[str, Any]) -> list[dict[str, Any]]:
    """Die Standpruefung als Liste fuer die Oberflaeche - Kennungen, keine Saetze."""
    return [
        {"code": befund.code, "stufe": befund.stufe, "werte": befund.werte}
        for befund in pruefung.pruefen(daten)
    ]
