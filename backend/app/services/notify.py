"""Benachrichtigungen anlegen - Glocke immer, Mail nur auf Wunsch.

Alle Stellen, die eine Benachrichtigung erzeugen, gehen durch ``create()``.
Vorher stand an vier Stellen im Code ein eigenes ``Notification(...)``; damit
haette jede neue Meldung wieder von Hand an den Mailversand angeschlossen
werden muessen - und genau das vergisst man.

Der Versand selbst passiert **nicht** hier, sondern spaeter im Hintergrund
(siehe ``mail_outbox``). Hier wird nur vermerkt, dass eine Mail rausgehen soll.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MediaRequest, Notification, NotificationType, Role, Ticket, User

logger = logging.getLogger("nexview.notify")

# Welcher Schalter im Profil steuert welche Meldung?
#
# Bewusst grob gebuendelt: vier Schalter sind ueberschaubar, acht waeren eine
# Zumutung. "Freigegeben" und "abgelehnt" gehoeren fuer den Anfragenden
# ohnehin zusammen - beides ist schlicht "es wurde entschieden".
MAIL_SWITCH: dict[NotificationType, str] = {
    NotificationType.download_complete: "mail_download_complete",
    NotificationType.request_pending: "mail_request_pending",
    NotificationType.approved: "mail_request_decided",
    NotificationType.rejected: "mail_request_decided",
    NotificationType.cancelled: "mail_request_decided",
    NotificationType.feedback: "mail_feedback",
    NotificationType.feedback_poor: "mail_feedback",
    NotificationType.feedback_reply: "mail_feedback",
    # Neues Ticket und Antwort darauf teilen sich einen Schalter - siehe
    # die Begruendung oben.
    NotificationType.ticket_new: "mail_ticket",
    NotificationType.ticket_reply: "mail_ticket",
    NotificationType.user_imported: "mail_user_imported",
}


def wants_mail(user: User, kind: NotificationType) -> bool:
    """Moechte dieser Benutzer zu dieser Meldung eine Mail?

    Ohne bestaetigte Adresse wird grundsaetzlich nichts verschickt: an eine
    unbestaetigte Adresse zu senden hiesse, sie fuer echt zu halten, obwohl
    genau das nie geprueft wurde.
    """
    if not user.email or not user.email_verified or not user.is_active:
        return False
    schalter = MAIL_SWITCH.get(kind)
    return bool(schalter and getattr(user, schalter, False))


def create(
    db: Session,
    *,
    user: User,
    kind: NotificationType,
    message_key: str,
    request: MediaRequest | None = None,
    ticket: Ticket | None = None,
    title: str | None = None,
) -> Notification:
    """Eine Benachrichtigung anlegen. Kein ``commit`` - das macht der Aufrufer.

    ``request`` und ``ticket`` sind zwei moegliche Bezuege; die Glocke
    springt anhand davon an die richtige Stelle. Der ``message_title``
    kommt aus dem, was gesetzt ist - beim Ticket der Betreff.

    ``title`` ist fuer Meldungen ohne solchen Bezug. Ein neues Konto ueber den
    Media-Server haengt an nichts, was einen Titel mitbraechte - der Name muss
    trotzdem in der Glocke stehen, weil die Textbausteine bewusst keine
    Platzhalter enthalten.
    """
    eintrag = Notification(
        user_id=user.id,
        request_id=request.id if request is not None else None,
        ticket_id=ticket.id if ticket is not None else None,
        type=kind,
        message_key=message_key,
        message_title=(
            title
            if title is not None
            else (
                request.title
                if request is not None
                else (ticket.subject if ticket is not None else None)
            )
        ),
        mail_pending=wants_mail(user, kind),
    )
    db.add(eintrag)
    return eintrag


def create_for_approvers(
    db: Session,
    *,
    kind: NotificationType,
    message_key: str,
    request: MediaRequest | None = None,
    ticket: Ticket | None = None,
    ausser: int | None = None,
) -> list[Notification]:
    """Dasselbe fuer alle, die freigeben duerfen (Admins und Entscheider).

    ``ausser`` laesst eine Benutzerkennung aus - wer selbst etwas ausgeloest
    hat, braucht darueber keine Meldung von sich an sich.
    """
    empfaenger = db.scalars(
        select(User).where(User.role.in_((Role.admin, Role.approver)), User.is_active.is_(True))
    )
    return [
        create(db, user=user, kind=kind, message_key=message_key, request=request, ticket=ticket)
        for user in empfaenger
        if user.id != ausser
    ]


def create_for_admins(
    db: Session,
    *,
    kind: NotificationType,
    message_key: str,
    request: MediaRequest | None = None,
    ticket: Ticket | None = None,
    ausser: int | None = None,
    title: str | None = None,
) -> list[Notification]:
    """Nur Administratoren - fuer alles, was auch nur sie beantworten koennen."""
    empfaenger = db.scalars(
        select(User).where(User.role == Role.admin, User.is_active.is_(True))
    )
    return [
        create(
            db,
            user=user,
            kind=kind,
            message_key=message_key,
            request=request,
            ticket=ticket,
            title=title,
        )
        for user in empfaenger
        if user.id != ausser
    ]
