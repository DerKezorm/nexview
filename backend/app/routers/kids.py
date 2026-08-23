"""Die Kinderansicht - der einzige Router, den ein Kinderkonto erreicht.

Jeder Endpunkt hier haengt an ``ChildUser``; alles andere in Nexview haengt an
``AdultUser`` oder an Administratoren/Entscheidern (siehe ``main.py``). Die
Grenze verlaeuft also einmal quer durch die Anwendung und nicht endpunktweise.

Die Oberflaeche eines Kindes ist bewusst schmal - entdecken, suchen, wuenschen.
Was hier nicht steht, gibt es fuer ein Kind nicht: keine Glocke, keine Tickets,
keine Merkliste, keine Favoriten, kein Kalender, keine Personen, keine
Einstellungen, kein Qualitaetsprofil, keine Staffelwahl, kein 4K.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from ..deps import ChildUser, DbSession
from ..models import ChildWish
from ..schemas_media import MediaDetail, MediaItem
from ..services import child_wishes, kids, media
from ..services.children import ChildError
from ..services.settings_service import for_user, load_settings
from ..services.tmdb import TmdbError

router = APIRouter(prefix="/api/kids", tags=["kids"])

logger = logging.getLogger("nexview.kids")

MediaTypePath = Annotated[Literal["movie", "tv"], Path()]
MediaTypeQuery = Annotated[Literal["movie", "tv"], Query()]


def _http(error: TmdbError) -> HTTPException:
    # Auch die Altersperre kommt hier als 404 an (``media.AgeRestricted`` ist
    # ein ``TmdbError`` mit genau dieser Nummer) - gewollt: eine eigene Meldung
    # wuerde bestaetigen, dass es den Titel gibt.
    return HTTPException(status_code=404 if error.status_code == 404 else 502, detail=error.message)


def _fehler(error: ChildError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


class KidsCategory(BaseModel):
    rubrik: str
    bilder: list[str]


class KidsItems(BaseModel):
    """Zwei Listen: was schon da ist, und was sich das Kind wuenschen kann.

    Getrennt statt gemischt, weil es zwei verschiedene Handlungen sind -
    anschauen oder fragen. Und getrennt statt "Vorhandenes weglassen", weil
    damit gemessen 90 % der Seite verschwanden.

    ``gewuenscht`` kommt mit, damit die Oberflaeche nicht fuer jede Kachel
    einzeln nachfragen muss.
    """

    verfuegbar: list[MediaItem]
    wuenschbar: list[MediaItem]
    gewuenscht: list[int]


class WishOut(BaseModel):
    id: int
    media_type: str
    tmdb_id: int
    title: str
    poster_path: str | None
    release_date: str | None
    # "waiting" | "coming" | "available" | "declined" - siehe
    # ``child_wishes.kinderzustand``.
    state: str
    decline_note: str | None
    created_at: str


class WishCreate(BaseModel):
    media_type: Literal["movie", "tv"]
    tmdb_id: int


def _wunsch_nach_aussen(wunsch: ChildWish) -> WishOut:
    return WishOut(
        id=wunsch.id,
        media_type=wunsch.media_type.value,
        tmdb_id=wunsch.tmdb_id,
        title=wunsch.title,
        poster_path=wunsch.poster_path,
        release_date=wunsch.release_date,
        state=child_wishes.kinderzustand(wunsch),
        decline_note=wunsch.decline_note,
        created_at=wunsch.created_at.isoformat(),
    )


@router.get("/categories", response_model=list[KidsCategory])
async def read_categories(kind: ChildUser, db: DbSession, media_type: MediaTypeQuery = "movie"):
    """Die Startseite: eine Kachel je freigeschalteter Rubrik.

    Erst die Rubriken, dann die Titel - so sieht ein Kind zuerst, worum es
    geht, statt in eine Wand aus Postern zu laufen. Vom Nutzer so entschieden.
    """
    settings = for_user(load_settings(db), kind)
    try:
        eintraege = await kids.kategorien(db, settings, kind, media_type)
    except TmdbError as error:
        raise _http(error) from error
    return [
        KidsCategory(rubrik=eintrag.rubrik, bilder=eintrag.bilder) for eintrag in eintraege
    ]


@router.get("/backdrops", response_model=list[str])
async def read_backdrops(kind: ChildUser, db: DbSession) -> list[str]:
    """Motive fuer den Seitengrund - siehe ``kids.hintergrundbilder``."""
    settings = for_user(load_settings(db), kind)
    try:
        return await kids.hintergrundbilder(db, settings, kind)
    except TmdbError:
        # Ein fehlender Hintergrund ist kein Grund, die Seite scheitern zu
        # lassen - dann bleibt es eben beim Verlauf.
        return []


@router.get("/rubrik/{rubrik}", response_model=KidsItems)
async def read_rubrik(
    rubrik: str,
    kind: ChildUser,
    db: DbSession,
    media_type: MediaTypeQuery = "movie",
    page: Annotated[int, Query(ge=1, le=100)] = 1,
):
    """Alle Titel einer Rubrik - als Gesamtuebersicht, nicht seitwaerts.

    ⚠️ Die Rubrik wird geprueft: Ueber die Adresszeile liesse sich sonst eine
    anfordern, die das Elternteil nicht freigeschaltet hat.
    """
    if not kids.darf_rubrik(kind, rubrik, media_type):
        raise HTTPException(status_code=404, detail="Diese Rubrik gibt es hier nicht.")

    settings = for_user(load_settings(db), kind)
    try:
        stand = await kids.rubrik_seite(db, settings, kind, media_type, rubrik, page)
    except TmdbError as error:
        raise _http(error) from error
    return KidsItems(
        verfuegbar=stand.verfuegbar,
        wuenschbar=stand.wuenschbar,
        gewuenscht=sorted(kids.gewuenschte_kennungen(db, kind, media_type)),
    )


@router.get("/search", response_model=KidsItems)
async def search(
    kind: ChildUser,
    db: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    media_type: MediaTypeQuery = "movie",
    page: Annotated[int, Query(ge=1, le=500)] = 1,
):
    """Suche - **nur** innerhalb der freigeschalteten Rubriken.

    Die Einschraenkung sitzt im Dienst, nicht in der Oberflaeche. Findet sie
    nichts, sagt die Oberflaeche das ausdruecklich ("das gibt es hier nicht,
    frag deine Eltern") statt anonym "keine Treffer" - sonst tippt ein Kind
    denselben Titel dreimal.
    """
    settings = for_user(load_settings(db), kind)
    try:
        stand = await kids.suche(db, settings, kind, media_type, q, page)
    except TmdbError as error:
        raise _http(error) from error
    return KidsItems(
        verfuegbar=stand.verfuegbar,
        wuenschbar=stand.wuenschbar,
        gewuenscht=sorted(kids.gewuenschte_kennungen(db, kind, media_type)),
    )


@router.get("/title/{media_type}/{tmdb_id}", response_model=MediaDetail)
async def read_title(media_type: MediaTypePath, tmdb_id: int, kind: ChildUser, db: DbSession):
    """Ein einzelner Titel - mit Trailer, ohne alles andere.

    ⚠️ Die Rubrik wird **hier** geprueft, nicht nur beim Auflisten: Sonst
    liesse sich ueber die Adresszeile jeder Titel oeffnen, den allein die
    Altersgrenze durchlaesst. Abgelehnt wird mit derselben 404 wie ein Titel,
    den es nicht gibt.
    """
    settings = for_user(load_settings(db), kind)
    try:
        detail = await media.full_detail(db, settings, media_type, tmdb_id)
        genre_namen = await media._genre_map(db, settings, media_type)
    except TmdbError as error:
        raise _http(error) from error

    if not kids.passt_in_rubrik(kind, detail, genre_namen):
        raise HTTPException(status_code=404, detail="Diesen Titel gibt es hier nicht.")

    # Trailer nur, wenn das Elternteil sie erlaubt hat. Die Sperre sitzt
    # hier und nicht in der Oberflaeche: Sonst kaeme die YouTube-Kennung
    # trotzdem im Browser an, und ein fehlender Knopf waere Dekoration.
    if not kind.child_trailers:
        detail.trailer = None

    # Liegt der Titel schon da, steht auf der Seite "Das kannst du schon
    # schauen" statt des Wunsch-Knopfes. Der Zustand wird hier gesetzt, damit
    # die Oberflaeche ihn nicht selbst herleiten muss.
    if await kids.ist_verfuegbar(db, settings, media_type, detail):
        detail.status = "downloaded"

    # Empfehlungen und Besetzung bleiben aussen vor: Sie fuehren aus den
    # Rubriken heraus und muessten dann einzeln nachgeprueft werden.
    detail.recommendations = []
    detail.cast = []
    detail.crew = []
    detail.keywords = []
    return detail


@router.get("/wishes", response_model=list[WishOut])
def read_wishes(kind: ChildUser, db: DbSession) -> list[WishOut]:
    return [_wunsch_nach_aussen(w) for w in child_wishes.wuensche_des_kindes(db, kind)]


@router.post("/wishes", response_model=WishOut, status_code=201)
async def create_wish(payload: WishCreate, kind: ChildUser, db: DbSession) -> WishOut:
    """Sich etwas wuenschen.

    Der Titel wird hier **noch einmal** ueber TMDB geholt, mit den
    Einstellungen des Kindes: Ein gesperrter Titel wirft dabei ``AgeRestricted``
    und kommt als 404 zurueck. Der fehlende Knopf in der Oberflaeche ist
    Bequemlichkeit - das hier ist die Sperre.
    """
    settings = for_user(load_settings(db), kind)
    try:
        item = await media.detail(db, settings, payload.media_type, payload.tmdb_id)
        genre_namen = await media._genre_map(db, settings, payload.media_type)
    except TmdbError as error:
        raise _http(error) from error

    if not kids.passt_in_rubrik(kind, item, genre_namen):
        raise HTTPException(status_code=404, detail="Diesen Titel gibt es hier nicht.")

    try:
        wunsch = child_wishes.wuenschen(db, kind, item)
    except ChildError as error:
        raise _fehler(error) from error

    logger.info("Child %r wished for %r", kind.username, item.title)
    return _wunsch_nach_aussen(wunsch)
