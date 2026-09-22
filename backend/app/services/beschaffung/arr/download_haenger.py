"""Haengende Downloads: abfragen, zusammenfassen, merken, als haengend erkennen.

Der Rundgang ruft ``auffrischen`` jede Runde, die Seite Downloads beim
Oeffnen. Beides laeuft durch dieselbe Sperre: Zwei Abgleiche gleichzeitig
legten dieselbe Zeile zweimal an.

⚠️ **Haengend ist eine Frage der Zeit, nicht des Zustands.** Radarr meldet
``importPending`` mit Warnung auch dann, wenn eine Datei nur noch nicht
fertig geschrieben ist, und ein Torrent ohne Gegenstelle findet oft nach ein
paar Minuten doch eine. Als haengend gilt ein Download deshalb erst, wenn er
``MINDESTALTER`` lang gestoert ist und sich seit ``RUHE`` nicht bewegt hat.
Die Werte sind vorlaeufig; an einem echten Betrieb ist noch keiner gemessen.

⚠️ **Eine stumme Instanz raeumt nichts ab.** Ob ihre Downloads noch haengen,
weiss in dem Moment niemand. Die Zeilen bleiben stehen, bis sie wieder
antwortet - dasselbe Muster wie in ``instanz_gesundheit``.

⚠️ **Fuer die Anfragen zaehlt nur, was wirklich haengt.** ``import_haengt``
an einer Anfrage wird erst gesetzt, wenn der Download als haengend gilt. Ein
Besteller soll nicht sehen, was sich in zwei Minuten von selbst erledigt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, true
from sqlalchemy.orm import Session

from ....models import DownloadHaenger, DownloadVerlauf, MediaRequest, RequestStatus
from ...settings_service import AppSettings, ArrInstanz
from . import download_gruende
from .client import ArrClient, ArrError

logger = logging.getLogger("nexview.downloads")

#: So lange muss ein Import gestoert sein, bevor er als haengend gilt.
MINDESTALTER = timedelta(minutes=10)
#: Beim Download-Programm laenger: Ein Torrent ohne Gegenstelle findet oft
#: nach ein paar Minuten doch noch eine.
PROGRAMM_MINDESTALTER = timedelta(minutes=15)
#: Und so lange darf sich nichts bewegt haben.
RUHE = timedelta(minutes=5)

#: So alt darf ein Abgleich sein, den die Seite wiederverwendet. Wer zweimal
#: neu laedt, fragt Radarr nicht zweimal.
FRISCH = timedelta(seconds=15)

#: So lange bleibt der Verlauf stehen.
VERLAUF_TAGE = 90

#: Anfragen, zu denen ein Download haengen kann.
OFFEN = (RequestStatus.approved, RequestStatus.searching)


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _zahl(wert: Any) -> int | None:
    """Eine Kennung aus der Antwort. ``0`` heisst bei Radarr/Sonarr "unbekannt"."""
    if isinstance(wert, bool) or not isinstance(wert, int) or wert <= 0:
        return None
    return wert


def _bytes(wert: Any) -> int:
    return int(wert) if isinstance(wert, (int, float)) and wert > 0 else 0


@dataclass
class Download:
    """Alle Zeilen der Warteschlange, die zu **einem** Download gehoeren."""

    instanz: ArrInstanz
    download_id: str
    saetze: list[dict]
    einordnung: download_gruende.Einordnung | None = None

    @property
    def erster(self) -> dict:
        """Die Zeile, die am meisten sagt: eine gestoerte, wenn es eine gibt."""
        for satz in self.saetze:
            if download_gruende.ist_gestoert(satz):
                return satz
        return self.saetze[0]

    def _medium(self) -> dict:
        schluessel = "movie" if self.instanz.media_type == "movie" else "series"
        for satz in self.saetze:
            if isinstance(satz.get(schluessel), dict):
                return satz[schluessel]
        return {}

    @property
    def zeilen(self) -> list[int]:
        return sorted({n for satz in self.saetze if (n := _zahl(satz.get("id"))) is not None})

    @property
    def arr_id(self) -> int | None:
        feld = "movieId" if self.instanz.media_type == "movie" else "seriesId"
        for satz in self.saetze:
            if (nummer := _zahl(satz.get(feld))) is not None:
                return nummer
        return _zahl(self._medium().get("id"))

    @property
    def folgen_ids(self) -> list[int]:
        return sorted(
            {n for satz in self.saetze if (n := _zahl(satz.get("episodeId"))) is not None}
        )

    @property
    def folgen(self) -> list[list[int]]:
        """``[[Staffel, Folge], ...]`` - nur, was beides traegt."""
        paare: set[tuple[int, int]] = set()
        for satz in self.saetze:
            folge = satz.get("episode") if isinstance(satz.get("episode"), dict) else {}
            staffel = folge.get("seasonNumber", satz.get("seasonNumber"))
            nummer = folge.get("episodeNumber")
            if isinstance(staffel, int) and isinstance(nummer, int):
                paare.add((staffel, nummer))
        return [list(paar) for paar in sorted(paare)]

    @property
    def titel(self) -> str:
        return str(self._medium().get("title") or "")

    @property
    def jahr(self) -> int | None:
        return _zahl(self._medium().get("year"))

    @property
    def release(self) -> str:
        return str(self.erster.get("title") or "")

    @property
    def groesse(self) -> int:
        # Sonarr traegt die Zahlen des ganzen Downloads an jeder Folgen-Zeile;
        # zusammengezaehlt waere ein Staffelpaket zwanzigmal so gross.
        return max((_bytes(satz.get("size")) for satz in self.saetze), default=0)

    @property
    def rest(self) -> int:
        return max((_bytes(satz.get("sizeleft")) for satz in self.saetze), default=0)

    @property
    def fortschritt(self) -> int | None:
        if self.groesse <= 0:
            return None
        return max(0, min(100, round((self.groesse - self.rest) * 100 / self.groesse)))


def zusammenfassen(instanz: ArrInstanz, saetze: list[dict]) -> list[Download]:
    """Zeilen der Warteschlange zu Downloads buendeln - ueber ``downloadId``.

    Eine Zeile ohne ``downloadId`` (etwas, das Radarr noch zurueckhaelt) bleibt
    fuer sich, unter ihrer eigenen Nummer.
    """
    gruppen: dict[str, list[dict]] = {}
    for satz in saetze:
        if not isinstance(satz, dict):
            continue
        schluessel = str(satz.get("downloadId") or "").strip()
        if not schluessel:
            schluessel = f"zeile-{satz.get('id')}"
        gruppen.setdefault(schluessel[:200], []).append(satz)
    return [Download(instanz, schluessel, zeilen) for schluessel, zeilen in gruppen.items()]


def _fingerabdruck(download: Download, einordnung: download_gruende.Einordnung) -> str:
    erster = download.erster
    roh = json.dumps(
        [
            str(erster.get("trackedDownloadState") or ""),
            str(erster.get("trackedDownloadStatus") or ""),
            str(erster.get("status") or ""),
            einordnung.grund.kennung,
            list(einordnung.wortlaut),
            download.rest,
        ]
    )
    return hashlib.sha256(roh.encode()).hexdigest()


def ist_reif(zeile: DownloadHaenger, grund: download_gruende.Grund, jetzt: datetime) -> bool:
    """Gilt der Download als haengend? Lange genug gestoert und lange genug still."""
    mindestens = PROGRAMM_MINDESTALTER if grund.vom_programm else MINDESTALTER
    return jetzt - zeile.erstmals_gesehen >= mindestens and jetzt - zeile.veraendert_am >= RUHE


def verlauf(
    zeile: DownloadHaenger,
    was: str,
    *,
    automatisch: bool,
    user_id: int | None = None,
    ergebnis: str = "",
) -> DownloadVerlauf:
    """Ein Eintrag im Verlauf, mit allem, was er ohne die Zeile noch sagen muss."""
    return DownloadVerlauf(
        kennung=zeile.kennung,
        download_id=zeile.download_id,
        media_type=zeile.media_type,
        arr_id=zeile.arr_id,
        folgen_ids=zeile.folgen_ids,
        titel=zeile.titel,
        release=zeile.release,
        grund=zeile.grund,
        was=was,
        automatisch=automatisch,
        user_id=user_id,
        ergebnis=ergebnis,
        am=_jetzt(),
    )


def _uebernehmen(
    zeile: DownloadHaenger,
    download: Download,
    einordnung: download_gruende.Einordnung,
    abdruck: str,
    jetzt: datetime,
) -> None:
    erster = download.erster
    zeile.zeilen = download.zeilen
    zeile.arr_id = download.arr_id
    zeile.folgen_ids = download.folgen_ids or None
    zeile.folgen = download.folgen or None
    zeile.release = download.release[:500]
    zeile.titel = download.titel[:300]
    zeile.jahr = download.jahr
    zeile.grund = einordnung.grund.kennung
    zeile.wortlaut = [text[:1000] for text in einordnung.wortlaut[:20]]
    zeile.zustand = str(erster.get("trackedDownloadState") or "")[:32]
    zeile.meldestufe = str(erster.get("trackedDownloadStatus") or "")[:16]
    zeile.programmstand = str(erster.get("status") or "")[:40]
    zeile.protokoll = str(erster.get("protocol") or "")[:16]
    zeile.programm = str(erster.get("downloadClient") or "")[:120]
    zeile.groesse = download.groesse
    zeile.rest = download.rest
    zeile.fingerabdruck = abdruck
    zeile.zuletzt_gesehen = jetzt


def abgleichen(
    db: Session, instanz: ArrInstanz, downloads: list[Download], jetzt: datetime
) -> None:
    """Die gemerkten Downloads einer Instanz an ihre frische Warteschlange angleichen.

    Kein ``commit`` - das macht der Aufrufer.
    """
    bekannt = {
        zeile.download_id: zeile
        for zeile in db.scalars(
            select(DownloadHaenger).where(DownloadHaenger.kennung == instanz.kennung)
        )
    }
    gestoert: set[str] = set()
    for download in downloads:
        einordnung = download_gruende.einordnen(download.saetze)
        download.einordnung = einordnung
        if einordnung is None:
            continue
        gestoert.add(download.download_id)
        abdruck = _fingerabdruck(download, einordnung)
        zeile = bekannt.get(download.download_id)
        if zeile is None:
            zeile = DownloadHaenger(
                kennung=instanz.kennung,
                download_id=download.download_id,
                media_type=instanz.media_type,
                grund=einordnung.grund.kennung,
                erstmals_gesehen=jetzt,
                veraendert_am=jetzt,
            )
            db.add(zeile)
        else:
            if zeile.fingerabdruck != abdruck:
                zeile.veraendert_am = jetzt
            # ⚠️ Wer weiterlaedt, haengt nicht. Faellt der Rest, faengt das
            # Warten von vorn an - auch bei einem Download, der schon als
            # haengend galt.
            if download.rest < (zeile.rest or 0):
                zeile.erstmals_gesehen = jetzt
                zeile.haengt_seit = None
        _uebernehmen(zeile, download, einordnung, abdruck, jetzt)
        if zeile.haengt_seit is None and ist_reif(zeile, einordnung.grund, jetzt):
            zeile.haengt_seit = jetzt
            db.add(verlauf(zeile, "erkannt", automatisch=True))
            logger.warning(
                "Download stuck in %s: %r (%s)",
                instanz.name,
                zeile.titel or zeile.release,
                zeile.grund,
            )

    for download_id, zeile in bekannt.items():
        if download_id not in gestoert:
            db.delete(zeile)


@dataclass
class Abfrage:
    """Was eine Instanz in diesem Abgleich geantwortet hat."""

    instanz: ArrInstanz
    erreichbar: bool
    #: Fehler-Kennung, wenn sie nicht geantwortet hat.
    fehler: str = ""
    downloads: list[Download] = field(default_factory=list)


@dataclass
class Rundgang:
    am: datetime
    abfragen: list[Abfrage]

    @property
    def warteschlangen(self) -> dict[tuple[str, str], list[dict]]:
        """Die rohen Warteschlangen je (Art, Stufe) - fuer ``status_poller.check_once``."""
        return {
            (abfrage.instanz.media_type, abfrage.instanz.tier): [
                satz for download in abfrage.downloads for satz in download.saetze
            ]
            for abfrage in self.abfragen
            if abfrage.erreichbar
        }


_sperre = asyncio.Lock()
_letzter: Rundgang | None = None


def vergessen() -> None:
    """Den gemerkten Abgleich verwerfen - nach einer Aktion ist er veraltet."""
    global _letzter
    _letzter = None


def _dieselben_instanzen(rundgang: Rundgang, settings: AppSettings) -> bool:
    return [(a.instanz.kennung, a.instanz.url) for a in rundgang.abfragen] == [
        (i.kennung, i.url) for i in settings.arr_instanzen()
    ]


async def auffrischen(
    db: Session, settings: AppSettings, *, frisch_genug: timedelta | None = None
) -> Rundgang:
    """Alle Instanzen abfragen, abgleichen, die Anfragen markieren.

    ``frisch_genug`` fuer die Seite: Ist der letzte Abgleich juenger, gilt er.
    Der Rundgang fragt immer neu.
    """
    global _letzter
    async with _sperre:
        jetzt = _jetzt()
        if (
            frisch_genug is not None
            and _letzter is not None
            and jetzt - _letzter.am <= frisch_genug
            and _dieselben_instanzen(_letzter, settings)
        ):
            return _letzter

        abfragen = [
            await _instanz_auffrischen(db, instanz, jetzt)
            for instanz in settings.arr_instanzen()
        ]
        try:
            _fremde_abraeumen(db, settings)
            anfragen_markieren(db, settings)
            db.commit()
        except Exception:  # noqa: BLE001 - die Anzeige darf den Rundgang nicht umreissen
            logger.exception("Stuck downloads could not be matched to requests")
            db.rollback()
        _letzter = Rundgang(am=jetzt, abfragen=abfragen)
        return _letzter


async def _instanz_auffrischen(db: Session, instanz: ArrInstanz, jetzt: datetime) -> Abfrage:
    client = ArrClient(instanz.url, instanz.api_key, instanz.name)
    try:
        saetze = await client.warteschlange_voll(instanz.media_type == "movie")
    except ArrError as fehler:
        return Abfrage(instanz, erreichbar=False, fehler=fehler.code or "arr_unreachable")
    except Exception:  # noqa: BLE001 - eine Instanz darf die anderen nicht mitnehmen
        logger.exception("Queue of %s could not be read", instanz.name)
        return Abfrage(instanz, erreichbar=False, fehler="internal_error")

    downloads = zusammenfassen(instanz, saetze)
    try:
        abgleichen(db, instanz, downloads, jetzt)
        db.commit()
    except Exception:  # noqa: BLE001 - siehe oben
        logger.exception("Stuck downloads of %s could not be stored", instanz.name)
        db.rollback()
    return Abfrage(instanz, erreichbar=True, downloads=downloads)


def _fremde_abraeumen(db: Session, settings: AppSettings) -> None:
    """Zeilen von Instanzen, die es nicht mehr gibt - sonst zaehlte der Befund sie ewig."""
    kennungen = [instanz.kennung for instanz in settings.arr_instanzen()]
    bedingung = DownloadHaenger.kennung.not_in(kennungen) if kennungen else true()
    db.execute(delete(DownloadHaenger).where(bedingung))


def gehoert_zu(anfrage: MediaRequest, instanz: ArrInstanz, zeile: DownloadHaenger) -> bool:
    """Gehoert dieser Download zu dieser Anfrage?

    Vorsichtig wie ``abgleich_kern.laedt_fortschritt``: Ein Download ohne
    Staffel- und Folgenangabe zaehlt bei einer Staffel- oder Paketanfrage nicht
    mit. Lieber kein Hinweis als ein falscher.
    """
    if anfrage.media_type.value != instanz.media_type or anfrage.tier != instanz.tier:
        return False
    if anfrage.arr_id is None or zeile.arr_id != anfrage.arr_id:
        return False
    if instanz.media_type != "tv" or anfrage.season is None:
        return True
    folgen = [
        (paar[0], paar[1])
        for paar in zeile.folgen or []
        if isinstance(paar, list) and len(paar) == 2
    ]
    in_der_staffel = [nummer for staffel, nummer in folgen if staffel == anfrage.season]
    if not in_der_staffel:
        return False
    if anfrage.episodes:
        return any(nummer in anfrage.episodes for nummer in in_der_staffel)
    return True


def anfragen_markieren(db: Session, settings: AppSettings) -> None:
    """``import_haengt`` an den Anfragen setzen und wieder loeschen. Kein ``commit``."""
    instanzen = {instanz.kennung: instanz for instanz in settings.arr_instanzen()}
    haengend = [
        zeile
        for zeile in db.scalars(
            select(DownloadHaenger)
            .where(DownloadHaenger.haengt_seit.is_not(None))
            .order_by(DownloadHaenger.haengt_seit)
        )
        if zeile.kennung in instanzen
    ]
    for anfrage in db.scalars(
        select(MediaRequest).where(
            or_(MediaRequest.status.in_(OFFEN), MediaRequest.import_haengt.is_not(None))
        )
    ):
        grund = None
        if anfrage.status in OFFEN:
            for zeile in haengend:
                if gehoert_zu(anfrage, instanzen[zeile.kennung], zeile):
                    grund = zeile.grund
                    break
        if anfrage.import_haengt != grund:
            anfrage.import_haengt = grund


def anfragen_zu(
    db: Session, settings: AppSettings, zeile: DownloadHaenger
) -> list[MediaRequest]:
    """Die offenen Anfragen, zu denen ein Download gehoert - fuer "angefragt von"."""
    instanz = next(
        (i for i in settings.arr_instanzen() if i.kennung == zeile.kennung), None
    )
    if instanz is None or zeile.arr_id is None:
        return []
    return [
        anfrage
        for anfrage in db.scalars(
            select(MediaRequest)
            .where(MediaRequest.status.in_(OFFEN), MediaRequest.arr_id == zeile.arr_id)
            .order_by(MediaRequest.requested_at)
        )
        if gehoert_zu(anfrage, instanz, zeile)
    ]


def zaehlen(db: Session) -> dict[str, int]:
    """Haengende Downloads je Instanz - fuer Befund und Dienste-Reiter, ohne Netz."""
    return {
        kennung: anzahl
        for kennung, anzahl in db.execute(
            select(DownloadHaenger.kennung, func.count(DownloadHaenger.id))
            .where(DownloadHaenger.haengt_seit.is_not(None))
            .group_by(DownloadHaenger.kennung)
        )
    }


def verlauf_aufraeumen(db: Session, jetzt: datetime | None = None) -> int:
    """Verlauf, der aelter ist als ``VERLAUF_TAGE``, wegraeumen."""
    grenze = (jetzt or _jetzt()) - timedelta(days=VERLAUF_TAGE)
    ergebnis = db.execute(delete(DownloadVerlauf).where(DownloadVerlauf.am < grenze))
    db.commit()
    return ergebnis.rowcount or 0
