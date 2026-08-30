"""Anmelden und Verknuepfen ueber einen OIDC-Anbieter.

Das Gegenstueck zu ``routers/mediaserver.py`` fuer die genormte Anmeldung.
Zwei Wege fuehren hier zusammen, unterschieden nur darin, was am Ende mit der
gepruefte Identitaet geschieht:

* **Anmelden** (ohne Sitzung) - endet in einer gewoehnlichen Nexview-Sitzung.
* **Verknuepfen** (mit Sitzung) - haengt die Identitaet an das eigene Konto.

⚠️ **Der Rueckweg ist eine Browser-Weiterleitung, keine API-Antwort.** Der
Anbieter schickt den Browser per GET zurueck; was immer hier herauskommt,
sieht ein Mensch. Deshalb endet jeder Ausgang - auch jeder Fehler - in einer
Weiterleitung auf die eigene Oberflaeche mit einer Kennung in der Adresse,
nie in nacktem JSON. Die Sitzung entsteht dabei trotzdem wie ueberall: Das
Erneuerungs-Cookie faehrt mit der Weiterleitung hinaus, und die Oberflaeche
holt sich ihr Zugangs-Token anschliessend ueber ``/api/auth/refresh`` - der
Weg existiert seit 0.21 und bekommt hier schlicht einen zweiten Nutzer.

⚠️ **Deaktivierte oder unbekannte Anbieter: 404 auf beiden Endpunkten.**
Deaktivieren blendet nicht nur den Knopf aus - der Weg selbst ist zu.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from ..config import get_settings
from ..deps import AdultUser, DbSession
from ..models import OidcProvider, User, utcnow
from ..schemas import UserPublic
from ..services import anmeldebremse, oidc, oidc_accounts, sitzung
from ..services.mediaserver_accounts import KontoFehler
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/auth/oidc", tags=["oidc"])

logger = logging.getLogger("nexview.oidc")

#: Wohin der Browser nach dem Rueckweg gefuehrt wird - Routen der Oberflaeche.
LOGIN_ZIEL = "/login"
PROFIL_ZIEL = "/profile"


class AnbieterKnopf(BaseModel):
    """Was die Anmeldeseite braucht, um einen Knopf zu malen - nicht mehr.

    Ausdruecklich ohne Adresse, Client-ID oder Schalter: Diese Liste ist
    **oeffentlich**, sie steht vor der Anmeldung.
    """

    slug: str
    label: str


class LinkStart(BaseModel):
    """Die Adresse, zu der die Oberflaeche den Browser schicken soll."""

    url: str


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _anbieter(db: DbSession, slug: str) -> OidcProvider:
    eintrag = db.scalar(
        select(OidcProvider).where(
            OidcProvider.slug == slug, OidcProvider.enabled.is_(True)
        )
    )
    if eintrag is None:
        # Absichtlich wortkarg und ohne Unterscheidung "gibt es nicht" /
        # "deaktiviert": Beides heisst fuer draussen dasselbe - hier ist
        # keine Tuer.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "oidc_unknown_provider", "message": "Diesen Anbieter gibt es nicht."},
        )
    return eintrag


def _oberflaeche(pfad: str, **params: str) -> RedirectResponse:
    """Weiterleitung auf eine Route der Oberflaeche - mit Kennungen als Query.

    303 statt 307: Der Browser soll das Ziel schlicht mit GET laden,
    unabhaengig davon, wie er herkam.
    """
    basis = get_settings().url_base
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(
        f"{basis}{pfad}{'?' + query if query else ''}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _cookie_setzen(antwort: Response, wert: str) -> None:
    antwort.set_cookie(
        oidc.COOKIE_NAME,
        wert,
        max_age=oidc.ANLAUF_MINUTEN * 60,
        path=oidc.cookie_pfad(),
        httponly=True,
        # ``lax`` laesst das Cookie bei der Rueckkehr vom Anbieter mitfahren -
        # das ist eine Top-Level-Navigation. ``strict`` saehe sicherer aus und
        # brueche genau diesen Schritt.
        samesite="lax",
    )


def _cookie_loeschen(antwort: Response) -> None:
    antwort.delete_cookie(oidc.COOKIE_NAME, path=oidc.cookie_pfad(), httponly=True)


def _rueckkehr_adresse(db: DbSession, slug: str) -> str:
    """Die Rueckkehr-Adresse, wie sie auch beim Anbieter hinterlegt ist.

    Gebaut aus der oeffentlichen Adresse (samt Unterpfad) - **nicht** aus der
    Anfrage: Hinter einem Proxy sieht Nexview seine eigene Adresse nicht, und
    eine geratene wuerde beim Anbieter schlicht nicht uebereinstimmen.
    """
    settings = load_settings(db)
    if not settings.public_url:
        raise oidc.OidcFehler(
            "oidc_no_public_url",
            "Die öffentliche Adresse ist nicht eingestellt.",
            status_code=409,
        )
    return settings.link(f"api/auth/oidc/{slug}/callback")


# ---------------------------------------------------------------------------
# Die Knoepfe der Anmeldeseite
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AnbieterKnopf])
def anbieter_liste(db: DbSession) -> list[AnbieterKnopf]:
    eintraege = db.scalars(
        select(OidcProvider).where(OidcProvider.enabled.is_(True)).order_by(OidcProvider.id)
    )
    return [AnbieterKnopf(slug=e.slug, label=e.label) for e in eintraege]


# ---------------------------------------------------------------------------
# Hinweg
# ---------------------------------------------------------------------------


@router.get("/{slug}/login")
async def login_start(slug: str, db: DbSession) -> RedirectResponse:
    """Zum Anbieter weiterleiten - der Klick auf den Knopf.

    Ein schlichtes GET, damit die Anmeldeseite einen Link daraus machen kann.
    Scheitert es (Anbieter nicht erreichbar, oeffentliche Adresse fehlt),
    landet der Browser wieder auf der Anmeldeseite mit einer Kennung - eine
    JSON-Fehlerantwort saehe an dieser Stelle nur der Mensch, der am
    wenigsten damit anfangen kann.
    """
    anbieter = _anbieter(db, slug)
    try:
        rueckkehr = _rueckkehr_adresse(db, slug)
        beschreibung = await oidc.discovery(anbieter.issuer_url)
    except oidc.OidcFehler as fehler:
        return _oberflaeche(LOGIN_ZIEL, oidc_fehler=fehler.code)

    anlauf = oidc.anlauf_erzeugen()
    antwort = RedirectResponse(
        oidc.autorisierungs_adresse(beschreibung, anbieter.client_id, rueckkehr, anlauf),
        status_code=status.HTTP_302_FOUND,
    )
    _cookie_setzen(antwort, oidc.zustand_verpacken(slug, "login", anlauf))
    return antwort


@router.post("/{slug}/link/start", response_model=LinkStart)
async def link_start(
    slug: str, response: Response, db: DbSession, user: AdultUser
) -> LinkStart:
    """Verknuepfen aus dem Profil - Schritt 1.

    Anders als beim Anmelden kommt der Aufruf **mit** Sitzung und deshalb als
    gewoehnlicher API-Aufruf: Die Oberflaeche holt sich die Adresse und
    schickt den Browser selbst dorthin. Ein GET mit Weiterleitung ginge nicht,
    weil eine Browser-Navigation den ``Authorization``-Kopf nicht traegt.
    """
    anbieter = _anbieter(db, slug)
    try:
        rueckkehr = _rueckkehr_adresse(db, slug)
        beschreibung = await oidc.discovery(anbieter.issuer_url)
    except oidc.OidcFehler as fehler:
        raise HTTPException(
            status_code=fehler.status_code,
            detail={"code": fehler.code, "message": fehler.message},
        ) from fehler

    anlauf = oidc.anlauf_erzeugen()
    _cookie_setzen(response, oidc.zustand_verpacken(slug, "link", anlauf, user_id=user.id))
    return LinkStart(
        url=oidc.autorisierungs_adresse(beschreibung, anbieter.client_id, rueckkehr, anlauf)
    )


# ---------------------------------------------------------------------------
# Rueckweg
# ---------------------------------------------------------------------------


@router.get("/{slug}/callback")
async def callback(
    slug: str,
    request: Request,
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Die Rueckkehr vom Anbieter - fuer beide Zwecke.

    Die Reihenfolge der Pruefungen ist Absicht: Erst der eigene Zustand
    (Cookie, ``state``), dann die Bremse, dann der Anbieter, dann das Konto.
    Nichts wird geschrieben, bevor nicht alles davor bestanden ist.
    """
    anbieter = _anbieter(db, slug)

    zustand = oidc.zustand_lesen(request.cookies.get(oidc.COOKIE_NAME))
    zweck = (zustand or {}).get("zweck", "login")
    ziel = PROFIL_ZIEL if zweck == "link" else LOGIN_ZIEL

    def scheitern(kennung: str) -> RedirectResponse:
        antwort = _oberflaeche(ziel, oidc_fehler=kennung)
        _cookie_loeschen(antwort)
        return antwort

    # Der Anbieter meldet selbst einen Fehler - meistens "abgebrochen".
    if error:
        logger.info("OIDC: provider %r sent the browser back with error=%r", slug, error)
        return scheitern("oidc_denied")

    if (
        zustand is None
        or not code
        or not state
        or zustand.get("slug") != slug
        or zustand.get("state") != state
    ):
        # Cookie fehlt (abgelaufen, anderer Browser) oder der ``state`` passt
        # nicht - dann gehoert diese Antwort nicht zu einem Lauf, den **dieser**
        # Browser begonnen hat.
        return scheitern("oidc_state_mismatch")

    # ⚠️ Die Bremse zaehlt erst **nach** der state-Pruefung: Was daran schon
    # scheitert, hat den Anbieter nie gesehen und kann beliebig billig
    # erzeugt werden - es soll niemandem den Zaehler fuellen. Ab hier dagegen
    # steckt in jedem Versuch ein echter Lauf beim Anbieter.
    bremse = anmeldebremse.torwaechter(request, "oidc", slug)

    try:
        beschreibung = await oidc.discovery(anbieter.issuer_url)
        id_token = await oidc.code_tauschen(
            beschreibung,
            anbieter.client_id,
            anbieter.client_secret,
            code,
            _rueckkehr_adresse(db, slug),
            str(zustand.get("verifier", "")),
        )
        identitaet = await oidc.ausweis_pruefen(
            beschreibung, anbieter.client_id, id_token, str(zustand.get("nonce", ""))
        )
    except oidc.OidcFehler as fehler:
        anmeldebremse.gescheitert(bremse)
        return scheitern(fehler.code)

    # Ab hier buergt der Anbieter fuer die Identitaet - alles Weitere sind
    # Konto-Fragen, keine Rateversuche.
    anmeldebremse.geklappt(bremse)

    if zweck == "link":
        benutzer = db.get(User, int(zustand.get("uid", 0)))
        if benutzer is None or not benutzer.is_active:
            return scheitern("oidc_state_mismatch")
        if oidc_accounts.is_blocked(db, identitaet.issuer, identitaet.subject):
            return scheitern("oidc_blocked")
        fremd = oidc_accounts.find_linked(db, identitaet)
        if fremd is not None and fremd.id != benutzer.id:
            return scheitern("oidc_link_conflict")
        oidc_accounts.link(benutzer, identitaet)
        db.commit()
        antwort = _oberflaeche(ziel, oidc="verknuepft")
        _cookie_loeschen(antwort)
        return antwort

    try:
        benutzer = oidc_accounts.resolve(
            db, load_settings(db), identitaet, auto_create=anbieter.auto_create
        )
    except KontoFehler as fehler:
        db.rollback()
        return scheitern(fehler.code)

    benutzer.last_login_at = utcnow()
    db.commit()

    # Die Weiterleitung traegt das Erneuerungs-Cookie hinaus; das Zugangs-Token
    # holt sich die Oberflaeche gleich darauf ueber ``/api/auth/refresh``. Eine
    # Adresse mit Token darin waere der eine Ort, an dem es in jedem Verlauf
    # und jedem Proxy-Protokoll stuende.
    antwort = _oberflaeche(ziel, oidc="angemeldet")
    _cookie_loeschen(antwort)
    sitzung.starten(antwort, request, benutzer)
    logger.info("User %r signed in via OIDC provider %r", benutzer.username, slug)
    return antwort


# ---------------------------------------------------------------------------
# Trennen (eigenes Profil)
# ---------------------------------------------------------------------------


@router.delete("/{slug}/link", response_model=UserPublic)
def link_delete(slug: str, db: DbSession, user: AdultUser) -> UserPublic:
    """Die eigene Verknuepfung bei diesem Anbieter loesen.

    Derselbe Aussperrschutz wie beim Media-Server: Wer danach keinen Weg mehr
    hinein haette, wird abgewiesen, bevor etwas passiert.
    """
    anbieter = db.scalar(select(OidcProvider).where(OidcProvider.slug == slug))
    issuer = anbieter.issuer_url if anbieter is not None else None
    if issuer is None:
        # Der Anbieter-Eintrag kann geloescht sein, waehrend die Verknuepfung
        # noch steht - dann traegt die Verknuepfung selbst die Adresse.
        vorhandene = {z.issuer for z in user.oidc_links}
        if len(vorhandene) == 1:
            issuer = vorhandene.pop()
    if issuer is None or oidc_accounts.verknuepfung(user, issuer) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "oidc_not_linked", "message": "Hier ist nichts verknüpft."},
        )
    try:
        oidc_accounts.loesen(user, issuer)
    except KontoFehler as fehler:
        raise HTTPException(
            status_code=fehler.status_code,
            detail={"code": fehler.code, "message": fehler.message},
        ) from fehler
    db.commit()
    db.refresh(user)
    return UserPublic.model_validate(user)
