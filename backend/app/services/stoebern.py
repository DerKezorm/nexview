"""Regale zum Stoebern im Rueckkatalog.

Die Entdecken-Seite ist ein **Erscheinungs-Radar**: Sie kennt nur ein
Zeitfenster von hoechstens einem Jahr. Die beiden Fragen, um die es beim
Filmabend wirklich geht - "eine Perle, die ich noch nicht kenne" und "etwas,
das ich lange nicht gesehen habe" - richten sich beide an den Rueckkatalog und
sind dort strukturell unbeantwortbar.

Dieses Modul beantwortet sie ueber **Regale**: fertige Filterkombinationen mit
einem Namen, die ohne Bibliothek und ohne persoenliche Daten funktionieren.

⚠️ **Anbieter-Neutralitaet.** Dieses Modul liest niemals aus
``services/mediaserver`` oder ``services/watchlist``. Persoenliche Daten kommen
ausschliesslich aus den neutralen Tabellen ``UserWatched`` und ``Favorite``.
Sonst waere jedes Regal beim naechsten Media-Server (Jellyfin) wieder kaputt.
Ein Test sichert das ab.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta

from ..schemas_media import MediaItem
from .filters import (
    BEKANNTE_TITEL_STIMMEN,
    EGAL_STIMMEN,
    EGAL_STIMMEN_TV,
    FILTER_JAHRZEHNTE,  # noqa: F401  - wird ueber diesen Dienst weitergereicht
    GENRE_STIMMEN,
    GENRE_STIMMEN_DOKU,
    GENRE_STIMMEN_DOKU_TV,
    GENRE_STIMMEN_TV,
    JAHRZEHNT_STIMMEN,
    JAHRZEHNT_STIMMEN_TV,
    KLASSIKER_MINDESTALTER_JAHRE,
    KLASSIKER_STIMMEN,
    KLASSIKER_STIMMEN_TV,
    KLASSIKER_WERTUNG,
    KNOWN_TITLES_MIN_VOTES,
    KURZER_ABEND_LAUFZEIT,
    KURZER_ABEND_STIMMEN,
    LAUFZEITEN,
    MIN_FEATURE_RUNTIME,
    NEU_TAGE,
    PERLEN_MINDESTALTER_JAHRE,
    PERLEN_STIMMEN,
    PERLEN_STIMMEN_TV,
    PERLEN_WERTUNG,
    PERLEN_WERTUNG_TV,
    DiscoverFilters,
)


class UnbekanntesRegal(LookupError):
    """Die Kennung gehoert zu keinem Regal."""


# --- Was in den Regalen steht ----------------------------------------------


@dataclass(frozen=True)
class Regal:
    """Ein Regal - Kennung plus die Frage, wo es ueberhaupt auftaucht.

    Der **Name** steht bewusst nicht hier. Er kommt als i18n-Schluessel
    (``stoebern.regal.<kennung>``) ins Frontend; ein fertiger Text hier waere
    einsprachig, und die App ist zweisprachig.
    """

    kennung: str
    medien_arten: tuple[str, ...] = ("movie", "tv")
    # Braucht das Regal Nutzerdaten? Solche Regale duerfen nie die einzigen
    # sein - eine frische Installation haette sonst eine leere Seite.
    persoenlich: bool = False
    # Wie das Regal auf der Uebersicht erscheint:
    #
    # * ``"reihe"``  - als geladene Kachelreihe. Kostet einen TMDB-Abruf plus
    #   bis zu 20 schlanke Detailabrufe.
    # * ``"kachel"`` - als blosser Verweis. Kostet nichts.
    #
    # Die Aufteilung ist kein Geschmack, sondern Rechnung: Zwanzig geladene
    # Reihen waeren rund 420 TMDB-Abrufe fuer eine einzige Seite. Drei Reihen
    # oben, der Rest als Kacheln - so bleibt die Uebersicht schnell und der
    # Mensch sieht trotzdem sofort, was es alles gibt.
    gruppe: str = "kachel"
    # Womit die Kachel gruppiert wird ("jahrzehnt", "genre"); leer = keine.
    kategorie: str = ""


# Genres, die als eigenes Regal auftauchen. Die TMDB-Kennungen sind stabil und
# unterscheiden sich zwischen Film und Serie: "Action" heisst bei Serien
# "Action & Adventure" (10759) und existiert als 28 dort gar nicht.
GENRE_REGALE: dict[str, tuple[tuple[str, int], ...]] = {
    "movie": (
        ("komoedie", 35),
        ("drama", 18),
        ("action", 28),
        ("thriller", 53),
        ("scifi", 878),
        ("horror", 27),
        ("animation", 16),
        ("doku", 99),
        ("familie", 10751),
        ("krimi", 80),
        ("abenteuer", 12),
        ("fantasy", 14),
    ),
    "tv": (
        ("komoedie", 35),
        ("drama", 18),
        ("krimi", 80),
        ("doku", 99),
        ("scifi", 10765),
        ("action", 10759),
        ("animation", 16),
        ("mystery", 9648),
    ),
}

GENRE_KENNUNGEN: dict[str, dict[str, int]] = {
    art: {name: kennung for name, kennung in eintraege}
    for art, eintraege in GENRE_REGALE.items()
}

# Welche Jahrzehnte ein eigenes Regal bekommen. Bewusst nicht bis heute: Das
# laufende Jahrzehnt deckt die Entdecken-Seite ohnehin ab.
#
# **Serien haben weniger.** Gemessen: Die 1970er ergeben bei Serien selbst mit
# einer Untergrenze von 50 Stimmen nur 20 Treffer - eine einzige Seite, und
# darauf Wrestling, Talkshows und Telenovelas. Ein Regal, das so aussieht, wirkt
# kaputt. TMDB hat fuer altes Fernsehen schlicht keine Daten; die 1990er sind
# mit 135 Treffern die aelteste Stufe, die noch traegt.
JAHRZEHNTE: dict[str, tuple[int, ...]] = {
    "movie": (1970, 1980, 1990, 2000, 2010),
    "tv": (1990, 2000, 2010),
}

# Die Reihenfolge auf der Uebersichtsseite. Perlen zuerst - das ist die Frage,
# mit der jemand herkommt.
_GRUNDREGALE: tuple[Regal, ...] = (
    # Der frueherer Menuepunkt "Filme/Serien entdecken", zusammengeschrumpft
    # auf das, was daran wirklich einzigartig war.
    Regal("neu", gruppe="reihe"),
    Regal("perlen", gruppe="reihe"),
    Regal("klassiker", gruppe="reihe"),
    Regal("kurz", medien_arten=("movie",), gruppe="reihe"),
)


def regale_fuer(media_type: str) -> list[Regal]:
    """Alle Regale einer Medienart, in Anzeigereihenfolge."""
    regale = [r for r in _GRUNDREGALE if media_type in r.medien_arten]
    regale += [
        Regal(f"jahrzehnt_{jahr}", kategorie="jahrzehnt")
        for jahr in reversed(JAHRZEHNTE[media_type])
    ]
    regale += [
        Regal(f"genre_{name}", kategorie="genre") for name, _ in GENRE_REGALE[media_type]
    ]
    return regale


# --- Persoenliche Regale ---------------------------------------------------
#
# ⚠️ Sie kommen **obendrauf**, nie als Tuersteher. Eine frische Installation
# ohne Herzen und ohne Media-Server muss die Stoeber-Seite genauso vollstaendig
# sehen wie eine eingelaufene - das ist die Lehre aus der Startseite, wo zwei
# von drei Reihen beim ersten Start gestrichelte Leerkaesten sind.

# Wie viele "Weil dir X gefaellt"-Reihen hoechstens erscheinen.
#
# Je Herz eine **eigene** Reihe, nicht alles zu einem Brei vermischt wie in
# ``home/curated``: Eine gemischte Liste sagt nicht, *warum* etwas
# vorgeschlagen wird, und genau das ist die nuetzliche Auskunft.
#
# ⚠️ Der Deckel allein reicht nicht. Der erste Bauversuch nahm stumpf die
# zuletzt markierten - wer hundert Favoriten hat, saehe die uebrigen
# sechsundneunzig **nie**. Deshalb ``herzen_fuer_heute``: ein Platz bleibt
# fest fuer das neueste Herz, die uebrigen wandern taeglich weiter.
MAX_WEIL_DU = 3

WEIL_DU = "weil_du_"


def weil_du_kennung(tmdb_id: int) -> str:
    return f"{WEIL_DU}{tmdb_id}"


def herzen_fuer_heute(herzen: list, heute: date | None = None) -> list:
    """Welche Favoriten heute eine Reihe bekommen - neuester plus Rotation.

    ``herzen`` kommt **neuestes zuerst**.

    Der erste Platz gehoert immer dem zuletzt markierten Titel: Wer gerade
    etwas mit dem Herz versehen hat, ist in dieser Stimmung, und das ist das
    frischeste Signal, das es gibt.

    Die uebrigen Plaetze wandern taeglich weiter. Bei hundert Favoriten sieht
    man so ueber die Zeit alle, statt immer dieselben drei - und die Seite
    aendert sich von selbst, ohne dass man etwas einstellen muss. Der
    Startpunkt haengt am Tag, nicht am Zufall: Innerhalb eines Tages bleibt
    die Seite stabil, sonst waere sie nach jedem Neuladen anders.
    """
    if len(herzen) <= MAX_WEIL_DU:
        return herzen

    heute = heute or date.today()
    rest = herzen[1:]
    # Der Startpunkt wandert jeden Tag um die Zahl der rotierenden Plaetze
    # weiter, damit ueber die Tage jeder Favorit an die Reihe kommt.
    plaetze = MAX_WEIL_DU - 1
    versatz = (heute.toordinal() * plaetze) % len(rest)
    gewaehlt = [rest[(versatz + i) % len(rest)] for i in range(plaetze)]
    return [herzen[0], *gewaehlt]


def weil_du_id(kennung: str) -> int | None:
    """Aus ``weil_du_603`` die 603 - oder ``None``, wenn es keine ist."""
    if not kennung.startswith(WEIL_DU):
        return None
    rest = kennung.removeprefix(WEIL_DU)
    return int(rest) if rest.isdigit() else None


def ist_persoenlich(kennung: str) -> bool:
    """Braucht dieses Regal Nutzerdaten statt einer TMDB-Abfrage?"""
    return kennung == "wieder" or weil_du_id(kennung) is not None


def regal_oder_404(kennung: str, media_type: str) -> Regal:
    """Ein Regal zur Kennung - oder ``UnbekanntesRegal``.

    Die Kennung kommt aus der Adresse, muss also gegen die Liste geprueft
    werden und nicht bloss geparst.
    """
    if ist_persoenlich(kennung):
        return Regal(kennung, persoenlich=True, gruppe="reihe", kategorie="persoenlich")
    for regal in regale_fuer(media_type):
        if regal.kennung == kennung:
            return regal
    raise UnbekanntesRegal(kennung)


def _jahresfenster(von: int, bis: int) -> tuple[str, str]:
    return f"{von}-01-01", f"{bis}-12-31"


def _doku_id(media_type: str) -> int:
    return GENRE_KENNUNGEN[media_type]["doku"]


def filter_fuer(
    kennung: str, media_type: str, *, page: int = 1, heute: date | None = None
) -> DiscoverFilters:
    """Die TMDB-Filter eines Regals.

    Wirft ``UnbekanntesRegal``, wenn die Kennung nicht passt - der Router macht
    daraus eine 404. Wichtig, weil die Kennungen aus der Adresse kommen.

    Filme und Serien bekommen durchgaengig **verschiedene** Untergrenzen. Das
    ist kein Detail: Serien sammeln bei TMDB deutlich weniger Stimmen, und
    dieselbe Zahl ergibt dort ein leeres Regal statt eines strengen.
    """
    heute = heute or date.today()
    ist_film = media_type == "movie"

    # Kurzfilme fliegen ueberall raus. Beim Stoebern ist das kein Schalter,
    # sondern Grundeinstellung: Muell rauszufiltern ist nicht die Aufgabe des
    # Menschen, der einen Film fuer heute Abend sucht.
    #
    # Bei Serien waere dieselbe Angabe die *Folgenlaenge* - vierzig Minuten
    # Mindestlaenge wuerfe jede Sitcom heraus.
    gemeinsam: dict = {
        "page": page,
        "min_runtime": MIN_FEATURE_RUNTIME if ist_film else None,
    }

    if kennung == "neu":
        # Was gerade herausgekommen ist. Die Stimmen-Untergrenze ist hier
        # besonders wichtig: TMDB fuehrt fuer ein Quartal ueber dreitausend
        # Filme, von denen die allermeisten keine Beschreibung und keine
        # Bewertung haben.
        return DiscoverFilters(
            date_from=(heute - timedelta(days=NEU_TAGE)).isoformat(),
            date_to=heute.isoformat(),
            min_votes=KNOWN_TITLES_MIN_VOTES,
            sort="popular",
            **gemeinsam,
        )

    if kennung == "perlen":
        # Zwei Bedingungen, und **beide** sind noetig:
        #
        # 1. Ein Stimmen-Fenster - bekannt genug, um eine echte
        #    Veroeffentlichung zu sein, unbekannt genug, um ein Fund zu sein.
        # 2. Eine Altersgrenze. Ohne sie misst die Stimmenzahl nur, wie neu
        #    ein Titel ist, und das Regal bestand aus lauter Blockbustern der
        #    laufenden Saison. Siehe PERLEN_MINDESTALTER_JAHRE.
        von, bis = PERLEN_STIMMEN if ist_film else PERLEN_STIMMEN_TV
        _, hoechstens = _jahresfenster(1900, heute.year - PERLEN_MINDESTALTER_JAHRE)
        return DiscoverFilters(
            date_to=hoechstens,
            min_votes=von,
            max_votes=bis,
            min_rating=PERLEN_WERTUNG if ist_film else PERLEN_WERTUNG_TV,
            # Innerhalb des Fensters sind die Noten ohnehin alle gut;
            # Beliebtheit holt das Ansehnlichere nach vorn. Nach Note sortiert
            # bestand das Regal ueberwiegend aus Musiker-Dokumentationen.
            sort="popular",
            **gemeinsam,
        )

    if kennung == "klassiker":
        _, bis = _jahresfenster(1900, heute.year - KLASSIKER_MINDESTALTER_JAHRE)
        return DiscoverFilters(
            date_to=bis,
            min_votes=KLASSIKER_STIMMEN if ist_film else KLASSIKER_STIMMEN_TV,
            min_rating=KLASSIKER_WERTUNG,
            sort="rating",
            **gemeinsam,
        )

    if kennung == "kurz":
        if not ist_film:
            raise UnbekanntesRegal(kennung)
        # "Wir haben nur anderthalb Stunden." Erst seit ``max_runtime``
        # ueberhaupt ausdrueckbar.
        return DiscoverFilters(
            max_runtime=KURZER_ABEND_LAUFZEIT,
            min_votes=KURZER_ABEND_STIMMEN,
            sort="rating",
            **gemeinsam,
        )

    if kennung.startswith("jahrzehnt_"):
        try:
            jahr = int(kennung.removeprefix("jahrzehnt_"))
        except ValueError as fehler:
            raise UnbekanntesRegal(kennung) from fehler
        if jahr not in JAHRZEHNTE[media_type]:
            raise UnbekanntesRegal(kennung)
        von, bis = _jahresfenster(jahr, jahr + 9)
        return DiscoverFilters(
            date_from=von,
            date_to=bis,
            min_votes=JAHRZEHNT_STIMMEN if ist_film else JAHRZEHNT_STIMMEN_TV,
            sort="rating",
            **gemeinsam,
        )

    if kennung.startswith("genre_"):
        name = kennung.removeprefix("genre_")
        genre_id = GENRE_KENNUNGEN[media_type].get(name)
        if genre_id is None:
            raise UnbekanntesRegal(kennung)
        # Dokumentationen bekommen eine eigene, viel niedrigere Grenze: Sie
        # sammeln kaum Stimmen. Mit der allgemeinen Grenze blieben bei Filmen
        # 26 und bei Serien 6 Treffer uebrig - ein Regal aus einer halben Zeile.
        ist_doku = genre_id == _doku_id(media_type)
        if ist_doku:
            stimmen = GENRE_STIMMEN_DOKU if ist_film else GENRE_STIMMEN_DOKU_TV
        else:
            stimmen = GENRE_STIMMEN if ist_film else GENRE_STIMMEN_TV
        return DiscoverFilters(
            genre_id=genre_id,
            min_votes=stimmen,
            # Nach Note, nicht nach Beliebtheit - sonst besteht jedes
            # Genre-Regal ausschliesslich aus Titeln des laufenden Jahres.
            sort="rating",
            **gemeinsam,
        )

    raise UnbekanntesRegal(kennung)


# --- Die freie Auswahl (Filterleiste) --------------------------------------
#
# Sechs Regler statt dreizehn - und vor allem: Jeder beantwortet eine Frage,
# die sich ein Mensch am Filmabend wirklich stellt.
#
# Die alte Entdecken-Seite hat dreizehn Bedienelemente, von denen acht nur
# existieren, weil die TMDB-Daten verrauscht sind: "Nur bekannte Titel", "Ohne
# Bewertung ausblenden", "Ohne Beschreibung ausblenden", "Nur Spielfilme",
# "Nur in DE erschienen", Originalsprache, Region. **Muell rauszufiltern ist
# nicht die Aufgabe des Menschen, der einen Film fuer heute Abend sucht.**
# Diese sieben sind hier stillschweigend Grundeinstellung.
#
# Was dagegen fehlte, ist das Einzige, was den Abend wirklich begrenzt: wie
# viel Zeit noch ist.


@dataclass(frozen=True)
class Wahl:
    """Was der Mensch in der Leiste eingestellt hat."""

    # "kurz" | "mittel" | "lang" | "egal"
    zeit: str = "egal"
    # TMDB-Genrenummern, ODER-verknuepft.
    genres: tuple[int, ...] = ()
    # Genres, die **nicht** vorkommen sollen ("heute kein Horror").
    ohne_genres: tuple[int, ...] = ()
    # "egal" | "aktuell" | "aelter" | ein Jahrzehnt als Text ("1990")
    epoche: str = "egal"
    # "egal" | "bekannt" | "geheimtipp"
    bekanntheit: str = "egal"
    # "rating" | "popular" | "newest"
    sortierung: str = "rating"


def zeitraum(epoche: str, heute: date) -> tuple[str | None, str | None]:
    """Aus der Epoche ein Datumsfenster machen.

    Echte Jahreszahlen statt "letzte 30 Tage": Der ganze Sinn dieser Seite ist
    der Rueckkatalog, und den erreicht ein relatives Fenster nie.
    """
    if epoche == "aktuell":
        return f"{heute.year - 3}-01-01", None
    if epoche == "aelter":
        return None, f"{FILTER_JAHRZEHNTE[-1] - 1}-12-31"
    if epoche.isdigit() and int(epoche) in FILTER_JAHRZEHNTE:
        jahr = int(epoche)
        return _jahresfenster(jahr, jahr + 9)
    return None, None


def filter_aus_wahl(
    wahl: Wahl, media_type: str, *, page: int = 1, heute: date | None = None
) -> DiscoverFilters:
    """Die Auswahl in TMDB-Filter uebersetzen."""
    heute = heute or date.today()
    ist_film = media_type == "movie"

    mindestens, hoechstens = LAUFZEITEN.get(wahl.zeit, LAUFZEITEN["egal"])
    von, bis = zeitraum(wahl.epoche, heute)

    min_votes: int | None
    max_votes: int | None = None
    if wahl.bekanntheit == "bekannt":
        min_votes = BEKANNTE_TITEL_STIMMEN
    elif wahl.bekanntheit == "geheimtipp":
        # Dasselbe Fenster wie im Perlen-Regal - samt Altersgrenze. Ohne sie
        # misst die Stimmenzahl nur, wie neu ein Titel ist.
        min_votes, max_votes = PERLEN_STIMMEN if ist_film else PERLEN_STIMMEN_TV
        grenze = f"{heute.year - PERLEN_MINDESTALTER_JAHRE}-12-31"
        bis = min(bis, grenze) if bis else grenze
    else:
        min_votes = EGAL_STIMMEN if ist_film else EGAL_STIMMEN_TV

    return DiscoverFilters(
        date_from=von,
        date_to=bis,
        genres_or="|".join(str(g) for g in wahl.genres),
        without_genres="|".join(str(g) for g in wahl.ohne_genres),
        # Kurzfilme fliegen still raus - kein Schalter, Grundeinstellung.
        # Bei Serien waere es die Folgenlaenge und damit etwas anderes.
        min_runtime=mindestens if ist_film else None,
        max_runtime=hoechstens if ist_film else None,
        min_votes=min_votes,
        max_votes=max_votes,
        sort=wahl.sortierung,
        page=page,
    )


# --- Sieben nach dem eigenen Bestand ---------------------------------------

# Zustaende, die "liegt schon hier" bedeuten - im Sinne von "kann heute Abend
# sofort laufen". Angefragt oder in der Suche zaehlt ausdruecklich **nicht**:
# Die Datei ist dann ja noch nicht da.
VORHANDEN = frozenset({"downloaded", "partial", "in_library"})

# Zustaende, die "muss ich nicht mehr anfragen" bedeuten. Groesser als
# VORHANDEN, weil eine laufende Anfrage ein zweites Mal nichts bringt.
ERLEDIGT = VORHANDEN | {"searching", "requested", "pending_approval"}

# So viele TMDB-Seiten werden hoechstens durchgesehen, bis genug uebrig ist.
#
# Bewusst niedriger als die 12 der Startseite: Dort geht es um eine einzige
# Reihe, hier um jedes Regal. Und wenn nach fuenf Seiten wenig uebrig ist, ist
# die ehrliche Antwort "dazu passt bei dir wenig" - nicht fuenfzig Seiten tief
# zu graben.
MAX_SEITEN = 5


def laufzeit_pruefer(filter_: DiscoverFilters) -> Callable[[MediaItem], bool]:
    """Die Laufzeit **noch einmal** pruefen, nachdem die Details da sind.

    ⚠️ Der TMDB-Filter ``with_runtime`` ist unzuverlaessig: Gemessen am
    23.08.2026 lieferte ``with_runtime.lte=95`` unter anderem "Young Hearts"
    (97 Min.) und den Miraculous-Film (99 Min.). Offenbar weicht die Laufzeit
    im Suchindex von der im Datensatz ab.

    Das ist keine Kleinigkeit, sondern genau die Zusage, die der Mensch
    gepruefte hat: Wer "hoechstens 90 Minuten" waehlt, hat einen Grund dafuer.
    ``_to_items`` holt die echte Laufzeit ohnehin fuer jede Kachel - sie hier
    zu vergleichen kostet also nichts extra.

    Titel **ohne** Laufzeitangabe fallen bei gesetzter Obergrenze weg: In einer
    Zusage ist "unbekannt" nicht "passt schon".
    """

    def passt(item: MediaItem) -> bool:
        dauer = item.runtime_minutes
        if filter_.max_runtime is not None and (not dauer or dauer > filter_.max_runtime):
            return False
        return not (filter_.min_runtime is not None and dauer and dauer < filter_.min_runtime)

    return passt


@dataclass
class Ausbeute:
    """Was das Sieb gefunden hat - samt der Frage, ob es zu Ende gesucht hat."""

    items: list[MediaItem]
    seiten_durchsucht: int
    # True, wenn TMDB keine weiteren Seiten mehr hat. Dann ist "wenig
    # gefunden" eine endgueltige Aussage und keine Frage der Geduld - und die
    # Oberflaeche darf das sagen, statt einen Knopf "mehr laden" anzubieten,
    # hinter dem nichts mehr kommt.
    erschoepft: bool
    total_pages: int


async def sammle(
    hole_seite: Callable[[int], Awaitable[tuple[list[MediaItem], int]]],
    mit_zustand: Callable[[list[MediaItem]], Awaitable[list[MediaItem]]],
    *,
    modus: str,
    ziel: int,
    erste_seite: int = 1,
    max_seiten: int = MAX_SEITEN,
    zusaetzlich: Callable[[MediaItem], bool] | None = None,
) -> Ausbeute:
    """Titel holen und nach dem eigenen Bestand sieben - **serverseitig**.

    Die Entdecken-Seite siebt erst im Browser, nachdem die Seite geladen ist.
    Bei einer gefuellten Bibliothek bleiben von 20 Kacheln zwei oder drei
    uebrig und "Mehr laden" wirkt kaputt. Bei leerer Bibliothek faellt das
    niemandem auf - genau der Fall, den man nicht am eigenen Setup misst.

    ``mit_zustand`` bekommt die Zustandsberechnung von aussen gereicht, statt
    sie hier ein drittes Mal nachzubauen. Die Rangfolge (Sperre schlaegt alles,
    Datei schlaegt Anfrage) gehoert an genau eine Stelle.

    ``modus``:
      * ``"nur_vorhanden"`` - was heute Abend sofort laufen kann
      * ``"nur_neu"``       - was sich noch anfragen laesst
      * ``"egal"``          - kein Sieb, eine Seite
    """
    if modus not in ("nur_vorhanden", "nur_neu", "egal"):
        raise ValueError(f"Unbekannter Modus: {modus}")

    # Ohne Sieb wuerde eine Nachpruefung die Seite bloss verkuerzen, statt
    # nachzuladen - deshalb zaehlt sie als Sieb.
    siebt = modus != "egal" or zusaetzlich is not None

    gesammelt: list[MediaItem] = []
    kennungen: set[int] = set()
    seiten = 0
    letzte_gesamtzahl = 1
    erschoepft = False

    # Ohne Sieb hat Nachladen keinen Sinn: Die Seite ist genau so lang, wie
    # TMDB sie liefert.
    grenze = max_seiten if siebt else 1

    for versatz in range(grenze):
        seite = erste_seite + versatz
        roh, total_pages = await hole_seite(seite)
        letzte_gesamtzahl = max(total_pages, 1)
        seiten += 1

        if not roh:
            erschoepft = True
            break

        for item in await mit_zustand(roh):
            if item.tmdb_id in kennungen:
                continue
            if modus == "nur_vorhanden" and item.status not in VORHANDEN:
                continue
            if modus == "nur_neu" and item.status in ERLEDIGT:
                continue
            if zusaetzlich is not None and not zusaetzlich(item):
                continue
            kennungen.add(item.tmdb_id)
            gesammelt.append(item)

        if len(gesammelt) >= ziel:
            break
        if seite >= letzte_gesamtzahl:
            erschoepft = True
            break

    return Ausbeute(
        items=gesammelt[:ziel],
        seiten_durchsucht=seiten,
        erschoepft=erschoepft,
        total_pages=letzte_gesamtzahl,
    )
