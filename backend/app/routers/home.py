"""Startseite: was zuletzt in der Bibliothek gelandet ist."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import CurrentUser, DbSession
from ..models import MediaRequest, MediaType, RequestStatus
from ..services import media
from ..services.settings_service import load_settings
from ..services.tmdb import image_url

router = APIRouter(prefix="/api/home", tags=["home"])

logger = logging.getLogger("nexview.home")

# So viele Titel zeigt die Startseite hoechstens.
LIMIT = 12


class RecentItem(BaseModel):
    request_id: int
    media_type: MediaType
    tmdb_id: int
    title: str
    overview: str = ""
    poster_url: str | None = None
    backdrop_url: str | None = None
    release_date: str | None = None
    vote_average: float = 0.0
    runtime_minutes: int | None = None
    genres: list[str] = []
    completed_at: datetime | None = None
    requested_by: str
    requester_avatar: str | None = None


@router.get("/recent", response_model=list[RecentItem])
async def recent_downloads(user: CurrentUser, db: DbSession) -> list[RecentItem]:
    """Die zuletzt fertig geladenen Titel - fuer alle sichtbar.

    Die Details kommen von TMDB (Handlung, Hintergrundbild). Klappt das nicht,
    bleiben Titel und Poster aus der eigenen Datenbank uebrig - die Startseite
    ist dann schlichter, aber nicht leer.
    """
    anfragen = list(
        db.scalars(
            select(MediaRequest)
            .options(selectinload(MediaRequest.user))
            .where(MediaRequest.status == RequestStatus.downloaded)
            .order_by(
                MediaRequest.completed_at.desc().nullslast(),
                MediaRequest.requested_at.desc(),
            )
            .limit(LIMIT)
        )
    )
    if not anfragen:
        return []

    settings = load_settings(db)

    async def hole(request: MediaRequest):
        try:
            return await media.detail(db, settings, request.media_type.value, request.tmdb_id)
        except Exception as fehler:  # noqa: BLE001 - die Startseite darf nie scheitern
            logger.warning("Could not load details for %r: %s", request.title, fehler)
            return None

    details = await asyncio.gather(*(hole(request) for request in anfragen))

    eintraege: list[RecentItem] = []
    for request, item in zip(anfragen, details, strict=True):
        eintraege.append(
            RecentItem(
                request_id=request.id,
                media_type=request.media_type,
                tmdb_id=request.tmdb_id,
                title=item.title if item else request.title,
                overview=item.overview if item else "",
                poster_url=(item.poster_url if item else None) or image_url(request.poster_path),
                backdrop_url=item.backdrop_url if item else None,
                release_date=(item.release_date if item else None) or request.release_date,
                vote_average=item.vote_average if item else 0.0,
                runtime_minutes=item.runtime_minutes if item else None,
                genres=item.genres if item else [],
                completed_at=request.completed_at,
                requested_by=request.user.display_name or request.user.username,
                requester_avatar=request.user.avatar_url,
            )
        )
    return eintraege
