"""Der Umstieg von Radarr und Sonarr auf nexcrate (Bauplan Abschnitt 7.3).

⚠️ **Es gibt keinen Rückweg außer der Sicherung.** Der Assistent sagt das
vorher, und Schritt 5 lässt sich nicht überspringen: Erst wenn die
Sicherungsdatei geschrieben ist, geht es weiter.

Die Reihenfolge ist nicht beliebig, jeder Schritt hängt am vorigen:

1. **Vorab** - was sich ändert, und die Zahlen dazu. Laufende Downloads in Arr
   laufen dort zu Ende und sind danach unsichtbar; wer sie sehen will, wartet.
2. **Verbinden** und Standprüfung (7.2).
3. **Abbildung** je Arr-Fassung auf eine nexcrate-Fassung.
4. **Probe** - kennt nexcrate die Titel, an denen etwas hängt? Geprüft wird
   das **Ergebnis**, nicht der Weg: Wie die Titel dorthin gekommen sind, ist
   Sache des Betreibers.
5. **Sicherung.**
6. **Umschalten** - Webhooks aus Arr, Wanderung, Zugänge löschen, Merker für
   das Nachreichen.
7. **Danach** - Bestand ganz lesen, nachreichen, Stand zeigen.

⚠️ **Die Wanderung ist der gefährliche Teil.** Sie schreibt Fassungskennungen
an Anfragen, Posten, Rechten, Einladungen und Regeln um - und bei Serien
zusätzlich den Speicherschlüssel von TVDB auf TMDB. Ein Schlüssel, der nicht
mehr zum Posten passt, heißt: Der Posten ist für Nexview weg, und die
Zurechnung des ganzen Hauses stimmt nicht mehr.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from ..models import (
    ArrLibraryCache,
    AuthToken,
    DownloadHaenger,
    Fassung,
    FassungRecht,
    InstanzStand,
    MediaRequest,
    MediaType,
    Regel,
    RequestStatus,
    StorageEntry,
    utcnow,
)
from . import fassungen as fassungen_dienst
from . import storage
from .beschaffung import ARR, NEX, Abschied, BeschaffungError, Kennt, get_beschaffung
from .settings_service import load_settings, save_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from .settings_service import AppSettings

logger = logging.getLogger("nexview.umstieg")

#: Anfragen, die beim Umschalten noch etwas wollen.
OFFEN = (
    RequestStatus.pending_approval,
    RequestStatus.approved,
    RequestStatus.searching,
    RequestStatus.deferred,
)

#: Wie viele Titel je Aufruf nachgeschlagen werden (N12).
STAPEL = 100


# --------------------------------------------------------------------------
# Schritt 1: die Zahlen vorab


@dataclass
class Vorab:
    """Was der Betreiber wissen muss, bevor er anfängt."""

    #: Downloads, die in Arr gerade laufen. Sie laufen dort zu Ende, aber
    #: Nexview sieht sie danach nicht mehr - deshalb steht die Zahl vorn.
    downloads_laufend: int = 0
    anfragen_offen: int = 0
    posten: int = 0
    instanzen: list[str] = field(default_factory=list)


async def vorab(db: Session, settings: AppSettings) -> Vorab:
    """Die Zahlen für den ersten Schritt. Fehler kosten nur eine Zahl."""
    stand = Vorab(
        anfragen_offen=int(
            db.scalar(
                select(func.count(MediaRequest.id)).where(MediaRequest.status.in_(OFFEN))
            )
            or 0
        ),
        posten=int(db.scalar(select(func.count(StorageEntry.id))) or 0),
        instanzen=[instanz.name for instanz in settings.arr_instanzen()],
    )
    try:
        rundgang = await get_beschaffung(settings).downloads_auffrischen(db)
        stand.downloads_laufend = sum(
            len(abfrage.downloads) for abfrage in rundgang.abfragen if abfrage.erreichbar
        )
    except BeschaffungError as fehler:
        logger.info("Running downloads could not be counted: %s", fehler.code)
    return stand


def nex_sicht(db: Session) -> AppSettings:
    """Die Einstellungen, als gälte schon `nex` - zum **Fragen**, nicht zum Speichern.

    ⚠️ **Der Assistent läuft, solange `arr` gilt.** Er muss trotzdem die neue
    nexcrate befragen können: ob sie taugt (7.2), ob sie die Titel kennt (7.3,
    Schritt 4). `get_beschaffung(settings)` gäbe ihm sonst den alten Weg und
    damit die falsche Antwort - im besten Fall eine leere.

    Gespeichert wird hier nichts. Das tut genau eine Stelle: `umschalten`.
    """
    return replace(load_settings(db, frisch=True), beschaffung=NEX)


# --------------------------------------------------------------------------
# Schritt 3: die Abbildung


def vorschlag(db: Session, nex_fassungen: list[Any]) -> dict[str, str | None]:
    """Je Arr-Fassung eine nexcrate-Fassung - nach Medienart und Klasse.

    ``hd`` zu ``hd``, ``uhd`` zu ``uhd``; findet sich nichts, bleibt es leer.
    **Geraten wird nicht**: Eine 4K-Anfrage auf eine 1080p-Fassung zu legen
    hieße, dem Anfragenden etwas anderes zu geben, als er wollte.
    """
    gefunden: dict[str, str | None] = {}
    for arr in fassungen_dienst.ARR_FASSUNGEN:
        passend = [
            eintrag
            for eintrag in nex_fassungen
            if eintrag.media_type == arr.media_type and eintrag.klasse == arr.klasse
        ]
        gefunden[arr.kennung] = passend[0].kennung if passend else None
    return gefunden


def pruefe_abbildung(
    abbildung: dict[str, str | None], nex_fassungen: list[Any]
) -> list[str]:
    """Was an einer Abbildung nicht stimmt - als Kennungen."""
    bekannt = {eintrag.kennung: eintrag for eintrag in nex_fassungen}
    fehler: list[str] = []
    for arr_kennung, ziel in abbildung.items():
        if arr_kennung not in {f.kennung for f in fassungen_dienst.ARR_FASSUNGEN}:
            fehler.append("umstieg_fassung_unbekannt")
            continue
        if ziel is None:
            continue
        eintrag = bekannt.get(ziel)
        if eintrag is None:
            fehler.append("umstieg_ziel_unbekannt")
            continue
        arr = next(f for f in fassungen_dienst.ARR_FASSUNGEN if f.kennung == arr_kennung)
        if eintrag.media_type != arr.media_type:
            # ⚠️ Eine Serie auf eine Filmfassung abzubilden ergibt einen
            # Posten, den nexcrate nie kennt - und keinen Fehler, bis jemand
            # ihn löschen will.
            fehler.append("umstieg_falsche_medienart")

    # ⚠️ **Die Abbildung muss eineindeutig sein.** Zwei Arr-Fassungen auf
    # dieselbe Fassung zu legen macht aus zwei Fassungen eine - und ein Titel,
    # der in beiden liegt, hat danach zweimal denselben Speicherschlüssel.
    # Genau das brach den ersten Umstieg an einer echten Anlage ab
    # (23.09.2026): 8 Filme lagen in „Radarr FHD" **und** „Radarr-4K".
    #
    # Nicht zu verwechseln mit dem Normalfall: Ein Titel **darf** in beliebig
    # vielen Fassungen liegen. Was er nicht darf, ist zweimal in derselben.
    # Wer für eine Arr-Fassung kein Gegenstück hat, wählt „Keine" - dann
    # bleiben ihre Posten unberührt, statt sich mit fremden zu mischen.
    ziele = [ziel for ziel in abbildung.values() if ziel]
    if len(ziele) != len(set(ziele)):
        fehler.append("umstieg_ziel_doppelt")
    return sorted(set(fehler))


# --------------------------------------------------------------------------
# Schritt 4: die Probe


@dataclass
class Titelbefund:
    media_type: str
    tmdb_id: int
    tvdb_id: int | None
    titel: str
    fassung: str
    #: ``bekannt`` | ``ohne_fassung`` | ``unbekannt``
    ergebnis: str
    #: Nur bei Serien: Was nexcrate als TMDB-Nummer führt. Ohne sie lässt sich
    #: der Speicherschlüssel nicht übersetzen.
    tmdb_aus_nexcrate: int | None = None
    #: Führt nexcrate diesen Titel als Anime? (N44.)
    anime: bool = False
    posten: bool = False
    anfrage: bool = False
    #: ⚠️ **Ein anderer Posten bekäme denselben neuen Speicherschlüssel.** Bei
    #: Serien hängt der Schlüssel im ARR-Betrieb an der TVDB-Nummer, im
    #: NEX-Betrieb an der TMDB-Nummer - was Sonarr als zwei Serien führt, ist
    #: in TMDB oft ein Titel. Beide bleiben dann stehen.
    kollidiert: bool = False
    #: ⚠️ **Die offene Anfrage bleibt bei der alten Fassung**, weil ein Posten
    #: derselben Serie sich nicht nach TMDB übersetzen lässt und stehen bleibt.
    #: Wanderte sie allein, stünde derselbe Titel in zwei Fassungen.
    anfrage_bleibt: bool = False

    @property
    def mitnehmbar(self) -> bool:
        """Kann der Umstieg diesen Titel mitnehmen?

        Bekannt in der gewählten Fassung - und bei einer Serie zusätzlich mit
        einer TMDB-Nummer aus nexcrate, denn ohne sie ließe sich der
        Speicherschlüssel nicht übersetzen.
        """
        if self.ergebnis != "bekannt" or self.kollidiert:
            return False
        return self.media_type != "tv" or bool(self.tmdb_aus_nexcrate)


@dataclass
class Probe:
    befunde: list[Titelbefund] = field(default_factory=list)

    @property
    def bekannt(self) -> list[Titelbefund]:
        return [b for b in self.befunde if b.ergebnis == "bekannt"]

    @property
    def ohne_fassung(self) -> list[Titelbefund]:
        return [b for b in self.befunde if b.ergebnis == "ohne_fassung"]

    @property
    def unbekannt(self) -> list[Titelbefund]:
        return [b for b in self.befunde if b.ergebnis == "unbekannt"]

    @property
    def posten_ohne_gegenstueck(self) -> list[Titelbefund]:
        """Geladene Posten, die der Umstieg nicht mitnehmen kann.

        Drei Fälle, eine Liste - alle enden gleich (Bauplan 7.3, Schritt 4):
        nexcrate führt den Titel nicht; es führt ihn, aber ohne die TMDB-Nummer,
        aus der der Speicherschlüssel entsteht; oder sein neuer Schlüssel wäre
        derselbe wie der eines anderen Postens.
        """
        return [b for b in self.befunde if b.posten and not b.mitnehmbar]

    @property
    def zu_entscheiden(self) -> list[Titelbefund]:
        """Was der Betreiber vor dem Umschalten sehen muss.

        Die Posten ohne Gegenstück und dazu die offenen Anfragen, die mit
        einem solchen Posten stehen bleiben.

        ⚠️ **Diese Eigenschaft ist die einzige Definition davon.** Der Router
        stellt dieselbe Frage vor dem Umschalten noch einmal; zwei Fassungen
        der Bedingung liefen nach dem ersten Feinschliff auseinander, und der
        Betreiber bekäme einen Riegel, den er nie gesehen hat.
        """
        return [
            b for b in self.befunde if (b.posten and not b.mitnehmbar) or b.anfrage_bleibt
        ]

    @property
    def anime_offen(self) -> list[Titelbefund]:
        """Anime-Serien mit offener Anfrage (N44).

        ⚠️ **Gezählt wird, was in nexcrate schon als Anime steht.** Eine Serie,
        die Nexview dort erst anlegt, ist zunächst `standard` - diese Zahl ist
        also eine Untergrenze, kein Versprechen.
        """
        return [b for b in self.befunde if b.anime and b.anfrage]

    def tmdb_je_tvdb(self) -> dict[int, int]:
        """Die Übersetzung für die Speicherschlüssel der Serien."""
        return {
            b.tvdb_id: b.tmdb_aus_nexcrate
            for b in self.befunde
            if b.tvdb_id and b.tmdb_aus_nexcrate
        }


def _kollisionen_vermerken(
    db: Session, abbildung: dict[str, str | None], ergebnis: Probe
) -> None:
    """An jedem Befund vermerken, ob sein Posten mit einem anderen kollidiert.

    Gerechnet wird mit derselben Funktion, die die Wanderung benutzt - eine
    zweite Rechnung liefe nach dem ersten Feinschliff auseinander, und der
    Betreiber bekäme wieder einen Absturz, den ihm niemand angekündigt hat.
    """
    doppelte = _doppelte_schluessel(db, abbildung, ergebnis.tmdb_je_tvdb())
    if not doppelte:
        return
    betroffen = {
        (posten.media_type.value, posten.tmdb_id, posten.fassung_kennung)
        for posten in db.scalars(select(StorageEntry).where(StorageEntry.id.in_(doppelte)))
    }
    for befund in ergebnis.befunde:
        if (befund.media_type, befund.tmdb_id, befund.fassung) in betroffen:
            befund.kollidiert = True


def _stehende_anfragen_vermerken(
    db: Session, abbildung: dict[str, str | None], ergebnis: Probe
) -> None:
    """An jedem Befund vermerken, ob seine Anfrage beim Umschalten stehen bleibt.

    Mit derselben Rechnung wie die Wanderung, aus demselben Grund wie bei den
    Kollisionen.
    """
    _, anfragen = _ohne_uebersetzung(db, abbildung, ergebnis.tmdb_je_tvdb())
    if not anfragen:
        return
    betroffen = {
        (anfrage.media_type.value, anfrage.tmdb_id, anfrage.fassung_kennung)
        for anfrage in db.scalars(select(MediaRequest).where(MediaRequest.id.in_(anfragen)))
    }
    for befund in ergebnis.befunde:
        if (befund.media_type, befund.tmdb_id, befund.fassung) in betroffen:
            befund.anfrage_bleibt = True


def _was_haengt(db: Session) -> list[Titelbefund]:
    """Jeder Titel, an dem eine offene Anfrage oder ein Posten hängt - **je Fassung**.

    ⚠️ **Der Schlüssel trägt die Fassung.** Derselbe Film in 1080p und in 4K
    ist für den Umstieg **zwei** Fälle: Die eine Kennung findet in nexcrate
    ihre Fassung, die andere vielleicht nicht. Wer nach Titel gruppiert,
    verschweigt genau den Fall, der eine Entscheidung braucht - und das an
    beiden Enden, denn ein Speicherposten hängt ohnehin an einer Fassung.
    """
    gefunden: dict[tuple[str, int, str], Titelbefund] = {}
    for anfrage in db.scalars(select(MediaRequest).where(MediaRequest.status.in_(OFFEN))):
        if not anfrage.tmdb_id:
            continue
        schluessel = (anfrage.media_type.value, anfrage.tmdb_id, anfrage.fassung_kennung)
        eintrag = gefunden.get(schluessel)
        if eintrag is None:
            gefunden[schluessel] = Titelbefund(
                media_type=anfrage.media_type.value,
                tmdb_id=anfrage.tmdb_id,
                tvdb_id=anfrage.tvdb_id,
                titel=anfrage.title or "",
                fassung=anfrage.fassung_kennung,
                ergebnis="unbekannt",
                anfrage=True,
            )
        else:
            eintrag.anfrage = True
    for posten in db.scalars(select(StorageEntry)):
        if not posten.tmdb_id:
            continue
        schluessel = (posten.media_type.value, posten.tmdb_id, posten.fassung_kennung)
        eintrag = gefunden.get(schluessel)
        if eintrag is None:
            gefunden[schluessel] = Titelbefund(
                media_type=posten.media_type.value,
                tmdb_id=posten.tmdb_id,
                tvdb_id=posten.tvdb_id,
                titel=posten.title or "",
                fassung=posten.fassung_kennung,
                ergebnis="unbekannt",
                posten=True,
            )
        else:
            eintrag.posten = True
            eintrag.tvdb_id = eintrag.tvdb_id or posten.tvdb_id
    return list(gefunden.values())


async def probe(
    db: Session, settings: AppSettings, abbildung: dict[str, str | None]
) -> Probe:
    """Kennt nexcrate die Titel, an denen etwas hängt? (Bauplan 7.3, Schritt 4)

    ⚠️ **Geprüft wird das Ergebnis, nicht der Weg.** Ob der Betreiber seine
    Bibliothek aus Sonarr übernommen oder von der Platte eingelesen hat, geht
    Nexview nichts an - nur, ob die Titel jetzt dort stehen.

    Serien werden über ``tvdb:`` gefragt, wo Nexview keine passende Antwort
    über TMDB bekommt: nexcrate nimmt beide Quellen und nennt in ``refs``, was
    es selbst führt. Genau daraus entsteht die Übersetzung für die
    Speicherschlüssel.
    """
    befunde = _was_haengt(db)
    if not befunde:
        return Probe()

    kenntnisse = await get_beschaffung(settings).kennt(
        [
            Kennt(
                media_type=befund.media_type,
                tmdb_id=befund.tmdb_id,
                tvdb_id=befund.tvdb_id,
                titel=befund.titel,
            )
            for befund in befunde
        ]
    )
    for befund, kenntnis in zip(befunde, kenntnisse, strict=False):
        if not kenntnis.bekannt:
            befund.ergebnis = "unbekannt"
            continue
        befund.tmdb_aus_nexcrate = kenntnis.tmdb_id
        befund.anime = kenntnis.anime
        ziel = abbildung.get(befund.fassung)
        befund.ergebnis = "bekannt" if ziel and ziel in kenntnis.fassungen else "ohne_fassung"

    # ⚠️ **Erst jetzt, mit allen Übersetzungen in der Hand.** Ob zwei Posten
    # kollidieren, lässt sich nicht je Titel entscheiden - es hängt an dem, was
    # nexcrate für **alle** anderen sagt. Ohne diesen Schritt sah der
    # Assistent nichts und die Wanderung stürzte mitten im Schreiben ab (erster
    # Umstieg an einer echten Anlage, 23.09.2026).
    ergebnis = Probe(befunde=befunde)
    _kollisionen_vermerken(db, abbildung, ergebnis)
    _stehende_anfragen_vermerken(db, abbildung, ergebnis)
    return ergebnis


# --------------------------------------------------------------------------
# Schritt 6: die Wanderung


@dataclass
class Wanderung:
    """Was umgeschrieben wurde - Zahl für Zahl, fürs Protokoll."""

    anfragen: int = 0
    #: Offene Anfragen, die mit ihrem Posten bei der alten Fassung bleiben.
    anfragen_ohne_uebersetzung: int = 0
    posten: int = 0
    posten_schluessel: int = 0
    posten_ohne_uebersetzung: int = 0
    #: Posten, die mit einem anderen denselben neuen Schlüssel bekämen.
    posten_doppelt: int = 0
    rechte: int = 0
    #: Rechte an Konten und offenen Einladungen, deren Fassung auf „Keine"
    #: zeigt. Sie entfallen ersatzlos - der Betreiber soll das wissen.
    rechte_entfallen: int = 0
    einladungen: int = 0
    regeln: int = 0
    zeilen_entfernt: int = 0


def _regel_werte(regel: Regel, abbildung: dict[str, str | None]) -> bool:
    """Kennungen in den Bedingungen einer Regel umschreiben.

    ⚠️ ``hd`` und ``uhd`` bleiben stehen - sie sind **Klassen**, keine
    Fassungen, und gelten in beiden Betriebsarten (Abweichung 2 aus Scheibe 1).
    Umgeschrieben wird nur, was eine Fassung beim Namen nennt.
    """
    geaendert = False
    neue: list[Any] = []
    for bedingung in regel.bedingungen or []:
        if not isinstance(bedingung, dict) or "werte" not in bedingung:
            neue.append(bedingung)
            continue
        werte = bedingung.get("werte")
        if not isinstance(werte, list):
            neue.append(bedingung)
            continue
        ersetzt = []
        for wert in werte:
            ziel = abbildung.get(str(wert))
            if str(wert) in abbildung:
                geaendert = True
                if ziel:
                    ersetzt.append(ziel)
                # Ohne Ziel faellt der Wert heraus - eine Regel auf eine
                # Fassung, die es nicht mehr gibt, traefe sonst nie wieder.
            else:
                ersetzt.append(wert)
        neue.append({**bedingung, "werte": ersetzt})
    if geaendert:
        regel.bedingungen = neue
    return geaendert


def wandern(
    db: Session, abbildung: dict[str, str | None], tmdb_je_tvdb: dict[int, int]
) -> Wanderung:
    """Alles, was eine Arr-Kennung trägt, auf die neue Fassung umschreiben.

    Kein ``commit``: Der Aufrufer schreibt alles in **einer** Transaktion,
    zusammen mit dem Umschalten selbst. Ein halber Umstieg wäre schlimmer als
    gar keiner.
    """
    zahlen = Wanderung()
    # Gezählt wird vorher, mit derselben Rechnung wie in der Probe.
    zahlen.rechte_entfallen = rechte_entfallen(db, abbildung)
    posten_bleiben, anfragen_bleiben = _ohne_uebersetzung(db, abbildung, tmdb_je_tvdb)

    # ⚠️ **Beim Umstieg werden die alten Rechte übertragen** (Bauplan, Entscheidung
    # vom 22.09.2026) - „offen für alle" ist eins davon. Eine neue Fassung aus
    # nexcrate ist zu; blieb sie es hier, bekäme jedes gewöhnliche Konto nach
    # dem Umschalten 403, bis der Betreiber es Konto für Konto freigibt.
    for arr_kennung, ziel in abbildung.items():
        zielzeile = db.get(Fassung, ziel) if ziel else None
        if zielzeile is not None:
            zielzeile.offen_fuer_alle = fassungen_dienst.offen_fuer_alle(db, arr_kennung)

    # Offene Anfragen. ⚠️ Erledigte behalten ihre Arr-Kennung: Sie sind
    # Geschichte, und die Fassungszeile bleibt mit ``aktiv=False`` stehen,
    # damit die Anzeige ihren Namen weiter kennt (Bauplan 6.10).
    for anfrage in db.scalars(select(MediaRequest).where(MediaRequest.status.in_(OFFEN))):
        ziel = abbildung.get(anfrage.fassung_kennung)
        if not ziel:
            continue
        if anfrage.id in anfragen_bleiben:
            zahlen.anfragen_ohne_uebersetzung += 1
            continue
        anfrage.fassung_kennung = ziel
        zahlen.anfragen += 1

    # ⚠️ **Erst rechnen, dann schreiben.** Zwei Posten können denselben neuen
    # Schlüssel bekommen: Im ARR-Betrieb hängt eine Serie an ihrer TVDB-Nummer,
    # im NEX-Betrieb an der TMDB-Nummer - und was Sonarr als zwei Serien führt,
    # ist in TMDB oft ein Titel (Anime, getrennt geführte Staffel-Reihen). Beim
    # ersten Umstieg an einer echten Anlage hat genau das die ganze Wanderung
    # zum Absturz gebracht, mitten im Schreiben.
    doppelte = _doppelte_schluessel(db, abbildung, tmdb_je_tvdb)

    # Speicherposten samt Schlüssel.
    for posten in db.scalars(select(StorageEntry)):
        ziel = abbildung.get(posten.fassung_kennung)
        if not ziel:
            continue
        if posten.id in doppelte:
            # Unberührt lassen, wie einen ohne Übersetzung: Lieber ein Posten
            # unter seiner alten Fassung als zwei, von denen einer verschwindet.
            zahlen.posten_doppelt += 1
            continue
        if posten.media_type == MediaType.tv:
            # ⚠️ **Von TVDB auf TMDB.** nexcrate ankert auf TMDB; ohne die
            # Übersetzung passte der Schlüssel zu nichts mehr.
            tmdb = tmdb_je_tvdb.get(posten.tvdb_id or 0) or posten.tmdb_id
            if posten.id in posten_bleiben:
                # ⚠️ **Dann bleibt der Posten ganz, wie er ist** - auch seine
                # Fassung. Ihm die neue Kennung zu geben und den alten
                # Schlüssel zu lassen wäre das Schlimmste von beidem: Der
                # Schlüssel entsteht aus der Herkunft der Fassung, also
                # rechnete jede spätere Runde einen anderen aus, und der
                # Posten wäre für Nexview weg. Die Arr-Fassung steht als
                # stillgelegte Zeile weiter da; unter ihr bleibt er zählbar.
                zahlen.posten_ohne_uebersetzung += 1
                continue
            posten.tmdb_id = tmdb
        posten.fassung_kennung = ziel
        zahlen.posten += 1
        neu = storage.schluessel(
            posten.media_type,
            ziel,
            tmdb_id=posten.tmdb_id,
            tvdb_id=posten.tvdb_id,
            season=posten.season,
            request_id=_paket_nummer(posten.key),
        )
        if neu and neu != posten.key:
            posten.key = neu
            zahlen.posten_schluessel += 1

    for recht in db.scalars(select(FassungRecht)):
        ziel = abbildung.get(recht.fassung_kennung)
        if ziel:
            recht.fassung_kennung = ziel
            zahlen.rechte += 1
        elif recht.fassung_kennung in abbildung:
            # Ohne Ziel gibt es nichts mehr zu erlauben. Gezählt ist es oben,
            # unter ``rechte_entfallen``.
            db.delete(recht)

    for token in db.scalars(select(AuthToken).where(AuthToken.invite_fassung_rechte.is_not(None))):
        roh = token.invite_fassung_rechte or []
        neue = []
        geaendert = False
        for eintrag in roh:
            if not isinstance(eintrag, dict):
                continue
            # ⚠️ **Der Schlüssel heißt ``kennung``**, wie ``AuthToken`` ihn
            # schreibt. Hier stand einmal ``fassung``, und damit blieb jede
            # Einladung bei ihrer Arr-Kennung stehen.
            kennung = str(eintrag.get("kennung"))
            ziel = abbildung.get(kennung)
            if kennung in abbildung:
                geaendert = True
                if ziel:
                    neue.append({**eintrag, "kennung": ziel})
            else:
                neue.append(eintrag)
        if geaendert:
            # ⚠️ Immer eine **neue** Liste zuweisen: SQLAlchemy bemerkt eine
            # Änderung *im* JSON-Wert nicht.
            token.invite_fassung_rechte = neue
            zahlen.einladungen += 1

    for regel in db.scalars(select(Regel)):
        if _regel_werte(regel, abbildung):
            zahlen.regeln += 1

    # Was nur zu den Arr-Instanzen gehört, verschwindet: Es zeigte sonst für
    # immer auf Dienste, die Nexview nicht mehr kennt.
    arr_kennungen = [f.kennung for f in fassungen_dienst.ARR_FASSUNGEN]
    for tabelle in (InstanzStand, DownloadHaenger, ArrLibraryCache):
        spalte = getattr(tabelle, "kennung", None)
        if spalte is None:
            continue
        for zeile in db.scalars(select(tabelle).where(spalte.in_(arr_kennungen))):
            db.delete(zeile)
            zahlen.zeilen_entfernt += 1

    # Die Arr-Fassungen bleiben als Zeile stehen, aber stillgelegt.
    for zeile in db.scalars(select(Fassung).where(Fassung.quelle == ARR)):
        zeile.aktiv = False
        zeile.bereit = False
        zeile.verschwunden_am = zeile.verschwunden_am or utcnow()

    db.flush()
    return zahlen


def _doppelte_schluessel(
    db: Session, abbildung: dict[str, str | None], tmdb_je_tvdb: dict[int, int]
) -> set[int]:
    """Welche Posten bekämen einen Schlüssel, den es dann zweimal gäbe?

    Gerechnet wird über **alle** Posten, auch die, die stehen bleiben: Ein
    wandernder Posten kann auch mit einem kollidieren, der gar nicht wandert.

    ⚠️ **Zurück kommen die Nummern der Posten, die man liegen lässt** - bei
    einer Kollision alle Beteiligten. Einen davon willkürlich zu nehmen hieße,
    dem Betreiber die Wahl abzunehmen, welcher seiner beiden Titel künftig
    gezählt wird.
    """
    nach_schluessel: dict[str, list[int]] = {}
    for posten in db.scalars(select(StorageEntry)):
        ziel = abbildung.get(posten.fassung_kennung)
        if not ziel:
            # Bleibt, wie er ist - sein heutiger Schlüssel zählt trotzdem mit.
            nach_schluessel.setdefault(posten.key, []).append(posten.id)
            continue
        tmdb = posten.tmdb_id
        if posten.media_type == MediaType.tv:
            tmdb = tmdb_je_tvdb.get(posten.tvdb_id or 0) or posten.tmdb_id
            if not tmdb:
                nach_schluessel.setdefault(posten.key, []).append(posten.id)
                continue
        neu = storage.schluessel(
            posten.media_type,
            ziel,
            tmdb_id=tmdb,
            tvdb_id=posten.tvdb_id,
            season=posten.season,
            request_id=_paket_nummer(posten.key),
        )
        nach_schluessel.setdefault(neu or posten.key, []).append(posten.id)
    return {
        nummer
        for nummern in nach_schluessel.values()
        if len(nummern) > 1
        for nummer in nummern
    }


def _ohne_uebersetzung(
    db: Session, abbildung: dict[str, str | None], tmdb_je_tvdb: dict[int, int]
) -> tuple[set[int], set[int]]:
    """Welche Serien bleiben stehen, weil sich ihr Titel nicht nach TMDB übersetzen lässt?

    Zurück kommen die Nummern der Posten und der offenen Anfragen.

    ⚠️ **Eine Anfrage bleibt mit ihrem Posten stehen**, nicht aus eigenem Grund:
    Sie trägt immer eine TMDB-Nummer (``media_requests.tmdb_id`` ist NOT NULL)
    und käme allein ohne Weiteres hinüber. Aber wenn ein Posten derselben
    Serie in derselben Fassung stehen bleibt, wanderte sonst nur die halbe
    Serie. Eine Anfrage auf eine Serie ganz ohne Posten wandert dagegen immer:
    Was nexcrate noch nicht kennt, wird nach dem Umschalten gestellt.

    Die Wanderung und die Probe rechnen beide hiermit - zwei Rechnungen liefen
    nach dem ersten Feinschliff auseinander.
    """
    posten_bleiben: set[int] = set()
    serien: set[tuple[str, int]] = set()
    for posten in db.scalars(select(StorageEntry).where(StorageEntry.media_type == MediaType.tv)):
        if not abbildung.get(posten.fassung_kennung):
            continue
        if tmdb_je_tvdb.get(posten.tvdb_id or 0) or posten.tmdb_id:
            continue
        posten_bleiben.add(posten.id)
        if posten.tvdb_id:
            serien.add((posten.fassung_kennung, posten.tvdb_id))

    anfragen_bleiben: set[int] = set()
    if serien:
        for anfrage in db.scalars(
            select(MediaRequest).where(
                MediaRequest.status.in_(OFFEN), MediaRequest.media_type == MediaType.tv
            )
        ):
            if (anfrage.fassung_kennung, anfrage.tvdb_id) in serien:
                anfragen_bleiben.add(anfrage.id)
    return posten_bleiben, anfragen_bleiben


def _entfaellt(kennung: str, abbildung: dict[str, str | None]) -> bool:
    return kennung in abbildung and not abbildung[kennung]


def rechte_entfallen(db: Session, abbildung: dict[str, str | None]) -> int:
    """Wie viele Rechte ersatzlos entfallen, weil ihre Fassung auf „Keine" zeigt.

    Gezählt werden Rechte an Konten und in Einladungen, die noch niemand
    eingelöst hat - eine eingelöste Einladung vergibt nichts mehr. Ein
    Eintrag, der weder Anfragen noch Freigabe erlaubt, ist kein Recht.

    Die Probe fragt es vorher, die Wanderung zählt damit - dieselbe Rechnung.
    """
    zahl = sum(
        1
        for recht in db.scalars(select(FassungRecht))
        if _entfaellt(recht.fassung_kennung, abbildung) and (recht.anfragen or recht.auto_freigabe)
    )
    for token in db.scalars(
        select(AuthToken).where(
            AuthToken.invite_fassung_rechte.is_not(None), AuthToken.used_at.is_(None)
        )
    ):
        zahl += sum(
            1
            for eintrag in token.invite_fassung_rechte or []
            if isinstance(eintrag, dict)
            and _entfaellt(str(eintrag.get("kennung")), abbildung)
            and (eintrag.get("anfragen") or eintrag.get("auto_freigabe"))
        )
    return zahl


@dataclass
class Mitzunehmen:
    """Was die Wanderung umschreiben müsste, weil es eine Arr-Kennung trägt.

    ⚠️ **Dieselben Dinge, die ``wandern`` anfasst** - und nur die. Die
    Einstellungsseite sperrt damit den Wechsel von ``arr`` auf ``nex`` am
    Assistenten vorbei (``routers/settings.py``): Ohne Wanderung stünden
    danach alle diese Zeilen bei einer Fassung, die es nicht mehr gibt, und der
    Assistent antwortete 409, weil schon ``nex`` gilt. Einen Reparaturweg gibt
    es dann nicht. ``test_die_sperre_zaehlt_was_die_wanderung_umschreibt``
    hält beide beieinander.

    Nicht gezählt wird, was auch die Wanderung liegen lässt oder nur aufräumt:
    erledigte Anfragen und eingelöste Einladungen (Geschichte), die Zeilen der
    Arr-Instanzen (Zwischenstände, keine Daten). Ohne alles hier bleibt der
    Wechsel frei, sonst käme keine Ersteinrichtung mit nexcrate durch.
    """

    #: Offene Anfragen.
    anfragen: int = 0
    posten: int = 0
    #: Rechte an Konten.
    rechte: int = 0
    #: Offene Einladungen, die ein Recht an einer Arr-Fassung vergeben.
    einladungen: int = 0
    regeln: int = 0
    #: Arr-Fassungen, die der Betreiber über ihre Vorgabe hinaus für alle
    #: geöffnet hat. Die Wanderung trägt das auf die neue Fassung; ohne sie
    #: bekäme jedes gewöhnliche Konto dort 403.
    geoeffnet: int = 0

    def __bool__(self) -> bool:
        return any(vars(self).values())


def _nennt_arr_fassung(regel: Regel, kennungen: frozenset[str]) -> bool:
    """Nennt eine Bedingung dieser Regel eine Arr-Fassung? Wie ``_regel_werte``."""
    for bedingung in regel.bedingungen or []:
        if not isinstance(bedingung, dict):
            continue
        werte = bedingung.get("werte")
        if isinstance(werte, list) and any(str(wert) in kennungen for wert in werte):
            return True
    return False


def mitzunehmen(db: Session) -> Mitzunehmen:
    """Zählt, was der Umstieg mitnehmen müsste. Schreibt nichts."""
    arr = frozenset(fassungen_dienst.ARR_KENNUNGEN)
    zahlen = Mitzunehmen(
        anfragen=int(
            db.scalar(
                select(func.count(MediaRequest.id)).where(
                    MediaRequest.status.in_(OFFEN), MediaRequest.fassung_kennung.in_(arr)
                )
            )
            or 0
        ),
        posten=int(db.scalar(select(func.count(StorageEntry.id)).where(StorageEntry.fassung_kennung.in_(arr))) or 0),
        rechte=sum(
            1
            for recht in db.scalars(select(FassungRecht).where(FassungRecht.fassung_kennung.in_(arr)))
            if recht.anfragen or recht.auto_freigabe
        ),
        regeln=sum(1 for regel in db.scalars(select(Regel)) if _nennt_arr_fassung(regel, arr)),
        geoeffnet=sum(
            1
            for kennung in arr
            if kennung not in fassungen_dienst.ARR_OFFEN and fassungen_dienst.offen_fuer_alle(db, kennung)
        ),
    )
    for token in db.scalars(
        select(AuthToken).where(AuthToken.invite_fassung_rechte.is_not(None), AuthToken.used_at.is_(None))
    ):
        if any(
            isinstance(eintrag, dict)
            and str(eintrag.get("kennung")) in arr
            and (eintrag.get("anfragen") or eintrag.get("auto_freigabe"))
            for eintrag in token.invite_fassung_rechte or []
        ):
            zahlen.einladungen += 1
    return zahlen


def _paket_nummer(schluessel: str) -> int | None:
    """Die Anfrage-Nummer aus dem Schlüssel eines Folgen-Pakets (``…:r17``)."""
    letztes = str(schluessel).rsplit(":", 1)[-1]
    return int(letztes[1:]) if letztes.startswith("r") and letztes[1:].isdigit() else None


# --------------------------------------------------------------------------
# Schritt 6 und 7: umschalten


@dataclass
class Bericht:
    wanderung: Wanderung
    verlassen: list[Abschied] = field(default_factory=list)
    fassungen: int = 0


async def umschalten(
    db: Session,
    settings: AppSettings,
    abbildung: dict[str, str | None],
    tmdb_je_tvdb: dict[int, int] | None = None,
) -> Bericht:
    """Der eine Schritt, der sich nicht zurücknehmen lässt.

    ⚠️ **Zuerst die Wanderung, erst danach Arr verlassen.** Umgekehrt war es
    bis zum 23.09.2026, mit dieser Begründung: Sonst stünde die Installation
    kurz auf `nex`, während Nexviews Webhook-Eintrag in Radarr noch ins Leere
    riefe - und ohne Zugang ließe er sich nicht mehr entfernen.

    Beim ersten Umstieg an einer echten Anlage hat sich gezeigt, dass das die
    falsche Sorge war. Die Wanderung scheiterte (zwei Serien bekamen denselben
    Speicherschlüssel), und zurück blieb eine Installation **ohne Arr-Zugänge
    und ohne nexcrate** - sie konnte gar nichts mehr. Ein Webhook, der ein paar
    Sekunden ins Leere ruft, ist dagegen nichts: Arr meldet ihn als krank und
    vergisst es wieder.

    Jetzt gilt: Was schiefgehen kann, geschieht in **einer** Transaktion und
    lässt bei einem Fehler alles, wie es war. Der Schreibzugriff auf Arr kommt
    danach, und ein Fehler dort hält nichts mehr auf - er steht nur im Bericht.
    """
    from . import beschaffung as grenze

    alter_weg = get_beschaffung(settings)
    bericht = Bericht(wanderung=Wanderung())

    # ⚠️ **Die Fassungen müssen stehen, bevor etwas auf sie zeigt.** Gelesen
    # wird mit einer Sicht, in der schon `nex` gilt - die Einstellung selbst
    # wechselt erst unten, in derselben Transaktion wie die Wanderung.
    frisch = nex_sicht(db)
    neuer_weg = get_beschaffung(frisch)
    await grenze.fassungen_auffrischen(db, frisch)
    neuer_weg.fassungen_abgleichen(db)
    bericht.fassungen = len(neuer_weg.fassungen())

    bericht.wanderung = wandern(db, abbildung, tmdb_je_tvdb or {})
    save_settings(
        db,
        {
            "beschaffung": NEX,
            "beschaffung_gewechselt_am": utcnow().isoformat(),
        },
        commit=False,
    )
    db.commit()
    logger.info(
        "Switched to nexcrate: %d request(s), %d storage entry/entries, %d right(s) moved",
        bericht.wanderung.anfragen,
        bericht.wanderung.posten,
        bericht.wanderung.rechte,
    )

    # ⚠️ **Ab hier ist der Umstieg vollzogen.** Was jetzt noch schiefgeht, darf
    # ihn nicht mehr zurücknehmen: Die Installation läuft bereits über den
    # neuen Weg. Ein stummes Radarr bedeutet nur, dass sein Webhook-Eintrag
    # stehen bleibt - der Bericht sagt es, und der Betreiber nimmt ihn von Hand
    # heraus.
    try:
        bericht.verlassen = await alter_weg.verlassen(db)
    except Exception:  # noqa: BLE001 - der Umstieg steht schon
        logger.exception("Switched, but the old way could not be left cleanly")
        bericht.verlassen = [Abschied("zugang_blieb")]
    return bericht
