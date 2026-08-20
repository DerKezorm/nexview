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
    # Ist ein Media-Server verbunden? Nur dann zeigt die Anmeldeseite den
    # zusaetzlichen Knopf - ohne Verbindung soll davon nichts zu sehen sein.
    mediaserver_login: bool = False
    mediaserver_provider: str | None = None


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
    theme: str
    is_active: bool
    auto_approve: bool
    # Je Medienart gesetzt; None heisst "es gilt der alte gemeinsame Haken".
    auto_approve_movies: bool | None
    auto_approve_series: bool | None
    # Was tatsaechlich gilt - bei Administratoren immer true.
    effective_auto_approve: bool
    effective_auto_approve_movies: bool
    effective_auto_approve_series: bool
    can_approve: bool
    quota_movies_limit: int | None
    quota_series_limit: int | None
    quota_period: QuotaPeriod
    # Wann der Admin das Kontingent zuletzt von Hand zurueckgesetzt hat.
    quota_reset_at: datetime | None
    blocked_movie_profiles: list[int]
    blocked_series_profiles: list[int]
    # --- 4K -----------------------------------------------------------------
    can_request_uhd_movies: bool
    can_request_uhd_series: bool
    auto_approve_uhd: bool
    effective_auto_approve_uhd: bool
    blocked_movie_uhd_profiles: list[int]
    blocked_series_uhd_profiles: list[int]
    avatar_url: str | None
    created_at: datetime
    last_login_at: datetime | None

    # Benachrichtigungen per Mail - Standard ist ueberall "aus".
    mail_download_complete: bool
    mail_request_pending: bool
    mail_request_decided: bool
    mail_feedback: bool
    mail_ticket: bool
    mail_user_imported: bool

    # *Ob* ein Plex-Zugang hinterlegt ist - das Token selbst verlaesst den
    # Server nie. Ohne ihn laesst sich die Merkliste nicht lesen.
    watchlist_connected: bool

    # --- Verknuepfung mit dem Media-Server ---------------------------------
    # Die Kennung selbst wird bewusst nicht ausgeliefert; fuer die Oberflaeche
    # zaehlt nur, *ob* verknuepft ist und mit welchem Namen.
    mediaserver_provider: str | None
    mediaserver_username: str | None
    mediaserver_linked: bool
    # Wer sich nur ueber den Media-Server anmeldet, hat kein Passwort. Das
    # Profil braucht die Auskunft, um "Passwort festlegen" anzubieten - und um
    # das Trennen zu verhindern, das aussperren wuerde.
    has_password: bool

    # Vorbelegung der Filterleiste; NULL = nichts Eigenes eingestellt.
    discover_region: str | None

    # Altersbeschraenkung. Wird mit ausgegeben, damit der Betroffene in seinem
    # Profil sieht, dass eine Grenze gilt - sonst wirkte die Anwendung fuer ihn
    # einfach luecken- und grundlos leerer als fuer andere. NULL = keine.
    age: int | None
    rating_region: str | None
    hide_unrated: bool

    @field_validator(
        "blocked_movie_profiles",
        "blocked_series_profiles",
        "blocked_movie_uhd_profiles",
        "blocked_series_uhd_profiles",
        mode="before",
    )
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
    auto_approve_movies: bool | None = None
    auto_approve_series: bool | None = None
    quota_movies_limit: int | None = Field(default=None, ge=0)
    quota_series_limit: int | None = Field(default=None, ge=0)
    quota_period: QuotaPeriod | None = None
    # Leere Liste bedeutet: alle Qualitaetsprofile erlaubt.
    blocked_movie_profiles: list[int] | None = None
    blocked_series_profiles: list[int] | None = None
    # --- 4K -----------------------------------------------------------------
    can_request_uhd_movies: bool | None = None
    can_request_uhd_series: bool | None = None
    auto_approve_uhd: bool | None = None
    blocked_movie_uhd_profiles: list[int] | None = None
    blocked_series_uhd_profiles: list[int] | None = None

    # Altersbeschraenkung - ausschliesslich hier, nie in ``ProfileUpdate``.
    # -1 hebt die Beschraenkung wieder auf; ``None`` hiesse ja nur "nicht
    # mitgeschickt", damit liesse sie sich nie mehr entfernen.
    age: int | None = Field(default=None, ge=-1, le=21)
    rating_region: str | None = Field(default=None, max_length=2)
    hide_unrated: bool | None = None


class PasswordReset(BaseModel):
    """Admin setzt das Passwort eines Benutzers neu."""

    password: str

    _check_password = field_validator("password")(_validate_password)


class ProfileUpdate(BaseModel):
    """Was ein Benutzer an sich selbst aendern darf."""

    display_name: str | None = None
    language: str | None = None
    theme: str | None = None

    # Benachrichtigungen per Mail - jede einzeln.
    mail_download_complete: bool | None = None
    mail_request_pending: bool | None = None
    mail_request_decided: bool | None = None
    mail_feedback: bool | None = None
    mail_ticket: bool | None = None
    mail_user_imported: bool | None = None

    # Vorbelegung der Filterleiste. Der leere String bedeutet "nichts
    # Eigenes" - anders liesse sich eine einmal gesetzte Wahl nie wieder
    # aufheben, weil ``None`` ja schon "Feld nicht mitgeschickt" heisst.
    discover_region: str | None = Field(default=None, max_length=2)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str

    _check_password = field_validator("new_password")(_validate_password)
