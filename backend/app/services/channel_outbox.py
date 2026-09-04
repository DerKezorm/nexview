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
from . import channels, meldungsziele
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
    # Radarr/Sonarr melden ein eigenes Problem - eine Haus-Durchsage im
    # Wortsinn: Sie betrifft jeden, der auf Downloads wartet.
    NotificationType.instanz_gesundheit: "instance_health",
}

# Die Haken, die es damit gibt - fuer die Pruefung im Router.
GROUPS = frozenset(EVENTS.values())

# ⚠️ **Wohin der Klick fuehrt, steht jetzt an EINER Stelle** -
# ``services/meldungsziele.py``. Hier stand eine zweite Liste, und die
# Oberflaeche hatte eine dritte Lesart; dieselbe Meldung fuehrte in Discord an
# die richtige Stelle und in der Glocke auf die eigene Anfrageliste.
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
        # Der Titel der Nachricht traegt die Aussage der Instanz im Wortlaut.
        NotificationType.instanz_gesundheit: {"title": "Radarr/Sonarr meldet ein Problem"},
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
        NotificationType.instanz_gesundheit: {"title": "Radarr/Sonarr reports a problem"},
    },
}


# Dieselbe Nachricht, an einen Menschen gerichtet.
#
# ⚠️ **Warum eine zweite Tabelle und nicht ein Platzhalter in der ersten.**
# ``TEXTS`` oben ist eine Durchsage in ein geteiltes Postfach: "Ein Titel wurde
# geloescht". Wer mitliest, weiss nicht, wessen Titel das war, und soll es auch
# nicht erfahren. Ein persoenliches Ziel hat genau einen Empfaenger, und dort
# ist derselbe Satz falsch herum - dort heisst es "Dein Titel wurde geloescht".
#
# Der Unterschied ist keiner, den ein Platzhalter traegt: Bei
# ``storage_released`` meldet die Durchsage, dass ein Titel dem Haus gehoert,
# und die persoenliche Fassung, dass wieder Platz frei ist. Das ist dasselbe
# Ereignis aus zwei Blickwinkeln, nicht derselbe Satz mit einem anderen Wort.
#
# ⚠️ **Diese Tabelle ist vollstaendig, und ein Test haelt sie so.** Fehlte ein
# Typ, verschwaende die Meldung lautlos - siehe ``_notice``, das ohne Bausteine
# aufgibt. Wer eine ``NotificationType`` ergaenzt, ergaenzt hier zwei Zeilen.
PERSOENLICH: dict[str, dict[NotificationType, dict[str, str]]] = {
    "de": {
        # --- Die eigene Anfrage --------------------------------------------
        NotificationType.download_complete: {"title": "Dein Titel ist da"},
        NotificationType.approved: {"title": "Deine Anfrage wurde freigegeben"},
        NotificationType.rejected: {"title": "Deine Anfrage wurde abgelehnt"},
        NotificationType.cancelled: {"title": "Deine Anfrage wurde storniert"},
        NotificationType.request_deferred: {"title": "Deine Anfrage wurde zurückgestellt"},
        NotificationType.request_fulfilled: {"title": "Dein zurückgestellter Titel ist da"},
        # --- Als Entscheider ------------------------------------------------
        #
        # Hier bleibt die Personenzeile: Wer freigeben soll, muss wissen, für
        # wen. Bei den eigenen Anfragen oben wäre sie "Angefragt von: du".
        NotificationType.request_pending: {
            "title": "Neue Freigabeanfrage",
            "by": "Angefragt von",
        },
        NotificationType.feedback: {"title": "Neue Rückmeldung", "by": "Von"},
        NotificationType.feedback_poor: {"title": "Schlechte Bewertung", "by": "Von"},
        # --- Ticket und Rückmeldung -----------------------------------------
        NotificationType.ticket_new: {"title": "Neues Ticket"},
        NotificationType.ticket_reply: {"title": "Antwort auf dein Ticket"},
        NotificationType.feedback_reply: {"title": "Antwort auf deine Rückmeldung"},
        # --- Media-Server ---------------------------------------------------
        NotificationType.user_imported: {"title": "Neues Konto über den Media-Server"},
        NotificationType.mediaserver_reconnect: {
            "title": "Dein Media-Server-Zugang ist abgelaufen"
        },
        # --- Speicher --------------------------------------------------------
        #
        # ⚠️ Aus der Sicht des Betroffenen, nicht aus der des Hauses: Die
        # Durchsage meldet, dass ein Titel dem Haus gehört; hier zählt, dass
        # wieder Platz frei ist. Wer das gleichsetzt, schreibt eine Meldung,
        # die niemanden angeht.
        NotificationType.storage_released: {"title": "Dein Speicher ist wieder frei"},
        NotificationType.storage_kept: {"title": "Dein Titel bleibt, lädt aber nicht weiter"},
        NotificationType.storage_deleted: {"title": "Dein Titel wurde gelöscht"},
        NotificationType.storage_release_requested: {"title": "Ein Titel wurde abgegeben"},
        NotificationType.storage_grew: {"title": "Ein Titel belegt jetzt mehr Platz"},
        NotificationType.storage_scheduled: {"title": "Ein Titel wird bald gelöscht"},
        NotificationType.storage_unscheduled: {"title": "Die Löschung ist zurückgenommen"},
        # --- Vorgemerkt -------------------------------------------------------
        NotificationType.watch_ready: {"title": "Ein vorgemerkter Titel ist da"},
        NotificationType.watch_episodes: {"title": "Neue Folgen sind da"},
        # --- Sonstiges --------------------------------------------------------
        NotificationType.child_wish: {"title": "Dein Kind wünscht sich einen Titel"},
        NotificationType.rating_outdated: {"title": "Ein bewerteter Titel wurde neu geladen"},
        NotificationType.instanz_gesundheit: {"title": "Radarr/Sonarr meldet ein Problem"},
    },
    "en": {
        NotificationType.download_complete: {"title": "Your title is ready"},
        NotificationType.approved: {"title": "Your request was approved"},
        NotificationType.rejected: {"title": "Your request was declined"},
        NotificationType.cancelled: {"title": "Your request was cancelled"},
        NotificationType.request_deferred: {"title": "Your request was deferred"},
        NotificationType.request_fulfilled: {"title": "Your deferred title has arrived"},
        NotificationType.request_pending: {
            "title": "New request awaiting approval",
            "by": "Requested by",
        },
        NotificationType.feedback: {"title": "New feedback", "by": "From"},
        NotificationType.feedback_poor: {"title": "Poor rating", "by": "From"},
        NotificationType.ticket_new: {"title": "New ticket"},
        NotificationType.ticket_reply: {"title": "Reply to your ticket"},
        NotificationType.feedback_reply: {"title": "Reply to your feedback"},
        NotificationType.user_imported: {"title": "New media-server account"},
        NotificationType.mediaserver_reconnect: {
            "title": "Your media-server access has expired"
        },
        NotificationType.storage_released: {"title": "Your storage is free again"},
        NotificationType.storage_kept: {"title": "Your title stays but stops downloading"},
        NotificationType.storage_deleted: {"title": "Your title has been deleted"},
        NotificationType.storage_release_requested: {"title": "A title was handed back"},
        NotificationType.storage_grew: {"title": "A title now takes more space"},
        NotificationType.storage_scheduled: {"title": "A title is scheduled for deletion"},
        NotificationType.storage_unscheduled: {"title": "The deletion has been called off"},
        NotificationType.watch_ready: {"title": "A title you were waiting for is ready"},
        NotificationType.watch_episodes: {"title": "New episodes have arrived"},
        NotificationType.child_wish: {"title": "Your child wishes for a title"},
        NotificationType.rating_outdated: {"title": "A title you rated was downloaded again"},
        NotificationType.instanz_gesundheit: {"title": "Radarr/Sonarr reports a problem"},
    },
}


# Das Wort vor der Staffelnummer, je Sprache des Ziels.
#
# ⚠️ Ohne diesen Zusatz sind fuenf freigegebene Staffeln derselben Serie fuenf
# vollkommen identische Push-Nachrichten - gemeldet als "ohne die Info, dass
# das nur eine Folge ist und welche". Der Anzeigename der Serie allein
# beantwortet nicht, worum es geht.
STAFFEL = {"de": "Staffel", "en": "Season"}

# Das Wort vor den Folgennummern eines Pakets - derselbe Zweck: Zwei Pakete
# derselben Staffel sind sonst zwei identische Nachrichten.
FOLGE = {"de": "Folge", "en": "Episode"}


def folgen_zusatz(request, sprache: str) -> str:
    """" · Folge 3, 7" - fuer Betreffzeilen zu einem Folgen-Paket, sonst leer."""
    folgen = getattr(request, "episodes", None) if request is not None else None
    if not folgen:
        return ""
    wort = FOLGE.get(sprache, FOLGE["de"])
    return f" · {wort} {', '.join(str(nummer) for nummer in folgen)}"


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
    """Wie dringend meldet dieses Ziel diesen Vorgang? ``None`` heisst: gar nicht.

    ⚠️ **Ein persoenliches Ziel hat keine Haken.** Es bekommt alles, was seinen
    Besitzer auch in der Glocke erreicht - ausgewaehlt wird auf der anderen
    Seite, in der Automation der Anbindung. Ein Haken hier wuerde eine dort
    fertig gebaute Regel lautlos totlegen, und die Ursache staende in einer
    anderen Anwendung.
    """
    if not target.verified or not aktiv(target):
        return None
    if target.user_id is not None:
        return channels.DEFAULT_LEVEL
    name = EVENTS.get(typ)
    if name is None:
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
    """Ein Ereignis fuer alle interessierten **Haus**-Ziele vormerken. Kein ``commit``.

    Genau **einmal je Ereignis und Ziel**, nicht je Empfaenger - siehe die
    Begruendung an ``ChannelMessage``.

    ⚠️ **Ziele mit Besitzer bleiben hier aussen vor.** Die haengen an einem
    Empfaenger und werden deshalb von ``enqueue_persoenlich`` bedient, das
    ``notify.create`` fuer jede Meldung genau einmal ruft. Stuenden sie hier,
    bekaeme jeder alles - und das Gegenteil davon ist der ganze Zweck.
    """
    if kind not in EVENTS:
        return []

    eintraege = []
    for target in db.scalars(
        select(ChannelTarget).where(
            ChannelTarget.verified.is_(True),
            ChannelTarget.user_id.is_(None),
        )
    ):
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


def ziel_von(db: Session, user_id: int) -> ChannelTarget | None:
    """Der Rueckkanal dieses Menschen - oder ``None``.

    Genau einer je Mensch: Angelegt wird er von einer Anbindung ueber
    ``/api/v1/me/push``, und ein zweiter Aufruf ersetzt den ersten. Damit kann
    sich an einem Konto keine Liste toter Adressen ansammeln.

    ⚠️ **Ein gesperrtes Konto bekommt nichts mehr.** Wer jemanden abschaltet,
    hat ihm den Zugang genommen; klingelte sein Home Assistant weiter, waere
    die Sperre nur die halbe. Ueber die Verknuepfung geprueft statt beim
    Sperren abgeraeumt: Eine Sperre wird auch zurueckgenommen, und dann soll
    der Rueckkanal wieder da sein, ohne dass jemand neu einrichtet.
    """
    return db.scalars(
        select(ChannelTarget)
        .join(User, User.id == ChannelTarget.user_id)
        .where(
            ChannelTarget.user_id == user_id,
            ChannelTarget.verified.is_(True),
            User.is_active.is_(True),
        )
    ).first()


def enqueue_persoenlich(
    db: Session,
    *,
    user_id: int,
    kind: NotificationType,
    request: MediaRequest | None = None,
    ticket: Ticket | None = None,
    title: str | None = None,
) -> ChannelMessage | None:
    """Eine Meldung an den Rueckkanal **dieses** Menschen. Kein ``commit``.

    ⚠️ **Hier wird nicht gefiltert, und das ist der Punkt.** Wer diese Funktion
    ruft, hat gerade eine Glockenmeldung fuer genau diesen Menschen angelegt -
    die Frage, ob sie ihn etwas angeht, ist damit schon beantwortet. Eine
    zweite Pruefung waere eine zweite Wahrheit, und die beiden liefen
    auseinander.
    """
    target = ziel_von(db, user_id)
    if target is None or not aktiv(target):
        return None

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
    return eintrag


def _notice(
    db: Session,
    eintrag: ChannelMessage,
    target: ChannelTarget,
    settings: AppSettings,
) -> channels.Notice | None:
    """Aus dem Auftrag die fertige Nachricht bauen."""
    sprache = target.language if target.language in TEXTS else "de"
    # Ein Ziel mit Besitzer bekommt die persoenliche Fassung: "Dein Titel wurde
    # geloescht" statt "Ein Titel wurde geloescht".
    tabelle = PERSOENLICH if target.user_id is not None else TEXTS
    bausteine = tabelle[sprache].get(eintrag.type)
    stufe = level_of(target, eintrag.type)
    if bausteine is None or stufe is None:
        return None

    request = db.get(MediaRequest, eintrag.request_id) if eintrag.request_id else None
    titel = eintrag.title or (request.title if request else "")
    if request is not None and request.season is not None:
        titel = f"{titel} · {STAFFEL.get(sprache, STAFFEL['de'])} {request.season}"
        titel += folgen_zusatz(request, sprache)

    zeilen = [f"**{titel}**"] if titel else []
    if request is not None and "by" in bausteine:
        anfragender = db.get(User, request.user_id)
        if anfragender is not None:
            name = anfragender.display_name or anfragender.username
            zeilen.append(f"{bausteine['by']}: {name}")

    # Ohne hinterlegte oeffentliche Adresse waere der Link ein Verweis auf
    # "localhost" - fuer den, der aufs Handy schaut, wertlos.
    pfad = meldungsziele.ziel_fuer(eintrag)
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
                    "%s/%s: message finally undeliverable (%s): %s",
                    channels.label(target.channel),
                    target.name,
                    eintrag.type.value,
                    fehler.message,
                )
            else:
                logger.warning(
                    "%s/%s: attempt %d of %d failed: %s",
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
        logger.info("Delivered %d notification(s) via server-side channels", zugestellt)
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
            logger.exception("Delivery via server-side channels failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
        except TimeoutError:
            continue
