"""Fernseher koppeln: Code auf dem Bildschirm, Bestaetigen im Browser.

Zwei Seiten, wie bei ``app_kopplung``:

* ``hallo_router`` - ``/api/app/1/hello``, ohne Schluessel: Woran eine App
  nexview im Heimnetz erkennt.
* ``public_router`` - das Geraet, unter ``/api/app/1/device/...`` und ohne
  Schluessel: Code holen und nachfragen. Steht unter der Zusage von
  ``/api/app/1`` (``app1.ZUGESAGT``), Vertrag in ``docs/app-device-pairing.md``.
* ``router`` - die Oberflaeche (Seite ``/link``): Code pruefen, bestaetigen,
  ablehnen. Jede Adresse ``AdultUser``, steht unter keiner Zusage.

Ablauf und Regeln stehen in ``services/geraete_code.py``.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import __version__, meldungen
from ..deps import AdultUser, DbSession
from ..services import anmeldebremse, children, geraete_code
from ..services.geraete_code import GeraeteCodeFehler
from ..services.settings_service import load_settings
from ..vendor.nexoidc import flow

#: Die Seite in der Oberflaeche, auf der man den Code eingibt.
SEITE = "/link"

#: Die Tuer der ``anmeldebremse`` fuer die Code-Eingabe.
TUER = "device_code"

router = APIRouter(prefix="/api/geraete-code", tags=["app"])
public_router = APIRouter(prefix="/api/app/1/device", tags=["app"])
hallo_router = APIRouter(prefix="/api/app/1", tags=["app"])


def _fehler(fehler: GeraeteCodeFehler) -> HTTPException:
    zusatz = {"retry_after": fehler.warten} if fehler.warten else {}
    return HTTPException(
        status_code=fehler.status,
        detail=meldungen.meldung(fehler.code, fehler.text, **zusatz),
        headers={"Retry-After": str(fehler.warten)} if fehler.warten else None,
    )


def _adresse(request: Request, db: DbSession, vom_geraet: str) -> str:
    """Die oeffentliche Adresse aus den Einstellungen, sonst die des Geraets, sonst die der Anfrage.

    ⚠️ **Die Einstellung geht vor**, wie beim QR-Code fuer die iPhone-App: Ein
    Fernseher, der nexview im Heimnetz unter einer IP-Adresse anspricht, soll
    trotzdem eine Adresse zeigen, die auch das Telefon im Mobilnetz oeffnet.
    """
    for kandidat in (load_settings(db).public_url, vom_geraet, str(request.base_url)):
        try:
            sauber = flow.clean_public_url(kandidat.strip()) if kandidat and kandidat.strip() else ""
        except ValueError:
            sauber = ""
        if sauber:
            return sauber
    raise HTTPException(  # pragma: no cover - die Anfrage hat immer eine Adresse
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=meldungen.meldung(
            "app_pairing_address", "Für den QR-Code fehlt die Adresse, unter der nexview erreichbar ist."
        ),
    )


# ---------------------------------------------------------------------------
# Erkennen im Heimnetz
# ---------------------------------------------------------------------------


class Hallo(BaseModel):
    app: str = Field(description='Always "nexview". This is how a scan recognises nexview.')
    version: str
    name: str | None = Field(default=None, description="The instance's name. nexview has none yet: null.")
    url: str | None = Field(default=None, description="The public address set in nexview, else null.")
    device_code: bool = Field(description="True when pairing with a code works (`/api/app/1/device/...`).")


@hallo_router.get(
    "/hello",
    response_model=Hallo,
    summary="Recognise nexview on the network",
    description=(
        "Needs no key. Answers who this is, so an app scanning the home network can tell nexview apart "
        "from anything else on the same port. Carries no secrets, no account data and no counts."
    ),
)
def hallo(request: Request, db: DbSession) -> JSONResponse:
    """⚠️ **Nur, was ohnehin jeder sieht**: Name der App, Fassung (wie
    ``/api/health``) und die oeffentliche Adresse, die nexview in jeden Link
    und jede Mail schreibt. Nichts ueber Konten, Anfragen oder Dienste."""
    try:
        geraete_code.hallo_bremsen(anmeldebremse.client_ip(request))
    except GeraeteCodeFehler as fehler:
        raise _fehler(fehler) from fehler
    try:
        oeffentlich = flow.clean_public_url(load_settings(db).public_url.strip()) or None
    except ValueError:
        oeffentlich = None
    antwort = Hallo(app="nexview", version=__version__, name=None, url=oeffentlich, device_code=True)
    return JSONResponse(content=antwort.model_dump(), headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Geraet
# ---------------------------------------------------------------------------


class CodeAnfordern(BaseModel):
    device: str = Field(
        default="",
        max_length=geraete_code.GERAET_HOECHSTENS,
        description='The device\'s name, e.g. "Living room". Shown when confirming and in the list of keys.',
    )
    app: str = Field(
        default="",
        max_length=geraete_code.APP_HOECHSTENS,
        description='Which app asks, e.g. "nexcinema". Shown when confirming.',
    )
    address: str = Field(
        default="",
        max_length=200,
        description="The address the device uses to reach nexview. Only used when nexview has no public address set.",
    )


class NeuerCode(BaseModel):
    device_code: str = Field(description="Secret. Keep it on the device; send it when polling.")
    user_code: str = Field(description='Show this on screen, e.g. "BCDF-GHJK".')
    verification_uri: str = Field(description="Where to enter the code.")
    verification_uri_complete: str = Field(description="The same page with the code filled in. Put it in a QR code.")
    expires_in: int = Field(description="Seconds until the code expires.")
    expires_at: datetime
    interval: int = Field(description="Wait at least this many seconds between polls.")


@public_router.post(
    "/code",
    response_model=NeuerCode,
    summary="Ask for a code to show on a TV",
    description=(
        "Needs no key. Show `user_code` and a QR code of `verification_uri_complete`, then poll "
        "`POST /api/app/1/device/token` with `device_code` every `interval` seconds. "
        "429 `device_code_busy` (with `Retry-After`) when too many codes are open."
    ),
)
def code_anfordern(request: Request, db: DbSession, payload: CodeAnfordern | None = None) -> JSONResponse:
    daten = payload or CodeAnfordern()
    adresse = _adresse(request, db, daten.address)
    try:
        neu = geraete_code.starten(
            db, geraet=daten.device, app=daten.app, adresse=anmeldebremse.client_ip(request)
        )
    except GeraeteCodeFehler as fehler:
        raise _fehler(fehler) from fehler
    angezeigt = geraete_code.anzeigen(neu.benutzer_code)
    seite = f"{adresse}{SEITE}"
    antwort = NeuerCode(
        device_code=neu.geraete_code,
        user_code=angezeigt,
        verification_uri=seite,
        verification_uri_complete=f"{seite}?code={quote(angezeigt, safe='')}",
        expires_in=int(geraete_code.LEBENSDAUER.total_seconds()),
        expires_at=neu.gilt_bis,
        interval=geraete_code.INTERVALL,
    )
    return JSONResponse(
        content=antwort.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
    )


class Nachfragen(BaseModel):
    device_code: str = Field(max_length=100)


class GekoppeltesKonto(BaseModel):
    name: str
    role: str


class Abfragestand(BaseModel):
    status: str = Field(description="`pending`, `denied`, `expired` or `approved`.")
    interval: int | None = Field(default=None, description="With `pending`: seconds until the next poll.")
    key: str | None = Field(
        default=None, description="With `approved`, exactly once. Send it as `Authorization: Bearer`."
    )
    account: GekoppeltesKonto | None = Field(default=None, description="With `approved`: whose key it is.")


@public_router.post(
    "/token",
    response_model=Abfragestand,
    summary="Poll until the code is confirmed",
    description=(
        "Needs no key. `pending` until someone confirms; `approved` carries the key **once**, the "
        "code is used up afterwards; `denied` when someone declined; `expired` for an expired, "
        "used or unknown code. 429 `device_poll_too_fast` (with `Retry-After`) when polled faster "
        "than `interval`. 409 `apikey_too_many` when the account already has 20 keys."
    ),
)
def nachfragen(payload: Nachfragen, db: DbSession) -> JSONResponse:
    try:
        ergebnis = geraete_code.abfragen(db, payload.device_code)
    except GeraeteCodeFehler as fehler:
        raise _fehler(fehler) from fehler
    antwort = Abfragestand(status=ergebnis.stand)
    if ergebnis.stand == "pending":
        antwort.interval = geraete_code.INTERVALL
    if ergebnis.stand == "approved" and ergebnis.konto is not None:
        antwort.key = ergebnis.schluessel
        antwort.account = GekoppeltesKonto(
            name=ergebnis.konto.display_name or ergebnis.konto.username,
            role=ergebnis.konto.role.value,
        )
    # ⚠️ Ein Schluessel darf in keinem Zwischenspeicher liegen bleiben.
    return JSONResponse(content=antwort.model_dump(), headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Oberflaeche
# ---------------------------------------------------------------------------


class CodeEingabe(BaseModel):
    code: str = Field(max_length=20)


class KindWahl(BaseModel):
    id: int
    name: str


class GefundenerCode(BaseModel):
    code: str
    geraet: str
    app: str
    gilt_bis: datetime
    #: Fuer welche eigenen Kinder man stattdessen bestaetigen darf.
    kinder: list[KindWahl]


class Bestaetigen(CodeEingabe):
    #: Leer: fuer das eigene Konto. Sonst das Kind, dem der Schluessel gehoert.
    child_id: int | None = None
    #: Ein anderer Name fuer das Geraet als der, den es selbst nennt.
    name: str | None = Field(default=None, max_length=geraete_code.GERAET_HOECHSTENS)


class Bestaetigt(BaseModel):
    geraet: str
    #: Wem der Schluessel gehoeren wird.
    konto: str


def _bremse(request: Request, user) -> list[str]:
    return anmeldebremse.torwaechter(request, TUER, user.username)


def _gesucht(bremse: list[str], db: DbSession, code: str):
    """Der wartende Code, sonst 404 - und dann zaehlt es als Fehlversuch.

    ⚠️ **Ein Treffer setzt die Bremse nicht zurueck.** Jeder kann sich per
    ``/api/app/1/device/code`` gueltige Codes machen; setzte ein Treffer den
    Zaehler auf null, liesse sich zwischen zwei Rateversuchen immer wieder ein
    eigener Code eingeben, und die Bremse waere wertlos.
    """
    zeile = geraete_code.finden(db, code)
    if zeile is None:
        anmeldebremse.gescheitert(bremse)
        raise _fehler(geraete_code.nicht_da())
    return zeile


def _kinder(db: DbSession, user) -> list[KindWahl]:
    if not children.darf_kinder_anlegen(user):
        return []
    return [
        KindWahl(id=k.id, name=k.display_name or k.username)
        for k in children.eigene_kinder(db, user)
        if k.is_active
    ]


@router.post("/pruefen", response_model=GefundenerCode)
def pruefen(payload: CodeEingabe, request: Request, user: AdultUser, db: DbSession) -> GefundenerCode:
    """Gibt es diesen Code? Zeigt Geraet und App, bevor jemand bestaetigt."""
    bremse = _bremse(request, user)
    zeile = _gesucht(bremse, db, payload.code)
    return GefundenerCode(
        code=geraete_code.anzeigen(zeile.benutzer_code),
        geraet=zeile.geraet,
        app=zeile.app,
        gilt_bis=zeile.expires_at,
        kinder=_kinder(db, user),
    )


@router.post("/bestaetigen", response_model=Bestaetigt)
def bestaetigen(
    payload: Bestaetigen, request: Request, user: AdultUser, db: DbSession
) -> Bestaetigt:
    """Das Geraet bekommt beim naechsten Nachfragen einen Schluessel.

    ⚠️ **Fuer ein Kind nur das eigene, und nur mit dem Recht auf
    Kinderkonten** - dieselben Regeln wie beim QR-Code fuer das Kindergeraet
    (``app_kopplung.kinder_kopplung``).
    """
    bremse = _bremse(request, user)
    _gesucht(bremse, db, payload.code)
    fuer = user
    if payload.child_id is not None:
        if not children.darf_kinder_anlegen(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=meldungen.meldung(
                    "children_not_allowed", "Für Kinderkonten brauchst du eine Freigabe vom Betreiber."
                ),
            )
        try:
            fuer = children.kind_von(db, user, payload.child_id)
        except children.ChildError as error:
            raise HTTPException(status_code=error.status_code, detail=error.als_meldung()) from error
    try:
        zeile = geraete_code.bestaetigen(db, payload.code, fuer=fuer, von=user, name=payload.name)
    except GeraeteCodeFehler as fehler:
        raise _fehler(fehler) from fehler
    return Bestaetigt(
        geraet=geraete_code.geraetename(zeile.geraet), konto=fuer.display_name or fuer.username
    )


@router.post("/ablehnen", status_code=status.HTTP_204_NO_CONTENT)
def ablehnen(payload: CodeEingabe, request: Request, user: AdultUser, db: DbSession) -> None:
    """Das war nicht mein Fernseher: Das Geraet bekommt "abgelehnt"."""
    bremse = _bremse(request, user)
    _gesucht(bremse, db, payload.code)
    try:
        geraete_code.ablehnen(db, payload.code, von=user)
    except GeraeteCodeFehler as fehler:
        raise _fehler(fehler) from fehler

