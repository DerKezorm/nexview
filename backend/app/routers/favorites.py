"""Favoriten: was jemand mit dem Herz markiert hat.

Grundlage der kuratierten Empfehlungen auf der Startseite. Bewusst getrennt
von den Anfragen: was jemand mag, ist etwas anderes als das, was er sich
gerade bestellt hat - und man kann auch etwas mögen, das längst in der
Bibliothek liegt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from ..deps import CurrentUser, DbSession
from ..models import Favorite, FavoritePerson, MediaType
from ..services import media
from ..services.settings_service import for_user, load_settings
from .. import meldungen

router = APIRouter(prefix="/api/favorites", tags=["favorites"])


# --- Personen --------------------------------------------------------------
#
# Bewusst vor den Titel-Endpunkten definiert: "/people" und
# "/people/{id}" wuerden sonst gegen "/{media_type}/{tmdb_id}" laufen, und die
# zuerst passende Route gewinnt. Personen haben keine Altersfreigabe, deshalb
# entfaellt hier die Filterung, die es bei den Titeln gibt.


class FavoritePersonOut(BaseModel):
    person_id: int
    name: str
    photo_url: str | None
    department: str
    created_at: datetime


class FavoritePersonIn(BaseModel):
    person_id: int = Field(ge=1)
    name: str = Field(default="", max_length=200)
    photo_url: str | None = Field(default=None, max_length=500)
    department: str = Field(default="", max_length=40)


@router.get("/people", response_model=list[FavoritePersonOut])
def my_favorite_people(user: CurrentUser, db: DbSession) -> list[FavoritePerson]:
    """Eigene gemerkte Personen, neueste zuerst."""
    return list(
        db.scalars(
            select(FavoritePerson)
            .where(FavoritePerson.user_id == user.id)
            .order_by(FavoritePerson.created_at.desc())
        )
    )


@router.post("/people", response_model=FavoritePersonOut, status_code=status.HTTP_201_CREATED)
def add_favorite_person(
    payload: FavoritePersonIn, user: CurrentUser, db: DbSession
) -> FavoritePerson:
    """Person merken. Ein zweites Mal ist kein Fehler."""
    vorhanden = db.scalar(
        select(FavoritePerson).where(
            FavoritePerson.user_id == user.id,
            FavoritePerson.person_id == payload.person_id,
        )
    )
    if vorhanden is not None:
        return vorhanden

    eintrag = FavoritePerson(
        user_id=user.id,
        person_id=payload.person_id,
        name=payload.name.strip(),
        photo_url=payload.photo_url,
        department=payload.department.strip(),
    )
    db.add(eintrag)
    db.commit()
    db.refresh(eintrag)
    return eintrag


@router.delete("/people/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite_person(
    person_id: Annotated[int, Path(ge=1)], user: CurrentUser, db: DbSession
) -> None:
    """Person aus den Favoriten nehmen."""
    ergebnis = db.execute(
        delete(FavoritePerson).where(
            FavoritePerson.user_id == user.id,
            FavoritePerson.person_id == person_id,
        )
    )
    db.commit()
    if ergebnis.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung(
                "person_not_marked",
                "Diese Person war nicht markiert.",
            ),
        )


class FavoriteOut(BaseModel):
    media_type: MediaType
    tmdb_id: int
    title: str
    poster_url: str | None
    created_at: datetime


class FavoriteIn(BaseModel):
    media_type: MediaType
    tmdb_id: int = Field(ge=1)
    # Titel und Bild werden mitgegeben, damit die Favoritenliste ohne eine
    # TMDB-Abfrage je Eintrag auskommt.
    title: str = Field(default="", max_length=300)
    poster_url: str | None = Field(default=None, max_length=500)


@router.get("", response_model=list[FavoriteOut])
async def my_favorites(user: CurrentUser, db: DbSession) -> list[Favorite]:
    """Eigene Markierungen, gefiltert nach der Altersbeschraenkung.

    Die Filterung ist noetig, weil Titel und Bild hier aus Nexviews eigener
    Tabelle kommen und nicht von TMDB - eine Sperre an den TMDB-Abfragen
    allein wuerde sie nicht erfassen. Der Fall tritt ein, sobald ein Konto
    *nachtraeglich* beschraenkt wird: was vorher markiert wurde, stuende sonst
    weiterhin unter "Mag ich".
    """
    eintraege = list(
        db.scalars(
            select(Favorite)
            .where(Favorite.user_id == user.id)
            .order_by(Favorite.created_at.desc())
        )
    )
    if user.age is None or not eintraege:
        return eintraege

    settings = for_user(load_settings(db), user)
    erlaubt: list[Favorite] = []
    for art in (MediaType.movie, MediaType.tv):
        kennungen = [e.tmdb_id for e in eintraege if e.media_type == art]
        if not kennungen:
            continue
        frei = await media.erlaubte_kennungen(db, settings, art.value, kennungen)
        erlaubt.extend(e for e in eintraege if e.media_type == art and e.tmdb_id in frei)

    # Die Sortierung der Abfrage wiederherstellen - oben wurde nach Art gruppiert.
    return sorted(erlaubt, key=lambda e: e.created_at, reverse=True)


@router.post("", response_model=FavoriteOut, status_code=status.HTTP_201_CREATED)
def add_favorite(payload: FavoriteIn, user: CurrentUser, db: DbSession) -> Favorite:
    """Markieren. Ein zweites Mal zu markieren ist kein Fehler."""
    vorhanden = db.scalar(
        select(Favorite).where(
            Favorite.user_id == user.id,
            Favorite.media_type == payload.media_type,
            Favorite.tmdb_id == payload.tmdb_id,
        )
    )
    if vorhanden is not None:
        return vorhanden

    eintrag = Favorite(
        user_id=user.id,
        media_type=payload.media_type,
        tmdb_id=payload.tmdb_id,
        title=payload.title.strip(),
        poster_url=payload.poster_url,
    )
    db.add(eintrag)
    db.commit()
    db.refresh(eintrag)
    return eintrag


@router.delete("/{media_type}/{tmdb_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(
    media_type: Annotated[Literal["movie", "tv"], Path()],
    tmdb_id: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    db: DbSession,
) -> None:
    """Markierung entfernen. Was nicht markiert war, bleibt es auch."""
    ergebnis = db.execute(
        delete(Favorite).where(
            Favorite.user_id == user.id,
            Favorite.media_type == MediaType(media_type),
            Favorite.tmdb_id == tmdb_id,
        )
    )
    db.commit()
    if ergebnis.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung(
                "title_not_marked",
                "Dieser Titel war nicht markiert.",
            ),
        )
