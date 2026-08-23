"""Der "Alles, was neu ist"-Hinweis nach einem Update.

Drei Regeln, alle hier festgenagelt:

1. **Nur Administratoren.** Sie haben das Update eingespielt; fuer alle
   anderen waere der Balken Laerm ueber einer Entscheidung, die sie nicht
   getroffen haben.
2. Quittiert wird die **Fassung**, kein Haken - nach dem naechsten Update
   kommt der Hinweis von selbst wieder, ohne dass irgendetwas zurueckgesetzt
   werden muesste.
3. "Schliessen" quittiert nichts - nur der ausdrueckliche Knopf tut es.
   (Das ist Sache der Oberflaeche; hier steht die Server-Seite.)
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import __version__
from app.db import SessionLocal
from app.models import User

from .conftest import auth_headers, create_user


def test_admin_bekommt_den_hinweis(admin_client: TestClient) -> None:
    antwort = admin_client.get("/api/about/neuigkeiten")
    assert antwort.status_code == 200
    assert antwort.json() == {"version": __version__, "offen": True}


def test_benutzer_bekommen_ihn_nicht(admin_client: TestClient) -> None:
    create_user(admin_client, "kim", "passwort-1234")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    antwort = admin_client.get("/api/about/neuigkeiten", headers=kopf)
    assert antwort.status_code == 200
    assert antwort.json()["offen"] is False


def test_quittieren_traegt_die_fassung_ein(admin_client: TestClient) -> None:
    antwort = admin_client.post("/api/about/neuigkeiten/gesehen")
    assert antwort.status_code == 200
    assert antwort.json()["offen"] is False

    # Ab jetzt bleibt der Hinweis weg ...
    assert admin_client.get("/api/about/neuigkeiten").json()["offen"] is False
    with SessionLocal() as db:
        admin = db.scalars(select(User).where(User.username == "admin")).one()
        assert admin.changelog_gesehen == __version__


def test_naechstes_update_bringt_ihn_zurueck(admin_client: TestClient) -> None:
    """Die quittierte Fassung ist eine andere als die laufende - genau der
    Zustand nach einem frisch eingespielten Update."""
    with SessionLocal() as db:
        admin = db.scalars(select(User).where(User.username == "admin")).one()
        admin.changelog_gesehen = "0.0.1"
        db.commit()

    assert admin_client.get("/api/about/neuigkeiten").json()["offen"] is True


def test_jeder_admin_quittiert_fuer_sich(admin_client: TestClient) -> None:
    """Der Haken eines Administrators nimmt keinem anderen den Hinweis weg."""
    zweiter = create_user(admin_client, "chef2", "passwort-1234")
    with SessionLocal() as db:
        from app.models import Role

        db.get(User, zweiter["id"]).role = Role.admin
        db.commit()

    admin_client.post("/api/about/neuigkeiten/gesehen")

    kopf = auth_headers(admin_client, "chef2", "passwort-1234")
    assert admin_client.get("/api/about/neuigkeiten", headers=kopf).json()["offen"] is True
