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
    QualityTier,
    RequestStatus,
    StorageEntry,
    StorageState,
    User,
    Role,
)
from app.security import hash_password
from app.services import storage
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
    return storage._schreiben(db, _erfasst(db, filme or {}, serien or {}))


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

    titel = [posten.title for posten in storage.posten_fuer(db, nutzer.id)]
    assert titel == ["Gross", "Mittel", "Klein"]


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
