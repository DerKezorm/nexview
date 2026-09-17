"""Die Vergleichstabelle: welcher Titel liegt auf welchem Server.

Anlass war Issue #10. Der Abgleich zaehlte nur ueber die TMDB-Nummer, und
ein Film, den ein Server unter einer anderen Nummer fuehrte, fehlte damit auf
beiden Seiten. Die Tests hier halten fest, dass so ein Titel **eine** Zeile
ist - und dass zwei verschiedene Titel mit gleichem Namen trotzdem zwei bleiben.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaServerLibraryItem, MediaType
from app.services import abgleich, befunde, server_vergleich

PFAD = "/api/admin/analyse/server-vergleich"


def _titel(
    provider: str,
    *,
    tmdb: int | None = None,
    tvdb: int | None = None,
    imdb: str | None = None,
    jahr: int | None = 2020,
    titel: str = "Ein Film",
    art: MediaType = MediaType.movie,
    nr: int = 0,
) -> None:
    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider=provider,
                media_type=art,
                guid=f"{provider}:{tmdb}:{tvdb}:{imdb}:{titel}:{jahr}:{nr}",
                tmdb_id=tmdb,
                tvdb_id=tvdb,
                imdb_id=imdb,
                title=titel,
                title_key=server_vergleich.titel_schluessel(titel),
                year=jahr,
            )
        )
        session.commit()


def _zeilen() -> list[server_vergleich.Zeile]:
    with SessionLocal() as session:
        return server_vergleich.zeilen_bauen(session)[1]


def test_andere_tmdb_nummer_ist_eine_zeile_und_fehlt_nirgends(admin_client: TestClient) -> None:
    """Der Fall aus Issue #10: dieselbe IMDb-Nummer, verschiedene TMDB-Nummern."""
    _titel("plex", tmdb=1557, imdb="tt0126765", titel="23")
    _titel("emby", tmdb=1557, imdb="tt0126765", titel="23")
    _titel("jellyfin", tmdb=753588, imdb="tt0126765", titel="23")

    zeilen = _zeilen()
    assert len(zeilen) == 1
    zeile = zeilen[0]
    assert zeile.zuordnung == "imdb"
    assert not zeile.fehlt_irgendwo()
    assert zeile.zellen["jellyfin"].zustand == "andere_nummer"
    assert zeile.zellen["plex"].zustand == "da"
    assert zeile.zellen["emby"].zustand == "da"
    # Als Unterschied zaehlt er trotzdem: Jellyfin fuehrt die falsche Nummer.
    with SessionLocal() as session:
        assert server_vergleich.luecke_zaehlen(session) == 1


def test_fehlender_titel_fehlt_nur_auf_einer_seite(admin_client: TestClient) -> None:
    _titel("plex", tmdb=1)
    _titel("jellyfin", tmdb=1)
    _titel("plex", tmdb=2, titel="Nur auf Plex")

    zeilen = {z.titel: z for z in _zeilen()}
    assert zeilen["Nur auf Plex"].zellen["jellyfin"].zustand == "fehlt"
    assert zeilen["Nur auf Plex"].zellen["plex"].zustand == "da"
    assert not zeilen["Ein Film"].fehlt_irgendwo()
    with SessionLocal() as session:
        assert server_vergleich.luecke_zaehlen(session) == 1


def test_gleicher_name_und_jahr_mit_verschiedenen_nummern_bleiben_zwei(
    admin_client: TestClient,
) -> None:
    """Sonst verschmilzt ein fehlender Titel mit einem vorhandenen und faellt nicht auf."""
    _titel("plex", tmdb=1)
    _titel("plex", tmdb=2)
    _titel("jellyfin", tmdb=1)

    zeilen = _zeilen()
    assert len(zeilen) == 2
    assert sum(1 for z in zeilen if z.fehlt_irgendwo()) == 1


def test_titel_ohne_nummer_findet_ueber_name_und_jahr_zusammen(admin_client: TestClient) -> None:
    """Mit einem Jahr Spielraum - und als "ohne Kennung" markiert."""
    _titel("plex", tmdb=5, titel="Terrifier", jahr=2016)
    _titel("jellyfin", titel="Terrifier", jahr=2017)

    zeilen = _zeilen()
    assert len(zeilen) == 1
    assert zeilen[0].ohne_kennung
    assert zeilen[0].zuordnung == "titel"
    assert not zeilen[0].fehlt_irgendwo()


def test_mehrdeutiger_name_wird_nicht_geraten(admin_client: TestClient) -> None:
    """Passen Name und Jahr auf zwei Titel mit Nummer, bleibt die Zeile allein."""
    _titel("plex", tmdb=1, titel="Fargo", jahr=2020)
    _titel("plex", tmdb=2, titel="Fargo", jahr=2021)
    _titel("jellyfin", titel="Fargo", jahr=2020)

    assert len(_zeilen()) == 3


def test_serie_mit_anderer_tvdb_nummer_ist_uneinig(admin_client: TestClient) -> None:
    """Bei Serien erkennt Nexview ueber TVDB - eine andere TVDB-Nummer zaehlt."""
    _titel("plex", tvdb=100, tmdb=7, art=MediaType.tv, titel="Dark")
    _titel("emby", tvdb=101, tmdb=7, art=MediaType.tv, titel="Dark")
    _titel("jellyfin", tvdb=100, tmdb=7, art=MediaType.tv, titel="Dark")

    (zeile,) = _zeilen()
    assert zeile.zellen["emby"].zustand == "andere_nummer"
    assert zeile.zellen["plex"].zustand == "da"


def test_jahre_weit_auseinander_werden_markiert(admin_client: TestClient) -> None:
    _titel("plex", tmdb=9, jahr=2016)
    _titel("jellyfin", tmdb=9, jahr=2018)
    _titel("plex", tmdb=10, jahr=2023, titel="Grenzfall")
    _titel("jellyfin", tmdb=10, jahr=2024, titel="Grenzfall")

    markiert = [z.titel for z in _zeilen() if z.jahr_uneinig]
    assert markiert == ["Ein Film"]


def test_endpunkt_filtert_und_blaettert(admin_client: TestClient) -> None:
    for nummer in range(1, 13):
        _titel("plex", tmdb=nummer, titel=f"Film {nummer:02d}")
        if nummer % 2:
            _titel("jellyfin", tmdb=nummer, titel=f"Film {nummer:02d}")
    _titel("jellyfin", tmdb=99, titel="Nur Jellyfin")

    antwort = admin_client.get(PFAD).json()
    assert antwort["moeglich"] is True
    assert [s["anbieter"] for s in antwort["server"]] == ["plex", "jellyfin"]
    plex, jellyfin = antwort["server"]
    assert (plex["filme"], plex["fehlen"]) == (12, 1)
    assert (jellyfin["filme"], jellyfin["fehlen"]) == (7, 6)
    assert antwort["anzahl"]["unterschiede"] == 7
    assert antwort["anzahl"]["alle"] == 13
    assert antwort["gesamt"] == 7

    auf_jellyfin = admin_client.get(PFAD, params={"fehlt_auf": "jellyfin"}).json()
    assert auf_jellyfin["gesamt"] == 6
    assert all(z["zellen"]["jellyfin"]["zustand"] == "fehlt" for z in auf_jellyfin["zeilen"])

    gesucht = admin_client.get(PFAD, params={"ansicht": "alle", "suche": "film 0"}).json()
    assert gesucht["gesamt"] == 9

    seite = admin_client.get(PFAD, params={"ansicht": "alle", "pro_seite": 10, "seite": 2}).json()
    assert (seite["seiten"], seite["seite"], len(seite["zeilen"])) == (2, 2, 3)


def test_nur_in_arr_kommt_aus_dem_abgelegten_stand(admin_client: TestClient) -> None:
    _titel("plex", tmdb=1)
    with SessionLocal() as session:
        abgleich._schreiben(
            session,
            abgleich.Stand(
                moeglich=True,
                arr_ohne_server=1,
                arr_ohne_server_titel=[{"art": "movie", "nummer": 77, "titel": "Nie eingelesen"}],
            ),
        )

    antwort = admin_client.get(PFAD, params={"ansicht": "nur_arr"}).json()
    assert antwort["anzahl"]["nur_arr"] == 1
    (zeile,) = antwort["zeilen"]
    assert zeile["titel"] == "Nie eingelesen"
    assert zeile["zuordnung"] == "arr"
    assert zeile["zellen"]["plex"]["zustand"] == "fehlt"


def test_ohne_medienserver_nichts_zu_vergleichen(admin_client: TestClient) -> None:
    antwort = admin_client.get(PFAD).json()
    assert antwort["moeglich"] is False
    assert antwort["zeilen"] == []


def test_befund_ziele_nennen_eine_ansicht_die_es_gibt() -> None:
    """Die Woerter in den Zielen sind eine Zusage an die Oberflaeche."""
    for ansicht in ("unterschiede", "jahr", "ohne_kennung", "nur_arr"):
        ziel = befunde.vergleich_ziel(ansicht)
        assert ziel.startswith("/admin/stats?reiter=bibliothek&vergleich=")
        assert ziel.rsplit("=", 1)[1] in server_vergleich.ANSICHTEN


# --------------------------------------------------------------------------
# Pfade: ohne sie laesst sich ein Titel wie "2BA" nicht wiederfinden
# --------------------------------------------------------------------------


def test_jellyfin_nennt_datei_und_weitere_fassungen() -> None:
    from app.services.mediaserver.jellyfin import _als_werk

    werk = _als_werk(
        {
            "Name": "2BA",
            "Id": "abc",
            "Path": "/data/movies/2BA (2021)/2BA.mkv",
            "MediaSources": [
                {"Path": "/data/movies/2BA (2021)/2BA.mkv", "Size": 1},
                {"Path": "/data/movies4k/2BA (2021)/2BA.mkv", "Size": 2},
            ],
        },
        "movie",
    )
    assert werk is not None
    assert werk.paths == ("/data/movies/2BA (2021)/2BA.mkv", "/data/movies4k/2BA (2021)/2BA.mkv")


def test_jellyfin_serie_nennt_ihren_ordner() -> None:
    from app.services.mediaserver.jellyfin import _als_werk

    werk = _als_werk({"Name": "Dark", "Id": "s1", "Path": "/data/tv/Dark"}, "tv", set())
    assert werk is not None
    assert werk.paths == ("/data/tv/Dark",)


def test_plex_nennt_jede_datei_und_den_serienordner() -> None:
    from app.services.mediaserver.plex import _als_werk

    film = _als_werk(
        {
            "title": "2BA",
            "ratingKey": "1",
            "Media": [
                {"Part": [{"file": "/filme/2BA/teil1.mkv"}, {"file": "/filme/2BA/teil2.mkv"}]},
            ],
        },
        "movie",
    )
    serie = _als_werk(
        {"title": "Dark", "ratingKey": "2", "Location": [{"path": "/serien/Dark"}]}, "tv"
    )
    assert film is not None and serie is not None
    assert film.paths == ("/filme/2BA/teil1.mkv", "/filme/2BA/teil2.mkv")
    assert serie.paths == ("/serien/Dark",)


def test_pfade_kommen_bis_in_die_tabelle_und_sind_suchbar(admin_client: TestClient) -> None:
    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider="plex",
                media_type=MediaType.movie,
                guid="p1",
                tmdb_id=1,
                title="2BA",
                title_key="2ba",
                year=2021,
                file_paths="/filme/2BA (2021)/2BA.mkv",
            )
        )
        session.commit()
    _titel("jellyfin", tmdb=2, titel="Anderer Film")

    antwort = admin_client.get(PFAD, params={"ansicht": "alle", "suche": "(2021)/2ba"}).json()
    (zeile,) = antwort["zeilen"]
    assert zeile["zellen"]["plex"]["pfade"] == ["/filme/2BA (2021)/2BA.mkv"]
    assert zeile["zellen"]["jellyfin"]["pfade"] == []


def _plex_serie(schluessel: str, pfade: str | None = None) -> None:
    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider="plex",
                media_type=MediaType.tv,
                guid=f"plex-serie-{schluessel}",
                rating_key=schluessel,
                tvdb_id=int(schluessel),
                title="Dark",
                title_key="dark",
                year=2017,
                file_paths=pfade,
            )
        )
        session.commit()


def test_fehlender_serienordner_wird_nachgeschlagen_und_gemerkt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plex nennt Serienordner nur in der Detailabfrage - einmal fragen, dann merken."""
    from app.routers import analyse

    anfragen: list[str] = []

    class Plex:
        async def titel_pfade(self, schluessel: str) -> list[str]:
            anfragen.append(schluessel)
            return ["/serien/Dark"]

    monkeypatch.setattr(analyse, "media_server_for_setup", lambda _s, _a: Plex())
    _plex_serie("42")

    erste = admin_client.get(f"{PFAD}/pfade", params={"anbieter": "plex", "schluessel": "42"})
    assert erste.json() == {"pfade": ["/serien/Dark"]}
    zweite = admin_client.get(f"{PFAD}/pfade", params={"anbieter": "plex", "schluessel": "42"})
    assert zweite.json() == {"pfade": ["/serien/Dark"]}
    assert anfragen == ["42"]

    tabelle = admin_client.get(PFAD, params={"ansicht": "alle"}).json()
    assert tabelle["zeilen"][0]["zellen"]["plex"]["pfade"] == ["/serien/Dark"]


def test_unbekannter_titel_ist_404(admin_client: TestClient) -> None:
    antwort = admin_client.get(f"{PFAD}/pfade", params={"anbieter": "plex", "schluessel": "9"})
    assert antwort.status_code == 404


def test_plex_liest_den_ordner_aus_der_detailabfrage() -> None:
    import asyncio

    from app.services.mediaserver.plex import PlexServer

    server = PlexServer.__new__(PlexServer)

    async def antwort(pfad: str, params=None, token=None):
        assert pfad == "/library/metadata/42"
        return {"Metadata": [{"title": "Dark", "type": "show", "Location": [{"path": "/serien/Dark"}]}]}

    server._server = antwort  # type: ignore[method-assign]
    assert asyncio.run(server.titel_pfade("42")) == ["/serien/Dark"]


def _ohne_nummer(provider: str, titel: str, jahr: int, pfad: str) -> None:
    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider=provider,
                media_type=MediaType.movie,
                guid=f"{provider}:{pfad}",
                title=titel,
                title_key=server_vergleich.titel_schluessel(titel),
                year=jahr,
                file_paths=pfad,
            )
        )
        session.commit()


def test_nummer_im_pfad_fuehrt_unerkannte_zeile_zu_ihrem_titel(admin_client: TestClient) -> None:
    """Gemessen an Jellyfin: ``{tmdb-1900}`` im Ordner, Titel "2BA", Jahr 1900."""
    _titel("plex", tmdb=1900, titel="Traffic", jahr=2000)
    _ohne_nummer(
        "jellyfin",
        "2BA",
        1900,
        "/data/Movies/Traffic (2000) {tmdb-1900}/Traffic (2000) {tmdb-1900} - [Bluray-1080p]-2BA.mkv",
    )

    (zeile,) = _zeilen()
    assert zeile.zuordnung == "pfad"
    assert zeile.ohne_kennung
    assert not zeile.fehlt_irgendwo()
    # Fuer Nexview bleibt Jellyfin trotzdem uneinig: Der Server kennt die Nummer nicht.
    assert zeile.zellen["jellyfin"].zustand == "andere_nummer"


def test_jellyfin_und_emby_schreibweise_im_pfad_zaehlen_auch(admin_client: TestClient) -> None:
    _titel("plex", tmdb=5, titel="Dune", jahr=2021)
    _titel("plex", tmdb=6, titel="Heat", jahr=1995)
    _ohne_nummer("jellyfin", "x", 5, "/filme/Dune [tmdbid-5]/Dune.mkv")
    _ohne_nummer("emby", "y", 6, "/filme/Heat [tmdbid=6]/Heat.mkv")

    zeilen = {z.titel: z for z in _zeilen()}
    assert set(zeilen) == {"Dune", "Heat"}
    assert zeilen["Dune"].zellen["jellyfin"].zustand != "fehlt"
    assert zeilen["Heat"].zellen["emby"].zustand != "fehlt"


def test_jahr_im_namen_schlaegt_das_falsche_jahrfeld(admin_client: TestClient) -> None:
    """ "Alexander (2004)" mit Jahr 1966 findet ueber Name und Jahr aus dem Namen zusammen."""
    _titel("plex", tmdb=1, titel="Alexander", jahr=2004)
    _ohne_nummer("jellyfin", "Alexander (2004)", 1966, "/filme/Alexander/Alexander.mkv")

    (zeile,) = _zeilen()
    assert zeile.zuordnung == "titel"
    assert not zeile.fehlt_irgendwo()


def _mit_datei(provider: str, tmdb: int, titel: str, pfad: str, jahr: int = 2020) -> None:
    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider=provider,
                media_type=MediaType.movie,
                guid=f"{provider}:{pfad}",
                tmdb_id=tmdb,
                title=titel,
                title_key=server_vergleich.titel_schluessel(titel),
                year=jahr,
                file_paths=pfad,
            )
        )
        session.commit()


def test_datei_unter_anderem_titel_wird_benannt_statt_verschmolzen(
    admin_client: TestClient,
) -> None:
    """Gemessen: Jellyfin fuehrte die Datei von "8" als "The Hateful 8".

    Verschmelzen ueber die Datei haengte Plex' echtes "The Hateful 8" mit an -
    zwei Filme in einer Zeile. Die Zeile bleibt deshalb getrennt, die Zelle
    nennt den fremden Titel. Die Mount-Punkte unterscheiden sich absichtlich.
    """
    datei_8 = "8 (2020) {tmdb-605802}/8 (2020) {tmdb-605802} - [WEBDL-1080p].mkv"
    datei_h8 = "The Hateful 8 (2015) {tmdb-273248}/The Hateful 8 (2015).mkv"
    _mit_datei("plex", 605802, "8 - The Soul Collector", f"/media/Movies/{datei_8}")
    _mit_datei("jellyfin", 273248, "The Hateful 8", f"/data/Movies/{datei_8}", 2015)
    _mit_datei("plex", 273248, "The Hateful 8", f"/media/Movies/{datei_h8}", 2015)
    _mit_datei("jellyfin", 273248, "The Hateful 8", f"/data/Movies/{datei_h8}", 2015)

    zeilen = {z.titel: z for z in _zeilen()}
    assert set(zeilen) == {"8 - The Soul Collector", "The Hateful 8"}

    acht = zeilen["8 - The Soul Collector"].zellen["jellyfin"]
    assert acht.zustand == "anders_erkannt"
    assert acht.titel == "The Hateful 8"
    assert acht.tmdb == [273248]
    assert zeilen["The Hateful 8"].zellen["plex"].zustand == "da"

    antwort = admin_client.get(PFAD, params={"fehlt_auf": "jellyfin"}).json()
    assert [z["titel"] for z in antwort["zeilen"]] == ["8 - The Soul Collector"]
    assert antwort["anzahl"]["andere_nummer"] == 1
    jellyfin = next(s for s in antwort["server"] if s["anbieter"] == "jellyfin")
    assert jellyfin["fehlen"] == 1


def test_gleicher_dateiname_in_anderem_ordner_ist_nicht_dieselbe_datei(
    admin_client: TestClient,
) -> None:
    _mit_datei("plex", 1, "Film A", "/media/A (2020)/movie.mkv")
    _mit_datei("jellyfin", 2, "Film B", "/data/B (2020)/movie.mkv")

    zeilen = {z.titel: z for z in _zeilen()}
    assert zeilen["Film A"].zellen["jellyfin"].zustand == "fehlt"
