"""Der Verbindungsvorrat darf keine Obergrenze haben.

⚠️ **Warum es diese Datei gibt.** Mit SQLAlchemys Standard (fuenf Plaetze
plus zehn Ueberlauf) blieb Nexview minutenlang stehen, gemeldet in Issue #17:
"QueuePool limit of size 5 overflow 10 reached, connection timed out". Viele
Routen sind ``async`` und halten ihre Sitzung, waehrend sie auf TMDB warten.
Ein Fernseher-Client schickt Dutzende Anfragen auf einmal; ab der sechzehnten
wartete die Entnahme synchron in der Ereignisschleife, waehrend die fuenfzehn
Halter in derselben Schleife festsassen. Die Begruendung steht in ``app/db.py``
an ``engine``.

Beide Tests hier waren gegen den alten Vorrat rot, mit ``TimeoutError``.

⚠️ **Die Wartezeit der Entnahme ist hier eine Sekunde statt dreissig.** Mit
den dreissig hing die Gegenprobe ueber fuenf Minuten: Jede wartende Anfrage
legte die Schleife einzeln still, also genau das Bild aus der Meldung. Ein
Rueckfall soll hier schnell rot werden, nicht die Testreihe anhalten.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import ExitStack

import pytest
from sqlalchemy import text

from app.db import VORRAT_PLAETZE, SessionLocal, engine

#: Deutlich mehr als die alten fuenfzehn.
GLEICHZEITIG = 40


@pytest.fixture(autouse=True)
def kurze_entnahme(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(engine.pool, "_timeout", 1.0)
    yield


def test_viele_gehaltene_sitzungen_warten_nicht_aufeinander() -> None:
    with ExitStack() as stapel:
        sitzungen = [stapel.enter_context(SessionLocal()) for _ in range(GLEICHZEITIG)]
        # Erst die Abfrage zieht eine Verbindung, und die Sitzung haelt sie bis
        # zum Schliessen - genau wie eine Route, die danach auf TMDB wartet.
        for sitzung in sitzungen:
            assert sitzung.execute(text("SELECT 1")).scalar_one() == 1


def test_async_halter_in_einer_schleife_blockieren_sich_nicht() -> None:
    """Der Fall aus Issue #17, nachgestellt: alle in derselben Schleife."""

    async def anfrage(nr: int) -> int:
        with SessionLocal() as db:
            db.execute(text("SELECT 1")).scalar_one()
            # Steht fuer den TMDB-Abruf: Die Verbindung bleibt dabei belegt.
            await asyncio.sleep(0.05)
            return db.execute(text("SELECT :nr"), {"nr": nr}).scalar_one()

    async def alle() -> list[int]:
        return await asyncio.wait_for(
            asyncio.gather(*(anfrage(nr) for nr in range(GLEICHZEITIG))),
            timeout=20,
        )

    assert asyncio.run(alle()) == list(range(GLEICHZEITIG))


def test_der_vorrat_hebt_weiter_verbindungen_auf() -> None:
    """Ohne Obergrenze heisst nicht ohne Vorrat: Wiederverwenden bleibt."""
    assert engine.pool.size() == VORRAT_PLAETZE
