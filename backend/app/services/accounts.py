"""Konten-Mails: Willkommen, Einladung, Bestaetigung, Passwort zuruecksetzen.

Fasst zusammen, was sonst an vier Stellen gleich aussaehe: Link erzeugen,
Nachricht bauen, verschicken - und melden, ob es geklappt hat.

Wichtig: Scheitert der Versand, ist das **kein** Grund, den Vorgang abzubrechen.
Das Konto bzw. die Einladung bleibt bestehen, und der Administrator bekommt den
Link zum Weitergeben. Sonst legt ein kaputter Mailserver die ganze
Benutzerverwaltung lahm.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import AuthToken, TokenPurpose, User
from . import mail, mail_templates, tokens
from .settings_service import AppSettings

logger = logging.getLogger("nexview.accounts")

# Pfade im Frontend, auf denen die Links landen.
PATHS = {
    TokenPurpose.invitation: "/einladung",
    TokenPurpose.email_verification: "/bestaetigen",
    TokenPurpose.password_reset: "/passwort",
}


@dataclass(frozen=True)
class Delivery:
    """Was aus einem Versandversuch geworden ist."""

    sent: bool
    link: str
    # Grund, falls es nicht geklappt hat - fuer die Anzeige beim Administrator.
    error: str | None = None

    @property
    def needs_manual_delivery(self) -> bool:
        return not self.sent


def link_for(settings: AppSettings, purpose: TokenPurpose, raw: str) -> str:
    return settings.link(f"{PATHS[purpose]}/{raw}")


async def _deliver(settings: AppSettings, to: str, nachricht: mail_templates.Mail, link: str) -> Delivery:
    if not settings.mail_configured:
        return Delivery(
            sent=False,
            link=link,
            error="Es ist kein Mailserver eingerichtet - bitte den Link selbst weitergeben.",
        )
    try:
        await mail.send(
            _mail_config(settings), to, nachricht.subject, nachricht.html, nachricht.text
        )
    except mail.MailError as fehler:
        logger.warning("Could not mail %s: %s", to, fehler.message)
        return Delivery(sent=False, link=link, error=fehler.message)
    return Delivery(sent=True, link=link)


def _mail_config(settings: AppSettings) -> mail.MailConfig:
    return mail.MailConfig(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security,
        username=settings.smtp_username,
        password=settings.smtp_password,
        from_address=settings.smtp_from_address,
        from_name=settings.smtp_from_name,
    )



async def send_invitation(
    settings: AppSettings, token: AuthToken, raw: str, sprache: str = "de"
) -> Delivery:
    """Einladung verschicken. Das Konto entsteht erst beim Einloesen."""
    link = link_for(settings, TokenPurpose.invitation, raw)
    nachricht = mail_templates.invitation_mail(link, sprache)
    return await _deliver(settings, token.email, nachricht, link)


async def send_verification(db: Session, settings: AppSettings, user: User) -> Delivery:
    """Adresse bestaetigen lassen."""
    roh, _ = tokens.create(
        db, TokenPurpose.email_verification, user.email or "", user=user
    )
    link = link_for(settings, TokenPurpose.email_verification, roh)
    nachricht = mail_templates.verification_mail(link, user.language)
    return await _deliver(settings, user.email or "", nachricht, link)


async def send_reset(db: Session, settings: AppSettings, user: User) -> Delivery:
    """Passwort zuruecksetzen."""
    roh, _ = tokens.create(db, TokenPurpose.password_reset, user.email or "", user=user)
    link = link_for(settings, TokenPurpose.password_reset, roh)
    nachricht = mail_templates.reset_mail(link, user.language)
    return await _deliver(settings, user.email or "", nachricht, link)


async def notify_address_change(
    settings: AppSettings, alte_adresse: str, neue_adresse: str, sprache: str
) -> None:
    """Die alte Adresse warnen. Scheitert das, ist es kein Beinbruch."""
    if not settings.mail_configured:
        return
    nachricht = mail_templates.address_changed_mail(neue_adresse, sprache)
    try:
        await mail.send(
            _mail_config(settings),
            alte_adresse,
            nachricht.subject,
            nachricht.html,
            nachricht.text,
        )
    except mail.MailError as fehler:
        logger.warning("Could not warn %s about the change: %s", alte_adresse, fehler.message)
