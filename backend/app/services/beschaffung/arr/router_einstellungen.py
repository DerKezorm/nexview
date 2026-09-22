"""Einstellungen, die nur Radarr und Sonarr betreffen. Nur fuer Administratoren.

Umgezogen aus ``routers/settings.py`` (Scheibe 2 des NEX-Umbaus), unter
denselben Pfaden und ohne Aenderung im Verhalten: Verbindung pruefen,
Papierkorb der Instanzen, Rueckkanal (Webhooks), Verbindung, Gesundheit und
Download-Kollision je Instanz, Instanz entfernen.

⚠️ **Eingebunden nach ``routers/settings``.** ``/settings/test/{service}``
darf ``/settings/test/tmdb`` und ``/settings/test/smtp`` nicht verdecken; die
stehen dort und werden deshalb zuerst gefunden.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field

from .... import meldungen
from ....deps import AdminUser, DbSession
from ....routers.settings import ConnectionTest, TestResult
from ...settings_service import (
    clear_secret,
    load_settings,
    public_settings,
    save_settings,
)
from . import (
    download_kollision,
    instanz_gesundheit,
    library,
    webhook_pflege,
    webhooks,
)
from .client import ArrClient, ArrError
from .radarr import RadarrClient
from .sonarr import SonarrClient

router = APIRouter(prefix="/api", tags=["settings"])

logger = logging.getLogger("nexview.settings")


@router.post("/settings/test/{service}", response_model=TestResult)
async def test_arr(
    service: Annotated[
        Literal["radarr", "sonarr", "radarr_uhd", "sonarr_uhd"], Path()
    ],
    payload: ConnectionTest,
    admin: AdminUser,
    db: DbSession,
) -> TestResult:
    """Verbindung zu Radarr bzw. Sonarr pruefen - Standard- oder 4K-Instanz.

    Nutzt die uebergebenen Daten, falls sie noch nicht gespeichert sind -
    so kann man testen, bevor man speichert.
    """
    settings = load_settings(db)
    is_radarr = service.startswith("radarr")
    tier = "uhd" if service.endswith("_uhd") else "standard"
    label = ("Radarr" if is_radarr else "Sonarr") + (" 4K" if tier == "uhd" else "")

    gespeicherte_url, gespeicherter_key = settings.arr_endpoint(
        "movie" if is_radarr else "tv", tier
    )
    url = (payload.url or "").strip() or gespeicherte_url
    api_key = (payload.api_key or "").strip()
    if not api_key or api_key.startswith("•"):
        api_key = gespeicherter_key

    if not url or not api_key:
        return TestResult(ok=False, message=f"Adresse und API-Key für {label} fehlen noch.")

    client = RadarrClient(url, api_key) if is_radarr else SonarrClient(url, api_key)
    try:
        info = await client.system_status()
    except ArrError as error:
        return TestResult(ok=False, message=error.message)

    # Verwechslung der beiden Adressen ist der haeufigste Einrichtungsfehler.
    reported = str(info.get("appName") or "").lower()
    erwartet = "radarr" if is_radarr else "sonarr"
    if reported and reported != erwartet:
        return TestResult(
            ok=False,
            message=f"Unter dieser Adresse antwortet {info.get('appName')}, nicht {label}.",
        )

    version = info.get("version")
    suffix = f" (Version {version})" if version else ""
    return TestResult(ok=True, message=f"Verbindung zu {label} erfolgreich{suffix}.")


class PapierkorbInstanz(BaseModel):
    """Wie eine einzelne Instanz beim Loeschen mit Dateien umgeht."""

    media_type: str
    tier: str
    # "Radarr", "Radarr 4K", "Sonarr", "Sonarr 4K"
    name: str
    # ⚠️ Drei Zustaende, nicht zwei: "nicht erreichbar" ist etwas anderes als
    # "kein Papierkorb". Wer beides gleich behandelt, meldet einen Fehlalarm,
    # sobald Radarr gerade neu startet.
    reachable: bool
    path: str
    cleanup_days: int | None
    protected: bool


class PapierkorbStand(BaseModel):
    """Der Papierkorb aller eingerichteten Instanzen auf einen Blick."""

    # Der abgeleitete Haken: an, wenn **jede** eingerichtete Instanz einen
    # Papierkorb hat. Bewusst gerechnet und nicht gespeichert - so kann er nicht
    # von der Wirklichkeit abweichen, und er kippt von selbst, sobald eine neue
    # Instanz ohne Papierkorb dazukommt.
    enabled: bool
    # Konnte ueberhaupt jede Instanz gefragt werden? Ist das falsch, ist
    # ``enabled`` eine Aussage ueber unvollstaendige Auskunft - die Oberflaeche
    # sagt dann "unbekannt" statt "aus".
    complete: bool
    instances: list[PapierkorbInstanz]


@router.get("/settings/recyclebin", response_model=PapierkorbStand)
async def papierkorb_stand(admin: AdminUser, db: DbSession) -> PapierkorbStand:
    """Wo landen geloeschte Dateien - in jeder eingerichteten Instanz?

    **Der Stand wird bei jedem Aufruf frisch geholt und nirgends gespeichert.**
    Er steht in Radarr bzw. Sonarr, und nur dort; wuerde Nexview ihn
    aufbewahren, liefen die beiden auseinander, sobald jemand ihn drueben
    aendert - und dann hielte Nexview eine Loeschung fuer umkehrbar, die es
    nicht ist.

    Damit ueberlebt die Logik auch das **Hinzufuegen** einer Instanz: Wer
    naechste Woche ein zweites Sonarr eintraegt, findet es hier ohne Zutun, und
    ohne Papierkorb faellt ``enabled`` von selbst auf falsch. Genau das soll es:
    Eine neue Instanz ist eine neue Stelle, an der geloescht wird.
    """
    staende = await library.papierkoerbe(load_settings(db))
    instanzen = [
        PapierkorbInstanz(
            media_type=art,
            tier=stufe,
            name=name,
            reachable=stand.erreichbar,
            path=stand.path,
            cleanup_days=stand.cleanup_days,
            protected=stand.geschuetzt,
        )
        for art, stufe, name, stand in staende
    ]
    return PapierkorbStand(
        # Ohne eine einzige Instanz gibt es nichts zu schuetzen - und "an"
        # waere dann eine Behauptung ueber das Nichts.
        enabled=bool(instanzen) and all(zeile.protected for zeile in instanzen),
        complete=all(zeile.reachable for zeile in instanzen),
        instances=instanzen,
    )


class PapierkorbWunsch(BaseModel):
    """Was fuer **eine** Instanz eingestellt werden soll."""

    media_type: Literal["movie", "tv"]
    tier: Literal["standard", "uhd"]
    # Leer heisst: Papierkorb abschalten. Das ist die gefaehrliche Richtung -
    # ab dann loescht die Instanz sofort und endgueltig.
    path: str = ""


class PapierkorbAenderung(BaseModel):
    """Der ganze Abschnitt auf einmal - alle Instanzen in einem Zug.

    Bewusst nicht je Instanz einzeln: Halb geschuetzt ist kein Zustand, den
    jemand absichtlich haben will, und wer vier Knoepfe nacheinander drueckt,
    hat zwischendurch genau das. Ein Speichern-Knopf, ein Ergebnis.
    """

    instances: list[PapierkorbWunsch]
    # Global, eine Zahl fuer alle. Mindestens ein Tag: Ob Radarr die Null als
    # "nie aufraeumen" oder "sofort" versteht, ist nicht dokumentiert - und bei
    # einem Papierkorb ist diese Verwechslung fatal.
    cleanup_days: Annotated[int, Field(ge=1, le=365)] = 7


class PapierkorbOrdner(BaseModel):
    """Eine Ebene der Ordner-Auswahl, aus Sicht **einer** Instanz."""

    path: str
    directories: list[str]


@router.get("/settings/recyclebin/folders", response_model=PapierkorbOrdner)
async def papierkorb_ordner(
    admin: AdminUser,
    db: DbSession,
    media_type: Literal["movie", "tv"],
    tier: Literal["standard", "uhd"] = "standard",
    path: str = "/",
) -> PapierkorbOrdner:
    """Welche Ordner sieht **diese** Instanz unter diesem Pfad?

    Je Instanz gefragt und nicht einmal fuer alle: Sonarr kann voellig anders
    eingebunden sein als Radarr. Ein geratener Pfad fuehrt dazu, dass die
    Instanz spaeter an eine Stelle loescht, die es bei ihr gar nicht gibt.
    """
    ordner = await library.ordner(load_settings(db), media_type, tier, path)
    return PapierkorbOrdner(path=path, directories=ordner)


@router.put("/settings/recyclebin", response_model=PapierkorbStand)
async def papierkorb_setzen(
    aenderung: PapierkorbAenderung, admin: AdminUser, db: DbSession
) -> PapierkorbStand:
    """Papierkorb in allen genannten Instanzen eintragen.

    ⚠️ **Das schreibt in Radarr und Sonarr, nicht in Nexview.** Die Einstellung
    gilt dort fuer **alles**, auch fuer Loeschungen, die nichts mit Nexview zu
    tun haben - wer einen Film von Hand in Radarr entfernt, findet ihn ab dann
    ebenfalls im Korb. Das ist sicherer, aber es ist eine Verhaltensaenderung
    am fremden Dienst, und die Oberflaeche sagt das dazu.

    Scheitert eine Instanz, bricht der ganze Vorgang ab und meldet **welche**.
    Die uebrigen bleiben, wie sie waren: Ein halb geschriebener Zustand waere
    schlimmer als gar keiner, weil danach niemand mehr weiss, was gilt.
    """
    settings = load_settings(db)

    for wunsch in aenderung.instances:
        if not settings.arr_configured(wunsch.media_type, wunsch.tier):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=meldungen.meldung(
                    "instance_not_configured",
                    "Diese Instanz ist gar nicht eingerichtet.",
                ),
            )
        try:
            await library.papierkorb_setzen(
                settings,
                wunsch.media_type,
                wunsch.tier,
                pfad=wunsch.path.strip(),
                tage=aenderung.cleanup_days,
            )
        except ArrError as fehler:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                # ⚠️ Der Rahmen bekommt eine Kennung, die fremde Meldung
                # bleibt als Zitat daneben stehen: Was Radarr antwortet,
                # antwortet Radarr in seiner Sprache - das kann Nexview nicht
                # uebersetzen, ohne es zu erfinden.
                detail=meldungen.meldung(
                    "recyclebin_write_failed",
                    f"{wunsch.media_type}/{wunsch.tier}: {fehler.message}",
                    media_type=wunsch.media_type,
                    tier=wunsch.tier,
                    reason=fehler.message,
                ),
            ) from fehler

    # Den frisch gelesenen Stand zurueckgeben, nicht den gewuenschten: Was
    # wirklich gilt, steht in Radarr - und nur das soll die Oberflaeche zeigen.
    return await papierkorb_stand(admin, db)


class PapierkorbInhaltInstanz(BaseModel):
    name: str
    path: str
    # Nur die Ordnernamen, so wie die Instanz sie fuehrt.
    entries: list[str]
    # Wurde die Liste gekuerzt? Dann steht das dabei, statt so zu tun, als
    # waere das alles.
    truncated: bool = False


class PapierkorbInhalt(BaseModel):
    instances: list[PapierkorbInhaltInstanz]


# Wieviele Eintraege je Instanz hoechstens gezeigt werden. Ein Papierkorb mit
# dreihundert Ordnern wird nicht dadurch nuetzlicher, dass man alle auflistet.
_HOECHSTENS = 200


@router.get("/settings/recyclebin/contents", response_model=PapierkorbInhalt)
async def papierkorb_inhalt(
    admin: AdminUser,
    db: DbSession,
    media_type: Literal["movie", "tv"] | None = None,
    tier: Literal["standard", "uhd"] | None = None,
    path: str = "",
) -> PapierkorbInhalt:
    """Was liegt gerade im Papierkorb - je Instanz?

    **Nur die Ordnernamen.** Kein Plakat, kein aufgeraeumter Titel.

    ⚠️ Der naheliegende Weg waere, die TMDB-Nummer aus dem Ordnernamen zu lesen
    (``The Matrix (1999) {tmdb-603}``) und damit ein Plakat zu holen. Das
    funktioniert - **aber nur, wenn das Benennungsschema die Nummer enthaelt.**
    Sie steht dort nicht von Natur aus, sondern weil jemand sein Schema so
    eingerichtet hat. Wer das anders haelt, saehe ueberall "kein Plakat", und
    eine Ansicht, die bei der Haelfte der Installationen leer aussieht, ist
    keine Ansicht.

    Der Ordnername steht dagegen immer da und sagt genug: welcher Titel, welche
    Fassung, wie viele.

    **Nur lesen.** Zurueckholen kann Nexview nicht: Dafuer muessten Dateien
    verschoben werden, und Nexview sieht das Dateisystem gar nicht - es spricht
    ausschliesslich ueber die API mit Radarr und Sonarr.
    """
    settings = load_settings(db)
    instanzen: list[PapierkorbInhaltInstanz] = []

    for art, stufe, name, stand in await library.papierkoerbe(settings):
        # Auf eine Instanz einschraenken, wenn danach gefragt wird.
        if media_type and (art != media_type or stufe != tier):
            continue

        # ``path`` erlaubt es, in einen **noch nicht gespeicherten** Ordner zu
        # schauen. Den gibt es ja bereits - Radarr fuehrt ihn nur noch nicht
        # als Papierkorb. Wer einen Ordner aussucht, will vorher hineinsehen,
        # und ihn dafuer erst speichern zu muessen waere die falsche
        # Reihenfolge.
        gewaehlt = path.strip() or stand.path
        if not gewaehlt or not stand.erreichbar:
            continue

        pfade = await library.ordner(settings, art, stufe, gewaehlt)
        instanzen.append(
            PapierkorbInhaltInstanz(
                name=name,
                path=gewaehlt,
                entries=[voll.rstrip("/").rsplit("/", maxsplit=1)[-1] for voll in pfade[:_HOECHSTENS]],
                truncated=len(pfade) > _HOECHSTENS,
            )
        )

    return PapierkorbInhalt(instances=instanzen)


# --- Rueckkanal (Webhook) je Instanz ----------------------------------------


class WebhookInstanzStand(BaseModel):
    """Der ehrliche Zustand einer Instanz - wie er auf der Diensteseite steht."""

    kennung: str
    name: str
    media_type: str
    tier: str
    aktiv: bool
    # Steht unser Eintrag gerade in Radarr/Sonarr?
    eingetragen: bool
    bewiesen_am: datetime | None
    zuletzt_angerufen_am: datetime | None
    letztes_ereignis: str
    geprueft_am: datetime | None
    # Kennung des Hindernisgrunds ("no_address", "too_old", "proof_failed",
    # "unreachable", "create_failed") - uebersetzt im Frontend; dazu ein roher
    # Zusatz (Version, fehlende Faehigkeiten), der nicht uebersetzt wird.
    fehler: str
    fehler_info: str


class WebhookStand(BaseModel):
    # Von wo aus Radarr/Sonarr anrufen - leer, wenn keine Adresse gesetzt ist.
    basis: str
    instanzen: list[WebhookInstanzStand]


def _webhook_stand(db, settings) -> WebhookStand:
    zeilen = []
    for instanz in settings.arr_instanzen():
        zeile = webhooks.eintrag(db, instanz.kennung)
        zeilen.append(
            WebhookInstanzStand(
                kennung=instanz.kennung,
                name=instanz.name,
                media_type=instanz.media_type,
                tier=instanz.tier,
                # Vorgabe an: Eine Instanz ohne Zustand hat noch nie eine
                # Pflege gesehen - der Haken gilt als gesetzt, ausgefuehrt
                # wird beim naechsten Rundgang.
                aktiv=zeile.aktiv if zeile else True,
                eingetragen=bool(zeile and zeile.eintrag_id is not None),
                bewiesen_am=zeile.bewiesen_am if zeile else None,
                zuletzt_angerufen_am=zeile.zuletzt_angerufen_am if zeile else None,
                letztes_ereignis=zeile.letztes_ereignis if zeile else "",
                geprueft_am=zeile.geprueft_am if zeile else None,
                fehler=zeile.fehler if zeile else "",
                fehler_info=zeile.fehler_info if zeile else "",
            )
        )
    return WebhookStand(basis=settings.webhook_basis, instanzen=zeilen)


def _webhook_instanz(settings, kennung: str):
    instanz = next(
        (i for i in settings.arr_instanzen() if i.kennung == kennung), None
    )
    if instanz is None:
        raise meldungen.fehler(
            "webhook_unknown_instance",
            "Zu dieser Kennung ist keine Instanz eingerichtet.",
            status.HTTP_404_NOT_FOUND,
        )
    return instanz


@router.get("/settings/webhooks", response_model=WebhookStand)
async def webhook_stand(admin: AdminUser, db: DbSession) -> WebhookStand:
    """Der Rueckkanal-Zustand aller eingerichteten Instanzen.

    Nur lesen, nichts anfassen: Die Wahrheit ueber "bewiesen" und "zuletzt
    angerufen" schreibt der Empfaenger (routers/webhooks), die ueber den
    Eintrag selbst die Pflege (services/webhook_pflege).
    """
    return _webhook_stand(db, load_settings(db))


class WebhookHaken(BaseModel):
    aktiv: bool


@router.patch("/settings/webhooks/{kennung}", response_model=WebhookStand)
async def webhook_haken(
    kennung: str, payload: WebhookHaken, admin: AdminUser, db: DbSession
) -> WebhookStand:
    """Den Haken "Webhook fuer Rueckkanal nutzen" umlegen - mit sofortiger Tat.

    Einschalten heisst: Probe, Beweis, Eintrag anlegen. Abwaehlen heisst:
    unseren Eintrag in Radarr/Sonarr rueckstandsfrei entfernen. Beides laeuft
    noch in dieser Anfrage, damit die Antwort den wirklichen Zustand traegt -
    die Sekunden Wartezeit sind hier Ehrlichkeit, keine Traegheit.
    """
    settings = load_settings(db)
    instanz = _webhook_instanz(settings, kennung)
    zeile = webhooks.eintrag_sicherstellen(db, kennung)
    zeile.aktiv = payload.aktiv
    db.commit()
    await webhook_pflege.instanz_pflegen(db, settings, instanz)
    return _webhook_stand(db, load_settings(db))


class WebhookProbe(BaseModel):
    angekommen: bool
    dauer_ms: int | None = None
    fehler: str | None = None
    info: str | None = None


class VerbindungInstanz(BaseModel):
    kennung: str
    name: str
    erreichbar: bool
    version: str = ""


class VerbindungStand(BaseModel):
    instanzen: list[VerbindungInstanz]


@router.get("/settings/instanzen/verbindung", response_model=VerbindungStand)
async def instanzen_verbindung(admin: AdminUser, db: DbSession) -> VerbindungStand:
    """Sind die Instanzen gerade erreichbar? Live gefragt, nichts gespeichert.

    Fuer die Statusleuchte auf den Kacheln - deshalb alle gleichzeitig und
    mit kurzem Atem: Eine stumme Instanz darf die Antwort der anderen nicht
    festhalten, und eine Leuchte, die fuenfzehn Sekunden nachdenkt, beruhigt
    niemanden.
    """
    settings = load_settings(db)
    kurzer_atem = httpx.Timeout(4.0, connect=3.0)

    async def pruefen(instanz) -> VerbindungInstanz:
        client = ArrClient(instanz.url, instanz.api_key, instanz.name)
        try:
            status = await client.system_status(timeout=kurzer_atem)
        except ArrError:
            return VerbindungInstanz(
                kennung=instanz.kennung, name=instanz.name, erreichbar=False
            )
        return VerbindungInstanz(
            kennung=instanz.kennung,
            name=instanz.name,
            erreichbar=True,
            version=str(status.get("version") or ""),
        )

    ergebnisse = await asyncio.gather(
        *(pruefen(instanz) for instanz in settings.arr_instanzen())
    )
    return VerbindungStand(instanzen=list(ergebnisse))


class GesundheitProblem(BaseModel):
    typ: str
    text: str


class GesundheitInstanz(BaseModel):
    kennung: str
    name: str
    probleme: list[GesundheitProblem]
    aktualisiert_am: datetime | None


class GesundheitStand(BaseModel):
    instanzen: list[GesundheitInstanz]


@router.get("/settings/instanzen/gesundheit", response_model=GesundheitStand)
async def instanzen_gesundheit(admin: AdminUser, db: DbSession) -> GesundheitStand:
    """Was die Instanzen selbst als Problem melden - je Instanz.

    Gelesen wird der zuletzt gesehene Stand (der Rundgang holt ihn jede
    Runde frisch); die Texte kommen im Wortlaut der Instanz und werden
    bewusst nicht uebersetzt.
    """
    settings = load_settings(db)
    zeilen = []
    for instanz in settings.arr_instanzen():
        zeile = instanz_gesundheit.eintrag(db, instanz.kennung)
        zeilen.append(
            GesundheitInstanz(
                kennung=instanz.kennung,
                name=instanz.name,
                probleme=[
                    GesundheitProblem(
                        typ=str(p.get("typ") or "warning"),
                        text=str(p.get("text") or ""),
                    )
                    for p in (zeile.stand if zeile else None) or []
                ],
                aktualisiert_am=zeile.aktualisiert_am if zeile else None,
            )
        )
    return GesundheitStand(instanzen=zeilen)


class KollisionOut(BaseModel):
    schluessel: str
    programm: str
    kategorie: str
    ohne_kategorie: bool
    instanzen: list[str]
    kennungen: list[str]


class KollisionStand(BaseModel):
    kollisionen: list[KollisionOut]


class KollisionIgnorierenIn(BaseModel):
    schluessel: str


@router.get("/settings/instanzen/downloadkollision", response_model=KollisionStand)
async def instanzen_downloadkollision(admin: AdminUser, db: DbSession) -> KollisionStand:
    """Benutzen zwei Instanzen dieselbe Kategorie im Download-Programm?

    ⚠️ **Das kann nur Nexview sehen.** Radarr kennt die zweite Instanz nicht
    und warnt deshalb nie - waehrend sich beide die Downloads wegnehmen und
    der Fehler wie ein Netzproblem aussieht.

    Frisch gefragt statt gemerkt: Die Einstellung aendert sich selten, aber
    ein gemerkter Stand waere nach jeder Aenderung drueben falsch.
    """
    settings = load_settings(db)
    je_instanz: list[tuple[str, str, list[dict]]] = []
    for instanz in settings.arr_instanzen():
        client = ArrClient(instanz.url, instanz.api_key, instanz.name)
        try:
            programme = await client.get("/downloadclient")
        except Exception:  # noqa: BLE001 - eine stumme Instanz kippt die Seite nicht
            logger.info("Download clients of %s not readable", instanz.kennung)
            continue
        je_instanz.append((instanz.kennung, instanz.name, list(programme or [])))

    treffer = download_kollision.offen(
        download_kollision.finden(je_instanz), download_kollision.ignorierte(db)
    )
    return KollisionStand(
        kollisionen=[
            KollisionOut(
                schluessel=k.schluessel, programm=k.programm_name,
                kategorie=k.kategorie, ohne_kategorie=k.ohne_kategorie,
                instanzen=k.instanzen, kennungen=k.kennungen,
            )
            for k in treffer
        ]
    )


@router.post("/settings/instanzen/downloadkollision/ignorieren", status_code=204)
async def instanzen_downloadkollision_ignorieren(
    payload: KollisionIgnorierenIn, admin: AdminUser, db: DbSession
) -> None:
    """Diese Kollision nicht mehr melden - sie ist gewollt."""
    download_kollision.ignorieren(db, payload.schluessel)


# Welche Einstellungs-Schluessel zu einer Instanz gehoeren - fuers Entfernen.
# Die 4K-Regeln gehen zurueck auf "" (= erben wieder von der Standard-Instanz);
# die Standard-Regeln bleiben stehen, sie sind eine Regel des Hauses.
INSTANZ_FELDGRUPPEN: dict[str, dict[str, object]] = {
    "radarr-standard": {
        "url": "radarr_url", "key": "radarr_api_key", "name": "radarr_name",
        "leeren": ["default_movie_profile_id", "default_movie_root"],
    },
    "radarr-uhd": {
        "url": "radarr_uhd_url", "key": "radarr_uhd_api_key", "name": "radarr_uhd_name",
        "leeren": [
            "default_movie_uhd_profile_id", "default_movie_uhd_root",
            "movie_uhd_profile_mode", "movie_uhd_root_folder_mode",
        ],
    },
    "sonarr-standard": {
        "url": "sonarr_url", "key": "sonarr_api_key", "name": "sonarr_name",
        "leeren": ["default_series_profile_id", "default_series_root"],
    },
    "sonarr-uhd": {
        "url": "sonarr_uhd_url", "key": "sonarr_uhd_api_key", "name": "sonarr_uhd_name",
        "leeren": [
            "default_series_uhd_profile_id", "default_series_uhd_root",
            "series_uhd_profile_mode", "series_uhd_root_folder_mode",
        ],
    },
}


@router.delete("/settings/instanzen/{kennung}")
async def instanz_entfernen(
    kennung: str, admin: AdminUser, db: DbSession
) -> dict[str, object]:
    """Nexviews Zugang zu dieser Instanz entfernen - mehr nicht.

    In Radarr/Sonarr selbst passiert nichts: Downloads und Suchlaeufe dort
    laufen weiter. Eine Ausnahme: Unser Webhook-Eintrag wird vorher
    rueckstandsfrei mit entfernt - sonst riefe er fuer immer ins Leere und
    stuende drueben als krank. Laufende Anfragen der Instanz bleiben bewusst
    stehen (kein Massen-Abbruch, dieselbe Regel wie beim Ausfall einer
    Quelle im Status-Abgleich); die Speicher-Posten uebernimmt der naechste
    Abgleich: Was der Medienserver weiter meldet, bleibt - nur nicht mehr
    ueber Nexview loeschbar -, was allein die Instanz kannte, verschwindet
    aus der Zurechnung.
    """
    settings = load_settings(db)
    instanz = _webhook_instanz(settings, kennung)
    felder = INSTANZ_FELDGRUPPEN[kennung]

    zeile = webhooks.eintrag(db, kennung)
    if zeile is not None:
        # Abwaehlen und einmal pflegen raeumt den Eintrag drueben weg - so
        # gut es geht: Eine gerade stumme Instanz haelt das Entfernen nicht
        # auf, dann bleibt ihr Eintrag eben stehen.
        zeile.aktiv = False
        db.commit()
        try:
            await webhook_pflege.instanz_pflegen(db, settings, instanz)
        except Exception:  # noqa: BLE001 - Aufraeumen ist Beiwerk des Entfernens
            logger.warning(
                "Webhook entry in %s could not be removed while deleting the instance",
                instanz.name,
            )
        rest = webhooks.eintrag(db, kennung)
        if rest is not None:
            db.delete(rest)
    gesund = instanz_gesundheit.eintrag(db, kennung)
    if gesund is not None:
        db.delete(gesund)
    db.commit()

    clear_secret(db, felder["key"])
    save_settings(
        db,
        {felder["url"]: "", felder["name"]: "", **{f: "" for f in felder["leeren"]}},
    )
    library.invalidate()
    logger.info("Instance access removed: %s", instanz.name)
    return public_settings(db)


@router.post("/settings/webhooks/{kennung}/testen", response_model=WebhookProbe)
async def webhook_testen(
    kennung: str, admin: AdminUser, db: DbSession
) -> WebhookProbe:
    """Der Testen-Knopf: die Instanz jetzt einmal anrufen lassen.

    Beweist die ganze Strecke - Nexview bittet Radarr/Sonarr um die Probe,
    die Instanz ruft unsere Anruf-Adresse, der Empfaenger vermerkt den
    Beweis. Die Antwort sagt ehrlich, ob und wie schnell der Anruf ankam,
    oder woran es haengt.
    """
    settings = load_settings(db)
    instanz = _webhook_instanz(settings, kennung)
    ergebnis = await webhook_pflege.testen(db, settings, instanz)
    return WebhookProbe(**ergebnis)
