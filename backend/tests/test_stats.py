"""Statistik-Seite: Zahlen zu Anfragen, Kontingenten und Bewertungen."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus

from .conftest import auth_headers, create_user
from sqlalchemy import select


def _anfrage(client: TestClient, headers: dict, index: int = 0) -> int:
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
    ).json()["id"]


def _setze_status(request_id: int, status: RequestStatus) -> None:
    with SessionLocal() as session:
        request = session.get(MediaRequest, request_id)
        assert request is not None
        request.status = status
        session.commit()


def test_leere_statistik(admin_client: TestClient) -> None:
    zahlen = admin_client.get("/api/admin/stats").json()
    assert zahlen["totals"]["requests"] == 0
    assert zahlen["totals"]["average_rating"] is None
    assert zahlen["totals"]["active_users"] == 0
    # Der Admin taucht trotzdem als Zeile auf - nur eben mit Nullen.
    assert [e["username"] for e in zahlen["users"]] == ["admin"]


def test_zaehlt_anfragen_und_zustaende(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")

    geladen = _anfrage(arr_client, kim, 0)
    _setze_status(geladen, RequestStatus.downloaded)
    _anfrage(arr_client, kim, 1)  # wartet auf Freigabe

    zahlen = arr_client.get("/api/admin/stats").json()
    assert zahlen["totals"]["requests"] == 2
    assert zahlen["totals"]["downloaded"] == 1
    assert zahlen["totals"]["pending"] == 1
    assert zahlen["totals"]["movies"] == 2
    assert zahlen["totals"]["active_users"] == 1

    eintrag = next(e for e in zahlen["users"] if e["username"] == "kim")
    assert eintrag["total"] == 2
    assert eintrag["downloaded"] == 1
    assert eintrag["success_rate"] == 50


def test_bewertungen_fliessen_ein(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")

    for index, note in enumerate((5, 1)):
        anfrage = _anfrage(arr_client, kim, index)
        _setze_status(anfrage, RequestStatus.downloaded)
        arr_client.post(
            f"/api/requests/{anfrage}/feedback",
            json={"rating": note, "comment": "Kommentar"},
            headers=kim,
        )

    zahlen = arr_client.get("/api/admin/stats").json()
    gesamt = zahlen["totals"]
    assert gesamt["ratings"] == 2
    assert gesamt["average_rating"] == 3.0
    assert gesamt["poor_ratings"] == 1
    assert gesamt["rating_distribution"]["5"] == 1
    assert gesamt["rating_distribution"]["1"] == 1
    # Beide Kommentare sind noch unbeantwortet.
    assert gesamt["unanswered_feedback"] == 2

    eintrag = next(e for e in zahlen["users"] if e["username"] == "kim")
    assert eintrag["ratings"] == 2
    assert eintrag["average_rating"] == 3.0
    assert eintrag["poor_ratings"] == 1


def test_beantwortete_rueckmeldung_zaehlt_nicht_mehr_als_offen(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    anfrage = _anfrage(arr_client, kim)
    _setze_status(anfrage, RequestStatus.downloaded)
    arr_client.post(
        f"/api/requests/{anfrage}/feedback",
        json={"rating": 2, "comment": "Ton kaputt."},
        headers=kim,
    )
    assert arr_client.get("/api/admin/stats").json()["totals"]["unanswered_feedback"] == 1

    arr_client.post(f"/api/admin/requests/{anfrage}/reply", json={"reply": "Ich schaue nach."})
    assert arr_client.get("/api/admin/stats").json()["totals"]["unanswered_feedback"] == 0


def test_kontingent_steht_in_der_statistik(arr_client: TestClient) -> None:
    created = create_user(arr_client, "kim")
    arr_client.patch(f"/api/users/{created['id']}", json={"quota_movies_limit": 2})
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _anfrage(arr_client, kim)

    eintrag = next(
        e for e in arr_client.get("/api/admin/stats").json()["users"] if e["username"] == "kim"
    )
    assert eintrag["quota_movie_used"] == 1
    assert eintrag["quota_movie_limit"] == 2
    # Serien sind unbegrenzt.
    assert eintrag["quota_series_limit"] is None


def test_ohne_speicherbetrieb_kein_speicherstand(arr_client: TestClient) -> None:
    """Voreinstellung ist die Stueckzaehlung - dann bleibt das Feld leer.

    Die Oberflaeche entscheidet daran, welche Spalten sie zeigt: Ist der Wert
    ``None``, stehen dort die Stueck-Kontingente.
    """
    create_user(arr_client, "kim")
    eintrag = next(
        e for e in arr_client.get("/api/admin/stats").json()["users"] if e["username"] == "kim"
    )
    assert eintrag["storage_used_bytes"] is None
    assert eintrag["storage_limit_bytes"] is None
    # Die Stueck-Zahlen sind trotzdem da.
    assert eintrag["quota_movie_used"] == 0


def test_im_speicherbetrieb_steht_der_platz_da(arr_client: TestClient) -> None:
    """⚠️ Anzahl **oder** Speicher, nie beides.

    Die Statistik zeigte frueher immer die Stueck-Kontingente, auch wenn das
    Haus laengst auf GB umgestellt hatte: darueber der belegte Platz in
    Gigabyte, darunter "unbegrenzt" Stueck - zwei Waehrungen nebeneinander,
    von denen nur eine gilt.

    ⚠️ Und die Felder muessen es bis zum Browser schaffen. Beim ersten Anlauf
    wurden sie berechnet, standen aber nicht in ``UserStatsPublic`` - Pydantic
    liess sie ohne Fehler und ohne Log einfach weg.
    """
    arr_client.put("/api/settings", json={"storage_enabled": True})
    created = create_user(arr_client, "kim")
    arr_client.patch(f"/api/users/{created['id']}", json={"storage_limit_gb": 50})

    eintrag = next(
        e for e in arr_client.get("/api/admin/stats").json()["users"] if e["username"] == "kim"
    )
    assert eintrag["storage_used_bytes"] == 0
    assert eintrag["storage_limit_bytes"] == 50 * 1024**3


def test_kinderkonten_stehen_nicht_in_der_statistik(arr_client: TestClient) -> None:
    """Sie haben kein eigenes Kontingent.

    Gibt ein Elternteil einen Kinderwunsch frei, laeuft die Anfrage auf
    **seinen** Namen. Ein Kind kann hier also nie etwas angesammelt haben,
    stuende aber mit lauter Nullen in der Aufstellung.
    """
    from app.db import SessionLocal
    from app.models import Role, User

    with SessionLocal() as db:
        elternteil = db.scalars(select(User).where(User.role == Role.admin)).first()
        assert elternteil is not None
        db.add(
            User(
                username="probekind",
                display_name="Probekind",
                email=None,
                password_hash="x",
                role=Role.child,
                parent_id=elternteil.id,
                age=8,
                email_verified=True,
            )
        )
        db.commit()

    namen = {e["username"] for e in arr_client.get("/api/admin/stats").json()["users"]}
    assert "probekind" not in namen


def test_mehrfach_angefragte_titel(arr_client: TestClient) -> None:
    """Derselbe Titel von zwei Leuten - einmal abgebrochen, einmal neu."""
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    erste = _anfrage(arr_client, kim)
    _setze_status(erste, RequestStatus.cancelled)

    create_user(arr_client, "alex")
    alex = auth_headers(arr_client, "alex", "passwort-1234")
    _anfrage(arr_client, alex)

    beliebt = arr_client.get("/api/admin/stats").json()["most_requested"]
    assert len(beliebt) == 1
    assert beliebt[0]["count"] == 2


def test_verlauf_hat_sechs_monate(admin_client: TestClient) -> None:
    verlauf = admin_client.get("/api/admin/stats").json()["history"]
    assert len(verlauf) == 6
    # Aufsteigend sortiert, der aktuelle Monat zuletzt.
    assert verlauf == sorted(verlauf, key=lambda punkt: punkt["month"])


def test_entscheider_sieht_die_statistik(arr_client: TestClient) -> None:
    created = create_user(arr_client, "eva")
    arr_client.patch(f"/api/users/{created['id']}", json={"role": "approver"})
    eva = auth_headers(arr_client, "eva", "passwort-1234")

    assert arr_client.get("/api/admin/stats", headers=eva).status_code == 200


def test_benutzer_sieht_die_statistik_nicht(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")

    assert arr_client.get("/api/admin/stats", headers=kim).status_code == 403


def test_ohne_anmeldung_keine_statistik(client: TestClient) -> None:
    assert client.get("/api/admin/stats").status_code == 401
