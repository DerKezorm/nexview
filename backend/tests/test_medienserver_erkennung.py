"""Zwei Medienserver auf demselben Rechner duerfen sich nicht verwechseln.

⚠️ **Der Fall, der es in die Praxis geschafft hat.** Jellyfin und Emby laufen
bei Radarr unter derselben Umsetzung ``MediaBrowser``, und sie stehen fast
immer auf demselben Rechner - nur die Ports unterscheiden sie. Wer allein den
Wirt vergleicht, haelt Emby fuer verbunden, sobald Jellyfin es ist: Nexview
meldete "Alle verbunden", waehrend in Radarr nur zwei von drei Verbindungen
standen, und legte die fehlende nie an.
"""

from __future__ import annotations

from app.services.medienserver_verbindung import Medienserver, _passender_eintrag


def _server(provider: str, url: str) -> Medienserver:
    return Medienserver(
        id=1, provider=provider, name=provider, url=url, zugang="x", braucht_schluessel=True
    )


def _eintrag(umsetzung: str, wirt: str, tor: int) -> dict:
    return {
        "implementation": umsetzung,
        "fields": [
            {"name": "host", "value": wirt},
            {"name": "port", "value": tor},
        ],
    }


JELLYFIN = _eintrag("MediaBrowser", "10.10.10.109", 8096)
PLEX = _eintrag("PlexServer", "10.10.10.109", 32400)


def test_jellyfin_gilt_nicht_als_emby():
    """Der Kern: gleicher Wirt, gleiche Umsetzung, anderer Port."""
    emby = _server("emby", "http://10.10.10.109:8097")
    assert _passender_eintrag([JELLYFIN, PLEX], emby) is None


def test_jellyfin_erkennt_sich_selbst():
    jellyfin = _server("jellyfin", "http://10.10.10.109:8096")
    assert _passender_eintrag([JELLYFIN, PLEX], jellyfin) is JELLYFIN


def test_plex_wird_an_der_umsetzung_erkannt():
    plex = _server("plex", "https://10.10.10.109:32400")
    assert _passender_eintrag([JELLYFIN, PLEX], plex) is PLEX


def test_anderer_rechner_zaehlt_nicht():
    """Gleicher Port, anderer Wirt - das ist ein anderer Server."""
    woanders = _server("jellyfin", "http://10.10.10.50:8096")
    assert _passender_eintrag([JELLYFIN], woanders) is None


def test_eintrag_ohne_port_gilt_als_passend():
    """Fehlt die Angabe, ist der Standardport gemeint - dann zaehlt der Wirt.

    Von Hand angelegte Eintraege fuehren das Feld nicht immer; sie sollen
    trotzdem als vorhandene Verbindung gelten, statt eine zweite zu erzeugen.
    """
    ohne_port = {"implementation": "MediaBrowser", "fields": [{"name": "host", "value": "10.10.10.109"}]}
    jellyfin = _server("jellyfin", "http://10.10.10.109:8096")
    assert _passender_eintrag([ohne_port], jellyfin) is ohne_port


def test_port_als_zahl_oder_text():
    """Radarr liefert den Port mal als Zahl, mal als Zeichenkette."""
    als_text = {
        "implementation": "MediaBrowser",
        "fields": [
            {"name": "host", "value": "10.10.10.109"},
            {"name": "port", "value": "8096"},
        ],
    }
    jellyfin = _server("jellyfin", "http://10.10.10.109:8096")
    assert _passender_eintrag([als_text], jellyfin) is als_text
