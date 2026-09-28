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
  andere Anwendungen (live gesehen: "Ruddarr") - und womoeglich eine zweite
  Nexview. Unser ist nur ein Eintrag, der **uns** anruft; welcher das ist,
  beantwortet ``unser_eintrag`` und sonst nichts. Name und Nummer allein
  reichen nicht.
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
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from ....models import ArrWebhook, utcnow
from ...settings_service import AppSettings, ArrInstanz
from . import webhooks
from .client import ArrClient, ArrError

logger = logging.getLogger("nexview.webhooks")

#: Der Name, unter dem bis 1.0.0 **jede** Nexview in Radarr/Sonarr stand.
#: Er wird nicht mehr vergeben, nur noch an einem Eintrag uebernommen, der
#: nachweislich uns anruft (dann bekommt er den eindeutigen Namen).
ALTER_NAME = "Nexview"

# Wie lange auf die Probe gewartet wird. Sonarr schickt sie sofort; laenger
# als ein paar Sekunden heisst praktisch immer "kommt nie an".
BEWEIS_WARTEZEIT_SEKUNDEN = 5.0
BEWEIS_SCHRITT_SEKUNDEN = 0.25

# Wie lange die Frage an eine fruehere eigene Adresse dauern darf. Knapp: Sie
# laeuft im Rundgang, und eine Antwort, die so lange braucht, gilt als unklar.
ALTE_ADRESSE_ZEITGRENZE_SEKUNDEN = 3.0

# ⚠️ Eine abgewiesene Verbindung allein beweist keinen Tod: Genauso sieht eine
# gesunde fremde Nexview aus, die gerade neu startet (Docker-Update,
# Host-Neustart). Geloescht wird erst, wenn die alte Adresse in zwei
# Pflegelaeufen mit mindestens diesem Abstand tot war - ohne dass dazwischen
# eine Nexview geantwortet hat oder das Ergebnis unklar war.
ALTE_ADRESSE_TOT_ABSTAND = timedelta(minutes=30)

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


def eintrag_name(basis: str) -> str:
    """Der Name unseres Eintrags: "Nexview" und die Adresse, unter der wir angerufen werden.

    ⚠️ **Eindeutig je Installation, nicht fest.** Radarr und Sonarr verlangen
    je Instanz verschiedene Namen und weisen einen vergebenen mit 400 ab -
    auch schon bei der Probe. Mit dem festen Namen "Nexview" konnte deshalb
    keine zweite Nexview an derselben Instanz einen eigenen Eintrag anlegen.
    Die Anruf-Adresse unterscheidet zwei Installationen ohnehin:
    Zwei mit derselben waeren derselbe Empfaenger. Sie steht ohne Schema im
    Namen, damit der Betreiber in Radarr sieht, welcher Eintrag wohin ruft.
    """
    gekuerzt = basis.strip().rstrip("/")
    teile = urlsplit(gekuerzt)
    ort = f"{teile.netloc}{teile.path}" if teile.netloc else gekuerzt
    return f"{ALTER_NAME} ({ort})" if ort else ALTER_NAME


def _bauen(
    schema: dict, ziel: str, geheimnis: str, media_type: str, name: str
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
        "name": name,
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


def _glatt(url: str) -> str:
    return url.strip().rstrip("/").casefold()


def _adresse(eintrag: dict) -> str:
    """Die Anruf-Adresse eines Eintrags, zum Vergleichen geglaettet."""
    url = next(
        (
            feld.get("value")
            for feld in eintrag.get("fields") or []
            if feld.get("name") == "url"
        ),
        "",
    )
    return _glatt(url) if isinstance(url, str) else ""


def unser_eintrag(
    vorhandene: list[dict], zeile: ArrWebhook, ziel: str, name: str
) -> dict | None:
    """Welcher Eintrag in Radarr/Sonarr ist unserer? ``None``, wenn keiner.

    ⚠️ **Die eine Stelle fuer diese Frage** - Pflege, Abwaehlen, Umstieg und
    Testen-Knopf fragen alle hier. Unser ist ein Webhook-Eintrag nur, wenn er
    **uns** anruft: Er traegt unsere heutige Anruf-Adresse ``ziel`` und dazu
    unseren Namen, den alten Namen "Nexview" oder unsere gemerkte Nummer.

    Was hier fehlt, fehlt mit Absicht: Der Pfad
    (``/api/webhooks/arr/radarr-standard``) ist bei jeder Nexview derselbe,
    der alte Name war es auch, und die gemerkte Nummer zeigt womoeglich auf
    einen Eintrag, den inzwischen eine andere Nexview beschrieben hat. Mit
    genau diesen drei Merkmalen hat der Umstieg einer Installation den
    Eintrag einer anderen geloescht.

    ⚠️ **Auch die Adresse, die wir selbst zuletzt hineingeschrieben haben,
    macht einen Eintrag nicht zu unserem.** Wer das Datenverzeichnis einer
    Nexview kopiert, um eine zweite aufzusetzen, gibt ihr Nummer und Adresse
    der ersten mit - und die zweite schriebe deren lebenden Eintrag auf sich
    um. Ein Eintrag mit einer anderen Adresse als unserer heutigen wird
    deshalb nie umgeschrieben; ``_frueheren_eintrag_pflegen`` entscheidet,
    ob er weg darf.
    """
    soll = _glatt(ziel)
    namen = {ALTER_NAME.casefold(), name.casefold()}
    passend: list[dict] = []
    for eintrag in vorhandene:
        if eintrag.get("implementation") != "Webhook":
            continue
        adresse = _adresse(eintrag)
        if not adresse:
            continue
        nummer_stimmt = zeile.eintrag_id is not None and eintrag.get("id") == zeile.eintrag_id
        name_stimmt = str(eintrag.get("name") or "").casefold() in namen
        if soll and adresse == soll and (nummer_stimmt or name_stimmt):
            passend.append(eintrag)
    # Die gemerkte Nummer zuerst: Stehen zwei Eintraege auf unserer Adresse,
    # bleibt der, den wir schon kennen.
    passend.sort(key=lambda eintrag: eintrag.get("id") != zeile.eintrag_id)
    return passend[0] if passend else None


def _basis_aus(url: str, kennung: str) -> str:
    """Die Basis einer Anruf-Adresse, ``""``, wenn sie nicht unsere Form hat."""
    gekuerzt = url.strip().rstrip("/")
    pfad = anruf_pfad(kennung)
    return gekuerzt[: -len(pfad)] if gekuerzt.endswith(pfad) else ""


async def _gesundheit_holen(url: str) -> httpx.Response:
    """Eine einzelne Frage mit eigenem, kurzlebigem Client (wie
    ``routers/settings.test_public_url``). Umleitungen werden nicht verfolgt:
    Eine Anmeldeseite davor waere keine Antwort auf die Frage."""
    async with httpx.AsyncClient(timeout=ALTE_ADRESSE_ZEITGRENZE_SEKUNDEN) as client:
        return await client.get(url)


def _abgewiesen(fehler: BaseException) -> bool:
    """Hat die Gegenstelle die Verbindung abgewiesen (dort lauscht nichts)?

    Nur das ist ein Beweis. Ein unbekannter Name, eine fremde Route oder ein
    Zertifikatsfehler sagt nur, dass **wir** die Adresse nicht erreichen -
    Radarr womoeglich schon.
    """
    gesehen: set[int] = set()
    kette: BaseException | None = fehler
    while kette is not None and id(kette) not in gesehen:
        if isinstance(kette, ConnectionRefusedError):
            return True
        gesehen.add(id(kette))
        kette = kette.__cause__ or kette.__context__
    return False


async def nexview_unter(basis: str) -> bool | None:
    """Antwortet unter dieser Adresse eine Nexview?

    ``True`` ja, ``False`` nachweislich nicht (Verbindung abgewiesen, oder
    ``/api/health`` gibt es dort nicht), ``None`` unklar: Zeitueberschreitung,
    Name nicht aufloesbar, 5xx eines Proxys, eine Anmeldeseite. Unklar heisst
    fuer den Aufrufer immer: stehen lassen.
    """
    try:
        antwort = await _gesundheit_holen(f"{basis.rstrip('/')}/api/health")
    except httpx.HTTPError as fehler:
        return False if _abgewiesen(fehler) else None
    if antwort.status_code in (404, 410):
        return False
    if antwort.status_code != 200:
        return None
    try:
        daten = antwort.json()
    except ValueError:
        return None
    return True if isinstance(daten, dict) and daten.get("status") == "ok" else None


def _frueheren_merken(vorhandene: list[dict], zeile: ArrWebhook, eigener: dict | None) -> None:
    """Steht unter unserer gemerkten Nummer noch ein Eintrag mit der Adresse,
    die wir zuletzt hineingeschrieben haben, aber nicht unserer heutigen?

    Dann hat sich unsere Adresse geaendert - oder diese Datenbank ist die
    Kopie einer anderen Installation, die unter der alten Adresse weiterlebt.
    Von hier aus ist beides gleich; der Eintrag wird als frueherer gemerkt,
    und die gemerkte Nummer gilt nicht mehr als unsere.
    """
    if zeile.eintrag_id is None or (eigener is not None and eigener.get("id") == zeile.eintrag_id):
        return
    gemerkt = _glatt(zeile.eintrag_url or "")
    frueher = next(
        (
            eintrag
            for eintrag in vorhandene
            if eintrag.get("implementation") == "Webhook"
            and eintrag.get("id") == zeile.eintrag_id
            and gemerkt
            and _adresse(eintrag) == gemerkt
        ),
        None,
    )
    if frueher is not None:
        zeile.alter_eintrag_id = int(frueher["id"])
        zeile.alter_eintrag_url = zeile.eintrag_url
        zeile.alte_adresse_tot_seit = None
    zeile.eintrag_id = None
    zeile.eintrag_url = None
    zeile.eingetragen_am = None


async def _frueheren_eintrag_pflegen(
    client: ArrClient,
    zeile: ArrWebhook,
    vorhandene: list[dict],
    eigener: dict | None,
    kennung: str,
) -> None:
    """Einen frueheren eigenen Eintrag entfernen - nur, wenn dort nachweislich
    keine Nexview mehr antwortet.

    ⚠️ **Umgeschrieben wird er nie**, auch nicht auf unsere neue Adresse: Er
    kann einer Installation gehoeren, die aus einer Kopie unserer Datenbank
    entstanden ist oder aus der wir entstanden sind. Antwortet unter seiner
    Adresse eine Nexview, oder laesst sich das nicht klaeren, bleibt er
    stehen, und die Diensteseite nennt ihn dem Betreiber. Tot heisst: zweimal,
    mit mindestens ``ALTE_ADRESSE_TOT_ABSTAND`` dazwischen, und jede andere
    Antwort dazwischen faengt die Zaehlung von vorn an.
    """
    if zeile.alter_eintrag_id is None:
        return
    gemerkt = _glatt(zeile.alter_eintrag_url or "")
    alt = next(
        (
            eintrag
            for eintrag in vorhandene
            if eintrag.get("implementation") == "Webhook"
            and eintrag.get("id") == zeile.alter_eintrag_id
            and gemerkt
            and _adresse(eintrag) == gemerkt
        ),
        None,
    )
    if alt is None or (eigener is not None and eigener.get("id") == alt.get("id")):
        # Weg, von jemandem umgeschrieben oder wieder unserer: nichts mehr zu merken.
        zeile.alter_eintrag_id = None
        zeile.alter_eintrag_url = None
        zeile.alte_adresse_tot_seit = None
        return
    basis = _basis_aus(zeile.alter_eintrag_url or "", kennung)
    if not basis or await nexview_unter(basis) is not False:
        zeile.alte_adresse_tot_seit = None
        return
    jetzt = utcnow().replace(tzinfo=None)
    zuerst = zeile.alte_adresse_tot_seit
    if zuerst is not None and zuerst.tzinfo is not None:
        zuerst = zuerst.replace(tzinfo=None)
    if zuerst is None:
        zeile.alte_adresse_tot_seit = jetzt
        return
    if jetzt - zuerst < ALTE_ADRESSE_TOT_ABSTAND:
        return  # Noch derselbe Befund, kein zweiter.
    try:
        await client.notification_loeschen(int(alt["id"]))
    except ArrError:
        return  # Beim naechsten Rundgang noch einmal.
    logger.info("Webhook entry for a former address removed, nothing answers at %s", basis)
    zeile.alter_eintrag_id = None
    zeile.alter_eintrag_url = None
    zeile.alte_adresse_tot_seit = None


def _weicht_ab(eigener: dict, gewuenscht: dict) -> bool:
    """Muss nachgezogen werden?

    Das Passwort wird bewusst nicht verglichen: Nicht jede Fassung liefert
    Geheimnisse zurueck, und ein Scheinunterschied wuerde stuendlich einen
    Schreibzugriff ausloesen. Ein wirklich verstelltes Passwort faellt ueber
    die fehlgeschlagene Probe auf - und wird dort geheilt.
    """
    # Der Name zaehlt mit: Ein Eintrag aus der Zeit vor 1.0.0 heisst noch
    # "Nexview" und bekaeme sonst nie seinen eindeutigen Namen.
    if eigener.get("name") != gewuenscht["name"]:
        return True
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

    ziel = _ziel(settings, instanz.kennung)
    name = eintrag_name(settings.webhook_basis)
    eigener = unser_eintrag(vorhandene, zeile, ziel, name)
    _frueheren_merken(vorhandene, zeile, eigener)
    await _frueheren_eintrag_pflegen(client, zeile, vorhandene, eigener, instanz.kennung)

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
        zeile.eintrag_url = None
        zeile.eingetragen_am = None
        _stand(db, zeile, "")
        return zeile

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
        schema, ziel, webhooks.geheimnis_klartext(zeile), instanz.media_type, name
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
        zeile.eintrag_url = ziel
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
    zeile.eintrag_url = ziel
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
    name = eintrag_name(settings.webhook_basis)
    payload, fehlend = _bauen(
        schema, ziel, webhooks.geheimnis_klartext(zeile), instanz.media_type, name
    )
    if fehlend:
        return {"angekommen": False, "fehler": "too_old", "info": ", ".join(fehlend)}

    # ⚠️ Existiert unser Eintrag schon, faehrt seine Nummer in der Probe mit.
    # Sonarr prueft die Probe wie ein Speichern - ohne Nummer hielte es den
    # gleichnamigen Bestand fuer ein Duplikat und antwortete mit 400, statt
    # anzurufen. Live so gesehen, nachdem der erste Beweis laengst stand.
    # Nur **unsere** Nummer, nie die eines fremden Eintrags.
    try:
        vorhandene = await client.notifications()
    except ArrError as fehler:
        return {"angekommen": False, "fehler": "unreachable", "info": fehler.message}
    eigener = unser_eintrag(vorhandene, zeile, ziel, name)
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
        dauer_ms = int((time.monotonic() - start) * 1000)
        _widerlegtes_loeschen(db, instanz.kennung)
        return {"angekommen": True, "dauer_ms": dauer_ms}
    return {"angekommen": False, "fehler": "proof_failed"}


# Gruende, die ein angekommener Anruf widerlegt: Die Adresse steht, die
# Instanz antwortet, der Anruf kam an. "too_old" und "create_failed" bleiben -
# die Probe legt keinen Eintrag an und sagt nichts ueber dessen Pflichtfelder.
WIDERLEGT = frozenset({"no_address", "unreachable", "proof_failed"})


def _widerlegtes_loeschen(db: Session, kennung: str) -> None:
    """Nach einem angekommenen Anruf den veralteten Grund wegnehmen.

    ⚠️ Bis 28.09.2026 blieb er stehen, bis die Pflege das naechste Mal lief
    (nach dem Speichern binnen zwei Minuten, sonst stuendlich): Die Seite
    zeigte "Es fehlt eine Adresse" direkt ueber "Anruf kam an". Die Pflege
    kommt trotzdem bald, damit der Eintrag in der Instanz nachzieht.
    """
    db.expire_all()
    zeile = webhooks.eintrag(db, kennung)
    if zeile is not None and zeile.fehler in WIDERLEGT:
        _stand(db, zeile, "")
    gleich_wieder()


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
