"""Der Rueckkanal: Radarr und Sonarr rufen an, Nexview zieht den Rundgang vor.

Der Anruf ist **ein Wecker, kein zweiter Wahrheitskanal**: Geprueft wird nur
das Geheimnis der Instanz, dem Inhalt wird nichts geglaubt - nicht einmal die
Serien-Kennung. Die Wahrheit holt sich der Takt-Laeufer wie immer selbst, nur
eben sofort statt erst beim naechsten Takt (``status_poller``).

Hier liegt der Zustand dazu: das Geheimnis je Instanz-Kennung (verschluesselt
wie die API-Keys), die Zeitstempel fuer "bewiesen" und "zuletzt angerufen" -
und das Wecksignal, auf das der Takt-Laeufer wartet.
"""

from __future__ import annotations

import asyncio
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..models import ArrWebhook

# ⚠️ Ein Signal fuer alle Instanzen, nicht eines je Instanz: Der Rundgang
# prueft ohnehin alles, was offen ist - zwei Anrufe verschiedener Instanzen
# brauchen deshalb auch nur einen Rundgang.
_weckruf = asyncio.Event()


def wecken() -> None:
    """Den Takt-Laeufer bitten, seinen Rundgang vorzuziehen.

    Absichtlich nur ein Signal und keine Warteschlange: Zehn Anrufe in zwei
    Sekunden (Massen-Import) sind **ein** Grund nachzusehen, nicht zehn.
    Das Entprellen erledigt der Laeufer beim Warten.
    """
    _weckruf.set()


def weckruf() -> asyncio.Event:
    """Das Signal selbst - fuer den Takt-Laeufer und fuer Tests."""
    return _weckruf


def eintrag(db: Session, kennung: str) -> ArrWebhook | None:
    return db.scalar(select(ArrWebhook).where(ArrWebhook.kennung == kennung))


def eintrag_sicherstellen(db: Session, kennung: str) -> ArrWebhook:
    """Zustand samt frischem Geheimnis anlegen, falls es ihn noch nicht gibt.

    Gerufen von der Pflege, **bevor** sie den Eintrag in Radarr/Sonarr
    schreibt - erst dadurch existiert ueberhaupt ein Geheimnis, mit dem sich
    ein Anruf ausweisen kann. Ohne diese Zeile weist der Empfaenger alles ab:
    Wo nie ein Geheimnis vergeben wurde, kann keines stimmen.
    """
    vorhanden = eintrag(db, kennung)
    if vorhanden is not None:
        return vorhanden
    zeile = ArrWebhook(
        kennung=kennung,
        geheimnis=crypto.encrypt(secrets.token_urlsafe(32)),
    )
    db.add(zeile)
    db.commit()
    return zeile


def geheimnis_klartext(zeile: ArrWebhook) -> str:
    """Das Geheimnis, wie es in Radarr/Sonarr eingetragen wird (Pflege)."""
    return crypto.decrypt(zeile.geheimnis)


def geheimnis_stimmt(zeile: ArrWebhook, kandidat: str) -> bool:
    """Traegt der Anruf das richtige Geheimnis?

    ``compare_digest`` statt ``==``: Der Vergleich dauert immer gleich lang,
    sonst liesse sich das Geheimnis Zeichen fuer Zeichen ueber die Antwortzeit
    erraten. Ein leeres gespeichertes Geheimnis (Schluessel verloren, siehe
    ``crypto.decrypt``) stimmt mit **nichts** ueberein - auch nicht mit einem
    leeren Kandidaten.
    """
    echt = geheimnis_klartext(zeile)
    if not echt:
        return False
    return secrets.compare_digest(echt.encode("utf-8"), kandidat.encode("utf-8"))
