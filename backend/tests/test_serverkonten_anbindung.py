"""Die Server-Anbindungen fuer Einladungen: Bibliotheken lesen, Konten anlegen, freigeben.

Gemockt wird auf HTTP-Ebene, wie in ``test_mediaserver_plextv.py``: Es geht
darum, welche Adressen, Felder und Reihenfolgen wirklich an den Server gehen.
Die Formen folgen den Messungen vom 12.09.2026 (Jellyfin 10.11.11 und Emby
4.9.5.0 samt ihrer API-Beschreibungen, Plex 1.43.3) und fuer die plex.tv-Wege
python-plexapi.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx
import pytest

from app.services.mediaserver import MediaServerError, base
from app.services.mediaserver.emby import EmbyServer
from app.services.mediaserver.jellyfin import JellyfinServer
from app.services.mediaserver.plex import PlexServer

Antwort = Callable[[httpx.Request], httpx.Response]


class _Stand:
    """Das Wenige, was die Adapter aus den Einstellungen lesen."""

    mediaserver_url = "http://medien.example.com:8096"
    mediaserver_token = "admin-token"
    mediaserver_machine_id = "maschine-1"
    mediaserver_client_identifier = "nexview-test"


@pytest.fixture
def leitung(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[list[httpx.Request], dict[tuple[str, str], Antwort]]]:
    """Ein Server, der nur auf eingetragene Wege antwortet und alles mitschreibt."""
    gesehen: list[httpx.Request] = []
    wege: dict[tuple[str, str], Antwort] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request)
        antwort = wege.get((request.method, request.url.path))
        return antwort(request) if antwort else httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(base, "_client", client)
    yield gesehen, wege


def _json(daten: object, status: int = 200) -> Antwort:
    return lambda _request: httpx.Response(status, json=daten)


def _xml(text: str) -> Antwort:
    return lambda _request: httpx.Response(
        200, text=text, headers={"Content-Type": "application/xml"}
    )


def _leer(status: int = 204) -> Antwort:
    return lambda _request: httpx.Response(status)


def _koerper(request: httpx.Request) -> dict:
    return json.loads(request.content)


POLICY = {
    "IsAdministrator": False,
    "IsHidden": True,
    "IsDisabled": False,
    "EnableAllFolders": True,
    "EnabledFolders": [],
    "EnableRemoteAccess": True,
    "AuthenticationProviderId": "Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider",
    "PasswordResetProviderId": "Jellyfin.Server.Implementations.Users.DefaultPasswordResetProvider",
}

FILME = "f1" * 16
SERIEN = "a2" * 16
MUSIK = "c3" * 16

JELLYFIN_BIBLIOTHEKEN = [
    {"Name": "Filme", "CollectionType": "movies", "ItemId": FILME},
    {"Name": "Serien", "CollectionType": "tvshows", "ItemId": SERIEN},
    {"Name": "Musik", "CollectionType": "music", "ItemId": MUSIK},
]


def _jellyfin_konto(wege: dict, *, sichtbar: list[str], vorhanden: tuple[str, ...] = ()) -> None:
    wege[("GET", "/Users")] = _json(
        [{"Id": f"alt-{nummer}", "Name": name} for nummer, name in enumerate(vorhanden)]
    )
    wege[("POST", "/Users/New")] = _json({"Id": "neu-1", "Name": "alex"})
    wege[("GET", "/Users/neu-1")] = _json({"Id": "neu-1", "Policy": POLICY})
    wege[("POST", "/Users/neu-1/Policy")] = _leer()
    wege[("POST", "/Users/Password")] = _leer()
    wege[("GET", "/Library/VirtualFolders")] = _json(JELLYFIN_BIBLIOTHEKEN)
    wege[("GET", "/UserViews")] = _json({"Items": [{"Name": name} for name in sichtbar]})


# --- Faehigkeiten -----------------------------------------------------------


def test_jellyfin_und_emby_legen_an_plex_gibt_nur_frei() -> None:
    """Die beiden Wege schliessen sich aus, sonst wuesste der Aufrufer nicht, welchen er geht."""
    assert JellyfinServer.legt_konten_an() and not JellyfinServer.gibt_frei()
    assert EmbyServer.legt_konten_an() and not EmbyServer.gibt_frei()
    assert PlexServer.gibt_frei() and not PlexServer.legt_konten_an()


# --- Jellyfin ---------------------------------------------------------------


async def test_jellyfin_liefert_bibliotheken_mit_der_kennung_fuer_enabledfolders(leitung) -> None:
    _gesehen, wege = leitung
    wege[("GET", "/Library/VirtualFolders")] = _json(JELLYFIN_BIBLIOTHEKEN)

    bibliotheken = await JellyfinServer(_Stand()).bibliotheken()

    assert [(b.kennung, b.name, b.art) for b in bibliotheken] == [
        (FILME, "Filme", "movies"),
        (SERIEN, "Serien", "tvshows"),
        (MUSIK, "Musik", "music"),
    ]


async def test_jellyfin_prueft_namen_ohne_gross_und_kleinschreibung(leitung) -> None:
    _gesehen, wege = leitung
    wege[("GET", "/Users")] = _json([{"Id": "1", "Name": "Alex"}])
    server = JellyfinServer(_Stand())

    assert await server.name_vergeben(" alex ") is True
    assert await server.name_vergeben("sam") is False


async def test_jellyfin_legt_an_und_vergibt_nur_die_gewaehlten_bibliotheken(leitung) -> None:
    gesehen, wege = leitung
    _jellyfin_konto(wege, sichtbar=["Filme", "Serien"])

    nummer = await JellyfinServer(_Stand()).konto_anlegen("alex", "geheim-123", [FILME, SERIEN])

    assert nummer == "neu-1"
    anlegen = _koerper(next(r for r in gesehen if r.url.path == "/Users/New"))
    assert anlegen["Name"] == "alex"
    # Beim Anlegen ein Passwort, das niemand kennt. Das echte kommt erst ans gesperrte Konto.
    assert anlegen["Password"] and anlegen["Password"] != "geheim-123"

    schritte = [r for r in gesehen if r.method == "POST" and r.url.path != "/Users/New"]
    assert [r.url.path for r in schritte] == [
        "/Users/neu-1/Policy",
        "/Users/Password",
        "/Users/neu-1/Policy",
    ]
    gesperrt, passwort, frei = schritte
    assert _koerper(gesperrt)["IsDisabled"] is True
    assert _koerper(gesperrt)["EnableAllFolders"] is False
    assert _koerper(gesperrt)["EnabledFolders"] == []
    assert passwort.url.params["userId"] == "neu-1"
    assert _koerper(passwort) == {"NewPw": "geheim-123", "ResetPassword": False}

    rechte = _koerper(frei)
    assert rechte["EnableAllFolders"] is False
    assert rechte["EnabledFolders"] == [FILME, SERIEN]
    assert rechte["IsAdministrator"] is False
    assert rechte["IsDisabled"] is False
    # Entscheidung vom 12.09.2026: sichtbar auf dem Anmeldebildschirm.
    assert rechte["IsHidden"] is False
    # Die Pflichtfelder der Policy gehen unveraendert zurueck.
    assert rechte["AuthenticationProviderId"] == POLICY["AuthenticationProviderId"]
    assert rechte["PasswordResetProviderId"] == POLICY["PasswordResetProviderId"]
    assert rechte["EnableRemoteAccess"] is True

    ansicht = next(r for r in gesehen if r.url.path == "/UserViews")
    assert ansicht.url.params["userId"] == "neu-1"


@pytest.mark.parametrize(
    ("sichtbar", "gewaehlt"),
    [
        # Eine Kennung der falschen Sorte: Das Konto sieht nichts.
        pytest.param([], [FILME, SERIEN], id="sieht-nichts"),
        pytest.param(["Filme"], [FILME, SERIEN], id="sieht-zu-wenig"),
        # Ein Server, der die Liste uebergeht: Das Konto sieht alles.
        pytest.param(["Filme", "Serien", "Musik"], [FILME, SERIEN], id="sieht-zu-viel"),
        # Die Bibliothek wurde nach dem Einladen geloescht.
        pytest.param(["Filme"], [FILME, "d4" * 16], id="gibt-es-nicht-mehr"),
    ],
)
async def test_jellyfin_meldet_wenn_das_konto_andere_bibliotheken_sieht(
    leitung, sichtbar, gewaehlt
) -> None:
    gesehen, wege = leitung
    _jellyfin_konto(wege, sichtbar=sichtbar)

    with pytest.raises(MediaServerError) as fehler:
        await JellyfinServer(_Stand()).konto_anlegen("alex", "geheim-123", gewaehlt)

    assert fehler.value.code == "mediaserver_libraries_mismatch"
    # Das Konto steht schon und darf nicht noch einmal angelegt werden.
    assert fehler.value.zahlen["konto"] == "neu-1"
    # Lieber gesperrt als mit den falschen Bibliotheken offen.
    zuletzt = [_koerper(r) for r in gesehen if r.url.path == "/Users/neu-1/Policy"][-1]
    assert zuletzt["IsDisabled"] is True
    assert zuletzt["EnabledFolders"] == []


async def test_jellyfin_legt_bei_vergebenem_namen_nichts_an(leitung) -> None:
    """Nie ein fremdes Konto uebernehmen, auch nicht eines, das nur anders geschrieben ist."""
    gesehen, wege = leitung
    _jellyfin_konto(wege, sichtbar=["Filme"], vorhanden=("Alex",))

    with pytest.raises(MediaServerError) as fehler:
        await JellyfinServer(_Stand()).konto_anlegen("alex", "geheim-123", [FILME])

    assert fehler.value.code == "mediaserver_name_taken"
    assert "konto" not in fehler.value.zahlen
    assert not any(r.method == "POST" for r in gesehen)


async def test_jellyfin_setzt_ein_angefangenes_konto_fort_statt_ein_zweites_anzulegen(
    leitung,
) -> None:
    """Der Name gehoert jetzt dem eigenen, halb fertigen Konto und zaehlt nicht als vergeben."""
    gesehen, wege = leitung
    _jellyfin_konto(wege, sichtbar=["Filme"], vorhanden=("alex",))

    nummer = await JellyfinServer(_Stand()).konto_anlegen(
        "alex", "geheim-123", [FILME], konto="neu-1"
    )

    assert nummer == "neu-1"
    assert not any(r.url.path == "/Users/New" for r in gesehen)
    assert [r.url.path for r in gesehen if r.method == "POST"] == [
        "/Users/neu-1/Policy",
        "/Users/Password",
        "/Users/neu-1/Policy",
    ]


async def test_jellyfin_nennt_das_konto_wenn_die_rechte_scheitern(leitung) -> None:
    gesehen, wege = leitung
    _jellyfin_konto(wege, sichtbar=["Filme"])
    wege[("POST", "/Users/neu-1/Policy")] = _leer(500)

    with pytest.raises(MediaServerError) as fehler:
        await JellyfinServer(_Stand()).konto_anlegen("alex", "geheim-123", [FILME])

    assert fehler.value.zahlen["konto"] == "neu-1"
    assert sum(1 for r in gesehen if r.url.path == "/Users/New") == 1


# --- Emby -------------------------------------------------------------------

EMBY_BIBLIOTHEKEN = [
    {"Name": "Filme", "CollectionType": "movies", "Guid": "g-filme", "Id": "3", "ItemId": "3"},
    {"Name": "Serien", "CollectionType": "tvshows", "Guid": "g-serien", "Id": "4", "ItemId": "4"},
]


def _emby_konto(wege: dict, *, passwort_status: int = 204) -> None:
    wege[("GET", "/Users")] = _json([])
    wege[("POST", "/Users/New")] = _json({"Id": "neu-2", "Name": "alex"})
    wege[("GET", "/Users/neu-2")] = _json({"Id": "neu-2", "Policy": POLICY})
    wege[("POST", "/Users/neu-2/Policy")] = _leer()
    wege[("POST", "/Users/neu-2/Password")] = _leer(passwort_status)
    wege[("GET", "/Library/VirtualFolders")] = _json(EMBY_BIBLIOTHEKEN)
    wege[("GET", "/Users/neu-2/Views")] = _json({"Items": [{"Name": "Filme"}]})


async def test_emby_sperrt_das_konto_bis_das_passwort_steht(leitung) -> None:
    """Emby nimmt beim Anlegen kein Passwort. Ohne Sperre stuende es kurz offen."""
    gesehen, wege = leitung
    _emby_konto(wege)

    nummer = await EmbyServer(_Stand()).konto_anlegen("alex", "geheim-123", ["g-filme"])

    assert nummer == "neu-2"
    anlegen = next(r for r in gesehen if r.url.path == "/Users/New")
    assert _koerper(anlegen) == {"Name": "alex"}

    schritte = [
        (r.url.path, _koerper(r)) for r in gesehen if r.method == "POST" and r.url.path != "/Users/New"
    ]
    assert [pfad for pfad, _ in schritte] == [
        "/Users/neu-2/Policy",
        "/Users/neu-2/Password",
        "/Users/neu-2/Policy",
    ]
    gesperrt, passwort, frei = (koerper for _, koerper in schritte)
    assert gesperrt["IsDisabled"] is True
    assert gesperrt["EnabledFolders"] == []
    assert passwort == {"Id": "neu-2", "NewPw": "geheim-123", "ResetPassword": False}
    assert frei["IsDisabled"] is False
    assert frei["EnabledFolders"] == ["g-filme"]
    assert any(r.url.path == "/Users/neu-2/Views" for r in gesehen)


async def test_emby_bleibt_gesperrt_wenn_das_passwort_scheitert(leitung) -> None:
    gesehen, wege = leitung
    _emby_konto(wege, passwort_status=500)

    with pytest.raises(MediaServerError) as fehler:
        await EmbyServer(_Stand()).konto_anlegen("alex", "geheim-123", ["g-filme"])

    assert fehler.value.zahlen["konto"] == "neu-2"
    rechte = [_koerper(r) for r in gesehen if r.url.path == "/Users/neu-2/Policy"]
    assert [eintrag["IsDisabled"] for eintrag in rechte] == [True]


async def test_emby_liefert_bibliotheken_mit_seiner_kennung(leitung) -> None:
    _gesehen, wege = leitung
    wege[("GET", "/Library/VirtualFolders")] = _json(EMBY_BIBLIOTHEKEN)

    bibliotheken = await EmbyServer(_Stand()).bibliotheken()

    assert [b.kennung for b in bibliotheken] == ["g-filme", "g-serien"]


# --- Plex -------------------------------------------------------------------

PLEX_SERVER = (
    '<MediaContainer size="1"><Server name="Wohnzimmer" machineIdentifier="maschine-1">'
    '<Section id="11" key="1" type="movie" title="Filme"/>'
    '<Section id="12" key="2" type="show" title="Serien"/>'
    "</Server></MediaContainer>"
)


async def test_plex_bibliotheken_tragen_die_nummer_von_plextv(leitung) -> None:
    """Gemessen: ``id`` bei plex.tv ist nicht ``key`` auf dem Server."""
    gesehen, wege = leitung
    wege[("GET", "/api/servers/maschine-1")] = _xml(PLEX_SERVER)

    bibliotheken = await PlexServer(_Stand()).bibliotheken()

    assert [(b.kennung, b.name, b.art) for b in bibliotheken] == [
        ("11", "Filme", "movie"),
        ("12", "Serien", "show"),
    ]
    assert gesehen[0].url.host == "plex.tv"
    assert gesehen[0].headers["X-Plex-Token"] == "admin-token"


async def test_plex_gibt_nur_bibliotheken_frei(leitung) -> None:
    gesehen, wege = leitung
    wege[("POST", "/api/servers/maschine-1/shared_servers")] = _xml("<MediaContainer/>")

    await PlexServer(_Stand()).freigeben("gast@example.com", ["11", "12"])

    anfrage = gesehen[0]
    assert anfrage.url.host == "plex.tv"
    assert anfrage.headers["X-Plex-Token"] == "admin-token"
    koerper = _koerper(anfrage)
    assert koerper["server_id"] == "maschine-1"
    assert koerper["shared_server"] == {
        "library_section_ids": [11, 12],
        "invited_email": "gast@example.com",
    }
    assert koerper["sharing_settings"]["allowSync"] == "0"
    assert koerper["sharing_settings"]["allowCameraUpload"] == "0"
    assert koerper["sharing_settings"]["allowChannels"] == "0"


async def test_plex_findet_eine_bestehende_freigabe(leitung) -> None:
    _gesehen, wege = leitung
    wege[("GET", "/api/servers/maschine-1/shared_servers")] = _xml(
        '<MediaContainer><SharedServer id="900" userID="4711" machineIdentifier="maschine-1">'
        '<Section id="11" key="1" shared="1" title="Filme" type="movie"/>'
        "</SharedServer></MediaContainer>"
    )
    server = PlexServer(_Stand())

    assert await server.hat_freigabe("4711") is True
    assert await server.hat_freigabe("99") is False


async def test_plex_nimmt_die_einladung_mit_dem_token_der_person_an(leitung) -> None:
    gesehen, wege = leitung
    wege[("GET", "/api/invites/requests")] = _xml(
        '<MediaContainer size="2">'
        '<Invite id="555" friend="1" home="0" server="1"><Server machineIdentifier="andere"/></Invite>'
        '<Invite id="556" friend="1" home="0" server="1"><Server machineIdentifier="maschine-1"/></Invite>'
        "</MediaContainer>"
    )
    wege[("PUT", "/api/invites/requests/556")] = _xml("<MediaContainer/>")

    angenommen = await PlexServer(_Stand()).einladung_annehmen("gast-token")

    assert angenommen is True
    annahme = next(r for r in gesehen if r.method == "PUT")
    assert annahme.url.path == "/api/invites/requests/556"
    assert dict(annahme.url.params) == {"friend": "1", "home": "0", "server": "1"}
    assert annahme.headers["X-Plex-Token"] == "gast-token"
    assert all(r.headers["X-Plex-Token"] == "gast-token" for r in gesehen)


async def test_plex_ohne_offene_einladung_nimmt_nichts_an(leitung) -> None:
    gesehen, wege = leitung
    wege[("GET", "/api/invites/requests")] = _xml('<MediaContainer size="0"/>')

    assert await PlexServer(_Stand()).einladung_annehmen("gast-token") is False
    assert not any(r.method == "PUT" for r in gesehen)
