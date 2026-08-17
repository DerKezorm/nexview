"""Abgleich der TMDB-Titel mit dem, was schon in Radarr/Sonarr liegt.

Die Bibliothek wird kurz im Arbeitsspeicher gehalten (nicht in der Datenbank):
sie aendert sich staendig, ist schnell neu geladen und muss einen Neustart
nicht ueberleben.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..schemas_media import MediaItem
from .arr import ArrError
from .radarr import LibraryEntry as MovieEntry
from .radarr import RadarrClient
from .settings_service import AppSettings
from .sonarr import LibraryEntry as SeriesEntry
from .sonarr import SonarrClient, normalize_title

import logging

logger = logging.getLogger("nexview.library")

LIBRARY_TTL_SECONDS = 60
OPTIONS_TTL_SECONDS = 300

_cache: dict[str, tuple[float, Any]] = {}


def _read(key: str, ttl: int) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at > ttl:
        _cache.pop(key, None)
        return None
    return value


def _write(key: str, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)


def invalidate() -> None:
    """Nach dem Hinzufuegen eines Titels oder geaenderten Einstellungen."""
    _cache.clear()


def radarr_client(settings: AppSettings) -> RadarrClient | None:
    if not settings.radarr_configured:
        return None
    return RadarrClient(settings.radarr_url, settings.radarr_api_key)


def sonarr_client(settings: AppSettings) -> SonarrClient | None:
    if not settings.sonarr_configured:
        return None
    return SonarrClient(settings.sonarr_url, settings.sonarr_api_key)


@dataclass
class MatchResult:
    """Ergebnis des Abgleichs samt Hinweis, falls er nicht moeglich war."""

    items: list[MediaItem]
    warning: str | None = None


async def movie_library(settings: AppSettings) -> dict[int, MovieEntry]:
    cached = _read("radarr:library", LIBRARY_TTL_SECONDS)
    if cached is not None:
        return cached

    client = radarr_client(settings)
    if client is None:
        return {}

    library = await client.library()
    _write("radarr:library", library)
    return library


async def series_library(
    settings: AppSettings,
) -> tuple[dict[int, SeriesEntry], dict[str, SeriesEntry]]:
    cached = _read("sonarr:library", LIBRARY_TTL_SECONDS)
    if cached is not None:
        return cached

    client = sonarr_client(settings)
    if client is None:
        return {}, {}

    library = await client.library()
    _write("sonarr:library", library)
    return library


def _status_for(entry: MovieEntry | SeriesEntry) -> str:
    """"Liegt schon da" oder "eingetragen, aber noch nicht geladen"."""
    return "downloaded" if entry.has_file else "searching"


async def apply_status(
    settings: AppSettings, media_type: str, items: list[MediaItem]
) -> MatchResult:
    """Jeder Kachel ihren Zustand geben.

    Ist Radarr/Sonarr nicht eingerichtet oder nicht erreichbar, bleiben die
    Titel auf "nicht angefragt" - mit einem Hinweis, damit niemand denkt,
    die Bibliothek sei leer.
    """
    if not items:
        return MatchResult(items=items)

    configured = settings.radarr_configured if media_type == "movie" else settings.sonarr_configured
    if not configured:
        return MatchResult(items=items)

    try:
        if media_type == "movie":
            library = await movie_library(settings)
            updated = [
                item.model_copy(update={"status": _status_for(library[item.tmdb_id])})
                if item.tmdb_id in library
                else item
                for item in items
            ]
        else:
            by_tvdb, by_title = await series_library(settings)
            updated = []
            for item in items:
                # Erst ueber die TVDB-Id, sonst ueber den normalisierten Titel.
                entry = by_tvdb.get(item.tvdb_id) if item.tvdb_id else None
                if entry is None:
                    entry = by_title.get(normalize_title(item.title))
                updated.append(
                    item.model_copy(update={"status": _status_for(entry)}) if entry else item
                )
    except ArrError as error:
        return MatchResult(items=items, warning=error.message)

    return MatchResult(items=updated)


async def options(settings: AppSettings, media_type: str) -> dict[str, Any]:
    """Qualitaetsprofile und Zielordner fuer die Auswahl beim Hinzufuegen."""
    key = f"options:{media_type}"
    cached = _read(key, OPTIONS_TTL_SECONDS)
    if cached is not None:
        return cached

    client = radarr_client(settings) if media_type == "movie" else sonarr_client(settings)
    if client is None:
        raise ArrError(
            "Radarr ist noch nicht eingerichtet."
            if media_type == "movie"
            else "Sonarr ist noch nicht eingerichtet."
        )

    profiles = await client.quality_profiles()
    folders = await client.root_folders()

    result = {
        "quality_profiles": [
            {"id": profile.get("id"), "name": profile.get("name")}
            for profile in profiles
            if profile.get("id") is not None
        ],
        "root_folders": [
            {
                "path": folder.get("path"),
                "free_space": folder.get("freeSpace"),
            }
            for folder in folders
            if folder.get("path")
        ],
    }
    _write(key, result)
    return result


async def episode_availability(
    settings: AppSettings, tvdb_id: int | None, title: str
) -> dict[int, set[int]]:
    """Welche Folgen dieser Serie liegen schon vor?

    Ergebnis: Staffelnummer -> Menge der vorhandenen Folgennummern. Ist Sonarr
    nicht eingerichtet, kennt es die Serie nicht oder antwortet es nicht, kommt
    eine leere Antwort zurueck - die Staffelliste zeigt dann eben nichts als
    vorhanden an, statt gar nicht zu erscheinen.
    """
    client = sonarr_client(settings)
    if client is None:
        return {}

    schluessel = f"episodes:{tvdb_id or title}"
    zwischengespeichert = _read(schluessel, LIBRARY_TTL_SECONDS)
    if zwischengespeichert is not None:
        return {int(staffel): set(folgen) for staffel, folgen in zwischengespeichert.items()}

    try:
        nach_tvdb, nach_titel = await series_library(settings)
        eintrag = nach_tvdb.get(tvdb_id) if tvdb_id else None
        if eintrag is None:
            eintrag = nach_titel.get(normalize_title(title))
        if eintrag is None:
            _write(schluessel, {})
            return {}

        vorhanden = await client.episode_status(eintrag.arr_id)
    except ArrError as fehler:
        logger.info("Folgenzustand nicht abrufbar: %s", fehler.message)
        return {}

    # Mengen lassen sich nicht als JSON ablegen - fuer den Zwischenspeicher
    # werden daraus Listen.
    _write(schluessel, {str(s): sorted(f) for s, f in vorhanden.items()})
    return vorhanden
