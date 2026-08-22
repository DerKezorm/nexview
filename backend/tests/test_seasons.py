"""Staffelweise Serien-Anfragen.

Der interessante Fall ist nicht die neue Serie, sondern die laufende: Staffel 4
fehlt, die Staffeln 1 bis 3 liegen längst da. Die Serie dafür neu in Sonarr
anzulegen wäre falsch - es darf nur die eine Staffel dazukommen.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus, User
from app.services import library, requests_service
from app.services.settings_service import load_settings
from app.services.sonarr import LibraryEntry
from tests.conftest import auth_headers, create_user


@pytest.fixture
def nutzer(arr_client: TestClient) -> dict[str, str]:
    """Gewoehnliches Konto - dessen Anfragen bleiben liegen statt sofort an
    Sonarr zu gehen, das im Test nicht erreichbar ist."""
    create_user(arr_client, "lena")
    return auth_headers(arr_client, "lena", "passwort-1234")


def _serie(client: TestClient) -> dict:
    antwort = client.get("/api/discover/tv?page=1")
    assert antwort.status_code == 200, antwort.text
    return antwort.json()["items"][0]


def _anfragen(client: TestClient, serie: dict, season: int | None, **extra):
    nutzlast = {
        "media_type": "tv",
        "tmdb_id": serie["tmdb_id"],
        "quality_profile_id": 1,
    }
    if season is not None:
        nutzlast["season"] = season
    return client.post("/api/requests", json=nutzlast, **extra)


# --- Anlegen -----------------------------------------------------------------


def test_ganze_serie_hat_keine_staffel(arr_client: TestClient, nutzer: dict[str, str]) -> None:
    serie = _serie(arr_client)
    antwort = _anfragen(arr_client, serie, None, headers=nutzer)
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["season"] is None


def test_einzelne_staffel_wird_gespeichert(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    antwort = _anfragen(arr_client, serie, 2, headers=nutzer)
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["season"] == 2


def test_zweite_staffel_ist_erlaubt(arr_client: TestClient, nutzer: dict[str, str]) -> None:
    """Der eigentliche Zweck: Staffel 3 anfragen, obwohl Staffel 2 schon läuft."""
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, 2, headers=nutzer).status_code == 201
    assert _anfragen(arr_client, serie, 3, headers=nutzer).status_code == 201

    with SessionLocal() as db:
        staffeln = sorted(
            r.season for r in db.query(MediaRequest).filter(MediaRequest.tmdb_id == serie["tmdb_id"])
        )
    assert staffeln == [2, 3]


def test_dieselbe_staffel_nicht_zweimal(arr_client: TestClient, nutzer: dict[str, str]) -> None:
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, 2, headers=nutzer).status_code == 201

    zweite = _anfragen(arr_client, serie, 2, headers=nutzer)
    assert zweite.status_code == 409
    assert "Staffel 2" in zweite.json()["detail"]


def test_ganze_serie_deckt_einzelne_staffeln_ab(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Sonst liesse sich dasselbe zweimal bestellen - einmal komplett, einmal
    stueckweise - und beides zaehlte aufs Kontingent."""
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, None, headers=nutzer).status_code == 201

    einzeln = _anfragen(arr_client, serie, 5, headers=nutzer)
    assert einzeln.status_code == 409
    assert "komplett" in einzeln.json()["detail"]


def test_bei_filmen_wird_die_staffel_verworfen(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    film = arr_client.get("/api/discover/movie?page=1").json()["items"][0]
    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": film["tmdb_id"],
            "quality_profile_id": 1,
            "season": 3,
        },
        headers=nutzer,
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["season"] is None


def test_unsinnige_staffel_wird_abgelehnt(arr_client: TestClient, nutzer: dict[str, str]) -> None:
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, -1, headers=nutzer).status_code == 422


# --- Uebergabe an Sonarr -----------------------------------------------------


class SonarrAttrappe:
    """Merkt sich, was Nexview an Sonarr geschickt haette."""

    def __init__(self) -> None:
        self.angelegt: list[dict] = []
        self.aktivierte_staffeln: list[tuple[int, list[int]]] = []
        self.gesucht: list[int | None] = []

    async def ensure_tag(self, _label: str) -> int:
        return 1

    async def add(self, tvdb_id, quality_profile_id, root_folder_path, **kwargs):  # noqa: ANN001
        self.angelegt.append({"tvdb_id": tvdb_id, "season": kwargs.get("season")})
        return {"id": 4242}

    async def monitor_seasons(
        self, arr_id: int, seasons: set[int], such_staffel: int | None = None
    ) -> None:
        self.aktivierte_staffeln.append((arr_id, sorted(seasons)))
        self.gesucht.append(such_staffel)


async def _uebergeben(monkeypatch, serie_in_sonarr: LibraryEntry | None, season: int | None):
    """Eine Anfrage bis zur Uebergabe an Sonarr durchspielen."""
    attrappe = SonarrAttrappe()
    monkeypatch.setattr(library, "sonarr_client", lambda _settings, _tier="standard": attrappe)

    async def bibliothek(_settings, _tier: str = "standard"):  # noqa: ANN001
        if serie_in_sonarr is None:
            return {}, {}
        return {555: serie_in_sonarr}, {}

    monkeypatch.setattr(library, "series_library", bibliothek)

    with SessionLocal() as db:
        benutzer = db.query(User).filter(User.username == "admin").one()
        anfrage = MediaRequest(
            user_id=benutzer.id,
            media_type=MediaType.tv,
            tmdb_id=99,
            tvdb_id=555,
            title="Testserie",
            season=season,
            status=RequestStatus.approved,
            quality_profile_id=1,
            root_folder_path="/data/TV-Shows",
        )
        db.add(anfrage)
        db.commit()
        await requests_service.push_to_arr(db, load_settings(db), anfrage)

    return attrappe


async def test_neue_serie_wird_mit_staffel_angelegt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    attrappe = await _uebergeben(monkeypatch, serie_in_sonarr=None, season=2)
    assert attrappe.angelegt == [{"tvdb_id": 555, "season": 2}]
    assert attrappe.aktivierte_staffeln == []


async def test_vorhandene_serie_bekommt_nur_die_staffel(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der wichtigste Fall: die Serie laeuft schon, es fehlt nur eine Staffel.

    Sie neu anzulegen wuerde die vorhandenen Folgen in Sonarr durcheinander
    bringen - stattdessen wird nur die Staffel aktiviert.
    """
    vorhanden = LibraryEntry(
        arr_id=77,
        has_file=True,
        monitored=True,
        episode_file_count=20,
        episode_count=30,
        title_key="testserie",
    )
    attrappe = await _uebergeben(monkeypatch, serie_in_sonarr=vorhanden, season=4)

    assert attrappe.angelegt == []
    assert attrappe.aktivierte_staffeln == [(77, [4])]
    assert attrappe.gesucht == [4]


async def test_ganze_serie_wird_normal_angelegt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Staffelangabe bleibt alles wie bisher."""
    attrappe = await _uebergeben(monkeypatch, serie_in_sonarr=None, season=None)
    assert attrappe.angelegt == [{"tvdb_id": 555, "season": None}]
