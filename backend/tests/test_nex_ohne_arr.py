"""Der Waechter des Umschaltens: Im NEX-Betrieb bekommt Arr **keine** Lesefrage.

Vorbild ist nexbeat (dort bekommt Lidarr im NEX-Betrieb nicht einmal eine
Lesefrage). Der Grund ist kein Schoenheitsfehler: Wer umschaltet, hat seine
Arr-Instanzen womoeglich abgeschaltet. Jede vergessene Abzweigung waere dann
eine Seite, die zwanzig Sekunden auf eine Zeitueberschreitung wartet - und
ein Titel, der als "nicht vorhanden" gilt, weil niemand geantwortet hat.

⚠️ **Der Gegenbeweis gehoert dazu.** Ein Test, der nur zaehlt, ob Radarr
gefragt wurde, ist gruen, solange die aufgerufenen Adressen Radarr ohnehin
nie fragen. ``test_im_arr_betrieb_wird_arr_sehr_wohl_gefragt`` laeuft
deshalb dieselben Adressen im ARR-Betrieb und besteht darauf, dass dort
gefragt wird.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.services.beschaffung.arr import client as arr_client
from app.services.settings_service import load_settings, save_settings

#: Adressen, die im ARR-Betrieb bei Radarr oder Sonarr nachfragen. Sie
#: brauchen kein TMDB und keinen Medienserver.
ADRESSEN = (
    "/api/config",
    "/api/settings",
    "/api/storage/me",
    "/api/storage/overview",
    "/api/downloads",
    "/api/downloads/automatik",
    "/api/admin/analyse",
    "/api/admin/requests",
    "/api/requests/mine",
    "/api/calendar?von=2026-09-01&bis=2026-09-30",
)

ARR_ZUGANG = {
    "radarr_url": "http://radarr.example.com",
    "radarr_api_key": "test-radarr-key",
    "sonarr_url": "http://sonarr.example.com",
    "sonarr_api_key": "test-sonarr-key",
}


class ArrLauscher:
    """Ein Transport, der jeden Aufruf an Arr mitschreibt und ins Leere gehen laesst."""

    def __init__(self) -> None:
        self.aufrufe: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def antwort(request: httpx.Request) -> httpx.Response:
            self.aufrufe.append(f"{request.method} {request.url}")
            return httpx.Response(200, json=[])

        return httpx.MockTransport(antwort)


@pytest.fixture
def arr_lauscher(monkeypatch: pytest.MonkeyPatch) -> Iterator[ArrLauscher]:
    lauscher = ArrLauscher()
    verbindung = httpx.AsyncClient(transport=lauscher.transport())

    async def statt_http() -> httpx.AsyncClient:
        return verbindung

    monkeypatch.setattr(arr_client, "_http", statt_http)
    yield lauscher


def _abfragen(admin_client: TestClient) -> None:
    for adresse in ADRESSEN:
        # Der Rueckgabewert ist hier gleichgueltig: Geprueft wird, wer gefragt
        # wurde, nicht was herauskam.
        admin_client.get(adresse)


def test_im_nex_betrieb_bekommt_arr_keine_lesefrage(
    admin_client: TestClient, arr_lauscher: ArrLauscher
) -> None:
    """Auch dann nicht, wenn die Arr-Zugaenge noch in der Datenbank stehen."""
    with SessionLocal() as db:
        save_settings(db, {**ARR_ZUGANG, "beschaffung": "nex"})
        assert load_settings(db, frisch=True).beschaffung_ist_nex is True

    _abfragen(admin_client)

    assert arr_lauscher.aufrufe == []


def test_im_arr_betrieb_wird_arr_sehr_wohl_gefragt(
    admin_client: TestClient, arr_lauscher: ArrLauscher
) -> None:
    """Der Gegenbeweis - sonst zaehlte der Test oben nur leere Seiten."""
    with SessionLocal() as db:
        save_settings(db, {**ARR_ZUGANG, "beschaffung": "arr"})

    _abfragen(admin_client)

    assert arr_lauscher.aufrufe, "Keine dieser Adressen fragt Arr - der Waechter misst nichts."


def test_die_betriebsart_entscheidet_auch_ohne_einstellungen_in_der_hand(
    admin_client: TestClient,
) -> None:
    """Der Weckruf und die Tabellen je Instanz fragen ohne Sitzung - und richtig."""
    from app.services import beschaffung

    with SessionLocal() as db:
        save_settings(db, {**ARR_ZUGANG, "beschaffung": "nex"})
        load_settings(db, frisch=True)
    assert beschaffung.betriebsart() == "nex"
    # Im NEX-Betrieb fuehrt nexcrate die Gesundheit und die Haenger selbst
    # (Scheibe 5); es sind jedenfalls nicht mehr die Arr-Instanzen.
    with SessionLocal() as db:
        assert beschaffung.gesundheit_je_instanz(db) == {}
        assert beschaffung.haenger_je_instanz(db) == {}

    with SessionLocal() as db:
        save_settings(db, {"beschaffung": "arr"})
        load_settings(db, frisch=True)
    assert beschaffung.betriebsart() == "arr"


# --- Der Riegel vor den Betreiberwerkzeugen (Bauplan 3.2) --------------------

#: Werkzeuge, die es im NEX-Betrieb nicht gibt: Sie gehoeren dort nexcrate.
WERKZEUGE = (
    ("POST", "/api/settings/test/radarr"),
    ("GET", "/api/settings/qualitaetsprofile"),
    ("GET", "/api/arr/movie/options"),
)


@pytest.mark.parametrize(("methode", "adresse"), WERKZEUGE)
def test_die_arr_werkzeuge_antworten_im_nex_betrieb_mit_409(
    admin_client: TestClient, methode: str, adresse: str
) -> None:
    with SessionLocal() as db:
        save_settings(db, {**ARR_ZUGANG, "beschaffung": "nex"})

    antwort = admin_client.request(methode, adresse, json={})

    assert antwort.status_code == 409, f"{adresse}: {antwort.status_code} {antwort.text[:120]}"
    assert antwort.json()["detail"]["code"] == "not_in_this_mode"


@pytest.mark.parametrize(("methode", "adresse"), WERKZEUGE)
def test_dieselben_werkzeuge_gibt_es_im_arr_betrieb_weiter(
    admin_client: TestClient, methode: str, adresse: str
) -> None:
    """Der Gegenbeweis: Der Riegel haelt nur die eine Betriebsart auf."""
    with SessionLocal() as db:
        save_settings(db, {**ARR_ZUGANG, "beschaffung": "arr"})

    antwort = admin_client.request(methode, adresse, json={})

    assert antwort.status_code != 409, antwort.text
