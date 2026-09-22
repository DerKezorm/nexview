"""Die Automatik fuer haengende Downloads - ab Werk aus, je Grund eine Regel.

⚠️ **Drei Riegel, und keiner ist verhandelbar.**

* **Ab Werk aus**, und jede Regel einzeln. Nexview entfernt hier Downloads
  samt Daten im Download-Programm; das darf nie passieren, nur weil jemand ein
  Update eingespielt hat.
* **Nur, was ``download_gruende`` fuer den Grund erlaubt.** Nie ein manueller
  Import - warum, steht dort.
* **Hoechstens ``OBERGRENZE`` Aktionen je Film bzw. Folge in ``FENSTER``.**
  Sonst entfernt die Automatik denselben Film jede Runde, sucht neu, bekommt
  das naechste Sample und entfernt wieder. Ist die Grenze erreicht, hoert sie
  auf und sagt es den Administratoren, einmal.

Unabhaengig von der Automatik meldet sich ein Film oder eine Folge, die in einer
Woche ``WIEDERHOLT_AB`` Mal haengen geblieben ist: Dann liegt es nicht am
einzelnen Release, sondern an etwas Gemeinsamem - Pfad, Rechte, Profil, Indexer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..models import DownloadHaenger, DownloadVerlauf, NotificationType, Setting
from . import beschaffung, notify
from .beschaffung import AUTOMATISCH_MOEGLICH, Aktion, BeschaffungError, DownloadFehler, get_beschaffung
from .settings_service import AppSettings

logger = logging.getLogger("nexview.downloads")

#: Wo die Regeln liegen (``settings``-Tabelle, als JSON).
SCHLUESSEL = "download_automatik"

#: Automatische Aktionen je Film bzw. Folge im Fenster - danach ist Schluss.
OBERGRENZE = 2
FENSTER = timedelta(hours=24)

#: Ab so vielen Haengern in einer Woche meldet sich ein Film oder eine Folge.
WIEDERHOLT_AB = 3
WIEDERHOLT_FENSTER = timedelta(days=7)

#: Was als automatische Aktion zaehlt.
AKTIONSARTEN = frozenset(aktion.value for aktion in AUTOMATISCH_MOEGLICH)


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class Einstellung:
    an: bool
    #: Grund-Kennung -> Aktion. Was nicht drinsteht, bleibt liegen.
    regeln: dict[str, str]


def _erlaubt(kennung: str) -> set[str]:
    grund = beschaffung.download_gruende().get(kennung)
    return {aktion.value for aktion in grund.automatik} if grund is not None else set()


def lesen(db: Session) -> Einstellung:
    """Die gespeicherten Regeln - nachsichtig gelesen.

    Was nicht (mehr) erlaubt ist, faellt still heraus. Aendert ein Update, was
    ein Grund darf, soll keine alte Regel weiterlaufen, die es so nie gab.
    """
    zeile = db.get(Setting, SCHLUESSEL)
    daten: dict = {}
    if zeile is not None and zeile.value:
        try:
            geladen = json.loads(zeile.value)
        except ValueError:
            geladen = {}
        if isinstance(geladen, dict):
            daten = geladen
    regeln: dict[str, str] = {}
    roh = daten.get("regeln")
    if isinstance(roh, dict):
        for kennung, aktion in roh.items():
            if isinstance(aktion, str) and aktion in _erlaubt(kennung):
                regeln[kennung] = aktion
    return Einstellung(an=daten.get("an") is True, regeln=regeln)


def schreiben(db: Session, *, an: bool, regeln: dict[str, str | None]) -> Einstellung:
    sauber: dict[str, str] = {}
    for kennung, aktion in regeln.items():
        if not aktion:
            continue
        if aktion not in _erlaubt(kennung):
            raise DownloadFehler(
                "Diese Aktion darf die Automatik bei diesem Grund nicht ausführen.",
                code="download_automation_not_allowed",
                status_code=422,
            )
        sauber[kennung] = aktion
    zeile = db.get(Setting, SCHLUESSEL)
    if zeile is None:
        zeile = Setting(key=SCHLUESSEL)
        db.add(zeile)
    zeile.value = json.dumps({"an": an, "regeln": sauber}, sort_keys=True)
    db.commit()
    logger.info("Download automation %s with %d rule(s)", "on" if an else "off", len(sauber))
    return lesen(db)


def _derselbe_titel(zeile: DownloadHaenger):
    """Die Vorauswahl im Verlauf: Instanz und Film bzw. Serie.

    Ohne Zuordnung bleibt nur der Download selbst. Welche Folge gemeint ist,
    entscheidet ``_dieselbe_folge``.
    """
    if zeile.arr_id is not None:
        return and_(DownloadVerlauf.kennung == zeile.kennung, DownloadVerlauf.arr_id == zeile.arr_id)
    return and_(
        DownloadVerlauf.kennung == zeile.kennung,
        DownloadVerlauf.download_id == zeile.download_id,
    )


def _dieselbe_folge(zeile: DownloadHaenger, eintrag: DownloadVerlauf) -> bool:
    """Bei einer Serie zaehlt dieselbe Folge, nicht dieselbe Serie.

    ⚠️ **Am Pruefstand gefunden (13.09.2026).** Drei Downloads einer Serie
    hingen zugleich, zwei Einzelfolgen und ein Staffelpaket. Gezaehlt je Serie
    kam beim ersten Rundgang "haengt immer wieder", und die Obergrenze der
    Automatik waere nach zwei Folgen fuer die ganze Serie verbraucht gewesen.
    Ein Staffelpaket betrifft jede seiner Folgen.
    """
    if zeile.media_type != "tv" or zeile.arr_id is None:
        return True
    eigene = set(zeile.folgen_ids or [])
    andere = set(eintrag.folgen_ids or [])
    if not eigene or not andere:
        # Ohne Folgen laesst sich nichts vergleichen: nur Gleiches zu Gleichem.
        return not eigene and not andere
    return not eigene.isdisjoint(andere)


def _anzahl(
    db: Session,
    zeile: DownloadHaenger,
    arten: set[str] | frozenset[str],
    seit: datetime,
    *,
    nur_automatisch: bool = False,
) -> int:
    abfrage = select(DownloadVerlauf).where(
        _derselbe_titel(zeile), DownloadVerlauf.was.in_(arten), DownloadVerlauf.am >= seit
    )
    if nur_automatisch:
        abfrage = abfrage.where(DownloadVerlauf.automatisch.is_(True))
    return sum(1 for eintrag in db.scalars(abfrage) if _dieselbe_folge(zeile, eintrag))


def _benennung(zeile: DownloadHaenger) -> str:
    """Wie die Nachricht den Download nennt: bei einer Serie mit Folge oder Staffel.

    Seit je Folge gezaehlt wird, kann dieselbe Serie in einer Woche zweimal
    gemeldet werden. Nur mit dem Seriennamen saehen beide gleich aus.
    """
    titel = zeile.titel or zeile.release
    if zeile.media_type != "tv":
        return titel
    folgen = sorted(
        {
            (eintrag[0], eintrag[1])
            for eintrag in zeile.folgen or []
            if isinstance(eintrag, list | tuple)
            and len(eintrag) == 2
            and all(isinstance(nummer, int) for nummer in eintrag)
        }
    )
    if len(folgen) == 1:
        staffel, folge = folgen[0]
        return f"{titel} S{staffel:02d}E{folge:02d}"
    staffeln = {staffel for staffel, _folge in folgen}
    if len(staffeln) == 1:
        return f"{titel} S{staffeln.pop():02d}"
    return titel


def _melden(db: Session, zeile: DownloadHaenger, jetzt: datetime, *, aufgegeben: bool) -> None:
    """Die Administratoren benachrichtigen - hoechstens einmal je Film bzw. Folge und Woche.

    Egal mit welcher der beiden Nachrichten: Wer schon weiss, dass es hier
    klemmt, bekommt in derselben Woche keine zweite.
    """
    if _anzahl(db, zeile, {"gemeldet"}, jetzt - WIEDERHOLT_FENSTER) > 0:
        return
    titel = _benennung(zeile)
    if aufgegeben:
        notify.create_for_admins(
            db,
            kind=NotificationType.download_stuck,
            message_key="notifications.downloadAutomationGaveUp",
            title=titel,
        )
    else:
        notify.create_for_admins(
            db,
            kind=NotificationType.download_stuck,
            message_key="notifications.downloadStuck",
            title=titel,
        )
    db.add(beschaffung.download_verlauf(zeile, "gemeldet", automatisch=True))
    db.commit()
    logger.warning(
        "Download keeps getting stuck: %r in %s (%s)%s",
        titel,
        zeile.kennung,
        zeile.grund,
        ", automation gave up" if aufgegeben else "",
    )


async def _handeln(db: Session, settings: AppSettings, zeile_id: int, aktion: str) -> None:
    if aktion == Aktion.entfernen_neu_suchen.value:
        await get_beschaffung(settings).download_entfernen(
            db, zeile_id, neu_suchen=True, wer=None, automatisch=True
        )
    elif aktion == Aktion.entfernen.value:
        await get_beschaffung(settings).download_entfernen(
            db, zeile_id, neu_suchen=False, wer=None, automatisch=True
        )
    elif aktion == Aktion.erneut_pruefen.value:
        await get_beschaffung(settings).download_erneut_pruefen(
            db, zeile_id, wer=None, automatisch=True
        )


async def ausfuehren(db: Session, settings: AppSettings, *, jetzt: datetime | None = None) -> int:
    """Einmal je Runde: handeln, wo eine Regel es erlaubt, und melden, was sich wiederholt.

    Gibt zurueck, wie oft gehandelt wurde. Fehler je Download, nie im Ganzen.
    """
    jetzt = jetzt or _jetzt()
    einstellung = lesen(db)
    getan = 0
    kennungen = list(
        db.scalars(
            select(DownloadHaenger.id)
            .where(DownloadHaenger.haengt_seit.is_not(None))
            .order_by(DownloadHaenger.haengt_seit)
        )
    )
    for zeile_id in kennungen:
        zeile = db.get(DownloadHaenger, zeile_id)
        if zeile is None:
            continue
        try:
            aktion = einstellung.regeln.get(zeile.grund) if einstellung.an else None
            if aktion is not None:
                if (
                    _anzahl(db, zeile, AKTIONSARTEN, jetzt - FENSTER, nur_automatisch=True)
                    >= OBERGRENZE
                ):
                    _melden(db, zeile, jetzt, aufgegeben=True)
                    continue
                await _handeln(db, settings, zeile_id, aktion)
                getan += 1
                continue
            if _anzahl(db, zeile, {"erkannt"}, jetzt - WIEDERHOLT_FENSTER) >= WIEDERHOLT_AB:
                _melden(db, zeile, jetzt, aufgegeben=False)
        except DownloadFehler as fehler:
            logger.info("Automation left download %s alone: %s", zeile_id, fehler.code)
            db.rollback()
        except BeschaffungError as fehler:
            # Info statt Warnung: Eine stumme Instanz meldet sich an anderer
            # Stelle ohnehin, und hier kaeme sonst jede Runde eine Zeile dazu.
            logger.info("Automation could not reach the instance for %s: %s", zeile_id, fehler.code)
            db.rollback()
        except Exception:  # noqa: BLE001 - ein Download darf die anderen nicht mitnehmen
            logger.exception("Automation failed for stuck download %s", zeile_id)
            db.rollback()
    return getan
