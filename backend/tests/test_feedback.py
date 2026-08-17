"""Rückmeldung zur Qualität - und die Antwort der Entscheider.

Bewerten dürfen nur Benutzer und Entscheider für ihre eigenen Anfragen.
Administratoren bewerten nicht, sie beantworten die Rückmeldungen.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus, User

from .conftest import auth_headers, create_user


def _geladene_anfrage(client: TestClient, benutzer: str = "kim") -> dict:
    """Legt einen Benutzer an, lässt ihn anfragen und setzt sie auf 'geladen'."""
    create_user(client, benutzer)
    headers = auth_headers(client, benutzer, "passwort-1234")
    item = client.get("/api/discover/movie").json()["items"][0]
    angelegt = client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    ).json()

    with SessionLocal() as session:
        request = session.get(MediaRequest, angelegt["id"])
        assert request is not None
        request.status = RequestStatus.downloaded
        session.commit()

    return {"id": angelegt["id"], "headers": headers}


def test_benutzer_bewertet_eigene_anfrage(arr_client: TestClient) -> None:
    anfrage = _geladene_anfrage(arr_client)

    antwort = arr_client.post(
        f"/api/requests/{anfrage['id']}/feedback",
        json={"rating": 4, "comment": "Bild gut, Ton etwas leise."},
        headers=anfrage["headers"],
    )
    assert antwort.status_code == 200
    assert antwort.json()["rating"] == 4
    assert antwort.json()["feedback"] == "Bild gut, Ton etwas leise."
    assert antwort.json()["rated_at"] is not None


def test_entscheider_darf_bewerten(arr_client: TestClient) -> None:
    """Der Entscheider hat Auto-Freigabe, seine Anfrage geht sofort an Radarr -
    deshalb entsteht sie hier als normaler Benutzer und wird erst danach zum
    Entscheider gemacht."""
    anfrage = _geladene_anfrage(arr_client, "eva")
    with SessionLocal() as session:
        eva = session.query(User).filter(User.username == "eva").one()
        kennung = eva.id
    arr_client.patch(f"/api/users/{kennung}", json={"role": "approver"})
    eva_neu = auth_headers(arr_client, "eva", "passwort-1234")

    antwort = arr_client.post(
        f"/api/requests/{anfrage['id']}/feedback", json={"rating": 5}, headers=eva_neu
    )
    assert antwort.status_code == 200


def test_admin_bewertet_nicht(arr_client: TestClient) -> None:
    """Der Admin soll antworten, nicht selbst Sterne vergeben."""
    anfrage = _geladene_anfrage(arr_client)
    # Die Anfrage dem Admin zuschreiben - er darf sie trotzdem nicht bewerten.
    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == "admin").one()
        request = session.get(MediaRequest, anfrage["id"])
        assert request is not None
        request.user_id = admin.id
        session.commit()

    antwort = arr_client.post(f"/api/requests/{anfrage['id']}/feedback", json={"rating": 3})
    assert antwort.status_code == 403


def test_fremde_anfrage_kann_man_nicht_bewerten(arr_client: TestClient) -> None:
    anfrage = _geladene_anfrage(arr_client, "kim")
    create_user(arr_client, "alex")
    alex = auth_headers(arr_client, "alex", "passwort-1234")

    antwort = arr_client.post(
        f"/api/requests/{anfrage['id']}/feedback", json={"rating": 1}, headers=alex
    )
    assert antwort.status_code == 404


def test_erst_bewerten_wenn_geladen(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = arr_client.get("/api/discover/movie").json()["items"][0]
    angelegt = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    ).json()

    antwort = arr_client.post(
        f"/api/requests/{angelegt['id']}/feedback", json={"rating": 5}, headers=headers
    )
    assert antwort.status_code == 409


def test_entscheider_werden_benachrichtigt(arr_client: TestClient) -> None:
    created = create_user(arr_client, "eva")
    arr_client.patch(f"/api/users/{created['id']}", json={"role": "approver"})
    anfrage = _geladene_anfrage(arr_client, "kim")

    arr_client.post(
        f"/api/requests/{anfrage['id']}/feedback",
        json={"rating": 1, "comment": "Nur Kamerafassung."},
        headers=anfrage["headers"],
    )

    with SessionLocal() as session:
        for name in ("admin", "eva"):
            empfaenger = session.query(User).filter(User.username == name).one()
            assert "feedback_poor" in [n.type.value for n in empfaenger.notifications]


def test_gute_bewertung_meldet_keine_beschwerde(arr_client: TestClient) -> None:
    anfrage = _geladene_anfrage(arr_client)
    arr_client.post(
        f"/api/requests/{anfrage['id']}/feedback", json={"rating": 5}, headers=anfrage["headers"]
    )

    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == "admin").one()
        arten = [n.type.value for n in admin.notifications]
        assert "feedback" in arten
        assert "feedback_poor" not in arten


def test_zweite_bewertung_meldet_nicht_erneut(arr_client: TestClient) -> None:
    """Sonst sammeln sich Meldungen, wenn jemand seine Meinung ändert."""
    anfrage = _geladene_anfrage(arr_client)
    for note in (3, 4):
        arr_client.post(
            f"/api/requests/{anfrage['id']}/feedback",
            json={"rating": note},
            headers=anfrage["headers"],
        )

    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == "admin").one()
        meldungen = [n for n in admin.notifications if n.type.value.startswith("feedback")]
        assert len(meldungen) == 1


def test_admin_antwortet_auf_die_rueckmeldung(arr_client: TestClient) -> None:
    anfrage = _geladene_anfrage(arr_client)
    arr_client.post(
        f"/api/requests/{anfrage['id']}/feedback",
        json={"rating": 2, "comment": "Ton auf Englisch."},
        headers=anfrage["headers"],
    )

    antwort = arr_client.post(
        f"/api/admin/requests/{anfrage['id']}/reply",
        json={"reply": "Ich suche eine deutsche Fassung."},
        headers=None,
    )
    assert antwort.status_code == 200
    assert antwort.json()["feedback_reply"] == "Ich suche eine deutsche Fassung."
    assert antwort.json()["replied_at"] is not None

    # Der Anfragende erfährt davon.
    with SessionLocal() as session:
        kim = session.query(User).filter(User.username == "kim").one()
        assert "feedback_reply" in [n.type.value for n in kim.notifications]


def test_antwort_nur_auf_bewertete_anfragen(arr_client: TestClient) -> None:
    anfrage = _geladene_anfrage(arr_client)

    antwort = arr_client.post(
        f"/api/admin/requests/{anfrage['id']}/reply", json={"reply": "Hallo?"}
    )
    assert antwort.status_code == 409


def test_benutzer_darf_nicht_antworten(arr_client: TestClient) -> None:
    anfrage = _geladene_anfrage(arr_client)
    arr_client.post(
        f"/api/requests/{anfrage['id']}/feedback", json={"rating": 2}, headers=anfrage["headers"]
    )

    antwort = arr_client.post(
        f"/api/admin/requests/{anfrage['id']}/reply",
        json={"reply": "Danke fürs Feedback."},
        headers=anfrage["headers"],
    )
    assert antwort.status_code == 403


def test_entscheider_darf_nicht_antworten(arr_client: TestClient) -> None:
    """Antworten ist dem Administrator vorbehalten - der Entscheider
    entscheidet über Anfragen, spricht aber nicht für den Betreiber."""
    anfrage = _geladene_anfrage(arr_client)
    arr_client.post(
        f"/api/requests/{anfrage['id']}/feedback",
        json={"rating": 2, "comment": "Ton kaputt."},
        headers=anfrage["headers"],
    )

    created = create_user(arr_client, "eva")
    arr_client.patch(f"/api/users/{created['id']}", json={"role": "approver"})
    eva = auth_headers(arr_client, "eva", "passwort-1234")

    antwort = arr_client.post(
        f"/api/admin/requests/{anfrage['id']}/reply", json={"reply": "Ich kümmere mich."}, headers=eva
    )
    assert antwort.status_code == 403


def test_bewertung_muss_zwischen_null_und_fuenf_liegen(arr_client: TestClient) -> None:
    anfrage = _geladene_anfrage(arr_client)

    for ungueltig in (-1, 6):
        antwort = arr_client.post(
            f"/api/requests/{anfrage['id']}/feedback",
            json={"rating": ungueltig},
            headers=anfrage["headers"],
        )
        assert antwort.status_code == 422


def test_bewertung_steht_in_der_admin_uebersicht(arr_client: TestClient) -> None:
    anfrage = _geladene_anfrage(arr_client)
    arr_client.post(
        f"/api/requests/{anfrage['id']}/feedback",
        json={"rating": 3, "comment": "Geht so."},
        headers=anfrage["headers"],
    )

    alle = arr_client.get("/api/admin/requests").json()
    eintrag = next(e for e in alle if e["id"] == anfrage["id"])
    assert eintrag["rating"] == 3
    assert eintrag["feedback"] == "Geht so."
    assert eintrag["username"] == "kim"
