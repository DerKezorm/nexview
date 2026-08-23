"""Kinderkonten: anlegen, aendern, loeschen - und wer das darf.

Wohin ein angemeldetes Kind ueberhaupt kommt, prueft ``test_child_permissions``
- und zwar ueber die ganze Routentabelle, damit ein kuenftiger neuer Router die
Grenze nicht stillschweigend unterlaufen kann.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, User

from .conftest import auth_headers, create_user


def _eltern(client: TestClient, username: str = "elternteil") -> dict[str, str]:
    """Ein gewoehnliches Konto **mit** dem Recht, Kinderkonten anzulegen.

    Das Recht ist standardmaessig aus - der Betreiber entscheidet, wer Konten
    auf seiner Installation erzeugen darf. Getestet wird es eigens in
    ``test_ohne_recht_kein_kinderkonto``.
    """
    create_user(client, username, "eltern-passwort", can_manage_children=True)
    return auth_headers(client, username, "eltern-passwort")


def _anlegen(
    client: TestClient,
    kopf: dict[str, str],
    username: str = "kind",
    password: str = "kind-passwort",
    age: int = 6,
    **extra: object,
):
    return client.post(
        "/api/children",
        json={"username": username, "password": password, "age": age, **extra},
        headers=kopf,
    )


def test_anlegen_und_auflisten(admin_client: TestClient) -> None:
    kopf = _eltern(admin_client)

    antwort = _anlegen(admin_client, kopf, display_name="Lena")
    assert antwort.status_code == 201, antwort.text
    kind = antwort.json()
    assert kind["username"] == "kind"
    assert kind["display_name"] == "Lena"
    assert kind["age"] == 6
    assert kind["is_active"] is True

    liste = admin_client.get("/api/children", headers=kopf)
    assert [eintrag["id"] for eintrag in liste.json()] == [kind["id"]]

    # Was das Konto in der Datenbank ausmacht: Rolle, Elternteil, keine
    # Adresse - und trotzdem als bestaetigt gefuehrt, weil es nichts zu
    # bestaetigen gibt.
    with SessionLocal() as sitzung:
        gespeichert = sitzung.get(User, kind["id"])
        assert gespeichert is not None
        assert gespeichert.role == Role.child
        assert gespeichert.parent_id is not None
        assert gespeichert.email is None
        assert gespeichert.email_verified is True
        assert gespeichert.hide_unrated is True


def test_kind_kann_sich_anmelden(admin_client: TestClient) -> None:
    """Ohne bestaetigte Adresse - ein Kind hat keine, die zu bestaetigen waere."""
    kopf = _eltern(admin_client)
    _anlegen(admin_client, kopf)

    kind_kopf = auth_headers(admin_client, "kind", "kind-passwort")
    ich = admin_client.get("/api/auth/me", headers=kind_kopf).json()
    assert ich["role"] == "child"


def test_name_ist_installationsweit_eindeutig(admin_client: TestClient) -> None:
    """Zwei Familien, ein Wunschname - der zweite muss sich etwas einfallen lassen."""
    eltern_a = _eltern(admin_client, "familie-a")
    eltern_b = _eltern(admin_client, "familie-b")

    assert _anlegen(admin_client, eltern_a, "max").status_code == 201

    # Auch mit anderer Gross-/Kleinschreibung: die Anmeldung unterscheidet sie
    # nicht, also darf es die Vergabe auch nicht.
    kollision = _anlegen(admin_client, eltern_b, "Max")
    assert kollision.status_code == 409
    # Eine blosse Absage liesse das Elternteil raten - es kommt ein Vorschlag mit.
    assert "Max" in kollision.json()["detail"]
    assert "Frei wäre" in kollision.json()["detail"]


def test_name_kollidiert_auch_mit_erwachsenem_konto(admin_client: TestClient) -> None:
    kopf = _eltern(admin_client)
    create_user(admin_client, "vorhanden")
    assert _anlegen(admin_client, kopf, "vorhanden").status_code == 409


def test_zu_kurzes_passwort(admin_client: TestClient) -> None:
    kopf = _eltern(admin_client)
    assert _anlegen(admin_client, kopf, password="ab").status_code == 422


def test_kind_darf_selbst_keine_kinder_anlegen(admin_client: TestClient) -> None:
    """Sonst umginge ein Kind die Grenze, indem es sich selbst eines anlegt."""
    kopf = _eltern(admin_client)
    kind = _anlegen(admin_client, kopf).json()

    # Anmelden geht nicht, also wird die Regel direkt am Dienst geprueft.
    from app.services import children

    with SessionLocal() as sitzung:
        assert children.darf_kinder_anlegen(sitzung.get(User, kind["id"])) is False


def test_altersbeschraenktes_konto_darf_keine_kinder_anlegen(
    admin_client: TestClient,
) -> None:
    """Sonst legte sich ein 14-Jaehriger ein Kind ohne Grenze an und saehe alles."""
    create_user(admin_client, "jugendlich", "jugend-passwort", age=14)
    kopf = auth_headers(admin_client, "jugendlich", "jugend-passwort")

    antwort = _anlegen(admin_client, kopf)
    assert antwort.status_code == 403


def test_fremdes_kind_ist_404(admin_client: TestClient) -> None:
    """Kein 403 - das wuerde bestaetigen, dass es diese Nummer gibt."""
    eltern_a = _eltern(admin_client, "familie-a")
    eltern_b = _eltern(admin_client, "familie-b")
    kind = _anlegen(admin_client, eltern_a, "kind-a").json()

    for aufruf in (
        admin_client.patch(f"/api/children/{kind['id']}", json={"age": 12}, headers=eltern_b),
        admin_client.post(
            f"/api/children/{kind['id']}/password",
            json={"password": "neues-passwort"},
            headers=eltern_b,
        ),
        admin_client.delete(f"/api/children/{kind['id']}", headers=eltern_b),
    ):
        assert aufruf.status_code == 404, aufruf.text

    # Und die Liste des anderen bleibt leer.
    assert admin_client.get("/api/children", headers=eltern_b).json() == []


def test_aendern_und_passwort_setzen(admin_client: TestClient) -> None:
    kopf = _eltern(admin_client)
    kind = _anlegen(admin_client, kopf).json()

    geaendert = admin_client.patch(
        f"/api/children/{kind['id']}",
        json={"age": 12, "display_name": "Lena", "is_active": False},
        headers=kopf,
    )
    assert geaendert.status_code == 200
    assert geaendert.json()["age"] == 12
    assert geaendert.json()["display_name"] == "Lena"
    assert geaendert.json()["is_active"] is False

    with SessionLocal() as sitzung:
        vorher = sitzung.get(User, kind["id"]).password_hash

    assert (
        admin_client.post(
            f"/api/children/{kind['id']}/password",
            json={"password": "ganz-neues-passwort"},
            headers=kopf,
        ).status_code
        == 200
    )

    with SessionLocal() as sitzung:
        assert sitzung.get(User, kind["id"]).password_hash != vorher


def test_loeschen(admin_client: TestClient) -> None:
    kopf = _eltern(admin_client)
    kind = _anlegen(admin_client, kopf).json()

    assert admin_client.delete(f"/api/children/{kind['id']}", headers=kopf).status_code == 204
    assert admin_client.get("/api/children", headers=kopf).json() == []

    with SessionLocal() as sitzung:
        assert sitzung.get(User, kind["id"]) is None


def test_elternteil_loeschen_nimmt_die_kinder_mit(admin_client: TestClient) -> None:
    """Ohne diesen Schritt scheiterte das Loeschen an der Fremdschluessel-Regel."""
    kopf = _eltern(admin_client)
    kind = _anlegen(admin_client, kopf).json()

    with SessionLocal() as sitzung:
        eltern_id = sitzung.query(User).filter(User.username == "elternteil").one().id

    antwort = admin_client.delete(f"/api/users/{eltern_id}")
    assert antwort.status_code == 204, antwort.text

    with SessionLocal() as sitzung:
        assert sitzung.get(User, kind["id"]) is None


def test_kinderrolle_ist_ueber_andere_wege_verboten(admin_client: TestClient) -> None:
    """Ein Kind ohne Elternteil koennte niemand mehr verwalten.

    Und schlimmer: ``update_user`` prueft den letzten Administrator an
    ``role == user``. Waere ``child`` erlaubt, liesse sich der letzte Admin
    daran vorbei herabstufen.
    """
    nutzer = create_user(admin_client, "normal")

    assert (
        admin_client.patch(f"/api/users/{nutzer['id']}", json={"role": "child"}).status_code
        == 422
    )
    assert (
        admin_client.post(
            "/api/users/invitations", json={"email": "kind@beispiel.de", "role": "child"}
        ).status_code
        == 422
    )


def test_kind_erscheint_in_der_benutzerliste_des_admins(admin_client: TestClient) -> None:
    """Sonst gaebe es Konten, von denen der Betreiber nichts weiss."""
    kopf = _eltern(admin_client)
    kind = _anlegen(admin_client, kopf).json()

    with SessionLocal() as sitzung:
        eltern_id = sitzung.query(User).filter(User.username == "elternteil").one().id

    zeile = next(
        eintrag for eintrag in admin_client.get("/api/users").json() if eintrag["id"] == kind["id"]
    )
    assert zeile["role"] == "child"
    assert zeile["parent_id"] == eltern_id


def test_ohne_recht_kein_kinderkonto(admin_client: TestClient) -> None:
    """Das Recht ist standardmaessig aus und muss vom Administrator kommen."""
    create_user(admin_client, "ohne-recht", "eltern-passwort")
    kopf = auth_headers(admin_client, "ohne-recht", "eltern-passwort")

    assert _anlegen(admin_client, kopf).status_code == 403

    # Der Administrator gibt das Recht - danach geht es.
    with SessionLocal() as sitzung:
        kennung = sitzung.query(User).filter(User.username == "ohne-recht").one().id
    assert (
        admin_client.patch(
            f"/api/users/{kennung}", json={"can_manage_children": True}
        ).status_code
        == 200
    )
    assert _anlegen(admin_client, kopf).status_code == 201


def test_admin_braucht_das_recht_nicht(admin_client: TestClient) -> None:
    """Wie ueberall: Den Haken koennte er sich selbst setzen."""
    antwort = _anlegen(admin_client, {}, "admin-kind")
    assert antwort.status_code == 201, antwort.text


def test_alter_ueber_16_wird_abgelehnt(admin_client: TestClient) -> None:
    """Ab 17 ist es kein Kinderkonto mehr, sondern ein gewoehnliches."""
    kopf = _eltern(admin_client)
    assert _anlegen(admin_client, kopf, age=17).status_code == 422
    assert _anlegen(admin_client, kopf, "gerade-noch", age=16).status_code == 201


def test_rubriken(admin_client: TestClient) -> None:
    kopf = _eltern(admin_client)

    verfuegbar = admin_client.get("/api/children/genres", headers=kopf).json()
    assert "animation" in verfuegbar and "kids" in verfuegbar

    # Ohne Angabe: leer gespeichert - und das heisst "alle".
    ohne = _anlegen(admin_client, kopf, "ohne-auswahl").json()
    assert ohne["genres"] == []

    mit = _anlegen(
        admin_client, kopf, "mit-auswahl", genres=["family", "animation"]
    ).json()
    # Die Reihenfolge folgt der festen Liste, nicht der Reihenfolge der Haekchen.
    assert mit["genres"] == ["animation", "family"]

    geaendert = admin_client.patch(
        f"/api/children/{mit['id']}", json={"genres": ["comedy"]}, headers=kopf
    )
    assert geaendert.json()["genres"] == ["comedy"]

    assert (
        _anlegen(admin_client, kopf, "unfug", genres=["horror"]).status_code == 422
    )


def test_recht_entziehen_legt_die_kinder_stumm(admin_client: TestClient) -> None:
    """Sonst waere der Entzug wirkungslos - die Kinder liefen einfach weiter."""
    kopf = _eltern(admin_client)
    kind = _anlegen(admin_client, kopf).json()
    assert kind["is_active"] is True

    with SessionLocal() as sitzung:
        eltern_id = sitzung.query(User).filter(User.username == "elternteil").one().id

    assert (
        admin_client.patch(
            f"/api/users/{eltern_id}", json={"can_manage_children": False}
        ).status_code
        == 200
    )

    with SessionLocal() as sitzung:
        assert sitzung.get(User, kind["id"]).is_active is False

    # Und anmelden geht damit auch nicht mehr.
    antwort = admin_client.post(
        "/api/auth/login",
        json={"username": "kind", "password": "kind-passwort"},
        headers={"Authorization": ""},
    )
    assert antwort.status_code == 403

    # Geloescht wird das Konto ausdruecklich **nicht** - der Administrator soll
    # die Freigabe zuruecknehmen koennen, ohne dass Konten verschwinden.
    with SessionLocal() as sitzung:
        assert sitzung.get(User, kind["id"]) is not None


def test_freigabe_beantragen_legt_ein_ticket_an(admin_client: TestClient) -> None:
    create_user(admin_client, "ohne-recht", "eltern-passwort")
    kopf = auth_headers(admin_client, "ohne-recht", "eltern-passwort")

    assert admin_client.post("/api/children/request-permission", headers=kopf).status_code == 204

    tickets = admin_client.get("/api/tickets").json()
    assert [t["subject"] for t in tickets] == ["Freigabe für Kinderkonten"]

    # Ein zweiter Antrag waere nur Laerm, solange der erste offen ist.
    assert admin_client.post("/api/children/request-permission", headers=kopf).status_code == 409


def test_wer_die_freigabe_hat_beantragt_sie_nicht(admin_client: TestClient) -> None:
    kopf = _eltern(admin_client)
    assert admin_client.post("/api/children/request-permission", headers=kopf).status_code == 409
    # Und der Administrator schon gar nicht - er setzt den Haken selbst.
    assert admin_client.post("/api/children/request-permission").status_code == 403


def test_sprache_kommt_vom_elternteil_oder_aus_der_eingabe(admin_client: TestClient) -> None:
    """Ein Kind stellt sie nicht selbst um - es gibt dafuer keinen Schalter."""
    kopf = _eltern(admin_client)

    geerbt = _anlegen(admin_client, kopf, "kind-geerbt").json()
    assert geerbt["language"] == "de"

    gesetzt = _anlegen(admin_client, kopf, "kind-englisch", language="en").json()
    assert gesetzt["language"] == "en"

    geaendert = admin_client.patch(
        f"/api/children/{geerbt['id']}", json={"language": "en"}, headers=kopf
    )
    assert geaendert.json()["language"] == "en"


def test_admin_schaltet_direkt_aus_dem_ticket_frei(admin_client: TestClient) -> None:
    """Ein Klick statt fuenf Schritte durch die Benutzerverwaltung."""
    create_user(admin_client, "ohne-recht", "eltern-passwort")
    kopf = auth_headers(admin_client, "ohne-recht", "eltern-passwort")
    admin_client.post("/api/children/request-permission", headers=kopf)

    ticket = admin_client.get("/api/tickets").json()[0]
    detail = admin_client.get(f"/api/tickets/{ticket['id']}").json()
    assert detail["kinderkonten_offen"] is True

    erledigt = admin_client.post(
        f"/api/tickets/{ticket['id']}/kinderkonten-freischalten"
    )
    assert erledigt.status_code == 200, erledigt.text
    # Recht gesetzt, Antwort geschrieben, Ticket geschlossen - alles in einem.
    assert erledigt.json()["status"] == "closed"
    assert len(erledigt.json()["messages"]) == 2
    assert erledigt.json()["kinderkonten_offen"] is False

    with SessionLocal() as sitzung:
        assert (
            sitzung.query(User).filter(User.username == "ohne-recht").one().can_manage_children
            is True
        )

    # Und der Nutzer kann sofort anlegen.
    assert _anlegen(admin_client, kopf).status_code == 201

    # Ein zweites Mal gibt es nichts mehr freizuschalten.
    assert (
        admin_client.post(
            f"/api/tickets/{ticket['id']}/kinderkonten-freischalten"
        ).status_code
        == 409
    )


def test_der_knopf_erscheint_nur_am_richtigen_ticket(admin_client: TestClient) -> None:
    create_user(admin_client, "jemand", "passwort-1234")
    kopf = auth_headers(admin_client, "jemand", "passwort-1234")
    admin_client.post(
        "/api/tickets",
        json={"subject": "Ganz was anderes", "body": "Hallo"},
        headers=kopf,
    )
    ticket = admin_client.get("/api/tickets").json()[0]
    detail = admin_client.get(f"/api/tickets/{ticket['id']}").json()
    assert detail["kinderkonten_offen"] is False
    assert (
        admin_client.post(
            f"/api/tickets/{ticket['id']}/kinderkonten-freischalten"
        ).status_code
        == 409
    )
