"""Die iPhone-App koppeln: QR-Code im Browser, Abholen in der App.

Zwei Seiten:

* ``router`` - die Oberflaeche. Ein angemeldeter Mensch erzeugt einen Code fuer
  sein eigenes Konto oder, als Elternteil, fuer ein Kinderkonto, fragt nach, ob
  er abgeholt ist, und sieht und trennt die Geraete seiner Kinder. Gehoert zur
  Oberflaeche und steht unter keiner Zusage.
* ``public_router`` - die App. Die einzige Adresse unter ``/api/app/1`` ohne
  Schluessel: Wer noch keinen hat, holt ihn hier mit Kennung und Geheimnis aus
  dem QR-Code. Sie steht unter der Zusage von ``/api/app/1`` (``app1.ZUGESAGT``).

Der Ablauf und seine Regeln stehen in ``services/app_kopplung.py``.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

import segno

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import meldungen
from ..deps import AdultUser, DbSession
from ..services import app_kopplung, children
from ..services.app_kopplung import KopplungFehler
from ..services.settings_service import load_settings
from ..vendor.nexoidc import flow

#: Das Schema, auf das die App hoert. Die Kamera-App des iPhones oeffnet den
#: Link aus dem QR-Code direkt in nexview.
APP_LINK = "nexview://connect"

router = APIRouter(prefix="/api", tags=["app"])
public_router = APIRouter(prefix="/api/app/1", tags=["app"])


def _fehler(fehler: KopplungFehler) -> HTTPException:
    return HTTPException(status_code=fehler.status, detail=meldungen.meldung(fehler.code, fehler.text))


def _kind(db: DbSession, elternteil, child_id: int):
    """Das Kind dieses Elternteils - sonst 404, wie ein Kind, das es nicht gibt."""
    try:
        return children.kind_von(db, elternteil, child_id)
    except children.ChildError as error:
        raise HTTPException(status_code=error.status_code, detail=error.als_meldung()) from error


# ---------------------------------------------------------------------------
# Oberflaeche
# ---------------------------------------------------------------------------


class KopplungAnlegen(BaseModel):
    #: Die Adresse, unter der der Browser nexview gerade sieht. Gilt nur, wenn
    #: unter Einstellungen keine oeffentliche Adresse steht - die geht vor.
    adresse: str = Field(default="", max_length=200)


class KopplungNeu(BaseModel):
    kennung: str
    #: ⚠️ Nur in dieser Antwort im Klartext, und nur im Link und im QR-Code.
    #: Die Oberflaeche zeigt den Code und vergisst ihn wieder.
    geheimnis: str
    gilt_bis: datetime
    #: Die Adresse, die die App sich merkt.
    adresse: str
    link: str
    qr_svg: str


def _qr_svg(link: str) -> str:
    """Dunkle Module auf Weiss mit Ruhezone: ein Telefon liest ihn auch auf einer dunklen Seite.

    Mit SVG-Namensraum, weil die Seite ihn als ``data:``-Bild zeigt. Wie in nexcrate.
    """
    aus = io.BytesIO()
    segno.make(link, error="m").save(
        aus, kind="svg", scale=4, dark="#111827", light="#ffffff", border=4, xmldecl=False, svgns=True
    )
    return aus.getvalue().decode("utf-8")


def _adresse(db: DbSession, vom_browser: str) -> str:
    """Die oeffentliche Adresse aus den Einstellungen, sonst die des Browsers.

    ⚠️ **Die Einstellung geht vor.** Wer nexview im Heimnetz unter einer
    IP-Adresse aufruft, will der App trotzdem die Adresse mitgeben, unter der
    nexview auch unterwegs erreichbar ist.
    """
    eingestellt = load_settings(db).public_url
    for kandidat in (eingestellt, vom_browser):
        try:
            sauber = flow.clean_public_url(kandidat.strip()) if kandidat and kandidat.strip() else ""
        except ValueError:
            sauber = ""
        if sauber:
            return sauber
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=meldungen.meldung(
            "app_pairing_address", "Für den QR-Code fehlt die Adresse, unter der nexview erreichbar ist."
        ),
    )


class KopplungStand(BaseModel):
    #: ``wartet``, ``verbunden``, ``fehlgeschlagen`` oder ``abgelaufen``.
    stand: str
    #: Nach dem Verbinden: unter welchem Namen das Geraet in der Liste steht.
    geraet: str | None = None


class Geraet(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None


def _neu(db: DbSession, fuer, von, payload: KopplungAnlegen | None) -> KopplungNeu:
    # Erst die Adresse: Ohne sie entstuende ein Code, den keine App benutzen kann.
    adresse = _adresse(db, payload.adresse if payload is not None else "")
    try:
        v = app_kopplung.vorbereiten(db, fuer=fuer, von=von)
    except KopplungFehler as fehler:
        raise _fehler(fehler) from fehler
    link = (
        f"{APP_LINK}?address={quote(adresse, safe='')}&pairing={quote(v.kennung, safe='')}"
        f"&secret={quote(v.geheimnis, safe='')}"
    )
    return KopplungNeu(
        kennung=v.kennung,
        geheimnis=v.geheimnis,
        gilt_bis=v.gilt_bis,
        adresse=adresse,
        link=link,
        qr_svg=_qr_svg(link),
    )


@router.post("/me/app-kopplung", response_model=KopplungNeu, status_code=status.HTTP_201_CREATED)
def eigene_kopplung(
    user: AdultUser, db: DbSession, payload: KopplungAnlegen | None = None
) -> KopplungNeu:
    """Einen QR-Code fuer das eigene Konto: "iPhone-App verbinden"."""
    return _neu(db, user, user, payload)


@router.post(
    "/children/{child_id}/app-kopplung",
    response_model=KopplungNeu,
    status_code=status.HTTP_201_CREATED,
)
def kinder_kopplung(
    child_id: int, user: AdultUser, db: DbSession, payload: KopplungAnlegen | None = None
) -> KopplungNeu:
    """Einen QR-Code fuer das Geraet eines eigenen Kindes.

    ⚠️ **Nur das eigene Kind.** Ein fremdes antwortet wie ein Kind, das es
    nicht gibt (``children.kind_von``).

    ⚠️ **Und nur, solange das Elternteil Kinderkonten fuehren darf.** Wem der
    Betreiber das Recht entzogen hat, dessen Kinder sind stillgelegt - ein
    neues Geraet fuer sie waere ein Weg an dieser Entscheidung vorbei.
    """
    if not children.darf_kinder_anlegen(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung(
                "children_not_allowed", "Für Kinderkonten brauchst du eine Freigabe vom Betreiber."
            ),
        )
    return _neu(db, _kind(db, user, child_id), user, payload)


@router.get("/me/app-kopplung/{kennung}", response_model=KopplungStand)
def kopplung_stand(kennung: str, user: AdultUser, db: DbSession) -> KopplungStand:
    """Ist der Code schon abgeholt? Der Browser fragt das alle paar Sekunden."""
    try:
        stand, geraet = app_kopplung.stand(db, kennung, user)
    except KopplungFehler as fehler:
        raise _fehler(fehler) from fehler
    return KopplungStand(stand=stand, geraet=geraet)


@router.delete("/me/app-kopplung/{kennung}", status_code=status.HTTP_204_NO_CONTENT)
def kopplung_zurueckziehen(kennung: str, user: AdultUser, db: DbSession) -> None:
    """Den Code schliessen, bevor ihn jemand gescannt hat."""
    try:
        app_kopplung.zurueckziehen(db, kennung, user)
    except KopplungFehler as fehler:
        raise _fehler(fehler) from fehler


@router.get("/children/{child_id}/geraete", response_model=list[Geraet])
def kinder_geraete(child_id: int, user: AdultUser, db: DbSession) -> list[Geraet]:
    """Mit welchen Geraeten das Kind verbunden ist."""
    kind = _kind(db, user, child_id)
    return [
        Geraet(id=s.id, name=s.name, created_at=s.created_at, last_used_at=s.last_used_at)
        for s in app_kopplung.schluessel_des_kindes(db, kind)
    ]


@router.delete(
    "/children/{child_id}/geraete/{schluessel_id}", status_code=status.HTTP_204_NO_CONTENT
)
def kinder_geraet_trennen(
    child_id: int, schluessel_id: int, user: AdultUser, db: DbSession
) -> None:
    """Ein Geraet des Kindes trennen - ab sofort kommt es nicht mehr hinein."""
    kind = _kind(db, user, child_id)
    for schluessel in app_kopplung.schluessel_des_kindes(db, kind):
        if schluessel.id == schluessel_id:
            db.delete(schluessel)
            db.commit()
            return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=meldungen.meldung("apikey_not_found", "Diesen Schlüssel gibt es nicht."),
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class SchluesselAbholen(BaseModel):
    device: str = Field(
        default="",
        max_length=app_kopplung.GERAET_HOECHSTENS,
        description="The device's name, e.g. \"Mia's iPad\". Shown in the list of keys.",
    )


class AbgeholtesKonto(BaseModel):
    name: str
    role: str


class AbgeholterSchluessel(BaseModel):
    key: str = Field(description="The key, exactly once. Send it as `Authorization: Bearer`.")
    account: AbgeholtesKonto


@public_router.post(
    "/pairing/{pairing_id}/key",
    response_model=AbgeholterSchluessel,
    summary="Fetch the key from a QR code",
    description=(
        "Needs no key: this is how the app gets one. Send the secret from the QR code as "
        "`X-Pairing-Secret`. The answer carries the key **once**; afterwards the pairing is used up. "
        "An unknown pairing, a wrong secret, one already used and one expired all answer the same "
        "404 `pairing_not_found`."
    ),
)
def abholen(
    pairing_id: str,
    db: DbSession,
    payload: SchluesselAbholen | None = None,
    secret: Annotated[str | None, Header(alias="X-Pairing-Secret", max_length=100)] = None,
) -> JSONResponse:
    try:
        ergebnis = app_kopplung.abholen(
            db, pairing_id, secret, payload.device if payload is not None else ""
        )
    except KopplungFehler as fehler:
        raise _fehler(fehler) from fehler
    antwort = AbgeholterSchluessel(
        key=ergebnis.schluessel,
        account=AbgeholtesKonto(
            name=ergebnis.konto.display_name or ergebnis.konto.username,
            role=ergebnis.konto.role.value,
        ),
    )
    # ⚠️ Ein Schluessel darf in keinem Zwischenspeicher liegen bleiben.
    return JSONResponse(content=antwort.model_dump(), headers={"Cache-Control": "no-store"})
