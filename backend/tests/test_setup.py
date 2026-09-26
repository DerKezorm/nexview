"""Die Erst-Einrichtung darf genau einmal funktionieren."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import db as db_modul
from app.db import SessionLocal
from app.models import User

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


def test_ein_update_bestaetigt_keine_adresse_des_betreibers(client: TestClient) -> None:
    """Eine unbestaetigte Adresse des Betreibers bleibt es auch nach dem Update.

    Bis zur Abnahme von 1.0.0 stand hier ein Einmal-Schritt
    (``_ersten_administrator_bestaetigen``), der sie beim Update bestaetigte,
    damit ein schon ausgesperrter Betreiber wieder hereinkam. Das erledigt
    jetzt die Anmeldung, die den Betreiber von der Sperre ausnimmt. Der
    Schritt haette dazu jede spaeter geaenderte, nie gepruefte Adresse des
    Betreibers bestaetigt, und Benachrichtigungen gingen an sie.
    """
    assert client.post("/api/setup/admin", json=ADMIN).status_code == 201
    _betreiber_unbestaetigt()

    db_modul.init_db()

    assert _bestaetigt() is False
    assert _anmelden(client) == 200


def test_ein_alter_bucheintrag_des_entfernten_schritts_stoert_nicht(
    client: TestClient,
) -> None:
    """Eine Datenbank, auf der der Schritt vor seiner Entfernung schon lief.

    Die Zeile im Wanderungsbuch bleibt liegen; das Buch fragt nur nach den
    Schritten, die es gibt.
    """
    assert client.post("/api/setup/admin", json=ADMIN).status_code == 201
    with db_modul.engine.begin() as verbindung:
        db_modul._eintragen(
            verbindung, "_ersten_administrator_bestaetigen", db_modul.AUSGEFUEHRT
        )
    _betreiber_unbestaetigt()

    db_modul.init_db()

    assert _bestaetigt() is False
    assert _anmelden(client) == 200
