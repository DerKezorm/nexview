"""Der Abgleich der Quellen: wo Radarr, Medienserver und Nexview auseinandergehen.

⚠️ **Der Grundgedanke jedes Tests hier ist die Gegenprobe.** Ein Abgleich, der
zu viel meldet, ist schlimmer als keiner: Er behauptet Fehler, wo nur die
normale Verzoegerung zwischen zwei Messungen liegt, und nach dem dritten
falschen Alarm sieht niemand mehr hin.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaServerLibraryItem, MediaType
from app.services import abgleich, library
from app.services.radarr import LibraryEntry as FilmEintrag
from app.services.settings_service import load_settings


def _server_titel(
    provider: str,
    *,
    tmdb: int | None = None,
    tvdb: int | None = None,
    jahr: int | None = 2020,
    titel: str = "Ein Film",
    art: MediaType = MediaType.movie,
) -> None:
    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider=provider,
                media_type=art,
                guid=f"{provider}:{tmdb or tvdb or titel}:{jahr}",
                tmdb_id=tmdb,
                tvdb_id=tvdb,
                title=titel,
                title_key=titel.lower(),
                year=jahr,
            )
        )
        session.commit()


def _radarr_hat(monkeypatch: pytest.MonkeyPatch, *tmdb_ids: int) -> None:
    """Radarr fuehrt diese Filme, jeweils mit Datei."""

    async def bestand(_settings, tier="standard"):
        if tier != "standard":
            return {}
        return {
            kennung: FilmEintrag(
                arr_id=kennung, has_file=True, monitored=True, title=f"Film {kennung}"
            )
            for kennung in tmdb_ids
        }

    monkeypatch.setattr(library, "movie_library", bestand)


async def _messen() -> abgleich.Stand:
    with SessionLocal() as session:
        return await abgleich.messen(session, load_settings(session))


async def test_ohne_medienserver_wird_nicht_verglichen(arr_client: TestClient) -> None:
    """Ein leerer Stand ist die ehrliche Antwort - nicht "alles in Ordnung"."""
    stand = await _messen()
    assert stand.moeglich is False
    assert stand.arr_ohne_server == 0


async def test_datei_ohne_eintrag_im_server_faellt_auf(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _radarr_hat(monkeypatch, 1, 2, 3)
    _server_titel("plex", tmdb=1)

    stand = await _messen()
    assert stand.moeglich is True
    assert stand.arr_ohne_server == 2


async def test_ueberwacht_ohne_datei_ist_kein_widerspruch(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Titel, der noch gar nicht geladen ist, fehlt voellig zu Recht."""

    async def bestand(_settings, tier="standard"):
        if tier != "standard":
            return {}
        return {
            7: FilmEintrag(arr_id=7, has_file=False, monitored=True, title="Kommt noch")
        }

    monkeypatch.setattr(library, "movie_library", bestand)
    _server_titel("plex", tmdb=99)

    stand = await _messen()
    assert stand.arr_ohne_server == 0


async def test_titel_ohne_kennung_wird_gezaehlt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _radarr_hat(monkeypatch)
    _server_titel("plex", tmdb=None, tvdb=None, titel="Unbekannt")
    _server_titel("plex", tmdb=5, titel="Erkannt")

    stand = await _messen()
    assert stand.nicht_erkannt == 1


async def test_doppelte_nur_innerhalb_eines_anbieters(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drei Server auf derselben Mediathek sind keine Dubletten.

    ⚠️ Genau hier lag die Falle: Ueber alle Anbieter gezaehlt haette jeder
    Titel eines Parallelbetriebs als "doppelt" gegolten - bei drei Servern
    also die ganze Bibliothek.
    """
    _radarr_hat(monkeypatch)
    _server_titel("plex", tmdb=10, titel="Einmal")
    _server_titel("jellyfin", tmdb=10, titel="Einmal")
    _server_titel("emby", tmdb=10, titel="Einmal")

    stand = await _messen()
    assert stand.doppelt == 0

    _server_titel("plex", tmdb=10, titel="Einmal", jahr=2021)
    stand = await _messen()
    assert stand.doppelt == 1


async def test_jahres_widerspruch_zwischen_anbietern(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _radarr_hat(monkeypatch)
    _server_titel("plex", tmdb=42, jahr=2023, titel="Der Streitfall")
    _server_titel("jellyfin", tmdb=42, jahr=2025, titel="Der Streitfall")

    stand = await _messen()
    assert stand.jahr_widerspruch == 1
    assert any("Der Streitfall" in b for b in stand.beispiele["jahr_widerspruch"])


async def test_ein_jahr_abweichung_ist_erlaubt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Festivalstart und Kinostart fallen oft in verschiedene Jahre."""
    _radarr_hat(monkeypatch)
    _server_titel("plex", tmdb=42, jahr=2023, titel="Grenzfall")
    _server_titel("jellyfin", tmdb=42, jahr=2024, titel="Grenzfall")

    stand = await _messen()
    assert stand.jahr_widerspruch == 0


async def test_ein_einziger_anbieter_streitet_mit_niemandem(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mit einem Server gibt es keinen Jahresvergleich - und keine Luecke.

    Die meisten Haeuser haben genau einen. Eine Pruefung, die dort etwas
    meldet, waere in der Mehrheit der Installationen falsch.
    """
    _radarr_hat(monkeypatch)
    _server_titel("plex", tmdb=42, jahr=2023)
    _server_titel("plex", tmdb=43, jahr=2019)

    stand = await _messen()
    assert stand.jahr_widerspruch == 0
    assert stand.anbieter_luecke == 0
    assert len(stand.je_anbieter) == 1


async def test_uneinige_anbieter_werden_gezaehlt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _radarr_hat(monkeypatch)
    _server_titel("plex", tmdb=1)
    _server_titel("plex", tmdb=2)
    _server_titel("jellyfin", tmdb=1)

    stand = await _messen()
    # Nummer 2 kennt nur einer von beiden.
    assert stand.anbieter_luecke == 1
    assert stand.je_anbieter == {"plex": 2, "jellyfin": 1}


async def test_stand_ueberlebt_das_ablegen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemessen wird stuendlich, gelesen bei jedem Aufruf - dazwischen liegt
    eine Zeile in ``settings``."""
    _radarr_hat(monkeypatch, 1, 2, 3)
    _server_titel("plex", tmdb=1)
    await _messen()

    with SessionLocal() as session:
        gelesen = abgleich.lesen(session)
    assert gelesen.moeglich is True
    assert gelesen.arr_ohne_server == 2


async def test_kaputter_gespeicherter_stand_wirft_nicht(
    admin_client: TestClient,
) -> None:
    """Lieber nichts melden als die Seite mitnehmen."""
    from app.models import Setting

    with SessionLocal() as session:
        session.add(Setting(key=abgleich.SCHLUESSEL, value="{kein json"))
        session.commit()
        assert abgleich.lesen(session).moeglich is False
