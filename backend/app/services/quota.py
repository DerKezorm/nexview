"""Kontingente: wie viele Anfragen ein Benutzer im Zeitraum stellen darf.

Filme und Serien werden getrennt gezaehlt. Der Zeitraum ist ein Kalender-
zeitraum (seit Mitternacht / seit Montag / seit dem Ersten) - das entspricht
dem, was man unter "maximal X pro Tag" versteht.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import MediaRequest, MediaType, QuotaPeriod, RequestStatus, User

# Abgelehnte Anfragen zaehlen nicht gegen das Kontingent - sonst wuerde eine
# Ablehnung den Benutzer zusaetzlich bestrafen.
COUNTED_STATUSES = (
    RequestStatus.pending_approval,
    RequestStatus.approved,
    RequestStatus.searching,
    RequestStatus.downloaded,
    RequestStatus.failed,
)


@dataclass(frozen=True)
class QuotaState:
    """Stand des Kontingents fuer eine Medienart."""

    limit: int | None  # None = unbegrenzt
    used: int
    period: QuotaPeriod
    resets_at: datetime | None

    @property
    def unlimited(self) -> bool:
        return self.limit is None

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit


def period_start(period: QuotaPeriod, now: datetime | None = None) -> datetime:
    """Beginn des laufenden Zeitraums (in UTC, ohne Zeitzonenangabe)."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == QuotaPeriod.day:
        return midnight
    if period == QuotaPeriod.week:
        return midnight - timedelta(days=midnight.weekday())
    return midnight.replace(day=1)


def period_end(period: QuotaPeriod, start: datetime) -> datetime:
    if period == QuotaPeriod.day:
        return start + timedelta(days=1)
    if period == QuotaPeriod.week:
        return start + timedelta(days=7)
    # Monatsende: in den naechsten Monat springen und auf den Ersten setzen.
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _limit_for(user: User, media_type: MediaType) -> int | None:
    """Wie viele Anfragen darf dieser Benutzer? ``None`` heisst unbegrenzt.

    Administratoren immer unbegrenzt - wie bei der Sperrliste, der Freigabe
    und 4K: Sie setzen die Grenzen und koennten die eigene jederzeit
    heraufsetzen. Bliebe sie bestehen, waere es eine Huerde, die genau eine
    Person aufhaelt: die, die sie gerade wegklicken kann.
    """
    if user.is_admin:
        return None
    return (
        user.quota_movies_limit if media_type == MediaType.movie else user.quota_series_limit
    )


def counting_start(user: User) -> datetime:
    """Ab wann der Verbrauch zaehlt.

    Normalerweise der Beginn des Zeitraums. Hat der Admin das Kontingent von
    Hand zurueckgesetzt und liegt das *innerhalb* des laufenden Zeitraums,
    zaehlt es ab da - eine Ruecksetzung aus einem frueheren Zeitraum ist durch
    den Wechsel ohnehin schon ueberholt.
    """
    start = period_start(user.quota_period)
    if user.quota_reset_at is not None and user.quota_reset_at > start:
        return user.quota_reset_at
    return start


def state_for(db: Session, user: User, media_type: MediaType) -> QuotaState:
    limit = _limit_for(user, media_type)
    start = counting_start(user)

    used = (
        db.scalar(
            select(func.count(MediaRequest.id)).where(
                MediaRequest.user_id == user.id,
                MediaRequest.media_type == media_type,
                MediaRequest.status.in_(COUNTED_STATUSES),
                MediaRequest.requested_at >= start,
            )
        )
        or 0
    )

    return QuotaState(
        limit=limit,
        used=used,
        # Der naechste automatische Wechsel richtet sich nach dem Kalender,
        # nicht nach einer Ruecksetzung von Hand.
        resets_at=(
            None
            if limit is None
            else period_end(user.quota_period, period_start(user.quota_period))
        ),
        period=user.quota_period,
    )


def overview(db: Session, user: User) -> dict[str, QuotaState]:
    return {
        "movie": state_for(db, user, MediaType.movie),
        "tv": state_for(db, user, MediaType.tv),
    }
