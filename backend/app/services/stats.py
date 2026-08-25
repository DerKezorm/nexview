"""Zahlen fuer die Statistik-Seite.

Alles wird in wenigen Abfragen zusammengezaehlt statt pro Benutzer einzeln -
bei ein paar hundert Anfragen ist das ohnehin schnell, aber so bleibt die
Anzahl der Datenbankzugriffe unabhaengig von der Anzahl der Benutzer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MediaRequest, MediaType, RequestStatus, Role, User
from . import quota, storage
from .settings_service import load_settings

# Ab dieser Bewertung (und darunter) gilt eine Rueckmeldung als Beschwerde.
POOR_RATING = 2

# So weit reicht der Verlauf zurueck.
HISTORY_MONTHS = 6

# Zustaende, die einen erledigten Download bedeuten. "deleted" gehoert dazu:
# Der Download hat geklappt, nur wurde die Datei spaeter wieder entfernt. Ihn
# hier auszunehmen wuerde die Erfolgsquote ruckwirkend druecken, obwohl an der
# Anfrage nichts scheiterte.
DONE = (RequestStatus.downloaded, RequestStatus.deleted)


@dataclass
class UserStats:
    user_id: int
    username: str
    display_name: str | None
    avatar_url: str | None
    role: str
    total: int = 0
    movies: int = 0
    series: int = 0
    downloaded: int = 0
    pending: int = 0
    rejected: int = 0
    cancelled: int = 0
    failed: int = 0
    ratings: int = 0
    rating_sum: int = 0
    poor_ratings: int = 0
    # Der Speicherstand. Nur im GB-Betrieb gefuellt, sonst ``None``.
    #
    # ⚠️ **Anzahl oder Speicher, nie beides** - dieselbe Regel wie ueberall
    # sonst. Die Statistik zeigte bisher immer die Stueck-Kontingente, auch
    # wenn das Haus laengst auf GB umgestellt hatte: Darueber stand der
    # belegte Platz in Gigabyte, darunter "unbegrenzt" Stueck. Zwei
    # Waehrungen nebeneinander, von denen nur eine gilt.
    storage_used_bytes: int | None = None
    storage_limit_bytes: int | None = None
    quota_movie_used: int = 0
    quota_movie_limit: int | None = None
    quota_series_used: int = 0
    quota_series_limit: int | None = None

    @property
    def average_rating(self) -> float | None:
        return round(self.rating_sum / self.ratings, 1) if self.ratings else None

    @property
    def success_rate(self) -> float | None:
        """Anteil der Anfragen, die tatsaechlich als Download ankamen."""
        return round(100 * self.downloaded / self.total) if self.total else None


@dataclass
class Totals:
    requests: int = 0
    movies: int = 0
    series: int = 0
    downloaded: int = 0
    downloaded_movies: int = 0
    downloaded_series: int = 0
    pending: int = 0
    rejected: int = 0
    cancelled: int = 0
    failed: int = 0
    active_users: int = 0
    ratings: int = 0
    rating_sum: int = 0
    poor_ratings: int = 0
    unanswered_feedback: int = 0
    rating_distribution: dict[int, int] = field(default_factory=dict)
    last_request_at: datetime | None = None

    @property
    def average_rating(self) -> float | None:
        return round(self.rating_sum / self.ratings, 1) if self.ratings else None


def _monat(zeitpunkt: datetime) -> str:
    return zeitpunkt.strftime("%Y-%m")


def _letzte_monate(anzahl: int) -> list[str]:
    heute = datetime.now(timezone.utc).replace(tzinfo=None)
    monate: list[str] = []
    zeiger = heute.replace(day=1)
    for _ in range(anzahl):
        monate.append(_monat(zeiger))
        # Einen Tag vor den Ersten springen landet im Vormonat.
        zeiger = (zeiger - timedelta(days=1)).replace(day=1)
    return list(reversed(monate))


def collect(db: Session) -> dict:
    """Alle Zahlen der Statistik-Seite."""
    # ⚠️ Kinderkonten gehoeren nicht in die Nutzer-Aufstellung.
    #
    # Sie haben **kein eigenes Kontingent**: Gibt ein Elternteil einen
    # Kinderwunsch frei, laeuft die Anfrage ueber
    # ``requests_service.create_request`` auf **seinen** Namen - sein
    # Kontingent, sein Speicher, sein Freigabeweg. Ein Kind kann hier also gar
    # nichts angesammelt haben und stuende mit 0 GB in der Liste, waehrend es
    # gleichzeitig die prozentuale Aufteilung im Ring verwaessert.
    benutzer = {
        user.id: user
        for user in db.scalars(select(User).where(User.role != Role.child))
    }

    pro_benutzer: dict[int, UserStats] = {
        user.id: UserStats(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            role=user.role.value,
        )
        for user in benutzer.values()
    }

    gesamt = Totals()
    verlauf: dict[str, dict[str, int]] = defaultdict(lambda: {"movie": 0, "tv": 0})
    verteilung: dict[int, int] = {note: 0 for note in range(6)}
    beliebteste: dict[tuple[str, int], dict] = {}

    for request in db.scalars(select(MediaRequest)):
        eintrag = pro_benutzer.get(request.user_id)
        gesamt.requests += 1

        if gesamt.last_request_at is None or request.requested_at > gesamt.last_request_at:
            gesamt.last_request_at = request.requested_at

        ist_film = request.media_type == MediaType.movie
        if ist_film:
            gesamt.movies += 1
        else:
            gesamt.series += 1

        zustand = request.status
        if zustand in DONE:
            gesamt.downloaded += 1
            if ist_film:
                gesamt.downloaded_movies += 1
            else:
                gesamt.downloaded_series += 1
        elif zustand == RequestStatus.pending_approval:
            gesamt.pending += 1
        elif zustand == RequestStatus.rejected:
            gesamt.rejected += 1
        elif zustand == RequestStatus.cancelled:
            gesamt.cancelled += 1
        elif zustand == RequestStatus.failed:
            gesamt.failed += 1

        if eintrag is not None:
            eintrag.total += 1
            if ist_film:
                eintrag.movies += 1
            else:
                eintrag.series += 1
            if zustand in DONE:
                eintrag.downloaded += 1
            elif zustand == RequestStatus.pending_approval:
                eintrag.pending += 1
            elif zustand == RequestStatus.rejected:
                eintrag.rejected += 1
            elif zustand == RequestStatus.cancelled:
                eintrag.cancelled += 1
            elif zustand == RequestStatus.failed:
                eintrag.failed += 1

        # ⚠️ **Veraltete Bewertungen zaehlen nicht mit.**
        #
        # Diese Seite beantwortet "wie zufrieden sind die Leute mit dem, was
        # hier liegt". Eine Bewertung, die einer Datei galt, die Radarr
        # inzwischen durch eine bessere ersetzt hat, verfaelscht genau diese
        # Antwort - ein "war schlecht" ueber etwas, das es so nicht mehr gibt.
        # An der Anfrage bleibt sie stehen, hier faellt sie heraus.
        if request.rating is not None and not request.rating_outdated:
            gesamt.ratings += 1
            gesamt.rating_sum += request.rating
            verteilung[request.rating] = verteilung.get(request.rating, 0) + 1
            if request.rating <= POOR_RATING:
                gesamt.poor_ratings += 1
            if request.feedback and not request.feedback_reply:
                gesamt.unanswered_feedback += 1
            if eintrag is not None:
                eintrag.ratings += 1
                eintrag.rating_sum += request.rating
                if request.rating <= POOR_RATING:
                    eintrag.poor_ratings += 1

        verlauf[_monat(request.requested_at)]["movie" if ist_film else "tv"] += 1

        # Welche Titel wurden von mehreren Leuten angefragt?
        schluessel = (request.media_type.value, request.tmdb_id)
        vorhanden = beliebteste.setdefault(
            schluessel,
            {
                "media_type": request.media_type.value,
                "tmdb_id": request.tmdb_id,
                "title": request.title,
                "poster_path": request.poster_path,
                "count": 0,
            },
        )
        vorhanden["count"] += 1

    gesamt.rating_distribution = verteilung
    gesamt.active_users = sum(1 for eintrag in pro_benutzer.values() if eintrag.total > 0)

    # Kontingent-Auslastung im laufenden Zeitraum.
    for eintrag in pro_benutzer.values():
        stand = quota.overview(db, benutzer[eintrag.user_id])
        eintrag.quota_movie_used = stand["movie"].used
        eintrag.quota_movie_limit = stand["movie"].limit
        eintrag.quota_series_used = stand["tv"].used
        eintrag.quota_series_limit = stand["tv"].limit

    # Im GB-Betrieb zusaetzlich der Speicherstand. Die Stueck-Zahlen bleiben
    # berechnet - sie kosten nichts und die Oberflaeche entscheidet, welche
    # Waehrung sie zeigt.
    einstellungen = load_settings(db)
    if einstellungen.storage_enabled:
        belegt = {
            user_id: stand.used_bytes
            for user_id, stand in storage.verteilung(db)
            if user_id is not None
        }
        for eintrag in pro_benutzer.values():
            person = benutzer[eintrag.user_id]
            eintrag.storage_used_bytes = belegt.get(eintrag.user_id, 0)
            eintrag.storage_limit_bytes = storage.grenze_in_bytes(person, einstellungen)

    monate = _letzte_monate(HISTORY_MONTHS)
    return {
        "totals": gesamt,
        "users": sorted(pro_benutzer.values(), key=lambda e: (-e.total, e.username)),
        "history": [
            {"month": monat, "movies": verlauf[monat]["movie"], "series": verlauf[monat]["tv"]}
            for monat in monate
        ],
        "most_requested": sorted(
            (eintrag for eintrag in beliebteste.values() if eintrag["count"] > 1),
            key=lambda eintrag: -eintrag["count"],
        )[:5],
    }
