"""Was auf einer Instanz liegt - und was daran haengt.

⚠️ **Warum Nexview auch das Fremde zeigt.** Bisher zeigte es nur, was es selbst
angelegt hat. Das ist ehrlich, aber unbrauchbar, sobald es zu Reibung kommt:
Ein Muster, das den Namen belegt, den Nexview braucht, ist unsichtbar; ein
Profil, das ein Loeschen blockiert, auch. Der Betreiber sah einen Fehler und
musste in Radarr selbst suchen - genau die Fleissarbeit, die Nexview abnehmen
soll.

⚠️ **Die eigentliche Leistung ist die Auskunft, nicht das Loeschen.** Radarr
lehnt das Loeschen eines benutzten Profils mit ``HTTP 500`` ab und sagt nicht,
**wer** es benutzt - gemessen am 27.08.2026, und die Ursache war eine
*Sammlung*, an die niemand gedacht hatte. Nexview kann alle drei Quellen
zusammentragen: Medien, Importlisten, Sammlungen.

⚠️ **Was "unseres" heisst - und was nicht.** Bei Profilen ist es beantwortbar:
Das Besitzbuch nennt die Nummer, und der Name muss dazu passen. Bei
Erkennungsmustern ist es das **nicht**: Nexview merkt sich fuer sie keine
Nummer, und seit der Praefix entfallen ist, tragen sie keine Handschrift mehr.
Deshalb wird bei Mustern nicht "gehoert uns" behauptet, sondern die Frage
beantwortet, die wirklich zaehlt: **Benutzt sie noch jemand?**
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .arr import ArrClient, ArrError
from .qualitaetsprofile import ALTER_PRAEFIX

logger = logging.getLogger("nexview.qualitaet")


@dataclass
class ProfilBestand:
    """Ein Qualitaetsprofil auf der Instanz - mit allem, was daran haengt."""

    id: int
    name: str
    #: Hat Nexview dieses Profil angelegt? (Besitzbuch + Namensgleichheit)
    unser: bool = False
    medien: int = 0
    importlisten: int = 0
    sammlungen: int = 0

    @property
    def loeschbar(self) -> bool:
        return not (self.medien or self.importlisten or self.sammlungen)

    def grund(self) -> str:
        """Warum es nicht geloescht werden kann - leer heisst: kann es."""
        teile = []
        if self.medien:
            teile.append(f"medien:{self.medien}")
        if self.importlisten:
            teile.append(f"importlisten:{self.importlisten}")
        if self.sammlungen:
            teile.append(f"sammlungen:{self.sammlungen}")
        return ",".join(teile)


@dataclass
class MusterBestand:
    """Ein Erkennungsmuster - und welche Profile ihm Punkte geben."""

    id: int
    name: str
    #: Profile, die diesem Muster Punkte != 0 geben.
    #:
    #: ⚠️ Punkte **ungleich null** ist das Kriterium, nicht "kommt vor":
    #: ``formatItems`` fuehrt jedes Muster der Instanz in jedem Profil auf, die
    #: meisten mit 0. Wer das als Benutzung zaehlt, haelt alles fuer in Gebrauch.
    benutzt_von: list[str] = field(default_factory=list)
    #: Gehoert es zum Bauplan eines hier liegenden Nexview-Profils?
    #:
    #: ⚠️ **Diese Muster sind nicht "ungenutzt", auch wenn sie 0 Punkte
    #: tragen.** Ein Bauplan bringt Muster mit, die er bewusst mit null
    #: bewertet - Streaming-Kennungen etwa, oder einzelne Sprachen, wenn
    #: stattdessen ein zusammengesetztes Muster zaehlt. Sie zu loeschen ist
    #: vergebliche Arbeit: Das naechste Verteilen legt sie wieder an. Wer das
    #: nicht unterscheidet, schickt den Betreiber in eine Schleife - genau so
    #: aufgefallen am 29.08.2026.
    gehoert_zu_plan: bool = False
    #: Traegt es noch den alten Nexview-Vorsatz?
    alter_vorsatz: bool = False
    #: Landet es im Dateinamen?
    im_dateinamen: bool = False

    @property
    def loeschbar(self) -> bool:
        return not self.benutzt_von and not self.gehoert_zu_plan


@dataclass
class Bestand:
    kennung: str
    name: str
    erreichbar: bool = True
    profile: list[ProfilBestand] = field(default_factory=list)
    muster: list[MusterBestand] = field(default_factory=list)


async def aufnehmen(
    client: ArrClient,
    kennung: str,
    dienst: str,
    unsere_nummern: dict[int, str],
    plan_muster: set[str] | None = None,
) -> Bestand:
    """Den vollstaendigen Bestand einer Instanz aufnehmen.

    ``plan_muster`` sind die Musternamen, die zu den Bauplaenen der hier
    liegenden Nexview-Profile gehoeren. Sie gelten nicht als ungenutzt, auch
    wenn kein Profil ihnen Punkte gibt - siehe ``MusterBestand.gehoert_zu_plan``.

    ``unsere_nummern`` bildet Radarr-Profilnummer auf den Nexview-Namen ab -
    daraus ergibt sich ``unser``. Die Nummer allein genuegt dafuer nicht (siehe
    ``qualitaetsprofile.unsere_kopie``): Nach dem Einspielen einer Sicherung auf
    einer anderen Instanz zeigt sie moeglicherweise auf ein fremdes Profil.
    """
    bestand = Bestand(kennung=kennung, name=client.label)
    art = "/movie" if dienst == "radarr" else "/series"

    try:
        profile = await client.quality_profiles()
        formate = await client.custom_formats()
        medien = await client.get(art) or []
    except ArrError as fehler:
        logger.info("Inventory of %s not readable: %s", kennung, fehler.message)
        bestand.erreichbar = False
        return bestand

    # ⚠️ Diese beiden duerfen fehlen: Sonarr kennt keine Sammlungen, und aeltere
    # Fassungen antworten auf ``/importlist`` womoeglich nicht. Ein Fehler hier
    # darf die ganze Aufnahme nicht kippen - dann fehlte lieber eine Zahl als
    # die ganze Seite.
    listen = await _weich(client, "/importlist")
    sammlungen = await _weich(client, "/collection") if dienst == "radarr" else []

    for profil in profile:
        nummer = int(profil.get("id") or 0)
        name = str(profil.get("name") or "")
        bestand.profile.append(
            ProfilBestand(
                id=nummer,
                name=name,
                unser=unsere_nummern.get(nummer) == name,
                medien=sum(1 for m in medien if m.get("qualityProfileId") == nummer),
                importlisten=sum(
                    1 for l in listen if l.get("qualityProfileId") == nummer
                ),
                sammlungen=sum(
                    1 for s in sammlungen if s.get("qualityProfileId") == nummer
                ),
            )
        )

    benutzt_von: dict[str, list[str]] = {}
    for profil in profile:
        for eintrag in profil.get("formatItems", []):
            if eintrag.get("score"):
                benutzt_von.setdefault(str(eintrag.get("name") or ""), []).append(
                    str(profil.get("name") or "")
                )

    zum_plan = plan_muster or set()
    for muster in formate:
        name = str(muster.get("name") or "")
        bestand.muster.append(
            MusterBestand(
                id=int(muster.get("id") or 0),
                name=name,
                benutzt_von=sorted(benutzt_von.get(name, [])),
                gehoert_zu_plan=name in zum_plan,
                alter_vorsatz=bool(ALTER_PRAEFIX and name.startswith(ALTER_PRAEFIX)),
                im_dateinamen=bool(muster.get("includeCustomFormatWhenRenaming")),
            )
        )
    # ⚠️ **Das Loeschbare zuerst.** Dieser Bereich ist zum Aufraeumen da, und
    # gehandelt wird auf das, was weg kann. Andersherum sortiert stand das
    # Handelbare unter hundert gebundenen Eintraegen und man musste danach
    # suchen - bei 186 Mustern eine halbe Bildschirmseite Scrollen.
    #
    # Bei den Profilen bleibt es umgekehrt: Dort ist die Zahl der Medien die
    # eigentliche Auskunft ("was haengt hier dran"), und ein Profil mit 3929
    # Filmen gehoert nicht ans Ende.
    bestand.profile.sort(key=lambda p: (-p.medien, p.name.lower()))
    bestand.muster.sort(key=lambda m: (bool(m.benutzt_von), m.name.lower()))
    return bestand


async def _weich(client: ArrClient, pfad: str) -> list[dict]:
    try:
        return await client.get(pfad) or []
    except ArrError:
        return []


@dataclass
class Aufraeumergebnis:
    geloescht_profile: list[str] = field(default_factory=list)
    geloescht_muster: list[str] = field(default_factory=list)
    #: Was abgelehnt wurde, mit Grund - ``name: grund``.
    abgelehnt: dict[str, str] = field(default_factory=dict)


async def aufraeumen(
    client: ArrClient,
    bestand: Bestand,
    profil_ids: list[int],
    muster_ids: list[int],
) -> Aufraeumergebnis:
    """Ausgewaehlte Profile und Muster loeschen - jedes einzeln geprueft.

    ⚠️ **Die Pruefung findet hier statt, nicht in der Oberflaeche.** Was der
    Browser vor einer Minute angezeigt hat, muss nicht mehr gelten: Ein Film
    kann inzwischen auf das Profil gezogen worden sein. Geprueft wird gegen den
    Bestand, der unmittelbar vorher aufgenommen wurde.

    ⚠️ **Reihenfolge: erst Profile, dann Muster.** Ein Muster gilt als benutzt,
    solange ein Profil ihm Punkte gibt. Wer die Profile zuerst wegnimmt, kann
    danach auch die Muster loeschen, die nur an ihnen hingen - andersherum
    blieben sie liegen und der Betreiber muesste zweimal aufraeumen.
    """
    ergebnis = Aufraeumergebnis()
    nach_id = {p.id: p for p in bestand.profile}

    for nummer in profil_ids:
        profil = nach_id.get(nummer)
        if profil is None:
            ergebnis.abgelehnt[str(nummer)] = "unbekannt"
            continue
        if not profil.loeschbar:
            ergebnis.abgelehnt[profil.name] = profil.grund()
            continue
        try:
            await client.quality_profile_loeschen(nummer)
            ergebnis.geloescht_profile.append(profil.name)
            logger.info("Quality profile %r deleted on %s", profil.name, client.label)
        except ArrError as fehler:
            # ⚠️ Radarr weiss manchmal mehr als wir - etwa eine Bindung, die
            # ueber keine der drei Listen sichtbar ist. Dann gilt seine Antwort.
            logger.info(
                "Instance refused to delete profile %r: %s", profil.name, fehler.message
            )
            ergebnis.abgelehnt[profil.name] = "instanz_verweigert"

    if not muster_ids:
        return ergebnis

    # Nach dem Loeschen der Profile neu nachsehen, welche Muster noch benutzt
    # werden - sonst richtete sich die Pruefung nach einem ueberholten Stand.
    noch_benutzt: set[str] = set()
    try:
        for profil in await client.quality_profiles():
            for eintrag in profil.get("formatItems", []):
                if eintrag.get("score"):
                    noch_benutzt.add(str(eintrag.get("name") or ""))
    except ArrError:
        noch_benutzt = {m.name for m in bestand.muster if m.benutzt_von}

    muster_nach_id = {m.id: m for m in bestand.muster}
    for nummer in muster_ids:
        muster = muster_nach_id.get(nummer)
        if muster is None:
            ergebnis.abgelehnt[str(nummer)] = "unbekannt"
            continue
        if muster.name in noch_benutzt:
            ergebnis.abgelehnt[muster.name] = "in_gebrauch"
            continue
        if muster.gehoert_zu_plan:
            # ⚠️ Auch hier, nicht nur in der Anzeige: Sonst loescht ein
            # veralteter Browser-Stand etwas, das gleich wiederkommt.
            ergebnis.abgelehnt[muster.name] = "gehoert_zu_profil"
            continue
        try:
            await client.custom_format_loeschen(nummer)
            ergebnis.geloescht_muster.append(muster.name)
        except ArrError as fehler:
            logger.info(
                "Instance refused to delete format %r: %s", muster.name, fehler.message
            )
            ergebnis.abgelehnt[muster.name] = "instanz_verweigert"
    if ergebnis.geloescht_muster:
        logger.info(
            "%d custom format(s) deleted on %s",
            len(ergebnis.geloescht_muster),
            client.label,
        )
    return ergebnis


#: Wie die Medien je Dienst heissen - fuer Liste und Sammel-Bearbeitung.
MEDIEN = {
    "radarr": {"liste": "/movie", "editor": "/movie/editor", "feld": "movieIds"},
    "sonarr": {"liste": "/series", "editor": "/series/editor", "feld": "seriesIds"},
}

#: Wie viele Titel je Aufruf umgehaengt werden.
#:
#: ⚠️ Nicht alle auf einmal: Bei mehreren tausend Titeln waere das eine sehr
#: lange offene Anfrage, und ein Abbruch mittendrin liesse den Betreiber im
#: Unklaren, wie viele schon umgehaengt sind.
UMHAENGE_HAEPPCHEN = 200


@dataclass
class Umhaengergebnis:
    umgehaengt: int = 0
    grund: str = ""


async def umhaengen(
    client: ArrClient, dienst: str, von: int, nach: int
) -> Umhaengergebnis:
    """Alle Medien eines Profils auf ein anderes umhaengen.

    ⚠️ **Das fehlende Stueck, um ueberhaupt aufraeumen zu koennen.** Ein Profil
    laesst sich nicht loeschen, solange Medien darauf liegen - und in einer
    gewachsenen Anlage liegt fast alles auf Profilen, die vor Nexview da waren.
    Ohne diesen Weg endet das Aufraeumen dort, wo es anfangen muesste.

    ⚠️ **``moveFiles: False`` ist Absicht.** Es geht um die Zuordnung, nicht um
    die Platte: Die Dateien bleiben, wo sie sind. Alles andere waere ein
    Verschieben von Terabytes, und danach hat niemand gefragt.

    ⚠️ **Was danach passiert, gehoert dazugesagt.** Das neue Profil bewertet
    anders. Titel, deren vorhandene Datei darunter liegt, werden von der
    Instanz zur Aufwertung vorgemerkt - das kann Downloads ausloesen. Diese
    Folge steht in der Oberflaeche, bevor jemand klickt.
    """
    art = MEDIEN.get(dienst)
    if art is None:
        return Umhaengergebnis(grund="unbekannter_dienst")

    try:
        medien = await client.get(art["liste"]) or []
    except ArrError as fehler:
        logger.info("Could not list media on %s: %s", client.label, fehler.message)
        return Umhaengergebnis(grund="unerreichbar")

    # ⚠️ ``is not None`` statt Wahrheitswert: Die Nummer **0** ist eine gueltige
    # Nummer und faellt sonst stillschweigend heraus - ein Titel bliebe zurueck,
    # das alte Profil waere weiter gebunden, und niemand wuesste warum.
    nummern = [
        int(m["id"]) for m in medien
        if m.get("qualityProfileId") == von and m.get("id") is not None
    ]
    if not nummern:
        return Umhaengergebnis(umgehaengt=0)

    umgehaengt = 0
    for start in range(0, len(nummern), UMHAENGE_HAEPPCHEN):
        haeppchen = nummern[start : start + UMHAENGE_HAEPPCHEN]
        try:
            await client.put(
                art["editor"],
                {art["feld"]: haeppchen, "qualityProfileId": nach, "moveFiles": False},
            )
        except ArrError as fehler:
            # ⚠️ Was schon umgehaengt ist, bleibt es - und die Zahl sagt die
            # Wahrheit. Ein "alles oder nichts" gibt es hier nicht, also wird
            # auch keines vorgetaeuscht.
            logger.info(
                "Moving titles on %s failed after %d: %s",
                client.label, umgehaengt, fehler.message,
            )
            return Umhaengergebnis(umgehaengt=umgehaengt, grund="abgebrochen")
        umgehaengt += len(haeppchen)

    logger.info(
        "Moved %d title(s) from profile %d to %d on %s",
        umgehaengt, von, nach, client.label,
    )
    return Umhaengergebnis(umgehaengt=umgehaengt)
