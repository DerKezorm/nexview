"""Der Medienserver misst weiter, aber er legt keinen Posten an.

⚠️ **Gefunden an einer echten Anlage (23.09.2026): 81 Posten ohne Pfad, 678 GiB,
alle dem Haus.** 66 davon waren byte-gleich mit einer Datei, die Radarr schon
meldete: Jellyfin ordnet Filme ueber den Titel zu und liegt dabei daneben. Aus
dem Ordner ``Blow (2001)`` wurde "Blow Out", aus ``Paris (2008)`` wurde "Paris,
Texas". Nexview legte fuer jede solche Meldung einen eigenen Posten an und
zaehlte dieselbe Datei zweimal.

Die Regel seitdem: **Was ein Beschaffungsweg einmal gemeldet hat, darf der
Medienserver weitermessen**, wenn es dort verschwindet. Damit wird niemand seine
Belastung los, indem er den Titel in Radarr oder nexcrate loescht. Einen
**neuen** Posten legt der Medienserver nie an; was keinem Posten entspricht,
wird gemeldet statt gebucht.

Dazu gehoert der NEX-Betrieb: Dort stand im Zweig fest ``radarr-standard``, und
jeder Posten, den der Medienserver weitermessen sollte, wurde unter einer
Fassung gesucht, die es dort gar nicht gibt.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_modul
from app.db import SessionLocal
from app.models import (
    Fassung,
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
from app.services import storage
from app.services.beschaffung.arr.radarr import LibraryEntry as MovieEntry
from app.services.fassungen import arr_kennung

GB = 1024**3

NEX_HD = "v_hd000001"
NEX_UHD = "v_uhd00001"


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as sitzung:
        yield sitzung


def film(size_gb: float, *, titel: str = "Ein Film", pfad: str = "") -> MovieEntry:
    return MovieEntry(
        arr_id=1,
        has_file=True,
        monitored=True,
        size_bytes=int(size_gb * GB),
        title=titel,
        path=pfad,
    )


def im_server(
    db: Session,
    tmdb_id: int,
    *,
    standard_gb: float = 0,
    uhd_gb: float = 0,
    anbieter: str = "plex",
) -> None:
    db.add(
        MediaServerLibraryItem(
            provider=anbieter,
            media_type=MediaType.movie,
            guid=f"{anbieter}://movie/{tmdb_id}",
            tmdb_id=tmdb_id,
            title=f"Film {tmdb_id}",
            title_key=f"film{tmdb_id}",
            size_standard=int(standard_gb * GB),
            size_uhd=int(uhd_gb * GB),
        )
    )
    db.commit()


def nex_fassungen(db: Session) -> None:
    for kennung, klasse, reihe in ((NEX_HD, "hd", 0), (NEX_UHD, "uhd", 1)):
        db.add(
            Fassung(
                kennung=kennung,
                media_type="movie",
                name=kennung,
                klasse=klasse,
                reihenfolge=reihe,
                quelle="nex",
                aktiv=True,
            )
        )
    db.commit()


def abgleich(db: Session, filme: dict[str, dict[int, MovieEntry]]) -> storage.Ergebnis:
    """Wie ``storage.abgleichen``, ab der Grenze: aufnehmen, Medienserver, schreiben."""
    gemessen: dict[str, storage._Gemessen] = {}
    for fassung, eintraege in filme.items():
        for tmdb_id, eintrag in eintraege.items():
            storage._film_aufnehmen(gemessen, fassung, tmdb_id, eintrag)
    storage._aus_media_server(db, gemessen)
    return storage._schreiben(db, gemessen)


def posten(db: Session) -> dict[str, StorageEntry]:
    return {zeile.key: zeile for zeile in db.scalars(select(StorageEntry))}


# ------------------------------------------------------------ keine neuen


def test_der_medienserver_legt_keinen_neuen_posten_an(db: Session) -> None:
    """Der gemessene Fall: Jellyfin meldet einen Film, den Radarr nicht fuehrt.

    Bis hierher entstand daraus ein Posten des Hauses, ohne Pfad und ohne
    Verwaltung - bei der echten Anlage 81 Stueck.
    """
    radarr = arr_kennung(MediaType.movie, "standard")
    # Blow (2001): Radarr kennt ihn richtig unter 4133.
    im_server(db, 11644, standard_gb=9.1, anbieter="jellyfin")  # "Blow Out", geraten

    abgleich(db, {radarr: {4133: film(9.1, pfad="/data/Blow (2001)")}})

    assert set(posten(db)) == {f"movie:{radarr}:tmdb:4133"}


def test_was_keinem_posten_entspricht_wird_gemeldet(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Nicht gebucht heisst nicht verschwiegen: Das Protokoll nennt die Zahl."""
    radarr = arr_kennung(MediaType.movie, "standard")
    im_server(db, 11644, standard_gb=9.1, anbieter="jellyfin")
    im_server(db, 655, standard_gb=4.0, anbieter="jellyfin")

    with caplog.at_level(logging.INFO, logger=storage.logger.name):
        abgleich(db, {radarr: {4133: film(9.1, pfad="/data/Blow (2001)")}})

    assert any(
        "2 movie(s)" in eintrag.getMessage() and "not booked" in eintrag.getMessage()
        for eintrag in caplog.records
    ), [eintrag.getMessage() for eintrag in caplog.records]


# ---------------------------------------------------------- weitermessen


def test_im_nex_betrieb_misst_der_medienserver_den_vorhandenen_posten_weiter(
    db: Session,
) -> None:
    """Der Schutz gilt auch dort: aus nexcrate geworfen, Datei behalten.

    Vorher suchte der Zweig unter ``radarr-standard`` - der Posten der
    nexcrate-Fassung fiel weg, und daneben entstand ein neuer unter einer
    Fassung, die es im NEX-Betrieb nicht gibt.
    """
    nex_fassungen(db)
    abgleich(db, {NEX_HD: {603: film(8)}})
    abgleich(db, {NEX_HD: {603: film(8), 1: film(2)}})  # zweiter Lauf, kein erster mehr
    im_server(db, 603, standard_gb=8)

    abgleich(db, {NEX_HD: {1: film(2)}})  # nexcrate fuehrt 603 nicht mehr

    stand = posten(db)
    assert set(stand) == {f"movie:{NEX_HD}:tmdb:603", f"movie:{NEX_HD}:tmdb:1"}
    zeile = stand[f"movie:{NEX_HD}:tmdb:603"]
    assert zeile.arr_managed is False
    assert zeile.size_bytes == 8 * GB


def test_eine_4k_fassung_aus_nexcrate_misst_die_4k_datei(db: Session) -> None:
    """Welche der beiden Groessen des Medienservers gilt, sagt die Klasse der Fassung.

    ``fassungen.stufe`` kennt nur die Arr-Kennungen und haelt jede nexcrate-Fassung
    fuer ``standard``. Hier wuerde der 4K-Posten dann mit der 1080p-Datei
    gemessen.
    """
    nex_fassungen(db)
    abgleich(db, {NEX_UHD: {603: film(50)}})
    abgleich(db, {NEX_UHD: {603: film(50), 1: film(2)}})
    im_server(db, 603, standard_gb=8, uhd_gb=50)

    abgleich(db, {NEX_UHD: {1: film(2)}})

    assert posten(db)[f"movie:{NEX_UHD}:tmdb:603"].size_bytes == 50 * GB


def test_ein_vorbestehender_posten_unter_anderer_fassung_wird_nicht_weiter_gezaehlt(
    db: Session,
) -> None:
    """⚠️ Der schwerste Fund des Speicher-Bereichs, an einer echten Anlage:
    32 Dateien, 540 GB, einmal vorhanden und zweimal gezählt.

    Die Standard-Instanz von Radarr lädt mit einem 1080p-Profil, greift aber
    eine 2160p-Datei - das passiert oft genug. Ein früherer Lauf hatte die
    Datei deshalb schon einmal unter der 4K-Fassung als Posten stehen (der
    Medienserver meldete sie dort weiter, weil er ``videoResolution=4k``
    sieht). Meldet Radarr sie jetzt korrekt unter der Standard-Fassung, darf
    der alte 4K-Posten nicht weiter mitgezählt werden - byte-genau dieselbe
    Datei zählt nur einmal, egal unter welcher Fassung sie zuerst stand.
    """
    radarr_standard = arr_kennung(MediaType.movie, "standard")
    radarr_uhd = arr_kennung(MediaType.movie, "uhd")

    # Erster Lauf: nur die 4K-Instanz meldet den Titel - der Posten entsteht
    # dort, ganz regulär.
    abgleich(db, {radarr_uhd: {435011: film(50, pfad="/data/Ein Film (4K)")}})
    assert set(posten(db)) == {f"movie:{radarr_uhd}:tmdb:435011"}

    # Zweiter Lauf: Die Standard-Instanz hat dieselbe (4K-)Datei gegriffen und
    # meldet sie jetzt korrekt unter sich selbst; der Medienserver misst die
    # alte 4K-Fassung byte-genau weiter.
    im_server(db, 435011, uhd_gb=50)
    abgleich(db, {radarr_standard: {435011: film(50, pfad="/data/Ein Film (4K)")}})

    assert set(posten(db)) == {f"movie:{radarr_standard}:tmdb:435011"}
    einziger = posten(db)[f"movie:{radarr_standard}:tmdb:435011"]
    assert einziger.size_bytes == 50 * GB


# ------------------------------------------------ Fassung am neuen Posten


def test_ein_neuer_posten_traegt_die_fassung_aus_seinem_schluessel(db: Session) -> None:
    """``fassung_kennung`` stand fest auf der Arr-Fassung, auch im NEX-Betrieb.

    Schluessel ``movie:v_...`` und Fassung ``radarr-standard`` an derselben
    Zeile - gemessen an einer echten Datenbank nach dem ersten Umstieg, 3.591
    Zeilen.
    """
    nex_fassungen(db)
    abgleich(db, {NEX_UHD: {603: film(50)}})

    zeile = posten(db)[f"movie:{NEX_UHD}:tmdb:603"]
    assert zeile.fassung_kennung == NEX_UHD


def test_verbuchen_traegt_die_fassung_der_anfrage(db: Session) -> None:
    """Dieselbe Stelle beim sofortigen Zurechnen einer fertigen Anfrage."""
    nex_fassungen(db)
    person = User(username="speicher", password_hash=hash_password("x"), role=Role.user)
    db.add(person)
    db.commit()
    anfrage = MediaRequest(
        user_id=person.id,
        media_type=MediaType.movie,
        fassung_kennung=NEX_UHD,
        tmdb_id=603,
        title="Ein Film",
        status=RequestStatus.downloaded,
    )
    db.add(anfrage)
    db.commit()

    storage.verbuchen(db, anfrage, film(50))
    db.commit()

    zeile = posten(db)[f"movie:{NEX_UHD}:tmdb:603"]
    assert zeile.fassung_kennung == NEX_UHD
    assert zeile.state == StorageState.owned


# ---------------------------------------------------- die vorhandenen 81


def test_die_wanderung_raeumt_die_posten_des_medienservers_ab(db: Session) -> None:
    """Einmal, beim Start: Was nie ein Beschaffungsweg gemeldet hat, geht.

    Erkannt an zwei Dingen zugleich: nicht verwaltet **und** kein Pfad. Radarr
    nennt zu jedem Film einen Pfad; ein Posten, den Radarr einmal gemeldet hat,
    behaelt ihn, auch wenn er danach nur noch im Medienserver liegt. Gemessen an
    der echten Anlage: 0 verwaltete Posten ohne Pfad.

    ⚠️ Das Merkmal taugt nur fuer den Bestand von heute. nexcrate nennt keinen
    Pfad; ein Posten aus nexcrate, den der Medienserver kuenftig weitermisst,
    saehe genauso aus. Deshalb ist es ein Einmal-Schritt und keine Regel im
    Abgleich.
    """
    radarr = arr_kennung(MediaType.movie, "standard")

    def zeile(key: str, *, verwaltet: bool, pfad: str | None) -> StorageEntry:
        return StorageEntry(
            key=key,
            media_type=MediaType.movie,
            fassung_kennung=key.split(":")[1],
            tmdb_id=int(key.rsplit(":", 1)[1]),
            title=key,
            size_bytes=GB,
            path=pfad,
            arr_managed=verwaltet,
            state=StorageState.house,
        )

    db.add_all(
        [
            zeile(f"movie:{radarr}:tmdb:11644", verwaltet=False, pfad=""),  # Geist
            zeile(f"movie:{radarr}:tmdb:655", verwaltet=False, pfad=None),  # Geist
            zeile(f"movie:{NEX_HD}:tmdb:11171", verwaltet=False, pfad=""),  # mitgewandert
            zeile(f"movie:{radarr}:tmdb:4133", verwaltet=True, pfad="/data/Blow"),
            zeile(f"movie:{radarr}:tmdb:63", verwaltet=False, pfad="/data/12 Monkeys"),
            zeile(f"movie:{NEX_HD}:tmdb:603", verwaltet=True, pfad=""),
        ]
    )
    db.commit()

    db_modul.init_db()
    db.expire_all()

    assert set(posten(db)) == {
        f"movie:{radarr}:tmdb:4133",
        f"movie:{radarr}:tmdb:63",
        f"movie:{NEX_HD}:tmdb:603",
    }
    with db_modul.engine.connect() as verbindung:
        herkunft = verbindung.exec_driver_sql(
            "SELECT wanderung_herkunft FROM wanderungen WHERE wanderung_name = ?",
            ("_medienserver_posten_abraeumen",),
        ).scalar()
    assert herkunft == db_modul.AUSGEFUEHRT


def test_die_wanderung_laeuft_nur_einmal(db: Session) -> None:
    """Danach sieht ein Posten aus nexcrate, den der Medienserver weitermisst,
    genauso aus - er darf beim naechsten Start nicht verschwinden."""
    radarr = arr_kennung(MediaType.movie, "standard")
    db.add(
        StorageEntry(
            key=f"movie:{radarr}:tmdb:11644",
            media_type=MediaType.movie,
            fassung_kennung=radarr,
            tmdb_id=11644,
            title="Geist",
            size_bytes=GB,
            path="",
            arr_managed=False,
            state=StorageState.house,
        )
    )
    db.commit()
    db_modul.init_db()
    db.add(
        StorageEntry(
            key=f"movie:{NEX_HD}:tmdb:603",
            media_type=MediaType.movie,
            fassung_kennung=NEX_HD,
            tmdb_id=603,
            title="Weitergemessen",
            size_bytes=GB,
            path="",
            arr_managed=False,
            state=StorageState.house,
        )
    )
    db.commit()

    db_modul.init_db()

    assert set(posten(db)) == {f"movie:{NEX_HD}:tmdb:603"}
