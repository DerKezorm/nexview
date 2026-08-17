"""Verbindet TMDB-Rohdaten, Zwischenspeicher und Demo-Daten zu fertigen Karten."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from ..mocks import demo_data
from ..models import MediaType
from ..schemas_media import (
    CastMember,
    CrewMember,
    EpisodeInfo,
    Genre,
    MediaDetail,
    MediaItem,
    MediaPage,
    NamedRef,
    PersonCredit,
    PersonDetail,
    SeasonDetail,
    SeasonInfo,
    Trailer,
)
from . import cache
from .filters import DiscoverFilters
from .settings_service import AppSettings
from .tmdb import (
    BACKDROP_SIZE,
    PROFILE_SIZE,
    STILL_SIZE,
    TmdbClient,
    TmdbError,
    extract_certification,
    extract_runtime,
    extract_tvdb_id,
    image_url,
)

PAGE_SIZE = 20

# Fassung der ausfuehrlichen Detailabfrage.
#
# Muss hochgezaehlt werden, sobald sich aendert, *was* bei TMDB geholt wird -
# etwa wenn Videos oder Schlagworte dazukommen. Ohne das liefe der
# Zwischenspeicher tagelang weiter mit der alten, unvollstaendigen Antwort, und
# das neue Feld bliebe leer, ohne dass man den Grund saehe.
DETAIL_SHAPE = "v2"

SORT_OPTIONS = {
    "newest": {"movie": "primary_release_date.desc", "tv": "first_air_date.desc"},
    "rating": {"movie": "vote_average.desc", "tv": "vote_average.desc"},
    "popular": {"movie": "popularity.desc", "tv": "popularity.desc"},
}


def _client(settings: AppSettings, region: str | None = None) -> TmdbClient:
    return TmdbClient(
        api_key=settings.tmdb_api_key,
        language=settings.default_language,
        region=region or settings.default_region,
    )


# --- Genres ----------------------------------------------------------------


async def genre_list(db: Session, settings: AppSettings, media_type: str) -> list[Genre]:
    if settings.use_demo_data:
        return [Genre(id=gid, name=name) for gid, name in demo_data.demo_genres(media_type)]

    async def fetch() -> dict[str, str]:
        raw = await _client(settings).genres(media_type)
        return {str(key): value for key, value in raw.items()}

    data = await cache.cached(
        db, f"genres:{media_type}:{settings.default_language}", cache.GENRE_TTL, fetch
    )
    return [Genre(id=int(gid), name=name) for gid, name in data.items()]


async def _genre_map(db: Session, settings: AppSettings, media_type: str) -> dict[int, str]:
    return {genre.id: genre.name for genre in await genre_list(db, settings, media_type)}


# --- Umwandlung TMDB -> MediaItem ------------------------------------------


def _base_item(raw: dict[str, Any], media_type: str, genres: dict[int, str]) -> MediaItem:
    is_movie = media_type == "movie"
    return MediaItem(
        media_type=MediaType(media_type),
        tmdb_id=raw["id"],
        title=(raw.get("title") if is_movie else raw.get("name")) or "",
        original_title=raw.get("original_title") if is_movie else raw.get("original_name"),
        overview=raw.get("overview") or "",
        poster_url=image_url(raw.get("poster_path")),
        backdrop_url=image_url(raw.get("backdrop_path"), BACKDROP_SIZE),
        release_date=(raw.get("release_date") if is_movie else raw.get("first_air_date")) or None,
        vote_average=round(float(raw.get("vote_average") or 0), 1),
        vote_count=int(raw.get("vote_count") or 0),
        genres=[genres[gid] for gid in raw.get("genre_ids", []) if gid in genres],
        original_language=raw.get("original_language"),
    )


def _seasons(detail: dict[str, Any]) -> list[SeasonInfo]:
    """Staffelliste aus den TMDB-Detaildaten.

    Staffel 0 sind die Specials - die bleiben draussen: sie als "Staffel 0"
    zur Auswahl zu stellen, verwirrt mehr als es nutzt. Staffeln ganz ohne
    Folgen sind meist erst angekuendigt und waeren nicht ladbar.
    """
    gefunden = []
    for eintrag in detail.get("seasons") or []:
        nummer = eintrag.get("season_number")
        folgen = int(eintrag.get("episode_count") or 0)
        if not isinstance(nummer, int) or nummer < 1 or folgen < 1:
            continue
        gefunden.append(
            SeasonInfo(
                season_number=nummer,
                name=eintrag.get("name") or f"Staffel {nummer}",
                episode_count=folgen,
                air_date=eintrag.get("air_date") or None,
            )
        )
    return sorted(gefunden, key=lambda s: s.season_number)


def _enrich(
    item: MediaItem, detail: dict[str, Any], media_type: str, region: str, *, mit_staffeln: bool = False
) -> MediaItem:
    """Laufzeit, Altersfreigabe, Genres und TVDB-Id aus den Detaildaten ergaenzen."""
    detail_genres = [genre["name"] for genre in detail.get("genres", []) if genre.get("name")]

    return item.model_copy(
        update={
            "genres": detail_genres or item.genres,
            "runtime_minutes": extract_runtime(detail, media_type),
            "certification": extract_certification(detail, media_type, region),
            "tvdb_id": extract_tvdb_id(detail) if media_type == "tv" else None,
            "overview": item.overview or (detail.get("overview") or ""),
            "seasons": _seasons(detail) if (mit_staffeln and media_type == "tv") else [],
        }
    )


async def _to_items(
    db: Session,
    settings: AppSettings,
    media_type: str,
    results: list[dict[str, Any]],
    region: str,
) -> list[MediaItem]:
    """Listeneintraege in Karten umwandeln und mit Detaildaten anreichern.

    TMDB liefert in Listen weder Laufzeit noch Altersfreigabe. Die Details
    werden deshalb parallel nachgeladen - und einzeln lange zwischengespeichert,
    weil sie sich praktisch nicht mehr aendern.
    """
    genres = await _genre_map(db, settings, media_type)
    items = [_base_item(raw, media_type, genres) for raw in results if raw.get("id")]

    missing = [
        item.tmdb_id
        for item in items
        if cache.read(db, f"detail:{media_type}:{item.tmdb_id}:{region}") is None
    ]
    if missing:
        fetched = await _client(settings, region).details(media_type, missing)
        for tmdb_id, detail in fetched.items():
            cache.write(db, f"detail:{media_type}:{tmdb_id}:{region}", detail, cache.DETAIL_TTL)

    enriched: list[MediaItem] = []
    for item in items:
        detail = cache.read(db, f"detail:{media_type}:{item.tmdb_id}:{region}")
        enriched.append(_enrich(item, detail, media_type, region) if detail else item)
    return enriched


# --- Demo-Modus ------------------------------------------------------------


def _demo_page(media_type: str, filters: DiscoverFilters) -> MediaPage:
    items = demo_data.demo_items(media_type)
    genres = dict(demo_data.demo_genres(media_type))

    if filters.date_from:
        items = [i for i in items if (i.release_date or "") >= filters.date_from]
    if filters.date_to:
        items = [i for i in items if (i.release_date or "") <= filters.date_to]
    if filters.language:
        items = [i for i in items if i.original_language == filters.language]
    if filters.genre_id is not None:
        wanted = genres.get(filters.genre_id)
        items = [i for i in items if wanted and wanted in i.genres]
    if filters.min_runtime is not None:
        items = [i for i in items if (i.runtime_minutes or 0) >= filters.min_runtime]
    if filters.min_rating is not None:
        items = [i for i in items if i.vote_average >= filters.min_rating]
    if filters.hide_unrated:
        items = [i for i in items if i.vote_count > 0]
    if filters.min_votes is not None:
        items = [i for i in items if i.vote_count >= filters.min_votes]

    if filters.sort == "rating":
        items.sort(key=lambda i: i.vote_average, reverse=True)
    elif filters.sort == "popular":
        items.sort(key=lambda i: i.vote_count, reverse=True)
    else:
        items.sort(key=lambda i: i.release_date or "", reverse=True)

    start = (filters.page - 1) * PAGE_SIZE
    window = items[start : start + PAGE_SIZE]
    total_pages = max(1, -(-len(items) // PAGE_SIZE))

    return MediaPage(
        page=filters.page,
        total_pages=total_pages,
        total_results=len(items),
        items=window,
        demo=True,
    )


def _demo_search(media_type: str, query: str, page: int) -> MediaPage:
    needle = query.casefold()
    items = [i for i in demo_data.demo_items(media_type) if needle in i.title.casefold()]
    start = (page - 1) * PAGE_SIZE
    return MediaPage(
        page=page,
        total_pages=max(1, -(-len(items) // PAGE_SIZE)),
        total_results=len(items),
        items=items[start : start + PAGE_SIZE],
        demo=True,
    )


# --- Oeffentliche Funktionen ------------------------------------------------


async def discover(
    db: Session, settings: AppSettings, media_type: str, filters: DiscoverFilters
) -> MediaPage:
    if settings.use_demo_data:
        return _demo_page(media_type, filters)

    region = filters.region or settings.default_region
    sort_by = SORT_OPTIONS.get(filters.sort, SORT_OPTIONS["newest"])[media_type]

    async def fetch() -> dict[str, Any]:
        return await _client(settings, region).discover(media_type, filters, sort_by)

    raw = await cache.cached(
        db, filters.cache_key(media_type, settings.default_language), cache.DISCOVER_TTL, fetch
    )

    return MediaPage(
        page=raw.get("page", filters.page),
        # TMDB liefert maximal 500 Seiten aus.
        total_pages=min(raw.get("total_pages", 1), 500),
        total_results=raw.get("total_results", 0),
        items=await _to_items(db, settings, media_type, raw.get("results", []), region),
    )


async def search(
    db: Session, settings: AppSettings, media_type: str, query: str, page: int = 1
) -> MediaPage:
    if settings.use_demo_data:
        return _demo_search(media_type, query, page)

    region = settings.default_region

    async def fetch() -> dict[str, Any]:
        return await _client(settings).search(media_type, query, page)

    key = f"search:{media_type}:{query.casefold()}:{page}:{settings.default_language}"
    raw = await cache.cached(db, key, cache.SEARCH_TTL, fetch)

    return MediaPage(
        page=raw.get("page", page),
        total_pages=min(raw.get("total_pages", 1), 500),
        total_results=raw.get("total_results", 0),
        items=await _to_items(db, settings, media_type, raw.get("results", []), region),
    )


async def detail(
    db: Session, settings: AppSettings, media_type: str, tmdb_id: int
) -> MediaItem:
    if settings.use_demo_data:
        for item in demo_data.demo_items(media_type):
            if item.tmdb_id == tmdb_id:
                return item
        raise TmdbError("Dieser Demo-Titel ist nicht vorhanden.", 404)

    region = settings.default_region

    async def fetch() -> dict[str, Any]:
        return await _client(settings).detail(media_type, tmdb_id)

    raw = await cache.cached(
        db,
        f"detail:{media_type}:{tmdb_id}:{region}:{settings.default_language}",
        cache.DETAIL_TTL,
        fetch,
    )

    genres = await _genre_map(db, settings, media_type)
    item = _base_item(raw, media_type, genres)
    # Nur hier mit Staffeln: die Detailansicht ist die einzige Stelle, an
    # der man eine Staffel auswaehlen kann.
    return _enrich(item, raw, media_type, region, mit_staffeln=True)


# --- Detailseite -------------------------------------------------------------
#
# Alles, was ueber die Kachel hinausgeht: Besetzung, Studios, Schlagworte,
# Empfehlungen - und bei Serien die Staffeln. Bewusst eine eigene Abfrage:
# in den Listen wuerde davon nichts dargestellt, aber jede Seite waere um ein
# Vielfaches groesser.

# Nur diese Rollen aus dem Stab. Der vollstaendige Stab hat bei grossen
# Produktionen dreistellig viele Eintraege, von denen niemand welche sucht.
INTERESSANTE_ROLLEN = ("Director", "Screenplay", "Writer", "Creator", "Story")

MAX_CAST = 24
MAX_RECOMMENDATIONS = 12
# Filmografie einer Person: nach Bekanntheit, sonst stehen oben lauter
# Kleinstauftritte, die niemand zuordnen kann.
MAX_PERSON_CREDITS = 40




def _cast(detail: dict[str, Any]) -> list[CastMember]:
    eintraege = (detail.get("credits") or {}).get("cast") or []
    return [
        CastMember(
            person_id=person["id"],
            name=person.get("name") or "",
            character=person.get("character") or "",
            photo_url=image_url(person.get("profile_path"), PROFILE_SIZE),
        )
        for person in eintraege[:MAX_CAST]
        if person.get("id")
    ]


def _crew(detail: dict[str, Any]) -> list[CrewMember]:
    """Regie und Drehbuch - jede Person nur einmal, auch bei Doppelrollen."""
    gesehen: set[tuple[int, str]] = set()
    stab: list[CrewMember] = []
    for person in (detail.get("credits") or {}).get("crew") or []:
        job = person.get("job") or ""
        kennung = person.get("id")
        if job not in INTERESSANTE_ROLLEN or not kennung:
            continue
        if (kennung, job) in gesehen:
            continue
        gesehen.add((kennung, job))
        stab.append(CrewMember(person_id=kennung, name=person.get("name") or "", job=job))
    return stab


def _serien_schoepfer(detail: dict[str, Any]) -> list[CrewMember]:
    """Bei Serien steht der Schoepfer nicht im Stab, sondern separat."""
    return [
        CrewMember(person_id=person["id"], name=person.get("name") or "", job="Creator")
        for person in detail.get("created_by") or []
        if person.get("id")
    ]


def _namen(eintraege: list[dict[str, Any]] | None, schluessel: str = "name") -> list[str]:
    return [eintrag[schluessel] for eintrag in eintraege or [] if eintrag.get(schluessel)]


def _referenzen(eintraege: list[dict[str, Any]] | None) -> list[NamedRef]:
    """Kennung und Name - die Kennung braucht es zum Weitersuchen."""
    return [
        NamedRef(id=eintrag["id"], name=eintrag["name"])
        for eintrag in eintraege or []
        if eintrag.get("id") and eintrag.get("name")
    ]


def _schlagworte(detail: dict[str, Any]) -> list[NamedRef]:
    """TMDB legt die Schlagworte je nach Medientyp unterschiedlich ab."""
    roh = detail.get("keywords") or {}
    return _referenzen(roh.get("keywords") or roh.get("results"))


def _trailer(detail: dict[str, Any], sprache: str) -> Trailer | None:
    """Den passendsten Trailer aussuchen.

    Reihenfolge der Vorlieben: erst ein richtiger Trailer statt eines Teasers,
    dann ein offizieller, dann einer in der eigenen Sprache. Angefragt wurden
    Deutsch, Englisch und sprachneutral - sonst haetten die meisten Titel
    ueberhaupt keinen.
    """
    videos = [
        video
        for video in (detail.get("videos") or {}).get("results") or []
        if video.get("site") == "YouTube" and video.get("key")
    ]
    if not videos:
        return None

    eigene = sprache.split("-")[0]

    def rang(video: dict[str, Any]) -> tuple[int, int, int]:
        art = video.get("type")
        return (
            0 if art == "Trailer" else 1 if art == "Teaser" else 2,
            0 if video.get("official") else 1,
            0 if video.get("iso_639_1") == eigene else 1,
        )

    bester = min(videos, key=rang)
    return Trailer(
        key=bester["key"],
        name=bester.get("name") or "",
        site="YouTube",
        language=bester.get("iso_639_1") or "",
    )


async def full_detail(
    db: Session, settings: AppSettings, media_type: str, tmdb_id: int
) -> MediaDetail:
    """Alles zu einem Titel - fuer die Detailseite.

    Eigener Zwischenspeicher-Schluessel: die ausfuehrliche Antwort enthaelt
    deutlich mehr als die schlanke, und beide unter demselben Schluessel
    abzulegen hiesse, dass die Listen je nach Zufall mal die eine und mal die
    andere Fassung bekaemen.
    """
    if settings.use_demo_data:
        for item in demo_data.demo_items(media_type):
            if item.tmdb_id == tmdb_id:
                return MediaDetail(**item.model_dump())
        raise TmdbError("Dieser Demo-Titel ist nicht vorhanden.", 404)

    region = settings.default_region

    async def fetch() -> dict[str, Any]:
        return await _client(settings).detail(media_type, tmdb_id, ausfuehrlich=True)

    raw = await cache.cached(
        db,
        f"full:{DETAIL_SHAPE}:{media_type}:{tmdb_id}:{region}:{settings.default_language}",
        cache.DETAIL_TTL,
        fetch,
    )

    genres = await _genre_map(db, settings, media_type)
    basis = _enrich(_base_item(raw, media_type, genres), raw, media_type, region, mit_staffeln=True)

    empfehlungen = [
        _base_item(eintrag, media_type, genres)
        for eintrag in (raw.get("recommendations") or {}).get("results") or []
        if eintrag.get("id")
    ][:MAX_RECOMMENDATIONS]

    ist_film = media_type == "movie"
    return MediaDetail(
        **basis.model_dump(),
        tagline=raw.get("tagline") or "",
        homepage=raw.get("homepage") or None,
        status_text=raw.get("status") or "",
        original_country=raw.get("origin_country") or [],
        spoken_languages=_namen(raw.get("spoken_languages"), "english_name"),
        # 0 heisst bei TMDB "unbekannt", nicht "kostenlos".
        budget=(raw.get("budget") or None) if ist_film else None,
        revenue=(raw.get("revenue") or None) if ist_film else None,
        studios=_referenzen(raw.get("production_companies")),
        keywords=_schlagworte(raw),
        trailer=_trailer(raw, settings.default_language),
        cast=_cast(raw),
        crew=_crew(raw) + ([] if ist_film else _serien_schoepfer(raw)),
        recommendations=empfehlungen,
        seasons_total=None if ist_film else raw.get("number_of_seasons"),
        episodes_total=None if ist_film else raw.get("number_of_episodes"),
        series_status="" if ist_film else (raw.get("status") or ""),
        networks=[] if ist_film else _referenzen(raw.get("networks")),
    )


async def season_detail(
    db: Session, settings: AppSettings, tmdb_id: int, season_number: int
) -> SeasonDetail:
    """Eine einzelne Staffel mit allen Folgen."""
    if settings.use_demo_data:
        return SeasonDetail(season_number=season_number, name=f"Staffel {season_number}")

    async def fetch() -> dict[str, Any]:
        return await _client(settings).season(tmdb_id, season_number)

    raw = await cache.cached(
        db,
        f"season:{tmdb_id}:{season_number}:{settings.default_language}",
        cache.DETAIL_TTL,
        fetch,
    )

    return SeasonDetail(
        season_number=raw.get("season_number", season_number),
        name=raw.get("name") or f"Staffel {season_number}",
        overview=raw.get("overview") or "",
        air_date=raw.get("air_date") or None,
        episodes=[
            EpisodeInfo(
                episode_number=folge.get("episode_number", 0),
                name=folge.get("name") or "",
                overview=folge.get("overview") or "",
                air_date=folge.get("air_date") or None,
                runtime_minutes=folge.get("runtime") or None,
                still_url=image_url(folge.get("still_path"), STILL_SIZE),
                vote_average=round(float(folge.get("vote_average") or 0), 1),
            )
            for folge in raw.get("episodes") or []
        ],
    )


async def person_detail(db: Session, settings: AppSettings, person_id: int) -> PersonDetail:
    """Eine Person samt Filmografie.

    Sortiert nach Bekanntheit: chronologisch stuenden oben lauter
    Kleinstauftritte, die niemand zuordnen kann.
    """
    if settings.use_demo_data:
        raise TmdbError("Im Demo-Modus gibt es keine Personendaten.", 404)

    async def fetch() -> dict[str, Any]:
        return await _client(settings).person(person_id)

    raw = await cache.cached(
        db, f"person:{person_id}:{settings.default_language}", cache.DETAIL_TTL, fetch
    )

    eintraege = (raw.get("combined_credits") or {}).get("cast") or []
    beste = sorted(eintraege, key=lambda e: float(e.get("popularity") or 0), reverse=True)

    credits: list[PersonCredit] = []
    gesehen: set[tuple[str, int]] = set()
    for eintrag in beste:
        art = eintrag.get("media_type")
        kennung = eintrag.get("id")
        if art not in ("movie", "tv") or not kennung or (art, kennung) in gesehen:
            continue
        gesehen.add((art, kennung))
        credits.append(
            PersonCredit(
                media_type=MediaType(art),
                tmdb_id=kennung,
                title=(eintrag.get("title") if art == "movie" else eintrag.get("name")) or "",
                character=eintrag.get("character") or "",
                poster_url=image_url(eintrag.get("poster_path")),
                release_date=(
                    eintrag.get("release_date")
                    if art == "movie"
                    else eintrag.get("first_air_date")
                )
                or None,
                vote_average=round(float(eintrag.get("vote_average") or 0), 1),
            )
        )
        if len(credits) >= MAX_PERSON_CREDITS:
            break

    return PersonDetail(
        person_id=raw.get("id", person_id),
        name=raw.get("name") or "",
        biography=raw.get("biography") or "",
        photo_url=image_url(raw.get("profile_path"), PROFILE_SIZE),
        birthday=raw.get("birthday") or None,
        deathday=raw.get("deathday") or None,
        place_of_birth=raw.get("place_of_birth") or None,
        known_for_department=raw.get("known_for_department") or "",
        credits=credits,
    )


# --- Nach Schlagwort oder Studio stoebern ------------------------------------


async def browse(
    db: Session,
    settings: AppSettings,
    media_type: str,
    *,
    keyword_id: int | None = None,
    company_id: int | None = None,
    page: int = 1,
) -> MediaPage:
    """Alle Titel zu einem Schlagwort oder Studio.

    Bewusst ohne Zeitraum und Mindestbewertung: wer auf "Zeitreise" klickt,
    will alles dazu sehen, nicht nur die Titel des letzten Monats.
    """
    if settings.use_demo_data:
        return MediaPage(page=1, total_pages=1, total_results=0, items=[], demo=True)

    params: dict[str, Any] = {}
    if keyword_id is not None:
        params["with_keywords"] = keyword_id
    if company_id is not None:
        params["with_companies"] = company_id
    if not params:
        return MediaPage(page=1, total_pages=1, total_results=0, items=[])

    region = settings.default_region

    async def fetch() -> dict[str, Any]:
        return await _client(settings).browse(media_type, params, page)

    schluessel = (
        f"browse:{media_type}:{keyword_id}:{company_id}:{page}:{settings.default_language}"
    )
    raw = await cache.cached(db, schluessel, cache.DISCOVER_TTL, fetch)

    return MediaPage(
        page=raw.get("page", page),
        total_pages=min(raw.get("total_pages", 1), 500),
        total_results=raw.get("total_results", 0),
        items=await _to_items(db, settings, media_type, raw.get("results", []), region),
    )


async def browse_label(
    db: Session, settings: AppSettings, *, keyword_id: int | None, company_id: int | None
) -> str:
    """Ueberschrift der Ergebnisseite.

    Wird beim direkten Aufruf der Adresse gebraucht - dann kennt die
    Oberflaeche den Namen ja noch nicht. Scheitert die Abfrage, bleibt die
    Ueberschrift leer statt die ganze Seite scheitern zu lassen.
    """
    if settings.use_demo_data:
        return ""

    try:
        if keyword_id is not None:
            roh = await cache.cached(
                db,
                f"keyword:{keyword_id}",
                cache.GENRE_TTL,
                lambda: _client(settings).keyword(keyword_id),
            )
        elif company_id is not None:
            roh = await cache.cached(
                db,
                f"company:{company_id}",
                cache.GENRE_TTL,
                lambda: _client(settings).company(company_id),
            )
        else:
            return ""
    except TmdbError:
        return ""

    return roh.get("name") or ""


async def suggestions(
    db: Session, settings: AppSettings, media_type: str, page: int = 1
) -> list[MediaItem]:
    """Beliebte Titel als Vorschlag für die Startseite.

    Bewusst über ``discover`` statt über die Trending-Liste: die umfasst nur
    100 Einträge, und bei einer gut gefüllten Bibliothek ist davon praktisch
    alles schon vorhanden - gemessen 0 brauchbare Vorschläge aus 100. Über
    ``discover`` steht dagegen der ganze Bestand zur Verfügung, nach Beliebtheit
    sortiert.

    Sortiert wird nach Beliebtheit; wer davon taugt, entscheidet der Aufrufer.
    Bewusst ohne Vorfilter auf Stimmen: kommende Filme haben naturgemäss kaum
    welche, und gerade die sind als Vorschlag interessant - sie können gar nicht
    schon in der Bibliothek liegen.
    """
    if settings.use_demo_data:
        return demo_data.demo_items(media_type)[:10] if page == 1 else []

    region = settings.default_region

    async def fetch() -> dict[str, Any]:
        return await _client(settings).browse(media_type, {}, page)

    raw = await cache.cached(
        db,
        f"suggest:{media_type}:{page}:{settings.default_language}",
        cache.DISCOVER_TTL,
        fetch,
    )
    return await _to_items(db, settings, media_type, raw.get("results", []), region)


# --- Kuratiert: Empfehlungen aus den Favoriten -------------------------------

# So viele Favoriten fliessen ein. Mehr braeuchte pro Aufruf mehr Abfragen,
# ohne das Ergebnis wesentlich zu verbessern - die haeufigsten Treffer stehen
# schon nach einer Handvoll fest.
MAX_FAVORITES_USED = 12


async def curated(
    db: Session, settings: AppSettings, media_type: str, favoriten: list[int]
) -> list[MediaItem]:
    """Empfehlungen auf Basis markierter Titel.

    Für jeden Favoriten holt TMDB seine Empfehlungsliste. Was in mehreren
    Listen auftaucht, passt am ehesten zum Geschmack - danach wird sortiert.
    Ein Titel, der nur einmal vorkommt, ist ein Zufallstreffer; einer, der bei
    vier Favoriten auftaucht, ist eine Aussage.

    Die Favoriten selbst fallen heraus - sie zu empfehlen waere sinnlos.
    """
    if not favoriten or settings.use_demo_data:
        return []

    ausgewaehlt = favoriten[:MAX_FAVORITES_USED]

    async def hole(tmdb_id: int) -> list[dict[str, Any]]:
        async def fetch() -> dict[str, Any]:
            return await _client(settings).recommendations(media_type, tmdb_id)

        try:
            roh = await cache.cached(
                db,
                f"recs:{media_type}:{tmdb_id}:{settings.default_language}",
                cache.DETAIL_TTL,
                fetch,
            )
        except TmdbError:
            return []
        return roh.get("results") or []

    listen = await asyncio.gather(*(hole(tmdb_id) for tmdb_id in ausgewaehlt))

    # Wie oft empfohlen, und wie beliebt - in dieser Reihenfolge.
    punkte: dict[int, int] = {}
    roh_nach_id: dict[int, dict[str, Any]] = {}
    for liste in listen:
        for eintrag in liste:
            kennung = eintrag.get("id")
            if not kennung or kennung in ausgewaehlt:
                continue
            punkte[kennung] = punkte.get(kennung, 0) + 1
            roh_nach_id.setdefault(kennung, eintrag)

    if not punkte:
        return []

    sortiert = sorted(
        roh_nach_id.values(),
        key=lambda e: (punkte[e["id"]], float(e.get("popularity") or 0)),
        reverse=True,
    )

    return await _to_items(db, settings, media_type, sortiert[:40], settings.default_region)
