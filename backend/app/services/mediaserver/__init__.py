"""Media-Server-Anbindung.

Hier - und nur hier - steht, welche Anbieter es gibt. Der Rest der Anwendung
holt sich ueber ``get_media_server`` eine Verbindung und spricht ausschliesslich
mit der Schnittstelle ``MediaServer``. Ein weiterer Anbieter (Jellyfin, Emby)
ist deshalb eine neue Datei plus ein Eintrag in ``PROVIDERS``.
"""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING

from .base import (
    ExternalAccount,
    LibraryItem,
    LoginChallenge,
    MediaServer,
    MediaServerError,
    SeasonWatchedRecord,
    ServerCandidate,
    ServerUser,
    WatchedRecord,
    WatchlistItem,
    close_http_client,
)
from .emby import EmbyServer
from .jellyfin import JellyfinServer
from .plex import PlexServer

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from ..settings_service import AppSettings, Verbindung

__all__ = [
    "PROVIDERS",
    "ExternalAccount",
    "LibraryItem",
    "LoginChallenge",
    "MediaServer",
    "MediaServerError",
    "SeasonWatchedRecord",
    "ServerCandidate",
    "ServerUser",
    "WatchedRecord",
    "WatchlistItem",
    "close_http_client",
    "get_media_server",
    "media_server_for_setup",
    "merklisten_anbieter",
    "merklisten_server",
    "new_client_identifier",
    "verbindung_fuer",
    "verbundene_anbieter",
]

logger = logging.getLogger("nexview.mediaserver")

PROVIDERS: dict[str, type[MediaServer]] = {
    PlexServer.provider: PlexServer,
    JellyfinServer.provider: JellyfinServer,
    EmbyServer.provider: EmbyServer,
}


def new_client_identifier() -> str:
    """Kennung, unter der sich Nexview beim Anbieter meldet.

    Wird einmal je Installation erzeugt und dann in den Einstellungen
    aufbewahrt. Plex fuehrt angemeldete Geraete darueber - eine wechselnde
    Kennung wuerde bei jeder Anmeldung einen neuen Eintrag hinterlassen.
    """
    return secrets.token_hex(12)


def get_media_server(settings: AppSettings) -> MediaServer | None:
    """Der eingerichtete Media-Server - ``None``, wenn keiner verbunden ist.

    Ohne Verbindung bleibt in Nexview schlicht alles beim Alten; niemand muss
    einen Media-Server betreiben.

    ⚠️ **Ein unbekannter Anbieter gilt als "keiner", nicht als Fehler.**
    Bis 0.18.0 gab es dafuer eine zweite Liste in ``settings_service``, die
    jeden anderen Wert still zu "" machte. Sie ist weggefallen - der Anbieter
    wird jetzt beim Verbinden gegen ``PROVIDERS`` geprueft, also an genau einer
    Stelle statt an zweien, die auseinanderlaufen koennen.

    Geblieben ist aber ihre stille Nachsicht, und die wird gebraucht: In der
    Tabelle kann ein Anbieter stehen, den diese Fassung nicht kennt - nach
    einem Rueckschritt auf eine aeltere Version etwa. Wuerde das eine Ausnahme
    werfen, kaeme die Anwendung nicht mehr hoch; so laeuft sie weiter, nur eben
    ohne Medienserver. Mit einer Protokollzeile, damit es nicht raetselhaft
    bleibt.
    """
    if not settings.mediaserver_configured:
        return None
    if settings.mediaserver_provider not in PROVIDERS:
        logger.warning(
            "Media server %r is connected but unknown to this version - treated as "
            "not connected. A downgrade would explain it.",
            settings.mediaserver_provider,
        )
        return None
    return media_server_for_setup(settings, settings.mediaserver_provider)


def verbundene_anbieter(settings: AppSettings) -> list[str]:
    """Welche Medienserver sind gerade verbunden?

    Heute hoechstens einer - die Liste ist trotzdem eine Liste, weil genau hier
    der Parallelbetrieb ansetzt. Wer sie benutzt, schreibt schon jetzt Code,
    der mit mehreren umgehen kann, statt spaeter jede Aufrufstelle zu suchen.

    Gebraucht wird das, sobald eine Aussage vom *Vergleich* abhaengt: Ein
    gruenes Auge heisst bei einem Server schlicht "gesehen", bei zweien kann es
    "der eine sagt ja, der andere nein" heissen. Ohne diese Liste liesse sich
    der Unterschied nicht erkennen.
    """
    return [v.provider for v in settings.mediaserver_verbindungen if v.nutzbar]


def verbindung_fuer(settings: AppSettings, provider: str) -> Verbindung | None:
    """Die gespeicherte Verbindung **dieses** Anbieters.

    Der Unterschied zu den Einzelwerten in ``AppSettings`` ist im Parallel-
    betrieb entscheidend: Die gelten immer der *ersten* Verbindung. Wer den
    Jellyfin-Adapter mit ihnen baut, waehrend Plex an erster Stelle steht,
    bekommt einen Adapter mit der Plex-Adresse und dem Plex-Token.
    """
    for zeile in settings.mediaserver_verbindungen:
        if zeile.provider == provider:
            return zeile
    return None


def merklisten_anbieter(settings: AppSettings) -> list[str]:
    """Verbundene Anbieter, die ueberhaupt eine Merkliste kennen.

    ⚠️ Nicht dasselbe wie "verbunden". Jellyfin und Emby haben keine
    Merkliste - sie taucht dort nirgends auf, es gibt sie schlicht nicht.
    Vorher fragte der Merklisten-Dienst nach dem *ersten* verbundenen Server;
    auf einer Installation mit nur Jellyfin bekam er damit einen Adapter, der
    die Frage gar nicht beantworten kann, und der ganze Bereich lief ins Leere.
    """
    return [
        anbieter
        for anbieter in verbundene_anbieter(settings)
        if anbieter in PROVIDERS and PROVIDERS[anbieter].supports_watchlist()
    ]


def merklisten_server(settings: AppSettings) -> MediaServer | None:
    """Der Adapter fuer die Merkliste - ``None``, wenn keiner sie kann."""
    anbieter = merklisten_anbieter(settings)
    return media_server_for_setup(settings, anbieter[0]) if anbieter else None


def media_server_for_setup(
    settings: AppSettings, provider: str = "plex", url: str = ""
) -> MediaServer:
    """Adapter fuer die Ersteinrichtung.

    Beim Verbinden gibt es naturgemaess noch keinen ausgewaehlten Server -
    deshalb hier ohne die Pruefung aus ``get_media_server``. Ist der Anbieter
    dagegen schon verbunden, bekommt der Adapter dessen eigene Zeile.

    ``url`` ist fuer den Fall gedacht, dass es die Zeile noch gar nicht gibt:
    Bei Plex fragt man plex.tv, wo der Server steht - bei Jellyfin gibt es
    niemanden zu fragen, die Adresse tippt der Administrator ein. Ohne diesen
    Weg bekaeme der Adapter eine leere Adresse (oder, schlimmer, im
    Parallelbetrieb die des anderen Anbieters).
    """
    klasse = PROVIDERS.get(provider)
    if klasse is None:
        raise MediaServerError(f"Unbekannter Media-Server: {provider}")
    zeile = verbindung_fuer(settings, provider)
    if url:
        # Erst hier importiert: ``settings_service`` steht sonst nur in der
        # Typangabe, und das soll auch so bleiben.
        from ..settings_service import Verbindung

        zeile = Verbindung(
            provider=provider,
            machine_id=zeile.machine_id if zeile else "",
            name=zeile.name if zeile else "",
            url=url,
            token=zeile.token if zeile else "",
        )
    return klasse(settings, zeile)
