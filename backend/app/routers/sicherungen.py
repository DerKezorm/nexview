"""Sicherungen - auflisten, anlegen, herunterladen. Nur fuer Administratoren."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from .. import __version__
from ..deps import AdminUser, DbSession
from .. import meldungen
from ..config import get_settings
from ..services import sicherung

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/sicherungen", tags=["admin"])


class SicherungPublic(BaseModel):
    name: str
    groesse: int
    erstellt: str
    art: str
    kommentar: str
    version: str
    #: Liesse sich diese Sicherung in die laufende Fassung einspielen? Wird
    #: schon jetzt mitgeschickt, obwohl das Wiederherstellen noch fehlt - die
    #: Liste soll niemanden in Sicherheit wiegen, der spaeter feststellt, dass
    #: ausgerechnet sein Stand nicht in Frage kommt.
    einspielbar: bool
    grund: str


class SicherungenPublic(BaseModel):
    eintraege: list[SicherungPublic]
    version: str
    #: Der Ordner, damit jemand die Dateien auch von aussen findet.
    ordner: str


class SicherungAnlegen(BaseModel):
    kommentar: str = Field("", max_length=200)


class ArchivAnfordern(BaseModel):
    #: ⚠️ Kein Mindestmass aus Bequemlichkeit: Im Archiv liegt ``secret.key``,
    #: und damit gibt die Datei alle Dienst-Zugaenge her.
    passwort: str = Field(..., min_length=8, max_length=200)


def _als_public(eintrag: sicherung.Eintrag) -> SicherungPublic:
    brief = sicherung.Steckbrief(
        version=eintrag.version, schema="", erstellt=eintrag.erstellt, art=eintrag.art
    )
    ok, grund = sicherung.vertraeglich(brief)
    return SicherungPublic(
        name=eintrag.name,
        groesse=eintrag.groesse,
        erstellt=eintrag.erstellt,
        art=eintrag.art,
        kommentar=eintrag.kommentar,
        version=eintrag.version,
        einspielbar=ok,
        grund=grund,
    )


@router.get("", response_model=SicherungenPublic)
def alle(_: AdminUser) -> SicherungenPublic:
    return SicherungenPublic(
        eintraege=[_als_public(e) for e in sicherung.liste()],
        version=__version__,
        ordner=str(sicherung.ordner()),
    )


@router.post("", response_model=SicherungPublic, status_code=status.HTTP_201_CREATED)
def anlegen(payload: SicherungAnlegen, _: AdminUser) -> SicherungPublic:
    try:
        pfad = sicherung.anlegen(art=sicherung.MANUELL, kommentar=payload.kommentar)
    except Exception as fehler:  # noqa: BLE001 - dem Betreiber sagen, was war
        logger.exception("Manual backup failed")
        raise HTTPException(
            status_code=500,
            detail=meldungen.meldung(
                "backup_failed",
                f"Die Sicherung konnte nicht angelegt werden: {fehler}",
            ),
        ) from fehler

    for eintrag in sicherung.liste():
        if eintrag.name == pfad.name:
            return _als_public(eintrag)
    raise HTTPException(status_code=500, detail=meldungen.meldung("backup_failed", "Fehlgeschlagen."))


@router.post("/{name}/archiv")
def archiv(name: str, payload: ArchivAnfordern, _: AdminUser) -> Response:
    """Die Sicherung als verschluesseltes ZIP ausliefern.

    ⚠️ Bewusst ``POST`` und nicht ``GET``: Das Passwort gehoert in den Rumpf.
    In einer Adresse landete es im Verlauf des Browsers und in jedem Protokoll,
    durch das die Anfrage unterwegs kommt.
    """
    try:
        daten = sicherung.archiv(name, payload.passwort)
    except FileNotFoundError as fehler:
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung("backup_missing", "Diese Sicherung gibt es nicht."),
        ) from fehler

    dateiname = name.removesuffix(".db") + ".zip"
    return Response(
        content=daten,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{dateiname}"'},
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def loeschen(name: str, _: AdminUser) -> None:
    """Eine Sicherung entfernen - samt Steckbrief.

    ⚠️ Ein Loeschen-Knopf neben Sicherungen nimmt genau das weg, was im
    Ernstfall zaehlt. Die Rueckfrage dazu sitzt in der Oberflaeche; hier wird
    ohne Umschweife geloescht, wenn es bis hierher kommt.
    """
    try:
        pfad = sicherung.datei(name)
    except FileNotFoundError as fehler:
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung("backup_missing", "Diese Sicherung gibt es nicht."),
        ) from fehler

    sicherung.entfernen(pfad)
    logger.info("Backup deleted: %s", pfad.name)


class SteckbriefPublic(BaseModel):
    """Was eine Sicherung ueber sich sagt - vor dem Einspielen zu sehen."""

    version: str
    erstellt: str
    art: str
    kommentar: str
    einspielbar: bool
    grund: str
    #: ⚠️ Steht der Schluessel in der Umgebungsvariablen, gewinnt sie immer -
    #: die Datei aus dem Archiv wird dann ignoriert, und danach laesst sich
    #: kein gespeicherter Zugang mehr entschluesseln. Das gehoert **vor** den
    #: Klick, nicht in eine Protokollzeile, die niemand im richtigen Moment
    #: liest.
    schluessel_aus_umgebung: bool
    #: ⚠️ **Und die andere Haelfte derselben Frage.** Ohne sie meldete die
    #: Vorschau nur, ob *diese* Installation einen Schluessel in der Umgebung
    #: hat - nicht, ob im Archiv einer liegt. Beim schlimmsten Fall, Archiv
    #: ohne Schluessel und Ziel ohne Variable, blieb sie deshalb still,
    #: obwohl danach kein gespeicherter Zugang mehr lesbar ist.
    schluessel_im_archiv: bool


def _steckbrief_public(befund: sicherung.Befund) -> SteckbriefPublic:
    return SteckbriefPublic(
        version=befund.brief.version,
        erstellt=befund.brief.erstellt,
        art=befund.brief.art,
        kommentar=befund.brief.kommentar,
        einspielbar=befund.einspielbar,
        grund=befund.grund,
        schluessel_aus_umgebung=bool(get_settings().secret_key),
        schluessel_im_archiv=befund.schluessel_im_archiv,
    )


def _als_fehler(fehler: sicherung.SicherungFehler) -> HTTPException:
    return HTTPException(status_code=400, detail=meldungen.meldung(fehler.code, fehler.text))


@router.post("/pruefen", response_model=SteckbriefPublic)
async def pruefen(
    _: AdminUser,
    datei: UploadFile = File(...),
    passwort: str = Form(...),
) -> SteckbriefPublic:
    """Nur nachsehen, nichts ersetzen."""
    try:
        befund = sicherung.pruefen(await datei.read(), passwort)
    except sicherung.SicherungFehler as fehler:
        raise _als_fehler(fehler) from fehler
    return _steckbrief_public(befund)


@router.post("/einspielen", response_model=SteckbriefPublic)
async def einspielen(
    _: AdminUser,
    db: DbSession,
    datei: UploadFile = File(...),
    passwort: str = Form(...),
) -> SteckbriefPublic:
    """Eine Sicherung in die **laufende** Installation einspielen.

    ⚠️ Danach ist niemand mehr angemeldet - auch der nicht, der es ausgeloest
    hat. Mit ``secret.key`` wechselt der Schluessel, mit dem die Sitzungs-Token
    unterschrieben sind. Das ist richtig so: Die Konten aus der Sicherung sind
    andere als die von eben.
    """
    # ⚠️ Siehe den Einrichtungsweg: Die Sitzung dieser Anfrage haelt die
    # Datenbank offen, die gleich ersetzt wird. Erst lesen, dann schliessen,
    # dann tauschen.
    daten = await datei.read()
    db.close()

    try:
        # ⚠️ In den Arbeitsthread, nicht auf die Ereignisschleife: das
        # Einspielen blockiert komplett (Sicherung vorher, Dateitausch,
        # ``init_db`` samt einmaligem Umstellungs-VACUUM - gemessen 0,44 bis
        # 0,59 s, inhaltsproportional nach oben offen). Solange es auf der
        # Schleife liefe, wuerde keine andere Anfrage bedient. Im Thread darf
        # es auch blockierend auf ``_pflege_schloss`` warten, falls gerade
        # eine Taktrunde laeuft.
        befund = await asyncio.to_thread(sicherung.wiederherstellen, daten, passwort)
    except sicherung.SicherungFehler as fehler:
        raise _als_fehler(fehler) from fehler
    return _steckbrief_public(befund)
