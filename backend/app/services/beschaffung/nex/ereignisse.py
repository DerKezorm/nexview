"""nexcrates Ereignisse: ein Wecker, keine Wahrheit.

Dasselbe Muster wie bei den Arr-Webhooks - nur sagt der Wecker hier, **was**
zu prüfen ist (Bauplan 6.8). Geglaubt wird einem Ereignis nichts: Der Zustand
kommt aus `lookup`, die Warteschlange aus `queue`. Ein Ereignis zieht den
Rundgang nur vor.

Zwei Wege, und sie ergänzen sich:

* **Der Strom** (`GET /events/stream`) hält eine Verbindung, der Zustand
  wechselt in Sekunden (N31). Er läuft in **eine** Richtung - nexcrate muss
  Nexview gar nicht erreichen können.
* **Der Feed** (`GET /events?after=`) holt nach, was der Strom verpasst hat.
  Bei Arr ist ein verpasster Webhook weg; hier nicht (N30).

⚠️ **Fällt der Strom aus, läuft alles weiter** - nur langsamer, im Takt von
`poll_interval_seconds`. Ein Rückkanal, ohne den nichts mehr geht, wäre der
gleiche Fehler wie bei Arr.

⚠️ **Die Marke gehört zu einer Installation.** Wechselt sie, sind alle
gemerkten Nummern wertlos (nexbeat-Befund 11).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ...settings_service import load_settings, save_settings

if TYPE_CHECKING:
    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")

#: Nach einem Fehler so lange warten, bevor der Strom neu verbunden wird.
#: nexcrate beendet ihn planmäßig nach einer Stunde - dann sofort wieder hin.
WARTEN_NACH_FEHLER = 30.0

#: Was welcher Wecker anstößt.
DOWNLOADS = ("download.", "problem.")
TITEL = ("title.", "version.", "request.", "file.", "series.")
FASSUNGEN = ("version_definition.",)
GESUNDHEIT = ("health.",)
UEBERNAHME = ("source.",)


class Wecker:
    """Was der nächste Rundgang nachsehen soll.

    Absichtlich grob: Ein Ereignis sagt „bei diesem Titel hat sich etwas
    getan", nicht was. Feiner zu werden hieße, dem Ereignis zu glauben.
    """

    def __init__(self) -> None:
        self.downloads = False
        self.titel = False
        self.fassungen = False
        self.gesundheit = False
        self.bestand = False

    def merken(self, art: str) -> bool:
        """Ein Ereignis einordnen. ``True``, wenn es den Rundgang vorzieht."""
        if art.startswith(DOWNLOADS):
            self.downloads = True
        elif art.startswith(FASSUNGEN):
            self.fassungen = True
        elif art.startswith(GESUNDHEIT):
            self.gesundheit = True
        elif art.startswith(UEBERNAHME):
            self.bestand = True
        elif art.startswith(TITEL):
            self.titel = True
        else:
            # Eine Art, die Nexview nicht kennt, ist kein Fehler - nexcrate
            # darf wachsen. Geweckt wird trotzdem: Lieber einmal zu viel
            # nachsehen als eine Änderung verschlafen.
            self.titel = True
        return True

    def abholen(self) -> Wecker:
        """Den Stand nehmen und zurücksetzen."""
        stand = Wecker()
        stand.__dict__.update(self.__dict__)
        self.__init__()
        return stand

    @property
    def leer(self) -> bool:
        return not any(self.__dict__.values())


_wecker = Wecker()


def wecker() -> Wecker:
    return _wecker


def marke_lesen(settings: AppSettings) -> int:
    """Die gemerkte Ereignis-Marke - aber nur, wenn sie zu dieser nexcrate gehört."""
    from . import system

    roh = settings.nexcrate_events_after
    installation, _, nummer = roh.partition(":")
    if not nummer.isdigit():
        return 0
    jetzt = system.installation_id() or settings.nexcrate_installation_id
    return int(nummer) if installation == jetzt else 0


def marke_schreiben(settings: AppSettings, nummer: int) -> None:
    """Die Marke **mit** der Installation merken (nexbeat-Befund 11)."""
    from ....db import SessionLocal
    from . import system

    installation = system.installation_id() or settings.nexcrate_installation_id
    with SessionLocal() as db:
        save_settings(db, {"nexcrate_events_after": f"{installation}:{nummer}"})


async def nachholen(settings: AppSettings) -> int:
    """Was der Strom verpasst hat - lückenlos ab der Marke (N30).

    Gibt die Zahl der eingeordneten Ereignisse zurück.
    """
    from .weg import client_fuer

    after = marke_lesen(settings)
    antwort = await client_fuer(settings).events(after=after)
    gezaehlt = 0
    for ereignis in antwort.get("items") or []:
        _wecker.merken(str(ereignis.get("type") or ""))
        gezaehlt += 1
    neue = int(antwort.get("next_after") or after)
    if neue != after:
        marke_schreiben(settings, neue)
    return gezaehlt


async def _strom_einmal(settings: AppSettings) -> None:
    """Eine Verbindung, bis nexcrate sie beendet."""
    from .weg import client_fuer

    after = marke_lesen(settings)
    letzte = after
    async for ereignis in client_fuer(settings).stream(after):
        art = str(ereignis.get("type") or "")
        nummer = int(ereignis.get("seq") or 0)
        if nummer:
            letzte = max(letzte, nummer)
        if _wecker.merken(art):
            _weckruf_setzen()
    if letzte != after:
        marke_schreiben(settings, letzte)


def _weckruf_setzen() -> None:
    from .weg import NexBeschaffung

    try:
        NexBeschaffung.weckruf().set()
    except RuntimeError:
        # Ohne laufende Schleife gibt es niemanden zu wecken.
        pass


async def run_forever(stop: asyncio.Event) -> None:
    """Der Strom als Dauerläufer.

    ⚠️ **Nur im NEX-Betrieb und nur mit Zugang.** Beides kann sich im Betrieb
    ändern, deshalb wird es je Runde gefragt statt einmal beim Start.
    """
    from ....db import SessionLocal
    from .fehler import NexcrateError

    while not stop.is_set():
        with SessionLocal() as db:
            settings = load_settings(db, frisch=True)
        if not settings.beschaffung_ist_nex or not settings.nexcrate_configured:
            await _warten(stop, WARTEN_NACH_FEHLER)
            continue
        try:
            await _strom_einmal(settings)
            # Planmäßiges Ende nach einer Stunde: sofort wieder hin.
        except NexcrateError as fehler:
            logger.info("The event stream stopped (%s); trying again", fehler.code)
            await _warten(stop, WARTEN_NACH_FEHLER)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - der Strom darf den Prozess nie umreissen
            logger.exception("The event stream failed")
            await _warten(stop, WARTEN_NACH_FEHLER)


async def _warten(stop: asyncio.Event, sekunden: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=sekunden)
    except TimeoutError:
        pass
