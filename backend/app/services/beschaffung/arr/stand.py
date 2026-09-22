"""Was sich an einer Radarr- oder Sonarr-Instanz messen laesst.

Umgezogen aus ``services/instanz_stand.py`` (Scheibe 2 des NEX-Umbaus), ohne
Aenderung im Verhalten. Dort bleibt, was fuer jeden Weg gilt: die Zeile je
Instanz, "erreichbar seit", der Takt. Hier steht, welche Adressen der Instanz
dafuer gefragt werden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ...settings_service import AppSettings, ArrInstanz
from . import library
from .client import ArrClient, ArrError

#: Kurz, wie auf der Diensteseite: Die Frage ist "antwortet sie ueberhaupt",
#: nicht "wie schnell". Ein langer Timeout wuerde den ganzen Rundgang
#: aufhalten, sobald eine Instanz haengt.
ANTWORTFRIST = httpx.Timeout(4.0, connect=3.0)


@dataclass
class Messung:
    erreichbar: bool
    #: Leer, wenn die Instanz nicht geantwortet hat.
    version: str = ""
    #: Nur bei der vollen Messung, und nur was geantwortet hat.
    messwerte: dict = field(default_factory=dict)


async def messen(settings: AppSettings, instanz: ArrInstanz, *, voll: bool) -> Messung:
    """Eine Instanz befragen: jede Runde die Erreichbarkeit, stuendlich den Rest."""
    client = ArrClient(instanz.url, instanz.api_key, instanz.name)

    messung = Messung(erreichbar=True)
    try:
        antwort = await client.system_status(timeout=ANTWORTFRIST)
        messung.version = str((antwort or {}).get("version") or "")
    except ArrError:
        messung.erreichbar = False

    if voll and messung.erreichbar:
        messung.messwerte.update(await _volle_messung(client))
        luecken = await _luecken_messen(settings, instanz)
        if luecken is not None:
            messung.messwerte["luecken"] = luecken
    return messung


async def _luecken_messen(
    settings: AppSettings, instanz: ArrInstanz
) -> dict | None:
    """Was ueberwacht wird, aber (noch) nicht daliegt.

    ⚠️ **Kostet keinen zusaetzlichen Aufruf.** Radarr und Sonarr liefern
    ``monitored`` und ``has_file`` bei jedem Titel gratis mit, und der Rundgang
    holt die Bibliothek ohnehin - ``library`` haelt sie 60 Sekunden im
    Speicher. Es wird also nur ausgewertet, was schon da ist.

    Bei Serien zaehlen **Folgen**, nicht Serien: Eine Serie, der drei von
    sechzig Folgen fehlen, ist etwas anderes als eine, die ganz fehlt - und
    "eine Serie unvollstaendig" waere in beiden Faellen dieselbe Aussage.
    """
    try:
        if instanz.media_type == "movie":
            bestand = await library.movie_library(settings, instanz.tier)
            fehlend = sum(
                1 for e in bestand.values() if e.monitored and not e.has_file
            )
            return {"fehlend": fehlend, "einheit": "titel"}
        bestand, _ = await library.series_library(settings, instanz.tier)
        fehlend = sum(
            max(0, e.episode_count - e.episode_file_count)
            for e in bestand.values()
            if e.monitored
        )
        return {"fehlend": fehlend, "einheit": "folgen"}
    except ArrError:
        return None


async def _volle_messung(client: ArrClient) -> dict:
    """Was stuendlich gemessen wird. Jeder Teil einzeln abgesichert."""
    ergebnis: dict = {}

    try:
        neuer = await client.aktualisierung()
        # ``None`` heisst "aktuell **oder** unbekannt" - beides fuehrt zu
        # keinem Befund, und der Unterschied waere ohnehin nicht anzeigbar.
        ergebnis["aktualisierung"] = neuer
    except ArrError:
        pass

    try:
        ergebnis["warteschlange"] = await client.warteschlangen_zustand()
    except ArrError:
        pass

    return ergebnis
