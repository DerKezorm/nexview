"""Jede Fehlerantwort traegt eine Kennung - und die Ausnahmen sind gezaehlt.

⚠️ **Die andere Richtung zu ``test_fehlermeldungen.py``.** Der prueft: Jede
Kennung hat einen Text in beiden Sprachen. Was er **nicht** pruefen kann: ob
eine Meldung ueberhaupt eine Kennung *hat*. Ein roher Satz kommt dort gar
nicht vor - er ist unsichtbar, gerade weil er keine Kennung traegt.

Die Folge steht in ``app/meldungen.py``: Der Server kennt die eingestellte
Sprache nicht. Er **benennt** einen Fehler, und die Oberflaeche baut den Satz.
Wer stattdessen ``detail="Deutscher Satz."`` schreibt, reicht diesen Satz
unveraendert durch (``client.ts``: ein String-``detail`` wird nicht
uebersetzt) - und ein englischer Nutzer liest Deutsch.

⚠️ **Die Liste unten ist Schulden, keine Erlaubnis.** Sie haelt fest, was heute
noch ohne Kennung dasteht, damit nichts **Neues** dazukommt. Jeder Eintrag,
der verschwindet, ist eine Meldung mehr, die in der Sprache des Lesers
ankommt. Wer einen entfernt, streicht ihn hier - und der Test besteht darauf,
dass die Liste nicht groesser wird und keine Karteileiche enthaelt.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

#: Noch ohne Kennung - Stand der Bestandsaufnahme.
#:
#: Alle uebrigen treffen **den Betreiber** in den Einstellungen, nicht den
#: gewoehnlichen Nutzer. Die drei, die einen Menschen beim ersten Kontakt mit
#: Nexview trafen (Passwort zu kurz am Einladungslink, abgelaufener Link, der
#: Stoeber-Assistent), sind bereits umgestellt.
NOCH_OHNE_KENNUNG = {
    # Englisch und technisch - eine unbekannte Adresse unter /api. Der Satz
    # geht nie in ein Fehlerbanner, sondern ist die schlichte 404-Antwort auf
    # einen Tippfehler in der Adresszeile.
    ("main.py", "Not Found"),
}


def _texte(knoten: ast.AST) -> str:
    """Den Text eines ``detail=`` zusammensetzen - auch bei f-Strings."""
    if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
        return knoten.value
    if isinstance(knoten, ast.JoinedStr):
        teile = []
        for stueck in knoten.values:
            if isinstance(stueck, ast.Constant):
                teile.append(str(stueck.value))
            elif isinstance(stueck, ast.FormattedValue):
                teile.append("{" + ast.unparse(stueck.value) + "}")
        return "".join(teile)
    return ""


def _ohne_kennung() -> list[tuple[str, str, int]]:
    """Alle ``HTTPException(detail=<roher Text>)`` im Backend."""
    gefunden: list[tuple[str, str, int]] = []
    for datei in sorted(APP.rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue
        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Call):
                continue
            funktion = knoten.func
            name = (
                funktion.attr
                if isinstance(funktion, ast.Attribute)
                else getattr(funktion, "id", "")
            )
            if name not in ("HTTPException", "StarletteHTTPException"):
                continue
            for schluesselwort in knoten.keywords:
                if schluesselwort.arg != "detail":
                    continue
                text = _texte(schluesselwort.value)
                if text:
                    gefunden.append((datei.name, text, knoten.lineno))
    return gefunden


def _passt(datei: str, text: str) -> tuple[str, str] | None:
    for eintrag in NOCH_OHNE_KENNUNG:
        if eintrag[0] == datei and eintrag[1] in text:
            return eintrag
    return None


def test_die_probe_findet_ueberhaupt_meldungen() -> None:
    """⚠️ Ein Waechter, der nichts sieht, meldet lebenslang "alles in Ordnung"."""
    # ⚠️ Die Zahl ist absichtlich klein: Es ist nur noch **eine** Stelle
    # uebrig. Der Selbsttest prueft deshalb nicht die Menge, sondern dass der
    # Ausdruck ueberhaupt greift - mit einem Text, den es nachweislich gibt.
    treffer = _ohne_kennung()
    assert any(datei == "main.py" for datei, _text, _zeile in treffer), (
        "Die bekannte Stelle in main.py wurde nicht gefunden - dann greift der "
        "Ausdruck ins Leere und die Pruefung darunter waere wertlos."
    )


def test_keine_neue_meldung_ohne_kennung() -> None:
    """Neue Fehlerantworten brauchen eine Kennung.

    Schlaegt der Test an, ist die Loesung nicht, den Text unten einzutragen,
    sondern ihn zu benennen::

        detail=meldungen.meldung("kennung", "Deutscher Satz.")

    und je einen Eintrag unter ``errors.byCode.kennung`` in ``de.json`` und
    ``en.json`` zu ergaenzen.
    """
    neu = [
        f"{datei}:{zeile}  {text[:70]}"
        for datei, text, zeile in _ohne_kennung()
        if _passt(datei, text) is None
    ]
    assert not neu, (
        "Diese Fehlerantworten tragen keine Kennung und kommen deshalb bei "
        "jedem auf Deutsch an, egal welche Sprache er eingestellt hat:\n  "
        + "\n  ".join(sorted(neu))
    )


def test_die_schuldenliste_hat_keine_karteileichen() -> None:
    """Wer eine Meldung umstellt, soll sie hier streichen.

    Sonst waechst die Liste nie, und beim naechsten Lesen glaubt jemand, es
    seien mehr offene Stellen als es gibt.
    """
    getroffen = {
        eintrag
        for datei, text, _ in _ohne_kennung()
        if (eintrag := _passt(datei, text)) is not None
    }
    verwaist = sorted(NOCH_OHNE_KENNUNG - getroffen)
    assert not verwaist, (
        f"In NOCH_OHNE_KENNUNG stehen Meldungen, die es nicht mehr gibt: {verwaist}. "
        "Wurde die Meldung umgestellt? Dann den Eintrag hier streichen."
    )
