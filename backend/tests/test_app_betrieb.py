"""Block 2 der App-Flaeche: der taegliche Betrieb unter ``/api/app/1``.

Die Handler sind dieselben wie im Browser; bewacht wird hier, dass die Zweitschrift
ihre Wachen behalten hat. Genau das ist die Falle: Eine Wache, die am Dekorator
haengt statt am Handler (``betreiberschutz`` an ``update_user``), ginge beim zweiten
Registrieren still verloren.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, User

from .conftest import ADMIN, auth_headers, create_user


def _id(name: str) -> int:
    with SessionLocal() as db:
        return db.query(User).filter(User.username == name).one().id


def test_der_betreiber_bleibt_in_der_app_geschuetzt(admin_client: TestClient) -> None:
    create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
    zweiter = auth_headers(admin_client, "zweiter", "zweites-passwort")
    betreiber = _id(ADMIN["username"])

    antwort = admin_client.patch(f"/api/app/1/admin/users/{betreiber}", json={"is_active": False}, headers=zweiter)

    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "betreiber_geschuetzt"
    with SessionLocal() as db:
        assert db.get(User, betreiber).is_active is True


def test_ein_admin_aendert_andere_konten_wie_im_browser(admin_client: TestClient) -> None:
    create_user(admin_client, "jonas", "passwort-1234")
    antwort = admin_client.patch(f"/api/app/1/admin/users/{_id('jonas')}", json={"is_active": False})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["is_active"] is False


ADMIN_ADRESSEN = (
    "/api/app/1/admin/dashboard",
    "/api/app/1/admin/downloads",
    "/api/app/1/admin/users",
    "/api/app/1/admin/invitations",
    "/api/app/1/admin/blocklist",
    "/api/app/1/admin/releases",
)


def test_die_betriebsadressen_sind_fuer_administratoren(admin_client: TestClient) -> None:
    create_user(admin_client, "jonas", "passwort-1234")
    create_user(admin_client, "robin", "passwort-1234", role=Role.approver)
    for name in ("jonas", "robin"):
        kopf = auth_headers(admin_client, name, "passwort-1234")
        for pfad in ADMIN_ADRESSEN:
            assert admin_client.get(pfad, headers=kopf).status_code == 403, (name, pfad)
    for pfad in ADMIN_ADRESSEN:
        assert admin_client.get(pfad).status_code == 200, pfad
    # Rueckmeldungen lesen und beantworten auch Entscheider - wie im Browser
    # unter Anfragen, Filter "Rueckmeldungen".
    robin = auth_headers(admin_client, "robin", "passwort-1234")
    assert admin_client.get("/api/app/1/admin/ratings", headers=robin).status_code == 200
    jonas = auth_headers(admin_client, "jonas", "passwort-1234")
    assert admin_client.get("/api/app/1/admin/ratings", headers=jonas).status_code == 403


def test_ein_kind_erreicht_nichts_davon(admin_client: TestClient) -> None:
    create_user(admin_client, "sabine", "passwort-1234", can_manage_children=True)
    eltern = auth_headers(admin_client, "sabine", "passwort-1234")
    admin_client.post("/api/children", json={"username": "mia", "password": "kind-passwort", "age": 8}, headers=eltern)
    kind = auth_headers(admin_client, "mia", "kind-passwort")
    for pfad in (
        *ADMIN_ADRESSEN,
        "/api/app/1/notifications",
        "/api/app/1/calendar",
        "/api/app/1/favorites",
        "/api/app/1/tickets",
        "/api/app/1/ratings/mine",
        "/api/app/1/watchlist/status",
        "/api/app/1/browse/shelves/movie",
    ):
        assert admin_client.get(pfad, headers=kind).status_code == 403, pfad


def test_tickets_gehen_ueber_die_app_wie_im_browser(admin_client: TestClient) -> None:
    create_user(admin_client, "jonas", "passwort-1234")
    kopf = auth_headers(admin_client, "jonas", "passwort-1234")
    neu = admin_client.post("/api/app/1/tickets", json={"subject": "Ton fehlt", "body": "Ab der Hälfte."}, headers=kopf)
    assert neu.status_code in (200, 201), neu.text
    kennung = neu.json()["id"]
    assert [t["id"] for t in admin_client.get("/api/app/1/tickets", headers=kopf).json()] == [kennung]
    # Den Zustand setzt nur der Administrator.
    assert admin_client.patch(f"/api/app/1/admin/tickets/{kennung}", json={"status": "closed"}, headers=kopf).status_code == 403
    assert admin_client.patch(f"/api/app/1/admin/tickets/{kennung}", json={"status": "closed"}).status_code == 200


def test_die_glocke_zaehlt_und_wird_gelesen(admin_client: TestClient) -> None:
    create_user(admin_client, "jonas", "passwort-1234")
    kopf = auth_headers(admin_client, "jonas", "passwort-1234")
    assert admin_client.get("/api/app/1/notifications", headers=kopf).status_code == 200
    assert admin_client.get("/api/app/1/notifications/unread/count", headers=kopf).status_code == 200
    assert admin_client.post("/api/app/1/notifications/read-all", headers=kopf).status_code in (200, 204)
