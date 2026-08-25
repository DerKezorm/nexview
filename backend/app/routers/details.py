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
from sqlalchemy import select

from ..models import MediaType, QualityTier, Role, Ticket, TicketStatus
from ..schemas_media import (
    MeineRueckmeldung,
    MediaDetail,
    MediaItem,
    MediaPage,
    PersonDetail,
    PersonSummary,
    SeasonDetail,
)
from ..services import (
    uhd,
    blocklist,
    library,
    media,
    mediaserver_library,
    mediaserver_watched,
    ratings,
    requests_service,
)
from ..services.mediaserver import verbundene_anbieter
from ..services.settings_service import for_user, load_settings
from ..services import ratings, streaming, watch
from ..services.streaming import eigene_dienste
from ..services.tmdb import TmdbError

router = APIRouter(prefix="/api", tags=["details"])

MediaTypePath = Annotated[Literal["movie", "tv"], Path()]


def _fehler(error: TmdbError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code or status.HTTP_502_BAD_GATEWAY, detail=error.message
    )


async def _mit_status(db, settings, media_type: str, eintraege: list, user=None) -> None:
    """Badges fuer eine Liste von Titeln setzen - an Ort und Stelle.

    Dieselbe Logik wie in den Listen: was in Radarr/Sonarr liegt, ueberlagert
    den Zustand aus den eigenen Anfragen. Faellt der Abgleich aus, bleiben die
    Badges neutral statt die ganze Seite scheitern zu lassen.
    """
    if not eintraege:
        return
    try:
        # Der Ablageort geht **nur** an Administratoren - hier entschieden
        # und nicht in der Oberflaeche: Ausblenden hiesse, ihn trotzdem
        # ausgeliefert zu haben.
        fuer_admin = bool(user is not None and user.role == Role.admin)
        abgeglichen = await library.apply_status(
            settings, media_type, list(eintraege), mit_pfad=fuer_admin
        )
        for ziel, quelle in zip(eintraege, abgeglichen.items, strict=True):
            ziel.status = quelle.status
            # Nur setzen, wo das Ziel das Feld ueberhaupt kennt: Durch diese
            # Funktion laufen auch Filmografie-Eintraege, und Pydantic laesst
            # kein undeklariertes Feld zu - genau daran ist die Personenseite
            # schon einmal mit 500 gescheitert.
            if fuer_admin and quelle.path and hasattr(ziel, "path"):
                ziel.path = quelle.path
    except Exception:  # noqa: BLE001 - Badges sind Beiwerk, keine Bedingung
        pass

    kennungen = [eintrag.tmdb_id for eintrag in eintraege]
    eigene = requests_service.badges_for(db, MediaType(media_type), kennungen)
    gesperrt = blocklist.gesperrte_kennungen(db, MediaType(media_type), kennungen)
    # Nur fuer Titel ohne genaueren Zustand: was im Media-Server liegt, aber
    # Radarr/Sonarr nicht kennt.
    im_server = mediaserver_library.vorhandene_kennungen(
        db,
        MediaType(media_type),
        [e for e in eintraege if e.status == "not_requested"],
        # Welche Stufe zaehlt hier?
        #
        # Ohne zweite Instanz gibt es nur eine Achse, und die Frage lautet
        # schlicht "habe ich den Titel?" - dann zaehlt jede Kopie. Gibt es sie,
        # sind es zwei getrennte Achsen: Eine reine 4K-Kopie darf dann nicht
        # als 1080p durchgehen, sonst laesst sich die 1080p-Fassung nie
        # anfragen. Dieselbe Unterscheidung trifft Overseerr ueber
        # ``enable4kMovie``.
        "standard" if settings.arr_configured(media_type, "uhd") else None,
    )

    for eintrag in eintraege:
        # Eine vorhandene Datei gewinnt immer gegen den eigenen Anfragezustand.
        eigen = eigene.get(eintrag.tmdb_id)
        # Siehe discover.py: "geladen" behauptet etwas ueber die Bibliothek.
        # Sagt die Bibliothek inzwischen etwas anderes, gilt die Behauptung
        # nicht mehr - sonst laesst sich der Titel nie wieder anfragen.
        if eigen == "downloaded" and eintrag.status == "not_requested":
            eigen = None
        if eintrag.status == "not_requested" and eigen:
            eintrag.status = eigen
        elif eintrag.status == "not_requested" and eintrag.tmdb_id in im_server:
            eintrag.status = "in_library"
        # Die Sperre gewinnt gegen alles - wie in den Listen. Diese Funktion
        # bedient die Detailseite, ihre Empfehlungen, die Schlagwortlisten und
        # die Filmografien; ohne sie traegen ausgerechnet dort gesperrte Titel
        # weiterhin einen Einkaufswagen.
        if eintrag.tmdb_id in gesperrt:
            eintrag.status = blocklist.BADGE

    # "Gesehen" kommt zum Zustand hinzu, statt ihn zu ersetzen - und gilt je
    # Person.
    if user is not None:
        gesehen = mediaserver_watched.gesehene_kennungen(
            db, user.id, MediaType(media_type), kennungen
        )
        verbunden = verbundene_anbieter(settings)
        for eintrag in eintraege:
            if eintrag.tmdb_id in gesehen:
                eintrag.watched = True
                # Nur bei mehreren verbundenen Servern und nur, wenn sie sich
                # uneins sind - sonst bleiben beide Listen leer und das Auge
                # sagt schlicht "gesehen".
                eintrag.watched_on, eintrag.watched_not_on = (
                    mediaserver_watched.herkunft_aufteilen(
                        gesehen[eintrag.tmdb_id], verbunden
                    )
                )

        # Zweite Achse zuletzt - sie ergaenzt nur, sie ersetzt nichts.
        await uhd.anreichern(db, settings, media_type, list(eintraege), user)


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

    await _mit_status(db, settings, media_type, [detail], user)
    await _mit_status(db, settings, media_type, detail.recommendations, user)

    # Laeuft der Titel in einem Abo, das *dieser* Benutzer hat? Hier und nicht
    # in ``full_detail``: Dessen TMDB-Antwort liegt fuer alle gemeinsam im
    # Zwischenspeicher.
    detail.watching = watch.vorgemerkt(db, user, MediaType(media_type), tmdb_id)
    detail.requested_by_me = requests_service.eigene_laeuft(
        db, user, MediaType(media_type), tmdb_id
    )

    # Meine eigene Rueckmeldung - bei Serien die zur ganzen Serie; die je
    # Staffel steht in der Staffelliste.
    # Ein offenes Ticket zu diesem Titel - egal auf welchem Weg es entstanden
    # ist. Nexview unterscheidet die Herkunft nicht, und das genuegt: Wer das
    # Problem schon gemeldet hat, braucht keinen zweiten Kanal dafuer.
    detail.open_ticket = (
        db.scalar(
            select(Ticket.id).where(
                Ticket.user_id == user.id,
                Ticket.media_type == MediaType(media_type),
                Ticket.tmdb_id == tmdb_id,
                Ticket.status != TicketStatus.closed,
            )
        )
        is not None
    )

    eigene = ratings.meine(db, user, MediaType(media_type), tmdb_id)
    if eigene is not None:
        detail.my_feedback = MeineRueckmeldung(
            rating=eigene.rating,
            comment=eigene.comment,
            reply=eigene.reply,
            outdated=eigene.outdated,
        )

    detail.in_my_subscriptions = streaming.treffer(
        eigene_dienste(db, user),
        [anbieter.id for anbieter in (detail.watch.flatrate if detail.watch else [])],
    )

    # Bei Serien: wie viele Folgen jeder Staffel liegen schon vor - und zu
    # welchen laeuft bereits eine Anfrage?
    if media_type == "tv" and detail.seasons:
        jahr = library.jahr_aus(detail.release_date)
        vorhanden = await library.episode_availability(
            settings, detail.tvdb_id, detail.title, jahr=jahr
        )
        angefragt = requests_service.angefragte_staffeln(db, detail.tmdb_id)
        # Die zweite Achse nur, wenn es sie gibt - sonst bleiben die Felder
        # ``None`` und heissen "unbekannt", wie bei ``status_uhd``.
        mit_uhd = settings.arr_configured("tv", "uhd")
        if mit_uhd:
            vorhanden_uhd = await library.episode_availability(
                settings, detail.tvdb_id, detail.title, tier="uhd", jahr=jahr
            )
            angefragt_uhd = requests_service.angefragte_staffeln(
                db, detail.tmdb_id, QualityTier.uhd
            )
        for staffel in detail.seasons:
            staffel.episodes_available = len(vorhanden.get(staffel.season_number, ()))
            # ``None`` in der Menge steht fuer eine Anfrage ueber die ganze
            # Serie - die deckt jede Staffel ab.
            staffel.requested = (
                staffel.season_number in angefragt or None in angefragt
            )
            if mit_uhd:
                staffel.episodes_available_uhd = len(
                    vorhanden_uhd.get(staffel.season_number, ())
                )
                staffel.requested_uhd = (
                    staffel.season_number in angefragt_uhd or None in angefragt_uhd
                )

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

    await _mit_status(db, settings, media_type, ausschnitt, user)
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

    vorhanden = await library.episode_availability(
        settings, serie.tvdb_id, serie.title, jahr=library.jahr_aus(serie.release_date)
    )
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

    await _mit_status(db, settings, media_type, ergebnis.items, user)
    return BrowseResult(label=label, page=ergebnis)


class PeopleResult(BaseModel):
    """Eine Seite Personen - und ob es noch mehr gibt (für „Mehr laden")."""

    items: list[PersonSummary]
    has_more: bool


@router.get("/people", response_model=PeopleResult)
async def people(
    user: CurrentUser,
    db: DbSession,
    q: Annotated[str, Query(max_length=100)] = "",
    department: Annotated[Literal["acting", "directing", "writing"], Query()] = "acting",
    page: Annotated[int, Query(ge=1, le=500)] = 1,
) -> PeopleResult:
    """Personen zum Stöbern und Suchen, nach Fach gefiltert - seitenweise.

    Ohne ``q`` die gefragtesten Personen des Fachs (praktisch nur Schauspiel);
    mit ``q`` die Treffer, gefiltert auf Schauspiel, Regie oder Drehbuch.
    """
    settings = for_user(load_settings(db), user)
    try:
        items, mehr = await media.people(
            db, settings, query=q, department=department, page=page
        )
    except TmdbError as error:
        raise _fehler(error) from error
    return PeopleResult(items=items, has_more=mehr)


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
            db, settings, art, [c for c in detail.credits if c.media_type.value == art], user
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
