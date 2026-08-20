"""Die Merkliste ansehen - und daraus anfragen wie ueberall sonst.

Gemockt wird an der **Abstraktions-Grenze**: ein erfundener ``MediaServer``
liefert die Merkliste, TMDB wird durch feste Titel ersetzt. Geprueft wird
damit, was Nexview daraus macht.

Was hier bewusst **nicht** steht: Kontingent, Freigabe, Sperrliste. Die
Merkliste hat davon keine eigene Fassung - sie schickt den Klick durch
``/api/requests``, und dort ist das alles laengst geprueft.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, Role, User, WatchlistLookup
from app.schemas_media import MediaItem
from app.services import watchlist
from app.services.mediaserver import (
    ExternalAccount,
    LoginChallenge,
    MediaServer,
    MediaServerError,
    ServerCandidate,
)
from app.services.mediaserver.base import WatchlistItem

from .conftest import auth_headers, create_user

FILM = WatchlistItem(
    guid="plex://movie/1000",
    media_type="movie",
    title="Der Testfilm",
    year=2024,
    tmdb_id=550,
    rating_key="1000",
)
SERIE = WatchlistItem(
    guid="plex://show/2000",
    media_type="tv",
    title="Die Testserie",
    year=2023,
    tmdb_id=1399,
    rating_key="2000",
)
# Kommt ohne TMDB-Nummer aus der Liste - die muss nachgeschlagen werden.
OHNE_NUMMER = WatchlistItem(
    guid="plex://movie/3000", media_type="movie", title="Nachzuschlagen", rating_key="3000"
)


class FakeMerkliste(MediaServer):
    """Media-Server, dessen Merkliste der Test vorgibt."""

    provider = "plex"
    label = "Plex"

    def __init__(self) -> None:
        self.werke: list[WatchlistItem] = [FILM]
        self.abgelehnt = False
        # Wie oft wurde einzeln nachgeschlagen? Daran haengt der Nachweis,
        # dass der Zwischenspeicher wirkt.
        self.nachgeschlagen = 0

    async def verify(self) -> dict:
        return {"name": "Testserver", "version": "1.0", "machine_id": "maschine-1"}

    async def list_servers(self, provider_token: str) -> list[ServerCandidate]:
        return []

    async def probe(self, url: str, provider_token: str) -> bool:
        return True

    async def begin_login(self) -> LoginChallenge:
        return LoginChallenge(ref="1", code="ABCD", auth_url="https://app.plex.tv/auth")

    async def poll_login(self, ref: str, code: str = "") -> str | None:
        return "anbieter-token"

    async def account_for_token(self, provider_token: str) -> ExternalAccount:
        return ExternalAccount(provider="plex", account_id="4711", username="Testkonto")

    async def user_has_server_access(self, provider_token: str) -> bool:
        return True

    async def watchlist(self, provider_token: str) -> list[WatchlistItem]:
        if self.abgelehnt:
            raise MediaServerError("Plex hat die Anmeldung nicht akzeptiert.", 401)
        return list(self.werke)

    async def watchlist_ids(
        self, provider_token: str, items: list[WatchlistItem]
    ) -> list[WatchlistItem]:
        self.nachgeschlagen += len(items)
        # Der Anbieter kennt fuer diesen einen Titel die Nummer 99.
        return [
            WatchlistItem(
                guid=w.guid,
                media_type=w.media_type,
                title=w.title,
                year=w.year,
                tmdb_id=99,
                rating_key=w.rating_key,
            )
            for w in items
        ]


@pytest.fixture
def merkliste(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMerkliste]:
    server = FakeMerkliste()
    monkeypatch.setattr(watchlist, "get_media_server", lambda _settings: server)

    async def detail(_db, _settings, media_type: str, tmdb_id: int) -> MediaItem:
        return MediaItem(
            media_type=MediaType(media_type),
            tmdb_id=tmdb_id,
            title=f"Titel {tmdb_id}",
            release_date="2024-01-01",
        )

    monkeypatch.setattr(watchlist.media, "detail", detail)
    yield server


def einstellen(**werte: object) -> None:
    from app.services import settings_service

    with SessionLocal() as session:
        settings_service.save_settings(session, {k: str(v) for k, v in werte.items()})


def freischalten(**abweichend: object) -> None:
    werte: dict[str, object] = {
        "mediaserver_provider": "plex",
        "mediaserver_machine_id": "maschine-1",
        "mediaserver_name": "Wohnzimmer",
        "mediaserver_url": "http://127.0.0.1:32400",
        "mediaserver_token": "admin-token",
        "watchlist_enabled": "on",
    }
    werte.update(abweichend)
    einstellen(**werte)


def admin_mit_zugang(**extra: object) -> None:
    """Dem Administrator ein verknuepftes Plex-Konto und einen Zugang geben."""
    from app.crypto import encrypt

    with SessionLocal() as session:
        admin = session.query(User).filter(User.role == Role.admin).one()
        admin.mediaserver_provider = "plex"
        admin.mediaserver_account_id = "4711"
        admin.watchlist_token = encrypt("nutzer-token")
        for feld, wert in extra.items():
            setattr(admin, feld, wert)
        session.commit()


# --------------------------------------------------------------------------
# Der Schalter
# --------------------------------------------------------------------------


def test_ohne_freischaltung_gibt_es_die_merkliste_nicht(
    arr_client: TestClient, merkliste: FakeMerkliste
) -> None:
    freischalten(watchlist_enabled="off")
    admin_mit_zugang()

    antwort = arr_client.get("/api/watchlist/plex")
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "watchlist_disabled"


def test_ohne_zugang_kommt_eine_handlungsanweisung(
    arr_client: TestClient, merkliste: FakeMerkliste
) -> None:
    """Kein Token heisst "einmal anmelden" - und muss als solches ankommen."""
    freischalten()
    admin_mit_zugang(watchlist_token=None)

    antwort = arr_client.get("/api/watchlist/plex")
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "watchlist_not_connected"


def test_abgelehntes_token_sagt_das_auch(
    arr_client: TestClient, merkliste: FakeMerkliste
) -> None:
    freischalten()
    admin_mit_zugang()
    merkliste.abgelehnt = True

    antwort = arr_client.get("/api/watchlist/plex")
    assert antwort.status_code == 409
    # Die Oberflaeche haengt an dieser Kennung: Sie bietet daraufhin die
    # Anmeldung an, statt bloss einen Fehler anzuzeigen.
    assert antwort.json()["detail"]["code"] == "watchlist_token_invalid"


# --------------------------------------------------------------------------
# Die Liste
# --------------------------------------------------------------------------


def test_merkliste_kommt_nach_medienart_getrennt(
    arr_client: TestClient, merkliste: FakeMerkliste
) -> None:
    freischalten()
    admin_mit_zugang()
    merkliste.werke = [FILM, SERIE]

    daten = arr_client.get("/api/watchlist/plex").json()
    assert [f["tmdb_id"] for f in daten["movies"]] == [550]
    assert [s["tmdb_id"] for s in daten["series"]] == [1399]
    assert daten["unmatched"] == 0
    # Die Abzeichen kommen aus derselben Quelle wie im Katalog.
    assert daten["movies"][0]["status"] == "not_requested"


def test_zuordnung_wird_nur_einmal_nachgeschlagen(
    arr_client: TestClient, merkliste: FakeMerkliste
) -> None:
    """Der Zwischenspeicher ist der einzige Rest des alten Apparats.

    Ohne ihn kostet jedes Oeffnen der Seite eine Abfrage **je Titel** - Plex
    nennt in der Liste selbst keine fremden Kennungen.
    """
    freischalten()
    admin_mit_zugang()
    merkliste.werke = [OHNE_NUMMER]

    erste = arr_client.get("/api/watchlist/plex").json()
    assert [f["tmdb_id"] for f in erste["movies"]] == [99]
    assert merkliste.nachgeschlagen == 1

    zweite = arr_client.get("/api/watchlist/plex").json()
    assert [f["tmdb_id"] for f in zweite["movies"]] == [99]
    # Beim zweiten Mal reicht die gemerkte Zuordnung.
    assert merkliste.nachgeschlagen == 1

    with SessionLocal() as session:
        eintrag = session.query(WatchlistLookup).one()
        assert eintrag.guid == OHNE_NUMMER.guid
        assert eintrag.tmdb_id == 99


def test_nicht_zuzuordnende_titel_werden_gezaehlt(
    arr_client: TestClient, merkliste: FakeMerkliste, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sie verschwinden nicht stillschweigend."""
    freischalten()
    admin_mit_zugang()
    merkliste.werke = [OHNE_NUMMER]

    async def nichts(self, provider_token, items):  # noqa: ANN001
        self.nachgeschlagen += len(items)
        return list(items)  # unveraendert, also weiterhin ohne Nummer

    monkeypatch.setattr(FakeMerkliste, "watchlist_ids", nichts)

    daten = arr_client.get("/api/watchlist/plex").json()
    assert daten["movies"] == []
    assert daten["unmatched"] == 1


# --------------------------------------------------------------------------
# Anfragen
# --------------------------------------------------------------------------


def _demo_titel(client: TestClient, index: int = 0) -> dict:
    """Ein Titel, den es im Demo-Modus wirklich gibt (ohne TMDB-Key)."""
    return client.get("/api/discover/movie").json()["items"][index]


def _anfragen(client: TestClient, item: dict, **extra: object):
    """Als gewoehnlicher Benutzer anfragen.

    Nicht als Administrator: Der hat Auto-Freigabe, und die Anfrage ginge
    sofort an ein Radarr, das es im Test nicht gibt. So bleibt sie auf
    "wartet auf Freigabe" stehen - fuer die Herkunftsfrage genau richtig.
    """
    create_user(client, "kim")
    daten = {
        "media_type": item["media_type"],
        "tmdb_id": item["tmdb_id"],
        "quality_profile_id": 1,
        "root_folder_path": "/data/Movies",
    }
    daten.update(extra)
    return client.post(
        "/api/requests", json=daten, headers=auth_headers(client, "kim", "passwort-1234")
    )


def test_anfrage_von_der_merkliste_ist_eine_gewoehnliche_anfrage(
    arr_client: TestClient,
) -> None:
    """Nur die Herkunft wird vermerkt - sonst aendert sich nichts."""
    antwort = _anfragen(arr_client, _demo_titel(arr_client), from_watchlist=True)
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["from_watchlist"] is True

    with SessionLocal() as session:
        assert session.query(MediaRequest).one().from_watchlist is True


def test_ohne_angabe_bleibt_die_anfrage_gewoehnlich(arr_client: TestClient) -> None:
    antwort = _anfragen(arr_client, _demo_titel(arr_client))
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["from_watchlist"] is False


def test_admin_kann_nach_herkunft_filtern(arr_client: TestClient) -> None:
    """Der Reiter "Über Merkliste angefragt" haengt an diesem Filter."""
    von_merkliste = _demo_titel(arr_client, 0)
    von_hand = _demo_titel(arr_client, 1)
    _anfragen(arr_client, von_merkliste, from_watchlist=True)
    _anfragen(arr_client, von_hand)

    alle = arr_client.get("/api/admin/requests").json()
    assert len(alle) == 2
    gefiltert = arr_client.get("/api/admin/requests?from_watchlist=true").json()
    assert [r["tmdb_id"] for r in gefiltert] == [von_merkliste["tmdb_id"]]
