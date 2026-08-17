"""Einmal-Links fuer Einladung, Adressbestaetigung und Passwort-Reset.

Grundregeln, die hier an einer Stelle durchgesetzt werden:

* Der Link selbst steht **nie** in der Datenbank - nur seine Pruefsumme. Wer
  die Datenbank kopiert, kann damit kein Konto uebernehmen.
* Jeder Link gilt genau einmal und laeuft ab.
* Ein neuer Link derselben Art macht die aelteren desselben Empfaengers
  ungueltig - sonst blieben alte Mails beliebig lange verwendbar.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuthToken, QuotaPeriod, Role, TokenPurpose, User, utcnow

# Wie lange die jeweilige Art gilt. Kurz genug, dass ein abgefangener Link
# selten noch nuetzt - lang genug, dass niemand in Zeitnot geraet.
LIFETIME = {
    TokenPurpose.invitation: timedelta(days=7),
    TokenPurpose.email_verification: timedelta(hours=24),
    TokenPurpose.password_reset: timedelta(hours=1),
}

# 32 Byte Zufall ergeben rund 43 Zeichen - nicht zu erraten.
TOKEN_BYTES = 32


def normalize_email(address: str) -> str:
    """Vergleichsform einer Adresse: ohne Leerzeichen, klein geschrieben."""
    return address.strip().lower()


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def invalidate(db: Session, purpose: TokenPurpose, email: str) -> None:
    """Offene Links derselben Art fuer diese Adresse entwerten."""
    jetzt = utcnow().replace(tzinfo=None)
    for token in db.scalars(
        select(AuthToken).where(
            AuthToken.purpose == purpose,
            AuthToken.email == normalize_email(email),
            AuthToken.used_at.is_(None),
        )
    ):
        token.used_at = jetzt


def create(
    db: Session,
    purpose: TokenPurpose,
    email: str,
    *,
    user: User | None = None,
    created_by: int | None = None,
    invite_role: Role | None = None,
    invite_quota_movies: int | None = None,
    invite_quota_series: int | None = None,
    invite_quota_period: QuotaPeriod | None = None,
    invite_blocked_movie_profiles: str = "",
    invite_blocked_series_profiles: str = "",
    lifetime_days: int | None = None,
) -> tuple[str, AuthToken]:
    """Neuen Einmal-Link anlegen. Gibt den Klartext zurueck - nur dieses Mal.

    ``lifetime_days`` verlaengert die uebliche Gueltigkeit. Gebraucht wird das
    fuer die Willkommensnachricht: dort ist der Link zwar technisch ein
    Passwort-Reset, aber eine Stunde waere fuer jemanden, der gerade erst
    angelegt wurde, viel zu knapp.
    """
    adresse = normalize_email(email)
    invalidate(db, purpose, adresse)

    gueltigkeit = timedelta(days=lifetime_days) if lifetime_days else LIFETIME[purpose]
    roh = secrets.token_urlsafe(TOKEN_BYTES)
    token = AuthToken(
        purpose=purpose,
        token_hash=_hash(roh),
        user_id=user.id if user else None,
        email=adresse,
        expires_at=utcnow().replace(tzinfo=None) + gueltigkeit,
        created_by=created_by,
        invite_role=invite_role,
        invite_quota_movies=invite_quota_movies,
        invite_quota_series=invite_quota_series,
        invite_quota_period=invite_quota_period,
        invite_blocked_movie_profiles=invite_blocked_movie_profiles,
        invite_blocked_series_profiles=invite_blocked_series_profiles,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return roh, token


def find(db: Session, raw: str, purpose: TokenPurpose) -> AuthToken | None:
    """Zum Link gehoerenden Eintrag suchen - ohne ihn zu verbrauchen.

    Fuer Seiten, die erst einmal nur zeigen wollen, worum es geht (etwa das
    Einladungsformular mit der Adresse darin).
    """
    token = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == _hash(raw), AuthToken.purpose == purpose
        )
    )
    return token if token is not None and token.open else None


def consume(db: Session, raw: str, purpose: TokenPurpose) -> AuthToken | None:
    """Link einloesen. Danach ist er verbraucht.

    Der Aufrufer muss anschliessend committen - so bleibt "verbraucht" und die
    eigentliche Aenderung in derselben Transaktion.
    """
    token = find(db, raw, purpose)
    if token is None:
        return None
    token.used_at = utcnow().replace(tzinfo=None)
    return token


def purge_expired(db: Session) -> int:
    """Abgelaufene und verbrauchte Links aufraeumen.

    Sie haben keinen Wert mehr; ohne das waechst die Tabelle mit jeder
    Einladung und jedem vergessenen Passwort weiter.
    """
    grenze = utcnow().replace(tzinfo=None) - timedelta(days=30)
    alte = list(
        db.scalars(
            select(AuthToken).where(
                (AuthToken.expires_at < grenze) | (AuthToken.used_at < grenze)
            )
        )
    )
    for token in alte:
        db.delete(token)
    if alte:
        db.commit()
    return len(alte)
