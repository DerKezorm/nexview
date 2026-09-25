"""Nachweise im NEX-Betrieb: Mitteilungen, Geisterposten, Kontingente.

Jede Pruefung hier gibt es fuer den ARR-Betrieb schon (Vorbild steht am
Test). Hier laeuft sie gegen ``tests/beschaffung/fake_nexcrate.py`` mit dem
echten Client, ohne Umweg ueber eine Methodenattrappe. Titel und Konten sind
erfunden.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    DownloadHaenger,
    DownloadVerlauf,
    Fassung,
    MediaRequest,
    MediaServerLibraryItem,
    MediaType,
    Notification,
    NotificationType,
    RequestStatus,
    Role,
    StorageEntry,
    StorageState,
    TitleWatch,
    User,
)
from app.security import hash_password
from app.services import befunde, download_automatik, storage, watch
from app.services.beschaffung import NEX, get_beschaffung
from app.services.beschaffung.nex import bestand as nex_bestand
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, KEY, URL, FakeNexcrate
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


def _einrichten(nexcrate: FakeNexcrate) -> Any:
    with SessionLocal() as sitzung:
        save_settings(
            sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY}
        )
        nex_fassungen.schreiben(sitzung, nexcrate.versions)
        sitzung.commit()
        return load_settings(sitzung, frisch=True)


@pytest.fixture
def nex(nexcrate: FakeNexcrate) -> Any:
    return _einrichten(nexcrate)


def _konto(db: Session, name: str, rolle: Role = Role.user) -> User:
    person = User(username=name, password_hash=hash_password("test"), role=rolle)
    db.add(person)
    db.commit()
    return person


def _meldungen(db: Session, art: NotificationType) -> list[Notification]:
    db.expire_all()
    return list(
        db.scalars(select(Notification).where(Notification.type == art).order_by(Notification.id))
    )


# --- A1: Gesundheit meldet sich bei den Administratoren ----------------------------
# Vorbild: tests/test_instanz_gesundheit.py::test_neues_problem_meldet_genau_einmal


async def test_ein_neuer_befund_von_nexcrate_meldet_sich_einmal_je_admin(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    chef = _konto(db, "chefin", Role.admin)
    _konto(db, "gast")
    nexcrate.health = [
        {"code": "indexer_none", "level": "error", "message": "No indexer.", "params": {}}
    ]
    weg = get_beschaffung(nex)

    await weg.gesundheit_pruefen(db)
    meldungen = _meldungen(db, NotificationType.instanz_gesundheit)
    assert [(m.user_id, m.message_key) for m in meldungen] == [
        (chef.id, "nexcrate.health.indexer_none")
    ]

    # Zweite Runde, derselbe Befund: keine Flut.
    await weg.gesundheit_pruefen(db)
    assert len(_meldungen(db, NotificationType.instanz_gesundheit)) == 1

    # Verschwindet er und kommt wieder, ist er wieder eine Meldung wert.
    nexcrate.health = []
    await weg.gesundheit_pruefen(db)
    nexcrate.health = [
        {"code": "indexer_none", "level": "error", "message": "No indexer.", "params": {}}
    ]
    await weg.gesundheit_pruefen(db)
    assert len(_meldungen(db, NotificationType.instanz_gesundheit)) == 2


# --- A2: Ein immer wieder haengender Download meldet sich ------------------------
# Vorbild: tests/test_download_automatik.py::
#   test_ein_titel_der_immer_wieder_haengt_meldet_sich_auch_ohne_automatik


def _problem(download_id: int, tmdb_id: int = 603) -> dict[str, Any]:
    return {
        "download_id": download_id,
        "title": {"kind": "movie", "ref": f"tmdb:{tmdb_id}", "name": "Erfundener Film"},
        "state": "problem",
        "size_bytes": 10,
        "problem": {
            "code": "no_video",
            "needs_owner": True,
            "message": "No video in the download.",
            "params": {},
            "actions": ["remove_and_search", "remove"],
            "automatic": ["remove_and_search"],
        },
    }


def _erkannt_vorher(db: Session, anzahl: int, *, vor: timedelta) -> None:
    for _ in range(anzahl):
        db.add(
            DownloadVerlauf(
                kennung="nexcrate", arr_id=603, media_type="movie", was="erkannt",
                automatisch=True, am=download_automatik._jetzt() - vor,
            )
        )
    db.commit()


@pytest.mark.parametrize(("erkannt", "gemeldet"), [(1, False), (2, True)])
async def test_im_nex_betrieb_meldet_die_automatik_einen_wiederholten_haenger(
    nex: Any, nexcrate: FakeNexcrate, db: Session, erkannt: int, gemeldet: bool
) -> None:
    """Nexview handelt im NEX-Betrieb nie selbst (``downloads_selbst``), melden
    soll es trotzdem. Vorher stehen ``erkannt`` Eintraege im Verlauf, der
    Rundgang schreibt den dritten (``WIEDERHOLT_AB``) selbst dazu."""
    _konto(db, "chefin", Role.admin)
    _erkannt_vorher(db, erkannt, vor=timedelta(days=2))
    nexcrate.queue = [_problem(7)]
    await get_beschaffung(nex).downloads_auffrischen(db)
    download_automatik.schreiben(db, an=True, regeln={"no_video": "entfernen_neu_suchen"})

    assert await download_automatik.ausfuehren(db, nex) == 0
    await download_automatik.ausfuehren(db, nex)

    meldungen = _meldungen(db, NotificationType.download_stuck)
    erwartet = [("notifications.downloadStuck", "Erfundener Film")] if gemeldet else []
    assert [(m.message_key, m.message_title) for m in meldungen] == erwartet
    # Und Nexview hat nexcrate nichts aufgetragen.
    assert not [k for k in nexcrate.calls if "/downloads/" in k[1]]


async def test_im_nex_betrieb_zaehlt_der_rundgang_jeden_neuen_haenger(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    """Der echte Weg: derselbe Film haengt dreimal mit je einem neuen Download.

    nexcrate ersetzt den kaputten Download selbst, und der naechste haengt
    wieder. Genau das soll sich laut ``download_automatik`` melden.
    """
    _konto(db, "chefin", Role.admin)
    weg = get_beschaffung(nex)
    for download_id in (7, 8, 9):
        nexcrate.queue = [_problem(download_id)]
        await weg.downloads_auffrischen(db)
        await download_automatik.ausfuehren(db, nex)

    assert db.query(DownloadHaenger).count() == 1
    meldungen = _meldungen(db, NotificationType.download_stuck)
    assert [m.message_key for m in meldungen] == ["notifications.downloadStuck"]


# --- A3: Ein vorgemerkter Film, den nexcrate mit Datei fuehrt -----------------------
# Vorbild: tests/test_watch.py (dort ueber einen nachgebauten Weg)


async def test_eine_vormerkung_meldet_den_film_aus_nexcrate(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    person = _konto(db, "wartende")
    db.add(
        TitleWatch(
            user_id=person.id, media_type=MediaType.movie, tmdb_id=9701, title="Erfundener Film"
        )
    )
    db.commit()

    # Erst ohne Datei: nichts zu melden, die Vormerkung bleibt.
    nexcrate.film(9701, name="Erfundener Film", versionen=[nexcrate.fassung(FILM_HD, "wanted")])
    assert await watch.pruefen(db, nex) == 0
    assert len(db.scalars(select(TitleWatch)).all()) == 1

    nexcrate.film(
        9701,
        name="Erfundener Film",
        versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=6 * GB)],
    )
    nex_bestand.verwerfen()
    assert await watch.pruefen(db, nex) == 1

    meldungen = _meldungen(db, NotificationType.watch_ready)
    assert [(m.user_id, m.message_key, m.message_title) for m in meldungen] == [
        (person.id, "notifications.watchReady", "Erfundener Film")
    ]
    assert db.scalars(select(TitleWatch)).all() == []


# --- A4 und B: Speicher --------------------------------------------------------------
# Vorbilder: tests/test_storage.py::test_aufwertung_meldet_sich_beim_betroffenen,
# tests/test_befunde.py::test_geisterposten_wird_gemeldet


def _film_mit_datei(nexcrate: FakeNexcrate, tmdb_id: int, groesse: int) -> None:
    nexcrate.film(
        tmdb_id,
        name=f"Erfundener Film {tmdb_id}",
        versionen=[nexcrate.fassung(FILM_HD, "available", size_bytes=groesse)],
    )


async def _besessener_posten(nex: Any, nexcrate: FakeNexcrate, db: Session) -> User:
    """Ein Posten fuer Film 603, der einem Konto gehoert.

    Der erste Lauf schreibt alles dem Haus zu; deshalb liegt vorher schon ein
    anderer Film da, und die Anfrage kommt erst danach.
    """
    _film_mit_datei(nexcrate, 1, 2 * GB)
    await storage.abgleichen(db, nex)
    person = _konto(db, "speicherkonto")
    db.add(
        MediaRequest(
            user_id=person.id,
            media_type=MediaType.movie,
            tmdb_id=603,
            title="Erfundener Film 603",
            fassung_kennung=FILM_HD,
            status=RequestStatus.downloaded,
            arr_id=603,
        )
    )
    db.commit()
    _film_mit_datei(nexcrate, 603, 5 * GB)
    await storage.abgleichen(db, nex)
    posten = db.scalar(
        select(StorageEntry).where(
            StorageEntry.key == storage.schluessel(MediaType.movie, FILM_HD, tmdb_id=603)
        )
    )
    assert posten is not None and posten.user_id == person.id
    assert posten.state == StorageState.owned and posten.arr_managed is True
    return person


async def test_ein_spuerbar_gewachsener_posten_meldet_sich_beim_besitzer(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    person = await _besessener_posten(nex, nexcrate, db)
    assert _meldungen(db, NotificationType.storage_grew) == []

    _film_mit_datei(nexcrate, 603, 5 * GB + storage.MELDESCHWELLE)
    ergebnis = await storage.abgleichen(db, nex)

    assert ergebnis.gewachsen == 1
    meldungen = _meldungen(db, NotificationType.storage_grew)
    assert [(m.user_id, m.message_title) for m in meldungen] == [
        (person.id, "Erfundener Film 603")
    ]


async def test_ein_posten_den_nexcrate_nicht_mehr_fuehrt_wird_zum_geisterposten(
    nex: Any, nexcrate: FakeNexcrate, db: Session
) -> None:
    await _besessener_posten(nex, nexcrate, db)
    assert [b for b in befunde.sammeln(db, nex) if b.kennung == "bibliothek.geisterposten"] == []

    # Der Betreiber wirft den Film aus nexcrate, die Datei liegt weiter im Medienserver.
    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.movie,
            guid="plex://movie/603",
            tmdb_id=603,
            title="Erfundener Film 603",
            title_key="erfundener film 603",
            has_standard=True,
            size_standard=5 * GB,
        )
    )
    db.commit()
    nexcrate.entfernt("movie", "tmdb:603")
    nex_bestand.verwerfen()
    await storage.abgleichen(db, nex)

    db.expire_all()
    posten = db.scalar(select(StorageEntry).where(StorageEntry.tmdb_id == 603))
    assert posten is not None
    assert posten.arr_managed is False and posten.state == StorageState.owned
    treffer = [b for b in befunde.sammeln(db, nex) if b.kennung == "bibliothek.geisterposten"]
    assert len(treffer) == 1
    assert treffer[0].werte == {"anzahl": 1, "bytes": 5 * GB}


# --- C: Stueck-Kontingent ueber die echten Adressen ----------------------------------
# Vorbilder: tests/test_requests.py (Kontingent, Ablehnung, Zurueckziehen),
# tests/test_cancel.py::test_abbrechen_gibt_das_kontingent_zurueck


def _kontingent_einrichten(
    admin_client: TestClient, nexcrate: FakeNexcrate, *, limit: int, sofort: bool
) -> tuple[dict[str, str], list[int]]:
    """NEX-Betrieb, HD offen fuer alle, ein Konto mit Filmkontingent.

    ``sofort``: Die Anfrage gilt gleich als freigegeben und geht an nexcrate.
    Zurueck kommen die Kopfzeilen des Kontos und drei Film-Nummern, die
    nexcrate kennt.
    """
    _einrichten(nexcrate)
    with SessionLocal() as sitzung:
        zeile = sitzung.get(Fassung, FILM_HD)
        assert zeile is not None
        zeile.offen_fuer_alle = True
        sitzung.commit()
    create_user(admin_client, "kim", quota_movies_limit=limit, auto_approve=sofort)
    headers = auth_headers(admin_client, "kim", "passwort-1234")
    karten = admin_client.get("/api/discover/movie").json()["items"][:3]
    for karte in karten:
        nexcrate.film(karte["tmdb_id"], name=karte["title"], versionen=[])
    nex_bestand.verwerfen()
    return headers, [karte["tmdb_id"] for karte in karten]


def _anfragen(client: TestClient, tmdb_id: int, headers: dict[str, str]) -> Any:
    return client.post(
        "/api/requests", json={"media_type": "movie", "tmdb_id": tmdb_id}, headers=headers
    )


def _an_nexcrate(nexcrate: FakeNexcrate) -> list[str]:
    return [k[3]["ref"] for k in nexcrate.calls if k[0] == "POST" and k[1].endswith("/requests")]


def _stand(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    return client.get("/api/requests/quota", headers=headers).json()["movie"]


def test_im_nex_betrieb_wird_das_kontingent_aufgebraucht(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    headers, filme = _kontingent_einrichten(admin_client, nexcrate, limit=2, sofort=True)

    for tmdb_id in filme[:2]:
        antwort = _anfragen(admin_client, tmdb_id, headers)
        assert antwort.status_code == 201, antwort.text
    dritte = _anfragen(admin_client, filme[2], headers)

    assert dritte.status_code == 429, dritte.text
    # Die beiden erlaubten gingen wirklich an nexcrate, die dritte nicht.
    assert _an_nexcrate(nexcrate) == [f"tmdb:{n}" for n in filme[:2]]
    stand = _stand(admin_client, headers)
    assert (stand["used"], stand["remaining"], stand["exhausted"]) == (2, 0, True)


def test_im_nex_betrieb_zaehlt_eine_abgelehnte_anfrage_nicht(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    headers, filme = _kontingent_einrichten(admin_client, nexcrate, limit=1, sofort=False)
    angelegt = _anfragen(admin_client, filme[0], headers)
    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["status"] == "pending_approval"
    assert _stand(admin_client, headers)["used"] == 1

    abgelehnt = admin_client.post(
        f"/api/admin/requests/{angelegt.json()['id']}/reject", json={"reason": "Nein"}
    )
    assert abgelehnt.status_code == 200, abgelehnt.text

    assert _stand(admin_client, headers)["used"] == 0
    assert _anfragen(admin_client, filme[1], headers).status_code == 201


def test_im_nex_betrieb_gibt_abbrechen_das_kontingent_zurueck(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    headers, filme = _kontingent_einrichten(admin_client, nexcrate, limit=1, sofort=True)
    angelegt = _anfragen(admin_client, filme[0], headers)
    assert angelegt.status_code == 201, angelegt.text
    assert _an_nexcrate(nexcrate) == [f"tmdb:{filme[0]}"]
    # Wie nexcrate: Die Fassung haengt danach mit Nexviews Marke am Titel.
    nexcrate.film(
        filme[0],
        versionen=[
            nexcrate.fassung(FILM_HD, "wanted", origin=f"nexview:request:{angelegt.json()['id']}")
        ],
    )
    nex_bestand.verwerfen()
    stand = _stand(admin_client, headers)
    assert (stand["used"], stand["exhausted"]) == (1, True)

    abgebrochen = admin_client.post(
        f"/api/requests/{angelegt.json()['id']}/cancel", headers=headers
    )

    assert abgebrochen.status_code == 200, abgebrochen.text
    assert abgebrochen.json()["status"] == "cancelled"
    # Zurueckgenommen wurde bei nexcrate, nicht nur in Nexview.
    assert [k for k in nexcrate.calls if k[1].endswith(f"tmdb:{filme[0]}/withdraw")]
    stand = _stand(admin_client, headers)
    assert (stand["used"], stand["exhausted"]) == (0, False)


def test_im_nex_betrieb_gibt_zurueckziehen_das_kontingent_zurueck(
    admin_client: TestClient, nexcrate: FakeNexcrate
) -> None:
    headers, filme = _kontingent_einrichten(admin_client, nexcrate, limit=1, sofort=False)
    angelegt = _anfragen(admin_client, filme[0], headers)
    assert angelegt.status_code == 201, angelegt.text
    assert _stand(admin_client, headers)["exhausted"] is True
    assert _anfragen(admin_client, filme[1], headers).status_code == 429

    geloescht = admin_client.delete(f"/api/requests/{angelegt.json()['id']}", headers=headers)

    assert geloescht.status_code == 204, geloescht.text
    assert admin_client.get("/api/requests/mine", headers=headers).json() == []
    stand = _stand(admin_client, headers)
    assert (stand["used"], stand["exhausted"]) == (0, False)
    assert _anfragen(admin_client, filme[1], headers).status_code == 201
