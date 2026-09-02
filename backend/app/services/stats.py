"""Zahlen fuer die Statistik-Seite.

Alles wird in wenigen Abfragen zusammengezaehlt statt pro Benutzer einzeln -
bei ein paar hundert Anfragen ist das ohnehin schnell, aber so bleibt die
Anzahl der Datenbankzugriffe unabhaengig von der Anzahl der Benutzer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import MediaRequest, MediaType, RequestStatus, Role, TitleRating, User
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
    #: Wie lange eine Anfrage typischerweise auf ihre Entscheidung wartet.
    #:
    #: ⚠️ **Der Median, nicht der Durchschnitt.** Eine einzige Anfrage, die
    #: jemand ein halbes Jahr liegen liess, zoege den Durchschnitt so weit
    #: hoch, dass die Zahl nichts mehr ueber den Alltag sagt - und genau
    #: danach wird hier gefragt. Der Median beschreibt den mittleren Fall und
    #: laesst sich von einem Ausreisser nicht bewegen.
    #:
    #: ``None`` heisst: Es wurde noch nie etwas freigegeben.
    freigabe_median_stunden: float | None = None
    #: Wie lange die aelteste **noch offene** Anfrage schon wartet. Das ist die,
    #: bei der sich als Naechstes jemand meldet.
    freigabe_laengste_offen_stunden: float | None = None
    rating_distribution: dict[int, int] = field(default_factory=dict)
    last_request_at: datetime | None = None

    @property
    def average_rating(self) -> float | None:
        return round(self.rating_sum / self.ratings, 1) if self.ratings else None


def _monat(zeitpunkt: datetime) -> str:
    return zeitpunkt.strftime("%Y-%m")


def _letzte_monate(anzahl: int) -> list[str]:
    heute = datetime.now(UTC).replace(tzinfo=None)
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

    # --- Rueckmeldungen zur Qualitaet ------------------------------------
    #
    # Eigener Durchgang, weil sie seit 0.19 **am Titel** haengen und nicht mehr
    # an einer Anfrage: Bewerten darf jeder, der einen vorhandenen Titel
    # gesehen hat, nicht nur der Besteller.
    #
    # ⚠️ **Veraltete zaehlen nicht mit.** Diese Seite beantwortet "wie
    # zufrieden sind die Leute mit dem, was hier liegt". Ein Urteil, das einer
    # Datei galt, die Radarr inzwischen durch eine bessere ersetzt hat,
    # verfaelscht genau diese Antwort. Es bleibt gespeichert, faellt hier aber
    # heraus.
    for bewertung in db.scalars(
        select(TitleRating).where(TitleRating.outdated.is_(False))
    ):
        gesamt.ratings += 1
        gesamt.rating_sum += bewertung.rating
        verteilung[bewertung.rating] = verteilung.get(bewertung.rating, 0) + 1
        if bewertung.rating <= POOR_RATING:
            gesamt.poor_ratings += 1
        if bewertung.comment and not bewertung.reply:
            gesamt.unanswered_feedback += 1

        eintrag = pro_benutzer.get(bewertung.user_id)
        if eintrag is not None:
            eintrag.ratings += 1
            eintrag.rating_sum += bewertung.rating
            if bewertung.rating <= POOR_RATING:
                eintrag.poor_ratings += 1

    # --- Wie lange auf eine Freigabe gewartet wird ------------------------
    #
    # ⚠️ **Nur was wirklich freigegeben wurde.** Abgelehntes und Abgebrochenes
    # hat auch eine Entscheidung bekommen, aber die Frage hier lautet "wie
    # lange wartet jemand auf sein Ja" - und eine Ablehnung ist kein Ja.
    #
    # ⚠️ **Auto-Freigaben zaehlen nicht mit.** Wer freigeben darf, dessen
    # eigene Anfragen sind in derselben Sekunde durch; sie stehen mit null
    # Stunden drin und druecken den Median auf null, sobald der Administrator
    # selbst ein paar Titel bestellt. Gemessen wird ab einer Minute.
    wartezeiten = sorted(
        (r.approved_at - r.requested_at).total_seconds() / 3600
        for r in db.scalars(select(MediaRequest))
        if r.approved_at is not None
        and r.requested_at is not None
        and (r.approved_at - r.requested_at).total_seconds() >= 60
    )
    if wartezeiten:
        mitte = len(wartezeiten) // 2
        gesamt.freigabe_median_stunden = round(
            wartezeiten[mitte]
            if len(wartezeiten) % 2
            else (wartezeiten[mitte - 1] + wartezeiten[mitte]) / 2,
            1,
        )

    aelteste = db.scalar(
        select(func.min(MediaRequest.requested_at)).where(
            MediaRequest.status == RequestStatus.pending_approval
        )
    )
    if aelteste is not None:
        gesamt.freigabe_laengste_offen_stunden = round(
            (datetime.now(UTC).replace(tzinfo=None) - aelteste).total_seconds()
            / 3600,
            1,
        )

    gesamt.rating_distribution = verteilung
    gesamt.active_users = sum(1 for eintrag in pro_benutzer.values() if eintrag.total > 0)

    # Kontingent-Auslastung im laufenden Zeitraum - **beide Waehrungen**, weil
    # beide immer gelten. Die Oberflaeche entscheidet, welche sie zeigt.
    #
    # Gruppiert gezaehlt statt je Konto einzeln - siehe ``quota.uebersichten``.
    # ``overview`` in einer Schleife war exakt die Klasse Fehler, die diese
    # Datei laut ihrem eigenen Kopf vermeiden soll: eine Abfrage je Benutzer.
    einstellungen = load_settings(db)
    staende = quota.uebersichten(db, list(benutzer.values()), einstellungen)
    for eintrag in pro_benutzer.values():
        stand = staende[eintrag.user_id]
        eintrag.quota_movie_used = stand["movie"].used
        eintrag.quota_movie_limit = stand["movie"].limit
        eintrag.quota_series_used = stand["tv"].used
        eintrag.quota_series_limit = stand["tv"].limit

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
