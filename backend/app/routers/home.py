"""Startseite: was zuletzt in der Bibliothek gelandet ist."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import CurrentUser, DbSession
from ..models import Favorite, MediaRequest, MediaType, RequestStatus
from ..schemas_media import MediaItem
from ..services import library, media, requests_service
from ..services.settings_service import for_user, load_settings
from ..services.tmdb import image_url

router = APIRouter(prefix="/api/home", tags=["home"])

logger = logging.getLogger("nexview.home")

# So viele Titel zeigt die Startseite hoechstens.
LIMIT = 12


class RecentItem(BaseModel):
    request_id: int
    media_type: MediaType
    tmdb_id: int
    title: str
    overview: str = ""
    poster_url: str | None = None
    backdrop_url: str | None = None
    release_date: str | None = None
    vote_average: float = 0.0
    runtime_minutes: int | None = None
    genres: list[str] = []
    completed_at: datetime | None = None
    requested_by: str
    requester_avatar: str | None = None


@router.get("/recent", response_model=list[RecentItem])
async def recent_downloads(user: CurrentUser, db: DbSession) -> list[RecentItem]:
    """Die zuletzt fertig geladenen Titel - fuer alle sichtbar.

    Die Details kommen von TMDB (Handlung, Hintergrundbild). Klappt das nicht,
    bleiben Titel und Poster aus der eigenen Datenbank uebrig - die Startseite
    ist dann schlichter, aber nicht leer.
    """
    anfragen = list(
        db.scalars(
            select(MediaRequest)
            .options(selectinload(MediaRequest.user))
            .where(MediaRequest.status == RequestStatus.downloaded)
            .order_by(
                MediaRequest.completed_at.desc().nullslast(),
                MediaRequest.requested_at.desc(),
            )
            .limit(LIMIT)
        )
    )
    if not anfragen:
        return []

    settings = for_user(load_settings(db), user)

    async def hole(request: MediaRequest):
        try:
            return await media.detail(db, settings, request.media_type.value, request.tmdb_id)
        except Exception as fehler:  # noqa: BLE001 - die Startseite darf nie scheitern
            logger.warning("Could not load details for %r: %s", request.title, fehler)
            return None

    details = await asyncio.gather(*(hole(request) for request in anfragen))

    eintraege: list[RecentItem] = []
    for request, item in zip(anfragen, details, strict=True):
        eintraege.append(
            RecentItem(
                request_id=request.id,
                media_type=request.media_type,
                tmdb_id=request.tmdb_id,
                title=item.title if item else request.title,
                overview=item.overview if item else "",
                poster_url=(item.poster_url if item else None) or image_url(request.poster_path),
                backdrop_url=item.backdrop_url if item else None,
                release_date=(item.release_date if item else None) or request.release_date,
                vote_average=item.vote_average if item else 0.0,
                runtime_minutes=item.runtime_minutes if item else None,
                genres=item.genres if item else [],
                completed_at=request.completed_at,
                requested_by=request.user.display_name or request.user.username,
                requester_avatar=request.user.avatar_url,
            )
        )
    return eintraege


# So viele Vorschlaege zeigt der Bereich "Aktuell beliebt" hoechstens.
TRENDING_LIMIT = 18
# So viele TMDB-Seiten werden dafuer hoechstens durchgesehen.
#
# Klingt viel, ist aber noetig: bei einer gut gefuellten Bibliothek faellt der
# grosse Teil der beliebten Titel weg, weil er laengst vorhanden ist - gemessen
# etwa ein brauchbarer Vorschlag pro Seite. Die Seiten werden nur geholt,
# solange noch etwas fehlt, und liegen danach im Zwischenspeicher.
TRENDING_PAGES = 12
# So viele kuratierte Empfehlungen zeigt die Startseite hoechstens.
CURATED_LIMIT = 12
# Ab wie vielen Stimmen ein *erschienener* Titel als bekannt genug gilt.
SUGGESTION_MIN_VOTES = 200



@router.get("/trending", response_model=list[MediaItem])
async def trending(user: CurrentUser, db: DbSession) -> list[MediaItem]:
    """Was gerade gefragt ist - ohne das, was ohnehin schon da ist.

    Bewusst gefiltert: ein Vorschlag, den man weder anfragen kann noch will,
    weil er längst in der Bibliothek liegt, ist kein Vorschlag. Übrig bleiben
    nur Titel, die sich mit einem Klick bestellen lassen.

    Fällt TMDB oder der Abgleich mit Radarr/Sonarr aus, kommt eine leere Liste
    zurück - der Bereich verschwindet dann einfach, statt die ganze Startseite
    scheitern zu lassen.
    """
    settings = for_user(load_settings(db), user)

    gesammelt: list[MediaItem] = []
    gesehen: set[int] = set()

    # Seitenweise nachladen, bis genug uebrig bleibt. Bei einer gut gefuellten
    # Bibliothek fallen fast alle Vorschlaege durch den Filter - mit nur einer
    # Seite stuenden dann zwei Titel da und der Bereich saehe kaputt aus.
    for seite in range(1, TRENDING_PAGES + 1):
        try:
            kandidaten = await media.suggestions(db, settings, "movie", page=seite)
        except Exception as fehler:  # noqa: BLE001 - die Startseite darf nie scheitern
            logger.warning("Trending nicht abrufbar: %s", fehler)
            break

        if not kandidaten:
            break

        try:
            kandidaten = (await library.apply_status(settings, "movie", kandidaten)).items
        except Exception as fehler:  # noqa: BLE001
            logger.info("Trending ohne Bibliotheksabgleich: %s", fehler)

        # Auch die eigenen offenen Anfragen zaehlen als "schon erledigt".
        eigene = requests_service.badges_for(
            db, MediaType.movie, [eintrag.tmdb_id for eintrag in kandidaten]
        )

        heute = datetime.now().strftime("%Y-%m-%d")

        for eintrag in kandidaten:
            if eintrag.status != "not_requested" or eintrag.tmdb_id in eigene:
                continue
            if eintrag.tmdb_id in gesehen:
                continue
            # Zwei Regeln, je nachdem ob der Titel schon draussen ist:
            #
            # * Erschienen: nur, wenn ihn genug Leute bewertet haben. Sonst
            #   stuenden hier regionale Kleinstproduktionen, die niemand kennt.
            # * Kommend: immer willkommen - Stimmen kann so ein Film noch gar
            #   nicht haben, und in die Beliebtheitsliste kommt er nur, wenn
            #   viele auf ihn warten. Wann er anlaeuft, steht dann gross dabei.
            kommt_noch = bool(eintrag.release_date) and eintrag.release_date > heute
            if not eintrag.release_date:
                continue
            if not kommt_noch and eintrag.vote_count < SUGGESTION_MIN_VOTES:
                continue
            gesehen.add(eintrag.tmdb_id)
            gesammelt.append(eintrag)

        if len(gesammelt) >= TRENDING_LIMIT:
            break

    return gesammelt[:TRENDING_LIMIT]


class CuratedResult(BaseModel):
    """Empfehlungen samt der Frage, ob es ueberhaupt Favoriten gibt.

    Ohne diese Unterscheidung koennte die Oberflaeche nicht zwischen "noch
    nichts markiert" und "markiert, aber nichts Passendes gefunden" trennen -
    und beides braucht einen anderen Hinweis.
    """

    has_favorites: bool
    items: list[MediaItem]


@router.get("/curated", response_model=CuratedResult)
async def curated(user: CurrentUser, db: DbSession) -> CuratedResult:
    """Empfehlungen auf Basis der eigenen Favoriten - nur, was noch fehlt."""
    settings = for_user(load_settings(db), user)

    favoriten = list(
        db.scalars(
            select(Favorite.tmdb_id)
            .where(Favorite.user_id == user.id, Favorite.media_type == MediaType.movie)
            .order_by(Favorite.created_at.desc())
        )
    )
    if not favoriten:
        return CuratedResult(has_favorites=False, items=[])

    try:
        vorschlaege = await media.curated(db, settings, "movie", favoriten)
    except Exception as fehler:  # noqa: BLE001 - die Startseite darf nie scheitern
        logger.warning("Kuratierte Empfehlungen nicht abrufbar: %s", fehler)
        return CuratedResult(has_favorites=True, items=[])

    try:
        vorschlaege = (await library.apply_status(settings, "movie", vorschlaege)).items
    except Exception as fehler:  # noqa: BLE001
        logger.info("Kuratiert ohne Bibliotheksabgleich: %s", fehler)

    eigene = requests_service.badges_for(
        db, MediaType.movie, [eintrag.tmdb_id for eintrag in vorschlaege]
    )

    fehlend = [
        eintrag
        for eintrag in vorschlaege
        if eintrag.status == "not_requested" and eintrag.tmdb_id not in eigene
    ]
    return CuratedResult(has_favorites=True, items=fehlend[:CURATED_LIMIT])
