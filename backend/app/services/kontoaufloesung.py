"""Konto aufloesen: was mit dem Bestand eines geloeschten Kontos passiert.

Bisher passierte beim Loeschen eines Kontos zweierlei stillschweigend: Die
Posten fielen per Datenbankregel ans Haus, und **laufende Bestellungen liefen
einfach weiter** - eine ueberwachte Staffel lud herrenlos nach, ohne dass sie
je wieder jemandem gehoerte. Beides sind Entscheidungen, die ein Mensch
treffen soll, kein Fremdschluessel.

Der Ablauf, wie er hier umgesetzt ist:

1. **Vorschau**: Der Administrator sieht vor dem Loeschen, was das Konto
   hinterlaesst - fertige Posten, angefangene Staffeln, offene Bestellungen.
2. **Entscheidung je Posten**: ins Haus oder loeschen (Haekchen-Liste, "Alle
   markieren"). Nur im Speicher-Betrieb ueberhaupt vorhanden - im
   Anzahl-Betrieb garantiert der Umschalt-Pardon, dass niemandem etwas
   zugerechnet ist.
3. **Angefangene Staffeln** (Dateien da, aber unfertig, noch kein Posten):
   behalten oder loeschen - und beim Behalten, ob weiter geladen werden soll.
4. **Offene Bestellungen ohne eine einzige Datei**: stornieren und
   stilllegen. Es liegt nichts, worueber man entscheiden koennte.

Die Buchhaltung (``MediaRequest``) stirbt per ``CASCADE`` mit dem Konto -
hier geht es ausschliesslich um die Wirklichkeit in Radarr, Sonarr und auf
der Platte.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    MediaRequest,
    MediaType,
    RequestStatus,
    StorageEntry,
    StorageState,
    User,
)
from . import library, storage
from .arr import ArrError
from .settings_service import AppSettings

logger = logging.getLogger("nexview.kontoaufloesung")

# Bestellungen in diesen Zustaenden erwarten noch etwas von Radarr/Sonarr.
# ``downloaded`` fehlt bewusst: Fertiges ist als Posten gebucht und wird dort
# entschieden. ``deferred``/``failed`` erwarten nichts mehr.
_OFFEN = (
    RequestStatus.pending_approval,
    RequestStatus.approved,
    RequestStatus.searching,
)


class Aufloesungsfehler(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class LaufendeStaffel:
    """Eine angefangene Bestellung, ueber die entschieden werden muss.

    ``season is None`` heisst: die **ganze Serie** wurde bestellt - dann gilt
    die Entscheidung fuer alles, was davon noch nicht als Posten gebucht ist.
    """

    request_id: int
    title: str
    tier: str
    season: int | None
    arr_id: int | None
    # Wie weit sie ist - fuer die Anzeige, nicht fuer die Entscheidung.
    dateien: int = 0
    folgen: int = 0


@dataclass(frozen=True)
class OffeneBestellung:
    """Eine genehmigte Bestellung, von der noch keine einzige Datei da ist.

    ⚠️ **Bis 0.22 war das nur ein Titel in einer Liste, und sie wurde ohne
    Rueckfrage storniert.** Die Begruendung dafuer - "wo keine Datei liegt,
    ist nichts verloren" - stimmt fuer den Speicherplatz, aber nicht fuer die
    Absicht: Jemand hat den Titel gewollt, jemand hat ihn genehmigt, er ist
    unterwegs. Dass der Besteller geht, macht ihn fuer den Haushalt nicht
    uninteressant. Und das Modul haelt sonst ueberall daran fest, dass ein
    Mensch entscheidet - nur hier entschied es allein.
    """

    request_id: int
    title: str
    tier: str
    season: int | None
    arr_id: int | None


@dataclass(frozen=True)
class Vorschau:
    """Was das Konto hinterlaesst - die Grundlage der Entscheidung."""

    # Fertig gebuchte Posten (nur im Speicher-Betrieb vorhanden).
    posten: list[storage.Posten] = field(default_factory=list)
    # Angefangene Staffeln bzw. Serien mit Dateien, aber ohne Posten.
    laufende: list[LaufendeStaffel] = field(default_factory=list)
    # Genehmigte Bestellungen ohne eine einzige Datei.
    offen: list[OffeneBestellung] = field(default_factory=list)


@dataclass(frozen=True)
class Staffelentscheidung:
    request_id: int
    behalten: bool
    # Nur relevant, wenn behalten: laeuft die Ueberwachung weiter?
    weiter: bool = False


async def vorschau(db: Session, settings: AppSettings, user: User) -> Vorschau:
    """Zusammentragen, was das Konto hinterlaesst.

    Liest die Sonarr-Bibliothek je betroffener Stufe **einmal** - dieselbe
    Antwort, die ueberall sonst benutzt wird. Ist eine Instanz nicht
    erreichbar, scheitert die Vorschau als Ganzes: Eine Aufloesung auf Basis
    geratener Zahlen waere schlimmer als eine vertagte.
    """
    posten = list(
        db.scalars(
            select(StorageEntry)
            .where(
                StorageEntry.user_id == user.id,
                StorageEntry.state.in_((StorageState.owned, StorageState.pending)),
            )
            .order_by(StorageEntry.size_bytes.desc())
        )
    )
    offene = list(
        db.scalars(
            select(MediaRequest).where(
                MediaRequest.user_id == user.id,
                MediaRequest.status.in_(_OFFEN),
            )
        )
    )

    def als_offen(anfrage: MediaRequest, stufe: str, arr_id: int | None) -> OffeneBestellung:
        return OffeneBestellung(
            request_id=anfrage.id,
            title=anfrage.title,
            tier=stufe,
            season=anfrage.season,
            arr_id=arr_id,
        )

    laufende: list[LaufendeStaffel] = []
    offen: list[OffeneBestellung] = []
    serien: dict[str, tuple[dict, dict]] = {}
    for anfrage in offene:
        if anfrage.media_type == MediaType.movie:
            # Ein Film mit Datei waere laengst als Posten gebucht - was hier
            # steht, ist eine Bestellung ohne Ergebnis.
            offen.append(als_offen(anfrage, anfrage.tier.value, anfrage.arr_id))
            continue
        stufe = anfrage.tier.value
        if not settings.arr_configured("tv", stufe):
            # Ohne erreichbare Instanz gibt es nichts zu behalten und nichts
            # stillzulegen - die Bestellung steht nur noch in der Buchhaltung.
            offen.append(als_offen(anfrage, stufe, anfrage.arr_id))
            continue
        if stufe not in serien:
            serien[stufe] = await library.series_library(settings, stufe)
        nach_tvdb, nach_titel = serien[stufe]
        eintrag = nach_tvdb.get(anfrage.tvdb_id) if anfrage.tvdb_id else None
        if eintrag is None:
            eintrag = library.treffer_nach_titel(
                nach_titel, anfrage.title, library.jahr_aus(anfrage.release_date)
            )
        if eintrag is None:
            offen.append(als_offen(anfrage, stufe, anfrage.arr_id))
            continue

        staffeln = getattr(eintrag, "staffeln", None) or {}
        if anfrage.season is not None:
            stand = staffeln.get(anfrage.season)
            dateien = stand.dateien if stand else 0
            folgen = stand.folgen if stand else 0
        else:
            # Ganze Serie: alles zaehlt, was **nicht** schon als Posten
            # gebucht ist - die gebuchten Staffeln entscheiden die Haekchen.
            gebucht = {p.season for p in posten if p.tvdb_id == anfrage.tvdb_id}
            dateien = sum(
                s.dateien for nr, s in staffeln.items() if nr not in gebucht
            )
            folgen = sum(s.folgen for nr, s in staffeln.items() if nr not in gebucht)

        if dateien > 0:
            laufende.append(
                LaufendeStaffel(
                    request_id=anfrage.id,
                    title=anfrage.title,
                    tier=stufe,
                    season=anfrage.season,
                    arr_id=eintrag.arr_id,
                    dateien=dateien,
                    folgen=folgen,
                )
            )
        else:
            offen.append(
                OffeneBestellung(
                    request_id=anfrage.id,
                    title=anfrage.title,
                    tier=stufe,
                    season=anfrage.season,
                    arr_id=eintrag.arr_id,
                )
            )

    return Vorschau(
        posten=[storage._als_posten(zeile) for zeile in posten],
        laufende=laufende,
        offen=offen,
    )


async def aufloesen(
    db: Session,
    settings: AppSettings,
    user: User,
    *,
    haus: set[int],
    loeschen: set[int],
    staffeln: list[Staffelentscheidung],
    #: Kennungen offener Bestellungen, die **weiterlaufen** sollen.
    #:
    #: ⚠️ Alles, was nicht darin steht, wird storniert - das war bis 0.22 das
    #: einzige Verhalten. Leer heisst also: wie frueher.
    offen_behalten: set[int] | None = None,
    wer: str,
) -> None:
    """Die Entscheidungen ausfuehren - **vor** dem Loeschen des Kontos.

    ⚠️ **Jeder Posten braucht eine Entscheidung.** Haus- und Loeschmenge
    muessen zusammen exakt die Posten des Kontos ergeben. Das faengt auch das
    Wettrennen ab: Wird zwischen Vorschau und Bestaetigung noch etwas fertig,
    taucht ein unentschiedener Posten auf, und die Aufloesung wird abgelehnt -
    der Administrator sieht den neuen Stand und entscheidet erneut.

    Scheitert ein Loeschvorgang an Radarr/Sonarr, bricht die Aufloesung ab.
    Bereits Erledigtes bleibt erledigt - der zweite Anlauf hat entsprechend
    weniger vor sich. Das Konto selbst loescht der Aufrufer erst danach.
    """
    zustand = await vorschau(db, settings, user)

    ist = {p.id for p in zustand.posten}
    if haus | loeschen != ist or haus & loeschen:
        raise Aufloesungsfehler(
            "Der Bestand hat sich geaendert - bitte die Liste neu laden und "
            "erneut entscheiden.",
            409,
        )
    nach_anfrage = {e.request_id: e for e in staffeln}
    fehlend = [l for l in zustand.laufende if l.request_id not in nach_anfrage]
    if fehlend:
        raise Aufloesungsfehler(
            "Der Bestand hat sich geaendert - bitte die Liste neu laden und "
            "erneut entscheiden.",
            409,
        )

    # 1. Posten: erst die Loeschungen (der riskante Teil), dann das Umbuchen.
    for posten_id in sorted(loeschen):
        await storage.loeschen(db, settings, posten_id, wer=wer)
    for posten_id in sorted(haus):
        zeile = db.get(StorageEntry, posten_id)
        if zeile is not None:
            zeile.user_id = None
            zeile.state = StorageState.house
            zeile.released_at = None
            zeile.release_wish = None

    # 2. Angefangene Staffeln bzw. Serien.
    for laufend in zustand.laufende:
        wahl = nach_anfrage[laufend.request_id]
        client = library.sonarr_client(settings, laufend.tier)
        if client is None or laufend.arr_id is None:
            continue
        if wahl.behalten and wahl.weiter:
            # Laeuft weiter und faellt fertig ans Haus - dieselbe Regel wie
            # bei der Haus-Uebernahme.
            continue
        if laufend.season is not None:
            if not wahl.behalten:
                kennungen = [
                    int(datei["id"])
                    for datei in await client.episode_files(
                        laufend.arr_id, laufend.season
                    )
                    if datei.get("id")
                ]
                await client.unmonitor_season(laufend.arr_id, laufend.season)
                if kennungen:
                    await client.delete_episode_files(kennungen)
            else:
                await client.unmonitor_season(laufend.arr_id, laufend.season)
        else:
            # Ganze Serie: stilllegen deckt "nicht weiter" wie "loeschen" ab -
            # geloescht werden dann zusaetzlich die Dateien der Staffeln, die
            # **nicht** als Posten gebucht waren (die gebuchten haben ihre
            # eigene Entscheidung schon hinter sich).
            await client.serie_stilllegen(laufend.arr_id)
            if not wahl.behalten:
                gebucht = {
                    z.season
                    for z in db.scalars(
                        select(StorageEntry).where(
                            StorageEntry.tvdb_id
                            == db.get(MediaRequest, laufend.request_id).tvdb_id
                        )
                    )
                }
                dateien = await client.get(
                    "/episodefile", {"seriesId": laufend.arr_id}
                ) or []
                kennungen = [
                    int(datei["id"])
                    for datei in dateien
                    if isinstance(datei, dict)
                    and datei.get("id")
                    and datei.get("seasonNumber") not in gebucht
                ]
                if kennungen:
                    await client.delete_episode_files(kennungen)
        logger.warning(
            "Account wind-down %r: %r %s -> %s",
            user.username,
            laufend.title,
            f"season {laufend.season}" if laufend.season is not None else "(series)",
            "kept, frozen" if wahl.behalten else "deleted",
        )

    # 3. Bestellungen ohne Dateien: aus der Instanz nehmen, damit dort nicht
    #    herrenlos weitergesucht wird. Filme fliegen ganz raus (es liegt ja
    #    nichts), leere Staffeln werden stillgelegt.
    #
    #    ⚠️ **Es sei denn, der Administrator will sie behalten.** Dann bleibt
    #    die Bestellung in Radarr bzw. Sonarr stehen und laedt zu Ende; der
    #    Posten faellt danach ans Haus, weil ihm niemand mehr zugerechnet ist.
    #    Dafuer ist hier nichts weiter zu tun als: nichts zu tun.
    behalten_offen = offen_behalten or set()
    offene = list(
        db.scalars(
            select(MediaRequest).where(
                MediaRequest.user_id == user.id,
                MediaRequest.status.in_(_OFFEN),
            )
        )
    )
    entschieden = set(nach_anfrage)
    for anfrage in offene:
        if anfrage.id in entschieden:
            continue
        if anfrage.id in behalten_offen:
            logger.info(
                "Account wind-down %r: keeping open order %r - it will fall to the house",
                user.username,
                anfrage.title,
            )
            continue
        try:
            if anfrage.media_type == MediaType.movie:
                client = library.radarr_client(settings, anfrage.tier.value)
                if client is not None and anfrage.arr_id:
                    # ``delete_files=True`` als Schutznetz: Sollte in der
                    # letzten Sekunde doch eine Datei angekommen sein, wandert
                    # sie in den Papierkorb statt verwaist liegenzubleiben.
                    await client.remove(anfrage.arr_id, delete_files=True)
            else:
                client = library.sonarr_client(settings, anfrage.tier.value)
                if (
                    client is not None
                    and anfrage.arr_id
                    and anfrage.season is not None
                ):
                    await client.unmonitor_season(anfrage.arr_id, anfrage.season)
                elif client is not None and anfrage.arr_id:
                    await client.serie_stilllegen(anfrage.arr_id)
        except ArrError as fehler:
            # 404 heisst: dort schon weg - Ziel erreicht. Alles andere bricht
            # ab, ehe das Konto faelschlich als aufgeraeumt gilt.
            if fehler.status_code != 404:
                raise
        logger.info(
            "Account wind-down %r: cancelled request %r", user.username, anfrage.title
        )

    db.flush()
