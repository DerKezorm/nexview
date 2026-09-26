"""Die Erst-Einrichtung darf genau einmal funktionieren."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import db as db_modul
from app.db import SessionLocal
from app.models import Role, User

from .conftest import ADMIN, create_user


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


# --- Der erste Administrator gilt sofort als bestaetigt ----------------------
#
# Befund aus dem grossen Pruefgang: Der Assistent schreibt ueber dem Feld "Sie
# gilt sofort als bestaetigt", das Konto entstand aber unbestaetigt. Ohne
# Mailserver kam der Betreiber nach der ersten abgelaufenen Sitzung nicht mehr
# in die eigene Installation: 403 ``email_unverified``, und die
# Bestaetigungsmail liess sich nicht verschicken.


def _anmelden(client: TestClient) -> int:
    client.cookies.clear()
    return client.post(
        "/api/auth/login",
        json={"username": ADMIN["username"], "password": ADMIN["password"]},
    ).status_code


def test_erster_admin_kommt_ohne_mailserver_wieder_herein(client: TestClient) -> None:
    """Abmelden, neu anmelden - ohne dass je ein Mailserver eingerichtet war."""
    antwort = client.post("/api/setup/admin", json=ADMIN)
    assert antwort.status_code == 201
    kopf = {"Authorization": f"Bearer {antwort.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=kopf).json()["email_verified"] is True

    client.post("/api/auth/logout")

    assert _anmelden(client) == 200


def _betreiber_unbestaetigt() -> None:
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == ADMIN["username"]).one()
        konto.email_verified = False
        db.commit()


def _bestaetigt() -> bool:
    with SessionLocal() as db:
        return db.query(User).filter(User.username == ADMIN["username"]).one().email_verified


def _herkunft() -> str | None:
    with db_modul.engine.connect() as verbindung:
        return verbindung.exec_driver_sql(
            "SELECT wanderung_herkunft FROM wanderungen WHERE wanderung_name = ?",
            ("_ersten_administrator_bestaetigen",),
        ).scalar()


def _datenbank_von_vorher() -> None:
    """Das Buch ohne den neuen Schritt - so kommt eine Datenbank vor 1.0.0 an.

    Der ``client`` hat beim Hochfahren schon ``init_db`` laufen lassen, auf
    leeren Tabellen; der Schritt steht dadurch als vorgefunden im Buch.
    """
    with db_modul.engine.begin() as verbindung:
        verbindung.exec_driver_sql(
            "DELETE FROM wanderungen WHERE wanderung_name = ?",
            ("_ersten_administrator_bestaetigen",),
        )


def test_ein_schon_ausgesperrter_betreiber_kommt_nach_dem_update_wieder_herein(
    client: TestClient,
) -> None:
    """Bestehende Installation: der Betreiber von damals, unbestaetigt.

    Hereinlassen wuerde ihn seit 1.0.0 schon die Anmeldung, die den Betreiber
    von der Sperre ausnimmt (``test_onboarding.py``). Der Schritt holt die
    Bestaetigung trotzdem nach: Der Assistent hatte sie versprochen, und ohne
    sie gehen keine Benachrichtigungen an die Adresse.
    """
    assert client.post("/api/setup/admin", json=ADMIN).status_code == 201
    _betreiber_unbestaetigt()
    _datenbank_von_vorher()
    assert _bestaetigt() is False, "Vorbedingung: so sah der Befund aus"

    db_modul.init_db()

    assert _bestaetigt() is True
    assert _herkunft() == db_modul.AUSGEFUEHRT
    assert _anmelden(client) == 200


def test_der_schritt_laeuft_nur_einmal(client: TestClient) -> None:
    """Eine spaeter geaenderte Adresse gilt erst mit dem Link als bestaetigt.

    Liefe der Schritt bei jedem Start, bestaetigte ein Neustart sie ungefragt.
    """
    assert client.post("/api/setup/admin", json=ADMIN).status_code == 201
    _betreiber_unbestaetigt()
    _datenbank_von_vorher()
    db_modul.init_db()
    assert _bestaetigt() is True

    _betreiber_unbestaetigt()
    db_modul.init_db()

    assert _bestaetigt() is False


def test_der_schritt_fasst_nur_den_betreiber_an(admin_client: TestClient) -> None:
    """Ein anderes unbestaetigtes Konto bleibt, wie es ist - auch ein Administrator."""
    create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
    with SessionLocal() as db:
        zweiter = db.query(User).filter(User.username == "zweiter").one()
        zweiter.email_verified = False
        db.commit()
    _betreiber_unbestaetigt()
    _datenbank_von_vorher()

    db_modul.init_db()

    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "zweiter").one().email_verified is False
    assert _bestaetigt() is True


def test_ohne_unbestaetigten_betreiber_gilt_der_schritt_als_erledigt(
    client: TestClient,
) -> None:
    """Nichts nachzuholen: vorgefunden, nicht ausgefuehrt."""
    assert client.post("/api/setup/admin", json=ADMIN).status_code == 201
    _datenbank_von_vorher()

    db_modul.init_db()

    assert _herkunft() == db_modul.VORGEFUNDEN
