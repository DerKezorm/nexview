"""Startseite: was zuletzt in der Bibliothek gelandet ist."""

from __future__ import annotations

import asyncio
import logging
from itertools import zip_longest
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import CurrentUser, DbSession
from ..models import Favorite, FavoritePerson, MediaRequest, MediaType, RequestStatus
from ..schemas_media import MediaItem
from ..services import library, media, mediaserver_library, requests_service, uhd
from ..services.settings_service import for_user, load_settings

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


async def _noch_vorhanden(
    db: DbSession, settings, anfragen: list[MediaRequest]
) -> list[MediaRequest]:
    """Von den erledigten Anfragen nur die behalten, deren Datei es noch gibt.

    "Frisch geladen" ist eine Aussage ueber die Bibliothek, kein Verlauf: Wer
    einen Film wieder loescht, will ihn dort nicht weiter stehen sehen. Die
    Anfrage-Zeile bleibt auf "geladen" stehen - sie ist ja tatsaechlich einmal
    erfuellt worden - deshalb muss hier nachgesehen werden.

    Geprueft wird je Stufe gegen Radarr/Sonarr **und** den Media-Server: Wer
    einen Titel nach Erreichen der Wunschqualitaet aus Radarr entfernt, hat ihn
    weiterhin in Plex, und dann gehoert er hierher.

    Faellt eine Abfrage aus, gilt der Titel als vorhanden. Ein leerer Bereich
    waere die schlechtere Antwort auf ein Netzwerkproblem als ein Eintrag zu
    viel.
    """
    behalten: list[MediaRequest] = []
    # Nach Medienart und Stufe buendeln: Jede Kombination fragt ihre eigene
    # Instanz, und die Bibliothek wird dabei nur einmal geholt.
    gruppen: dict[tuple[MediaType, str], list[MediaRequest]] = {}
    for anfrage in anfragen:
        gruppen.setdefault((anfrage.media_type, anfrage.tier.value), []).append(anfrage)

    for (art, stufe), teil in gruppen.items():
        # Ohne eingerichtete Instanz gibt es keine Quelle, die "weg" sagen
        # koennte - dann bleibt alles stehen. Ohne diese Zeile verschwaende der
        # ganze Bereich bei jedem, der Nexview ohne Radarr/Sonarr betreibt.
        if not settings.arr_configured(art.value, stufe):
            behalten.extend(teil)
            continue

        # Kennung **und** Jahr muessen mit: Serien werden ueber die TVDB-Id
        # abgeglichen und sonst ueber Titel und Jahr. Ohne beides faende der
        # Abgleich keine einzige Serie - und der Bereich haette sie alle
        # stillschweigend weggelassen.
        kacheln = [
            MediaItem(
                media_type=art,
                tmdb_id=a.tmdb_id,
                tvdb_id=a.tvdb_id,
                title=a.title or "",
                release_date=a.release_date,
            )
            for a in teil
        ]
        try:
            ergebnis = await library.apply_status(settings, art.value, kacheln, stufe)
            vorhanden = {
                eintrag.tmdb_id
                for eintrag in ergebnis.items
                if eintrag.status != "not_requested"
            }
        except Exception as fehler:  # noqa: BLE001 - die Startseite darf nie scheitern
            logger.warning("Library check failed (%s/%s): %s", art.value, stufe, fehler)
            behalten.extend(teil)
            continue

        # Zweite Quelle: Was Radarr/Sonarr nicht mehr kennt, kann im
        # Media-Server liegen.
        offen = [k for k in kacheln if k.tmdb_id not in vorhanden]
        if offen:
            vorhanden |= mediaserver_library.vorhandene_kennungen(db, art, offen, stufe)

        behalten.extend(a for a in teil if a.tmdb_id in vorhanden)

    # Die Reihenfolge der Abfrage wiederherstellen - oben wurde gebuendelt.
    reihenfolge = {id(a): i for i, a in enumerate(anfragen)}
    return sorted(behalten, key=lambda a: reihenfolge[id(a)])


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
    anfragen = await _noch_vorhanden(db, settings, anfragen)
    if not anfragen:
        return []

    # Zwei verschiedene Arten von "keine Details":
    #
    # * **Altersbeschraenkung.** Der Eintrag muss ganz verschwinden. Genau hier
    #   waere sonst ein Leck: unten greift die Anzeige auf Titel und Poster aus
    #   Nexviews eigener Anfragetabelle zurueck, und der gesperrte Titel stuende
    #   trotzdem auf der Startseite.
    # * **Etwas ging schief** - TMDB langsam, Schluessel abgelaufen, Titel dort
    #   geloescht. Dann ist der gespeicherte Titel besser als eine leere
    #   Startseite. Am Statuscode allein laesst sich das nicht unterscheiden:
    #   beides ist ein 404.
    VERBERGEN = object()

    async def hole(request: MediaRequest):
        try:
            return await media.detail(db, settings, request.media_type.value, request.tmdb_id)
        except media.AgeRestricted:
            return VERBERGEN
        except Exception as fehler:  # noqa: BLE001 - die Startseite darf nie scheitern
            logger.warning("Could not load details for %r: %s", request.title, fehler)
            return None

    details = await asyncio.gather(*(hole(request) for request in anfragen))

    eintraege: list[RecentItem] = []
    for request, item in zip(anfragen, details, strict=True):
        if item is VERBERGEN:
            continue
        eintraege.append(
            RecentItem(
                request_id=request.id,
                media_type=request.media_type,
                tmdb_id=request.tmdb_id,
                title=item.title if item else request.title,
                overview=item.overview if item else "",
                poster_url=(item.poster_url if item else None) or request.poster_path,
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
            logger.warning("Trending not available: %s", fehler)
            break

        if not kandidaten:
            break

        try:
            kandidaten = (await library.apply_status(settings, "movie", kandidaten)).items
        except Exception as fehler:  # noqa: BLE001
            logger.warning("Trending without library match: %s", fehler)

        # Auch die eigenen offenen Anfragen zaehlen als "schon erledigt".
        eigene = requests_service.badges_for(
            db, MediaType.movie, [eintrag.tmdb_id for eintrag in kandidaten]
        )

        # Und die dritte Quelle: der Media-Server. Ohne sie stand ein Film,
        # dessen Eintrag aus Radarr entfernt wurde, der aber weiter in Plex
        # liegt, hier als Vorschlag - waehrend die Suche ihn laengst als
        # vorhanden zeigte. Zwei Seiten, zwei Wahrheiten; gemeldet an
        # "Backrooms". Dieselbe Stufen-Regel wie ueberall: Mit 4K-Instanz
        # zaehlt nur die Standard-Kopie, die 4K-Achse haengt am Ende dran.
        im_server = mediaserver_library.vorhandene_kennungen(
            db,
            MediaType.movie,
            [e for e in kandidaten if e.status == "not_requested"],
            "standard" if settings.arr_configured("movie", "uhd") else None,
        )

        heute = datetime.now().strftime("%Y-%m-%d")

        for eintrag in kandidaten:
            if eintrag.status != "not_requested" or eintrag.tmdb_id in eigene:
                continue
            if eintrag.tmdb_id in im_server:
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

    ergebnis = gesammelt[:TRENDING_LIMIT]
    # Erst ganz am Ende und nur fuer die uebrig gebliebenen Titel: Sonst
    # fragte die Startseite die 4K-Instanz nach Dutzenden Eintraegen, die sie
    # gleich darauf wegwirft.
    await uhd.anreichern(db, settings, "movie", ergebnis, user)
    return ergebnis


class CuratedResult(BaseModel):
    """Empfehlungen samt der Frage, ob es ueberhaupt Favoriten gibt.

    Ohne diese Unterscheidung koennte die Oberflaeche nicht zwischen "noch
    nichts markiert" und "markiert, aber nichts Passendes gefunden" trennen -
    und beides braucht einen anderen Hinweis.
    """

    has_favorites: bool
    items: list[MediaItem]


async def _kuratiert_fuer(
    db: DbSession,
    settings,
    media_type: MediaType,
    favoriten: list[int],
    personen: list[int],
    user: CurrentUser,
) -> list[MediaItem]:
    """Empfehlungen einer Medienart - schon gefiltert auf das, was noch fehlt."""
    if not favoriten and not personen:
        return []

    try:
        vorschlaege = await media.curated(
            db, settings, media_type.value, favoriten, personen
        )
    except Exception as fehler:  # noqa: BLE001 - die Startseite darf nie scheitern
        logger.warning("Curated recommendations (%s) not available: %s", media_type.value, fehler)
        return []

    try:
        vorschlaege = (await library.apply_status(settings, media_type.value, vorschlaege)).items
    except Exception as fehler:  # noqa: BLE001
        logger.warning("Curated recommendations without library match: %s", fehler)

    eigene = requests_service.badges_for(
        db, media_type, [eintrag.tmdb_id for eintrag in vorschlaege]
    )
    # Dritte Quelle Media-Server - siehe die Begruendung bei den Trending-
    # Vorschlaegen; hier fehlte sie genauso.
    im_server = mediaserver_library.vorhandene_kennungen(
        db,
        media_type,
        [e for e in vorschlaege if e.status == "not_requested"],
        "standard" if settings.arr_configured(media_type.value, "uhd") else None,
    )
    uebrig = [
        eintrag
        for eintrag in vorschlaege
        if eintrag.status == "not_requested"
        and eintrag.tmdb_id not in eigene
        and eintrag.tmdb_id not in im_server
    ]
    # Ohne diese Zeile stand auf der Startseite "Nicht angefragt" an einem
    # Film, den es in 4K laengst gibt - die zweite Achse fehlte hier ganz.
    await uhd.anreichern(db, settings, media_type.value, uebrig, user)
    return uebrig


@router.get("/curated", response_model=CuratedResult)
async def curated(user: CurrentUser, db: DbSession) -> CuratedResult:
    """Empfehlungen auf Basis der eigenen Favoriten - nur, was noch fehlt.

    **Filme und Serien.** Zuerst sah diese Auskunft nur Filme an: wer
    ausschliesslich Serien markiert hatte, bekam "noch nichts markiert" zu
    lesen, obwohl sein Herz an mehreren Serien hing. Das Herz gibt es an beidem,
    also muss auch beides zaehlen.
    """
    settings = for_user(load_settings(db), user)

    favoriten: dict[MediaType, list[int]] = {}
    for art in (MediaType.movie, MediaType.tv):
        favoriten[art] = list(
            db.scalars(
                select(Favorite.tmdb_id)
                .where(Favorite.user_id == user.id, Favorite.media_type == art)
                .order_by(Favorite.created_at.desc())
            )
        )

    # Gemerkte Personen speisen beide Medienarten - ein Schauspieler spielt in
    # Filmen wie in Serien.
    personen = list(
        db.scalars(
            select(FavoritePerson.person_id)
            .where(FavoritePerson.user_id == user.id)
            .order_by(FavoritePerson.created_at.desc())
        )
    )

    if not any(favoriten.values()) and not personen:
        return CuratedResult(has_favorites=False, items=[])

    filme, serien = await asyncio.gather(
        _kuratiert_fuer(db, settings, MediaType.movie, favoriten[MediaType.movie], personen, user),
        _kuratiert_fuer(db, settings, MediaType.tv, favoriten[MediaType.tv], personen, user),
    )

    # Abwechselnd zusammenlegen statt erst alle Filme, dann alle Serien: sonst
    # sieht jemand mit vielen Filmfavoriten nie eine Serie, weil die Liste
    # vorher voll ist.
    gemischt: list[MediaItem] = []
    for film, serie in zip_longest(filme, serien):
        if film is not None:
            gemischt.append(film)
        if serie is not None:
            gemischt.append(serie)

    return CuratedResult(has_favorites=True, items=gemischt[:CURATED_LIMIT])
