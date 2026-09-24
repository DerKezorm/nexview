"""Seit wann ein Posten liegt, im NEX-Betrieb aus nexcrates ``imported_at``.

Der Aufräum-Vorschlag braucht zwei Uhren: lange nicht angesehen **und** lange
da. Die zweite kam im NEX-Betrieb nie an - nexcrate nannte kein Datum, und
jeder Posten stand unter „Alter unbekannt". Seit nexcrate ``39dfc05`` trägt
jede Fassung und jede Staffel je Fassung ``imported_at``: beim Film die
jetzige Datei, bei der Staffel ihre älteste, ``null`` wo nexcrate es selbst
nicht weiß. Nexview übernimmt es wie das Datum aus Radarr und Sonarr, und rät
nie: ``null`` und ein fehlendes Feld bleiben unbekannt.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus, Role, StorageEntry, User
from app.security import hash_password
from app.services import aufraeumen, storage
from app.services.beschaffung import NEX
from app.services.beschaffung.nex import bestand as nex_bestand
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, KEY, SERIE_HD, URL, FakeNexcrate

GB = 1024**3

ALT = "2023-05-04T06:07:08+00:00"
ALT_NAIV = datetime(2023, 5, 4, 6, 7, 8)
NEUER = "2025-01-02T03:04:05+00:00"
NEUER_NAIV = datetime(2025, 1, 2, 3, 4, 5)


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
    save_settings(db, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY})
    nex_fassungen.schreiben(db, nexcrate.versions)
    db.commit()
    return load_settings(db, frisch=True)


def _vor_tagen(tage: int) -> str:
    return (datetime.now(UTC) - timedelta(days=tage)).isoformat(timespec="seconds")


def _film(nexcrate: FakeNexcrate, tmdb_id: int, **datum: Any) -> dict[str, Any]:
    """Ein Film mit Datei; ``datum`` ist ``imported_at=...`` oder leer (Feld fehlt)."""
    return nexcrate.film(
        tmdb_id,
        name=f"Example Movie {tmdb_id}",
        versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=8 * GB, **datum)],
    )


def _staffel(nummer: int, groesse: int, **datum: Any) -> dict[str, Any]:
    """Eine Staffel der Einzelansicht; ``datum`` wie bei ``_film``."""
    je_fassung: dict[str, Any] = {
        "version_id": SERIE_HD,
        "state": "available",
        "monitored": True,
        "counts": {"have": 3, "aired": 3, "expected": 3},
        "size_bytes": groesse,
    }
    je_fassung.update(datum)
    return {"season": nummer, "name": f"Season {nummer}", "versions": [je_fassung]}


def _serie(
    nexcrate: FakeNexcrate, tmdb_id: int, staffeln: list[dict[str, Any]], **datum: Any
) -> dict[str, Any]:
    return nexcrate.serie(
        tmdb_id,
        versionen=[
            nexcrate.fassung(
                SERIE_HD,
                "available",
                size_bytes=sum(s["versions"][0]["size_bytes"] for s in staffeln),
                series={"counts": {"have": 3 * len(staffeln), "aired": 3 * len(staffeln)}},
                **datum,
            )
        ],
        staffeln=staffeln,
    )


def _zeilen(db: Session) -> dict[str, StorageEntry]:
    db.expire_all()
    return {z.key: z for z in db.query(StorageEntry).all()}


# --- Aus nexcrates Antwort in Nexviews Form --------------------------------------


def test_ein_film_traegt_das_datum_seiner_datei(nexcrate: FakeNexcrate) -> None:
    titel = _film(nexcrate, 603, imported_at=ALT)

    stand = nex_bestand.film_stand(titel, FILM_HD)

    assert stand is not None and stand.added_at == ALT_NAIV


def test_ein_film_unbekannten_alters_bleibt_ohne_datum(nexcrate: FakeNexcrate) -> None:
    titel = _film(nexcrate, 603, imported_at=None)

    stand = nex_bestand.film_stand(titel, FILM_HD)

    assert stand is not None and stand.added_at is None


def test_eine_aeltere_nexcrate_ohne_das_feld_geht_weiter(nexcrate: FakeNexcrate) -> None:
    titel = _film(nexcrate, 603)
    assert "imported_at" not in titel["versions"][0]

    stand = nex_bestand.film_stand(titel, FILM_HD)

    assert stand is not None and stand.added_at is None
    assert stand.has_file and stand.size_bytes == 8 * GB


@pytest.mark.parametrize(
    "roh",
    [
        "2023-05-04T06:07:08Z",
        "2023-05-04T06:07:08+00:00",
        "2023-05-04T08:07:08+02:00",
        "2023-05-03T22:07:08-08:00",
        # Ohne Zone gilt UTC, nicht die Ortszeit dieses Rechners.
        "2023-05-04T06:07:08",
    ],
)
def test_das_datum_wird_naiv_in_utc_abgelegt(nexcrate: FakeNexcrate, roh: str) -> None:
    stand = nex_bestand.film_stand(_film(nexcrate, 603, imported_at=roh), FILM_HD)

    assert stand is not None
    assert stand.added_at == ALT_NAIV
    assert stand.added_at.tzinfo is None


@pytest.mark.parametrize("roh", ["", "yesterday", 1683180428, {"at": ALT}])
def test_ein_unlesbares_datum_ist_kein_datum(nexcrate: FakeNexcrate, roh: Any) -> None:
    stand = nex_bestand.film_stand(_film(nexcrate, 603, imported_at=roh), FILM_HD)

    assert stand is not None and stand.added_at is None


def test_jede_staffel_traegt_ihr_eigenes_datum(nexcrate: FakeNexcrate) -> None:
    """Nie das der Serie: Die nennt ihre älteste Datei, und die liegt in Staffel 1."""
    titel = _serie(
        nexcrate,
        1399,
        [_staffel(1, 3 * GB, imported_at=ALT), _staffel(2, 5 * GB, imported_at=None), _staffel(3, 2 * GB)],
        imported_at=ALT,
    )

    stand = nex_bestand.serien_stand(titel, SERIE_HD)

    assert stand is not None
    assert stand.staffeln[1].added_at == ALT_NAIV
    assert stand.staffeln[2].added_at is None
    assert stand.staffeln[3].added_at is None


# --- Der Abgleich schreibt es an die Posten --------------------------------------


async def test_der_abgleich_schreibt_das_datum_an_filme_und_staffeln(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    _film(nexcrate, 603, imported_at=ALT)
    _film(nexcrate, 604, imported_at=None)
    _film(nexcrate, 605)
    _serie(
        nexcrate,
        1399,
        [_staffel(1, 3 * GB, imported_at=ALT), _staffel(2, 5 * GB, imported_at=None)],
        # Die Serie nennt ihre älteste Datei; eine Staffel ohne Datum
        # bekommt es trotzdem nicht.
        imported_at=ALT,
    )
    _serie(nexcrate, 1400, [_staffel(4, 2 * GB, imported_at=NEUER)], imported_at=ALT)

    await storage.abgleichen(db, nex)

    zeilen = _zeilen(db)
    assert zeilen[f"movie:{FILM_HD}:tmdb:603"].added_at == ALT_NAIV
    assert zeilen[f"movie:{FILM_HD}:tmdb:604"].added_at is None
    assert zeilen[f"movie:{FILM_HD}:tmdb:605"].added_at is None
    assert zeilen[f"tv:{SERIE_HD}:tmdb:1399:s1"].added_at == ALT_NAIV
    assert zeilen[f"tv:{SERIE_HD}:tmdb:1399:s2"].added_at is None
    assert zeilen[f"tv:{SERIE_HD}:tmdb:1400:s4"].added_at == NEUER_NAIV


async def test_ein_spaeter_bekanntes_datum_wird_nachgetragen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    _film(nexcrate, 603, imported_at=None)
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB, imported_at=None)])
    await storage.abgleichen(db, nex)
    assert _zeilen(db)[f"movie:{FILM_HD}:tmdb:603"].added_at is None

    _film(nexcrate, 603, imported_at=ALT)
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB, imported_at=ALT)])
    await storage.abgleichen(db, nex)

    zeilen = _zeilen(db)
    assert zeilen[f"movie:{FILM_HD}:tmdb:603"].added_at == ALT_NAIV
    assert zeilen[f"tv:{SERIE_HD}:tmdb:1399:s1"].added_at == ALT_NAIV


async def test_ein_bekanntes_datum_bleibt_wie_im_arr_betrieb(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Eine ersetzte Datei macht einen alten Titel nicht wieder neu.

    nexcrate nennt beim Film die jetzige Datei; nach einer Aufwertung ist das
    Datum jünger. Der ARR-Weg trägt nur nach und überschreibt nie
    (``storage._schreiben``), und ein ``null`` danach löscht auch nichts.
    """
    _film(nexcrate, 603, imported_at=ALT)
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB, imported_at=ALT)])
    await storage.abgleichen(db, nex)

    _film(nexcrate, 603, imported_at=NEUER)
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB, imported_at=None)])
    await storage.abgleichen(db, nex)

    zeilen = _zeilen(db)
    assert zeilen[f"movie:{FILM_HD}:tmdb:603"].added_at == ALT_NAIV
    assert zeilen[f"tv:{SERIE_HD}:tmdb:1399:s1"].added_at == ALT_NAIV


async def test_ein_paket_bekommt_das_datum_seiner_staffel(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Wie im ARR-Betrieb, wo ``_staffeldaten_nachtragen`` es dem Paket gibt."""
    person = User(username="sammler", password_hash=hash_password("test"), role=Role.user)
    db.add(person)
    db.commit()
    anfrage = MediaRequest(
        user_id=person.id,
        media_type=MediaType.tv,
        fassung_kennung=SERIE_HD,
        tmdb_id=1399,
        title="Example Show",
        season=3,
        episodes=[1],
        status=RequestStatus.approved,
        arr_id=1399,
    )
    db.add(anfrage)
    db.commit()
    _serie(nexcrate, 1399, [_staffel(3, 9 * GB, imported_at=ALT)])
    nexcrate.staffel(
        "tmdb:1399",
        3,
        [
            nexcrate.folge(
                1,
                versionen=[
                    {
                        "version_id": SERIE_HD,
                        "state": "available",
                        "monitored": True,
                        "size_bytes": 2 * GB,
                        "files": [{"file_id": "f1", "size_bytes": 2 * GB}],
                    }
                ],
            )
        ],
    )
    basis = storage.schluessel(MediaType.tv, SERIE_HD, tmdb_id=1399, season=3)
    assert basis is not None
    gemessen = {
        basis: storage._Gemessen(
            key=basis,
            media_type=MediaType.tv,
            tier="standard",
            tmdb_id=1399,
            tvdb_id=None,
            season=3,
            title="Example Show",
            size_bytes=9 * GB,
            added_at=ALT_NAIV,
            arr_id=1399,
        )
    }

    await storage._pakete_aufnehmen(db, nex, gemessen)

    paket = storage.schluessel(
        MediaType.tv, SERIE_HD, tmdb_id=1399, season=3, request_id=anfrage.id
    )
    assert gemessen[paket].size_bytes == 2 * GB
    assert gemessen[paket].added_at == ALT_NAIV


# --- Und der Aufräum-Vorschlag sieht es ------------------------------------------


async def test_der_aufraeum_vorschlag_nennt_einen_alten_titel(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Vorher stand alles unter „Alter unbekannt" und nichts in der Liste."""
    _film(nexcrate, 603, imported_at=_vor_tagen(400))
    _film(nexcrate, 604, imported_at=_vor_tagen(3))
    _film(nexcrate, 605, imported_at=None)
    _serie(
        nexcrate,
        1399,
        [
            _staffel(1, 3 * GB, imported_at=_vor_tagen(500)),
            _staffel(2, 5 * GB, imported_at=_vor_tagen(2)),
        ],
        imported_at=_vor_tagen(500),
    )
    await storage.abgleichen(db, nex)

    liste = aufraeumen.liste(db, monate=6)

    vorgeschlagen = {(k.media_type, k.tmdb_id, k.season) for k in liste.kandidaten}
    assert vorgeschlagen == {(MediaType.movie, 603, None), (MediaType.tv, 1399, 1)}
    assert liste.ohne_datum == 1
