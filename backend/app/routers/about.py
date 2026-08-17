"""Angaben fuer die Ueber-Seite: Version, Herkunft, Update-Hinweis."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from .. import __version__
from ..deps import AdminUser, CurrentUser, DbSession
from ..services import settings_service, updates

router = APIRouter(prefix="/api/about", tags=["about"])


class AboutInfo(BaseModel):
    version: str
    repo_url: str
    release_url: str
    license: str = "MIT"
    # Nur fuer Administratoren gefuellt - alle anderen koennen ohnehin nichts
    # aktualisieren, und ein Hinweis, dem niemand nachgehen kann, ist nur Laerm.
    update_checked: bool = False
    latest_version: str | None = None
    update_available: bool = False
    checked_at: datetime | None = None


def _basis() -> AboutInfo:
    return AboutInfo(
        version=__version__,
        repo_url=updates.REPO_URL,
        release_url=updates.RELEASES_URL,
    )


@router.get("", response_model=AboutInfo)
async def about(user: CurrentUser, db: DbSession) -> AboutInfo:
    info = _basis()
    if not user.is_admin:
        return info

    settings = settings_service.load_settings(db)
    if not settings.update_check:
        return info

    stand = await updates.status(enabled=True)
    info.update_checked = True
    info.latest_version = stand.latest
    info.update_available = stand.update_available
    info.checked_at = stand.checked_at
    return info


@router.post("/check", response_model=AboutInfo)
async def check_now(admin: AdminUser, db: DbSession) -> AboutInfo:
    """Sofort nachsehen, ohne auf den taeglichen Zeitpunkt zu warten."""
    settings = settings_service.load_settings(db)
    info = _basis()
    if not settings.update_check:
        return info

    stand = await updates.status(enabled=True, force=True)
    info.update_checked = True
    info.latest_version = stand.latest
    info.update_available = stand.update_available
    info.checked_at = stand.checked_at
    return info
