"""Was die unabhaengige Pruefung vom 14.11.2026 gefunden hat - festgehalten.

Jeder Test hier war vor der Reparatur rot (oder haette es sein muessen) und
nennt das Szenario, das der Pruefer beschrieben hat.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, Notification, NotificationType, RequestStatus, Role
from app.seerr_api.app import app as seerr_app
from app.services import media, requests_service

from .conftest import create_user
from .test_altersfilter_waechter import ERLAUBT, GESPERRT
from .test_seerr_api import kennung, schluessel
from .test_seerr_api_alter import _SeerrTmdb


@pytest.fixture
def seerr() -> TestClient:
    return TestClient(seerr_app)


def _erster_demo_titel(client: TestClient, art: str = "movie") -> dict:
    return client.get(f"/api/discover/{art}").json()["items"][0]


def test_user_id_haengt_an_der_handelnden_person_nicht_am_schluessel(
    arr_client: TestClient, seerr: TestClient
) -> None:
    """Befund 1: Litefin fragt mit Admin-Schluessel im Namen eines Jugendlichen.

    Schickt der ``userId: <admin>`` mit, darf daraus keine Anfrage des
    Administrators werden.
    """
    create_user(arr_client, "teen", age=12)
    titel = _erster_demo_titel(arr_client)
    antwort = seerr.post(
        "/api/v1/request",
        headers={**schluessel("admin"), "X-Api-User": str(kennung("teen"))},
        json={"mediaType": "movie", "mediaId": titel["tmdb_id"], "userId": kennung("admin")},
    )
    assert antwort.status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(MediaRequest)) is None


def test_keine_namen_und_keine_emby_kennung_als_jellyfin(admin_client: TestClient, seerr: TestClient) -> None:
    """Befund 3: Litefin faellt auf ``jellyfinUsername`` zurueck."""
    create_user(
        admin_client, "max", mediaserver_provider="emby", mediaserver_account_id="emby-1", mediaserver_username="max"
    )
    create_user(
        admin_client,
        "nina",
        mediaserver_provider="jellyfin",
        mediaserver_account_id="jf-nina-0002",
        mediaserver_username="nina",
    )
    liste = seerr.get("/api/v1/user?take=100", headers=schluessel("admin")).json()["results"]
    nach_id = {n["id"]: n for n in liste}
    assert all(n["jellyfinUsername"] is None for n in liste)
    assert nach_id[kennung("max")]["jellyfinUserId"] is None
    assert nach_id[kennung("nina")]["jellyfinUserId"] == "jf-nina-0002"


def _absage_bei_staffel(monkeypatch: pytest.MonkeyPatch, staffel: int, code: str, status: int) -> None:
    echt = requests_service.create_request

    async def vielleicht(db, settings, user, item, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if args[2] == staffel:
            raise requests_service.RequestError("Nein.", status, code=code)
        return await echt(db, settings, user, item, *args, **kwargs)

    monkeypatch.setattr(requests_service, "create_request", vielleicht)


def test_teilerfolg_bei_staffeln_wird_als_erfolg_gemeldet(
    arr_client: TestClient, seerr: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund 4: Staffel 1 ist angelegt, Staffel 2 scheitert am Kontingent."""
    create_user(arr_client, "olga")
    serie = _erster_demo_titel(arr_client, "tv")
    _absage_bei_staffel(monkeypatch, 2, "quota_exhausted", 429)

    antwort = seerr.post(
        "/api/v1/request",
        headers=schluessel("olga"),
        json={"mediaType": "tv", "mediaId": serie["tmdb_id"], "seasons": [1, 2]},
    )
    assert antwort.status_code == 201, antwort.text
    assert [s["seasonNumber"] for s in antwort.json()["seasons"]] == [1]


def test_nur_eine_echte_doppelung_ist_409(
    arr_client: TestClient, seerr: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund 5: "Fassung nicht bereit" ist bei Nexview 409, aber keine Doppelung."""
    titel = _erster_demo_titel(arr_client)

    async def nicht_bereit(*args, **kwargs):  # noqa: ANN002, ANN003
        raise requests_service.RequestError("Nicht bereit.", 409, code="fassung_not_ready")

    monkeypatch.setattr(requests_service, "create_request", nicht_bereit)
    antwort = seerr.post(
        "/api/v1/request", headers=schluessel("admin"), json={"mediaType": "movie", "mediaId": titel["tmdb_id"]}
    )
    assert antwort.status_code == 403
    assert "fassung_not_ready" in antwort.json()["message"]


def test_abbrechen_meldet_es_der_person(arr_client: TestClient, seerr: TestClient) -> None:
    """Befund 7: Wie in der Oberflaeche erfaehrt die anfragende Person den Abbruch."""
    create_user(arr_client, "paul")
    with SessionLocal() as db:
        zeile = MediaRequest(
            user_id=kennung("paul"),
            media_type=MediaType.movie,
            tmdb_id=4242,
            title="Ein Film",
            status=RequestStatus.searching,
            fassung_kennung="radarr-standard",
        )
        db.add(zeile)
        db.commit()
        nummer = zeile.id

    assert seerr.delete(f"/api/v1/request/{nummer}", headers=schluessel("admin")).status_code == 204
    with SessionLocal() as db:
        assert db.get(MediaRequest, nummer).status == RequestStatus.cancelled  # type: ignore[union-attr]
        meldung = db.scalar(select(Notification).where(Notification.user_id == kennung("paul")))
        assert meldung is not None
        assert meldung.type == NotificationType.cancelled


@pytest.fixture
def tmdb(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "TmdbClient", _SeerrTmdb)
    monkeypatch.setattr(media, "_client", lambda settings, region=None: _SeerrTmdb())
    assert admin_client.put("/api/settings", json={"tmdb_api_key": "test-key", "default_region": "DE"}).status_code == 200


@pytest.mark.usefixtures("tmdb")
def test_reihe_aus_lauter_gesperrten_filmen_gibt_es_nicht(
    admin_client: TestClient, seerr: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund 8: Name und Plakat der Reihe sind die ihrer Filme."""
    from .test_altersfilter_waechter import _roh

    async def nur_gesperrt(self, sammlung_id: int) -> dict:  # noqa: ANN001
        return {"id": sammlung_id, "name": "Reihe", "poster_path": "/reihe.jpg", "parts": [_roh(GESPERRT, "movie")]}

    monkeypatch.setattr(_SeerrTmdb, "collection", nur_gesperrt)
    create_user(admin_client, "kind12", age=12)
    assert seerr.get("/api/v1/collection/5", headers=schluessel("kind12")).status_code == 404
    assert seerr.get("/api/v1/collection/5", headers=schluessel("admin")).status_code == 200


@pytest.mark.usefixtures("tmdb")
def test_details_nennen_nur_die_eigenen_anfragen(admin_client: TestClient, seerr: TestClient) -> None:
    """Befund 2 (Teil): Litefin zeigt Details mit dem Admin-Schluessel jedem Fernseher."""
    create_user(admin_client, "quinn")
    with SessionLocal() as db:
        db.add(
            MediaRequest(
                user_id=kennung("quinn"),
                media_type=MediaType.movie,
                tmdb_id=ERLAUBT,
                title="Titel",
                status=RequestStatus.pending_approval,
                fassung_kennung="radarr-standard",
            )
        )
        db.commit()

    als_admin = seerr.get(f"/api/v1/movie/{ERLAUBT}", headers=schluessel("admin")).json()
    assert "quinn" not in str(als_admin)
    assert all(a["requestedBy"]["id"] != kennung("quinn") for a in als_admin.get("mediaInfo", {}).get("requests", []))

    als_quinn = seerr.get(
        f"/api/v1/movie/{ERLAUBT}", headers={**schluessel("admin"), "X-Api-User": str(kennung("quinn"))}
    ).json()
    assert [a["requestedBy"]["id"] for a in als_quinn["mediaInfo"]["requests"]] == [kennung("quinn")]


def test_entscheider_darf_nicht_im_namen_anderer(arr_client: TestClient, seerr: TestClient) -> None:
    """Gegenstueck zu Befund 1: auch ein Entscheider nicht - die Regel nennt Administratoren."""
    create_user(arr_client, "rita", role=Role.approver)
    create_user(arr_client, "sven")
    titel = _erster_demo_titel(arr_client)
    antwort = seerr.post(
        "/api/v1/request",
        headers=schluessel("rita"),
        json={"mediaType": "movie", "mediaId": titel["tmdb_id"], "userId": kennung("sven")},
    )
    assert antwort.status_code == 403
