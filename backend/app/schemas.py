"""Datenformate der API (Pydantic)."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import QuotaPeriod, Role

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")

# Bewusst niedrig gehalten, damit auch kurze Konten wie "user" moeglich sind.
# Wer Nexview von aussen erreichbar macht, sollte trotzdem lange Passwoerter
# vergeben - siehe README.
MIN_PASSWORD_LENGTH = 4


def _validate_username(value: str) -> str:
    value = value.strip()
    if not USERNAME_PATTERN.match(value):
        raise ValueError(
            "Benutzername: 3-32 Zeichen, erlaubt sind Buchstaben, Ziffern, Punkt, Bindestrich "
            "und Unterstrich."
        )
    return value


def _validate_password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.")
    return value


# --- Erst-Einrichtung -------------------------------------------------------


class SetupStatus(BaseModel):
    needs_setup: bool
    # Kommt vom Server, weil ``/api/config`` eine Anmeldung braucht - der
    # Assistent laeuft aber davor. Sonst koennten Hinweistext und Pruefung
    # auseinanderlaufen, und genau das ist hier schon einmal passiert.
    min_password_length: int = MIN_PASSWORD_LENGTH


class SetupAdminCreate(BaseModel):
    username: str
    password: str
    email: str = Field(min_length=3, max_length=255)
    display_name: str | None = None
    language: str = "de"

    _check_username = field_validator("username")(_validate_username)
    _check_password = field_validator("password")(_validate_password)


# --- Anmeldung -------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


# --- Benutzer --------------------------------------------------------------


class UserPublic(BaseModel):
    """Eigenes Profil bzw. Benutzer in Admin-Listen."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Role
    display_name: str | None
    # NULL nur bei Konten aus der Zeit vor der Mailpflicht.
    email: str | None
    email_verified: bool
    language: str
    is_active: bool
    auto_approve: bool
    # Was tatsaechlich gilt - bei Administratoren immer true.
    effective_auto_approve: bool
    can_approve: bool
    quota_movies_limit: int | None
    quota_series_limit: int | None
    quota_period: QuotaPeriod
    # Wann der Admin das Kontingent zuletzt von Hand zurueckgesetzt hat.
    quota_reset_at: datetime | None
    blocked_movie_profiles: list[int]
    blocked_series_profiles: list[int]
    avatar_url: str | None
    created_at: datetime
    last_login_at: datetime | None

    @field_validator("blocked_movie_profiles", "blocked_series_profiles", mode="before")
    @classmethod
    def _split_profiles(cls, value: object) -> object:
        """In der Datenbank steht eine Komma-Liste, nach aussen eine echte Liste."""
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip().isdigit()]
        return value


class UserWithUsage(UserPublic):
    """Benutzer in der Admin-Liste - mit dem Verbrauch im laufenden Zeitraum.

    Ohne diese Zahlen waere der Knopf "Kontingent zurücksetzen" ein Blindflug:
    der Admin saehe nicht, ob ueberhaupt etwas zurueckzusetzen ist.
    """

    quota_movies_used: int
    quota_series_used: int


class VerificationSent(BaseModel):
    sent: bool
    error: str | None = None


class InvitationCreate(BaseModel):
    """Einladung: der Eingeladene waehlt Benutzername und Namen selbst."""

    email: str = Field(min_length=3, max_length=255)
    role: Role = Role.user
    quota_movies_limit: int | None = Field(default=None, ge=0)
    quota_series_limit: int | None = Field(default=None, ge=0)
    quota_period: QuotaPeriod = QuotaPeriod.week


class InvitationPublic(BaseModel):
    id: int
    email: str
    role: Role
    created_at: datetime
    expires_at: datetime


class InvitationCreated(InvitationPublic):
    mail_sent: bool
    mail_error: str | None = None
    manual_link: str | None = None




class UserUpdate(BaseModel):
    role: Role | None = None
    display_name: str | None = None
    language: str | None = None
    is_active: bool | None = None
    auto_approve: bool | None = None
    quota_movies_limit: int | None = Field(default=None, ge=0)
    quota_series_limit: int | None = Field(default=None, ge=0)
    quota_period: QuotaPeriod | None = None
    # Leere Liste bedeutet: alle Qualitaetsprofile erlaubt.
    blocked_movie_profiles: list[int] | None = None
    blocked_series_profiles: list[int] | None = None


class PasswordReset(BaseModel):
    """Admin setzt das Passwort eines Benutzers neu."""

    password: str

    _check_password = field_validator("password")(_validate_password)


class ProfileUpdate(BaseModel):
    """Was ein Benutzer an sich selbst aendern darf."""

    display_name: str | None = None
    language: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str

    _check_password = field_validator("new_password")(_validate_password)
