"""Der Weg einer Anfrage: pruefen, speichern und an Radarr/Sonarr uebergeben."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import meldungen
from ..models import (
    MediaRequest,
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    RequestStatus,
    Role,
    User,
    utcnow,
)
from ..schemas_media import MediaItem
from . import (
    age_rating,
    blocklist,
    library,
    logs,
    media,
    mediaserver_library,
    notify,
    quota,
    regeln,
    serien_zuordnung,
    storage,
)
from .arr import ArrError
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
    "requester_tag",
    "resolve_root_folder",
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
    """Fachlicher Fehler mit lesbarer Meldung und passendem HTTP-Code.

    ⚠️ **Die Kennung leistet zweierlei.** Sie ist der Schluessel, unter dem die
    Oberflaeche den Satz in ihrer Sprache baut (``errors.byCode``, siehe
    ``meldungen``) - **und** der Marker, an dem anderer Python-Code den Fall
    erkennt. Beides faellt hier zusammen, weil es dieselbe Aussage ist.

    Die zwei, die eine tragen, haben etwas gemeinsam: Sie sagen nicht "das war
    falsch", sondern "der Titel ist schon da". Wer eine Anfrage im Namen eines
    anderen stellt (``child_wishes.freigeben``), muss diesen Unterschied
    kennen - sonst behandelt er "ist laengst da" wie "Kontingent voll" und
    laesst einen Wunsch offen, der nie mehr erfuellbar ist.

    Ohne Kennung liesse sich beides nur am deutschen Meldungstext festmachen -
    und an einem Text, an dem Verhalten haengt, traut sich niemand mehr eine
    Umformulierung zu.

    ⚠️ **Wer hier eine Kennung ergaenzt, braucht zwei Uebersetzungen.**
    ``test_fehlermeldungen`` findet jedes ``code="..."`` im Quelltext und
    besteht auf einem Eintrag in **beiden** Sprachdateien. Das ist kein
    Formalismus: Ohne ihn faellt die Oberflaeche auf den deutschen Text zurueck,
    und ein englischer Nutzer liest einen deutschen Satz.

    ``zahlen`` sind die Platzhalter dieses Satzes - hier immer der Titel.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: str | None = None,
        **zahlen: object,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.zahlen = zahlen

    def als_meldung(self) -> dict[str, object] | str:
        """Der Inhalt fuer ``detail`` - mit Kennung, wenn es eine gibt.

        Ohne Kennung bleibt es beim blossen Text, genau wie bisher. So mussten
        die drei Dutzend Fehler ohne Kennung nicht angefasst werden.
        """
        if not self.code:
            return self.message
        return meldungen.meldung(self.code, self.message, **self.zahlen)


#: Kennungen, die **"der Titel liegt bereits vor"** bedeuten - nicht "das war
#: falsch". Ein Wunsch, der daran scheitert, ist erfuellt und nicht abzulehnen.
#:
#: "Wurde bereits angefragt" gehoert ausdruecklich **nicht** hierher: Da laeuft
#: der Download noch. Dieser Wunsch schliesst sich von selbst, sobald die
#: laufende Anfrage fertig ist (``status_poller`` ruft
#: ``child_wishes.erledigte_schliessen``).
SCHON_DA = ("already_in_library", "already_on_media_server")


def find_active(
    db: Session,
    media_type: MediaType,
    tmdb_id: int,
    season: int | None = None,
    tier: QualityTier = QualityTier.standard,
    episodes: list[int] | None = None,
) -> MediaRequest | None:
    """Laeuft zu diesem Titel schon eine Anfrage *dieser Stufe*, die im Weg steht?

    Bei Serien gilt die Abdeckungs-Leiter: Die **ganze Serie** deckt jede
    Staffel ab, eine **Staffel** jede ihrer Folgen, ein **Folgen-Paket** genau
    seine Folgen. Staffel 3 anzufragen bleibt moeglich, obwohl Staffel 2
    laeuft - und ein Paket "Folge 1+2" bleibt moeglich, obwohl "Folge 5+6"
    laeuft. Aber je Folge gibt es hoechstens **einen** laufenden Besitzer:
    Sonst waere beim Loeschen und beim Speicher-Zurechnen unklar, wem die
    Datei gehoert.

    Eine ganze Staffel anzufragen, waehrend einzelne Folgen daraus laufen,
    scheitert deshalb ebenfalls - der Aufrufer nennt dann die belegten Folgen,
    und die Oberflaeche bietet den Rest an.

    Die Stufe gehoert zwingend dazu: Derselbe Film in 1080p **und** in 4K ist
    genau der Fall, um den es geht - das sind zwei Dateien in zwei Ordnern,
    also zwei Anfragen. Zweimal dieselbe Stufe bleibt gesperrt.
    """
    bedingungen = [
        MediaRequest.media_type == media_type,
        MediaRequest.tmdb_id == tmdb_id,
        MediaRequest.tier == tier,
        MediaRequest.status.in_(ACTIVE_STATUSES),
    ]

    if media_type == MediaType.tv and season is not None:
        bedingungen.append(
            (MediaRequest.season == season) | (MediaRequest.season.is_(None))
        )
        kandidaten = list(db.scalars(select(MediaRequest).where(*bedingungen)))
        for zeile in kandidaten:
            # Ganze Serie oder ganze Staffel - deckt jede Folge mit ab.
            if zeile.season is None or not zeile.episodes:
                return zeile
        if episodes is None:
            # Ganze Staffel gewuenscht: jedes laufende Paket steht ihr im Weg.
            return kandidaten[0] if kandidaten else None
        gewuenscht = set(episodes)
        for zeile in kandidaten:
            if gewuenscht & set(zeile.episodes or []):
                return zeile
        return None

    return db.scalar(select(MediaRequest).where(*bedingungen))


def angefragte_folgen(
    db: Session,
    tmdb_id: int,
    season: int,
    tier: QualityTier = QualityTier.standard,
) -> set[int] | None:
    """Welche Folgen dieser Staffel sind schon von laufenden Anfragen belegt?

    ``None`` heisst **alle**: Eine Anfrage ueber die ganze Serie oder die
    ganze Staffel deckt jede Folge ab. Eine Menge nennt die Folgen laufender
    Pakete. Fuer den Folgen-Waehler gilt dieselbe Regel wie bei den Staffeln:
    Belegtes wird angezeigt statt angeboten - sonst lehnte der Server die
    Auswahl anschliessend mit 409 ab.
    """
    zeilen = db.scalars(
        select(MediaRequest).where(
            MediaRequest.media_type == MediaType.tv,
            MediaRequest.tmdb_id == tmdb_id,
            MediaRequest.tier == tier,
            MediaRequest.status.in_(ACTIVE_STATUSES),
            (MediaRequest.season == season) | (MediaRequest.season.is_(None)),
        )
    )
    belegte: set[int] = set()
    for zeile in zeilen:
        if zeile.season is None or not zeile.episodes:
            return None
        belegte.update(zeile.episodes)
    return belegte


def _folgenliste(nummern: list[int]) -> str:
    """"3, 4 und 7" - fuer Meldungen, die einzelne Folgen nennen."""
    worte = [str(n) for n in nummern]
    if len(worte) == 1:
        return worte[0]
    return ", ".join(worte[:-1]) + " und " + worte[-1]


# Wie ein Anfrage-Zustand auf dem Badge heisst.
#
# ⚠️ **``failed`` steht hier bewusst nicht mehr.** Das Abzeichen am Titel gilt
# fuer *alle* - es sagt aus, was mit diesem Titel im Haus gerade geschieht.
# Eine fehlgeschlagene Anfrage geschieht aber nicht mehr: Sie laeuft nicht,
# haelt keinen Platz besetzt und ``find_active`` kennt sie nicht. Trotzdem
# stand an so einem Titel fuer jeden "Fehlgeschlagen", und niemand konnte ihn
# neu anfragen - obwohl der Server eine neue Anfrage angenommen haette.
#
# Alle anderen erledigten Zustaende - abgelehnt, abgebrochen, wieder
# geloescht - waren von jeher draussen. ``failed`` war der einzige Ausreisser.
BADGE_FOR_STATUS = {
    RequestStatus.pending_approval: "pending_approval",
    RequestStatus.approved: "requested",
    RequestStatus.searching: "searching",
    RequestStatus.downloaded: "downloaded",
}


def zurueckgestellte_schliessen(
    db: Session, anfrage: MediaRequest
) -> list[MediaRequest]:
    """Andere zurueckgestellte Anfragen zu **demselben** Titel abschliessen.

    ⚠️ **Ohne diese Regel wird die Speicher-Rechnung mehrdeutig.** Da eine
    zurueckgestellte Anfrage niemanden blockiert, koennen zwei Personen
    denselben Titel anfragen. Wuerden beide freigegeben, gaebe es **zwei**
    zurechenbare Anfragen fuer **eine** Datei - und ``storage._zuordnung``
    nimmt per ``setdefault`` die erste, die die Datenbank zufaellig liefert.
    Wem der Platz dann angerechnet wird, waere Glueckssache.

    Deshalb: Sobald **eine** Anfrage freigegeben wird, sind die anderen
    erledigt. Sie bekommen den Titel ja trotzdem - es ist dieselbe Datei.

    ``cancelled`` ist dabei die beste vorhandene Ablage: Sie zaehlt gegen
    nichts und blockiert nichts. Dass das Wort "abgebrochen" den Sachverhalt
    nicht ganz trifft, faengt die Benachrichtigung auf - sie sagt, dass der
    Titel jetzt da ist.

    Gibt die geschlossenen Anfragen zurueck, damit der Aufrufer benachrichtigen
    kann; ein ``commit`` macht er ebenfalls.
    """
    bedingungen = [
        MediaRequest.id != anfrage.id,
        MediaRequest.media_type == anfrage.media_type,
        MediaRequest.tmdb_id == anfrage.tmdb_id,
        MediaRequest.tier == anfrage.tier,
        MediaRequest.status == RequestStatus.deferred,
    ]
    # Bei Serien zaehlt die Staffel mit - dieselbe Regel wie in ``find_active``.
    if anfrage.media_type == MediaType.tv and anfrage.season is not None:
        bedingungen.append(
            (MediaRequest.season == anfrage.season) | (MediaRequest.season.is_(None))
        )

    andere = list(db.scalars(select(MediaRequest).where(*bedingungen)))
    if anfrage.media_type == MediaType.tv and anfrage.episodes:
        # Ein Paket deckt nur ab, was vollstaendig in ihm steckt. Wer mehr
        # wollte - die ganze Staffel, die ganze Serie oder ein groesseres
        # Paket -, bleibt zurueckgestellt: Er bekaeme sonst weniger, als er
        # bestellt hat, und die Meldung "ist jetzt da" waere gelogen.
        gedeckt = set(anfrage.episodes)
        andere = [
            zeile
            for zeile in andere
            if zeile.season == anfrage.season
            and zeile.episodes
            and set(zeile.episodes) <= gedeckt
        ]
    for zeile in andere:
        zeile.status = RequestStatus.cancelled
    return andere


def badges_for(
    db: Session,
    media_type: MediaType,
    tmdb_ids: list[int],
    tier: QualityTier = QualityTier.standard,
) -> dict[int, str]:
    """Eigene Anfragen zu diesen Titeln - fuer die Badges auf den Kacheln.

    Ohne das saehe ein Titel, den jemand angefragt hat und der auf Freigabe
    wartet, fuer alle weiterhin wie "nicht angefragt" aus.

    Je Stufe getrennt abgefragt: Eine laufende 4K-Anfrage darf das Abzeichen
    der Standard-Fassung nicht ueberschreiben - sonst saehe ein Film, den nur
    jemand in 4K angefragt hat, in 1080p faelschlich als angefragt aus.
    """
    if not tmdb_ids:
        return {}

    rows = db.scalars(
        select(MediaRequest).where(
            MediaRequest.media_type == media_type,
            MediaRequest.tmdb_id.in_(tmdb_ids),
            MediaRequest.tier == tier,
            MediaRequest.status.in_(ACTIVE_STATUSES),
        )
    )
    return {row.tmdb_id: BADGE_FOR_STATUS.get(row.status, "requested") for row in rows}


def eigene_laeuft(db: Session, user: User, media_type: MediaType, tmdb_id: int) -> bool:
    """Habe **ich** zu diesem Titel eine laufende Anfrage?

    Gebraucht fuer "Sag mir Bescheid": Wer selbst angefragt hat, bekommt die
    Fertig-Meldung ohnehin. Ihm den Knopf anzubieten hiesse, ihm zwei
    Nachrichten ueber dasselbe zu schicken - und die Frage zu stellen, auf die
    er die Antwort schon bestellt hat.

    Ohne Ruecksicht auf Stufe und Staffel: Wer die Serie in 1080p angefragt
    hat, wartet auf denselben Titel wie der, der auf 4K wartet. Fuer die
    Meldung ist das dasselbe Ereignis.
    """
    return db.scalar(
        select(MediaRequest.id).where(
            MediaRequest.user_id == user.id,
            MediaRequest.media_type == media_type,
            MediaRequest.tmdb_id == tmdb_id,
            MediaRequest.status.in_(ACTIVE_STATUSES),
        )
    ) is not None


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


def _nicht_eingerichtet(dienst: str, tier: QualityTier) -> str:
    """Fehlertext, der die Stufe mitnennt.

    Ohne den Zusatz stuende bei einer 4K-Anfrage "Radarr ist nicht
    eingerichtet", obwohl das normale Radarr laeuft - und niemand kaeme darauf,
    dass die *zweite* Instanz gemeint ist.
    """
    zusatz = " für 4K" if tier == QualityTier.uhd else ""
    return f"{dienst}{zusatz} ist nicht eingerichtet."


def _nicht_eingerichtet_fehler(dienst: str, tier: QualityTier) -> ArrError:
    """Dasselbe als ``ArrError`` - mit Kennung, damit es uebersetzbar bleibt.

    Zwei Kennungen statt einer mit Platzhalter: "Radarr für 4K" laesst sich
    nicht sauber aus Bausteinen zusammensetzen, ohne dass eine Sprache
    irgendwann daran zerbricht.
    """
    return ArrError(
        _nicht_eingerichtet(dienst, tier),
        code="arr_uhd_not_configured" if tier == QualityTier.uhd else "arr_not_configured",
        service=dienst,
    )


def requester_tag(username: str) -> str:
    """Etikett, das in Radarr/Sonarr zeigt, wer den Titel angefordert hat."""
    return f"nexview-{username.lower()}"


async def _radarr_eintrag(settings: AppSettings, request: MediaRequest):
    """Kennt Radarr diesen Film bereits? Sonst ``None``.

    ⚠️ **Das Gegenstueck zu ``_sonarr_eintrag`` - und es hat lange gefehlt.**
    Bei Serien wird seit jeher nachgesehen, bevor etwas angelegt wird; bei
    Filmen ging der Auftrag bedingungslos an Radarr. Liegt der Film dort
    schon, antwortet Radarr mit einem gewoehnlichen 400er, dessen Begruendung
    nur im Protokoll landet - die Anfrage wurde "fehlgeschlagen", und in der
    Freigabeliste blieb sie ohne einen einzigen Knopf stehen.

    Der Fall ist nicht selten: eine zweite Radarr-Instanz, ein von Hand
    hinzugefuegter Film, ein eingespielter Stand aus einer anderen
    Installation. Gemeldet wurde er, nachdem ein Film waehrend der offenen
    Freigabe ueber eine andere Instanz ins Haus kam.

    Liegt er schon da, wird nichts neu angelegt: Die Anfrage uebernimmt seine
    Radarr-Nummer und laeuft ganz gewoehnlich weiter. Hat er bereits eine
    Datei, setzt der naechste Rundgang sie auf "geladen" - dafuer braucht es
    hier keinen Sonderfall.

    Der Bestand kommt aus demselben Zwischenspeicher wie bei Serien. Er kann
    ein paar Minuten alt sein; in diesem Fenster schlaegt weiterhin Radarrs
    400er durch, und dafuer gibt es den Weg aus der Freigabeliste.
    """
    bestand = await library.movie_library(settings, request.tier.value)
    return bestand.get(request.tmdb_id)


async def _sonarr_eintrag(settings: AppSettings, request: MediaRequest):
    """Kennt Sonarr diese Serie bereits? Sonst ``None``.

    Erst ueber die TVDB-Kennung, ersatzweise ueber den normalisierten Titel -
    fuer viele neue Serien kennt TMDB noch keine TVDB-Kennung.
    """
    nach_tvdb, nach_titel = await library.series_library(settings, request.tier.value)
    if request.tvdb_id:
        treffer = nach_tvdb.get(request.tvdb_id)
        if treffer is not None:
            return treffer
    return library.treffer_nach_titel(
        nach_titel, request.title, library.jahr_aus(request.release_date)
    )


def _gewollte_staffeln(db: Session, request: MediaRequest) -> set[int]:
    """Alle Staffeln dieser Serie, zu denen eine Anfrage laeuft.

    ⚠️ **Warum die ganze Menge und nicht nur die neue Staffel.**
    ``addOptions.monitor: "none"`` wirkt bei Sonarr **asynchron**: Es raeumt
    nach dem Anlegen alles ab - auch das, was Nexview unmittelbar danach
    eingeschaltet hat. Nachgemessen an "Baywatch": Staffel 3 wurde freigegeben,
    angelegt und geladen; zwei Minuten spaeter kam die Freigabe fuer Staffel 2,
    las den inzwischen abgeraeumten Stand und schrieb ihn samt abgeschalteter
    Staffel 3 zurueck. In Nexview stand "wird gesucht", in Sonarr war die
    Staffel aus - sie waere nie gekommen.

    Deshalb ist **Nexview** die Quelle der Wahrheit und nicht der Zustand, den
    Sonarr gerade zeigt. Ein abgeraeumter Zustand heilt damit von selbst,
    sobald die naechste Staffel derselben Serie freigegeben wird.
    """
    if request.tvdb_id is None:
        return {request.season} if request.season is not None else set()
    laufend = db.scalars(
        select(MediaRequest).where(
            MediaRequest.media_type == MediaType.tv,
            MediaRequest.tvdb_id == request.tvdb_id,
            MediaRequest.tier == request.tier,
            MediaRequest.season.is_not(None),
            # ⚠️ Folgen-Pakete bleiben draussen: Ihre Staffel hier mitzunehmen
            # hiesse, die **ganze** Staffel einzuschalten - und Sonarr zoege
            # alles, obwohl nur einzelne Folgen bestellt sind. Pakete heilt
            # der Status-Abgleich folgengenau.
            MediaRequest.episodes.is_(None),
            MediaRequest.status.in_(ACTIVE_STATUSES),
        )
    )
    staffeln = {zeile.season for zeile in laufend if zeile.season is not None}
    if request.season is not None:
        staffeln.add(request.season)
    return staffeln


async def _folgen_einschalten(client, arr_id: int, request: MediaRequest) -> bool:
    """Die bestellten Folgen eines Pakets ueberwachen und suchen.

    ``True`` heisst erledigt. ``False`` heisst: Sonarr kennt die Folgen dieser
    Staffel **noch** nicht - direkt nach dem Anlegen laedt es die Metadaten
    asynchron nach. Das ist kein Fehler: Die Anfrage bleibt stehen, und der
    Status-Abgleich holt das Einschalten im naechsten Durchgang nach
    (dieselbe Heilung, die auch abgeraeumte Ueberwachung repariert).

    Kennt Sonarr die Staffel zwar, aber eine der bestellten Folgen nicht,
    ist das dagegen ein echter Fehler - die Nummer gibt es dort nicht.
    """
    stand = await client.folgen_stand(arr_id)
    staffel = stand.get(request.season) or {}
    if not staffel:
        if stand:
            raise ArrError(
                f"Sonarr kennt Staffel {request.season} dieser Serie nicht.",
                404,
                code="sonarr_season_unknown",
                season=request.season,
            )
        logger.info(
            "Episodes of %r season %s not listed in Sonarr yet - "
            "the status sync will switch them on",
            request.title,
            request.season,
        )
        return False

    fehlend = sorted(
        nummer for nummer in (request.episodes or []) if nummer not in staffel
    )
    if fehlend:
        raise ArrError(
            f"Sonarr kennt Folge {fehlend[0]} von Staffel {request.season} nicht.",
            404,
            code="sonarr_episode_unknown",
            season=request.season,
            episode=fehlend[0],
        )

    # Serie an (ohne Staffel-Flaggen), dann genau die eigenen Folgen - und
    # gesucht wird nur, was noch keine Datei hat.
    await client.serie_ueberwachen(arr_id)
    eigene = [staffel[nummer] for nummer in (request.episodes or [])]
    await client.folgen_schalten([folge.kennung for folge in eigene], True)
    await client.folgen_suchen(
        [folge.kennung for folge in eigene if not folge.has_file]
    )
    return True


async def _paket_uebergeben(client, request: MediaRequest, vorhanden) -> dict:
    """Ein Folgen-Paket an Sonarr uebergeben.

    Liegt die Serie noch nicht dort, wird sie **stumm** angelegt: keine
    Staffel-Ueberwachung, kein Nachschub, keine Suche - eingeschaltet wird
    danach folgengenau. Liegt sie schon dort, wird nichts neu angelegt,
    sondern nur geschaltet und gesucht.
    """
    if vorhanden is not None:
        arr_id = vorhanden.arr_id
        created: dict = {"id": arr_id}
    else:
        tag_id = await client.ensure_tag(requester_tag(request.user.username))
        created = await client.add(
            request.tvdb_id,
            request.quality_profile_id or 0,
            request.root_folder_path or "",
            tag_ids=[tag_id] if tag_id else None,
            nur_anlegen=True,
        )
        arr_id = created.get("id") if isinstance(created, dict) else None

    if arr_id:
        await _folgen_einschalten(client, arr_id, request)
    return created


async def push_to_arr(db: Session, settings: AppSettings, request: MediaRequest) -> MediaRequest:
    """Freigegebene Anfrage tatsaechlich an Radarr bzw. Sonarr uebergeben."""
    try:
        if request.media_type == MediaType.movie:
            client = library.radarr_client(settings, request.tier.value)
            if client is None:
                raise _nicht_eingerichtet_fehler("Radarr", request.tier)
            # Liegt der Film schon in Radarr, wird er nicht neu angelegt -
            # sonst antwortet Radarr mit einem 400er, und die Anfrage bliebe
            # als "fehlgeschlagen" liegen (siehe ``_radarr_eintrag``).
            vorhanden = await _radarr_eintrag(settings, request)
            if vorhanden is not None:
                logger.info(
                    "Radarr already holds %r (tmdb=%s) as #%s - linking the request "
                    "to it instead of adding it again",
                    request.title,
                    request.tmdb_id,
                    vorhanden.arr_id,
                )
                created = {"id": vorhanden.arr_id}
            else:
                tag_id = await client.ensure_tag(requester_tag(request.user.username))
                created = await client.add(
                    request.tmdb_id,
                    request.quality_profile_id or 0,
                    request.root_folder_path or "",
                    tag_ids=[tag_id] if tag_id else None,
                )
        else:
            client = library.sonarr_client(settings, request.tier.value)
            if client is None:
                raise _nicht_eingerichtet_fehler("Sonarr", request.tier)
            if not request.tvdb_id:
                # ⚠️ **Erst frisch nachfragen, dann aufgeben.** Die Kennung an
                # der Anfrage stammt aus dem Detail-Zwischenspeicher, und der
                # haelt sieben Tage. Neuen Serien fehlt die TVDB-Kennung bei
                # TMDB anfangs regelmaessig und wird spaeter nachgetragen -
                # ohne den Nachschlag scheiterte dieselbe Serie eine Woche
                # lang mit einer Begruendung, die laengst nicht mehr stimmte.
                # Der zusaetzliche TMDB-Aufruf faellt nur in diesem Fehlerfall
                # an. Live nachgemessen am 26./27.08.2026.
                request.tvdb_id = await media.tvdb_kennung_nachschlagen(
                    db, settings, request.tmdb_id
                )
                if request.tvdb_id:
                    db.commit()
                    logger.info(
                        "TVDB id for %r (tmdb=%s) appeared since the cached answer: %s",
                        request.title,
                        request.tmdb_id,
                        request.tvdb_id,
                    )
            if not request.tvdb_id:
                raise ArrError(
                    "Für diese Serie kennt TMDB noch keine TVDB-Kennung - "
                    "Sonarr kann sie deshalb nicht anlegen.",
                    code="tvdb_id_missing",
                )

            # Liegt die Serie schon in Sonarr, wird sie nicht neu angelegt -
            # das brächte die vorhandenen Folgen durcheinander. Stattdessen
            # wird nur die gewünschte Staffel aktiviert und gesucht.
            vorhanden = await _sonarr_eintrag(settings, request)
            if request.episodes:
                created = await _paket_uebergeben(client, request, vorhanden)
            elif vorhanden is not None and request.season is not None:
                await client.monitor_seasons(
                    vorhanden.arr_id,
                    _gewollte_staffeln(db, request),
                    such_staffel=request.season,
                )
                created = {"id": vorhanden.arr_id}
            else:
                tag_id = await client.ensure_tag(requester_tag(request.user.username))
                created = await client.add(
                    request.tvdb_id,
                    request.quality_profile_id or 0,
                    request.root_folder_path or "",
                    tag_ids=[tag_id] if tag_id else None,
                    season=request.season,
                    monitor_future=request.monitor_future,
                )
    except ArrError as error:
        # **Zeitueberschreitung ist kein Fehlschlag, sondern Ungewissheit.**
        # Der Auftrag kann angekommen und ausgefuehrt worden sein - nur die
        # Antwort kam nicht mehr an. "Fehlgeschlagen" zu schreiben war dann
        # schlicht falsch: In Sonarr lief die Suche, in Nexview stand ein
        # Fehler, und der Titel liess sich nicht einmal neu anfragen.
        #
        # Deshalb bleibt die Anfrage in diesem Fall auf "freigegeben" stehen.
        # Der Status-Abgleich sieht ohnehin alle 2 Minuten in der Bibliothek
        # nach und setzt sie auf "wird gesucht" bzw. "geladen", sobald der
        # Titel dort auftaucht - er klaert die Ungewissheit von selbst.
        request.status = (
            RequestStatus.approved if error.ungewiss else RequestStatus.failed
        )
        request.error_message = error.message
        request.error_detail = error.als_meldung() if error.code else None
        request.last_checked_at = utcnow()
        db.commit()
        logger.error(
            "Could not add %r (tmdb=%s) for user %r: %s%s",
            request.title,
            request.tmdb_id,
            request.user.username,
            logs.kennung(error),
            " - Ausgang ungewiss, der Status-Abgleich prüft nach"
            if error.ungewiss
            else "",
        )
        raise RequestError(error.message, 502) from error

    request.arr_id = created.get("id") if isinstance(created, dict) else None
    request.status = RequestStatus.searching
    request.error_message = None
    request.error_detail = None
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


async def _ziel_auswahl(
    settings: AppSettings, media_type: str, tier: str = "standard"
) -> tuple[list[str], list[int]]:
    """Welche Zielordner und Qualitaetsprofile kennt Radarr bzw. Sonarr?

    Eine Funktion fuer beide Listen, weil sie aus derselben Antwort stammen -
    getrennt geholt waeren es zwei Abfragen fuer dieselben Daten.
    """
    try:
        optionen = await library.options(settings, media_type, tier)
    except ArrError as fehler:
        raise RequestError(fehler.message, 502) from fehler

    ordner = [
        eintrag["path"] for eintrag in optionen.get("root_folders", []) if eintrag.get("path")
    ]
    profile = [
        eintrag["id"] for eintrag in optionen.get("quality_profiles", []) if eintrag.get("id")
    ]
    return ordner, profile


async def apply_target(
    settings: AppSettings,
    request: MediaRequest,
    *,
    root_folder_path: str | None = None,
    quality_profile_id: int | None = None,
) -> None:
    """Zielordner und Qualitaetsprofil einer wartenden Anfrage nachtragen.

    Gebraucht, wenn ``approver_picks_target`` gesetzt ist: Der Anfragende
    laesst beides offen, erst der Entscheider fuellt es aus.

    Was ankommt, wird gegen Radarr/Sonarr geprueft - ein selbstgebauter Aufruf
    darf sich weder einen beliebigen Pfad auf dem Server noch ein unbekanntes
    Profil aussuchen. Fehlt eine Angabe, wird **abgebrochen statt geraten**:
    Auf den Standardordner auszuweichen waere genau der Fehler, den diese
    Einstellung verhindern soll - der Titel laege dann im falschen Ordner,
    ohne dass jemand es merkt.

    Anders als ``resolve_root_folder`` fragt das hier **nicht** nach
    ``root_folder_choice``: Der Schalter regelt, ob der *Anfragende* waehlen
    darf. **Der Entscheider darf immer** - auch dann, wenn der Betreiber die
    Regel inzwischen umgestellt hat. Genau daran haengt, dass eine Anfrage
    ohne Ordner nie unfreigebbar wird; die Oberflaeche bietet ihm die Wahl
    deshalb an, sobald der Ordner fehlt, und nicht nur solange die Regel gilt.
    """
    art = request.media_type.value
    ordner, profile = await _ziel_auswahl(settings, art, request.tier.value)

    gewuenschter_ordner = root_folder_path or request.root_folder_path
    if not gewuenschter_ordner:
        raise RequestError("Bitte einen Zielordner für diese Anfrage wählen.", 422)
    if gewuenschter_ordner not in ordner:
        raise RequestError("Diesen Zielordner gibt es nicht.", 422)

    gewuenschtes_profil = quality_profile_id or request.quality_profile_id
    if not gewuenschtes_profil:
        raise RequestError("Bitte ein Qualitätsprofil für diese Anfrage wählen.", 422)
    # Leere Profilliste heisst "Radarr hat gerade nichts geliefert" - dann
    # nicht auch noch das mitgeschickte Profil verwerfen.
    if profile and gewuenschtes_profil not in profile:
        raise RequestError("Dieses Qualitätsprofil gibt es nicht.", 422)

    request.root_folder_path = gewuenschter_ordner
    request.quality_profile_id = gewuenschtes_profil


async def resolve_profile(
    settings: AppSettings,
    media_type: str,
    gewuenscht: int | None,
    tier: str = "standard",
    darf_frei_waehlen: bool = False,
) -> int:
    """Welches Qualitaetsprofil gilt fuer diese Anfrage?

    Spiegelbild zu ``resolve_root_folder``: Darf der Benutzer nicht waehlen,
    gilt das vom Administrator gesetzte Profil - egal was mitgeschickt wurde.
    Ein Aufruf am Formular vorbei soll sich keines aussuchen koennen.

    Administratoren waehlen immer frei; sie stellen die Vorgabe hier ein und
    muessten sonst an die eigene Einstellung nicht mehr heran.
    """
    _, bekannte = await _ziel_auswahl(settings, media_type, tier)

    if not (settings.profile_choice(media_type, tier) or darf_frei_waehlen):
        vorgabe = settings.default_profile_id(media_type, tier)
        if vorgabe and (not bekannte or vorgabe in bekannte):
            return vorgabe
        if bekannte:
            return bekannte[0]
        raise RequestError(
            "Es ist noch kein Qualitätsprofil eingerichtet.", 409
        )

    if gewuenscht is None:
        raise RequestError("Bitte ein Qualitätsprofil wählen.", 422)
    if bekannte and gewuenscht not in bekannte:
        raise RequestError("Dieses Qualitätsprofil gibt es nicht.", 422)
    return gewuenscht


async def resolve_root_folder(
    settings: AppSettings,
    media_type: str,
    gewuenscht: str | None,
    tier: str = "standard",
    darf_frei_waehlen: bool = False,
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
    vorhanden, _ = await _ziel_auswahl(settings, media_type, tier)

    if not vorhanden:
        raise RequestError(
            "In Radarr bzw. Sonarr ist kein Zielordner eingerichtet."
            if media_type == "movie"
            else "In Sonarr ist kein Zielordner eingerichtet.",
            409,
        )

    standard = settings.default_root(media_type, tier)

    # ``darf_frei_waehlen`` ist fuer Administratoren gesetzt. Vorher wurde ihre
    # Wahl hier stillschweigend verworfen, obwohl die Oberflaeche ihnen sehr
    # wohl eine Auswahl anbot und der Hinweistext "Administratoren waehlen
    # weiterhin frei" versprach - ein Widerspruch, der niemandem auffiel, weil
    # am Ende einfach der Standardordner benutzt wurde.
    if not (settings.root_folder_choice(media_type, tier) or darf_frei_waehlen):
        # Der eingestellte Ordner kann inzwischen aus Radarr/Sonarr verschwunden
        # sein - dann lieber der erste vorhandene als ein Fehlschlag beim
        # Hinzufuegen.
        return standard if standard in vorhanden else vorhanden[0]

    if gewuenscht:
        if gewuenscht not in vorhanden:
            raise RequestError("Diesen Zielordner gibt es nicht.", 422)
        return gewuenscht

    return standard if standard in vorhanden else vorhanden[0]


def _jahr_aus(datum: str | None) -> int | None:
    """Das Erscheinungsjahr aus einem TMDB-Datum. Ohne Datum kein Jahr."""
    if not datum or len(datum) < 4 or not datum[:4].isdigit():
        return None
    return int(datum[:4])


def _bestand_stufe(
    db: Session, media_type: MediaType, tmdb_id: int, tier: QualityTier
) -> str:
    """In welcher **anderen** Stufe liegt der Titel schon vor?

    ⚠️ **Die eigene Stufe kann hier nicht mehr auftauchen.** Dieselbe Stufe
    zweimal hat ``find_active`` weiter oben schon abgefangen - wer bis hierher
    kommt, fragt eine Stufe an, die es noch nicht gibt. Uebrig bleibt genau
    die Frage, um die es geht: Gibt es den Titel schon in der anderen?

    ⚠️ **Nur was Nexview kennt.** Ein Film, der vor Nexview in der Mediathek
    lag, hat hier keine Anfrage und zaehlt deshalb als "nichts". Das ist kein
    Versehen, sondern die Grenze dieser Auskunft: Der Bibliotheksabgleich
    weiter oben prueft die angefragte Stufe, nicht die andere.
    """
    andere = QualityTier.standard if tier == QualityTier.uhd else QualityTier.uhd
    vorhanden = db.scalar(
        select(MediaRequest.id).where(
            MediaRequest.media_type == media_type,
            MediaRequest.tmdb_id == tmdb_id,
            MediaRequest.tier == andere,
            MediaRequest.status.in_(ACTIVE_STATUSES),
        )
    )
    if vorhanden is None:
        return "nichts"
    return regeln.stufe_von(andere)


def _kontingent_pruefen(
    db: Session, settings: AppSettings, user: User, media_type: MediaType
) -> None:
    """Darf dieser Nutzer noch anfragen?

    **Beide Kontingente gelten immer.** Die Anfrage geht nur durch, wenn
    Stueckzahl *und* belegter Platz noch Luft haben. Bis 0.19 war das ein
    haus-weites Entweder-oder; dass beides gilt, ist die Entscheidung des
    Betreibers und kein Sonderfall mehr.

    Der Preis dafuer sind zwei Gruende zu scheitern, die sich vollkommen
    unterschiedlich verhalten: Die Stueckzahl erneuert sich jeden Montag von
    selbst, der Platz nie; gegen das eine hilft warten, gegen das andere nur
    aufraeumen. Deshalb ⚠️ **muss die Meldung sagen, welche der beiden Grenzen
    gegriffen hat** - sonst zwingt ein "ich kann nichts anfragen" den
    Administrator zum Raten. Wer nur nach einer Waehrung begrenzen will, laesst
    die andere auf "unbegrenzt" stehen.

    Zuerst die Stueckzahl: Sie ist die Grenze, gegen die man nichts tun kann
    ausser warten - das zuerst zu erfahren, erspart ein vergebliches Aufraeumen.
    """
    state = quota.state_for(db, user, media_type, settings)
    if state.exhausted:
        art = "Filme" if media_type == MediaType.movie else "Serien"
        raise RequestError(
            f"Dein Kontingent für {art} ist aufgebraucht ({state.limit} pro "
            f"{_period_label(state.period.value)}).",
            429,
        )

    stand = storage.stand_fuer(db, user, settings)
    if stand.exhausted:
        fehlt = -(stand.remaining_bytes or 0)
        raise RequestError(
            "Dein Speicher-Kontingent ist aufgebraucht. "
            f"Du belegst {_gb(stand.used_bytes)} von {_gb(stand.limit_bytes or 0)}"
            + (f" und liegst {_gb(fehlt)} darüber" if fehlt > 0 else "")
            + ". Gib etwas ab, dann geht es weiter.",
            429,
        )


def _gb(bytes_: int) -> str:
    """Bytes als lesbare GB-Angabe fuer eine Fehlermeldung."""
    gb = bytes_ / 1024**3
    return f"{gb:.1f} GB".replace(".", ",") if gb < 10 else f"{gb:.0f} GB"


async def _mit_datei_in_standard(settings: AppSettings, item: MediaItem) -> set[int]:
    """Fuehrt die **Standard**-Instanz diesen Titel mit Datei?

    Die eine Angabe, die ``mediaserver_library.echte_uhd_kennungen`` von aussen
    braucht - dort steht auch, wozu. Kurz: Nur so laesst sich eine 4K-Datei, die
    im normalen Radarr liegt, von einer echten Zweitfassung unterscheiden.

    Der Zustand wird vorher zurueckgesetzt: ``apply_status`` laesst ihn bei
    einem Fehlschlag stehen, und ein von anderswoher mitgebrachtes "downloaded"
    wuerde hier sonst als Treffer der Standard-Instanz gelesen.
    """
    kopie = item.model_copy(update={"status": "not_requested"})
    ergebnis = await library.apply_status(settings, item.media_type, [kopie], "standard")
    # "partial" zaehlt mit: Gefragt ist "fuehrt eine Datei", nicht "ist
    # vollstaendig" - eine halbe Serie liegt genauso in der Standard-Instanz.
    return {
        eintrag.tmdb_id
        for eintrag in ergebnis.items
        if eintrag.status in ("downloaded", "partial")
    }


#: Bis zu welchem Alter ein Titel als "gerade erst erschienen" gilt.
#:
#: Entscheidet nur, **welcher Satz** erscheint, wenn Sonarr die Serie nicht
#: kennt: "TheTVDB traegt das meist in ein paar Tagen nach" oder "daran wird
#: sich nichts mehr aendern". Ein Jahr ist gegriffen, nicht gemessen - aber
#: die Richtung stimmt: Bei "Ciné regards" von 1978 waere ein Vertroesten
#: schlicht falsch, und derjenige kaeme naechste Woche wieder.
FRISCH_TAGE = 365


def _frisch(release_date: str | None) -> bool:
    """Ist der Titel jung genug, dass TheTVDB ihn noch nachtragen wird?"""
    if not release_date:
        # Ohne Datum lieber vertroesten als abwuergen: Ein Titel ohne
        # Erstausstrahlung ist meist einer, der noch gar nicht lief.
        return True
    try:
        erschienen = datetime.strptime(release_date[:10], "%Y-%m-%d")
    except ValueError:
        return True
    return (utcnow().replace(tzinfo=None) - erschienen).days <= FRISCH_TAGE


def _darf_waehlen(settings: AppSettings, user: User) -> bool:
    """Darf dieser Mensch aus Sonarrs Vorschlaegen waehlen?

    ⚠️ **Das beantwortet nur die halbe Frage.** Ob ueberhaupt jemand
    davorsitzt, der antworten kann, weiss der **Weg** - siehe
    ``auswahl_moeglich`` in ``_tvdb_klaeren``.

    ⚠️ **Nein, sobald eine Altersbeschraenkung gilt.** Die Vorschlaege kommen
    aus Sonarr und sind damit an TMDB vorbei - und an TMDB haengt die
    Alterspruefung (``media._darf_sehen``). Wer waehlen darf, koennte sich
    ueber eine harmlose Anfrage eine beliebige Serie in die Bibliothek holen.

    Kinderkonten sind ohnehin aussen vor: Sie stellen keine Anfragen, sondern
    Wuensche, und die entscheiden die Eltern.
    """
    return settings.age_limit is None and user.role != Role.child


async def _tvdb_klaeren(
    db: Session,
    settings: AppSettings,
    user: User,
    item: MediaItem,
    tier: QualityTier,
    wahl: int | None,
    season: int | None,
    *,
    # ⚠️ **Ohne Vorgabe, mit Absicht.** Eine Vorgabe waere eine Annahme
    # darueber, ob am anderen Ende jemand antworten kann - und die kann nur
    # der Aufrufer treffen. ``create_request`` hat eine (aus), weil es der
    # oeffentliche Dienst ist; hier drinnen soll niemand raten muessen, und
    # ein vergessenes Argument faellt sofort auf statt still das Falsche zu
    # tun.
    auswahl_moeglich: bool,
) -> int | None:
    """Die TVDB-Kennung einer Serie klaeren - **bevor** die Anfrage entsteht.

    ⚠️ **Warum hier und nicht in ``push_to_arr``.** Dort scheiterte es bisher,
    und dort ist der Anfragende laengst weg: Bei einer Anfrage, die erst
    freigegeben werden muss, faellt der Fehler dem Entscheider vor die Fuesse -
    und der weiss nicht, welche Serie gemeint war. Fragen kann man nur den,
    der gerade davorsitzt.

    Drei Ausgaenge, und nur der erste ist stumm:

    * **Eindeutig** - ein Treffer traegt dieselbe TMDB-Kennung. Nichts zu
      fragen, die Anfrage laeuft durch.
    * **Vorschlaege** - Sonarr kennt aehnliche Serien. Die Oberflaeche zeigt
      sie zur Auswahl (``tvdb_choice_needed``) und schickt die Anfrage danach
      noch einmal, diesmal mit ``tvdb_id``. Nur, wenn ``auswahl_moeglich``
      gesetzt ist.

    ⚠️ **``auswahl_moeglich`` ist standardmaessig aus, und das mit Absicht.**
    Eine Rueckfrage taugt nur, wo jemand davorsitzt und antworten kann. Beim
    Freigeben eines **Kinderwunsches** ist das nicht so: Dort laeuft derselbe
    Dienst, aber die Oberflaeche kennt kein Auswahlfenster - das Elternteil
    laese "Bitte waehle die richtige aus" und haette nichts zum Waehlen, und
    der Wunsch bliebe fuer immer offen. Wer einen neuen Weg baut, bekommt
    deshalb die Auskunft, bis er ausdruecklich sagt, dass er fragen kann.
    * **Nichts** - dann sagt ``tvdb_id_missing``, woran es liegt, und ob
      Warten hilft.
    """
    client = library.sonarr_client(settings, tier.value)
    if client is None:
        # Ohne eingerichtetes Sonarr scheitert die Anfrage weiter unten mit
        # der Meldung, dass nichts eingerichtet ist - die ist hier die
        # bessere, und sie kommt von ``push_to_arr``.
        return None

    # ⚠️ **Der englische Titel gehoert dazu, sonst greift das hier gar nicht.**
    # TheTVDB ist englisch indiziert. In deutscher Oberflaeche liefert TMDB
    # fuer eine thailaendische Serie Titel *und* Originaltitel auf Thai -
    # Sonarr findet damit nichts, obwohl es unter dem englischen Namen zwanzig
    # Treffer hat. Live nachgemessen; ohne diese Zeile lief der Rueckfall bei
    # genau den Serien ins Leere, fuer die er gedacht ist.
    englisch = await media.englischer_titel(db, settings, "tv", item.tmdb_id)

    try:
        zuordnung = await serien_zuordnung.zuordnen(
            client, item.tmdb_id, item.title, item.original_title or "", englisch or ""
        )
    except ArrError as fehler:
        # ⚠️ **Ein stummes Sonarr darf die Anfrage nicht kippen.** Diese Suche
        # ist eine Zusatzchance, kein Teil der Pruefung: Vorher entstand die
        # Anfrage auch ohne TVDB-Kennung und scheiterte erst bei der Uebergabe -
        # mit der Meldung, dass Sonarr nicht erreichbar ist. Genau die ist hier
        # die richtige, und sie kommt von ``push_to_arr``. Wer stattdessen hier
        # abbraeche, liesse den Anfragenden ueber eine fehlende TVDB-Kennung
        # raetseln, waehrend in Wahrheit der Server aus war.
        logger.info(
            "Sonarr could not be asked about %r (tmdb=%s): %s",
            item.title,
            item.tmdb_id,
            logs.kennung(fehler),
        )
        return None
    if zuordnung.eindeutig:
        logger.info(
            "TVDB id for %r (tmdb=%s) came from Sonarr rather than TMDB: %s",
            item.title,
            item.tmdb_id,
            zuordnung.tvdb_id,
        )
        return zuordnung.tvdb_id

    if wahl is not None:
        # ⚠️ Die Zahl kommt aus dem Browser. Ungeprueft uebernommen waere sie
        # ein Weg, jede beliebige Serie anlegen zu lassen - siehe
        # ``serien_zuordnung.erlaubt``.
        if not serien_zuordnung.erlaubt(zuordnung, wahl):
            raise RequestError(
                "Diese Auswahl steht nicht mehr zur Verfügung. Bitte frage den Titel erneut an.",
                400,
                code="tvdb_choice_invalid",
                title=item.title,
            )
        return wahl

    if zuordnung.kandidaten and auswahl_moeglich and _darf_waehlen(settings, user):
        # ⚠️ **428 und nicht 409, und das ist keine Feinheit.** Die Oberflaeche
        # fragt Staffeln einzeln an und **verschluckt jeden 409** - "ist schon
        # angefragt" soll den Stapel nicht abbrechen (``AddRequestForm``).
        # Unter 409 waere dieses Fenster nie erschienen; der Anfragende saehe
        # "alle Staffeln bereits angefragt" und nie die Auswahl.
        raise RequestError(
            "Diese Serie ließ sich nicht automatisch zuordnen. Bitte wähle die richtige aus.",
            428,
            code="tvdb_choice_needed",
            title=item.title,
            fresh=_frisch(item.release_date),
            candidates=[
                {
                    "tvdb_id": k.tvdb_id,
                    "title": k.title,
                    "year": k.year,
                    "overview": k.overview,
                    "poster_url": k.poster_url,
                }
                for k in zuordnung.kandidaten
            ],
        )

    # Zwei Kennungen statt einer mit Schalter: Der Unterschied ist nicht die
    # Ursache, sondern der Rat. "Versuch es spaeter" bei einem Titel von 1978
    # waere eine Vertroestung, und uebersetzen laesst sich ein Satz, kein
    # Wahrheitswert.
    if _frisch(item.release_date):
        raise RequestError(
            "TheTVDB führt diese Serie noch nicht - bei neuen Titeln dauert das "
            "meist ein paar Tage.",
            422,
            code="tvdb_id_missing_new",
            title=item.title,
        )
    raise RequestError(
        "TheTVDB führt keinen Eintrag zu dieser Serie, und Sonarr kann sie "
        "deshalb nicht anlegen.",
        422,
        code="tvdb_id_missing",
        title=item.title,
    )


async def create_request(
    db: Session,
    settings: AppSettings,
    user: User,
    item: MediaItem,
    quality_profile_id: int | None,
    root_folder_path: str | None = None,
    season: int | None = None,
    tier: QualityTier = QualityTier.standard,
    from_watchlist: bool = False,
    monitor_future: bool = False,
    episodes: list[int] | None = None,
    tvdb_wahl: int | None = None,
    tvdb_auswahl_moeglich: bool = False,
) -> MediaRequest:
    """Neue Anfrage anlegen - inklusive aller Vorpruefungen.

    ``episodes`` macht aus der Staffel-Anfrage ein **Folgen-Paket**: Statt der
    ganzen Staffel kommen genau diese Folgen. Ein Paket kostet einen Platz wie
    eine Staffel - den echten Verbrauch misst das Speicher-Kontingent.

    ``from_watchlist`` haelt nur fest, **woher** der Klick kam: von der
    Merklisten-Seite statt aus dem Katalog. Am Ablauf aendert das nichts -
    es ist dieselbe Anfrage mit denselben Regeln.

    ``root_folder_path`` ist nur ein *Wunsch*. Welcher Ordner tatsaechlich
    gilt, entscheidet ``resolve_root_folder`` weiter unten - und zwar erst,
    nachdem feststeht, dass Radarr bzw. Sonarr ueberhaupt eingerichtet sind.
    Sonst bekaeme jemand ohne eingerichteten Dienst einen Verbindungsfehler
    statt der verstaendlichen Meldung, dass noch nichts eingerichtet ist.

    Ist ``approver_picks_target`` gesetzt, bleiben Ordner und Profil fuer
    gewoehnliche Benutzer offen und werden erst bei der Freigabe gesetzt
    (siehe ``apply_target``).

    ``tier`` waehlt die Instanz. Das Recht dafuer wird **hier** geprueft, nicht
    nur in der Oberflaeche: der fehlende Umschalter ist Bequemlichkeit, das
    hier ist die Sperre.
    """
    media_type = MediaType(item.media_type)
    # Eine Staffel ergibt nur bei Serien Sinn - bei Filmen wird sie still
    # verworfen statt mit einem Fehler abgelehnt. Folgen genauso.
    if media_type != MediaType.tv:
        season = None
        episodes = None

    # Folgen-Pakete: aufraeumen (sortiert, ohne Doppelte) und pruefen. Eine
    # leere Liste ist keine Auswahl, sondern "ganze Staffel".
    if episodes is not None:
        episodes = sorted({int(nummer) for nummer in episodes})
        if not episodes:
            episodes = None
    if episodes is not None:
        if not settings.episode_requests_enabled:
            raise RequestError(
                "Folgenweises Anfragen ist in diesem Haus abgeschaltet - "
                "wähle die ganze Staffel.",
                403,
            )
        if season is None:
            raise RequestError(
                "Einzelne Folgen gibt es nur zusammen mit einer Staffel.", 422
            )
        if episodes[0] < 0 or episodes[-1] > 2000:
            raise RequestError(
                "Folgennummern müssen zwischen 0 und 2000 liegen.", 422
            )
        # Ein Paket ist eine feste Liste - es folgt keinem Nachschub.
        monitor_future = False

    # 4K nur, wenn es dafuer auch eine Instanz gibt - und der Benutzer sie
    # nutzen darf. Beides serverseitig, sonst waere das Recht Dekoration.
    if tier == QualityTier.uhd:
        if not user.may_request_uhd(media_type):
            raise RequestError(
                "Für 4K-Anfragen fehlt dir die Berechtigung. "
                "Der Administrator kann sie freischalten.",
                403,
            )
        if not settings.arr_configured(item.media_type, "uhd"):
            raise RequestError(
                _nicht_eingerichtet(
                    "Radarr" if media_type == MediaType.movie else "Sonarr", tier
                ),
                409,
            )

    # Wer seine Mediathek in mehrere Ordner sortiert - etwa nach Genre - kann
    # die Wahl nicht dem Anfragenden ueberlassen: der kennt die Struktur nicht.
    # Dann faellt die Entscheidung erst bei der Freigabe.
    #
    # Entscheider und Administratoren sind ausgenommen: Sie waeren es, die bei
    # der Freigabe waehlen wuerden - sie waehlen also gleich jetzt. Der Umweg
    # ueber die eigene Warteschlange waere ein Klick, der nichts entscheidet,
    # und er widerspraeche der Regel "wer freigeben darf, gibt sich selbst frei".
    # Seit dem Kachel-Umbau je Instanz: Eine 4K-Anfrage kann auf den
    # Entscheider warten, waehrend dieselbe Person in Standard sofort
    # durchlaeuft - und die Sofort-Freigabe des Benutzers (auch die eigene
    # 4K-Sofort-Freigabe) ist hier bewusst uebersteuert.
    ziel_erst_bei_freigabe = (
        settings.approver_picks_target(item.media_type, tier.value)
        and not user.can_approve
    )

    # Die Sperrliste zuerst - und mit klarer Ansage. Anders als bei der
    # Altersbeschraenkung wird hier nichts versteckt: der Titel ist ja
    # sichtbar, also waere ein "gibt es nicht" schlicht gelogen. Wer anfragt,
    # soll erfahren, dass daraus nichts wird, statt es weiter zu versuchen.
    #
    # Die Pruefung steht bewusst auch hier im Dienst und nicht nur in der
    # Oberflaeche: der fehlende Knopf ist Bequemlichkeit, das hier ist die
    # Sperre.
    #
    # **Der Administrator ist ausgenommen.** Die Liste ist seine eigene
    # Entscheidung - sie soll die anderen bremsen, nicht ihn. Erst gab es die
    # Ausnahme nicht, und er musste den Titel zum Hinzufuegen freigeben; das
    # war ein Umweg ohne Gewinn. Der Eintrag bleibt dabei bestehen: fuer alle
    # anderen gilt die Sperre weiter.
    if user.role != Role.admin:
        gesperrt = blocklist.eintrag(db, media_type, item.tmdb_id)
        if gesperrt is not None:
            grund = f" Begründung: {gesperrt.reason}" if gesperrt.reason else ""
            raise RequestError(
                f"„{item.title}“ steht auf der Sperrliste und kann nicht angefragt "
                f"werden.{grund}",
                403,
            )

    # Ohne Radarr/Sonarr koennte aus der Anfrage nie etwas werden. Lieber
    # gleich sagen als eine Anfrage anlegen, die spaeter ins Leere laeuft.
    if tier == QualityTier.standard and media_type == MediaType.movie and not settings.radarr_configured:
        raise RequestError(
            "Radarr ist noch nicht eingerichtet - Filme können deshalb nicht "
            "angefragt werden. Der Administrator trägt die Zugangsdaten unter "
            "Einstellungen ein.",
            409,
        )
    if tier == QualityTier.standard and media_type == MediaType.tv and not settings.sonarr_configured:
        raise RequestError(
            "Sonarr ist noch nicht eingerichtet - Serien können deshalb nicht "
            "angefragt werden. Der Administrator trägt die Zugangsdaten unter "
            "Einstellungen ein.",
            409,
        )

    # Eine eigene zurueckgestellte Anfrage zaehlt zwar nicht als "aktiv" - sie
    # blockiert ja bewusst niemanden -, aber **zweimal dasselbe** soll auch
    # niemand von sich selbst haben. Sonst stuenden nach dem dritten Versuch
    # drei zurueckgestellte Anfragen desselben Titels in derselben Liste.
    eigene_zurueck = db.scalars(
        select(MediaRequest).where(
            MediaRequest.user_id == user.id,
            MediaRequest.media_type == media_type,
            MediaRequest.tmdb_id == item.tmdb_id,
            MediaRequest.tier == tier,
            MediaRequest.season == season,
            MediaRequest.status == RequestStatus.deferred,
        )
    )
    # Bei Folgen-Paketen blockiert nur, was sich wirklich ueberschneidet:
    # "Folge 1+2 steht zurueck" ist kein Grund, Folge 5+6 zu verweigern.
    eigene_zurueckgestellte = next(
        (
            zeile
            for zeile in eigene_zurueck
            if episodes is None
            or not zeile.episodes
            or (set(zeile.episodes) & set(episodes))
        ),
        None,
    )
    if eigene_zurueckgestellte is not None:
        # ⚠️ **Dieser Satz war lange nur halb wahr.** Er versprach eine
        # Automatik, die es nicht gab: Niemand holte eine zurueckgestellte
        # Anfrage zurueck, sie blieb liegen, bis ein Administrator zufaellig
        # hinsah. Seit ``services/zurueckgestellt`` stimmt er - deshalb steht
        # jetzt auch "von selbst" darin.
        raise RequestError(
            f"„{item.title}“ steht bereits zurück. Sobald du wieder Platz hast, "
            "kommt sie von selbst zurück zu den offenen Freigaben.",
            409,
        )

    existing = find_active(db, media_type, item.tmdb_id, season, tier, episodes)
    if existing is not None:
        if season is not None and existing.season is None:
            raise RequestError(
                f"„{item.title}“ ist bereits komplett angefragt - Staffel {season} ist damit abgedeckt.",
                409,
            )
        if season is not None and existing.episodes:
            # Ein laufendes Paket steht im Weg - die belegten Folgen beim
            # Namen nennen, damit die Oberflaeche den Rest anbieten kann.
            belegt_nummern = (
                sorted(set(existing.episodes) & set(episodes))
                if episodes
                else sorted(existing.episodes)
            )
            verb = "läuft" if len(belegt_nummern) == 1 else "laufen"
            raise RequestError(
                f"Folge {_folgenliste(belegt_nummern)} von Staffel {season} "
                f"(„{item.title}“) {verb} schon - wähle die übrigen Folgen.",
                409,
            )
        if season is not None and episodes:
            raise RequestError(
                f"Staffel {season} von „{item.title}“ wurde bereits komplett "
                "angefragt - deine Folgen sind damit abgedeckt.",
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
        matched = await library.apply_status(settings, item.media_type, [item], tier.value)
        current = matched.items[0]
        if current.status in ("downloaded", "searching"):
            raise RequestError(
                f"„{item.title}“ ist bereits in deiner Bibliothek.",
                409,
                code="already_in_library",
                titel=item.title,
            )

        # Zweite Quelle: der Media-Server. Wer einen Titel nach dem Laden aus
        # Radarr/Sonarr entfernt, hat ihn weiterhin in Plex - die Anzeige
        # weiss das laengst (Abzeichen "In der Bibliothek"), aber der fehlende
        # Knopf ist Bequemlichkeit, das hier ist die Sperre. Ohne sie liesse
        # ein veralteter Zwischenspeicher oder ein direkter Aufruf den Titel
        # ein zweites Mal herunterladen.
        #
        # Die Stufen-Frage ist dieselbe wie auf der Entdecken-Seite: Ohne
        # zweite Instanz zaehlt jede Kopie; mit ihr zaehlt nur die Kopie der
        # angefragten Stufe - eine reine 4K-Kopie darf die 1080p-Anfrage
        # nicht blockieren, sonst laesst sich die Standard-Fassung nie holen.
        #
        # ⚠️ Auf der 4K-Achse gilt dieselbe Regel wie beim Abzeichen, und zwar
        # **zwingend dieselbe**: Liefen Anzeige und Sperre auseinander, stuende
        # am Titel "4K noch nicht angefragt" und die Anfrage schluege trotzdem
        # fehl. Genau so ist es gemeldet worden.
        if tier == QualityTier.uhd:
            belegt = mediaserver_library.echte_uhd_kennungen(
                db,
                media_type,
                [item],
                in_standard_instanz=await _mit_datei_in_standard(settings, item),
            )
        else:
            belegt = mediaserver_library.vorhandene_kennungen(
                db,
                media_type,
                [item],
                "standard" if settings.arr_configured(item.media_type, "uhd") else None,
            )
        if belegt:
            raise RequestError(
                f"„{item.title}“ liegt bereits auf dem Media-Server.",
                409,
                code="already_on_media_server",
                titel=item.title,
            )

    # Beide Pruefungen entfallen, wenn erst der Entscheider waehlt: Es gibt
    # dann noch kein Profil zu sperren und keinen Ordner aufzuloesen. Die
    # Pruefung holt das ``apply_target`` bei der Freigabe nach.
    if not ziel_erst_bei_freigabe:
        # Das Profil erst aufloesen, dann die Sperrliste pruefen: Waehlt der
        # Benutzer gar nicht, kann ihm die Vorgabe des Administrators auch
        # nicht "gesperrt" sein.
        profil = await resolve_profile(
            settings,
            item.media_type,
            quality_profile_id,
            tier.value,
            darf_frei_waehlen=user.is_admin,
        )
        gesperrt = set(user.blocked_profiles(media_type, tier))
        # Sind *alle* Profile gesperrt, bliebe dem Benutzer keines uebrig und
        # er koennte gar nichts mehr anfragen - eine Sackgasse, aus der er
        # selbst nicht herausfindet. Eine Sperrliste, die alles sperrt, ist
        # offensichtlich nicht gemeint; sie wird dann ignoriert.
        #
        # Die Liste aller Profile wird nur geholt, wenn ueberhaupt etwas
        # gesperrt ist. Ohne diese Bedingung stellte *jede* Anfrage eine
        # zusaetzliche Abfrage an Radarr/Sonarr - fuer den Normalfall "nichts
        # gesperrt", in dem die Antwort gar nicht gebraucht wird.
        if gesperrt:
            _, alle_profile = await _ziel_auswahl(settings, item.media_type, tier.value)
            if alle_profile and gesperrt >= set(alle_profile):
                gesperrt = set()
        if settings.profile_choice(item.media_type, tier.value) and profil in gesperrt:
            raise RequestError(
                "Dieses Qualitätsprofil ist für dich gesperrt. Bitte wähle ein anderes.",
                403,
            )
        quality_profile_id = profil
        zielordner = await resolve_root_folder(
            settings,
            item.media_type,
            root_folder_path,
            tier.value,
            darf_frei_waehlen=user.is_admin,
        )
    else:
        zielordner = None

    _kontingent_pruefen(db, settings, user, media_type)

    # ⚠️ **Erst hier, ganz zum Schluss der Pruefungen.** Die Frage "welche
    # Serie meinst du?" kostet zwei Abfragen an Sonarr; sie zu stellen, waehrend
    # das Kontingent voll ist oder das Profil gesperrt, waere eine Rueckfrage
    # ohne Zweck. Und noch ist nichts geschrieben: Wer hier abbricht,
    # hinterlaesst keine halbe Anfrage.
    tvdb_id = item.tvdb_id
    if media_type == MediaType.tv and not tvdb_id:
        tvdb_id = await _tvdb_klaeren(
            db, settings, user, item, tier, tvdb_wahl, season,
            auswahl_moeglich=tvdb_auswahl_moeglich,
        )

    # ------------------------------------------------------------------
    # Die letzte Sprosse: sofort freigeben, ablehnen, oder zum Entscheider?
    #
    # ⚠️ **Alles davor ist bereits gelaufen** - Sperrliste, Bibliothek,
    # Medienserver, Qualitaetsprofil und vor allem das **Kontingent**. Eine
    # Regel entscheidet nur ueber Anfragen, die es bis hierher geschafft haben.
    # Sie kann nichts durchwinken, was aus einem anderen Grund schon
    # gescheitert ist; siehe den Kopf von ``services/regeln.py``.
    # ------------------------------------------------------------------
    titel_fuer_regeln = regeln.Titel(
        typ=media_type,
        qualitaet=regeln.stufe_von(tier),
        bestand=_bestand_stufe(db, media_type, item.tmdb_id, tier),
        genres=tuple(item.genre_ids),
        # ⚠️ ``vote_average`` ist 0.0, wenn **niemand** bewertet hat - TMDB
        # unterscheidet das nicht von einer echten Null. Eine Regel
        # "Bewertung unter 5 ablehnen" haette sonst jeden unbewerteten Titel
        # erwischt, und gerade neue Titel sind unbewertet.
        bewertung=item.vote_average if item.vote_count else None,
        stimmen=item.vote_count or None,
        jahr=_jahr_aus(item.release_date),
        laufzeit=item.runtime_minutes,
        sprache=item.original_language,
        altersfreigabe=age_rating.stufe(settings.default_region, item.certification or ""),
    )
    regel_ergebnis = regeln.entscheiden(db, user, titel_fuer_regeln)

    # ⚠️ **Hier wird protokolliert, nicht nur beim Scheitern.** Wenn jemand
    # fragt "warum ist das durchgelaufen?", ist die Antwort ohne diese Zeile
    # nicht mehr zu bekommen: Regeln lassen sich aendern und loeschen, die
    # Anfrage bleibt. Die Bedingungen stehen mit im Protokoll, weil dieselbe
    # Regel morgen andere haben kann.
    if regel_ergebnis is None:
        logger.info(
            "No rule matched for %r (%s, tier %s) - the account setting applies",
            item.title,
            media_type.value,
            tier.value,
        )
    else:
        logger.info(
            "Rule %d (%r) decided %r for %r: %s%s",
            regel_ergebnis.regel.id,
            regel_ergebnis.regel.name,
            "approve" if regel_ergebnis.freigeben else "reject",
            item.title,
            regel_ergebnis.regel.bedingungen,
            " (house stock)" if regel_ergebnis.hausbestand else "",
        )

    if regel_ergebnis is not None and not regel_ergebnis.freigeben:
        # ⚠️ **Die Anfrage entsteht trotzdem, als abgelehnt.** Sonst waere die
        # Absage ein Satz auf dem Bildschirm und danach spurlos: Der
        # Anfragende koennte nicht nachlesen, warum, und der Administrator
        # nie sehen, dass seine Regel zu scharf steht.
        #
        # ``rejected`` steht weder in ``ACTIVE_STATUSES`` noch in
        # ``COUNTED_STATUSES`` - der Titel bleibt fuer alle anderen frei, und
        # das Kontingent kostet die Ablehnung nichts.
        abgelehnt = MediaRequest(
            user_id=user.id,
            media_type=media_type,
            tier=tier,
            tmdb_id=item.tmdb_id,
            tvdb_id=tvdb_id,
            title=item.title,
            poster_path=item.poster_url,
            release_date=item.release_date,
            season=season,
            episodes=episodes,
            status=RequestStatus.rejected,
            rejection_reason=regel_ergebnis.begruendung or None,
            regel_id=regel_ergebnis.regel.id,
            from_watchlist=from_watchlist,
        )
        db.add(abgelehnt)
        db.commit()
        db.refresh(abgelehnt)
        return abgelehnt

    # Ohne Ordner darf nichts durchrutschen: eine automatisch freigegebene
    # Anfrage kaeme an keinem Entscheider vorbei, und genau der soll ja waehlen.
    #
    # Eine Regel setzt sich an die Stelle der Einstellung am Konto - aber
    # ``ziel_erst_bei_freigabe`` uebersteuert weiterhin beide.
    sofort = (
        regel_ergebnis.freigeben
        if regel_ergebnis is not None
        else user.auto_approve_for(media_type, tier)
    ) and not ziel_erst_bei_freigabe
    request = MediaRequest(
        user_id=user.id,
        media_type=media_type,
        tier=tier,
        tmdb_id=item.tmdb_id,
        tvdb_id=tvdb_id,
        title=item.title,
        poster_path=item.poster_url,
        release_date=item.release_date,
        quality_profile_id=None if ziel_erst_bei_freigabe else quality_profile_id,
        root_folder_path=zielordner,
        season=season,
        episodes=episodes,
        status=RequestStatus.approved if sofort else RequestStatus.pending_approval,
        from_watchlist=from_watchlist,
        # Nur bei Serien sinnvoll - bei Filmen gibt es keine Folgestaffel.
        monitor_future=monitor_future and media_type == MediaType.tv,
    )
    if regel_ergebnis is not None:
        request.regel_id = regel_ergebnis.regel.id
        request.hausbestand = regel_ergebnis.hausbestand
    if sofort:
        request.approved_by = user.id
        request.approved_at = utcnow()

    db.add(request)

    # Offene Kinderwuensche zu diesem Titel haben sich damit erledigt - egal,
    # wer die Anfrage gestellt hat. Holt ein Vater den Film einfach selbst,
    # saesse das Kind des Nachbarzimmers sonst weiter auf einem Wunsch, den
    # niemand mehr entscheiden kann.
    #
    # Der Import steht hier unten, weil ``child_wishes`` seinerseits diesen
    # Dienst braucht - oben waere es ein Ringschluss. Dieselbe Stelle deckt
    # jeden Weg ab: gewoehnliche Anfrage, freigegebener Wunsch, Admin-Anfrage.
    from . import child_wishes

    child_wishes.erledigte_schliessen(db, media_type, item.tmdb_id, season=season)

    db.commit()
    db.refresh(request)

    if sofort:
        return await push_to_arr(db, settings, request)

    _notify_admins(db, request)
    db.commit()
    return request


def _period_label(period: str) -> str:
    return {"day": "Tag", "week": "Woche", "month": "Monat"}.get(period, period)


async def _liegt_noch_dort(client, request: MediaRequest) -> bool:
    """Kennt Radarr/Sonarr diesen Titel unter seiner Kennung noch?

    Wird nur gefragt, wenn das Loeschen fehlgeschlagen ist - und beantwortet
    dann die einzige Frage, die zaehlt: War es ein echter Fehler, oder war der
    Titel ohnehin schon weg?

    ⚠️ **Bewusst ein frischer Aufruf und nicht die zwischengespeicherte
    Bibliothek.** Der Zwischenspeicher kann Minuten alt sein; wer gerade in
    Sonarr geloescht hat, staende darin noch drin - und der Abbruch scheiterte
    ein zweites Mal an derselben Ursache.

    Im Zweifel ``True``: Nur was nachweislich weg ist, gilt als weg. Antwortet
    die Instanz gar nicht mehr oder mit einem weiteren Fehler, bleibt es beim
    urspruenglichen Fehler - lieber eine Anfrage, die stehen bleibt, als die
    Behauptung, Dateien seien geloescht.
    """
    pfad = (
        f"/movie/{request.arr_id}"
        if request.media_type == MediaType.movie
        else f"/series/{request.arr_id}"
    )
    try:
        return await client.get(pfad) is not None
    except ArrError as nachfrage:
        return nachfrage.status_code != 404


def _weitere_aktive(db: Session, request: MediaRequest) -> list[MediaRequest]:
    """Welche anderen laufenden Anfragen wollen noch etwas von diesem Titel?

    Zeilen-, nicht nutzerbasiert: Auch die zweite Staffel desselben Nutzers
    zaehlt. ``pending_approval`` zaehlt bewusst mit - einer wartenden Anfrage
    soll ein fremder Abbruch nicht die Serie unter den Fuessen wegziehen.
    """
    return list(
        db.scalars(
            select(MediaRequest).where(
                MediaRequest.media_type == request.media_type,
                MediaRequest.tmdb_id == request.tmdb_id,
                MediaRequest.tier == request.tier,
                MediaRequest.status.in_(ACTIVE_STATUSES),
                MediaRequest.id != request.id,
            )
        )
    )


async def _serie_abbrechen(db: Session, client, request: MediaRequest) -> str:
    """Beim Abbruch einer Serien-Anfrage nur das selbst Bestellte entfernen.

    Frueher loeschte jeder Abbruch die **ganze Serie samt Dateien** - auch
    dann, wenn andere Nutzer andere Staffeln derselben Serie laufen hatten
    oder laengst fertig geladen waren. Jetzt faellt die Serie erst, wenn
    niemand mehr etwas von ihr will; sonst gehen nur die eigenen
    Staffel-Dateien, und die Staffel wird stillgelegt, damit Sonarr sie
    nicht im naechsten Suchlauf gleich wieder laedt.

    Gibt fuer das Protokoll zurueck, was tatsaechlich geschehen ist.
    """
    andere = _weitere_aktive(db, request)
    if not andere:
        await client.remove(request.arr_id, delete_files=True)
        return "removed the series including files"

    gewollte_staffeln = {anfrage.season for anfrage in andere}
    if None in gewollte_staffeln:
        # Jemand will weiterhin die ganze Serie - dann ist hier nichts zu
        # loeschen, jede Datei ist noch gedeckt.
        return "left all files in place - another request covers the whole series"

    if request.episodes:
        # Paket: genau die eigenen Folgen stilllegen und deren Dateien
        # loeschen. Die Folgen gehoeren nachweislich niemandem sonst - je
        # Folge gibt es hoechstens einen laufenden Besitzer.
        stand = await client.folgen_stand(request.arr_id)
        staffel = stand.get(request.season) or {}
        eigene = [
            folge
            for nummer in request.episodes
            if (folge := staffel.get(nummer)) is not None
        ]
        if eigene:
            await client.folgen_schalten([folge.kennung for folge in eigene], False)
        datei_ids = [folge.datei_id for folge in eigene if folge.datei_id]
        if datei_ids:
            await client.delete_episode_files(datei_ids)
        return (
            f"removed only episodes {', '.join(str(n) for n in request.episodes)} "
            f"of season {request.season} ({len(datei_ids)} files) - "
            "the series remains for other requests"
        )

    if request.season is not None:
        kennungen = [
            int(datei["id"])
            for datei in await client.episode_files(request.arr_id, request.season)
            if datei.get("id")
        ]
        await client.unmonitor_season(request.arr_id, request.season)
        if kennungen:
            await client.delete_episode_files(kennungen)
        return (
            f"removed only season {request.season} ({len(kennungen)} files) - "
            "the series remains for other requests"
        )

    # Bestand: eine Anfrage ueber die ganze Serie neben Staffeln anderer.
    # Anlegbar ist das laengst nicht mehr (``find_active`` sperrt es),
    # Altdaten koennen es aber noch enthalten. Dann gilt das Muster der
    # Konto-Aufloesung: stilllegen und nur die Staffeln loeschen, die
    # niemand will - die Ueberwachung der laufenden fremden Staffeln heilt
    # der Status-Abgleich im naechsten Durchgang.
    await client.serie_stilllegen(request.arr_id)
    dateien = await client.get("/episodefile", {"seriesId": request.arr_id}) or []
    kennungen = [
        int(datei["id"])
        for datei in dateien
        if isinstance(datei, dict)
        and datei.get("id")
        and datei.get("seasonNumber") not in gewollte_staffeln
    ]
    if kennungen:
        await client.delete_episode_files(kennungen)
    return (
        f"froze the series and removed {len(kennungen)} files "
        "of seasons nobody else wants"
    )


async def cancel(
    db: Session, settings: AppSettings, request: MediaRequest
) -> MediaRequest:
    """Eine laufende Anfrage abbrechen.

    Entfernt wird nur das selbst Bestellte: ein Film ganz, eine Serie erst
    dann komplett, wenn keine andere laufende Anfrage mehr etwas von ihr
    will - sonst nur die eigenen Staffel-Dateien. Das Kontingent des
    Anfragenden wird wieder frei, weil ``cancelled`` nicht mitgezaehlt wird.

    ⚠️ **Auch fehlgeschlagene Anfragen lassen sich abbrechen** - seit 0.26.
    Vorher war ``failed`` eine Sackgasse: freigeben ging nicht mehr (dafuer
    ist der Zustand zu spaet), abbrechen war verboten, und einen Loesch-Knopf
    gab es in der Oberflaeche nie. Die Anfrage blieb sichtbar liegen, und der
    Besteller wartete auf etwas, das nie kommen wuerde. Zu entfernen gibt es
    dabei in aller Regel nichts - ohne ``arr_id`` ueberspringt der Weg unten
    Radarr und Sonarr ohnehin.
    """
    if request.status not in (
        RequestStatus.approved,
        RequestStatus.searching,
        RequestStatus.failed,
    ):
        raise RequestError(
            "Nur laufende oder fehlgeschlagene Anfragen können abgebrochen werden.",
            409,
        )

    umfang = "removed it including files"
    if request.arr_id:
        # Die Stufe der Anfrage entscheidet, aus welcher Instanz geloescht wird -
        # sonst bliebe die 4K-Datei liegen, waehrend Nexview "abgebrochen" meldet.
        client = (
            library.radarr_client(settings, request.tier.value)
            if request.media_type == MediaType.movie
            else library.sonarr_client(settings, request.tier.value)
        )
        if client is not None:
            try:
                if request.media_type == MediaType.movie:
                    await client.remove(request.arr_id, delete_files=True)
                else:
                    umfang = await _serie_abbrechen(db, client, request)
            except ArrError as error:
                # 404 heisst: dort schon weg - dann ist das Ziel ja erreicht.
                #
                # ⚠️ **Sonarr sagt aber nicht immer 404.** Wer eine Serie in
                # Sonarr von Hand entfernt und danach in Nexview abbricht,
                # bekam gemessen einen **500er** auf dasselbe Loeschen:
                #
                #     DELETE /api/v3/series/213?deleteFiles=true -> 500
                #     POST /api/requests/6/cancel -> 502
                #
                # Damit war die Anfrage nicht mehr loszuwerden: Abbrechen
                # scheiterte immer wieder an einer Serie, die es laengst nicht
                # mehr gab. Ein 500er darf trotzdem nicht einfach als Erfolg
                # gelten - dann behauptete Nexview geloeschte Dateien, die
                # weiter auf der Platte liegen. Also wird **nachgesehen**:
                # Ist der Titel unter dieser Kennung wirklich weg, ist das
                # Ziel erreicht; liegt er noch dort, war es ein echter Fehler.
                if error.status_code != 404 and await _liegt_noch_dort(client, request):
                    raise RequestError(error.message, 502) from error
                umfang = "it was already gone there"

    request.status = RequestStatus.cancelled
    request.completed_at = utcnow()
    request.arr_id = None
    db.commit()

    logger.warning(
        "Cancelled %r (tmdb=%s) for user %r - %s",
        request.title,
        request.tmdb_id,
        request.user.username,
        umfang,
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


def angefragte_staffeln(
    db: Session, tmdb_id: int, tier: QualityTier = QualityTier.standard
) -> set[int | None]:
    """Zu welchen Staffeln dieser Serie laeuft schon eine Anfrage *dieser Stufe*?

    ``None`` in der Menge heisst: Es gibt eine Anfrage ueber die **ganze**
    Serie, und die deckt jede Staffel ab.

    Bewusst ueber **alle** Nutzer und nicht nur den anfragenden: ``find_active``
    sperrt eine laufende Anfrage fuer alle. Wuerde die Oberflaeche nur die
    eigenen ausblenden, saehe ein zweiter Nutzer eine waehlbare Staffel, die
    der Server anschliessend mit 409 ablehnt - und verstuende nicht, warum.

    Die Stufe gehoert dazu - aus demselben Grund wie in ``find_active``:
    Dieselbe Staffel in 1080p **und** 4K sind zwei Dateien in zwei Instanzen,
    also zwei Anfragen. Vorher galt die Staffel hier stufenuebergreifend als
    belegt, und die Auswahl graute eine 4K-Anfrage aus, die der Server
    laengst erlaubt haette.

    ⚠️ **Folgen-Pakete zaehlen hier nicht.** "Belegt" heisst fuer die
    Oberflaeche "ganz vergeben" - eine Staffel mit zwei laufenden Folgen ist
    aber weiter waehlbar, der Rest gehoert noch niemandem. Was ein Paket
    belegt, sagt ``angefragte_pakete`` je Folge.
    """
    zeilen = db.scalars(
        select(MediaRequest.season).where(
            MediaRequest.media_type == MediaType.tv,
            MediaRequest.tmdb_id == tmdb_id,
            MediaRequest.tier == tier,
            MediaRequest.episodes.is_(None),
            MediaRequest.status.in_(ACTIVE_STATUSES),
        )
    ).all()
    return set(zeilen)


def angefragte_pakete(
    db: Session, tmdb_id: int, tier: QualityTier = QualityTier.standard
) -> dict[int, dict[int, str]]:
    """Welche Folgen je Staffel sind von laufenden Paketen belegt - und wie?

    Das Gegenstueck zu ``angefragte_staffeln`` fuer die dritte Ebene, je
    Folge mit dem **Status** der belegenden Anfrage (je Folge gibt es genau
    einen Besitzer). Die Auswahl macht daraus ehrliche Worte: "wartet" ist
    etwas anderes als "laeuft" - eine wartende Freigabe kann noch abgelehnt
    werden, und wer das nicht sieht, wundert sich spaeter zu Recht.
    """
    zeilen = db.scalars(
        select(MediaRequest).where(
            MediaRequest.media_type == MediaType.tv,
            MediaRequest.tmdb_id == tmdb_id,
            MediaRequest.tier == tier,
            MediaRequest.episodes.is_not(None),
            MediaRequest.status.in_(ACTIVE_STATUSES),
        )
    )
    belegt: dict[int, dict[int, str]] = {}
    for zeile in zeilen:
        if zeile.season is None or not zeile.episodes:
            continue
        staffel = belegt.setdefault(zeile.season, {})
        for nummer in zeile.episodes:
            staffel[nummer] = zeile.status.value
    return belegt


def staffel_belegung(
    db: Session, tmdb_id: int, tier: QualityTier = QualityTier.standard
) -> dict[int | None, str]:
    """Der Status der deckenden Voll-Anfrage je Staffel (``None`` = ganze Serie).

    Nur volle Abdeckungen - Pakete stehen in ``angefragte_pakete``. Je
    Staffel kann es hoechstens eine aktive Voll-Anfrage geben, der Status
    ist also eindeutig.
    """
    zeilen = db.scalars(
        select(MediaRequest).where(
            MediaRequest.media_type == MediaType.tv,
            MediaRequest.tmdb_id == tmdb_id,
            MediaRequest.tier == tier,
            MediaRequest.episodes.is_(None),
            MediaRequest.status.in_(ACTIVE_STATUSES),
        )
    )
    return {zeile.season: zeile.status.value for zeile in zeilen}
