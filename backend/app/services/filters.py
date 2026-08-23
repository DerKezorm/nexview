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

# Untergrenze beim Sortieren nach Bewertung.
#
# Frueher standen hier 5 Stimmen. Das machte "Beste Bewertung" wertlos: Ein
# Film mit 20 Stimmen und 9,4 stand ueber "Der Pate". TMDB kennt keine
# gewichtete Sortierung (kein bayessches Mittel), deshalb ist eine harte
# Untergrenze das einzige Mittel. 300 ist die Schwelle, ab der eine
# Durchschnittsnote nicht mehr von einer Handvoll Fans getragen wird.
MIN_VOTES_FOR_RATING_SORT = 300

# --- Regale beim Stoebern ------------------------------------------------
#
# ⚠️ Alle Zahlen hier sind **gemessen**, nicht geschaetzt: am 23.08.2026 gegen
# die echte TMDB-API, mit Blick auf die tatsaechlichen Titel und die Zahl der
# Treffer. Wer sie aendert, sollte genauso nachsehen - ein zu hoher Wert macht
# ein Regal leer, ein zu niedriger fuellt es mit Rauschen. Beides sieht aus wie
# ein Defekt.
#
# Der wichtigste Fund dabei: **Die Stimmenzahl misst nicht Unbekanntheit,
# sondern Alter mal Beliebtheit.** Ein Kinofilm von letzter Woche hat nach
# zwei Wochen ein paar hundert Stimmen - genau wie eine echte Perle von 2015.
# Ohne Altersgrenze bestand das Perlen-Regal darum aus lauter aktuellen
# Blockbustern (gemessen: 7 von 8 Treffern aus 2026, darunter Spider-Man und
# Toy Story 5). Erst die Altersgrenze macht aus "wenige Stimmen" wirklich
# "kennt kaum jemand".
PERLEN_MINDESTALTER_JAHRE = 2

# Filme: Fenster 200-2500 Stimmen bei mindestens 7,5.
#
# 7,2 war zu niedrig - dabei standen "Paw Patrol: Der Kinofilm" und "Trolls
# World Tour" im Regal, beide am oberen Rand des Fensters. Mit 7,5 bleiben
# Titel wie "Lebewohl, meine Konkubine", "The Gangster, The Cop, The Devil"
# und "Die glorreichen Sieben" - 1104 Treffer, genug Tiefe.
PERLEN_STIMMEN = (200, 2500)
PERLEN_WERTUNG = 7.5

# Serien sammeln deutlich weniger Stimmen als Filme; dieselben Grenzen liefern
# dort nur bekannte Ware (Silo, Outer Banks). Enger und strenger gemessen:
# 587 Treffer, darunter "Agatha Christie's Poirot" und "The West Wing".
PERLEN_STIMMEN_TV = (150, 1000)
PERLEN_WERTUNG_TV = 8.0

# Zeitlose Klassiker: viel gesehen, hoch bewertet, lange genug her.
# 3000 Stimmen ergeben bei Filmen 298 Treffer (Der Pate, Die Verurteilten,
# Schindlers Liste) - bei Serien aber nur 48. Deshalb dort 800: 225 Treffer,
# angefuehrt von Breaking Bad und den Sopranos.
KLASSIKER_STIMMEN = 3000
KLASSIKER_STIMMEN_TV = 800
KLASSIKER_WERTUNG = 7.5
KLASSIKER_MINDESTALTER_JAHRE = 15

# Jahrzehnte. Bei Filmen tragen 500 Stimmen bis zurueck in die 1970er
# (244 Treffer, Der Pate / Apocalypse Now / Einer flog ueber das Kuckucksnest).
JAHRZEHNT_STIMMEN = 500
JAHRZEHNT_STIMMEN_TV = 200

# Genre-Regale. Nach **Note** sortiert, nicht nach Beliebtheit: Mit
# "popularity.desc" bestand jedes Genre-Regal nur aus Titeln des laufenden
# Jahres - fuer eine Seite, die den *Rueckkatalog* zeigen soll, genau falsch.
# Nach Note kommen Parasite, Pulp Fiction und Zurueck in die Zukunft.
#
# 1000 Stimmen tragen bei Filmen jedes Genre (schwaechstes: Animation mit 439)
# - **ausser Dokumentationen**, die nur 26 Treffer haetten. Dokumentarfilme
# sammeln kaum Stimmen; sie brauchen eine eigene, viel niedrigere Grenze.
# Serien liegen generell tiefer (bei 1000 haette Krimi noch 124 Treffer).
GENRE_STIMMEN = 1000
GENRE_STIMMEN_TV = 300
GENRE_STIMMEN_DOKU = 200
GENRE_STIMMEN_DOKU_TV = 100

# "Wir haben nur anderthalb Stunden" - Obergrenze in Minuten, nach Note
# sortiert. Ergibt Die zwoelf Geschworenen, Die letzten Gluehwuermchen,
# Der wilde Roboter, Perfect Blue, Moderne Zeiten.
KURZER_ABEND_LAUFZEIT = 90
KURZER_ABEND_STIMMEN = 1000

# Wie weit "Neu erschienen" zurueckreicht.
#
# Das Regal ersetzt die frueheren Menuepunkte "Filme/Serien entdecken", die als
# Erscheinungs-Radar gebaut waren. Neunzig Tage statt dreissig: Ein Monat ist
# zu kurz, um eine Reihe zu fuellen, sobald man das Vorhandene aussiebt - und
# wer es taggenau will, nimmt ohnehin den Kalender.
#
# Die Stimmen-Untergrenze ist ``KNOWN_TITLES_MIN_VOTES`` (20), gemessen am
# 23.08.2026: Ohne sie hat das Fenster **3472** Treffer, mit ihr **110** - und
# die ersten vier sind in beiden Faellen dieselben. Das Weggesiebte ist also
# vollstaendig Rauschen. Bei Serien: 2181 gegen 61.
NEU_TAGE = 90

# "Grosser Titel" im Assistenten und im Bekanntheits-Regler.
BEKANNTE_TITEL_STIMMEN = 1500

# Untergrenze, wenn die Bekanntheit **egal** ist.
#
# Nicht null: Ohne jede Grenze besteht der Rueckkatalog aus namenlosen
# Kleinstproduktionen ohne Beschreibung. Aber deutlich niedriger als die
# Genre-Regale, damit ein Nischen-Genre nicht leer bleibt.
EGAL_STIMMEN = 300
EGAL_STIMMEN_TV = 100

# --- Vokabeln, die Assistent und Filterleiste teilen ---------------------
#
# Beide muessen dasselbe unter "hoechstens 90 Minuten" verstehen. Zwei
# Definitionen liefen beim ersten Feinschliff auseinander, und der Unterschied
# faellt niemandem auf: Die Leiste verspraeche 90 und lieferte 95.
#
# (Mindestlaufzeit, Hoechstlaufzeit) in Minuten; ``None`` = keine Grenze.
# ⚠️ Die Stufen muessen **lueckenlos** aneinanderstossen. Vorher endete
# "mittel" bei 125 und "lang" begann bei 130 - jeder Film zwischen 126 und 129
# Minuten fiel durch **beide** Optionen und war ueber die Laufzeit gar nicht
# auffindbar. Solche Luecken sieht man nicht, man merkt nur, dass ein Titel
# "irgendwie nie kommt".
#
# ⚠️ Und die Zahlen muessen zur Beschriftung passen. "lang" hiess einmal
# "Darf lang sein" - das liest sich als *ohne Obergrenze*, gebaut war aber
# *mindestens zwei Stunden*, also genau das Gegenteil. Wer das Etikett aendert,
# aendert hier mit; wer die Zahl aendert, aendert das Etikett mit.
# Die Beschriftungen stehen unter ``stoebern.filter.zeit_*`` und
# ``stoebern.filmabend.zeit.*``.
LAUFZEITEN: dict[str, tuple[int | None, int | None]] = {
    "kurz": (MIN_FEATURE_RUNTIME, 90),
    "mittel": (MIN_FEATURE_RUNTIME, 120),
    "lang": (121, None),
    "egal": (MIN_FEATURE_RUNTIME, None),
}

# Welche Jahrzehnte die Filterleiste anbietet - dieselben wie die Regale.
FILTER_JAHRZEHNTE = (2020, 2010, 2000, 1990, 1980, 1970)

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
    # Hoechstlaufzeit in Minuten; None = keine Obergrenze.
    #
    # Beantwortet die einzige Frage, die am Filmabend wirklich bindet: "Wie
    # viel Zeit haben wir noch?" Die Entdecken-Seite kannte sie nie.
    max_runtime: int | None = None
    # Mindestbewertung, z. B. 7.0
    min_rating: float | None = None
    # Titel ohne jede Bewertung ausblenden
    hide_unrated: bool = False
    # Mindestanzahl Stimmen ("nur bekannte Titel"); None = keine Untergrenze
    min_votes: int | None = None
    # Hoechstzahl Stimmen; None = keine Obergrenze. Zusammen mit ``min_votes``
    # ergibt das ein Fenster - siehe PERLEN_STIMMEN.
    max_votes: int | None = None
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

    # --- Stoebern -----------------------------------------------------------
    # Genres, die ausgeschlossen werden ("27|53" = kein Horror, kein Thriller).
    # ODER-verknuepft wie ``genres_or``: TMDB wirft jeden Titel raus, der
    # mindestens eines davon traegt.
    without_genres: str = ""
    # Personen, ODER-verknuepft ("mehr von Denis Villeneuve"). Bisher wurden
    # dafuer die kompletten Filmografien einzeln geholt und im Code sortiert.
    people_ids: str = ""

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
        Ergebnis - stundenlang und ohne Fehlermeldung. Die Kennung "v4" gehoert
        dazu, weil aeltere Eintraege nach dem gleichen Muster gebaut waren, aber
        eine andere Bedeutung hatten.
        """
        return (
            f"discover:v4:{media_type}:{textsprache}:{self.date_from}:{self.date_to}:"
            f"{self.language}:{self.region}:{self.genre_id}:{self.sort}:{self.page}:"
            f"{self.min_runtime}:{self.max_runtime}:{self.min_rating}:{self.hide_unrated}:"
            f"{self.released_in_region}:{self.studio_id}:{self.min_votes}:{self.max_votes}:"
            f"{self.release_types}:{self.company_ids}:{self.network_ids}:{self.series_types}:"
            f"{self.genres_or}:{self.certification_country}:{self.certifications}:"
            f"{self.without_genres}:{self.people_ids}"
        )
