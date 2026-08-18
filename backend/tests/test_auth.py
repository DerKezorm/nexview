"""Anmeldung, Token und Zugriffsschutz."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import ADMIN, auth_headers, create_user


def test_login_mit_richtigem_passwort(admin_client: TestClient) -> None:
    response = admin_client.post("/api/auth/login", json=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0


def test_login_mit_falschem_passwort(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/auth/login", json={"username": ADMIN["username"], "password": "falsch-falsch"}
    )
    assert response.status_code == 401


def test_login_ist_unabhaengig_von_gross_kleinschreibung(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/auth/login", json={"username": "ADMIN", "password": ADMIN["password"]}
    )
    assert response.status_code == 200


def test_geschuetzte_route_ohne_token(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_geschuetzte_route_mit_unsinnigem_token(client: TestClient) -> None:
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer kein-echtes-token"})
    assert response.status_code == 401


def test_refresh_token_liefert_neuen_zugang(admin_client: TestClient) -> None:
    login = admin_client.post("/api/auth/login", json=ADMIN).json()
    response = admin_client.post(
        "/api/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_access_token_taugt_nicht_als_refresh_token(admin_client: TestClient) -> None:
    login = admin_client.post("/api/auth/login", json=ADMIN).json()
    response = admin_client.post(
        "/api/auth/refresh", json={"refresh_token": login["access_token"]}
    )
    assert response.status_code == 401


def test_deaktivierter_benutzer_kann_sich_nicht_anmelden(admin_client: TestClient) -> None:
    created = create_user(admin_client, "gast")
    admin_client.patch(f"/api/users/{created['id']}", json={"is_active": False})

    response = admin_client.post(
        "/api/auth/login",
        json={"username": "gast", "password": "passwort-1234"},
        headers={"Authorization": ""},
    )
    assert response.status_code == 403


def test_eigenes_profil_aendern(admin_client: TestClient) -> None:
    response = admin_client.patch("/api/auth/me", json={"language": "en", "display_name": "Chef"})
    assert response.status_code == 200
    assert response.json()["language"] == "en"
    assert response.json()["display_name"] == "Chef"


def test_darstellung_standard_ist_dunkel(admin_client: TestClient) -> None:
    """Ohne eigene Wahl bleibt es dunkel - das war Nexview schon immer."""
    assert admin_client.get("/api/auth/me").json()["theme"] == "dark"


def test_darstellung_wird_am_konto_gespeichert(admin_client: TestClient) -> None:
    """Die Wahl gehoert zum Konto, nicht zum Browser - sonst haette nicht jeder
    seine eigene Voreinstellung."""
    response = admin_client.patch("/api/auth/me", json={"theme": "light"})
    assert response.status_code == 200
    assert response.json()["theme"] == "light"
    # Und sie ueberdauert - beim naechsten Abruf steht sie noch da.
    assert admin_client.get("/api/auth/me").json()["theme"] == "light"


def test_unsinnige_darstellung_wird_abgelehnt(admin_client: TestClient) -> None:
    response = admin_client.patch("/api/auth/me", json={"theme": "lila"})
    assert response.status_code == 422
    assert admin_client.get("/api/auth/me").json()["theme"] == "dark"


def test_eigenes_passwort_aendern(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/auth/me/password",
        json={"current_password": ADMIN["password"], "new_password": "neues-passwort-9"},
    )
    assert response.status_code == 204

    assert (
        admin_client.post(
            "/api/auth/login",
            json={"username": ADMIN["username"], "password": "neues-passwort-9"},
            headers={"Authorization": ""},
        ).status_code
        == 200
    )


def test_passwortwechsel_braucht_altes_passwort(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/auth/me/password",
        json={"current_password": "stimmt-nicht", "new_password": "neues-passwort-9"},
    )
    assert response.status_code == 400


def test_normaler_benutzer_kommt_nicht_an_admin_routen(admin_client: TestClient) -> None:
    create_user(admin_client, "kim")
    headers = auth_headers(admin_client, "kim", "passwort-1234")

    assert admin_client.get("/api/users", headers=headers).status_code == 403
    assert (
        admin_client.post(
            "/api/users/invitations", json={"email": "neu@beispiel.de"}, headers=headers
        ).status_code
        == 403
    )


def test_anmeldung_mit_der_mailadresse(admin_client: TestClient) -> None:
    """Benutzername oder Adresse - beides führt zum selben Konto."""
    create_user(admin_client, "kim")

    for eingabe in ("kim", "KIM", "kim@beispiel.de", "  KIM@Beispiel.DE  "):
        antwort = admin_client.post(
            "/api/auth/login",
            json={"username": eingabe, "password": "passwort-1234"},
            headers={"Authorization": ""},
        )
        assert antwort.status_code == 200, f"{eingabe!r} sollte funktionieren"


def test_unbekannte_adresse_bleibt_abgewiesen(admin_client: TestClient) -> None:
    antwort = admin_client.post(
        "/api/auth/login",
        json={"username": "gibtsnicht@beispiel.de", "password": "passwort-1234"},
        headers={"Authorization": ""},
    )
    assert antwort.status_code == 401


def test_benutzername_laesst_sich_nicht_aendern(admin_client: TestClient) -> None:
    """Er steht in Anfragen, Freigaben und den Radarr-Etiketten."""
    vorher = admin_client.get("/api/auth/me").json()["username"]

    admin_client.patch("/api/auth/me", json={"username": "neuer-name"})

    assert admin_client.get("/api/auth/me").json()["username"] == vorher
