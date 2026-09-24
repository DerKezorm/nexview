"""Nach dem Umschalten: was liegen bleibt, muss man sehen.

⚠️ **Der Fehler, den diese Datei festhält** (gemessen am 24.09.2026): Eine
freigegebene Anfrage auf einer Fassung, die der neue Weg nicht kennt, zählte
beim Nachreichen als „liegen". Danach wurde der Merker geleert, und nichts
versuchte es je wieder. In der Oberfläche stand sie für immer auf
„freigegeben", die einzige Spur war eine Zeile im Protokoll.

Wiederholen hilft nicht, eine unbekannte Fassung wird dadurch nicht bekannt.
Deshalb bleibt das Nachreichen dabei fertig, und ein Befund übernimmt die
Sichtbarkeit: Er steht, solange es solche Anfragen gibt, und führt dorthin,
wo man sie zurücknehmen kann.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus, Role, User, utcnow
from app.security import hash_password
from app.services import abgleich_kern, befunde, nachreichen, requests_service, status_poller
from app.services.beschaffung import NEX, BeschaffungError
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, KEY, URL, FakeNexcrate

KENNUNG = "nachschub.fremde_fassung"


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


def _nutzer(db: Session, name: str = "besteller") -> User:
    person = User(username=name, password_hash=hash_password("test"), role=Role.user)
    db.add(person)
    db.commit()
    return person


def _anfrage(db: Session, person: User, **werte: Any) -> MediaRequest:
    grund: dict[str, Any] = {
        "user_id": person.id,
        "media_type": MediaType.movie,
        "tmdb_id": 603,
        "title": "Beispielfilm",
        "fassung_kennung": FILM_HD,
        "status": RequestStatus.approved,
        "arr_id": None,
    }
    anfrage = MediaRequest(**{**grund, **werte})
    db.add(anfrage)
    db.commit()
    return anfrage


def _ohne_fassung(db: Session, anfrage: MediaRequest) -> None:
    """Eine Zeile ohne Fassung, wie sie aus einer alten Datenbank kommt.

    Neu anlegen lässt das Modell so eine Zeile nicht (``_fassung_pflicht``);
    die Spalte bekam eine bestehende Installation aber per ``ALTER TABLE``.
    """
    db.execute(
        update(MediaRequest).where(MediaRequest.id == anfrage.id).values(fassung_kennung=None)
    )
    db.commit()


def _umgeschaltet(db: Session, vor: timedelta = timedelta(minutes=5)) -> Any:
    """Den Merker setzen, wie es das Umschalten tut: mit dem Zeitpunkt.

    Seit es eine Obergrenze gibt (``nachreichen.WIEDERHOLEN_BIS``), zaehlt der
    Zeitpunkt; ein fester alter Wert lag schon jenseits davon.
    """
    save_settings(db, {"beschaffung_gewechselt_am": (utcnow() - vor).isoformat()})
    return load_settings(db, frisch=True)


def _befund() -> list[befunde.Befund]:
    with SessionLocal() as sitzung:
        gefunden = befunde.sammeln(sitzung, load_settings(sitzung, frisch=True))
    return [b for b in gefunden if b.kennung == KENNUNG]


# --- Das Nachreichen selbst --------------------------------------------------------


async def test_was_liegen_bleibt_wird_gemeldet_und_der_merker_geleert(
    nex: Any, nexcrate: FakeNexcrate, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    person = _nutzer(db)
    anfrage = _anfrage(db, person, fassung_kennung="radarr-standard")
    frisch = _umgeschaltet(db)

    with caplog.at_level(logging.INFO, logger="nexview.requests"):
        ergebnis = await nachreichen.einmal(db, frisch)

    assert ergebnis.gereicht == 0
    assert ergebnis.liegen == 1
    assert any(
        "1 approved request(s)" in zeile.getMessage() for zeile in caplog.records
    ), [zeile.getMessage() for zeile in caplog.records]
    # Fertig ist das Nachreichen trotzdem: Wiederholen macht die Fassung nicht bekannt.
    assert nachreichen.faellig(load_settings(db, frisch=True)) is False
    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.approved
    assert _gesendet(nexcrate) == []

    # Die Sichtbarkeit übernimmt der Befund.
    treffer = _befund()
    assert len(treffer) == 1
    assert treffer[0].werte["anzahl"] == 1
    assert treffer[0].werte["titel"] == "Beispielfilm"
    assert treffer[0].ziel == "/admin/requests?filter=fremde_fassung"


async def test_fremde_fassungen_verdraengen_keine_bekannte(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ Je Durchgang gibt es nur eine begrenzte Zahl Plätze.

    Standen die ältesten Anfragen alle auf einer fremden Fassung, belegten sie
    jeden Platz, es ging nichts durch, und das Nachreichen erklärte sich für
    fertig. Die jüngere Anfrage auf einer bekannten Fassung bekam nie ihre
    Übergabe.
    """
    from datetime import datetime, timedelta

    nexcrate.film(603)
    person = _nutzer(db)
    anfang = datetime(2026, 9, 1)
    for nummer in range(nachreichen.JE_DURCHGANG):
        _anfrage(
            db,
            person,
            tmdb_id=700 + nummer,
            fassung_kennung="radarr-standard",
            requested_at=anfang + timedelta(minutes=nummer),
        )
    bekannt = _anfrage(db, person, requested_at=datetime(2026, 9, 10))
    frisch = _umgeschaltet(db)

    ergebnis = await nachreichen.einmal(db, frisch)

    db.refresh(bekannt)
    assert bekannt.status == RequestStatus.searching
    gesendet = _gesendet(nexcrate)
    assert [k["origin"] for k in gesendet] == [f"nexview:request:{bekannt.id}"]
    assert ergebnis.gereicht == 1
    assert ergebnis.liegen == nachreichen.JE_DURCHGANG


def _gesendet(nexcrate: FakeNexcrate) -> list[Any]:
    return [k[3] for k in nexcrate.calls if k[1].endswith("/requests")]


@pytest.mark.parametrize(
    "art",
    ["503", "zeitueberschreitung", "verbindung_abgelehnt", "fremde_seite", "ausgelastet"],
)
async def test_ein_ausfall_von_nexcrate_beendet_das_nachreichen_nicht(
    art: str, nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """⚠️ Nichts ging durch heisst nicht: nichts mehr zu tun.

    Bis zum 24.09.2026 leerte ein Durchgang ohne eine einzige Übergabe den
    Merker, auch wenn nexcrate nur gerade nicht antwortete. Die gültige
    Anfrage blieb danach für immer freigegeben, ohne Befund, denn ihre
    Fassung ist ja bekannt.

    ⚠️ Eine abgelehnte Verbindung (nexcrate aus) machte die Anfrage bis zum
    24.09.2026 zu „fehlgeschlagen": ``nexcrate_unreachable`` ist nicht
    ungewiss, und niemand versuchte es wieder.

    Eine fremde Seite mit 200 (die Anmeldeseite eines Proxys davor) und ein
    429 sind ebenso vorübergehend (``nexcrate_unexpected_answer``,
    ``nexcrate_busy``); sie hält nur der Korb, nicht die Ungewissheit.
    """
    nexcrate.film(603)
    person = _nutzer(db)
    gueltig = _anfrage(db, person)
    frisch = _umgeschaltet(db)
    if art == "503":
        nexcrate.next_answer["POST /api/v1/requests"] = httpx.Response(
            503, json={"detail": {"code": "unavailable", "message": "down"}}
        )
    elif art == "verbindung_abgelehnt":
        nexcrate.next_answer["POST /api/v1/requests"] = httpx.ConnectError("abgelehnt")
    elif art == "fremde_seite":
        nexcrate.next_answer["POST /api/v1/requests"] = httpx.Response(
            200,
            text="<html><head><title>Anmelden</title></head><body>Beispiel</body></html>",
            headers={"content-type": "text/html"},
        )
    elif art == "ausgelastet":
        nexcrate.next_answer["POST /api/v1/requests"] = httpx.Response(
            429,
            json={"code": "rate_limited", "message": "slow down", "params": {}},
            headers={"Retry-After": "1"},
        )
    else:
        nexcrate.next_answer["POST /api/v1/requests"] = httpx.ReadTimeout("zu langsam")

    ergebnis = await nachreichen.einmal(db, frisch)

    assert ergebnis.gereicht == 0
    db.refresh(gueltig)
    assert gueltig.status == RequestStatus.approved
    assert nachreichen.faellig(load_settings(db, frisch=True)) is True

    # Der nächste Durchgang übergibt, was beim ersten nicht ankam.
    ergebnis = await nachreichen.einmal(db, load_settings(db, frisch=True))

    assert ergebnis.gereicht == 1
    db.refresh(gueltig)
    assert gueltig.status == RequestStatus.searching

    # Und erst wenn nichts Bekanntes mehr offen ist, ist Schluss.
    await nachreichen.einmal(db, load_settings(db, frisch=True))
    assert nachreichen.faellig(load_settings(db, frisch=True)) is False


async def test_beim_klick_scheitert_eine_abgelehnte_verbindung_weiter(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Nur das Nachreichen hält eine abgelehnte Verbindung für vorübergehend.

    Wer freigibt, sieht den Fehler und kann es neu versuchen; stünde die
    Anfrage still auf „freigegeben", versuchte es ausserhalb des Nachreichens
    niemand wieder.
    """
    nexcrate.film(603)
    anfrage = _anfrage(db, _nutzer(db))
    nexcrate.next_answer["POST /api/v1/requests"] = httpx.ConnectError("abgelehnt")

    with pytest.raises(requests_service.RequestError):
        await requests_service.push_to_arr(db, nex, anfrage)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.failed


async def test_ein_ausfall_wird_als_warnung_ohne_stapel_protokolliert(
    nex: Any, nexcrate: FakeNexcrate, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Ein erwarteter Ausfall ist keine Ausnahme mit Stapelauszug.

    ``push_to_arr`` macht aus dem Fehler des Wegs einen ``RequestError``; der
    Zweig für ``BeschaffungError`` wurde deshalb nie erreicht, und jeder
    Ausfall landete mit Stapel als Programmfehler im Protokoll.
    """
    nexcrate.film(603)
    person = _nutzer(db)
    gueltig = _anfrage(db, person)
    frisch = _umgeschaltet(db)
    nexcrate.next_answer["POST /api/v1/requests"] = httpx.Response(
        503, json={"detail": {"code": "unavailable", "message": "down"}}
    )

    with caplog.at_level(logging.INFO, logger="nexview.requests"):
        await nachreichen.einmal(db, frisch)

    zeilen = [
        zeile
        for zeile in caplog.records
        if zeile.getMessage().startswith(f"Could not hand over request {gueltig.id} ")
    ]
    assert len(zeilen) == 1, [zeile.getMessage() for zeile in caplog.records]
    assert zeilen[0].levelno == logging.WARNING
    assert zeilen[0].exc_info is None


async def test_ohne_gelesene_fassungen_bleibt_der_merker_und_schweigt(
    nex: Any, nexcrate: FakeNexcrate, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Direkt nach dem Umschalten ist nexcrate womöglich noch nicht gelesen.

    Dann wäre jede Anfrage "fremd": Das Nachreichen zählte sie alle als
    liegend, schrieb das ins Protokoll und erklärte sich für fertig. Die
    gültigen kamen nie an. Dieselbe Regel wie beim Befund: nichts gelesen
    heisst nicht alles fremd.
    """
    nexcrate.film(603)
    person = _nutzer(db)
    gueltig = _anfrage(db, person)
    _anfrage(db, person, tmdb_id=604, fassung_kennung="radarr-standard")
    frisch = _umgeschaltet(db)
    nex_fassungen.vergessen()

    with caplog.at_level(logging.INFO, logger="nexview.requests"):
        ergebnis = await nachreichen.einmal(db, frisch)

    assert ergebnis.gereicht == 0
    assert ergebnis.liegen == 0
    assert [zeile.getMessage() for zeile in caplog.records] == []
    assert nachreichen.faellig(load_settings(db, frisch=True)) is True
    db.refresh(gueltig)
    assert gueltig.status == RequestStatus.approved
    assert _gesendet(nexcrate) == []


# --- Der Befund ---------------------------------------------------------------------


async def test_der_befund_zaehlt_auch_suchende_und_keine_wartenden(
    nex: Any, db: Session
) -> None:
    """Freigegeben und sucht: Beides wartet auf den Weg. Eine wartende Anfrage nicht."""
    person = _nutzer(db)
    _anfrage(db, person, tmdb_id=1, fassung_kennung="radarr-standard")
    _anfrage(
        db, person, tmdb_id=2, fassung_kennung="sonarr-standard",
        status=RequestStatus.searching, media_type=MediaType.tv,
    )
    _anfrage(
        db, person, tmdb_id=3, fassung_kennung="radarr-uhd",
        status=RequestStatus.pending_approval,
    )
    _anfrage(db, person, tmdb_id=4, fassung_kennung="radarr-uhd", status=RequestStatus.cancelled)
    _anfrage(db, person, tmdb_id=5)

    treffer = _befund()
    assert len(treffer) == 1
    assert treffer[0].werte["anzahl"] == 2


async def test_ohne_fassung_zaehlt_eine_anfrage_als_fremd(nex: Any, db: Session) -> None:
    """Auch für eine Anfrage ohne Fassung gibt es keinen Weg.

    ``NOT IN`` allein liesse NULL stillschweigend fallen.
    """
    person = _nutzer(db)
    _ohne_fassung(db, _anfrage(db, person, title="Erfundener Film ohne Fassung"))

    treffer = _befund()
    assert len(treffer) == 1
    assert treffer[0].werte == {"anzahl": 1, "titel": "Erfundener Film ohne Fassung"}


async def test_der_befund_verschwindet_nach_dem_zuruecknehmen(
    admin_client: TestClient, nex: Any, db: Session
) -> None:
    """Der Ausweg des Betreibers: zurücknehmen, und beim nächsten Mal ist er weg."""
    person = _nutzer(db)
    anfrage = _anfrage(db, person, fassung_kennung="radarr-standard")
    assert len(_befund()) == 1

    antwort = admin_client.post(f"/api/admin/requests/{anfrage.id}/cancel")

    assert antwort.status_code == 200, antwort.text
    assert _befund() == []


async def test_der_befund_verschwindet_mit_einer_bekannten_fassung(
    nex: Any, db: Session
) -> None:
    person = _nutzer(db)
    anfrage = _anfrage(db, person, fassung_kennung="radarr-standard")
    assert len(_befund()) == 1

    anfrage.fassung_kennung = FILM_HD
    db.commit()

    assert _befund() == []


async def test_ohne_bekannte_fassungen_schweigt_der_befund(
    nex: Any, db: Session
) -> None:
    """Noch nichts gelesen heisst nicht: alles fremd. Dann lieber schweigen."""
    person = _nutzer(db)
    _anfrage(db, person, fassung_kennung="radarr-standard")
    nex_fassungen.vergessen()

    assert _befund() == []


async def test_die_anfragenliste_filtert_genau_diese(
    admin_client: TestClient, nex: Any, db: Session
) -> None:
    """Der Sprung aus dem Befund landet auf genau den Anfragen, die er zählt."""
    person = _nutzer(db)
    fremd = _anfrage(db, person, tmdb_id=1, fassung_kennung="radarr-standard")
    _anfrage(db, person, tmdb_id=2)
    _anfrage(
        db, person, tmdb_id=3, fassung_kennung="radarr-uhd",
        status=RequestStatus.pending_approval,
    )

    antwort = admin_client.get("/api/admin/requests?fremde_fassung=true")

    assert antwort.status_code == 200, antwort.text
    assert [zeile["id"] for zeile in antwort.json()] == [fremd.id]


def test_die_anfragenliste_filtert_im_arr_betrieb_nichts(arr_client: TestClient) -> None:
    """Im ARR-Betrieb schweigt der Befund, und der Filter liefert dann nichts.

    Ein Filter, der hier alles zeigte, würde jede laufende Anfrage als
    "Fassung gibt es nicht" ausgeben.
    """
    with SessionLocal() as sitzung:
        person = _nutzer(sitzung)
        _anfrage(sitzung, person, tmdb_id=1, fassung_kennung="radarr-uhd")
        _anfrage(sitzung, person, tmdb_id=2, fassung_kennung="radarr-standard")

    alle = arr_client.get("/api/admin/requests")
    gefiltert = arr_client.get("/api/admin/requests?fremde_fassung=true")

    assert len(alle.json()) == 2
    assert gefiltert.status_code == 200, gefiltert.text
    assert gefiltert.json() == []


async def test_die_adresse_des_assistenten_nennt_was_liegt(
    admin_client: TestClient, nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Schritt 7 des Assistenten sah bisher nur, was ankam, nicht was liegt."""
    nexcrate.film(603)
    person = _nutzer(db)
    _anfrage(db, person)
    _anfrage(db, person, tmdb_id=604, fassung_kennung="radarr-standard")
    _anfrage(db, person, tmdb_id=605, fassung_kennung="radarr-uhd")
    _umgeschaltet(db)

    antwort = admin_client.post("/api/umstieg/nachreichen")

    assert antwort.status_code == 200, antwort.text
    assert antwort.json() == {"gereicht": 1, "liegen": 2, "weiter": True}


# --- Dauerhaft scheiternde Übergaben ---------------------------------------------


def _scheitert_immer(
    monkeypatch: pytest.MonkeyPatch, nexcrate: FakeNexcrate, *tmdb_ids: int
) -> None:
    """``POST /requests`` für diese Titel antwortet jedes Mal 500."""
    echt = nexcrate._route
    kaputt = tuple(f"tmdb:{nummer}" for nummer in tmdb_ids)

    def route(methode: str, rest: str, abfrage: dict[str, str], koerper: Any) -> httpx.Response:
        if methode == "POST" and rest == "/requests" and any(
            k in json.dumps(koerper) for k in kaputt
        ):
            return httpx.Response(500, json={"detail": {"code": "internal", "message": "kaputt"}})
        return echt(methode, rest, abfrage, koerper)

    monkeypatch.setattr(nexcrate, "_route", route)


async def test_dauerhaft_scheiternde_verdraengen_keine_juengere(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Wer scheitert, stellt sich hinten an.

    Eine 500 lässt die Anfrage freigegeben, und der nächste Durchgang nahm
    wieder die ältesten zuerst. Waren das ``JE_DURCHGANG`` dauerhaft
    scheiternde, kam eine jüngere gültige nie an die Reihe. Gemessen wird mit
    dem Rundgang selbst: ``check_once`` stempelt ``last_checked_at`` jeder
    freigegebenen Anfrage neu, danach allein zu sortieren genügt nicht.
    """
    monkeypatch.setattr(nachreichen, "JE_DURCHGANG", 2)
    _scheitert_immer(monkeypatch, nexcrate, 601, 602)
    # nexcrate kennt die Titel, führt sie aber noch in keiner Fassung: Erst die
    # Übergabe legt sie an. Alle drei innerhalb der Schonfrist, sonst hielte
    # ``check_once`` die nie übergebenen für verschwunden und bräche sie ab.
    for nummer in (601, 602, 603):
        nexcrate.film(nummer, versionen=[])
    person = _nutzer(db)
    jetzt = utcnow()
    alt_1 = _anfrage(
        db, person, tmdb_id=601, title="Erfundener Film A", requested_at=jetzt - timedelta(minutes=3)
    )
    alt_2 = _anfrage(
        db, person, tmdb_id=602, title="Erfundener Film B", requested_at=jetzt - timedelta(minutes=2)
    )
    gueltig = _anfrage(
        db, person, tmdb_id=603, title="Erfundener Film C", requested_at=jetzt - timedelta(minutes=1)
    )
    _umgeschaltet(db)

    for _runde in range(3):
        einstellungen = load_settings(db, frisch=True)
        await nachreichen.einmal(db, einstellungen)
        await status_poller.check_once(db, einstellungen)

    for anfrage in (alt_1, alt_2, gueltig):
        db.refresh(anfrage)
    assert gueltig.status == RequestStatus.searching
    assert (alt_1.status, alt_2.status) == (RequestStatus.approved, RequestStatus.approved)


async def test_nach_einem_tag_wird_ein_gescheiterter_nicht_mehr_versucht(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Die Obergrenze: Sonst lief das Nachreichen alle zwei Minuten, für immer.

    Nach ``WIEDERHOLEN_BIS`` wird nur noch übergeben, was nie gescheitert ist;
    ist davon nichts mehr übrig, ist Schluss. Was dann noch freigegeben liegt,
    zeigt der Befund.
    """
    _scheitert_immer(monkeypatch, nexcrate, 601)
    nexcrate.film(601)
    nexcrate.film(603)
    person = _nutzer(db)
    gescheitert = _anfrage(
        db, person, tmdb_id=601, title="Erfundener Dauerfehler",
        error_message="nexcrate hat abgelehnt.",
    )
    nie_versucht = _anfrage(db, person, tmdb_id=603, title="Erfundener Nachzügler")
    _umgeschaltet(db, vor=nachreichen.WIEDERHOLEN_BIS + timedelta(minutes=1))

    await nachreichen.einmal(db, load_settings(db, frisch=True))
    await nachreichen.einmal(db, load_settings(db, frisch=True))

    gesendet = [k["origin"] for k in _gesendet(nexcrate)]
    assert gesendet == [f"nexview:request:{nie_versucht.id}"]
    assert nachreichen.faellig(load_settings(db, frisch=True)) is False
    db.refresh(gescheitert)
    assert gescheitert.status == RequestStatus.approved

    treffer = _befund()
    assert len(treffer) == 1
    assert treffer[0].werte == {"anzahl": 1, "titel": "Erfundener Dauerfehler"}


async def test_vor_der_obergrenze_wird_ein_gescheiterter_wieder_versucht(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe: Innerhalb der Frist ist eine 500 nur ein Grund, es noch einmal zu versuchen."""
    _scheitert_immer(monkeypatch, nexcrate, 601)
    nexcrate.film(601)
    person = _nutzer(db)
    gescheitert = _anfrage(
        db, person, tmdb_id=601, title="Erfundener Dauerfehler",
        error_message="nexcrate hat abgelehnt.",
    )
    _umgeschaltet(db, vor=nachreichen.WIEDERHOLEN_BIS - timedelta(hours=1))

    await nachreichen.einmal(db, load_settings(db, frisch=True))

    assert [k["origin"] for k in _gesendet(nexcrate)] == [f"nexview:request:{gescheitert.id}"]
    assert nachreichen.faellig(load_settings(db, frisch=True)) is True


async def test_eine_gescheiterte_uebergabe_protokolliert_die_englische_kennung(
    nex: Any, nexcrate: FakeNexcrate, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Ins Protokoll gehört die Kennung, nicht der deutsche Satz.

    ``RequestError.code`` ist hier leer; der Satz stand deshalb im Protokoll.
    Die Kennung steckt im ``BeschaffungError`` dahinter.
    """
    nexcrate.film(603)
    person = _nutzer(db)
    gueltig = _anfrage(db, person)
    frisch = _umgeschaltet(db)
    nexcrate.next_answer["POST /api/v1/requests"] = httpx.Response(
        500, json={"detail": {"code": "internal", "message": "kaputt"}}
    )

    with caplog.at_level(logging.INFO, logger="nexview.requests"):
        await nachreichen.einmal(db, frisch)

    db.refresh(gueltig)
    zeilen = [
        zeile.getMessage()
        for zeile in caplog.records
        if zeile.getMessage().startswith(f"Could not hand over request {gueltig.id} ")
    ]
    assert len(zeilen) == 1, [zeile.getMessage() for zeile in caplog.records]
    assert "nexcrate_refused" in zeilen[0]
    assert gueltig.error_message
    assert gueltig.error_message not in zeilen[0]


async def test_der_befund_nennt_die_aelteste_anfrage(nex: Any, db: Session) -> None:
    """Der Titel in der Kachel ist der des ältesten Falls, nicht der des jüngsten."""
    person = _nutzer(db)
    jetzt = utcnow()
    # Die jüngere zuerst angelegt: Die Reihenfolge der Zeilen ist nicht das Alter.
    _anfrage(
        db, person, tmdb_id=1, fassung_kennung="radarr-standard",
        title="Erfundener junger Film", requested_at=jetzt - timedelta(days=1),
    )
    _anfrage(
        db, person, tmdb_id=2, fassung_kennung="radarr-uhd",
        title="Erfundener alter Film", requested_at=jetzt - timedelta(days=9),
    )

    treffer = _befund()
    assert len(treffer) == 1
    assert treffer[0].werte == {"anzahl": 2, "titel": "Erfundener alter Film"}


async def test_eine_abgebrochene_anfrage_mit_fehler_zaehlt_nicht_im_befund(
    nex: Any, db: Session
) -> None:
    """Der Befund zählt nur, was noch freigegeben liegt.

    Eine abgebrochene Anfrage behält ihren Fehlertext; zählte sie mit, stünde
    der Befund für immer, und zurücknehmen ginge nicht mehr.
    """
    person = _nutzer(db)
    _anfrage(
        db, person, status=RequestStatus.cancelled, title="Erfundener Abbruch",
        error_message="nexcrate hat abgelehnt.",
    )
    _anfrage(
        db, person, tmdb_id=604, status=RequestStatus.failed, title="Erfundener Fehlschlag",
        error_message="nexcrate hat abgelehnt.",
    )

    assert _befund() == []


# --- Nie übergeben heisst nie verschwunden -----------------------------------------


class _Uhr:
    """Eine vorstellbare Uhr für Nachreichen, Rundgang und Übergabe."""

    def __init__(self) -> None:
        self.jetzt = utcnow()

    def __call__(self) -> Any:
        # Jeder Aufruf eine Millisekunde später, wie eine echte Uhr im Durchgang.
        self.jetzt += timedelta(milliseconds=1)
        return self.jetzt


@pytest.fixture
def uhr(monkeypatch: pytest.MonkeyPatch) -> _Uhr:
    gestellt = _Uhr()
    for modul in (nachreichen, abgleich_kern, status_poller, requests_service):
        monkeypatch.setattr(modul, "utcnow", gestellt)
    return gestellt


def _nexcrate_legt_an(
    monkeypatch: pytest.MonkeyPatch, nexcrate: FakeNexcrate, stoerung: dict[str, Any]
) -> None:
    """Wie nexcrate: Ein angenommener Titel steht danach in der Fassung.

    ``stoerung["alles"] = True`` lässt jede Adresse mit 503 antworten, auch
    das Nachschlagen des Rundgangs.
    """
    echt = nexcrate._route

    def route(methode: str, rest: str, abfrage: dict[str, str], koerper: Any) -> httpx.Response:
        if stoerung.get("alles"):
            return httpx.Response(503, json={"detail": {"code": "unavailable", "message": "down"}})
        antwort = echt(methode, rest, abfrage, koerper)
        if methode == "POST" and rest == "/requests" and antwort.status_code == 200:
            nummer = int(str(koerper["ref"]).split(":")[1])
            nexcrate.film(
                nummer, name=f"Erfundener Film {nummer}",
                versionen=[nexcrate.fassung(FILM_HD, "wanted")],
            )
        return antwort

    monkeypatch.setattr(nexcrate, "_route", route)


async def _runde(db: Session) -> None:
    """Ein Rundgang, wie ``status_poller`` ihn macht: erst nachreichen, dann abgleichen."""
    einstellungen = load_settings(db, frisch=True)
    if nachreichen.faellig(einstellungen):
        await nachreichen.einmal(db, einstellungen)
    try:
        await status_poller.check_once(db, load_settings(db, frisch=True))
    except BeschaffungError:
        # Wie ``run_forever``: Antwortet nexcrate nicht, endet der Durchgang.
        db.rollback()
    db.commit()


def _staende(db: Session, ids: list[int]) -> dict[str, int]:
    db.expire_all()
    zahl: dict[str, int] = {}
    for anfrage_id in ids:
        stand = db.get(MediaRequest, anfrage_id).status.value
        zahl[stand] = zahl.get(stand, 0) + 1
    return zahl


async def test_freigaben_aus_der_arr_zeit_bricht_der_rundgang_nicht_ab(
    nex: Any, nexcrate: FakeNexcrate, db: Session, uhr: _Uhr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ 60 Freigaben von vor zwei Tagen: Alle kommen an, keine wird abgebrochen.

    Der Rundgang hielt eine freigegebene Anfrage für übergeben und rechnete
    die Schonfrist ab der Freigabe. Nach dem Umschalten fand er in nexcrate
    nichts von den 35, die das Nachreichen (25 je Durchgang) noch nicht
    erreicht hatte, und brach sie ab: „no longer present in Radarr".
    """
    _nexcrate_legt_an(monkeypatch, nexcrate, {})
    person = _nutzer(db)
    freigabe = uhr.jetzt - timedelta(days=2)
    ids = []
    for i in range(60):
        nummer = 1000 + i
        nexcrate.film(nummer, name=f"Erfundener Film {nummer}", versionen=[])
        ids.append(
            _anfrage(
                db, person, tmdb_id=nummer, title=f"Erfundener Film {nummer}",
                requested_at=freigabe - timedelta(minutes=60 - i), approved_at=freigabe,
            ).id
        )
    save_settings(db, {"beschaffung_gewechselt_am": uhr.jetzt.isoformat()})

    for _runde_nr in range(4):
        await _runde(db)
        uhr.jetzt += timedelta(seconds=120)

    assert _staende(db, ids) == {"searching": 60}


async def test_nach_acht_stunden_ausfall_kommen_alle_freigaben_an(
    nex: Any, nexcrate: FakeNexcrate, db: Session, uhr: _Uhr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Ein Ausfall über Nacht, gleich nach dem Umschalten: danach 60 übergeben.

    Während des Ausfalls antwortet nexcrate nirgends, der Rundgang bricht
    nichts ab. Danach reichte das Nachreichen 25 nach, und der Rundgang brach
    die übrigen 35 ab, weil ihre Freigabe länger als die Schonfrist zurücklag.
    """
    stoerung: dict[str, Any] = {}
    _nexcrate_legt_an(monkeypatch, nexcrate, stoerung)
    person = _nutzer(db)
    ids = []
    for i in range(60):
        nummer = 4000 + i
        nexcrate.film(nummer, name=f"Erfundener Film {nummer}", versionen=[])
        ids.append(
            _anfrage(
                db, person, tmdb_id=nummer, title=f"Erfundener Film {nummer}",
                requested_at=uhr.jetzt - timedelta(minutes=60 - i), approved_at=uhr.jetzt,
            ).id
        )
    save_settings(db, {"beschaffung_gewechselt_am": uhr.jetzt.isoformat()})
    stoerung["alles"] = True
    ende = uhr.jetzt + timedelta(hours=8)
    while uhr.jetzt < ende:
        await _runde(db)
        uhr.jetzt += timedelta(minutes=30)
    assert _staende(db, ids) == {"approved": 60}

    stoerung.clear()
    for _runde_nr in range(4):
        await _runde(db)
        uhr.jetzt += timedelta(seconds=120)

    assert _staende(db, ids) == {"searching": 60}


async def test_eine_alte_freigabe_mit_fehlertext_kommt_hinter_scheiternden_dran(
    nex: Any, nexcrate: FakeNexcrate, db: Session, uhr: _Uhr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Der Rundgang stempelte die nie übergebene Freigabe jede Runde neu.

    Eine Freigabe aus der Arr-Zeit trägt noch ihren alten Fehlertext. Beim
    Nachreichen steht sie damit hinter jeder, die noch nie scheiterte, und
    unter den gescheiterten entscheidet ``last_checked_at``. Das setzte
    ``check_once`` an jeder freigegebenen Anfrage, in der Reihenfolge der
    Zeilen, also immer an ihr zuletzt. Scheiterten ``JE_DURCHGANG`` andere
    dauerhaft (500), kam sie nie dran, obwohl sie im NEX-Betrieb nie
    versucht wurde (gemessen, 156 Runden).
    """
    monkeypatch.setattr(nachreichen, "JE_DURCHGANG", 2)
    _scheitert_immer(monkeypatch, nexcrate, 601, 602, 603)
    for nummer in (601, 602, 603, 609):
        nexcrate.film(nummer, name=f"Erfundener Film {nummer}", versionen=[])
    person = _nutzer(db)
    for nummer in (601, 602, 603):
        _anfrage(
            db, person, tmdb_id=nummer, title=f"Erfundener Film {nummer}",
            requested_at=uhr.jetzt - timedelta(minutes=700 - nummer), approved_at=uhr.jetzt,
        )
    altfall = _anfrage(
        db, person, tmdb_id=609, title="Erfundener Altfall",
        requested_at=uhr.jetzt - timedelta(days=1), approved_at=uhr.jetzt,
        error_message="Radarr ist nicht erreichbar.",
    )
    save_settings(db, {"beschaffung_gewechselt_am": uhr.jetzt.isoformat()})

    for _runde_nr in range(6):
        await _runde(db)
        uhr.jetzt += timedelta(seconds=120)

    gesendet = [k["origin"] for k in _gesendet(nexcrate)]
    assert f"nexview:request:{altfall.id}" in gesendet, gesendet
    db.refresh(altfall)
    assert altfall.status == RequestStatus.searching


async def test_abbrechen_erreicht_nexcrate_auch_nach_verlorener_antwort(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Die Übergabe kam an, ihre Antwort nicht: Abbrechen muss trotzdem hin.

    Die Anfrage blieb ohne ``arr_id`` freigegeben; der Rundgang fand den
    Titel bei nexcrate und setzte „wird gesucht", die Kennung aber nicht.
    ``cancel`` schickte ohne Kennung nichts, nexcrate suchte weiter, und
    Nexview zeigte „abgebrochen".
    """
    echt = nexcrate._route

    def route(methode: str, rest: str, abfrage: dict[str, str], koerper: Any) -> httpx.Response:
        antwort = echt(methode, rest, abfrage, koerper)
        if methode == "POST" and rest == "/requests" and antwort.status_code == 200:
            # nexcrate legt die Fassung mit Nexviews Herkunft an, die Antwort geht verloren.
            nexcrate.film(
                603, name="Erfundener Film",
                versionen=[nexcrate.fassung(FILM_HD, "wanted", origin=koerper["origin"])],
            )
            raise httpx.ReadTimeout("Antwort verloren")
        return antwort

    monkeypatch.setattr(nexcrate, "_route", route)
    nexcrate.film(603, name="Erfundener Film", versionen=[])
    anfrage = _anfrage(db, _nutzer(db), title="Erfundener Film")

    with pytest.raises(requests_service.RequestError):
        await requests_service.push_to_arr(db, nex, anfrage)
    db.refresh(anfrage)
    assert (anfrage.status, anfrage.arr_id) == (RequestStatus.approved, None)

    await status_poller.check_once(db, load_settings(db, frisch=True))
    db.commit()
    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.searching
    # Dieselbe Kennung, die ``push_to_arr`` im NEX-Betrieb setzt.
    assert anfrage.arr_id == 603

    vorher = len(nexcrate.calls)
    await requests_service.cancel(db, nex, anfrage)

    zurueck = [
        pfad for methode, pfad, _, _ in nexcrate.calls[vorher:]
        if methode == "POST" and pfad.endswith("/withdraw")
    ]
    assert zurueck == ["/api/v1/titles/movie/tmdb:603/withdraw"], nexcrate.calls[vorher:]
    assert anfrage.status == RequestStatus.cancelled
