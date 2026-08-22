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

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict[int, MovieEntry]:
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

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict[int, MovieEntry]:
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

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict[int, MovieEntry]:
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

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict[int, MovieEntry]:
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

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict[int, MovieEntry]:
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
        jahr = int((request.release_date or "2020")[:4])

    eintrag = SeriesEntry(
        arr_id=7,
        has_file=True,
        monitored=True,
        episode_file_count=10,
        episode_count=10,
        title_key="".join(c for c in titel.casefold() if c.isalnum()),
        # Sonarr liefert das Jahr immer mit. Ohne es greift der Titelabgleich
        # bewusst nicht mehr - siehe test_serien_matching.py (Issue #1).
        year=jahr,
    )

    async def bibliothek(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
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


@pytest.mark.asyncio
async def test_verschwundene_datei_setzt_auf_geloescht(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aus "geladen" wird "wieder gelöscht", wenn die Datei nirgends mehr liegt.

    Der Fall aus der Praxis: Ein Film wird nach dem Test wieder aus Radarr
    entfernt - samt Datei. Vorher blieb die Anfrage fuer immer auf "Bereits
    geladen" stehen; das war ab da eine falsche Behauptung, und der Titel
    liess sich nie wieder anfragen.
    """
    request = _laufende_anfrage(arr_client, RequestStatus.downloaded)

    async def leere_bibliothek(_settings: object, _tier: str = "standard") -> dict:
        return {}

    monkeypatch.setattr(library, "movie_library", leere_bibliothek)

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    with SessionLocal() as session:
        aktualisiert = session.query(MediaRequest).one()
        assert aktualisiert.status == RequestStatus.deleted

    # Und der Titel ist wieder anfragbar - "deleted" blockiert keine neue
    # Anfrage.
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": request.tmdb_id,
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    )
    assert antwort.status_code == 201, antwort.json()


@pytest.mark.asyncio
async def test_plex_kopie_haelt_den_titel_auf_geladen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nur aus Radarr entfernt, aber noch in Plex - dann bleibt "geladen" wahr."""
    from app.models import MediaServerLibraryItem, MediaType

    request = _laufende_anfrage(arr_client, RequestStatus.downloaded)

    async def leere_bibliothek(_settings: object, _tier: str = "standard") -> dict:
        return {}

    monkeypatch.setattr(library, "movie_library", leere_bibliothek)

    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider="plex",
                media_type=MediaType.movie,
                guid=f"plex://film/{request.tmdb_id}",
                title=request.title,
                title_key=request.title.lower(),
                tmdb_id=request.tmdb_id,
                year=int((request.release_date or "2000")[:4]),
            )
        )
        session.commit()

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    with SessionLocal() as session:
        aktualisiert = session.query(MediaRequest).one()
        assert aktualisiert.status == RequestStatus.downloaded
        session.query(MediaServerLibraryItem).delete()
        session.commit()


async def test_gescheiterter_abgleich_vergiftet_die_sitzung_nicht(
    arr_client, monkeypatch
) -> None:
    """Ein Fehler beim Bibliotheks-Abgleich darf den Mailversand nicht mitreissen.

    Im Protokoll einer echten Installation stand hinter jedem
    "Media-Server konnte nicht abgeglichen werden" prompt ein
    "Status-Abgleich fehlgeschlagen: PendingRollbackError" - die Sitzung
    blieb nach dem gescheiterten Schreibvorgang im Rollback-Zustand, und der
    Mailversand im selben Durchgang starb an einem fremden Fehler. In der
    Folge ging stundenlang keine Benachrichtigung mehr raus.
    """
    from app.db import SessionLocal
    from app.models import Setting
    from app.services import mediaserver_library, status_poller
    from app.services.settings_service import load_settings

    async def kaputt(db, settings):  # noqa: ANN001
        # So sieht ein Abbruch mitten im Schreiben aus.
        db.add(Setting(key="kaputt-test", value="x"))
        db.flush()
        raise RuntimeError("Abgleich abgebrochen")

    monkeypatch.setattr(mediaserver_library, "refresh", kaputt)
    monkeypatch.setattr(status_poller, "_bibliothek_zuletzt", 0.0)

    with SessionLocal() as db:
        db.add(Setting(key="mediaserver_provider", value="plex"))
        db.add(Setting(key="mediaserver_machine_id", value="m1"))
        db.add(Setting(key="mediaserver_token", value="t"))
        db.commit()
        settings = load_settings(db)

        await status_poller._bibliothek_vielleicht(db, settings)

        # Entscheidend: Die Sitzung ist danach wieder benutzbar.
        assert db.query(Setting).count() >= 0

# --- Verschwundene Titel ----------------------------------------------------


def _alt_genug(request_id: int) -> None:
    """Die Anfrage aus der Schonfrist herausdatieren."""
    from datetime import timedelta

    from app.models import utcnow

    with SessionLocal() as session:
        request = session.get(MediaRequest, request_id)
        request.approved_at = utcnow() - timedelta(minutes=60)
        session.commit()


@pytest.mark.asyncio
async def test_aus_radarr_entfernt_bricht_die_anfrage_ab(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Sonst wartet jemand auf etwas, das nie kommt.

    Wer einen Titel direkt in Radarr oder Sonarr entfernt, hatte in Nexview
    weiterhin "wird gesucht" stehen - fuer immer. Das Kontingent blieb
    belastet, und ``find_active`` sperrte den Titel fuer **alle anderen** mit.
    """
    from app.models import Notification, NotificationType

    anfrage = _laufende_anfrage(arr_client, RequestStatus.searching)
    _alt_genug(anfrage.id)

    async def leer(_settings, _tier="standard"):
        return {}

    monkeypatch.setattr(library, "movie_library", leer)

    with SessionLocal() as session:
        await status_poller.check_once(session, load_settings(session))

    with SessionLocal() as session:
        assert session.get(MediaRequest, anfrage.id).status == RequestStatus.cancelled
        arten = [n.type for n in session.query(Notification).all()]
        assert NotificationType.cancelled in arten


@pytest.mark.asyncio
async def test_frisch_uebergebenes_wird_geschont(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **Die Schonfrist ist kein Luxus.**

    Die Bibliothek wird kurz zwischengespeichert. Ohne Frist bricht der
    naechste Durchgang genau die Anfrage ab, die gerade erst uebergeben wurde -
    sie steht in der alten Antwort noch nicht drin.
    """
    anfrage = _laufende_anfrage(arr_client, RequestStatus.searching)

    async def leer(_settings, _tier="standard"):
        return {}

    monkeypatch.setattr(library, "movie_library", leer)

    with SessionLocal() as session:
        await status_poller.check_once(session, load_settings(session))

    with SessionLocal() as session:
        assert session.get(MediaRequest, anfrage.id).status == RequestStatus.searching


@pytest.mark.asyncio
async def test_ohne_antwort_der_instanz_wird_nichts_abgebrochen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Ausfall darf kein Grund sein, reihenweise Anfragen abzubrechen."""
    from app.services.settings_service import save_settings

    anfrage = _laufende_anfrage(arr_client, RequestStatus.searching)
    _alt_genug(anfrage.id)

    with SessionLocal() as session:
        # Radarr gar nicht eingerichtet - dann gibt es keine Quelle, die "weg"
        # sagen koennte.
        save_settings(session, {"radarr_url": "", "radarr_api_key": ""})
        await status_poller.check_once(session, load_settings(session))

    with SessionLocal() as session:
        assert session.get(MediaRequest, anfrage.id).status == RequestStatus.searching


@pytest.mark.asyncio
async def test_wartende_freigabe_bleibt_unangetastet(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine Anfrage vor der Freigabe steht naturgemaess in keiner Bibliothek."""
    anfrage = _laufende_anfrage(arr_client, RequestStatus.pending_approval)
    _alt_genug(anfrage.id)

    async def leer(_settings, _tier="standard"):
        return {}

    monkeypatch.setattr(library, "movie_library", leer)

    with SessionLocal() as session:
        await status_poller.check_once(session, load_settings(session))

    with SessionLocal() as session:
        assert (
            session.get(MediaRequest, anfrage.id).status
            == RequestStatus.pending_approval
        )
