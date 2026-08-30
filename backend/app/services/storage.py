"""Wer belegt wie viel Platz - Erfassung und Kontostand.

⚠️ **Dieser Kopf stand einmal auf "Stufe 1 von dreien: nur messen und
anzeigen".** Das galt genau eine Version lang. Inzwischen begrenzt dieses
Modul (``stand_fuer`` speist das Tor in ``requests_service.create_request``,
das eine Anfrage im Minus mit 429 abweist), es nimmt Titel zurueck
(``abgeben``/``ins_haus``/``entfolgen``) und es **loescht** ueber
``loeschen`` - der einzige Weg in Nexview, auf dem Dateien wirklich
verschwinden. Wer hier etwas aendert, aendert also nichts Harmloses.

Die Rechnung ist eine bewusste Fiktion: Speicherplatz wird nicht *pro Person*
verbraucht, sondern gemeinsam belegt. Damit sie ehrlich bleibt, gilt eine
klare Definition - ein Konto deckt, was jemand ins Haus geholt hat **und noch
beansprucht**. Alles andere ist Hausbestand und zaehlt bei niemandem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from ..models import (
    MediaRequest,
    MediaServerLibraryItem,
    MediaType,
    NotificationType,
    QualityTier,
    RequestStatus,
    StorageEntry,
    Role,
    StorageState,
    StorageWish,
    User,
    UserWatched,
    UserWatchedSeason,
    utcnow,
)
from .arr import ArrError
from .radarr import LibraryEntry as MovieEntry
from .settings_service import AppSettings
from . import sonarr
from .sonarr import LibraryEntry as SeriesEntry
from . import library, notify, quota

logger = logging.getLogger("nexview.storage")

# Welche Anfragen einen Posten ueberhaupt zugerechnet bekommen koennen.
# Bewusst dieselbe Auswahl wie beim Stueck-Kontingent minus der Zustaende, in
# denen noch gar keine Datei existieren kann.
# Ab wieviel Zuwachs sich eine Aufwertung meldet.
#
# Radarr schiebt staendig geringfuegig groessere Releases nach; eine Meldung
# ueber 80 MB waere Laerm - und Laerm wird weggeklickt, danach auch die
# Meldung, auf die es ankommt. Ein Gigabyte ist zugleich die Einheit, in der
# das ganze Kontingent gerechnet wird.
MELDESCHWELLE = 1024**3

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
    request_id: int | None = None,
) -> str | None:
    """Die eindeutige Kennung eines Postens.

    Filme ueber die TMDB-Nummer (die kennt Radarr), Serien ueber die
    TVDB-Nummer (die kennt Sonarr). Nicht mischen: Sonst entstuende derselbe
    Titel zweimal, je nachdem welche Quelle ihn zuerst gemeldet hat.

    ``request_id`` macht daraus die Kennung eines **Folgen-Pakets**
    (``...:s2:r17``): Ein Paket belegt nur die Dateien seiner Folgen, und der
    Rest der Staffel wird getrennt gefuehrt - zwei Posten, zwei Kennungen.
    Die Anfrage-Nummer ist dabei die stabilste Kennung, die es gibt: Die
    Folgenliste selbst koennte in keiner festen Laenge in den Schluessel.

    Gibt ``None`` zurueck, wenn die noetige Nummer fehlt - dann laesst sich der
    Posten nicht verlaesslich fuehren und wird uebersprungen.
    """
    art = media_type.value if isinstance(media_type, MediaType) else str(media_type)
    stufe = tier.value if isinstance(tier, QualityTier) else str(tier)
    if art == MediaType.movie.value:
        return f"movie:{stufe}:tmdb:{tmdb_id}" if tmdb_id else None
    if not tvdb_id:
        return None
    basis = f"tv:{stufe}:tvdb:{tvdb_id}:s{season if season is not None else 0}"
    return f"{basis}:r{request_id}" if request_id else basis


def spuerbar_zugelegt(db: Session, request, size_bytes: int) -> bool:
    """Hat dieser geladene Posten spuerbar zugelegt - eine Aufwertung?

    Fuer den Status-Abgleich: Der sieht die frische Groesse in derselben
    Antwort, die gerade "noch da" beantwortet hat, und zieht bei Ja den
    Speicher-Abgleich vor, statt bis zur vollen Stunde zu warten. Verbucht
    und gemeldet wird erst **dort** (``abgleichen``) - eine Rechnung, eine
    Schwelle, eine Meldung, samt der Staffel-Schutzregel ``unvollstaendig``.
    Hier wird nur verglichen, nichts geschrieben.
    """
    if size_bytes <= 0:
        return False
    kennung = schluessel(
        request.media_type,
        request.tier,
        tmdb_id=request.tmdb_id,
        tvdb_id=request.tvdb_id,
        season=request.season,
    )
    if kennung is None:
        return False
    zeile = db.scalar(select(StorageEntry).where(StorageEntry.key == kennung))
    return zeile is not None and size_bytes - zeile.size_bytes >= MELDESCHWELLE


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
    # Wann der Nutzer den Posten abgegeben hat. NULL, solange er ihn behaelt.
    released_at: datetime | None = None
    # Was er sich dabei wuenscht - "delete" oder "keep", NULL ohne Abgabe.
    release_wish: str | None = None
    # Hat der Besitzer den Titel schon gesehen? Nur auf der **eigenen**
    # Speicherseite gefuellt, und nur bei Filmen - die Gesehen-Daten sind
    # Titel-genau, bei einer Staffel wuerde "gesehen" zu viel behaupten.
    # ``None`` heisst "keine Aussage" (Serie, oder kein Media-Server).
    watched: bool | None = None
    # Fuehrt Radarr/Sonarr den Titel noch? ``False`` heisst: nicht mehr
    # loeschbar - Nexview loescht ausschliesslich ueber diese Dienste.
    managed: bool = True


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

    Drei Stufen, in dieser Reihenfolge - dieselben wie beim Stueck-Kontingent
    (``quota._limit_for``). Zwei verschiedene Regeln fuer dieselbe Frage waeren
    eine Falle.

    1. **Administratoren sind immer unbegrenzt.**
    2. Traegt das Konto etwas Eigenes, gilt das: ``quota.UNBEGRENZT``
       ausdruecklich ohne Grenze, die **0** ausdruecklich "darf nichts".
    3. Sonst die Vorgabe des Hauses - und ist auch die leer, ist niemand
       begrenzt.

    ⚠️ **Die 0 hat ihre Bedeutung gewechselt.** Bis 0.19 hiess sie hier
    "unbegrenzt"; gespeicherte Nullen ziehen deshalb einmalig auf
    ``quota.UNBEGRENZT`` um (``db._kontingente_dreiwertig_machen``). Ohne den
    Umzug haette dieselbe Zahl von einem Tag auf den anderen das Gegenteil
    bedeutet - und ein Konto still gesperrt.
    """
    if user.role == Role.admin:
        return None
    eigen = user.storage_limit_gb
    if eigen is not None:
        return None if eigen == quota.UNBEGRENZT else max(0, eigen) * GB
    vorgabe = settings.storage_default_limit_gb
    return vorgabe * GB if vorgabe is not None else None


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
    db: Session,
    user_id: int,
    *,
    suche: str = "",
    seite: int = 1,
    je_seite: int = 20,
    nur_gesehene: bool = False,
) -> tuple[list[Posten], int]:
    """Alles, was ein Nutzer belegt - das Groesste zuerst, seitenweise.

    Die Reihenfolge ist der eigentliche Zweck der Liste: Wer Platz schaffen
    soll, muss zuerst sehen, wo der Platz steckt.

    Geblaettert wie der Hausbestand, und aus demselben Grund: Wer zweihundert
    Titel hat, findet einen bestimmten sonst nicht wieder.

    ``nur_gesehene`` zeigt die Kandidaten fuers Abgeben: Filme, die der Nutzer
    laut Media-Server gesehen hat, und Staffeln, deren Folgen er **alle**
    gesehen hat - halb Gesehenes zaehlt nicht, siehe ``UserWatchedSeason``.
    Der Filter greift in der Datenbank, nicht auf der Seite - sonst stimmte
    die Seitenzahl nicht.
    """
    bedingungen = [
        StorageEntry.user_id == user_id,
        StorageEntry.state.in_((StorageState.owned, StorageState.pending)),
    ]
    if nur_gesehene:
        film_gesehen = (
            (StorageEntry.media_type == MediaType.movie)
            & StorageEntry.tmdb_id.in_(
                select(UserWatched.tmdb_id).where(
                    UserWatched.user_id == user_id,
                    UserWatched.media_type == MediaType.movie,
                )
            )
        )
        staffel_gesehen = (
            (StorageEntry.media_type == MediaType.tv)
            & exists().where(
                UserWatchedSeason.user_id == user_id,
                UserWatchedSeason.tmdb_id == StorageEntry.tmdb_id,
                UserWatchedSeason.season == StorageEntry.season,
            )
        )
        bedingungen.append(film_gesehen | staffel_gesehen)
    return _seite(
        db,
        bedingungen,
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
    gefunden = await traeger(settings)
    return sum(t.frei for t in gefunden), len(gefunden)


@dataclass(frozen=True)
class Traeger:
    """Ein Datentraeger, auf dem mindestens ein Zielordner liegt."""

    #: Gesamtgroesse - zugleich die Kennung, ueber die entdoppelt wird.
    gesamt: int
    frei: int
    #: Welche Zielordner darauf liegen. Fuer die Anzeige: "die Platte unter
    #: /data/Movies" sagt einem Betreiber mehr als eine Byte-Zahl.
    ordner: tuple[str, ...]

    @property
    def belegt_anteil(self) -> float:
        """Anteil belegt, 0.0 bis 1.0. Ohne Gesamtgroesse ist er 0."""
        return 0.0 if self.gesamt <= 0 else (self.gesamt - self.frei) / self.gesamt


async def traeger(settings: AppSettings) -> list[Traeger]:
    """Die Datentraeger hinter den Zielordnern - jeder genau einmal.

    ⚠️ **Herausgeloest aus ``freier_platz``, weil die Entdopplung jetzt zweimal
    gebraucht wird**: dort fuer "wieviel ist noch frei", hier fuer "die Platte
    ist zu 91 Prozent voll". Die Begruendung, warum ueber die *Gesamtgroesse*
    entdoppelt wird und nicht ueber den freien Platz, steht im Docstring von
    ``freier_platz`` - sie ist an einer echten Anlage teuer gelernt worden und
    darf nicht in zwei Fassungen auseinanderlaufen.
    """
    # Kennung des Traegers -> (freier Platz, Ordner darauf). Bei mehreren
    # Meldungen zaehlt der kleinste freie Wert: Er ist die juengste Nachricht
    # ueber eine Platte, auf die gerade geschrieben wird, und untertreibt im
    # Zweifel.
    gefunden: dict[int, tuple[int, list[str]]] = {}

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
                bisher = gefunden.get(gesamt)
                if bisher is None:
                    gefunden[gesamt] = (frei, [pfad])
                else:
                    pfade = bisher[1]
                    if pfad not in pfade:
                        pfade.append(pfad)
                    gefunden[gesamt] = (min(bisher[0], frei), pfade)

    return [
        Traeger(gesamt=gesamt, frei=frei, ordner=tuple(sorted(pfade)))
        for gesamt, (frei, pfade) in sorted(gefunden.items())
    ]


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
        released_at=zeile.released_at,
        release_wish=zeile.release_wish.value if zeile.release_wish else None,
        managed=zeile.arr_managed,
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
    # ⚠️ Laeuft der Download dieser Staffel noch?
    #
    # Nur fuer die Wachstums-Meldung gedacht, und nur dort benutzt. Der Posten
    # wird trotzdem gefuehrt - die Bytes liegen ja auf der Platte. Aber
    # "gewachsen" waere die falsche Vokabel: Eine Staffel, die ueber Stunden
    # laedt, haette bei jedem Abgleich eine Meldung ausgeloest, obwohl sie
    # schlicht ankommt. Gemeint ist mit der Meldung die **Aufwertung** -
    # aus 5 GB werden 50, weil 1080p durch 2160p ersetzt wurde.
    unvollstaendig: bool = False
    # Kommt der Posten von Radarr/Sonarr - oder nur noch vom Media-Server?
    verwaltet: bool = True
    # Seit wann die Datei da liegt (siehe ``StorageEntry.added_at``). Bei
    # Filmen kommt es gratis mit; bei Staffeln wird es nachgetragen, aber nur
    # dort, wo es noch fehlt - siehe ``_staffeldaten_nachtragen``.
    added_at: datetime | None = None
    # Nur bei Serien und nur fuer genau diese Nachfrage: Ohne die Sonarr-Id
    # laesst sich ``/episodefile`` nicht abrufen.
    arr_id: int | None = None


@dataclass(frozen=True)
class Zuwachs:
    """Ein Posten ist gewachsen - Radarr hat etwas Besseres nachgeschoben.

    Traegt die Kennung mit, nicht nur den Titel: Daran haengt inzwischen mehr
    als eine Meldung - eine Bewertung dieses Titels gilt danach einer Datei,
    die es nicht mehr gibt, und muss gekennzeichnet werden.
    """

    # ``None`` heisst Hausbestand - niemand wird belastet. Fuer die
    # Speicher-Meldung faellt so ein Posten heraus, fuer die Bewertung
    # ausdruecklich **nicht**: Ob eine Datei jemandem zugerechnet ist, hat
    # nichts damit zu tun, ob eine Bewertung von ihr noch gilt.
    user_id: int | None
    titel: str
    zuwachs: int
    media_type: MediaType
    tmdb_id: int | None
    season: int | None


@dataclass(frozen=True)
class Ergebnis:
    """Was ein Abgleich bewirkt hat - fuer Protokoll und Anzeige."""

    neu: int = 0
    aktualisiert: int = 0
    entfernt: int = 0
    gewachsen: int = 0
    erster_lauf: bool = False
    # Posten, die einem Nutzer gehoeren und **spuerbar** gewachsen sind. Der
    # Aufrufer benachrichtigt; dieses Modul kennt keine Benachrichtigungen.
    zugelegt: list["Zuwachs"] = field(default_factory=list)


async def abgleichen(db: Session, settings: AppSettings) -> Ergebnis:
    """Belegung neu erfassen.

    **Erst holen, dann schreiben.** SQLite laesst genau einen Schreiber zu;
    bliebe eine Schreib-Transaktion offen, waehrend auf Radarr oder Sonarr
    gewartet wird, wartet jede Anfrage eines Nutzers mit. Deshalb laufen alle
    Abfragen zuerst und die Datenbank wird danach in einem kurzen Zug
    angefasst - dasselbe Vorgehen wie im Status-Abgleich.
    """
    gemessen = await _erfassen(db, settings)
    ergebnis = _schreiben(db, gemessen)
    _wachstum_melden(db, ergebnis)
    return ergebnis


def _wachstum_melden(db: Session, ergebnis: Ergebnis) -> None:
    """Wer belastet wird, ohne etwas getan zu haben, erfaehrt davon.

    Der Fall: Radarr oder Sonarr schieben ein besseres Release nach, aus 5 GB
    werden 50 - und das Konto steht ploetzlich anders da, obwohl niemand etwas
    angefragt hat. Ohne Hinweis sucht der Betroffene den Fehler bei sich.

    **Beim allerersten Lauf wird nichts gemeldet.** Dort gehoert ohnehin alles
    dem Haus, es gibt also gar keinen Betroffenen - und selbst wenn: Eine
    Ladung Meldungen am Tag der Einfuehrung waere der denkbar schlechteste
    Einstieg.

    Der Zuwachs steht **nicht** im Textbaustein: Nachrichten-Schluessel duerfen
    keine Platzhalter tragen, sonst stehen die geschweiften Klammern woertlich
    in der Glocke. Der Titel geht als ``message_title`` mit, die Zahl sieht man
    im eigenen Speicher-Reiter.
    """
    if ergebnis.erster_lauf or not ergebnis.zugelegt:
        return

    for eintrag in ergebnis.zugelegt:
        # Nur wer belastet wird, bekommt die Meldung - beim Hausbestand gibt
        # es niemanden.
        person = db.get(User, eintrag.user_id) if eintrag.user_id else None
        if person is not None:
            notify.create(
                db,
                user=person,
                kind=NotificationType.storage_grew,
                message_key="notifications.storageGrew",
                title=eintrag.titel,
            )
    db.commit()



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
                    "Radarr (%s) not reachable, sizes left unchanged: %s",
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
                    "Sonarr (%s) not reachable, sizes left unchanged: %s",
                    stufe.value,
                    fehler.message,
                )

    await _pakete_aufnehmen(db, settings, gemessen)
    _aus_media_server(db, gemessen)
    await _staffeldaten_nachtragen(db, settings, gemessen)
    return gemessen


async def _pakete_aufnehmen(
    db: Session, settings: AppSettings, gemessen: dict[str, _Gemessen]
) -> None:
    """Folgen-Pakete aus der Staffel-Zeile herausrechnen.

    Ein Paket belegt nur die Dateien **seiner** Folgen. Die Staffelstatistik
    von Sonarr kennt aber nur die ganze Staffel - deshalb wird fuer jede Serie
    mit laufendem Paket einmal je Durchgang die Dateiliste geholt, das Paket
    als eigener Posten gefuehrt (Kennung ``...:r<anfrage>``) und der
    Staffel-Zeile abgezogen. Bleibt von ihr nichts uebrig, faellt sie weg.

    Serien ohne Paket kosten weiterhin keinen einzigen zusaetzlichen Aufruf.
    Die Klammer bei null faengt Mess-Drift zwischen Staffelstatistik und
    Dateisummen ab - beide stammen aus verschiedenen Sonarr-Antworten.
    """
    anfragen = [
        anfrage
        for anfrage in db.scalars(
            select(MediaRequest).where(
                MediaRequest.status.in_(ZURECHENBAR),
                MediaRequest.episodes.is_not(None),
            )
        )
        if anfrage.episodes and anfrage.tvdb_id and anfrage.season is not None
    ]
    if not anfragen:
        return

    befunde: dict[tuple[str, int], tuple[dict, dict[int, int]] | None] = {}
    for anfrage in anfragen:
        stufe = anfrage.tier or QualityTier.standard
        basis = schluessel(
            MediaType.tv, stufe, tvdb_id=anfrage.tvdb_id, season=anfrage.season
        )
        staffelzeile = gemessen.get(basis or "")
        if staffelzeile is None or staffelzeile.arr_id is None:
            # Die Serie meldet keine Quelle (mehr) - dann gibt es auch nichts
            # aufzuteilen; eine bestehende Paket-Zeile raeumt der Abgleich ab.
            continue

        merkmal = (stufe.value, anfrage.tvdb_id)
        if merkmal not in befunde:
            client = library.sonarr_client(settings, stufe.value)
            if client is None:
                befunde[merkmal] = None
            else:
                try:
                    stand = await client.folgen_stand(staffelzeile.arr_id)
                    dateien = (
                        await client.get(
                            "/episodefile", {"seriesId": staffelzeile.arr_id}
                        )
                        or []
                    )
                    groessen = {
                        int(datei["id"]): int(datei.get("size") or 0)
                        for datei in dateien
                        if isinstance(datei, dict) and datei.get("id")
                    }
                    befunde[merkmal] = (stand, groessen)
                except ArrError as fehler:
                    logger.warning(
                        "Sonarr (%s) gave no episode files for series %s - "
                        "package sizes left unchanged: %s",
                        stufe.value,
                        staffelzeile.arr_id,
                        fehler.message,
                    )
                    befunde[merkmal] = None
        befund = befunde[merkmal]
        if befund is None:
            continue
        stand, groessen = befund
        staffel = stand.get(anfrage.season) or {}

        eigene = [
            folge
            for nummer in anfrage.episodes
            if (folge := staffel.get(nummer)) is not None
        ]
        bytes_ = sum(
            groessen.get(folge.datei_id, 0) for folge in eigene if folge.datei_id
        )
        kennung = schluessel(
            MediaType.tv,
            stufe,
            tvdb_id=anfrage.tvdb_id,
            season=anfrage.season,
            request_id=anfrage.id,
        )
        if kennung is None:
            continue
        gemessen[kennung] = _Gemessen(
            key=kennung,
            media_type=MediaType.tv,
            tier=stufe,
            tmdb_id=staffelzeile.tmdb_id,
            tvdb_id=anfrage.tvdb_id,
            season=anfrage.season,
            title=staffelzeile.title,
            size_bytes=bytes_,
            path=staffelzeile.path,
            # Solange nicht jede bestellte Folge liegt, waechst das Paket noch -
            # dieselbe Regel wie bei einer ladenden Staffel.
            unvollstaendig=any(
                not folge.has_file for folge in eigene
            )
            or len(eigene) < len(anfrage.episodes),
            arr_id=staffelzeile.arr_id,
        )
        # Der Staffel-Zeile abziehen; die Klammer faengt Mess-Drift ab.
        staffelzeile.size_bytes = max(0, staffelzeile.size_bytes - bytes_)
        if staffelzeile.size_bytes == 0 and basis:
            del gemessen[basis]


async def _staffeldaten_nachtragen(
    db: Session, settings: AppSettings, gemessen: dict[str, _Gemessen]
) -> None:
    """Seit wann die Staffeln da liegen - **nur wo es noch fehlt**.

    ⚠️ **Der Sparsamkeits-Teil ist der wichtige.** Sonarr haengt an ``/series``
    zwar Groesse und Folgenzahl je Staffel, aber kein Datum; das steht nur an
    der einzelnen Datei und kostet eine Abfrage je Serie. Dieser Abgleich
    laeuft **stuendlich** - sie jedes Mal fuer jede Serie zu stellen hiesse bei
    zweihundert Serien fast fuenftausend Abfragen am Tag, fuer ein Datum, das
    sich nie aendert.

    Gefragt wird deshalb nur nach Staffeln, deren Datum in der Datenbank noch
    fehlt. Nach dem ersten Lauf ist das praktisch keine mehr - nur noch neue
    Staffeln.

    Faellt die Abfrage aus, bleibt das Datum eben leer. Der Aufraeum-Vorschlag
    laesst solche Posten dann aus, statt ihr Alter zu raten.
    """
    bekannt = set(
        db.scalars(
            select(StorageEntry.key).where(StorageEntry.added_at.is_not(None))
        )
    )
    # Je Serie einmal, nicht je Staffel - eine Abfrage liefert alle auf einmal.
    offen: dict[int, list[_Gemessen]] = {}
    for wert in gemessen.values():
        if wert.season is None or wert.arr_id is None or wert.key in bekannt:
            continue
        offen.setdefault(wert.arr_id, []).append(wert)
    if not offen:
        return

    for stufe in (QualityTier.standard, QualityTier.uhd):
        if not settings.arr_configured("tv", stufe.value):
            continue
        client = library.sonarr_client(settings, stufe.value)
        if client is None:
            continue
        for serie_id, posten in list(offen.items()):
            try:
                daten = await sonarr.staffel_daten(client, serie_id)
            except ArrError as fehler:
                logger.warning(
                    "Sonarr (%s) gave no file dates for series %s: %s",
                    stufe.value,
                    serie_id,
                    fehler.message,
                )
                continue
            for wert in posten:
                wann = daten.get(wert.season) if wert.season is not None else None
                if wann is not None:
                    wert.added_at = wann
            if daten:
                offen.pop(serie_id, None)

    logger.info("File dates fetched; %d series still without one", len(offen))


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
        added_at=eintrag.added_at,
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
        stand = (getattr(eintrag, "staffeln", None) or {}).get(staffel)
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
            unvollstaendig=stand is not None and not stand.vollstaendig,
            arr_id=eintrag.arr_id,
        )


def _aus_media_server(db: Session, ziel: dict[str, _Gemessen]) -> None:
    """Filme, die nur noch im Media-Server liegen.

    Der Fall, um den es geht: laden, bis die Qualitaet stimmt, dann den
    Eintrag aus Radarr werfen und die Datei behalten. Danach ist der
    Media-Server die einzige Stelle, die die Groesse ueberhaupt noch kennt.

    Was Radarr bereits gemeldet hat, wird **nicht** ueberschrieben - dessen
    Zahl ist die genauere. Serien bleiben aussen vor: Dort haengen die Dateien
    an den Folgen, der Serien-Eintrag traegt keine Groesse.

    **Melden mehrere Server denselben Titel, zaehlt der groessere Wert.** Bis
    zum Parallelbetrieb gewann schlicht der erste Treffer - was in der Praxis
    stimmte, weil es nur einen Server gab, aber eben ein Zufall war und keine
    Entscheidung. Die Begruendung fuer "groesser" steht unten an der Stelle,
    an der es passiert.
    """
    zeilen = db.scalars(
        select(MediaServerLibraryItem).where(
            MediaServerLibraryItem.media_type == MediaType.movie,
            MediaServerLibraryItem.tmdb_id.is_not(None),
        )
    ).all()

    # Erst den besten Wert je Posten ueber **alle** Server bestimmen, dann
    # eintragen. Die Reihenfolge ist wichtig: Waere beides in einer Schleife,
    # entschiede darueber, welcher Server zufaellig zuerst gelesen wird - und
    # dieselbe Bibliothek ergaebe von Lauf zu Lauf andere Zahlen.
    bester: dict[str, _Gemessen] = {}
    quelle: dict[str, str] = {}
    uneinig = 0

    for zeile in zeilen:
        for stufe, bytes_ in (
            (QualityTier.standard, zeile.size_standard),
            (QualityTier.uhd, zeile.size_uhd),
        ):
            # Null heisst "unbekannt", nicht "leer" - ein Server ohne Angabe
            # soll keinen Posten auf 0 druecken. Faellt hier von selbst weg.
            if bytes_ <= 0:
                continue
            kennung = schluessel(MediaType.movie, stufe, tmdb_id=zeile.tmdb_id)
            if kennung is None:
                continue

            vorher = bester.get(kennung)
            if vorher is not None:
                if quelle.get(kennung) != zeile.provider:
                    uneinig += 1
                # ⚠️ **Bei Uneinigkeit gewinnt der groessere Wert** - und das
                # ist keine Vermutung darueber, wer recht hat, sondern eine
                # Entscheidung darueber, welcher Irrtum weniger schadet.
                #
                # Zu wenig zu zaehlen hiesse: Die Platte laeuft voll, obwohl
                # die Kontingente greifen - genau das, wogegen sie gebaut
                # wurden. Zu viel zu zaehlen heisst: Jemand hoert "aufgebraucht",
                # obwohl noch Luft ist. Das ist aergerlich, aber sichtbar, und
                # er kann etwas abgeben.
                if vorher.size_bytes >= bytes_:
                    continue

            bester[kennung] = _Gemessen(
                key=kennung,
                media_type=MediaType.movie,
                tier=stufe,
                tmdb_id=zeile.tmdb_id,
                tvdb_id=zeile.tvdb_id,
                season=None,
                title=zeile.title,
                size_bytes=bytes_,
                # ⚠️ Nur der Media-Server kennt ihn noch - also **nicht mehr
                # loeschbar**. Wer hier landet, hat den Eintrag aus Radarr
                # geworfen und die Datei behalten.
                verwaltet=False,
            )
            quelle[kennung] = zeile.provider

    # Was Radarr/Sonarr schon gemeldet haben, **je Titel** - nicht je
    # Schluessel. Genau daran ist die Regel unten frueher gescheitert.
    schon_gemeldet: dict[int, set[int]] = {}
    for wert in ziel.values():
        if wert.media_type == MediaType.movie and wert.tmdb_id is not None:
            schon_gemeldet.setdefault(wert.tmdb_id, set()).add(wert.size_bytes)

    doppelt = 0
    for kennung, wert in bester.items():
        # Was Radarr unter genau diesem Schluessel gemeldet hat, bleibt stehen.
        if kennung in ziel:
            continue

        # ⚠️ **Und was es unter einer anderen Stufe gemeldet hat, ebenfalls.**
        #
        # Hier lag ein Fehler, der Speicher **doppelt zaehlte**. Der Ablauf:
        # Die Standard-Instanz laedt mit einem 1080p-Profil, greift aber eine
        # 2160p-Datei - das passiert oft genug. Nexview verbucht Radarrs
        # Meldung unter der Stufe der **Instanz** (``standard``); der
        # Media-Server meldet dieselbe Datei mit ``videoResolution=4k``, und
        # daraus entstand ein **zweiter** Posten unter ``uhd``. Die Pruefung
        # oben griff nicht: Sie vergleicht den Schluessel, und der
        # unterscheidet sich ja gerade in der Stufe.
        #
        # Gemessen an einer echten Anlage: 32 Dateien, 540 GB, die es einmal
        # gibt und die zweimal gezaehlt wurden. Beim Hausbestand faellt das
        # niemandem auf - wer ein Speicher-Kontingent hat, bekommt dagegen
        # eine Datei zweimal angerechnet und kann nichts dagegen tun, denn
        # der Phantom-Posten laesst sich weder abgeben noch loeschen.
        #
        # Erkannt wird es an der **byte-genauen Groesse**: Zwei wirklich
        # verschiedene Fassungen desselben Films - 1080p in der einen, 4K in
        # der anderen Instanz - haben nie dieselbe Byte-Zahl. Ein echter
        # Doppelbestand bleibt also erhalten, und genau darum geht es.
        if wert.tmdb_id is not None and wert.size_bytes in schon_gemeldet.get(
            wert.tmdb_id, ()
        ):
            doppelt += 1
            continue

        ziel[kennung] = wert

    if doppelt:
        logger.info(
            "Storage: %d file(s) reported by both the media server and Radarr/Sonarr "
            "under different tiers - counted once",
            doppelt,
        )

    if uneinig:
        logger.info(
            "Storage: %d title(s) reported with differing sizes by the connected "
            "servers - the larger value counts",
            uneinig,
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
    zugelegt: list[Zuwachs] = []
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
                    arr_managed=wert.verwaltet,
                    measured_at=jetzt,
                    added_at=wert.added_at,
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
                # **Nur spuerbares Wachstum meldet sich.** Radarr schiebt
                # staendig geringfuegig groessere Releases nach; eine Meldung
                # ueber 80 MB waere Laerm, und Laerm wird weggeklickt - danach
                # auch die Meldung, auf die es ankommt. Der Fall, um den es
                # geht, ist die Aufwertung von 1080p auf 2160p: aus 5 GB
                # werden 50.
                zuwachs = wert.size_bytes - zeile.size_bytes
                # Bewusst **ohne** Bedingung an ``user_id``: Auch ein
                # Haus-Posten kann aufgewertet werden, und eine Bewertung
                # dazu wird davon genauso hinfaellig. Wer die Meldung
                # bekommt, entscheidet der Aufrufer.
                if (
                    zuwachs >= MELDESCHWELLE
                    # ⚠️ Eine Staffel, deren Download noch laeuft, waechst mit
                    # jeder Folge. Ohne diese Bedingung kaeme stuendlich eine
                    # Meldung "ist um X GB gewachsen", bis die Staffel
                    # vollstaendig ist - und die eine Meldung, auf die es
                    # ankommt (die Aufwertung), ginge darin unter.
                    and not wert.unvollstaendig
                ):
                    zugelegt.append(
                        Zuwachs(
                            user_id=zeile.user_id,
                            titel=zeile.title,
                            zuwachs=zuwachs,
                            media_type=zeile.media_type,
                            tmdb_id=zeile.tmdb_id,
                            season=zeile.season,
                        )
                    )
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
        # ⚠️ Faellt bei **jedem** Abgleich neu an, in beide Richtungen: Ein
        # Titel kann aus Radarr fliegen (dann steht er ab jetzt allein im
        # Media-Server) und genauso wieder hinein.
        if zeile.arr_managed != wert.verwaltet:
            zeile.arr_managed = wert.verwaltet
        # ⚠️ **Nur nachtragen, nie ueberschreiben.** Das Datum sagt, seit wann
        # die Datei liegt; es aendert sich nicht. Wuerde es bei jedem Abgleich
        # neu gesetzt, waere es dasselbe wie ``measured_at`` - und damit
        # wertlos fuer die Frage, was schon lange herumliegt.
        if zeile.added_at is None and wert.added_at is not None:
            zeile.added_at = wert.added_at
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
        zugelegt=zugelegt,
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
    nach_paket: dict[int, int] = {}
    for anfrage in anfragen:
        if anfrage.user_id in admins:
            continue
        stufe = anfrage.tier.value if anfrage.tier else QualityTier.standard.value
        if anfrage.media_type == MediaType.movie:
            nach_film.setdefault((anfrage.tmdb_id, stufe), anfrage.user_id)
        elif anfrage.episodes:
            # Folgen-Pakete beanspruchen nie die Staffel-Zeile - nur ihre
            # eigene ``:r``-Zeile, ueber die Anfrage-Nummer im Schluessel.
            nach_paket[anfrage.id] = anfrage.user_id
        elif anfrage.tvdb_id:
            nach_serie.setdefault(
                (anfrage.tvdb_id, stufe, anfrage.season), anfrage.user_id
            )

    ergebnis: dict[str, int] = {}
    for wert in werte:
        if wert.media_type == MediaType.movie:
            besitzer = nach_film.get((wert.tmdb_id, wert.tier.value))
        elif (paket_nummer := _paket_nummer(wert.key)) is not None:
            besitzer = nach_paket.get(paket_nummer)
        else:
            # Erst die genaue Staffel, dann die Anfrage ueber die ganze Serie.
            besitzer = nach_serie.get(
                (wert.tvdb_id, wert.tier.value, wert.season)
            ) or nach_serie.get((wert.tvdb_id, wert.tier.value, None))
        if besitzer is not None:
            ergebnis[wert.key] = besitzer
    return ergebnis


def _paket_nummer(kennung: str | None) -> int | None:
    """Die Anfrage-Nummer aus einer Paket-Kennung (``...:s2:r17`` -> 17)."""
    if not kennung or ":r" not in kennung:
        return None
    try:
        return int(kennung.rsplit(":r", 1)[1])
    except ValueError:
        return None


def verbuchen(
    db: Session,
    request: MediaRequest,
    eintrag: object,
    paket_bytes: int | None = None,
) -> int:
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
    elif request.episodes and request.tvdb_id:
        # Ein Folgen-Paket bekommt seine eigene Zeile - mit der Summe der
        # eigenen Episodendateien, die der Aufrufer gerade gemessen hat. Ohne
        # Messung startet die Zeile bei null; der stuendliche Abgleich traegt
        # die Groesse nach.
        kennung = schluessel(
            MediaType.tv,
            stufe,
            tvdb_id=request.tvdb_id,
            season=request.season,
            request_id=request.id,
        )
        if kennung is not None:
            gemessen[kennung] = _Gemessen(
                key=kennung,
                media_type=MediaType.tv,
                tier=stufe,
                tmdb_id=request.tmdb_id,
                tvdb_id=request.tvdb_id,
                season=request.season,
                title=request.title,
                size_bytes=paket_bytes or 0,
                path=str(getattr(eintrag, "path", "") or ""),
                arr_id=getattr(eintrag, "arr_id", None),
            )
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
    logger.info(
        "Household: item %s %r (%s bytes) taken over from user %s - no file touched",
        posten.id,
        posten.title,
        posten.size_bytes,
        vorher,
    )
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


# Wieviele Abgaben ein Nutzer gleichzeitig offen haben darf.
#
# Ohne Deckel kann jemand alles anfragen und alles ans Haus durchreichen; der
# Administrator ertrinkt in Entscheidungen, und das Kontingent waere eine
# Formalitaet. Zehn sind genug, um aufzuraeumen, und wenig genug, dass eine
# Warteschlange ueberschaubar bleibt.
HOECHSTENS_OFFEN = 10


class Abgabefehler(Exception):
    """Fachlicher Fehler beim Abgeben - mit lesbarer Meldung und HTTP-Code."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def abgeben(
    db: Session, posten_id: int, user: "User", wunsch: StorageWish | None = None
) -> Posten:
    """"Brauche ich nicht mehr" - der Nutzer gibt einen Posten ab.

    ⚠️ **Es passiert dabei genau nichts an der Datei.** Der Posten wechselt auf
    ``pending`` und wartet auf die Entscheidung eines Administrators. Bis
    dahin **zaehlt er weiter** gegen das Kontingent - sonst waere Abgeben ein
    Freifahrtschein: Man gaebe alles ab, waere sofort frei und niemand muesste
    je entscheiden.

    ``wunsch`` sagt dem Entscheider, was sich der Abgebende vorstellt -
    einstufig, damit niemand zweimal gefragt werden muss. ``keep`` ("Folgen
    behalten, aber keine neuen mehr") gibt es nur bei Serien-Staffeln: Ein
    Film waechst nicht, dort waere der Wunsch dasselbe wie gar nichts.
    Ohne Angabe gilt ``delete`` - das war bisher der einzige Weg.

    Nur der eigene Posten, und nur ein eigener: Ein fremder ist fuer den
    Aufrufer nicht von einem nicht existierenden zu unterscheiden - deshalb
    beide Male 404 statt 403. Wer eine fremde Nummer durchprobiert, soll daraus
    nicht ablesen koennen, was es gibt.
    """
    posten = db.get(StorageEntry, posten_id)
    if posten is None or posten.user_id != user.id:
        raise Abgabefehler("Diesen Posten gibt es bei dir nicht.", 404)

    if wunsch == StorageWish.keep and posten.season is None:
        raise Abgabefehler(
            "„Behalten, aber nicht mehr folgen“ gibt es nur bei Serien-Staffeln.",
            422,
        )

    if posten.state == StorageState.pending:
        raise Abgabefehler(
            f"„{posten.title}“ wartet bereits auf eine Entscheidung.", 409
        )
    if posten.state != StorageState.owned:
        raise Abgabefehler("Dieser Posten gehoert bereits dem Haus.", 409)

    offen = db.scalar(
        select(func.count(StorageEntry.id)).where(
            StorageEntry.user_id == user.id,
            StorageEntry.state == StorageState.pending,
        )
    ) or 0
    if offen >= HOECHSTENS_OFFEN:
        raise Abgabefehler(
            f"Du hast schon {offen} Abgaben offen. Warte, bis darueber "
            "entschieden ist - sonst geht der Ueberblick verloren.",
            409,
        )

    posten.state = StorageState.pending
    posten.released_at = utcnow()
    posten.release_wish = wunsch or StorageWish.delete
    db.flush()
    logger.info(
        "Handover: %s hands over item %s %r (%s bytes, wish: %s) - awaiting a decision",
        user.username,
        posten.id,
        posten.title,
        posten.size_bytes,
        posten.release_wish.value,
    )
    return _als_posten(posten)


def zuruecknehmen(db: Session, posten_id: int, user: "User") -> Posten:
    """Eine Abgabe zurueckziehen - "doch nicht".

    Kostet nichts und verhindert, dass ein versehentlicher Klick bis zur
    naechsten Entscheidung des Administrators stehen bleibt. Der Posten zaehlte
    ohnehin die ganze Zeit mit; es aendert sich nur, dass er nicht mehr in der
    Warteschlange steht.
    """
    posten = db.get(StorageEntry, posten_id)
    if posten is None or posten.user_id != user.id:
        raise Abgabefehler("Diesen Posten gibt es bei dir nicht.", 404)
    if posten.state != StorageState.pending:
        raise Abgabefehler("Dieser Posten steht gar nicht zur Entscheidung.", 409)

    posten.state = StorageState.owned
    posten.released_at = None
    posten.release_wish = None
    db.flush()
    return _als_posten(posten)


async def entfolgen(db: Session, settings: AppSettings, posten_id: int) -> Posten:
    """Den Behalten-Wunsch ausfuehren: Folgen bleiben, Ueberwachung aus.

    Das dritte Ergebnis einer Abgabe neben "Ins Haus" und "Loeschen" - und das
    einzige, bei dem der Posten **belastet bleibt**: Die Dateien liegen ja
    weiter auf der Platte, und behalten wollte sie der Abgebende ausdruecklich.
    Es aendert sich nur eines: Sonarr laedt keine neuen Folgen dieser Staffel
    mehr nach, die Zahl waechst also nicht weiter.

    Ist die Serie inzwischen gar nicht mehr in Sonarr, gibt es nichts
    stillzulegen - dann ist das Ziel schon erreicht, und der Posten geht
    genauso zurueck auf sein Konto.
    """
    zeile = db.get(StorageEntry, posten_id)
    if zeile is None:
        raise Abgabefehler("Diesen Posten gibt es nicht.", 404)
    if zeile.state != StorageState.pending:
        raise Abgabefehler("Dieser Posten steht gar nicht zur Entscheidung.", 409)
    if zeile.season is None:
        raise Abgabefehler(
            "„Behalten, aber nicht mehr folgen“ gibt es nur bei Serien-Staffeln.",
            409,
        )

    client, arr_id = await _arr_eintrag(settings, zeile)
    if client is not None and arr_id is not None:
        await client.unmonitor_season(arr_id, zeile.season)
        logger.info(
            "Handover decided: %r season %s frozen (arr_id=%s) - stays charged, will not grow",
            zeile.title,
            zeile.season,
            arr_id,
        )
    else:
        logger.info(
            "Handover decided: %r season %s is no longer in Sonarr - nothing to freeze, item "
            "stays charged",
            zeile.title,
            zeile.season,
        )

    zeile.state = StorageState.owned
    zeile.released_at = None
    zeile.release_wish = None
    db.flush()
    return _als_posten(zeile)


def konto_zuruecksetzen(db: Session, user_id: int) -> tuple[int, int]:
    """Ein einzelnes Konto auf null - seine Posten gehen ins Haus.

    Der Ausweg aus dem **Geisterposten**: Wer einen ueber Nexview angefragten
    Titel aus Radarr wirft und die Datei behaelt, bleibt dafuer belastet - und
    Nexview kann ihn nicht mehr entfernen, weil es ausschliesslich ueber
    Radarr/Sonarr loescht. Ohne diesen Knopf sitzt der Betroffene auf einer
    Belastung, die er selbst nicht mehr loswird.

    Wie beim haus-weiten Zuruecksetzen gilt: **keine Datei wird angefasst**,
    die gespeicherte Grenze bleibt stehen, und offene Abgaben dieses Kontos
    sind danach erledigt.
    """
    betroffen = list(
        db.scalars(select(StorageEntry).where(StorageEntry.user_id == user_id))
    )
    bytes_ = sum(zeile.size_bytes for zeile in betroffen)
    for zeile in betroffen:
        zeile.user_id = None
        zeile.state = StorageState.house
        zeile.released_at = None
        zeile.release_wish = None
    if betroffen:
        logger.info(
            "Storage account %s reset: %s item(s) with %s bytes moved to the household",
            user_id,
            len(betroffen),
            bytes_,
        )
    return len(betroffen), bytes_


def konten_zuruecksetzen(db: Session) -> tuple[int, int]:
    """Alle zugerechneten Posten ins Haus - jedes Konto startet bei null.

    ⚠️ **Der Umschalt-Generalpardon.** Laeuft beim Wechsel der Betriebsart
    (Anzahl <-> Speicher), und zwar in **beide** Richtungen - eine Regel statt
    einer Ausnahme. Ohne ihn waere jemand nach dem Einschalten schlagartig
    ueberzogen, wegen einer Historie, von der er nicht wusste, dass sie
    mitzaehlt.

    Was dabei passiert - und was ausdruecklich nicht:

    * Posten mit Besitzer (auch abgegebene, noch unentschiedene) werden
      Hausbestand. Die Abgabe-Warteschlange ist danach leer: Wer abgegeben
      hat, wollte die Belastung loswerden - genau das erledigt der Pardon.
    * **Keine Datei wird angefasst.** Es aendert sich nur, wem etwas
      zugerechnet wird.
    * Gespeicherte Grenzen (``storage_limit_gb``) bleiben stehen und gelten
      wieder, wenn zurueckgeschaltet wird.
    * ``request_id`` bleibt als Herkunftsbeleg erhalten.

    Gibt zurueck, wie viele Posten mit wie vielen Bytes umgebucht wurden -
    dieselben Zahlen, die die Oberflaeche vor dem Umschalten ankuendigt.
    """
    betroffen = list(
        db.scalars(select(StorageEntry).where(StorageEntry.user_id.is_not(None)))
    )
    bytes_ = sum(zeile.size_bytes for zeile in betroffen)
    for zeile in betroffen:
        zeile.user_id = None
        zeile.state = StorageState.house
        zeile.released_at = None
        zeile.release_wish = None
    if betroffen:
        logger.warning(
            "Storage accounts reset: %s item(s) with %s bytes moved to the household",
            len(betroffen),
            bytes_,
        )
    return len(betroffen), bytes_


def umbuchungs_vorschau(db: Session) -> tuple[int, int]:
    """Was ein Ruecksetzen traefe - fuer den Warnhinweis **vor** dem Klick.

    Ein allgemeiner Warnhinweis wird weggeklickt; eine Zahl wird gelesen.
    Deshalb steht im Dialog "X Titel mit zusammen Y GB", nicht "alles".
    """
    zeilen = db.execute(
        select(
            func.count(StorageEntry.id),
            func.coalesce(func.sum(StorageEntry.size_bytes), 0),
        ).where(StorageEntry.user_id.is_not(None))
    ).one()
    return int(zeilen[0]), int(zeilen[1])


def offene_abgaben(db: Session) -> list[tuple[Posten, "User | None"]]:
    """Was wartet auf die Entscheidung des Administrators?

    Das Aelteste zuerst: Wer am laengsten wartet, steht oben. Eine
    Warteschlange nach Groesse sortiert saehe geschaeftiger aus, verdeckt aber
    genau das, worauf es hier ankommt - dass etwas liegen bleibt.
    """
    zeilen = db.scalars(
        select(StorageEntry)
        .where(StorageEntry.state == StorageState.pending)
        .order_by(StorageEntry.released_at)
    ).all()
    return [(_als_posten(zeile), db.get(User, zeile.user_id)) for zeile in zeilen]


# ------------------------------------------------------------------ Loeschen
#
# ⚠️ **Der einzige Teil von Nexview, der Dateien vernichtet.** Alles andere
# hier verschiebt nur, wem etwas zugerechnet wird.


@dataclass(frozen=True)
class Datei:
    """Eine Datei, die beim Loeschen wegfiele."""

    pfad: str
    size_bytes: int


# Welche Stufen geloescht werden duerfen. **Leer heisst: alle.**
#
# Stand hier eine Weile auf ``(QualityTier.uhd,)``, solange nur die
# 4K-Testinstanz drankommen sollte. Aufgehoben, nachdem in **allen** drei
# Instanzen ein Papierkorb eingerichtet war - damit ist eine falsche Loeschung
# sieben Tage lang umkehrbar, und das Sicherheitsnetz liegt dort, wo es
# hingehoert: unter der Datei, nicht in einer Konstanten.
#
# Wieder einzuschraenken ist eine Zeile, falls es je noetig wird.
LOESCHBARE_STUFEN: tuple[QualityTier, ...] = ()


class Loeschfehler(Exception):
    """Fachlicher Fehler beim Loeschen - mit lesbarer Meldung und HTTP-Code."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def _arr_eintrag(settings: AppSettings, zeile: StorageEntry):
    """Wie heisst dieser Posten in Radarr bzw. Sonarr? ``(client, arr_id)``.

    Ueber die Bibliothek und nicht ueber die Anfrage: Ein Posten kann ganz ohne
    Anfrage entstanden sein (Altbestand), und eine zurueckgezogene Anfrage
    darf das Loeschen nicht unmoeglich machen.
    """
    stufe = zeile.tier.value
    if zeile.media_type == MediaType.movie:
        client = library.radarr_client(settings, stufe)
        if client is None or not zeile.tmdb_id:
            return None, None
        eintrag = (await library.movie_library(settings, stufe)).get(zeile.tmdb_id)
        return client, (eintrag.arr_id if eintrag else None)

    client = library.sonarr_client(settings, stufe)
    if client is None or not zeile.tvdb_id:
        return None, None
    nach_tvdb, _ = await library.series_library(settings, stufe)
    eintrag = nach_tvdb.get(zeile.tvdb_id)
    return client, (eintrag.arr_id if eintrag else None)


def _paket_folgen(db: Session, zeile: StorageEntry) -> list[int] | None:
    """Die Folgen eines Paket-Postens - ``None``, wenn es keiner ist.

    Die Folgenliste steht an der Anfrage, nicht am Posten. Ist die Anfrage
    weg (Zeile ohne ``request_id``), laesst sich nicht mehr sagen, welche
    Dateien gemeint waren - dann wird das Loeschen verweigert statt geraten.
    """
    if _paket_nummer(zeile.key) is None:
        return None
    anfrage = db.get(MediaRequest, zeile.request_id) if zeile.request_id else None
    if anfrage is None or not anfrage.episodes:
        raise Loeschfehler(
            "Dieser Folgen-Posten laesst sich keiner Anfrage mehr zuordnen - "
            "seine Dateien nennt nur noch Sonarr selbst.",
            409,
        )
    return list(anfrage.episodes)


async def dateien_fuer(
    db: Session, settings: AppSettings, posten_id: int
) -> list[Datei]:
    """Welche Dateien fielen weg? **Es wird nichts angefasst.**

    Der Probelauf vor dem Loeschen. Der Administrator soll die tatsaechliche
    Liste sehen und nicht eine Zahl: Ein Fehler beim staffelweisen Loeschen
    trifft Folgen, die jemand behalten wollte, und eine Zahl verraet nicht,
    welche.

    Eine **leere** Liste ist eine Aussage: Dann kennt die Instanz den Titel
    nicht (mehr), und Nexview kann ihn nicht loeschen - genau der Fall, den der
    rote Hinweis in den Einstellungen meint.
    """
    zeile = db.get(StorageEntry, posten_id)
    if zeile is None:
        raise Loeschfehler("Diesen Posten gibt es nicht.", 404)

    client, arr_id = await _arr_eintrag(settings, zeile)
    if client is None or arr_id is None:
        return []

    try:
        if zeile.media_type == MediaType.movie:
            filme = await library.movie_library(settings, zeile.tier.value)
            eintrag = filme.get(zeile.tmdb_id or 0)
            if eintrag is None or not eintrag.has_file:
                return []
            return [Datei(pfad=eintrag.path, size_bytes=eintrag.size_bytes)]

        if zeile.season is None:
            raise Loeschfehler(
                "Fuer eine ganze Serie gibt es hier keinen Loeschweg - "
                "abgegeben wird staffelweise.",
                400,
            )
        dateien = await client.episode_files(arr_id, zeile.season)
        folgen_nummern = _paket_folgen(db, zeile)
        if folgen_nummern is not None:
            # Ein Paket-Posten trifft nur die Dateien seiner eigenen Folgen.
            stand = await client.folgen_stand(arr_id)
            staffel = stand.get(zeile.season) or {}
            eigene_dateien = {
                folge.datei_id
                for nummer in folgen_nummern
                if (folge := staffel.get(nummer)) is not None and folge.datei_id
            }
            dateien = [
                datei for datei in dateien if datei.get("id") in eigene_dateien
            ]
        return [
            Datei(
                pfad=str(datei.get("path") or datei.get("relativePath") or ""),
                size_bytes=int(datei.get("size") or 0),
            )
            for datei in dateien
        ]
    except ArrError as fehler:
        raise Loeschfehler(fehler.message, 502) from fehler


async def loeschen(
    db: Session, settings: AppSettings, posten_id: int, *, wer: str = "?"
) -> int:
    """Den Titel wirklich entfernen - **samt Datei**. Gibt die Bytes zurueck.

    ⚠️ **Ab hier gibt es keinen Rueckweg**, ausser dem Papierkorb von Radarr
    bzw. Sonarr. Ist dort keiner eingerichtet, ist die Datei sofort und
    endgueltig weg.

    Geloescht wird ueber die Instanz, nicht am Dateisystem: Nexview sieht es
    gar nicht, und nur so bleiben Bibliothek, Importliste und Papierkorb
    stimmig. ``remove`` nimmt den Titel gleich mit aus der Instanz - bliebe er
    stehen und ueberwacht, laedt Radarr ihn sofort wieder herunter.

    **Der Posten wird sofort entfernt**, nicht erst beim naechsten Abgleich.
    Der raeumt einen Posten erst weg, wenn der Titel weder in Radarr/Sonarr
    **noch** im Media-Server auftaucht - und Plex weiss davon erst nach seinem
    naechsten Durchlauf. Bis dahin bliebe jemand fuer eine Datei belastet, die
    es nicht mehr gibt. Die Regel schuetzt vor *geratener* Loeschung; hier
    haben wir sie selbst durchgefuehrt.
    """
    zeile = db.get(StorageEntry, posten_id)
    if zeile is None:
        raise Loeschfehler("Diesen Posten gibt es nicht.", 404)

    if LOESCHBARE_STUFEN and zeile.tier not in LOESCHBARE_STUFEN:
        raise Loeschfehler(
            "Loeschen ist zurzeit nur in der 4K-Instanz freigeschaltet. "
            "Auf der Standard-Instanz wird nichts entfernt.",
            403,
        )

    client, arr_id = await _arr_eintrag(settings, zeile)
    if client is None or arr_id is None:
        raise Loeschfehler(
            f"„{zeile.title}“ wird nicht mehr von Radarr bzw. Sonarr verwaltet. "
            "Nexview loescht ausschliesslich ueber diese Dienste und kann die "
            "Datei deshalb nicht entfernen - abgeben geht nur an den Hausbestand.",
            409,
        )

    bytes_ = zeile.size_bytes

    # ⚠️ **Vor dem Zugriff protokollieren, nicht danach.**
    #
    # Schlaegt es fehl oder trifft es das Falsche, ist dieser Eintrag der
    # einzige Beleg dafuer, worum Nexview ueberhaupt gebeten hat - mit Instanz,
    # Nummer und der Dateiliste. Ein Protokolleintrag nach getaner Arbeit
    # erzaehlt nur von den Faellen, die geklappt haben.
    dateien = await dateien_fuer(db, settings, posten_id)
    logger.warning(
        "DELETE requested by %s: item %s %r (%s/%s, arr_id=%s, %s bytes) - "
        "%s file(s): %s",
        wer,
        posten_id,
        zeile.title,
        zeile.media_type.value,
        zeile.tier.value,
        arr_id,
        bytes_,
        len(dateien),
        " | ".join(datei.pfad for datei in dateien) or "(none reported)",
    )

    try:
        if zeile.media_type == MediaType.movie:
            await client.remove(arr_id, delete_files=True)
        else:
            # ⚠️ **Erst stilllegen, dann loeschen.** Sonarr sucht fuer jede
            # ueberwachte Staffel nach fehlenden Folgen; bliebe sie an, waere
            # die Staffel beim naechsten Durchlauf wieder da - und der Nutzer,
            # der abgegeben hat, saehe seinen Speicher erneut steigen.
            #
            # Die Reihenfolge ist der Punkt: Scheitert das Stilllegen, liegen
            # die Dateien noch da und nichts ist verloren. Andersherum waeren
            # sie weg **und** kaemen zurueck.
            folgen_nummern = _paket_folgen(db, zeile)
            if folgen_nummern is not None:
                # Ein Paket-Posten: genau die eigenen Folgen stilllegen und
                # nur deren Dateien loeschen - die Staffel gehoert anderen mit.
                stand = await client.folgen_stand(arr_id)
                staffel = stand.get(zeile.season) or {}
                eigene = [
                    folge
                    for nummer in folgen_nummern
                    if (folge := staffel.get(nummer)) is not None
                ]
                if eigene:
                    await client.folgen_schalten(
                        [folge.kennung for folge in eigene], False
                    )
                datei_ids = [folge.datei_id for folge in eigene if folge.datei_id]
                if not datei_ids:
                    raise Loeschfehler(
                        "Sonarr meldet fuer dieses Folgen-Paket keine Dateien.",
                        409,
                    )
                entfernt = await client.delete_episode_files(datei_ids)
                logger.warning(
                    "DELETE: removed %s of %s episode files of the package in season %s",
                    entfernt,
                    len(datei_ids),
                    zeile.season,
                )
            else:
                await client.unmonitor_season(arr_id, zeile.season)
                kennungen = [
                    int(datei["id"])
                    for datei in await client.episode_files(arr_id, zeile.season)
                    if datei.get("id")
                ]
                if not kennungen:
                    raise Loeschfehler(
                        f"Sonarr meldet fuer Staffel {zeile.season} keine Dateien.", 409
                    )
                entfernt = await client.delete_episode_files(kennungen)
                logger.warning(
                    "DELETE: removed %s of %s files of season %s",
                    entfernt,
                    len(kennungen),
                    zeile.season,
                )
    except Loeschfehler:
        raise
    except ArrError as fehler:
        # 404 heisst: dort schon weg - dann ist das Ziel ja erreicht.
        if fehler.status_code != 404:
            logger.error(
                "DELETE failed: item %s %r (arr_id=%s) - %s",
                posten_id,
                zeile.title,
                arr_id,
                fehler.message,
            )
            raise Loeschfehler(fehler.message, 502) from fehler
        logger.warning(
            "DELETE: item %s %r was already gone in the instance (404) - goal reached",
            posten_id,
            zeile.title,
        )

    titel = zeile.title
    # **Die Anfragen zum Posten sofort schliessen**, nicht erst per Abgleich.
    #
    # Ohne das stand die Anfrage weiter auf "geladen", die Staffel galt als
    # belegt und liess sich nie wieder anfragen - der Abgleich haette es zwar
    # irgendwann gerichtet, aber wer gerade geloescht hat, sieht die Folge
    # seiner Entscheidung sofort oder haelt sie fuer wirkungslos.
    geschlossen = _anfragen_schliessen(db, zeile)
    db.delete(zeile)
    db.flush()
    library.invalidate()
    logger.warning(
        "DELETE done: %r removed, %s bytes freed, %s request(s) closed",
        titel,
        bytes_,
        geschlossen,
    )
    return bytes_


# Anfragen in diesen Zustaenden behaupten oder erwarten eine Datei, die es
# nach dem Loeschen nicht mehr gibt (die Staffel ist zudem stillgelegt).
# ``pending_approval`` bleibt bewusst draussen: Das ist eine offene
# Entscheidung, und die trifft weiterhin ein Mensch.
_ZU_SCHLIESSEN = (
    RequestStatus.downloaded,
    RequestStatus.searching,
    RequestStatus.approved,
)


def _anfragen_schliessen(db: Session, zeile: StorageEntry) -> int:
    """Alle laufenden Anfragen zum geloeschten Posten auf "geloescht" setzen."""
    bedingungen = [
        MediaRequest.media_type == zeile.media_type,
        MediaRequest.tier == zeile.tier,
        MediaRequest.status.in_(_ZU_SCHLIESSEN),
    ]
    if zeile.media_type == MediaType.movie:
        bedingungen.append(MediaRequest.tmdb_id == zeile.tmdb_id)
    else:
        bedingungen.append(MediaRequest.tvdb_id == zeile.tvdb_id)
        # Eine Staffel trifft nur ihre eigenen Anfragen; ``season IS NULL``
        # (ganze Serie) bleibt stehen - von der Serie existiert ja noch mehr.
        bedingungen.append(MediaRequest.season == zeile.season)
        paket_nummer = _paket_nummer(zeile.key)
        if paket_nummer is not None:
            # Ein Paket-Posten schliesst genau seine eine Anfrage.
            bedingungen.append(MediaRequest.id == paket_nummer)
        else:
            # Eine Staffel-Zeile trifft keine Folgen-Pakete - deren Dateien
            # haengen an der eigenen ``:r``-Zeile.
            bedingungen.append(MediaRequest.episodes.is_(None))

    getroffen = 0
    for anfrage in db.scalars(select(MediaRequest).where(*bedingungen)):
        anfrage.status = RequestStatus.deleted
        anfrage.completed_at = utcnow()
        getroffen += 1
    return getroffen
