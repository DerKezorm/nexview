"""Nach dem Umschalten: was freigegeben war, dem neuen Weg noch einmal geben.

⚠️ **Ohne das bleibt eine freigegebene Anfrage für immer stehen.** Sie wird
genau einmal übergeben - bei der Freigabe. Wer danach von Radarr und Sonarr
auf nexcrate umschaltet, hat dort einen Stapel Anfragen, von denen der neue
Weg nie gehört hat: In Nexview steht „freigegeben", und es passiert nichts.

Dass das gefahrlos geht, liegt an einer Zusage: `POST /requests` ist
idempotent (N17). Ein Titel, den nexcrate schon führt, antwortet `unchanged`;
doppelt senden schadet nie. Die ganze Wartelogik, die nexbeat für Lidarr
brauchte, entfällt deshalb hier.

⚠️ **Nachgereicht wird nur, was der neue Weg auch kennt.** Eine Anfrage trägt
die Kennung ihrer Fassung; nach dem Umschalten ist das eine Arr-Kennung, die
es in nexcrate nicht gibt. Die Abbildung darauf macht der Umstiegsassistent.
Was er auf „Keine" abbildet oder was mit seinem Posten bei der alten Fassung
bleibt, bleibt liegen.

⚠️ **Liegen heisst nicht still.** Wiederholen macht eine unbekannte Fassung
nicht bekannt, das Nachreichen ist also trotzdem fertig. Sichtbar bleibt so
eine Anfrage als Befund (``befunde._nachschub_fremde_fassung``), solange es
sie gibt; bis zum 24.09.2026 war die einzige Spur eine Protokollzeile, und
die Anfrage stand für immer auf „freigegeben".

⚠️ **Und Wiederholen hat eine Grenze.** Eine Übergabe, die mit 5xx scheitert,
bleibt freigegeben (Ausgang ungewiss) und wird wieder versucht. Ohne Grenze
hiess das: alle zwei Minuten, für immer, mit zwei Protokollzeilen je Versuch.
Nach ``WIEDERHOLEN_BIS`` wird nur noch übergeben, was nie gescheitert ist;
was dann noch liegt, zeigt derselbe Befund.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select

from ..models import MediaRequest, RequestStatus, utcnow
from . import logs
from .beschaffung import NEX, get_beschaffung
from .settings_service import save_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.elements import ColumnElement

    from .settings_service import AppSettings

logger = logging.getLogger("nexview.requests")

#: Höchstens so viele je Durchgang - ein Umstieg kann hunderte bedeuten, und
#: der Rundgang soll daran nicht hängenbleiben. Der nächste macht weiter.
JE_DURCHGANG = 25

#: Hier erwartet der Besteller noch etwas vom Weg: freigegeben heisst "wird
#: übergeben", sucht heisst "läuft dort". Auf einer fremden Fassung kommt
#: beides nie.
LAUFEND = (RequestStatus.approved, RequestStatus.searching)

#: So lange nach dem Umschalten wird eine gescheiterte Übergabe wiederholt.
#:
#: Eine Zeitspanne und keine Zahl von Versuchen: Dafür bräuchte jede Anfrage
#: einen Zähler in der Datenbank, und der Rundgang ist nicht der einzige, der
#: sie anfasst. Den Zeitpunkt des Umschaltens gibt es schon, er übersteht
#: jeden Neustart, und ein Tag deckt einen Ausfall über Nacht ab.
WIEDERHOLEN_BIS = timedelta(hours=24)


@dataclass(frozen=True)
class Ergebnis:
    gereicht: int
    #: Auf einer Fassung, die der Weg nicht kennt. Die zeigt der Befund.
    liegen: int


def faellig(settings: AppSettings) -> bool:
    return bool(settings.beschaffung_gewechselt_am)


def _frist_vorbei(settings: AppSettings) -> bool:
    """Liegt das Umschalten länger als ``WIEDERHOLEN_BIS`` zurück?

    Ein Wert, der sich nicht lesen lässt, zählt als vorbei: Dann endet nur das
    Wiederholen, und was liegt, zeigt der Befund.
    """
    try:
        seit = datetime.fromisoformat(settings.beschaffung_gewechselt_am)
    except ValueError:
        return True
    if seit.tzinfo is None:
        seit = seit.replace(tzinfo=UTC)
    return utcnow() - seit > WIEDERHOLEN_BIS


def _bekannt(settings: AppSettings) -> list[str]:
    return [eintrag.kennung for eintrag in get_beschaffung(settings).fassungen()]


def _fremd(bekannt: list[str]) -> ColumnElement[bool]:
    # Leer zählt mit: Auch dafür gibt es keinen Weg, und ``NOT IN`` allein
    # liesse NULL stillschweigend fallen.
    return or_(
        MediaRequest.fassung_kennung.is_(None),
        MediaRequest.fassung_kennung.not_in(bekannt),
    )


def fremde_fassung(settings: AppSettings) -> ColumnElement[bool] | None:
    """Laufende Anfragen auf einer Fassung, die der eingestellte Weg nicht kennt.

    ⚠️ **Eine Bedingung für Befund und Anfragenliste**, damit der Sprung aus
    dem Befund genau die Anfragen zeigt, die er zählt.

    ``None``, solange der Weg gar keine Fassung kennt (nexcrate noch nie
    gelesen): Dann wäre jede Anfrage "fremd", und das wäre geraten.

    ⚠️ **Und ``None`` im ARR-Betrieb.** Dort entsteht so eine Anfrage nicht
    durch den Umstieg, sondern nur, wenn eine Instanz ausgetragen wird. Die
    Kachel hat eine feste Abfragezahl (``test_abfragezahl.py``), und eine
    Abfrage je Aufruf für diesen Fall sprengte sie; die Grenze wird nicht
    hochgesetzt.

    ⚠️ **Mitgezählt wird, was nexcrate nicht angenommen hat**: freigegeben
    und mit Fehler, auch auf einer bekannten Fassung. Das ist eine gescheiterte
    Übergabe mit ungewissem Ausgang; kommt der Titel doch an, setzt der
    Rundgang sie auf "sucht", und sie fällt heraus. Ohne das stand eine
    Anfrage, deren Übergabe dauerhaft scheiterte, nach dem Ende des
    Nachreichens für immer auf „freigegeben", ohne Befund. Eine Bedingung und
    kein zweiter Befund, weil die Kachel keine Abfrage mehr hergibt.
    """
    if settings.beschaffung != NEX:
        return None
    bekannt = _bekannt(settings)
    if not bekannt:
        return None
    return or_(
        and_(MediaRequest.status.in_(LAUFEND), _fremd(bekannt)),
        and_(
            MediaRequest.status == RequestStatus.approved,
            MediaRequest.error_message.is_not(None),
        ),
    )


async def einmal(db: Session, settings: AppSettings) -> Ergebnis:
    """Freigegebene Anfragen dem neuen Weg geben. Wie viele ankamen, wie viele liegen.

    Kein Fehler bricht den Durchgang ab: Eine Anfrage, die nicht durchgeht,
    bleibt freigegebenen und wird beim nächsten Mal wieder versucht - genau
    das, was `push_to_arr` bei einem ungewissen Ausgang auch tut.

    ⚠️ **Nur Bekanntes belegt einen Platz im Durchgang.** Vorher standen die
    fremden mit in der Auswahl; waren die ältesten ``JE_DURCHGANG`` alle
    fremd, ging nichts durch, und das Nachreichen hielt sich für fertig, bevor
    eine jüngere Anfrage auf einer bekannten Fassung dran war.

    ⚠️ **Fertig ist es erst, wenn nichts Bekanntes mehr offen ist**, nicht
    schon, wenn in einem Durchgang nichts durchging. Bis zum 24.09.2026 hiess
    ein 503 oder eine Zeitüberschreitung von nexcrate "fertig", und die
    gültige Anfrage blieb für immer freigegeben, ohne Befund.

    ⚠️ **Wer gescheitert ist, stellt sich hinten an.** Vorher kamen immer die
    ältesten zuerst; scheiterten ``JE_DURCHGANG`` davon dauerhaft, kam eine
    jüngere gültige nie dran. Gescheitert heisst hier: mit Fehler
    freigegeben. ``last_checked_at`` allein reicht dafür nicht, denn
    ``status_poller.check_once`` stempelt es jede Runde an jeder
    freigegebenen Anfrage neu.
    """
    bekannt = _bekannt(settings)
    if not bekannt:
        # Noch nichts gelesen: Dann wäre jede Anfrage "fremd", und das wäre
        # geraten (dieselbe Regel wie ``fremde_fassung``). Der Merker bleibt,
        # der nächste Durchgang fragt wieder.
        return Ergebnis(gereicht=0, liegen=0)
    freigegeben = MediaRequest.status == RequestStatus.approved
    liegen = (
        db.scalar(
            select(func.count(MediaRequest.id)).where(freigegeben, _fremd(bekannt))
        )
        or 0
    )
    if liegen:
        logger.info(
            "%d approved request(s) carry a version the current mode does not know; "
            "they stay approved and show up as a finding",
            liegen,
        )
    auswahl = [freigegeben, MediaRequest.fassung_kennung.in_(bekannt)]
    if _frist_vorbei(settings):
        # Nach der Frist nur noch, was nie gescheitert ist.
        auswahl.append(MediaRequest.error_message.is_(None))
    offen = list(
        db.scalars(
            select(MediaRequest)
            .where(*auswahl)
            .order_by(
                MediaRequest.error_message.is_not(None),
                MediaRequest.last_checked_at.asc().nulls_first(),
                MediaRequest.requested_at,
            )
            .limit(JE_DURCHGANG)
        )
    )
    if not offen:
        _fertig(db, settings)
        return Ergebnis(gereicht=0, liegen=liegen)

    from . import requests_service

    gereicht = 0
    for anfrage in offen:
        try:
            await requests_service.push_to_arr(db, settings, anfrage)
            gereicht += 1
        except requests_service.RequestError as fehler:
            # So kommt ein Fehler des Wegs hier an: ``push_to_arr`` hat den
            # Stand der Anfrage schon geschrieben. Ein ungewisser Ausgang
            # lässt sie freigegeben, der nächste Durchgang versucht es wieder.
            # Ins Protokoll die Kennung des Wegs, nicht der deutsche Satz:
            # ``RequestError`` trägt hier keine eigene.
            logger.warning(
                "Could not hand over request %s after the switch: %s",
                anfrage.id,
                logs.kennung(fehler.__cause__ or fehler),
            )
            db.rollback()
        except Exception:  # noqa: BLE001 - eine Anfrage darf die anderen nicht mitnehmen
            logger.exception("Could not hand over request %s after the switch", anfrage.id)
            db.rollback()

    if gereicht:
        logger.info("Handed over %d approved request(s) after the switch", gereicht)
    return Ergebnis(gereicht=gereicht, liegen=liegen)


def _fertig(db: Session, settings: AppSettings) -> None:
    save_settings(db, {"beschaffung_gewechselt_am": ""})
    logger.info("Nothing left to hand over after the switch")
