"""Die Aufwertungs-Meldung kommt sofort, nicht erst zur vollen Stunde.

Die Meldung selbst ("dein Posten ist gewachsen", ``storage_grew``) gibt es
laengst - sie haengt am stuendlichen Speicher-Abgleich samt Schwelle und
Staffel-Schutzregel. Neu ist nur die Kette davor: Der Status-Rundgang sieht
die frische Groesse ohnehin (dieselbe Antwort, die "noch da" beantwortet),
erkennt spuerbaren Zuwachs und zieht den Abgleich vor. Der Upgrade-Anruf
weckt den Rundgang - damit ist die Meldung Sekunden nach der Aufwertung da.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, QualityTier, RequestStatus, StorageEntry
from app.services import library, status_poller, storage
from app.services.radarr import LibraryEntry as MovieEntry
from app.services.settings_service import load_settings

from .conftest import auth_headers, create_user

GIB = 1024**3


def _film_anfrage(tmdb_id: int = 603) -> SimpleNamespace:
    return SimpleNamespace(
        media_type=MediaType.movie,
        tier=QualityTier.standard,
        tmdb_id=tmdb_id,
        tvdb_id=None,
        season=None,
    )


def _posten(size_bytes: int, tmdb_id: int = 603) -> None:
    with SessionLocal() as db:
        db.add(
            StorageEntry(
                key=storage.schluessel(MediaType.movie, QualityTier.standard, tmdb_id=tmdb_id),
                media_type=MediaType.movie,
                tmdb_id=tmdb_id,
                title="Matrix",
                size_bytes=size_bytes,
            )
        )
        db.commit()


def test_ohne_posten_kein_zuwachs() -> None:
    with SessionLocal() as db:
        assert storage.spuerbar_zugelegt(db, _film_anfrage(), 50 * GIB) is False


def test_kleiner_zuwachs_bleibt_unter_der_schwelle() -> None:
    _posten(5 * GIB)
    with SessionLocal() as db:
        assert storage.spuerbar_zugelegt(db, _film_anfrage(), 5 * GIB + 200_000_000) is False


def test_aufwertung_liegt_ueber_der_schwelle() -> None:
    _posten(5 * GIB)
    with SessionLocal() as db:
        assert storage.spuerbar_zugelegt(db, _film_anfrage(), 50 * GIB) is True


def test_groesse_null_ist_kein_zuwachs() -> None:
    _posten(5 * GIB)
    with SessionLocal() as db:
        assert storage.spuerbar_zugelegt(db, _film_anfrage(), 0) is False


@pytest.mark.asyncio
async def test_rundgang_zieht_den_speicher_abgleich_vor(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = arr_client.get("/api/discover/movie").json()["items"][0]
    arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    )
    with SessionLocal() as session:
        request = session.query(MediaRequest).one()
        request.status = RequestStatus.downloaded
        session.commit()
        tmdb_id = request.tmdb_id
    _posten(5 * GIB, tmdb_id)

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict[int, MovieEntry]:
        return {
            tmdb_id: MovieEntry(
                arr_id=4242, has_file=True, monitored=True, size_bytes=50 * GIB
            )
        }

    monkeypatch.setattr(library, "movie_library", bibliothek)
    # Als waere gerade erst abgeglichen worden - nur der Zuwachs darf es
    # wieder faellig machen.
    monkeypatch.setattr(status_poller, "_speicher_zuletzt", time.monotonic())

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    assert status_poller._speicher_zuletzt == 0.0, (
        "Der erkannte Zuwachs muss den Speicher-Abgleich sofort faellig machen"
    )
