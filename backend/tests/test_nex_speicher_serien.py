"""Der Speicher-Abgleich misst im NEX-Betrieb Staffeln, nicht nur Filme.

Bis nexcrate 39dfc05 trug die Liste über die Änderungsmarke ``series.seasons``
immer als ``null`` (gemessen); die Staffelgrößen standen nur in der
Einzelansicht. Ohne sie legte der Abgleich keine Staffelzeile an und räumte
nach dem Umstieg jede gewanderte Staffelzeile ab, samt Besitzer - nexcrate
hatte ja geantwortet, der Lauf galt als vollständig.

Gegen ``tests/beschaffung/fake_nexcrate.py``, hier als **ältere** nexcrate
(``liste_staffeln = STAFFELN_NULL``): Diese Tests prüfen den Weg über die
Einzelansicht samt Merker, und mit Staffeln in der Liste würde er nie
begangen. Die Staffeln aus Liste und ``lookup`` prüft
``test_nex_staffeln_aus_liste.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    RequestStatus,
    Role,
    StorageEntry,
    StorageState,
    User,
)
from app.security import hash_password
from app.services import storage
from app.services.beschaffung import NEX
from app.services.beschaffung.nex import bestand as nex_bestand
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import (
    KEY,
    SERIE_HD,
    SERIE_UHD,
    STAFFELN_NULL,
    URL,
    FakeNexcrate,
    _fehler,
)

GB = 1024**3


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    attrappe = FakeNexcrate()
    # Eine ältere nexcrate: Staffeln nur in der Einzelansicht (siehe oben).
    attrappe.liste_staffeln = STAFFELN_NULL
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


def _nutzer(db: Session, name: str = "sammler") -> User:
    person = User(username=name, password_hash=hash_password("test"), role=Role.user)
    db.add(person)
    db.commit()
    return person


def _staffel(nummer: int, groesse: int, kennung: str = SERIE_HD) -> dict[str, Any]:
    """Eine Staffel, wie die Einzelansicht sie nennt."""
    return {
        "season": nummer,
        "name": f"Season {nummer}",
        "versions": [
            {
                "version_id": kennung,
                "state": "available",
                "monitored": True,
                "counts": {"have": 3, "aired": 3, "expected": 3},
                "size_bytes": groesse,
            }
        ],
    }


def _serie(
    nexcrate: FakeNexcrate, tmdb_id: int, staffeln: list[dict[str, Any]], **weiter: Any
) -> dict[str, Any]:
    """Eine Serie mit Datei; die Liste nennt nur die Gesamtgröße."""
    return nexcrate.serie(
        tmdb_id,
        versionen=[
            nexcrate.fassung(
                SERIE_HD,
                "available",
                size_bytes=sum(s["versions"][0]["size_bytes"] for s in staffeln),
                series={"counts": {"have": 3 * len(staffeln), "aired": 3 * len(staffeln)}},
            )
        ],
        staffeln=staffeln,
        **weiter,
    )


def _zeile(
    db: Session, tmdb_id: int, staffel: int, groesse: int, besitzer: User | None
) -> StorageEntry:
    """Eine Staffelzeile, wie der Umstieg sie hinterlässt."""
    zeile = StorageEntry(
        key=storage.schluessel(MediaType.tv, SERIE_HD, tmdb_id=tmdb_id, season=staffel),
        user_id=besitzer.id if besitzer else None,
        media_type=MediaType.tv,
        fassung_kennung=SERIE_HD,
        tmdb_id=tmdb_id,
        tvdb_id=None,
        season=staffel,
        title="Example Show",
        size_bytes=groesse,
        path="",
        state=StorageState.owned if besitzer else StorageState.house,
    )
    db.add(zeile)
    db.commit()
    return zeile


def _einzelansichten(nexcrate: FakeNexcrate) -> list[str]:
    return [
        pfad
        for methode, pfad, _, _ in nexcrate.calls
        if methode == "GET" and pfad.startswith("/api/v1/titles/series/") and "/seasons/" not in pfad
    ]


# --- Staffeln kommen aus der Einzelansicht ---------------------------------------


async def test_jede_staffel_bekommt_ihre_zeile(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB), _staffel(2, 5 * GB)])

    ergebnis = await storage.abgleichen(db, nex)

    zeilen = {z.key: z for z in db.query(StorageEntry).all()}
    erste = zeilen.get(f"tv:{SERIE_HD}:tmdb:1399:s1")
    zweite = zeilen.get(f"tv:{SERIE_HD}:tmdb:1399:s2")
    assert erste is not None and zweite is not None, sorted(zeilen)
    assert (erste.size_bytes, zweite.size_bytes) == (3 * GB, 5 * GB)
    assert erste.fassung_kennung == SERIE_HD and erste.tmdb_id == 1399
    assert erste.season == 1 and zweite.season == 2
    assert ergebnis.neu == 2


async def test_eine_gewanderte_staffelzeile_bleibt_und_wird_weitergemessen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der Befund: vorher löschte der erste Abgleich nach dem Umstieg diese Zeile."""
    person = _nutzer(db)
    _zeile(db, 1399, 2, 5 * GB, person)
    _serie(nexcrate, 1399, [_staffel(2, 6 * GB)])

    ergebnis = await storage.abgleichen(db, nex)

    zeile = db.query(StorageEntry).filter_by(key=f"tv:{SERIE_HD}:tmdb:1399:s2").one_or_none()
    assert zeile is not None, "die gewanderte Staffelzeile wurde gelöscht"
    assert zeile.size_bytes == 6 * GB
    assert zeile.user_id == person.id
    assert ergebnis.entfernt == 0


async def test_eine_fassung_ohne_datei_bekommt_keine_zeile(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die 4K-Fassung derselben Serie liegt nicht vor - also keine 4K-Zeile."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])

    await storage.abgleichen(db, nex)

    schluessel = {z.key for z in db.query(StorageEntry).all()}
    assert schluessel == {f"tv:{SERIE_HD}:tmdb:1399:s1"}
    assert not any(SERIE_UHD in k for k in schluessel)


async def test_eine_serie_die_nicht_antwortet_verliert_nichts(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scheitert die Einzelansicht einer Serie, bleiben ihre Zeilen stehen.

    Die andere Serie wird trotzdem gemessen und aufgeräumt: Der Lauf bricht
    nicht ab und gilt auch nicht im Ganzen als stumm.
    """
    person = _nutzer(db)
    _zeile(db, 1399, 1, 4 * GB, person)
    _zeile(db, 1399, 2, 7 * GB, None)
    _zeile(db, 1400, 9, 2 * GB, None)  # gibt es bei nexcrate nicht mehr
    _serie(nexcrate, 1399, [_staffel(1, 9 * GB)])
    _serie(nexcrate, 1400, [_staffel(1, 1 * GB)], name="Another Show", tvdb=121362)

    echt = nexcrate._titel_weg

    def stumm_fuer_eine(methode: str, teile: list[str], koerper: Any) -> Any:
        if teile[1:3] == ["series", "tmdb:1399"] and len(teile) == 3:
            return _fehler(500, "internal_error", "Something broke.")
        return echt(methode, teile, koerper)

    monkeypatch.setattr(nexcrate, "_titel_weg", stumm_fuer_eine)

    ergebnis = await storage.abgleichen(db, nex)

    zeilen = {z.key: z for z in db.query(StorageEntry).all()}
    erste = zeilen.get(f"tv:{SERIE_HD}:tmdb:1399:s1")
    zweite = zeilen.get(f"tv:{SERIE_HD}:tmdb:1399:s2")
    assert erste is not None and zweite is not None, sorted(zeilen)
    assert (erste.size_bytes, erste.user_id) == (4 * GB, person.id)
    assert (zweite.size_bytes, zweite.user_id) == (7 * GB, None)
    # Die andere Serie wurde gelesen: neu gemessen und ihr Altes geräumt.
    assert zeilen[f"tv:{SERIE_HD}:tmdb:1400:s1"].size_bytes == 1 * GB
    assert f"tv:{SERIE_HD}:tmdb:1400:s9" not in zeilen
    assert ergebnis.entfernt == 1


async def test_eine_alte_einzelansicht_gilt_nicht_fuer_einen_neuen_stand(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hat sich die Serie geändert und antwortet nicht, ist sie ungemessen.

    Die gemerkte Einzelansicht beschreibt den Stand vor der Änderung; mit ihr
    zu messen hieße, eine alte Zahl als frisch zu buchen.
    """
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    await storage.abgleichen(db, nex)
    zeile = db.query(StorageEntry).filter_by(key=f"tv:{SERIE_HD}:tmdb:1399:s1").one()
    gemessen_am = zeile.measured_at

    _serie(nexcrate, 1399, [_staffel(1, 5 * GB)])
    echt = nexcrate._titel_weg

    def stumm(methode: str, teile: list[str], koerper: Any) -> Any:
        if teile[1:3] == ["series", "tmdb:1399"] and len(teile) == 3:
            return _fehler(500, "internal_error", "Something broke.")
        return echt(methode, teile, koerper)

    monkeypatch.setattr(nexcrate, "_titel_weg", stumm)
    await storage.abgleichen(db, nex)

    db.expire_all()
    zeile = db.query(StorageEntry).filter_by(key=f"tv:{SERIE_HD}:tmdb:1399:s1").one()
    assert zeile.size_bytes == 3 * GB
    assert zeile.measured_at == gemessen_am, "der alte Stand wurde als frisch gebucht"


async def test_eine_unveraenderte_serie_wird_nicht_noch_einmal_gelesen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Marke je Titel sagt, ob sich etwas getan hat - erst dann neu lesen."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    await storage.abgleichen(db, nex)
    assert len(_einzelansichten(nexcrate)) == 1, "beide Fassungen lasen je einmal"

    await storage.abgleichen(db, nex)
    assert len(_einzelansichten(nexcrate)) == 1

    # Dieselbe Serie ändert sich: neue Marke, neue Größe.
    _serie(nexcrate, 1399, [_staffel(1, 4 * GB)])
    await storage.abgleichen(db, nex)

    assert len(_einzelansichten(nexcrate)) == 2
    zeile = db.query(StorageEntry).filter_by(key=f"tv:{SERIE_HD}:tmdb:1399:s1").one()
    assert zeile.size_bytes == 4 * GB


async def test_nach_dem_verwerfen_wird_wieder_ganz_gelesen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Eine andere Installation (oder ein Neustart) liest jede Serie neu."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    await storage.abgleichen(db, nex)

    nex_bestand.verwerfen()
    await storage.abgleichen(db, nex)

    assert len(_einzelansichten(nexcrate)) == 2


# --- Folgen-Pakete: gezählt über nexcrates Dateien je Folge ----------------------


def _paket(db: Session, person: User, *, tvdb_id: int | None = None) -> MediaRequest:
    anfrage = MediaRequest(
        user_id=person.id,
        media_type=MediaType.tv,
        fassung_kennung=SERIE_HD,
        tmdb_id=1399,
        tvdb_id=tvdb_id,
        title="Example Show",
        season=3,
        episodes=[1, 2],
        status=RequestStatus.approved,
        arr_id=1399,
    )
    db.add(anfrage)
    db.commit()
    return anfrage


def _folge(
    nexcrate: FakeNexcrate,
    nummer: int,
    dateien: list[tuple[str, int]] | None,
    *,
    groesse: int | None = None,
    state: str = "available",
) -> dict[str, Any]:
    """Eine Folge der Staffelansicht; ``dateien=None`` ist eine nexcrate ohne ``files``."""
    fassung: dict[str, Any] = {
        "version_id": SERIE_HD,
        "state": state,
        "monitored": True,
        "size_bytes": groesse,
    }
    if dateien is not None:
        fassung["files"] = [
            {"file_id": kennung, "size_bytes": bytes_} for kennung, bytes_ in dateien
        ]
    return nexcrate.folge(nummer, versionen=[fassung])


def _staffelzeile(groesse: int) -> tuple[str, dict[str, storage._Gemessen]]:
    basis = storage.schluessel(MediaType.tv, SERIE_HD, tmdb_id=1399, season=3)
    assert basis is not None
    return basis, {
        basis: storage._Gemessen(
            key=basis,
            media_type=MediaType.tv,
            tier="standard",
            tmdb_id=1399,
            tvdb_id=None,
            season=3,
            title="Example Show",
            size_bytes=groesse,
            arr_id=1399,
        )
    }


def _paketschluessel(anfrage: MediaRequest) -> str:
    kennung = storage.schluessel(
        MediaType.tv, SERIE_HD, tmdb_id=1399, season=3, request_id=anfrage.id
    )
    assert kennung is not None
    return kennung


async def test_eine_datei_an_zwei_folgen_zaehlt_einmal(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Folge 1 und 2 liegen in einer Datei; nexcrate nennt sie an beiden mit voller Größe."""
    _serie(nexcrate, 1399, [_staffel(3, 5 * GB)])
    nexcrate.staffel(
        "tmdb:1399",
        3,
        [
            _folge(nexcrate, 1, [("1", 3 * GB)], groesse=3 * GB),
            _folge(nexcrate, 2, [("1", 3 * GB)], groesse=3 * GB),
            _folge(nexcrate, 3, [("2", 2 * GB)], groesse=2 * GB),
        ],
    )
    anfrage = _paket(db, _nutzer(db))
    basis, gemessen = _staffelzeile(5 * GB)

    behalten = await storage._pakete_aufnehmen(db, nex, gemessen)

    paket = _paketschluessel(anfrage)
    assert gemessen[paket].size_bytes == 3 * GB
    assert gemessen[paket].unvollstaendig is False
    assert gemessen[basis].size_bytes == 2 * GB
    assert paket not in behalten


async def test_eine_doppelfolge_zaehlt_teil_eins_und_zwei(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """``size_bytes`` der Folge nennt nur Teil 1; ``files`` nennt beide Dateien."""
    _serie(nexcrate, 1399, [_staffel(3, 5000)])
    nexcrate.staffel(
        "tmdb:1399",
        3,
        [
            _folge(nexcrate, 1, [("2", 900), ("3", 800)], groesse=900),
            _folge(nexcrate, 2, [("4", 1000)], groesse=1000),
            _folge(nexcrate, 3, [("5", 2300)], groesse=2300),
        ],
    )
    anfrage = _paket(db, _nutzer(db))
    basis, gemessen = _staffelzeile(5000)

    await storage._pakete_aufnehmen(db, nex, gemessen)

    assert gemessen[_paketschluessel(anfrage)].size_bytes == 2700
    assert gemessen[basis].size_bytes == 2300


async def test_eine_fassung_ohne_groesse_zaehlt_ihre_dateien(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """``wanted`` mit ``size_bytes: null`` und trotzdem einer Datei auf der Platte (gemessen)."""
    _serie(nexcrate, 1399, [_staffel(3, 5000)])
    nexcrate.staffel(
        "tmdb:1399",
        3,
        [
            _folge(nexcrate, 1, [("1", 1000)], groesse=1000),
            _folge(nexcrate, 2, [("9", 2500)], groesse=None, state="wanted"),
        ],
    )
    anfrage = _paket(db, _nutzer(db))
    basis, gemessen = _staffelzeile(5000)

    await storage._pakete_aufnehmen(db, nex, gemessen)

    paket = gemessen[_paketschluessel(anfrage)]
    assert paket.size_bytes == 3500
    # Folge 2 steht auf "wanted": das Paket wächst noch.
    assert paket.unvollstaendig is True
    assert gemessen[basis].size_bytes == 1500


async def test_ein_paket_in_der_zweitfassung_zaehlt_deren_dateien(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Dieselben Folgen liegen in HD und 4K; das 4K-Paket zählt die 4K-Dateien."""
    _serie(nexcrate, 1399, [_staffel(3, 9 * GB, SERIE_UHD)])

    def beide(nummer: int) -> dict[str, Any]:
        return nexcrate.folge(
            nummer,
            versionen=[
                {
                    "version_id": kennung,
                    "state": "available",
                    "monitored": True,
                    "size_bytes": groesse,
                    "files": [{"file_id": f"{kennung}-{nummer}", "size_bytes": groesse}],
                }
                for kennung, groesse in ((SERIE_HD, 1 * GB), (SERIE_UHD, 4 * GB))
            ],
        )

    nexcrate.staffel("tmdb:1399", 3, [beide(1), beide(2)])
    anfrage = _paket(db, _nutzer(db))
    anfrage.fassung_kennung = SERIE_UHD
    db.commit()
    basis = storage.schluessel(MediaType.tv, SERIE_UHD, tmdb_id=1399, season=3)
    assert basis is not None
    gemessen = {
        basis: storage._Gemessen(
            key=basis,
            media_type=MediaType.tv,
            tier="uhd",
            tmdb_id=1399,
            tvdb_id=None,
            season=3,
            title="Example Show",
            size_bytes=9 * GB,
            arr_id=1399,
        )
    }

    await storage._pakete_aufnehmen(db, nex, gemessen)

    paket = storage.schluessel(
        MediaType.tv, SERIE_UHD, tmdb_id=1399, season=3, request_id=anfrage.id
    )
    assert gemessen[paket].size_bytes == 8 * GB
    assert gemessen[basis].size_bytes == 1 * GB


async def test_ohne_files_zaehlen_die_folgengroessen_gedeckelt(
    nex: Any, nexcrate: FakeNexcrate, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Eine nexcrate vor 5427612 nennt keine Dateien: Folgen summieren, nie über die Staffel."""
    _serie(nexcrate, 1399, [_staffel(3, 4 * GB)])
    nexcrate.staffel(
        "tmdb:1399",
        3,
        [
            _folge(nexcrate, 1, None, groesse=3 * GB),
            _folge(nexcrate, 2, None, groesse=3 * GB),
        ],
    )
    anfrage = _paket(db, _nutzer(db))
    basis, gemessen = _staffelzeile(4 * GB)

    with caplog.at_level("INFO", logger="nexview"):
        await storage._pakete_aufnehmen(db, nex, gemessen)

    assert gemessen[_paketschluessel(anfrage)].size_bytes == 4 * GB
    assert basis not in gemessen
    meldungen = [m for m in caplog.messages if "names no episode files" in m]
    assert len(meldungen) == 1, caplog.messages


async def test_ohne_files_zaehlt_eine_kleine_summe_voll(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der Deckel greift nur, wo die Summe über die Staffel ginge."""
    _serie(nexcrate, 1399, [_staffel(3, 9 * GB)])
    nexcrate.staffel(
        "tmdb:1399",
        3,
        [
            _folge(nexcrate, 1, None, groesse=3 * GB),
            _folge(nexcrate, 2, None, groesse=2 * GB),
        ],
    )
    anfrage = _paket(db, _nutzer(db))
    basis, gemessen = _staffelzeile(9 * GB)

    await storage._pakete_aufnehmen(db, nex, gemessen)

    assert gemessen[_paketschluessel(anfrage)].size_bytes == 5 * GB
    assert gemessen[basis].size_bytes == 4 * GB


async def test_eine_gewanderte_paketzeile_wird_neu_gemessen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Zeile aus dem Umstieg trägt schon den NEX-Schlüssel; der Abgleich misst sie neu."""
    person = _nutzer(db)
    anfrage = _paket(db, person)
    paket = _paketschluessel(anfrage)
    db.add(
        StorageEntry(
            key=paket,
            user_id=person.id,
            media_type=MediaType.tv,
            fassung_kennung=SERIE_HD,
            tmdb_id=1399,
            season=3,
            title="Example Show",
            size_bytes=1 * GB,
            path="",
            state=StorageState.owned,
            request_id=anfrage.id,
        )
    )
    db.commit()
    _serie(nexcrate, 1399, [_staffel(3, 5 * GB)])
    nexcrate.staffel(
        "tmdb:1399",
        3,
        [
            _folge(nexcrate, 1, [("1", 3 * GB)], groesse=3 * GB),
            _folge(nexcrate, 2, [("1", 3 * GB)], groesse=3 * GB),
            _folge(nexcrate, 3, [("2", 2 * GB)], groesse=2 * GB),
        ],
    )

    await storage.abgleichen(db, nex)

    zeile = db.query(StorageEntry).filter_by(key=paket).one_or_none()
    assert zeile is not None, "die Paketzeile wurde gelöscht"
    assert (zeile.size_bytes, zeile.user_id) == (3 * GB, person.id)
    staffel = db.query(StorageEntry).filter_by(key=f"tv:{SERIE_HD}:tmdb:1399:s3").one()
    assert staffel.size_bytes == 2 * GB


async def test_eine_gescheiterte_staffelansicht_laesst_die_paketzeile_stehen(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nicht gelesen heißt nicht weg: Zeile und Staffel bleiben, wie sie waren."""
    person = _nutzer(db)
    anfrage = _paket(db, person)
    paket = _paketschluessel(anfrage)
    db.add(
        StorageEntry(
            key=paket,
            user_id=person.id,
            media_type=MediaType.tv,
            fassung_kennung=SERIE_HD,
            tmdb_id=1399,
            season=3,
            title="Example Show",
            size_bytes=1 * GB,
            path="",
            state=StorageState.owned,
            request_id=anfrage.id,
        )
    )
    db.commit()
    _serie(nexcrate, 1399, [_staffel(3, 3 * GB)])
    echt = nexcrate._titel_weg

    def stumm(methode: str, teile: list[str], koerper: Any) -> Any:
        if len(teile) == 5 and teile[3] == "seasons":
            return _fehler(500, "internal_error", "Something broke.")
        return echt(methode, teile, koerper)

    monkeypatch.setattr(nexcrate, "_titel_weg", stumm)

    await storage.abgleichen(db, nex)

    zeile = db.query(StorageEntry).filter_by(key=paket).one_or_none()
    assert zeile is not None, "die Paketzeile wurde gelöscht"
    assert (zeile.size_bytes, zeile.user_id) == (1 * GB, person.id)
    staffel = db.query(StorageEntry).filter_by(key=f"tv:{SERIE_HD}:tmdb:1399:s3").one()
    assert staffel.size_bytes == 3 * GB


# --- Was der Merker und das Behalten nicht dürfen ---------------------------------


def _k(tmdb_id: int, staffel: int) -> str:
    return f"tv:{SERIE_HD}:tmdb:{tmdb_id}:s{staffel}"


async def test_eine_ungelesene_serie_schuetzt_nur_ihre_eigenen_zeilen(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Anfang von Serie 12 ist nicht der von Serie 123."""
    _zeile(db, 12, 1, 4 * GB, None)
    _zeile(db, 123, 1, 2 * GB, None)  # gibt es bei nexcrate nicht mehr
    _serie(nexcrate, 12, [_staffel(1, 9 * GB)], tvdb=5012)
    echt = nexcrate._titel_weg

    def stumm(methode: str, teile: list[str], koerper: Any) -> Any:
        if teile[1:3] == ["series", "tmdb:12"] and len(teile) == 3:
            return _fehler(500, "internal_error", "Something broke.")
        return echt(methode, teile, koerper)

    monkeypatch.setattr(nexcrate, "_titel_weg", stumm)

    await storage.abgleichen(db, nex)

    schluessel = {z.key for z in db.query(StorageEntry).all()}
    assert _k(12, 1) in schluessel
    assert _k(123, 1) not in schluessel


async def test_ist_nexcrate_ganz_weg_fragt_jede_fassung_nur_einmal(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein Zeitablauf je Serie: Nach dem ersten Ausfall hört der Lauf auf."""
    for nummer in (1399, 1400, 1401):
        _zeile(db, nummer, 1, 4 * GB, None)
        _serie(nexcrate, nummer, [_staffel(1, 9 * GB)], name=f"Show {nummer}", tvdb=5000 + nummer)
    echt = nexcrate._titel_weg

    def weg(methode: str, teile: list[str], koerper: Any) -> Any:
        if teile[1] == "series" and len(teile) == 3:
            raise httpx.ConnectTimeout("weg")
        return echt(methode, teile, koerper)

    monkeypatch.setattr(nexcrate, "_titel_weg", weg)

    await storage.abgleichen(db, nex)

    # Je Fassung (HD und UHD) ein Aufruf, jeweils an die erste Serie.
    assert _einzelansichten(nexcrate) == ["/api/v1/titles/series/tmdb:1399"] * 2
    schluessel = {z.key for z in db.query(StorageEntry).all()}
    assert all(_k(n, 1) in schluessel for n in (1399, 1400, 1401)), sorted(schluessel)


async def test_eine_marke_ueber_latest_liest_die_staffeln_neu(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """nexcrate aus einer Sicherung: dieselben Marken, ein anderer Stand."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    _serie(nexcrate, 1400, [_staffel(1, 1 * GB)], name="Other Show", tvdb=5400)
    await storage.abgleichen(db, nex)
    assert nex_bestand.gehalten().marke["series"] == 2

    nexcrate.titles.clear()
    nexcrate.seq = 0
    _serie(nexcrate, 1399, [_staffel(1, 5 * GB)])  # wieder Marke 1
    await storage.abgleichen(db, nex)

    db.expire_all()
    assert db.query(StorageEntry).filter_by(key=_k(1399, 1)).one().size_bytes == 5 * GB


async def test_eine_serie_ohne_datei_gilt_als_gelesen_und_wird_geraeumt(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Dateien in nexcrate gelöscht: nichts zu lesen, also auch nichts zu behalten."""
    _zeile(db, 1399, 1, 4 * GB, None)
    nexcrate.serie(
        1399,
        versionen=[
            nexcrate.fassung(
                SERIE_HD, "wanted", size_bytes=None, series={"counts": {"have": 0, "aired": 3}}
            )
        ],
    )

    await storage.abgleichen(db, nex)

    assert db.query(StorageEntry).filter_by(key=_k(1399, 1)).one_or_none() is None
