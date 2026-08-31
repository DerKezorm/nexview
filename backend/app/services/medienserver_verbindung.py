"""Radarr und Sonarr mit den Medienservern verbinden.

⚠️ **Warum das ueberhaupt jemand braucht.** Ohne diese Verbindung erfaehrt der
Medienserver von einem Import, einer Aufwertung oder einer Umbenennung nichts -
er zeigt bis zu seinem naechsten eigenen Durchlauf auf Dateien, die so nicht
mehr heissen. Besonders nach einem Umbenennen des Bestands ist das der
Unterschied zwischen "kurz weg" und "stundenlang kaputt".

Von Hand ist es Fleissarbeit: Wer drei Medienserver und vier Instanzen
betreibt, traegt zwoelf Verbindungen ein - jede mit Adresse, Token und den
richtigen Haken. Nexview kennt beide Seiten und kann es abnehmen.

⚠️ **Erst die Probe, dann der Eintrag.** Dieselbe Falle wie beim Webhook: Die
Adresse, unter der *Nexview* einen Medienserver erreicht, muss nicht die sein,
unter der *Radarr* ihn erreicht - Radarr steckt womoeglich in einem Container
mit eigener Sicht aufs Netz. Ein Eintrag, der nie funktioniert, ist schlimmer
als keiner, weil ihn niemand mehr hinterfragt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..models import MediaServerConnection
from .arr import ArrClient, ArrError
from .pfad_zuordnung import Zuordnung, ableiten, server_pfade
from . import logs

logger = logging.getLogger("nexview.qualitaet")

#: Welcher Anbieter welchen Bauplan in Radarr/Sonarr bekommt.
BAUPLAN = {
    "plex": ("PlexServer", "PlexServerSettings"),
    "emby": ("MediaBrowser", "MediaBrowserSettings"),
    "jellyfin": ("MediaBrowser", "MediaBrowserSettings"),
}

#: Bei welchen Anbietern Nexview den Zugang **selbst** beisteuern kann.
#:
#: ⚠️ **Nur Plex.** Dort meldet sich Nexview mit einem Plex-Token an, und genau
#: so eines will Radarr im Feld ``authToken`` - dasselbe Stueck Papier.
#:
#: Bei Jellyfin und Emby ist es ein anderes: Nexview meldet sich mit Benutzer
#: und Passwort an (``/Users/AuthenticateByName``) und bekommt einen
#: **Sitzungs-Token** zurueck. Radarr will dort aber einen **API-Schluessel**,
#: den man im Dashboard des Medienservers anlegt. Den Sitzungs-Token
#: einzutragen waere selbst dann falsch, wenn er heute funktionierte: Er haengt
#: an Nexviews Sitzung und ist weg, sobald sich Nexview neu anmeldet oder
#: jemand die Geraete abmeldet - und dann steht in Radarr eine Verbindung, die
#: stillschweigend nichts mehr tut.
SELBST_MOEGLICH = {"plex"}

#: Ereignisse, bei denen der Medienserver Bescheid bekommen soll.
#:
#: ⚠️ ``onRename`` ist das eigentlich wichtige - ohne es bringt eine
#: Umbenennung den Medienserver aus dem Tritt. Die uebrigen sorgen dafuer, dass
#: ein neuer Film ueberhaupt zeitnah auftaucht.
EREIGNISSE = (
    "onDownload",
    "onUpgrade",
    "onRename",
    "onMovieDelete",
    "onMovieFileDelete",
    "onSeriesDelete",
    "onEpisodeFileDelete",
)

#: Woran Nexview seine eigenen Eintraege wiedererkennt.
NAME_VORNE = "Nexview: "


@dataclass
class Luecke:
    """Ein Medienserver, zu dem diese Instanz (noch) keine Verbindung hat."""

    provider: str
    name: str
    url: str
    #: Kann Nexview das allein einrichten, oder braucht es einen Schluessel?
    #:
    #: ``False`` heisst nicht "geht nicht", sondern "es fehlt ein API-Schluessel
    #: aus dem Dashboard des Medienservers" - siehe SELBST_MOEGLICH.
    selbst_moeglich: bool = False
    #: Warum es nicht geht, falls es nicht geht.
    hindernis: str = ""
    #: Wie Pfade umgeschrieben werden muessen - siehe ``pfad_zuordnung``.
    #:
    #: ⚠️ Steht hier ``kein_treffer``, haben die beiden Seiten keine Bibliothek
    #: gemeinsam. Das ist meist kein Fehler, sondern eine Aussage: Dieser
    #: Medienserver fuehrt die Filme dieser Instanz schlicht nicht.
    zuordnung: Zuordnung = field(default_factory=Zuordnung)


@dataclass
class Medienserver:
    """Ein Server, wie ihn diese Funktion braucht - samt passendem Zugang."""

    id: int
    provider: str
    name: str
    url: str
    #: Was in Radarr eingetragen wird. Leer heisst: es fehlt noch etwas.
    zugang: str
    #: Braucht dieser Anbieter einen Schluessel vom Betreiber?
    braucht_schluessel: bool


def server_liste(db: Session) -> list[Medienserver]:
    """Alle verbundenen Medienserver mit dem Zugang, der nach Radarr gehoert.

    ⚠️ Bei Plex ist das der Plex-Token, den Nexview ohnehin fuehrt. Bei
    Jellyfin und Emby der **API-Schluessel**, den der Betreiber eingetragen
    hat - Nexviews eigener Sitzungs-Token taugt dort nicht.
    """
    ergebnis: list[Medienserver] = []
    for zeile in db.scalars(select(MediaServerConnection).order_by(MediaServerConnection.id)):
        braucht = zeile.provider not in SELBST_MOEGLICH
        roh = zeile.arr_api_key if braucht else zeile.token
        try:
            zugang = crypto.decrypt(roh) if roh else ""
        except Exception:  # noqa: BLE001 - ein unlesbarer Zugang ist kein Absturz
            logger.warning("Access for media server %s is not readable", zeile.name)
            zugang = ""
        ergebnis.append(
            Medienserver(
                id=zeile.id,
                provider=zeile.provider,
                name=zeile.name or zeile.provider,
                url=zeile.url,
                zugang=zugang,
                braucht_schluessel=braucht,
            )
        )
    return ergebnis


def schluessel_setzen(db: Session, server_id: int, schluessel: str) -> bool:
    """Den API-Schluessel eines Medienservers hinterlegen.

    Ein leerer Wert loescht ihn wieder - dann faellt die Instanz auf "fehlt"
    zurueck, statt mit einem toten Schluessel weiterzumachen.
    """
    zeile = db.get(MediaServerConnection, server_id)
    if zeile is None:
        return False
    zeile.arr_api_key = crypto.encrypt(schluessel) if schluessel.strip() else ""
    return True


def _adresse(url: str) -> tuple[str, int, bool]:
    """Adresse zerlegen - Radarr will Wirt, Tor und SSL getrennt."""
    teile = urlparse(url)
    ssl = teile.scheme == "https"
    return teile.hostname or "", int(teile.port or (443 if ssl else 80)), ssl


def _payload(
    provider: str,
    name: str,
    url: str,
    token: str,
    unterstuetzt: dict,
    zuordnung: Zuordnung | None = None,
) -> dict:
    """Den Eintrag bauen, wie ihn die Instanz erwartet."""
    umsetzung, vertrag = BAUPLAN[provider]
    wirt, tor, ssl = _adresse(url)
    felder = [
        {"name": "host", "value": wirt},
        {"name": "port", "value": tor},
        {"name": "useSsl", "value": ssl},
        {"name": "updateLibrary", "value": True},
    ]
    if provider == "plex":
        felder.append({"name": "authToken", "value": token})
    else:
        felder.append({"name": "apiKey", "value": token})

    # ⚠️ **Der eigentliche Sinn der ganzen Uebung.** Ohne diese beiden Felder
    # nennt die Instanz einen Pfad aus *ihrer* Sicht (``/data/Movies``), und
    # der Medienserver sucht ihn bei sich vergeblich (``/media/Movies``). Der
    # Anruf kommt an, wird bejaht - und nichts passiert. Nur setzen, wenn
    # wirklich etwas umzuschreiben ist: Wo beide Seiten denselben Pfad sehen,
    # waeren leere Felder richtig und ein erfundener Wert schaedlich.
    if zuordnung and zuordnung.von and zuordnung.nach:
        felder.append({"name": "mapFrom", "value": zuordnung.von})
        felder.append({"name": "mapTo", "value": zuordnung.nach})

    # Nur Flaggen setzen, die diese Instanz auch kennt: Radarr fuehrt
    # ``onMovieDelete``, Sonarr ``onSeriesDelete`` - die jeweils andere lehnt
    # sie ab.
    flaggen = {
        ereignis: True
        for ereignis in EREIGNISSE
        if unterstuetzt.get(f"supports{ereignis[0].upper()}{ereignis[1:]}")
    }
    return {
        "name": NAME_VORNE + name,
        "implementation": umsetzung,
        "configContract": vertrag,
        "tags": [],
        "fields": felder,
        **flaggen,
    }


async def _bauplan(client: ArrClient, umsetzung: str) -> dict | None:
    for eintrag in await client.get("/notification/schema") or []:
        if isinstance(eintrag, dict) and eintrag.get("implementation") == umsetzung:
            return eintrag
    return None


def _feld(eintrag: dict, name: str) -> str:
    for f in eintrag.get("fields", []):
        if f.get("name") == name:
            return str(f.get("value") or "")
    return ""


def _passender_eintrag(vorhanden: list[dict], server: Medienserver) -> dict | None:
    """Den vorhandenen Eintrag zu genau diesem Medienserver finden.

    ⚠️ **Der Port gehoert zwingend dazu.** Jellyfin und Emby laufen bei Radarr
    unter derselben Umsetzung ``MediaBrowser``, und beide stehen oft auf
    demselben Rechner - nur die Ports unterscheiden sie (8096 und 8097). Wer
    allein den Wirt vergleicht, haelt Emby fuer verbunden, sobald Jellyfin es
    ist, meldet "Alle verbunden" und legt die fehlende Verbindung nie an.
    Genau so ist es passiert.

    Der Name taugt als Merkmal nicht: "Nexview: Bizzy" kann jeder vergeben, und
    ein von Hand eingetragener Eintrag zum selben Server soll genauso zaehlen.
    """
    if server.provider not in BAUPLAN:
        return None
    umsetzung, _vertrag = BAUPLAN[server.provider]
    wirt, tor, _ssl = _adresse(server.url)
    for eintrag in vorhanden:
        if eintrag.get("implementation") != umsetzung:
            continue
        if _feld(eintrag, "host") != wirt:
            continue
        # Ein Eintrag ohne Port-Feld gilt als passend: Fehlt die Angabe, ist
        # der Standardport gemeint, und dann entscheidet der Wirt.
        port_roh = _feld(eintrag, "port")
        if port_roh and port_roh not in (str(tor), str(float(tor))):
            continue
        return eintrag
    return None


async def luecken(
    client: ArrClient,
    server: list[Medienserver],
    karte: dict[str, Zuordnung] | None = None,
) -> list[Luecke]:
    """Zu welchen Medienservern fehlt dieser Instanz eine Verbindung?

    Erkannt wird an Anbieter **und Adresse** - nicht am Namen: "Nexview: Bizzy"
    kann jeder vergeben, und ein von Hand eingetragener Eintrag zum selben
    Server zaehlt genauso. Zweimal dasselbe einzutragen brächte nur doppelte
    Anrufe.
    """
    vorhanden = await client.notifications()
    offen: list[Luecke] = []
    for eintrag_server in server:
        if eintrag_server.provider not in BAUPLAN:
            offen.append(
                Luecke(
                    eintrag_server.provider,
                    eintrag_server.name,
                    eintrag_server.url,
                    hindernis="unknown_provider",
                )
            )
            continue
        if _passender_eintrag(vorhanden, eintrag_server) is None:
            offen.append(
                Luecke(
                    provider=eintrag_server.provider,
                    name=eintrag_server.name,
                    url=eintrag_server.url,
                    selbst_moeglich=bool(eintrag_server.zugang),
                    hindernis="" if eintrag_server.zugang else "kein_schluessel",
                    zuordnung=(karte or {}).get(
                        eintrag_server.provider + eintrag_server.url, Zuordnung()
                    ),
                )
            )
    return offen


async def zuordnungen(
    client: ArrClient, server: list[Medienserver]
) -> dict[str, Zuordnung]:
    """Fuer jeden Medienserver ermitteln, wie Pfade umzuschreiben sind.

    Der Schluessel ist ``provider + url`` - dieselbe Kennung wie sonst auch,
    damit sich Luecke und Zuordnung zusammenfinden.

    ⚠️ **Beide Seiten werden gefragt, keine geraten.** Antwortet eine nicht,
    steht das Hindernis in der Zuordnung und die Felder bleiben leer. Eine
    unbekannte Zuordnung darf nie als "keine noetig" durchgehen: Das eine
    heisst "weiss ich nicht", das andere "ich habe nachgesehen".
    """
    try:
        wurzeln = [
            str(eintrag.get("path") or "")
            for eintrag in await client.root_folders()
            if eintrag.get("path")
        ]
    except ArrError as fehler:
        logger.info("No root folders from %s: %s", client.label, logs.kennung(fehler))
        wurzeln = []

    ergebnis: dict[str, Zuordnung] = {}
    for eintrag_server in server:
        pfade = await server_pfade(
            eintrag_server.provider, eintrag_server.url, eintrag_server.zugang
        )
        if pfade.hindernis:
            ergebnis[eintrag_server.provider + eintrag_server.url] = Zuordnung(
                hindernis=pfade.hindernis
            )
            continue
        ergebnis[eintrag_server.provider + eintrag_server.url] = ableiten(
            wurzeln, pfade.pfade
        )
    return ergebnis


async def bestehende_pruefen(
    client: ArrClient, server: list[Medienserver]
) -> list[tuple[str, str]]:
    """Tun die *vorhandenen* Verbindungen noch, was sie sollen?

    ⚠️ **Warum das noetig ist.** Ein Zugang laeuft zwar nicht nach Zeit ab -
    API-Schluessel von Jellyfin und Emby gelten, bis sie jemand widerruft, und
    der Plex-Token, bis jemand die Geraete abmeldet. Aber *wenn* er ungueltig
    wird, merkt es niemand: Radarr ruft weiter an, wird abgewiesen und
    schweigt. In der Liste steht die Verbindung unveraendert da.

    Gibt Paare ``(provider, grund)`` fuer **jede vorhandene** Verbindung
    zurueck; ein leerer Grund heisst "antwortet".

    ⚠️ Auch das Gelungene gehoert in die Antwort. Wer nur Fehler zurueckgibt,
    zwingt die Oberflaeche dazu, ausschliesslich Luecken zu zeigen - und dann
    steht bei einer halb verbundenen Instanz nur, was fehlt, waehrend die
    bestehende Verbindung unsichtbar bleibt und wie ein Versaeumnis wirkt.
    """
    vorhanden = await client.notifications()
    kaputt: list[tuple[str, str]] = []
    for eintrag_server in server:
        if eintrag_server.provider not in BAUPLAN:
            continue
        umsetzung, _vertrag = BAUPLAN[eintrag_server.provider]
        # ⚠️ Dieselbe Erkennung wie in ``luecken`` - inklusive Port. Sonst
        # pruefte diese Funktion die Jellyfin-Verbindung und schriebe das
        # Ergebnis Emby zu: eine gruene Meldung ueber etwas, das nie
        # angefasst wurde.
        bestand = _passender_eintrag(vorhanden, eintrag_server)
        if bestand is None:
            continue
        if not eintrag_server.zugang:
            kaputt.append((eintrag_server.provider, "kein_schluessel"))
            continue
        plan = await _bauplan(client, umsetzung)
        if plan is None:
            continue
        payload = _payload(
            eintrag_server.provider,
            eintrag_server.name,
            eintrag_server.url,
            eintrag_server.zugang,
            plan,
        )
        # ⚠️ **Mit ``id`` und dem echten Namen pruefen, sonst luegt die Probe.**
        # ``/notification/test`` laesst dieselbe Pruefung laufen wie das
        # Speichern - und dazu gehoert "Name schon vergeben". Ohne die ``id``
        # haelt die Instanz den Eintrag fuer einen *neuen*, stoesst sich am
        # eigenen bestehenden Namen und antwortet mit einem Fehler. Der sieht
        # aus wie "nicht erreichbar", und schon meldet Nexview sieben
        # funktionierende Verbindungen als kaputt.
        payload["id"] = bestand.get("id")
        payload["name"] = bestand.get("name") or payload["name"]
        try:
            await client.post("/notification/test", payload)
        except ArrError as fehler:
            logger.info(
                "Existing link %s -> %s no longer works: %s",
                client.label,
                eintrag_server.provider,
                logs.kennung(fehler),
            )
            kaputt.append((eintrag_server.provider, "unreachable"))
            continue
        kaputt.append((eintrag_server.provider, ""))
    return kaputt


async def herstellen(
    client: ArrClient,
    provider: str,
    name: str,
    url: str,
    token: str,
    zuordnung: Zuordnung | None = None,
) -> str:
    """Eine Verbindung anlegen - erst pruefen, dann eintragen.

    ``token`` ist bei Plex der Plex-Token, den Nexview ohnehin hat; bei Emby
    und Jellyfin ein **API-Schluessel**, den der Betreiber im Dashboard seines
    Medienservers anlegt und hier eintraegt. Nexviews eigener Zugang taugt dort
    nicht (siehe ``SELBST_MOEGLICH``).

    Gibt "" zurueck, wenn es geklappt hat, sonst den Grund.
    """
    if not token:
        return "kein_schluessel"
    umsetzung, _vertrag = BAUPLAN[provider]
    plan = await _bauplan(client, umsetzung)
    if plan is None:
        return "too_old"

    payload = _payload(provider, name, url, token, plan, zuordnung)
    try:
        await client.post("/notification/test", payload)
    except ArrError as fehler:
        # ⚠️ Die haeufigste Ursache ist, dass Radarr die Adresse nicht
        # erreicht - und ausgerechnet die meldet es als 500 mit Stacktrace.
        logger.info(
            "Media server connection to %s not reachable from %s: %s",
            name,
            client.label,
            logs.kennung(fehler),
        )
        return "unreachable"

    await client.notification_anlegen(payload)
    logger.info("Connected %s to media server %s", client.label, name)
    return ""
