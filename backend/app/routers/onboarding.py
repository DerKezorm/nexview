"""Links aus E-Mails einloesen - bewusst ohne Anmeldung erreichbar.

Wer hierherkommt, hat noch kein Passwort oder es vergessen. Der Nachweis ist
der Einmal-Link selbst: nur wer die Mail bekommen hat, kennt ihn.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import DbSession
from ..models import AuthToken, Role, TokenPurpose, User, utcnow
from ..schemas import MIN_PASSWORD_LENGTH, VerificationSent
from ..security import hash_password, verify_password
from ..services import accounts, anmeldebremse, mail, tokens
from ..services.settings_service import load_settings
from .. import meldungen

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

logger = logging.getLogger("nexview.onboarding")


def _token_bremse(request: Request) -> list[str]:
    """Bremse fuer die Routen, deren Geheimnis in der Adresszeile steht.

    ⚠️ **Das ist bewusst nur eine halbe Bremse, und das hat einen Grund.**
    Diese Token haben 32 Byte Zufall (``tokens.TOKEN_BYTES``), also 256 Bit -
    sie sind nicht ratbar. Eine Sperre *je Token* waere Theater gegen einen
    Angriff, den es nicht gibt.

    Was bleibt, ist blosses Haemmern: Jeder Aufruf kostet eine Datenbank-
    abfrage, und dafuer braucht niemand ein Konto. Deshalb zaehlt hier **nur
    die Adresse** (``kennung=None``) - und nur, wenn ``NEXVIEW_CLIENT_IP``
    gesetzt ist, Nexview die echte Adresse also kennt.

    Eine haus-weite Sperre waere hier falsch: Sie liesse sich von aussen
    ausloesen und wuerde dann allen das Zuruecksetzen ihres Passworts
    verbauen - ein echter Schaden als Abwehr gegen einen unmoeglichen Angriff.
    """
    return anmeldebremse.torwaechter(request, "token", None)

ABGELAUFEN = "Dieser Link ist abgelaufen oder wurde bereits verwendet."


class InvitationInfo(BaseModel):
    """Was das Einladungsformular anzeigen darf - mehr nicht."""

    email: str
    role: Role


class AcceptInvitation(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    display_name: str | None = Field(default=None, max_length=120)
    password: str


class PasswordInfo(BaseModel):
    username: str


class SetPassword(BaseModel):
    password: str


class Availability(BaseModel):
    available: bool


def _pruefe_passwort(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.",
        )


class ForgotPassword(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class ForgotResult(BaseModel):
    """Immer dieselbe Antwort - siehe unten."""

    message: str


# Hoechstens so viele Anfragen je Adresse und Stunde. Ohne Bremse koennte man
# jemanden mit Mails zuschuetten.
MAX_RESETS_PER_HOUR = 5

NEUTRALE_ANTWORT = (
    "Falls es zu dieser Adresse ein Konto gibt, ist die Nachricht unterwegs. "
    "Schau auch im Spam-Ordner nach."
)


@router.post("/forgot-password", response_model=ForgotResult)
async def forgot_password(payload: ForgotPassword, db: DbSession) -> ForgotResult:
    """Link zum Zuruecksetzen anfordern.

    Die Antwort ist **immer** dieselbe, ob es die Adresse gibt oder nicht.
    Sonst koennte hier jeder durchprobieren, wer ein Konto hat - und das ist
    eine Auskunft, die niemanden ausser dem Betreiber etwas angeht.
    """
    adresse = tokens.normalize_email(payload.email)
    user = db.scalar(select(User).where(User.email == adresse))

    if user is not None and user.is_active:
        seit = utcnow().replace(tzinfo=None) - timedelta(hours=1)
        versuche = (
            db.scalar(
                select(func.count(AuthToken.id)).where(
                    AuthToken.purpose == TokenPurpose.password_reset,
                    AuthToken.email == adresse,
                    AuthToken.created_at >= seit,
                )
            )
            or 0
        )
        if versuche >= MAX_RESETS_PER_HOUR:
            logger.warning("Too many reset requests for %s - ignored", adresse)
        else:
            await accounts.send_reset(db, load_settings(db), user)
            logger.info("Password reset requested for %r", user.username)
    else:
        logger.info("Password reset requested for unknown address")

    return ForgotResult(message=NEUTRALE_ANTWORT)


class PendingRequest(BaseModel):
    """Der Notausgang vor der gesperrten Anmeldung.

    Bewusst mit Benutzername und Passwort statt mit einer Sitzung: eine
    Sitzung gibt es hier ja gerade nicht, und ein Sonder-Token nur fuer diesen
    Fall waere eine zusaetzliche Angriffsflaeche fuer sehr wenig Nutzen.
    """

    username: str
    password: str


class ChangePendingEmail(PendingRequest):
    email: str = Field(min_length=3, max_length=255)


def _unbestaetigter_benutzer(db: DbSession, payload: PendingRequest) -> User:
    # Wie beim Anmelden: Benutzername oder Adresse.
    eingabe = payload.username.strip()
    user = db.scalar(
        select(User).where(
            (func.lower(User.username) == eingabe.lower())
            | (User.email == tokens.normalize_email(eingabe))
        )
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail=meldungen.meldung(
                "bad_credentials",
                "Benutzername oder Passwort ist falsch.",
            ),
        )
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail=meldungen.meldung(
                "account_disabled",
                "Dieses Konto ist deaktiviert.",
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
    return user


@router.post("/pending/resend", response_model=VerificationSent)
async def resend_pending(payload: PendingRequest, db: DbSession) -> VerificationSent:
    """Bestaetigungsmail erneut anfordern, ohne angemeldet zu sein."""
    user = _unbestaetigter_benutzer(db, payload)
    zustellung = await accounts.send_verification(db, load_settings(db), user)
    logger.info("Verification resent for %r", user.username)
    return VerificationSent(sent=zustellung.sent, error=zustellung.error)


@router.put("/pending/email", response_model=VerificationSent)
async def change_pending_email(payload: ChangePendingEmail, db: DbSession) -> VerificationSent:
    """Adresse korrigieren, solange sie noch nicht bestaetigt ist.

    Der haeufigste Grund fuer eine nie ankommende Mail ist ein Tippfehler.
    Ohne diesen Weg waere die Installation dann verloren.
    """
    user = _unbestaetigter_benutzer(db, payload)

    neue = tokens.normalize_email(payload.email)
    if not mail.valid_address(neue):
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "email_invalid",
                "Das ist keine gültige E-Mail-Adresse.",
            ),
        )
    if db.scalar(select(User.id).where(User.email == neue, User.id != user.id)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "email_taken_other_account",
                "Diese Adresse gehört bereits zu einem anderen Konto.",
            ),
        )

    user.email = neue
    db.commit()

    zustellung = await accounts.send_verification(db, load_settings(db), user)
    logger.info("Unverified address corrected for %r", user.username)
    return VerificationSent(sent=zustellung.sent, error=zustellung.error)


@router.get("/username-available", response_model=Availability)
def username_available(username: str, db: DbSession) -> Availability:
    """Schon waehrend der Eingabe zeigen, ob der Name noch frei ist.

    Bewusst ohne Anmeldung: wer eine Einladung einloest, ist noch niemand. Es
    verraet nur, ob ein Name vergeben ist - das sieht man beim Absenden ohnehin.
    """
    name = username.strip()
    if len(name) < 3:
        return Availability(available=False)
    vergeben = db.scalar(select(User.id).where(func.lower(User.username) == name.lower()))
    return Availability(available=vergeben is None)


@router.get("/invitation/{raw}", response_model=InvitationInfo)
def read_invitation(raw: str, request: Request, db: DbSession) -> InvitationInfo:
    bremse = _token_bremse(request)
    token = tokens.find(db, raw, TokenPurpose.invitation)
    if token is None:
        anmeldebremse.gescheitert(bremse)
        raise HTTPException(status_code=404, detail=ABGELAUFEN)
    anmeldebremse.geklappt(bremse)
    return InvitationInfo(email=token.email, role=token.invite_role or Role.user)


@router.post("/invitation/{raw}", status_code=status.HTTP_201_CREATED)
def accept_invitation(
    raw: str, payload: AcceptInvitation, request: Request, db: DbSession
) -> dict[str, str]:
    """Einladung einloesen: Konto anlegen, wie der Eingeladene es haben will."""
    bremse = _token_bremse(request)
    token = tokens.find(db, raw, TokenPurpose.invitation)
    if token is None:
        anmeldebremse.gescheitert(bremse)
        raise HTTPException(status_code=404, detail=ABGELAUFEN)
    anmeldebremse.geklappt(bremse)

    name = payload.username.strip()
    _pruefe_passwort(payload.password)
    if db.scalar(select(User.id).where(func.lower(User.username) == name.lower())) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "username_taken",
                "Dieser Benutzername ist bereits vergeben. Bitte wähle einen anderen.",
            ),
        )
    if db.scalar(select(User.id).where(User.email == token.email)) is not None:
        # In der Zwischenzeit hat der Administrator das Konto von Hand angelegt.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "account_exists_sign_in",
                "Zu dieser Adresse gibt es bereits ein Konto. Bitte melde dich einfach an.",
            ),
        )

    user = User(
        username=name,
        password_hash=hash_password(payload.password),
        email=token.email,
        # Wer den Link aus der Mail geoeffnet hat, hat die Adresse damit belegt.
        email_verified=True,
        role=token.invite_role or Role.user,
        display_name=(payload.display_name or "").strip() or name,
        # Neue Konten starten in der Standardsprache der Installation. Umstellen
        # kann sie jeder danach jederzeit oben rechts.
        language=load_settings(db).default_language,
        quota_movies_limit=token.invite_quota_movies,
        quota_series_limit=token.invite_quota_series,
        blocked_movie_profiles=token.invite_blocked_movie_profiles,
        blocked_series_profiles=token.invite_blocked_series_profiles,
    )
    db.add(user)
    tokens.consume(db, raw, TokenPurpose.invitation)
    db.commit()

    logger.info("Invitation for %s accepted as %r", token.email, name)
    return {"username": name}


@router.get("/password/{raw}", response_model=PasswordInfo)
def read_password_token(raw: str, request: Request, db: DbSession) -> PasswordInfo:
    bremse = _token_bremse(request)
    token = tokens.find(db, raw, TokenPurpose.password_reset)
    if token is None or token.user is None:
        anmeldebremse.gescheitert(bremse)
        raise HTTPException(status_code=404, detail=ABGELAUFEN)
    anmeldebremse.geklappt(bremse)
    return PasswordInfo(username=token.user.username)


@router.post("/password/{raw}", status_code=status.HTTP_204_NO_CONTENT)
def set_password(raw: str, payload: SetPassword, request: Request, db: DbSession) -> None:
    """Passwort setzen - erstes oder neues.

    Damit gilt auch die Adresse als bestaetigt: die Mail ist ja angekommen.
    Und alle bestehenden Sitzungen werden ungueltig, denn wer sein Passwort
    zuruecksetzt, will genau das.
    """
    bremse = _token_bremse(request)
    token = tokens.find(db, raw, TokenPurpose.password_reset)
    if token is None or token.user is None:
        anmeldebremse.gescheitert(bremse)
        raise HTTPException(status_code=404, detail=ABGELAUFEN)
    anmeldebremse.geklappt(bremse)

    _pruefe_passwort(payload.password)

    user = token.user
    user.password_hash = hash_password(payload.password)
    user.password_changed_at = utcnow()
    if token.email == user.email:
        user.email_verified = True
    tokens.consume(db, raw, TokenPurpose.password_reset)
    db.commit()

    logger.info("Password set for %r via mailed link", user.username)


@router.post("/verify/{raw}", status_code=status.HTTP_204_NO_CONTENT)
def verify_email(raw: str, request: Request, db: DbSession) -> None:
    """Adresse bestaetigen."""
    bremse = _token_bremse(request)
    token = tokens.find(db, raw, TokenPurpose.email_verification)
    if token is None or token.user is None:
        anmeldebremse.gescheitert(bremse)
        raise HTTPException(status_code=404, detail=ABGELAUFEN)
    anmeldebremse.geklappt(bremse)

    user = token.user
    if user.email != token.email:
        # Die Adresse wurde zwischenzeitlich wieder geaendert.
        raise HTTPException(
            status_code=409,
            detail=meldungen.meldung(
                "email_gone",
                "Diese Adresse ist nicht mehr hinterlegt.",
            ),
        )

    user.email_verified = True
    tokens.consume(db, raw, TokenPurpose.email_verification)
    db.commit()

    logger.info("Address %s confirmed for %r", token.email, user.username)
