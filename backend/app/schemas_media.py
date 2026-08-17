"""Datenformate fuer Filme und Serien."""

from __future__ import annotations

from pydantic import BaseModel

from .models import MediaType


class MediaItem(BaseModel):
    """Ein Titel, so wie ihn die Oberflaeche als Kachel oder Zeile darstellt."""

    media_type: MediaType
    tmdb_id: int
    tvdb_id: int | None = None

    title: str
    original_title: str | None = None
    overview: str = ""

    poster_url: str | None = None
    backdrop_url: str | None = None

    release_date: str | None = None
    vote_average: float = 0.0
    vote_count: int = 0

    genres: list[str] = []
    runtime_minutes: int | None = None
    certification: str | None = None
    original_language: str | None = None

    # Zustand fuer das Badge auf der Kachel. Ab Meilenstein 3 wird hier der
    # echte Abgleich mit Radarr/Sonarr eingetragen.
    status: str = "not_requested"


class MediaPage(BaseModel):
    page: int
    total_pages: int
    total_results: int
    items: list[MediaItem]
    # Kennzeichnet Beispieldaten, damit die Oberflaeche einen Hinweis zeigen kann.
    demo: bool = False
    # Gesetzt, wenn der Abgleich mit Radarr/Sonarr nicht moeglich war. Ohne
    # diesen Hinweis saehe es so aus, als waere die Bibliothek leer.
    arr_warning: str | None = None


class ArrOption(BaseModel):
    id: int
    name: str


class ArrRootFolder(BaseModel):
    path: str
    free_space: int | None = None


class ArrOptions(BaseModel):
    """Auswahlmoeglichkeiten beim Hinzufuegen zu Radarr/Sonarr."""

    quality_profiles: list[ArrOption]
    root_folders: list[ArrRootFolder]
    # Vorauswahl fuer diesen Benutzer: das vom Admin gesetzte Standardprofil,
    # oder - falls es fuer ihn gesperrt ist - das erste erlaubte.
    default_quality_profile_id: int | None = None


class Genre(BaseModel):
    id: int
    name: str
