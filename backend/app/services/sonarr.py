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

    async def calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        """Welche Folgen in diesem Zeitraum laufen.

        ``includeSeries=true`` haengt an jede Folge die zugehoerige Serie an -
        das spart einen zweiten Aufruf je Serie und liefert Titel, TVDB-Id und
        (ab Sonarr 4) sogar die TMDB-Id gleich mit.
        """
        entries = await self.get(
            "/calendar",
            {"start": start, "end": end, "unmonitored": "true", "includeSeries": "true"},
        )
        return entries if isinstance(entries, list) else []

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
        season: int | None = None,
    ) -> dict[str, Any]:
        """Serie zu Sonarr hinzufuegen.

        Ohne ``season`` wird die komplette Serie ueberwacht. Mit ``season``
        wird ausschliesslich diese eine Staffel ueberwacht - alle anderen
        bleiben aus, damit Sonarr nicht doch die ganze Serie herunterlaedt.
        """
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

        if season is not None:
            # "monitor: none" allein genuegt nicht - Sonarr richtet sich beim
            # Anlegen nach der mitgeschickten Staffelliste. Beides zu setzen ist
            # der sichere Weg ueber verschiedene Sonarr-Fassungen hinweg.
            payload["seasons"] = [
                {**eintrag, "monitored": eintrag.get("seasonNumber") == season}
                for eintrag in (found.get("seasons") or [])
            ]
            payload["addOptions"] = {
                "monitor": "none",
                "searchForMissingEpisodes": False,
            }

        angelegt = await self.post("/series", payload)

        # Die Suche erst nach dem Anlegen anstossen, und dann gezielt fuer diese
        # eine Staffel.
        if season is not None and search_now and isinstance(angelegt, dict):
            await self.search_season(angelegt.get("id"), season)

        return angelegt

    async def monitor_season(self, arr_id: int, season: int, search_now: bool = True) -> None:
        """Eine weitere Staffel einer bereits vorhandenen Serie aktivieren.

        Der haeufigste Fall ueberhaupt: die Serie laeuft schon mit, nur die
        neue Staffel fehlt. Die Serie neu anzulegen waere hier falsch - Sonarr
        wuerde die vorhandenen Folgen durcheinanderbringen.
        """
        serie = await self.get(f"/series/{arr_id}")
        if not isinstance(serie, dict):
            raise ArrError("Sonarr liefert diese Serie nicht.", 404)

        staffeln = serie.get("seasons") or []
        if not any(eintrag.get("seasonNumber") == season for eintrag in staffeln):
            raise ArrError(f"Sonarr kennt Staffel {season} dieser Serie nicht.", 404)

        serie["seasons"] = [
            {**eintrag, "monitored": True}
            if eintrag.get("seasonNumber") == season
            else eintrag
            for eintrag in staffeln
        ]
        # Eine Serie, die als Ganzes nicht ueberwacht wird, laedt auch einzelne
        # Staffeln nicht - deshalb hier mit aktivieren.
        serie["monitored"] = True

        await self.put(f"/series/{arr_id}", serie)

        if search_now:
            await self.search_season(arr_id, season)

    async def search_season(self, arr_id: int | None, season: int) -> None:
        """Sonarr anweisen, genau diese Staffel zu suchen.

        Schlaegt das fehl, ist das kein Beinbruch: die Staffel ist ueberwacht,
        Sonarr findet sie beim naechsten regulaeren Durchlauf von selbst.
        """
        if not arr_id:
            return
        try:
            await self.post(
                "/command", {"name": "SeasonSearch", "seriesId": arr_id, "seasonNumber": season}
            )
        except ArrError:
            pass

    async def episode_status(self, arr_id: int) -> dict[int, set[int]]:
        """Welche Folgen liegen bereits vor? Nach Staffelnummer gebuendelt.

        Sonarr liefert alle Folgen einer Serie in einem Aufruf; feiner
        aufzuteilen waere eine Abfrage pro Staffel, und davon haette niemand
        etwas.
        """
        folgen = await self.get("/episode", {"seriesId": arr_id}) or []
        vorhanden: dict[int, set[int]] = {}
        for folge in folgen:
            if not folge.get("hasFile"):
                continue
            staffel = folge.get("seasonNumber")
            nummer = folge.get("episodeNumber")
            if isinstance(staffel, int) and isinstance(nummer, int):
                vorhanden.setdefault(staffel, set()).add(nummer)
        return vorhanden

    async def remove(self, arr_id: int, delete_files: bool = True) -> None:
        """Serie aus Sonarr entfernen - samt bereits geladener Folgen."""
        await self.delete(
            f"/series/{arr_id}",
            {"deleteFiles": str(delete_files).lower(), "addImportListExclusion": "false"},
        )
