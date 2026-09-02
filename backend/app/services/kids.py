"""Der Katalog, den ein Kinderkonto zu sehen bekommt.

Drei Grenzen wirken zusammen, alle **serverseitig**:

1. **Die Rubriken**, die das Elternteil freigeschaltet hat. Sie entscheiden,
   welche Genres ueberhaupt vorkommen - beim Entdecken *und* bei der Suche.
2. **Die Altersfreigabe** aus Stufe 1, die ohnehin schon in ``media._to_items``
   und ``media.detail`` haengt.
3. **Was es schon gibt, steht getrennt.** Ein Kind soll sich nichts wuenschen
   koennen, das laengst da ist - der Wunsch scheiterte spaeter beim Freigeben,
   und bis dahin haette es gewartet. Verbergen waere aber falsch: Gemessen an
   der Bibliothek dieses Nutzers fielen damit **90 %** der Kinderseite weg, und
   zwar ausgerechnet das, was das Kind sofort schauen koennte. Deshalb zwei
   Bereiche - "Das kannst du schon schauen" und "Wuenschen".

⚠️ **Warum der Freigabefilter direkt an TMDB geht:** In dieser Installation
haben 31 % der Filme und 43 % der Serien gar keine Einstufung; auf der
Entdecken-Seite blieben von 20 geholten Titeln **2** uebrig, nachdem der
Altersfilter gelaufen war. Wer fuer Kinder dieselbe Abfrage nimmt und hinterher
siebt, baut eine leere App. TMDB kann bei **Filmen** selbst filtern
(``certification_country`` + ``certification``), und dann kommen 20 passende
statt 2. Bei Serien bietet TMDB das nicht an - dort tragen die Rubriken allein.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChildWish, MediaType, User, WishState
from ..schemas_media import MediaItem
from ..services.settings_service import AppSettings
from . import age_rating, blocklist, cache, library, media, mediaserver_library, requests_service
from .children import RUBRIKEN, rubriken_von
from .filters import DiscoverFilters

# Wie viele TMDB-Seiten eine Rubrikseite auf einmal holt.
#
# TMDB liefert 20 Eintraege je Seite. Nach der Einordnung bleibt davon oft nur
# ein Bruchteil im Bereich "Wuenschen" uebrig - in einer gut gefuellten
# Mediathek gemessen 2 von 20. Mit einer Seite muesste ein Kind also nach zwei
# Kacheln schon "Mehr anzeigen" druecken. Drei Seiten sind der Ausgleich
# zwischen "genug zu sehen" und "die Seite laedt ewig": Jede TMDB-Seite zieht
# die Detaildaten ihrer 20 Titel hinter sich her.
SEITEN_JE_ABRUF = 3

# Die Freigabe-Tabelle von TMDB aendert sich praktisch nie.
FREIGABEN_TTL = timedelta(days=30)


# Wie viele Bilder eine Kategorie-Kachel zur Auswahl bekommt.
#
# Die Oberflaeche waehlt daraus eines aus. Serverseitig zu wuerfeln waere
# wirkungslos: Die TMDB-Antwort liegt drei Stunden im Zwischenspeicher, es kaeme
# also jedes Mal dasselbe Bild heraus.
BILDER_JE_KATEGORIE = 8


@dataclass(frozen=True)
class Kategorie:
    """Eine Kachel auf der Kinder-Startseite."""

    rubrik: str
    # Szenenbilder aus den Titeln dieser Rubrik. Bewusst die **Breitbilder**
    # (``backdrop``) und nicht die Poster: Die Kachel ist quer, ein Poster
    # muesste dafuer beschnitten werden, und Kinderfilme haben gerade in den
    # Szenenbildern ihre schoensten Motive.
    bilder: list[str]


@dataclass(frozen=True)
class Zweiteilung:
    """Was ein Kind schon schauen kann - und was es sich wuenschen kann."""

    verfuegbar: list[MediaItem]
    wuenschbar: list[MediaItem]

    @property
    def leer(self) -> bool:
        return not self.verfuegbar and not self.wuenschbar


def genres_fuer(kind: User, media_type: str) -> set[int]:
    """Alle TMDB-Genres, die dieses Kind sehen darf.

    Leer waere gefaehrlich - eine leere Menge liest sich in einem Filter wie
    "keine Einschraenkung". Kann hier aber nicht vorkommen: ``rubriken_von``
    liefert nie eine leere Liste, und jede Rubrik hat auf mindestens einer der
    beiden Seiten Genres.
    """
    spalte = 0 if media_type == "movie" else 1
    erlaubt: set[int] = set()
    for name in rubriken_von(kind):
        erlaubt.update(RUBRIKEN[name][spalte])
    return erlaubt


def rubriken_mit_inhalt(kind: User, media_type: str) -> list[str]:
    """Die Rubriken dieses Kindes, die es bei dieser Medienart ueberhaupt gibt.

    "Kinderserien" kommt bei Filmen nicht vor, "Musik" nicht bei Serien - eine
    Kachel, hinter der nichts sein kann, waere ein Fehlklick.
    """
    spalte = 0 if media_type == "movie" else 1
    return [name for name in rubriken_von(kind) if RUBRIKEN[name][spalte]]


def darf_rubrik(kind: User, rubrik: str, media_type: str) -> bool:
    return rubrik in rubriken_mit_inhalt(kind, media_type)


async def _freigaben(db: Session, settings: AppSettings, media_type: str) -> dict[str, Any]:
    async def hole() -> dict[str, Any]:
        return await media._client(settings).certification_list(media_type)

    return await cache.cached(db, f"certifications:{media_type}", FREIGABEN_TTL, hole)


async def erlaubte_freigaben(
    db: Session, settings: AppSettings, kind: User
) -> tuple[str | None, str]:
    """Land und Bezeichnungen fuer den TMDB-Filter, z. B. ("DE", "0|6|12").

    Die Uebersetzung kommt aus der vorhandenen Tabelle in ``age_rating``: TMDB
    nennt je Land seine Bezeichnungen, ``stufe()`` macht daraus ein
    Mindestalter, und behalten wird, was das Kind sehen darf. Was ``stufe()``
    nicht kennt, faellt weg - eine geratene Einstufung waere in einer
    Alterssperre schlimmer als eine fehlende.

    Ohne Altersangabe oder ohne brauchbare Tabelle kommt (None, "") zurueck;
    dann filtert allein die Pruefung in ``media._to_items``.
    """
    return await freigaben_bis_alter(
        db, settings, (kind.rating_region or settings.rating_region or ""), kind.age
    )


async def freigaben_bis_alter(
    db: Session, settings: AppSettings, land: str, hoechstalter: int | None
) -> tuple[str | None, str]:
    """Dasselbe fuer ein beliebiges Hoechstalter, ohne Kinderkonto.

    Herausgeloest, damit der Filmabend-Assistent bei "mit Kindern" dieselbe
    Uebersetzung benutzt, statt kindgerechte Genres zu **raten**. Eine
    Altersfreigabe ist die ehrliche Auskunft; ein Genre ist es nicht.
    """
    land = (land or "").upper()
    if not land or hoechstalter is None:
        return None, ""

    try:
        tabelle = await _freigaben(db, settings, "movie")
    except Exception:  # noqa: BLE001 - jeder Ausfall fuehrt hier zu demselben Ruecktritt
        # Faellt die Abfrage aus, bleibt der Nachfilter - lieber eine duenne
        # Seite als gar keine.
        return None, ""

    eintraege = (tabelle.get("certifications") or {}).get(land) or []
    erlaubt = []
    for eintrag in eintraege:
        bezeichnung = str(eintrag.get("certification") or "").strip()
        if not bezeichnung:
            continue
        stufe = age_rating.stufe(land, bezeichnung)
        if stufe is not None and stufe <= hoechstalter:
            erlaubt.append(bezeichnung)

    return (land, "|".join(erlaubt)) if erlaubt else (None, "")


async def einordnen(
    db: Session, settings: AppSettings, media_type: str, items: list[MediaItem]
) -> Zweiteilung:
    """Titel in "schon da" und "kann man sich wuenschen" trennen.

    Vier Quellen, dieselben wie bei den Abzeichen der Erwachsenen-Ansicht:
    Radarr/Sonarr, der Media-Server, die Anfragetabelle und die Sperrliste.

    Drei Faelle, und der dritte ist der Grund fuer diese Funktion:

    * **verfuegbar** - die Datei liegt da (Radarr/Sonarr oder Media-Server).
      Das Kind kann sie sofort schauen; ein Wunsch waere sinnlos.
    * **wuenschbar** - nichts davon trifft zu.
    * **weder noch** - schon angefragt, aber noch nicht geladen, oder auf der
      Sperrliste. Beides faellt ganz weg: Das Kind kann damit nichts anfangen,
      und ein Wunsch scheiterte spaeter beim Freigeben.
    """
    if not items:
        return Zweiteilung(verfuegbar=[], wuenschbar=[])

    art = MediaType(media_type)
    stand = await library.apply_status(settings, media_type, items)
    kennungen = [item.tmdb_id for item in stand.items]

    angefragt = requests_service.badges_for(db, art, kennungen)
    gesperrt = blocklist.gesperrte_kennungen(db, art, kennungen)
    im_server = mediaserver_library.vorhandene_kennungen(
        db,
        art,
        [i for i in stand.items if i.status == "not_requested"],
        "standard" if settings.arr_configured(media_type, "uhd") else None,
    )

    verfuegbar: list[MediaItem] = []
    wuenschbar: list[MediaItem] = []
    for item in stand.items:
        if item.tmdb_id in gesperrt:
            continue
        # "downloaded" aus der Bibliothek oder ein Treffer im Media-Server:
        # beides heisst, die Datei ist wirklich da. "searching" heisst das
        # ausdruecklich **nicht** - da laeuft sie noch.
        if item.status == "downloaded" or item.tmdb_id in im_server:
            verfuegbar.append(item)
        elif item.status == "not_requested" and item.tmdb_id not in angefragt:
            wuenschbar.append(item)
    return Zweiteilung(verfuegbar=verfuegbar, wuenschbar=wuenschbar)


async def ist_verfuegbar(
    db: Session, settings: AppSettings, media_type: str, item: MediaItem
) -> bool:
    """Kann das Kind diesen einen Titel schon schauen?

    Gebraucht auf der Titelseite: Dort muss statt des Wunsch-Knopfes "Das
    kannst du schon schauen" stehen - sonst waere die Seite die einzige
    Stelle, an der die Trennung nicht gilt.
    """
    stand = await einordnen(db, settings, media_type, [item])
    return bool(stand.verfuegbar)


def gewuenschte_kennungen(db: Session, kind: User, media_type: str) -> set[int]:
    """Was dieses Kind sich schon gewuenscht hat - fuer das Abzeichen.

    Anders als vorhandene Titel bleiben eigene Wuensche **sichtbar**: Sie
    verschwinden zu lassen sieht aus, als waere der Klick verlorengegangen.
    """
    art = MediaType(media_type)
    return set(
        db.scalars(
            select(ChildWish.tmdb_id).where(
                ChildWish.child_id == kind.id,
                ChildWish.media_type == art,
                ChildWish.state == WishState.open,
            )
        ).all()
    )


async def rubrik_seite(
    db: Session,
    settings: AppSettings,
    kind: User,
    media_type: str,
    rubrik: str,
    page: int = 1,
) -> Zweiteilung:
    """Alle Titel einer Rubrik - eine TMDB-Seite, schon eingeordnet."""
    spalte = 0 if media_type == "movie" else 1
    land, freigaben = await erlaubte_freigaben(db, settings, kind)

    roh: list[MediaItem] = []
    for versatz in range(SEITEN_JE_ABRUF):
        filters = DiscoverFilters(
            # Beliebtes zuerst: Kinder suchen nicht nach Neuerscheinungen, sie
            # wollen das kennen, worueber alle reden.
            sort="popular",
            page=(page - 1) * SEITEN_JE_ABRUF + versatz + 1,
            genres_or="|".join(str(kennung) for kennung in RUBRIKEN[rubrik][spalte]),
            certification_country=land,
            certifications=freigaben,
            # Kurzfilme raus, wie ueberall sonst auch.
            min_runtime=40 if media_type == "movie" else None,
        )
        teil = await media.discover(db, settings, media_type, filters)
        if not teil.items:
            # Hinter einer leeren Seite kommt nichts mehr.
            break
        roh.extend(teil.items)

    return await einordnen(db, settings, media_type, roh)


async def _erste_seite(
    db: Session, settings: AppSettings, kind: User, media_type: str, rubrik: str
) -> Zweiteilung:
    """Nur die erste TMDB-Seite - fuer das Titelbild einer Kategorie.

    Die Startseite braucht je Rubrik ein Bild und die Auskunft "hier ist
    ueberhaupt etwas". Dafuer drei Seiten je Rubrik zu holen, hiesse beim
    Aufschlagen der Kinderansicht zwei Dutzend TMDB-Abrufe.
    """
    spalte = 0 if media_type == "movie" else 1
    land, freigaben = await erlaubte_freigaben(db, settings, kind)
    filters = DiscoverFilters(
        sort="popular",
        page=1,
        genres_or="|".join(str(kennung) for kennung in RUBRIKEN[rubrik][spalte]),
        certification_country=land,
        certifications=freigaben,
        min_runtime=40 if media_type == "movie" else None,
    )
    seite = await media.discover(db, settings, media_type, filters)
    return await einordnen(db, settings, media_type, seite.items)


async def kategorien(
    db: Session, settings: AppSettings, kind: User, media_type: str
) -> list[Kategorie]:
    """Die Startseite: eine Kachel je freigeschalteter Rubrik.

    Das Titelbild ist der erste Titel der Rubrik - eine Kachel mit nur einem
    Wort darauf sagt einem Sechsjaehrigen nichts.

    Nacheinander statt gleichzeitig: Jede Rubrik zieht die Detaildaten ihrer
    Titel hinter sich her, und parallel liefen dieselben Detailabfragen doppelt,
    solange keine davon fertig zwischengespeichert ist.
    """
    ergebnis: list[Kategorie] = []
    for rubrik in rubriken_mit_inhalt(kind, media_type):
        stand = await _erste_seite(db, settings, kind, media_type, rubrik)
        # Eine leere Rubrik gar nicht erst anbieten - dahinter waere nichts.
        if stand.leer:
            continue
        # Vorhandenes zuerst: Das kennt das Kind womoeglich schon wieder.
        # Als Rueckfall das Poster - manche Titel haben kein Breitbild.
        bilder = [
            item.backdrop_url or item.poster_url
            for item in (stand.verfuegbar + stand.wuenschbar)
            if item.backdrop_url or item.poster_url
        ]
        ergebnis.append(Kategorie(rubrik=rubrik, bilder=bilder[:BILDER_JE_KATEGORIE]))
    return ergebnis


async def hintergrundbilder(
    db: Session, settings: AppSettings, kind: User
) -> list[str]:
    """Bilder fuer den Seitengrund der Kinderansicht.

    Ein einfarbiger Verlauf sieht leer aus; ein paar weich gezeichnete Motive
    aus den eigenen Rubriken machen daraus einen Raum, der zu diesem Kind
    gehoert. Bewusst quer durch **beide** Medienarten und ueber alle Rubriken:
    Der Grund soll nicht mitwandern, wenn das Kind zwischen Filmen und Serien
    umschaltet.

    Teuer ist das nicht - die Rubrik-Abfragen liegen ohnehin schon im
    Zwischenspeicher, sobald die Startseite einmal geladen war.
    """
    gesammelt: list[str] = []
    for art in ("movie", "tv"):
        for rubrik in rubriken_mit_inhalt(kind, art):
            stand = await _erste_seite(db, settings, kind, art, rubrik)
            for item in stand.verfuegbar + stand.wuenschbar:
                bild = item.backdrop_url
                if bild and bild not in gesammelt:
                    gesammelt.append(bild)
            if len(gesammelt) >= 12:
                return gesammelt[:12]
    return gesammelt[:12]


async def suche(
    db: Session, settings: AppSettings, kind: User, media_type: str, query: str, page: int = 1
) -> Zweiteilung:
    """Suche - **nur innerhalb der freigeschalteten Rubriken**.

    So vom Nutzer entschieden. Die Einschraenkung sitzt hier und nicht in der
    Oberflaeche: Ohne sie fuende ein Kind ueber die Suche jeden Titel, den
    seine Altersgrenze durchlaesst, auch aus Rubriken, die das Elternteil
    ausdruecklich nicht freigeschaltet hat.
    """
    seite = await media.search(
        db, settings, media_type, query, page, nur_genres=genres_fuer(kind, media_type)
    )
    return await einordnen(db, settings, media_type, seite.items)


def passt_in_rubrik(kind: User, item: MediaItem, genre_namen: dict[int, str]) -> bool:
    """Gehoert dieser Titel in eine Rubrik dieses Kindes?

    Gebraucht auf der Titelseite: Ueber Adresszeile oder einen alten Link
    koennte sonst ein Titel aufgerufen werden, den die Rubriken aussperren.
    Verglichen wird ueber die **Namen**, weil ``MediaItem.genres`` Namen
    fuehrt - die Nummern stecken nur in den Rohdaten.
    """
    erlaubt = {
        genre_namen[kennung]
        for kennung in genres_fuer(kind, item.media_type.value)
        if kennung in genre_namen
    }
    return bool(erlaubt & set(item.genres))
