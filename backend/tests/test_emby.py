"""Emby als dritter Medienserver.

Emby erbt fast alles von Jellyfin - die Messung gegen einen echten Server
(4.9.5.0) hat ergeben, dass beide dieselbe Sprache sprechen. Geprueft wird
deshalb genau das, was eine Ableitung kaputtmachen kann: dass der Anbieter
sich richtig ausweist, dass die Bibliothekskennungen sich **nicht** mit denen
von Jellyfin ueberschneiden, und dass die Meldungen den richtigen Namen
tragen.
"""

from __future__ import annotations

from app.services.mediaserver import PROVIDERS
from app.services.mediaserver.emby import EmbyServer
from app.services.mediaserver.jellyfin import JellyfinServer, _als_werk


class _Stand:
    """Das Wenige, was der Adapter aus den Einstellungen liest."""

    mediaserver_url = "http://beispiel:8097"
    mediaserver_token = "token"
    mediaserver_machine_id = "maschine"
    mediaserver_client_identifier = "nexview-test"


# --- Anmeldung am Bausatz ---------------------------------------------------


def test_emby_ist_registriert() -> None:
    assert PROVIDERS["emby"] is EmbyServer
    assert EmbyServer.label == "Emby"


def test_emby_meldet_sich_mit_passwort_an() -> None:
    """Wie Jellyfin - es gibt keinen Vermittler wie plex.tv."""
    assert EmbyServer.login_kind == "password"


def test_emby_kennt_keine_mailadresse() -> None:
    """Nachgemessen: Die Kontenliste fuehrt kein solches Feld.

    Daran haengt eine Entscheidung, nicht nur ein Merkmal: Ohne Adresse darf
    die Anmeldung ueber Emby **kein Konto anlegen**, sonst bekaeme jemand mit
    Einladung still ein zweites Konto ohne Passwort und ohne Weg zurueck.
    """
    assert EmbyServer.knows_email is False


def test_emby_hat_keine_merkliste() -> None:
    assert EmbyServer.supports_watchlist() is False


# --- Die Kennung, an der alles haengt ---------------------------------------


def test_kennungen_kollidieren_nicht_mit_jellyfin() -> None:
    """Derselbe Titel auf zwei Servern muss zwei Zeilen ergeben.

    Die Bibliothekstabelle schluesselt ueber ``(provider, guid)``. Truege Emby
    denselben ``guid`` wie Jellyfin, waere im Parallelbetrieb nicht mehr zu
    unterscheiden, welcher Server einen Titel wirklich hat - und die zweite
    Zeile wuerde die erste ueberschreiben.
    """
    eintrag = {"Name": "Alien", "Id": "42", "ProviderIds": {"Tmdb": "348"}}
    aus_jellyfin = _als_werk(eintrag, "movie", None, "jellyfin")
    aus_emby = _als_werk(eintrag, "movie", None, "emby")

    assert aus_jellyfin is not None and aus_emby is not None
    assert aus_jellyfin.guid == "jellyfin://42"
    assert aus_emby.guid == "emby://42"
    assert aus_jellyfin.guid != aus_emby.guid
    # Die Titelangaben bleiben gleich - unterschieden wird nur die Herkunft.
    assert aus_jellyfin.tmdb_id == aus_emby.tmdb_id == 348


def test_jahr_wird_uebernommen() -> None:
    """Ohne Jahr faellt der Titel-Rueckfall der Bibliothek in sich zusammen.

    ``vorhandene_kennungen`` vergleicht Titel **und Jahr**, damit "The Lion
    King" von 1994 nicht dasselbe ist wie das Remake von 2019. Beim Messen
    gegen Emby kam das Feld zunaechst nicht mit, weil es nicht ausdruecklich
    angefordert wurde - jeder Eintrag hatte ``year=None``.
    """
    werk = _als_werk(
        {"Name": "The Lion King", "Id": "7", "ProductionYear": 1994}, "movie", None, "emby"
    )
    assert werk is not None
    assert werk.year == 1994


def test_jahr_wird_angefordert() -> None:
    """Der Feldkatalog muss ``ProductionYear`` nennen - sonst kommt es nicht mit.

    Ein Test am Quelltext statt am Ergebnis, weil der Fehler genau hier sass:
    Die Uebersetzung konnte das Jahr immer, es wurde nur nie mitgeschickt.
    """
    import inspect

    quelle = inspect.getsource(JellyfinServer._titel_lesen)
    assert "ProductionYear" in quelle


# --- Die Meldungen ----------------------------------------------------------


def test_meldungen_nennen_emby_nicht_jellyfin() -> None:
    """Ein Emby-Server darf sich beim Nutzer nicht als Jellyfin beschweren."""
    server = EmbyServer(_Stand())
    server.base_url = ""
    import asyncio

    from app.services.mediaserver.base import MediaServerError

    try:
        asyncio.run(server._anfrage("GET", "/System/Info"))
    except MediaServerError as fehler:
        assert "Emby" in fehler.message
        assert "Jellyfin" not in fehler.message
    else:  # pragma: no cover - ohne Adresse muss es fehlschlagen
        raise AssertionError("Ohne Adresse haette das fehlschlagen muessen")


def test_geraetekennung_je_zweck_verschieden() -> None:
    """Von Jellyfin geerbt, und hier genauso wichtig.

    Emby fuehrt Zugaenge je Geraet. Meldet sich jemand unter derselben Kennung
    erneut an, verfaellt der vorherige Zugang - auch der eines *anderen*
    Kontos. Genau das hat bei Jellyfin dem Administrator die Serververbindung
    ausgeknipst.
    """
    server = EmbyServer(_Stand())
    assert server.geraet("server") != server.geraet("user-7")
    assert server.geraet("user-7") == server.geraet("user-7")


def test_pin_verlaesst_den_adapter_nicht() -> None:
    """Embys Profil-PIN steht im Klartext in ``/Users`` - sie darf nicht weiter.

    ``Configuration.ProfilePin`` kommt bei jeder Kontenabfrage ungefragt mit.
    Der Adapter übersetzt Konten in ``ServerUser``, und dieser Typ hat für so
    etwas keinen Platz - das ist die Absicherung. Der Test hält fest, dass
    das so bleibt: Wer die Übersetzung einmal um ein "alles Übrige"-Feld
    erweitert, hätte die PINs aller Konten in der Datenbank.
    """
    from dataclasses import fields

    from app.services.mediaserver.base import ExternalAccount, ServerUser

    erlaubt_user = {f.name for f in fields(ServerUser)}
    erlaubt_konto = {f.name for f in fields(ExternalAccount)}
    verboten = {"configuration", "profile_pin", "pin", "policy", "raw", "extra"}

    assert not (erlaubt_user & verboten)
    assert not (erlaubt_konto & verboten)
