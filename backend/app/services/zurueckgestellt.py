"""Zurueckgestellte Anfragen zurueck auf den Tisch holen.

„Nicht abgelehnt, nur vertagt" - so ist ``deferred`` gemeint, und so steht es
auch in der Meldung, die der Besteller bekommt:

    „… steht bereits zurück – sobald du wieder Platz hast, kann die Anfrage
    freigegeben werden."

⚠️ **Dieser Satz war lange nur halb wahr.** Niemand holte eine zurueckgestellte
Anfrage zurueck - weder der Poller noch die Kontingent-Rechnung noch die
Speichermessung kannten ``deferred`` ueberhaupt. Sie blieb liegen, bis ein
Administrator zufaellig in den elften Reiter sah. Der Satz versprach eine
Automatik, die es nicht gab; genau dieselbe Sorte Zusage wie der Docstring von
``set_password``, der behauptete, ein Passwortwechsel beende alle Sitzungen.

Jetzt gilt er: Sobald die Anfrage wieder durch **beide** Kontingente passt,
wandert sie zurueck zu den offenen Freigaben - dorthin, wo der Administrator
ohnehin hinsieht - und der Besteller erfaehrt es.

**Was hier bewusst nicht passiert: freigeben.** Die Anfrage kehrt in den
Wartezustand zurueck, nicht in die Bibliothek. Ob sie durchgeht, bleibt die
Entscheidung eines Menschen; das Kontingent sagt nur, dass sie wieder gestellt
werden *darf*.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MediaRequest, NotificationType, RequestStatus, User
from . import notify, quota, storage
from .settings_service import AppSettings, for_user

logger = logging.getLogger("nexview.zurueckgestellt")


def _passt_wieder(db: Session, settings: AppSettings, anfrage: MediaRequest) -> bool:
    """Ginge diese Anfrage jetzt durch - Stueckzahl **und** Platz?

    Dieselben zwei Grenzen wie beim Anfragen (``requests_service`` prueft sie
    in derselben Reihenfolge). Bewusst nicht deren Funktion aufgerufen: Die
    wirft Ausnahmen mit fertigen Meldungen fuer den Besteller, und hier wird
    nur gefragt, nicht geantwortet.
    """
    person = anfrage.user
    if person is None or not person.is_active:
        return False

    eigene = for_user(settings, person)
    if quota.state_for(db, person, anfrage.media_type, eigene).exhausted:
        return False
    return not storage.stand_fuer(db, person, eigene).exhausted


def zurueckholen(db: Session, settings: AppSettings) -> int:
    """Alle zurueckgestellten Anfragen pruefen, die wieder passen.

    Gibt zurueck, wie viele zurueck auf den Tisch gewandert sind. Ohne
    zurueckgestellte Anfragen kostet das eine Abfrage und sonst nichts.
    """
    offene = list(
        db.scalars(
            select(MediaRequest).where(MediaRequest.status == RequestStatus.deferred)
        )
    )
    if not offene:
        return 0

    zurueck = 0
    for anfrage in offene:
        if not _passt_wieder(db, settings, anfrage):
            continue

        anfrage.status = RequestStatus.pending_approval
        # ⚠️ Der Besteller muss es erfahren. Sonst wechselt seine Anfrage
        # stillschweigend den Zustand - und das sieht von aussen aus wie ein
        # Fehler, genau wie beim Zuruecksetzen selbst.
        if anfrage.user is not None:
            notify.create(
                db,
                user=anfrage.user,
                kind=NotificationType.request_pending,
                message_key="notifications.deferredBack",
                request=anfrage,
                title=anfrage.title,
            )
        zurueck += 1
        logger.info(
            "Deferred request %r is within quota again - back to pending", anfrage.title
        )

    if zurueck:
        # Die Administratoren sehen sie jetzt in den offenen Freigaben. Eine
        # eigene Meldung an sie waere zu viel: Sie sehen den Reiter ohnehin,
        # und bei einem freigewordenen Wochenkontingent kaemen sonst zehn
        # Nachrichten auf einmal.
        db.commit()
    return zurueck
