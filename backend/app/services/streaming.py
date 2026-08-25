"""Die Streaming-Abos, die jemand selbst hat.

Wozu: Wer einen Titel anfragt, der in seinem eigenen Abo laeuft, soll das
erfahren, bevor er ihn herunterladen laesst. Ein **Hinweis**, keine Sperre -
die Daten sind gut genug fuer einen Satz und nicht gut genug fuer ein Verbot
(siehe ``TRIFFT_NICHT_IMMER`` unten).

Woher die Daten kommen: TMDB reicht sie von JustWatch durch, und Nexview holt
sie ohnehin schon - ``watch/providers`` haengt per ``append_to_response`` an
jeder Detailabfrage. Fuer den Abgleich faellt also **kein** zusaetzlicher
Aufruf an. Die Quellenangabe fuer JustWatch steht auf der Detailseite und in
"Ueber Nexview"; sie ist Bedingung fuer die Nutzung der Daten.

Warum eine handverlesene Liste statt der von TMDB:
    TMDB fuehrt 194 Anbieter fuer Deutschland und 292 fuer die USA. Darin
    stehen Kaufhaeuser (Apple TV Store, Google Play Movies), Nischenkanaele
    (Cultpix, GuideDoc, Sun Nxt) und vor allem Untermieter - "Paramount+
    Amazon Channel", "HBO Max Amazon Channel", ein Dutzend "... Apple TV
    channel". Ein Untermieter ist kein Abo, das man hat: Wer Prime bezahlt,
    hat damit nicht Paramount+ ueber Prime.

    ``display_priority`` waere der naheliegende Filter und taugt nicht: dort
    stehen fuer DE der Apple TV Store und Google Play Movies **vor** Disney+.
    Es sortiert nach Relevanz fuer JustWatch, nicht nach Groesse.

    Also werden die grossen Dienste benannt. Achtzig Logos zum Durchhaken
    waeren ohnehin keine Auswahl, sondern eine Zumutung.

Warum je Marke *mehrere* Kennungen:
    Dieselbe Marke hat bei TMDB je nach Region und Tarif verschiedene
    Kennungen. Amazon Prime Video ist 9 in DE, AT, GB und US - aber 119 in der
    Schweiz. Netflix hat neben 8 noch 175 ("Netflix Kids") und 1796 ("Standard
    with Ads"). Wer "Netflix" anhakt, meint alle drei; ein Titel, der nur im
    Werbe-Tarif liegt, ist fuer ihn trotzdem da.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .settings_service import AppSettings
from . import cache
from .tmdb import TmdbClient, image_url

# Logos in derselben Groesse wie auf der Detailseite - dort stehen dieselben
# Marken, und zwei Groessen desselben Bildes waeren zwei Downloads.
LOGO_SIZE = "w92"


@dataclass(frozen=True)
class Marke:
    """Ein Abo-Dienst, wie ein Mensch ihn nennt.

    ``slug`` ist das, was gespeichert wird - nicht die TMDB-Kennung. Marken
    werden umbenannt und Tarife kommen dazu; wer die Kennung speichert, muss
    bei jeder Aenderung die Datenbank anfassen. Der Name ist absichtlich
    **nicht** uebersetzt: "Netflix" heisst ueberall Netflix.
    """

    slug: str
    name: str
    kennungen: frozenset[int]


def _marke(slug: str, name: str, *kennungen: int) -> Marke:
    return Marke(slug=slug, name=name, kennungen=frozenset(kennungen))


# Die grossen Abo-Dienste im deutschsprachigen Raum, Grossbritannien und den
# USA. Alle Kennungen einmal aus TMDBs Katalog geholt und geprueft, nicht aus
# dem Gedaechtnis geschrieben.
#
# Bewusst **nicht** dabei:
#   * Kaufen und Leihen (Apple TV Store 2, Google Play 3, Rakuten 35, Sky
#     Store 130) - das ist kein Abo, das man hat.
#   * Untermieter ("... Amazon Channel", "... Apple TV channel") - siehe oben.
#   * Kostenlose und werbefinanzierte Sender (ARD, ZDF, Arte 234, Pluto,
#     Joyn 304 im Gratistarif). Sie kosten nichts, also spart der Hinweis
#     niemandem etwas, und sie wuerden die Liste verdoppeln.
MARKEN: tuple[Marke, ...] = (
    _marke("netflix", "Netflix", 8, 175, 1796),
    _marke("amazon-prime-video", "Amazon Prime Video", 9, 119, 2100),
    _marke("disney-plus", "Disney+", 337),
    _marke("apple-tv-plus", "Apple TV+", 350),
    _marke("paramount-plus", "Paramount+", 531, 2303, 2616),
    _marke("hbo-max", "HBO Max", 1899),
    _marke("wow", "WOW", 30),
    _marke("sky", "Sky", 29, 210, 321),
    _marke("magentatv", "MagentaTV", 178, 1856, 2412),
    _marke("rtl-plus", "RTL+", 2750),
    _marke("joyn-plus", "Joyn Plus+", 421),
    _marke("crunchyroll", "Crunchyroll", 283),
    _marke("mubi", "MUBI", 11),
    _marke("discovery-plus", "Discovery+", 524),
    _marke("hulu", "Hulu", 15),
    _marke("peacock", "Peacock", 386, 387),
    _marke("starz", "Starz", 43),
    _marke("britbox", "BritBox", 151),
    _marke("itvx-premium", "ITVX Premium", 2300),
    _marke("now", "NOW", 39, 591),
)

NACH_SLUG: dict[str, Marke] = {marke.slug: marke for marke in MARKEN}

# Von der TMDB-Kennung zur Marke - fuer den Abgleich, der pro Titel laeuft.
NACH_KENNUNG: dict[int, Marke] = {
    kennung: marke for marke in MARKEN for kennung in marke.kennungen
}


def ist_bekannt(slug: str) -> bool:
    return slug in NACH_SLUG


@dataclass(frozen=True)
class Dienst:
    """Ein Eintrag der Auswahlliste: Marke plus Logo aus der Region."""

    slug: str
    name: str
    logo_url: str | None


async def katalog(db: Session, settings: AppSettings, region: str) -> list[Dienst]:
    """Welche der grossen Dienste gibt es in dieser Region? - mit Logo.

    Die Region entscheidet, was ueberhaupt zur Auswahl steht: WOW und RTL+ in
    Deutschland, Hulu und Peacock in den USA, Sky in drei Laendern unter drei
    Kennungen. Damit muss die Markenliste oben kein Land kennen - die
    Schnittmenge mit dem Katalog erledigt das.

    Der Katalog aendert sich praktisch nie und liegt deshalb lange im
    Zwischenspeicher. Gespeichert wird das fertige Ergebnis, nicht TMDBs
    Rohantwort: die ist knapp 200 Eintraege lang, von denen zwanzig
    interessieren.
    """
    region = (region or "").upper()
    if not region:
        return []

    async def beschaffen() -> list[dict[str, Any]]:
        client = TmdbClient(
            api_key=settings.tmdb_api_key,
            language=settings.default_language,
            region=region,
        )
        # Film und Serie zusammen: Manche Marke fuehrt TMDB nur unter einer der
        # beiden Arten, und wer Crunchyroll hat, hat es fuer beides.
        roh: dict[int, dict[str, Any]] = {}
        for media_type in ("movie", "tv"):
            for eintrag in await client.watch_provider_list(media_type, region):
                kennung = eintrag.get("provider_id")
                if isinstance(kennung, int):
                    roh.setdefault(kennung, eintrag)

        gefunden: list[dict[str, Any]] = []
        for marke in MARKEN:
            # Die erste Kennung, die es in dieser Region gibt, liefert das Logo.
            eintrag = next(
                (roh[kennung] for kennung in sorted(marke.kennungen) if kennung in roh),
                None,
            )
            if eintrag is None:
                continue
            gefunden.append(
                {
                    "slug": marke.slug,
                    "name": marke.name,
                    "logo_path": eintrag.get("logo_path"),
                }
            )
        return gefunden

    eintraege = await cache.cached(
        db, f"streaming:katalog:{region}", cache.GENRE_TTL, beschaffen
    )

    return [
        Dienst(
            slug=eintrag["slug"],
            name=eintrag["name"],
            logo_url=image_url(eintrag.get("logo_path"), LOGO_SIZE),
        )
        for eintrag in eintraege
    ]


def treffer(slugs: set[str], kennungen: list[int]) -> list[str]:
    """Welche der eigenen Dienste fuehren diesen Titel? - Namen, keine Kennungen.

    ``kennungen`` sind die Anbieter aus der Abo-Gruppe des Titels
    (``flatrate``), ``slugs`` die angehakten Dienste. Die Reihenfolge folgt
    ``MARKEN``, damit zwei Treffer immer gleich herum stehen - "Netflix und
    Disney+" heute und morgen, nicht mal so und mal so.

    .. _TRIFFT_NICHT_IMMER:

    Zwei Faelle, in denen das Ergebnis nicht stimmt, und beide sind der Grund,
    warum daraus ein Hinweis wird und keine Sperre:

    * **Serien kennen keine Staffeln.** TMDB sagt "laeuft auf Netflix" fuer die
      Serie, nicht fuer die vierte Staffel, die dort fehlt. Genau deshalb
      bekommt der Hinweis bei Serien einen anderen Wortlaut.
    * **Lizenzen wechseln schneller als die Daten.** Ein Titel, der Netflix
      gerade verlassen hat, steht dort noch Tage.
    """
    if not slugs or not kennungen:
        return []

    getroffen = {
        NACH_KENNUNG[kennung].slug
        for kennung in kennungen
        if kennung in NACH_KENNUNG and NACH_KENNUNG[kennung].slug in slugs
    }
    return [marke.name for marke in MARKEN if marke.slug in getroffen]


def eigene_dienste(db: Session, user: Any) -> set[str]:
    """Die angehakten Dienste eines Kontos.

    Hier statt beim Aufrufer, damit die Tabelle nur an einer Stelle abgefragt
    wird - sie wird spaeter auch von der Wunschliste der Eltern und der
    Freigabeliste des Betreibers gebraucht.
    """
    from ..models import StreamingService

    return set(
        db.scalars(
            select(StreamingService.slug).where(StreamingService.user_id == user.id)
        )
    )
