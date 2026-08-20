"""„Schon gesehen" - je Benutzer.

Der Schwerpunkt liegt auf den beiden Stellen, an denen es wirklich schiefgehen
kann: der Zuordnung der Konten (Plex fuehrt zwei Nummernraeume in einem Feld)
und der Trennung der Benutzer untereinander.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.crypto import encrypt
from app.db import SessionLocal
from app.models import (
    MediaServerLibraryItem,
    MediaType,
    Notification,
    NotificationType,
    User,
    UserWatched,
)
from app.routers import details
from app.services import mediaserver_watched
from app.services.mediaserver import MediaServerError, ServerUser, WatchedRecord
from app.services.settings_service import load_settings, save_settings

from .conftest import ADMIN, auth_headers, create_user
from .test_mediaserver_login import FakeMediaServer, verbinde


class VerlaufsServer(FakeMediaServer):
    """Ein Media-Server mit Wiedergabe-Verlauf und Kontenliste.

    ``stand`` ist der vollstaendige Gesehen-Stand fuer ein persoenliches
    Token; ohne Angabe kennt der Server den Weg nicht (``NotImplementedError``,
    wie die Basisklasse). ``token_kaputt`` laesst jede Token-Abfrage mit 401
    scheitern - der Fall "Passwort geaendert, Zugang abgelaufen".
    """

    def __init__(
        self,
        verlauf: list[WatchedRecord],
        konten: list[ServerUser] | None = None,
        stand: list[WatchedRecord] | None = None,
        token_kaputt: bool = False,
        **rest: object,
    ) -> None:
        super().__init__(**rest)  # type: ignore[arg-type]
        self.verlauf = verlauf
        self.konten = konten or []
        self.stand = stand
        self.token_kaputt = token_kaputt
        self.gefragte_tokens: list[str] = []

    async def watched_since(self, since: datetime | None = None) -> list[WatchedRecord]:
        return self.verlauf

    async def list_server_users(self) -> list[ServerUser]:
        return self.konten

    async def watched_index(self, provider_token: str) -> list[WatchedRecord]:
        self.gefragte_tokens.append(provider_token)
        if self.token_kaputt:
            raise MediaServerError("Der Plex-Server hat den Zugang nicht akzeptiert.", 401)
        if self.stand is None:
            raise NotImplementedError
        return self.stand


def bibliothek(*werke: tuple[str, int, MediaType], owner_watched: bool = False) -> None:
    """Titel in den Bibliotheks-Abgleich legen - der Verlauf haengt daran."""
    with SessionLocal() as db:
        for rating_key, tmdb_id, art in werke:
            db.add(
                MediaServerLibraryItem(
                    provider="plex",
                    media_type=art,
                    guid=f"plex://{rating_key}",
                    rating_key=rating_key,
                    tmdb_id=tmdb_id,
                    title=f"Titel {tmdb_id}",
                    title_key=f"titel{tmdb_id}",
                    year=2020,
                    owner_watched=owner_watched,
                )
            )
        db.commit()


def verknuepfen(username: str, konto: str, plexname: str, token: str | None = None) -> int:
    with SessionLocal() as db:
        u = db.query(User).filter(User.username == username).one()
        u.mediaserver_provider = "plex"
        u.mediaserver_account_id = konto
        u.mediaserver_username = plexname
        if token is not None:
            u.watchlist_token = encrypt(token)
        db.commit()
        return u.id


def gesehen_eintragen(user_id: int, tmdb_id: int, art: MediaType = MediaType.movie) -> None:
    with SessionLocal() as db:
        db.add(UserWatched(user_id=user_id, media_type=art, tmdb_id=tmdb_id))
        db.commit()


async def abgleichen(server: VerlaufsServer, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(mediaserver_watched, "get_media_server", lambda _s: server)
    with SessionLocal() as db:
        return await mediaserver_watched.refresh(db, load_settings(db))


async def test_geteilter_nutzer_ueber_die_nummer(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer geteilt bekommt, erscheint im Verlauf unter seiner plex.tv-Nummer."""
    verbinde(admin_client)
    create_user(admin_client, "gast", email="gast@beispiel.de")
    gast_id = verknuepfen("gast", "838679208", "DilaraUygunMrozek")
    bibliothek(("54860", 12345, MediaType.movie))

    server = VerlaufsServer(
        [WatchedRecord(account_id="838679208", item_key="54860", media_type="movie")]
    )
    assert await abgleichen(server, monkeypatch) == 1

    with SessionLocal() as db:
        eintrag = db.query(UserWatched).one()
        assert eintrag.user_id == gast_id
        assert eintrag.tmdb_id == 12345


async def test_eigentuemer_ueber_den_namen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der wichtigste Fall.

    Plex fuehrt den Eigentuemer des Servers unter der ``1`` - nicht unter
    seiner plex.tv-Nummer. Ohne den Umweg ueber den Namen aus der Kontenliste
    bliebe ausgerechnet der Administrator ohne jede Zuordnung. Sein Stand
    kommt aus dem Zaehler am Titel (``owner_watched``), nicht aus dem Verlauf.
    """
    verbinde(admin_client)
    admin_id = verknuepfen(ADMIN["username"], "490145397", "DerKezorm")
    bibliothek(("54860", 12345, MediaType.movie), owner_watched=True)

    server = VerlaufsServer(
        [],
        konten=[ServerUser(account_id="1", username="DerKezorm")],
    )
    assert await abgleichen(server, monkeypatch) == 1

    with SessionLocal() as db:
        assert db.query(UserWatched).one().user_id == admin_id


async def test_alter_verlauf_setzt_entfernten_haken_nicht_zurueck(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Eigentuemer hat einen Haken entfernt - der Zaehler sagt "ungesehen".

    Der Verlauf kennt die Wiedergabe von damals trotzdem noch. Wuerde er fuer
    den Eigentuemer weiter angewendet, staende das Auge sofort wieder da.
    """
    verbinde(admin_client)
    admin_id = verknuepfen(ADMIN["username"], "490145397", "DerKezorm")
    bibliothek(("54860", 12345, MediaType.movie), owner_watched=False)
    gesehen_eintragen(admin_id, 12345)

    server = VerlaufsServer(
        [WatchedRecord(account_id="1", item_key="54860", media_type="movie")],
        konten=[ServerUser(account_id="1", username="DerKezorm")],
    )
    await abgleichen(server, monkeypatch)

    with SessionLocal() as db:
        assert db.query(UserWatched).count() == 0


async def test_namensvergleich_ignoriert_schreibweise(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plex nennt denselben Menschen mal mit und mal ohne Leerzeichen."""
    verbinde(admin_client)
    create_user(admin_client, "gast", email="gast@beispiel.de")
    gast_id = verknuepfen("gast", "unbekannt", "DilaraUygunMrozek")
    bibliothek(("77", 999, MediaType.movie))

    server = VerlaufsServer(
        [WatchedRecord(account_id="5", item_key="77", media_type="movie")],
        konten=[ServerUser(account_id="5", username="Dilara Uygun-Mrozek")],
    )
    assert await abgleichen(server, monkeypatch) == 1

    with SessionLocal() as db:
        assert db.query(UserWatched).one().user_id == gast_id


async def test_unbekanntes_konto_wird_uebergangen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verwaltete Profile ohne eigenes Konto lassen sich niemandem zuordnen."""
    verbinde(admin_client)
    verknuepfen(ADMIN["username"], "490145397", "DerKezorm")
    bibliothek(("54860", 12345, MediaType.movie))

    server = VerlaufsServer(
        [WatchedRecord(account_id="7", item_key="54860", media_type="movie")],
        konten=[ServerUser(account_id="7", username="")],
    )
    assert await abgleichen(server, monkeypatch) == 0

    with SessionLocal() as db:
        assert db.query(UserWatched).count() == 0


async def test_titel_ausserhalb_der_bibliothek(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Bibliotheks-Eintrag gibt es keine TMDB-Kennung - also keine Zuordnung."""
    verbinde(admin_client)
    verknuepfen(ADMIN["username"], "490145397", "DerKezorm")
    bibliothek(("54860", 12345, MediaType.movie))

    server = VerlaufsServer(
        [WatchedRecord(account_id="490145397", item_key="99999", media_type="movie")]
    )
    assert await abgleichen(server, monkeypatch) == 0


async def test_zweiter_lauf_verdoppelt_nichts(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    verbinde(admin_client)
    verknuepfen(ADMIN["username"], "490145397", "DerKezorm")
    bibliothek(("54860", 12345, MediaType.movie))

    server = VerlaufsServer(
        [
            WatchedRecord(
                account_id="490145397",
                item_key="54860",
                media_type="movie",
                watched_at=datetime(2026, 8, 1, 20, 0),
            )
        ]
    )
    assert await abgleichen(server, monkeypatch) == 1

    # Dieselbe Wiedergabe noch einmal, spaeter gesehen.
    server.verlauf = [
        WatchedRecord(
            account_id="490145397",
            item_key="54860",
            media_type="movie",
            watched_at=datetime(2026, 8, 15, 20, 0),
        )
    ]
    assert await abgleichen(server, monkeypatch) == 0

    with SessionLocal() as db:
        eintrag = db.query(UserWatched).one()
        # Der spaetere Zeitpunkt gewinnt.
        assert eintrag.watched_at == datetime(2026, 8, 15, 20, 0)


async def test_serie_zaehlt_als_ganzes(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bei Serien zaehlt die Serie, nicht die einzelne Folge."""
    verbinde(admin_client)
    verknuepfen(ADMIN["username"], "490145397", "DerKezorm")
    bibliothek(("21339", 555, MediaType.tv))

    server = VerlaufsServer(
        [
            WatchedRecord(account_id="490145397", item_key="21339", media_type="tv"),
            WatchedRecord(account_id="490145397", item_key="21339", media_type="tv"),
        ]
    )
    assert await abgleichen(server, monkeypatch) == 1


# --------------------------------------------------------------------------
# Das persoenliche Token - die vollstaendige Quelle
# --------------------------------------------------------------------------


async def test_eigenes_token_liest_vollstaendig(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mit eigenem Token kommt der Stand vom Zaehler - nicht aus dem Verlauf."""
    verbinde(admin_client)
    create_user(admin_client, "gast", email="gast@beispiel.de")
    gast_id = verknuepfen("gast", "838679208", "DilaraUygunMrozek", token="gast-token")
    bibliothek(("54860", 12345, MediaType.movie))

    server = VerlaufsServer(
        [],
        stand=[WatchedRecord(account_id="", item_key="54860", media_type="movie")],
    )
    assert await abgleichen(server, monkeypatch) == 1

    # Gefragt wurde mit dem entschluesselten Token der Person.
    assert server.gefragte_tokens == ["gast-token"]
    with SessionLocal() as db:
        eintrag = db.query(UserWatched).one()
        assert eintrag.user_id == gast_id
        assert eintrag.tmdb_id == 12345


async def test_eigenes_token_entfernt_haken(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Haken in Plex entfernt -> Auge in Nexview weg. Der Plex-Stand gilt."""
    verbinde(admin_client)
    create_user(admin_client, "gast", email="gast@beispiel.de")
    gast_id = verknuepfen("gast", "838679208", "DilaraUygunMrozek", token="gast-token")
    bibliothek(("54860", 12345, MediaType.movie))
    gesehen_eintragen(gast_id, 12345)

    server = VerlaufsServer([], stand=[])
    await abgleichen(server, monkeypatch)

    with SessionLocal() as db:
        assert db.query(UserWatched).count() == 0


async def test_entfernte_titel_behalten_ihr_auge(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Titel, der aus der Bibliothek verschwunden ist, bleibt gesehen.

    Der Abgleich kann ihn nicht mehr sehen - das macht die Wiedergabe nicht
    ungeschehen. Entfernt wird nur innerhalb des aktuellen Bestands.
    """
    verbinde(admin_client)
    create_user(admin_client, "gast", email="gast@beispiel.de")
    gast_id = verknuepfen("gast", "838679208", "DilaraUygunMrozek", token="gast-token")
    bibliothek(("54860", 12345, MediaType.movie))
    gesehen_eintragen(gast_id, 99999)  # nicht (mehr) in der Bibliothek

    server = VerlaufsServer([], stand=[])
    await abgleichen(server, monkeypatch)

    with SessionLocal() as db:
        assert db.query(UserWatched).one().tmdb_id == 99999


async def test_verlauf_gilt_nicht_fuer_token_nutzer(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer eine vollstaendige Quelle hat, wird vom Verlauf nicht mehr angefasst.

    Sonst setzte ein alter Verlaufseintrag den gerade entfernten Haken beim
    naechsten Abgleich sofort wieder.
    """
    verbinde(admin_client)
    create_user(admin_client, "gast", email="gast@beispiel.de")
    verknuepfen("gast", "838679208", "DilaraUygunMrozek", token="gast-token")
    bibliothek(("54860", 12345, MediaType.movie))

    server = VerlaufsServer(
        [WatchedRecord(account_id="838679208", item_key="54860", media_type="movie")],
        stand=[],
    )
    assert await abgleichen(server, monkeypatch) == 0

    with SessionLocal() as db:
        assert db.query(UserWatched).count() == 0


async def test_defektes_token_meldet_sich_genau_einmal(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """401 vom Server -> Person wird uebersprungen und einmal benachrichtigt.

    Der Abgleich laeuft stuendlich; ohne die Bremse staende jede Stunde
    dieselbe Meldung in der Glocke.
    """
    verbinde(admin_client)
    create_user(admin_client, "gast", email="gast@beispiel.de")
    gast_id = verknuepfen("gast", "838679208", "DilaraUygunMrozek", token="gast-token")
    bibliothek(("54860", 12345, MediaType.movie))

    server = VerlaufsServer([], token_kaputt=True)
    assert await abgleichen(server, monkeypatch) == 0
    # Gleich noch einmal - wie beim naechsten Stundenlauf.
    assert await abgleichen(server, monkeypatch) == 0

    with SessionLocal() as db:
        meldungen = (
            db.query(Notification)
            .filter(
                Notification.user_id == gast_id,
                Notification.type == NotificationType.mediaserver_reconnect,
            )
            .all()
        )
        assert len(meldungen) == 1
        assert meldungen[0].message_key == "notifications.mediaserverReconnect"
        # Vorhandene Marker bleiben unangetastet - aus einem Fehler wird
        # nichts geloescht.
        assert db.query(UserWatched).count() == 0


# --------------------------------------------------------------------------
# Anzeige
# --------------------------------------------------------------------------


def test_jeder_sieht_nur_seine_eigenen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was der eine gesehen hat, geht den anderen nichts an."""
    verbinde(arr_client)
    create_user(arr_client, "gast", email="gast@beispiel.de")

    seite = arr_client.get("/api/discover/movie").json()
    erster = seite["items"][0]
    assert erster["watched"] is False

    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == ADMIN["username"]).one()
        db.add(
            UserWatched(
                user_id=admin.id, media_type=MediaType.movie, tmdb_id=erster["tmdb_id"]
            )
        )
        db.commit()

    # Der Administrator sieht sein Abzeichen ...
    meins = arr_client.get("/api/discover/movie").json()
    assert next(i for i in meins["items"] if i["tmdb_id"] == erster["tmdb_id"])["watched"] is True

    # ... der Gast aber nicht.
    fremd = arr_client.get(
        "/api/discover/movie", headers=auth_headers(arr_client, "gast", "passwort-1234")
    ).json()
    assert next(i for i in fremd["items"] if i["tmdb_id"] == erster["tmdb_id"])["watched"] is False


def test_filmografie_vertraegt_gesehene_titel(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Personenseite darf an einem gesehenen Titel nicht scheitern.

    Ihre Eintraege sind ein **eigener** Typ (``PersonCredit``), werden aber von
    derselben Funktion mit Badges versehen wie die Kacheln. Fehlt dort das Feld
    ``watched``, lehnt Pydantic das Setzen ab und die ganze Seite antwortet mit
    500 - allerdings nur bei Personen, in deren Filmografie ueberhaupt ein
    gesehener Titel vorkommt. Genau deshalb fiel es zunaechst nicht auf.
    """
    verbinde(arr_client)

    class FakeClient:
        async def person(self, person_id: int) -> dict:
            return {
                "id": person_id,
                "name": "Testperson",
                "known_for_department": "Acting",
                "combined_credits": {
                    "crew": [],
                    "cast": [
                        {
                            "id": 4242,
                            "media_type": "movie",
                            "character": "Hauptrolle",
                            "title": "Ein gesehener Film",
                            "popularity": 80,
                        }
                    ],
                },
            }

    with SessionLocal() as db:
        save_settings(db, {"tmdb_api_key": "test-schluessel"})
        admin = db.query(User).filter(User.username == ADMIN["username"]).one()
        db.add(UserWatched(user_id=admin.id, media_type=MediaType.movie, tmdb_id=4242))
        db.commit()
    monkeypatch.setattr(details.media, "_client", lambda settings, region=None: FakeClient())

    antwort = arr_client.get("/api/person/488")
    assert antwort.status_code == 200, antwort.text
    passend = next(c for c in antwort.json()["credits"] if c["tmdb_id"] == 4242)
    assert passend["watched"] is True


def test_gesehen_ueberschreibt_den_zustand_nicht(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der eigentliche Grund fuer ein eigenes Feld statt eines Zustandswerts."""
    verbinde(arr_client)
    seite = arr_client.get("/api/discover/movie").json()
    erster = seite["items"][0]

    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == ADMIN["username"]).one()
        db.add(
            UserWatched(
                user_id=admin.id, media_type=MediaType.movie, tmdb_id=erster["tmdb_id"]
            )
        )
        db.add(
            MediaServerLibraryItem(
                provider="plex",
                media_type=MediaType.movie,
                guid="plex://x",
                rating_key="x",
                tmdb_id=erster["tmdb_id"],
                title=erster["title"],
                title_key="egal",
                year=int((erster.get("release_date") or "0000")[:4]) or None,
            )
        )
        db.commit()

    nachher = arr_client.get("/api/discover/movie").json()
    passend = next(i for i in nachher["items"] if i["tmdb_id"] == erster["tmdb_id"])
    # Beides steht nebeneinander, keines verdraengt das andere.
    assert passend["status"] == "in_library"
    assert passend["watched"] is True
