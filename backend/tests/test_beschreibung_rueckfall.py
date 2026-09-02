"""Rueckfall auf die englische Beschreibung, wenn TMDB in der eingestellten
Sprache keinen Text fuehrt (Issue #6).

Geprueft wird ``media._englische_beschreibungen`` unmittelbar: Die drei Wege,
die sie benutzen (Listen, schlanke Einzelabfrage, Detailseite), unterscheiden
sich nur darin, *woher* die Karten kommen - was mit einer leeren Beschreibung
geschieht, entscheidet allein diese Stelle.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.db import SessionLocal
from app.models import MediaType
from app.schemas_media import MediaItem
from app.services import cache, media
from app.services.settings_service import AppSettings, load_settings


def _karte(tmdb_id: int, overview: str = "") -> MediaItem:
    return MediaItem(
        media_type=MediaType.tv,
        tmdb_id=tmdb_id,
        title=f"Titel {tmdb_id}",
        overview=overview,
        vote_average=0.0,
        vote_count=0,
    )


def _einstellungen(db: Any, sprache: str = "de") -> AppSettings:
    """Die echten Vorgaben, nur mit gesetztem TMDB-Key und Sprache.

    Bewusst ueber ``load_settings`` statt selbst gebaut: ohne Key laeuft
    Nexview im Beispielbetrieb, und der Rueckfall faellt dann ganz aus - der
    Test wuerde gruen sein, ohne je etwas geprueft zu haben.
    """
    return replace(
        load_settings(db),
        tmdb_api_key="test-key",
        default_language=sprache,
        default_region="DE",
    )


class _Fake:
    """Ein TMDB-Client, der zaehlt, wonach gefragt wurde."""

    antworten: dict[int, str] = {}
    ausfaelle: set[int] = set()
    gefragt: list[list[int]] = []
    sprachen: list[str] = []

    def __init__(self, api_key: str, language: str = "de", region: str = "DE") -> None:
        _Fake.sprachen.append(language)

    async def overviews(self, media_type: str, tmdb_ids: list[int]) -> dict[int, str]:
        _Fake.gefragt.append(list(tmdb_ids))
        return {
            tmdb_id: _Fake.antworten.get(tmdb_id, "")
            for tmdb_id in tmdb_ids
            if tmdb_id not in _Fake.ausfaelle
        }


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> type[_Fake]:
    _Fake.antworten = {}
    _Fake.ausfaelle = set()
    _Fake.gefragt = []
    _Fake.sprachen = []
    monkeypatch.setattr(media, "TmdbClient", _Fake)
    return _Fake


async def _lauf(items: list[MediaItem], sprache: str = "de") -> list[MediaItem]:
    with SessionLocal() as db:
        return await media._englische_beschreibungen(
            db, _einstellungen(db, sprache), "tv", items
        )


@pytest.mark.anyio
async def test_leere_beschreibung_wird_englisch_aufgefuellt(fake: type[_Fake]) -> None:
    fake.antworten = {331616: "A three-part documentary."}

    ergebnis = await _lauf([_karte(331616)])

    assert ergebnis[0].overview == "A three-part documentary."
    assert fake.gefragt == [[331616]]
    # Gefragt wird ausdruecklich auf Englisch - sonst kaeme derselbe leere Text.
    assert fake.sprachen == ["en-US"]


@pytest.mark.anyio
async def test_vorhandener_text_bleibt_unangetastet(fake: type[_Fake]) -> None:
    fake.antworten = {331616: "The English one."}

    ergebnis = await _lauf([_karte(331616, "Der deutsche Text.")])

    assert ergebnis[0].overview == "Der deutsche Text."
    # ⚠️ Der Kern der Sache: ohne leere Karte darf keine Abfrage laufen.
    assert fake.gefragt == []


@pytest.mark.anyio
async def test_derselbe_titel_zweimal_behaelt_seinen_text(fake: type[_Fake]) -> None:
    """Ein vorhandener Text wird auch dann nicht ersetzt, wenn der englische
    fuer denselben Titel gerade geholt wurde.

    Der Fall entsteht, sobald ein Titel zweimal in derselben Liste steht -
    einmal ohne und einmal mit Beschreibung. Die heutigen Aufrufer bauen ihre
    Listen aus einer TMDB-Antwort und liefern damit keine Wiederholungen; die
    Regel lautet trotzdem "vorhandener Text bleibt", nicht "vorhandener Text
    bleibt, solange niemand ihn doppelt schickt".
    """
    fake.antworten = {331616: "The English one."}

    ergebnis = await _lauf([_karte(331616), _karte(331616, "Der deutsche Text.")])

    assert [item.overview for item in ergebnis] == [
        "The English one.",
        "Der deutsche Text.",
    ]


@pytest.mark.anyio
async def test_nur_die_leeren_werden_gefragt(fake: type[_Fake]) -> None:
    fake.antworten = {2: "Second.", 3: "Third."}

    ergebnis = await _lauf([_karte(1, "Da."), _karte(2), _karte(3)])

    assert [item.overview for item in ergebnis] == ["Da.", "Second.", "Third."]
    assert fake.gefragt == [[2, 3]]


@pytest.mark.anyio
async def test_englische_oberflaeche_fragt_gar_nicht(fake: type[_Fake]) -> None:
    ergebnis = await _lauf([_karte(331616)], sprache="en")

    assert ergebnis[0].overview == ""
    assert fake.gefragt == []


@pytest.mark.anyio
async def test_zweiter_aufruf_kommt_aus_dem_zwischenspeicher(fake: type[_Fake]) -> None:
    fake.antworten = {331616: "A three-part documentary."}

    await _lauf([_karte(331616)])
    ergebnis = await _lauf([_karte(331616)])

    assert ergebnis[0].overview == "A three-part documentary."
    assert len(fake.gefragt) == 1, "die zweite Runde hat erneut bei TMDB gefragt"


@pytest.mark.anyio
async def test_auch_englisch_kein_text_wird_gemerkt(fake: type[_Fake]) -> None:
    """Ein leerer Text ist eine Auskunft - sonst laeuft die Abfrage ewig neu."""
    fake.antworten = {331616: ""}

    await _lauf([_karte(331616)])
    ergebnis = await _lauf([_karte(331616)])

    assert ergebnis[0].overview == ""
    assert len(fake.gefragt) == 1
    with SessionLocal() as db:
        assert cache.read(db, media._beschreibungs_schluessel("tv", 331616)) == ""


@pytest.mark.anyio
async def test_ausfall_wird_nicht_gemerkt(fake: type[_Fake]) -> None:
    """Ein Aussetzer von TMDB darf keine Woche lang eine leere Karte bedeuten."""
    fake.ausfaelle = {331616}

    ergebnis = await _lauf([_karte(331616)])
    assert ergebnis[0].overview == ""

    fake.ausfaelle = set()
    fake.antworten = {331616: "Now it works."}
    zweite = await _lauf([_karte(331616)])

    assert zweite[0].overview == "Now it works."
    assert len(fake.gefragt) == 2, "der Ausfall wurde gemerkt statt erneut gefragt"


# --- Sitzt der Rueckfall an allen drei Wegen? --------------------------------
#
# Die Tests oben pruefen den Helfer. Sie waeren genauso gruen, wenn er nur an
# *einer* der drei Stellen eingehaengt waere - und die zwei anderen zeigten
# weiter "Keine Beschreibung vorhanden". Deshalb hier einmal der ganze Weg:
# Listenkachel, schlanke Einzelabfrage und Detailseite.


class _FakeTmdb:
    """Der deutsche Client: liefert einen Titel ganz ohne Beschreibung."""

    def __init__(self, tmdb_id: int) -> None:
        self.tmdb_id = tmdb_id

    async def genres(self, media_type: str) -> dict[int, str]:
        return {18: "Dokumentation"}

    async def detail(
        self, media_type: str, tmdb_id: int, *, ausfuehrlich: bool = False
    ) -> dict[str, Any]:
        return self._roh()

    async def details(self, media_type: str, tmdb_ids: list[int]) -> dict[int, dict[str, Any]]:
        return {tmdb_id: self._roh() for tmdb_id in tmdb_ids}

    def _roh(self) -> dict[str, Any]:
        return {
            "id": self.tmdb_id,
            "name": "Death of the Pastor's Wife",
            "overview": "",  # genau der Fall aus Issue #6
            "first_air_date": "2026-08-26",
            "vote_average": 7.0,
            "vote_count": 12,
            "genre_ids": [18],
            "genres": [{"name": "Dokumentation"}],
            "seasons": [],
            "content_ratings": {"results": []},
            "external_ids": {"tvdb_id": 481321},
            "recommendations": {"results": []},
        }


@pytest.fixture
def wege(monkeypatch: pytest.MonkeyPatch, fake: type[_Fake]) -> type[_Fake]:
    fake.antworten = {331616: "A three-part documentary."}
    monkeypatch.setattr(media, "_client", lambda settings, region=None: _FakeTmdb(331616))
    return fake


@pytest.mark.anyio
async def test_listenkachel_bekommt_den_englischen_text(wege: type[_Fake]) -> None:
    with SessionLocal() as db:
        items = await media._to_items(
            db, _einstellungen(db), "tv", [{"id": 331616}], "DE"
        )

    assert items[0].overview == "A three-part documentary."


@pytest.mark.anyio
async def test_einzelabfrage_bekommt_den_englischen_text(wege: type[_Fake]) -> None:
    with SessionLocal() as db:
        item = await media.detail(db, _einstellungen(db), "tv", 331616)

    assert item.overview == "A three-part documentary."


@pytest.mark.anyio
async def test_detailseite_bekommt_den_englischen_text(wege: type[_Fake]) -> None:
    with SessionLocal() as db:
        detail = await media.full_detail(db, _einstellungen(db), "tv", 331616)

    assert detail.overview == "A three-part documentary."


# --- Staffeln und Folgen -----------------------------------------------------
#
# Derselbe Fall eine Ebene tiefer: Bei einer frisch angelaufenen Staffel fuehrt
# TMDB zu jeder Folge einen englischen Text und zu keiner einen deutschen.


class _FakeStaffel:
    """Liefert die Staffel je nach eingestellter Sprache."""

    deutsch: dict[str, Any] = {}
    englisch: dict[str, Any] = {}
    gefragt: list[str] = []
    ausfall = False

    def __init__(self, api_key: str = "", language: str = "de", region: str = "DE") -> None:
        self.language = language

    async def season(self, tmdb_id: int, season_number: int) -> dict[str, Any]:
        _FakeStaffel.gefragt.append(self.language)
        if self.language.startswith("en"):
            if _FakeStaffel.ausfall:
                from app.services.tmdb import TmdbError

                raise TmdbError("TMDB antwortet nicht.")
            return _FakeStaffel.englisch
        return _FakeStaffel.deutsch


@pytest.fixture
def staffel(monkeypatch: pytest.MonkeyPatch) -> type[_FakeStaffel]:
    _FakeStaffel.gefragt = []
    _FakeStaffel.ausfall = False
    _FakeStaffel.deutsch = {
        "season_number": 1,
        "name": "Staffel 1",
        "overview": "",
        "episodes": [
            {"episode_number": 1, "name": "Erste", "overview": ""},
            {"episode_number": 2, "name": "Zweite", "overview": "Die zweite auf Deutsch."},
        ],
    }
    _FakeStaffel.englisch = {
        "season_number": 1,
        "name": "Season 1",
        "overview": "The season in English.",
        "episodes": [
            {"episode_number": 1, "name": "First", "overview": "The first in English."},
            {"episode_number": 2, "name": "Second", "overview": "The second in English."},
        ],
    }
    monkeypatch.setattr(
        media,
        "_client",
        lambda settings, region=None: _FakeStaffel(language=settings.default_language),
    )
    monkeypatch.setattr(media, "TmdbClient", _FakeStaffel)
    # Die Altersschranke haengt an der Serie und wuerde hier eine eigene
    # TMDB-Abfrage ausloesen; sie ist nicht Gegenstand dieses Tests.
    monkeypatch.setattr(media, "erlaubte_kennungen", _alles_erlaubt)
    return _FakeStaffel


async def _alles_erlaubt(db: Any, settings: Any, media_type: str, tmdb_ids: list[int]) -> set[int]:
    return set(tmdb_ids)


async def _staffel_holen() -> Any:
    with SessionLocal() as db:
        return await media.season_detail(db, _einstellungen(db), 331616, 1)


@pytest.mark.anyio
async def test_folge_ohne_text_bekommt_den_englischen(staffel: type[_FakeStaffel]) -> None:
    ergebnis = await _staffel_holen()

    assert ergebnis.overview == "The season in English."
    assert ergebnis.episodes[0].overview == "The first in English."
    # Der vorhandene deutsche Text bleibt stehen.
    assert ergebnis.episodes[1].overview == "Die zweite auf Deutsch."


@pytest.mark.anyio
async def test_vollstaendige_staffel_fragt_nicht_nach(staffel: type[_FakeStaffel]) -> None:
    staffel.deutsch = {
        "season_number": 1,
        "name": "Staffel 1",
        "overview": "Die Staffel auf Deutsch.",
        "episodes": [{"episode_number": 1, "name": "Erste", "overview": "Da."}],
    }

    ergebnis = await _staffel_holen()

    assert ergebnis.overview == "Die Staffel auf Deutsch."
    assert staffel.gefragt == ["de"], "es wurde englisch nachgefragt, obwohl nichts fehlte"


@pytest.mark.anyio
async def test_eine_abfrage_fuer_die_ganze_staffel(staffel: type[_FakeStaffel]) -> None:
    await _staffel_holen()

    # Einmal deutsch, einmal englisch - nicht einmal je Folge.
    assert staffel.gefragt == ["de", "en-US"]


@pytest.mark.anyio
async def test_englische_oberflaeche_fragt_keine_staffel_nach(
    staffel: type[_FakeStaffel],
) -> None:
    """Bei englischer Oberflaeche bleibt es bei einer einzigen Abfrage.

    Der Text kommt dann schon beim ersten Mal auf Englisch; ein Rueckfall
    haette nichts, worauf er zurueckfallen koennte.
    """
    with SessionLocal() as db:
        ergebnis = await media.season_detail(db, _einstellungen(db, "en"), 331616, 1)

    assert ergebnis.episodes[0].overview == "The first in English."
    assert staffel.gefragt == ["en"], "es wurde ein zweites Mal auf Englisch gefragt"


@pytest.mark.anyio
async def test_ausfall_laesst_die_staffel_trotzdem_durch(staffel: type[_FakeStaffel]) -> None:
    staffel.ausfall = True

    ergebnis = await _staffel_holen()

    assert ergebnis.episodes[0].overview == ""
    assert ergebnis.episodes[1].overview == "Die zweite auf Deutsch."
    assert len(ergebnis.episodes) == 2


# --- Der englische Titel für Sonarrs Suche -----------------------------------
#
# TheTVDB ist englisch indiziert. In deutscher Oberfläche liefert TMDB für eine
# thailändische Serie Titel *und* Originaltitel auf Thai - damit findet Sonarr
# nichts. Live nachgemessen (Issue #5).


class _FakeTitel:
    antwort: dict[str, Any] = {}
    ausfall = False
    gefragt: list[str] = []

    def __init__(self, api_key: str = "", language: str = "de", region: str = "DE") -> None:
        _FakeTitel.gefragt.append(f"{language}|{region}")

    async def detail(self, media_type: str, tmdb_id: int, *, ausfuehrlich: bool = False):
        if _FakeTitel.ausfall:
            from app.services.tmdb import TmdbError

            raise TmdbError("TMDB antwortet nicht.")
        return _FakeTitel.antwort


@pytest.fixture
def titel(monkeypatch: pytest.MonkeyPatch) -> type[_FakeTitel]:
    _FakeTitel.antwort = {"name": "Still Water"}
    _FakeTitel.ausfall = False
    _FakeTitel.gefragt = []
    monkeypatch.setattr(media, "TmdbClient", _FakeTitel)
    return _FakeTitel


async def _titel_holen(sprache: str = "de", region: str = "DE") -> str | None:
    with SessionLocal() as db:
        # ``use_demo_data`` ist berechnet, nicht gesetzt: ``_einstellungen``
        # legt einen TMDB-Schlüssel hin, damit ist der Beispielbetrieb aus.
        einstellungen = replace(_einstellungen(db, sprache), default_region=region)
        return await media.englischer_titel(db, einstellungen, "tv", 331370)


@pytest.mark.anyio
async def test_englischer_titel_wird_geholt(titel: type[_FakeTitel]) -> None:
    assert await _titel_holen() == "Still Water"
    # Ausdrücklich auf Englisch - sonst käme derselbe thailändische Name.
    assert titel.gefragt == ["en-US|DE"]


@pytest.mark.anyio
async def test_englische_oberflaeche_holt_keinen_titel(titel: type[_FakeTitel]) -> None:
    """Dort ist der Titel aus ``item`` bereits der englische."""
    assert await _titel_holen(sprache="en") is None
    assert titel.gefragt == []


@pytest.mark.anyio
async def test_verschiedene_regionen_teilen_sich_einen_eintrag(
    titel: type[_FakeTitel],
) -> None:
    """⚠️ Jeder Benutzer darf seine eigene Region einstellen.

    Wie eine Serie auf **Englisch** heißt, hängt davon aber nicht ab: Gefragt
    wird mit ``en-US``. Ein Eintrag je Region wäre derselbe Name in zwanzig
    Kopien und zwanzigmal derselbe Aufruf an TMDB. Was wirklich an der Region
    hängt, steht weiterhin im regionsbehafteten Eintrag.
    """
    assert await _titel_holen(region="DE") == "Still Water"
    assert await _titel_holen(region="AT") == "Still Water"
    assert await _titel_holen(region="US") == "Still Water"

    assert len(titel.gefragt) == 1, "die Region hat einen zweiten Abruf ausgelöst"


@pytest.mark.anyio
async def test_titel_kommt_beim_zweiten_mal_aus_dem_zwischenspeicher(
    titel: type[_FakeTitel],
) -> None:
    await _titel_holen()
    await _titel_holen()

    assert len(titel.gefragt) == 1


@pytest.mark.anyio
async def test_ausfall_beim_titel_wird_nicht_gemerkt(titel: type[_FakeTitel]) -> None:
    """Sonst verkürzte ein einzelner Aussetzer eine Woche lang die Suche."""
    titel.ausfall = True
    assert await _titel_holen() is None

    titel.ausfall = False
    assert await _titel_holen() == "Still Water"
