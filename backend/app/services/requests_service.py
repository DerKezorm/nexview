"""Der Weg einer Anfrage: pruefen, speichern und an Radarr/Sonarr uebergeben."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    MediaRequest,
    MediaType,
    Notification,
    NotificationType,
    RequestStatus,
    Role,
    User,
    utcnow,
)
from ..schemas_media import MediaItem
from . import library, notify, quota
from .arr import ArrError
from .sonarr import normalize_title
from .settings_service import AppSettings

logger = logging.getLogger("nexview.requests")

__all__ = [
    "ACTIVE_STATUSES",
    "RequestError",
    "badges_for",
    "cancel",
    "create_request",
    "find_active",
    "push_to_arr",
    "resolve_root_folder",
    "requester_tag",
    "withdraw",
]

# Zustaende, in denen eine Anfrage als "laeuft noch oder ist erledigt" gilt.
ACTIVE_STATUSES = (
    RequestStatus.pending_approval,
    RequestStatus.approved,
    RequestStatus.searching,
    RequestStatus.downloaded,
)


class RequestError(Exception):
    """Fachlicher Fehler mit lesbarer Meldung und passendem HTTP-Code."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def find_active(
    db: Session, media_type: MediaType, tmdb_id: int, season: int | None = None
) -> MediaRequest | None:
    """Laeuft zu diesem Titel schon eine Anfrage?

    Bei Serien zaehlt die Staffel mit: Staffel 3 anzufragen muss moeglich
    bleiben, obwohl Staffel 2 schon laeuft. Eine Anfrage ueber die **ganze**
    Serie deckt dagegen jede einzelne Staffel mit ab - sonst liesse sich
    dasselbe zweimal bestellen.
    """
    bedingungen = [
        MediaRequest.media_type == media_type,
        MediaRequest.tmdb_id == tmdb_id,
        MediaRequest.status.in_(ACTIVE_STATUSES),
    ]

    if media_type == MediaType.tv and season is not None:
        bedingungen.append(
            (MediaRequest.season == season) | (MediaRequest.season.is_(None))
        )

    return db.scalar(select(MediaRequest).where(*bedingungen))


# Wie ein Anfrage-Zustand auf dem Badge heisst.
BADGE_FOR_STATUS = {
    RequestStatus.pending_approval: "pending_approval",
    RequestStatus.approved: "requested",
    RequestStatus.searching: "searching",
    RequestStatus.downloaded: "downloaded",
    RequestStatus.failed: "failed",
}


def badges_for(
    db: Session, media_type: MediaType, tmdb_ids: list[int]
) -> dict[int, str]:
    """Eigene Anfragen zu diesen Titeln - fuer die Badges auf den Kacheln.

    Ohne das saehe ein Titel, den jemand angefragt hat und der auf Freigabe
    wartet, fuer alle weiterhin wie "nicht angefragt" aus.
    """
    if not tmdb_ids:
        return {}

    rows = db.scalars(
        select(MediaRequest).where(
            MediaRequest.media_type == media_type,
            MediaRequest.tmdb_id.in_(tmdb_ids),
            MediaRequest.status.in_(ACTIVE_STATUSES + (RequestStatus.failed,)),
        )
    )
    return {row.tmdb_id: BADGE_FOR_STATUS.get(row.status, "requested") for row in rows}


def _notify_admins(db: Session, request: MediaRequest) -> None:
    """Alle, die freigeben duerfen, ueber eine wartende Anfrage informieren.

    Der Anfragende selbst bleibt aussen vor: waer er Entscheider, waere seine
    Anfrage ohnehin sofort freigegeben - und eine Meldung von sich an sich
    braucht niemand.
    """
    notify.create_for_approvers(
        db,
        kind=NotificationType.request_pending,
        message_key="notifications.requestPending",
        request=request,
        ausser=request.user_id,
    )


def clear_pending_notice(db: Session, request: MediaRequest) -> None:
    """Die Meldung "wartet auf Freigabe" entfernen, sobald entschieden wurde.

    Ohne das steht bei den uebrigen Entscheidern noch tagelang eine Aufgabe in
    der Glocke, die laengst jemand anderes erledigt hat.
    """
    db.query(Notification).filter(
        Notification.request_id == request.id,
        Notification.type == NotificationType.request_pending,
    ).delete(synchronize_session=False)


def requester_tag(username: str) -> str:
    """Etikett, das in Radarr/Sonarr zeigt, wer den Titel angefordert hat."""
    return f"nexview-{username.lower()}"


async def _sonarr_eintrag(settings: AppSettings, request: MediaRequest):
    """Kennt Sonarr diese Serie bereits? Sonst ``None``.

    Erst ueber die TVDB-Kennung, ersatzweise ueber den normalisierten Titel -
    fuer viele neue Serien kennt TMDB noch keine TVDB-Kennung.
    """
    nach_tvdb, nach_titel = await library.series_library(settings)
    if request.tvdb_id:
        treffer = nach_tvdb.get(request.tvdb_id)
        if treffer is not None:
            return treffer
    return nach_titel.get(normalize_title(request.title))


async def push_to_arr(db: Session, settings: AppSettings, request: MediaRequest) -> MediaRequest:
    """Freigegebene Anfrage tatsaechlich an Radarr bzw. Sonarr uebergeben."""
    try:
        if request.media_type == MediaType.movie:
            client = library.radarr_client(settings)
            if client is None:
                raise ArrError("Radarr ist nicht eingerichtet.")
            tag_id = await client.ensure_tag(requester_tag(request.user.username))
            created = await client.add(
                request.tmdb_id,
                request.quality_profile_id or 0,
                request.root_folder_path or "",
                tag_ids=[tag_id] if tag_id else None,
            )
        else:
            client = library.sonarr_client(settings)
            if client is None:
                raise ArrError("Sonarr ist nicht eingerichtet.")
            if not request.tvdb_id:
                raise ArrError(
                    "Für diese Serie kennt TMDB noch keine TVDB-Kennung - "
                    "Sonarr kann sie deshalb nicht anlegen."
                )

            # Liegt die Serie schon in Sonarr, wird sie nicht neu angelegt -
            # das brächte die vorhandenen Folgen durcheinander. Stattdessen
            # wird nur die gewünschte Staffel aktiviert und gesucht.
            vorhanden = await _sonarr_eintrag(settings, request)
            if vorhanden is not None and request.season is not None:
                await client.monitor_season(vorhanden.arr_id, request.season)
                created = {"id": vorhanden.arr_id}
            else:
                tag_id = await client.ensure_tag(requester_tag(request.user.username))
                created = await client.add(
                    request.tvdb_id,
                    request.quality_profile_id or 0,
                    request.root_folder_path or "",
                    tag_ids=[tag_id] if tag_id else None,
                    season=request.season,
                )
    except ArrError as error:
        request.status = RequestStatus.failed
        request.error_message = error.message
        db.commit()
        logger.error(
            "Could not add %r (tmdb=%s) for user %r: %s",
            request.title,
            request.tmdb_id,
            request.user.username,
            error.message,
        )
        raise RequestError(error.message, 502) from error

    request.arr_id = created.get("id") if isinstance(created, dict) else None
    request.status = RequestStatus.searching
    request.error_message = None
    request.last_checked_at = utcnow()
    db.commit()

    logger.info(
        "Added %s %r (tmdb=%s) to %s for user %r",
        request.media_type.value,
        request.title,
        request.tmdb_id,
        "Radarr" if request.media_type == MediaType.movie else "Sonarr",
        request.user.username,
    )

    # Die Bibliothek hat sich geaendert - Badges sollen das sofort zeigen.
    library.invalidate()
    return request


async def resolve_root_folder(
    settings: AppSettings, media_type: str, gewuenscht: str | None
) -> str:
    """Welcher Zielordner gilt fuer diese Anfrage?

    Drei Faelle:

    * Der Administrator hat die Auswahl **abgeschaltet** - dann gilt sein
      Ordner, egal was mitgeschickt wurde. Ein Aufruf am Formular vorbei darf
      sich keinen anderen Ordner aussuchen koennen.
    * Die Auswahl ist erlaubt und ein Ordner wurde mitgeschickt - dann wird
      geprueft, ob es ihn in Radarr/Sonarr ueberhaupt gibt. Sonst koennte ein
      selbstgebauter Aufruf einen beliebigen Pfad auf dem Server erzeugen.
    * Nichts mitgeschickt - dann der Standard des Administrators, ersatzweise
      der erste vorhandene Ordner.
    """
    try:
        vorhanden = [
            eintrag["path"]
            for eintrag in (await library.options(settings, media_type)).get("root_folders", [])
            if eintrag.get("path")
        ]
    except ArrError as fehler:
        raise RequestError(fehler.message, 502) from fehler

    if not vorhanden:
        raise RequestError(
            "In Radarr bzw. Sonarr ist kein Zielordner eingerichtet."
            if media_type == "movie"
            else "In Sonarr ist kein Zielordner eingerichtet.",
            409,
        )

    standard = settings.default_root(media_type)

    if not settings.root_folder_choice:
        # Der eingestellte Ordner kann inzwischen aus Radarr/Sonarr verschwunden
        # sein - dann lieber der erste vorhandene als ein Fehlschlag beim
        # Hinzufuegen.
        return standard if standard in vorhanden else vorhanden[0]

    if gewuenscht:
        if gewuenscht not in vorhanden:
            raise RequestError("Diesen Zielordner gibt es nicht.", 422)
        return gewuenscht

    return standard if standard in vorhanden else vorhanden[0]


async def create_request(
    db: Session,
    settings: AppSettings,
    user: User,
    item: MediaItem,
    quality_profile_id: int,
    root_folder_path: str | None = None,
    season: int | None = None,
) -> MediaRequest:
    """Neue Anfrage anlegen - inklusive aller Vorpruefungen.

    ``root_folder_path`` ist nur ein *Wunsch*. Welcher Ordner tatsaechlich
    gilt, entscheidet ``resolve_root_folder`` weiter unten - und zwar erst,
    nachdem feststeht, dass Radarr bzw. Sonarr ueberhaupt eingerichtet sind.
    Sonst bekaeme jemand ohne eingerichteten Dienst einen Verbindungsfehler
    statt der verstaendlichen Meldung, dass noch nichts eingerichtet ist.
    """
    media_type = MediaType(item.media_type)
    # Eine Staffel ergibt nur bei Serien Sinn - bei Filmen wird sie still
    # verworfen statt mit einem Fehler abgelehnt.
    if media_type != MediaType.tv:
        season = None

    # Ohne Radarr/Sonarr koennte aus der Anfrage nie etwas werden. Lieber
    # gleich sagen als eine Anfrage anlegen, die spaeter ins Leere laeuft.
    if media_type == MediaType.movie and not settings.radarr_configured:
        raise RequestError(
            "Radarr ist noch nicht eingerichtet - Filme können deshalb nicht "
            "angefragt werden. Der Administrator trägt die Zugangsdaten unter "
            "Einstellungen ein.",
            409,
        )
    if media_type == MediaType.tv and not settings.sonarr_configured:
        raise RequestError(
            "Sonarr ist noch nicht eingerichtet - Serien können deshalb nicht "
            "angefragt werden. Der Administrator trägt die Zugangsdaten unter "
            "Einstellungen ein.",
            409,
        )

    existing = find_active(db, media_type, item.tmdb_id, season)
    if existing is not None:
        if season is not None and existing.season is None:
            raise RequestError(
                f"„{item.title}“ ist bereits komplett angefragt - Staffel {season} ist damit abgedeckt.",
                409,
            )
        raise RequestError(
            f"„{item.title}“ wurde bereits angefragt."
            if season is None
            else f"Staffel {season} von „{item.title}“ wurde bereits angefragt.",
            409,
        )

    # Schon in der Bibliothek? Dann waere die Anfrage sinnlos.
    # Bei einer einzelnen Staffel ist das kein Ausschluss: die Serie liegt
    # ja gerade deshalb schon da, weil die vorherigen Staffeln geladen sind.
    if season is None:
        matched = await library.apply_status(settings, item.media_type, [item])
        current = matched.items[0]
        if current.status in ("downloaded", "searching"):
            raise RequestError(
                f"„{item.title}“ ist bereits in deiner Bibliothek.",
                409,
            )

    if quality_profile_id in user.blocked_profiles(media_type):
        raise RequestError(
            "Dieses Qualitätsprofil ist für dich gesperrt. Bitte wähle ein anderes.",
            403,
        )

    zielordner = await resolve_root_folder(settings, item.media_type, root_folder_path)

    state = quota.state_for(db, user, media_type)
    if state.exhausted:
        art = "Filme" if media_type == MediaType.movie else "Serien"
        raise RequestError(
            f"Dein Kontingent für {art} ist aufgebraucht ({state.limit} pro "
            f"{_period_label(state.period.value)}).",
            429,
        )

    sofort = user.effective_auto_approve
    request = MediaRequest(
        user_id=user.id,
        media_type=media_type,
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        title=item.title,
        poster_path=item.poster_url,
        release_date=item.release_date,
        quality_profile_id=quality_profile_id,
        root_folder_path=zielordner,
        season=season,
        status=RequestStatus.approved if sofort else RequestStatus.pending_approval,
    )
    if sofort:
        request.approved_by = user.id
        request.approved_at = utcnow()

    db.add(request)
    db.commit()
    db.refresh(request)

    if sofort:
        return await push_to_arr(db, settings, request)

    _notify_admins(db, request)
    db.commit()
    return request


def _period_label(period: str) -> str:
    return {"day": "Tag", "week": "Woche", "month": "Monat"}.get(period, period)


async def cancel(
    db: Session, settings: AppSettings, request: MediaRequest
) -> MediaRequest:
    """Eine laufende Anfrage abbrechen.

    Der Titel wird in Radarr/Sonarr geloescht - samt bereits geladener
    Dateien -, und das Kontingent des Anfragenden wird wieder frei, weil
    ``cancelled`` nicht mitgezaehlt wird.
    """
    if request.status not in (RequestStatus.approved, RequestStatus.searching):
        raise RequestError(
            "Nur laufende Anfragen können abgebrochen werden.",
            409,
        )

    if request.arr_id:
        client = (
            library.radarr_client(settings)
            if request.media_type == MediaType.movie
            else library.sonarr_client(settings)
        )
        if client is not None:
            try:
                await client.remove(request.arr_id, delete_files=True)
            except ArrError as error:
                # 404 heisst: dort schon weg - dann ist das Ziel ja erreicht.
                if error.status_code != 404:
                    raise RequestError(error.message, 502) from error

    request.status = RequestStatus.cancelled
    request.completed_at = utcnow()
    request.arr_id = None
    db.commit()

    logger.warning(
        "Cancelled %r (tmdb=%s) for user %r and removed it including files",
        request.title,
        request.tmdb_id,
        request.user.username,
    )
    library.invalidate()
    return request


def withdraw(db: Session, user: User, request_id: int) -> None:
    """Eigene, noch nicht freigegebene Anfrage zuruecknehmen."""
    request = db.get(MediaRequest, request_id)
    if request is None or request.user_id != user.id:
        raise RequestError("Anfrage nicht gefunden.", 404)
    if request.status != RequestStatus.pending_approval:
        raise RequestError(
            "Diese Anfrage wurde bereits bearbeitet und kann nicht mehr zurückgenommen werden.",
            409,
        )
    db.delete(request)
    db.commit()
