"""Verbindet TMDB-Rohdaten, Zwischenspeicher und Demo-Daten zu fertigen Karten."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..mocks import demo_data
from ..models import MediaType
from ..schemas_media import Genre, MediaItem, MediaPage
from . import cache
from .filters import DiscoverFilters
from .settings_service import AppSettings
from .tmdb import (
    BACKDROP_SIZE,
    TmdbClient,
    TmdbError,
    extract_certification,
    extract_runtime,
    extract_tvdb_id,
    image_url,
)

PAGE_SIZE = 20

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


def _enrich(item: MediaItem, detail: dict[str, Any], media_type: str, region: str) -> MediaItem:
    """Laufzeit, Altersfreigabe, Genres und TVDB-Id aus den Detaildaten ergaenzen."""
    detail_genres = [genre["name"] for genre in detail.get("genres", []) if genre.get("name")]

    return item.model_copy(
        update={
            "genres": detail_genres or item.genres,
            "runtime_minutes": extract_runtime(detail, media_type),
            "certification": extract_certification(detail, media_type, region),
            "tvdb_id": extract_tvdb_id(detail) if media_type == "tv" else None,
            "overview": item.overview or (detail.get("overview") or ""),
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

    raw = await cache.cached(db, filters.cache_key(media_type), cache.DISCOVER_TTL, fetch)

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
        db, f"detail:{media_type}:{tmdb_id}:{region}", cache.DETAIL_TTL, fetch
    )

    genres = await _genre_map(db, settings, media_type)
    item = _base_item(raw, media_type, genres)
    return _enrich(item, raw, media_type, region)
