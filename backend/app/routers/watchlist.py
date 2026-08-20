"""Die eigene Merkliste ansehen - und daraus anfragen wie ueberall sonst.

Drei Endpunkte, mehr braucht es nicht:

* ``GET /api/watchlist/plex`` - die eigene Merkliste als Titel-Liste, mit
  denselben Abzeichen wie im Katalog.
* ``POST /api/watchlist/connect/start`` bzw. ``/poll`` - die einmalige
  Plex-Anmeldung fuer alle, die noch keinen persoenlichen Zugang hinterlegt
  haben (Konten von vor dieser Fassung, oder ein abgelaufener Zugang).

Angefragt wird **nicht** hier, sondern ueber ``/api/requests`` wie von jeder
anderen Kachel aus. Deshalb gibt es an dieser Stelle weder Kontingent- noch
Freigabe-Logik: Sie steckt dort, wo sie hingehoert, und zwar einmal.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ..deps import CurrentUser, DbSession
from ..models import utcnow
from ..schemas_media import MediaItem
from ..services import settings_service, watchlist
from ..services.mediaserver import MediaServerError
from ..services.mediaserver_accounts import KontoFehler
from ..services.watchlist import WatchlistFehler

# Bewusst aus dem Media-Server-Router uebernommen statt nachgebaut: Die
# Zugriffspruefung auf die Bibliothek darf es nur einmal im Code geben.
from .discover import _status_for
from .mediaserver import (
    ChallengeStarted,
    PollRequest,
    _anbieter_fehler,
    _fehler,
    _identitaet,
    _verbundener_server,
)
from ..services import mediaserver_accounts as konten

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

logger = logging.getLogger("nexview.watchlist")


class WatchlistAntwort(BaseModel):
    """Die Merkliste, wie die Oberflaeche sie braucht."""

    movies: list[MediaItem]
    series: list[MediaItem]
    # Titel, zu denen Plex keine TMDB-Nummer kennt. Sie lassen sich weder
    # anzeigen noch anfragen - die Zahl steht als ehrlicher Hinweis dabei,
    # statt dass sie stillschweigend fehlen.
    unmatched: int


class VerbindungsStand(BaseModel):
    """Stand der Plex-Anmeldung.

    ``status`` gehoert dazu, weil der Anmeldeweg in der Oberflaeche fuer alle
    Anbieter derselbe ist: Er fragt so lange nach, bis "ready" kommt.
    """

    # "pending" = beim Anbieter noch nicht bestaetigt, "ready" = fertig.
    status: str = "ready"
    connected: bool
    linked: bool


def _konflikt(fehler: WatchlistFehler) -> HTTPException:
    return HTTPException(
        status_code=fehler.status_code,
        detail={"code": fehler.code, "message": fehler.message},
    )


def _pruefe_eingeschaltet(settings) -> None:
    if not settings.watchlist_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "watchlist_disabled",
                "message": "Die Merkliste ist nicht freigeschaltet.",
            },
        )


@router.get("/status", response_model=VerbindungsStand)
def verbindung(user: CurrentUser) -> VerbindungsStand:
    """Liegt ein persoenlicher Plex-Zugang vor, und ist ein Konto verknuepft?"""
    return VerbindungsStand(
        connected=user.watchlist_connected, linked=user.mediaserver_linked
    )


@router.get("/plex", response_model=WatchlistAntwort)
async def plex_merkliste(db: DbSession, user: CurrentUser) -> WatchlistAntwort:
    settings = settings_service.load_settings(db)
    _pruefe_eingeschaltet(settings)

    try:
        filme, serien, ohne = await watchlist.lesen(db, settings, user)
    except WatchlistFehler as fehler:
        raise _konflikt(fehler) from fehler

    # Genau dieselben Abzeichen wie im Katalog - "bereits geladen",
    # "angefragt", "gesperrt", "gesehen" und die 4K-Achse. Ohne das waere die
    # Merkliste eine zweite Wahrheit neben der Entdecken-Seite.
    filme, _ = await _status_for(db, settings, "movie", filme, user)
    serien, _ = await _status_for(db, settings, "tv", serien, user)

    return WatchlistAntwort(movies=filme, series=serien, unmatched=ohne)


# --------------------------------------------------------------------------
# Einmalige Plex-Anmeldung
#
# Noetig fuer Konten, die vor dieser Fassung verknuepft wurden - damals wurde
# das Token nach der Pruefung verworfen -, und wenn Plex einen Zugang nicht
# mehr annimmt.
# --------------------------------------------------------------------------


@router.post("/connect/start", response_model=ChallengeStarted)
async def connect_start(db: DbSession, user: CurrentUser) -> ChallengeStarted:
    server, settings = _verbundener_server(db)
    _pruefe_eingeschaltet(settings)
    if not user.mediaserver_linked:
        raise _fehler(
            KontoFehler(
                "watchlist_not_linked",
                "Bitte zuerst das Plex-Konto verknüpfen - erst dann lässt sich "
                "die Merkliste lesen.",
                status_code=409,
            )
        )

    try:
        challenge = await server.begin_login()
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc

    poll_token = konten.start_challenge(
        db, settings.mediaserver_provider, challenge, user=user
    )
    return ChallengeStarted(
        poll_token=poll_token, code=challenge.code, auth_url=challenge.auth_url
    )


@router.post("/connect/poll", response_model=VerbindungsStand)
async def connect_poll(
    payload: PollRequest, db: DbSession, user: CurrentUser
) -> VerbindungsStand:
    """Nachsehen, ob bestaetigt wurde - und den Zugang hinterlegen.

    Es muss **dasselbe** Konto sein, das schon verknuepft ist: Sonst liesse
    sich eine fremde Merkliste anzeigen und auf das eigene Kontingent anfragen.
    """
    server, _ = _verbundener_server(db)
    try:
        eintrag, daten, konto = await _identitaet(
            db, server, payload.poll_token, erwarteter_benutzer=user.id
        )
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    if konto is None:
        return VerbindungsStand(
            status="pending", connected=False, linked=user.mediaserver_linked
        )

    if (
        konto.provider != user.mediaserver_provider
        or konto.account_id != user.mediaserver_account_id
    ):
        raise _fehler(
            KontoFehler(
                "watchlist_other_account",
                "Das ist ein anderes Konto als das mit deinem Profil verknüpfte. "
                "Bitte mit demselben Konto anmelden.",
                status_code=409,
            )
        )

    konten.merke_token(user, daten.get("token"))
    eintrag.used_at = utcnow().replace(tzinfo=None)
    db.commit()
    return VerbindungsStand(
        status="ready", connected=user.watchlist_connected, linked=True
    )
