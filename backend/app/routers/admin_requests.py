"""Freigeben, Ablehnen und die Uebersicht ueber alle Anfragen - nur fuer Admins."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..deps import AdminUser, ApproverUser, DbSession
from ..models import (
    MediaRequest,
    MediaType,
    QualityTier,
    Notification,
    NotificationType,
    RequestStatus,
    TitleRating,
    Role,
    User,
    utcnow,
)
from ..schemas_requests import AnfragerSpeicher, FeedbackReply, RequestWithUser
from ..services import blocklist, media, notify, ratings, requests_service, storage, streaming
from ..services.settings_service import load_settings
from ..services.tmdb import TmdbError, image_url

router = APIRouter(prefix="/api/admin/requests", tags=["admin"])

logger = logging.getLogger("nexview.admin")


class RejectPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    # Den Titel gleich mit auf die Sperrliste setzen. Darf nur ein
    # Administrator - siehe die Pruefung in ``reject``.
    block: bool = False


class TargetChoice(BaseModel):
    """Wohin und in welcher Qualitaet - die Wahl des Entscheiders.

    Nur noetig, wenn ``approver_picks_target`` gesetzt ist: dann laesst der
    Anfragende beides offen. Sonst traegt die Anfrage die Werte schon.
    """

    root_folder_path: str | None = Field(default=None, max_length=500)
    quality_profile_id: int | None = None


class ApproveAllPayload(BaseModel):
    """Ziele fuer die Sammelfreigabe - je Medienart **und** Stufe eines.

    Im selben Stapel koennen Filme und Serien liegen, und jeweils in Standard
    und 4K. Das sind bis zu vier verschiedene Instanzen mit vollkommen
    verschiedenen Ordnern und Profilen; eine einzige Wahl waere fuer drei
    davon zwangslaeufig falsch.
    """

    movie: TargetChoice | None = None
    tv: TargetChoice | None = None
    movie_uhd: TargetChoice | None = None
    tv_uhd: TargetChoice | None = None

    def fuer(self, media_type: MediaType, tier: QualityTier) -> TargetChoice | None:
        art = "movie" if media_type == MediaType.movie else "tv"
        return getattr(self, f"{art}_uhd" if tier == QualityTier.uhd else art)


def _braucht_ziel(request: MediaRequest, wahl: TargetChoice | None) -> bool:
    """Muss vor der Freigabe noch ein Ziel gesetzt werden?

    Zwei Faelle: Der Anfrage fehlt eines (dann *muss* der Entscheider waehlen),
    oder er hat ausdruecklich etwas mitgeschickt (dann *will* er es aendern).
    """
    if request.root_folder_path is None or request.quality_profile_id is None:
        return True
    return bool(wahl and (wahl.root_folder_path or wahl.quality_profile_id))


def _speicher_staende(
    db: Session, anfragen: list[MediaRequest]
) -> dict[int, AnfragerSpeicher]:
    """Speicherstand je Anfragendem - **einmal je Person, nicht je Zeile.**

    In der Freigabeliste stehen von einer Person oft zehn Anfragen; ihren Stand
    zehnmal zu berechnen waere zehnmal dieselbe Summe ueber dieselbe Tabelle.

    Der Stand steht an jeder Zeile, weil der Speicher **immer** mitzaehlt: Wer
    freigibt, soll sehen, wie viel Luft der Anfragende noch hat - auch in einem
    Haushalt, der ueber die Stueckzahl steuert.
    """
    einstellungen = load_settings(db)
    staende: dict[int, AnfragerSpeicher] = {}
    for anfrage in anfragen:
        if anfrage.user_id in staende or anfrage.user is None:
            continue
        grenze = storage.stand_fuer(db, anfrage.user, einstellungen)
        staende[anfrage.user_id] = AnfragerSpeicher(
            used_bytes=grenze.used_bytes,
            limit_bytes=grenze.limit_bytes,
            exhausted=grenze.exhausted,
        )
    return staende


async def _abo_treffer(
    db: Session, anfragen: list[MediaRequest]
) -> dict[int, list[str]]:
    """Welche Anfragen laufen im Abo **ihres Anfragenden**? - je Anfrage-Id.

    Drei Sparmassnahmen, weil hier sonst pro Zeile ein TMDB-Aufruf stuende:

    * Konten ohne hinterlegte Abos fallen sofort raus - das ist eine Abfrage
      ueber eine schmale Tabelle und erledigt in der Regel die Mehrheit.
    * Je Titel wird **einmal** nachgesehen, nicht je Anfrage. Zehn Staffeln
      derselben Serie sind ein Titel.
    * Gefragt wird ueber ``full_detail``, und das liegt fuer alles, was gerade
      angefragt wurde, ohnehin im Zwischenspeicher - jemand hat die
      Detailseite kurz vorher geoeffnet, sonst haette er nicht angefragt.

    Faellt TMDB aus, bleibt die Spalte leer. Ein fehlender Hinweis ist
    hinnehmbar; eine Freigabeliste, die deswegen gar nicht laedt, nicht.
    """
    if not anfragen:
        return {}

    # ⚠️ **Nur, wo noch entschieden wird.**
    #
    # Steht die automatische Freigabe an, war die Anfrage nie auf dem Tisch des
    # Entscheiders - der Titel laeuft laengst durch Radarr. Ihm dann zu sagen,
    # der Anfragende koenne das auch streamen, ist keine Entscheidungshilfe
    # mehr, sondern ein Vorwurf ohne Adressat.
    #
    # Dasselbe gilt fuer alles Abgeschlossene, Abgelehnte und Fehlgeschlagene:
    # Die Freigabeliste zeigt auch die Vergangenheit, und dort ist der Hinweis
    # bestenfalls Laerm. Nebenbei spart die Bedingung die TMDB-Abfrage fuer
    # jede Zeile der gesamten Historie.
    wartend = [a for a in anfragen if a.status == RequestStatus.pending_approval]
    if not wartend:
        return {}

    dienste: dict[int, set[str]] = {}
    for anfrage in wartend:
        if anfrage.user_id in dienste or anfrage.user is None:
            continue
        dienste[anfrage.user_id] = streaming.eigene_dienste(db, anfrage.user)

    offen = [a for a in wartend if dienste.get(a.user_id)]
    if not offen:
        return {}

    einstellungen = load_settings(db)
    anbieter: dict[tuple[str, int], list[int]] = {}
    for anfrage in offen:
        schluessel = (anfrage.media_type.value, anfrage.tmdb_id)
        if schluessel in anbieter:
            continue
        try:
            detail = await media.full_detail(
                db, einstellungen, anfrage.media_type.value, anfrage.tmdb_id
            )
        except TmdbError:
            anbieter[schluessel] = []
            continue
        anbieter[schluessel] = [
            eintrag.id for eintrag in (detail.watch.flatrate if detail.watch else [])
        ]

    ergebnis: dict[int, list[str]] = {}
    for anfrage in offen:
        namen = streaming.treffer(
            dienste[anfrage.user_id],
            anbieter.get((anfrage.media_type.value, anfrage.tmdb_id), []),
        )
        if namen:
            ergebnis[anfrage.id] = namen
    return ergebnis


def _with_user(
    request: MediaRequest,
    speicher: AnfragerSpeicher | None = None,
    abos: list[str] | None = None,
    bewertung=None,
) -> RequestWithUser:
    vom_benutzer = (
        "username",
        "display_name",
        "avatar_url",
        "storage",
        "requester_subscriptions",
    )
    zeile = RequestWithUser(
        **{
            field: getattr(request, field)
            for field in RequestWithUser.model_fields
            if field not in vom_benutzer
        },
        username=request.user.username,
        display_name=request.user.display_name,
        avatar_url=request.user.avatar_url,
        storage=speicher,
        requester_subscriptions=abos or [],
    )
    # Die Rueckmeldung haengt am Titel, nicht an der Anfrage - siehe
    # ``models.TitleRating``. Die gleichnamigen Spalten hier sind tot.
    zeile.rating = bewertung.rating if bewertung else None
    zeile.feedback = bewertung.comment if bewertung else None
    zeile.feedback_reply = bewertung.reply if bewertung else None
    zeile.rated_at = bewertung.updated_at if bewertung else None
    zeile.replied_at = bewertung.replied_at if bewertung else None
    return zeile


def _zurueckgestellte_abschliessen(db: Session, request: MediaRequest) -> None:
    """Andere zurueckgestellte Anfragen zu diesem Titel erledigen.

    Warum das sein **muss**, steht in ``requests_service.zurueckgestellte_
    schliessen``: Zwei zurechenbare Anfragen fuer eine Datei machen die
    Speicher-Rechnung mehrdeutig.

    Die Betroffenen bekommen Bescheid - sonst verschwaende ihre Anfrage
    kommentarlos aus der Liste, waehrend der Titel auftaucht.
    """
    for andere in requests_service.zurueckgestellte_schliessen(db, request):
        if andere.user is not None:
            notify.create(
                db,
                user=andere.user,
                kind=NotificationType.request_fulfilled,
                message_key="notifications.requestFulfilled",
                request=andere,
            )


def _bewertung_zu(db: Session, request: MediaRequest):
    """Die Rueckmeldung des Anfragenden - sie haengt am Titel, nicht hier."""
    besitzer = db.get(User, request.user_id)
    if besitzer is None:
        return None
    return ratings.meine(
        db, besitzer, request.media_type, request.tmdb_id, request.season
    )


def _antwort(db: Session, request: MediaRequest) -> RequestWithUser:
    """Eine einzelne Anfrage nach aussen - samt Stand des Anfragenden.

    Auch nach dem Freigeben: Dann zeigt die Antwort den Stand, der **danach**
    gilt. Das ist die Warnung an der Stelle, an der sie ankommt - der
    Entscheider sieht sofort, was seine Entscheidung bewirkt hat.
    """
    return _with_user(
        request,
        _speicher_staende(db, [request]).get(request.user_id),
        bewertung=_bewertung_zu(db, request),
    )


def _get_or_404(db: Session, request_id: int) -> MediaRequest:
    request = db.get(MediaRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden.")
    return request


def _notify_requester(
    db: Session, request: MediaRequest, kind: NotificationType, message_key: str
) -> None:
    """Den Anfragenden ueber die Entscheidung informieren.

    Wer ueber die eigene Anfrage entscheidet - Admins und Entscheider tun das
    dauernd, ihre Anfragen laufen automatisch durch - bekommt keine Meldung
    ueber sich selbst.
    """
    anfragender = db.get(User, request.user_id)
    if anfragender is None:
        return
    notify.create(
        db, user=anfragender, kind=kind, message_key=message_key, request=request
    )


@router.get("", response_model=list[RequestWithUser])
async def list_all(
    entscheider: ApproverUser,
    db: DbSession,
    # Die Liste wird aus dem Enum abgeleitet statt abgeschrieben: Ein neuer
    # Zustand war sonst zwar in der Oberflaeche als Filterknopf da, lieferte
    # aber 422 - und die Seite blieb im Ladezustand haengen. Genau das ist mit
    # "deleted" passiert.
    request_status: Annotated[
        RequestStatus | None,
        Query(alias="status"),
    ] = None,
    user_id: Annotated[int | None, Query(ge=1)] = None,
    feedback: Annotated[bool, Query()] = False,
    # Keine Zustandsfrage, sondern eine Herkunftsfrage - deshalb ein eigener
    # Schalter neben ``status`` und nicht ein weiterer Wert darin.
    from_watchlist: Annotated[bool, Query()] = False,
) -> list[RequestWithUser]:
    """Alle Anfragen aller Benutzer, optional gefiltert."""
    query = (
        select(MediaRequest)
        .options(selectinload(MediaRequest.user))
        .order_by(MediaRequest.requested_at.desc())
    )
    if feedback:
        # Jede Bewertung ohne Antwort - auch die ohne Text. Wer Sterne vergibt,
        # sagt damit etwas, und das soll der Administrator sehen, ohne die
        # ganze Liste durchzugehen.
        #
        # ⚠️ Die Bewertung haengt seit 0.19 am **Titel**: Gesucht wird ueber
        # ``title_ratings``, verbunden ueber Konto, Medienart, Titel und
        # Staffel. Die gleichnamigen Spalten an der Anfrage stehen noch da,
        # werden aber nicht mehr geschrieben - danach zu filtern faende nichts.
        query = (
            query.join(
                TitleRating,
                (TitleRating.user_id == MediaRequest.user_id)
                & (TitleRating.media_type == MediaRequest.media_type)
                & (TitleRating.tmdb_id == MediaRequest.tmdb_id)
                & (TitleRating.season.is_not_distinct_from(MediaRequest.season)),
            )
            .where(TitleRating.reply.is_(None))
            .order_by(None)
            .order_by(TitleRating.updated_at.desc())
        )
    elif from_watchlist:
        query = query.where(MediaRequest.from_watchlist.is_(True))
    elif request_status is not None:
        query = query.where(MediaRequest.status == RequestStatus(request_status))
    if user_id is not None:
        query = query.where(MediaRequest.user_id == user_id)

    zeilen = list(db.scalars(query))
    staende = _speicher_staende(db, zeilen)
    abos = await _abo_treffer(db, zeilen)
    return [
        _with_user(row, staende.get(row.user_id), abos.get(row.id), _bewertung_zu(db, row))
        for row in zeilen
    ]


@router.post("/{request_id}/approve", response_model=RequestWithUser)
async def approve(
    request_id: Annotated[int, Path(ge=1)],
    entscheider: ApproverUser,
    db: DbSession,
    payload: TargetChoice | None = None,
) -> RequestWithUser:
    request = _get_or_404(db, request_id)
    # ⚠️ **Zurueckgestellte gehoeren ausdruecklich dazu.**
    #
    # "Ja im Prinzip, nur nicht jetzt" ist das ganze Versprechen des
    # Zurueckstellens: Sobald wieder Platz ist, wird freigegeben, und niemand
    # muss neu fragen. Ohne diese Zeile war es eine Sackgasse - der Entscheider
    # bekam "wartet nicht mehr auf eine Freigabe", und der Anfragende beim
    # zweiten Versuch "steht bereits zurueck, sobald du wieder Platz hast, kann
    # die Anfrage freigegeben werden". Beide Meldungen verwiesen auf einen Weg,
    # den es nicht gab.
    if request.status not in (RequestStatus.pending_approval, RequestStatus.deferred):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese Anfrage wartet nicht (mehr) auf eine Freigabe.",
        )

    settings = load_settings(db)

    # Ziel nachtragen, solange die Anfrage noch wartet: Schlaegt die Pruefung
    # fehl, bleibt sie unveraendert in der Warteschlange stehen, statt als
    # "freigegeben" mit leerem Ordner liegenzubleiben.
    if _braucht_ziel(request, payload):
        try:
            await requests_service.apply_target(
                settings,
                request,
                root_folder_path=payload.root_folder_path if payload else None,
                quality_profile_id=payload.quality_profile_id if payload else None,
            )
        except requests_service.RequestError as error:
            raise HTTPException(status_code=error.status_code, detail=error.message) from error

    request.status = RequestStatus.approved
    request.approved_by = entscheider.id
    request.approved_at = utcnow()
    requests_service.clear_pending_notice(db, request)
    db.commit()

    try:
        await requests_service.push_to_arr(db, settings, request)
    except requests_service.RequestError as error:
        # Der Zustand steht jetzt auf "fehlgeschlagen" - der Admin sieht warum.
        db.refresh(request)
        raise HTTPException(status_code=error.status_code, detail=error.message) from error

    _notify_requester(db, request, NotificationType.approved, "notifications.approved")
    _zurueckgestellte_abschliessen(db, request)
    db.commit()
    db.refresh(request)
    return _antwort(db, request)


@router.post("/{request_id}/reject", response_model=RequestWithUser)
def reject(
    request_id: Annotated[int, Path(ge=1)],
    payload: RejectPayload,
    entscheider: ApproverUser,
    db: DbSession,
) -> RequestWithUser:
    request = _get_or_404(db, request_id)
    if request.status not in (RequestStatus.pending_approval, RequestStatus.deferred):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese Anfrage wartet nicht (mehr) auf eine Freigabe.",
        )

    # Sperren ist eine Grundsatzentscheidung fuer die ganze Bibliothek und
    # steht deshalb nur dem Administrator zu - ein Entscheider lehnt die
    # einzelne Anfrage ab, mehr nicht. Deutlich abweisen statt still zu
    # ignorieren: sonst klickt jemand den Haken und glaubt, es sei passiert.
    if payload.block and entscheider.role != Role.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur ein Administrator darf Titel auf die Sperrliste setzen.",
        )

    request.status = RequestStatus.rejected
    request.approved_by = entscheider.id
    request.approved_at = utcnow()
    request.rejection_reason = (payload.reason or "").strip() or None

    if payload.block:
        blocklist.sperren(
            db,
            media_type=request.media_type,
            tmdb_id=request.tmdb_id,
            title=request.title,
            # ``poster_path`` heisst so, enthaelt aber laengst die fertige
            # Adresse (siehe models.MediaRequest). Ein ``image_url`` darum
            # herum setzt das Praefix ein zweites Mal davor.
            poster_url=request.poster_path,
            # Die Begruendung der Ablehnung ist zugleich die der Sperre - man
            # sagt denselben Satz nicht zweimal.
            reason=request.rejection_reason,
            admin=entscheider,
        )

    requests_service.clear_pending_notice(db, request)
    _notify_requester(db, request, NotificationType.rejected, "notifications.rejected")
    db.commit()
    db.refresh(request)
    return _antwort(db, request)


@router.post("/{request_id}/reply", response_model=RequestWithUser)
def reply_to_feedback(
    request_id: Annotated[int, Path(ge=1)],
    payload: FeedbackReply,
    admin: AdminUser,
    db: DbSession,
) -> RequestWithUser:
    """Auf die Rueckmeldung des Anfragenden antworten.

    Das darf ausdruecklich nur der Administrator - Entscheider entscheiden
    ueber Anfragen, aber sie sprechen nicht fuer den Betreiber. Der Anfragende
    bekommt dazu eine Benachrichtigung.
    """
    request = _get_or_404(db, request_id)
    # Die Rueckmeldung haengt seit 0.19 am **Titel**, nicht an der Anfrage -
    # bewerten darf jeder, der ihn vorliegen hat. Dieser Weg antwortet auf die
    # des Anfragenden; die Uebersicht ueber alle steht unter ``/api/feedback``.
    besitzer = db.get(User, request.user_id)
    bewertung = (
        ratings.meine(db, besitzer, request.media_type, request.tmdb_id, request.season)
        if besitzer is not None
        else None
    )
    if bewertung is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Zu dieser Anfrage gibt es noch keine Rückmeldung.",
        )

    bewertung.reply = payload.reply.strip()
    bewertung.replied_at = utcnow().replace(tzinfo=None)
    bewertung.replied_by = admin.id

    _notify_requester(db, request, NotificationType.feedback_reply, "notifications.feedbackReply")
    db.commit()
    db.refresh(request)

    logger.info("%r replied to the feedback on %r", admin.username, request.title)
    return _antwort(db, request)


@router.post("/approve-all/{user_id}", response_model=list[RequestWithUser])
async def approve_all(
    user_id: Annotated[int, Path(ge=1)],
    entscheider: ApproverUser,
    db: DbSession,
    payload: ApproveAllPayload | None = None,
) -> list[RequestWithUser]:
    """Alle offenen Anfragen eines Benutzers auf einmal freigeben.

    Schlaegt eine Uebergabe an Radarr/Sonarr fehl, laufen die uebrigen
    trotzdem weiter - die fehlgeschlagene steht danach auf "fehlgeschlagen"
    und ist in der Liste sichtbar.

    Fehlt einer Anfrage das Ziel und wurde auch keines mitgeschickt, wird sie
    **uebersprungen** statt den ganzen Stapel scheitern zu lassen: Sie bleibt
    in der Warteschlange und kann einzeln freigegeben werden.
    """
    offen = list(
        db.scalars(
            select(MediaRequest)
            .options(selectinload(MediaRequest.user))
            .where(
                MediaRequest.user_id == user_id,
                MediaRequest.status == RequestStatus.pending_approval,
            )
            .order_by(MediaRequest.requested_at)
        )
    )
    if not offen:
        raise HTTPException(status_code=404, detail="Keine offenen Anfragen für diesen Benutzer.")

    settings = load_settings(db)
    uebersprungen: list[MediaRequest] = []
    for request in offen:
        wahl = payload.fuer(request.media_type, request.tier) if payload is not None else None
        if _braucht_ziel(request, wahl):
            try:
                await requests_service.apply_target(
                    settings,
                    request,
                    root_folder_path=wahl.root_folder_path if wahl else None,
                    quality_profile_id=wahl.quality_profile_id if wahl else None,
                )
            except requests_service.RequestError:
                # Bleibt wartend - lieber eine Anfrage stehen lassen als den
                # ganzen Stapel abbrechen.
                uebersprungen.append(request)
                continue

        request.status = RequestStatus.approved
        request.approved_by = entscheider.id
        request.approved_at = utcnow()
        requests_service.clear_pending_notice(db, request)
        db.commit()

        try:
            await requests_service.push_to_arr(db, settings, request)
        except requests_service.RequestError:
            db.refresh(request)
            continue

        _notify_requester(db, request, NotificationType.approved, "notifications.approved")
        _zurueckgestellte_abschliessen(db, request)
        db.commit()

    # Uebersprungene wurden nie angefasst; sie gehoeren nicht ins Ergebnis,
    # sonst saehe es aus, als waeren sie freigegeben worden.
    erledigt = [request for request in offen if request not in uebersprungen]
    for request in erledigt:
        db.refresh(request)
    staende = _speicher_staende(db, erledigt)
    return [_with_user(request, staende.get(request.user_id)) for request in erledigt]


@router.post("/{request_id}/defer", response_model=RequestWithUser)
def zuruecksetzen(
    request_id: Annotated[int, Path(ge=1)],
    entscheider: ApproverUser,
    db: DbSession,
) -> RequestWithUser:
    """Eine wartende Anfrage zurueckstellen: "Ja im Prinzip, nur nicht jetzt."

    Gedacht fuer das ueberzogene Konto: Der Entscheider will weder durchwinken
    noch ablehnen. Die Anfrage bleibt fuer beide Seiten sichtbar, und sobald
    wieder Platz ist, laesst sie sich freigeben - **niemand muss neu fragen.**

    ⚠️ **Und sie gibt den Titel frei.** Das ist der eigentliche Gewinn
    gegenueber "einfach stehen lassen": Eine wartende Anfrage reserviert den
    Titel fuer alle anderen mit. Der Grund fuers Zurueckstellen liegt aber an
    der **Person**, nicht am Titel - also darf ihn jemand anders holen. Dann
    ist er da, und die zurueckgestellte Anfrage hat sich erledigt.

    Der Anfragende bekommt Bescheid: Eine Anfrage, die stillschweigend den
    Zustand wechselt, sieht aus wie ein Fehler.
    """
    request = _get_or_404(db, request_id)
    if request.status != RequestStatus.pending_approval:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese Anfrage wartet nicht (mehr) auf eine Freigabe.",
        )

    request.status = RequestStatus.deferred
    requests_service.clear_pending_notice(db, request)
    _notify_requester(
        db, request, NotificationType.request_deferred, "notifications.deferred"
    )
    db.commit()
    db.refresh(request)
    return _antwort(db, request)


@router.post("/{request_id}/cancel", response_model=RequestWithUser)
async def cancel_request(
    request_id: Annotated[int, Path(ge=1)], entscheider: ApproverUser, db: DbSession
) -> RequestWithUser:
    """Laufende Anfrage abbrechen und in Radarr/Sonarr loeschen."""
    request = _get_or_404(db, request_id)
    try:
        await requests_service.cancel(db, load_settings(db), request)
    except requests_service.RequestError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error

    _notify_requester(db, request, NotificationType.cancelled, "notifications.cancelled")
    db.commit()
    db.refresh(request)
    return _antwort(db, request)


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_request(
    request_id: Annotated[int, Path(ge=1)], entscheider: ApproverUser, db: DbSession
) -> None:
    """Eintrag aus der Uebersicht entfernen.

    Loescht nur den Eintrag in Nexview - in Radarr/Sonarr bleibt der Titel.
    """
    db.delete(_get_or_404(db, request_id))
    db.commit()


@router.get("/pending/count")
def pending_count(entscheider: ApproverUser, db: DbSession) -> dict[str, int]:
    """Kleine Zahl fuer den Menuepunkt - wie viele warten auf Freigabe?"""
    waiting = (
        db.scalar(
            select(func.count(MediaRequest.id)).where(
                MediaRequest.status == RequestStatus.pending_approval
            )
        )
        or 0
    )
    return {"pending": waiting}
