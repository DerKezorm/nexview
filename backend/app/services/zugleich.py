"""Unabhaengige Schritte einer Anfrage gleichzeitig - und sauber, wenn einer scheitert."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any


async def alle(*schritte: Awaitable[Any]) -> list[Any]:
    """Wie ``asyncio.gather`` - aber scheitert einer, hoeren die anderen auf.

    Die Schritte einer Anfrage teilen sich ihre Datenbanksitzung. Liefe einer
    nach dem Fehler eines anderen weiter, fasste er sie noch an, waehrend
    FastAPI sie schon schliesst. Der Fehler selbst kommt unveraendert heraus,
    wie vorher nacheinander - keine ``ExceptionGroup`` wie bei ``TaskGroup``,
    damit jedes ``except TmdbError`` darueber weiter greift.
    """
    aufgaben = [asyncio.ensure_future(schritt) for schritt in schritte]
    try:
        return list(await asyncio.gather(*aufgaben))
    except BaseException:
        for aufgabe in aufgaben:
            aufgabe.cancel()
        await asyncio.gather(*aufgaben, return_exceptions=True)
        raise
