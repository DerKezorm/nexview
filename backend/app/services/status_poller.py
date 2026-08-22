"""Haelt den Zustand laufender Anfragen aktuell.

Radarr und Sonarr melden von sich aus nichts. Deshalb fragt Nexview in einem
Intervall nach, ob aus "wird gesucht" inzwischen "liegt da" geworden ist -
und benachrichtigt dann denjenigen, der den Titel angefragt hat.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    MediaRequest,
    MediaType,
    NotificationType,
    RequestStatus,
    User,
    utcnow,
)
from . import (
    library,
    mail_outbox,
    mediaserver_library,
    mediaserver_watched,
    notify,
    storage,
)
from .arr import ArrError
from .settings_service import AppSettings, load_settings

logger = logging.getLogger("nexview.poller")

# Zustaende, bei denen sich noch etwas tun kann.
WATCHED_STATUSES = (RequestStatus.approved, RequestStatus.searching)

# Nach einem Fehler nicht sofort wieder loslaufen.
ERROR_BACKOFF_SECONDS = 60


def _open_requests(db: Session) -> list[MediaRequest]:
    return list(
        db.scalars(select(MediaRequest).where(MediaRequest.status.in_(WATCHED_STATUSES)))
    )


def _fertig(request: MediaRequest, eintrag: Any) -> bool:
    """Ist **das Angefragte** geladen - die Staffel, nicht die Serie?

    ⚠️ ``has_file`` sagt "irgendeine Folge der ganzen Serie liegt vor". Solange
    nur ganze Serien angefragt werden konnten, war das dieselbe Aussage. Seit
    es Staffelanfragen gibt, ist es das nicht mehr: Gemeldet wurde eine Serie
    mit drei Dateien in einer einzigen Staffel, worauf **fuenf** Staffeln
    gleichzeitig als "bereits geladen" galten - und fuenf Fertig-Meldungen in
    derselben Sekunde hinausgingen, obwohl vier davon noch gar nicht gesucht
    hatten.
    """
    if request.season is None:
        return bool(getattr(eintrag, "has_file", False))
    stand = (getattr(eintrag, "staffeln", None) or {}).get(request.season)
    return stand is not None and stand.vollstaendig


def _noch_da(request: MediaRequest, eintrag: Any) -> bool:
    """Liegt vom Angefragten ueberhaupt noch etwas auf der Platte?

    Bewusst schwaecher als :func:`_fertig`: Einer fertigen Staffel, der jemand
    eine einzelne Folge entfernt, ist nicht "geloescht". Mit derselben
    Schwelle in beide Richtungen spraenge sie zwischen "geladen" und "weg" hin
    und her, und jeder Sprung erzeugte eine Meldung.
    """
    if request.season is None:
        return bool(getattr(eintrag, "has_file", False))
    stand = (getattr(eintrag, "staffeln", None) or {}).get(request.season)
    return stand is not None and stand.dateien > 0


async def check_once(db: Session, settings: AppSettings) -> int:
    """Einmal nachsehen. Gibt zurueck, wie viele Titel fertig geworden sind."""
    offen = _open_requests(db)
    # Kein fruehes Ende mehr: Auch ohne offene Anfragen gibt es unten noch die
    # Gegenrichtung zu pruefen - gilt ein fertig geladener Titel noch?

    # Bibliotheken **je Stufe**. Das ist die wichtigste Stelle des ganzen
    # 4K-Umbaus: Wuerde eine 4K-Anfrage gegen die 1080p-Bibliothek geprueft,
    # setzte die dort liegende Datei sie auf "fertig" und Nexview verschickte
    # "Dein Film ist da" - fuer eine Datei, die es in 4K gar nicht gibt. In der
    # Oberflaeche saehe alles richtig aus; auffallen wuerde es erst beim
    # Abspielen.
    #
    # Geladen wird nur, was auch gebraucht wird: Ohne offene 4K-Anfragen gibt
    # es keine einzige zusaetzliche Abfrage.
    filme: dict[str, dict[int, object]] = {}
    serien: dict[str, tuple[dict[int, object], dict[str, object]]] = {}

    for stufe in {r.tier.value for r in offen}:
        if any(r.media_type == MediaType.movie and r.tier.value == stufe for r in offen):
            if settings.arr_configured("movie", stufe):
                filme[stufe] = await library.movie_library(settings, stufe)
        if any(r.media_type == MediaType.tv and r.tier.value == stufe for r in offen):
            if settings.arr_configured("tv", stufe):
                serien[stufe] = await library.series_library(settings, stufe)

    # Welche Bibliotheken wirklich geantwortet haben. **Entscheidend fuer die
    # Frage "weg oder nur nicht gefragt":** Eine nicht eingerichtete oder
    # unerreichbare Instanz liefert ein leeres Ergebnis, und daraus "alles
    # verschwunden" zu folgern hiesse, bei einem Ausfall reihenweise Anfragen
    # abzubrechen.
    geladen = {("movie", stufe) for stufe in filme} | {("tv", stufe) for stufe in serien}

    fertig = 0
    verschwunden = 0
    for request in offen:
        stufe = request.tier.value
        if request.media_type == MediaType.movie:
            eintrag = filme.get(stufe, {}).get(request.tmdb_id)
        else:
            nach_tvdb, nach_titel = serien.get(stufe, ({}, {}))
            eintrag = nach_tvdb.get(request.tvdb_id) if request.tvdb_id else None
            if eintrag is None:
                eintrag = library.treffer_nach_titel(
                    nach_titel, request.title, library.jahr_aus(request.release_date)
                )

        request.last_checked_at = utcnow()
        if eintrag is None:
            # ⚠️ **Der Titel ist aus Radarr/Sonarr verschwunden.**
            #
            # Wer ihn dort von Hand entfernt, hatte in Nexview weiterhin "wird
            # gesucht" stehen - fuer immer. Der Anfragende wartete auf etwas,
            # das nie kommt, sein Kontingent blieb belastet, und ``find_active``
            # sperrte den Titel fuer **alle anderen** gleich mit.
            #
            # Anders als beim fertig geladenen Titel weiter unten wird der
            # Media-Server hier **nicht** befragt: Ueber diese Anfrage wurde nie
            # etwas geladen, es gibt also keine Datei, die "doch noch da" sein
            # koennte.
            if _wirklich_weg(request, settings, geladen):
                request.status = RequestStatus.cancelled
                request.completed_at = utcnow()
                verschwunden += 1
                logger.warning(
                    "Anfrage %s %r (%s/%s) abgebrochen: in %s nicht mehr vorhanden",
                    request.id,
                    request.title,
                    request.media_type.value,
                    request.tier.value,
                    "Radarr" if request.media_type == MediaType.movie else "Sonarr",
                )
                anfragender = db.get(User, request.user_id)
                if anfragender is not None:
                    notify.create(
                        db,
                        user=anfragender,
                        kind=NotificationType.cancelled,
                        message_key="notifications.cancelled",
                        request=request,
                    )
            continue

        # Ist der Titel inzwischen wirklich heruntergeladen?
        if _fertig(request, eintrag):
            request.status = RequestStatus.downloaded
            request.completed_at = utcnow()
            # Belegten Platz sofort zurechnen, nicht erst beim stuendlichen
            # Abgleich: Wer gerade etwas angefragt hat und nachsieht, was es
            # ihn kostet, faende dort sonst bis zu eine Stunde lang eine Null
            # und hielte die Anzeige fuer kaputt.
            if settings.storage_enabled:
                storage.verbuchen(db, request, eintrag)
            anfragender = db.get(User, request.user_id)
            if anfragender is not None:
                notify.create(
                    db,
                    user=anfragender,
                    kind=NotificationType.download_complete,
                    message_key="notifications.downloadComplete",
                    request=request,
                )
            fertig += 1
        elif request.status == RequestStatus.approved:
            # In Radarr/Sonarr angelegt, Datei fehlt noch.
            request.status = RequestStatus.searching

    # Und die Gegenrichtung: Gilt ein fertig geladener Titel noch?
    #
    # Wer eine Datei aus Radarr entfernt (etwa weil die Wunschqualitaet
    # erreicht war und der Eintrag dort nur noch stoerte), hatte in Nexview
    # weiter "Bereits geladen" stehen - und der Titel liess sich nie wieder
    # anfragen. Overseerr loest das mit einem eigenen Abgleichdienst
    # (availabilitySync); hier reicht derselbe Durchgang, der auch das
    # Fertigwerden erkennt.
    geloescht = 0
    fertige = list(
        db.scalars(
            select(MediaRequest).where(MediaRequest.status == RequestStatus.downloaded)
        )
    )
    for request in fertige:
        stufe = request.tier.value
        if not settings.arr_configured(request.media_type.value, stufe):
            # Ohne Instanz gibt es keine Quelle, die "weg" sagen koennte.
            continue
        if request.media_type == MediaType.movie:
            if stufe not in filme:
                filme[stufe] = await library.movie_library(settings, stufe)
            eintrag = filme[stufe].get(request.tmdb_id)
        else:
            if stufe not in serien:
                serien[stufe] = await library.series_library(settings, stufe)
            nach_tvdb, nach_titel = serien[stufe]
            eintrag = nach_tvdb.get(request.tvdb_id) if request.tvdb_id else None
            if eintrag is None:
                eintrag = library.treffer_nach_titel(
                    nach_titel, request.title, library.jahr_aus(request.release_date)
                )

        if eintrag is not None and _noch_da(request, eintrag):
            continue
        # Zweite Quelle: der Media-Server. Wer den Titel nur aus Radarr
        # entfernt hat, hat ihn dort weiterhin - dann bleibt "geladen" wahr.
        #
        # ⚠️ **Nicht bei Staffelanfragen, solange die Serie in Sonarr steht.**
        # Die Media-Server-Tabelle kennt nur Titel, keine Staffeln - ihr
        # Treffer sagt "irgendetwas von Baywatch liegt in Plex". Damit hielt
        # ein Serien-Treffer jede geloeschte Staffel fuer immer auf "geladen",
        # und sie liess sich nie wieder anfragen. Steht die Serie in Sonarr
        # und meldet dort null Dateien fuer diese Staffel, ist das die
        # Autoritaet ueber die Platte. Nur wenn die Serie ganz aus Sonarr
        # verschwunden ist, bleibt der Titel-Treffer das Beste, was es gibt.
        if request.season is not None and eintrag is not None:
            pass  # Sonarr hat gesprochen: Staffel leer.
        elif mediaserver_library.vorhandene_kennungen(
            db, request.media_type, [request], stufe
        ):
            continue
        request.status = RequestStatus.deleted
        geloescht += 1

    db.commit()
    if fertig:
        logger.info("Status-Abgleich: %d Titel fertig geladen", fertig)
    if geloescht:
        logger.info("Status-Abgleich: %d geladene Titel sind verschwunden", geloescht)
    if verschwunden:
        logger.info(
            "Status-Abgleich: %d wartende Anfragen abgebrochen - Titel nicht mehr "
            "in Radarr/Sonarr",
            verschwunden,
        )
    return fertig


# Wie lange eine frisch uebergebene Anfrage geschont wird, bevor ihr
# Verschwinden als endgueltig gilt.
#
# ⚠️ Ohne diese Frist bricht der naechste Durchgang genau die Anfrage ab, die
# gerade erst an Radarr uebergeben wurde: Die Bibliothek wird kurz
# zwischengespeichert, und ein Titel, der vor dreissig Sekunden angelegt wurde,
# steht in der alten Antwort noch nicht drin.
SCHONFRIST_MINUTEN = 15


def _wirklich_weg(
    request: MediaRequest, settings: AppSettings, geladen: set[tuple[str, str]]
) -> bool:
    """Ist der Titel wirklich aus der Instanz verschwunden?

    Drei Bedingungen, und alle drei sind noetig:

    * Die Instanz **hat geantwortet**. Sonst waere jeder Ausfall ein Grund,
      reihenweise Anfragen abzubrechen.
    * Die Anfrage wurde bereits **uebergeben** (``approved`` oder
      ``searching``). Eine wartende Freigabe steht naturgemaess in keiner
      Bibliothek.
    * Sie liegt laenger als die Schonfrist zurueck - siehe dort.
    """
    if (request.media_type.value, request.tier.value) not in geladen:
        return False
    if request.status not in (RequestStatus.approved, RequestStatus.searching):
        return False
    seit = request.approved_at or request.requested_at
    if seit is None:
        return False
    # ⚠️ Aus der Datenbank kommen Zeiten **ohne** Zeitzone zurueck, ``utcnow``
    # liefert eine **mit** - der direkte Vergleich wirft einen TypeError.
    # Dasselbe ``.replace(tzinfo=None)`` steht an jeder anderen Stelle, die
    # gespeicherte Zeiten vergleicht (``cache``, ``tokens``, ``quota``).
    jetzt = utcnow().replace(tzinfo=None)
    if seit.tzinfo is not None:
        seit = seit.replace(tzinfo=None)
    return (jetzt - seit) > timedelta(minutes=SCHONFRIST_MINUTEN)


# Wie oft die Bibliothek des Media-Servers neu gelesen wird. Von Hand
# hineinkopierte Dateien sind die Ausnahme, nicht der Normalfall - stuendlich
# ist reichlich schnell und belastet den Server kaum.
BIBLIOTHEK_INTERVALL_SEKUNDEN = 3600
_bibliothek_zuletzt: float = 0.0


async def _bibliothek_vielleicht(db, settings) -> None:
    """Die Bibliothek des Media-Servers einlesen, wenn es an der Zeit ist.

    Faellt der Server aus, ist das kein Grund, den ganzen Durchgang scheitern
    zu lassen - der Rest des Abgleichs haengt nicht daran.
    """
    global _bibliothek_zuletzt
    if not settings.mediaserver_configured:
        return
    jetzt = time.monotonic()
    if jetzt - _bibliothek_zuletzt < BIBLIOTHEK_INTERVALL_SEKUNDEN:
        return
    _bibliothek_zuletzt = jetzt
    try:
        await mediaserver_library.refresh(db, settings)
        # Erst danach: Der Verlauf verweist auf Titel aus der Bibliothek und
        # laeuft ins Leere, solange die nicht eingelesen ist.
        await mediaserver_watched.refresh(db, settings)
    except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
        logger.exception("Media-Server konnte nicht abgeglichen werden")
        # **Die Sitzung zuruecksetzen, nicht nur den Fehler schlucken.**
        # Scheitert der Abgleich mitten im Schreiben, bleibt die Sitzung im
        # Zustand "muss zurueckgerollt werden" - und der naechste Schritt im
        # selben Durchgang (der Mailversand) stirbt an einem Fehler, mit dem
        # er nichts zu tun hat. Genau so gesehen: Auf jeden
        # "Media-Server konnte nicht abgeglichen werden" folgte prompt ein
        # "Status-Abgleich fehlgeschlagen: PendingRollbackError", und damit
        # ging in dem Durchgang keine einzige Mail raus.
        db.rollback()


# Wie oft die Speicher-Belegung neu erfasst wird. Dateigroessen aendern sich
# langsam: Ein Film waechst nur bei einer Aufwertung, eine Serie um eine Folge
# je Woche. Stuendlich ist reichlich.
SPEICHER_INTERVALL_SEKUNDEN = 3600
_speicher_zuletzt: float = 0.0


async def _speicher_vielleicht(db, settings) -> None:
    """Die Speicher-Belegung erfassen, wenn es an der Zeit ist.

    Laeuft **unabhaengig davon**, ob Kontingente nach Speicher eingestellt
    sind: Gemessen wird immer, begrenzt nur auf Wunsch. Sonst staende beim
    Umschalten nirgends eine Zahl, auf deren Grundlage sich eine Grenze
    festlegen liesse.
    """
    global _speicher_zuletzt
    # Ist der Schalter aus, wird nicht einmal gemessen: Die Funktion soll sich
    # dann verhalten, als gaebe es sie nicht.
    if not settings.storage_enabled:
        return
    if not (settings.radarr_configured or settings.sonarr_configured):
        return
    jetzt = time.monotonic()
    if jetzt - _speicher_zuletzt < SPEICHER_INTERVALL_SEKUNDEN:
        return
    _speicher_zuletzt = jetzt
    try:
        ergebnis = await storage.abgleichen(db, settings)
        if ergebnis.erster_lauf:
            logger.info(
                "Speicher-Belegung zum ersten Mal erfasst: %s Posten, alle im "
                "Hausbestand - jedes Konto startet bei null",
                ergebnis.neu,
            )
        elif ergebnis.neu or ergebnis.aktualisiert or ergebnis.entfernt:
            logger.info(
                "Speicher-Belegung: %s neu, %s geaendert (davon %s gewachsen), "
                "%s entfallen",
                ergebnis.neu,
                ergebnis.aktualisiert,
                ergebnis.gewachsen,
                ergebnis.entfernt,
            )
    except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
        logger.exception("Speicher-Belegung konnte nicht erfasst werden")
        # Siehe _bibliothek_vielleicht: Ohne das Zuruecksetzen stirbt der
        # naechste Schritt im selben Durchgang an einem fremden Fehler.
        db.rollback()


async def run_forever(stop: asyncio.Event) -> None:
    """Hintergrundschleife - laeuft, bis die Anwendung beendet wird."""
    while not stop.is_set():
        wartezeit = 120
        try:
            with SessionLocal() as db:
                settings = load_settings(db)
                wartezeit = settings.poll_interval_seconds
                if settings.radarr_configured or settings.sonarr_configured:
                    await check_once(db, settings)

                # Die Bibliothek des Media-Servers seltener - sie aendert sich
                # kaum, und ein voller Durchlauf kostet bei ein paar tausend
                # Titeln spuerbar mehr als eine Statusabfrage.
                await _bibliothek_vielleicht(db, settings)

                # Danach die Speicher-Belegung: Sie greift auf den gerade
                # aufgefrischten Bestand des Media-Servers zu, um Titel zu
                # erfassen, die Radarr/Sonarr nicht mehr kennen.
                await _speicher_vielleicht(db, settings)

                # Erst danach: so gehen die Mails zu gerade fertig gewordenen
                # Downloads schon in diesem Durchgang raus statt erst im
                # naechsten.
                await mail_outbox.process(db, settings)
        except ArrError as error:
            # Radarr/Sonarr gerade nicht erreichbar - kein Grund zur Aufregung.
            logger.info("Status-Abgleich übersprungen: %s", error.message)
            wartezeit = max(wartezeit, ERROR_BACKOFF_SECONDS)
        except Exception:  # noqa: BLE001 - die Schleife darf nie sterben
            logger.exception("Status-Abgleich fehlgeschlagen")
            wartezeit = max(wartezeit, ERROR_BACKOFF_SECONDS)

        try:
            await asyncio.wait_for(stop.wait(), timeout=wartezeit)
        except TimeoutError:
            continue
