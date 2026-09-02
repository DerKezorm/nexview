"""Wer laesst sich aus einem Medienserver uebernehmen - und unter welcher Kennung.

⚠️ **Die eine Frage, an der der Nutzer-Import haengt.** Ein Import legt Konto
**und** Verknuepfung in einem Zug an. Die Verknuepfung traegt eine Kennung, und
beim spaeteren Anmelden sucht ``mediaserver_accounts.find_linked`` genau danach.
Passt sie nicht, entsteht ein Konto, in das niemand hineinkommt - und das faellt
erst auf, wenn sich jemand beschwert.

Bei Jellyfin und Emby ist es dieselbe Liste wie ueberall: ``GET /Users`` nennt
die ``Id``, mit der ``AuthenticateByName`` antwortet.

Bei Plex nicht. Dort gibt es **zwei** Listen, und die Verwechslung waere
unsichtbar:

* ``list_server_users`` fragt den Server (``/accounts``). Dessen Nummern
  braucht der Seh-Verlauf, der Eigentuemer steht dort auf der 1.
* ``importierbare_konten`` fragt plex.tv (``/users/account.json`` und
  ``/api/users``). Von dort kommt die Kennung der Anmeldung.

⚠️ **Was diese Datei beweisen kann und was nicht.** Sie prueft gegen Attrappen,
also gegen meine Annahme darueber, wie die Anbieter antworten - nicht gegen die
Anbieter. Die Annahme ist einmal an einer echten Installation gemessen worden
(02.09.2026): Der Eigentuemer und ein geteiltes Konto trugen bei plex.tv genau
die Kennungen, die in ``user_media_server_accounts`` lagen, und
``/api/v2/friends`` antwortete mit 410 Gone. ``tools/plex_kennungen_pruefen.py``
stellt dieselbe Frage jederzeit wieder.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.mediaserver import plextv
from app.services.mediaserver.base import MediaServer, MediaServerError, ServerUser
from app.services.mediaserver.emby import EmbyServer
from app.services.mediaserver.jellyfin import JellyfinServer
from app.services.mediaserver.plex import PlexServer

MASCHINE = "maschine-des-hauses"
FREMDE_MASCHINE = "server-von-jemand-anderem"

# So antwortet plex.tv auf ``/api/users`` - XML, nicht JSON.
#
# ⚠️ Zwei Eintraege mit Absicht: Einer hat Zugriff auf diesen Server, der
# andere ist ein Freund des Kontos ohne Zugriff. Ohne den zweiten wuerde der
# Filter nie geprueft, und eine kaputte Filterzeile fiele nicht auf.
USERS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer>
  <User id="700000002" title="Zweitkonto" username="zweit" email="zweit@beispiel.test">
    <Server machineIdentifier="{MASCHINE}" name="Daheim"/>
  </User>
  <User id="111222333" title="Bekannte" username="bekannte" email="wer@beispiel.test">
    <Server machineIdentifier="{FREMDE_MASCHINE}" name="Woanders"/>
  </User>
  <User id="" title="Ohne Nummer" username="kaputt"/>
</MediaContainer>"""

ACCOUNT_JSON = {"user": {"id": 700000001, "username": "eigner", "email": "eigner@beispiel.test"}}


@pytest.fixture()
def plex_tv(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """plex.tv als Attrappe, die mitschreibt, wonach gefragt wurde."""
    gefragt: dict[str, Any] = {"pfade": [], "als_text": []}

    async def _fake(method: str, path: str, **kw: Any) -> Any:
        gefragt["pfade"].append(path)
        gefragt["als_text"].append(bool(kw.get("als_text")))
        gefragt["base"] = kw.get("base")
        gefragt["token"] = kw.get("token")
        if path == "/users/account.json":
            return gefragt.get("konto", ACCOUNT_JSON)
        if path == "/api/users":
            return gefragt.get("users", USERS_XML)
        raise AssertionError(f"Unerwarteter Pfad: {path}")

    monkeypatch.setattr(plextv, "_request", _fake)
    return gefragt


def _plex_server() -> PlexServer:
    server = PlexServer.__new__(PlexServer)
    server.token = "ein-token"
    server.machine_id = MASCHINE
    server.client_identifier = "nexview-kennung"
    return server


# --- Plex --------------------------------------------------------------------


def test_plex_nimmt_die_kennung_von_plex_tv(plex_tv: dict[str, Any]) -> None:
    """Eigentuemer und geteiltes Konto, beide mit ihrer plex.tv-Nummer."""
    konten = asyncio.run(_plex_server().importierbare_konten())

    assert [k.account_id for k in konten] == ["700000001", "700000002"]
    # Der Eigentuemer kommt aus der anderen Abfrage - er steht in seiner
    # eigenen Freundesliste nicht drin.
    assert plex_tv["pfade"] == ["/users/account.json", "/api/users"]
    # Und die Kontenliste muss unausgewertet kommen, sonst scheitert das XML.
    assert plex_tv["als_text"] == [False, True]


def test_plex_laesst_fremde_server_draussen(plex_tv: dict[str, Any]) -> None:
    """Ein Freund ohne Zugriff auf diesen Server gehoert nicht auf die Liste.

    ⚠️ Ohne den Filter stuenden wildfremde Leute zum Import bereit - die Liste
    von plex.tv fuehrt **alle** Freunde des Kontos, nicht die dieses Servers.
    """
    konten = asyncio.run(_plex_server().importierbare_konten())
    assert "111222333" not in {k.account_id for k in konten}


def test_plex_ueberspringt_eintraege_ohne_nummer(plex_tv: dict[str, Any]) -> None:
    """Ohne Kennung laesst sich keine Verknuepfung bauen - also weglassen."""
    konten = asyncio.run(_plex_server().importierbare_konten())
    assert all(k.account_id for k in konten)


def test_plex_ohne_maschinenkennung_raet_nicht(plex_tv: dict[str, Any]) -> None:
    """Ohne Maschinenkennung liesse sich der Filter nicht anwenden.

    Dann lieber abbrechen als eine ungefilterte Liste ausliefern: Die waere
    stillschweigend zu lang, und niemand saehe es der Liste an.
    """
    server = _plex_server()
    server.machine_id = ""
    with pytest.raises(MediaServerError):
        asyncio.run(server.importierbare_konten())


def test_plex_meldet_unlesbares_xml_als_fehler(plex_tv: dict[str, Any]) -> None:
    """Kaputtes XML darf nicht als leere Liste durchgehen.

    ⚠️ Eine leere Liste hiesse "niemand da" und saehe im Bildschirm genauso aus
    wie ein Server ohne geteilte Konten. Der Unterschied muss sichtbar sein.
    """
    plex_tv["users"] = "<MediaContainer><User"
    with pytest.raises(MediaServerError):
        asyncio.run(_plex_server().importierbare_konten())


def test_plex_kommt_auch_ohne_geteilte_konten_zurecht(plex_tv: dict[str, Any]) -> None:
    """Ein Server, den niemand mitbenutzt: der Eigentuemer allein."""
    plex_tv["users"] = '<?xml version="1.0"?><MediaContainer/>'
    konten = asyncio.run(_plex_server().importierbare_konten())
    assert [k.account_id for k in konten] == ["700000001"]


# --- Jellyfin und Emby -------------------------------------------------------


class _FakeUsers:
    """Jellyfin und Emby antworten auf ``/Users`` in derselben Form."""

    def __init__(self) -> None:
        self.gefragt: list[str] = []

    async def __call__(self, method: str, pfad: str, **kw: Any) -> Any:
        self.gefragt.append(pfad)
        return [
            {"Id": "7f3c2a", "Name": "erste"},
            {"Id": "9b1d4e", "Name": "zweite"},
            {"Id": "", "Name": "ohne Nummer"},
        ]


@pytest.mark.parametrize("klasse", [JellyfinServer, EmbyServer])
def test_jellyfin_und_emby_nehmen_dieselbe_liste(
    klasse: type[MediaServer], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dort faellt beides zusammen, und die Vorgabe der Basis traegt.

    ⚠️ Emby steht hier ausdruecklich mit drin. ``EmbyServer`` erbt von
    ``JellyfinServer``, und beim ersten Hinsehen wirkt es, als koenne Emby
    diese Liste gar nicht - in ``emby.py`` steht keine Zeile dazu. Sie ist
    geerbt, und dieser Test haelt das fest.
    """
    server = klasse.__new__(klasse)
    fake = _FakeUsers()
    monkeypatch.setattr(klasse, "_anfrage", fake, raising=False)

    konten = asyncio.run(server.importierbare_konten())

    assert [k.account_id for k in konten] == ["7f3c2a", "9b1d4e"]
    assert fake.gefragt == ["/Users"]



class _Grundgeruest(MediaServer):
    """Die Pflichtteile eines Adapters, damit sich einer bauen laesst.

    ``MediaServer`` ist abstrakt und verlangt sieben Methoden. Fuer die beiden
    Proben darunter zaehlt keine davon - sie stehen hier, damit Python die
    Klasse ueberhaupt anlegt.
    """

    async def probe(self, *a: Any, **k: Any) -> Any: ...
    async def verify(self, *a: Any, **k: Any) -> Any: ...
    async def begin_login(self, *a: Any, **k: Any) -> Any: ...
    async def poll_login(self, *a: Any, **k: Any) -> Any: ...
    async def account_for_token(self, *a: Any, **k: Any) -> Any: ...
    async def list_servers(self, *a: Any, **k: Any) -> Any: ...
    async def user_has_server_access(self, *a: Any, **k: Any) -> Any: ...


def test_die_vorgabe_der_basis_reicht_weiter() -> None:
    """Wer nichts eigenes hat, fragt ``list_server_users`` - und nichts sonst.

    Die Gegenprobe zum Test darueber: Sie zeigt, dass die Weiterleitung
    wirklich stattfindet und nicht bloss zufaellig dieselbe Antwort herauskommt.
    """

    class _Eigenbau(_Grundgeruest):
        async def list_server_users(self) -> list[ServerUser]:
            return [ServerUser(account_id="abc", username="wer")]

    server = _Eigenbau.__new__(_Eigenbau)
    assert asyncio.run(server.importierbare_konten())[0].account_id == "abc"


def test_ohne_eigene_liste_bleibt_es_ein_fehler() -> None:
    """Und ein Anbieter, der es gar nicht kann, sagt das - still ist es nicht.

    ⚠️ Wichtig fuer die Oberflaeche: "kann ich nicht" und "niemand da" duerfen
    nicht dasselbe aussehen. Sonst zeigt der Import bei einem Anbieter ohne
    Kontenliste eine leere Tabelle, und der Betreiber sucht den Fehler bei sich.
    """

    class _Kannnicht(_Grundgeruest):
        pass

    server = _Kannnicht.__new__(_Kannnicht)
    with pytest.raises(NotImplementedError):
        asyncio.run(server.importierbare_konten())
