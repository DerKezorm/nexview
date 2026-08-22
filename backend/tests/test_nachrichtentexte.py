"""Jeder Nachrichten-Schluessel braucht einen Text - in **beiden** Sprachen.

Der Anlass war ein Befund aus dem Betrieb: In der Glocke stand woertlich
``notifications.storageReleaseRequested``. Der Benachrichtigungstyp war
angelegt, der Aufruf stand, nur der Textbaustein fehlte - und das faellt beim
Bauen nicht auf, weil i18next einen unbekannten Schluessel klaglos als Text
ausgibt.

Das ist eine ganze Fehlerklasse: Sie trifft jede neue Benachrichtigung, sie
zeigt sich erst im laufenden Betrieb, und sie sieht fuer den Empfaenger wie ein
Programmfehler aus. Deshalb wird sie hier maschinell zugehalten.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
BACKEND = WURZEL / "backend" / "app"
SPRACHEN = WURZEL / "frontend" / "src" / "i18n"


def _schluessel_im_backend() -> set[str]:
    """Alle ``message_key="..."`` aus dem Quelltext."""
    gefunden: set[str] = set()
    for datei in BACKEND.rglob("*.py"):
        text = datei.read_text(encoding="utf-8")
        gefunden.update(re.findall(r'message_key\s*=\s*"([^"]+)"', text))
    return gefunden


def _text(daten: dict, schluessel: str) -> object:
    stelle: object = daten
    for teil in schluessel.split("."):
        if not isinstance(stelle, dict):
            return None
        stelle = stelle.get(teil)
    return stelle


def test_jeder_nachrichtenschluessel_hat_einen_text() -> None:
    schluessel = _schluessel_im_backend()
    assert schluessel, "Kein einziger Nachrichten-Schluessel gefunden - stimmt der Pfad?"

    for sprache in ("de", "en"):
        daten = json.loads((SPRACHEN / f"{sprache}.json").read_text(encoding="utf-8"))
        fehlend = sorted(s for s in schluessel if not isinstance(_text(daten, s), str))
        assert not fehlend, (
            f"Ohne Text in {sprache}.json: {fehlend}. "
            "i18next gibt den Schluessel dann woertlich in der Glocke aus."
        )


def test_kein_nachrichtentext_traegt_platzhalter() -> None:
    """⚠️ Der Titel gehoert in ``message_title``, nicht in den Baustein.

    Die Benachrichtigungen werden ohne Variablen uebersetzt. Steht ein
    ``{{name}}`` im Text, erscheinen die geschweiften Klammern woertlich in der
    Glocke - genauso haesslich wie ein fehlender Text, nur schwerer zu finden.
    """
    schluessel = _schluessel_im_backend()
    for sprache in ("de", "en"):
        daten = json.loads((SPRACHEN / f"{sprache}.json").read_text(encoding="utf-8"))
        mit_platzhalter = sorted(
            s for s in schluessel if "{{" in str(_text(daten, s) or "")
        )
        assert not mit_platzhalter, (
            f"Platzhalter in {sprache}.json: {mit_platzhalter}. "
            "Der Titel gehoert in message_title."
        )
