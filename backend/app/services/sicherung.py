"""Sicherungen anlegen, auflisten und als verschluesseltes Archiv ausliefern.

Es gibt zwei Anlaesse. **Automatisch** legt ``db.init_db`` eine Kopie an, bevor
es das Schema anfasst - das ist ein Ruecksetzpunkt fuer eine misslungene
Wanderung. **Von Hand** legt der Betreiber eine an, wenn er etwas Riskantes
vorhat; die darf einen Kommentar tragen und wird nie automatisch weggeraeumt.

⚠️ **Eine Kopie neben der Datenbank ist noch keine Sicherung.** Beide liegen im
selben Verzeichnis, also auf demselben Volume - stirbt das, sind sie zusammen
weg. Deshalb gibt es hier zusaetzlich ``archiv()``: Damit laesst sich eine
Sicherung vom Rechner **herunterladen**, und erst dann ist sie eine.

Was in so ein Archiv gehoert - und warum das mehr ist als die Datenbank
-------------------------------------------------------------------------
Die Zugaenge zu Radarr, Sonarr, TMDB und dem Mailserver liegen verschluesselt
in der Datenbank. Der Schluessel dazu steht **nicht** darin, sondern daneben in
``secret.key``. Wer nur die Datenbank sichert, merkt das erst im Ernstfall:
Beim Einspielen auf einer frischen Installation erzeugt Nexview stillschweigend
einen neuen Schluessel, und danach laesst sich kein einziger Zugang mehr lesen.
Man muesste alles neu eintragen.

Deshalb wandert ``secret.key`` mit ins Archiv - und deshalb ist das Archiv
**passwortgeschuetzt**. Mit dem Schluessel darin gibt die Datei tatsaechlich
alles her.

Aus demselben Grund wandern die Profilbilder und der geholte TRaSH-Stand mit
(siehe ``BEILAGEN``): Beides liegt als Datei neben der Datenbank, und beides
faellt erst nach dem Einspielen auf - als verlorene Bilder und als
Qualitaetsprofile, die gegen einen fremden Stand gemessen werden.

⚠️ **AES-ZIP und kein eigenes Format.** Ein selbstgebautes Format koennte nur
Nexview wieder oeffnen - und ausgerechnet dann, wenn man die Sicherung braucht,
laeuft Nexview vielleicht nicht. Ein ZIP bekommt man mit 7-Zip, WinRAR oder dem
Explorer auf. Die Daten bleiben ihrem Besitzer zugaenglich, auch ohne uns.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

import pyzipper

from .. import __version__
from ..config import get_settings

logger = logging.getLogger(__name__)

ORDNER_NAME = "sicherungen"
AUTOMATISCH = "automatisch"
MANUELL = "manuell"

#: So viele automatische Staende bleiben liegen. Von Hand angelegte zaehlen
#: **nicht** mit - wer bewusst eine Sicherung anlegt, soll sie nicht durch den
#: naechsten Versionssprung verlieren.
AUTOMATISCH_BEHALTEN = 5

#: Tabellen, die beim Sichern geleert werden.
#:
#: ⚠️ Das ist kein Detail, sondern der Unterschied zwischen 180 MB und 3 MB:
#: Auf einer gewachsenen Installation sind ueber 90 % der Datei
#: TMDB-Zwischenspeicher. Der ist in Stunden wieder da und in einer Sicherung
#: nichts als Ballast - eine Sicherung, die zu gross zum Herunterladen ist,
#: laedt niemand herunter.
ZWISCHENSPEICHER = ("tmdb_cache", "arr_library_cache")

#: Was statt des Schluessels ins Archiv wandert, wenn keiner danebenliegt.
#:
#: Er steht dann in NEXVIEW_SECRET_KEY, also in der Docker-Datei - und der
#: Hinweis darauf gehoert ins Archiv, nicht in ein Protokoll, das niemand liest.
OHNE_SCHLUESSEL = (
    "Dieser Installation liegt kein secret.key bei - der Schluessel kommt\n"
    "aus der Umgebungsvariablen NEXVIEW_SECRET_KEY.\n\n"
    "Beim Wiederherstellen muss derselbe Wert wieder gesetzt sein, sonst\n"
    "lassen sich die gespeicherten Zugaenge zu Radarr, Sonarr, TMDB und\n"
    "dem Mailserver nicht mehr entschluesseln.\n"
)

#: Name des Steckbriefs im Archiv.
STECKBRIEF = "nexview-sicherung.json"

#: Ordner im Datenverzeichnis, die zur Sicherung gehoeren - neben der Datenbank.
#:
#: ``avatars`` enthaelt die Profilbilder; in der Datenbank steht nur ihr Name.
#: ``trash`` enthaelt den geholten TRaSH-Stand. Der klingt nach Beiwerk, ist
#: aber der Massstab: Zu jedem Qualitaetsprofil steht in der Datenbank, gegen
#: welchen Stand es geschrieben wurde. Fehlt der Ordner, faellt Nexview auf den
#: mitgelieferten Abzug zurueck und meldet danach Abweichungen an Profilen, an
#: denen niemand etwas geaendert hat.
#: ``hausordnung`` enthaelt die Bilder im Regeltext des Betreibers - wieder
#: steht in der Datenbank nur ihr Name. Ohne den Ordner kaeme der Text
#: vollstaendig zurueck und zeigte an jeder Bildstelle eine Luecke; geschrieben
#: hat ihn jemand von Hand, ein zweites Mal will das niemand tun.
BEILAGEN = ("avatars", "trash", "hausordnung")

#: Was im Datenverzeichnis liegt und **nicht** ins Archiv wandert - mit Grund.
#:
#: ⚠️ **Diese Liste ist der Waechter, nicht die Ausrede.** Bis hierher pruefte
#: jeder Test benannte Bestandteile: "ist die Datenbank drin, ist der Schluessel
#: drin". Genau deshalb konnte ``trash/`` unbemerkt fehlen - es stand auf keiner
#: Liste, also fragte niemand danach. Der Test dazu dreht das um: Alles im
#: Datenverzeichnis muss entweder ins Archiv gehen oder **hier** stehen, samt
#: Begruendung.
#:
#: ⚠️ **Beim Fehlschlag wird hier nicht routinemaessig ein Eintrag ergaenzt.**
#: Ein neuer Name heisst: Jemand hat etwas angelegt, ohne zu entscheiden, ob es
#: in eine Sicherung gehoert. Diese Entscheidung ist der Zweck des roten Laufs.
#: Wer hier eintraegt, schreibt den Grund dazu - und wenn ihm keiner einfaellt,
#: gehoert der Eintrag ins Archiv statt in diese Liste.
NICHT_INS_ARCHIV = {
    "logs": (
        "Das Protokoll beschreibt den Betrieb, nicht den Stand. Vier Wochen "
        "alte Zeilen in einer frisch eingespielten Installation waeren "
        "irrefuehrend, und die Datei ist das Groesste im Verzeichnis."
    ),
    ORDNER_NAME: (
        "Die Sicherungen selbst. Eine Sicherung, die alle vorherigen "
        "enthaelt, waechst bei jedem Lauf um sich selbst."
    ),
    "nexview.db-wal": (
        "Begleitdatei von SQLite. VACUUM INTO schreibt eine in sich "
        "stimmige Kopie - die Begleitdatei einer anderen Datenbank waere "
        "beim Einspielen schaedlich, nicht nuetzlich (siehe wiederherstellen)."
    ),
    "nexview.db-shm": ("Begleitdatei von SQLite, dasselbe wie -wal."),
}

#: Was umgekehrt ins Archiv gehoert. Zusammen mit der Liste darueber muss das
#: **alles** abdecken, was im Datenverzeichnis liegt.
IM_ARCHIV = ("nexview.db", "secret.key", *BEILAGEN)


#: Woran der Ordner mit den Beilagen einer Sicherung zu erkennen ist.
#:
#: Kein ``.``-Suffix: ``datei()`` laesst nur ``.db`` durch, und ``aufraeumen``
#: zaehlt ueber ``*.db``. Ein Ordner, der auf ``-dateien`` endet, kommt keinem
#: von beiden in die Quere.
BEILAGEN_ORDNER = "-dateien"


@dataclass(slots=True)
class Steckbrief:
    """Was eine Sicherung ueber sich selbst weiss."""

    version: str
    schema: str
    erstellt: str
    art: str
    kommentar: str = ""
    enthaelt: list[str] = field(default_factory=list)
    geleert: list[str] = field(default_factory=list)

    def als_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def aus_json(cls, roh: str | bytes) -> "Steckbrief":
        daten = json.loads(roh)
        erlaubt = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in daten.items() if k in erlaubt})


@dataclass(slots=True)
class Eintrag:
    """Eine Sicherung, wie sie in der Liste erscheint."""

    name: str
    groesse: int
    erstellt: str
    art: str
    kommentar: str
    version: str


def ordner() -> Path:
    return get_settings().data_dir / ORDNER_NAME


def nicht_zugeordnet(datenverzeichnis: Path | None = None) -> list[str]:
    """Was im Datenverzeichnis liegt, ohne dass jemand entschieden hat, wohin es gehoert.

    ⚠️ **Die Frage, die vorher niemand gestellt hat.** Die Tests zur Sicherung
    pruefen benannte Bestandteile - und finden deshalb nie etwas, das auf keiner
    Liste steht. So konnte ``trash/`` fehlen: Es war nicht vergessen worden,
    es war nie gefragt worden.

    Leere Rueckgabe heisst: Zu jedem Eintrag gibt es eine Entscheidung. Steht
    etwas darin, fehlt genau diese Entscheidung - nicht unbedingt ein Eintrag im
    Archiv.
    """
    wurzel = datenverzeichnis or get_settings().data_dir
    if not wurzel.is_dir():
        return []
    bekannt = set(IM_ARCHIV) | set(NICHT_INS_ARCHIV)
    return sorted(e.name for e in wurzel.iterdir() if e.name not in bekannt)


def _steckbrief_pfad(sicherung: Path) -> Path:
    """Der Steckbrief liegt **neben** der Datei, nicht darin.

    In die Datenbank selbst gehoert er nicht: Sie wird beim Wiederherstellen ja
    gerade ersetzt, und eine Liste, die dabei verschwindet, ist keine.
    """
    return sicherung.with_suffix(".json")


def _beilagen_pfad(sicherung: Path) -> Path:
    """Der Ordner mit den Dateien, die zu **dieser** Sicherung gehoeren.

    Ein eigener Ordner je Sicherung und kein gemeinsamer: Zu einem Stand
    gehoeren die Bilder und der TRaSH-Abzug von damals, und zwei Staende haben
    verschiedene.
    """
    return sicherung.with_name(sicherung.stem + BEILAGEN_ORDNER)


def _beilagen_sichern(sicherung: Path) -> list[str]:
    """Bilder und TRaSH-Stand neben die Kopie legen - wie sie **jetzt** sind.

    ⚠️ **Hier und nicht erst beim Herunterladen.** Vorher wurde die Datenbank
    beim Anlegen kopiert und die Dateien daneben erst beim Packen des Archivs
    aus dem laufenden Datenverzeichnis geholt. Wer das Archiv einer vier Wochen
    alten Sicherung zog, bekam eine alte Datenbank mit heutigen Dateien: ein
    ausgetauschtes Profilbild kam als kaputtes Bild zurueck, und die
    Qualitaetsprofile massen sich an einem TRaSH-Stand, den es zu ihrer Zeit
    noch nicht gab.

    Der Ordner entsteht **auch dann, wenn nichts darin landet**. Er ist das
    Kennzeichen dafuer, dass diese Sicherung ihre Beilagen selbst mitbringt -
    ohne ihn liesse sich "hatte damals keine Bilder" nicht von "stammt aus
    einer Fassung vor diesem Umbau" unterscheiden, und ``archiv`` muesste
    raten.
    """
    ordner_ = _beilagen_pfad(sicherung)
    ordner_.mkdir(parents=True, exist_ok=True)

    daten = get_settings().data_dir
    kopiert: list[str] = []
    for name in BEILAGEN:
        quelle = daten / name
        if not quelle.is_dir():
            continue
        for datei_ in sorted(quelle.iterdir()):
            if not datei_.is_file():
                continue
            ziel_datei = ordner_ / name / datei_.name
            try:
                ziel_datei.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(datei_, ziel_datei)
            except OSError as fehler:
                # ⚠️ Daran darf die Sicherung nicht scheitern - die Datenbank
                # ist der Teil, auf den es ankommt. Verschwiegen wird es
                # trotzdem nicht: Der Steckbrief zaehlt gleich auf, was
                # wirklich hier liegt, und diese Datei fehlt dann darin.
                logger.warning("Could not copy %s into backup: %s", datei_.name, fehler)
                continue
            kopiert.append(f"{name}/{datei_.name}")
    return kopiert


def entfernen(sicherung: Path) -> None:
    """Eine Sicherung restlos loeschen: Datenbank, Steckbrief, Beilagen.

    ⚠️ **Der einzige Weg, eine Sicherung loszuwerden - absichtlich.** Zu ihr
    gehoeren drei Dinge, nicht mehr nur zwei. Wer den Beilagenordner an einer
    Stelle vergisst, sammelt dort die Bilder und TRaSH-Abzuege aller je
    aufgeraeumten Staende, und niemand sucht in einem Sicherungsordner nach
    verlorenem Platz.
    """
    sicherung.unlink(missing_ok=True)
    _steckbrief_pfad(sicherung).unlink(missing_ok=True)
    shutil.rmtree(_beilagen_pfad(sicherung), ignore_errors=True)


def schema_fingerabdruck(verbindung: sqlite3.Connection) -> str:
    """Ein Abdruck ueber alle Tabellen und Spalten.

    ⚠️ **Verlaesslicher als die Versionsnummer.** Eine Nummer sagt nur, welche
    Fassung die Datei geschrieben hat; der Abdruck sagt, wie sie **aussieht**.
    Damit laesst sich beim Wiederherstellen die Frage beantworten, auf die es
    ankommt: Kennt die laufende Fassung dieses Schema, oder ist es neuer als
    sie?
    """
    teile: list[str] = []
    tabellen = verbindung.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for (tabelle,) in tabellen:
        spalten = sorted(
            zeile[1] for zeile in verbindung.execute(f"PRAGMA table_info([{tabelle}])")
        )
        teile.append(f"{tabelle}({','.join(spalten)})")
    roh = ";".join(teile).encode("utf-8")
    return "sha256:" + hashlib.sha256(roh).hexdigest()[:32]


def _sicherer_name(kommentar: str) -> str:
    """Aus dem Kommentar ein Stueck Dateiname machen - oder nichts.

    ⚠️ Der Kommentar kommt vom Benutzer und landet in einem Dateinamen. Alles
    ausser Buchstaben, Ziffern und Bindestrich fliegt raus; Punkte und
    Schraegstriche wuerden sonst aus dem Ordner herausfuehren.
    """
    knapp = re.sub(r"[^\w-]+", "-", kommentar.strip(), flags=re.UNICODE).strip("-")
    return knapp[:40].lower()


def anlegen(*, art: str = MANUELL, kommentar: str = "") -> Path:
    """Eine Sicherung schreiben und ihren Steckbrief daneben legen.

    ``VACUUM INTO`` erzeugt eine in sich stimmige Kopie, auch waehrend
    geschrieben wird - ein blosses Kopieren der Datei wuerde im WAL-Betrieb die
    zuletzt gespeicherten Aenderungen verlieren.
    """
    # Erst hier importiert: ``db`` legt beim Start selbst Sicherungen an, ein
    # Import auf Modulebene waere ein Ring.
    from ..db import engine

    ziel_ordner = ordner()
    ziel_ordner.mkdir(parents=True, exist_ok=True)

    stempel = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    zusatz = _sicherer_name(kommentar)
    grund = f"nexview-{art}-{__version__}-{stempel}" + (f"-{zusatz}" if zusatz else "")

    ziel = ziel_ordner / f"{grund}.db"
    # VACUUM INTO weigert sich, eine vorhandene Datei zu ueberschreiben.
    zaehler = 2
    while ziel.exists():
        ziel = ziel_ordner / f"{grund}-{zaehler}.db"
        zaehler += 1

    with engine.connect() as verbindung:
        # Der Pfad kommt aus der eigenen Konfiguration, nicht von aussen.
        # Einfache Anfuehrungszeichen darin wuerden das Statement dennoch
        # zerlegen - deshalb werden sie verdoppelt.
        verbindung.exec_driver_sql(f"VACUUM INTO '{str(ziel).replace(chr(39), chr(39) * 2)}'")

    geleert = _zwischenspeicher_leeren(ziel)
    beilagen = _beilagen_sichern(ziel)

    roh = sqlite3.connect(ziel)
    try:
        abdruck = schema_fingerabdruck(roh)
    finally:
        roh.close()

    brief = Steckbrief(
        version=__version__,
        schema=abdruck,
        erstellt=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        art=art,
        kommentar=kommentar.strip(),
        # ⚠️ Was wirklich hier liegt, nicht was hier liegen sollte. ``secret.key``
        # steht bewusst **nicht** darin: Der Schluessel bleibt im
        # Datenverzeichnis und kommt erst beim Packen des Archivs dazu.
        enthaelt=["nexview.db", *beilagen],
        geleert=geleert,
    )
    _steckbrief_pfad(ziel).write_text(brief.als_json(), encoding="utf-8")

    logger.info(
        "Backup created: %s (%s, %.1f MB)", ziel.name, art, ziel.stat().st_size / 1048576
    )
    if art == AUTOMATISCH:
        aufraeumen(_wie_viele_behalten())
    return ziel


def _wie_viele_behalten() -> int:
    """Die eingestellte Zahl - oder die Voreinstellung, wenn sie nicht zu haben ist.

    Beim allerersten Start gibt es die Einstellungstabelle noch nicht; die
    Sicherung darf daran nicht scheitern.
    """
    try:
        from ..db import SessionLocal
        from .settings_service import load_settings

        with SessionLocal() as db:
            return load_settings(db).backup_keep
    except Exception:  # noqa: BLE001 - lieber die Voreinstellung als gar nichts
        return AUTOMATISCH_BEHALTEN


def _zwischenspeicher_leeren(sicherung: Path) -> list[str]:
    """Die Zwischenspeicher aus der frischen Kopie werfen.

    Passiert auf der **Kopie**, nicht auf der laufenden Datenbank - die bleibt
    unberuehrt. Das anschliessende ``VACUUM`` gibt den Platz wirklich frei;
    ohne das bliebe die Datei so gross wie vorher.
    """
    geleert: list[str] = []
    verbindung = sqlite3.connect(sicherung)
    try:
        vorhanden = {
            zeile[0]
            for zeile in verbindung.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for tabelle in ZWISCHENSPEICHER:
            if tabelle in vorhanden:
                verbindung.execute(f"DELETE FROM [{tabelle}]")
                geleert.append(tabelle)
        verbindung.commit()
        verbindung.execute("VACUUM")
    finally:
        verbindung.close()
    return geleert


def liste() -> list[Eintrag]:
    """Alle vorhandenen Sicherungen, neueste zuerst."""
    ziel_ordner = ordner()
    if not ziel_ordner.is_dir():
        return []

    eintraege: list[Eintrag] = []
    for datei in ziel_ordner.glob("*.db"):
        brief = _steckbrief_lesen(datei)
        eintraege.append(
            Eintrag(
                name=datei.name,
                groesse=_groesse(datei),
                erstellt=brief.erstellt,
                art=brief.art,
                kommentar=brief.kommentar,
                version=brief.version,
            )
        )
    eintraege.sort(key=lambda e: e.erstellt, reverse=True)
    return eintraege


def _groesse(sicherung: Path) -> int:
    """Was eine Sicherung wirklich belegt - Datenbank **und** Beilagen.

    ⚠️ Seit Bilder und TRaSH-Stand je Sicherung mitkopiert werden, ist die
    ``.db`` allein nicht mehr die Antwort. Eine Spalte, die zu wenig anzeigt,
    ist genau dann falsch, wenn jemand hinsieht: weil der Platz knapp wird.
    """
    gesamt = sicherung.stat().st_size
    beilagen = _beilagen_pfad(sicherung)
    if beilagen.is_dir():
        gesamt += sum(p.stat().st_size for p in beilagen.rglob("*") if p.is_file())
    return gesamt


def _steckbrief_lesen(sicherung: Path) -> Steckbrief:
    """Den Steckbrief holen - oder aus dem Dateinamen zusammenreimen.

    ⚠️ Sicherungen aus Fassungen vor 0.22 haben keinen Steckbrief. Sie
    deswegen zu verschweigen waere falsch: Es sind genau die Staende, die
    jemand nach einem missglueckten Update sucht.
    """
    pfad = _steckbrief_pfad(sicherung)
    if pfad.exists():
        try:
            return Steckbrief.aus_json(pfad.read_text(encoding="utf-8"))
        except (ValueError, TypeError) as fehler:
            logger.warning("Backup manifest %s unreadable: %s", pfad.name, fehler)

    zeit = datetime.fromtimestamp(sicherung.stat().st_mtime, timezone.utc)
    alt = re.match(r"nexview-vor-(?P<version>[\d.]+)-", sicherung.name)
    return Steckbrief(
        version=alt.group("version") if alt else "",
        schema="",
        erstellt=zeit.isoformat(timespec="seconds"),
        art=AUTOMATISCH,
        kommentar="",
        enthaelt=["nexview.db"],
    )


def aufraeumen(behalten: int = AUTOMATISCH_BEHALTEN, ordner_: Path | None = None) -> int:
    """Nur die juengsten **automatischen** Staende behalten.

    ⚠️ Von Hand angelegte Sicherungen bleiben, egal wie viele es sind. Wer
    bewusst eine anlegt, bevor er etwas Riskantes tut, darf sie nicht dadurch
    verlieren, dass Nexview zwischendurch fuenfmal startet.
    """
    ziel_ordner = ordner_ or ordner()
    if not ziel_ordner.is_dir():
        return 0

    automatisch = [
        datei
        for datei in ziel_ordner.glob("*.db")
        if _steckbrief_lesen(datei).art == AUTOMATISCH
    ]
    automatisch.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    entfernt = 0
    for alt in automatisch[behalten:]:
        try:
            entfernen(alt)
            entfernt += 1
        except OSError as fehler:
            logger.warning("Could not delete old backup %s: %s", alt.name, fehler)
    return entfernt


def datei(name: str) -> Path:
    """Den Pfad zu einer Sicherung - **nur** innerhalb des Ordners.

    ⚠️ Der Name kommt aus einer Anfrage. Ohne diese Pruefung liesse sich mit
    ``../secret.key`` genau die Datei herunterladen, die das Archiv sonst
    verschluesselt mitnimmt.
    """
    ziel_ordner = ordner().resolve()
    kandidat = (ziel_ordner / name).resolve()
    if kandidat.parent != ziel_ordner or kandidat.suffix != ".db" or not kandidat.is_file():
        raise FileNotFoundError(name)
    return kandidat


def archiv(name: str, passwort: str) -> bytes:
    """Eine Sicherung als verschluesseltes ZIP.

    Darin: die Datenbank, der Schluessel, die Beilagen aus ``BEILAGEN`` und ein
    Steckbrief, der aufzaehlt, was wirklich mitgekommen ist.

    ⚠️ Ohne ``secret.key`` waere das Archiv unvollstaendig: Die Zugaenge zu
    Radarr, Sonarr, TMDB und dem Mailserver sind damit verschluesselt. Wer nur
    die Datenbank mitnimmt, steht beim Einspielen vor lauter Zugaengen, die sich
    nicht mehr lesen lassen.

    Und genau deswegen braucht es ein Passwort: Mit dem Schluessel darin gibt
    die Datei alles her.
    """
    if not passwort:
        raise ValueError("Ein Archiv ohne Passwort wird nicht gebaut.")

    quelle = datei(name)
    brief = _steckbrief_lesen(quelle)

    puffer = io.BytesIO()
    with pyzipper.AESZipFile(
        puffer, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zip_datei:
        zip_datei.setpassword(passwort.encode("utf-8"))
        zip_datei.write(quelle, "nexview.db")
        drin = ["nexview.db"]

        # ⚠️ **Profilbilder und TRaSH-Stand liegen als Dateien daneben, nicht
        # in der Datenbank.** Von den Bildern steht dort nur der Name; ohne sie
        # kommt eine Installation zurueck, in der jeder sein Bild verloren hat -
        # und niemand versteht, warum, weil "die Sicherung war doch
        # vollstaendig".
        for pfad, eintrag in _beilagen_zum_packen(quelle):
            zip_datei.write(pfad, eintrag)
            drin.append(eintrag)

        schluessel = get_settings().key_file
        if schluessel.exists():
            zip_datei.write(schluessel, "secret.key")
            drin.append("secret.key")
        else:
            zip_datei.writestr("SCHLUESSEL-FEHLT.txt", OHNE_SCHLUESSEL)
            drin.append("SCHLUESSEL-FEHLT.txt")

        # ⚠️ **Zuletzt, und mit der wirklichen Liste.** Hier stand ein fester
        # Wert, geschrieben bevor feststand, was tatsaechlich hineinwandert: Er
        # nannte ``secret.key`` auch dann, wenn stattdessen nur der Hinweis
        # darauf drinlag, und die Profilbilder nie. Gelesen wird das Feld
        # nirgends im Programm - getaeuscht hat es also genau den Leser, fuer
        # den ueberhaupt ein offenes ZIP gewaehlt wurde: den Menschen, der das
        # Archiv aufmacht, weil Nexview gerade nicht laeuft.
        #
        # Der Steckbrief selbst steht nicht in seiner eigenen Liste - das
        # beantwortet keine Frage.
        brief.enthaelt = drin
        zip_datei.writestr(STECKBRIEF, brief.als_json())

    return puffer.getvalue()


def _beilagen_zum_packen(sicherung: Path) -> list[tuple[Path, str]]:
    """Welche Dateien neben der Datenbank ins Archiv gehoeren - und woher.

    Normalerweise aus dem Beilagenordner der Sicherung, also im Zustand von
    damals. Fehlt er, stammt der Stand aus einer Fassung vor diesem Umbau;
    dann bleibt nur das heutige Datenverzeichnis. Das ist nicht derselbe
    Zeitpunkt - aber eine Installation, die ohne Bilder zurueckkommt, ist
    schlechter als eine mit den falschen.
    """
    ordner_ = _beilagen_pfad(sicherung)
    hat_eigene = ordner_.is_dir()
    if not hat_eigene:
        logger.info(
            "Backup %s has no file snapshot of its own - packing today's files instead",
            sicherung.name,
        )
    wurzel = ordner_ if hat_eigene else get_settings().data_dir

    gefunden: list[tuple[Path, str]] = []
    for name in BEILAGEN:
        unter = wurzel / name
        if not unter.is_dir():
            continue
        for datei_ in sorted(unter.iterdir()):
            if datei_.is_file():
                gefunden.append((datei_, f"{name}/{datei_.name}"))
    return gefunden


# ---------------------------------------------------------------------------
# Fuer das Wiederherstellen - schon hier, weil das Format es tragen muss
# ---------------------------------------------------------------------------


def _als_zahlen(version: str) -> tuple[int, ...]:
    return tuple(int(teil) for teil in re.findall(r"\d+", version)) or (0,)


def vertraeglich(brief: Steckbrief) -> tuple[bool, str]:
    """Darf diese Sicherung in die laufende Fassung eingespielt werden?

    ⚠️ **Die Wanderung in ``db.py`` kann nur vorwaerts.** Eine aeltere Sicherung
    zieht Nexview beim naechsten Start sauber hoch. Eine **neuere** darf niemals
    eingespielt werden: Die laufende Fassung kennt deren Tabellen und deren
    Bedeutung nicht, und was sie nicht kennt, liest sie falsch oder gar nicht.

    Der Rueckgabewert traegt die Begruendung mit, damit die Oberflaeche sagen
    kann, *warum* etwas abgelehnt wird - "nicht vertraeglich" allein hilft
    niemandem weiter.
    """
    if not brief.version:
        return False, "unknown_version"

    sicherung = _als_zahlen(brief.version)
    laufend = _als_zahlen(__version__)

    if sicherung > laufend:
        return False, "backup_newer"
    return True, "ok"


# ---------------------------------------------------------------------------
# Regelmaessig sichern
# ---------------------------------------------------------------------------

#: Wie viele Tage zwischen zwei Sicherungen liegen duerfen, je Einstellung.
TAKTE = {"daily": 1, "weekly": 7, "monthly": 30}

#: Wie oft nachgesehen wird, ob etwas ansteht. Eine Stunde reicht: Der
#: kuerzeste Takt ist ein Tag, und ein paar Stunden Versatz aendern nichts.
NACHSEHEN_SEKUNDEN = 3600


def faellig(takt: str, *, jetzt: datetime | None = None) -> bool:
    """Ist seit der letzten automatischen Sicherung genug Zeit vergangen?

    ⚠️ **Der Zeitpunkt kommt aus den Dateien, nicht aus einem gespeicherten
    Wert.** Ein Wert in der Datenbank waere nach einer Wiederherstellung falsch
    - er stammte dann aus dem Stand von damals. Die Dateien liegen daneben und
    sagen die Wahrheit.
    """
    if takt not in TAKTE:
        return False

    automatisch = [e for e in liste() if e.art == AUTOMATISCH]
    if not automatisch:
        return True

    jetzt = jetzt or datetime.now(timezone.utc)
    try:
        letzte = datetime.fromisoformat(automatisch[0].erstellt)
    except ValueError:
        return True
    if letzte.tzinfo is None:
        letzte = letzte.replace(tzinfo=timezone.utc)

    return (jetzt - letzte).total_seconds() >= TAKTE[takt] * 86400


def _takt() -> None:
    """Eine Runde des Sicherungstakts, gemacht fuer einen Arbeitsthread.

    Alles hier drin blockiert: das Lesen der Einstellungen, das Dateilisten
    in ``faellig`` und vor allem ``anlegen`` selbst (``VACUUM INTO`` plus
    Aufraeumen). ``run_forever`` schiebt deshalb die ganze Runde per
    ``asyncio.to_thread`` aus der Ereignisschleife, nicht nur das Anlegen.

    ``anlegen`` ist fuer den Thread belegt geeignet: es nutzt
    ``engine.connect()`` (Vorrat mit ``check_same_thread=False``), rohe
    sqlite3 Verbindungen nur auf der Kopie und je Aufruf eine eigene
    ``SessionLocal``. Der manuelle Endpunkt (``routers/sicherungen.py``) ist
    ein synchrones ``def`` und laeuft heute schon im FastAPI Threadpool,
    ``anlegen`` wird also laengst produktiv aus Arbeitsthreads gerufen.
    """
    from ..db import SessionLocal, platz_zurueckgeben
    from .settings_service import load_settings

    with SessionLocal() as db:
        takt = load_settings(db).backup_schedule
    if faellig(takt):
        logger.info("Scheduled backup due (%s)", takt)
        anlegen(art=AUTOMATISCH, kommentar="")
    # Der laufende Teil der Selbstpflege: gibt geloeschten Platz stueckweise
    # ans Dateisystem zurueck, 1000 Seiten sind rund 4 MB je Runde. Warum
    # gerade hier und warum das reicht, steht bei ``platz_zurueckgeben``.
    platz_zurueckgeben(1000)


async def run_forever(stop: "asyncio.Event") -> None:
    """Stuendlich nachsehen, ob eine Sicherung ansteht.

    Bewusst keine feste Uhrzeit: Ein Container, der nachts um drei aus ist,
    wuerde seinen Termin sonst still ueberspringen. Hier zaehlt der Abstand zur
    letzten Sicherung, nicht der Kalender - dann holt ein Neustart den Termin
    einfach nach.
    """
    while not stop.is_set():
        try:
            await asyncio.to_thread(_takt)
        except Exception:  # noqa: BLE001 - die Schleife darf nie sterben
            logger.exception("Scheduled backup failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=NACHSEHEN_SEKUNDEN)
        except TimeoutError:
            continue


# ---------------------------------------------------------------------------
# Wiederherstellen
# ---------------------------------------------------------------------------


def _hartnaeckig_loeschen(pfad: Path, versuche: int = 20) -> None:
    """Eine Datei loeschen, auch wenn sie gerade noch jemand offen hat.

    ⚠️ Nicht wegschauen, wenn es nicht klappt. Eine liegengebliebene
    ``-wal``-Datei der alten Datenbank wird in die neue eingespielt - das ist
    schlimmer als ein abgebrochener Vorgang.
    """
    for versuch in range(versuche):
        try:
            pfad.unlink(missing_ok=True)
            return
        except PermissionError:
            if versuch == versuche - 1:
                raise SicherungFehler(
                    "restore_files_busy",
                    f"{pfad.name} ist noch in Benutzung - bitte gleich noch einmal versuchen.",
                )
            time.sleep(0.25)


class SicherungFehler(Exception):
    """Etwas stimmt mit dem Archiv nicht. Traegt eine Kennung fuer die Oberflaeche."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


def _oeffnen(
    daten: bytes, passwort: str
) -> tuple[Steckbrief, bytes, str | None, dict[str, bytes]]:
    """Archiv aufmachen und den Inhalt herausholen - ohne irgendetwas zu ersetzen.

    Gibt Steckbrief, Datenbank, den Schluessel (falls enthalten) und die
    Beilagen zurueck - letztere unter ihrem Ordnernamen, also
    ``"avatars/bild.jpg"`` oder ``"trash/trash-radarr.json"``.
    """
    try:
        zip_datei = pyzipper.AESZipFile(io.BytesIO(daten))
    except Exception as fehler:  # noqa: BLE001
        raise SicherungFehler("restore_not_an_archive", "Das ist kein gueltiges Archiv.") from fehler

    zip_datei.setpassword(passwort.encode("utf-8"))
    try:
        namen = set(zip_datei.namelist())
        if STECKBRIEF not in namen or "nexview.db" not in namen:
            raise SicherungFehler(
                "restore_not_a_backup",
                "Das Archiv enthaelt keine Nexview-Sicherung.",
            )
        roh_brief = zip_datei.read(STECKBRIEF)
        roh_db = zip_datei.read("nexview.db")
        schluessel = zip_datei.read("secret.key").decode("utf-8").strip() if "secret.key" in namen else None
        beilagen = {
            # Ordnername aus unserer eigenen Liste, Dateiname nur als reiner
            # Name - nie der Pfad aus dem Archiv: Ein praepariertes ZIP
            # koennte sonst ``avatars/../../secret.key`` enthalten und beim
            # Auspacken irgendwo landen.
            f"{unter}/{Path(name).name}": zip_datei.read(name)
            for unter in BEILAGEN
            for name in namen
            if name.startswith(f"{unter}/") and not name.endswith("/")
        }
    except SicherungFehler:
        raise
    except Exception as fehler:  # noqa: BLE001 - falsches Passwort sieht hier gleich aus
        raise SicherungFehler("restore_wrong_password", "Das Passwort passt nicht.") from fehler
    finally:
        zip_datei.close()

    # ⚠️ Ein ZIP kann alles enthalten. Bevor irgendetwas ersetzt wird, muss
    # feststehen, dass das wirklich eine SQLite-Datei ist - sonst tauscht man
    # eine funktionierende Installation gegen eine Textdatei.
    if not roh_db.startswith(b"SQLite format 3\x00"):
        raise SicherungFehler("restore_not_a_database", "Die enthaltene Datei ist keine Datenbank.")

    try:
        brief = Steckbrief.aus_json(roh_brief)
    except (ValueError, TypeError) as fehler:
        raise SicherungFehler("restore_no_manifest", "Dem Archiv fehlt der Steckbrief.") from fehler

    return brief, roh_db, schluessel, beilagen


@dataclass(slots=True)
class Befund:
    """Was sich ueber ein Archiv sagen laesst - vor und nach dem Einspielen."""

    brief: Steckbrief
    einspielbar: bool
    grund: str
    #: Liegt im Archiv eine ``secret.key``?
    #:
    #: ⚠️ **Nicht dieselbe Frage wie die, ob die Zielinstallation einen
    #: Schluessel hat** - und die Verwechslung liess die Vorschau ausgerechnet
    #: im schlimmsten Fall beruhigend aussehen. Kommt das Archiv von einer
    #: Installation mit ``NEXVIEW_SECRET_KEY`` und hat das Ziel die Variable
    #: nicht, erzeugt Nexview beim Einspielen einen **neuen** Schluessel.
    #: Danach ist kein gespeicherter Zugang mehr lesbar - Radarr, Sonarr,
    #: TMDB, Mailserver, dazu jedes Webhook- und OIDC-Geheimnis -, und der
    #: Betreiber hat keinen Anlass, den Schluessel zu verdaechtigen.
    schluessel_im_archiv: bool


def pruefen(daten: bytes, passwort: str) -> Befund:
    """Nur nachsehen: Was ist das, und darf es eingespielt werden?

    ⚠️ **Getrennt vom Einspielen, und das ist der Punkt.** Wiederherstellen
    ersetzt alles. Wer den Knopf drueckt, soll vorher gesehen haben, *was* er
    einspielt - Datum, Fassung, Notiz -, statt es hinterher zu erfahren.
    """
    brief, _, schluessel, _ = _oeffnen(daten, passwort)
    ok, grund = vertraeglich(brief)
    return Befund(brief, ok, grund, schluessel is not None)


def wiederherstellen(daten: bytes, passwort: str) -> Befund:
    """Datenbank und Schluessel aus dem Archiv einspielen.

    ⚠️ **Vorher wird der jetzige Stand gesichert.** Auch wenn gerade nichts
    Wertvolles dasteht: Ist die eingespielte Sicherung selbst kaputt, ist das
    der einzige Weg zurueck. Sie kostet ein paar Sekunden und Megabyte.

    ⚠️ **Alle Anmeldungen enden.** Mit ``secret.key`` wechselt der Schluessel,
    mit dem die Sitzungs-Token unterschrieben sind - die alten passen danach
    nicht mehr. Das ist kein Nebeneffekt, sondern richtig so: Die Konten aus der
    Sicherung sind andere als die von eben.
    """
    brief, roh_db, schluessel, beilagen = _oeffnen(daten, passwort)

    ok, grund = vertraeglich(brief)
    if not ok:
        # ⚠️ **Jede Kennung steht ausgeschrieben direkt am ``raise``.**
        # Kuerzer waere eine Zuordnungstabelle oder ein ``"restore_" + grund``.
        # Beides versteckt die Kennung aber vor dem Test, der prueft, dass zu
        # jeder ein uebersetzter Text existiert - und der waere dann still
        # gruen, waehrend die Meldung in der englischen Oberflaeche deutsch
        # erscheint. Die drei Zeilen sind das wert.
        woher = f"Diese Sicherung stammt aus Fassung {brief.version or '?'}"
        if grund == "backup_newer":
            raise SicherungFehler(
                "restore_backup_newer",
                f"{woher} und ist damit neuer als {__version__}.",
            )
        if grund == "unknown_version":
            raise SicherungFehler(
                "restore_unknown_version",
                "Bei dieser Sicherung steht nicht dabei, aus welcher Fassung sie stammt.",
            )
        raise SicherungFehler("restore_incompatible", f"{woher} und passt nicht zu {__version__}.")

    from ..db import engine, init_db

    einstellungen = get_settings()
    ziel = einstellungen.db_path

    # ⚠️ **Wer den Betreiber-Haken traegt, wird jetzt gemerkt - vor dem Tausch.**
    #
    # Eine Sicherung kopiert die ganze Datenbank, der Haken reiste also mit. Ein
    # zweiter Administrator koennte damit eine Uebergabe rueckgaengig machen:
    # einen Stand von vor der Uebergabe einspielen, und der Haken saesse wieder
    # bei ihm. Genau der Angriff, gegen den der Haken steht, ginge dann ueber
    # die Sicherungsseite weiter.
    #
    # Der Haken ist deshalb der **eine** Wert, der die Zeitmaschine nicht
    # mitmacht. Das ist eine bewusste Ausnahme von "eine Sicherung stellt den
    # Stand von damals her", und sie ist in ``betreiber.nach_dem_einspielen``
    # ausgeschrieben.
    #
    # Der **Name**, nicht die Kennung: Die Kennungen der eingespielten Datenbank
    # sind andere. Vor der Einrichtung gibt es niemanden - dann gilt der Stand
    # aus der Sicherung, und das ist richtig.
    from .betreiber import nach_dem_einspielen, traeger as betreiber_traeger

    try:
        from ..db import SessionLocal

        with SessionLocal() as vorher:
            bisheriger_betreiber = getattr(betreiber_traeger(vorher), "username", None)
    except Exception as fehler:  # noqa: BLE001
        # Eine Datenbank, die sich nicht mehr lesen laesst, ist genau der Grund,
        # aus dem jemand einspielt. Der Vorgang darf daran nicht scheitern.
        logger.warning("Could not read the current owner before restoring: %s", fehler)
        bisheriger_betreiber = None

    # Rueckweg zuerst - solange die alte Datenbank noch steht.
    if ziel.exists():
        try:
            anlegen(art=AUTOMATISCH, kommentar="vor dem Wiederherstellen")
        except Exception as fehler:  # noqa: BLE001
            logger.warning("Could not back up before restore: %s", fehler)

    # Verbindungen schliessen, sonst haelt SQLite die Dateien fest.
    engine.dispose()

    # ⚠️ **Die Begleitdateien muessen mit weg** - und genau daran haengt es.
    # Bleibt ein ``-wal`` der alten Datenbank liegen, spielt SQLite dessen
    # Aenderungen in die **neue** ein; die stammen aber aus einer voellig
    # anderen Datenbank.
    #
    # ⚠️ Und es kann fehlschlagen: Haelt noch irgendetwas eine Verbindung
    # offen - eine Hintergrundschleife mitten in einer Abfrage, die Sitzung
    # der laufenden Anfrage -, verweigert Windows das Loeschen. Deshalb ein
    # paar Versuche mit kurzer Pause.
    #
    # Unter Linux waere das Ausbleiben dieses Fehlers **schlimmer**: Dort
    # laesst sich eine offene Datei loeschen, der Aufruf ginge still durch,
    # und ein Schreiber haenge danach an einer Datei, die es nicht mehr gibt.
    # Der Schaden fiele erst viel spaeter auf. Deshalb wird hier nicht
    # weggeschaut, sondern abgebrochen.
    for anhang in ("-wal", "-shm"):
        _hartnaeckig_loeschen(ziel.with_name(ziel.name + anhang))

    ziel.write_bytes(roh_db)

    if schluessel:
        if einstellungen.secret_key:
            # Die Umgebungsvariable gewinnt immer - eine Datei daneben aendert
            # daran nichts. Wer das nicht weiss, sucht spaeter lange.
            logger.warning(
                "Restored backup contains a secret.key, but NEXVIEW_SECRET_KEY is set - "
                "the variable wins. Stored service credentials will stay unreadable "
                "unless the variable holds the same value."
            )
        else:
            einstellungen.key_file.write_text(schluessel, encoding="utf-8")

    for schluessel_name, inhalt in beilagen.items():
        # ``unter`` stammt aus BEILAGEN, nicht aus dem Archiv - der Name kann
        # also nicht aus dem Datenverzeichnis herausfuehren.
        unter, _, dateiname = schluessel_name.partition("/")
        ziel_ordner = einstellungen.data_dir / unter
        ziel_ordner.mkdir(parents=True, exist_ok=True)
        (ziel_ordner / dateiname).write_bytes(inhalt)
    if beilagen:
        logger.info("Restored %d accompanying file(s)", len(beilagen))

    # ⚠️ **Der TRaSH-Stand wird gemerkt.** Ohne das Leeren arbeitet der
    # laufende Prozess bis zum Neustart mit dem Abzug von vor dem Einspielen
    # weiter - und misst die gerade eingespielten Qualitaetsprofile gegen einen
    # Stand, den sie nie gesehen haben.
    from .trash import schnappschuss

    schnappschuss.cache_clear()

    # ⚠️ **Und der abgeleitete Verschluesselungsschluessel genauso.** Ein paar
    # Zeilen weiter oben kann gerade ein fremdes ``secret.key`` geschrieben
    # worden sein. ``crypto`` merkt sich den daraus abgeleiteten Fernet einmal
    # je Prozesslauf; ohne dieses Vergessen liefe der Dienst bis zum Neustart
    # mit dem Schluessel von **vor** dem Einspielen weiter und hielte jedes
    # eingespielte Geheimnis fuer unlesbar.
    from ..crypto import fernet_vergessen

    fernet_vergessen()

    # Aeltere Sicherung? Dann fehlen ihr Spalten und Tabellen - die ergaenzt
    # der gewoehnliche Startweg.
    init_db()

    # ⚠️ **Nach ``init_db``, nicht davor.** Der Startweg traegt die Spalte
    # nach und vergibt den Haken notfalls neu (aelteste Sicherung, in der es
    # ihn noch gar nicht gab). Erst danach steht fest, worauf hier gesetzt
    # werden kann.
    from ..db import SessionLocal as _Sitzung

    with _Sitzung() as nachher:
        nach_dem_einspielen(nachher, bisheriger_betreiber)

    _alle_abmelden()

    logger.info("Backup restored: version %s from %s", brief.version, brief.erstellt)
    return Befund(brief, True, "ok", schluessel is not None)


def _alle_abmelden() -> None:
    """Nach dem Einspielen ist niemand mehr angemeldet.

    ⚠️ **Das passiert nicht von selbst, wie ich zuerst geglaubt habe.** Ich
    hatte angenommen, mit ``secret.key`` wechsele der Schluessel und alle
    Sitzungs-Token wuerden ungueltig. Das gilt aber nur, wenn der Schluessel
    sich **aendert** - wer eine Sicherung derselben Installation einspielt, hat
    hinterher denselben. Dann bleibt jede Anmeldung von vorher gueltig, und die
    Leute schauen auf einen Datenstand von vorgestern, ohne es zu merken.

    Deshalb wird die Grenze hier ausdruecklich gesetzt.
    """
    from ..db import SessionLocal
    from ..models import User, utcnow
    from sqlalchemy import update

    try:
        with SessionLocal() as db:
            db.execute(update(User).values(sessions_valid_from=utcnow()))
            db.commit()
    except Exception:  # noqa: BLE001 - die Wiederherstellung selbst ist durch
        logger.exception("Could not end existing sessions after restore")
