"""Protokollmeldungen sind englisch - und bleiben es.

Ohne diesen Test zerfaellt die Regel bei der naechsten Funktion wieder: Sie
stand jahrelang so im Docstring von ``services/logs.py``, und trotzdem war gut
die Haelfte der Meldungen deutsch. Ein gemischtes Protokoll ist nicht
durchsuchbar - wer nach "not reachable" sucht, findet die deutsche Haelfte der
Faelle nicht.

Nicht betroffen: Kommentare, Docstrings und die Fehlertexte fuer den Nutzer.
Die bleiben deutsch.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

LOG_METHODEN = {"debug", "info", "warning", "error", "exception", "critical"}

#: Woerter, die es im Englischen nicht gibt und die in echten Meldungen
#: vorkommen. Bewusst ohne "in", "so", "die" und aehnliche Doppelgaenger.
DEUTSCHE_WOERTER = {
    "abgerufen",
    "abgleich",
    "angelegt",
    "anzahl",
    "aufgetreten",
    "benutzer",
    "bereits",
    "bestand",
    "datei",
    "eingerichtet",
    "eintrag",
    "entfernt",
    "erreicht",
    "erstellt",
    "fehlender",
    "fehlgeschlagen",
    "gefunden",
    "gelesen",
    "geloescht",
    "geschrieben",
    "gespeichert",
    "keine",
    "konnte",
    "lesbar",
    "moeglich",
    "nicht",
    "nachricht",
    "neue",
    "ohne",
    "titel",
    "ueber",
    "uebersprungen",
    "verbindung",
    "verschickt",
    "wird",
    "wurde",
    "zeitgrenze",
    "zugeordnet",
    "zurueck",
}

UMLAUTE = set("äöüßÄÖÜ")


def _meldungen() -> list[tuple[Path, int, str]]:
    """Alle Protokollmeldungen im Quelltext einsammeln."""
    gefunden: list[tuple[Path, int, str]] = []
    for datei in sorted(APP.rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Call):
                continue
            ziel = knoten.func
            if not isinstance(ziel, ast.Attribute) or ziel.attr not in LOG_METHODEN:
                continue
            # Nur Aufrufe auf etwas, das "logger" oder "logging" heisst.
            wurzel = ziel.value
            name = getattr(wurzel, "id", "") or getattr(wurzel, "attr", "")
            if "log" not in name.lower() and not isinstance(wurzel, ast.Call):
                continue
            if not knoten.args:
                continue
            erstes = knoten.args[0]
            if isinstance(erstes, ast.Constant) and isinstance(erstes.value, str):
                gefunden.append((datei, knoten.lineno, erstes.value))
            elif isinstance(erstes, ast.JoinedStr):
                gefunden.append(
                    (
                        datei,
                        knoten.lineno,
                        "".join(
                            teil.value
                            for teil in erstes.values
                            if isinstance(teil, ast.Constant) and isinstance(teil.value, str)
                        ),
                    )
                )
    return gefunden


def test_es_gibt_ueberhaupt_meldungen() -> None:
    """Wacht darueber, dass das Einsammeln oben nicht ins Leere laeuft."""
    assert len(_meldungen()) > 80


def test_alle_meldungen_sind_englisch() -> None:
    verstoesse: list[str] = []

    for datei, zeile, text in _meldungen():
        ort = f"{datei.relative_to(APP.parent)}:{zeile}"
        if UMLAUTE & set(text):
            verstoesse.append(f"{ort}: Umlaut in {text!r}")
            continue
        woerter = {wort.strip(".,:;()[]%").lower() for wort in text.split()}
        treffer = woerter & DEUTSCHE_WOERTER
        if treffer:
            verstoesse.append(f"{ort}: {sorted(treffer)} in {text!r}")

    assert not verstoesse, "Protokollmeldungen müssen englisch sein:\n" + "\n".join(verstoesse)


# ---------------------------------------------------------------------------
# Die zweite Lücke: deutsche Saetze, die erst zur Laufzeit hineinkommen
# ---------------------------------------------------------------------------
#
# ⚠️ **Der Test darueber sieht nur feste Texte im Quelltext.** Eine Zeile wie
#
#     logger.warning("Library not readable: %s", fehler.message)
#
# ist dort englisch und faellt nicht auf - deutsch wird sie erst, wenn der
# Fehler eingesetzt wird. Denn ``message`` ist unser Rueckfalltext fuer die
# Oberflaeche (siehe ``meldungen``), und der ist deutsch.
#
# Gefunden in Issue #7: Ein Betreiber in Rumaenien las in seinem Protokoll
# "Der Jellyfin-Server hat auch auf kleine Abfragen (25 Titel) nicht
# rechtzeitig geantwortet." Ins Protokoll gehoert stattdessen die Kennung -
# ``services.logs.kennung`` baut sie.

#: Stellen, an denen der eingesetzte Text **nicht** von uns stammt.
#:
#: ⚠️ **Das ist keine Ausrede-Liste.** Was ein Mailserver oder TMDB selbst
#: antwortet, ist deren Aussage und oft das Einzige, was den Fall erklaert -
#: genau wie der Wortlaut von Radarr. Wer hier eintraegt, schreibt dazu, wessen
#: Worte es sind. Wem das nicht gelingt, hat gerade gemerkt, dass die Stelle
#: eine Kennung braucht.
FREMDER_WORTLAUT: dict[tuple[str, str], str] = {
    ("kids.py", "_http"): "Was TMDB selbst geantwortet hat.",
    # ``einer`` ist die innere Funktion in ``_als_titel``.
    ("watchlist.py", "einer"): "Was TMDB selbst geantwortet hat.",
    ("accounts.py", "_deliver"): "Was der Mailserver selbst geantwortet hat.",
    ("accounts.py", "notify_address_change"): "Was der Mailserver selbst geantwortet hat.",
    # ⚠️ **Die einzige echte Luecke, und sie ist benannt.** ``ChannelError``
    # traegt unsere eigenen deutschen Saetze und keine Kennung. Ihn umzustellen
    # heisst, 24 Wurfstellen in sieben Dateien mit Kennungen und je zwei
    # Uebersetzungen zu versehen - ein eigener Durchgang, nicht ein Nebenbei.
    # Bis dahin steht hier ein deutscher Satz im Protokoll, und das ist
    # ausdruecklich bekannt.
    ("channel_outbox.py", "process"): "ChannelError hat noch keine Kennung - siehe oben.",
}


def _eingesetzte_fehlertexte() -> list[tuple[str, str, int]]:
    """Jede Log-Zeile, die ein ``…\u200b.message`` einsetzt: (Datei, Funktion, Zeile)."""
    gefunden: list[tuple[str, str, int]] = []
    for pfad in sorted(APP.rglob("*.py")):
        baum = ast.parse(pfad.read_text(encoding="utf-8"))

        # ⚠️ **Jede Fundstelle gehoert genau einer Funktion** - der innersten.
        # Ein Durchlauf ueber alle Funktionen fand verschachtelte Stellen
        # zweimal (einmal unter der inneren, einmal unter der aeusseren), und
        # dann braeuchte jede zwei Eintraege in der Liste oben.
        eltern: dict[ast.AST, str] = {}
        for knoten in ast.walk(baum):
            if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for kind in ast.walk(knoten):
                    if kind is not knoten:
                        eltern[kind] = knoten.name

        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Call):
                continue
            ziel = knoten.func
            if not (isinstance(ziel, ast.Attribute) and ziel.attr in LOG_METHODEN):
                continue
            if not (isinstance(ziel.value, ast.Name) and ziel.value.id in {"logger", "log"}):
                continue
            for arg in knoten.args[1:]:
                if isinstance(arg, ast.Attribute) and arg.attr == "message":
                    gefunden.append((pfad.name, eltern.get(knoten, "<modul>"), knoten.lineno))
    return gefunden


def test_der_waechter_sieht_diese_form_ueberhaupt() -> None:
    """⚠️ Zuerst: Findet er noch, wonach er sucht?

    Die erlaubten Stellen sind selbst der Beweis - waeren sie null, suchte der
    Test an der falschen Form.
    """
    assert _eingesetzte_fehlertexte(), (
        "Keine einzige Stelle gefunden, die einen Fehlertext ins Protokoll "
        "einsetzt - wird jetzt anders geloggt? Dann muss dieser Test das lernen."
    )


def test_kein_fehlertext_ohne_begruendung_im_protokoll() -> None:
    """Unsere eigenen Saetze sind deutsch - im Protokoll haben sie nichts verloren."""
    offen = [
        f"{datei}:{zeile} in {funktion}()"
        for datei, funktion, zeile in _eingesetzte_fehlertexte()
        if (datei, funktion) not in FREMDER_WORTLAUT
    ]
    assert not offen, (
        "Diese Stellen setzen einen Fehlertext ins Protokoll ein und schmuggeln "
        "damit Deutsch hinein:\n  " + "\n  ".join(offen) + "\n\n"
        "Richtig ist die Kennung: services.logs.kennung(fehler).\n"
        "Ist der Text der Wortlaut eines fremden Systems (Mailserver, TMDB, "
        "Radarr), gehoert die Stelle mit Begruendung nach FREMDER_WORTLAUT."
    )


# --- Deutsch, das erst zur Laufzeit entsteht ---------------------------------


def test_kein_fehlerobjekt_wandert_ungefiltert_ins_protokoll() -> None:
    """⚠️ Die Lücke, durch die Issue #7 gekommen ist.

    Die Prüfungen oben lesen die **festen Texte** im Quelltext, und die sind
    englisch. Ein ``logger.info("... %s", fehler)`` besteht sie deshalb mühelos
    und schreibt trotzdem Deutsch ins Protokoll: Die Meldungen von ``ArrError``,
    ``TmdbError`` und ``MediaServerError`` sind deutsch, denn sie sind der
    Rückfall für die Oberfläche, falls eine Übersetzung fehlt.

    So las ein Betreiber in Rumänien "Der Jellyfin-Server hat auch auf kleine
    Abfragen (25 Titel) nicht rechtzeitig geantwortet." Vier weitere Stellen
    derselben Art fanden sich später in media, watch, watchlist und calendar.

    Richtig ist ``logs.kennung(fehler)``: englisch, kurz, durchsuchbar.
    """
    import ast

    EIGENE_FEHLER = {"ArrError", "TmdbError", "MediaServerError", "RequestError"}
    funde: list[str] = []

    for datei in sorted(APP.rglob("*.py")):
        baum = ast.parse(datei.read_text(encoding="utf-8"), str(datei))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.ExceptHandler) or not knoten.name:
                continue
            typen = knoten.type
            namen = (
                {t.id for t in typen.elts if isinstance(t, ast.Name)}
                if isinstance(typen, ast.Tuple)
                else {typen.id} if isinstance(typen, ast.Name) else set()
            )
            if not (namen & EIGENE_FEHLER):
                continue

            for innen in ast.walk(knoten):
                if not (
                    isinstance(innen, ast.Call)
                    and isinstance(innen.func, ast.Attribute)
                    and isinstance(innen.func.value, ast.Name)
                    and innen.func.value.id == "logger"
                ):
                    continue
                for arg in innen.args:
                    # ``logger.info("...", fehler)``
                    roh = isinstance(arg, ast.Name) and arg.id == knoten.name
                    # ``logger.info("...", fehler.message)``
                    satz = (
                        isinstance(arg, ast.Attribute)
                        and isinstance(arg.value, ast.Name)
                        and arg.value.id == knoten.name
                        and arg.attr == "message"
                    )
                    if roh or satz:
                        pfad = datei.relative_to(APP.parent).as_posix()
                        funde.append(f"{pfad}:{innen.lineno}")

    assert not funde, (
        "Diese Zeilen schreiben den deutschen Satz eines eigenen Fehlers ins "
        "Protokoll. Richtig ist logs.kennung(fehler):\n  " + "\n  ".join(funde)
    )
