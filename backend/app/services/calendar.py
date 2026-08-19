"""Der Erscheinungs-Kalender: was wann kommt.

Bringt drei Quellen auf eine Zeitleiste:

* **Sonarr** - welche Folgen der eigenen Serien laufen, und ob sie schon da sind.
* **Radarr** - wann die eigenen Filme erscheinen.
* **TMDB** - was es sonst noch Neues gibt, in der Region des Benutzers.

Die beiden ersten sind "meine Titel", die dritte sind "Neuerscheinungen". Der
Unterschied ist nicht die Art des Datums, sondern die Auswahl: das eine ist der
eigene Bestand, das andere der Markt.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..mocks import demo_data
from ..models import MediaRequest, MediaServerLibraryItem, MediaType
from ..schemas_calendar import CalendarDay, CalendarEntry, CalendarResult
from . import cache, library, media
from .arr import ArrError
from .filters import (
    DIGITAL_ARTEN,
    ERZAEHLENDE_SERIEN,
    HERKUNFTSLAENDER,
    KINO_ARTEN,
    KNOWN_TITLES_MIN_VOTES,
    NETWORK_IDS,
    RELEASE_TYPES,
    STUDIO_IDS,
    DiscoverFilters,
)
from .settings_service import AppSettings
from .tmdb import TmdbError

logger = logging.getLogger("nexview.calendar")

# Standard-Fenster: ab heute, vier Wochen voraus.
#
# Bewusst ohne Rueckblick. Ein Kalender, der mit einem Datum von vor drei
# Wochen beginnt, beantwortet die falsche Frage zuerst - man will wissen, was
# kommt, nicht was war. Wer zurueckschauen will, dehnt den Zeitraum ueber den
# Knopf; dann erscheinen auch die Hinweise auf Ausgestrahltes, das noch fehlt.
STANDARD_ZURUECK = 0
STANDARD_VORAUS = 28
# Obergrenze fuer selbst gewaehlte Zeitraeume. Begrenzt zugleich, wie viele
# TMDB-Seiten zusammenkommen koennen.
MAX_TAGE = 120

# Wie viele Seiten je TMDB-Abfrage. Drei Seiten sind 60 Titel - mehr liest
# ohnehin niemand durch.
MAX_SEITEN = 3

# Obergrenze fuer die teuerste Art der Zuordnung (ein TMDB-Aufruf je Serie).
MAX_TVDB_ABFRAGEN = 20
TVDB_TTL = 30 * 24 * 3600


def heute() -> str:
    """Der heutige Tag in der Zeitzone des Servers.

    Bewusst die Serverzeit: Sie bestimmt auch die Grenzen des Zeitraums, und
    der Haushalt lebt in derselben Zone wie sein Server. Laeuft der Container
    ohne gesetztes ``TZ``, ist das UTC - dann verrutschen spaete Ausstrahlungen
    um einen Tag. Deshalb steht ``TZ`` in der Compose-Datei.
    """
    return date.today().isoformat()


def standard_zeitraum() -> tuple[str, str]:
    """Vorgabe-Fenster - absichtlich auf dem Server gerechnet.

    Im Browser waere es verlockend, das aus ``today()`` zu bauen; das rechnet
    dort aber ueber UTC, sodass ein Aufruf um 23 Uhr schon den naechsten Tag
    liefert und die Zeitleiste einen Tag zu frueh beginnt.
    """
    jetzt = date.today()
    return (
        (jetzt - timedelta(days=STANDARD_ZURUECK)).isoformat(),
        (jetzt + timedelta(days=STANDARD_VORAUS)).isoformat(),
    )


# --- Kleine Helfer ---------------------------------------------------------


def _lokaler_tag(zeitpunkt: str | None) -> str | None:
    """UTC-Zeitstempel auf den lokalen Kalendertag bringen.

    Sonarr liefert ``airDateUtc``. Eine Serie, die in den USA um 21 Uhr laeuft,
    traegt dort 01:30 UTC des Folgetags - in Deutschland ist das derselbe
    Morgen, in Kalifornien noch der Vorabend. Ohne Umrechnung stuende die Folge
    im Kalender am falschen Tag.
    """
    if not zeitpunkt:
        return None
    try:
        gelesen = datetime.fromisoformat(zeitpunkt.replace("Z", "+00:00"))
    except ValueError:
        # Manche Felder tragen nur "2026-08-19" - dann ist der Tag schon da.
        return zeitpunkt[:10] if len(zeitpunkt) >= 10 else None
    if gelesen.tzinfo is None:
        return gelesen.date().isoformat()
    return gelesen.astimezone().date().isoformat()


def _poster(images: Any) -> str | None:
    """Posteradresse aus einem Radarr-/Sonarr-Eintrag.

    Beide speichern die Fernadresse des Posters mit - meist zeigt sie direkt
    auf TMDB. Das spart eine eigene Abfrage nur fuer das Bild.
    """
    if not isinstance(images, list):
        return None
    for bild in images:
        if not isinstance(bild, dict):
            continue
        if bild.get("coverType") != "poster":
            continue
        adresse = bild.get("remoteUrl") or bild.get("url")
        if isinstance(adresse, str) and adresse.startswith("http"):
            return adresse
    return None


def _folgen_label(staffel: int, nummern: list[int]) -> str:
    """"S03E05", "S03E05-06" oder "S03E05, 07".

    Zweistellig aufgefuellt, weil Sonarr das auch so schreibt und weil "E5"
    neben "E12" unruhig aussieht.
    """
    nummern = sorted(set(nummern))
    if not nummern:
        return f"S{staffel:02d}"
    if len(nummern) == 1:
        return f"S{staffel:02d}E{nummern[0]:02d}"
    # Lueckenlos? Dann ist ein Bereich die ehrliche Kurzform.
    if nummern[-1] - nummern[0] + 1 == len(nummern):
        return f"S{staffel:02d}E{nummern[0]:02d}–{nummern[-1]:02d}"
    if len(nummern) <= 3:
        return f"S{staffel:02d}E" + ", ".join(f"{n:02d}" for n in nummern)
    return f"S{staffel:02d}E{nummern[0]:02d}–{nummern[-1]:02d} ({len(nummern)})"


def _status_fuer(alle_da: bool) -> str:
    """Zustand in derselben Sprache wie ueberall sonst.

    Genau dieselbe Regel wie ``library._status_for``: Liegt die Datei vor,
    heisst es "bereits geladen", sonst "wird gesucht". Ob der Termin schon
    vorbei ist, spielt hier bewusst **keine** Rolle - die App macht diesen
    Unterschied an keiner anderen Stelle, und ein eigener Wert nur fuer den
    Kalender liesse dieselbe Serie je nach Seite anders aussehen.

    Ob etwas ueberfaellig ist, sagt stattdessen das Kennzeichen "fehlt noch"
    neben dem Zustand. Das ist die andere Achse und verdeckt den Zustand nicht.
    """
    return "downloaded" if alle_da else "searching"


# --- Sonarr ----------------------------------------------------------------


def _falte_folgen(rohe: list[dict[str, Any]], stichtag: str) -> list[CalendarEntry]:
    """Eine Zeile je Serie, Staffel und Tag.

    Laufen an einem Abend drei Folgen (bei Streamingdiensten die Regel), waeren
    drei gleiche Kacheln nur Rauschen. Die Staffel gehoert in den Schluessel:
    Ein Staffelfinale und der Auftakt der naechsten Staffel koennen am selben
    Tag liegen, und "S03E13-S04E01" waere kein Bereich, sondern Unsinn.
    """
    gruppen: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    serien: dict[int, dict[str, Any]] = {}

    for folge in rohe:
        tag = _lokaler_tag(folge.get("airDateUtc") or folge.get("airDate"))
        staffel = folge.get("seasonNumber")
        serie = folge.get("series") or {}
        serien_id = folge.get("seriesId") or serie.get("id")
        if tag is None or not isinstance(staffel, int) or serien_id is None:
            continue
        gruppen.setdefault((tag, serien_id, staffel), []).append(folge)
        serien[serien_id] = serie

    eintraege: list[CalendarEntry] = []
    for (tag, serien_id, staffel), folgen in gruppen.items():
        serie = serien.get(serien_id) or {}
        nummern = [f["episodeNumber"] for f in folgen if isinstance(f.get("episodeNumber"), int)]
        fehlend = [
            f["episodeNumber"]
            for f in folgen
            if isinstance(f.get("episodeNumber"), int) and not f.get("hasFile")
        ]
        ausgestrahlt = tag <= stichtag
        alle_da = bool(folgen) and all(f.get("hasFile") for f in folgen)

        eintraege.append(
            CalendarEntry(
                key=f"sonarr:{serien_id}:{staffel}:{tag}",
                date=tag,
                source="meine",
                origin="sonarr",
                media_type=MediaType.tv,
                tmdb_id=serie.get("tmdbId") if isinstance(serie.get("tmdbId"), int) else None,
                tvdb_id=serie.get("tvdbId") if isinstance(serie.get("tvdbId"), int) else None,
                title=serie.get("title") or "",
                poster_url=_poster(serie.get("images")),
                overview=(folgen[0].get("overview") or "") if len(folgen) == 1 else "",
                vote_average=round(float((serie.get("ratings") or {}).get("value") or 0), 1),
                genres=[g for g in (serie.get("genres") or []) if isinstance(g, str)][:3],
                certification=serie.get("certification"),
                status=_status_fuer(alle_da),
                season=staffel,
                episode_label=_folgen_label(staffel, nummern),
                # Nur bei genau einer Folge: sonst stuende ein Titel
                # stellvertretend fuer mehrere und fuehrte in die Irre.
                episode_title=folgen[0].get("title") if len(folgen) == 1 else None,
                missing_episodes=sorted(fehlend),
                aired=ausgestrahlt,
                missing=bool(fehlend) and ausgestrahlt and bool(serie.get("monitored")),
            )
        )
    return eintraege


# --- Radarr ----------------------------------------------------------------


def _film_termin(eintrag: dict[str, Any], datumsart: str) -> tuple[str, str] | None:
    """Welches der drei Radarr-Daten zaehlt - und wie es heisst.

    Bei "digital" gilt die digitale Veroeffentlichung, sonst der Datentraeger.
    Kennt Radarr beides nicht, faellt der Eintrag auf den Kinostart zurueck -
    aber ausdruecklich als "Kino" beschriftet. Ihn unter "Digital" zu zeigen,
    waere eine Behauptung, die niemand gepruefet hat.
    """
    kino = (eintrag.get("inCinemas") or "")[:10]
    digital = (eintrag.get("digitalRelease") or "")[:10]
    physisch = (eintrag.get("physicalRelease") or "")[:10]

    if datumsart == "kino":
        return (kino, "kino") if kino else None

    if digital and physisch:
        return (digital, "digital") if digital <= physisch else (physisch, "physisch")
    if digital:
        return digital, "digital"
    if physisch:
        return physisch, "physisch"
    return (kino, "kino") if kino else None


def _meine_filme(
    rohe: list[dict[str, Any]], datumsart: str, von: str, bis: str, stichtag: str
) -> list[CalendarEntry]:
    eintraege: list[CalendarEntry] = []
    for film in rohe:
        termin = _film_termin(film, datumsart)
        if termin is None:
            continue
        tag, art = termin
        # Radarr filtert nach *irgendeinem* seiner Daten. Sobald wir uns fuer
        # eines entscheiden, kann es ausserhalb des Fensters liegen.
        if not (von <= tag <= bis):
            continue

        ausgestrahlt = tag <= stichtag
        hat_datei = bool(film.get("hasFile"))
        kennung = film.get("tmdbId")

        eintraege.append(
            CalendarEntry(
                key=f"radarr:{film.get('id')}:{tag}",
                date=tag,
                source="meine",
                origin="radarr",
                media_type=MediaType.movie,
                tmdb_id=kennung if isinstance(kennung, int) else None,
                title=film.get("title") or "",
                poster_url=_poster(film.get("images")),
                overview=film.get("overview") or "",
                vote_average=round(
                    float(((film.get("ratings") or {}).get("tmdb") or {}).get("value") or 0), 1
                ),
                genres=[g for g in (film.get("genres") or []) if isinstance(g, str)][:3],
                runtime_minutes=film.get("runtime") or None,
                certification=film.get("certification"),
                status=_status_fuer(hat_datei),
                date_type=art,  # type: ignore[arg-type]
                aired=ausgestrahlt,
                missing=ausgestrahlt and not hat_datei and bool(film.get("monitored")),
            )
        )
    return eintraege


# --- TMDB ------------------------------------------------------------------


def _filter(
    *, von: str, bis: str, region: str, datumsart: str, schaerfe: str, seite: int, fuer_film: bool
) -> DiscoverFilters:
    """Filter fuer eine Kalender-Abfrage bei TMDB."""
    studios = "|".join(str(kennung) for kennung in sorted(STUDIO_IDS))
    sender = "|".join(str(kennung) for kennung in sorted(NETWORK_IDS))

    return DiscoverFilters(
        date_from=von,
        date_to=bis,
        region=region,
        sort="soonest",
        page=seite,
        released_in_region=fuer_film,
        release_types=RELEASE_TYPES["kino" if datumsart == "kino" else "digital"],
        company_ids=studios if (schaerfe == "studios" and fuer_film) else "",
        network_ids=sender if (schaerfe == "studios" and not fuer_film) else "",
        series_types=ERZAEHLENDE_SERIEN if (schaerfe == "studios" and not fuer_film) else "",
        min_votes=KNOWN_TITLES_MIN_VOTES if schaerfe == "known" else None,
    )


async def _neuerscheinungen(
    db: Session,
    settings: AppSettings,
    *,
    von: str,
    bis: str,
    region: str,
    datumsart: str,
    schaerfe: str,
    stichtag: str,
) -> list[CalendarEntry]:
    """Was es sonst noch Neues gibt - Filme und Serienpremieren.

    Bei Serien zaehlt bewusst das *Erstausstrahlungsdatum*: Neuerscheinungen
    sind neue Serien. Neue Staffeln laufender Serien stehen ohnehin unter
    "meine Titel", sofern man sie verfolgt.
    """
    # Serien laufen nicht im Kino. Unter dieser Auswahl haben sie nichts zu
    # suchen - und die drei Abfragen dafuer sparen wir uns gleich mit.
    arten_liste = ("movie",) if datumsart == "kino" else ("movie", "tv")

    aufgaben = []
    for art in arten_liste:
        for seite in range(1, MAX_SEITEN + 1):
            aufgaben.append(
                media.discover(
                    db,
                    settings,
                    art,
                    _filter(
                        von=von,
                        bis=bis,
                        region=region,
                        datumsart=datumsart,
                        schaerfe=schaerfe,
                        seite=seite,
                        fuer_film=art == "movie",
                    ),
                )
            )

    seiten = await asyncio.gather(*aufgaben, return_exceptions=True)

    filme: list[Any] = []
    serien: list[Any] = []
    for index, seite in enumerate(seiten):
        if isinstance(seite, BaseException):
            continue
        (filme if index < MAX_SEITEN else serien).extend(seite.items)

    # Der wichtige Teil: TMDB filtert zwar nach dem regionalen Datum, liefert
    # in der Liste aber das weltweite. Ohne diesen Schritt stuende ein Film mit
    # Kinostart im Maerz und digitaler Veroeffentlichung im Juni unter Maerz.
    arten = KINO_ARTEN if datumsart == "kino" else DIGITAL_ARTEN
    termine = await media.regionale_daten(
        db, settings, [film.tmdb_id for film in filme], region, arten
    )

    eintraege: list[CalendarEntry] = []
    gesehen: set[str] = set()

    for film in filme:
        treffer = termine.get(film.tmdb_id)
        tag = treffer[0] if treffer else (film.release_date or "")[:10]
        if not tag or not (von <= tag <= bis):
            continue
        schluessel = f"tmdb:movie:{film.tmdb_id}"
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        eintraege.append(
            _aus_medienobjekt(film, tag, stichtag, schluessel, _art_name(treffer[1]) if treffer else None)
        )

    for serie in serien:
        tag = (serie.release_date or "")[:10]
        if not tag or not (von <= tag <= bis):
            continue
        # Nur bei "grosse Studios": Dort kommt alles ueber Netflix & Co., also
        # auch jede asiatische Eigenproduktion. Bei "bekannte Titel" sortiert
        # schon die Stimmenzahl aus, und "alles" heisst alles.
        if schaerfe == "studios" and not _aus_bekanntem_land(serie):
            continue
        schluessel = f"tmdb:tv:{serie.tmdb_id}"
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        eintraege.append(_aus_medienobjekt(serie, tag, stichtag, schluessel, None))

    return eintraege


def _demo_eintraege(von: str, bis: str, stichtag: str, datumsart: str) -> list[CalendarEntry]:
    """Kalender ohne TMDB-Schluessel.

    Jede andere Seite laesst sich mit Beispieldaten ansehen; ohne das waere der
    Kalender ausgerechnet die eine Seite, die auf einer frischen Installation
    kaputt aussieht.

    Die Demo-Titel tragen Erscheinungsdaten aus den vergangenen Wochen - die
    liegen bei einer Wochenansicht fast nie im Fenster. Deshalb werden sie
    hier gleichmaessig ueber den angefragten Zeitraum verteilt. Erfundene Daten
    fuer erfundene Titel: Das ist der Sinn des Demo-Modus.
    """
    beginn = date.fromisoformat(von)
    spanne = (date.fromisoformat(bis) - beginn).days + 1

    arten = ("movie",) if datumsart == "kino" else ("movie", "tv")
    titel = [(art, item) for art in arten for item in demo_data.demo_items(art)]
    if not titel:
        return []

    eintraege: list[CalendarEntry] = []
    for nummer, (art, item) in enumerate(titel):
        tag = (beginn + timedelta(days=(nummer * 3) % spanne)).isoformat()
        eintraege.append(
            _aus_medienobjekt(item, tag, stichtag, f"demo:{art}:{item.tmdb_id}", None)
        )
    return eintraege


def _aus_bekanntem_land(serie: Any) -> bool:
    """Stammt die Serie aus einem Land, das hier ueberhaupt jemand sucht?

    Kennt TMDB kein Herkunftsland, faellt der Titel durch - lieber einen
    Grenzfall uebersehen als die Rubrik mit Unbekanntem fluten.
    """
    return bool(HERKUNFTSLAENDER.intersection(serie.origin_country or []))


def _art_name(art: int) -> str | None:
    return {3: "kino", 2: "kino", 1: "premiere", 4: "digital", 5: "physisch", 6: "tv"}.get(art)


def _aus_medienobjekt(
    item: Any, tag: str, stichtag: str, schluessel: str, art: str | None
) -> CalendarEntry:
    return CalendarEntry(
        key=schluessel,
        date=tag,
        source="neu",
        origin="tmdb",
        media_type=item.media_type,
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        title=item.title,
        poster_url=item.poster_url,
        overview=item.overview,
        vote_average=item.vote_average,
        vote_count=item.vote_count,
        genres=item.genres,
        runtime_minutes=item.runtime_minutes,
        certification=item.certification,
        status=item.status,
        date_type=art,  # type: ignore[arg-type]
        aired=tag <= stichtag,
    )


# --- Zuordnung TVDB -> TMDB ------------------------------------------------


async def _tvdb_nach_tmdb(
    db: Session, settings: AppSettings, eintraege: list[CalendarEntry]
) -> None:
    """Fehlende TMDB-Kennungen nachtragen - billigste Quelle zuerst.

    Ohne TMDB-Kennung hat eine Kachel keinen Link, keinen Einkaufswagen und
    kein Herz; bei gesetzter Altersgrenze faellt sie sogar ganz weg. Die drei
    ersten Wege kosten nichts, weil die Zuordnung schon in der Datenbank steht.
    """
    offen = {e.tvdb_id for e in eintraege if e.tmdb_id is None and e.tvdb_id}
    if not offen:
        return

    zuordnung: dict[int, int] = {}

    # 1. Frueher gestellte Anfragen kennen beide Kennungen.
    for tvdb_id, tmdb_id in db.execute(
        select(MediaRequest.tvdb_id, MediaRequest.tmdb_id).where(MediaRequest.tvdb_id.in_(offen))
    ):
        if tvdb_id and tmdb_id:
            zuordnung.setdefault(tvdb_id, tmdb_id)

    # 2. Der Bestand des Media-Servers, sofern verknuepft.
    fehlt = offen - zuordnung.keys()
    if fehlt:
        for tvdb_id, tmdb_id in db.execute(
            select(MediaServerLibraryItem.tvdb_id, MediaServerLibraryItem.tmdb_id).where(
                MediaServerLibraryItem.tvdb_id.in_(fehlt)
            )
        ):
            if tvdb_id and tmdb_id:
                zuordnung.setdefault(tvdb_id, tmdb_id)

    # 3. Erst jetzt TMDB fragen - gedeckelt und lange aufgehoben.
    fehlt = sorted(offen - zuordnung.keys())[:MAX_TVDB_ABFRAGEN]
    if fehlt and not settings.use_demo_data and settings.tmdb_configured:
        client = media._client(settings)

        async def hole(tvdb_id: int) -> tuple[int, int | None]:
            async def fetch() -> dict[str, Any]:
                return {"tmdb_id": await client.find_by_tvdb(tvdb_id)}

            try:
                gefunden = await cache.cached(db, f"tvdb:{tvdb_id}", TVDB_TTL, fetch)
            except TmdbError as fehler:
                logger.info("TVDB-Zuordnung fehlgeschlagen: %s", fehler)
                return tvdb_id, None
            return tvdb_id, gefunden.get("tmdb_id")

        for tvdb_id, tmdb_id in await asyncio.gather(*(hole(k) for k in fehlt)):
            if tmdb_id:
                zuordnung[tvdb_id] = tmdb_id

    for eintrag in eintraege:
        if eintrag.tmdb_id is None and eintrag.tvdb_id in zuordnung:
            eintrag.tmdb_id = zuordnung[eintrag.tvdb_id]


# --- Zusammenfuehren -------------------------------------------------------


def _entdoppeln(eintraege: list[CalendarEntry]) -> list[CalendarEntry]:
    """Was schon im eigenen Bestand steht, nicht noch einmal als Neuheit zeigen.

    Radarr und TMDB kennen denselben Film, und beide melden ihn zum selben
    Termin. Ohne diesen Schritt stuende er zweimal untereinander am selben Tag
    - einmal mit Zustand, einmal ohne. Der eigene Eintrag gewinnt: er weiss, ob
    die Datei da ist.
    """
    eigene = {
        (e.media_type, e.tmdb_id) for e in eintraege if e.source == "meine" and e.tmdb_id
    }
    return [
        e
        for e in eintraege
        if e.source == "meine" or (e.media_type, e.tmdb_id) not in eigene
    ]


def _gruppiere(eintraege: list[CalendarEntry]) -> list[CalendarDay]:
    """Nach Tagen buendeln, aufsteigend, ohne leere Tage.

    Innerhalb eines Tages stehen die eigenen Titel vorn - was einen selbst
    betrifft, ist die dringendere Auskunft.
    """
    nach_tag: dict[str, list[CalendarEntry]] = {}
    for eintrag in eintraege:
        nach_tag.setdefault(eintrag.date, []).append(eintrag)

    tage: list[CalendarDay] = []
    for tag in sorted(nach_tag):
        gebuendelt = sorted(
            nach_tag[tag], key=lambda e: (0 if e.source == "meine" else 1, e.title.casefold())
        )
        tage.append(CalendarDay(date=tag, entries=gebuendelt))
    return tage


async def _altersfilter(
    db: Session, settings: AppSettings, eintraege: list[CalendarEntry]
) -> list[CalendarEntry]:
    """Gesperrte Titel entfernen - auch die aus Radarr und Sonarr.

    Titel von TMDB sind schon gefiltert, weil sie durch ``media`` laufen. Die
    aus Radarr und Sonarr umgehen TMDB dagegen vollstaendig und waeren ein
    neues Leck: Ein Kind saehe Titel und Poster einer Serie, die es sonst
    nirgends zu sehen bekaeme.
    """
    if settings.age_limit is None:
        return eintraege

    ergebnis: list[CalendarEntry] = []
    for art in (MediaType.movie, MediaType.tv):
        betroffen = [e for e in eintraege if e.media_type == art and e.origin != "tmdb"]
        if not betroffen:
            continue
        erlaubt = await media.erlaubte_kennungen(
            db, settings, art.value, [e.tmdb_id for e in betroffen if e.tmdb_id]
        )
        # Ohne TMDB-Kennung laesst sich die Freigabe nicht pruefen. Dann faellt
        # der Eintrag weg - im Zweifel verbergen, nicht zeigen.
        ergebnis.extend(e for e in betroffen if e.tmdb_id in erlaubt)

    ergebnis.extend(e for e in eintraege if e.origin == "tmdb")
    return ergebnis


async def kalender(
    db: Session,
    settings: AppSettings,
    *,
    von: str,
    bis: str,
    quellen: str,
    datumsart: str,
    schaerfe: str,
) -> CalendarResult:
    """Die ganze Zeitleiste fuer einen Zeitraum.

    Die Quellen laufen nebeneinander und einzeln abgesichert: Faellt Radarr
    aus, stehen die Neuerscheinungen trotzdem da - mit einem Hinweis statt
    einer Fehlerseite. Das ist im Haus so ueblich.
    """
    stichtag = heute()
    region = settings.default_region
    will_meine = quellen in ("all", "mine")
    will_neu = quellen in ("all", "new")

    async def eigene_serien() -> list[CalendarEntry]:
        # Bei "Kino" bleiben Serien draussen: Sie haben keinen Kinostart, und
        # eine Folge unter dieser Auswahl zu zeigen, hiesse etwas zu behaupten,
        # das es nicht gibt.
        if not will_meine or datumsart == "kino" or not settings.sonarr_configured:
            return []
        return _falte_folgen(await library.series_calendar(settings, von, bis), stichtag)

    async def eigene_filme() -> list[CalendarEntry]:
        if not will_meine or not settings.radarr_configured:
            return []
        rohe = await library.movie_calendar(settings, von, bis)
        return _meine_filme(rohe, datumsart, von, bis, stichtag)

    async def neues() -> list[CalendarEntry]:
        if not will_neu:
            return []
        if settings.use_demo_data:
            return _demo_eintraege(von, bis, stichtag, datumsart)
        return await _neuerscheinungen(
            db,
            settings,
            von=von,
            bis=bis,
            region=region,
            datumsart=datumsart,
            schaerfe=schaerfe,
            stichtag=stichtag,
        )

    serien, filme, tmdb_neu = await asyncio.gather(
        eigene_serien(), eigene_filme(), neues(), return_exceptions=True
    )

    eintraege: list[CalendarEntry] = []
    arr_hinweis: str | None = None
    tmdb_hinweis: str | None = None

    for ergebnis in (serien, filme):
        if isinstance(ergebnis, ArrError):
            arr_hinweis = ergebnis.message
        elif isinstance(ergebnis, BaseException):
            logger.warning("Kalender: eigene Titel nicht abrufbar: %s", ergebnis)
            arr_hinweis = arr_hinweis or "Radarr/Sonarr sind gerade nicht erreichbar."
        else:
            eintraege.extend(ergebnis)

    if isinstance(tmdb_neu, TmdbError):
        tmdb_hinweis = tmdb_neu.message
    elif isinstance(tmdb_neu, BaseException):
        logger.warning("Kalender: Neuerscheinungen nicht abrufbar: %s", tmdb_neu)
        tmdb_hinweis = "Neuerscheinungen sind gerade nicht abrufbar."
    else:
        eintraege.extend(tmdb_neu)

    if not settings.use_demo_data and settings.tmdb_configured:
        try:
            await _tvdb_nach_tmdb(db, settings, eintraege)
        except Exception as fehler:  # noqa: BLE001 - Zuordnung ist Beiwerk
            logger.info("TVDB-Zuordnung uebersprungen: %s", fehler)

    eintraege = await _altersfilter(db, settings, eintraege)
    # Erst nach der Zuordnung der Kennungen - vorher waere der eigene Eintrag
    # noch gar nicht als derselbe Titel zu erkennen.
    eintraege = _entdoppeln(eintraege)

    return CalendarResult(
        date_from=von,
        date_to=bis,
        days=_gruppiere(eintraege),
        arr_warning=arr_hinweis,
        tmdb_warning=tmdb_hinweis,
        demo=settings.use_demo_data,
    )
