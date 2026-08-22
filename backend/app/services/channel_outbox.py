"""Postausgang der serverseitigen Kanaele.

Getrennt vom Anlegen aus demselben Grund wie beim Mailversand: ein Push-Dienst
kann Sekunden brauchen oder gar nicht antworten, und daran darf kein Klick auf
"Anfragen" haengen. Der Auftrag steht in der Datenbank und wird abgearbeitet,
wenn es passt.

Eigene Schleife statt der Status-Abfrage: die laeuft standardmaessig alle zwei
Minuten. Fuer eine E-Mail ist das in Ordnung, fuer eine Push-Nachricht nicht -
zwei Minuten nach dem Klick zu klingeln fuehlt sich kaputt an, auch wenn es
funktioniert.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    ChannelMessage,
    ChannelTarget,
    MediaRequest,
    NotificationType,
    Ticket,
    User,
    utcnow,
)
from . import channels
from .settings_service import AppSettings, load_settings

logger = logging.getLogger("nexview.channels")

# Wie oft nachgesehen wird, ob etwas rausgehen soll.
INTERVAL_SECONDS = 10

# Wie viele Nachrichten pro Durchgang.
BATCH = 20

# Nach so vielen erfolglosen Anlaeufen wird aufgegeben. Der letzte Fehler
# bleibt stehen und steht in den Einstellungen auf der Kachel.
MAX_ATTEMPTS = 3

# Welche Meldungen kann ein serverseitiges Ziel ueberhaupt berichten?
#
# Bewusst eine ausdrueckliche Aufzaehlung und kein "alles, was es gibt": ein
# serverseitiges Ziel ist ein geteiltes Postfach. Was dort landet, sieht jeder,
# der es abonniert hat - das gehoert angehakt, nicht vorausgesetzt.
#
# Der Wert ist der **Haken**, zu dem die Meldung gehoert. Mehrere Typen
# teilen sich einen: "freigegeben" und "abgelehnt" sind fuer den Kanal
# dieselbe Auskunft ("es wurde entschieden"), und wer gute Rueckmeldungen
# sehen will, will die schlechten erst recht. Sieben Haken sind ueberschaubar,
# elf waeren eine Zumutung - dieselbe Abwaegung wie bei den Mail-Schaltern.
#
# Persoenliches (Antwort auf *dein* Ticket, Antwort auf *deine* Rueckmeldung)
# steht absichtlich nicht hier: Das gehoert in die Glocke des Betroffenen,
# nicht in ein geteiltes Postfach.
EVENTS: dict[NotificationType, str] = {
    NotificationType.request_pending: "request_pending",
    NotificationType.approved: "request_decided",
    NotificationType.rejected: "request_decided",
    NotificationType.cancelled: "request_cancelled",
    NotificationType.download_complete: "download_complete",
    NotificationType.ticket_new: "ticket_new",
    NotificationType.feedback: "feedback",
    NotificationType.feedback_poor: "feedback",
    NotificationType.user_imported: "user_imported",
    # --- Speicher ---------------------------------------------------------
    #
    # ⚠️ **Ein serverseitiges Ziel hat keinen Empfaenger.** Ein Topic ist ein
    # Postfach, das jemand eingerichtet hat - Nexview weiss nicht, wer
    # mitliest. Beides sind hier deshalb **Haus-Durchsagen**, keine
    # persoenlichen Nachrichten, und die Texte sind entsprechend gehalten: Sie
    # nennen den Titel, nicht die Person und schon gar keine Kontostaende.
    #
    # Die persoenliche Zustellung laeuft weiter ueber die Glocke, die sehr wohl
    # einen Empfaenger kennt.
    NotificationType.storage_release_requested: "storage_release",
    NotificationType.storage_released: "storage_release",
    NotificationType.storage_kept: "storage_release",
    NotificationType.storage_deleted: "storage_release",
}

# Die Haken, die es damit gibt - fuer die Pruefung im Router.
GROUPS = frozenset(EVENTS.values())

# Wohin der Klick auf die Meldung fuehrt. Was hier nicht steht, zeigt auf die
# Freigabeliste - dort landet ohnehin fast alles Anfragenbezogene.
LINKS: dict[NotificationType, str] = {
    NotificationType.ticket_new: "/tickets",
    NotificationType.user_imported: "/admin/settings",
    # Dorthin, wo die Warteschlange steht und entschieden wird.
    NotificationType.storage_release_requested: "/admin/settings",
    NotificationType.storage_released: "/profil",
    NotificationType.storage_kept: "/profil",
    NotificationType.storage_deleted: "/profil",
}

# Textbausteine. Ein serverseitiges Ziel hat keinen Empfaenger und damit auch
# keine Empfaengersprache; welche gilt, steht deshalb beim Ziel. ``by`` ist
# die Beschriftung der Personenzeile - fehlt sie, entfaellt die Zeile.
TEXTS: dict[str, dict[NotificationType, dict[str, str]]] = {
    "de": {
        NotificationType.request_pending: {
            "title": "Neue Freigabeanfrage",
            "by": "Angefragt von",
        },
        NotificationType.approved: {"title": "Anfrage freigegeben", "by": "Angefragt von"},
        NotificationType.rejected: {"title": "Anfrage abgelehnt", "by": "Angefragt von"},
        NotificationType.cancelled: {"title": "Anfrage storniert", "by": "Angefragt von"},
        NotificationType.download_complete: {"title": "Neu verfügbar", "by": "Angefragt von"},
        NotificationType.ticket_new: {"title": "Neues Ticket"},
        NotificationType.feedback: {"title": "Neue Rückmeldung", "by": "Von"},
        NotificationType.feedback_poor: {"title": "Schlechte Bewertung", "by": "Von"},
        NotificationType.user_imported: {"title": "Neues Konto über den Media-Server"},
        # Ohne "by": Wer etwas abgegeben hat, geht ein Topic nichts an, das die
        # halbe Familie liest.
        NotificationType.storage_release_requested: {
            "title": "Ein Titel wurde abgegeben"
        },
        NotificationType.storage_released: {"title": "Ein Titel gehört jetzt dem Haus"},
        NotificationType.storage_kept: {"title": "Ein Titel bleibt, wird aber nicht mehr geladen"},
        NotificationType.storage_deleted: {"title": "Ein Titel wurde gelöscht"},
    },
    "en": {
        NotificationType.request_pending: {
            "title": "New request awaiting approval",
            "by": "Requested by",
        },
        NotificationType.approved: {"title": "Request approved", "by": "Requested by"},
        NotificationType.rejected: {"title": "Request declined", "by": "Requested by"},
        NotificationType.cancelled: {"title": "Request cancelled", "by": "Requested by"},
        NotificationType.download_complete: {"title": "Now available", "by": "Requested by"},
        NotificationType.ticket_new: {"title": "New ticket"},
        NotificationType.feedback: {"title": "New feedback", "by": "From"},
        NotificationType.feedback_poor: {"title": "Poor rating", "by": "From"},
        NotificationType.user_imported: {"title": "New media-server account"},
        NotificationType.storage_release_requested: {"title": "A title was handed back"},
        NotificationType.storage_released: {"title": "A title now belongs to the house"},
        NotificationType.storage_kept: {"title": "A title stays, but stops downloading"},
        NotificationType.storage_deleted: {"title": "A title has been deleted"},
    },
}


# Das Wort vor der Staffelnummer, je Sprache des Ziels.
#
# ⚠️ Ohne diesen Zusatz sind fuenf freigegebene Staffeln derselben Serie fuenf
# vollkommen identische Push-Nachrichten - gemeldet als "ohne die Info, dass
# das nur eine Folge ist und welche". Der Anzeigename der Serie allein
# beantwortet nicht, worum es geht.
STAFFEL = {"de": "Staffel", "en": "Season"}


def aktiv(target: ChannelTarget) -> bool:
    """Ist dieses Ziel in Betrieb - und seine Instanz auch?

    Ein stillgelegtes Topic schweigt; eine stillgelegte Instanz legt alle ihre
    Topics mit still, denn ohne Adresse und Anmeldung koennten sie ohnehin
    nichts ausrichten.
    """
    if not target.enabled:
        return False
    return target.parent is None or target.parent.enabled


def level_of(target: ChannelTarget, typ: NotificationType) -> str | None:
    """Wie dringend meldet dieses Ziel diesen Vorgang? ``None`` heisst: gar nicht."""
    name = EVENTS.get(typ)
    if name is None or not target.verified or not aktiv(target):
        return None
    stufe = (target.events or {}).get(name)
    return stufe if stufe in channels.LEVELS else None


def enqueue(
    db: Session,
    *,
    kind: NotificationType,
    request: MediaRequest | None = None,
    ticket: Ticket | None = None,
    title: str | None = None,
) -> list[ChannelMessage]:
    """Ein Ereignis fuer alle interessierten Ziele vormerken. Kein ``commit``.

    Genau **einmal je Ereignis und Ziel**, nicht je Empfaenger - siehe die
    Begruendung an ``ChannelMessage``.
    """
    if kind not in EVENTS:
        return []

    eintraege = []
    for target in db.scalars(select(ChannelTarget).where(ChannelTarget.verified.is_(True))):
        if level_of(target, kind) is None:
            continue
        eintrag = ChannelMessage(
            channel=target.channel,
            target_id=target.id,
            type=kind,
            title=(
                title
                if title is not None
                else (
                    request.title
                    if request is not None
                    else (ticket.subject if ticket is not None else None)
                )
            ),
            request_id=request.id if request is not None else None,
            ticket_id=ticket.id if ticket is not None else None,
        )
        db.add(eintrag)
        eintraege.append(eintrag)
    return eintraege


def _notice(
    db: Session,
    eintrag: ChannelMessage,
    target: ChannelTarget,
    settings: AppSettings,
) -> channels.Notice | None:
    """Aus dem Auftrag die fertige Nachricht bauen."""
    sprache = target.language if target.language in TEXTS else "de"
    bausteine = TEXTS[sprache].get(eintrag.type)
    stufe = level_of(target, eintrag.type)
    if bausteine is None or stufe is None:
        return None

    request = db.get(MediaRequest, eintrag.request_id) if eintrag.request_id else None
    titel = eintrag.title or (request.title if request else "")
    if request is not None and request.season is not None:
        titel = f"{titel} · {STAFFEL.get(sprache, STAFFEL['de'])} {request.season}"

    zeilen = [f"**{titel}**"] if titel else []
    if request is not None and "by" in bausteine:
        anfragender = db.get(User, request.user_id)
        if anfragender is not None:
            name = anfragender.display_name or anfragender.username
            zeilen.append(f"{bausteine['by']}: {name}")

    # Ohne hinterlegte oeffentliche Adresse waere der Link ein Verweis auf
    # "localhost" - fuer den, der aufs Handy schaut, wertlos.
    pfad = LINKS.get(eintrag.type, "/admin/requests")
    ziel = settings.link(pfad) if settings.public_url else None

    return channels.Notice(
        title=bausteine["title"],
        body="\n".join(zeilen),
        level=stufe,
        poster_url=request.poster_path if request else None,
        click_url=ziel,
        event=eintrag.type.value,
    )


def _offen(db: Session) -> list[ChannelMessage]:
    return list(
        db.scalars(
            select(ChannelMessage)
            .where(
                ChannelMessage.sent_at.is_(None),
                ChannelMessage.attempts < MAX_ATTEMPTS,
            )
            .order_by(ChannelMessage.created_at)
            .limit(BATCH)
        )
    )


def _aufgeben(eintrag: ChannelMessage, grund: str) -> None:
    """Endgueltig abhaken. Der Grund bleibt sichtbar."""
    eintrag.attempts = MAX_ATTEMPTS
    eintrag.last_error = grund


async def process(db: Session, settings: AppSettings) -> int:
    """Offene Nachrichten verschicken. Liefert die Anzahl der zugestellten."""
    from . import channel_targets

    offen = _offen(db)
    if not offen:
        return 0

    zugestellt = 0
    for eintrag in offen:
        target = db.get(ChannelTarget, eintrag.target_id)
        config = channel_targets.config(target, settings) if target is not None else None
        if target is None or config is None:
            # Zwischenzeitlich abgeraeumt - der Auftrag ist hinfaellig.
            _aufgeben(eintrag, "Das Ziel ist nicht mehr eingerichtet.")
            continue

        nachricht = _notice(db, eintrag, target, settings)
        if nachricht is None:
            _aufgeben(eintrag, "Dieses Ziel meldet diesen Vorgang nicht mehr.")
            continue

        eintrag.attempts += 1
        try:
            await channels.send(target.channel, config, nachricht)
        except channels.ChannelError as fehler:
            eintrag.last_error = fehler.message
            if eintrag.attempts >= MAX_ATTEMPTS:
                logger.warning(
                    "%s/%s: Nachricht endgültig nicht zustellbar (%s): %s",
                    channels.label(target.channel),
                    target.name,
                    eintrag.type.value,
                    fehler.message,
                )
            else:
                logger.info(
                    "%s/%s: Versuch %d von %d fehlgeschlagen: %s",
                    channels.label(target.channel),
                    target.name,
                    eintrag.attempts,
                    MAX_ATTEMPTS,
                    fehler.message,
                )
            continue

        eintrag.sent_at = utcnow()
        eintrag.last_error = None
        zugestellt += 1

    db.commit()
    if zugestellt:
        logger.info("%d Benachrichtigung(en) über serverseitige Kanäle verschickt", zugestellt)
    return zugestellt


def last_failure(db: Session, target: ChannelTarget) -> ChannelMessage | None:
    """Der letzte endgueltig gescheiterte Versand an dieses Ziel.

    Steht auf der Kachel in den Einstellungen. Ohne diese Anzeige merkt niemand,
    dass seit Wochen nichts mehr durchgeht - ein Ziel, das schweigt, sieht
    genauso aus wie eines, ueber das es nichts zu berichten gibt.
    """
    return db.scalars(
        select(ChannelMessage)
        .where(
            ChannelMessage.target_id == target.id,
            ChannelMessage.sent_at.is_(None),
            ChannelMessage.attempts >= MAX_ATTEMPTS,
        )
        .order_by(ChannelMessage.created_at.desc())
        .limit(1)
    ).first()


async def run_forever(stop: asyncio.Event) -> None:
    """Eigene Hintergrundschleife - kurzer Takt, damit Push sich nach Push anfuehlt."""
    while not stop.is_set():
        try:
            with SessionLocal() as db:
                # Erst die billige Frage: liegt ueberhaupt etwas an? Die
                # Einstellungen zu laden heisst, Geheimnisse zu entschluesseln -
                # das alle zehn Sekunden fuer nichts zu tun waere Verschwendung.
                if _offen(db):
                    await process(db, load_settings(db))
        except Exception:  # noqa: BLE001 - die Schleife darf nie sterben
            logger.exception("Versand über serverseitige Kanäle fehlgeschlagen")

        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
        except TimeoutError:
            continue
