"""Die Hausordnung: lesen, quittieren, verwalten.

Drei Gruppen von Pfaden, mit drei verschiedenen Zutrittsregeln:

* ``GET /api/hausordnung`` und ``POST /api/hausordnung/gelesen`` - fuer jedes
  erwachsene Konto. Der Text ist nur sichtbar, wenn er **veroeffentlicht** ist;
  ein Entwurf gehoert dem Betreiber allein.
* ``GET /api/hausordnung/bild/{name}`` - dasselbe, aber fuer Bilder.
* ``/api/hausordnung/verwaltung`` und ``/api/hausordnung/bilder`` - nur fuer
  Administratoren. Hier ist auch der Entwurf sichtbar.

⚠️ **Kinderkonten kommen hier nicht vor.** Der ganze Router haengt an
``AdultUser``; die Hausordnung richtet sich an die, die anfragen und Speicher
verbrauchen. Ein Kind saehe sie ohnehin nicht - sein Rahmen hat weder den
Knopf noch die Fusszeile -, aber die Grenze steht trotzdem im Server und nicht
nur in der Oberflaeche.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import meldungen
from ..deps import AdminUser, AdultUser, DbSession
from ..models import Hausordnung, Role, User, utcnow
from ..services import hausordnung_bilder

router = APIRouter(prefix="/api/hausordnung", tags=["hausordnung"])

logger = logging.getLogger("nexview.hausordnung")

#: Grenze fuer den Text. Grosszuegig, aber vorhanden - ohne sie kann ein
#: verunglueckter Einfuegevorgang die Datenbank aufblaehen.
MAX_ZEICHEN = 50_000


class HausordnungOeffentlich(BaseModel):
    """Was ein gewoehnliches Konto zu sehen bekommt."""

    titel: str
    inhalt: str
    fassung: int
    quittierbar: bool
    #: Welche Fassung dieses Konto entschieden hat - ``None`` heisst "noch nie".
    gelesen: int | None
    #: Wie entschieden wurde: ``True`` akzeptiert, ``False`` abgelehnt.
    akzeptiert: bool | None


class HausordnungVerwaltung(BaseModel):
    """Die Sicht des Administrators - mit Entwurf und Zustand."""

    titel: str
    inhalt: str
    fassung: int
    quittierbar: bool
    veroeffentlicht: bool
    aktualisiert_am: datetime | None
    #: Wie viele Konten die Hausordnung angeht. Steht neben dem Haken "alle
    #: muessen erneut lesen", damit die Folge in Zahlen dasteht.
    betroffene_konten: int


class HausordnungSpeichern(BaseModel):
    titel: str = Field(default="", max_length=120)
    inhalt: str = Field(default="", max_length=MAX_ZEICHEN)
    quittierbar: bool = True
    veroeffentlicht: bool = False
    #: Setzt die Quittungen aller Konten zurueck - ausdrueckliche Entscheidung
    #: des Betreibers, nicht Folge jedes Speicherns. Ein berichtigter
    #: Tippfehler soll nicht bei allen den Hinweis erneut aufpoppen lassen.
    erneut_lesen: bool = False


class BildZeile(BaseModel):
    name: str
    bytes: int


def _laden(db: DbSession) -> Hausordnung | None:
    return db.get(Hausordnung, 1)


#: Wen die Hausordnung angeht.
#:
#: ⚠️ **Administratoren stehen nicht dabei.** Sie schreiben die Regeln - sie
#: sich selbst vorlegen zu lassen, waere Zeremonie: ein Punkt am Knopf, der an
#: den eigenen Text erinnert, und nach jeder neuen Fassung wieder. Entscheider
#: (``approver``) sind ausdruecklich **nicht** ausgenommen; sie entscheiden
#: ueber Anfragen, nicht ueber die Regeln.
#:
#: Kinderkonten fehlen aus einem anderen Grund: Sie bekommen die Hausordnung
#: nie zu sehen.
UNBETEILIGT = (Role.child, Role.admin)


def _geht_es_an(user: User) -> bool:
    return user.role not in UNBETEILIGT


def _erwachsene_konten(db: DbSession) -> int:
    return (
        db.scalar(
            select(func.count(User.id)).where(
                User.role.notin_(UNBETEILIGT), User.is_active.is_(True)
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Fuer alle
# ---------------------------------------------------------------------------


@router.get("", response_model=HausordnungOeffentlich)
def lesen(user: AdultUser, db: DbSession) -> HausordnungOeffentlich:
    """Den Text lesen - nur wenn es einen veroeffentlichten gibt."""
    ordnung = _laden(db)
    if ordnung is None or not ordnung.veroeffentlicht:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=meldungen.meldung(
                "hausordnung_missing",
                "Es ist keine Hausordnung hinterlegt.",
            ),
        )
    return HausordnungOeffentlich(
        titel=ordnung.titel,
        inhalt=ordnung.inhalt,
        fassung=ordnung.fassung,
        quittierbar=ordnung.quittierbar,
        gelesen=user.hausordnung_gelesen,
        akzeptiert=user.hausordnung_akzeptiert,
    )


class Entscheidung(BaseModel):
    """Angenommen oder abgelehnt - beides ist eine Entscheidung."""

    akzeptiert: bool = True


class UebersichtZeile(BaseModel):
    """Ein Konto in der Uebersicht des Betreibers."""

    user_id: int
    username: str
    display_name: str | None
    avatar_url: str | None
    role: Role
    #: ``True`` akzeptiert, ``False`` abgelehnt, ``None`` noch offen.
    akzeptiert: bool | None
    entschieden_am: datetime | None
    #: Welche Fassung entschieden wurde. Ist sie aelter als die aktuelle,
    #: gilt das Konto wieder als offen - es hat eine andere gelesen.
    fassung: int | None


@router.post("/entscheidung", status_code=status.HTTP_204_NO_CONTENT)
def entscheiden(payload: Entscheidung, user: AdultUser, db: DbSession) -> None:
    """"Akzeptieren" oder "Ablehnen".

    ⚠️ **Festgehalten wird immer die laufende Fassung**, nicht die, die
    gerade auf dem Bildschirm stand. Dieselbe Regel wie beim "Was ist
    neu"-Fenster, und aus demselben Grund: Alles andere waere eine Falle.

    ⚠️ **Ablehnen sperrt nichts.** Es ist eine Auskunft an den Betreiber, der
    dann selbst entscheidet - nachfragen, stilllegen, oder es auf sich beruhen
    lassen. Ein Automatismus haette den falschen Preis: Ein versehentlicher
    Klick sperrte jemanden aus, und gemerkt haette es niemand.
    """
    ordnung = _laden(db)
    if ordnung is None or not ordnung.veroeffentlicht:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=meldungen.meldung(
                "hausordnung_missing",
                "Es ist keine Hausordnung hinterlegt.",
            ),
        )
    if not ordnung.quittierbar:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "hausordnung_not_ackable",
                "Über diese Hausordnung lässt sich nicht entscheiden.",
            ),
        )

    user.hausordnung_gelesen = ordnung.fassung
    user.hausordnung_gelesen_am = utcnow()
    user.hausordnung_akzeptiert = payload.akzeptiert
    db.commit()


@router.get("/uebersicht", response_model=list[UebersichtZeile])
def uebersicht(admin: AdminUser, db: DbSession) -> list[UebersichtZeile]:
    """Wer hat entschieden - und wie?

    Kinderkonten und Administratoren stehen nicht dabei: Die einen bekommen
    die Hausordnung nie zu sehen, die anderen schreiben sie. Eine Zeile, die
    dauerhaft "offen" sagt, waere in beiden Faellen kein Hinweis, sondern
    Laerm.

    ⚠️ **Eine aeltere Fassung gilt als offen.** Wer Fassung 1 akzeptiert hat
    und inzwischen steht Fassung 2, hat ueber den heutigen Text nicht
    entschieden. Die Zeile nennt trotzdem, was er damals gewaehlt hat - sonst
    saehe eine Neuveroeffentlichung so aus, als haette nie jemand zugestimmt.
    """
    ordnung = _laden(db)
    aktuell = ordnung.fassung if ordnung else None

    zeilen = db.scalars(
        select(User).where(User.role.notin_(UNBETEILIGT)).order_by(User.created_at)
    )
    ergebnis: list[UebersichtZeile] = []
    for konto in zeilen:
        veraltet = (
            aktuell is not None
            and konto.hausordnung_gelesen is not None
            and konto.hausordnung_gelesen < aktuell
        )
        ergebnis.append(
            UebersichtZeile(
                user_id=konto.id,
                username=konto.username,
                display_name=konto.display_name,
                avatar_url=konto.avatar_url,
                role=konto.role,
                akzeptiert=None if veraltet else konto.hausordnung_akzeptiert,
                entschieden_am=None if veraltet else konto.hausordnung_gelesen_am,
                fassung=konto.hausordnung_gelesen,
            )
        )
    return ergebnis


@router.get("/bild/{name}", include_in_schema=False)
def bild(name: str) -> Response:
    """Ein Bild aus der Hausordnung ausliefern - **ohne Anmeldung.**

    ⚠️ **Das ist keine Nachlaessigkeit, sondern die einzige Bauart, die
    funktioniert.** Der erste Versuch verlangte ein angemeldetes Konto, und
    das Ergebnis war ein zerbrochenes Bild in jeder Hausordnung: Ein
    ``<img>``-Element schickt keinen ``Authorization``-Header mit, und das
    Sitzungs-Cookie gilt nur unter ``/api/auth`` (siehe ``services/sitzung``).
    Der Browser hat an dieser Adresse also nichts vorzuweisen - egal, wer
    davorsitzt.

    Profilbilder haengen aus demselben Grund offen (``routers/users``), und
    der Schutz ist derselbe: Der Dateiname besteht aus 32 Zufalls-Hexziffern,
    also 128 Bit. Er ist nicht zu erraten, und er steht nirgends, wo ihn
    jemand ohne Zugang zu sehen bekaeme.

    ⚠️ **Was damit ausdruecklich nicht mehr gilt:** Auch die Bilder eines
    unveroeffentlichten Entwurfs sind abrufbar, wenn jemand den Namen kennt.
    Der **Text** bleibt geschuetzt; das Bild allein sagt ohne ihn wenig.

    Der Test dazu prueft den Abruf **ohne jeden Kopfzeilen-Zusatz** - genau
    so, wie ein Browser ihn stellt. Ein Test mit angemeldetem Client haette
    den Fehler nie gefunden; er hat ihn auch nicht gefunden.
    """
    try:
        inhalt, inhaltstyp = hausordnung_bilder.lesen(name)
    except hausordnung_bilder.BildFehler as fehler:
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung("hausordnung_image_missing", fehler.message),
        ) from fehler

    return Response(
        content=inhalt,
        media_type=inhaltstyp,
        headers={
            "Cache-Control": "private, max-age=300",
            # Der Browser soll den Inhalt als nichts anderes deuten als das,
            # was wir sagen.
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------------------------
# Nur fuer Administratoren
# ---------------------------------------------------------------------------


@router.get("/verwaltung", response_model=HausordnungVerwaltung)
def verwaltung_lesen(admin: AdminUser, db: DbSession) -> HausordnungVerwaltung:
    """Der Stand fuer den Editor - auch als Entwurf."""
    ordnung = _laden(db)
    if ordnung is None:
        return HausordnungVerwaltung(
            titel="",
            inhalt="",
            fassung=1,
            quittierbar=True,
            veroeffentlicht=False,
            aktualisiert_am=None,
            betroffene_konten=_erwachsene_konten(db),
        )
    return HausordnungVerwaltung(
        titel=ordnung.titel,
        inhalt=ordnung.inhalt,
        fassung=ordnung.fassung,
        quittierbar=ordnung.quittierbar,
        veroeffentlicht=ordnung.veroeffentlicht,
        aktualisiert_am=ordnung.aktualisiert_am,
        betroffene_konten=_erwachsene_konten(db),
    )


@router.put("/verwaltung", response_model=HausordnungVerwaltung)
def verwaltung_speichern(
    payload: HausordnungSpeichern, admin: AdminUser, db: DbSession
) -> HausordnungVerwaltung:
    """Speichern - und nur auf ausdrueckliche Anweisung alle erneut fragen."""
    if payload.veroeffentlicht and not payload.inhalt.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung(
                "hausordnung_empty",
                "Eine leere Hausordnung lässt sich nicht veröffentlichen.",
            ),
        )

    ordnung = _laden(db)
    if ordnung is None:
        ordnung = Hausordnung(id=1)
        db.add(ordnung)
        # ⚠️ **Das ``flush`` ist Pflicht, nicht Kosmetik.** Die Vorgabewerte
        # des Modells (``fassung=1``) setzt SQLAlchemy erst beim Schreiben -
        # vorher steht dort ``None``. Wer beim allerersten Speichern gleich
        # "alle muessen erneut lesen" ankreuzte, lief deshalb in ein
        # ``NoneType + int`` und bekam einen 500er; gespeichert wurde nichts.
        # Gemeldet aus dem Betrieb, beim ersten Anlegen ueberhaupt.
        db.flush()

    ordnung.titel = payload.titel.strip()
    ordnung.inhalt = payload.inhalt
    ordnung.quittierbar = payload.quittierbar
    ordnung.veroeffentlicht = payload.veroeffentlicht
    ordnung.aktualisiert_von = admin.id
    if payload.erneut_lesen:
        ordnung.fassung += 1
        logger.info(
            "House rules updated by %r; everyone has to read them again (version %d)",
            admin.username,
            ordnung.fassung,
        )

    db.commit()
    db.refresh(ordnung)

    return verwaltung_lesen(admin, db)


@router.delete("/verwaltung", status_code=status.HTTP_204_NO_CONTENT)
def verwaltung_loeschen(admin: AdminUser, db: DbSession) -> None:
    """Die Hausordnung ganz entfernen - samt aller Bilder.

    Der Knopf unten rechts und der Fusszeilen-Verweis verschwinden damit
    ueberall. Die Quittungen an den Konten bleiben stehen; sie kosten nichts
    und waeren beim naechsten Anlegen ohnehin ueberholt (die Fassung faengt
    dann wieder bei 1 an).
    """
    ordnung = _laden(db)
    if ordnung is not None:
        db.delete(ordnung)
        db.commit()
    for bild_ in hausordnung_bilder.alle():
        hausordnung_bilder.loeschen(bild_.name)
    logger.info("House rules deleted by %r", admin.username)


@router.get("/bilder", response_model=list[BildZeile])
def bilder_auflisten(admin: AdminUser) -> list[BildZeile]:
    return [BildZeile(name=b.name, bytes=b.bytes) for b in hausordnung_bilder.alle()]


@router.post("/bilder", response_model=BildZeile, status_code=status.HTTP_201_CREATED)
async def bild_hochladen(
    admin: AdminUser, datei: UploadFile = File(...)
) -> BildZeile:
    try:
        abgelegt = hausordnung_bilder.ablegen(await datei.read())
    except hausordnung_bilder.BildFehler as fehler:
        raise HTTPException(
            status_code=400,
            detail=meldungen.meldung("hausordnung_image_rejected", fehler.message),
        ) from fehler
    return BildZeile(name=abgelegt.name, bytes=abgelegt.bytes)


@router.delete("/bilder/{name}", status_code=status.HTTP_204_NO_CONTENT)
def bild_loeschen(name: str, admin: AdminUser) -> None:
    if not hausordnung_bilder.loeschen(name):
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung("hausordnung_image_missing", "Bild nicht gefunden."),
        )
