"""Abbrechen laufender Anfragen und Sammelfreigabe."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus, User
from app.services import library
from app.services.radarr import RadarrClient

from .conftest import auth_headers, create_user


@pytest.fixture
def geloescht_in_radarr(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, bool]]:
    """Merkt sich, was in Radarr gelöscht worden wäre - ohne echten Aufruf."""
    aufrufe: list[tuple[int, bool]] = []

    async def remove(_self: RadarrClient, arr_id: int, delete_files: bool = True) -> None:
        aufrufe.append((arr_id, delete_files))

    monkeypatch.setattr(RadarrClient, "remove", remove)
    return aufrufe


def _laufende_anfrage(client: TestClient, benutzer: str = "kim") -> dict:
    create_user(client, benutzer)
    headers = auth_headers(client, benutzer, "passwort-1234")
    item = client.get("/api/discover/movie").json()["items"][0]
    angelegt = client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    ).json()

    with SessionLocal() as session:
        request = session.query(MediaRequest).filter(MediaRequest.id == angelegt["id"]).one()
        request.status = RequestStatus.searching
        request.arr_id = 4711
        session.commit()

    return {"id": angelegt["id"], "headers": headers}


def test_benutzer_bricht_eigene_anfrage_ab(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    anfrage = _laufende_anfrage(arr_client)

    antwort = arr_client.post(
        f"/api/requests/{anfrage['id']}/cancel", headers=anfrage["headers"]
    )
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "cancelled"

    # In Radarr wurde gelöscht - samt Dateien, wie vereinbart.
    assert geloescht_in_radarr == [(4711, True)]


def test_abbrechen_gibt_das_kontingent_zurueck(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    created = create_user(arr_client, "kim")
    arr_client.patch(f"/api/users/{created['id']}", json={"quota_movies_limit": 1})
    anfrage = _laufende_anfrage(arr_client)

    stand = arr_client.get("/api/requests/quota", headers=anfrage["headers"]).json()
    assert stand["movie"]["used"] == 1

    arr_client.post(f"/api/requests/{anfrage['id']}/cancel", headers=anfrage["headers"])

    danach = arr_client.get("/api/requests/quota", headers=anfrage["headers"]).json()
    assert danach["movie"]["used"] == 0
    assert danach["movie"]["exhausted"] is False


def test_abgebrochener_titel_kann_neu_angefragt_werden(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    anfrage = _laufende_anfrage(arr_client)
    arr_client.post(f"/api/requests/{anfrage['id']}/cancel", headers=anfrage["headers"])

    item = arr_client.get("/api/discover/movie").json()["items"][0]
    assert item["status"] == "not_requested"


def test_fremde_anfrage_kann_man_nicht_abbrechen(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    anfrage = _laufende_anfrage(arr_client, "kim")
    create_user(arr_client, "alex")
    alex = auth_headers(arr_client, "alex", "passwort-1234")

    assert arr_client.post(f"/api/requests/{anfrage['id']}/cancel", headers=alex).status_code == 404
    assert geloescht_in_radarr == []


def test_wartende_anfrage_wird_nicht_abgebrochen_sondern_zurueckgezogen(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    """Was noch auf Freigabe wartet, liegt gar nicht in Radarr."""
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = arr_client.get("/api/discover/movie").json()["items"][0]
    angelegt = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    ).json()

    assert arr_client.post(f"/api/requests/{angelegt['id']}/cancel", headers=headers).status_code == 409
    assert geloescht_in_radarr == []


def test_admin_bricht_fremde_anfrage_ab(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    anfrage = _laufende_anfrage(arr_client)

    antwort = arr_client.post(f"/api/admin/requests/{anfrage['id']}/cancel")
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "cancelled"

    # Der Anfragende wird informiert.
    with SessionLocal() as session:
        kim = session.query(User).filter(User.username == "kim").one()
        assert "cancelled" in [n.type.value for n in kim.notifications]


def test_sammelfreigabe(arr_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drei Anfragen eines Benutzers auf einen Schlag freigeben."""

    async def push(db, settings, request):  # noqa: ANN001
        request.status = RequestStatus.searching
        request.arr_id = 1000 + request.id
        db.commit()
        return request

    from app.services import requests_service

    monkeypatch.setattr(requests_service, "push_to_arr", push)

    created = create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    for index in range(3):
        item = arr_client.get("/api/discover/movie").json()["items"][index]
        arr_client.post(
            "/api/requests",
            json={
                "media_type": "movie",
                "tmdb_id": item["tmdb_id"],
                "quality_profile_id": 1,
                "root_folder_path": "/data/Movies",
            },
            headers=headers,
        )

    assert arr_client.get("/api/admin/requests/pending/count").json() == {"pending": 3}

    antwort = arr_client.post(f"/api/admin/requests/approve-all/{created['id']}")
    assert antwort.status_code == 200
    assert len(antwort.json()) == 3
    assert {eintrag["status"] for eintrag in antwort.json()} == {"searching"}
    assert arr_client.get("/api/admin/requests/pending/count").json() == {"pending": 0}


def test_sammelfreigabe_ohne_offene_anfragen(arr_client: TestClient) -> None:
    created = create_user(arr_client, "kim")
    assert arr_client.post(f"/api/admin/requests/approve-all/{created['id']}").status_code == 404


def test_abbrechen_braucht_rechte(arr_client: TestClient, geloescht_in_radarr: list) -> None:
    anfrage = _laufende_anfrage(arr_client, "kim")
    create_user(arr_client, "alex")
    alex = auth_headers(arr_client, "alex", "passwort-1234")

    assert (
        arr_client.post(f"/api/admin/requests/{anfrage['id']}/cancel", headers=alex).status_code
        == 403
    )
