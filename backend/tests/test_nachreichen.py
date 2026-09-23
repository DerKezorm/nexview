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

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus, Role, User
from app.security import hash_password
from app.services import befunde, nachreichen
from app.services.beschaffung import NEX
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


def _umgeschaltet(db: Session) -> Any:
    save_settings(db, {"beschaffung_gewechselt_am": "2026-09-22T20:00:00"})
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
