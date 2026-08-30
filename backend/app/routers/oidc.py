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
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import get_settings
from ..crypto import encrypt
from ..deps import AdminUser, AdultUser, DbSession
from ..models import OidcBlock, OidcLink, OidcProvider, User, utcnow
from ..schemas import UserPublic
from ..services import anmeldebremse, oidc, oidc_accounts, sitzung
from ..services.mediaserver_accounts import KontoFehler
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/auth/oidc", tags=["oidc"])
admin_router = APIRouter(prefix="/api/admin/oidc", tags=["oidc"])

logger = logging.getLogger("nexview.oidc")

#: Wohin der Browser nach dem Rueckweg gefuehrt wird - Routen der Oberflaeche.
#: ``/login`` gibt es dort gar nicht als eigene Route, und genau das traegt:
#: Ohne Sitzung zeigt die App auf jedem Pfad die Anmeldeseite (die den
#: Fehler-Parameter liest), und **mit** frischer Sitzung faengt der
#: Auffangpfad des Routers die Adresse und raeumt sie auf die Startseite.
LOGIN_ZIEL = "/login"
PROFIL_ZIEL = "/profil"


class AnbieterKnopf(BaseModel):
    """Was Anmeldeseite und Profil brauchen - nicht mehr.

    Diese Liste ist **oeffentlich**, sie steht vor der Anmeldung: Client-ID,
    Geheimnis und Schalter bleiben drinnen. Die Anbieter-Adresse steht dabei -
    sie ist kein Geheimnis (der erste Klick auf den Knopf zeigt sie in der
    Adresszeile), und das Profil ordnet darueber seine Verknuepfungen zu.
    """

    slug: str
    label: str
    issuer_url: str


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
    return [
        AnbieterKnopf(slug=e.slug, label=e.label, issuer_url=e.issuer_url)
        for e in eintraege
    ]


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
    # ``?reiter=anmeldung`` oeffnet im Profil direkt die richtige Karte - der
    # Browser war beim Anbieter und soll nicht vor dem falschen Reiter landen.
    extra = {"reiter": "anmeldung"} if zweck == "link" else {}

    def scheitern(kennung: str) -> RedirectResponse:
        antwort = _oberflaeche(ziel, oidc_fehler=kennung, **extra)
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
        antwort = _oberflaeche(ziel, oidc="verknuepft", **extra)
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


@router.delete("/link", response_model=UserPublic)
def link_delete(issuer: str, db: DbSession, user: AdultUser) -> UserPublic:
    """Die eigene Verknuepfung bei diesem Anbieter loesen.

    Ueber die Anbieter-**Adresse**, nicht das Kuerzel: Die Verknuepfung haengt
    an der Adresse, und sie muss sich auch dann loesen lassen, wenn der
    Administrator den Anbieter-Eintrag laengst geloescht hat - genau wie beim
    Media-Server, wo ``?provider=`` dieselbe Rolle spielt. Derselbe
    Aussperrschutz wie ueberall: Wer danach keinen Weg mehr hinein haette,
    wird abgewiesen, bevor etwas passiert.
    """
    adresse = issuer.rstrip("/")
    if oidc_accounts.verknuepfung(user, adresse) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "oidc_not_linked", "message": "Hier ist nichts verknüpft."},
        )
    try:
        oidc_accounts.loesen(user, adresse)
    except KontoFehler as fehler:
        raise HTTPException(
            status_code=fehler.status_code,
            detail={"code": fehler.code, "message": fehler.message},
        ) from fehler
    db.commit()
    db.refresh(user)
    return UserPublic.model_validate(user)


# ---------------------------------------------------------------------------
# Verwaltung (Administrator)
# ---------------------------------------------------------------------------

#: Klein geschrieben, mit Bindestrichen - es steht in einer Adresse. Und ab
#: dem Anlegen fest: Beim Anbieter ist es Teil der Rueckkehr-Adresse.
_SLUG_MUSTER = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class AnbieterAdmin(BaseModel):
    """Ein Eintrag, wie ihn die Einstellungsseite zeigt.

    ``client_secret_vorschau`` statt des Geheimnisses - wie bei jedem anderen
    Schluessel. ``rueckkehr_adresse`` ist der Wert, den der Administrator beim
    Anbieter eintraegt; leer, solange die oeffentliche Adresse fehlt.
    """

    id: int
    slug: str
    label: str
    issuer_url: str
    client_id: str
    client_secret_vorschau: str
    auto_create: bool
    enabled: bool
    created_at: datetime
    rueckkehr_adresse: str
    verknuepfte: int


class AnbieterAnlegen(BaseModel):
    slug: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    issuer_url: str = Field(min_length=1, max_length=500)
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str = Field(min_length=1, max_length=500)
    auto_create: bool = False
    enabled: bool = True


class AnbieterAendern(BaseModel):
    """Alles ausser dem Kuerzel - das ist Teil der Rueckkehr-Adresse und fest.

    Ein leeres ``client_secret`` heisst "behalten": Die Seite zeigt das
    Geheimnis nie an, also darf ein unangefasstes Feld es nicht loeschen.
    """

    label: str | None = Field(default=None, min_length=1, max_length=80)
    issuer_url: str | None = Field(default=None, min_length=1, max_length=500)
    client_id: str | None = Field(default=None, min_length=1, max_length=255)
    client_secret: str | None = Field(default=None, max_length=500)
    auto_create: bool | None = None
    enabled: bool | None = None


class PruefErgebnis(BaseModel):
    """Was der Pruef-Knopf meldet - strukturiert, nie als Fehlerantwort.

    Ein nicht erreichbarer Anbieter ist hier kein Ausnahmefall, sondern genau
    die Auskunft, um die gebeten wurde.
    """

    ok: bool
    code: str | None = None
    aussteller: str | None = None


class GefaehrdetesKonto(BaseModel):
    id: int
    username: str
    display_name: str | None


class LoeschFolgen(BaseModel):
    """Was ein Loeschen dieses Anbieters anrichten wuerde.

    Wie beim Media-Server bewusst **immer** abrufbar - ein Hinweis, der nur im
    Ernstfall erscheint, wird beim ersten Mal nicht gelesen.
    """

    verknuepft: int
    gefaehrdet: list[GefaehrdetesKonto]


def _normalisiert(issuer_url: str) -> str:
    adresse = issuer_url.strip().rstrip("/")
    if not adresse.lower().startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "url_needs_scheme",
                "message": "Die Adresse muss mit http:// oder https:// beginnen.",
            },
        )
    return adresse


def _eintrag(db: DbSession, provider_id: int) -> OidcProvider:
    eintrag = db.get(OidcProvider, provider_id)
    if eintrag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "oidc_unknown_provider", "message": "Diesen Anbieter gibt es nicht."},
        )
    return eintrag


def _als_admin(db: DbSession, eintrag: OidcProvider) -> AnbieterAdmin:
    settings = load_settings(db)
    return AnbieterAdmin(
        id=eintrag.id,
        slug=eintrag.slug,
        label=eintrag.label,
        issuer_url=eintrag.issuer_url,
        client_id=eintrag.client_id,
        # Bewusst ohne die letzten Zeichen, anders als bei den API-Schluesseln:
        # Zum Wiedererkennen reicht "gesetzt", und ein Geheimnis, von dem nie
        # ein Zeichen die Datenbank verlaesst, ist das bessere Geheimnis.
        client_secret_vorschau="••••" if eintrag.client_secret else "",
        auto_create=eintrag.auto_create,
        enabled=eintrag.enabled,
        created_at=eintrag.created_at,
        rueckkehr_adresse=(
            settings.link(f"api/auth/oidc/{eintrag.slug}/callback")
            if settings.public_url
            else ""
        ),
        verknuepfte=db.scalar(
            select(func.count())
            .select_from(OidcLink)
            .where(OidcLink.issuer == eintrag.issuer_url)
        )
        or 0,
    )


def _loesch_folgen(db: DbSession, eintrag: OidcProvider) -> LoeschFolgen:
    konten = list(
        db.scalars(
            select(User)
            .join(OidcLink, OidcLink.user_id == User.id)
            .where(OidcLink.issuer == eintrag.issuer_url)
            .order_by(User.username)
        )
    )
    return LoeschFolgen(
        verknuepft=len(konten),
        gefaehrdet=[
            GefaehrdetesKonto(
                id=konto.id, username=konto.username, display_name=konto.display_name
            )
            for konto in konten
            if oidc_accounts.kaeme_nicht_mehr_herein(konto, ohne=eintrag.issuer_url)
        ],
    )


@admin_router.get("", response_model=list[AnbieterAdmin])
def admin_liste(db: DbSession, admin: AdminUser) -> list[AnbieterAdmin]:
    return [
        _als_admin(db, eintrag)
        for eintrag in db.scalars(select(OidcProvider).order_by(OidcProvider.id))
    ]


@admin_router.post("", response_model=AnbieterAdmin, status_code=status.HTTP_201_CREATED)
def admin_anlegen(payload: AnbieterAnlegen, db: DbSession, admin: AdminUser) -> AnbieterAdmin:
    """Einen Anbieter eintragen.

    ⚠️ **Ohne oeffentliche Adresse geht es nicht los** - aus ihr entsteht die
    Rueckkehr-Adresse, die der Administrator beim Anbieter hinterlegen muss.
    Die Pruefung sitzt beim Anlegen und nicht erst beim ersten Anmeldeversuch:
    Dort traefe sie den falschen Menschen.
    """
    if not load_settings(db).public_url:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "oidc_no_public_url",
                "message": "Die öffentliche Adresse von Nexview ist nicht eingestellt.",
            },
        )

    slug = payload.slug.strip().lower()
    if not _SLUG_MUSTER.fullmatch(slug):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "oidc_slug_invalid",
                "message": "Das Kürzel darf nur Kleinbuchstaben, Ziffern und Bindestriche enthalten.",
            },
        )
    adresse = _normalisiert(payload.issuer_url)

    if db.scalar(select(OidcProvider.id).where(OidcProvider.slug == slug)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "oidc_slug_taken", "message": "Dieses Kürzel ist bereits vergeben."},
        )
    if (
        db.scalar(select(OidcProvider.id).where(OidcProvider.issuer_url == adresse))
        is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "oidc_issuer_taken",
                "message": "Dieser Anbieter ist bereits eingetragen.",
            },
        )

    eintrag = OidcProvider(
        slug=slug,
        label=payload.label.strip(),
        issuer_url=adresse,
        client_id=payload.client_id.strip(),
        client_secret=encrypt(payload.client_secret),
        auto_create=payload.auto_create,
        enabled=payload.enabled,
    )
    db.add(eintrag)
    db.commit()
    db.refresh(eintrag)
    logger.info("OIDC provider %r (%r) created by %r", slug, adresse, admin.username)
    return _als_admin(db, eintrag)


@admin_router.patch("/{provider_id}", response_model=AnbieterAdmin)
def admin_aendern(
    provider_id: int, payload: AnbieterAendern, db: DbSession, admin: AdminUser
) -> AnbieterAdmin:
    eintrag = _eintrag(db, provider_id)

    if payload.issuer_url is not None:
        adresse = _normalisiert(payload.issuer_url)
        if adresse != eintrag.issuer_url:
            doppelt = db.scalar(
                select(OidcProvider.id).where(
                    OidcProvider.issuer_url == adresse, OidcProvider.id != eintrag.id
                )
            )
            if doppelt is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "oidc_issuer_taken",
                        "message": "Dieser Anbieter ist bereits eingetragen.",
                    },
                )
            # ⚠️ Eine andere Adresse heisst: ein anderer Anbieter, andere
            # Identitaeten. Bestehende Verknuepfungen haengen an der alten
            # Adresse und gelten fuer die neue schlicht nicht - das ist die
            # Norm, kein Datenverlust. Es steht im Protokoll, weil die Frage
            # "warum sind alle Verknuepfungen weg?" sonst hier ihre stille
            # Antwort haette.
            logger.warning(
                "OIDC provider %r changed issuer from %r to %r - existing links "
                "keep pointing at the old issuer and will not match the new one",
                eintrag.slug,
                eintrag.issuer_url,
                adresse,
            )
            eintrag.issuer_url = adresse
            oidc.cache_leeren()

    if payload.label is not None:
        eintrag.label = payload.label.strip()
    if payload.client_id is not None:
        eintrag.client_id = payload.client_id.strip()
    if payload.client_secret:
        eintrag.client_secret = encrypt(payload.client_secret)
    if payload.auto_create is not None:
        eintrag.auto_create = payload.auto_create
    if payload.enabled is not None:
        eintrag.enabled = payload.enabled

    db.commit()
    db.refresh(eintrag)
    return _als_admin(db, eintrag)


@admin_router.get("/{provider_id}/folgen", response_model=LoeschFolgen)
def admin_folgen(provider_id: int, db: DbSession, admin: AdminUser) -> LoeschFolgen:
    """Wen ein Loeschen treffen wuerde - **vor** dem Klick, nicht danach."""
    return _loesch_folgen(db, _eintrag(db, provider_id))


@admin_router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_loeschen(
    provider_id: int, db: DbSession, admin: AdminUser, bestaetigt: bool = False
) -> None:
    """Einen Anbieter loeschen.

    Die Verknuepfungen der Benutzer bleiben stehen - wer denselben Anbieter
    spaeter wieder eintraegt (gleiche Adresse), findet alles vor. Sind Konten
    gefaehrdet, deren einziger Weg hinein dieser Anbieter ist, wird wie beim
    Media-Server mit 409 abgelehnt; der Administrator kann das mit
    ``bestaetigt=true`` ueberstimmen und hinterher Passwoerter setzen.
    """
    eintrag = _eintrag(db, provider_id)
    folgen = _loesch_folgen(db, eintrag)
    if folgen.gefaehrdet and not bestaetigt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "oidc_would_lock_out_others",
                "message": (
                    f"{len(folgen.gefaehrdet)} Konten kämen ohne diesen Anbieter "
                    "nicht mehr herein."
                ),
                "gefaehrdet": [konto.model_dump() for konto in folgen.gefaehrdet],
            },
        )
    if folgen.gefaehrdet:
        logger.warning(
            "OIDC provider %r deleted by %r although %d account(s) lose their only "
            "way in: %s",
            eintrag.slug,
            admin.username,
            len(folgen.gefaehrdet),
            ", ".join(konto.username for konto in folgen.gefaehrdet),
        )

    db.delete(eintrag)
    db.commit()
    oidc.cache_leeren()
    logger.info("OIDC provider %r deleted by %r", eintrag.slug, admin.username)


class SperrEintrag(BaseModel):
    """Eine gesperrte Identitaet, wie die Liste sie zeigt."""

    id: int
    issuer: str
    display: str | None = None

    model_config = {"from_attributes": True}


@admin_router.get("/blocks", response_model=list[SperrEintrag])
def admin_sperrliste(db: DbSession, admin: AdminUser) -> list[SperrEintrag]:
    """Gesperrte Identitaeten - entstanden beim Loeschen von Konten.

    Das Gegenstueck zur Medienserver-Sperrliste: Ohne diese Ansicht waere
    eine Sperre fuer immer, denn sie entsteht still beim Loeschen.
    """
    return [
        SperrEintrag.model_validate(zeile)
        for zeile in db.scalars(select(OidcBlock).order_by(OidcBlock.blocked_at.desc()))
    ]


@admin_router.delete("/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_sperre_aufheben(block_id: int, db: DbSession, admin: AdminUser) -> None:
    """Sperre aufheben - danach darf diese Identitaet wieder herein."""
    eintrag = db.get(OidcBlock, block_id)
    if eintrag is not None:
        db.delete(eintrag)
        db.commit()


@admin_router.post("/{provider_id}/pruefen", response_model=PruefErgebnis)
async def admin_pruefen(provider_id: int, db: DbSession, admin: AdminUser) -> PruefErgebnis:
    """Der Pruef-Knopf: Meldet sich unter der Adresse ein Anbieter?

    Holt die Selbstauskunft **frisch** (am Zwischenspeicher vorbei) - wer den
    Knopf drueckt, hat gerade etwas geaendert und will die Wahrheit von jetzt,
    nicht die von vor einer Stunde. Mehr als die Selbstauskunft laesst sich
    ohne einen echten Anmeldelauf nicht pruefen; ob Client-ID und Geheimnis
    stimmen, zeigt erst der erste Knopfdruck auf der Anmeldeseite.
    """
    eintrag = _eintrag(db, provider_id)
    try:
        beschreibung = await oidc.discovery(eintrag.issuer_url, frisch=True)
    except oidc.OidcFehler as fehler:
        return PruefErgebnis(ok=False, code=fehler.code)
    return PruefErgebnis(ok=True, aussteller=str(beschreibung["issuer"]))
