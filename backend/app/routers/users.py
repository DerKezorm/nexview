"""Benutzerverwaltung - ausschliesslich fuer Administratoren."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from ..deps import AdminUser, DbSession
from ..models import AuthToken, Role, TokenPurpose, User, utcnow
from ..schemas import (
    InvitationCreate,
    InvitationCreated,
    InvitationPublic,
    PasswordReset,
    UserPublic,
    UserUpdate,
    UserWithUsage,
)
from ..security import hash_password
from ..services import accounts, avatars, mail, quota, tokens
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/users", tags=["users"])

logger = logging.getLogger("nexview.users")


def _username_taken(db: DbSession, username: str) -> bool:
    return (
        db.scalar(select(User.id).where(func.lower(User.username) == username.lower())) is not None
    )


def _get_user_or_404(db: DbSession, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Benutzer nicht gefunden."
        )
    return user


def _count_active_admins(db: DbSession) -> int:
    return (
        db.scalar(
            select(func.count(User.id)).where(User.role == Role.admin, User.is_active.is_(True))
        )
        or 0
    )


@router.get("/avatar/{name}", include_in_schema=False)
def read_avatar(name: str) -> Response:
    """Profilbild ausliefern.

    Bewusst ohne Anmeldung: ``<img>``-Elemente schicken keinen Token mit.
    Die Dateinamen sind zufaellig, also nicht erratbar. ``nosniff`` verhindert,
    dass der Browser den Inhalt als etwas anderes als ein Bild deutet.
    """
    try:
        content, media_type = avatars.read(name)
    except avatars.AvatarError as error:
        raise HTTPException(status_code=404, detail=error.message) from error

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _mit_verbrauch(db: DbSession, user: User) -> UserWithUsage:
    stand = quota.overview(db, user)
    return UserWithUsage(
        **UserPublic.model_validate(user).model_dump(),
        quota_movies_used=stand["movie"].used,
        quota_series_used=stand["tv"].used,
    )


@router.get("", response_model=list[UserWithUsage])
def list_users(admin: AdminUser, db: DbSession) -> list[UserWithUsage]:
    return [
        _mit_verbrauch(db, user)
        for user in db.scalars(select(User).order_by(User.created_at))
    ]


@router.post("/{user_id}/quota/reset", response_model=UserWithUsage)
def reset_quota(user_id: int, admin: AdminUser, db: DbSession) -> UserWithUsage:
    """Den Verbrauch im laufenden Zeitraum auf null setzen.

    Die Anfragen bleiben erhalten - es wird lediglich ab jetzt neu gezaehlt.
    Beim naechsten Zeitraumwechsel greift wieder der Kalender.
    """
    user = _get_user_or_404(db, user_id)
    user.quota_reset_at = utcnow()
    db.commit()
    db.refresh(user)

    logger.info("Quota reset for user %r by %r", user.username, admin.username)
    return _mit_verbrauch(db, user)


def _email_taken(db: DbSession, email: str, ausser: int | None = None) -> bool:
    """Gehoert die Adresse schon zu einem Konto oder zu einer offenen Einladung?"""
    adresse = tokens.normalize_email(email)
    query = select(User.id).where(User.email == adresse)
    if ausser is not None:
        query = query.where(User.id != ausser)
    if db.scalar(query) is not None:
        return True

    offen = db.scalars(
        select(AuthToken).where(
            AuthToken.purpose == TokenPurpose.invitation,
            AuthToken.email == adresse,
            AuthToken.used_at.is_(None),
        )
    )
    return any(token.open for token in offen)


# Konten entstehen ausschliesslich ueber eine Einladung. Ein Weg, auf dem der
# Administrator Benutzername und Passwort vorgibt, gibt es bewusst nicht mehr:
# er muesste das Passwort weitergeben, und damit kennen es zwei. Die einzige
# Ausnahme ist der erste Administrator - den legt der Einrichtungsassistent an,
# bevor es ueberhaupt einen Mailserver gibt.


@router.get("/invitations", response_model=list[InvitationPublic])
def list_invitations(admin: AdminUser, db: DbSession) -> list[InvitationPublic]:
    """Offene Einladungen - verbrauchte und abgelaufene interessieren nicht mehr."""
    offen = db.scalars(
        select(AuthToken)
        .where(
            AuthToken.purpose == TokenPurpose.invitation,
            AuthToken.used_at.is_(None),
        )
        .order_by(AuthToken.created_at.desc())
    )
    return [
        InvitationPublic(
            id=token.id,
            email=token.email,
            role=token.invite_role or Role.user,
            created_at=token.created_at,
            expires_at=token.expires_at,
        )
        for token in offen
        if token.open
    ]


@router.post("/invitations", response_model=InvitationCreated, status_code=201)
async def invite(payload: InvitationCreate, admin: AdminUser, db: DbSession) -> InvitationCreated:
    """Jemanden einladen. Das Konto entsteht erst, wenn er den Link einloest.

    Voraussetzung sind beide Bausteine: ohne oeffentliche Adresse enthaelt die
    Mail einen Link ins Leere, ohne Mailserver kommt sie gar nicht erst an.
    Eine Einladung anzulegen, die niemand einloesen kann, hilft niemandem -
    deshalb wird hier abgebrochen statt es zu versuchen.
    """
    settings = load_settings(db)
    fehlt = [
        bezeichnung
        for bezeichnung, vorhanden in (
            ("die öffentliche Adresse", bool(settings.public_url)),
            ("ein Mailserver", settings.mail_configured),
        )
        if not vorhanden
    ]
    if fehlt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Zum Einladen fehlt noch {' und '.join(fehlt)}. "
                "Nachzutragen unter Einstellungen → E-Mail."
            ),
        )

    if not mail.valid_address(payload.email):
        raise HTTPException(status_code=422, detail="Das ist keine gültige E-Mail-Adresse.")
    if _email_taken(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese Adresse gehört bereits zu einem Konto oder zu einer offenen Einladung.",
        )

    roh, token = tokens.create(
        db,
        TokenPurpose.invitation,
        payload.email,
        created_by=admin.id,
        invite_role=payload.role,
        invite_quota_movies=payload.quota_movies_limit,
        invite_quota_series=payload.quota_series_limit,
        invite_quota_period=payload.quota_period,
    )

    # Bewusst die Standardsprache der Installation, nicht die des einladenden
    # Admins: der Eingeladene hat noch kein Konto und damit keine eigene
    # Einstellung. Die Sprache des Admins waere reiner Zufall.
    zustellung = await accounts.send_invitation(settings, token, roh, settings.default_language)
    logger.info(
        "Invitation for %s created by %r (mail %s)",
        token.email,
        admin.username,
        "sent" if zustellung.sent else "NOT sent",
    )
    return InvitationCreated(
        id=token.id,
        email=token.email,
        role=payload.role,
        created_at=token.created_at,
        expires_at=token.expires_at,
        mail_sent=zustellung.sent,
        mail_error=zustellung.error,
        manual_link=None if zustellung.sent else zustellung.link,
    )


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_invitation(invitation_id: int, admin: AdminUser, db: DbSession) -> None:
    """Einladung zurueckziehen - der Link funktioniert danach nicht mehr."""
    token = db.get(AuthToken, invitation_id)
    if token is None or token.purpose != TokenPurpose.invitation:
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden.")
    db.delete(token)
    db.commit()
    logger.info("Invitation for %s withdrawn by %r", token.email, admin.username)


@router.patch("/{user_id}", response_model=UserPublic)
def update_user(user_id: int, payload: UserUpdate, admin: AdminUser, db: DbSession) -> User:
    user = _get_user_or_404(db, user_id)
    data = payload.model_dump(exclude_unset=True)

    # Sich selbst nicht aussperren, und nie den letzten Admin entfernen.
    losing_admin = (data.get("role") == Role.user) or (data.get("is_active") is False)
    if user.role == Role.admin and losing_admin and _count_active_admins(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Der letzte aktive Administrator kann nicht herabgestuft oder deaktiviert werden.",
        )
    if user.id == admin.id and losing_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Du kannst dir nicht selbst die Administratorrechte entziehen.",
        )

    # Wer selbst freigeben darf, gibt sich nicht erst selbst frei - der Haken
    # hat fuer Administratoren und Entscheider keine Wirkung und laesst sich
    # deshalb auch nicht setzen.
    kuenftige_rolle = data.get("role", user.role)
    if kuenftige_rolle in (Role.admin, Role.approver):
        data.pop("auto_approve", None)

    # Die Altersbeschraenkung aufheben heisst NULL schreiben. ``None`` kann das
    # nicht ausdruecken - das bedeutet in ``exclude_unset`` bereits "nicht
    # mitgeschickt". Deshalb steht -1 fuer "nicht mehr beschraenkt".
    if data.get("age") == -1:
        data["age"] = None

    for field, value in data.items():
        # Profil-Sperren liegen als Komma-Liste in der Datenbank.
        if field in ("blocked_movie_profiles", "blocked_series_profiles"):
            value = ",".join(str(int(entry)) for entry in value or [])
        # Der leere String heisst "kein eigenes Land", dann gilt die Vorgabe.
        if field == "rating_region":
            value = (value or "").strip().upper() or None
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(user_id: int, payload: PasswordReset, admin: AdminUser, db: DbSession) -> None:
    user = _get_user_or_404(db, user_id)
    user.password_hash = hash_password(payload.password)
    user.password_changed_at = utcnow()
    db.commit()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, admin: AdminUser, db: DbSession) -> None:
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Du kannst dein eigenes Konto nicht loeschen.",
        )
    if user.role == Role.admin and _count_active_admins(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Der letzte aktive Administrator kann nicht geloescht werden.",
        )

    # Sonst bliebe das Profilbild als verwaiste Datei liegen.
    avatars.remove(user.avatar_path)
    db.delete(user)
    db.commit()
