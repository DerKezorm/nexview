"""Media-Server-Anbindung.

Hier - und nur hier - steht, welche Anbieter es gibt. Der Rest der Anwendung
holt sich ueber ``get_media_server`` eine Verbindung und spricht ausschliesslich
mit der Schnittstelle ``MediaServer``. Ein weiterer Anbieter (Jellyfin, Emby)
ist deshalb eine neue Datei plus ein Eintrag in ``PROVIDERS``.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from .base import (
    ExternalAccount,
    LibraryItem,
    LoginChallenge,
    MediaServer,
    MediaServerError,
    ServerCandidate,
    ServerUser,
    WatchedRecord,
    WatchlistItem,
    close_http_client,
)
from .plex import PlexServer

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from ..settings_service import AppSettings

__all__ = [
    "ExternalAccount",
    "LibraryItem",
    "LoginChallenge",
    "MediaServer",
    "MediaServerError",
    "PROVIDERS",
    "ServerCandidate",
    "ServerUser",
    "WatchedRecord",
    "WatchlistItem",
    "close_http_client",
    "get_media_server",
    "media_server_for_setup",
    "new_client_identifier",
]

PROVIDERS: dict[str, type[MediaServer]] = {
    PlexServer.provider: PlexServer,
}


def new_client_identifier() -> str:
    """Kennung, unter der sich Nexview beim Anbieter meldet.

    Wird einmal je Installation erzeugt und dann in den Einstellungen
    aufbewahrt. Plex fuehrt angemeldete Geraete darueber - eine wechselnde
    Kennung wuerde bei jeder Anmeldung einen neuen Eintrag hinterlassen.
    """
    return secrets.token_hex(12)


def get_media_server(settings: "AppSettings") -> MediaServer | None:
    """Der eingerichtete Media-Server - ``None``, wenn keiner verbunden ist.

    Ohne Verbindung bleibt in Nexview schlicht alles beim Alten; niemand muss
    einen Media-Server betreiben.
    """
    if not settings.mediaserver_configured:
        return None
    return media_server_for_setup(settings, settings.mediaserver_provider)


def media_server_for_setup(settings: "AppSettings", provider: str = "plex") -> MediaServer:
    """Adapter fuer die Ersteinrichtung.

    Beim Verbinden gibt es naturgemaess noch keinen ausgewaehlten Server -
    deshalb hier ohne die Pruefung aus ``get_media_server``.
    """
    klasse = PROVIDERS.get(provider)
    if klasse is None:
        raise MediaServerError(f"Unbekannter Media-Server: {provider}")
    return klasse(settings)
