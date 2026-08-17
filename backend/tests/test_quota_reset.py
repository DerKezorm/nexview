"""Der Admin kann das Kontingent eines Benutzers zurücksetzen."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, User, utcnow
from app.services import quota

from .conftest import auth_headers, create_user


def _benutzer_mit_grenze(client: TestClient, grenze: int = 1) -> tuple[int, dict]:
    created = create_user(client, "kim")
    client.patch(
        f"/api/users/{created['id']}",
        json={"quota_movies_limit": grenze, "quota_series_limit": grenze},
    )
    return created["id"], auth_headers(client, "kim", "passwort-1234")


def _anfrage(client: TestClient, headers: dict, index: int = 0):
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
    )


def test_ruecksetzen_gibt_das_kontingent_frei(arr_client: TestClient) -> None:
    kennung, kim = _benutzer_mit_grenze(arr_client)
    _anfrage(arr_client, kim, 0)

    stand = arr_client.get("/api/requests/quota", headers=kim).json()
    assert stand["movie"]["used"] == 1
    assert stand["movie"]["exhausted"] is True
    # Die zweite Anfrage prallt am Kontingent ab.
    assert _anfrage(arr_client, kim, 1).status_code == 429

    antwort = arr_client.post(f"/api/users/{kennung}/quota/reset")
    assert antwort.status_code == 200
    assert antwort.json()["quota_movies_used"] == 0

    danach = arr_client.get("/api/requests/quota", headers=kim).json()
    assert danach["movie"]["used"] == 0
    assert danach["movie"]["exhausted"] is False
    assert _anfrage(arr_client, kim, 1).status_code == 201


def test_anfragen_bleiben_erhalten(arr_client: TestClient) -> None:
    """Zurücksetzen heisst neu zählen - nicht löschen."""
    kennung, kim = _benutzer_mit_grenze(arr_client)
    _anfrage(arr_client, kim)

    arr_client.post(f"/api/users/{kennung}/quota/reset")

    assert len(arr_client.get("/api/requests/mine", headers=kim).json()) == 1
    with SessionLocal() as session:
        assert session.query(MediaRequest).count() == 1


def test_ruecksetzung_wird_vermerkt(arr_client: TestClient) -> None:
    kennung, _ = _benutzer_mit_grenze(arr_client)
    assert arr_client.get("/api/users").json()[1]["quota_reset_at"] is None

    arr_client.post(f"/api/users/{kennung}/quota/reset")

    eintrag = next(u for u in arr_client.get("/api/users").json() if u["id"] == kennung)
    assert eintrag["quota_reset_at"] is not None


def test_alte_ruecksetzung_wirkt_nicht_mehr(arr_client: TestClient) -> None:
    """Nach einem Zeitraumwechsel zählt wieder der Kalender - eine
    Rücksetzung aus der Vorwoche darf nichts mehr bewirken."""
    kennung, kim = _benutzer_mit_grenze(arr_client)
    _anfrage(arr_client, kim)

    with SessionLocal() as session:
        kim_db = session.get(User, kennung)
        assert kim_db is not None
        # Rücksetzung auf "vor 30 Tagen" datieren, also vor den Zeitraumbeginn.
        kim_db.quota_reset_at = utcnow().replace(tzinfo=None) - timedelta(days=30)
        session.commit()
        assert quota.counting_start(kim_db) == quota.period_start(kim_db.quota_period)

    stand = arr_client.get("/api/requests/quota", headers=kim).json()
    assert stand["movie"]["used"] == 1


def test_verbrauch_steht_in_der_benutzerliste(arr_client: TestClient) -> None:
    kennung, kim = _benutzer_mit_grenze(arr_client, grenze=3)
    _anfrage(arr_client, kim, 0)
    _anfrage(arr_client, kim, 1)

    eintrag = next(u for u in arr_client.get("/api/users").json() if u["id"] == kennung)
    assert eintrag["quota_movies_used"] == 2
    assert eintrag["quota_series_used"] == 0


def test_naechster_wechsel_bleibt_am_kalender(arr_client: TestClient) -> None:
    """Eine Rücksetzung verschiebt den automatischen Wechsel nicht."""
    kennung, kim = _benutzer_mit_grenze(arr_client)
    vorher = arr_client.get("/api/requests/quota", headers=kim).json()["movie"]["resets_at"]

    arr_client.post(f"/api/users/{kennung}/quota/reset")

    nachher = arr_client.get("/api/requests/quota", headers=kim).json()["movie"]["resets_at"]
    assert nachher == vorher


def test_nur_admins_duerfen_zuruecksetzen(arr_client: TestClient) -> None:
    kennung, kim = _benutzer_mit_grenze(arr_client)
    assert arr_client.post(f"/api/users/{kennung}/quota/reset", headers=kim).status_code == 403

    created = create_user(arr_client, "eva")
    arr_client.patch(f"/api/users/{created['id']}", json={"role": "approver"})
    eva = auth_headers(arr_client, "eva", "passwort-1234")
    assert arr_client.post(f"/api/users/{kennung}/quota/reset", headers=eva).status_code == 403


def test_unbekannter_benutzer(arr_client: TestClient) -> None:
    assert arr_client.post("/api/users/9999/quota/reset").status_code == 404
