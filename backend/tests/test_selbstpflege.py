"""Selbstpflege der Datenbank: einmalige Umstellung, laufende Rueckgabe.

``_speicher_zurueckgeben_umstellen`` stellt die Datei am Ende von ``init_db``
einmalig auf ``auto_vacuum=INCREMENTAL`` um, ``platz_zurueckgeben`` traegt den
Freiraum danach am Sicherungstakt stueckweise ab. Beides wird hier an einer
kleinen, kuenstlich aufgeblasenen Datei geprueft: 500 Blobs zu 4000 Bytes rein
und wieder raus ergeben 2 MB Datei mit 500 freien Seiten, ganz ohne grosse
Datei im Repo.

Der Schritt ist bewusst Pflege und keine Wanderung: die Entscheidung faellt am
direkt ablesbaren ``PRAGMA auto_vacuum``, und nur so heilt sich eine
eingespielte Sicherung von vor der Umstellung beim naechsten Start von selbst.
Die Einordnung bewacht ``test_wanderungsbuch.py``.
"""

from __future__ import annotations

import logging
import sqlite3
import types
from pathlib import Path

import pytest
from sqlalchemy import create_engine

import app.db as db_modul


def _aufgeblasene_datei(pfad: Path, *, umgestellt: bool = False) -> None:
    """500 Blobs zu 4000 Bytes rein und wieder raus: 2 MB Datei, Freiliste 500.

    Mit ``umgestellt=True`` entsteht die Datei gleich als INCREMENTAL: vor der
    ersten Tabelle gesetzt wirkt das PRAGMA sofort, ganz ohne VACUUM. So laesst
    sich der laufende Teil pruefen, ohne vorher die Umstellung zu bemuehen.
    """
    verbindung = sqlite3.connect(pfad)
    try:
        if umgestellt:
            verbindung.execute("PRAGMA auto_vacuum=2")
        verbindung.execute("CREATE TABLE ballast (inhalt BLOB)")
        verbindung.executemany(
            "INSERT INTO ballast (inhalt) VALUES (?)",
            [(b"x" * 4000,) for _ in range(500)],
        )
        verbindung.commit()
        verbindung.execute("DELETE FROM ballast")
        verbindung.commit()
    finally:
        verbindung.close()


def _befund(pfad: Path) -> tuple[int, int]:
    """``(auto_vacuum, freelist_count)`` der Datei, frisch gelesen."""
    verbindung = sqlite3.connect(pfad)
    try:
        modus = verbindung.execute("PRAGMA auto_vacuum").fetchone()[0]
        frei = verbindung.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        verbindung.close()
    return modus, frei


@pytest.fixture
def eigenes_datenverzeichnis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """``db_path`` und ``engine`` auf ein eigenes Verzeichnis richten.

    Dasselbe Muster wie in ``test_migration.py``: ``db_path`` und der Ort der
    Sicherungen leiten sich beide aus ``data_dir`` ab, ein Umbiegen genuegt.
    """
    datenverzeichnis = tmp_path / "data"
    datenverzeichnis.mkdir()
    db_pfad = datenverzeichnis / "nexview.db"
    motor = create_engine(
        f"sqlite:///{db_pfad}", connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(db_modul, "engine", motor)
    monkeypatch.setattr(db_modul._settings, "data_dir", datenverzeichnis)
    yield db_pfad
    motor.dispose()


class TestUmstellung:
    def test_stellt_um_und_leert_die_freiliste(
        self, eigenes_datenverzeichnis: Path
    ) -> None:
        _aufgeblasene_datei(eigenes_datenverzeichnis)
        # Erst das Rezept festnageln: sonst prueft der Test unbemerkt eine
        # Datei, die gar nichts abzutragen hat.
        assert _befund(eigenes_datenverzeichnis) == (0, 500)
        assert eigenes_datenverzeichnis.stat().st_size == 2_056_192

        db_modul._speicher_zurueckgeben_umstellen()

        assert _befund(eigenes_datenverzeichnis) == (2, 0)
        # Das volle VACUUM gibt den Ballast sofort zurueck (gemessen 12.288).
        assert eigenes_datenverzeichnis.stat().st_size < 100_000

    def test_init_db_fuehrt_die_umstellung_aus(
        self, eigenes_datenverzeichnis: Path
    ) -> None:
        """Der Schritt haengt wirklich am Start, nicht nur an einem Helfer."""
        _aufgeblasene_datei(eigenes_datenverzeichnis)

        db_modul.init_db()

        assert _befund(eigenes_datenverzeichnis) == (2, 0)

    def test_zweiter_lauf_aendert_nichts_und_schweigt(
        self, eigenes_datenverzeichnis: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Idempotenz ist die Doktrin der Pflegeschritte, also wird sie geprueft."""
        _aufgeblasene_datei(eigenes_datenverzeichnis)
        db_modul._speicher_zurueckgeben_umstellen()
        groesse = eigenes_datenverzeichnis.stat().st_size

        # ⚠️ ``caplog.records`` sammelt ueber den ganzen Test, nicht nur im
        # ``with``. Lief vorher irgendein Test mit ``TestClient``, steht die
        # Stufe durch ``logs.setup()`` auf INFO, und die Zeilen des ersten
        # Laufs staenden sonst mit in der Liste.
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="nexview.db"):
            db_modul._speicher_zurueckgeben_umstellen()

        assert not caplog.records
        assert _befund(eigenes_datenverzeichnis) == (2, 0)
        assert eigenes_datenverzeichnis.stat().st_size == groesse

    def test_fehlschlag_reisst_den_start_nicht(
        self,
        eigenes_datenverzeichnis: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Scheitert das VACUUM, laeuft ``init_db`` durch und der naechste Start holt nach."""
        _aufgeblasene_datei(eigenes_datenverzeichnis)

        echt = sqlite3.connect

        class _VacuumKaputt:
            def __init__(self, innen: sqlite3.Connection) -> None:
                self._innen = innen

            def execute(self, sql: str, *parameter):
                if sql.strip().upper().startswith("VACUUM"):
                    raise sqlite3.OperationalError("disk I/O error")
                return self._innen.execute(sql, *parameter)

            def close(self) -> None:
                self._innen.close()

        # Nur die Namensbindung in ``app.db`` wird getauscht: die Engine und
        # ``sicherung.py`` verbinden sich weiter ungestoert.
        monkeypatch.setattr(
            db_modul,
            "sqlite3",
            types.SimpleNamespace(
                connect=lambda pfad, *a, **kw: _VacuumKaputt(echt(pfad, *a, **kw)),
                Error=sqlite3.Error,
            ),
        )

        with caplog.at_level(logging.WARNING, logger="nexview.db"):
            db_modul.init_db()

        assert any(
            "Auto vacuum conversion failed" in eintrag.getMessage()
            for eintrag in caplog.records
        )
        # Die Zustandsschleuse: das PRAGMA meldet weiter 0, der naechste
        # Start versucht die Umstellung erneut.
        modus, _ = _befund(eigenes_datenverzeichnis)
        assert modus == 0


class TestPlatzZurueckgeben:
    def test_senkt_die_freiliste_und_die_datei_schrumpft(
        self, eigenes_datenverzeichnis: Path
    ) -> None:
        _aufgeblasene_datei(eigenes_datenverzeichnis, umgestellt=True)
        assert _befund(eigenes_datenverzeichnis) == (2, 500)
        vorher = eigenes_datenverzeichnis.stat().st_size

        freigegeben = db_modul.platz_zurueckgeben(1000)

        assert freigegeben == 500
        assert _befund(eigenes_datenverzeichnis) == (2, 0)
        # Gemessen: von 2.060.288 auf 12.288 Bytes.
        assert eigenes_datenverzeichnis.stat().st_size < vorher

    def test_auf_nicht_umgestellter_datei_ein_belegter_leerlauf(
        self, eigenes_datenverzeichnis: Path
    ) -> None:
        """Vor der Umstellung darf der Takt nichts tun und nichts behaupten."""
        _aufgeblasene_datei(eigenes_datenverzeichnis)

        freigegeben = db_modul.platz_zurueckgeben(1000)

        assert freigegeben == 0
        assert _befund(eigenes_datenverzeichnis) == (0, 500)
