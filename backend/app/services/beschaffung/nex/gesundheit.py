"""Was nexcrate über sich meldet (``GET /health``, N35).

Dieselbe Tabelle wie im ARR-Betrieb: Sie ist das Gedächtnis fürs Entprellen
(„einmal je Problem melden, nicht stündlich wieder") und die Anzeige auf der
Dienste-Seite. Die Wahrheit ist immer die frische Antwort - der Rundgang holt
sie jede Runde.

⚠️ **Der Text bleibt englisch.** Er ist nexcrates Aussage, nicht unsere;
Nexview zeigt die Kennung und übersetzt sie selbst (N5). Der Wortlaut steht
nur daneben, für den Betreiber.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ....models import ArrGesundheit, NotificationType, utcnow
from ... import notify

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")

#: Welche Stufen überhaupt als Problem gelten. ``info`` ist keins.
STUFEN = frozenset({"error", "warning"})


def verdichten(roh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """nexcrates Befunde in die Form der Tabelle.

    ⚠️ **Der Schlüssel ist Kennung plus Werte**, nicht der Satz: ``automatic_off``
    kommt je Medienart einmal, ``version_not_ready`` je Fassung. Über den Satz
    zu entprellen hieße, eine Umbenennung in nexcrate als neues Problem zu
    melden.
    """
    gefunden: list[dict[str, Any]] = []
    gesehen: set[str] = set()
    for eintrag in roh:
        code = str(eintrag.get("code") or "")
        if not code or str(eintrag.get("level") or "") not in STUFEN:
            continue
        werte = eintrag.get("params") or {}
        teile = [code]
        for name in ("kind", "version_id"):
            if werte.get(name):
                teile.append(str(werte[name]))
        schluessel = ":".join(teile)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        gefunden.append(
            {
                "schluessel": schluessel,
                "typ": str(eintrag.get("level")),
                "text": str(eintrag.get("message") or code),
                "code": code,
                "params": werte,
            }
        )
    return gefunden


async def pruefen(db: Session, settings: AppSettings, kennung: str, name: str) -> None:
    """nexcrate einmal befragen und melden, was neu ist."""
    from ..arr.instanz_gesundheit import eintrag as gemerkt
    from .fehler import NexcrateError
    from .weg import client_fuer

    try:
        roh = await client_fuer(settings).health()
    except NexcrateError:
        # Stumm heisst unbekannt, nicht gesund - der gemerkte Stand bleibt.
        return

    jetzt = verdichten(roh)
    zeile = gemerkt(db, kennung)
    if zeile is None:
        zeile = ArrGesundheit(kennung=kennung)
        db.add(zeile)

    bekannt = {p.get("schluessel") for p in zeile.stand or []}
    for problem in jetzt:
        if problem["schluessel"] in bekannt:
            continue
        logger.warning("Health issue reported by nexcrate: %s", problem["text"])
        notify.create_for_admins(
            db,
            kind=NotificationType.instanz_gesundheit,
            message_key="notifications.instanceHealth",
            title=f"{name}: {problem['text']}",
        )

    zeile.stand = jetzt
    zeile.aktualisiert_am = utcnow()
    db.commit()
