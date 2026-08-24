"""Bewertungen von IMDb, Rotten Tomatoes und Metacritic.

TMDB kennt nur die eigene Wertung. Radarr dagegen liefert zu jedem Film die
Wertungen der grossen Portale mit - und zwar auch fuer Filme, die gar nicht in
der Bibliothek liegen. Das kostet keinen weiteren Dienst und keinen weiteren
Schluessel.

Fuer Serien gibt es das nicht: Sonarr liefert nur eine einzige Sammelwertung
ohne Aufschluesselung. Deshalb beschraenkt sich dieser Dienst auf Filme.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .arr import ArrError
from .library import radarr_client
from .settings_service import AppSettings

logger = logging.getLogger("nexview.ratings")

# Wertungen aendern sich langsam - einmal am Tag reicht voellig.
TTL_SECONDS = 24 * 60 * 60

# Wie viele Abfragen gleichzeitig an Radarr gehen. Jede loest dort eine
# Abfrage beim Metadatendienst aus; mehr als eine Handvoll auf einmal bringt
# nichts und belastet nur.
MAX_PARALLEL = 6

# Laenger als das darf die Beschaffung nie dauern. Bewertungen sind Beiwerk -
# lieber keine als eine haengende Seite.
TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class Ratings:
    # Fuer den Link auf die IMDb-Seite - ohne sie bliebe nur eine Suche.
    imdb_id: str | None = None
    imdb: float | None = None
    imdb_votes: int | None = None
    rotten_tomatoes: int | None = None
    metacritic: int | None = None

    @property
    def leer(self) -> bool:
        return (
            self.imdb is None and self.rotten_tomatoes is None and self.metacritic is None
        )


_cache: dict[int, tuple[float, Ratings]] = {}


def _lesen(tmdb_id: int) -> Ratings | None:
    eintrag = _cache.get(tmdb_id)
    if eintrag is None:
        return None
    zeit, wert = eintrag
    if time.monotonic() - zeit > TTL_SECONDS:
        del _cache[tmdb_id]
        return None
    return wert


def _auswerten(roh: dict) -> Ratings:
    """Radarrs Bewertungsblock in unsere Form bringen.

    Radarr liefert Eintraege auch dann, wenn es keine Wertung gibt - dann steht
    dort eine 0. Die als "0 von 10" anzuzeigen waere schlicht falsch.
    """
    quellen = roh.get("ratings") or {}

    def wert(name: str) -> float | None:
        eintrag = quellen.get(name) or {}
        zahl = eintrag.get("value")
        return float(zahl) if isinstance(zahl, (int, float)) and zahl > 0 else None

    imdb = wert("imdb")
    stimmen = ((quellen.get("imdb") or {}).get("votes")) or None
    tomaten = wert("rottenTomatoes")
    meta = wert("metacritic")

    kennung = roh.get("imdbId") or None

    return Ratings(
        imdb_id=kennung if isinstance(kennung, str) and kennung.startswith("tt") else None,
        imdb=round(imdb, 1) if imdb else None,
        imdb_votes=int(stimmen) if stimmen else None,
        rotten_tomatoes=int(tomaten) if tomaten else None,
        metacritic=int(meta) if meta else None,
    )


async def for_movies(settings: AppSettings, tmdb_ids: list[int]) -> dict[int, Ratings]:
    """Bewertungen zu mehreren Filmen - was nicht zu holen ist, fehlt eben."""
    if not tmdb_ids or not settings.radarr_configured:
        return {}

    ergebnis: dict[int, Ratings] = {}
    offen: list[int] = []
    for tmdb_id in dict.fromkeys(tmdb_ids):  # Reihenfolge erhalten, Doppelte raus
        vorhanden = _lesen(tmdb_id)
        if vorhanden is None:
            offen.append(tmdb_id)
        elif not vorhanden.leer:
            ergebnis[tmdb_id] = vorhanden

    if not offen:
        return ergebnis

    client = radarr_client(settings)
    if client is None:
        return ergebnis

    plaetze = asyncio.Semaphore(MAX_PARALLEL)

    async def hole(tmdb_id: int) -> None:
        async with plaetze:
            try:
                treffer = await client.get("/movie/lookup/tmdb", {"tmdbId": tmdb_id})
            except ArrError:
                return
            if isinstance(treffer, list):
                treffer = treffer[0] if treffer else None
            if not isinstance(treffer, dict):
                return

            bewertung = _auswerten(treffer)
            # Auch ein leeres Ergebnis merken: sonst fragt jede Seite erneut
            # nach einem Film, zu dem es nun einmal nichts gibt.
            _cache[tmdb_id] = (time.monotonic(), bewertung)
            if not bewertung.leer:
                ergebnis[tmdb_id] = bewertung

    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            await asyncio.gather(*(hole(tmdb_id) for tmdb_id in offen))
    except TimeoutError:
        logger.debug("Ratings: time limit reached, fetched %d of %d", len(ergebnis), len(offen))

    return ergebnis


def reset_cache() -> None:
    """Nur fuer Tests."""
    _cache.clear()
