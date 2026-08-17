"""Verschickt die Mails, die zu Benachrichtigungen gehoeren.

Warum ueberhaupt getrennt vom Anlegen? Ein Mailserver kann Sekunden brauchen
oder gar nicht antworten. Haenge der Versand an der Freigabe-Schaltflaeche,
wuerde ein stiller Mailserver den Klick blockieren - und ein Neustart mitten
im Versand die Nachricht verlieren. So steht der Auftrag in der Datenbank und
wird abgearbeitet, wenn es passt.

Der Ablauf laeuft in derselben Hintergrundschleife wie die Status-Abfrage.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    MediaRequest,
    Notification,
    NotificationType,
    User,
    utcnow,
)
from . import mail, mail_templates
from .settings_service import AppSettings

logger = logging.getLogger("nexview.mail")

# Wie viele Nachrichten pro Durchgang. Verhindert, dass eine lange Warteschlange
# den Mailserver auf einen Schlag flutet.
BATCH = 20

# Nach so vielen erfolglosen Anlaeufen wird aufgegeben. Ohne Grenze wuerde eine
# dauerhaft unzustellbare Adresse den Postausgang bis in alle Ewigkeit belegen.
MAX_ATTEMPTS = 3


def _link(settings: AppSettings, pfad: str) -> str | None:
    """Vollstaendiger Link - oder ``None``, wenn keine oeffentliche Adresse gesetzt ist.

    Ohne Adresse verschickt Nexview die Mail trotzdem, nur eben ohne Knopf.
    Ein Link auf ``localhost`` waere fuer den Empfaenger wertlos.
    """
    return settings.link(pfad) if settings.public_url else None


def _nachricht(
    eintrag: Notification,
    empfaenger: User,
    request: MediaRequest | None,
    anfragender: User | None,
    settings: AppSettings,
) -> mail_templates.Mail | None:
    """Passende Vorlage zur Benachrichtigung waehlen."""
    sprache = empfaenger.language
    titel = eintrag.message_title or (request.title if request else "")
    profil = _link(settings, "/profil")

    match eintrag.type:
        case NotificationType.download_complete:
            return mail_templates.download_ready_mail(
                titel, sprache, link=_link(settings, "/requests"), profil_link=profil
            )

        case NotificationType.request_pending:
            name = "?"
            if anfragender is not None:
                name = anfragender.display_name or anfragender.username
            return mail_templates.request_pending_mail(
                titel, name, sprache, link=_link(settings, "/admin/requests"), profil_link=profil
            )

        case NotificationType.approved:
            return mail_templates.request_decided_mail(
                titel, True, sprache, link=_link(settings, "/requests"), profil_link=profil
            )

        case NotificationType.rejected | NotificationType.cancelled:
            return mail_templates.request_decided_mail(
                titel, False, sprache, link=_link(settings, "/requests"), profil_link=profil
            )

        case NotificationType.feedback | NotificationType.feedback_poor:
            return mail_templates.feedback_mail(
                titel,
                request.rating if request and request.rating is not None else 0,
                request.feedback if request else None,
                sprache,
                link=_link(settings, "/admin/requests"),
                profil_link=profil,
            )

        case NotificationType.feedback_reply:
            return mail_templates.feedback_reply_mail(
                titel, sprache, link=_link(settings, "/requests"), profil_link=profil
            )

    return None


async def process(db: Session, settings: AppSettings) -> int:
    """Offene Mails verschicken. Liefert die Anzahl der zugestellten Nachrichten."""
    if not settings.mail_configured:
        return 0

    offen = list(
        db.scalars(
            select(Notification)
            .where(Notification.mail_pending.is_(True))
            .order_by(Notification.created_at)
            .limit(BATCH)
        )
    )
    if not offen:
        return 0

    config = mail.MailConfig(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security,
        username=settings.smtp_username,
        password=settings.smtp_password,
        from_address=settings.smtp_from_address,
        from_name=settings.smtp_from_name,
    )

    zugestellt = 0
    for eintrag in offen:
        empfaenger = db.get(User, eintrag.user_id)
        request = db.get(MediaRequest, eintrag.request_id) if eintrag.request_id else None
        anfragender = db.get(User, request.user_id) if request is not None else None

        # Zwischenzeitlich abgeschaltet, geloescht oder Adresse entfernt?
        # Dann ist der Auftrag hinfaellig - stillschweigend abhaken.
        if empfaenger is None or not empfaenger.email or not empfaenger.email_verified:
            eintrag.mail_pending = False
            continue

        nachricht = _nachricht(eintrag, empfaenger, request, anfragender, settings)
        if nachricht is None:
            eintrag.mail_pending = False
            continue

        eintrag.mail_attempts += 1
        try:
            await mail.send(
                config,
                empfaenger.email,
                nachricht.subject,
                nachricht.html,
                nachricht.text,
            )
        except Exception as fehler:  # noqa: BLE001 - kein Versand darf die Schleife stoppen
            if eintrag.mail_attempts >= MAX_ATTEMPTS:
                eintrag.mail_pending = False
                logger.warning(
                    "Benachrichtigung an %s endgültig nicht zustellbar (%s): %s",
                    empfaenger.email,
                    eintrag.type.value,
                    fehler,
                )
            else:
                logger.info(
                    "Benachrichtigung an %s fehlgeschlagen, Versuch %d von %d: %s",
                    empfaenger.email,
                    eintrag.mail_attempts,
                    MAX_ATTEMPTS,
                    fehler,
                )
            continue

        eintrag.mail_pending = False
        eintrag.mail_sent_at = utcnow()
        zugestellt += 1

    db.commit()
    if zugestellt:
        logger.info("%d Benachrichtigung(en) per Mail verschickt", zugestellt)
    return zugestellt
