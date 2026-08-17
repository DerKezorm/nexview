"""Startseite: die zuletzt fertig geladenen Titel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus
from app.routers import home
from app.services import media

from .conftest import auth_headers, create_user


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


def _fertig(request_id: int, minuten_her: int = 0) -> None:
    with SessionLocal() as session:
        request = session.get(MediaRequest, request_id)
        assert request is not None
        request.status = RequestStatus.downloaded
        request.completed_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=minuten_her
        )
        session.commit()


def test_ohne_downloads_ist_die_liste_leer(admin_client: TestClient) -> None:
    assert admin_client.get("/api/home/recent").json() == []


def test_zeigt_geladene_titel(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _fertig(_anfrage(arr_client, kim))

    eintraege = arr_client.get("/api/home/recent").json()
    assert len(eintraege) == 1
    assert eintraege[0]["requested_by"] == "kim"
    assert eintraege[0]["title"]


def test_nur_fertige_titel(arr_client: TestClient) -> None:
    """Was noch wartet oder abgelehnt wurde, gehört nicht auf die Startseite."""
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _anfrage(arr_client, kim, 0)  # wartet auf Freigabe
    _fertig(_anfrage(arr_client, kim, 1))

    assert len(arr_client.get("/api/home/recent").json()) == 1


def test_neuester_zuerst(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    alt = _anfrage(arr_client, kim, 0)
    neu = _anfrage(arr_client, kim, 1)
    _fertig(alt, minuten_her=120)
    _fertig(neu, minuten_her=1)

    eintraege = arr_client.get("/api/home/recent").json()
    assert [e["request_id"] for e in eintraege] == [neu, alt]


def test_hoechstens_zwoelf_titel(arr_client: TestClient) -> None:
    """Die Demo-Daten reichen nicht so weit - die Anfragen kommen direkt in die
    Datenbank."""
    created = create_user(arr_client, "kim")

    with SessionLocal() as session:
        jetzt = datetime.now(timezone.utc).replace(tzinfo=None)
        for nummer in range(home.LIMIT + 2):
            session.add(
                MediaRequest(
                    user_id=created["id"],
                    media_type=MediaType.movie,
                    tmdb_id=900000 + nummer,
                    title=f"Testtitel {nummer}",
                    status=RequestStatus.downloaded,
                    completed_at=jetzt - timedelta(minutes=nummer),
                )
            )
        session.commit()

    assert len(arr_client.get("/api/home/recent").json()) == home.LIMIT


def test_alle_sehen_die_startseite(arr_client: TestClient) -> None:
    """Auch Titel, die jemand anderes angefragt hat."""
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _fertig(_anfrage(arr_client, kim))

    create_user(arr_client, "alex")
    alex = auth_headers(arr_client, "alex", "passwort-1234")

    eintraege = arr_client.get("/api/home/recent", headers=alex).json()
    assert len(eintraege) == 1
    assert eintraege[0]["requested_by"] == "kim"


def test_startseite_bleibt_stehen_wenn_tmdb_streikt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne TMDB fehlen Handlung und Hintergrundbild - der Titel bleibt."""
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _fertig(_anfrage(arr_client, kim))

    async def kaputt(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("TMDB nicht erreichbar")

    monkeypatch.setattr(media, "detail", kaputt)

    antwort = arr_client.get("/api/home/recent")
    assert antwort.status_code == 200
    eintraege = antwort.json()
    assert len(eintraege) == 1
    assert eintraege[0]["title"]
    assert eintraege[0]["backdrop_url"] is None


def test_ohne_anmeldung_keine_startseite(client: TestClient) -> None:
    assert client.get("/api/home/recent").status_code == 401


# --- Kuratierte Empfehlungen -------------------------------------------------


def _favorit(client: TestClient, media_type: str, tmdb_id: int, headers: dict):
    return client.post(
        "/api/favorites",
        json={"media_type": media_type, "tmdb_id": tmdb_id, "title": f"Test {tmdb_id}"},
        headers=headers,
    )


def test_ohne_favoriten_sagt_die_startseite_das_auch(arr_client: TestClient) -> None:
    daten = arr_client.get("/api/home/curated").json()
    assert daten["has_favorites"] is False
    assert daten["items"] == []


def test_serien_favoriten_zaehlen_genauso_wie_filme(
    client: TestClient, arr_client: TestClient
) -> None:
    """Der eigentliche Fehler: die Auskunft sah nur Filme an.

    Wer ausschliesslich Serien markiert hatte, bekam "noch nichts markiert" zu
    lesen - obwohl sein Herz an mehreren Serien hing. Das Herz gibt es an
    beidem, also muss auch beides zaehlen.
    """
    create_user(arr_client, "serienfan")
    headers = auth_headers(client, "serienfan", "passwort-1234")

    assert client.get("/api/home/curated", headers=headers).json()["has_favorites"] is False

    # Nur Serien markieren - kein einziger Film.
    assert _favorit(client, "tv", 1396, headers).status_code == 201
    assert _favorit(client, "tv", 1399, headers).status_code == 201

    daten = client.get("/api/home/curated", headers=headers).json()
    assert daten["has_favorites"] is True, "Serien-Favoriten müssen zählen"


def test_film_favoriten_zaehlen_weiterhin(client: TestClient, arr_client: TestClient) -> None:
    create_user(arr_client, "filmfan")
    headers = auth_headers(client, "filmfan", "passwort-1234")

    _favorit(client, "movie", 9800, headers)
    assert client.get("/api/home/curated", headers=headers).json()["has_favorites"] is True
