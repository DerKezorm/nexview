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
der Form ``modul.name`` muss es in diesem Modul auch geben.

⚠️ **Und der Waechter selbst war zwei Versionen lang halb blind.** Er loeste
214 Modulnamen auf, wo es 592 sind: ``from .. import X`` fiel wegen eines
Punktes zu viel im Importpfad heraus (65 eigene Module, darunter
``meldungen``, ``notify``, ``quota``, ``storage``, ``sicherung``, ``library``,
``mail``), und ``import X`` sah er gar nicht erst an (25 fremde Module,
darunter ``httpx``, ``jwt``, ``bcrypt``, ``json``, ``sqlite3``). Beides ist
repariert; dass es repariert **bleibt**, sichern zwei Dinge:
``test_der_scan_sieht_beide_importformen`` und die Bodenschwelle
``MINDESTENS_MODULE``. Die Schwelle hat hier als einziger der Waechter
gefehlt - deshalb fiel der Ausfall auch niemandem auf.

Was sie nicht findet: Aufrufe ueber Objekte statt ueber Module, und alles, was
zur Laufzeit zusammengesetzt wird (``getattr``). Das ist der Preis dafuer,
dass sie fast ohne Ausnahmeliste auskommt.
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

    ⚠️ **Beide Zweige hier waren einmal kaputt, und beide still.**

    ``from .. import meldungen``: Der Pfad wurde als ``"." * level + module +
    "." + name`` gebaut. Bei ``from ..`` ist ``module`` aber ``None``, und
    daraus wurde ``"...meldungen"`` - ein Punkt zu viel. Der ``ImportError``
    fiel ins nackte ``except``, der Name verschwand. So waren **65 eigene
    Module** unsichtbar, darunter ``meldungen`` (in 25 Routern), ``notify``,
    ``quota``, ``storage``, ``sicherung``, ``library`` und ``mail``.

    ``import httpx``: gar nicht erst angesehen - der Scan kannte nur
    ``ast.ImportFrom``. Damit fehlten weitere **25 fremde Module**, unter
    anderem ``httpx``, ``jwt``, ``bcrypt``, ``json``, ``sqlite3``,
    ``pyzipper``, ``smtplib`` und ``ssl``.

    Gegen beides steht jetzt eine Bodenschwelle (``MINDESTENS_MODULE``): Ein
    Waechter, der nichts mehr einsammelt, meldet auch nichts.
    """
    gefunden: dict[str, object] = {}
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for name in knoten.names:
                # ``import a.b`` bindet ``a``, ``import a.b as c`` bindet ``c``.
                gebunden = name.asname or name.name.split(".")[0]
                pfad = name.name if name.asname else gebunden
                try:
                    gefunden[gebunden] = importlib.import_module(pfad)
                except Exception:  # noqa: BLE001, S112 - ein Modul, das sich nicht laden laesst, ist nicht unser Fall
                    continue
            continue
        if not isinstance(knoten, ast.ImportFrom):
            continue
        for name in knoten.names:
            # ⚠️ Der Punkt gehoert an ``module``, nicht davor: ``from .. import
            # meldungen`` hat gar kein ``module`` und ergibt ``..meldungen``.
            pfad = "." * knoten.level + (knoten.module + "." if knoten.module else "") + name.name
            try:
                ziel = importlib.import_module(pfad, package=paket)
            except Exception:  # noqa: BLE001, S112 - siehe oben
                # Kein Modul, sondern eine Klasse oder Funktion - nicht unser Fall.
                continue
            gefunden[name.asname or name.name] = ziel
    return gefunden


#: Zugriffe, die es zur Ladezeit wirklich nicht gibt - mit Grund.
#:
#: ⚠️ Keine Ablage fuer alles, was gerade rot ist. Ein Eintrag hier heisst:
#: "Ich habe nachgesehen, und dieser Zugriff geht zur Laufzeit gut." Der
#: uebliche Fall ist ein **Untermodul**, das erst importiert wird, wenn es
#: gebraucht wird - ``os.path`` steht nach ``import os`` bereit, andere nicht.
NICHT_ZUR_LAUFZEIT: set[str] = set()


def _befunde() -> tuple[list[str], int]:
    """Alle Aufrufe ins Leere - und wie viele Module ueberhaupt aufgeloest wurden."""
    befunde: list[str] = []
    aufgeloest = 0

    for datei in sorted(APP.rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue

        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        module = _module_im_blick(baum, _paket_von(datei))
        aufgeloest += len(module)
        if not module:
            continue

        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Attribute) or not isinstance(knoten.value, ast.Name):
                continue
            ziel = module.get(knoten.value.id)
            if ziel is None or hasattr(ziel, knoten.attr):
                continue
            if f"{knoten.value.id}.{knoten.attr}" in NICHT_ZUR_LAUFZEIT:
                continue
            befunde.append(
                f"{datei.relative_to(BACKEND)}:{knoten.lineno} - "
                f"{knoten.value.id}.{knoten.attr} gibt es in "
                f"{getattr(ziel, '__name__', ziel)} nicht"
            )

    return befunde, aufgeloest


#: So viele Modulnamen loest der Scan mindestens auf.
#:
#: ⚠️ **Diese Schwelle hat als einzige der Waechter gefehlt, und genau
#: deshalb blieb der Ausfall von 90 Modulen so lange unbemerkt.** Der Test war
#: gruen, weil er fast nichts ansah - und nichts sah aus wie in Ordnung.
MINDESTENS_MODULE = 400


def test_kein_aufruf_zeigt_ins_leere() -> None:
    befunde, aufgeloest = _befunde()
    assert not befunde, "Aufrufe ins Leere:\n  " + "\n  ".join(befunde)
    assert aufgeloest >= MINDESTENS_MODULE, (
        f"Nur {aufgeloest} Modulnamen aufgelöst, erwartet mindestens "
        f"{MINDESTENS_MODULE}. Der Wächter sieht offenbar nichts mehr."
    )


def test_der_scan_sieht_beide_importformen() -> None:
    """Die Mutationsprobe fuer beide Zweige - einzeln.

    ⚠️ **Ohne sie waere die Reparatur wieder nur eine Absicht.** Beide Formen
    waren still kaputt, und still heisst: Der Test blieb gruen. Hier wird an
    einem kuenstlichen Quelltext nachgewiesen, dass jede Form einen Treffer
    liefert.
    """
    fall_import = ast.parse("import json\n")
    module = _module_im_blick(fall_import, "app")
    assert "json" in module, "'import json' bleibt unsichtbar - ast.Import fehlt."
    assert not hasattr(module["json"], "GibtEsNichtMehr")

    # ``from .. import meldungen`` aus Sicht von ``app.routers`` - der Fall,
    # an dem der Pfad einen Punkt zu viel bekam.
    fall_relativ = ast.parse("from .. import meldungen\n")
    module = _module_im_blick(fall_relativ, "app.routers")
    assert "meldungen" in module, (
        "'from .. import meldungen' bleibt unsichtbar - der Importpfad wird "
        "wieder falsch gebaut (ein Punkt zu viel)."
    )
    assert hasattr(module["meldungen"], "meldung")

    # Und der Fall, der immer schon ging - als Gegenprobe, dass nichts
    # kaputtgegangen ist.
    fall_paket = ast.parse("from ..services import library\n")
    module = _module_im_blick(fall_paket, "app.routers")
    assert "library" in module
