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
        added_at=None,
    )


def serien_stand(titel: dict[str, Any], kennung: str) -> SerienStand | None:
    """Eine Serie in einer Fassung, samt Staffeln - soweit sie dabeistehen.

    Die Staffeln stehen nur in der Einzelansicht; in der Liste ist
    ``series.seasons`` immer ``null`` (gemessen). Ohne sie bleiben
    ``seasons`` und ``staffeln`` leer, und wer eine Staffelfrage hat, holt
    die Einzelansicht.
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
    """
    gefunden: dict[int, Folge] = {}
    for eintrag in staffel.get("episodes") or []:
        nummer = eintrag.get("episode")
        if nummer is None:
            continue
        je_fassung = _fassung(eintrag, kennung, schluessel="versions")
        gefunden[int(nummer)] = Folge(
            kennung=int(nummer),
            nummer=int(nummer),
            monitored=bool((je_fassung or {}).get("monitored")),
            has_file=mapping.hat_datei((je_fassung or {}).get("state")),
            datei_id=None,
        )
    return gefunden


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
        #: Marke (``seq``) des Listeneintrags, zu dem sie gelesen wurden.
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

        ⚠️ Gelesen heißt: zu **dieser** Marke. Eine ältere Einzelansicht
        beschreibt einen Stand, den es nicht mehr gibt; eine Serie ohne Datei
        hat nichts zu lesen und gilt als gelesen.
        """
        if not _hat_dateien(eintrag):
            return eintrag, True
        gemerkt = self.staffeln.get(str(eintrag.get("ref")))
        if gemerkt is None or gemerkt[0] != eintrag.get("seq"):
            return eintrag, False
        serie = dict(eintrag.get("series") or {})
        serie["seasons"] = gemerkt[1]
        return {**eintrag, "series": serie}, True

    async def staffeln_lesen(self, client: NexcrateClient) -> int:
        """Die Staffeln jeder Serie mit Datei, ein Aufruf je Serie.

        Nur die Einzelansicht nennt sie; in der Liste steht ``series.seasons``
        immer auf ``null`` (gemessen). Gelesen wird nur, was sich seit dem
        letzten Mal geändert hat: Die Marke je Titel ist dieselbe, auf die
        sich die Liste selbst verlässt. Ohne Marke wird immer gelesen.

        Eine Serie, die nicht antwortet, bleibt ungelesen (``mit_staffeln``);
        der Lauf geht weiter. Ist nexcrate im Ganzen weg, hört er auf, statt
        jede Serie einzeln in den Zeitablauf laufen zu lassen.
        """
        serien = self.alle("series")
        for ref in [r for r in self.staffeln if r not in serien]:
            del self.staffeln[ref]
        offen = [
            (ref, eintrag)
            for ref, eintrag in serien.items()
            if _hat_dateien(eintrag)
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
