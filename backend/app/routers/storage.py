"""Speicher-Belegung ansehen - und einzelne Posten dem Haus zuschlagen.

Was es hier **nicht** gibt: irgendetwas, das eine Datei anfasst. Der einzige
schreibende Vorgang (``…/haus``) aendert ausschliesslich, **wem** ein Titel
zugerechnet wird. Auf der Platte bleibt alles, wie es ist - das ist der ganze
Sinn des Hausbestands: dem Nutzer Luft verschaffen, ohne etwas zu loeschen.
"""

from __future__ import annotations

from datetime import datetime

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import NotificationType, Role, User
from ..services import notify, storage
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/storage", tags=["storage"])


class StoragePosten(BaseModel):
    """Ein belegter Titel oder eine belegte Staffel."""

    id: int
    media_type: str
    tier: str
    tmdb_id: int | None
    tvdb_id: int | None
    season: int | None
    title: str
    size_bytes: int
    state: str
    measured_at: datetime
    # Nur im Hausbestand gefuellt - siehe dort.
    path: str = ""


class StorageMine(BaseModel):
    """Was der angemeldete Nutzer belegt - und was er darf."""

    used_bytes: int
    items: int
    # None heisst unbegrenzt. Wieviel jemand *darf*, steht bewusst hier und
    # nicht am Benutzer-Schema: Es haengt an drei Dingen (Rolle, eigene Zahl,
    # Standardgrenze) und soll nicht an zwei Stellen verschieden gerechnet
    # werden.
    limit_bytes: int | None = None
    # Abgegeben, aber noch nicht entschieden. Zaehlt weiter mit - deshalb
    # getrennt ausgewiesen und nicht abgezogen.
    pending_bytes: int
    # Wieviele Zeilen die Suche trifft, fuer die Seitenzahl.
    matches: int = 0
    # Wieviele Zeilen eine Seite fasst. Kommt mit, damit die Oberflaeche die
    # Zahl nicht spiegeln muss - eine zweite Konstante dort ginge beim naechsten
    # Aendern auseinander.
    per_page: int = storage.JE_SEITE
    entries: list[StoragePosten]


class StorageAnteil(BaseModel):
    """Eine Zeile der Verteilung."""

    # NULL steht fuer den Hausbestand - der gehoert niemandem.
    user_id: int | None
    username: str | None
    display_name: str | None
    used_bytes: int
    items: int
    # Wieviel diese Person **darf**. ``None`` heisst unbegrenzt - und beim
    # Hausbestand heisst es "die Frage stellt sich nicht".
    #
    # Gerechnet wird sie hier und nicht in der Oberflaeche: Sie haengt an drei
    # Dingen (Rolle, eigene Zahl, Standardgrenze), und zwei Rechenwege waeren
    # zwei Wahrheiten.
    limit_bytes: int | None = None


class StorageUebersicht(BaseModel):
    """Wie sich der Platz auf die Beteiligten verteilt."""

    total_bytes: int
    house_bytes: int
    house_items: int
    shares: list[StorageAnteil]


def _als_posten(posten: storage.Posten, *, mit_pfad: bool = False) -> StoragePosten:
    """Einen Posten nach aussen geben.

    **Der Pfad bleibt weg, wenn er nicht ausdruecklich verlangt wird.** Er
    verraet die Ordnerstruktur des Servers; das geht nur den Administrator
    etwas an. Weglassen ist hier die sichere Voreinstellung - so kann eine
    neue Aufrufstelle ihn nicht versehentlich mitliefern.
    """
    felder = vars(posten)
    return StoragePosten(**{**felder, "path": felder["path"] if mit_pfad else ""})


def _muss_eingeschaltet_sein(db) -> None:
    """Ist der Schalter aus, gibt es diese Funktion nicht.

    Bewusst 404 und nicht 403: "Ausgeschaltet" ist kein Rechteproblem,
    sondern heisst, dass es hier nichts gibt. Und die Oberflaeche blendet
    ohnehin alles aus - wer hier ankommt, hat eine Adresse von Hand
    eingetippt oder einen alten Reiter offen.
    """
    if not load_settings(db).storage_enabled:
        raise HTTPException(404, "Speicher-Kontingente sind nicht eingeschaltet.")


@router.get("/me", response_model=StorageMine)
def eigener_speicher(
    user: CurrentUser,
    db: DbSession,
    q: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
) -> StorageMine:
    """Der eigene Stand samt Einzelposten, das Groesste zuerst.

    Die Reihenfolge ist der Zweck der Liste: Wer Platz schaffen soll, muss
    zuerst sehen, wo der Platz steckt.

    **Ohne Pfad.** Wo eine Datei auf dem Server liegt, geht einen gewoehnlichen
    Benutzer nichts an - anders als beim Hausbestand und bei der Admin-Sicht
    auf ein fremdes Konto.
    """
    _muss_eingeschaltet_sein(db)
    stand = storage.kontostand(db, user.id)
    zeilen, treffer = storage.posten_fuer(
        db, user.id, suche=q, seite=page, je_seite=storage.JE_SEITE
    )
    return StorageMine(
        used_bytes=stand.used_bytes,
        items=stand.items,
        limit_bytes=storage.grenze_in_bytes(user, load_settings(db)),
        pending_bytes=stand.pending_bytes,
        matches=treffer,
        per_page=storage.JE_SEITE,
        entries=[_als_posten(p) for p in zeilen],
    )


class StorageHausSeite(BaseModel):
    """Eine Seite des Hausbestands."""

    used_bytes: int
    items: int
    # Wieviele Zeilen die **Suche** trifft - nicht wieviele das Haus haelt.
    # Ohne diese Zahl liesse sich nicht sagen, wieviele Seiten es gibt.
    matches: int
    # Freier Platz auf den Zielordnern und auf wievielen Traegern.
    #
    # **Keine Gesamtkapazitaet** - die kennt Radarr nicht, und "belegt + frei"
    # waere erfunden: Liegt anderes auf demselben Traeger, ist die Platte
    # groesser, ohne dass es jemand saehe.
    free_bytes: int = 0
    free_volumes: int = 0
    per_page: int = storage.JE_SEITE
    entries: list[StoragePosten]


@router.get("/house", response_model=StorageHausSeite)
async def hausbestand(
    admin: AdminUser,
    db: DbSession,
    q: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
) -> StorageHausSeite:
    """Was das Haus haelt - das Groesste zuerst, seitenweise und durchsuchbar.

    **Admin-only, und das aus einem konkreten Grund:** Die Zeilen tragen den
    Pfad auf dem Server. Ein gewoehnlicher Benutzer hat damit nichts zu
    schaffen, und die Ordnerstruktur ist nichts, was er wissen muss.
    """
    _muss_eingeschaltet_sein(db)
    stand = storage.hausbestand(db)
    zeilen, treffer = storage.posten_im_haus(db, suche=q, seite=page)
    return StorageHausSeite(
        used_bytes=stand.used_bytes,
        items=stand.items,
        matches=treffer,
        per_page=storage.JE_SEITE,
        **dict(zip(("free_bytes", "free_volumes"), await storage.freier_platz(load_settings(db)))),
        entries=[_als_posten(p, mit_pfad=True) for p in zeilen],
    )


@router.get("/overview", response_model=StorageUebersicht)
def uebersicht(admin: AdminUser, db: DbSession) -> StorageUebersicht:
    """Wer belegt wie viel - fuer den Administrator.

    Bewusst **admin-only**: Wieviel Platz jemand belegt, ist eine Angabe ueber
    eine Person. Entscheider sehen sie nicht, aus demselben Grund, aus dem sie
    das Ticketcenter anderer nicht sehen.
    """
    _muss_eingeschaltet_sein(db)
    haus = storage.hausbestand(db)
    anteile: list[StorageAnteil] = []
    gesamt = 0

    einstellungen = load_settings(db)

    for user_id, stand in storage.verteilung(db):
        gesamt += stand.used_bytes
        person = db.get(User, user_id) if user_id is not None else None

        # **Administratoren, die nichts halten, bleiben weg.** Ihr Konto steht
        # per Definition auf null - was sie holen, gehoert dem Haus -, und eine
        # Zeile "0 GB von unbegrenzt" ist Beschriftung ohne Aussage.
        #
        # Ausdruecklich nur bei null: Haelt ein Administrator doch etwas, ist
        # das ein Hinweis auf einen Fehler, und der soll sichtbar sein statt
        # weggefiltert. (Der stuendliche Abgleich raeumt solche Posten von
        # selbst ins Haus - bis dahin will man es sehen.)
        #
        # Entscheider bleiben drin: Sie haben sehr wohl ein Kontingent.
        if person is not None and person.role == Role.admin and stand.used_bytes == 0:
            continue

        anteile.append(
            StorageAnteil(
                user_id=user_id,
                username=person.username if person else None,
                display_name=person.display_name if person else None,
                used_bytes=stand.used_bytes,
                items=stand.items,
                limit_bytes=(
                    storage.grenze_in_bytes(person, einstellungen)
                    if person is not None
                    else None
                ),
            )
        )

    return StorageUebersicht(
        total_bytes=gesamt,
        house_bytes=haus.used_bytes,
        house_items=haus.items,
        shares=anteile,
    )


class StorageNutzerSeite(BaseModel):
    """Der Stand **eines** Nutzers, aus Sicht des Administrators."""

    user_id: int
    username: str
    display_name: str | None
    used_bytes: int
    items: int
    # None heisst unbegrenzt - dieselbe Rechnung wie bei ``/me``, damit hier
    # nicht eine zweite Wahrheit entsteht.
    limit_bytes: int | None = None
    pending_bytes: int
    # Wieviele Zeilen die Suche trifft, fuer die Seitenzahl.
    matches: int
    per_page: int = storage.JE_SEITE_KOMPAKT
    entries: list[StoragePosten]


@router.get("/user/{user_id}", response_model=StorageNutzerSeite)
def nutzer_speicher(
    user_id: int,
    admin: AdminUser,
    db: DbSession,
    q: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
) -> StorageNutzerSeite:
    """Was belegt dieser eine Nutzer - das Groesste zuerst.

    **Admin-only**, aus demselben Grund wie die Uebersicht: Wieviel Platz
    jemand belegt, ist eine Angabe ueber eine Person. Entscheider sehen sie
    nicht, genau wie sie fremde Tickets nicht sehen.

    Der Pfad kommt mit - der Administrator soll ja beurteilen koennen, worum
    es geht, bevor er einen Titel dem Haus zuschlaegt.
    """
    _muss_eingeschaltet_sein(db)
    person = db.get(User, user_id)
    if person is None:
        raise HTTPException(404, "Dieses Konto gibt es nicht.")

    stand = storage.kontostand(db, user_id)
    # Kompakter als der Hausbestand: Diese Liste sitzt eingeklappt in einer
    # Karte neben anderen - zwanzig Zeilen waeren dort eine Wand.
    zeilen, treffer = storage.posten_fuer(
        db, user_id, suche=q, seite=page, je_seite=storage.JE_SEITE_KOMPAKT
    )
    return StorageNutzerSeite(
        user_id=person.id,
        username=person.username,
        display_name=person.display_name,
        used_bytes=stand.used_bytes,
        items=stand.items,
        limit_bytes=storage.grenze_in_bytes(person, load_settings(db)),
        pending_bytes=stand.pending_bytes,
        matches=treffer,
        per_page=storage.JE_SEITE_KOMPAKT,
        entries=[_als_posten(p, mit_pfad=True) for p in zeilen],
    )


@router.post("/entries/{posten_id}/haus", response_model=StoragePosten)
def in_den_hausbestand(posten_id: int, admin: AdminUser, db: DbSession) -> StoragePosten:
    """Einen Posten dem Haus zuschlagen - das Kontingent des Nutzers wird frei.

    ⚠️ **Es wird keine Datei angefasst.** Der Titel bleibt liegen, es wechselt
    nur, wem er zugerechnet wird. Genau das ist der Zweck: Der Administrator
    kann sagen "den Klassiker will hier ohnehin jeder sehen, der soll nicht auf
    deinem Konto lasten", ohne dass jemand etwas loeschen muss.

    **Nur Administratoren, ausdruecklich keine Entscheider.** Das ist keine
    Zustaendigkeitsfrage, sondern eine Sicherheitsregel: Entscheider haben
    selbst ein Kontingent *und* dauerhafte Auto-Freigabe. Duerften sie Posten
    ins Haus schieben, waere die Kette geschlossen - selbst anfragen, selbst
    freigeben, selbst ins Haus - und ihr Kontingent damit wirkungslos.

    Der Betroffene bekommt eine Nachricht. Ohne sie saenke seine Zahl grundlos,
    und er wuesste nicht, warum.
    """
    _muss_eingeschaltet_sein(db)
    if storage.posten_von(db, posten_id) is None:
        raise HTTPException(404, "Diesen Posten gibt es nicht.")

    uebernahme = storage.ins_haus(db, posten_id)
    if uebernahme is None:
        raise HTTPException(409, "Dieser Posten gehoert bereits dem Haus.")

    betroffener = db.get(User, uebernahme.vorher_user_id)
    if betroffener is not None:
        notify.create(
            db,
            user=betroffener,
            kind=NotificationType.storage_released,
            message_key="notifications.storageReleased",
            title=uebernahme.posten.title,
        )
    db.commit()
    return _als_posten(uebernahme.posten, mit_pfad=True)
