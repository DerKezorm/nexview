"""Wohin ein Kinderkonto kommt - und vor allem: wohin nicht.

Der erste Test hier laeuft ueber die **ganze Routentabelle**. Er ist der
eigentliche Wachhund: Wer kuenftig einen neuen Router einhaengt und die
Erwachsenen-Pruefung vergisst, faellt hier auf und nicht erst, wenn ein Kind
in den Einstellungen steht.

Die Regel dahinter ist eine **Erlaubnisliste**: Ein Pfad ist entweder
ausdruecklich fuer Kinder gedacht (``KINDER_ERLAUBT``), oder er haengt an einer
Wache - ``require_adult``, ``require_admin`` oder ``require_approver``. Ein
Kind ist weder Administrator noch Entscheider, deshalb zaehlen alle drei.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.deps import get_current_user, require_admin, require_adult, require_approver
from app.main import app

from .conftest import auth_headers, create_user

WACHEN = {require_adult, require_admin, require_approver}

# Pfade, die ein Kinderkonto erreichen darf - oder die gar keine Anmeldung
# verlangen (Einrichtung, Einladungslinks, Bilder).
#
# Jeder Eintrag ist eine bewusste Entscheidung. Wer hier etwas ergaenzt, sollte
# sich fragen: Was sieht ein Achtjaehriger damit?
KINDER_ERLAUBT = {
    "/api/health",
    "/api/config",
    "/api/auth/login",
    "/api/auth/refresh",
    # Abmelden nimmt nur ein Cookie weg und verlangt deshalb keine Anmeldung -
    # ein Kind darf sich selbstverstaendlich abmelden.
    "/api/auth/logout",
    "/api/auth/me",
    "/api/setup/status",
    "/api/setup/admin",
    # Aus einer Sicherung starten statt bei null. Dieselbe Einordnung wie
    # "/api/setup/admin": Beide sind zu, sobald **ein** Konto existiert - und
    # ein Kinderkonto kann es vorher gar nicht geben, denn Kinder sind
    # Unterprofile ihrer Eltern. Ein Achtjaehriger sieht damit also nichts.
    # Die zugesagte Schnittstelle kennt nur einen Pfad ohne Anmeldung: die
    # Frage, ob Nexview laeuft. Alles andere unter /api/v1 haengt an
    # NUR_ERWACHSENE - siehe main.py.
    "/api/v1/health",
    "/api/setup/sicherung/pruefen",
    "/api/setup/sicherung/einspielen",
    "/api/users/avatar/{name}",
    "/api/demo/poster/{media_type}/{tmdb_id}.svg",
    # Anmeldung ueber den Media-Server: laeuft ohne angemeldeten Benutzer, und
    # ein Kinderkonto hat dort ohnehin kein Gegenstueck.
    "/api/auth/mediaserver/login/start",
    "/api/auth/mediaserver/login/poll",
    # Derselbe Weg fuer Anbieter ohne Vermittler (Jellyfin, Emby): Benutzername
    # und Passwort statt Code. Ebenfalls ohne angemeldeten Benutzer - und ein
    # Kinderkonto hat auf dem Medienserver kein Gegenstueck, denn Kinder sind
    # Unterprofile ihrer Eltern und existieren dort gar nicht.
    "/api/auth/mediaserver/login/password",
}

KINDER_PRAEFIXE = ("/api/kids/", "/api/onboarding/")


def _wachen_einer_route(route) -> set:
    gefunden: set = set()

    def sammeln(dependant) -> None:
        for eintrag in dependant.dependencies:
            gefunden.add(eintrag.call)
            sammeln(eintrag)

    sammeln(route.dependant)
    return gefunden


def test_jede_route_ist_entschieden() -> None:
    """Kein Pfad darf ohne Entscheidung dastehen."""
    offen = []
    for route in app.routes:
        pfad = str(getattr(route, "path", ""))
        if not pfad.startswith("/api") or not getattr(route, "methods", None):
            continue
        if pfad in KINDER_ERLAUBT or pfad.startswith(KINDER_PRAEFIXE):
            continue

        wachen = _wachen_einer_route(route)
        if wachen & WACHEN:
            continue
        # Pfade ganz ohne Anmeldung muessen ausdruecklich in der Liste stehen -
        # sonst rutscht ein neuer offener Endpunkt unbemerkt durch.
        if get_current_user not in wachen:
            offen.append(pfad)
            continue
        offen.append(pfad)

    assert not offen, (
        "Diese Pfade sind weder für Kinder freigegeben noch geschützt: "
        + ", ".join(sorted(set(offen)))
    )


def _kind(client: TestClient) -> dict[str, str]:
    create_user(client, "elternteil", "eltern-passwort", can_manage_children=True)
    eltern = auth_headers(client, "elternteil", "eltern-passwort")
    client.post(
        "/api/children",
        json={"username": "kind", "password": "kind-passwort", "age": 6},
        headers=eltern,
    )
    return auth_headers(client, "kind", "kind-passwort")


def test_kind_kommt_nirgends_hin_wo_es_nicht_hingehoert(admin_client: TestClient) -> None:
    """Stichprobe quer durch die Anwendung - alles muss 403 sein."""
    kopf = _kind(admin_client)

    for pfad in (
        "/api/tickets",
        "/api/notifications",
        "/api/favorites",
        "/api/watchlist/status",
        "/api/storage/me",
        "/api/requests/mine",
        "/api/requests/quota",
        "/api/home/trending",
        "/api/calendar",
        "/api/people",
        "/api/about",
        "/api/discover/movie",
        "/api/search/movie?q=test",
        "/api/detail/movie/603",
        "/api/children",
    ):
        antwort = admin_client.get(pfad, headers=kopf)
        assert antwort.status_code == 403, f"{pfad} -> {antwort.status_code}"

    # Und die Verwaltungsseiten sowieso.
    for pfad in ("/api/users", "/api/settings", "/api/logs", "/api/admin/requests"):
        assert admin_client.get(pfad, headers=kopf).status_code == 403, pfad

    # ⚠️ **Und die zugesagte Schnittstelle, mit denselben Handlern.**
    #
    # Der Schutz haengt bei Nexview am Einhaengen des Routers, nicht am
    # Handler. Eine zweite Registrierung derselben Funktion unter einer
    # anderen Adresse kommt also daran **vorbei**, wenn man es vergisst -
    # genau das ist beim ersten Anlauf von /api/v1 passiert. Der Test darueber
    # hat es gefunden; dieser hier weist nach, dass die Behebung wirkt.
    for pfad in (
        "/api/v1/requests/mine",
        "/api/v1/requests/quota",
        "/api/v1/storage/me",
        "/api/v1/home/recent",
        "/api/v1/about",
        "/api/v1/search/movie?q=test",
        "/api/v1/tickets/open-count",
        "/api/v1/notifications/unread/count",
    ):
        antwort = admin_client.get(pfad, headers=kopf)
        assert antwort.status_code == 403, f"{pfad} -> {antwort.status_code}"

    # Nur die Frage "laeufst du noch" steht offen - wie /api/health.
    assert admin_client.get("/api/v1/health", headers=kopf).status_code == 200


def test_kind_darf_sein_passwort_nicht_selbst_setzen(admin_client: TestClient) -> None:
    """Sonst koennte es sich gegen das Elternteil aussperren."""
    kopf = _kind(admin_client)
    antwort = admin_client.post(
        "/api/auth/me/password",
        json={"current_password": "kind-passwort", "new_password": "heimlich-123"},
        headers=kopf,
    )
    assert antwort.status_code == 403


def test_kind_darf_keine_mailadresse_eintragen(admin_client: TestClient) -> None:
    kopf = _kind(admin_client)
    antwort = admin_client.put(
        "/api/auth/me/email", json={"email": "kind@beispiel.de"}, headers=kopf
    )
    assert antwort.status_code == 403


def test_kind_darf_seine_sprache_umstellen(admin_client: TestClient) -> None:
    """Was harmlos ist, bleibt erlaubt - sonst waere die Sperre Schikane."""
    kopf = _kind(admin_client)
    antwort = admin_client.patch("/api/auth/me", json={"language": "en"}, headers=kopf)
    assert antwort.status_code == 200
    assert antwort.json()["language"] == "en"


def test_erwachsener_kommt_nicht_in_die_kinderansicht(admin_client: TestClient) -> None:
    """Die Grenze gilt in beide Richtungen - sonst waere sie keine."""
    for pfad in ("/api/kids/categories", "/api/kids/wishes"):
        assert admin_client.get(pfad).status_code == 403, pfad
