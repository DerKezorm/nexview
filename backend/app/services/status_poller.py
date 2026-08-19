"""Haelt den Zustand laufender Anfragen aktuell.

Radarr und Sonarr melden von sich aus nichts. Deshalb fragt Nexview in einem
Intervall nach, ob aus "wird gesucht" inzwischen "liegt da" geworden ist -
und benachrichtigt dann denjenigen, der den Titel angefragt hat.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    MediaRequest,
    MediaType,
    NotificationType,
    RequestStatus,
    User,
    utcnow,
)
from . import library, mail_outbox, mediaserver_library, mediaserver_watched, notify
from .arr import ArrError
from .settings_service import AppSettings, load_settings
from .sonarr import normalize_title

logger = logging.getLogger("nexview.poller")

# Zustaende, bei denen sich noch etwas tun kann.
WATCHED_STATUSES = (RequestStatus.approved, RequestStatus.searching)

# Nach einem Fehler nicht sofort wieder loslaufen.
ERROR_BACKOFF_SECONDS = 60


def _open_requests(db: Session) -> list[MediaRequest]:
    return list(
        db.scalars(select(MediaRequest).where(MediaRequest.status.in_(WATCHED_STATUSES)))
    )


async def check_once(db: Session, settings: AppSettings) -> int:
    """Einmal nachsehen. Gibt zurueck, wie viele Titel fertig geworden sind."""
    offen = _open_requests(db)
    if not offen:
        return 0

    braucht_filme = any(r.media_type == MediaType.movie for r in offen)
    braucht_serien = any(r.media_type == MediaType.tv for r in offen)

    filme: dict[int, object] = {}
    serien_nach_tvdb: dict[int, object] = {}
    serien_nach_titel: dict[str, object] = {}

    if braucht_filme and settings.radarr_configured:
        filme = await library.movie_library(settings)
    if braucht_serien and settings.sonarr_configured:
        serien_nach_tvdb, serien_nach_titel = await library.series_library(settings)

    fertig = 0
    for request in offen:
        if request.media_type == MediaType.movie:
            eintrag = filme.get(request.tmdb_id)
        else:
            eintrag = serien_nach_tvdb.get(request.tvdb_id) if request.tvdb_id else None
            if eintrag is None:
                eintrag = serien_nach_titel.get(normalize_title(request.title))

        request.last_checked_at = utcnow()
        if eintrag is None:
            continue

        # Ist der Titel inzwischen wirklich heruntergeladen?
        if getattr(eintrag, "has_file", False):
            request.status = RequestStatus.downloaded
            request.completed_at = utcnow()
            anfragender = db.get(User, request.user_id)
            if anfragender is not None:
                notify.create(
                    db,
                    user=anfragender,
                    kind=NotificationType.download_complete,
                    message_key="notifications.downloadComplete",
                    request=request,
                )
            fertig += 1
        elif request.status == RequestStatus.approved:
            # In Radarr/Sonarr angelegt, Datei fehlt noch.
            request.status = RequestStatus.searching

    db.commit()
    if fertig:
        logger.info("Status-Abgleich: %d Titel fertig geladen", fertig)
    return fertig


# Wie oft die Bibliothek des Media-Servers neu gelesen wird. Von Hand
# hineinkopierte Dateien sind die Ausnahme, nicht der Normalfall - stuendlich
# ist reichlich schnell und belastet den Server kaum.
BIBLIOTHEK_INTERVALL_SEKUNDEN = 3600
_bibliothek_zuletzt: float = 0.0


async def _bibliothek_vielleicht(db, settings) -> None:
    """Die Bibliothek des Media-Servers einlesen, wenn es an der Zeit ist.

    Faellt der Server aus, ist das kein Grund, den ganzen Durchgang scheitern
    zu lassen - der Rest des Abgleichs haengt nicht daran.
    """
    global _bibliothek_zuletzt
    if not settings.mediaserver_configured:
        return
    jetzt = time.monotonic()
    if jetzt - _bibliothek_zuletzt < BIBLIOTHEK_INTERVALL_SEKUNDEN:
        return
    _bibliothek_zuletzt = jetzt
    try:
        await mediaserver_library.refresh(db, settings)
        # Erst danach: Der Verlauf verweist auf Titel aus der Bibliothek und
        # laeuft ins Leere, solange die nicht eingelesen ist.
        await mediaserver_watched.refresh(db, settings)
    except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
        logger.exception("Media-Server konnte nicht abgeglichen werden")


async def run_forever(stop: asyncio.Event) -> None:
    """Hintergrundschleife - laeuft, bis die Anwendung beendet wird."""
    while not stop.is_set():
        wartezeit = 120
        try:
            with SessionLocal() as db:
                settings = load_settings(db)
                wartezeit = settings.poll_interval_seconds
                if settings.radarr_configured or settings.sonarr_configured:
                    await check_once(db, settings)

                # Die Bibliothek des Media-Servers seltener - sie aendert sich
                # kaum, und ein voller Durchlauf kostet bei ein paar tausend
                # Titeln spuerbar mehr als eine Statusabfrage.
                await _bibliothek_vielleicht(db, settings)

                # Erst danach: so gehen die Mails zu gerade fertig gewordenen
                # Downloads schon in diesem Durchgang raus statt erst im
                # naechsten.
                await mail_outbox.process(db, settings)
        except ArrError as error:
            # Radarr/Sonarr gerade nicht erreichbar - kein Grund zur Aufregung.
            logger.info("Status-Abgleich übersprungen: %s", error.message)
            wartezeit = max(wartezeit, ERROR_BACKOFF_SECONDS)
        except Exception:  # noqa: BLE001 - die Schleife darf nie sterben
            logger.exception("Status-Abgleich fehlgeschlagen")
            wartezeit = max(wartezeit, ERROR_BACKOFF_SECONDS)

        try:
            await asyncio.wait_for(stop.wait(), timeout=wartezeit)
        except TimeoutError:
            continue
