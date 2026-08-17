"""Freigeben, Ablehnen und die Uebersicht ueber alle Anfragen - nur fuer Admins."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..deps import AdminUser, ApproverUser, DbSession
from ..models import (
    MediaRequest,
    Notification,
    NotificationType,
    RequestStatus,
    Role,
    User,
    utcnow,
)
from ..schemas_requests import FeedbackReply, RequestWithUser
from ..services import blocklist, notify, requests_service
from ..services.settings_service import load_settings
from ..services.tmdb import image_url

router = APIRouter(prefix="/api/admin/requests", tags=["admin"])

logger = logging.getLogger("nexview.admin")


class RejectPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    # Den Titel gleich mit auf die Sperrliste setzen. Darf nur ein
    # Administrator - siehe die Pruefung in ``reject``.
    block: bool = False


def _with_user(request: MediaRequest) -> RequestWithUser:
    vom_benutzer = ("username", "display_name", "avatar_url")
    return RequestWithUser(
        **{
            field: getattr(request, field)
            for field in RequestWithUser.model_fields
            if field not in vom_benutzer
        },
        username=request.user.username,
        display_name=request.user.display_name,
        avatar_url=request.user.avatar_url,
    )


def _get_or_404(db: Session, request_id: int) -> MediaRequest:
    request = db.get(MediaRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden.")
    return request


def _notify_requester(
    db: Session, request: MediaRequest, kind: NotificationType, message_key: str
) -> None:
    """Den Anfragenden ueber die Entscheidung informieren.

    Wer ueber die eigene Anfrage entscheidet - Admins und Entscheider tun das
    dauernd, ihre Anfragen laufen automatisch durch - bekommt keine Meldung
    ueber sich selbst.
    """
    anfragender = db.get(User, request.user_id)
    if anfragender is None:
        return
    notify.create(
        db, user=anfragender, kind=kind, message_key=message_key, request=request
    )


@router.get("", response_model=list[RequestWithUser])
def list_all(
    entscheider: ApproverUser,
    db: DbSession,
    request_status: Annotated[
        Literal[
            "pending_approval",
            "approved",
            "searching",
            "downloaded",
            "rejected",
            "failed",
            "cancelled",
        ]
        | None,
        Query(alias="status"),
    ] = None,
    user_id: Annotated[int | None, Query(ge=1)] = None,
    feedback: Annotated[bool, Query()] = False,
) -> list[RequestWithUser]:
    """Alle Anfragen aller Benutzer, optional gefiltert."""
    query = (
        select(MediaRequest)
        .options(selectinload(MediaRequest.user))
        .order_by(MediaRequest.requested_at.desc())
    )
    if feedback:
        # Jede Bewertung ohne Antwort - auch die ohne Text. Wer Sterne vergibt,
        # sagt damit etwas, und das soll der Administrator sehen, ohne die
        # ganze Liste durchzugehen.
        query = (
            query.where(
                MediaRequest.rating.is_not(None),
                MediaRequest.feedback_reply.is_(None),
            )
            .order_by(None)
            .order_by(MediaRequest.rated_at.desc())
        )
    elif request_status is not None:
        query = query.where(MediaRequest.status == RequestStatus(request_status))
    if user_id is not None:
        query = query.where(MediaRequest.user_id == user_id)

    return [_with_user(row) for row in db.scalars(query)]


@router.post("/{request_id}/approve", response_model=RequestWithUser)
async def approve(
    request_id: Annotated[int, Path(ge=1)], entscheider: ApproverUser, db: DbSession
) -> RequestWithUser:
    request = _get_or_404(db, request_id)
    if request.status != RequestStatus.pending_approval:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese Anfrage wartet nicht (mehr) auf eine Freigabe.",
        )

    request.status = RequestStatus.approved
    request.approved_by = entscheider.id
    request.approved_at = utcnow()
    requests_service.clear_pending_notice(db, request)
    db.commit()

    try:
        await requests_service.push_to_arr(db, load_settings(db), request)
    except requests_service.RequestError as error:
        # Der Zustand steht jetzt auf "fehlgeschlagen" - der Admin sieht warum.
        db.refresh(request)
        raise HTTPException(status_code=error.status_code, detail=error.message) from error

    _notify_requester(db, request, NotificationType.approved, "notifications.approved")
    db.commit()
    db.refresh(request)
    return _with_user(request)


@router.post("/{request_id}/reject", response_model=RequestWithUser)
def reject(
    request_id: Annotated[int, Path(ge=1)],
    payload: RejectPayload,
    entscheider: ApproverUser,
    db: DbSession,
) -> RequestWithUser:
    request = _get_or_404(db, request_id)
    if request.status != RequestStatus.pending_approval:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese Anfrage wartet nicht (mehr) auf eine Freigabe.",
        )

    # Sperren ist eine Grundsatzentscheidung fuer die ganze Bibliothek und
    # steht deshalb nur dem Administrator zu - ein Entscheider lehnt die
    # einzelne Anfrage ab, mehr nicht. Deutlich abweisen statt still zu
    # ignorieren: sonst klickt jemand den Haken und glaubt, es sei passiert.
    if payload.block and entscheider.role != Role.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur ein Administrator darf Titel auf die Sperrliste setzen.",
        )

    request.status = RequestStatus.rejected
    request.approved_by = entscheider.id
    request.approved_at = utcnow()
    request.rejection_reason = (payload.reason or "").strip() or None

    if payload.block:
        blocklist.sperren(
            db,
            media_type=request.media_type,
            tmdb_id=request.tmdb_id,
            title=request.title,
            # ``poster_path`` heisst so, enthaelt aber laengst die fertige
            # Adresse (siehe models.MediaRequest). Ein ``image_url`` darum
            # herum setzt das Praefix ein zweites Mal davor.
            poster_url=request.poster_path,
            # Die Begruendung der Ablehnung ist zugleich die der Sperre - man
            # sagt denselben Satz nicht zweimal.
            reason=request.rejection_reason,
            admin=entscheider,
        )

    requests_service.clear_pending_notice(db, request)
    _notify_requester(db, request, NotificationType.rejected, "notifications.rejected")
    db.commit()
    db.refresh(request)
    return _with_user(request)


@router.post("/{request_id}/reply", response_model=RequestWithUser)
def reply_to_feedback(
    request_id: Annotated[int, Path(ge=1)],
    payload: FeedbackReply,
    admin: AdminUser,
    db: DbSession,
) -> RequestWithUser:
    """Auf die Rueckmeldung des Anfragenden antworten.

    Das darf ausdruecklich nur der Administrator - Entscheider entscheiden
    ueber Anfragen, aber sie sprechen nicht fuer den Betreiber. Der Anfragende
    bekommt dazu eine Benachrichtigung.
    """
    request = _get_or_404(db, request_id)
    if request.rating is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Zu dieser Anfrage gibt es noch keine Rückmeldung.",
        )

    request.feedback_reply = payload.reply.strip()
    request.replied_at = utcnow()
    request.replied_by = admin.id

    _notify_requester(db, request, NotificationType.feedback_reply, "notifications.feedbackReply")
    db.commit()
    db.refresh(request)

    logger.info("%r replied to the feedback on %r", admin.username, request.title)
    return _with_user(request)


@router.post("/approve-all/{user_id}", response_model=list[RequestWithUser])
async def approve_all(
    user_id: Annotated[int, Path(ge=1)], entscheider: ApproverUser, db: DbSession
) -> list[RequestWithUser]:
    """Alle offenen Anfragen eines Benutzers auf einmal freigeben.

    Schlaegt eine Uebergabe an Radarr/Sonarr fehl, laufen die uebrigen
    trotzdem weiter - die fehlgeschlagene steht danach auf "fehlgeschlagen"
    und ist in der Liste sichtbar.
    """
    offen = list(
        db.scalars(
            select(MediaRequest)
            .options(selectinload(MediaRequest.user))
            .where(
                MediaRequest.user_id == user_id,
                MediaRequest.status == RequestStatus.pending_approval,
            )
            .order_by(MediaRequest.requested_at)
        )
    )
    if not offen:
        raise HTTPException(status_code=404, detail="Keine offenen Anfragen für diesen Benutzer.")

    settings = load_settings(db)
    for request in offen:
        request.status = RequestStatus.approved
        request.approved_by = entscheider.id
        request.approved_at = utcnow()
        requests_service.clear_pending_notice(db, request)
        db.commit()

        try:
            await requests_service.push_to_arr(db, settings, request)
        except requests_service.RequestError:
            db.refresh(request)
            continue

        _notify_requester(db, request, NotificationType.approved, "notifications.approved")
        db.commit()

    for request in offen:
        db.refresh(request)
    return [_with_user(request) for request in offen]


@router.post("/{request_id}/cancel", response_model=RequestWithUser)
async def cancel_request(
    request_id: Annotated[int, Path(ge=1)], entscheider: ApproverUser, db: DbSession
) -> RequestWithUser:
    """Laufende Anfrage abbrechen und in Radarr/Sonarr loeschen."""
    request = _get_or_404(db, request_id)
    try:
        await requests_service.cancel(db, load_settings(db), request)
    except requests_service.RequestError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error

    _notify_requester(db, request, NotificationType.cancelled, "notifications.cancelled")
    db.commit()
    db.refresh(request)
    return _with_user(request)


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_request(
    request_id: Annotated[int, Path(ge=1)], entscheider: ApproverUser, db: DbSession
) -> None:
    """Eintrag aus der Uebersicht entfernen.

    Loescht nur den Eintrag in Nexview - in Radarr/Sonarr bleibt der Titel.
    """
    db.delete(_get_or_404(db, request_id))
    db.commit()


@router.get("/pending/count")
def pending_count(entscheider: ApproverUser, db: DbSession) -> dict[str, int]:
    """Kleine Zahl fuer den Menuepunkt - wie viele warten auf Freigabe?"""
    waiting = (
        db.scalar(
            select(func.count(MediaRequest.id)).where(
                MediaRequest.status == RequestStatus.pending_approval
            )
        )
        or 0
    )
    return {"pending": waiting}
