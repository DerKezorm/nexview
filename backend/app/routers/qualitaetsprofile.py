"""Qualitaetsprofile: ablegen, verteilen, wieder loswerden.

⚠️ **Durchgehend admin-only** - deshalb steht ``AdminUser`` an jedem Endpunkt
und nicht einmal am Router. Wer den Schutz an einer Stelle vergisst, faellt so
beim Lesen auf; am ``include_router`` sieht man ihn hier nicht mehr.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import meldungen
from ..deps import AdminUser, DbSession
from ..models import Qualitaetsprofil
from ..services import arr_bestand
from ..services import benennung as benennung_dienst
from ..services import medienserver_verbindung as mediaserver_verbindung
from ..services import qualitaetsprofile as dienst
from ..services import trash_bezug as bezug
from ..services.arr import ArrClient, ArrError
from ..services.settings_service import load_settings
from ..services.trash import TrashFehler, schnappschuss

logger = logging.getLogger("nexview.qualitaet")

router = APIRouter(prefix="/api/settings/qualitaetsprofile", tags=["qualitaetsprofile"])


class InstallationOut(BaseModel):
    kennung: str
    geschrieben_am: str | None = None
    trash_stand: str = ""


class ProfilOut(BaseModel):
    id: int
    name: str
    dienst: str
    rezept: dict
    installationen: list[InstallationOut]


class ProfilIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dienst: Literal["radarr", "sonarr"]
    rezept: dict


class VerteilenIn(BaseModel):
    """Wo das Profil kuenftig liegen soll - die Liste ist die Wahrheit.

    Instanzen, die fehlen, werden nicht mehr verwaltet; ihre Kopie bleibt aber
    in Radarr stehen.
    """

    kennungen: list[str] = Field(default_factory=list)


class VerteilenOut(BaseModel):
    installationen: list[InstallationOut]
    formate_neu: int = 0
    formate_wiederverwendet: int = 0
    hinweise: list[str] = Field(default_factory=list)


class SchnappschussOut(BaseModel):
    """Welcher TRaSH-Stand gilt - und ob es einen neueren gibt."""

    stand: str
    quelle: str
    lizenz: str
    commit: str = ""
    #: True, solange nur der mit Nexview ausgelieferte Stand da ist.
    mitgeliefert: bool = True
    geholt_am: str = ""
    #: Hat das taegliche Nachsehen schon etwas ergeben?
    pruefung_bekannt: bool = False
    neuer_stand_da: bool = False
    neuer_stand_datum: str = ""


class AktualisierenOut(BaseModel):
    """Der neue Stand liegt jetzt in Nexview - und sonst nirgends.

    ⚠️ Hier stand einmal ein Feld "betroffene Installationen". Es zaehlte
    schlicht **alle** und behauptete damit etwas, das es nicht wusste. Welche
    Kopien wirklich hinterherhinken, sagt der Abgleich - er wird nach dem
    Uebernehmen ohnehin neu geholt.
    """

    stand: str
    commit: str
    geholt_am: str


class FortschrittOut(BaseModel):
    """Wo das Schreiben steht - ``laeuft: false`` heisst: gerade nichts zu tun."""

    laeuft: bool = False
    instanz: str = ""
    schritt: str = ""
    erledigt: int = 0
    gesamt: int = 0
    instanz_nummer: int = 1
    von_instanzen: int = 1


def _hinaus(profil: Qualitaetsprofil) -> ProfilOut:
    return ProfilOut(
        id=profil.id,
        name=profil.name,
        dienst=profil.dienst,
        rezept=dict(profil.rezept or {}),
        installationen=[
            InstallationOut(
                kennung=i.kennung,
                geschrieben_am=i.geschrieben_am.isoformat() if i.geschrieben_am else None,
                trash_stand=i.trash_stand,
            )
            for i in sorted(profil.installationen, key=lambda x: x.kennung)
        ],
    )


@router.get("/quelle", response_model=SchnappschussOut)
def quelle(admin: AdminUser) -> SchnappschussOut:
    daten = schnappschuss("radarr")
    woher = bezug.herkunft()
    geprueft = bezug.neues_bekannt()
    return SchnappschussOut(
        stand=daten["stand"],
        quelle=daten["quelle"],
        lizenz=daten["lizenz"],
        commit=woher.commit,
        mitgeliefert=woher.mitgeliefert,
        geholt_am=woher.geholt_am,
        pruefung_bekannt=bool(geprueft["bekannt"]),
        neuer_stand_da=bool(geprueft["vorhanden"]),
        neuer_stand_datum=str(geprueft["datum"]),
    )


@router.post("/quelle/aktualisieren", response_model=AktualisierenOut)
async def aktualisieren(admin: AdminUser, db: DbSession) -> AktualisierenOut:
    """Den aktuellen Stand der TRaSH-Guides holen und uebernehmen.

    ⚠️ **Aendert von sich aus nichts in Radarr oder Sonarr.** Danach zeigt der
    Abgleich, welche Kopien nicht mehr dem neuen Stand entsprechen; geschrieben
    wird erst auf Klick. Ein Stand, mit dem sich ein vorhandenes Profil nicht
    mehr bauen liesse, wird abgelehnt statt uebernommen.
    """
    profile = dienst.alle(db)
    rezepte = [(p.dienst, dict(p.rezept or {})) for p in profile]
    try:
        neu = await bezug.holen_und_pruefen(rezepte)
    except bezug.BezugFehler as fehler:
        logger.info("Fetching a new TRaSH state failed: %s", fehler.meldung)
        raise meldungen.fehler(fehler.code, fehler.meldung, 502) from fehler
    return AktualisierenOut(
        stand=neu.commit_datum[:10], commit=neu.commit, geholt_am=neu.geholt_am
    )


@router.get("", response_model=list[ProfilOut])
def alle(admin: AdminUser, db: DbSession) -> list[ProfilOut]:
    return [_hinaus(p) for p in dienst.alle(db)]


@router.post("", response_model=ProfilOut, status_code=status.HTTP_201_CREATED)
def anlegen(payload: ProfilIn, admin: AdminUser, db: DbSession) -> ProfilOut:
    profil = dienst.anlegen(db, payload.name, payload.dienst, payload.rezept)
    db.commit()
    db.refresh(profil)
    return _hinaus(profil)


class UnterschiedOut(BaseModel):
    art: str
    was: str = ""
    ist: str = ""
    soll: str = ""


class AbgleichOut(BaseModel):
    profil_id: int
    kennung: str
    #: "aktuell" | "update" | "angepasst" | "konflikt" | "fehlt" | "unerreichbar"
    stand: str
    unterschiede: list[UnterschiedOut] = Field(default_factory=list)


@router.get("/abgleich", response_model=list[AbgleichOut])
async def abgleich(admin: AdminUser, db: DbSession) -> list[AbgleichOut]:
    """Steht auf den Instanzen noch, was Nexview geschrieben hat?

    Getrennt von der Liste, weil dafuer jede Instanz gefragt werden muss - die
    Liste selbst soll sofort da sein, auch wenn eine Instanz gerade stumm ist.
    Je Instanz wird nur **einmal** gefragt und dann gegen alle dort liegenden
    Profile verglichen.
    """
    settings = load_settings(db)
    instanzen = {i.kennung: i for i in settings.arr_instanzen()}
    profile = dienst.alle(db)

    nach_instanz: dict[str, list[tuple[Qualitaetsprofil, object]]] = {}
    for profil in profile:
        for eintrag_ in profil.installationen:
            nach_instanz.setdefault(eintrag_.kennung, []).append((profil, eintrag_))

    ergebnis: list[AbgleichOut] = []
    for kennung, paare in nach_instanz.items():
        instanz = instanzen.get(kennung)
        if instanz is None:
            for profil, _ in paare:
                ergebnis.append(
                    AbgleichOut(profil_id=profil.id, kennung=kennung, stand="fehlt")
                )
            continue
        client = ArrClient(instanz.url, instanz.api_key, instanz.name)
        try:
            live = await client.quality_profiles()
            # Einmal je Instanz, nicht je Profil - sonst holt jeder Vergleich
            # Sprachliste und Qualitaetsplan erneut.
            umgebung = await dienst.umgebung_von(client)
        except ArrError as fehler:
            # Eine stumme Instanz darf die Auskunft ueber die anderen nicht
            # verhindern - sie bekommt ihren eigenen Stand.
            logger.info("Instance %s not reachable for comparison: %s", kennung, fehler.message)
            for profil, _ in paare:
                ergebnis.append(
                    AbgleichOut(
                        profil_id=profil.id, kennung=kennung, stand="unerreichbar"
                    )
                )
            continue
        for profil, eintrag_ in paare:
            try:
                stand = await dienst.vergleichen(
                    client, profil, eintrag_, live, umgebung
                )
            except (ArrError, TrashFehler) as fehler:
                logger.info("Comparison for profile %s failed: %s", profil.id, fehler)
                ergebnis.append(
                    AbgleichOut(
                        profil_id=profil.id, kennung=kennung, stand="unerreichbar"
                    )
                )
                continue
            ergebnis.append(
                AbgleichOut(
                    profil_id=profil.id,
                    kennung=stand.kennung,
                    stand=stand.stand,
                    unterschiede=[
                        UnterschiedOut(art=u.art, was=u.was, ist=u.ist, soll=u.soll)
                        for u in stand.unterschiede
                    ],
                )
            )
    return ergebnis


class AltnamenOut(BaseModel):
    """Muster mit altem Vorsatz - die Voraussetzung fuer einen Bestandslauf.

    ⚠️ ``im_dateinamen`` ist die Zahl, auf die es ankommt: So viele Muster
    fliessen in Dateinamen ein und wuerden ``[NXV - …]`` hineinschreiben.
    """

    gesamt: int = 0
    im_dateinamen: int = 0
    #: Davon die, die sich nicht umbenennen lassen - sie brauchen eine
    #: Entscheidung und bleiben nach dem Aufraeumen stehen.
    blockiert: int = 0
    beispiele: list[str] = Field(default_factory=list)
    blockierte_namen: list[str] = Field(default_factory=list)


class AufraeumOut(BaseModel):
    umbenannt: int = 0
    altnamen: AltnamenOut = Field(default_factory=AltnamenOut)


class BenennungOut(BaseModel):
    """Wie eine Instanz benennt - und was TRaSH empfiehlt."""

    kennung: str
    name: str
    dienst: str
    umbenennen_an: bool
    datei_ist: str = ""
    datei_soll: str = ""
    ordner_ist: str = ""
    ordner_soll: str = ""
    fassung: str = "default"
    erreichbar: bool = True
    #: Sagt diese Instanz dem Medienserver Bescheid, wenn sich etwas aendert?
    meldet_medienserver: bool = True
    altnamen: AltnamenOut = Field(default_factory=AltnamenOut)
    #: Laeuft gerade ein Bestandslauf auf dieser Instanz?
    #:
    #: ⚠️ Gehoert in **diese** Liste, nicht nur in den Fortschritts-Endpunkt:
    #: Sonst findet nur die Sitzung den Lauf wieder, die ihn angestossen hat,
    #: und wer die Seite neu laedt, sieht nichts mehr - waehrend im Hintergrund
    #: tausende Dateien umbenannt werden.
    lauf_offen: bool = False


class BenennungIn(BaseModel):
    kennung: str
    #: Was uebernommen werden soll - beides einzeln, weil die Folgen sich
    #: unterscheiden: Dateinamen sind harmlos, Ordnernamen nicht.
    datei: bool = False
    ordner: bool = False
    #: ⚠️ Den vorhandenen Bestand mit angleichen. Der einzige Teil, der
    #: Dateien auf der Platte anfasst - deshalb ein eigener Haken.
    bestand: bool = False


class UmbenennenOut(BaseModel):
    """Wie weit das Umbenennen ist - ``laeuft: false`` heisst: nichts zu tun."""

    laeuft: bool = False
    instanz: str = ""
    schritt: str = ""
    erledigt: int = 0
    gesamt: int = 0
    betroffen: int = 0
    beispiele: list[str] = Field(default_factory=list)
    #: Nach einem Abbruch wieder aufgenommen? Ohne diese Auskunft wirkte ein
    #: Lauf, der nach einem Neustart von selbst weiterlaeuft, wie ein Fehler.
    fortgesetzt: bool = False


class MedienserverOut(BaseModel):
    """Ein Medienserver und der Zugang, den Radarr/Sonarr dafuer braucht."""

    id: int
    provider: str
    name: str
    url: str
    #: Braucht dieser Anbieter einen API-Schluessel vom Betreiber?
    braucht_schluessel: bool
    #: Liegt er vor? Der Schluessel selbst wird nie ausgeliefert.
    schluessel_da: bool


class ZuordnungOut(BaseModel):
    """Wie Pfade umgeschrieben werden - die Vorschau vor dem Verbinden."""

    von: str = ""
    nach: str = ""
    hindernis: str = ""
    beispiel_arr: str = ""
    beispiel_server: str = ""


class LueckeOut(BaseModel):
    provider: str
    name: str
    url: str
    selbst_moeglich: bool = False
    hindernis: str = ""
    zuordnung: ZuordnungOut = Field(default_factory=ZuordnungOut)


class VerbindungslageOut(BaseModel):
    kennung: str
    name: str
    erreichbar: bool = True
    fehlend: list[LueckeOut] = Field(default_factory=list)
    #: Anbieter, zu denen eine funktionierende Verbindung besteht.
    #:
    #: ⚠️ Gehoert sichtbar in die Oberflaeche: Sonst zeigt eine halb
    #: verbundene Instanz nur ihre Luecken, und das Bestehende sieht aus, als
    #: fehlte es auch.
    verbunden: list[str] = Field(default_factory=list)


class WarnungOut(BaseModel):
    """Eine bestehende Verbindung, die nicht mehr tut, was sie soll."""

    instanz: str
    provider: str
    grund: str


class VerbindungslageGesamt(BaseModel):
    server: list[MedienserverOut] = Field(default_factory=list)
    instanzen: list[VerbindungslageOut] = Field(default_factory=list)
    #: ⚠️ Leer heisst "nichts gefunden", nicht "nicht geprueft" - eine Instanz,
    #: die gar nicht antwortet, steht oben mit ``erreichbar: false``.
    warnungen: list[WarnungOut] = Field(default_factory=list)


class SchluesselIn(BaseModel):
    server_id: int
    #: Leer loescht den Schluessel wieder.
    schluessel: str = ""


class VerbindenIn(BaseModel):
    #: Leer heisst: alle Instanzen.
    kennungen: list[str] = Field(default_factory=list)


class VerbindenOut(BaseModel):
    hergestellt: int = 0
    #: Was nicht ging, als "Instanz: Server: Grund".
    gescheitert: list[str] = Field(default_factory=list)


@router.get("/medienserver", response_model=VerbindungslageGesamt)
async def medienserver_lage(admin: AdminUser, db: DbSession) -> VerbindungslageGesamt:
    """Welche Instanz kennt welchen Medienserver - und wo fehlt eine Verbindung?

    Ohne sie erfaehrt der Medienserver von Importen und Umbenennungen nichts
    und zeigt bis zu seinem naechsten eigenen Durchlauf auf alte Pfade.
    """
    server = mediaserver_verbindung.server_liste(db)
    settings = load_settings(db)
    lage: list[VerbindungslageOut] = []
    warnungen: list[WarnungOut] = []
    for instanz in settings.arr_instanzen():
        client = ArrClient(instanz.url, instanz.api_key, instanz.name)
        try:
            karte = await mediaserver_verbindung.zuordnungen(client, server)
            offen = await mediaserver_verbindung.luecken(client, server, karte)
            # ⚠️ Die bestehenden gleich mitpruefen: Eine Verbindung, die
            # stillschweigend aufgehoert hat zu wirken, faellt sonst nie auf.
            verbunden: list[str] = []
            for provider, grund in await mediaserver_verbindung.bestehende_pruefen(
                client, server
            ):
                if grund:
                    warnungen.append(
                        WarnungOut(instanz=instanz.name, provider=provider, grund=grund)
                    )
                else:
                    verbunden.append(provider)
        except ArrError:
            lage.append(
                VerbindungslageOut(
                    kennung=instanz.kennung, name=instanz.name, erreichbar=False
                )
            )
            continue
        lage.append(
            VerbindungslageOut(
                kennung=instanz.kennung,
                name=instanz.name,
                verbunden=verbunden,
                fehlend=[
                    LueckeOut(
                        **{k: v for k, v in vars(luecke).items() if k != "zuordnung"},
                        zuordnung=ZuordnungOut(**vars(luecke.zuordnung)),
                    )
                    for luecke in offen
                ],
            )
        )
    return VerbindungslageGesamt(
        warnungen=warnungen,
        server=[
            MedienserverOut(
                id=s.id,
                provider=s.provider,
                name=s.name,
                url=s.url,
                braucht_schluessel=s.braucht_schluessel,
                schluessel_da=bool(s.zugang),
            )
            for s in server
        ],
        instanzen=lage,
    )


@router.put("/medienserver/schluessel", status_code=status.HTTP_204_NO_CONTENT)
def medienserver_schluessel(
    payload: SchluesselIn, admin: AdminUser, db: DbSession
) -> None:
    """Den API-Schluessel eines Medienservers hinterlegen.

    ⚠️ Das ist **nicht** der Zugang, mit dem Nexview selbst spricht - der
    entsteht bei Jellyfin und Emby aus Benutzer und Passwort und endet mit der
    Sitzung. Radarr und Sonarr brauchen einen dauerhaften Schluessel aus dem
    Dashboard des Medienservers.
    """
    if not mediaserver_verbindung.schluessel_setzen(
        db, payload.server_id, payload.schluessel
    ):
        raise meldungen.fehler(
            "mediaserver_unknown", "Diesen Medienserver gibt es nicht.", 404
        )
    db.commit()


@router.post("/medienserver/verbinden", response_model=VerbindenOut)
async def medienserver_verbinden(
    payload: VerbindenIn, admin: AdminUser, db: DbSession
) -> VerbindenOut:
    """Die fehlenden Verbindungen anlegen - erst geprueft, dann eingetragen.

    Eine Verbindung, die die Instanz nicht erreicht, wird **nicht** eingetragen:
    Ein toter Eintrag ist schlimmer als keiner, weil ihn niemand hinterfragt.
    """
    server = {s.provider + s.url: s for s in mediaserver_verbindung.server_liste(db)}
    settings = load_settings(db)
    instanzen = [
        i
        for i in settings.arr_instanzen()
        if not payload.kennungen or i.kennung in payload.kennungen
    ]
    hergestellt = 0
    gescheitert: list[str] = []
    for instanz in instanzen:
        client = ArrClient(instanz.url, instanz.api_key, instanz.name)
        try:
            karte = await mediaserver_verbindung.zuordnungen(
                client, list(server.values())
            )
            offen = await mediaserver_verbindung.luecken(
                client, list(server.values()), karte
            )
        except ArrError as fehler:
            gescheitert.append(f"{instanz.name}: {fehler.message}")
            continue
        for luecke in offen:
            eintrag = server.get(luecke.provider + luecke.url)
            if eintrag is None or not eintrag.zugang:
                gescheitert.append(f"{instanz.name}: {luecke.name}: kein_schluessel")
                continue
            # ⚠️ **Bei unbekannter Zuordnung nicht eintragen.** Ohne
            # ``mapFrom``/``mapTo`` prueft sich die Verbindung gruen und tut
            # trotzdem nichts - genau der Eintrag, den danach niemand mehr
            # hinterfragt. Lieber offen lassen und den Grund nennen.
            if luecke.zuordnung.hindernis:
                # ⚠️ Den **Anbieter** nennen, nicht den Servernamen: Emby meldet
                # sich als Maschinenkennung ("fed014e636a7"), und die sagt in
                # einer Fehlermeldung niemandem etwas.
                gescheitert.append(
                    f"{instanz.name}: {eintrag.provider}: {luecke.zuordnung.hindernis}"
                )
                continue
            grund = await mediaserver_verbindung.herstellen(
                client,
                eintrag.provider,
                eintrag.name,
                eintrag.url,
                eintrag.zugang,
                luecke.zuordnung,
            )
            if grund:
                gescheitert.append(f"{instanz.name}: {eintrag.name}: {grund}")
            else:
                hergestellt += 1
    return VerbindenOut(hergestellt=hergestellt, gescheitert=gescheitert)


@router.get("/benennung/{kennung}/fortschritt", response_model=UmbenennenOut)
def benennung_fortschritt(
    kennung: Annotated[str, Path(min_length=1, max_length=32)], admin: AdminUser
) -> UmbenennenOut:
    """Wie weit das Angleichen des Bestands ist.

    Noetig, weil Radarr und Sonarr zu einem Auftrag nur "laeuft" oder "fertig"
    melden - der Fortschritt entsteht dadurch, dass Nexview die Arbeit selbst
    in Haeppchen zerlegt und zaehlt.
    """
    stand = benennung_dienst.umbenennstand(kennung)
    if stand is None:
        return UmbenennenOut()
    return UmbenennenOut(
        laeuft=stand.schritt != "fertig",
        instanz=stand.instanz,
        schritt=stand.schritt,
        erledigt=stand.erledigt,
        gesamt=stand.gesamt,
        betroffen=stand.betroffen,
        beispiele=list(stand.beispiele),
        fortgesetzt=stand.fortgesetzt,
    )


class ProfilBestandOut(BaseModel):
    id: int
    name: str
    #: Hat Nexview dieses Profil angelegt?
    unser: bool = False
    medien: int = 0
    importlisten: int = 0
    sammlungen: int = 0
    loeschbar: bool = True
    #: Warum nicht - "medien:12,sammlungen:3". Leer heisst: geht.
    grund: str = ""


class MusterBestandOut(BaseModel):
    id: int
    name: str
    #: Profile, die diesem Muster Punkte geben.
    benutzt_von: list[str] = Field(default_factory=list)
    #: Gehoert zum Bauplan eines Nexview-Profils - dann nicht loeschbar.
    gehoert_zu_plan: bool = False
    alter_vorsatz: bool = False
    im_dateinamen: bool = False
    loeschbar: bool = True


class BestandOut(BaseModel):
    kennung: str
    name: str
    erreichbar: bool = True
    profile: list[ProfilBestandOut] = Field(default_factory=list)
    muster: list[MusterBestandOut] = Field(default_factory=list)


class UmhaengenIn(BaseModel):
    """Von welchem Profil auf welches."""

    von: int
    nach: int


class UmhaengenOut(BaseModel):
    umgehaengt: int = 0
    #: Leer heisst: hat geklappt.
    grund: str = ""


class AufraeumenIn(BaseModel):
    profil_ids: list[int] = Field(default_factory=list)
    muster_ids: list[int] = Field(default_factory=list)


class AufraeumenOut(BaseModel):
    geloescht_profile: list[str] = Field(default_factory=list)
    geloescht_muster: list[str] = Field(default_factory=list)
    abgelehnt: dict[str, str] = Field(default_factory=dict)


async def _plan_muster(db: Session, client: ArrClient, kennung: str) -> set[str]:
    """Welche Musternamen gehoeren zu den Bauplaenen der Profile dieser Instanz?

    ⚠️ **Noetig, damit die Aufraeum-Liste nicht in die Irre fuehrt.** Ein
    Bauplan bringt Muster mit, die er bewusst mit **null Punkten** bewertet -
    Streaming-Kennungen etwa. Ohne diese Auskunft gaelten sie als "ungenutzt",
    der Betreiber loescht sie, und das naechste Verteilen legt sie wieder an.
    Genau diese Schleife ist am 29.08.2026 aufgefallen.

    Scheitert der Bau eines Plans, gilt lieber gar nichts als geschuetzt: Ein
    Muster faelschlich zum Loeschen anzubieten ist aergerlich, aber die ganze
    Seite daran scheitern zu lassen waere schlimmer.
    """
    namen: set[str] = set()
    profile = [
        p for p in dienst.alle(db)
        if any(e.kennung == kennung for e in p.installationen)
    ]
    if not profile:
        return namen
    try:
        umgebung = await dienst.umgebung_von(client)
        for profil in profile:
            plan = await dienst.plan_fuer(client, profil, umgebung)
            namen.update(w.name for w in plan.formate)
    except Exception:  # noqa: BLE001 - eine fehlende Auskunft kippt die Seite nicht
        logger.info("Could not determine plan formats for %s", kennung)
    return namen


def _unsere_nummern(db: Session, kennung: str) -> dict[int, str]:
    """Welche Profilnummer auf der Instanz gehoert zu welchem Nexview-Namen?"""
    zuordnung: dict[int, str] = {}
    for profil in dienst.alle(db):
        for eintrag_ in profil.installationen:
            if eintrag_.kennung == kennung and eintrag_.profil_id_extern:
                zuordnung[int(eintrag_.profil_id_extern)] = profil.name
    return zuordnung


@router.get("/bestand", response_model=list[BestandOut])
async def bestand(admin: AdminUser, db: DbSession) -> list[BestandOut]:
    """Alles, was auf den Instanzen liegt - auch was Nexview nicht angelegt hat.

    ⚠️ **Warum auch das Fremde.** Ein Muster, das den Namen belegt, den Nexview
    braucht, war bisher unsichtbar; ein Profil, das ein Loeschen blockiert,
    ebenso. Der Betreiber sah nur einen Fehler und musste in Radarr selbst
    suchen. Hier steht stattdessen, was woran haengt - Medien, Importlisten und
    Sammlungen zusammengetragen, was Radarr in seiner eigenen Fehlermeldung
    nicht tut.
    """
    settings = load_settings(db)
    ergebnis: list[BestandOut] = []
    for instanz in settings.arr_instanzen():
        art = instanz.kennung.split("-")[0]
        client = ArrClient(instanz.url, instanz.api_key, instanz.name)
        roh = await arr_bestand.aufnehmen(
            client,
            instanz.kennung,
            art,
            _unsere_nummern(db, instanz.kennung),
            await _plan_muster(db, client, instanz.kennung),
        )
        ergebnis.append(
            BestandOut(
                kennung=roh.kennung,
                name=roh.name,
                erreichbar=roh.erreichbar,
                profile=[
                    ProfilBestandOut(
                        id=p.id, name=p.name, unser=p.unser, medien=p.medien,
                        importlisten=p.importlisten, sammlungen=p.sammlungen,
                        loeschbar=p.loeschbar, grund=p.grund(),
                    )
                    for p in roh.profile
                ],
                muster=[
                    MusterBestandOut(
                        id=m.id, name=m.name, benutzt_von=m.benutzt_von,
                        gehoert_zu_plan=m.gehoert_zu_plan,
                        alter_vorsatz=m.alter_vorsatz,
                        im_dateinamen=m.im_dateinamen, loeschbar=m.loeschbar,
                    )
                    for m in roh.muster
                ],
            )
        )
    return ergebnis


@router.post("/bestand/{kennung}/umhaengen", response_model=UmhaengenOut)
async def bestand_umhaengen(
    kennung: Annotated[str, Path(min_length=1, max_length=32)],
    payload: UmhaengenIn,
    admin: AdminUser,
    db: DbSession,
) -> UmhaengenOut:
    """Alle Medien eines Profils auf ein anderes umhaengen.

    ⚠️ **Ohne diesen Weg endet das Aufraeumen, bevor es anfaengt.** Ein Profil
    laesst sich nicht loeschen, solange Medien darauf liegen - und in einer
    gewachsenen Anlage liegt fast alles auf Profilen, die es vor Nexview schon
    gab. Wer nur loeschen kann, was ohnehin leer ist, raeumt nichts auf.

    ⚠️ **Dateien werden nicht angefasst.** Es aendert sich die Zuordnung,
    nicht der Speicherort. Das neue Profil bewertet aber anders: Titel, deren
    Datei darunter liegt, merkt die Instanz zur Aufwertung vor - das kann
    Downloads ausloesen. Diese Folge nennt die Oberflaeche vor dem Klick.
    """
    settings = load_settings(db)
    instanz = next(
        (i for i in settings.arr_instanzen() if i.kennung == kennung), None
    )
    if instanz is None:
        raise meldungen.fehler(
            "quality_instance_unknown", "Diese Instanz ist nicht eingerichtet.", 400
        )
    if payload.von == payload.nach:
        raise meldungen.fehler(
            "quality_move_same_profile",
            "Quelle und Ziel sind dasselbe Profil.",
            400,
        )
    art = instanz.kennung.split("-")[0]
    client = ArrClient(instanz.url, instanz.api_key, instanz.name)

    # ⚠️ Erst nachsehen, ob es das Ziel ueberhaupt gibt: Eine ungueltige
    # Nummer wuerde die Instanz sonst kommentarlos schlucken, und danach haengen
    # die Medien an einem Profil, das es nicht gibt.
    try:
        vorhanden = {int(p.get("id") or 0) for p in await client.quality_profiles()}
    except ArrError as fehler:
        raise meldungen.fehler(
            "quality_instance_unreachable", fehler.message, 502
        ) from fehler
    if payload.nach not in vorhanden or payload.von not in vorhanden:
        raise meldungen.fehler(
            "quality_profile_unknown", "Dieses Profil gibt es dort nicht.", 404
        )

    ergebnis = await arr_bestand.umhaengen(client, art, payload.von, payload.nach)
    return UmhaengenOut(umgehaengt=ergebnis.umgehaengt, grund=ergebnis.grund)


@router.post("/bestand/{kennung}/aufraeumen", response_model=AufraeumenOut)
async def bestand_aufraeumen(
    kennung: Annotated[str, Path(min_length=1, max_length=32)],
    payload: AufraeumenIn,
    admin: AdminUser,
    db: DbSession,
) -> AufraeumenOut:
    """Ausgewaehlte Profile und Muster von der Instanz entfernen.

    ⚠️ **Jedes einzeln geprueft, unmittelbar vorher.** Was der Browser vor
    einer Minute angezeigt hat, muss nicht mehr gelten - ein Film kann
    inzwischen auf das Profil gezogen worden sein. Was benutzt wird, wird
    abgelehnt und der Grund genannt, statt es zu erzwingen.

    ⚠️ **Auch Fremdes darf hier weg** - anders als beim Schreiben, wo Nexview
    nichts anfasst, das es nicht angelegt hat. Der Unterschied ist die Absicht:
    Hier hat der Betreiber ausgewaehlt, was verschwinden soll.
    """
    settings = load_settings(db)
    instanz = next(
        (i for i in settings.arr_instanzen() if i.kennung == kennung), None
    )
    if instanz is None:
        raise meldungen.fehler(
            "quality_instance_unknown", "Diese Instanz ist nicht eingerichtet.", 400
        )
    art = instanz.kennung.split("-")[0]
    client = ArrClient(instanz.url, instanz.api_key, instanz.name)
    roh = await arr_bestand.aufnehmen(
        client,
        instanz.kennung,
        art,
        _unsere_nummern(db, instanz.kennung),
        await _plan_muster(db, client, instanz.kennung),
    )
    if not roh.erreichbar:
        raise meldungen.fehler(
            "quality_instance_unreachable",
            f"{instanz.name} antwortet gerade nicht.",
            502,
        )
    ergebnis = await arr_bestand.aufraeumen(
        client, roh, payload.profil_ids, payload.muster_ids
    )

    # ⚠️ Das Besitzbuch nachziehen: Wurde eine Kopie von Nexview geloescht,
    # darf der Eintrag nicht stehenbleiben - sonst meldete der Abgleich fuer
    # immer "fehlt" und die Oberflaeche riete zum Neuschreiben.
    if ergebnis.geloescht_profile:
        entfernt = set(payload.profil_ids)
        for profil in dienst.alle(db):
            for eintrag_ in list(profil.installationen):
                if eintrag_.kennung == kennung and eintrag_.profil_id_extern in entfernt:
                    db.delete(eintrag_)
        db.commit()

    return AufraeumenOut(
        geloescht_profile=ergebnis.geloescht_profile,
        geloescht_muster=ergebnis.geloescht_muster,
        abgelehnt=ergebnis.abgelehnt,
    )


@router.get("/benennung", response_model=list[BenennungOut])
async def benennung(admin: AdminUser, db: DbSession) -> list[BenennungOut]:
    """Das Benennungsschema jeder Instanz, daneben die Empfehlung."""
    settings = load_settings(db)
    medienserver = (settings.mediaserver_provider or "").lower()
    ergebnis: list[BenennungOut] = []
    for instanz in settings.arr_instanzen():
        # ⚠️ Nicht ``dienst`` nennen: So heisst hier das Modul
        # ``services.qualitaetsprofile``. Eine lokale Variable gleichen Namens
        # verdeckt es lautlos, und der Fehler faellt erst zur Laufzeit auf.
        art = instanz.kennung.split("-")[0]
        client = ArrClient(instanz.url, instanz.api_key, instanz.name)
        try:
            vorschlag = await benennung_dienst.vorschlag_fuer(
                client, instanz.kennung, instanz.name, art, medienserver
            )
        except ArrError as fehler:
            logger.info(
                "Naming scheme of %s not readable: %s", instanz.kennung, fehler.message
            )
            ergebnis.append(
                BenennungOut(
                    kennung=instanz.kennung,
                    name=instanz.name,
                    dienst=art,
                    umbenennen_an=False,
                    erreichbar=False,
                )
            )
            continue
        # ⚠️ **Die Praefix-Frage gehoert genau hierher.** Sie entscheidet, ob
        # ein Bestandslauf die Dateinamen aufraeumt oder ``[NXV - German DL]``
        # hineinschreibt - und der Knopf dafuer steht auf dieser Seite.
        lage = await dienst.praefix_lage(client)
        laeuft = benennung_dienst.umbenennstand(instanz.kennung)
        ergebnis.append(
            BenennungOut(
                **vars(vorschlag),
                erreichbar=True,
                altnamen=AltnamenOut(**vars(lage)),
                lauf_offen=bool(laeuft and laeuft.schritt != "fertig"),
            )
        )
    return ergebnis


@router.post("/benennung/{kennung}/altnamen", response_model=AufraeumOut)
async def altnamen_aufraeumen(
    kennung: Annotated[str, Path(min_length=1, max_length=32)],
    admin: AdminUser,
    db: DbSession,
) -> AufraeumOut:
    """Muster mit altem Vorsatz auf den schlichten Namen zurueckdrehen.

    ⚠️ **Vor jedem Bestandslauf.** Solange die Muster ``NXV - `` heissen und
    ``includeCustomFormatWhenRenaming`` tragen, schreibt jede Umbenennung den
    Vorsatz in den Dateinamen. Die Nummern bleiben erhalten, Profile zeigen
    weiter auf dieselben Muster - es aendert sich nur, wie sie heissen.
    """
    settings = load_settings(db)
    instanz = next(
        (i for i in settings.arr_instanzen() if i.kennung == kennung), None
    )
    if instanz is None:
        raise meldungen.fehler(
            "quality_instance_unknown", "Diese Instanz ist nicht eingerichtet.", 400
        )
    client = ArrClient(instanz.url, instanz.api_key, instanz.name)
    try:
        anzahl = await dienst.praefix_aufraeumen(client)
    except ArrError as fehler:
        raise meldungen.fehler(
            "quality_instance_unreachable", fehler.message, 502
        ) from fehler
    return AufraeumOut(
        umbenannt=anzahl, altnamen=AltnamenOut(**vars(await dienst.praefix_lage(client)))
    )


@router.put("/benennung", response_model=BenennungOut)
async def benennung_uebernehmen(
    payload: BenennungIn, admin: AdminUser, db: DbSession
) -> BenennungOut:
    """Das empfohlene Schema auf einer Instanz setzen.

    ⚠️ **Benennt nichts um, was schon da ist.** Das Schema gilt fuer alles, was
    die Instanz ab jetzt selbst schreibt. Den Bestand anzufassen ist ein eigener
    Vorgang in Radarr beziehungsweise Sonarr - mit Vorschau, und mit Folgen fuer
    laufendes Seeding und den Medienserver.
    """
    settings = load_settings(db)
    instanz = next(
        (i for i in settings.arr_instanzen() if i.kennung == payload.kennung), None
    )
    if instanz is None:
        raise meldungen.fehler(
            "quality_instance_unknown", "Diese Instanz ist nicht eingerichtet.", 400
        )
    # Nicht ``dienst`` nennen - so heisst hier das Modul (siehe ``benennung``).
    art = instanz.kennung.split("-")[0]
    client = ArrClient(instanz.url, instanz.api_key, instanz.name)
    try:
        vorschlag = await benennung_dienst.uebernehmen(
            client,
            art,
            payload.datei,
            payload.ordner,
            (settings.mediaserver_provider or "").lower(),
        )
        if payload.bestand:
            # ⚠️ Erst das Schema setzen, dann den Bestand angleichen - in
            # dieser Reihenfolge. Andersherum benennt die Instanz nach dem
            # alten Schema um, und der Lauf war umsonst.
            #
            # Und nur **anstossen**: Bei mehreren tausend Titeln dauert der
            # Lauf Minuten. Die Antwort geht sofort hinaus, den Fortschritt
            # holt sich die Oberflaeche ueber ``/benennung/{kennung}/fortschritt``.
            benennung_dienst.anstossen(client, art, instanz.kennung)
    except ArrError as fehler:
        raise meldungen.fehler(
            fehler.code or "arr_http_error", fehler.message, 502
        ) from fehler
    vorschlag.kennung = instanz.kennung
    vorschlag.name = instanz.name
    # Auch hier die Praefix-Lage mitgeben: Ein Standardwert von Null behauptete
    # "keine Altnamen mehr", ohne nachgesehen zu haben.
    return BenennungOut(
        **vars(vorschlag),
        erreichbar=True,
        altnamen=AltnamenOut(**vars(await dienst.praefix_lage(client))),
    )


@router.get("/{profil_id}/fortschritt", response_model=FortschrittOut)
def fortschritt(
    profil_id: Annotated[int, Path(ge=1)], admin: AdminUser
) -> FortschrittOut:
    """Wie weit das Schreiben ist.

    Wird waehrend des Verteilens gefragt: Der Vorgang haelt die Verbindung
    ueber eine Minute offen, weil Radarr jedes Erkennungsmuster einzeln
    annimmt - ohne diese Auskunft saehe der Nutzer solange nichts.
    """
    stand = dienst.fortschritt(profil_id)
    if stand is None:
        return FortschrittOut()
    return FortschrittOut(
        laeuft=True,
        instanz=stand.instanz,
        schritt=stand.schritt,
        erledigt=stand.erledigt,
        gesamt=stand.gesamt,
        instanz_nummer=stand.instanz_nummer,
        von_instanzen=stand.von_instanzen,
    )


@router.delete("/{profil_id}", status_code=status.HTTP_204_NO_CONTENT)
def loeschen(
    profil_id: Annotated[int, Path(ge=1)], admin: AdminUser, db: DbSession
) -> None:
    profil = dienst.eintrag(db, profil_id)
    if profil is None:
        raise meldungen.fehler(
            "quality_profile_unknown", "Dieses Profil gibt es nicht.", 404
        )
    dienst.loeschen(db, profil)
    db.commit()


@router.put("/{profil_id}/instanzen", response_model=VerteilenOut)
async def verteilen(
    profil_id: Annotated[int, Path(ge=1)],
    payload: VerteilenIn,
    admin: AdminUser,
    db: DbSession,
) -> VerteilenOut:
    """Das Profil auf die genannten Instanzen schreiben.

    Fehler je Instanz brechen den ganzen Vorgang ab: Ein halb verteiltes
    Profil waere schlimmer als gar keins, weil niemand sagen koennte, wo es
    nun liegt und wo nicht.
    """
    profil = dienst.eintrag(db, profil_id)
    if profil is None:
        raise meldungen.fehler(
            "quality_profile_unknown", "Dieses Profil gibt es nicht.", 404
        )

    settings = load_settings(db)
    passende = {
        i.kennung: i
        for i in settings.arr_instanzen()
        if i.kennung.split("-")[0] == profil.dienst
    }
    unbekannt = [k for k in payload.kennungen if k not in passende]
    if unbekannt:
        raise meldungen.fehler(
            "quality_instance_unknown",
            "Diese Instanz ist nicht eingerichtet.",
            400,
            kennungen=unbekannt,
        )

    neu = wieder = 0
    hinweise: list[str] = []
    with dienst.fortschritt_fuehren(profil.id) as stand:
        for nummer, kennung in enumerate(payload.kennungen, start=1):
            instanz = passende[kennung]
            client = ArrClient(instanz.url, instanz.api_key, instanz.name)
            stand.instanz = instanz.name
            stand.schritt = "plan"
            stand.von_instanzen = len(payload.kennungen)
            stand.instanz_nummer = nummer
            ergebnis = await _eine_instanz(db, profil, kennung, client, stand)
            dienst.merken(db, profil, kennung, ergebnis)
            neu += ergebnis.formate_neu
            wieder += ergebnis.formate_wiederverwendet
            hinweise.extend(ergebnis.hinweise)

    for kennung in list(passende):
        if kennung not in payload.kennungen:
            dienst.vergessen(db, profil, kennung)

    db.commit()
    db.refresh(profil)
    return VerteilenOut(
        installationen=_hinaus(profil).installationen,
        formate_neu=neu,
        formate_wiederverwendet=wieder,
        hinweise=sorted(set(hinweise)),
    )


async def _eine_instanz(
    db: Session,
    profil: Qualitaetsprofil,
    kennung: str,
    client: ArrClient,
    stand: dienst.Fortschritt,
) -> dienst.Schreibergebnis:
    """Eine Instanz beschreiben - Fehler werden hier in Meldungen uebersetzt."""
    try:
        plan = await dienst.plan_fuer(client, profil)
        vorher = dienst.installation(db, profil.id, kennung)
        return await dienst.schreiben(
            client, plan, vorher.profil_id_extern if vorher else None, melden=stand
        )
    except TrashFehler as fehler:
        logger.warning("Recipe for profile %s cannot be built: %s", profil.id, fehler)
        raise meldungen.fehler(
            "quality_recipe_unsupported",
            "Diese Kombination laesst sich mit dem vorliegenden Stand nicht bauen.",
            409,
        ) from fehler
    except ArrError as fehler:
        db.rollback()
        raise meldungen.fehler(
            fehler.code or "arr_http_error", fehler.message, 502
        ) from fehler
