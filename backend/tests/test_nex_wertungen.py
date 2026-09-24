"""Portal-Wertungen im NEX-Betrieb: Stapel für Listen, Einzelansicht für die Titelseite.

Im ARR-Betrieb zeigt die Titelseite Rotten Tomatoes und Metacritic, weil
Radarrs ``/movie/lookup/tmdb`` sie mitliefert. nexcrates Stapel
(``POST /ratings``) trägt nur IMDb; die beiden anderen stehen allein in der
Einzelansicht ``GET /ratings/{kind}/{ref}``, zusammen mit der Namensnennung,
die OMDb verlangt. Bis zum 24.09.2026 rief Nexview die Einzelansicht nie, und
die Titelseite zeigte im NEX-Betrieb weniger als im ARR-Betrieb.

⚠️ **Listen dürfen die Einzelansicht nicht je Titel rufen.** Jeder Aufruf
kostet nexcrate eine OMDb-Abfrage aus einem Tageskontingent.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services.beschaffung import NEX
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system

from .beschaffung.fake_nexcrate import (
    IMDB_NENNUNG,
    KEY,
    OMDB_NENNUNG,
    URL,
    FakeNexcrate,
    _fehler,
)


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    attrappe = FakeNexcrate()
    nex_client.use_transport(attrappe.transport())
    system.merken(attrappe._system())
    nex_fassungen.vergessen()
    try:
        yield attrappe
    finally:
        nex_client.use_transport(None)
        system.vergessen()
        nex_fassungen.vergessen()


@pytest.fixture
def nex_admin(admin_client: TestClient, nexcrate: FakeNexcrate) -> TestClient:
    antwort = admin_client.put(
        "/api/settings",
        json={"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY},
    )
    assert antwort.status_code == 200, antwort.text
    return admin_client


def _einzeln(nexcrate: FakeNexcrate) -> list[str]:
    return [pfad for methode, pfad, _, _ in nexcrate.calls if methode == "GET" and "/ratings/" in pfad]


def _stapel(nexcrate: FakeNexcrate) -> list[list[dict]]:
    return [
        koerper["items"]
        for methode, pfad, _, koerper in nexcrate.calls
        if methode == "POST" and pfad.endswith("/ratings")
    ]


def test_die_titelseite_bekommt_tomaten_und_metacritic_samt_nennung(
    nex_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88, metacritic=71)

    antwort = nex_admin.get("/api/ratings/movie", params={"ids": "603", "detail": "true"})

    assert antwort.status_code == 200, antwort.text
    wert = antwort.json()["603"]
    assert wert["rotten_tomatoes"] == 88
    assert wert["metacritic"] == 71
    assert wert["imdb"] == 7.5
    assert wert["imdb_votes"] == 1200
    assert wert["imdb_id"] == "tt0000603"
    # OMDb verlangt die Nennung dort, wo die Werte stehen; der Wortlaut ist nexcrates.
    assert wert["attribution"] == [IMDB_NENNUNG, OMDB_NENNUNG]
    # Die Einzelansicht trägt IMDb mit - ein Aufruf statt zwei.
    assert _einzeln(nexcrate) == ["/api/v1/ratings/movie/tmdb:603"]
    assert _stapel(nexcrate) == []


def test_listen_fragen_nur_den_stapel(nex_admin: TestClient, nexcrate: FakeNexcrate) -> None:
    """Ohne ``detail`` nie die Einzelansicht - und IMDb steht unter ``rating``."""
    nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88, metacritic=71)
    nexcrate.wertung(604, imdb=6.1, stimmen=40)

    antwort = nex_admin.get("/api/ratings/movie", params={"ids": "603,604,605"})

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert _einzeln(nexcrate) == []
    assert len(_stapel(nexcrate)) == 1
    assert daten["603"]["imdb"] == 7.5 and daten["603"]["imdb_votes"] == 1200
    assert daten["603"]["rotten_tomatoes"] is None
    assert daten["604"]["imdb"] == 6.1
    assert daten["603"]["attribution"] == [IMDB_NENNUNG]
    # Ohne Wert kein Eintrag, wie im ARR-Betrieb.
    assert "605" not in daten


def test_detail_ruft_die_einzelansicht_nur_fuer_wenige_titel(
    nex_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    """Wer ``detail`` an eine lange Liste hängt, bekommt den Rest aus dem Stapel."""
    ids = list(range(701, 709))
    for nummer in ids:
        nexcrate.wertung(nummer, imdb=7.0, tomaten=50)

    antwort = nex_admin.get(
        "/api/ratings/movie", params={"ids": ",".join(map(str, ids)), "detail": "true"}
    )

    assert antwort.status_code == 200, antwort.text
    assert len(_einzeln(nexcrate)) == 5
    [stapel] = _stapel(nexcrate)
    assert len(stapel) == 3
    assert len(antwort.json()) == 8


@pytest.mark.parametrize(
    "ausfall",
    [
        _fehler(404, "imdb_unknown", "nexcrate knows no IMDb number for this title; ask by imdb:."),
        httpx.Response(500, text="Internal Server Error"),
        httpx.ConnectError("weg"),
    ],
    ids=["404", "500", "netz"],
)
def test_faellt_die_einzelansicht_fuer_einen_titel_aus_bleiben_die_anderen(
    nex_admin: TestClient, nexcrate: FakeNexcrate, ausfall: httpx.Response | Exception
) -> None:
    for nummer in (603, 604, 605):
        nexcrate.wertung(nummer, imdb=7.0, tomaten=60, metacritic=55)
    nexcrate.next_answer["GET /api/v1/ratings/movie/tmdb:604"] = ausfall

    antwort = nex_admin.get("/api/ratings/movie", params={"ids": "603,604,605", "detail": "true"})

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["603"]["rotten_tomatoes"] == 60
    assert daten["605"]["metacritic"] == 55
    assert "604" not in daten


@pytest.mark.parametrize("detail", [True, False], ids=["titelseite", "liste"])
def test_faellt_der_stapel_aus_steht_die_seite_trotzdem(
    nex_admin: TestClient, nexcrate: FakeNexcrate, detail: bool
) -> None:
    """⚠️ Ein 500 aus ``POST /ratings`` wurde bis zum 24.09.2026 selbst ein 500.

    Im ARR-Betrieb faengt ``portal_ratings`` einen Ausfall ab, im NEX-Betrieb
    flog er bis in den Router. Wertungen sind Beiwerk; die Einzelwerte der
    Titelseite bleiben, der Stapel faellt leer aus.
    """
    ids = list(range(801, 809))
    for nummer in ids:
        nexcrate.wertung(nummer, imdb=6.0, stimmen=10, tomaten=40)
    nexcrate.next_answer["POST /api/v1/ratings"] = httpx.Response(500, text="boom")

    params = {"ids": ",".join(map(str, ids))}
    if detail:
        params["detail"] = "true"
    antwort = nex_admin.get("/api/ratings/movie", params=params)

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    if detail:
        assert sorted(daten) == [str(nummer) for nummer in ids[:5]]
        assert daten["801"]["rotten_tomatoes"] == 40
    else:
        assert daten == {}
