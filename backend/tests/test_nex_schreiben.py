"""Die Schreibwege im NEX-Betrieb: anfragen, zurücknehmen, einfrieren, löschen.

Gegen ``tests/beschaffung/fake_nexcrate.py``, also gegen die gemessenen
Antworten. Der echte Client läuft mit.

⚠️ **Der Prüfgegenstand ist fast überall der Umfang.** Was Nexview an
nexcrate schickt, entscheidet, was auf der Platte passiert - und genau da
liegen die teuren Fehler: eine Rücknahme, die einem anderen Benutzer die
Serie unter den Füßen wegzieht, oder ein Löschen, das die Überwachung
anlässt und die Datei gleich wieder holt.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    DownloadHaenger,
    MediaRequest,
    MediaType,
    RequestStatus,
    Role,
    StorageEntry,
    StorageState,
    User,
)
from app.security import hash_password
from app.services import nachreichen, requests_service
from app.services.beschaffung import NEX, DownloadFehler, get_beschaffung
from app.services.beschaffung.nex import auftraege, system
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, FILM_UHD, KEY, SERIE_HD, URL, FakeNexcrate

GB = 1024**3


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


def _nutzer(db: Session, name: str = "schreiber") -> User:
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
        "fassung_kennung": FILM_HD,
        "status": RequestStatus.approved,
        "arr_id": 603,
    }
    anfrage = MediaRequest(**{**grund, **werte})
    db.add(anfrage)
    db.commit()
    return anfrage


def _gesendet(nexcrate: FakeNexcrate, pfadende: str) -> list[Any]:
    return [k[3] for k in nexcrate.calls if k[1].endswith(pfadende)]


# --- Anfragen -----------------------------------------------------------------


async def test_eine_anfrage_nennt_fassung_herkunft_und_suchwunsch(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.film(603)
    person = _nutzer(db)
    anfrage = _anfrage(db, person)

    kennung = await get_beschaffung(nex).anfragen(db, anfrage)

    koerper = _gesendet(nexcrate, "/requests")[0]
    assert koerper["kind"] == "movie" and koerper["ref"] == "tmdb:603"
    assert koerper["versions"] == [FILM_HD]
    assert koerper["search_now"] is True
    assert koerper["origin"] == f"nexview:request:{anfrage.id}"
    # ⚠️ Im NEX-Betrieb ist die Kennung bei der Quelle die TMDB-Nummer.
    assert kennung == 603


async def test_der_name_des_anfragenden_geht_nur_mit_schalter_hinaus(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """N19: nexcrate erfährt von Nexviews Benutzern nichts, solange nichts anderes gilt."""
    nexcrate.film(603)
    person = _nutzer(db)
    anfrage = _anfrage(db, person)

    await get_beschaffung(nex).anfragen(db, anfrage)
    assert "origin_label" not in _gesendet(nexcrate, "/requests")[0]

    save_settings(db, {"nexcrate_anzeigename": True})
    mit_namen = load_settings(db, frisch=True)
    await get_beschaffung(mit_namen).anfragen(db, anfrage)
    assert _gesendet(nexcrate, "/requests")[1]["origin_label"] == "schreiber"


@pytest.mark.parametrize(
    ("werte", "erwartet"),
    [
        ({"media_type": MediaType.movie}, {}),
        (
            {"media_type": MediaType.tv, "tmdb_id": 1399, "fassung_kennung": SERIE_HD},
            {"series": {"seasons": "all", "future_seasons": True}},
        ),
        (
            {
                "media_type": MediaType.tv,
                "tmdb_id": 1399,
                "fassung_kennung": SERIE_HD,
                "season": 2,
            },
            {"series": {"seasons": [2]}},
        ),
        (
            {
                "media_type": MediaType.tv,
                "tmdb_id": 1399,
                "fassung_kennung": SERIE_HD,
                "season": 2,
                "episodes": [3, 1],
            },
            {
                "series": {
                    "episodes": [
                        {"season": 2, "episode": 1},
                        {"season": 2, "episode": 3},
                    ]
                }
            },
        ),
    ],
)
def test_der_umfang_einer_anfrage(
    db: Session, werte: dict[str, Any], erwartet: dict[str, Any]
) -> None:
    """Der Umfang steht unter dem Schlüssel der Medienart, nie oben (Form 3)."""
    person = _nutzer(db)
    anfrage = _anfrage(db, person, **werte)
    assert auftraege.umfang(anfrage) == erwartet


async def test_dieselbe_anfrage_geht_nie_zweimal_gleichzeitig_hinaus(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ nexbeat-Befund 17: Zwei gleichzeitige Anfragen endeten in nexcrate in `500`.

    Gemessen an einer echten nexcrate: Beide wollten denselben unbekannten
    Titel anlegen, die zweite scheiterte an einer eindeutigen Spalte. Nexview
    hat mehrere Benutzer je Titel und trifft genau das - also geht dieselbe
    Anfrage nie zweimal gleichzeitig hinaus.
    """
    import asyncio

    from app.services.beschaffung.nex.client import NexcrateClient

    nexcrate.film(603)
    person = _nutzer(db)
    eine = _anfrage(db, person)
    andere = _anfrage(db, _nutzer(db, "zweiter"))

    laufend = 0
    hoechstens = 0
    echt = NexcrateClient.request

    async def langsam(self: NexcrateClient, koerper: dict[str, Any]) -> Any:
        nonlocal laufend, hoechstens
        laufend += 1
        hoechstens = max(hoechstens, laufend)
        # Genau hier faende der Wettlauf statt: Die Schleife darf wechseln.
        await asyncio.sleep(0.02)
        try:
            return await echt(self, koerper)
        finally:
            laufend -= 1

    monkeypatch.setattr(NexcrateClient, "request", langsam)
    weg = get_beschaffung(nex)
    await asyncio.gather(weg.anfragen(db, eine), weg.anfragen(db, andere))

    assert hoechstens == 1, "zwei Anfragen auf denselben Titel waren zugleich unterwegs"
    assert len(_gesendet(nexcrate, "/requests")) == 2


async def test_zwei_verschiedene_titel_warten_nicht_aufeinander(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe: Die Sperre gilt je Titel, nicht fuer alles.

    Sonst waere eine Sammelfreigabe von hundert Titeln hundertmal
    hintereinander - und genau dafuer ist der Stapel gedacht (N24).
    """
    import asyncio

    from app.services.beschaffung.nex.client import NexcrateClient

    nexcrate.film(603)
    nexcrate.film(604, name="Another Example")
    person = _nutzer(db)
    eine = _anfrage(db, person)
    andere = _anfrage(db, person, tmdb_id=604, title="Another Example", arr_id=604)

    laufend = 0
    hoechstens = 0
    echt = NexcrateClient.request

    async def langsam(self: NexcrateClient, koerper: dict[str, Any]) -> Any:
        nonlocal laufend, hoechstens
        laufend += 1
        hoechstens = max(hoechstens, laufend)
        await asyncio.sleep(0.02)
        try:
            return await echt(self, koerper)
        finally:
            laufend -= 1

    monkeypatch.setattr(NexcrateClient, "request", langsam)
    weg = get_beschaffung(nex)
    await asyncio.gather(weg.anfragen(db, eine), weg.anfragen(db, andere))

    assert hoechstens == 2, "verschiedene Titel warteten aufeinander"


async def test_ein_500_ist_ungewiss_und_kein_fehlschlag(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """nexbeat-Befund 17: Der Auftrag kann angekommen sein - noch einmal senden."""
    nexcrate.film(603)
    person = _nutzer(db)
    anfrage = _anfrage(db, person)
    nexcrate.next_answer["POST /api/v1/requests"] = httpx.Response(
        500, json={"code": "internal_error", "message": "e5458f08", "params": {}}
    )

    with pytest.raises(Exception):  # noqa: B017 - push_to_arr macht daraus 502
        await requests_service.push_to_arr(db, nex, anfrage)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.approved


# --- Zurücknehmen ----------------------------------------------------------------


async def test_zuruecknehmen_laesst_liegen_was_ein_anderer_noch_will(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ Bauplan 6.2: Zurückgenommen wird nur der **freie** Umfang."""
    nexcrate.film(603, versionen=[nexcrate.fassung(FILM_HD, "available", origin="nexview:request:1")])
    person = _nutzer(db)
    meine = _anfrage(db, person, status=RequestStatus.searching)
    _anfrage(db, _nutzer(db, "zweiter"), status=RequestStatus.searching)

    bericht = await get_beschaffung(nex).abbrechen(db, meine)

    assert "another request" in bericht
    assert _gesendet(nexcrate, "/withdraw") == []


async def test_was_der_betreiber_selbst_ueberwacht_bleibt(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Ohne ``nexview:``-Marke fasst Nexview die Fassung nicht an (6.2)."""
    nexcrate.film(603, versionen=[nexcrate.fassung(FILM_HD, "available", origin=None)])
    person = _nutzer(db)
    meine = _anfrage(db, person, status=RequestStatus.searching)

    bericht = await get_beschaffung(nex).abbrechen(db, meine)

    assert "owner watches" in bericht
    assert _gesendet(nexcrate, "/withdraw") == []


async def test_zuruecknehmen_schickt_umfang_und_dateien(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.serie(1399, versionen=[nexcrate.fassung(SERIE_HD, "available", origin="nexview:request:1")])
    person = _nutzer(db)
    meine = _anfrage(
        db,
        person,
        media_type=MediaType.tv,
        tmdb_id=1399,
        fassung_kennung=SERIE_HD,
        season=2,
        arr_id=1399,
        status=RequestStatus.searching,
    )

    await get_beschaffung(nex).abbrechen(db, meine)

    koerper = _gesendet(nexcrate, "/withdraw")[0]
    assert koerper["versions"] == [SERIE_HD]
    assert koerper["series"] == {"seasons": [2]}
    # ⚠️ Gelöscht wird über ``withdraw`` mit ``delete_files`` - nicht über
    # ``delete-files``, das ließe die Überwachung an (Bauplan 6.4).
    assert koerper["delete_files"] is True


def test_der_freie_umfang_eines_folgen_pakets(db: Session) -> None:
    """Zwei Pakete derselben Staffel: Nur die eigenen Folgen sind frei."""
    person = _nutzer(db)
    meine = _anfrage(
        db,
        person,
        media_type=MediaType.tv,
        tmdb_id=1399,
        fassung_kennung=SERIE_HD,
        season=2,
        episodes=[1, 2, 3],
        status=RequestStatus.searching,
    )
    _anfrage(
        db,
        _nutzer(db, "zweiter"),
        media_type=MediaType.tv,
        tmdb_id=1399,
        fassung_kennung=SERIE_HD,
        season=2,
        episodes=[3, 4],
        status=RequestStatus.searching,
    )

    frei = auftraege.freier_umfang(db, meine)

    assert frei == {"series": {"episodes": [{"season": 2, "episode": 1}, {"season": 2, "episode": 2}]}}


def test_eine_fremde_ganze_serie_laesst_nichts_frei(db: Session) -> None:
    person = _nutzer(db)
    meine = _anfrage(
        db,
        person,
        media_type=MediaType.tv,
        tmdb_id=1399,
        fassung_kennung=SERIE_HD,
        season=2,
        status=RequestStatus.searching,
    )
    _anfrage(
        db,
        _nutzer(db, "zweiter"),
        media_type=MediaType.tv,
        tmdb_id=1399,
        fassung_kennung=SERIE_HD,
        status=RequestStatus.searching,
    )

    assert auftraege.freier_umfang(db, meine) is None


def test_eine_andere_fassung_zaehlt_nicht_mit(db: Session) -> None:
    """4K und 1080p sind zwei Dateien - die eine hält die andere nicht auf."""
    person = _nutzer(db)
    meine = _anfrage(db, person, status=RequestStatus.searching)
    _anfrage(db, _nutzer(db, "zweiter"), fassung_kennung=FILM_UHD, status=RequestStatus.searching)

    assert auftraege.freier_umfang(db, meine) == {}


# --- Einfrieren und Speicher -------------------------------------------------------


async def test_einfrieren_laesst_die_datei_liegen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """N43: der dritte Weg neben Behalten und Entfernen."""
    nexcrate.film(603)
    person = _nutzer(db)
    anfrage = _anfrage(db, person, status=RequestStatus.searching)

    await auftraege.einfrieren(nex, anfrage)

    koerper = _gesendet(nexcrate, "/monitoring")[0]
    assert koerper == {"monitored": False, "versions": [FILM_HD]}


async def test_ein_posten_wird_ueber_den_papierkorb_geloescht(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    from app.services.beschaffung.nex import speicher

    nexcrate.film(603)
    zeile = StorageEntry(
        key=f"movie:{FILM_HD}:tmdb:603",
        media_type=MediaType.movie,
        fassung_kennung=FILM_HD,
        tmdb_id=603,
        title="Example Movie",
        size_bytes=8 * GB,
        state=StorageState.owned,
    )
    db.add(zeile)
    db.commit()
    nexcrate.next_answer["POST /api/v1/titles/movie/tmdb:603/withdraw"] = httpx.Response(
        200, json={"title_removed": False, "versions": [{"version_id": FILM_HD, "files_recycled": 1}]}
    )

    await speicher.loeschen(nex, zeile, 603, lambda: None)

    koerper = _gesendet(nexcrate, "/withdraw")[0]
    assert koerper["delete_files"] is True and koerper["versions"] == [FILM_HD]


async def test_ein_posten_ohne_datei_laesst_sich_nicht_loeschen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Zählbar, aber nicht löschbar - wie im ARR-Betrieb (Bauplan 6.4)."""
    from app.services.beschaffung import NichtsZuLoeschen
    from app.services.beschaffung.nex import speicher

    nexcrate.film(603)
    zeile = StorageEntry(
        key=f"movie:{FILM_HD}:tmdb:603",
        media_type=MediaType.movie,
        fassung_kennung=FILM_HD,
        tmdb_id=603,
        title="Example Movie",
        size_bytes=8 * GB,
        state=StorageState.owned,
    )
    db.add(zeile)
    db.commit()

    with pytest.raises(NichtsZuLoeschen):
        await speicher.loeschen(nex, zeile, 603, lambda: None)


async def test_stilllegen_meldet_nichts_wenn_nexcrate_den_titel_nicht_fuehrt(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    from app.services.beschaffung.nex import speicher

    zeile = StorageEntry(
        key=f"movie:{FILM_HD}:tmdb:603",
        media_type=MediaType.movie,
        fassung_kennung=FILM_HD,
        tmdb_id=603,
        title="Example Movie",
        size_bytes=0,
        state=StorageState.owned,
    )
    db.add(zeile)
    db.commit()

    assert await speicher.stilllegen(nex, zeile) is None
    assert _gesendet(nexcrate, "/monitoring") == []


# --- Download-Aktionen ---------------------------------------------------------------


def _haenger(db: Session, **werte: Any) -> DownloadHaenger:
    from app.models import utcnow

    grund: dict[str, Any] = {
        "kennung": "nexcrate",
        "download_id": "7",
        "media_type": "movie",
        "arr_id": 603,
        "grund": "no_video",
        "aktionen": {
            "erlaubt": ["remove_and_search", "remove"],
            "automatisch": ["remove_and_search"],
        },
        "haengt_seit": utcnow(),
    }
    zeile = DownloadHaenger(**{**grund, **werte})
    db.add(zeile)
    db.commit()
    return zeile


async def test_nur_was_nexcrate_anbietet_wird_gesendet(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    zeile = _haenger(db)
    nexcrate.queue = [
        {
            "download_id": 7,
            "title": {"kind": "movie", "ref": "tmdb:603", "name": "Example Movie"},
            "state": "problem",
            "problem": {"code": "no_video", "needs_owner": True, "actions": ["remove"], "automatic": []},
        }
    ]

    await get_beschaffung(nex).download_entfernen(db, zeile.id, neu_suchen=False, wer=None)

    aufrufe = [k[1] for k in nexcrate.calls if "/downloads/7/" in k[1]]
    assert aufrufe == ["/api/v1/downloads/7/remove"]


async def test_was_nexcrate_nicht_anbietet_wird_nicht_erfunden(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Ein Knopf, der ins Leere greift, ist schlimmer als keiner."""
    zeile = _haenger(db, aktionen={"erlaubt": ["remove"], "automatisch": []})

    with pytest.raises(DownloadFehler) as gefangen:
        await get_beschaffung(nex).download_erneut_pruefen(db, zeile.id, wer=None)

    assert gefangen.value.code == "download_action_not_offered"


async def test_die_automatik_darf_nur_was_nexcrate_ihr_erlaubt(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ Bauplan 6.6: nexcrates ``automatic`` ist die einzige Erlaubnis."""
    zeile = _haenger(db)
    nexcrate.queue = [
        {
            "download_id": 7,
            "title": {"kind": "movie", "ref": "tmdb:603", "name": "Example Movie"},
            "state": "problem",
            "problem": {
                "code": "no_video",
                "needs_owner": True,
                "actions": ["remove_and_search", "remove"],
                "automatic": ["remove_and_search"],
            },
        }
    ]

    with pytest.raises(DownloadFehler) as gefangen:
        await get_beschaffung(nex).download_entfernen(
            db, zeile.id, neu_suchen=False, wer=None, automatisch=True
        )
    assert gefangen.value.code == "download_action_not_automatic"

    # Mit Suche steht sie in ``automatic`` - das geht.
    await get_beschaffung(nex).download_entfernen(
        db, zeile.id, neu_suchen=True, wer=None, automatisch=True
    )
    assert any("/downloads/7/remove_and_search" in k[1] for k in nexcrate.calls)


async def test_von_hand_zuordnen_nennt_die_dateien_und_das_trotzdem(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    zeile = _haenger(db, aktionen={"erlaubt": ["assign"], "automatisch": []})
    nexcrate.queue = [
        {
            "download_id": 7,
            "title": {"kind": "movie", "ref": "tmdb:603", "name": "Example Movie"},
            "state": "problem",
            "problem": {"code": "several_videos", "needs_owner": True, "actions": ["assign"], "automatic": []},
        }
    ]

    await get_beschaffung(nex).download_importieren(
        db, zeile.id, ["datei-1"], trotzdem=True, wer=None
    )

    koerper = next(k[3] for k in nexcrate.calls if k[1].endswith("/downloads/7/assign"))
    assert koerper["files"] == [{"key": "datei-1"}]
    assert koerper["confirm"] == ["not_better"]


# --- Nachreichen ------------------------------------------------------------------


async def test_nach_dem_umschalten_werden_freigegebene_nachgereicht(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ Sonst bleiben sie für immer auf „freigegeben" stehen (Bauplan 6.10)."""
    nexcrate.film(603)
    person = _nutzer(db)
    anfrage = _anfrage(db, person)
    save_settings(db, {"beschaffung_gewechselt_am": "2026-09-22T20:00:00"})
    frisch = load_settings(db, frisch=True)
    assert nachreichen.faellig(frisch) is True

    gereicht = await nachreichen.einmal(db, frisch)

    db.refresh(anfrage)
    assert gereicht == 1
    assert anfrage.status == RequestStatus.searching
    assert len(_gesendet(nexcrate, "/requests")) == 1


async def test_eine_anfrage_mit_fremder_fassung_bleibt_liegen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Abbildung macht der Umstiegsassistent (S7) - geraten wird nicht."""
    nexcrate.film(603)
    person = _nutzer(db)
    anfrage = _anfrage(db, person, fassung_kennung="radarr-standard")
    save_settings(db, {"beschaffung_gewechselt_am": "2026-09-22T20:00:00"})
    frisch = load_settings(db, frisch=True)

    gereicht = await nachreichen.einmal(db, frisch)

    db.refresh(anfrage)
    assert gereicht == 0
    assert anfrage.status == RequestStatus.approved
    assert _gesendet(nexcrate, "/requests") == []


def test_ohne_umschalten_wird_nichts_nachgereicht(db: Session) -> None:
    assert nachreichen.faellig(load_settings(db)) is False


def test_das_umschalten_setzt_den_merker(admin_client: Any) -> None:
    """⚠️ Ohne ihn bliebe jede freigegebene Anfrage nach dem Wechsel stehen."""
    with SessionLocal() as db:
        assert load_settings(db).beschaffung_gewechselt_am == ""

    assert admin_client.put("/api/settings", json={"beschaffung": "nex"}).status_code == 200

    with SessionLocal() as db:
        einstellungen = load_settings(db, frisch=True)
    assert einstellungen.beschaffung_gewechselt_am != ""
    assert nachreichen.faellig(einstellungen) is True


def test_ein_speichern_ohne_wechsel_setzt_ihn_nicht(admin_client: Any) -> None:
    """Sonst liefe das Nachreichen bei jedem Speichern der Einstellungen."""
    admin_client.put("/api/settings", json={"beschaffung": "arr", "default_region": "AT"})
    with SessionLocal() as db:
        assert load_settings(db, frisch=True).beschaffung_gewechselt_am == ""


# --- Der Papierkorb -----------------------------------------------------------------


async def test_der_papierkorb_ist_eine_liste_und_kein_ordner(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    """Arrs Papierkorb ist ein Ordner, nexcrates eine Liste mit Zurückholen (N22)."""
    from app.services.beschaffung import BeschaffungError

    nexcrate.recycle = [{"entry_id": 3, "title": {"kind": "movie", "ref": "tmdb:603"}}]
    weg = get_beschaffung(nex)

    assert await weg.papierkorb() == nexcrate.recycle
    assert weg.faehigkeiten().papierkorb is True

    with pytest.raises(BeschaffungError) as gefangen:
        await weg.papierkoerbe()
    assert gefangen.value.code == "not_in_this_mode"

    await weg.wiederherstellen(3)
    assert any(k[1].endswith("/recycle-bin/3/restore") for k in nexcrate.calls)

    # Und die Gegenrichtung: Radarr und Sonarr fuehren keine Liste, aus der
    # sich etwas zurueckholen liesse - eine leere Liste waere eine Luege.
    from dataclasses import replace

    with SessionLocal() as db:
        arr = get_beschaffung(replace(load_settings(db), beschaffung="arr"))
    assert arr.faehigkeiten().papierkorb is False
    with pytest.raises(BeschaffungError) as gefangen:
        await arr.papierkorb()
    assert gefangen.value.code == "not_in_this_mode"
