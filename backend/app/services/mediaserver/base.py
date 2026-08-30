"""Gemeinsame Grundlage fuer Media-Server (Plex, spaeter Jellyfin und Emby).

Nexview braucht von einem Media-Server drei Dinge, und nur diese drei:

1. **Wer meldet sich an?** Der Server buergt fuer die Identitaet, damit sich
   niemand ein zweites Passwort merken muss.
2. **Darf diese Person ueberhaupt?** Nur wer Zugriff auf die Bibliothek des
   Administrators hat, bekommt ein Konto.
3. Spaeter: **was liegt schon da** und **was wurde schon gesehen**.

Die Punkte 1 und 2 gehoeren zu Meilenstein 1, Punkt 3 ist vorbereitet
(``library_index`` / ``watched_since``), aber noch nicht gebaut.

**Die wichtigste Regel dieses Pakets:** ausserhalb von ``services/mediaserver``
darf niemand einen bestimmten Anbieter kennen. Router und Dienste holen sich
ueber ``get_media_server`` ein ``MediaServer`` und sprechen nur mit dieser
Schnittstelle. Genau deshalb ist ein weiterer Anbieter spaeter eine neue Datei
und ein Eintrag in der Registrierung - und kein Umbau.
"""

from __future__ import annotations

import enum

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

import httpx

from .. import http_log

TIMEOUT = httpx.Timeout(15.0, connect=6.0)
MAX_PARALLEL_REQUESTS = 6

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


class MediaServerError(Exception):
    """Fehler beim Zugriff auf einen Media-Server - mit lesbarer Meldung."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def http_client() -> httpx.AsyncClient:
    """Gemeinsame HTTP-Verbindung (siehe arr.py: neue Clients kosten Sekunden)."""
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=TIMEOUT,
                    headers={"Accept": "application/json"},
                    # Eine Zeile pro Aufruf ins Protokoll - siehe http_log.py.
                    event_hooks=http_log.event_hooks("mediaserver"),
                    limits=httpx.Limits(
                        max_connections=MAX_PARALLEL_REQUESTS,
                        max_keepalive_connections=MAX_PARALLEL_REQUESTS,
                        keepalive_expiry=60.0,
                    ),
                )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


# --------------------------------------------------------------------------
# Datentypen - bewusst anbieter-neutral.
#
# Rohdaten von Plex (oder spaeter Jellyfin) werden im jeweiligen Adapter in
# diese Formen uebersetzt und verlassen ihn nie im Originalzustand. Sonst
# haette der erste Anbieter seine Eigenheiten in der ganzen Anwendung verteilt.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExternalAccount:
    """Ein Konto beim Media-Server - die Identitaet, die sich anmeldet."""

    provider: str
    account_id: str
    username: str
    # Plex gibt die Adresse nicht immer heraus; verwaltete Profile haben gar keine.
    email: str | None = None
    thumb: str | None = None


@dataclass(frozen=True)
class ServerUser:
    """Jemand, der Zugriff auf die Bibliothek des Administrators hat."""

    account_id: str
    username: str
    email: str | None = None


@dataclass(frozen=True)
class ServerCandidate:
    """Ein Server zur Auswahl bei der Einrichtung.

    ``machine_id`` ist die dauerhafte Kennung des Servers. Nach ihr wird
    spaeter der Zugriff geprueft - ausdruecklich nicht nach der Adresse, denn
    dieselbe Installation ist mal ueber die lokale IP und mal ueber eine
    Fremdadresse erreichbar.

    ``urls`` sind **alle** bekannten Adressen, die lokalen zuerst. Welche davon
    taugt, entscheidet sich erst beim Ausprobieren: die lokale ist die schnellere,
    aber aus einem abgeschotteten Docker-Netz heraus nicht immer erreichbar.

    ``owned`` trennt eigene Server von solchen, auf die nur geteilt wurde. Der
    Unterschied ist wichtig: Bei einem fremden Server duerfte Nexview spaeter
    keine fremden Wiedergabe-Daten lesen, und die Zugriffspruefung wuerde am
    falschen Kreis haengen.
    """

    machine_id: str
    name: str
    url: str
    owned: bool = False
    urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoginChallenge:
    """Der angefangene Anmeldevorgang.

    ``ref`` ist die Kennung beim Anbieter (bei Plex die PIN-Nummer). Sie bleibt
    im Backend; der Browser bekommt sie nie zu sehen.
    """

    ref: str
    code: str
    auth_url: str


@dataclass(frozen=True)
class LibraryItem:
    """Ein Titel in der Bibliothek des Media-Servers.

    Vorbereitet fuer Meilenstein 2 ("extern hinzugefuegt erkennen"), noch nicht
    befuellt.
    """

    media_type: str
    guid: str
    title: str
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    year: int | None = None
    # Die interne Nummer beim Anbieter. Wird gebraucht, weil der
    # Wiedergabe-Verlauf ausschliesslich damit auf Titel verweist.
    rating_key: str | None = None
    # Hat das Konto, mit dessen Zugang gelesen wurde, das gesehen? Der Anbieter
    # liefert das am Titel mit, also faellt es beim Einlesen der Bibliothek
    # kostenlos ab. Und es ist die weit bessere Quelle als der Verlauf: der ist
    # gedeckelt (gemessen 499 Eintraege, davon 38 Filme), der Zaehler am Titel
    # dagegen vollstaendig (gemessen 354 gesehene Filme). Beim Einlesen mit dem
    # hinterlegten Zugang ist das der Eigentuemer; ``watched_index`` liest mit
    # dem persoenlichen Token, dann gilt es fuer dessen Konto.
    owner_watched: bool = False
    # Wann zuletzt gesehen - aus Sicht desselben Kontos. Fehlt bei Anbietern,
    # die nur den Zaehler kennen.
    watched_at: datetime | None = None
    # In welcher Stufe liegt der Titel hier? Zwei Merkmale, weil beides
    # gleichzeitig zutreffen kann: Plex fuehrt mehrere Dateien unter einem
    # Titel, etwa 1080p und 4K nebeneinander.
    #
    # ``has_standard`` ist bewusst standardmaessig **wahr**: Wo die Auflösung
    # unbekannt ist - bei Serien liefert Plex am Titel gar keine Datei-Angaben -
    # bleibt es beim bisherigen Verhalten "vorhanden". Eine 4K-Behauptung
    # dagegen wird nie geraten; ``has_uhd`` ist nur wahr, wenn es dasteht.
    has_standard: bool = True
    has_uhd: bool = False
    # Belegter Platz in Bytes, **getrennt nach Stufe** - aus demselben Grund
    # wie oben: 1080p und 4K sind zwei Dateien und werden getrennt verbucht.
    #
    # Gebraucht wird das fuer einen verbreiteten Arbeitsablauf: laden, bis die
    # Qualitaet stimmt, dann den Eintrag aus Radarr/Sonarr werfen und die Datei
    # behalten. Danach ist der Media-Server die einzige Stelle, die die Groesse
    # noch kennt.
    #
    # Null heisst "unbekannt", nicht "leer": Bei Serien haengen die Dateien an
    # den Folgen, der Serien-Eintrag traegt keine Groesse.
    size_standard: int = 0
    size_uhd: int = 0
    # Seit wann die Datei auf dem Server liegt.
    #
    # ⚠️ **Der einzige Weg zum Alter fuer Titel, die Radarr nicht mehr fuehrt.**
    # Der verbreitete Ablauf "laden bis die Qualitaet stimmt, dann den Eintrag
    # aus Radarr werfen und die Datei behalten" laesst genau solche Posten
    # zurueck: Sie belegen Platz, aber keine Instanz kennt mehr ein
    # ``dateAdded``. Ohne diese Angabe kann der Aufraeum-Vorschlag ueber sie
    # nichts sagen - und sagt es beim Nutzer als "wird nachgetragen", was fuer
    # sie nie eintraete.
    added_at: datetime | None = None


@dataclass(frozen=True)
class SeasonWatchedRecord:
    """Eine **vollstaendig** gesehene Staffel des abgefragten Kontos.

    ``item_key`` verweist auf die Serie, ``season`` auf die Staffelnummer.
    Die Liste ist als Ganzes zu lesen: Was nicht drinsteht, gilt als nicht
    (mehr) vollstaendig gesehen - erscheinen neue Folgen, verschwindet die
    Staffel wieder aus ihr.
    """

    item_key: str
    season: int


@dataclass(frozen=True)
class WatchedRecord:
    """Ein gesehener Titel je Konto.

    ``account_id`` ist die Kennung, unter der der Anbieter die Wiedergabe
    fuehrt - die muss nicht dieselbe sein, unter der sich jemand anmeldet.
    ``item_key`` verweist auf den Titel in der Bibliothek; bei einer Folge auf
    die **Serie**, denn fuer ein Abzeichen an der Kachel zaehlt die Serie.
    """

    account_id: str
    item_key: str
    media_type: str
    watched_at: datetime | None = None


@dataclass(frozen=True)
class WatchlistItem:
    """Ein Titel auf der persoenlichen Merkliste eines Kontos.

    Die Merkliste gehoert der Person, nicht dem Server: Sie laesst sich nur
    mit **ihrem** Token lesen, der Zugang des Administrators sieht sie nicht.

    ``tmdb_id`` ist beim Auflisten noch leer. Der Anbieter nennt die fremden
    Kennungen erst, wenn man den einzelnen Titel abruft - eine Abfrage je
    Titel. Deshalb sind Auflisten und Zuordnen zwei Schritte: aufgelistet wird
    alles, nachgeschlagen nur, was Nexview noch nie gesehen hat.
    """

    guid: str
    media_type: str  # "movie" | "tv"
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    # Die interne Nummer beim Anbieter - nur mit ihr laesst sich der einzelne
    # Titel nachschlagen.
    rating_key: str | None = None


class Umrechnung(str, enum.Enum):
    """Wie stark der Server an einer Wiedergabe arbeitet.

    ⚠️ **Drei Zustaende, nicht zwei - und das ist an echten Servern gemessen,
    nicht gedacht.** "Rechnet der Server um" ist die Frage, die einem Betreiber
    die CPU erklaert; die naheliegende Antwort waere "laeuft eine Umrechnung
    ja/nein". Sie ist falsch:

    * Plex meldete am 30.08.2026 bei laufendem Film eine ``TranscodeSession``
      mit ``videoDecision: "copy"`` und ``audioDecision: "transcode"`` - das
      Bild wird durchgereicht, nur der Ton umgerechnet.
    * Emby meldete im selben Test ``PlayMethod: "Transcode"`` **und**
      ``IsVideoDirect: true`` - dieselbe Lage, andere Worte.

    Beide haetten als "Umrechnung" in Rot gestanden, obwohl der Server fast
    nichts tut. Was zaehlt, ist allein das **Bild**.
    """

    direkt = "direkt"
    #: Nur Ton oder Behaelter - billig.
    ton = "ton"
    #: Das Bild wird neu berechnet - das ist der teure Fall.
    bild = "bild"


@dataclass(frozen=True)
class Wiedergabe:
    """Was gerade auf einem Server laeuft.

    ⚠️ **Nur wirklich laufende Wiedergaben.** Jellyfin und Emby geben unter
    ``/Sessions`` auch bloss **verbundene Geraete** heraus - gemessen am
    30.08.2026: vier bzw. fuenf Sitzungen, davon null mit Wiedergabe, darunter
    Eintraege von vor zwei Tagen und **Nexview selbst**. Wer das ungefiltert
    anzeigt, meldet "fuenf Leute schauen gerade", waehrend niemand schaut.
    Der Adapter filtert deshalb, nicht der Aufrufer.

    ``konto`` ist der Name beim Anbieter - die Zuordnung zu einem
    Nexview-Konto macht der Dienst darueber, nicht der Adapter.
    """

    provider: str
    konto: str
    titel: str
    #: "movie" | "tv" - wie ueberall sonst.
    media_type: str
    #: Die Nummer des Kontos **beim Anbieter**. Der sichere Weg zum
    #: Nexview-Konto: Sie steht so auch an der Verknuepfung. Der Name taugt
    #: dafuer nicht - gemessen am 30.08.2026 heisst dieselbe Person bei
    #: Jellyfin "Markus" und in Nexview "admin-kezorm".
    konto_id: str = ""
    #: Bei einer Folge die Serie; sonst leer.
    serie: str = ""
    #: 0.0 bis 1.0. ``None`` heisst: der Anbieter sagt es nicht.
    fortschritt: float | None = None
    geraet: str = ""
    anwendung: str = ""
    pausiert: bool = False
    umrechnung: Umrechnung = Umrechnung.direkt
    #: Warum umgerechnet wird - im Wortlaut des Anbieters, unuebersetzt.
    grund: str = ""
    #: Laeuft eine Grafikkarte mit? Leer heisst: der Anbieter sagt nichts dazu.
    beschleunigung: str = ""
    #: Kilobit je Sekunde, soweit gemeldet.
    bandbreite: int | None = None
    #: Die Kennung des Titels beim Anbieter - Bruecke in den Bestand.
    tmdb_id: int | None = None


class MediaServer(ABC):
    """Was Nexview von einem Media-Server erwartet."""

    provider: ClassVar[str]
    label: ClassVar[str]

    # Wie meldet man sich bei diesem Anbieter an?
    #
    # ``"pin"``      - ueber einen Dritten: Nexview zeigt einen Code, bestaetigt
    #                  wird bei plex.tv. Nexview sieht das Passwort nie.
    # ``"password"`` - direkt: Benutzername und Passwort gehen an den Server.
    #                  Jellyfin und Emby haben keinen Dritten.
    #
    # ⚠️ Das ist keine Zierde, sondern entscheidet, **welchen Knopf** die
    # Anmeldeseite zeigt. Vorher hing dort ein fester "Mit Plex anmelden" an
    # der blossen Frage, ob *irgendein* Server verbunden ist - eine
    # Installation mit nur Jellyfin bekam damit einen Knopf, der beim Klick
    # eine Fehlermeldung warf.
    login_kind: ClassVar[str] = "pin"

    # Nennt dieser Anbieter zu einem Konto eine E-Mail-Adresse?
    #
    # Daran haengt mehr, als es aussieht. Die Adresse ist das einzige Merkmal,
    # ueber das sich eine fremde Identitaet einem **bestehenden** Nexview-Konto
    # zuordnen laesst: Wer eingeladen wurde und sich spaeter erstmals ueber den
    # Medienserver anmeldet, landet dank ihr in seinem Konto statt in einem
    # zweiten.
    #
    # ⚠️ **Jellyfin hat kein solches Feld** - nicht leer, sondern gar nicht
    # vorhanden. Deshalb kann ueber Jellyfin kein Konto neu entstehen: Nexview
    # koennte nicht unterscheiden, ob da ein neuer Mensch steht oder jemand,
    # der laengst ein Konto hat. Ueber den Namen zu raten waere ein
    # Sicherheitsloch - wer sein Jellyfin-Konto "hans" nennt, uebernaehme
    # Hans' Nexview-Konto.
    knows_email: ClassVar[bool] = True

    # --- Einrichtung -------------------------------------------------------

    @abstractmethod
    async def verify(self) -> dict[str, Any]:
        """Verbindungstest - liefert Name, Version und Kennung des Servers."""

    @abstractmethod
    async def list_servers(self, provider_token: str) -> list[ServerCandidate]:
        """**Alle** Server, auf die dieses Konto Zugriff hat.

        Bewusst ungefiltert: Die Zugriffspruefung braucht auch die Server, auf
        die nur geteilt wurde - sonst kaeme kein einziger eingeladener Gast
        mehr herein. Wer nur die eigenen sehen will (die Einrichtung), filtert
        selbst ueber ``owned``.
        """

    @abstractmethod
    async def probe(self, url: str, provider_token: str) -> bool:
        """Antwortet der Server unter dieser Adresse?

        Damit nicht stillschweigend eine Adresse gespeichert wird, die aus
        Sicht von Nexview gar nicht erreichbar ist.
        """

    # --- Anmeldung ---------------------------------------------------------

    @abstractmethod
    async def begin_login(self) -> LoginChallenge:
        """Anmeldung starten - der Browser oeffnet danach ``auth_url``."""

    @abstractmethod
    async def poll_login(self, ref: str, code: str = "") -> str | None:
        """Nachsehen, ob bestaetigt wurde.

        Gibt das Token des Anbieters zurueck, sobald die Person zugestimmt hat,
        sonst ``None`` (noch offen). ``code`` gehoert dazu, weil Plex ihn bei
        jeder Nachfrage erwartet - Jellyfins Quick Connect ebenso.
        """

    async def login_with_password(
        self, username: str, password: str, url: str | None = None, zweck: str = ""
    ) -> tuple[str, ExternalAccount, bool]:
        """Anmelden mit Benutzername und Passwort.

        Der zweite Weg herein, neben dem PIN-Ablauf darueber - und fuer manche
        Anbieter der einzige. Plex hat mit plex.tv einen Vermittler, bei dem
        man bestaetigt; Jellyfin und Emby haben keinen und nehmen die Angaben
        direkt entgegen.

        Zurueck kommen drei Dinge: das Token, das Konto - und ob es ein
        Administrator ist. Das letzte steht hier, weil der Anbieter es bei
        derselben Antwort mitliefert und der Aufrufer es beim Verbinden
        braucht: Ein Zugang ohne Verwaltungsrechte kann die Konten des Servers
        nicht lesen, und das soll auffallen, bevor eine halbe Verbindung
        gespeichert ist.

        ⚠️ **Das Passwort darf nirgends bleiben.** Es geht durch diese Methode
        an den Anbieter und wird nie gespeichert, nie protokolliert und nie an
        den Browser zurueckgegeben. Aufbewahrt wird ausschliesslich das Token.

        ``zweck`` trennt Anmeldungen, die nichts miteinander zu tun haben -
        die Server-Verbindung des Administrators und die persoenlichen
        Zugaenge. Jellyfin fuehrt Zugaenge je *Geraet*: Ohne diese Trennung
        loescht jede neue Anmeldung die vorherige, und das Verbinden des
        Servers und das Anmelden derselben Person schliessen sich gegenseitig
        aus. Genau so passiert.

        Anbieter ohne diesen Weg lassen die Methode stehen;
        ``supports_password_login`` sagt es vorher.
        """
        raise NotImplementedError

    @classmethod
    def supports_password_login(cls) -> bool:
        """Kennt dieser Anbieter die Anmeldung mit Passwort?

        Wie ``supports_watchlist``: Die Oberflaeche fragt hier nach, statt ein
        Formular anzubieten, das der Anbieter gar nicht bedienen kann.
        """
        return cls.login_with_password is not MediaServer.login_with_password

    @abstractmethod
    async def account_for_token(self, provider_token: str) -> ExternalAccount:
        """Zu welchem Konto gehoert dieses Token?"""

    @abstractmethod
    async def user_has_server_access(self, provider_token: str) -> bool:
        """Darf dieses Konto auf die Bibliothek des Administrators?

        Das ist die einzige Huerde gegen Fremde und wird deshalb immer
        geprueft, **bevor** ein Konto entsteht oder veraendert wird.
        """

    # --- Merkliste ---------------------------------------------------------

    async def watchlist(self, provider_token: str) -> list[WatchlistItem]:
        """Die vollstaendige Merkliste zu diesem Token.

        Ohne fremde Kennungen - die kostet der Anbieter einzeln, siehe
        ``watchlist_ids``. Ein Anbieter ohne Merkliste laesst diese Methode
        weg; ``supports_watchlist`` sagt es vorher.
        """
        raise NotImplementedError

    async def watchlist_ids(
        self, provider_token: str, items: list[WatchlistItem]
    ) -> list[WatchlistItem]:
        """Dieselben Titel, um die TMDB-Nummer ergaenzt (soweit auffindbar).

        Absichtlich als Liste und nicht je Titel: So darf ein Anbieter die
        Abfragen buendeln oder begrenzt nebenlaeufig stellen, ohne dass der
        Aufrufer davon etwas wissen muss.
        """
        raise NotImplementedError

    @classmethod
    def supports_watchlist(cls) -> bool:
        """Kennt dieser Anbieter ueberhaupt eine Merkliste?

        Jellyfin und Emby haben keine. Die Oberflaeche fragt hier nach, statt
        einen Haken anzubieten, der nichts tun kann.
        """
        return cls.watchlist is not MediaServer.watchlist

    # --- Vorbereitet, noch nicht gebaut ------------------------------------

    async def list_server_users(self) -> list[ServerUser]:
        """Alle Konten, unter denen der Server Wiedergabe fuehrt.

        Wichtig und leicht zu uebersehen: Diese Kennungen sind **nicht** die,
        mit denen sich jemand anmeldet. Plex fuehrt den Eigentuemer unter der 1,
        geteilte Nutzer dagegen unter ihrer plex.tv-Nummer. Deshalb kommt hier
        auch der Anzeigename mit - ueber ihn laesst sich der Eigentuemer
        zuordnen, wenn die Nummer nicht passt.
        """
        raise NotImplementedError

    async def library_index(self) -> list[LibraryItem]:
        """Meilenstein 2 - erkennt Titel, die nicht ueber Radarr/Sonarr kamen."""
        raise NotImplementedError

    async def laufende_wiedergaben(self) -> list[Wiedergabe]:
        """Was gerade laeuft.

        ⚠️ **Gibt eine leere Liste zurueck statt ``NotImplementedError``.**
        Anders als bei den uebrigen Faehigkeiten ist "kann ich nicht" hier
        keine Stoerung: Die Anzeige heisst "gerade laeuft nichts", und dieselbe
        Antwort ist bei einem Anbieter ohne Sitzungsliste richtig. Wer es
        anders macht, zwingt jeden Aufrufer zu einem ``try``.
        """
        return []

    async def watched_since(self, since: datetime | None = None) -> list[WatchedRecord]:
        """Meilenstein 3 - wer hat was gesehen (Wiedergabe-Verlauf des Servers).

        Nur eine Notloesung fuer Konten ohne eigenes Token: Der Verlauf ist
        gedeckelt und kennt manuell Abgehaktes nicht. Wo ein persoenliches
        Token vorliegt, ist ``watched_index`` die richtige Quelle.
        """
        raise NotImplementedError

    async def watched_index(
        self, provider_token: str, account_id: str = ""
    ) -> list[WatchedRecord]:
        """Der vollstaendige Gesehen-Stand des Kontos hinter diesem Token.

        Liest die Bibliothek mit dem **persoenlichen** Token der Person - der
        Zaehler am Titel gilt dann fuer ihr Konto und ist vollstaendig,
        einschliesslich allem, was sie von Hand als gesehen markiert hat.
        ``account_id`` bleibt in den Eintraegen leer: Wessen Stand das ist,
        weiss der Aufrufer bereits, denn ihm gehoert das Token.

        ``account_id`` ist die Nummer des Kontos auf dem Server, falls der
        Aufrufer sie kennt - er kennt sie fast immer, sie steht an der
        Verknuepfung. Fuer Emby ist sie **noetig**: Dort gibt es kein
        "/Users/Me", ueber das ein Adapter sie sonst erfragen koennte.
        """
        raise NotImplementedError

    async def watched_seasons(
        self, provider_token: str, series_keys: list[str], account_id: str = ""
    ) -> list[SeasonWatchedRecord]:
        """Vollstaendig gesehene Staffeln **der genannten Serien**.

        Gezielt statt flaechendeckend: Die Staffel-Zaehler kosten bei Plex
        eine Abfrage **je Serie** - gebraucht werden sie aber nur fuer die
        Serien, die in Speicher-Posten der Person stehen, und das sind eine
        Handvoll. Fuer die genannten Serien ist die Antwort vollstaendig:
        Was fehlt, gilt als nicht (mehr) komplett gesehen. Anbieter ohne
        Staffel-Zaehler lassen die Methode auf ``NotImplementedError`` stehen -
        dann gibt es schlicht keine Staffel-Augen, statt falscher.
        """
        raise NotImplementedError
