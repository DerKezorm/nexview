"""Erst-Einrichtung: einmalig den ersten Administrator anlegen.

Dieser Router ist der einzige, der ohne Anmeldung schreiben darf - und auch
nur so lange, wie es ueberhaupt noch keinen Benutzer gibt. Danach antwortet
er dauerhaft mit 409.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ..deps import DbSession, has_any_user
from ..models import Role, User
from ..schemas import AnmeldeWeg, SetupAdminCreate, SetupStatus, TokenPair
from ..security import (
    access_token_expires_in,
    create_access_token,
    create_refresh_token,
    hash_password,
)
from ..services import mail, tokens
from ..services.mediaserver import PROVIDERS, verbundene_anbieter
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/status", response_model=SetupStatus)
def setup_status(db: DbSession) -> SetupStatus:
    # Die Anmeldeseite fragt das ohnehin beim Start ab. Sie muss vor dem
    # Anmelden wissen, ob es den Weg ueber den Media-Server gibt - und genau
    # dort ist noch niemand angemeldet, der die Einstellungen lesen duerfte.
    settings = load_settings(db)
    # Je verbundenem Anbieter ein Weg - mit der Art, wie er funktioniert.
    # Ein Anbieter, den diese Fassung nicht kennt, faellt still weg; er
    # koennte ohnehin nichts anbieten.
    wege = [
        AnmeldeWeg(
            provider=anbieter,
            label=PROVIDERS[anbieter].label,
            kind=PROVIDERS[anbieter].login_kind,
        )
        for anbieter in verbundene_anbieter(settings)
        if anbieter in PROVIDERS
    ]
    return SetupStatus(
        needs_setup=not has_any_user(db),
        mediaserver_login=bool(wege),
        mediaserver_provider=settings.mediaserver_provider or None,
        mediaserver_login_ways=wege,
    )


@router.post("/admin", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def create_first_admin(payload: SetupAdminCreate, db: DbSession) -> TokenPair:
    if has_any_user(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Die Einrichtung wurde bereits abgeschlossen.",
        )

    if not mail.valid_address(payload.email):
        raise HTTPException(status_code=422, detail="Das ist keine gültige E-Mail-Adresse.")

    admin = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        email=tokens.normalize_email(payload.email),
        # Auch der erste Administrator bestaetigt seine Adresse richtig.
        # Einen Mailserver gibt es hier noch nicht - die Bestaetigung holt der
        # Assistent am Ende nach, sobald einer eingerichtet ist. Sie einfach
        # als geprueft auszugeben waere eine Behauptung ins Blaue: ein
        # Tippfehler fiele erst auf, wenn er sich aussperrt.
        email_verified=False,
        role=Role.admin,
        display_name=payload.display_name or payload.username,
        language=payload.language,
        auto_approve=True,  # der Admin braucht keine Freigabe von sich selbst
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    return TokenPair(
        access_token=create_access_token(admin.id),
        refresh_token=create_refresh_token(admin.id),
        expires_in=access_token_expires_in(),
    )
