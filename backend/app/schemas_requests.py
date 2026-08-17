"""Datenformate rund um Anfragen."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import MediaType, QuotaPeriod, RequestStatus


class RequestCreate(BaseModel):
    """Was die Oberflaeche zum Anlegen schickt.

    Titel, Poster und TVDB-Kennung holt der Server selbst von TMDB - so kann
    niemand ueber den Browser falsche Angaben unterschieben.
    """

    media_type: MediaType
    tmdb_id: int = Field(ge=1)
    quality_profile_id: int = Field(ge=1)
    root_folder_path: str = Field(min_length=1, max_length=500)


class FeedbackCreate(BaseModel):
    """Rueckmeldung des Anfragenden zur Qualitaet des Downloads."""

    rating: int = Field(ge=0, le=5)
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackReply(BaseModel):
    """Antwort eines Administrators auf eine Rueckmeldung."""

    reply: str = Field(min_length=1, max_length=1000)


class RequestPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_type: MediaType
    tmdb_id: int
    title: str
    poster_path: str | None
    release_date: str | None
    status: RequestStatus
    quality_profile_id: int | None
    root_folder_path: str | None
    requested_at: datetime
    approved_at: datetime | None
    completed_at: datetime | None
    rejection_reason: str | None
    error_message: str | None
    rating: int | None
    feedback: str | None
    rated_at: datetime | None
    feedback_reply: str | None
    replied_at: datetime | None


class RequestWithUser(RequestPublic):
    """Fuer die Freigabe-Uebersicht: wer hat was angefragt.

    Die Benutzer-Kennung wird mitgeliefert, damit auch Entscheider gruppieren
    und sammelfreigeben koennen - sie duerfen die Benutzerliste nicht abrufen.
    """

    user_id: int
    username: str
    display_name: str | None
    avatar_url: str | None


class QuotaInfo(BaseModel):
    limit: int | None
    used: int
    remaining: int | None
    unlimited: bool
    exhausted: bool
    period: QuotaPeriod
    resets_at: datetime | None


class QuotaOverview(BaseModel):
    movie: QuotaInfo
    tv: QuotaInfo
    auto_approve: bool
