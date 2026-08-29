"""Qualitaetsprofile ablegen und auf Instanzen schreiben.

Die Ablage ist das Einfache; das Schreiben hat drei Fallen, die live gemessen
wurden (27.08.2026, Radarr 6.3.0) und die man kennen muss, sonst antwortet die
Instanz mit Meldungen, die in die Irre fuehren:

1. ``specifications[].fields`` ist bei TRaSH ein **Objekt**, Radarr erwartet
   eine **Liste** - sonst ``400`` mit einer Meldung ueber .NET-Typen
   (behandelt in ``services/trash.py``).
2. Radarr fasst WEB-Qualitaeten schon selbst zu Gruppen zusammen. Eine eigene
   Merge-Gruppe muss diese Gruppe **ersetzen**, nicht danebengestellt werden.
3. ``formatItems`` muss **alle** Erkennungsmuster der Instanz enthalten, nicht
   nur die bepunkteten - fehlende gelten sonst als geloescht.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Qualitaetsprofil, QualitaetsprofilInstallation, utcnow
from .arr import ArrClient, ArrError
from .trash import Bauplan, bauplan

logger = logging.getLogger("nexview.qualitaet")

#: ⚠️ **Hier stand einmal ein Praefix "NXV - ".** Gedacht war es als Etikett:
#: In Radarr sollte man sehen, welche Erkennungsmuster von Nexview stammen.
#:
#: Das war ein Fehler. **82 der 242 TRaSH-Formate sind ausdruecklich dafuer
#: gedacht, im Dateinamen zu erscheinen** (``includeCustomFormatWhenRenaming``)
#: - Streaming-Anbieter, Schnittfassungen, Tonspur-Kennzeichnungen. Bei denen
#: ist der Name kein Etikett, sondern Inhalt. Mit Praefix hiessen die Dateien
#: dann ``[NXV - German DL]`` statt ``[German DL]``.
#:
#: Jetzt gilt der Originalname aus den Guides - so macht es auch Recyclarr, und
#: damit heisst "German DL" ueberall dasselbe. Wem etwas gehoert, sagt ohnehin
#: das Besitzbuch, nicht der Name.
PRAEFIX = ""

#: Der alte Praefix - nur noch, um Altbestand einzusammeln (siehe _alt_umbenennen).
ALTER_PRAEFIX = "NXV - "
#: Eigene Gruppen-Nummer. Radarr vergibt 1000 aufwaerts fuer seine eigenen;
#: mit Abstand dazwischen kollidiert nichts.
GRUPPEN_NUMMER = 1100


@dataclass
class Schreibergebnis:
    profil_id_extern: int
    fingerabdruck: str
    trash_stand: str
    formate_neu: int
    formate_wiederverwendet: int
    hinweise: tuple[str, ...]


@dataclass
class Fortschritt:
    """Wo das Schreiben gerade steht.

    ⚠️ **Warum das ueberhaupt noetig ist.** Ein Profil zu schreiben heisst, bis
    zu sechzig Erkennungsmuster **einzeln** anzulegen - Radarr nimmt sie nicht
    im Paket. Live gemessen dauert das rund anderthalb Minuten. Ohne Anzeige
    sieht der Nutzer in dieser Zeit einen Knopf, der sich nicht ruehrt, und
    muss raten, ob noch etwas passiert.
    """

    instanz: str = ""
    #: "plan" (Bauplan bauen) | "formate" (Muster anlegen) | "profil" (Profil schreiben)
    schritt: str = "plan"
    erledigt: int = 0
    gesamt: int = 0
    #: Die wievielte von wie vielen Instanzen - beim Verteilen auf mehrere.
    instanz_nummer: int = 1
    von_instanzen: int = 1


#: Der Stand je Profil, waehrend geschrieben wird.
#:
#: ⚠️ **Im Arbeitsspeicher, nicht in der Datenbank.** Der Wert lebt nur so
#: lange wie der Vorgang selbst; ihn zu speichern hiesse, ihn nach einem
#: Absturz auch wieder aufraeumen zu muessen. Setzt voraus, dass Nexview in
#: einem Prozess laeuft - tut es (ein Uvicorn ohne ``--workers``).
_fortschritt: dict[int, Fortschritt] = {}


def fortschritt(profil_id: int) -> Fortschritt | None:
    return _fortschritt.get(profil_id)


@contextmanager
def fortschritt_fuehren(profil_id: int) -> Iterator[Fortschritt]:
    """Den Stand fuer die Dauer eines Schreibvorgangs bereitstellen.

    Als Kontext, damit er auch dann verschwindet, wenn unterwegs etwas
    schiefgeht - ein liegengebliebener Eintrag hiesse fuer die Oberflaeche
    "laeuft noch", und zwar bis zum Neustart.
    """
    stand = Fortschritt()
    _fortschritt[profil_id] = stand
    try:
        yield stand
    finally:
        _fortschritt.pop(profil_id, None)


# --------------------------------------------------------------------- Ablage


def alle(db: Session) -> list[Qualitaetsprofil]:
    return list(db.scalars(select(Qualitaetsprofil).order_by(Qualitaetsprofil.id)))


def eintrag(db: Session, profil_id: int) -> Qualitaetsprofil | None:
    return db.get(Qualitaetsprofil, profil_id)


def anlegen(db: Session, name: str, dienst: str, rezept: dict) -> Qualitaetsprofil:
    profil = Qualitaetsprofil(name=name.strip(), dienst=dienst, rezept=rezept)
    db.add(profil)
    db.flush()
    return profil


def loeschen(db: Session, profil: Qualitaetsprofil) -> None:
    """Nur aus Nexview.

    ⚠️ **Die Kopien in Radarr bleiben stehen.** Ein Profil zu loeschen, das
    Titeln zugewiesen ist, wuerde diese Titel beschaedigen - das darf nicht
    als Nebenwirkung des Aufraeumens hier passieren.
    """
    db.delete(profil)


def installation(
    db: Session, profil_id: int, kennung: str
) -> QualitaetsprofilInstallation | None:
    return db.scalar(
        select(QualitaetsprofilInstallation).where(
            QualitaetsprofilInstallation.profil_id == profil_id,
            QualitaetsprofilInstallation.kennung == kennung,
        )
    )


def _gestalt(plan: Bauplan) -> dict:
    """Der Bauplan auf das reduziert, worauf es beim Vergleich ankommt."""
    return {
        "merge": sorted(plan.merge),
        "min": plan.min_punkte,
        "schluss": plan.schluss_punkte,
        "formate": sorted((f.name, f.punkte) for f in plan.formate),
    }


def _fingerabdruck(plan: Bauplan) -> str:
    """Was genau geschrieben wurde - Grundlage fuer "hat jemand daran gedreht?"."""
    return _abdruck(_gestalt(plan))


def _abdruck(gestalt: dict) -> str:
    return hashlib.sha256(
        json.dumps(gestalt, sort_keys=True).encode()
    ).hexdigest()[:32]


def _gestalt_von_instanz(profil_live: dict, plan: Bauplan) -> dict:
    """Dieselbe Gestalt, aber aus dem, was gerade in Radarr steht.

    ⚠️ **Nur die Muster des Bauplans zaehlen.** ``formatItems`` fuehrt *jedes*
    Muster der Instanz auf, auch die aus fremden Profilen - die stehen dort mit
    0 Punkten und gehoeren nicht zu diesem Profil. Zusaetzlich wird geprueft,
    ob jemand einem fremden Muster Punkte gegeben hat; das waere eine Aenderung
    von Hand und faellt sonst durch das Raster.
    """
    punkte_je_name: dict[str, int] = {}
    for eintrag in profil_live.get("formatItems", []):
        name = str(eintrag.get("name") or "")
        punkte_je_name[name] = int(eintrag.get("score") or 0)

    # ⚠️ **Ein Muster, zwei Schreibweisen.** Frueher schrieb Nexview seine
    # Muster mit dem Vorsatz "NXV - " - bis sich zeigte, dass er in die
    # Dateinamen durchschlug. Auf Instanzen, die seither nicht neu beschrieben
    # wurden, stehen sie weiter unter dem alten Namen.
    #
    # Ohne diese Ruecksicht faende der Vergleich *keines* der erwarteten Muster
    # (alle 0 Punkte) und hielte *alle* alten fuer fremd - und meldete damit
    # "von dir angepasst", wo niemand etwas angefasst hat. Eine Umbenennung
    # unsererseits darf niemandem als Eingriff angelastet werden.
    # ⚠️ **Es gilt die Schreibweise, die wirklich Punkte traegt.**
    #
    # Auf einer Instanz liegen beide nebeneinander: Was Nexview vor der
    # Praefix-Umstellung geschrieben hat, bepunktet "NXV - AMZN"; was danach
    # entstand, bepunktet "AMZN". Und weil Radarr und Sonarr eigene Muster
    # gleichen Namens mitbringen, steht die jeweils andere Schreibweise fast
    # immer mit 0 daneben.
    #
    # Zwei Anlaeufe waren zu grob und haben je einen Fall falsch beschuldigt:
    # "immer die alte zuerst" verlor die neu geschriebenen Profile, und eine
    # Mehrheitsregel je Profil verlor die einzelnen Muster, die beim Aufraeumen
    # blockiert blieben (dort trug die Mehrheit den schlichten Namen, ein paar
    # aber weiter den alten).
    #
    # Ein Muster mit 0 Punkten wirkt sich nicht aus; taucht derselbe Name mit
    # Punkten auf, ist **das** der Eintrag, den dieses Profil benutzt. Steht in
    # beiden Schreibweisen etwas, gilt die schlichte - die aktuelle.
    def _punkte(name: str) -> int:
        schlicht = punkte_je_name.get(name, 0)
        if schlicht:
            return schlicht
        return punkte_je_name.get(ALTER_PRAEFIX + name, 0)

    formate = [(w.name, _punkte(w.name)) for w in plan.formate]
    erwartet = {w.name for w in plan.formate}
    erwartet |= {ALTER_PRAEFIX + w.name for w in plan.formate}
    fremd_mit_punkten = sorted(
        (name, punkte)
        for name, punkte in punkte_je_name.items()
        if punkte != 0 and name not in erwartet
    )

    merge: list[str] = []
    for eintrag in profil_live.get("items", []):
        if not eintrag.get("allowed"):
            continue
        kinder = eintrag.get("items") or []
        if kinder:
            merge.extend(
                k["quality"]["name"] for k in kinder if k.get("allowed")
            )
        elif "quality" in eintrag:
            merge.append(eintrag["quality"]["name"])

    return {
        "merge": sorted(set(merge)),
        "min": int(profil_live.get("minFormatScore") or 0),
        "schluss": int(profil_live.get("cutoffFormatScore") or 0),
        "formate": sorted(formate),
        "fremd": fremd_mit_punkten,
    }


@dataclass
class Unterschied:
    """Ein einzelner Unterschied - so, dass die Oberflaeche ihn zeigen kann."""

    art: str
    was: str = ""
    ist: str = ""
    soll: str = ""


@dataclass
class Abgleich:
    """Der Zustand einer Installation.

    ⚠️ Die fuenf Faelle sind nicht ausgedacht, sondern das Kreuz aus zwei
    Fragen - hat sich die Quelle bewegt, hat jemand drueben gedreht - plus der
    Fall, dass das Profil drueben gar nicht mehr existiert.
    """

    kennung: str
    stand: str
    unterschiede: list[Unterschied]


def _unterschiede(soll: dict, ist: dict) -> list[Unterschied]:
    """Was zwischen zwei Gestalten abweicht - in der Reihenfolge der Wichtigkeit."""
    liste: list[Unterschied] = []
    if soll["merge"] != ist["merge"]:
        liste.append(
            Unterschied(
                art="qualitaeten",
                ist=", ".join(ist["merge"]),
                soll=", ".join(soll["merge"]),
            )
        )
    for schluessel, art in (("min", "mindestpunkte"), ("schluss", "schlusspunkte")):
        if soll[schluessel] != ist[schluessel]:
            liste.append(
                Unterschied(art=art, ist=str(ist[schluessel]), soll=str(soll[schluessel]))
            )
    soll_formate = dict(soll["formate"])
    ist_formate = dict(ist["formate"])
    for name, punkte in sorted(soll_formate.items()):
        if ist_formate.get(name, 0) != punkte:
            liste.append(
                Unterschied(
                    art="punkte",
                    was=name,
                    ist=str(ist_formate.get(name, 0)),
                    soll=str(punkte),
                )
            )
    for name, punkte in ist.get("fremd", []):
        liste.append(Unterschied(art="fremd", was=name, ist=str(punkte), soll="0"))
    return liste


def unsere_kopie(
    profile_live: list[dict], nummer: int | None, name: str
) -> dict | None:
    """Das Profil drueben, das wirklich unseres ist - oder ``None``.

    ⚠️ **Die Nummer allein genuegt nicht.** Nexview merkt sich, unter welcher
    Nummer sein Profil auf der Instanz liegt. Die Nummer gilt aber nur dort:
    Wird eine Nexview-Sicherung auf einem **anderen** Radarr eingespielt - oder
    Radarr selbst neu aufgesetzt -, zeigt sie ins Nichts oder, schlimmer, auf
    ein fremdes Profil, das zufaellig dieselbe Nummer bekommen hat. Radarr
    vergibt sie fortlaufend, ein neu aufgebautes faengt wieder bei 1 an.

    Ohne diese Pruefung wuerde "Neu schreiben" dann ein **fremdes Profil
    ueberschreiben**, und der Betreiber saehe nur, dass "sein" Profil sich
    merkwuerdig veraendert hat.

    ⚠️ **Der Name ist der einzige Anhaltspunkt.** Radarrs Qualitaetsprofile
    haben kein Feld fuer Markierungen - kein ``tags``, keine Beschreibung
    (gemessen 29.08.2026). Es gibt also keine Moeglichkeit, drueben zu
    hinterlassen "das ist von Nexview". Bleibt: Nummer **und** Name muessen
    zusammenpassen. Tun sie das nicht, ist es nicht unseres - egal, was unsere
    eigene Datenbank behauptet.
    """
    if nummer is None:
        return None
    live = next((p for p in profile_live if p.get("id") == nummer), None)
    if live is None:
        return None
    if str(live.get("name") or "") != name:
        logger.info(
            "Profile %s on the instance is named %r, not %r - not treating it as ours",
            nummer,
            live.get("name"),
            name,
        )
        return None
    return live


def abweichungen(profil_live: dict, plan: Bauplan) -> list[Unterschied]:
    """Was unterscheidet die Kopie drueben von dem, was der Bauplan will?

    ⚠️ **Oeffentlich, weil der Umzug sie braucht.** Beim Uebernehmen einer
    vorgefundenen Kopie muss gesagt werden koennen, ob sie sich deckt - ohne
    dafuer in die Innereien dieses Moduls zu greifen. Leere Liste heisst:
    identisch.
    """
    return _unterschiede(_gestalt(plan), _gestalt_von_instanz(profil_live, plan))


def abdruck_von(plan: Bauplan) -> str:
    """Der Fingerabdruck eines Bauplans.

    ⚠️ Beim Uebernehmen wird **dieser** festgehalten, nicht der der
    vorgefundenen Kopie. Der Unterschied entscheidet, was die Ablage danach
    anzeigt: Mit dem Plan-Abdruck gilt eine abweichende Kopie als
    "angepasst" - jemand hat drueben nachgebessert - und genau das ist wahr.
    Mit dem Abdruck der Kopie hiesse es "update", also "die Quelle hat sich
    bewegt", und das waere gelogen.
    """
    return _fingerabdruck(plan)


async def vergleichen(
    client: ArrClient,
    profil: Qualitaetsprofil,
    eintrag_: QualitaetsprofilInstallation,
    profile_live: list[dict] | None = None,
    umgebung: Umgebung | None = None,
) -> Abgleich:
    """Steht drueben noch, was wir geschrieben haben - und ist es noch aktuell?"""
    if profile_live is None:
        profile_live = await client.quality_profiles()
    live = unsere_kopie(profile_live, eintrag_.profil_id_extern, profil.name)
    if live is None:
        return Abgleich(eintrag_.kennung, "fehlt", [Unterschied(art="fehlt")])

    plan = await plan_fuer(client, profil, umgebung)
    soll_neu = _gestalt(plan)
    ist = _gestalt_von_instanz(live, plan)
    unterschiede = _unterschiede(soll_neu, ist)

    # ⚠️ **Zuerst die einzige Frage, die den Nutzer angeht: Gibt es ueberhaupt
    # etwas zu tun?** Deckt sich die Kopie mit dem, was Nexview heute will, ist
    # sie aktuell - ganz gleich, wie sie dort hingekommen ist. Ohne diesen
    # Vorrang stand hier schon "Konflikt" ueber einer Tabelle, die "kein
    # Unterschied gefunden" meldete; der gespeicherte Abdruck war nur veraltet.
    if not unterschiede:
        return Abgleich(eintrag_.kennung, "aktuell", [])

    # Etwas weicht ab - der gespeicherte Abdruck sagt, wer sich bewegt hat.
    kopie_wie_geschrieben = _abdruck(
        {k: v for k, v in ist.items() if k != "fremd"}
    ) == eintrag_.fingerabdruck and not ist["fremd"]
    quelle_bewegt = _abdruck(soll_neu) != eintrag_.fingerabdruck

    if kopie_wie_geschrieben:
        stand = "update"
    elif not quelle_bewegt:
        stand = "angepasst"
    else:
        stand = "konflikt"
    return Abgleich(eintrag_.kennung, stand, unterschiede)


# -------------------------------------------------------------------- Schreiben


def _qualitaeten_bauen(schema: dict, merge: set[str]) -> list[dict]:
    """Die Qualitaetsliste des Profils aus dem Bauplan der Instanz.

    ⚠️ Radarr liefert WEB-Stufen bereits gebuendelt ("WEB 2160p"). Wer seine
    Merge-Gruppe danebenstellt, hat dieselbe Qualitaet zweimal im Profil und
    Radarr lehnt ab. Die eigene Gruppe **ersetzt** darum jeden Eintrag, der
    eine der gewuenschten Stufen enthaelt.
    """
    einzeln: list[dict] = []
    for eintrag_ in schema.get("items", []):
        kinder = [eintrag_] if "quality" in eintrag_ else eintrag_.get("items", [])
        for kind in kinder:
            if kind.get("quality", {}).get("name") in merge:
                einzeln.append(
                    {"quality": kind["quality"], "items": [], "allowed": True}
                )

    liste: list[dict] = []
    eingefuegt = False
    for eintrag_ in schema.get("items", []):
        kinder = [eintrag_] if "quality" in eintrag_ else eintrag_.get("items", [])
        namen = {k.get("quality", {}).get("name") for k in kinder}
        if namen & merge:
            if not eingefuegt:
                liste.append(
                    {
                        "id": GRUPPEN_NUMMER,
                        "name": "Nexview",
                        "allowed": True,
                        "items": einzeln,
                    }
                )
                eingefuegt = True
            continue
        eintrag_["allowed"] = False
        liste.append(eintrag_)
    return liste if eingefuegt else []


async def _alt_umbenennen(
    client: ArrClient, bestand: list[dict], plan: Bauplan
) -> None:
    """Altbestand mit dem alten Praefix auf den Originalnamen zurueckdrehen.

    ⚠️ **Umbenennen statt neu anlegen.** Ein zweites Muster mit demselben
    Inhalt anzulegen liesse die alten als Waisen zurueck - sie stuenden weiter
    in jeder Profilmaske herum, und in bereits umbenannten Dateien bliebe
    ``[NXV - German DL]`` stehen. Beim Umbenennen behaelt das Muster seine
    Nummer, die Profile zeigen weiter darauf, und der naechste Umbenenn-Lauf
    raeumt die Dateinamen mit auf.

    Wo der Originalname schon von einem fremden Muster belegt ist, bleibt alles
    wie es war: Ein fremdes Muster zu ueberschreiben ist keine Aufraeumarbeit.
    """
    if not ALTER_PRAEFIX:
        return
    belegt = {f.get("name") for f in bestand}
    gewollt = {w.name for w in plan.formate}
    for eintrag in bestand:
        name = str(eintrag.get("name") or "")
        if not name.startswith(ALTER_PRAEFIX):
            continue
        ohne = name[len(ALTER_PRAEFIX) :]
        if ohne not in gewollt or ohne in belegt:
            continue
        try:
            await client.custom_format_nachziehen(
                int(eintrag["id"]), {**eintrag, "name": ohne}
            )
        except ArrError as fehler:
            logger.info("Could not rename custom format %r: %s", name, fehler.message)
            continue
        belegt.add(ohne)
        logger.info("Custom format renamed: %r -> %r on %s", name, ohne, client.label)


@dataclass
class Praefixlage:
    """Wie viele Muster auf einer Instanz noch den alten Vorsatz tragen.

    ⚠️ **Warum das eine eigene Auskunft braucht.** Der Vorsatz ``NXV - `` ist
    nicht bloss haesslich: 22 der TRaSH-Muster tragen
    ``includeCustomFormatWhenRenaming``, fliessen also in den **Dateinamen**.
    Ein Bestandslauf in diesem Zustand schreibt ``[NXV - German DL]`` in jede
    Datei der Bibliothek.

    Der Zustandsabgleich meldet trotzdem "aktuell", weil er beide Schreibweisen
    als dasselbe Muster erkennt - das muss er, sonst laege der Verdacht beim
    Nutzer. Genau deshalb muss es *hier* auffallen, dicht am Umbenenn-Knopf.
    """

    #: Muster mit altem Vorsatz insgesamt.
    gesamt: int = 0
    #: Davon die, die im Dateinamen landen - die eigentliche Gefahr.
    im_dateinamen: int = 0
    #: Was im Dateinamen landet und sich **nicht** umbenennen laesst, weil ein
    #: fremdes Muster den schlichten Namen haelt.
    #:
    #: ⚠️ Bewusst nur die dateinamen-wirksamen: Ein blockiertes Muster, das
    #: ohnehin nie in einem Dateinamen auftaucht, ist kein Problem, und es
    #: mitzuzaehlen liesse die Lage schlimmer aussehen, als sie ist. Diese Zahl
    #: dagegen bleibt nach dem Aufraeumen stehen und braucht eine Entscheidung.
    blockiert: int = 0
    beispiele: list[str] = field(default_factory=list)
    #: Namen der blockierten - damit die Oberflaeche sie benennen kann.
    blockierte_namen: list[str] = field(default_factory=list)


def _praefix_lage_aus(bestand: list[dict]) -> Praefixlage:
    """Die Lage aus einer bereits geholten Musterliste - ohne Netzzugriff."""
    if not ALTER_PRAEFIX:
        return Praefixlage()
    belegt = {str(f.get("name") or "") for f in bestand}
    lage = Praefixlage()
    for eintrag in bestand:
        name = str(eintrag.get("name") or "")
        if not name.startswith(ALTER_PRAEFIX):
            continue
        lage.gesamt += 1
        if not eintrag.get("includeCustomFormatWhenRenaming"):
            # Taucht nie in einem Dateinamen auf - haesslich, aber harmlos.
            continue
        lage.im_dateinamen += 1
        if len(lage.beispiele) < 4:
            lage.beispiele.append(name)
        if name[len(ALTER_PRAEFIX) :] in belegt:
            lage.blockiert += 1
            if len(lage.blockierte_namen) < 6:
                lage.blockierte_namen.append(name)
    return lage


async def praefix_lage(client: ArrClient) -> Praefixlage:
    """Nachsehen, ob auf dieser Instanz noch alte Musternamen liegen."""
    try:
        return _praefix_lage_aus(await client.custom_formats())
    except ArrError as fehler:
        logger.info("Could not read custom formats of %s: %s", client.label, fehler.message)
        return Praefixlage()


async def praefix_aufraeumen(client: ArrClient) -> int:
    """**Alle** Muster mit altem Vorsatz auf den schlichten Namen zurueckdrehen.

    Der Unterschied zu ``_alt_umbenennen``: Das dort raeumt nur auf, was zum
    gerade geschriebenen Bauplan gehoert. Hier geht es um die ganze Instanz -
    denn im Dateinamen landet jedes Muster, das ein Film trifft, nicht nur die
    eines bestimmten Profils.

    ⚠️ Umbenannt wird, nicht neu angelegt: Das Muster behaelt seine Nummer, alle
    Profile zeigen weiter darauf. Wo der schlichte Name schon von einem fremden
    Muster belegt ist, bleibt alles, wie es war - fremde Muster zu ueberschreiben
    waere keine Aufraeumarbeit, sondern ein Eingriff.

    Gibt die Zahl der umbenannten Muster zurueck.
    """
    if not ALTER_PRAEFIX:
        return 0
    bestand = await client.custom_formats()
    belegt = {str(f.get("name") or "") for f in bestand}
    geaendert = 0
    for eintrag in bestand:
        name = str(eintrag.get("name") or "")
        if not name.startswith(ALTER_PRAEFIX):
            continue
        ohne = name[len(ALTER_PRAEFIX) :]
        if not ohne or ohne in belegt:
            continue
        try:
            await client.custom_format_nachziehen(
                int(eintrag["id"]), {**eintrag, "name": ohne}
            )
        except ArrError as fehler:
            logger.info("Could not rename custom format %r: %s", name, fehler.message)
            continue
        belegt.discard(name)
        belegt.add(ohne)
        geaendert += 1
        logger.info("Custom format renamed: %r -> %r on %s", name, ohne, client.label)
    return geaendert


def _regelform(spezifikationen: object) -> str:
    """Die Regeln eines Musters in eine vergleichbare Form bringen.

    Nummern und Reihenfolge sagen nichts - dieselbe Regel kann in beliebiger
    Ordnung stehen und traegt drueben eine andere ``id``. Verglichen wird
    deshalb nur, **was** geprueft wird.
    """
    if not isinstance(spezifikationen, list):
        return ""
    teile = []
    for spez in spezifikationen:
        if not isinstance(spez, dict):
            continue
        felder = spez.get("fields")
        if isinstance(felder, dict):
            felder = [{"name": k, "value": v} for k, v in felder.items()]
        werte = sorted(
            f"{f.get('name')}={f.get('value')}"
            for f in (felder or [])
            if isinstance(f, dict)
        )
        teile.append(
            f"{spez.get('implementation')}|{bool(spez.get('negate'))}"
            f"|{bool(spez.get('required'))}|{','.join(werte)}"
        )
    return ";".join(sorted(teile))


def _regeln_abweichend(vorhanden: dict | None, wunsch: object) -> bool:
    """Traegt ein vorhandenes Muster andere Regeln als der Bauplan will?"""
    if not vorhanden:
        return False
    soll = getattr(wunsch, "spezifikationen", None)
    if not soll:
        # Ohne eigene Regeln laesst sich nichts vergleichen - dann lieber
        # schweigen als etwas behaupten.
        return False
    return _regelform(vorhanden.get("specifications")) != _regelform(soll)


async def schreiben(
    client: ArrClient,
    plan: Bauplan,
    vorhandene_id: int | None = None,
    melden: "Fortschritt | None" = None,
) -> Schreibergebnis:
    """Den Bauplan auf einer Instanz umsetzen.

    Erst die Erkennungsmuster (vorhandene werden wiederverwendet, nicht
    verdoppelt), dann das Profil. Bei einer bekannten Nummer wird nachgezogen
    statt ein zweites anzulegen.

    ``melden`` wird unterwegs fortgeschrieben, damit die Oberflaeche zeigen
    kann, wie weit es ist.
    """
    if melden is not None:
        melden.instanz = client.label
        melden.schritt = "formate"
        melden.gesamt = len(plan.formate)
        melden.erledigt = 0

    bestand = await client.custom_formats()
    await _alt_umbenennen(client, bestand, plan)
    vorhandene_formate = {
        f["name"]: f["id"] for f in await client.custom_formats() if f.get("name")
    }
    nach_name = {str(f.get("name") or ""): f for f in await client.custom_formats()}
    nummern: dict[str, int] = {}
    fremde_regeln: list[str] = []
    neu = wieder = 0
    for wunsch in plan.formate:
        name = PRAEFIX + wunsch.name
        if name in vorhandene_formate:
            nummern[name] = vorhandene_formate[name]
            wieder += 1
            # ⚠️ **Wiederverwenden heisst nicht "geprueft".**
            #
            # Muster werden allein am **Namen** wiedererkannt - eine Nummer
            # merkt sich Nexview dafuer nicht. Findet es ein "German DL", nimmt
            # es das, ohne hineinzusehen. Stammt es von jemand anderem
            # (Recyclarr, ein aelterer TRaSH-Import, Handarbeit), koennen dahinter
            # ganz andere Regeln stehen - gemessen am 28.08.2026 war das bei
            # **allen 17** gleichnamigen Mustern der Fall.
            #
            # Das Profil verspricht dann "Deutsch Pflicht" und zeigt auf eine
            # Regel, die etwas anderes tut. Ueberschreiben waere falsch (es ist
            # nicht unseres), also bleibt: es benennen.
            if _regeln_abweichend(nach_name.get(name), wunsch):
                fremde_regeln.append(wunsch.name)
        else:
            angelegt = await client.custom_format_anlegen(
                {
                    "name": name,
                    "includeCustomFormatWhenRenaming": wunsch.beim_umbenennen,
                    "specifications": wunsch.spezifikationen,
                }
            )
            nummern[name] = int(angelegt["id"])
            neu += 1
        if melden is not None:
            melden.erledigt = neu + wieder

    if melden is not None:
        melden.schritt = "profil"
    schema = await client.quality_profile_schema()
    items = _qualitaeten_bauen(schema, set(plan.merge))
    if not items:
        raise ArrError(
            f"{client.label} kennt keine der gewuenschten Qualitaetsstufen.",
            code="arr_quality_unknown",
        )

    punkte = {nummern[PRAEFIX + w.name]: w.punkte for w in plan.formate}
    # Falle 3: alle Formate der Instanz auffuehren, nicht nur unsere.
    alle_formate = await client.custom_formats()
    format_items = [
        {"format": f["id"], "name": f["name"], "score": punkte.get(f["id"], 0)}
        for f in alle_formate
    ]

    payload: dict[str, Any] = {
        "name": plan.profilname,
        "upgradeAllowed": True,
        "cutoff": GRUPPEN_NUMMER,
        "minFormatScore": plan.min_punkte,
        "cutoffFormatScore": plan.schluss_punkte,
        "minUpgradeFormatScore": 1,
        "items": items,
        "language": {"id": -1, "name": "Any"},
        "formatItems": format_items,
    }

    if vorhandene_id:
        payload["id"] = vorhandene_id
        antwort = await client.quality_profile_nachziehen(vorhandene_id, payload)
    else:
        # ⚠️ **Vorher nachsehen, ob der Name drueben schon vergeben ist.**
        #
        # Radarr und Sonarr antworten darauf mit ``HTTP 409`` und einem
        # SQLite-Stacktrace ("UNIQUE constraint failed: QualityProfiles.Name").
        # Das ist fuer den Betreiber unbrauchbar: Er sieht "Fehler 409" und
        # weiss weder, welches Profil im Weg steht, noch dass es ueberhaupt um
        # einen Namen geht.
        #
        # Der haeufigste Weg dorthin ist harmlos und selbstgemacht: Ein in
        # Nexview geloeschtes Profil laesst seine Kopie drueben stehen. Wer es
        # danach neu anlegt, laeuft in genau diesen Fehler.
        # Die Probe darf das Schreiben nie verhindern: Antwortet die Instanz
        # hier nicht, wird trotzdem versucht - schlimmstenfalls kommt der
        # unverstaendliche 409, den es vorher immer gab.
        try:
            drueben = await client.quality_profiles()
        except Exception:  # noqa: BLE001
            drueben = []
        belegt = [str(p.get("name") or "") for p in drueben]

        # ⚠️ **Gleicher Name, gleicher Inhalt: dann ist es unseres.**
        #
        # Nexview merkt sich den Besitz allein in seiner eigenen Datenbank.
        # Wer Nexview neu aufsetzt und auf dasselbe Radarr zeigt - ohne
        # Sicherung -, steht vor seinen eigenen Profilen wie vor fremden: Die
        # Ablage ist leer, und ein Neuanlegen scheitert am Namen. Eine
        # Sackgasse, aus der nur Loeschen und Neuschreiben herausfuehrt.
        #
        # Der Bauplan sagt aber genau, wie das Profil aussehen muss. Deckt sich
        # ein vorhandenes damit **vollstaendig** - Qualitaeten, Muster, Punkte,
        # Schwellen -, dann ist es dieses Profil, ganz gleich wer es einmal
        # angelegt hat. Es zu uebernehmen legt nichts an und aendert nichts;
        # es traegt nur die Nummer nach.
        gleiches = next(
            (
                p
                for p in drueben
                if str(p.get("name") or "") == plan.profilname
                and not _unterschiede(_gestalt(plan), _gestalt_von_instanz(p, plan))
            ),
            None,
        )
        if gleiches is not None:
            logger.info(
                "Adopting identical profile %r (id %s) on %s instead of creating a copy",
                plan.profilname,
                gleiches.get("id"),
                client.label,
            )
            return Schreibergebnis(
                profil_id_extern=int(gleiches["id"]),
                fingerabdruck=_fingerabdruck(plan),
                trash_stand=plan.stand,
                formate_neu=neu,
                formate_wiederverwendet=wieder,
                hinweise=plan.hinweise,
            )

        if plan.profilname in belegt:
            raise ArrError(
                f"In {client.label} gibt es bereits ein Profil namens "
                f"„{plan.profilname}“. Nexview ueberschreibt keine "
                "fremden Profile - benenne das Profil um oder raeume das "
                "andere drueben weg.",
                409,
                code="quality_profile_name_taken",
                service=client.label,
                name=plan.profilname,
            )
        antwort = await client.quality_profile_anlegen(payload)

    logger.info(
        "Quality profile %r written to %s (%d formats added, %d reused)",
        plan.profilname,
        client.label,
        neu,
        wieder,
    )
    return Schreibergebnis(
        profil_id_extern=int(antwort["id"]),
        fingerabdruck=_fingerabdruck(plan),
        trash_stand=plan.stand,
        formate_neu=neu,
        formate_wiederverwendet=wieder,
        # ⚠️ Fremde Regeln zuerst: Sie betreffen, ob das Profil ueberhaupt tut,
        # was sein Name verspricht - das wiegt schwerer als die Hinweise aus
        # dem Bauplan.
        hinweise=(
            (
                "fremde_regeln:"
                + ", ".join(sorted(fremde_regeln)[:6])
                + (f" (+{len(fremde_regeln) - 6})" if len(fremde_regeln) > 6 else ""),
            )
            if fremde_regeln
            else ()
        )
        + plan.hinweise,
    )


def _qualitaetsnamen(schema: dict) -> set[str]:
    """Alle Stufen, die diese Instanz kennt - auch die in Gruppen."""
    namen: set[str] = set()
    for eintrag in schema.get("items", []):
        kinder = [eintrag] if "quality" in eintrag else eintrag.get("items", [])
        for kind in kinder:
            name = kind.get("quality", {}).get("name")
            if name:
                namen.add(name)
    return namen


@dataclass(frozen=True)
class Umgebung:
    """Was eine Instanz ueber sich sagt - je Instanz einmal, nicht je Profil."""

    sprachnummern: dict[str, int]
    qualitaeten: set[str]


async def umgebung_von(client: ArrClient) -> Umgebung:
    """Sprachen und Qualitaetsstufen dieser Instanz.

    Beides wird gefragt statt angenommen: Die Sprachnummern unterscheiden sich
    zwischen Fassungen, und welche Qualitaetsstufen es gibt, weiss nur die
    Instanz selbst.
    """
    from .trash import SPRACHNAMEN

    sprachen = {
        eintrag_["name"]: int(eintrag_["id"])
        for eintrag_ in await client.sprachen()
        if isinstance(eintrag_, dict) and eintrag_.get("name")
    }
    schema = await client.quality_profile_schema()
    return Umgebung(
        sprachnummern={
            code: sprachen[name]
            for code, name in SPRACHNAMEN.items()
            if name in sprachen
        },
        qualitaeten=_qualitaetsnamen(schema),
    )


async def plan_fuer(
    client: ArrClient, profil: Qualitaetsprofil, umgebung: Umgebung | None = None
) -> Bauplan:
    """Den Bauplan bauen - mit Sprachen und Qualitaeten **dieser** Instanz.

    ``umgebung`` durchreichen, wenn mehrere Profile derselben Instanz gebaut
    werden: Sonst werden Sprachliste und Qualitaetsplan je Profil erneut
    geholt, obwohl sie sich zwischendurch nicht aendern.
    """
    if umgebung is None:
        umgebung = await umgebung_von(client)
    rezept = dict(profil.rezept or {})
    # ⚠️ **Der Name kommt aus der Ablage, nicht aus dem Rezept.**
    #
    # ``bauplan`` nimmt ihn aus ``rezept["name"]`` und faellt sonst auf den
    # Dateinamen der TRaSH-Vorlage zurueck ("german-hd-remux-web"). Damit gab
    # es zwei Quellen fuer dieselbe Sache - und wenn sie auseinanderliefen,
    # stand in Radarr ein Name, den niemand vergeben hatte.
    #
    # Schlimmer noch: Mehrere Rezepte zeigen auf dieselbe Vorlage. Zwei Profile
    # ohne eigenen Namen bekamen also **denselben**, und das zweite scheiterte
    # an Radarrs Eindeutigkeit - mit "HTTP 409" und ohne jeden Hinweis worauf.
    # Massgeblich ist, was in der Ablage steht: Das hat der Betreiber getippt,
    # und das zeigt ihm die Liste.
    if profil.name:
        rezept["name"] = profil.name
    return bauplan(
        rezept,
        profil.dienst,
        umgebung.sprachnummern,
        umgebung.qualitaeten,
    )


def merken(
    db: Session,
    profil: Qualitaetsprofil,
    kennung: str,
    ergebnis: Schreibergebnis,
) -> QualitaetsprofilInstallation:
    """Festhalten, was wo in welchem Stand liegt - das Besitzbuch."""
    eintrag_ = installation(db, profil.id, kennung)
    if eintrag_ is None:
        eintrag_ = QualitaetsprofilInstallation(profil_id=profil.id, kennung=kennung)
        db.add(eintrag_)
    eintrag_.profil_id_extern = ergebnis.profil_id_extern
    eintrag_.fingerabdruck = ergebnis.fingerabdruck
    eintrag_.trash_stand = ergebnis.trash_stand
    eintrag_.geschrieben_am = utcnow()
    profil.aktualisiert_am = utcnow()
    return eintrag_


def vergessen(db: Session, profil: Qualitaetsprofil, kennung: str) -> bool:
    """Nexview verwaltet das Profil dort nicht mehr - die Kopie bleibt stehen."""
    eintrag_ = installation(db, profil.id, kennung)
    if eintrag_ is None:
        return False
    db.delete(eintrag_)
    return True
