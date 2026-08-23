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

# Streamingdienste mit ihrer TMDB-Kennung ("networks", nicht zu verwechseln mit
# den Produktionsfirmen oben - das sind zwei getrennte Namensraeume bei TMDB).
#
# Bewusst KEINE klassischen Fernsehsender: Streamingdienste sind weltweit
# dieselben, waehrend ARD, ZDF oder RTL fuer jemanden ausserhalb Deutschlands
# wertlos waeren. So braucht die Liste keine Laender-Logik.
#
# Wie bei STUDIOS gilt: Kennungen pruefen, nicht raten. Eine falsche Kennung
# liefert keinen Fehler, sondern schlicht nichts - eine leere Rubrik sieht aus
# wie "diese Woche kommt nichts".
# Alle Kennungen am 19.08.2026 gegen themoviedb.org/network/<id> geprueft.
NETWORKS: list[tuple[int, str]] = [
    (213, "Netflix"),
    (1024, "Prime Video"),
    (2739, "Disney+"),
    # TMDB fuehrt den Dienst seit der Umbenennung als "Apple TV" ohne Plus.
    # Die Kennung blieb dieselbe - deshalb nie ueber den Namen vergleichen.
    (2552, "Apple TV+"),
    # HBO braucht BEIDE Kennungen. Sie sind nicht alt gegen neu, sondern
    # aufgeteilt: TMDB ordnet jede Serie dem Haus zu, das sie beauftragt hat.
    # "The Last of Us" steht unter 49, "Hacks" und "Peacemaker" unter 3186.
    # Mit nur einer Kennung fehlte still ein grosser Teil des Katalogs.
    (49, "HBO"),
    (3186, "HBO Max"),
    (4330, "Paramount+"),
    (453, "Hulu"),
    (3353, "Peacock"),
]

NETWORK_IDS = frozenset(network_id for network_id, _ in NETWORKS)

# Wie TMDB die Art einer Veroeffentlichung nummeriert:
# 1 Premiere, 2 Kino (begrenzt), 3 Kino, 4 Digital, 5 Datentraeger, 6 TV.
KINO_ARTEN = (3, 2, 1)
DIGITAL_ARTEN = (4, 5)

# Die Werte fuer "with_release_type". Das Trennzeichen "|" bedeutet bei TMDB
# ODER - ein Komma waere UND und lieferte nichts.
RELEASE_TYPES = {
    "kino": "3|2|1",
    "digital": "4|5",
}

# Wie TMDB Sendungen einteilt:
# 0 Dokumentation, 1 Nachrichten, 2 Mehrteiler, 3 Reality, 4 Erzaehlend,
# 5 Talkshow, 6 Video.
#
# Ohne diese Einschraenkung ist die Rubrik "grosse Studios" bei Serien
# unbrauchbar: Netflix und Hulu fuehren bei TMDB auch ihre Begleit-Podcasts,
# Talkshows und Spielshows als Serien. Gemessen war rund die Haelfte der
# Treffer so etwas - "Outer Banks: The Official Podcast" neben "ESPN Jeopardy!".
# Uebrig bleiben Dokumentationen, Mehrteiler und erzaehlende Serien.
ERZAEHLENDE_SERIEN = "0|2|4"

# Aus welchen Laendern Neuerscheinungen ueberhaupt gezeigt werden.
#
# Netflix und die anderen Dienste produzieren weltweit, und TMDB fuehrt jede
# koreanische, thailaendische oder japanische Produktion unter demselben
# Sender. Fuer den Kalender ist das Rauschen: Titel, die hier niemand sucht.
#
# Bewusst nach Herkunftsland und nicht nach Sprache - so bleiben franzoesische
# und spanische Produktionen drin, die man hier durchaus sieht.
#
# Geprueft wird im eigenen Code, nicht ueber TMDB: Deren "with_origin_country"
# nimmt offiziell nur ein einziges Land, und ein stillschweigend ignorierter
# Parameter saehe aus wie "diese Woche kommt nichts".
HERKUNFTSLAENDER = frozenset({"DE", "AT", "CH", "US", "GB", "CA", "AU", "FR", "IT", "ES"})


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

    # Welche Art von Veroeffentlichung zaehlt (nur Filme). Die Vorbelegung ist
    # genau das, was frueher fest im TMDB-Client stand - so aendert sich fuer
    # die Entdecken-Seite nichts.
    release_types: str = "2|3"
    # Mehrere Produktionsfirmen bzw. Streamingdienste auf einmal, ODER-verknuepft
    # ("|"). Leer heisst: kein Filter. Haben Vorrang vor studio_id.
    company_ids: str = ""
    network_ids: str = ""
    # Art der Sendung (nur Serien), siehe SERIENARTEN. Leer = alles.
    series_types: str = ""

    # --- Kinderansicht ------------------------------------------------------
    # Mehrere Genres ODER-verknuepft ("16|10751"). Schlaegt ``genre_id``.
    #
    # Gebraucht fuer die Rubriken der Kinderansicht: "Abenteuer" ist bei Serien
    # kein einzelnes Genre, sondern "Action & Adventure" - und manche Rubriken
    # fassen ohnehin mehrere zusammen.
    genres_or: str = ""

    # Nur Filme: TMDB filtert selbst nach Altersfreigabe, wenn Land **und**
    # Bezeichnungen mitkommen ("DE" + "0|6|12").
    #
    # ⚠️ Das ist mehr als Bequemlichkeit. Nachtraeglich zu filtern hiesse, von
    # 20 geholten Titeln 2 zu behalten - gemessen an dieser Installation. Fuer
    # ein Kind waere die Seite damit praktisch leer. Bei Serien geht es nicht:
    # ``/discover/tv`` kennt den Filter nicht, dort tragen die Rubriken allein.
    certification_country: str | None = None
    certifications: str = ""

    def cache_key(self, media_type: str, textsprache: str = "") -> str:
        """Schluessel fuer den Zwischenspeicher.

        ``textsprache`` gehoert zwingend hinein - sie bestimmt, in welcher
        Sprache TMDB Titel und Beschreibungen liefert. Ohne sie bekaeme der
        naechste Benutzer die Fassung des vorherigen aus dem Speicher, obwohl
        er die Oberflaeche auf eine andere Sprache gestellt hat. Nicht zu
        verwechseln mit ``self.language``: das ist die *Originalsprache* als
        Filter, also in welcher Sprache gedreht wurde.

        Wichtig: Jedes Feld, das die Anfrage an TMDB veraendert, muss hier
        auftauchen. Sonst teilen sich zwei verschiedene Abfragen dieselbe Zeile
        im Zwischenspeicher, und wer zuerst kommt, liefert dem anderen sein
        Ergebnis - stundenlang und ohne Fehlermeldung. Die Kennung "v2" gehoert
        dazu, weil aeltere Eintraege nach dem gleichen Muster gebaut waren, aber
        eine andere Bedeutung hatten.
        """
        return (
            f"discover:v3:{media_type}:{textsprache}:{self.date_from}:{self.date_to}:"
            f"{self.language}:{self.region}:{self.genre_id}:{self.sort}:{self.page}:"
            f"{self.min_runtime}:{self.min_rating}:{self.hide_unrated}:"
            f"{self.released_in_region}:{self.studio_id}:{self.min_votes}:"
            f"{self.release_types}:{self.company_ids}:{self.network_ids}:{self.series_types}:"
            f"{self.genres_or}:{self.certification_country}:{self.certifications}"
        )
