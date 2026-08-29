"""Wo Radarr eine Datei sieht - und wo der Medienserver dieselbe Datei sieht.

⚠️ **Warum es diese Datei ueberhaupt gibt.** Radarr sagt dem Medienserver nicht
"Film Nr. 42 hat sich geaendert", sondern nennt einen **Pfad**. Steckt jede
Seite in einem eigenen Container, meint derselbe Film zwei verschiedene Pfade:
Radarr kennt ``/data/Movies``, Plex kennt ``/media/Movies``. Der Anruf kommt
dann an, wird hoeflich mit "OK" beantwortet - und der Medienserver sucht an
einer Stelle nach, die es bei ihm nicht gibt.

Das ist die schlimmste Sorte Fehler: Die Verbindung *prueft* sich gruen, sie
steht in der Liste, und sie tut trotzdem nichts. Radarr hat dafuer die Felder
``mapFrom``/``mapTo``. Nur fragt es nie danach - man muss sie kennen.

**Wie Nexview sie ermittelt.** Es fragt beide Seiten nach ihren Wurzeln:
Radarr ueber ``/rootfolder``, den Medienserver nach seinen Bibliotheken. Dann
vergleicht es die Pfade **von hinten**. Was hinten gleich ist, ist der
gemeinsame Teil; was vorne uebrig bleibt, ist die Zuordnung::

    Radarr:  /data  /Movies4K      \\
                                    >  gemeinsam: Movies4K
    Plex:    /media /Movies4K      /

    ⇒ mapFrom = /data,  mapTo = /media

**Was diese Datei bewusst nicht tut: raten.** Findet sich kein gemeinsames
Ende, gibt es keine Zuordnung - und dann ist "kann ich nicht sagen" die einzige
ehrliche Antwort. Ein erfundenes ``mapFrom`` waere schlimmer als keines, weil
es einen Fehler festschreibt, den danach niemand mehr sucht.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from xml.etree import ElementTree

import httpx

logger = logging.getLogger("nexview.qualitaet")

#: Kurz halten: Das laeuft waehrend ein Mensch auf eine Seite wartet.
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


@dataclass
class Zuordnung:
    """Was aus dem Vergleich zweier Wurzeln herauskam."""

    #: Der Teil, den Radarr sagt. Leer heisst: keine Umschreibung noetig.
    von: str = ""
    #: Der Teil, den der Medienserver versteht.
    nach: str = ""
    #: Warum nichts ermittelt wurde, falls nichts ermittelt wurde.
    #:
    #: ``""``               - alles gut (auch wenn von/nach leer sind: dann
    #:                        sehen beide Seiten denselben Pfad)
    #: ``"keine_pfade"``    - der Medienserver rueckt keine Pfade heraus
    #: ``"keine_wurzeln"``  - die Instanz hat keine Stammordner
    #: ``"kein_treffer"``   - beides da, aber nichts passt zusammen
    hindernis: str = ""
    #: Die Belege, damit der Mensch die Rechnung nachvollziehen kann.
    beispiel_arr: str = ""
    beispiel_server: str = ""


@dataclass
class Serverpfade:
    """Die Bibliothekswurzeln eines Medienservers."""

    pfade: list[str] = field(default_factory=list)
    hindernis: str = ""


def _teile(pfad: str) -> list[str]:
    """Einen Pfad in Bestandteile zerlegen - Windows und Unix gleichermassen.

    Medienserver und Instanz koennen verschiedene Trennzeichen benutzen; fuer
    den Vergleich zaehlt nur die Abfolge der Namen.
    """
    return [t for t in pfad.replace("\\", "/").split("/") if t]


def _wieder_zusammen(teile: list[str], vorbild: str) -> str:
    """Aus Bestandteilen wieder einen Pfad - in der Schreibweise des Vorbilds."""
    if not teile:
        return ""
    if "\\" in vorbild and "/" not in vorbild.replace("\\", ""):
        return "\\".join(teile) if not vorbild.startswith("\\") else "\\" + "\\".join(teile)
    return "/" + "/".join(teile)


def ableiten(arr_wurzeln: list[str], server_pfade: list[str]) -> Zuordnung:
    """Die Zuordnung aus zwei Listen von Wurzeln ermitteln.

    ⚠️ **Das laengste gemeinsame Ende gewinnt.** Bei mehreren Kandidaten ist
    der mit der groessten Uebereinstimmung der sicherste: ``Movies4K`` allein
    koennte Zufall sein, ``Filme/Movies4K`` kaum noch.

    Bewusst ohne Netzzugriff, damit es sich ohne laufende Server pruefen laesst.
    """
    if not arr_wurzeln:
        return Zuordnung(hindernis="keine_wurzeln")
    if not server_pfade:
        return Zuordnung(hindernis="keine_pfade")

    bester: tuple[int, Zuordnung] | None = None
    for arr_pfad in arr_wurzeln:
        arr_teile = _teile(arr_pfad)
        for server_pfad in server_pfade:
            server_teile = _teile(server_pfad)
            # Von hinten zaehlen, wie viel uebereinstimmt.
            gleich = 0
            while (
                gleich < len(arr_teile)
                and gleich < len(server_teile)
                and arr_teile[-1 - gleich].lower() == server_teile[-1 - gleich].lower()
            ):
                gleich += 1
            if gleich == 0:
                continue
            kopf_arr = arr_teile[: len(arr_teile) - gleich]
            kopf_server = server_teile[: len(server_teile) - gleich]
            treffer = Zuordnung(
                von=_wieder_zusammen(kopf_arr, arr_pfad),
                nach=_wieder_zusammen(kopf_server, server_pfad),
                beispiel_arr=arr_pfad,
                beispiel_server=server_pfad,
            )
            if bester is None or gleich > bester[0]:
                bester = (gleich, treffer)

    if bester is None:
        # ⚠️ Hier **nicht** auf "/" zurueckfallen. Kein gemeinsames Ende heisst,
        # dass die beiden Seiten nichts miteinander zu tun haben - etwa weil der
        # Medienserver ganz andere Bibliotheken fuehrt. Dann ist Schweigen
        # richtig.
        return Zuordnung(
            hindernis="kein_treffer",
            beispiel_arr=arr_wurzeln[0],
            beispiel_server=server_pfade[0],
        )
    return bester[1]


#: Welche Plex-Abschnitte ueberhaupt in Frage kommen.
#:
#: ⚠️ Musik und Fotos gehoeren nicht dazu: Zu ihnen gibt es keine
#: Radarr-Wurzel, und als Kandidat wuerden sie den Vergleich nur verwaessern.
PLEX_ARTEN = ("movie", "show")


def _plex_pfade(roh: str) -> list[str]:
    """Die Bibliothekspfade aus Plex' Antwort - JSON **oder** XML.

    ⚠️ **Beides, nicht nur JSON.** Mit ``Accept: application/json`` antwortet
    Plex normalerweise in JSON, aber verlassen kann man sich darauf nicht:
    Ein Reverse Proxy kann den Kopf verschlucken, aeltere Fassungen antworten
    ohnehin in XML. Wer nur JSON liest, meldet dann "nicht erreichbar" - obwohl
    der Server sauber geantwortet hat. Diese Fehlmeldung schickt den Betreiber
    auf die Suche nach einem Netzproblem, das es nicht gibt.
    """
    text = (roh or "").lstrip()
    if text.startswith("{"):
        try:
            abschnitte = (json.loads(text).get("MediaContainer") or {}).get("Directory") or []
        except ValueError:
            return []
        return [
            str(ort.get("path") or "")
            for abschnitt in abschnitte
            if abschnitt.get("type") in PLEX_ARTEN
            for ort in (abschnitt.get("Location") or [])
            if ort.get("path")
        ]

    try:
        wurzel = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return []
    return [
        str(ort.get("path") or "")
        for abschnitt in wurzel.iter("Directory")
        if abschnitt.get("type") in PLEX_ARTEN
        for ort in abschnitt.iter("Location")
        if ort.get("path")
    ]


async def server_pfade(provider: str, url: str, zugang: str) -> Serverpfade:
    """Die Bibliothekswurzeln eines Medienservers holen.

    ⚠️ **Beide Seiten geben das nur Berechtigten.** Jellyfin und Emby liefern
    ``/Library/VirtualFolders`` nur einem Zugang mit Verwaltungsrechten; Plex
    nennt die Pfade in ``/library/sections`` nur dem Besitzer. Ein Zugang ohne
    diese Rechte bekommt eine leere Liste - und das ist kein Fehler, sondern
    schlicht "weiss ich nicht".
    """
    if not zugang:
        return Serverpfade(hindernis="kein_schluessel")
    stamm = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, verify=False) as klient:
            if provider == "plex":
                antwort = await klient.get(
                    f"{stamm}/library/sections",
                    headers={"X-Plex-Token": zugang, "Accept": "application/json"},
                )
                antwort.raise_for_status()
                pfade = _plex_pfade(antwort.text)
            else:
                # ⚠️ **Jellyfin genuegt ``X-Emby-Token`` nicht.** Es besteht auf
                # der ausgeschriebenen ``MediaBrowser``-Zeile mit ``Client``;
                # ohne sie weist es die Anfrage ab. Emby nimmt beide - also
                # schicken wir beide und haben eine Sonderregel weniger.
                antwort = await klient.get(
                    f"{stamm}/Library/VirtualFolders",
                    headers={
                        "X-Emby-Token": zugang,
                        "Authorization": (
                            'MediaBrowser Client="Nexview", Device="Nexview", '
                            f'DeviceId="nexview-pfade", Token="{zugang}"'
                        ),
                        "Accept": "application/json",
                    },
                )
                antwort.raise_for_status()
                pfade = [
                    str(ort)
                    for ordner in antwort.json() or []
                    if ordner.get("CollectionType") in ("movies", "tvshows", None)
                    for ort in (ordner.get("Locations") or [])
                    if ort
                ]
    except Exception as fehler:  # noqa: BLE001 - eine stille Zuordnung ist kein Absturz
        logger.info("Could not read library paths from %s: %s", url, fehler)
        return Serverpfade(hindernis="unreachable")

    if not pfade:
        return Serverpfade(hindernis="keine_pfade")
    return Serverpfade(pfade=sorted(set(pfade)))
