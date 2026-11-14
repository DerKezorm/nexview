"""Die zweite ASGI-Anwendung: nur die Seerr-Adressen, sonst nichts.

⚠️ **Bewusst eine eigene App und kein Router an der Haupt-App.** Auf dem
Seerr-Port soll genau das erreichbar sein, was hier steht: keine Oberflaeche,
keine Nexview-API, keine Anmeldung mit Passwort. Ein Router an der Haupt-App
waere auf beiden Ports erreichbar, und der Port waere nur eine zweite Tuer
ins selbe Haus.

Fehler kommen in Seerrs Form (``{"message": "..."}``) statt in FastAPIs
(``{"detail": ...}``): Seerr-Clients lesen ``message`` und zeigen es teils an.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..middleware import RequestContextMiddleware
from .routen import router
from .zugang import SeerrFehler

logger = logging.getLogger(__name__)


def erstellen() -> FastAPI:
    app = FastAPI(
        title="Nexview Seerr-compatible API",
        version=__version__,
        # Keine Doku auf diesem Port: Was hier gilt, ist Seerrs API, und deren
        # Doku liegt bei Seerr. Ein eigenes Swagger waere eine zweite,
        # unvollstaendige Beschreibung derselben Adressen.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Vorgangsnummer und Protokoll wie auf dem Hauptport - ein Fehler auf dem
    # Seerr-Port soll im selben Log mit derselben Art Nummer stehen.
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(SeerrFehler)
    async def _seerr(_request: Request, fehler: SeerrFehler) -> JSONResponse:
        return JSONResponse({"message": fehler.message}, status_code=fehler.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, fehler: StarletteHTTPException) -> JSONResponse:
        text = fehler.detail if isinstance(fehler.detail, str) else "Request failed."
        if fehler.status_code == 404:
            text = "Not found. Nexview answers only the Seerr endpoints its supported clients use."
        return JSONResponse({"message": text}, status_code=fehler.status_code)

    @app.exception_handler(RequestValidationError)
    async def _ungueltig(_request: Request, fehler: RequestValidationError) -> JSONResponse:
        return JSONResponse({"message": "Invalid request.", "errors": fehler.errors()}, status_code=400)

    @app.exception_handler(Exception)
    async def _unerwartet(request: Request, fehler: Exception) -> JSONResponse:
        nummer = getattr(request.state, "request_id", "-")
        logger.exception("Seerr API: unhandled error in %s %s [%s]", request.method, request.url.path, nummer)
        return JSONResponse({"message": f"Internal error ({nummer})."}, status_code=500)

    app.include_router(router)
    return app


app = erstellen()
