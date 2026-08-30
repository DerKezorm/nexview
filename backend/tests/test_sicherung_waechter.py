"""Der Waechter: Findet die naechste Luecke in der Sicherung, bevor jemand sie braucht.

⚠️ **Warum es diese Datei gibt.** Alle anderen Tests zur Sicherung pruefen
**benannte** Bestandteile: "ist die Datenbank drin, ist der Schluessel drin,
sind die Bilder drin". Auf Tabellenebene ist das richtig - ``VACUUM INTO``
kopiert die ganze Datei, jede neue Tabelle faehrt von selbst mit. Auf
**Dateiebene** ist es der Grund, warum ``data/trash/`` unbemerkt fehlen konnte:
Es war nicht vergessen worden, es war nie gefragt worden.

Hier wird die Frage umgedreht. Nicht "ist X drin?", sondern: **Gibt es zu
allem, was im Datenverzeichnis liegt, eine Entscheidung?** Jeder Name muss
entweder in ``IM_ARCHIV`` stehen oder in ``NICHT_INS_ARCHIV`` - letzteres mit
ausgeschriebenem Grund.

⚠️ **Und die Regel dazu, ohne die der Waechter wertlos ist.** Wird dieser Test
rot, ist die Antwort **nicht**, einen Eintrag in ``NICHT_INS_ARCHIV``
nachzutragen. Rot heisst: Jemand hat etwas angelegt, ohne zu entscheiden, ob es
in eine Sicherung gehoert. Diese Entscheidung ist der ganze Zweck. Wem kein
Grund einfaellt, warum etwas draussen bleiben soll, der hat gerade
herausgefunden, dass es hineingehoert.

Geprueft wird von zwei Seiten, weil jede allein ein Loch hat:

* **Aus dem Quelltext**: Jede Stelle, die einen Pfad unter ``data_dir`` baut,
  muss einen zugeordneten Namen nennen. Faengt schon beim Schreiben des Codes -
  aber nur, was als Zeichenkette dasteht.
* **Aus dem Verzeichnis**: Was nach einem Testlauf wirklich dort liegt, muss
  zugeordnet sein. Faengt auch, was ueber Umwege entsteht - aber nur, wenn ein
  Test es zufaellig angelegt hat.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

import pyzipper
import pytest

from app.config import get_settings
from app.services import sicherung


QUELLEN = Path(__file__).resolve().parent.parent / "app"

#: Stellen, die den Namen **berechnen** statt ihn hinzuschreiben - mit Grund.
#:
#: ⚠️ Dieselbe Regel wie oben: Diese Liste ist keine Ablage fuer alles, was der
#: Waechter nicht versteht. Ein berechneter Name heisst, dass niemand von aussen
#: sehen kann, was dort entsteht; das braucht eine Begruendung, die auf die
#: Stelle passt.
DYNAMISCH_ERLAUBT = {
    (
        "services/sicherung.py",
        "unter",
    ): "Laeuft ueber BEILAGEN, ist also genau die zugeordnete Liste (wiederherstellen).",
}

#: So viele Fundstellen gibt es mindestens.
#:
#: ⚠️ **Ohne diese Schwelle waere der Test still gruen, sobald er nichts mehr
#: findet** - etwa weil jemand den Zugriff auf das Datenverzeichnis anders
#: schreibt. Ein Waechter, der nichts sieht, meldet auch nichts.
MINDESTENS_FUNDSTELLEN = 6


def _konstanten(baum: ast.Module) -> dict[str, str]:
    """Zeichenketten-Konstanten auf Modulebene - fuer ``data_dir / ORDNER_NAME``."""
    werte: dict[str, str] = {}
    for knoten in baum.body:
        if not isinstance(knoten, ast.Assign):
            continue
        if not isinstance(knoten.value, ast.Constant) or not isinstance(knoten.value.value, str):
            continue
        for ziel in knoten.targets:
            if isinstance(ziel, ast.Name):
                werte[ziel.id] = knoten.value.value
    return werte


def _fundstellen() -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int]]]:
    """Alle ``… .data_dir / X`` im Quelltext - getrennt nach benannt und berechnet."""
    benannt: list[tuple[str, str, int]] = []
    berechnet: list[tuple[str, str, int]] = []

    for datei in sorted(QUELLEN.rglob("*.py")):
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        konstanten = _konstanten(baum)
        kurz = datei.relative_to(QUELLEN).as_posix()

        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.BinOp) or not isinstance(knoten.op, ast.Div):
                continue
            # Nur das erste Stueck hinter data_dir zaehlt: bei
            # ``data_dir / "trash" / datei`` ist das der innere Ausdruck.
            if not isinstance(knoten.left, ast.Attribute) or knoten.left.attr != "data_dir":
                continue

            rechts = knoten.right
            if isinstance(rechts, ast.Constant) and isinstance(rechts.value, str):
                benannt.append((kurz, rechts.value, knoten.lineno))
            elif isinstance(rechts, ast.Name) and rechts.id in konstanten:
                benannt.append((kurz, konstanten[rechts.id], knoten.lineno))
            else:
                berechnet.append((kurz, ast.unparse(rechts), knoten.lineno))

    return benannt, berechnet


class TestAusDemQuelltext:
    def test_der_waechter_sieht_ueberhaupt_etwas(self) -> None:
        """⚠️ Zuerst: Findet er noch, wonach er sucht?"""
        benannt, berechnet = _fundstellen()
        gesamt = len(benannt) + len(berechnet)
        assert gesamt >= MINDESTENS_FUNDSTELLEN, (
            f"Nur {gesamt} Zugriffe auf das Datenverzeichnis gefunden, erwartet "
            f"mindestens {MINDESTENS_FUNDSTELLEN}. Entweder wird jetzt anders darauf "
            "zugegriffen - dann muss dieser Test das lernen - oder er prueft nichts mehr."
        )

    def test_jeder_ort_im_quelltext_ist_zugeordnet(self) -> None:
        """Jede Stelle, die etwas ins Datenverzeichnis legt, muss entschieden sein."""
        benannt, _ = _fundstellen()
        bekannt = set(sicherung.IM_ARCHIV) | set(sicherung.NICHT_INS_ARCHIV)

        offen = [(d, name, z) for d, name, z in benannt if name not in bekannt]
        assert not offen, (
            "Diese Orte im Datenverzeichnis sind weder im Archiv noch ausdruecklich "
            f"davon ausgenommen: {offen}\n\n"
            "Der Eintrag gehoert NICHT routinemaessig nach NICHT_INS_ARCHIV. "
            "Erst entscheiden: Wuerde ein Betreiber das nach einem Plattencrash "
            "vermissen? Dann in BEILAGEN. Sonst nach NICHT_INS_ARCHIV, mit Grund."
        )

    def test_berechnete_namen_sind_einzeln_begruendet(self) -> None:
        """⚠️ Ein berechneter Name ist von aussen nicht nachzulesen.

        Deshalb reicht hier nicht, dass er existiert - es muss dabeistehen,
        warum an dieser Stelle kein fester Name moeglich ist.
        """
        _, berechnet = _fundstellen()
        offen = [
            (datei, ausdruck, zeile)
            for datei, ausdruck, zeile in berechnet
            if (datei, ausdruck) not in DYNAMISCH_ERLAUBT
        ]
        assert not offen, (
            f"Berechnete Pfade unter data_dir ohne Begruendung: {offen}\n"
            "Entweder einen festen Namen verwenden oder in DYNAMISCH_ERLAUBT "
            "eintragen - mit einem Grund, der auf diese Stelle passt."
        )


class TestAusDemVerzeichnis:
    def test_nichts_liegt_unzugeordnet_herum(self) -> None:
        """Was wirklich dort liegt, muss ebenfalls entschieden sein."""
        offen = sicherung.nicht_zugeordnet()
        assert not offen, (
            f"Im Datenverzeichnis liegt Unzugeordnetes: {offen}\n\n"
            "Es ist nicht in der Sicherung und steht auch nicht auf der Liste "
            "der bewussten Ausnahmen. Beides zusammen heisst: Niemand hat "
            "entschieden, was im Ernstfall damit passieren soll."
        )

    def test_der_waechter_meldet_wirklich(self, tmp_path: Path) -> None:
        """⚠️ Sonst waere der Test darueber gruen, weil er nichts prueft.

        Ein leeres Ergebnis kann heissen "alles zugeordnet" - oder "die Pruefung
        sieht nichts". Diese Zeile trennt die beiden Faelle.
        """
        (tmp_path / "irgendwas-neues").mkdir()
        assert sicherung.nicht_zugeordnet(tmp_path) == ["irgendwas-neues"]

    def test_zugeordnetes_meldet_er_nicht(self, tmp_path: Path) -> None:
        for name in sicherung.IM_ARCHIV:
            (tmp_path / name).mkdir()
        for name in sicherung.NICHT_INS_ARCHIV:
            (tmp_path / name).mkdir()
        assert sicherung.nicht_zugeordnet(tmp_path) == []


class TestDieZusageStimmt:
    """⚠️ Die Liste ist nur so viel wert wie das, was das Archiv wirklich tut.

    ``IM_ARCHIV`` ist eine Behauptung. Ohne diese Pruefung koennte jemand einen
    Namen eintragen, ohne dass je etwas gepackt wird - und der Waechter oben
    waere zufrieden.
    """

    def test_alles_aus_im_archiv_ist_auch_wirklich_drin(self, tmp_path: Path) -> None:
        daten = get_settings().data_dir
        # Die Beilagen muessen existieren, sonst kann nichts von ihnen mitkommen.
        for unter in sicherung.BEILAGEN:
            (daten / unter).mkdir(parents=True, exist_ok=True)
            (daten / unter / "probe.txt").write_bytes(b"probe")

        try:
            pfad = sicherung.anlegen(art=sicherung.MANUELL)
            rohdaten = sicherung.archiv(pfad.name, "ein-langes-testpasswort")
        finally:
            for unter in sicherung.BEILAGEN:
                (daten / unter / "probe.txt").unlink(missing_ok=True)

        zip_datei = pyzipper.AESZipFile(io.BytesIO(rohdaten))
        zip_datei.setpassword(b"ein-langes-testpasswort")
        try:
            namen = set(zip_datei.namelist())
        finally:
            zip_datei.close()

        fehlend = []
        for eintrag in sicherung.IM_ARCHIV:
            if eintrag == "secret.key":
                # ⚠️ Der einzige Eintrag, der fehlen darf - und nur dann, wenn
                # der Schluessel aus der Umgebungsvariablen kommt. Dann liegt
                # statt seiner der Hinweis darauf im Archiv, und der zaehlt.
                if "secret.key" in namen or "SCHLUESSEL-FEHLT.txt" in namen:
                    continue
                fehlend.append(eintrag)
            elif eintrag in namen or any(n.startswith(f"{eintrag}/") for n in namen):
                continue
            else:
                fehlend.append(eintrag)

        assert not fehlend, (
            f"IM_ARCHIV verspricht {fehlend}, im Archiv steht davon nichts. "
            f"Enthalten ist: {sorted(namen)}"
        )
        sicherung.entfernen(pfad)


@pytest.fixture(autouse=True)
def _aufraeumen():
    """Was dieser Test anlegt, raeumt er weg.

    ⚠️ Sonst prueft der naechste Testlauf gegen die Reste des vorigen - und der
    Waechter meldete ausgerechnet die Dateien, die er selbst hinterlassen hat.
    """
    yield
    import shutil

    from app.services import trash

    ordner = sicherung.ordner()
    if ordner.is_dir():
        for eintrag in list(ordner.glob("*")):
            if eintrag.is_dir():
                shutil.rmtree(eintrag, ignore_errors=True)
            else:
                eintrag.unlink(missing_ok=True)
    shutil.rmtree(get_settings().data_dir / "trash", ignore_errors=True)
    trash.schnappschuss.cache_clear()
