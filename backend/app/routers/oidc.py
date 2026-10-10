"""Anmelden ueber fremde Anbieter (OIDC) und der authentik-Knopf - nach dem Bauplan.

Den Ablauf, die Pruefungen, die Anbieterliste und den Knopf macht das gemeinsame
Modul ``vendor/nexoidc`` (in allen nex-Apps gleich, per Pruefsumme bewacht).
Hier steht nur, was Nexview gehoert: Sitzung, Bremse, Passwortabfrage vor dem
Verknuepfen, Schutz des Betreibers, Sperrliste geloeschter Konten und die
Fehlerform der API.

Pfade (Bauplan 01 und ``tests-fuer-apps/reference/app.py``):

| Methode | Pfad | Wer |
|---|---|---|
| GET | ``/api/oidc/providers`` | jeder: Kurzname und Name der aktiven Eintraege |
| GET, POST | ``/api/oidc/admin/providers`` | Betreiber: Liste (nie ein Geheimnis), anlegen |
| PUT, DELETE | ``/api/oidc/admin/providers/{id}`` | Betreiber: speichern, entfernen |
| GET | ``/api/oidc/admin/providers/{id}/impact`` | Betreiber: Zahlen fuer die Rueckfragen |
| PUT | ``/api/oidc/admin/providers/order`` | Betreiber: Reihenfolge |
| GET, DELETE | ``/api/oidc/admin/blocks[/{id}]`` | Betreiber: gesperrte Identitaeten |
| GET | ``/api/oidc/{slug}/start[?invite=]`` | jeder: zum Anbieter |
| GET | ``/api/oidc/{slug}/callback``, ``/api/oidc/callback`` | jeder: die Rueckkehr |
| GET | ``/api/auth/oidc/{slug}/callback`` | jeder: die Rueckkehr der Eintraege aus 1.0.1 |
| GET | ``/api/oidc/me`` | angemeldet: jeder aktive Eintrag mit ``linked`` |
| POST, DELETE | ``/api/oidc/{slug}/link`` | angemeldet: verknuepfen (mit Passwort), loesen |
| DELETE | ``/api/oidc/admin/accounts/{account}/links/{provider}`` | Betreiber, mit eigenem Passwort |
| POST | ``/api/oidc/authentik/setup`` | Betreiber: der Knopf |
| GET, POST | ``/api/oidc/authentik/address`` | Betreiber: die oeffentliche Adresse der Karte |
| GET | ``/api/oidc/authentik/blueprint`` | Betreiber: ``nexview-authentik.yaml`` |

⚠️ **Der Rueckweg ist eine Browser-Weiterleitung, keine API-Antwort.** Jeder
Ausgang, auch jeder Fehler, endet auf der eigenen Oberflaeche mit ``?error=``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import meldungen
from ..config import get_settings
from ..db import SessionLocal
from ..deps import AdminUser, AdultUser, schluessel_der_anfrage
from ..models import OidcBlock, User, utcnow
from ..security import TokenInhalt, decode_token, has_usable_password, verify_password
from ..services import anmeldebremse, oidc_store, sitzung
from ..services import betreiber as betreiber_dienst
from ..services.oidc_store import SqlStore, anfrage_basis
from ..vendor.nexoidc import OidcError, ProviderInvalid, attempt, authentik, flow, providers
from ..vendor.nexoidc import accounts as oidc_accounts
from ..vendor.nexoidc.config import current

router = APIRouter(prefix="/api/oidc", tags=["oidc"])
#: Die Rueckleitadresse, die Anbieter von Nexview bis 1.0.1 kannten.
alter_router = APIRouter(prefix="/api/auth/oidc", tags=["oidc"])

logger = logging.getLogger("nexview.oidc")

#: Abweisungen, die nichts kosten: ein Rueckweg ohne gueltigen Anlauf, ein
#: gebremster Absender, ein unbekannter Anbieter. Sie fuettern die Bremse nie -
#: sonst sperrte ein Fremder mit gebastelten Rueckwegen andere aus.
FREIE_ABWEISUNGEN = ("oidc_state_mismatch", "too_many_attempts", "oidc_not_configured")
#: Die Tuer der Bremse (``anmeldebremse``) fuer alles hier.
TUER = "oidc"
#: Das Cookie, das beim Verknuepfen die Sitzung zum Rueckweg traegt (siehe
#: ``_sitzungsbeleg``).
BELEG_COOKIE = "nexview_oidc_konto"


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


@contextmanager
def _store() -> Iterator[SqlStore]:
    with SessionLocal() as db:
        yield SqlStore(db)


def _basis(request: Request) -> str:
    return anfrage_basis(str(request.base_url))


def _formfehler(fehler: ProviderInvalid) -> HTTPException:
    return HTTPException(
        status_code=422 if fehler.code not in ("provider_unknown",) else 404,
        detail={"code": fehler.code, "message": fehler.message, "field": fehler.field},
    )


def _weiterleitung(ziel: flow.Redirect, request: Request) -> RedirectResponse:
    """Ein ``flow.Redirect`` als Antwort - 303, das Cookie gesetzt oder geloescht."""
    antwort = RedirectResponse(ziel.location, status_code=status.HTTP_303_SEE_OTHER)
    cookie = ziel.cookie
    secure = cookie.secure or sitzung.cookie_secure(request)
    if cookie.max_age == 0:
        antwort.delete_cookie(
            cookie.name, path=cookie.path, secure=secure, httponly=True, samesite="lax"
        )
    else:
        antwort.set_cookie(
            cookie.name,
            cookie.value,
            max_age=cookie.max_age,
            path=cookie.path,
            httponly=cookie.http_only,
            samesite="lax",
            secure=secure,
        )
    return antwort


def _gebremst(request: Request, kennung: str | None = None) -> list[str]:
    """Die Bremse vor Start und Rueckkehr (Bauplan 02, "Ablauf" 4).

    Gezaehlt wird die Adresse des Absenders - nur wenn ``NEXVIEW_CLIENT_IP``
    sagt, woher sie kommt (sonst waere es die des Proxys, und ein Vertipper
    sperrte das ganze Haus aus) - und beim Verknuepfen das Konto. ⚠️ **Nie der
    Kurzname eines Anbieters:** Ein Zaehler, den alle teilen, liesse jeden
    Fremden alle anderen aussperren.
    """
    try:
        return anmeldebremse.torwaechter(request, TUER, kennung)
    except HTTPException as gesperrt:
        raise OidcError("too_many_attempts", "sender is braked") from gesperrt


def _betreiber_nicht_aussperren(db, admin: User, provider_id: int, *, issuer: str | None) -> None:
    """Die Seitentuer: den Betreiber aussperren, ohne sein Konto anzufassen.

    Entfernen, Abschalten oder ein anderer Aussteller nehmen ihm den Weg
    hinein, wenn er nur ueber diesen Anbieter kommt. Das darf nur er selbst
    (sein Passwort setzen darf auch niemand sonst). Fuer alle anderen Konten
    fragt die Oberflaeche vorher mit der Zahl der Betroffenen.
    """
    traeger = betreiber_dienst.traeger(db)
    if traeger is None or traeger.id == admin.id:
        return
    if oidc_store.anderer_weg_hinein(traeger):
        return
    store = SqlStore(db)
    eigene = store.links_of_account(traeger.id)
    wege = [link for link in eigene if link.provider_id != provider_id]
    if any(
        (eintrag := store.get_provider(link.provider_id)) is not None and eintrag.enabled
        for link in wege
    ):
        return
    if not any(link.provider_id == provider_id for link in eigene):
        return
    if issuer is not None and not providers.issuer_change_impact(store, provider_id, issuer):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=meldungen.meldung(
            "betreiber_wuerde_ausgesperrt",
            "Der Betreiber käme danach nicht mehr herein - das kann niemand "
            "außer ihm selbst entscheiden.",
        ),
    )


def _passwort_pruefen(user: User, passwort: str, bremse: list[str]) -> None:
    if not has_usable_password(user.password_hash) or not verify_password(
        passwort, user.password_hash
    ):
        anmeldebremse.gescheitert(bremse)
        raise meldungen.fehler(
"current_password_wrong", "Das aktuelle Passwort stimmt nicht.", 401
        )


# ---------------------------------------------------------------------------
# Die Knoepfe der Anmelde- und Einladungsseite
# ---------------------------------------------------------------------------


@router.get("/providers")
def anbieter_liste() -> list[dict[str, str]]:
    """Nur Kurzname und Name der aktiven Eintraege - sie stehen vor jeder Sitzung."""
    with _store() as s:
        return providers.public_list(s)


# ---------------------------------------------------------------------------
# Die Anbieterliste (Betreiber)
# ---------------------------------------------------------------------------


class AnbieterForm(BaseModel):
    label: str = Field(max_length=200)
    slug: str = Field(default="", max_length=100)
    issuer: str = Field(max_length=600)
    client_id: str = Field(max_length=300)
    client_secret: str = Field(default="", max_length=2000)
    scopes: str = Field(default="openid profile email", max_length=500)
    enabled: bool = True
    auto_create: bool = False
    trusts_second_factor: bool = True


class Reihenfolge(BaseModel):
    ids: list[int] = Field(max_length=200)


class PasswortIn(BaseModel):
    password: str = Field(default="", max_length=500)


@router.get("/admin/providers")
def admin_liste(request: Request, admin: AdminUser) -> list[dict[str, Any]]:
    with _store() as s:
        gekoppelt = providers.coupled(s)
        return [
            providers.admin_view(
                eintrag, _basis(request), len(s.links_of_provider(eintrag.id)), coupled=gekoppelt
            )
            for eintrag in s.list_providers()
        ]


@router.post("/admin/providers", status_code=status.HTTP_201_CREATED)
async def admin_anlegen(payload: AnbieterForm, request: Request, admin: AdminUser) -> dict[str, Any]:
    with _store() as s:
        try:
            neu = await providers.create(s, providers.Form(**payload.model_dump()))
        except ProviderInvalid as fehler:
            raise _formfehler(fehler) from fehler
        logger.info("OIDC provider %s added by %r", neu.slug, admin.username)
        return providers.admin_view(neu, _basis(request), 0)


@router.put("/admin/providers/order")
def admin_ordnen(payload: Reihenfolge, admin: AdminUser) -> list[dict[str, str]]:
    with _store() as s:
        try:
            providers.reorder(s, payload.ids)
        except ProviderInvalid as fehler:
            raise _formfehler(fehler) from fehler
        return providers.public_list(s)


@router.put("/admin/providers/{provider_id}")
async def admin_speichern(
    provider_id: Annotated[int, Path(ge=1)],
    payload: AnbieterForm,
    request: Request,
    admin: AdminUser,
) -> dict[str, Any]:
    with _store() as s:
        bisher = s.get_provider(provider_id)
        if bisher is not None and (not payload.enabled and bisher.enabled):
            _betreiber_nicht_aussperren(s.db, admin, provider_id, issuer=None)
        elif bisher is not None:
            _betreiber_nicht_aussperren(s.db, admin, provider_id, issuer=payload.issuer)
        try:
            gespeichert, verworfen = await providers.update(
                s, provider_id, providers.Form(**payload.model_dump())
            )
        except ProviderInvalid as fehler:
            raise _formfehler(fehler) from fehler
        logger.info("OIDC provider %s saved by %r", gespeichert.slug, admin.username)
        ansicht = providers.admin_view(
            gespeichert, _basis(request), len(s.links_of_provider(gespeichert.id))
        )
        return {**ansicht, "dropped": verworfen}


@router.get("/admin/providers/{provider_id}/impact")
def admin_folgen(
    provider_id: Annotated[int, Path(ge=1)],
    admin: AdminUser,
    issuer: Annotated[str, Query(max_length=600)] = "",
) -> dict[str, Any]:
    with _store() as s:
        if s.get_provider(provider_id) is None:
            raise meldungen.fehler("provider_unknown", "Diesen Anbieter gibt es nicht mehr.", 404)
        entfernen = providers.removal_impact(s, provider_id)
        wechsel = providers.issuer_change_impact(s, provider_id, issuer) if issuer else 0
        return {"issuer_change": wechsel, **asdict(entfernen)}


@router.delete("/admin/providers/{provider_id}")
def admin_entfernen(provider_id: Annotated[int, Path(ge=1)], admin: AdminUser) -> dict[str, Any]:
    with _store() as s:
        _betreiber_nicht_aussperren(s.db, admin, provider_id, issuer=None)
        try:
            folgen = providers.remove(s, provider_id)
        except ProviderInvalid as fehler:
            raise _formfehler(fehler) from fehler
    logger.info("OIDC provider %s removed by %r", provider_id, admin.username)
    return asdict(folgen)


class SperrEintrag(BaseModel):
    """Eine gesperrte Identitaet, wie die Liste sie zeigt."""

    id: int
    issuer: str
    display: str | None = None

    model_config = {"from_attributes": True}


@router.get("/admin/blocks", response_model=list[SperrEintrag])
def admin_sperrliste(admin: AdminUser) -> list[SperrEintrag]:
    """Gesperrte Identitaeten - entstanden beim Loeschen von Konten."""
    with SessionLocal() as db:
        return [
            SperrEintrag.model_validate(zeile)
            for zeile in db.scalars(select(OidcBlock).order_by(OidcBlock.blocked_at.desc()))
        ]


@router.delete("/admin/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_sperre_aufheben(block_id: Annotated[int, Path(ge=1)], admin: AdminUser) -> None:
    """Sperre aufheben - danach darf diese Identitaet wieder herein."""
    with SessionLocal() as db:
        eintrag = db.get(OidcBlock, block_id)
        if eintrag is not None:
            db.delete(eintrag)
            db.commit()
            logger.info("OIDC block %s lifted by %r", block_id, admin.username)


# ---------------------------------------------------------------------------
# Hinweg und Rueckweg
# ---------------------------------------------------------------------------


@router.get("/{slug}/start")
async def start(
    slug: Annotated[str, Path(max_length=40)],
    request: Request,
    invite: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    """Zum Anbieter - der Knopf auf der Anmelde- oder Einladungsseite.

    Ein schlichtes GET, damit die Seite einen Link daraus machen kann. Jeder
    Fehler endet wieder auf der Anmeldeseite, mit Kennung.
    """
    with _store() as s:
        try:
            _gebremst(request)
            ziel = await flow.begin(s, slug, request_base=_basis(request), invite=invite)
        except OidcError as fehler:
            return _weiterleitung(flow.refused(fehler, None), request)
    return _weiterleitung(ziel, request)


def _sitzungsbeleg(request: Request, user: User) -> str:
    """Wer gerade verknuepft - als Beleg der Sitzung fuer den Rueckweg.

    Nexviews Sitzung steckt im ``Authorization``-Kopf; der Rueckweg vom Anbieter
    ist eine Browser-Navigation und traegt ihn nicht, und das Erneuerungs-Cookie
    liegt nur unter ``/api/auth``. Damit die Rueckkehr das Konto **aus der
    Sitzung** nimmt und nicht aus dem Anlauf (Bauplan 01, "Verknuepfen"), legt
    der Start neben das Anlauf-Cookie ein zweites: Konto und Kennung der
    Sitzung, signiert. Der Rueckweg prueft, dass diese Sitzung noch gilt (nicht
    abgemeldet, kein Passwortwechsel seitdem). Ein Anlauf-Cookie allein, in
    einen anderen Browser getragen, verknuepft nichts.
    """
    kopf = request.headers.get("authorization", "")
    inhalt = decode_token(kopf[7:].strip(), "access") if kopf[:7].lower() == "bearer " else None
    if inhalt is None or inhalt.benutzer_id != user.id:
        raise meldungen.fehler("oidc_link_mismatch", "Diese Verknüpfung gehört zu einem anderen Konto.", 409)
    return jwt.encode(
        {"uid": user.id, "sid": inhalt.sitzung, "ms": inhalt.ausgestellt},
        _beleg_schluessel(),
        algorithm="HS256",
    )


def _beleg_schluessel() -> bytes:
    return b"nexview-oidc-link:" + get_settings().resolved_secret_key().encode("utf-8")


def _konto_aus_beleg(request: Request, db) -> int | None:
    """Das Konto der Sitzung, die das Verknuepfen begann - wenn sie noch gilt."""
    roh = request.cookies.get(BELEG_COOKIE)
    if not roh:
        return None
    try:
        daten = jwt.decode(roh, _beleg_schluessel(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    try:
        user = db.get(User, int(daten.get("uid")))
        # Der Beleg traegt keine Laufzeit des Zugangs-Tokens: Gilt die Sitzung
        # noch, gilt er (das Anlauf-Cookie daneben laeuft nach zehn Minuten ab).
        sitz = TokenInhalt(
            benutzer_id=int(daten.get("uid")),
            ausgestellt=int(daten.get("ms") or 0),
            sitzung=str(daten.get("sid") or ""),
        )
    except (TypeError, ValueError):
        return None
    if user is None or not user.is_active:
        return None
    if not sitz.sitzung or sitzung.beendet(db, sitz) or not sitzung.gilt_noch(sitz, user):
        return None
    return user.id


async def _zurueck(slug: str, request: Request) -> RedirectResponse:
    """Die Rueckkehr vom Anbieter. Die Reihenfolge ist die des Moduls: der Fehler
    des Anbieters, Cookie und ``state``, das einmalige ``state``, dann der Anbieter;
    vorher wird nichts geschrieben."""
    cookie = request.cookies.get(current().cookie_name)
    angelaufen = attempt.read(cookie)
    verknuepfen = angelaufen is not None and angelaufen.purpose == "link"
    kennung = (
        f"uid:{angelaufen.link_account_id}"
        if verknuepfen and angelaufen is not None and angelaufen.link_account_id
        else None
    )
    bremse: list[str] = []
    with _store() as s:
        try:
            bremse = _gebremst(request, kennung)
            ankunft = await flow.finish(s, slug, cookie=cookie, params=dict(request.query_params))
            # Ein geloeschtes Konto kommt ueber den Anbieter nicht wieder (Sperrliste);
            # eine noch bestehende Verknuepfung geht vor.
            gesperrt = oidc_store.ist_gesperrt(
                s.db, ankunft.provider.issuer, ankunft.identity.subject
            ) or oidc_store.ist_gesperrt(s.db, ankunft.identity.issuer, ankunft.identity.subject)
            if gesperrt and s.find_link(ankunft.provider.id, ankunft.identity.subject) is None:
                raise OidcError("account_blocked", "this identity is on the block list")
            if ankunft.started.purpose == "link":
                oidc_accounts.link(s, ankunft, _konto_aus_beleg(request, s.db))
                anmeldebremse.geklappt(bremse)
                antwort = _weiterleitung(flow.arrived(ankunft, flow.linked_page(slug)), request)
                _beleg_loeschen(antwort, ankunft, request)
                return antwort
            angemeldet = oidc_accounts.sign_in(s, ankunft)
        except OidcError as fehler:
            # Nur ein Rueckweg mit gueltigem Anlauf hat einen echten Lauf beim
            # Anbieter hinter sich; alles andere laesst sich beliebig billig
            # erzeugen und zaehlt nicht.
            if angelaufen is not None and fehler.code not in FREIE_ABWEISUNGEN:
                anmeldebremse.gescheitert(bremse)
            antwort = _weiterleitung(flow.refused(fehler, cookie), request)
            if verknuepfen:
                antwort.delete_cookie(
                    BELEG_COOKIE,
                    path=_beleg_pfad(angelaufen.redirect_uri if angelaufen else ""),
                    httponly=True,
                    samesite="lax",
                    secure=sitzung.cookie_secure(request),
                )
            return antwort
        anmeldebremse.geklappt(bremse)
        user = s.db.get(User, angemeldet.account_id)
        assert user is not None
        user.last_login_at = utcnow()
        s.db.commit()
        antwort = _weiterleitung(flow.arrived(ankunft), request)
        sitzung.starten(antwort, request, user)
        logger.info("User %r signed in via OIDC provider %r", user.username, slug)
        return antwort


def _beleg_pfad(redirect_uri: str) -> str:
    from urllib.parse import urlsplit

    return flow.cookie_path_for(urlsplit(redirect_uri).path) if redirect_uri else current().cookie_path


def _beleg_loeschen(antwort: Response, ankunft: flow.Arrival, request: Request) -> None:
    antwort.delete_cookie(
        BELEG_COOKIE,
        path=_beleg_pfad(ankunft.started.redirect_uri),
        httponly=True,
        samesite="lax",
        secure=sitzung.cookie_secure(request),
    )


@router.get("/callback")
async def rueckweg_oidc(request: Request) -> RedirectResponse:
    """Die Rueckkehr des Eintrags ``oidc`` (nur bei einer Kopplung)."""
    return await _zurueck("oidc", request)


@router.get("/{slug}/callback")
async def rueckweg(slug: Annotated[str, Path(max_length=40)], request: Request) -> RedirectResponse:
    return await _zurueck(slug, request)


@alter_router.get("/{slug}/callback")
async def rueckweg_alt(
    slug: Annotated[str, Path(max_length=40)], request: Request
) -> RedirectResponse:
    """Die Adresse, die Anbieter von Nexview bis 1.0.1 kannten.

    Die Eintraege von damals tragen sie als ``redirect_path``; sie bleibt
    dauerhaft gueltig (Bauplan 06, "Pfad der Schnittstelle").
    """
    return await _zurueck(slug, request)


# ---------------------------------------------------------------------------
# Mein Konto
# ---------------------------------------------------------------------------


@router.get("/me")
def meine_anbieter(user: AdultUser) -> list[dict[str, Any]]:
    """Jeder aktive Eintrag mit der Frage, ob dieses Konto mit ihm verknuepft ist."""
    with _store() as s:
        verknuepft = {link.provider_id for link in s.links_of_account(user.id)}
        return [
            {"slug": e.slug, "label": e.label, "linked": e.id in verknuepft}
            for e in s.list_providers()
            if e.enabled
        ]


@router.post("/{slug}/link")
async def verknuepfen_starten(
    slug: Annotated[str, Path(max_length=40)],
    payload: PasswortIn,
    request: Request,
    response: Response,
    user: AdultUser,
) -> dict[str, str]:
    """Verknuepfen im Profil, Schritt 1: das Passwort noch einmal, dann zum Anbieter.

    Ein Verknuepfen gibt das Konto an den, der beim Anbieter angemeldet ist -
    deshalb das Passwort. Ein persoenlicher Zugriffs-Schluessel startet es nicht.
    """
    if schluessel_der_anfrage(request) is not None:
        raise meldungen.fehler("apikey_not_allowed", "Das geht nur in einer Sitzung, nicht mit einem Schlüssel.", 403)
    with _store() as s:
        konto = s.db.get(User, user.id)
        assert konto is not None
        try:
            bremse = _gebremst(request, f"uid:{konto.id}")
        except OidcError as fehler:
            raise meldungen.fehler(
"too_many_attempts",
                "Zu viele Versuche. Warte einen Moment und versuch es dann noch einmal.",
                429,
            ) from fehler
        if not has_usable_password(konto.password_hash):
            raise meldungen.fehler(
"oidc_only_account",
                "Dieses Konto meldet sich nur über einen Anbieter an.",
                409,
            )
        _passwort_pruefen(konto, payload.password, bremse)
        anmeldebremse.geklappt(bremse)
        beleg = _sitzungsbeleg(request, konto)
        try:
            ziel = await flow.begin(s, slug, request_base=_basis(request), link_account_id=konto.id)
        except OidcError as fehler:
            raise meldungen.fehler(fehler.code, fehler.message, 409) from fehler
    cookie = ziel.cookie
    secure = cookie.secure or sitzung.cookie_secure(request)
    response.set_cookie(
        cookie.name,
        cookie.value,
        max_age=cookie.max_age,
        path=cookie.path,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    response.set_cookie(
        BELEG_COOKIE,
        beleg,
        max_age=cookie.max_age,
        path=cookie.path,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    logger.info("User %r starts linking to OIDC provider %s", user.username, slug)
    return {"url": ziel.location}


@router.delete("/{slug}/link", status_code=status.HTTP_204_NO_CONTENT)
def loesen(slug: Annotated[str, Path(max_length=40)], user: AdultUser) -> None:
    with _store() as s:
        eintrag = s.provider_by_slug(slug)
        if eintrag is None:
            raise meldungen.fehler("oidc_not_configured", "Dieser Anbieter ist nicht eingerichtet.", 404)
        try:
            oidc_accounts.unlink(s, user.id, eintrag.id)
        except OidcError as fehler:
            raise meldungen.fehler(fehler.code, fehler.message, 409) from fehler


@router.delete(
    "/admin/accounts/{account_id}/links/{provider_id}", status_code=status.HTTP_204_NO_CONTENT
)
def betreiber_loest(
    account_id: Annotated[int, Path(ge=1)],
    provider_id: Annotated[int, Path(ge=1)],
    payload: PasswortIn,
    request: Request,
    admin: AdminUser,
) -> None:
    """Der Betreiber loest die Verknuepfung eines Kontos - mit dem eigenen Passwort.

    Das Konto des Betreibers kann niemand sonst anfassen (dieselbe Regel wie
    ``deps.betreiberschutz``).
    """
    with _store() as s:
        ich = s.db.get(User, admin.id)
        assert ich is not None
        bremse = anmeldebremse.torwaechter(request, "login", admin.username)
        _passwort_pruefen(ich, payload.password, bremse)
        anmeldebremse.geklappt(bremse)
        ziel = s.db.get(User, account_id)
        if ziel is None or s.get_provider(provider_id) is None:
            raise meldungen.fehler("not_found", "Nicht gefunden.", 404)
        if ziel.is_betreiber and ziel.id != admin.id:
            raise meldungen.fehler(
"betreiber_geschuetzt",
                "Das Konto des Betreibers kann von niemandem sonst geändert werden.",
                403,
            )
        try:
            geloest = oidc_accounts.unlink(s, account_id, provider_id)
        except OidcError as fehler:
            raise meldungen.fehler(fehler.code, fehler.message, 409) from fehler
        if not geloest:
            raise meldungen.fehler("oidc_not_linked", "Hier ist nichts verknüpft.", 404)
        logger.info(
            "OIDC link of %r to provider %s removed by %r", ziel.username, provider_id, admin.username
        )


# ---------------------------------------------------------------------------
# authentik in einem Schritt
# ---------------------------------------------------------------------------


class EinrichtenIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    token: str = Field(min_length=1, max_length=2000)
    confirm_issuer_change: bool = False
    #: Die Adresse, die der Betreiber in der Karte bestaetigt hat (die des
    #: Browsers), solange Nexview keine oeffentliche Adresse hat.
    public_url: str = Field(default="", max_length=600)


class AdresseIn(BaseModel):
    public_url: str = Field(max_length=600)
    confirm: bool = False


@router.post("/authentik/setup")
async def authentik_einrichten(
    payload: EinrichtenIn, request: Request, admin: AdminUser
) -> dict[str, Any]:
    """Immer 200 mit den gelaufenen Schritten, auch einem gescheiterten: die Seite
    sagt in der Sprache des Betreibers, warum. Das Token wird nie gespeichert."""
    with _store() as s:
        ergebnis = await authentik.setup(
            s,
            payload.url,
            payload.token,
            _basis(request),
            confirm_issuer_change=payload.confirm_issuer_change,
            public_url=payload.public_url,
            headers=request.headers,
        )
    logger.info("authentik button run by %r", admin.username)
    return ergebnis.as_dict()


@router.get("/authentik/address")
def authentik_adresse(request: Request, admin: AdminUser) -> dict[str, Any]:
    with _store() as s:
        return authentik.address_view(s, _basis(request), request.headers)


@router.post("/authentik/address")
def authentik_adresse_speichern(
    payload: AdresseIn, request: Request, admin: AdminUser
) -> dict[str, Any]:
    """"Nur uebernehmen" der Karte: die oeffentliche Adresse speichern, mit Rueckfrage."""
    with _store() as s:
        try:
            return authentik.save_public_url(
                s, payload.public_url, _basis(request), confirm=payload.confirm
            )
        except ValueError as fehler:
            raise meldungen.fehler(
            "public_url_invalid", "Diese Adresse lässt sich nicht verwenden.", 422
            ) from fehler
        except authentik.PublicUrlChange as frage:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "public_url_change", "message": "", **frage.question},
            ) from frage
        except authentik.PublicUrlNotKept as fehler:
            raise meldungen.fehler(
"public_url_needed", "Nexview hat noch keine öffentliche Adresse.", 409
            ) from fehler


@router.get("/authentik/blueprint")
def authentik_blueprint(request: Request, admin: AdminUser) -> Response:
    with _store() as s:
        try:
            adresse = authentik.blueprint_redirect(s, _basis(request))
        except authentik.PublicUrlNeeded as fehler:
            raise meldungen.fehler(
"public_url_needed", "Nexview hat noch keine öffentliche Adresse.", 409
            ) from fehler
        text = authentik.blueprint(adresse)
    name = authentik.blueprint_filename()
    return Response(
        text,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def adresse_bewachen(db, neu: str, request: Request, *, bestaetigt: bool) -> None:
    """Fuer jede eigene Route, die die oeffentliche Adresse speichert (Bauplan 04, Karte 1).

    Fuehrt die neue Adresse bei einem Eintrag des authentik-Knopfs von der
    Rueckleitadresse weg, die authentik kennt, oder gibt es aktive Eintraege von
    Hand, fragt die Route zurueck: 409 ``public_url_change`` mit Zahl und
    Adresse, nichts gespeichert. Ein Leeren der Adresse zaehlt genauso.
    """
    try:
        frage = authentik.public_url_guard(SqlStore(db), neu, _basis(request), confirm=bestaetigt)
    except ValueError as fehler:
        # Ein Leeren kommt hier nicht an: Das Modul rechnet es selbst als
        # Rueckfall auf die Adresse der Anfrage und fragt dann wie sonst auch.
        raise meldungen.fehler(
            "public_url_invalid", "Diese Adresse lässt sich nicht verwenden.", 422
        ) from fehler
    if frage is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "public_url_change", "message": "", **frage},
        )
