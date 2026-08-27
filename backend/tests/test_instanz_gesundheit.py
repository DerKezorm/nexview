"""Gesundheits-Probleme der Instanzen: einmal melden, nicht fluten.

Die drei Regeln aus ``services/instanz_gesundheit``:

* Ein **neues** Problem erzeugt genau eine Meldung an die Administratoren -
  ein Dauerproblem meldet sich nicht jede Runde wieder.
* Wiedererkannt wird an Quelle und Schwere, nicht am Text: Sonarr zaehlt in
  seine Texte Stunden hinein ("unavailable for 6 hours"), und je Aenderung
  neu zu melden waere genau die Flut, die das Gedaechtnis verhindern soll.
* Eine **stumme** Instanz laesst den gemerkten Stand stehen - stumm heisst
  unbekannt, nicht gesund.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Notification, NotificationType
from app.services import instanz_gesundheit
from app.services.arr import ArrClient, ArrError
from app.services.settings_service import load_settings, save_settings

RADARR = {"radarr_url": "http://127.0.0.1:7878", "radarr_api_key": "schluessel-r"}

DOWNLOAD_CLIENT_TOT = {
    "source": "DownloadClientStatusCheck",
    "type": "error",
    "message": "All download clients are unavailable due to failures",
}


def _radarr():
    with SessionLocal() as db:
        save_settings(db, RADARR)
        return load_settings(db)


def _antworten(monkeypatch, probleme) -> None:
    async def gesundheit(self):  # noqa: ANN001 - Signatur der echten Methode
        if isinstance(probleme, ArrError):
            raise probleme
        return list(probleme)

    monkeypatch.setattr(ArrClient, "gesundheit", gesundheit)


def _meldungen() -> int:
    with SessionLocal() as db:
        return len(
            list(
                db.scalars(
                    select(Notification).where(
                        Notification.type == NotificationType.instanz_gesundheit
                    )
                )
            )
        )


def _stand() -> list:
    with SessionLocal() as db:
        zeile = instanz_gesundheit.eintrag(db, "radarr-standard")
        return list(zeile.stand or []) if zeile else []


@pytest.mark.anyio
async def test_neues_problem_meldet_genau_einmal(admin_client, monkeypatch) -> None:
    settings = _radarr()
    _antworten(monkeypatch, [DOWNLOAD_CLIENT_TOT])

    with SessionLocal() as db:
        await instanz_gesundheit.pruefen(db, settings)
    assert _meldungen() == 1
    assert _stand()[0]["typ"] == "error"

    # Zweite Runde, dasselbe Problem: keine Flut.
    with SessionLocal() as db:
        await instanz_gesundheit.pruefen(db, settings)
    assert _meldungen() == 1


@pytest.mark.anyio
async def test_geaenderter_text_ist_kein_neues_problem(admin_client, monkeypatch) -> None:
    settings = _radarr()
    _antworten(monkeypatch, [DOWNLOAD_CLIENT_TOT])
    with SessionLocal() as db:
        await instanz_gesundheit.pruefen(db, settings)

    _antworten(
        monkeypatch,
        [{**DOWNLOAD_CLIENT_TOT, "message": "unavailable for 6 hours now"}],
    )
    with SessionLocal() as db:
        await instanz_gesundheit.pruefen(db, settings)

    assert _meldungen() == 1
    # Der Text selbst wird trotzdem aktuell gehalten - fuer die Anzeige.
    assert "6 hours" in _stand()[0]["text"]


@pytest.mark.anyio
async def test_verschwinden_ist_still(admin_client, monkeypatch) -> None:
    settings = _radarr()
    _antworten(monkeypatch, [DOWNLOAD_CLIENT_TOT])
    with SessionLocal() as db:
        await instanz_gesundheit.pruefen(db, settings)

    _antworten(monkeypatch, [])
    with SessionLocal() as db:
        await instanz_gesundheit.pruefen(db, settings)

    assert _stand() == []
    assert _meldungen() == 1

    # Kommt es wieder, ist es wieder eine Meldung wert.
    _antworten(monkeypatch, [DOWNLOAD_CLIENT_TOT])
    with SessionLocal() as db:
        await instanz_gesundheit.pruefen(db, settings)
    assert _meldungen() == 2


@pytest.mark.anyio
async def test_stumme_instanz_laesst_den_stand_stehen(admin_client, monkeypatch) -> None:
    settings = _radarr()
    _antworten(monkeypatch, [DOWNLOAD_CLIENT_TOT])
    with SessionLocal() as db:
        await instanz_gesundheit.pruefen(db, settings)

    _antworten(monkeypatch, ArrError("Radarr antwortet nicht.", code="arr_timeout"))
    with SessionLocal() as db:
        await instanz_gesundheit.pruefen(db, settings)

    # Stumm heisst unbekannt, nicht gesund: Stand und Meldungszahl unveraendert.
    assert len(_stand()) == 1
    assert _meldungen() == 1


def test_verbindungsleuchte_meldet_erreichbar(arr_client, monkeypatch) -> None:
    """Die Statusleuchte der Kacheln: live gefragt, mit Version."""

    async def status(self, timeout=None):  # noqa: ANN001 - Signatur der echten Methode
        return {"version": "6.3.0"}

    monkeypatch.setattr(ArrClient, "system_status", status)

    antwort = arr_client.get("/api/settings/instanzen/verbindung")
    assert antwort.status_code == 200, antwort.text
    zeilen = {z["kennung"]: z for z in antwort.json()["instanzen"]}
    assert zeilen["radarr-standard"]["erreichbar"] is True
    assert zeilen["radarr-standard"]["version"] == "6.3.0"


def test_verbindungsleuchte_sagt_ehrlich_nicht_erreichbar(arr_client) -> None:
    """Port 9 lehnt sofort ab - die Leuchte wird rot, die Antwort kommt schnell."""
    antwort = arr_client.get("/api/settings/instanzen/verbindung")
    assert antwort.status_code == 200, antwort.text
    assert all(not z["erreichbar"] for z in antwort.json()["instanzen"])


def test_diensteseite_zeigt_die_probleme(admin_client, monkeypatch) -> None:
    import asyncio

    settings = _radarr()
    _antworten(monkeypatch, [DOWNLOAD_CLIENT_TOT])
    with SessionLocal() as db:
        asyncio.run(instanz_gesundheit.pruefen(db, settings))

    antwort = admin_client.get("/api/settings/instanzen/gesundheit")
    assert antwort.status_code == 200, antwort.text
    zeile = next(
        z for z in antwort.json()["instanzen"] if z["kennung"] == "radarr-standard"
    )
    assert zeile["probleme"] == [
        {"typ": "error", "text": "All download clients are unavailable due to failures"}
    ]
