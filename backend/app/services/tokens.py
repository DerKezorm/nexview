"""Einmal-Links fuer Einladung, Adressbestaetigung und Passwort-Reset.

Grundregeln, die hier an einer Stelle durchgesetzt werden:

* Der Link selbst steht **nie** in der Datenbank - nur seine Pruefsumme. Wer
  die Datenbank kopiert, kann damit kein Konto uebernehmen.
* Jeder Link gilt genau einmal und laeuft ab.
* Ein neuer Link derselben Art macht die aelteren desselben Empfaengers
  ungueltig - sonst blieben alte Mails beliebig lange verwendbar.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuthToken, EinladungsServer, Role, TokenPurpose, User, utcnow

# Wie lange die jeweilige Art gilt. Kurz genug, dass ein abgefangener Link
# selten noch nuetzt - lang genug, dass niemand in Zeitnot geraet.
LIFETIME = {
    TokenPurpose.invitation: timedelta(days=7),
    TokenPurpose.email_verification: timedelta(hours=24),
    TokenPurpose.password_reset: timedelta(hours=1),
    # Ein angefangener Anmeldevorgang. Plex laesst seine PIN nach etwa einer
    # Viertelstunde verfallen; laenger festzuhalten hiesse nur, abgebrochene
    # Versuche unnoetig lange aufzubewahren.
    TokenPurpose.mediaserver_login: timedelta(minutes=15),
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
    invite_storage_limit_gb: int | None = None,
    invite_blocked_movie_profiles: str = "",
    invite_blocked_series_profiles: str = "",
    invite_rechte: dict[str, bool] | None = None,
    invite_hausordnung: bool = False,
    lifetime_days: int | None = None,
    mediaserver_ref: str | None = None,
    invalidate_previous: bool = True,
) -> tuple[str, AuthToken]:
    """Neuen Einmal-Link anlegen. Gibt den Klartext zurueck - nur dieses Mal.

    ``lifetime_days`` verlaengert die uebliche Gueltigkeit. Gebraucht wird das
    fuer die Willkommensnachricht: dort ist der Link zwar technisch ein
    Passwort-Reset, aber eine Stunde waere fuer jemanden, der gerade erst
    angelegt wurde, viel zu knapp.

    ``invalidate_previous=False`` laesst aeltere Vorgaenge stehen. Das ist bei
    der Anmeldung ueber den Media-Server noetig: dort gibt es noch keine
    Adresse, ueber die sich Vorgaenge unterscheiden liessen - zwei Personen,
    die sich gleichzeitig anmelden, wuerden einander sonst gegenseitig
    hinauswerfen.

    ``invite_rechte`` sind die Schalter aus ``kontorechte.SCHALTER``, so wie
    ``Bewertung.werte_fuers_konto`` sie liefert. Jeder Name landet in der
    Spalte ``invite_<name>``; ein unbekannter Name scheitert laut, statt still
    nichts zu setzen.
    """
    adresse = normalize_email(email)
    if invalidate_previous:
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
        invite_storage_limit_gb=invite_storage_limit_gb,
        invite_blocked_movie_profiles=invite_blocked_movie_profiles,
        invite_blocked_series_profiles=invite_blocked_series_profiles,
        invite_hausordnung=invite_hausordnung,
        **{f"invite_{name}": bool(wert) for name, wert in (invite_rechte or {}).items()},
        mediaserver_ref=mediaserver_ref,
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

    ⚠️ **Ausser einer Einladung, die noch mit Hinweis in der Liste steht.** Was
    nicht uebernommen wurde oder auf einem Medienserver fehlt, bleibt dort
    sichtbar, bis der Administrator den Hinweis wegnimmt. Mit der Einladung
    verschwaende sonst auch das Nachholen einer Freigabe.
    """
    grenze = utcnow().replace(tzinfo=None) - timedelta(days=30)
    kandidaten = list(
        db.scalars(
            select(AuthToken).where(
                (AuthToken.expires_at < grenze) | (AuthToken.used_at < grenze)
            )
        )
    )
    alte = [token for token in kandidaten if not _mit_hinweis(token)]
    for token in alte:
        db.delete(token)
    if alte:
        db.commit()
    return len(alte)


def _mit_hinweis(token: AuthToken) -> bool:
    """Steht die Einladung noch mit Hinweis in der Liste (``routers/users.list_invitations``)?"""
    return token.purpose == TokenPurpose.invitation and (
        bool(token.invite_dropped)
        or any(ziel.zustand == EinladungsServer.FEHLT for ziel in token.server)
    )


#: Einmal am Tag reicht: Aufgeraeumt wird, was seit dreissig Tagen tot ist.
AUFRAEUMEN_SEKUNDEN = 24 * 60 * 60


def _aufraeumen() -> None:
    from ..db import SessionLocal  # wie in ``logs``: erst beim Aufruf geladen

    with SessionLocal() as db:
        weg = purge_expired(db)
    if weg:
        logging.getLogger("nexview.tokens").info("Removed %d expired or used link(s)", weg)


async def run_forever(stop: asyncio.Event) -> None:
    """Gleich nach dem Start einmal aufraeumen, danach taeglich.

    Bis zum 12.09.2026 gab es ``purge_expired``, aber keine Stelle rief es auf.
    """
    while not stop.is_set():
        try:
            await asyncio.to_thread(_aufraeumen)
        except Exception:  # noqa: BLE001 - die Schleife darf nie sterben
            logging.getLogger("nexview.tokens").exception("Removing expired links failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=AUFRAEUMEN_SEKUNDEN)
        except TimeoutError:
            continue
