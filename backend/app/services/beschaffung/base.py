"""Gemeinsame Grundlage fuer die Beschaffung (heute Radarr und Sonarr).

Nexview beschafft Titel nicht selbst, es gibt sie in Auftrag. Wer den Auftrag
ausfuehrt, ist eine Betriebsart: heute Radarr und Sonarr (``arr/``), spaeter
nexcrate. **Ausserhalb von ``services/beschaffung/`` kennt niemand eine davon.**
Router und Dienste holen sich ueber ``get_beschaffung`` eine ``Beschaffung``
und reden nur mit dieser Schnittstelle; der Waechter
``tests/test_beschaffung_grenze.py`` haelt die Regel.

Hier steht, was beide Wege teilen: die Schnittstelle, die Formen der
Ergebnisse, der Fehler mit seinen drei Koerben und die Liste der Zustaende.

⚠️ **Die Schnittstelle spricht in dieser Fassung noch die Sprache von heute.**
Bestand, Warteschlange und Folgen kommen in den Formen, die Radarr und Sonarr
liefern (``FilmStand``, ``SerienStand``, ``Folge``), und die Stufe ist noch
``"standard"``/``"uhd"``. Die Formen stehen aber hier und nicht mehr in
``arr/``: Wer sie liest, liest die Grenze, nicht Radarr. Der zweite Weg
bildet auf dieselben Formen ab oder erweitert sie; erst dann zeigt sich, was
davon Arr-Eigenheit ist (``arr_id``) und was bleibt.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:  # nur fuer Typangaben - vermeidet Ringschluesse
    import asyncio

    from fastapi import APIRouter
    from sqlalchemy.orm import Session

    from ...models import MediaRequest, StorageEntry, User
    from ...schemas_media import MediaItem
    from ..settings_service import AppSettings


#: Unter welchem Betrieb eine Fassung oder Anfrage entstand.
ARR = "arr"
NEX = "nex"

#: Die Klassen einer Fassung, soweit Nexview sie unterscheidet (grobe Aufloesung).
KLASSE_HD = "hd"
KLASSE_UHD = "uhd"


@dataclass(frozen=True)
class FassungInfo:
    """Eine Fassung, wie die Einstellungen sie kennen - ohne Datenbank."""

    kennung: str
    media_type: str
    name: str
    klasse: str | None
    reihenfolge: int
    quelle: str


#: Die Zustaende eines Titels je Fassung, auf die jeder Weg abbildet (N14).
#: ``unbekannt`` ist kein Fehler: Der Weg weiss es gerade nicht.
ZUSTAENDE: tuple[str, ...] = (
    "fehlt",
    "gesucht",
    "laedt",
    "da",
    "vorerst",
    "verbesserbar",
    "problem",
    "unbekannt",
)


class Korb(enum.StrEnum):
    """Was ein Fehler fuer den naechsten Versuch heisst.

    * ``voruebergehend``: Zeit, Netz, 5xx, 429, eine fremde Antwort (etwa
      HTML von einem Proxy). Noch einmal versuchen lohnt sich.
    * ``abgelehnt``: Die Gegenseite hat verstanden und nein gesagt (4xx mit
      Kennung). Dieselbe Anfrage scheitert wieder; die Kennung wird gezeigt.
    * ``unbekannt``: Weder noch - ins Protokoll mit Kennung.
    """

    voruebergehend = "voruebergehend"
    abgelehnt = "abgelehnt"
    unbekannt = "unbekannt"


class BeschaffungError(Exception):
    """Fehler beim Beschaffen - mit Kennung, Werten und Korb.

    ``ungewiss`` trennt "hat nicht geklappt" von "wir wissen es nicht": Bei
    einer Zeitueberschreitung kann der Auftrag angekommen und ausgefuehrt
    worden sein. Wer schreibt, darf dann nicht "fehlgeschlagen" vermerken.

    ``code`` und ``zahlen`` sind dasselbe, was ``meldungen.meldung`` fuer
    HTTP-Antworten liefert: eine Kennung und die Werte zum Einsetzen. Den Satz
    baut das Frontend; ``message`` ist der deutsche Rueckfall fuer alles, was
    ohne die Oberflaeche liest, und fuer alte Eintraege in
    ``MediaRequest.error_message``.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        ungewiss: bool = False,
        code: str | None = None,
        korb: Korb | None = None,
        **zahlen: object,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.ungewiss = ungewiss
        self.code = code
        self.zahlen = zahlen
        self.korb = korb if korb is not None else self._korb_ableiten()

    def _korb_ableiten(self) -> Korb:
        status = self.status_code
        if self.ungewiss or status == 429 or (status is not None and status >= 500):
            return Korb.voruebergehend
        if status is not None and 400 <= status < 500:
            return Korb.abgelehnt
        # Ohne Antwort der Gegenseite, aber mit Kennung: Nexview selbst hat
        # entschieden, dass es so nicht geht (etwa "keine TVDB-Kennung").
        return Korb.abgelehnt if self.code else Korb.unbekannt

    def als_meldung(self) -> dict[str, object]:
        """Kennung, deutscher Rueckfall und Werte - wie ``meldungen.meldung``."""
        return {"code": self.code, "message": self.message, **self.zahlen}


class NichtsZuLoeschen(BeschaffungError):
    """Die Quelle meldet fuer einen Posten keine Dateien - Loeschen geht nicht.

    Kein Fehler der Gegenseite, sondern eine Auskunft: Wer loeschen wollte,
    bekommt ``409`` und den Satz, nicht ``502``.
    """


# --------------------------------------------------------------------------
# Haengende Downloads: was sich tun laesst


class Aktion(str, enum.Enum):
    """Was sich an einem haengenden Download tun laesst - die Knoepfe."""

    manuell_importieren = "manuell_importieren"
    entfernen_neu_suchen = "entfernen_neu_suchen"
    entfernen = "entfernen"
    erneut_pruefen = "erneut_pruefen"


#: Was die Automatik ueberhaupt darf.
#:
#: ⚠️ **Der manuelle Import gehoert nie dazu.** Radarr prueft beim Befehl
#: ``ManualImport`` die Ablehnungen nicht noch einmal (gelesen in
#: ``ManualImportService``): Wer ein Sample mitschickt, bekommt es als Film in
#: die Bibliothek. Das darf nur ein Mensch entscheiden, der die Datei gesehen hat.
AUTOMATISCH_MOEGLICH = frozenset(
    {Aktion.entfernen_neu_suchen, Aktion.entfernen, Aktion.erneut_pruefen}
)


class DownloadFehler(Exception):
    """Eine Aktion ging nicht - mit Kennung, deutschem Rueckfall und Werten.

    Dieselbe Form wie ``meldungen.meldung``: Das Backend benennt, die
    Oberflaeche uebersetzt (``errors.byCode``).
    """

    def __init__(
        self, text: str, *, code: str, status_code: int = 409, **zahlen: object
    ) -> None:
        super().__init__(text)
        self.text = text
        self.code = code
        self.status_code = status_code
        self.zahlen = zahlen

    def als_meldung(self) -> dict[str, object]:
        return {"code": self.code, "message": self.text, **self.zahlen}


@dataclass(frozen=True)
class Faehigkeiten:
    """Was ein Weg kann. Gefragt wird vorher, nicht beim Aufruf gescheitert."""

    #: Profile, TRaSH, Benennung, Pfade, Webhooks: die Werkzeuge fuer Arr.
    betreiberwerkzeuge: bool
    #: Kann der Weg sagen, warum ein Titel nicht kommt?
    warum: bool
    vorschau: bool
    ereignisstrom: bool
    anime: bool
    papierkorb: bool
    kalender: bool
    #: Fuer welche Medienarten es Wertungen gibt (``"movie"``, ``"tv"``).
    wertungen: tuple[str, ...]


# --------------------------------------------------------------------------
# Ergebnisformen


@dataclass(frozen=True)
class FilmStand:
    """Ein Film, wie ihn die Beschaffung kennt."""

    arr_id: int
    has_file: bool
    monitored: bool
    # Belegter Platz in Bytes, fuer die Speicher-Belegung (services/storage.py).
    size_bytes: int = 0
    # Fuer die Anzeige eines Postens, auch wenn der Film spaeter verschwindet -
    # dann steht hier der letzte bekannte Titel.
    title: str = ""
    # Wo die Datei liegt, samt Dateiname. Nur fuer den Administrator gedacht -
    # ein gewoehnlicher Benutzer hat mit Serverpfaden nichts zu schaffen.
    path: str = ""
    # Seit wann die Datei da liegt. Gebraucht vom Aufraeum-Vorschlag: Ohne
    # dieses Datum stuende ein Film, der heute Nacht fertig wurde, ganz oben
    # in der Liste der Ladenhueter.
    added_at: datetime | None = None


@dataclass(frozen=True)
class Staffelstand:
    """Wie weit **eine** Staffel geladen ist.

    ⚠️ Gebraucht, weil ``has_file`` eine Aussage ueber die **ganze Serie** ist:
    "mindestens eine Folge liegt vor". Solange nur ganze Serien angefragt
    werden konnten, war das dasselbe. Bei Staffelanfragen ist es das nicht -
    gemeldet wurde eine Serie mit drei Dateien in Staffel 3, worauf **fuenf**
    Staffelanfragen gleichzeitig als "bereits geladen" galten und fuenf
    Fertig-Meldungen in derselben Sekunde hinausgingen.
    """

    dateien: int
    folgen: int
    # Laeuft die Ueberwachung? ``True`` als Vorgabe heisst "kein Anlass zur
    # Heilung" - wo die Angabe fehlt, wird nicht herumgestellt.
    monitored: bool = True

    @property
    def vollstaendig(self) -> bool:
        """Alle Folgen dieser Staffel liegen vor.

        Strenger als ``has_file`` und mit Absicht: Eine Staffel ist eine
        abgeschlossene, abzaehlbare Menge - "fertig" laesst sich hier wirklich
        beantworten. Bei einer ganzen Serie waere dieselbe Frage sinnlos, weil
        eine laufende Serie nie fertig ist.
        """
        return self.folgen > 0 and self.dateien >= self.folgen


@dataclass(frozen=True)
class Folge:
    """Eine Folge - das Noetigste fuer Folgen-Pakete.

    ``kennung`` ist die Episoden-Id der Quelle (fuers Einschalten und Suchen),
    ``datei_id`` die Id der Episodendatei (fuers gezielte Loeschen beim
    Abbruch) - ``None``, solange keine Datei liegt.
    """

    kennung: int
    nummer: int
    monitored: bool
    has_file: bool
    datei_id: int | None = None


@dataclass(frozen=True)
class SerienStand:
    """Eine Serie, wie sie die Beschaffung kennt."""

    arr_id: int
    has_file: bool  # mindestens eine Folge der **ganzen Serie** liegt vor
    monitored: bool
    episode_file_count: int
    episode_count: int
    title_key: str  # normalisierter Titel als Rueckfallweg
    # Nur fuer den Titel-Rueckfall: Ohne Jahr trifft "Countdown" (1982) jede
    # andere Serie desselben Namens - samt deren Folgen. Siehe jahre_passen.
    year: int | None = None
    # Belegter Platz der ganzen Serie in Bytes.
    size_bytes: int = 0
    # Belegter Platz **je Staffel**: {Staffelnummer: Bytes}. Die
    # Speicher-Belegung rechnet staffelweise.
    seasons: dict[int, int] = field(default_factory=dict)
    # Ladestand **je Staffel** - aus derselben Statistik wie die Groessen.
    # Ohne diese Aufschluesselung laesst sich eine Staffelanfrage nicht
    # beantworten; siehe ``Staffelstand``.
    staffeln: dict[int, Staffelstand] = field(default_factory=dict)
    # Letzter bekannter Titel, damit ein Posten anzeigbar bleibt, wenn die
    # Serie spaeter verschwindet.
    title: str = ""
    # Der **Ordner** der Serie - kein Dateiname. Eine Staffel ist keine Datei,
    # sondern zwanzig; echte Dateinamen braeuchten eine Abfrage je Serie.
    path: str = ""


@dataclass(frozen=True)
class WarteschlangenEintrag:
    """Ein laufender Download - aufs Noetigste verdichtet.

    ``arr_id`` ist die Kennung des Titels bei der Quelle; Staffel und Folge
    gibt es nur bei Serien. ``size``/``sizeleft`` tragen die Fortschritts-
    Anzeige: geladen ist, was von ``size`` nicht mehr uebrig ist.
    """

    arr_id: int
    season: int | None
    episode: int | None
    size: int
    sizeleft: int


#: Serien-Bestand: nach TVDB-Kennung und nach normalisiertem Titel (der
#: der Titel-Index ist der Rueckfallweg, wenn TMDB noch keine TVDB-Kennung kennt).
SerienBestand = tuple[dict[int, SerienStand], dict[str, SerienStand]]


# --------------------------------------------------------------------------
# Nachschlagen: die Normalform des Lesens (Bauplan Abschnitt 3.1)


@dataclass(frozen=True)
class Nachschlag:
    """Wonach gefragt wird: **ein Titel in einer Fassung**.

    Die Normalform des Lesens. Vorher holte jeder Aufrufer die ganze
    Bibliothek je Stufe und suchte sich seinen Titel heraus - bei Serien ueber
    die TVDB-Kennung mit dem Titel als Rueckfall. Das ist eine Arr-Eigenheit:
    nexcrate ankert auf TMDB und beantwortet einen Stapel Kennungen in einem
    Aufruf (N12).

    ``tvdb_id``, ``titel`` und ``jahr`` stehen nur fuer den ARR-Weg dabei, der
    seinen Rueckfall braucht; der NEX-Weg sieht sie nie an.
    """

    media_type: str
    #: Die Kennung der Fassung - nicht mehr die Stufe (Bauplan Abschnitt 2).
    fassung: str
    tmdb_id: int
    tvdb_id: int | None = None
    titel: str = ""
    jahr: int | None = None


@dataclass(frozen=True)
class Kennt:
    """Wonach der Umstieg fragt: kennst du diesen Titel ueberhaupt?

    Eine andere Frage als ``Nachschlag``: Dort geht es um den **Stand** einer
    Fassung, hier um „fuehrst du ihn, und unter welchen Kennungen". Der
    Unterschied traegt den Umstieg: Nexviews Serien haengen an TVDB, nexcrate
    ankert auf TMDB, und die Uebersetzung steht nur in der Antwort.
    """

    media_type: str
    tmdb_id: int
    tvdb_id: int | None = None
    titel: str = ""
    jahr: int | None = None


@dataclass(frozen=True)
class Kenntnis:
    """Was der Weg ueber einen Titel weiss."""

    bekannt: bool
    #: Die Fassungen, die der Weg fuer ihn fuehrt.
    fassungen: tuple[str, ...] = ()
    #: Was der Weg selbst als Kennung fuehrt - fuer die Uebersetzung.
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    #: Fuehrt der Weg diesen Titel als Anime? ⚠️ **Nur der NEX-Weg beantwortet
    #: das.** Radarr und Sonarr sagen hier immer ``False``; gefragt wird bisher
    #: nur im Umstiegsassistenten, und der fragt nexcrate.
    anime: bool = False


@dataclass(frozen=True)
class Grund:
    """Warum eine Fassung eines Titels noch nicht da ist (N28).

    ⚠️ **Eine Kennung, kein Satz.** ``code`` ist nexcrates Grund, ``werte``
    sind seine Platzhalter; den Satz baut die Oberflaeche. Der englische
    Wortlaut des Wegs steht nirgends in dieser Form - er waere eine zweite
    Wahrheit neben der Uebersetzung.
    """

    fassung: str
    code: str
    werte: dict[str, Any] = field(default_factory=dict)
    #: Untergruende, wo der Weg sie nennt (``version_not_ready`` traegt die
    #: Gruende der Fassung: kein Indexer, kein Profil, kein Download-Programm).
    darunter: tuple[str, ...] = ()


@dataclass(frozen=True)
class Warum:
    """Der Stand eines Titels: sucht der Weg, und was steht je Fassung an?"""

    #: ``True``, wenn der Weg den Titel ueberhaupt kennt.
    bekannt: bool = False
    #: Laeuft die Automatik fuer ihn?
    automatisch: bool = False
    #: Steht ein Suchwunsch offen?
    suchwunsch: bool = False
    zuletzt_gesucht: str | None = None
    naechste_suche: str | None = None
    gruende: tuple[Grund, ...] = ()


@dataclass(frozen=True)
class Sprung:
    """Eine fertige Adresse in die Oberflaeche des Wegs - oder nichts.

    ⚠️ **Leer heisst: kein Sprung.** Ein Weg ohne eigene Oberflaeche (Radarr
    und Sonarr haben ihre eigene, aber Nexview kennt ihre Adressen nicht) und
    eine Installation ohne eingetragene Adresse nach aussen liefern dasselbe:
    nichts. Die Oberflaeche zeigt dann keinen Verweis, statt einen ins Leere.
    """

    titel: str = ""
    fassung: str = ""
    probleme: str = ""
    papierkorb: str = ""
    kalender: str = ""


@dataclass(frozen=True)
class Pruefbefund:
    """Was gegen diesen Weg spricht (Bauplan 7.2).

    ``sperrt`` heisst: So geht es nicht weiter. ``warnt`` heisst: Es geht,
    aber der Betreiber soll es wissen.
    """

    code: str
    #: ``sperrt`` oder ``warnt``.
    stufe: str
    werte: dict[str, Any] = field(default_factory=dict)


SPERRT = "sperrt"
WARNT = "warnt"


@dataclass(frozen=True)
class Nachschlagen:
    """Was beim Nachschlagen herauskam.

    ⚠️ **``gelesen`` ist der Unterschied zwischen „weg" und „nicht gefragt".**
    Eine nicht eingerichtete oder stumme Quelle liefert nichts; daraus „der
    Titel ist verschwunden" zu folgern hiesse, bei einem Ausfall reihenweise
    Anfragen abzubrechen. Hier steht deshalb, welche Fassungen wirklich
    geantwortet haben.
    """

    treffer: dict[Nachschlag, FilmStand | SerienStand]
    gelesen: frozenset[tuple[str, str]]

    def stand(self, wonach: Nachschlag) -> FilmStand | SerienStand | None:
        return self.treffer.get(wonach)

    def hat_geantwortet(self, wonach: Nachschlag) -> bool:
        return (wonach.media_type, wonach.fassung) in self.gelesen


# --------------------------------------------------------------------------
# Titel vergleichen - ohne Anbieter, deshalb hier


def normalize_title(title: str) -> str:
    """Titel auf einen vergleichbaren Kern reduzieren."""
    return "".join(character for character in title.casefold() if character.isalnum())


def jahre_passen(gesucht: int | None, gefunden: int | None) -> bool:
    """Gehoeren die beiden Jahresangaben plausibel zusammen?

    Gebraucht ueberall dort, wo ueber den **Titel** abgeglichen wird - und das
    ist bei Serien der Regelfall, weil TMDB fuer viele Serien keine TVDB-Id
    kennt. Ohne diese Pruefung reicht Namensgleichheit: Gemeldet wurde
    "Countdown" (1982), das eine voellig andere Serie traf und samt deren
    Folgenliste als "bereits geladen" erschien.

    Ein Jahr Abweichung ist erlaubt: Erstausstrahlung und Serienstart nach
    Zaehlweise der jeweiligen Datenbank fallen oft auseinander. Fehlt eine der
    beiden Angaben, wird der Treffer verworfen - lieber einen vorhandenen Titel
    uebersehen als einen falschen behaupten. Ein uebersehener kostet einen
    doppelten Download, ein falscher nimmt einen Titel dauerhaft aus dem
    Angebot, ohne dass jemand den Grund sieht.
    """
    if gesucht is None or gefunden is None:
        return False
    return abs(gesucht - gefunden) <= 1


def treffer_nach_titel(
    nach_titel: dict[str, SerienStand], titel: str, jahr: int | None
) -> SerienStand | None:
    """Serie ueber den Titel finden - aber nur bei passendem Jahr.

    Der einzige erlaubte Weg zum Titel-Index. Frueher griff jede Fundstelle
    direkt darauf zu, und Namensgleichheit genuegte; gemeldet wurde eine Serie
    "Countdown" (1982), die dadurch die Folgen einer voellig anderen Serie
    gleichen Namens erbte. Die Regel steht in ``jahre_passen``.
    """
    eintrag = nach_titel.get(normalize_title(titel))
    if eintrag is None:
        return None
    return eintrag if jahre_passen(jahr, eintrag.year) else None


def jahr_aus(datum: str | None) -> int | None:
    """Jahr aus einem Datum wie "1982-05-03"."""
    vorn = (datum or "")[:4]
    return int(vorn) if vorn.isdigit() else None


# --------------------------------------------------------------------------
# Die Schnittstelle


class Beschaffung(ABC):
    """Ein Weg, Titel zu beschaffen.

    Eine Instanz je Aufruf, ohne eigenen Zustand ausser den Einstellungen;
    Zwischenspeicher liegen im Weg selbst. Was keine Einstellungen braucht
    (Tabellen je Instanz, der Wecker, Start und Ende), sind Klassenmethoden:
    Das Paket fragt sie auch dort, wo gerade keine Einstellungen geladen sind.

    ``stufe`` ist in dieser Fassung noch ``"standard"`` oder ``"uhd"`` (siehe
    Kopf der Datei); ``arr_id`` die Kennung des Titels bei der Quelle.
    """

    #: Kennung der Betriebsart, wie sie in den Einstellungen steht.
    art: ClassVar[str]

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    @abstractmethod
    def faehigkeiten(self) -> Faehigkeiten: ...

    # -- Fassungen ------------------------------------------------------------

    @abstractmethod
    def fassungen(self) -> tuple[FassungInfo, ...]:
        """Die eingerichteten Fassungen, in Anzeigereihenfolge."""

    @abstractmethod
    def fassungen_abgleichen(self, db: Session) -> None:
        """Die Tabelle ``fassungen`` auf den Stand der Quelle bringen (ohne Commit)."""

    @classmethod
    @abstractmethod
    def feste_fassungen(cls) -> tuple[Any, ...]:
        """Fassungen, die der Weg ohne Verbindung kennt (ARR: eine je Instanz)."""

    # -- Bestand --------------------------------------------------------------

    @abstractmethod
    def instanzen(self) -> tuple[Any, ...]:
        """Die Instanzen, die gemessen werden - Kennung, Name, Adresse.

        Im ARR-Betrieb bis zu vier (eine je Stufe und Medienart), im
        NEX-Betrieb genau eine. Sie sind **nicht** dasselbe wie Fassungen: Eine
        Instanz ist etwas, das erreichbar sein kann und eine Version hat; eine
        Fassung ist eine Art, in der ein Titel vorliegt. Bei Arr fallen beide
        zusammen, bei nexcrate nicht.
        """

    @abstractmethod
    def verwaltet(self, media_type: str, stufe: str = "standard") -> bool:
        """Gibt es fuer Art und Stufe etwas, das beschafft?"""

    @abstractmethod
    async def bestand_filme(
        self, stufe: str = "standard", *, fassung: str = ""
    ) -> dict[int, FilmStand]:
        """Alle Filme der Fassung, nach TMDB-Kennung.

        ``fassung`` ist die Kennung und geht vor; ohne sie gilt die Stufe (und
        im NEX-Betrieb die Hauptfassung). Siehe ``status_setzen``.
        """

    @abstractmethod
    async def bestand_serien(
        self, stufe: str = "standard", *, fassung: str = ""
    ) -> SerienBestand:
        """Alle Serien der Fassung, nach TVDB-Kennung und nach Titel."""

    @classmethod
    @abstractmethod
    def bestand_verwerfen(cls) -> None:
        """Zwischengespeicherten Bestand vergessen (nach einem Auftrag)."""

    @abstractmethod
    async def nachschlagen(self, gesucht: list[Nachschlag]) -> Nachschlagen:
        """Den Stand vieler Titel auf einmal - die Normalform des Lesens.

        Der ARR-Weg holt dafuer die Bibliothek je Fassung (wie bisher, 60 s im
        Speicher) und loest jeden Eintrag ueber TVDB oder Titel auf; der
        NEX-Weg fragt ``POST /titles/lookup`` im Stapel zu hundert (N12).
        """

    @abstractmethod
    async def kennt(self, gesucht: list[Kennt]) -> list[Kenntnis]:
        """Fuehrt dieser Weg diese Titel - und unter welchen Kennungen?

        Die Antwort steht in derselben Reihenfolge wie die Frage. Gebraucht
        vom Umstiegsassistenten: Er prueft das **Ergebnis** (kennt der neue
        Weg die Titel, an denen etwas haengt), nicht den Weg dorthin.
        """

    @abstractmethod
    async def pruefen(self) -> list[Pruefbefund]:
        """Taugt dieser Weg? Leer heisst ja (Bauplan 7.2).

        Gefragt in der Einrichtung, im Umstiegsassistenten und als Befund im
        laufenden Betrieb - immer mit derselben Antwort.
        """

    @abstractmethod
    async def status_setzen(
        self,
        media_type: str,
        items: list[MediaItem],
        stufe: str = "standard",
        *,
        fassung: str = "",
        mit_pfad: bool = False,
    ) -> Any:
        """Kacheln mit dem Stand der Fassung versehen (``.items``, ``.warning``).

        ``fassung`` ist die Kennung und geht vor; ohne sie gilt die Stufe (und
        im NEX-Betrieb die Hauptfassung der Medienart). Beides steht hier, weil
        die Grenze bis Scheibe 8 an beiden Enden bedient wird: Der ARR-Weg
        rechnet in Stufen, nexcrate kennt sie nicht.
        """

    @abstractmethod
    async def folgen_verfuegbarkeit(
        self, tvdb_id: int | None, title: str, stufe: str = "standard", jahr: int | None = None
    ) -> dict[int, set[int]]:
        """Welche Folgen je Staffel schon vorliegen."""

    @abstractmethod
    async def serien_eintrag(
        self, tvdb_id: int | None, titel: str, jahr: int | None = None, stufe: str = "standard"
    ) -> SerienStand | None: ...

    @abstractmethod
    async def folgen_stand(self, stufe: str, arr_id: int) -> dict[int, dict[int, Folge]] | None:
        """Folgen einer Serie je Staffel und Nummer; ``None``, wenn nichts eingerichtet ist."""

    @abstractmethod
    async def episodendateien(
        self, stufe: str, arr_id: int, season: int | None = None
    ) -> list[dict[str, Any]] | None: ...

    @abstractmethod
    async def staffel_daten(self, stufe: str, arr_id: int) -> dict[int, datetime] | None: ...

    @abstractmethod
    async def warteschlange(self, media_type: str, stufe: str) -> list[WarteschlangenEintrag]: ...

    @abstractmethod
    def warteschlange_verdichten(
        self, media_type: str, roh: list[dict[str, Any]]
    ) -> list[WarteschlangenEintrag]: ...

    # -- Ziele, Platz, Kalender, Wertungen ------------------------------------

    @abstractmethod
    async def optionen(self, media_type: str, stufe: str = "standard") -> dict[str, Any]:
        """Zielordner und Qualitaetsprofile zur Auswahl."""

    @abstractmethod
    async def datentraeger(self, media_type: str, stufe: str = "standard") -> list[dict[str, Any]]: ...

    @abstractmethod
    async def papierkoerbe(self) -> list[tuple[str, str, str, Any]]: ...

    @abstractmethod
    async def papierkorb_groesse(self, media_type: str, stufe: str, pfad: str) -> tuple[int, bool]: ...

    @abstractmethod
    async def warum(self, gefragt: list[Kennt]) -> list[Warum]:
        """Warum diese Titel noch nicht da sind (N28), in derselben Reihenfolge.

        ⚠️ **Der Grund steht je Fassung, nicht am Titel** (nexbeat-Befund 7):
        ``next_search_reason`` stand auf „nichts gewollt", waehrend eine
        Fassung sehr wohl gesucht wurde. Wer den Titelgrund allein liest,
        erzaehlt dem Anfragenden das Falsche.
        """

    @abstractmethod
    def spruenge(self) -> Sprung:
        """Fertige Adressen in die Oberflaeche des Wegs; leer heisst keine.

        Synchron, weil die Oberflaeche sie bei jeder Konfiguration mitliest -
        eine Abfrage ueber das Netz an dieser Stelle machte jeden Seitenaufbau
        von einer fremden Anwendung abhaengig.
        """

    @abstractmethod
    async def papierkorb(self) -> list[dict[str, Any]]:
        """Was im Papierkorb liegt (N22) - Titel, Fassung, Umfang, Groesse, wann.

        ⚠️ **Nicht dasselbe wie ``papierkoerbe``.** Radarr und Sonarr haben
        einen Papierkorb-**Ordner**, den Nexview durchsucht; nexcrate fuehrt
        eine Liste und holt daraus zurueck. Wer den hat, sagt
        ``faehigkeiten().papierkorb``.
        """

    @abstractmethod
    async def wiederherstellen(self, eintrag_id: int) -> None:
        """Einen Eintrag aus dem Papierkorb zurueckholen."""

    @abstractmethod
    async def kalender(self, media_type: str, von: str, bis: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def wertungen_filme(self, tmdb_ids: list[int]) -> dict[int, Any]: ...

    @abstractmethod
    def nicht_eingerichtet(self, media_type: str, stufe: str) -> str:
        """Der Satz, wenn fuer eine Stufe nichts eingerichtet ist."""

    # -- Auftraege ------------------------------------------------------------

    @abstractmethod
    async def anfragen(self, db: Session, anfrage: MediaRequest) -> int | None:
        """Eine freigegebene Anfrage in Auftrag geben; die Kennung dort."""

    @abstractmethod
    async def abbrechen(self, db: Session, anfrage: MediaRequest) -> str:
        """Eine laufende Anfrage zuruecknehmen, samt eigener Dateien.

        Gibt fuer das Protokoll zurueck, was geschehen ist."""

    @abstractmethod
    async def ueberwachung_heilen(self, db: Session, anfrage: MediaRequest, arr_id: int) -> None:
        """Abgeschaltete Ueberwachung einer laufenden Serien-Anfrage wieder an."""

    @abstractmethod
    async def serie_zuordnen(self, tmdb_id: int, stufe: str, *titel: str) -> Any:
        """Eine Serie ohne TVDB-Kennung zuordnen; ``None``, wenn nichts eingerichtet ist."""

    @abstractmethod
    def serien_wahl_erlaubt(self, zuordnung: Any, tvdb_id: int) -> bool: ...

    # -- Speicherposten -------------------------------------------------------

    @abstractmethod
    async def posten_kennung(self, zeile: StorageEntry) -> int | None: ...

    @abstractmethod
    async def posten_dateien(
        self, zeile: StorageEntry, arr_id: int, paket_folgen: Callable[[], list[int] | None]
    ) -> list[tuple[str, int]]: ...

    @abstractmethod
    async def posten_loeschen(
        self, zeile: StorageEntry, arr_id: int, paket_folgen: Callable[[], list[int] | None]
    ) -> None:
        """Wirft ``NichtsZuLoeschen``, wenn es keine Dateien gibt."""

    @abstractmethod
    async def posten_stilllegen(self, zeile: StorageEntry) -> int | None: ...

    # -- Konto aufloesen ------------------------------------------------------

    @abstractmethod
    async def laufende_aufloesen(
        self, db: Session, laufend: Any, *, behalten: bool, weiter: bool
    ) -> bool: ...

    @abstractmethod
    async def bestellung_zuruecknehmen(self, anfrage: MediaRequest) -> None: ...

    # -- Instanzen: Stand, Gesundheit, Rueckkanal ------------------------------

    @abstractmethod
    async def instanz_messen(self, instanz: Any, *, voll: bool) -> Any: ...

    @abstractmethod
    async def gesundheit_pruefen(self, db: Session) -> None: ...

    @abstractmethod
    async def rueckkanal_pflegen(self, db: Session) -> None: ...

    @abstractmethod
    async def verlassen(self, db: Session) -> list[str]:
        """Diesen Weg aufgeben: aufraeumen, was er anderswo hinterlassen hat.

        Gibt zurueck, was geschehen ist - fuer das Protokoll und fuer den
        Assistenten, der es dem Betreiber zeigt.

        ⚠️ **Der letzte Schreibzugriff auf den alten Weg.** Danach sind seine
        Zugaenge geloescht; was drueben weiterlaeuft, laeuft ohne Nexview zu
        Ende. Ein stummer Dienst haelt das nicht auf: Dann bleibt sein Eintrag
        eben stehen, und der Bericht sagt es.
        """

    # -- Haengende Downloads --------------------------------------------------

    @abstractmethod
    async def downloads_auffrischen(
        self, db: Session, *, frisch_genug: timedelta | None = None
    ) -> Any: ...

    @abstractmethod
    def download_anfragen(self, db: Session, zeile: Any) -> list[MediaRequest]: ...

    @abstractmethod
    async def download_entfernen(
        self,
        db: Session,
        zeile_id: int,
        *,
        neu_suchen: bool,
        wer: User | None,
        automatisch: bool = False,
    ) -> Any: ...

    @abstractmethod
    async def download_erneut_pruefen(
        self, db: Session, zeile_id: int, *, wer: User | None, automatisch: bool = False
    ) -> Any: ...

    @abstractmethod
    async def download_kandidaten(self, db: Session, zeile_id: int) -> list[Any]: ...

    @abstractmethod
    async def download_importieren(
        self, db: Session, zeile_id: int, pfade: list[str], *, trotzdem: bool, wer: User | None
    ) -> Any: ...

    # -- Betrieb (ohne Einstellungen) -------------------------------------------

    @classmethod
    @abstractmethod
    def router(cls) -> list[APIRouter]:
        """Eigene Adressen des Wegs (Einstellungen, Werkzeuge, Rueckruf)."""

    @classmethod
    @abstractmethod
    def beim_start(cls) -> None: ...

    @classmethod
    @abstractmethod
    def hintergrundaufgaben(cls, stop: asyncio.Event) -> list[Coroutine[Any, Any, None]]: ...

    @classmethod
    @abstractmethod
    async def schliessen(cls) -> None: ...

    @classmethod
    @abstractmethod
    def rueckkanal_bald_pflegen(cls) -> None: ...

    @classmethod
    @abstractmethod
    def nach_wiederherstellung(cls) -> None: ...

    @classmethod
    @abstractmethod
    def weckruf(cls) -> asyncio.Event: ...

    @classmethod
    @abstractmethod
    def gesundheit_je_instanz(cls, db: Session) -> dict[str, Any]: ...

    @classmethod
    @abstractmethod
    def haenger_je_instanz(cls, db: Session) -> dict[str, int]: ...

    @classmethod
    @abstractmethod
    def download_aktionen_moeglich(cls, zeile: Any) -> list[Aktion]: ...

    @classmethod
    @abstractmethod
    def download_verlauf_aufraeumen(cls, db: Session) -> int: ...

    @classmethod
    @abstractmethod
    def download_verlauf(cls, zeile: Any, was: str, **kwargs: Any) -> Any: ...

    @classmethod
    @abstractmethod
    def download_gruende(cls) -> dict[str, Any]: ...

    @classmethod
    @abstractmethod
    def download_frisch(cls) -> timedelta: ...
