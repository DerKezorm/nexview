"""Filter fuer die Entdecken-Seite.

Liegt bewusst in einem eigenen Modul, damit sowohl der TMDB-Client als auch
der Medien-Service damit arbeiten koennen, ohne sich gegenseitig zu importieren.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ab welcher Laufzeit ein Film als abendfuellend gilt (Minuten).
MIN_FEATURE_RUNTIME = 40

# Wie viele Stimmen mindestens noetig sind, damit eine Mindestbewertung
# aussagekraeftig ist. Ohne das wuerde ein einzelnes 10-Sterne-Votum reichen.
MIN_VOTES_FOR_RATING = 10

# Voreinstellung fuer "nur bekannte Titel".
#
# TMDB fuehrt fuer ein Jahr ueber 50.000 Filme. Sortiert nach Datum stehen
# oben fast ausschliesslich Kleinstproduktionen ohne Beschreibung und ohne
# Bewertung - gemessen: 0 von 20 Treffern hatten eine Beschreibung. Eine
# Untergrenze bei den Stimmen holt die echten Veroeffentlichungen nach vorn.
KNOWN_TITLES_MIN_VOTES = 20

# Bekannte Filmstudios mit ihrer TMDB-Kennung.
# Die Kennungen wurden gegen die TMDB-Suche geprueft - nicht raten, mehrere
# Studios haben Namensdubletten mit fast leeren Eintraegen (z. B. A24).
STUDIOS: list[tuple[int, str]] = [
    (2, "Walt Disney Pictures"),
    (3, "Pixar"),
    (420, "Marvel Studios"),
    (174, "Warner Bros. Pictures"),
    (12, "New Line Cinema"),
    (33, "Universal Pictures"),
    (5, "Columbia Pictures"),
    (4, "Paramount Pictures"),
    (127928, "20th Century Studios"),
    (1632, "Lionsgate"),
    (210099, "Amazon MGM Studios"),
    (41077, "A24"),
    (3172, "Blumhouse"),
    (923, "Legendary Pictures"),
    (10146, "Focus Features"),
    (521, "DreamWorks Animation"),
    (10342, "Studio Ghibli"),
    (47, "Constantin Film"),
]

STUDIO_IDS = frozenset(studio_id for studio_id, _ in STUDIOS)


@dataclass(frozen=True)
class DiscoverFilters:
    date_from: str | None = None
    date_to: str | None = None
    language: str | None = None  # Originalsprache, z. B. "de"; None = alle
    region: str | None = None
    genre_id: int | None = None
    sort: str = "newest"
    page: int = 1

    # Kurzfilme ausblenden (Mindestlaufzeit in Minuten; None = alles zeigen)
    min_runtime: int | None = None
    # Mindestbewertung, z. B. 7.0
    min_rating: float | None = None
    # Titel ohne jede Bewertung ausblenden
    hide_unrated: bool = False
    # Mindestanzahl Stimmen ("nur bekannte Titel"); None = keine Untergrenze
    min_votes: int | None = None
    # Nur Titel, die in der gewaehlten Region veroeffentlicht wurden (nur Filme)
    released_in_region: bool = False
    # Produktionsfirma (nur Filme)
    studio_id: int | None = None

    def cache_key(self, media_type: str) -> str:
        return (
            f"discover:{media_type}:{self.date_from}:{self.date_to}:{self.language}:"
            f"{self.region}:{self.genre_id}:{self.sort}:{self.page}:{self.min_runtime}:"
            f"{self.min_rating}:{self.hide_unrated}:{self.released_in_region}:{self.studio_id}:"
            f"{self.min_votes}"
        )
