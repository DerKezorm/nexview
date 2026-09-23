"""Der Status-Abgleich misst im NEX-Betrieb Staffeln, nicht nur Serien.

nexcrates ``lookup`` nennt für Serien nie Staffeln (``series.seasons`` ist
``null``, wie in der Liste; gemessen). Der Takt-Läufer nahm seine Staffeldaten
bisher genau daraus: Eine fertige Staffelanfrage mit NEX-Fassung galt beim
ersten Lauf als gelöscht, obwohl die Staffel vollständig dalag, und eine
suchende Staffelanfrage wurde nie fertig.

Dazu der Speicher-Abgleich: Eine Serie ohne ``tvdb:`` in ``refs`` fehlte im
TVDB-Index und wurde nicht gemessen; ihre gewanderte Zeile verschwand samt
Besitzer. Im NEX-Betrieb ist TMDB der Anker, TVDB nur Beiwerk.

Folgen-Pakete gehen über ``folgen_stand``: gelesen in der Fassung der Anfrage,
nicht in der Hauptfassung, und eine gescheiterte Folgenansicht hält den Lauf
nicht an.

Gegen ``tests/beschaffung/fake_nexcrate.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    Notification,
    NotificationType,
    RequestStatus,
    StorageEntry,
    User,
)
from app.services import status_poller, storage
from app.services.beschaffung import NEX, Nachschlag, get_beschaffung
from app.services.beschaffung.nex import bestand as nex_bestand
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import (
    FILM_HD,
    KEY,
    SERIE_HD,
    SERIE_UHD,
    URL,
    FakeNexcrate,
    _fehler,
)
from .test_nex_speicher_serien import (
    GB,
    _einzelansichten,
    _nutzer,
    _serie,
    _staffel,
    _zeile,
)


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


#: Lange genug her, dass die Schonfrist nicht mehr gilt.
FRUEHER = datetime(2020, 1, 1, tzinfo=UTC).replace(tzinfo=None)


def _staffelanfrage(
    db: Session,
    person: User,
    *,
    staffel: int,
    status: RequestStatus,
    tmdb_id: int = 1399,
    **weiter: Any,
) -> MediaRequest:
    anfrage = MediaRequest(
        user_id=person.id,
        media_type=MediaType.tv,
        tmdb_id=tmdb_id,
        title="Example Show",
        fassung_kennung=SERIE_HD,
        season=staffel,
        status=status,
        arr_id=tmdb_id,
        **weiter,
    )
    db.add(anfrage)
    db.commit()
    return anfrage


def _stumm_fuer(nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch, ref: str) -> None:
    """Die Einzelansicht dieser einen Serie antwortet 500, alles andere normal."""
    echt = nexcrate._titel_weg

    def stumm(methode: str, teile: list[str], koerper: Any) -> Any:
        if teile[1:3] == ["series", ref] and len(teile) == 3:
            return _fehler(500, "internal_error", "Something broke.")
        return echt(methode, teile, koerper)

    monkeypatch.setattr(nexcrate, "_titel_weg", stumm)


# --- (a) Eine fertige Staffel bleibt fertig ---------------------------------------


async def test_eine_fertige_staffel_bleibt_fertig(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der Befund: vorher setzte der erste Lauf sie auf „gelöscht"."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    person = _nutzer(db)
    anfrage = _staffelanfrage(db, person, staffel=1, status=RequestStatus.downloaded)

    await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.downloaded


# --- (b) Eine suchende Staffel wird fertig ---------------------------------------


async def test_eine_suchende_staffel_wird_fertig_gemeldet_und_verbucht(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB), _staffel(2, 5 * GB)])
    person = _nutzer(db)
    anfrage = _staffelanfrage(db, person, staffel=2, status=RequestStatus.searching)

    fertig = await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert fertig == 1
    assert anfrage.status == RequestStatus.downloaded
    arten = [n.type for n in db.query(Notification).filter_by(user_id=person.id).all()]
    assert NotificationType.download_complete in arten
    # Verbucht wird nur die angefragte Staffel, nicht die ganze Serie.
    zeilen = {z.key: z for z in db.query(StorageEntry).all()}
    assert set(zeilen) == {f"tv:{SERIE_HD}:tmdb:1399:s2"}, sorted(zeilen)
    zeile = zeilen[f"tv:{SERIE_HD}:tmdb:1399:s2"]
    assert (zeile.size_bytes, zeile.user_id) == (5 * GB, person.id)


async def test_eine_freigegebene_staffel_wird_fertig(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    person = _nutzer(db)
    anfrage = _staffelanfrage(db, person, staffel=1, status=RequestStatus.approved)

    await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.downloaded


# --- (c) Weg ist nur, was gelesen wurde ------------------------------------------


async def test_eine_wirklich_fehlende_staffel_kippt_nach_dem_lesen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Staffel weg, Serie da: gelöscht - aber erst, nachdem die Staffeln gelesen sind.

    Und eine Serie, die nexcrate gar nicht mehr führt, bricht die offene
    Anfrage ab wie bisher.
    """
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    person = _nutzer(db)
    geladen = _staffelanfrage(db, person, staffel=2, status=RequestStatus.downloaded)
    verschwunden = _staffelanfrage(
        db,
        person,
        staffel=1,
        status=RequestStatus.searching,
        tmdb_id=1400,
        requested_at=FRUEHER,
    )

    await status_poller.check_once(db, nex)

    db.refresh(geladen)
    db.refresh(verschwunden)
    assert geladen.status == RequestStatus.deleted
    assert verschwunden.status == RequestStatus.cancelled
    assert _einzelansichten(nexcrate) == ["/api/v1/titles/series/tmdb:1399"]


async def test_eine_gescheiterte_einzelansicht_ist_keine_antwort(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """500 für diese Serie: nicht geantwortet, und nichts kippt."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    person = _nutzer(db)
    geladen = _staffelanfrage(db, person, staffel=1, status=RequestStatus.downloaded)
    suchend = _staffelanfrage(
        db, person, staffel=2, status=RequestStatus.searching, requested_at=FRUEHER
    )
    _stumm_fuer(nexcrate, monkeypatch, "tmdb:1399")

    await status_poller.check_once(db, nex)

    db.refresh(geladen)
    db.refresh(suchend)
    assert geladen.status == RequestStatus.downloaded
    assert suchend.status == RequestStatus.searching

    wonach = Nachschlag("tv", SERIE_HD, 1399, mit_staffeln=True)
    antwort = await get_beschaffung(nex).nachschlagen([wonach])
    assert antwort.hat_geantwortet(wonach) is False


# --- Status und Speicher teilen die Einzelansicht --------------------------------


async def test_status_und_speicher_lesen_die_einzelansicht_einmal(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der Merker gilt für beide Läufe; gelesen wird erst nach einer Änderung neu."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    person = _nutzer(db)
    _staffelanfrage(db, person, staffel=1, status=RequestStatus.downloaded)

    await status_poller.check_once(db, nex)
    assert len(_einzelansichten(nexcrate)) == 1

    await status_poller.check_once(db, nex)
    await storage.abgleichen(db, nex)
    assert len(_einzelansichten(nexcrate)) == 1


# --- (d) Der Speicher-Abgleich ankert auf TMDB -----------------------------------


async def test_eine_serie_ohne_tvdb_wird_gemessen_und_behaelt_ihre_zeile(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    person = _nutzer(db)
    _zeile(db, 1399, 2, 5 * GB, person)
    _serie(nexcrate, 1399, [_staffel(1, 2 * GB), _staffel(2, 6 * GB)], tvdb=None)

    ergebnis = await storage.abgleichen(db, nex)

    zeilen = {z.key: z for z in db.query(StorageEntry).all()}
    zweite = zeilen.get(f"tv:{SERIE_HD}:tmdb:1399:s2")
    assert zweite is not None, "die gewanderte Staffelzeile wurde gelöscht"
    assert (zweite.size_bytes, zweite.user_id) == (6 * GB, person.id)
    assert zeilen[f"tv:{SERIE_HD}:tmdb:1399:s1"].size_bytes == 2 * GB
    assert ergebnis.entfernt == 0


async def test_zwei_serien_mit_und_ohne_tvdb_bleiben_ueber_zwei_laeufe(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Auch der zweite Lauf, der die Einzelansicht aus dem Merker nimmt."""
    person = _nutzer(db)
    _zeile(db, 1399, 1, 5 * GB, person)
    _zeile(db, 1500, 1, 5 * GB, person)
    _serie(nexcrate, 1399, [_staffel(1, 6 * GB)])
    _serie(nexcrate, 1500, [_staffel(1, 7 * GB)], name="Other Show", tvdb=None)

    for lauf in (1, 2):
        await storage.abgleichen(db, nex)
        db.expire_all()
        zeilen = {z.key: z for z in db.query(StorageEntry).all()}
        erste = zeilen.get(f"tv:{SERIE_HD}:tmdb:1399:s1")
        zweite = zeilen.get(f"tv:{SERIE_HD}:tmdb:1500:s1")
        assert erste is not None and zweite is not None, (lauf, sorted(zeilen))
        assert (erste.size_bytes, erste.user_id) == (6 * GB, person.id), lauf
        assert (zweite.size_bytes, zweite.user_id) == (7 * GB, person.id), lauf


# --- Der Merker gilt nur für den Stand, zu dem er gelesen wurde ------------------


async def test_eine_geloeschte_staffel_wird_nach_neuer_marke_erkannt(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Gemerkte Staffeln zu einer alten Marke beschreiben einen Stand, den es nicht mehr gibt."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB), _staffel(2, 4 * GB)])
    person = _nutzer(db)
    anfrage = _staffelanfrage(db, person, staffel=1, status=RequestStatus.downloaded)

    await status_poller.check_once(db, nex)
    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.downloaded

    # Staffel 1 gelöscht: neue Marke, neue Zahlen.
    _serie(nexcrate, 1399, [_staffel(2, 4 * GB)])
    await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.deleted


async def test_hinkt_die_liste_hinter_lookup_her_wird_neu_gelesen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Liste kennt noch keine neue Marke, ``lookup`` schon andere Fassungszahlen."""
    titel = _serie(nexcrate, 1399, [_staffel(1, 3 * GB), _staffel(2, 4 * GB)])
    person = _nutzer(db)
    anfrage = _staffelanfrage(db, person, staffel=1, status=RequestStatus.downloaded)

    await status_poller.check_once(db, nex)
    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.downloaded

    # Ohne neue Marke: Über die Liste ist nichts Neues zu sehen.
    titel["versions"] = [
        nexcrate.fassung(
            SERIE_HD, "available", size_bytes=4 * GB, series={"counts": {"have": 3, "aired": 6}}
        )
    ]
    titel["series"]["seasons"] = [_staffel(2, 4 * GB)]
    await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.deleted


async def test_kennt_lookup_die_serie_und_die_einzelansicht_nicht_entscheidet_der_naechste_lookup(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 der Einzelansicht, obwohl ``lookup`` die Serie eben noch nannte.

    Das ist kein „keine Staffeln": Die Frage bleibt eine Runde ungelesen, und
    nichts kippt. Ist die Serie wirklich weg, sagt es der nächste ``lookup``.
    """
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    person = _nutzer(db)
    anfrage = _staffelanfrage(db, person, staffel=1, status=RequestStatus.downloaded)
    echt = nexcrate._titel_weg

    def fehlt(methode: str, teile: list[str], koerper: Any) -> Any:
        if teile[1:3] == ["series", "tmdb:1399"] and len(teile) == 3:
            return _fehler(404, "title_not_found", "nexcrate does not have this title.")
        return echt(methode, teile, koerper)

    monkeypatch.setattr(nexcrate, "_titel_weg", fehlt)

    await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.downloaded
    wonach = Nachschlag("tv", SERIE_HD, 1399, mit_staffeln=True)
    antwort = await get_beschaffung(nex).nachschlagen([wonach])
    assert antwort.hat_geantwortet(wonach) is False

    nexcrate.entfernt("series", "tmdb:1399")
    await status_poller.check_once(db, nex)

    db.refresh(anfrage)
    assert anfrage.status == RequestStatus.deleted


# --- Folgen-Pakete: gelesen in ihrer Fassung --------------------------------------


def _folgen(nexcrate: FakeNexcrate, tmdb_id: int, kennung: str, staffel: int = 1) -> None:
    """Drei Folgen, die nur in dieser Fassung liegen."""
    nexcrate.staffel(
        f"tmdb:{tmdb_id}",
        staffel,
        [
            nexcrate.folge(
                nummer, versionen=[{"version_id": kennung, "state": "available", "monitored": True}]
            )
            for nummer in (1, 2, 3)
        ],
    )


def _paket(
    db: Session,
    person: User,
    *,
    fassung: str,
    status: RequestStatus,
    folgen: list[int] | None = None,
    tmdb_id: int = 1399,
) -> MediaRequest:
    anfrage = MediaRequest(
        user_id=person.id,
        media_type=MediaType.tv,
        tmdb_id=tmdb_id,
        title="Example Show",
        fassung_kennung=fassung,
        season=1,
        episodes=folgen or [1, 2],
        status=status,
        arr_id=tmdb_id,
    )
    db.add(anfrage)
    db.commit()
    return anfrage


async def test_ein_fertiges_paket_in_der_zweitfassung_bleibt_fertig(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der Befund: gelesen wurde in der Hauptfassung, dort fehlten die Folgen - „gelöscht"."""
    nexcrate.serie(
        1399,
        versionen=[
            nexcrate.fassung(
                SERIE_UHD, "available", size_bytes=3 * GB, series={"counts": {"have": 3, "aired": 3}}
            )
        ],
        staffeln=[_staffel(1, 3 * GB, SERIE_UHD)],
    )
    _folgen(nexcrate, 1399, SERIE_UHD)
    person = _nutzer(db)
    paket = _paket(db, person, fassung=SERIE_UHD, status=RequestStatus.downloaded)

    await status_poller.check_once(db, nex)

    db.refresh(paket)
    assert paket.status == RequestStatus.downloaded


async def test_ein_suchendes_paket_in_der_zweitfassung_wartet_auf_seine_folgen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Folgen liegen nur in HD: Das 4K-Paket ist nicht fertig, und keiner bekommt Bescheid."""
    nexcrate.serie(
        1399,
        versionen=[
            nexcrate.fassung(
                SERIE_HD, "available", size_bytes=3 * GB, series={"counts": {"have": 3, "aired": 3}}
            ),
            nexcrate.fassung(
                SERIE_UHD, "wanted", size_bytes=0, series={"counts": {"have": 0, "aired": 3}}
            ),
        ],
        staffeln=[_staffel(1, 3 * GB, SERIE_HD)],
    )
    _folgen(nexcrate, 1399, SERIE_HD)
    person = _nutzer(db)
    paket = _paket(db, person, fassung=SERIE_UHD, status=RequestStatus.searching)

    fertig = await status_poller.check_once(db, nex)

    db.refresh(paket)
    assert fertig == 0
    assert paket.status == RequestStatus.searching
    assert db.query(Notification).filter_by(user_id=person.id).count() == 0


async def test_zwei_pakete_derselben_klasse_werden_je_fassung_gelesen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Zwei HD-Fassungen haben dieselbe Stufe; der Befund der einen gilt nicht für die andere."""
    zweite = "v_0000beef"
    nexcrate.versions.append(nexcrate._version(zweite, "series", "Series Extra", 3, "hd"))
    nex_fassungen.schreiben(db, nexcrate.versions)
    db.commit()
    nexcrate.serie(
        1399,
        versionen=[
            nexcrate.fassung(
                SERIE_HD, "available", size_bytes=3 * GB, series={"counts": {"have": 3, "aired": 3}}
            ),
            nexcrate.fassung(zweite, "wanted", size_bytes=0, series={"counts": {"have": 0, "aired": 3}}),
        ],
        staffeln=[_staffel(1, 3 * GB)],
    )
    _folgen(nexcrate, 1399, SERIE_HD)
    person = _nutzer(db)
    suchend = _paket(db, person, fassung=zweite, status=RequestStatus.searching)
    fertig = _paket(db, person, fassung=SERIE_HD, status=RequestStatus.downloaded, folgen=[3])

    await status_poller.check_once(db, nex)

    db.refresh(suchend)
    db.refresh(fertig)
    assert suchend.status == RequestStatus.searching
    assert fertig.status == RequestStatus.downloaded


async def test_eine_gescheiterte_folgenansicht_haelt_den_lauf_nicht_an(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """500 für die Serie eines Pakets: Das Paket bleibt, wie es ist, alle anderen laufen weiter.

    Vorher warf ``check_once`` die Ausnahme weiter, und der ganze Statuslauf
    brach ab, jede Runde. Und „nicht gelesen" ist nicht „keine Folgen": Das
    fertige Paket darf daraus nicht „gelöscht" werden.
    """
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    _folgen(nexcrate, 1399, SERIE_HD)
    _serie(nexcrate, 1500, [_staffel(1, 3 * GB)], name="Other Show", tvdb=None)
    nexcrate.film(603)
    person = _nutzer(db)
    suchendes_paket = _paket(db, person, fassung=SERIE_HD, status=RequestStatus.searching)
    fertiges_paket = _paket(
        db, person, fassung=SERIE_HD, status=RequestStatus.downloaded, folgen=[3]
    )
    film = MediaRequest(
        user_id=person.id,
        media_type=MediaType.movie,
        tmdb_id=603,
        title="Example Movie",
        fassung_kennung=FILM_HD,
        status=RequestStatus.searching,
        arr_id=603,
    )
    db.add(film)
    db.commit()
    staffel = _staffelanfrage(
        db, person, staffel=1, status=RequestStatus.searching, tmdb_id=1500
    )
    _stumm_fuer(nexcrate, monkeypatch, "tmdb:1399")

    await status_poller.check_once(db, nex)

    for anfrage in (suchendes_paket, fertiges_paket, film, staffel):
        db.refresh(anfrage)
    assert suchendes_paket.status == RequestStatus.searching
    assert fertiges_paket.status == RequestStatus.downloaded
    assert film.status == RequestStatus.downloaded
    assert staffel.status == RequestStatus.downloaded


# --- Nicht gelesen ist nicht "keine Folgen" ---------------------------------------


def _staffelansicht_fehlt(nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch, ref: str) -> None:
    """Die Einzelansicht nennt die Staffel, ihre Staffelansicht antwortet 404."""
    echt = nexcrate._titel_weg

    def fehlt(methode: str, teile: list[str], koerper: Any) -> Any:
        if teile[1:3] == ["series", ref] and len(teile) == 5:
            return _fehler(404, "season_not_found", "The series has no such season.")
        return echt(methode, teile, koerper)

    monkeypatch.setattr(nexcrate, "_titel_weg", fehlt)


async def test_ein_fertiges_paket_bleibt_bei_404_der_einzelansicht_bis_lookup_die_serie_verliert(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``lookup`` kennt die Serie, die Einzelansicht sagt 404: nicht gelesen, nicht „gelöscht".

    Vorher wurde aus dem ``None`` ein ``{}``, und ``ist_noch_da`` setzte das
    fertige Paket auf „gelöscht". Weg ist es erst, wenn ``lookup`` die Serie
    nicht mehr kennt.
    """
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    _folgen(nexcrate, 1399, SERIE_HD)
    person = _nutzer(db)
    paket = _paket(db, person, fassung=SERIE_HD, status=RequestStatus.downloaded)
    echt = nexcrate._titel_weg

    def fehlt(methode: str, teile: list[str], koerper: Any) -> Any:
        if teile[1:3] == ["series", "tmdb:1399"] and len(teile) == 3:
            return _fehler(404, "title_not_found", "nexcrate does not have this title.")
        return echt(methode, teile, koerper)

    monkeypatch.setattr(nexcrate, "_titel_weg", fehlt)

    await status_poller.check_once(db, nex)

    db.refresh(paket)
    assert paket.status == RequestStatus.downloaded

    nexcrate.entfernt("series", "tmdb:1399")
    await status_poller.check_once(db, nex)

    db.refresh(paket)
    assert paket.status == RequestStatus.deleted


async def test_ein_fertiges_paket_bleibt_bei_404_der_staffelansicht(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Einzelansicht nennt Staffel 1, deren Ansicht sagt 404: Die Serie gilt als ungelesen."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    _folgen(nexcrate, 1399, SERIE_HD)
    person = _nutzer(db)
    paket = _paket(db, person, fassung=SERIE_HD, status=RequestStatus.downloaded)
    _staffelansicht_fehlt(nexcrate, monkeypatch, "tmdb:1399")

    await status_poller.check_once(db, nex)

    db.refresh(paket)
    assert paket.status == RequestStatus.downloaded


async def test_zwei_serien_derselben_fassung_werden_je_serie_gelesen(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der Merker gilt je Serie: Die Folgen der einen machen das Paket der anderen nicht fertig."""
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    _folgen(nexcrate, 1399, SERIE_HD)
    _serie(nexcrate, 1500, [_staffel(1, 3 * GB)], name="Other Show", tvdb=None)
    nexcrate.staffel(
        "tmdb:1500",
        1,
        [
            nexcrate.folge(
                nummer, versionen=[{"version_id": SERIE_HD, "state": "wanted", "monitored": True}]
            )
            for nummer in (1, 2, 3)
        ],
    )
    person = _nutzer(db)
    liegt = _paket(db, person, fassung=SERIE_HD, status=RequestStatus.searching)
    wartet = _paket(db, person, fassung=SERIE_HD, status=RequestStatus.searching, tmdb_id=1500)

    await status_poller.check_once(db, nex)

    db.refresh(liegt)
    db.refresh(wartet)
    assert liegt.status == RequestStatus.downloaded
    assert wartet.status == RequestStatus.searching


async def test_eine_gescheiterte_einzelansicht_wird_je_runde_einmal_gefragt(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drei Pakete derselben Serie und Fassung, 500: eine Einzelansicht je Runde, nicht drei.

    Auch der Fehlschlag wird gemerkt; sonst fragt jedes Paket einer
    ausgefallenen Serie noch einmal nach.
    """
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    _folgen(nexcrate, 1399, SERIE_HD)
    person = _nutzer(db)
    _paket(db, person, fassung=SERIE_HD, status=RequestStatus.searching)
    _paket(db, person, fassung=SERIE_HD, status=RequestStatus.searching, folgen=[3])
    _paket(db, person, fassung=SERIE_HD, status=RequestStatus.downloaded, folgen=[2])
    _stumm_fuer(nexcrate, monkeypatch, "tmdb:1399")

    for runde in range(2):
        vorher = len(_einzelansichten(nexcrate))
        await status_poller.check_once(db, nex)
        assert len(_einzelansichten(nexcrate)) - vorher == 1, runde
