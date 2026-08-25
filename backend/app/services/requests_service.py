"""Der Weg einer Anfrage: pruefen, speichern und an Radarr/Sonarr uebergeben."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    MediaRequest,
    MediaType,
    QualityTier,
    Notification,
    NotificationType,
    RequestStatus,
    Role,
    User,
    utcnow,
)
from ..schemas_media import MediaItem
from . import blocklist, library, mediaserver_library, notify, quota, storage
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
    db: Session,
    media_type: MediaType,
    tmdb_id: int,
    season: int | None = None,
    tier: QualityTier = QualityTier.standard,
) -> MediaRequest | None:
    """Laeuft zu diesem Titel schon eine Anfrage *dieser Stufe*?

    Bei Serien zaehlt die Staffel mit: Staffel 3 anzufragen muss moeglich
    bleiben, obwohl Staffel 2 schon laeuft. Eine Anfrage ueber die **ganze**
    Serie deckt dagegen jede einzelne Staffel mit ab - sonst liesse sich
    dasselbe zweimal bestellen.

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

    return db.scalar(select(MediaRequest).where(*bedingungen))


# Wie ein Anfrage-Zustand auf dem Badge heisst.
BADGE_FOR_STATUS = {
    RequestStatus.pending_approval: "pending_approval",
    RequestStatus.approved: "requested",
    RequestStatus.searching: "searching",
    RequestStatus.downloaded: "downloaded",
    RequestStatus.failed: "failed",
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
            MediaRequest.status.in_(ACTIVE_STATUSES + (RequestStatus.failed,)),
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


def requester_tag(username: str) -> str:
    """Etikett, das in Radarr/Sonarr zeigt, wer den Titel angefordert hat."""
    return f"nexview-{username.lower()}"


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
            MediaRequest.status.in_(ACTIVE_STATUSES),
        )
    )
    staffeln = {zeile.season for zeile in laufend if zeile.season is not None}
    if request.season is not None:
        staffeln.add(request.season)
    return staffeln


async def push_to_arr(db: Session, settings: AppSettings, request: MediaRequest) -> MediaRequest:
    """Freigegebene Anfrage tatsaechlich an Radarr bzw. Sonarr uebergeben."""
    try:
        if request.media_type == MediaType.movie:
            client = library.radarr_client(settings, request.tier.value)
            if client is None:
                raise ArrError(_nicht_eingerichtet("Radarr", request.tier))
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
                raise ArrError(_nicht_eingerichtet("Sonarr", request.tier))
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
        request.last_checked_at = utcnow()
        db.commit()
        logger.error(
            "Could not add %r (tmdb=%s) for user %r: %s%s",
            request.title,
            request.tmdb_id,
            request.user.username,
            error.message,
            " - Ausgang ungewiss, der Status-Abgleich prüft nach"
            if error.ungewiss
            else "",
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

    if not (settings.profile_choice(media_type) or darf_frei_waehlen):
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


def _kontingent_pruefen(
    db: Session, settings: AppSettings, user: User, media_type: MediaType
) -> None:
    """Darf dieser Nutzer noch anfragen?

    **Es gilt immer nur eine Waehrung.** Sind Speicher-Kontingente
    eingeschaltet, zaehlt der belegte Platz - und die Stueckzahl gar nicht
    mehr. Sonst umgekehrt.

    Beides gleichzeitig gaebe es zwei Gruende zu scheitern, die sich vollkommen
    unterschiedlich verhalten: Die Stueckzahl erneuert sich jeden Montag, der
    Platz nie; gegen das eine hilft warten, gegen das andere nur aufraeumen.
    Wer dann "ich kann nichts anfragen" meldet, zwingt den Administrator zum
    Raten, welche der beiden Grenzen gegriffen hat.
    """
    if settings.storage_enabled:
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
        return

    state = quota.state_for(db, user, media_type)
    if state.exhausted:
        art = "Filme" if media_type == MediaType.movie else "Serien"
        raise RequestError(
            f"Dein Kontingent für {art} ist aufgebraucht ({state.limit} pro "
            f"{_period_label(state.period.value)}).",
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
) -> MediaRequest:
    """Neue Anfrage anlegen - inklusive aller Vorpruefungen.

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
    # verworfen statt mit einem Fehler abgelehnt.
    if media_type != MediaType.tv:
        season = None

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
    ziel_erst_bei_freigabe = (
        settings.approver_picks_target(item.media_type) and not user.can_approve
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
    eigene_zurueckgestellte = db.scalar(
        select(MediaRequest).where(
            MediaRequest.user_id == user.id,
            MediaRequest.media_type == media_type,
            MediaRequest.tmdb_id == item.tmdb_id,
            MediaRequest.tier == tier,
            MediaRequest.season == season,
            MediaRequest.status == RequestStatus.deferred,
        )
    )
    if eigene_zurueckgestellte is not None:
        raise RequestError(
            f"„{item.title}“ steht bereits zurück – sobald du wieder Platz "
            "hast, kann die Anfrage freigegeben werden.",
            409,
        )

    existing = find_active(db, media_type, item.tmdb_id, season, tier)
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
        matched = await library.apply_status(settings, item.media_type, [item], tier.value)
        current = matched.items[0]
        if current.status in ("downloaded", "searching"):
            raise RequestError(
                f"„{item.title}“ ist bereits in deiner Bibliothek.",
                409,
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
        if settings.profile_choice(item.media_type) and profil in gesperrt:
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

    # Ohne Ordner darf nichts durchrutschen: eine automatisch freigegebene
    # Anfrage kaeme an keinem Entscheider vorbei, und genau der soll ja waehlen.
    sofort = user.auto_approve_for(media_type, tier) and not ziel_erst_bei_freigabe
    request = MediaRequest(
        user_id=user.id,
        media_type=media_type,
        tier=tier,
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        title=item.title,
        poster_path=item.poster_url,
        release_date=item.release_date,
        quality_profile_id=None if ziel_erst_bei_freigabe else quality_profile_id,
        root_folder_path=zielordner,
        season=season,
        status=RequestStatus.approved if sofort else RequestStatus.pending_approval,
        from_watchlist=from_watchlist,
        # Nur bei Serien sinnvoll - bei Filmen gibt es keine Folgestaffel.
        monitor_future=monitor_future and media_type == MediaType.tv,
    )
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

    child_wishes.erledigte_schliessen(db, media_type, item.tmdb_id)

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
        # Die Stufe der Anfrage entscheidet, aus welcher Instanz geloescht wird -
        # sonst bliebe die 4K-Datei liegen, waehrend Nexview "abgebrochen" meldet.
        client = (
            library.radarr_client(settings, request.tier.value)
            if request.media_type == MediaType.movie
            else library.sonarr_client(settings, request.tier.value)
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
    """
    zeilen = db.scalars(
        select(MediaRequest.season).where(
            MediaRequest.media_type == MediaType.tv,
            MediaRequest.tmdb_id == tmdb_id,
            MediaRequest.tier == tier,
            MediaRequest.status.in_(ACTIVE_STATUSES),
        )
    ).all()
    return set(zeilen)
