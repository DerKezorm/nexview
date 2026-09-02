"""Regeln verwalten - ausschliesslich fuer Administratoren.

``AdminUser`` an jedem Endpunkt, wie bei der Sperrliste und aus demselben
Grund: Ein Entscheider entscheidet ueber die Anfrage, die vor ihm liegt. Wie
mit einer ganzen Art von Titeln umgegangen wird, ist eine Grundsatzfrage - die
beantwortet der Betreiber.

⚠️ **Die Reihenfolge ist Teil der Bedeutung**, nicht Kosmetik. Deshalb gibt es
eine eigene Adresse dafuer, die die ganze Liste auf einmal entgegennimmt: Wer
Positionen einzeln setzt, hat zwischendurch zwangslaeufig Zustaende mit zwei
gleichen oder einer fehlenden Position, und in genau diesem Moment entscheidet
eine Anfrage falsch.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field

from .. import meldungen
from ..deps import AdminUser, DbSession
from ..models import Regel, RegelEntscheidung
from ..services import regeln as regeln_dienst

router = APIRouter(prefix="/api/admin/regeln", tags=["regeln"])


class BedingungIn(BaseModel):
    """Eine Bedingung. Entweder ``von``/``bis`` oder ``werte`` - nie beides.

    Geprueft wird im Dienst (``regeln.bedingungen_pruefen``) und nicht hier:
    Dort steht, welche Felder es gibt, und eine zweite Liste im Schema waere
    die naechste, die veraltet.
    """

    feld: str = Field(max_length=40)
    von: float | None = None
    bis: float | None = None
    werte: list[str] | None = None


class RegelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    aktiv: bool = True
    bedingungen: list[BedingungIn]
    entscheidung: RegelEntscheidung
    hausbestand: bool = False
    begruendung: str = Field(default="", max_length=500)
    trotzdem_fragen: bool = False


class RegelOut(BaseModel):
    id: int
    position: int
    name: str
    aktiv: bool
    bedingungen: list[dict]
    entscheidung: RegelEntscheidung
    hausbestand: bool
    begruendung: str
    trotzdem_fragen: bool
    created_at: datetime


class ReihenfolgeIn(BaseModel):
    """Die vollstaendige Liste der Regel-Nummern in ihrer neuen Reihenfolge."""

    reihenfolge: list[int]


class Feld(BaseModel):
    kennung: str
    art: Literal["zahl", "menge"]


def _raus(regel: Regel) -> RegelOut:
    return RegelOut(
        id=regel.id,
        position=regel.position,
        name=regel.name,
        aktiv=regel.aktiv,
        bedingungen=list(regel.bedingungen or []),
        entscheidung=regel.entscheidung,
        hausbestand=regel.hausbestand,
        begruendung=regel.begruendung or "",
        trotzdem_fragen=regel.trotzdem_fragen,
        created_at=regel.created_at,
    )


def _uebernehmen(regel: Regel, daten: RegelIn) -> None:
    """Die Eingabe auf die Regel schreiben - nach der Pruefung im Dienst."""
    try:
        bedingungen = regeln_dienst.bedingungen_pruefen(
            [b.model_dump(exclude_none=False) for b in daten.bedingungen]
        )
    except regeln_dienst.RegelFehler as fehler:
        # ⚠️ Der Text der Ausnahme sagt, *welche* Bedingung nicht taugt. Er
        # geht als ``grund`` mit hinaus, damit die Oberflaeche nicht raten
        # muss, was der Administrator falsch gemacht hat.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung(
                "regel_bedingung_ungueltig",
                str(fehler),
            ),
        ) from fehler

    regel.name = daten.name.strip()
    regel.aktiv = daten.aktiv
    regel.bedingungen = bedingungen
    regel.entscheidung = daten.entscheidung
    # ⚠️ Beide Zusatzfelder gelten nur fuer je eine Entscheidung. Sie hier
    # zurueckzusetzen statt sie nur zu ignorieren, haelt die Datenbank ehrlich:
    # Sonst stuende an einer ablehnenden Regel "Hausbestand: ja", und wer das
    # liest, glaubt es.
    regel.hausbestand = daten.hausbestand and daten.entscheidung == RegelEntscheidung.freigeben
    regel.trotzdem_fragen = (
        daten.trotzdem_fragen and daten.entscheidung == RegelEntscheidung.ablehnen
    )
    regel.begruendung = daten.begruendung.strip() or None


@router.get("/felder", response_model=list[Feld])
def felder(user: AdminUser) -> list[Feld]:
    """Welche Felder es gibt und welcher Art sie sind.

    ⚠️ **Damit die Oberflaeche keine eigene Liste fuehrt.** Ein zweites
    Verzeichnis derselben Felder im Frontend waere spaetestens beim naechsten
    neuen Feld falsch, und der Fehler faellt erst auf, wenn eine Regel nicht
    greift.
    """
    return [
        Feld(kennung=k, art="zahl" if k in regeln_dienst.ZAHLENFELDER else "menge")
        for k in sorted(regeln_dienst.FELDER)
    ]


@router.get("", response_model=list[RegelOut])
def liste(user: AdminUser, db: DbSession) -> list[RegelOut]:
    return [_raus(r) for r in regeln_dienst.geordnet(db)]


@router.post("", response_model=RegelOut, status_code=status.HTTP_201_CREATED)
def anlegen(daten: RegelIn, user: AdminUser, db: DbSession) -> RegelOut:
    vorhandene = regeln_dienst.geordnet(db)
    regel = Regel(entscheidung=daten.entscheidung, name=daten.name)
    _uebernehmen(regel, daten)
    # Ans Ende: Eine neue Regel soll nichts ueberholen, was schon da ist.
    regel.position = (vorhandene[-1].position + 1) if vorhandene else 0
    db.add(regel)
    db.commit()
    db.refresh(regel)
    return _raus(regel)


# ⚠️ **Diese Adresse muss VOR ``/{regel_id}`` stehen.** FastAPI nimmt die
# erste passende Route, und ``/{regel_id}`` passt auch auf
# ``/reihenfolge`` - die Antwort waere dann ein 422 „das ist keine Zahl"
# statt der Umsortierung. Beim ersten Bau stand sie darunter.
@router.put("/reihenfolge", response_model=list[RegelOut])
def reihenfolge(daten: ReihenfolgeIn, user: AdminUser, db: DbSession) -> list[RegelOut]:
    """Die ganze Liste neu ordnen, in einem Zug.

    ⚠️ **Vollstaendig oder gar nicht.** Fehlt eine Regel in der Liste, wird
    abgelehnt statt sie ans Ende zu haengen: Eine unvollstaendige Liste ist
    fast immer ein Fehler beim Aufrufer, und ihn stillschweigend zu deuten
    hiesse, die Reihenfolge zu raten - und die ist hier die Bedeutung.
    """
    vorhandene = {r.id: r for r in regeln_dienst.geordnet(db)}
    if sorted(daten.reihenfolge) != sorted(vorhandene):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung(
                "regel_reihenfolge_unvollstaendig",
                "Die Reihenfolge muss genau die vorhandenen Regeln nennen, "
                "jede genau einmal.",
            ),
        )
    for platz, regel_id in enumerate(daten.reihenfolge):
        vorhandene[regel_id].position = platz
    db.commit()
    return [_raus(r) for r in regeln_dienst.geordnet(db)]


@router.put("/{regel_id}", response_model=RegelOut)
def aendern(
    regel_id: Annotated[int, Path(ge=1)],
    daten: RegelIn,
    user: AdminUser,
    db: DbSession,
) -> RegelOut:
    regel = db.get(Regel, regel_id)
    if regel is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=meldungen.meldung("regel_nicht_gefunden", "Diese Regel gibt es nicht."),
        )
    _uebernehmen(regel, daten)
    db.commit()
    db.refresh(regel)
    return _raus(regel)


@router.delete("/{regel_id}", status_code=status.HTTP_204_NO_CONTENT)
def loeschen(
    regel_id: Annotated[int, Path(ge=1)], user: AdminUser, db: DbSession
) -> None:
    regel = db.get(Regel, regel_id)
    if regel is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=meldungen.meldung("regel_nicht_gefunden", "Diese Regel gibt es nicht."),
        )
    db.delete(regel)
    db.commit()
