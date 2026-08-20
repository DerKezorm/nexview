"""Eine Instanz, die nicht antwortet, darf nicht jeden Aufruf ausbremsen.

Der Fehler dahinter: Bei einem Fehlschlag wurde nichts zwischengespeichert.
Jeder Seitenaufruf startete den Versuch neu und wartete das volle Zeitlimit
von 15 Sekunden ab - mit zwei Stufen (Standard und 4K) doppelt. Nach aussen
sah es aus, als antworte Radarr nicht, obwohl es lief und nur unter der Last
nicht hinterherkam.
"""

from __future__ import annotations

import pytest

from app.services import library
from app.services.arr import ArrError


class LahmerClient:
    """Zaehlt mit, wie oft wirklich gefragt wurde."""

    def __init__(self) -> None:
        self.versuche = 0

    async def library(self):
        self.versuche += 1
        raise ArrError("Radarr antwortet nicht (Zeitüberschreitung).")


@pytest.fixture(autouse=True)
def _leerer_zwischenspeicher():
    library.invalidate()
    yield
    library.invalidate()


@pytest.mark.asyncio
async def test_haengende_instanz_wird_nur_einmal_gefragt(monkeypatch) -> None:
    client = LahmerClient()
    monkeypatch.setattr(library, "radarr_client", lambda settings, tier="standard": client)

    for _ in range(5):
        with pytest.raises(ArrError):
            await library.movie_library(object(), "standard")

    assert client.versuche == 1, (
        f"Es wurde {client.versuche}-mal gefragt - die Fehlersperre greift nicht."
    )


@pytest.mark.asyncio
async def test_beide_stufen_sperren_getrennt(monkeypatch) -> None:
    """Faellt die 4K-Instanz aus, darf die Standard-Instanz weiterlaufen."""
    standard = LahmerClient()
    uhd = LahmerClient()
    monkeypatch.setattr(
        library,
        "radarr_client",
        lambda settings, tier="standard": uhd if tier == "uhd" else standard,
    )

    with pytest.raises(ArrError):
        await library.movie_library(object(), "uhd")
    with pytest.raises(ArrError):
        await library.movie_library(object(), "uhd")
    # Die Standard-Stufe hat ihre eigene Sperre und ist noch unberuehrt.
    with pytest.raises(ArrError):
        await library.movie_library(object(), "standard")

    assert uhd.versuche == 1
    assert standard.versuche == 1
