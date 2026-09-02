"""Wuensche der Kinder - und wie daraus eine Anfrage der Eltern wird.

Der Kern des ganzen Aufbaus steht in ``freigeben``: Das Elternteil gibt frei,
und dann laeuft **``requests_service.create_request`` mit ihm als
Anfragendem** - genau derselbe Aufruf wie bei einem Klick auf "Anfragen". Damit
greifen ohne eine Zeile Sonderlogik: Sperrliste, "liegt schon in der
Bibliothek", "liegt schon auf dem Media-Server", Profilsperren, 4K-Recht,
Stueck-Kontingent, Speicher-Kontingent, Auto-Freigabe und die Ziel-Wahl durch
den Entscheider.

Das Elternteil ist damit Freigeber **nach unten**, nicht nach oben: Ob der
Titel wirklich geladen wird, entscheidet danach wie immer der Administrator.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ChildWish,
    MediaRequest,
    MediaType,
    NotificationType,
    RequestStatus,
    User,
    WishState,
    utcnow,
)
from ..schemas_media import MediaItem
from ..services.settings_service import AppSettings
from . import notify, requests_service
from .children import ChildError

# Welche Anfrage-Zustaende das Kind als "kommt noch" lesen soll. Alles andere
# ist entweder da oder erledigt.
UNTERWEGS = (
    RequestStatus.pending_approval,
    RequestStatus.approved,
    RequestStatus.searching,
)


def offene_wuensche(db: Session, elternteil: User) -> list[ChildWish]:
    """Was gerade auf eine Entscheidung wartet - ueber alle eigenen Kinder."""
    return list(
        db.scalars(
            select(ChildWish)
            .where(ChildWish.parent_id == elternteil.id, ChildWish.state == WishState.open)
            .order_by(ChildWish.created_at)
        )
    )


def wuensche_des_kindes(db: Session, kind: User) -> list[ChildWish]:
    """Die eigene Liste - Neues zuerst."""
    return list(
        db.scalars(
            select(ChildWish)
            .where(ChildWish.child_id == kind.id)
            .order_by(ChildWish.created_at.desc())
        )
    )


def kinderzustand(wunsch: ChildWish) -> str:
    """Der Zustand in der Sprache eines Kindes.

    Bewusst **vier** Stufen statt der acht aus ``RequestStatus``: "wartet",
    "unterwegs", "ist da", "diesmal nicht". Ein Kind soll nicht lernen, was
    ``pending_approval`` von ``searching`` unterscheidet.
    """
    if wunsch.state == WishState.open:
        return "waiting"
    if wunsch.state == WishState.declined:
        return "declined"
    if wunsch.state == WishState.obsolete:
        return "available"

    anfrage = wunsch.request
    if anfrage is None:
        # Die Anfrage wurde geloescht oder storniert - der Titel kommt nicht.
        return "declined"
    if anfrage.status == RequestStatus.downloaded:
        return "available"
    if anfrage.status in UNTERWEGS:
        return "coming"
    # rejected, failed, cancelled, deleted, deferred: aus Sicht des Kindes ist
    # der Titel nicht da und kommt auch nicht.
    return "declined"


def wuenschen(db: Session, kind: User, item: MediaItem) -> ChildWish:
    """Einen Wunsch anlegen.

    Die Altersgrenze ist an dieser Stelle **schon geprueft**: Der Aufrufer holt
    den Titel ueber ``media.detail`` mit den Einstellungen des Kindes, und ein
    gesperrter Titel kommt dort gar nicht erst heraus (``AgeRestricted`` -> 404).
    """
    media_type = MediaType(item.media_type)

    vorhanden = db.scalar(
        select(ChildWish).where(
            ChildWish.child_id == kind.id,
            ChildWish.media_type == media_type,
            ChildWish.tmdb_id == item.tmdb_id,
            ChildWish.state == WishState.open,
        )
    )
    if vorhanden is not None:
        raise ChildError("Das steht schon auf deiner Wunschliste.", 409)

    if kind.parent_id is None:
        # Kann nicht vorkommen - ein Kinderkonto ohne Elternteil entsteht auf
        # keinem Weg. Trotzdem lieber eine klare Meldung als ein NULL im Feld.
        raise ChildError("Zu diesem Konto gehört kein Erwachsener.", 409)

    wunsch = ChildWish(
        child_id=kind.id,
        parent_id=kind.parent_id,
        media_type=media_type,
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        title=item.title,
        poster_path=item.poster_url,
        release_date=item.release_date,
    )
    db.add(wunsch)
    db.commit()
    db.refresh(wunsch)

    elternteil = db.get(User, kind.parent_id)
    if elternteil is not None:
        notify.create(
            db,
            user=elternteil,
            kind=NotificationType.child_wish,
            message_key="notifications.childWish",
            # Wer sich was wuenscht - beides in einer Zeile. Die Textbausteine
            # tragen bewusst keine Platzhalter (``NotificationBell`` setzt
            # nichts ein), also muss der Name hierhin.
            title=f"{kind.display_name or kind.username}: {item.title}",
            # Kein Ruf in die hausweiten Kanaele: Ein Kinderwunsch geht das
            # Elternteil an, nicht die ganze Installation.
            broadcast=False,
        )
        db.commit()
    return wunsch


def _wunsch_von(db: Session, elternteil: User, wunsch_id: int) -> ChildWish:
    """Ein Wunsch eines eigenen Kindes - oder 404.

    Wie bei den Tickets: kein 403, das wuerde bestaetigen, dass es die Nummer
    gibt.
    """
    wunsch = db.get(ChildWish, wunsch_id)
    if wunsch is None or wunsch.parent_id != elternteil.id:
        raise ChildError("Wunsch nicht gefunden.", 404)
    return wunsch


def ablehnen(db: Session, elternteil: User, wunsch_id: int, notiz: str | None) -> ChildWish:
    wunsch = _wunsch_von(db, elternteil, wunsch_id)
    if wunsch.state != WishState.open:
        raise ChildError("Über diesen Wunsch ist schon entschieden.", 409)

    wunsch.state = WishState.declined
    wunsch.decline_note = (notiz or "").strip() or None
    wunsch.decided_at = utcnow()
    db.commit()
    db.refresh(wunsch)
    return wunsch


async def freigeben(
    db: Session,
    settings: AppSettings,
    elternteil: User,
    wunsch_id: int,
    item: MediaItem,
    **anfrage_optionen,
) -> MediaRequest:
    """Aus dem Wunsch wird eine Anfrage des Elternteils.

    ``item`` kommt frisch von TMDB - aus den Sicht **des Elternteils**, nicht
    des Kindes: Es ist seine Anfrage, sein Kontingent, und der Titel muss
    seinen Regeln genuegen. Scheitert ``create_request`` (Kontingent voll,
    Sperrliste), bleibt der Wunsch offen und der Fehler geht unveraendert an
    das Elternteil - es soll ja wissen, warum.

    ⚠️ **Ein Fall ist davon ausgenommen: Der Titel liegt laengst da.** Frueher
    lief er in denselben Zweig, und das war eine Sackgasse - die Freigabe
    scheiterte bei jedem Versuch aufs Neue, weil der Grund sich nicht mehr
    aendern konnte. Uebrig blieb nur "Ablehnen", also ausgerechnet die
    Antwort, die das Gegenteil der Wahrheit sagt: Das Kind hat ja bekommen,
    was es wollte. Gemeldet wurde genau das, nachdem ein Film ueber eine
    zweite Instanz ins Haus kam, waehrend der Wunsch noch zur Freigabe lag.

    Deshalb schliesst dieser Fall den Wunsch als ``obsolete`` - dieselbe
    Bewertung, die ``erledigte_schliessen`` seit jeher trifft, und aus
    demselben Grund. Die Meldung bleibt, aber sie sagt jetzt, was wirklich
    passiert ist.
    """
    wunsch = _wunsch_von(db, elternteil, wunsch_id)
    if wunsch.state != WishState.open:
        raise ChildError("Über diesen Wunsch ist schon entschieden.", 409)

    try:
        anfrage = await requests_service.create_request(
            db, settings, elternteil, item, **anfrage_optionen
        )
    except requests_service.RequestError as fehler:
        if fehler.code not in requests_service.SCHON_DA:
            raise
        wunsch.state = WishState.obsolete
        wunsch.decided_at = utcnow()
        db.commit()
        raise ChildError(
            f"„{wunsch.title}“ ist bereits da - der Wunsch ist damit erledigt.",
            409,
            code="wish_already_available",
            titel=wunsch.title,
        ) from fehler

    anfrage.for_child_id = wunsch.child_id

    # ⚠️ **Eine Regel kann das Ja der Eltern ueberstimmen - dann darf der
    # Wunsch nicht als freigegeben gelten.**
    #
    # ``create_request`` wirft bei einer Regel-Ablehnung keine Ausnahme; sie
    # legt eine Anfrage im Zustand ``rejected`` an und gibt sie zurueck. Wer
    # das nicht ansieht, schreibt ``released`` in die Datenbank, waehrend in
    # Wirklichkeit abgelehnt wurde: Das Elternteil sieht seinen Wunsch aus der
    # Liste verschwinden und glaubt, es habe freigegeben. Das Kind liest
    # "diesmal nicht". Und niemand erfaehrt, dass eine Hausregel dazwischen
    # stand.
    #
    # Der Wunsch bleibt deshalb **offen**. Die abgelehnte Anfrage bleibt
    # ebenfalls stehen - sie ist der Vorgang, an dem "trotzdem fragen" haengt,
    # falls die Regel das zulaesst.
    if anfrage.status == RequestStatus.rejected and anfrage.regel_id is not None:
        db.commit()
        grund = f" {anfrage.rejection_reason}" if anfrage.rejection_reason else ""
        raise ChildError(
            f"Eine Regel des Hauses hat „{wunsch.title}“ abgelehnt.{grund}",
            409,
            code="wish_rule_rejected",
            titel=wunsch.title,
        )

    wunsch.state = WishState.released
    wunsch.decided_at = utcnow()
    wunsch.request_id = anfrage.id
    db.commit()
    return anfrage


def erledigte_schliessen(
    db: Session, media_type: MediaType, tmdb_id: int, season: int | None = None
) -> int:
    """Offene Wuensche schliessen, deren Titel inzwischen da ist.

    Zwei Kinder duerfen sich denselben Film wuenschen. Holt ihn ein Elternteil,
    haette das andere Kind sonst weiter einen offenen Wunsch auf etwas, das
    laengst in der Bibliothek liegt - und sein Elternteil eine Aufgabe, die
    sich nicht mehr erledigen laesst.

    ⚠️ **Nur volle Abdeckung schliesst.** Eine Staffel- oder Folgen-Anfrage
    (``season`` gesetzt) liefert weniger, als ein Wunsch meint - der Wunsch
    gilt dem Titel. Wuerde sie fremde Wuensche schliessen, staende bei einem
    anderen Kind "ist da", weil ein Elternteil zwei Folgen zum Antesten
    geholt hat. Die bleiben deshalb offen; nur eine Anfrage ueber die ganze
    Serie (oder ein Film) erledigt sie.

    Der Zustand ist ``obsolete`` und ausdruecklich **nicht** ``declined``: Das
    Kind hat ja bekommen, was es wollte. Als Absage gelesen waere es das genaue
    Gegenteil der Wahrheit - derselbe Grund, aus dem es
    ``NotificationType.request_fulfilled`` gibt.

    Committet **nicht** - der Aufrufer haengt das an seine eigene Transaktion.
    """
    if media_type == MediaType.tv and season is not None:
        return 0
    offen = db.scalars(
        select(ChildWish).where(
            ChildWish.media_type == media_type,
            ChildWish.tmdb_id == tmdb_id,
            ChildWish.state == WishState.open,
        )
    ).all()
    for wunsch in offen:
        wunsch.state = WishState.obsolete
        wunsch.decided_at = utcnow()
    return len(offen)


def wuensche_loeschen(db: Session, kind: User) -> int:
    """Alle Wuensche eines Kindes entfernen - **ohne** zu committen.

    Gebraucht beim Loeschen des Kontos: ``ChildWish.child_id`` traegt keine
    Fremdschluessel-Regel (nachgetragene Spalten koennen das in SQLite nicht),
    also bliebe sonst ein Verweis ins Leere stehen - bzw. auf einer frischen
    Datenbank scheiterte das Loeschen an der Regel.
    """
    wuensche = db.scalars(select(ChildWish).where(ChildWish.child_id == kind.id)).all()
    for wunsch in wuensche:
        db.delete(wunsch)
    if wuensche:
        db.flush()
    return len(wuensche)
