"""Die Schluesseldatei gehoert zugeschlossen, und zwar auf jedem Weg dorthin.

⚠️ **Warum es diese Datei gibt.** ``secret.key`` entschluesselt die abgelegten
Zugaenge zu Radarr, Sonarr, TMDB und dem Mailserver. Im veroeffentlichten
Abbild gemessen (02.09.2026, ``ghcr.io/derkezorm/nexview:latest``) lag sie mit
``-rw-r--r--`` neben der Datenbank, also lesbar fuer jeden Prozess im Behaelter
und fuer jeden, der das eingehaengte Datenverzeichnis sieht.

**Was das ist und was nicht.** Es ist Haertung, kein geschlossenes Loch: Wer
``/data`` lesen darf, liest ohnehin ``nexview.db``. Die Verschluesselung der
Zugaenge ist aber genau fuer den anderen Fall gebaut - dass jemand die
**Datenbank allein** bekommt, als hochgeladene Kopie oder als Anhang in einer
Fehlermeldung. In dem Fall soll der Schluessel nicht die Datei daneben mit
denselben Rechten sein.

⚠️ **Der eigentliche Waechter ist der dritte Test.** Die beiden ersten pruefen
je einen Weg; der dritte geht das ganze Backend durch und verlangt, dass
**jede** Stelle, die ``key_file`` beschreibt, danach zuschliesst. Genau daran
haengt es: Es gab zwei solche Stellen, und die zweite (das Einspielen einer
Sicherung) waere bei einer Reparatur von Hand leicht uebersehen worden.
"""

from __future__ import annotations

import ast
import os
import stat
from pathlib import Path

import pytest

from app.config import Settings

APP_ORDNER = Path(__file__).resolve().parent.parent / "app"

#: Unter Windows kennt das Dateisystem diese Rechte nicht: ``chmod`` setzt dort
#: nur das Schreibschutz-Bit, und ``st_mode`` meldet 0o666 oder 0o444. Die
#: beiden Messungen unten sind deshalb ausdruecklich uebersprungen statt
#: heimlich abgeschwaecht - Nexview laeuft im Behaelter unter Linux, und dort
#: wird gemessen.
nur_posix = pytest.mark.skipif(
    os.name != "posix", reason="Dateirechte gibt es so nur unter POSIX"
)


def _modus(pfad: Path) -> int:
    return stat.S_IMODE(pfad.stat().st_mode)


@nur_posix
def test_die_frisch_erzeugte_datei_ist_zu(tmp_path: Path) -> None:
    """Beim ersten Start entsteht der Schluessel bereits zugeschlossen."""
    einstellungen = Settings(data_dir=tmp_path, secret_key="")
    schluessel = einstellungen.resolved_secret_key()

    assert schluessel, "Ohne Schluessel misst der Test nichts."
    assert einstellungen.key_file.exists()
    assert _modus(einstellungen.key_file) == 0o600, (
        f"secret.key steht auf {oct(_modus(einstellungen.key_file))}, erwartet 0o600."
    )


@nur_posix
def test_eine_offene_datei_wird_beim_start_zugeschlossen(tmp_path: Path) -> None:
    """Auch eine bestehende Installation wird zugeschlossen, nicht nur eine neue.

    ⚠️ **Der wichtigere der beiden Faelle.** Wer heute laeuft, hat die Datei
    mit 0644 auf der Platte. Wuerde nur beim Anlegen zugeschlossen, gaelte die
    Haertung ausschliesslich fuer Neuinstallationen - also fuer niemanden, der
    das Problem hat.
    """
    einstellungen = Settings(data_dir=tmp_path, secret_key="")
    einstellungen.key_file.write_text("ein-alter-schluessel", encoding="utf-8")
    einstellungen.key_file.chmod(0o644)

    # Die Gegenprobe zur Messung selbst: Liegt sie wirklich offen, bevor
    # Nexview sie anfasst? Ohne diese Zeile koennte das Dateisystem die
    # Rechte still erzwingen und der Test waere hohl.
    assert _modus(einstellungen.key_file) == 0o644

    zurueck = einstellungen.resolved_secret_key()

    assert zurueck == "ein-alter-schluessel", "Der Schluessel darf sich nicht ändern."
    assert _modus(einstellungen.key_file) == 0o600, (
        f"secret.key steht nach dem Start auf {oct(_modus(einstellungen.key_file))}, "
        "erwartet 0o600."
    )


#: So viele Stellen beschreiben die Schluesseldatei mindestens.
#:
#: ⚠️ **Ohne diese Schwelle waere der Waechter still gruen, sobald er nichts
#: mehr findet** - etwa weil jemand ``key_file`` umbenennt. Ein Waechter, der
#: nichts sieht, meldet auch nichts. Am 02.09.2026 gemessen: 2, in
#: ``config.resolved_secret_key`` und ``sicherung.einspielen``.
MINDESTENS_SCHREIBSTELLEN = 2

#: Und so viele Module muss er dabei gelesen haben.
MINDESTENS_MODULE = 90


def _schreibt_die_schluesseldatei(knoten: ast.AST) -> bool:
    """Schreibt diese Funktion an ``key_file``?

    Erkannt wird der Aufruf einer schreibenden Methode auf einem Ausdruck, der
    ``key_file`` enthaelt - ``einstellungen.key_file.write_text(...)`` ebenso
    wie ``self.key_file.write_bytes(...)``.
    """
    schreibend = {"write_text", "write_bytes", "open"}
    for inner in ast.walk(knoten):
        if not isinstance(inner, ast.Call):
            continue
        if not (isinstance(inner.func, ast.Attribute) and inner.func.attr in schreibend):
            continue
        for teil in ast.walk(inner.func.value):
            if isinstance(teil, ast.Attribute) and teil.attr == "key_file":
                return True
    return False


def _schliesst_zu(knoten: ast.AST) -> bool:
    for inner in ast.walk(knoten):
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "schluesseldatei_zuschliessen"
        ):
            return True
    return False


def test_jede_schreibstelle_schliesst_die_datei_zu() -> None:
    """Wer den Schluessel schreibt, schliesst ihn auch zu.

    ⚠️ **Die Frage ist umgedreht, wie beim Betreiber-Waechter.** Nicht "ist
    Stelle X in Ordnung?", sondern: **Gibt es zu jeder Stelle, die diese Datei
    anlegt, eine Entscheidung?** Eine Liste von Hand waere morgen
    unvollstaendig, und der zweite Weg (Einspielen einer Sicherung) stand schon
    einmal genau deshalb offen.
    """
    offen: list[str] = []
    gefunden = 0
    module = 0
    for datei in sorted(APP_ORDNER.rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue
        module += 1
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _schreibt_die_schluesseldatei(knoten):
                continue
            gefunden += 1
            if not _schliesst_zu(knoten):
                offen.append(f"{datei.relative_to(APP_ORDNER).as_posix()}::{knoten.name}")

    assert not offen, (
        "Diese Stellen schreiben secret.key, ohne sie danach zuzuschließen: "
        + ", ".join(sorted(offen))
        + ". Ruf dort `einstellungen.schluesseldatei_zuschliessen()` auf."
    )
    assert module >= MINDESTENS_MODULE, (
        f"Der Wächter hat nur {module} Module gelesen, erwartet mindestens "
        f"{MINDESTENS_MODULE}. Er sieht offenbar den Großteil des Backends nicht."
    )
    assert gefunden >= MINDESTENS_SCHREIBSTELLEN, (
        f"Nur {gefunden} Schreibstellen an secret.key gefunden, erwartet mindestens "
        f"{MINDESTENS_SCHREIBSTELLEN}. Der Wächter läuft offenbar leer."
    )


def test_der_waechter_wuerde_eine_offene_stelle_bemerken() -> None:
    """Die Mutationsprobe: Eine Schreibstelle ohne Zuschliessen muss auffallen.

    ⚠️ **Ohne sie waere der Test darueber eine Absichtserklaerung.** Ein
    Tippfehler in ``_schreibt_die_schluesseldatei`` liesse ihn leer laufen, und
    er bliebe fuer immer gruen - genau die Sorte Waechter, die hier schon
    einmal 90 Module uebersehen hat.
    """
    ohne = ast.parse(
        "def schreibt(einstellungen, wert):\n"
        "    einstellungen.key_file.write_text(wert, encoding='utf-8')\n"
    ).body[0]
    mit = ast.parse(
        "def schreibt(einstellungen, wert):\n"
        "    einstellungen.key_file.write_text(wert, encoding='utf-8')\n"
        "    einstellungen.schluesseldatei_zuschliessen()\n"
    ).body[0]
    harmlos = ast.parse(
        "def liest(einstellungen):\n"
        "    return einstellungen.key_file.read_text(encoding='utf-8')\n"
    ).body[0]

    assert _schreibt_die_schluesseldatei(ohne) and not _schliesst_zu(ohne)
    assert _schreibt_die_schluesseldatei(mit) and _schliesst_zu(mit)
    # Die Gegenprobe: Lesen ist kein Schreiben. Ein Waechter, der bei jedem
    # ``read_text`` anschlaegt, wird abgeschaltet und nimmt die echten Funde mit.
    assert not _schreibt_die_schluesseldatei(harmlos)
