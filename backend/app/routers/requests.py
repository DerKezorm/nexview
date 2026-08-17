"""Anfragen stellen und die eigenen einsehen."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select

from ..deps import CurrentUser, DbSession
from ..models import (
    MediaRequest,
    Notification,
    NotificationType,
    RequestStatus,
    Role,
    User,
    utcnow,
)
from ..schemas_requests import (
    FeedbackCreate,
    QuotaInfo,
    QuotaOverview,
    RequestCreate,
    RequestPublic,
)
from ..services import media, quota, requests_service
from ..services.quota import QuotaState
from ..services.settings_service import load_settings
from ..services.tmdb import TmdbError

router = APIRouter(prefix="/api/requests", tags=["requests"])

logger = logging.getLogger("nexview.requests")

# Ab dieser Bewertung (und darunter) gilt eine Rueckmeldung als Beschwerde.
POOR_RATING = 2


def _quota_info(state: QuotaState) -> QuotaInfo:
    return QuotaInfo(
        limit=state.limit,
        used=state.used,
        remaining=state.remaining,
        unlimited=state.unlimited,
        exhausted=state.exhausted,
        period=state.period,
        resets_at=state.resets_at,
    )


@router.get("/quota", response_model=QuotaOverview)
def read_quota(user: CurrentUser, db: DbSession) -> QuotaOverview:
    states = quota.overview(db, user)
    return QuotaOverview(
        movie=_quota_info(states["movie"]),
        tv=_quota_info(states["tv"]),
        auto_approve=user.effective_auto_approve,
    )


@router.get("/mine", response_model=list[RequestPublic])
def my_requests(user: CurrentUser, db: DbSession) -> list[MediaRequest]:
    return list(
        db.scalars(
            select(MediaRequest)
            .where(MediaRequest.user_id == user.id)
            .order_by(MediaRequest.requested_at.desc())
        )
    )


@router.post("", response_model=RequestPublic, status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: RequestCreate, user: CurrentUser, db: DbSession
) -> MediaRequest:
    settings = load_settings(db)

    try:
        item = await media.detail(db, settings, payload.media_type.value, payload.tmdb_id)
    except TmdbError as error:
        raise HTTPException(status_code=502, detail=error.message) from error

    try:
        return await requests_service.create_request(
            db,
            settings,
            user,
            item,
            payload.quality_profile_id,
            payload.root_folder_path,
        )
    except requests_service.RequestError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.post("/{request_id}/feedback", response_model=RequestPublic)
def give_feedback(
    request_id: Annotated[int, Path(ge=1)],
    payload: FeedbackCreate,
    user: CurrentUser,
    db: DbSession,
) -> MediaRequest:
    """Eigene Anfrage bewerten - taugt die heruntergeladene Fassung etwas?

    Die Entscheider werden benachrichtigt; bei schwacher Bewertung deutlicher,
    damit man reagieren kann, statt es zufaellig zu entdecken.
    """
    request = db.get(MediaRequest, request_id)
    if request is None or request.user_id != user.id:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden.")
    if user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Administratoren bewerten nicht - sie beantworten die "
                "Rückmeldungen der Benutzer."
            ),
        )
    if request.status != RequestStatus.downloaded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bewerten kannst du erst, wenn der Titel heruntergeladen ist.",
        )

    war_bewertet = request.rating is not None
    request.rating = payload.rating
    request.feedback = (payload.comment or "").strip() or None
    request.rated_at = utcnow()
    db.commit()

    if not war_bewertet:
        schwach = payload.rating <= POOR_RATING
        for entscheider in db.scalars(
            select(User).where(
                User.role.in_((Role.admin, Role.approver)), User.is_active.is_(True)
            )
        ):
            db.add(
                Notification(
                    user_id=entscheider.id,
                    request_id=request.id,
                    type=NotificationType.feedback_poor if schwach else NotificationType.feedback,
                    message_key=(
                        "notifications.feedbackPoor" if schwach else "notifications.feedback"
                    ),
                    message_title=request.title,
                )
            )
        db.commit()

    logger.info(
        "User %r rated %r with %d/5%s",
        user.username,
        request.title,
        payload.rating,
        f": {request.feedback}" if request.feedback else "",
    )
    db.refresh(request)
    return request


@router.post("/{request_id}/cancel", response_model=RequestPublic)
async def cancel_own_request(
    request_id: Annotated[int, Path(ge=1)], user: CurrentUser, db: DbSession
) -> MediaRequest:
    """Eigene laufende Anfrage abbrechen.

    Sinnvoll, wenn ein Titel seit Tagen nicht gefunden wird: der Platz im
    eigenen Kontingent wird dadurch wieder frei.
    """
    request = db.get(MediaRequest, request_id)
    if request is None or request.user_id != user.id:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden.")

    try:
        return await requests_service.cancel(db, load_settings(db), request)
    except requests_service.RequestError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_request(
    request_id: Annotated[int, Path(ge=1)], user: CurrentUser, db: DbSession
) -> None:
    try:
        requests_service.withdraw(db, user, request_id)
    except requests_service.RequestError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
