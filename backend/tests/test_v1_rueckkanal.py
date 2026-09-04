"""Der persoenliche Rueckkanal: ``/api/v1/me/push``.

Eine Anbindung meldet an, wohin Nexview *sie* benachrichtigen soll. Das ist die
einzige Stelle, an der ein Benachrichtigungsziel entsteht, ohne dass ein
Administrator es eintraegt - und deshalb die Stelle, an der am meisten
schiefgehen kann.

Geprueft wird entlang der vier Dinge, die dabei stimmen muessen:

1. Ein Ziel entsteht erst, wenn jemand den Code aus der Testnachricht
   zurueckgibt. Vorher ist es eine Adresse, von der niemand weiss, ob dort
   ueberhaupt jemand sitzt.
2. Es bekommt **nur**, was seinen Besitzer angeht. Bekaeme es alles, waere es
   ein Weg, den Hausfunk mitzulesen.
3. Es stirbt mit seinem Schluessel und schweigt bei einem gesperrten Konto.
4. Die Ausnahme von der Nur-Lese-Regel bleibt eine Ausnahme. Der letzte Test
   ist die Bodenschwelle dazu: Er faehrt jeden anderen schreibenden Pfad ab
   und besteht nur, wenn er dabei wirklich welche gesehen hat.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    ApiKey,
    ChannelKind,
    ChannelMessage,
    ChannelTarget,
    NotificationType,
    Role,
    User,
)
from app.security import hash_password
from app.services import channel_outbox, channels, notify
from app.services.settings_service import load_settings

ZIEL = "http://192.168.1.50:8123/api/webhook/nexview-abc"


@pytest.fixture
def versand(monkeypatch: pytest.MonkeyPatch) -> list[channels.Notice]:
    """Statt zu verschicken: einsammeln.

    Die Testnachricht traegt den Bestaetigungscode in einem eigenen Feld -
    genau dafuer ist es da, und hier wird es zum ersten Mal wirklich benutzt.
    """
    gesammelt: list[channels.Notice] = []

    async def merken(kind, config, notice):  # noqa: ANN001, ANN202
        gesammelt.append(notice)

    monkeypatch.setattr("app.routers.v1.channels.send", merken)
    return gesammelt


def _schluessel(client: TestClient, *, nur_lesen: bool = False) -> str:
    """Einen Schluessel fuer den angemeldeten Admin anlegen und einsetzen."""
    antwort = client.post(
        "/api/auth/me/schluessel",
        json={"name": "Home Assistant", "nur_lesen": nur_lesen},
    )
    assert antwort.status_code in (200, 201), antwort.text
    return antwort.json()["schluessel"]


def _mit(client: TestClient, token: str) -> TestClient:
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def _angemeldet(client: TestClient, versand: list, *, nur_lesen: bool = False) -> str:
    """Den vollen Weg gehen: anmelden, Code lesen, bestaetigen."""
    token = _schluessel(client, nur_lesen=nur_lesen)
    _mit(client, token)

    antwort = client.put("/api/v1/me/push", json={"url": ZIEL, "language": "de"})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["ok"] is True

    code = versand[-1].code
    assert code, "Die Testnachricht traegt keinen Code in ihrem eigenen Feld."
    bestaetigt = client.post("/api/v1/me/push", json={"code": code})
    assert bestaetigt.status_code == 200, bestaetigt.text
    assert bestaetigt.json()["ok"] is True, bestaetigt.text
    return token


class TestDerRueckkanalEntsteht:
    def test_anmelden_schickt_eine_testnachricht_mit_code(
        self, admin_client: TestClient, versand: list
    ) -> None:
        _mit(admin_client, _schluessel(admin_client))

        antwort = admin_client.put("/api/v1/me/push", json={"url": ZIEL, "language": "de"})

        assert antwort.status_code == 200, antwort.text
        assert len(versand) == 1
        assert versand[0].code and len(versand[0].code) == 4
        # ⚠️ Der Code steht auch im Titel. Fuer einen Menschen, der zufaellig
        # mitliest, ist das der Ort - die Anbindung nimmt das Feld.
        assert versand[0].code in versand[0].title

    def test_ohne_code_bleibt_es_stumm(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """⚠️ Der Punkt der Bestaetigung.

        Eine Adresse, deren Code niemand zurueckgegeben hat, ist eine Adresse,
        von der niemand weiss, ob dort jemand sitzt. HTTP 200 vom Empfaenger
        heisst nur "angenommen".
        """
        _mit(admin_client, _schluessel(admin_client))
        admin_client.put("/api/v1/me/push", json={"url": ZIEL, "language": "de"})

        stand = admin_client.get("/api/v1/me/push").json()
        assert stand["eingerichtet"] is True
        assert stand["bestaetigt"] is False

        with SessionLocal() as db:
            admin = db.query(User).filter(User.username == "admin").one()
            assert channel_outbox.ziel_von(db, admin.id) is None

    def test_mit_code_geht_es_los(self, admin_client: TestClient, versand: list) -> None:
        _angemeldet(admin_client, versand)

        stand = admin_client.get("/api/v1/me/push").json()
        assert stand["bestaetigt"] is True
        assert stand["url"] == ZIEL
        assert stand["language"] == "de"

    def test_zweimal_anmelden_legt_kein_zweites_an(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """Wer die Integration neu einrichtet, soll keine Leiche hinterlassen."""
        _angemeldet(admin_client, versand)
        zweite = "http://192.168.1.77:8123/api/webhook/neu"
        admin_client.put("/api/v1/me/push", json={"url": zweite, "language": "en"})

        with SessionLocal() as db:
            ziele = db.query(ChannelTarget).filter(ChannelTarget.user_id.isnot(None)).all()
            assert len(ziele) == 1
            assert ziele[0].url == zweite

    def test_eine_neue_adresse_muss_neu_bestaetigt_werden(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """⚠️ Sonst liesse sich ein bestaetigtes Ziel umbiegen.

        Bliebe der Haken stehen, koennte man die Adresse auf einen fremden
        Empfaenger aendern, ohne dass dort je jemand einen Code gelesen haette.
        """
        _angemeldet(admin_client, versand)
        admin_client.put(
            "/api/v1/me/push", json={"url": "http://10.0.0.9:8123/x", "language": "de"}
        )

        assert admin_client.get("/api/v1/me/push").json()["bestaetigt"] is False

    def test_die_selbstauskunft_der_umgebung_ist_gesperrt(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """169.254.169.254 - dahinter sitzt kein Empfaenger, sondern ein Anbieter."""
        _mit(admin_client, _schluessel(admin_client))

        antwort = admin_client.put(
            "/api/v1/me/push", json={"url": "http://169.254.169.254/latest/meta-data"}
        )

        assert antwort.status_code == 422, antwort.text
        assert versand == []

    def test_das_heimnetz_bleibt_erlaubt(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """⚠️ Die Gegenprobe zur Sperre oben.

        Private Adressen zu verbieten waere die naheliegende Haerte - und
        wuerde genau den Fall aussperren, fuer den das hier gebaut ist. Ein
        Home Assistant steht im Heimnetz.
        """
        _mit(admin_client, _schluessel(admin_client))

        antwort = admin_client.put("/api/v1/me/push", json={"url": ZIEL})

        assert antwort.status_code == 200, antwort.text

    def test_eine_sitzung_ohne_schluessel_bekommt_kein_ziel(
        self, admin_client: TestClient
    ) -> None:
        """Ein Ziel ohne Schluessel haette keine Sollbruchstelle."""
        antwort = admin_client.put("/api/v1/me/push", json={"url": ZIEL})

        assert antwort.status_code == 403, antwort.text


class TestErBekommtNurSeines:
    def _zwei_leute(self) -> tuple[int, int]:
        with SessionLocal() as db:
            admin = db.query(User).filter(User.username == "admin").one()
            fremder = User(
                username="jemand",
                password_hash=hash_password("passwort-123456"),
                role=Role.user,
                is_active=True,
            )
            db.add(fremder)
            db.commit()
            return admin.id, fremder.id

    def test_meine_meldung_landet_bei_mir(
        self, admin_client: TestClient, versand: list
    ) -> None:
        _angemeldet(admin_client, versand)
        ich, _ = self._zwei_leute()

        with SessionLocal() as db:
            mensch = db.get(User, ich)
            notify.create(
                db,
                user=mensch,
                kind=NotificationType.download_complete,
                message_key="download_complete",
                title="The Dark Knight",
            )
            db.commit()

            offen = db.query(ChannelMessage).all()
            assert len(offen) == 1
            assert offen[0].type == NotificationType.download_complete

    def test_die_meldung_eines_anderen_nicht(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """⚠️ Der eigentliche Zweck der ganzen Uebung.

        Haenge der Rueckkanal am Hausfunk, bekaeme er jede Anfrage im Haus.
        Hier bekommt er nur, was in der Glocke seines Besitzers steht.
        """
        _angemeldet(admin_client, versand)
        _, fremder = self._zwei_leute()

        with SessionLocal() as db:
            mensch = db.get(User, fremder)
            notify.create(
                db,
                user=mensch,
                kind=NotificationType.download_complete,
                message_key="download_complete",
                title="Ein fremder Titel",
            )
            db.commit()

            assert db.query(ChannelMessage).count() == 0

    def test_der_hausfunk_bleibt_unberuehrt(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """Ein Ziel ohne Besitzer verhaelt sich wie eh und je."""
        _angemeldet(admin_client, versand)

        with SessionLocal() as db:
            haus = ChannelTarget(
                channel=ChannelKind.gotify,
                name="Familie",
                url="http://gotify.test",
                verified=True,
                events={"download_complete": "normal"},
            )
            db.add(haus)
            db.commit()
            haus_id = haus.id

            admin = db.query(User).filter(User.username == "admin").one()
            notify.create(
                db,
                user=admin,
                kind=NotificationType.download_complete,
                message_key="download_complete",
                title="The Dark Knight",
            )
            db.commit()

            # ⚠️ Nachrichten zaehlen, nicht Ziele. Eine Menge von Kennungen
            # bliebe auch dann zweielementig, wenn der Hausfunk zusaetzlich das
            # persoenliche Ziel bediente - und genau das ist der Fehler, den
            # dieser Test finden soll.
            nachrichten = db.query(ChannelMessage).all()
            assert len(nachrichten) == 2, (
                "Genau zwei: eine Hausdurchsage und eine persoenliche. Mehr "
                "hiesse, dass ein Weg den anderen mitbedient."
            )
            ziele = {m.target_id for m in nachrichten}
            assert haus_id in ziele, "Die Hausdurchsage fehlt."
            assert len(ziele) == 2, "Haus und Person, je einmal."

    def test_der_text_ist_die_persoenliche_fassung(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """⚠️ "Dein Titel" statt "Ein Titel".

        Derselbe Vorgang, zwei Blickwinkel. Wer beide Tabellen zusammenlegt,
        schreibt entweder eine Durchsage, die zu viel verraet, oder eine
        persoenliche Meldung, die niemanden meint.
        """
        _angemeldet(admin_client, versand)

        with SessionLocal() as db:
            admin = db.query(User).filter(User.username == "admin").one()
            notify.create(
                db,
                user=admin,
                kind=NotificationType.storage_released,
                message_key="storage_released",
                title="The Dark Knight",
            )
            db.commit()

            eintrag = db.query(ChannelMessage).one()
            ziel = db.get(ChannelTarget, eintrag.target_id)
            nachricht = channel_outbox._notice(db, eintrag, ziel, load_settings(db))

        assert nachricht is not None
        assert nachricht.title == "Dein Speicher ist wieder frei"
        assert channel_outbox.TEXTS["de"][NotificationType.storage_released]["title"] != (
            nachricht.title
        ), "Haus und Person sagen hier absichtlich nicht dasselbe."

    def test_er_bekommt_auch_was_keinen_haken_hat(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """Antwort auf mein Ticket kennt der Hausfunk gar nicht."""
        _angemeldet(admin_client, versand)

        assert NotificationType.ticket_reply not in channel_outbox.EVENTS

        with SessionLocal() as db:
            admin = db.query(User).filter(User.username == "admin").one()
            notify.create(
                db,
                user=admin,
                kind=NotificationType.ticket_reply,
                message_key="ticket_reply",
                title="Mein Anliegen",
            )
            db.commit()

            eintrag = db.query(ChannelMessage).one()
            ziel = db.get(ChannelTarget, eintrag.target_id)
            nachricht = channel_outbox._notice(db, eintrag, ziel, load_settings(db))

        assert nachricht is not None
        assert nachricht.title == "Antwort auf dein Ticket"


class TestErStirbtMitDemSchluessel:
    def test_schluessel_widerrufen_raeumt_das_ziel_ab(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """⚠️ Sonst funkt Nexview an ein Home Assistant, dessen Zugang gesperrt ist."""
        _angemeldet(admin_client, versand)

        with SessionLocal() as db:
            assert db.query(ChannelTarget).count() == 1
            schluessel = db.query(ApiKey).one()
            kennung = schluessel.id

        antwort = admin_client.delete(f"/api/auth/me/schluessel/{kennung}")
        assert antwort.status_code in (200, 204), antwort.text

        with SessionLocal() as db:
            assert db.query(ChannelTarget).count() == 0, (
                "Das Ziel hat seinen Schluessel ueberlebt."
            )

    def test_ein_gesperrtes_konto_bekommt_nichts_mehr(
        self, admin_client: TestClient, versand: list
    ) -> None:
        _angemeldet(admin_client, versand)

        with SessionLocal() as db:
            admin = db.query(User).filter(User.username == "admin").one()
            admin.is_active = False
            db.commit()

            assert channel_outbox.ziel_von(db, admin.id) is None

    def test_trennen_raeumt_auf(self, admin_client: TestClient, versand: list) -> None:
        _angemeldet(admin_client, versand)

        antwort = admin_client.delete("/api/v1/me/push")

        assert antwort.status_code == 204
        with SessionLocal() as db:
            assert db.query(ChannelTarget).count() == 0


class TestDieNurLeseAusnahme:
    def test_ein_nur_lese_schluessel_darf_sein_eigenes_ziel_anmelden(
        self, admin_client: TestClient, versand: list
    ) -> None:
        """⚠️ Die eine Ausnahme, und sie ist der sicherere Weg.

        Ohne sie muesste jeder, der Ereignisse will, den Haken abwaehlen - und
        haette dann einen Schluessel, der anfragen und entscheiden darf.
        """
        _angemeldet(admin_client, versand, nur_lesen=True)

        assert admin_client.get("/api/v1/me/push").json()["bestaetigt"] is True

    def test_sonst_darf_er_weiterhin_nichts(self, admin_client: TestClient) -> None:
        """Die Bodenschwelle unter der Ausnahme.

        ⚠️ **Ein Test, der nichts findet, besteht auch.** Deshalb zaehlt dieser
        mit, wie viele schreibende Adressen er wirklich abgefahren hat, und
        faellt um, wenn es zu wenige waren. Ohne den Zaehler wuerde eine
        Aenderung an der Wegefindung diesen Test lautlos aushoehlen.
        """
        from app.main import app
        from app.services.api_schluessel import SCHREIBT_NUR_FUER_SICH

        assert SCHREIBT_NUR_FUER_SICH == {"/api/v1/me/push"}, (
            "Die Ausnahmeliste ist gewachsen. Bitte die Begruendung an "
            "SCHREIBT_NUR_FUER_SICH noch einmal lesen, dann diesen Test anpassen."
        )

        token = _schluessel(admin_client, nur_lesen=True)

        geprueft = 0
        for route in app.routes:
            pfad = getattr(route, "path", "")
            methoden = getattr(route, "methods", set()) - {"GET", "HEAD", "OPTIONS"}
            if not methoden or "{" in pfad or pfad in SCHREIBT_NUR_FUER_SICH:
                continue
            if not pfad.startswith("/api/"):
                continue
            methode = min(methoden)

            # Erst ohne alles: Was auch unangemeldet durchgeht, ist keine
            # Wache, die ein Schluessel passieren koennte - die Erst-
            # Einrichtung etwa. Solche Pfade zaehlen hier nicht mit, sonst
            # bewiese der Zaehler unten etwas anderes als er behauptet.
            admin_client.headers.pop("Authorization", None)
            offen = admin_client.request(methode, pfad, json={})
            if offen.status_code not in (401, 403):
                continue

            _mit(admin_client, token)
            antwort = admin_client.request(methode, pfad, json={})
            assert antwort.status_code == 403, (
                f"{methode} {pfad} kam an einem Nur-Lese-Schluessel vorbei "
                f"(HTTP {antwort.status_code})."
            )
            geprueft += 1

        assert geprueft > 30, (
            f"Nur {geprueft} schreibende Adressen geprueft - das ist zu wenig, "
            "um etwas zu beweisen. Hat sich die Wegefindung geaendert?"
        )
