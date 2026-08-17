"""Beispieldaten fuer den Demo-Modus.

Damit laesst sich Nexview vollstaendig ausprobieren, bevor ein TMDB-API-Key
hinterlegt ist. Die Titel sind frei erfunden - die Poster erzeugt der Server
selbst als schlichte Farbverlaeufe (siehe ``demo_poster``).
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

from ..schemas_media import MediaItem

MOVIE_GENRES = [
    (28, "Action"),
    (18, "Drama"),
    (35, "Komödie"),
    (878, "Science Fiction"),
    (27, "Horror"),
    (53, "Thriller"),
    (16, "Animation"),
    (99, "Dokumentarfilm"),
]

TV_GENRES = [
    (10759, "Action & Abenteuer"),
    (18, "Drama"),
    (35, "Komödie"),
    (10765, "Sci-Fi & Fantasy"),
    (80, "Krimi"),
    (99, "Dokumentation"),
]

_MOVIES: list[dict[str, Any]] = [
    {
        "title": "Nordlicht",
        "overview": "Eine Meteorologin strandet auf einer Forschungsstation in Spitzbergen und "
        "entdeckt im Polarwinter ein Signal, das dort niemand gesendet haben kann.",
        "genres": ["Science Fiction", "Thriller"],
        "runtime": 128,
        "certification": "12",
        "vote": 7.8,
        "votes": 1420,
        "language": "de",
    },
    {
        "title": "Der letzte Zug nach Triest",
        "overview": "1954 begleitet ein Schaffner eine Fremde über die Grenze - und riskiert "
        "damit alles, was ihm geblieben ist.",
        "genres": ["Drama"],
        "runtime": 141,
        "certification": "12",
        "vote": 8.2,
        "votes": 890,
        "language": "de",
    },
    {
        "title": "Pixelherz",
        "overview": "Ein Spieleentwickler baut seiner Tochter ein Spiel, in dem sie ihre "
        "verstorbene Mutter wiedersehen kann. Dann fängt das Spiel an, eigene Regeln zu schreiben.",
        "genres": ["Drama", "Science Fiction"],
        "runtime": 112,
        "certification": "6",
        "vote": 7.1,
        "votes": 3310,
        "language": "en",
    },
    {
        "title": "Küstenwache Rügen",
        "overview": "Eine ausgemusterte Rettungsschwimmerin nimmt einen letzten Auftrag an und "
        "gerät zwischen Schmuggler und alte Freunde.",
        "genres": ["Action", "Krimi"],
        "runtime": 97,
        "certification": "16",
        "vote": 6.3,
        "votes": 540,
        "language": "de",
    },
    {
        "title": "Die Sprache der Bienen",
        "overview": "Eine Imkerin und ein Datenanalyst versuchen zu beweisen, dass ein Konzern "
        "das Sterben ganzer Völker verschweigt.",
        "genres": ["Dokumentarfilm"],
        "runtime": 88,
        "certification": "0",
        "vote": 7.6,
        "votes": 220,
        "language": "de",
    },
    {
        "title": "Zwei Wochen Marseille",
        "overview": "Zwei Fremde teilen sich versehentlich dieselbe Ferienwohnung - und "
        "beschließen, es niemandem zu sagen.",
        "genres": ["Komödie"],
        "runtime": 103,
        "certification": "6",
        "vote": 6.9,
        "votes": 2110,
        "language": "fr",
    },
    {
        "title": "Schwarzwald 1876",
        "overview": "Nach einem Grubenunglück deckt eine Hebamme auf, dass das Dorf ein "
        "Jahrhundert altes Versprechen gebrochen hat.",
        "genres": ["Horror", "Drama"],
        "runtime": 119,
        "certification": "16",
        "vote": 7.4,
        "votes": 1760,
        "language": "de",
    },
    {
        "title": "Orbit 9",
        "overview": "Die letzte Besatzung einer stillgelegten Raumstation bekommt Besuch, "
        "obwohl seit acht Jahren kein Schiff mehr gestartet ist.",
        "genres": ["Science Fiction", "Horror"],
        "runtime": 134,
        "certification": "16",
        "vote": 8.0,
        "votes": 5240,
        "language": "en",
    },
    {
        "title": "Papierboote",
        "overview": "Ein Junge schickt Nachrichten den Fluss hinunter - und bekommt Antworten "
        "von jemandem, den es nicht mehr geben dürfte.",
        "genres": ["Animation", "Drama"],
        "runtime": 94,
        "certification": "6",
        "vote": 8.5,
        "votes": 4020,
        "language": "ja",
    },
    {
        "title": "Nachtschicht",
        "overview": "Eine Notärztin erkennt in ihrem Patienten den Mann wieder, der vor zehn "
        "Jahren ihre Aussage zunichtegemacht hat.",
        "genres": ["Thriller"],
        "runtime": 108,
        "certification": "16",
        "vote": 7.0,
        "votes": 1290,
        "language": "de",
    },
    {
        "title": "Alles außer Wetter",
        "overview": "Ein Wetterreporter verliert seine Sendung und übernimmt widerwillig den "
        "Chor seines Heimatdorfes.",
        "genres": ["Komödie", "Drama"],
        "runtime": 101,
        "certification": "0",
        "vote": 6.6,
        "votes": 980,
        "language": "de",
    },
    {
        "title": "Tiefenrausch",
        "overview": "Beim Bergen eines Wracks vor Malta findet ein Taucherteam eine Ladung, "
        "die auf keinem Frachtbrief steht.",
        "genres": ["Action", "Thriller"],
        "runtime": 116,
        "certification": "12",
        "vote": 7.3,
        "votes": 2670,
        "language": "en",
    },
]

_SERIES: list[dict[str, Any]] = [
    {
        "title": "Grenzfall",
        "overview": "Eine Zollfahnderin an der deutsch-polnischen Grenze zieht an einem Faden, "
        "der bis in ihre eigene Familie reicht.",
        "genres": ["Krimi", "Drama"],
        "runtime": 48,
        "certification": "16",
        "vote": 8.1,
        "votes": 3400,
        "language": "de",
    },
    {
        "title": "Kollektiv",
        "overview": "Sechs Fremde wachen in einer Wohnung auf, an die sich keiner von ihnen "
        "erinnert - und die Tür lässt sich nur von innen abschließen.",
        "genres": ["Sci-Fi & Fantasy", "Krimi"],
        "runtime": 52,
        "certification": "16",
        "vote": 7.9,
        "votes": 6100,
        "language": "en",
    },
    {
        "title": "Praxis Sonnenberg",
        "overview": "Eine Landärztin übernimmt die Praxis ihres Vaters und findet dessen "
        "Karteikarten voller Einträge, die es nie gegeben hat.",
        "genres": ["Drama"],
        "runtime": 45,
        "certification": "12",
        "vote": 7.2,
        "votes": 1180,
        "language": "de",
    },
    {
        "title": "Hafenkante 88",
        "overview": "Drei Generationen einer Hamburger Speditionsfamilie streiten über das "
        "letzte Grundstück am Wasser.",
        "genres": ["Drama"],
        "runtime": 50,
        "certification": "12",
        "vote": 6.8,
        "votes": 720,
        "language": "de",
    },
    {
        "title": "Die Kartografen",
        "overview": "Ein Vermessungsteam kartiert eine Insel, die auf keiner Karte auftaucht - "
        "und die jede Nacht ihre Form ändert.",
        "genres": ["Sci-Fi & Fantasy", "Action & Abenteuer"],
        "runtime": 55,
        "certification": "12",
        "vote": 8.4,
        "votes": 8900,
        "language": "en",
    },
    {
        "title": "Feierabendbier",
        "overview": "Vier Kollegen einer Stadtreinigung lösen im Feierabend Probleme, für die "
        "eigentlich niemand zuständig ist.",
        "genres": ["Komödie"],
        "runtime": 26,
        "certification": "6",
        "vote": 7.5,
        "votes": 2450,
        "language": "de",
    },
    {
        "title": "Stillwasser",
        "overview": "Nach dem Verschwinden zweier Jugendlicher kehrt eine Ermittlerin in den "
        "Ort zurück, aus dem sie einst geflohen ist.",
        "genres": ["Krimi"],
        "runtime": 47,
        "certification": "16",
        "vote": 7.7,
        "votes": 3900,
        "language": "de",
    },
    {
        "title": "Werkstatt der Dinge",
        "overview": "Eine Dokureihe über Menschen, die reparieren, was andere längst "
        "weggeworfen haben.",
        "genres": ["Dokumentation"],
        "runtime": 30,
        "certification": "0",
        "vote": 8.0,
        "votes": 610,
        "language": "de",
    },
    {
        "title": "Zeitkapsel",
        "overview": "Ein Physiklehrer entdeckt, dass jeder Montag in seiner Kleinstadt "
        "geringfügig anders verläuft als geplant.",
        "genres": ["Sci-Fi & Fantasy", "Komödie"],
        "runtime": 42,
        "certification": "12",
        "vote": 7.4,
        "votes": 2050,
        "language": "en",
    },
    {
        "title": "Auf Bewährung",
        "overview": "Eine Bewährungshelferin muss entscheiden, wem sie glaubt: ihrem "
        "Klienten oder den Akten.",
        "genres": ["Drama", "Krimi"],
        "runtime": 51,
        "certification": "16",
        "vote": 7.6,
        "votes": 1640,
        "language": "de",
    },
]


def _stable_id(title: str, media_type: str) -> int:
    """Immer dieselbe Kennung fuer denselben Demo-Titel."""
    digest = hashlib.sha256(f"{media_type}:{title}".encode()).hexdigest()
    return 900_000 + int(digest[:6], 16) % 90_000


def _build(entry: dict[str, Any], media_type: str, offset_days: int) -> MediaItem:
    tmdb_id = _stable_id(entry["title"], media_type)
    release = date.today() - timedelta(days=offset_days)

    return MediaItem(
        media_type=media_type,  # type: ignore[arg-type]
        tmdb_id=tmdb_id,
        tvdb_id=tmdb_id + 500_000 if media_type == "tv" else None,
        title=entry["title"],
        original_title=entry["title"],
        overview=entry["overview"],
        poster_url=f"/api/demo/poster/{media_type}/{tmdb_id}.svg",
        backdrop_url=f"/api/demo/poster/{media_type}/{tmdb_id}.svg?wide=1",
        release_date=release.isoformat(),
        vote_average=entry["vote"],
        vote_count=entry["votes"],
        genres=entry["genres"],
        runtime_minutes=entry["runtime"],
        certification=entry["certification"],
        original_language=entry["language"],
    )


def demo_items(media_type: str) -> list[MediaItem]:
    source = _MOVIES if media_type == "movie" else _SERIES
    # Erscheinungsdaten ueber die letzten Wochen verteilen, damit die
    # Zeitraum-Filter etwas zu filtern haben.
    return [_build(entry, media_type, offset_days=index * 6 + 2) for index, entry in enumerate(source)]


def demo_genres(media_type: str) -> list[tuple[int, str]]:
    return MOVIE_GENRES if media_type == "movie" else TV_GENRES


# Farbpaare fuer die erzeugten Demo-Poster (dunkel, zum Theme passend).
_POSTER_COLORS = [
    ("#3b1220", "#8f0f1c"),
    ("#101a2e", "#1e3a5f"),
    ("#1d1030", "#4c1d6b"),
    ("#0f2420", "#14532d"),
    ("#2a1a08", "#7c4a11"),
    ("#241018", "#5b1a2c"),
]


def demo_poster(tmdb_id: int, title: str, wide: bool = False) -> str:
    """Erzeugt ein schlichtes Poster als SVG - funktioniert auch ohne Internet."""
    dark, light = _POSTER_COLORS[tmdb_id % len(_POSTER_COLORS)]
    width, height = (1280, 720) if wide else (500, 750)

    # Titel auf mehrere Zeilen umbrechen, damit lange Namen nicht ueberstehen.
    words = title.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > 14 and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    font_size = 54 if wide else 44
    start_y = height / 2 - (len(lines) - 1) * font_size * 0.6
    text_lines = "".join(
        f'<text x="50%" y="{start_y + index * font_size * 1.2:.0f}" text-anchor="middle" '
        f'font-family="Segoe UI, system-ui, sans-serif" font-size="{font_size}" '
        f'font-weight="700" fill="#f2f2f5">{_escape(line)}</text>'
        for index, line in enumerate(lines)
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{dark}"/><stop offset="100%" stop-color="{light}"/>'
        f"</linearGradient></defs>"
        f'<rect width="{width}" height="{height}" fill="url(#g)"/>'
        f"{text_lines}"
        f'<text x="50%" y="{height - 40}" text-anchor="middle" '
        f'font-family="Segoe UI, system-ui, sans-serif" font-size="22" '
        f'fill="#f2f2f5" fill-opacity=".55" letter-spacing="3">DEMO</text>'
        f"</svg>"
    )


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
