"""Statistik-Seite - fuer Administratoren und Entscheider."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ..deps import ApproverUser, DbSession
from ..services import stats

router = APIRouter(prefix="/api/admin/stats", tags=["admin"])


class UserStatsPublic(BaseModel):
    user_id: int
    username: str
    display_name: str | None
    avatar_url: str | None
    role: str
    total: int
    movies: int
    series: int
    downloaded: int
    pending: int
    rejected: int
    cancelled: int
    failed: int
    ratings: int
    average_rating: float | None
    poor_ratings: int
    success_rate: float | None
    quota_movie_used: int
    quota_movie_limit: int | None
    quota_series_used: int
    quota_series_limit: int | None


class TotalsPublic(BaseModel):
    requests: int
    movies: int
    series: int
    downloaded: int
    downloaded_movies: int
    downloaded_series: int
    pending: int
    rejected: int
    cancelled: int
    failed: int
    active_users: int
    ratings: int
    average_rating: float | None
    poor_ratings: int
    unanswered_feedback: int
    rating_distribution: dict[int, int]
    last_request_at: datetime | None


class MonthPoint(BaseModel):
    month: str
    movies: int
    series: int


class PopularTitle(BaseModel):
    media_type: str
    tmdb_id: int
    title: str
    poster_path: str | None
    count: int


class StatsPublic(BaseModel):
    totals: TotalsPublic
    users: list[UserStatsPublic]
    history: list[MonthPoint]
    most_requested: list[PopularTitle]


@router.get("", response_model=StatsPublic)
def read_stats(entscheider: ApproverUser, db: DbSession) -> dict:
    """Zahlen zu Anfragen, Downloads, Kontingenten und Bewertungen."""
    zahlen = stats.collect(db)
    return {
        "totals": TotalsPublic(
            **{
                feld: getattr(zahlen["totals"], feld)
                for feld in TotalsPublic.model_fields
            }
        ),
        "users": [
            UserStatsPublic(
                **{feld: getattr(eintrag, feld) for feld in UserStatsPublic.model_fields}
            )
            for eintrag in zahlen["users"]
        ],
        "history": zahlen["history"],
        "most_requested": zahlen["most_requested"],
    }
