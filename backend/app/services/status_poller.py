"""Haelt den Zustand laufender Anfragen aktuell.

Radarr und Sonarr melden von sich aus nichts. Deshalb fragt Nexview in einem
Intervall nach, ob aus "wird gesucht" inzwischen "liegt da" geworden ist -
und benachrichtigt dann denjenigen, der den Titel angefragt hat.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    SpeicherVerlauf,
    MediaRequest,
    MediaType,
    NotificationType,
    RequestStatus,
    User,
    utcnow,
)
from . import (
    abgleich_kern,
    aufraeum_bericht,
    instanz_gesundheit,
    abgleich,
    instanz_stand,
    wiedergaben,
    updates,
    library,
    loeschfrist,
    mail_outbox,
    mediaserver_library,
    mediaserver_watched,
    notify,
    storage,
    ratings,
    requests_service,
    webhook_pflege,
    webhooks,
    zurueckgestellt,
)
from .arr import ArrError
from .settings_service import AppSettings, load_settings
from . import watch

logger = logging.getLogger("nexview.poller")

# Zustaende, bei denen sich noch etwas tun kann.
WATCHED_STATUSES = (RequestStatus.approved, RequestStatus.searching)

# Nach einem Fehler nicht sofort wieder loslaufen.
ERROR_BACKOFF_SECONDS = 60


async def _paket_groesse(settings, request: MediaRequest, eintrag, folgen: dict) -> int | None:
    """Summe der Episodendateien eines gerade fertig gewordenen Pakets.

    Nur fuer die Sofort-Zurechnung. Scheitert die Abfrage, bleibt es bei
    ``None`` - der stuendliche Abgleich traegt die Groesse dann nach.
    """
    arr_id = getattr(eintrag, "arr_id", None)
    client = library.sonarr_client(settings, request.tier.value)
    if not arr_id or client is None:
        return None
    staffel = folgen.get(request.season) or {}
    eigene = {
        folge.datei_id
        for nummer in (request.episodes or [])
        if (folge := staffel.get(nummer)) is not None and folge.datei_id
    }
    if not eigene:
        return 0
    try:
        dateien = await client.episode_files(arr_id, request.season)
    except ArrError:
        return None
    return sum(
        int(datei.get("size") or 0) for datei in dateien if datei.get("id") in eigene
    )


def _open_requests(db: Session) -> list[MediaRequest]:
    return list(
        db.scalars(select(MediaRequest).where(MediaRequest.status.in_(WATCHED_STATUSES)))
    )


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
    # Je Durchlauf hoechstens eine Heilung je Serie - mehrere Staffelanfragen
    # derselben Serie ergeben ohnehin dieselbe Menge.
    geheilt: set[tuple[str, int]] = set()
    verschwunden = 0

    # Folgengenaue Befunde, hoechstens einmal je Serie und Durchlauf geholt -
    # und nur fuer Serien, zu denen ein Folgen-Paket laeuft. Alle anderen
    # kosten weiterhin keinen einzigen zusaetzlichen Aufruf.
    folgen_befunde: dict[tuple[str, int], dict] = {}

    async def _folgen_befund(stufe: str, arr_id: int) -> dict:
        schluessel = (stufe, arr_id)
        if schluessel not in folgen_befunde:
            client = library.sonarr_client(settings, stufe)
            folgen_befunde[schluessel] = (
                await client.folgen_stand(arr_id) if client is not None else {}
            )
        return folgen_befunde[schluessel]

    # Die Warteschlangen fuer "laedt gerade" - hoechstens einmal je Instanz
    # und Durchlauf geholt, und nur, wenn ueberhaupt eine Suche laeuft.
    warteschlangen: dict[tuple[str, str], list] = {}

    async def _warteschlange(art: str, stufe: str) -> list:
        schluessel = (art, stufe)
        if schluessel not in warteschlangen:
            client = (
                library.radarr_client(settings, stufe)
                if art == "movie"
                else library.sonarr_client(settings, stufe)
            )
            try:
                warteschlangen[schluessel] = (
                    await client.warteschlange() if client is not None else []
                )
            except ArrError:
                # Instanz gerade stumm: Ohne Warteschlange fehlt nur die
                # Fortschritts-Anzeige - still weiter, das Erreichbarkeits-
                # Problem meldet sich an anderer Stelle ohnehin.
                warteschlangen[schluessel] = []
            except Exception:  # noqa: BLE001 - Anzeige darf den Abgleich nie umreissen
                # Alles andere ist ein echter Fehler und soll laermen - aber
                # als Protokollzeile, nicht als gescheiterter Durchgang. Genau
                # so gefunden: Ein unvollstaendiger Test-Fake liess frueher
                # den ganzen check_once sterben.
                logger.exception("Queue for %s/%s could not be read", art, stufe)
                warteschlangen[schluessel] = []
        return warteschlangen[schluessel]
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
            geantwortet = (request.media_type.value, request.tier.value) in geladen
            if abgleich_kern.ist_wirklich_weg(request, geantwortet):
                request.status = RequestStatus.cancelled
                request.completed_at = utcnow()
                request.laedt_fortschritt = None
                request.laedt_seit = None
                verschwunden += 1
                logger.warning(
                    "Request %s %r (%s/%s) cancelled: no longer present in %s",
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

        # Fuer Folgen-Pakete zaehlt der folgengenaue Befund.
        folgen = None
        if request.episodes:
            arr_id_befund = getattr(eintrag, "arr_id", None)
            if arr_id_befund:
                folgen = await _folgen_befund(stufe, arr_id_befund)

        # Ist der Titel inzwischen wirklich heruntergeladen?
        if abgleich_kern.ist_fertig(request, eintrag, folgen):
            request.status = RequestStatus.downloaded
            request.completed_at = utcnow()
            # Fertig heisst: nichts laedt mehr - die Anzeige raeumt mit auf.
            request.laedt_fortschritt = None
            request.laedt_seit = None
            # Belegten Platz sofort zurechnen, nicht erst beim stuendlichen
            # Abgleich: Wer gerade etwas angefragt hat und nachsieht, was es
            # ihn kostet, faende dort sonst bis zu eine Stunde lang eine Null
            # und hielte die Anzeige fuer kaputt.
            paket_bytes = None
            if request.episodes and folgen is not None:
                paket_bytes = await _paket_groesse(settings, request, eintrag, folgen)
            storage.verbuchen(db, request, eintrag, paket_bytes=paket_bytes)
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

        # "Laedt gerade": die Momentaufnahme aus der Warteschlange - gesetzt,
        # solange etwas vom Angefragten dort liegt, sonst wieder geloescht.
        # Der Grab-Anruf macht sie sofort sichtbar; ohne Draht erscheint sie
        # ueber den Takt binnen zwei Minuten.
        if request.status == RequestStatus.searching:
            fortschritt = abgleich_kern.laedt_fortschritt(
                request,
                eintrag,
                await _warteschlange(request.media_type.value, stufe),
            )
            if fortschritt is None:
                request.laedt_fortschritt = None
                request.laedt_seit = None
            else:
                if request.laedt_seit is None:
                    request.laedt_seit = utcnow()
                request.laedt_fortschritt = fortschritt

        # Die Ueberwachungs-Heilung - warum es sie gibt, steht bei der Frage:
        # ``abgleich_kern.heilung_noetig``. Entschieden wird dort, ausgefuehrt
        # hier - je Durchlauf hoechstens einmal je Serie.
        if abgleich_kern.heilung_noetig(request, eintrag, folgen):
            arr_id = getattr(eintrag, "arr_id", None)
            if (stufe, arr_id) not in geheilt:
                geheilt.add((stufe, arr_id))
                client = library.sonarr_client(settings, stufe)
                if client is not None:
                    try:
                        if request.episodes:
                            # Paket: Serie an, genau die eigenen Folgen an,
                            # Suche anstossen - dieselbe Strecke wie bei der
                            # Uebergabe, dort steht auch das Warum.
                            geschafft = await requests_service._folgen_einschalten(
                                client, arr_id, request
                            )
                            if geschafft:
                                logger.warning(
                                    "Monitoring healed: %r season %s episodes %s "
                                    "were off or not yet on (arr_id=%s)",
                                    request.title,
                                    request.season,
                                    request.episodes,
                                    arr_id,
                                )
                        else:
                            await client.monitor_seasons(
                                arr_id,
                                requests_service._gewollte_staffeln(db, request),
                                such_staffel=request.season,
                            )
                            logger.warning(
                                "Monitoring healed: %r season %s was switched off in Sonarr "
                                "(arr_id=%s)",
                                request.title,
                                request.season,
                                arr_id,
                            )
                    except ArrError as fehler:
                        logger.warning(
                            "Monitoring of %r could not be healed: %s",
                            request.title,
                            fehler.message,
                        )

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

        folgen = None
        if request.episodes and eintrag is not None:
            arr_id_befund = getattr(eintrag, "arr_id", None)
            if arr_id_befund:
                folgen = await _folgen_befund(stufe, arr_id_befund)
        if eintrag is not None and abgleich_kern.ist_noch_da(request, eintrag, folgen):
            # ⚠️ Der Titel liegt noch da - aber ist es noch **dieselbe** Datei?
            #
            # Radarr und Sonarr laden weiter, bis das Qualitaetsprofil erreicht
            # ist. Eine Bewertung galt der Fassung von damals; nach einer
            # Aufwertung ist sie eine Aussage ueber nichts mehr. Hier faellt es
            # auf, ohne einen zusaetzlichen Aufruf: Die Groesse steht in
            # derselben Antwort, die gerade "ist noch da" beantwortet hat.
            #
            # Nur bei Filmen und ganzen Serien: Bei einer Staffelanfrage ist
            # die Groesse am Serien-Eintrag die der *ganzen* Serie - sie waechst
            # auch, wenn eine andere Staffel aufgewertet wird, und die
            # Bewertung galt nicht der.
            if request.season is None:
                groesse = int(getattr(eintrag, "size_bytes", 0) or 0)
                ratings.entwerten(db, request.media_type, request.tmdb_id, groesse)
                # Aufwertung erkannt? Dann den Speicher-Abgleich vorziehen -
                # Verbuchung und "dein Posten ist gewachsen"-Meldung macht
                # er selbst, er soll es nur nicht erst zur vollen Stunde
                # erfahren. Der Upgrade-Anruf weckt den Rundgang, der
                # Rundgang zieht den Abgleich vor: eine Kette, ein
                # Wahrheitsweg.
                if storage.spuerbar_zugelegt(db, request, groesse):
                    logger.info(
                        "Upgrade spotted for %r - storage sync brought forward",
                        request.title,
                    )
                    _speicher_vorziehen()
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
        logger.info("Status sync: %d title(s) finished downloading", fertig)
    if geloescht:
        logger.info("Status sync: %d downloaded title(s) disappeared", geloescht)
    if verschwunden:
        logger.info(
            "Status sync: %d pending request(s) cancelled - title no longer in Radarr/Sonarr",
            verschwunden,
        )
    return fertig


# Wie oft die Bibliothek des Media-Servers neu gelesen wird. Von Hand
# hineinkopierte Dateien sind die Ausnahme, nicht der Normalfall - stuendlich
# ist reichlich schnell und belastet den Server kaum.
BIBLIOTHEK_INTERVALL_SEKUNDEN = 3600
_bibliothek_zuletzt: float = 0.0

#: Wie oft die "grosse" Messung laeuft: Datentraeger, Warteschlange,
#: Aktualisierung. Erreichbarkeit und Fassung werden dagegen **jede** Runde
#: gemessen - das ist eine winzige Antwort, und sie veraltet schnell.
MESSUNG_INTERVALL_SEKUNDEN = 3600
_messung_zuletzt: float = 0.0


async def _instanzen_messen(db, settings) -> None:
    """Erreichbarkeit jede Runde, der Rest stuendlich."""
    global _messung_zuletzt
    jetzt = time.monotonic()
    voll = jetzt - _messung_zuletzt >= MESSUNG_INTERVALL_SEKUNDEN
    if voll:
        _messung_zuletzt = jetzt
    try:
        await instanz_stand.messen(db, settings, voll=voll)
    except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
        logger.exception("Instance measurement failed")
        db.rollback()

    if not voll:
        return

    # Den eigenen Aktualisierungs-Stand warmhalten. ``status()`` fragt selbst
    # hoechstens einmal am Tag nach; der Aufruf hier kostet also fast nie
    # etwas und sorgt dafuer, dass das Befund-Register (das synchron ist und
    # nicht ins Netz greifen darf) ueberhaupt einen Stand vorfindet.
    try:
        await updates.status()
    except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
        logger.exception("Update check failed")

    try:
        wiedergaben.verlauf_aufraeumen(db)
    except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
        logger.exception("Playback history cleanup failed")
        db.rollback()

    try:
        _verlaufspunkt(db)
    except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
        logger.exception("Storage history point failed")
        db.rollback()

    # Der Abgleich der Quellen. Stuendlich und nicht auf Klick: Er laeuft
    # ueber tausende Zeilen und braucht die Bibliothek aus dem Netz.
    try:
        await abgleich.messen(db, settings)
    except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
        logger.exception("Reconciliation failed")
        db.rollback()


def _verlaufspunkt(db) -> None:
    """Einen Messpunkt fuer heute festhalten - genau einen.

    ⚠️ **Ueberschreiben statt ueberspringen.** Der Punkt des laufenden Tages
    wird bei jedem Durchgang aktualisiert; erst mit dem Datumswechsel entsteht
    ein neuer. Wuerde der erste Wert des Tages stehenbleiben, waere der
    juengste Punkt im Verlauf immer bis zu 24 Stunden alt - und die
    Hochrechnung "in sechs Wochen voll" damit systematisch zu optimistisch.
    """
    stand = instanz_stand.alle(db)
    traeger = None
    for zeile in stand.values():
        moeglich = (zeile.messwerte or {}).get("traeger")
        if isinstance(moeglich, list) and moeglich:
            traeger = moeglich
            break
    if not traeger:
        return

    frei = sum(int(t.get("frei") or 0) for t in traeger if isinstance(t, dict))
    gesamt = sum(int(t.get("gesamt") or 0) for t in traeger if isinstance(t, dict))
    if gesamt <= 0:
        return

    tag = utcnow().strftime("%Y-%m-%d")
    zeile = db.scalar(select(SpeicherVerlauf).where(SpeicherVerlauf.tag == tag))
    if zeile is None:
        zeile = SpeicherVerlauf(tag=tag)
        db.add(zeile)
    zeile.belegt_bytes = gesamt - frei
    zeile.frei_bytes = frei
    zeile.gemessen_am = utcnow()
    db.commit()


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
        logger.exception("Media server sync failed")
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


def _speicher_vorziehen() -> None:
    """Den stuendlichen Speicher-Abgleich sofort faellig machen.

    Gerufen, wenn der Status-Abgleich eine Aufwertung sieht. Weil
    ``_speicher_vielleicht`` in derselben Runde **nach** ``check_once``
    laeuft, geht die Meldung noch im selben Durchgang hinaus.
    """
    global _speicher_zuletzt
    _speicher_zuletzt = 0.0


async def _speicher_vielleicht(db, settings) -> None:
    """Die Speicher-Belegung erfassen, wenn es an der Zeit ist.

    Laeuft **immer**, unabhaengig davon, ob ueberhaupt jemand begrenzt ist:
    Gemessen wird stets, begrenzt nur auf Wunsch. Ohne das staende bei jedem
    eine Null, sobald der Betreiber zum ersten Mal eine Grenze setzen will -
    also genau dann, wenn er eine Zahl braucht, um eine sinnvolle zu waehlen.
    """
    global _speicher_zuletzt
    # "Irgendeine Instanz" - nicht nur die Standard-Plaetze (siehe run_forever).
    if not settings.arr_instanzen():
        return
    jetzt = time.monotonic()
    if jetzt - _speicher_zuletzt < SPEICHER_INTERVALL_SEKUNDEN:
        return
    _speicher_zuletzt = jetzt
    try:
        ergebnis = await storage.abgleichen(db, settings)
        if ergebnis.erster_lauf:
            logger.info(
                "Storage usage measured for the first time: %s item(s), all owned by the "
                "household - every account starts at zero",
                ergebnis.neu,
            )
        elif ergebnis.neu or ergebnis.aktualisiert or ergebnis.entfernt:
            logger.info(
                "Storage usage: %s new, %s changed (%s of them grown), %s gone",
                ergebnis.neu,
                ergebnis.aktualisiert,
                ergebnis.gewachsen,
                ergebnis.entfernt,
            )
    except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
        logger.exception("Storage usage could not be measured")
        # Siehe _bibliothek_vielleicht: Ohne das Zuruecksetzen stirbt der
        # naechste Schritt im selben Durchgang an einem fremden Fehler.
        db.rollback()


async def _faellige_loeschungen(db, settings) -> None:
    """Was seine Schonfrist ueberlebt hat, jetzt wirklich loeschen.

    ⚠️ **Der Punkt, an dem es keinen Rueckweg mehr gibt** (ausser dem
    Papierkorb von Radarr/Sonarr). Deshalb steht hier alles einzeln in einer
    eigenen Fehlerbehandlung: Scheitert eine Loeschung, sollen die anderen
    trotzdem laufen - und der Posten bleibt mit abgelaufener Frist stehen, wird
    es also beim naechsten Durchgang wieder versuchen, statt still zu
    verschwinden.

    Wer den Titel in der Frist angesehen hat, ist hier nicht mehr dabei: Die
    Vormerkung wurde dann laengst aufgehoben (``loeschfrist.angesehen_hebt_auf``,
    aufgerufen nach dem Abgleich der Sehdaten).
    """
    faellig = loeschfrist.faellig(db)
    if not faellig:
        return

    for zeile in faellig:
        titel, posten_id = zeile.title, zeile.id
        try:
            bytes_ = await storage.loeschen(db, settings, posten_id, wer="Schonfrist")
            logger.info(
                "Grace period over, deleted %r (%s bytes)", titel, bytes_
            )
        except Exception:  # noqa: BLE001 - eine Loeschung darf die anderen nicht mitnehmen
            logger.exception("Scheduled deletion of %r failed - will try again", titel)
            db.rollback()


# Wie viel Ruhe nach dem **Beginn** eines Rundgangs mindestens ist, bevor ein
# Anruf (Webhook) den naechsten ausloesen darf. Ein Massen-Import feuert je
# Datei einen Anruf; gebuendelt kosten sie einen Rundgang alle paar Sekunden
# statt fuenfzig hintereinander. Vorlaeufiger Wert - wird beim Bau der Pflege
# an einem echten Massen-Import nachgemessen (Bauplan "Draht statt Takt").
WECK_MINDESTABSTAND_SEKUNDEN = 10.0


async def _bis_zum_naechsten_durchgang(
    stop: asyncio.Event, wartezeit: float, frueheste_weckung: float
) -> None:
    """Schlafen bis zum Takt - oder frueher, wenn ein Anruf weckt.

    Drei Ausgaenge, und ihre Rangfolge ist Absicht:

    * ``stop`` gewinnt immer und sofort - das Herunterfahren wartet auf
      keinen Entprell-Abstand.
    * Ein Weckruf (``services.webhooks``) verkuerzt das Warten, aber
      fruehestens nach ``frueheste_weckung`` Sekunden. Anrufe in dieser
      tauben Phase gehen nicht verloren: Das Signal bleibt gesetzt und wird
      genau **einmal** verbraucht - aus zehn Anrufen wird ein vorgezogener
      Rundgang.
    * Sonst endet das Warten mit dem Takt (``wartezeit``).

    Das Signal wird beim Verlassen geloescht ("verbraucht"): Der nun folgende
    Rundgang deckt alles ab, was bis hierher angerufen hat. Was waehrend des
    Rundgangs anruft, setzt es erneut - und fuehrt zu genau einem Nachlauf.
    """
    weckruf = webhooks.weckruf()
    stop_warten = asyncio.create_task(stop.wait())
    weck_warten: asyncio.Task | None = None
    try:
        taub = min(frueheste_weckung, wartezeit)
        if taub > 0:
            fertig, _ = await asyncio.wait({stop_warten}, timeout=taub)
            if stop_warten in fertig:
                return
        rest = wartezeit - taub
        if rest <= 0:
            return
        weck_warten = asyncio.create_task(weckruf.wait())
        await asyncio.wait(
            {stop_warten, weck_warten},
            timeout=rest,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        # Aufgaben abraeumen statt nur abbrechen: Eine liegengelassene
        # Ausnahme (etwa ein Signal, das noch an einer frueheren
        # Ereignisschleife hing - der Fall der Tests) wuerde sonst erst beim
        # Aufraeumen des Speichers als Warnung laermen.
        stop_warten.cancel()
        if weck_warten is not None:
            weck_warten.cancel()
        for aufgabe in (stop_warten, weck_warten):
            if aufgabe is None:
                continue
            try:
                await aufgabe
            except (asyncio.CancelledError, RuntimeError):
                pass
        # Verbraucht - egal ob geweckt, Takt oder stop: Es folgt ohnehin ein
        # Rundgang (bzw. das Ende), und ein liegengebliebenes Signal wuerde
        # den uebernaechsten Durchgang grundlos sofort starten.
        weckruf.clear()


async def run_forever(stop: asyncio.Event) -> None:
    """Hintergrundschleife - laeuft, bis die Anwendung beendet wird."""
    while not stop.is_set():
        wartezeit = 120
        durchgang_start = time.monotonic()
        fehlgeschlagen = False
        try:
            with SessionLocal() as db:
                settings = load_settings(db)
                wartezeit = settings.poll_interval_seconds
                # ⚠️ "Irgendeine Instanz eingerichtet" - nicht nur die
                # Standard-Plaetze. Vorher zaehlten hier nur radarr/sonarr
                # ohne 4K: Eine reine 4K-Installation wurde nie abgeglichen.
                if settings.arr_instanzen():
                    await check_once(db, settings)

                    # Vorgemerkte Titel („Sag mir Bescheid"). Bewusst
                    # **neben** ``check_once`` und nicht darin: Das dort
                    # haengt an Anfragen und meldet, wenn *deine* Anfrage
                    # fertig wird. Eine Vormerkung hat keine Anfrage
                    # dahinter, also treibt sie dort nichts an.
                    #
                    # Und bewusst mit eigenem Auffangnetz: Ein Fehler beim
                    # Vormerken darf nicht die Statusabfrage mitreissen, an
                    # der die eigentlichen Anfragen haengen.
                    try:
                        await watch.pruefen(db, settings)
                    except Exception:  # noqa: BLE001
                        logger.exception("Watch check failed")

                # Die Bibliothek des Media-Servers seltener - sie aendert sich
                # kaum, und ein voller Durchlauf kostet bei ein paar tausend
                # Titeln spuerbar mehr als eine Statusabfrage.
                await _bibliothek_vielleicht(db, settings)

                # Danach die Speicher-Belegung: Sie greift auf den gerade
                # aufgefrischten Bestand des Media-Servers zu, um Titel zu
                # erfassen, die Radarr/Sonarr nicht mehr kennen.
                await _speicher_vielleicht(db, settings)

                # Der Rueckkanal-Eintrag in Radarr/Sonarr: stuendlich still
                # nachgesehen (fehlt er, wird er neu angelegt; weicht er ab,
                # nachgezogen), sofort faellig nach geaenderten Einstellungen
                # (webhook_pflege.gleich_wieder) und beim Start.
                try:
                    await webhook_pflege.vielleicht_pflegen(db, settings)
                except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
                    logger.exception("Webhook upkeep failed")
                    db.rollback()

                # Die Gesundheit der Instanzen - jede Runde, denn der Anruf
                # (onHealthIssue) ist nur ein Wecker: Er zieht den Rundgang
                # vor, und erst diese Nachfrage hier holt die Wahrheit. Die
                # Abfrage ist winzig; gemeldet wird einmal je Problem.
                try:
                    await instanz_gesundheit.pruefen(db, settings)
                except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
                    logger.exception("Instance health check failed")
                    db.rollback()

                # Was gerade laeuft - jede Runde, und das ist hier richtig:
                # Eine Spitze, die zwischen zwei Messungen liegt, ist verloren.
                # Gespeichert wird trotzdem nur eine Zeile je Viertelstunde,
                # sonst waeren es 260.000 im Jahr.
                try:
                    laufend = await wiedergaben.laufende(db, settings)
                    wiedergaben.spitze_merken(db, laufend)
                except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
                    logger.exception("Playback sampling failed")
                    db.rollback()

                # Direkt daneben, weil beides dieselbe Frage in zwei Haelften
                # beantwortet: Was die Instanz *ueber sich* meldet, steht
                # oben; was sich *an ihr* messen laesst - antwortet sie
                # ueberhaupt, wie voll ist die Platte, haengt etwas in der
                # Warteschlange - steht hier. Zusammen ergibt das den
                # Dienste-Teil des Dashboards.
                await _instanzen_messen(db, settings)

                # ⚠️ **Nach** der Speicher-Messung, nicht davor: Eine Datei,
                # die inzwischen ohnehin verschwunden ist, hat dann keinen
                # Posten mehr - und wir loeschen nichts, was es nicht mehr
                # gibt. Ausserdem ist der Sehstand zu diesem Zeitpunkt frisch,
                # eine Vormerkung also schon aufgehoben, wenn jemand den Titel
                # doch noch angesehen hat.
                await _faellige_loeschungen(db, settings)

                # Erst danach: so gehen die Mails zu gerade fertig gewordenen
                # Downloads schon in diesem Durchgang raus statt erst im
                # naechsten.
                await mail_outbox.process(db, settings)

                # Nach der Speichermessung: Der Platz-Kontingentstand ist
                # jetzt aktuell, und genau der entscheidet mit, ob eine
                # zurueckgestellte Anfrage wieder passt.
                try:
                    zurueckgestellt.zurueckholen(db, settings)
                except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
                    logger.exception("Deferred requests could not be checked")
                    db.rollback()

                # Zuletzt: Der monatliche Aufraeum-Bericht braucht die gerade
                # gemessenen Zahlen, und er darf nichts aufhalten. Der Dienst
                # entscheidet selbst, ob ueberhaupt etwas faellig ist - hier
                # steht nur der Takt.
                try:
                    await aufraeum_bericht.vielleicht_verschicken(db, settings)
                except Exception:  # noqa: BLE001 - Beiwerk, kein Grund zum Abbruch
                    logger.exception("Cleanup report could not be sent")
                    db.rollback()
        except ArrError as error:
            # Radarr/Sonarr gerade nicht erreichbar - kein Grund zur Aufregung.
            logger.warning("Status sync skipped: %s", error.message)
            wartezeit = max(wartezeit, ERROR_BACKOFF_SECONDS)
            fehlgeschlagen = True
        except Exception:  # noqa: BLE001 - die Schleife darf nie sterben
            logger.exception("Status sync failed")
            wartezeit = max(wartezeit, ERROR_BACKOFF_SECONDS)
            fehlgeschlagen = True

        if fehlgeschlagen:
            # Nach einem Fehlschlag gilt der Backoff auch fuer Anrufe: Ein
            # Anruf-Gewitter darf eine gerade erst gescheiterte Instanz nicht
            # sofort wieder treffen.
            frueheste_weckung = float(ERROR_BACKOFF_SECONDS)
        else:
            # Der Mindestabstand zaehlt ab **Beginn** des Rundgangs: Wer nach
            # langer Stille anruft, bekommt seinen Rundgang sofort - nur wer
            # draengelt, wird gebuendelt.
            frueheste_weckung = max(
                0.0,
                WECK_MINDESTABSTAND_SEKUNDEN - (time.monotonic() - durchgang_start),
            )
        await _bis_zum_naechsten_durchgang(stop, wartezeit, frueheste_weckung)
