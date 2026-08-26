"""Radarr - zustaendig ausschliesslich fuer Filme."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .arr import ArrClient, ArrError


@dataclass(frozen=True)
class LibraryEntry:
    """Ein Film, wie ihn Radarr kennt."""

    arr_id: int
    has_file: bool
    monitored: bool
    # Belegter Platz in Bytes. Radarr liefert die Zahl bei jedem Film gratis
    # mit; gebraucht wird sie fuer die Speicher-Belegung (services/storage.py).
    size_bytes: int = 0
    # Fuer die Anzeige eines Postens, auch wenn der Film spaeter aus Radarr
    # verschwindet - dann steht hier der letzte bekannte Titel.
    title: str = ""
    # Wo die Datei liegt, samt Dateiname. Nur fuer den Administrator gedacht -
    # ein gewoehnlicher Benutzer hat mit Serverpfaden nichts zu schaffen.
    path: str = ""
    # Seit wann die Datei da liegt. Radarr fuehrt es an ``movieFile`` und
    # schickt es in derselben Antwort mit - es kostet also keine Abfrage.
    # Gebraucht vom Aufraeum-Vorschlag: Ohne dieses Datum stuende ein Film,
    # der heute Nacht fertig wurde, ganz oben in der Liste der Ladenhueter.
    added_at: datetime | None = None


def _groesse(movie: dict[str, Any]) -> int:
    """Belegter Platz eines Films in Bytes.

    ``sizeOnDisk`` ist die Angabe, die Radarr fuehrt. Sie steht auch dann auf
    einem Wert, wenn gerade keine Datei da ist (dann null), deshalb wird sie
    ohne Ruecksicht auf ``hasFile`` gelesen - der Aufrufer entscheidet, was
    eine Null bedeutet.

    Der Rueckfall auf ``movieFile.size`` deckt Radarr-Fassungen ab, die
    ``sizeOnDisk`` nicht auf oberster Ebene mitschicken.
    """
    roh = movie.get("sizeOnDisk")
    if isinstance(roh, (int, float)) and roh > 0:
        return int(roh)
    datei = movie.get("movieFile") or {}
    groesse = datei.get("size")
    return int(groesse) if isinstance(groesse, (int, float)) and groesse > 0 else 0


def _hinzugefuegt(movie: dict[str, Any]) -> datetime | None:
    """Wann die **Datei** dazukam - nicht, wann der Film eingetragen wurde.

    ``movie.added`` waere das Falsche: Es sagt, wann jemand den Film in Radarr
    aufgenommen hat, und das kann Jahre vor dem Download liegen. Gefragt ist,
    seit wann die Datei Platz belegt - also ``movieFile.dateAdded``.

    Fehlt die Datei, fehlt auch das Datum. ``None`` ist dann die richtige
    Antwort; der Aufrufer raet nicht.
    """
    roh = (movie.get("movieFile") or {}).get("dateAdded")
    if not isinstance(roh, str) or not roh:
        return None
    try:
        # Radarr schreibt ISO 8601 mit "Z". Naiv ablegen, wie alles andere
        # auch - die Datenbank kennt keine Zeitzonen.
        return datetime.fromisoformat(roh.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).replace(tzinfo=None)
    except ValueError:
        return None


def _pfad(movie: dict[str, Any]) -> str:
    """Voller Pfad der Datei - Ordner des Films plus Dateiname.

    Beides steht in derselben Antwort, die ``library()`` ohnehin holt; eine
    zusaetzliche Abfrage waere dafuer nicht noetig. Fehlt die Datei, bleibt es
    beim Ordner - der sagt immer noch, wohin der Film gehoert.
    """
    ordner = str(movie.get("path") or "").rstrip("/")
    datei = str((movie.get("movieFile") or {}).get("relativePath") or "")
    if ordner and datei:
        return f"{ordner}/{datei}"
    return ordner or datei


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
                size_bytes=_groesse(movie),
                title=str(movie.get("title") or ""),
                path=_pfad(movie),
                added_at=_hinzugefuegt(movie),
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
