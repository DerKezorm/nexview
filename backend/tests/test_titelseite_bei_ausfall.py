"""Titelseite und Downloads-Seite, wenn der Beschaffungsweg nicht antwortet.

Zwei Befunde aus dem großen Prüfgang (26.09.2026):

* Während nexcrate neu startete, stand auf der Titelseite eines angefragten
  Films „Not requested" samt Anfrage-Knopf. Die Anfrage war längst fertig
  („downloaded"); weil die Bibliothek das in dem Moment nicht bestätigen
  konnte, wurde die Aussage verworfen, als hätte die Bibliothek „weg" gesagt.
  Nicht lesen ist aber nicht dasselbe wie „nicht da". Radarr und Sonarr gehen
  durch dieselbe Stelle.
* War nexcrate ganz weg, wartete die Titelseite 6 bis 24 Sekunden, die
  Downloads-Seite 6,5 Sekunden, bei einer eingefrorenen nexcrate länger als
  45 Sekunden - ohne jeden Hinweis. Jede Abfrage an nexcrate lief einzeln in
  die volle Zeitgrenze.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus
from app.routers import details as details_router
from app.schemas_media import EpisodeInfo, MediaDetail, SeasonDetail
from app.services.beschaffung import NEX
from app.services.beschaffung.arr import library
from app.services.beschaffung.arr.client import ArrError
from app.services.beschaffung.nex import bestand as nex_bestand
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.beschaffung.nex.fehler import NexcrateError
from app.services.fassungen import arr_kennung
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, FILM_UHD, KEY, SERIE_HD, URL, FakeNexcrate
from .conftest import create_user


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    attrappe = FakeNexcrate()
    nex_client.use_transport(attrappe.transport())
    system.merken(attrappe._system())
    nex_fassungen.vergessen()
    nex_bestand.verwerfen()
    try:
        yield attrappe
    finally:
        nex_client.use_transport(None)
        system.vergessen()
        nex_fassungen.vergessen()
        nex_bestand.verwerfen()


def _einrichten(nexcrate: FakeNexcrate) -> Any:
    with SessionLocal() as sitzung:
        save_settings(sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY})
        nex_fassungen.schreiben(sitzung, nexcrate.versions)
        sitzung.commit()
        return load_settings(sitzung, frisch=True)


def _anfrage(client: TestClient, tmdb_id: int, fassung: str, status: RequestStatus) -> None:
    kim = create_user(client, f"kim{tmdb_id}{fassung[-4:]}")
    with SessionLocal() as sitzung:
        sitzung.add(
            MediaRequest(
                user_id=kim["id"],
                media_type=MediaType.movie,
                fassung_kennung=fassung,
                tmdb_id=tmdb_id,
                title="Erfundener Film",
                status=status,
            )
        )
        sitzung.commit()


def _titelseite_ohne_tmdb(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _detail(_db, _settings, _art, tmdb_id, **_rest):
        return MediaDetail(tmdb_id=tmdb_id, media_type="movie", title="Erfundener Film")

    monkeypatch.setattr(details_router.media, "full_detail", _detail)


def _nexcrate_weg(fehler: type[httpx.TransportError] = httpx.ConnectError) -> list[dict]:
    """nexcrate antwortet nicht mehr. Zurück kommen die Zeitgrenzen jedes Versuchs."""
    versuche: list[dict] = []

    def stumm(anfrage: httpx.Request) -> httpx.Response:
        versuche.append(dict(anfrage.extensions.get("timeout") or {}))
        raise fehler("nexcrate antwortet nicht")

    nex_client.use_transport(httpx.MockTransport(stumm))
    return versuche


def _fassung(antwort: dict, kennung: str) -> str:
    return next(f["status"] for f in antwort["fassungen"] if f["kennung"] == kennung)


# --------------------------------------------------------------------------
# Nicht lesen ist nicht "nicht da"


def test_eine_fertige_anfrage_bleibt_fertig_waehrend_nexcrate_schweigt(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _einrichten(nexcrate)
    _titelseite_ohne_tmdb(monkeypatch)
    _anfrage(admin_client, 9501, FILM_HD, RequestStatus.downloaded)
    _anfrage(admin_client, 9501, FILM_UHD, RequestStatus.downloaded)
    _nexcrate_weg()

    antwort = admin_client.get("/api/detail/movie/9501")

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["status"] == "downloaded"
    assert _fassung(daten, FILM_UHD) == "downloaded"
    # Und die Seite sagt dazu, dass es der letzte bekannte Stand ist.
    assert daten["status_unconfirmed"] is True
    assert daten["status_refused"] is False


def test_lehnt_nexcrate_ab_sagt_die_titelseite_das_statt_schweigen(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein falscher Schlüssel geht nicht von selbst vorbei - „antwortet gerade
    nicht" wäre dort die falsche Auskunft."""
    _einrichten(nexcrate)
    _titelseite_ohne_tmdb(monkeypatch)
    _anfrage(admin_client, 9513, FILM_HD, RequestStatus.downloaded)
    nexcrate.key = "ein-anderer-schluessel"

    daten = admin_client.get("/api/detail/movie/9513").json()

    assert daten["status"] == "downloaded"
    assert daten["status_unconfirmed"] is True
    assert daten["status_refused"] is True


def test_eine_laufende_anfrage_bleibt_laufend_waehrend_nexcrate_schweigt(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _einrichten(nexcrate)
    _titelseite_ohne_tmdb(monkeypatch)
    _anfrage(admin_client, 9502, FILM_HD, RequestStatus.searching)
    _nexcrate_weg()

    daten = admin_client.get("/api/detail/movie/9502").json()

    assert daten["status"] == "searching"
    assert daten["status_unconfirmed"] is True


def test_antwortet_nexcrate_ist_der_stand_bestaetigt(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe: Kein Hinweis, wenn alles gelesen wurde."""
    _einrichten(nexcrate)
    _titelseite_ohne_tmdb(monkeypatch)
    _anfrage(admin_client, 9503, FILM_HD, RequestStatus.downloaded)
    nexcrate.film(9503, name="Erfundener Film", versionen=[nexcrate.fassung(FILM_HD, "available")])

    daten = admin_client.get("/api/detail/movie/9503").json()

    assert daten["status"] == "downloaded"
    assert daten["status_unconfirmed"] is False


def test_sagt_nexcrate_weg_gilt_die_fertige_anfrage_nicht_mehr(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die bestehende Regel bleibt: Eine gelesene Bibliothek ohne den Titel
    nimmt „geladen" zurück, damit er sich wieder anfragen lässt."""
    _einrichten(nexcrate)
    _titelseite_ohne_tmdb(monkeypatch)
    _anfrage(admin_client, 9504, FILM_HD, RequestStatus.downloaded)
    nexcrate.film(9504, name="Erfundener Film", versionen=[])

    daten = admin_client.get("/api/detail/movie/9504").json()

    assert daten["status"] == "not_requested"
    assert daten["status_unconfirmed"] is False


async def test_ohne_eigene_anfrage_gilt_der_zuletzt_gelesene_bestand(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Titel, den der Betreiber selbst in nexcrate geholt hat: Nexview
    kennt ihn nur aus dem gehaltenen Bestand, und der ist der letzte bekannte
    Stand."""
    settings = _einrichten(nexcrate)
    _titelseite_ohne_tmdb(monkeypatch)
    nexcrate.film(9505, name="Erfundener Film", versionen=[nexcrate.fassung(FILM_HD, "available")])
    await nex_bestand.auffrischen(settings, "movie")
    _nexcrate_weg()

    daten = admin_client.get("/api/detail/movie/9505").json()

    assert daten["status"] == "downloaded"
    assert daten["status_unconfirmed"] is True


def test_die_kacheln_der_listen_behalten_eine_fertige_anfrage(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    """Dieselbe Regel steht in den Listen (Entdecken, Suche, Startseite)."""
    _einrichten(nexcrate)
    tmdb_id = admin_client.get("/api/discover/movie").json()["items"][0]["tmdb_id"]
    _anfrage(admin_client, tmdb_id, FILM_HD, RequestStatus.downloaded)
    _nexcrate_weg()

    antwort = admin_client.get("/api/discover/movie").json()

    karte = next(k for k in antwort["items"] if k["tmdb_id"] == tmdb_id)
    assert karte["status"] == "downloaded"
    assert antwort["arr_warning"]


def test_im_arr_betrieb_bleibt_eine_fertige_anfrage_fertig_waehrend_radarr_schweigt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _titelseite_ohne_tmdb(monkeypatch)
    _anfrage(arr_client, 9506, arr_kennung("movie", "standard"), RequestStatus.downloaded)

    async def schweigt(_settings: object, _tier: str = "standard") -> dict:
        raise ArrError("Radarr antwortet nicht.", code="arr_unreachable", service="Radarr")

    monkeypatch.setattr(library, "movie_library", schweigt)

    daten = arr_client.get("/api/detail/movie/9506").json()

    assert daten["status"] == "downloaded"
    assert daten["status_unconfirmed"] is True


def test_schweigt_nur_die_4k_instanz_sagt_die_titelseite_es_dazu(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Radarr antwortet, Radarr-4K nicht: Die Hauptachse ist bestätigt, die
    4K-Achse nicht. Die 4K-Achse behält ihren letzten Stand, und die Seite
    sagt, dass nicht alles bestätigt ist."""
    antwort = arr_client.put(
        "/api/settings",
        json={"radarr_uhd_url": "http://127.0.0.1:11", "radarr_uhd_api_key": "test-radarr-4k-key"},
    )
    assert antwort.status_code == 200, antwort.text
    _titelseite_ohne_tmdb(monkeypatch)
    uhd = arr_kennung("movie", "uhd")
    _anfrage(arr_client, 9509, uhd, RequestStatus.downloaded)

    async def nur_4k_schweigt(_settings: object, tier: str = "standard") -> dict:
        if tier == "uhd":
            raise ArrError("Radarr antwortet nicht.", code="arr_unreachable", service="Radarr")
        return {}

    monkeypatch.setattr(library, "movie_library", nur_4k_schweigt)

    daten = arr_client.get("/api/detail/movie/9509").json()

    assert _fassung(daten, uhd) == "downloaded"
    assert daten["status"] == "not_requested"
    assert daten["status_unconfirmed"] is True


# --------------------------------------------------------------------------
# Die Staffelansicht


def _staffel_ohne_tmdb(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _serie(_db, _settings, _art, tmdb_id, **_rest):
        return MediaDetail(tmdb_id=tmdb_id, media_type="tv", title="Erfundene Serie", tvdb_id=95100)

    async def _staffel(_db, _settings, _tmdb_id, nummer, **_rest):
        return SeasonDetail(
            season_number=nummer,
            name=f"Staffel {nummer}",
            episodes=[EpisodeInfo(episode_number=n, name=f"Folge {n}") for n in (1, 2)],
        )

    monkeypatch.setattr(details_router.media, "detail", _serie)
    monkeypatch.setattr(details_router.media, "season_detail", _staffel)


def test_die_staffelansicht_sagt_dazu_wenn_nexcrate_schweigt(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst stand jede Folge als „fehlt noch" da, ohne jeden Hinweis."""
    _einrichten(nexcrate)
    _staffel_ohne_tmdb(monkeypatch)
    _nexcrate_weg()

    antwort = admin_client.get("/api/detail/tv/9510/season/1")

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["status_unconfirmed"] is True
    assert antwort.json()["status_refused"] is False


def test_die_staffelansicht_sagt_es_wenn_nexcrate_ablehnt(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _einrichten(nexcrate)
    _staffel_ohne_tmdb(monkeypatch)
    nexcrate.key = "ein-anderer-schluessel"

    daten = admin_client.get("/api/detail/tv/9514/season/1").json()

    assert daten["status_unconfirmed"] is True
    assert daten["status_refused"] is True


def test_die_staffelansicht_ist_bestaetigt_wenn_nexcrate_antwortet(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _einrichten(nexcrate)
    _staffel_ohne_tmdb(monkeypatch)
    ref = "tmdb:9511"
    nexcrate.serie(9511, versionen=[nexcrate.fassung(SERIE_HD, "available")])
    nexcrate.staffel(
        ref,
        1,
        [
            nexcrate.folge(
                1,
                versionen=[
                    nexcrate.folgen_fassung(
                        SERIE_HD, "available", files=[{"file_id": 1, "size_bytes": 100}]
                    )
                ],
            ),
            nexcrate.folge(2, versionen=[nexcrate.folgen_fassung(SERIE_HD, "wanted")]),
        ],
    )

    daten = admin_client.get("/api/detail/tv/9511/season/1").json()

    assert [f["available"] for f in daten["episodes"]] == [True, False]
    assert daten["status_unconfirmed"] is False


def test_die_staffelansicht_sagt_es_auch_wenn_sonarr_schweigt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _staffel_ohne_tmdb(monkeypatch)

    async def schweigt(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
        raise ArrError("Sonarr antwortet nicht.", code="arr_unreachable", service="Sonarr")

    monkeypatch.setattr(library, "series_library", schweigt)

    daten = arr_client.get("/api/detail/tv/9512/season/1").json()

    assert daten["status_unconfirmed"] is True


# --------------------------------------------------------------------------
# Eine eigene, kurze Zeitgrenze für Seiten, auf die jemand wartet


def test_die_titelseite_wartet_nur_kurz_und_fragt_nach_dem_ersten_ausfall_nicht_weiter(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _einrichten(nexcrate)
    _titelseite_ohne_tmdb(monkeypatch)
    _anfrage(admin_client, 9507, FILM_HD, RequestStatus.searching)
    versuche = _nexcrate_weg(httpx.ConnectTimeout)

    antwort = admin_client.get("/api/detail/movie/9507")

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["status_unconfirmed"] is True
    # Hauptfassung und 4K-Fassung fragen beide - aber nur der erste Versuch
    # geht hinaus, und der mit der kurzen Grenze.
    assert len(versuche) == 1, versuche
    assert versuche[0]["connect"] <= nex_client.KURZ.connect
    assert versuche[0]["read"] <= nex_client.KURZ.read
    assert nex_client.KURZ.read is not None and nex_client.KURZ.read <= 5


def test_die_downloads_seite_wartet_nur_kurz(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    _einrichten(nexcrate)
    versuche = _nexcrate_weg(httpx.ReadTimeout)

    antwort = admin_client.get("/api/admin/downloads")

    assert antwort.status_code == 200, antwort.text
    instanz = antwort.json()["instanzen"][0]
    assert instanz["erreichbar"] is False
    assert instanz["fehler"] == "nexcrate_timeout"
    assert len(versuche) == 1
    assert versuche[0]["read"] <= nex_client.KURZ.read


async def test_ausserhalb_der_seiten_gilt_die_volle_zeitgrenze(nexcrate: FakeNexcrate) -> None:
    """Der Rundgang und alles im Hintergrund wartet weiter so lange wie bisher -
    und ein Ausfall sperrt dort keine weitere Frage."""
    settings = _einrichten(nexcrate)
    versuche = _nexcrate_weg(httpx.ConnectTimeout)
    client = nex_client.NexcrateClient(settings.nexcrate_url, settings.nexcrate_api_key)

    for _ in range(2):
        with pytest.raises(NexcrateError):
            await client.system()

    assert len(versuche) == 2
    assert versuche[0]["read"] == nex_client.TIMEOUT.read


async def test_die_kurze_frist_endet_mit_der_seite(nexcrate: FakeNexcrate) -> None:
    settings = _einrichten(nexcrate)
    versuche = _nexcrate_weg(httpx.ConnectTimeout)
    client = nex_client.NexcrateClient(settings.nexcrate_url, settings.nexcrate_api_key)

    with nex_client.kurze_frist():
        for _ in range(3):
            with pytest.raises(NexcrateError) as gefangen:
                await client.system()
            assert gefangen.value.code == "nexcrate_timeout"
    assert len(versuche) == 1

    with pytest.raises(NexcrateError):
        await client.system()
    assert len(versuche) == 2
    assert versuche[1]["read"] == nex_client.TIMEOUT.read


def test_woran_es_haengt_wartet_nur_kurz(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    """Der Abschnitt „Woran es hängt" auf derselben Seite."""
    _einrichten(nexcrate)
    versuche = _nexcrate_weg(httpx.ReadTimeout)

    antwort = admin_client.get("/api/beschaffung/warum/movie/9508")

    assert antwort.status_code >= 500
    assert antwort.json()["detail"]["code"] == "nexcrate_timeout"
    assert len(versuche) == 1
    assert versuche[0]["read"] <= nex_client.KURZ.read
