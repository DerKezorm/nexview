"""Der Serverzugang wird mit erneuert, wenn sich ein Administrator neu anmeldet.

Gemeldet am 17.09.2026: Emby lehnte persoenlichen und Serverzugang ab. Die
Anmeldung ueber den roten Balken erneuerte nur den persoenlichen - die Kachel
blieb bei "Zugang abgelehnt".
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.crypto import decrypt, encrypt
from app.db import SessionLocal
from app.models import MediaServerConnection, Role, User
from app.routers import mediaserver as mediaserver_router
from app.services import serverzugang
from app.services.mediaserver import ExternalAccount, MediaServerError, ServerCandidate
from app.services.settings_service import load_settings

MASCHINE = "emby-1"
ABGELEHNT = MediaServerError("abgelehnt", 401)


class Server:
    """Ein Medienserver, dessen gespeicherter Zugang gilt oder nicht."""

    provider = "emby"
    label = "Emby"

    def __init__(
        self,
        *,
        alter_zugang: MediaServerError | None = ABGELEHNT,
        maschine: str = MASCHINE,
        eigener: bool = True,
    ) -> None:
        self.alter_zugang = alter_zugang
        self.maschine = maschine
        self.eigener = eigener

    async def list_server_users(self) -> list:
        if self.alter_zugang:
            raise self.alter_zugang
        return []

    async def list_servers(self, token: str) -> list[ServerCandidate]:
        return [
            ServerCandidate(
                machine_id=self.maschine,
                name="Emby",
                url="http://emby.example.com",
                owned=self.eigener,
                urls=("http://emby.example.com",),
            )
        ]


def _verbindung() -> None:
    with SessionLocal() as session:
        session.add(
            MediaServerConnection(
                provider="emby",
                machine_id=MASCHINE,
                name="Emby",
                url="http://emby.example.com",
                token=encrypt("alt"),
            )
        )
        session.commit()


def _erneuern(
    monkeypatch: pytest.MonkeyPatch,
    server: Server,
    *,
    rolle: Role = Role.admin,
    neues_token: str | None = "neu",
    nur_eigene: bool = False,
) -> tuple[bool, list[str]]:
    monkeypatch.setattr(serverzugang.medienserver, "media_server_for_setup", lambda _s, _p: server)
    gefragt: list[str] = []

    async def token_holen() -> str | None:
        gefragt.append("ja")
        return neues_token

    with SessionLocal() as session:
        person = User(username="chef", role=rolle)
        ergebnis = asyncio.run(
            serverzugang.mit_erneuern(
                session, load_settings(session), person, "emby", token_holen, nur_eigene=nur_eigene
            )
        )
    return ergebnis, gefragt


def _token() -> str:
    with SessionLocal() as session:
        return decrypt(session.scalars(select(MediaServerConnection)).one().token)


def test_abgelehnter_serverzugang_wird_ersetzt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _verbindung()
    ersetzt, _ = _erneuern(monkeypatch, Server())
    assert ersetzt is True
    assert _token() == "neu"


def test_funktionierender_zugang_bleibt_und_es_wird_nicht_angemeldet(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _verbindung()
    ersetzt, gefragt = _erneuern(monkeypatch, Server(alter_zugang=None))
    assert ersetzt is False
    assert gefragt == []
    assert _token() == "alt"


def test_kein_administrator_fasst_den_serverzugang_nicht_an(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _verbindung()
    ersetzt, gefragt = _erneuern(monkeypatch, Server(), rolle=Role.user)
    assert (ersetzt, gefragt) == (False, [])
    assert _token() == "alt"


def test_ausgefallener_server_ist_kein_abgelehnter_zugang(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _verbindung()
    aus = MediaServerError("antwortet nicht", code="mediaserver_timeout")
    ersetzt, gefragt = _erneuern(monkeypatch, Server(alter_zugang=aus))
    assert (ersetzt, gefragt) == (False, [])
    assert _token() == "alt"


def test_anderer_server_wird_nicht_eingetragen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _verbindung()
    ersetzt, _ = _erneuern(monkeypatch, Server(maschine="fremder-server"))
    assert ersetzt is False
    assert _token() == "alt"


def test_ohne_verwaltungsrechte_bleibt_es_beim_alten(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _verbindung()
    ersetzt, _ = _erneuern(monkeypatch, Server(), neues_token=None)
    assert ersetzt is False
    assert _token() == "alt"


def test_geteilter_plex_server_zaehlt_nicht(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _verbindung()
    ersetzt, _ = _erneuern(monkeypatch, Server(eigener=False), nur_eigene=True)
    assert ersetzt is False
    assert _token() == "alt"


def test_persoenliche_anmeldung_des_administrators_nimmt_den_serverzugang_mit(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Weg ueber den Balken: ``/link/password``."""
    _verbindung()

    class EmbyMitPasswort(Server):
        async def login_with_password(self, username, password, url=None, zweck=""):
            konto = ExternalAccount(provider="emby", account_id="emby-admin", username="admin")
            return f"token-{zweck or 'persoenlich'}", konto, True

        async def user_has_server_access(self, token):
            return True

    server = EmbyMitPasswort()
    monkeypatch.setattr(mediaserver_router, "verbundene_anbieter", lambda _s: ["emby"])
    monkeypatch.setattr(mediaserver_router, "media_server_for_setup", lambda _s, _p="emby", _u="": server)
    monkeypatch.setattr(serverzugang.medienserver, "media_server_for_setup", lambda _s, _p: server)

    antwort = admin_client.post(
        "/api/auth/mediaserver/link/password",
        json={"provider": "emby", "username": "admin", "password": "geheim"},
    )
    assert antwort.status_code == 200, antwort.text
    # Mit eigener Geraetekennung - sonst loeste die persoenliche Anmeldung den
    # Serverzugang bei Jellyfin gleich wieder ab.
    assert _token() == "token-server"


@pytest.mark.parametrize(("eigener", "erwartet"), [(True, "plex-neu"), (False, "alt")])
def test_plex_anmeldung_des_administrators_nimmt_den_serverzugang_mit(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, eigener: bool, erwartet: str
) -> None:
    """Der Plex-Weg ueber den Balken: ``/api/watchlist/connect/poll``.

    Ein Server, auf den nur geteilt wurde, gehoert nicht in die Verbindung -
    plex.tv nennt auch solche.
    """
    from types import SimpleNamespace

    from app.models import UserMediaServerAccount, utcnow
    from app.routers import watchlist as watchlist_router

    with SessionLocal() as session:
        session.add(
            MediaServerConnection(
                provider="plex", machine_id=MASCHINE, name="Plex", url="http://plex.example.com",
                token=encrypt("alt"),
            )
        )
        admin = session.scalars(select(User).where(User.role == Role.admin)).one()
        admin.mediaserver_accounts.append(
            UserMediaServerAccount(
                provider="plex", account_id="plex-1", username="chef",
                linked_at=utcnow().replace(tzinfo=None), token=encrypt("alt"),
            )
        )
        session.commit()

    konto = ExternalAccount(provider="plex", account_id="plex-1", username="chef")

    async def identitaet(db, server, poll_token, *, erwarteter_benutzer):
        return SimpleNamespace(used_at=None), {"token": encrypt("plex-neu")}, konto

    server = Server(eigener=eigener)
    monkeypatch.setattr(watchlist_router, "_merklisten_server", lambda _db: (server, None))
    monkeypatch.setattr(watchlist_router, "_identitaet", identitaet)
    monkeypatch.setattr(serverzugang.medienserver, "media_server_for_setup", lambda _s, _p: server)

    antwort = admin_client.post("/api/watchlist/connect/poll", json={"poll_token": "x"})
    assert antwort.status_code == 200, antwort.text
    with SessionLocal() as session:
        verbindung = session.scalars(
            select(MediaServerConnection).where(MediaServerConnection.provider == "plex")
        ).one()
        assert decrypt(verbindung.token) == erwartet
