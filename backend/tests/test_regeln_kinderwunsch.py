"""Eine Regel darf das Ja der Eltern nicht lautlos in ein Nein verwandeln.

⚠️ **Der Fall, den eine unabhaengige Pruefung am 03.09.2026 gefunden hat.**
Das Kind wuenscht, das Elternteil klickt "Freigeben" - und ``create_request``
gibt eine Anfrage im Zustand ``rejected`` zurueck, weil eine Regel des Hauses
sie abgelehnt hat. Eine Ausnahme fliegt dabei nicht.

``child_wishes.freigeben`` sah das Ergebnis nicht an und schrieb ``released``.
Folge: Die Antwort war **HTTP 200**, in der Datenbank stand "freigegeben", das
Elternteil sah den Wunsch aus seiner Liste verschwinden und glaubte, es habe
freigegeben. Das Kind las "diesmal nicht". Und niemand erfuhr, dass eine Regel
dazwischenstand.

Fuer ein Feature, dessen erklaerte Grenze "die Entscheidung der Eltern" ist,
war das die unangenehmste Stelle.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    ChildWish,
    MediaRequest,
    Regel,
    RegelEntscheidung,
    RequestStatus,
    User,
    WishState,
)

from .conftest import auth_headers, create_user


def _titel(client: TestClient) -> dict:
    return client.get("/api/discover/movie").json()["items"][0]


def _kind_mit_wunsch(arr_client: TestClient) -> tuple[dict, int, dict]:
    """Elternteil, Wunsch-Nummer und der Titel, um den es geht."""
    # Zielordner und Profil waehlt der Entscheider - sonst verlangt die
    # Freigabe hier ein Qualitaetsprofil, und darum geht es in dieser Datei
    # nicht.
    gesetzt = arr_client.put("/api/settings", json={"movie_root_folder_mode": "approver"})
    assert gesetzt.status_code == 200, gesetzt.text

    create_user(arr_client, "elternteil")
    # Ohne den Haken darf niemand Kinderkonten fuehren - auch nicht im Test.
    with SessionLocal() as db:
        person = db.query(User).filter(User.username == "elternteil").one()
        person.can_manage_children = True
        db.commit()
    eltern = auth_headers(arr_client, "elternteil", "passwort-1234")

    angelegt = arr_client.post(
        "/api/children",
        json={
            "username": "kind",
            "display_name": "Kind",
            "password": "kind-passwort-1234",
            "age": 10,
        },
        headers=eltern,
    )
    assert angelegt.status_code in (200, 201), angelegt.text
    kind_id = angelegt.json()["id"]

    item = _titel(arr_client)
    with SessionLocal() as db:
        eltern_id = db.query(User).filter(User.username == "elternteil").one().id
        wunsch = ChildWish(
            parent_id=eltern_id,
            child_id=kind_id,
            media_type=item["media_type"],
            tmdb_id=item["tmdb_id"],
            title=item["title"],
            state=WishState.open,
        )
        db.add(wunsch)
        db.commit()
        wunsch_id = wunsch.id

    return eltern, wunsch_id, item


def _regel(entscheidung: RegelEntscheidung = RegelEntscheidung.ablehnen) -> None:
    with SessionLocal() as db:
        db.add(
            Regel(
                name="Keine Filme",
                position=0,
                bedingungen=[{"feld": "typ", "werte": ["movie"]}],
                entscheidung=entscheidung,
                begruendung="Filme holen wir gerade nicht.",
            )
        )
        db.commit()


def test_eine_regel_schliesst_den_wunsch_nicht_als_freigegeben(
    arr_client: TestClient,
) -> None:
    """Der Kern: Was abgelehnt wurde, darf nicht als freigegeben dastehen."""
    eltern, wunsch_id, _ = _kind_mit_wunsch(arr_client)
    _regel()

    antwort = arr_client.post(f"/api/children/wishes/{wunsch_id}/release", json={}, headers=eltern)
    assert antwort.status_code == 409, antwort.text

    with SessionLocal() as db:
        wunsch = db.get(ChildWish, wunsch_id)
        assert wunsch is not None
        # ⚠️ **Offen, nicht ``released`` und nicht ``obsolete``.** Der Wunsch
        # ist weder erfuellt noch erledigt - er wartet, und das Elternteil kann
        # ueber die abgelehnte Anfrage weiter nachfassen, wenn die Regel es
        # zulaesst.
        assert wunsch.state == WishState.open
        assert wunsch.request_id is None


def test_die_abgelehnte_anfrage_bleibt_beim_elternteil_stehen(
    arr_client: TestClient,
) -> None:
    """Sie ist der Vorgang, an dem ein spaeteres „trotzdem fragen“ haengt.

    Sie wegzuwerfen waere bequemer, naehme dem Elternteil aber jede Spur: Es
    wuesste nur, dass etwas nicht ging, und haette keinen Ort, an dem der Grund
    steht.
    """
    eltern, wunsch_id, _ = _kind_mit_wunsch(arr_client)
    _regel()
    arr_client.post(f"/api/children/wishes/{wunsch_id}/release", json={}, headers=eltern)

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.status == RequestStatus.rejected
        assert anfrage.rejection_reason == "Filme holen wir gerade nicht."
        assert anfrage.regel_id is not None


def test_ohne_regel_wird_der_wunsch_ganz_normal_freigegeben(
    arr_client: TestClient,
) -> None:
    """⚠️ **Die Gegenprobe, und ohne sie taugen die beiden oben nichts.**

    Sie zeigt, dass die Regel die Ursache ist - und nicht irgendetwas anderes
    am Weg, das den Wunsch ohnehin nie geschlossen haette.
    """
    eltern, wunsch_id, _ = _kind_mit_wunsch(arr_client)

    antwort = arr_client.post(f"/api/children/wishes/{wunsch_id}/release", json={}, headers=eltern)
    assert antwort.status_code in (200, 201), antwort.text

    with SessionLocal() as db:
        wunsch = db.get(ChildWish, wunsch_id)
        assert wunsch is not None
        assert wunsch.state == WishState.released
        assert wunsch.request_id is not None


def test_eine_freigebende_regel_stoert_den_wunsch_nicht(arr_client: TestClient) -> None:
    """Nur die Ablehnung ist der Sonderfall. Eine Freigabe laeuft durch."""
    eltern, wunsch_id, _ = _kind_mit_wunsch(arr_client)
    _regel(RegelEntscheidung.freigeben)

    antwort = arr_client.post(f"/api/children/wishes/{wunsch_id}/release", json={}, headers=eltern)
    assert antwort.status_code in (200, 201), antwort.text

    with SessionLocal() as db:
        assert db.get(ChildWish, wunsch_id).state == WishState.released
