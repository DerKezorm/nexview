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
from . import mapping

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")

#: Welche Stufen überhaupt als Problem gelten. ``info`` ist keins.
STUFEN = frozenset({"error", "warning"})

#: Kennungen, deren Text unter ``nexcrate.health`` ohne Platzhalter auskommt.
#: Nur sie taugen als ``message_key`` der Glocke; die zeigt keine Werte.
#: ``test_die_glocke_hat_jeden_text`` haelt die Liste an den Sprachdateien.
GLOCKE = frozenset(
    {
        "indexer_none",
        "download_client_none",
        "tmdb_token_missing",
        "automatic_off",
        "automatic_off_movie",
        "automatic_off_series",
        "version_not_ready",
        "nexcrate_wuensche_warten",
        "nexcrate_ohne_anime",
    }
)


def glockentext(problem: dict[str, Any]) -> str:
    """Der ``message_key`` der Glocke: die Kennung, sonst der allgemeine Satz.

    ⚠️ **Nie nexcrates Satz**, auch nicht im Titel (Rundgang-Befund 5).
    """
    code = str(problem.get("code") or "")
    kind = (problem.get("params") or {}).get("kind")
    for kandidat in (f"{code}_{kind}" if kind else "", code):
        if kandidat in GLOCKE:
            return f"nexcrate.health.{kandidat}"
    return "notifications.instanceHealth_nex"


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
        # Musik fuehrt Nexview nicht. nexcrate meldet ``automatic_off`` je Art,
        # auch ``album``, und daraus wurde „Die Automatik ist aus“, obwohl
        # Filme und Serien an waren (Rundgang-Befund 5).
        if werte.get("kind") and mapping.art(str(werte["kind"])) not in mapping.EIGENE_ARTEN:
            continue
        teile = [code]
        # ⚠️ ``art`` und ``recht`` gehoeren dazu: Die Standpruefung meldet
        # ``nexcrate_ohne_medienart`` je Medienart und ``nexcrate_recht_fehlt``
        # je fehlendem Recht. Ohne sie im Schluessel bliebe von zwei Befunden
        # einer uebrig, und der Betreiber suchte nach dem zweiten.
        for name in ("kind", "version_id", "art", "recht"):
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
    from . import pruefung, system
    from .fehler import NexcrateError
    from .weg import client_fuer

    try:
        roh = await client_fuer(settings).health()
    except NexcrateError:
        # Stumm heisst unbekannt, nicht gesund - der gemerkte Stand bleibt.
        return

    # ⚠️ **Die Standpruefung laeuft hier mit** (Bauplan 7.2). Sie gehoert nicht
    # nur in die Einrichtung: Eine nexcrate kann zurueckgestuft werden, ein
    # Schluessel kann ein Recht verlieren. Wer das erst an der naechsten
    # Anfrage merkt, sucht den Fehler in Nexview.
    jetzt = verdichten([*pruefung.als_health(pruefung.pruefen(system.stand())), *roh])
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
            # Nicht der Schluessel des Arr-Wegs: Dessen Text sagt "Radarr/Sonarr
            # meldet ein Problem", und einen Platzhalter traegt die Glocke nicht.
            # Der Titel ist nur der Name: nexcrates Satz ist englisch.
            message_key=glockentext(problem),
            title=name,
        )

    zeile.stand = jetzt
    zeile.aktualisiert_am = utcnow()
    db.commit()
