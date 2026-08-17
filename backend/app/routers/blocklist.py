"""Die Sperrliste verwalten - ausschliesslich fuer Administratoren.

``AdminUser`` und nicht ``ApproverUser``, und zwar an jedem einzelnen Endpunkt:
ein Entscheider entscheidet ueber die Anfrage, die vor ihm liegt. Ob ein Titel
in dieser Bibliothek grundsaetzlich nichts zu suchen hat, ist eine andere Frage
- die beantwortet der Betreiber.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field

from ..deps import AdminUser, DbSession
from ..models import Blocked, MediaType
from ..services import blocklist

router = APIRouter(prefix="/api/admin/blocklist", tags=["blocklist"])


class BlockedOut(BaseModel):
    media_type: MediaType
    tmdb_id: int
    title: str
    poster_url: str | None
    reason: str | None
    created_at: datetime


class BlockIn(BaseModel):
    media_type: MediaType
    tmdb_id: int = Field(ge=1)
    # Titel und Bild kommen von der Kachel mit, damit die Uebersicht ohne eine
    # TMDB-Abfrage je Eintrag auskommt.
    title: str = Field(default="", max_length=300)
    poster_url: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=500)


@router.get("", response_model=list[BlockedOut])
def alle_gesperrten(admin: AdminUser, db: DbSession) -> list[Blocked]:
    return blocklist.alle(db)


@router.post("", response_model=BlockedOut, status_code=status.HTTP_201_CREATED)
def sperren(payload: BlockIn, admin: AdminUser, db: DbSession) -> Blocked:
    """Sperren. Ein zweites Mal zu sperren ist kein Fehler."""
    eintrag = blocklist.sperren(
        db,
        media_type=payload.media_type,
        tmdb_id=payload.tmdb_id,
        title=payload.title,
        poster_url=payload.poster_url,
        reason=payload.reason,
        admin=admin,
    )
    db.commit()
    db.refresh(eintrag)
    return eintrag


@router.delete("/{media_type}/{tmdb_id}", status_code=status.HTTP_204_NO_CONTENT)
def freigeben(
    media_type: MediaType,
    tmdb_id: Annotated[int, Path(ge=1)],
    admin: AdminUser,
    db: DbSession,
) -> None:
    if not blocklist.freigeben(db, media_type, tmdb_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dieser Titel steht nicht auf der Sperrliste.",
        )
    db.commit()
