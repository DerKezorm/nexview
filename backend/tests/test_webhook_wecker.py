"""Das Warten des Takt-Laeufers: Wer darf es verkuerzen, und wer nicht?

Getestet wird ``status_poller._bis_zum_naechsten_durchgang`` mit kleinen
Zeiten und grosszuegigen Schwellen - hier zaehlt die **Rangfolge** der drei
Ausgaenge (stop vor Weckruf vor Takt), nicht die Millisekunde. Die Werte im
Betrieb (Takt 120 s, Mindestabstand 10 s) stehen im ``status_poller``.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.services import status_poller, webhooks


@pytest.fixture(autouse=True)
def _frischer_wecker(monkeypatch):
    """Je Test ein neues Signal.

    ⚠️ ``asyncio.Event`` bindet sich an die erste Ereignisschleife, die darauf
    wartet. Im Betrieb gibt es genau eine - in Tests hat aber jeder seine
    eigene, und das Warten auf ein fremdgebundenes Signal stirbt sofort mit
    ``RuntimeError``. Genau so gefunden: Der Takt-Test kehrte nach 0,2 ms
    zurueck statt nach dem Takt.
    """
    monkeypatch.setattr(webhooks, "_weckruf", asyncio.Event())


@pytest.mark.anyio
async def test_weckruf_verkuerzt_das_warten() -> None:
    stop = asyncio.Event()

    async def anrufen() -> None:
        await asyncio.sleep(0.05)
        webhooks.wecken()

    anruf = asyncio.create_task(anrufen())
    start = time.monotonic()
    await status_poller._bis_zum_naechsten_durchgang(
        stop, wartezeit=30.0, frueheste_weckung=0.0
    )
    dauer = time.monotonic() - start
    await anruf

    assert dauer < 5.0, "Der Weckruf haette das Warten abkuerzen muessen"
    # Verbraucht: Der folgende Rundgang deckt diesen Anruf ab.
    assert not webhooks.weckruf().is_set()


@pytest.mark.anyio
async def test_taube_phase_buendelt_draengelnde_anrufe() -> None:
    """Ein schon gesetztes Signal wirkt erst nach dem Mindestabstand -
    so wird aus einem Anruf-Gewitter ein Rundgang, nicht fuenfzig."""
    stop = asyncio.Event()
    webhooks.wecken()

    start = time.monotonic()
    await status_poller._bis_zum_naechsten_durchgang(
        stop, wartezeit=30.0, frueheste_weckung=0.3
    )
    dauer = time.monotonic() - start

    assert dauer >= 0.25, "Die taube Phase wurde nicht eingehalten"
    assert dauer < 5.0, "Nach der tauben Phase muss der Weckruf sofort wirken"
    assert not webhooks.weckruf().is_set()


@pytest.mark.anyio
async def test_stop_gewinnt_auch_in_der_tauben_phase() -> None:
    stop = asyncio.Event()
    stop.set()

    start = time.monotonic()
    await status_poller._bis_zum_naechsten_durchgang(
        stop, wartezeit=30.0, frueheste_weckung=30.0
    )
    dauer = time.monotonic() - start

    assert dauer < 5.0, "Das Herunterfahren darf auf keinen Abstand warten"


@pytest.mark.anyio
async def test_ohne_weckruf_endet_das_warten_mit_dem_takt() -> None:
    stop = asyncio.Event()

    start = time.monotonic()
    await status_poller._bis_zum_naechsten_durchgang(
        stop, wartezeit=0.2, frueheste_weckung=0.0
    )
    dauer = time.monotonic() - start

    assert dauer >= 0.15, "Ohne Weckruf gilt der Takt"


@pytest.mark.anyio
async def test_backoff_deckelt_die_taube_phase_auf_die_wartezeit() -> None:
    """Ist der Mindestabstand laenger als der Takt, endet das Warten trotzdem
    mit dem Takt - sonst ueberholte die Entprellung den Rueckfall selbst."""
    stop = asyncio.Event()
    webhooks.wecken()

    start = time.monotonic()
    await status_poller._bis_zum_naechsten_durchgang(
        stop, wartezeit=0.2, frueheste_weckung=5.0
    )
    dauer = time.monotonic() - start

    assert dauer < 3.0, "Die taube Phase darf nie laenger sein als der Takt"
    assert not webhooks.weckruf().is_set()
