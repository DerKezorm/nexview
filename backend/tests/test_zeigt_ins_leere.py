"""Aufrufe, die ins Leere zeigen.

⚠️ **Dieser Test ist die Lehre aus einem ausgelieferten Fehler.**

In 0.19.0 zog die Haushalts-Bewertung vom Auftrag auf den Titel um. Dabei
wurde ``services/ratings.py`` vollstaendig neu geschrieben - vorher lagen dort
die Portal-Wertungen aus Radarr. Der Aufrufer in ``routers/details.py`` blieb
stehen:

    gefunden = await ratings.for_movies(settings, kennungen)

Die Funktion gab es nicht mehr. Python merkt so etwas erst, wenn die Zeile
laeuft - und dann heisst es 500. Zwei Versionen lang endete jede Anfrage an
``/api/ratings/movie`` in einem Serverfehler, ohne dass irgendetwas rot wurde.

Hier wird deshalb der ganze Quelltext daraufhin durchgesehen: Jeder Zugriff
der Form ``modul.name`` muss es in diesem Modul auch geben. Beim ersten Lauf
fand die Probe genau einen Treffer - den echten - und **keinen einzigen
Fehlalarm**. Sie ist also scharf genug, um sie scharf zu lassen.

Was sie nicht findet: Aufrufe ueber Objekte statt ueber Module, und alles, was
zur Laufzeit zusammengesetzt wird (``getattr``). Das ist der Preis dafuer,
dass sie ohne Ausnahmeliste auskommt.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent.parent
APP = BACKEND / "app"


def _paket_von(datei: pathlib.Path) -> str:
    """Das Paket, aus dessen Sicht ein relativer Import aufgeloest wird."""
    modul = datei.relative_to(BACKEND).with_suffix("").as_posix().replace("/", ".")
    if datei.name == "__init__.py":
        return modul.removesuffix(".__init__")
    return modul.rsplit(".", 1)[0]


def _module_im_blick(baum: ast.Module, paket: str) -> dict[str, object]:
    """Welcher Name im Code steht fuer welches **Modul**?

    Nur Module interessieren. Wird eine Klasse oder Funktion importiert, ist
    ihr Inhalt hier nicht zu pruefen - dafuer gibt es die Typpruefung.
    """
    gefunden: dict[str, object] = {}
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.ImportFrom):
            continue
        for name in knoten.names:
            pfad = "." * knoten.level + (knoten.module or "") + "." + name.name
            try:
                ziel = importlib.import_module(pfad, package=paket)
            except Exception:
                # Kein Modul, sondern eine Klasse oder Funktion - nicht unser Fall.
                continue
            gefunden[name.asname or name.name] = ziel
    return gefunden


def test_kein_aufruf_zeigt_ins_leere() -> None:
    befunde: list[str] = []

    for datei in sorted(APP.rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue

        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        module = _module_im_blick(baum, _paket_von(datei))
        if not module:
            continue

        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Attribute) or not isinstance(knoten.value, ast.Name):
                continue
            ziel = module.get(knoten.value.id)
            if ziel is not None and not hasattr(ziel, knoten.attr):
                befunde.append(
                    f"{datei.relative_to(BACKEND)}:{knoten.lineno} - "
                    f"{knoten.value.id}.{knoten.attr} gibt es in "
                    f"{getattr(ziel, '__name__', ziel)} nicht"
                )

    assert not befunde, "Aufrufe ins Leere:\n  " + "\n  ".join(befunde)
