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
from app.services import mediaserver as mediaserver_paket
from app.services.mediaserver import (
    ExternalAccount,
    LoginChallenge,
    MediaServer,
    MediaServerError,
    ServerCandidate,
)

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
    # ``get_media_server`` ist hier weggefallen: Der Code-Ablauf sucht seit dem
    # Parallelbetrieb gezielt einen Anbieter mit Code-Anmeldung, statt blind
    # den ersten verbundenen zu nehmen (siehe ``_code_server``).
    monkeypatch.setattr(
        mediaserver_router,
        "media_server_for_setup",
        lambda _settings, _provider="plex", url="": server,
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
    """Ohne verbundenen Server gibt es den Weg gar nicht.

    ⚠️ Die Kennung ist bewusst **eng**: ``mediaserver_no_code_login`` und nicht
    das fruehere ``mediaserver_not_configured``. Letzteres stand an vier
    Stellen fuer vier verschiedene Saetze - "kein Server mit Code-Anmeldung",
    "dieser Server ist nicht verbunden", "gar kein Server verbunden" und "kein
    Server mit Merkliste". Solange die Kennung nur eine Nummer war, fiel das
    nicht auf; sobald daraus ein uebersetzter Satz wird, muesste eine
    Uebersetzung vier Dinge gleichzeitig sagen.
    """
    antwort = client.post("/api/auth/mediaserver/login/start")
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "mediaserver_no_code_login"


def test_erste_anmeldung_legt_konto_an(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Auto-Import: nur die Rolle kommt aus den Vorgaben.

    Es gab dort einmal auch Stueckzahlen und ein Alter. Beide sind weg, und
    beide aus einem eigenen Grund:

    * Die **Stueckzahlen** waren im Speicher-Betrieb wirkungslos - dort zaehlt
      der Platz, und die Pruefung steigt vorher aus. Die Bremse fuer neue
      Konten ist ohnehin die Freigabe, nicht das Kontingent.
    * Das **Alter** hat nie gewirkt: ``db._altersgrenzen_aufraeumen`` setzt es
      bei jedem Start an jedem Konto zurueck, das kein Kinderkonto ist - und
      per Auto-Import entsteht nie eines. Der Wert ueberlebte bis zum naechsten
      Neustart. Fuer Kinder gibt es seit 0.16.0 den richtigen Weg.
    """
    verbinde(admin_client)

    antwort = anmelden(admin_client)
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["status"] == "ready"

    with SessionLocal() as session:
        neu = session.query(User).filter(User.mediaserver_account_id == "4711").one()
        assert neu.role == Role.user
        assert neu.quota_movies_limit is None, "neue Konten kommen ohne Stueck-Grenze"
        assert neu.quota_series_limit is None
        assert neu.age is None, "Altersgrenzen gibt es nur an Kinderkonten"
        # Wer neu dazukommt, darf nicht ungefragt herunterladen. **Das** ist die
        # Bremse - nicht das Kontingent.
        assert neu.auto_approve is False
        # Ohne Passwort - und ohne den Weg, sich selbst eines zu setzen.
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


# --------------------------------------------------------------------------
# Die ganze Verbindung trennen - was das fuer die anderen bedeutet
# --------------------------------------------------------------------------


def _ohne_passwort_und_adresse(account_id: str = "4711") -> int:
    """Ein per Auto-Import entstandenes Konto vollends wehrlos machen.

    Der Import setzt bereits ``unusable_password``; hier faellt zusaetzlich
    die Adresse weg. Damit bleibt wirklich kein Weg mehr hinein ausser dem
    Medienserver - genau der Fall, um den es geht.
    """
    with SessionLocal() as session:
        neu = session.query(User).filter(User.mediaserver_account_id == account_id).one()
        neu.email = None
        neu.email_verified = False
        session.commit()
        return neu.id


def test_folgen_sagen_vorher_wen_es_traefe(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Die Auskunft gibt es **vor** dem Klick - und auch dann, wenn nichts droht.

    Ein Hinweis, der nur im Ernstfall erscheint, wird beim ersten Mal nicht
    gelesen, weil niemand ihn kennt.
    """
    verbinde(admin_client)
    anmelden(admin_client)

    # Noch hat das importierte Konto eine Adresse vom Anbieter - Entwarnung.
    entwarnung = admin_client.get("/api/admin/mediaserver/connection/folgen")
    assert entwarnung.status_code == 200
    assert entwarnung.json()["verknuepft"] >= 1
    assert entwarnung.json()["gefaehrdet"] == []

    _ohne_passwort_und_adresse()

    warnung = admin_client.get("/api/admin/mediaserver/connection/folgen")
    assert warnung.status_code == 200
    gefaehrdet = warnung.json()["gefaehrdet"]
    assert len(gefaehrdet) == 1
    # Der Name muss mit - eine blosse Anzahl sagt dem Administrator nicht,
    # wem er ein Passwort setzen soll.
    assert gefaehrdet[0]["username"]


def test_trennen_wird_abgelehnt_wenn_es_andere_aussperrt(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Dieselbe Sperre wie beim eigenen Trennen - nur eine Ebene hoeher.

    Sie sitzt im Endpunkt und nicht bloss im Bestaetigungsdialog: Ein Dialog
    schuetzt nur den Weg, der durch ihn hindurchfuehrt.
    """
    verbinde(admin_client)
    anmelden(admin_client)
    _ohne_passwort_und_adresse()

    antwort = admin_client.delete("/api/admin/mediaserver/connection")

    assert antwort.status_code == 409
    detail = antwort.json()["detail"]
    assert detail["code"] == "mediaserver_would_lock_out_others"
    assert len(detail["gefaehrdet"]) == 1

    # Und nichts ist passiert - die Verbindung steht noch.
    from app.services import settings_service

    with SessionLocal() as session:
        assert settings_service.load_settings(session).mediaserver_configured


def test_trennen_geht_mit_ausdruecklicher_bestaetigung(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Der Administrator darf ueberstimmen - er kann den Schaden ja beheben.

    Anders als der einzelne Nutzer, der sich selbst aussperrt und danach
    niemanden mehr hat, der ihm hilft.
    """
    verbinde(admin_client)
    anmelden(admin_client)
    _ohne_passwort_und_adresse()

    antwort = admin_client.delete(
        "/api/admin/mediaserver/connection", params={"bestaetigt": "true"}
    )

    assert antwort.status_code == 204
    from app.services import settings_service

    with SessionLocal() as session:
        assert not settings_service.load_settings(session).mediaserver_configured


def test_trennen_ohne_gefaehrdete_braucht_keine_bestaetigung(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Der Normalfall darf nicht schwerer werden als vorher.

    Wer nur Konten mit Passwort hat, soll nicht gegen eine Sperre laufen, die
    fuer ihn nie gedacht war.
    """
    verbinde(admin_client)
    create_user(admin_client, "gast", email="gast@beispiel.de")
    headers = auth_headers(admin_client, "gast", "passwort-1234")
    verknuepfen(admin_client, headers)

    antwort = admin_client.delete("/api/admin/mediaserver/connection")

    assert antwort.status_code == 204


def test_unbekannter_anbieter_wird_abgelehnt(admin_client: TestClient) -> None:
    """Was nicht in ``PROVIDERS`` steht, kommt gar nicht erst in Gang.

    Ohne diese Pruefung entstuende ein Anmeldevorgang, der beim Abholen mit
    einem Serverfehler endet - und der Administrator suchte den Grund bei
    seinem Server statt bei einem Tippfehler.
    """
    antwort = admin_client.post(
        "/api/admin/mediaserver/connect/start", json={"provider": "kodi"}
    )

    assert antwort.status_code == 400
    assert antwort.json()["detail"]["code"] == "mediaserver_unknown_provider"


def test_ohne_angabe_bleibt_es_bei_plex(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Ein Aufruf ohne Anbieter verhaelt sich wie frueher.

    Die Oberflaeche schickt heute noch keinen - und soll davon nichts merken,
    bis sie es tut.
    """
    antwort = admin_client.post("/api/admin/mediaserver/connect/start", json={})

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["poll_token"]


# --------------------------------------------------------------------------
# Anmelden mit Benutzername und Passwort (Jellyfin, Emby)
# --------------------------------------------------------------------------


JELLY_KONTO = ExternalAccount(
    provider="jellyfin",
    account_id="jf-4711",
    username="Markus",
    # ⚠️ **Ohne Adresse - und das ist der Punkt.** Jellyfin hat kein solches
    # Feld. Daran haengt, dass darueber kein Konto entstehen darf.
    email=None,
)


class FakeJellyfin(FakeMediaServer):
    """Ein Anbieter ohne Vermittler: Benutzername und Passwort direkt."""

    provider = "jellyfin"
    label = "Jellyfin"
    login_kind = "password"
    knows_email = False

    def __init__(self, *, passwort: str = "geheim", **kw: object) -> None:
        super().__init__(konto=JELLY_KONTO, **kw)  # type: ignore[arg-type]
        self.passwort = passwort

    async def login_with_password(
        self, username: str, password: str, url: str | None = None, zweck: str = ""
    ) -> tuple[str, ExternalAccount, bool]:
        # ``zweck`` trennt die Geraete-Kennungen - hier nur entgegengenommen,
        # geprueft wird er gegen den echten Server.
        if password != self.passwort:
            raise MediaServerError("Abgelehnt.", 401)
        return "jelly-token", self.konto, True

    async def watchlist(self, provider_token: str):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture()
def jelly_server(monkeypatch: pytest.MonkeyPatch) -> FakeJellyfin:
    """Jellyfin als verbundenen Anbieter unterschieben."""
    server = FakeJellyfin()
    monkeypatch.setitem(mediaserver_paket.PROVIDERS, "jellyfin", FakeJellyfin)
    monkeypatch.setattr(
        mediaserver_router, "media_server_for_setup", lambda _s, _p, url="": server
    )
    return server


def _plex_verbinden() -> None:
    """Eine Plex-Zeile in der Verbindungstabelle - neben der Jellyfin-Zeile."""
    from app.crypto import encrypt
    from app.models import MediaServerConnection

    with SessionLocal() as session:
        session.add(
            MediaServerConnection(
                provider="plex",
                machine_id="maschine-1",
                name="Wohnzimmer",
                url="http://127.0.0.1:32400",
                token=encrypt("admin-token"),
            )
        )
        session.commit()


def _jelly_verbinden() -> None:
    """Eine Jellyfin-Verbindung eintragen - neben einer moeglichen Plex-Zeile."""
    from app.models import MediaServerConnection
    from app.crypto import encrypt

    with SessionLocal() as session:
        session.add(
            MediaServerConnection(
                provider="jellyfin",
                machine_id="jf-maschine",
                name="Jellyfin",
                url="http://127.0.0.1:8096",
                token=encrypt("admin-token"),
            )
        )
        session.commit()


def _jelly_anmelden(client: TestClient, passwort: str = "geheim"):
    return client.post(
        "/api/auth/mediaserver/login/password",
        json={"provider": "jellyfin", "username": "Markus", "password": passwort},
        headers={"Authorization": ""},
    )


def test_passwort_anmeldung_liefert_vollstaendige_token(
    admin_client: TestClient, jelly_server: FakeJellyfin
) -> None:
    """Das Token-Paar muss **vollstaendig** sein.

    ⚠️ Genau das fehlte: ``expires_in`` war nicht gesetzt, Pydantic liess das
    Paar gar nicht erst entstehen, und die Anmeldung endete in einem 500er -
    nachdem das Konto bereits verknuepft war. Der Fall kam beim Ausprobieren
    heraus, nicht hier; deshalb steht er jetzt hier.
    """
    verbinde(admin_client)
    _jelly_verbinden()
    # Ein Konto, das Jellyfin schon verknuepft hat - nur so geht es hinein.
    create_user(
        admin_client,
        "markus",
        email="markus@beispiel.de",
        mediaserver_provider="jellyfin",
        mediaserver_account_id="jf-4711",
        mediaserver_username="Markus",
    )

    antwort = _jelly_anmelden(admin_client)
    assert antwort.status_code == 200, antwort.text
    tokens = antwort.json()["tokens"]
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] > 0
    assert tokens["token_type"] == "bearer"
    # Und die Sitzung gehoert dem **bestehenden** Konto, nicht einem neuen.
    with SessionLocal() as session:
        assert session.query(User).filter(User.username == "markus").count() == 1


def test_falsches_passwort_ist_kein_serverausfall(
    admin_client: TestClient, jelly_server: FakeJellyfin
) -> None:
    """401 statt 502 - sonst heisst es "Server kaputt" statt "vertippt"."""
    verbinde(admin_client)
    _jelly_verbinden()
    create_user(
        admin_client,
        "markus",
        email="markus@beispiel.de",
        mediaserver_provider="jellyfin",
        mediaserver_account_id="jf-4711",
    )

    antwort = _jelly_anmelden(admin_client, passwort="daneben")
    assert antwort.status_code == 401
    assert antwort.json()["detail"]["code"] == "mediaserver_bad_credentials"


def test_ohne_verknuepfung_entsteht_kein_konto(
    admin_client: TestClient, jelly_server: FakeJellyfin
) -> None:
    """Ueber einen Anbieter ohne Adresse darf kein Konto neu entstehen.

    Sonst bekaeme jemand, der laengst ein Nexview-Konto hat, ein **zweites** -
    ohne Adresse und ohne Passwort, also eines, in das nur der Medienserver
    hineinfuehrt. Und zwar auch dann, wenn das automatische Anlegen an ist.
    """
    verbinde(admin_client, mediaserver_auto_import="on")
    _jelly_verbinden()

    antwort = _jelly_anmelden(admin_client)
    # 403 wie jede andere Absage aus ``KontoFehler`` - der Zugang wird
    # verweigert, die Anfrage war nicht fehlerhaft.
    assert antwort.status_code == 403, antwort.text
    assert antwort.json()["detail"]["code"] == "mediaserver_no_new_account"

    with SessionLocal() as session:
        assert (
            session.query(User)
            .filter(User.mediaserver_account_id == "jf-4711")
            .count()
            == 0
        )


def test_plex_nimmt_den_passwortweg_nicht(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Plex hat einen Vermittler - Passwoerter gehoeren dort nicht hin."""
    verbinde(admin_client)
    antwort = admin_client.post(
        "/api/auth/mediaserver/login/password",
        json={"provider": "plex", "username": "x", "password": "y"},
        headers={"Authorization": ""},
    )
    assert antwort.status_code == 400
    assert antwort.json()["detail"]["code"] == "mediaserver_password_unsupported"


def test_trennen_nimmt_nur_den_genannten_anbieter(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Ein Anbieter geht, der andere bleibt.

    ⚠️ Genau das ging schief: Das Backend konnte es längst, die Oberfläche
    rief aber ohne ``provider`` auf - und ohne Angabe fallen *alle*
    Verbindungen. Ein Klick auf "Jellyfin trennen" nahm Plex gleich mit.
    """
    from app.models import MediaServerConnection

    verbinde(admin_client)
    _plex_verbinden()
    _jelly_verbinden()
    with SessionLocal() as session:
        assert session.query(MediaServerConnection).count() == 2

    antwort = admin_client.delete(
        "/api/admin/mediaserver/connection?provider=jellyfin&bestaetigt=true"
    )
    assert antwort.status_code == 204, antwort.text

    with SessionLocal() as session:
        uebrig = [z.provider for z in session.query(MediaServerConnection).all()]
    assert uebrig == ["plex"], uebrig


def test_trennen_ohne_anbieter_nimmt_alle(
    admin_client: TestClient, fake_server: FakeMediaServer
) -> None:
    """Ohne Angabe fallen alle - das ist das alte Verhalten und bleibt so.

    Es ist der Weg für "Medienserver ganz abschalten". Er darf nur nicht
    versehentlich getroffen werden; deshalb schickt die Oberfläche immer einen
    Anbieter mit.
    """
    from app.models import MediaServerConnection

    verbinde(admin_client)
    _plex_verbinden()
    _jelly_verbinden()

    antwort = admin_client.delete("/api/admin/mediaserver/connection?bestaetigt=true")
    assert antwort.status_code == 204, antwort.text

    with SessionLocal() as session:
        assert session.query(MediaServerConnection).count() == 0
