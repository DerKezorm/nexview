"""Ticketcenter: Anliegen der Benutzer an den Administrator.

Die Zugriffsregel steht **einmal** in ``sichtbares_ticket`` bzw.
``sichtbare_tickets``, und jeder Endpunkt geht durch eine der beiden. Verteilte
Sichtbarkeitspruefungen sind der zuverlaessigste Weg, irgendwann eine zu
vergessen - und hier waere das nicht ein falsches Abzeichen, sondern fremde
Post.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    MediaType,
    Notification,
    NotificationType,
    Role,
    Ticket,
    TicketMessage,
    TicketStatus,
    User,
    utcnow,
)
from . import notify


class TicketError(Exception):
    """Etwas ist nicht erlaubt oder nicht moeglich."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def darf_alles_sehen(user: User) -> bool:
    """Nur der Administrator sieht fremde Tickets.

    Ausdruecklich **nicht** der Entscheider: er entscheidet ueber Anfragen,
    liest aber nicht die Post der anderen. Wer das aendern will, aendert es
    hier - und nur hier.
    """
    return user.role == Role.admin


def sichtbare_tickets(
    db: Session,
    user: User,
    *,
    status: TicketStatus | None = None,
    user_id: int | None = None,
) -> list[Ticket]:
    """Die Liste, die dieser Benutzer sehen darf."""
    query = (
        select(Ticket)
        .options(selectinload(Ticket.user))
        # Zuletzt Bewegtes zuerst - was gerade laeuft, steht oben.
        .order_by(Ticket.updated_at.desc())
    )

    if darf_alles_sehen(user):
        if user_id is not None:
            query = query.where(Ticket.user_id == user_id)
    else:
        # Der Filter nach fremden Benutzern wird still ignoriert statt mit
        # einem Fehler quittiert: eine Fehlermeldung waere schon eine Auskunft.
        query = query.where(Ticket.user_id == user.id)

    if status is not None:
        query = query.where(Ticket.status == status)

    return list(db.scalars(query))


def sichtbares_ticket(db: Session, user: User, ticket_id: int) -> Ticket:
    """Ein einzelnes Ticket - oder 404.

    Bewusst 404 und nicht 403: ein "verboten" bestaetigt, dass es diese Nummer
    gibt. Wer fremde Tickets nicht sehen darf, soll auch nicht erfahren,
    wie viele es davon gibt.
    """
    ticket = db.get(
        Ticket, ticket_id, options=[selectinload(Ticket.messages), selectinload(Ticket.user)]
    )
    if ticket is None or (ticket.user_id != user.id and not darf_alles_sehen(user)):
        raise TicketError("Dieses Ticket gibt es nicht.", 404)
    return ticket


def _nachricht_anhaengen(db: Session, ticket: Ticket, user: User, body: str) -> TicketMessage:
    """Die einzige Stelle, an der eine Nachricht entsteht.

    Deshalb steht die Pruefung auf leeren Text hier und nicht im Schema:
    Pydantic sieht bei ``"   "`` drei Zeichen und laesst es durch, im Verlauf
    entstuende eine leere Blase.
    """
    text = body.strip()
    if not text:
        raise TicketError("Die Nachricht darf nicht leer sein.", 422)

    nachricht = TicketMessage(ticket_id=ticket.id, user_id=user.id, body=text)
    db.add(nachricht)

    jetzt = utcnow()
    ticket.updated_at = jetzt
    ticket.last_reply_at = jetzt
    ticket.last_reply_by = user.id
    return nachricht


def erstellen(
    db: Session,
    user: User,
    *,
    subject: str,
    body: str,
    media_type: MediaType | None = None,
    tmdb_id: int | None = None,
    media_title: str | None = None,
    fuer_benutzer: int | None = None,
) -> Ticket:
    """Neues Ticket samt erster Nachricht.

    ``fuer_benutzer`` kehrt die Richtung um: Der **Administrator** schreibt
    jemanden an, statt angeschrieben zu werden. Das Ticket gehoert dann dem
    Empfaenger - er sieht es unter seinen Tickets und kann antworten -, waehrend
    die erste Nachricht den Administrator als Verfasser traegt.

    Genau diese Trennung macht den Rest der Datei unveraendert richtig:
    ``Ticket.user_id`` beantwortet "mit wem", die Nachricht "von wem". Die
    Sichtbarkeit, der Zaehler am Menuepunkt und die Erkennung "kommt von
    drueben" haengen alle an der ersten Frage - und die stimmt weiterhin.
    """
    betreff = subject.strip()
    if not betreff:
        raise TicketError("Das Ticket braucht einen Betreff.", 422)

    empfaenger = user
    if fuer_benutzer is not None and fuer_benutzer != user.id:
        if not darf_alles_sehen(user):
            raise TicketError(
                "Nur ein Administrator darf ein Ticket für jemand anderen eröffnen.", 403
            )
        ziel = db.get(User, fuer_benutzer)
        if ziel is None or not ziel.is_active:
            raise TicketError("Diesen Benutzer gibt es nicht.", 404)
        empfaenger = ziel

    ticket = Ticket(
        user_id=empfaenger.id,
        subject=betreff[:200],
        status=TicketStatus.open,
        media_type=media_type,
        tmdb_id=tmdb_id,
        media_title=(media_title or "").strip()[:300] or None,
    )
    db.add(ticket)
    # Die Nachricht braucht die Ticketkennung.
    db.flush()

    _nachricht_anhaengen(db, ticket, user, body)

    if empfaenger.id == user.id:
        _melde_den_admins(db, ticket, verfasser=user, neu=True)
    else:
        # Umgekehrte Richtung: der Angeschriebene soll es erfahren, nicht die
        # uebrigen Administratoren.
        _melde_dem_eigentuemer(
            db,
            ticket,
            ausser=user.id,
            message_key="notifications.ticketNew",
            kind=NotificationType.ticket_new,
        )
    return ticket


def antworten(db: Session, user: User, ticket: Ticket, body: str) -> TicketMessage:
    """Antwort anhaengen.

    **Ein geschlossenes Ticket ist fuer den Eigentuemer zu.** Er sieht den
    Verlauf weiterhin, kann aber nichts mehr hinzufuegen; wer noch etwas hat,
    eroeffnet ein neues Ticket. Die Oberflaeche blendet das Antwortfeld dazu
    aus - das hier ist die Regel, das Ausblenden nur die Bequemlichkeit.

    Der Administrator darf auch in ein geschlossenes Ticket noch schreiben:
    einen Nachtrag zu hinterlassen, ohne es dafuer wieder aufmachen zu muessen,
    ist genau der Fall, fuer den er es geschlossen hat.
    """
    vom_eigentuemer = ticket.user_id == user.id
    if vom_eigentuemer and ticket.status == TicketStatus.closed:
        raise TicketError(
            "Dieses Ticket ist geschlossen. Für ein neues Anliegen eröffne bitte "
            "ein neues Ticket.",
            409,
        )

    nachricht = _nachricht_anhaengen(db, ticket, user, body)

    if vom_eigentuemer:
        _melde_den_admins(db, ticket, verfasser=user, neu=False)
    else:
        _melde_dem_eigentuemer(
            db, ticket, ausser=user.id, message_key="notifications.ticketReply"
        )

    return nachricht


def bearbeiten(db: Session, user: User, nachricht: TicketMessage, body: str) -> TicketMessage:
    """Eine eigene Nachricht nachbessern.

    Nur der Verfasser, und nur der Text. Geloescht wird nichts - ein Verlauf
    mit Luecken laesst sich nicht mehr lesen. Dass etwas geaendert wurde, steht
    danach in ``edited_at`` und ist fuer beide Seiten sichtbar.
    """
    if nachricht.user_id != user.id:
        raise TicketError("Nur der Verfasser darf seine Nachricht ändern.", 403)

    text = body.strip()
    if not text:
        raise TicketError("Die Nachricht darf nicht leer sein.", 422)

    nachricht.body = text
    nachricht.edited_at = utcnow()
    return nachricht


def status_setzen(db: Session, admin: User, ticket: Ticket, status: TicketStatus) -> Ticket:
    """Zustand aendern - nur der Administrator.

    Die Pruefung steht hier und zusaetzlich am Endpunkt (``AdminUser``): der
    Endpunkt ist die Tuer, das hier die Regel.
    """
    if not darf_alles_sehen(admin):
        raise TicketError("Nur ein Administrator darf den Zustand ändern.", 403)

    if ticket.status == status:
        return ticket

    ticket.status = status
    ticket.updated_at = utcnow()
    ticket.closed_at = utcnow() if status == TicketStatus.closed else None

    # Der Eigentuemer soll erfahren, dass sich etwas getan hat - besonders beim
    # Schliessen, sonst wartet er weiter auf eine Antwort. Eigener Text: es
    # wurde ja nichts geschrieben, nur der Zustand geaendert.
    _melde_dem_eigentuemer(
        db, ticket, ausser=admin.id, message_key="notifications.ticketStatus"
    )
    return ticket


def loeschen(db: Session, admin: User, ticket_ids: list[int]) -> int:
    """Geschlossene Tickets endgueltig entfernen. Gibt zurueck, wie viele.

    Zwei Regeln, und beide stehen hier statt am Endpunkt:

    * **Nur der Administrator.** Ein Ticket zu loeschen heisst, den Verlauf zu
      vernichten - auch den des Benutzers.
    * **Nur geschlossene.** Ein offenes Ticket wegzuraeumen hiesse, jemandem
      mitten im Gespraech das Wort abzuschneiden. Wer es loswerden will,
      schliesst es zuerst - dann ist die Entscheidung eine bewusste und in zwei
      Schritten getroffen.

    Nicht gefundene oder noch offene Kennungen werden still uebergangen statt
    den ganzen Vorgang abzubrechen: beim Loeschen mehrerer soll nicht eine
    einzige unpassende dafuer sorgen, dass gar nichts passiert.

    Die Nachrichten verschwinden mit - dafuer sorgt ``ON DELETE CASCADE``.
    """
    if not darf_alles_sehen(admin):
        raise TicketError("Nur ein Administrator darf Tickets löschen.", 403)
    if not ticket_ids:
        return 0

    betroffen = list(
        db.scalars(
            select(Ticket).where(
                Ticket.id.in_(ticket_ids), Ticket.status == TicketStatus.closed
            )
        )
    )
    if not betroffen:
        return 0

    # Die Meldungen in der Glocke ausdruecklich mitloeschen, statt sich auf
    # ``ON DELETE CASCADE`` zu verlassen.
    #
    # Der Fremdschluessel steht zwar am Modell, existiert in einer *gewachsenen*
    # Datenbank aber nicht: ``_add_missing_columns`` ruestet Spalten per
    # ``ALTER TABLE ADD COLUMN`` nach, und SQLite kann einer bestehenden Tabelle
    # damit keinen Fremdschluessel mehr geben. Bei einer frisch angelegten
    # Datenbank greift die Weiterleitung, bei jeder aktualisierten nicht - der
    # Fehler waere also genau dort aufgetreten, wo kein Test hinsieht. Nach dem
    # Loeschen blieben Glockeneintraege stehen, die ins Leere fuehren.
    kennungen = [ticket.id for ticket in betroffen]
    db.execute(delete(Notification).where(Notification.ticket_id.in_(kennungen)))

    for ticket in betroffen:
        db.delete(ticket)
    return len(betroffen)


# --- Benachrichtigungen ------------------------------------------------------


def _melde_den_admins(db: Session, ticket: Ticket, *, verfasser: User, neu: bool) -> None:
    """Alle Administratoren informieren - ausser dem Verfasser selbst.

    ``neu`` unterscheidet das eroeffnete Ticket von einer Antwort darauf. Ohne
    diese Unterscheidung stand in der Glocke bei jeder Antwort "Neues Ticket",
    obwohl es laengst lief - der Administrator konnte an der Meldung nicht
    erkennen, ob ihn ein neues Anliegen erwartet oder ein Nachtrag.
    """
    notify.create_for_admins(
        db,
        kind=NotificationType.ticket_new if neu else NotificationType.ticket_reply,
        message_key="notifications.ticketNew" if neu else "notifications.ticketReply",
        ticket=ticket,
        ausser=verfasser.id,
    )


def _melde_dem_eigentuemer(
    db: Session,
    ticket: Ticket,
    *,
    ausser: int | None,
    message_key: str,
    kind: NotificationType = NotificationType.ticket_reply,
) -> None:
    """Den Eigentuemer ueber eine Bewegung an seinem Ticket informieren.

    ``message_key`` unterscheidet die Antwort vom blossen Zustandswechsel:
    "Ticket aktualisiert" zu lesen, wenn jemand geschrieben hat, waere ebenso
    irrefuehrend wie umgekehrt.
    """
    eigentuemer = db.get(User, ticket.user_id)
    # Wer sein eigenes Ticket bearbeitet - der Admin sein eigenes etwa -
    # braucht keine Meldung von sich an sich.
    if eigentuemer is None or eigentuemer.id == ausser:
        return
    notify.create(
        db,
        user=eigentuemer,
        kind=kind,
        message_key=message_key,
        ticket=ticket,
    )
