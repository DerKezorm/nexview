"""Der Umstieg von Radarr und Sonarr auf nexcrate (Bauplan 7.1 bis 7.4).

⚠️ **Der gefährlichste Code im ganzen NEX-Modus.** Die Wanderung schreibt
Fassungskennungen an Anfragen, Posten, Rechten, Einladungen und Regeln um und
bei Serien zusätzlich den Speicherschlüssel von TVDB auf TMDB. Ein Schlüssel,
der danach nicht mehr zum Posten passt, heißt: Der Posten ist für Nexview weg,
und die Zurechnung des ganzen Hauses stimmt nicht mehr - ohne dass irgendetwas
rot würde.

Geprüft wird deshalb nicht „läuft durch", sondern jede einzelne Zeile, die
hinterher anders dasteht.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    AuthToken,
    Fassung,
    FassungRecht,
    MediaRequest,
    MediaType,
    Regel,
    RequestStatus,
    Role,
    StorageEntry,
    StorageState,
    TokenPurpose,
    User,
    utcnow,
)
from app.security import hash_password
from app.services import storage, umstieg
from app.services.beschaffung import ARR, NEX, SPERRT, WARNT, get_beschaffung
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import gesundheit, pruefung, system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, FILM_UHD, KEY, SERIE_HD, URL, FakeNexcrate

#: Die vier Fassungen, die der ARR-Betrieb fest mitbringt.
RADARR = "radarr-standard"
RADARR_UHD = "radarr-uhd"
SONARR = "sonarr-standard"
SONARR_UHD = "sonarr-uhd"


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
def db() -> Iterator[Session]:
    with SessionLocal() as sitzung:
        yield sitzung


@pytest.fixture
def vor_dem_umstieg(db: Session, nexcrate: FakeNexcrate) -> Any:
    """Eine Installation im ARR-Betrieb, die nexcrate schon erreichen kann.

    Genau der Zustand des Assistenten: ``beschaffung`` steht noch auf ``arr``,
    der Zugang zu nexcrate liegt (Schritt 2 ist durch).
    """
    save_settings(db, {"beschaffung": ARR, "nexcrate_url": URL, "nexcrate_api_key": KEY})
    nex_fassungen.schreiben(db, nexcrate.versions)
    db.commit()
    return load_settings(db, frisch=True)


def _abbildung() -> dict[str, str | None]:
    return {
        RADARR: FILM_HD,
        RADARR_UHD: FILM_UHD,
        SONARR: SERIE_HD,
        SONARR_UHD: None,
    }


# --------------------------------------------------------------------------
# 7.2 Die Standprüfung


def test_ein_fremder_hauptvertrag_sperrt_und_haelt_die_pruefung_an() -> None:
    befunde = pruefung.pruefen({"contract": {"major": 2, "stage": "V9"}, "scopes": []})

    # ⚠️ Genau **ein** Befund: Bei einem anderen Hauptvertrag heißen die Felder
    # nicht mehr dasselbe. Weiterzuprüfen hieße, fremde Felder zu deuten.
    assert [(b.code, b.stufe) for b in befunde] == [("nexcrate_vertrag_fremd", SPERRT)]


def test_eine_zu_alte_nexcrate_sperrt() -> None:
    daten = {
        "contract": {"major": 1, "stage": "V3"},
        "capabilities": {"movies": True, "series": True, "wishes_search_at_once": True},
        "scopes": ["read", "request", "operate"],
    }
    codes = {b.code: b.stufe for b in pruefung.pruefen(daten)}
    assert codes["nexcrate_zu_alt"] == SPERRT


def test_jedes_fehlende_recht_wird_einzeln_genannt() -> None:
    daten = {
        "contract": {"major": 1, "stage": "V5"},
        "capabilities": {"movies": True, "series": True, "wishes_search_at_once": True},
        "scopes": ["read"],
    }
    rechte = sorted(
        str(b.werte.get("recht")) for b in pruefung.pruefen(daten) if b.code == "nexcrate_recht_fehlt"
    )
    assert rechte == ["operate", "request"]


def test_wuensche_und_anime_warnen_nur() -> None:
    daten = {
        "contract": {"major": 1, "stage": "V5"},
        "capabilities": {
            "movies": True,
            "series": True,
            "wishes_search_at_once": False,
            "anime": False,
        },
        "scopes": ["read", "request", "operate"],
    }
    befunde = {b.code: b.stufe for b in pruefung.pruefen(daten)}
    assert befunde == {"nexcrate_wuensche_warten": WARNT, "nexcrate_ohne_anime": WARNT}
    assert not pruefung.sperrt(pruefung.pruefen(daten))


def test_ohne_gelesenen_stand_wird_nichts_behauptet() -> None:
    """⚠️ Leer heißt „noch nicht gefragt", nicht „fremder Vertrag"."""
    assert pruefung.pruefen({}) == []


def test_die_gemessene_nexcrate_besteht_die_pruefung(nexcrate: FakeNexcrate) -> None:
    assert pruefung.pruefen(nexcrate._system()) == []


def test_zwei_fehlende_rechte_bleiben_im_rundgang_zwei_befunde() -> None:
    """⚠️ Der Schlüssel fürs Entprellen trägt den Wert, nicht nur die Kennung.

    Sonst bliebe von „request fehlt" und „operate fehlt" einer übrig, und der
    Betreiber sucht nach dem zweiten.
    """
    daten = {
        "contract": {"major": 1, "stage": "V5"},
        "capabilities": {"movies": True, "series": True, "wishes_search_at_once": True},
        "scopes": [],
    }
    verdichtet = gesundheit.verdichten(pruefung.als_health(pruefung.pruefen(daten)))
    assert len({eintrag["schluessel"] for eintrag in verdichtet}) == 2


async def test_der_arr_weg_hat_keinen_vertragsstand_zu_pruefen(db: Session) -> None:
    save_settings(db, {"beschaffung": ARR})
    assert await get_beschaffung(load_settings(db, frisch=True)).pruefen() == []


async def test_der_nex_weg_prueft_gegen_die_frische_antwort(
    vor_dem_umstieg: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.scopes = ["read"]
    befunde = await get_beschaffung(umstieg.nex_sicht(db)).pruefen()
    assert {b.code for b in befunde} == {"nexcrate_recht_fehlt"}


# --------------------------------------------------------------------------
# 7.3 Schritt 3: die Abbildung


def test_der_vorschlag_paart_nach_medienart_und_klasse(
    vor_dem_umstieg: Any, db: Session
) -> None:
    gefunden = umstieg.vorschlag(db, list(get_beschaffung(umstieg.nex_sicht(db)).fassungen()))
    assert gefunden == {
        RADARR: FILM_HD,
        RADARR_UHD: FILM_UHD,
        SONARR: SERIE_HD,
        SONARR_UHD: "v_66260bea",
    }


def test_eine_serie_auf_eine_filmfassung_ist_ein_fehler(
    vor_dem_umstieg: Any, db: Session
) -> None:
    """⚠️ Sonst entsteht ein Posten, den nexcrate nie kennt - ohne Fehler."""
    nex = list(get_beschaffung(umstieg.nex_sicht(db)).fassungen())
    assert umstieg.pruefe_abbildung({SONARR: FILM_HD}, nex) == ["umstieg_falsche_medienart"]


def test_eine_erfundene_zielfassung_ist_ein_fehler(vor_dem_umstieg: Any, db: Session) -> None:
    nex = list(get_beschaffung(umstieg.nex_sicht(db)).fassungen())
    assert umstieg.pruefe_abbildung({RADARR: "v_00000000"}, nex) == ["umstieg_ziel_unbekannt"]


# --------------------------------------------------------------------------
# 7.3 Schritt 4: die Probe


def _nutzer(db: Session, name: str = "umsteiger") -> User:
    person = User(username=name, password_hash=hash_password("test"), role=Role.user)
    db.add(person)
    db.commit()
    return person


def _anfrage(db: Session, person: User, **werte: Any) -> MediaRequest:
    grund: dict[str, Any] = {
        "user_id": person.id,
        "media_type": MediaType.movie,
        "tmdb_id": 603,
        "title": "Example Movie",
        "fassung_kennung": RADARR,
        "status": RequestStatus.approved,
    }
    anfrage = MediaRequest(**{**grund, **werte})
    db.add(anfrage)
    db.commit()
    return anfrage


def _posten(db: Session, **werte: Any) -> StorageEntry:
    grund: dict[str, Any] = {
        "key": "movie:radarr-standard:603",
        "media_type": MediaType.movie,
        "tmdb_id": 603,
        "title": "Example Movie",
        "fassung_kennung": RADARR,
        "state": StorageState.owned,
        "size_bytes": 8_000_000_000,
    }
    posten = StorageEntry(**{**grund, **werte})
    db.add(posten)
    db.commit()
    return posten


async def test_die_probe_trennt_bekannt_ohne_fassung_und_unbekannt(
    vor_dem_umstieg: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.film(603)  # nur in der hd-Fassung
    person = _nutzer(db)
    _anfrage(db, person, tmdb_id=603, fassung_kennung=RADARR)
    _anfrage(db, person, tmdb_id=604, title="Fremd", fassung_kennung=RADARR)
    _anfrage(db, person, tmdb_id=603, fassung_kennung=RADARR_UHD)

    ergebnis = await umstieg.probe(db, umstieg.nex_sicht(db), _abbildung())

    nach_art = {(b.tmdb_id, b.fassung): b.ergebnis for b in ergebnis.befunde}
    assert nach_art[(603, RADARR)] == "bekannt"
    # ⚠️ Derselbe Titel, andere Fassung: nexcrate führt ihn, aber nicht in 4K.
    assert nach_art[(603, RADARR_UHD)] == "ohne_fassung"
    assert nach_art[(604, RADARR)] == "unbekannt"


async def test_eine_serie_wird_notfalls_ueber_tvdb_gefunden(
    vor_dem_umstieg: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ Daraus entsteht die Übersetzung der Speicherschlüssel.

    Eine aus Sonarr übernommene Serie hat in Nexview oft eine TMDB-Nummer, die
    nexcrate nicht führt - wohl aber ihre TVDB-Nummer.
    """
    nexcrate.serie(tmdb_id=1399, tvdb=121361)
    person = _nutzer(db)
    _anfrage(
        db,
        person,
        media_type=MediaType.tv,
        tmdb_id=999999,
        tvdb_id=121361,
        title="Example Show",
        fassung_kennung=SONARR,
    )

    ergebnis = await umstieg.probe(db, umstieg.nex_sicht(db), _abbildung())

    assert [b.ergebnis for b in ergebnis.befunde] == ["bekannt"]
    assert ergebnis.tmdb_je_tvdb() == {121361: 1399}


async def test_anime_mit_offener_anfrage_wird_gezaehlt(
    vor_dem_umstieg: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.serie(tmdb_id=1399, typ="anime")
    person = _nutzer(db)
    _anfrage(
        db,
        person,
        media_type=MediaType.tv,
        tmdb_id=1399,
        tvdb_id=121361,
        title="Example Show",
        fassung_kennung=SONARR,
    )

    ergebnis = await umstieg.probe(db, umstieg.nex_sicht(db), _abbildung())
    assert len(ergebnis.anime_offen) == 1


async def test_ein_posten_ohne_gegenstueck_braucht_eine_entscheidung(
    vor_dem_umstieg: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    _posten(db, tmdb_id=604, key="movie:radarr-standard:604")

    ergebnis = await umstieg.probe(db, umstieg.nex_sicht(db), _abbildung())
    assert [b.tmdb_id for b in ergebnis.posten_ohne_gegenstueck] == [604]


# --------------------------------------------------------------------------
# 7.3 Schritt 6: die Wanderung


def test_die_wanderung_schreibt_jede_zeile_um(vor_dem_umstieg: Any, db: Session) -> None:
    person = _nutzer(db)
    offen = _anfrage(db, person, fassung_kennung=RADARR, status=RequestStatus.approved)
    erledigt = _anfrage(db, person, fassung_kennung=RADARR, status=RequestStatus.downloaded)
    posten = _posten(db)
    recht = FassungRecht(user_id=person.id, fassung_kennung=RADARR, anfragen=True)
    ohne_ziel = FassungRecht(user_id=person.id, fassung_kennung=SONARR_UHD, anfragen=True)
    einladung = AuthToken(
        token_hash="x" * 64,
        purpose=TokenPurpose.invitation,
        email="eingeladen@example.com",
        expires_at=utcnow(),
        invite_fassung_rechte=[{"fassung": RADARR, "anfragen": True}],
    )
    regel = Regel(
        name="Vierkay",
        bedingungen=[{"feld": "fassung", "werte": [RADARR_UHD, "uhd"]}],
        entscheidung="ablehnen",
        position=1,
    )
    db.add_all([recht, ohne_ziel, einladung, regel])
    db.commit()

    zahlen = umstieg.wandern(db, _abbildung(), {})
    db.commit()

    db.refresh(offen)
    db.refresh(erledigt)
    db.refresh(posten)
    db.refresh(einladung)
    db.refresh(regel)
    assert offen.fassung_kennung == FILM_HD
    # ⚠️ Erledigte behalten ihre Arr-Kennung: Sie sind Geschichte.
    assert erledigt.fassung_kennung == RADARR
    assert posten.fassung_kennung == FILM_HD
    assert posten.key == storage.schluessel(MediaType.movie, FILM_HD, tmdb_id=603)
    assert einladung.invite_fassung_rechte == [{"fassung": FILM_HD, "anfragen": True}]
    # ⚠️ ``uhd`` bleibt stehen - eine Klasse, keine Fassung.
    assert regel.bedingungen == [{"feld": "fassung", "werte": [FILM_UHD, "uhd"]}]
    assert zahlen.anfragen == 1
    assert db.get(FassungRecht, ohne_ziel.id) is None


def test_eine_serie_wandert_von_tvdb_auf_tmdb(vor_dem_umstieg: Any, db: Session) -> None:
    """⚠️ nexcrate ankert auf TMDB. Ohne Übersetzung passte der Schlüssel zu nichts."""
    posten = _posten(
        db,
        key="tv:sonarr-standard:121361:1",
        media_type=MediaType.tv,
        tmdb_id=999999,
        tvdb_id=121361,
        season=1,
        fassung_kennung=SONARR,
        title="Example Show",
    )

    umstieg.wandern(db, _abbildung(), {121361: 1399})
    db.commit()

    db.refresh(posten)
    assert posten.tmdb_id == 1399
    assert posten.key == storage.schluessel(MediaType.tv, SERIE_HD, tmdb_id=1399, season=1)


def test_ohne_uebersetzung_bleibt_der_posten_unangetastet(
    vor_dem_umstieg: Any, db: Session
) -> None:
    """Lieber ein Posten mit alter Kennung als einer mit erfundener."""
    posten = _posten(
        db,
        key="tv:sonarr-standard:121361:1",
        media_type=MediaType.tv,
        tmdb_id=None,
        tvdb_id=121361,
        season=1,
        fassung_kennung=SONARR,
        title="Example Show",
    )

    zahlen = umstieg.wandern(db, _abbildung(), {})
    db.commit()

    db.refresh(posten)
    assert zahlen.posten_ohne_uebersetzung == 1
    assert zahlen.posten == 0
    assert posten.key == "tv:sonarr-standard:121361:1"
    # ⚠️ **Auch die Fassung bleibt.** Neue Kennung plus alter Schlüssel wäre
    # das Schlimmste von beidem: Der Schlüssel entsteht aus der Herkunft der
    # Fassung, und jede spätere Runde rechnete einen anderen aus.
    assert posten.fassung_kennung == SONARR


def test_die_arr_fassungen_bleiben_als_stillgelegte_zeile_stehen(
    vor_dem_umstieg: Any, db: Session
) -> None:
    """Damit die Anzeige den Namen einer erledigten Anfrage weiter kennt (6.10)."""
    zeile = db.get(Fassung, RADARR)
    assert zeile is not None
    # Ohne eingerichtetes Radarr steht die Zeile schon still; fuer diesen Fall
    # zaehlt, dass die Wanderung sie **stilllegt**, also wird sie erst geweckt.
    zeile.aktiv = True
    zeile.verschwunden_am = None
    db.commit()

    umstieg.wandern(db, _abbildung(), {})
    db.commit()

    db.refresh(zeile)
    assert zeile.aktiv is False


# --------------------------------------------------------------------------
# 7.3 Schritt 6: umschalten


async def test_umschalten_setzt_die_betriebsart_und_den_merker(
    vor_dem_umstieg: Any, db: Session
) -> None:
    bericht = await umstieg.umschalten(db, vor_dem_umstieg, _abbildung(), {})

    frisch = load_settings(db, frisch=True)
    assert frisch.beschaffung == NEX
    # ⚠️ Ohne den Merker reicht nichts nach - freigegebene Anfragen blieben liegen.
    assert frisch.beschaffung_gewechselt_am
    assert bericht.fassungen == 4


async def test_umschalten_loescht_den_zugang_zu_nexcrate_nicht(
    vor_dem_umstieg: Any, db: Session
) -> None:
    """``verlassen`` gilt dem **alten** Weg. Der neue muss danach arbeiten können."""
    await umstieg.umschalten(db, vor_dem_umstieg, _abbildung(), {})

    frisch = load_settings(db, frisch=True)
    assert frisch.nexcrate_url == URL
    assert frisch.nexcrate_api_key == KEY


# --------------------------------------------------------------------------
# Die Riegel des Assistenten (routers/umstieg.py)


@pytest.fixture
def assistent(admin_client: TestClient, nexcrate: FakeNexcrate) -> TestClient:
    """Ein angemeldeter Administrator im ARR-Betrieb, mit Zugang zu nexcrate."""
    admin_client.put("/api/settings", json={"nexcrate_url": URL, "nexcrate_api_key": KEY})
    return admin_client


def test_im_nex_betrieb_gibt_es_den_umstieg_nicht(assistent: TestClient) -> None:
    """⚠️ Ein zweiter Umstieg schriebe Kennungen um, die schon stimmen."""
    assistent.put("/api/settings", json={"beschaffung": NEX})

    antwort = assistent.get("/api/umstieg/vorab")
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "not_in_this_mode"


def test_ohne_sicherung_wird_nicht_umgeschaltet(assistent: TestClient) -> None:
    """Es gibt keinen Rückweg außer ihr - also prüft der Server, dass sie liegt."""
    antwort = assistent.post(
        "/api/umstieg/umschalten",
        json={"abbildung": _abbildung(), "sicherung": "gibt-es-nicht.nvbak"},
    )
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "umstieg_ohne_sicherung"


def test_die_abbildung_liefert_vorschlag_und_beide_listen(assistent: TestClient) -> None:
    antwort = assistent.get("/api/umstieg/abbildung")
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["sperrt"] is False
    assert len(daten["arr_fassungen"]) == 4
    assert len(daten["nex_fassungen"]) == 4
    assert daten["vorschlag"][RADARR] == FILM_HD


def test_eine_gesperrte_nexcrate_bietet_keine_fassungen_an(
    assistent: TestClient, nexcrate: FakeNexcrate
) -> None:
    """⚠️ Sonst wäre die hübsche Zuordnung eine Einladung, weiterzuklicken."""
    nexcrate.stage = "V2"

    daten = assistent.get("/api/umstieg/abbildung").json()
    assert daten["sperrt"] is True
    assert daten["nex_fassungen"] == []
    assert [b["code"] for b in daten["pruefung"]] == ["nexcrate_zu_alt"]


def test_eine_falsche_abbildung_kommt_als_kennung_zurueck(assistent: TestClient) -> None:
    assistent.get("/api/umstieg/abbildung")
    antwort = assistent.post("/api/umstieg/probe", json={"abbildung": {SONARR: FILM_HD}})
    assert antwort.json()["fehler"] == ["umstieg_falsche_medienart"]


def test_eine_fassung_ohne_zeile_laesst_sich_nicht_oeffnen(admin_client: TestClient) -> None:
    antwort = admin_client.put(
        "/api/settings/fassungen", json=[{"kennung": "v_00000000", "offen_fuer_alle": True}]
    )
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "fassung_unknown"


def test_die_fassungen_lassen_sich_oeffnen(assistent: TestClient) -> None:
    assistent.get("/api/umstieg/abbildung")  # legt die nexcrate-Zeilen an

    antwort = assistent.put(
        "/api/settings/fassungen", json=[{"kennung": FILM_HD, "offen_fuer_alle": True}]
    )
    assert antwort.status_code == 200, antwort.text
    with SessionLocal() as sitzung:
        zeile = sitzung.get(Fassung, FILM_HD)
        assert zeile is not None and zeile.offen_fuer_alle is True


async def test_eine_serie_ohne_tmdb_nummer_braucht_dieselbe_entscheidung(
    vor_dem_umstieg: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ Bekannt heißt nicht mitnehmbar.

    nexcrate führt die Serie - aber ohne TMDB-Nummer ließe sich ihr
    Speicherschlüssel nicht übersetzen. Für den Betreiber ist das derselbe
    Fall wie ein Titel, den nexcrate gar nicht kennt.
    """
    nexcrate.serie(tmdb_id=1399, tvdb=121361)
    titel = nexcrate.titles[("series", "tmdb:1399")]
    titel["refs"] = ["tvdb:121361"]
    _posten(
        db,
        key="tv:sonarr-standard:tvdb:121361:s1",
        media_type=MediaType.tv,
        tmdb_id=1399,
        tvdb_id=121361,
        season=1,
        fassung_kennung=SONARR,
        title="Example Show",
    )

    ergebnis = await umstieg.probe(db, umstieg.nex_sicht(db), _abbildung())

    befund = ergebnis.befunde[0]
    assert befund.ergebnis == "bekannt"
    assert befund.mitnehmbar is False
    assert ergebnis.posten_ohne_gegenstueck == [befund]


async def test_die_zahlen_vorab_zaehlen_offene_anfragen_und_posten(
    vor_dem_umstieg: Any, db: Session
) -> None:
    """⚠️ Der erste Schritt des Assistenten - und er war einmal ein 500er.

    Gefunden hat das der Playwright-Lauf, nicht dieser Test: Die Zählung stand
    als ``MediaRequest.id.distinct().count()`` da, was SQLAlchemy nicht kennt.
    Ein Test, der die Zahlen nur baut, aber nie abfragt, hätte es nie gemerkt.
    """
    person = _nutzer(db)
    _anfrage(db, person, status=RequestStatus.approved)
    _anfrage(db, person, status=RequestStatus.downloaded)
    _posten(db)

    stand = await umstieg.vorab(db, vor_dem_umstieg)

    assert stand.anfragen_offen == 1
    assert stand.posten == 1


# --------------------------------------------------------------------------
# Was der erste Umstieg an einer echten Anlage fand (23.09.2026)


def test_zwei_serien_mit_derselben_tmdb_nummer_bleiben_stehen(
    vor_dem_umstieg: Any, db: Session
) -> None:
    """⚠️ **Der Fehler, der den ersten echten Umstieg abbrechen ließ.**

    Im ARR-Betrieb hängt der Schlüssel einer Serie an ihrer TVDB-Nummer, im
    NEX-Betrieb an der TMDB-Nummer. Was Sonarr als zwei Serien führt, ist in
    TMDB oft ein Titel - bei Anime und getrennt geführten Staffel-Reihen der
    Normalfall. Beide bekämen denselben neuen Schlüssel, und SQLite brach die
    Wanderung mitten im Schreiben ab: `UNIQUE constraint failed`.

    Jetzt bleiben **beide** unberührt. Einen davon willkürlich zu nehmen hieße,
    dem Betreiber die Wahl abzunehmen, welcher seiner Titel künftig zählt.
    """
    eine = _posten(
        db,
        key="tv:sonarr-standard:tvdb:111:s1",
        media_type=MediaType.tv,
        tmdb_id=None,
        tvdb_id=111,
        season=1,
        fassung_kennung=SONARR,
        title="Beispielserie, Teil 1",
    )
    andere = _posten(
        db,
        key="tv:sonarr-standard:tvdb:222:s1",
        media_type=MediaType.tv,
        tmdb_id=None,
        tvdb_id=222,
        season=1,
        fassung_kennung=SONARR,
        title="Beispielserie, Teil 2",
    )

    # Beide TVDB-Nummern zeigen in nexcrate auf denselben Titel.
    zahlen = umstieg.wandern(db, _abbildung(), {111: 1399, 222: 1399})
    db.commit()

    db.refresh(eine)
    db.refresh(andere)
    assert zahlen.posten_doppelt == 2
    assert zahlen.posten == 0
    assert eine.fassung_kennung == SONARR and eine.key == "tv:sonarr-standard:tvdb:111:s1"
    assert andere.fassung_kennung == SONARR and andere.key == "tv:sonarr-standard:tvdb:222:s1"


def test_eine_kollision_mit_einem_posten_der_gar_nicht_wandert(
    vor_dem_umstieg: Any, db: Session
) -> None:
    """⚠️ Auch ein Posten, der stehen bleibt, belegt seinen Schlüssel weiter.

    Hier trägt der zweite schon die Zielfassung (etwa aus einem abgebrochenen
    Lauf). Wer nur die wandernden Posten gegeneinander prüft, übersieht ihn -
    und SQLite bricht wieder ab.
    """
    wandernd = _posten(
        db,
        key="tv:sonarr-standard:tvdb:111:s1",
        media_type=MediaType.tv,
        tmdb_id=None,
        tvdb_id=111,
        season=1,
        fassung_kennung=SONARR,
        title="Wandert",
    )
    _posten(
        db,
        key=f"tv:{SERIE_HD}:tmdb:1399:s1",
        media_type=MediaType.tv,
        tmdb_id=1399,
        tvdb_id=999,
        season=1,
        fassung_kennung=SERIE_HD,
        title="Liegt schon da",
    )

    zahlen = umstieg.wandern(db, _abbildung(), {111: 1399})
    db.commit()

    db.refresh(wandernd)
    # ⚠️ Gezählt wird, **wer wandern sollte und nicht durfte** - einer. Der
    # andere trägt die Zielfassung längst und war nie unterwegs.
    assert zahlen.posten_doppelt == 1
    assert zahlen.posten == 0
    assert wandernd.key == "tv:sonarr-standard:tvdb:111:s1"


async def test_die_probe_nennt_die_kollision_vorher(
    vor_dem_umstieg: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ Der Betreiber soll das im Assistenten sehen, nicht als Fehler 500.

    Nachgestellt wie an der echten Anlage: Nexview führt zwei Serien mit
    **verschiedenen** TMDB-Nummern - nexcrate kennt nur eine davon und nennt
    für die andere, über ihre TVDB-Nummer gefunden, dieselbe. Erst dadurch
    fallen beide Speicherschlüssel zusammen.
    """
    nexcrate.serie(tmdb_id=1399, tvdb=111)
    _posten(
        db,
        key="tv:sonarr-standard:tvdb:111:s1",
        media_type=MediaType.tv,
        tmdb_id=1399,
        tvdb_id=111,
        season=1,
        fassung_kennung=SONARR,
        title="Beispielserie, Teil 1",
    )
    _posten(
        db,
        key="tv:sonarr-standard:tvdb:222:s1",
        media_type=MediaType.tv,
        # Nexview hat hier eine eigene Nummer; nexcrate führt denselben Titel.
        tmdb_id=1400,
        tvdb_id=111,
        season=1,
        fassung_kennung=SONARR,
        title="Beispielserie, Teil 2",
    )

    ergebnis = await umstieg.probe(db, umstieg.nex_sicht(db), _abbildung())

    assert len(ergebnis.befunde) == 2, [b.titel for b in ergebnis.befunde]
    assert all(b.kollidiert for b in ergebnis.befunde), [
        (b.titel, b.kollidiert) for b in ergebnis.befunde
    ]
    # ⚠️ Und damit stehen sie in der Liste, die eine Entscheidung verlangt -
    # der Betreiber sieht sie, bevor er umschaltet.
    assert len(ergebnis.posten_ohne_gegenstueck) == 2


async def test_umschalten_verlaesst_arr_erst_nach_der_wanderung(
    vor_dem_umstieg: Any, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **Die teuerste Lehre des ersten echten Umstiegs.**

    Vorher wurde Arr zuerst verlassen. Als die Wanderung scheiterte, blieb eine
    Installation ohne Arr-Zugänge und ohne nexcrate zurück - sie konnte gar
    nichts mehr. Jetzt geschieht zuerst alles, was scheitern kann.
    """
    reihenfolge: list[str] = []

    from app.services.beschaffung.arr.weg import ArrBeschaffung

    async def verlassen(self: Any, sitzung: Session) -> list[str]:
        reihenfolge.append("verlassen")
        return []

    echt = umstieg.wandern

    def wandern(sitzung: Session, abbildung: Any, uebersetzung: Any) -> Any:
        reihenfolge.append("wandern")
        return echt(sitzung, abbildung, uebersetzung)

    monkeypatch.setattr(ArrBeschaffung, "verlassen", verlassen)
    monkeypatch.setattr(umstieg, "wandern", wandern)

    await umstieg.umschalten(db, vor_dem_umstieg, _abbildung(), {})

    assert reihenfolge == ["wandern", "verlassen"]


async def test_ein_stummes_arr_haelt_den_umstieg_nicht_mehr_auf(
    vor_dem_umstieg: Any, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Danach ist die Installation umgestellt - ein Fehler beim Aufräumen
    drüben darf das nicht zurücknehmen, er steht nur im Bericht."""
    from app.services.beschaffung.arr.weg import ArrBeschaffung

    async def verlassen(self: Any, sitzung: Session) -> list[str]:
        raise RuntimeError("Radarr antwortet nicht")

    monkeypatch.setattr(ArrBeschaffung, "verlassen", verlassen)

    bericht = await umstieg.umschalten(db, vor_dem_umstieg, _abbildung(), {})

    assert load_settings(db, frisch=True).beschaffung == NEX
    assert bericht.verlassen and "by hand" in bericht.verlassen[0]
