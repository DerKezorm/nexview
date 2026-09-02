"""Der Wächter über die ruff-Konfiguration.

⚠️ **Warum es diese Datei gibt.** Beim Einbau von ruff stand die Regel RUF100
zur Wahl, die tote ``# noqa``-Kommentare meldet. Sie hielt beim Probelauf 75 der
damals 115 vorhandenen für überflüssig, und ``ruff check --fix`` hätte sie
gelöscht: samt der deutschen Begründung, die danebensteht. Betroffen wären unter
anderem alle neun ``# noqa: E402`` in ``conftest.py`` gewesen, die festhalten,
dass die Importe dort mit Absicht hinter den Umgebungsvariablen stehen, und die
acht ``# noqa: S608`` in ``db.py``, die sagen, warum ein zusammengebautes SQL
dort unbedenklich ist.

Ein Aufräum-Durchgang über 150 Dateien ist genau der Moment, in dem so etwas
niemandem auffällt. Deshalb steht RUF100 in der ``ignore``-Liste, und deshalb
gibt es diesen Test: Wer die Regel einschaltet, soll das bewusst tun und die
Begründungen vorher aussortiert haben, nicht nebenbei.

Der Test ist kein Verbot. Er ist eine Bodenschwelle: Sobald im Baum keine
begründeten ``noqa`` mehr stehen, darf RUF100 an.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
KONFIGURATION = BACKEND / "pyproject.toml"

# Unterhalb dieser Zahl lohnt der Schutz nicht mehr: Dann sind so wenige
# Begründungen übrig, dass man sie von Hand durchsehen kann.
NOCH_SCHUETZENSWERT = 20


def _konfiguration() -> dict:
    with KONFIGURATION.open("rb") as datei:
        return tomllib.load(datei)


def _noqa_im_baum() -> int:
    """Wie viele ``# noqa`` stehen heute im Python-Code?"""
    treffer = 0
    for ordner in ("app", "tests", "tools"):
        for datei in (BACKEND / ordner).rglob("*.py"):
            treffer += datei.read_text(encoding="utf-8").count("# noqa")
    return treffer


def test_die_konfiguration_ist_lesbar() -> None:
    """Bodenschwelle: Ohne sie prüfen die Tests darunter gegen ein leeres Wörterbuch."""
    ruff = _konfiguration().get("tool", {}).get("ruff", {})
    assert ruff, "backend/pyproject.toml hat keinen [tool.ruff]-Abschnitt mehr"
    assert ruff.get("required-version"), (
        "Die Fassungsgrenze ist weg. Ohne sie urteilt ein neueres ruff "
        "stillschweigend anders, und der Bau wird rot, ohne dass jemand etwas geändert hat."
    )


def test_ruf100_bleibt_aus_solange_begruendungen_im_baum_stehen() -> None:
    """RUF100 darf erst an, wenn die begründeten ``noqa`` durchgesehen sind."""
    vorhanden = _noqa_im_baum()
    if vorhanden < NOCH_SCHUETZENSWERT:
        return

    ignoriert = _konfiguration()["tool"]["ruff"]["lint"]["ignore"]
    assert "RUF100" in ignoriert, (
        f"Im Baum stehen {vorhanden} ``# noqa``-Kommentare, viele davon mit einer "
        "deutschen Begründung daneben. RUF100 hält einen großen Teil davon für tot "
        "und ``ruff check --fix`` löscht sie samt Begründung.\n\n"
        "Wenn du die Regel wirklich einschalten willst: erst die toten aussortieren, "
        "dann diesen Test anpassen. Nicht umgekehrt."
    )


def test_die_ausnahmen_tragen_ihre_begruendung() -> None:
    """Jede ignorierte Regel braucht ihre eigene Marke in derselben Zeile.

    ⚠️ **Warum in derselben Zeile und nicht als Block darüber.** Der erste
    Entwurf dieses Tests hat auf einen Kommentar irgendwo über der Regel
    geachtet. Das ist nicht prüfbar: Bei einer Gruppe wie DTZ005/DTZ011/DTZ901
    unter einer gemeinsamen Erklärung lässt sich eine hinten angehängte vierte
    Regel nicht von einem Gruppenmitglied unterscheiden. Die Mutationsprobe hat
    genau das gezeigt, der Test blieb grün.

    Deshalb trägt jede Regel jetzt einen eigenen Halbsatz hinter sich. Die
    ausführliche Begründung darf weiter als Block darüberstehen.
    """
    text = KONFIGURATION.read_text(encoding="utf-8")
    beginn = text.index("ignore = [")
    ende = text.index("\n]", beginn)
    block = text[beginn:ende]

    ohne_marke = [
        zeile.strip()
        for zeile in block.splitlines()[1:]
        if zeile.strip().startswith('"') and "#" not in zeile
    ]

    assert not ohne_marke, (
        f"Diese ignorierten Regeln tragen keine Marke in ihrer Zeile: {ohne_marke}. "
        "Eine Ausnahme ohne Begründung ist in einem Jahr nicht mehr zu beurteilen "
        "und wird dann geerbt statt entschieden. Ein Halbsatz reicht."
    )
