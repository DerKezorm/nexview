"""Datenformate fuer Filme und Serien."""

from __future__ import annotations

from pydantic import BaseModel

from .models import MediaType


class SeasonInfo(BaseModel):
    """Eine Staffel, wie TMDB sie kennt."""

    season_number: int
    name: str
    episode_count: int = 0
    air_date: str | None = None
    overview: str = ""
    poster_url: str | None = None
    # Wie viele Folgen liegen schon in der Bibliothek? Kommt aus Sonarr, nicht
    # von TMDB - ohne eingerichtetes Sonarr bleibt es bei 0.
    episodes_available: int = 0


class EpisodeInfo(BaseModel):
    """Eine einzelne Folge."""

    episode_number: int
    name: str
    overview: str = ""
    air_date: str | None = None
    runtime_minutes: int | None = None
    still_url: str | None = None
    vote_average: float = 0.0
    # Liegt diese Folge schon vor? Aus Sonarr.
    available: bool = False


class SeasonDetail(BaseModel):
    """Eine aufgeklappte Staffel mit allen Folgen."""

    season_number: int
    name: str
    overview: str = ""
    air_date: str | None = None
    episodes: list[EpisodeInfo] = []


class NamedRef(BaseModel):
    """Etwas mit Kennung und Namen - Schlagwort oder Studio.

    Der Name allein genuegt nicht: zum Weitersuchen braucht TMDB die Kennung.
    """

    id: int
    name: str


class Trailer(BaseModel):
    """Ein Trailer, wie TMDB ihn kennt - das Video selbst liegt bei YouTube."""

    key: str
    name: str = ""
    site: str = "YouTube"
    language: str = ""


class CastMember(BaseModel):
    """Ein Eintrag der Besetzung."""

    person_id: int
    name: str
    character: str = ""
    photo_url: str | None = None


class CrewMember(BaseModel):
    """Regie, Drehbuch und Ähnliches - nur die paar Rollen, die man sucht."""

    person_id: int
    name: str
    job: str


class PersonCredit(BaseModel):
    """Ein Titel aus der Filmografie einer Person."""

    media_type: MediaType
    tmdb_id: int
    title: str
    character: str = ""
    poster_url: str | None = None
    release_date: str | None = None
    vote_average: float = 0.0
    status: str = "not_requested"


class PersonDetail(BaseModel):
    """Eine Person mit Foto, Biografie und ihren bekanntesten Titeln."""

    person_id: int
    name: str
    biography: str = ""
    photo_url: str | None = None
    birthday: str | None = None
    deathday: str | None = None
    place_of_birth: str | None = None
    known_for_department: str = ""
    credits: list[PersonCredit] = []


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

    # Nur bei Serien und nur in der Detailansicht gefuellt: die Staffelliste
    # fuer die Auswahl beim Anfragen. In Listenansichten waere sie unnoetiger
    # Ballast - dort wird sie gar nicht erst mitgeliefert.
    seasons: list[SeasonInfo] = []

    # Zustand fuer das Badge auf der Kachel. Ab Meilenstein 3 wird hier der
    # echte Abgleich mit Radarr/Sonarr eingetragen.
    status: str = "not_requested"


class MediaDetail(MediaItem):
    """Alles, was die Detailseite zeigt - deutlich mehr als eine Kachel.

    Eigener Typ, damit die Listenabfragen nicht plötzlich Besetzung und
    Empfehlungen mitschleppen: das waere pro Seite ein Vielfaches an Daten,
    ohne dass irgendetwas davon dargestellt wird.
    """

    tagline: str = ""
    homepage: str | None = None
    status_text: str = ""
    original_country: list[str] = []
    spoken_languages: list[str] = []
    budget: int | None = None
    revenue: int | None = None

    studios: list[NamedRef] = []
    keywords: list[NamedRef] = []
    trailer: Trailer | None = None

    cast: list[CastMember] = []
    crew: list[CrewMember] = []
    recommendations: list[MediaItem] = []

    # Nur bei Serien
    seasons_total: int | None = None
    episodes_total: int | None = None
    series_status: str = ""
    networks: list[NamedRef] = []


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
    # Welcher Zielordner gilt, und darf der Benutzer ihn ueberhaupt aendern?
    # Ist die Auswahl abgeschaltet, blendet die Oberflaeche das Feld aus - und
    # der Server setzt den Ordner ohnehin selbst.
    default_root_folder: str | None = None
    root_folder_choice: bool = True


class Genre(BaseModel):
    id: int
    name: str
