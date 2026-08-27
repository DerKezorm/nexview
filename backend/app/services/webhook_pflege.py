"""Die Pflege des Rueckkanals: Nexview traegt sich in Radarr/Sonarr selbst ein.

Der Betreiber stellt in Radarr/Sonarr nichts ein - Nexview legt seinen
Benachrichtigungs-Eintrag selbst an, zieht ihn nach, wenn Adresse oder
Geheimnis sich aendern, und legt ihn neu an, wenn ihn jemand von Hand
loescht. Vier Grundsaetze, alle im Bauplan "Draht statt Takt" entschieden:

* **Erst der Beweis, dann der Eintrag.** Angelegt wird erst, wenn die Probe
  (Sonarrs Test-Ereignis) nachweislich bei uns angekommen ist. Ohne Beweis
  bliebe in Radarr/Sonarr ein Eintrag stehen, der bei jedem Ereignis
  fehlschlaegt und dort als Gesundheitsproblem auffaellt - in einem System,
  das uns nur einen API-Schluessel gegeben hat.
* **Fremde Eintraege sind tabu.** In echten Installationen haengen dort
  andere Anwendungen (live gesehen: "Ruddarr"). Unser Eintrag wird an der
  Nummer erkannt, ersatzweise an Name **und** unserer Anruf-Adresse - Name
  allein reicht nicht, den kann jeder vergeben.
* **Abwaehlen raeumt auf.** Der Haken je Instanz entfernt unseren Eintrag
  rueckstandsfrei, statt ihn nur zu ignorieren.
* **Faehigkeiten werden gemessen, nicht geraten.** Welche Ereignisse eine
  Instanz kann, sagt ihr eigener Bauplan (``notification_schema``); fehlt
  dort Pflichtwerk, gilt sie als zu alt - mit Ansage statt stillem Versagen.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy.orm import Session

from ..models import ArrWebhook, utcnow
from . import webhooks
from .arr import ArrClient, ArrError
from .settings_service import AppSettings, ArrInstanz

logger = logging.getLogger("nexview.webhooks")

EINTRAG_NAME = "Nexview"

# Wie lange auf die Probe gewartet wird. Sonarr schickt sie sofort; laenger
# als ein paar Sekunden heisst praktisch immer "kommt nie an".
BEWEIS_WARTEZEIT_SEKUNDEN = 5.0
BEWEIS_SCHRITT_SEKUNDEN = 0.25

# Ereignis-Flaggen je Dienst. PFLICHT: Ohne sie kann der Rueckkanal seinen
# Zweck nicht erfuellen (fertig, aufgewertet, geloescht) - fehlt eine im
# Bauplan der Instanz, gilt sie als zu alt. WUENSCHENSWERT wird abonniert,
# wenn die Instanz es kann: Download-Beginn ("laedt gerade", Stufe 4),
# Gesundheit und "manuelles Eingreifen noetig" (Stufe 5). Von Anfang an
# abonniert, damit spaeter keine Nachpflege in Radarr/Sonarr noetig ist -
# der Empfaenger weckt bei jedem Ereignis, mehr muss er nicht koennen.
PFLICHT: dict[str, tuple[str, ...]] = {
    "movie": ("onDownload", "onUpgrade", "onMovieDelete", "onMovieFileDelete"),
    "tv": ("onDownload", "onUpgrade", "onSeriesDelete", "onEpisodeFileDelete"),
}
WUENSCHENSWERT: tuple[str, ...] = (
    "onGrab",
    "onHealthIssue",
    "onHealthRestored",
    "onManualInteractionRequired",
)


def anruf_pfad(kennung: str) -> str:
    """Der Pfad, unter dem diese Instanz bei uns anruft (routers/webhooks)."""
    return f"/api/webhooks/arr/{kennung}"


def _ziel(settings: AppSettings, kennung: str) -> str:
    basis = settings.webhook_basis
    return f"{basis}{anruf_pfad(kennung)}" if basis else ""


def _client(instanz: ArrInstanz) -> ArrClient:
    return ArrClient(instanz.url, instanz.api_key, instanz.name)


def _bauen(
    schema: dict, ziel: str, geheimnis: str, media_type: str
) -> tuple[dict, list[str]]:
    """Den gewuenschten Eintrag bauen - und sagen, welche Pflicht fehlt.

    Die Feld- und Flaggen-Namen stammen aus dem live gemessenen Bauplan
    (27.08.2026, ``schema_webhook_*.json`` beim Bauplan "Draht statt Takt"):
    ``method`` ist ein Auswahlfeld, POST traegt den Wert 1. Das Geheimnis
    faehrt als Basic-Passwort mit - diese Felder gibt es in jeder Fassung,
    anders als die erst spaeter dazugekommenen eigenen Kopfzeilen.
    """
    flaggen: dict[str, bool] = {}
    fehlend: list[str] = []
    for flagge in PFLICHT[media_type]:
        unterstuetzt = f"supports{flagge[0].upper()}{flagge[1:]}"
        if schema.get(unterstuetzt):
            flaggen[flagge] = True
        else:
            fehlend.append(flagge)
    for flagge in WUENSCHENSWERT:
        unterstuetzt = f"supports{flagge[0].upper()}{flagge[1:]}"
        if schema.get(unterstuetzt):
            flaggen[flagge] = True

    payload = {
        "name": EINTRAG_NAME,
        "implementation": "Webhook",
        "configContract": "WebhookSettings",
        "tags": [],
        "fields": [
            {"name": "url", "value": ziel},
            {"name": "method", "value": 1},
            {"name": "username", "value": "nexview"},
            {"name": "password", "value": geheimnis},
        ],
        **flaggen,
    }
    return payload, fehlend


def _gehoert_uns(eintrag: dict, kennung: str) -> bool:
    """Nur was zweifelsfrei unseres ist.

    Der Name allein reicht nicht - "Nexview" kann jeder vergeben. Erst Name
    **und** unsere Anruf-Adresse (der Pfad traegt die Kennung) machen einen
    Eintrag unser; alles andere bleibt unangetastet, komme es von Ruddarr
    oder von Hand.
    """
    if eintrag.get("implementation") != "Webhook":
        return False
    if str(eintrag.get("name") or "") != EINTRAG_NAME:
        return False
    url = next(
        (
            feld.get("value")
            for feld in eintrag.get("fields") or []
            if feld.get("name") == "url"
        ),
        "",
    )
    return isinstance(url, str) and url.rstrip("/").endswith(anruf_pfad(kennung))


def _weicht_ab(eigener: dict, gewuenscht: dict) -> bool:
    """Muss nachgezogen werden?

    Das Passwort wird bewusst nicht verglichen: Nicht jede Fassung liefert
    Geheimnisse zurueck, und ein Scheinunterschied wuerde stuendlich einen
    Schreibzugriff ausloesen. Ein wirklich verstelltes Passwort faellt ueber
    die fehlgeschlagene Probe auf - und wird dort geheilt.
    """
    ist_felder = {
        feld.get("name"): feld.get("value") for feld in eigener.get("fields") or []
    }
    for feld in gewuenscht["fields"]:
        if feld["name"] == "password":
            continue
        if ist_felder.get(feld["name"]) != feld["value"]:
            return True
    return any(
        bool(eigener.get(flagge)) != wert
        for flagge, wert in gewuenscht.items()
        if flagge.startswith("on")
    )


def _stand(db: Session, zeile: ArrWebhook, code: str, info: str = "") -> None:
    """Fehlerstand setzen (oder mit ``""`` loeschen) und sichern."""
    zeile.fehler = code
    zeile.fehler_info = (info or "")[:200]
    db.commit()


async def _beweis_abwarten(db: Session, kennung: str, seit) -> bool:
    """Kam die Probe an? Der Empfaenger setzt ``bewiesen_am`` - hier wird nur
    kurz darauf gewartet. Die Schreibseite lebt in einer anderen Sitzung,
    deshalb vor jedem Blick ``expire_all``."""
    schritte = int(BEWEIS_WARTEZEIT_SEKUNDEN / BEWEIS_SCHRITT_SEKUNDEN)
    for _ in range(schritte):
        await asyncio.sleep(BEWEIS_SCHRITT_SEKUNDEN)
        db.expire_all()
        zeile = webhooks.eintrag(db, kennung)
        bewiesen = zeile.bewiesen_am if zeile else None
        if bewiesen is not None:
            if bewiesen.tzinfo is not None:
                bewiesen = bewiesen.replace(tzinfo=None)
            if bewiesen > seit:
                return True
    return False


async def instanz_pflegen(
    db: Session, settings: AppSettings, instanz: ArrInstanz
) -> ArrWebhook:
    """Eine Instanz in Ordnung bringen - anlegen, nachziehen oder aufraeumen."""
    zeile = webhooks.eintrag_sicherstellen(db, instanz.kennung)
    zeile.geprueft_am = utcnow()
    client = _client(instanz)

    try:
        vorhandene = await client.notifications()
    except ArrError as fehler:
        _stand(db, zeile, "unreachable", fehler.message)
        return zeile

    eigener = None
    if zeile.eintrag_id is not None:
        eigener = next(
            (
                eintrag
                for eintrag in vorhandene
                if eintrag.get("id") == zeile.eintrag_id
                and eintrag.get("implementation") == "Webhook"
            ),
            None,
        )
    if eigener is None:
        eigener = next(
            (
                eintrag
                for eintrag in vorhandene
                if _gehoert_uns(eintrag, instanz.kennung)
            ),
            None,
        )

    if not zeile.aktiv:
        # Abgewaehlt: rueckstandsfrei aufraeumen - der Eintrag verschwindet
        # aus Radarr/Sonarr, nicht nur aus unserer Betrachtung.
        if eigener is not None:
            try:
                await client.notification_loeschen(int(eigener["id"]))
            except ArrError as fehler:
                _stand(db, zeile, "unreachable", fehler.message)
                return zeile
            logger.info(
                "Webhook entry removed from %s (switched off)", instanz.name
            )
        zeile.eintrag_id = None
        zeile.eingetragen_am = None
        _stand(db, zeile, "")
        return zeile

    ziel = _ziel(settings, instanz.kennung)
    if not ziel:
        _stand(db, zeile, "no_address")
        return zeile

    try:
        schema = await client.notification_schema_webhook()
    except ArrError as fehler:
        _stand(db, zeile, "unreachable", fehler.message)
        return zeile
    if schema is None:
        _stand(db, zeile, "too_old", "no webhook notification type")
        return zeile

    payload, fehlend = _bauen(
        schema, ziel, webhooks.geheimnis_klartext(zeile), instanz.media_type
    )
    if fehlend:
        _stand(db, zeile, "too_old", "missing: " + ", ".join(fehlend))
        return zeile

    if eigener is None:
        # Erst der Beweis, dann der Eintrag.
        seit = utcnow().replace(tzinfo=None)
        db.commit()
        try:
            await client.notification_probe(payload)
        except ArrError as fehler:
            _stand(db, zeile, "proof_failed", fehler.message)
            return zeile
        if not await _beweis_abwarten(db, instanz.kennung, seit):
            logger.warning(
                "Webhook proof for %s never arrived at %s - entry not created",
                instanz.name,
                ziel,
            )
            _stand(db, zeile, "proof_failed")
            return zeile
        try:
            angelegt = await client.notification_anlegen(payload)
        except ArrError as fehler:
            _stand(db, zeile, "create_failed", fehler.message)
            return zeile
        zeile.eintrag_id = angelegt.get("id") if isinstance(angelegt, dict) else None
        zeile.eingetragen_am = utcnow()
        _stand(db, zeile, "")
        logger.info("Webhook registered in %s -> %s", instanz.name, ziel)
        return zeile

    # Vorhanden: Nummer merken und nachziehen, wenn etwas abweicht.
    zeile.eintrag_id = int(eigener["id"])
    if zeile.eingetragen_am is None:
        zeile.eingetragen_am = utcnow()
    if _weicht_ab(eigener, payload):
        try:
            await client.notification_nachziehen(int(eigener["id"]), payload)
        except ArrError as fehler:
            _stand(db, zeile, "create_failed", fehler.message)
            return zeile
        logger.info("Webhook entry in %s brought up to date", instanz.name)
    _stand(db, zeile, "")
    return zeile


async def pflegen(db: Session, settings: AppSettings) -> None:
    """Alle Instanzen einmal durchgehen - Fehler je Instanz, nie im Ganzen."""
    for instanz in settings.arr_instanzen():
        try:
            await instanz_pflegen(db, settings, instanz)
        except Exception:  # noqa: BLE001 - eine Instanz darf die anderen nicht mitnehmen
            logger.exception("Webhook upkeep for %s failed", instanz.name)
            db.rollback()


async def testen(db: Session, settings: AppSettings, instanz: ArrInstanz) -> dict:
    """Der Testen-Knopf: die Instanz **jetzt** einmal anrufen lassen.

    Beweist die ganze Strecke in beide Richtungen - Nexview bittet die
    Instanz um die Probe, die Instanz ruft an, der Empfaenger setzt
    ``bewiesen_am``. Zurueck kommt, ob und wie schnell der Anruf ankam,
    oder woran es haengt (dieselben Kennungen wie in der Pflege).
    """
    zeile = webhooks.eintrag_sicherstellen(db, instanz.kennung)
    ziel = _ziel(settings, instanz.kennung)
    if not ziel:
        return {"angekommen": False, "fehler": "no_address"}

    client = _client(instanz)
    try:
        schema = await client.notification_schema_webhook()
    except ArrError as fehler:
        return {"angekommen": False, "fehler": "unreachable", "info": fehler.message}
    if schema is None:
        return {"angekommen": False, "fehler": "too_old"}
    payload, fehlend = _bauen(
        schema, ziel, webhooks.geheimnis_klartext(zeile), instanz.media_type
    )
    if fehlend:
        return {"angekommen": False, "fehler": "too_old", "info": ", ".join(fehlend)}

    # ⚠️ Existiert unser Eintrag schon, faehrt seine Nummer in der Probe mit.
    # Sonarr prueft die Probe wie ein Speichern - ohne Nummer hielte es den
    # gleichnamigen Bestand fuer ein Duplikat und antwortete mit 400, statt
    # anzurufen. Live so gesehen, nachdem der erste Beweis laengst stand.
    try:
        vorhandene = await client.notifications()
    except ArrError as fehler:
        return {"angekommen": False, "fehler": "unreachable", "info": fehler.message}
    eigener = None
    if zeile.eintrag_id is not None:
        eigener = next(
            (
                eintrag
                for eintrag in vorhandene
                if eintrag.get("id") == zeile.eintrag_id
                and eintrag.get("implementation") == "Webhook"
            ),
            None,
        )
    if eigener is None:
        eigener = next(
            (eintrag for eintrag in vorhandene if _gehoert_uns(eintrag, instanz.kennung)),
            None,
        )
    if eigener is not None:
        payload = {**payload, "id": int(eigener["id"])}

    seit = utcnow().replace(tzinfo=None)
    db.commit()
    start = time.monotonic()
    try:
        await client.notification_probe(payload)
    except ArrError as fehler:
        return {"angekommen": False, "fehler": "proof_failed", "info": fehler.message}
    if await _beweis_abwarten(db, instanz.kennung, seit):
        return {
            "angekommen": True,
            "dauer_ms": int((time.monotonic() - start) * 1000),
        }
    return {"angekommen": False, "fehler": "proof_failed"}


PFLEGE_INTERVALL_SEKUNDEN = 3600.0
_zuletzt = 0.0


def gleich_wieder() -> None:
    """Beim naechsten Rundgang pflegen, nicht erst zur vollen Stunde.

    Gerufen nach dem Speichern der Dienste-Einstellungen. Der Endpunkt dort
    ist **synchron** und laeuft im Thread-Pool - von dort darf kein
    asyncio-Signal angefasst werden. Deshalb nur diese gefahrlose Markierung;
    der Takt nimmt sie binnen zwei Minuten auf.
    """
    global _zuletzt
    _zuletzt = 0.0


async def vielleicht_pflegen(db: Session, settings: AppSettings) -> None:
    """Im Takt aufgerufen: stuendlich, beim Start und nach ``gleich_wieder``."""
    global _zuletzt
    if not settings.arr_instanzen():
        return
    jetzt = time.monotonic()
    if _zuletzt and jetzt - _zuletzt < PFLEGE_INTERVALL_SEKUNDEN:
        return
    _zuletzt = jetzt
    await pflegen(db, settings)
