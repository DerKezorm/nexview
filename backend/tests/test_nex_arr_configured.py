"""Im NEX-Betrieb fragt die Oberflaeche den Weg, nicht Radarr oder Sonarr.

``settings.arr_configured`` ist im NEX-Betrieb immer ``False``, und
``radarr_configured``/``sonarr_configured`` sind es, sobald keine alten
Zugaenge mehr dastehen. Jede Stelle ausserhalb der Grenze, die daran eine
Nutzerfunktion haengte, fiel im NEX-Betrieb still aus:

* die Duplikatsperre und die Abzeichen zaehlten jede Kopie des Medienservers,
  auch wenn es eine 4K-Fassung gibt: Eine reine 4K-Kopie sperrte die
  HD-Anfrage;
* die Startseite behielt jede erledigte Anfrage, auch geloeschte Titel;
* die Kontoaufloesung fuehrte jede Serienanfrage als offen;
* „Meine" im Kalender blieb leer;
* Kinder, Vormerkungen und Bewertungen lasen nur die Hauptfassung;
* die 4K-Sperre fragte „liegt die Datei in HD" die Hauptfassung, auch wenn
  die eine 4K-Fassung ist.

Gegen ``tests/beschaffung/fake_nexcrate.py``; Titel und Adressen erfunden.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaServerLibraryItem,
    MediaType,
    RequestStatus,
    Role,
    TitleRating,
    TitleWatch,
    User,
)
from app.schemas_media import MediaItem
from app.security import hash_password
from app.services import calendar as calendar_service
from app.services import kids, kontoaufloesung, requests_service, watch
from app.services.beschaffung import NEX
from app.services.beschaffung.nex import bestand as nex_bestand
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import (
    FILM_HD,
    FILM_UHD,
    KEY,
    SERIE_HD,
    URL,
    FakeNexcrate,
)
from .conftest import auth_headers, create_user

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


def _einrichten(nexcrate: FakeNexcrate, versionen: list[dict[str, Any]] | None = None) -> Any:
    with SessionLocal() as sitzung:
        save_settings(
            sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY}
        )
        nex_fassungen.schreiben(sitzung, nexcrate.versions if versionen is None else versionen)
        sitzung.commit()
        return load_settings(sitzung, frisch=True)


@pytest.fixture
def nex(nexcrate: FakeNexcrate) -> Any:
    return _einrichten(nexcrate)


def _nur_hd(nexcrate: FakeNexcrate) -> list[dict[str, Any]]:
    return [v for v in nexcrate.versions if v["tier"] != "uhd"]


def _nutzer(db: Session, name: str, rolle: Role = Role.user) -> User:
    person = User(username=name, password_hash=hash_password("test"), role=rolle)
    db.add(person)
    db.commit()
    return person


def _titel(tmdb_id: int) -> MediaItem:
    return MediaItem(
        media_type="movie", tmdb_id=tmdb_id, title="Erfundener Film", release_date="2020-01-01"
    )


def _im_medienserver(
    db: Session, tmdb_id: int, *, hd: bool, uhd: bool, jahr: int = 2020
) -> None:
    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.movie,
            guid=f"plex://movie/{tmdb_id}",
            tmdb_id=tmdb_id,
            title="Erfundener Film",
            title_key="erfundener film",
            year=jahr,
            has_standard=hd,
            has_uhd=uhd,
        )
    )
    db.commit()


# --- 1. Duplikatsperre und Abzeichen: zwei Achsen, sobald es 4K gibt -------------


async def test_eine_reine_4k_kopie_im_medienserver_sperrt_die_hd_anfrage_nicht(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Vorher zaehlte fuer HD jede Kopie, weil ``arr_configured(.., "uhd")`` nie galt."""
    _im_medienserver(db, 9201, hd=False, uhd=True)
    nexcrate.film(9201, name="Erfundener Film", versionen=[])
    chefin = _nutzer(db, "chefin", Role.admin)

    anfrage = await requests_service.create_request(
        db, nex, chefin, _titel(9201), quality_profile_id=None, fassung=FILM_HD
    )

    assert anfrage.fassung_kennung == FILM_HD


async def test_ohne_4k_fassung_sperrt_jede_kopie_die_hd_anfrage(
    nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Gegenprobe: Ohne zweite Achse lautet die Frage nur „habe ich ihn"."""
    nex = _einrichten(nexcrate, _nur_hd(nexcrate))
    _im_medienserver(db, 9202, hd=False, uhd=True)
    nexcrate.film(9202, name="Erfundener Film", versionen=[])
    chefin = _nutzer(db, "chefin", Role.admin)

    with pytest.raises(requests_service.RequestError) as gefangen:
        await requests_service.create_request(
            db, nex, chefin, _titel(9202), quality_profile_id=None, fassung=FILM_HD
        )

    assert gefangen.value.code == "already_on_media_server"


def _status_auf_entdecken(client: TestClient, db: Session, *, hd: bool, uhd: bool) -> str:
    karte = client.get("/api/discover/movie").json()["items"][0]
    tmdb_id = karte["tmdb_id"]
    jahr = int(karte["release_date"][:4])
    _im_medienserver(db, tmdb_id, hd=hd, uhd=uhd, jahr=jahr)
    karten = client.get("/api/discover/movie").json()["items"]
    return next(k["status"] for k in karten if k["tmdb_id"] == tmdb_id)


def test_das_hd_abzeichen_zaehlt_keine_reine_4k_kopie(
    admin_client: TestClient, nexcrate: FakeNexcrate, db: Session
) -> None:
    _einrichten(nexcrate)

    assert _status_auf_entdecken(admin_client, db, hd=False, uhd=True) == "not_requested"


def test_ohne_4k_fassung_zaehlt_fuer_das_abzeichen_jede_kopie(
    admin_client: TestClient, nexcrate: FakeNexcrate, db: Session
) -> None:
    _einrichten(nexcrate, _nur_hd(nexcrate))

    assert _status_auf_entdecken(admin_client, db, hd=False, uhd=True) == "in_library"


# --- 2. Startseite: geloeschte Titel verschwinden --------------------------------


def _fertige_anfrage(client: TestClient, tmdb_id: int) -> None:
    kim = create_user(client, "kim")
    with SessionLocal() as sitzung:
        sitzung.add(
            MediaRequest(
                user_id=kim["id"],
                media_type=MediaType.movie,
                fassung_kennung=FILM_HD,
                tmdb_id=tmdb_id,
                title="Erfundener Film",
                status=RequestStatus.downloaded,
            )
        )
        sitzung.commit()


def test_ein_geloeschter_titel_verschwindet_von_der_startseite(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    _einrichten(nexcrate)
    _fertige_anfrage(admin_client, 9301)
    # nexcrate kennt den Titel, fuehrt ihn aber in keiner Fassung mehr.
    nexcrate.film(9301, name="Erfundener Film", versionen=[])

    antwort = admin_client.get("/api/home/recent")

    assert antwort.status_code == 200, antwort.text
    assert antwort.json() == []


def test_ein_vorhandener_titel_bleibt_auf_der_startseite(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    _einrichten(nexcrate)
    _fertige_anfrage(admin_client, 9302)
    nexcrate.film(
        9302,
        name="Erfundener Film",
        versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=9 * GB)],
    )

    antwort = admin_client.get("/api/home/recent")

    assert antwort.status_code == 200, antwort.text
    assert [e["tmdb_id"] for e in antwort.json()] == [9302]


# --- 3. Kontoaufloesung: eine laufende Serie ist nicht offen ---------------------


async def test_die_kontoaufloesung_fuehrt_eine_laufende_serie_als_laufend(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.serie(
        9401,
        name="Erfundene Serie",
        tvdb=94010,
        versionen=[
            nexcrate.fassung(
                SERIE_HD,
                "wanted",
                size_bytes=3 * GB,
                series={"counts": {"have": 2, "aired": 3}},
            )
        ],
        staffeln=[
            {
                "season": 1,
                "name": "Season 1",
                "versions": [
                    {
                        "version_id": SERIE_HD,
                        "state": "wanted",
                        "monitored": True,
                        "counts": {"have": 2, "aired": 3, "expected": 3},
                        "size_bytes": 3 * GB,
                    }
                ],
            }
        ],
    )
    person = _nutzer(db, "sammler")
    db.add(
        MediaRequest(
            user_id=person.id,
            media_type=MediaType.tv,
            fassung_kennung=SERIE_HD,
            tmdb_id=9401,
            tvdb_id=94010,
            season=1,
            title="Erfundene Serie",
            release_date="2020-01-01",
            status=RequestStatus.searching,
        )
    )
    db.commit()

    vorschau = await kontoaufloesung.vorschau(db, nex, person)

    assert [l.title for l in vorschau.laufende] == ["Erfundene Serie"]
    assert vorschau.laufende[0].fassung == SERIE_HD
    assert vorschau.offen == []


# --- 4. Kalender: „Meine" liefert im NEX-Betrieb Eintraege -----------------------


async def test_meine_im_kalender_liefert_folgen_und_filme(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    nexcrate.calendar_items = [
        {
            "kind": "series",
            "ref": "tmdb:9501",
            "name": "Erfundene Serie",
            "year": 2020,
            "date": "2026-02-03",
            "date_kind": "air",
            "monitored": True,
            "versions": [{"version_id": SERIE_HD, "state": "wanted", "monitored": True}],
            "series": {"season": 1, "episode": 3, "name": "Episode 3"},
        },
        {
            "kind": "movie",
            "ref": "tmdb:9502",
            "name": "Erfundener Film",
            "year": 2026,
            "date": "2026-02-10",
            "date_kind": "digital",
            "monitored": True,
            "versions": [{"version_id": FILM_HD, "state": "wanted", "monitored": True}],
        },
    ]

    ergebnis = await calendar_service.kalender(
        db,
        nex,
        von="2026-02-01",
        bis="2026-02-28",
        quellen="mine",
        datumsart="digital",
        schaerfe="sinnvoll",
    )

    arten = {e.media_type for tag in ergebnis.days for e in tag.entries}
    assert arten == {MediaType.tv, MediaType.movie}


# --- 5. Kinder, Vormerkungen, Bewertungen: jede Fassung zaehlt --------------------


def _nur_in_4k(nexcrate: FakeNexcrate, tmdb_id: int) -> None:
    nexcrate.film(
        tmdb_id,
        name="Erfundener Film",
        versionen=[
            nexcrate.fassung(FILM_HD, "wanted"),
            nexcrate.fassung(FILM_UHD, "available", size_bytes=45 * GB),
        ],
    )


async def test_ein_film_nur_in_4k_ist_fuer_kinder_verfuegbar(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    _nur_in_4k(nexcrate, 9601)

    stand = await kids.einordnen(db, nex, "movie", [_titel(9601)])

    assert [i.tmdb_id for i in stand.verfuegbar] == [9601]
    assert stand.wuenschbar == []


async def test_eine_vormerkung_meldet_einen_film_nur_in_4k(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    _nur_in_4k(nexcrate, 9602)
    person = _nutzer(db, "wartende")
    db.add(TitleWatch(user_id=person.id, media_type=MediaType.movie, tmdb_id=9602, title="F"))
    db.commit()

    assert await watch.pruefen(db, nex) == 1
    assert db.scalars(select(TitleWatch)).all() == []


def test_eine_bewertung_bekommt_die_groesse_der_4k_datei(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    _einrichten(nexcrate)
    _nur_in_4k(nexcrate, 9603)
    create_user(admin_client, "kim")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")

    antwort = admin_client.put(
        "/api/feedback/movie/9603", json={"rating": 4, "title": "F"}, headers=kopf
    )

    assert antwort.status_code == 200, antwort.text
    with SessionLocal() as sitzung:
        urteil = sitzung.scalars(select(TitleRating)).one()
        assert urteil.file_size_bytes == 45 * GB


# --- 6. Die 4K-Sperre fragt die HD-Fassung, nicht die Hauptfassung ----------------


async def test_die_4k_sperre_fragt_die_hd_fassung_auch_wenn_4k_die_hauptfassung_ist(
    nexcrate: FakeNexcrate, db: Session
) -> None:
    """Liegt eine 4K-Datei in der HD-Fassung, ist sie keine eigene 4K-Fassung.

    Dieselbe Regel wie im ARR-Betrieb (``echte_uhd_kennungen``). Gefragt wurde
    die Hauptfassung; steht bei nexcrate 4K vorn, fragte die Sperre also 4K
    selbst, fand nichts, und die 4K-Kopie des Medienservers galt als eigene
    Fassung.
    """
    versionen = [dict(v) for v in nexcrate.versions]
    for eintrag in versionen:
        if eintrag["version_id"] == FILM_UHD:
            eintrag["order"] = 1
        elif eintrag["version_id"] == FILM_HD:
            eintrag["order"] = 2
    nex = _einrichten(nexcrate, versionen)
    _im_medienserver(db, 9701, hd=False, uhd=True)
    nexcrate.film(
        9701,
        name="Erfundener Film",
        versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=40 * GB)],
    )
    chefin = _nutzer(db, "chefin", Role.admin)

    anfrage = await requests_service.create_request(
        db, nex, chefin, _titel(9701), quality_profile_id=None, fassung=FILM_UHD
    )

    assert anfrage.fassung_kennung == FILM_UHD
