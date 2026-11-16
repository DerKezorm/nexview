"""Eine langsame Liste haelt keine andere Anfrage auf.

Am 16.11.2026 auf der Synology (ein Worker): nexcinema oeffnete Entdecken mit
fuenf Anfragen zugleich, und alle fuenf kamen nach 7,2 Sekunden zurueck, auch
``requests/quota`` und ``requests/mine``, die nur eine Zeile lesen. Die
Ereignisschleife stand: ``media._to_items`` las jeden der sechzig gemerkten
TMDB-Eintraege einer Liste zweimal, synchron, samt ``json.loads`` von rund
50 bis 100 KB je Eintrag. Seitdem liest ``cache`` in einem Arbeitsfaden.

Hier wird das Lesen kuenstlich langsam gemacht (``json.loads`` im
Zwischenspeicher schlaeft je Eintrag), und gemessen wird zweierlei: Kommt die
billige Anfrage zurueck, waehrend die Liste noch liest? Und wie lange steht
die Schleife hoechstens still?
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import cache, media

from .test_app_entdecken import _Tmdb

PFAD = "/api/app/1/discover/movie?list=popular&page=1"
#: So lange dauert hier das Entschluesseln eines gemerkten Eintrags.
JE_EINTRAG = 0.01
#: Laenger darf die Schleife nie am Stueck stehen.
HOECHSTENS = 0.15


class _LangsamesJson:
    """``json`` fuer ``cache``, nur ``loads`` braucht seine Zeit."""

    JSONDecodeError = json.JSONDecodeError

    @staticmethod
    def loads(text: str) -> Any:
        time.sleep(JE_EINTRAG)
        return json.loads(text)

    @staticmethod
    def dumps(wert: Any) -> str:
        return json.dumps(wert)


@pytest.fixture
def gemerkte_liste(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Eine Entdecken-Liste mit drei TMDB-Seiten, schon im Zwischenspeicher."""
    assert admin_client.put("/api/settings", json={"tmdb_api_key": "test-key"}).status_code == 200
    seiten = {seite: list(range(seite * 100, seite * 100 + 20)) for seite in (1, 2, 3)}
    monkeypatch.setattr(media, "_client", lambda settings, region=None: _Tmdb(seiten, 3))
    antwort = admin_client.get(PFAD)
    assert antwort.status_code == 200, antwort.text
    assert len(antwort.json()["items"]) == 60
    return {"Authorization": admin_client.headers["Authorization"]}


async def test_eine_billige_anfrage_wartet_nicht_auf_eine_liste(
    gemerkte_liste: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache, "json", _LangsamesJson())
    luecken: list[float] = []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://nexview.test", headers=gemerkte_liste
    ) as client:
        start = time.perf_counter()
        liste = asyncio.create_task(client.get(PFAD))

        async def sonde() -> None:
            while not liste.done():
                vorher = time.perf_counter()
                await asyncio.sleep(0.01)
                luecken.append(time.perf_counter() - vorher - 0.01)

        messung = asyncio.create_task(sonde())
        await asyncio.sleep(0.05)
        kontingent = await client.get("/api/app/1/requests/quota")
        kontingent_fertig = time.perf_counter() - start
        antwort = await liste
        liste_fertig = time.perf_counter() - start
        await messung

    assert antwort.status_code == 200, antwort.text
    assert kontingent.status_code == 200, kontingent.text
    # Sonst misst der Test nichts: Die Liste muss lange genug lesen.
    assert liste_fertig > 0.4, liste_fertig
    assert kontingent_fertig < liste_fertig - 0.2, (kontingent_fertig, liste_fertig)
    assert max(luecken) < HOECHSTENS, sorted(luecken, reverse=True)[:5]
