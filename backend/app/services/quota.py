"""Kontingente: wie viele Anfragen ein Benutzer im Zeitraum stellen darf.

Filme und Serien werden getrennt gezaehlt. Der Zeitraum ist ein Kalender-
zeitraum (seit Mitternacht / seit Montag / seit dem Ersten) - das entspricht
dem, was man unter "maximal X pro Tag" versteht.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import scheiben
from ..models import MediaRequest, MediaType, QuotaPeriod, RequestStatus, User

if TYPE_CHECKING:  # pragma: no cover - nur fuer die Typpruefung
    from .settings_service import AppSettings

# Was ein Konto eintraegt, wenn es **ausdruecklich** unbegrenzt sein soll.
#
# ⚠️ Drei Bedeutungen, drei Zustaende - und deshalb reichen "leer" und "0"
# nicht aus:
#
# * ``None`` (leer)  -> es gilt der Standardwert des Hauses
# * ``UNBEGRENZT``   -> ausdruecklich ohne Grenze, der Standardwert greift nicht
# * ``0`` und groesser -> genau diese Zahl; die **0 heisst "darf nichts"**
#
# Bis 0.19 war die 0 beim Speicher das Zeichen fuer "unbegrenzt". Diese
# Bedeutung ist umgezogen (``db._kontingente_dreiwertig_machen``), sonst
# haette dieselbe Zahl von einem Tag auf den anderen das Gegenteil bedeutet.
UNBEGRENZT = -1

# Abgelehnte Anfragen zaehlen nicht gegen das Kontingent - sonst wuerde eine
# Ablehnung den Benutzer zusaetzlich bestrafen.
#
# ⚠️ **``failed`` zaehlt aus demselben Grund nicht mehr mit.** Ein Fehlschlag
# ist keine Entscheidung ueber die Person, sondern eine Stoerung im Haus:
# Sonarr war nicht erreichbar, TMDB kannte noch keine TVDB-Kennung. Wer das
# ausbaden muss, verliert einen Platz in seinem Kontingent, ohne je eine Datei
# bekommen zu haben - und beim zweiten Versuch noch einen. Nachgemessen an
# einer Serie, die zweimal fehlschlug: zwei verbrauchte Plaetze, null Dateien.
#
# Die Anfrage bleibt trotzdem als "fehlgeschlagen" in der Liste stehen. Sie
# soll sichtbar sein - sie soll nur nichts mehr kosten. Am Titel selbst hat
# sie ebenfalls keine Wirkung mehr (``requests_service.BADGE_FOR_STATUS``),
# und einen Speicher-Posten bekam sie nie (``storage.ZURECHENBAR``).
COUNTED_STATUSES = (
    RequestStatus.pending_approval,
    RequestStatus.approved,
    RequestStatus.searching,
    RequestStatus.downloaded,
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
    moment = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
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


def _limit_for(
    user: User, media_type: MediaType, settings: AppSettings
) -> int | None:
    """Wie viele Anfragen darf dieser Benutzer? ``None`` heisst unbegrenzt.

    Drei Stufen, dieselben wie beim Speicher (``storage.grenze_in_bytes``) -
    zwei verschiedene Regeln fuer dieselbe Frage waeren eine Falle:

    1. **Administratoren immer unbegrenzt** - wie bei der Sperrliste, der
       Freigabe und 4K: Sie setzen die Grenzen und koennten die eigene
       jederzeit heraufsetzen. Bliebe sie bestehen, waere es eine Huerde, die
       genau eine Person aufhaelt: die, die sie gerade wegklicken kann.
    2. Traegt das Konto etwas Eigenes, gilt das - ``UNBEGRENZT`` heisst
       ausdruecklich ohne Grenze, die **0** ausdruecklich "darf nichts".
    3. Sonst der Standardwert des Hauses; ist auch der leer, ist niemand
       begrenzt.
    """
    if user.is_admin:
        return None
    eigen = (
        user.quota_movies_limit if media_type == MediaType.movie else user.quota_series_limit
    )
    if eigen is not None:
        return None if eigen == UNBEGRENZT else max(0, eigen)
    vorgabe = (
        settings.quota_default_movies
        if media_type == MediaType.movie
        else settings.quota_default_series
    )
    return None if vorgabe is None else max(0, vorgabe)


def counting_start(user: User, settings: AppSettings) -> datetime:
    """Ab wann der Verbrauch zaehlt.

    Normalerweise der Beginn des Zeitraums. Hat der Admin das Kontingent von
    Hand zurueckgesetzt und liegt das *innerhalb* des laufenden Zeitraums,
    zaehlt es ab da - eine Ruecksetzung aus einem frueheren Zeitraum ist durch
    den Wechsel ohnehin schon ueberholt.

    Der Zeitraum kommt seit 0.20 aus den Einstellungen und gilt fuer das ganze
    Haus; ``User.quota_period`` wird nicht mehr gelesen.
    """
    start = period_start(settings.quota_period)
    if user.quota_reset_at is not None and user.quota_reset_at > start:
        return user.quota_reset_at
    return start


def state_for(
    db: Session, user: User, media_type: MediaType, settings: AppSettings
) -> QuotaState:
    limit = _limit_for(user, media_type, settings)
    start = counting_start(user, settings)

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
            else period_end(settings.quota_period, period_start(settings.quota_period))
        ),
        period=settings.quota_period,
    )


def uebersichten(
    db: Session, konten: list[User], settings: AppSettings
) -> dict[int, dict[str, QuotaState]]:
    """Der Stand mehrerer Konten - gruppiert gezaehlt statt zweimal je Konto.

    Die Benutzerliste rief ``state_for`` zweimal je Konto; das waren zwei
    Zaehl-Abfragen je Zeile. Hier zaehlt **eine** gruppierte Abfrage je
    Zaehlbeginn - und der Zaehlbeginn ist fuer fast alle derselbe. Nur ein
    von Hand zurueckgesetztes Konto (``quota_reset_at``) bildet eine eigene
    Gruppe, denn ein globaler Beginn wuerde genau dessen Zahlen verfaelschen.
    """
    gruppen: dict[datetime, list[int]] = {}
    for user in konten:
        gruppen.setdefault(counting_start(user, settings), []).append(user.id)

    zaehler: dict[tuple[int, MediaType], int] = {}
    for start, kennungen in gruppen.items():
        # In Scheiben wegen der SQLite-Parametergrenze (siehe ``db.scheiben``).
        # Die Scheiben einer Gruppe sind disjunkt, jedes Konto liefert seine
        # Zahlen also aus genau einer Abfrage - das Zusammenlegen per
        # Zuweisung bleibt richtig.
        for scheibe in scheiben(kennungen):
            for user_id, media_type, anzahl in db.execute(
                select(
                    MediaRequest.user_id,
                    MediaRequest.media_type,
                    func.count(MediaRequest.id),
                )
                .where(
                    MediaRequest.user_id.in_(scheibe),
                    MediaRequest.status.in_(COUNTED_STATUSES),
                    MediaRequest.requested_at >= start,
                )
                .group_by(MediaRequest.user_id, MediaRequest.media_type)
            ):
                zaehler[(user_id, media_type)] = int(anzahl)

    # Der naechste automatische Wechsel richtet sich nach dem Kalender,
    # nicht nach einer Ruecksetzung von Hand.
    ende = period_end(settings.quota_period, period_start(settings.quota_period))

    ergebnis: dict[int, dict[str, QuotaState]] = {}
    for user in konten:
        staende: dict[str, QuotaState] = {}
        for schluessel, media_type in (("movie", MediaType.movie), ("tv", MediaType.tv)):
            limit = _limit_for(user, media_type, settings)
            staende[schluessel] = QuotaState(
                limit=limit,
                used=zaehler.get((user.id, media_type), 0),
                resets_at=None if limit is None else ende,
                period=settings.quota_period,
            )
        ergebnis[user.id] = staende
    return ergebnis


def overview(
    db: Session, user: User, settings: AppSettings
) -> dict[str, QuotaState]:
    """Der Stand eines Kontos - der Einzelfall der Sammelabfrage."""
    return uebersichten(db, [user], settings)[user.id]
