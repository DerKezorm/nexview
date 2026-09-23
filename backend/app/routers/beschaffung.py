"""Was der Beschaffungsweg über sich hergibt - unabhängig davon, welcher es ist.

Drei Dinge, die es im ARR-Betrieb nicht gibt und im NEX-Betrieb schon: die
Gründe, warum ein Titel noch nicht da ist, der Papierkorb zum Zurückholen und
die Sprünge in die Oberfläche des Wegs.

⚠️ **Der Router fragt die Fähigkeit, nicht den Namen des Wegs.** `warum`
antwortet auch im ARR-Betrieb - mit „weiß ich nicht", denn Radarr und Sonarr
sagen es nicht. Der Papierkorb antwortet `409 not_in_this_mode`, weil es dort
wirklich keinen gibt: Arrs Papierkorb ist ein **Ordner**, den der Betreiber
unter „Kontingente" einstellt, und aus dem sich nichts zurückholen lässt.

⚠️ **Kein Satz aus dem Weg geht nach draußen** (N5). Jeder Grund ist eine
Kennung mit Werten; den Satz baut die Oberfläche.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from .. import meldungen
from ..deps import AdminUser, AdultUser, DbSession
from ..models import MediaType
from ..services import beschaffung
from ..services.beschaffung import BeschaffungError, Kennt, get_beschaffung
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/beschaffung", tags=["beschaffung"])

#: ⚠️ **Zwei Adressen behalten ihren alten Namen.** Der Stand und die Gesundheit
#: der Instanzen hiessen immer ``/api/settings/instanzen/...``; sie umzubenennen
#: haette die Dienste-Seite gebrochen und jedem, der sie kennt, den Verweis
#: genommen - fuer nichts. Nur der Weg darunter ist jetzt austauschbar.
instanzen_router = APIRouter(prefix="/api", tags=["settings"])


def _weg(db: DbSession) -> Any:
    return get_beschaffung(load_settings(db))


def _als_meldung(fehler: BeschaffungError) -> HTTPException:
    return HTTPException(status_code=502, detail=fehler.als_meldung())


# --------------------------------------------------------------------------
# Warum ein Titel noch nicht da ist


class GrundZeile(BaseModel):
    """⚠️ Eine Kennung, kein Satz. ``werte`` füllt ihre Platzhalter."""

    fassung: str
    code: str
    werte: dict[str, Any] = Field(default_factory=dict)
    #: Untergründe, wo der Weg sie nennt - etwa die Gründe einer Fassung, die
    #: noch nicht bereit ist (kein Indexer, kein Profil, kein Download-Programm).
    darunter: list[str] = Field(default_factory=list)


class WarumAntwort(BaseModel):
    #: ``False``, wenn der Weg gar nicht sagen kann, warum (Radarr und Sonarr).
    beantwortbar: bool
    bekannt: bool = False
    automatisch: bool = False
    suchwunsch: bool = False
    zuletzt_gesucht: str | None = None
    naechste_suche: str | None = None
    gruende: list[GrundZeile] = Field(default_factory=list)


@router.get("/warum/{media_type}/{tmdb_id}", response_model=WarumAntwort)
async def warum(
    media_type: MediaType, tmdb_id: int, user: AdultUser, db: DbSession
) -> WarumAntwort:
    """Warum dieser Titel noch nicht da ist (N28).

    Für jeden angemeldeten Erwachsenen: Wer einen Titel anfragen darf, darf
    auch erfahren, woran es hängt. Die Antwort nennt keine Pfade und keine
    Indexer-Namen - nur Kennungen.
    """
    weg = _weg(db)
    if not weg.faehigkeiten().warum:
        return WarumAntwort(beantwortbar=False)
    try:
        gefunden = await weg.warum([Kennt(media_type=media_type.value, tmdb_id=tmdb_id)])
    except BeschaffungError as fehler:
        raise _als_meldung(fehler) from fehler
    if not gefunden:
        return WarumAntwort(beantwortbar=True)
    stand = gefunden[0]
    return WarumAntwort(
        beantwortbar=True,
        bekannt=stand.bekannt,
        automatisch=stand.automatisch,
        suchwunsch=stand.suchwunsch,
        zuletzt_gesucht=stand.zuletzt_gesucht,
        naechste_suche=stand.naechste_suche,
        gruende=[
            GrundZeile(
                fassung=grund.fassung,
                code=grund.code,
                werte=grund.werte,
                darunter=list(grund.darunter),
            )
            for grund in stand.gruende
        ],
    )


# --------------------------------------------------------------------------
# Der Papierkorb des Wegs


class PapierkorbZeile(BaseModel):
    """Eine gelöschte Datei, wie der Weg sie führt.

    ⚠️ **``present`` und ``in_library`` sind zwei verschiedene Nein.** Die
    Datei kann weg sein (oder ihre Platte gerade nicht sichtbar), oder der
    Titel hat die Bibliothek verlassen - dann führt Zurückholen zu nichts. Ein
    Knopf, der beides verschweigt, verspricht etwas, das nicht eintritt.
    """

    eintrag_id: int
    media_type: str
    tmdb_id: int | None = None
    name: str | None = None
    jahr: int | None = None
    fassung: str | None = None
    staffel: int | None = None
    folgen: list[int] = Field(default_factory=list)
    dateiname: str = ""
    size_bytes: int = 0
    geloescht_am: str = ""
    geloescht_von: str = ""
    geloescht_von_name: str | None = None
    datei_da: bool = True
    im_bestand: bool = True


class PapierkorbAntwort(BaseModel):
    eintraege: list[PapierkorbZeile] = Field(default_factory=list)
    #: Der Sprung in die Papierkorb-Seite des Wegs, wenn es eine gibt.
    sprung: str = ""


def _nur_mit_papierkorb(db: DbSession) -> Any:
    weg = _weg(db)
    if weg.faehigkeiten().papierkorb:
        return weg
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=meldungen.meldung(
            "not_in_this_mode",
            "In dieser Betriebsart gibt es keinen Papierkorb zum Zurückholen.",
        ),
    )


def _zeile(roh: dict[str, Any]) -> PapierkorbZeile:
    """Eine Zeile des Wegs in Nexviews Form.

    ``ref`` ist ``tmdb:<Nummer>``; ein Album trägt ``mbid:`` und wird hier
    ausgelassen, weil Nexview keine Musik führt.
    """
    ref = str(roh.get("ref") or "")
    nummer = ref.split(":", 1)[1] if ref.startswith("tmdb:") else ""
    art = "tv" if str(roh.get("kind")) == "series" else str(roh.get("kind") or "")
    return PapierkorbZeile(
        eintrag_id=int(roh.get("entry_id") or 0),
        media_type=art,
        tmdb_id=int(nummer) if nummer.isdigit() else None,
        name=roh.get("name"),
        jahr=roh.get("year"),
        fassung=roh.get("version_id"),
        staffel=roh.get("season"),
        folgen=[int(n) for n in (roh.get("episodes") or []) if str(n).isdigit()],
        dateiname=str(roh.get("file_name") or ""),
        size_bytes=int(roh.get("size_bytes") or 0),
        geloescht_am=str(roh.get("deleted_at") or ""),
        geloescht_von=str(roh.get("deleted_by") or ""),
        geloescht_von_name=roh.get("deleted_by_name"),
        datei_da=bool(roh.get("present", True)),
        im_bestand=bool(roh.get("in_library", True)),
    )


@router.get("/papierkorb", response_model=PapierkorbAntwort)
async def papierkorb(admin: AdminUser, db: DbSession) -> PapierkorbAntwort:
    """Was gelöscht wurde und was davon zurückkommen kann.

    ⚠️ **Alben bleiben draußen.** Der NEX-Weg führt auch Musik; Nexview nicht.
    Ein Eintrag, dessen Titel Nexview gar nicht kennt, wäre eine Zeile ohne
    Bezug - und der Knopf daran eine Einladung, fremden Bestand anzufassen.
    """
    weg = _nur_mit_papierkorb(db)
    try:
        roh = await weg.papierkorb()
    except BeschaffungError as fehler:
        raise _als_meldung(fehler) from fehler
    return PapierkorbAntwort(
        eintraege=[
            _zeile(eintrag)
            for eintrag in roh
            if str(eintrag.get("kind")) in ("movie", "series")
        ],
        sprung=weg.spruenge().papierkorb,
    )


@router.post("/papierkorb/{eintrag_id}/zurueckholen", status_code=status.HTTP_204_NO_CONTENT)
async def zurueckholen(eintrag_id: int, admin: AdminUser, db: DbSession) -> None:
    """Eine gelöschte Datei zurückholen.

    ⚠️ **Nexview prüft nicht, ob es geht.** Ob die Datei noch liegt und ob ihr
    Titel überhaupt noch da ist, weiß nur der Weg selbst - und er sagt es in
    derselben Antwort, die auch das Zurückholen ausführt. Eine eigene Prüfung
    davor wäre ein zweiter Stand, der zwischen Frage und Tat altert.
    """
    weg = _nur_mit_papierkorb(db)
    try:
        await weg.wiederherstellen(eintrag_id)
    except BeschaffungError as fehler:
        raise _als_meldung(fehler) from fehler


# --------------------------------------------------------------------------
# Die Instanzen, die hinter der Beschaffung stehen
#
# ⚠️ **Beide Adressen lagen einmal hinter dem Riegel der Arr-Werkzeuge** und
# antworteten im NEX-Betrieb `409` - obwohl die Dienste-Seite sie bei jedem
# Aufbau fragt und es dort sehr wohl eine Instanz gibt (Bauplan 9: „eine
# Instanz nexcrate"). Gefunden hat das erst der Durchlauf gegen eine echte
# nexcrate, kein Test. Sie heißen weiter, wie sie hießen; nur der Weg darunter
# ist jetzt austauschbar.


class VerbindungInstanz(BaseModel):
    kennung: str
    name: str
    erreichbar: bool
    version: str = ""


class VerbindungStand(BaseModel):
    instanzen: list[VerbindungInstanz]


@instanzen_router.get("/settings/instanzen/verbindung", response_model=VerbindungStand)
async def instanzen_verbindung(admin: AdminUser, db: DbSession) -> VerbindungStand:
    """Sind die Instanzen gerade erreichbar? Live gefragt, nichts gespeichert.

    Für die Statusleuchte auf den Kacheln - deshalb alle gleichzeitig und mit
    kurzem Atem: Eine stumme Instanz darf die Antwort der anderen nicht
    festhalten, und eine Leuchte, die fünfzehn Sekunden nachdenkt, beruhigt
    niemanden.
    """
    weg = _weg(db)

    async def pruefen(instanz: Any) -> VerbindungInstanz:
        try:
            messung = await weg.instanz_messen(instanz, voll=False)
        except BeschaffungError:
            return VerbindungInstanz(kennung=instanz.kennung, name=instanz.name, erreichbar=False)
        return VerbindungInstanz(
            kennung=instanz.kennung,
            name=instanz.name,
            erreichbar=messung.erreichbar,
            version=messung.version,
        )

    ergebnisse = await asyncio.gather(*(pruefen(instanz) for instanz in weg.instanzen()))
    return VerbindungStand(instanzen=list(ergebnisse))


class GesundheitProblem(BaseModel):
    typ: str
    text: str


class GesundheitInstanz(BaseModel):
    kennung: str
    name: str
    probleme: list[GesundheitProblem]
    aktualisiert_am: datetime | None


class GesundheitStand(BaseModel):
    instanzen: list[GesundheitInstanz]


@instanzen_router.get("/settings/instanzen/gesundheit", response_model=GesundheitStand)
def instanzen_gesundheit(admin: AdminUser, db: DbSession) -> GesundheitStand:
    """Was die Instanzen selbst als Problem melden - je Instanz.

    Gelesen wird der zuletzt gesehene Stand (der Rundgang holt ihn jede Runde
    frisch); die Texte kommen im Wortlaut der Instanz und werden bewusst nicht
    übersetzt.
    """
    stand = beschaffung.gesundheit_je_instanz(db)
    return GesundheitStand(
        instanzen=[
            GesundheitInstanz(
                kennung=instanz.kennung,
                name=instanz.name,
                probleme=[
                    GesundheitProblem(
                        typ=str(p.get("typ") or "warning"), text=str(p.get("text") or "")
                    )
                    for p in getattr(stand.get(instanz.kennung), "stand", None) or []
                ],
                aktualisiert_am=getattr(stand.get(instanz.kennung), "aktualisiert_am", None),
            )
            for instanz in _weg(db).instanzen()
        ]
    )
