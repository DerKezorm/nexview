"""Web Push - die Adressen fuer den Browser.

Der Browser holt hier den oeffentlichen Schluessel, meldet sein Abonnement an,
sieht seine Geraete, meldet eines ab und laesst sich eine Probemeldung
schicken. *Wobei* gemeldet wird, steht nicht hier: Das sind die
``push_*``-Haken am Konto, und die gehen wie die Mail-Haken ueber
``PATCH /api/auth/me``.

⚠️ **Der oeffentliche Schluessel ist kein Geheimnis, die Adresse trotzdem
angemeldet.** Er ist dafuer gemacht, im Browser zu stehen. Aber wer sich
anmelden will, ist angemeldet - es gibt keinen Grund fuer eine offene
Adresse, und jede offene Adresse will begruendet sein.

Die englischen Texte fuer ``/docs`` stehen in ``api_texte.py``.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .. import meldungen
from ..deps import CurrentUser, DbSession
from ..services import webpush

router = APIRouter(prefix="/api/push", tags=["push"])


class Schluessel(BaseModel):
    public_key: str


class Abonnement(BaseModel):
    """Was ``PushSubscription.toJSON()`` im Browser liefert - flach."""

    endpoint: str = Field(min_length=1, max_length=2000)
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=64)
    #: Die Sprache der Oberflaeche in diesem Browser. Die Meldung wird auf
    #: dem Server formuliert, und der kennt die eingestellte Sprache sonst
    #: nicht - siehe ``meldungen.py``.
    language: str = "de"


class Geraet(BaseModel):
    id: int
    name: str
    #: Ob das die Anmeldung genau dieses Browsers ist.
    this: bool
    created_at: datetime
    last_success: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    #: Nur in der Antwort auf das Anmelden: Wurden dabei die Haken gesetzt?
    vorbelegt: bool = False


class Probe(BaseModel):
    #: Nur dieses Geraet - fehlt die Adresse, gehen alle.
    endpoint: str | None = None


class ProbeErgebnis(BaseModel):
    ok: bool
    message: str


@router.get("/key", response_model=Schluessel)
def schluessel(user: CurrentUser, db: DbSession) -> Schluessel:
    """Was der Browser als ``applicationServerKey`` braucht."""
    return Schluessel(public_key=webpush.oeffentlicher_schluessel(db))


@router.post("/devices", response_model=Geraet)
def anmelden(payload: Abonnement, request: Request, user: CurrentUser, db: DbSession) -> Geraet:
    """Dieses Geraet nimmt ab jetzt Meldungen an."""
    try:
        ziel, vorbelegt = webpush.anmelden(
            db,
            user,
            endpoint=payload.endpoint,
            p256dh=payload.p256dh,
            auth=payload.auth,
            user_agent=request.headers.get("user-agent", ""),
            language=payload.language,
        )
    except webpush.UnbrauchbareAdresse as fehler:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=meldungen.meldung(
                "push_endpoint_invalid",
                "Mit dieser Adresse ließ sich nichts anfangen.",
            ),
        ) from fehler
    return Geraet(**webpush.zeile(db, ziel, payload.endpoint), vorbelegt=vorbelegt)


@router.get("/devices", response_model=list[Geraet])
def geraete(user: CurrentUser, db: DbSession, endpoint: str = "") -> list[Geraet]:
    """Die angemeldeten Geraete dieses Menschen.

    ``endpoint`` ist der eigene, damit die Liste "dieses" markieren kann. Er
    ist freiwillig: Ein Browser ohne Erlaubnis hat keinen.
    """
    return [Geraet(**webpush.zeile(db, ziel, endpoint)) for ziel in webpush.eigene(db, user)]


@router.delete("/devices/{ziel_id}", status_code=status.HTTP_204_NO_CONTENT)
def abmelden(ziel_id: int, user: CurrentUser, db: DbSession) -> None:
    """Ein Geraet abraeumen - nur ein eigenes."""
    if not webpush.abmelden(db, user, ziel_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=meldungen.meldung("push_device_unknown", "Dieses Gerät ist nicht angemeldet."),
        )


@router.post("/test", response_model=ProbeErgebnis)
async def probe(payload: Probe, user: CurrentUser, db: DbSession) -> ProbeErgebnis:
    """Eine Probemeldung an dieses Geraet - oder an alle."""
    if not webpush.eigene(db, user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung("push_no_device", "Es ist kein Gerät angemeldet."),
        )
    ok, message = await webpush.probe(db, user, payload.endpoint)
    return ProbeErgebnis(ok=ok, message=message)
