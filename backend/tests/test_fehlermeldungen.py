"""Jede Fehler-Kennung braucht einen Text - in **beiden** Sprachen.

Der Anlass war ein Befund aus dem Betrieb: Die Oberflaeche stand auf Englisch,
und beim ersten Vertipper kam "Benutzername oder Passwort ist falsch." Die
Oberflaeche war seit jeher zweisprachig; die Fehlermeldungen des Servers waren
es nie, weil das Backend gar kein Uebersetzungssystem hatte. Aufgefallen ist es
niemandem, weil man einen Fehler ausloesen muss, um einen zu sehen.

Das ist eine ganze Fehlerklasse, und sie ist von der gleichen Art wie die in
``test_nachrichtentexte.py``: Sie faellt beim Bauen nicht auf, sie zeigt sich
erst im Betrieb, und sie trifft immer den ungeschicktesten Moment - den, in
dem ohnehin schon etwas schiefgegangen ist. Deshalb wird sie hier maschinell
zugehalten.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
BACKEND = WURZEL / "backend" / "app"
SPRACHEN = WURZEL / "frontend" / "src" / "i18n"

# Die Datei, die ``meldung`` und ``fehler`` **definiert**. Ihre Beispiele in
# den Doc-Strings sind keine echten Meldungen.
DEFINITION = BACKEND / "meldungen.py"

# Kennungen, deren Satz nicht durch blosses Einsetzen entsteht und die
# deshalb in ``client.ts`` unter ``MIT_EIGENER_LOGIK`` stehen statt unter
# ``errors.byCode``. Wer hier etwas eintraegt, muss dort nachsehen.
MIT_EIGENER_LOGIK = {
    # Braucht die Vorgangsnummer im Text.
    "internal_error",
    # Waehlt zwischen Sekunden und Minuten - "in 900 Sekunden" waere richtig
    # und trotzdem unbrauchbar.
    "too_many_attempts",
}

# ⚠️ Kennungen, deren Text **von aussen** kommt und deshalb nicht uebersetzbar
# ist: Was Plex, Jellyfin oder Emby als Fehler melden, meldet der fremde
# Server in seiner Sprache. Ihn hier zu uebersetzen hiesse, ihn zu erfinden.
# Diese Meldungen fallen bewusst auf ``message`` zurueck.
FREMDER_TEXT = {
    "mediaserver_unreachable",
}


def _kennungen_im_backend() -> set[str]:
    """Alle ``"code": "..."`` aus Fehler-Antworten des Backends.

    Bewusst ueber den Quelltext statt ueber eine gepflegte Liste: Eine Liste
    muss man nachziehen, und wer das vergisst, bekommt genau den Zustand
    zurueck, den dieser Test verhindern soll.
    """
    gefunden: set[str] = set()
    for datei in BACKEND.rglob("*.py"):
        if datei == DEFINITION:
            continue
        text = datei.read_text(encoding="utf-8")
        # meldungen.meldung("kennung", ...) und meldungen.fehler("kennung", ...)
        gefunden.update(re.findall(r'\b(?:meldung|fehler)\(\s*\n?\s*"([a-z0-9_]+)"', text))
        # Handgeschriebene Antworten: detail={"code": "kennung", ...}
        gefunden.update(re.findall(r'"code"\s*:\s*"([a-z0-9_]+)"', text))
        # ⚠️ Eigene Fehlerklassen, die eine Kennung tragen. Ohne diese Zeile
        # entgeht dem Waechter jede Kennung, die nicht direkt an
        # ``meldungen.meldung`` uebergeben wird - genau so ist beim
        # Sicherungs-Wiederherstellen eine Luecke entstanden.
        for klasse in ("SicherungFehler", "SchluesselFehler"):
            gefunden.update(re.findall(klasse + r'\(\s*\n?\s*"([a-z0-9_]+)"', text))
        # ⚠️ Kennungen, die als **Schluesselwort** uebergeben werden:
        # ``ArrError(..., code="arr_timeout", service=...)``. Diese Meldungen
        # nehmen einen eigenen Weg - sie landen in ``MediaRequest.error_detail``
        # und stehen von dort Wochen spaeter im Verlauf. Ohne diese Zeile bliebe
        # ausgerechnet die Gruppe ungeprueft, die am laengsten sichtbar ist.
        gefunden.update(re.findall(r'\bcode\s*=\s*"([a-z0-9_]+)"', text))
    return gefunden


def _text(daten: dict, schluessel: str) -> object:
    stelle: object = daten
    for teil in schluessel.split("."):
        if not isinstance(stelle, dict):
            return None
        stelle = stelle.get(teil)
    return stelle


def _sprache(name: str) -> dict:
    return json.loads((SPRACHEN / f"{name}.json").read_text(encoding="utf-8"))


def test_jede_fehlerkennung_hat_einen_text() -> None:
    """⚠️ Der eigentliche Waechter dieser Datei.

    Fehlt die Uebersetzung, faellt das Frontend auf den deutschen Text aus der
    Antwort zurueck - die Meldung ist also nicht kaputt, nur in der falschen
    Sprache. Genau deshalb braucht es diesen Test: Ohne ihn faellt es nie auf.
    """
    kennungen = _kennungen_im_backend() - MIT_EIGENER_LOGIK - FREMDER_TEXT
    assert kennungen, "Keine einzige Fehler-Kennung gefunden - stimmt der Pfad?"

    for sprache in ("de", "en"):
        daten = _sprache(sprache)
        fehlend = sorted(
            k for k in kennungen if not isinstance(_text(daten, f"errors.byCode.{k}"), str)
        )
        assert not fehlend, (
            f"Ohne Text unter errors.byCode in {sprache}.json: {fehlend}. "
            "Die Meldung erscheint dann auf Deutsch, auch wenn die Oberflaeche "
            "auf Englisch steht."
        )


def test_beide_sprachen_kennen_dieselben_kennungen() -> None:
    """Sonst schleicht sich eine Sprache davon, ohne dass es auffaellt."""
    de = _text(_sprache("de"), "errors.byCode") or {}
    en = _text(_sprache("en"), "errors.byCode") or {}
    assert isinstance(de, dict) and isinstance(en, dict)

    nur_de = sorted(set(de) - set(en))
    nur_en = sorted(set(en) - set(de))
    assert not nur_de, f"Nur in de.json: {nur_de}"
    assert not nur_en, f"Nur in en.json: {nur_en}"


def test_keine_uebersetzung_ohne_kennung_im_backend() -> None:
    """Aufgeraeumt halten: Was das Backend nicht mehr schickt, kann weg.

    Kein Schaden, aber Ballast - und beim naechsten Lesen die Frage, ob die
    Meldung noch irgendwo vorkommt.
    """
    kennungen = _kennungen_im_backend()
    de = _text(_sprache("de"), "errors.byCode") or {}
    assert isinstance(de, dict)

    verwaist = sorted(set(de) - kennungen)
    assert not verwaist, (
        f"Uebersetzt, aber vom Backend nirgends geschickt: {verwaist}."
    )


def test_kein_platzhalter_ohne_zahlen_dahinter() -> None:
    """Ein ``{{...}}``, das die Antwort nicht fuellt, erscheint woertlich.

    Geprueft wird nur, dass ueberhaupt jemand den Platzhalter fuellen koennte:
    Steht er im Text, muss die Kennung im Backend auch Zahlen mitschicken.
    """
    de = _text(_sprache("de"), "errors.byCode") or {}
    assert isinstance(de, dict)

    quelltext = "\n".join(
        datei.read_text(encoding="utf-8") for datei in BACKEND.rglob("*.py")
    )
    ohne_werte = []
    for kennung, text in de.items():
        for platzhalter in re.findall(r"\{\{\s*([a-z0-9_]+)", str(text)):
            if f"{platzhalter}=" not in quelltext and f'"{platzhalter}"' not in quelltext:
                ohne_werte.append(f"{kennung}:{{{{{platzhalter}}}}}")
    assert not ohne_werte, (
        f"Platzhalter ohne Wert in der Antwort: {ohne_werte}. "
        "Die geschweiften Klammern stehen dann woertlich in der Meldung."
    )
