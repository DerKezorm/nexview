"""Vorgangsnummer und Auffangnetz fuer jede Anfrage.

Zwei Dinge, die sich nur hier zentral erledigen lassen:

**Die Vorgangsnummer.** Jede eingehende Anfrage bekommt eine kurze Kennung, die
in *jeder* Protokollzeile steht, die waehrend dieser Anfrage entsteht. Meldet
jemand einen Fehler, nennt er die Nummer aus der Fehlermeldung - und ein
einziges Suchen im Protokoll zeigt den vollstaendigen Ablauf seines Klicks.
Vorher blieb nur die Uhrzeit, und die kennt der Meldende selten genau.

**Das Auffangnetz.** Ein unbehandelter Fehler ging bisher an der Protokolldatei
vorbei: uvicorn schreibt ihn auf seinen eigenen Logger, und der gibt nichts an
den Wurzel-Logger weiter. Der Administrator lud also das Protokoll herunter -
und ausgerechnet der Absturz fehlte darin.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from .services import logs
from .services.http_log import mask_query

logger = logging.getLogger("nexview.api")

#: Pfade, deren Aufrufe nichts erklaeren, aber das Protokoll fuellen.
QUIET_PATHS = ("/api/health", "/api/logs")


class RequestContextMiddleware:
    """Reines ASGI-Zwischenstueck - kein ``BaseHTTPMiddleware``.

    ``BaseHTTPMiddleware`` fuehrt die Anwendung in einer eigenen Aufgabe aus und
    macht damit Streaming-Antworten und Hintergrundaufgaben unnoetig heikel.
    Hier wird nur ein Wert gesetzt und die Zeit gemessen; dafuer braucht es das
    nicht.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        nummer = secrets.token_hex(3)
        scope.setdefault("state", {})["request_id"] = nummer

        # Wer die Anfrage stellt, steht erst nach der Anmeldepruefung fest -
        # ``deps.get_current_user`` traegt es nach.
        marke = logs.bind_request(nummer)
        start = time.perf_counter()
        status = 0

        async def send_wrapper(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                kopf = message.setdefault("headers", [])
                kopf.append((b"x-request-id", nummer.encode("ascii")))
            await send(message)

        methode = scope.get("method", "?")
        pfad = scope.get("path", "?")
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            dauer = (time.perf_counter() - start) * 1000
            # Mit Stacktrace, und noch **innerhalb** des Zusammenhangs: Sobald
            # der Fehler weiter oben ankommt, ist die Vorgangsnummer schon weg.
            logger.exception(
                "Unhandled error on %s %s after %dms", methode, self._pfad(scope), dauer
            )
            raise
        else:
            dauer = (time.perf_counter() - start) * 1000
            if status >= 500:
                logger.error(
                    "%s %s -> %s in %dms", methode, self._pfad(scope), status, dauer
                )
            elif not pfad.startswith(QUIET_PATHS):
                logger.debug(
                    "%s %s -> %s in %dms", methode, self._pfad(scope), status, dauer
                )
        finally:
            logs.unbind_request(marke)

    @staticmethod
    def _pfad(scope: dict) -> str:
        query = mask_query(scope.get("query_string", b"").decode("latin-1"))
        pfad = scope.get("path", "?")
        return f"{pfad}?{query}" if query else pfad


async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Antwort auf einen unbehandelten Fehler - mit der Vorgangsnummer.

    Protokolliert wird bereits im Zwischenstueck oben, wo der Zusammenhang noch
    steht. Hier geht es nur um die Antwort: Der Nutzer soll eine Nummer nennen
    koennen, statt die Uhrzeit zu schaetzen.
    """
    nummer = getattr(request.state, "request_id", None) or "-"
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": (
                    "Auf dem Server ist ein Fehler aufgetreten. "
                    f"Nummer für die Fehlersuche: {nummer}"
                ),
                "request_id": nummer,
            }
        },
        headers={"X-Request-Id": nummer},
    )


class BasisPfadMiddleware:
    """Streift den Unterpfad (``NEXVIEW_URL_BASE``) vom Anfragepfad.

    Damit beantwortet Nexview jede Adresse **doppelt**: mit Vorbau
    (``/nexview/api/health``) und ohne (``/api/health``). Beides ist noetig:

    * Ein **durchreichender** Proxy schickt die Anfrage mitsamt Vorbau weiter -
      der Normalfall, so erwarten es auch Radarr und Sonarr.
    * Ein **abschneidender** Proxy entfernt den Vorbau, bevor die Anfrage hier
      ankommt. Und der Docker-Healthcheck sowie die Radarr-/Sonarr-Webhooks
      rufen den Server ohnehin direkt an der Wurzel an.

    Es wird nur der Pfad umgeschrieben, sonst nichts: Routing, Erlaubnis-
    pruefungen und Protokoll sehen die Anfrage anschliessend so, als waere sie
    an der Wurzel angekommen. Ein Pfad, der den Vorbau nur scheinbar traegt
    (``/nexviewfoo``), bleibt unangetastet.
    """

    def __init__(self, app: Any, basis: str) -> None:
        self.app = app
        self.basis = basis.rstrip("/")
        self._praefix = self.basis + "/"
        self._basis_roh = self.basis.encode("latin-1")

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] in ("http", "websocket") and self.basis:
            pfad = scope.get("path", "")
            neu = None
            if pfad == self.basis:
                neu = "/"
            elif pfad.startswith(self._praefix):
                neu = pfad[len(self.basis):]
            if neu is not None:
                scope["path"] = neu
                # ``raw_path`` ist die unentschluesselte Urfassung; wo sie
                # vorliegt, muss sie denselben Schnitt bekommen, sonst zeigen
                # zwei Felder desselben Scopes auf verschiedene Adressen.
                roh = scope.get("raw_path")
                if isinstance(roh, bytes) and roh.startswith(self._basis_roh):
                    scope["raw_path"] = roh[len(self._basis_roh):] or b"/"
        await self.app(scope, receive, send)


class SicherheitskopfMiddleware:
    """Setzt die Inhaltsregeln an jede Antwort.

    Ebenfalls reines ASGI und aus demselben Grund wie oben: Es wird nur eine
    Kopfzeile angehaengt.

    An **jede** Antwort, nicht nur an die HTML-Seite. Das ist ein paar hundert
    Byte teurer und dafuer nicht zu vergessen - eine Regel, die nur an einem
    von mehreren Auslieferungswegen haengt, ist irgendwann keine Regel mehr.
    """

    def __init__(self, app: Any, name: str, regeln: str) -> None:
        self.app = app
        self.kopf = (name.encode("ascii"), regeln.encode("ascii"))

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append(self.kopf)
            await send(message)

        await self.app(scope, receive, send_wrapper)
