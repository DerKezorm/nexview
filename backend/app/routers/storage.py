"""Speicher-Belegung ansehen.

Stufe 1: **nur lesen.** Es gibt hier nichts, was etwas begrenzt, verschiebt
oder loescht - diese Endpunkte beantworten ausschliesslich die Frage
"wer belegt wie viel".
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import User
from ..services import storage
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


class StorageMine(BaseModel):
    """Was der angemeldete Nutzer belegt."""

    used_bytes: int
    items: int
    # Abgegeben, aber noch nicht entschieden. Zaehlt weiter mit - deshalb
    # getrennt ausgewiesen und nicht abgezogen.
    pending_bytes: int
    entries: list[StoragePosten]


class StorageAnteil(BaseModel):
    """Eine Zeile der Verteilung."""

    # NULL steht fuer den Hausbestand - der gehoert niemandem.
    user_id: int | None
    username: str | None
    display_name: str | None
    used_bytes: int
    items: int


class StorageUebersicht(BaseModel):
    """Wie sich der Platz auf die Beteiligten verteilt."""

    total_bytes: int
    house_bytes: int
    house_items: int
    shares: list[StorageAnteil]


def _als_posten(posten: storage.Posten) -> StoragePosten:
    return StoragePosten(**vars(posten))


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
def eigener_speicher(user: CurrentUser, db: DbSession) -> StorageMine:
    """Der eigene Stand samt Einzelposten, das Groesste zuerst.

    Die Reihenfolge ist der Zweck der Liste: Wer Platz schaffen soll, muss
    zuerst sehen, wo der Platz steckt.
    """
    _muss_eingeschaltet_sein(db)
    stand = storage.kontostand(db, user.id)
    return StorageMine(
        used_bytes=stand.used_bytes,
        items=stand.items,
        pending_bytes=stand.pending_bytes,
        entries=[_als_posten(p) for p in storage.posten_fuer(db, user.id)],
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

    for user_id, stand in storage.verteilung(db):
        gesamt += stand.used_bytes
        person = db.get(User, user_id) if user_id is not None else None
        anteile.append(
            StorageAnteil(
                user_id=user_id,
                username=person.username if person else None,
                display_name=person.display_name if person else None,
                used_bytes=stand.used_bytes,
                items=stand.items,
            )
        )

    return StorageUebersicht(
        total_bytes=gesamt,
        house_bytes=haus.used_bytes,
        house_items=haus.items,
        shares=anteile,
    )
