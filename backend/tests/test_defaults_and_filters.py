"""Standard-Qualitätsprofil, aufgeräumte Benachrichtigungen und der Filter
für offene Rückmeldungen."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, Notification, RequestStatus, User
from app.services import library, requests_service

from .conftest import auth_headers, create_user

# Profile, wie Radarr/Sonarr sie liefern wuerden.
PROFILE = [
    {"id": 4, "name": "HD-1080p"},
    {"id": 6, "name": "Ultra-HD"},
    {"id": 7, "name": "Any"},
]


@pytest.fixture
def arr_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Qualitaetsprofile und Zielordner ohne echtes Radarr/Sonarr."""

    async def optionen(_settings: object, _media_type: str) -> dict:
        return {
            "quality_profiles": PROFILE,
            "root_folders": [{"path": "/data/Movies", "free_space": None}],
        }

    monkeypatch.setattr(library, "options", optionen)


def _anfrage(client: TestClient, headers: dict, index: int = 0) -> dict:
    item = client.get("/api/discover/movie").json()["items"][index]
    return client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    ).json()


# --- Standard-Qualitätsprofil ------------------------------------------------


def test_ohne_standard_wird_das_erste_profil_vorgeschlagen(arr_client: TestClient, arr_options: None) -> None:
    optionen = arr_client.get("/api/arr/movie/options").json()
    assert optionen["default_quality_profile_id"] == optionen["quality_profiles"][0]["id"]


def test_admin_setzt_den_standard(arr_client: TestClient, arr_options: None) -> None:
    profile = arr_client.get("/api/arr/movie/options").json()["quality_profiles"]
    zweites = profile[1]["id"]

    arr_client.put("/api/settings", json={"default_movie_profile_id": str(zweites)})

    optionen = arr_client.get("/api/arr/movie/options").json()
    assert optionen["default_quality_profile_id"] == zweites


def test_gesperrter_standard_weicht_auf_das_erste_erlaubte_aus(arr_client: TestClient, arr_options: None) -> None:
    profile = arr_client.get("/api/arr/movie/options").json()["quality_profiles"]
    standard, anderes = profile[0]["id"], profile[1]["id"]
    arr_client.put("/api/settings", json={"default_movie_profile_id": str(standard)})

    created = create_user(arr_client, "kim")
    arr_client.patch(f"/api/users/{created['id']}", json={"blocked_movie_profiles": [standard]})
    kim = auth_headers(arr_client, "kim", "passwort-1234")

    optionen = arr_client.get("/api/arr/movie/options", headers=kim).json()
    assert standard not in [p["id"] for p in optionen["quality_profiles"]]
    assert optionen["default_quality_profile_id"] == anderes


def test_standard_bleibt_gespeichert(arr_client: TestClient) -> None:
    arr_client.put("/api/settings", json={"default_series_profile_id": "7"})
    assert arr_client.get("/api/settings").json()["default_series_profile_id"] == 7

    # Leerer Wert hebt die Vorauswahl wieder auf.
    arr_client.put("/api/settings", json={"default_series_profile_id": ""})
    assert arr_client.get("/api/settings").json()["default_series_profile_id"] is None


# --- Benachrichtigung "wartet auf Freigabe" ---------------------------------


def test_freigabe_raeumt_die_meldung_weg(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def push(db, settings, request):  # noqa: ANN001
        request.status = RequestStatus.searching
        db.commit()
        return request

    monkeypatch.setattr(requests_service, "push_to_arr", push)

    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    angelegt = _anfrage(arr_client, kim)

    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == "admin").one()
        assert "request_pending" in [n.type.value for n in admin.notifications]

    arr_client.post(f"/api/admin/requests/{angelegt['id']}/approve")

    with SessionLocal() as session:
        offen = session.query(Notification).filter(
            Notification.type == "request_pending"
        ).count()
        assert offen == 0


def test_ablehnen_raeumt_die_meldung_ebenfalls_weg(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    angelegt = _anfrage(arr_client, kim)

    arr_client.post(f"/api/admin/requests/{angelegt['id']}/reject", json={})

    with SessionLocal() as session:
        assert (
            session.query(Notification).filter(Notification.type == "request_pending").count() == 0
        )
        kim_db = session.query(User).filter(User.username == "kim").one()
        assert "rejected" in [n.type.value for n in kim_db.notifications]


# --- Filter "offene Rückmeldungen" ------------------------------------------


def _bewertete_anfrage(client: TestClient, kommentar: str | None, index: int = 0) -> int:
    kim = auth_headers(client, "kim", "passwort-1234")
    angelegt = _anfrage(client, kim, index)
    with SessionLocal() as session:
        request = session.get(MediaRequest, angelegt["id"])
        assert request is not None
        request.status = RequestStatus.downloaded
        session.commit()
    client.post(
        f"/api/requests/{angelegt['id']}/feedback",
        json={"rating": 2, "comment": kommentar},
        headers=kim,
    )
    return angelegt["id"]


def test_filter_zeigt_auch_sterne_ohne_text(arr_client: TestClient) -> None:
    """Wer Sterne vergibt, sagt damit etwas - auch ohne Kommentar."""
    create_user(arr_client, "kim")
    mit_text = _bewertete_anfrage(arr_client, "Ton ist kaputt.", 0)
    ohne_text = _bewertete_anfrage(arr_client, None, 1)

    offen = arr_client.get("/api/admin/requests?feedback=true").json()
    assert {e["id"] for e in offen} == {mit_text, ohne_text}


def test_filter_zeigt_nur_unbewertete_nicht(arr_client: TestClient) -> None:
    """Anfragen ohne jede Bewertung gehören nicht in diese Ansicht."""
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _anfrage(arr_client, kim, 2)  # angefragt, aber nie bewertet

    offen = arr_client.get("/api/admin/requests?feedback=true").json()
    assert offen == []


def test_beantwortete_verschwinden_aus_dem_filter(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kennung = _bewertete_anfrage(arr_client, "Bitte nochmal laden.")
    assert len(arr_client.get("/api/admin/requests?feedback=true").json()) == 1

    arr_client.post(f"/api/admin/requests/{kennung}/reply", json={"reply": "Erledigt."})
    assert arr_client.get("/api/admin/requests?feedback=true").json() == []


def test_entscheider_sieht_den_filter_auch(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    _bewertete_anfrage(arr_client, "Bild ruckelt.")

    created = create_user(arr_client, "eva")
    arr_client.patch(f"/api/users/{created['id']}", json={"role": "approver"})
    eva = auth_headers(arr_client, "eva", "passwort-1234")

    antwort = arr_client.get("/api/admin/requests?feedback=true", headers=eva)
    assert antwort.status_code == 200
    assert len(antwort.json()) == 1
