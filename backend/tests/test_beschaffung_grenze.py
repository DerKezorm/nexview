"""Der Waechter der Grenze: Ausserhalb von ``services/beschaffung/`` kennt
niemand Radarr, Sonarr oder nexcrate.

Vorbild ist ``services/mediaserver/``: Dort kennt ausserhalb des Pakets niemand
Plex, Jellyfin oder Emby, alle reden mit ``get_provider``. Fuer die Beschaffung
soll dasselbe gelten (Bauplan NEX-Modus, Abschnitt 3). Zwei Regeln:

1. **Kein Import** von ``services.radarr``, ``services.sonarr``,
   ``services.arr`` oder irgendeinem Modul namens ``nexcrate`` ausserhalb von
   ``services/beschaffung/`` - und **nichts aus dem Inneren der Grenze**:
   Draussen gibt es nur das Paket selbst (``get_beschaffung``, die Formen)
   und ``beschaffung.base``, nie ``beschaffung.arr`` oder ``beschaffung.nex``.
   Ohne diesen Zusatz waere der Umzug selbst die Luecke gewesen: Wer
   ``beschaffung.arr.library`` importiert, kennt Radarr genauso wie vorher.
2. **Kein Router nennt eine Instanz-Kennung woertlich** (``radarr-standard``,
   ``radarr-uhd``, ``sonarr-standard``, ``sonarr-uhd``, ``nexcrate``). Die
   Kennungen entstehen an genau einer Stelle; ein Router, der sie
   ausschreibt, weiss mehr ueber die Beschaffung, als er darf.

⚠️ **Die Ausnahmelisten sind leer, und so bleiben sie.** In Scheibe 0 standen
dort 31 Dateien mit ihrem Ziel; Scheibe 2 hat sie abgearbeitet. Wer hier einen
Eintrag **nachtraegt**, verschiebt die Grenze zurueck; das ist nie die Antwort
auf einen roten Lauf. Die Listen bleiben als Form stehen, damit der Test
``test_jede_ausnahme_wird_noch_gebraucht`` weiter greift.

⚠️ **Und eine Ausnahme, die nicht mehr gebraucht wird, macht den Lauf rot**
(``test_jede_ausnahme_wird_noch_gebraucht``). Sonst bliebe ein umgezogener
Eintrag stehen, und die Datei duerfte ab dann still wieder Radarr importieren.

Was der Scan nicht sieht: ``importlib.import_module`` mit zusammengesetztem
Namen und Kennungen, die zur Laufzeit gebaut werden (``f"radarr-{stufe}"``).
Dass er die drei Importformen sieht, beweist
``test_der_scan_sieht_alle_importformen``; dass er ueberhaupt etwas liest, die
Bodenschwelle ``MINDESTENS_DATEIEN``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

#: Das Paket, in dem Radarr, Sonarr und nexcrate bekannt sein duerfen.
GRENZE = APP / "services" / "beschaffung"

#: Module unter ``app.services``, die nur hinter der Grenze importiert werden.
VERBOTENE_DIENSTE = frozenset({"radarr", "sonarr", "arr"})

#: Ein Modul dieses Namens ist ueberall verboten, wo es auch liegt.
VERBOTENER_NAME = "nexcrate"

#: Das Paket der Grenze als Modulname. Von aussen erlaubt: es selbst und
#: ``base`` darin - alles andere ist das Innere eines Wegs.
GRENZPAKET = ("app", "services", "beschaffung")
OEFFENTLICH_IN_DER_GRENZE = frozenset({"base"})

#: Die Wege als Unterpakete der Grenze, auch die, die es noch nicht gibt.
WEGE = frozenset({"arr", "nex"})

#: Was wirklich in der Grenze liegt - Schreibweise genau so wie auf der Platte.
_INHALT_DER_GRENZE = frozenset(
    eintrag.stem if eintrag.suffix == ".py" else eintrag.name
    for eintrag in GRENZE.iterdir()
    if eintrag.name != "__pycache__"
)

KENNUNG = re.compile(r"^(?:(?:radarr|sonarr)-(?:standard|uhd)|nexcrate)$")

#: Unter so vielen Dateien liest der Scan nicht mehr, was er soll. Heute sind
#: es 151 (vor dem Umzug in Scheibe 2: 173 - 22 Dateien liegen seitdem hinter
#: der Grenze und zaehlen nicht mehr mit); faellt die Zahl darunter, hat sich
#: der Pfad verschoben, und ein gruener Lauf hiesse nur, dass nichts gelesen
#: wurde.
MINDESTENS_DATEIEN = 140

#: Dateien, die einen verbotenen Dienst importieren duerfen. Leer seit Scheibe 2.
#: Pfade relativ zu ``app/``, mit Schraegstrich.
AUSNAHMEN_IMPORT: dict[str, str] = {}

#: Router, die eine Instanz-Kennung ausschreiben duerfen. Leer seit Scheibe 2.
AUSNAHMEN_KENNUNG: dict[str, str] = {}


def _modulname(pfad: Path) -> str:
    teile = pfad.relative_to(APP.parent).with_suffix("").parts
    if teile[-1] == "__init__":
        teile = teile[:-1]
    return ".".join(teile)


def _paket(pfad: Path) -> str:
    """Das Paket, gegen das relative Importe dieser Datei aufgeloest werden."""
    name = _modulname(pfad)
    return name if pfad.name == "__init__.py" else name.rpartition(".")[0]


def _aufloesen(paket: str, knoten: ast.ImportFrom) -> str:
    if not knoten.level:
        return knoten.module or ""
    teile = paket.split(".")
    basis = teile[: len(teile) - (knoten.level - 1)]
    if knoten.module:
        basis.append(knoten.module)
    return ".".join(basis)


def _untermodul_der_grenze(name: str) -> bool:
    """Ist ``name`` in ``from app.services.beschaffung import name`` ein Modul?

    Sonst ist es ein Name aus ``__init__`` (``get_beschaffung``, eine Form),
    und den darf jeder importieren.
    """
    # ⚠️ **Nicht ueber das Dateisystem fragen.** Windows unterscheidet keine
    # Gross- und Kleinschreibung: ``(GRENZE / "ARR").is_dir()`` war dort wahr,
    # und der Name ``ARR`` aus ``__init__`` galt als das Paket ``arr``. Der
    # Waechter meldete einen Verstoss, den es nicht gab.
    return name in WEGE or name in _INHALT_DER_GRENZE


def _verboten(modul: str) -> bool:
    teile = modul.split(".")
    if VERBOTENER_NAME in teile:
        return True
    if tuple(teile[:3]) == GRENZPAKET and len(teile) > 3:
        return teile[3] not in OEFFENTLICH_IN_DER_GRENZE
    return len(teile) == 3 and teile[:2] == ["app", "services"] and teile[2] in VERBOTENE_DIENSTE


def verbotene_importe(quelle: str, pfad: Path) -> list[str]:
    """Jeder Import in ``quelle``, der hinter die Grenze greift."""
    paket = _paket(pfad)
    treffer: list[str] = []
    for knoten in ast.walk(ast.parse(quelle)):
        if isinstance(knoten, ast.Import):
            treffer += [a.name for a in knoten.names if _verboten(a.name)]
        elif isinstance(knoten, ast.ImportFrom):
            modul = _aufloesen(paket, knoten)
            if _verboten(modul):
                treffer.append(modul)
                continue
            # ``from app.services import radarr``: der Name ist das Modul.
            if tuple(modul.split(".")) == GRENZPAKET:
                treffer += [
                    f"{modul}.{a.name}"
                    for a in knoten.names
                    if _untermodul_der_grenze(a.name) and _verboten(f"{modul}.{a.name}")
                ]
                continue
            treffer += [
                f"{modul}.{a.name}" for a in knoten.names if _verboten(f"{modul}.{a.name}")
            ]
    return treffer


def woertliche_kennungen(quelle: str) -> list[str]:
    return [
        knoten.value
        for knoten in ast.walk(ast.parse(quelle))
        if isinstance(knoten, ast.Constant)
        and isinstance(knoten.value, str)
        and KENNUNG.match(knoten.value)
    ]


def _dateien() -> list[Path]:
    return sorted(
        p
        for p in APP.rglob("*.py")
        if "__pycache__" not in p.parts and GRENZE not in p.parents
    )


def _schluessel(pfad: Path) -> str:
    return pfad.relative_to(APP).as_posix()


def _importbrueche() -> dict[str, list[str]]:
    brueche = {}
    for pfad in _dateien():
        treffer = verbotene_importe(pfad.read_text(encoding="utf-8"), pfad)
        if treffer:
            brueche[_schluessel(pfad)] = treffer
    return brueche


def _kennungsbrueche() -> dict[str, list[str]]:
    brueche = {}
    for pfad in _dateien():
        if "routers" not in pfad.relative_to(APP).parts:
            continue
        treffer = woertliche_kennungen(pfad.read_text(encoding="utf-8"))
        if treffer:
            brueche[_schluessel(pfad)] = treffer
    return brueche


def test_der_scan_liest_genug_dateien() -> None:
    assert len(_dateien()) >= MINDESTENS_DATEIEN


def test_niemand_ausserhalb_der_grenze_importiert_arr_oder_nexcrate() -> None:
    neu = {datei: t for datei, t in _importbrueche().items() if datei not in AUSNAHMEN_IMPORT}
    assert not neu, (
        "Diese Dateien greifen hinter die Grenze services/beschaffung/. "
        f"Ueber die Grenze rufen statt Ausnahme eintragen: {neu}"
    )


def test_kein_router_nennt_eine_instanz_kennung() -> None:
    neu = {
        datei: t for datei, t in _kennungsbrueche().items() if datei not in AUSNAHMEN_KENNUNG
    }
    assert not neu, f"Router mit woertlicher Instanz-Kennung: {neu}"


def test_jede_ausnahme_wird_noch_gebraucht() -> None:
    importe = _importbrueche()
    kennungen = _kennungsbrueche()
    veraltet = sorted(
        [d for d in AUSNAHMEN_IMPORT if d not in importe]
        + [d for d in AUSNAHMEN_KENNUNG if d not in kennungen]
    )
    assert not veraltet, f"Ausnahme ohne Bruch, bitte streichen: {veraltet}"


def test_der_scan_sieht_alle_importformen() -> None:
    """Jede Schreibweise, mit der man an Radarr kommt, muss auffallen."""
    pfad = APP / "routers" / "beispiel.py"
    quelle = """
import app.services.radarr
from app.services import sonarr
from app.services.arr import arr_get
from ..services import arr
from ..services.radarr import radarr_client
from app.services.beschaffung.nex import nexcrate
from ..services.beschaffung.arr import library
from ..services.beschaffung import arr
from app.services.beschaffung.arr.weg import ArrBeschaffung
# Keine Treffer: aehnliche Namen und das, was die Grenze nach aussen zeigt.
from app.services import arr_bestand, library
from ..services.mediaserver import get_provider
from ..services.beschaffung import BeschaffungError, get_beschaffung
from ..services import beschaffung
from app.services.beschaffung.base import Korb
"""
    assert verbotene_importe(quelle, pfad) == [
        "app.services.radarr",
        "app.services.sonarr",
        "app.services.arr",
        "app.services.arr",
        "app.services.radarr",
        "app.services.beschaffung.nex",
        "app.services.beschaffung.arr",
        "app.services.beschaffung.arr",
        "app.services.beschaffung.arr.weg",
    ]
    dienst = APP / "services" / "beispiel.py"
    assert verbotene_importe("from .sonarr import x\nfrom . import radarr", dienst) == [
        "app.services.sonarr",
        "app.services.radarr",
    ]


def test_der_scan_sieht_woertliche_kennungen() -> None:
    quelle = 'a = "radarr-uhd"\nb = {"sonarr-standard": 1}\nc = "radarr"\nd = "nexcrate"'
    assert sorted(woertliche_kennungen(quelle)) == ["nexcrate", "radarr-uhd", "sonarr-standard"]
