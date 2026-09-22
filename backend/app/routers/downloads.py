"""Die Seite Downloads: was haengt, warum, und die Knoepfe dagegen.

⚠️ **Durchgehend Administratoren.** Hier wird entfernt, gesperrt und
importiert - das veraendert Radarr, Sonarr und das Download-Programm.
Entscheider sehen in ihrer Anfrageliste, dass ein Import haengt; handeln
kann nur, wer den Betrieb verantwortet.

⚠️ **Die Uebersicht fragt live, aber nicht bei jedem Neuladen.** Sie laeuft
durch denselben Abgleich wie der Rundgang (``download_haenger.auffrischen``)
und nimmt einen, der hoechstens ``download_haenger.FRISCH`` alt ist. Jede
Aktion verwirft ihn, damit die Seite danach nicht den Stand von vorher zeigt.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import AdminUser, DbSession
from ..models import DownloadHaenger, DownloadVerlauf, User
from ..services import beschaffung, download_automatik
from ..services.beschaffung import BeschaffungError, DownloadFehler, get_beschaffung
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/admin/downloads", tags=["admin"])


async def _ausfuehren[T](aufruf: Awaitable[T]) -> T:
    """Fehler der Aktionen in Antworten uebersetzen - an einer Stelle fuer alle Knoepfe."""
    try:
        return await aufruf
    except DownloadFehler as fehler:
        raise HTTPException(status_code=fehler.status_code, detail=fehler.als_meldung()) from fehler
    except BeschaffungError as fehler:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=fehler.als_meldung()
        ) from fehler


class Besteller(BaseModel):
    anfrage_id: int
    name: str


class HaengerZeile(BaseModel):
    """Ein Download, der haengt - eine Karte unter "Braucht dich"."""

    id: int
    kennung: str
    instanz: str
    media_type: str
    titel: str
    jahr: int | None
    release: str
    folgen: list[list[int]]
    grund: str
    #: Die Gruende im Wortlaut der Instanz - englisch, und das bleibt so.
    wortlaut: list[str]
    zustand: str
    meldestufe: str
    programmstand: str
    protokoll: str
    programm: str
    groesse: int
    erstmals_gesehen: datetime
    haengt_seit: datetime
    #: Was der Grund empfiehlt und technisch geht - die Knoepfe vorn.
    empfohlen: list[str]
    #: Was sonst noch geht.
    weitere: list[str]
    besteller: list[Besteller]
    poster: str | None


class LaufZeile(BaseModel):
    """Alles andere in den Warteschlangen."""

    kennung: str
    instanz: str
    media_type: str
    titel: str
    jahr: int | None
    release: str
    folgen: list[list[int]]
    fortschritt: int | None
    groesse: int
    rest: int
    #: Wie die Instanz sie schreibt ("00:12:34"), oder ``None``.
    restzeit: str | None
    programm: str
    protokoll: str
    programmstand: str
    zustand: str
    #: Gestoert, aber noch nicht haengend: der Grund. Sonst ``None``.
    beobachtet: str | None


class InstanzAntwort(BaseModel):
    kennung: str
    name: str
    media_type: str
    erreichbar: bool
    #: Fehler-Kennung, wenn sie nicht geantwortet hat.
    fehler: str


class DownloadsStand(BaseModel):
    instanzen: list[InstanzAntwort]
    haenger: list[HaengerZeile]
    laufend: list[LaufZeile]
    automatik_an: bool
    stand_am: datetime


def _knoepfe(zeile: DownloadHaenger) -> tuple[list[str], list[str]]:
    moeglich = beschaffung.download_aktionen_moeglich(zeile)
    grund = beschaffung.download_gruende().get(zeile.grund)
    empfohlen = [a for a in (grund.aktionen if grund else ()) if a in moeglich]
    weitere = [a for a in moeglich if a not in empfohlen]
    return [a.value for a in empfohlen], [a.value for a in weitere]


@router.get("", response_model=DownloadsStand)
async def uebersicht(admin: AdminUser, db: DbSession) -> DownloadsStand:
    """Was haengt, was laeuft, und welche Instanz nicht geantwortet hat."""
    settings = load_settings(db)
    rundgang = await get_beschaffung(settings).downloads_auffrischen(
        db, frisch_genug=beschaffung.download_frisch()
    )
    namen = {
        instanz.kennung: instanz.name
        for instanz in get_beschaffung(settings).instanzen()
    }
    zeilen = list(
        db.scalars(
            select(DownloadHaenger)
            .where(DownloadHaenger.kennung.in_(list(namen)))
            .order_by(DownloadHaenger.haengt_seit, DownloadHaenger.id)
        )
    )

    haenger: list[HaengerZeile] = []
    for zeile in zeilen:
        if zeile.haengt_seit is None:
            continue
        anfragen = get_beschaffung(settings).download_anfragen(db, zeile)
        empfohlen, weitere = _knoepfe(zeile)
        haenger.append(
            HaengerZeile(
                id=zeile.id,
                kennung=zeile.kennung,
                instanz=namen[zeile.kennung],
                media_type=zeile.media_type,
                titel=zeile.titel,
                jahr=zeile.jahr,
                release=zeile.release,
                folgen=zeile.folgen or [],
                grund=zeile.grund,
                wortlaut=zeile.wortlaut or [],
                zustand=zeile.zustand,
                meldestufe=zeile.meldestufe,
                programmstand=zeile.programmstand,
                protokoll=zeile.protokoll,
                programm=zeile.programm,
                groesse=zeile.groesse,
                erstmals_gesehen=zeile.erstmals_gesehen,
                haengt_seit=zeile.haengt_seit,
                empfohlen=empfohlen,
                weitere=weitere,
                besteller=[
                    Besteller(
                        anfrage_id=anfrage.id,
                        name=anfrage.user.display_name or anfrage.user.username,
                    )
                    for anfrage in anfragen
                ],
                poster=next((a.poster_path for a in anfragen if a.poster_path), None),
            )
        )

    haengend = {(z.kennung, z.download_id) for z in zeilen if z.haengt_seit is not None}
    beobachtet = {(z.kennung, z.download_id): z.grund for z in zeilen if z.haengt_seit is None}
    laufend: list[LaufZeile] = []
    for abfrage in rundgang.abfragen:
        for download in abfrage.downloads:
            schluessel = (abfrage.instanz.kennung, download.download_id)
            if schluessel in haengend:
                continue
            restzeit = download.erster.get("timeleft")
            laufend.append(
                LaufZeile(
                    kennung=abfrage.instanz.kennung,
                    instanz=abfrage.instanz.name,
                    media_type=abfrage.instanz.media_type,
                    titel=download.titel,
                    jahr=download.jahr,
                    release=download.release,
                    folgen=download.folgen,
                    fortschritt=download.fortschritt,
                    groesse=download.groesse,
                    rest=download.rest,
                    restzeit=restzeit if isinstance(restzeit, str) and restzeit else None,
                    programm=str(download.erster.get("downloadClient") or ""),
                    protokoll=str(download.erster.get("protocol") or ""),
                    programmstand=str(download.erster.get("status") or ""),
                    zustand=str(download.erster.get("trackedDownloadState") or ""),
                    beobachtet=beobachtet.get(schluessel),
                )
            )

    return DownloadsStand(
        instanzen=[
            InstanzAntwort(
                kennung=abfrage.instanz.kennung,
                name=abfrage.instanz.name,
                media_type=abfrage.instanz.media_type,
                erreichbar=abfrage.erreichbar,
                fehler=abfrage.fehler,
            )
            for abfrage in rundgang.abfragen
        ],
        haenger=haenger,
        laufend=laufend,
        automatik_an=download_automatik.lesen(db).an,
        stand_am=rundgang.am,
    )


class EntfernenWunsch(BaseModel):
    #: Release sperren und neu suchen - sonst nur entfernen.
    neu_suchen: bool = False


class AktionsAntwort(BaseModel):
    gesucht: bool
    befehl: str


@router.post("/{haenger_id}/entfernen", response_model=AktionsAntwort)
async def entfernen(
    haenger_id: Annotated[int, Path(ge=1)],
    wunsch: EntfernenWunsch,
    admin: AdminUser,
    db: DbSession,
) -> AktionsAntwort:
    """Aus Warteschlange und Download-Programm nehmen - wahlweise mit Sperre und Suche."""
    ergebnis = await _ausfuehren(
        get_beschaffung(load_settings(db)).download_entfernen(
            db, haenger_id, neu_suchen=wunsch.neu_suchen, wer=admin
        )
    )
    return AktionsAntwort(gesucht=ergebnis.gesucht, befehl=ergebnis.befehl)


@router.post("/{haenger_id}/erneut", response_model=AktionsAntwort)
async def erneut(
    haenger_id: Annotated[int, Path(ge=1)], admin: AdminUser, db: DbSession
) -> AktionsAntwort:
    """Radarr bzw. Sonarr den Download noch einmal pruefen lassen."""
    ergebnis = await _ausfuehren(
        get_beschaffung(load_settings(db)).download_erneut_pruefen(db, haenger_id, wer=admin)
    )
    return AktionsAntwort(gesucht=ergebnis.gesucht, befehl=ergebnis.befehl)


class AblehnungZeile(BaseModel):
    #: Wortlaut der Instanz.
    text: str
    dauerhaft: bool


class KandidatZeile(BaseModel):
    pfad: str
    name: str
    groesse: int
    qualitaet: str
    sprachen: list[str]
    zuordnung: str
    folgen: list[list[int]]
    zuordenbar: bool
    ablehnungen: list[AblehnungZeile]


@router.get("/{haenger_id}/dateien", response_model=list[KandidatZeile])
async def dateien(
    haenger_id: Annotated[int, Path(ge=1)], admin: AdminUser, db: DbSession
) -> list[KandidatZeile]:
    """Die Dateien fuer den manuellen Import, samt Zuordnung und Ablehnungen."""
    kandidaten = await _ausfuehren(
        get_beschaffung(load_settings(db)).download_kandidaten(db, haenger_id)
    )
    return [
        KandidatZeile(
            pfad=k.pfad,
            name=k.name,
            groesse=k.groesse,
            qualitaet=k.qualitaet,
            sprachen=list(k.sprachen),
            zuordnung=k.zuordnung,
            folgen=[list(f) for f in k.folgen],
            zuordenbar=k.zuordenbar,
            ablehnungen=[AblehnungZeile(text=a.text, dauerhaft=a.dauerhaft) for a in k.ablehnungen],
        )
        for k in kandidaten
    ]


class ImportWunsch(BaseModel):
    pfade: list[Annotated[str, Field(min_length=1, max_length=4000)]] = Field(
        min_length=1, max_length=500
    )
    #: Auch Dateien mit dauerhafter Ablehnung - nur ausdruecklich.
    trotzdem: bool = False


@router.post("/{haenger_id}/importieren", response_model=AktionsAntwort)
async def importieren(
    haenger_id: Annotated[int, Path(ge=1)],
    wunsch: ImportWunsch,
    admin: AdminUser,
    db: DbSession,
) -> AktionsAntwort:
    """Die gewaehlten Dateien importieren."""
    ergebnis = await _ausfuehren(
        get_beschaffung(load_settings(db)).download_importieren(
            db, haenger_id, wunsch.pfade, trotzdem=wunsch.trotzdem, wer=admin
        )
    )
    return AktionsAntwort(gesucht=ergebnis.gesucht, befehl=ergebnis.befehl)


class AutomatikRegel(BaseModel):
    grund: str
    #: Die eingestellte Aktion, ``None`` heisst: liegen lassen.
    aktion: str | None
    erlaubt: list[str]


class AutomatikStand(BaseModel):
    an: bool
    regeln: list[AutomatikRegel]
    obergrenze: int
    fenster_stunden: int
    wiederholt_ab: int
    wiederholt_tage: int


def _automatik_stand(einstellung: download_automatik.Einstellung) -> AutomatikStand:
    return AutomatikStand(
        an=einstellung.an,
        regeln=[
            AutomatikRegel(
                grund=grund.kennung,
                aktion=einstellung.regeln.get(grund.kennung),
                erlaubt=[aktion.value for aktion in grund.automatik],
            )
            for grund in beschaffung.download_gruende().values()
            if grund.automatik
        ],
        obergrenze=download_automatik.OBERGRENZE,
        fenster_stunden=int(download_automatik.FENSTER.total_seconds() // 3600),
        wiederholt_ab=download_automatik.WIEDERHOLT_AB,
        wiederholt_tage=download_automatik.WIEDERHOLT_FENSTER.days,
    )


@router.get("/automatik", response_model=AutomatikStand)
def automatik(admin: AdminUser, db: DbSession) -> AutomatikStand:
    """Ist die Automatik an, und was tut sie bei welchem Grund?"""
    return _automatik_stand(download_automatik.lesen(db))


class AutomatikWunsch(BaseModel):
    an: bool
    regeln: dict[Annotated[str, Field(max_length=40)], str | None] = Field(
        default_factory=dict, max_length=100
    )


@router.put("/automatik", response_model=AutomatikStand)
def automatik_setzen(wunsch: AutomatikWunsch, admin: AdminUser, db: DbSession) -> AutomatikStand:
    """Die Automatik ein- oder ausschalten und ihre Regeln setzen."""
    try:
        einstellung = download_automatik.schreiben(db, an=wunsch.an, regeln=wunsch.regeln)
    except DownloadFehler as fehler:
        raise HTTPException(status_code=fehler.status_code, detail=fehler.als_meldung()) from fehler
    return _automatik_stand(einstellung)


class VerlaufZeile(BaseModel):
    id: int
    am: datetime
    kennung: str
    instanz: str
    media_type: str
    titel: str
    release: str
    grund: str
    #: ``erkannt``, ``gemeldet`` oder eine Aktion.
    was: str
    automatisch: bool
    #: Wer es von Hand ausgeloest hat. ``None`` bei der Automatik oder einem
    #: geloeschten Konto.
    wer: str | None
    #: Leer heisst: hat geklappt.
    ergebnis: str


@router.get("/verlauf", response_model=list[VerlaufZeile])
def verlauf(
    admin: AdminUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[VerlaufZeile]:
    """Was mit haengenden Downloads geschah, das Neueste zuerst."""
    settings = load_settings(db)
    namen = {
        instanz.kennung: instanz.name
        for instanz in get_beschaffung(settings).instanzen()
    }
    zeilen = db.execute(
        select(DownloadVerlauf, User)
        .outerjoin(User, User.id == DownloadVerlauf.user_id)
        .order_by(DownloadVerlauf.am.desc(), DownloadVerlauf.id.desc())
        .limit(limit)
    )
    return [
        VerlaufZeile(
            id=eintrag.id,
            am=eintrag.am,
            kennung=eintrag.kennung,
            # Eine entfernte Instanz behaelt ihre Kennung - besser als nichts.
            instanz=namen.get(eintrag.kennung, eintrag.kennung),
            media_type=eintrag.media_type,
            titel=eintrag.titel,
            release=eintrag.release,
            grund=eintrag.grund,
            was=eintrag.was,
            automatisch=eintrag.automatisch,
            wer=(person.display_name or person.username) if person is not None else None,
            ergebnis=eintrag.ergebnis,
        )
        for eintrag, person in zeilen
    ]
