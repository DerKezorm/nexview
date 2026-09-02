"""``busy_timeout``: der zweite Schreiber wartet, statt sofort zu scheitern.

Seit der Sicherungstakt in einem eigenen Thread laeuft, schreibt planmaessig
ein zweiter Faden. Bis zum 02.09.2026 trug diese Nebenlaeufigkeit allein der
stillschweigende Standard von ``sqlite3.connect`` (timeout=5.0) - im Code
stand nichts, und wer je ein ``timeout`` uebergibt oder den Treiber wechselt,
verloere den Schutz unbemerkt. Der connect-Horcher in ``db.py`` setzt den
Wert deshalb ausdruecklich als PRAGMA.

Geprueft wird genau dort, wo der stille Standard NICHT greift: auf einer
Verbindung mit ``timeout=0``. Was sie dann noch warten laesst, ist allein das
PRAGMA aus dem Horcher - faellt es weg, scheitert der zweite Schreiber hier
sofort mit "database is locked".
"""

from __future__ import annotations

import sqlite3
import threading
import time

import app.db as db_modul
from app.config import get_settings


def test_zweiter_schreiber_wartet_statt_sofort_zu_scheitern() -> None:
    pfad = get_settings().db_path

    # Der Halter: eine offene Schreibtransaktion auf der echten Testdatei.
    # ``check_same_thread=False``, weil ein zweiter Faden sie gleich wieder
    # loslaesst - benutzt wird sie nie gleichzeitig.
    halter = sqlite3.connect(pfad, check_same_thread=False)
    # Der Wartende: ``timeout=0`` nimmt den stillen sqlite3-Standard weg,
    # dann laeuft der Horcher darueber - wie bei jeder Verbindung der Engine.
    wartender = sqlite3.connect(pfad, timeout=0)
    db_modul._configure_sqlite(wartender, None)
    try:
        halter.execute("BEGIN IMMEDIATE")

        def loslassen() -> None:
            time.sleep(0.3)
            halter.commit()

        faden = threading.Thread(target=loslassen)
        beginn = time.perf_counter()
        faden.start()
        try:
            # Ohne das PRAGMA: sofort ``OperationalError: database is
            # locked``. Mit ihm: warten, bis der Halter loslaesst.
            wartender.execute("BEGIN IMMEDIATE")
        finally:
            faden.join(5)
        dauer = time.perf_counter() - beginn
        wartender.rollback()

        assert dauer >= 0.2, f"nicht gewartet, nach {dauer:.3f}s durch"
        assert dauer < 5, f"zu lange gewartet: {dauer:.3f}s"
    finally:
        halter.close()
        wartender.close()
