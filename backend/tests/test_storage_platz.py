"""Freier Platz: dieselbe Platte darf nicht zweimal gezaehlt werden.

Filme, Serien und die beiden 4K-Instanzen liegen fast immer auf **einem**
Datentraeger. Vier Ordner mal 83 TB zu addieren waere grober Unsinn - und genau
das ist im Betrieb passiert: gemeldet wurde "167,37 TB frei auf 2
Datentraegern" bei genau einer Platte.

Die Ursache war die Unterscheidung ueber den **freien** Platz. Zwei Instanzen
lesen ihn zu verschiedenen Zeitpunkten; sobald etwas geschrieben wird, weichen
sie um ein paar Bytes ab - und aus einer Platte werden zwei.

Unterschieden wird deshalb ueber die **Gesamtgroesse**. Die aendert sich nicht,
waehrend geschrieben wird.
"""

from __future__ import annotations

import pytest

from app.services import library, storage
from app.services.settings_service import AppSettings, load_settings, save_settings
from app.db import SessionLocal

TB = 1024**4


@pytest.fixture
def settings() -> AppSettings:
    with SessionLocal() as db:
        save_settings(
            db,
            {
                "radarr_url": "http://127.0.0.1:10",
                "radarr_api_key": "test",
                "sonarr_url": "http://127.0.0.1:11",
                "sonarr_api_key": "test",
            },
        )
        return load_settings(db)


def _antworten(monkeypatch, *, ordner: dict[str, list], punkte: dict[str, list]) -> None:
    """Radarr und Sonarr vortaeuschen - je Medienart eigene Antworten."""

    async def optionen(_s, media_type: str, _tier: str = "standard") -> dict:
        return {"quality_profiles": [], "root_folders": ordner.get(media_type, [])}

    async def traeger(_s, media_type: str, _tier: str = "standard") -> list:
        return punkte.get(media_type, [])

    monkeypatch.setattr(library, "options", optionen)
    monkeypatch.setattr(library, "datentraeger", traeger)


# --- Der gemeldete Fehler --------------------------------------------------


@pytest.mark.anyio
async def test_abweichende_bytes_machen_aus_einer_platte_keine_zwei(
    monkeypatch, settings
) -> None:
    """**Der Fehler aus dem Betrieb.**

    Radarr meldet 83,6855 TB frei, Sonarr eine Sekunde spaeter zwei Bytes
    weniger - dieselbe Platte. Ueber den freien Platz unterschieden waeren das
    zwei Traeger und die doppelte Summe.
    """
    _antworten(
        monkeypatch,
        ordner={
            "movie": [{"path": "/data/Movies"}],
            "tv": [{"path": "/data/TV-Shows"}],
        },
        punkte={
            "movie": [{"path": "/data", "free_space": 83 * TB, "total_space": 125 * TB}],
            "tv": [{"path": "/data", "free_space": 83 * TB - 2, "total_space": 125 * TB}],
        },
    )

    frei, anzahl = await storage.freier_platz(settings)
    assert anzahl == 1
    # Die kleinere der beiden Meldungen - im Zweifel untertreiben.
    assert frei == 83 * TB - 2


@pytest.mark.anyio
async def test_container_einhaengepunkte_zaehlen_nicht_mehrfach(
    monkeypatch, settings
) -> None:
    """Ein Container meldet ``/``, ``/config`` und ``/data`` nebeneinander.

    Alle drei "passen" zu ``/data/Movies`` und zeigen im Container auf dasselbe
    Dateisystem. Ungefiltert summiert waere das dreimal dieselbe Platte.
    """
    _antworten(
        monkeypatch,
        ordner={"movie": [{"path": "/data/Movies"}]},
        punkte={
            "movie": [
                {"path": "/", "free_space": 83 * TB, "total_space": 125 * TB},
                {"path": "/config", "free_space": 83 * TB, "total_space": 125 * TB},
                {"path": "/data", "free_space": 83 * TB, "total_space": 125 * TB},
            ]
        },
    )

    frei, anzahl = await storage.freier_platz(settings)
    assert anzahl == 1
    assert frei == 83 * TB


# --- Wenn es wirklich mehrere sind -----------------------------------------


@pytest.mark.anyio
async def test_zwei_echte_traeger_werden_addiert(monkeypatch, settings) -> None:
    """Verschiedene Platten haben verschiedene Gesamtgroessen."""
    _antworten(
        monkeypatch,
        ordner={
            "movie": [{"path": "/filme"}],
            "tv": [{"path": "/serien"}],
        },
        punkte={
            "movie": [{"path": "/filme", "free_space": 10 * TB, "total_space": 20 * TB}],
            "tv": [{"path": "/serien", "free_space": 4 * TB, "total_space": 8 * TB}],
        },
    )

    frei, anzahl = await storage.freier_platz(settings)
    assert anzahl == 2
    assert frei == 14 * TB


# --- Wenn die Auskunft fehlt -----------------------------------------------


@pytest.mark.anyio
async def test_ohne_passenden_einhaengepunkt_faellt_der_ordner_weg(
    monkeypatch, settings
) -> None:
    """Lieber eine Zahl weniger als eine erfundene.

    Ohne Gesamtgroesse laesst sich nicht sagen, ob es dieselbe Platte ist -
    und wer das raet, landet wieder bei der doppelten Summe.
    """
    _antworten(
        monkeypatch,
        ordner={"movie": [{"path": "/woanders/Movies"}]},
        punkte={"movie": [{"path": "/data", "free_space": 83 * TB, "total_space": 125 * TB}]},
    )

    assert await storage.freier_platz(settings) == (0, 0)


@pytest.mark.anyio
async def test_ohne_diskspace_kommt_keine_zahl(monkeypatch, settings) -> None:
    """Aeltere Fassungen oder eine stumme Instanz duerfen nichts kaputtmachen."""
    _antworten(
        monkeypatch,
        ordner={"movie": [{"path": "/data/Movies"}]},
        punkte={"movie": []},
    )

    assert await storage.freier_platz(settings) == (0, 0)


@pytest.mark.anyio
async def test_ohne_eingerichtete_instanz_bleibt_es_bei_null(monkeypatch) -> None:
    with SessionLocal() as db:
        save_settings(db, {"radarr_url": "", "radarr_api_key": "", "sonarr_url": "", "sonarr_api_key": ""})
        leer = load_settings(db)

    assert await storage.freier_platz(leer) == (0, 0)
