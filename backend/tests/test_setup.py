"""Die Erst-Einrichtung darf genau einmal funktionieren."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import ADMIN


def test_status_meldet_einrichtung_noetig(client: TestClient) -> None:
    status = client.get("/api/setup/status").json()
    assert status["needs_setup"] is True
    # Die Mindestlänge kommt vom Server - der Assistent läuft vor der Anmeldung.
    assert status["min_password_length"] >= 1


def test_erster_admin_wird_angelegt_und_ist_admin(client: TestClient) -> None:
    response = client.post("/api/setup/admin", json=ADMIN)
    assert response.status_code == 201
    token = response.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "admin"
    # Der Admin soll seine eigenen Anfragen nicht selbst freigeben muessen.
    assert me.json()["auto_approve"] is True

    assert client.get("/api/setup/status").json()["needs_setup"] is False


def test_zweiter_setup_aufruf_wird_abgewiesen(client: TestClient) -> None:
    client.post("/api/setup/admin", json=ADMIN)
    response = client.post(
        "/api/setup/admin", json={"username": "eindringling", "password": "passwort-1234", "email": "neu@beispiel.de"}
    )
    assert response.status_code == 409


def test_zu_kurzes_passwort_wird_abgelehnt(client: TestClient) -> None:
    response = client.post("/api/setup/admin", json={"username": "admin", "password": "ab", "email": "neu@beispiel.de"})
    assert response.status_code == 422


def test_kurzes_aber_erlaubtes_passwort(client: TestClient) -> None:
    # "admin"/"user" sollen als Zugangsdaten moeglich sein.
    assert client.post("/api/setup/admin", json={"username": "admin", "password": "admin", "email": "neu@beispiel.de"}).status_code == 201


def test_ungueltiger_benutzername_wird_abgelehnt(client: TestClient) -> None:
    response = client.post(
        "/api/setup/admin", json={"username": "mit leerzeichen", "password": "passwort-1234", "email": "neu@beispiel.de"}
    )
    assert response.status_code == 422
