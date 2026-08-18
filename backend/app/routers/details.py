"""Ausfuehrliche Ansichten: ein Titel, eine Staffel, eine Person.

Bewusst getrennt von ``discover``: die Listen dort sollen schlank bleiben.
Hier wird alles geholt, was die Detailseite braucht - und nur dann, wenn
jemand sie wirklich oeffnet.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel

from ..deps import CurrentUser, DbSession
from ..models import MediaType
from ..schemas_media import MediaDetail, MediaItem, MediaPage, PersonDetail, SeasonDetail
from ..services import blocklist, library, media, ratings, requests_service
from ..services.settings_service import for_user, load_settings
from ..services.tmdb import TmdbError

router = APIRouter(prefix="/api", tags=["details"])

MediaTypePath = Annotated[Literal["movie", "tv"], Path()]


def _fehler(error: TmdbError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code or status.HTTP_502_BAD_GATEWAY, detail=error.message
    )


async def _mit_status(db, settings, media_type: str, eintraege: list) -> None:
    """Badges fuer eine Liste von Titeln setzen - an Ort und Stelle.

    Dieselbe Logik wie in den Listen: was in Radarr/Sonarr liegt, ueberlagert
    den Zustand aus den eigenen Anfragen. Faellt der Abgleich aus, bleiben die
    Badges neutral statt die ganze Seite scheitern zu lassen.
    """
    if not eintraege:
        return
    try:
        abgeglichen = await library.apply_status(settings, media_type, list(eintraege))
        for ziel, quelle in zip(eintraege, abgeglichen.items, strict=True):
            ziel.status = quelle.status
    except Exception:  # noqa: BLE001 - Badges sind Beiwerk, keine Bedingung
        pass

    kennungen = [eintrag.tmdb_id for eintrag in eintraege]
    eigene = requests_service.badges_for(db, MediaType(media_type), kennungen)
    gesperrt = blocklist.gesperrte_kennungen(db, MediaType(media_type), kennungen)

    for eintrag in eintraege:
        # Eine vorhandene Datei gewinnt immer gegen den eigenen Anfragezustand.
        if eintrag.status == "not_requested" and eintrag.tmdb_id in eigene:
            eintrag.status = eigene[eintrag.tmdb_id]
        # Die Sperre gewinnt gegen alles - wie in den Listen. Diese Funktion
        # bedient die Detailseite, ihre Empfehlungen, die Schlagwortlisten und
        # die Filmografien; ohne sie traegen ausgerechnet dort gesperrte Titel
        # weiterhin einen Einkaufswagen.
        if eintrag.tmdb_id in gesperrt:
            eintrag.status = blocklist.BADGE


@router.get("/detail/{media_type}/{tmdb_id}", response_model=MediaDetail)
async def title_detail(
    media_type: MediaTypePath,
    tmdb_id: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    db: DbSession,
) -> MediaDetail:
    """Alles zu einem Titel: Besetzung, Studios, Schlagworte, Empfehlungen."""
    settings = for_user(load_settings(db), user)

    try:
        detail = await media.full_detail(db, settings, media_type, tmdb_id)
    except TmdbError as error:
        raise _fehler(error) from error

    await _mit_status(db, settings, media_type, [detail])
    await _mit_status(db, settings, media_type, detail.recommendations)

    # Bei Serien: wie viele Folgen jeder Staffel liegen schon vor?
    if media_type == "tv" and detail.seasons:
        vorhanden = await library.episode_availability(settings, detail.tvdb_id, detail.title)
        for staffel in detail.seasons:
            staffel.episodes_available = len(vorhanden.get(staffel.season_number, ()))

    return detail


class Auswahl(BaseModel):
    """Eine Runde Vorschlaege - und wie viele es insgesamt gibt."""

    items: list[MediaItem]
    runde: int
    # Steht sie auf 1, gibt es nichts zu wechseln: die Oberflaeche blendet den
    # Knopf dann aus, statt einen anzubieten, der nichts tut.
    runden: int


@router.get("/detail/{media_type}/{tmdb_id}/recommendations", response_model=Auswahl)
async def recommendations(
    media_type: MediaTypePath,
    tmdb_id: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    db: DbSession,
    runde: Annotated[int, Query(ge=0)] = 0,
) -> Auswahl:
    """Eine andere Auswahl passender Titel - fuer "Neue Auswahl".

    Der Vorrat steht fest und ist immer gleich sortiert; ``runde`` schneidet
    nur ein anderes Stueck heraus. Ist er durch, geht es wieder von vorn los -
    ein Knopf, der irgendwann nichts mehr tut, waere aergerlicher als eine
    Wiederholung.
    """
    settings = for_user(load_settings(db), user)

    try:
        vorrat = await media.empfehlungs_vorrat(db, settings, media_type, tmdb_id)
    except TmdbError as error:
        raise _fehler(error) from error

    if not vorrat:
        return Auswahl(items=[], runde=0, runden=0)

    groesse = media.MAX_RECOMMENDATIONS
    runden = -(-len(vorrat) // groesse)  # aufrunden
    aktuell = runde % runden
    ausschnitt = vorrat[aktuell * groesse : (aktuell + 1) * groesse]

    await _mit_status(db, settings, media_type, ausschnitt)
    return Auswahl(items=ausschnitt, runde=aktuell, runden=runden)


@router.get("/detail/tv/{tmdb_id}/season/{season_number}", response_model=SeasonDetail)
async def season(
    tmdb_id: Annotated[int, Path(ge=1)],
    season_number: Annotated[int, Path(ge=0, le=200)],
    user: CurrentUser,
    db: DbSession,
) -> SeasonDetail:
    """Die Folgen einer Staffel - inklusive der Frage, welche schon da sind."""
    settings = for_user(load_settings(db), user)

    try:
        staffel = await media.season_detail(db, settings, tmdb_id, season_number)
    except TmdbError as error:
        raise _fehler(error) from error

    # Fuer den Abgleich mit Sonarr wird die TVDB-Kennung gebraucht; die steckt
    # in den Detaildaten der Serie.
    try:
        serie = await media.detail(db, settings, "tv", tmdb_id)
    except TmdbError:
        return staffel

    vorhanden = await library.episode_availability(settings, serie.tvdb_id, serie.title)
    in_dieser_staffel = vorhanden.get(season_number, set())
    for folge in staffel.episodes:
        folge.available = folge.episode_number in in_dieser_staffel

    return staffel


class BrowseResult(BaseModel):
    """Ergebnisse plus die Ueberschrift, unter der sie stehen."""

    label: str
    page: MediaPage


@router.get("/browse/{media_type}", response_model=BrowseResult)
async def browse(
    media_type: MediaTypePath,
    user: CurrentUser,
    db: DbSession,
    keyword_id: Annotated[int | None, Query(ge=1)] = None,
    company_id: Annotated[int | None, Query(ge=1)] = None,
    page: Annotated[int, Query(ge=1, le=500)] = 1,
) -> BrowseResult:
    """Alle Titel zu einem Schlagwort oder Studio."""
    if keyword_id is None and company_id is None:
        raise HTTPException(
            status_code=422, detail="Es fehlt das Schlagwort bzw. das Studio."
        )

    settings = for_user(load_settings(db), user)

    try:
        ergebnis = await media.browse(
            db, settings, media_type, keyword_id=keyword_id, company_id=company_id, page=page
        )
        # Die Ueberschrift nur auf der ersten Seite holen - beim Nachladen
        # steht sie laengst da.
        label = (
            await media.browse_label(db, settings, keyword_id=keyword_id, company_id=company_id)
            if page == 1
            else ""
        )
    except TmdbError as error:
        raise _fehler(error) from error

    await _mit_status(db, settings, media_type, ergebnis.items)
    return BrowseResult(label=label, page=ergebnis)


@router.get("/person/{person_id}", response_model=PersonDetail)
async def person(
    person_id: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    db: DbSession,
) -> PersonDetail:
    """Eine Person mit Foto, Biografie und ihren bekanntesten Titeln."""
    settings = for_user(load_settings(db), user)

    try:
        detail = await media.person_detail(db, settings, person_id)
    except TmdbError as error:
        raise _fehler(error) from error

    # Filme und Serien getrennt abgleichen - die Bibliotheken sind es auch.
    for art in ("movie", "tv"):
        await _mit_status(
            db, settings, art, [c for c in detail.credits if c.media_type.value == art]
        )

    return detail


class MovieRatings(BaseModel):
    """Bewertungen der grossen Portale - nur bei Filmen verfuegbar."""

    imdb_id: str | None = None
    imdb: float | None = None
    imdb_votes: int | None = None
    rotten_tomatoes: int | None = None
    metacritic: int | None = None


@router.get("/ratings/movie", response_model=dict[int, MovieRatings])
async def movie_ratings(
    user: CurrentUser,
    db: DbSession,
    ids: Annotated[str, Query(max_length=400)],
) -> dict[int, MovieRatings]:
    """Bewertungen zu mehreren Filmen auf einmal.

    Bewusst ein eigener Aufruf und nicht Teil der Listen: die Werte kommen aus
    Radarr, und zwanzig Abfragen dorthin wuerden den Seitenaufbau spuerbar
    verzoegern. So steht die Seite sofort und die Wertungen erscheinen kurz
    darauf.
    """
    kennungen = [
        int(teil) for teil in ids.split(",") if teil.strip().isdigit()
    ][:40]
    if not kennungen:
        return {}

    settings = load_settings(db)
    gefunden = await ratings.for_movies(settings, kennungen)
    return {
        tmdb_id: MovieRatings(
            imdb_id=wert.imdb_id,
            imdb=wert.imdb,
            imdb_votes=wert.imdb_votes,
            rotten_tomatoes=wert.rotten_tomatoes,
            metacritic=wert.metacritic,
        )
        for tmdb_id, wert in gefunden.items()
    }
