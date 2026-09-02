"""Einstiegspunkt von Nexview.

Im Docker-Container liefert dieselbe Anwendung sowohl die API unter ``/api/*``
als auch das gebaute React-Frontend aus. Im Entwicklungsmodus laeuft das
Frontend separat auf dem Vite-Server und wird ueber CORS erlaubt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, api_texte
from .config import get_settings
from .db import init_db
from .deps import require_adult
from .middleware import (
    BasisPfadMiddleware,
    RequestContextMiddleware,
    SicherheitskopfMiddleware,
    unhandled_error,
)
from .routers import (
    about as about_router,
)
from .routers import (
    admin_requests,
    auth,
    discover,
    notifications,
    onboarding,
    setup,
    users,
)
from .routers import (
    analyse as analyse_router,
)
from .routers import (
    blocklist as blocklist_router,
)
from .routers import (
    calendar as calendar_router,
)
from .routers import (
    channels as channels_router,
)
from .routers import (
    children as children_router,
)
from .routers import (
    dashboard as dashboard_router,
)
from .routers import (
    details as details_router,
)
from .routers import (
    favorites as favorites_router,
)
from .routers import (
    feedback as feedback_router,
)
from .routers import (
    hausordnung as hausordnung_router,
)
from .routers import (
    home as home_router,
)
from .routers import (
    kids as kids_router,
)
from .routers import (
    logs as logs_router,
)
from .routers import (
    mediaserver as mediaserver_router,
)
from .routers import (
    oidc as oidc_router,
)
from .routers import (
    qualitaetsprofile as qualitaetsprofile_router,
)
from .routers import (
    regeln as regeln_router,
)
from .routers import (
    requests as requests_router,
)
from .routers import (
    settings as settings_router,
)
from .routers import (
    sicherungen as sicherungen_router,
)
from .routers import (
    stats as stats_router,
)
from .routers import (
    stoebern as stoebern_router,
)
from .routers import (
    storage as storage_router,
)
from .routers import (
    streaming as streaming_router,
)
from .routers import (
    tickets as tickets_router,
)
from .routers import (
    v1 as v1_router,
)
from .routers import (
    watch as watch_router,
)
from .routers import (
    watchlist as watchlist_router,
)
from .routers import (
    webhooks as webhooks_router,
)
from .services import (
    benennung,
    channel_outbox,
    csp,
    logs,
    sicherung,
    status_poller,
    trash_bezug,
)
from .services.arr import close_http_client as close_arr_client
from .services.mediaserver import close_http_client as close_mediaserver_client
from .services.oidc import close_http_client as close_oidc_client
from .services.tmdb import close_http_client

settings = get_settings()


# In Tests wuerde die Hintergrundschleife nur stoeren.
POLLER_ENABLED = os.getenv("NEXVIEW_DISABLE_POLLER", "").lower() not in ("1", "true", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs.setup()
    init_db()
    # Erst jetzt: Die gewaehlte Protokoll-Stufe steht in der Datenbank, und die
    # gibt es beim allerersten Start noch nicht.
    logs.apply_stored_mode()
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
    # Der Waechter der Protokoll-Stufe laeuft immer - auch ohne Poller. Eine
    # eingeschaltete Diagnose-Stufe muss sich verlaesslich selbst abschalten.
    tasks.append(asyncio.create_task(logs.run_forever(stop)))
    # Regelmaessige Sicherungen. Haengt am selben Schalter wie der Poller:
    # ⚠️ Diese Schleife **schreibt Dateien**. In Tests laege sonst nach jedem
    # Lauf eine Sicherung im Datenverzeichnis, und ein Test, der Staende zaehlt,
    # zaehlte die des Nachbarn mit.
    if POLLER_ENABLED:
        tasks.append(asyncio.create_task(sicherung.run_forever(stop)))
        # Einmal am Tag nachsehen, ob es einen neueren TRaSH-Stand gibt.
        # ⚠️ Nur nachsehen - geholt wird nie von selbst. Ein Stand, der sich
        # ungefragt aendert, verschoebe stillschweigend die Profile in
        # Radarr/Sonarr. Haengt am Poller-Schalter, weil hier das Netz
        # angesprochen wird und Tests das nicht tun sollen.
        tasks.append(asyncio.create_task(trash_bezug.run_forever(stop)))

        # ⚠️ **Abgebrochene Umbenennungslaeufe wieder aufnehmen.** Ein Lauf
        # ueber mehrere tausend Titel dauert lange; faellt der Prozess
        # mittendrin aus, bliebe sonst eine halb umbenannte Bibliothek zurueck -
        # teils altes, teils neues Schema, ohne erkennbare Grenze. Ohne diesen
        # Aufruf waere der gespeicherte Zwischenstand wertlos.
        start_log = logging.getLogger("nexview.qualitaet")
        try:
            aufgenommen = benennung.abgebrochene_aufnehmen()
            if aufgenommen:
                start_log.info("Picked up %d unfinished rename run(s)", aufgenommen)
        except Exception:  # noqa: BLE001 - der Start darf daran nicht scheitern
            start_log.exception("Could not pick up unfinished rename runs")

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
    await close_oidc_client()


app = FastAPI(
    title="Nexview",
    version=__version__,
    description="Persoenliches Media-Discovery-Dashboard mit Radarr-/Sonarr-Anbindung.",
    lifespan=lifespan,
    # ⚠️ **Beide abgeschaltet - und weiter unten selbst gebaut.**
    #
    # FastAPIs eigene Doku-Seiten holen Swagger UI bzw. ReDoc von
    # ``cdn.jsdelivr.net``. Seit 0.21.0 schickt Nexview eine
    # Content-Security-Policy mit ``script-src 'self'``, und damit weigert sich
    # der Browser, diese Dateien zu laden: ``/docs`` und ``/redoc`` waren in
    # jeder Installation eine **weisse Seite**. Nicht kaputt aussehend - leer.
    #
    # Das ist beim Einbau der CSP niemandem aufgefallen, weil geprueft wurde,
    # ob die *Anwendung* noch laeuft. Sie lief. Die Doku-Seiten gehoeren aber
    # nicht zur Anwendung, sondern zu FastAPI, und niemand ruft sie im Alltag
    # auf.
    docs_url=None,
    redoc_url=None,
)

# Vorgangsnummer fuer jede Anfrage. Bewusst vor CORS eingetragen, damit CORS
# aussen liegt und die Vorabfragen (OPTIONS) gar nicht erst hier ankommen.
app.add_middleware(RequestContextMiddleware)

# Auffangnetz: Ein unbehandelter Fehler soll eine nennbare Nummer haben, statt
# nur "Internal Server Error" zu sagen.
app.add_exception_handler(Exception, unhandled_error)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ⚠️ **Erlaubnisliste, keine Verbotsliste.** Jeder Router, der nicht
# ausdruecklich fuer Kinderkonten gedacht ist, bekommt hier die
# Erwachsenen-Pruefung - und zwar am Router, nicht am einzelnen Endpunkt.
#
# Umgekehrt gedacht ("ich sperre die Stellen, die einem Kind schaden") waere
# die erste vergessene Zeile kein falsches Abzeichen, sondern ein Kind in einer
# Erwachsenenfunktion. ``test_child_permissions.py`` laeuft ueber die ganze
# Routentabelle und schlaegt fehl, sobald ein Pfad weder hier haengt noch in
# der Kinder-Erlaubnisliste steht.
#
# Router, die schon durchgehend Administratoren oder Entscheidern vorbehalten
# sind (``users``, ``settings``, ``logs``, ``blocklist``, ``admin_requests``,
# ``stats``, ``channels``, ``mediaserver.admin_router``), brauchen nichts
# zusaetzlich - ein Kind ist weder das eine noch das andere.
NUR_ERWACHSENE = [Depends(require_adult)]

app.include_router(setup.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(settings_router.router)
app.include_router(channels_router.router)
# Durchgehend admin-only, deshalb ohne NUR_ERWACHSENE.
app.include_router(qualitaetsprofile_router.router)
app.include_router(children_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(discover.router, dependencies=NUR_ERWACHSENE)
app.include_router(stoebern_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(discover.public_router)
app.include_router(kids_router.router)
app.include_router(requests_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(feedback_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(admin_requests.router)
app.include_router(hausordnung_router.router)
app.include_router(stats_router.router)
# Durchgehend admin-only, deshalb ohne NUR_ERWACHSENE - wie qualitaetsprofile.
app.include_router(dashboard_router.router)
app.include_router(analyse_router.router)
app.include_router(home_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(onboarding.router)
app.include_router(notifications.router, dependencies=NUR_ERWACHSENE)
app.include_router(logs_router.router)
app.include_router(sicherungen_router.router)
# ⚠️ **Die zugesagte Flaeche.** Dieselben Handler, zweite Adresse - siehe
# routers/v1.py. Bewusst zuletzt eingehaengt: Was hier steht, ist ein
# Versprechen, und das soll man beim Lesen als Letztes sehen, nicht zwischen
# den uebrigen Routern verschwinden.
# ⚠️ **Mit ``NUR_ERWACHSENE``.** Dieselben Handler unter einer zweiten Adresse
# waeren sonst ein Weg am Kinderschutz vorbei - der haengt hier am Einhaengen,
# nicht am Handler.
app.include_router(v1_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(v1_router.public_router)
app.include_router(about_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(details_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(calendar_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(favorites_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(blocklist_router.router)
app.include_router(regeln_router.router)
app.include_router(tickets_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(mediaserver_router.router)
# Wie die Medienserver-Anmeldung ohne NUR_ERWACHSENE: Die Anmeldewege stehen
# vor jeder Sitzung; die Endpunkte mit Sitzung verlangen selbst ``AdultUser``.
app.include_router(oidc_router.router)
app.include_router(oidc_router.admin_router)
app.include_router(storage_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(streaming_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(mediaserver_router.admin_router)
app.include_router(watchlist_router.router, dependencies=NUR_ERWACHSENE)
app.include_router(watch_router.router, dependencies=NUR_ERWACHSENE)
# Ohne Anmeldung, mit Anruf-Geheimnis: die Adresse, die Radarr/Sonarr rufen.
# Warum das sicher ist, steht im Router selbst; die bewusste Ausnahme vom
# Kinderschutz in test_child_permissions.py.
app.include_router(webhooks_router.router)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


def fehler_als_json(exc: StarletteHTTPException) -> JSONResponse:
    """Eine HTTPException als JSON-Antwort - **mit** ihren Kopfzeilen.

    ⚠️ **Die Kopfzeilen sind der ganze Grund, warum es diese Funktion gibt.**
    Der SPA-Rueckfall unten faengt *jede* HTTPException der ganzen Anwendung
    ab und baut die Antwort neu zusammen. Was hier fehlt, fehlt ueberall -
    und es fehlte: ``exc.headers`` wurde stillschweigend weggeworfen. Damit
    verlor jede 401 ihr ``WWW-Authenticate`` (``deps.get_current_user``) und
    die Anmeldebremse ihr ``Retry-After``, mit dem die Oberflaeche sagen
    koennte, wie lange zu warten ist. Auffallen konnte das nicht: Die Antwort
    hatte den richtigen Status und den richtigen Text, ihr fehlte nur die
    Haelfte.

    Bewusst eine eigene Funktion auf Modulebene statt einer Zeile in der
    Schliessung darunter: Der Rueckfall wird nur eingehaengt, wenn ein
    gebautes Frontend danebenliegt. In der CI laufen die Tests **vor** dem
    Frontend-Bau - ein Test ueber die Anwendung haette den Rueckschritt dort
    also gar nicht bemerkt. So laesst er sich direkt pruefen.
    """
    return JSONResponse(
        {"detail": exc.detail},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


# ⚠️ **Nach allen include_router-Aufrufen.** Legt die englischen Texte ueber
# die Routen; vorher gibt es die Routen noch nicht. Warum sie in einer eigenen
# Datei stehen und nicht am Dekorator, steht dort.
api_texte.anwenden(app)


#: Wo Swagger UI liegt - bei uns, nicht bei einem CDN.
#:
#: ⚠️ **Die zwei Dateien liegen im Abbild** (rund 1,7 MB, Fassung 5.32.14).
#: Das ist Absicht und kein Versehen: Nexview laeuft auf Geraeten, die im Keller
#: stehen und nicht zwingend ins Internet duerfen. Eine Doku, die erst laedt,
#: wenn jsdelivr erreichbar ist, waere dort genauso leer wie vorher - nur aus
#: einem anderen Grund. Und ein Loch in die CSP zu schneiden, um fremde Skripte
#: wieder zuzulassen, waere die Regel von gestern rueckgaengig zu machen.
#:
#: Erneuern von Hand: die beiden Dateien aus
#: ``https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/`` neu holen.
SWAGGER_DIR = Path(__file__).resolve().parent / "static" / "swagger"

app.mount("/docs-dateien", StaticFiles(directory=SWAGGER_DIR), name="swagger")


@app.get("/docs", include_in_schema=False)
async def api_dokumentation() -> HTMLResponse:
    """Die anklickbare Beschreibung der Schnittstelle.

    Selbst gebaut statt ``get_swagger_ui_html``: Dessen Seite traegt die
    Startanweisung als eingebettetes ``<script>``, und das verbietet unsere
    CSP. Hier steht sie stattdessen in ``start.js`` daneben.
    """
    # Ein einziger Text mit echten Zeilenumbruechen - keine zusammengesetzten
    # Bruchstuecke. Die Seite ist so kurz, dass jede Zerlegung sie nur
    # schwerer lesbar machen wuerde. Der Unterpfad steht vor jedem Verweis,
    # damit die Seite auch hinter einem Reverse Proxy ihre Dateien findet;
    # ohne NEXVIEW_URL_BASE ist er leer und alles bleibt wie gehabt.
    basis = settings.url_base
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nexview API</title>
<link rel="stylesheet" href="{basis}/docs-dateien/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{basis}/docs-dateien/swagger-ui-bundle.js"></script>
<script src="{basis}/docs-dateien/start.js"></script>
</body>
</html>
"""
    )


@app.get("/redoc", include_in_schema=False)
async def redoc_umleiten() -> RedirectResponse:
    """Alte Lesezeichen auf die eine Doku-Seite fuehren.

    ⚠️ **Ohne diese Zeile landet /redoc in der Weboberflaeche.** Bei
    unbekannten Pfaden liefert Nexview ``index.html`` aus, damit React Router
    seine Adressen selbst verwalten kann - eine abgeschaltete Doku-Seite faellt
    genau in diesen Auffang und zeigt dann die Anwendung mit Status 200. Das
    ist schlimmer als ein Fehler: Wer eine Beschreibung sucht, bekommt eine
    Startseite und haelt sie fuer die Antwort.

    ReDoc selbst gibt es nicht mehr - es holt seine Dateien ebenfalls von einem
    CDN, und ein zweites Megabyte im Abbild fuer eine zweite Ansicht derselben
    Daten waere es nicht wert.
    """
    return RedirectResponse(f"{settings.url_base}/docs", status_code=308)


def _static_dir() -> Path | None:
    """Ordner mit dem gebauten Frontend, falls vorhanden."""
    if settings.static_dir:
        candidate = Path(settings.static_dir)
    else:
        candidate = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    return candidate if (candidate / "index.html").exists() else None


def _index_mit_basis(index_file: Path) -> str | None:
    """``index.html`` mit vorangestelltem Unterpfad - oder ``None`` ohne Basis.

    Die gebaute Seite verweist mit absoluten Pfaden auf sich selbst
    (``/assets/…``, ``/logo.svg``). Unter einem Unterpfad muessen diese
    Verweise den Vorbau tragen, sonst fragt der Browser an der Wurzel der
    Domain - dort, wohin der Proxy gar nicht zeigt. Zusaetzlich bekommt die
    Seite die Basis als Inline-Wert mit; daraus beziehen React Router und der
    API-Aufrufer im Frontend ihren Vorbau.

    Einmal beim Start umgeschrieben, nicht je Anfrage - und **vor** der
    CSP-Berechnung unten, denn deren Pruefsummen muessen das eingefuegte
    Skript einschliessen.
    """
    basis = settings.url_base
    if not basis:
        return None
    html = index_file.read_text(encoding="utf-8")
    html = re.sub(
        r'\b(href|src)="/(?!/)',
        lambda treffer: f'{treffer.group(1)}="{basis}/',
        html,
    )
    marke = f"<script>window.__NEXVIEW_BASIS__ = {json.dumps(basis)};</script>"
    if "<head>" in html:
        return html.replace("<head>", f"<head>\n    {marke}", 1)
    return marke + html


def _mount_frontend(directory: Path, index_inhalt: str | None) -> None:
    """Gebautes Frontend ausliefern.

    React Router verwaltet die Adressen im Browser. Ruft jemand direkt
    ``/login`` auf, kennt der Server diese Datei nicht - deshalb wird bei
    unbekannten Pfaden ``index.html`` zurueckgegeben (SPA-Fallback).
    ``/api/*`` bleibt davon ausgenommen und liefert weiterhin echte 404er.

    ``index_inhalt`` ist die beim Start umgeschriebene Fassung fuer den
    Betrieb unter einem Unterpfad; ohne Unterpfad kommt die Datei unveraendert
    von der Platte.
    """
    app.mount("/assets", StaticFiles(directory=directory / "assets"), name="assets")
    index_file = directory / "index.html"

    def index_antwort():
        if index_inhalt is not None:
            return HTMLResponse(index_inhalt)
        return FileResponse(index_file)

    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(request, exc: StarletteHTTPException):
        if exc.status_code == 404 and not request.url.path.startswith("/api"):
            return index_antwort()
        return fehler_als_json(exc)

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
        return index_antwort()


_frontend_dir = _static_dir()
_index_umgeschrieben: str | None = None
if _frontend_dir is not None:
    _index_umgeschrieben = _index_mit_basis(_frontend_dir / "index.html")
    _mount_frontend(_frontend_dir, _index_umgeschrieben)

# ⚠️ **Ganz zum Schluss, und das ist Absicht.** Die Regeln brauchen die
# Pruefsummen der Inline-Skripte aus der ausgelieferten ``index.html`` - und
# wo die liegt, steht erst hier fest. Zuletzt eingetragen heisst ausserdem
# aussen: Die Kopfzeile haengt dann auch an Antworten, die weiter innen
# entstehen, einschliesslich der Fehlerseiten.
#
# Mit Unterpfad zaehlt die **umgeschriebene** Fassung - sie enthaelt ein
# zusaetzliches Inline-Skript, dessen Pruefsumme sonst fehlen wuerde und die
# Seite beim ersten Laden lautlos brechen liesse.
_inhaltsregeln = csp.kopfzeile(
    settings.csp,
    _index_umgeschrieben
    if _index_umgeschrieben is not None
    else ((_frontend_dir / "index.html") if _frontend_dir is not None else None),
    settings.frame_ancestors,
    settings.img_sources,
)
if _inhaltsregeln is not None:
    app.add_middleware(
        SicherheitskopfMiddleware, name=_inhaltsregeln[0], regeln=_inhaltsregeln[1]
    )

# ⚠️ **Nach den Inhaltsregeln, damit ganz aussen.** Jede Anfrage wird zuerst
# vom Unterpfad befreit, bevor irgendetwas anderes sie sieht - so zaehlen
# Routing, Kinderschutz-Erlaubnisliste und die stillen Pfade des Protokolls
# weiter auf dieselben Wurzel-Adressen wie ohne Unterpfad. Ohne gesetzte
# Basis wird gar nichts eingehaengt.
if settings.url_base:
    app.add_middleware(BasisPfadMiddleware, basis=settings.url_base)
