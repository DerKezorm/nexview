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
es in nexcrate nicht gibt. Die Abbildung darauf macht der Umstiegsassistent
(Scheibe 7). Bis dahin bleibt so eine Anfrage liegen - mit einer Zeile im
Protokoll, nicht mit einem stillen Fehlschlag.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..models import MediaRequest, RequestStatus
from .beschaffung import BeschaffungError, get_beschaffung
from .settings_service import save_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from .settings_service import AppSettings

logger = logging.getLogger("nexview.requests")

#: Höchstens so viele je Durchgang - ein Umstieg kann hunderte bedeuten, und
#: der Rundgang soll daran nicht hängenbleiben. Der nächste macht weiter.
JE_DURCHGANG = 25


def faellig(settings: AppSettings) -> bool:
    return bool(settings.beschaffung_gewechselt_am)


async def einmal(db: Session, settings: AppSettings) -> int:
    """Freigegebene Anfragen dem neuen Weg geben. Wie viele ankamen.

    Kein Fehler bricht den Durchgang ab: Eine Anfrage, die nicht durchgeht,
    bleibt freigegebenen und wird beim nächsten Mal wieder versucht - genau
    das, was `push_to_arr` bei einem ungewissen Ausgang auch tut.
    """
    beschaffung = get_beschaffung(settings)
    bekannt = {eintrag.kennung for eintrag in beschaffung.fassungen()}
    offen = list(
        db.scalars(
            select(MediaRequest)
            .where(MediaRequest.status == RequestStatus.approved)
            .order_by(MediaRequest.requested_at)
            .limit(JE_DURCHGANG)
        )
    )
    if not offen:
        _fertig(db, settings)
        return 0

    gereicht = 0
    liegen = 0
    for anfrage in offen:
        if anfrage.fassung_kennung not in bekannt:
            liegen += 1
            continue
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

    if liegen:
        logger.info(
            "%d approved request(s) still carry a version the new mode does not know; "
            "the migration assistant maps them",
            liegen,
        )
    if gereicht:
        logger.info("Handed over %d approved request(s) after the switch", gereicht)
    if gereicht == 0:
        # Nichts ging mehr durch - entweder ist alles nachgereicht oder es
        # wartet auf die Abbildung. Beides heisst: hier ist Schluss.
        _fertig(db, settings)
    return gereicht


def _fertig(db: Session, settings: AppSettings) -> None:
    save_settings(db, {"beschaffung_gewechselt_am": ""})
    logger.info("Nothing left to hand over after the switch")
