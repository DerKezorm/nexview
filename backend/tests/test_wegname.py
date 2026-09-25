"""Welche Instanz eine Anfrage beschafft, steht im Protokoll beim Namen (#idea-54).

Dort stand fest „Radarr“ oder „Sonarr“, auch im NEX-Betrieb (gemessen live am
25.09.2026: „Added tv 'Dr. House' … to Sonarr“). Den NEX-Fall prueft
``test_nex_schreiben.test_die_uebergabe_nennt_nexcrate_nicht_sonarr``; hier
der ARR-Betrieb mit einem eigenen Instanznamen.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType
from app.services import requests_service
from app.services.settings_service import load_settings


def test_im_arr_betrieb_steht_der_name_der_instanz(arr_client: TestClient) -> None:
    arr_client.put("/api/settings", json={"radarr_name": "Radarr FHD"})
    with SessionLocal() as db:
        settings = load_settings(db, frisch=True)
    film = MediaRequest(media_type=MediaType.movie, fassung_kennung="radarr-standard")
    serie = MediaRequest(media_type=MediaType.tv, fassung_kennung="sonarr-standard")

    assert requests_service.wegname(settings, film) == "Radarr FHD"
    assert requests_service.wegname(settings, serie) != "Radarr FHD"


def test_eine_unbekannte_fassung_faellt_auf_die_medienart_zurueck(arr_client: TestClient) -> None:
    """Mehrere Instanzen, keine passt: nicht willkuerlich die erste nehmen."""
    with SessionLocal() as db:
        settings = load_settings(db, frisch=True)
    serie = MediaRequest(media_type=MediaType.tv, fassung_kennung="gibt-es-nicht")

    assert requests_service.wegname(settings, serie) == "Sonarr"
