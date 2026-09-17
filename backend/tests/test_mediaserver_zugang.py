"""Gilt der Zugang zum Medienserver noch - und wie erneuert man ihn?

Anlass (17.09.2026): Ein Emby-Server lehnte jede Anfrage mit 401 ab. Die
Einstellungen zeigten trotzdem gruen "Verbunden", der rote Balken bot die
Plex-Anmeldung an, und die Karte hatte keinen Weg, sich neu anzumelden - nur
"Trennen", mit der Warnung, dass verknuepfte Konten den Zugang verlieren.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.crypto import decrypt
from app.db import SessionLocal
from app.models import MediaServerConnection, User, UserMediaServerAccount, utcnow
from app.routers import mediaserver as mediaserver_router
from app.schemas import VerknuepftesKonto
from app.services import mediaserver_library
from app.services.mediaserver import ExternalAccount, MediaServerError, ServerCandidate

PRUEFEN = "/api/admin/mediaserver/connection/pruefen"


class EmbyZumPruefen:
    """Antwortet auf die Kontenliste so, wie der Test es vorgibt."""

    def __init__(self, fehler: MediaServerError | None = None) -> None:
        self.fehler = fehler

    async def list_server_users(self) -> list:
        if self.fehler:
            raise self.fehler
        return []


def _pruefen_mit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, server: EmbyZumPruefen
) -> dict:
    monkeypatch.setattr(mediaserver_router, "verbundene_anbieter", lambda _s: ["emby"])
    monkeypatch.setattr(mediaserver_router, "media_server_for_setup", lambda _s, _p: server)
    antwort = client.get(PRUEFEN, params={"provider": "emby"})
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


def test_abgelehnter_zugang_heisst_abgelehnt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fehler = MediaServerError("Der Emby-Server hat die Anmeldung nicht akzeptiert.", 401)
    assert _pruefen_mit(admin_client, monkeypatch, EmbyZumPruefen(fehler))["zustand"] == "abgelehnt"


def test_weggenommene_verwaltungsrechte_heissen_auch_abgelehnt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fehler = MediaServerError("verboten", 403)
    assert _pruefen_mit(admin_client, monkeypatch, EmbyZumPruefen(fehler))["zustand"] == "abgelehnt"


def test_schweigender_server_ist_nicht_erreichbar_und_nicht_abgelehnt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Ausfall darf nicht zur Neuanmeldung draengen - das Passwort ist nicht schuld."""
    fehler = MediaServerError("antwortet nicht", code="mediaserver_timeout")
    zustand = _pruefen_mit(admin_client, monkeypatch, EmbyZumPruefen(fehler))["zustand"]
    assert zustand == "nicht_erreichbar"


def test_funktionierender_zugang_ist_ok(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _pruefen_mit(admin_client, monkeypatch, EmbyZumPruefen())["zustand"] == "ok"


def test_nicht_verbundener_server_ist_404(admin_client: TestClient) -> None:
    antwort = admin_client.get(PRUEFEN, params={"provider": "emby"})
    assert antwort.status_code == 404


def test_ablauf_steht_je_anbieter_am_konto() -> None:
    """Der Balken muss sagen koennen, **welcher** Server den Zugang ablehnt."""
    plex = UserMediaServerAccount(provider="plex", account_id="1", token="t")
    emby = UserMediaServerAccount(
        provider="emby", account_id="2", token="t", token_invalid_at=utcnow()
    )
    ohne_token = UserMediaServerAccount(
        provider="jellyfin", account_id="3", token=None, token_invalid_at=utcnow()
    )
    assert VerknuepftesKonto.model_validate(plex).token_abgelehnt is False
    assert VerknuepftesKonto.model_validate(emby).token_abgelehnt is True
    # Ohne Token gibt es nichts, was abgelaufen sein koennte.
    assert VerknuepftesKonto.model_validate(ohne_token).token_abgelehnt is False


# --------------------------------------------------------------------------
# Neu anmelden: nur der Zugang wird ersetzt
# --------------------------------------------------------------------------


class EmbyZumAnmelden:
    provider = "emby"
    label = "Emby"

    def __init__(self) -> None:
        self.token = "token-alt"

    async def login_with_password(self, username, password, url=None, zweck=""):
        konto = ExternalAccount(provider="emby", account_id="emby-admin", username="admin")
        return self.token, konto, True

    async def list_servers(self, provider_token):
        return [
            ServerCandidate(
                machine_id="emby-1",
                name="Emby",
                url="http://emby.example.com:8096",
                owned=True,
                urls=("http://emby.example.com:8096",),
            )
        ]

    async def probe(self, url, provider_token):
        return True


def test_neu_anmelden_ersetzt_nur_den_zugang(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = EmbyZumAnmelden()
    monkeypatch.setattr(
        mediaserver_router, "media_server_for_setup", lambda _s, _p="plex", _u="": server
    )

    async def nicht_einlesen(*_a, **_k):
        return 0

    monkeypatch.setattr(mediaserver_library, "refresh", nicht_einlesen)
    daten = {
        "provider": "emby",
        "url": "http://emby.example.com:8096",
        "username": "admin",
        "password": "geheim",
    }

    assert admin_client.post("/api/admin/mediaserver/connect/password", json=daten).status_code == 200
    with SessionLocal() as session:
        (verbindung,) = session.scalars(select(MediaServerConnection)).all()
        erste_id = verbindung.id
        # Der Server lehnt den persoenlichen Zugang ab - der Balken erscheint.
        admin = session.scalars(select(User).where(User.role == "admin")).first()
        link = next(k for k in admin.mediaserver_accounts if k.provider == "emby")
        link.token_invalid_at = utcnow()
        session.commit()

    server.token = "token-neu"
    antwort = admin_client.post("/api/admin/mediaserver/connect/password", json=daten)
    assert antwort.status_code == 200, antwort.text

    with SessionLocal() as session:
        (verbindung,) = session.scalars(select(MediaServerConnection)).all()
        assert verbindung.id == erste_id
        assert decrypt(verbindung.token) == "token-neu"
        admin = session.scalars(select(User).where(User.role == "admin")).first()
        link = next(k for k in admin.mediaserver_accounts if k.provider == "emby")
        assert link.token_abgelehnt is False
    konten = antwort.json()["user"]["mediaserver_accounts"]
    assert {"provider": "emby", "username": "admin", "token_abgelehnt": False} in konten
