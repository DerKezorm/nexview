"""Wohin ein Kinderkonto kommt - und vor allem: wohin nicht.

Der erste Test hier laeuft ueber die **ganze Routentabelle**. Er ist der
eigentliche Wachhund: Wer kuenftig einen neuen Router einhaengt und die
Erwachsenen-Pruefung vergisst, faellt hier auf und nicht erst, wenn ein Kind
in den Einstellungen steht.

Die Regel dahinter ist eine **Erlaubnisliste**: Ein Pfad ist entweder
ausdruecklich fuer Kinder gedacht (``KINDER_ERLAUBT``), oder er haengt an einer
Wache - ``require_adult``, ``require_admin``, ``require_approver`` oder
``require_child``. Ein Kind ist weder Administrator noch Entscheider, deshalb
zaehlen die ersten drei; ``require_child`` zaehlt, weil es die Entscheidung in
die andere Richtung faellt - Kinderansicht, Erwachsene draussen.

⚠️ **Es gab hier einen Praefix, und der war das Loch.** Bis zum 02.09.2026
nahm ``KINDER_PRAEFIXE`` jeden Pfad unter ``/api/kids/`` und
``/api/onboarding/`` pauschal aus. Ein ``@router.delete("/alles-loeschen")``
ganz ohne Abhaengigkeit kam damit durch - ohne jede Anmeldung, und der Test,
den diese Datei "den eigentlichen Wachhund" nennt, blieb gruen. Jetzt braucht
auch dort jeder Pfad eine Entscheidung: eine Wache oder einen Eintrag mit
Grund.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.deps import require_admin, require_adult, require_approver, require_child
from app.main import app

from .conftest import auth_headers, create_user

WACHEN = {require_adult, require_admin, require_approver, require_child}

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
    # Der Umzug aus einer laufenden Seerr-Installation. Dieselbe Einordnung
    # und derselbe Riegel wie die zwei Zeilen darueber: ``has_any_user``.
    # Sobald **ein** Konto existiert, antworten sie mit 409 - und ein
    # Kinderkonto kann es davor nicht geben, weil Kinder Unterprofile ihrer
    # Eltern sind. Wer hier vor dem Besitzer ankommt, kann ohnehin schon
    # ``/api/setup/admin`` und uebernimmt die ganze Installation; diese drei
    # machen die Luecke nicht groesser.
    "/api/setup/seerr/pruefen",
    "/api/setup/seerr/vorschau",
    "/api/setup/seerr/uebernehmen",
    "/api/users/avatar/{name}",
    # Bilder aus der Hausordnung - dieselbe Einordnung wie das Profilbild
    # darueber, und aus demselben Grund: Ein ``<img>``-Element schickt keinen
    # Token mit, und das Sitzungs-Cookie gilt nur unter ``/api/auth``. Ein
    # Endpunkt mit Anmeldung liefert im Browser also **nie** ein Bild (so
    # gemeldet). Der Schutz ist der unratbare Dateiname: 32 Hexziffern.
    # Ein Kind sieht damit hoechstens ein Bild aus einem Text, den seine
    # Eltern ohnehin lesen - und nur, wenn es den Namen kennt.
    "/api/hausordnung/bild/{name}",
    "/api/demo/poster/{media_type}/{tmdb_id}.svg",
    # Anmeldung ueber den Media-Server: laeuft ohne angemeldeten Benutzer, und
    # ein Kinderkonto hat dort ohnehin kein Gegenstueck.
    "/api/auth/mediaserver/login/start",
    "/api/auth/mediaserver/login/poll",
    # Anmeldung ueber fremde Anbieter (OIDC): dieselbe Einordnung. Die drei
    # Pfade stehen **vor** jeder Sitzung - eine Knopfliste und der Hin- und
    # Rueckweg der Anmeldung. Ein Kinderkonto hat beim Anbieter kein
    # Gegenstueck (Kinder sind Unterprofile ihrer Eltern), und die Kaskade in
    # ``oidc_accounts.resolve`` weist Kinderkonten ausdruecklich ab. Das
    # Verknuepfen und Trennen im Profil haengt dagegen an ``require_adult``.
    "/api/auth/oidc",
    "/api/auth/oidc/{slug}/login",
    "/api/auth/oidc/{slug}/callback",
    # Derselbe Weg fuer Anbieter ohne Vermittler (Jellyfin, Emby): Benutzername
    # und Passwort statt Code. Ebenfalls ohne angemeldeten Benutzer - und ein
    # Kinderkonto hat auf dem Medienserver kein Gegenstueck, denn Kinder sind
    # Unterprofile ihrer Eltern und existieren dort gar nicht.
    "/api/auth/mediaserver/login/password",
    # Der Rueckkanal von Radarr/Sonarr: verlangt das Anruf-Geheimnis der
    # Instanz und liefert nichts zurueck - ein Achtjaehriger (oder sonst
    # jemand ohne Geheimnis) sieht damit nichts. Siehe routers/webhooks.py.
    "/api/webhooks/arr/{kennung}",
    # ⚠️ **Die Einrichtungswege - frueher pauschal per Praefix ausgenommen.**
    #
    # Sie stehen alle **vor** jeder Sitzung, und ihr Nachweis ist der
    # Einmal-Link aus einer Mail oder das Passwort selbst. Fuer ein
    # Kinderkonto sind sie aus einem gemeinsamen Grund unerreichbar: Ein Kind
    # ist ein Unterprofil seiner Eltern und hat **keine Mailadresse** -
    # ``children`` legt es mit ``email=None`` und ``email_verified=True`` an.
    # Jeder Eintrag nennt zusaetzlich, woran es im Einzelnen scheitert.
    #
    # Der Weg zum neuen Passwort sucht ueber ``User.email``. Ohne Adresse
    # findet sich kein Kinderkonto, und die Antwort ist ohnehin immer
    # dieselbe - auch fuer Erwachsene.
    "/api/onboarding/forgot-password",
    # Beide verlangen ein Konto mit **unbestaetigter** Adresse. Ein
    # Kinderkonto ist von Anfang an bestaetigt; es bekaeme 409, noch bevor
    # irgendetwas passiert.
    "/api/onboarding/pending/resend",
    "/api/onboarding/pending/email",
    # Verraet nur, ob ein Benutzername vergeben ist - dieselbe Auskunft, die
    # das Absenden des Formulars ohnehin gibt.
    "/api/onboarding/username-available",
    # Die drei Wege ueber einen Einmal-Link. Wer den Link hat, hat das
    # Postfach; ein Kinderkonto hat keines, und eine Einladung an ein Kind
    # gibt es nicht - Kinderkonten legen die Eltern an.
    "/api/onboarding/invitation/{raw}",
    "/api/onboarding/password/{raw}",
    "/api/onboarding/verify/{raw}",
}


def _wachen_einer_route(route) -> set:
    gefunden: set = set()

    def sammeln(dependant) -> None:
        for eintrag in dependant.dependencies:
            gefunden.add(eintrag.call)
            sammeln(eintrag)

    sammeln(route.dependant)
    return gefunden


def _entschiedene_pfade() -> tuple[list[str], int]:
    """Alle offenen Pfade und die Zahl der ueberhaupt angesehenen."""
    offen: list[str] = []
    angesehen = 0
    for route in app.routes:
        pfad = str(getattr(route, "path", ""))
        if not pfad.startswith("/api") or not getattr(route, "methods", None):
            continue
        angesehen += 1
        if pfad in KINDER_ERLAUBT:
            continue
        if _wachen_einer_route(route) & WACHEN:
            continue
        # Alles Uebrige - ob ohne Anmeldung oder nur mit ``get_current_user`` -
        # ist unentschieden. Ein Kind ist ein angemeldeter Benutzer; die blosse
        # Anmeldung sagt also gar nichts.
        offen.append(pfad)
    return offen, angesehen


#: So viele Pfade sieht der Wachhund mindestens an.
#:
#: ⚠️ **Ohne diese Schwelle waere der Test still gruen, sobald er nichts mehr
#: findet** - etwa weil das Praefix ``/api`` wandert oder ``app.routes`` anders
#: heisst. Dieselbe Ueberlegung wie ``MINDESTENS_BEWACHT`` im
#: Betreiber-Waechter, und genau die hat hier bisher gefehlt.
MINDESTENS_ANGESEHEN = 200


def test_jede_route_ist_entschieden() -> None:
    """Kein Pfad darf ohne Entscheidung dastehen."""
    offen, angesehen = _entschiedene_pfade()

    assert not offen, (
        "Diese Pfade sind weder für Kinder freigegeben noch geschützt: "
        + ", ".join(sorted(set(offen)))
    )
    assert angesehen >= MINDESTENS_ANGESEHEN, (
        f"Nur {angesehen} Pfade angesehen, erwartet mindestens "
        f"{MINDESTENS_ANGESEHEN}. Der Wachhund sieht offenbar nichts mehr."
    )


def test_der_wachhund_meldet_eine_route_ohne_wache() -> None:
    """Die Mutationsprobe: Eine neue Adresse ohne Wache muss auffallen.

    ⚠️ **Genau dieser Fall kam hier einmal durch.** Ein
    ``@router.delete("/alles-loeschen")`` unter ``/api/kids/`` ohne jede
    Abhaengigkeit blieb gruen, weil der Praefix ihn ausnahm. Der Nachbau
    haengt eine solche Route voruebergehend ein und verlangt, dass sie
    gemeldet wird.
    """
    from fastapi import APIRouter

    probe = APIRouter(prefix="/api/kids", tags=["probe"])

    @probe.delete("/alles-loeschen", include_in_schema=False)
    def alles_loeschen() -> None:  # pragma: no cover - wird nie gerufen
        return None

    app.include_router(probe)
    try:
        offen, _ = _entschiedene_pfade()
        assert "/api/kids/alles-loeschen" in offen, (
            "Eine Route unter /api/kids/ ohne jede Wache fällt dem Wachhund "
            "nicht auf - genau das Loch, das der Präfix gerissen hat."
        )
    finally:
        app.router.routes[:] = [
            route
            for route in app.router.routes
            if str(getattr(route, "path", "")) != "/api/kids/alles-loeschen"
        ]

    # Und aufgeräumt ist aufgeräumt - sonst faerbte die Probe alle folgenden
    # Tests in dieser Sitzung rot.
    offen, _ = _entschiedene_pfade()
    assert "/api/kids/alles-loeschen" not in offen


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
