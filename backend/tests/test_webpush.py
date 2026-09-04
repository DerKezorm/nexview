"""Web Push - was still falsch sein kann.

⚠️ **Hier scheitert nichts laut.** Eine falsch verschluesselte Meldung nimmt
der Push-Dienst an, und sie geht auf dem Handy nur nicht auf. Ein Haken, der
nicht zieht, schickt eine Meldung zu viel oder eine zu wenig. Ein 403, der wie
ein 410 behandelt wird, raeumt bei einem eigenen Fehler alle Geraete weg.

Deshalb prueft diese Datei die Kryptografie **rueckwaerts** - der Doppelgaenger
des Browsers hat einen echten P-256-Schluessel und entschluesselt, was
hinausging - und daneben die Regeln: wem ein Abonnement gehoert, welche Haken
den Postausgang steuern, wann ein Ziel weggeraeumt wird und was der Betreiber
davon sieht.
"""

from __future__ import annotations

import asyncio
import base64
import json

import http_ece
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.crypto import decrypt
from app.db import SessionLocal
from app.models import ChannelKind, ChannelMessage, ChannelTarget, NotificationType, User
from app.services import channel_outbox, notify, webpush
from app.services.channels import webpush as kanal
from app.services.settings_service import load_settings, save_settings

from .conftest import ADMIN, auth_headers, create_user

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


# --------------------------------------------------------------------------- #
# Ein Browser, der wirklich entschluesseln kann
# --------------------------------------------------------------------------- #


class Browser:
    """Ein Abonnement mit echten Schluesseln - und der Faehigkeit, mitzulesen.

    ⚠️ **Ohne die zweite Haelfte waere jeder Test hier hohl.** Ein
    Doppelgaenger, der nur "201" sagt, bestaetigt, dass irgendwelche Bytes
    hinausgingen. Ob sie sich entschluesseln lassen, sagt er nicht - und genau
    das ist der Unterschied zwischen einer Meldung und einer, die auf dem
    Handy nicht aufgeht.
    """

    def __init__(self, endpoint: str = "https://push.example.com/abo/1") -> None:
        self.endpoint = endpoint
        self._privat = ec.generate_private_key(ec.SECP256R1())
        self._auth = b"0123456789abcdef"

    @property
    def p256dh(self) -> str:
        punkt = self._privat.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        return base64.urlsafe_b64encode(punkt).rstrip(b"=").decode()

    @property
    def auth(self) -> str:
        return base64.urlsafe_b64encode(self._auth).rstrip(b"=").decode()

    def abonnement(self, language: str = "de") -> dict[str, str]:
        return {
            "endpoint": self.endpoint,
            "p256dh": self.p256dh,
            "auth": self.auth,
            "language": language,
        }

    def lesen(self, rumpf: bytes) -> dict:
        klar = http_ece.decrypt(
            rumpf,
            private_key=self._privat,
            auth_secret=self._auth,
            version="aes128gcm",
        )
        return json.loads(klar)


class Postbote:
    """Der Push-Dienst - faengt ab, was ``send`` hinausschicken will."""

    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.sendungen: list[tuple[str, bytes, dict[str, str]]] = []

    def __call__(self, *_a, **_kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, url, content=None, headers=None):
        self.sendungen.append((url, content, dict(headers or {})))
        return httpx.Response(self.status, request=httpx.Request("POST", url))


@pytest.fixture
def postbote(monkeypatch: pytest.MonkeyPatch) -> Postbote:
    bote = Postbote()
    monkeypatch.setattr(kanal.httpx, "AsyncClient", bote)
    return bote


def _anmelden(client: TestClient, browser: Browser, language: str = "de", **kopf: str) -> dict:
    antwort = client.post(
        "/api/push/devices",
        json=browser.abonnement(language),
        headers={"User-Agent": CHROME, **kopf},
    )
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


def _ziele() -> list[ChannelTarget]:
    with SessionLocal() as db:
        return list(db.scalars(select(ChannelTarget).where(ChannelTarget.channel == ChannelKind.webpush)))


def _auftraege(kind: ChannelKind = ChannelKind.webpush) -> list[ChannelMessage]:
    with SessionLocal() as db:
        return list(db.scalars(select(ChannelMessage).where(ChannelMessage.channel == kind)))


def _anspruch(headers: dict[str, str]) -> dict:
    """Die Behauptungen aus der VAPID-Unterschrift."""
    token = headers["Authorization"]
    assert token.startswith("vapid t="), token
    jwt = token[len("vapid t=") :].split(",")[0]
    teil = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(teil + "=" * (-len(teil) % 4)))


def _meldung_anlegen(kind: NotificationType, username: str = ADMIN["username"]) -> None:
    """Eine Glockenmeldung fuer diesen Menschen - so, wie die Anwendung es tut."""
    with SessionLocal() as db:
        person = db.query(User).filter(User.username == username).one()
        notify.create(db, user=person, kind=kind, message_key="test", title="Der Film")
        db.commit()


def _verschicken() -> int:
    with SessionLocal() as db:
        return asyncio.run(channel_outbox.process(db, load_settings(db)))


# --------------------------------------------------------------------------- #
# Der Schluessel
# --------------------------------------------------------------------------- #


def test_der_oeffentliche_schluessel_ist_ein_roher_punkt(admin_client: TestClient) -> None:
    """65 Byte unkomprimierter P-256-Punkt, base64url ohne Polster.

    ⚠️ Ein PEM oder DER an dieser Stelle laesst den Browser mit einem
    ``InvalidCharacterError`` stehen, und das liest sich wie ein Fehler im
    eigenen JavaScript.
    """
    erster = admin_client.get("/api/push/key").json()["public_key"]
    roh = base64.urlsafe_b64decode(erster + "=" * (-len(erster) % 4))

    assert len(roh) == 65
    assert roh[0] == 0x04
    assert "=" not in erster

    # Einmal erzeugt, danach nie wieder: Ein zweites Paar machte jedes
    # angemeldete Geraet still taub.
    assert admin_client.get("/api/push/key").json()["public_key"] == erster


# --------------------------------------------------------------------------- #
# Anmelden, auflisten, abmelden
# --------------------------------------------------------------------------- #


def test_anmelden_legt_ein_bestaetigtes_ziel_mit_besitzer_an(admin_client: TestClient) -> None:
    browser = Browser()
    zeile = _anmelden(admin_client, browser)

    assert zeile["name"] == "Chrome, Windows"
    assert zeile["this"] is True
    assert zeile["vorbelegt"] is True

    with SessionLocal() as db:
        ziel = db.scalar(select(ChannelTarget).where(ChannelTarget.channel == ChannelKind.webpush))
        admin = db.query(User).filter(User.username == ADMIN["username"]).one()
        assert ziel is not None
        assert ziel.user_id == admin.id
        assert ziel.api_key_id is None
        # Ohne Code bestaetigt: Das Abonnement entstand in genau dem Browser,
        # der die Meldungen bekommt.
        assert ziel.verified is True
        assert ziel.enabled is True
        assert ziel.url == browser.endpoint
        assert ziel.token == browser.p256dh
        # Das Ableitungsgeheimnis liegt verschluesselt wie jedes Passwort.
        assert ziel.password != browser.auth
        assert decrypt(ziel.password) == browser.auth
        assert ziel.language == "de"
        # Erstes Geraet, keine Haken: alle gesetzt.
        assert all(getattr(admin, feld) for feld in webpush.HAKEN)


def test_dieselbe_adresse_gibt_keine_zweite_zeile(admin_client: TestClient) -> None:
    """Der Browser meldet sich bei jedem Start erneut an - mit derselben Adresse."""
    browser = Browser()
    _anmelden(admin_client, browser)
    zweite = _anmelden(admin_client, browser)

    assert len(_ziele()) == 1
    assert zweite["vorbelegt"] is False
    geraete = admin_client.get("/api/push/devices", params={"endpoint": browser.endpoint}).json()
    assert [g["this"] for g in geraete] == [True]


def test_die_adresse_wechselt_den_besitzer(admin_client: TestClient) -> None:
    """Ein geteilter Rechner: Wer sich jetzt anmeldet, bekommt das Abonnement."""
    browser = Browser()
    _anmelden(admin_client, browser)

    zweiter = create_user(admin_client, "zweiter")
    kopf = auth_headers(admin_client, "zweiter", "passwort-1234")
    _anmelden(admin_client, browser, **kopf)

    ziele = _ziele()
    assert len(ziele) == 1
    assert ziele[0].user_id == zweiter["id"]
    # Der Vorgaenger sieht das Geraet nicht mehr - und bekommt nichts mehr.
    assert admin_client.get("/api/push/devices").json() == []


def test_vorbelegt_wird_nur_ohne_haken(admin_client: TestClient) -> None:
    """Wer schon Haken hat, wird nicht angefasst - auch nicht beim ersten Geraet."""
    admin_client.patch("/api/auth/me", json={"push_ticket": True})

    zeile = _anmelden(admin_client, Browser())

    assert zeile["vorbelegt"] is False
    konto = admin_client.get("/api/auth/me").json()
    assert konto["push_ticket"] is True
    assert konto["push_download_complete"] is False


def test_unbrauchbare_adresse_wird_abgewiesen(admin_client: TestClient) -> None:
    browser = Browser(endpoint="ftp://push.example.com/abo/1")
    antwort = admin_client.post("/api/push/devices", json=browser.abonnement(), headers={"User-Agent": CHROME})
    assert antwort.status_code == 422, antwort.text
    assert "push_endpoint_invalid" in antwort.text
    assert _ziele() == []


def test_abmelden_nur_das_eigene(admin_client: TestClient) -> None:
    """⚠️ Die Kennungen sind fortlaufende Zahlen - ohne Besitzerpruefung
    meldete jeder die Geraete jedes anderen ab."""
    zeile = _anmelden(admin_client, Browser())
    create_user(admin_client, "zweiter")
    fremd = auth_headers(admin_client, "zweiter", "passwort-1234")

    antwort = admin_client.delete(f"/api/push/devices/{zeile['id']}", headers=fremd)
    assert antwort.status_code == 404, antwort.text
    assert "push_device_unknown" in antwort.text
    assert len(_ziele()) == 1

    antwort = admin_client.delete(f"/api/push/devices/{zeile['id']}")
    assert antwort.status_code == 204, antwort.text
    assert _ziele() == []
    assert admin_client.get("/api/push/devices").json() == []


# --------------------------------------------------------------------------- #
# Die Probemeldung - und die Kryptografie rueckwaerts
# --------------------------------------------------------------------------- #


def test_die_probemeldung_laesst_sich_im_browser_lesen(admin_client: TestClient, postbote: Postbote) -> None:
    browser = Browser()
    _anmelden(admin_client, browser)

    antwort = admin_client.post("/api/push/test", json={})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json() == {"ok": True, "message": ""}

    url, inhalt, kopf = postbote.sendungen[-1]
    assert url == browser.endpoint
    # ⚠️ Vapid02, nicht Vapid01: Nur diese Kopfzeile gehoert zu aes128gcm.
    assert kopf["Authorization"].startswith("vapid t=")
    assert ",k=" in kopf["Authorization"]
    assert kopf["Content-Encoding"] == "aes128gcm"
    assert kopf["TTL"] == str(24 * 3600)
    assert kopf["Urgency"] == "normal"

    daten = browser.lesen(inhalt)
    assert daten["title"] == "Probemeldung"
    assert "kommt auf diesem Gerät alles an" in daten["body"]
    assert daten["code"] is None
    assert daten["tag"].startswith("test:")


def test_die_probemeldung_spricht_die_sprache_des_geraets(admin_client: TestClient, postbote: Postbote) -> None:
    browser = Browser()
    _anmelden(admin_client, browser, language="en")
    admin_client.post("/api/push/test", json={"endpoint": browser.endpoint})

    assert browser.lesen(postbote.sendungen[-1][1])["title"] == "Test message"


def test_die_probemeldung_ohne_geraet_ist_ein_fehler(admin_client: TestClient) -> None:
    antwort = admin_client.post("/api/push/test", json={})
    assert antwort.status_code == 400, antwort.text
    assert "push_no_device" in antwort.text


def test_der_absender_ist_nur_die_herkunft(admin_client: TestClient, postbote: Postbote) -> None:
    """⚠️ ``py_vapid`` prueft ``sub`` mit einer Regex, die am Hostnamen endet.

    ``https://filme.example/nexview`` fiele durch, und die Meldung ("Missing
    'sub' from claims") zeigte auf ein fehlendes Feld statt auf ein zu langes.
    """
    browser = Browser()
    _anmelden(admin_client, browser)

    with SessionLocal() as db:
        save_settings(db, {"public_url": "https://filme.example/nexview"})
    admin_client.post("/api/push/test", json={})
    anspruch = _anspruch(postbote.sendungen[-1][2])
    assert anspruch["sub"] == "https://filme.example"
    assert anspruch["aud"] == "https://push.example.com"

    # Ohne https-Adresse: keine Adresse eines Menschen - der Wert geht an
    # Google und Mozilla.
    with SessionLocal() as db:
        save_settings(db, {"public_url": "http://192.168.1.10:8000"})
    admin_client.post("/api/push/test", json={})
    assert _anspruch(postbote.sendungen[-1][2])["sub"] == "mailto:admin@localhost"


def test_404_und_410_raeumen_das_geraet_weg_403_nicht(admin_client: TestClient, postbote: Postbote) -> None:
    """⚠️ Nur "weg" ist weg. Ein 403 heisst "falsche Unterschrift" - unser
    Fehler, und wer den genauso behandelt, raeumt die Geraete aller weg."""
    browser = Browser()
    _anmelden(admin_client, browser)

    postbote.status = 410
    antwort = admin_client.post("/api/push/test", json={}).json()
    assert antwort["ok"] is False
    assert "410" in antwort["message"]
    assert _ziele() == []

    _anmelden(admin_client, browser)
    postbote.status = 403
    antwort = admin_client.post("/api/push/test", json={}).json()
    assert antwort["ok"] is False
    assert "Unterschrift" in antwort["message"]
    assert len(_ziele()) == 1


# --------------------------------------------------------------------------- #
# Der Postausgang
# --------------------------------------------------------------------------- #


def test_die_haken_folgen_der_mail() -> None:
    """Dieselben Haken wie bei der Mail, ohne den Monatsbericht - abgeleitet."""
    erwartet = {
        kind: "push_" + schalter.removeprefix("mail_")
        for kind, schalter in notify.MAIL_SWITCH.items()
        if schalter != "mail_cleanup"
    }
    assert notify.PUSH_SWITCH == erwartet
    assert erwartet, "Die Ableitung laeuft leer"

    spalten = {c.name for c in User.__table__.columns}
    assert set(notify.PUSH_SWITCH.values()) <= spalten
    assert set(webpush.HAKEN) == set(notify.PUSH_SWITCH.values())


def test_der_postausgang_bedient_nur_gesetzte_haken(admin_client: TestClient) -> None:
    _anmelden(admin_client, Browser())

    admin_client.patch("/api/auth/me", json={"push_download_complete": False})
    _meldung_anlegen(NotificationType.download_complete)
    assert _auftraege() == []

    admin_client.patch("/api/auth/me", json={"push_download_complete": True})
    _meldung_anlegen(NotificationType.download_complete)
    assert len(_auftraege()) == 1

    # Eine Meldungsart ohne Haken erreicht das Handy nie - wie bei der Mail.
    _meldung_anlegen(NotificationType.rating_outdated)
    assert len(_auftraege()) == 1


def test_der_rueckkanal_bleibt_ungefiltert(admin_client: TestClient) -> None:
    """Der Haken gilt fuer Browser. Das Home Assistant bekommt weiter alles."""
    _anmelden(admin_client, Browser())
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == ADMIN["username"]).one()
        db.add(
            ChannelTarget(
                channel=ChannelKind.webhook,
                name="Home Assistant",
                url="http://192.168.1.50:8123/api/webhook/nexview",
                user_id=admin.id,
                verified=True,
                enabled=True,
                language="de",
            )
        )
        db.commit()

    admin_client.patch("/api/auth/me", json={"push_download_complete": False})
    _meldung_anlegen(NotificationType.download_complete)

    assert len(_auftraege(ChannelKind.webhook)) == 1
    assert _auftraege(ChannelKind.webpush) == []


def test_ein_gesperrtes_konto_bekommt_nichts(admin_client: TestClient) -> None:
    create_user(admin_client, "zweiter")
    kopf = auth_headers(admin_client, "zweiter", "passwort-1234")
    _anmelden(admin_client, Browser(), **kopf)

    with SessionLocal() as db:
        person = db.query(User).filter(User.username == "zweiter").one()
        person.is_active = False
        db.commit()
    _meldung_anlegen(NotificationType.download_complete, username="zweiter")

    assert _auftraege() == []


def test_der_postausgang_verschickt_und_raeumt_erloschene_geraete_weg(
    admin_client: TestClient, postbote: Postbote
) -> None:
    admin_client.get("/api/push/key")
    browser = Browser()
    _anmelden(admin_client, browser)

    _meldung_anlegen(NotificationType.download_complete)
    assert _verschicken() == 1

    daten = browser.lesen(postbote.sendungen[-1][1])
    persoenlich = channel_outbox.PERSOENLICH["de"][NotificationType.download_complete]
    assert daten["title"] == persoenlich["title"]
    assert "Der Film" in daten["body"]
    assert daten["tag"] == "download_complete:Der Film"

    geraete = admin_client.get("/api/push/devices").json()
    assert geraete[0]["last_success"] is not None
    assert geraete[0]["last_error"] is None

    # Der Browser hat sein Abonnement weggeworfen: Der Push-Dienst sagt 410.
    # Kein Fehlschlag, sondern der vorgesehene Weg - das Geraet geht weg,
    # und mit ihm seine Auftraege, ohne dass der Abschluss stolpert.
    postbote.status = 410
    _meldung_anlegen(NotificationType.download_complete)
    _meldung_anlegen(NotificationType.download_complete)
    assert _verschicken() == 0
    assert _ziele() == []
    assert _auftraege() == []


# --------------------------------------------------------------------------- #
# Was der Betreiber sieht - und was nicht
# --------------------------------------------------------------------------- #


def test_der_betreiber_sieht_den_dienst_und_nie_die_adresse(admin_client: TestClient) -> None:
    _anmelden(admin_client, Browser())

    liste = admin_client.get("/api/settings/channels/rueckkanaele").json()
    assert len(liste) == 1
    zeile = liste[0]
    assert zeile["kanal"] == "webpush"
    assert zeile["name"] == "Chrome, Windows"
    assert zeile["schluessel"] is None
    assert zeile["bestaetigt"] is True
    # Die Adresse ist ein Schluessel zum Geraet - nur der Dienst wird genannt.
    assert zeile["url"] == "push.example.com"
    assert "/abo/" not in json.dumps(liste)


def test_die_kanalverwaltung_kennt_web_push_nicht(admin_client: TestClient) -> None:
    """Ein Web-Push-Ziel richtet der Browser ein, nie der Betreiber.

    Der Riegel ist der Pfadparameter selbst: Er zaehlt die Arten einzeln auf,
    und ``webpush`` steht nicht darin. Faellt das je weg, entstuende hier eine
    Adresse ohne Besitzer, deren Schluessel niemand hat.
    """
    antwort = admin_client.get("/api/settings/channels/webpush/targets")
    assert antwort.status_code == 422, antwort.text
    assert "webpush" not in antwort.json()["detail"][0]["ctx"]["expected"]
