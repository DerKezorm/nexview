"""Jeder Befund muss in beiden Sprachen einen Text haben.

⚠️ **Warum ein Test und keine Sorgfalt.** Fehlt ein Schluessel, zeigt i18next
den rohen Punktpfad an — `befund.dienst.indexer_aus.titel` steht dann mitten
im Dashboard. Es gibt keine Fehlermeldung, nichts wird rot, und auffallen
kann es nur jemandem, bei dem der Befund gerade zutrifft. Genau deshalb faellt
es meist erst dem Nutzer auf und nicht dem, der die Pruefung gebaut hat.

Der Test liest die Sprachdateien der Oberflaeche. Das ist ungewoehnlich — sie
liegen im anderen Teil des Projekts —, aber die Verbindung ist real: Das
Register erzeugt Kennungen, und die Oberflaeche muss sie uebersetzen koennen.
Wer die Verbindung nicht prueft, hat sie trotzdem, nur ungeprueft.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import befunde

#: Von ``backend/tests`` aus zwei Ebenen hoch und in die Oberflaeche.
I18N = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"

#: i18next haengt bei Mengenangaben ein Suffix an. Vorhanden ist ein Text
#: also, wenn es entweder ihn selbst oder **beide** Beugungen gibt.
BEUGUNGEN = ("_one", "_other")


def _sprachdatei(name: str) -> dict:
    return json.loads((I18N / f"{name}.json").read_text(encoding="utf-8"))


def _wert(daten: dict, pfad: str):
    stelle = daten
    for teil in pfad.split("."):
        if not isinstance(stelle, dict) or teil not in stelle:
            return None
        stelle = stelle[teil]
    return stelle


def _hat_text(daten: dict, pfad: str) -> bool:
    if isinstance(_wert(daten, pfad), str):
        return True
    return all(isinstance(_wert(daten, pfad + b), str) for b in BEUGUNGEN)


def _alle_kennungen() -> set[str]:
    """Jede Kennung, die das Register ueberhaupt erzeugen kann.

    Aus dem Quelltext gelesen und nicht aus einem Lauf: Ein Lauf zeigt nur,
    was gerade zutrifft, und genau die selten zutreffenden Pruefungen sind
    die, deren fehlender Text niemandem auffaellt.
    """
    quelle = Path(befunde.__file__).read_text(encoding="utf-8")
    gefunden = set()
    for zeile in quelle.splitlines():
        stueck = zeile.strip()
        if not stueck.startswith("kennung="):
            continue
        wert = stueck[len("kennung=") :].strip().rstrip(",")
        if wert.startswith('"') and wert.endswith('"'):
            gefunden.add(wert[1:-1])
    # Faelle mit mehreren Kennungen in einem Ausdruck stehen nicht auf einer
    # Zeile - die werden hier ergaenzt, damit der Test nicht vorgibt,
    # vollstaendig zu sein, ohne es zu sein.
    for zeile in quelle.splitlines():
        stueck = zeile.strip()
        if stueck.startswith('"bibliothek.luecken') or stueck.startswith(
            '"betrieb.sicherung'
        ):
            gefunden.add(stueck.strip('",'))
    return gefunden


def test_der_kennungs_sammler_findet_wirklich_alle() -> None:
    """Absicherung des Sammlers selbst.

    Faende er nur die Haelfte, waere der Test darunter gruen und wertlos —
    die klassische Art, wie ein Waechter unbemerkt aufhoert zu wachen.
    """
    gefunden = _alle_kennungen()
    assert len(gefunden) >= len(befunde.PRUEFUNGEN), (
        f"Nur {len(gefunden)} Kennungen gefunden bei "
        f"{len(befunde.PRUEFUNGEN)} Pruefungen - der Sammler uebersieht etwas."
    )
    # Stichproben aus jedem Bereich, damit ein kaputter Sammler auffaellt.
    for pflicht in (
        "dienst.nicht_erreichbar",
        "platz.knapp",
        "nachschub.haengt",
        "bibliothek.geisterposten",
        "betrieb.mail_haengt",
    ):
        assert pflicht in gefunden, pflicht


@pytest.mark.parametrize("sprache", ["de", "en"])
def test_jeder_befund_hat_titel_und_folge(sprache: str) -> None:
    daten = _sprachdatei(sprache)
    fehlend = [
        f"befund.{kennung}.{teil}"
        for kennung in sorted(_alle_kennungen())
        for teil in ("titel", "folge")
        if not _hat_text(daten, f"befund.{kennung}.{teil}")
    ]
    assert fehlend == [], (
        f"In {sprache}.json fehlen diese Texte - die Oberflaeche zeigt dann "
        f"den rohen Punktpfad an:\n  " + "\n  ".join(fehlend)
    )


def test_beide_sprachen_kennen_dieselben_befund_texte() -> None:
    """Ein Schluessel, den es nur auf Deutsch gibt, faellt sonst erst spaeter auf."""

    def flach(daten: dict, praefix: str = "") -> set[str]:
        gefunden = set()
        for schluessel, wert in daten.items():
            name = f"{praefix}.{schluessel}" if praefix else schluessel
            if isinstance(wert, dict):
                gefunden |= flach(wert, name)
            else:
                gefunden.add(name)
        return gefunden

    de = {k for k in flach(_sprachdatei("de")) if k.startswith("befund.")}
    en = {k for k in flach(_sprachdatei("en")) if k.startswith("befund.")}
    assert de == en, f"nur de: {sorted(de - en)} | nur en: {sorted(en - de)}"
