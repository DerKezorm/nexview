"""Neuerscheinungen, Suche und Detailansicht - alles ueber TMDB."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Response, status

from ..deps import CurrentUser, DbSession
from ..mocks import demo_data
from ..models import MediaType
from ..schemas_media import ArrOptions, Genre, MediaItem, MediaPage
from ..services import blocklist, library, media, requests_service
from ..services.arr import ArrError
from ..services.filters import (
    KNOWN_TITLES_MIN_VOTES,
    MIN_FEATURE_RUNTIME,
    STUDIO_IDS,
    STUDIOS,
    DiscoverFilters,
)
from ..services.settings_service import for_user, load_settings
from ..services.tmdb import TmdbError

router = APIRouter(prefix="/api", tags=["discover"])

MediaTypePath = Annotated[Literal["movie", "tv"], Path()]

# Ein ISO-Datum wie 2026-08-16.
DatePattern = r"^\d{4}-\d{2}-\d{2}$"


def _http_error(error: TmdbError) -> HTTPException:
    """TMDB-Fehler in eine verstaendliche HTTP-Antwort uebersetzen."""
    if error.status_code == 404:
        code = status.HTTP_404_NOT_FOUND
    elif error.status_code == 401:
        # Der Key ist hinterlegt, wird aber abgelehnt - das muss der Admin richten.
        code = status.HTTP_502_BAD_GATEWAY
    elif error.status_code == 429:
        code = status.HTTP_429_TOO_MANY_REQUESTS
    else:
        code = status.HTTP_502_BAD_GATEWAY
    return HTTPException(status_code=code, detail=error.message)


async def _status_for(
    db, settings, media_type: str, items: list[MediaItem]
) -> tuple[list[MediaItem], str | None]:
    """Titeln ihren Zustand geben.

    Drei Quellen: was in Radarr/Sonarr liegt, was bei uns angefragt wurde, und
    die Sperrliste. Eine vorhandene Datei sticht die Anfrage - aber die Sperre
    sticht alles.

    Dass die Sperre ganz oben steht, ist Absicht: sie ist die Auskunft, auf die
    es ankommt. Stuende dort "noch nicht angefragt", waere der einzige Weg zur
    Wahrheit ein Klick auf den Einkaufswagen und eine Fehlermeldung. Und liegt
    der Titel schon in der Bibliothek, ist "gesperrt" fuer den Administrator
    die wichtigere Information - die Sperre entfernt ihn ja nicht, sie
    verhindert nur, dass er erneut angefragt wird.
    """
    result = await library.apply_status(settings, media_type, items)
    kennungen = [item.tmdb_id for item in result.items]

    own = requests_service.badges_for(db, MediaType(media_type), kennungen)
    gesperrt = blocklist.gesperrte_kennungen(db, MediaType(media_type), kennungen)

    merged = []
    for item in result.items:
        if item.tmdb_id in gesperrt:
            merged.append(item.model_copy(update={"status": blocklist.BADGE}))
        elif item.tmdb_id in own and item.status != "downloaded":
            merged.append(item.model_copy(update={"status": own[item.tmdb_id]}))
        else:
            merged.append(item)
    return merged, result.warning


async def _with_status(db, settings, media_type: str, page: MediaPage) -> MediaPage:
    items, warning = await _status_for(db, settings, media_type, page.items)
    return page.model_copy(update={"items": items, "arr_warning": warning})


@router.get("/studios", response_model=list[Genre])
def studios(user: CurrentUser) -> list[Genre]:
    """Auswahlliste bekannter Filmstudios (nur fuer Filme sinnvoll)."""
    return [Genre(id=studio_id, name=name) for studio_id, name in STUDIOS]


@router.get("/discover/{media_type}", response_model=MediaPage)
async def discover(
    media_type: MediaTypePath,
    user: CurrentUser,
    db: DbSession,
    date_from: Annotated[str | None, Query(pattern=DatePattern)] = None,
    date_to: Annotated[str | None, Query(pattern=DatePattern)] = None,
    language: Annotated[str | None, Query(max_length=5)] = None,
    region: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    genre_id: Annotated[int | None, Query(ge=0)] = None,
    sort: Annotated[Literal["newest", "rating", "popular"], Query()] = "newest",
    page: Annotated[int, Query(ge=1, le=500)] = 1,
    feature_length: Annotated[bool, Query()] = False,
    min_rating: Annotated[float | None, Query(ge=0, le=10)] = None,
    hide_unrated: Annotated[bool, Query()] = False,
    released_in_region: Annotated[bool, Query()] = False,
    studio_id: Annotated[int | None, Query(ge=1)] = None,
    known_only: Annotated[bool, Query()] = False,
) -> MediaPage:
    if studio_id is not None and studio_id not in STUDIO_IDS:
        raise HTTPException(status_code=422, detail="Unbekanntes Studio.")

    settings = for_user(load_settings(db), user)
    filters = DiscoverFilters(
        date_from=date_from,
        date_to=date_to,
        language=language,
        region=region.upper() if region else None,
        genre_id=genre_id,
        sort=sort,
        page=page,
        # Kurzfilme herausfiltern - sonst stehen bei "Neueste zuerst" oben
        # lauter 3-Minuten-Beitraege.
        min_runtime=MIN_FEATURE_RUNTIME if feature_length else None,
        min_rating=min_rating,
        hide_unrated=hide_unrated,
        # Ohne Untergrenze stehen bei "Neueste zuerst" fast nur namenlose
        # Kleinstproduktionen oben - gemessen 0 von 20 mit Beschreibung.
        min_votes=KNOWN_TITLES_MIN_VOTES if known_only else None,
        # Beides ergibt nur bei Filmen Sinn.
        released_in_region=released_in_region and media_type == "movie",
        studio_id=studio_id if media_type == "movie" else None,
    )

    try:
        result = await media.discover(db, settings, media_type, filters)
    except TmdbError as error:
        raise _http_error(error) from error
    return await _with_status(db, settings, media_type, result)


@router.get("/search/{media_type}", response_model=MediaPage)
async def search(
    media_type: MediaTypePath,
    user: CurrentUser,
    db: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=120)],
    page: Annotated[int, Query(ge=1, le=500)] = 1,
) -> MediaPage:
    settings = for_user(load_settings(db), user)
    try:
        result = await media.search(db, settings, media_type, q.strip(), page)
    except TmdbError as error:
        raise _http_error(error) from error
    return await _with_status(db, settings, media_type, result)


@router.get("/media/{media_type}/{tmdb_id}", response_model=MediaItem)
async def media_detail(
    media_type: MediaTypePath,
    tmdb_id: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    db: DbSession,
) -> MediaItem:
    settings = for_user(load_settings(db), user)
    try:
        item = await media.detail(db, settings, media_type, tmdb_id)
    except TmdbError as error:
        raise _http_error(error) from error

    items, _ = await _status_for(db, settings, media_type, [item])
    return items[0]


@router.get("/genres/{media_type}", response_model=list[Genre])
async def genres(media_type: MediaTypePath, user: CurrentUser, db: DbSession) -> list[Genre]:
    settings = for_user(load_settings(db), user)
    try:
        return await media.genre_list(db, settings, media_type)
    except TmdbError as error:
        raise _http_error(error) from error


@router.get("/arr/{media_type}/options", response_model=ArrOptions)
async def arr_options(media_type: MediaTypePath, user: CurrentUser, db: DbSession) -> ArrOptions:
    """Qualitaetsprofile und Zielordner - fuer die Auswahl vor dem Hinzufuegen.

    Fuer diesen Benutzer gesperrte Profile werden gar nicht erst
    ausgeliefert - er kann sie also nicht auswaehlen. Mitgeliefert wird auch
    die Vorauswahl: das vom Admin gesetzte Standardprofil, oder das erste
    erlaubte, falls der Standard fuer ihn gesperrt ist.
    """
    settings = load_settings(db)
    try:
        options = ArrOptions(**await library.options(settings, media_type))
    except ArrError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST if error.status_code is None else 502,
            detail=error.message,
        ) from error

    # Administratoren vergeben die Sperren selbst - fuer sie gelten sie nicht.
    # Sonst koennten sie ein Profil, das sie sich versehentlich gesperrt haben,
    # auch nicht mehr als Standard auswaehlen.
    gesperrt = [] if user.is_admin else user.blocked_profiles(MediaType(media_type))
    if gesperrt:
        options.quality_profiles = [p for p in options.quality_profiles if p.id not in gesperrt]

    erlaubt = {profil.id for profil in options.quality_profiles}
    standard = settings.default_profile_id(media_type)
    options.default_quality_profile_id = (
        standard
        if standard in erlaubt
        else (options.quality_profiles[0].id if options.quality_profiles else None)
    )

    # Zielordner: welcher gilt, und darf der Benutzer ihn aendern?
    # Administratoren stellen den Ordner hier ein und muessen ihn deshalb immer
    # auswaehlen koennen - sonst kaeme man an die eigene Vorgabe nicht heran.
    pfade = [ordner.path for ordner in options.root_folders]
    vorgabe = settings.default_root(media_type)
    options.default_root_folder = vorgabe if vorgabe in pfade else (pfade[0] if pfade else None)
    options.root_folder_choice = settings.root_folder_choice(media_type) or user.is_admin

    if not options.root_folder_choice:
        # Gar nicht erst mitliefern, was nicht zur Wahl steht.
        options.root_folders = [
            ordner for ordner in options.root_folders if ordner.path == options.default_root_folder
        ]
    return options


@router.get("/demo/poster/{media_type}/{tmdb_id}.svg", include_in_schema=False)
def demo_poster(
    media_type: MediaTypePath,
    tmdb_id: Annotated[int, Path(ge=1)],
    wide: Annotated[int, Query(ge=0, le=1)] = 0,
) -> Response:
    """Erzeugtes Platzhalter-Poster fuer den Demo-Modus.

    Bewusst ohne Anmeldung erreichbar: ``<img>``-Elemente schicken keinen
    Anmelde-Token mit. Der Inhalt ist ein reiner Farbverlauf mit Titel.
    """
    title = next(
        (item.title for item in demo_data.demo_items(media_type) if item.tmdb_id == tmdb_id),
        "Nexview",
    )
    return Response(
        content=demo_data.demo_poster(tmdb_id, title, wide=bool(wide)),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )
