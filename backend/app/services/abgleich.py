"""Wo die Quellen auseinanderlaufen - Radarr/Sonarr, Medienserver, Nexview.

Jede Installation fuehrt dieselben Titel an mehreren Stellen: Radarr und
Sonarr verwalten sie, der Medienserver spielt sie ab, Nexview rechnet sie
jemandem an. Keine der drei Seiten merkt von allein, wenn sie etwas anderes
behauptet als die anderen - und genau dort entstehen die Fehler, die niemand
sucht, weil nirgends etwas rot ist.

**Was hier verglichen wird:**

* Ein Titel liegt in Radarr als Datei, der Medienserver kennt ihn nicht. Meist
  hat er nicht eingelesen oder die Pfadzuordnung stimmt nicht - und fuer jeden
  Benutzer sieht es aus, als gaebe es ihn nicht.
* Der Medienserver kennt einen Titel ueberhaupt nicht (keine TMDB-, keine
  TVDB-Nummer). Fuer Nexview ist er damit unsichtbar; er wird ein zweites Mal
  bestellt.
* Derselbe Titel liegt zweimal in einer Bibliothek.
* Zwei Quellen behaupten verschiedene Erscheinungsjahre. Das ist entweder eine
  falsche Zuordnung - oder bloss Festival- gegen Kinostart.
* Mehrere Medienserver nebeneinander kennen unterschiedliche Bestaende.

⚠️ **Eine Pruefung, die nicht zutrifft, schweigt.** Ohne Medienserver
verschwindet der ganze Bereich; der Anbieter-Vergleich erscheint nur bei mehr
als einem verbundenen Server. Sonst waere es eine Funktion fuer einen einzigen
Pruefstand - und die meisten Haeuser haben genau einen Server oder gar keinen.

⚠️ **Keine festen Namen.** Nicht "Plex", nicht "/data/Movies", nicht "Radarr":
Benennung und Aufbau sind von Installation zu Installation verschieden. Was
hier steht, kommt aus ``settings.arr_instanzen()`` und
``MediaServerLibraryItem.provider``.

⚠️ **Gemessen wird stuendlich, gelesen bei jedem Aufruf.** Der Vergleich laeuft
ueber tausende Zeilen und braucht die Bibliothek aus dem Netz; ein Klick darf
so etwas nicht ausloesen. Das Ergebnis liegt als eine Zeile in ``settings``.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MediaServerLibraryItem, MediaType, Setting
from . import library
from .arr import ArrError
from .settings_service import AppSettings

logger = logging.getLogger("nexview.abgleich")

#: Wo das Ergebnis liegt. Eine Zeile in ``settings`` statt einer eigenen
#: Tabelle: Es ist genau ein Stand fuer die ganze Installation, und er wird
#: vollstaendig ersetzt statt fortgeschrieben.
SCHLUESSEL = "abgleich_stand"

#: So viele Beispiele werden je Befund mitgefuehrt. Mehr liest niemand, und
#: die Zeile in der Datenbank soll nicht ins Unermessliche wachsen.
BEISPIELE = 5

#: Ein Jahr Abweichung ist normal - Festivalstart und Kinostart fallen oft in
#: verschiedene Jahre. Dieselbe Grenze wie in ``mediaserver_library``.
JAHR_TOLERANZ = 1


@dataclass
class Stand:
    """Das Ergebnis eines Abgleichs."""

    #: In Radarr/Sonarr liegt eine Datei, kein Medienserver kennt den Titel.
    arr_ohne_server: int = 0
    #: Der Medienserver kennt ihn, Radarr/Sonarr nicht. **Kennzahl, kein
    #: Befund** - auf einer gewachsenen Anlage sind das dauerhaft viele, und
    #: ein Daueralarm entwertet die Befunde daneben.
    server_ohne_arr: int = 0
    #: Titel, denen der Medienserver keine einzige Kennung zuordnen konnte.
    nicht_erkannt: int = 0
    #: Dieselbe Nummer mehrfach in derselben Bibliothek. **Kennzahl, kein
    #: Befund** - an einer echten Anlage nachgemessen (30.08.2026): Von 28
    #: Treffern waren fast alle rechtmaessig. Plex fuehrt HD und 4K desselben
    #: Films als **einen** Eintrag, Jellyfin als **zwei**; dazu kommen
    #: Kinofassung und Extended Cut. Wer eine getrennte 4K-Bibliothek
    #: betreibt, bekaeme also dutzendfach falschen Alarm - und wuerde die
    #: Liste danach ueberblaettern. Die Zahl steht auf der Analyse-Seite, wo
    #: ein Mensch sie beurteilen kann.
    doppelt: int = 0
    #: Anbieter, die sich ueber ein Erscheinungsjahr uneinig sind.
    jahr_widerspruch: int = 0
    #: Wie viele Titel jeder Anbieter kennt - fuer "die Server sind uneinig".
    je_anbieter: dict[str, int] = field(default_factory=dict)
    #: Wie viele Titel mindestens ein Anbieter kennt, aber nicht alle.
    anbieter_luecke: int = 0
    #: Beispieltitel je Befund, damit die Zahl greifbar wird.
    beispiele: dict[str, list[str]] = field(default_factory=dict)
    #: Konnte ueberhaupt verglichen werden? Ohne Medienserver: nein.
    moeglich: bool = False


def lesen(db: Session) -> Stand:
    """Der zuletzt gemessene Stand. Nie gemessen heisst: nichts zu melden."""
    zeile = db.get(Setting, SCHLUESSEL)
    if zeile is None or not zeile.value:
        return Stand()
    try:
        daten = json.loads(zeile.value)
    except (TypeError, ValueError):
        return Stand()
    if not isinstance(daten, dict):
        return Stand()
    # Nachsichtig lesen: Ein Feld, das eine aeltere Fassung noch nicht kannte,
    # darf den ganzen Stand nicht wertlos machen.
    erlaubt = {f for f in Stand.__dataclass_fields__}
    return Stand(**{k: v for k, v in daten.items() if k in erlaubt})


def _schreiben(db: Session, stand: Stand) -> None:
    zeile = db.get(Setting, SCHLUESSEL)
    if zeile is None:
        zeile = Setting(key=SCHLUESSEL, value="")
        db.add(zeile)
    zeile.value = json.dumps(asdict(stand), ensure_ascii=False)
    db.commit()


def _medienserver_bestand(db: Session) -> dict[str, list[MediaServerLibraryItem]]:
    """Alle Bibliothekszeilen, nach Anbieter sortiert."""
    nach_anbieter: dict[str, list[MediaServerLibraryItem]] = defaultdict(list)
    for zeile in db.scalars(select(MediaServerLibraryItem)):
        nach_anbieter[zeile.provider].append(zeile)
    return nach_anbieter


async def _arr_bestand(settings: AppSettings) -> tuple[set[int], set[int], dict]:
    """Welche Nummern Radarr und Sonarr fuehren - und mit welchem Titel.

    Nur Eintraege **mit Datei**: Ein ueberwachter Titel ohne Datei fehlt im
    Medienserver voellig zu Recht, das waere kein Widerspruch, sondern der
    Normalfall.
    """
    filme: set[int] = set()
    serien: set[int] = set()
    titel: dict[tuple[str, int], str] = {}

    for stufe in ("standard", "uhd"):
        if settings.arr_configured("movie", stufe):
            try:
                for tmdb, eintrag in (
                    await library.movie_library(settings, stufe)
                ).items():
                    if eintrag.has_file:
                        filme.add(tmdb)
                        titel.setdefault(("movie", tmdb), eintrag.title or "")
            except ArrError:
                logger.warning("Movie library unavailable for tier %s", stufe)
        if settings.arr_configured("tv", stufe):
            try:
                nach_tvdb, _ = await library.series_library(settings, stufe)
                for tvdb, eintrag in nach_tvdb.items():
                    if eintrag.has_file:
                        serien.add(tvdb)
                        titel.setdefault(("tv", tvdb), getattr(eintrag, "title", ""))
            except ArrError:
                logger.warning("Series library unavailable for tier %s", stufe)

    return filme, serien, titel


def _doppelte(zeilen: list[MediaServerLibraryItem]) -> tuple[int, list[str]]:
    """Dieselbe Nummer mehrfach in **einer** Bibliothek.

    ⚠️ Innerhalb eines Anbieters, nicht ueber alle. Wer drei Medienserver auf
    dieselbe Mediathek zeigen laesst, hat jeden Titel dreimal in der Tabelle -
    das ist der Normalfall und kein Fund.
    """
    gesehen: dict[tuple[str, int], list[str]] = defaultdict(list)
    for zeile in zeilen:
        if zeile.tmdb_id is None:
            continue
        gesehen[(zeile.media_type.value, zeile.tmdb_id)].append(zeile.title or "")
    treffer = [titel for titel in gesehen.values() if len(titel) > 1]
    return len(treffer), [t[0] for t in treffer[:BEISPIELE] if t[0]]


def _jahre_uneinig(
    nach_anbieter: dict[str, list[MediaServerLibraryItem]],
) -> tuple[int, list[str]]:
    """Zwei Anbieter, dieselbe Nummer, verschiedene Jahre.

    ⚠️ **Das ist ein Verdacht, keine Feststellung.** Entweder hat einer den
    falschen Titel erwischt - oder die beiden benutzen verschiedene
    Erscheinungsdaten (Festival gegen Kinostart). Der Befundtext muss beides
    offenlassen; wer hier "falsch zugeordnet" behauptet, schickt jemanden auf
    die Suche nach einem Fehler, den es vielleicht nicht gibt.
    """
    if len(nach_anbieter) < 2:
        return 0, []

    jahre: dict[tuple[str, int], dict[str, tuple[int, str]]] = defaultdict(dict)
    for anbieter, zeilen in nach_anbieter.items():
        for zeile in zeilen:
            if zeile.tmdb_id is None or zeile.year is None:
                continue
            jahre[(zeile.media_type.value, zeile.tmdb_id)][anbieter] = (
                zeile.year,
                zeile.title or "",
            )

    treffer: list[str] = []
    for angaben in jahre.values():
        if len(angaben) < 2:
            continue
        werte = [j for j, _ in angaben.values()]
        if max(werte) - min(werte) > JAHR_TOLERANZ:
            titel = next((t for _, t in angaben.values() if t), "")
            treffer.append(f"{titel} ({min(werte)} / {max(werte)})")
    return len(treffer), treffer[:BEISPIELE]


async def messen(db: Session, settings: AppSettings) -> Stand:
    """Einen vollstaendigen Abgleich rechnen und ablegen."""
    nach_anbieter = _medienserver_bestand(db)
    if not nach_anbieter:
        # Kein Medienserver, nichts zu vergleichen. Ein leerer Stand ist die
        # ehrliche Antwort - nicht "alles in Ordnung".
        stand = Stand(moeglich=False)
        _schreiben(db, stand)
        return stand

    filme, serien, arr_titel = await _arr_bestand(settings)

    # Was mindestens ein Anbieter kennt.
    server_filme: set[int] = set()
    server_serien: set[int] = set()
    nicht_erkannt = 0
    doppelt = 0
    doppel_beispiele: list[str] = []
    je_anbieter: dict[str, int] = {}
    bekannt_je_anbieter: dict[str, set[tuple[str, int]]] = {}

    for anbieter, zeilen in nach_anbieter.items():
        je_anbieter[anbieter] = len(zeilen)
        eigene: set[tuple[str, int]] = set()
        for zeile in zeilen:
            if zeile.tmdb_id is None and zeile.tvdb_id is None:
                nicht_erkannt += 1
                continue
            if zeile.tmdb_id is not None:
                eigene.add((zeile.media_type.value, zeile.tmdb_id))
                if zeile.media_type == MediaType.movie:
                    server_filme.add(zeile.tmdb_id)
            if zeile.tvdb_id is not None and zeile.media_type == MediaType.tv:
                server_serien.add(zeile.tvdb_id)
        bekannt_je_anbieter[anbieter] = eigene
        anzahl, beispiele = _doppelte(zeilen)
        doppelt += anzahl
        doppel_beispiele.extend(beispiele)

    fehlende_filme = filme - server_filme
    fehlende_serien = serien - server_serien
    beispiele_arr = [
        arr_titel.get(("movie", t), "") for t in list(fehlende_filme)[:BEISPIELE]
    ] + [arr_titel.get(("tv", t), "") for t in list(fehlende_serien)[:BEISPIELE]]

    # Wieviel der Medienserver kennt, das Radarr/Sonarr nicht fuehrt.
    ohne_arr = len(server_filme - filme) + len(server_serien - serien)

    # Uneinige Anbieter: was einer kennt und ein anderer nicht.
    luecke = 0
    if len(bekannt_je_anbieter) > 1:
        alle = set().union(*bekannt_je_anbieter.values())
        gemeinsam = set.intersection(*bekannt_je_anbieter.values())
        luecke = len(alle - gemeinsam)

    jahr_anzahl, jahr_beispiele = _jahre_uneinig(nach_anbieter)

    stand = Stand(
        arr_ohne_server=len(fehlende_filme) + len(fehlende_serien),
        server_ohne_arr=ohne_arr,
        nicht_erkannt=nicht_erkannt,
        doppelt=doppelt,
        jahr_widerspruch=jahr_anzahl,
        je_anbieter=je_anbieter,
        anbieter_luecke=luecke,
        beispiele={
            "arr_ohne_server": [t for t in beispiele_arr if t][:BEISPIELE],
            "doppelt": doppel_beispiele[:BEISPIELE],
            "jahr_widerspruch": jahr_beispiele,
        },
        moeglich=True,
    )
    _schreiben(db, stand)
    logger.info(
        "Reconciliation: %d in arr only, %d in server only, %d unmatched, "
        "%d duplicates, %d year conflicts",
        stand.arr_ohne_server,
        stand.server_ohne_arr,
        stand.nicht_erkannt,
        stand.doppelt,
        stand.jahr_widerspruch,
    )
    return stand
