"""Was passiert mit einer bestehenden Datenbank nach einem Update?

Das ist der gefaehrlichste Moment im Betrieb: Auf dem NAS laeuft eine
Datenbank voller Konten und Anfragen, und der Container bringt ploetzlich ein
Schema mit, das mehr Tabellen und Spalten kennt. Geht dabei etwas schief,
merkt es der Nutzer erst, wenn nichts mehr startet.

Die Tests hier bauen deshalb absichtlich eine *aeltere* Datenbank nach und
lassen ``init_db()` darauf los - genau wie beim echten Update.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app import db as db_modul
from app.models import Base


def _alte_datenbank(pfad: Path) -> None:
    """Eine Datenbank im Stand einer aelteren Version erzeugen.

    Nachgebildet wird der Zustand vor der Konten-Ueberarbeitung: die
    Benutzertabelle kennt weder E-Mail-Adresse noch Bestaetigung, und die
    Tabelle fuer Einladungs- und Passwortlinks gibt es ueberhaupt nicht.
    """
    engine = create_engine(f"sqlite:///{pfad}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER NOT NULL PRIMARY KEY,
                    username VARCHAR(50) NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL,
                    display_name VARCHAR(100),
                    language VARCHAR(5) NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    auto_approve BOOLEAN NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users
                    (username, password_hash, role, display_name, language,
                     is_active, auto_approve, created_at)
                VALUES
                    ('altbenutzer', 'egal', 'user', 'Alter Hase', 'de',
                     1, 0, '2026-01-01 12:00:00')
                """
            )
        )
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_db():
    """Die gemeinsame Vorbereitung aushebeln.

    ``conftest.clean_db`` ruft ``init_db()`` auf der echten Testdatenbank auf.
    Hier soll ausschliesslich die nachgebaute alte Datenbank angefasst werden -
    sonst zaehlen die Tests Sicherungen, die gar nicht von ihnen stammen.
    """
    yield


@pytest.fixture
def alte_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``init_db()`` auf eine alte Datenbank in einem eigenen Verzeichnis richten."""
    datenverzeichnis = tmp_path / "data"
    datenverzeichnis.mkdir()
    db_pfad = datenverzeichnis / "nexview.db"
    _alte_datenbank(db_pfad)

    engine = create_engine(f"sqlite:///{db_pfad}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_modul, "engine", engine)
    # ``db_path`` und der Ort der Sicherungen leiten sich beide aus
    # ``data_dir`` ab - ein Umbiegen genuegt also.
    monkeypatch.setattr(db_modul._settings, "data_dir", datenverzeichnis)

    yield db_pfad
    engine.dispose()


def test_update_ergaenzt_fehlende_spalten_und_tabellen(alte_installation: Path) -> None:
    """Nach dem Update kennt die alte Datenbank das komplette Schema."""
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        tabellen = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        spalten = db_modul._existing_columns(connection, "users")

    # Neue Tabelle ist dazugekommen ...
    assert "auth_tokens" in tabellen
    # ... und die neuen Spalten stecken in der alten Tabelle.
    assert {"email", "email_verified", "quota_reset_at"} <= spalten

    # Jede im Modell definierte Tabelle muss existieren.
    fehlend = {t.name for t in Base.metadata.sorted_tables} - tabellen
    assert not fehlend, f"Nach dem Update fehlen noch Tabellen: {sorted(fehlend)}"


def test_update_behaelt_vorhandene_daten(alte_installation: Path) -> None:
    """Der Bestand darf beim Update nicht verlorengehen."""
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        zeilen = connection.exec_driver_sql(
            "SELECT username, display_name, email, email_verified FROM users"
        ).fetchall()

    assert len(zeilen) == 1
    name, anzeige, email, bestaetigt = zeilen[0]
    assert name == "altbenutzer"
    assert anzeige == "Alter Hase"
    # Neue Spalten stehen auf ihrem Standardwert, nicht auf Unsinn.
    assert email is None
    assert not bestaetigt


def test_update_legt_sicherung_an(alte_installation: Path) -> None:
    """Vor der ersten Aenderung entsteht eine Kopie der Datenbank."""
    db_modul.init_db()

    sicherungen = list((alte_installation.parent / "sicherungen").glob("nexview-vor-*.db"))
    assert len(sicherungen) == 1

    # Die Kopie muss den alten Stand enthalten - also lesbar sein und den
    # Benutzer von vorher fuehren.
    kopie = create_engine(f"sqlite:///{sicherungen[0]}")
    with kopie.connect() as connection:
        namen = [row[0] for row in connection.exec_driver_sql("SELECT username FROM users")]
    kopie.dispose()
    assert namen == ["altbenutzer"]


def test_zweiter_start_aendert_nichts_mehr(alte_installation: Path) -> None:
    """Ohne Schemaaenderung darf keine weitere Sicherung entstehen.

    Sonst liefe bei jedem Neustart des Containers eine Kopie mit - auf einem
    NAS mit grosser Datenbank waere das schnell unangenehm.
    """
    db_modul.init_db()
    assert db_modul._pending_changes() == []

    db_modul.init_db()
    sicherungen = list((alte_installation.parent / "sicherungen").glob("nexview-vor-*.db"))
    assert len(sicherungen) == 1


def test_frische_installation_ohne_sicherung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Beim allerersten Start gibt es nichts zu sichern."""
    datenverzeichnis = tmp_path / "data"
    datenverzeichnis.mkdir()
    engine = create_engine(f"sqlite:///{datenverzeichnis / 'nexview.db'}")
    monkeypatch.setattr(db_modul, "engine", engine)
    monkeypatch.setattr(db_modul._settings, "data_dir", datenverzeichnis)

    db_modul.init_db()
    engine.dispose()

    assert not (datenverzeichnis / "sicherungen").exists()


def test_alte_sicherungen_werden_aufgeraeumt(tmp_path: Path) -> None:
    """Es bleiben hoechstens die juengsten Kopien liegen."""
    ordner = tmp_path / "sicherungen"
    ordner.mkdir()
    for nummer in range(8):
        datei = ordner / f"nexview-vor-0.{nummer}.0-2026-01-0{nummer + 1}_120000.db"
        datei.write_text("x", encoding="utf-8")
        # Klar unterscheidbare Zeitstempel, damit die Reihenfolge eindeutig ist.
        import os

        os.utime(datei, (1_700_000_000 + nummer * 60, 1_700_000_000 + nummer * 60))

    db_modul._prune_backups(ordner, behalten=3)

    uebrig = sorted(p.name for p in ordner.glob("*.db"))
    assert len(uebrig) == 3
    # Die drei juengsten muessen es sein.
    assert all("0.5.0" in n or "0.6.0" in n or "0.7.0" in n for n in uebrig), uebrig
