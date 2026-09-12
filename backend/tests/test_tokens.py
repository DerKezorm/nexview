"""Einmal-Links: Ablauf, Einmaligkeit und Aufräumen."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import AuthToken, EinladungsServer, TokenPurpose, User, utcnow
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


def test_aufraeumen_laesst_einladungen_mit_hinweis_stehen(admin_client: TestClient) -> None:
    """Was die Einladungsliste noch zeigt, nimmt das Aufraeumen nicht weg.

    Eine eingeloeste Einladung bleibt dort stehen, solange etwas nicht
    uebernommen wurde oder ein Medienserver fehlt, bis der Administrator den
    Hinweis wegnimmt. Verschwaende sie nach dreissig Tagen still, waere mit ihr
    auch "Plex nachholen" weg.
    """
    vor_40_tagen = utcnow().replace(tzinfo=None) - timedelta(days=40)
    with SessionLocal() as session:
        _, entfallen = tokens.create(session, TokenPurpose.invitation, "entfallen@example.com")
        entfallen.invite_dropped = "auto_approve_movies"
        _, server_fehlt = tokens.create(session, TokenPurpose.invitation, "fehlt@example.com")
        server_fehlt.server.append(
            EinladungsServer(provider="plex", zustand=EinladungsServer.FEHLT)
        )
        _, erledigt = tokens.create(session, TokenPurpose.invitation, "erledigt@example.com")
        erledigt.server.append(
            EinladungsServer(provider="jellyfin", zustand=EinladungsServer.FERTIG)
        )
        for token in (entfallen, server_fehlt, erledigt):
            token.used_at = vor_40_tagen
        session.commit()

        assert tokens.purge_expired(session) == 1

        uebrig = {
            token.email
            for token in session.query(AuthToken).filter(
                AuthToken.purpose == TokenPurpose.invitation
            )
        }
        assert uebrig == {"entfallen@example.com", "fehlt@example.com"}
        # Mit der Einladung gehen ihre Server-Zeilen, und nur ihre.
        assert {ziel.provider for ziel in session.query(EinladungsServer)} == {"plex"}


async def test_die_hintergrundschleife_raeumt_auf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gleich nach dem Start einmal, und die Schleife endet mit der Anwendung."""
    aufrufe: list[int] = []
    monkeypatch.setattr(tokens, "purge_expired", lambda _session: aufrufe.append(1) or 0)
    stop = asyncio.Event()

    aufgabe = asyncio.create_task(tokens.run_forever(stop))
    for _ in range(100):
        if aufrufe:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(aufgabe, timeout=5)

    assert aufrufe == [1]


def test_der_start_haengt_das_aufraeumen_an() -> None:
    """Bis zum 12.09.2026 gab es ``purge_expired``, aber niemand rief es auf."""
    quelle = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert "tokens.run_forever(stop)" in quelle


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
