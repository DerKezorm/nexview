"""Personen-Übersicht und -Suche, nach Fach gefiltert.

Der Kern ist die Fach-Filterung: die drei Knöpfe oben (Schauspiel, Regie,
Drehbuch) sollen genau das eingrenzen. TMDB wird untergeschoben - wie viele
Regisseure es kennt, ist keine Eigenschaft dieses Codes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.routers import details
from app.services.settings_service import save_settings
from tests.conftest import auth_headers, create_user

PFAD = "/api/people"


class FakeClient:
    """Ersetzt den TMDB-Client mit fester, überschaubarer Antwort."""

    async def popular_people(self, page: int = 1) -> dict[str, Any]:
        return {
            "total_pages": 3,
            "page": page,
            "results": [
                {"id": 1, "name": "Schauspieler A", "known_for_department": "Acting",
                 "known_for": [{"title": "Film A"}, {"name": "Serie A"}]},
                {"id": 2, "name": "Regie A", "known_for_department": "Directing", "known_for": []},
                {"id": 1, "name": "Schauspieler A", "known_for_department": "Acting", "known_for": []},
            ]
        }

    async def search_person(self, query: str, page: int = 1) -> dict[str, Any]:
        return {
            "total_pages": 1,
            "page": page,
            "results": [
                {"id": 10, "name": "Chris Nolan", "known_for_department": "Directing",
                 "known_for": [{"title": "Inception"}], "popularity": 50},
                {"id": 11, "name": "Nur Schauspieler", "known_for_department": "Acting",
                 "known_for": [], "popularity": 5},
            ]
        }


@pytest.fixture
def nutzer(admin_client: TestClient) -> dict[str, str]:
    create_user(admin_client, "lena")
    return auth_headers(admin_client, "lena", "passwort-1234")


@pytest.fixture
def mit_tmdb(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """TMDB als eingerichtet ausgeben und den Client fälschen."""
    with SessionLocal() as db:
        save_settings(db, {"tmdb_api_key": "test-schluessel"})
    monkeypatch.setattr(details.media, "_client", lambda settings, region=None: FakeClient())


def test_uebersicht_zeigt_nur_das_gewaehlte_fach(
    admin_client: TestClient, nutzer: dict[str, str], mit_tmdb
) -> None:
    daten = admin_client.get(f"{PFAD}?department=acting", headers=nutzer).json()
    kennungen = [p["person_id"] for p in daten["items"]]
    assert 1 in kennungen  # Schauspieler A
    assert 2 not in kennungen  # Regie A gehört nicht ins Fach
    # Doppelter Eintrag (kommt bei TMDB vor) wird entdoppelt.
    assert kennungen.count(1) == 1
    # known_for wird als Wiedererkennung mitgegeben.
    assert daten["items"][0]["known_for"] == "Film A, Serie A"
    # Es gibt weitere Seiten (total_pages=3) -> Knopf "Mehr laden".
    assert daten["has_more"] is True


def test_suche_filtert_auf_das_fach(
    admin_client: TestClient, nutzer: dict[str, str], mit_tmdb
) -> None:
    daten = admin_client.get(f"{PFAD}?department=directing&q=nolan", headers=nutzer).json()
    namen = [p["name"] for p in daten["items"]]
    assert namen == ["Chris Nolan"]  # der Schauspieler faellt raus
    assert daten["has_more"] is False  # nur eine Suchseite


def test_unbekanntes_fach_wird_abgelehnt(
    admin_client: TestClient, nutzer: dict[str, str], mit_tmdb
) -> None:
    assert admin_client.get(f"{PFAD}?department=kamera", headers=nutzer).status_code == 422


def test_ohne_tmdb_leer_statt_fehler(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Im Demo-Modus (kein TMDB-Schlüssel) gibt es keine Personendaten."""
    antwort = admin_client.get(PFAD, headers=nutzer)
    assert antwort.status_code == 200
    assert antwort.json() == {"items": [], "has_more": False}


def test_nur_fuer_angemeldete(client: TestClient) -> None:
    assert client.get(PFAD, headers={"Authorization": ""}).status_code == 401
