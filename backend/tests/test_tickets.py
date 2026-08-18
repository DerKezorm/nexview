"""Ticketcenter.

Schwerpunkt ist die **Sichtbarkeit**. Ein Fehler an einer Statusanzeige ist
aergerlich; ein Fehler hier heisst, dass jemand fremde Post liest. Deshalb wird
jede Rolle einzeln gegen jeden Endpunkt geprueft.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    Notification,
    NotificationType,
    Role,
    Ticket,
    TicketMessage,
    TicketStatus,
    User,
)

from .conftest import auth_headers, create_user


def _anlegen(client: TestClient, headers: dict | None = None, **felder: object):
    daten = {"subject": "Ich komme nicht rein", "body": "Passwort vergessen.", **felder}
    return client.post("/api/tickets", json=daten, headers=headers)


def _als(client: TestClient, name: str) -> dict[str, str]:
    return auth_headers(client, name, "passwort-1234")


# --- Sichtbarkeit ------------------------------------------------------------


def test_fremdes_ticket_ist_nicht_zu_sehen(client: TestClient, admin_client: TestClient) -> None:
    """Der wichtigste Test der Datei."""
    create_user(admin_client, "anna")
    create_user(admin_client, "bert")

    anna = _als(client, "anna")
    bert = _als(client, "bert")

    ticket = _anlegen(client, anna).json()

    # In der Liste taucht es bei Bert nicht auf ...
    assert client.get("/api/tickets", headers=bert).json() == []

    # ... und einzeln bekommt er 404, nicht 403. Ein "verboten" waere schon
    # die Auskunft, dass es diese Nummer gibt.
    assert client.get(f"/api/tickets/{ticket['id']}", headers=bert).status_code == 404

    # Antworten kann er auch nicht.
    antwort = client.post(
        f"/api/tickets/{ticket['id']}/messages", json={"body": "Hallo?"}, headers=bert
    )
    assert antwort.status_code == 404

    # Und ueber den Benutzerfilter kommt er ebenso wenig heran.
    fremd = client.get(f"/api/tickets?user_id={ticket['user_id']}", headers=bert).json()
    assert fremd == []


def test_admin_sieht_alle_tickets(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "clara")
    ticket = _anlegen(client, _als(client, "clara")).json()

    alle = admin_client.get("/api/tickets").json()
    assert [t["id"] for t in alle] == [ticket["id"]]
    assert admin_client.get(f"/api/tickets/{ticket['id']}").status_code == 200


def test_entscheider_ist_hier_kein_admin(client: TestClient, admin_client: TestClient) -> None:
    """Er entscheidet ueber Anfragen - er liest nicht die Post der anderen."""
    create_user(admin_client, "dora")
    create_user(admin_client, "pruefer", role=Role.approver)

    ticket = _anlegen(client, _als(client, "dora")).json()
    pruefer = _als(client, "pruefer")

    assert client.get("/api/tickets", headers=pruefer).json() == []
    assert client.get(f"/api/tickets/{ticket['id']}", headers=pruefer).status_code == 404
    assert (
        client.patch(
            f"/api/tickets/{ticket['id']}", json={"status": "closed"}, headers=pruefer
        ).status_code
        == 403
    )


def test_ohne_anmeldung_kein_zugriff(client: TestClient) -> None:
    assert client.get("/api/tickets", headers={"Authorization": ""}).status_code == 401


# --- Verlauf -----------------------------------------------------------------


def test_ticket_beginnt_offen_und_mit_der_ersten_nachricht(
    client: TestClient, admin_client: TestClient
) -> None:
    create_user(admin_client, "emil")
    ticket = _anlegen(client, _als(client, "emil")).json()

    assert ticket["status"] == "open"
    assert len(ticket["messages"]) == 1
    assert ticket["messages"][0]["body"] == "Passwort vergessen."
    # Die eigene Nachricht kommt nicht "von drueben".
    assert ticket["messages"][0]["from_staff"] is False


def test_hin_und_her(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "frida")
    headers = _als(client, "frida")
    ticket = _anlegen(client, headers).json()

    admin_client.post(f"/api/tickets/{ticket['id']}/messages", json={"body": "Ich schau nach."})
    nachher = client.post(
        f"/api/tickets/{ticket['id']}/messages", json={"body": "Danke!"}, headers=headers
    ).json()

    assert [m["body"] for m in nachher["messages"]] == [
        "Passwort vergessen.",
        "Ich schau nach.",
        "Danke!",
    ]
    assert [m["from_staff"] for m in nachher["messages"]] == [False, True, False]


# --- Der Administrator schreibt jemanden an ----------------------------------


def test_admin_eroeffnet_ein_ticket_fuer_einen_benutzer(
    client: TestClient, admin_client: TestClient
) -> None:
    """Die umgekehrte Richtung: nicht angeschrieben werden, sondern schreiben.

    Das Ticket gehoert dem Empfaenger - er findet es unter seinen Tickets und
    kann antworten. Die erste Nachricht traegt aber den Administrator als
    Verfasser.
    """
    ziel = create_user(admin_client, "norbert")

    ticket = _anlegen(
        admin_client,
        subject="Bitte Passwort ändern",
        body="Deins ist zu kurz.",
        user_id=ziel["id"],
    ).json()

    # Es gehoert dem Empfaenger ...
    assert ticket["user_id"] == ziel["id"]
    assert ticket["username"] == "norbert"
    # ... geschrieben hat es der Administrator.
    assert ticket["opened_by_name"] == "admin"
    assert ticket["messages"][0]["username"] == "admin"

    # Der Benutzer sieht es und kann antworten.
    headers = _als(client, "norbert")
    seine = client.get("/api/tickets", headers=headers).json()
    assert [t["id"] for t in seine] == [ticket["id"]]

    antwort = client.post(
        f"/api/tickets/{ticket['id']}/messages", json={"body": "Mach ich."}, headers=headers
    )
    assert antwort.status_code == 201


def test_angeschriebener_benutzer_wird_benachrichtigt(
    client: TestClient, admin_client: TestClient
) -> None:
    """Nicht die uebrigen Administratoren - der Empfaenger."""
    ziel = create_user(admin_client, "olivia")
    _anlegen(admin_client, subject="Kurze Frage", body="Läuft alles?", user_id=ziel["id"])

    with SessionLocal() as session:
        meldungen = session.query(Notification).filter(Notification.ticket_id.is_not(None)).all()
        assert [m.user_id for m in meldungen] == [ziel["id"]]
        assert meldungen[0].message_key == "notifications.ticketNew"
        # Auch die Einstufung muss stimmen, nicht nur der Text - eine als
        # "Antwort" abgelegte Eroeffnung waere eine Zeitbombe fuer jede spaetere
        # Auswertung.
        assert meldungen[0].type == NotificationType.ticket_new


def test_zwei_administratoren_koennen_sich_gegenseitig_schreiben(
    client: TestClient, admin_client: TestClient
) -> None:
    """Haelt die Logik auch, wenn beide Seiten Administrator sind?

    Sie haelt, weil sie nirgends "ist Admin" gegen "ist Benutzer" stellt,
    sondern immer nur "gehoert mir" gegen "gehoert einem anderen". Der Zweite
    ist Eigentuemer des Tickets - unabhaengig von seiner Rolle.
    """
    zweiter = create_user(admin_client, "admin2", role=Role.admin)
    headers = _als(client, "admin2")

    ticket = _anlegen(
        admin_client, subject="Unter uns", body="Kurze Absprache.", user_id=zweiter["id"]
    ).json()
    assert ticket["user_id"] == zweiter["id"]
    assert ticket["opened_by_name"] == "admin"

    # Beide sehen es - der eine als Eigentuemer, der andere als Administrator.
    assert ticket["id"] in [t["id"] for t in admin_client.get("/api/tickets").json()]
    assert ticket["id"] in [
        t["id"] for t in client.get("/api/tickets", headers=headers).json()
    ]

    # Der Angeschriebene wurde benachrichtigt, der Absender nicht.
    with SessionLocal() as session:
        empfaenger = [
            m.user_id
            for m in session.query(Notification)
            .filter(Notification.type == NotificationType.ticket_new)
            .all()
        ]
        assert empfaenger == [zweiter["id"]]

    # Antwort des Zweiten geht an die uebrigen Administratoren zurueck.
    client.post(
        f"/api/tickets/{ticket['id']}/messages", json={"body": "Passt."}, headers=headers
    )
    with SessionLocal() as session:
        erster = session.query(User).filter(User.username == "admin").one()
        antworten = (
            session.query(Notification)
            .filter(Notification.type == NotificationType.ticket_reply)
            .all()
        )
        assert [m.user_id for m in antworten] == [erster.id]

    # Und "von drueben" stimmt aus Sicht des Eigentuemers.
    verlauf = admin_client.get(f"/api/tickets/{ticket['id']}").json()
    assert [m["from_staff"] for m in verlauf["messages"]] == [True, False]


def test_benutzer_darf_niemanden_anschreiben(client: TestClient, admin_client: TestClient) -> None:
    """Sonst koennte jeder jedem schreiben - und die Sichtbarkeitsregel waere
    nur noch eine Empfehlung."""
    opfer = create_user(admin_client, "paul")
    create_user(admin_client, "quirin")
    create_user(admin_client, "pruefer4", role=Role.approver)

    for name in ("quirin", "pruefer4"):
        antwort = _anlegen(
            client, _als(client, name), subject="Hallo", body="Text", user_id=opfer["id"]
        )
        assert antwort.status_code == 403

    with SessionLocal() as session:
        assert session.query(Ticket).count() == 0


def test_unbekannter_empfaenger_wird_abgewiesen(admin_client: TestClient) -> None:
    assert _anlegen(admin_client, user_id=999999).status_code == 404


def test_eigene_kennung_bleibt_ein_normales_ticket(admin_client: TestClient) -> None:
    """Sich selbst anzuschreiben ist kein Sonderfall."""
    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == "admin").one()
        kennung = admin.id

    ticket = _anlegen(admin_client, user_id=kennung).json()
    assert ticket["user_id"] == kennung
    assert ticket["opened_by"] == kennung
    # Und keine Meldung von sich an sich.
    with SessionLocal() as session:
        assert session.query(Notification).filter(Notification.ticket_id.is_not(None)).count() == 0


# --- Bearbeiten --------------------------------------------------------------


def test_nur_der_verfasser_darf_seine_nachricht_aendern(
    client: TestClient, admin_client: TestClient
) -> None:
    create_user(admin_client, "gustav")
    headers = _als(client, "gustav")
    ticket = _anlegen(client, headers).json()
    eigene = ticket["messages"][0]["id"]

    mit_antwort = admin_client.post(
        f"/api/tickets/{ticket['id']}/messages", json={"body": "Erledigt."}
    ).json()
    vom_admin = mit_antwort["messages"][-1]["id"]

    # Der Benutzer darf die Antwort des Admins nicht umschreiben.
    assert (
        client.patch(
            f"/api/tickets/messages/{vom_admin}", json={"body": "Nein!"}, headers=headers
        ).status_code
        == 403
    )
    # Und der Admin nicht die des Benutzers.
    assert (
        admin_client.patch(
            f"/api/tickets/messages/{eigene}", json={"body": "Umgeschrieben"}
        ).status_code
        == 403
    )


def test_bearbeiten_vermerkt_den_zeitpunkt(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "hanna")
    headers = _als(client, "hanna")
    ticket = _anlegen(client, headers).json()
    kennung = ticket["messages"][0]["id"]
    assert ticket["messages"][0]["edited_at"] is None

    geaendert = client.patch(
        f"/api/tickets/messages/{kennung}", json={"body": "Doch nicht."}, headers=headers
    ).json()

    assert geaendert["messages"][0]["body"] == "Doch nicht."
    assert geaendert["messages"][0]["edited_at"] is not None


def test_fremde_nachricht_ist_nicht_einmal_auffindbar(
    client: TestClient, admin_client: TestClient
) -> None:
    """404 statt 403 - wie beim Ticket selbst."""
    create_user(admin_client, "ida")
    create_user(admin_client, "jonas")
    ticket = _anlegen(client, _als(client, "ida")).json()

    antwort = client.patch(
        f"/api/tickets/messages/{ticket['messages'][0]['id']}",
        json={"body": "Fremd"},
        headers=_als(client, "jonas"),
    )
    assert antwort.status_code == 404


# --- Zustand -----------------------------------------------------------------


def test_nur_der_admin_setzt_den_zustand(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "karl")
    headers = _als(client, "karl")
    ticket = _anlegen(client, headers).json()

    verweigert = client.patch(
        f"/api/tickets/{ticket['id']}", json={"status": "closed"}, headers=headers
    )
    assert verweigert.status_code == 403

    erlaubt = admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "in_progress"})
    assert erlaubt.status_code == 200
    assert erlaubt.json()["status"] == "in_progress"


def test_geschlossen_heisst_fuer_den_benutzer_zu(
    client: TestClient, admin_client: TestClient
) -> None:
    """Er sieht den Verlauf weiter, kann aber nichts mehr hinzufuegen.

    Die Oberflaeche blendet das Antwortfeld aus - hier wird geprueft, dass es
    auch ohne Oberflaeche nicht geht.
    """
    create_user(admin_client, "lena")
    headers = _als(client, "lena")
    ticket = _anlegen(client, headers).json()

    geschlossen = admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "closed"})
    assert geschlossen.json()["status"] == "closed"
    assert geschlossen.json()["closed_at"] is not None

    abgewiesen = client.post(
        f"/api/tickets/{ticket['id']}/messages", json={"body": "Noch was!"}, headers=headers
    )
    assert abgewiesen.status_code == 409

    # Lesen darf er weiterhin.
    weiterhin = client.get(f"/api/tickets/{ticket['id']}", headers=headers)
    assert weiterhin.status_code == 200
    assert len(weiterhin.json()["messages"]) == 1


def test_der_admin_darf_auch_in_ein_geschlossenes_ticket_schreiben(
    client: TestClient, admin_client: TestClient
) -> None:
    """Ein Nachtrag, ohne es dafuer wieder aufmachen zu muessen."""
    create_user(admin_client, "mia")
    ticket = _anlegen(client, _als(client, "mia")).json()
    admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "closed"})

    danach = admin_client.post(
        f"/api/tickets/{ticket['id']}/messages", json={"body": "Nachtrag."}
    )
    assert danach.status_code == 201
    assert danach.json()["status"] == "closed"


# --- Loeschen ----------------------------------------------------------------


def test_nur_geschlossene_tickets_lassen_sich_loeschen(
    client: TestClient, admin_client: TestClient
) -> None:
    """Ein offenes wegzuraeumen hiesse, jemandem mitten im Gespraech das Wort
    abzuschneiden."""
    create_user(admin_client, "yvonne")
    headers = _als(client, "yvonne")
    offen = _anlegen(client, headers, subject="Bleibt offen").json()
    zu = _anlegen(client, headers, subject="Wird geschlossen").json()
    admin_client.patch(f"/api/tickets/{zu['id']}", json={"status": "closed"})

    antwort = admin_client.post(
        "/api/tickets/delete", json={"ticket_ids": [offen["id"], zu["id"]]}
    )
    assert antwort.status_code == 200
    # Nur eines war geschlossen - das offene bleibt, ohne dass der ganze
    # Vorgang scheitert.
    assert antwort.json() == {"deleted": 1}

    uebrig = admin_client.get("/api/tickets").json()
    assert [t["subject"] for t in uebrig] == ["Bleibt offen"]


def test_nur_der_admin_darf_loeschen(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "zoe")
    create_user(admin_client, "pruefer3", role=Role.approver)
    headers = _als(client, "zoe")
    ticket = _anlegen(client, headers).json()
    admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "closed"})

    for name in ("zoe", "pruefer3"):
        antwort = client.post(
            "/api/tickets/delete",
            json={"ticket_ids": [ticket["id"]]},
            headers=_als(client, name),
        )
        assert antwort.status_code == 403

    with SessionLocal() as session:
        assert session.query(Ticket).count() == 1


def test_loeschen_nimmt_die_nachrichten_mit(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "achim")
    headers = _als(client, "achim")
    ticket = _anlegen(client, headers).json()
    admin_client.post(f"/api/tickets/{ticket['id']}/messages", json={"body": "Antwort."})
    admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "closed"})

    with SessionLocal() as session:
        assert session.query(TicketMessage).count() == 2

    admin_client.post("/api/tickets/delete", json={"ticket_ids": [ticket["id"]]})

    with SessionLocal() as session:
        assert session.query(Ticket).count() == 0
        assert session.query(TicketMessage).count() == 0


def test_mehrere_auf_einmal_loeschen(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "berta")
    headers = _als(client, "berta")
    kennungen = []
    for nummer in range(3):
        ticket = _anlegen(client, headers, subject=f"Anliegen {nummer}").json()
        admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "closed"})
        kennungen.append(ticket["id"])

    antwort = admin_client.post("/api/tickets/delete", json={"ticket_ids": kennungen})
    assert antwort.json() == {"deleted": 3}
    assert admin_client.get("/api/tickets").json() == []


def test_loeschen_raeumt_die_glocke_mit_auf(
    client: TestClient, admin_client: TestClient
) -> None:
    """Sonst bleiben Eintraege stehen, die ins Leere fuehren.

    Der Fremdschluessel auf ``notifications.ticket_id`` steht zwar am Modell,
    fehlt aber in jeder *gewachsenen* Datenbank: nachgeruestete Spalten kommen
    per ``ALTER TABLE ADD COLUMN``, und damit kann SQLite keinen
    Fremdschluessel mehr anlegen. Hier laeuft alles auf frischen Tabellen, wo
    die Weiterleitung greift - deshalb loescht der Dienst ausdruecklich selbst,
    und dieser Test haelt fest, dass er es tut.
    """
    create_user(admin_client, "carla")
    ticket = _anlegen(client, _als(client, "carla")).json()
    admin_client.post(f"/api/tickets/{ticket['id']}/messages", json={"body": "Antwort."})
    admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "closed"})

    with SessionLocal() as session:
        vorher = session.query(Notification).filter(Notification.ticket_id.is_not(None)).count()
        assert vorher > 0

    admin_client.post("/api/tickets/delete", json={"ticket_ids": [ticket["id"]]})

    with SessionLocal() as session:
        assert session.query(Notification).filter(Notification.ticket_id.is_not(None)).count() == 0


def test_die_drei_meldungsarten_sind_auseinanderzuhalten(
    client: TestClient, admin_client: TestClient
) -> None:
    """Neues Ticket, Antwort und Zustandswechsel sagen Verschiedenes.

    Zuerst meldete jede Antwort des Benutzers dem Administrator "Neues Ticket".
    Er konnte damit nicht erkennen, ob ihn ein neues Anliegen erwartet oder nur
    ein Nachtrag zu einem laufenden - und bei drei Meldungen zum selben Ticket
    stand dreimal "neu".
    """
    create_user(admin_client, "elke")
    headers = _als(client, "elke")

    def schluessel(nach: NotificationType) -> list[str]:
        with SessionLocal() as session:
            return [
                m.message_key
                for m in session.query(Notification)
                .filter(Notification.type == nach)
                .order_by(Notification.id)
                .all()
            ]

    ticket = _anlegen(client, headers).json()
    assert schluessel(NotificationType.ticket_new) == ["notifications.ticketNew"]

    # Antwort des Benutzers: ein Nachtrag, kein neues Ticket.
    client.post(f"/api/tickets/{ticket['id']}/messages", json={"body": "Noch da?"}, headers=headers)
    assert schluessel(NotificationType.ticket_new) == ["notifications.ticketNew"], (
        "Eine Antwort darf nicht als neues Ticket gemeldet werden"
    )
    assert schluessel(NotificationType.ticket_reply) == ["notifications.ticketReply"]

    # Antwort des Admins an den Eigentuemer.
    admin_client.post(f"/api/tickets/{ticket['id']}/messages", json={"body": "Gleich."})
    # Zustandswechsel - eigener Text, es wurde ja nichts geschrieben.
    admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "closed"})

    assert schluessel(NotificationType.ticket_reply) == [
        "notifications.ticketReply",
        "notifications.ticketReply",
        "notifications.ticketStatus",
    ]


def test_meldung_traegt_den_betreff_als_titel(
    client: TestClient, admin_client: TestClient
) -> None:
    """Die Glocke setzt ``message_key`` **ohne** Platzhalter ein und zeigt den
    Titel in einer eigenen Zeile - der Betreff muss also dort landen."""
    create_user(admin_client, "dirk")
    _anlegen(client, _als(client, "dirk"), subject="Ganz ohne Bezug")

    with SessionLocal() as session:
        meldung = (
            session.query(Notification)
            .filter(Notification.type == NotificationType.ticket_new)
            .one()
        )
        assert meldung.message_title == "Ganz ohne Bezug"
        # Kein Platzhalter im Schluessel - sonst stuende "{{title}}" in der Glocke.
        assert "{{" not in meldung.message_key


def test_unbekannte_kennungen_stoeren_nicht(admin_client: TestClient) -> None:
    assert admin_client.post("/api/tickets/delete", json={"ticket_ids": [999, 1000]}).json() == {
        "deleted": 0
    }


# --- Benachrichtigungen ------------------------------------------------------


def _meldungen(kind: NotificationType) -> list[Notification]:
    with SessionLocal() as session:
        return list(session.query(Notification).filter(Notification.type == kind).all())


def test_neues_ticket_meldet_sich_bei_jedem_admin(
    client: TestClient, admin_client: TestClient
) -> None:
    create_user(admin_client, "nina")
    zweiter = create_user(admin_client, "admin2", role=Role.admin)

    ticket = _anlegen(client, _als(client, "nina")).json()

    meldungen = _meldungen(NotificationType.ticket_new)
    with SessionLocal() as session:
        erster = session.query(User).filter(User.username == "admin").one()
        empfaenger = {m.user_id for m in meldungen}
        assert empfaenger == {erster.id, zweiter["id"]}
        # Die Glocke muss ins Ticket fuehren koennen, nicht nur in die Liste.
        assert all(m.ticket_id == ticket["id"] for m in meldungen)
        assert all(m.message_title == "Ich komme nicht rein" for m in meldungen)


def test_admin_bekommt_keine_meldung_ueber_sein_eigenes_ticket(
    admin_client: TestClient,
) -> None:
    _anlegen(admin_client)
    assert _meldungen(NotificationType.ticket_new) == []


def test_antwort_des_admins_meldet_sich_beim_eigentuemer(
    client: TestClient, admin_client: TestClient
) -> None:
    create_user(admin_client, "olaf")
    ticket = _anlegen(client, _als(client, "olaf")).json()

    admin_client.post(f"/api/tickets/{ticket['id']}/messages", json={"body": "Schon dabei."})

    with SessionLocal() as session:
        olaf = session.query(User).filter(User.username == "olaf").one()
        meldungen = _meldungen(NotificationType.ticket_reply)
        assert [m.user_id for m in meldungen] == [olaf.id]


def test_schliessen_meldet_sich_beim_eigentuemer(
    client: TestClient, admin_client: TestClient
) -> None:
    """Sonst wartet er weiter auf eine Antwort, die nie kommt."""
    create_user(admin_client, "petra")
    ticket = _anlegen(client, _als(client, "petra")).json()
    admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "closed"})

    assert len(_meldungen(NotificationType.ticket_reply)) == 1


def test_mail_nur_bei_eingeschaltetem_schalter(
    client: TestClient, admin_client: TestClient
) -> None:
    create_user(admin_client, "quentin")

    # Der Standard ist aus - wie bei allen anderen Meldungen auch.
    _anlegen(client, _als(client, "quentin"))
    assert all(not m.mail_pending for m in _meldungen(NotificationType.ticket_new))

    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == "admin").one()
        admin.mail_ticket = True
        session.commit()

    create_user(admin_client, "rosa")
    _anlegen(client, _als(client, "rosa"), subject="Zweites Anliegen")

    neu = [m for m in _meldungen(NotificationType.ticket_new) if m.message_title == "Zweites Anliegen"]
    assert neu and all(m.mail_pending for m in neu)


# --- Titelbezug --------------------------------------------------------------


def test_ticket_kann_auf_einen_titel_zeigen(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "sven")
    ticket = _anlegen(
        client,
        _als(client, "sven"),
        subject="Ton ist asynchron",
        media_type="movie",
        tmdb_id=9800,
        media_title="Philadelphia",
    ).json()

    assert ticket["media_type"] == "movie"
    assert ticket["tmdb_id"] == 9800
    assert ticket["media_title"] == "Philadelphia"


def test_ohne_titelbezug_bleiben_die_felder_leer(
    client: TestClient, admin_client: TestClient
) -> None:
    create_user(admin_client, "tina")
    ticket = _anlegen(client, _als(client, "tina")).json()
    assert ticket["media_type"] is None
    assert ticket["tmdb_id"] is None


# --- Aufraeumen --------------------------------------------------------------


def test_geloeschtes_konto_nimmt_seine_tickets_mit(
    client: TestClient, admin_client: TestClient
) -> None:
    benutzer = create_user(admin_client, "ulf")
    _anlegen(client, _als(client, "ulf"))

    assert admin_client.delete(f"/api/users/{benutzer['id']}").status_code == 204
    with SessionLocal() as session:
        assert session.query(Ticket).count() == 0


def test_zaehler_fuer_den_menuepunkt(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "vera")
    headers = _als(client, "vera")
    ticket = _anlegen(client, headers).json()

    # Beim Admin zaehlt jedes offene Ticket.
    assert admin_client.get("/api/tickets/open-count").json()["count"] == 1
    # Beim Benutzer nur das, worauf jemand anderes geantwortet hat - sonst
    # stuende dauerhaft eine Zahl da, nur weil sein Ticket offen ist.
    assert client.get("/api/tickets/open-count", headers=headers).json()["count"] == 0

    admin_client.post(f"/api/tickets/{ticket['id']}/messages", json={"body": "Antwort."})
    assert client.get("/api/tickets/open-count", headers=headers).json()["count"] == 1


def test_status_muss_ein_bekannter_wert_sein(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "wanda")
    ticket = _anlegen(client, _als(client, "wanda")).json()
    assert admin_client.patch(f"/api/tickets/{ticket['id']}", json={"status": "unfug"}).status_code == 422


def test_leere_nachricht_wird_abgewiesen(client: TestClient, admin_client: TestClient) -> None:
    create_user(admin_client, "xaver")
    headers = _als(client, "xaver")
    ticket = _anlegen(client, headers).json()
    antwort = client.post(
        f"/api/tickets/{ticket['id']}/messages", json={"body": "   "}, headers=headers
    )
    # Pydantic laesst Leerzeichen durch, der Dienst nicht.
    assert antwort.status_code in (422, 400)


def test_status_bleibt_bei_TicketStatus_enum() -> None:
    """Damit ein Umbenennen sofort auffaellt - die Werte stehen in der API."""
    assert [s.value for s in TicketStatus] == ["open", "in_progress", "closed"]
