"""Zwei Instanzen, eine Kategorie - der Fehler, der wie ein Netzproblem aussieht.

⚠️ **Was passiert.** Radarr und Sonarr sehen im Download-Programm nur, was in
*ihrer* Kategorie liegt. Benutzen zwei Instanzen dieselbe, sieht jede die
Downloads der anderen, greift danach und importiert sie - oder raeumt sie weg.
Die Anfragen haengen, Dateien landen an der falschen Stelle, und weil an keiner
Stelle ein Fehler steht, sucht der Betreiber beim Netz, beim Indexer, beim
Usenet-Anbieter.

⚠️ **Niemand warnt davor.** Radarr kann Kategorien nicht anlegen - sie muessen
drueben im Download-Programm existieren - und prueft auch nicht, ob eine andere
Instanz dieselbe schon benutzt. Es *kennt* die andere Instanz gar nicht.
Nexview kennt beide. Das ist der ganze Grund, warum diese Pruefung hier steht
und nicht dort.

⚠️ **Es ist kein SABnzbd-Problem.** Gemessen am 29.08.2026 an einem echten
Radarr: **16 von 18** unterstuetzten Programmen fuehren ein Kategorie-Feld,
darunter qBittorrent, Deluge, Transmission und NZBGet. Zwei kennen gar keine
(Aria2, Flood) - die kollidieren bei gemeinsamer Nutzung immer.

⚠️ **Leer ist der schwerere Fall.** Radarr sagt dazu selbst: "Das Hinzufuegen
einer eigenen Kategorie fuer Radarr vermeidet Konflikte mit anderen Downloads,
die nicht zu Radarr gehoeren. Die Verwendung einer Kategorie ist optional, wird
aber dringend empfohlen." Ohne Kategorie greift eine Instanz also nach allem,
was dort liegt - nicht nur nach dem einer zweiten Instanz.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("nexview.instanzen")

#: Felder, die die "Bahn" innerhalb eines Programms bestimmen.
#:
#: ⚠️ Der Name unterscheidet sich je Dienst und Programm - Radarr nennt es
#: ``movieCategory``, Sonarr ``tvCategory``, manche schlicht ``category``.
#: Verglichen wird der **Wert**, nicht der Feldname: Traegt Radarrs
#: ``movieCategory`` denselben Text wie Sonarrs ``tvCategory``, sehen beide
#: dieselben Downloads.
KATEGORIE_FELDER = ("category", "movieCategory", "tvCategory", "musicCategory")

#: Programme ohne Kategorie arbeiten ueber einen Ordner - dort ist der Ordner
#: die Bahn. ``watchFolder`` ist der entscheidende: Aus ihm wird importiert.
ORDNER_FELDER = ("watchFolder",)

#: ⚠️ **Nicht** mitvergleichen: Das ist die Kategorie *nach* dem Import. Zwei
#: Instanzen duerfen ihre fertigen Downloads in dieselbe Ablage legen - dort
#: greift keine mehr zu.
NICHT_VERGLEICHEN = ("movieImportedCategory", "tvImportedCategory")


@dataclass(frozen=True)
class Bahn:
    """Ein Programm und die Spur darin, auf die eine Instanz zugreift."""

    #: Programmkennung zum Vergleichen: Art, Rechner, Anschluss.
    programm: str
    #: Wie es dem Betreiber genannt wird - "SABnzbd auf 10.10.10.109:8080".
    programm_name: str
    #: Leer heisst: keine Kategorie gesetzt (oder das Programm kennt keine).
    kategorie: str


@dataclass
class Kollision:
    """Mehrere Instanzen auf derselben Bahn."""

    #: Programmkennung zum Vergleichen - geht nur den Schluessel etwas an.
    programm: str
    programm_name: str
    kategorie: str
    #: Die Namen der beteiligten Instanzen, in der Reihenfolge der Einrichtung.
    instanzen: list[str] = field(default_factory=list)
    #: Die Kennungen dazu - die Oberflaeche haengt die Warnung an ihre Kacheln.
    kennungen: list[str] = field(default_factory=list)

    @property
    def ohne_kategorie(self) -> bool:
        return not self.kategorie

    @property
    def schluessel(self) -> str:
        """Woran diese Kollision wiedererkannt wird - fuers Wegklicken.

        ⚠️ Die beteiligten Instanzen gehoeren dazu. Wer die Warnung fuer zwei
        Instanzen wegklickt und spaeter eine dritte auf dieselbe Kategorie
        setzt, soll sie wieder sehen: Das ist ein neuer Fehler, kein
        weggeklickter alter.
        """
        return f"{self.programm}|{self.kategorie}|{'+'.join(sorted(self.kennungen))}"


def _feld(client: dict, name: str) -> str:
    for f in client.get("fields") or []:
        if f.get("name") == name:
            wert = f.get("value")
            return "" if wert is None else str(wert).strip()
    return ""


def bahn_von(client: dict) -> Bahn | None:
    """Auf welche Spur greift dieses Download-Programm zu?

    ``None`` heisst: kommt fuer einen Vergleich nicht infrage - abgeschaltet,
    oder ohne erkennbaren Rechner.
    """
    if client.get("enable") is False:
        return None

    art = str(client.get("implementation") or "?")
    host = _feld(client, "host")
    port = _feld(client, "port")
    basis = _feld(client, "urlBase")

    kategorie = ""
    for name in KATEGORIE_FELDER:
        wert = _feld(client, name)
        if wert:
            kategorie = wert
            break
    if not kategorie:
        for name in ORDNER_FELDER:
            wert = _feld(client, name)
            if wert:
                kategorie = wert
                break

    if not host:
        # Ordnerbasierte Programme (Blackhole) haben keinen Rechner - dann ist
        # der Ordner selbst die ganze Kennung.
        if not kategorie:
            return None
        programm = f"{art}|{kategorie}"
        return Bahn(programm=programm, programm_name=art, kategorie=kategorie)

    anschluss = f"{host}:{port}" if port else host
    programm = f"{art}|{anschluss}|{basis}".lower()
    return Bahn(
        programm=programm,
        programm_name=f"{art} auf {anschluss}",
        # ⚠️ Kleinschreibung beim Vergleichen: SABnzbd unterscheidet bei
        # Kategorienamen nicht, und "Movies" gegen "movies" waere sonst ein
        # uebersehener Treffer.
        kategorie=kategorie.lower(),
    )


def finden(
    je_instanz: list[tuple[str, str, list[dict]]],
) -> list[Kollision]:
    """Welche Instanzen teilen sich eine Bahn?

    ``je_instanz`` sind Dreiergruppen ``(kennung, name, download_programme)``.

    ⚠️ **Eine Instanz kann mehrere Programme haben.** Verglichen wird je
    Programm, nicht je Instanz - wer zwei Downloader betreibt und nur bei einem
    kollidiert, soll genau das erfahren.
    """
    nach_bahn: dict[tuple[str, str], Kollision] = {}
    for kennung, name, programme in je_instanz:
        # Dieselbe Bahn zweimal an einer Instanz ist keine Kollision.
        gesehen: set[tuple[str, str]] = set()
        for client in programme or []:
            bahn = bahn_von(client)
            if bahn is None:
                continue
            schluessel = (bahn.programm, bahn.kategorie)
            if schluessel in gesehen:
                continue
            gesehen.add(schluessel)
            treffer = nach_bahn.get(schluessel)
            if treffer is None:
                treffer = Kollision(
                    programm=bahn.programm,
                    programm_name=bahn.programm_name,
                    kategorie=bahn.kategorie,
                )
                nach_bahn[schluessel] = treffer
            treffer.instanzen.append(name)
            treffer.kennungen.append(kennung)

    return [k for k in nach_bahn.values() if len(k.kennungen) > 1]


# ------------------------------------------------------------- Weggeklicktes
#
# ⚠️ **Es gibt Aufbauten, in denen das Absicht ist.** Wer zwei Instanzen
# bewusst auf eine Kategorie stellt, soll die Warnung loswerden koennen - sonst
# steht sie fuer immer da und stumpft gegen die naechste ab, die zaehlt.

#: Wo die weggeklickten Kennungen liegen.
SCHLUESSEL_IGNORIERT = "download_kollision_ignoriert"


def ignorierte(db) -> set[str]:
    """Welche Kollisionen der Betreiber weggeklickt hat."""
    from ..models import Setting  # lokal: das Modell haengt sonst am Modulstart

    zeile = db.get(Setting, SCHLUESSEL_IGNORIERT)
    if zeile is None or not zeile.value:
        return set()
    return {t for t in str(zeile.value).split("\n") if t}


def ignorieren(db, schluessel: str) -> None:
    """Eine Kollision wegklicken - dauerhaft, bis sich die Beteiligten aendern."""
    from ..models import Setting

    alle = ignorierte(db)
    alle.add(schluessel)
    zeile = db.get(Setting, SCHLUESSEL_IGNORIERT)
    if zeile is None:
        zeile = Setting(key=SCHLUESSEL_IGNORIERT, value="")
        db.add(zeile)
    zeile.value = "\n".join(sorted(alle))
    db.commit()
    logger.info("Download category collision dismissed: %s", schluessel)


def offen(alle: list[Kollision], weggeklickt: set[str]) -> list[Kollision]:
    """Was nach dem Wegklicken uebrig bleibt."""
    return [k for k in alle if k.schluessel not in weggeklickt]
