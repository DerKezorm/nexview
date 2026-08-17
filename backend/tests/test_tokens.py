"""Einmal-Links: Ablauf, Einmaligkeit und Aufräumen."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import AuthToken, TokenPurpose, User, utcnow
from app.services import tokens


def _admin(session) -> User:  # noqa: ANN001
    return session.query(User).filter(User.username == "admin").one()


def test_link_steht_nicht_in_der_datenbank(admin_client: TestClient) -> None:
    """Nur die Prüfsumme wird gespeichert - ein Datenbankleck reicht nicht."""
    with SessionLocal() as session:
        roh, token = tokens.create(
            session, TokenPurpose.password_reset, "Kim@Beispiel.DE", user=_admin(session)
        )
        assert roh not in token.token_hash
        assert len(token.token_hash) == 64
        # Adressen werden klein geschrieben abgelegt.
        assert token.email == "kim@beispiel.de"


def test_link_gilt_genau_einmal(admin_client: TestClient) -> None:
    with SessionLocal() as session:
        roh, _ = tokens.create(
            session, TokenPurpose.password_reset, "kim@beispiel.de", user=_admin(session)
        )

        assert tokens.consume(session, roh, TokenPurpose.password_reset) is not None
        session.commit()
        assert tokens.consume(session, roh, TokenPurpose.password_reset) is None


def test_abgelaufener_link_zaehlt_nicht(admin_client: TestClient) -> None:
    with SessionLocal() as session:
        roh, token = tokens.create(
            session, TokenPurpose.password_reset, "kim@beispiel.de", user=_admin(session)
        )
        token.expires_at = utcnow().replace(tzinfo=None) - timedelta(minutes=1)
        session.commit()

        assert tokens.find(session, roh, TokenPurpose.password_reset) is None
        assert tokens.consume(session, roh, TokenPurpose.password_reset) is None


def test_falscher_zweck_passt_nicht(admin_client: TestClient) -> None:
    """Ein Bestätigungslink darf kein Passwort zurücksetzen."""
    with SessionLocal() as session:
        roh, _ = tokens.create(
            session, TokenPurpose.email_verification, "kim@beispiel.de", user=_admin(session)
        )
        assert tokens.find(session, roh, TokenPurpose.password_reset) is None
        assert tokens.find(session, roh, TokenPurpose.email_verification) is not None


def test_neuer_link_entwertet_den_alten(admin_client: TestClient) -> None:
    with SessionLocal() as session:
        alt, _ = tokens.create(
            session, TokenPurpose.password_reset, "kim@beispiel.de", user=_admin(session)
        )
        neu, _ = tokens.create(
            session, TokenPurpose.password_reset, "kim@beispiel.de", user=_admin(session)
        )

        assert tokens.find(session, alt, TokenPurpose.password_reset) is None
        assert tokens.find(session, neu, TokenPurpose.password_reset) is not None


def test_erfundener_link_findet_nichts(admin_client: TestClient) -> None:
    with SessionLocal() as session:
        assert tokens.find(session, "ausgedacht", TokenPurpose.password_reset) is None


def test_einladung_merkt_sich_die_vorgaben(admin_client: TestClient) -> None:
    with SessionLocal() as session:
        roh, _ = tokens.create(
            session,
            TokenPurpose.invitation,
            "neu@beispiel.de",
            invite_role="approver",
            invite_quota_movies=3,
            invite_blocked_movie_profiles="4,6",
        )
        token = tokens.find(session, roh, TokenPurpose.invitation)
        assert token is not None
        assert token.invite_role.value == "approver"
        assert token.invite_quota_movies == 3
        assert token.invite_blocked_movie_profiles == "4,6"
        # Ohne Konto - der Eingeladene legt es ja erst an.
        assert token.user_id is None


def test_aufraeumen_loescht_nur_altes(admin_client: TestClient) -> None:
    with SessionLocal() as session:
        frisch, _ = tokens.create(
            session, TokenPurpose.password_reset, "frisch@beispiel.de", user=_admin(session)
        )
        _, alt = tokens.create(
            session, TokenPurpose.password_reset, "alt@beispiel.de", user=_admin(session)
        )
        alt.expires_at = utcnow().replace(tzinfo=None) - timedelta(days=40)
        session.commit()

        assert tokens.purge_expired(session) == 1
        # Der alte Eintrag ist weg, der frische unberührt.
        assert (
            session.query(AuthToken).filter(AuthToken.email == "alt@beispiel.de").count() == 0
        )
        assert tokens.find(session, frisch, TokenPurpose.password_reset) is not None


def test_adressen_werden_vereinheitlicht() -> None:
    assert tokens.normalize_email("  Kim@Beispiel.DE ") == "kim@beispiel.de"


# --- Öffentliche Adresse ----------------------------------------------------


def test_oeffentliche_adresse_speichern(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json={"public_url": "https://nexview.beispiel.de/"})
    # Der Schrägstrich am Ende wird abgeschnitten, damit Links nicht doppeln.
    assert admin_client.get("/api/settings").json()["public_url"] == "https://nexview.beispiel.de"


def test_adresse_braucht_ein_schema(admin_client: TestClient) -> None:
    antwort = admin_client.put("/api/settings", json={"public_url": "nexview.beispiel.de"})
    assert antwort.status_code == 422


def test_link_wird_richtig_zusammengesetzt(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json={"public_url": "https://nexview.beispiel.de"})
    from app.services.settings_service import load_settings

    with SessionLocal() as session:
        settings = load_settings(session)
        assert settings.link("/einladung/abc") == "https://nexview.beispiel.de/einladung/abc"
        assert settings.link("einladung/abc") == "https://nexview.beispiel.de/einladung/abc"
