"""Downloads im NEX-Betrieb: die Warteschlange und was in ihr klemmt.

Der große Unterschied zu Radarr und Sonarr: **nexcrate sagt selbst, was
kaputt ist und was man dagegen tun darf** (N27). Nexview unterscheidet bei Arr
rund dreißig Störungsgründe über Textmuster, je Grund mit eigener Tabelle
erlaubter Aktionen; hier steht beides in der Antwort. Nexviews Tabelle gilt
deshalb im NEX-Betrieb nicht - nexcrates `actions` und `automatic` sind die
einzige Erlaubnis (Bauplan 6.6).

⚠️ **Ein Eintrag je Download** (N26), nicht je Zeile: Ein Staffelpaket hat bei
Sonarr eine Zeile je Folge, bei nexcrate eine einzige mit der Liste ihrer
Folgen. Zu falten gibt es nichts.

⚠️ **Hinweise sind keine Hänger.** `needs_owner: false` heißt „schau mal, wenn
du Zeit hast"; nur `true` füllt die Tabelle und damit die Befunde
(`nachschub.eingriff_noetig`) und `import_haengt` an der Anfrage.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from ....models import DownloadHaenger, utcnow
from ..base import Aktion
from . import mapping

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...settings_service import AppSettings, ArrInstanz

logger = logging.getLogger("nexview.nexcrate")

#: Die Problemcodes, die nexcrate am 22.09.2026 führte (`download_store.PROBLEMS`).
#:
#: ⚠️ **Keine Liste, gegen die geprüft wird.** Ein Code, den Nexview nicht
#: kennt, kommt trotzdem durch und steht dann als Kennung da - nexcrate darf
#: wachsen, ohne dass hier jemand nachzieht. Sie steht nur, damit der Wächter
#: über die Texte (`tests/test_download_gruende.py`) weiß, wofür es
#: Übersetzungen geben soll und wofür nicht.
PROBLEME: frozenset[str] = frozenset(
    {
        "path_not_found",
        "packed",
        "no_video",
        "no_space",
        "gone_from_client",
        "client_error",
        "import_failed",
        "dangerous_file",
        "encrypted",
        "client_unreachable",
        "stalled",
        "files_unassigned",
        "other_series_suspected",
        "several_videos",
        "import_stalled",
        "too_many_files",
        "multi_part",
        "no_audio",
        "album_single_file",
        "album_not_better",
        "album_tracks_missing",
        "download_failed",
        # Seit nexcrate 46c42cd (24.09.2026): ein Video laut MediaInfo abgeschnitten.
        "file_truncated",
    }
)

#: nexcrates Aktionen in Nexviews Knöpfe. Was hier fehlt, wird nicht angeboten -
#: lieber ein Knopf weniger als einer, der nichts tut.
AKTIONEN: dict[str, Aktion] = {
    "remove_and_search": Aktion.entfernen_neu_suchen,
    "remove": Aktion.entfernen,
    "clear": Aktion.entfernen,
    "retry": Aktion.erneut_pruefen,
    "search": Aktion.erneut_pruefen,
    "assign": Aktion.manuell_importieren,
    "confirm-mapping": Aktion.manuell_importieren,
    "finish": Aktion.manuell_importieren,
}

#: Und zurück: Was Nexview auslöst, heißt bei nexcrate so.
#: ⚠️ Gesendet wird immer der Name, den nexcrate in ``actions`` genannt hat -
#: diese Tabelle wählt nur, welcher davon zu einem Knopf gehört.
NEXCRATE_AKTION = {
    Aktion.entfernen_neu_suchen: ("remove_and_search",),
    Aktion.entfernen: ("remove", "clear"),
    Aktion.erneut_pruefen: ("retry", "search"),
    Aktion.manuell_importieren: ("assign", "confirm-mapping", "finish"),
}


@dataclass
class NexDownload:
    """Ein laufender oder klemmender Download, wie die Downloads-Seite ihn liest.

    Die Felder heißen wie beim ARR-Weg, damit `routers/downloads.py` beide
    Betriebsarten gleich zeichnet; `erster` ist dort die aussagekräftigste
    Zeile der Warteschlange und hier schlicht der Eintrag selbst.
    """

    download_id: str
    media_type: str
    arr_id: int | None
    titel: str
    jahr: int | None
    release: str
    folgen: list[list[int]]
    fortschritt: float
    groesse: int
    rest: int
    erster: dict[str, Any]
    problem: dict[str, Any] | None = None
    zeilen: list[int] = field(default_factory=list)
    #: Nein, wenn nexcrate ihn beendet meldet (``failed``): Dann gehoert er
    #: nicht unter „Läuft“ (Rundgang-Befund 9).
    laeuft: bool = True
    folgen_ids: list[int] = field(default_factory=list)


@dataclass
class NexAbfrage:
    """Was nexcrate in diesem Abgleich geantwortet hat."""

    instanz: Any
    erreichbar: bool
    fehler: str = ""
    downloads: list[NexDownload] = field(default_factory=list)


@dataclass
class NexRundgang:
    am: datetime
    abfragen: list[NexAbfrage]

    @property
    def warteschlangen(self) -> dict[tuple[str, str], list[dict]]:
        """Die rohen Warteschlangen je (Art, Stufe) - für ``status_poller``.

        Leer: Im NEX-Betrieb spricht die Grenze keine Stufen mehr, und der
        Takt-Läufer holt sich die Warteschlange selbst über
        ``warteschlange(art, stufe)``. Ein Eintrag hier müsste eine Stufe
        erfinden, die es nicht gibt.
        """
        return {}


def aus_antwort(eintrag: dict[str, Any]) -> NexDownload | None:
    """Ein Eintrag aus ``GET /queue`` bzw. ``GET /problems``."""
    titel = eintrag.get("title") or {}
    nummer = mapping.tmdb_aus(titel.get("ref"))
    serie = eintrag.get("series") or {}
    folgen = [
        [int(paar.get("season")), int(paar.get("episode"))]
        for paar in serie.get("episodes") or []
        if paar.get("season") is not None and paar.get("episode") is not None
    ]
    kennung = eintrag.get("download_id")
    if kennung is None:
        return None
    return NexDownload(
        download_id=str(kennung),
        media_type=mapping.art(str(titel.get("kind") or "")),
        arr_id=nummer,
        titel=str(titel.get("name") or ""),
        jahr=None,
        release=str(eintrag.get("release") or ""),
        folgen=folgen,
        fortschritt=float(eintrag.get("progress") or 0.0),
        groesse=int(eintrag.get("size_bytes") or 0),
        rest=int(eintrag.get("remaining_bytes") or 0),
        erster={
            # Dieselben Schlüssel, die die Seite bei Arr liest.
            "timeleft": _restzeit(eintrag.get("remaining_seconds")),
            "downloadClient": str(eintrag.get("protocol") or ""),
            "protocol": str(eintrag.get("protocol") or ""),
            "status": str(eintrag.get("state") or ""),
            "trackedDownloadState": str(eintrag.get("state") or ""),
        },
        problem=eintrag.get("problem"),
        zeilen=[int(kennung)] if str(kennung).isdigit() else [],
        laeuft=mapping.download_laeuft(eintrag),
    )


def _restzeit(sekunden: Any) -> str | None:
    """Sekunden als ``HH:MM:SS`` - so schreibt Arr es, und so liest es die Seite."""
    if sekunden is None:
        return None
    ganze = max(0, int(sekunden))
    return f"{ganze // 3600:02d}:{ganze % 3600 // 60:02d}:{ganze % 60:02d}"


def erlaubte_aktionen(zeile: DownloadHaenger) -> list[Aktion]:
    """Was sich an diesem Download tun lässt - nach nexcrates Wort.

    ⚠️ **Nexviews eigene Tabelle gilt hier nicht.** Sie rät aus Textmustern,
    was Radarr nicht sagt; nexcrate sagt es. Steht nichts in der Zeile, wird
    auch nichts angeboten - ein Knopf, der ins Leere greift, ist schlimmer als
    keiner.
    """
    erlaubt = ((zeile.aktionen or {}).get("erlaubt")) or []
    gefunden: list[Aktion] = []
    for name in erlaubt:
        aktion = AKTIONEN.get(str(name))
        if aktion is not None and aktion not in gefunden:
            gefunden.append(aktion)
    return gefunden


def darf_automatik(zeile: DownloadHaenger, aktion: Aktion) -> bool:
    """Darf die Automatik das ohne Menschen? Nur, wenn nexcrate es sagt."""
    automatisch = set(((zeile.aktionen or {}).get("automatisch")) or [])
    return any(name in automatisch for name in NEXCRATE_AKTION.get(aktion, ()))


def nexcrate_name(zeile: DownloadHaenger, aktion: Aktion) -> str | None:
    """Wie die Aktion bei nexcrate heißt - aus dem, was es selbst genannt hat."""
    erlaubt = [str(n) for n in ((zeile.aktionen or {}).get("erlaubt")) or []]
    for name in NEXCRATE_AKTION.get(aktion, ()):
        if name in erlaubt:
            return name
    return None


def _fingerabdruck(download: NexDownload) -> str:
    problem = download.problem or {}
    roh = json.dumps(
        [
            download.erster.get("status"),
            problem.get("code"),
            problem.get("needs_owner"),
            sorted(problem.get("actions") or []),
            download.rest,
        ],
        sort_keys=True,
    )
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:64]


def abgleichen(
    db: Session, kennung: str, downloads: list[NexDownload], jetzt: datetime
) -> None:
    """Die gemerkten Hänger an nexcrates frische Antwort angleichen (ohne Commit)."""
    bekannt = {
        zeile.download_id: zeile
        for zeile in db.scalars(
            select(DownloadHaenger).where(DownloadHaenger.kennung == kennung)
        )
    }
    gestoert: set[str] = set()
    for download in downloads:
        problem = download.problem
        # ⚠️ Nur was den Betreiber braucht. Ein Hinweis erscheint auf der
        # Downloads-Seite, wird aber kein Befund und keine Meldung.
        if not problem or not problem.get("needs_owner"):
            continue
        gestoert.add(download.download_id)
        abdruck = _fingerabdruck(download)
        zeile = bekannt.get(download.download_id)
        if zeile is None:
            zeile = DownloadHaenger(
                kennung=kennung,
                download_id=download.download_id,
                erstmals_gesehen=jetzt,
                veraendert_am=jetzt,
                # nexcrate hat den Download schon beobachtet, bevor es ihn als
                # Problem meldet - es gibt hier nichts mehr abzuwarten.
                haengt_seit=jetzt,
            )
            db.add(zeile)
        elif zeile.fingerabdruck != abdruck:
            zeile.veraendert_am = jetzt
            zeile.haengt_seit = zeile.haengt_seit or jetzt
        zeile.zeilen = download.zeilen
        zeile.media_type = download.media_type or "movie"
        zeile.arr_id = download.arr_id
        zeile.folgen_ids = []
        zeile.folgen = download.folgen
        zeile.release = download.release[:500]
        zeile.titel = download.titel[:300]
        zeile.jahr = download.jahr
        zeile.grund = str(problem.get("code") or "")[:40]
        # ⚠️ Der Wortlaut ist nexcrates englischer Satz und bleibt englisch -
        # er ist seine Aussage, nicht unsere. Gezeigt wird die Kennung.
        zeile.wortlaut = [problem.get("message")] if problem.get("message") else []
        zeile.aktionen = {
            "erlaubt": list(problem.get("actions") or []),
            "automatisch": list(problem.get("automatic") or []),
        }
        zeile.zustand = str(download.erster.get("status") or "")[:32]
        zeile.meldestufe = "error"
        zeile.programmstand = str(download.erster.get("status") or "")[:40]
        zeile.protokoll = str(download.erster.get("protocol") or "")[:16]
        zeile.programm = ""
        zeile.groesse = download.groesse
        zeile.rest = download.rest
        zeile.fingerabdruck = abdruck
        zeile.zuletzt_gesehen = jetzt

    # Was nicht mehr klemmt, verschwindet - was geschah, steht im Verlauf.
    for download_id, zeile in bekannt.items():
        if download_id not in gestoert:
            db.delete(zeile)


def fremde_abraeumen(db: Session, kennung: str) -> None:
    """Zeilen anderer Instanzen - sonst zählte der Befund Arr-Hänger mit."""
    db.execute(delete(DownloadHaenger).where(DownloadHaenger.kennung != kennung))


async def auffrischen(db: Session, settings: AppSettings, instanz: ArrInstanz) -> NexRundgang:
    """Warteschlange und Probleme in **einem** Rundgang (Entscheidung 11).

    So hat die Downloads-Seite eine Quelle: Was läuft, und was davon klemmt.
    """
    from .fehler import NexcrateError
    from .weg import client_fuer

    jetzt = utcnow()
    client = client_fuer(settings)
    try:
        roh = await client.queue()
    except NexcrateError as fehler:
        return NexRundgang(am=jetzt, abfragen=[
            NexAbfrage(instanz=instanz, erreichbar=False, fehler=fehler.code or "")
        ])

    downloads = [eintrag for eintrag in (aus_antwort(e) for e in roh) if eintrag is not None]
    try:
        fremde_abraeumen(db, instanz.kennung)
        abgleichen(db, instanz.kennung, downloads, jetzt)
        anfragen_markieren(db, instanz.kennung)
        db.commit()
    except Exception:  # noqa: BLE001 - die Anzeige darf den Rundgang nie umreissen
        logger.exception("Stuck downloads of nexcrate could not be stored")
        db.rollback()
    return NexRundgang(
        am=jetzt, abfragen=[NexAbfrage(instanz=instanz, erreichbar=True, downloads=downloads)]
    )


def gehoert_zu(anfrage: Any, zeile: DownloadHaenger) -> bool:
    """Gehört dieser Download zu dieser Anfrage?

    Vorsichtig wie im ARR-Betrieb: Ein Download ohne Staffel- und
    Folgenangabe zählt bei einer Staffel- oder Paketanfrage nicht mit. Lieber
    kein Hinweis als ein falscher.

    ⚠️ Verglichen wird über die **Medienart der Zeile**, nicht über die der
    Instanz: Im NEX-Betrieb gibt es eine Instanz für beides.
    """
    if anfrage.media_type.value != zeile.media_type:
        return False
    if anfrage.arr_id is None or zeile.arr_id != anfrage.arr_id:
        return False
    if zeile.media_type != "tv" or anfrage.season is None:
        return True
    folgen = [
        (paar[0], paar[1])
        for paar in zeile.folgen or []
        if isinstance(paar, list) and len(paar) == 2
    ]
    in_der_staffel = [nummer for staffel, nummer in folgen if staffel == anfrage.season]
    if not in_der_staffel:
        return False
    if not anfrage.episodes:
        return True
    return bool(set(in_der_staffel) & set(anfrage.episodes))


def anfragen_zu(db: Session, zeile: DownloadHaenger) -> list[Any]:
    """Die offenen Anfragen, zu denen ein Download gehört - für „angefragt von"."""
    from ....models import MediaRequest
    from ..arr.download_haenger import OFFEN

    if zeile.arr_id is None:
        return []
    return [
        anfrage
        for anfrage in db.scalars(
            select(MediaRequest)
            .where(MediaRequest.status.in_(OFFEN), MediaRequest.arr_id == zeile.arr_id)
            .order_by(MediaRequest.requested_at)
        )
        if gehoert_zu(anfrage, zeile)
    ]


def anfragen_markieren(db: Session, kennung: str) -> None:
    """``import_haengt`` an den Anfragen setzen und wieder löschen (ohne Commit)."""
    from sqlalchemy import or_

    from ....models import MediaRequest
    from ..arr.download_haenger import OFFEN

    haengend = [
        zeile
        for zeile in db.scalars(
            select(DownloadHaenger)
            .where(DownloadHaenger.haengt_seit.is_not(None))
            .order_by(DownloadHaenger.haengt_seit)
        )
        if zeile.kennung == kennung
    ]
    for anfrage in db.scalars(
        select(MediaRequest).where(
            or_(MediaRequest.status.in_(OFFEN), MediaRequest.import_haengt.is_not(None))
        )
    ):
        grund = None
        if anfrage.status in OFFEN:
            for zeile in haengend:
                if gehoert_zu(anfrage, zeile):
                    grund = zeile.grund
                    break
        if anfrage.import_haengt != grund:
            anfrage.import_haengt = grund
