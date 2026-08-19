"""Anmelden und Verknuepfen ueber den Media-Server.

Gemockt wird an der **Abstraktions-Grenze**, nicht an httpx: Die Tests setzen
einen erfundenen ``MediaServer`` ein und pruefen damit Router und Kontologik.
Was Plex genau ueber die Leitung schickt, ist Sache von
``test_mediaserver_plextv.py`` - hier geht es um die Entscheidungen, die
Nexview daraus ableitet.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaServerBlock, Role, User
from app.routers import mediaserver as mediaserver_router
from app.services.mediaserver import ExternalAccount, LoginChallenge, MediaServer, ServerCandidate

from .conftest import ADMIN, auth_headers, create_user

KONTO = ExternalAccount(
    provider="plex",
    account_id="4711",
    username="Testkonto",
    email="gast@beispiel.de",
    thumb="https://plex.tv/bild.png",
)


class FakeMediaServer(MediaServer):
    """Ein Media-Server, der genau das antwortet, was der Test vorgibt."""

    provider = "plex"
    label = "Plex"

    def __init__(
        self,
        *,
        konto: ExternalAccount = KONTO,
        token: str | None = "anbieter-token",
        zugriff: bool = True,
        erreichbar: bool = True,
    ) -> None:
        self.konto = konto
        self.token = token
        self.zugriff = zugriff
        self.erreichbar = erreichbar

    async def verify(self) -> dict:
        return {"name": "Testserver", "version": "1.0", "machine_id": "maschine-1"}

    async def list_servers(self, provider_token: str) -> list[ServerCandidate]:
        return [
            ServerCandidate(
                machine_id="maschine-1",
                name="Wohnzimmer",
                url="http://10.0.0.5:32400",
                owned=True,
                urls=("http://10.0.0.5:32400", "https://fern.plex.direct:32400"),
            ),
            # Ein Server, auf den nur geteilt wurde - der darf nicht zur Auswahl
            # stehen, wohl aber in der Zugriffspruefung auftauchen.
            ServerCandidate(
                machine_id="fremde-maschine",
                name="Bei Freunden",
                url="http://192.168.5.9:32400",
                owned=False,
                urls=("http://192.168.5.9:32400",),
            ),
        ]

    async def probe(self, url: str, provider_token: str) -> bool:
        return self.erreichbar

    async def begin_login(self) -> LoginChallenge:
        return LoginChallenge(ref="99", code="ABCD", auth_url="https://app.plex.tv/auth#?code=ABCD")

    async def poll_login(self, ref: str, code: str = "") -> str | None:
        return self.token

    async def account_for_token(self, provider_token: str) -> ExternalAccount:
        return self.konto

    async def user_has_server_access(self, provider_token: str) -> bool:
        return self.zugriff


@pytest.fixture
def fake_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMediaServer]:
    """Verbundener Media-Server, der auf Zuruf antwortet."""
    server = FakeMediaServer()
    monkeypatch.setattr(mediaserver_router, "get_media_server", lambda _settings: server)
    monkeypatch.setattr(
        mediaserver_router, "media_server_for_setup", lambda _settings, _provider="plex": server
    )
    yield server


def verbinde(client: TestClient, **abweichend: object) -> None:
    """Einen Server als verbunden eintragen - ohne den Einrichtungsweg zu gehen."""
    from app.services import settings_service

    werte = {
        "mediaserver_provider": "plex",
        "mediaserver_machine_id": "maschine-1",
        "mediaserver_name": "Wohnzimmer",
        "mediaserver_url": "http://127.0.0.1:32400",
        "mediaserver_token": "admin-token",
        "mediaserver_auto_import": "on",
    }
    werte.update({k: str(v) for k, v in abweichend.items()})
    with SessionLocal() as session:
        settings_service.save_settings(session, werte)


def anmelden(client: TestClient) -> dict:
    """Den vollstaendigen Anmeldeweg gehen und die Antwort des Pollens liefern."""
    start = client.post("/api/auth/mediaserver/login/start", headers={"Authorization": ""})
    assert start.status_code == 200, start.text
    return client.post(
        "/api/auth/mediaserver/login/poll",
        json={"poll_token": start.json()["poll_token"]},
        headers={"Authorization": ""},
    )


# --------------------------------------------------------------------------
# Anmelden
# --------------------------------------------------------------------------


def test_ohne_verbindung_kein_plex_login(client: TestClient) -> None:
    """Ohne verbundenen Server gibt es den Weg gar nicht."""
    antwort = client.post("/api/auth/mediaserver/login/start")
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "mediaserver_not_configured"


def test_erste_anmeldung_legt_konto_an(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Auto-Import: Rolle, Kontingent und Alter kommen aus den Vorgaben."""
    verbinde(admin_client, mediaserver_default_quota_movies="5", mediaserver_default_age="16")

    antwort = anmelden(admin_client)
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["status"] == "ready"

    with SessionLocal() as session:
        neu = session.query(User).filter(User.mediaserver_account_id == "4711").one()
        assert neu.role == Role.user
        assert neu.quota_movies_limit == 5
        assert neu.age == 16
        # Wer neu dazukommt, darf nicht ungefragt herunterladen.
        assert neu.auto_approve is False
        # Ohne Passwort - eines setzt man spaeter im Profil.
        assert neu.has_password is False
        # Plex hat die Adresse geprueft, eine eigene Bestaetigung waere Formalie.
        assert neu.email_verified is True


def test_sitzung_funktioniert_wirklich(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Die ausgegebenen Token muessen an einer echten Anfrage bestehen."""
    verbinde(admin_client)
    tokens = anmelden(admin_client).json()["tokens"]

    ich = admin_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert ich.status_code == 200
    assert ich.json()["mediaserver_linked"] is True
    assert ich.json()["mediaserver_username"] == "Testkonto"
    assert ich.json()["has_password"] is False


def test_zweite_anmeldung_nutzt_dasselbe_konto(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Kein zweites Konto beim Wiederkommen - und auch nicht bei Gleichzeitigkeit."""
    verbinde(admin_client)
    anmelden(admin_client)
    anmelden(admin_client)

    with SessionLocal() as session:
        assert session.query(User).filter(User.mediaserver_account_id == "4711").count() == 1


def test_gleiche_adresse_wird_verknuepft(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Ein eingeladenes Konto mit derselben Adresse wird verbunden, nicht verdoppelt."""
    verbinde(admin_client)
    create_user(admin_client, "gast", email="gast@beispiel.de")

    assert anmelden(admin_client).status_code == 200

    with SessionLocal() as session:
        assert session.query(User).filter(User.email == "gast@beispiel.de").count() == 1
        gast = session.query(User).filter(User.username == "gast").one()
        assert gast.mediaserver_account_id == "4711"


def test_fremde_kennung_auf_gleicher_adresse_wird_abgelehnt(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Zwei Media-Server-Identitaeten auf einer Adresse duerfen sich nicht verdraengen."""
    verbinde(admin_client)
    create_user(
        admin_client,
        "gast",
        email="gast@beispiel.de",
        mediaserver_provider="plex",
        mediaserver_account_id="anderes-konto",
    )

    antwort = anmelden(admin_client)
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "mediaserver_link_conflict"


def test_ohne_server_zugriff_entsteht_kein_konto(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Die einzige Huerde gegen Fremde - und sie greift *vor* jeder Aenderung."""
    verbinde(admin_client)
    fake_server.zugriff = False

    antwort = anmelden(admin_client)
    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "mediaserver_no_access"

    with SessionLocal() as session:
        assert session.query(User).filter(User.mediaserver_account_id == "4711").count() == 0


def test_deaktiviertes_konto_kommt_nicht_herein(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Sonst waere der Media-Server ein Weg an der Sperre vorbei."""
    verbinde(admin_client)
    create_user(
        admin_client,
        "gast",
        email="gast@beispiel.de",
        mediaserver_provider="plex",
        mediaserver_account_id="4711",
        is_active=False,
    )

    antwort = anmelden(admin_client)
    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "account_disabled"


def test_ohne_auto_import_braucht_es_eine_einladung(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    verbinde(admin_client, mediaserver_auto_import="off")

    antwort = anmelden(admin_client)
    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "mediaserver_not_invited"


def test_noch_nicht_bestaetigt_meldet_pending(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Solange das Plex-Fenster offen ist, passiert nichts."""
    verbinde(admin_client)
    fake_server.token = None

    antwort = anmelden(admin_client)
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "pending"
    assert antwort.json()["tokens"] is None


def test_abgelaufener_vorgang(admin_client: TestClient, fake_server: FakeMediaServer) -> None:
    verbinde(admin_client)
    antwort = admin_client.post(
        "/api/auth/mediaserver/login/poll",
        json={"poll_token": "gibt-es-nicht"},
        headers={"Authorization": ""},
    )
    assert antwort.status_code == 410


# --------------------------------------------------------------------------
# Sperrliste
# --------------------------------------------------------------------------


def test_geloeschter_benutzer_kommt_nicht_zurueck(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Ohne Sperre waere das Loeschen wirkungslos - man meldet sich einfach neu an."""
    verbinde(admin_client)
    anmelden(admin_client)

    with SessionLocal() as session:
        neu = session.query(User).filter(User.mediaserver_account_id == "4711").one()
        kennung = neu.id

    assert admin_client.delete(f"/api/users/{kennung}").status_code == 204

    with SessionLocal() as session:
        assert session.query(MediaServerBlock).count() == 1

    antwort = anmelden(admin_client)
    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "mediaserver_blocked"

    with SessionLocal() as session:
        assert session.query(User).filter(User.mediaserver_account_id == "4711").count() == 0


def test_sperre_laesst_sich_aufheben(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    verbinde(admin_client)
    anmelden(admin_client)
    with SessionLocal() as session:
        kennung = session.query(User).filter(User.mediaserver_account_id == "4711").one().id
    admin_client.delete(f"/api/users/{kennung}")

    sperren = admin_client.get("/api/admin/mediaserver/blocks").json()
    assert len(sperren) == 1
    assert admin_client.delete(f"/api/admin/mediaserver/blocks/{sperren[0]['id']}").status_code == 204

    assert anmelden(admin_client).status_code == 200


# --------------------------------------------------------------------------
# Verknuepfen im Profil
# --------------------------------------------------------------------------


def verknuepfen(client: TestClient, headers: dict[str, str]) -> object:
    start = client.post("/api/auth/mediaserver/link/start", headers=headers)
    assert start.status_code == 200, start.text
    return client.post(
        "/api/auth/mediaserver/link/poll",
        json={"poll_token": start.json()["poll_token"]},
        headers=headers,
    )


def test_bestehendes_konto_nachtraeglich_verknuepfen(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Der Weg, den der Administrator nach der Einrichtung selbst geht."""
    verbinde(admin_client)
    create_user(admin_client, "gast", email="anders@beispiel.de")
    headers = auth_headers(admin_client, "gast", "passwort-1234")

    antwort = verknuepfen(admin_client, headers)
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["status"] == "ready"
    assert antwort.json()["user"]["mediaserver_linked"] is True
    # Das Passwort bleibt gueltig - beide Wege fuehren in dasselbe Konto.
    assert antwort.json()["user"]["has_password"] is True


def test_verknuepfen_ohne_zugriff_wird_abgelehnt(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Auch fuer Eingeladene: erst Freigabe in der Bibliothek, dann verbinden."""
    verbinde(admin_client)
    fake_server.zugriff = False
    create_user(admin_client, "gast", email="anders@beispiel.de")
    headers = auth_headers(admin_client, "gast", "passwort-1234")

    antwort = verknuepfen(admin_client, headers)
    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "mediaserver_no_access"


def test_fremdes_konto_kann_nicht_doppelt_verknuepft_werden(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    verbinde(admin_client)
    create_user(
        admin_client,
        "erster",
        email="erster@beispiel.de",
        mediaserver_provider="plex",
        mediaserver_account_id="4711",
    )
    create_user(admin_client, "zweiter", email="zweiter@beispiel.de")
    headers = auth_headers(admin_client, "zweiter", "passwort-1234")

    antwort = verknuepfen(admin_client, headers)
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "mediaserver_link_conflict"


def test_fremder_vorgang_wird_abgewiesen(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Ein abgefangener Merkzettel darf keine fremde Identitaet anhaengen."""
    verbinde(admin_client)
    create_user(admin_client, "eins", email="eins@beispiel.de")
    create_user(admin_client, "zwei", email="zwei@beispiel.de")

    start = admin_client.post(
        "/api/auth/mediaserver/link/start",
        headers=auth_headers(admin_client, "eins", "passwort-1234"),
    )
    antwort = admin_client.post(
        "/api/auth/mediaserver/link/poll",
        json={"poll_token": start.json()["poll_token"]},
        headers=auth_headers(admin_client, "zwei", "passwort-1234"),
    )
    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "mediaserver_challenge_foreign"


def test_trennen_wuerde_aussperren(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Ohne Passwort und ohne bestaetigte Adresse darf man sich nicht trennen."""
    verbinde(admin_client)
    anmelden(admin_client)

    with SessionLocal() as session:
        neu = session.query(User).filter(User.mediaserver_account_id == "4711").one()
        neu.email = None
        neu.email_verified = False
        session.commit()
        kennung = neu.id

    from app.security import create_access_token

    antwort = admin_client.delete(
        "/api/auth/mediaserver/link",
        headers={"Authorization": f"Bearer {create_access_token(kennung)}"},
    )
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "mediaserver_would_lock_out"


def test_trennen_mit_passwort_geht(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    verbinde(admin_client)
    create_user(admin_client, "gast", email="anders@beispiel.de")
    headers = auth_headers(admin_client, "gast", "passwort-1234")
    verknuepfen(admin_client, headers)

    antwort = admin_client.delete("/api/auth/mediaserver/link", headers=headers)
    assert antwort.status_code == 200
    assert antwort.json()["mediaserver_linked"] is False


# --------------------------------------------------------------------------
# Einrichtung durch den Administrator
# --------------------------------------------------------------------------


def test_verbinden_waehlt_server_und_verknuepft_den_admin(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Der Kern der Einrichtung: dabei entsteht kein zweites Admin-Konto."""
    start = admin_client.post("/api/admin/mediaserver/connect/start")
    assert start.status_code == 200, start.text
    poll_token = start.json()["poll_token"]

    auswahl = admin_client.post(
        "/api/admin/mediaserver/connect/poll", json={"poll_token": poll_token}
    )
    assert auswahl.status_code == 200, auswahl.text
    # Nur der eigene Server steht zur Wahl; der geteilte wird gezaehlt, nicht gezeigt.
    assert [s["machine_id"] for s in auswahl.json()["servers"]] == ["maschine-1"]
    assert auswahl.json()["shared_hidden"] == 1

    fertig = admin_client.post(
        "/api/admin/mediaserver/connect/select",
        json={"poll_token": poll_token, "machine_id": "maschine-1"},
    )
    assert fertig.status_code == 200, fertig.text
    assert fertig.json()["user"]["mediaserver_linked"] is True
    assert fertig.json()["user"]["username"] == ADMIN["username"]
    # Die lokale Adresse steht vorn und antwortet - also wird sie genommen.
    assert fertig.json()["reachable"] is True
    assert fertig.json()["server_url"] == "http://10.0.0.5:32400"

    einstellungen = admin_client.get("/api/settings").json()
    assert einstellungen["mediaserver_configured"] is True
    assert einstellungen["mediaserver_name"] == "Wohnzimmer"
    # Das Token des Anbieters darf die Oberflaeche nie sehen.
    assert "mediaserver_token" not in einstellungen

    # Und die Anmeldeseite weiss jetzt Bescheid.
    assert admin_client.get("/api/setup/status").json()["mediaserver_login"] is True


def test_fremder_server_kann_nicht_gewaehlt_werden(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Auch direkt geschickt nicht - sonst waere das Ausblenden reine Kosmetik.

    Einen Server zu verbinden, auf den man nur Zuschauer ist, faellt erst
    Monate spaeter auf: Die Zugriffspruefung haenge am falschen Personenkreis,
    und fremde Wiedergabe-Daten darf ohnehin nur der Eigentuemer lesen.
    """
    start = admin_client.post("/api/admin/mediaserver/connect/start")
    poll_token = start.json()["poll_token"]
    admin_client.post("/api/admin/mediaserver/connect/poll", json={"poll_token": poll_token})

    antwort = admin_client.post(
        "/api/admin/mediaserver/connect/select",
        json={"poll_token": poll_token, "machine_id": "fremde-maschine"},
    )
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "mediaserver_server_unknown"


def test_unerreichbarer_server_wird_gemeldet(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Gespeichert wird trotzdem - die Anmeldung braucht den Server ja nicht."""
    fake_server.erreichbar = False

    start = admin_client.post("/api/admin/mediaserver/connect/start")
    poll_token = start.json()["poll_token"]
    admin_client.post("/api/admin/mediaserver/connect/poll", json={"poll_token": poll_token})

    antwort = admin_client.post(
        "/api/admin/mediaserver/connect/select",
        json={"poll_token": poll_token, "machine_id": "maschine-1"},
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["reachable"] is False
    assert antwort.json()["warning"]
    # Verbunden ist trotzdem, sonst waere die Anmeldung grundlos blockiert.
    assert admin_client.get("/api/settings").json()["mediaserver_configured"] is True


def test_verbindung_loesen(admin_client: TestClient, fake_server: FakeMediaServer) -> None:
    verbinde(admin_client)
    assert admin_client.delete("/api/admin/mediaserver/connection").status_code == 204

    einstellungen = admin_client.get("/api/settings").json()
    assert einstellungen["mediaserver_configured"] is False
    assert admin_client.get("/api/setup/status").json()["mediaserver_login"] is False


def test_nur_admins_duerfen_verbinden(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    create_user(admin_client, "gast", email="gast2@beispiel.de")
    headers = auth_headers(admin_client, "gast", "passwort-1234")
    assert (
        admin_client.post("/api/admin/mediaserver/connect/start", headers=headers).status_code
        == 403
    )
    assert admin_client.get("/api/admin/mediaserver/blocks", headers=headers).status_code == 403
