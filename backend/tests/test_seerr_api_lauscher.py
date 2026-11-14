"""Der zweite Port: oeffnet, schliesst, und ein belegter Port reisst nichts mit.

⚠️ Hier wird wirklich ein Port geoeffnet, auf 127.0.0.1 und mit einer Nummer,
die das Betriebssystem frei gibt. Die anderen Tests der Schicht rufen die App
direkt; nur hier geht es um den Lauscher selbst.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from app.seerr_api import lauscher


@pytest.fixture(autouse=True)
def nur_lokal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXVIEW_SEERR_HOST", "127.0.0.1")


def _freier_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def test_oeffnet_antwortet_und_schliesst() -> None:
    port = _freier_port()
    ohr = lauscher.Lauscher()
    await ohr.anpassen(True, port)
    try:
        assert lauscher.zustand() == lauscher.Zustand(laeuft=True, port=port, fehler=None)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            assert (await client.get("/api/v1/status")).status_code == 200
            # Auf diesem Port gibt es nur die Seerr-Adressen.
            assert (await client.get("/api/settings")).status_code == 404
            assert (await client.get("/api/v1/me")).status_code == 404
    finally:
        await ohr.anpassen(False, port)

    assert lauscher.zustand().laeuft is False
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(f"http://127.0.0.1:{port}/api/v1/status")


async def test_portwechsel_zieht_um() -> None:
    erster, zweiter = _freier_port(), _freier_port()
    ohr = lauscher.Lauscher()
    try:
        await ohr.anpassen(True, erster)
        await ohr.anpassen(True, zweiter)
        assert lauscher.zustand().port == zweiter
        async with httpx.AsyncClient() as client:
            assert (await client.get(f"http://127.0.0.1:{zweiter}/api/v1/status")).status_code == 200
            with pytest.raises(httpx.ConnectError):
                await client.get(f"http://127.0.0.1:{erster}/api/v1/status")
    finally:
        await ohr.stoppen()


async def test_belegter_port_wird_gemeldet_statt_alles_zu_beenden() -> None:
    besetzt = socket.socket()
    # Unter Windows liesse SO_REUSEADDR (das uvicorn setzt) sonst einen
    # zweiten Bind auf denselben Port zu, und die Probe maesse nichts.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        besetzt.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    besetzt.bind(("127.0.0.1", 0))
    besetzt.listen()
    port = int(besetzt.getsockname()[1])
    ohr = lauscher.Lauscher()
    try:
        # Kein SystemExit, keine Ausnahme: nur der Zustand sagt es.
        await ohr.anpassen(True, port)
        assert lauscher.zustand() == lauscher.Zustand(laeuft=False, port=port, fehler="port_unavailable")
        # Und der zweite Versuch wartet, statt alle fuenf Sekunden das Log zu fuellen.
        await ohr.anpassen(True, port)
        assert lauscher.zustand().fehler == "port_unavailable"
    finally:
        besetzt.close()
        await ohr.stoppen()


async def test_schleife_folgt_der_einstellung(admin_client: TestClient) -> None:
    port = _freier_port()
    stop = asyncio.Event()
    aufgabe = asyncio.create_task(lauscher.run_forever(stop))
    try:
        assert admin_client.put(
            "/api/settings", json={"seerr_api_enabled": True, "seerr_api_port": port}
        ).status_code == 200
        # Kein eigenes ``aufwecken()``: Das Speichern weckt aus dem
        # Arbeitsthread des TestClient. Drei Sekunden sind weniger als ein
        # Takt (fuenf) - geht es schneller, hat das Wecken gewirkt.
        for _ in range(30):
            if lauscher.zustand().laeuft:
                break
            await asyncio.sleep(0.1)
        assert lauscher.zustand() == lauscher.Zustand(laeuft=True, port=port, fehler=None)

        stand = admin_client.get("/api/settings").json()
        assert stand["seerr_api_enabled"] is True
        assert stand["seerr_api_port"] == port
        assert stand["seerr_api_listening"] is True

        assert admin_client.put("/api/settings", json={"seerr_api_enabled": False}).status_code == 200
        # Kein eigenes ``aufwecken()``: Das Speichern weckt aus dem
        # Arbeitsthread des TestClient. Drei Sekunden sind weniger als ein
        # Takt (fuenf) - geht es schneller, hat das Wecken gewirkt.
        for _ in range(30):
            if not lauscher.zustand().laeuft:
                break
            await asyncio.sleep(0.1)
        assert lauscher.zustand().laeuft is False
    finally:
        stop.set()
        await asyncio.wait_for(aufgabe, timeout=10)


def test_port_unter_1024_wird_abgelehnt(admin_client: TestClient) -> None:
    assert admin_client.put("/api/settings", json={"seerr_api_port": 80}).status_code == 422
    assert admin_client.get("/api/settings").json()["seerr_api_port"] == lauscher.STANDARD_PORT
