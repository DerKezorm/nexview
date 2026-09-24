"""Portal-Wertungen im NEX-Betrieb: Stapel für Listen, Einzelansicht für die Titelseite.

Im ARR-Betrieb zeigt die Titelseite Rotten Tomatoes und Metacritic, weil
Radarrs ``/movie/lookup/tmdb`` sie mitliefert. Im NEX-Betrieb fragt nur
nexcrates Einzelansicht ``GET /ratings/{kind}/{ref}`` OMDb danach, zusammen
mit der Namensnennung, die OMDb verlangt. Bis zum 24.09.2026 rief Nexview die
Einzelansicht nie, und die Titelseite zeigte im NEX-Betrieb weniger als im
ARR-Betrieb.

Der Stapel (``POST /ratings``) trug bis nexcrate ``39dfc05`` nur IMDb. Seither
nennt er Rotten Tomatoes und Metacritic auch, aber nur aus nexcrates
30-Tage-Speicher (``not_cached`` sonst), und die OMDb-Nennung einmal für alle
in ``omdb_attribution``.

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
    assert daten["604"]["imdb"] == 6.1
    assert daten["604"]["attribution"] == [IMDB_NENNUNG]
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


# --- Rotten Tomatoes und Metacritic im Stapel (nexcrate 39dfc05) -------------------


def test_der_stapel_bringt_tomaten_und_metacritic_aus_dem_speicher(
    nex_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    """Was in nexcrates Speicher liegt, steht auch an Karten und Listen."""
    nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88, metacritic=71)
    nexcrate.wertung(604, imdb=6.1, stimmen=40, metacritic=55)

    antwort = nex_admin.get("/api/ratings/movie", params={"ids": "603,604"})

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["603"]["rotten_tomatoes"] == 88
    assert daten["603"]["metacritic"] == 71
    assert daten["603"]["imdb"] == 7.5
    assert daten["604"]["rotten_tomatoes"] is None
    assert daten["604"]["metacritic"] == 55
    assert daten["603"]["attribution"] == [IMDB_NENNUNG, OMDB_NENNUNG]
    assert daten["604"]["attribution"] == [IMDB_NENNUNG, OMDB_NENNUNG]
    # Nie die Einzelansicht: Die fragte OMDb, und das kostet.
    assert _einzeln(nexcrate) == []


def test_die_omdb_nennung_haengt_nur_an_zeilen_mit_einem_wert_von_dort(
    nex_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    """``omdb_attribution`` gilt dem Stapel; gezeigt wird sie je Zeile.

    Eine Zeile nur mit IMDb trägt nur IMDbs Satz, eine nur mit Portalen nur
    den von OMDb - wie die Einzelansicht, die OMDb nur nennt, wenn von dort
    etwas kam.
    """
    nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88)
    nexcrate.wertung(604, imdb=6.1, stimmen=40)
    nexcrate.wertung(605, tomaten=64, metacritic=58)
    nexcrate.wertung(606, imdb=5.0, stimmen=9, tomaten=30, im_speicher=False)

    daten = nex_admin.get("/api/ratings/movie", params={"ids": "603,604,605,606"}).json()

    assert daten["603"]["attribution"] == [IMDB_NENNUNG, OMDB_NENNUNG]
    assert daten["604"]["attribution"] == [IMDB_NENNUNG]
    assert daten["606"]["attribution"] == [IMDB_NENNUNG]
    assert daten["606"]["rotten_tomatoes"] is None
    # Nur Portale, kein IMDb: die Zeile steht trotzdem, ohne IMDbs Satz.
    assert daten["605"]["imdb"] is None and daten["605"]["imdb_votes"] is None
    assert daten["605"]["rotten_tomatoes"] == 64 and daten["605"]["metacritic"] == 58
    assert daten["605"]["attribution"] == [OMDB_NENNUNG]


@pytest.mark.parametrize("grund", ["not_cached", "no_key"])
def test_ohne_portale_im_stapel_bleibt_es_bei_imdb(
    nex_admin: TestClient, nexcrate: FakeNexcrate, grund: str
) -> None:
    """``null`` heißt: nicht im Speicher oder kein Schlüssel. Kein Fehler, keine Nennung."""
    if grund == "no_key":
        nexcrate.omdb_schluessel = False
        nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88, metacritic=71)
    else:
        nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88, metacritic=71, im_speicher=False)
    nexcrate.wertung(604, tomaten=50, im_speicher=False)
    stapel = nexcrate._ratings([{"kind": "movie", "ref": "tmdb:603"}])
    assert stapel["items"][0]["sources"]["omdb"] == grund
    assert stapel["omdb_attribution"] is None

    daten = nex_admin.get("/api/ratings/movie", params={"ids": "603,604"}).json()

    assert daten["603"]["imdb"] == 7.5
    assert daten["603"]["rotten_tomatoes"] is None and daten["603"]["metacritic"] is None
    assert daten["603"]["attribution"] == [IMDB_NENNUNG]
    # Ohne IMDb und ohne Portal gibt es nichts zu zeigen.
    assert "604" not in daten


def test_eine_aeltere_nexcrate_ohne_portale_im_stapel_bleibt_bei_imdb(
    nex_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    nexcrate.stapel_nur_imdb = True
    nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88, metacritic=71)
    nexcrate.wertung(605, tomaten=64)
    stapel = nexcrate._ratings([{"kind": "movie", "ref": "tmdb:603"}])
    assert "rotten_tomatoes" not in stapel["items"][0] and "omdb_attribution" not in stapel

    antwort = nex_admin.get("/api/ratings/movie", params={"ids": "603,605"})

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["603"]["imdb"] == 7.5 and daten["603"]["imdb_votes"] == 1200
    assert daten["603"]["rotten_tomatoes"] is None and daten["603"]["metacritic"] is None
    assert daten["603"]["attribution"] == [IMDB_NENNUNG]
    assert "605" not in daten


def test_die_titelseite_bleibt_bei_der_einzelansicht_auch_wenn_der_stapel_portale_kennt(
    nex_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    """Der Stapel kennt nur, was schon im Speicher liegt; die Einzelansicht holt den Rest."""
    nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88, metacritic=71, im_speicher=False)

    liste = nex_admin.get("/api/ratings/movie", params={"ids": "603"}).json()
    titelseite = nex_admin.get("/api/ratings/movie", params={"ids": "603", "detail": "true"}).json()

    assert liste["603"]["rotten_tomatoes"] is None
    assert titelseite["603"]["rotten_tomatoes"] == 88
    assert titelseite["603"]["attribution"] == [IMDB_NENNUNG, OMDB_NENNUNG]
    assert _einzeln(nexcrate) == ["/api/v1/ratings/movie/tmdb:603"]


# --- Filme, die nexcrate nicht führt (nexcrate 39dfc05) ----------------------------


def test_die_titelseite_zeigt_die_wertung_eines_films_den_nexcrate_nicht_fuehrt(
    nex_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    """nexcrate findet die IMDb-Nummer eines ``tmdb:``-Titels außerhalb bei TMDB.

    Nexview fragte schon vorher jeden Film, auch einen nicht geführten; eine
    ältere nexcrate antwortete dort nur ``imdb_unknown``.
    """
    nexcrate.film(604, name="Example Movie In Library")
    nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88, metacritic=71)
    assert ("movie", "tmdb:603") not in nexcrate.titles

    antwort = nex_admin.get("/api/ratings/movie", params={"ids": "603", "detail": "true"})

    assert antwort.status_code == 200, antwort.text
    wert = antwort.json()["603"]
    assert wert["imdb"] == 7.5 and wert["imdb_id"] == "tt0000603"
    assert wert["rotten_tomatoes"] == 88 and wert["metacritic"] == 71
    assert wert["attribution"] == [IMDB_NENNUNG, OMDB_NENNUNG]
    assert _einzeln(nexcrate) == ["/api/v1/ratings/movie/tmdb:603"]


def test_karten_nicht_gefuehrter_filme_bekommen_die_wertung_aus_dem_stapel(
    nex_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    nexcrate.film(604, name="Example Movie In Library")
    nexcrate.wertung(603, imdb=7.5, stimmen=1200)
    nexcrate.wertung(604, imdb=6.1, stimmen=40)

    daten = nex_admin.get("/api/ratings/movie", params={"ids": "603,604"}).json()

    assert daten["603"]["imdb"] == 7.5
    assert daten["604"]["imdb"] == 6.1
    [stapel] = _stapel(nexcrate)
    assert {"kind": "movie", "ref": "tmdb:603"} in stapel


@pytest.mark.parametrize("detail", [True, False], ids=["titelseite", "liste"])
def test_ohne_imdb_nummer_bleibt_ein_nicht_gefuehrter_film_still_leer(
    nex_admin: TestClient, nexcrate: FakeNexcrate, detail: bool
) -> None:
    """Eine ältere nexcrate oder eine ohne TMDB-Token: ``imdb_unknown``, kein Fehler.

    Der geführte Film daneben behält seine Wertung.
    """
    nexcrate.ausserhalb_aufloesen = False
    nexcrate.film(604, name="Example Movie In Library")
    nexcrate.wertung(603, imdb=7.5, stimmen=1200, tomaten=88)
    nexcrate.wertung(604, imdb=6.1, stimmen=40, tomaten=70)

    params = {"ids": "603,604"}
    if detail:
        params["detail"] = "true"
    antwort = nex_admin.get("/api/ratings/movie", params=params)

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert "603" not in daten
    assert daten["604"]["imdb"] == 6.1 and daten["604"]["rotten_tomatoes"] == 70
    if not detail:
        [stapel] = _stapel(nexcrate)
        assert len(stapel) == 2
