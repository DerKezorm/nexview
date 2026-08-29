"""Die Profilablage mitnehmen - und drueben wiedererkennen, was zu ihr gehoert.

⚠️ **Warum es das braucht.** Nexview fuehrt das Besitzbuch allein in seiner
eigenen Datenbank: Welche Nummer in Radarr zu welchem Profil gehoert, steht in
``qualitaetsprofil_installationen``. Wer Nexview neu aufsetzt und auf dasselbe
Radarr zeigt, steht damit vor seinen **eigenen** Profilen wie vor fremden.

Das ist nicht bloss unschoen. Ein Bauplan bringt bewusst Muster mit **null
Punkten** mit - bei Radarr sind das die 15 Streaming-Kennungen (AMZN, NF, DSNP
und so weiter), die dort nur der Erkennung dienen. Ob so ein Muster zu einem
Plan gehoert oder ein Ueberbleibsel ist, weiss allein die Ablage. Ohne sie
meldet der Bestand sie als "ungenutzt", und wer dann aufraeumt, loescht Teile
seiner eigenen Profile. Am 29.08.2026 an einer frischen Installation gemessen:
**17 ungenutzt** statt 2, und darunter beide TRaSH-Profile.

⚠️ **Bei Sonarr faellt das nicht auf** - dort geben dieselben Streaming-Muster
75 Punkte, also erkennt jede Installation sie als benutzt. Der Unterschied
steckt in den Guides, nicht in Nexview. Wer nur mit Sonarr prueft, haelt das
Problem fuer nicht vorhanden.

⚠️ **Was die Datei NICHT enthaelt.** Keine Zugangsdaten, keine Schluessel,
keine Benutzer - nur Rezepte und Namen. Sie darf weitergegeben werden, ohne
etwas preiszugeben, und sie ist damit auch der Weg, ein Profil an jemand
anderen zu geben.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..models import Qualitaetsprofil
from .arr import ArrClient
from . import qualitaetsprofile as dienst

logger = logging.getLogger("nexview.qualitaet")

#: Kennzeichen der Datei. Wer eine fremde JSON-Datei hochlaedt, soll eine
#: verstaendliche Absage bekommen und keinen Schluesselfehler.
ART = "nexview-qualitaetsprofile"
#: Fassung des Dateiformats - steigt, wenn sich der Aufbau aendert.
FASSUNG = 1


@dataclass
class Ausfuhr:
    """Ein Profil, wie es in der Datei steht."""

    name: str
    dienst: str
    rezept: dict[str, Any]
    #: Wo es zuletzt lag. Nur ein Hinweis - der Import prueft selbst nach.
    lag_auf: list[str] = field(default_factory=list)


@dataclass
class Befund:
    """Was der Import mit **einem** Profil auf **einer** Instanz vorhat."""

    name: str
    dienst: str
    kennung: str
    instanz: str
    #: "uebernehmen" | "weicht_ab" | "nicht_gefunden" | "unerreichbar"
    lage: str
    #: Die Nummer drueben, falls gefunden.
    profil_id_extern: int | None = None
    #: Wie viele Punkte/Qualitaeten anders sind - nur bei "weicht_ab".
    unterschiede: int = 0


@dataclass
class Vorschau:
    """Was der Import insgesamt tun wuerde."""

    #: Profile, die neu in die Ablage kommen.
    neu: list[str] = field(default_factory=list)
    #: Profile, die es in der Ablage schon gibt - sie werden uebersprungen.
    schon_da: list[str] = field(default_factory=list)
    befunde: list[Befund] = field(default_factory=list)

    @property
    def uebernommen(self) -> int:
        return len([b for b in self.befunde if b.lage in ("uebernehmen", "weicht_ab")])


def ausfuehren(db: Session) -> dict[str, Any]:
    """Die ganze Ablage als Datei.

    Die Installationen werden als blosse Kennungsliste mitgegeben, nicht mit
    ihren Nummern: Eine Nummer aus einem fremden Radarr waere schlimmer als
    keine - sie zeigte auf irgendein Profil. Der Import sieht selbst nach.
    """
    profile = dienst.alle(db)
    return {
        "art": ART,
        "fassung": FASSUNG,
        "profile": [
            {
                "name": p.name,
                "dienst": p.dienst,
                "rezept": dict(p.rezept or {}),
                "lag_auf": sorted({e.kennung for e in p.installationen}),
            }
            for p in profile
        ],
    }


def einlesen(roh: Any) -> list[Ausfuhr]:
    """Die Datei pruefen und in Profile zerlegen.

    ⚠️ Fehler hier sind Nutzerfehler, keine Programmfehler: Es kommt die
    falsche Datei, eine aus einer neueren Fassung, oder gar kein JSON. Jeder
    Fall braucht einen Satz, der sagt, was zu tun ist - deshalb eigene
    Ausnahmen statt eines Schluesselfehlers aus der Tiefe.
    """
    if not isinstance(roh, dict):
        raise ValueError("kein_objekt")
    if roh.get("art") != ART:
        raise ValueError("falsche_art")
    fassung = roh.get("fassung")
    if not isinstance(fassung, int) or fassung > FASSUNG:
        raise ValueError("zu_neu")
    profile = roh.get("profile")
    if not isinstance(profile, list) or not profile:
        raise ValueError("leer")

    heraus: list[Ausfuhr] = []
    for eintrag in profile:
        if not isinstance(eintrag, dict):
            raise ValueError("kaputt")
        name = str(eintrag.get("name") or "").strip()
        art = str(eintrag.get("dienst") or "").strip()
        rezept = eintrag.get("rezept")
        if not name or art not in ("radarr", "sonarr") or not isinstance(rezept, dict):
            raise ValueError("kaputt")
        lag = eintrag.get("lag_auf")
        heraus.append(
            Ausfuhr(
                name=name,
                dienst=art,
                rezept=dict(rezept),
                lag_auf=[str(k) for k in lag] if isinstance(lag, list) else [],
            )
        )
    return heraus


async def _befund_je_instanz(
    client: ArrClient,
    kennung: str,
    instanzname: str,
    profil: Qualitaetsprofil,
) -> Befund:
    """Liegt drueben ein Profil dieses Namens - und deckt es sich mit dem Plan?

    ⚠️ **Gesucht wird ueber den Namen, nicht ueber den Inhalt.** Ein Profil,
    das jemand drueben nachjustiert hat, soll trotzdem wiedererkannt werden -
    sonst bliebe genau der Fall ungeloest, der das Aufraeumen gefaehrlich
    macht. Es wird dabei **nichts** geschrieben; Abweichungen werden gezeigt,
    nicht begradigt.
    """
    grund = Befund(
        name=profil.name, dienst=profil.dienst, kennung=kennung,
        instanz=instanzname, lage="nicht_gefunden",
    )
    try:
        drueben = await client.quality_profiles()
    except Exception:  # noqa: BLE001 - eine stumme Instanz kippt den Import nicht
        logger.info("Instance %s did not answer during profile import", kennung)
        grund.lage = "unerreichbar"
        return grund

    treffer = next(
        (p for p in drueben if str(p.get("name") or "") == profil.name), None
    )
    if treffer is None:
        return grund

    grund.profil_id_extern = int(treffer["id"])
    try:
        plan = await dienst.plan_fuer(client, profil, None)
        unterschiede = dienst.abweichungen(treffer, plan)
    except Exception:  # noqa: BLE001
        # Der Plan liess sich nicht bauen - uebernehmen geht trotzdem, nur die
        # Aussage ueber Abweichungen faellt weg.
        logger.info("Could not build plan for %r on %s", profil.name, kennung)
        grund.lage = "uebernehmen"
        return grund

    grund.lage = "uebernehmen" if not unterschiede else "weicht_ab"
    grund.unterschiede = len(unterschiede)
    return grund


async def pruefen(
    db: Session,
    eintraege: list[Ausfuhr],
    instanzen: list[tuple[str, str, str, ArrClient | None]],
) -> Vorschau:
    """Was der Import taete - ohne etwas zu tun.

    ``instanzen`` sind Vierergruppen ``(kennung, name, art, client)``; ``art``
    ist "radarr" oder "sonarr", ``client`` darf ``None`` sein, wenn die Instanz
    nicht eingerichtet ist.

    ⚠️ **Vorschau vor Zugriff** - wie ueberall in diesem Bereich. Der Import
    traegt Profile in die Ablage ein und Nummern ins Besitzbuch; wer das
    ausloest, soll vorher gesehen haben, was wo gefunden wurde.
    """
    vorhandene = {(p.name, p.dienst) for p in dienst.alle(db)}
    schau = Vorschau()

    for eintrag in eintraege:
        if (eintrag.name, eintrag.dienst) in vorhandene:
            schau.schon_da.append(eintrag.name)
            continue
        schau.neu.append(eintrag.name)

        # Nur zum Rechnen - kommt nicht in die Datenbank.
        entwurf = Qualitaetsprofil(
            name=eintrag.name, dienst=eintrag.dienst, rezept=eintrag.rezept
        )
        for kennung, instanzname, art, client in instanzen:
            if art != eintrag.dienst:
                continue
            if client is None:
                schau.befunde.append(
                    Befund(
                        name=eintrag.name, dienst=eintrag.dienst, kennung=kennung,
                        instanz=instanzname, lage="unerreichbar",
                    )
                )
                continue
            schau.befunde.append(
                await _befund_je_instanz(client, kennung, instanzname, entwurf)
            )
    return schau


async def uebernehmen(
    db: Session,
    eintraege: list[Ausfuhr],
    instanzen: list[tuple[str, str, str, ArrClient | None]],
) -> Vorschau:
    """Die Profile in die Ablage holen und drueben Vorgefundenes uebernehmen.

    ⚠️ **Es wird nichts nach Radarr geschrieben.** Uebernehmen heisst: Nexview
    traegt die Nummer in sein Besitzbuch ein. Was drueben liegt, bleibt Zeichen
    fuer Zeichen so, wie es war - auch wenn es vom Bauplan abweicht. Wer es
    angleichen will, drueckt danach "Verteilen" und sieht vorher die
    Unterschiede.

    ⚠️ **Ein Profil, dessen Name in der Ablage schon vergeben ist, wird
    uebersprungen** statt ueberschrieben. Zwei Rezepte unter einem Namen waeren
    beim naechsten Verteilen ein Namensstreit auf der Instanz - und welches das
    richtige ist, kann nur der Betreiber wissen.
    """
    schau = Vorschau()
    vorhandene = {(p.name, p.dienst) for p in dienst.alle(db)}

    for eintrag in eintraege:
        if (eintrag.name, eintrag.dienst) in vorhandene:
            schau.schon_da.append(eintrag.name)
            continue

        profil = dienst.anlegen(db, eintrag.name, eintrag.dienst, eintrag.rezept)
        schau.neu.append(eintrag.name)

        for kennung, instanzname, art, client in instanzen:
            if art != eintrag.dienst or client is None:
                continue
            befund = await _befund_je_instanz(client, kennung, instanzname, profil)
            schau.befunde.append(befund)
            if befund.lage not in ("uebernehmen", "weicht_ab"):
                continue
            try:
                plan = await dienst.plan_fuer(client, profil, None)
            except Exception:  # noqa: BLE001
                logger.info("No plan for %r on %s - not adopted", profil.name, kennung)
                continue
            dienst.merken(
                db,
                profil,
                kennung,
                dienst.Schreibergebnis(
                    profil_id_extern=befund.profil_id_extern or 0,
                    # ⚠️ Der Abdruck des **Plans**, nicht der Kopie - siehe
                    # ``abdruck_von``. Sonst hiesse eine drueben nachjustierte
                    # Kopie "update" statt "angepasst".
                    fingerabdruck=dienst.abdruck_von(plan),
                    trash_stand=plan.stand,
                    formate_neu=0,
                    formate_wiederverwendet=0,
                    hinweise=(),
                ),
            )
            logger.info(
                "Adopted %r on %s as id %s (%s)",
                profil.name, kennung, befund.profil_id_extern, befund.lage,
            )
    db.commit()
    return schau
