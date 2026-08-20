"""E-Mail-Benachrichtigungen: wer bekommt was - und wer nicht.

Der wichtigste Punkt hier ist der Standardfall: **niemand** bekommt Mails,
solange er sie nicht selbst eingeschaltet hat. Ungefragt zu verschicken waere
der sicherste Weg, sich die Anwendung zu verleiden - und bei einer selbst
gehosteten Anwendung mit der eigenen Mailadresse als Absender obendrein
unangenehm.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    Notification,
    NotificationType,
    RequestStatus,
    Ticket,
    User,
)
from app.services import mail_outbox, notify
from app.services.settings_service import load_settings
from tests.conftest import auth_headers, create_user

ALLE_SCHALTER = (
    "mail_download_complete",
    "mail_request_pending",
    "mail_request_decided",
    "mail_feedback",
)


@pytest.fixture
def mailserver(admin_client: TestClient) -> None:
    """Einen Mailserver eintragen, damit der Postausgang ueberhaupt arbeitet."""
    admin_client.put(
        "/api/settings",
        json={
            "smtp_host": "mail.beispiel.de",
            "smtp_port": 587,
            "smtp_from_address": "nexview@beispiel.de",
            "public_url": "https://nexview.beispiel.de",
        },
    )


class Postfach:
    """Faengt ab, was verschickt worden waere."""

    def __init__(self) -> None:
        self.nachrichten: list[dict[str, str]] = []

    async def send(self, _config, to, subject, html, text):  # noqa: ANN001
        self.nachrichten.append({"to": to, "subject": subject, "html": html, "text": text})

    @property
    def empfaenger(self) -> list[str]:
        return [n["to"] for n in self.nachrichten]


@pytest.fixture
def postfach(monkeypatch: pytest.MonkeyPatch) -> Postfach:
    kasten = Postfach()
    monkeypatch.setattr(mail_outbox.mail, "send", kasten.send)
    return kasten


def _abarbeiten() -> int:
    with SessionLocal() as db:
        return asyncio.run(mail_outbox.process(db, load_settings(db)))


def _anfrage(db, benutzer: User, titel: str = "Testtitel") -> MediaRequest:
    request = MediaRequest(
        user_id=benutzer.id,
        media_type=MediaType.movie,
        tmdb_id=4242,
        title=titel,
        status=RequestStatus.pending_approval,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


# --- Standardverhalten -------------------------------------------------------


def test_standard_ist_alles_aus(admin_client: TestClient) -> None:
    daten = create_user(admin_client, "lena")
    for schalter in ALLE_SCHALTER:
        assert daten[schalter] is False, schalter


def test_ohne_zustimmung_keine_mail(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    """Der wichtigste Test: nichts eingeschaltet, also nichts verschickt."""
    create_user(admin_client, "lena")

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena)
        notify.create(
            db,
            user=lena,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()
        # Die Glocke bekommt den Eintrag trotzdem.
        assert db.query(Notification).count() == 1
        assert db.query(Notification).one().mail_pending is False

    assert _abarbeiten() == 0
    assert postfach.nachrichten == []


def test_eingeschaltet_wird_verschickt(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    create_user(admin_client, "lena")
    kopf = auth_headers(admin_client, "lena", "passwort-1234")
    admin_client.patch("/api/auth/me", json={"mail_download_complete": True}, headers=kopf)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena, "Dune: Teil Drei")
        notify.create(
            db,
            user=lena,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()

    assert _abarbeiten() == 1
    assert postfach.empfaenger == ["lena@beispiel.de"]
    assert "Dune: Teil Drei" in postfach.nachrichten[0]["subject"]
    # Der Hinweis, wo man das wieder abstellt, muss drin sein.
    assert "/profil" in postfach.nachrichten[0]["text"]


def test_ein_schalter_zieht_nicht_die_anderen_mit(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    """Wer nur "fertig geladen" will, darf nicht auch Freigabe-Mails bekommen."""
    create_user(admin_client, "lena")
    kopf = auth_headers(admin_client, "lena", "passwort-1234")
    admin_client.patch("/api/auth/me", json={"mail_download_complete": True}, headers=kopf)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena)
        notify.create(
            db,
            user=lena,
            kind=NotificationType.approved,
            message_key="notifications.approved",
            request=request,
        )
        db.commit()

    assert _abarbeiten() == 0
    assert postfach.nachrichten == []


# --- Wer darf ueberhaupt Post bekommen? --------------------------------------


def test_unbestaetigte_adresse_bekommt_nichts(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    """An eine ungeprüfte Adresse zu senden hiesse, sie fuer echt zu halten."""
    create_user(admin_client, "lena", email_verified=False, mail_download_complete=True)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena)
        notify.create(
            db,
            user=lena,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()
        assert db.query(Notification).one().mail_pending is False

    assert _abarbeiten() == 0
    assert postfach.nachrichten == []


def test_abgeschaltetes_konto_bekommt_nichts(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    create_user(admin_client, "lena", is_active=False, mail_download_complete=True)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena)
        notify.create(
            db,
            user=lena,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()

    assert _abarbeiten() == 0


def test_ohne_mailserver_wird_nichts_versucht(
    admin_client: TestClient, postfach: Postfach
) -> None:
    """Ohne eingetragenen Mailserver bleibt der Auftrag einfach liegen."""
    create_user(admin_client, "lena", mail_download_complete=True)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena)
        notify.create(
            db,
            user=lena,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()

    assert _abarbeiten() == 0
    assert postfach.nachrichten == []


# --- Wer bekommt welche Meldung? ---------------------------------------------


def test_wartende_anfrage_geht_an_entscheider_nicht_an_nutzer(
    arr_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    """"Wartet auf Freigabe" ist nichts, was einen normalen Nutzer angeht."""
    create_user(arr_client, "lena", mail_request_pending=True)
    create_user(arr_client, "eva", role="approver", mail_request_pending=True)

    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "admin").one()
        admin.mail_request_pending = True
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena, "Severance")
        notify.create_for_approvers(
            db,
            kind=NotificationType.request_pending,
            message_key="notifications.requestPending",
            request=request,
            ausser=lena.id,
        )
        db.commit()

    _abarbeiten()
    assert sorted(postfach.empfaenger) == ["admin@beispiel.de", "eva@beispiel.de"]
    assert "lena@beispiel.de" not in postfach.empfaenger


def test_entscheidung_geht_an_den_anfragenden(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    create_user(admin_client, "lena", mail_request_decided=True)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena, "Alien: Earth")
        notify.create(
            db,
            user=lena,
            kind=NotificationType.rejected,
            message_key="notifications.rejected",
            request=request,
        )
        db.commit()

    _abarbeiten()
    assert postfach.empfaenger == ["lena@beispiel.de"]
    assert "Alien: Earth" in postfach.nachrichten[0]["subject"]


def test_sprache_richtet_sich_nach_dem_empfaenger(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    create_user(admin_client, "lena", language="en", mail_download_complete=True)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena, "Dune")
        notify.create(
            db,
            user=lena,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()

    _abarbeiten()
    assert postfach.nachrichten[0]["subject"].startswith("Ready to watch")


# --- Verhalten im Fehlerfall -------------------------------------------------


def test_ein_fehlschlag_stoppt_die_anderen_nicht(
    admin_client: TestClient, mailserver: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein kaputter Empfaenger darf die Warteschlange nicht blockieren."""
    create_user(admin_client, "lena", mail_download_complete=True)
    create_user(admin_client, "tom", mail_download_complete=True)

    zugestellt: list[str] = []

    async def waehlerisch(_config, to, *_args):  # noqa: ANN001
        if to == "lena@beispiel.de":
            raise OSError("Empfänger unbekannt")
        zugestellt.append(to)

    monkeypatch.setattr(mail_outbox.mail, "send", waehlerisch)

    with SessionLocal() as db:
        for name in ("lena", "tom"):
            benutzer = db.query(User).filter(User.username == name).one()
            request = _anfrage(db, benutzer)
            notify.create(
                db,
                user=benutzer,
                kind=NotificationType.download_complete,
                message_key="notifications.downloadComplete",
                request=request,
            )
        db.commit()

    assert _abarbeiten() == 1
    assert zugestellt == ["tom@beispiel.de"]


def test_nach_drei_versuchen_wird_aufgegeben(
    admin_client: TestClient, mailserver: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst belegt eine unzustellbare Adresse den Postausgang fuer immer."""
    create_user(admin_client, "lena", mail_download_complete=True)

    versuche = 0

    async def scheitert(*_args):  # noqa: ANN001
        nonlocal versuche
        versuche += 1
        raise OSError("dauerhaft kaputt")

    monkeypatch.setattr(mail_outbox.mail, "send", scheitert)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena)
        notify.create(
            db,
            user=lena,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()

    for _ in range(5):
        _abarbeiten()

    assert versuche == mail_outbox.MAX_ATTEMPTS
    with SessionLocal() as db:
        assert db.query(Notification).one().mail_pending is False


def test_verschickte_mail_geht_nicht_zweimal_raus(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    create_user(admin_client, "lena", mail_download_complete=True)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        request = _anfrage(db, lena)
        notify.create(
            db,
            user=lena,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()

    _abarbeiten()
    _abarbeiten()
    _abarbeiten()
    assert len(postfach.nachrichten) == 1

    with SessionLocal() as db:
        eintrag = db.query(Notification).one()
        assert eintrag.mail_pending is False
        assert eintrag.mail_sent_at is not None


# --- Profil ------------------------------------------------------------------


def test_schalter_lassen_sich_umlegen(admin_client: TestClient) -> None:
    create_user(admin_client, "lena")
    kopf = auth_headers(admin_client, "lena", "passwort-1234")

    antwort = admin_client.patch(
        "/api/auth/me",
        json={"mail_download_complete": True, "mail_request_decided": True},
        headers=kopf,
    )
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["mail_download_complete"] is True
    assert daten["mail_request_decided"] is True
    assert daten["mail_feedback"] is False

    # Und wieder aus - ein Wahrheitswert muss in beide Richtungen ankommen.
    aus = admin_client.patch(
        "/api/auth/me", json={"mail_download_complete": False}, headers=kopf
    ).json()
    assert aus["mail_download_complete"] is False
    assert aus["mail_request_decided"] is True


def test_niemand_stellt_fremde_schalter_um(admin_client: TestClient) -> None:
    """Die Schalter haengen am eigenen Konto - der Admin fasst sie nicht an."""
    create_user(admin_client, "lena")
    antwort = admin_client.patch(
        f"/api/users/{create_user(admin_client, 'tom')['id']}",
        json={"mail_download_complete": True},
    )
    # Das Feld gibt es in der Benutzerverwaltung gar nicht - es wird ignoriert.
    assert antwort.status_code == 200
    assert antwort.json()["mail_download_complete"] is False


# --- Tickets und neue Konten -------------------------------------------------
#
# Diese Tests verfolgen den Versand bis zur fertigen Mail. Genau das fehlte
# lange: Schalter und Vermerk existierten, aber der Postausgang kannte fuer
# diese Meldungsarten keine Vorlage und hakte die Auftraege still ab. Ein
# Haken ohne Vorlage darf nie wieder stumm bleiben.


def _ticket(db, benutzer: User, subject: str = "Anmeldung klemmt") -> Ticket:
    ticket = Ticket(user_id=benutzer.id, subject=subject)
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def test_neues_ticket_kommt_als_mail(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    create_user(admin_client, "lena", mail_ticket=True)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        ticket = _ticket(db, lena)
        notify.create(
            db,
            user=lena,
            kind=NotificationType.ticket_new,
            message_key="notifications.ticketNew",
            ticket=ticket,
        )
        db.commit()
        assert db.query(Notification).one().mail_pending is True
        kennung = ticket.id

    assert _abarbeiten() == 1
    assert postfach.empfaenger == ["lena@beispiel.de"]
    assert "Anmeldung klemmt" in postfach.nachrichten[0]["subject"]
    # Der Link muss in den Verlauf fuehren, nicht bloss auf die Liste.
    assert f"/tickets/{kennung}" in postfach.nachrichten[0]["text"]


def test_ticket_antwort_kommt_als_mail(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    create_user(admin_client, "lena", mail_ticket=True)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        ticket = _ticket(db, lena)
        notify.create(
            db,
            user=lena,
            kind=NotificationType.ticket_reply,
            message_key="notifications.ticketReply",
            ticket=ticket,
        )
        db.commit()

    assert _abarbeiten() == 1
    assert "Anmeldung klemmt" in postfach.nachrichten[0]["subject"]
    # Antwort und neues Ticket muessen unterscheidbar sein.
    assert postfach.nachrichten[0]["subject"] != "Neues Ticket: Anmeldung klemmt"


def test_neues_konto_kommt_als_mail(
    admin_client: TestClient, mailserver: None, postfach: Postfach
) -> None:
    create_user(admin_client, "lena", mail_user_imported=True)

    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        notify.create(
            db,
            user=lena,
            kind=NotificationType.user_imported,
            message_key="notifications.userImported",
            title="DilaraUygunMrozek",
        )
        db.commit()

    assert _abarbeiten() == 1
    assert "DilaraUygunMrozek" in postfach.nachrichten[0]["subject"]
    assert "/admin/settings" in postfach.nachrichten[0]["text"]
