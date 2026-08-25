"""Fehlermeldungen, die in der eingestellten Sprache ankommen.

⚠️ **Das Backend uebersetzt nicht - es benennt.**

Der Server kennt die eingestellte Sprache nicht. Sie liegt im ``localStorage``
des Browsers und wird nirgends mitgeschickt; auf der Anmeldeseite gibt es
nicht einmal ein Konto, an dem eine Sprache hinge. Ein Server, der hier
uebersetzt, muss raten - und laege genau bei der Person falsch, die bewusst
umgeschaltet hat. ``Accept-Language`` haette denselben Fehler.

Deshalb schickt jede Meldung eine **Kennung** mit. Den Satz baut das Frontend
daraus (``client.ts``, ``errors.byCode`` in ``de.json``/``en.json``), wo die
Sprache ohnehin feststeht.

Der deutsche Text bleibt trotzdem dabei. Er ist der Rueckfall fuer alles, was
die API ohne die Nexview-Oberflaeche benutzt, und fuer jede Kennung, deren
Uebersetzung noch fehlt - ein deutscher Satz im Fehlerbanner ist besser als
eine nackte Kennung.

**Eine neue Meldung anlegen:**

1. ``detail=meldung("kennung", "Deutscher Satz.")`` schreiben.
2. Je einen Eintrag unter ``errors.byCode.kennung`` in ``de.json`` **und**
   ``en.json``.

``test_fehlermeldungen.py`` laeuft ueber den ganzen Quelltext und schlaegt
fehl, sobald eine Kennung ohne Uebersetzung dasteht - in einer der beiden
Sprachen. Vergessen kann man es also nicht.

⚠️ **Warum ``meldung`` ein dict liefert und nicht gleich die Ausnahme.** Die
82 bestehenden Stellen rufen ``HTTPException`` in einem halben Dutzend
Schreibweisen auf - ein-, mehrzeilig, mit und ohne ``status_code``, manche
tief in einer Bedingung. Alle auf eine neue Aufrufform umzuschreiben waere ein
Umbau mit Risiko gewesen; so bleibt jeder Aufruf stehen, wie er ist, und nur
der Text davor wird umschlossen. Fuer neuen Code gibt es ``fehler()``.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def meldung(code: str, text: str, **zahlen: Any) -> dict[str, Any]:
    """Der Inhalt einer Fehlerantwort: Kennung, deutscher Rueckfall, Zahlen.

    ``zahlen`` landet unveraendert in der Antwort und steht dem Frontend als
    Platzhalter zur Verfuegung. So kommt ein Satz wie "noch {{rest}} uebrig"
    ohne Sonderbehandlung aus.

    >>> HTTPException(status_code=404, detail=meldung("not_found", "Nicht da."))
    """
    return {"code": code, "message": text, **zahlen}


def fehler(code: str, text: str, status_code: int = 400, **zahlen: Any) -> HTTPException:
    """Dasselbe als fertige Ausnahme - fuer neue Stellen die kuerzere Form.

    >>> raise fehler("quota_exhausted", "Dein Kontingent ist aufgebraucht.", 429, limit=5)
    """
    return HTTPException(status_code=status_code, detail=meldung(code, text, **zahlen))
