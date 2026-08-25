"""Rueckmeldungen zur Qualitaet: setzen, lesen, beantworten.

Am Titel, nicht an der Anfrage - warum, steht in ``models.TitleRating``.

⚠️ **Der Pfad heisst ``/api/feedback`` und nicht ``/api/ratings``.** Letzteres
ist vergeben: ``details.py`` liefert darunter die **Kritikerwertungen** von
IMDb, Rotten Tomatoes und Metacritic. Zwei verschiedene Dinge unter demselben
Wort waeren eine Verwechslung, die niemand mehr aufloest - und die Oberflaeche
nennt es ohnehin ueberall "Rueckmeldung" (``feedback.*``).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import select
from pydantic import BaseModel, Field

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import MediaType, NotificationType, TitleRating, User, utcnow
from ..services import library, notify, ratings
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

logger = logging.getLogger("nexview.ratings")

MediaTypePath = Annotated[str, Path(pattern="^(movie|tv)$")]


class RatingIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)
    # Nur zur Anzeige - erspart der Uebersicht eine TMDB-Abfrage je Zeile.
    title: str = Field(default="", max_length=300)
    season: int | None = Field(default=None, ge=0, le=200)


class ReplyIn(BaseModel):
    reply: str = Field(min_length=1, max_length=1000)


class RatingOut(BaseModel):
    id: int
    media_type: str
    tmdb_id: int
    season: int | None
    rating: int
    comment: str | None
    title: str
    reply: str | None
    outdated: bool
    updated_at: str

    @classmethod
    def von(cls, eintrag: TitleRating) -> "RatingOut":
        return cls(
            id=eintrag.id,
            media_type=eintrag.media_type.value,
            tmdb_id=eintrag.tmdb_id,
            season=eintrag.season,
            rating=eintrag.rating,
            comment=eintrag.comment,
            title=eintrag.title,
            reply=eintrag.reply,
            outdated=eintrag.outdated,
            updated_at=eintrag.updated_at.isoformat(),
        )


# ⚠️ **Feste Pfade zuerst.** ``/mine`` und ``/{id}/reply`` muessen vor
# ``/{media_type}/{tmdb_id}`` stehen, sonst versucht FastAPI, "mine" als
# Medienart zu lesen und antwortet mit 422 - die zuerst passende Route
# gewinnt. Dieselbe Falle wie bei den Kinderwuenschen in ``children.py``.


@router.get("/mine", response_model=list[RatingOut])
def meine_bewertungen(user: CurrentUser, db: DbSession) -> list[RatingOut]:
    zeilen = db.scalars(
        select(TitleRating)
        .where(TitleRating.user_id == user.id)
        .order_by(TitleRating.updated_at.desc())
    )
    return [RatingOut.von(z) for z in zeilen]


@router.get("", response_model=list[RatingOut])
def alle(
    admin: AdminUser,
    db: DbSession,
    unanswered: Annotated[bool, Query()] = False,
) -> list[RatingOut]:
    """Alle Rueckmeldungen - fuer die Uebersicht des Betreibers."""
    query = select(TitleRating).order_by(TitleRating.updated_at.desc())
    if unanswered:
        query = query.where(TitleRating.reply.is_(None))
    return [RatingOut.von(z) for z in db.scalars(query)]


@router.post("/{rating_id}/reply", response_model=RatingOut)
def antworten(
    rating_id: Annotated[int, Path(ge=1)],
    payload: ReplyIn,
    admin: AdminUser,
    db: DbSession,
) -> RatingOut:
    """Auf eine Rueckmeldung antworten - nur der Administrator.

    Entscheider sehen die Bewertungen, antworten duerfen sie nicht: Eine
    Antwort ist eine Zusage im Namen der Installation.
    """
    eintrag = db.get(TitleRating, rating_id)
    if eintrag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nicht gefunden.")

    eintrag.reply = payload.reply.strip()
    eintrag.replied_at = utcnow().replace(tzinfo=None)
    eintrag.replied_by = admin.id

    besitzer = db.get(User, eintrag.user_id)
    if besitzer is not None:
        notify.create(
            db,
            user=besitzer,
            kind=NotificationType.feedback_reply,
            message_key="notifications.feedbackReply",
            title=eintrag.title,
        )
    db.commit()
    db.refresh(eintrag)
    return RatingOut.von(eintrag)


@router.put("/{media_type}/{tmdb_id}", response_model=RatingOut)
async def bewerten(
    media_type: MediaTypePath,
    tmdb_id: Annotated[int, Path(ge=1)],
    payload: RatingIn,
    user: CurrentUser,
    db: DbSession,
) -> RatingOut:
    """Die Qualitaet eines vorhandenen Titels beurteilen.

    **Jeder darf**, nicht nur der Besteller: Es geht um die Datei, und die
    beurteilt jeder gleich gut, der sie gesehen hat. Bewusst ohne Gatter ueber
    den Gesehen-Stand - der sagt, dass jemand den *Titel* gesehen hat, nicht
    *diese Datei*; nach einer Aufwertung bleibt der Haken stehen.

    Nur Administratoren nicht: Sie beantworten die Rueckmeldungen der anderen,
    und ein Urteil ueber die eigene Bibliothek waere eine Antwort an sich
    selbst.
    """
    if user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Administratoren bewerten nicht - sie beantworten die "
                "Rückmeldungen der Benutzer."
            ),
        )

    # Die Groesse der Datei, die gerade dort liegt - sie ist der Massstab,
    # an dem spaeter auffaellt, dass Radarr etwas Besseres nachgeschoben hat.
    # Aus dem Zwischenspeicher, den der Bibliotheksabgleich ohnehin fuellt.
    groesse = 0
    if media_type == "movie":
        try:
            bestand = await library.movie_library(load_settings(db))
            groesse = int(getattr(bestand.get(tmdb_id), "size_bytes", 0) or 0)
        except Exception:  # noqa: BLE001 - ohne Groesse geht es auch, nur ohne Alterung
            groesse = 0

    eintrag, ist_neu = ratings.setzen(
        db,
        user,
        MediaType(media_type),
        tmdb_id,
        rating=payload.rating,
        comment=payload.comment,
        title=payload.title.strip(),
        season=payload.season,
        file_size_bytes=groesse,
    )
    db.commit()

    # Nur beim ersten Mal klingeln - eine Aenderung ist kein neues Ereignis.
    if ist_neu:
        schwach = payload.rating <= ratings.POOR_RATING
        notify.create_for_approvers(
            db,
            kind=NotificationType.feedback_poor if schwach else NotificationType.feedback,
            message_key="notifications.feedbackPoor" if schwach else "notifications.feedback",
            title=eintrag.title or str(tmdb_id),
            ausser=user.id,
        )
        db.commit()

    logger.info(
        "User %r rated %r with %d/5%s",
        user.username,
        eintrag.title,
        payload.rating,
        f": {eintrag.comment}" if eintrag.comment else "",
    )
    db.refresh(eintrag)
    return RatingOut.von(eintrag)
