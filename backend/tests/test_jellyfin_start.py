"""Ein Jellyfin, das gerade startet, ist trotzdem ein Jellyfin.

Gemessen am 12.10.2026 an Jellyfin 12.2.0, alle halbe Sekunde nach dem Start:

1. Ein Startdienst beantwortet nur ``/System/Info/Public``, und zwar in
   camelCase (``id``, ``serverName``). Jede andere Adresse gibt 503.
2. Einige Sekunden lang "Jellyfin Server is loading. Please try again
   shortly." als HTML, ebenfalls 503.
3. Danach der normale Betrieb, wieder in PascalCase.

Nexview las nur ``Id`` und hielt den Server im ersten Fenster fuer "kein
Jellyfin"; eine Anmeldung im zweiten meldete nur "HTTP 503". Die Antworten
unten sind die gemessenen, gekuerzt.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services.mediaserver import jellyfin
from app.services.mediaserver.base import MediaServerError
from app.services.mediaserver.emby import EmbyServer
from app.services.mediaserver.jellyfin import JellyfinServer

URL = "http://jelly.test"

EINSTELLUNGEN = SimpleNamespace(
    mediaserver_url=URL,
    mediaserver_token="",
    mediaserver_machine_id="",
    mediaserver_client_identifier="nexview-test",
    mediaserver_account_id="",
)

STARTDIENST = {
    "localAddress": "http://jellyfin.example.com:8096",
    "serverName": "wohnzimmer",
    "version": "12.2.0",
    "productName": "Jellyfin Server",
    "operatingSystem": "",
    "id": "kennung-des-servers",
    "startupWizardCompleted": True,
}
BETRIEB = {
    "LocalAddress": "http://jellyfin.example.com:8096",
    "ServerName": "wohnzimmer",
    "Version": "12.2.0",
    "ProductName": "Jellyfin Server",
    "OperatingSystem": "",
    "Id": "kennung-des-servers",
    "StartupWizardCompleted": True,
}
LAEDT = "Jellyfin Server is loading. Please try again shortly."


def _server(monkeypatch, antwort, klasse=JellyfinServer):
    """Ein Adapter, dessen HTTP-Verbindung immer ``antwort(request)`` liefert."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(antwort))

    async def _client():
        return client

    monkeypatch.setattr(jellyfin, "http_client", _client)
    return klasse(EINSTELLUNGEN)


def _startdienst(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/System/Info/Public":
        return httpx.Response(200, json=STARTDIENST)
    return httpx.Response(503)


def _laedt(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, text=LAEDT, headers={"Content-Type": "text/html"})


@pytest.mark.anyio
async def test_der_startdienst_wird_als_jellyfin_erkannt(monkeypatch):
    server = _server(monkeypatch, _startdienst)
    assert await server.probe(URL, "") is True


@pytest.mark.anyio
async def test_kennung_und_name_kommen_auch_aus_camelcase(monkeypatch):
    """Die Kennung entscheidet, welche Verbindungszeile es wird."""
    server = _server(monkeypatch, _startdienst)
    (kandidat,) = await server.list_servers("")
    assert kandidat.machine_id == STARTDIENST["id"]
    assert kandidat.name == "wohnzimmer"


@pytest.mark.anyio
async def test_der_normale_betrieb_liest_sich_wie_vorher(monkeypatch):
    server = _server(monkeypatch, lambda r: httpx.Response(200, json=BETRIEB))
    assert await server.probe(URL, "") is True
    (kandidat,) = await server.list_servers("")
    assert (kandidat.machine_id, kandidat.name) == (BETRIEB["Id"], "wohnzimmer")


@pytest.mark.anyio
async def test_eine_fremde_antwort_bleibt_kein_jellyfin(monkeypatch):
    """Gross und klein egal heisst nicht: jedes JSON ist ein Jellyfin."""
    server = _server(monkeypatch, lambda r: httpx.Response(200, json={"status": "ok"}))
    assert await server.probe(URL, "") is False
    with pytest.raises(MediaServerError):
        await server.list_servers("")


@pytest.mark.anyio
@pytest.mark.parametrize("antwort", [_startdienst, _laedt], ids=["startdienst", "laedt"])
async def test_anmelden_waehrend_des_starts_sagt_dass_er_startet(monkeypatch, antwort):
    server = _server(monkeypatch, antwort)
    with pytest.raises(MediaServerError) as fehler:
        await server.login_with_password("admin", "geheim", URL, zweck="server")
    assert fehler.value.status_code == 503
    assert fehler.value.als_meldung()["code"] == "mediaserver_starting"
    assert fehler.value.als_meldung()["service"] == "Jellyfin"


@pytest.mark.anyio
async def test_emby_traegt_seinen_eigenen_namen(monkeypatch):
    server = _server(monkeypatch, _laedt, EmbyServer)
    with pytest.raises(MediaServerError) as fehler:
        await server.verify()
    assert fehler.value.als_meldung()["service"] == "Emby"


@pytest.mark.anyio
async def test_ein_503_ist_kein_abgelehnter_zugang(monkeypatch):
    """Sonst hiesse es "neu anmelden", obwohl nur der Server noch hochfaehrt."""
    server = _server(monkeypatch, _laedt)
    with pytest.raises(MediaServerError) as fehler:
        await server.user_has_server_access("token")
    assert fehler.value.status_code != 401
