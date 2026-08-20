"""Einstiegspunkt von Nexview.

Im Docker-Container liefert dieselbe Anwendung sowohl die API unter ``/api/*``
als auch das gebaute React-Frontend aus. Im Entwicklungsmodus laeuft das
Frontend separat auf dem Vite-Server und wird ueber CORS erlaubt.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import get_settings
from .db import init_db
from .routers import (
    about as about_router,
    admin_requests,
    auth,
    blocklist as blocklist_router,
    calendar as calendar_router,
    channels as channels_router,
    details as details_router,
    discover,
    favorites as favorites_router,
    home as home_router,
    logs as logs_router,
    mediaserver as mediaserver_router,
    notifications,
    onboarding,
    requests as requests_router,
    settings as settings_router,
    setup,
    stats as stats_router,
    tickets as tickets_router,
    users,
    watchlist as watchlist_router,
)
from .services import channel_outbox, logs, status_poller
from .services.arr import close_http_client as close_arr_client
from .services.mediaserver import close_http_client as close_mediaserver_client
from .services.tmdb import close_http_client

settings = get_settings()


# In Tests wuerde die Hintergrundschleife nur stoeren.
POLLER_ENABLED = os.getenv("NEXVIEW_DISABLE_POLLER", "").lower() not in ("1", "true", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs.setup()
    init_db()
    # Erzeugt beim allerersten Start data/secret.key, falls kein
    # NEXVIEW_SECRET_KEY gesetzt ist.
    settings.resolved_secret_key()

    # Startbericht: Woher kommt der Geheimschluessel, und ist die
    # Media-Server-Verbindung mit ihm lesbar? Das Raetsel "Verbindung weg"
    # liess sich aus der Ferne nie entscheiden, weil genau diese drei Zeilen
    # fehlten - mit ihnen steht die Antwort im ersten Log nach dem Start.
    # Der Schluessel selbst wird selbstverstaendlich nie geloggt.
    from .services.settings_service import verbindungsbericht

    verbindungsbericht()

    stop = asyncio.Event()
    # Zwei Schleifen mit unterschiedlichem Takt. Die Status-Abfrage fragt
    # standardmaessig alle zwei Minuten bei Radarr/Sonarr nach; der Versand
    # ueber die serverseitigen Kanaele alle zehn Sekunden, weil eine
    # Push-Nachricht sonst zwei Minuten nach dem Klick eintrudelt.
    tasks = (
        [
            asyncio.create_task(status_poller.run_forever(stop)),
            asyncio.create_task(channel_outbox.run_forever(stop)),
        ]
        if POLLER_ENABLED
        else []
    )

    yield

    if tasks:
        stop.set()
        for task in tasks:
            task.cancel()
        try:
            # Mit Zeitgrenze: das Herunterfahren darf nie an einer
            # Hintergrundschleife haengenbleiben (Container-Stopp, Neustart).
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=5
            )
        except (asyncio.CancelledError, TimeoutError):
            pass

    await close_http_client()
    await close_arr_client()
    await close_mediaserver_client()


app = FastAPI(
    title="Nexview",
    version=__version__,
    description="Persoenliches Media-Discovery-Dashboard mit Radarr-/Sonarr-Anbindung.",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(setup.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(settings_router.router)
app.include_router(channels_router.router)
app.include_router(discover.router)
app.include_router(requests_router.router)
app.include_router(admin_requests.router)
app.include_router(stats_router.router)
app.include_router(home_router.router)
app.include_router(onboarding.router)
app.include_router(notifications.router)
app.include_router(logs_router.router)
app.include_router(about_router.router)
app.include_router(details_router.router)
app.include_router(calendar_router.router)
app.include_router(favorites_router.router)
app.include_router(blocklist_router.router)
app.include_router(tickets_router.router)
app.include_router(mediaserver_router.router)
app.include_router(mediaserver_router.admin_router)
app.include_router(watchlist_router.router)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


def _static_dir() -> Path | None:
    """Ordner mit dem gebauten Frontend, falls vorhanden."""
    if settings.static_dir:
        candidate = Path(settings.static_dir)
    else:
        candidate = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    return candidate if (candidate / "index.html").exists() else None


def _mount_frontend(directory: Path) -> None:
    """Gebautes Frontend ausliefern.

    React Router verwaltet die Adressen im Browser. Ruft jemand direkt
    ``/login`` auf, kennt der Server diese Datei nicht - deshalb wird bei
    unbekannten Pfaden ``index.html`` zurueckgegeben (SPA-Fallback).
    ``/api/*`` bleibt davon ausgenommen und liefert weiterhin echte 404er.
    """
    app.mount("/assets", StaticFiles(directory=directory / "assets"), name="assets")
    index_file = directory / "index.html"

    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(request, exc: StarletteHTTPException):
        if exc.status_code == 404 and not request.url.path.startswith("/api"):
            return FileResponse(index_file)
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        # Unbekannte API-Adressen duerfen nicht die Weboberflaeche liefern -
        # sonst bekaeme ein Tippfehler in der URL eine HTML-Seite mit Status
        # 200 statt eines Fehlers.
        if full_path == "api" or full_path.startswith("api/"):
            raise StarletteHTTPException(status_code=404, detail="Not Found")

        candidate = directory / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)


_frontend_dir = _static_dir()
if _frontend_dir is not None:
    _mount_frontend(_frontend_dir)
