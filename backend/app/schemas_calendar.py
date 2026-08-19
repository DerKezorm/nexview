"""Datenformate fuer den Erscheinungs-Kalender.

Eigene Datei, weil der Kalender drei sehr verschiedene Herkuenfte auf eine Form
bringt: Folgen aus Sonarr, Filme aus Radarr und Neuerscheinungen von TMDB.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .models import MediaType

# Woher ein Eintrag stammt - und ob er den Benutzer schon etwas angeht.
CalendarSource = Literal["meine", "neu"]
CalendarOrigin = Literal["sonarr", "radarr", "tmdb"]
# Welche Art von Veroeffentlichung das Datum meint.
CalendarDateType = Literal["kino", "digital", "physisch", "premiere", "tv"]


class CalendarEntry(BaseModel):
    """Ein Ereignis an einem Tag.

    Bewusst *kein* ``MediaItem``, obwohl es fast gleich aussieht: dort ist die
    TMDB-Kennung Pflicht. Eine Sonarr-Folge kann aber voellig zu Recht keine
    haben - eine erfundene wuerde die Kachel stillschweigend auf einen falschen
    Titel verlinken. Lieber ein eigener Typ, der ehrlich ``None`` sagen darf.
    """

    # Eindeutig und stabil, z. B. "sonarr:1234:3:2026-08-19". Dient dem
    # Frontend als React-Schluessel und dem Backend zum Entdoppeln.
    key: str
    date: str
    source: CalendarSource
    origin: CalendarOrigin

    media_type: MediaType
    tmdb_id: int | None = None
    tvdb_id: int | None = None

    title: str
    poster_url: str | None = None
    overview: str = ""
    vote_average: float = 0.0
    vote_count: int = 0
    genres: list[str] = []
    runtime_minutes: int | None = None
    certification: str | None = None

    # Dieselbe Sprache wie MediaItem.status - kein neuer Wert, sonst muessten
    # die geschlossene Typ-Union und die Farbtabelle im Frontend mitwachsen.
    status: str = "not_requested"
    watched: bool = False

    # --- nur Serien ---
    season: int | None = None
    # "S03E05" oder "S03E05-06", schon fertig formatiert.
    episode_label: str | None = None
    # Nur gesetzt, wenn an diesem Tag genau EINE Folge laeuft. Sonst wuerde ein
    # Titel stellvertretend fuer mehrere stehen und in die Irre fuehren.
    episode_title: str | None = None
    missing_episodes: list[int] = []

    # --- nur Filme ---
    date_type: CalendarDateType | None = None

    # Liegt das Datum in der Vergangenheit?
    aired: bool = False
    # Ausgestrahlt bzw. erschienen, aber die Datei fehlt. Nur bei source="meine"
    # - was einem nicht gehoert, kann auch nicht fehlen.
    missing: bool = False


class CalendarDay(BaseModel):
    """Alle Ereignisse eines Tages.

    Die Gruppierung macht der Server, nicht der Browser: "Was zaehlt als ein
    Tag" ist eine Zeitzonenfrage, und die gehoert an die Stelle, die auch die
    Grenzen des Zeitraums bestimmt.
    """

    date: str
    entries: list[CalendarEntry] = []


class CalendarResult(BaseModel):
    date_from: str
    date_to: str
    days: list[CalendarDay] = []

    # Faellt eine Quelle aus, bleibt die andere stehen und der Hinweis erklaert
    # die Luecke - sonst saehe ein Ausfall aus wie "diese Woche kommt nichts".
    arr_warning: str | None = None
    tmdb_warning: str | None = None
    demo: bool = False
