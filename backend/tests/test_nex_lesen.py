"""Die Lesewege im NEX-Betrieb: Bestand, Kacheln, Platz, Kalender, Downloads.

Gegen ``tests/beschaffung/fake_nexcrate.py``, also gegen die gemessenen
Antworten einer echten Wegwerf-nexcrate. Der echte Client laeuft dabei mit.

⚠️ **Was hier gruen ist, sagt nichts ueber den ARR-Betrieb.** Dass der sich
nicht bewegt hat, halten seine eigenen Tests fest - sie sind unveraendert
geblieben und muessen es bleiben.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    ArrGesundheit,
    DownloadHaenger,
    MediaRequest,
    MediaType,
    RequestStatus,
    Role,
    User,
)
from app.security import hash_password
from app.services import fassungen as fassungen_dienst
from app.services import status_poller, storage
from app.services.beschaffung import NEX, Aktion, Nachschlag, get_beschaffung
from app.services.beschaffung.nex import bestand as nex_bestand
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import downloads as nex_downloads
from app.services.beschaffung.nex import ereignisse, lesen, system
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, FILM_UHD, KEY, SERIE_HD, URL, FakeNexcrate

GB = 1024**3


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    attrappe = FakeNexcrate()
    nex_client.use_transport(attrappe.transport())
    system.merken(attrappe._system())
    nex_bestand.verwerfen()
    nex_fassungen.vergessen()
    try:
        yield attrappe
    finally:
        nex_client.use_transport(None)
        system.vergessen()
        nex_bestand.verwerfen()
        nex_fassungen.vergessen()


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as sitzung:
        yield sitzung


@pytest.fixture
def nex(db: Session, nexcrate: FakeNexcrate) -> Any:
    """Eine Installation im NEX-Betrieb, mit gelesenen Fassungen."""
    save_settings(db, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY})
    nex_fassungen.schreiben(db, nexcrate.versions)
    db.commit()
    return load_settings(db, frisch=True)


def _nutzer(db: Session) -> User:
    person = User(username="leser", password_hash=hash_password("test"), role=Role.user)
    db.add(person)
    db.commit()
    return person


def _anfrage(db: Session, person: User, **werte: Any) -> MediaRequest:
    grund = {
        "user_id": person.id,
        "media_type": MediaType.movie,
        "tmdb_id": 603,
        "title": "Example Movie",
        "fassung_kennung": FILM_HD,
        "status": RequestStatus.searching,
    }
    anfrage = MediaRequest(**{**grund, **werte})
    db.add(anfrage)
    db.commit()
    return anfrage


# --- Nachschlagen -------------------------------------------------------------


async def test_nachschlagen_fragt_einmal_fuer_alle(nex: Any, nexcrate: FakeNexcrate) -> None:
    """N12: ein Aufruf fuer viele Titel - nicht die ganze Bibliothek."""
    nexcrate.film(603)
    nexcrate.film(604, name="Another Example")
    weg = get_beschaffung(nex)

    antwort = await weg.nachschlagen(
        [
            Nachschlag(media_type="movie", fassung=FILM_HD, tmdb_id=603),
            Nachschlag(media_type="movie", fassung=FILM_HD, tmdb_id=604),
            Nachschlag(media_type="movie", fassung=FILM_HD, tmdb_id=999),
        ]
    )

    assert antwort.stand(Nachschlag("movie", FILM_HD, 603)).has_file is True
    assert antwort.stand(Nachschlag("movie", FILM_HD, 999)) is None
    assert antwort.hat_geantwortet(Nachschlag("movie", FILM_HD, 999)) is True
    assert len([k for k in nexcrate.calls if k[1].endswith("/titles/lookup")]) == 1


async def test_jede_fassung_wird_getrennt_beantwortet(nex: Any, nexcrate: FakeNexcrate) -> None:
    """⚠️ Der Kern des 4K-Umbaus: Eine 4K-Anfrage darf die 1080p-Datei nicht sehen."""
    nexcrate.film(603, versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=8 * GB)])
    weg = get_beschaffung(nex)

    antwort = await weg.nachschlagen(
        [
            Nachschlag("movie", FILM_HD, 603),
            Nachschlag("movie", FILM_UHD, 603),
        ]
    )

    assert antwort.stand(Nachschlag("movie", FILM_HD, 603)).has_file is True
    # Die Fassung gibt es am Titel nicht - also gibt es keinen Stand.
    assert antwort.stand(Nachschlag("movie", FILM_UHD, 603)) is None


async def test_eine_serie_kommt_mit_ihren_zahlen(nex: Any, nexcrate: FakeNexcrate) -> None:
    nexcrate.serie(1399)
    weg = get_beschaffung(nex)

    stand = (await weg.nachschlagen([Nachschlag("tv", SERIE_HD, 1399)])).stand(
        Nachschlag("tv", SERIE_HD, 1399)
    )

    assert stand.episode_file_count == 2 and stand.episode_count == 3
    assert stand.has_file is True
    assert stand.arr_id == 1399  # im NEX-Betrieb ist die TMDB-Nummer die Kennung


# --- Die Marke ------------------------------------------------------------------


async def test_der_bestand_liest_nur_geaendertes(nex: Any, nexcrate: FakeNexcrate) -> None:
    nexcrate.film(603)
    weg = get_beschaffung(nex)
    assert len(await weg.bestand_filme()) == 1

    nexcrate.film(604, name="Another Example")
    gefunden = await weg.bestand_filme()

    assert set(gefunden) == {603, 604}
    letzter = [k for k in nexcrate.calls if k[1].endswith("/titles")][-1]
    assert int(letzter[2]["after"]) > 0, "die zweite Runde las wieder von vorn"


async def test_ein_entfernter_titel_faellt_aus_dem_bestand(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    nexcrate.film(603)
    nexcrate.film(604, name="Another Example")
    weg = get_beschaffung(nex)
    await weg.bestand_filme()

    nexcrate.entfernt("movie", "tmdb:604")

    assert set(await weg.bestand_filme()) == {603}


async def test_eine_andere_installation_laesst_ganz_neu_lesen(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    """nexbeat-Befund 11: Eine gemerkte Marke gehoert **einer** Installation."""
    nexcrate.film(603)
    weg = get_beschaffung(nex)
    await weg.bestand_filme()

    nexcrate.installation_id = "eineandere00000"
    system.merken(nexcrate._system())
    nexcrate.film(604, name="Another Example")
    gefunden = await weg.bestand_filme()

    letzter = [k for k in nexcrate.calls if k[1].endswith("/titles")][-1]
    assert letzter[2]["after"] == "0", "die Marke der alten Installation galt weiter"
    assert set(gefunden) == {603, 604}


# --- Kacheln --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "monitored", "erwartet"),
    [("available", True, "downloaded"), ("wanted", True, "searching"), ("unmonitored", False, "searching")],
)
def test_der_zustand_einer_kachel(state: str, monitored: bool, erwartet: str) -> None:
    eintrag = {"versions": [{"version_id": FILM_HD, "state": state, "monitored": monitored}]}
    assert lesen.zustand_der_kachel(eintrag, FILM_HD) == erwartet


def test_eine_serie_mit_luecke_ist_nur_teilweise_da() -> None:
    """„Bereits geladen" auf einer Serie mit einer von elf Staffeln waere gelogen."""
    eintrag = {
        "versions": [
            {
                "version_id": SERIE_HD,
                "state": "available",
                "monitored": True,
                "series": {"counts": {"have": 2, "aired": 9, "expected": 10}},
            }
        ]
    }
    assert lesen.zustand_der_kachel(eintrag, SERIE_HD) == "partial"


async def test_die_kacheln_bekommen_den_stand_ihrer_fassung(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    from app.schemas_media import MediaItem

    nexcrate.film(603, versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=8 * GB)])
    weg = get_beschaffung(nex)
    kacheln = [MediaItem(tmdb_id=603, media_type=MediaType.movie, title="Example Movie")]

    haupt = await weg.status_setzen("movie", list(kacheln), fassung=FILM_HD)
    vier_k = await weg.status_setzen("movie", list(kacheln), fassung=FILM_UHD)

    assert haupt.items[0].status == "downloaded"
    assert vier_k.items[0].status != "downloaded"


# --- Warteschlange, Platz, Kalender ----------------------------------------------


def test_die_warteschlange_zaehlt_einen_download_einmal() -> None:
    """N26: Ein Staffelpaket ist **ein** Eintrag, nicht einer je Folge."""
    roh = [
        {
            "download_id": 7,
            "title": {"kind": "series", "ref": "tmdb:1399", "name": "Example Show"},
            "size_bytes": 1000,
            "remaining_bytes": 400,
            "series": {"season": 2, "episodes": [{"season": 2, "episode": 1}, {"season": 2, "episode": 2}]},
        }
    ]
    gefunden = lesen.warteschlange(roh, "tv")
    assert len(gefunden) == 1
    assert gefunden[0].arr_id == 1399 and gefunden[0].season == 2
    # Zwei Folgen: keine eindeutige Zuordnung zu **einer**.
    assert gefunden[0].episode is None
    assert gefunden[0].size == 1000 and gefunden[0].sizeleft == 400


def test_ein_gescheiterter_download_zaehlt_nicht_als_ladend() -> None:
    """Rundgang-Befund 9: nexcrate nennt ``remaining_bytes`` nur, solange ein
    Download laeuft. Ein gescheiterter hatte damit „0 Bytes uebrig“, und eine
    Anfrage auf denselben Titel stand bei 100 Prozent."""
    roh = [
        {
            "download_id": 8,
            "title": {"kind": "movie", "ref": "tmdb:603", "name": "Example Movie"},
            "state": "failed",
            "size_bytes": 1000,
            "remaining_bytes": None,
        },
        {
            "download_id": 9,
            "title": {"kind": "movie", "ref": "tmdb:604", "name": "Other Movie"},
            "state": "downloading",
            "size_bytes": 1000,
            "remaining_bytes": 250,
        },
    ]
    gefunden = lesen.warteschlange(roh, "movie")
    assert [(eintrag.arr_id, eintrag.sizeleft) for eintrag in gefunden] == [(604, 250)]


def test_dieselbe_platte_steht_nur_einmal_da() -> None:
    """Gemessen: nexcrate meldet den Platz je Fassung, nicht je Datentraeger."""
    roh = [
        {"version_id": FILM_HD, "volume": "volume-1", "free_bytes": 100, "total_bytes": 500},
        {"version_id": FILM_UHD, "volume": "volume-1", "free_bytes": 100, "total_bytes": 500},
        {"version_id": SERIE_HD, "volume": "volume-2", "free_bytes": 50, "total_bytes": 200},
    ]
    gefunden = lesen.datentraeger(roh)
    assert [p["path"] for p in gefunden] == ["volume-1", "volume-2"]


async def test_zwei_gleich_grosse_platten_bleiben_zwei_im_nex_betrieb(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    """``lesen.datentraeger`` faltet schon ueber ``volume`` - ``storage._traeger_nex``
    darf das nicht wieder aufheben, indem sie ueber die Groesse zusammenwirft. Zwei
    wirklich verschiedene Platten mit zufaellig derselben Gesamtgroesse duerfen nicht
    zu einer werden.
    """
    nexcrate.storage = [
        {"version_id": FILM_HD, "volume": "volume-1", "free_bytes": 10 * GB, "total_bytes": 500 * GB},
        {"version_id": FILM_UHD, "volume": "volume-2", "free_bytes": 20 * GB, "total_bytes": 500 * GB},
    ]

    gefunden = await storage.traeger(nex)
    assert len(gefunden) == 2

    frei, anzahl = await storage.freier_platz(nex)
    assert anzahl == 2
    assert frei == 30 * GB


async def test_vier_fassungen_auf_einer_platte_bleiben_eine_im_nex_betrieb(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    """Vier Fassungen auf derselben Platte sind eine Zeile, nicht vier."""
    nexcrate.storage = [
        {"version_id": v, "volume": "volume-1", "free_bytes": 10 * GB, "total_bytes": 500 * GB}
        for v in (FILM_HD, FILM_UHD, SERIE_HD, "v_weitere")
    ]

    gefunden = await storage.traeger(nex)
    assert len(gefunden) == 1
    assert gefunden[0].gesamt == 500 * GB


async def test_der_kalender_wird_in_stuecke_zerlegt(nex: Any, nexcrate: FakeNexcrate) -> None:
    """⚠️ Gemessen: mehr als hundert Tage am Stueck sind ``invalid_input``."""
    weg = get_beschaffung(nex)

    await weg.kalender("tv", "2026-01-01", "2026-12-31")

    spannen = [k[2] for k in nexcrate.calls if k[1].endswith("/calendar")]
    assert len(spannen) >= 4
    assert all(s["from"] <= s["to"] for s in spannen)


async def test_eine_folge_im_kalender_traegt_staffel_und_nummer(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    nexcrate.calendar_items = [
        {
            "kind": "series",
            "ref": "tmdb:1399",
            "name": "Example Show",
            "year": 2011,
            "date": "2026-02-03",
            "date_kind": "air",
            "monitored": True,
            "versions": [{"version_id": SERIE_HD, "state": "wanted", "monitored": True}],
            "series": {"season": 1, "episode": 3, "name": "Episode 3"},
        }
    ]
    gefunden = await get_beschaffung(nex).kalender("tv", "2026-02-01", "2026-02-28")

    assert len(gefunden) == 1
    assert gefunden[0]["seasonNumber"] == 1 and gefunden[0]["episodeNumber"] == 3
    assert gefunden[0]["series"]["tmdbId"] == 1399
    assert gefunden[0]["hasFile"] is False


async def test_wertungen_kommen_im_stapel(nex: Any, nexcrate: FakeNexcrate) -> None:
    gefunden = await get_beschaffung(nex).wertungen_filme([603, 604])

    aufrufe = [k for k in nexcrate.calls if k[1].endswith("/ratings")]
    assert len(aufrufe) == 1 and len(aufrufe[0][3]["items"]) == 2
    # Ohne eingerichtete Quelle sind alle Werte ``null`` - kein Fehler.
    assert gefunden == {}


# --- Gesundheit und Downloads ------------------------------------------------------


async def test_die_gesundheit_landet_in_der_zeile(nex: Any, nexcrate: FakeNexcrate, db: Session) -> None:
    nexcrate.health = [
        {"code": "indexer_none", "level": "error", "message": "No indexer.", "params": {}},
        {"code": "automatic_off", "level": "warning", "message": "off", "params": {"kind": "movie"}},
        {"code": "nur_ein_hinweis", "level": "info", "message": "hm", "params": {}},
    ]

    await get_beschaffung(nex).gesundheit_pruefen(db)

    zeile = db.get(ArrGesundheit, 1)
    assert zeile is not None and zeile.kennung == "nexcrate"
    kennungen = [p["schluessel"] for p in zeile.stand]
    # ``info`` ist kein Problem, und der Schluessel traegt die Werte mit.
    assert kennungen == ["indexer_none", "automatic_off:movie"]


async def test_die_glocke_nennt_nexcrate_nicht_radarr(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Meldung kam mit dem Schluessel des Arr-Wegs: "Radarr/Sonarr meldet ein Problem".

    ⚠️ **Nie nexcrates Satz, auch nicht im Titel** (Rundgang-Befund 5): Der
    Titel war ``"nexcrate: <englischer Satz>"``. Jetzt traegt die Glocke die
    Kennung als Schluessel, wo ihr Text ohne Platzhalter auskommt, sonst den
    allgemeinen Satz; der Titel ist nur der Name.
    """
    from app.models import Notification

    db.add(User(username="chef", password_hash=hash_password("test"), role=Role.admin))
    db.commit()
    nexcrate.health = [
        {"code": "indexer_none", "level": "error", "message": "No indexer.", "params": {}},
        {"code": "automatic_off", "level": "warning", "message": "The automatic for movie is off.",
         "params": {"kind": "movie"}},
        {"code": "brandneu", "level": "warning", "message": "Something new.", "params": {}},
    ]

    await get_beschaffung(nex).gesundheit_pruefen(db)

    meldungen = db.query(Notification).order_by(Notification.id).all()
    assert [m.message_key for m in meldungen] == [
        "nexcrate.health.indexer_none",
        "nexcrate.health.automatic_off_movie",
        "notifications.instanceHealth_nex",
    ]
    assert {m.message_title for m in meldungen} == {"nexcrate"}


def test_die_glocke_hat_jeden_text() -> None:
    """Jeder Schluessel, den die Glocke bekommen kann, steht in beiden Sprachen
    und ohne Platzhalter: Die Glocke zeigt keine Werte."""
    import json
    from pathlib import Path

    from app.services.beschaffung.nex import gesundheit

    sprachen = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"
    for sprache in ("de", "en"):
        texte = json.loads((sprachen / f"{sprache}.json").read_text(encoding="utf-8"))
        assert len(gesundheit.GLOCKE) >= 8
        for kennung in gesundheit.GLOCKE:
            text = texte["nexcrate"]["health"][kennung]
            assert text and "{{" not in text, (sprache, kennung)
        allgemein = texte["notifications"]["instanceHealth_nex"]
        assert "nexcrate" in allgemein and "Radarr" not in allgemein, (sprache, allgemein)


async def test_musik_meldet_nexview_nichts(nex: Any, nexcrate: FakeNexcrate, db: Session) -> None:
    """Rundgang-Befund 5: nexcrate meldet ``automatic_off`` je Art, auch fuer
    ``album``; Nexview zeigte daraus „Die Automatik ist aus“, obwohl Filme und
    Serien an waren. Musik fuehrt Nexview nicht."""
    nexcrate.health = [
        {"code": "automatic_off", "level": "warning", "message": "music off", "params": {"kind": "album"}},
        {"code": "automatic_off", "level": "warning", "message": "off", "params": {"kind": "series"}},
        {"code": "indexer_none", "level": "error", "message": "No indexer.", "params": {}},
    ]

    await get_beschaffung(nex).gesundheit_pruefen(db)

    zeile = db.get(ArrGesundheit, 1)
    assert zeile is not None
    assert [p["schluessel"] for p in zeile.stand] == ["automatic_off:series", "indexer_none"]


async def test_nur_was_den_betreiber_braucht_wird_ein_haenger(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.queue = [
        {
            "download_id": 1,
            "title": {"kind": "movie", "ref": "tmdb:603", "name": "Example Movie"},
            "state": "downloading",
            "progress": 40.0,
            "size_bytes": 1000,
            "remaining_bytes": 600,
            "remaining_seconds": 90,
            "problem": None,
        },
        {
            "download_id": 2,
            "title": {"kind": "movie", "ref": "tmdb:604", "name": "Another"},
            "state": "problem",
            "size_bytes": 10,
            "problem": {
                "code": "no_video",
                "needs_owner": True,
                "message": "No video in the download.",
                "params": {},
                "actions": ["remove_and_search", "remove"],
                "automatic": ["remove_and_search"],
            },
        },
        {
            "download_id": 3,
            "title": {"kind": "movie", "ref": "tmdb:605", "name": "Third"},
            "state": "downloading",
            "size_bytes": 10,
            "problem": {
                "code": "slow",
                "needs_owner": False,
                "message": "slow",
                "params": {},
                "actions": [],
                "automatic": [],
            },
        },
    ]

    rundgang = await get_beschaffung(nex).downloads_auffrischen(db)

    # Alle drei stehen auf der Seite - aber nur einer ist ein Haenger.
    assert len(rundgang.abfragen[0].downloads) == 3
    zeilen = db.query(DownloadHaenger).all()
    assert [z.download_id for z in zeilen] == ["2"]
    assert zeilen[0].grund == "no_video"
    assert zeilen[0].aktionen == {
        "erlaubt": ["remove_and_search", "remove"],
        "automatisch": ["remove_and_search"],
    }


def test_nur_nexcrates_aktionen_werden_angeboten() -> None:
    """Bauplan 6.6: Nexviews eigene Tabelle gilt im NEX-Betrieb nicht."""
    zeile = DownloadHaenger(
        kennung="nexcrate",
        download_id="2",
        media_type="movie",
        grund="no_video",
        aktionen={"erlaubt": ["remove_and_search", "remove"], "automatisch": ["remove_and_search"]},
    )
    assert nex_downloads.erlaubte_aktionen(zeile) == [
        Aktion.entfernen_neu_suchen,
        Aktion.entfernen,
    ]
    assert nex_downloads.darf_automatik(zeile, Aktion.entfernen_neu_suchen) is True
    assert nex_downloads.darf_automatik(zeile, Aktion.entfernen) is False
    assert nex_downloads.nexcrate_name(zeile, Aktion.entfernen) == "remove"


def test_ohne_gespeicherte_aktionen_gibt_es_keine_knoepfe() -> None:
    """Ein Knopf, der ins Leere greift, ist schlimmer als keiner."""
    zeile = DownloadHaenger(kennung="nexcrate", download_id="2", media_type="movie", grund="x")
    assert nex_downloads.erlaubte_aktionen(zeile) == []


# --- Ereignisse ---------------------------------------------------------------------


def test_ein_ereignis_weckt_das_richtige() -> None:
    wecker = ereignisse.Wecker()
    wecker.merken("download.failed")
    wecker.merken("version_definition.changed")
    assert wecker.downloads and wecker.fassungen
    assert not wecker.titel

    # Eine unbekannte Art ist kein Fehler - geweckt wird trotzdem.
    wecker.merken("etwas.ganz.neues")
    assert wecker.titel


def test_die_ereignis_marke_gehoert_einer_installation(nex: Any, nexcrate: FakeNexcrate, db: Session) -> None:
    save_settings(db, {"nexcrate_events_after": f"{nexcrate.installation_id}:17"})
    frisch = load_settings(db, frisch=True)
    assert ereignisse.marke_lesen(frisch) == 17

    save_settings(db, {"nexcrate_events_after": "eineandere:17"})
    frisch = load_settings(db, frisch=True)
    assert ereignisse.marke_lesen(frisch) == 0


async def test_nachholen_ordnet_ein_und_merkt_sich_die_marke(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.ereignis("download.failed", download_id=5)
    nexcrate.ereignis("title.changed", title={"kind": "movie", "ref": "tmdb:603"})

    gezaehlt = await ereignisse.nachholen(nex)

    assert gezaehlt == 2
    wecker = ereignisse.wecker()
    assert wecker.downloads and wecker.titel
    gemerkt = load_settings(db, frisch=True).nexcrate_events_after
    assert gemerkt.startswith(f"{nexcrate.installation_id}:")
    wecker.abholen()


# --- Der Takt-Laeufer ----------------------------------------------------------------


async def test_eine_anfrage_wird_im_nex_betrieb_fertig(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der ganze Weg: nexcrate sagt „liegt da", und die Anfrage ist fertig."""
    nexcrate.film(603, versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=8 * GB)])
    person = _nutzer(db)
    anfrage = _anfrage(db, person, arr_id=603)

    fertig = await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert fertig == 1
    assert anfrage.status == RequestStatus.downloaded


async def test_ein_titel_der_aus_nexcrate_verschwindet_beendet_die_anfrage(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    person = _nutzer(db)
    anfrage = _anfrage(
        db,
        person,
        arr_id=603,
        requested_at=datetime(2020, 1, 1, tzinfo=UTC).replace(tzinfo=None),
    )

    await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.cancelled


async def test_ohne_antwort_wird_keine_anfrage_abgebrochen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ „Weg" und „nicht gefragt" sind zweierlei - sonst raeumt ein Ausfall auf."""
    import httpx

    person = _nutzer(db)
    anfrage = _anfrage(db, person, arr_id=603)
    nexcrate.next_answer["POST /api/v1/titles/lookup"] = httpx.ConnectError("nope")

    with pytest.raises(Exception):  # noqa: B017 - der Rundgang faengt es selbst
        await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.searching


# --- Speicher ----------------------------------------------------------------------


async def test_der_speicher_zaehlt_im_nex_betrieb_mit(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Und der Schluessel traegt die Fassung aus nexcrate (Bauplan 6.5)."""
    nexcrate.film(603, versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=8 * GB)])

    ergebnis = await storage.abgleichen(db, nex)

    assert ergebnis.neu == 1
    schluessel = storage.schluessel(MediaType.movie, FILM_HD, tmdb_id=603)
    assert schluessel == f"movie:{FILM_HD}:tmdb:603"
    assert storage.hausbestand(db).used_bytes == 8 * GB


def test_eine_serie_haengt_im_nex_betrieb_an_tmdb() -> None:
    """Im ARR-Betrieb an TVDB, im NEX-Betrieb an TMDB - die Fassung entscheidet."""
    assert storage.schluessel(
        MediaType.tv, SERIE_HD, tmdb_id=1399, tvdb_id=121361, season=3
    ) == f"tv:{SERIE_HD}:tmdb:1399:s3"
    assert storage.schluessel(
        MediaType.tv, "sonarr-standard", tmdb_id=1399, tvdb_id=121361, season=3
    ) == "tv:sonarr-standard:tvdb:121361:s3"


def test_die_quelle_einer_fassung_steht_fest() -> None:
    assert fassungen_dienst.quelle("radarr-standard") == "arr"
    assert fassungen_dienst.quelle(FILM_HD) == NEX
    assert fassungen_dienst.quelle("") == NEX


# --- Die Instanz -------------------------------------------------------------------


def test_im_nex_betrieb_gibt_es_genau_eine_instanz(nex: Any) -> None:
    eigene = get_beschaffung(nex).instanzen()
    assert len(eigene) == 1
    assert eigene[0].kennung == "nexcrate"
    assert eigene[0].url == URL


def test_ohne_zugang_gibt_es_keine_instanz(db: Session, nexcrate: FakeNexcrate) -> None:
    save_settings(db, {"beschaffung": NEX})
    assert get_beschaffung(load_settings(db, frisch=True)).instanzen() == ()


async def test_die_messung_nennt_version_und_update(nex: Any, nexcrate: FakeNexcrate) -> None:
    nexcrate.update = {"current": "0.1.0", "latest": "0.2.0", "available": True, "checked_at": None}
    weg = get_beschaffung(nex)

    messung = await weg.instanz_messen(weg.instanzen()[0], voll=True)

    assert messung.erreichbar and messung.version == "0.1.0"
    assert messung.messwerte["aktualisierung"] == "0.2.0"
    assert messung.messwerte["warteschlange"] == {"gesamt": 0, "gestoert": 0}


async def test_die_messung_zaehlt_gescheiterte_nicht_als_laufend(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    """Rundgang-Befund 9: „wie viele Downloads laufen“ zaehlt keinen
    gescheiterten ohne Problem; einer, der auf den Betreiber wartet, bleibt
    gestoert."""
    nexcrate.queue = [
        {"download_id": 1, "title": {"kind": "movie", "ref": "tmdb:603"}, "state": "downloading"},
        {"download_id": 2, "title": {"kind": "movie", "ref": "tmdb:604"}, "state": "failed"},
        {
            "download_id": 3,
            "title": {"kind": "movie", "ref": "tmdb:605"},
            "state": "failed",
            "problem": {"code": "download_failed", "needs_owner": True},
        },
    ]
    weg = get_beschaffung(nex)

    messung = await weg.instanz_messen(weg.instanzen()[0], voll=True)

    assert messung.messwerte["warteschlange"] == {"gesamt": 2, "gestoert": 1}


async def test_eine_stumme_nexcrate_gilt_als_nicht_erreichbar(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    import httpx

    nexcrate.next_answer["GET /api/v1/system"] = httpx.ConnectError("nope")
    weg = get_beschaffung(nex)

    messung = await weg.instanz_messen(weg.instanzen()[0], voll=False)

    assert messung.erreichbar is False


async def test_der_rundgang_laeuft_auch_im_nex_betrieb(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Der Rundgang fragte „gibt es Arr-Instanzen?" - im NEX-Betrieb: keine.

    Ohne diese Stelle liefe er leer mit: kein Speicher-Abgleich, keine
    Gesundheit, keine Anfrage, die je fertig wird. Ein Ausfall, den niemand
    sieht, weil nichts scheitert - es passiert nur nichts.
    """
    nexcrate.film(603, versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=8 * GB)])
    monkeypatch.setattr(status_poller, "_speicher_zuletzt", 0.0)
    monkeypatch.setattr(status_poller, "SPEICHER_INTERVALL_SEKUNDEN", 0)

    await status_poller._speicher_vielleicht(db, nex)

    assert storage.hausbestand(db).used_bytes == 8 * GB


def test_die_einstellungen_bleiben_im_arr_betrieb_unberuehrt(db: Session) -> None:
    """Die Gegenprobe: Ohne Umschalten aendert Scheibe 5 nichts."""
    einstellungen = load_settings(db)
    assert einstellungen.beschaffung == "arr"
    assert get_beschaffung(einstellungen).instanzen() == einstellungen.arr_instanzen()
