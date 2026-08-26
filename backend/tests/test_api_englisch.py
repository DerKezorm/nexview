"""Die Schnittstelle beschreibt sich nach aussen auf Englisch - vollstaendig.

⚠️ **Warum das ein Test ist und keine Absichtserklaerung.**

Nexview schreibt seine Docstrings auf Deutsch, und FastAPI stellt genau die auf
``/docs``. Am 26.08.2026 waren dort 152 von 227 Operationen deutsch beschrieben
und 62 gar nicht - auf einer Seite, die sich an Entwickler richtet, die des
Deutschen womoeglich nicht maechtig sind.

Das laesst sich einmal aufraeumen. Es bleibt aber nur aufgeraeumt, wenn eine
**neue** Route ohne englischen Text nicht durchkommt. Sonst steht in einem
halben Jahr wieder ein Drittel auf Deutsch, und niemand weiss, wann es passiert
ist.

Der Unterschied ist exakt feststellbar, nicht geschaetzt: FastAPI setzt
``route.description`` auf den Docstring, wenn kein ``description=`` angegeben
wurde. Stimmen beide ueberein, ist der Text durchgereicht - also deutsch.
"""

from __future__ import annotations

import inspect

from fastapi.routing import APIRoute

from app import api_texte
from app.main import app


def test_jede_operation_hat_einen_englischen_text() -> None:
    """Keine Adresse ohne eigenen aeusseren Text.

    Schlaegt der Test an, fehlt der Eintrag in ``app/api_texte.py``. Die
    Fehlermeldung nennt die Adressen - eintragen, fertig.
    """
    offen = api_texte.fehlende(app)
    assert offen == [], (
        f"{len(offen)} Operationen beschreiben sich noch nicht auf Englisch.\n"
        "Sie reichen ihren deutschen Docstring nach draussen durch, oder sie "
        "haben gar keinen Text.\n"
        "Einzutragen in app/api_texte.py unter genau diesem Schluessel:\n  "
        + "\n  ".join(offen)
    )


def test_keine_karteileichen() -> None:
    """⚠️ Die andere Richtung - Text ohne Adresse.

    Wird eine Adresse umbenannt oder entfernt, faellt ihr Text lautlos aus der
    Anwendung. Ohne diese Pruefung bleibt er als Karteileiche stehen, und beim
    naechsten Lesen glaubt jemand, die alte Adresse gaebe es noch.
    """
    tote = api_texte.verwaiste(app)
    assert tote == [], (
        "In app/api_texte.py stehen Texte fuer Adressen, die es nicht gibt:\n  "
        + "\n  ".join(tote)
        + "\n\nEntweder ist die Adresse umbenannt worden - dann den Schluessel "
        "mitziehen - oder sie ist weg, dann kann der Text weg."
    )


def test_der_kurztitel_steht_fuer_sich() -> None:
    """Ein Kurztitel muss ohne den Pfad daneben verstaendlich sein.

    In der Liste auf ``/docs`` steht er neben der Adresse, aber in einer
    Suchtrefferliste steht er allein. "Get" oder "List" sagt dort nichts.
    """
    zu_kurz: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        titel = (route.summary or "").strip()
        for methode in sorted(route.methods - {"HEAD", "OPTIONS"}):
            if len(titel.split()) < 2:
                zu_kurz.append(f"{methode} {route.path}: {titel!r}")

    assert zu_kurz == [], (
        "Diese Kurztitel bestehen aus weniger als zwei Woertern und sagen "
        "allein zu wenig:\n  " + "\n  ".join(zu_kurz)
    )


#: Woerter, die es **nur** im Deutschen gibt.
#:
#: ⚠️ Bewusst kurz und bewusst vorsichtig gewaehlt. Nicht dabei sind Woerter,
#: die in beiden Sprachen vorkommen - "die", "man", "war", "so", "in", "an" -
#: denn ein Waechter, der faelschlich anschlaegt, wird abgeschaltet und nimmt
#: beim Abschalten die echten Funde mit. Lieber ein paar Faelle uebersehen als
#: einmal falsch Alarm schlagen.
DEUTSCHE_WOERTER = frozenset(
    """
    nicht werden wird wurde und oder auch eine einen einem keine kein
    kann koennen muss muessen beim vom sich aber dass dieser diese dieses
    fuer ueber waere gibt steht stehen statt ohne durch mit ist sind haben
    hat wenn weil damit schon noch nur mehr immer jeder jede jedes welche
    """.split()
)


def test_die_texte_sind_englisch() -> None:
    """⚠️ **Der Waechter, der zuletzt gefehlt hat.**

    Die drei Tests darueber pruefen, dass ein eigener Text *existiert* und zu
    einer echten Adresse gehoert. Ob er englisch ist, prueft keiner davon -
    man koennte Deutsch in ``api_texte.py`` schreiben und alles bliebe gruen.
    Genau das faellt beim Schreiben leicht: Man liest den deutschen Docstring
    und formuliert daneben, und zwei von zwanzig bleiben deutsch.

    Geprueft wird auf Umlaute, auf ``ss``-Umschreibungen und auf eine kurze
    Liste von Woertern, die es im Englischen nicht gibt.

    ⚠️ **Das ist ein Anhaltspunkt, kein Beweis.** Ein deutscher Satz ohne eines
    dieser Woerter kaeme durch. Der Test faengt den Regelfall - jemand
    uebernimmt den deutschen Text - und nicht den boesen Willen.
    """
    verdaechtig: list[str] = []
    for schluessel, (titel, text) in api_texte.TEXTE.items():
        ganz = f"{titel} {text}"
        gefunden = sorted(
            {w for w in DEUTSCHE_WOERTER if f" {w} " in f" {ganz.lower()} "}
        )
        umlaute = [z for z in "äöüÄÖÜß" if z in ganz]
        if gefunden or umlaute:
            grund = ", ".join(gefunden + umlaute)
            verdaechtig.append(f"{schluessel}: {grund}")

    assert verdaechtig == [], (
        "Diese Texte in app/api_texte.py sehen deutsch aus. Was auf /docs "
        "erscheint, gehoert auf Englisch - die deutsche Begruendung bleibt im "
        "Docstring:\n  " + "\n  ".join(verdaechtig)
    )


def test_die_doku_folgt_dem_code() -> None:
    """Die Beschreibung beschreibt, was es wirklich gibt.

    ⚠️ **Der Punkt, an dem handgepflegte Beschreibungen scheitern.** Wer seine
    Schnittstelle in einer YAML-Datei neben dem Code pflegt, hat irgendwann
    beides - und nur eines davon stimmt. Bei uns liest FastAPI die tatsaechlich
    vorhandenen Routen aus, also kann das Dokument ueber das *Was* nicht
    luegen.

    Dieser Test haelt fest, dass das so bleibt: Jede Adresse aus dem laufenden
    Programm steht im Dokument, und im Dokument steht nichts, was es nicht
    gibt. Wuerde jemand kuenftig eine Beschreibung von Hand danebenlegen,
    faellt es hier auf.
    """
    aus_dem_code = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        for methode in route.methods - {"HEAD", "OPTIONS"}:
            aus_dem_code.add(f"{methode} {route.path}")

    dokument = app.openapi()
    aus_dem_dokument = {
        f"{methode.upper()} {pfad}"
        for pfad, operationen in dokument.get("paths", {}).items()
        for methode in operationen
    }

    assert aus_dem_code == aus_dem_dokument, (
        "Das OpenAPI-Dokument und die laufenden Routen gehen auseinander.\n"
        f"Nur im Programm: {sorted(aus_dem_code - aus_dem_dokument)}\n"
        f"Nur im Dokument: {sorted(aus_dem_dokument - aus_dem_code)}"
    )


# ⚠️ **Hier stand ein Test, der geloescht wurde - und der Grund gehoert notiert.**
#
# Er sollte verhindern, dass jemand die deutschen Docstrings uebersetzt, statt
# Texte in api_texte.py einzutragen, und erkannte "uebersetzt" daran, dass kein
# einziges Sonderzeichen mehr vorkam.
#
# Das kann in diesem Projekt nicht funktionieren: Die deutschen Docstrings sind
# **absichtlich** reines ASCII - "Aenderung" statt "Änderung", durchgaengig. Der
# Test haette also bei fast jedem ausfuehrlichen Docstring angeschlagen, ohne
# dass irgendetwas falsch waere.
#
# Ein Test, der staendig faelschlich rot ist, wird abgeschaltet und nimmt beim
# Abschalten die echten Funde mit. Lieber keiner als der.
