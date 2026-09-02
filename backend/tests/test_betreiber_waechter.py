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
  fremdes Konto benennen kann, muss ``betreiberschutz`` tragen. Benennen kann
  sie es auf zwei Wegen - ueber den Pfad (``{user_id}``) und ueber den
  **Rumpf** der Anfrage (``KONTO_FELDER``).
* **Aus dem Quelltext** (Teil 2): Jede Funktion in ``app/routers``, die an
  einem Konto schreibt, muss entschieden sein. Auch dann, wenn sie ueber die
  SQLAlchemy Core schreibt und deshalb gar kein Feld im Router stehen laesst.
  Und weil sich diese eine Zeile auch eine Datei weiter schreiben laesst, wird
  der Core-Weg zusaetzlich im **ganzen** ``app/`` gesucht (``CORE_AUSSERHALB``).

⚠️ **Beide Erweiterungen sind nachgetragen, und zwar aus demselben Anlass.**
Eine Adresse ``POST /api/users/stilllegen``, die ein beliebiges Konto ueber
``db.execute(update(User)...)`` abschaltet und ihr Ziel aus dem Rumpf nimmt,
lief durch die ganze Testreihe: 2.482 Tests, kein einziger roter. Teil 1 sah
sie nicht, weil kein ``{user_id}`` im Pfad stand; Teil 2 sah sie nicht, weil
sein Quelltext-Scan nur Attribut-Zuweisungen und ``setattr()`` kannte. Der
Nachbau steht als ``test_der_scan_sieht_den_core_schreibweg`` in dieser Datei
und muss einen Treffer liefern - sonst ist die Erweiterung nur eine Absicht.

Die gefaehrlichen Felder sind **nicht aufgezaehlt**, sondern aus ``models.User``
abgeleitet. Wer der Nutzertabelle morgen eine Spalte gibt, ist damit von selbst
mit drin; eine Liste haette man nachziehen muessen, und wer das vergisst,
bekommt genau den Zustand zurueck, den dieser Test verhindern soll.
"""

from __future__ import annotations

import ast
import typing
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


#: Feldnamen, die eine **fremde** Benutzernummer bezeichnen.
#:
#: ⚠️ Der Pfad ist nur der auffaellige Weg. Eine Adresse, die ihr Ziel aus dem
#: Anfrage-Rumpf nimmt, erreicht dasselbe Konto - und stand bis hierhin
#: ausserhalb des Blickfelds. Genau so kam ``POST /api/users/stilllegen``
#: durch die ganze Testreihe: kein ``{user_id}`` im Pfad, also kein Waechter.
KONTO_FELDER = {
    "user_id",
    "user_ids",
    "benutzer_id",
    "benutzer_ids",
    "konto_id",
    "child_id",
    "kind_id",
}


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


def _schreibende_kontoadressen() -> list[tuple[str, object]]:
    """Alle Adressen, die ein fremdes Konto benennen koennen und schreiben.

    Zwei Wege hinein, weil einer allein nicht reicht:

    * die Benutzernummer steht im **Pfad** (``/api/users/{user_id}``),
    * oder sie steht im **Rumpf** bzw. in der Abfrage (``KONTO_FELDER``).

    Der zweite Weg ist nachgetragen: Ohne ihn sah dieser Teil eine Adresse
    nicht, die ihr Ziel aus dem Rumpf nahm - und die kam damit an jedem
    Waechter vorbei.
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
        if not (aus_dem_pfad or aus_dem_rumpf):
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

#: Und so viele Adressen mit Kontobezug gibt es ueberhaupt - Pfad und Rumpf
#: zusammengezaehlt. Dieselbe Ueberlegung: ein Waechter, der nichts mehr
#: einsammelt, meldet auch nichts.
MINDESTENS_KONTOADRESSEN = 8


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


def _schreibstellen() -> dict[tuple[str, str], set[str]]:
    """Wo in ``app/routers`` an einem Konto geschrieben wird.

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
    felder = _felder_nur_am_konto()
    gefunden: dict[tuple[str, str], set[str]] = {}
    for datei in sorted(ROUTER_ORDNER.glob("*.py")):
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            schluessel = (datei.name, knoten.name)
            for inner in ast.walk(knoten):
                if isinstance(inner, ast.Assign):
                    for ziel in inner.targets:
                        if isinstance(ziel, ast.Attribute) and ziel.attr in felder:
                            gefunden.setdefault(schluessel, set()).add(ziel.attr)
                if isinstance(inner, ast.Call):
                    if isinstance(inner.func, ast.Name) and inner.func.id == "setattr":
                        gefunden.setdefault(schluessel, set()).add("setattr()")
                    if (
                        isinstance(inner.func, ast.Attribute)
                        and inner.func.attr in KONTO_VERAENDERNDE_AUFRUFE
                    ):
                        gefunden.setdefault(schluessel, set()).add(
                            f"{inner.func.attr}()"
                        )
                    weg = _core_schreibweg(inner)
                    if weg is not None:
                        gefunden.setdefault(schluessel, set()).add(weg)
                        # Die Felder stehen bei ``.values(is_active=False)``
                        # als Schluesselwoerter da - die gehoeren in die
                        # Meldung, sonst steht dort nur "update(User)".
                        if (
                            isinstance(inner.func, ast.Attribute)
                            and inner.func.attr == "values"
                        ):
                            for schluesselwort in inner.keywords:
                                if schluesselwort.arg in felder:
                                    gefunden[schluessel].add(schluesselwort.arg)
    return gefunden


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
CORE_AUSSERHALB: dict[tuple[str, str], str] = {
    ("services/sicherung.py", "_alle_abmelden"): (
        "Setzt nach dem Einspielen einer Sicherung ``sessions_valid_from`` an "
        "**allen** Konten, den Betreiber eingeschlossen - genau das ist der "
        "Zweck. Kein Ziel aus einer Anfrage: Die Funktion nimmt gar keinen "
        "Parameter, und wer eine Sicherung einspielen darf, hat die "
        "Installation ohnehin in der Hand."
    ),
}

APP_ORDNER = ROUTER_ORDNER.parent


def _core_schreibstellen_ausserhalb() -> dict[tuple[str, str], set[str]]:
    """Core-Schreibwege an der Nutzertabelle im ganzen ``app/`` ausser Routern."""
    gefunden: dict[tuple[str, str], set[str]] = {}
    for datei in sorted(APP_ORDNER.rglob("*.py")):
        if "__pycache__" in datei.parts or datei.parent == ROUTER_ORDNER:
            continue
        name = datei.relative_to(APP_ORDNER).as_posix()
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(knoten):
                if not isinstance(inner, ast.Call):
                    continue
                weg = _core_schreibweg(inner)
                if weg is not None:
                    gefunden.setdefault((name, knoten.name), set()).add(weg)
    return gefunden


def test_kein_core_schreibweg_ausserhalb_der_entscheidung() -> None:
    """Auch ein Dienst darf nicht unentschieden am Konto vorbeischreiben.

    ⚠️ **Der Scan darueber sieht nur ``app/routers``.** Wer dieselbe Zeile
    eine Datei weiter schreibt und sie aus einem Router ruft, erreicht
    dasselbe Konto und faellt dort nicht auf.
    """
    stellen = _core_schreibstellen_ausserhalb()
    offen = {
        f"{datei}::{funktion} ({', '.join(sorted(wege))})"
        for (datei, funktion), wege in stellen.items()
        if (datei, funktion) not in CORE_AUSSERHALB
    }
    assert not offen, (
        "Diese Stellen schreiben über die SQLAlchemy Core an der Nutzertabelle, "
        "ohne über den Betreiber-Schutz entschieden zu haben: "
        + ", ".join(sorted(offen))
        + ". Trag sie mit ausgeschriebenem Grund in CORE_AUSSERHALB ein - oder "
        "häng die Entscheidung an die Adresse, die sie ruft."
    )
    # Die Bodenschwelle: Der Scan muss die eine bekannte Stelle wirklich
    # finden. Sonst liefe er leer und waere still gruen.
    assert ("services/sicherung.py", "_alle_abmelden") in stellen, (
        "Der Scan findet die bekannte Stelle in sicherung.py nicht mehr - "
        "er sieht offenbar nichts."
    )


def test_keine_verwaisten_schreibstellen() -> None:
    """Auch hier: keine Erlaubnis fuer eine Funktion, die es nicht mehr gibt."""
    stellen = set(_schreibstellen())
    verwaist = set(NUR_EIGENES_KONTO) - stellen
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
