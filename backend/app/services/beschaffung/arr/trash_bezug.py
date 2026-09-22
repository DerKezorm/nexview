"""Neue TRaSH-Staende holen - auf Klick, und gelegentlich nachsehen, ob es welche gibt.

⚠️ **Warum ueberhaupt geholt wird.** Nexview liefert einen Schnappschuss mit,
sonst braeuchte die erste Einrichtung Internet. Der veraltet aber, und die
Guides aendern sich laufend - Punktwerte, neue Release-Gruppen, entschaerfte
Fehlgriffe. Ohne einen Weg, das nachzuziehen, haengt jeder an dem Stand, der
zufaellig in seiner Nexview-Fassung lag.

⚠️ **Nexview spricht dafuer mit github.com.** Das ist die einzige Stelle, an der
das passiert, und sie laeuft nur auf Klick oder beim taeglichen Nachsehen. Wer
das nicht will, ruehrt den Knopf nicht an - der mitgelieferte Stand bleibt
gueltig.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import tarfile
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from ....config import get_settings
from . import trash

logger = logging.getLogger("nexview.qualitaet")

#: Woher. Die Guides stehen unter MIT-Lizenz.
REPO = "TRaSH-Guides/Guides"
ZWEIG = "master"
#: Welcher Pfad im Repo uns angeht - alles andere (Bilder, Text) bleibt liegen.
PFAD = "docs/json"

#: Eine einzige API-Anfrage sagt, wann sich dieser Pfad zuletzt bewegt hat.
COMMITS = f"https://api.github.com/repos/{REPO}/commits"
#: Das Paket. 25 MB fuer 3 MB Nutzlast - aber eine Anfrage statt 630.
#: Die Alternative waere, jede Datei einzeln zu holen; das kostet bei GitHub
#: ohne Anmeldung schon nach 60 Anfragen die Sperre.
PAKET = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/{ZWEIG}"

#: Wie oft nachgesehen wird, ob es Neues gibt.
NACHSEHEN_SEKUNDEN = 24 * 60 * 60
#: Das Herunterladen darf dauern, das Nachsehen nicht.
FRIST_PRUEFEN = httpx.Timeout(15.0, connect=8.0)
FRIST_HOLEN = httpx.Timeout(180.0, connect=10.0)
#: Bremse gegen ein Paket, das unerwartet riesig ist.
MAX_BYTES = 120 * 1024 * 1024


class BezugFehler(Exception):
    """Der neue Stand liess sich nicht holen oder nicht uebernehmen."""

    def __init__(self, code: str, meldung: str) -> None:
        super().__init__(meldung)
        self.code = code
        self.meldung = meldung


@dataclass(frozen=True)
class Herkunft:
    """Welcher Stand der Guides gerade gilt."""

    commit: str = ""
    commit_datum: str = ""
    geholt_am: str = ""
    #: True, solange nur der mitgelieferte Stand da ist.
    mitgeliefert: bool = True


def ordner() -> Path:
    """Wohin ein geholter Stand kommt.

    ⚠️ In das **Datenverzeichnis**, nicht neben das Programm: Nur das ueberlebt
    einen neu gebauten Container. Ein Stand im Programmordner waere nach jedem
    Update wieder weg, und niemand wuesste, warum.
    """
    return get_settings().data_dir / "trash"


def _herkunft_datei() -> Path:
    return ordner() / "herkunft.json"


def _regeln_datei(dienst: str) -> Path:
    return ordner() / f"regeln-{dienst}.json"


@lru_cache(maxsize=4)
def _mitgelieferte_regeln(dienst: str) -> dict[str, str]:
    """Die Regeln des Stands, der mit dieser Nexview-Fassung kam. Er aendert sich nie."""
    datei = trash.DATEN / f"trash-{dienst}.json"
    if not datei.is_file():
        return {}
    return trash.regeln_je_muster(json.loads(datei.read_text(encoding="utf-8")))


def _gemerkte_regeln(dienst: str) -> dict[str, list[str]]:
    datei = _regeln_datei(dienst)
    if not datei.is_file():
        return {}
    try:
        roh = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Known TRaSH rules for %s are unreadable, starting over", dienst)
        return {}
    if not isinstance(roh, dict):
        return {}
    return {str(k): [str(a) for a in v] for k, v in roh.items() if isinstance(v, list)}


def bekannte_regeln(dienst: str) -> dict[str, set[str]]:
    """Welche Regeln jedes Muster in einem TRaSH-Stand hatte, den Nexview kennt.

    ⚠️ **Die Grundlage fuer "unveraendert von TRaSH".** Muster gelten in Radarr
    fuer alle Profile einer Instanz, und manche hat jemand mit Absicht anders
    gebaut. Nexview zieht deshalb nur Regeln nach, die genau einem bekannten
    Stand entsprechen: dem mitgelieferten, dem geltenden und jedem, der einmal
    geholt wurde. Alles andere hat jemand geaendert, und das bleibt.
    """
    bekannt: dict[str, set[str]] = {}
    for quelle in (
        _mitgelieferte_regeln(dienst),
        trash.regeln_je_muster(trash.schnappschuss(dienst)),
    ):
        for name, abdruck in quelle.items():
            bekannt.setdefault(name, set()).add(abdruck)
    for name, abdruecke in _gemerkte_regeln(dienst).items():
        bekannt.setdefault(name, set()).update(abdruecke)
    return bekannt


def herkunft() -> Herkunft:
    """Welcher Stand gerade benutzt wird."""
    datei = _herkunft_datei()
    if not datei.is_file():
        # Der mitgelieferte Stand traegt seine Herkunft selbst.
        daten = trash.schnappschuss("radarr")
        return Herkunft(
            commit=str(daten.get("commit") or ""),
            commit_datum=str(daten.get("commit_datum") or daten.get("stand") or ""),
            mitgeliefert=True,
        )
    roh = json.loads(datei.read_text(encoding="utf-8"))
    return Herkunft(
        commit=str(roh.get("commit") or ""),
        commit_datum=str(roh.get("commit_datum") or ""),
        geholt_am=str(roh.get("geholt_am") or ""),
        mitgeliefert=False,
    )


async def neuester_commit() -> tuple[str, str]:
    """Wann sich ``docs/json`` zuletzt bewegt hat - eine Anfrage.

    Nicht der Zweig insgesamt: Am Repo wird staendig etwas geaendert, meist an
    Bildern und Text. Uns geht nur der Ordner an, aus dem wir lesen.
    """
    async with httpx.AsyncClient(timeout=FRIST_PRUEFEN) as client:
        antwort = await client.get(
            COMMITS,
            params={"path": PFAD, "per_page": 1, "sha": ZWEIG},
            headers={"Accept": "application/vnd.github+json"},
        )
    if antwort.status_code == 403:
        raise BezugFehler(code="trash_rate_limit", meldung="GitHub bremst gerade weitere Anfragen.")
    if antwort.status_code != 200:
        raise BezugFehler(code="trash_unreachable", meldung="GitHub antwortet nicht wie erwartet.")
    eintraege = antwort.json()
    if not isinstance(eintraege, list) or not eintraege:
        raise BezugFehler(code="trash_unreachable", meldung="GitHub liefert keinen Stand.")
    eintrag = eintraege[0]
    return str(eintrag["sha"]), str(eintrag["commit"]["committer"]["date"])


async def gibt_es_neues() -> tuple[bool, str, str]:
    """Ist der Stand bei GitHub ein anderer als der benutzte?"""
    sha, datum = await neuester_commit()
    return sha != herkunft().commit, sha, datum


def _aus_paket(rohdaten: bytes) -> dict[str, dict[str, Any]]:
    """Aus dem Paket die beiden Schnappschuesse bauen.

    Gelesen wird nur ``docs/json/{radarr,sonarr}``; alles andere im Paket wird
    uebersprungen, ohne es je auszupacken.
    """
    gesammelt: dict[str, dict[str, dict]] = {
        dienst: {
            "cf": {},
            "quality-profiles": {},
            "cf-groups": {},
            "quality-size": {},
            "naming": {},
        }
        for dienst in ("radarr", "sonarr")
    }
    with tarfile.open(fileobj=io.BytesIO(rohdaten), mode="r:gz") as paket:
        for mitglied in paket:
            if not mitglied.isfile() or not mitglied.name.endswith(".json"):
                continue
            teile = mitglied.name.split("/")
            # <wurzel>/docs/json/<dienst>/<bereich>/<datei>.json
            if len(teile) < 6 or teile[1:3] != ["docs", "json"]:
                continue
            dienst, bereich, datei = teile[3], teile[4], teile[5]
            if dienst not in gesammelt or bereich not in gesammelt[dienst]:
                continue
            inhalt = paket.extractfile(mitglied)
            if inhalt is None:
                continue
            gesammelt[dienst][bereich][datei[:-5]] = json.loads(
                inhalt.read().decode("utf-8")
            )

    schnappschuesse: dict[str, dict[str, Any]] = {}
    for dienst, bereiche in gesammelt.items():
        formate = bereiche["cf"]
        if not formate or not bereiche["quality-profiles"]:
            raise BezugFehler(
                code="trash_incomplete",
                meldung="Das geholte Paket enthaelt nicht die erwarteten Daten.",
            )
        schnappschuesse[dienst] = {
            "quelle": f"https://github.com/{REPO}",
            "lizenz": "MIT",
            "formate": {f["trash_id"]: f for f in formate.values()},
            "formate_nach_datei": {n: f["trash_id"] for n, f in formate.items()},
            "profile": bereiche["quality-profiles"],
            "gruppen": bereiche["cf-groups"],
            "groessen": bereiche["quality-size"],
            "namen": bereiche["naming"],
        }
    return schnappschuesse


async def _paket_holen() -> bytes:
    async with (
        httpx.AsyncClient(timeout=FRIST_HOLEN, follow_redirects=True) as client,
        client.stream("GET", PAKET) as antwort,
    ):
            if antwort.status_code != 200:
                raise BezugFehler(code="trash_unreachable", meldung="Das Paket liess sich nicht laden.")
            teile: list[bytes] = []
            groesse = 0
            async for stueck in antwort.aiter_bytes():
                groesse += len(stueck)
                if groesse > MAX_BYTES:
                    raise BezugFehler(
                        code="trash_too_large",
                        meldung="Das Paket ist unerwartet gross.",
                    )
                teile.append(stueck)
    return b"".join(teile)


async def holen_und_pruefen(rezepte: list[tuple[str, dict]]) -> Herkunft:
    """Den neuen Stand holen, gegen die eigenen Profile pruefen, dann uebernehmen.

    ⚠️ **Erst pruefen, dann uebernehmen.** Ein Stand, aus dem sich ein
    vorhandenes Profil nicht mehr bauen laesst - weil ein Erkennungsmuster
    verschwunden ist -, wird abgelehnt. Sonst faellt der Schaden erst auf,
    wenn jemand verteilen will, und der alte Stand ist dann schon weg.

    ``rezepte`` sind die abgelegten Profile als ``(dienst, rezept)``.
    """
    sha, datum = await neuester_commit()
    rohdaten = await _paket_holen()
    neu = _aus_paket(rohdaten)

    # ⚠️ Die Herkunft **vor** der Pruefung eintragen, nicht erst beim
    # Speichern: Der Bauplan traegt den Stand mit, und ohne ihn scheitert
    # schon die Pruefung - an einem Feld, das wir selbst vergessen haben.
    for daten in neu.values():
        daten["stand"] = datum[:10]
        daten["commit"] = sha
        daten["commit_datum"] = datum

    # Zum Pruefen zaehlt nur, ob die Bausteine noch da sind - welche Nummer
    # eine Sprache auf einer Instanz traegt, spielt dabei keine Rolle. Trotzdem
    # **alle** Sprachen mitgeben: Sonst faellt eine Pflichtsprache aus dem
    # Bauplan, und die Pruefung liefe an genau dem Teil vorbei, den sie pruefen
    # soll.
    alle_sprachen = {code: nummer for nummer, code in enumerate(trash.SPRACHNAMEN, 1)}
    for dienst, rezept in rezepte:
        daten = neu.get(dienst)
        if daten is None:
            raise BezugFehler(code="trash_incomplete", meldung="Das Paket kennt diesen Dienst nicht.")
        try:
            trash.bauplan_aus(rezept, dienst, daten, alle_sprachen)
        except trash.TrashFehler as fehler:
            logger.warning("New TRaSH state rejected: %s", fehler)
            raise BezugFehler(
                code="trash_breaks_profiles",
                meldung=(
                    "Mit dem neuen Stand liesse sich ein vorhandenes Profil "
                    "nicht mehr bauen."
                ),
            ) from fehler

    # ⚠️ **Erst alles danebenschreiben, dann umbenennen.** Bricht es beim
    # zweiten Dienst ab - volle Platte etwa -, laege sonst ein neuer Stand
    # neben einem alten, und die Herkunft nennte weiter den alten. Umbenennen
    # ist der letzte Schritt und geht schnell genug, dass dazwischen nichts
    # passiert.
    ziel = ordner()
    ziel.mkdir(parents=True, exist_ok=True)
    vorlaeufig: list[tuple[Path, Path]] = []
    for dienst, daten in neu.items():
        neben = ziel / f"trash-{dienst}.json.neu"
        neben.write_text(
            json.dumps(daten, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        vorlaeufig.append((neben, ziel / f"trash-{dienst}.json"))

        # ⚠️ **Die Regeln des alten und des neuen Stands merken.** Muster, die
        # Nexview aus einem Stand geschrieben hat, erkennt es nur daran wieder;
        # ist der Stand ersetzt, waeren sie sonst ab jetzt "fremd" und wuerden
        # nie mehr nachgezogen.
        gemerkt = _gemerkte_regeln(dienst)
        try:
            bisher = trash.regeln_je_muster(trash.schnappschuss(dienst))
        except trash.TrashFehler:
            bisher = {}
        for quelle in (bisher, trash.regeln_je_muster(daten)):
            for name, abdruck in quelle.items():
                if abdruck not in gemerkt.setdefault(name, []):
                    gemerkt[name].append(abdruck)
        neben_regeln = ziel / f"regeln-{dienst}.json.neu"
        neben_regeln.write_text(
            json.dumps(gemerkt, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        vorlaeufig.append((neben_regeln, _regeln_datei(dienst)))
    for neben, endgueltig in vorlaeufig:
        neben.replace(endgueltig)
    geholt = datetime.now().astimezone().isoformat(timespec="seconds")
    _herkunft_datei().write_text(
        json.dumps({"commit": sha, "commit_datum": datum, "geholt_am": geholt}),
        encoding="utf-8",
    )
    trash.schnappschuss.cache_clear()
    # ⚠️ **Damit ist auch das Nachsehen erledigt.** Ohne diese Zeile hiess es nach
    # dem Holen weiter "ein neuerer Stand ist verfuegbar", ueber dem gerade
    # geholten, bis das Nachsehen am naechsten Tag wieder lief (13.09.2026).
    _neues.update({"bekannt": True, "vorhanden": False, "datum": datum})
    logger.info("TRaSH state updated to %s (%s)", sha[:12], datum)
    return Herkunft(commit=sha, commit_datum=datum, geholt_am=geholt, mitgeliefert=False)


#: Was das taegliche Nachsehen zuletzt ergeben hat - nur zum Anzeigen.
_neues: dict[str, Any] = {"bekannt": False, "vorhanden": False, "datum": ""}


def neues_bekannt() -> dict[str, Any]:
    return dict(_neues)


async def run_forever(stop: asyncio.Event) -> None:
    """Taeglich nachsehen, ob es einen neueren Stand gibt.

    Nur nachsehen - geholt wird nie von selbst. Ein Stand, der sich ungefragt
    aendert, wuerde Profile still verschieben; die Entscheidung bleibt beim
    Betreiber. Eine Anfrage am Tag, das reicht auch ohne Anmeldung bei GitHub.
    """
    while not stop.is_set():
        try:
            vorhanden, _sha, datum = await gibt_es_neues()
            _neues.update({"bekannt": True, "vorhanden": vorhanden, "datum": datum})
            if vorhanden:
                logger.info("A newer TRaSH state is available (%s)", datum)
        except BezugFehler as fehler:
            # Kein Grund zur Aufregung: Ohne Internet laeuft alles weiter, der
            # mitgelieferte Stand bleibt gueltig.
            logger.debug("Checking for a newer TRaSH state failed: %s", fehler.meldung)
        except Exception:  # noqa: BLE001 - die Schleife darf nie sterben
            logger.exception("Checking for a newer TRaSH state failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=NACHSEHEN_SEKUNDEN)
        except TimeoutError:
            continue
