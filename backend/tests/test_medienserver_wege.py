"""Die Rueckverbindung zu den Medienservern - alle Wege, auch die schlechten.

⚠️ **Warum ausgerechnet hier gruendlich geprueft werden muss.** Dieser Bereich
hat eine Eigenheit, die ihn gefaehrlicher macht als den Rest: Sein
Versagensfall ist **still**. Eine Verbindung mit falscher Pfad-Zuordnung prueft
sich gruen, steht in der Liste und tut trotzdem nichts - der Medienserver sucht
an einer Stelle, die es bei ihm nicht gibt. Niemand merkt das, bis jemand
zufaellig nachsieht.

Deshalb wird hier gegen **echte HTTP-Server** getestet, nicht gegen Attrappen:
Die interessanten Fehler stecken im Zerlegen der Antworten (Plex antwortet in
XML, Jellyfin in JSON) und in der Frage, welche Antwort welchen Schluss
zulaesst.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import medienserver_verbindung as mv
from app.services import pfad_zuordnung as pz
from app.services.arr import ArrClient

PLEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer>
  <Directory type="movie" key="1" title="Filme">
    <Location id="1" path="/media/Movies"/>
    <Location id="2" path="/media/Movies4K"/>
  </Directory>
  <Directory type="show" key="2" title="Serien">
    <Location id="3" path="/media/TV-Shows"/>
  </Directory>
  <Directory type="artist" key="3" title="Musik">
    <Location id="4" path="/media/Music"/>
  </Directory>
</MediaContainer>"""

JELLYFIN_JSON = [
    {"Name": "Filme", "CollectionType": "movies", "Locations": ["/data/Movies"]},
    {"Name": "Serien", "CollectionType": "tvshows", "Locations": ["/data/TV-Shows"]},
    {"Name": "Musik", "CollectionType": "music", "Locations": ["/data/Music"]},
]


class Server(BaseHTTPRequestHandler):
    """Spielt Plex oder Jellyfin/Emby - je nach eingestellter Laune."""

    laune: dict

    def log_message(self, *_a):
        pass

    def _sende(self, code, koerper, typ):
        roh = koerper.encode() if isinstance(koerper, str) else json.dumps(koerper).encode()
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    def do_GET(self):
        art = self.laune.get("art")
        if self.laune.get("status"):
            self._sende(self.laune["status"], {"m": "nein"}, "application/json")
            return
        if self.path.startswith("/library/sections"):
            self._sende(200, self.laune.get("plex", PLEX_XML), "application/xml")
        elif self.path.startswith("/Library/VirtualFolders"):
            # ⚠️ Jellyfin verlangt die ausgeschriebene MediaBrowser-Zeile.
            if art == "jellyfin" and "MediaBrowser" not in (
                self.headers.get("Authorization") or ""
            ):
                self._sende(401, {"m": "nein"}, "application/json")
                return
            self._sende(200, self.laune.get("json", JELLYFIN_JSON), "application/json")
        else:
            self._sende(404, {"m": "?"}, "application/json")


@pytest.fixture
def medienserver():
    laune: dict = {}
    Server.laune = laune
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = HTTPServer(("127.0.0.1", port), Server)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield laune, f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# Bibliothekspfade holen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plex_pfade_werden_aus_dem_xml_gelesen(medienserver):
    """Plex antwortet in XML - und nur Film- und Serienabschnitte zaehlen.

    ⚠️ Der Musik-Abschnitt darf nicht mitkommen: Er brachte einen Kandidaten
    ins Spiel, zu dem keine Radarr-Wurzel passt, und haette den Vergleich
    verwaessert.
    """
    laune, url = medienserver
    laune["art"] = "plex"
    ergebnis = await pz.server_pfade("plex", url, "token")
    assert ergebnis.hindernis == ""
    assert ergebnis.pfade == ["/media/Movies", "/media/Movies4K", "/media/TV-Shows"]
    assert "/media/Music" not in ergebnis.pfade


@pytest.mark.anyio
async def test_jellyfin_braucht_die_ausgeschriebene_ausweiszeile(medienserver):
    """⚠️ Live gemessen: ``X-Emby-Token`` allein gibt 401 bei Jellyfin.

    Der Server hier weist alles ab, was keine ``MediaBrowser``-Zeile traegt -
    wenn Nexview sie also nicht mitschickt, faellt es hier auf.
    """
    laune, url = medienserver
    laune["art"] = "jellyfin"
    ergebnis = await pz.server_pfade("jellyfin", url, "schluessel")
    assert ergebnis.hindernis == ""
    assert ergebnis.pfade == ["/data/Movies", "/data/TV-Shows"]


@pytest.mark.anyio
async def test_ohne_schluessel_wird_gar_nicht_erst_gefragt(medienserver):
    """"Kein Schluessel" ist etwas anderes als "antwortet nicht"."""
    _laune, url = medienserver
    ergebnis = await pz.server_pfade("emby", url, "")
    assert ergebnis.hindernis == "kein_schluessel"
    assert ergebnis.pfade == []


@pytest.mark.anyio
async def test_abgewiesener_zugang_heisst_unreachable(medienserver):
    """Ein 401 darf nicht als "keine Bibliotheken" durchgehen.

    Der Unterschied entscheidet, ob der Betreiber seinen Schluessel prueft oder
    seine Bibliotheken.
    """
    laune, url = medienserver
    laune["art"] = "emby"
    laune["status"] = 401
    ergebnis = await pz.server_pfade("emby", url, "falsch")
    assert ergebnis.hindernis == "unreachable"


@pytest.mark.anyio
async def test_server_ohne_bibliotheken_sagt_das_auch(medienserver):
    laune, url = medienserver
    laune["art"] = "emby"
    laune["json"] = []
    ergebnis = await pz.server_pfade("emby", url, "k")
    assert ergebnis.hindernis == "keine_pfade"


@pytest.mark.anyio
async def test_geschlossener_port_wird_nicht_zum_absturz():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        tot = s.getsockname()[1]
    ergebnis = await pz.server_pfade("emby", f"http://127.0.0.1:{tot}", "k")
    assert ergebnis.hindernis == "unreachable"


# ---------------------------------------------------------------------------
# Der Eintrag, der in Radarr landet
# ---------------------------------------------------------------------------


SCHEMA = {
    "implementation": "MediaBrowser",
    "supportsOnDownload": True,
    "supportsOnUpgrade": True,
    "supportsOnRename": True,
    "supportsOnMovieDelete": True,
}


def _felder(payload):
    return {f["name"]: f["value"] for f in payload["fields"]}


def test_ohne_zuordnung_bleiben_die_pfadfelder_leer():
    """⚠️ Ein erfundenes ``mapFrom`` ist schlimmer als keines.

    Sehen beide Seiten denselben Pfad, waeren die Felder falsch belegt - und
    Radarr schriebe den Pfad um, obwohl er stimmte.
    """
    payload = mv._payload(
        "jellyfin", "Jellyfin", "http://x:8096", "k", SCHEMA, pz.Zuordnung()
    )
    felder = _felder(payload)
    assert "mapFrom" not in felder
    assert "mapTo" not in felder


def test_mit_zuordnung_stehen_beide_felder_drin():
    payload = mv._payload(
        "jellyfin", "Jellyfin", "http://x:8096", "k", SCHEMA,
        pz.Zuordnung(von="/data", nach="/media"),
    )
    felder = _felder(payload)
    assert felder["mapFrom"] == "/data"
    assert felder["mapTo"] == "/media"


def test_plex_bekommt_authToken_die_anderen_apiKey():
    """Zwei verschiedene Zugaenge - die Verwechslung waere teuer."""
    plex = _felder(mv._payload("plex", "P", "https://x:32400", "t", {"implementation": "PlexServer"}))
    emby = _felder(mv._payload("emby", "E", "http://x:8097", "s", SCHEMA))
    assert plex["authToken"] == "t" and "apiKey" not in plex
    assert emby["apiKey"] == "s" and "authToken" not in emby


def test_adresse_wird_richtig_zerlegt():
    """Wirt, Tor und SSL getrennt - inklusive Standardports."""
    mit_port = _felder(mv._payload("emby", "E", "http://10.0.0.1:8097", "s", SCHEMA))
    assert (mit_port["host"], mit_port["port"], mit_port["useSsl"]) == ("10.0.0.1", 8097, False)
    ohne_port = _felder(mv._payload("emby", "E", "https://medien.example", "s", SCHEMA))
    assert (ohne_port["host"], ohne_port["port"], ohne_port["useSsl"]) == (
        "medien.example", 443, True,
    )


def test_nur_flaggen_die_die_instanz_kennt():
    """⚠️ Radarr fuehrt ``onMovieDelete``, Sonarr ``onSeriesDelete``.

    Wer beide schickt, bekommt vom jeweils anderen eine Abfuhr.
    """
    payload = mv._payload("emby", "E", "http://x:8097", "s", SCHEMA)
    assert payload["onRename"] is True
    assert payload["onMovieDelete"] is True
    assert "onSeriesDelete" not in payload, "Sonarr-Flagge gehoert nicht in einen Radarr-Eintrag"


def test_der_eintrag_traegt_die_handschrift_von_nexview():
    """Am Namen erkennt Nexview spaeter wieder, was von ihm stammt."""
    payload = mv._payload("emby", "Wohnzimmer", "http://x:8097", "s", SCHEMA)
    assert payload["name"].startswith(mv.NAME_VORNE)
    assert "Wohnzimmer" in payload["name"]


PLEX_JSON = {
    "MediaContainer": {
        "Directory": [
            {"type": "movie", "Location": [{"path": "/media/Movies"}]},
            {"type": "show", "Location": [{"path": "/media/TV-Shows"}]},
            {"type": "artist", "Location": [{"path": "/media/Music"}]},
        ]
    }
}


def test_plex_wird_in_beiden_formaten_verstanden():
    """⚠️ JSON **und** XML - der Unterschied ist nicht unsere Entscheidung.

    Plex antwortet auf ``Accept: application/json`` normalerweise in JSON.
    Verlassen kann man sich darauf nicht: Ein Reverse Proxy kann den Kopf
    verschlucken, aeltere Fassungen antworten in XML. Wer nur eines liest,
    meldet im anderen Fall "nicht erreichbar" und schickt den Betreiber auf die
    Suche nach einem Netzproblem, das es nicht gibt.
    """
    erwartet = ["/media/Movies", "/media/TV-Shows"]
    assert pz._plex_pfade(json.dumps(PLEX_JSON)) == erwartet
    assert pz._plex_pfade(PLEX_XML) == ["/media/Movies", "/media/Movies4K", "/media/TV-Shows"]


def test_unlesbare_plex_antwort_gibt_leer_statt_absturz():
    """Weder JSON noch XML - dann eben nichts, aber kein Fehler."""
    assert pz._plex_pfade("kaputt <<<") == []
    assert pz._plex_pfade("{nicht wirklich json}") == []
    assert pz._plex_pfade("") == []
