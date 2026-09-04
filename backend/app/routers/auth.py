"""Anmeldung, Token-Erneuerung und eigenes Profil."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import meldungen
from ..deps import AdultUser, CurrentUser, DbSession
from ..models import ChannelTarget, User, utcnow
from ..schemas import (
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    TokenPair,
    UserPublic,
    VerificationSent,
)
from ..security import (
    decode_token,
    hash_password,
    verify_password,
)
from ..services import accounts, anmeldebremse, api_schluessel, avatars, mail, sitzung, tokens
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger("nexview.auth")

_INVALID_LOGIN = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail=meldungen.meldung("bad_credentials", "Benutzername oder Passwort ist falsch."),
)

# Gegen einen echten Hash pruefen, wenn es den Benutzer gar nicht gibt - sonst
# koennte man an der Antwortzeit ablesen, welche Benutzernamen existieren.
_DUMMY_HASH = hash_password("nexview-dummy-password")


@router.post("/login", response_model=TokenPair)
def login(
    payload: LoginRequest, request: Request, response: Response, db: DbSession
) -> TokenPair:
    # Benutzername *oder* E-Mail-Adresse, beides ohne Ruecksicht auf Gross- und
    # Kleinschreibung. Adressen liegen ohnehin klein geschrieben in der
    # Datenbank; beim Benutzernamen muss verglichen werden.
    eingabe = payload.username.strip()

    # Die Bremse zaehlt gegen die **Eingabe**, nicht gegen den gefundenen
    # Benutzer: Sonst waere ein Name, den es gar nicht gibt, beliebig oft
    # frei - und genau damit faengt jeder Angriff an.
    bremse = anmeldebremse.torwaechter(request, "login", eingabe)

    user = db.scalar(
        select(User).where(
            (func.lower(User.username) == eingabe.lower())
            | (User.email == tokens.normalize_email(eingabe))
        )
    )

    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        anmeldebremse.gescheitert(bremse)
        raise _INVALID_LOGIN

    if not verify_password(payload.password, user.password_hash):
        anmeldebremse.gescheitert(bremse)
        raise _INVALID_LOGIN

    # Ab hier stimmt das Passwort. Der Zaehler ist erledigt, **auch wenn die
    # Anmeldung gleich noch scheitert**: Ein deaktiviertes Konto oder eine
    # unbestaetigte Adresse ist kein Rateversuch, sondern jemand, der sein
    # Passwort kennt. Wuerde das mitzaehlen, sperrte sich genau die Person
    # aus, die gerade auf ihre Bestaetigungsmail wartet und es alle zwei
    # Minuten noch einmal versucht.
    anmeldebremse.geklappt(bremse)

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung(
                "account_disabled_ask_admin",
                "Dieses Konto ist deaktiviert. Bitte wende dich an den Administrator.",
            ),
        )

    # Ohne bestaetigte Adresse gibt es keine Sitzung. Die Sperre sitzt hier und
    # nicht an jeder einzelnen Anfrage: sonst waere schon der
    # Einrichtungsassistent blockiert, der ja gerade erst dabei ist, den
    # Mailserver einzurichten. Der Code hilft der Oberflaeche, den richtigen
    # Ausweg anzubieten, statt nur "verboten" zu melden.
    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "email_unverified",
                "message": (
                    "Deine E-Mail-Adresse ist noch nicht bestätigt. "
                    "Bitte klicke auf den Link in der Bestätigungsmail."
                ),
                "email": user.email or "",
            },
        )

    user.last_login_at = utcnow()
    db.commit()

    return sitzung.starten(response, request, user)


_ABGELAUFEN = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail=meldungen.meldung("session_expired", "Sitzung abgelaufen. Bitte erneut anmelden."),
)


@router.post("/refresh", response_model=TokenPair)
def refresh(request: Request, response: Response, db: DbSession) -> TokenPair:
    """Zugangs-Token erneuern.

    Das Erneuerungs-Token kommt aus dem HttpOnly-Cookie, nicht mehr aus dem
    Anfragekoerper. Wer noch mit einem Token aus dem ``localStorage`` einer
    aelteren Version kommt, bekommt hier ein 401 und landet auf der
    Anmeldeseite - das ist der einmalige Rauswurf beim Umstieg auf 0.21.

    Der Endpunkt ist die **einzige** Stelle der API, die ueber ein Cookie
    beglaubigt wird, und damit die einzige mit CSRF-Flaeche. Sie ist folgenlos:
    ``SameSite=Lax`` laesst das Cookie bei einem fremdveranlassten POST gar
    nicht mitfahren, und die Antwort duerfte eine fremde Seite ohnehin nicht
    lesen. Ausfuehrlich in ``services/sitzung.py``.
    """
    roh = sitzung.gelesen(request)
    if roh is None:
        raise _ABGELAUFEN

    inhalt = decode_token(roh, "refresh")
    if inhalt is None:
        raise _ABGELAUFEN

    user = db.get(User, inhalt.benutzer_id)
    if user is None or not user.is_active or not sitzung.gilt_noch(inhalt, user):
        raise _ABGELAUFEN

    return sitzung.starten(response, request, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    """Abmelden - das Cookie loeschen.

    Ohne Anmeldepruefung, mit Absicht: Wer sich abmelden will, soll das auch
    koennen, wenn sein Zugangs-Token laengst abgelaufen ist. Der Endpunkt tut
    nichts weiter, als ein Cookie dieses Browsers wegzunehmen - schaden kann
    das niemandem ausser dem, der ihn aufruft.

    ⚠️ Das Erneuerungs-Token selbst wird damit **nicht** ungueltig; es ist ein
    reines JWT ohne Eintrag in der Datenbank. Wer es vorher kopiert hat, kaeme
    damit weiter herein, bis es von selbst ablaeuft.

    **Das ist so gewollt.** Wuerde jedes Abmelden alle Sitzungen beenden, floege
    man beim Abmelden auf dem Handy auch vom Fernseher.

    Wer wirklich alle beenden will, hat seit 0.22 ``/me/ueberall-abmelden`` -
    das schliesst die Luecke, fuer die es vorher nur den Passwortwechsel gab.
    **Offen bleibt die feine Variante:** genau *diese eine* Sitzung entwerten,
    ohne die anderen anzufassen. Dafuer braeuchte es eine Merkliste beendeter
    Token.
    """
    sitzung.beenden(response, request)


@router.get("/me", response_model=UserPublic)
def read_me(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserPublic)
def update_me(payload: ProfileUpdate, user: CurrentUser, db: DbSession) -> User:
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or None
    if payload.language is not None:
        if payload.language not in {"de", "en"}:
            raise HTTPException(
                status_code=422,
                detail=meldungen.meldung("language_invalid", "Sprache muss 'de' oder 'en' sein."),
            )
        user.language = payload.language
    if payload.theme is not None:
        if payload.theme not in {"dark", "light"}:
            raise HTTPException(
                status_code=422,
                detail=meldungen.meldung(
                    "theme_invalid",
                    "Darstellung muss 'dark' oder 'light' sein.",
                ),
            )
        user.theme = payload.theme

    # ⚠️ **Abgeleitet, nicht aufgezaehlt - und das ist die Lehre aus einem
    # Fehler.** Hier stand eine Liste von Hand. Als spaeter ``mail_storage``
    # und ``mail_child_wish`` dazukamen, wurden sie ins Schema und in die
    # Oberflaeche eingetragen, aber nicht hierher: Zwei Haken im Profil liessen
    # sich setzen, sahen danach richtig aus - und bewirkten nichts. Auffallen
    # konnte das nicht, denn gescheitert ist nie etwas.
    #
    # Aus ``ProfileUpdate`` abgeleitet kann die Liste nicht mehr auseinander-
    # laufen: Was im Schema steht, wird geschrieben. ``test_mail_schalter.py``
    # prueft zusaetzlich, dass Schema und Konto dieselben Felder fuehren.
    for schalter in (feld for feld in ProfileUpdate.model_fields if feld.startswith("mail_")):
        wert = getattr(payload, schalter, None)
        if wert is not None:
            setattr(user, schalter, wert)

    # Leerer String heisst "nichts Eigenes": dann gilt wieder die Vorgabe des
    # Admins.
    if payload.discover_region is not None:
        user.discover_region = payload.discover_region.strip().upper() or None

    db.commit()
    db.refresh(user)
    return user


class EmailChange(BaseModel):
    email: str = Field(min_length=3, max_length=255)


@router.put("/me/email", response_model=UserPublic)
async def change_my_email(payload: EmailChange, user: AdultUser, db: DbSession) -> User:
    """Eigene Adresse aendern.

    Die neue Adresse gilt erst als bestaetigt, wenn der Link aus der Mail
    angeklickt wurde - sonst koennte man sich mit einer fremden Adresse
    eintragen. Die *alte* wird gewarnt: das ist die uebliche Absicherung, falls
    jemand anderes am Konto sitzt.
    """
    neue = tokens.normalize_email(payload.email)
    if not mail.valid_address(neue):
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "email_invalid",
                "Das ist keine gültige E-Mail-Adresse.",
            ),
        )
    if neue == (user.email or ""):
        return user

    if db.scalar(select(User.id).where(User.email == neue, User.id != user.id)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "email_taken_other_account",
                "Diese Adresse gehört bereits zu einem anderen Konto.",
            ),
        )

    alte = user.email
    user.email = neue
    user.email_verified = False
    db.commit()
    db.refresh(user)

    settings = load_settings(db)
    await accounts.send_verification(db, settings, user)
    if alte:
        await accounts.notify_address_change(settings, alte, neue, user.language)

    logger.info("User %r changed their address", user.username)
    return user


@router.post("/me/resend-verification", response_model=VerificationSent)
async def resend_verification(user: AdultUser, db: DbSession) -> VerificationSent:
    """Bestaetigungsmail erneut anfordern."""
    if not user.email:
        raise HTTPException(
            status_code=409,
            detail=meldungen.meldung(
                "email_missing",
                "Es ist keine Adresse hinterlegt.",
            ),
        )
    if user.email_verified:
        raise HTTPException(
            status_code=409,
            detail=meldungen.meldung(
                "email_already_verified",
                "Deine Adresse ist bereits bestätigt.",
            ),
        )

    zustellung = await accounts.send_verification(db, load_settings(db), user)
    return VerificationSent(sent=zustellung.sent, error=zustellung.error)


@router.post("/me/avatar", response_model=UserPublic)
async def upload_avatar(user: AdultUser, db: DbSession, file: UploadFile = File(...)) -> User:
    """Eigenes Profilbild hochladen."""
    try:
        user.avatar_path = avatars.save(await file.read(), user.avatar_path)
    except avatars.AvatarError as error:
        raise HTTPException(status_code=400, detail=error.message) from error

    db.commit()
    db.refresh(user)
    return user


@router.delete("/me/avatar", response_model=UserPublic)
def delete_avatar(user: AdultUser, db: DbSession) -> User:
    avatars.remove(user.avatar_path)
    user.avatar_path = None
    db.commit()
    db.refresh(user)
    return user


@router.post("/me/password", response_model=TokenPair)
def change_own_password(
    payload: PasswordChange,
    request: Request,
    response: Response,
    user: AdultUser,
    db: DbSession,
) -> TokenPair:
    """Eigenes Passwort aendern - und damit alle **anderen** Geraete abmelden.

    Seit 0.21 gilt jedes Token, das aelter ist als ``password_changed_at``,
    nicht mehr (``sitzung.gilt_noch``). Genau deshalb gibt dieser Endpunkt ein
    frisches Paar zurueck statt eines leeren 204: Ohne das wuerde sich jeder,
    der sein Passwort aendert, im selben Moment selbst aussperren - und der
    haeufigste Grund, es zu aendern, ist der Verdacht, dass jemand anderes
    drin ist. Wer sich aussperrt, waehrend der Angreifer bleibt, haette
    nichts gewonnen.

    Dass das frische Paar seinen eigenen Riegel passiert, kommt allein daher,
    dass es **nach** ``password_changed_at`` entsteht - der Zeitstempel im
    Token ist auf die Millisekunde genau (siehe ``security._create_token``).
    Ein Sonderfall ist dafuer nicht noetig.
    """
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung(
                "current_password_wrong",
                "Das aktuelle Passwort ist falsch.",
            ),
        )
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = utcnow()
    db.commit()

    logger.info("User %r changed their password; other sessions were ended", user.username)
    return sitzung.starten(response, request, user)


# ---------------------------------------------------------------------------
# Persoenliche Zugriffs-Schluessel
# ---------------------------------------------------------------------------


class SchluesselPublic(BaseModel):
    """Ein Schluessel, wie ihn die Liste zeigt - **nie** mit dem Klartext."""

    id: int
    name: str
    vorschau: str
    nur_lesen: bool
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    #: Wohin Nexview diese Anbindung benachrichtigt - ``None``, wenn sie nicht
    #: danach gefragt hat.
    #:
    #: ⚠️ **Steht hier und in keiner Kanalverwaltung.** Ein Rueckkanal wird
    #: nicht eingerichtet, sondern von einer Anbindung angemeldet; er gehoert
    #: deshalb neben den Schluessel, mit dem sie sich anmeldet, und stirbt mit
    #: ihm. Unsichtbar duerfte er trotzdem nicht sein: Was man nicht sieht,
    #: kann man nicht abschalten, und ein abgeschaltetes Home Assistant fuellt
    #: sonst still den Postausgang mit Fehlversuchen.
    rueckkanal: str | None = None
    #: Ist er bestaetigt, also im Betrieb?
    rueckkanal_bereit: bool = False


class SchluesselNeu(SchluesselPublic):
    """Nur bei der Antwort aufs Anlegen: **einmalig** der Klartext.

    ⚠️ Ein eigener Typ und nicht ein zusaetzliches Feld am gewoehnlichen: So
    kann der Klartext gar nicht versehentlich in einer Liste landen.
    """

    schluessel: str


class SchluesselAnlegen(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    nur_lesen: bool = False
    #: ``None`` = gilt bis zum Widerruf.
    tage: int | None = Field(default=None, ge=1, le=3650)


def _als_public(eintrag, ziel: ChannelTarget | None = None) -> SchluesselPublic:
    return SchluesselPublic(
        id=eintrag.id,
        name=eintrag.name,
        vorschau=eintrag.vorschau,
        nur_lesen=eintrag.nur_lesen,
        created_at=eintrag.created_at,
        expires_at=eintrag.expires_at,
        last_used_at=eintrag.last_used_at,
        rueckkanal=ziel.url if ziel is not None else None,
        rueckkanal_bereit=ziel.verified if ziel is not None else False,
    )


@router.get("/me/schluessel", response_model=list[SchluesselPublic])
def schluessel_liste(user: AdultUser, db: DbSession) -> list[SchluesselPublic]:
    """Die eigenen Zugriffs-Schluessel, jeder mit seinem Rueckkanal."""
    eintraege = list(api_schluessel.liste(db, user))
    # Eine Abfrage fuer alle statt einer je Schluessel: Es sind hoechstens eine
    # Handvoll, aber die Liste laedt bei jedem Blick ins Profil.
    ziele = {
        z.api_key_id: z
        for z in db.scalars(
            select(ChannelTarget).where(
                ChannelTarget.api_key_id.in_([e.id for e in eintraege] or [0])
            )
        )
    }
    return [_als_public(e, ziele.get(e.id)) for e in eintraege]


@router.delete(
    "/me/schluessel/{schluessel_id}/rueckkanal", status_code=status.HTTP_204_NO_CONTENT
)
def rueckkanal_trennen(schluessel_id: int, user: AdultUser, db: DbSession) -> None:
    """Nexview soll diese Anbindung nicht mehr anrufen. Der Schluessel bleibt.

    ⚠️ **Warum das nicht dasselbe ist wie den Schluessel zu widerrufen.** Ein
    Home Assistant, das neu aufgesetzt wurde, ist unter seiner alten Adresse
    nicht mehr zu erreichen - der Schluessel funktioniert aber weiter, und ihn
    wegzuwerfen hiesse, ihn ueberall neu eintragen zu muessen. Hier geht nur
    der Weg zurueck weg; die Anbindung fragt weiter ab.
    """
    ziel = db.scalars(
        select(ChannelTarget).where(
            ChannelTarget.api_key_id == schluessel_id,
            # ⚠️ Nicht nur nach der Kennung: Sonst raeumte eine geratene Zahl
            # den Rueckkanal eines Fremden ab.
            ChannelTarget.user_id == user.id,
        )
    ).first()
    if ziel is not None:
        db.delete(ziel)
        db.commit()


@router.post("/me/schluessel", response_model=SchluesselNeu, status_code=status.HTTP_201_CREATED)
def schluessel_anlegen(
    payload: SchluesselAnlegen, user: AdultUser, db: DbSession
) -> SchluesselNeu:
    """Einen Zugriffs-Schluessel erzeugen.

    ⚠️ **Der Klartext steht nur in dieser einen Antwort.** Danach gibt es ihn
    nirgends mehr - auch der Administrator kann ihn nicht nachschlagen.
    """
    try:
        eintrag, klartext = api_schluessel.anlegen(
            db, user, name=payload.name, nur_lesen=payload.nur_lesen, tage=payload.tage
        )
    except api_schluessel.SchluesselFehler as fehler:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung(fehler.code, fehler.text),
        ) from fehler

    return SchluesselNeu(**_als_public(eintrag).model_dump(), schluessel=klartext)


@router.delete("/me/schluessel/{schluessel_id}", status_code=status.HTTP_204_NO_CONTENT)
def schluessel_widerrufen(schluessel_id: int, user: AdultUser, db: DbSession) -> None:
    """Einen eigenen Schluessel entfernen - ab sofort geht damit nichts mehr."""
    try:
        api_schluessel.widerrufen(db, user, schluessel_id)
    except api_schluessel.SchluesselFehler as fehler:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=meldungen.meldung(fehler.code, fehler.text),
        ) from fehler


@router.post("/me/ueberall-abmelden", response_model=TokenPair)
def abmelden_ueberall(
    request: Request,
    response: Response,
    user: AdultUser,
    db: DbSession,
) -> TokenPair:
    """Alle anderen Geraete abmelden - ohne das Passwort zu aendern.

    ⚠️ **Der Ausweg, den es bis 0.22 nicht gab.** Gewoehnliches Abmelden nimmt
    nur das Cookie aus *diesem* Browser; wer eine Kopie davon hat, kommt damit
    weiter herein, bis es ablaeuft. Der einzige Riegel war bis dahin ein
    Passwortwechsel - was heisst, dass man sein Passwort aendern musste, obwohl
    mit dem Passwort nichts war.

    Das frische Paar am Ende ist derselbe Gedanke wie beim Passwortwechsel:
    Wer den Verdacht hat, dass jemand mitliest, soll **den anderen**
    hinauswerfen, nicht sich selbst. Es passiert seinen eigenen Riegel, weil
    es **nach** dem Stempel entsteht - auf die Millisekunde genau.

    ⚠️ **Und die Zugriffs-Schluessel gehen mit.** Sie nehmen einen zweiten Weg
    durch ``deps.get_current_user``, der ``sitzung.gilt_noch`` nie aufruft -
    ein Stempel allein liesse sie also weiterlaufen. Wer diesen Knopf drueckt,
    sagt "jemand liest mit"; ein Riegel, hinter dem eine Tuer offen bleibt,
    waere schlimmer als keiner, weil er Sicherheit vorgibt.

    Beim **Passwortwechsel** bleiben die Schluessel bewusst stehen: Das ist
    meistens Hausputz, und jede Anbindung dabei stumm sterben zu lassen waere
    eine Ueberraschung. Die Oberflaeche sagt dort, wo man sie widerruft.
    """
    user.sessions_valid_from = utcnow()
    db.commit()

    widerrufen = api_schluessel.alle_widerrufen(db, user)

    logger.info(
        "User %r ended all other sessions and %d API key(s)", user.username, widerrufen
    )
    return sitzung.starten(response, request, user)
