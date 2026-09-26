"""Der Speicher-Abgleich im NEX-Betrieb raeumt einen wirklich aufgegebenen Titel ab.

Wurde ein Titel in nexcrate vollstaendig entfernt (Datei und
Bibliothekseintrag), blieb sein Speicherposten stehen, solange ein Medienserver
(Plex/Jellyfin) ihn noch aus einer eigenen, noch nicht nachgezogenen Bibliothek
weitermass - der bestehende Geisterposten-Schutz kann "Titel absichtlich
rausgeworfen, Datei behalten" nicht von "Titel und Dateien wirklich aufgegeben"
unterscheiden, wenn er nur die Aenderungsmarke fragt.

Das sichere Signal kommt aus nexcrates Papierkorb (N22): Eine Zeile entsteht
dort nur, wenn ein Titel **mit** Dateien entfernt wird (nexcrates eigener Code,
``routers/v1_write.py::remove_title``, ruft ``recycle_bin.delete`` nur dann;
ohne Dateien bleibt die Zeile ganz aus). ``in_library=False`` an einer solchen
Zeile heisst deshalb: der Nutzer hat die Dateien aufgegeben - unabhaengig davon,
ob ``present`` gerade wahr ist (das sagt nur, ob die Datei noch im
Papierkorb-Ordner liegt; raeumt nexcrate sie dort spaeter auf, verschwindet die
Zeile ganz, siehe nexcrates ``forget_missing``).

⚠️ **Der bestehende Geisterposten-Schutz muss unveraendert bleiben**, wenn ein
Titel **ohne** Dateien geworfen wird (keine Papierkorb-Zeile entsteht):
``tests/test_nex_nachweise.py::
test_ein_posten_den_nexcrate_nicht_mehr_fuehrt_wird_zum_geisterposten`` deckt
das ab und bleibt gruen.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaServerLibraryItem,
    MediaType,
    RequestStatus,
    Role,
    StorageEntry,
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

from .beschaffung.fake_nexcrate import FILM_HD, KEY, URL, FakeNexcrate

GB = 1024**3


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


def _nutzer(db: Session, name: str = "sammler") -> User:
    person = User(username=name, password_hash=hash_password("test"), role=Role.user)
    db.add(person)
    db.commit()
    return person


def _anfrage(db: Session, nutzer: User, tmdb_id: int) -> MediaRequest:
    eintrag = MediaRequest(
        user_id=nutzer.id,
        media_type=MediaType.movie,
        fassung_kennung=FILM_HD,
        tmdb_id=tmdb_id,
        title="Gaslight",
        status=RequestStatus.downloaded,
    )
    db.add(eintrag)
    db.commit()
    return eintrag


def _im_medienserver(db: Session, tmdb_id: int = 13528) -> None:
    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.movie,
            guid=f"plex://movie/{tmdb_id}",
            tmdb_id=tmdb_id,
            title="Gaslight",
            title_key="gaslight",
            size_standard=2 * GB,
        )
    )
    db.commit()


async def _gestellt(nex: Any, nexcrate: FakeNexcrate, db: Session, person: User) -> None:
    """Ein Alt-Titel verbraucht den ersten Lauf, dann Gaslight: angefragt und gemessen."""
    nexcrate.film(1, name="Altbestand")
    await storage.abgleichen(db, nex)

    nexcrate.film(13528, name="Gaslight", versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=2 * GB)])
    _anfrage(db, person, 13528)
    await storage.abgleichen(db, nex)
    zeile = db.query(StorageEntry).filter_by(key=f"movie:{FILM_HD}:tmdb:13528").one_or_none()
    assert zeile is not None and zeile.user_id == person.id


async def test_geloeschter_film_verschwindet_aus_dem_speicherposten(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der einfache Fall, ohne Medienserver: funktioniert schon ohne Papierkorb-Signal."""
    person = _nutzer(db)
    await _gestellt(nex, nexcrate, db, person)

    nexcrate.entfernt("movie", "tmdb:13528", delete_files=True)
    ergebnis = await storage.abgleichen(db, nex)

    zeile = db.query(StorageEntry).filter_by(key=f"movie:{FILM_HD}:tmdb:13528").one_or_none()
    assert zeile is None, "der aufgegebene Titel zaehlt weiter gegen das Kontingent"
    assert ergebnis.entfernt == 1


async def test_geloeschter_film_lebt_nicht_ueber_einen_traegen_medienserver_weiter(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der genaue Fall aus der Notiz: Plex/Jellyfin hat die Loeschung noch nicht gesehen.

    ``nexcrate.entfernt(..., delete_files=True)`` legt genau wie die echte
    nexcrate eine Papierkorb-Zeile mit ``in_library=False`` an (``present``
    bleibt wahr, die Datei liegt noch im Papierkorb-Ordner) - erst das darf den
    Geisterposten-Schutz aussetzen.
    """
    person = _nutzer(db)
    await _gestellt(nex, nexcrate, db, person)
    _im_medienserver(db)

    nexcrate.entfernt("movie", "tmdb:13528", delete_files=True)
    ergebnis = await storage.abgleichen(db, nex)

    zeile = db.query(StorageEntry).filter_by(key=f"movie:{FILM_HD}:tmdb:13528").one_or_none()
    assert zeile is None, "ein traeger Medienserver haelt einen aufgegebenen Titel am Leben"
    assert ergebnis.entfernt == 1


async def test_nur_aus_der_bibliothek_geworfen_bleibt_geisterposten(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Ohne Dateien entfernt legt nexcrate gar keine Papierkorb-Zeile an: bleibt Geisterposten.

    Derselbe Fall wie ``test_nex_nachweise.py`` (Betreiber wirft den Titel aus
    nexcrate, behaelt die Datei) - hier mit Medienserver und ohne
    ``delete_files``, wie es die echte nexcrate fuer diesen Ablauf auch nicht
    tut.
    """
    person = _nutzer(db)
    await _gestellt(nex, nexcrate, db, person)
    _im_medienserver(db)

    nexcrate.entfernt("movie", "tmdb:13528")  # delete_files=False, wie am Fall des Betreibers
    assert nexcrate.recycle == [], "ohne Dateien darf keine Papierkorb-Zeile entstehen"
    ergebnis = await storage.abgleichen(db, nex)

    zeile = db.query(StorageEntry).filter_by(key=f"movie:{FILM_HD}:tmdb:13528").one_or_none()
    assert zeile is not None, "eine noch vorhandene Datei darf den Posten nicht verlieren"
    assert zeile.arr_managed is False
    assert ergebnis.entfernt == 0


async def test_ein_zurueckgeholter_titel_zaehlt_wieder(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Kommt der Titel zurueck, zaehlt die naechste Messung ihn wieder - kein Geist bleibt haengen."""
    person = _nutzer(db)
    await _gestellt(nex, nexcrate, db, person)
    _im_medienserver(db)

    nexcrate.entfernt("movie", "tmdb:13528", delete_files=True)
    ergebnis = await storage.abgleichen(db, nex)
    assert db.query(StorageEntry).filter_by(key=f"movie:{FILM_HD}:tmdb:13528").one_or_none() is None
    assert ergebnis.entfernt == 1

    nexcrate.film(13528, name="Gaslight", versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=2 * GB)])
    await storage.abgleichen(db, nex)

    zeile = db.query(StorageEntry).filter_by(key=f"movie:{FILM_HD}:tmdb:13528").one_or_none()
    assert zeile is not None, "ein wieder angelegter Titel muss wieder zaehlen"
    assert zeile.size_bytes == 2 * GB


async def test_eine_nicht_antwortende_nexcrate_raeumt_nichts_ab(
    nex: Any, nexcrate: FakeNexcrate, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ungelesen ist nicht geloescht: Ein Ausfall darf den Posten nicht kosten."""
    from app.services.beschaffung.base import BeschaffungError
    from app.services.beschaffung.nex import client as nex_client_modul

    person = _nutzer(db)
    _anfrage(db, person, 13528)
    nexcrate.film(13528, name="Gaslight", versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=2 * GB)])
    await storage.abgleichen(db, nex)

    async def scheitert(*_args: Any, **_kwargs: Any) -> Any:
        raise BeschaffungError("nexcrate ist nicht erreichbar.", 502, code="nexcrate_unreachable")

    monkeypatch.setattr(nex_client_modul.NexcrateClient, "titles", scheitert)
    ergebnis = await storage.abgleichen(db, nex)

    zeile = db.query(StorageEntry).filter_by(key=f"movie:{FILM_HD}:tmdb:13528").one_or_none()
    assert zeile is not None, "ein Ausfall darf den Posten nicht abraeumen"
    assert ergebnis.entfernt == 0
