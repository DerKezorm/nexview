"""Entdecken der Fernseh-App: ``GET /api/app/1/discover/{type}?list=…``.

nexcinema zeigt damit drei Reihen - neu auf Streaming, neu erschienen, beliebt
aller Zeiten -, und in allen fehlt, was die Mediathek schon hat. Vertrag in
``docs/app-discover.md``.

Gemessen wird an zwei Stellen:

* **Was an TMDB hinausgeht** (``TmdbClient._get`` abgefangen). Eine Liste, die
  den falschen Parameter schickt, liefert keinen Fehler, sondern andere Titel;
  das sieht niemand.
* **Was die App bekommt** (TMDB durch eine Attrappe ersetzt, der Zustand der
  Mediathek ueber ``library.apply_status`` und ``im_medienserver``). Die
  Zustandsberechnung selbst laeuft echt, ``discover._status_for``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus
from app.services import filters, media, requests_service
from app.services.beschaffung.arr import library
from app.services.fassungen import arr_kennung
from app.services.filters import DiscoverFilters, entdecken_filter
from app.services.tmdb import TmdbClient

from .conftest import auth_headers, create_user
from .test_altersfilter_waechter import ERLAUBT, GESPERRT, _kennungen, beschraenkt  # noqa: F401
from .test_child_permissions import _kind

HEUTE = date(2026, 11, 15)
LISTEN = ("streaming", "new", "popular")


# --- Was an TMDB hinausgeht -------------------------------------------------


async def _gesendet(
    media_type: str, filter_: DiscoverFilters, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Die Parameter, die ``TmdbClient.discover`` wirklich schickt."""
    gesehen: dict[str, Any] = {}

    async def abfangen(self, pfad: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        gesehen["pfad"] = pfad
        gesehen.update({k: v for k, v in (params or {}).items() if v is not None})
        return {"page": 1, "total_pages": 1, "total_results": 0, "results": []}

    monkeypatch.setattr(TmdbClient, "_get", abfangen)
    sortierung = media.SORT_OPTIONS[filter_.sort][media_type]
    await TmdbClient(api_key="k", language="de", region="DE").discover(media_type, filter_, sortierung)
    return gesehen


def _filter(liste: str, media_type: str, **mehr: Any) -> DiscoverFilters:
    return entdecken_filter(liste, media_type, region="DE", heute=HEUTE, **mehr)


async def test_streaming_filme_nach_digitalem_start_im_abo_der_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = await _gesendet("movie", _filter("streaming", "movie"), monkeypatch)
    assert p["pfad"] == "/discover/movie"
    assert p["watch_region"] == "DE"
    assert p["with_watch_monetization_types"] == "flatrate"
    # Das Datum der digitalen Veroeffentlichung in der Region, nicht der Kinostart.
    assert p["with_release_type"] == "4"
    assert p["region"] == "DE"
    assert p["release_date.gte"] == (HEUTE - timedelta(days=filters.ENTDECKEN_STREAMING_TAGE)).isoformat()
    assert p["release_date.lte"] == HEUTE.isoformat()
    assert "primary_release_date.gte" not in p
    assert p["sort_by"] == "release_date.desc"
    assert p["vote_count.gte"] == filters.ENTDECKEN_STREAMING_STIMMEN


async def test_streaming_serien_nach_erstausstrahlung_im_abo_der_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = await _gesendet("tv", _filter("streaming", "tv"), monkeypatch)
    assert p["pfad"] == "/discover/tv"
    assert p["watch_region"] == "DE"
    assert p["with_watch_monetization_types"] == "flatrate"
    assert p["first_air_date.gte"] == (HEUTE - timedelta(days=filters.ENTDECKEN_STREAMING_TAGE)).isoformat()
    assert p["first_air_date.lte"] == HEUTE.isoformat()
    assert p["sort_by"] == "first_air_date.desc"
    assert p["with_type"] == filters.ENTDECKEN_SERIENARTEN
    assert p["vote_count.gte"] == filters.ENTDECKEN_STREAMING_STIMMEN_TV


async def test_neu_erschienen_nimmt_kino_und_heimkino_der_letzten_monate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    film = await _gesendet("movie", _filter("new", "movie"), monkeypatch)
    assert film["release_date.gte"] == (HEUTE - timedelta(days=filters.ENTDECKEN_NEU_TAGE)).isoformat()
    assert film["release_date.lte"] == HEUTE.isoformat()
    assert set(film["with_release_type"].split("|")) == {"2", "3", "4", "5"}
    assert film["sort_by"] == "popularity.desc"
    assert "with_watch_monetization_types" not in film

    serie = await _gesendet("tv", _filter("new", "tv"), monkeypatch)
    assert serie["first_air_date.gte"] == (HEUTE - timedelta(days=filters.ENTDECKEN_NEU_TAGE)).isoformat()
    assert serie["first_air_date.lte"] == HEUTE.isoformat()
    assert serie["sort_by"] == "popularity.desc"


@pytest.mark.parametrize("media_type", ["movie", "tv"])
async def test_beliebt_ist_aller_zeiten_nach_stimmen_und_gut_bewertet(
    media_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = await _gesendet(media_type, _filter("popular", media_type), monkeypatch)
    assert p["sort_by"] == "vote_count.desc"
    assert p["vote_average.gte"] == filters.ENTDECKEN_BELIEBT_WERTUNG
    # Aller Zeiten: kein Datum, sonst fehlen genau die Klassiker.
    assert not [k for k in p if "date" in k and k != "sort_by"]
    assert "watch_region" not in p


@pytest.mark.parametrize("liste", LISTEN)
@pytest.mark.parametrize("media_type", ["movie", "tv"])
async def test_das_genre_geht_mit(liste: str, media_type: str, monkeypatch: pytest.MonkeyPatch) -> None:
    p = await _gesendet(media_type, _filter(liste, media_type, genre_id=18), monkeypatch)
    assert p["with_genres"] == 18


async def test_ohne_tarif_geht_keine_region_fuer_anbieter_hinaus(monkeypatch: pytest.MonkeyPatch) -> None:
    """TMDB ignoriert ``watch_region`` allein; dann soll es auch nicht dastehen."""
    p = await _gesendet("tv", DiscoverFilters(watch_region="DE"), monkeypatch)
    assert "watch_region" not in p
    assert "with_watch_monetization_types" not in p


def test_jede_liste_hat_ihren_eigenen_zwischenspeicher() -> None:
    schluessel = {
        _filter(liste, art, genre_id=genre).cache_key(art, "de")
        for liste in LISTEN
        for art in ("movie", "tv")
        for genre in (None, 18)
    }
    assert len(schluessel) == len(LISTEN) * 2 * 2


def test_der_tarif_steht_im_zwischenspeicher_schluessel() -> None:
    """Sonst bekaeme "nur im Abo" die Antwort der Abfrage ohne Abo-Filter."""
    ohne = DiscoverFilters(date_from="2026-10-01")
    mit = DiscoverFilters(date_from="2026-10-01", watch_region="DE", watch_monetization="flatrate")
    andere_region = DiscoverFilters(
        date_from="2026-10-01", watch_region="AT", watch_monetization="flatrate"
    )
    schluessel = {f.cache_key("movie", "de") for f in (ohne, mit, andere_region)}
    assert len(schluessel) == 3


def test_unbekannte_liste_ist_ein_fehler() -> None:
    with pytest.raises(ValueError):
        _filter("trending", "movie")


@pytest.mark.parametrize(
    ("page", "tmdb"),
    [(1, [1, 2, 3]), (2, [4, 5, 6]), (167, [499, 500]), (168, [])],
)
def test_jede_antwortseite_deckt_einen_festen_block_tmdb_seiten(page: int, tmdb: list[int]) -> None:
    assert filters.entdecken_tmdb_seiten(page) == tmdb


@pytest.mark.parametrize(("tmdb", "seiten"), [(0, 1), (1, 1), (3, 1), (4, 2), (7, 3), (999, 167)])
def test_seitenzahl_der_antwort(tmdb: int, seiten: int) -> None:
    assert filters.entdecken_seitenzahl(tmdb) == seiten


# --- Was die App bekommt ----------------------------------------------------


def _roh(tmdb_id: int, media_type: str) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "title": f"Film {tmdb_id}",
        "name": f"Serie {tmdb_id}",
        "overview": "Eine Beschreibung.",
        "poster_path": "/p.jpg",
        "backdrop_path": "/b.jpg",
        "vote_average": 7.5,
        "vote_count": 900,
        "genre_ids": [18],
        "genres": [{"id": 18, "name": "Drama"}],
        "release_date": "2026-10-01",
        "first_air_date": "2026-10-01",
        "runtime": 100,
        "episode_run_time": [45],
        "external_ids": {"tvdb_id": tmdb_id},
        "seasons": [],
    }


class _Tmdb:
    """TMDB mit festen Seiten; merkt sich jede Discover-Abfrage."""

    def __init__(self, seiten: dict[int, list[int]], gesamt: int) -> None:
        self.seiten = seiten
        self.gesamt = gesamt
        self.abfragen: list[tuple[str, DiscoverFilters, str]] = []

    async def discover(self, media_type: str, filter_: DiscoverFilters, sort_by: str) -> dict[str, Any]:
        self.abfragen.append((media_type, filter_, sort_by))
        kennungen = self.seiten.get(filter_.page, [])
        return {
            "page": filter_.page,
            "total_pages": self.gesamt,
            "total_results": self.gesamt * 20,
            "results": [_roh(k, media_type) for k in kennungen],
        }

    async def search(self, media_type: str, *a: Any, **k: Any) -> dict[str, Any]:
        return {"page": 1, "total_pages": 1, "total_results": 3, "results": [_roh(k, media_type) for k in (1, 2, 3)]}

    async def details(self, media_type: str, tmdb_ids: list[int]) -> dict[int, dict[str, Any]]:
        return {k: _roh(k, media_type) for k in tmdb_ids}

    async def genres(self, media_type: str) -> dict[int, str]:
        return {18: "Drama"}

    def seitenfolge(self) -> list[int]:
        return [f.page for _, f, _ in self.abfragen]


@pytest.fixture()
def tmdb(monkeypatch: pytest.MonkeyPatch, arr_client: TestClient):
    """Eine TMDB-Attrappe mit gesetztem Schluessel - der Beispielbetrieb siebt nicht."""
    assert arr_client.put(
        "/api/settings", json={"tmdb_api_key": "test-key", "default_region": "DE"}
    ).status_code == 200
    stand: dict[str, _Tmdb] = {}

    def einsetzen(seiten: dict[int, list[int]], gesamt: int = 1) -> _Tmdb:
        attrappe = _Tmdb(seiten, gesamt)
        stand["jetzt"] = attrappe
        monkeypatch.setattr(media, "_client", lambda settings, region=None: attrappe)
        return attrappe

    return einsetzen


def _mediathek(
    monkeypatch: pytest.MonkeyPatch,
    zustand: dict[int, str],
    im_medienserver: set[int] = frozenset(),
) -> None:
    """Was Radarr/Sonarr meldet (``zustand``) und was nur im Medienserver liegt."""

    async def status(_einstellungen, _art, items, _stufe="standard", **_rest):
        for eintrag in items:
            eintrag.status = zustand.get(eintrag.tmdb_id, "not_requested")
        return library.MatchResult(items=items)

    async def server(_db, _settings, _art, items, *_a, **_k):
        return {i.tmdb_id for i in items if i.tmdb_id in im_medienserver}

    monkeypatch.setattr(library, "apply_status", status)
    monkeypatch.setattr(requests_service, "im_medienserver", server)


def _ids(antwort) -> list[int]:
    assert antwort.status_code == 200, antwort.text
    return [i["tmdb_id"] for i in antwort.json()["items"]]


@pytest.mark.parametrize("liste", LISTEN)
def test_was_in_der_mediathek_liegt_fehlt(
    liste: str, arr_client: TestClient, tmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmdb({1: [1, 2, 3, 4, 5]})
    _mediathek(
        monkeypatch,
        {1: "downloaded", 3: "searching", 5: "partial"},
        im_medienserver={2},
    )
    antwort = arr_client.get(f"/api/app/1/discover/movie?list={liste}")
    ids = _ids(antwort)
    # 1 liegt in Radarr, 2 im Medienserver - beides ist "habt ihr schon".
    assert 1 not in ids
    assert 2 not in ids
    # Bekannt, aber noch nicht da, und was niemand kennt: bleibt.
    assert 3 in ids and 4 in ids
    # Eine Serie mit Luecken gibt es bei Filmen nicht; der Zustand siebt trotzdem nicht.
    assert 5 in ids


def test_angefragtes_bleibt_mit_seinem_zustand(
    arr_client: TestClient, tmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die App zeigt dann "Angefragt" statt "Wünschen" - verschwinden soll es nicht."""
    tmdb({1: [7, 8]})
    _mediathek(monkeypatch, {})
    admin = next(u["id"] for u in arr_client.get("/api/users").json() if u["role"] == "admin")
    with SessionLocal() as sitzung:
        sitzung.add(
            MediaRequest(
                user_id=admin,
                media_type=MediaType.movie,
                fassung_kennung=arr_kennung("movie", "standard"),
                tmdb_id=7,
                title="Film 7",
                status=RequestStatus.pending_approval,
            )
        )
        sitzung.commit()
    antwort = arr_client.get("/api/app/1/discover/movie?list=popular")
    assert _ids(antwort) == [7, 8]
    assert antwort.json()["items"][0]["status"] == "pending_approval"


def test_serien_mit_fehlenden_staffeln_bleiben(
    arr_client: TestClient, tmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmdb({1: [11, 12, 13]})
    _mediathek(monkeypatch, {11: "downloaded", 12: "partial"})
    antwort = arr_client.get("/api/app/1/discover/tv?list=popular")
    ids = _ids(antwort)
    assert ids == [12, 13]
    assert antwort.json()["items"][0]["status"] == "partial"


def test_seiten_werden_aus_mehreren_tmdb_seiten_aufgefuellt(
    arr_client: TestClient, tmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seite 1 von TMDB liegt ganz in der Mediathek - die Antwort ist trotzdem voll."""
    attrappe = tmdb(
        {
            1: list(range(1, 21)),
            2: list(range(21, 41)),
            3: list(range(41, 61)),
            4: list(range(61, 81)),
        },
        gesamt=10,
    )
    _mediathek(monkeypatch, dict.fromkeys(range(1, 21), "downloaded"))
    antwort = arr_client.get("/api/app/1/discover/movie?list=new")
    assert _ids(antwort) == list(range(21, 61))
    assert attrappe.seitenfolge() == [1, 2, 3]
    daten = antwort.json()
    assert daten["page"] == 1
    assert daten["total_pages"] == 4  # zehn TMDB-Seiten in Bloecken zu drei

    # Seite 2 macht genau dort weiter, wo Seite 1 aufgehoert hat.
    attrappe.abfragen.clear()
    assert _ids(arr_client.get("/api/app/1/discover/movie?list=new&page=2")) == list(range(61, 81))
    assert attrappe.seitenfolge() == [4, 5, 6]


def test_hoert_auf_wenn_tmdb_keine_seiten_mehr_hat(
    arr_client: TestClient, tmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    attrappe = tmdb({1: [1, 2], 2: [2, 3]}, gesamt=2)
    _mediathek(monkeypatch, {})
    antwort = arr_client.get("/api/app/1/discover/tv?list=streaming")
    # Doppelte (TMDB wiederholt beim Blaettern) erscheinen nur einmal.
    assert _ids(antwort) == [1, 2, 3]
    assert attrappe.seitenfolge() == [1, 2]
    assert antwort.json()["total_pages"] == 1


@pytest.mark.parametrize(
    ("liste", "sortierung"),
    [("streaming", "release_date.desc"), ("new", "popularity.desc"), ("popular", "vote_count.desc")],
)
def test_jede_liste_fragt_ihre_abfrage(
    liste: str, sortierung: str, arr_client: TestClient, tmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    attrappe = tmdb({1: [1]})
    _mediathek(monkeypatch, {})
    _ids(arr_client.get(f"/api/app/1/discover/movie?list={liste}&genre=18"))
    art, filter_, sort_by = attrappe.abfragen[0]
    assert art == "movie"
    assert sort_by == sortierung
    assert filter_.genre_id == 18
    assert filter_ == entdecken_filter(liste, "movie", region="DE", genre_id=18, page=1)


def test_ohne_list_bleibt_die_alte_adresse_wie_sie_war(
    arr_client: TestClient, tmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zugesagt seit Block 1: ohne ``list`` die Liste der Webseite, ungesiebt."""
    attrappe = tmdb({1: [1, 2]})
    _mediathek(monkeypatch, {1: "downloaded"})
    antwort = arr_client.get("/api/app/1/discover/movie?genre_id=18&sort=rating")
    assert _ids(antwort) == [1, 2]
    assert antwort.json()["items"][0]["status"] == "downloaded"
    _, filter_, sort_by = attrappe.abfragen[0]
    assert sort_by == "vote_average.desc"
    assert filter_.genre_id == 18
    assert filter_.watch_monetization == ""
    assert attrappe.seitenfolge() == [1]


def test_unbekannte_liste_ist_ein_422(arr_client: TestClient, tmdb) -> None:
    tmdb({})
    assert arr_client.get("/api/app/1/discover/movie?list=trending").status_code == 422


def test_genres_in_der_sprache_des_kontos(arr_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert arr_client.put("/api/settings", json={"tmdb_api_key": "test-key"}).status_code == 200

    class _Sprachen:
        def __init__(self, sprache: str) -> None:
            self.sprache = sprache

        async def genres(self, media_type: str) -> dict[int, str]:
            if self.sprache.startswith("en"):
                return {28: "Action", 18: "Drama", 35: "Comedy"}
            return {28: "Action", 18: "Drama", 35: "Komödie"}

    monkeypatch.setattr(media, "_client", lambda settings, region=None: _Sprachen(settings.default_language))
    create_user(arr_client, "anna", "anna-passwort-1", language="en")
    englisch = auth_headers(arr_client, "anna", "anna-passwort-1")

    deutsch = arr_client.get("/api/app/1/discover/genres/movie")
    assert deutsch.status_code == 200, deutsch.text
    assert {"id": 35, "name": "Komödie"} in deutsch.json()

    antwort = arr_client.get("/api/app/1/discover/genres/movie", headers=englisch)
    assert antwort.status_code == 200, antwort.text
    assert {"id": 35, "name": "Comedy"} in antwort.json()
    assert all(set(g) == {"id", "name"} for g in antwort.json())


def test_die_suche_sagt_je_titel_ob_er_schon_da_ist(
    arr_client: TestClient, tmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fuer "Habt ihr schon": die Suche siebt nicht, sie traegt den Zustand."""
    tmdb({})
    _mediathek(monkeypatch, {1: "downloaded"}, im_medienserver={2})
    antwort = arr_client.get("/api/app/1/search/movie?q=Film")
    assert antwort.status_code == 200, antwort.text
    zustand = {i["tmdb_id"]: i["status"] for i in antwort.json()["items"]}
    assert zustand == {1: "downloaded", 2: "in_library", 3: "not_requested"}


@pytest.mark.parametrize("liste", [*LISTEN, None])
def test_altersgrenze_gilt_in_jeder_liste(
    liste: str | None, beschraenkt: tuple[TestClient, dict[str, str]]  # noqa: F811
) -> None:
    """Dieselbe Probe wie ``test_altersfilter_waechter``, fuer jede Liste.

    Der Waechter dort ruft die Adresse nur einmal (ohne ``list``); die Listen
    gehen einen eigenen Weg (``media.entdecken``) und werden hier gemessen.
    """
    client, kopf = beschraenkt
    adresse = "/api/app/1/discover/movie" + (f"?list={liste}" if liste else "")
    antwort = client.get(adresse, headers=kopf)
    assert antwort.status_code == 200, antwort.text
    kennungen = _kennungen(antwort.json())
    assert GESPERRT not in kennungen
    assert ERLAUBT in kennungen, "Die Probe hätte nichts gemessen."

    # Gegenprobe: Ohne Grenze kommen beide.
    alle = _kennungen(client.get(adresse).json())
    assert {ERLAUBT, GESPERRT} <= alle


def test_ein_kind_kommt_nicht_an_die_listen(admin_client: TestClient) -> None:
    kopf = _kind(admin_client)
    for adresse in ("/api/app/1/discover/movie?list=popular", "/api/app/1/discover/genres/movie"):
        assert admin_client.get(adresse, headers=kopf).status_code == 403, adresse
