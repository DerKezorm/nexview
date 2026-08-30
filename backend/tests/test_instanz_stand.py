"""Der Messdienst: was gemerkt wird - und was sich dabei NICHT bewegen darf.

Der Kern ist ``erreichbar_seit``. Es beantwortet "seit wann ist die Instanz in
diesem Zustand" und ist damit der Unterschied zwischen "startet gerade neu"
und "seit gestern Abend weg". Wandert es bei jeder Messung mit, heisst es
immer "seit gerade eben" und die ganze Angabe ist wertlos - ein Fehler, den
man in der Oberflaeche nie sieht, weil dort einfach eine plausible kleine Zahl
steht.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import InstanzStand
from app.services import instanz_stand
from app.services.arr import ArrClient, ArrError
from app.services.settings_service import load_settings


def _antwortet(version: str = "6.3.0"):
    async def system_status(self, timeout=None):
        return {"appName": "Radarr", "version": version}

    return system_status


def _stumm():
    async def system_status(self, timeout=None):
        raise ArrError("keine Verbindung")

    return system_status


async def _messen(monkeypatch: pytest.MonkeyPatch, antwort, *, voll: bool = False):
    monkeypatch.setattr(ArrClient, "system_status", antwort)
    with SessionLocal() as session:
        await instanz_stand.messen(session, load_settings(session), voll=voll)


def _zeile(kennung: str = "radarr-standard") -> InstanzStand | None:
    with SessionLocal() as session:
        return instanz_stand.eintrag(session, kennung)


async def test_erreichbare_instanz_wird_gemerkt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _messen(monkeypatch, _antwortet("6.3.0"))

    zeile = _zeile()
    assert zeile is not None
    assert zeile.erreichbar is True
    assert zeile.version == "6.3.0"
    assert zeile.gemessen_am is not None


async def test_stumme_instanz_wird_gemerkt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _messen(monkeypatch, _stumm())

    zeile = _zeile()
    assert zeile is not None
    assert zeile.erreichbar is False


async def test_seit_wandert_nicht_bei_jeder_messung(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Der wichtigste Test dieser Datei.**

    Bleibt der Zustand gleich, muss der Zeitpunkt stehenbleiben. Sonst meldet
    ein seit Tagen toter Radarr ewig "seit einer Minute nicht erreichbar" -
    und niemand merkt, dass die Angabe erfunden ist.
    """
    await _messen(monkeypatch, _stumm())
    erste = _zeile()
    assert erste is not None
    zuerst = erste.erreichbar_seit

    # Den Zeitpunkt kuenstlich zurueckdatieren, damit ein Mitwandern auffiele.
    with SessionLocal() as session:
        zeile = instanz_stand.eintrag(session, "radarr-standard")
        assert zeile is not None
        zeile.erreichbar_seit = zuerst - timedelta(hours=5)
        alt = zeile.erreichbar_seit
        session.commit()

    await _messen(monkeypatch, _stumm())

    zweite = _zeile()
    assert zweite is not None
    assert zweite.erreichbar_seit == alt
    # Nachgesehen wurde trotzdem - die beiden Zeitpunkte sind verschiedene Dinge.
    assert zweite.gemessen_am is not None
    assert zweite.gemessen_am > alt


async def test_seit_springt_beim_wechsel(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _messen(monkeypatch, _antwortet())
    with SessionLocal() as session:
        zeile = instanz_stand.eintrag(session, "radarr-standard")
        assert zeile is not None
        zeile.erreichbar_seit = zeile.erreichbar_seit - timedelta(hours=5)
        alt = zeile.erreichbar_seit
        session.commit()

    await _messen(monkeypatch, _stumm())

    zweite = _zeile()
    assert zweite is not None
    assert zweite.erreichbar_seit > alt


async def test_stumme_instanz_behaelt_ihre_fassung(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leer waere die Behauptung, sie haette keine Fassung."""
    await _messen(monkeypatch, _antwortet("6.3.0"))
    await _messen(monkeypatch, _stumm())

    zeile = _zeile()
    assert zeile is not None
    assert zeile.version == "6.3.0"


async def test_kurze_messung_ruehrt_die_messwerte_nicht_an(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne ``voll`` bleibt alles Stuendliche stehen.

    Sonst waeren Plattenstand und Warteschlange nach jeder zweiten Minute
    geloescht, und die Befunde flackerten im Zwei-Minuten-Takt.
    """
    with SessionLocal() as session:
        session.add(
            InstanzStand(
                kennung="radarr-standard",
                erreichbar=True,
                messwerte={"traeger": [{"gesamt": 100, "frei": 5, "ordner": ["/x"]}]},
            )
        )
        session.commit()

    await _messen(monkeypatch, _antwortet())

    zeile = _zeile()
    assert zeile is not None
    assert zeile.messwerte is not None
    assert zeile.messwerte["traeger"][0]["gesamt"] == 100


async def test_eine_kaputte_instanz_nimmt_die_anderen_nicht_mit(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst ist ausgerechnet bei einem Ausfall das ganze Dashboard leer."""
    aufrufe: list[str] = []

    async def launisch(self, timeout=None):
        aufrufe.append(self.label)
        if len(aufrufe) == 1:
            raise RuntimeError("etwas ganz anderes")
        return {"version": "4.0.19"}

    monkeypatch.setattr(ArrClient, "system_status", launisch)
    with SessionLocal() as session:
        settings = load_settings(session)
        await instanz_stand.messen(session, settings, voll=False)
        anzahl = len(settings.arr_instanzen())

    assert len(aufrufe) == anzahl
    # Die uebrigen sind trotzdem gemessen worden.
    with SessionLocal() as session:
        assert len(instanz_stand.alle(session)) == anzahl - 1
