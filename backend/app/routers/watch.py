"""„Sag mir Bescheid" - Titel vormerken und die Vormerkung wieder beenden.

Warum es das gibt und warum Film und Serie sich unterschiedlich verhalten:
siehe ``services/watch.py``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, Field

from ..deps import CurrentUser, DbSession
from ..models import MediaType
from ..services import watch

router = APIRouter(prefix="/api/watch", tags=["watch"])

MediaTypePath = Annotated[str, Path(pattern="^(movie|tv)$")]


class WatchIn(BaseModel):
    # Nur zur Anzeige der eigenen Liste - erspart eine TMDB-Abfrage je Zeile.
    title: str = Field(default="", max_length=300)
    poster_url: str | None = Field(default=None, max_length=500)


class WatchOut(BaseModel):
    media_type: str
    tmdb_id: int
    title: str
    poster_url: str | None
    created_at: str


class WatchState(BaseModel):
    watching: bool


@router.get("", response_model=list[WatchOut])
def meine_vormerkungen(user: CurrentUser, db: DbSession) -> list[WatchOut]:
    """Worauf warte ich gerade?"""
    return [
        WatchOut(
            media_type=eintrag.media_type.value,
            tmdb_id=eintrag.tmdb_id,
            title=eintrag.title,
            poster_url=eintrag.poster_url,
            created_at=eintrag.created_at.isoformat(),
        )
        for eintrag in watch.meine(db, user)
    ]


@router.put("/{media_type}/{tmdb_id}", response_model=WatchState)
def vormerken(
    media_type: MediaTypePath,
    tmdb_id: Annotated[int, Path(ge=1)],
    payload: WatchIn,
    user: CurrentUser,
    db: DbSession,
) -> WatchState:
    """Vormerken. Zweimal geklickt ist kein Fehler, sondern ein Doppelklick.

    Bewusst ``PUT`` und nicht ``POST``: Der Aufruf beschreibt einen Zustand
    („ich warte auf diesen Titel"), nicht ein Ereignis. Zweimal aufgerufen
    liegt hinterher dieselbe eine Zeile da.
    """
    watch.vormerken(
        db,
        user,
        MediaType(media_type),
        tmdb_id,
        title=payload.title.strip(),
        poster_url=payload.poster_url,
    )
    db.commit()
    return WatchState(watching=True)


@router.delete("/{media_type}/{tmdb_id}", response_model=WatchState)
def beenden(
    media_type: MediaTypePath,
    tmdb_id: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    db: DbSession,
) -> WatchState:
    """Nicht mehr warten.

    Kein 404, wenn gar nichts vorgemerkt war: Das Ziel ist „ich will davon
    nichts mehr hören", und das ist danach erfuellt - unabhaengig davon, ob
    vorher eine Zeile da war.
    """
    watch.beenden(db, user, MediaType(media_type), tmdb_id)
    db.commit()
    return WatchState(watching=False)
