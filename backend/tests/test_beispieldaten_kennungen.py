"""Beispieldaten haben eigene Nummern, und ohne TMDB sagt die Titelseite, warum.

Zwei Befunde aus dem grossen Pruefgang (25./26.09.2026):

* **#note-30**: Die Beispieltitel trugen Kennungen zwischen 900.000 und
  990.000 - mitten in TMDBs Nummernraum. Trug jemand danach einen TMDB-
  Schluessel ein, zeigte die alte Beispielanfrage "Pixelherz" (900678) auf
  einen echten ungarischen Kurzfilm mit expliziter Beschreibung, weiterhin
  als "Wartet auf Freigabe". Wer aus der Liste heraus freigab, gab einen
  fremden Titel frei.
* **#note-12**: Ohne TMDB-Schluessel antwortete die Titelseite eines echten
  Bibliothekstitels mit "Dieser Demo-Titel ist nicht vorhanden." Der Titel
  war kein Demo-Titel, und die Meldung sagte nicht, was fehlt.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import db as db_modul
from app.db import SessionLocal
from app.mocks import demo_data
from app.models import Favorite, MediaRequest, MediaType
from app.services import media

from .conftest import auth_headers, create_user

#: Die alten Kennungen aus dem Befund, gemessen an einer echten Installation.
PIXELHERZ_ALT = 900678
NORDLICHT_ALT = 950765


def _alte_kennung(titel: str, art: str) -> int:
    """Die Formel bis 1.0.0 - unabhaengig vom Code nachgerechnet."""
    digest = hashlib.sha256(f"{art}:{titel}".encode()).hexdigest()
    return 900_000 + int(digest[:6], 16) % 90_000


def _beispiel(art: str, titel: str) -> Any:
    return next(item for item in demo_data.demo_items(art) if item.title == titel)


class _EchtesTmdb:
    """TMDB, das zu jeder Kennung einen echten, fremden Titel kennt."""

    def __init__(self) -> None:
        self.abrufe: list[int] = []

    async def genres(self, media_type: str) -> dict[int, str]:
        return {18: "Drama"}

    async def detail(
        self, media_type: str, tmdb_id: int, *, ausfuehrlich: bool = False
    ) -> dict[str, Any]:
        self.abrufe.append(tmdb_id)
        return {
            "id": tmdb_id,
            "title": "Kedvelem",
            "name": "Kedvelem",
            "overview": "Ein fremder Kurzfilm.",
            "release_date": "2022-01-01",
            "genre_ids": [18],
            "recommendations": {"results": []},
        }

    async def details(self, media_type: str, tmdb_ids: list[int]) -> dict[int, dict[str, Any]]:
        return {}


@pytest.fixture
def echtes_tmdb(monkeypatch: pytest.MonkeyPatch) -> _EchtesTmdb:
    fake = _EchtesTmdb()
    monkeypatch.setattr(media, "_client", lambda settings, region=None: fake)
    monkeypatch.setattr(media, "TmdbClient", lambda *args, **kwargs: fake)
    return fake


# ---------------------------------------------------------------------------
# #note-30: eigener Nummernraum
# ---------------------------------------------------------------------------


def test_die_beispieltitel_sind_als_solche_erkennbar() -> None:
    """Jede Kennung des Beispielkatalogs liegt im eigenen Bereich, keine alte mehr."""
    for art in ("movie", "tv"):
        for item in demo_data.demo_items(art):
            assert demo_data.ist_beispiel(item.tmdb_id), (art, item.title, item.tmdb_id)
            assert item.tmdb_id != _alte_kennung(item.title, art)
            if item.tvdb_id is not None:
                assert demo_data.ist_beispiel(item.tvdb_id)
    # Und umgekehrt: Eine gewoehnliche TMDB-Kennung ist keiner.
    assert not demo_data.ist_beispiel(PIXELHERZ_ALT)
    assert not demo_data.ist_beispiel(1_500_000)


def test_eine_beispielanfrage_zeigt_nach_dem_tmdb_schluessel_keinen_fremden_titel(
    arr_client: TestClient, echtes_tmdb: _EchtesTmdb
) -> None:
    """Der Weg aus dem Befund: im Beispielbetrieb anfragen, dann TMDB eintragen."""
    create_user(arr_client, "kim", "passwort-1234")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    pixelherz = _beispiel("movie", "Pixelherz")
    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": pixelherz.tmdb_id,
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=kim,
    )
    assert antwort.status_code == 201, antwort.text

    assert arr_client.put("/api/settings", json={"tmdb_api_key": "test-key"}).status_code == 200
    assert arr_client.get("/api/config").json()["using_demo_data"] is False

    seite = arr_client.get(f"/api/detail/movie/{pixelherz.tmdb_id}")
    assert seite.status_code == 404, seite.text
    assert seite.json()["detail"]["code"] == "sample_title_gone"
    # Gar nicht erst gefragt: Die Kennung gehoert TMDB nicht.
    assert pixelherz.tmdb_id not in echtes_tmdb.abrufe


def _alte_installation(arr_client: TestClient) -> dict[str, int]:
    """Anfragen und ein Herz, wie eine Installation vor 1.0.0 sie gespeichert hat."""
    kim = create_user(arr_client, "kim", "passwort-1234")
    kopf = auth_headers(arr_client, "kim", "passwort-1234")
    kennungen: dict[str, int] = {}
    for art, titel in (("movie", "Pixelherz"), ("movie", "Nordlicht"), ("tv", "Grenzfall")):
        item = _beispiel(art, titel)
        antwort = arr_client.post(
            "/api/requests",
            json={
                "media_type": art,
                "tmdb_id": item.tmdb_id,
                "quality_profile_id": 1,
                "root_folder_path": "/data/Movies" if art == "movie" else "/data/TV-Shows",
            },
            headers=kopf,
        )
        assert antwort.status_code == 201, antwort.text
        kennungen[titel] = antwort.json()["id"]

    alt_grenzfall = _alte_kennung("Grenzfall", "tv")
    with SessionLocal() as sitzung:
        for titel, nummer in kennungen.items():
            anfrage = sitzung.get(MediaRequest, nummer)
            art = anfrage.media_type.value
            anfrage.tmdb_id = _alte_kennung(titel, art)
            if art == "tv":
                anfrage.tvdb_id = alt_grenzfall + 500_000
        # Dieselbe alte Nummer, aber ein echter Titel: Den hat jemand mit
        # TMDB-Schluessel angefragt, und er muss bleiben, wo er ist.
        echt = sitzung.get(MediaRequest, kennungen["Nordlicht"])
        sitzung.add(
            MediaRequest(
                user_id=echt.user_id,
                media_type=MediaType.movie,
                tmdb_id=PIXELHERZ_ALT,
                title="Kedvelem",
                status=echt.status,
                fassung_kennung=echt.fassung_kennung,
            )
        )
        sitzung.add(
            Favorite(
                user_id=kim["id"], media_type=MediaType.movie, tmdb_id=NORDLICHT_ALT,
                title="Nordlicht",
            )
        )
        sitzung.commit()
    return kennungen


def test_alte_beispieldaten_ziehen_beim_start_in_den_eigenen_bereich(
    arr_client: TestClient,
) -> None:
    kennungen = _alte_installation(arr_client)

    db_modul.init_db()

    with SessionLocal() as sitzung:
        pixelherz = sitzung.get(MediaRequest, kennungen["Pixelherz"])
        assert pixelherz.tmdb_id == _beispiel("movie", "Pixelherz").tmdb_id
        nordlicht = sitzung.get(MediaRequest, kennungen["Nordlicht"])
        assert nordlicht.tmdb_id == _beispiel("movie", "Nordlicht").tmdb_id
        grenzfall = sitzung.get(MediaRequest, kennungen["Grenzfall"])
        neu = _beispiel("tv", "Grenzfall")
        assert (grenzfall.tmdb_id, grenzfall.tvdb_id) == (neu.tmdb_id, neu.tvdb_id)

        echt = sitzung.query(MediaRequest).filter(MediaRequest.title == "Kedvelem").one()
        assert echt.tmdb_id == PIXELHERZ_ALT, "ein echter Titel wurde umgeschrieben"

        herz = sitzung.query(Favorite).one()
        assert herz.tmdb_id == _beispiel("movie", "Nordlicht").tmdb_id

    # Ein zweiter Start findet nichts mehr und aendert nichts.
    db_modul.init_db()
    with SessionLocal() as sitzung:
        assert (
            sitzung.query(MediaRequest).filter(MediaRequest.tmdb_id == PIXELHERZ_ALT).count()
            == 1
        )


# ---------------------------------------------------------------------------
# #note-12: ohne TMDB ehrlich
# ---------------------------------------------------------------------------


def test_ohne_tmdb_sagt_die_titelseite_eines_echten_titels_was_fehlt(
    admin_client: TestClient,
) -> None:
    """435011 liegt in Radarr; ohne TMDB gibt es keine Titelseite - aber einen Grund."""
    seite = admin_client.get("/api/detail/movie/435011")
    assert seite.status_code == 404
    assert seite.json()["detail"]["code"] == "title_needs_tmdb"
    assert "Demo" not in seite.json()["detail"]["message"]


def test_fest_eingeschaltete_beispieldaten_sagen_das_statt_nach_dem_schluessel_zu_fragen(
    admin_client: TestClient,
) -> None:
    assert admin_client.put(
        "/api/settings", json={"tmdb_api_key": "test-key", "demo_mode": "on"}
    ).status_code == 200

    seite = admin_client.get("/api/detail/movie/435011")
    assert seite.status_code == 404
    assert seite.json()["detail"]["code"] == "title_hidden_by_sample_data"


def test_ein_unbekannter_beispieltitel_bleibt_ein_unbekannter_beispieltitel(
    admin_client: TestClient,
) -> None:
    unbekannt = demo_data.BEISPIEL_ANFANG + 7
    seite = admin_client.get(f"/api/detail/movie/{unbekannt}")
    assert seite.status_code == 404
    assert seite.json()["detail"]["code"] == "sample_title_unknown"


def test_die_beispieltitel_selbst_oeffnen_sich_weiter(admin_client: TestClient) -> None:
    for art in ("movie", "tv"):
        item = demo_data.demo_items(art)[0]
        seite = admin_client.get(f"/api/detail/{art}/{item.tmdb_id}")
        assert seite.status_code == 200, seite.text
        assert seite.json()["title"] == item.title


def test_auch_die_schlanke_titelabfrage_nennt_den_grund(admin_client: TestClient) -> None:
    """``GET /api/media/...`` (Detailfenster, Startseite) lief ueber einen eigenen Weg.

    Er reichte nur den deutschen Satz durch, ohne Kennung - die englische
    Oberflaeche zeigte ihn deutsch (Pruefer zu #note-12).
    """
    antwort = admin_client.get("/api/media/movie/435011")
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "title_needs_tmdb"
