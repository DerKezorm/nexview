"""Gesundheits-Probleme der Instanzen: sehen, dem Betreiber melden, merken.

Der haeufigste stille Totalausfall: Sonarrs Download-Client oder Indexer
faellt aus - dann haengen **alle** Anfragen auf "wird gesucht", und in
Nexview merkte das bisher niemand. Radarr/Sonarr wissen es laengst und
fuehren es unter ``/health``; hier wird es jede Runde abgeholt, auf der
Diensteseite gezeigt und den Administratoren gemeldet.

Drei Regeln:

* **Einmal je Problem, nicht je Runde.** Der zuletzt gesehene Stand liegt in
  ``arr_gesundheit``; gemeldet wird nur, was dort noch nicht stand. Ein
  Dauerproblem erzeugt genau eine Meldung - Laerm wird weggeklickt, und
  danach auch die Meldung, auf die es ankommt.
* **Verschwinden ist still.** Die Anzeige raeumt sich auf, aber es gibt
  keine "wieder gut"-Meldung - wer die Glocke oeffnet, sieht am Fehlen des
  Warnkastens genug.
* **Nicht erreichbar ist kein Gesundheitsproblem.** Eine stumme Instanz
  laesst den gemerkten Stand unangetastet: Ob ihre Probleme noch bestehen,
  weiss in dem Moment niemand - und die Erreichbarkeit selbst zeigt die
  Diensteseite ohnehin an anderer Stelle.

Der Anruf (Webhook, ``onHealthIssue``) ist auch hier nur ein Wecker: Er
zieht den Rundgang vor, der Rundgang fragt selbst nach - derselbe eine
Wahrheitsweg wie beim Status-Abgleich.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ArrGesundheit, NotificationType, utcnow
from . import notify
from .arr import ArrClient, ArrError
from .settings_service import AppSettings, ArrInstanz

logger = logging.getLogger("nexview.gesundheit")


def _schluessel(problem: dict) -> str:
    """Woran ein Problem wiedererkannt wird - Quelle und Schwere.

    Bewusst ohne den Meldungstext: Der traegt Zaehler und Zeitangaben
    ("unavailable for 6 hours"), die sich aendern, waehrend das Problem
    dasselbe bleibt - je Aenderung neu zu melden waere genau die Flut, die
    das Gedaechtnis verhindern soll.
    """
    return f"{problem.get('source') or '?'}|{problem.get('type') or '?'}"


def _verdichten(roh: list[dict]) -> list[dict]:
    ergebnis = []
    gesehen: set[str] = set()
    for problem in roh:
        if not isinstance(problem, dict):
            continue
        schluessel = _schluessel(problem)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        ergebnis.append(
            {
                "schluessel": schluessel,
                "typ": str(problem.get("type") or "warning"),
                "text": str(problem.get("message") or problem.get("source") or ""),
            }
        )
    return ergebnis


def eintrag(db: Session, kennung: str) -> ArrGesundheit | None:
    return db.scalar(select(ArrGesundheit).where(ArrGesundheit.kennung == kennung))


def alle(db: Session) -> dict[str, ArrGesundheit]:
    """Der gemerkte Stand aller Instanzen, nach Kennung.

    Gegenstueck zu ``instanz_stand.alle``: Wer je Instanz fragt, stellt bei
    drei Instanzen drei Abfragen fuer eine Tabelle mit drei Zeilen.
    """
    return {zeile.kennung: zeile for zeile in db.scalars(select(ArrGesundheit))}


async def pruefen(db: Session, settings: AppSettings) -> None:
    """Alle Instanzen einmal befragen - Fehler je Instanz, nie im Ganzen."""
    for instanz in settings.arr_instanzen():
        try:
            await _instanz_pruefen(db, instanz)
        except Exception:  # noqa: BLE001 - eine Instanz darf die anderen nicht mitnehmen
            logger.exception("Health check for %s failed", instanz.name)
            db.rollback()


async def _instanz_pruefen(db: Session, instanz: ArrInstanz) -> None:
    client = ArrClient(instanz.url, instanz.api_key, instanz.name)
    try:
        roh = await client.gesundheit()
    except ArrError:
        # Stumm heisst unbekannt, nicht gesund - der gemerkte Stand bleibt.
        return

    jetzt = _verdichten(roh)
    zeile = eintrag(db, instanz.kennung)
    if zeile is None:
        zeile = ArrGesundheit(kennung=instanz.kennung)
        db.add(zeile)

    bekannt = {p.get("schluessel") for p in zeile.stand or []}
    for problem in jetzt:
        if problem["schluessel"] in bekannt:
            continue
        logger.warning(
            "Health issue reported by %s: %s", instanz.name, problem["text"]
        )
        notify.create_for_admins(
            db,
            kind=NotificationType.instanz_gesundheit,
            message_key="notifications.instanceHealth",
            title=f"{instanz.name}: {problem['text']}",
        )

    zeile.stand = jetzt
    zeile.aktualisiert_am = utcnow()
    db.commit()
