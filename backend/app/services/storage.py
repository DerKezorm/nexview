"""Wer belegt wie viel Platz - Erfassung und Kontostand.

Stufe 1 von dreien: **nur messen und anzeigen.** Hier wird nichts begrenzt,
nichts blockiert und nichts geloescht. Das kommt erst in Stufe 2 und 3.

Die Rechnung ist eine bewusste Fiktion: Speicherplatz wird nicht *pro Person*
verbraucht, sondern gemeinsam belegt. Damit sie ehrlich bleibt, gilt eine
klare Definition - ein Konto deckt, was jemand ins Haus geholt hat **und noch
beansprucht**. Alles andere ist Hausbestand und zaehlt bei niemandem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    MediaRequest,
    MediaServerLibraryItem,
    MediaType,
    QualityTier,
    RequestStatus,
    StorageEntry,
    Role,
    StorageState,
    User,
    utcnow,
)
from .arr import ArrError
from .radarr import LibraryEntry as MovieEntry
from .settings_service import AppSettings
from .sonarr import LibraryEntry as SeriesEntry
from . import library

logger = logging.getLogger("nexview.storage")

# Welche Anfragen einen Posten ueberhaupt zugerechnet bekommen koennen.
# Bewusst dieselbe Auswahl wie beim Stueck-Kontingent minus der Zustaende, in
# denen noch gar keine Datei existieren kann.
ZURECHENBAR = (
    RequestStatus.approved,
    RequestStatus.searching,
    RequestStatus.downloaded,
)


def schluessel(
    media_type: MediaType | str,
    tier: QualityTier | str,
    *,
    tmdb_id: int | None = None,
    tvdb_id: int | None = None,
    season: int | None = None,
) -> str | None:
    """Die eindeutige Kennung eines Postens.

    Filme ueber die TMDB-Nummer (die kennt Radarr), Serien ueber die
    TVDB-Nummer (die kennt Sonarr). Nicht mischen: Sonst entstuende derselbe
    Titel zweimal, je nachdem welche Quelle ihn zuerst gemeldet hat.

    Gibt ``None`` zurueck, wenn die noetige Nummer fehlt - dann laesst sich der
    Posten nicht verlaesslich fuehren und wird uebersprungen.
    """
    art = media_type.value if isinstance(media_type, MediaType) else str(media_type)
    stufe = tier.value if isinstance(tier, QualityTier) else str(tier)
    if art == MediaType.movie.value:
        return f"movie:{stufe}:tmdb:{tmdb_id}" if tmdb_id else None
    if not tvdb_id:
        return None
    return f"tv:{stufe}:tvdb:{tvdb_id}:s{season if season is not None else 0}"


@dataclass(frozen=True)
class Kontostand:
    """Was ein Nutzer belegt."""

    used_bytes: int
    items: int
    # Wieviel davon abgegeben ist und auf eine Entscheidung wartet. Zaehlt
    # weiter mit - sonst waere Abgeben ein Freifahrtschein.
    pending_bytes: int = 0


@dataclass(frozen=True)
class Posten:
    """Eine Zeile fuer die Anzeige."""

    id: int
    media_type: str
    tier: str
    tmdb_id: int | None
    tvdb_id: int | None
    season: int | None
    title: str
    size_bytes: int
    state: str
    measured_at: datetime
    path: str = ""


def kontostand(db: Session, user_id: int) -> Kontostand:
    """Belegter Platz eines Nutzers.

    Hausbestand zaehlt ausdruecklich **nicht** mit - er gehoert niemandem.
    """
    zeilen = db.execute(
        select(
            func.coalesce(func.sum(StorageEntry.size_bytes), 0),
            func.count(StorageEntry.id),
            StorageEntry.state,
        )
        .where(
            StorageEntry.user_id == user_id,
            StorageEntry.state.in_((StorageState.owned, StorageState.pending)),
        )
        .group_by(StorageEntry.state)
    ).all()

    gesamt = sum(int(zeile[0]) for zeile in zeilen)
    anzahl = sum(int(zeile[1]) for zeile in zeilen)
    wartend = sum(
        int(zeile[0]) for zeile in zeilen if zeile[2] == StorageState.pending
    )
    return Kontostand(used_bytes=gesamt, items=anzahl, pending_bytes=wartend)


GB = 1024**3


@dataclass(frozen=True)
class Grenze:
    """Was fuer einen Nutzer gilt - und wo er gerade steht."""

    used_bytes: int
    # None heisst unbegrenzt. Sonst der Wert in Bytes.
    limit_bytes: int | None

    @property
    def unlimited(self) -> bool:
        return self.limit_bytes is None

    @property
    def remaining_bytes(self) -> int | None:
        """Wieviel noch frei ist. Negativ, wenn ueberzogen."""
        return None if self.limit_bytes is None else self.limit_bytes - self.used_bytes

    @property
    def exhausted(self) -> bool:
        """Ist das Konto **schon** ueberzogen?

        Bewusst ``>=`` und nicht "wuerde die naechste Anfrage sprengen": Die
        Groesse steht beim Anfragen noch gar nicht fest, und eine Schaetzung
        ist keine Grundlage fuer eine Ablehnung. Wer noch Luft hat, darf
        anfragen - auch wenn es danach ins Minus geht. Erst die **naechste**
        Anfrage ist gesperrt.
        """
        return self.limit_bytes is not None and self.used_bytes >= self.limit_bytes


def grenze_in_bytes(user: "User", settings: AppSettings) -> int | None:
    """Welche Grenze fuer diesen Nutzer gilt - in Bytes, ``None`` = unbegrenzt.

    Drei Stufen, in dieser Reihenfolge:

    1. **Administratoren sind immer unbegrenzt.** Genau wie beim
       Stueck-Kontingent (``quota._limit_for``). Zwei verschiedene Regeln fuer
       dieselbe Frage waeren eine Falle.
    2. Traegt das Konto etwas Eigenes, gilt das. Die **0** heisst dort
       ausdruecklich "unbegrenzt".
    3. Sonst die Vorgabe des Hauses - und ist auch die leer, ist niemand
       begrenzt.
    """
    if user.role == Role.admin:
        return None
    eigen = user.storage_limit_gb
    if eigen is not None:
        return None if eigen <= 0 else eigen * GB
    vorgabe = settings.storage_default_limit_gb
    return vorgabe * GB if vorgabe and vorgabe > 0 else None


def stand_fuer(db: Session, user: "User", settings: AppSettings) -> Grenze:
    """Belegung und Grenze eines Nutzers in einem Stueck."""
    return Grenze(
        used_bytes=kontostand(db, user.id).used_bytes,
        limit_bytes=grenze_in_bytes(user, settings),
    )


# Wieviele Zeilen eine Seite fasst. Zwei Groessen, weil es zwei Orte gibt: Der
# Hausbestand hat eine ganze Seite fuer sich, die Aufschlüsselung je Konto sitzt
# eingeklappt in einer Karte neben anderen - dort sind zwanzig Zeilen eine Wand.
#
# ⚠️ Die Zahl reist in der Antwort mit (``per_page``). Die Oberflaeche darf sie
# **nicht** spiegeln: Eine zweite Konstante dort geht beim naechsten Aendern
# auseinander, und dann stimmt die Seitenzahl nicht mehr.
JE_SEITE = 20
JE_SEITE_KOMPAKT = 10


def _seite(
    db: Session, bedingungen: list, *, suche: str, seite: int, je_seite: int
) -> tuple[list[Posten], int]:
    """Eine Seite Posten - das Groesste zuerst, dazu die Gesamtzahl.

    Gemeinsamer Kern von Hausbestand und einzelnem Konto. Es ist beide Male
    dieselbe Frage - "wo steckt der Platz" - und sie darf nicht zwei
    verschieden sortierte oder verschieden durchsuchbare Antworten bekommen.

    Gesucht wird in Titel **und** Pfad: Der Ordner sagt oft mehr ueber die
    Einsortierung als der Name.
    """
    if suche.strip():
        muster = f"%{suche.strip()}%"
        bedingungen = [
            *bedingungen,
            StorageEntry.title.ilike(muster) | StorageEntry.path.ilike(muster),
        ]

    gesamt = db.scalar(select(func.count(StorageEntry.id)).where(*bedingungen)) or 0
    zeilen = db.scalars(
        select(StorageEntry)
        .where(*bedingungen)
        .order_by(StorageEntry.size_bytes.desc())
        .offset(max(0, seite - 1) * je_seite)
        .limit(je_seite)
    ).all()
    return [_als_posten(zeile) for zeile in zeilen], int(gesamt)


def posten_fuer(
    db: Session, user_id: int, *, suche: str = "", seite: int = 1, je_seite: int = 20
) -> tuple[list[Posten], int]:
    """Alles, was ein Nutzer belegt - das Groesste zuerst, seitenweise.

    Die Reihenfolge ist der eigentliche Zweck der Liste: Wer Platz schaffen
    soll, muss zuerst sehen, wo der Platz steckt.

    Geblaettert wie der Hausbestand, und aus demselben Grund: Wer zweihundert
    Titel hat, findet einen bestimmten sonst nicht wieder.
    """
    return _seite(
        db,
        [
            StorageEntry.user_id == user_id,
            StorageEntry.state.in_((StorageState.owned, StorageState.pending)),
        ],
        suche=suche,
        seite=seite,
        je_seite=je_seite,
    )


def posten_im_haus(
    db: Session, *, suche: str = "", seite: int = 1, je_seite: int = 20
) -> tuple[list[Posten], int]:
    """Was das Haus haelt - das Groesste zuerst, seitenweise.

    Fuer den Administrator die eigentliche Auskunft: Sein persoenliches Konto
    steht per Definition auf null, weil alles, was er holt, dem Haus gehoert.
    Ihm "du belegst nichts" zu zeigen waere richtig und trotzdem wertlos.

    **Geblaettert und durchsuchbar statt gedeckelt.** In einer gewachsenen
    Bibliothek sind das Tausende Zeilen; ein Auszug der groessten beantwortet
    "was frisst den Platz", aber nicht "wo steckt eigentlich dieser eine
    Titel". Gesucht wird in Titel **und** Pfad - der Ordner sagt oft mehr
    ueber die Einsortierung als der Name.

    Gibt die Zeilen und die Gesamtzahl zurueck; ohne sie liesse sich nicht
    sagen, wie viele Seiten es gibt.
    """
    return _seite(
        db,
        [StorageEntry.state == StorageState.house],
        suche=suche,
        seite=seite,
        je_seite=je_seite,
    )


def hausbestand(db: Session) -> Kontostand:
    """Was dem Haus gehoert - also niemandem persoenlich."""
    summe, anzahl = db.execute(
        select(
            func.coalesce(func.sum(StorageEntry.size_bytes), 0),
            func.count(StorageEntry.id),
        ).where(StorageEntry.state == StorageState.house)
    ).one()
    return Kontostand(used_bytes=int(summe), items=int(anzahl))


def _einhaengepunkt(ordner: str, punkte: list[dict]) -> dict | None:
    """Auf welchem Datentraeger liegt dieser Ordner?

    Der **laengste** passende Einhaengepunkt gewinnt: Ein Container meldet oft
    ``/``, ``/config`` und ``/data`` nebeneinander, und alle drei "passen" zu
    ``/data/Movies``. Nur der laengste beschreibt wirklich die Platte, auf der
    der Ordner liegt.
    """
    passend = [
        punkt
        for punkt in punkte
        if (pfad := str(punkt.get("path") or "").rstrip("/"))
        and (ordner == pfad or ordner.startswith(pfad + "/"))
    ]
    if not passend:
        return None
    return max(passend, key=lambda punkt: len(str(punkt.get("path") or "")))


async def freier_platz(settings: AppSettings) -> tuple[int, int]:
    """Wieviel auf den Zielordnern noch frei ist - und auf wievielen Traegern.

    Gibt ``(bytes, anzahl_traeger)`` zurueck.

    **Der Kern des Problems ist, dieselbe Platte nicht zweimal zu zaehlen.**
    Filme und Serien liegen fast immer auf demselben Traeger, oft dazu noch
    die 4K-Instanzen. Vier Ordner, ein Datentraeger - vier Mal 83 TB zu
    addieren waere grober Unsinn.

    ⚠️ **Nicht ueber den freien Platz unterscheiden.** Genau das stand hier
    vorher, mit der Begruendung, zwei verschiedene Traeger haetten "praktisch
    nie" denselben Wert auf das Byte genau. Die Begruendung stimmt - nur zieht
    sie in die falsche Richtung: Auch **derselbe** Traeger meldet nicht immer
    denselben Wert. Radarr und Sonarr lesen ihn zu verschiedenen Zeitpunkten,
    und sobald irgendetwas geschrieben wird, weichen sie um ein paar Bytes ab.
    Gemeldet wurde: "167,37 TB frei auf 2 Datentraegern" bei genau einer
    Platte mit 83,69 TB.

    Unterschieden wird deshalb ueber die **Gesamtgroesse** des Traegers. Die
    aendert sich nicht, waehrend geschrieben wird, und ist damit eine stabile
    Kennung. Zwei verschiedene Platten *exakt* gleicher Groesse gaebe es
    theoretisch - dann wird zu wenig gemeldet statt zu viel, und das ist der
    Fehler, den man haben will: Er verspricht nie mehr Platz, als da ist.

    Die Gesamtgroesse kommt aus ``/diskspace``; ``/rootfolder`` kennt sie
    nicht. Liefert eine Instanz sie nicht, faellt ihr Ordner still weg - lieber
    eine Zahl weniger als eine erfundene.

    **Es gibt hier bewusst keine Gesamtkapazitaet nach aussen.** Zwar steht sie
    jetzt zur Verfuegung (125,67 TB in der Messinstallation), aber "belegt +
    frei" waere trotzdem irrefuehrend: Auf demselben Traeger liegen Backups,
    Fotos, das Betriebssystem. Der Unterschied zwischen Hausbestand und
    Gesamtgroesse ist eben **nicht** der Platz, der fuer Medien frei ist.
    """
    # Kennung des Traegers -> freier Platz. Bei mehreren Meldungen zaehlt die
    # kleinste: Sie ist die juengste Nachricht ueber eine Platte, auf die
    # gerade geschrieben wird, und untertreibt im Zweifel.
    traeger: dict[int, int] = {}

    for art in ("movie", "tv"):
        for stufe in ("standard", "uhd"):
            if not settings.arr_configured(art, stufe):
                continue
            try:
                daten = await library.options(settings, art, stufe)
                punkte = await library.datentraeger(settings, art, stufe)
            except ArrError:
                continue

            for ordner in daten.get("root_folders") or []:
                pfad = str(ordner.get("path") or "").rstrip("/")
                if not pfad:
                    continue
                punkt = _einhaengepunkt(pfad, punkte)
                gesamt = (punkt or {}).get("total_space")
                frei = (punkt or {}).get("free_space")
                if not isinstance(gesamt, int) or not isinstance(frei, int) or frei <= 0:
                    continue
                traeger[gesamt] = min(traeger.get(gesamt, frei), frei)

    return sum(traeger.values()), len(traeger)


def verteilung(db: Session) -> list[tuple[int | None, Kontostand]]:
    """Wer belegt wieviel - fuer die Admin-Uebersicht, das Groesste zuerst.

    ``None`` als Nutzer steht fuer den Hausbestand.

    **Jedes aktive Konto steht in der Liste, auch mit null Bytes.** Wer nur
    die Belegten zeigt, laesst den Betrachter raetseln, warum jemand fehlt -
    "hat nichts" und "wird nicht erfasst" saehen gleich aus.
    """
    gemessen = {
        zeile[0]: Kontostand(used_bytes=int(zeile[1]), items=int(zeile[2]))
        for zeile in db.execute(
            select(
                StorageEntry.user_id,
                func.coalesce(func.sum(StorageEntry.size_bytes), 0),
                func.count(StorageEntry.id),
            ).group_by(StorageEntry.user_id)
        ).all()
    }

    ergebnis: list[tuple[int | None, Kontostand]] = []
    for user_id in db.scalars(select(User.id).where(User.is_active.is_(True))).all():
        ergebnis.append((user_id, gemessen.pop(user_id, Kontostand(0, 0))))

    # Was uebrig bleibt: der Hausbestand (None) und Posten geloeschter Konten,
    # deren Nutzer-Id per ON DELETE SET NULL ohnehin schon auf None steht.
    for user_id, stand in gemessen.items():
        ergebnis.append((user_id, stand))

    ergebnis.sort(key=lambda paar: paar[1].used_bytes, reverse=True)
    return ergebnis


def _als_posten(zeile: StorageEntry) -> Posten:
    return Posten(
        id=zeile.id,
        media_type=zeile.media_type.value,
        tier=zeile.tier.value,
        tmdb_id=zeile.tmdb_id,
        tvdb_id=zeile.tvdb_id,
        season=zeile.season,
        title=zeile.title,
        size_bytes=zeile.size_bytes,
        state=zeile.state.value,
        measured_at=zeile.measured_at,
        path=zeile.path or "",
    )


# ---------------------------------------------------------------- Abgleich


@dataclass
class _Gemessen:
    """Ein Posten, wie ihn die Dienste gerade melden."""

    key: str
    media_type: MediaType
    tier: QualityTier
    tmdb_id: int | None
    tvdb_id: int | None
    season: int | None
    title: str
    size_bytes: int
    path: str = ""


@dataclass(frozen=True)
class Ergebnis:
    """Was ein Abgleich bewirkt hat - fuer Protokoll und Anzeige."""

    neu: int = 0
    aktualisiert: int = 0
    entfernt: int = 0
    gewachsen: int = 0
    erster_lauf: bool = False


async def abgleichen(db: Session, settings: AppSettings) -> Ergebnis:
    """Belegung neu erfassen.

    **Erst holen, dann schreiben.** SQLite laesst genau einen Schreiber zu;
    bliebe eine Schreib-Transaktion offen, waehrend auf Radarr oder Sonarr
    gewartet wird, wartet jede Anfrage eines Nutzers mit. Deshalb laufen alle
    Abfragen zuerst und die Datenbank wird danach in einem kurzen Zug
    angefasst - dasselbe Vorgehen wie im Status-Abgleich.
    """
    gemessen = await _erfassen(db, settings)
    return _schreiben(db, gemessen)


async def _erfassen(db: Session, settings: AppSettings) -> dict[str, _Gemessen]:
    """Alle Groessen einsammeln - reine Leserei, kein Schreiben."""
    gemessen: dict[str, _Gemessen] = {}

    for stufe in (QualityTier.standard, QualityTier.uhd):
        if settings.arr_configured("movie", stufe.value):
            try:
                for tmdb_id, eintrag in (
                    await library.movie_library(settings, stufe.value)
                ).items():
                    _film_aufnehmen(gemessen, stufe, tmdb_id, eintrag)
            except ArrError as fehler:
                logger.warning(
                    "Radarr (%s) nicht erreichbar, Groessen bleiben wie sie waren: %s",
                    stufe.value,
                    fehler.message,
                )

        if settings.arr_configured("tv", stufe.value):
            try:
                nach_tvdb, _ = await library.series_library(settings, stufe.value)
                for tvdb_id, eintrag in nach_tvdb.items():
                    _serie_aufnehmen(gemessen, stufe, tvdb_id, eintrag)
            except ArrError as fehler:
                logger.warning(
                    "Sonarr (%s) nicht erreichbar, Groessen bleiben wie sie waren: %s",
                    stufe.value,
                    fehler.message,
                )

    _aus_media_server(db, gemessen)
    return gemessen


def _film_aufnehmen(
    ziel: dict[str, _Gemessen],
    stufe: QualityTier,
    tmdb_id: int,
    eintrag: MovieEntry,
) -> None:
    if eintrag.size_bytes <= 0:
        return
    kennung = schluessel(MediaType.movie, stufe, tmdb_id=tmdb_id)
    if kennung is None:
        return
    ziel[kennung] = _Gemessen(
        key=kennung,
        media_type=MediaType.movie,
        tier=stufe,
        tmdb_id=tmdb_id,
        tvdb_id=None,
        season=None,
        title=eintrag.title,
        size_bytes=eintrag.size_bytes,
        path=eintrag.path,
    )


def _serie_aufnehmen(
    ziel: dict[str, _Gemessen],
    stufe: QualityTier,
    tvdb_id: int,
    eintrag: SeriesEntry,
) -> None:
    """Eine Zeile **je Staffel** - nie je Folge.

    Die Staffel ist die feinste Koernung, die Sonarr ohne zusaetzliche Abfrage
    hergibt. Feiner zu rechnen haette eine Abfrage je Serie gekostet, und
    "Staffel 3 belegt 40 GB" ist ohnehin die Aussage, mit der jemand etwas
    anfangen kann.
    """
    for staffel, bytes_ in eintrag.seasons.items():
        if bytes_ <= 0:
            continue
        kennung = schluessel(MediaType.tv, stufe, tvdb_id=tvdb_id, season=staffel)
        if kennung is None:
            continue
        ziel[kennung] = _Gemessen(
            key=kennung,
            media_type=MediaType.tv,
            tier=stufe,
            tmdb_id=None,
            tvdb_id=tvdb_id,
            season=staffel,
            title=eintrag.title,
            size_bytes=bytes_,
            # Der Ordner der **Serie**, kein Dateiname: Eine Staffel ist keine
            # Datei, sondern zwanzig. Echte Dateinamen braeuchten eine Abfrage
            # je Serie - und die Aussage "wo liegt das" beantwortet der Ordner.
            path=eintrag.path,
        )


def _aus_media_server(db: Session, ziel: dict[str, _Gemessen]) -> None:
    """Filme, die nur noch im Media-Server liegen.

    Der Fall, um den es geht: laden, bis die Qualitaet stimmt, dann den
    Eintrag aus Radarr werfen und die Datei behalten. Danach ist der
    Media-Server die einzige Stelle, die die Groesse ueberhaupt noch kennt.

    Was Radarr bereits gemeldet hat, wird **nicht** ueberschrieben - dessen
    Zahl ist die genauere. Serien bleiben aussen vor: Dort haengen die Dateien
    an den Folgen, der Serien-Eintrag traegt keine Groesse.
    """
    zeilen = db.scalars(
        select(MediaServerLibraryItem).where(
            MediaServerLibraryItem.media_type == MediaType.movie,
            MediaServerLibraryItem.tmdb_id.is_not(None),
        )
    ).all()

    for zeile in zeilen:
        for stufe, bytes_ in (
            (QualityTier.standard, zeile.size_standard),
            (QualityTier.uhd, zeile.size_uhd),
        ):
            if bytes_ <= 0:
                continue
            kennung = schluessel(MediaType.movie, stufe, tmdb_id=zeile.tmdb_id)
            if kennung is None or kennung in ziel:
                continue
            ziel[kennung] = _Gemessen(
                key=kennung,
                media_type=MediaType.movie,
                tier=stufe,
                tmdb_id=zeile.tmdb_id,
                tvdb_id=zeile.tvdb_id,
                season=None,
                title=zeile.title,
                size_bytes=bytes_,
            )


def _tvdb_nach_tmdb(db: Session) -> dict[int, int]:
    """Welche TMDB-Nummer gehoert zu welcher TVDB-Nummer?

    Sonarr kennt **nur** TVDB-Nummern; ein Posten wird danach geschluesselt.
    Fuer die Anzeige braucht es aber die TMDB-Nummer - sonst fuehrt eine
    Staffel auf keine Detailseite, und man kann sie als Einzige nicht
    anklicken.

    Beide Nummern stehen bereits an zwei Stellen beieinander: an den eigenen
    Anfragen und im Bestand des Media-Servers. Das kostet keine einzige
    Netzabfrage - anders als der Umweg ueber TMDB.
    """
    paare: dict[int, int] = {}
    for tvdb_id, tmdb_id in db.execute(
        select(MediaRequest.tvdb_id, MediaRequest.tmdb_id).where(
            MediaRequest.tvdb_id.is_not(None), MediaRequest.tmdb_id.is_not(None)
        )
    ).all():
        paare.setdefault(int(tvdb_id), int(tmdb_id))

    for tvdb_id, tmdb_id in db.execute(
        select(MediaServerLibraryItem.tvdb_id, MediaServerLibraryItem.tmdb_id).where(
            MediaServerLibraryItem.tvdb_id.is_not(None),
            MediaServerLibraryItem.tmdb_id.is_not(None),
        )
    ).all():
        paare.setdefault(int(tvdb_id), int(tmdb_id))

    return paare


def _schreiben(db: Session, gemessen: dict[str, _Gemessen]) -> Ergebnis:
    """Den gemessenen Stand in die Datenbank uebertragen."""
    vorhanden = {zeile.key: zeile for zeile in db.scalars(select(StorageEntry)).all()}

    # Beim allererste Lauf gehoert alles dem Haus: Was schon da war, hat
    # niemand ueber Nexview angefordert - und niemand soll am Tag der
    # Einfuehrung ueberzogen sein. Erkannt an der leeren Tabelle und nicht an
    # einem Zeitstempel, der ein zurueckgespieltes Backup ueberleben wuerde.
    erster_lauf = not vorhanden
    zuordnung = {} if erster_lauf else _zuordnung(db, gemessen.values())

    # Staffeln kommen ohne TMDB-Nummer herein - ohne sie fuehren sie auf keine
    # Detailseite. Beide Nummern stehen bereits beieinander; nachschlagen
    # kostet nichts.
    nach_tmdb = _tvdb_nach_tmdb(db)
    for wert in gemessen.values():
        if wert.tmdb_id is None and wert.tvdb_id:
            wert.tmdb_id = nach_tmdb.get(wert.tvdb_id)

    neu = aktualisiert = gewachsen = 0
    jetzt = utcnow()

    for kennung, wert in gemessen.items():
        zeile = vorhanden.pop(kennung, None)
        if zeile is None:
            besitzer = zuordnung.get(kennung)
            db.add(
                StorageEntry(
                    key=kennung,
                    user_id=besitzer,
                    media_type=wert.media_type,
                    tier=wert.tier,
                    tmdb_id=wert.tmdb_id,
                    tvdb_id=wert.tvdb_id,
                    season=wert.season,
                    title=wert.title,
                    size_bytes=wert.size_bytes,
                    path=wert.path,
                    measured_at=jetzt,
                    state=(
                        StorageState.owned if besitzer else StorageState.house
                    ),
                )
            )
            neu += 1
            continue

        if zeile.size_bytes != wert.size_bytes:
            if wert.size_bytes > zeile.size_bytes:
                gewachsen += 1
            zeile.size_bytes = wert.size_bytes
            aktualisiert += 1
        # Der Titel kann sich aendern (Umbenennung in Radarr), und er ist das
        # Einzige, was den Posten spaeter noch lesbar macht.
        if wert.title and zeile.title != wert.title:
            zeile.title = wert.title
        if wert.path and zeile.path != wert.path:
            zeile.path = wert.path
        # Nachtragen, sobald die Zuordnung bekannt wird - etwa weil jemand die
        # Serie inzwischen ueber Nexview angefragt hat.
        if zeile.tmdb_id is None and wert.tmdb_id:
            zeile.tmdb_id = wert.tmdb_id
        zeile.measured_at = jetzt

    # Wurde jemand zwischenzeitlich Administrator, wandern seine Posten ins
    # Haus. So gilt die Regel durchgehend und nicht nur ab dem naechsten
    # neuen Titel.
    for zeile in db.scalars(
        select(StorageEntry)
        .join(User, User.id == StorageEntry.user_id)
        .where(User.role == Role.admin)
    ).all():
        zeile.user_id = None
        zeile.state = StorageState.house

    # Was uebrig bleibt, meldet keine Quelle mehr.
    #
    # Wichtig: "Radarr kennt es nicht mehr" ist **kein** Beweis, dass die Datei
    # weg ist - siehe _aus_media_server. Uebrig bleibt hier nur, was weder
    # Radarr/Sonarr noch der Media-Server meldet. Genau dieselbe Regel wie im
    # Status-Abgleich, bevor er eine Anfrage auf "geloescht" setzt.
    for zeile in vorhanden.values():
        db.delete(zeile)

    db.commit()
    return Ergebnis(
        neu=neu,
        aktualisiert=aktualisiert,
        entfernt=len(vorhanden),
        gewachsen=gewachsen,
        erster_lauf=erster_lauf,
    )


def _zuordnung(db: Session, werte) -> dict[str, int]:
    """Wem gehoert ein neu aufgetauchter Posten?

    Wer den Titel ueber Nexview angefragt hat, traegt ihn. Alles andere ist
    Hausbestand.

    Das gilt ausdruecklich **auch dann**, wenn noch gar keine Grenze
    eingeschaltet ist. Wuerde erst ab dem Einschalten zugeordnet, staende nach
    Wochen des Messens bei jedem eine Null - und die Frage "wer belegt am
    meisten" waere unbeantwortbar, also der Zweck der Messung verfehlt.
    """
    anfragen = db.scalars(
        select(MediaRequest).where(MediaRequest.status.in_(ZURECHENBAR))
    ).all()

    # **Was ein Administrator holt, gehoert dem Haus.**
    #
    # Er hat ohnehin keine Grenze - ihm etwas zuzurechnen erfuellt keinen
    # Zweck und verfaelscht nur die Uebersicht: Dort staende dann "admin
    # belegt 20 TB", und alle anderen waeren daneben unsichtbar. Wer kuratiert,
    # holt fuer alle.
    #
    # Die Herkunft geht dabei nicht verloren - ``StorageEntry.request_id``
    # zeigt weiterhin auf die Anfrage.
    admins = set(
        db.scalars(select(User.id).where(User.role == Role.admin)).all()
    )

    nach_film: dict[tuple[int, str], int] = {}
    nach_serie: dict[tuple[int, str, int | None], int] = {}
    for anfrage in anfragen:
        if anfrage.user_id in admins:
            continue
        stufe = anfrage.tier.value if anfrage.tier else QualityTier.standard.value
        if anfrage.media_type == MediaType.movie:
            nach_film.setdefault((anfrage.tmdb_id, stufe), anfrage.user_id)
        elif anfrage.tvdb_id:
            nach_serie.setdefault(
                (anfrage.tvdb_id, stufe, anfrage.season), anfrage.user_id
            )

    ergebnis: dict[str, int] = {}
    for wert in werte:
        if wert.media_type == MediaType.movie:
            besitzer = nach_film.get((wert.tmdb_id, wert.tier.value))
        else:
            # Erst die genaue Staffel, dann die Anfrage ueber die ganze Serie.
            besitzer = nach_serie.get(
                (wert.tvdb_id, wert.tier.value, wert.season)
            ) or nach_serie.get((wert.tvdb_id, wert.tier.value, None))
        if besitzer is not None:
            ergebnis[wert.key] = besitzer
    return ergebnis


def verbuchen(db: Session, request: MediaRequest, eintrag: object) -> int:
    """Einen gerade fertig gewordenen Titel sofort zurechnen.

    Der stuendliche Abgleich wuerde das auch erledigen - aber bis zu eine
    Stunde spaeter. Wer gerade etwas angefragt hat und nachsieht, was es ihn
    kostet, findet dann eine Null vor und haelt die Anzeige fuer kaputt.

    Aufgerufen aus ``status_poller.check_once``, genau in dem Moment, in dem
    ``has_file`` wahr wird: Dort liegen Anfrage, Nutzer und der frisch geholte
    Bibliothekseintrag samt Groesse gleichzeitig vor.

    Gibt zurueck, wie viele Posten dabei zugerechnet wurden.
    """
    # Was ein Administrator holt, gehoert dem Haus - siehe _zuordnung.
    anfragender = db.get(User, request.user_id)
    if anfragender is not None and anfragender.role == Role.admin:
        return 0

    gemessen: dict[str, _Gemessen] = {}
    stufe = request.tier or QualityTier.standard

    if request.media_type == MediaType.movie:
        _film_aufnehmen(gemessen, stufe, request.tmdb_id, eintrag)  # type: ignore[arg-type]
    elif request.tvdb_id:
        _serie_aufnehmen(gemessen, stufe, request.tvdb_id, eintrag)  # type: ignore[arg-type]
        # Eine Anfrage auf **eine** Staffel darf auch nur diese eine belasten.
        if request.season is not None:
            nur = schluessel(
                MediaType.tv, stufe, tvdb_id=request.tvdb_id, season=request.season
            )
            gemessen = {k: v for k, v in gemessen.items() if k == nur}

    if not gemessen:
        return 0

    vorhanden = {
        zeile.key: zeile
        for zeile in db.scalars(
            select(StorageEntry).where(StorageEntry.key.in_(gemessen))
        ).all()
    }

    jetzt = utcnow()
    verbucht = 0
    for kennung, wert in gemessen.items():
        zeile = vorhanden.get(kennung)
        if zeile is None:
            db.add(
                StorageEntry(
                    key=kennung,
                    user_id=request.user_id,
                    media_type=wert.media_type,
                    tier=wert.tier,
                    tmdb_id=wert.tmdb_id or request.tmdb_id,
                    tvdb_id=wert.tvdb_id or request.tvdb_id,
                    season=wert.season,
                    title=wert.title or request.title,
                    size_bytes=wert.size_bytes,
                    measured_at=jetzt,
                    state=StorageState.owned,
                    request_id=request.id,
                )
            )
            verbucht += 1
            continue

        # Vorhandenes nur uebernehmen, wenn es **niemandem** gehoert.
        #
        # Einem anderen Nutzer etwas wegzunehmen waere in jedem Fall falsch;
        # und beim Hausbestand ist die Uebernahme richtig, weil derjenige den
        # Titel ja gerade selbst ins Haus geholt hat.
        if zeile.state == StorageState.house and zeile.user_id is None:
            zeile.user_id = request.user_id
            zeile.state = StorageState.owned
            zeile.request_id = request.id
            verbucht += 1
        if wert.size_bytes > 0:
            zeile.size_bytes = wert.size_bytes
            zeile.measured_at = jetzt

    return verbucht


def posten_von(db: Session, posten_id: int) -> Posten | None:
    """Ein einzelner Posten in der Form, die nach aussen geht."""
    zeile = db.get(StorageEntry, posten_id)
    return _als_posten(zeile) if zeile is not None else None


@dataclass(frozen=True)
class Uebernahme:
    """Was beim Zuschlagen an das Haus geschehen ist.

    Der bisherige Besitzer steht mit dabei, weil der Aufrufer ihn braucht: Er
    bekommt die Nachricht. Ihn nachtraeglich aus dem Posten zu lesen ginge
    nicht mehr - der gehoert dann ja schon dem Haus.
    """

    posten: Posten
    vorher_user_id: int


def ins_haus(db: Session, posten_id: int) -> Uebernahme | None:
    """Einen einzelnen Posten dem Haus zuschlagen.

    Der Titel bleibt liegen - **es wird keine Datei angefasst.** Es wechselt
    nur, wem er zugerechnet wird, und das Kontingent des bisherigen Besitzers
    wird um diesen Betrag frei.

    Gedacht fuer den Fall, den der Betreiber am besten beurteilen kann: "Den
    Klassiker will hier ohnehin jeder sehen, der soll nicht auf deinem Konto
    lasten." Der Nutzer muss dafuer nichts beantragen.

    Gibt den Posten zurueck, wenn sich etwas geaendert hat - sonst ``None``,
    etwa wenn er ohnehin schon dem Haus gehoert.
    """
    posten = db.get(StorageEntry, posten_id)
    if posten is None or posten.user_id is None:
        return None
    vorher = posten.user_id
    posten.user_id = None
    posten.state = StorageState.house
    db.flush()
    return Uebernahme(posten=_als_posten(posten), vorher_user_id=vorher)


def zuruecksetzen(db: Session) -> int:
    """Alles ins Haus, alle Konten auf null.

    Gebraucht beim Umschalten der Betriebsart und als Notausgang. **Dateien
    werden dabei nie angefasst** - es wechselt nur, wem sie zugerechnet werden.
    """
    zeilen = db.scalars(
        select(StorageEntry).where(StorageEntry.state != StorageState.house)
    ).all()
    for zeile in zeilen:
        zeile.user_id = None
        zeile.state = StorageState.house
    db.commit()
    return len(zeilen)
