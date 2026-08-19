"""Radarr - zustaendig ausschliesslich fuer Filme."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .arr import ArrClient, ArrError


@dataclass(frozen=True)
class LibraryEntry:
    """Ein Film, wie ihn Radarr kennt."""

    arr_id: int
    has_file: bool
    monitored: bool


class RadarrClient(ArrClient):
    def __init__(self, base_url: str, api_key: str) -> None:
        super().__init__(base_url, api_key, "Radarr")

    async def library(self) -> dict[int, LibraryEntry]:
        """Alle Filme aus Radarr, nach TMDB-Id sortiert.

        ``hasFile`` unterscheidet "liegt bereits auf der Platte" von
        "ist eingetragen, wird aber noch gesucht".
        """
        movies = await self.get("/movie") or []
        result: dict[int, LibraryEntry] = {}
        for movie in movies:
            tmdb_id = movie.get("tmdbId")
            if not isinstance(tmdb_id, int):
                continue
            result[tmdb_id] = LibraryEntry(
                arr_id=movie.get("id", 0),
                has_file=bool(movie.get("hasFile")),
                monitored=bool(movie.get("monitored")),
            )
        return result

    async def calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        """Was in diesem Zeitraum erscheint - von Filmen, die Radarr kennt.

        Die Eintraege tragen ``inCinemas``, ``digitalRelease`` und
        ``physicalRelease``; welches davon zaehlt, entscheidet der Kalender.
        ``unmonitored=true``, damit auch stillgelegte Filme auftauchen - der
        Kalender zeigt schliesslich, was erscheint, nicht was gesucht wird.
        """
        entries = await self.get(
            "/calendar", {"start": start, "end": end, "unmonitored": "true"}
        )
        return entries if isinstance(entries, list) else []

    async def lookup(self, tmdb_id: int) -> dict[str, Any] | None:
        """Film bei Radarr nachschlagen - noetig vor dem Hinzufuegen."""
        result = await self.get("/movie/lookup/tmdb", {"tmdbId": tmdb_id})
        if isinstance(result, list):
            return result[0] if result else None
        return result or None

    async def add(
        self,
        tmdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        search_now: bool = True,
        tag_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Film zu Radarr hinzufuegen und direkt die Suche anstossen."""
        found = await self.lookup(tmdb_id)
        if found is None:
            raise ArrError("Radarr kennt diesen Film nicht.", 404)

        payload = {
            **found,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": True,
            "minimumAvailability": "released",
            "tags": tag_ids or [],
            "addOptions": {"searchForMovie": search_now},
        }
        return await self.post("/movie", payload)

    async def remove(self, arr_id: int, delete_files: bool = True) -> None:
        """Film aus Radarr entfernen - samt bereits geladener Dateien."""
        await self.delete(
            f"/movie/{arr_id}",
            {"deleteFiles": str(delete_files).lower(), "addImportExclusion": "false"},
        )
