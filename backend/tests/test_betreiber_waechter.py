"""Der Waechter: Findet die naechste ungeschuetzte Tuer zum Betreiberkonto.

⚠️ **Warum es diese Datei gibt.** Eine Liste von Stellen, die man von Hand
schuetzt, ist morgen unvollstaendig. Genau so entstand vor Kurzem ein echtes
Loch: Die Anmeldebremse kannte drei Tueren, eine vierte kam spaeter dazu und
wurde nicht mitgezaehlt - 100 falsche Passwoerter, kein einziges Mal gebremst.

Hier wird die Frage deshalb umgedreht, wie in ``test_sicherung_waechter.py``.
Nicht "ist Adresse X geschuetzt?", sondern: **Gibt es zu allem, was ein
fremdes Konto anfassen kann, eine Entscheidung?**

⚠️ **Und die Regel, ohne die der Waechter wertlos ist.** Wird dieser Test rot,
ist die Antwort **nicht**, einen Eintrag in ``NUR_EIGENES_KONTO`` oder
``OHNE_SCHUTZ`` nachzutragen. Rot heisst: Jemand hat eine Adresse angelegt, die
an einem Konto schreibt, ohne zu entscheiden, ob der Betreiber davor geschuetzt
gehoert. **Diese Entscheidung ist der ganze Zweck.** Wem kein Grund einfaellt,
warum eine Stelle ohne Schutz auskommt, der hat gerade herausgefunden, dass sie
ihn braucht.

Geprueft wird von zwei Seiten, weil jede allein ein Loch hat:

* **Aus der Routentabelle** (Teil 1): Jede schreibende Adresse, die ein
  fremdes Konto erreichen kann, muss ``betreiberschutz`` tragen. Erreichen kann
  sie es auf drei Wegen - ueber den Pfad (``{user_id}``), ueber den **Rumpf**
  der Anfrage (``KONTO_FELDER``, abgeleitet aus den Fremdschluesseln auf
  ``users.id``) und indem sie sich das Konto **selbst holt**
  (``_holt_selbst_ein_konto``).
* **Aus dem Quelltext** (Teil 2): Jede Funktion im **ganzen** ``app/``, die an
  einem Konto schreibt, muss entschieden sein. Auch dann, wenn sie ueber die
  SQLAlchemy Core schreibt und deshalb gar kein Feld stehen laesst. Router
  gehen gegen ``NUR_EIGENES_KONTO``, alles andere gegen ``AUSSERHALB``, und
  **beide durch dieselbe Erkennung** (``_formen_in``).

⚠️ **Alle Erweiterungen sind nachgetragen, und jede hat ihren eigenen Anlass.**

1. Eine Adresse ``POST /api/users/stilllegen``, die ein beliebiges Konto ueber
   ``db.execute(update(User)...)`` abschaltet und ihr Ziel aus dem Rumpf nimmt,
   lief durch die ganze Testreihe: 2.482 Tests, kein einziger roter. Teil 1 sah
   sie nicht, weil kein ``{user_id}`` im Pfad stand; Teil 2 sah sie nicht, weil
   sein Quelltext-Scan nur Attribut-Zuweisungen und ``setattr()`` kannte. Der
   Nachbau steht als ``test_der_scan_sieht_den_core_schreibweg`` in dieser Datei
   und muss einen Treffer liefern - sonst ist die Erweiterung nur eine Absicht.
2. Danach blieb der einfachste Weg offen: ``opfer.role = Role.user`` in einem
   **Dienst**, gerufen aus einem Router. Teil 2 las ausserhalb von
   ``app/routers`` nur den Core-Weg. Nachgewiesen mit zwei Zeilen in
   ``services/tickets.py``, ueber die ein zweiter Administrator dem Betreiber
   Rolle und Haken nahm - 2.491 Tests, kein einziger roter. Seitdem gilt
   ueberall derselbe Umfang.
3. Und Teil 1 hing an sieben von Hand eingetragenen Feldnamen. Ein Rumpf-Feld
   namens ``ziel`` war damit unsichtbar. Die Namen kommen jetzt aus den
   Fremdschluesseln der Datenbank, und wo auch das nicht reicht, sieht das
   dritte Bein den Griff zur Nutzertabelle selbst.

Die gefaehrlichen Felder sind **nicht aufgezaehlt**, sondern aus ``models.User``
abgeleitet. Wer der Nutzertabelle morgen eine Spalte gibt, ist damit von selbst
mit drin; eine Liste haette man nachziehen muessen, und wer das vergisst,
bekommt genau den Zustand zurueck, den dieser Test verhindern soll.
"""

from __future__ import annotations

import ast
import inspect
import typing
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.deps import betreiberschutz
from app.main import app
from app.models import Base, Role, User

from .conftest import auth_headers, create_user

ROUTER_ORDNER = Path(__file__).resolve().parent.parent / "app" / "routers"


# ---------------------------------------------------------------------------
# Teil 1: aus der Routentabelle
# ---------------------------------------------------------------------------

#: Schreibende Adressen mit ``{user_id}`` im Pfad, die den Schutz **nicht**
#: tragen - jede mit ausgeschriebenem Grund.
#:
#: ⚠️ Keine Ablage fuer alles, was gerade rot ist. Ein Eintrag hier heisst:
#: "Ich habe nachgesehen, und diese Adresse kann dem Betreiberkonto nichts
#: antun." Wer das nicht begruenden kann, haengt stattdessen die Wache dran.
OHNE_SCHUTZ: dict[str, str] = {
    "POST /api/admin/requests/approve-all/{user_id}": (
        "Gibt fremde Anfragen frei, aendert das Konto nicht. Sie zu sperren "
        "naehme dem Betreiber etwas weg, statt ihn zu schuetzen - und der Haken "
        "gibt ihm nichts und nimmt ihm nichts."
    ),
    "POST /api/users/betreiber/uebergeben": (
        "Der Traeger gibt selbst weiter, und ``user_id`` im Rumpf ist das "
        "**Ziel** der Uebergabe, nicht das geschuetzte Konto. Die Wache haenge "
        "hier nur im Weg: Sie schuetzt den Betreiber vor anderen, hier fasst er "
        "sein eigenes Konto an. Wer nicht der Traeger ist, kommt gar nicht erst "
        "vorbei - services/betreiber.uebergeben prueft das als Erstes."
    ),
    "POST /api/tickets": (
        "Ein Administrator schreibt jemanden an, statt angeschrieben zu werden. "
        "``user_id`` benennt den Empfaenger des Tickets; an der Nutzertabelle "
        "wird dabei nichts geschrieben. Dieselbe Ueberlegung wie bei "
        "approve-all darueber: Den Betreiber davon auszunehmen hiesse, dass ihm "
        "niemand mehr schreiben kann."
    ),
    # ------------------------------------------------------------------
    # Ab hier: Adressen, die erst das dritte Bein sichtbar gemacht hat.
    #
    # ⚠️ Sie holen sich alle ein Konto aus der Nutzertabelle, aber keine
    # schreibt daran. Neun Eintraege sind der Preis dafuer, dass der Waechter
    # nicht mehr an einem Feldnamen haengt - jeder einzelne ist nachgesehen,
    # nicht abgeschrieben.
    # ------------------------------------------------------------------
    "POST /api/admin/requests/{request_id}/reply": (
        "Holt mit ``db.get(User, request.user_id)`` den Besteller, um ihm die "
        "Antwort zu melden. Geschrieben wird an der Bewertung (``reply``, "
        "``replied_at``, ``replied_by``), nicht am Konto - und das Ziel sucht "
        "sich die Anfrage nicht aus, es haengt an der Anfragenummer im Pfad."
    ),
    "POST /api/feedback/{rating_id}/reply": (
        "Dieselbe Bauart wie die Adresse darueber, nur von der anderen Seite: "
        "geschrieben wird an ``TitleRating``, das Konto wird geholt, um die "
        "Meldung zuzustellen."
    ),
    "POST /api/auth/login": (
        "Sucht das Konto zum eingegebenen Benutzernamen. Wer das Passwort hat, "
        "**ist** das Konto - eine Wache davor wuerde den Betreiber aussperren, "
        "nicht schuetzen. Steht aus demselben Grund auch in NUR_EIGENES_KONTO."
    ),
    "POST /api/auth/refresh": (
        "Holt das Konto zur Nummer **aus dem eigenen Token**. Die Anfrage kann "
        "hier gar kein Ziel benennen; sie kann nur ein Token vorlegen, das "
        "jemand ausgestellt hat."
    ),
    "POST /api/onboarding/forgot-password": (
        "Sucht das Konto zur Mailadresse und legt einen Einladungs-Token an. "
        "Am Konto selbst wird nichts geaendert, und der Weg zurueck fuehrt "
        "ueber das Postfach - siehe die ausfuehrliche Begruendung bei "
        "``onboarding.set_password`` in NUR_EIGENES_KONTO."
    ),
    "POST /api/settings/channels/{channel}/test": (
        "Ein Fehlalarm, und ein lehrreicher: Der Entwurf eines Meldeziels "
        "traegt ein Feld ``parent_id``, das auf ein **uebergeordnetes Ziel** "
        "zeigt, nicht auf ein Konto. Denselben Spaltennamen gibt es an "
        "``User``, und daran kann die Ableitung die beiden nicht "
        "unterscheiden. Die Adresse schickt eine Probemeldung und schreibt "
        "nirgends an einem Konto."
    ),
    "POST /api/storage/entries/{posten_id}/haus": (
        "Nimmt einen Posten in den Hausbestand. Geschrieben wird am "
        "``StorageEntry``; das Konto wird geholt, um dem bisherigen Besitzer "
        "die Uebernahme zu melden. Das Ziel haengt am Posten, nicht an der "
        "Anfrage."
    ),
    "POST /api/storage/entries/{posten_id}/entfolgen": (
        "Dieselbe Bauart: geschrieben wird am Posten und in Sonarr, das Konto "
        "wird nur fuer die Meldung geholt."
    ),
    "POST /api/storage/entries/{posten_id}/loeschen": (
        "Dieselbe Bauart wie die beiden Adressen darueber. Was hier "
        "verschwindet, ist ein Posten samt Dateien, kein Konto."
    ),
    "POST /api/admin/mediaserver/{provider}/import": (
        "⚠️ **Der unbequemste Eintrag in dieser Liste, und er gehoert gelesen.** "
        "Die Adresse verknuepft Medienserver-Konten mit Nexview-Konten, greift "
        "also sehr wohl an fremde Konten. Die Wache passt hier trotzdem nicht: "
        "``betreiberschutz`` liest genau **ein** ``user_id`` aus dem Pfad, und "
        "diese Adresse bekommt beliebig viele Ziele im Rumpf. Sie anzuhaengen "
        "hiesse, sie umzubauen.\n\n"
        "Stattdessen prueft ``nutzer_import.uebernehmen`` jedes Ziel selbst und "
        "weist das Betreiberkonto ab. Warum das noetig ist: Wer eine "
        "Medienserver-Identitaet an ein Konto haengt, kommt ohne Passwort in "
        "dieses Konto - am Betreiber waere das eine Uebernahme in zwei Klicks.\n\n"
        "Der Ersatz fuer die Wache ist "
        "``test_nutzer_import.py::test_der_betreiber_ist_kein_ziel``. Er ist "
        "schwaecher als sie, weil er an einer Stelle steht und nicht an allen: "
        "Wer eine **zweite** Adresse mit Zielen im Rumpf baut, bekommt von ihm "
        "keine Warnung. Sollte das je vorkommen, gehoert ``betreiberschutz`` so "
        "umgebaut, dass er auch eine Liste im Rumpf lesen kann - und dieser "
        "Eintrag hier verschwindet."
    ),
}


def _pfad_und_methoden(route) -> tuple[str, set[str]]:
    return str(getattr(route, "path", "")), set(getattr(route, "methods", None) or ())


def _wachen(route) -> set:
    gefunden: set = set()

    def sammeln(dependant) -> None:
        for eintrag in dependant.dependencies:
            gefunden.add(eintrag.call)
            sammeln(eintrag)

    sammeln(route.dependant)
    return gefunden


def _kontofelder() -> set[str]:
    """Feldnamen, die eine **fremde** Benutzernummer bezeichnen - abgeleitet.

    ⚠️ Der Pfad ist nur der auffaellige Weg. Eine Adresse, die ihr Ziel aus dem
    Anfrage-Rumpf nimmt, erreicht dasselbe Konto - und stand bis vor Kurzem
    ausserhalb des Blickfelds. Genau so kam ``POST /api/users/stilllegen``
    durch die ganze Testreihe: kein ``{user_id}`` im Pfad, also kein Waechter.

    ⚠️ **Und deshalb steht hier keine Liste mehr.** Bis zum 02.09.2026 waren es
    sieben von Hand eingetragene Namen. Vier davon (``benutzer_id``,
    ``benutzer_ids``, ``konto_id``, ``kind_id``) kommen im ganzen Schema nicht
    vor, waren also Vorrat fuer einen Fall, den es nie gab; dafuer fehlten
    sechs, die es wirklich gibt - ``parent_id``, ``for_child_id``,
    ``approved_by``, ``blocked_by``, ``created_by``, ``last_reply_by``,
    ``replied_by``, ``aktualisiert_von``. Genau die Sorte Liste, deretwegen es
    diesen Waechter ueberhaupt gibt.

    Die Datenbank weiss es besser: Ein Fremdschluessel auf ``users.id`` ist die
    Definition von "diese Spalte benennt ein Konto". Wer der naechsten Tabelle
    eine solche Spalte gibt, ist damit von selbst mit drin. Dazu die
    Mehrzahlform, weil eine Adresse auch eine Liste entgegennehmen kann.

    ⚠️ Ein Name kann in zwei Tabellen dasselbe heissen und Verschiedenes
    meinen: ``parent_id`` zeigt an ``User`` auf ein Elternkonto, an
    ``ChannelTarget`` auf einen uebergeordneten Kanal. Der Waechter kann das
    nicht unterscheiden und schlaegt bei beiden an. Das ist die richtige
    Richtung: lieber einmal zu viel entschieden als einmal zu wenig, und der
    Fehlalarm kostet einen Eintrag in ``OHNE_SCHUTZ`` mit ausgeschriebenem
    Grund.
    """
    namen: set[str] = set()
    for tabelle in Base.metadata.tables.values():
        for spalte in tabelle.columns:
            for fremd in spalte.foreign_keys:
                if fremd.column.table.name == User.__table__.name:
                    namen.add(spalte.name)
    return namen | {name + "s" for name in namen if name.endswith("_id")}


KONTO_FELDER = _kontofelder()


#: Aufrufe, mit denen eine Adresse ein Konto **selbst aufloest**.
#:
#: ⚠️ **Das zweite Bein von Teil 1, und es schliesst genau die Luecke, die
#: ein Name allein offenlaesst.** Ein Rumpf-Feld muss nicht ``user_id`` heissen.
#: Es kann ``ziel`` heissen oder ``empfaenger``, und dann findet es keine noch
#: so gut abgeleitete Namensliste. Was sich nicht verstecken laesst, ist der
#: Griff zur Nutzertabelle: Wer ein fremdes Konto anfassen will, muss es zuerst
#: holen.
KONTO_AUFLOESUNG = ("db.get(User, ...)", "select(User)")


class _Leer:
    """Ein Platzhalter fuer Routen ohne ``dependant`` (z. B. WebSockets)."""

    body_params: list = []
    query_params: list = []
    dependencies: list = []


_LEER = _Leer()


def _feldnamen(annotation: object, tiefe: int = 0) -> set[str]:
    """Alle Feldnamen eines Anfrage-Modells, auch aus verschachtelten Modellen."""
    namen: set[str] = set()
    if tiefe > 4:
        return namen
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        for name, feld in annotation.model_fields.items():
            namen.add(name)
            namen |= _feldnamen(feld.annotation, tiefe + 1)
        return namen
    for teil in typing.get_args(annotation) or ():
        namen |= _feldnamen(teil, tiefe + 1)
    return namen


def _hereingereichte_namen(dependant) -> set[str]:
    """Was eine Anfrage an dieser Adresse benennen kann - Rumpf und Abfrage."""
    namen: set[str] = set()
    for feld in dependant.body_params:
        namen.add(feld.name)
        namen |= _feldnamen(feld.field_info.annotation)
    for feld in dependant.query_params:
        namen.add(feld.name)
    for eintrag in dependant.dependencies:
        namen |= _hereingereichte_namen(eintrag)
    return namen


def _holt_selbst_ein_konto(endpunkt: object) -> bool:
    """Greift diese Handler-Funktion selbst zur Nutzertabelle?

    ⚠️ **Das dritte Bein, und das einzige, das ohne Namen auskommt.** Die
    beiden anderen erkennen ein Ziel daran, wie es *heisst* - im Pfad oder im
    Rumpf. Beide sind blind gegen ein Feld, das ``ziel`` oder ``empfaenger``
    heisst, und dagegen hilft keine noch so gut abgeleitete Namensliste.

    Was sich nicht umbenennen laesst, ist der Griff selbst: ``db.get(User, x)``
    und ``select(User)``. Wer ein Konto anfassen will, muss es zuerst holen.

    Bewusst grob: Auch das blosse Nachschlagen zaehlt, nicht erst das
    Schreiben. Neun der siebzehn heutigen Adressen holen ein Konto nur, um
    seinen Namen anzuzeigen oder ihm eine Meldung zu schicken - jede davon
    steht mit diesem Grund in ``OHNE_SCHUTZ``. Der Preis ist ein Eintrag je
    Adresse, der Gewinn ist, dass niemand mehr an einem Feldnamen vorbeikommt.
    """
    try:
        quelle = inspect.getsource(endpunkt)  # type: ignore[arg-type]
    except (OSError, TypeError):
        return False
    try:
        # Der Umweg ueber ``unparse`` nimmt die Einrueckung heraus, mit der
        # eine Methode sonst nicht fuer sich allein parsbar waere.
        baum = ast.parse(ast.unparse(ast.parse(quelle)))
    except SyntaxError:  # pragma: no cover - nur bei kaputtem Quelltext
        return False
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        if (
            isinstance(knoten.func, ast.Attribute)
            and knoten.func.attr == "get"
            and knoten.args
            and isinstance(knoten.args[0], ast.Name)
            and knoten.args[0].id == "User"
        ):
            return True
        if (
            isinstance(knoten.func, ast.Name)
            and knoten.func.id == "select"
            and any(isinstance(a, ast.Name) and a.id == "User" for a in knoten.args)
        ):
            return True
    return False


def _schreibende_kontoadressen() -> list[tuple[str, object]]:
    """Alle Adressen, die ein fremdes Konto erreichen koennen und schreiben.

    Drei Wege hinein, weil keiner allein reicht:

    * die Benutzernummer steht im **Pfad** (``/api/users/{user_id}``),
    * oder sie steht im **Rumpf** bzw. in der Abfrage (``KONTO_FELDER``,
      abgeleitet aus den Fremdschluesseln auf ``users.id``),
    * oder die Adresse **holt sich das Konto selbst** (``_holt_selbst_ein_konto``).

    Der zweite Weg war nachgetragen, weil dieser Teil eine Adresse nicht sah,
    die ihr Ziel aus dem Rumpf nahm. Der dritte ist nachgetragen, weil auch der
    zweite an einem Namen haengt und ein Feld ``ziel`` heissen darf.
    """
    gefunden = []
    for route in app.routes:
        pfad, methoden = _pfad_und_methoden(route)
        if not pfad.startswith("/api"):
            continue
        schreibend = sorted(methoden - {"GET", "HEAD", "OPTIONS"})
        if not schreibend:
            continue
        aus_dem_pfad = "{user_id}" in pfad
        aus_dem_rumpf = bool(
            _hereingereichte_namen(getattr(route, "dependant", None) or _LEER)
            & KONTO_FELDER
        )
        aus_dem_quelltext = _holt_selbst_ein_konto(getattr(route, "endpoint", None))
        if not (aus_dem_pfad or aus_dem_rumpf or aus_dem_quelltext):
            continue
        for methode in schreibend:
            gefunden.append((f"{methode} {pfad}", route))
    return gefunden


#: So viele bewachte Adressen gibt es mindestens.
#:
#: ⚠️ **Ohne diese Schwelle waere der Test still gruen, sobald er nichts mehr
#: findet** - etwa weil jemand den Pfadteil umbenennt. Ein Waechter, der nichts
#: sieht, meldet auch nichts.
MINDESTENS_BEWACHT = 5

#: Und so viele Adressen mit Kontobezug gibt es ueberhaupt - Pfad, Rumpf und
#: Quelltext zusammengezaehlt. Dieselbe Ueberlegung: ein Waechter, der nichts
#: mehr einsammelt, meldet auch nichts.
#:
#: Am 02.09.2026 gemessen: 17. Die Schwelle liegt darunter, damit eine
#: absichtlich entfernte Adresse den Test nicht rot macht - aber nicht so weit,
#: dass ein Bein still ausfallen koennte.
MINDESTENS_KONTOADRESSEN = 15

#: So viele Feldnamen leitet ``_kontofelder`` mindestens ab.
#:
#: ⚠️ Ohne diese Schwelle liefe die Ableitung leer, sobald jemand die Tabelle
#: umbenennt oder ``Base.metadata`` zum falschen Zeitpunkt gelesen wird - und
#: der Rumpf-Weg waere still wieder zu.
MINDESTENS_KONTOFELDER = 10

#: So viele Adressen findet das dritte Bein mindestens von sich aus.
#:
#: ⚠️ Dieselbe Ueberlegung eine Ebene tiefer: Faende ``_holt_selbst_ein_konto``
#: nichts mehr - etwa weil ``inspect.getsource`` an einer verpackten Funktion
#: scheitert -, faellt das ganze Bein aus, ohne dass eine Zahl kleiner wuerde,
#: solange die anderen beiden noch genug einsammeln. Am 02.09.2026: 8.
MINDESTENS_AUS_DEM_QUELLTEXT = 6


def test_jede_kontoadresse_ist_entschieden() -> None:
    """Keine schreibende Adresse mit Benutzernummer darf unentschieden sein."""
    offen = []
    bewacht = 0
    for name, route in _schreibende_kontoadressen():
        if betreiberschutz in _wachen(route):
            bewacht += 1
            continue
        if name in OHNE_SCHUTZ:
            continue
        offen.append(name)

    assert not offen, (
        "Diese Adressen verändern ein fremdes Konto, ohne über den "
        "Betreiber-Schutz entschieden zu haben: "
        + ", ".join(sorted(offen))
        + ". Häng `dependencies=[Depends(betreiberschutz)]` an die Route - oder "
        "trag sie mit ausgeschriebenem Grund in OHNE_SCHUTZ ein."
    )
    assert bewacht >= MINDESTENS_BEWACHT, (
        f"Nur {bewacht} bewachte Adressen gefunden, erwartet mindestens "
        f"{MINDESTENS_BEWACHT}. Der Wächter sieht offenbar nichts mehr."
    )


def test_teil_eins_sieht_auch_den_rumpf() -> None:
    """Der Rumpf-Weg muss wirklich etwas finden, nicht bloss dastehen.

    ⚠️ **Sonst waere die Erweiterung eine Absichtserklaerung.** Ein Tippfehler
    in ``_hereingereichte_namen`` liesse sie leer laufen, und Teil 1 saehe
    wieder nur Pfade - genau der Zustand, den die Hintertuer ausgenutzt hat.
    ``POST /api/users/betreiber/uebergeben`` traegt kein ``{user_id}`` im
    Pfad; wenn diese Adresse dabei ist, kommt sie aus dem Rumpf.
    """
    namen = {name for name, _ in _schreibende_kontoadressen()}
    ohne_pfadnummer = {name for name in namen if "{user_id}" not in name}
    assert "POST /api/users/betreiber/uebergeben" in ohne_pfadnummer
    assert len(namen) >= MINDESTENS_KONTOADRESSEN, (
        f"Nur {len(namen)} Adressen mit Kontobezug gefunden, erwartet mindestens "
        f"{MINDESTENS_KONTOADRESSEN}. Der Wächter sieht offenbar nichts mehr."
    )


def test_die_kontofelder_kommen_aus_dem_schema() -> None:
    """Die Feldnamen muessen wirklich aus den Fremdschluesseln stammen.

    ⚠️ **Ohne diese Probe koennte die Ableitung leer laufen** - und der
    Rumpf-Weg waere still wieder zu, ohne dass eine einzige Zeile rot wird.
    Geprueft wird deshalb beides: dass etwas herauskommt, und dass genau die
    Namen dabei sind, die es vorher **nicht** gab.
    """
    felder = _kontofelder()
    assert len(felder) >= MINDESTENS_KONTOFELDER, (
        f"Nur {len(felder)} Kontofelder abgeleitet, erwartet mindestens "
        f"{MINDESTENS_KONTOFELDER}. Zeigt in diesem Schema nichts mehr auf users.id?"
    )
    # Der offensichtliche Fall, und die Mehrzahlform dazu.
    assert {"user_id", "user_ids"} <= felder
    # Die Namen, die der Handliste gefehlt haben. Sie stehen hier als Beleg,
    # dass die Ableitung wirklich mehr sieht als das, was jemand aufzaehlt.
    assert {"parent_id", "for_child_id", "approved_by", "blocked_by"} <= felder
    # Und die Gegenrichtung: Was nie im Schema stand, taucht auch nicht auf.
    # ``konto_id`` und ``kind_id`` standen bis zum 02.09.2026 in der Handliste
    # und haben dort nie etwas getroffen.
    assert not ({"konto_id", "kind_id", "benutzer_id"} & felder)


def test_teil_eins_sieht_auch_den_quelltext() -> None:
    """Das dritte Bein muss wirklich etwas finden, nicht bloss dastehen.

    ⚠️ **Dieselbe Sorge wie beim Rumpf-Weg, nur eine Ebene tiefer.** Scheitert
    ``inspect.getsource`` kuenftig an einer verpackten Handler-Funktion, gibt
    ``_holt_selbst_ein_konto`` still ueberall ``False`` zurueck. Die
    Gesamtzahl faellt dann kaum auf, weil die anderen beiden Beine weiter
    liefern - dieser Test faellt auf.
    """
    aus_dem_quelltext = [
        name
        for name, route in _schreibende_kontoadressen()
        if _holt_selbst_ein_konto(getattr(route, "endpoint", None))
    ]
    assert len(aus_dem_quelltext) >= MINDESTENS_AUS_DEM_QUELLTEXT, (
        f"Nur {len(aus_dem_quelltext)} Adressen holen sich laut Quelltext selbst ein "
        f"Konto, erwartet mindestens {MINDESTENS_AUS_DEM_QUELLTEXT}. Das dritte Bein "
        "läuft offenbar leer."
    )
    # Eine Adresse, die **nur** ueber dieses Bein hereinkommt: kein
    # ``{user_id}`` im Pfad, und ihr Rumpf traegt keinen Kontonamen.
    assert "POST /api/auth/refresh" in aus_dem_quelltext


def test_keine_verwaisten_ausnahmen() -> None:
    """Jeder Eintrag in ``OHNE_SCHUTZ`` muss es noch geben.

    Eine Ausnahme fuer eine Adresse, die es nicht mehr gibt, ist eine
    Erlaubnis, die niemand mehr liest - und beim naechsten gleichnamigen Pfad
    gilt sie stillschweigend weiter.
    """
    vorhanden = {name for name, _ in _schreibende_kontoadressen()}
    verwaist = set(OHNE_SCHUTZ) - vorhanden
    assert not verwaist, f"Ausnahmen ohne Adresse: {sorted(verwaist)}"


# ---------------------------------------------------------------------------
# Teil 2: aus dem Quelltext
# ---------------------------------------------------------------------------


def _felder_nur_am_konto() -> set[str]:
    """Spaltennamen, die es **nur** an der Nutzertabelle gibt.

    Aus dem Modell abgeleitet, nicht aufgezaehlt: Eine neue Spalte an ``User``
    ist damit ohne Zutun mit ueberwacht.

    Namen, die auch anderswo vorkommen (``created_at``, ``id``, ``language``),
    fallen heraus - sonst schluege der Waechter bei jeder Einstellungsseite an
    und waere binnen eines Monats abgestumpft. ``email``, ``username`` und
    ``parent_id`` kommen ausdruecklich zurueck: Sie sind die Kennzeichen eines
    Kontos, und ein Schreibzugriff darauf gehoert entschieden, egal wo er steht.
    """
    tabelle = User.__table__
    andere: set[str] = set()
    for weitere in Base.metadata.tables.values():
        if weitere is tabelle:
            continue
        andere |= {spalte.name for spalte in weitere.columns}
    return ({spalte.name for spalte in tabelle.columns} - andere) | {
        "email",
        "username",
        "parent_id",
    }


#: Aufrufe, die ein Konto veraendern, ohne dass ein Feld im Router steht.
#:
#: ``ernennen`` steht hier, weil es der einzige Weg ist, den Betreiber-Haken
#: **ohne** die Regeln aus ``uebergeben`` zu setzen - er gehoert dem Startweg
#: und der Einrichtung, nicht einem Router.
KONTO_VERAENDERNDE_AUFRUFE = {"aufloesen", "uebergeben", "ernennen"}

#: Befehle der SQLAlchemy Core, die schreiben. ``select`` fehlt mit Absicht.
CORE_SCHREIBBEFEHLE = {"update", "delete", "insert"}


def _core_schreibweg(knoten: ast.Call) -> str | None:
    """Schreibt diese Aufrufkette **an den Objekten vorbei** an der Nutzertabelle?

    ⚠️ **Das ist die Form, die den Waechter einmal ausgehebelt hat.** Wer so
    schreibt, laesst kein Feld im Router stehen - es gibt keine Zeile
    ``user.is_active = False``, die der Scan sehen koennte. Gemeint sind:

    * ``db.execute(update(User).where(...).values(is_active=False))``
    * ``db.execute(delete(User).where(...))`` - dasselbe beim Loeschen
    * ``db.query(User).update({...})`` und ``.delete()`` - der aeltere Weg

    Erkannt wird an der **Wurzel** der Kette, damit ``.where(...)`` und
    ``.values(...)`` denselben Treffer melden und die Reihenfolge im Baum
    keine Rolle spielt.
    """
    letztes_glied = knoten.func.attr if isinstance(knoten.func, ast.Attribute) else None
    wurzel: ast.Call = knoten
    while isinstance(wurzel.func, ast.Attribute):
        davor = wurzel.func.value
        if not isinstance(davor, ast.Call):
            break
        wurzel = davor
    if not any(isinstance(teil, ast.Name) and teil.id == "User" for teil in wurzel.args):
        return None
    if isinstance(wurzel.func, ast.Name) and wurzel.func.id in CORE_SCHREIBBEFEHLE:
        return f"{wurzel.func.id}(User)"
    # ``db.query(User).update(...)``: hier steht der Befehl am Ende der Kette.
    if (
        isinstance(wurzel.func, ast.Attribute)
        and wurzel.func.attr == "query"
        and letztes_glied in CORE_SCHREIBBEFEHLE
    ):
        return f"query(User).{letztes_glied}()"
    return None


def _formen_in(knoten: ast.AST, felder: set[str]) -> set[str]:
    """Alle Schreibformen an einem Konto innerhalb dieser Funktion.

    ⚠️ **Es gibt genau diese eine Definition von "hier wird geschrieben", und
    das ist der Sinn dieser Funktion.** Bis zum 02.09.2026 stand die Erkennung
    zweimal da: einmal vollstaendig fuer ``app/routers``, einmal verkuerzt auf
    den Core-Weg fuer alles andere. Der einfache Weg - ``nutzer.is_active =
    False`` in einem Dienst - fiel damit durch beide. Nachgewiesen: Zwei Zeilen
    in ``services/tickets.py`` nahmen dem Betreiber Rolle und Haken, und alle
    2.491 Tests blieben gruen.

    Vier Formen, und jede hat ihren Grund:

    * ``irgendwas.is_active = ...`` - der gewoehnliche Fall.
    * ``setattr(...)`` - undurchsichtig. Was dort geschrieben wird, sieht man
      von aussen nicht, also gilt es als Schreibzugriff. ``update_user`` ist
      genau so gebaut.
    * Aufrufe aus ``KONTO_VERAENDERNDE_AUFRUFE`` - Vorgaenge, die ein Konto
      veraendern, ohne dass hier ein Feld dasteht. ``aufloesen`` laesst ein
      Konto samt Bestand verschwinden, ``ernennen`` setzt den Betreiber-Haken
      **an allen Regeln vorbei**. Gerade der zweite muss auffallen: Wer ihn
      kuenftig aus einem Router aufruft, hat eine Adresse gebaut, die den
      Betreiber wechseln kann, ohne dass der Traeger gefragt wurde.
    * Die Core-Schreibwege aus ``_core_schreibweg`` - nachgetragen, weil eine
      Adresse genau darueber jedes Konto abschalten konnte, ohne dass der
      Waechter etwas gemerkt haette.
    """
    gefunden: set[str] = set()
    for inner in ast.walk(knoten):
        if isinstance(inner, ast.Assign):
            for ziel in inner.targets:
                if isinstance(ziel, ast.Attribute) and ziel.attr in felder:
                    gefunden.add(ziel.attr)
        if isinstance(inner, ast.Call):
            if isinstance(inner.func, ast.Name) and inner.func.id == "setattr":
                gefunden.add("setattr()")
            if (
                isinstance(inner.func, ast.Attribute)
                and inner.func.attr in KONTO_VERAENDERNDE_AUFRUFE
            ):
                gefunden.add(f"{inner.func.attr}()")
            weg = _core_schreibweg(inner)
            if weg is not None:
                gefunden.add(weg)
                # Die Felder stehen bei ``.values(is_active=False)`` als
                # Schluesselwoerter da - die gehoeren in die Meldung, sonst
                # steht dort nur "update(User)".
                if isinstance(inner.func, ast.Attribute) and inner.func.attr == "values":
                    for schluesselwort in inner.keywords:
                        if schluesselwort.arg in felder:
                            gefunden.add(schluesselwort.arg)
    return gefunden


def _scan_ueber_dateien(
    dateien: list[Path], schluessel_von: Callable[[Path], str]
) -> tuple[dict[tuple[str, str], set[str]], int]:
    """Alle Schreibstellen in diesen Dateien, dazu die Zahl der gelesenen.

    ⚠️ **Die zweite Rueckgabe ist kein Beiwerk.** Ein Scan, dessen Dateiliste
    leer laeuft, meldet nichts und ist damit fuer immer gruen. Genau das ist
    hier schon passiert: Ein verwandter Waechter hatte still aufgehoert, 90
    Module zu sehen, und niemandem fiel etwas auf. Wer diese Funktion ruft,
    bekommt die Zahl mit und muss sie gegen eine Schwelle halten.
    """
    felder = _felder_nur_am_konto()
    gefunden: dict[tuple[str, str], set[str]] = {}
    gelesen = 0
    for datei in sorted(dateien):
        gelesen += 1
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            formen = _formen_in(knoten, felder)
            if formen:
                gefunden.setdefault((schluessel_von(datei), knoten.name), set())
                gefunden[(schluessel_von(datei), knoten.name)] |= formen
    return gefunden, gelesen


def _router_dateien() -> list[Path]:
    return [d for d in ROUTER_ORDNER.glob("*.py") if "__pycache__" not in d.parts]


def _schreibstellen() -> dict[tuple[str, str], set[str]]:
    """Wo in ``app/routers`` an einem Konto geschrieben wird."""
    return _scan_ueber_dateien(_router_dateien(), lambda d: d.name)[0]


#: Schreibstellen, die **kein** fremdes Konto erreichen koennen - mit Grund.
#:
#: ⚠️ Dieselbe Regel wie oben: keine Ablage fuer alles, was der Waechter nicht
#: versteht. Jeder Eintrag behauptet etwas Nachpruefbares - "diese Funktion
#: schreibt ausschliesslich am angemeldeten Konto oder an einem Konto, das die
#: Anfrage sich nicht aussuchen kann".
NUR_EIGENES_KONTO: dict[tuple[str, str], str] = {
    ("about.py", "neuigkeiten_gesehen"): "Quittiert am eigenen Konto (CurrentUser).",
    ("hausordnung.py", "entscheiden"): (
        "Haelt die eigene Entscheidung ueber die Hausordnung fest (AdultUser) - "
        "dieselbe Bauart wie neuigkeiten_gesehen darueber. Eine fremde Kennung "
        "kommt gar nicht erst herein: Der Pfad traegt keine, und im Rumpf steht "
        "nur ja oder nein."
    ),
    ("auth.py", "abmelden_ueberall"): "Beendet die eigenen Sitzungen.",
    ("auth.py", "change_my_email"): "Die eigene Mailadresse - /api/auth/me/email.",
    ("auth.py", "change_own_password"): (
        "Das eigene Passwort, und nur gegen das alte. Nicht zu verwechseln mit "
        "users.reset_password - das ist die Admin-Adresse und traegt die Wache."
    ),
    ("auth.py", "delete_avatar"): "Das eigene Bild.",
    ("auth.py", "upload_avatar"): "Das eigene Bild.",
    ("auth.py", "login"): (
        "Setzt last_login_at an dem Konto, das sich gerade selbst anmeldet - "
        "wer das Passwort hat, ist das Konto."
    ),
    ("auth.py", "update_me"): "Das eigene Profil - Anzeigename, Darstellung, Region.",
    ("mediaserver.py", "login_password"): "last_login_at beim Anmelden, wie auth.login.",
    ("mediaserver.py", "login_poll"): "last_login_at beim Anmelden, wie auth.login.",
    ("oidc.py", "callback"): "last_login_at beim Anmelden, wie auth.login.",
    ("onboarding.py", "change_pending_email"): (
        "Vor der Anmeldung, gegen einen Einladungs-Token: erreicht nur das Konto, "
        "zu dem der Token gehoert - und das gibt es noch gar nicht."
    ),
    ("onboarding.py", "set_password"): (
        "Gegen einen Token aus einer Mail an genau diese Adresse. Wer den Token "
        "hat, hat das Postfach; das ist der uebliche Weg zurueck und kein Angriff "
        "eines zweiten Administrators. Der Betreiber kann ihn selbst nicht "
        "abschalten, ohne die Passwort-vergessen-Funktion abzuschaffen."
    ),
    ("onboarding.py", "verify_email"): "Bestaetigt die Adresse gegen den Token aus der Mail.",
    ("settings.py", "update_settings"): (
        "setattr am haus-weiten Einstellungssatz (AppSettings), nicht an einem Konto."
    ),
    ("users.py", "reset_quota"): "Traegt die Wache - siehe Teil 1.",
    ("users.py", "reset_password"): "Traegt die Wache - siehe Teil 1.",
    ("users.py", "update_user"): "Traegt die Wache - siehe Teil 1.",
    ("users.py", "delete_user"): "Traegt die Wache - siehe Teil 1.",
    ("users.py", "betreiber_uebergeben"): (
        "Der Traeger gibt selbst weiter. Ausdruecklich ohne die Wache: Sie schuetzt "
        "das Betreiberkonto vor anderen, hier fasst der Betreiber sein eigenes an. "
        "Die Regeln stehen in services/betreiber.uebergeben."
    ),
}

#: So viele Schreibstellen gibt es mindestens - dieselbe Ueberlegung wie
#: ``MINDESTENS_BEWACHT``.
MINDESTENS_SCHREIBSTELLEN = 20


def test_jede_schreibstelle_am_konto_ist_entschieden() -> None:
    """Kein Router darf an einem Konto schreiben, ohne entschieden zu sein."""
    stellen = _schreibstellen()
    offen = {
        f"{datei}::{funktion} ({', '.join(sorted(felder))})"
        for (datei, funktion), felder in stellen.items()
        if (datei, funktion) not in NUR_EIGENES_KONTO
    }
    assert not offen, (
        "Diese Stellen schreiben an einem Konto, ohne über den Betreiber-Schutz "
        "entschieden zu haben: "
        + ", ".join(sorted(offen))
        + ". Häng die Wache an die Route - oder trag die Stelle mit "
        "ausgeschriebenem Grund in NUR_EIGENES_KONTO ein."
    )
    assert len(stellen) >= MINDESTENS_SCHREIBSTELLEN, (
        f"Nur {len(stellen)} Schreibstellen gefunden, erwartet mindestens "
        f"{MINDESTENS_SCHREIBSTELLEN}. Der Wächter sieht offenbar nichts mehr."
    )


#: Die Hintertuer, die dem Waechter einmal entwischt ist - als Quelltext.
#:
#: ⚠️ **Wortgleich zum echten Fund.** Sie wurde so in ``app/routers/users.py``
#: eingebaut, und die ganze Testreihe blieb gruen. Wer den Scan spaeter
#: umbaut, muss an dieser Probe vorbei.
HINTERTUER_QUELLTEXT = '''
@router.post("/stilllegen", status_code=204)
def konto_stilllegen(payload: StilllegenAnfrage, admin: AdminUser, db: DbSession) -> None:
    db.execute(update(User).where(User.id == payload.user_id).values(is_active=False))
    db.commit()


@router.post("/entfernen", status_code=204)
def konto_entfernen(payload: StilllegenAnfrage, admin: AdminUser, db: DbSession) -> None:
    db.execute(delete(User).where(User.id == payload.user_id))
    db.commit()


@router.post("/altweg", status_code=204)
def konto_altweg(payload: StilllegenAnfrage, admin: AdminUser, db: DbSession) -> None:
    db.query(User).filter(User.id == payload.user_id).update({"role": "admin"})
    db.commit()
'''


def _scan_ueber_quelltext(quelltext: str) -> dict[str, set[str]]:
    """Denselben Scan wie ``_schreibstellen``, nur ueber losen Quelltext."""
    felder = _felder_nur_am_konto()
    gefunden: dict[str, set[str]] = {}
    baum = ast.parse(quelltext)
    for knoten in ast.walk(baum):
        if not isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(knoten):
            if not isinstance(inner, ast.Call):
                continue
            weg = _core_schreibweg(inner)
            if weg is not None:
                gefunden.setdefault(knoten.name, set()).add(weg)
                if isinstance(inner.func, ast.Attribute) and inner.func.attr == "values":
                    for schluesselwort in inner.keywords:
                        if schluesselwort.arg in felder:
                            gefunden[knoten.name].add(schluesselwort.arg)
    return gefunden


def test_der_scan_sieht_den_core_schreibweg() -> None:
    """Die Mutationsprobe: Die Hintertuer muss dem Scan auffallen.

    ⚠️ **Ohne diese Probe waere die Erweiterung von Teil 2 nur eine
    Absichtserklaerung.** Genau dieser Quelltext lief einmal durch 2.482 Tests,
    ohne dass etwas rot wurde - weil der Scan ``update()`` der Core nicht
    kannte. Hier wird nachgewiesen, dass er ihn jetzt kennt.
    """
    treffer = _scan_ueber_quelltext(HINTERTUER_QUELLTEXT)

    assert "konto_stilllegen" in treffer, (
        "db.execute(update(User)...) fällt dem Scan nicht auf - genau die Form, "
        "mit der die Hintertür durch die ganze Testreihe kam."
    )
    assert "update(User)" in treffer["konto_stilllegen"]
    # Das Feld aus ``.values(...)`` gehoert in die Meldung, sonst steht dort
    # nur "update(User)" und niemand sieht, was geschrieben wird.
    assert "is_active" in treffer["konto_stilllegen"]

    assert "delete(User)" in treffer.get("konto_entfernen", set())
    assert "query(User).update()" in treffer.get("konto_altweg", set())


def test_der_scan_schlaegt_beim_lesen_nicht_an() -> None:
    """Die Gegenprobe: Lesen ist kein Schreiben.

    ⚠️ **Ein Waechter, der bei jedem ``select(User)`` anschlaegt, wird
    abgeschaltet** - und nimmt beim Abschalten die echten Funde mit. Deshalb
    steht ``select`` nicht in ``CORE_SCHREIBBEFEHLE``.
    """
    harmlos = '''
def zaehlen(db):
    return db.scalar(select(func.count()).select_from(User))


def liste(db):
    return list(db.scalars(select(User).order_by(User.created_at)))
'''
    assert _scan_ueber_quelltext(harmlos) == {}


#: Core-Schreibwege **ausserhalb** von ``app/routers`` - mit Grund.
#:
#: ⚠️ Warum ueberhaupt hier hinsehen? Weil ein Router die Zeile auch
#: auslagern kann. ``db.execute(update(User)...)`` in einem Dienst ist von
#: einer Adresse aus genauso erreichbar wie im Router selbst, nur unsichtbar
#: fuer den Scan darueber. Die Liste ist absichtlich kurz und bleibt es nur,
#: solange jeder Eintrag begruendet ist.
#: Schreibstellen **ausserhalb** von ``app/routers`` - jede mit Grund.
#:
#: ⚠️ Warum ueberhaupt hier hinsehen? Weil ein Router die Zeile auslagern kann.
#: Ein Schreibzugriff in einem Dienst ist von einer Adresse aus genauso
#: erreichbar wie im Router selbst, nur unsichtbar fuer den Scan darueber.
#:
#: ⚠️ **Bis zum 02.09.2026 stand hier nur der Core-Weg**, also
#: ``db.execute(update(User)...)``. Der gewoehnliche Weg - ``nutzer.role =
#: Role.user`` - wurde ausserhalb der Router nie gesucht. Nachgewiesen mit zwei
#: Zeilen in ``services/tickets.py``: Ein zweiter Administrator nahm dem
#: Betreiber ueber ``POST /api/tickets`` Rolle und Haken ab, und alle 2.491
#: Tests blieben gruen. Seitdem gilt hier derselbe Umfang wie im Router.
#:
#: Die Liste ist absichtlich lang statt kurz. Jeder Eintrag behauptet etwas
#: Nachpruefbares, und drei Sorten kommen vor:
#:
#: * **schreibt wirklich am Konto**, aber an einem, das die Anfrage sich nicht
#:   aussuchen kann (``betreiber._setzen``, ``children.recht_entzogen``),
#: * **schreibt am Konto der eigenen Sitzung** oder an einem Unterprofil davon
#:   (``children.*``, ``mediaserver_accounts.*``, ``oidc_accounts.link``),
#: * **schreibt gar nicht am Konto** - der Spaltenname kommt an einer anderen
#:   Tabelle noch einmal vor, oder ``setattr`` trifft ein anderes Objekt.
#:   Diese Fehlalarme sind der Preis dafuer, dass ``email``, ``username`` und
#:   ``parent_id`` ausdruecklich mit ueberwacht werden.
AUSSERHALB: dict[tuple[str, str], str] = {
    ("services/sicherung.py", "_alle_abmelden"): (
        "Setzt nach dem Einspielen einer Sicherung ``sessions_valid_from`` an "
        "**allen** Konten, den Betreiber eingeschlossen - genau das ist der "
        "Zweck. Kein Ziel aus einer Anfrage: Die Funktion nimmt gar keinen "
        "Parameter, und wer eine Sicherung einspielen darf, hat die "
        "Installation ohnehin in der Hand."
    ),
    ("services/betreiber.py", "_setzen"): (
        "**Das ist der Haken selbst.** Die einzige Stelle, die "
        "``is_betreiber`` umlegt, und sie raeumt erst ab und setzt dann. "
        "Gerufen wird sie nur aus ``ernennen`` und ``uebergeben``, und dort "
        "stehen die Regeln. Eine Wache davor haenge im Weg des Vorgangs, den "
        "sie schuetzen soll."
    ),
    ("services/betreiber.py", "nach_dem_einspielen"): (
        "Stellt nach einer eingespielten Sicherung den Traeger von vorher "
        "wieder her - die eine Stelle, an der der Haken die Zeitmaschine nicht "
        "mitmacht. Ohne sie koennte ein zweiter Administrator eine Uebergabe "
        "rueckgaengig machen, indem er einen alten Stand einspielt. Das Ziel "
        "kommt aus dem gemerkten Benutzernamen, nicht aus einer Anfrage."
    ),
    ("services/children.py", "recht_entzogen"): (
        "Legt die Kinder eines Elternteils still, wenn ihm das Recht genommen "
        "wird. Zwei Gruende, und beide nachgesehen: Der Weg hierher ist "
        "``PATCH /api/users/{user_id}``, und der traegt die Wache. Und die "
        "Ziele stammen aus ``eigene_kinder``, sind also Unterprofile - ein "
        "Kinderkonto kann den Haken gar nicht tragen, weil ``uebergeben`` "
        "``an.is_child`` und ``an.role != Role.admin`` ausdruecklich abweist "
        "und ``ernennen`` nur aus Einrichtung, Startweg und Umgebung gerufen "
        "wird."
    ),
    ("services/children.py", "aendern"): (
        "``setattr(kind, feld, wert)`` am eigenen Kind - ``kind_von`` laesst "
        "nur Unterprofile des angemeldeten Elternteils durch. ⚠️ Dass das "
        "``setattr`` harmlos ist, haengt allein daran, dass ``ChildUpdate`` "
        "ein geschlossenes Modell mit sechs Feldern ist. Wer ihm eines Tages "
        "``extra=\"allow\"`` gibt, macht daraus einen Schreibzugriff auf jede "
        "Spalte der Nutzertabelle."
    ),
    ("services/children.py", "passwort_setzen"): (
        "Setzt Passwort und ``password_changed_at`` am eigenen Kind, wieder "
        "ueber ``kind_von``. Fuer Kinder gibt es kein Passwort-vergessen, das "
        "Elternteil ist der Weg zurueck."
    ),
    ("services/mediaserver_accounts.py", "_spalten_spiegeln"): (
        "Die einzige Stelle, die die gespiegelten ``mediaserver_*``-Spalten "
        "schreibt, und sie schreibt sie aus der Verknuepfungstabelle desselben "
        "Kontos. Kein fremdes Ziel: Die Funktion nimmt genau einen Benutzer "
        "entgegen, und den bringt der Aufrufer aus der laufenden Anmeldung mit."
    ),
    ("services/mediaserver_accounts.py", "link"): (
        "``email`` und ``username`` gehoeren hier der Zeile in "
        "``user_media_server_accounts``, nicht dem Konto - beide Namen gibt es "
        "an beiden Tabellen. Am Konto wird nur ``email_verified`` gesetzt, und "
        "nur gesetzt, nie zurueckgenommen, weil Jellyfin zu einem Konto gar "
        "keine Adresse kennt."
    ),
    ("services/mediaserver_accounts.py", "merke_token"): (
        "Legt das persoenliche Anbieter-Token an der Verknuepfung ab und ruft "
        "``_spalten_spiegeln``. Dasselbe Konto, dieselbe Anmeldung."
    ),
    ("services/mediaserver_accounts.py", "resolve"): (
        "``username`` ist hier das Feld der Verknuepfungszeile. Wo wirklich ein "
        "Konto entsteht, geschieht das ueber ``User(...)`` mit einem frisch "
        "eindeutig gemachten Namen - ein neues Konto kann kein fremdes sein."
    ),
    ("services/oidc_accounts.py", "link"): (
        "Setzt ``email_verified`` am Konto, das sich gerade verknuepft, und nur "
        "wenn der Anbieter fuer die Adresse buergt **und** es dieselbe ist, die "
        "am Konto steht. Strenger als der Media-Server-Weg, und mit Absicht."
    ),
    ("services/tickets.py", "kinderkonten_freischalten"): (
        "Setzt ``can_manage_children`` beim **Eigentuemer des Tickets**, nicht "
        "bei einem frei gewaehlten Konto, und nur auf ``True``. Die Freigabe "
        "gibt etwas und nimmt nichts; den Betreiber davon auszunehmen hiesse, "
        "dass ausgerechnet er seinen Antrag nicht bewilligt bekommt. ⚠️ Fiele "
        "die Richtung je weg, waere das hier eine Adresse, an der ein zweiter "
        "Administrator ein Recht entziehen kann."
    ),
    ("services/aufraeum_bericht.py", "vielleicht_verschicken"): (
        "Setzt ``cleanup_mail_at`` als Merker \"Bericht fuer diesen Monat ist "
        "raus\". Laeuft im Hintergrund ueber alle Konten, hat keine Anfrage und "
        "damit kein waehlbares Ziel; der Wert ist ein Datum und traegt kein "
        "Recht."
    ),
    ("services/channel_targets.py", "anwenden"): (
        "Fehlalarm: Das ``setattr`` trifft einen ``ChannelTarget``, kein Konto. "
        "Der Scan zaehlt jedes ``setattr`` mit, weil er nicht sehen kann, was "
        "es trifft - lieber diese Zeile hier als ein uebersehenes "
        "``update_user``."
    ),
    ("services/stats.py", "collect"): (
        "Fehlalarm derselben Sorte: ``quota_series_limit`` ist hier das Feld "
        "einer Auswertungszeile, die genauso heisst wie die Spalte am Konto. "
        "Geschrieben wird an einer Datenklasse, die nur bis zum Ende der "
        "Antwort lebt."
    ),
}

APP_ORDNER = ROUTER_ORDNER.parent


def _dateien_ausserhalb() -> list[Path]:
    return [
        d
        for d in APP_ORDNER.rglob("*.py")
        if "__pycache__" not in d.parts and d.parent != ROUTER_ORDNER
    ]


def _schreibstellen_ausserhalb() -> tuple[dict[tuple[str, str], set[str]], int]:
    """Schreibstellen am Konto im ganzen ``app/`` ausser Routern.

    Derselbe Umfang wie im Router, weil ``_scan_ueber_dateien`` und
    ``_formen_in`` dieselben sind. Das ist der ganze Punkt: Zwei Scans mit
    verschiedener Reichweite sind ein Loch mit Ansage.
    """
    return _scan_ueber_dateien(
        _dateien_ausserhalb(), lambda d: d.relative_to(APP_ORDNER).as_posix()
    )


#: So viele Schreibstellen gibt es ausserhalb der Router mindestens.
#: Am 02.09.2026 gemessen: 15.
MINDESTENS_STELLEN_AUSSERHALB = 12

#: Und so viele Module muss der Scan dabei wirklich gelesen haben.
#:
#: ⚠️ **Die wichtigere der beiden Schwellen.** Eine Stellenzahl kann noch
#: stimmen, waehrend der Scan die Haelfte des Backends nicht mehr aufmacht;
#: genau so hat ein verwandter Waechter hier still 90 Module verloren. Am
#: 02.09.2026 gemessen: 111 Dateien ausserhalb von ``app/routers``.
MINDESTENS_MODULE_AUSSERHALB = 90


def test_kein_schreibweg_ausserhalb_der_entscheidung() -> None:
    """Auch ein Dienst darf nicht unentschieden am Konto schreiben.

    ⚠️ **Der Scan darueber sieht nur ``app/routers``.** Wer dieselbe Zeile
    eine Datei weiter schreibt und sie aus einem Router ruft, erreicht
    dasselbe Konto und faellt dort nicht auf.

    Bis zum 02.09.2026 suchte dieser Test hier nur den Core-Weg. Der
    gewoehnliche Schreibzugriff, ``nutzer.role = Role.user`` in einem Dienst,
    lief durch - nachgewiesen an ``services/tickets.py``, wo zwei Zeilen dem
    Betreiber Rolle und Haken nahmen, ohne dass einer von 2.491 Tests rot
    wurde.
    """
    stellen, module = _schreibstellen_ausserhalb()
    offen = {
        f"{datei}::{funktion} ({', '.join(sorted(wege))})"
        for (datei, funktion), wege in stellen.items()
        if (datei, funktion) not in AUSSERHALB
    }
    assert not offen, (
        "Diese Stellen schreiben außerhalb der Router an der Nutzertabelle, "
        "ohne über den Betreiber-Schutz entschieden zu haben: "
        + ", ".join(sorted(offen))
        + ". Trag sie mit ausgeschriebenem Grund in AUSSERHALB ein - oder "
        "häng die Entscheidung an die Adresse, die sie ruft."
    )
    # Drei Bodenschwellen, und jede fuer einen eigenen Ausfall.
    assert module >= MINDESTENS_MODULE_AUSSERHALB, (
        f"Der Scan hat nur {module} Module gelesen, erwartet mindestens "
        f"{MINDESTENS_MODULE_AUSSERHALB}. Er sieht offenbar den Großteil des "
        "Backends nicht mehr."
    )
    assert len(stellen) >= MINDESTENS_STELLEN_AUSSERHALB, (
        f"Nur {len(stellen)} Schreibstellen außerhalb der Router gefunden, erwartet "
        f"mindestens {MINDESTENS_STELLEN_AUSSERHALB}. Der Scan läuft offenbar leer."
    )
    # Und die eine bekannte Stelle des Core-Wegs muss weiter dabei sein -
    # sonst waere der Core-Teil still ausgefallen, waehrend die einfachen
    # Zuweisungen die Zahl oben noch halten.
    assert ("services/sicherung.py", "_alle_abmelden") in stellen, (
        "Der Scan findet die bekannte Core-Stelle in sicherung.py nicht mehr - "
        "der Core-Weg ist offenbar ausgefallen."
    )


def test_beide_scans_haben_denselben_umfang() -> None:
    """Router und Dienste muessen mit derselben Elle gemessen werden.

    ⚠️ **Genau hier lag das Loch.** Zwei Scans mit verschiedener Reichweite
    sind ein Loch mit Ansage: Was der eine kann und der andere nicht, ist der
    Weg, den der naechste nimmt. Der Test haelt fest, dass beide durch
    ``_formen_in`` gehen, indem er denselben Quelltext einmal als Router und
    einmal als Dienst durch die Erkennung schickt.
    """
    quelle = """
def irgendwas(db, opfer):
    opfer.is_betreiber = False
"""
    felder = _felder_nur_am_konto()
    knoten = next(
        k
        for k in ast.walk(ast.parse(quelle))
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert _formen_in(knoten, felder) == {"is_betreiber"}


def test_keine_verwaisten_schreibstellen() -> None:
    """Auch hier: keine Erlaubnis fuer eine Funktion, die es nicht mehr gibt."""
    stellen = set(_schreibstellen())
    verwaist = set(NUR_EIGENES_KONTO) - stellen
    assert not verwaist, f"Erlaubnisse ohne Schreibstelle: {sorted(verwaist)}"


def test_keine_verwaisten_ausnahmen_ausserhalb() -> None:
    """Und dasselbe fuer die Liste ausserhalb der Router.

    Eine Erlaubnis fuer eine Funktion, die es nicht mehr gibt, ist eine
    Erlaubnis, die niemand mehr liest - und beim naechsten gleichnamigen
    Nachfolger gilt sie stillschweigend weiter.
    """
    stellen, _ = _schreibstellen_ausserhalb()
    verwaist = set(AUSSERHALB) - set(stellen)
    assert not verwaist, f"Erlaubnisse ohne Schreibstelle: {sorted(verwaist)}"


def test_die_felderliste_kommt_aus_dem_modell() -> None:
    """Der Waechter muss die Spalten wirklich aus ``User`` holen.

    Ohne diese Probe koennte die Ableitung leer laufen - etwa weil die Tabelle
    umbenannt wird - und Teil 2 waere still gruen.
    """
    felder = _felder_nur_am_konto()
    assert "is_betreiber" in felder
    assert "is_active" in felder
    assert "role" in felder
    assert "password_hash" in felder
    # Geteilte Namen bleiben draussen, sonst wird der Waechter zu laut.
    assert "created_at" not in felder
    assert len(felder) > 40, f"Nur {len(felder)} Felder abgeleitet - das ist zu wenig."


# ---------------------------------------------------------------------------
# Und die Wache selbst muss wirken - nicht nur haengen
# ---------------------------------------------------------------------------


@pytest.fixture()
def zwei_admins(admin_client: TestClient) -> tuple[TestClient, dict[str, str], int]:
    """Der Betreiber (aus der Einrichtung) und ein zweiter Administrator."""
    create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
    kopf_b = auth_headers(admin_client, "zweiter", "zweites-passwort")
    betreiber_id = admin_client.get("/api/users/betreiber").json()["user_id"]
    return admin_client, kopf_b, betreiber_id


def test_wache_greift_an_jeder_bewachten_adresse(
    zwei_admins: tuple[TestClient, dict[str, str], int],
) -> None:
    """Admin B kommt an keiner der bewachten Adressen durch - alle 403.

    ⚠️ **Ueber die Adressen selbst, nicht ueber die Oberflaeche.** Ausgegraute
    Knoepfe sind Hoeflichkeit; die Sperre muss auch dann halten, wenn jemand
    die Anfrage von Hand stellt.
    """
    client, kopf_b, betreiber_id = zwei_admins

    antworten = {
        "PATCH": client.patch(
            f"/api/users/{betreiber_id}", json={"is_active": False}, headers=kopf_b
        ),
        "PATCH-rolle": client.patch(
            f"/api/users/{betreiber_id}", json={"role": "user"}, headers=kopf_b
        ),
        "PASSWORT": client.post(
            f"/api/users/{betreiber_id}/password",
            json={"password": "uebernommen-123"},
            headers=kopf_b,
        ),
        "LOESCHEN": client.request(
            "DELETE", f"/api/users/{betreiber_id}", json={}, headers=kopf_b
        ),
        "KONTINGENT": client.post(
            f"/api/users/{betreiber_id}/quota/reset", headers=kopf_b
        ),
        "SPEICHER": client.post(
            f"/api/users/{betreiber_id}/storage/reset", headers=kopf_b
        ),
    }
    for name, antwort in antworten.items():
        assert antwort.status_code == 403, f"{name}: {antwort.status_code} {antwort.text}"
        assert antwort.json()["detail"]["code"] == "betreiber_geschuetzt", name


def test_der_betreiber_darf_sein_eigenes_konto_weiter_aendern(
    zwei_admins: tuple[TestClient, dict[str, str], int],
) -> None:
    """⚠️ Der Haken gibt kein Recht - er darf auch keines wegnehmen.

    Der Betreiber muss sein eigenes Konto weiter einstellen koennen. Eine
    Wache, die ihn selbst aussperrt, waere ein Fehler, der erst auffiele, wenn
    er etwas aendern will.
    """
    client, _, betreiber_id = zwei_admins
    antwort = client.patch(f"/api/users/{betreiber_id}", json={"display_name": "Chef"})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["display_name"] == "Chef"


def test_fremde_konten_bleiben_ganz_normal_aenderbar(
    zwei_admins: tuple[TestClient, dict[str, str], int],
) -> None:
    """Die Gegenprobe: An jedem anderen Konto darf Admin B alles wie bisher.

    ⚠️ **Ohne diese Probe koennte die Wache alles sperren und saehe richtig
    aus.** Ein Schutz, der zu viel schuetzt, ist genauso kaputt - er faellt nur
    spaeter auf.
    """
    client, kopf_b, _ = zwei_admins
    create_user(client, "gewoehnlich", "gewoehnliches-passwort")
    fremd = next(
        u["id"] for u in client.get("/api/users").json() if u["username"] == "gewoehnlich"
    )

    assert client.patch(
        f"/api/users/{fremd}", json={"is_active": False}, headers=kopf_b
    ).status_code == 200
    assert client.post(
        f"/api/users/{fremd}/password", json={"password": "neues-passwort"}, headers=kopf_b
    ).status_code == 204
    assert client.post(f"/api/users/{fremd}/quota/reset", headers=kopf_b).status_code == 200
