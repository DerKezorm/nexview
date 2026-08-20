"""Titel erkennen, die im Media-Server liegen, aber nicht in Radarr/Sonarr.

Der Zweck ist eng: Doppel-Anfragen verhindern. Entsprechend geht es hier vor
allem um **Fehltreffer** - denn nur so kann diese Funktion ueberhaupt schaden.
Sie nimmt sonst einen Titel aus dem Angebot, den es in Wahrheit gar nicht gibt.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaServerLibraryItem,
    MediaType,
    RequestStatus,
    User,
)
from app.services import mediaserver_library
from app.services.mediaserver import LibraryItem
from app.services.settings_service import load_settings

from .conftest import ADMIN
from .test_mediaserver_login import FakeMediaServer, verbinde


class Werk:
    """Ein Titel, wie ihn die Anzeige durchreicht (nur die benutzten Felder)."""

    def __init__(
        self,
        tmdb_id: int,
        title: str,
        release_date: str = "",
        tvdb_id: int | None = None,
        status: str = "not_requested",
    ) -> None:
        self.tmdb_id = tmdb_id
        self.title = title
        self.release_date = release_date
        self.tvdb_id = tvdb_id
        self.status = status


def eintragen(**felder: object) -> None:
    with SessionLocal() as db:
        db.add(
            MediaServerLibraryItem(
                provider="plex",
                media_type=MediaType.movie,
                guid=str(felder.get("guid") or felder.get("title")),
                title=str(felder.get("title") or ""),
                title_key=str(felder.get("title_key") or ""),
                tmdb_id=felder.get("tmdb_id"),
                tvdb_id=felder.get("tvdb_id"),
                year=felder.get("year"),
            )
        )
        db.commit()


def suchen(items: list[Werk], media_type: MediaType = MediaType.movie) -> set[int]:
    with SessionLocal() as db:
        return mediaserver_library.vorhandene_kennungen(db, media_type, items)


# --------------------------------------------------------------------------
# Treffer
# --------------------------------------------------------------------------


def test_treffer_ueber_tmdb() -> None:
    eintragen(title="Dune", tmdb_id=438631, year=2021)
    assert suchen([Werk(438631, "Dune", "2021-09-15")]) == {438631}


def test_treffer_ueber_tvdb_bei_serien() -> None:
    """Serien fuehrt Plex oft nur mit der TVDB-Kennung."""
    with SessionLocal() as db:
        db.add(
            MediaServerLibraryItem(
                provider="plex",
                media_type=MediaType.tv,
                guid="s1",
                title="Severance",
                title_key="severance",
                tvdb_id=371980,
                year=2022,
            )
        )
        db.commit()

    treffer = suchen([Werk(95396, "Severance", "2022-02-18", tvdb_id=371980)], MediaType.tv)
    assert treffer == {95396}


def test_treffer_ueber_titel_und_jahr() -> None:
    """Alte Sammlungen ohne Kennungen - der letzte Ausweg."""
    eintragen(title="Der Pate", title_key="derpate", year=1972)
    assert suchen([Werk(238, "Der Pate", "1972-03-14")]) == {238}


# --------------------------------------------------------------------------
# Fehltreffer - der eigentlich gefaehrliche Teil
# --------------------------------------------------------------------------


def test_remake_wird_nicht_verwechselt() -> None:
    """Der wichtigste Test.

    "The Lion King" gibt es 1994 und 2019. Ohne das Jahr im Abgleich haette
    der Besitzer des Originals das Remake nie anfragen koennen - Nexview
    haette behauptet, er habe es schon.
    """
    eintragen(title="The Lion King", title_key="thelionking", year=1994)

    assert suchen([Werk(8587, "The Lion King", "1994-06-24")]) == {8587}
    assert suchen([Werk(420818, "The Lion King", "2019-07-12")]) == set()


def test_falsche_kennung_in_plex_fuehrt_nicht_in_die_irre() -> None:
    """Ein echter Fall aus einer Bibliothek mit 3509 Filmen.

    Plex fuehrte "Irenas Geheimnis" (2023) unter ``tmdb=1291936`` - diese
    Nummer gehoert aber zu einem chinesischen Film ohne Erscheinungsdatum.
    Ohne den Jahresabgleich haette Nexview jedem, der jenen Film sucht,
    gemeldet, er habe ihn schon.
    """
    eintragen(title="Irenas Geheimnis", tmdb_id=1291936, year=2023)

    # Der Film, dem die Nummer wirklich gehoert - ohne Datum bei TMDB.
    assert suchen([Werk(1291936, "誓言", "")]) == set()
    # Und einer aus einem anderen Jahr ebenso wenig.
    assert suchen([Werk(1291936, "誓言", "2016-05-01")]) == set()


def test_ein_jahr_abweichung_ist_erlaubt() -> None:
    """Festivalstart und Kinostart fallen oft in verschiedene Jahre."""
    eintragen(title="Irgendwas", tmdb_id=999, year=2023)
    assert suchen([Werk(999, "Irgendwas", "2024-02-01")]) == {999}
    assert suchen([Werk(999, "Irgendwas", "2021-02-01")]) == set()


def test_ohne_jahr_kein_titel_treffer() -> None:
    """Lieber einen alten Eintrag uebersehen als einen falschen behaupten."""
    eintragen(title="Irgendwas", title_key="irgendwas", year=None)
    assert suchen([Werk(1, "Irgendwas", "1999-01-01")]) == set()


def test_fremder_titel_trifft_nicht() -> None:
    eintragen(title="Dune", tmdb_id=438631, year=2021)
    assert suchen([Werk(1, "Ganz anderer Film", "2021-01-01")]) == set()


def test_leere_eingabe() -> None:
    assert suchen([]) == set()


# --------------------------------------------------------------------------
# Einlesen und Anzeige
# --------------------------------------------------------------------------


class BibliotheksServer(FakeMediaServer):
    """Ein Media-Server mit Bibliothek."""

    def __init__(self, werke: list[LibraryItem], **rest: object) -> None:
        super().__init__(**rest)  # type: ignore[arg-type]
        self.werke = werke
        self.abrufe = 0

    async def library_index(self) -> list[LibraryItem]:
        self.abrufe += 1
        return self.werke


async def test_einlesen_ersetzt_den_bestand(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    verbinde(admin_client)
    server = BibliotheksServer(
        [
            LibraryItem(media_type="movie", guid="p1", title="Dune", tmdb_id=438631, year=2021),
            LibraryItem(media_type="tv", guid="p2", title="Severance", tvdb_id=371980, year=2022),
        ]
    )
    monkeypatch.setattr(mediaserver_library, "get_media_server", lambda _s: server)

    with SessionLocal() as db:
        anzahl = await mediaserver_library.refresh(db, load_settings(db))
    assert anzahl == 2

    # Noch einmal - der Bestand darf sich nicht verdoppeln.
    server.werke = [
        LibraryItem(media_type="movie", guid="p1", title="Dune", tmdb_id=438631, year=2021)
    ]
    with SessionLocal() as db:
        anzahl = await mediaserver_library.refresh(db, load_settings(db))
        assert anzahl == 1
        assert db.query(MediaServerLibraryItem).count() == 1


async def test_ausfall_laesst_den_bestand_stehen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein halb gefuellter Abgleich waere schlimmer als ein veralteter.

    Titel wuerden sonst faelschlich wieder als anfragbar erscheinen.
    """
    verbinde(admin_client)
    eintragen(title="Dune", tmdb_id=438631, year=2021)

    class Kaputt(BibliotheksServer):
        async def library_index(self) -> list[LibraryItem]:
            from app.services.mediaserver import MediaServerError

            raise MediaServerError("Server aus")

    monkeypatch.setattr(mediaserver_library, "get_media_server", lambda _s: Kaputt([]))
    with SessionLocal() as db:
        assert await mediaserver_library.refresh(db, load_settings(db)) == 0
        assert db.query(MediaServerLibraryItem).count() == 1


async def test_ohne_verbundenen_server_passiert_nichts(admin_client: TestClient) -> None:
    with SessionLocal() as db:
        assert await mediaserver_library.refresh(db, load_settings(db)) == 0


def test_anbieter_ohne_bibliothek_ist_kein_fehler(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``library_index`` ist fuer kuenftige Anbieter noch ein Platzhalter."""
    verbinde(admin_client)

    class Schlicht(FakeMediaServer):
        pass  # erbt den NotImplementedError aus der Basis

    monkeypatch.setattr(mediaserver_library, "get_media_server", lambda _s: Schlicht())

    import asyncio

    with SessionLocal() as db:
        assert asyncio.run(mediaserver_library.refresh(db, load_settings(db))) == 0


def test_abzeichen_erscheint_in_der_anzeige(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Weg von Anfang bis Ende: Plex kennt den Titel, Radarr nicht.

    Genau dieser Fall laesst sich in einer Installation, in der alles ueber
    Radarr laeuft, gar nicht herbeifuehren - Plex und Radarr sehen dort
    dieselben Ordner. Hier ist Radarr leer (siehe ``arr_client``), also greift
    der neue Zweig.
    """
    verbinde(arr_client)

    seite = arr_client.get("/api/discover/movie").json()
    erster = seite["items"][0]
    assert erster["status"] == "not_requested"

    # Denselben Titel in die Bibliothek des Media-Servers legen.
    eintragen(
        title=erster["title"],
        tmdb_id=erster["tmdb_id"],
        year=int((erster.get("release_date") or "0000")[:4]) or None,
    )

    nachher = arr_client.get("/api/discover/movie").json()
    passend = next(i for i in nachher["items"] if i["tmdb_id"] == erster["tmdb_id"])
    assert passend["status"] == "in_library"

    # Und auf der Detailseite ebenso - sie hat ihre eigene Status-Stelle.
    einzeln = arr_client.get(f"/api/media/movie/{erster['tmdb_id']}").json()
    assert einzeln["status"] == "in_library"


def test_eigene_anfrage_sticht_die_bibliothek(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was gerade angefragt ist, bleibt angefragt.

    Sonst verschwaende der Zustand "wird gerade geladen" hinter einem
    beilaeufigen "liegt schon irgendwo".
    """
    verbinde(arr_client)
    seite = arr_client.get("/api/discover/movie").json()
    erster = seite["items"][0]

    eintragen(title=erster["title"], tmdb_id=erster["tmdb_id"], year=2000)

    # Die Anfrage direkt anlegen: Der Weg ueber die API braucht ein
    # erreichbares Radarr, und darum geht es hier nicht.
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == ADMIN["username"]).one()
        db.add(
            MediaRequest(
                user_id=admin.id,
                media_type=MediaType.movie,
                tmdb_id=erster["tmdb_id"],
                title=erster["title"],
                status=RequestStatus.searching,
            )
        )
        db.commit()

    nachher = arr_client.get("/api/discover/movie").json()
    passend = next(i for i in nachher["items"] if i["tmdb_id"] == erster["tmdb_id"])
    assert passend["status"] != "in_library"


def test_nur_film_und_serien_bibliotheken() -> None:
    """Musik und Fotos gehen Nexview nichts an - geprueft am Uebersetzer."""
    from app.services.mediaserver.plex import _als_werk

    werk = _als_werk(
        {
            "title": "Dune",
            "year": 2021,
            "ratingKey": "1",
            "Guid": [{"id": "tmdb://438631"}, {"id": "imdb://tt1160419"}],
        },
        "movie",
    )
    assert werk is not None
    assert werk.tmdb_id == 438631
    assert werk.imdb_id == "tt1160419"
    assert werk.year == 2021

    # Ohne Titel ist der Eintrag wertlos.
    assert _als_werk({"ratingKey": "2"}, "movie") is None


async def test_handknopf_zeigt_den_fehler(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Jetzt abgleichen" darf einen Ausfall nicht als Erfolg tarnen.

    Vorher schluckte ``refresh`` jeden Fehler, und der Knopf meldete
    kommentarlos den alten Zaehler samt Zeitstempel - scheinbarer Erfolg.
    Genau daran ist ein Nutzer verzweifelt, bei dem kein einziger Plex-Titel
    ein Abzeichen bekam (Issue #2): Es gab schlicht keine Stelle, an der die
    Ursache je sichtbar geworden waere. Der Hintergrund-Abgleich schluckt
    weiterhin - ein Aussetzer darf den Durchgang nicht beenden.
    """
    verbinde(admin_client)

    class Kaputt(BibliotheksServer):
        async def library_index(self) -> list[LibraryItem]:
            from app.services.mediaserver import MediaServerError

            raise MediaServerError("Der Plex-Server antwortet nicht (Zeitüberschreitung).")

    # Beide Stellen ueberschreiben: Der Router prueft die Verbindung selbst.
    from app.routers import mediaserver as mediaserver_router

    monkeypatch.setattr(mediaserver_library, "get_media_server", lambda _s: Kaputt([]))
    monkeypatch.setattr(mediaserver_router, "get_media_server", lambda _s: Kaputt([]))

    antwort = admin_client.post("/api/admin/mediaserver/library/refresh")
    assert antwort.status_code == 502
    assert "antwortet nicht" in antwort.json()["detail"]["message"]


def test_namensvetter_mit_bekannter_kennung_trifft_nicht() -> None:
    """Gleicher Titel, gleiches Jahr, anderer Film - kein Treffer.

    Der gemeldete Fall: "Backrooms" (2026, Spielfilm) lag mit bekannter
    TMDB-Kennung in Plex. Ein gleichnamiger 4-Minuten-Kurzfilm aus demselben
    Jahr erschien darueber als "In der Bibliothek" - die Jahres-Pruefung kann
    Doppelgaenger nicht trennen. Traegt die Plex-Zeile eine Kennung, ist ihre
    Identitaet geklaert; der Titel-Rueckfall bleibt Eintraegen ohne jede
    Kennung vorbehalten.
    """
    eintragen(title="Backrooms", title_key="backrooms", tmdb_id=1083381, year=2026)

    kurzfilm = Werk(tmdb_id=999999, title="Backrooms", release_date="2026-01-01")
    assert suchen([kurzfilm]) == set()

    # Der echte Film trifft weiterhin - ueber seine Kennung.
    spielfilm = Werk(tmdb_id=1083381, title="Backrooms", release_date="2026-01-01")
    assert suchen([spielfilm]) == {1083381}


def test_eintrag_ohne_kennung_trifft_weiter_ueber_den_titel() -> None:
    """Alte Plex-Agenten liefern gar keine Kennungen - dort bleibt der Rueckfall."""
    eintragen(title="Alter Schinken", title_key="alterschinken", tmdb_id=None, year=1965)

    kachel = Werk(tmdb_id=424242, title="Alter Schinken", release_date="1965-05-01")
    assert suchen([kachel]) == {424242}
