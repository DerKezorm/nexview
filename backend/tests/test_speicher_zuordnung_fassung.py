"""Wem ein neuer Posten gehört, entscheidet die Fassung, nicht die Stufe.

Die Zuordnung verglich die Stufe (``standard``/``uhd``). Mit zwei Fassungen
derselben Klasse, etwa 3D neben Full-HD (25.09.2026 an der Live-Instanz), trug
eine Anfrage in der einen Fassung jede Datei der anderen. Erledigte Anfragen
aus der Arr-Zeit behalten beim Umstieg ihre alte Kennung; für sie gilt die
Stufe weiter, sonst fiele ihr Posten ans Haus.

Titel und Konten erfunden.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Fassung, MediaRequest, MediaType, RequestStatus, Role, User
from app.security import hash_password
from app.services import storage

HD_EINS = "v_hd_eins"
HD_ZWEI = "v_hd_zwei"


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as sitzung:
        for reihe, kennung in enumerate((HD_EINS, HD_ZWEI)):
            sitzung.add(
                Fassung(
                    kennung=kennung,
                    media_type="movie",
                    name=kennung,
                    klasse="hd",
                    reihenfolge=reihe,
                    bereit=True,
                    quelle="nex",
                    aktiv=True,
                )
            )
        sitzung.commit()
        yield sitzung


def _person(db: Session) -> User:
    person = User(username="person", password_hash=hash_password("test"), role=Role.user)
    db.add(person)
    db.commit()
    return person


def _anfrage(db: Session, person: User, fassung: str) -> None:
    db.add(
        MediaRequest(
            user_id=person.id,
            media_type=MediaType.movie,
            tmdb_id=9900,
            title="Erfundener Film",
            fassung_kennung=fassung,
            status=RequestStatus.downloaded,
        )
    )
    db.commit()


def _posten(fassung: str) -> storage._Gemessen:
    return storage._Gemessen(
        key=storage.schluessel(MediaType.movie, fassung, tmdb_id=9900),
        media_type=MediaType.movie,
        tier="standard",
        tmdb_id=9900,
        tvdb_id=None,
        season=None,
        title="Erfundener Film",
        size_bytes=1,
    )


@pytest.mark.parametrize(("angefragt", "gehoert"), [(HD_EINS, True), (HD_ZWEI, False)])
def test_eine_anfrage_traegt_nur_die_datei_ihrer_fassung(
    db: Session, angefragt: str, gehoert: bool
) -> None:
    person = _person(db)
    _anfrage(db, person, angefragt)
    posten = _posten(HD_EINS)

    zuordnung = storage._zuordnung(db, [posten])

    assert (zuordnung.get(posten.key) == person.id) is gehoert, zuordnung


def test_eine_erledigte_anfrage_aus_der_arr_zeit_traegt_weiter(db: Session) -> None:
    """Sie trägt ``radarr-standard``, das es im NEX-Betrieb nicht mehr gibt."""
    person = _person(db)
    _anfrage(db, person, "radarr-standard")
    posten = _posten(HD_EINS)

    assert storage._zuordnung(db, [posten]).get(posten.key) == person.id


@pytest.mark.parametrize(("angefragt", "gehoert"), [("v_tv_eins", True), ("v_tv_zwei", False)])
def test_bei_serien_ebenso(db: Session, angefragt: str, gehoert: bool) -> None:
    for reihe, kennung in enumerate(("v_tv_eins", "v_tv_zwei")):
        db.add(
            Fassung(
                kennung=kennung, media_type="tv", name=kennung, klasse="hd",
                reihenfolge=100 + reihe, bereit=True, quelle="nex", aktiv=True,
            )
        )
    person = _person(db)
    db.add(
        MediaRequest(
            user_id=person.id, media_type=MediaType.tv, tmdb_id=9901, tvdb_id=8801,
            title="Erfundene Serie", fassung_kennung=angefragt, season=1,
            status=RequestStatus.downloaded,
        )
    )
    db.commit()
    posten = storage._Gemessen(
        key=storage.schluessel(MediaType.tv, "v_tv_eins", tmdb_id=9901, season=1),
        media_type=MediaType.tv, tier="standard", tmdb_id=9901, tvdb_id=8801,
        season=1, title="Erfundene Serie", size_bytes=1,
    )

    zuordnung = storage._zuordnung(db, [posten])

    assert (zuordnung.get(posten.key) == person.id) is gehoert, zuordnung


def test_eine_serie_ohne_tvdb_nummer_findet_ihren_posten_ueber_tmdb(db: Session) -> None:
    """nexcrate ankert auf TMDB. Kennt TMDB keine TVDB-Nummer, trägt die
    Anfrage keine; sie wurde trotzdem nur über TVDB gesucht, und der Posten
    fiel ans Haus."""
    db.add(
        Fassung(
            kennung="v_tv_eins", media_type="tv", name="v_tv_eins", klasse="hd",
            reihenfolge=100, bereit=True, quelle="nex", aktiv=True,
        )
    )
    person = _person(db)
    db.add(
        MediaRequest(
            user_id=person.id, media_type=MediaType.tv, tmdb_id=9902, tvdb_id=None,
            title="Erfundene Serie", fassung_kennung="v_tv_eins", season=None,
            status=RequestStatus.downloaded,
        )
    )
    db.commit()
    posten = storage._Gemessen(
        key=storage.schluessel(MediaType.tv, "v_tv_eins", tmdb_id=9902, season=1),
        media_type=MediaType.tv, tier="standard", tmdb_id=9902, tvdb_id=8802,
        season=1, title="Erfundene Serie", size_bytes=1,
    )

    assert storage._zuordnung(db, [posten]).get(posten.key) == person.id
