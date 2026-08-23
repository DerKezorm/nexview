"""Die Filtermenge fuer TMDB-Abfragen und ihr Zwischenspeicher-Schluessel."""

from __future__ import annotations

import dataclasses

import pytest

from app.services.filters import (
    MIN_VOTES_FOR_RATING,
    MIN_VOTES_FOR_RATING_SORT,
    DiscoverFilters,
)
from app.services.tmdb import TmdbClient


def _anderer_wert(feld: dataclasses.Field) -> object:
    """Irgendein Wert, der sich vom Standard unterscheidet."""
    standard = feld.default
    if isinstance(standard, bool):
        return not standard
    if isinstance(standard, int):
        return standard + 7
    if isinstance(standard, float):
        return standard + 1.5
    if isinstance(standard, str):
        return standard + "abweichend"
    # None-Vorbelegung: der Typ steht nur als Text in der Annotation.
    annotation = str(feld.type)
    if "int" in annotation:
        return 42
    if "float" in annotation:
        return 4.2
    return "abweichend"


def test_jedes_feld_trennt_den_zwischenspeicher() -> None:
    """Der gefaehrlichste Fehler dieser Datei, generisch abgesichert.

    ``cache_key`` zaehlt seine Felder **von Hand** auf. Wer eines vergisst,
    laesst zwei verschiedene Abfragen dieselbe Zeile im Zwischenspeicher
    benutzen - drei Stunden lang, ohne Fehlermeldung, und wer zuerst kommt,
    liefert dem anderen sein Ergebnis.

    Dieser Test laeuft ueber **alle** Felder statt sie aufzuzaehlen. Ein neu
    ergaenztes Feld ist damit automatisch abgedeckt; die alte Fassung des
    Tests haette dieselbe Luecke gehabt wie der Code.
    """
    standard = DiscoverFilters()
    vergessen: list[str] = []

    for feld in dataclasses.fields(DiscoverFilters):
        abweichend = dataclasses.replace(standard, **{feld.name: _anderer_wert(feld)})
        if abweichend.cache_key("movie") == standard.cache_key("movie"):
            vergessen.append(feld.name)

    assert not vergessen, (
        "Diese Felder fehlen in DiscoverFilters.cache_key(): " + ", ".join(vergessen)
    )


def test_textsprache_gehoert_in_den_schluessel() -> None:
    """Sonst bekommt der naechste Nutzer die Sprache des vorherigen."""
    filter_ = DiscoverFilters()
    assert filter_.cache_key("movie", "de") != filter_.cache_key("movie", "en")


def test_medienart_gehoert_in_den_schluessel() -> None:
    filter_ = DiscoverFilters()
    assert filter_.cache_key("movie") != filter_.cache_key("tv")


def test_praefix_ist_v4() -> None:
    """Steigt das Praefix nicht mit, mischen sich alte und neue Eintraege.

    Die neuen Felder (max_runtime, max_votes, without_genres, people_ids)
    veraendern die Anfrage an TMDB. Eintraege aus der Zeit davor wurden nach
    demselben Muster gebaut, bedeuten aber etwas anderes.
    """
    assert DiscoverFilters().cache_key("movie").startswith("discover:v4:")


# --- Die Untergrenze bei den Stimmen ---------------------------------------


def test_sortieren_nach_bewertung_verlangt_viele_stimmen() -> None:
    """Sonst ist "Beste Bewertung" wertlos.

    TMDB kennt keine gewichtete Sortierung. Mit der frueheren Untergrenze von
    5 Stimmen stand ein Film mit 20 Stimmen und 9,4 ueber "Der Pate".
    """
    assert MIN_VOTES_FOR_RATING_SORT >= 300
    grenze = TmdbClient._min_votes(DiscoverFilters(), "vote_average.desc")
    assert grenze == MIN_VOTES_FOR_RATING_SORT


def test_andere_sortierungen_bleiben_unbeschraenkt() -> None:
    """Die hohe Grenze gilt nur beim Sortieren nach Bewertung."""
    assert TmdbClient._min_votes(DiscoverFilters(), "popularity.desc") is None


@pytest.mark.parametrize(
    ("filter_", "sortierung", "erwartet"),
    [
        (DiscoverFilters(hide_unrated=True), "popularity.desc", 1),
        (DiscoverFilters(min_rating=7.0), "popularity.desc", MIN_VOTES_FOR_RATING),
        (DiscoverFilters(min_votes=20), "popularity.desc", 20),
        # Es gilt immer die schaerfste Regel, nicht die zuletzt gesetzte.
        (DiscoverFilters(hide_unrated=True, min_votes=20), "popularity.desc", 20),
        (DiscoverFilters(min_votes=20), "vote_average.desc", MIN_VOTES_FOR_RATING_SORT),
    ],
)
def test_schaerfste_regel_gewinnt(
    filter_: DiscoverFilters, sortierung: str, erwartet: int
) -> None:
    assert TmdbClient._min_votes(filter_, sortierung) == erwartet


# --- Welche Parameter tatsaechlich bei TMDB ankommen ------------------------


async def _gesendete_parameter(
    filter_: DiscoverFilters, media_type: str = "movie", sortierung: str = "popularity.desc"
) -> dict:
    """Ruft ``discover`` auf und faengt die Parameter ab, statt TMDB zu fragen.

    Die Pruefung sitzt bewusst hinter dem ``None``-Sieb aus ``_get``: Ein Feld,
    das nicht gesetzt ist, darf gar nicht erst gesendet werden.
    """
    gefangen: dict = {}
    client = TmdbClient("schluessel")

    async def _fake_get(path: str, params: dict | None = None) -> dict:
        gefangen.update({k: v for k, v in (params or {}).items() if v is not None})
        return {"results": [], "total_pages": 0, "total_results": 0}

    client._get = _fake_get  # type: ignore[method-assign]
    await client.discover(media_type, filter_, sortierung)
    return gefangen


@pytest.mark.asyncio
async def test_hoechstlaufzeit_wird_gesendet() -> None:
    """Die einzige Frage, die am Filmabend wirklich bindet.

    ``with_runtime.lte`` wurde nie gesendet - eine Obergrenze fuer die Laufzeit
    war schlicht nicht ausdrueckbar.
    """
    params = await _gesendete_parameter(DiscoverFilters(max_runtime=95))
    assert params["with_runtime.lte"] == 95


@pytest.mark.asyncio
async def test_stimmenfenster_wird_gesendet() -> None:
    """Beide Grenzen zusammen ergeben das Perlen-Fenster."""
    params = await _gesendete_parameter(DiscoverFilters(min_votes=200, max_votes=2500))
    assert params["vote_count.gte"] == 200
    assert params["vote_count.lte"] == 2500


@pytest.mark.asyncio
async def test_ausgeschlossene_genres_werden_gesendet() -> None:
    params = await _gesendete_parameter(DiscoverFilters(without_genres="27|53"))
    assert params["without_genres"] == "27|53"


@pytest.mark.asyncio
async def test_personen_werden_gesendet() -> None:
    params = await _gesendete_parameter(DiscoverFilters(people_ids="1245|287"))
    assert params["with_people"] == "1245|287"


@pytest.mark.asyncio
async def test_leere_felder_werden_nicht_gesendet() -> None:
    """Sonst schickt TMDB bei ``without_genres=""`` eine leere Ergebnisliste."""
    params = await _gesendete_parameter(DiscoverFilters())
    for name in ("without_genres", "with_people", "with_runtime.lte", "vote_count.lte"):
        assert name not in params


@pytest.mark.asyncio
async def test_serien_bekommen_kein_watch_region() -> None:
    """Ohne ``with_watch_providers`` daneben ignoriert TMDB den Parameter.

    Er stand jahrelang wirkungslos in jeder Serien-Abfrage und sah aus wie ein
    funktionierender Regionsfilter.
    """
    params = await _gesendete_parameter(DiscoverFilters(region="DE"), media_type="tv")
    assert "watch_region" not in params
