"""Die Adressen der Seerr-Schicht.

Welche es gibt und warum genau diese, steht in ``__init__.py``. Jede Adresse
geht durch dieselben Wege wie die Oberflaeche:

* **Altersgrenze:** Listen laufen durch ``media._to_items``, Details durch
  ``media.roh_ausfuehrlich`` bzw. ``media.detail``. Beide sieben nach der
  Grenze des Kontos, in dessen Namen gefragt wird.
* **Zustand eines Titels:** ``discover._status_for``, dieselbe Funktion, die
  die Abzeichen der Kacheln setzt (Bibliothek, Medienserver, Anfragen, Sperre).
* **Anfragen:** ``requests_service.create_request`` mit allen Regeln, Rechten
  und Kontingenten.

⚠️ Nichts hier entscheidet etwas selbst, was Nexview anderswo entscheidet. Wer
eine Regel nur hier nachbaut, hat zwei Regeln, die auseinanderlaufen.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import __version__
from ..db import get_db
from ..models import (
    Favorite,
    MediaRequest,
    MediaServerLibraryItem,
    MediaType,
    NotificationType,
    RequestStatus,
    Role,
    User,
)
from ..routers import admin_requests
from ..routers import discover as discover_router
from ..routers import favorites as favoriten
from ..services import cache, fassungen, media, requests_service
from ..services.beschaffung.base import KLASSE_UHD
from ..services.settings_service import AppSettings, for_user, load_settings
from ..services.tmdb import TmdbError
from . import form
from .zugang import SeerrFehler, SeerrZugang

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

Db = Annotated[Session, Depends(get_db)]

#: Seiten der eigenen Listen (Merkliste), wie bei Seerr.
MERKLISTE_SEITE = 20


# --- Hilfen -------------------------------------------------------------------


def _einstellungen(db: Session, nutzer: User) -> AppSettings:
    return for_user(load_settings(db), nutzer)


def _tmdb(settings: AppSettings):  # noqa: ANN202 - TmdbClient oder der Ersatz im Test
    """Der TMDB-Zugang - oder ein klares Nein, wenn Nexview keinen hat."""
    if settings.use_demo_data:
        raise SeerrFehler(503, "Nexview has no TMDB API key configured yet.")
    return media._client(settings)


async def _roh(db: Session, settings: AppSettings, pfad: str, params: dict[str, Any], ttl: timedelta) -> dict[str, Any]:
    """Eine TMDB-Abfrage, zwischengespeichert wie Nexviews eigene Listen."""
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    schluessel = (
        "seerr:" + pfad + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params)) + f"#{settings.default_language}"
    )

    async def holen() -> dict[str, Any]:
        return await _tmdb(settings).roh(pfad, params)

    try:
        return await cache.cached(db, schluessel, ttl, holen)
    except TmdbError as fehler:
        raise _tmdb_fehler(fehler) from fehler


def _tmdb_fehler(fehler: TmdbError) -> SeerrFehler:
    # Eine Altersgrenze sieht von aussen aus wie "gibt es nicht" - siehe
    # ``media.AgeRestricted``.
    if fehler.status_code == 404 or isinstance(fehler, media.AgeRestricted):
        return SeerrFehler(404, "Not found.")
    return SeerrFehler(502, "TMDB did not answer.")


def _seitennummer(seite: int | None) -> int:
    return max(1, min(int(seite or 1), 500))


def _uhd_kennungen(settings: AppSettings, media_type: str) -> set[str]:
    return {f.kennung for f in settings.fassungen_fuer(media_type) if f.klasse == KLASSE_UHD}


def _ist_4k(kennung: str | None) -> bool:
    return fassungen.klasse(kennung) == KLASSE_UHD


def _darf_4k(db: Session, settings: AppSettings, user: User) -> bool:
    return any(
        fassungen.darf_anfragen(db, user, kennung)
        for art in ("movie", "tv")
        for kennung in _uhd_kennungen(settings, art)
    )


def _nutzerform(db: Session, user: User) -> dict[str, Any]:
    settings = _einstellungen(db, user)
    anzahl = db.scalar(select(func.count()).select_from(MediaRequest).where(MediaRequest.user_id == user.id)) or 0
    return form.nutzer(user, darf_4k=_darf_4k(db, settings, user), anfragen=int(anzahl))


def _verwalter(zugang_: Any) -> None:
    """Nur fuer Konten, die ueber fremde Anfragen entscheiden duerfen."""
    if not zugang_.nutzer.can_approve:
        raise SeerrFehler(403, "You do not have permission to access this endpoint.")


# --- Titel in Seerrs Form -------------------------------------------------------


def _medieninfo(item: Any, media_type: str, anfragen: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """``mediaInfo`` einer Kachel - oder ``None``, wenn Nexview nichts darueber weiss.

    Seerr laesst ``mediaInfo`` weg, solange es keine Zeile zu dem Titel hat.
    Clients pruefen deshalb ``mediaInfo?.status`` und behandeln das Fehlen als
    "noch nicht angefragt". Ein ``status: 1`` an jedem Titel waere dasselbe,
    nur lauter.
    """
    status = form.medienstatus(getattr(item, "status", None))
    status_4k = form.medienstatus(getattr(item, "status_uhd", None))
    if status == form.MEDIUM_UNBEKANNT and status_4k == form.MEDIUM_UNBEKANNT and not anfragen:
        return None
    info: dict[str, Any] = {
        "id": item.tmdb_id,
        "mediaType": media_type,
        "tmdbId": item.tmdb_id,
        "tvdbId": getattr(item, "tvdb_id", None),
        "imdbId": None,
        "status": status,
        "status4k": status_4k,
        "downloadStatus": [],
        "downloadStatus4k": [],
        "seasons": [],
    }
    if anfragen is not None:
        info["requests"] = anfragen
    return info


def _titel(roh: dict[str, Any], media_type: str, item: Any) -> dict[str, Any]:
    """Ein Listeneintrag: TMDB umbenannt, Beschreibung und Zustand von Nexview."""
    daten = form.kamel({k: v for k, v in roh.items() if k != "media_type"})
    daten["mediaType"] = media_type
    # Nexviews Beschreibung, nicht TMDBs: Sie hat den Rueckfall auf Englisch,
    # wo die eingestellte Sprache leer ist (Issue #6).
    daten["overview"] = item.overview or daten.get("overview") or ""
    if "genreIds" not in daten:
        daten["genreIds"] = list(item.genre_ids)
    info = _medieninfo(item, media_type)
    if info is not None:
        daten["mediaInfo"] = info
    return daten


async def _ergebnisse(
    db: Session,
    settings: AppSettings,
    nutzer: User,
    rohliste: list[dict[str, Any]],
    standardart: str | None = None,
) -> list[dict[str, Any]]:
    """TMDB-Eintraege in Seerrs Form - gesiebt, in der Reihenfolge von TMDB.

    Gemischte Listen (Trends, Suche, Filmografie) tragen ``media_type`` je
    Eintrag; reine Film- oder Serienlisten nicht, dann gilt ``standardart``.
    Personen und Sammlungen gehen ohne Anreicherung durch, aber die Titel, fuer
    die eine Person "bekannt" ist, laufen ebenfalls durchs Sieb - sonst waere
    die Personenzeile der Suche ein Weg an der Altersgrenze vorbei.
    """
    region = settings.default_region
    gruppen: dict[str, list[dict[str, Any]]] = {"movie": [], "tv": []}
    for eintrag in rohliste:
        art = eintrag.get("media_type") or standardart
        if art in gruppen and eintrag.get("id"):
            gruppen[art].append(eintrag)

    fertig: dict[tuple[str, int], Any] = {}
    for art, eintraege in gruppen.items():
        if not eintraege:
            continue
        # Dieselbe Kennung kann mehrfach vorkommen (zwei Rollen in einer
        # Filmografie); angereichert wird sie einmal.
        einmal = list({e["id"]: e for e in eintraege}.values())
        items = await media._to_items(db, settings, art, einmal, region)
        items, _ = await discover_router._status_for(db, settings, art, items, nutzer)
        for item in items:
            fertig[(art, item.tmdb_id)] = item

    ergebnis: list[dict[str, Any]] = []
    for eintrag in rohliste:
        art = eintrag.get("media_type") or standardart
        if art in ("movie", "tv"):
            item = fertig.get((art, eintrag.get("id")))
            if item is not None:
                ergebnis.append(_titel(eintrag, art, item))
        elif art == "person":
            bekannt = await _ergebnisse(db, settings, nutzer, list(eintrag.get("known_for") or []))
            person = form.kamel({k: v for k, v in eintrag.items() if k not in ("known_for", "media_type")})
            person["mediaType"] = "person"
            person["knownFor"] = bekannt
            ergebnis.append(person)
        elif art == "collection":
            sammlung = form.kamel({k: v for k, v in eintrag.items() if k != "media_type"})
            sammlung["mediaType"] = "collection"
            ergebnis.append(sammlung)
    return ergebnis


async def _seite(
    db: Session,
    settings: AppSettings,
    nutzer: User,
    roh: dict[str, Any],
    standardart: str | None = None,
    **zusatz: Any,
) -> dict[str, Any]:
    ergebnisse = await _ergebnisse(db, settings, nutzer, list(roh.get("results") or []), standardart)
    antwort = form.seite(
        ergebnisse,
        seite_nr=int(roh.get("page") or 1),
        # TMDB liefert hoechstens 500 Seiten aus.
        seiten=min(int(roh.get("total_pages") or 1), 500),
        gesamt=int(roh.get("total_results") or 0),
    )
    antwort.update(zusatz)
    return antwort


# --- Status und Anmeldung -------------------------------------------------------


@router.get("/status")
def status_() -> dict[str, Any]:
    """Wie Seerr ohne Anmeldung. Clients pruefen damit nur, ob jemand antwortet."""
    return {
        "version": __version__,
        "commitTag": f"nexview-{__version__}",
        "updateAvailable": False,
        "commitsBehind": 0,
        "restartRequired": False,
    }


@router.get("/auth/me")
def auth_me(zugang_: SeerrZugang, db: Db) -> dict[str, Any]:
    return _nutzerform(db, zugang_.nutzer)


@router.post("/auth/local")
@router.post("/auth/jellyfin")
@router.post("/auth/plex")
@router.post("/auth/jellyfin/quickconnect/initiate")
@router.post("/auth/jellyfin/quickconnect/authenticate")
@router.get("/auth/jellyfin/quickconnect/check")
def anmelden_mit_passwort() -> None:
    """Anmelden mit Passwort oder Quick Connect gibt es hier nicht.

    Litefin holt sich so Seerrs Installationsschluessel aus
    ``settings/main``. Bei Nexview traegt man stattdessen einen eigenen
    Schluessel von Hand ein - einen Schluessel nach Passwort herauszugeben,
    waere eine zweite Tuer ins Konto, die niemand ueberwacht.
    """
    raise SeerrFehler(
        403,
        "Signing in with a password is not available. Create an API token in Nexview "
        "(Profile, Account, Security) and enter it as the Seerr API key.",
    )


# --- Dienste (Fassungen) -------------------------------------------------------


def _server(settings: AppSettings, art: str, nutzer: User, db: Session) -> list[dict[str, Any]]:
    """Nexviews Fassungen als Seerrs Radarr- bzw. Sonarr-Server.

    Gezeigt wird nur, was das Konto anfragen darf. ``isDefault`` traegt je
    Klasse die erste Fassung: Seerr hat einen Standardserver fuer HD und einen
    fuer 4K, und Silo waehlt "default" genau so.
    """
    gesehen: set[bool] = set()
    server = []
    for fassung in settings.fassungen_fuer(art):
        if not fassungen.darf_anfragen(db, nutzer, fassung.kennung):
            continue
        ist_4k = fassung.klasse == KLASSE_UHD
        server.append(
            {
                "id": form.server_id(fassung.kennung),
                "name": fassung.name or fassung.kennung,
                "is4k": ist_4k,
                "isDefault": ist_4k not in gesehen,
                "activeDirectory": "",
                "activeProfileId": settings.default_profile_id(art, "uhd" if ist_4k else "standard"),
                "activeTags": [],
            }
        )
        gesehen.add(ist_4k)
    return server


@router.get("/service/radarr")
def dienste_radarr(zugang_: SeerrZugang, db: Db) -> list[dict[str, Any]]:
    return _server(_einstellungen(db, zugang_.nutzer), "movie", zugang_.nutzer, db)


@router.get("/service/sonarr")
def dienste_sonarr(zugang_: SeerrZugang, db: Db) -> list[dict[str, Any]]:
    return _server(_einstellungen(db, zugang_.nutzer), "tv", zugang_.nutzer, db)


@router.get("/service/{dienst}/{server}")
def dienst(dienst: str, server: int, zugang_: SeerrZugang, db: Db) -> dict[str, Any]:
    """Ein Server mit Profilen und Ordnern.

    ⚠️ Profile und Ordner bleiben leer. Ein Client schickt dann keins mit, und
    Nexview nimmt die Vorgabe des Administrators - so wie bei einer Anfrage
    aus der Oberflaeche, wenn die Wahl abgeschaltet ist.
    """
    art = {"radarr": "movie", "sonarr": "tv"}.get(dienst)
    if art is None:
        raise SeerrFehler(404, "Unknown service.")
    treffer = next(
        (s for s in _server(_einstellungen(db, zugang_.nutzer), art, zugang_.nutzer, db) if s["id"] == server),
        None,
    )
    if treffer is None:
        raise SeerrFehler(404, "Unknown server.")
    return {"server": treffer, "profiles": [], "rootFolders": [], "languageProfiles": [], "tags": []}


# --- Anfragen ---------------------------------------------------------------

#: Seerrs ``filter`` -> Nexviews Zustaende (routes/request.ts, GET /).
_FILTER: dict[str, tuple[RequestStatus, ...]] = {
    "approved": (RequestStatus.approved, RequestStatus.searching),
    "processing": (RequestStatus.approved, RequestStatus.searching),
    "pending": (RequestStatus.pending_approval,),
    "unavailable": (RequestStatus.pending_approval, RequestStatus.approved, RequestStatus.searching),
    "failed": (RequestStatus.failed,),
    "completed": (RequestStatus.downloaded,),
    "available": (RequestStatus.downloaded,),
    "deleted": (RequestStatus.deleted,),
}


def _anfrage_sichtbar(zugang_: Any, zeile: MediaRequest) -> bool:
    return zugang_.nutzer.can_approve or zeile.user_id == zugang_.nutzer.id


def _anfrageform(zeile: MediaRequest) -> dict[str, Any]:
    return form.anfrage(zeile, ist_4k=_ist_4k(zeile.fassung_kennung))


@router.get("/request")
def anfragen(
    zugang_: SeerrZugang,
    db: Db,
    take: Annotated[int, Query(ge=1, le=1000)] = 10,
    skip: Annotated[int, Query(ge=0)] = 0,
    filter: str | None = None,  # noqa: A002 - Seerrs Name
    sort: str | None = None,
    sortDirection: str | None = None,  # noqa: N803 - Seerrs Name
    requestedBy: int | None = None,  # noqa: N803 - Seerrs Name
    mediaType: str | None = None,  # noqa: N803 - Seerrs Name
) -> dict[str, Any]:
    """Anfragen, wie Seerr sie listet.

    Wer nicht freigeben darf, sieht nur die eigenen - auch mit
    ``requestedBy`` einer anderen Person (Seerr verlangt dafuer
    ``MANAGE_REQUESTS`` oder ``REQUEST_VIEW``).
    """
    abfrage = select(MediaRequest).options(
        selectinload(MediaRequest.user), selectinload(MediaRequest.approver)
    )
    if not zugang_.nutzer.can_approve:
        abfrage = abfrage.where(MediaRequest.user_id == zugang_.nutzer.id)
    elif requestedBy is not None:
        abfrage = abfrage.where(MediaRequest.user_id == requestedBy)
    if filter in _FILTER:
        abfrage = abfrage.where(MediaRequest.status.in_(_FILTER[filter]))
    if mediaType in ("movie", "tv"):
        abfrage = abfrage.where(MediaRequest.media_type == MediaType(mediaType))

    gesamt = db.scalar(select(func.count()).select_from(abfrage.subquery())) or 0
    spalte = (
        func.coalesce(MediaRequest.completed_at, MediaRequest.approved_at, MediaRequest.requested_at)
        if sort == "modified"
        else MediaRequest.requested_at
    )
    reihenfolge = spalte.asc() if sortDirection == "asc" else spalte.desc()
    zeilen = list(db.scalars(abfrage.order_by(reihenfolge, MediaRequest.id.desc()).offset(skip).limit(take)))
    return form.seitenangabe([_anfrageform(z) for z in zeilen], gesamt=int(gesamt), take=take, skip=skip)


@router.get("/request/count")
def anfragen_zaehlen(zugang_: SeerrZugang, db: Db) -> dict[str, int]:
    _verwalter(zugang_)

    def zahl(*zustaende: RequestStatus) -> int:
        return int(
            db.scalar(select(func.count()).select_from(MediaRequest).where(MediaRequest.status.in_(zustaende))) or 0
        )

    return {
        "total": int(db.scalar(select(func.count()).select_from(MediaRequest)) or 0),
        "movie": int(
            db.scalar(
                select(func.count()).select_from(MediaRequest).where(MediaRequest.media_type == MediaType.movie)
            )
            or 0
        ),
        "tv": int(
            db.scalar(select(func.count()).select_from(MediaRequest).where(MediaRequest.media_type == MediaType.tv))
            or 0
        ),
        "pending": zahl(RequestStatus.pending_approval),
        "approved": zahl(RequestStatus.approved, RequestStatus.searching),
        "declined": zahl(RequestStatus.rejected),
        "processing": zahl(RequestStatus.approved, RequestStatus.searching),
        "available": zahl(RequestStatus.downloaded),
        "completed": zahl(RequestStatus.downloaded),
    }


@router.get("/request/{anfrage_id}")
def anfrage(anfrage_id: int, zugang_: SeerrZugang, db: Db) -> dict[str, Any]:
    zeile = db.get(MediaRequest, anfrage_id)
    if zeile is None or not _anfrage_sichtbar(zugang_, zeile):
        raise SeerrFehler(404, "Request not found.")
    return _anfrageform(zeile)


def _fassung_waehlen(settings: AppSettings, art: str, ist_4k: bool, server: int | None) -> tuple[str | None, str]:
    """Welche Fassung eine Seerr-Anfrage meint: ``serverId`` geht vor ``is4k``."""
    stufe = "uhd" if ist_4k else "standard"
    if server is not None:
        for fassung in settings.fassungen_fuer(art):
            if form.server_id(fassung.kennung) == server:
                return fassung.kennung, ("uhd" if fassung.klasse == KLASSE_UHD else "standard")
        raise SeerrFehler(400, "Unknown serverId.")
    return fassungen.gewaehlt(settings, art, None, stufe), stufe


#: Die Absagen, die bei Seerr ein 409 sind: "gibt es schon".
_DOPPELT = {"request_already_exists", "request_already_exists_season", *requests_service.SCHON_DA}


async def _vorgabeprofil(settings: AppSettings, art: str, stufe: str) -> int | None:
    """Das Profil, wenn ein Seerr-Client keines nennt.

    In der Oberflaeche waehlt, wer waehlen darf, im Dialog. Ein Seerr-Client
    hat keinen Dialog; ohne Angabe bekaeme er "Bitte ein Qualitaetsprofil
    waehlen" und koennte nichts dagegen tun. Genommen wird deshalb die Vorgabe
    des Administrators, sonst das erste Profil der Instanz - dasselbe, was
    ``resolve_profile`` fuer Konten ohne Wahl nimmt.

    Im NEX-Betrieb gibt es keine Profile (``create_request`` setzt es dort
    ohnehin auf ``None``); die Frage an die Instanz scheitert dann, und das
    ist hier kein Fehler.
    """
    vorgabe = settings.default_profile_id(art, stufe)
    if vorgabe is not None:
        return vorgabe
    try:
        _, bekannte = await requests_service._ziel_auswahl(settings, art, stufe)
    except Exception:  # noqa: BLE001 - keine Instanz, kein Profil: create_request sagt es genauer
        return None
    return bekannte[0] if bekannte else None


def _seerr_fehler_aus(fehler: requests_service.RequestError) -> SeerrFehler:
    """Nexviews Absage in Seerrs Form.

    Nexviews Saetze sind deutsch (das Frontend uebersetzt ueber die Kennung,
    siehe ``meldungen``). Nach aussen geht ein englischer Satz mit der
    Kennung, damit ein Betreiber im Log findet, was gemeint war.
    """
    code = fehler.code or "request_refused"
    # Die Kennung steht im Protokoll (das ist englisch, ``test_log_sprache``):
    # Dort sucht der Betreiber, warum Silo "refused" meldet.
    logger.info("Seerr API: request refused (%s, %s)", code, fehler.status_code)
    if code in _DOPPELT:
        return SeerrFehler(409, f"This title is already requested or available ({code}).")
    # Jede andere Absage ist keine Doppelung, auch wenn Nexview sie mit 409
    # beantwortet ("Fassung nicht bereit", "steht schon zurueck"). Silo haelt
    # 409 fuer "gibt es schon" und sucht dann vergeblich die vorhandene.
    status = fehler.status_code if fehler.status_code in (400, 403, 404, 429, 502) else 403
    if fehler.status_code == 422:
        status = 400
    return SeerrFehler(status, f"Nexview refused the request ({code}).")


@router.post("/request", status_code=201)
async def anfrage_anlegen(
    zugang_: SeerrZugang,
    db: Db,
    body: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    """Anfragen wie bei Seerr: ``mediaType``, ``mediaId``, ``seasons``, ``is4k``, ``serverId``.

    ``seasons`` ist ``"all"`` oder eine Liste von Staffeln. Nexview legt je
    Staffel eine Anfrage an; zurueck kommt **eine** Seerr-Anfrage, deren
    ``seasons`` alle Teile nennt. ``"all"`` oder keine Angabe heisst bei
    Nexview "die ganze Serie".

    ``userId`` fragt im Namen eines anderen Kontos an - nur mit einem
    Schluessel eines Administrators, wie ``X-Api-User``.
    """
    art = body.get("mediaType")
    if art not in ("movie", "tv"):
        raise SeerrFehler(400, "mediaType must be movie or tv.")
    try:
        tmdb_id = int(body.get("mediaId"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SeerrFehler(400, "mediaId must be a TMDB id.") from None

    nutzer = zugang_.nutzer
    fuer = body.get("userId")
    if fuer not in (None, ""):
        try:
            fuer_id = int(fuer)
        except (TypeError, ValueError):
            raise SeerrFehler(400, "userId must be a user id.") from None
        if fuer_id != nutzer.id:
            # ⚠️ **Gefragt wird die handelnde Person, nicht der Besitzer des
            # Schluessels.** Litefin fragt mit dem Schluessel eines
            # Administrators im Namen der Person (``X-Api-User``) und reicht
            # ``userId`` aus seiner eigenen Anfrage ungeprueft weiter. Hinge die
            # Erlaubnis am Besitzer, schickte ein Jugendlicher am Fernseher
            # ``userId: 1`` und fragte als Administrator an - ohne
            # Altersgrenze, ohne Kontingent, sofort freigegeben. Seerr prueft
            # ebenfalls die handelnde Person.
            if nutzer.role != Role.admin:
                raise SeerrFehler(403, "You do not have permission to request for another user.")
            ziel = db.get(User, fuer_id)
            if ziel is None or not ziel.is_active or ziel.role == Role.child:
                raise SeerrFehler(403, "Unknown userId.")
            nutzer = ziel

    server = body.get("serverId")
    try:
        server_nr = int(server) if server is not None else None
    except (TypeError, ValueError):
        raise SeerrFehler(400, "serverId must be a number.") from None

    settings = _einstellungen(db, nutzer)
    # Ohne ``_tmdb``-Sperre: ``media.detail`` kennt den Beispielbetrieb selbst,
    # und angefragt wird dort wie in der Oberflaeche.
    try:
        item = await media.detail(db, settings, art, tmdb_id)
    except TmdbError as fehler:
        raise _tmdb_fehler(fehler) from fehler

    kennung, stufe = _fassung_waehlen(settings, art, bool(body.get("is4k")), server_nr)
    profil = body.get("profileId")
    profil_id = int(profil) if isinstance(profil, int | str) and str(profil).isdigit() else None
    if profil_id is None:
        profil_id = await _vorgabeprofil(settings, art, stufe)
    ordner = body.get("rootFolder") if isinstance(body.get("rootFolder"), str) else None

    staffeln: list[int | None] = [None]
    if art == "tv" and isinstance(body.get("seasons"), list):
        nummern = sorted({int(s) for s in body["seasons"] if isinstance(s, int) or str(s).isdigit()})
        if nummern:
            staffeln = list(nummern)

    angelegt: list[MediaRequest] = []
    for staffel in staffeln:
        try:
            zeile = await requests_service.create_request(
                db,
                settings,
                nutzer,
                item,
                profil_id,
                ordner,
                staffel,
                kennung,
                tier=stufe,
            )
        except requests_service.RequestError as fehler:
            # Eine Staffel, die schon da ist, haelt die uebrigen nicht auf.
            if len(staffeln) > 1 and fehler.code in _DOPPELT:
                continue
            # ⚠️ **Was schon angelegt ist, bleibt angelegt und wird gemeldet.**
            # Jede Staffel ist eine eigene Anfrage, die beim Anlegen
            # gespeichert (und bei Sofort-Freigabe uebergeben) wird. Eine
            # Absage fuer Staffel 3 nach angelegter Staffel 1 und 2 als Fehler
            # zu melden hiesse, dem Client zu sagen, es sei nichts passiert.
            if angelegt:
                logger.info(
                    "Seerr API: stopped after %d of %d season(s) (%s)", len(angelegt), len(staffeln), fehler.code
                )
                break
            raise _seerr_fehler_aus(fehler) from fehler
        angelegt.append(zeile)

    if not angelegt:
        raise SeerrFehler(409, "All requested seasons are already requested or available.")
    erste = angelegt[0]
    logger.info(
        "Seerr API: %s requested %r (tmdb=%s, %d part(s))",
        nutzer.username,
        erste.title,
        erste.tmdb_id,
        len(angelegt),
    )
    return form.anfrage(erste, ist_4k=_ist_4k(erste.fassung_kennung), staffeln=angelegt)


@router.delete("/request/{anfrage_id}", status_code=204)
async def anfrage_loeschen(anfrage_id: int, zugang_: SeerrZugang, db: Db) -> Response:
    """Zuruecknehmen wie bei Seerr.

    Die eigene, noch offene Anfrage nimmt jeder zurueck. Eine laufende bricht
    nur ab, wer freigeben darf - mit Nexviews Abbruch, der auch die Dateien
    entfernt, die nur fuer diese Anfrage kamen.
    """
    zeile = db.get(MediaRequest, anfrage_id)
    if zeile is None or not _anfrage_sichtbar(zugang_, zeile):
        raise SeerrFehler(404, "Request not found.")
    try:
        if zeile.status == RequestStatus.pending_approval and zeile.user_id == zugang_.nutzer.id:
            requests_service.withdraw(db, zugang_.nutzer, anfrage_id)
        elif zugang_.nutzer.can_approve and zeile.status in (
            RequestStatus.approved,
            RequestStatus.searching,
            RequestStatus.failed,
        ):
            await requests_service.cancel(db, _einstellungen(db, zeile.user), zeile)
            # Wie ``admin_requests.cancel_request``: Wer angefragt hat, erfaehrt es.
            admin_requests._notify_requester(db, zeile, NotificationType.cancelled, "notifications.cancelled")
            db.commit()
        else:
            raise SeerrFehler(401, "You do not have permission to delete this request.")
    except requests_service.RequestError as fehler:
        raise _seerr_fehler_aus(fehler) from fehler
    return Response(status_code=204)


# --- Titel ------------------------------------------------------------------


def _anfragen_zum_titel(db: Session, zugang_: Any, art: str, tmdb_id: int) -> list[dict[str, Any]]:
    """``mediaInfo.requests``: die laufenden Anfragen zu diesem Titel.

    Silo sucht hier nach einer vorhandenen Anfrage, bevor es eine zweite
    anlegt - seiner eigenen, unter dem Konto des Schluessels.

    ⚠️ **Immer nur die eigenen, auch fuer Administratoren.** Litefin liest
    Details mit dem Schluessel eines Administrators und ohne ``X-Api-User``
    und reicht die Antwort an jeden Fernseher im Haus weiter. Mit allen
    Anfragen darin saehe dort jeder, wer was angefragt hat. Ein Client, der
    alle Anfragen braucht, hat ``GET /request``.
    """
    abfrage = select(MediaRequest).where(
        MediaRequest.media_type == MediaType(art),
        MediaRequest.tmdb_id == tmdb_id,
        MediaRequest.status.in_(requests_service.ACTIVE_STATUSES),
        MediaRequest.user_id == zugang_.nutzer.id,
    )
    return [
        form.anfrage(z, ist_4k=_ist_4k(z.fassung_kennung), mit_medium=False)
        for z in db.scalars(abfrage.order_by(MediaRequest.id))
    ]


def _videos(roh: dict[str, Any]) -> list[dict[str, Any]]:
    seiten = {"YouTube": "https://www.youtube.com/watch?v=", "Vimeo": "https://vimeo.com/"}
    return [
        {
            "site": v.get("site"),
            "key": v.get("key"),
            "name": v.get("name"),
            "size": v.get("size"),
            "type": v.get("type"),
            "url": (seiten.get(v.get("site") or "", "") + (v.get("key") or "")) if v.get("site") in seiten else None,
        }
        for v in ((roh.get("videos") or {}).get("results") or [])
    ]


def _anbieter(roh: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"iso_3166_1": land, **form.kamel({k: v for k, v in eintrag.items() if k in ("link", "buy", "flatrate")})}
        for land, eintrag in ((roh.get("watch/providers") or {}).get("results") or {}).items()
    ]


async def _detail(db: Session, zugang_: Any, art: str, tmdb_id: int) -> dict[str, Any]:
    settings = _einstellungen(db, zugang_.nutzer)
    _tmdb(settings)
    try:
        roh = await media.roh_ausfuehrlich(db, settings, art, tmdb_id)
        item = await media.detail(db, settings, art, tmdb_id)
    except TmdbError as fehler:
        raise _tmdb_fehler(fehler) from fehler
    items, _ = await discover_router._status_for(db, settings, art, [item], zugang_.nutzer)
    item = items[0]

    weg = {"videos", "recommendations", "keywords", "credits", "aggregate_credits", "external_ids",
           "watch/providers", "release_dates", "content_ratings", "belongs_to_collection", "similar"}
    daten = form.kamel({k: v for k, v in roh.items() if k not in weg})
    daten["mediaType"] = art
    daten["overview"] = item.overview or daten.get("overview") or ""
    daten["relatedVideos"] = _videos(roh)
    daten["watchProviders"] = _anbieter(roh)
    credits = roh.get("credits") or {}
    besetzung = (roh.get("aggregate_credits") or {}).get("cast") if art == "tv" else None
    daten["credits"] = {
        "cast": form.kamel(besetzung if besetzung is not None else credits.get("cast") or []),
        "crew": form.kamel(credits.get("crew") or []),
    }
    schlagworte = roh.get("keywords") or {}
    daten["keywords"] = [
        {"id": s.get("id"), "name": s.get("name")}
        for s in (schlagworte.get("keywords") or schlagworte.get("results") or [])
    ]
    extern = dict(roh.get("external_ids") or {})
    if art == "movie":
        extern.setdefault("imdb_id", roh.get("imdb_id"))
        daten["releases"] = form.kamel(roh.get("release_dates") or {"results": []})
        reihe = roh.get("belongs_to_collection")
        if reihe:
            daten["collection"] = {
                "id": reihe.get("id"),
                "name": reihe.get("name"),
                "posterPath": reihe.get("poster_path"),
                "backdropPath": reihe.get("backdrop_path"),
            }
    else:
        daten["contentRatings"] = form.kamel(roh.get("content_ratings") or {"results": []})
    daten["externalIds"] = form.kamel(extern)

    anfragen_ = _anfragen_zum_titel(db, zugang_, art, tmdb_id)
    info = _medieninfo(item, art, anfragen_)
    if info is not None:
        daten["mediaInfo"] = info
    daten["onUserWatchlist"] = (
        db.scalar(
            select(Favorite.id).where(
                Favorite.user_id == zugang_.nutzer.id,
                Favorite.media_type == MediaType(art),
                Favorite.tmdb_id == tmdb_id,
            )
        )
        is not None
    )
    return daten


@router.get("/movie/{tmdb_id}")
async def film(tmdb_id: int, zugang_: SeerrZugang, db: Db) -> dict[str, Any]:
    return await _detail(db, zugang_, "movie", tmdb_id)


@router.get("/tv/{tmdb_id}")
async def serie(tmdb_id: int, zugang_: SeerrZugang, db: Db) -> dict[str, Any]:
    return await _detail(db, zugang_, "tv", tmdb_id)


async def _verwandte(db: Session, zugang_: Any, art: str, tmdb_id: int, welche: str, seite: int) -> dict[str, Any]:
    settings = _einstellungen(db, zugang_.nutzer)
    # Erst die Altersfrage am Ausgangstitel: Wer ihn nicht sehen darf, bekommt
    # auch nicht zu sehen, was ihm aehnelt.
    try:
        await media.detail(db, settings, art, tmdb_id)
    except TmdbError as fehler:
        raise _tmdb_fehler(fehler) from fehler
    roh = await _roh(db, settings, f"/{art}/{tmdb_id}/{welche}", {"page": seite}, cache.DISCOVER_TTL)
    return await _seite(db, settings, zugang_.nutzer, roh, art)


@router.get("/movie/{tmdb_id}/similar")
async def film_aehnlich(tmdb_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _verwandte(db, zugang_, "movie", tmdb_id, "similar", _seitennummer(page))


@router.get("/movie/{tmdb_id}/recommendations")
async def film_empfehlungen(tmdb_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _verwandte(db, zugang_, "movie", tmdb_id, "recommendations", _seitennummer(page))


@router.get("/tv/{tmdb_id}/similar")
async def serie_aehnlich(tmdb_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _verwandte(db, zugang_, "tv", tmdb_id, "similar", _seitennummer(page))


@router.get("/tv/{tmdb_id}/recommendations")
async def serie_empfehlungen(tmdb_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _verwandte(db, zugang_, "tv", tmdb_id, "recommendations", _seitennummer(page))


@router.get("/movie/{tmdb_id}/ratingscombined")
@router.get("/movie/{tmdb_id}/ratings")
@router.get("/tv/{tmdb_id}/ratingscombined")
@router.get("/tv/{tmdb_id}/ratings")
def bewertungen(tmdb_id: int, zugang_: SeerrZugang) -> dict[str, Any]:
    """Rotten Tomatoes und IMDb fragt Nexview nicht ab - leer statt erfunden."""
    return {}


# --- Suche und Entdecken -----------------------------------------------------


@router.get("/search")
async def suche(zugang_: SeerrZugang, db: Db, query: str = "", page: int = 1) -> dict[str, Any]:
    settings = _einstellungen(db, zugang_.nutzer)
    if not query.strip():
        return form.seite([], seite_nr=1, seiten=1, gesamt=0)
    roh = await _roh(
        db,
        settings,
        "/search/multi",
        {"query": query.strip(), "page": _seitennummer(page), "include_adult": "false"},
        cache.SEARCH_TTL,
    )
    return await _seite(db, settings, zugang_.nutzer, roh)


@router.get("/discover/trending")
async def trends(zugang_: SeerrZugang, db: Db, page: int = 1, timeWindow: str = "day") -> dict[str, Any]:  # noqa: N803
    settings = _einstellungen(db, zugang_.nutzer)
    zeitraum = "week" if timeWindow == "week" else "day"
    roh = await _roh(db, settings, f"/trending/all/{zeitraum}", {"page": _seitennummer(page)}, cache.DISCOVER_TTL)
    return await _seite(db, settings, zugang_.nutzer, roh)


#: Seerrs Filter (``QueryFilterOptions``) -> TMDBs Parameter, je Medienart.
_FILTER_FILM = {
    "genre": "with_genres",
    "keywords": "with_keywords",
    "excludeKeywords": "without_keywords",
    "studio": "with_companies",
    "language": "with_original_language",
    "primaryReleaseDateGte": "primary_release_date.gte",
    "primaryReleaseDateLte": "primary_release_date.lte",
    "withRuntimeGte": "with_runtime.gte",
    "withRuntimeLte": "with_runtime.lte",
    "voteAverageGte": "vote_average.gte",
    "voteAverageLte": "vote_average.lte",
    "voteCountGte": "vote_count.gte",
    "voteCountLte": "vote_count.lte",
    "watchProviders": "with_watch_providers",
    "watchRegion": "watch_region",
    "certification": "certification",
    "certificationGte": "certification.gte",
    "certificationLte": "certification.lte",
    "certificationCountry": "certification_country",
}
_FILTER_SERIE = {
    **{k: v for k, v in _FILTER_FILM.items() if not k.startswith("primaryRelease") and k != "studio"},
    "network": "with_networks",
    "firstAirDateGte": "first_air_date.gte",
    "firstAirDateLte": "first_air_date.lte",
    "status": "with_status",
}

_SORTIERUNG = {
    "popularity.desc", "popularity.asc", "vote_average.desc", "vote_average.asc", "vote_count.desc",
    "vote_count.asc", "primary_release_date.desc", "primary_release_date.asc", "first_air_date.desc",
    "first_air_date.asc", "revenue.desc", "revenue.asc", "original_title.asc", "original_title.desc",
    "title.asc", "title.desc", "name.asc", "name.desc",
}


async def _entdecken(
    db: Session, zugang_: Any, art: str, abfrage: dict[str, Any], seite: int, **zusatz: Any
) -> dict[str, Any]:
    settings = _einstellungen(db, zugang_.nutzer)
    tabelle = _FILTER_FILM if art == "movie" else _FILTER_SERIE
    params: dict[str, Any] = {tabelle[k]: v for k, v in abfrage.items() if k in tabelle and v not in (None, "")}
    sortierung = abfrage.get("sortBy")
    params["sort_by"] = sortierung if sortierung in _SORTIERUNG else "popularity.desc"
    params["include_adult"] = "false"
    params["page"] = seite
    roh = await _roh(db, settings, f"/discover/{art}", params, cache.DISCOVER_TTL)
    return await _seite(db, settings, zugang_.nutzer, roh, art, **zusatz)


def _abfrage(request_params: Any) -> dict[str, Any]:
    return {k: v for k, v in request_params.items() if k != "page"}



@router.get("/discover/movies")
async def filme(request: Request, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _entdecken(db, zugang_, "movie", _abfrage(request.query_params), _seitennummer(page))


@router.get("/discover/tv")
async def serien(request: Request, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _entdecken(db, zugang_, "tv", _abfrage(request.query_params), _seitennummer(page))


@router.get("/discover/movies/upcoming")
async def filme_demnaechst(zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    heute = date.today().isoformat()
    return await _entdecken(db, zugang_, "movie", {"primaryReleaseDateGte": heute}, _seitennummer(page))


@router.get("/discover/tv/upcoming")
async def serien_demnaechst(zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    heute = date.today().isoformat()
    return await _entdecken(db, zugang_, "tv", {"firstAirDateGte": heute}, _seitennummer(page))


@router.get("/discover/movies/genre/{genre_id}")
async def filme_genre(genre_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _entdecken(db, zugang_, "movie", {"genre": genre_id}, _seitennummer(page))


@router.get("/discover/tv/genre/{genre_id}")
async def serien_genre(genre_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _entdecken(db, zugang_, "tv", {"genre": genre_id}, _seitennummer(page))


@router.get("/discover/movies/language/{sprache}")
async def filme_sprache(sprache: str, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _entdecken(db, zugang_, "movie", {"language": sprache}, _seitennummer(page))


@router.get("/discover/tv/language/{sprache}")
async def serien_sprache(sprache: str, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _entdecken(db, zugang_, "tv", {"language": sprache}, _seitennummer(page))


@router.get("/discover/movies/studio/{studio_id}")
async def filme_studio(studio_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    settings = _einstellungen(db, zugang_.nutzer)
    studio = await _roh(db, settings, f"/company/{studio_id}", {}, cache.DETAIL_TTL)
    return await _entdecken(
        db,
        zugang_,
        "movie",
        {"studio": studio_id},
        _seitennummer(page),
        studio={
            "id": studio.get("id"),
            "name": studio.get("name"),
            "logoPath": studio.get("logo_path"),
            "originCountry": studio.get("origin_country"),
            "description": studio.get("description"),
            "headquarters": studio.get("headquarters"),
            "homepage": studio.get("homepage"),
        },
    )


@router.get("/discover/tv/network/{network_id}")
async def serien_sender(network_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    settings = _einstellungen(db, zugang_.nutzer)
    sender = await _roh(db, settings, f"/network/{network_id}", {}, cache.DETAIL_TTL)
    return await _entdecken(
        db,
        zugang_,
        "tv",
        {"network": network_id},
        _seitennummer(page),
        network={
            "id": sender.get("id"),
            "name": sender.get("name"),
            "logoPath": sender.get("logo_path"),
            "originCountry": sender.get("origin_country"),
            "headquarters": sender.get("headquarters"),
            "homepage": sender.get("homepage"),
        },
    )


@router.get("/discover/keyword/{keyword_id}/movies")
async def filme_schlagwort(keyword_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    return await _entdecken(db, zugang_, "movie", {"keywords": keyword_id}, _seitennummer(page))


async def _genreleiste(db: Session, zugang_: Any, art: str) -> list[dict[str, Any]]:
    """Seerrs Genre-Leiste: je Genre ein paar Hintergruende zum Durchblaettern.

    ⚠️ Die Bilder kommen aus der gesiebten Liste, nicht aus TMDBs roher
    Antwort: Ein Hintergrundbild ist auch ein Titel, und ein gesperrter
    Titel soll nicht als Kachelbild durch die Hintertuer kommen.
    """
    settings = _einstellungen(db, zugang_.nutzer)
    genres = await _roh(db, settings, f"/genre/{art}/list", {}, cache.DETAIL_TTL)
    leiste = []
    for genre in genres.get("genres") or []:
        seite = await _entdecken(db, zugang_, art, {"genre": genre.get("id")}, 1)
        leiste.append(
            {
                "id": genre.get("id"),
                "name": genre.get("name"),
                "backdrops": [t["backdropPath"] for t in seite["results"] if t.get("backdropPath")],
            }
        )
    return sorted(leiste, key=lambda g: str(g.get("name") or ""))


@router.get("/discover/genreslider/movie")
async def genreleiste_filme(zugang_: SeerrZugang, db: Db) -> list[dict[str, Any]]:
    return await _genreleiste(db, zugang_, "movie")


@router.get("/discover/genreslider/tv")
async def genreleiste_serien(zugang_: SeerrZugang, db: Db) -> list[dict[str, Any]]:
    return await _genreleiste(db, zugang_, "tv")


@router.get("/genres/movie")
@router.get("/genres/tv")
async def genres(request: Request, zugang_: SeerrZugang, db: Db) -> list[dict[str, Any]]:
    art = "movie" if request.url.path.endswith("/movie") else "tv"
    settings = _einstellungen(db, zugang_.nutzer)
    roh = await _roh(db, settings, f"/genre/{art}/list", {}, cache.DETAIL_TTL)
    return [{"id": g.get("id"), "name": g.get("name")} for g in roh.get("genres") or []]


# --- Personen und Sammlungen -------------------------------------------------


@router.get("/person/{person_id}")
async def person(person_id: int, zugang_: SeerrZugang, db: Db) -> dict[str, Any]:
    settings = _einstellungen(db, zugang_.nutzer)
    roh = await _roh(db, settings, f"/person/{person_id}", {}, cache.DETAIL_TTL)
    felder = (
        "id", "name", "birthday", "deathday", "known_for_department", "also_known_as", "gender", "biography",
        "popularity", "place_of_birth", "profile_path", "adult", "imdb_id", "homepage",
    )
    return form.kamel({k: roh.get(k) for k in felder})


@router.get("/person/{person_id}/combined_credits")
async def person_werke(person_id: int, zugang_: SeerrZugang, db: Db) -> dict[str, Any]:
    settings = _einstellungen(db, zugang_.nutzer)
    roh = await _roh(db, settings, f"/person/{person_id}/combined_credits", {}, cache.DETAIL_TTL)
    return {
        "id": person_id,
        "cast": await _ergebnisse(db, settings, zugang_.nutzer, list(roh.get("cast") or [])),
        "crew": await _ergebnisse(db, settings, zugang_.nutzer, list(roh.get("crew") or [])),
    }


@router.get("/collection/{sammlung_id}")
async def sammlung(sammlung_id: int, zugang_: SeerrZugang, db: Db) -> dict[str, Any]:
    settings = _einstellungen(db, zugang_.nutzer)
    roh = await _roh(db, settings, f"/collection/{sammlung_id}", {}, cache.DETAIL_TTL)
    teile = sorted(roh.get("parts") or [], key=lambda t: str(t.get("release_date") or "9999"))
    erlaubt = await _ergebnisse(db, settings, zugang_.nutzer, teile, "movie")
    # Name, Text und Plakat einer Reihe sind die ihrer Filme. Ist keiner davon
    # erlaubt, gibt es die Reihe fuer dieses Konto nicht - wie bei einem
    # gesperrten Titel.
    if teile and not erlaubt:
        raise SeerrFehler(404, "Not found.")
    return {
        "id": roh.get("id"),
        "name": roh.get("name"),
        "overview": roh.get("overview"),
        "posterPath": roh.get("poster_path"),
        "backdropPath": roh.get("backdrop_path"),
        "parts": erlaubt,
    }


# --- Zuletzt hinzugefuegt -----------------------------------------------------


@router.get("/media")
async def medien(
    zugang_: SeerrZugang,
    db: Db,
    take: Annotated[int, Query(ge=1, le=100)] = 20,
    skip: Annotated[int, Query(ge=0)] = 0,
    filter: str | None = None,  # noqa: A002 - Seerrs Name
    sort: str | None = None,
) -> dict[str, Any]:
    """Was zuletzt in die Bibliothek kam (``filter=allavailable&sort=mediaAdded``).

    Quelle ist Nexviews Abbild des Medienservers, nicht die Anfragen: Auch was
    ohne Anfrage ins Haus kam, ist "neu hinzugefuegt". Jeder Titel einmal, mit
    seinem juengsten Zeitpunkt.

    ⚠️ Auch diese Liste geht durch die Altersfrage. Sie nennt nur Kennungen,
    aber ein Client holt danach die Details - und schon die Kennung eines
    gesperrten Titels in "Neu hinzugefuegt" waere eine Auskunft, die das Konto
    sonst nirgends bekommt. Gesiebt wird in Bloecken, bis die Seite voll ist.
    """
    settings = _einstellungen(db, zugang_.nutzer)
    neueste = func.max(MediaServerLibraryItem.added_at).label("neueste")
    zeilen = list(
        db.execute(
            select(
                MediaServerLibraryItem.media_type,
                MediaServerLibraryItem.tmdb_id,
                func.max(MediaServerLibraryItem.tvdb_id),
                func.max(MediaServerLibraryItem.imdb_id),
                func.max(MediaServerLibraryItem.has_uhd),
                func.max(MediaServerLibraryItem.has_standard),
                neueste,
            )
            .where(MediaServerLibraryItem.tmdb_id.is_not(None))
            .group_by(MediaServerLibraryItem.media_type, MediaServerLibraryItem.tmdb_id)
            .order_by(neueste.desc().nulls_last(), MediaServerLibraryItem.tmdb_id.desc())
        )
    )

    gebraucht = skip + take
    erlaubt: list[Any] = []
    if settings.age_limit is None:
        erlaubt = zeilen[:gebraucht]
        gesamt = len(zeilen)
    else:
        block = 50
        for start in range(0, len(zeilen), block):
            stueck = zeilen[start : start + block]
            frei: set[tuple[str, int]] = set()
            for art in ("movie", "tv"):
                kennungen = [z[1] for z in stueck if z[0].value == art]
                if kennungen:
                    frei |= {(art, k) for k in await media.erlaubte_kennungen(db, settings, art, kennungen)}
            erlaubt.extend(z for z in stueck if (z[0].value, z[1]) in frei)
            if len(erlaubt) > gebraucht:
                break
        # Die Gesamtzahl ist nach dem Sieb nur bis hierher bekannt. Ein "es
        # gibt mehr" reicht einem Client zum Weiterblaettern.
        gesamt = len(erlaubt) + (1 if len(erlaubt) > gebraucht else 0)

    ergebnisse = []
    for art, tmdb_id, tvdb_id, imdb_id, hat_uhd, hat_standard, hinzu in erlaubt[skip:gebraucht]:
        zeitpunkt = form.zeit(hinzu)
        ergebnisse.append(
            {
                "id": tmdb_id,
                "mediaType": art.value,
                "tmdbId": tmdb_id,
                "tvdbId": tvdb_id,
                "imdbId": imdb_id,
                "status": form.MEDIUM_DA if (hat_standard or not hat_uhd) else form.MEDIUM_UNBEKANNT,
                "status4k": form.MEDIUM_DA if hat_uhd else form.MEDIUM_UNBEKANNT,
                "createdAt": zeitpunkt,
                "updatedAt": zeitpunkt,
                "mediaAddedAt": zeitpunkt,
                "downloadStatus": [],
                "downloadStatus4k": [],
                "seasons": [],
            }
        )
    return form.seitenangabe(ergebnisse, gesamt=gesamt, take=take, skip=skip)


# --- Nutzer -------------------------------------------------------------------


def _ist_ich_oder_verwalter(zugang_: Any, user_id: int) -> None:
    if zugang_.nutzer.id != user_id and zugang_.nutzer.role != Role.admin:
        raise SeerrFehler(403, "You do not have permission to access this endpoint.")


@router.get("/user")
def nutzerliste(
    zugang_: SeerrZugang,
    db: Db,
    take: Annotated[int, Query(ge=1, le=1000)] = 10,
    skip: Annotated[int, Query(ge=0)] = 0,
    sort: str | None = None,
) -> dict[str, Any]:
    """Alle aktiven Konten ausser Kindern - nur fuer Administratoren, wie bei Seerr.

    Silo und Litefin lesen hier, wem eine Anfrage gehoert. Zugeordnet wird
    ueber ``jellyfinUserId``; Mailadresse und Benutzername bleiben leer, siehe
    ``form.nutzer``.
    """
    if zugang_.nutzer.role != Role.admin:
        raise SeerrFehler(403, "You do not have permission to access this endpoint.")
    abfrage = select(User).where(User.is_active.is_(True), User.role != Role.child)
    gesamt = db.scalar(select(func.count()).select_from(abfrage.subquery())) or 0
    if sort == "displayname":
        abfrage = abfrage.order_by(func.lower(func.coalesce(User.display_name, User.username)), User.id)
    elif sort == "created":
        abfrage = abfrage.order_by(User.created_at.desc(), User.id)
    else:
        abfrage = abfrage.order_by(User.id)
    konten = list(db.scalars(abfrage.offset(skip).limit(take)))
    return form.seitenangabe([_nutzerform(db, k) for k in konten], gesamt=int(gesamt), take=take, skip=skip)


@router.post("/user")
def nutzer_anlegen(zugang_: SeerrZugang) -> None:
    raise SeerrFehler(403, "Nexview does not create accounts through the Seerr API. Invite the person in Nexview.")


@router.get("/user/{user_id}")
def nutzer_(user_id: int, zugang_: SeerrZugang, db: Db) -> dict[str, Any]:
    _ist_ich_oder_verwalter(zugang_, user_id)
    konto = db.get(User, user_id)
    if konto is None or not konto.is_active or konto.role == Role.child:
        raise SeerrFehler(404, "User not found.")
    return _nutzerform(db, konto)


@router.get("/user/{user_id}/watchlist")
async def nutzer_merkliste(user_id: int, zugang_: SeerrZugang, db: Db, page: int = 1) -> dict[str, Any]:
    """Seerrs Merkliste ist bei Nexview "Mag ich" (Favoriten).

    Gelesen ueber ``favorites.my_favorites``: Die siebt nach der Altersgrenze
    des Kontos, dem die Liste gehoert.
    """
    _ist_ich_oder_verwalter(zugang_, user_id)
    konto = db.get(User, user_id)
    if konto is None or not konto.is_active or konto.role == Role.child:
        raise SeerrFehler(404, "User not found.")
    eintraege = await favoriten.my_favorites(konto, db)
    seite = _seitennummer(page)
    stueck = eintraege[(seite - 1) * MERKLISTE_SEITE : seite * MERKLISTE_SEITE]
    return form.seite(
        [
            {
                "id": e.id,
                "ratingKey": "",
                "title": e.title,
                "mediaType": e.media_type.value,
                "tmdbId": e.tmdb_id,
            }
            for e in stueck
        ],
        seite_nr=seite,
        seiten=max(1, (len(eintraege) + MERKLISTE_SEITE - 1) // MERKLISTE_SEITE),
        gesamt=len(eintraege),
    )


@router.post("/watchlist", status_code=201)
def merken(zugang_: SeerrZugang, db: Db, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    art = body.get("mediaType")
    if art not in ("movie", "tv"):
        raise SeerrFehler(400, "mediaType must be movie or tv.")
    try:
        tmdb_id = int(body.get("tmdbId"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SeerrFehler(400, "tmdbId must be a TMDB id.") from None
    if tmdb_id < 1:
        raise SeerrFehler(400, "tmdbId must be a TMDB id.")
    titel = str(body.get("title") or "")[:300]
    eintrag = favoriten.add_favorite(
        favoriten.FavoriteIn(media_type=MediaType(art), tmdb_id=tmdb_id, title=titel),
        zugang_.nutzer,
        db,
    )
    return {
        "id": eintrag.id,
        "tmdbId": eintrag.tmdb_id,
        "mediaType": eintrag.media_type.value,
        "title": eintrag.title,
        "ratingKey": "",
        "createdAt": form.zeit(eintrag.created_at),
        "updatedAt": form.zeit(eintrag.created_at),
    }


@router.delete("/watchlist/{tmdb_id}", status_code=204)
def vergessen(tmdb_id: int, zugang_: SeerrZugang, db: Db, mediaType: str | None = None) -> Response:  # noqa: N803
    if mediaType not in ("movie", "tv"):
        raise SeerrFehler(400, "Invalid mediaType query parameter.")
    eintrag = db.scalar(
        select(Favorite).where(
            Favorite.user_id == zugang_.nutzer.id,
            Favorite.media_type == MediaType(mediaType),
            Favorite.tmdb_id == tmdb_id,
        )
    )
    if eintrag is None:
        raise SeerrFehler(404, "Not on the watchlist.")
    db.delete(eintrag)
    db.commit()
    return Response(status_code=204)
