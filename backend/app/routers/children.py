"""Kinderkonten - jedes erwachsene Konto verwaltet seine eigenen.

Jeder Endpunkt geht durch ``services/children.py``; dort steht die Regel, wem
welches Kind gehoert. Hier wird sie nur in HTTP uebersetzt.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..deps import CurrentUser, DbSession
from ..models import ChildWish, MediaType, QualityTier, User
from ..schemas import ChildCreate, ChildPassword, ChildPublic, ChildUpdate
from ..schemas_media import MediaDetail, MediaItem
from ..schemas_requests import RequestPublic
from ..services import (
    child_wishes,
    children,
    kids,
    media,
    requests_service,
    streaming,
    tickets,
)
from ..services.settings_service import for_user, load_settings
from ..services.tmdb import TmdbError

router = APIRouter(prefix="/api/children", tags=["children"])

logger = logging.getLogger("nexview.children")


def _fehler(error: children.ChildError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


# ⚠️ Muss **vor** jedem Pfad mit Platzhalter stehen: sonst versucht FastAPI,
# "genres" als ``child_id`` zu lesen, und antwortet mit 422 statt der Liste.
@router.get("/genres", response_model=list[str])
def list_genres(user: CurrentUser) -> list[str]:
    """Die waehlbaren Rubriken, in ihrer festen Reihenfolge.

    Die Oberflaeche holt sie hier statt sie selbst aufzuzaehlen - sonst gaebe
    es zwei Listen, die auseinanderlaufen koennen, und die Uebersetzung stuende
    fuer eine Rubrik, die es serverseitig gar nicht gibt.
    """
    return list(children.RUBRIKEN)


class WishForParent(BaseModel):
    """Ein offener Wunsch, wie das Elternteil ihn sieht."""

    id: int
    child_id: int
    child_name: str
    media_type: str
    tmdb_id: int
    title: str
    poster_path: str | None
    # Abos **des Elternteils**, in denen der Titel schon laeuft. Das Kind hat
    # keine eigenen - es guckt ueber die seiner Eltern.
    in_my_subscriptions: list[str] = []
    release_date: str | None
    created_at: str


class WishDecline(BaseModel):
    # Kurze Begruendung fuers Kind. Optional - aber ohne sie fragt es so lange
    # nach, bis es jemand sagt.
    note: str | None = Field(default=None, max_length=500)


class WishRelease(BaseModel):
    """Dieselben Angaben wie bei einer gewoehnlichen Anfrage.

    Der Wunsch traegt nur Titel und Nummer; alles, was Radarr/Sonarr braucht,
    entscheidet das Elternteil hier - so wie bei jeder eigenen Anfrage auch.
    """

    quality_profile_id: int | None = Field(default=None, ge=1)
    root_folder_path: str | None = Field(default=None, max_length=500)
    season: int | None = Field(default=None, ge=0, le=200)
    monitor_future: bool = False
    tier: QualityTier = QualityTier.standard


async def _abo_treffer(
    db: DbSession, elternteil: User, wuensche: list[ChildWish]
) -> dict[int, list[str]]:
    """Welche Wuensche laufen in einem Abo **des Elternteils**? - je Wunsch-Id.

    Das Kind hat keine eigenen Abos; es guckt ueber die seiner Eltern. Gemessen
    wird deshalb an deren Diensten, und der Hinweis erscheint dort, wo
    entschieden wird - nicht beim Kind, das ohnehin nichts entscheiden kann.

    Ohne hinterlegte Dienste faellt der ganze Weg sofort weg. Faellt TMDB aus,
    bleibt die Spalte leer: Ein fehlender Hinweis ist hinnehmbar, eine
    Wunschliste, die deswegen nicht laedt, nicht.
    """
    dienste = streaming.eigene_dienste(db, elternteil)
    if not dienste or not wuensche:
        return {}

    einstellungen = load_settings(db)
    ergebnis: dict[int, list[str]] = {}
    gesehen: dict[tuple[str, int], list[int]] = {}
    for wunsch in wuensche:
        schluessel = (wunsch.media_type.value, wunsch.tmdb_id)
        if schluessel not in gesehen:
            try:
                detail = await media.full_detail(
                    db, einstellungen, wunsch.media_type.value, wunsch.tmdb_id
                )
            except TmdbError:
                gesehen[schluessel] = []
            else:
                gesehen[schluessel] = [
                    a.id for a in (detail.watch.flatrate if detail.watch else [])
                ]
        namen = streaming.treffer(dienste, gesehen[schluessel])
        if namen:
            ergebnis[wunsch.id] = namen
    return ergebnis


def _wunsch_zeile(wunsch: ChildWish, abos: list[str] | None = None) -> WishForParent:
    return WishForParent(
        id=wunsch.id,
        child_id=wunsch.child_id,
        child_name=wunsch.child.display_name or wunsch.child.username,
        media_type=wunsch.media_type.value,
        tmdb_id=wunsch.tmdb_id,
        title=wunsch.title,
        poster_path=wunsch.poster_path,
        release_date=wunsch.release_date,
        created_at=wunsch.created_at.isoformat(),
        in_my_subscriptions=abos or [],
    )


# ⚠️ Alle Wunsch-Pfade stehen **vor** ``/{child_id}``: sonst versucht FastAPI,
# "wishes" als Kennung zu lesen und antwortet 422.
@router.post("/request-permission", status_code=status.HTTP_204_NO_CONTENT)
def request_permission(user: CurrentUser, db: DbSession) -> None:
    """Die Freigabe für Kinderkonten beim Betreiber erbitten.

    Ein Knopf, kein Formular: Der Text steht fest, der Nutzer muss sich nichts
    ausdenken. Landet als gewöhnliches Ticket bei den Administratoren.
    """
    try:
        tickets.kinderkonten_beantragen(db, user)
    except tickets.TicketError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
    db.commit()


@router.get("/wishes", response_model=list[WishForParent])
async def read_wishes(user: CurrentUser, db: DbSession) -> list[WishForParent]:
    """Was gerade auf eine Entscheidung wartet."""
    wuensche = list(child_wishes.offene_wuensche(db, user))
    abos = await _abo_treffer(db, user, wuensche)
    return [_wunsch_zeile(w, abos.get(w.id)) for w in wuensche]


@router.post("/wishes/{wish_id}/decline", status_code=status.HTTP_204_NO_CONTENT)
def decline_wish(
    wish_id: int, payload: WishDecline, user: CurrentUser, db: DbSession
) -> None:
    try:
        child_wishes.ablehnen(db, user, wish_id, payload.note)
    except children.ChildError as error:
        raise _fehler(error) from error


@router.post("/wishes/{wish_id}/release", response_model=RequestPublic)
async def release_wish(
    wish_id: int, payload: WishRelease, user: CurrentUser, db: DbSession
):
    """Aus dem Wunsch wird eine Anfrage - auf den Namen des Elternteils.

    Der Titel wird dabei aus **seiner** Sicht geholt: Es ist seine Anfrage, mit
    seinem Kontingent und seinen Regeln. Scheitert sie (Kontingent voll,
    Sperrliste, liegt schon da), bleibt der Wunsch offen und der Grund kommt
    unveraendert zurueck.
    """
    settings = for_user(load_settings(db), user)
    try:
        wunsch = child_wishes._wunsch_von(db, user, wish_id)
    except children.ChildError as error:
        raise _fehler(error) from error

    try:
        item = await media.detail(db, settings, wunsch.media_type.value, wunsch.tmdb_id)
    except TmdbError as error:
        code = 404 if error.status_code == 404 else 502
        raise HTTPException(status_code=code, detail=error.message) from error

    try:
        return await child_wishes.freigeben(
            db,
            settings,
            user,
            wish_id,
            item,
            quality_profile_id=payload.quality_profile_id,
            root_folder_path=payload.root_folder_path,
            season=payload.season if wunsch.media_type == MediaType.tv else None,
            tier=payload.tier,
            monitor_future=payload.monitor_future,
        )
    except children.ChildError as error:
        raise _fehler(error) from error
    except requests_service.RequestError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.get("", response_model=list[ChildPublic])
def list_children(user: CurrentUser, db: DbSession) -> list[User]:
    """Die eigenen Kinder. Wer keine hat, bekommt eine leere Liste."""
    return children.eigene_kinder(db, user)


@router.post("", response_model=ChildPublic, status_code=status.HTTP_201_CREATED)
def create_child(payload: ChildCreate, user: CurrentUser, db: DbSession) -> User:
    try:
        kind = children.anlegen(
            db,
            user,
            username=payload.username,
            password=payload.password,
            age=payload.age,
            display_name=payload.display_name,
            genres=payload.genres,
            child_trailers=payload.child_trailers,
            language=payload.language,
        )
    except children.ChildError as error:
        raise _fehler(error) from error

    logger.info("Child account %r created by %r", kind.username, user.username)
    return kind


@router.patch("/{child_id}", response_model=ChildPublic)
def update_child(
    child_id: int, payload: ChildUpdate, user: CurrentUser, db: DbSession
) -> User:
    try:
        return children.aendern(
            db, user, child_id, daten=payload.model_dump(exclude_unset=True)
        )
    except children.ChildError as error:
        raise _fehler(error) from error


@router.post("/{child_id}/password", response_model=ChildPublic)
def set_child_password(
    child_id: int, payload: ChildPassword, user: CurrentUser, db: DbSession
) -> User:
    try:
        return children.passwort_setzen(db, user, child_id, payload.password)
    except children.ChildError as error:
        raise _fehler(error) from error


@router.delete("/{child_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_child(child_id: int, user: CurrentUser, db: DbSession) -> None:
    try:
        kind = children.kind_von(db, user, child_id)
        # Erst die Wuensche, dann das Konto - ``ChildWish.child_id`` traegt
        # keine Fremdschluessel-Regel (nachgetragene Spalten koennen das in
        # SQLite nicht), also bliebe sonst ein Verweis ins Leere stehen.
        child_wishes.wuensche_loeschen(db, kind)
        children.loeschen(db, user, child_id)
    except children.ChildError as error:
        raise _fehler(error) from error

    logger.info("Child account %s deleted by %r", child_id, user.username)


# --- "Was wuerde mein Kind sehen?" -----------------------------------------
#
# Dieselben Daten wie in der Kinderansicht, nur aus Sicht **eines bestimmten
# eigenen Kindes**. Der Sinn: Das Elternteil soll selbst nachsehen koennen,
# was dort ankommt, statt es glauben zu muessen.
#
# Bewusst eigene Pfade statt eines Umschalters an ``/api/kids/*``: Dort haengt
# ``ChildUser``, und diese Grenze soll genau eine Bedeutung behalten. Hier
# haengt stattdessen ``children.kind_von`` - fremde Kinder ergeben 404.
#
# Gewuenscht werden kann in der Vorschau nichts. Das waere ein Wunsch im Namen
# des Kindes, und dafuer gibt es keinen Grund - das Elternteil kann den Titel
# direkt selbst anfragen.


class VorschauKategorie(BaseModel):
    rubrik: str
    bilder: list[str]


def _kind_oder_404(db: DbSession, user: User, child_id: int):
    try:
        return children.kind_von(db, user, child_id)
    except children.ChildError as error:
        raise _fehler(error) from error


@router.get("/{child_id}/preview/categories", response_model=list[VorschauKategorie])
async def preview_categories(
    child_id: int, user: CurrentUser, db: DbSession, media_type: str = "movie"
):
    kind = _kind_oder_404(db, user, child_id)
    settings = for_user(load_settings(db), kind)
    try:
        eintraege = await kids.kategorien(db, settings, kind, media_type)
    except TmdbError as error:
        raise HTTPException(status_code=502, detail=error.message) from error
    return [VorschauKategorie(rubrik=e.rubrik, bilder=e.bilder) for e in eintraege]


class VorschauListe(BaseModel):
    verfuegbar: list[MediaItem]
    wuenschbar: list[MediaItem]
    # Immer leer - in der Vorschau wird nichts gewuenscht. Steht trotzdem hier,
    # damit die Oberflaeche dieselbe Form bekommt wie in der Kinderansicht.
    gewuenscht: list[int] = []


@router.get("/{child_id}/preview/backdrops", response_model=list[str])
async def preview_backdrops(child_id: int, user: CurrentUser, db: DbSession) -> list[str]:
    kind = _kind_oder_404(db, user, child_id)
    settings = for_user(load_settings(db), kind)
    try:
        return await kids.hintergrundbilder(db, settings, kind)
    except TmdbError:
        return []


@router.get("/{child_id}/preview/rubrik/{rubrik}", response_model=VorschauListe)
async def preview_rubrik(
    child_id: int,
    rubrik: str,
    user: CurrentUser,
    db: DbSession,
    media_type: str = "movie",
    page: int = 1,
):
    kind = _kind_oder_404(db, user, child_id)
    if not kids.darf_rubrik(kind, rubrik, media_type):
        raise HTTPException(status_code=404, detail="Diese Rubrik gibt es dort nicht.")
    settings = for_user(load_settings(db), kind)
    try:
        stand = await kids.rubrik_seite(db, settings, kind, media_type, rubrik, page)
    except TmdbError as error:
        raise HTTPException(status_code=502, detail=error.message) from error
    return VorschauListe(verfuegbar=stand.verfuegbar, wuenschbar=stand.wuenschbar)


@router.get("/{child_id}/preview/search", response_model=VorschauListe)
async def preview_search(
    child_id: int,
    user: CurrentUser,
    db: DbSession,
    q: str = "",
    media_type: str = "movie",
    page: int = 1,
):
    kind = _kind_oder_404(db, user, child_id)
    if len(q.strip()) < 2:
        return VorschauListe(verfuegbar=[], wuenschbar=[])
    settings = for_user(load_settings(db), kind)
    try:
        stand = await kids.suche(db, settings, kind, media_type, q, page)
    except TmdbError as error:
        raise HTTPException(status_code=502, detail=error.message) from error
    return VorschauListe(verfuegbar=stand.verfuegbar, wuenschbar=stand.wuenschbar)


@router.get("/{child_id}/preview/title/{media_type}/{tmdb_id}", response_model=MediaDetail)
async def preview_title(
    child_id: int, media_type: str, tmdb_id: int, user: CurrentUser, db: DbSession
):
    """Ein Titel, genau so wie das Kind ihn saehe - samt Rubrik-Pruefung."""
    kind = _kind_oder_404(db, user, child_id)
    settings = for_user(load_settings(db), kind)
    try:
        detail = await media.full_detail(db, settings, media_type, tmdb_id)
        genre_namen = await media._genre_map(db, settings, media_type)
    except TmdbError as error:
        code = 404 if error.status_code == 404 else 502
        raise HTTPException(status_code=code, detail=error.message) from error

    if not kids.passt_in_rubrik(kind, detail, genre_namen):
        raise HTTPException(status_code=404, detail="Diesen Titel sähe dein Kind nicht.")

    # Auch in der Vorschau: Sie soll zeigen, was das Kind sieht - nicht mehr.
    if not kind.child_trailers:
        detail.trailer = None
    if await kids.ist_verfuegbar(db, settings, media_type, detail):
        detail.status = "downloaded"

    detail.recommendations = []
    detail.cast = []
    detail.crew = []
    detail.keywords = []
    return detail
