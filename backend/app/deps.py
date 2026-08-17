"""Wiederverwendbare Pruefungen: eingeloggt? Admin?"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import decode_token

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbSession,
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Nicht angemeldet.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    user_id = decode_token(credentials.credentials, "access")
    if user_id is None:
        raise unauthorized

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diese Aktion ist Administratoren vorbehalten.",
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def require_approver(user: CurrentUser) -> User:
    """Admin oder Entscheider - darf ueber fremde Anfragen bestimmen."""
    if not user.can_approve:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diese Aktion ist Administratoren und Entscheidern vorbehalten.",
        )
    return user


ApproverUser = Annotated[User, Depends(require_approver)]


def has_any_user(db: Session) -> bool:
    """Gibt es bereits mindestens einen Benutzer? (steuert die Erst-Einrichtung)"""
    return db.scalar(select(User.id).limit(1)) is not None
