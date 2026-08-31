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

* **Aus der Routentabelle** (Teil 1): Jede schreibende Adresse mit einer
  Benutzernummer im Pfad muss ``betreiberschutz`` tragen. Faengt den
  Regelfall - aber nur, wenn die Nummer im *Pfad* steht.
* **Aus dem Quelltext** (Teil 2): Jede Funktion in ``app/routers``, die an
  einem Konto schreibt, muss entschieden sein. Faengt auch die Adresse, die
  ihr Ziel aus dem **Rumpf** der Anfrage holt statt aus dem Pfad - und genau
  die saehe Teil 1 nicht.

Die gefaehrlichen Felder sind **nicht aufgezaehlt**, sondern aus ``models.User``
abgeleitet. Wer der Nutzertabelle morgen eine Spalte gibt, ist damit von selbst
mit drin; eine Liste haette man nachziehen muessen, und wer das vergisst,
bekommt genau den Zustand zurueck, den dieser Test verhindern soll.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


def _schreibende_kontoadressen() -> list[tuple[str, object]]:
    """Alle Adressen, die eine Benutzernummer im Pfad tragen und schreiben."""
    gefunden = []
    for route in app.routes:
        pfad, methoden = _pfad_und_methoden(route)
        if not pfad.startswith("/api") or "{user_id}" not in pfad:
            continue
        for methode in sorted(methoden - {"GET", "HEAD", "OPTIONS"}):
            gefunden.append((f"{methode} {pfad}", route))
    return gefunden


#: So viele bewachte Adressen gibt es mindestens.
#:
#: ⚠️ **Ohne diese Schwelle waere der Test still gruen, sobald er nichts mehr
#: findet** - etwa weil jemand den Pfadteil umbenennt. Ein Waechter, der nichts
#: sieht, meldet auch nichts.
MINDESTENS_BEWACHT = 5


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


def _schreibstellen() -> dict[tuple[str, str], set[str]]:
    """Wo in ``app/routers`` an einem Konto geschrieben wird.

    Drei Formen, und jede hat ihren Grund:

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
MINDESTENS_SCHREIBSTELLEN = 15


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
