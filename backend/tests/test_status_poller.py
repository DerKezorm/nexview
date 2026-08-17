"""Status-Verfolgung: aus "wird gesucht" wird "geladen"."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus, User
from app.services import library, status_poller
from app.services.radarr import LibraryEntry as MovieEntry
from app.services.settings_service import load_settings
from app.services.sonarr import LibraryEntry as SeriesEntry

from .conftest import auth_headers, create_user


def _laufende_anfrage(client: TestClient, status: RequestStatus) -> MediaRequest:
    """Legt kim an, stellt eine Anfrage und setzt sie auf den gewünschten Zustand."""
    create_user(client, "kim")
    headers = auth_headers(client, "kim", "passwort-1234")
    item = client.get("/api/discover/movie").json()["items"][0]
    client.post(
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
        request.status = status
        request.arr_id = 4242
        session.commit()
        session.refresh(request)
        return request


@pytest.mark.asyncio
async def test_fertiger_film_wird_als_geladen_markiert(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _laufende_anfrage(arr_client, RequestStatus.searching)

    async def bibliothek(_settings: object) -> dict[int, MovieEntry]:
        return {request.tmdb_id: MovieEntry(arr_id=4242, has_file=True, monitored=True)}

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        fertig = await status_poller.check_once(db, load_settings(db))

    assert fertig == 1
    with SessionLocal() as session:
        aktualisiert = session.query(MediaRequest).one()
        assert aktualisiert.status == RequestStatus.downloaded
        assert aktualisiert.completed_at is not None


@pytest.mark.asyncio
async def test_noch_nicht_fertig_bleibt_stehen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _laufende_anfrage(arr_client, RequestStatus.searching)

    async def bibliothek(_settings: object) -> dict[int, MovieEntry]:
        return {request.tmdb_id: MovieEntry(arr_id=4242, has_file=False, monitored=True)}

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 0

    with SessionLocal() as session:
        assert session.query(MediaRequest).one().status == RequestStatus.searching


@pytest.mark.asyncio
async def test_freigegebene_anfrage_wird_zu_wird_gesucht(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sobald der Titel in Radarr auftaucht, aber noch keine Datei hat."""
    request = _laufende_anfrage(arr_client, RequestStatus.approved)

    async def bibliothek(_settings: object) -> dict[int, MovieEntry]:
        return {request.tmdb_id: MovieEntry(arr_id=4242, has_file=False, monitored=True)}

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    with SessionLocal() as session:
        assert session.query(MediaRequest).one().status == RequestStatus.searching


@pytest.mark.asyncio
async def test_benachrichtigung_geht_an_den_anfragenden(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _laufende_anfrage(arr_client, RequestStatus.searching)

    async def bibliothek(_settings: object) -> dict[int, MovieEntry]:
        return {request.tmdb_id: MovieEntry(arr_id=4242, has_file=True, monitored=True)}

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    with SessionLocal() as session:
        kim = session.query(User).filter(User.username == "kim").one()
        fertig = [n for n in kim.notifications if n.type.value == "download_complete"]
        assert len(fertig) == 1
        assert fertig[0].message_title == request.title
        assert fertig[0].is_read is False

        # Der Admin bekommt keine Meldung über fremde Downloads.
        admin = session.query(User).filter(User.username == "admin").one()
        assert not [n for n in admin.notifications if n.type.value == "download_complete"]


@pytest.mark.asyncio
async def test_zweiter_durchlauf_meldet_nicht_erneut(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst käme bei jedem Durchlauf eine neue Benachrichtigung."""
    request = _laufende_anfrage(arr_client, RequestStatus.searching)

    async def bibliothek(_settings: object) -> dict[int, MovieEntry]:
        return {request.tmdb_id: MovieEntry(arr_id=4242, has_file=True, monitored=True)}

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        settings = load_settings(db)
        assert await status_poller.check_once(db, settings) == 1
        assert await status_poller.check_once(db, settings) == 0

    with SessionLocal() as session:
        kim = session.query(User).filter(User.username == "kim").one()
        assert len(kim.notifications) == 1


@pytest.mark.asyncio
async def test_serie_wird_ueber_den_titel_gefunden(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TMDB kennt für viele neue Serien keine TVDB-Kennung - dann muss der
    Titelabgleich greifen."""
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = arr_client.get("/api/discover/tv").json()["items"][0]
    arr_client.post(
        "/api/requests",
        json={
            "media_type": "tv",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/TV-Shows",
        },
        headers=headers,
    )

    with SessionLocal() as session:
        request = session.query(MediaRequest).one()
        request.status = RequestStatus.searching
        request.tvdb_id = None  # keine TVDB-Kennung vorhanden
        session.commit()
        titel = request.title

    eintrag = SeriesEntry(
        arr_id=7,
        has_file=True,
        monitored=True,
        episode_file_count=10,
        episode_count=10,
        title_key="".join(c for c in titel.casefold() if c.isalnum()),
    )

    async def bibliothek(_settings: object) -> tuple[dict, dict]:
        return {}, {eintrag.title_key: eintrag}

    monkeypatch.setattr(library, "series_library", bibliothek)

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 1

    with SessionLocal() as session:
        assert session.query(MediaRequest).one().status == RequestStatus.downloaded


@pytest.mark.asyncio
async def test_ohne_offene_anfragen_passiert_nichts(arr_client: TestClient) -> None:
    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 0
