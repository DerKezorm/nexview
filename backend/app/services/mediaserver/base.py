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

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

import httpx

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
    # Hat der **Eigentuemer** des hinterlegten Zugangs das gesehen? Der Anbieter
    # liefert das am Titel mit, also faellt es beim Einlesen der Bibliothek
    # kostenlos ab. Und es ist die weit bessere Quelle als der Verlauf: der ist
    # gedeckelt (gemessen 499 Eintraege, davon 38 Filme), der Zaehler am Titel
    # dagegen vollstaendig (gemessen 354 gesehene Filme).
    owner_watched: bool = False
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


class MediaServer(ABC):
    """Was Nexview von einem Media-Server erwartet."""

    provider: ClassVar[str]
    label: ClassVar[str]

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

    async def watched_since(self, since: datetime | None = None) -> list[WatchedRecord]:
        """Meilenstein 3 - wer hat was gesehen."""
        raise NotImplementedError
