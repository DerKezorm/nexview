"""Freigeben und Ablehnen von Anfragen."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus, User

from .conftest import auth_headers, create_user


def _anfrage_von_kim(client: TestClient) -> tuple[dict, dict]:
    """Legt kim an und stellt eine Anfrage, die auf Freigabe wartet."""
    create_user(client, "kim")
    headers = auth_headers(client, "kim", "passwort-1234")
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
    return angelegt, headers


def test_uebersicht_zeigt_wer_angefragt_hat(arr_client: TestClient) -> None:
    _anfrage_von_kim(arr_client)

    alle = arr_client.get("/api/admin/requests").json()
    assert len(alle) == 1
    assert alle[0]["username"] == "kim"
    assert alle[0]["status"] == "pending_approval"


def test_uebersicht_laesst_sich_filtern(arr_client: TestClient) -> None:
    _anfrage_von_kim(arr_client)

    assert len(arr_client.get("/api/admin/requests?status=pending_approval").json()) == 1
    assert arr_client.get("/api/admin/requests?status=downloaded").json() == []


def test_offene_anzahl(arr_client: TestClient) -> None:
    assert arr_client.get("/api/admin/requests/pending/count").json() == {"pending": 0}
    _anfrage_von_kim(arr_client)
    assert arr_client.get("/api/admin/requests/pending/count").json() == {"pending": 1}


def test_ablehnen_mit_begruendung(arr_client: TestClient) -> None:
    angelegt, kim = _anfrage_von_kim(arr_client)

    response = arr_client.post(
        f"/api/admin/requests/{angelegt['id']}/reject",
        json={"reason": "Haben wir schon auf DVD."},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["rejection_reason"] == "Haben wir schon auf DVD."

    # Der Anfragende sieht die Ablehnung samt Grund ...
    meine = arr_client.get("/api/requests/mine", headers=kim).json()
    assert meine[0]["status"] == "rejected"
    assert meine[0]["rejection_reason"] == "Haben wir schon auf DVD."

    # ... und wird benachrichtigt.
    with SessionLocal() as session:
        kim_user = session.query(User).filter(User.username == "kim").one()
        arten = [n.type.value for n in kim_user.notifications]
    assert "rejected" in arten


def test_ablehnen_ohne_begruendung(arr_client: TestClient) -> None:
    angelegt, _ = _anfrage_von_kim(arr_client)
    response = arr_client.post(f"/api/admin/requests/{angelegt['id']}/reject", json={})
    assert response.status_code == 200
    assert response.json()["rejection_reason"] is None


def test_abgelehnte_anfrage_gibt_den_titel_wieder_frei(arr_client: TestClient) -> None:
    """Nach einer Ablehnung soll der Titel erneut angefragt werden können."""
    angelegt, kim = _anfrage_von_kim(arr_client)
    arr_client.post(f"/api/admin/requests/{angelegt['id']}/reject", json={})

    item = arr_client.get("/api/discover/movie").json()["items"][0]
    assert item["status"] == "not_requested"

    erneut = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=kim,
    )
    assert erneut.status_code == 201


def test_zweimal_ablehnen_geht_nicht(arr_client: TestClient) -> None:
    angelegt, _ = _anfrage_von_kim(arr_client)
    arr_client.post(f"/api/admin/requests/{angelegt['id']}/reject", json={})
    zweite = arr_client.post(f"/api/admin/requests/{angelegt['id']}/reject", json={})
    assert zweite.status_code == 409


def test_freigeben_bei_nicht_erreichbarem_radarr(arr_client: TestClient) -> None:
    """Radarr ist eingetragen, antwortet aber nicht - die Freigabe muss das
    verständlich melden und die Anfrage als fehlgeschlagen markieren."""
    angelegt, _ = _anfrage_von_kim(arr_client)

    response = arr_client.post(f"/api/admin/requests/{angelegt['id']}/approve")
    assert response.status_code == 502
    assert "Radarr" in response.json()["detail"]

    with SessionLocal() as session:
        request = session.query(MediaRequest).one()
        assert request.status == RequestStatus.failed
        assert request.error_message


def test_unbekannte_anfrage(arr_client: TestClient) -> None:
    assert arr_client.post("/api/admin/requests/999/approve").status_code == 404
    assert arr_client.post("/api/admin/requests/999/reject", json={}).status_code == 404


def test_eintrag_entfernen(arr_client: TestClient) -> None:
    angelegt, _ = _anfrage_von_kim(arr_client)
    assert arr_client.delete(f"/api/admin/requests/{angelegt['id']}").status_code == 204
    assert arr_client.get("/api/admin/requests").json() == []


def test_normaler_benutzer_kommt_nicht_an_die_freigaben(arr_client: TestClient) -> None:
    angelegt, kim = _anfrage_von_kim(arr_client)

    assert arr_client.get("/api/admin/requests", headers=kim).status_code == 403
    assert (
        arr_client.post(f"/api/admin/requests/{angelegt['id']}/approve", headers=kim).status_code
        == 403
    )
    assert (
        arr_client.post(
            f"/api/admin/requests/{angelegt['id']}/reject", json={}, headers=kim
        ).status_code
        == 403
    )


def test_jeder_zustand_ist_als_filter_erlaubt(admin_client: TestClient) -> None:
    """Was es als Zustand gibt, muss sich auch filtern lassen.

    Der Fehler dahinter: Die erlaubten Werte des Filters standen als
    abgeschriebene Liste im Endpunkt. Als "deleted" dazukam, war der
    Filterknopf in der Oberflaeche da, die Abfrage lieferte aber 422 - und die
    Seite blieb fuer immer in "Wird geladen ..." haengen, ohne Fehlermeldung.

    Der Test geht deshalb ueber das Enum und nicht ueber eine zweite Liste.
    """
    from app.models import RequestStatus

    for zustand in RequestStatus:
        antwort = admin_client.get(f"/api/admin/requests?status={zustand.value}")
        assert antwort.status_code == 200, (
            f"Zustand {zustand.value!r} wird vom Filter nicht angenommen "
            f"(HTTP {antwort.status_code})"
        )


def test_freigabe_verknuepft_einen_film_der_schon_in_radarr_liegt(
    arr_client: TestClient, monkeypatch
) -> None:
    """Kein zweites Anlegen - und vor allem kein Fehlschlag.

    Der Fall aus dem Betrieb: Waehrend die Anfrage auf Freigabe wartete, kam
    der Film ueber eine zweite Instanz ins Haus. Radarr antwortet auf ein
    zweites Anlegen mit einem gewoehnlichen 400er, und die Anfrage landete auf
    "fehlgeschlagen" - danach ohne einen einzigen Knopf in der Liste.

    Bei Serien wurde seit jeher vorher nachgesehen (``_sonarr_eintrag``); bei
    Filmen fehlte genau das.
    """
    from app.services import library
    from app.services.radarr import LibraryEntry

    angelegt, _ = _anfrage_von_kim(arr_client)

    async def bestand(*_args, **_kwargs):
        return {angelegt["tmdb_id"]: LibraryEntry(arr_id=815, has_file=True, monitored=True)}

    monkeypatch.setattr(library, "movie_library", bestand)

    # ⚠️ Die Radarr-Attrappe dieser Testreihe antwortet nicht. Ohne die
    # Vorabpruefung ginge der Auftrag also hinaus und die Freigabe endete mit
    # 502 auf "fehlgeschlagen" - genau das prueft
    # ``test_freigeben_bei_nicht_erreichbarem_radarr`` eine Ebene weiter oben.
    # Dass hier stattdessen 200 herauskommt, ist der Beweis.
    antwort = arr_client.post(f"/api/admin/requests/{angelegt['id']}/approve")
    assert antwort.status_code == 200, antwort.text

    with SessionLocal() as session:
        request = session.query(MediaRequest).one()
        assert request.status == RequestStatus.searching
        # Die vorhandene Radarr-Nummer, nicht eine neu angelegte.
        assert request.arr_id == 815
        assert request.error_message is None
