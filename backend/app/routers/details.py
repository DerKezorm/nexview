"""Ausfuehrliche Ansichten: ein Titel, eine Staffel, eine Person.

Bewusst getrennt von ``discover``: die Listen dort sollen schlank bleiben.
Hier wird alles geholt, was die Detailseite braucht - und nur dann, wenn
jemand sie wirklich oeffnet.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from .. import meldungen
from ..deps import CurrentUser, DbSession
from ..models import MediaType, Role, Ticket, TicketStatus
from ..schemas_media import (
    FolgenFassung,
    MediaDetail,
    MediaItem,
    MediaPage,
    MeineRueckmeldung,
    PersonDetail,
    PersonSummary,
    SeasonDetail,
    StaffelFassung,
)
from ..services import (
    blocklist,
    fassungen,
    fassungsachsen,
    media,
    mediaserver_watched,
    ratings,
    requests_service,
    streaming,
    watch,
)
from ..services.beschaffung import KLASSE_UHD, get_beschaffung, jahr_aus
from ..services.mediaserver import verbundene_anbieter
from ..services.settings_service import for_user, load_settings
from ..services.streaming import eigene_dienste
from ..services.tmdb import TmdbError

logger = logging.getLogger("nexview.details")

router = APIRouter(prefix="/api", tags=["details"])


def _fassungskennungen(settings, media_type: str) -> list[str]:
    """Die Hauptfassung zuerst, dann jede weitere eingerichtete.

    Die Hauptfassung steht auch dann in der Liste, wenn ihre Instanz fehlt:
    Ihre Antworten sind die alten Felder (``episodes_available`` und die
    anderen ohne Suffix), und die gibt es seit jeher auch ohne Sonarr.
    """
    haupt = fassungen.hauptkennung(media_type)
    # Ohne Hauptfassung (NEX-Betrieb, nichts gelesen) gibt es auch keine weiteren.
    if haupt is None:
        return []
    return [haupt] + [
        eintrag.kennung
        for eintrag in settings.fassungen_fuer(media_type)
        if eintrag.kennung != haupt
    ]


class _Staffeldaten(BaseModel):
    """Was eine Fassung ueber die Staffeln einer Serie sagt."""

    model_config = {"arbitrary_types_allowed": True}

    vorhanden: dict[int, set[int]]
    staffelstaende: dict
    angefragt: set[int | None]
    pakete: dict[int, dict[int, str]]
    belegung: dict[int | None, str]

    def staffel(self, kennung: str, nummer: int) -> StaffelFassung:
        stand = self.staffelstaende.get(nummer)
        return StaffelFassung(
            kennung=kennung,
            episodes_available=len(self.vorhanden.get(nummer, ())),
            # ``None`` in der Menge steht fuer eine Anfrage ueber die ganze
            # Serie - die deckt jede Staffel ab.
            requested=nummer in self.angefragt or None in self.angefragt,
            requested_episodes=sorted(self.pakete.get(nummer, {})),
            requested_status=self.belegung.get(nummer) or self.belegung.get(None),
            # Sonarrs eigene Staffel-Zaehlung - der Massstab fuer "vollstaendig".
            episodes_total=stand.folgen if stand is not None and stand.folgen > 0 else None,
        )


class _Folgendaten(BaseModel):
    """Dasselbe fuer die Folgen einer Staffel."""

    vorhanden: set[int]
    deck_status: str | None
    paket_status: dict[int, str]

    def folge(self, kennung: str, nummer: int) -> FolgenFassung:
        status = self.deck_status or self.paket_status.get(nummer)
        return FolgenFassung(
            kennung=kennung,
            available=nummer in self.vorhanden,
            requested=status is not None,
            requested_status=status,
        )


async def _staffeldaten(
    db: DbSession, settings, detail: MediaDetail, kennung: str, jahr: int | None
) -> _Staffeldaten:
    stufe = fassungen.stufe(kennung)
    beschaffung = get_beschaffung(settings)
    vorhanden = await beschaffung.folgen_verfuegbarkeit(
        detail.tvdb_id, detail.title, stufe=stufe, jahr=jahr
    )
    eintrag = await beschaffung.serien_eintrag(
        detail.tvdb_id, detail.title, jahr=jahr, stufe=stufe
    )
    return _Staffeldaten(
        vorhanden=vorhanden,
        staffelstaende=getattr(eintrag, "staffeln", None) or {},
        angefragt=requests_service.angefragte_staffeln(db, detail.tmdb_id, kennung),
        pakete=requests_service.angefragte_pakete(db, detail.tmdb_id, kennung),
        belegung=requests_service.staffel_belegung(db, detail.tmdb_id, kennung),
    )


async def _folgendaten(
    db: DbSession, settings, serie: MediaDetail, tmdb_id: int, season_number: int, kennung: str
) -> _Folgendaten:
    vorhanden = await get_beschaffung(settings).folgen_verfuegbarkeit(
        serie.tvdb_id, serie.title, stufe=fassungen.stufe(kennung), jahr=jahr_aus(serie.release_date)
    )
    voll = requests_service.staffel_belegung(db, tmdb_id, kennung)
    return _Folgendaten(
        vorhanden=vorhanden.get(season_number, set()),
        deck_status=voll.get(season_number) or voll.get(None),
        paket_status=requests_service.angefragte_pakete(db, tmdb_id, kennung).get(
            season_number, {}
        ),
    )

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
        abgeglichen = await get_beschaffung(settings).status_setzen(
            media_type, list(eintraege), mit_pfad=fuer_admin
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
        # Diagnose-Stufe, siehe calendar.py: haeufiger Weg, harmlose Folge, aber der
        # Grund soll auffindbar sein, wenn jemand fehlende Abzeichen meldet.
        logger.debug("Details: status badges could not be filled in", exc_info=True)

    kennungen = [eintrag.tmdb_id for eintrag in eintraege]
    eigene = requests_service.badges_for(db, MediaType(media_type), kennungen)
    gesperrt = blocklist.gesperrte_kennungen(db, MediaType(media_type), kennungen)
    # Nur fuer Titel ohne genaueren Zustand: was im Media-Server liegt, aber
    # Radarr/Sonarr nicht kennt.
    #
    # Welche Kopie zaehlt, entscheidet dieselbe Funktion wie bei der Sperre
    # (``requests_service.im_medienserver``) - Begruendung in discover.py.
    im_server = await requests_service.im_medienserver(
        db,
        settings,
        media_type,
        [e for e in eintraege if e.status == "not_requested"],
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
        await fassungsachsen.anreichern(db, settings, media_type, list(eintraege), user)


@router.get("/detail/{media_type}/{tmdb_id}", response_model=MediaDetail)
async def title_detail(
    media_type: MediaTypePath,
    tmdb_id: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    db: DbSession,
) -> MediaDetail:
    """Alles zu einem Titel: Besetzung, Studios, Schlagworte, Empfehlungen, Filmreihe."""
    settings = for_user(load_settings(db), user)

    try:
        # Die Filmreihe fragt nur die Titelseite an. ``full_detail`` bedient
        # auch die Kinderansicht, und die siebt nach eigenen Regeln.
        detail = await media.full_detail(db, settings, media_type, tmdb_id, mit_reihe=True)
    except TmdbError as error:
        raise _fehler(error) from error

    await _mit_status(db, settings, media_type, [detail], user)
    await _mit_status(db, settings, media_type, detail.recommendations, user)
    if detail.collection is not None:
        await _mit_status(db, settings, media_type, detail.collection.items, user)

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
    # welchen laeuft bereits eine Anfrage? Ohne Hauptfassung (NEX-Betrieb,
    # nichts gelesen) gibt es keine Fassung, in der etwas vorliegen koennte.
    if media_type == "tv" and detail.seasons and fassungen.hauptkennung("tv") is not None:
        jahr = jahr_aus(detail.release_date)
        # Je Fassung dieselben vier Fragen - zuerst die Hauptfassung, deren
        # Antworten auch in den alten Feldern stehen. Eine Fassung, die es
        # nicht gibt, wird nicht gefragt; ihre ``*_uhd``-Felder bleiben
        # ``None`` und heissen "unbekannt", wie bei ``status_uhd``.
        je_fassung = {
            kennung: await _staffeldaten(db, settings, detail, kennung, jahr)
            for kennung in _fassungskennungen(settings, "tv")
        }
        haupt_kennung = fassungen.hauptkennung("tv")
        haupt = je_fassung[haupt_kennung]
        vierk = next(
            (
                kennung
                for kennung in je_fassung
                if kennung != haupt_kennung and fassungen.klasse(kennung) == KLASSE_UHD
            ),
            None,
        )
        for staffel in detail.seasons:
            staffel.fassungen = [
                daten.staffel(kennung, staffel.season_number)
                for kennung, daten in je_fassung.items()
            ]
            eigene = haupt.staffel(haupt_kennung, staffel.season_number)
            staffel.episodes_available = eigene.episodes_available
            staffel.requested = eigene.requested
            staffel.requested_episodes = eigene.requested_episodes
            staffel.requested_status = eigene.requested_status
            staffel.episodes_total_arr = eigene.episodes_total
            if vierk is not None:
                vier = je_fassung[vierk].staffel(vierk, staffel.season_number)
                staffel.episodes_available_uhd = vier.episodes_available
                staffel.requested_uhd = vier.requested
                staffel.requested_episodes_uhd = vier.requested_episodes
                staffel.requested_status_uhd = vier.requested_status
                staffel.episodes_total_arr_uhd = vier.episodes_total

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
    # Ohne Hauptfassung (NEX-Betrieb, nichts gelesen) gibt es nichts abzugleichen.
    haupt_kennung = fassungen.hauptkennung("tv")
    if haupt_kennung is None:
        return staffel

    vorhanden = await get_beschaffung(settings).folgen_verfuegbarkeit(
        serie.tvdb_id, serie.title, jahr=jahr_aus(serie.release_date)
    )
    in_dieser_staffel = vorhanden.get(season_number, set())

    # Und die zweite Frage je Folge: Laeuft schon eine Anfrage - und in
    # welchem Zustand? Eine deckende Voll-Anfrage gibt allen Folgen ihren
    # Status; sonst zaehlt das Paket, das die einzelne Folge besitzt.
    voll = requests_service.staffel_belegung(db, tmdb_id)
    deck_status = voll.get(season_number) or voll.get(None)
    paket_status = requests_service.angefragte_pakete(db, tmdb_id).get(
        season_number, {}
    )
    # Dieselben Fragen je weiterer Fassung; die Hauptfassung steht schon oben.
    weitere = [k for k in _fassungskennungen(settings, "tv") if k != haupt_kennung]
    je_fassung = {
        kennung: await _folgendaten(db, settings, serie, tmdb_id, season_number, kennung)
        for kennung in weitere
    }
    vierk = next((k for k in weitere if fassungen.klasse(k) == KLASSE_UHD), None)

    for folge in staffel.episodes:
        folge.available = folge.episode_number in in_dieser_staffel
        folge.requested_status = deck_status or paket_status.get(folge.episode_number)
        folge.requested = folge.requested_status is not None
        folge.fassungen = [
            FolgenFassung(
                kennung=haupt_kennung,
                available=folge.available,
                requested=folge.requested,
                requested_status=folge.requested_status,
            ),
            *(
                je_fassung[kennung].folge(kennung, folge.episode_number)
                for kennung in weitere
            ),
        ]
        if vierk is not None:
            vier = je_fassung[vierk].folge(vierk, folge.episode_number)
            folge.available_uhd = vier.available
            folge.requested_status_uhd = vier.requested_status
            folge.requested_uhd = vier.requested

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
            status_code=422, detail=meldungen.meldung(
                "keyword_or_studio_missing",
                "Es fehlt das Schlagwort bzw. das Studio.",
            )
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
    #: Die Saetze, die die Quelle neben ihren Werten verlangt (im NEX-Betrieb
    #: IMDb und OMDb, wortwoertlich von nexcrate). Im ARR-Betrieb leer.
    attribution: list[str] = []


@router.get("/ratings/movie", response_model=dict[int, MovieRatings])
async def movie_ratings(
    user: CurrentUser,
    db: DbSession,
    ids: Annotated[str, Query(max_length=400)],
    detail: bool = False,
) -> dict[int, MovieRatings]:
    """Bewertungen zu mehreren Filmen auf einmal.

    Bewusst ein eigener Aufruf und nicht Teil der Listen: die Werte kommen aus
    Radarr (ARR-Betrieb) bzw. von nexcrate (NEX-Betrieb), und zwanzig Abfragen
    dorthin wuerden den Seitenaufbau spuerbar verzoegern. So steht die Seite sofort und die Wertungen erscheinen kurz
    darauf.

    ``detail`` setzt nur die Titelseite: Im NEX-Betrieb fragt dann nexcrates
    Einzelansicht OMDb nach Rotten Tomatoes und Metacritic, wenn sie noch
    nicht in seinem Speicher liegen, und das kostet je Titel eine Abfrage aus
    dem Tageskontingent. Der Stapel fuer Listen liest nur diesen Speicher.
    """
    kennungen = [
        int(teil) for teil in ids.split(",") if teil.strip().isdigit()
    ][:40]
    if not kennungen:
        return {}

    settings = load_settings(db)
    gefunden = await get_beschaffung(settings).wertungen_filme(kennungen, einzeln=detail)
    return {
        tmdb_id: MovieRatings(
            imdb_id=wert.imdb_id,
            imdb=wert.imdb,
            imdb_votes=wert.imdb_votes,
            rotten_tomatoes=wert.rotten_tomatoes,
            metacritic=wert.metacritic,
            attribution=list(wert.attribution),
        )
        for tmdb_id, wert in gefunden.items()
    }
