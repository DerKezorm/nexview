"""Ticketcenter - Anliegen der Benutzer an den Administrator.

Jeder Endpunkt holt sein Ticket ueber ``tickets.sichtbares_ticket`` bzw. seine
Liste ueber ``tickets.sichtbare_tickets``. Dort - und nur dort - steht, wer was
sehen darf.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import MediaType, Ticket, TicketMessage, TicketStatus
from ..services import tickets

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


class MessageOut(BaseModel):
    id: int
    body: str
    created_at: datetime
    edited_at: datetime | None
    # Wer geschrieben hat. NULL, wenn das Konto geloescht wurde - der Verlauf
    # bleibt trotzdem lesbar.
    user_id: int | None
    username: str | None
    display_name: str | None
    avatar_url: str | None
    # Spart der Oberflaeche den Rollenvergleich.
    from_staff: bool


class TicketOut(BaseModel):
    id: int
    subject: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    last_reply_at: datetime | None
    last_reply_by: int | None

    media_type: MediaType | None
    tmdb_id: int | None
    media_title: str | None

    # Wem das Ticket gehoert - fuer die Admin-Uebersicht.
    user_id: int
    username: str
    display_name: str | None
    avatar_url: str | None
    message_count: int
    # Wer das Ticket eroeffnet hat. Bei einem Anschreiben des Administrators
    # ist das *nicht* der Eigentuemer - sonst stuende in der Kopfzeile
    # "Eroeffnet von" der Name des Empfaengers.
    opened_by: int | None
    opened_by_name: str | None


class TicketDetail(TicketOut):
    messages: list[MessageOut]


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    # Nur fuer Administratoren: jemanden anschreiben, statt selbst ein
    # Anliegen zu haben. Das Ticket gehoert dann dem Empfaenger.
    user_id: int | None = Field(default=None, ge=1)
    media_type: MediaType | None = None
    tmdb_id: int | None = Field(default=None, ge=1)
    media_title: str | None = Field(default=None, max_length=300)


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class StatusIn(BaseModel):
    status: TicketStatus


def _fehler(error: tickets.TicketError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


def _nachricht(eintrag: TicketMessage, besitzer_id: int) -> MessageOut:
    verfasser = eintrag.author
    return MessageOut(
        id=eintrag.id,
        body=eintrag.body,
        created_at=eintrag.created_at,
        edited_at=eintrag.edited_at,
        user_id=eintrag.user_id,
        username=verfasser.username if verfasser else None,
        display_name=verfasser.display_name if verfasser else None,
        avatar_url=verfasser.avatar_url if verfasser else None,
        # Alles, was nicht vom Eigentuemer kommt, ist eine Antwort "von drueben".
        from_staff=eintrag.user_id != besitzer_id,
    )


def _uebersicht(ticket: Ticket) -> TicketOut:
    erste = ticket.messages[0] if ticket.messages else None
    return TicketOut(
        id=ticket.id,
        subject=ticket.subject,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        closed_at=ticket.closed_at,
        last_reply_at=ticket.last_reply_at,
        last_reply_by=ticket.last_reply_by,
        media_type=ticket.media_type,
        tmdb_id=ticket.tmdb_id,
        media_title=ticket.media_title,
        user_id=ticket.user_id,
        username=ticket.user.username,
        display_name=ticket.user.display_name,
        avatar_url=ticket.user.avatar_url,
        message_count=len(ticket.messages),
        opened_by=erste.user_id if erste else None,
        opened_by_name=(
            (erste.author.display_name or erste.author.username)
            if erste and erste.author
            else None
        ),
    )


def _detail(ticket: Ticket) -> TicketDetail:
    return TicketDetail(
        **_uebersicht(ticket).model_dump(),
        messages=[_nachricht(m, ticket.user_id) for m in ticket.messages],
    )


@router.get("", response_model=list[TicketOut])
def meine_tickets(
    user: CurrentUser,
    db: DbSession,
    status: Annotated[TicketStatus | None, Query()] = None,
    user_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[TicketOut]:
    """Eigene Tickets - fuer den Administrator alle."""
    gefunden = tickets.sichtbare_tickets(db, user, status=status, user_id=user_id)
    return [_uebersicht(t) for t in gefunden]


@router.get("/open-count")
def offene_anzahl(user: CurrentUser, db: DbSession) -> dict[str, int]:
    """Fuer den Zaehler am Menuepunkt.

    Fuer den Administrator die offenen Tickets aller - fuer alle anderen die
    eigenen, auf die geantwortet wurde.
    """
    offen = tickets.sichtbare_tickets(db, user, status=TicketStatus.open)
    if tickets.darf_alles_sehen(user):
        return {"count": len(offen)}
    # Beim Benutzer zaehlt nur, was auf ihn wartet: zuletzt hat jemand anderes
    # geschrieben. Sonst stuende dauerhaft eine Zahl da, weil sein eigenes
    # Ticket nun einmal offen ist.
    return {"count": sum(1 for t in offen if t.last_reply_by not in (None, user.id))}


@router.post("", response_model=TicketDetail, status_code=201)
def anlegen(payload: TicketCreate, user: CurrentUser, db: DbSession) -> TicketDetail:
    try:
        ticket = tickets.erstellen(
            db,
            user,
            subject=payload.subject,
            body=payload.body,
            media_type=payload.media_type,
            tmdb_id=payload.tmdb_id,
            media_title=payload.media_title,
            fuer_benutzer=payload.user_id,
        )
    except tickets.TicketError as error:
        raise _fehler(error) from error

    db.commit()
    db.refresh(ticket)
    return _detail(ticket)


@router.get("/{ticket_id}", response_model=TicketDetail)
def einzeln(
    ticket_id: Annotated[int, Path(ge=1)], user: CurrentUser, db: DbSession
) -> TicketDetail:
    try:
        return _detail(tickets.sichtbares_ticket(db, user, ticket_id))
    except tickets.TicketError as error:
        raise _fehler(error) from error


@router.post("/{ticket_id}/messages", response_model=TicketDetail, status_code=201)
def antworten(
    ticket_id: Annotated[int, Path(ge=1)],
    payload: MessageIn,
    user: CurrentUser,
    db: DbSession,
) -> TicketDetail:
    try:
        ticket = tickets.sichtbares_ticket(db, user, ticket_id)
        tickets.antworten(db, user, ticket, payload.body)
    except tickets.TicketError as error:
        raise _fehler(error) from error

    db.commit()
    db.refresh(ticket)
    return _detail(ticket)


@router.patch("/messages/{message_id}", response_model=TicketDetail)
def nachricht_aendern(
    message_id: Annotated[int, Path(ge=1)],
    payload: MessageIn,
    user: CurrentUser,
    db: DbSession,
) -> TicketDetail:
    nachricht = db.get(TicketMessage, message_id)
    if nachricht is None:
        raise HTTPException(status_code=404, detail="Diese Nachricht gibt es nicht.")

    try:
        # Erst die Sichtbarkeit des Tickets, dann die Verfasserschaft: wer das
        # Ticket nicht sehen darf, soll nicht einmal erfahren, dass es die
        # Nachricht gibt.
        ticket = tickets.sichtbares_ticket(db, user, nachricht.ticket_id)
        tickets.bearbeiten(db, user, nachricht, payload.body)
    except tickets.TicketError as error:
        raise _fehler(error) from error

    db.commit()
    db.refresh(ticket)
    return _detail(ticket)


class DeleteIn(BaseModel):
    # Als Liste, damit ein Stapel in einem Aufruf weggeht statt in zwanzig.
    ticket_ids: list[int] = Field(min_length=1, max_length=500)


@router.post("/delete", response_model=dict[str, int])
def loeschen(payload: DeleteIn, admin: AdminUser, db: DbSession) -> dict[str, int]:
    """Geschlossene Tickets endgueltig entfernen - nur der Administrator.

    Bewusst ``POST /delete`` und nicht ``DELETE``: ein Rumpf gehoert bei DELETE
    nicht dazu, und ohne Rumpf muesste die Liste in die Adresse - bei 500
    Kennungen wird die zu lang.
    """
    try:
        anzahl = tickets.loeschen(db, admin, payload.ticket_ids)
    except tickets.TicketError as error:
        raise _fehler(error) from error

    db.commit()
    return {"deleted": anzahl}


@router.patch("/{ticket_id}", response_model=TicketDetail)
def status_aendern(
    ticket_id: Annotated[int, Path(ge=1)],
    payload: StatusIn,
    admin: AdminUser,
    db: DbSession,
) -> TicketDetail:
    """Zustand setzen - ausschliesslich fuer Administratoren."""
    try:
        ticket = tickets.sichtbares_ticket(db, admin, ticket_id)
        tickets.status_setzen(db, admin, ticket, payload.status)
    except tickets.TicketError as error:
        raise _fehler(error) from error

    db.commit()
    db.refresh(ticket)
    return _detail(ticket)
