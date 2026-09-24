"""Staffeln aus Liste und ``lookup``: die Einzelansicht je Serie nur noch, wo nötig.

Seit nexcrate 39dfc05 tragen ``GET /titles?after=`` und ``POST /titles/lookup``
``series.seasons`` in derselben Form wie die Einzelansicht, jede Staffel mit
jeder Fassung; ``seq`` bewegt sich auch bei einer Änderung nur an einer
Staffel. Nexview nimmt die Staffeln von dort und holt die Einzelansicht einer
Serie nur noch, wenn eine ältere nexcrate ``null`` schreibt oder das Feld
weglässt. Dann geht alles wie vorher, gemerkt über ``seq``.

``None`` heißt „nicht gelesen", ``[]`` ist eine Antwort: nexcrate kennt zu der
Serie keine Staffel, genau wie ihre Einzelansicht es dann sagt.

Gegen ``tests/beschaffung/fake_nexcrate.py``; ``liste_staffeln`` schaltet sie
auf die ältere nexcrate um.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import RequestStatus, StorageEntry
from app.services import status_poller, storage
from app.services.beschaffung import NEX, Nachschlag, SerienStand, get_beschaffung
from app.services.beschaffung.nex import bestand as nex_bestand
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import (
    KEY,
    SERIE_HD,
    STAFFELN_FEHLT,
    STAFFELN_NULL,
    STAFFELN_WIE_EINZELANSICHT,
    URL,
    FakeNexcrate,
)
from .test_nex_speicher_serien import GB, _einzelansichten, _nutzer, _serie, _staffel, _zeile
from .test_nex_status_staffeln import _staffelanfrage

#: So viele Serien mit Datei legen die Zähl-Tests an.
N = 3
#: Neue nexcrate: keine Einzelansicht. Ältere, mit ``null`` oder ohne das Feld:
#: eine je Serie.
MODI = [
    pytest.param(STAFFELN_WIE_EINZELANSICHT, 0, id="neu"),
    pytest.param(STAFFELN_NULL, N, id="alt-null"),
    pytest.param(STAFFELN_FEHLT, N, id="alt-fehlt"),
]


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


def _serien(nexcrate: FakeNexcrate) -> list[int]:
    """``N`` Serien mit Datei, je zwei Staffeln unterschiedlicher Größe."""
    nummern = [2000 + i for i in range(N)]
    for i, tmdb_id in enumerate(nummern):
        _serie(
            nexcrate,
            tmdb_id,
            [_staffel(1, (i + 1) * GB), _staffel(2, (i + 4) * GB)],
            name=f"Example Show {i}",
            tvdb=121400 + i,
        )
    return nummern


def _erwartet(nummern: list[int]) -> dict[str, int]:
    return {
        f"tv:{SERIE_HD}:tmdb:{tmdb_id}:s{staffel}": (i + (1 if staffel == 1 else 4)) * GB
        for i, tmdb_id in enumerate(nummern)
        for staffel in (1, 2)
    }


def _groessen(db: Session) -> dict[str, int]:
    db.expire_all()
    return {z.key: z.size_bytes for z in db.query(StorageEntry).all()}


# --- Speicher-Abgleich über die Liste ---------------------------------------------


@pytest.mark.parametrize(("modus", "einzeln"), MODI)
async def test_der_speicher_abgleich_holt_die_einzelansicht_nur_bei_aelterer_nexcrate(
    nex: Any, nexcrate: FakeNexcrate, db: Session, modus: str, einzeln: int
) -> None:
    """Dieselben Zeilen in jedem Fall; die Einzelansicht kostet nur die ältere nexcrate.

    Der zweite Lauf ohne Änderung liest nichts neu: bei der neuen nexcrate,
    weil die Liste die Staffeln trägt, bei der älteren über den Merker.
    """
    nexcrate.liste_staffeln = modus
    nummern = _serien(nexcrate)

    await storage.abgleichen(db, nex)

    assert _groessen(db) == _erwartet(nummern)
    assert len(_einzelansichten(nexcrate)) == einzeln

    await storage.abgleichen(db, nex)
    assert len(_einzelansichten(nexcrate)) == einzeln


async def test_eine_geaenderte_staffel_kommt_mit_der_liste(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Nur Staffel 2 wächst: neue Marke, neue Größe, und trotzdem keine Einzelansicht."""
    nummern = _serien(nexcrate)
    await storage.abgleichen(db, nex)

    _serie(
        nexcrate,
        nummern[0],
        [_staffel(1, 1 * GB), _staffel(2, 9 * GB)],
        name="Example Show 0",
        tvdb=121400,
    )
    await storage.abgleichen(db, nex)

    groessen = _groessen(db)
    assert groessen[f"tv:{SERIE_HD}:tmdb:{nummern[0]}:s2"] == 9 * GB
    assert groessen[f"tv:{SERIE_HD}:tmdb:{nummern[0]}:s1"] == 1 * GB
    assert _einzelansichten(nexcrate) == []


async def test_nur_die_serie_ohne_staffeln_in_der_liste_kostet_eine_einzelansicht(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entschieden wird je Eintrag, nicht je Lauf.

    Etwa kurz nach dem Aufspielen einer neuen nexcrate, solange die Liste
    für eine Serie noch den alten Eintrag ohne Staffeln führt.
    """
    nummern = _serien(nexcrate)
    ohne = f"tmdb:{nummern[1]}"
    echt = nexcrate._ohne_seq

    def ohne_staffeln_fuer_eine(titel: dict[str, Any]) -> dict[str, Any]:
        gezeigt = echt(titel)
        if gezeigt.get("ref") == ohne:
            gezeigt["series"] = {**gezeigt["series"], "seasons": None}
        return gezeigt

    monkeypatch.setattr(nexcrate, "_ohne_seq", ohne_staffeln_fuer_eine)

    await storage.abgleichen(db, nex)

    assert _groessen(db) == _erwartet(nummern)
    assert _einzelansichten(nexcrate) == [f"/api/v1/titles/series/{ohne}"]


# --- Nachschlagen über lookup -----------------------------------------------------


@pytest.mark.parametrize(("modus", "einzeln"), MODI)
async def test_nachschlagen_mit_staffeln_holt_die_einzelansicht_nur_bei_aelterer_nexcrate(
    nex: Any, nexcrate: FakeNexcrate, modus: str, einzeln: int
) -> None:
    """Bei der neuen nexcrate genügt ``lookup``: kein Aufruf je Serie, auch nicht die Liste."""
    nexcrate.liste_staffeln = modus
    nummern = _serien(nexcrate)
    gefragt = [Nachschlag("tv", SERIE_HD, tmdb_id, mit_staffeln=True) for tmdb_id in nummern]
    nexcrate.calls.clear()

    antwort = await get_beschaffung(nex).nachschlagen(gefragt)

    for i, wonach in enumerate(gefragt):
        stand = antwort.stand(wonach)
        assert isinstance(stand, SerienStand), wonach
        assert antwort.hat_geantwortet(wonach) is True
        assert stand.staffeln_gelesen is True
        assert sorted(stand.staffeln) == [1, 2]
        assert stand.seasons == {1: (i + 1) * GB, 2: (i + 4) * GB}
    assert len(_einzelansichten(nexcrate)) == einzeln
    if einzeln == 0:
        assert [pfad for _, pfad, _, _ in nexcrate.calls] == ["/api/v1/titles/lookup"]


async def test_nachschlagen_fragt_nur_die_serie_einzeln_die_ohne_staffeln_kommt(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    """Entschieden wird je Titel, auch in ``lookup``.

    Eine Serie mit ``series.seasons`` und eine mit ``null`` in derselben
    Antwort: Die erste gilt so, nur die zweite kostet eine Einzelansicht.
    """
    nummern = _serien(nexcrate)
    ohne = f"tmdb:{nummern[1]}"
    nexcrate.staffeln_null_fuer = {ohne}
    gefragt = [Nachschlag("tv", SERIE_HD, tmdb_id, mit_staffeln=True) for tmdb_id in nummern[:2]]
    nexcrate.calls.clear()

    antwort = await get_beschaffung(nex).nachschlagen(gefragt)

    for i, wonach in enumerate(gefragt):
        stand = antwort.stand(wonach)
        assert isinstance(stand, SerienStand), wonach
        assert antwort.hat_geantwortet(wonach) is True
        assert stand.staffeln_gelesen is True
        assert stand.seasons == {1: (i + 1) * GB, 2: (i + 4) * GB}
    assert _einzelansichten(nexcrate) == [f"/api/v1/titles/series/{ohne}"]


@pytest.mark.parametrize(("modus", "einzeln"), MODI)
async def test_der_rundgang_misst_staffelanfragen_aus_lookup(
    nex: Any, nexcrate: FakeNexcrate, db: Session, modus: str, einzeln: int
) -> None:
    """Fertig, weiter fertig und gelöscht: dieselben Urteile wie über die Einzelansicht."""
    nexcrate.liste_staffeln = modus
    nummern = _serien(nexcrate)
    person = _nutzer(db)
    suchend = _staffelanfrage(
        db, person, staffel=2, status=RequestStatus.searching, tmdb_id=nummern[0]
    )
    fertig = _staffelanfrage(
        db, person, staffel=1, status=RequestStatus.downloaded, tmdb_id=nummern[1]
    )
    weg = _staffelanfrage(
        db, person, staffel=3, status=RequestStatus.downloaded, tmdb_id=nummern[2]
    )

    await status_poller.check_once(db, nex)

    for anfrage in (suchend, fertig, weg):
        db.refresh(anfrage)
    assert suchend.status == RequestStatus.downloaded
    assert fertig.status == RequestStatus.downloaded
    assert weg.status == RequestStatus.deleted
    assert len(_einzelansichten(nexcrate)) == einzeln


# --- [] ist eine Antwort, None nicht ----------------------------------------------


async def test_die_attrappe_nennt_eine_serie_ohne_staffeln_wie_nexcrate(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    """Eine Serie ohne Staffeln heißt bei nexcrate 39dfc05 ``[]``, in Liste und
    Einzelansicht; ``null`` schreibt nur eine ältere. Die Attrappe gab bis zum
    24.09.2026 ``null`` vor und ließ damit jeden Test, der nicht daran dachte,
    still als ältere nexcrate laufen."""
    nexcrate.serie(1399)
    wonach = Nachschlag("tv", SERIE_HD, 1399, mit_staffeln=True)

    stand = (await get_beschaffung(nex).nachschlagen([wonach])).stand(wonach)

    assert isinstance(stand, SerienStand)
    assert stand.staffeln_gelesen is True
    assert _einzelansichten(nexcrate) == []
    einzeln = await nex_client.NexcrateClient(URL, KEY).title("series", "tmdb:1399")
    assert einzeln["series"]["seasons"] == []


async def test_eine_leere_staffelliste_ist_eine_antwort(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """``[]`` trotz Datei: nexcrate kennt keine Staffel, und das gilt, ohne nachzufragen.

    Dieselbe Aussage hätte die Einzelansicht gemacht, und Nexview nahm sie dort
    schon als Antwort: Die alte Staffelzeile wird geräumt, eine Staffelanfrage
    kippt. Nur ``None`` (``null`` oder fehlend) heißt „nicht gelesen".
    """
    _serie(nexcrate, 1399, [_staffel(1, 3 * GB)])
    nexcrate.titles[("series", "tmdb:1399")]["series"]["seasons"] = []
    _zeile(db, 1399, 1, 3 * GB, None)
    wonach = Nachschlag("tv", SERIE_HD, 1399, mit_staffeln=True)

    antwort = await get_beschaffung(nex).nachschlagen([wonach])
    stand = antwort.stand(wonach)
    assert isinstance(stand, SerienStand)
    assert antwort.hat_geantwortet(wonach) is True
    assert stand.staffeln_gelesen is True
    assert (stand.staffeln, stand.seasons) == ({}, {})

    ergebnis = await storage.abgleichen(db, nex)

    assert _groessen(db) == {}
    assert ergebnis.entfernt == 1
    assert _einzelansichten(nexcrate) == []
