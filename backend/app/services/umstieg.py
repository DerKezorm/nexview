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
from .beschaffung import ARR, NEX, BeschaffungError, Kennt, get_beschaffung
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

    @property
    def mitnehmbar(self) -> bool:
        """Kann der Umstieg diesen Titel mitnehmen?

        Bekannt in der gewählten Fassung - und bei einer Serie zusätzlich mit
        einer TMDB-Nummer aus nexcrate, denn ohne sie ließe sich der
        Speicherschlüssel nicht übersetzen.
        """
        if self.ergebnis != "bekannt":
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

        Zwei Fälle, eine Liste - beide enden gleich (Bauplan 7.3, Schritt 4):
        nexcrate führt den Titel nicht, **oder** es führt ihn, aber ohne die
        TMDB-Nummer, aus der der Speicherschlüssel entsteht.

        ⚠️ **Diese Eigenschaft ist die einzige Definition davon.** Der Router
        stellt dieselbe Frage vor dem Umschalten noch einmal; zwei Fassungen
        der Bedingung liefen nach dem ersten Feinschliff auseinander, und der
        Betreiber bekäme einen Riegel, den er nie gesehen hat.
        """
        return [b for b in self.befunde if b.posten and not b.mitnehmbar]

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
    return Probe(befunde=befunde)


# --------------------------------------------------------------------------
# Schritt 6: die Wanderung


@dataclass
class Wanderung:
    """Was umgeschrieben wurde - Zahl für Zahl, fürs Protokoll."""

    anfragen: int = 0
    posten: int = 0
    posten_schluessel: int = 0
    posten_ohne_uebersetzung: int = 0
    rechte: int = 0
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

    # Offene Anfragen. ⚠️ Erledigte behalten ihre Arr-Kennung: Sie sind
    # Geschichte, und die Fassungszeile bleibt mit ``aktiv=False`` stehen,
    # damit die Anzeige ihren Namen weiter kennt (Bauplan 6.10).
    for anfrage in db.scalars(select(MediaRequest).where(MediaRequest.status.in_(OFFEN))):
        ziel = abbildung.get(anfrage.fassung_kennung)
        if ziel:
            anfrage.fassung_kennung = ziel
            zahlen.anfragen += 1

    # Speicherposten samt Schlüssel.
    for posten in db.scalars(select(StorageEntry)):
        ziel = abbildung.get(posten.fassung_kennung)
        if not ziel:
            continue
        if posten.media_type == MediaType.tv:
            # ⚠️ **Von TVDB auf TMDB.** nexcrate ankert auf TMDB; ohne die
            # Übersetzung passte der Schlüssel zu nichts mehr.
            tmdb = tmdb_je_tvdb.get(posten.tvdb_id or 0) or posten.tmdb_id
            if not tmdb:
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
            # Ohne Ziel gibt es nichts mehr zu erlauben.
            db.delete(recht)
            zahlen.rechte += 1

    for token in db.scalars(select(AuthToken).where(AuthToken.invite_fassung_rechte.is_not(None))):
        roh = token.invite_fassung_rechte or []
        neue = []
        geaendert = False
        for eintrag in roh:
            if not isinstance(eintrag, dict):
                continue
            ziel = abbildung.get(str(eintrag.get("fassung")))
            if str(eintrag.get("fassung")) in abbildung:
                geaendert = True
                if ziel:
                    neue.append({**eintrag, "fassung": ziel})
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


def _paket_nummer(schluessel: str) -> int | None:
    """Die Anfrage-Nummer aus dem Schlüssel eines Folgen-Pakets (``…:r17``)."""
    letztes = str(schluessel).rsplit(":", 1)[-1]
    return int(letztes[1:]) if letztes.startswith("r") and letztes[1:].isdigit() else None


# --------------------------------------------------------------------------
# Schritt 6 und 7: umschalten


@dataclass
class Bericht:
    wanderung: Wanderung
    verlassen: list[str] = field(default_factory=list)
    fassungen: int = 0


async def umschalten(
    db: Session,
    settings: AppSettings,
    abbildung: dict[str, str | None],
    tmdb_je_tvdb: dict[int, int] | None = None,
) -> Bericht:
    """Der eine Schritt, der sich nicht zurücknehmen lässt.

    Reihenfolge mit Bedacht: **erst** der letzte Schreibzugriff auf Arr
    (Webhooks raus, Zugänge löschen), **dann** die Wanderung und das
    Umschalten in einer Transaktion. Umgekehrt stünde die Installation kurz
    auf `nex`, während Nexviews Webhook-Eintrag in Radarr noch ins Leere
    riefe - und ohne Zugang ließe er sich nicht mehr entfernen.
    """
    from . import beschaffung as grenze

    alter_weg = get_beschaffung(settings)
    bericht = Bericht(wanderung=Wanderung())
    bericht.verlassen = await alter_weg.verlassen(db)

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
    return bericht
