"""Der Poller muss die Stufen streng auseinanderhalten.

Das ist der teuerste Fehler des ganzen 4K-Umbaus: Würde eine 4K-Anfrage gegen
die 1080p-Bibliothek geprüft, setzte die dort liegende Datei sie auf „fertig",
und Nexview verschickte „Dein Film ist da" — für eine Datei, die es in 4K gar
nicht gibt. In der Oberfläche sähe alles richtig aus; auffallen würde es erst
beim Abspielen.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    Notification,
    NotificationType,
    QualityTier,
    RequestStatus,
)
from app.services import library, status_poller
from app.services.radarr import LibraryEntry as MovieEntry
from app.services.settings_service import load_settings, save_settings

from .conftest import auth_headers, create_user

# Die Attrappen laufen unter anderen Adressen als die Standard-Instanz - sonst
# greift die Prüfung "beide Stufen dürfen nicht dieselbe Adresse haben".
UHD_EINSTELLUNGEN = {
    "radarr_uhd_url": "http://127.0.0.1:10",
    "radarr_uhd_api_key": "test-radarr-4k",
    "sonarr_uhd_url": "http://127.0.0.1:10",
    "sonarr_uhd_api_key": "test-sonarr-4k",
}


@pytest.fixture
def mit_uhd(arr_client: TestClient) -> TestClient:
    """Beide Instanzen eingerichtet, kim darf 4K anfragen."""
    with SessionLocal() as db:
        save_settings(db, UHD_EINSTELLUNGEN)
    create_user(
        arr_client, "kim", can_request_uhd_movies=True, can_request_uhd_series=True
    )
    return arr_client


def _anfrage(client: TestClient, tier: str) -> int:
    """Eine laufende Anfrage dieser Stufe anlegen."""
    headers = auth_headers(client, "kim", "passwort-1234")
    item = client.get("/api/discover/movie").json()["items"][0]
    antwort = client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "tier": tier,
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    )
    assert antwort.status_code == 201, antwort.text
    kennung = antwort.json()["id"]

    with SessionLocal() as session:
        request = session.get(MediaRequest, kennung)
        request.status = RequestStatus.searching
        request.arr_id = 4242
        session.commit()
    return kennung


def _zustand(kennung: int) -> RequestStatus:
    with SessionLocal() as session:
        return session.get(MediaRequest, kennung).status


@pytest.mark.asyncio
async def test_1080p_datei_schliesst_keine_4k_anfrage_ab(
    mit_uhd: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Kern der Sache - hier hängt alles dran."""
    kennung = _anfrage(mit_uhd, "uhd")
    item = mit_uhd.get("/api/discover/movie").json()["items"][0]

    async def bibliothek(_settings: object, tier: str = "standard"):
        # Nur die Standard-Instanz hat die Datei. Die 4K-Instanz ist leer.
        if tier == "standard":
            return {item["tmdb_id"]: MovieEntry(arr_id=1, has_file=True, monitored=True)}
        return {}

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 0

    assert _zustand(kennung) == RequestStatus.searching
    with SessionLocal() as session:
        # Die Meldung "wartet auf Freigabe" an den Admin darf es geben - eine
        # "Dein Film ist da" auf keinen Fall.
        fertig_meldungen = (
            session.query(Notification)
            .filter(Notification.type == NotificationType.download_complete)
            .count()
        )
        assert fertig_meldungen == 0


@pytest.mark.asyncio
async def test_4k_datei_schliesst_die_4k_anfrage_ab(
    mit_uhd: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kennung = _anfrage(mit_uhd, "uhd")
    item = mit_uhd.get("/api/discover/movie").json()["items"][0]

    async def bibliothek(_settings: object, tier: str = "standard"):
        if tier == "uhd":
            return {item["tmdb_id"]: MovieEntry(arr_id=7, has_file=True, monitored=True)}
        return {}

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 1

    assert _zustand(kennung) == RequestStatus.downloaded


@pytest.mark.asyncio
async def test_ohne_4k_anfragen_wird_die_4k_bibliothek_nie_geholt(
    mit_uhd: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer kein 4K nutzt, zahlt keine einzige zusätzliche Abfrage."""
    _anfrage(mit_uhd, "standard")
    gefragte_stufen: list[str] = []

    async def bibliothek(_settings: object, tier: str = "standard"):
        gefragte_stufen.append(tier)
        return {}

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    assert gefragte_stufen == ["standard"]


@pytest.mark.asyncio
async def test_entfernte_4k_instanz_laesst_die_anfrage_stehen(
    mit_uhd: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nimmt der Admin die 4K-Instanz wieder heraus, bleibt die Anfrage hängen -
    sichtbar unter „Alle Anfragen" und dort abbrechbar. Das ist die richtige
    Ausfallrichtung: lieber wartend als fälschlich fertig."""
    kennung = _anfrage(mit_uhd, "uhd")
    with SessionLocal() as db:
        save_settings(db, {"radarr_uhd_url": "", "radarr_uhd_api_key": ""})

    async def bibliothek(_settings: object, tier: str = "standard"):
        raise AssertionError("ohne Instanz darf gar nicht gefragt werden")

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 0

    assert _zustand(kennung) == RequestStatus.searching


def test_beide_stufen_nebeneinander(mit_uhd: TestClient) -> None:
    """Derselbe Film in Standard *und* 4K - genau der Anwendungsfall."""
    standard = _anfrage(mit_uhd, "standard")
    uhd = _anfrage(mit_uhd, "uhd")

    with SessionLocal() as session:
        assert session.get(MediaRequest, standard).tier == QualityTier.standard
        assert session.get(MediaRequest, uhd).tier == QualityTier.uhd
