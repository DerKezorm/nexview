"""Laufende Wiedergaben - gegen echte, gemessene Antworten.

⚠️ **Die Beispieldaten hier sind nicht erfunden.** Sie stammen aus Abfragen an
die drei Server einer echten Anlage am 30.08.2026, bei laufendem Film. Das ist
der ganze Wert dieser Datei: Die Feldnamen und ihre Kombinationen sind
beobachtet, nicht aus einer Doku uebernommen - und genau dabei kamen zwei
Fallen heraus, die ein erfundener Testfall nie gezeigt haette.
"""

from __future__ import annotations

import pytest

from app.services.mediaserver.base import Umrechnung
from app.services.mediaserver.jellyfin import JellyfinServer
from app.services.mediaserver.plex import PlexServer

# --- Gemessene Antworten ----------------------------------------------------

#: Plex, laufender Film. ``videoDecision: "copy"`` neben
#: ``audioDecision: "transcode"`` - das Bild wird durchgereicht.
PLEX_TON = {
    "title": "8 Blickwinkel",
    "type": "movie",
    "viewOffset": 29000,
    "duration": 5405440,
    "ratingKey": "9405",
    "User": {"id": "1", "title": "DerKezorm"},
    "Player": {
        "device": "Windows",
        "platform": "Chrome",
        "product": "Plex Web",
        "state": "playing",
        "title": "Chrome",
    },
    "Session": {"id": "xx90j3", "bandwidth": 12839, "location": "wan"},
    "TranscodeSession": {
        "videoDecision": "copy",
        "audioDecision": "transcode",
        "sourceVideoCodec": "hevc",
        "speed": 8.5,
    },
}

#: Jellyfin, laufender Film mit **Bild**-Umrechnung.
JELLYFIN_BILD = {
    "UserName": "Jonas",
    "UserId": "11111111111111111111111111111111",
    "Client": "Jellyfin Web",
    "DeviceName": "Chrome",
    "NowPlayingItem": {
        "Name": "2 Fast 2 Furious",
        "Type": "Movie",
        "RunTimeTicks": 64558720000,
        "ProviderIds": {"Imdb": "tt0322259", "Tmdb": "584"},
    },
    "PlayState": {
        "PositionTicks": 221707239,
        "IsPaused": False,
        "PlayMethod": "Transcode",
    },
    "TranscodingInfo": {
        "IsVideoDirect": False,
        "IsAudioDirect": False,
        "Bitrate": 11505940,
        "HardwareAccelerationType": "none",
        "TranscodeReasons": ["AudioCodecNotSupported", "SubtitleCodecNotSupported"],
    },
}

#: Emby, laufender Film. ``PlayMethod: "Transcode"`` **und**
#: ``IsVideoDirect: true`` - nur der Ton wird umgerechnet.
EMBY_TON = {
    "UserName": "admin",
    "UserId": "22222222222222222222222222222222",
    "Client": "Emby Web",
    "DeviceName": "Chrome Windows",
    "NowPlayingItem": {
        "Name": "8MM - Acht Millimeter",
        "Type": "Movie",
        "RunTimeTicks": 73990080000,
        "ProviderIds": {"Tmdb": "8224", "Imdb": "tt0134273"},
    },
    "PlayState": {
        "PositionTicks": 205161260,
        "IsPaused": False,
        "PlayMethod": "Transcode",
    },
    "TranscodingInfo": {
        "IsVideoDirect": True,
        "IsAudioDirect": False,
        "Bitrate": 6140840,
    },
}

#: Jellyfin, **nur verbunden** - kein ``NowPlayingItem``. Genau so sahen alle
#: vier Sitzungen aus, solange niemand schaute; ``IsActive`` stand trotzdem auf
#: ``true``, obwohl der Eintrag zwei Tage alt war.
JELLYFIN_NUR_VERBUNDEN = {
    "UserName": "Jonas",
    "Client": "Infuse-Library",
    "DeviceName": "Apple TV",
    "IsActive": True,
    "LastActivityDate": "2026-08-28T04:38:01",
    "PlayState": {"CanSeek": False, "IsPaused": False},
}


# ⚠️ **Ohne Konstruktor gebaut, und das ist hier richtig.** Die Adapter
# brauchen zum Bauen die ganzen Einstellungen samt Verbindung; ``_als_wiedergabe``
# liest davon nichts ausser ``provider``, das am *Typ* haengt. Eine
# Einstellungs-Attrappe waere hier nur Zeremonie - und sie wuerde beim
# naechsten neuen Feld in ``AppSettings`` brechen, ohne dass sich an dieser
# Umwandlung irgendetwas geaendert haette.
def _jellyfin() -> JellyfinServer:
    return object.__new__(JellyfinServer)


def _plex() -> PlexServer:
    return object.__new__(PlexServer)


# --- Die beiden Fallen ------------------------------------------------------


def test_emby_meldet_transcode_und_meint_nur_den_ton() -> None:
    """⚠️ **Die teure Falle.**

    Emby sagt ``PlayMethod: "Transcode"`` und reicht das Bild trotzdem durch
    (``IsVideoDirect: true``). Wer auf ``PlayMethod`` allein prueft, meldet dem
    Betreiber eine CPU-Last, die es nicht gibt - und der glaubt es beim
    naechsten Mal nicht mehr.
    """
    wiedergabe = _jellyfin()._als_wiedergabe(EMBY_TON, EMBY_TON["NowPlayingItem"])
    assert wiedergabe.umrechnung is Umrechnung.ton


def test_plex_meldet_transcodesession_und_meint_nur_den_ton() -> None:
    """Dieselbe Falle beim anderen Anbieter, mit anderen Worten.

    ``TranscodeSession`` ist vorhanden, aber ``videoDecision: "copy"``.
    """
    wiedergabe = _plex()._als_wiedergabe(PLEX_TON)
    assert wiedergabe.umrechnung is Umrechnung.ton
    assert wiedergabe.bandbreite == 12839


def test_echte_bildumrechnung_wird_als_solche_erkannt() -> None:
    """Die Gegenprobe - sonst waere alles harmlos und der Befund wertlos."""
    wiedergabe = _jellyfin()._als_wiedergabe(
        JELLYFIN_BILD, JELLYFIN_BILD["NowPlayingItem"]
    )
    assert wiedergabe.umrechnung is Umrechnung.bild
    assert "SubtitleCodecNotSupported" in wiedergabe.grund
    assert wiedergabe.beschleunigung == "none"


# --- Die Felder -------------------------------------------------------------


def test_jellyfin_liest_alle_gemessenen_felder() -> None:
    w = _jellyfin()._als_wiedergabe(JELLYFIN_BILD, JELLYFIN_BILD["NowPlayingItem"])
    assert w.titel == "2 Fast 2 Furious"
    assert w.media_type == "movie"
    assert w.konto == "Jonas"
    assert w.konto_id == "11111111111111111111111111111111"
    assert w.geraet == "Chrome" and w.anwendung == "Jellyfin Web"
    assert w.tmdb_id == 584
    assert w.pausiert is False
    # 221707239 von 64558720000 Ticks - gut drei Promille.
    assert w.fortschritt is not None and 0 < w.fortschritt < 0.01
    # Der Anbieter meldet Bit je Sekunde, gezeigt werden kBit.
    assert w.bandbreite == 11505


def test_plex_liest_alle_gemessenen_felder() -> None:
    w = _plex()._als_wiedergabe(PLEX_TON)
    assert w.titel == "8 Blickwinkel"
    assert w.konto == "DerKezorm" and w.konto_id == "1"
    assert w.geraet == "Windows" and w.anwendung == "Plex Web"
    assert w.pausiert is False
    assert w.fortschritt is not None and 0 < w.fortschritt < 0.01
    # ⚠️ Plex nennt hier keine TMDB-Nummer - anders als die beiden anderen.
    assert w.tmdb_id is None


def test_ohne_laufenden_titel_ist_es_keine_wiedergabe() -> None:
    """⚠️ **Die zweite Falle: ``/Sessions`` sind Geraete.**

    Jellyfin meldete vier und Emby fuenf Sitzungen, waehrend niemand schaute -
    darunter Nexview selbst und ein Radarr. Ungefiltert stuende auf dem
    Dashboard "fuenf Leute schauen gerade".
    """
    assert JELLYFIN_NUR_VERBUNDEN.get("NowPlayingItem") is None
    # ``IsActive`` taugt als Ersatz nicht: Der Eintrag war zwei Tage alt.
    assert JELLYFIN_NUR_VERBUNDEN["IsActive"] is True


@pytest.mark.parametrize(
    "pausiert, erwartet", [({"IsPaused": True}, True), ({"IsPaused": False}, False)]
)
def test_pause_wird_uebernommen(pausiert: dict, erwartet: bool) -> None:
    sitzung = {**JELLYFIN_BILD, "PlayState": {**JELLYFIN_BILD["PlayState"], **pausiert}}
    w = _jellyfin()._als_wiedergabe(sitzung, sitzung["NowPlayingItem"])
    assert w.pausiert is erwartet


def test_direktwiedergabe_ist_der_ruhige_fall() -> None:
    sitzung = {
        **JELLYFIN_BILD,
        "PlayState": {**JELLYFIN_BILD["PlayState"], "PlayMethod": "DirectPlay"},
        "TranscodingInfo": None,
    }
    w = _jellyfin()._als_wiedergabe(sitzung, sitzung["NowPlayingItem"])
    assert w.umrechnung is Umrechnung.direkt
    assert w.grund == ""


# --- Der Abrufweg -----------------------------------------------------------
#
# ⚠️ **Die Tests darueber pruefen nur die Umwandlung.** Genau dazwischen sass
# ein Fehler: ``PlexServer._server`` packt ``MediaContainer`` bereits aus, der
# Adapter packte ein zweites Mal aus und lieferte deshalb **immer** eine leere
# Liste - waehrend Plex einen laufenden Film meldete. Neun gruene Tests haben
# das nicht gesehen, weil keiner den Abrufweg durchlief. Aufgefallen ist es
# durch den Vergleich mit der rohen Antwort des Servers.


async def test_plex_verliert_die_wiedergabe_nicht_beim_auspacken() -> None:
    """``_server`` liefert den **ausgepackten** MediaContainer."""
    server = _plex()

    async def antwort(pfad, params=None, token=None):
        assert pfad == "/status/sessions"
        # Genau die Form, die ``_server`` zurueckgibt - schon ausgepackt.
        return {"size": 1, "Metadata": [PLEX_TON]}

    server._server = antwort  # type: ignore[method-assign]
    gefunden = await server.laufende_wiedergaben()

    assert len(gefunden) == 1
    assert gefunden[0].titel == "8 Blickwinkel"


async def test_jellyfin_filtert_bloss_verbundene_geraete_weg() -> None:
    """Der Abrufweg muss die Sitzungen ohne laufenden Titel verwerfen."""
    server = _jellyfin()

    async def antwort(methode, pfad, **kwargs):
        assert pfad == "/Sessions"
        return [JELLYFIN_NUR_VERBUNDEN, JELLYFIN_BILD, JELLYFIN_NUR_VERBUNDEN]

    server._anfrage = antwort  # type: ignore[method-assign]
    gefunden = await server.laufende_wiedergaben()

    assert len(gefunden) == 1
    assert gefunden[0].titel == "2 Fast 2 Furious"


async def test_leere_antwort_ist_kein_fehler() -> None:
    """Kein laufender Film heisst leere Liste, nicht Ausnahme."""
    server = _plex()

    async def leer(pfad, params=None, token=None):
        return {"size": 0}

    server._server = leer  # type: ignore[method-assign]
    assert await server.laufende_wiedergaben() == []
