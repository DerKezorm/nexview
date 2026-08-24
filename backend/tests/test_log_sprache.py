"""Protokollmeldungen sind englisch - und bleiben es.

Ohne diesen Test zerfaellt die Regel bei der naechsten Funktion wieder: Sie
stand jahrelang so im Docstring von ``services/logs.py``, und trotzdem war gut
die Haelfte der Meldungen deutsch. Ein gemischtes Protokoll ist nicht
durchsuchbar - wer nach "not reachable" sucht, findet die deutsche Haelfte der
Faelle nicht.

Nicht betroffen: Kommentare, Docstrings und die Fehlertexte fuer den Nutzer.
Die bleiben deutsch.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

LOG_METHODEN = {"debug", "info", "warning", "error", "exception", "critical"}

#: Woerter, die es im Englischen nicht gibt und die in echten Meldungen
#: vorkommen. Bewusst ohne "in", "so", "die" und aehnliche Doppelgaenger.
DEUTSCHE_WOERTER = {
    "abgerufen",
    "abgleich",
    "angelegt",
    "anzahl",
    "aufgetreten",
    "benutzer",
    "bereits",
    "bestand",
    "datei",
    "eingerichtet",
    "eintrag",
    "entfernt",
    "erreicht",
    "erstellt",
    "fehlender",
    "fehlgeschlagen",
    "gefunden",
    "gelesen",
    "geloescht",
    "geschrieben",
    "gespeichert",
    "keine",
    "konnte",
    "lesbar",
    "moeglich",
    "nicht",
    "nachricht",
    "neue",
    "ohne",
    "titel",
    "ueber",
    "uebersprungen",
    "verbindung",
    "verschickt",
    "wird",
    "wurde",
    "zeitgrenze",
    "zugeordnet",
    "zurueck",
}

UMLAUTE = set("äöüßÄÖÜ")


def _meldungen() -> list[tuple[Path, int, str]]:
    """Alle Protokollmeldungen im Quelltext einsammeln."""
    gefunden: list[tuple[Path, int, str]] = []
    for datei in sorted(APP.rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Call):
                continue
            ziel = knoten.func
            if not isinstance(ziel, ast.Attribute) or ziel.attr not in LOG_METHODEN:
                continue
            # Nur Aufrufe auf etwas, das "logger" oder "logging" heisst.
            wurzel = ziel.value
            name = getattr(wurzel, "id", "") or getattr(wurzel, "attr", "")
            if "log" not in name.lower() and not isinstance(wurzel, ast.Call):
                continue
            if not knoten.args:
                continue
            erstes = knoten.args[0]
            if isinstance(erstes, ast.Constant) and isinstance(erstes.value, str):
                gefunden.append((datei, knoten.lineno, erstes.value))
            elif isinstance(erstes, ast.JoinedStr):
                gefunden.append(
                    (
                        datei,
                        knoten.lineno,
                        "".join(
                            teil.value
                            for teil in erstes.values
                            if isinstance(teil, ast.Constant) and isinstance(teil.value, str)
                        ),
                    )
                )
    return gefunden


def test_es_gibt_ueberhaupt_meldungen() -> None:
    """Wacht darueber, dass das Einsammeln oben nicht ins Leere laeuft."""
    assert len(_meldungen()) > 80


def test_alle_meldungen_sind_englisch() -> None:
    verstoesse: list[str] = []

    for datei, zeile, text in _meldungen():
        ort = f"{datei.relative_to(APP.parent)}:{zeile}"
        if UMLAUTE & set(text):
            verstoesse.append(f"{ort}: Umlaut in {text!r}")
            continue
        woerter = {wort.strip(".,:;()[]%").lower() for wort in text.split()}
        treffer = woerter & DEUTSCHE_WOERTER
        if treffer:
            verstoesse.append(f"{ort}: {sorted(treffer)} in {text!r}")

    assert not verstoesse, "Protokollmeldungen müssen englisch sein:\n" + "\n".join(verstoesse)
