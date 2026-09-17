"""Neu zuordnen: der einzige Weg, auf dem Nexview in einen Medienserver schreibt.

Gemessen am 17.09.2026 an Jellyfin 10.11.11 und Emby 4.9.5.0, gelesen an Plex:
Gesucht wird ueber die Nummer (ueber den Namen "8" kamen zwanzig Kandidaten,
der falsche zuerst). Emby zeigte die neue Zuordnung erst nach Sekunden und
sprang bei einer Serie von selbst zurueck - deshalb wird nachgeprueft.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import MediaServerLibraryItem, MediaType
from app.routers import analyse
from app.services.mediaserver import LibraryItem, MediaServerError
from app.services.mediaserver.jellyfin import JellyfinServer
from app.services.mediaserver.plex import PlexServer

PFAD = "/api/admin/analyse/server-vergleich/zuordnen"


def _zeile(tmdb: int = 1291936) -> None:
    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider="plex",
                media_type=MediaType.movie,
                guid="plex://movie/1",
                rating_key="4711",
                tmdb_id=tmdb,
                title="Irenas Geheimnis",
                title_key="irenasgeheimnis",
                year=2023,
                file_paths="/filme/Irena (2023) {tmdb-1026880}/Irena.mkv",
            )
        )
        session.commit()


class Server:
    """Nimmt die Korrektur an und meldet danach, was der Test vorgibt."""

    def __init__(self, antworten: list[int | None]) -> None:
        self.antworten = antworten
        self.angewendet: list[tuple] = []

    async def zuordnung_anwenden(self, schluessel, art, tmdb, tvdb):
        self.angewendet.append((schluessel, art, tmdb, tvdb))
        return "Irenas Geheimnis"

    async def titel_nummern(self, schluessel):
        tmdb = self.antworten.pop(0) if len(self.antworten) > 1 else self.antworten[0]
        return LibraryItem(
            media_type="movie", guid="g", title="Irena's Vow", tmdb_id=tmdb, year=2023
        )


@pytest.fixture
def ohne_warten(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sofort(_sekunden: float) -> None:
        return None

    monkeypatch.setattr(analyse, "_warten", sofort)


def _mit(monkeypatch: pytest.MonkeyPatch, server: Server) -> None:
    monkeypatch.setattr(analyse, "media_server_for_setup", lambda _s, _p: server)


def _anfrage(client: TestClient, **abweichend) -> dict:
    daten = {"anbieter": "plex", "schluessel": "4711", "art": "movie", "tmdb": 1026880}
    daten.update(abweichend)
    return client.post(PFAD, json=daten)


def test_korrektur_wird_nachgeprueft_und_in_die_tabelle_uebernommen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, ohne_warten: None
) -> None:
    _zeile()
    # Wie Emby: erst noch die alte Nummer, dann die neue.
    server = Server([1291936, 1291936, 1026880])
    _mit(monkeypatch, server)

    antwort = _anfrage(admin_client)
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["ergebnis"] == "korrigiert"
    assert server.angewendet == [("4711", "movie", 1026880, None)]
    with SessionLocal() as session:
        zeile = session.scalars(select(MediaServerLibraryItem)).one()
        assert zeile.tmdb_id == 1026880
        assert zeile.title == "Irena's Vow"
        # Pfade bleiben, wie sie sind - nur die Zuordnung aendert sich.
        assert "tmdb-1026880" in zeile.file_paths


def test_zurueckgesprungen_wird_ehrlich_gemeldet_und_nichts_uebernommen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, ohne_warten: None
) -> None:
    """Die Emby-Serie vom 17.09.2026: zwei Sekunden richtig, dann wieder die alte."""
    _zeile()
    _mit(monkeypatch, Server([1026880, 1291936]))

    antwort = _anfrage(admin_client)
    assert antwort.json()["ergebnis"] == "zurueckgesprungen"
    with SessionLocal() as session:
        zeile = session.scalars(select(MediaServerLibraryItem)).one()
        assert zeile.tmdb_id == 1291936
        # Auch der Titel nicht: Was der Server zuletzt meldete, ist nicht bestaetigt.
        assert zeile.title == "Irenas Geheimnis"


def test_nie_sichtbar_heisst_nicht_bestaetigt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, ohne_warten: None
) -> None:
    _zeile()
    _mit(monkeypatch, Server([1291936]))
    assert _anfrage(admin_client).json()["ergebnis"] == "nicht_bestaetigt"


def test_ohne_nummer_wird_nichts_geschrieben(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _zeile()
    server = Server([1])
    _mit(monkeypatch, server)
    antwort = _anfrage(admin_client, tmdb=None)
    assert antwort.status_code == 400
    assert server.angewendet == []


def test_unbekannter_titel_ist_404(admin_client: TestClient) -> None:
    assert _anfrage(admin_client).status_code == 404


def test_fehler_des_servers_kommt_mit_kennung_an(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _zeile()

    class Scheitert(Server):
        async def zuordnung_anwenden(self, *_a):
            raise MediaServerError(
                "keiner", code="mediaserver_rematch_no_candidate", service="Plex"
            )

    _mit(monkeypatch, Scheitert([1]))
    antwort = _anfrage(admin_client)
    assert antwort.status_code == 502
    assert antwort.json()["detail"]["code"] == "mediaserver_rematch_no_candidate"


# --------------------------------------------------------------------------
# Die Wege je Anbieter
# --------------------------------------------------------------------------


def test_jellyfin_sucht_ueber_die_nummer_und_nimmt_den_passenden_treffer() -> None:
    server = JellyfinServer.__new__(JellyfinServer)
    server.provider = "jellyfin"
    server.label = "Jellyfin"
    aufrufe: list[tuple] = []

    async def anfrage(methode, pfad, **kwargs):
        aufrufe.append((methode, pfad, kwargs))
        if pfad == "/Items/RemoteSearch/Movie":
            return [
                {"Name": "The Hateful 8", "ProviderIds": {"Tmdb": "273248"}},
                {"Name": "8 - The Soul Collector", "ProviderIds": {"Tmdb": "605802"}},
            ]
        return None

    server._anfrage = anfrage  # type: ignore[method-assign]
    name = asyncio.run(server.zuordnung_anwenden("abc", "movie", 605802, None))

    assert name == "8 - The Soul Collector"
    suche, anwenden = aufrufe
    assert suche[2]["json"]["SearchInfo"] == {"ProviderIds": {"Tmdb": "605802"}}
    assert "Name" not in suche[2]["json"]["SearchInfo"]
    assert anwenden[:2] == ("POST", "/Items/RemoteSearch/Apply/abc")
    assert anwenden[2]["json"]["ProviderIds"]["Tmdb"] == "605802"


def test_jellyfin_ohne_passenden_treffer_schreibt_nichts() -> None:
    server = JellyfinServer.__new__(JellyfinServer)
    server.provider = "jellyfin"
    server.label = "Jellyfin"
    aufrufe: list[str] = []

    async def anfrage(methode, pfad, **kwargs):
        aufrufe.append(pfad)
        return [{"Name": "Etwas anderes", "ProviderIds": {"Tmdb": "1"}}]

    server._anfrage = anfrage  # type: ignore[method-assign]
    with pytest.raises(MediaServerError) as fehler:
        asyncio.run(server.zuordnung_anwenden("abc", "movie", 605802, None))
    assert fehler.value.code == "mediaserver_rematch_no_candidate"
    assert aufrufe == ["/Items/RemoteSearch/Movie"]


def _plex(matches: list[dict]) -> tuple[PlexServer, list[tuple]]:
    server = PlexServer.__new__(PlexServer)
    aufrufe: list[tuple] = []

    async def anfrage(pfad, params=None, token=None, methode="GET"):
        aufrufe.append((methode, pfad, params))
        if pfad.endswith("/matches"):
            return {"SearchResult": matches}
        return {}

    server._server = anfrage  # type: ignore[method-assign]
    return server, aufrufe


def test_plex_sucht_mit_tmdb_titel_und_wendet_die_plex_kennung_an() -> None:
    server, aufrufe = _plex([{"name": "Irena's Vow", "year": 2023, "guid": "plex://movie/abc"}])
    asyncio.run(server.zuordnung_anwenden("4711", "movie", 1026880, None))

    suche, anwenden = aufrufe
    assert suche == ("GET", "/library/metadata/4711/matches", {"manual": 1, "title": "tmdb-1026880"})
    assert anwenden[0] == "PUT"
    assert anwenden[1] == "/library/metadata/4711/match"
    assert anwenden[2]["guid"] == "plex://movie/abc"


def test_plex_serie_sucht_zuerst_ueber_tvdb() -> None:
    server, aufrufe = _plex([{"name": "Columbo", "year": 1971, "guid": "plex://show/c"}])
    asyncio.run(server.zuordnung_anwenden("10939", "tv", 873, 70369))
    assert aufrufe[0][2]["title"] == "tvdb-70369"


def test_plex_mehrdeutige_kandidaten_werden_nicht_geraten() -> None:
    server, aufrufe = _plex(
        [
            {"name": "A", "year": 2020, "guid": "plex://movie/a"},
            {"name": "B", "year": 2020, "guid": "plex://movie/b"},
        ]
    )
    with pytest.raises(MediaServerError):
        asyncio.run(server.zuordnung_anwenden("4711", "movie", 1, None))
    assert all(methode == "GET" for methode, _p, _q in aufrufe)


def test_plex_mit_zwei_tmdb_nummern_zaehlt_die_erste_und_kennt_beide() -> None:
    """Gemessen 17.09.2026: "Irenas Geheimnis" mit tmdb://1026880 und tmdb://1291936.

    Mit der letzten Nummer stand der Film in Nexview unter der alten, und die
    Nachpruefung nach dem Neu-Zuordnen meldete "noch nicht sichtbar", obwohl
    Plex die richtige Nummer nannte.
    """
    from app.services.mediaserver.plex import _als_werk

    werk = _als_werk(
        {
            "title": "Irenas Geheimnis",
            "ratingKey": "3852",
            "Guid": [
                {"id": "imdb://tt19869662"},
                {"id": "tmdb://1026880"},
                {"id": "tmdb://1291936"},
            ],
        },
        "movie",
    )
    assert werk is not None
    assert werk.tmdb_id == 1026880
    assert werk.tmdb_ids == (1026880, 1291936)


def test_nachpruefung_akzeptiert_jede_genannte_nummer(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, ohne_warten: None
) -> None:
    _zeile()

    class ZweiNummern(Server):
        async def titel_nummern(self, schluessel):
            return LibraryItem(
                media_type="movie",
                guid="g",
                title="Irenas Geheimnis",
                tmdb_id=1291936,
                tmdb_ids=(1291936, 1026880),
                year=2023,
            )

    _mit(monkeypatch, ZweiNummern([1]))
    assert _anfrage(admin_client).json()["ergebnis"] == "korrigiert"
    with SessionLocal() as session:
        assert session.scalars(select(MediaServerLibraryItem)).one().tmdb_id == 1026880
