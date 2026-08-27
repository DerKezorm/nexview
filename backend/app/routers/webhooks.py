"""Die Anruf-Adresse fuer Radarr und Sonarr - ohne Anmeldung, mit Geheimnis.

⚠️ **Der einzige Pfad, den eine fremde Anwendung von sich aus aufruft.**
Deshalb gelten hier drei Regeln strenger als sonst:

* Ausgewiesen wird sich mit dem **Anruf-Geheimnis der Instanz** (als
  Basic-Passwort - das Feld gibt es in jeder Radarr-/Sonarr-Fassung, anders
  als eigene Kopfzeilen). Keine Sitzung, kein API-Schluessel: Ein
  Benachrichtigungs-Eintrag in Sonarr soll niemals einen Zugang tragen, mit
  dem man Nexview *bedienen* koennte.
* Dem Inhalt wird **nichts geglaubt**. Gelesen wird nur ``eventType``, und
  auch das nur, um den Test-Beweis vom echten Ereignis zu unterscheiden und
  fuer die Anzeige "zuletzt: Download". Der Anruf ist ein Wecker - die
  Wahrheit holt sich der Rundgang selbst (``services/webhooks``).
* Die Antwort kommt **sofort**. Kein Anruf wartet auf einen Rundgang; sonst
  liefe Sonarr bei jedem Import in seine eigene Zeitgrenze und meldete den
  Eintrag als krank.

Der Pfad steht bewusst in der Erlaubnisliste von
``test_child_permissions.py``: Er verlangt das Geheimnis und liefert nichts
zurueck - ein Kind (oder irgendjemand ohne Geheimnis) sieht hier nichts.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..deps import DbSession
from ..meldungen import fehler
from ..models import utcnow
from ..services import webhooks
from ..services.settings_service import load_settings

logger = logging.getLogger("nexview.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# ``auto_error=False``: Ohne Anmeldedaten soll unsere eigene 401 mit Kennung
# kommen, nicht die generische von FastAPI.
_basic = HTTPBasic(auto_error=False)


@router.post("/arr/{kennung}", status_code=status.HTTP_204_NO_CONTENT)
async def anruf(
    kennung: str,
    request: Request,
    db: DbSession,
    anmeldung: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
) -> Response:
    instanzen = load_settings(db).arr_instanzen()
    if all(instanz.kennung != kennung for instanz in instanzen):
        # Auch eine frueher eingerichtete, inzwischen entfernte Instanz landet
        # hier: Wer nicht mehr in den Einstellungen steht, darf nicht wecken.
        raise fehler(
            "webhook_unknown_instance",
            "Zu dieser Kennung ist keine Instanz eingerichtet.",
            status.HTTP_404_NOT_FOUND,
        )

    zeile = webhooks.eintrag(db, kennung)
    if (
        zeile is None
        or anmeldung is None
        or not webhooks.geheimnis_stimmt(zeile, anmeldung.password or "")
    ):
        # Ein Eintrag ohne Zeile heisst: Es wurde nie ein Geheimnis vergeben -
        # dann kann auch keines stimmen. Absichtlich dieselbe Antwort wie bei
        # einem falschen Geheimnis, damit von aussen nicht unterscheidbar ist,
        # welche Instanzen einen Rueckkanal haben.
        logger.warning(
            "Webhook call for %s rejected: missing or wrong secret", kennung
        )
        raise fehler(
            "webhook_secret_rejected",
            "Das Anruf-Geheimnis fehlt oder stimmt nicht.",
            status.HTTP_401_UNAUTHORIZED,
        )

    # Nur ``eventType`` - und selbst das bloss fuer Beweis und Anzeige. Ein
    # unlesbarer Inhalt ist kein Ablehnungsgrund: Das Geheimnis stimmte, also
    # hat die Instanz angerufen, und mehr muss der Wecker nicht wissen.
    ereignis = ""
    try:
        daten = await request.json()
        if isinstance(daten, dict):
            ereignis = str(daten.get("eventType") or "")
    except Exception:  # noqa: BLE001 - Inhalt ist ohnehin unglaubwuerdig
        pass

    zeile.zuletzt_angerufen_am = utcnow()
    zeile.letztes_ereignis = ereignis[:64]

    if ereignis == "Test":
        # Der Erreichbarkeits-Beweis: Sonarrs Probe kam wirklich hier an.
        # Ein Test weckt nicht - er sagt nur, dass die Strecke steht.
        zeile.bewiesen_am = utcnow()
        db.commit()
        logger.info("Webhook proof received for %s", kennung)
    else:
        db.commit()
        webhooks.wecken()
        logger.info(
            "Webhook call received for %s (%s) - status sync brought forward",
            kennung,
            ereignis or "no event type",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
