"""Anfragen: Kontingent, Freigabe, Doppelungen.

Die Tests nutzen Demo-Daten und ein eingerichtetes, aber absichtlich nicht
erreichbares Radarr/Sonarr (siehe ``arr_client`` in conftest.py). Es geht also
garantiert nichts an eine echte Instanz.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, QuotaPeriod, RequestStatus, User
from app.services import quota

from .conftest import auth_headers, create_user


def _first_demo(client: TestClient, media_type: str = "movie", index: int = 0) -> dict:
    return client.get(f"/api/discover/{media_type}").json()["items"][index]


def _anfrage(client: TestClient, item: dict, headers: dict | None = None):
    return client.post(
        "/api/requests",
        json={
            "media_type": item["media_type"],
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    )


# --- Kontingent-Berechnung -------------------------------------------------


def test_zeitraum_beginn_tag() -> None:
    jetzt = datetime(2026, 8, 16, 14, 30, tzinfo=timezone.utc)
    assert quota.period_start(QuotaPeriod.day, jetzt) == datetime(2026, 8, 16)


def test_zeitraum_beginn_woche_ist_montag() -> None:
    # 16.08.2026 ist ein Sonntag -> Wochenbeginn ist Montag, der 10.
    jetzt = datetime(2026, 8, 16, 14, 30, tzinfo=timezone.utc)
    assert quota.period_start(QuotaPeriod.week, jetzt) == datetime(2026, 8, 10)


def test_zeitraum_beginn_monat() -> None:
    jetzt = datetime(2026, 8, 16, 14, 30, tzinfo=timezone.utc)
    assert quota.period_start(QuotaPeriod.month, jetzt) == datetime(2026, 8, 1)


@pytest.mark.parametrize(
    ("period", "start", "erwartet"),
    [
        (QuotaPeriod.day, datetime(2026, 8, 16), datetime(2026, 8, 17)),
        (QuotaPeriod.week, datetime(2026, 8, 10), datetime(2026, 8, 17)),
        (QuotaPeriod.month, datetime(2026, 12, 1), datetime(2027, 1, 1)),
    ],
)
def test_zeitraum_ende(period: QuotaPeriod, start: datetime, erwartet: datetime) -> None:
    assert quota.period_end(period, start) == erwartet


# --- Anfragen stellen ------------------------------------------------------


def test_anfrage_ohne_freigabe_wartet(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = _first_demo(arr_client)

    response = _anfrage(arr_client, item, headers)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["title"] == item["title"]


def test_admin_bekommt_benachrichtigung_ueber_wartende_anfrage(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    _anfrage(arr_client, _first_demo(arr_client), headers)

    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == "admin").one()
        assert len(admin.notifications) == 1
        assert admin.notifications[0].type.value == "request_pending"


def test_gleicher_titel_nicht_zweimal(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = _first_demo(arr_client)

    assert _anfrage(arr_client, item, headers).status_code == 201
    zweite = _anfrage(arr_client, item, headers)
    assert zweite.status_code == 409
    assert item["title"] in zweite.json()["detail"]


def test_auch_ein_anderer_benutzer_kann_nicht_doppelt_anfragen(arr_client: TestClient) -> None:
    for name in ("kim", "alex"):
        create_user(arr_client, name)
    item = _first_demo(arr_client)

    _anfrage(arr_client, item, auth_headers(arr_client, "kim", "passwort-1234"))
    zweite = _anfrage(arr_client, item, auth_headers(arr_client, "alex", "passwort-1234"))
    assert zweite.status_code == 409


def test_unbekannter_titel(arr_client: TestClient) -> None:
    response = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": 999999,
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
    )
    assert response.status_code in (404, 502)


def test_ohne_radarr_keine_filmanfrage(admin_client: TestClient) -> None:
    """Ohne eingerichtetes Radarr könnte aus der Anfrage nie etwas werden -
    das muss sofort gesagt werden, statt eine tote Anfrage anzulegen."""
    response = _anfrage(admin_client, _first_demo(admin_client, "movie"))
    assert response.status_code == 409
    assert "Radarr" in response.json()["detail"]
    # Es darf auch nichts gespeichert worden sein.
    assert admin_client.get("/api/requests/mine").json() == []


def test_ohne_sonarr_keine_serienanfrage(admin_client: TestClient) -> None:
    response = _anfrage(admin_client, _first_demo(admin_client, "tv"))
    assert response.status_code == 409
    assert "Sonarr" in response.json()["detail"]


def test_gesperrtes_qualitaetsprofil_wird_abgelehnt(arr_client: TestClient) -> None:
    created = create_user(arr_client, "kim")
    # Profil 1 sperren - genau damit wird angefragt.
    arr_client.patch(f"/api/users/{created['id']}", json={"blocked_movie_profiles": [1]})
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    antwort = _anfrage(arr_client, _first_demo(arr_client), headers)
    assert antwort.status_code == 403
    assert "Qualitätsprofil" in antwort.json()["detail"]


def test_alle_profile_gesperrt_wird_ignoriert(arr_client: TestClient) -> None:
    """Wer alles sperrt, hat es nicht so gemeint.

    Waeren wirklich alle Profile gesperrt, koennte dieser Benutzer gar nichts
    mehr anfragen - und zwar ohne zu erfahren, warum. Eine Sperrliste, die
    alles sperrt, wird deshalb ignoriert; mindestens ein Profil bleibt immer
    waehlbar.
    """
    created = create_user(arr_client, "kim")
    arr_client.patch(f"/api/users/{created['id']}", json={"blocked_movie_profiles": [1, 2]})
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    # Die Auswahl liefert weiterhin Profile aus.
    optionen = arr_client.get("/api/arr/movie/options", headers=headers).json()
    assert len(optionen["quality_profiles"]) >= 1

    assert _anfrage(arr_client, _first_demo(arr_client), headers).status_code == 201


def test_nicht_gesperrtes_profil_geht_durch(arr_client: TestClient) -> None:
    """Ein Haken sperrt nur genau dieses Profil - alle anderen bleiben frei."""
    created = create_user(arr_client, "kim")
    arr_client.patch(f"/api/users/{created['id']}", json={"blocked_movie_profiles": [2, 4]})
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    # Angefragt wird mit Profil 1 - das ist nicht gesperrt.
    assert _anfrage(arr_client, _first_demo(arr_client), headers).status_code == 201


def test_ohne_sperrliste_sind_alle_profile_erlaubt(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    assert _anfrage(arr_client, _first_demo(arr_client), headers).status_code == 201


def test_sperrliste_wird_gespeichert_und_zurueckgegeben(arr_client: TestClient) -> None:
    created = create_user(arr_client, "kim")
    assert created["blocked_movie_profiles"] == []

    aktualisiert = arr_client.patch(
        f"/api/users/{created['id']}",
        json={"blocked_movie_profiles": [4, 7], "blocked_series_profiles": [2]},
    ).json()
    assert aktualisiert["blocked_movie_profiles"] == [4, 7]
    assert aktualisiert["blocked_series_profiles"] == [2]

    # Und wieder aufheben.
    zurueck = arr_client.patch(
        f"/api/users/{created['id']}", json={"blocked_movie_profiles": []}
    ).json()
    assert zurueck["blocked_movie_profiles"] == []


# --- Kontingent im Einsatz -------------------------------------------------


def test_kontingent_wird_aufgebraucht(arr_client: TestClient) -> None:
    created = create_user(arr_client, "kim", quota_movies_limit=2)
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    for index in range(2):
        assert _anfrage(arr_client, _first_demo(arr_client, index=index), headers).status_code == 201

    dritte = _anfrage(arr_client, _first_demo(arr_client, index=2), headers)
    assert dritte.status_code == 429
    assert "Kontingent" in dritte.json()["detail"]

    stand = arr_client.get("/api/requests/quota", headers=headers).json()
    assert stand["movie"]["used"] == 2
    assert stand["movie"]["remaining"] == 0
    assert stand["movie"]["exhausted"] is True
    assert created["quota_movies_limit"] == 2


def test_filme_und_serien_haben_getrennte_kontingente(arr_client: TestClient) -> None:
    create_user(arr_client, "kim", quota_movies_limit=1, quota_series_limit=1)
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    assert _anfrage(arr_client, _first_demo(arr_client, "movie"), headers).status_code == 201
    # Filmkontingent ist voll ...
    assert _anfrage(arr_client, _first_demo(arr_client, "movie", 1), headers).status_code == 429
    # ... die Serie geht trotzdem.
    assert _anfrage(arr_client, _first_demo(arr_client, "tv"), headers).status_code == 201


def test_ohne_limit_unbegrenzt(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    for index in range(4):
        assert _anfrage(arr_client, _first_demo(arr_client, index=index), headers).status_code == 201

    stand = arr_client.get("/api/requests/quota", headers=headers).json()
    assert stand["movie"]["unlimited"] is True
    assert stand["movie"]["remaining"] is None


def test_abgelehnte_anfrage_zaehlt_nicht_gegen_das_kontingent(arr_client: TestClient) -> None:
    create_user(arr_client, "kim", quota_movies_limit=1)
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    _anfrage(arr_client, _first_demo(arr_client), headers)

    with SessionLocal() as session:
        request = session.query(MediaRequest).one()
        request.status = RequestStatus.rejected
        session.commit()

    stand = arr_client.get("/api/requests/quota", headers=headers).json()
    assert stand["movie"]["used"] == 0


def test_alte_anfragen_zaehlen_nicht_mehr(arr_client: TestClient) -> None:
    create_user(arr_client, "kim", quota_movies_limit=1, quota_period="day")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    _anfrage(arr_client, _first_demo(arr_client), headers)

    # Anfrage auf gestern zurückdatieren -> zählt nicht mehr für heute.
    with SessionLocal() as session:
        request = session.query(MediaRequest).one()
        request.requested_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        session.commit()

    stand = arr_client.get("/api/requests/quota", headers=headers).json()
    assert stand["movie"]["used"] == 0
    assert _anfrage(arr_client, _first_demo(arr_client, index=1), headers).status_code == 201


# --- Eigene Anfragen -------------------------------------------------------


def test_jeder_sieht_nur_seine_eigenen(arr_client: TestClient) -> None:
    for name in ("kim", "alex"):
        create_user(arr_client, name)

    kim = auth_headers(arr_client, "kim", "passwort-1234")
    alex = auth_headers(arr_client, "alex", "passwort-1234")
    _anfrage(arr_client, _first_demo(arr_client, index=0), kim)
    _anfrage(arr_client, _first_demo(arr_client, index=1), alex)

    assert len(arr_client.get("/api/requests/mine", headers=kim).json()) == 1
    assert len(arr_client.get("/api/requests/mine", headers=alex).json()) == 1
    assert arr_client.get("/api/requests/mine").json() == []


def test_offene_anfrage_zurueckziehen(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    angelegt = _anfrage(arr_client, _first_demo(arr_client), headers).json()

    assert arr_client.delete(f"/api/requests/{angelegt['id']}", headers=headers).status_code == 204
    assert arr_client.get("/api/requests/mine", headers=headers).json() == []


def test_fremde_anfrage_kann_nicht_zurueckgezogen_werden(arr_client: TestClient) -> None:
    for name in ("kim", "alex"):
        create_user(arr_client, name)
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    angelegt = _anfrage(arr_client, _first_demo(arr_client), kim).json()

    alex = auth_headers(arr_client, "alex", "passwort-1234")
    assert arr_client.delete(f"/api/requests/{angelegt['id']}", headers=alex).status_code == 404


def test_bereits_freigegebene_anfrage_bleibt(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    angelegt = _anfrage(arr_client, _first_demo(arr_client), headers).json()

    with SessionLocal() as session:
        request = session.query(MediaRequest).one()
        request.status = RequestStatus.searching
        session.commit()

    assert arr_client.delete(f"/api/requests/{angelegt['id']}", headers=headers).status_code == 409


def test_anfrage_ohne_anmeldung(client: TestClient) -> None:
    assert client.post("/api/requests", json={}).status_code == 401


# --- Badges auf den Kacheln ------------------------------------------------


def test_angefragter_titel_zeigt_das_auf_der_kachel(arr_client: TestClient) -> None:
    """Sonst sähe ein Titel, der auf Freigabe wartet, für alle wie
    "nicht angefragt" aus - und würde erneut angefragt werden."""
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = _first_demo(arr_client)

    assert item["status"] == "not_requested"
    _anfrage(arr_client, item, headers)

    # ... und zwar für jeden, nicht nur für den Anfragenden.
    for wer in (headers, None):
        neu = arr_client.get("/api/discover/movie", headers=wer).json()["items"]
        treffer = next(i for i in neu if i["tmdb_id"] == item["tmdb_id"])
        assert treffer["status"] == "pending_approval"

    detail = arr_client.get(f"/api/media/movie/{item['tmdb_id']}").json()
    assert detail["status"] == "pending_approval"


def test_zurueckgezogene_anfrage_verschwindet_vom_badge(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = _first_demo(arr_client)
    angelegt = _anfrage(arr_client, item, headers).json()

    arr_client.delete(f"/api/requests/{angelegt['id']}", headers=headers)

    neu = arr_client.get("/api/discover/movie").json()["items"]
    treffer = next(i for i in neu if i["tmdb_id"] == item["tmdb_id"])
    assert treffer["status"] == "not_requested"
