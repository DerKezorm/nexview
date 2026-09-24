"""Was die Oberfläche im NEX-Betrieb zeigt und im ARR-Betrieb nicht (Scheibe S8).

Drei Dinge gibt es nur, wenn der Weg sie kann: die Gründe (N28), der
Papierkorb zum Zurückholen und die Sprünge in seine Oberfläche.

⚠️ **Geprüft wird die Fähigkeit, nicht der Name des Wegs.** Ein dritter Weg
mit Gründen bekäme sie, ohne dass jemand eine Liste pflegt - und genau das
soll hier festgehalten sein.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.beschaffung import ARR, NEX, Kennt, get_beschaffung
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, FILM_UHD, KEY, URL, FakeNexcrate


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
def nex(db: Session, nexcrate: FakeNexcrate) -> Any:
    save_settings(db, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY})
    nex_fassungen.schreiben(db, nexcrate.versions)
    db.commit()
    return load_settings(db, frisch=True)


@pytest.fixture
def nex_client_admin(admin_client: TestClient, nexcrate: FakeNexcrate) -> TestClient:
    admin_client.put(
        "/api/settings",
        json={"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY},
    )
    return admin_client


# --------------------------------------------------------------------------
# Warum ein Titel noch nicht da ist


async def test_der_grund_steht_je_fassung_nicht_am_titel(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ nexbeat-Befund 7: ``next_search_reason`` sagte „nichts gewollt",
    während eine Fassung sehr wohl gesucht wurde."""
    nexcrate.film(603)
    nexcrate.why[("movie", "tmdb:603")] = {
        "title": {"kind": "movie", "ref": "tmdb:603", "name": "Example Movie"},
        "automatic": True,
        "search_wish": False,
        "next_search_reason": "nothing_wanted",
        "versions": [
            {"version_id": FILM_HD, "because": {"code": "available", "params": {}}},
            {
                "version_id": FILM_UHD,
                "because": {
                    "code": "version_not_ready",
                    "params": {"reasons": [{"code": "no_indexer"}, {"code": "no_profile"}]},
                },
            },
        ],
    }

    gefunden = await get_beschaffung(nex).warum([Kennt(media_type="movie", tmdb_id=603)])

    stand = gefunden[0]
    assert stand.bekannt is True
    assert stand.automatisch is True
    nach_fassung = {grund.fassung: grund for grund in stand.gruende}
    assert nach_fassung[FILM_HD].code == "available"
    assert nach_fassung[FILM_UHD].code == "version_not_ready"
    # ⚠️ Die Untergründe gehören zur Fassung, nicht in die Werte.
    assert nach_fassung[FILM_UHD].darunter == ("no_indexer", "no_profile")
    assert "reasons" not in nach_fassung[FILM_UHD].werte


async def test_ein_unbekannter_titel_sagt_das_statt_leer_zu_sein(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    gefunden = await get_beschaffung(nex).warum([Kennt(media_type="movie", tmdb_id=999999)])
    assert gefunden[0].bekannt is False
    assert gefunden[0].gruende == ()


async def test_der_arr_weg_sagt_nicht_warum(db: Session) -> None:
    """⚠️ Nicht dasselbe wie „es gibt keinen Grund"."""
    save_settings(db, {"beschaffung": ARR})
    settings = load_settings(db, frisch=True)
    weg = get_beschaffung(settings)

    assert weg.faehigkeiten().warum is False
    gefunden = await weg.warum([Kennt(media_type="movie", tmdb_id=603)])
    assert gefunden[0].bekannt is False


def test_die_adresse_sagt_es_auch_nach_aussen(arr_client: TestClient) -> None:
    antwort = arr_client.get("/api/beschaffung/warum/movie/603")
    assert antwort.status_code == 200
    assert antwort.json()["beantwortbar"] is False


def test_die_gruende_kommen_als_kennung_nach_aussen(
    nex_client_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    nexcrate.film(603)
    nexcrate.why[("movie", "tmdb:603")] = {
        "title": {"kind": "movie", "ref": "tmdb:603", "name": "Example Movie"},
        "automatic": False,
        "search_wish": True,
        "versions": [{"version_id": FILM_HD, "because": {"code": "no_release", "params": {}}}],
    }

    daten = nex_client_admin.get("/api/beschaffung/warum/movie/603").json()

    assert daten["beantwortbar"] is True
    assert daten["suchwunsch"] is True
    assert daten["gruende"] == [
        {"fassung": FILM_HD, "code": "no_release", "werte": {}, "darunter": []}
    ]


# --------------------------------------------------------------------------
# Der Papierkorb


def _eintrag(**werte: Any) -> dict[str, Any]:
    grund = {
        "entry_id": 7,
        "kind": "movie",
        "ref": "tmdb:603",
        "name": "Example Movie",
        "year": 1999,
        "version_id": FILM_HD,
        "season": None,
        "episodes": [],
        "file_name": "Example Movie (1999).mkv",
        "size_bytes": 8_000_000_000,
        "deleted_at": "2026-09-23T06:00:00Z",
        "deleted_by": "key",
        "deleted_by_name": "Nexview",
        "present": True,
        "in_library": True,
    }
    return {**grund, **werte}


def test_der_papierkorb_nennt_beide_arten_von_nein(
    nex_client_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    """⚠️ Datei weg und Titel weg sind zwei verschiedene Gründe, warum
    Zurückholen nicht geht - ein Knopf, der beides verschweigt, lügt."""
    nexcrate.recycle = [
        _eintrag(entry_id=7),
        _eintrag(entry_id=8, present=False),
        _eintrag(entry_id=9, in_library=False),
    ]

    daten = nex_client_admin.get("/api/beschaffung/papierkorb").json()

    nach_id = {zeile["eintrag_id"]: zeile for zeile in daten["eintraege"]}
    assert nach_id[7]["datei_da"] is True and nach_id[7]["im_bestand"] is True
    assert nach_id[8]["datei_da"] is False
    assert nach_id[9]["im_bestand"] is False
    assert nach_id[7]["tmdb_id"] == 603


def test_ein_album_bleibt_draussen(
    nex_client_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    """Nexview führt keine Musik; eine Zeile ohne Bezug wäre nur ein Knopf
    auf fremdem Bestand."""
    nexcrate.recycle = [_eintrag(entry_id=7), _eintrag(entry_id=8, kind="album", ref="mbid:x")]

    daten = nex_client_admin.get("/api/beschaffung/papierkorb").json()
    assert [zeile["eintrag_id"] for zeile in daten["eintraege"]] == [7]


def test_eine_serie_heisst_nach_aussen_tv(
    nex_client_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    nexcrate.recycle = [_eintrag(kind="series", ref="tmdb:1399", season=2, episodes=[3, 4])]

    zeile = nex_client_admin.get("/api/beschaffung/papierkorb").json()["eintraege"][0]
    assert zeile["media_type"] == "tv"
    assert zeile["staffel"] == 2 and zeile["folgen"] == [3, 4]


def test_zurueckholen_geht_an_den_weg(
    nex_client_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    antwort = nex_client_admin.post("/api/beschaffung/papierkorb/7/zurueckholen")
    assert antwort.status_code == 204
    assert any(ruf[1].endswith("/recycle-bin/7/restore") for ruf in nexcrate.calls)


def test_im_arr_betrieb_gibt_es_diesen_papierkorb_nicht(arr_client: TestClient) -> None:
    """⚠️ Arrs Papierkorb ist ein **Ordner** - daraus holt niemand etwas zurück."""
    antwort = arr_client.get("/api/beschaffung/papierkorb")
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "not_in_this_mode"


# --------------------------------------------------------------------------
# Die Sprünge


def test_ohne_adresse_nach_aussen_gibt_es_keinen_sprung(nex: Any, nexcrate: FakeNexcrate) -> None:
    """⚠️ Gemessen: ``web_url`` ist ``null``, solange drüben nichts steht.

    Nexviews eigene Sicht einzusetzen führte einen Besucher von draußen ins
    Leere.
    """
    assert nexcrate.web_url is None
    assert get_beschaffung(nex).spruenge().papierkorb == ""


def test_mit_adresse_werden_die_vorlagen_fertig(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.web_url = "https://nexcrate.example.com"
    system.merken(nexcrate._system())

    sprung = get_beschaffung(load_settings(db, frisch=True)).spruenge()

    assert sprung.papierkorb == "https://nexcrate.example.com/open/recycle-bin"
    # Vorlagen mit Platzhaltern bleiben Vorlagen - die Oberfläche setzt ein.
    assert sprung.titel == "https://nexcrate.example.com/open/title/{kind}/{ref}"


def test_der_arr_weg_springt_nirgendwohin(db: Session) -> None:
    save_settings(db, {"beschaffung": ARR})
    sprung = get_beschaffung(load_settings(db, frisch=True)).spruenge()
    assert sprung.titel == "" and sprung.papierkorb == ""


def test_die_konfiguration_traegt_faehigkeiten_und_spruenge(
    nex_client_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    daten = nex_client_admin.get("/api/config").json()

    assert daten["beschaffung"] == NEX
    assert daten["beschaffung_kann"]["warum"] is True
    assert daten["beschaffung_kann"]["papierkorb"] is True
    # Ohne Adresse nach außen steht dort nichts - und zwar gar nichts, nicht
    # ein leerer Eintrag je Name.
    assert daten["beschaffung_sprung"] == {}


def test_im_arr_betrieb_kann_der_weg_weniger(arr_client: TestClient) -> None:
    daten = arr_client.get("/api/config").json()
    assert daten["beschaffung"] == ARR
    assert daten["beschaffung_kann"]["warum"] is False
    assert daten["beschaffung_kann"]["papierkorb"] is False


def test_zielwahl_folgt_dem_riegel_der_listen_adresse(
    nex_client_admin: TestClient,
) -> None:
    """Rundgang-Befund 6: ``zielwahl`` sagt der Oberflaeche, ob es Ordner und
    Profil zu waehlen gibt. Sie muss genau dann nein sagen, wenn
    ``/api/arr/{art}/options`` mit ``not_in_this_mode`` abweist; sonst holt
    das Anfrageformular die Listen und zeigt nur das ``409``."""
    nex = nex_client_admin.get("/api/config").json()
    assert nex["beschaffung_kann"]["zielwahl"] is False
    abweisung = nex_client_admin.get("/api/arr/movie/options")
    assert abweisung.status_code == 409
    assert abweisung.json()["detail"]["code"] == "not_in_this_mode"


def test_im_arr_betrieb_gibt_es_die_zielwahl(arr_client: TestClient) -> None:
    arr = arr_client.get("/api/config").json()
    assert arr["beschaffung_kann"]["zielwahl"] is True


def test_papierkorb_ist_gesperrt_ohne_operate_recht(nexcrate: FakeNexcrate) -> None:
    """Ohne ``operate`` sperrt schon die Standpruefung jeden schreibenden
    Aufruf (``nexcrate_recht_fehlt``) - dann darf ``faehigkeiten()`` den
    Papierkorb nicht mehr zusagen."""
    nexcrate.scopes = ["read", "request"]
    system.merken(nexcrate._system())

    assert system.faehigkeiten().papierkorb is False


def test_papierkorb_bleibt_zugesagt_ohne_gelesenen_stand() -> None:
    """Ohne gelesenen Stand gilt weiter die Zusage des Vertrags."""
    system.vergessen()

    assert system.faehigkeiten().papierkorb is True


# --------------------------------------------------------------------------
# Was es im NEX-Betrieb nicht mehr zu wählen gibt


def test_im_nex_betrieb_waehlt_niemand_mehr_ordner_und_profil(db: Session) -> None:
    """⚠️ Sonst zeigte die Freigabe zwei Listen, die ihren Inhalt bei einer
    Adresse holen, die ``409`` antwortet - und jede Anfrage wartete auf eine
    Wahl, die niemand treffen kann.

    Die Einstellung selbst bleibt gespeichert: Wer zurückwechselt, findet sie
    wieder.
    """
    save_settings(db, {"beschaffung": ARR, "movie_root_folder_mode": "approver"})
    assert load_settings(db, frisch=True).approver_picks_target("movie") is True

    save_settings(db, {"beschaffung": NEX})
    frisch = load_settings(db, frisch=True)
    assert frisch.approver_picks_target("movie") is False
    assert frisch.movie_root_folder_mode == "approver"


def test_die_konfiguration_sagt_es_der_oberflaeche_weiter(
    nex_client_admin: TestClient,
) -> None:
    nex_client_admin.put("/api/settings", json={"movie_root_folder_mode": "approver"})
    daten = nex_client_admin.get("/api/config").json()
    assert daten["approver_picks_target_movie"] is False


# --------------------------------------------------------------------------
# Was der Durchlauf gegen eine echte nexcrate fand (Scheibe S9)


def test_die_hauptfassung_ist_im_nex_betrieb_keine_arr_kennung(
    nex: Any, db: Session
) -> None:
    """⚠️ Sonst bietet jede Karte und jedes Formular eine Fassung an, die es in
    dieser Installation gar nicht gibt - und die Anfrage darauf scheitert erst
    beim Absenden.

    Gefunden im Durchlauf gegen eine echte nexcrate, nicht von einem Test.
    """
    from app.services import fassungen as fassungen_dienst

    for art in ("movie", "tv"):
        haupt = fassungen_dienst.hauptkennung(art)
        assert haupt.startswith("v_"), f"{art}: {haupt}"
        assert fassungen_dienst.info(load_settings(db, frisch=True), haupt).quelle == NEX


def test_im_arr_betrieb_bleibt_die_hauptfassung_die_standard_instanz(db: Session) -> None:
    """Die Gegenprobe - sonst wäre die Reparatur oben eine Verschiebung."""
    from app.services import fassungen as fassungen_dienst

    save_settings(db, {"beschaffung": ARR})
    load_settings(db, frisch=True)
    assert fassungen_dienst.hauptkennung("movie") == "radarr-standard"
    assert fassungen_dienst.hauptkennung("tv") == "sonarr-standard"


def test_die_konfiguration_bietet_nur_fassungen_an_die_es_gibt(
    nex_client_admin: TestClient,
) -> None:
    daten = nex_client_admin.get("/api/config").json()
    for fassung in daten["fassungen"]:
        assert fassung["quelle"] == NEX, fassung
        assert fassung["kennung"].startswith("v_"), fassung


def test_die_instanz_gesundheit_gibt_es_in_beiden_betriebsarten(
    nex_client_admin: TestClient,
) -> None:
    """⚠️ Sie lag einmal hinter dem Riegel der Arr-Werkzeuge und antwortete
    ``409`` - obwohl die Dienste-Seite sie bei **jedem** Aufbau fragt und es
    hier sehr wohl eine Instanz gibt (Bauplan 9)."""
    antwort = nex_client_admin.get("/api/settings/instanzen/gesundheit")
    assert antwort.status_code == 200, antwort.text
    namen = [zeile["kennung"] for zeile in antwort.json()["instanzen"]]
    assert namen == ["nexcrate"]


def test_die_verbindungsleuchte_auch(nex_client_admin: TestClient) -> None:
    antwort = nex_client_admin.get("/api/settings/instanzen/verbindung")
    assert antwort.status_code == 200, antwort.text
    zeilen = antwort.json()["instanzen"]
    assert [z["kennung"] for z in zeilen] == ["nexcrate"]
    assert zeilen[0]["erreichbar"] is True


# --------------------------------------------------------------------------
# Gescheiterte Downloads (Rundgang-Befund 9)


def _download(kennung: int, zustand: str, problem: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "download_id": kennung,
        "title": {"kind": "movie", "ref": f"tmdb:{600 + kennung}", "name": f"Example {kennung}"},
        "state": zustand,
        "progress": 40.0 if zustand == "downloading" else 0.0,
        "size_bytes": 1000,
        # nexcrate nennt den Rest nur, solange der Download laeuft.
        "remaining_bytes": 600 if zustand == "downloading" else None,
        "remaining_seconds": 90 if zustand == "downloading" else None,
        "protocol": "usenet",
        "problem": problem,
    }


def test_ein_gescheiterter_download_laeuft_nicht(
    nex_client_admin: TestClient, nexcrate: FakeNexcrate
) -> None:
    """Gemessen an der Live-Instanz am 24.09.2026: 42 Eintraege mit
    ``state: failed`` und ohne ``problem`` standen unter „Läuft“ mit
    Fortschrittsbalken. Wartet ein gescheiterter auf den Betreiber, bringt
    nexcrate ``problem.needs_owner`` mit, und er gehoert nach oben."""
    nexcrate.queue = [
        _download(1, "downloading"),
        _download(2, "failed"),
        _download(3, "failed"),
        _download(
            4,
            "failed",
            {
                "code": "download_failed",
                "needs_owner": True,
                "message": "The download failed.",
                "params": {},
                "actions": ["remove_and_search", "remove"],
                "automatic": [],
            },
        ),
    ]

    daten = nex_client_admin.get("/api/admin/downloads").json()

    assert [zeile["titel"] for zeile in daten["laufend"]] == ["Example 1"]
    assert [zeile["titel"] for zeile in daten["haenger"]] == ["Example 4"]
    assert daten["gescheitert"] == 2


# --------------------------------------------------------------------------
# Gesundheit ohne nexcrates Satz (Rundgang-Befund 5)


def test_gesundheit_geht_als_kennung_hinaus(nex_client_admin: TestClient) -> None:
    """Dienste-Seite und Analyse bekamen nexcrates englischen Satz. Mit
    Kennung geht kein Satz hinaus, nur Kennung und Werte."""
    from app.db import SessionLocal
    from app.models import ArrGesundheit

    with SessionLocal() as db:
        db.add(
            ArrGesundheit(
                kennung="nexcrate",
                stand=[
                    {
                        "schluessel": "automatic_off:movie",
                        "typ": "warning",
                        "text": "The automatic for movie is off; nothing loads by itself.",
                        "code": "automatic_off",
                        "params": {"kind": "movie"},
                    }
                ],
            )
        )
        db.commit()

    gesundheit = nex_client_admin.get("/api/settings/instanzen/gesundheit").json()
    analyse = nex_client_admin.get("/api/admin/analyse").json()

    erwartet = {"typ": "warning", "text": "", "code": "automatic_off", "params": {"kind": "movie"}}
    assert gesundheit["instanzen"][0]["probleme"] == [erwartet]
    zeilen = [z for z in analyse["instanzen"] if z["kennung"] == "nexcrate"]
    assert zeilen[0]["meldungen"] == [erwartet]
    assert "automatic for" not in str(gesundheit) + str(analyse)


# --------------------------------------------------------------------------
# Download-Verlauf nach dem Umstieg (Rundgang-Befund 7)


def test_der_verlauf_nennt_alte_instanzen_beim_namen(nex_client_admin: TestClient) -> None:
    """Nach dem Umstieg standen Eintraege aus der Arr-Zeit mit ``radarr-standard``
    statt „Radarr FHD“ da. Die Arr-Fassungen bleiben stillgelegt in der
    Tabelle stehen, samt Namen."""
    from app.db import SessionLocal
    from app.models import DownloadVerlauf, Fassung

    with SessionLocal() as db:
        zeile = db.get(Fassung, "radarr-standard")
        assert zeile is not None and zeile.quelle == ARR
        zeile.name = "Radarr FHD"
        zeile.aktiv = False
        db.add_all(
            [
                DownloadVerlauf(kennung="radarr-standard", titel="Alt", was="entfernen"),
                DownloadVerlauf(kennung="sonarr-verschwunden", titel="Weg", was="entfernen"),
            ]
        )
        db.commit()

    zeilen = nex_client_admin.get("/api/admin/downloads/verlauf").json()

    assert {z["titel"]: z["instanz"] for z in zeilen} == {
        "Alt": "Radarr FHD",
        # Ohne Zeile in der Tabelle bleibt die Kennung, besser als nichts.
        "Weg": "sonarr-verschwunden",
    }
