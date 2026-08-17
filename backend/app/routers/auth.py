"""Anmeldung, Token-Erneuerung und eigenes Profil."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import CurrentUser, DbSession
from ..models import User, utcnow
from ..services import accounts, avatars, mail, tokens
from ..services.settings_service import load_settings
from ..schemas import (
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    RefreshRequest,
    TokenPair,
    UserPublic,
    VerificationSent,
)
from ..security import (
    access_token_expires_in,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger("nexview.auth")

_INVALID_LOGIN = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Benutzername oder Passwort ist falsch.",
)

# Gegen einen echten Hash pruefen, wenn es den Benutzer gar nicht gibt - sonst
# koennte man an der Antwortzeit ablesen, welche Benutzernamen existieren.
_DUMMY_HASH = hash_password("nexview-dummy-password")


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, db: DbSession) -> TokenPair:
    # Benutzername *oder* E-Mail-Adresse, beides ohne Ruecksicht auf Gross- und
    # Kleinschreibung. Adressen liegen ohnehin klein geschrieben in der
    # Datenbank; beim Benutzernamen muss verglichen werden.
    eingabe = payload.username.strip()
    user = db.scalar(
        select(User).where(
            (func.lower(User.username) == eingabe.lower())
            | (User.email == tokens.normalize_email(eingabe))
        )
    )

    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        raise _INVALID_LOGIN

    if not verify_password(payload.password, user.password_hash):
        raise _INVALID_LOGIN

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dieses Konto ist deaktiviert. Bitte wende dich an den Administrator.",
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

    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        expires_in=access_token_expires_in(),
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: DbSession) -> TokenPair:
    user_id = decode_token(payload.refresh_token, "refresh")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sitzung abgelaufen. Bitte erneut anmelden.",
        )

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sitzung abgelaufen. Bitte erneut anmelden.",
        )

    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        expires_in=access_token_expires_in(),
    )


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
                detail="Sprache muss 'de' oder 'en' sein.",
            )
        user.language = payload.language

    for schalter in (
        "mail_download_complete",
        "mail_request_pending",
        "mail_request_decided",
        "mail_feedback",
    ):
        wert = getattr(payload, schalter)
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
async def change_my_email(payload: EmailChange, user: CurrentUser, db: DbSession) -> User:
    """Eigene Adresse aendern.

    Die neue Adresse gilt erst als bestaetigt, wenn der Link aus der Mail
    angeklickt wurde - sonst koennte man sich mit einer fremden Adresse
    eintragen. Die *alte* wird gewarnt: das ist die uebliche Absicherung, falls
    jemand anderes am Konto sitzt.
    """
    neue = tokens.normalize_email(payload.email)
    if not mail.valid_address(neue):
        raise HTTPException(status_code=422, detail="Das ist keine gültige E-Mail-Adresse.")
    if neue == (user.email or ""):
        return user

    if db.scalar(select(User.id).where(User.email == neue, User.id != user.id)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese Adresse gehört bereits zu einem anderen Konto.",
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
async def resend_verification(user: CurrentUser, db: DbSession) -> VerificationSent:
    """Bestaetigungsmail erneut anfordern."""
    if not user.email:
        raise HTTPException(status_code=409, detail="Es ist keine Adresse hinterlegt.")
    if user.email_verified:
        raise HTTPException(status_code=409, detail="Deine Adresse ist bereits bestätigt.")

    zustellung = await accounts.send_verification(db, load_settings(db), user)
    return VerificationSent(sent=zustellung.sent, error=zustellung.error)


@router.post("/me/avatar", response_model=UserPublic)
async def upload_avatar(user: CurrentUser, db: DbSession, file: UploadFile = File(...)) -> User:
    """Eigenes Profilbild hochladen."""
    try:
        user.avatar_path = avatars.save(await file.read(), user.avatar_path)
    except avatars.AvatarError as error:
        raise HTTPException(status_code=400, detail=error.message) from error

    db.commit()
    db.refresh(user)
    return user


@router.delete("/me/avatar", response_model=UserPublic)
def delete_avatar(user: CurrentUser, db: DbSession) -> User:
    avatars.remove(user.avatar_path)
    user.avatar_path = None
    db.commit()
    db.refresh(user)
    return user


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_own_password(payload: PasswordChange, user: CurrentUser, db: DbSession) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Das aktuelle Passwort ist falsch.",
        )
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = utcnow()
    db.commit()
