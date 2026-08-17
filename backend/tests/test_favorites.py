"""Favoriten und die daraus kuratierten Empfehlungen."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import media
from tests.conftest import auth_headers, create_user

FILM = {"media_type": "movie", "tmdb_id": 603, "title": "Matrix", "poster_url": None}


# --- Markieren ---------------------------------------------------------------


def test_ohne_favoriten_ist_die_liste_leer(admin_client: TestClient) -> None:
    assert admin_client.get("/api/favorites").json() == []


def test_markieren_und_wieder_entfernen(admin_client: TestClient) -> None:
    antwort = admin_client.post("/api/favorites", json=FILM)
    assert antwort.status_code == 201
    assert antwort.json()["tmdb_id"] == 603

    assert len(admin_client.get("/api/favorites").json()) == 1

    assert admin_client.delete("/api/favorites/movie/603").status_code == 204
    assert admin_client.get("/api/favorites").json() == []


def test_zweimal_markieren_legt_nichts_doppelt_an(admin_client: TestClient) -> None:
    """Ein zweiter Klick darf kein Fehler sein - und die Empfehlungen nicht
    doppelt gewichten."""
    admin_client.post("/api/favorites", json=FILM)
    zweite = admin_client.post("/api/favorites", json=FILM)
    assert zweite.status_code == 201
    assert len(admin_client.get("/api/favorites").json()) == 1


def test_entfernen_was_nicht_markiert_war(admin_client: TestClient) -> None:
    assert admin_client.delete("/api/favorites/movie/999999").status_code == 404


def test_favoriten_sind_privat(admin_client: TestClient) -> None:
    """Der wichtigste Test: Geschmack ist nichts, was andere angeht."""
    admin_client.post("/api/favorites", json=FILM)

    create_user(admin_client, "lena")
    kopf = auth_headers(admin_client, "lena", "passwort-1234")
    assert admin_client.get("/api/favorites", headers=kopf).json() == []

    # Und lenas Markierung taucht beim Admin nicht auf.
    admin_client.post(
        "/api/favorites",
        json={"media_type": "movie", "tmdb_id": 27205, "title": "Inception", "poster_url": None},
        headers=kopf,
    )
    eigene = [f["tmdb_id"] for f in admin_client.get("/api/favorites").json()]
    assert eigene == [603]


def test_ohne_anmeldung_gesperrt(client: TestClient) -> None:
    assert client.get("/api/favorites").status_code == 401
    assert client.post("/api/favorites", json=FILM).status_code == 401


# --- Kuratierte Empfehlungen -------------------------------------------------


def test_ohne_favoriten_kommt_der_hinweis(admin_client: TestClient) -> None:
    """Die Oberflaeche muss "noch nichts markiert" von "nichts gefunden"
    unterscheiden koennen - sonst kann sie nicht sagen, was zu tun ist."""
    daten = admin_client.get("/api/home/curated").json()
    assert daten["has_favorites"] is False
    assert daten["items"] == []


async def test_empfehlungen_zaehlen_haeufigkeit(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was bei mehreren Favoriten auftaucht, steht vorn.

    Genau das ist der Kern der Kuratierung: ein einzelner Treffer ist Zufall,
    ein mehrfacher eine Aussage.
    """

    empfehlungen = {
        1: [{"id": 100, "popularity": 5}, {"id": 200, "popularity": 90}],
        2: [{"id": 100, "popularity": 5}, {"id": 300, "popularity": 80}],
        3: [{"id": 100, "popularity": 5}],
    }

    class Attrappe:
        async def recommendations(self, _media_type: str, tmdb_id: int) -> dict:
            return {"results": empfehlungen.get(tmdb_id, [])}

    monkeypatch.setattr(media, "_client", lambda *_a, **_k: Attrappe())

    async def durchreichen(_db, _settings, _media_type, ergebnisse, _region):
        from app.models import MediaType
        from app.schemas_media import MediaItem

        return [
            MediaItem(media_type=MediaType.movie, tmdb_id=e["id"], title=f"Film {e['id']}")
            for e in ergebnisse
        ]

    monkeypatch.setattr(media, "_to_items", durchreichen)

    from app.db import SessionLocal
    from app.services.settings_service import load_settings

    with SessionLocal() as db:
        settings = load_settings(db)
        # Ein TMDB-Key, damit nicht auf Demo-Daten umgeschaltet wird.
        settings = settings.__class__(**{**settings.__dict__, "tmdb_api_key": "x", "demo_mode": "off"})
        ergebnis = await media.curated(db, settings, "movie", [1, 2, 3])

    kennungen = [item.tmdb_id for item in ergebnis]
    # 100 kommt dreimal vor und steht deshalb vorn - trotz geringster Beliebtheit.
    assert kennungen[0] == 100
    # Die Favoriten selbst tauchen nicht als Empfehlung auf.
    assert not ({1, 2, 3} & set(kennungen))


async def test_favoriten_selbst_werden_nicht_empfohlen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Attrappe:
        async def recommendations(self, _media_type: str, _tmdb_id: int) -> dict:
            # Empfiehlt ausgerechnet den anderen Favoriten.
            return {"results": [{"id": 7, "popularity": 1}, {"id": 9, "popularity": 1}]}

    monkeypatch.setattr(media, "_client", lambda *_a, **_k: Attrappe())

    from app.db import SessionLocal
    from app.services.settings_service import load_settings

    with SessionLocal() as db:
        settings = load_settings(db)
        settings = settings.__class__(**{**settings.__dict__, "tmdb_api_key": "x", "demo_mode": "off"})
        ergebnis = await media.curated(db, settings, "movie", [7, 9])

    assert ergebnis == []
