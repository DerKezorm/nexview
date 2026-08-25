"""Speicher-Belegung: Messen und Zurechnen (Stufe 1).

Stufe 1 begrenzt niemanden. Getestet wird deshalb ausschliesslich, ob die
Zahlen stimmen und ob ein Posten dem Richtigen zugerechnet wird.
"""

from __future__ import annotations

import pytest
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal

from app.models import (
    MediaRequest,
    MediaServerLibraryItem,
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    QuotaPeriod,
    RequestStatus,
    StorageEntry,
    StorageState,
    User,
    Role,
)
from app.security import hash_password
from app.services import quota, storage
from app.services.radarr import LibraryEntry as MovieEntry
from app.services.sonarr import LibraryEntry as SeriesEntry
from app.services.settings_service import AppSettings, load_settings

GB = 1024**3


# --------------------------------------------------------------- Bausteine


def film(size_gb: float, *, titel: str = "Ein Film") -> MovieEntry:
    return MovieEntry(
        arr_id=1,
        has_file=True,
        monitored=True,
        size_bytes=int(size_gb * GB),
        title=titel,
    )


def serie(staffeln: dict[int, float], *, titel: str = "Eine Serie") -> SeriesEntry:
    groessen = {nr: int(gb * GB) for nr, gb in staffeln.items()}
    return SeriesEntry(
        arr_id=1,
        has_file=True,
        monitored=True,
        episode_file_count=10,
        episode_count=10,
        title_key="eineserie",
        year=2020,
        size_bytes=sum(groessen.values()),
        seasons=groessen,
        title=titel,
    )


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as sitzung:
        yield sitzung


@pytest.fixture
def settings(db: Session) -> AppSettings:
    return load_settings(db)


@pytest.fixture
def nutzer(db: Session) -> User:
    person = User(
        username="speicher",
        password_hash=hash_password("test"),
        role=Role.user,
    )
    db.add(person)
    db.commit()
    return person


def anfrage(
    db: Session,
    nutzer: User,
    *,
    tmdb_id: int = 0,
    tvdb_id: int | None = None,
    media_type: MediaType = MediaType.movie,
    tier: QualityTier = QualityTier.standard,
    season: int | None = None,
) -> MediaRequest:
    eintrag = MediaRequest(
        user_id=nutzer.id,
        media_type=media_type,
        tier=tier,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        season=season,
        title="Ein Titel",
        status=RequestStatus.downloaded,
    )
    db.add(eintrag)
    db.commit()
    return eintrag


async def messen(db: Session, settings: AppSettings, *, filme=None, serien=None):
    """Einen Abgleich mit vorgegebenen Bibliotheken laufen lassen.

    Gemockt wird an der Grenze zwischen Holen und Schreiben - so laeuft die
    ganze Rechen- und Zuordnungslogik echt durch, ohne dass ein Radarr
    antworten muesste.
    """
    ergebnis = storage._schreiben(db, _erfasst(db, filme or {}, serien or {}))
    # Wie in ``storage.abgleichen``: Erst schreiben, dann melden. Stuende das
    # hier nicht, liefe jeder Test an der Benachrichtigung vorbei.
    storage._wachstum_melden(db, ergebnis)
    return ergebnis


def _erfasst(db, filme, serien):
    gemessen: dict[str, storage._Gemessen] = {}
    for stufe, eintraege in filme.items():
        for tmdb_id, eintrag in eintraege.items():
            storage._film_aufnehmen(gemessen, stufe, tmdb_id, eintrag)
    for stufe, eintraege in serien.items():
        for tvdb_id, eintrag in eintraege.items():
            storage._serie_aufnehmen(gemessen, stufe, tvdb_id, eintrag)
    storage._aus_media_server(db, gemessen)
    return gemessen


# ------------------------------------------------------------- Schluessel


def test_schluessel_trennt_die_stufen() -> None:
    """4K und 1080p sind zwei Dateien und muessen zwei Posten sein."""
    standard = storage.schluessel(MediaType.movie, QualityTier.standard, tmdb_id=603)
    uhd = storage.schluessel(MediaType.movie, QualityTier.uhd, tmdb_id=603)
    assert standard != uhd


def test_schluessel_ohne_nummer_gibt_nichts() -> None:
    """Lieber keinen Posten als einen, der spaeter nicht wiederzufinden ist."""
    assert storage.schluessel(MediaType.movie, QualityTier.standard) is None
    assert storage.schluessel(MediaType.tv, QualityTier.standard) is None


def test_serien_schluesseln_ueber_tvdb() -> None:
    """Sonarr kennt keine TMDB-Nummern - der Schluessel darf sie nicht verlangen."""
    kennung = storage.schluessel(
        MediaType.tv, QualityTier.standard, tvdb_id=81189, season=3
    )
    assert kennung == "tv:standard:tvdb:81189:s3"


# ----------------------------------------------------------- Erster Lauf


async def test_erster_lauf_legt_alles_ins_haus(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Niemand soll am Tag der Einfuehrung ueberzogen sein.

    Auch wer den Titel frueher einmal angefragt hat, bekommt ihn beim ersten
    Lauf **nicht** zugerechnet - sonst startete er mit einer Historie, von der
    er nie wusste, dass sie zaehlt.
    """
    anfrage(db, nutzer, tmdb_id=603)

    ergebnis = await messen(
        db, settings, filme={QualityTier.standard: {603: film(8)}}
    )

    assert ergebnis.erster_lauf is True
    assert storage.kontostand(db, nutzer.id).used_bytes == 0
    assert storage.hausbestand(db).used_bytes == 8 * GB


async def test_nach_dem_ersten_lauf_wird_zugerechnet(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Ab dem zweiten Lauf traegt, wer angefragt hat.

    Ohne diese Zurechnung staende nach Wochen des Messens bei jedem eine Null,
    und die Frage "wer belegt am meisten" waere unbeantwortbar - also genau der
    Zweck der Messung verfehlt.
    """
    await messen(db, settings, filme={QualityTier.standard: {1: film(2)}})
    anfrage(db, nutzer, tmdb_id=603)

    await messen(
        db,
        settings,
        filme={QualityTier.standard: {1: film(2), 603: film(8)}},
    )

    assert storage.kontostand(db, nutzer.id).used_bytes == 8 * GB
    # Der Altbestand bleibt beim Haus.
    assert storage.hausbestand(db).used_bytes == 2 * GB


# ------------------------------------------------------------- Rechnen


async def test_hausbestand_zaehlt_bei_niemandem(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    await messen(db, settings, filme={QualityTier.standard: {1: film(50)}})
    assert storage.kontostand(db, nutzer.id).used_bytes == 0


async def test_vier_k_und_standard_sind_zwei_posten(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Wer beide Fassungen haelt, belegt beides - das sind wirklich zwei Dateien."""
    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})
    anfrage(db, nutzer, tmdb_id=603, tier=QualityTier.standard)
    anfrage(db, nutzer, tmdb_id=603, tier=QualityTier.uhd)

    await messen(
        db,
        settings,
        filme={
            QualityTier.standard: {1: film(1), 603: film(8)},
            QualityTier.uhd: {603: film(40)},
        },
    )

    assert storage.kontostand(db, nutzer.id).used_bytes == 48 * GB
    assert storage.kontostand(db, nutzer.id).items == 2


async def test_serie_zaehlt_staffelweise(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Eine Zeile je Staffel, nie je Folge - und Staffel 0 belegt echten Platz."""
    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})
    anfrage(
        db,
        nutzer,
        tmdb_id=1399,
        tvdb_id=121361,
        media_type=MediaType.tv,
    )

    await messen(
        db,
        settings,
        filme={QualityTier.standard: {1: film(1)}},
        serien={QualityTier.standard: {121361: serie({0: 2, 1: 20, 2: 25})}},
    )

    stand = storage.kontostand(db, nutzer.id)
    assert stand.used_bytes == 47 * GB
    assert stand.items == 3


async def test_staffel_ohne_dateien_wird_nicht_gefuehrt(
    db: Session, settings: AppSettings
) -> None:
    """Ein Posten ueber null Bytes waere eine Zeile ohne Aussage."""
    await messen(
        db,
        settings,
        serien={QualityTier.standard: {121361: serie({1: 20, 2: 0})}},
    )
    assert db.scalar(select(StorageEntry.season).order_by(StorageEntry.id)) == 1
    assert len(db.scalars(select(StorageEntry)).all()) == 1


# ----------------------------------------------------- Nur noch in Plex


async def test_aus_radarr_entfernt_aber_in_plex_bleibt_belastet(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Radarrs Schweigen ist kein Beweis, dass die Datei weg ist.

    Verbreiteter Arbeitsablauf: laden, bis die Qualitaet stimmt, dann den
    Eintrag aus Radarr werfen und die Datei behalten. Wuerde der Posten dabei
    verschwinden, gaebe er das Kontingent frei, obwohl der Platz weiter belegt
    ist - eine Umgehung, die jeder versehentlich findet.
    """
    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})
    anfrage(db, nutzer, tmdb_id=603)
    await messen(
        db,
        settings,
        filme={QualityTier.standard: {1: film(1), 603: film(8)}},
    )
    assert storage.kontostand(db, nutzer.id).used_bytes == 8 * GB

    # Der Titel liegt jetzt nur noch im Media-Server.
    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.movie,
            guid="plex://movie/603",
            tmdb_id=603,
            title="Ein Film",
            title_key="einfilm",
            size_standard=8 * GB,
        )
    )
    db.commit()

    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})

    assert storage.kontostand(db, nutzer.id).used_bytes == 8 * GB


async def test_aus_radarr_und_plex_verschwunden_gibt_frei(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Erst wenn keine Quelle mehr etwas meldet, ist die Datei wirklich weg."""
    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})
    anfrage(db, nutzer, tmdb_id=603)
    await messen(
        db,
        settings,
        filme={QualityTier.standard: {1: film(1), 603: film(8)}},
    )

    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})

    assert storage.kontostand(db, nutzer.id).used_bytes == 0


async def test_radarr_schlaegt_plex_bei_der_groesse(
    db: Session, settings: AppSettings
) -> None:
    """Solange Radarr den Titel kennt, gilt dessen Zahl - sie ist genauer."""
    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.movie,
            guid="plex://movie/603",
            tmdb_id=603,
            title="Ein Film",
            title_key="einfilm",
            size_standard=99 * GB,
        )
    )
    db.commit()

    await messen(db, settings, filme={QualityTier.standard: {603: film(8)}})

    assert storage.hausbestand(db).used_bytes == 8 * GB


# --------------------------------------------------------- Aufwertungen


async def test_aufwertung_wird_neu_berechnet(
    db: Session, settings: AppSettings
) -> None:
    """Waechst die Datei, waechst der Posten - sonst driftet die Zahl weg."""
    await messen(db, settings, filme={QualityTier.standard: {603: film(4)}})
    ergebnis = await messen(
        db, settings, filme={QualityTier.standard: {603: film(12)}}
    )

    assert ergebnis.gewachsen == 1
    assert storage.hausbestand(db).used_bytes == 12 * GB


# ------------------------------------------------------- Zuruecksetzen


async def test_zuruecksetzen_gibt_alles_ans_haus(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Der Notausgang: Konten auf null, Dateien unangetastet."""
    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})
    anfrage(db, nutzer, tmdb_id=603)
    await messen(
        db,
        settings,
        filme={QualityTier.standard: {1: film(1), 603: film(8)}},
    )
    assert storage.kontostand(db, nutzer.id).used_bytes == 8 * GB

    betroffen = storage.zuruecksetzen(db)

    assert betroffen == 1
    assert storage.kontostand(db, nutzer.id).used_bytes == 0
    assert storage.hausbestand(db).used_bytes == 9 * GB
    # Kein Posten ist verschwunden - es hat nur der Eigentuemer gewechselt.
    assert len(db.scalars(select(StorageEntry)).all()) == 2


# -------------------------------------------------------------- Anzeige


async def test_posten_kommen_gross_zuerst(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Wer Platz schaffen soll, muss zuerst sehen, wo der Platz steckt."""
    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})
    for tmdb_id in (10, 20, 30):
        anfrage(db, nutzer, tmdb_id=tmdb_id)

    await messen(
        db,
        settings,
        filme={
            QualityTier.standard: {
                1: film(1),
                10: film(3, titel="Klein"),
                20: film(40, titel="Gross"),
                30: film(12, titel="Mittel"),
            }
        },
    )

    zeilen, gesamt = storage.posten_fuer(db, nutzer.id)
    assert [posten.title for posten in zeilen] == ["Gross", "Mittel", "Klein"]
    assert gesamt == 3


async def test_abgegebenes_zaehlt_weiter(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Sonst waere Abgeben ein Freifahrtschein, solange niemand entscheidet."""
    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})
    anfrage(db, nutzer, tmdb_id=603)
    await messen(
        db,
        settings,
        filme={QualityTier.standard: {1: film(1), 603: film(8)}},
    )

    posten = db.scalar(
        select(StorageEntry).where(StorageEntry.user_id == nutzer.id)
    )
    posten.state = StorageState.pending
    db.commit()

    stand = storage.kontostand(db, nutzer.id)
    assert stand.used_bytes == 8 * GB
    assert stand.pending_bytes == 8 * GB


# ------------------------------------------------------------- Endpunkte


def test_eigener_speicher_braucht_anmeldung(client) -> None:
    assert client.get("/api/storage/me").status_code == 401


def test_uebersicht_ist_nur_fuer_admins(admin_client) -> None:
    """Wieviel jemand belegt, ist eine Angabe ueber eine Person.

    Entscheider sehen sie nicht - aus demselben Grund, aus dem sie fremde
    Tickets nicht sehen. Wichtig, weil hier leicht die Gewohnheit greift,
    Entscheider mit Admins gleichzusetzen: Bei Freigaben duerfen sie alles
    sehen, hier ausdruecklich nicht.
    """
    from .conftest import auth_headers, create_user

    admin_client.put("/api/settings", json={"storage_enabled": True})
    create_user(admin_client, "entscheider2", "test1234", role=Role.approver)
    kopf = auth_headers(admin_client, "entscheider2", "test1234")
    assert admin_client.get("/api/storage/overview", headers=kopf).status_code == 403
    # Den eigenen Stand darf er sehr wohl sehen.
    assert admin_client.get("/api/storage/me", headers=kopf).status_code == 200


def test_eigener_speicher_ist_leer_ohne_messung(admin_client) -> None:
    """Ohne Abgleich steht ueberall null - und kein Fehler."""
    admin_client.put("/api/settings", json={"storage_enabled": True})
    antwort = admin_client.get("/api/storage/me")
    assert antwort.status_code == 200
    assert antwort.json() == {
        "used_bytes": 0,
        "items": 0,
        "pending_bytes": 0,
        # Der Admin ist immer unbegrenzt - null steht hier fuer genau das.
        "limit_bytes": None,
        "matches": 0,
        # Die Seitengroesse reist mit, damit die Oberflaeche sie nicht
        # spiegeln muss - zwei Konstanten gingen beim Aendern auseinander.
        "per_page": 20,
        # Ohne Media-Server gibt es keine Gesehen-Daten - der Filter
        # "Nur Gesehene" wird dann gar nicht erst angeboten.
        "watched_available": False,
        "entries": [],
    }


def test_uebersicht_weist_den_hausbestand_aus(admin_client, db: Session) -> None:
    """Der Hausbestand steht als eigene Zeile - er gehoert niemandem."""
    admin_client.put("/api/settings", json={"storage_enabled": True})
    db.add(
        StorageEntry(
            key="movie:standard:tmdb:1",
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=1,
            title="Hausfilm",
            size_bytes=10 * GB,
            state=StorageState.house,
        )
    )
    db.commit()

    daten = admin_client.get("/api/storage/overview").json()

    assert daten["house_bytes"] == 10 * GB
    assert daten["house_items"] == 1
    assert daten["total_bytes"] == 10 * GB
    assert daten["shares"][0]["user_id"] is None

# ------------------------------------------- Sofort bei Fertigstellung


def test_verbuchen_rechnet_sofort_zu(db: Session, nutzer: User) -> None:
    """Der Kontostand darf nicht bis zum stuendlichen Abgleich warten.

    Wer gerade etwas angefragt hat und nachsieht, was es ihn kostet, faende
    dort sonst bis zu eine Stunde lang eine Null - und hielte die Anzeige
    fuer kaputt. Genau das ist beim ersten Testlauf passiert.
    """
    gesuch = anfrage(db, nutzer, tmdb_id=603)

    verbucht = storage.verbuchen(db, gesuch, film(8))
    db.commit()

    assert verbucht == 1
    assert storage.kontostand(db, nutzer.id).used_bytes == 8 * GB


def test_verbuchen_nimmt_niemandem_etwas_weg(
    db: Session, nutzer: User, settings: AppSettings
) -> None:
    """Ein Posten, der schon jemandem gehoert, wechselt nie den Besitzer."""
    anderer = User(username="zweiter", password_hash=hash_password("test"), role=Role.user)
    db.add(anderer)
    db.commit()

    db.add(
        StorageEntry(
            key="movie:standard:tmdb:603",
            user_id=anderer.id,
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=603,
            title="Ein Film",
            size_bytes=8 * GB,
            state=StorageState.owned,
        )
    )
    db.commit()

    gesuch = anfrage(db, nutzer, tmdb_id=603)
    storage.verbuchen(db, gesuch, film(8))
    db.commit()

    assert storage.kontostand(db, nutzer.id).used_bytes == 0
    assert storage.kontostand(db, anderer.id).used_bytes == 8 * GB


def test_verbuchen_uebernimmt_aus_dem_hausbestand(db: Session, nutzer: User) -> None:
    """Was niemandem gehoert, darf uebernommen werden - er holt es ja gerade."""
    db.add(
        StorageEntry(
            key="movie:standard:tmdb:603",
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=603,
            title="Ein Film",
            size_bytes=8 * GB,
            state=StorageState.house,
        )
    )
    db.commit()

    gesuch = anfrage(db, nutzer, tmdb_id=603)
    storage.verbuchen(db, gesuch, film(8))
    db.commit()

    assert storage.kontostand(db, nutzer.id).used_bytes == 8 * GB
    assert storage.hausbestand(db).used_bytes == 0


def test_staffel_anfrage_belastet_nur_diese_staffel(db: Session, nutzer: User) -> None:
    """Wer Staffel 3 anfragt, zahlt Staffel 3 - nicht die ganze Serie.

    Sonarr meldet die Groessen **aller** Staffeln in einer Antwort. Ohne diese
    Einschraenkung bekaeme jemand, der eine einzelne Staffel anfragt, die
    gesamte Serie aufs Konto.
    """
    gesuch = anfrage(
        db,
        nutzer,
        tmdb_id=1399,
        tvdb_id=121361,
        media_type=MediaType.tv,
        season=3,
    )

    verbucht = storage.verbuchen(db, gesuch, serie({1: 20, 2: 25, 3: 30}))
    db.commit()

    assert verbucht == 1
    assert storage.kontostand(db, nutzer.id).used_bytes == 30 * GB


def test_serien_anfrage_ohne_staffel_belastet_alle(db: Session, nutzer: User) -> None:
    """Eine Anfrage auf die ganze Serie traegt auch die ganze Serie."""
    gesuch = anfrage(
        db, nutzer, tmdb_id=1399, tvdb_id=121361, media_type=MediaType.tv
    )

    verbucht = storage.verbuchen(db, gesuch, serie({1: 20, 2: 25}))
    db.commit()

    assert verbucht == 2
    assert storage.kontostand(db, nutzer.id).used_bytes == 45 * GB


# ------------------------------------------- Kein Hauptschalter mehr


def test_die_endpunkte_stehen_immer_offen(admin_client) -> None:
    """Gemessen wird immer - es gibt nichts mehr auszuschalten.

    Bis 0.19 antworteten diese Adressen mit 404, solange das Haus nach
    Stueckzahl begrenzte ("Anzahl **oder** Speicher"). Beide Waehrungen gelten
    jetzt zusammen; ein frischer Client kommt ohne jede Vorbereitung durch.
    """
    assert admin_client.get("/api/storage/me").status_code == 200
    assert admin_client.get("/api/storage/overview").status_code == 200


def test_die_konfiguration_kennt_den_schalter_nicht_mehr(admin_client) -> None:
    """Die Oberflaeche soll nichts mehr daran aufhaengen koennen.

    Bliebe das Feld stehen, wuerde irgendwo weiter ein Reiter davon abhaengen -
    und niemand faende heraus, warum er fehlt.
    """
    assert "storage_enabled" not in admin_client.get("/api/config").json()


def test_verteilung_zeigt_auch_leere_konten(admin_client, db: Session) -> None:
    """Jedes aktive Konto steht in der Liste, auch mit null Bytes.

    Wer nur die Belegten zeigt, laesst den Betrachter raetseln, warum jemand
    fehlt: "hat nichts" und "wird nicht erfasst" saehen gleich aus.
    """
    from .conftest import create_user

    create_user(admin_client, "leer", "test1234")
    admin_client.put("/api/settings", json={"storage_enabled": True})

    daten = admin_client.get("/api/storage/overview").json()
    namen = {a["username"] for a in daten["shares"] if a["user_id"] is not None}

    assert "leer" in namen
    assert all(a["used_bytes"] == 0 for a in daten["shares"])


# ------------------------------------------------- Stufe 2: die Grenze


def grenze(db: Session, nutzer: User, settings: AppSettings):
    return storage.stand_fuer(db, nutzer, settings)


def test_admins_sind_immer_unbegrenzt(db: Session) -> None:
    """Dieselbe Regel wie beim Stueck-Kontingent - zwei waeren eine Falle."""

    class Haus:
        storage_default_limit_gb = 100

    chef = User(username="chef", password_hash=hash_password("t"), role=Role.admin)
    assert storage.grenze_in_bytes(chef, Haus()) is None


def test_eigene_zahl_schlaegt_die_hausvorgabe(db: Session) -> None:
    class Haus:
        storage_default_limit_gb = 100

    person = User(username="p", role=Role.user, storage_limit_gb=500)
    assert storage.grenze_in_bytes(person, Haus()) == 500 * GB


def test_null_am_konto_heisst_darf_nichts(db: Session) -> None:
    """⚠️ **Die 0 hat ihre Bedeutung gewechselt.**

    Bis 0.19 hiess sie "fuer dieses Konto unbegrenzt", jetzt das Gegenteil.
    Deshalb zieht ``db._kontingente_dreiwertig_machen`` gespeicherte Nullen
    einmalig auf ``UNBEGRENZT`` um - ohne das waere ein Konto ueber Nacht
    still gesperrt worden.
    """

    class Haus:
        storage_default_limit_gb = 100

    person = User(username="p", role=Role.user, storage_limit_gb=0)
    assert storage.grenze_in_bytes(person, Haus()) == 0


def test_ausdrueckliches_unbegrenzt_schlaegt_die_hausvorgabe(db: Session) -> None:
    """Der dritte Zustand: nicht "Standard", sondern ausdruecklich ohne Grenze."""

    class Haus:
        storage_default_limit_gb = 100

    person = User(username="p", role=Role.user, storage_limit_gb=quota.UNBEGRENZT)
    assert storage.grenze_in_bytes(person, Haus()) is None


def test_ohne_eigene_zahl_gilt_die_hausvorgabe(db: Session) -> None:
    """NULL heisst "Standard" - und der Standard ist eine echte Grenze."""

    class Haus:
        storage_default_limit_gb = 100

    person = User(username="p", role=Role.user, storage_limit_gb=None)
    assert storage.grenze_in_bytes(person, Haus()) == 100 * GB


def test_ohne_hausvorgabe_ist_niemand_begrenzt(db: Session) -> None:
    """So aendert das blosse Einschalten fuer sich genommen nichts."""

    class Haus:
        storage_default_limit_gb = None

    person = User(username="p", role=Role.user)
    assert storage.grenze_in_bytes(person, Haus()) is None


def test_gesperrt_erst_wenn_schon_ueberzogen() -> None:
    """Die Groesse steht beim Anfragen noch gar nicht fest.

    Eine Schaetzung ist keine Grundlage fuer eine Ablehnung: Wer noch Luft
    hat, darf anfragen - auch wenn es danach ins Minus geht. Erst die
    **naechste** Anfrage ist gesperrt.
    """
    knapp = storage.Grenze(used_bytes=99 * GB, limit_bytes=100 * GB)
    assert knapp.exhausted is False
    assert knapp.remaining_bytes == 1 * GB

    genau = storage.Grenze(used_bytes=100 * GB, limit_bytes=100 * GB)
    assert genau.exhausted is True

    minus = storage.Grenze(used_bytes=140 * GB, limit_bytes=100 * GB)
    assert minus.exhausted is True
    assert minus.remaining_bytes == -40 * GB


def test_unbegrenzt_ist_nie_aufgebraucht() -> None:
    offen = storage.Grenze(used_bytes=9999 * GB, limit_bytes=None)
    assert offen.unlimited is True
    assert offen.exhausted is False
    assert offen.remaining_bytes is None


def test_beide_waehrungen_gelten_zusammen(db: Session, nutzer: User) -> None:
    """Eine Anfrage muss durch **beide** Grenzen.

    Bis 0.19 galt genau eine, haus-weit umschaltbar. Der Preis fuer das
    Zusammenlegen sind zwei Gruende zu scheitern, die sich vollkommen
    unterschiedlich verhalten: Die Stueckzahl erneuert sich jeden Montag, der
    Platz nie. ⚠️ Deshalb **muss die Meldung sagen, welche Grenze griff** -
    sonst zwingt ein "ich kann nichts anfragen" den Administrator zum Raten.
    """
    from app.services import requests_service
    from app.services.requests_service import RequestError

    # Ein Stueckzahl-Kontingent, das sofort aufgebraucht ist.
    nutzer.quota_movies_limit = 0
    db.commit()

    class Haus:
        quota_default_movies = None
        quota_default_series = None
        quota_period = QuotaPeriod.week
        # Grosszuegig - der Speicher soll gerade *nicht* bremsen.
        storage_default_limit_gb = 10_000

    # Die Stueckzahl bremst, obwohl im Speicher reichlich Luft ist.
    with pytest.raises(RequestError) as fehler:
        requests_service._kontingent_pruefen(db, Haus(), nutzer, MediaType.movie)
    assert fehler.value.status_code == 429
    assert "Kontingent für Filme" in fehler.value.message

    # Frei gemacht: jetzt geht sie durch.
    nutzer.quota_movies_limit = quota.UNBEGRENZT
    db.commit()
    requests_service._kontingent_pruefen(db, Haus(), nutzer, MediaType.movie)


def test_speichergrenze_bremst_erst_im_minus(db: Session, nutzer: User) -> None:
    from app.services import requests_service
    from app.services.requests_service import RequestError

    class Haus:
        quota_default_movies = None
        quota_default_series = None
        quota_period = QuotaPeriod.week
        storage_default_limit_gb = 10

    db.add(
        StorageEntry(
            key="movie:standard:tmdb:1",
            user_id=nutzer.id,
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=1,
            title="Gross",
            size_bytes=9 * GB,
            state=StorageState.owned,
        )
    )
    db.commit()
    # Noch Luft: geht durch.
    requests_service._kontingent_pruefen(db, Haus(), nutzer, MediaType.movie)

    db.add(
        StorageEntry(
            key="movie:standard:tmdb:2",
            user_id=nutzer.id,
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=2,
            title="Noch groesser",
            size_bytes=5 * GB,
            state=StorageState.owned,
        )
    )
    db.commit()
    # Jetzt im Minus: die naechste Anfrage ist gesperrt.
    with pytest.raises(RequestError) as fehler:
        requests_service._kontingent_pruefen(db, Haus(), nutzer, MediaType.movie)
    assert fehler.value.status_code == 429
    assert "Speicher" in fehler.value.message


# ------------------------------------- Was Admins holen, gehoert dem Haus


async def test_admin_bekommt_nichts_zugerechnet(
    db: Session, settings: AppSettings
) -> None:
    chef = User(username="chef", password_hash=hash_password("t"), role=Role.admin)
    db.add(chef)
    db.commit()

    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})
    anfrage(db, chef, tmdb_id=603)

    await messen(
        db, settings, filme={QualityTier.standard: {1: film(1), 603: film(8)}}
    )

    assert storage.kontostand(db, chef.id).used_bytes == 0
    assert storage.hausbestand(db).used_bytes == 9 * GB


async def test_sofort_verbuchen_ueberspringt_admins(
    db: Session, settings: AppSettings
) -> None:
    chef = User(username="chef", password_hash=hash_password("t"), role=Role.admin)
    db.add(chef)
    db.commit()
    gesuch = anfrage(db, chef, tmdb_id=603)

    assert storage.verbuchen(db, gesuch, film(8)) == 0
    db.commit()
    assert storage.kontostand(db, chef.id).used_bytes == 0


async def test_befoerderung_gibt_die_posten_ans_haus(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Die Regel gilt durchgehend, nicht erst ab dem naechsten neuen Titel."""
    await messen(db, settings, filme={QualityTier.standard: {1: film(1)}})
    anfrage(db, nutzer, tmdb_id=603)
    await messen(
        db, settings, filme={QualityTier.standard: {1: film(1), 603: film(8)}}
    )
    assert storage.kontostand(db, nutzer.id).used_bytes == 8 * GB

    nutzer.role = Role.admin
    db.commit()

    await messen(
        db, settings, filme={QualityTier.standard: {1: film(1), 603: film(8)}}
    )

    assert storage.kontostand(db, nutzer.id).used_bytes == 0
    assert storage.hausbestand(db).used_bytes == 9 * GB


def test_ins_haus_nimmt_niemandem_eine_datei(db: Session, nutzer: User) -> None:
    """Der Titel bleibt liegen - es wechselt nur, wem er zugerechnet wird."""
    db.add(
        StorageEntry(
            key="movie:standard:tmdb:603",
            user_id=nutzer.id,
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=603,
            title="Ein Klassiker",
            size_bytes=8 * GB,
            state=StorageState.owned,
        )
    )
    db.commit()

    posten = storage.ins_haus(db, db.scalar(select(StorageEntry.id)))
    db.commit()

    assert posten is not None
    assert storage.kontostand(db, nutzer.id).used_bytes == 0
    assert storage.hausbestand(db).used_bytes == 8 * GB
    # Der Posten existiert weiter - nur eben beim Haus.
    assert len(db.scalars(select(StorageEntry)).all()) == 1


def test_ins_haus_zweimal_aendert_nichts(db: Session, nutzer: User) -> None:
    db.add(
        StorageEntry(
            key="movie:standard:tmdb:603",
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=603,
            title="Schon im Haus",
            size_bytes=8 * GB,
            state=StorageState.house,
        )
    )
    db.commit()

    assert storage.ins_haus(db, db.scalar(select(StorageEntry.id))) is None


def test_pfad_kommt_bei_film_und_staffel_an(db: Session, settings: AppSettings) -> None:
    """Beim Film samt Dateiname, bei der Staffel der Ordner der Serie.

    Der Serien-Pfad war beim ersten Anlauf schlicht vergessen - die Zeilen
    sahen richtig aus, nur stand dort nichts.
    """
    ziel: dict[str, storage._Gemessen] = {}
    storage._film_aufnehmen(
        ziel,
        QualityTier.standard,
        603,
        MovieEntry(
            arr_id=1,
            has_file=True,
            monitored=True,
            size_bytes=8 * GB,
            title="Matrix",
            path="/data/Movies/Matrix (1999)/Matrix.mkv",
        ),
    )
    storage._serie_aufnehmen(
        ziel,
        QualityTier.standard,
        121361,
        SeriesEntry(
            arr_id=2,
            has_file=True,
            monitored=True,
            episode_file_count=10,
            episode_count=10,
            title_key="got",
            year=2011,
            size_bytes=20 * GB,
            seasons={1: 20 * GB},
            title="Game of Thrones",
            path="/data/TV-Shows/Game of Thrones",
        ),
    )

    pfade = {w.media_type: w.path for w in ziel.values()}
    assert pfade[MediaType.movie].endswith("Matrix.mkv")
    assert pfade[MediaType.tv] == "/data/TV-Shows/Game of Thrones"


def test_pfad_geht_nur_an_admins(arr_client, monkeypatch) -> None:
    """Der Ablageort verlaesst den Server nur fuer Administratoren.

    Entschieden wird das **im Server**, nicht in der Oberflaeche: Ausblenden
    hiesse, ihn trotzdem ausgeliefert zu haben - ein Blick in die
    Netzwerkanzeige des Browsers genuegte.
    """
    from app.schemas_media import MediaItem
    from app.services import library

    async def filme(_settings, _stufe="standard"):
        return {
            603: MovieEntry(
                arr_id=1,
                has_file=True,
                monitored=True,
                size_bytes=8 * GB,
                title="Matrix",
                path="/data/Movies/Matrix/Matrix.mkv",
            )
        }

    monkeypatch.setattr(library, "movie_library", filme)

    werk = MediaItem(tmdb_id=603, media_type="movie", title="Matrix")
    from app.services.settings_service import load_settings

    with SessionLocal() as db:
        settings = load_settings(db)

    import asyncio

    ohne = asyncio.run(library.apply_status(settings, "movie", [werk]))
    mit = asyncio.run(library.apply_status(settings, "movie", [werk], mit_pfad=True))

    assert ohne.items[0].path is None
    assert mit.items[0].path == "/data/Movies/Matrix/Matrix.mkv"


# ------------------------------------------------- Aufwertung meldet sich


async def test_aufwertung_meldet_sich_beim_betroffenen(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Aus 5 GB werden 50 - und niemand hat etwas getan.

    Radarr schiebt ein besseres Release nach, das Konto steigt von selbst.
    Ohne Hinweis faende der Betroffene eine still gestiegene Zahl vor und
    suchte den Fehler bei sich.
    """
    await messen(db, settings, filme={QualityTier.standard: {1: film(2)}})
    anfrage(db, nutzer, tmdb_id=603)
    await messen(
        db,
        settings,
        filme={QualityTier.standard: {1: film(2), 603: film(5, titel="Matrix")}},
    )
    vorher = set(db.scalars(select(Notification.id)).all())

    ergebnis = await messen(
        db,
        settings,
        filme={QualityTier.standard: {1: film(2), 603: film(50, titel="Matrix")}},
    )

    assert ergebnis.gewachsen == 1
    neue = [
        zeile
        for zeile in db.scalars(select(Notification)).all()
        if zeile.id not in vorher
    ]
    assert [zeile.type for zeile in neue] == [NotificationType.storage_grew]
    assert neue[0].message_title == "Matrix"
    # ⚠️ Keine geschweiften Klammern im Baustein - sonst stehen sie woertlich
    # in der Glocke.
    assert "{{" not in neue[0].message_key


async def test_geringfuegiges_wachstum_meldet_sich_nicht(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Laerm wird weggeklickt - danach auch die Meldung, auf die es ankommt.

    Radarr schiebt staendig geringfuegig groessere Releases derselben Stufe
    nach. Meldete sich jede davon, waere die Glocke unbrauchbar.
    """
    await messen(db, settings, filme={QualityTier.standard: {1: film(2)}})
    anfrage(db, nutzer, tmdb_id=603)
    await messen(
        db, settings, filme={QualityTier.standard: {1: film(2), 603: film(5)}}
    )
    vorher = len(db.scalars(select(Notification)).all())

    ergebnis = await messen(
        db, settings, filme={QualityTier.standard: {1: film(2), 603: film(5.3)}}
    )

    assert ergebnis.gewachsen == 1  # gezaehlt wird es sehr wohl
    assert len(db.scalars(select(Notification)).all()) == vorher


async def test_hausbestand_meldet_kein_wachstum(
    db: Session, settings: AppSettings
) -> None:
    """Ohne Besitzer gibt es niemanden, den es betraefe."""
    await messen(db, settings, filme={QualityTier.standard: {603: film(5)}})
    await messen(db, settings, filme={QualityTier.standard: {603: film(50)}})

    assert db.scalars(select(Notification)).all() == []


async def test_der_erste_lauf_meldet_nichts(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """Eine Ladung Meldungen am Tag der Einfuehrung waere der schlechteste
    denkbare Einstieg - und es gaebe ohnehin keinen Betroffenen, weil beim
    ersten Lauf alles dem Haus gehoert."""
    anfrage(db, nutzer, tmdb_id=603)

    ergebnis = await messen(
        db, settings, filme={QualityTier.standard: {603: film(50)}}
    )

    assert ergebnis.erster_lauf
    assert db.scalars(select(Notification)).all() == []


async def test_bei_zwei_servern_zaehlt_der_groessere_wert(
    db: Session, settings: AppSettings
) -> None:
    """Melden zwei Medienserver denselben Titel verschieden, gilt der groessere.

    Das ist keine Vermutung darueber, wer recht hat, sondern eine Entscheidung
    darueber, welcher Irrtum weniger schadet: Zu wenig zu zaehlen hiesse, die
    Platte laeuft voll, obwohl die Kontingente greifen - genau das, wogegen sie
    gebaut wurden.

    Bis zum Parallelbetrieb gewann schlicht der erste Treffer. Das stimmte in
    der Praxis, weil es nur einen Server gab - aber es war ein Zufall, kein
    Ergebnis, und mit zwei Servern haette dieselbe Bibliothek von Lauf zu Lauf
    andere Zahlen ergeben.
    """
    for anbieter, groesse in (("plex", 5 * GB), ("jellyfin", 9 * GB)):
        db.add(
            MediaServerLibraryItem(
                provider=anbieter,
                media_type=MediaType.movie,
                guid=f"{anbieter}://movie/603",
                tmdb_id=603,
                title="Ein Film",
                title_key="einfilm",
                size_standard=groesse,
            )
        )
    db.commit()

    await messen(db, settings, filme={QualityTier.standard: {}})

    assert storage.hausbestand(db).used_bytes == 9 * GB


async def test_ein_server_ohne_groesse_drueckt_den_posten_nicht(
    db: Session, settings: AppSettings
) -> None:
    """Null heisst "unbekannt", nicht "leer".

    Ein Server, der zu einem Titel keine Groesse liefert - etwa weil er ihn
    gar nicht kennt -, darf den Posten nicht auf 0 ziehen. Sonst haenge die
    Buchhaltung davon ab, welcher Server zuletzt gelesen wurde.
    """
    for anbieter, groesse in (("plex", 7 * GB), ("jellyfin", 0)):
        db.add(
            MediaServerLibraryItem(
                provider=anbieter,
                media_type=MediaType.movie,
                guid=f"{anbieter}://movie/603",
                tmdb_id=603,
                title="Ein Film",
                title_key="einfilm",
                size_standard=groesse,
            )
        )
    db.commit()

    await messen(db, settings, filme={QualityTier.standard: {}})

    assert storage.hausbestand(db).used_bytes == 7 * GB


async def test_radarr_schlaegt_auch_den_groesseren_server(
    db: Session, settings: AppSettings
) -> None:
    """Die neue Regel gilt **unter** den Servern, nicht gegen Radarr.

    Sonst haette der Parallelbetrieb nebenbei eine Regel gekippt, die mit ihm
    nichts zu tun hat: Radarrs Zahl ist die genauere, egal wie gross die
    Medienserver melden.
    """
    for anbieter, groesse in (("plex", 50 * GB), ("jellyfin", 99 * GB)):
        db.add(
            MediaServerLibraryItem(
                provider=anbieter,
                media_type=MediaType.movie,
                guid=f"{anbieter}://movie/603",
                tmdb_id=603,
                title="Ein Film",
                title_key="einfilm",
                size_standard=groesse,
            )
        )
    db.commit()

    await messen(db, settings, filme={QualityTier.standard: {603: film(8)}})

    assert storage.hausbestand(db).used_bytes == 8 * GB


def test_nur_im_media_server_heisst_nicht_mehr_verwaltet(
    db: Session, settings: AppSettings, nutzer: User
) -> None:
    """⚠️ Der **Geisterposten** - und ab jetzt ist er erkennbar.

    Verbreiteter Ablauf: laden bis die Qualitaet stimmt, dann den Eintrag aus
    Radarr werfen und die Datei behalten. Der Posten zaehlt weiter (die Bytes
    liegen ja auf der Platte), laesst sich aber **nicht mehr loeschen** -
    Nexview loescht ausschliesslich ueber Radarr/Sonarr.

    Vorher merkte das nur der Administrator, und zwar erst beim Loeschversuch.
    """
    import asyncio

    from app.models import MediaServerLibraryItem

    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.movie,
            guid="plex://movie/603",
            tmdb_id=603,
            title="Nur noch im Server",
            title_key="nurnochimserver",
            size_standard=7 * GB,
        )
    )
    db.commit()

    # Radarr meldet nichts - der Titel kommt allein vom Media-Server.
    asyncio.run(messen(db, settings, filme={QualityTier.standard: {}}))

    zeile = db.scalars(select(StorageEntry)).one()
    assert zeile.arr_managed is False
    assert storage.posten_von(db, zeile.id).managed is False
