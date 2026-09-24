"""Der NEX-Weg: Client, Fehlerkoerbe, Fassungen, Koppeln und der Riegel.

Gegen ``tests/beschaffung/fake_nexcrate.py``, also gegen die gemessenen
Antworten einer echten Wegwerf-nexcrate. Weil die Attrappe auf HTTP-Ebene
haengt, laeuft dabei der echte Client mit: Kopfzeilen, beide Fehlerformen,
die Stapelgrenzen.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Fassung
from app.services import fassungen as fassungen_dienst
from app.services.beschaffung import ARR, NEX, Korb, get_beschaffung
from app.services.beschaffung.arr.weg import ArrBeschaffung
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import mapping, system
from app.services.beschaffung.nex.client import NexcrateClient
from app.services.beschaffung.nex.fehler import NexcrateError
from app.services.beschaffung.nex.weg import NexBeschaffung
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, FILM_UHD, KEY, SERIE_HD, URL, FakeNexcrate


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    """Eine nexcrate im Speicher, unter der der echte Client laeuft."""
    attrappe = FakeNexcrate()
    nex_client.use_transport(attrappe.transport())
    system.vergessen()
    nex_fassungen.vergessen()
    try:
        yield attrappe
    finally:
        nex_client.use_transport(None)
        system.vergessen()
        nex_fassungen.vergessen()


def _client(key: str = KEY) -> NexcrateClient:
    return NexcrateClient(URL, key)


def _nex_einstellungen(db: Any) -> Any:
    return replace(
        load_settings(db), beschaffung=NEX, nexcrate_url=URL, nexcrate_api_key=KEY
    )


# --- Der Client --------------------------------------------------------------


def test_geloescht_wird_nur_ueber_withdraw() -> None:
    """Regel 4 (Bauplan, ``nex/auftraege.py``): Loeschen geht ueber
    ``withdraw`` mit ``delete_files`` im Koerper, nie ueber die eigene
    Adresse ``delete-files``. Eine tote ``delete_files``-Methode auf dem
    Client hatte keinen Aufrufer und widersprach genau dieser Regel."""
    assert not hasattr(NexcrateClient, "delete_files")


@pytest.mark.anyio
async def test_der_schluessel_reist_nur_in_der_kopfzeile(nexcrate: FakeNexcrate) -> None:
    """N1: nie in der Adresse. Ein Aufruf ohne Schluessel wird abgelehnt."""
    await _client().system()
    methode, pfad, abfrage, _ = nexcrate.calls[-1]
    assert (methode, pfad) == ("GET", "/api/v1/system")
    assert abfrage == {}
    assert nexcrate.kopfzeilen["authorization"] == f"Bearer {KEY}"

    with pytest.raises(NexcrateError) as gefangen:
        await _client("").system()
    assert gefangen.value.code == "nexcrate_key_rejected"
    assert gefangen.value.korb is Korb.abgelehnt


@pytest.mark.anyio
async def test_system_nennt_installation_faehigkeiten_und_spruenge(nexcrate: FakeNexcrate) -> None:
    daten = await _client().system()
    assert daten["installation_id"] == nexcrate.installation_id
    assert daten["contract"] == {"major": 1, "stage": "V5"}
    assert daten["links"]["title"] == "/open/title/{kind}/{ref}"

    system.merken(daten)
    koennen = system.faehigkeiten()
    assert koennen.betreiberwerkzeuge is False
    assert koennen.warum and koennen.papierkorb and koennen.ereignisstrom
    assert koennen.anime is True
    assert koennen.wertungen == ("movie", "tv")


def test_ohne_gelesenen_stand_gilt_anime_als_nein() -> None:
    """N44: Fehlt die Faehigkeit, sucht nexcrate kein Anime - geraten wird nicht."""
    system.vergessen()
    assert system.faehigkeiten().anime is False
    system.merken({"capabilities": {"anime": True}})
    assert system.faehigkeiten().anime is True


@pytest.mark.anyio
async def test_versions_traegt_klasse_und_gruende(nexcrate: FakeNexcrate) -> None:
    nexcrate.nicht_bereit(FILM_UHD, "no_indexer", "automatic_off")
    eintraege = await _client().versions()
    nach_kennung = {eintrag["version_id"]: eintrag for eintrag in eintraege}
    assert nach_kennung[FILM_HD]["tier"] == "hd" and nach_kennung[FILM_HD]["ready"] is True
    assert mapping.klasse(nach_kennung[FILM_UHD]["tier"]) == "uhd"
    assert mapping.gruende(nach_kennung[FILM_UHD]) == ["no_indexer", "automatic_off"]


@pytest.mark.anyio
async def test_lookup_geht_in_stapeln_von_hundert(nexcrate: FakeNexcrate) -> None:
    """N12: Ein Aufruf fuer viele Titel - und nie mehr als hundert auf einmal."""
    nexcrate.film(603)
    wunsch = [{"kind": "movie", "ref": f"tmdb:{nummer}"} for nummer in range(1, 151)]
    wunsch[0] = {"kind": "movie", "ref": "tmdb:603"}
    gefunden = await _client().lookup(wunsch)

    assert len(gefunden) == 150
    assert gefunden[0]["known"] is True and gefunden[1]["known"] is False
    stapel = [k for k in nexcrate.calls if k[1].endswith("/titles/lookup")]
    assert [len(k[3]["items"]) for k in stapel] == [100, 50]


@pytest.mark.anyio
async def test_ein_unbekannter_titel_ist_kein_fehler(nexcrate: FakeNexcrate) -> None:
    nexcrate.film(603)
    assert (await _client().title("movie", "tmdb:603"))["name"] == "Example Movie"
    assert await _client().title("movie", "tmdb:424242") is None
    assert await _client().season("tmdb:1399", 9) is None


@pytest.mark.anyio
async def test_die_kennung_geht_nur_klein_hinaus(nexcrate: FakeNexcrate) -> None:
    """Gemessen: ``TMDB:603`` ist ``ref_source_unknown`` (422)."""
    assert mapping.ref(603) == "tmdb:603"
    assert mapping.tmdb_aus("tmdb:603") == 603
    assert mapping.tmdb_aus("tvdb:121361") is None
    with pytest.raises(NexcrateError) as gefangen:
        await _client()._request("GET", "/titles/movie/TMDB:603")
    assert gefangen.value.code == "nexcrate_ref_invalid"
    assert gefangen.value.fremd == "ref_source_unknown"


@pytest.mark.anyio
async def test_titel_und_staffel_in_tmdb_zaehlung(nexcrate: FakeNexcrate) -> None:
    """N15: Staffeln und Folgen kommen in TMDB-Nummern - keine TVDB-Klaerung."""
    nexcrate.serie(1399)
    nexcrate.staffel(
        "tmdb:1399",
        1,
        [
            nexcrate.folge(1, versionen=[nexcrate.fassung(SERIE_HD, "available", size_bytes=3_000_000_000, quality="WEBDL-1080p")]),
            nexcrate.folge(3, air_date="2020-01-15", versionen=[nexcrate.fassung(SERIE_HD, "wanted")]),
        ],
    )
    staffel = await _client().season("tmdb:1399", 1)
    assert [folge["episode"] for folge in staffel["episodes"]] == [1, 3]
    assert staffel["episodes"][0]["versions"][0]["size_bytes"] == 3_000_000_000


@pytest.mark.anyio
async def test_die_marke_liest_nur_geaendertes_und_nennt_entferntes(nexcrate: FakeNexcrate) -> None:
    """N13, und der Grabstein dazu: ``removed`` sagt, was verschwunden ist."""
    nexcrate.film(603)
    zweiter = nexcrate.film(604, name="Another Example")
    erste = await _client().titles(after=0, kind="movie")
    assert [item["ref"] for item in erste["items"]] == ["tmdb:603", "tmdb:604"]

    nexcrate.entfernt("movie", "tmdb:604")
    weiter = await _client().titles(after=zweiter["seq"], kind="movie")
    assert weiter["items"] == []
    assert weiter["removed"] == [{"kind": "movie", "ref": "tmdb:604", "seq": nexcrate.seq}]


@pytest.mark.anyio
async def test_eine_marke_ueber_latest_bleibt_still_leer(nexcrate: FakeNexcrate) -> None:
    """⚠️ nexbeat-Befund 11, hier nachgemessen: kein ``410``, nur leer.

    Wer die Marke haelt, muss selbst mit ``latest`` vergleichen - sonst sieht
    eine neu aufgesetzte nexcrate nie wieder eine Aenderung.
    """
    nexcrate.film(603)
    antwort = await _client().titles(after=999999, kind="movie")
    assert antwort["items"] == [] and antwort["latest"] == nexcrate.seq
    assert antwort["next_after"] == 999999


# --- Die Fehlerkoerbe --------------------------------------------------------


@pytest.mark.parametrize(
    ("antwort", "code", "korb", "ungewiss"),
    [
        (httpx.Response(429, json={"code": "rate_limited", "message": "slow down", "params": {}}, headers={"Retry-After": "1"}), "nexcrate_busy", Korb.voruebergehend, False),
        (httpx.Response(502, text="<html><title>502 Bad Gateway</title></html>"), "nexcrate_unavailable", Korb.voruebergehend, True),
        (httpx.Response(500, json={"code": "internal_error", "message": "e5458f08", "params": {}}), "nexcrate_refused", Korb.voruebergehend, True),
        (httpx.Response(403, json={"code": "scope_missing", "message": "no", "params": {}}), "nexcrate_scope_missing", Korb.abgelehnt, False),
        (httpx.Response(410, json={"code": "marker_too_old", "message": "old", "params": {}}), "nexcrate_marker_too_old", Korb.abgelehnt, False),
        (httpx.Response(404, json={"detail": {"code": "not_found", "message": "Not Found"}}), "nexcrate_path_unknown", Korb.abgelehnt, False),
        (httpx.Response(422, json={"code": "kind_unsupported", "message": "no", "params": {}}), "nexcrate_kind_unsupported", Korb.abgelehnt, False),
    ],
)
@pytest.mark.anyio
async def test_jede_antwort_faellt_in_ihren_korb(
    nexcrate: FakeNexcrate, antwort: httpx.Response, code: str, korb: Korb, ungewiss: bool
) -> None:
    """Drei Koerbe: noch einmal, abgelehnt, unbekannt.

    ⚠️ ``ungewiss`` trennt "hat nicht geklappt" von "wir wissen es nicht": Bei
    5xx kann der Auftrag angekommen sein (nexbeat-Befund 17).
    """
    nexcrate.next_answer["GET /api/v1/system"] = antwort
    with pytest.raises(NexcrateError) as gefangen:
        await _client().system()
    assert gefangen.value.code == code
    assert gefangen.value.korb is korb
    assert gefangen.value.ungewiss is ungewiss


@pytest.mark.anyio
async def test_eine_fremde_seite_wird_nicht_zur_begruendung(nexcrate: FakeNexcrate) -> None:
    """Die 502-Seite eines Proxys wird ihr Titel, nicht der ganze Quelltext."""
    from app.services.beschaffung.nex import fehler

    assert fehler.seitentext("<html><title>502 Bad Gateway</title><body>x" * 40) == "502 Bad Gateway"
    assert fehler.seitentext("") == "(leer)"


@pytest.mark.anyio
async def test_eine_antwort_ohne_json_ist_unerwartet(nexcrate: FakeNexcrate) -> None:
    nexcrate.next_answer["GET /api/v1/system"] = httpx.Response(200, text="<html>Anmeldung</html>")
    with pytest.raises(NexcrateError) as gefangen:
        await _client().system()
    assert gefangen.value.code == "nexcrate_unexpected_answer"
    assert gefangen.value.korb is Korb.voruebergehend


@pytest.mark.anyio
async def test_kein_satz_aus_nexcrate_steht_in_der_meldung(nexcrate: FakeNexcrate) -> None:
    """Uebersetzt wird nach Kennung (N5) - nexcrates Code steht nur in den Zahlen."""
    nexcrate.next_answer["GET /api/v1/system"] = httpx.Response(
        409, json={"code": "something_new", "message": "A sentence nobody translated.", "params": {}}
    )
    with pytest.raises(NexcrateError) as gefangen:
        await _client().system()
    meldung = gefangen.value.als_meldung()
    assert meldung["code"] == "nexcrate_refused"
    assert "A sentence nobody translated." not in str(meldung)
    assert meldung["nexcrate_code"] == "something_new"


@pytest.mark.anyio
async def test_nicht_erreichbar_heisst_noch_einmal(nexcrate: FakeNexcrate) -> None:
    nexcrate.next_answer["GET /api/v1/system"] = httpx.ConnectError("nope")
    with pytest.raises(NexcrateError) as gefangen:
        await _client().system()
    assert gefangen.value.code == "nexcrate_unreachable"
    assert gefangen.value.korb is Korb.voruebergehend


# --- Zustaende ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "monitored", "erwartet"),
    [
        ("available", True, "da"),
        ("available", False, "vorerst"),
        ("upgrade", True, "verbesserbar"),
        ("upgrade", False, "vorerst"),
        ("wanted", True, "gesucht"),
        ("unmonitored", False, "fehlt"),
        ("downloading", True, "laedt"),
        ("problem", True, "problem"),
        ("incomplete", True, "unbekannt"),
        ("etwas-neues", True, "unbekannt"),
    ],
)
def test_jeder_zustand_hat_seine_entsprechung(state: str, monitored: bool, erwartet: str) -> None:
    """N14. ⚠️ Ein unbekannter Zustand ist ``unbekannt``, nie ein Fehler."""
    assert mapping.zustand(state, monitored) == erwartet


@pytest.mark.anyio
async def test_die_zustaende_der_schnittstelle_sind_die_gemessenen(nexcrate: FakeNexcrate) -> None:
    """Was nexcrate unter ``/states`` fuehrt, muss Nexview alles abbilden koennen."""
    gemeldet = {eintrag["state"] for eintrag in await _client().states()}
    ohne_alben = gemeldet - {"incomplete"}
    assert all(mapping.zustand(state) != "unbekannt" for state in ohne_alben)
    assert all(eintrag["meaning"].strip() for eintrag in await _client().states())


# --- Die Fassungen in der Tabelle --------------------------------------------


@pytest.mark.anyio
async def test_eine_neue_fassung_ist_gesperrt_bis_der_betreiber_sie_freigibt(
    nexcrate: FakeNexcrate,
) -> None:
    """Entscheidung des Betreibers, 22.09.2026."""
    with SessionLocal() as db:
        await nex_fassungen.auffrischen(db, _nex_einstellungen(db))
        db.commit()
        zeilen = {zeile.kennung: zeile for zeile in db.query(Fassung).all()}
    assert set(zeilen) == {FILM_HD, FILM_UHD, SERIE_HD, "v_66260bea"}
    assert all(zeile.offen_fuer_alle is False for zeile in zeilen.values())
    assert zeilen[FILM_HD].media_type == "movie" and zeilen[SERIE_HD].media_type == "tv"
    assert zeilen[FILM_UHD].klasse == "uhd" and zeilen[FILM_UHD].quelle == NEX
    assert zeilen[FILM_HD].reihenfolge < zeilen[SERIE_HD].reihenfolge


@pytest.mark.anyio
async def test_eine_freigabe_des_betreibers_nimmt_kein_abgleich_zurueck(
    nexcrate: FakeNexcrate,
) -> None:
    with SessionLocal() as db:
        await nex_fassungen.auffrischen(db, _nex_einstellungen(db))
        db.get(Fassung, FILM_HD).offen_fuer_alle = True
        db.commit()
    with SessionLocal() as db:
        await nex_fassungen.auffrischen(db, _nex_einstellungen(db))
        db.commit()
        assert db.get(Fassung, FILM_HD).offen_fuer_alle is True


@pytest.mark.anyio
async def test_eine_verschwundene_fassung_behaelt_ihre_zeile(nexcrate: FakeNexcrate) -> None:
    """Sonst stuenden Anfragen und Posten ohne Namen da."""
    with SessionLocal() as db:
        await nex_fassungen.auffrischen(db, _nex_einstellungen(db))
        db.commit()
    nexcrate.versions = [eintrag for eintrag in nexcrate.versions if eintrag["version_id"] != FILM_UHD]
    with SessionLocal() as db:
        gefunden = await nex_fassungen.auffrischen(db, _nex_einstellungen(db))
        db.commit()
        weg = db.get(Fassung, FILM_UHD)
        assert weg is not None and weg.aktiv is False and weg.verschwunden_am is not None
    assert FILM_UHD not in {eintrag.kennung for eintrag in gefunden}


@pytest.mark.anyio
async def test_die_gruende_einer_fassung_stehen_in_der_zeile(nexcrate: FakeNexcrate) -> None:
    nexcrate.nicht_bereit(SERIE_HD, "no_profile", "no_indexer")
    with SessionLocal() as db:
        await nex_fassungen.auffrischen(db, _nex_einstellungen(db))
        db.commit()
        zeile = db.get(Fassung, SERIE_HD)
        assert zeile.bereit is False and zeile.gruende == ["no_profile", "no_indexer"]


# --- Der Weg -----------------------------------------------------------------


def test_der_nex_weg_erfuellt_die_ganze_schnittstelle() -> None:
    assert not NexBeschaffung.__abstractmethods__


def test_der_nex_weg_hat_nichts_ausserhalb_der_schnittstelle() -> None:
    """Wer etwas an der Grenze vorbei anbietet, wird ausserhalb benutzt."""
    eigen = {
        name
        for name, wert in vars(NexBeschaffung).items()
        if not name.startswith("_") and (callable(wert) or isinstance(wert, (classmethod, property)))
    }
    erlaubt = {
        name for name in dir(ArrBeschaffung) if not name.startswith("_")
    } | {"client"}
    assert eigen <= erlaubt, sorted(eigen - erlaubt)


@pytest.mark.anyio
async def test_ein_werkzeug_des_anderen_betriebs_sagt_es_mit_kennung(
    nexcrate: FakeNexcrate,
) -> None:
    """Was es hier nicht gibt, antwortet ehrlich statt mit einer leeren Liste.

    ⚠️ **In beide Richtungen.** Zielordner und Profile gehoeren im NEX-Betrieb
    nexcrate; einen Papierkorb, aus dem sich etwas zurueckholen laesst, haben
    Radarr und Sonarr nicht.
    """
    from app.services.beschaffung import BeschaffungError

    with SessionLocal() as db:
        nex = get_beschaffung(_nex_einstellungen(db))
        arr = get_beschaffung(load_settings(db))

    with pytest.raises(BeschaffungError) as gefangen:
        await nex.optionen("movie")
    assert gefangen.value.code == "not_in_this_mode"
    assert gefangen.value.korb is Korb.abgelehnt

    with pytest.raises(BeschaffungError) as gefangen:
        await arr.papierkorb()
    assert gefangen.value.code == "not_in_this_mode"


@pytest.mark.anyio
async def test_ohne_zugang_sagt_der_weg_das_und_fragt_niemanden(nexcrate: FakeNexcrate) -> None:
    """Auch mit bekannten Fassungen: Ohne Adresse und Schluessel gibt es nichts."""
    nex_fassungen.merken(
        (
            mapping.fassung_info(
                {"version_id": FILM_HD, "kind": "movie", "name": "Movies", "order": 1, "tier": "hd"},
                0,
            ),
        )
    )
    with SessionLocal() as db:
        weg = get_beschaffung(replace(load_settings(db), beschaffung=NEX))
    assert weg.verwaltet("movie") is False
    assert "nexcrate" in weg.nicht_eingerichtet("movie", "standard").lower()
    assert nexcrate.calls == []


def test_die_stufe_zaehlt_im_nex_betrieb_nicht() -> None:
    """``verwaltet`` fragt nach der Medienart, nicht nach Standard oder 4K."""
    with SessionLocal() as db:
        einstellungen = _nex_einstellungen(db)
    nex_fassungen.merken(
        (mapping.fassung_info({"version_id": FILM_HD, "kind": "movie", "name": "Movies", "order": 1, "tier": "hd"}, 0),)
    )
    weg = get_beschaffung(einstellungen)
    assert weg.verwaltet("movie", "standard") is True
    assert weg.verwaltet("movie", "uhd") is True
    assert weg.verwaltet("tv") is False


# --- Koppeln -----------------------------------------------------------------


def test_koppeln_bittet_und_speichert_den_schluessel_genau_einmal(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    """N8. ⚠️ Der Schluessel kommt einmal - er wird vor allem anderen gespeichert."""
    gestartet = admin_client.post("/api/settings/nexcrate/pairing", json={"url": URL})
    assert gestartet.status_code == 200, gestartet.text
    kennung = gestartet.json()["pairing_id"]
    assert gestartet.json()["code"] == "5Z3-M4G"
    assert "secret" not in gestartet.text

    offen = admin_client.get(f"/api/settings/nexcrate/pairing/{kennung}")
    assert offen.json()["state"] == "pending" and offen.json()["gespeichert"] is False

    nexcrate.bestaetigen(kennung)
    fertig = admin_client.get(f"/api/settings/nexcrate/pairing/{kennung}")
    assert fertig.status_code == 200, fertig.text
    assert fertig.json()["gespeichert"] is True
    assert fertig.json()["installation_id"] == nexcrate.installation_id
    assert fertig.json()["fassungen"] == 4

    with SessionLocal() as db:
        einstellungen = load_settings(db, frisch=True)
        assert einstellungen.nexcrate_api_key == KEY
        assert einstellungen.nexcrate_url == URL
        assert einstellungen.nexcrate_configured is True
        assert db.query(Fassung).filter(Fassung.quelle == NEX).count() == 4


def test_der_schluessel_verlaesst_den_server_nur_maskiert(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    gestartet = admin_client.post("/api/settings/nexcrate/pairing", json={"url": URL})
    kennung = gestartet.json()["pairing_id"]
    nexcrate.bestaetigen(kennung)
    admin_client.get(f"/api/settings/nexcrate/pairing/{kennung}")

    gelesen = admin_client.get("/api/settings").json()
    assert KEY not in admin_client.get("/api/settings").text
    assert gelesen["nexcrate_api_key_set"] is True
    assert gelesen["nexcrate_url"] == URL


def test_eine_abgelaufene_bitte_sagt_es_statt_zu_scheitern(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    """nexbeat: ohne oder mit falschem Geheimnis sagt nexcrate ``404``."""
    antwort = admin_client.get("/api/settings/nexcrate/pairing/pr_9999")
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "nexcrate_pairing_gone"


def test_die_verbindungsprobe_sagt_was_dort_antwortet(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    gut = admin_client.post(
        "/api/settings/nexcrate/test", json={"url": URL, "api_key": KEY}
    ).json()
    assert gut["ok"] is True and "0.1.0" in gut["message"]

    schlecht = admin_client.post(
        "/api/settings/nexcrate/test", json={"url": URL, "api_key": "falsch"}
    ).json()
    assert schlecht["ok"] is False

    ohne = admin_client.post("/api/settings/nexcrate/test", json={}).json()
    assert ohne["ok"] is False


def test_eine_andere_installation_verwirft_die_marken(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    """nexbeat-Befund 11: Dieselbe Adresse, andere nexcrate - die Marken gehoeren ihr nicht."""
    with SessionLocal() as db:
        save_settings(
            db,
            {
                "nexcrate_url": URL,
                "nexcrate_api_key": KEY,
                "nexcrate_installation_id": "alteinstallation",
                "nexcrate_titles_after": "42",
                "nexcrate_events_after": "17",
            },
        )
    antwort = admin_client.get("/api/settings/nexcrate/status")
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["installation_id"] == nexcrate.installation_id
    with SessionLocal() as db:
        einstellungen = load_settings(db, frisch=True)
        assert einstellungen.nexcrate_titles_after == ""
        assert einstellungen.nexcrate_events_after == ""


def test_der_stand_nennt_fassungen_und_befunde(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    nexcrate.health = [
        {"code": "indexer_none", "level": "error", "message": "No indexer.", "params": {}}
    ]
    nexcrate.update = {"current": "0.1.0", "latest": "0.2.0", "available": True, "checked_at": None}
    nexcrate.nicht_bereit(FILM_UHD, "no_profile")
    with SessionLocal() as db:
        save_settings(db, {"nexcrate_url": URL, "nexcrate_api_key": KEY})

    stand = admin_client.get("/api/settings/nexcrate/status").json()
    assert stand["eingerichtet"] and stand["erreichbar"]
    assert stand["update_verfuegbar"] is True and stand["update_version"] == "0.2.0"
    assert stand["anime"] is True
    nach_kennung = {eintrag["kennung"]: eintrag for eintrag in stand["fassungen"]}
    assert nach_kennung[FILM_UHD]["bereit"] is False
    assert nach_kennung[FILM_UHD]["gruende"] == ["no_profile"]
    assert stand["probleme"][0]["code"] == "indexer_none"


def test_ohne_zugang_meldet_der_stand_nur_das(admin_client: TestClient, nexcrate: FakeNexcrate) -> None:
    stand = admin_client.get("/api/settings/nexcrate/status").json()
    assert stand["eingerichtet"] is False and stand["erreichbar"] is False
    assert nexcrate.calls == []


def test_eine_nicht_erreichbare_nexcrate_ist_kein_absturz(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    with SessionLocal() as db:
        save_settings(db, {"nexcrate_url": URL, "nexcrate_api_key": KEY})
    nexcrate.next_answer["GET /api/v1/system"] = httpx.ConnectError("nope")
    stand = admin_client.get("/api/settings/nexcrate/status").json()
    assert stand["eingerichtet"] is True and stand["erreichbar"] is False
    assert stand["fehler"] == "nexcrate_unreachable"


# --- Die Betriebsart ---------------------------------------------------------


def test_die_betriebsart_laesst_sich_umstellen_und_nur_auf_bekanntes(
    admin_client: TestClient,
) -> None:
    assert admin_client.put("/api/settings", json={"beschaffung": "nex"}).status_code == 200
    assert admin_client.get("/api/settings").json()["beschaffung"] == "nex"
    falsch = admin_client.put("/api/settings", json={"beschaffung": "lidarr"})
    assert falsch.status_code == 422
    assert falsch.json()["detail"]["code"] == "beschaffung_invalid"
    assert admin_client.get("/api/settings").json()["beschaffung"] == "nex"


def test_die_betriebsart_steht_in_der_konfiguration(admin_client: TestClient) -> None:
    """Die Oberflaeche muss sie kennen - sonst blendet sie die falschen Reiter ein."""
    assert admin_client.get("/api/config").json()["beschaffung"] == "arr"
    admin_client.put("/api/settings", json={"beschaffung": "nex"})
    assert admin_client.get("/api/config").json()["beschaffung"] == "nex"


def test_im_nex_betrieb_gibt_es_keine_arr_instanzen(admin_client: TestClient) -> None:
    """Auch dann nicht, wenn die Zugaenge noch in der Datenbank stehen."""
    admin_client.put(
        "/api/settings",
        json={
            "radarr_url": "http://127.0.0.1:9",
            "radarr_api_key": "test-radarr-key",
            "beschaffung": "nex",
        },
    )
    with SessionLocal() as db:
        einstellungen = load_settings(db, frisch=True)
    assert einstellungen.arr_instanzen() == ()
    assert einstellungen.arr_configured("movie") is False
    assert einstellungen.beschaffung_ist_nex is True


def test_die_fassungen_kommen_im_nex_betrieb_aus_der_tabelle(nexcrate: FakeNexcrate) -> None:
    with SessionLocal() as db:
        einstellungen = _nex_einstellungen(db)
        nex_fassungen.schreiben(db, nexcrate.versions)
        db.commit()
        gefunden = fassungen_dienst.aus_einstellungen(einstellungen)
    assert [eintrag.kennung for eintrag in gefunden][:2] == [FILM_HD, FILM_UHD]
    assert [eintrag.quelle for eintrag in gefunden] == [NEX] * 4
    assert einstellungen.fassungen_fuer("movie")[0].name == "Movies"


def test_im_arr_betrieb_aendert_sich_nichts() -> None:
    """Die Probe aufs Exempel: ARR bewegt sich durch den zweiten Weg nicht."""
    with SessionLocal() as db:
        einstellungen = load_settings(db)
    assert einstellungen.beschaffung == ARR
    assert isinstance(get_beschaffung(einstellungen), ArrBeschaffung)
    assert fassungen_dienst.ARR_KENNUNGEN == (
        "radarr-standard",
        "radarr-uhd",
        "sonarr-standard",
        "sonarr-uhd",
    )


def test_musik_aus_nexcrate_kommt_nicht_in_nexviews_fassungen(nexcrate: FakeNexcrate) -> None:
    """⚠️ nexcrate fuehrt auch Alben - Nexview darf sie nicht anbieten.

    Beim ersten Umstieg an einer echten Anlage stand nexcrates Musikfassung
    danach in jeder Liste, die Nexview zeigt: in der Benutzerverwaltung, im
    Abgleich des Umsteigers, auf der nexcrate-Seite. ``mapping.art`` reicht
    ein unbekanntes ``kind`` unveraendert durch, und niemand hielt es auf.
    """
    musik = {
        "version_id": "v_beef0001",
        "kind": "album",
        "name": "Lossless",
        "tier": None,
        "order": 0,
        "ready": True,
        "reasons": [],
    }
    with SessionLocal() as db:
        einstellungen = _nex_einstellungen(db)
        nex_fassungen.schreiben(db, [*nexcrate.versions, musik])
        db.commit()
        gefunden = fassungen_dienst.aus_einstellungen(einstellungen)
    assert "v_beef0001" not in [eintrag.kennung for eintrag in gefunden]
    assert [eintrag.media_type for eintrag in gefunden] == ["movie", "movie", "tv", "tv"]


def test_eine_musikfassung_verschwindet_auch_wieder(nexcrate: FakeNexcrate) -> None:
    """Wer sie schon in der Tabelle hat, wird sie beim naechsten Abgleich los.

    Das ist der Weg fuer alle, die vor der Reparatur umgestiegen sind: kein
    Wanderungsskript, sondern der gewoehnliche Abgleich.
    """
    musik = {"version_id": "v_beef0001", "kind": "album", "name": "Lossless", "ready": True}
    with SessionLocal() as db:
        einstellungen = _nex_einstellungen(db)
        # So, wie die Zeile vor der Reparatur entstand.
        db.add(
            Fassung(
                kennung="v_beef0001",
                media_type="album",
                quelle=NEX,
                name="Lossless",
                aktiv=True,
                offen_fuer_alle=False,
            )
        )
        db.flush()
        nex_fassungen.schreiben(db, [*nexcrate.versions, musik])
        db.commit()
        zeile = db.get(Fassung, "v_beef0001")
        assert zeile.aktiv is False
        assert "v_beef0001" not in [
            eintrag.kennung for eintrag in fassungen_dienst.aus_einstellungen(einstellungen)
        ]


def test_der_stand_zeigt_keine_musikfassung_und_nexviews_medienart(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    """Die Dienste-Seite filtert selbst, statt sich auf die Tabelle zu verlassen.

    ⚠️ Sie liest nexcrates Antwort direkt, nicht die Fassungstabelle - ein
    Filter nur beim Schreiben hielte hier nichts auf. Und die Medienart kommt
    als Nexviews ``tv``, nicht als nexcrates ``series``: Die Oberfläche
    übersetzt nur die eigene.
    """
    nexcrate.versions.append(
        {
            "version_id": "v_beef0002",
            "kind": "album",
            "name": "Lossless",
            "tier": None,
            "order": 0,
            "ready": True,
            "reasons": [],
        }
    )
    with SessionLocal() as db:
        save_settings(db, {"nexcrate_url": URL, "nexcrate_api_key": KEY})

    stand = admin_client.get("/api/settings/nexcrate/status").json()
    assert "v_beef0002" not in [eintrag["kennung"] for eintrag in stand["fassungen"]]
    assert sorted({eintrag["media_type"] for eintrag in stand["fassungen"]}) == ["movie", "tv"]
