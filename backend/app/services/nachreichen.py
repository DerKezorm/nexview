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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select

from ..models import MediaRequest, RequestStatus
from .beschaffung import NEX, BeschaffungError, get_beschaffung
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


@dataclass(frozen=True)
class Ergebnis:
    gereicht: int
    #: Auf einer Fassung, die der Weg nicht kennt. Die zeigt der Befund.
    liegen: int


def faellig(settings: AppSettings) -> bool:
    return bool(settings.beschaffung_gewechselt_am)


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
    """
    if settings.beschaffung != NEX:
        return None
    bekannt = _bekannt(settings)
    if not bekannt:
        return None
    return and_(MediaRequest.status.in_(LAUFEND), _fremd(bekannt))


async def einmal(db: Session, settings: AppSettings) -> Ergebnis:
    """Freigegebene Anfragen dem neuen Weg geben. Wie viele ankamen, wie viele liegen.

    Kein Fehler bricht den Durchgang ab: Eine Anfrage, die nicht durchgeht,
    bleibt freigegebenen und wird beim nächsten Mal wieder versucht - genau
    das, was `push_to_arr` bei einem ungewissen Ausgang auch tut.

    ⚠️ **Nur Bekanntes belegt einen Platz im Durchgang.** Vorher standen die
    fremden mit in der Auswahl; waren die ältesten ``JE_DURCHGANG`` alle
    fremd, ging nichts durch, und das Nachreichen hielt sich für fertig, bevor
    eine jüngere Anfrage auf einer bekannten Fassung dran war.
    """
    bekannt = _bekannt(settings)
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
    offen = list(
        db.scalars(
            select(MediaRequest)
            .where(freigegeben, MediaRequest.fassung_kennung.in_(bekannt))
            .order_by(MediaRequest.requested_at)
            .limit(JE_DURCHGANG)
        )
    )
    if not offen:
        _fertig(db, settings)
        return Ergebnis(gereicht=0, liegen=liegen)

    gereicht = 0
    for anfrage in offen:
        try:
            from . import requests_service

            await requests_service.push_to_arr(db, settings, anfrage)
            gereicht += 1
        except BeschaffungError as fehler:
            logger.warning(
                "Could not hand over request %s after the switch: %s",
                anfrage.id,
                fehler.code or fehler.message,
            )
            db.rollback()
        except Exception:  # noqa: BLE001 - eine Anfrage darf die anderen nicht mitnehmen
            logger.exception("Could not hand over request %s after the switch", anfrage.id)
            db.rollback()

    if gereicht:
        logger.info("Handed over %d approved request(s) after the switch", gereicht)
    if gereicht == 0:
        # Nichts ging mehr durch. Hier ist Schluss.
        _fertig(db, settings)
    return Ergebnis(gereicht=gereicht, liegen=liegen)


def _fertig(db: Session, settings: AppSettings) -> None:
    save_settings(db, {"beschaffung_gewechselt_am": ""})
    logger.info("Nothing left to hand over after the switch")
