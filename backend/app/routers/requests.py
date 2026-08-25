"""Anfragen stellen und die eigenen einsehen."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import CurrentUser, DbSession
from ..models import (
    TitleRating,
    MediaRequest,
    NotificationType,
    RequestStatus,
    utcnow,
)
from ..schemas_requests import (
    FeedbackCreate,
    QuotaInfo,
    QuotaOverview,
    RequestCreate,
    RequestPublic,
)
from ..services import media, notify, quota, requests_service, ratings
from ..services.quota import QuotaState
from ..services.settings_service import for_user, load_settings
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


def _mit_bewertung(zeile: MediaRequest, bewertung: TitleRating | None) -> RequestPublic:
    """Eine Anfrage samt der Bewertung, die am **Titel** haengt.

    Die gleichnamigen Spalten an der Anfrage stehen noch da, werden aber seit
    0.19 nicht mehr geschrieben - bewerten darf jeder, der einen Titel
    vorliegen hat, und dafuer braucht es keine Anfrage.
    """
    eintrag = RequestPublic.model_validate(zeile)
    eintrag.rating = bewertung.rating if bewertung else None
    eintrag.feedback = bewertung.comment if bewertung else None
    eintrag.feedback_reply = bewertung.reply if bewertung else None
    eintrag.rated_at = bewertung.updated_at if bewertung else None
    eintrag.replied_at = bewertung.replied_at if bewertung else None
    eintrag.rating_outdated = bool(bewertung and bewertung.outdated)
    return eintrag


@router.get("/mine", response_model=list[RequestPublic])
def my_requests(user: CurrentUser, db: DbSession) -> list[RequestPublic]:
    """Die eigenen Anfragen - mit der Bewertung, die am Titel haengt.

    ⚠️ Die Bewertungsfelder kommen seit 0.19 aus ``title_ratings`` und nicht
    mehr aus den gleichnamigen Spalten der Anfrage. Die stehen dort noch, sind
    aber tot: Bewerten darf jeder, der einen Titel vorliegen hat, und dafuer
    braucht es keine Anfrage. Einmal alle Bewertungen dieser Person holen statt
    einer Abfrage je Zeile.
    """
    zeilen = list(
        db.scalars(
            select(MediaRequest)
            # Den Entscheider gleich mitladen: Der Verlauf nennt seinen Namen,
            # und ohne das waere es eine Abfrage je Zeile.
            .options(selectinload(MediaRequest.approver))
            .where(MediaRequest.user_id == user.id)
            .order_by(MediaRequest.requested_at.desc())
        )
    )

    nach_titel = {
        (b.media_type, b.tmdb_id, b.season): b
        for b in db.scalars(
            select(TitleRating).where(TitleRating.user_id == user.id)
        )
    }

    return [
        _mit_bewertung(zeile, nach_titel.get((zeile.media_type, zeile.tmdb_id, zeile.season)))
        for zeile in zeilen
    ]


@router.post("", response_model=RequestPublic, status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: RequestCreate, user: CurrentUser, db: DbSession
) -> MediaRequest:
    # Aus Sicht des Anfragenden: der Titel wird so gespeichert, wie er ihn beim
    # Anklicken gesehen hat. Sonst steht in "Meine Anfragen" ploetzlich ein
    # anderer Name als eben noch beim Entdecken.
    settings = for_user(load_settings(db), user)

    # Diese Abfrage ist zugleich die Sperre: ``media.detail`` verweigert die
    # Auskunft ueber alles, was die Altersbeschraenkung des Anfragenden
    # ueberschreitet. Damit ist auch der Weg an der Oberflaeche vorbei zu -
    # eine Kachel aus einem alten Zwischenspeicher oder ein von Hand
    # abgeschickter Aufruf kommt hier trotzdem nicht durch.
    try:
        item = await media.detail(db, settings, payload.media_type.value, payload.tmdb_id)
    except TmdbError as error:
        # "Gibt es nicht" bleibt 404; nur echte Stoerungen sind ein 502.
        code = 404 if error.status_code == 404 else 502
        raise HTTPException(status_code=code, detail=error.message) from error

    try:
        return await requests_service.create_request(
            db,
            settings,
            user,
            item,
            payload.quality_profile_id,
            payload.root_folder_path,
            payload.season,
            payload.tier,
            payload.from_watchlist,
            payload.monitor_future,
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

    # ⚠️ **Der Weg bleibt, das Ziel hat sich geaendert.**
    #
    # Seit 0.19 haengt eine Bewertung am **Titel**, nicht an der Anfrage -
    # bewerten darf jeder, der ihn vorliegen hat. Dieser Endpunkt gibt es
    # weiterhin, weil er der bequeme Weg aus "Meine Anfragen" ist; er schreibt
    # aber in dieselbe Tabelle wie ``/api/feedback``. Zwei Ziele hiessen zwei
    # Wahrheiten, und die zweite waere genau die Art Altlast, die einem ein
    # halbes Jahr spaeter begegnet.
    eintrag, ist_neu = ratings.setzen(
        db,
        user,
        request.media_type,
        request.tmdb_id,
        rating=payload.rating,
        comment=payload.comment,
        title=request.title,
        season=request.season,
        file_size_bytes=request.file_size_bytes or 0,
    )
    war_bewertet = not ist_neu
    db.commit()

    if not war_bewertet:
        schwach = payload.rating <= POOR_RATING
        # Geht an Admins *und* Entscheider: antworten darf zwar nur der Admin,
        # aber Entscheider sehen die Bewertungen auch in der Statistik - sie
        # von der Meldung auszuschliessen waere ein Bruch.
        notify.create_for_approvers(
            db,
            kind=NotificationType.feedback_poor if schwach else NotificationType.feedback,
            message_key="notifications.feedbackPoor" if schwach else "notifications.feedback",
            request=request,
            ausser=user.id,
        )
        db.commit()

    logger.info(
        "User %r rated %r with %d/5%s",
        user.username,
        eintrag.title,
        payload.rating,
        f": {request.feedback}" if request.feedback else "",
    )
    db.refresh(request)
    db.refresh(eintrag)
    return _mit_bewertung(request, eintrag)


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
