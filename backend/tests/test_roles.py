"""Rollen: Admin, Entscheider, Benutzer - wer darf was."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus, User

from .conftest import ADMIN, auth_headers, create_user


def _anfrage(client: TestClient, headers: dict | None = None, index: int = 0):
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


# --- Administratoren geben sich selbst frei --------------------------------


def test_admin_anfrage_wartet_nicht_auf_freigabe(arr_client: TestClient) -> None:
    """Der Admin ist selbst die Freigabe-Instanz - er darf nicht auf sich
    selbst warten müssen."""
    # Haken bewusst entfernen: er darf trotzdem keine Wirkung haben.
    me = arr_client.get("/api/auth/me").json()
    arr_client.patch(f"/api/users/{me['id']}", json={"auto_approve": False})

    antwort = _anfrage(arr_client)
    # Radarr ist im Test nicht erreichbar -> die Übergabe scheitert (502),
    # aber eben nicht mit "wartet auf Freigabe".
    assert antwort.status_code == 502

    with SessionLocal() as session:
        request = session.query(MediaRequest).one()
        assert request.status != RequestStatus.pending_approval


def test_auto_freigabe_laesst_sich_beim_admin_nicht_abschalten(arr_client: TestClient) -> None:
    me = arr_client.get("/api/auth/me").json()
    geaendert = arr_client.patch(f"/api/users/{me['id']}", json={"auto_approve": False}).json()
    assert geaendert["effective_auto_approve"] is True


def test_hochstufen_zum_admin_schaltet_freigabe_ein(arr_client: TestClient) -> None:
    kim = create_user(arr_client, "kim")
    assert kim["effective_auto_approve"] is False

    zum_admin = arr_client.patch(f"/api/users/{kim['id']}", json={"role": "admin"}).json()
    assert zum_admin["effective_auto_approve"] is True


def test_normaler_benutzer_behaelt_seinen_haken(arr_client: TestClient) -> None:
    kim = create_user(arr_client, "kim")
    mit_haken = arr_client.patch(f"/api/users/{kim['id']}", json={"auto_approve": True}).json()
    assert mit_haken["auto_approve"] is True
    assert mit_haken["effective_auto_approve"] is True


# --- Entscheider -----------------------------------------------------------


def _entscheider(client: TestClient) -> dict[str, str]:
    created = create_user(client, "eva")
    client.patch(f"/api/users/{created['id']}", json={"role": "approver"})
    return auth_headers(client, "eva", "passwort-1234")


def test_entscheider_darf_anfragen_sehen_und_ablehnen(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    angelegt = _anfrage(arr_client, kim).json()

    eva = _entscheider(arr_client)
    assert arr_client.get("/api/admin/requests", headers=eva).status_code == 200
    assert (
        arr_client.post(
            f"/api/admin/requests/{angelegt['id']}/reject", json={}, headers=eva
        ).status_code
        == 200
    )


def test_entscheider_darf_keine_einstellungen_aendern(arr_client: TestClient) -> None:
    eva = _entscheider(arr_client)

    assert arr_client.get("/api/settings", headers=eva).status_code == 403
    assert arr_client.put("/api/settings", json={}, headers=eva).status_code == 403
    assert arr_client.get("/api/users", headers=eva).status_code == 403
    assert (
        arr_client.post(
            "/api/users/invitations", json={"email": "neu@beispiel.de"}, headers=eva
        ).status_code
        == 403
    )


def test_entscheider_gibt_sich_selbst_frei(arr_client: TestClient) -> None:
    """Wer selbst freigeben darf, wartet nicht auf eine eigene Freigabe.

    Das Kontingent zeigt es am deutlichsten: ``auto_approve`` ist gesetzt und
    laesst sich auch nicht abschalten.
    """
    eva = _entscheider(arr_client)

    stand = arr_client.get("/api/requests/quota", headers=eva).json()
    assert stand["auto_approve"] is True

    with SessionLocal() as session:
        kennung = session.query(User).filter(User.username == "eva").one().id
    arr_client.patch(f"/api/users/{kennung}", json={"auto_approve": False})

    danach = arr_client.get("/api/requests/quota", headers=eva).json()
    assert danach["auto_approve"] is True


def test_entscheider_wird_ueber_neue_anfragen_benachrichtigt(arr_client: TestClient) -> None:
    _entscheider(arr_client)
    create_user(arr_client, "kim")
    _anfrage(arr_client, auth_headers(arr_client, "kim", "passwort-1234"))

    with SessionLocal() as session:
        eva = session.query(User).filter(User.username == "eva").one()
        assert [n.type.value for n in eva.notifications] == ["request_pending"]


def test_entscheider_kann_sammelfreigeben(arr_client: TestClient) -> None:
    """Er darf die Benutzerliste nicht sehen - die Kennung muss deshalb an
    der Anfrage selbst hängen."""
    kim = create_user(arr_client, "kim")
    kim_headers = auth_headers(arr_client, "kim", "passwort-1234")
    _anfrage(arr_client, kim_headers, index=0)
    _anfrage(arr_client, kim_headers, index=1)

    eva = _entscheider(arr_client)
    liste = arr_client.get("/api/admin/requests", headers=eva).json()
    assert {eintrag["user_id"] for eintrag in liste} == {kim["id"]}
    assert all("avatar_url" in eintrag for eintrag in liste)

    # Und die Sammelfreigabe steht ihm offen (scheitert hier nur an Radarr).
    antwort = arr_client.post(f"/api/admin/requests/approve-all/{kim['id']}", headers=eva)
    assert antwort.status_code == 200


def test_normaler_benutzer_bleibt_aussen_vor(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")

    assert arr_client.get("/api/admin/requests", headers=kim).status_code == 403
    assert arr_client.get("/api/settings", headers=kim).status_code == 403


def test_admin_bleibt_admin(arr_client: TestClient) -> None:
    """Die neue Rolle darf dem Admin nichts wegnehmen."""
    assert arr_client.get("/api/settings").status_code == 200
    assert arr_client.get("/api/admin/requests").status_code == 200
    assert arr_client.post("/api/auth/login", json=ADMIN).status_code == 200
