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


@dataclass(frozen=True)
class WatchedRecord:
    """Ein gesehener Titel je Konto.

    Vorbereitet fuer Meilenstein 3 ("schon gesehen"), noch nicht befuellt.
    """

    account_id: str
    guid: str
    watched_at: datetime | None = None


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

    # --- Vorbereitet, noch nicht gebaut ------------------------------------

    async def list_server_users(self) -> list[ServerUser]:
        """Alle, die Zugriff auf die Bibliothek haben.

        Erst fuer Meilenstein 3 noetig. Bewusst noch offen: Plex fuehrt je
        Server eigene Konto-Nummern, die **nicht** mit denen von plex.tv
        uebereinstimmen - welche hier die richtige ist, entscheidet sich erst
        an der Wiedergabe-Auswertung.
        """
        raise NotImplementedError

    async def library_index(self) -> list[LibraryItem]:
        """Meilenstein 2 - erkennt Titel, die nicht ueber Radarr/Sonarr kamen."""
        raise NotImplementedError

    async def watched_since(self, since: datetime | None = None) -> list[WatchedRecord]:
        """Meilenstein 3 - wer hat was gesehen."""
        raise NotImplementedError
