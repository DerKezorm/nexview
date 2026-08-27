"""Die "laedt gerade"-Anzeige: Momentaufnahme aus der Warteschlange.

Zwei Ebenen. Der **Kern** (``abgleich_kern.laedt_fortschritt``) ordnet
Warteschlangen-Eintraege einer Anfrage zu - bewusst vorsichtig: Was keine
Staffel- oder Folgenangabe traegt, zaehlt bei Staffel- und Paket-Anfragen
nicht mit; lieber keine Anzeige als eine falsche. Der **Rundgang** setzt die
Anzeige-Felder und raeumt sie wieder ab, sobald nichts mehr laedt oder die
Anfrage fertig ist - ohne je einen eigenen Status daraus zu machen.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus
from app.services import abgleich_kern, library, status_poller
from app.services.arr import WarteschlangenEintrag
from app.services.radarr import LibraryEntry as MovieEntry
from app.services.settings_service import load_settings

from .conftest import auth_headers, create_user

EINTRAG = SimpleNamespace(arr_id=4242)


def _film(status: RequestStatus = RequestStatus.searching) -> MediaRequest:
    return SimpleNamespace(
        media_type=MediaType.movie, season=None, episodes=None, status=status
    )


def _staffel(season: int, episodes: list[int] | None = None) -> MediaRequest:
    return SimpleNamespace(
        media_type=MediaType.tv,
        season=season,
        episodes=episodes,
        status=RequestStatus.searching,
    )


def _zeile(
    arr_id: int = 4242,
    season: int | None = None,
    episode: int | None = None,
    size: int = 100,
    sizeleft: int = 50,
) -> WarteschlangenEintrag:
    return WarteschlangenEintrag(
        arr_id=arr_id, season=season, episode=episode, size=size, sizeleft=sizeleft
    )


# --- Der Kern: Zuordnung und Rechnung ---------------------------------------


def test_film_mit_laufendem_download_zeigt_prozent() -> None:
    fortschritt = abgleich_kern.laedt_fortschritt(
        _film(), EINTRAG, [_zeile(size=200, sizeleft=50)]
    )
    assert fortschritt == 75


def test_fremder_download_zaehlt_nicht() -> None:
    assert (
        abgleich_kern.laedt_fortschritt(_film(), EINTRAG, [_zeile(arr_id=7)]) is None
    )


def test_ohne_eintrag_keine_aussage() -> None:
    assert abgleich_kern.laedt_fortschritt(_film(), None, [_zeile()]) is None


def test_staffelanfrage_zaehlt_nur_die_eigene_staffel() -> None:
    warteschlange = [
        _zeile(season=2, episode=1, size=100, sizeleft=0),
        _zeile(season=3, episode=1, size=100, sizeleft=100),
    ]
    assert abgleich_kern.laedt_fortschritt(_staffel(2), EINTRAG, warteschlange) == 100


def test_eintrag_ohne_staffelangabe_zaehlt_bei_staffelanfragen_nicht() -> None:
    """Die Vorsichtsregel: Sonarrs Warteschlange war beim Messen leer, die
    Feldnamen sind unbelegt - ein Eintrag ohne Staffel koennte zu irgendeiner
    Staffel gehoeren. Lieber keine Anzeige als eine falsche."""
    assert (
        abgleich_kern.laedt_fortschritt(_staffel(2), EINTRAG, [_zeile(season=None)])
        is None
    )


def test_folgen_paket_zaehlt_nur_bestellte_folgen() -> None:
    warteschlange = [
        _zeile(season=2, episode=3, size=100, sizeleft=50),
        _zeile(season=2, episode=4, size=100, sizeleft=0),
    ]
    assert (
        abgleich_kern.laedt_fortschritt(_staffel(2, [3]), EINTRAG, warteschlange) == 50
    )


def test_ganze_serie_zaehlt_alle_folgen_der_serie() -> None:
    warteschlange = [
        _zeile(season=1, episode=1, size=100, sizeleft=100),
        _zeile(season=2, episode=5, size=100, sizeleft=0),
    ]
    anfrage = SimpleNamespace(
        media_type=MediaType.tv, season=None, episodes=None,
        status=RequestStatus.searching,
    )
    assert abgleich_kern.laedt_fortschritt(anfrage, EINTRAG, warteschlange) == 50


def test_groesse_null_heisst_null_prozent() -> None:
    """Manche Download-Clients melden die Groesse erst spaeter - dann ist
    "0 %" die ehrliche Aussage, nicht "keine Anzeige"."""
    assert (
        abgleich_kern.laedt_fortschritt(_film(), EINTRAG, [_zeile(size=0, sizeleft=0)])
        == 0
    )


# --- Der Rundgang: setzen und wieder abraeumen ------------------------------


def _laufende_anfrage(client: TestClient) -> MediaRequest:
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
        request.status = RequestStatus.searching
        session.commit()
        session.refresh(request)
        return request


class _FakeRadarr:
    def __init__(self, eintraege: list[WarteschlangenEintrag]) -> None:
        self.eintraege = eintraege

    async def warteschlange(self) -> list[WarteschlangenEintrag]:
        return self.eintraege


@pytest.mark.asyncio
async def test_rundgang_setzt_und_raeumt_die_anzeige(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _laufende_anfrage(arr_client)

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict[int, MovieEntry]:
        return {request.tmdb_id: MovieEntry(arr_id=4242, has_file=False, monitored=True)}

    monkeypatch.setattr(library, "movie_library", bibliothek)
    fake = _FakeRadarr([_zeile(size=200, sizeleft=50)])
    monkeypatch.setattr(library, "radarr_client", lambda _s, _t="standard": fake)

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))
    with SessionLocal() as session:
        stand = session.query(MediaRequest).one()
        assert stand.laedt_fortschritt == 75
        assert stand.laedt_seit is not None
        assert stand.status == RequestStatus.searching

    # Der Download verschwindet aus der Warteschlange (fehlgeschlagen oder
    # verworfen) - die Anzeige verschwindet mit, der Status bleibt ehrlich.
    fake.eintraege = []
    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))
    with SessionLocal() as session:
        stand = session.query(MediaRequest).one()
        assert stand.laedt_fortschritt is None
        assert stand.laedt_seit is None
        assert stand.status == RequestStatus.searching


@pytest.mark.asyncio
async def test_fertig_raeumt_die_anzeige_mit_auf(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _laufende_anfrage(arr_client)
    with SessionLocal() as session:
        zeile = session.query(MediaRequest).one()
        zeile.laedt_fortschritt = 90
        zeile.laedt_seit = zeile.requested_at
        session.commit()

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict[int, MovieEntry]:
        return {request.tmdb_id: MovieEntry(arr_id=4242, has_file=True, monitored=True)}

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 1
    with SessionLocal() as session:
        stand = session.query(MediaRequest).one()
        assert stand.status == RequestStatus.downloaded
        assert stand.laedt_fortschritt is None
        assert stand.laedt_seit is None
