"""Das empfohlene Benennungsschema der TRaSH-Guides uebernehmen.

⚠️ **Warum das ueberhaupt eine eigene Funktion ist.** Der Dateiname ist die
einzige Stelle, an der nicht wiederherstellbare Angaben ueberleben: Quelle,
Release-Gruppe, Schnittfassung. Geht die Datenbank verloren oder wird eine
Bibliothek neu eingelesen, entscheidet allein der Name, ob Radarr denselben
Film wiedererkennt - oder ihn ein zweites Mal herunterlaedt.

⚠️ **Was diese Funktion NICHT tut: vorhandene Dateien umbenennen.** Das Schema
wirkt nur auf das, was Radarr oder Sonarr von sich aus schreibt - neue Importe
und Aufwertungen. Der Bestand bleibt liegen, bis jemand in Radarr selbst die
Massen-Umbenennung anstoesst. Das ist Absicht: Umbenennen bricht laufendes
Seeding, wenn keine Hardlinks im Spiel sind, und laesst den Medienserver neu
einlesen. Diese Entscheidung gehoert nicht in einen Nebeneffekt.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Umbenennlauf, utcnow
from .arr import ArrClient
from .trash import schnappschuss

logger = logging.getLogger("nexview.qualitaet")

#: Welches Feld der Instanz welches Schema traegt - je Dienst.
#:
#: ⚠️ Radarr und Sonarr heissen die Felder verschieden, und Sonarr fuehrt gleich
#: mehrere Dateischemata (normal, taeglich, Anime). Wer hier eines vergisst,
#: setzt ein Schema, das fuer einen Teil der Bibliothek nie greift.
FELDER: dict[str, dict[str, str]] = {
    "radarr": {
        "datei": "standardMovieFormat",
        "ordner": "movieFolderFormat",
        "umbenennen": "renameMovies",
    },
    "sonarr": {
        "datei": "standardEpisodeFormat",
        "datei_taeglich": "dailyEpisodeFormat",
        "datei_anime": "animeEpisodeFormat",
        "ordner": "seriesFolderFormat",
        "ordner_staffel": "seasonFolderFormat",
        "umbenennen": "renameEpisodes",
    },
}

#: Wie die Titel je Dienst heissen - fuer Vorschau und Auftrag.
TITEL = {
    "radarr": {"liste": "/movie", "feld": "movieId", "felder": "movieIds",
               "befehl": "RenameMovie"},
    "sonarr": {"liste": "/series", "feld": "seriesId", "felder": "seriesIds",
               "befehl": "RenameSeries"},
}

#: Wie viele Titel ein Auftrag umfasst.
#:
#: ⚠️ **Warum ueberhaupt Haeppchen.** Radarr meldet zu einem Auftrag nur, ob er
#: laeuft - keinen Fortschritt. Ein einziger Auftrag ueber tausend Titel waere
#: darum eine schwarze Schachtel. In Haeppchen zerlegt zaehlt Nexview selbst,
#: und der Balken sagt die Wahrheit.
HAEPPCHEN = 25

#: Wie viele Vorschauen gleichzeitig laufen duerfen.
#:
#: ⚠️ **Die Vorschau geht nur titelweise** - ``/rename`` lehnt eine Liste mit
#: 400 ab (live gemessen 28.08.2026). Bei einer Bibliothek mit mehreren tausend
#: Titeln waere das nacheinander eine Viertelstunde. Nebeneinander wird daraus
#: rund eine Minute; mehr als eine Handvoll gleichzeitig bringt nichts mehr und
#: setzt die Instanz nur unter Druck, waehrend sie noch anderes tut.
GLEICHZEITIG = 8

#: Welche TRaSH-Fassung zu welchem Medienserver passt.
#:
#: Plex schreibt die Kennung in geschweifte, Emby und Jellyfin in eckige
#: Klammern - deshalb gibt es ueberhaupt Fassungen. Ohne Medienserver gilt die
#: schlichte, die ohne Kennung auskommt.
FASSUNGEN = {
    "plex": "plex-tmdb",
    "emby": "emby-tmdb",
    "jellyfin": "jellyfin-tmdb",
}
FASSUNGEN_SONARR = {
    "plex": "plex-tvdb",
    "emby": "emby-tvdb",
    "jellyfin": "jellyfin-tvdb",
}


@dataclass
class Umbenennstand:
    """Wie weit das Umbenennen des Bestands ist.

    Zwei Abschnitte mit eigenem Zaehler: erst wird **geprueft** (nur gelesen,
    jederzeit gefahrlos abzubrechen), dann **umbenannt**. Die Trennung steht
    auch in der Oberflaeche, weil nur der zweite Teil Dateien anfasst.
    """

    instanz: str = ""
    #: "pruefen" | "umbenennen" | "fertig"
    schritt: str = "pruefen"
    erledigt: int = 0
    gesamt: int = 0
    #: Wie viele Titel tatsaechlich einen neuen Namen bekommen.
    betroffen: int = 0
    #: Ein paar Beispiele, damit man sieht, was gemeint ist.
    beispiele: list[str] = field(default_factory=list)
    #: Nach einem Abbruch wieder aufgenommen? Gehoert in die Oberflaeche.
    fortgesetzt: bool = False


_umbenennen: dict[str, Umbenennstand] = {}
_laeufe: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Festhalten, wie weit der Lauf ist
#
# ⚠️ **Warum ueberhaupt in der Datenbank.** Ein Lauf ueber mehrere tausend Titel
# dauert lange. Faellt der Prozess mittendrin aus - Container-Neustart,
# zugeklappter Deckel, Absturz -, bliebe sonst eine halb umbenannte Bibliothek
# zurueck, ohne dass irgendwo stuende, wo die Grenze verlaeuft.
#
# Geschrieben wird **nach jedem Haeppchen**, nicht nach jedem Titel: Umbenennen
# ist wiederholbar, ein abgebrochenes Haeppchen darf also einfach noch einmal
# laufen. Damit kostet der Schutz 160 Schreibvorgaenge statt 4000.
# ---------------------------------------------------------------------------


def _merken(
    kennung: str,
    dienst: str,
    stand: Umbenennstand,
    offen: list[int] | None = None,
) -> None:
    """Den Stand festhalten. Fehler hier duerfen den Lauf nicht kippen."""
    try:
        with SessionLocal() as db:
            lauf = db.scalar(select(Umbenennlauf).where(Umbenennlauf.kennung == kennung))
            if lauf is None:
                lauf = Umbenennlauf(kennung=kennung)
                db.add(lauf)
            lauf.dienst = dienst
            lauf.instanz = stand.instanz
            lauf.schritt = stand.schritt
            lauf.gesamt = stand.gesamt
            lauf.erledigt = stand.erledigt
            lauf.betroffen = stand.betroffen
            lauf.beispiele = list(stand.beispiele)
            lauf.fortgesetzt = stand.fortgesetzt
            if offen is not None:
                lauf.offen = list(offen)
            lauf.beruehrt_am = utcnow()
            db.commit()
    except Exception:  # noqa: BLE001 - lieber ohne Netz weiterlaufen als abbrechen
        logger.exception("Could not record rename progress for %s", kennung)


def _vergessen(kennung: str) -> None:
    """Einen abgeschlossenen Lauf aus der Datenbank nehmen."""
    try:
        with SessionLocal() as db:
            lauf = db.scalar(select(Umbenennlauf).where(Umbenennlauf.kennung == kennung))
            if lauf is not None:
                db.delete(lauf)
                db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not clear rename record for %s", kennung)


def offene_laeufe() -> list[dict[str, Any]]:
    """Laeufe, die beim letzten Mal nicht fertig geworden sind.

    ⚠️ Auch ein Lauf im Schritt "pruefen" gilt als offen. Das Pruefen selbst
    liest nur - abzubrechen ist dort gefahrlos -, aber der Nutzer hat einen
    Lauf angestossen und erwartet, dass er stattfindet.
    """
    try:
        with SessionLocal() as db:
            return [
                {
                    "kennung": lauf.kennung,
                    "dienst": lauf.dienst,
                    "schritt": lauf.schritt,
                    "offen": list(lauf.offen or []),
                    "erledigt": lauf.erledigt,
                    "gesamt": lauf.gesamt,
                    "betroffen": lauf.betroffen,
                    "beispiele": list(lauf.beispiele or []),
                    "fortgesetzt": lauf.fortgesetzt,
                    "instanz": lauf.instanz,
                }
                for lauf in db.scalars(
                    select(Umbenennlauf).where(Umbenennlauf.schritt.notin_(("fertig",)))
                )
            ]
    except Exception:  # noqa: BLE001
        logger.exception("Could not read unfinished rename runs")
        return []


def umbenennstand(kennung: str) -> Umbenennstand | None:
    """Der Stand aus dem Arbeitsspeicher - oder aus der Datenbank.

    ⚠️ **Der Rueckgriff auf die Datenbank ist nicht bloss Vorsicht.** Ohne ihn
    findet nur die Sitzung den Lauf wieder, die ihn angestossen hat: Wer die
    Seite neu laedt, sieht einen leeren Balken, waehrend im Hintergrund noch
    tausende Dateien umbenannt werden. Nach einem Neustart der Anwendung gilt
    dasselbe. Der Zwischenstand liegt ohnehin fest - dann soll ihn auch jeder
    sehen koennen.
    """
    im_speicher = _umbenennen.get(kennung)
    if im_speicher is not None:
        return im_speicher
    for lauf in offene_laeufe():
        if lauf["kennung"] != kennung:
            continue
        return Umbenennstand(
            instanz=lauf.get("instanz") or kennung,
            schritt=lauf["schritt"],
            erledigt=lauf["erledigt"],
            gesamt=lauf["gesamt"],
            betroffen=lauf["betroffen"],
            beispiele=list(lauf["beispiele"]),
            fortgesetzt=lauf["fortgesetzt"],
        )
    return None


def laeuft_schon(kennung: str) -> bool:
    auftrag = _laeufe.get(kennung)
    return auftrag is not None and not auftrag.done()


@contextmanager
def umbenennen_fuehren(kennung: str) -> Iterator[Umbenennstand]:
    """Den Stand fuer die Dauer eines Laufs bereitstellen - und danach raeumen."""
    stand = Umbenennstand()
    _umbenennen[kennung] = stand
    try:
        yield stand
    finally:
        _umbenennen.pop(kennung, None)


def anstossen(
    client: ArrClient,
    dienst: str,
    kennung: str,
    weiter_mit: list[int] | None = None,
) -> None:
    """Den Bestand im Hintergrund angleichen.

    ⚠️ **Nicht in der HTTP-Anfrage.** Eine Bibliothek mit mehreren tausend
    Titeln braucht dafuer Minuten: Jeder Titel muss einzeln gefragt werden, weil
    ``/rename`` keine Liste annimmt. Eine Anfrage, die so lange offen bleibt,
    stirbt unterwegs an einem Reverse Proxy, einem Browser-Zeitlimit oder einem
    zugeklappten Deckel - und niemand wuesste, wie weit sie gekommen ist.
    Deshalb: anstossen, sofort antworten, den Fortschritt erfragen lassen.

    Der Stand bleibt nach dem Ende **stehen** (Schritt "fertig"), damit die
    Oberflaeche das Ergebnis noch zeigen kann.
    """
    if laeuft_schon(kennung):
        logger.info("Rename already running for %s - not starting a second one", kennung)
        return

    stand = Umbenennstand()
    stand.fortgesetzt = bool(weiter_mit)
    _umbenennen[kennung] = stand

    async def lauf() -> None:
        try:
            await bestand_umbenennen(client, dienst, stand, kennung, weiter_mit)
        except Exception:  # noqa: BLE001 - der Lauf darf die Anwendung nicht mitnehmen
            logger.exception("Renaming the library on %s failed", client.label)
            stand.schritt = "fehler"
            # ⚠️ Den Eintrag **stehen lassen**: Beim naechsten Start wird
            # weitergemacht. Ein geloeschter Eintrag hiesse, den Rest der
            # Bibliothek stillschweigend aufzugeben.
            _merken(kennung, dienst, stand)

    _laeufe[kennung] = asyncio.create_task(lauf())


#: Welche Verbindungen einen Medienserver ueber Aenderungen unterrichten.
MEDIENSERVER_VERBINDUNGEN = {"PlexServer", "Emby", "MediaBrowser", "Jellyfin"}


@dataclass
class Vorschlag:
    """Was jetzt gilt und was TRaSH stattdessen empfiehlt."""

    kennung: str
    name: str
    dienst: str
    umbenennen_an: bool
    datei_ist: str = ""
    datei_soll: str = ""
    ordner_ist: str = ""
    ordner_soll: str = ""
    #: Welche Fassung benutzt wurde ("default", "plex-tmdb", ...).
    fassung: str = "default"
    #: ⚠️ Sagt diese Instanz dem Medienserver Bescheid, wenn sich etwas aendert?
    #:
    #: Ohne diese Verbindung merkt Plex, Emby oder Jellyfin vom Umbenennen
    #: nichts - bis zum naechsten eigenen Durchlauf zeigen sie ins Leere. Das
    #: ist der Unterschied zwischen "kurz weg" und "stundenlang kaputt", und
    #: deshalb steht es vor dem Knopf und nicht im Kleingedruckten.
    meldet_medienserver: bool = True


def _namen(dienst: str) -> dict[str, Any]:
    daten = schnappschuss(dienst).get("namen") or {}
    # Die Datei heisst "radarr-naming" bzw. "sonarr-naming".
    for inhalt in daten.values():
        if isinstance(inhalt, dict):
            return inhalt
    return {}


def empfehlung(dienst: str, medienserver: str = "") -> tuple[str, str, str]:
    """Datei- und Ordnerschema aus den Guides, passend zum Medienserver.

    Gibt ``(datei, ordner, fassung)`` zurueck. Fehlt eine Fassung, gilt die
    schlichte - lieber ein Schema ohne Kennung als gar keines.
    """
    namen = _namen(dienst)
    if dienst == "radarr":
        dateien = namen.get("file") or {}
        ordner = namen.get("folder") or {}
        wunsch = FASSUNGEN.get(medienserver, "")
        datei = dateien.get(wunsch) or dateien.get("standard") or ""
        ordnername = ordner.get(wunsch) or ordner.get("default") or ""
        return datei, ordnername, wunsch if wunsch in dateien else "standard"

    # ⚠️ **Sonarr liegt eine Ebene tiefer als Radarr.** Dort ist ``episodes``
    # nach Art gegliedert (normal, taeglich, Anime) und *jede* Art hat wieder
    # Fassungen ("default", "original", "p2p-scene"). Wer das uebersieht,
    # schickt ein Objekt statt eines Schemas los.
    serien = namen.get("series") or {}
    wunsch = FASSUNGEN_SONARR.get(medienserver, "")
    datei = _sonarr_dateischema(namen, "standard")
    ordnername = serien.get(wunsch) or serien.get("default") or ""
    return datei, ordnername, wunsch if wunsch in serien else "default"


def _sonarr_dateischema(namen: dict[str, Any], art: str) -> str:
    """Das Schema einer Episoden-Art - immer die Fassung ``default``."""
    eintrag = (namen.get("episodes") or {}).get(art)
    if isinstance(eintrag, dict):
        return str(eintrag.get("default") or "")
    return str(eintrag or "")


async def vorschlag_fuer(
    client: ArrClient, kennung: str, name: str, dienst: str, medienserver: str
) -> Vorschlag:
    """Lesen, was gilt - und danebenstellen, was empfohlen wird."""
    ist = await client.benennung()
    felder = FELDER[dienst]
    datei_soll, ordner_soll, fassung = empfehlung(dienst, medienserver)

    # ⚠️ Sagt die Instanz dem Medienserver Bescheid? Radarr und Sonarr bieten
    # dafuer "Plex Media Server" und "Emby / Jellyfin" an, beide mit dem
    # Ereignis **OnRename**. Fehlt die Verbindung, merkt der Medienserver vom
    # Umbenennen erst beim naechsten eigenen Durchlauf etwas - und zeigt bis
    # dahin ins Leere.
    try:
        verbindungen = await client.notifications()
        meldet = any(
            v.get("implementation") in MEDIENSERVER_VERBINDUNGEN
            and (v.get("onRename") or v.get("onDownload"))
            for v in verbindungen
        )
    except Exception:  # noqa: BLE001 - die Auskunft ist eine Zugabe, kein Muss
        logger.debug("Could not read connections of %s", client.label)
        meldet = True

    return Vorschlag(
        kennung=kennung,
        name=name,
        dienst=dienst,
        umbenennen_an=bool(ist.get(felder["umbenennen"])),
        datei_ist=str(ist.get(felder["datei"]) or ""),
        datei_soll=datei_soll,
        ordner_ist=str(ist.get(felder["ordner"]) or ""),
        ordner_soll=ordner_soll,
        fassung=fassung,
        meldet_medienserver=meldet,
    )


async def uebernehmen(
    client: ArrClient, dienst: str, datei: bool, ordner: bool, medienserver: str
) -> Vorschlag:
    """Das empfohlene Schema setzen - nur die ausgewaehlten Teile.

    ⚠️ Gelesen, veraendert, zurueckgeschrieben. Ein selbst gebauter Datensatz
    loeschte Felder, die diese Fassung fuehrt und wir nicht kennen.
    """
    stand = await client.benennung()
    felder = FELDER[dienst]
    datei_soll, ordner_soll, _fassung = empfehlung(dienst, medienserver)

    if datei and datei_soll:
        stand[felder["datei"]] = datei_soll
        # Sonarr fuehrt eigene Schemata fuer taegliche Formate und Anime. Wer
        # nur das normale setzt, laesst zwei Drittel der Bibliothek unberuehrt.
        namen = _namen(dienst)
        for schluessel, art in (("datei_taeglich", "daily"), ("datei_anime", "anime")):
            if schluessel not in felder:
                continue
            schema = _sonarr_dateischema(namen, art)
            if schema:
                stand[felder[schluessel]] = schema
    if ordner and ordner_soll:
        stand[felder["ordner"]] = ordner_soll
        namen_s = _namen(dienst)
        if "ordner_staffel" in felder:
            staffel = (namen_s.get("season") or {}).get("default")
            if staffel:
                stand[felder["ordner_staffel"]] = staffel

    # Ohne diesen Schalter greift das Schema gar nicht - Radarr benennt dann
    # ueberhaupt nicht um, auch nicht bei neuen Dateien.
    if datei or ordner:
        stand[felder["umbenennen"]] = True

    await client.benennung_speichern(stand)
    logger.info(
        "Naming scheme applied to %s (file=%s, folder=%s)", client.label, datei, ordner
    )
    return await vorschlag_fuer(client, "", client.label, dienst, medienserver)


async def bestand_umbenennen(
    client: ArrClient,
    dienst: str,
    melden: Umbenennstand,
    kennung: str = "",
    weiter_mit: list[int] | None = None,
) -> Umbenennstand:
    """Den vorhandenen Bestand an das Schema angleichen.

    ⚠️ **Der Teil, der Dateien anfasst.** Erst wird jeder Titel gefragt, was
    sich aendern wuerde - das ist reines Lesen. Erst danach gehen Auftraege
    hinaus, und zwar nur fuer die Titel, bei denen wirklich etwas anders wird.
    Wer nichts umzubenennen hat, loest auch keinen Auftrag aus.

    Der Fortschritt wird selbst gezaehlt: Radarr und Sonarr melden zu einem
    Auftrag nur "laeuft" oder "fertig".
    """
    art = TITEL[dienst]
    melden.instanz = client.label

    # ⚠️ **Ein fortgesetzter Lauf ueberspringt die Vorschau.** Sie steht schon
    # in der Datenbank; sie noch einmal ueber mehrere tausend Titel laufen zu
    # lassen kostete Minuten und brachte dasselbe Ergebnis.
    if weiter_mit:
        melden.fortgesetzt = True
        melden.schritt = "umbenennen"
        zu_tun = sorted(weiter_mit)
        logger.info(
            "Resuming rename on %s with %d title(s) still open", client.label, len(zu_tun)
        )
        return await _haeppchen_abarbeiten(client, art, melden, zu_tun, kennung, dienst)

    titel = await client.get(art["liste"]) or []
    nummern = [int(e["id"]) for e in titel if e.get("id")]
    melden.schritt = "pruefen"
    melden.gesamt = len(nummern)
    melden.erledigt = 0
    if kennung:
        _merken(kennung, dienst, melden, offen=[])

    zu_tun: list[int] = []
    bremse = asyncio.Semaphore(GLEICHZEITIG)

    async def pruefen(nummer: int) -> None:
        async with bremse:
            try:
                aenderungen = await client.umbenennen_vorschau(art["feld"], nummer)
            except Exception:  # noqa: BLE001 - ein stummer Titel darf den Lauf nicht kippen
                logger.exception("Rename preview failed for %s %s", dienst, nummer)
                aenderungen = []
        if aenderungen:
            zu_tun.append(nummer)
            if len(melden.beispiele) < 5:
                erste = aenderungen[0]
                melden.beispiele.append(
                    str(erste.get("newPath") or erste.get("existingPath") or "")
                )
        melden.erledigt += 1
        melden.betroffen = len(zu_tun)

    await asyncio.gather(*(pruefen(n) for n in nummern))
    # Die Reihenfolge ging beim Nebeneinander verloren - fuer die Haeppchen
    # zaehlt sie nicht, aber ein stabiler Ablauf liest sich im Protokoll besser.
    zu_tun.sort()

    if not zu_tun:
        melden.schritt = "fertig"
        logger.info("Nothing to rename on %s", client.label)
        if kennung:
            _vergessen(kennung)
        return melden

    melden.schritt = "umbenennen"
    return await _haeppchen_abarbeiten(client, art, melden, zu_tun, kennung, dienst)


async def _haeppchen_abarbeiten(
    client: ArrClient,
    art: dict[str, str],
    melden: Umbenennstand,
    zu_tun: list[int],
    kennung: str,
    dienst: str,
) -> Umbenennstand:
    """Die eigentliche Umbenennung - haeppchenweise, mit Zwischenstand.

    ⚠️ **Nach jedem Haeppchen wird festgehalten, was noch offen ist.** Bricht
    der Prozess danach ab, nimmt der naechste Start genau hier wieder auf. Ohne
    das bliebe eine halb umbenannte Bibliothek zurueck - und niemand wuesste,
    wo die Grenze verlaeuft.
    """
    melden.gesamt = len(zu_tun)
    melden.erledigt = 0
    offen = list(zu_tun)
    if kennung:
        _merken(kennung, dienst, melden, offen=offen)

    for start in range(0, len(zu_tun), HAEPPCHEN):
        haeppchen = zu_tun[start : start + HAEPPCHEN]
        auftrag = await client.befehl(art["befehl"], **{art["felder"]: haeppchen})
        auftrag_id = int(auftrag.get("id") or 0)
        # Abwarten, sonst rennen die Haeppchen einander hinterher und der
        # Balken zeigt Arbeit an, die noch gar nicht getan ist.
        while auftrag_id:
            zustand = await client.befehl_stand(auftrag_id)
            if str(zustand.get("status") or "") in ("completed", "failed", "aborted"):
                break
            await asyncio.sleep(1.0)
        melden.erledigt += len(haeppchen)
        offen = offen[len(haeppchen) :]
        if kennung:
            _merken(kennung, dienst, melden, offen=offen)

    melden.schritt = "fertig"
    logger.info("Renamed %d title(s) on %s", len(zu_tun), client.label)
    if kennung:
        _vergessen(kennung)
    return melden


def abgebrochene_aufnehmen() -> int:
    """Laeufe wieder aufnehmen, die beim letzten Mal nicht fertig wurden.

    ⚠️ **Wird beim Start aufgerufen.** Ohne das waere die Sicherung sinnlos:
    Der Zwischenstand laege zwar in der Datenbank, aber niemand griffe ihn auf,
    und der Betreiber saesse vor einer halb umbenannten Bibliothek.

    Bewusst **ohne Rueckfrage**: Ein angefangener Umbenennungslauf ist ein
    Zustand, den niemand haben will - halb altes, halb neues Schema. Ihn zu
    Ende zu bringen stellt her, was der Betreiber ohnehin angestossen hat.

    Gibt zurueck, wie viele Laeufe aufgenommen wurden.
    """
    # Erst hier holen: Beim Import waere ``settings_service`` noch nicht bereit.
    from .settings_service import load_settings

    offene = offene_laeufe()
    if not offene:
        return 0

    with SessionLocal() as db:
        instanzen = {i.kennung: i for i in load_settings(db).arr_instanzen()}

    aufgenommen = 0
    for lauf in offene:
        instanz = instanzen.get(lauf["kennung"])
        if instanz is None:
            # Die Instanz gibt es nicht mehr - der Eintrag ist gegenstandslos.
            logger.info(
                "Dropping unfinished rename for %s: instance no longer configured",
                lauf["kennung"],
            )
            _vergessen(lauf["kennung"])
            continue
        client = ArrClient(instanz.url, instanz.api_key, instanz.name)
        logger.info(
            "Picking up unfinished rename on %s (%d title(s) still open)",
            instanz.name,
            len(lauf["offen"]),
        )
        anstossen(client, lauf["dienst"], lauf["kennung"], lauf["offen"] or None)
        aufgenommen += 1
    return aufgenommen
