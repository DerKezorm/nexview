"""Die Filmreihe auf der Detailseite (Issue #9).

Unter der Besetzung stehen die uebrigen Filme der Reihe, zu der ein Film
gehoert. Jeder davon ist eine gewoehnliche Kachel mit dem gewoehnlichen
Anfrageweg. Geprueft wird hier deshalb nur, was die Reihe selbst ausmacht:
welche Filme darin stehen, in welcher Reihenfolge, und dass sie weder die
Detailseite noch die anderen Nutzer von ``full_detail`` belastet.

Dass kein gesperrter Teil durchkommt, prueft der Waechter in
``test_altersfilter_waechter.py``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.services import media
from app.services.settings_service import AppSettings, load_settings
from app.services.tmdb import TmdbError

from .conftest import auth_headers, create_user

#: Der Film, dessen Detailseite geoeffnet wird.
FILM = 910001
#: Die Reihe, zu der er gehoert.
REIHE = 4400


def _teil(tmdb_id: int, datum: str) -> dict[str, Any]:
    """Ein Film, wie er in TMDBs Reihe unter ``parts`` steht."""
    return {
        "id": tmdb_id,
        "title": f"Teil {tmdb_id}",
        "overview": "Eine Beschreibung.",
        "release_date": datum,
        "vote_average": 7.0,
        "vote_count": 100,
        "genre_ids": [28],
    }


class _FakeTmdb:
    """TMDB mit einem Film, der zu einer Reihe gehoert, und einem Zaehler."""

    def __init__(self) -> None:
        self.zugehoerig: dict[str, Any] | None = {"id": REIHE, "name": "Beispielreihe"}
        self.teile: list[dict[str, Any]] = []
        self.ausfall = False
        self.reihen_abrufe: list[int] = []

    async def genres(self, media_type: str) -> dict[int, str]:
        return {28: "Action"}

    async def detail(
        self, media_type: str, tmdb_id: int, *, ausfuehrlich: bool = False
    ) -> dict[str, Any]:
        return {
            **_teil(tmdb_id, "2010-06-01"),
            "belongs_to_collection": self.zugehoerig,
            "recommendations": {"results": []},
        }

    async def details(self, media_type: str, tmdb_ids: list[int]) -> dict[int, dict[str, Any]]:
        bekannt = {teil["id"]: teil for teil in self.teile}
        return {tmdb_id: bekannt.get(tmdb_id, _teil(tmdb_id, "")) for tmdb_id in tmdb_ids}

    async def collection(self, collection_id: int) -> dict[str, Any]:
        self.reihen_abrufe.append(collection_id)
        if self.ausfall:
            raise TmdbError("TMDB antwortet nicht.")
        return {"id": collection_id, "name": "Beispielreihe", "parts": list(self.teile)}


@pytest.fixture
def tmdb(monkeypatch: pytest.MonkeyPatch) -> _FakeTmdb:
    fake = _FakeTmdb()
    # Bewusst durcheinander, wie TMDB sie nicht garantiert sortiert liefert.
    fake.teile = [
        _teil(910003, "2019-06-20"),
        _teil(FILM, "2010-06-01"),
        _teil(910009, ""),
        _teil(910002, "2015-03-10"),
        _teil(910004, "2031-05-01"),
    ]
    monkeypatch.setattr(media, "_client", lambda settings, region=None: fake)
    monkeypatch.setattr(media, "TmdbClient", lambda *args, **kwargs: fake)
    return fake


def _einstellungen(db: Any) -> AppSettings:
    """Mit TMDB-Schluessel. Ohne ihn laeuft der Beispielbetrieb, und der kennt
    keine Reihen - jeder Test hier waere gruen, ohne etwas geprueft zu haben."""
    return replace(
        load_settings(db),
        tmdb_api_key="test-key",
        default_language="de",
        default_region="DE",
    )


async def _detail(*, mit_reihe: bool = True) -> Any:
    with SessionLocal() as db:
        return await media.full_detail(
            db, _einstellungen(db), "movie", FILM, mit_reihe=mit_reihe
        )


@pytest.mark.anyio
async def test_die_uebrigen_filme_nach_erscheinen(tmdb: _FakeTmdb) -> None:
    detail = await _detail()

    assert detail.collection is not None
    assert detail.collection.name == "Beispielreihe"
    # Der Film selbst steht nicht in seiner eigenen Reihe, ein Teil ohne Termin
    # steht am Ende.
    assert [item.tmdb_id for item in detail.collection.items] == [
        910002,
        910003,
        910004,
        910009,
    ]


@pytest.mark.anyio
async def test_ohne_reihe_wird_nichts_abgefragt(tmdb: _FakeTmdb) -> None:
    tmdb.zugehoerig = None

    detail = await _detail()

    assert detail.collection is None
    assert tmdb.reihen_abrufe == []


@pytest.mark.anyio
async def test_nur_die_titelseite_fragt_nach_der_reihe(tmdb: _FakeTmdb) -> None:
    """``full_detail`` bedient auch die Kinderansicht, die Anfrageliste der
    Verwaltung und die Vormerkungen.

    Keiner davon zeigt eine Reihe, und die Kinderansicht siebt nach eigenen
    Regeln. Dort waere jede Abfrage verschwendet, im schlimmsten Fall ein
    Schlupfloch.
    """
    detail = await _detail(mit_reihe=False)

    assert detail.collection is None
    assert tmdb.reihen_abrufe == []


@pytest.mark.anyio
async def test_ein_ausfall_kostet_nur_die_reihe(tmdb: _FakeTmdb) -> None:
    tmdb.ausfall = True

    detail = await _detail()
    assert detail.collection is None
    assert detail.title == f"Teil {FILM}"

    # Nicht gemerkt: Beim naechsten Aufruf wird wieder gefragt.
    tmdb.ausfall = False
    zweite = await _detail()
    assert zweite.collection is not None
    assert tmdb.reihen_abrufe == [REIHE, REIHE]


@pytest.mark.anyio
async def test_die_reihe_kommt_aus_dem_zwischenspeicher(tmdb: _FakeTmdb) -> None:
    await _detail()
    await _detail()

    assert tmdb.reihen_abrufe == [REIHE]


@pytest.mark.anyio
async def test_ohne_weitere_filme_keine_reihe(tmdb: _FakeTmdb) -> None:
    """Steht nur der Film selbst darin, gibt es nichts zu zeigen."""
    tmdb.teile = [_teil(FILM, "2010-06-01")]

    assert (await _detail()).collection is None


def test_kacheln_der_reihe_tragen_ihren_zustand(
    admin_client: TestClient, tmdb: _FakeTmdb
) -> None:
    """Die Reihe bekommt dieselben Abzeichen wie jede andere Liste.

    Ohne sie truege ein gesperrter Teil weiter seinen Einkaufswagen.
    """
    assert admin_client.put(
        "/api/settings", json={"tmdb_api_key": "test-key", "default_region": "DE"}
    ).status_code == 200
    gesperrt = admin_client.post(
        "/api/admin/blocklist",
        json={"media_type": "movie", "tmdb_id": 910003, "title": "Teil 910003"},
    )
    assert gesperrt.status_code == 201, gesperrt.text

    create_user(admin_client, "lena")
    kopf = auth_headers(admin_client, "lena", "passwort-1234")
    antwort = admin_client.get(f"/api/detail/movie/{FILM}", headers=kopf)

    assert antwort.status_code == 200, antwort.text
    zustand = {
        item["tmdb_id"]: item["status"] for item in antwort.json()["collection"]["items"]
    }
    assert zustand[910003] == "blocked"
    assert zustand[910002] == "not_requested"
