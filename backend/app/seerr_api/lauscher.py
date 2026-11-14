"""Der zweite Port: oeffnen, wenn eingeschaltet, schliessen, wenn nicht.

Nexview laeuft als **ein** uvicorn-Prozess (SQLite, ein Arbeiter). Der
Seerr-Port ist deshalb kein zweiter Prozess, sondern ein zweiter
``uvicorn.Server`` in derselben Ereignisschleife, mit eigener App
(``app.py``). Eine Schleife hier sieht alle paar Sekunden in die
Einstellungen und oeffnet, schliesst oder wechselt den Port; nach dem
Speichern weckt ``aufwecken()`` sie sofort.

⚠️ **Drei Fallen, die uvicorn stellt, wenn man es so benutzt:**

* ``Server.serve`` faengt SIGINT/SIGTERM ab und setzt danach die alten
  Handler zurueck. Ein zweiter Server stuende damit dem ersten beim
  Herunterfahren im Weg. ``_OhneSignale`` laesst das weg; beendet wird er
  von hier aus ueber ``should_exit``.
* ``Config`` richtet ab Werk das Logging neu ein und ueberschriebe Nexviews
  Einrichtung (``logs.setup``). ``log_config=None`` laesst sie stehen.
* Ist der Port belegt, ruft uvicorn ``sys.exit(1)``. In einer Aufgabe ist das
  ein ``SystemExit``, der ohne Abfangen die ganze Anwendung beendete. Er wird
  hier zur Meldung "Port belegt".

⚠️ **Im Container muss der Port auch veroeffentlicht sein** (``-p 5055:5055``
bzw. ``ports:`` in Compose). Das kann Nexview nicht selbst; die Einstellungsseite
sagt es dazu.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass

import uvicorn

logger = logging.getLogger(__name__)

STANDARD_PORT = 5055

#: Wie oft die Schleife ungeweckt nachsieht.
TAKT_SEKUNDEN = 5.0

#: Nach einem belegten Port erst so viel spaeter erneut versuchen - sonst
#: stuende alle fuenf Sekunden dieselbe Fehlerzeile im Protokoll.
WARTEN_NACH_FEHLER = 60.0

_wecker: asyncio.Event | None = None
_schleife: asyncio.AbstractEventLoop | None = None


@dataclass(frozen=True)
class Zustand:
    laeuft: bool
    port: int | None
    fehler: str | None


_zustand = Zustand(laeuft=False, port=None, fehler=None)


def zustand() -> Zustand:
    """Was gerade ist - fuer die Einstellungsseite."""
    return _zustand


def aufwecken() -> None:
    """Nach dem Speichern: nicht auf den naechsten Takt warten.

    ⚠️ **Threadsicher, weil der Aufrufer im Threadpool laeuft.**
    ``update_settings`` ist ein gewoehnliches ``def``, FastAPI fuehrt es in
    einem Arbeitsthread aus. ``asyncio.Event.set()`` von dort weckt die
    Schleife nicht zuverlaessig (und wirft im Debug-Modus).
    """
    wecker, schleife = _wecker, _schleife
    if wecker is None or schleife is None or schleife.is_closed():
        return
    try:
        laufend = asyncio.get_running_loop()
    except RuntimeError:
        laufend = None
    if laufend is schleife:
        wecker.set()
    else:
        schleife.call_soon_threadsafe(wecker.set)


class _OhneSignale(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def _host() -> str:
    # Wie der Hauptport (``docker/entrypoint.sh`` startet mit 0.0.0.0). Wer
    # Nexview ohne Container nur lokal laufen laesst, kann es enger stellen.
    return os.getenv("NEXVIEW_SEERR_HOST", "0.0.0.0")  # noqa: S104 - bewusst wie der Hauptport


class Lauscher:
    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None
        self._aufgabe: asyncio.Task[None] | None = None
        self._port: int | None = None
        self._fehler_bei: tuple[int, float] | None = None

    async def anpassen(self, an: bool, port: int) -> None:
        global _zustand
        if self._aufgabe is not None and self._aufgabe.done():
            # Von selbst beendet - belegt oder abgestuerzt. Aufraeumen und
            # beim naechsten Mal neu versuchen (nach der Wartezeit).
            await self._abschliessen()

        if not an:
            if self._server is not None:
                await self.stoppen()
            _zustand = Zustand(laeuft=False, port=None, fehler=None)
            self._fehler_bei = None
            return

        if self._server is not None and self._port == port:
            return
        if self._server is not None:
            await self.stoppen()

        jetzt = asyncio.get_running_loop().time()
        if self._fehler_bei is not None:
            alter_port, zeitpunkt = self._fehler_bei
            if alter_port == port and jetzt - zeitpunkt < WARTEN_NACH_FEHLER:
                return

        await self._starten(port)

    async def _starten(self, port: int) -> None:
        global _zustand
        from .app import app  # erst hier: die App zieht die Router samt Datenbank nach

        config = uvicorn.Config(
            app,
            host=_host(),
            port=port,
            log_config=None,
            # Seerr spricht kein WebSocket; ohne das laedt uvicorn die
            # websockets-Bibliothek gar nicht erst.
            ws="none",
            lifespan="off",
            # Wie der Hauptport: Kopfzeilen eines Proxys gelten nur von 127.0.0.1.
            proxy_headers=True,
        )
        server = _OhneSignale(config)
        self._server = server
        self._port = port

        async def laufen() -> None:
            try:
                await server.serve()
            except SystemExit:
                # uvicorn meldet einen belegten Port mit sys.exit(1).
                pass

        self._aufgabe = asyncio.create_task(laufen(), name="seerr-api")
        # Kurz warten, bis er lauscht oder aufgegeben hat - sonst sagte die
        # Einstellungsseite "laeuft", waehrend der Port gerade scheitert.
        for _ in range(50):
            if server.started or self._aufgabe.done():
                break
            await asyncio.sleep(0.1)

        if server.started:
            logger.info("Seerr-compatible API listening on port %d", port)
            _zustand = Zustand(laeuft=True, port=port, fehler=None)
            self._fehler_bei = None
        else:
            logger.error(
                "Seerr-compatible API could not listen on port %d (in use or not allowed); retrying in %ds",
                port,
                int(WARTEN_NACH_FEHLER),
            )
            _zustand = Zustand(laeuft=False, port=port, fehler="port_unavailable")
            self._fehler_bei = (port, asyncio.get_running_loop().time())
            await self._abschliessen()

    async def stoppen(self) -> None:
        global _zustand
        if self._server is not None:
            self._server.should_exit = True
            logger.info("Seerr-compatible API on port %s stopped", self._port)
        await self._abschliessen()
        _zustand = Zustand(laeuft=False, port=None, fehler=_zustand.fehler)

    async def _abschliessen(self) -> None:
        aufgabe = self._aufgabe
        self._aufgabe = None
        self._server = None
        self._port = None
        if aufgabe is not None:
            try:
                await asyncio.wait_for(asyncio.shield(aufgabe), timeout=5)
            except TimeoutError:
                aufgabe.cancel()
            except asyncio.CancelledError:
                # Der Abbruch gilt dem, der hier wartet (Herunterfahren) - er
                # wird weitergereicht, nicht geschluckt. Der Server bekommt
                # ihn ebenfalls, statt verwaist weiterzulaufen.
                aufgabe.cancel()
                raise


def _wunsch() -> tuple[bool, int]:
    from ..db import SessionLocal
    from ..services.settings_service import load_settings

    with SessionLocal() as db:
        einstellungen = load_settings(db, frisch=True)
        return einstellungen.seerr_api_enabled, einstellungen.seerr_api_port


async def run_forever(stop: asyncio.Event) -> None:
    global _wecker, _schleife
    _wecker = asyncio.Event()
    _schleife = asyncio.get_running_loop()
    lauscher = Lauscher()
    try:
        while not stop.is_set():
            # Vor dem Lesen leeren, nicht danach: Ein Wecken, das kommt,
            # waehrend ``anpassen`` laeuft, gilt dann fuer die naechste Runde,
            # statt verschluckt zu werden.
            _wecker.clear()
            try:
                an, port = _wunsch()
                await lauscher.anpassen(an, port)
            except Exception:  # noqa: BLE001 - die Schleife darf nicht sterben
                logger.exception("Seerr-compatible API: could not apply the setting")
            warten = asyncio.ensure_future(_wecker.wait())
            ende = asyncio.ensure_future(stop.wait())
            try:
                await asyncio.wait({warten, ende}, timeout=TAKT_SEKUNDEN, return_when=asyncio.FIRST_COMPLETED)
            finally:
                warten.cancel()
                ende.cancel()
    finally:
        _wecker = None
        _schleife = None
        await lauscher.stoppen()
