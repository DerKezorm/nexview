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
