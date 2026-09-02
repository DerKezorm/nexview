"""In-App-Benachrichtigungen: was der Nutzer verpasst hat."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select, update

from .. import meldungen
from ..deps import CurrentUser, DbSession
from ..models import Notification, NotificationType
from ..services import meldungsziele

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: NotificationType
    # Uebersetzungsschluessel - die Oberflaeche entscheidet ueber die Sprache.
    message_key: str
    message_title: str | None
    # Bei Staffelanfragen die Staffel - sonst ``None``. Die Oberflaeche haengt
    # sie an den Titel, damit sich fuenf Meldungen zu einer Serie unterscheiden.
    season: int | None = None
    # Bei Folgen-Paketen die Folgen - aus demselben Grund.
    episodes: list[int] | None = None
    request_id: int | None
    # Damit die Glocke direkt in den Ticketverlauf springt.
    ticket_id: int | None
    is_read: bool
    created_at: datetime
    # ⚠️ **Wohin der Klick fuehrt - vom Server, nicht geraten.** Die Glocke
    # hatte dafuer eine eigene Liste mit acht Faellen und einem Rueckfall auf
    # die eigene Anfrageliste. Zwei Sorten, die an den Betreiber gehen
    # ("Titel abgegeben", "Instanz meldet ein Problem"), landeten damit bei
    # seinen eigenen Anfragen. Siehe ``services/meldungsziele.py``.
    ziel: str


@router.get("", response_model=list[NotificationPublic])
def list_notifications(
    user: CurrentUser,
    db: DbSession,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[NotificationPublic]:
    query = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if unread_only:
        query = query.where(Notification.is_read.is_(False))
    return [
        NotificationPublic(
            **{
                feld: getattr(eintrag, feld)
                for feld in NotificationPublic.model_fields
                if feld != "ziel"
            },
            ziel=meldungsziele.ziel_fuer(eintrag),
        )
        for eintrag in db.scalars(query)
    ]


@router.get("/unread/count")
def unread_count(user: CurrentUser, db: DbSession) -> dict[str, int]:
    count = (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.is_read.is_(False)
            )
        )
        or 0
    )
    return {"unread": count}


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(
    notification_id: Annotated[int, Path(ge=1)], user: CurrentUser, db: DbSession
) -> None:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung(
                "notification_not_found",
                "Benachrichtigung nicht gefunden.",
            ),
        )
    notification.is_read = True
    db.commit()


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(user: CurrentUser, db: DbSession) -> None:
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.commit()


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(
    notification_id: Annotated[int, Path(ge=1)], user: CurrentUser, db: DbSession
) -> None:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung(
                "notification_not_found",
                "Benachrichtigung nicht gefunden.",
            ),
        )
    db.delete(notification)
    db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_all(user: CurrentUser, db: DbSession) -> None:
    """Eigene Benachrichtigungen loeschen - nicht nur als gelesen markieren."""
    db.execute(delete(Notification).where(Notification.user_id == user.id))
    db.commit()
