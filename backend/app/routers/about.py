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


class Neuigkeiten(BaseModel):
    """Ob der "Alles, was neu ist"-Hinweis fuer dieses Konto ansteht."""

    version: str
    # Nur Administratoren bekommen den Balken: Sie haben das Update
    # eingespielt, sie sollen wissen, was es bringt. Fuer alle anderen ist
    # ``offen`` immer falsch.
    offen: bool
    # Bis wohin dieses Konto quittiert hat; ``None`` = noch nie. Das Fenster
    # haelt die letzten Fassungen vor und markiert damit, welche davon seither
    # dazugekommen sind - ohne die Angabe muesste man raten, was man schon
    # gelesen hat.
    zuletzt_gesehen: str | None = None


@router.get("/neuigkeiten", response_model=Neuigkeiten)
def neuigkeiten(user: CurrentUser) -> Neuigkeiten:
    from ..models import Role

    offen = user.role == Role.admin and user.changelog_gesehen != __version__
    return Neuigkeiten(
        version=__version__, offen=offen, zuletzt_gesehen=user.changelog_gesehen
    )


@router.post("/neuigkeiten/gesehen", response_model=Neuigkeiten)
def neuigkeiten_gesehen(user: CurrentUser, db: DbSession) -> Neuigkeiten:
    """"Verstanden, nicht mehr anzeigen" - bis zum naechsten Update.

    Gespeichert wird die Fassung, nicht ein Haken: So kommt der Balken nach
    dem naechsten Update von selbst wieder, ohne dass irgendetwas
    zurueckgesetzt werden muesste.
    """
    user.changelog_gesehen = __version__
    db.commit()
    return Neuigkeiten(version=__version__, offen=False, zuletzt_gesehen=__version__)


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
