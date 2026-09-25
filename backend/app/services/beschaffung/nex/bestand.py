"""Was nexcrate führt, in Nexviews Formen.

Zwei Wege hinein, und sie sind nicht dasselbe (nexbeat-Befund 12):

* **Nachschlagen** (``POST /titles/lookup``) kennt einen Titel **sofort**, auch
  wenn er gerade erst entstanden ist. Das ist der Weg für „ist meine Anfrage
  angekommen" und für die Kacheln.
* **Die Änderungsmarke** (``GET /titles?after=``) hinkt bis zu zehn Sekunden
  hinterher, liest dafür aber nur Geändertes. Das ist der Weg für „was ist neu
  in der Bibliothek" - Speicher-Abgleich und Vergleich.

⚠️ **Die Marke gehört zu einer Installation.** Wechselt ``installation_id``
oder steht die Marke über ``latest``, wird ganz gelesen: nexcrate antwortet in
beiden Fällen still leer, und ein Verbraucher mit gemerkter Marke sähe sonst
nie wieder eine Änderung (nexbeat-Befund 11, an dieser nexcrate nachgemessen).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..base import (
    BeschaffungError,
    FilmStand,
    Folge,
    SerienStand,
    Staffelstand,
    normalize_title,
)
from . import mapping

if TYPE_CHECKING:
    from ...settings_service import AppSettings
    from .client import NexcrateClient

logger = logging.getLogger("nexview.nexcrate")

#: So viele Titel holt ein Durchgang der Marke je Aufruf.
SEITE = 500
#: Und so viele Seiten höchstens, damit ein Durchgang nicht ewig läuft.
SEITEN_JE_LAUF = 40
#: Diese Fehler heißen „nexcrate ist weg“, nicht „diese eine Serie hakt“.
AUSFALL = frozenset({"nexcrate_timeout", "nexcrate_unreachable", "nexcrate_unavailable"})


def film_stand(titel: dict[str, Any], kennung: str) -> FilmStand | None:
    """Ein Film in einer Fassung - oder nichts, wenn er sie nicht hat."""
    fassung = _fassung(titel, kennung)
    if fassung is None:
        return None
    return FilmStand(
        # ⚠️ Im NEX-Betrieb ist die Kennung bei der Quelle die TMDB-Nummer:
        # jede Adresse von nexcrate nimmt ``tmdb:<n>``. Damit bleibt
        # ``arr_id`` an Anfrage und Posten eine wahre Aussage - „so heißt der
        # Titel dort" - ohne dass eine Spalte dazukommt.
        arr_id=int(mapping.tmdb_aus(titel.get("ref")) or 0),
        has_file=mapping.hat_datei(fassung.get("state")),
        monitored=bool(fassung.get("monitored")),
        size_bytes=int(fassung.get("size_bytes") or 0),
        title=str(titel.get("name") or ""),
        # nexcrate nennt keinen Dateipfad, und die Grenze kennt keinen
        # (Bauplan 6.4). Der Ordner ist ein Sprung, kein Text.
        path="",
        added_at=_zeitpunkt(fassung.get("imported_at")),
    )


def _zeitpunkt(roh: object) -> datetime | None:
    """``imported_at`` in einen naiven UTC-Zeitpunkt, wie der ARR-Weg ablegt.

    nexcrate nennt es an jeder Fassung und jeder Staffel je Fassung (seit
    ``39dfc05``): seit wann die Datei Platz belegt, bei einer Staffel ihre
    älteste. ``null`` heißt, nexcrate kennt das Alter selbst nicht; eine
    ältere nexcrate nennt das Feld gar nicht. Beides bleibt ``None`` - der
    Aufräum-Vorschlag übergeht solche Posten, statt ihr Alter zu raten.

    Ohne Zeitzone gilt UTC: nexcrate schreibt immer UTC, und ``astimezone``
    auf einem naiven Wert nimmt die Ortszeit des Rechners.
    """
    if not isinstance(roh, str) or not roh:
        return None
    try:
        wann = datetime.fromisoformat(roh.replace("Z", "+00:00"))
    except ValueError:
        return None
    if wann.tzinfo is None:
        return wann
    return wann.astimezone(UTC).replace(tzinfo=None)


def serien_stand(titel: dict[str, Any], kennung: str) -> SerienStand | None:
    """Eine Serie in einer Fassung, samt Staffeln - soweit sie dabeistehen.

    Seit nexcrate ``39dfc05`` stehen sie in Liste, ``lookup`` und
    Einzelansicht in derselben Form (``staffeln_dabei``). Bei einer älteren
    nexcrate nur in der Einzelansicht; in Liste und ``lookup`` ist
    ``series.seasons`` dort ``null`` (gemessen). Ohne sie bleiben ``seasons``
    und ``staffeln`` leer, und wer eine Staffelfrage hat, holt die
    Einzelansicht.
    """
    fassung = _fassung(titel, kennung)
    if fassung is None:
        return None
    zahlen = ((fassung.get("series") or {}).get("counts")) or {}
    staffeln: dict[int, Staffelstand] = {}
    groessen: dict[int, int] = {}
    for staffel in ((titel.get("series") or {}).get("seasons") or []):
        nummer = staffel.get("season")
        if nummer is None:
            continue
        je_fassung = _fassung(staffel, kennung, schluessel="versions")
        if je_fassung is None:
            continue
        stand = je_fassung.get("counts") or {}
        staffeln[int(nummer)] = Staffelstand(
            dateien=int(stand.get("have") or 0),
            # ⚠️ ``expected`` sind alle Folgen der Staffel, ``aired`` die schon
            # gesendeten. „Vollständig" misst gegen die gesendeten - sonst
            # gälte eine laufende Staffel nie als fertig.
            folgen=int(stand.get("aired") or 0),
            monitored=bool(je_fassung.get("monitored")),
            # Je Staffel ihr eigenes Datum, nie das der Serie: Die Serie nennt
            # ihre älteste Datei, und die liegt vielleicht in Staffel 1.
            added_at=_zeitpunkt(je_fassung.get("imported_at")),
            # Auf Staffelebene zaehlt nexcrate jede gesendete Folge, je
            # Fassung nur die ueberwachten.
            gesendet=int(staffel["aired"]) if isinstance(staffel.get("aired"), int) else None,
        )
        groessen[int(nummer)] = int(je_fassung.get("size_bytes") or 0)
    return SerienStand(
        arr_id=int(mapping.tmdb_aus(titel.get("ref")) or 0),
        has_file=int(zahlen.get("have") or 0) > 0,
        monitored=bool(fassung.get("monitored")),
        episode_file_count=int(zahlen.get("have") or 0),
        episode_count=int(zahlen.get("aired") or 0),
        title_key=normalize_title(str(titel.get("name") or "")),
        year=titel.get("year"),
        size_bytes=int(fassung.get("size_bytes") or 0),
        seasons=groessen,
        staffeln=staffeln,
        title=str(titel.get("name") or ""),
        path="",
    )


def stand(titel: dict[str, Any], kennung: str) -> FilmStand | SerienStand | None:
    if str(titel.get("kind")) == "series":
        return serien_stand(titel, kennung)
    return film_stand(titel, kennung)


def folgen(staffel: dict[str, Any], kennung: str) -> dict[int, Folge]:
    """Die Folgen einer Staffel nach Nummer (``GET .../seasons/{n}``).

    ``kennung`` ist die Folgennummer selbst: nexcrate kennt keine eigene
    Folgen-Id, und die Adressen sprechen ohnehin in TMDB-Zählung (N15).

    ``dateien`` kommt aus ``files`` (nexcrate 5427612): dieselbe Datei trägt
    an jeder ihrer Folgen dieselbe ``file_id``, Teil 2 einer Doppelfolge steht
    mit dabei. ⚠️ ``size_bytes`` der Folge zählt Teil 2 nicht mit, und eine
    Fassung auf ``wanted`` kann trotzdem eine Datei haben (gemessen). Fehlt
    das Feld, ist die nexcrate älter: ``None``, nicht ``()``.
    """
    gefunden: dict[int, Folge] = {}
    for eintrag in staffel.get("episodes") or []:
        nummer = eintrag.get("episode")
        if nummer is None:
            continue
        je_fassung = _fassung(eintrag, kennung, schluessel="versions") or {}
        gefunden[int(nummer)] = Folge(
            kennung=int(nummer),
            nummer=int(nummer),
            monitored=bool(je_fassung.get("monitored")),
            has_file=mapping.hat_datei(je_fassung.get("state")),
            datei_id=None,
            dateien=_dateien(je_fassung) if je_fassung else (),
            groesse=int(je_fassung.get("size_bytes") or 0),
        )
    return gefunden


def _dateien(je_fassung: dict[str, Any]) -> tuple[tuple[str, int], ...] | None:
    """``files`` einer Folge als ``(file_id, Größe)``; ``None`` ohne das Feld."""
    roh = je_fassung.get("files")
    if not isinstance(roh, list):
        return None
    return tuple(
        (str(datei["file_id"]), int(datei.get("size_bytes") or 0))
        for datei in roh
        if isinstance(datei, dict) and datei.get("file_id") is not None
    )


def staffeln_dabei(titel: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Die Staffeln, die ein Eintrag aus Liste oder ``lookup`` selbst trägt.

    Seit nexcrate ``39dfc05`` stehen sie dort in derselben Form wie in der
    Einzelansicht: jede Staffel mit jeder Fassung, die Größe zählt jede Datei
    einmal, und ``seq`` bewegt sich auch, wenn sich nur eine Staffel ändert.

    ⚠️ ``None`` heißt „nicht dabei“: Eine ältere nexcrate schreibt ``null``
    oder lässt das Feld weg, dann bleibt nur die Einzelansicht. ``[]`` ist
    dagegen eine Antwort: nexcrate kennt zu der Serie keine Staffel, und ihre
    Einzelansicht sagte genau dasselbe (beide kommen aus ``titles.items``).
    """
    staffeln = (titel.get("series") or {}).get("seasons")
    return staffeln if isinstance(staffeln, list) else None


def _hat_dateien(titel: dict[str, Any]) -> bool:
    """Liegt in irgendeiner Fassung dieser Serie etwas?

    Nicht über ``state``: Eine Serie mit zwei von drei Folgen steht auf
    ``wanted`` und belegt trotzdem Platz.
    """
    for fassung in titel.get("versions") or []:
        zahlen = ((fassung.get("series") or {}).get("counts")) or {}
        if int(fassung.get("size_bytes") or 0) > 0 or int(zahlen.get("have") or 0) > 0:
            return True
    return False


def _fassung(
    traeger: dict[str, Any], kennung: str, schluessel: str = "versions"
) -> dict[str, Any] | None:
    for eintrag in traeger.get(schluessel) or []:
        if str(eintrag.get("version_id")) == kennung:
            return eintrag
    return None


# --------------------------------------------------------------------------
# Der Bestand über die Marke


class Bestand:
    """Der Bestand einer Medienart, gehalten und über die Marke fortgeschrieben.

    Im Speicher, nicht in der Datenbank: Der Speicher-Abgleich und der
    Vergleich lesen ihn, und ein Neustart liest ihn einmal ganz - genau wie
    der ARR-Weg seine Bibliothek alle sechzig Sekunden ganz holt.
    """

    def __init__(self) -> None:
        #: Je ``kind`` die Titel nach ``ref``.
        self.titel: dict[str, dict[str, dict[str, Any]]] = {}
        #: Je ``kind`` die zuletzt gelesene Marke.
        self.marke: dict[str, int] = {}
        #: Zu welcher Installation die Marken gehören.
        self.installation: str = ""
        #: Die Staffeln je Serie aus der Einzelansicht, nach ``ref``, samt der
        #: Marke (``seq``) des Listeneintrags, zu dem sie gelesen wurden. Nur
        #: für Einträge ohne eigene Staffeln (ältere nexcrate).
        self.staffeln: dict[str, tuple[Any, list[dict[str, Any]]]] = {}

    def verwerfen(self) -> None:
        self.titel.clear()
        self.marke.clear()
        self.installation = ""
        self.staffeln.clear()

    def alle(self, kind: str) -> dict[str, dict[str, Any]]:
        return self.titel.get(kind, {})

    def mit_staffeln(self, eintrag: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Ein Serieneintrag der Liste samt Staffeln - und ob sie gelesen sind.

        Trägt der Eintrag seine Staffeln selbst (``staffeln_dabei``), gelten
        sie; sie gehören zu genau dieser Marke. Sonst kommen sie aus der
        gemerkten Einzelansicht.

        ⚠️ Gelesen heißt: zu **dieser** Marke. Eine ältere Einzelansicht
        beschreibt einen Stand, den es nicht mehr gibt; eine Serie ohne Datei
        hat nichts zu lesen und gilt als gelesen.
        """
        if not _hat_dateien(eintrag) or staffeln_dabei(eintrag) is not None:
            return eintrag, True
        gemerkt = self.staffeln.get(str(eintrag.get("ref")))
        if gemerkt is None or gemerkt[0] != eintrag.get("seq"):
            return eintrag, False
        serie = dict(eintrag.get("series") or {})
        serie["seasons"] = gemerkt[1]
        return {**eintrag, "series": serie}, True

    async def staffeln_lesen(self, client: NexcrateClient) -> int:
        """Die Staffeln jeder Serie mit Datei, die die Liste nicht selbst nennt.

        Seit nexcrate ``39dfc05`` trägt jeder Serieneintrag der Liste seine
        Staffeln (``staffeln_dabei``); dann kostet keine Serie einen Aufruf.
        Bei einer älteren nexcrate steht ``series.seasons`` dort auf ``null``
        (gemessen), und nur die Einzelansicht nennt sie, ein Aufruf je Serie.
        Gelesen wird nur, was sich seit dem letzten Mal geändert hat: Die
        Marke je Titel ist dieselbe, auf die sich die Liste selbst verlässt.
        Ohne Marke wird immer gelesen.

        Eine Serie, die nicht antwortet, bleibt ungelesen (``mit_staffeln``);
        der Lauf geht weiter. Ist nexcrate im Ganzen weg, hört er auf, statt
        jede Serie einzeln in den Zeitablauf laufen zu lassen.
        """
        serien = self.alle("series")
        # Gemerkt bleibt nur, was noch gebraucht wird: Nennt die Liste die
        # Staffeln selbst, ist die gemerkte Einzelansicht überholt.
        for ref in [
            r for r in self.staffeln if r not in serien or staffeln_dabei(serien[r]) is not None
        ]:
            del self.staffeln[ref]
        offen = [
            (ref, eintrag)
            for ref, eintrag in serien.items()
            if _hat_dateien(eintrag)
            and staffeln_dabei(eintrag) is None
            and (
                eintrag.get("seq") is None
                or self.staffeln.get(ref, (None,))[0] != eintrag.get("seq")
            )
        ]
        gelesen = gescheitert = 0
        for nummer, (ref, eintrag) in enumerate(offen):
            try:
                titel = await client.title("series", ref)
            except BeschaffungError as fehler:
                gescheitert += 1
                if fehler.code in AUSFALL:
                    logger.warning(
                        "nexcrate stopped answering while reading seasons (%s); "
                        "%d series keep their entries",
                        fehler.code,
                        len(offen) - nummer,
                    )
                    return gelesen
                continue
            if titel is None:
                gescheitert += 1
                continue
            staffeln = (titel.get("series") or {}).get("seasons") or []
            self.staffeln[ref] = (eintrag.get("seq"), list(staffeln))
            gelesen += 1
        if gescheitert:
            logger.warning(
                "nexcrate gave no seasons for %d series; their entries are kept", gescheitert
            )
        return gelesen

    async def staffeln_zu(
        self, client: NexcrateClient, gefragt: dict[str, dict[str, Any]], installation: str
    ) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
        """Die Staffeln zu Serien aus ``lookup`` - aus ``lookup`` selbst oder über den Merker.

        ``gefragt`` sind Titel nach ``ref``, wie ``lookup`` sie nannte. Zurück
        kommen die Staffeln je ``ref`` und die ``ref``, zu denen es keine gab.

        Seit nexcrate ``39dfc05`` nennt ``lookup`` die Staffeln selbst
        (``staffeln_dabei``): Sie gelten, ohne Liste und ohne Einzelansicht.
        Nur ein Titel ohne sie (ältere nexcrate) geht den Weg darunter.

        Gemerktes gilt nur, wenn die Liste denselben Stand zeigt wie ``lookup``
        (gleiche Fassungen) und die Marke passt: Die Liste hinkt bis zu zehn
        Sekunden hinterher, und eine Einzelansicht zu einer alten Marke
        beschreibt einen Stand, den es nicht mehr gibt. Was so gelesen wird,
        wird unter der Marke gemerkt; der Speicher-Abgleich liest es dann nicht
        noch einmal (``staffeln_lesen``) und umgekehrt.
        """
        staffeln: dict[str, list[dict[str, Any]]] = {}
        ungelesen: set[str] = set()
        # Eine Serie ohne Datei hat nichts zu lesen: Keine Staffeln sind dort
        # die Wahrheit, und sie kostet keinen Aufruf.
        gefragt = {ref: titel for ref, titel in gefragt.items() if _hat_dateien(titel)}
        for ref in list(gefragt):
            dabei = staffeln_dabei(gefragt[ref])
            if dabei is not None:
                staffeln[ref] = list(dabei)
                del gefragt[ref]
        if not gefragt:
            return staffeln, ungelesen
        try:
            await self.auffrischen(client, "series", installation)
            liste = self.alle("series")
        except BeschaffungError:
            # Ohne frische Liste keine passende Marke: frisch lesen, nichts merken.
            liste = {}
        ausfall = False
        for ref, titel in gefragt.items():
            eintrag = liste.get(ref)
            passt = (
                eintrag is not None
                and eintrag.get("seq") is not None
                and eintrag.get("versions") == titel.get("versions")
            )
            gemerkt = self.staffeln.get(ref)
            if passt and gemerkt is not None and gemerkt[0] == eintrag.get("seq"):
                staffeln[ref] = gemerkt[1]
                continue
            if ausfall:
                ungelesen.add(ref)
                continue
            try:
                einzeln = await client.title("series", ref)
            except BeschaffungError as fehler:
                ungelesen.add(ref)
                ausfall = fehler.code in AUSFALL
                continue
            if einzeln is None:
                # ``lookup`` kannte ihn eben noch: kein "weg", nur ungelesen.
                ungelesen.add(ref)
                continue
            staffeln[ref] = list((einzeln.get("series") or {}).get("seasons") or [])
            if passt:
                self.staffeln[ref] = (eintrag.get("seq"), staffeln[ref])
        if ungelesen:
            logger.warning(
                "nexcrate gave no seasons for %d series; their requests stay as they are",
                len(ungelesen),
            )
        return staffeln, ungelesen

    async def auffrischen(self, client: NexcrateClient, kind: str, installation: str) -> int:
        """Nur Geändertes holen - oder ganz, wenn die Marke nicht mehr gilt.

        Gibt zurück, wie viele Titel sich geändert haben.
        """
        if installation and installation != self.installation:
            if self.installation:
                logger.info("nexcrate is a different installation now; reading the library in full")
            self.verwerfen()
            self.installation = installation
        after = self.marke.get(kind, 0)
        geaendert = 0
        for _ in range(SEITEN_JE_LAUF):
            antwort = await client.titles(after=after, kind=kind, limit=SEITE)
            letzte = int(antwort.get("latest") or 0)
            if after > letzte:
                # Still leer statt 410 (nexbeat-Befund 11): Die Marke gehört
                # einer nexcrate, die es so nicht mehr gibt.
                logger.info("The stored marker is beyond nexcrate's latest; reading in full")
                self.titel.pop(kind, None)
                if kind == "series":
                    # Die Marken fangen dann von vorn an; eine gemerkte
                    # Einzelansicht könnte zufällig dieselbe Zahl tragen.
                    self.staffeln.clear()
                after = 0
                continue
            wohin = self.titel.setdefault(kind, {})
            for eintrag in antwort.get("items") or []:
                wohin[str(eintrag.get("ref"))] = eintrag
                geaendert += 1
            for weg in antwort.get("removed") or []:
                wohin.pop(str(weg.get("ref")), None)
                geaendert += 1
            after = int(antwort.get("next_after") or after)
            if not antwort.get("more"):
                break
        self.marke[kind] = after
        return geaendert


#: Der Bestand dieser Installation. Eine Instanz, weil es eine nexcrate gibt.
_bestand = Bestand()


def gehalten() -> Bestand:
    return _bestand


def verwerfen() -> None:
    _bestand.verwerfen()


async def auffrischen(settings: AppSettings, kind: str) -> int:
    from . import system
    from .weg import client_fuer

    return await _bestand.auffrischen(client_fuer(settings), kind, system.installation_id())


async def staffeln_lesen(settings: AppSettings) -> int:
    from .weg import client_fuer

    return await _bestand.staffeln_lesen(client_fuer(settings))
