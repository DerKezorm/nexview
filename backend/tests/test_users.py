"""Benutzerverwaltung durch den Administrator."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import auth_headers, create_user


def test_admin_setzt_passwort_zurueck(admin_client: TestClient) -> None:
    created = create_user(admin_client, "alex")

    assert (
        admin_client.post(
            f"/api/users/{created['id']}/password", json={"password": "ganz-neues-pw"}
        ).status_code
        == 204
    )
    assert auth_headers(admin_client, "alex", "ganz-neues-pw")


def test_letzter_admin_kann_sich_nicht_herabstufen(admin_client: TestClient) -> None:
    me = admin_client.get("/api/auth/me").json()
    response = admin_client.patch(f"/api/users/{me['id']}", json={"role": "user"})
    assert response.status_code == 400


def test_letzter_admin_kann_nicht_geloescht_werden(admin_client: TestClient) -> None:
    me = admin_client.get("/api/auth/me").json()
    assert admin_client.delete(f"/api/users/{me['id']}").status_code == 400


def test_freigabe_und_kontingent_aendern(admin_client: TestClient) -> None:
    created = create_user(admin_client, "alex")

    response = admin_client.patch(
        f"/api/users/{created['id']}",
        json={"auto_approve": True, "quota_movies_limit": None, "quota_period": "day"},
    )
    assert response.status_code == 200
    assert response.json()["auto_approve"] is True
    assert response.json()["quota_movies_limit"] is None
    assert response.json()["quota_period"] == "day"


def test_benutzer_loeschen(admin_client: TestClient) -> None:
    created = create_user(admin_client, "alex")
    assert admin_client.delete(f"/api/users/{created['id']}").status_code == 204
    assert len(admin_client.get("/api/users").json()) == 1
