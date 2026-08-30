"""Das Admin-Dashboard - der eine Ort, an dem der Betreiber nachsieht.

⚠️ **Hier steht, was zu tun ist. Nicht, wie es sich entwickelt.** Die
Statistik-Seite beantwortet "wie laeuft es", dieses Dashboard "was ist
kaputt". Ohne diese Trennung entstehen zwei Seiten, die dasselbe zeigen, und
man macht keine von beiden mehr auf.

Zwei Sorten Zahl, und der Unterschied ist wichtig:

* **Befunde** sind Ausnahmen - etwas stimmt nicht. Sie kommen aus
  ``services/befunde.py`` und verschwinden, sobald es behoben ist.
* **Handlungszahlen** stehen immer da. "Drei Anfragen warten auf Freigabe" ist
  kein Problem, sondern Alltag; als Befund waere es ein Daueralarm, der die
  echten Befunde daneben entwertet.

⚠️ **Durchgehend Administratoren.** Deshalb wird der Router in ``main.py`` ohne
``NUR_ERWACHSENE`` eingehaengt - genauso wie ``qualitaetsprofile``. Ein
Kinderkonto ist kein Administrator, und ``require_admin`` zaehlt fuer
``test_child_permissions`` als Wache.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from ..deps import AdminUser, DbSession
from ..models import (
    MediaRequest,
    RequestStatus,
    SpeicherVerlauf,
    StorageEntry,
    TicketStatus,
    TitleRating,
)
from ..services import befunde as befunde_service
from ..services import tickets as tickets_service
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


class BefundPublic(BaseModel):
    """Ein Befund, wie ihn die Oberflaeche bekommt.

    ⚠️ **Kein fertiger Satz.** Geliefert werden Kennung und Werte; den Text
    baut das Frontend aus ``befund.<kennung>.titel`` und
    ``befund.<kennung>.folge``. Ein hier zusammengesetzter Satz waere in der
    Sprache des Servers und nicht in der des Lesers.
    """

    #: Eindeutig auch dann, wenn dieselbe Pruefung mehrfach anschlaegt.
    schluessel: str
    kennung: str
    schwere: str
    bereich: str
    werte: dict
    ziel: str | None
    #: Wortlaut von Radarr/Sonarr - bleibt englisch, es ist ihre Aussage.
    wortlaut: str | None


class HandlungsZahlen(BaseModel):
    """Was auf jemanden wartet - auch wenn nichts kaputt ist."""

    freigaben_offen: int
    laeuft: int
    tickets_offen: int
    rueckmeldungen_offen: int


class VerlaufsPunkt(BaseModel):
    """Ein Tag im Speicher-Verlauf."""

    tag: str
    belegt_bytes: int
    frei_bytes: int


class Datentraeger(BaseModel):
    """Wie voll die Platte ist - und wie viel davon ueberhaupt Medien sind.

    ⚠️ **Drei Zahlen und nicht zwei, und das ist der ganze Punkt.** "Belegt
    gegen frei" waere die naheliegende Aufteilung und eine Halbwahrheit: Auf
    demselben Traeger liegen Sicherungen, Fotos, das Betriebssystem. Wer nur
    belegt/frei zeigt, behauptet stillschweigend, der belegte Platz sei die
    Mediathek - und ein Betreiber, der aufraeumen will, sucht dann an der
    falschen Stelle. Dieselbe Ueberlegung steht schon im Docstring von
    ``storage.freier_platz``, wo aus genau diesem Grund bewusst **keine**
    Gesamtgroesse nach aussen gegeben wird.
    """

    gesamt_bytes: int
    frei_bytes: int
    #: Was Nexview als Medien kennt - Hausbestand und zugerechnete Posten.
    medien_bytes: int


class DashboardStand(BaseModel):
    befunde: list[BefundPublic]
    #: Anzahl je Schwere - fuer das Abzeichen am Menuepunkt.
    zaehler: dict[str, int]
    zahlen: HandlungsZahlen
    #: Der Speicher-Verlauf, aeltester Punkt zuerst. Leer heisst: noch nicht
    #: genug gemessen - die Oberflaeche zeigt dann kein Diagramm statt eines
    #: leeren Rahmens.
    verlauf: list[VerlaufsPunkt]
    #: ``None`` heisst: noch nichts gemessen. Die Oberflaeche zeigt dann
    #: keinen Ring statt eines leeren Kreises.
    traeger: Datentraeger | None


def _als_public(befund: befunde_service.Befund) -> BefundPublic:
    return BefundPublic(
        schluessel=befund.schluessel,
        kennung=befund.kennung,
        schwere=befund.schwere.value,
        bereich=befund.bereich.value,
        werte=befund.werte,
        ziel=befund.ziel,
        wortlaut=befund.wortlaut,
    )


def _handlungszahlen(db, admin) -> HandlungsZahlen:
    freigaben = (
        db.scalar(
            select(func.count(MediaRequest.id)).where(
                MediaRequest.status == RequestStatus.pending_approval
            )
        )
        or 0
    )
    laeuft = (
        db.scalar(
            select(func.count(MediaRequest.id)).where(
                MediaRequest.status.in_(
                    (RequestStatus.approved, RequestStatus.searching)
                )
            )
        )
        or 0
    )
    # Ueber den Dienst, nicht ueber eine eigene Abfrage: Die Sichtbarkeitsregel
    # fuer Tickets steht genau einmal, und dabei bleibt es.
    offene_tickets = tickets_service.sichtbare_tickets(
        db, admin, status=TicketStatus.open
    )
    # Kommentare ohne Antwort. Veraltete zaehlen nicht mit - ein Urteil ueber
    # eine Datei, die Radarr laengst ersetzt hat, ist keine offene Aufgabe.
    rueckmeldungen = (
        db.scalar(
            select(func.count(TitleRating.id)).where(
                TitleRating.outdated.is_(False),
                TitleRating.comment.is_not(None),
                TitleRating.comment != "",
                TitleRating.reply.is_(None),
            )
        )
        or 0
    )
    return HandlungsZahlen(
        freigaben_offen=freigaben,
        laeuft=laeuft,
        tickets_offen=len(offene_tickets),
        rueckmeldungen_offen=rueckmeldungen,
    )


@router.get("/dashboard", response_model=DashboardStand)
def dashboard(admin: AdminUser, db: DbSession) -> DashboardStand:
    """Alles fuers Betreiber-Dashboard in einem Aufruf.

    Ein Aufruf und nicht drei: Die Seite ist der Ort, an dem man in zehn
    Sekunden wissen will, ob etwas kaputt ist. Drei Anfragen, die nacheinander
    eintrudeln, machen daraus ein Ruckeln.
    """
    settings = load_settings(db)
    gefunden = befunde_service.sammeln(db, settings)
    return DashboardStand(
        befunde=[_als_public(b) for b in gefunden],
        zaehler=befunde_service.zaehlen(gefunden),
        zahlen=_handlungszahlen(db, admin),
        verlauf=_verlauf(db),
        traeger=_traeger(db),
    )


def _traeger(db) -> Datentraeger | None:
    """Der juengste Messpunkt plus das, was Nexview an Medien kennt."""
    juengster = db.scalar(
        select(SpeicherVerlauf).order_by(SpeicherVerlauf.tag.desc()).limit(1)
    )
    if juengster is None:
        return None
    gesamt = juengster.belegt_bytes + juengster.frei_bytes
    if gesamt <= 0:
        return None
    medien = db.scalar(select(func.sum(StorageEntry.size_bytes))) or 0
    return Datentraeger(
        gesamt_bytes=gesamt,
        frei_bytes=juengster.frei_bytes,
        # Gedeckelt: Der Speicher-Abgleich und die Platten-Messung laufen zu
        # verschiedenen Zeitpunkten, und ein Ring, dessen Stuecke zusammen
        # mehr als hundert Prozent ergeben, sieht schlicht kaputt aus.
        medien_bytes=min(int(medien), juengster.belegt_bytes),
    )


#: So weit reicht die Kurve zurueck. Zwei Monate zeigen eine Entwicklung, ohne
#: dass einzelne Tage zu Strichen zusammenschrumpfen.
VERLAUF_TAGE = 60


def _verlauf(db) -> list[VerlaufsPunkt]:
    """Die Tagespunkte, aeltester zuerst.

    Aufsteigend, weil eine Kurve von links nach rechts gelesen wird - die
    Abfrage sortiert absteigend, um die *juengsten* zu bekommen.
    """
    zeilen = list(
        db.scalars(
            select(SpeicherVerlauf)
            .order_by(SpeicherVerlauf.tag.desc())
            .limit(VERLAUF_TAGE)
        )
    )
    return [
        VerlaufsPunkt(
            tag=z.tag, belegt_bytes=z.belegt_bytes, frei_bytes=z.frei_bytes
        )
        for z in reversed(zeilen)
    ]


@router.get("/befunde", response_model=list[BefundPublic])
def liste(
    admin: AdminUser,
    db: DbSession,
    bereich: str | None = Query(
        default=None,
        description="dienste | platz | nachschub | bibliothek | betrieb",
    ),
) -> list[BefundPublic]:
    """Die Befunde eines Bereichs - fuer die Analyse-Seite.

    Ein unbekannter Bereich liefert eine leere Liste statt eines Fehlers: Die
    Namen wandern mit der Oberflaeche, und ein Tippfehler in der Adresse soll
    keine kaputte Seite ergeben.
    """
    settings = load_settings(db)
    gewaehlt = None
    if bereich:
        try:
            gewaehlt = befunde_service.Bereich(bereich)
        except ValueError:
            return []
    return [
        _als_public(b)
        for b in befunde_service.sammeln(db, settings, bereich=gewaehlt)
    ]
