"""Die Klasse einer nexcrate-Fassung zaehlt: 4K ist auch im NEX-Betrieb 4K.

``fassungen.klasse()`` las bis zum 24.09.2026 nur die vier Arr-Kennungen. Jede
Fassung aus nexcrate hatte damit keine Klasse, und ``stufe()`` fiel auf
``standard``. Gemessen folgte daraus: Ein Film in einer HD- und einer
4K-Fassung ging beim Speicher-Abgleich zweimal an denselben Anfragenden,
``/api/v1`` nannte eine 4K-Fassung ``tier: standard``, die Duplikatpruefung
nahm den HD-Zweig, und der Kontodialog meldete „keine 4K-Instanz".

Dazu zwei Nachbarn derselben Wurzel: ``hauptkennung()`` fiel ohne gelesene
Fassung auf ``radarr-standard`` zurueck, und der Medienserver-Vergleich las
im NEX-Betrieb nur die Hauptfassung.

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
    StorageEntry,
    StorageState,
    User,
)
from app.security import hash_password
from app.services import abgleich, fassungen, kontorechte, requests_service, storage
from app.services.beschaffung import NEX
from app.services.beschaffung.arr.radarr import LibraryEntry as FilmEintrag
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
    SERIE_UHD,
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


@pytest.fixture
def nex(db: Session, nexcrate: FakeNexcrate) -> Any:
    save_settings(db, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY})
    nex_fassungen.schreiben(db, nexcrate.versions)
    db.commit()
    return load_settings(db, frisch=True)


def _nutzer(db: Session, name: str, rolle: Role = Role.user) -> User:
    person = User(username=name, password_hash=hash_password("test"), role=rolle)
    db.add(person)
    db.commit()
    return person


def _film(groesse_gb: float) -> FilmEintrag:
    return FilmEintrag(
        arr_id=1,
        has_file=True,
        monitored=True,
        size_bytes=int(groesse_gb * GB),
        title="Erfundener Film",
    )


def _titel(tmdb_id: int) -> Any:
    from app.schemas_media import MediaItem

    return MediaItem(
        media_type="movie", tmdb_id=tmdb_id, title="Erfundener Film", release_date="2020-01-01"
    )


# --- (i) Die Wurzel -------------------------------------------------------------


def test_klasse_und_stufe_einer_nex_fassung_kommen_aus_der_tabelle(nex: Any) -> None:
    assert fassungen.klasse(FILM_UHD) == "uhd"
    assert fassungen.stufe(FILM_UHD) == "uhd"
    assert fassungen.klasse(FILM_HD) == "hd"
    assert fassungen.stufe(FILM_HD) == "standard"


# --- (ii) Speicher: jeder bekommt seinen Posten ---------------------------------


def test_hd_und_4k_desselben_films_gehen_an_ihre_eigenen_anfragenden(
    nex: Any, db: Session
) -> None:
    """Vorher bekam der erste Anfragende beide Posten, der zweite nichts."""
    hd = _nutzer(db, "hd-freund")
    vierk = _nutzer(db, "vierk-freund")
    for person, kennung in ((hd, FILM_HD), (vierk, FILM_UHD)):
        db.add(
            MediaRequest(
                user_id=person.id,
                media_type=MediaType.movie,
                fassung_kennung=kennung,
                tmdb_id=4711,
                title="Erfundener Film",
                status=RequestStatus.downloaded,
            )
        )
    # Ein Bestandsposten, damit nicht der allererste Lauf alles dem Haus gibt.
    db.add(
        StorageEntry(
            key=f"movie:{FILM_HD}:tmdb:1",
            media_type=MediaType.movie,
            fassung_kennung=FILM_HD,
            tmdb_id=1,
            title="Anderer Film",
            size_bytes=GB,
            state=StorageState.house,
        )
    )
    db.commit()

    gemessen: dict[str, Any] = {}
    storage._film_aufnehmen(gemessen, FILM_HD, 4711, _film(9))
    storage._film_aufnehmen(gemessen, FILM_UHD, 4711, _film(45))
    storage._schreiben(db, gemessen)

    besitzer = {
        zeile.fassung_kennung: zeile.user_id
        for zeile in db.scalars(select(StorageEntry).where(StorageEntry.tmdb_id == 4711))
    }
    assert besitzer == {FILM_HD: hd.id, FILM_UHD: vierk.id}


async def test_der_speicherabgleich_liest_die_fassungen_vorher_aus_der_tabelle(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Nach einer Wiederherstellung ist der Merker leer, die Tabelle nicht.

    Liefe der Abgleich mit leerem Merker, galt jede nexcrate-Fassung als
    ``standard`` und HD und 4K fielen wieder zusammen. Er liest die Tabelle
    deshalb selbst, ohne nexcrate zu fragen.
    """
    hd = _nutzer(db, "hd-freund")
    vierk = _nutzer(db, "vierk-freund")
    for person, kennung in ((hd, FILM_HD), (vierk, FILM_UHD)):
        db.add(
            MediaRequest(
                user_id=person.id,
                media_type=MediaType.movie,
                fassung_kennung=kennung,
                tmdb_id=4711,
                title="Erfundener Film",
                status=RequestStatus.downloaded,
            )
        )
    db.add(
        StorageEntry(
            key=f"movie:{FILM_HD}:tmdb:1",
            media_type=MediaType.movie,
            fassung_kennung=FILM_HD,
            tmdb_id=1,
            title="Anderer Film",
            size_bytes=GB,
            state=StorageState.house,
        )
    )
    db.commit()
    nexcrate.film(
        4711,
        name="Erfundener Film",
        versionen=[
            nexcrate.fassung(FILM_HD, "available", size_bytes=9 * GB),
            nexcrate.fassung(FILM_UHD, "available", size_bytes=45 * GB),
        ],
    )
    # So steht es nach ``nach_wiederherstellung``: gemerkt ist nichts mehr.
    nex_fassungen.vergessen()
    aufrufe_vorher = len(nexcrate.calls)

    await storage.abgleichen(db, load_settings(db, frisch=True))

    besitzer = {
        zeile.fassung_kennung: zeile.user_id
        for zeile in db.scalars(select(StorageEntry).where(StorageEntry.tmdb_id == 4711))
    }
    assert besitzer == {FILM_HD: hd.id, FILM_UHD: vierk.id}
    # Gelesen wurde die Tabelle, nicht ``/versions``.
    assert not any(k[1].endswith("/versions") for k in nexcrate.calls[aufrufe_vorher:])


# --- (iii) /api/v1 nennt 4K als 4K ------------------------------------------------


def test_v1_nennt_eine_nex_4k_fassung_uhd(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    with SessionLocal() as sitzung:
        save_settings(
            sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY}
        )
        nex_fassungen.schreiben(sitzung, nexcrate.versions)
        sitzung.commit()
    kim = create_user(admin_client, "kim")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    with SessionLocal() as sitzung:
        sitzung.add(
            MediaRequest(
                user_id=kim["id"],
                media_type=MediaType.movie,
                fassung_kennung=FILM_UHD,
                tmdb_id=4711,
                title="Erfundener Film",
                status=RequestStatus.downloaded,
            )
        )
        sitzung.add(
            StorageEntry(
                key=f"movie:{FILM_UHD}:tmdb:4711",
                user_id=kim["id"],
                media_type=MediaType.movie,
                fassung_kennung=FILM_UHD,
                tmdb_id=4711,
                title="Erfundener Film",
                size_bytes=45 * GB,
                state=StorageState.owned,
            )
        )
        sitzung.commit()

    speicher = admin_client.get("/api/v1/storage/me", headers=kopf)
    anfragen = admin_client.get("/api/v1/requests/mine", headers=kopf)

    assert speicher.status_code == 200, speicher.text
    assert anfragen.status_code == 200, anfragen.text
    assert [p["tier"] for p in speicher.json()["entries"]] == ["uhd"]
    assert [a["tier"] for a in anfragen.json()] == ["uhd"]


# --- (iv) Ohne gelesene Fassung gibt es keine Arr-Kennung ------------------------


def _nex_ohne_fassungen(zugang: bool = True) -> None:
    with SessionLocal() as sitzung:
        werte: dict[str, Any] = {"beschaffung": NEX}
        if zugang:
            werte |= {"nexcrate_url": URL, "nexcrate_api_key": KEY}
        save_settings(sitzung, werte)
        load_settings(sitzung, frisch=True)
    nex_fassungen.vergessen()


def test_die_hauptkennung_ist_im_nex_betrieb_ohne_fassung_keine_arr_kennung(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    """Vorher: ``radarr-standard``, obwohl der Betrieb ``nex`` ist."""
    _nex_ohne_fassungen()

    assert fassungen.hauptkennung("movie") is None
    assert fassungen.hauptkennung("tv") is None

    # Und eine Anfrage ohne Fassungsangabe endet in der Absage mit Kennung,
    # nicht in einer Anfrage auf eine Arr-Fassung.
    create_user(admin_client, "kim")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    tmdb_id = admin_client.get("/api/discover/movie").json()["items"][0]["tmdb_id"]
    antwort = admin_client.post(
        "/api/requests", json={"media_type": "movie", "tmdb_id": tmdb_id}, headers=kopf
    )
    assert antwort.status_code == 409, antwort.text
    assert antwort.json()["detail"]["code"] == "nexcrate_no_version_for_kind"
    with SessionLocal() as sitzung:
        assert sitzung.query(MediaRequest).count() == 0


def _serie_mit_staffeln(monkeypatch: pytest.MonkeyPatch, tmdb_id: int) -> None:
    """Eine erfundene Serie mit zwei Staffeln zu drei Folgen - die Demo-Daten haben keine."""
    from app.routers import details as details_router
    from app.schemas_media import EpisodeInfo, MediaDetail, SeasonDetail, SeasonInfo

    detail = MediaDetail(
        tmdb_id=tmdb_id,
        media_type="tv",
        title="Erfundene Serie",
        seasons=[
            SeasonInfo(season_number=1, name="Staffel 1", episode_count=3),
            SeasonInfo(season_number=2, name="Staffel 2", episode_count=3),
        ],
    )

    async def _detail(_db, _settings, _art, _tmdb_id, **_rest):
        return detail.model_copy(deep=True)

    async def _staffel(_db, _settings, _tmdb_id, nummer, **_rest):
        return SeasonDetail(
            season_number=nummer,
            name=f"Staffel {nummer}",
            episodes=[EpisodeInfo(episode_number=n, name=f"Folge {n}") for n in (1, 2, 3)],
        )

    monkeypatch.setattr(details_router.media, "full_detail", _detail)
    monkeypatch.setattr(details_router.media, "detail", _detail)
    monkeypatch.setattr(details_router.media, "season_detail", _staffel)


def test_karten_und_staffeln_bieten_ohne_fassung_keine_arr_fassung_an(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Hauptachse jeder Karte und jeder Staffel hing an ``hauptkennung``."""
    _nex_ohne_fassungen()
    arr = set(fassungen.ARR_KENNUNGEN)

    karten = admin_client.get("/api/discover/movie")
    assert karten.status_code == 200, karten.text
    genannt = {a["kennung"] for k in karten.json()["items"] for a in k.get("fassungen") or []}
    assert not genannt & arr

    serie = 456125
    _serie_mit_staffeln(monkeypatch, serie)
    detail = admin_client.get(f"/api/detail/tv/{serie}")
    assert detail.status_code == 200, detail.text
    staffeln = detail.json().get("seasons") or []
    assert staffeln, "Die Demo-Serie braucht Staffeln, sonst prueft das nichts"
    genannt = {f["kennung"] for s in staffeln for f in s.get("fassungen") or []}
    assert not genannt & arr

    nummer = staffeln[0]["season_number"]
    folgen = admin_client.get(f"/api/detail/tv/{serie}/season/{nummer}")
    assert folgen.status_code == 200, folgen.text
    genannt = {
        f["kennung"] for e in folgen.json().get("episodes") or [] for f in e.get("fassungen") or []
    }
    assert not genannt & arr


# --- (v) Kontorechte: eine nexcrate-4K-Fassung ist eine 4K-Fassung ----------------


def test_eine_nex_4k_fassung_ist_keine_fehlende_4k_instanz(nex: Any) -> None:
    bewertung = kontorechte.bewerten(
        nex,
        kontorechte.Wunsch(rolle=Role.user),
        hausordnung_veroeffentlicht=False,
        offene=frozenset(),
    )

    gruende = {
        bewertung.can_request_uhd_movies.grund,
        bewertung.can_request_uhd_series.grund,
        bewertung.auto_approve_uhd.grund,
    }
    assert not gruende & {
        kontorechte.KEINE_4K_INSTANZ,
        kontorechte.KEINE_4K_INSTANZ_FILME,
        kontorechte.KEINE_4K_INSTANZ_SERIEN,
    }
    assert bewertung.can_request_uhd_movies.frei


def test_ohne_nex_4k_fassung_bleibt_der_grund(
    db: Session, nexcrate: FakeNexcrate
) -> None:
    """Die Gegenprobe: Ohne Fassung der Klasse ``uhd`` gibt es wirklich kein 4K."""
    save_settings(db, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY})
    nex_fassungen.schreiben(db, [v for v in nexcrate.versions if v["tier"] != "uhd"])
    db.commit()

    bewertung = kontorechte.bewerten(
        load_settings(db, frisch=True),
        kontorechte.Wunsch(rolle=Role.user),
        hausordnung_veroeffentlicht=False,
        offene=frozenset(),
    )

    assert bewertung.can_request_uhd_movies.grund == kontorechte.KEINE_4K_INSTANZ_FILME
    assert bewertung.auto_approve_uhd.grund == kontorechte.KEINE_4K_INSTANZ


# --- (vi) Die Duplikatpruefung nimmt fuer 4K den 4K-Zweig ------------------------


def _im_medienserver_nur_hd(db: Session, tmdb_id: int) -> None:
    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.movie,
            guid=f"plex://movie/{tmdb_id}",
            tmdb_id=tmdb_id,
            title="Erfundener Film",
            title_key="erfundener film",
            year=2020,
            has_standard=True,
            has_uhd=False,
        )
    )
    db.commit()


async def test_eine_hd_kopie_im_medienserver_sperrt_die_4k_anfrage_nicht(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Vorher zaehlte fuer die nexcrate-4K-Fassung jede Kopie, also auch die HD-Datei."""
    _im_medienserver_nur_hd(db, 9101)
    # nexcrate kennt den Titel, fuehrt ihn aber in keiner Fassung.
    nexcrate.film(9101, name="Erfundener Film", versionen=[])
    chefin = _nutzer(db, "chefin", Role.admin)

    anfrage = await requests_service.create_request(
        db, nex, chefin, _titel(9101), quality_profile_id=None, fassung=FILM_UHD
    )

    assert anfrage.fassung_kennung == FILM_UHD


async def test_dieselbe_hd_kopie_sperrt_die_hd_anfrage_weiter(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Gegenprobe: In HD liegt der Film ja wirklich schon."""
    _im_medienserver_nur_hd(db, 9101)
    nexcrate.film(9101, name="Erfundener Film", versionen=[])
    chefin = _nutzer(db, "chefin", Role.admin)

    with pytest.raises(requests_service.RequestError) as gefangen:
        await requests_service.create_request(
            db, nex, chefin, _titel(9101), quality_profile_id=None, fassung=FILM_HD
        )

    assert gefangen.value.code == "already_on_media_server"


# --- Medienserver-Vergleich: jede Fassung, nicht nur die Hauptfassung -------------


async def test_der_medienserver_vergleich_liest_jede_nex_fassung(
    nex: Any, nexcrate: FakeNexcrate
) -> None:
    """Ein Film, den nexcrate nur in 4K hat, fehlte dem Vergleich ganz."""
    nexcrate.film(
        4712,
        name="Nur in 4K",
        versionen=[nexcrate.fassung(FILM_UHD, "available", size_bytes=45 * GB)],
    )
    nexcrate.serie(
        4713,
        name="Erfundene Serie",
        versionen=[nexcrate.fassung(SERIE_HD, "available", size_bytes=3 * GB)],
    )

    filme, _serien, _titel_je = await abgleich._arr_bestand(nex)

    assert 4712 in filme


# --- Bibliotheksprüfung beim Anfragen: in der angefragten Fassung ----------------


async def test_ein_film_in_hd_sperrt_die_4k_anfrage_nicht(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Vorher fragte die Prüfung die Hauptfassung: 409 ``already_in_library`` für 4K."""
    nexcrate.film(
        9101,
        name="Erfundener Film",
        versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=9 * GB)],
    )
    chefin = _nutzer(db, "chefin", Role.admin)

    anfrage = await requests_service.create_request(
        db, nex, chefin, _titel(9101), quality_profile_id=None, fassung=FILM_UHD
    )

    assert anfrage.fassung_kennung == FILM_UHD


async def test_ein_film_in_hd_sperrt_die_hd_anfrage_weiter(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Gegenprobe: In HD liegt der Film ja wirklich schon."""
    nexcrate.film(
        9101,
        name="Erfundener Film",
        versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=9 * GB)],
    )
    chefin = _nutzer(db, "chefin", Role.admin)

    with pytest.raises(requests_service.RequestError) as gefangen:
        await requests_service.create_request(
            db, nex, chefin, _titel(9101), quality_profile_id=None, fassung=FILM_HD
        )

    assert (gefangen.value.status_code, gefangen.value.code) == (409, "already_in_library")


async def test_ein_film_in_4k_sperrt_die_4k_anfrage(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Vorher sah die Prüfung nur HD, fand nichts und ließ die 4K-Anfrage ein zweites Mal durch."""
    nexcrate.film(
        9101,
        name="Erfundener Film",
        versionen=[nexcrate.fassung(FILM_UHD, "available", size_bytes=45 * GB)],
    )
    chefin = _nutzer(db, "chefin", Role.admin)

    with pytest.raises(requests_service.RequestError) as gefangen:
        await requests_service.create_request(
            db, nex, chefin, _titel(9101), quality_profile_id=None, fassung=FILM_UHD
        )

    assert (gefangen.value.status_code, gefangen.value.code) == (409, "already_in_library")


def _serientitel(tmdb_id: int) -> Any:
    from app.schemas_media import MediaItem

    return MediaItem(
        media_type="tv", tmdb_id=tmdb_id, title="Erfundene Serie", release_date="2020-01-01"
    )


def _serie_in(nexcrate: FakeNexcrate, fassung: str) -> None:
    nexcrate.serie(
        4713,
        name="Erfundene Serie",
        versionen=[
            nexcrate.fassung(
                fassung, "available", size_bytes=3 * GB, series={"counts": {"have": 3, "aired": 3}}
            )
        ],
    )


async def test_eine_serie_in_hd_sperrt_die_4k_anfrage_der_ganzen_serie_nicht(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Dieselbe Regel wie beim Film, für eine Anfrage ohne Staffel."""
    _serie_in(nexcrate, SERIE_HD)
    chefin = _nutzer(db, "chefin", Role.admin)

    anfrage = await requests_service.create_request(
        db, nex, chefin, _serientitel(4713), quality_profile_id=None, fassung=SERIE_UHD
    )

    assert anfrage.fassung_kennung == SERIE_UHD


async def test_eine_serie_in_4k_sperrt_die_4k_anfrage_der_ganzen_serie(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Die Gegenprobe: In 4K liegt die Serie wirklich schon."""
    _serie_in(nexcrate, SERIE_UHD)
    chefin = _nutzer(db, "chefin", Role.admin)

    with pytest.raises(requests_service.RequestError) as gefangen:
        await requests_service.create_request(
            db, nex, chefin, _serientitel(4713), quality_profile_id=None, fassung=SERIE_UHD
        )

    assert (gefangen.value.status_code, gefangen.value.code) == (409, "already_in_library")
