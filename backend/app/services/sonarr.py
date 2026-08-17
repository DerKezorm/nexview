"""Sonarr - zustaendig ausschliesslich fuer Serien.

Wichtiger Unterschied zu Radarr: Sonarr kennt keine TMDB-Ids, sondern
arbeitet mit TVDB-Ids. Deshalb wird die TVDB-Id bei Serien schon beim
Laden der Details von TMDB mitgeholt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .arr import ArrClient, ArrError


@dataclass(frozen=True)
class LibraryEntry:
    """Eine Serie, wie sie Sonarr kennt."""

    arr_id: int
    has_file: bool  # mindestens eine Folge liegt vor
    monitored: bool
    episode_file_count: int
    episode_count: int
    title_key: str  # normalisierter Titel als Rueckfallweg


def normalize_title(title: str) -> str:
    """Titel auf einen vergleichbaren Kern reduzieren."""
    return "".join(character for character in title.casefold() if character.isalnum())


class SonarrClient(ArrClient):
    def __init__(self, base_url: str, api_key: str) -> None:
        super().__init__(base_url, api_key, "Sonarr")

    async def library(self) -> tuple[dict[int, LibraryEntry], dict[str, LibraryEntry]]:
        """Alle Serien aus Sonarr - einmal nach TVDB-Id, einmal nach Titel.

        Der Titel-Index ist der Rueckfallweg: TMDB kennt fuer viele neue
        Serien noch keine TVDB-Id, dann waere sonst kein Abgleich moeglich.
        """
        series = await self.get("/series") or []
        by_tvdb: dict[int, LibraryEntry] = {}
        by_title: dict[str, LibraryEntry] = {}

        for show in series:
            statistics = show.get("statistics") or {}
            file_count = int(statistics.get("episodeFileCount") or 0)
            entry = LibraryEntry(
                arr_id=show.get("id", 0),
                has_file=file_count > 0,
                monitored=bool(show.get("monitored")),
                episode_file_count=file_count,
                episode_count=int(statistics.get("episodeCount") or 0),
                title_key=normalize_title(show.get("title") or ""),
            )

            tvdb_id = show.get("tvdbId")
            if isinstance(tvdb_id, int) and tvdb_id > 0:
                by_tvdb[tvdb_id] = entry
            if entry.title_key:
                by_title[entry.title_key] = entry

        return by_tvdb, by_title

    async def lookup(self, tvdb_id: int) -> dict[str, Any] | None:
        result = await self.get("/series/lookup", {"term": f"tvdb:{tvdb_id}"})
        if isinstance(result, list):
            return result[0] if result else None
        return result or None

    async def add(
        self,
        tvdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        search_now: bool = True,
        tag_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Serie komplett zu Sonarr hinzufuegen (alle Staffeln ueberwacht)."""
        found = await self.lookup(tvdb_id)
        if found is None:
            raise ArrError("Sonarr kennt diese Serie nicht.", 404)

        payload = {
            **found,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": True,
            "seasonFolder": True,
            "tags": tag_ids or [],
            "addOptions": {
                "monitor": "all",
                "searchForMissingEpisodes": search_now,
            },
        }
        return await self.post("/series", payload)

    async def remove(self, arr_id: int, delete_files: bool = True) -> None:
        """Serie aus Sonarr entfernen - samt bereits geladener Folgen."""
        await self.delete(
            f"/series/{arr_id}",
            {"deleteFiles": str(delete_files).lower(), "addImportListExclusion": "false"},
        )
