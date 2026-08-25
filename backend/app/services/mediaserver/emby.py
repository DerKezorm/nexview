"""Emby als Media-Server.

Emby und Jellyfin sind verwandt - Jellyfin ist 2018 aus Emby hervorgegangen -,
und die Schnittstelle hat sich seither weniger auseinanderentwickelt, als man
erwarten wuerde. Gemessen an Emby 4.9.5.0 gegen einen echten Server:

* ``/System/Info/Public``, ``/System/Info``, ``/Users``, ``/Items`` und
  ``/Users/AuthenticateByName`` antworten in derselben Form.
* ``ProviderIds`` traegt ``Tmdb`` wie gehabt.
* ``UserData`` fuehrt ``Played``, ``PlayCount`` und ``PlaybackPositionTicks``.
* Und die Ausweiszeile: Emby akzeptiert **auch** die Jellyfin-Schreibweise
  ``Authorization: MediaBrowser ...`` - gemessen, alle fuenf Varianten
  antworteten mit HTTP 200. Deshalb erbt diese Klasse den ganzen Apparat und
  ueberschreibt nur, was sich wirklich unterscheidet.

⚠️ **Auch Emby-Konten haben keine E-Mail-Adresse.** Nachgemessen an einem
echten Server: Die Kontenliste fuehrt kein solches Feld. Es gilt deshalb
dasselbe wie bei Jellyfin - die Anmeldung ueber Emby darf **kein Konto
anlegen**, weil die Adresse das Einzige ist, woran Nexview jemanden
wiedererkennt. Wer eingeladen wurde, bekaeme sonst still ein zweites Konto
ohne Passwort und ohne Weg zurueck. Verknuepft wird aus dem Profil heraus.

**Die PIN je Konto ist keine Anmeldehuerde.** Emby fuehrt sie unter
``Configuration.ProfilePin`` - also zwischen ``SubtitleMode`` und
``PlayDefaultAudioTrack``, bei den Anzeigeeinstellungen, und ausdruecklich
**nicht** unter ``Policy`` bei den Rechten. Es ist eine Profil-PIN wie bei
Netflix: zum Umschalten auf einem geteilten Geraet. ``AuthenticateByName``
mit Benutzername und Passwort ist davon unberuehrt; ein Konto mit PIN traegt
weiterhin ``HasPassword: true``. Nachgemessen an einem Konto, das eine hat.

⚠️ **Und sie steht dort im Klartext.** Wer einen Emby-API-Schluessel hat,
liest sie mit. Nexview holt die Konten ueber ``/Users`` und bekommt den
``Configuration``-Block damit ungefragt mitgeliefert - er wird **nirgends
gespeichert und nirgends protokolliert**, und das soll so bleiben. Wer hier
einmal `logger.debug(daten)` schreibt, hat die PINs aller Konten im
Protokoll stehen.
"""

from __future__ import annotations

import logging

from .base import ExternalAccount, MediaServerError
from .jellyfin import JellyfinServer

logger = logging.getLogger("nexview.mediaserver")


class EmbyServer(JellyfinServer):
    """Emby - technisch Jellyfins aelterer Bruder.

    Bewusst eine Ableitung und keine Kopie. Zwei fast gleiche Dateien
    nebeneinander heissen, dass jede Fehlerbehebung zweimal gemacht werden
    muss - und beim zweiten Mal vergessen wird. Was sich unterscheidet, steht
    hier; alles andere kommt aus ``jellyfin.py`` und wird dort gepflegt.
    """

    provider = "emby"
    label = "Emby"
    # ⚠️ Keine Merkliste - und das ist gemessen, nicht von Jellyfin geerbt.
    #
    # Emby *hat* Favoriten je Konto, abfragbar mit ``Filters=IsFavorite`` und
    # samt TMDB-Kennung. Sie taugen hier trotzdem nicht: Favorisieren laesst
    # sich nur, was schon in der Bibliothek liegt. Plex' Merkliste ist das
    # Gegenteil - sie lebt bei plex.tv und enthaelt gerade das, was noch
    # fehlt, und genau darauf ist die Funktion gebaut ("aus der Merkliste
    # anfragen"). Aus Emby-Favoriten liesse sich nichts anfragen; der Reiter
    # waere da, und dahinter stuende eine Liste ohne Handlung.
    login_kind = "password"
    # Nachgemessen: kein E-Mail-Feld an der Kontenliste. Siehe Kopf der Datei.
    knows_email = False

    async def user_has_server_access(self, provider_token: str) -> bool:
        """Ein Emby-Token gilt nur auf dem Server, der es ausgestellt hat.

        Dieselbe Begruendung wie bei Jellyfin: Wer eines hat, das hier
        funktioniert, hat damit Zugriff - anders als bei Plex, wo ein
        plex.tv-Token fuer *irgendeinen* Server gilt.

        Der Unterschied liegt nur im Nachweis. Jellyfin fragt ``/Users/Me``;
        das gibt es bei Emby nicht (HTTP 500, gemessen an 4.9.5.0).

        ⚠️ **Und hier keinen anderen Endpunkt einsetzen, ohne zu pruefen, wer
        ihn aufrufen darf.** Der erste Entwurf nahm ``/Sessions`` - ein Weg,
        den Emby moeglicherweise nur Administratoren erlaubt. Damit haette
        ausgerechnet jeder gewoehnliche Benutzer ein "kein Zugriff" bekommen
        und sich nicht verknuepfen koennen. Beide Aufrufstellen folgen
        unmittelbar auf eine **erfolgreiche** Anmeldung; das Token ist Sekunden
        alt und vom Server selbst ausgestellt. Ein zusaetzlicher Aufruf koennte
        daran nichts mehr pruefen, was die Anmeldung nicht schon bewiesen hat -
        er koennte nur an Rechten scheitern, die mit der Frage nichts zu tun
        haben.
        """
        return bool(provider_token)

    async def account_for_token(self, provider_token: str) -> ExternalAccount:
        """Gibt es bei Emby nicht - und wird auf diesem Weg auch nicht gebraucht.

        Der Aufruf gehoert zum Plex-Ablauf, bei dem erst ein Token feststeht
        und danach gefragt wird, wem es gehoert. Emby meldet sich mit
        Benutzername und Passwort an, und ``AuthenticateByName`` nennt das
        Konto in derselben Antwort - es muss also nie nachgefragt werden.

        Statt hier etwas zu erfinden, sagt es die Wahrheit: Wer diesen Weg
        kuenftig fuer Emby benutzen will, braucht die Kontonummer von dort,
        wo sie schon steht - an der Verbindung oder an der Verknuepfung.
        """
        raise MediaServerError(
            f"{self.label} kann zu einem Token kein Konto nennen. "
            "Bitte mit Benutzername und Passwort verbinden.",
            401,
        )
