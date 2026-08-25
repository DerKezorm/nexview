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
from .services import logs
from . import meldungen

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbSession,
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=meldungen.meldung("not_signed_in", "Nicht angemeldet."),
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

    # Ab hier steht in jeder Protokollzeile dieser Anfrage, wer sie gestellt hat.
    logs.set_actor(user.username)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung(
                "admins_only",
                "Diese Aktion ist Administratoren vorbehalten.",
            ),
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def require_approver(user: CurrentUser) -> User:
    """Admin oder Entscheider - darf ueber fremde Anfragen bestimmen."""
    if not user.can_approve:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung(
                "approvers_only",
                "Diese Aktion ist Administratoren und Entscheidern vorbehalten.",
            ),
        )
    return user


ApproverUser = Annotated[User, Depends(require_approver)]


def require_adult(user: CurrentUser) -> User:
    """Alles ausser Kinderkonten.

    ⚠️ **Die Sperre fuer Kinder ist eine Erlaubnisliste, keine Verbotsliste.**
    Sie haengt in ``main.py`` an jedem Router, der *nicht* ausdruecklich fuer
    Kinder gedacht ist. Umgekehrt - "an jedem Endpunkt, der Kindern schadet" -
    waere es eine Verbotsliste, und die erste vergessene Zeile hiesse nicht ein
    falsches Abzeichen, sondern ein Kind in einer Erwachsenenfunktion.

    ``test_child_permissions.py`` laeuft ueber die ganze Routentabelle und
    schlaegt fehl, sobald ein Pfad weder hier haengt noch in der
    Kinder-Erlaubnisliste steht. Ein kuenftiger neuer Router kann das also
    nicht stillschweigend unterlaufen.
    """
    if user.is_child:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung("not_for_children", "Das ist nichts für Kinderkonten."),
        )
    return user


AdultUser = Annotated[User, Depends(require_adult)]


def require_child(user: CurrentUser) -> User:
    """Nur Kinderkonten - fuer die Kinderansicht."""
    if not user.is_child:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung(
                "children_only",
                "Diese Ansicht gibt es nur für Kinderkonten.",
            ),
        )
    return user


ChildUser = Annotated[User, Depends(require_child)]


def has_any_user(db: Session) -> bool:
    """Gibt es bereits mindestens einen Benutzer? (steuert die Erst-Einrichtung)"""
    return db.scalar(select(User.id).limit(1)) is not None
