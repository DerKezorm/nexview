"""Langsame Aufrufe stehen im Protokoll (Rundgang 2, R2-5).

Gemessen an der Live-Instanz am 25.09.2026: ``/api/admin/dashboard`` brauchte
16,9 und 16,4 Sekunden, spaeter 0,3 bis 1,4. Die Dauer stand nur auf DEBUG im
Protokoll, also nirgends; die Ursache liess sich hinterher nicht mehr messen.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app import middleware


def test_ein_langsamer_aufruf_wird_gewarnt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(middleware, "LANGSAM_MS", 0)
    with caplog.at_level(logging.WARNING, logger="nexview.api"):
        admin_client.get("/api/config")
    zeilen = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(z.startswith("Slow request: GET /api/config -> 200 in ") for z in zeilen), zeilen


def test_ein_schneller_aufruf_bleibt_still(
    admin_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="nexview.api"):
        admin_client.get("/api/config")
    assert not [r for r in caplog.records if "Slow request" in r.getMessage()]


def test_die_schwelle_liegt_bei_drei_sekunden() -> None:
    assert middleware.LANGSAM_MS == 3000
