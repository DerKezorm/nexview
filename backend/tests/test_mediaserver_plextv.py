"""Was Nexview tatsaechlich mit plex.tv spricht.

Hier wird - anders als in ``test_mediaserver_login.py`` - unter der
Abstraktion gemockt: auf HTTP-Ebene. Nur so laesst sich pruefen, ob die
richtigen Adressen, Kopfzeilen und Felder verwendet werden. Der wichtigste
Test steht ganz unten: ein fremdes Konto darf keinen Zugriff bekommen.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from app.services.mediaserver import MediaServerError
from app.services.mediaserver import base, plextv

KENNUNG = "test-client-id"

KONTO_ANTWORT = {
    "id": 4711,
    "username": "Testkonto",
    "email": "gast@beispiel.de",
    "thumb": "https://plex.tv/bild.png",
}

RESSOURCEN = [
    {
        "clientIdentifier": "maschine-1",
        "name": "Wohnzimmer",
        "provides": "server",
        "owned": True,
        "connections": [
            {"uri": "https://fern.plex.direct:32400", "local": False},
            {"uri": "http://10.0.0.5:32400", "local": True},
        ],
    },
    {
        # Ein Player, kein Server - darf nicht in der Auswahl auftauchen.
        "clientIdentifier": "handy-1",
        "name": "Handy",
        "provides": "player",
        "connections": [],
    },
]


@pytest.fixture
def aufrufe(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """plex.tv durch eine feste Antwortliste ersetzen und mitschreiben."""
    gesehen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request)
        pfad = request.url.path
        if pfad == "/api/v2/pins":
            return httpx.Response(201, json={"id": 99, "code": "ABCD"})
        if pfad.startswith("/api/v2/pins/"):
            return httpx.Response(200, json={"id": 99, "authToken": _pin_antwort})
        if pfad == "/api/v2/user":
            return httpx.Response(200, json=KONTO_ANTWORT)
        if pfad == "/api/v2/resources":
            return httpx.Response(200, json=RESSOURCEN)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(base, "_client", client)
    yield gesehen


# Wird von einzelnen Tests umgestellt, um "noch nicht bestaetigt" zu proben.
_pin_antwort: str | None = "anbieter-token"


@pytest.fixture(autouse=True)
def _pin_zuruecksetzen() -> Iterator[None]:
    global _pin_antwort
    _pin_antwort = "anbieter-token"
    yield
    _pin_antwort = "anbieter-token"


async def test_anmeldung_starten(aufrufe: list[httpx.Request]) -> None:
    challenge = await plextv.begin_login(KENNUNG)

    assert challenge.ref == "99"
    assert challenge.code == "ABCD"
    # Der Browser braucht Code und Geraetekennung in der Adresse.
    assert "code=ABCD" in challenge.auth_url
    assert f"clientID={KENNUNG}" in challenge.auth_url

    anfrage = aufrufe[0]
    assert anfrage.method == "POST"
    assert anfrage.url.params["strong"] == "true"
    # Ohne diese Kopfzeile fuehrt Plex das Geraet nicht - und lehnt spaeter ab.
    assert anfrage.headers["X-Plex-Client-Identifier"] == KENNUNG
    assert anfrage.headers["X-Plex-Product"] == "Nexview"


async def test_noch_nicht_bestaetigt(aufrufe: list[httpx.Request]) -> None:
    """Solange niemand zugestimmt hat, liefert Plex ``authToken: null``."""
    global _pin_antwort
    _pin_antwort = None

    assert await plextv.poll_login(KENNUNG, "99", "ABCD") is None


async def test_bestaetigt_liefert_token(aufrufe: list[httpx.Request]) -> None:
    token = await plextv.poll_login(KENNUNG, "99", "ABCD")

    assert token == "anbieter-token"
    # Plex erwartet den Code bei jeder Nachfrage mit.
    assert aufrufe[0].url.params["code"] == "ABCD"


async def test_konto_wird_uebersetzt(aufrufe: list[httpx.Request]) -> None:
    konto = await plextv.account_for_token(KENNUNG, "anbieter-token")

    assert konto.provider == "plex"
    assert konto.account_id == "4711"  # immer als Text, nie als Zahl
    assert konto.username == "Testkonto"
    assert konto.email == "gast@beispiel.de"
    assert aufrufe[0].headers["X-Plex-Token"] == "anbieter-token"


async def test_konto_ohne_adresse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verwaltete Profile haben oft keine E-Mail-Adresse - das ist kein Fehler."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 12, "username": "Kind", "email": ""})

    monkeypatch.setattr(
        base, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    konto = await plextv.account_for_token(KENNUNG, "anbieter-token")

    assert konto.email is None
    assert konto.account_id == "12"


async def test_serverliste_filtert_und_bevorzugt_lokal(aufrufe: list[httpx.Request]) -> None:
    server = await plextv.list_servers(KENNUNG, "anbieter-token")

    assert len(server) == 1  # der Player faellt heraus
    assert server[0].machine_id == "maschine-1"
    assert server[0].name == "Wohnzimmer"
    assert server[0].owned is True
    # Die lokale Adresse ist vorzuziehen - der Umweg ueber plex.direct kostet nur Zeit.
    assert server[0].url == "http://10.0.0.5:32400"
    # Beide bleiben erhalten: Ob die lokale taugt, zeigt sich erst beim Verbinden.
    assert server[0].urls == ("http://10.0.0.5:32400", "https://fern.plex.direct:32400")


async def test_zugriff_wird_ueber_die_maschinenkennung_geprueft(
    aufrufe: list[httpx.Request],
) -> None:
    """Der sicherheitskritische Test.

    Ein Konto darf nur herein, wenn *dieser* Server in seinen Ressourcen steht.
    Ein fremdes Plex-Konto hat die Kennung dort nicht - und wird abgelehnt.
    """
    assert await plextv.has_server_access(KENNUNG, "anbieter-token", "maschine-1") is True
    assert await plextv.has_server_access(KENNUNG, "anbieter-token", "fremde-maschine") is False
    # Ohne eingerichteten Server darf niemals jemand durchkommen.
    assert await plextv.has_server_access(KENNUNG, "anbieter-token", "") is False


async def test_abgelehntes_token_wird_verstaendlich(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    monkeypatch.setattr(
        base, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MediaServerError) as fehler:
        await plextv.account_for_token(KENNUNG, "falsch")

    assert fehler.value.status_code == 401
    assert "nicht akzeptiert" in fehler.value.message


async def test_plex_nicht_erreichbar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Faellt plex.tv aus, muss die Meldung sagen, woran es liegt."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kein Netz")

    monkeypatch.setattr(
        base, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MediaServerError) as fehler:
        await plextv.begin_login(KENNUNG)

    assert "nicht erreichbar" in fehler.value.message
