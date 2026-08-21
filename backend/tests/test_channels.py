"""Systembenachrichtigungen: die serverseitigen Ziele (ntfy, Gotify).

Serverseitig heisst: vom Administrator eingerichtet, ein geteiltes Postfach fuer
die ganze Installation, kein Bezug zu einem einzelnen Benutzer. Die
persoenlichen Wege (Glocke, E-Mail) haben damit nichts zu tun und werden hier
auch nicht angefasst.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app.crypto import encrypt
from app.db import SessionLocal
from app.models import (
    ChannelKind,
    ChannelMessage,
    ChannelTarget,
    MediaType,
    NotificationType,
    QualityTier,
    Role,
    User,
)
from app.security import hash_password
from app.services import channel_outbox, notify
from app.services.channels import ChannelError, Notice, gotify, ntfy
from app.services.settings_service import load_settings

GOTIFY_ENTWURF = {
    "name": "Handy",
    "url": "http://gotify.test",
    "token": "geheim",
    "language": "de",
}


def _ziel(
    *,
    kanal: ChannelKind = ChannelKind.gotify,
    name: str = "Handy",
    events: dict | None = None,
    verified: bool = True,
    **felder: str,
) -> int:
    """Ein fertig eingerichtetes Postfach direkt in die Datenbank legen.

    Bei ntfy entstehen dabei **zwei** Zeilen: die Instanz mit Adresse und
    Anmeldung, darunter das Topic. Geliefert wird die Kennung des Postfachs -
    also bei ntfy die des Topics, denn dorthin gehen die Nachrichten.
    """
    meldungen = events if events is not None else {"request_pending": "high"}

    with SessionLocal() as db:
        if kanal == ChannelKind.ntfy:
            instanz = ChannelTarget(
                channel=kanal,
                name=f"{name}-Instanz",
                url=felder.pop("url", "http://ntfy.test"),
                auth=felder.pop("auth", "none"),
                username=felder.pop("username", ""),
                password=felder.pop("password", ""),
                token=felder.pop("token", ""),
            )
            db.add(instanz)
            db.flush()
            topic = ChannelTarget(
                channel=kanal,
                parent_id=instanz.id,
                name=name,
                verified=verified,
                events=meldungen,
                topic=felder.pop("topic", "nexview"),
                language=felder.pop("language", "de"),
            )
            db.add(topic)
            db.commit()
            return topic.id

        werte = {"url": "http://gotify.test", "token": encrypt("geheim"), "language": "de"}
        werte.update(felder)
        target = ChannelTarget(
            channel=kanal, name=name, verified=verified, events=meldungen, **werte
        )
        db.add(target)
        db.commit()
        return target.id


def _anfrage(db, besitzer: User):
    from app.models import MediaRequest

    request = MediaRequest(
        user_id=besitzer.id,
        media_type=MediaType.movie,
        tier=QualityTier.standard,
        tmdb_id=155,
        title="The Dark Knight",
        poster_path="https://image.tmdb.org/t/p/w500/poster.jpg",
    )
    db.add(request)
    db.flush()
    return request


def _benutzer(db, name: str, rolle: Role) -> User:
    user = User(
        username=name,
        password_hash=hash_password("passwort-123456"),
        role=rolle,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _freigabe_anfragen(db) -> None:
    """Ein Benutzer fragt an, ein Admin muss freigeben."""
    anfragender = _benutzer(db, "nutzer", Role.user)
    _benutzer(db, "chef", Role.admin)
    request = _anfrage(db, anfragender)
    notify.create_for_approvers(
        db,
        kind=NotificationType.request_pending,
        message_key="notifications.requestPending",
        request=request,
        ausser=anfragender.id,
    )
    db.commit()


# --- Vormerken ------------------------------------------------------------


def test_ein_ereignis_erzeugt_je_ziel_genau_eine_zeile() -> None:
    """Drei Entscheider, eine Anfrage - trotzdem nur eine Nachricht je Ziel.

    Das ist der Kern der Trennung: die Glocke gehoert einem Benutzer, ein
    serverseitiges Ziel einem Ereignis. Haengte es an den Glocken-Meldungen,
    stuende dieselbe Anfrage dreimal im selben Postfach.
    """
    _ziel(name="Admin-Postfach")
    _ziel(kanal=ChannelKind.ntfy, name="Familie")

    with SessionLocal() as db:
        anfragender = _benutzer(db, "nutzer", Role.user)
        for name in ("chef", "vertretung", "dritter"):
            _benutzer(db, name, Role.admin)
        request = _anfrage(db, anfragender)
        notify.create_for_approvers(
            db,
            kind=NotificationType.request_pending,
            message_key="notifications.requestPending",
            request=request,
            ausser=anfragender.id,
        )
        db.commit()

        zeilen = db.query(ChannelMessage).all()
        assert len(zeilen) == 2
        assert {zeile.channel for zeile in zeilen} == {ChannelKind.ntfy, ChannelKind.gotify}
        assert all(zeile.title == "The Dark Knight" for zeile in zeilen)


def test_mehrere_ziele_desselben_dienstes_bekommen_alle_etwas() -> None:
    """Genau dafuer gibt es die Kacheln: zwei Gotify-Postfaecher, zwei Meldungen."""
    _ziel(name="Entscheider")
    _ziel(name="Betreiber", url="http://gotify-zwei.test")

    with SessionLocal() as db:
        _freigabe_anfragen(db)
        zeilen = db.query(ChannelMessage).all()
        assert len(zeilen) == 2
        assert len({zeile.target_id for zeile in zeilen}) == 2


def test_unbestaetigtes_ziel_bekommt_nichts() -> None:
    """Ein Haken auf einem ungepruefen Ziel darf nichts ausloesen."""
    _ziel(verified=False)
    with SessionLocal() as db:
        _freigabe_anfragen(db)
        assert db.query(ChannelMessage).count() == 0


def test_ohne_angehakte_meldung_bekommt_niemand_etwas() -> None:
    _ziel(events={})
    with SessionLocal() as db:
        _freigabe_anfragen(db)
        assert db.query(ChannelMessage).count() == 0


def test_meldung_ausserhalb_der_liste_bleibt_draussen() -> None:
    """Was ein serverseitiges Ziel nicht berichten kann, loest auch nichts aus."""
    _ziel(events={"request_pending": "high"})
    with SessionLocal() as db:
        empfaenger = _benutzer(db, "nutzer", Role.user)
        request = _anfrage(db, empfaenger)
        notify.create(
            db,
            user=empfaenger,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()
        assert db.query(ChannelMessage).count() == 0


# --- Versand --------------------------------------------------------------


def _ntfy_config(**extra) -> ntfy.NtfyConfig:
    werte = {
        "url": "http://ntfy.test",
        "topic": "nexview",
        "auth": "none",
        "username": "",
        "password": "",
        "token": "",
        "language": "de",
    }
    werte.update(extra)
    return ntfy.NtfyConfig(**werte)


@pytest.mark.asyncio
async def test_ntfy_schickt_topic_dringlichkeit_und_poster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(200))[1])

    await ntfy.send(
        _ntfy_config(),
        Notice(
            title="Neue Freigabeanfrage",
            body="**Film**",
            level="high",
            poster_url="https://bild.test/poster.jpg",
            click_url="https://nexview.test/admin/requests",
        ),
    )

    daten = json.loads(gesehen[0].content)
    assert str(gesehen[0].url) == "http://ntfy.test"
    assert daten["topic"] == "nexview"
    assert daten["priority"] == ntfy.PRIORITIES["high"]
    assert daten["markdown"] is True
    assert daten["attach"] == "https://bild.test/poster.jpg"
    assert daten["click"] == "https://nexview.test/admin/requests"


@pytest.mark.asyncio
@pytest.mark.parametrize("stufe", ["low", "normal", "high", "urgent"])
async def test_ntfy_bleibt_in_seiner_spanne(monkeypatch: pytest.MonkeyPatch, stufe: str) -> None:
    """ntfy kennt nur 1 bis 5 - alles andere weist es mit HTTP 400 ab."""
    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(200))[1])

    await ntfy.send(_ntfy_config(), Notice(title="t", body="b", level=stufe))
    assert 1 <= json.loads(gesehen[0].content)["priority"] <= 5


@pytest.mark.asyncio
@pytest.mark.parametrize("stufe", ["low", "normal", "high", "urgent"])
async def test_gotify_bleibt_in_seiner_spanne(monkeypatch: pytest.MonkeyPatch, stufe: str) -> None:
    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(200))[1])

    config = gotify.GotifyConfig(url="http://gotify.test", token="geheim", language="de")
    await gotify.send(config, Notice(title="t", body="b", level=stufe))
    assert 0 <= json.loads(gesehen[0].content)["priority"] <= 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth", "erwartet"),
    [
        ("none", None),
        ("basic", "Basic bmFtZTpnZWhlaW0="),  # name:geheim
        ("token", "Bearer tk_123"),
    ],
)
async def test_ntfy_anmeldeverfahren(
    monkeypatch: pytest.MonkeyPatch, auth: str, erwartet: str | None
) -> None:
    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(200))[1])

    await ntfy.send(
        _ntfy_config(auth=auth, username="name", password="geheim", token="tk_123"),
        Notice(title="t", body="b"),
    )
    assert gesehen[0].headers.get("authorization") == erwartet


@pytest.mark.asyncio
async def test_gotify_haengt_token_an_und_setzt_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(200))[1])

    config = gotify.GotifyConfig(url="http://gotify.test", token="geheim", language="de")
    await gotify.send(
        config,
        Notice(title="t", body="b", level="high", poster_url="https://bild.test/p.jpg"),
    )

    daten = json.loads(gesehen[0].content)
    assert str(gesehen[0].url) == "http://gotify.test/message?token=geheim"
    assert daten["priority"] == gotify.PRIORITIES["high"]
    assert daten["extras"]["client::display"]["contentType"] == "text/markdown"
    assert daten["extras"]["client::notification"]["bigImageUrl"] == "https://bild.test/p.jpg"


@pytest.mark.asyncio
async def test_fehlerhafte_adresse_wird_gar_nicht_erst_versucht() -> None:
    config = gotify.GotifyConfig(url="ftp://gotify.test", token="geheim", language="de")
    with pytest.raises(ChannelError):
        await gotify.send(config, Notice(title="t", body="b"))


# --- Postausgang ----------------------------------------------------------


@pytest.mark.asyncio
async def test_totes_ziel_gibt_nach_drei_versuchen_auf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Und der letzte Fehler bleibt stehen - sonst faellt der Ausfall niemandem auf."""
    _mit_attrappe(monkeypatch, lambda _request: httpx.Response(401, json={"error": "nope"}))
    ziel_id = _ziel()

    with SessionLocal() as db:
        _freigabe_anfragen(db)
        settings = load_settings(db)
        for erwartet in (1, 2, 3):
            await channel_outbox.process(db, settings)
            zeile = db.query(ChannelMessage).one()
            db.refresh(zeile)
            assert zeile.attempts == erwartet
            assert zeile.sent_at is None

        # Vierter Durchgang: nichts mehr zu tun, der Auftrag ist abgehakt.
        assert await channel_outbox.process(db, settings) == 0

        zeile = db.query(ChannelMessage).one()
        assert "401" in (zeile.last_error or "")
        assert channel_outbox.last_failure(db, db.get(ChannelTarget, ziel_id)) is not None


@pytest.mark.asyncio
async def test_erfolgreicher_versand_wird_vermerkt(monkeypatch: pytest.MonkeyPatch) -> None:
    _mit_attrappe(monkeypatch, lambda _request: httpx.Response(200))
    _ziel()

    with SessionLocal() as db:
        _freigabe_anfragen(db)
        assert await channel_outbox.process(db, load_settings(db)) == 1
        zeile = db.query(ChannelMessage).one()
        assert zeile.sent_at is not None
        assert zeile.last_error is None
        # Und nichts geht ein zweites Mal raus.
        assert await channel_outbox.process(db, load_settings(db)) == 0


@pytest.mark.asyncio
async def test_dringlichkeit_kommt_vom_ziel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sie gehoert zur Meldung, nicht zum Dienst - und ist je Ziel einstellbar."""
    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(200))[1])
    _ziel(kanal=ChannelKind.ntfy, events={"request_pending": "low"})

    with SessionLocal() as db:
        _freigabe_anfragen(db)
        await channel_outbox.process(db, load_settings(db))

    assert json.loads(gesehen[0].content)["priority"] == ntfy.PRIORITIES["low"]


@pytest.mark.asyncio
async def test_geloeschtes_ziel_nimmt_offene_auftraege_mit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Auftrag ohne Ziel haette kein Gegenueber mehr."""
    _mit_attrappe(monkeypatch, lambda _request: httpx.Response(200))
    ziel_id = _ziel()

    with SessionLocal() as db:
        _freigabe_anfragen(db)
        assert db.query(ChannelMessage).count() == 1
        db.delete(db.get(ChannelTarget, ziel_id))
        db.commit()
        assert db.query(ChannelMessage).count() == 0


# --- Ablauf: testen, bestaetigen, speichern -------------------------------


def _code_aus(gesendet: list[dict]) -> str:
    """Den vierstelligen Code aus der zuletzt verschickten Nachricht klauben."""
    treffer = re.search(r"\d{4}", gesendet[-1].get("title", ""))
    assert treffer, f"kein Code im Titel: {gesendet[-1]!r}"
    return treffer.group()


def _mitschnitt(monkeypatch: pytest.MonkeyPatch, antwort: int = 200) -> list[dict]:
    gesendet: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesendet.append(json.loads(request.content))
        return httpx.Response(antwort)

    _mit_attrappe(monkeypatch, handler)
    return gesendet


def _einrichten(
    admin_client: TestClient, gesendet: list[dict], entwurf: dict | None = None
) -> dict:
    """Den ganzen Ablauf durchlaufen und das angelegte Ziel liefern."""
    daten = entwurf or GOTIFY_ENTWURF
    assert admin_client.post("/api/settings/channels/gotify/test", json=daten).json()["ok"]
    assert admin_client.post(
        "/api/settings/channels/gotify/confirm", json={"code": _code_aus(gesendet)}
    ).json()["ok"]
    antwort = admin_client.post("/api/settings/channels/gotify/targets", json=daten)
    assert antwort.status_code == 201, antwort.text
    return antwort.json()


def test_ablauf_testen_bestaetigen_speichern(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der ganze Weg - und unterwegs entsteht nichts, was nicht bestaetigt ist."""
    gesendet = _mitschnitt(monkeypatch)

    # 1. Getestet werden die eingetippten, noch nicht gespeicherten Werte.
    antwort = admin_client.post("/api/settings/channels/gotify/test", json=GOTIFY_ENTWURF)
    assert antwort.json()["ok"] is True, antwort.text
    assert admin_client.get("/api/settings/channels/gotify/targets").json() == []

    code = _code_aus(gesendet)

    # 2. Ein falscher Code bringt nichts.
    falsch = "0000" if code != "0000" else "1111"
    assert (
        admin_client.post(
            "/api/settings/channels/gotify/confirm", json={"code": falsch}
        ).json()["ok"]
        is False
    )

    # 3. Der richtige schon.
    assert (
        admin_client.post(
            "/api/settings/channels/gotify/confirm", json={"code": code}
        ).json()["ok"]
        is True
    )

    # 4. Erst das Speichern legt das Ziel an - bestaetigt und ohne Meldungen.
    antwort = admin_client.post("/api/settings/channels/gotify/targets", json=GOTIFY_ENTWURF)
    assert antwort.status_code == 201, antwort.text
    ziel = antwort.json()
    assert ziel["name"] == "Handy"
    assert ziel["verified"] is True
    assert ziel["events"] == {}
    assert "geheim" not in ziel["token"]
    assert ziel["token_set"] is True


def test_speichern_ohne_bestaetigten_code_wird_abgelehnt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst waere der ganze Ablauf eine Empfehlung statt einer Regel."""
    _mitschnitt(monkeypatch)
    admin_client.post("/api/settings/channels/gotify/test", json=GOTIFY_ENTWURF)
    # Kein confirm - direkt speichern.
    antwort = admin_client.post("/api/settings/channels/gotify/targets", json=GOTIFY_ENTWURF)
    assert antwort.status_code == 409
    assert admin_client.get("/api/settings/channels/gotify/targets").json() == []


def test_andere_werte_speichern_als_getestet_wird_abgelehnt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Getestet wurde eine Adresse, gespeichert eine andere - das zaehlt nicht."""
    gesendet = _mitschnitt(monkeypatch)
    admin_client.post("/api/settings/channels/gotify/test", json=GOTIFY_ENTWURF)
    admin_client.post(
        "/api/settings/channels/gotify/confirm", json={"code": _code_aus(gesendet)}
    )

    antwort = admin_client.post(
        "/api/settings/channels/gotify/targets",
        json={**GOTIFY_ENTWURF, "url": "http://ganz-woanders.test"},
    )
    assert antwort.status_code == 409


def test_mehrere_ziele_lassen_sich_anlegen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesendet = _mitschnitt(monkeypatch)
    _einrichten(admin_client, gesendet)
    _einrichten(
        admin_client,
        gesendet,
        {**GOTIFY_ENTWURF, "name": "Zweites", "url": "http://gotify-zwei.test"},
    )

    ziele = admin_client.get("/api/settings/channels/gotify/targets").json()
    assert [ziel["name"] for ziel in ziele] == ["Handy", "Zweites"]


def test_meldungen_lassen_sich_erst_nach_der_bestaetigung_setzen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesendet = _mitschnitt(monkeypatch)
    ziel = _einrichten(admin_client, gesendet)

    antwort = admin_client.put(
        f"/api/settings/channels/gotify/targets/{ziel['id']}/events",
        json={"events": {"request_pending": "urgent"}},
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["events"] == {"request_pending": "urgent"}

    # Und ein unbestaetigtes Ziel nimmt keine Haken an.
    unbestaetigt = _ziel(name="Roh", verified=False, events={})
    gesperrt = admin_client.put(
        f"/api/settings/channels/gotify/targets/{unbestaetigt}/events",
        json={"events": {"request_pending": "high"}},
    )
    assert gesperrt.status_code == 409


def test_unbekannte_meldung_oder_stufe_wird_abgelehnt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesendet = _mitschnitt(monkeypatch)
    ziel = _einrichten(admin_client, gesendet)
    pfad = f"/api/settings/channels/gotify/targets/{ziel['id']}/events"

    assert admin_client.put(pfad, json={"events": {"gibt_es_nicht": "high"}}).status_code == 422
    assert admin_client.put(pfad, json={"events": {"request_pending": "laut"}}).status_code == 422


def test_bearbeiten_ohne_neues_token_behaelt_das_alte(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Oberflaeche bekommt das Token nur maskiert - leer heisst "unveraendert"."""
    gesendet = _mitschnitt(monkeypatch)
    ziel = _einrichten(admin_client, gesendet)

    entwurf = {"name": "Neuer Name", "url": "http://gotify.test", "token": "", "language": "de"}
    assert admin_client.post(
        f"/api/settings/channels/gotify/test?target_id={ziel['id']}", json=entwurf
    ).json()["ok"]
    admin_client.post(
        "/api/settings/channels/gotify/confirm", json={"code": _code_aus(gesendet)}
    )
    antwort = admin_client.put(
        f"/api/settings/channels/gotify/targets/{ziel['id']}", json=entwurf
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["name"] == "Neuer Name"

    with SessionLocal() as db:
        from app.services import channel_targets

        assert channel_targets.werte(db.get(ChannelTarget, ziel["id"]))["token"] == "geheim"


def test_loeschen_entfernt_das_ziel(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesendet = _mitschnitt(monkeypatch)
    ziel = _einrichten(admin_client, gesendet)

    assert (
        admin_client.delete(f"/api/settings/channels/gotify/targets/{ziel['id']}").status_code
        == 204
    )
    assert admin_client.get("/api/settings/channels/gotify/targets").json() == []


def test_ziel_eines_anderen_dienstes_ist_unerreichbar(admin_client: TestClient) -> None:
    """Sonst liesse sich ein Gotify-Postfach ueber den ntfy-Pfad umkonfigurieren."""
    ziel_id = _ziel()
    assert (
        admin_client.delete(f"/api/settings/channels/ntfy/targets/{ziel_id}").status_code == 404
    )


def test_gescheiterter_versand_gibt_keinen_code_aus(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mitschnitt(monkeypatch, antwort=403)
    antwort = admin_client.post("/api/settings/channels/gotify/test", json=GOTIFY_ENTWURF)
    assert antwort.json()["ok"] is False

    # Und ohne vorherigen Versand laesst sich auch nichts bestaetigen.
    ergebnis = admin_client.post(
        "/api/settings/channels/gotify/confirm", json={"code": "1234"}
    ).json()
    assert ergebnis["ok"] is False


def test_zu_viele_fehlversuche_beenden_den_vorgang(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vier Ziffern sind schnell geraten, wenn man beliebig oft raten darf."""
    gesendet = _mitschnitt(monkeypatch)
    admin_client.post("/api/settings/channels/gotify/test", json=GOTIFY_ENTWURF)
    code = _code_aus(gesendet)
    falsch = "0000" if code != "0000" else "1111"

    for _ in range(5):
        admin_client.post("/api/settings/channels/gotify/confirm", json={"code": falsch})

    # Selbst der richtige Code hilft jetzt nicht mehr.
    assert (
        admin_client.post(
            "/api/settings/channels/gotify/confirm", json={"code": code}
        ).json()["ok"]
        is False
    )


def test_test_ohne_angaben_scheitert_verstaendlich(admin_client: TestClient) -> None:
    antwort = admin_client.post("/api/settings/channels/gotify/test", json={})
    daten = antwort.json()
    assert daten["ok"] is False
    assert "Gotify" in daten["message"]


def test_ziel_ohne_namen_wird_abgelehnt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesendet = _mitschnitt(monkeypatch)
    ohne_namen = {**GOTIFY_ENTWURF, "name": "  "}
    admin_client.post("/api/settings/channels/gotify/test", json=ohne_namen)
    admin_client.post(
        "/api/settings/channels/gotify/confirm", json={"code": _code_aus(gesendet)}
    )
    antwort = admin_client.post("/api/settings/channels/gotify/targets", json=ohne_namen)
    assert antwort.status_code == 422


def test_gewoehnlicher_benutzer_sieht_die_ziele_nicht(client: TestClient) -> None:
    """Systembenachrichtigungen sind Sache des Administrators."""
    assert client.get("/api/settings/channels/gotify/targets").status_code in (401, 403)
    assert client.post("/api/settings/channels/ntfy/test", json={}).status_code in (401, 403)


# --- Hilfsmittel ----------------------------------------------------------


def _mit_attrappe(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Alle ausgehenden Anfragen der Kanaele auf eine Attrappe umlenken.

    Die Kanaele bauen sich ihren Client je Versand selbst - deshalb wird hier
    die Klasse ersetzt und nicht eine einzelne Instanz.
    """
    from app.services.channels import base

    class Attrappe(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs.pop("transport", None)
            super().__init__(*args, transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(base.httpx, "AsyncClient", Attrappe)


# --- Zwei Ebenen: ntfy ----------------------------------------------------


def test_ntfy_topic_erbt_die_verbindung_der_instanz() -> None:
    """Adresse und Anmeldung stehen einmal an der Instanz, nicht bei jedem Topic."""
    from app.services import channel_targets

    topic_id = _ziel(
        kanal=ChannelKind.ntfy, name="Familie", url="http://haus.test", topic="wohnen"
    )
    with SessionLocal() as db:
        werte = channel_targets.werte(db.get(ChannelTarget, topic_id))
        assert werte["url"] == "http://haus.test"
        assert werte["topic"] == "wohnen"


def test_ntfy_instanz_bekommt_selbst_nichts() -> None:
    """Die Instanz ist kein Postfach - dorthin geht nie eine Nachricht."""
    topic_id = _ziel(kanal=ChannelKind.ntfy)
    with SessionLocal() as db:
        instanz_id = db.get(ChannelTarget, topic_id).parent_id
        _freigabe_anfragen(db)
        ziele = {zeile.target_id for zeile in db.query(ChannelMessage).all()}
        assert ziele == {topic_id}
        assert instanz_id not in ziele


def test_mehrere_topics_einer_instanz_bekommen_alle_etwas() -> None:
    topic_id = _ziel(kanal=ChannelKind.ntfy, name="Erstes", topic="eins")
    with SessionLocal() as db:
        instanz = db.get(ChannelTarget, topic_id).parent
        db.add(
            ChannelTarget(
                channel=ChannelKind.ntfy,
                parent_id=instanz.id,
                name="Zweites",
                topic="zwei",
                language="de",
                verified=True,
                events={"request_pending": "low"},
            )
        )
        db.commit()

        _freigabe_anfragen(db)
        assert db.query(ChannelMessage).count() == 2


def test_instanz_loeschen_nimmt_ihre_topics_mit() -> None:
    """Ohne Adresse und Anmeldung haetten die Topics kein Gegenueber mehr."""
    topic_id = _ziel(kanal=ChannelKind.ntfy)
    with SessionLocal() as db:
        instanz = db.get(ChannelTarget, topic_id).parent
        db.delete(instanz)
        db.commit()
        assert db.get(ChannelTarget, topic_id) is None


def test_ntfy_instanz_laesst_sich_ohne_code_anlegen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dorthin geht nie eine Nachricht - es gibt nichts zu bestaetigen."""
    _mitschnitt(monkeypatch)
    antwort = admin_client.post(
        "/api/settings/channels/ntfy/targets",
        json={"name": "Zuhause", "url": "http://ntfy.test", "auth": "none"},
    )
    assert antwort.status_code == 201, antwort.text
    daten = antwort.json()
    assert daten["verified"] is False
    assert daten["children"] == []


def test_ntfy_topic_braucht_den_code(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesendet = _mitschnitt(monkeypatch)
    instanz = admin_client.post(
        "/api/settings/channels/ntfy/targets",
        json={"name": "Zuhause", "url": "http://ntfy.test", "auth": "none"},
    ).json()

    entwurf = {"name": "Familie", "topic": "wohnen", "language": "de"}

    # Ohne Bestaetigung: abgelehnt.
    ohne = admin_client.post(
        f"/api/settings/channels/ntfy/targets/{instanz['id']}/children", json=entwurf
    )
    assert ohne.status_code == 409

    # Mit Testnachricht und Code: angelegt. Die Nachricht geht an das Topic
    # und ueber die Adresse der Instanz.
    assert admin_client.post(
        f"/api/settings/channels/ntfy/test?parent_id={instanz['id']}", json=entwurf
    ).json()["ok"]
    assert gesendet[-1]["topic"] == "wohnen"
    assert admin_client.post(
        "/api/settings/channels/ntfy/confirm", json={"code": _code_aus(gesendet)}
    ).json()["ok"]

    antwort = admin_client.post(
        f"/api/settings/channels/ntfy/targets/{instanz['id']}/children", json=entwurf
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["verified"] is True

    # Und die Instanz fuehrt es jetzt als Kind.
    ziele = admin_client.get("/api/settings/channels/ntfy/targets").json()
    assert [kind["name"] for kind in ziele[0]["children"]] == ["Familie"]


def test_gotify_kennt_keine_zweite_ebene(admin_client: TestClient) -> None:
    ziel_id = _ziel()
    antwort = admin_client.post(
        f"/api/settings/channels/gotify/targets/{ziel_id}/children",
        json={"name": "geht nicht"},
    )
    assert antwort.status_code == 422


def test_erreichbarkeit_prueft_ohne_code(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/health"
        return httpx.Response(200, json={"healthy": True})

    _mit_attrappe(monkeypatch, handler)
    antwort = admin_client.post(
        "/api/settings/channels/ntfy/check", json={"url": "http://ntfy.test"}
    )
    assert antwort.json()["ok"] is True


def test_erreichbarkeit_meldet_fremden_dienst(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mit_attrappe(
        monkeypatch, lambda _request: httpx.Response(200, json={"etwas": "anderes"})
    )
    antwort = admin_client.post(
        "/api/settings/channels/ntfy/check", json={"url": "http://ntfy.test"}
    )
    daten = antwort.json()
    assert daten["ok"] is False
    assert "ntfy" in daten["message"]


# --- E-Mail: allgemeine Adresse ohne Code ---------------------------------


def _mailserver() -> None:
    with SessionLocal() as db:
        from app.services.settings_service import save_settings as _save

        _save(db, {"smtp_host": "mail.test", "smtp_from_address": "nexview@test.de"})


def test_email_ziel_braucht_keinen_code(admin_client: TestClient) -> None:
    """Hinter einer allgemeinen Adresse sitzt niemand, der einen Code ablesen koennte."""
    _mailserver()
    antwort = admin_client.post(
        "/api/settings/channels/email/targets",
        json={"name": "Team", "address": "team@test.de", "language": "de"},
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["verified"] is True


def test_email_ohne_mailserver_verweist_auf_die_richtige_stelle(
    admin_client: TestClient,
) -> None:
    """"Fehlen noch Angaben" schickte einen sonst ins falsche Formular."""
    antwort = admin_client.post(
        "/api/settings/channels/email/test",
        json={"name": "Team", "address": "team@test.de"},
    )
    daten = antwort.json()
    assert daten["ok"] is False
    assert "Mailserver" in daten["message"]


@pytest.mark.asyncio
async def test_email_versand_mit_eigenem_betreff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Betreff je Adresse - mit {titel} als einzigem Platzhalter."""
    _mailserver()
    from app.crypto import encrypt as _encrypt  # noqa: F401 - Gleichlauf mit _ziel
    from app.services import mail as mail_service

    gesendet: list[tuple[str, str]] = []

    async def attrappe(config, to, subject, html, text):  # noqa: ANN001
        gesendet.append((to, subject))

    monkeypatch.setattr(mail_service, "send", attrappe)

    with SessionLocal() as db:
        db.add(
            ChannelTarget(
                channel=ChannelKind.email,
                name="Team",
                address="team@test.de",
                subject="Kino: {titel}",
                language="de",
                verified=True,
                events={"request_pending": "high"},
            )
        )
        db.commit()
        _freigabe_anfragen(db)
        assert await channel_outbox.process(db, load_settings(db)) == 1

    assert gesendet == [("team@test.de", "Kino: Neue Freigabeanfrage")]


# --- Telegram --------------------------------------------------------------


def test_telegram_bot_laesst_sich_ohne_code_anlegen(admin_client: TestClient) -> None:
    """Der Bot ist kein Postfach - dorthin geht nie eine Nachricht."""
    antwort = admin_client.post(
        "/api/settings/channels/telegram/targets",
        json={"name": "Bot", "token": "123:AA"},
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["verified"] is False


@pytest.mark.asyncio
async def test_telegram_entschaerft_html_und_stellt_leise_zu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filmtitel bestimmt jemand anderes - und "leise" heisst ohne Ton."""
    from app.services.channels import telegram

    gesehen: list[httpx.Request] = []
    _mit_attrappe(
        monkeypatch,
        lambda request: (gesehen.append(request), httpx.Response(200, json={"ok": True}))[1],
    )

    config = telegram.TelegramConfig(
        token="123:AA", username="bot", chat_id="42", thread_id="", silent=False, language="de"
    )
    await telegram.send(config, Notice(title="<Dune> & Co", body="**fett**", level="low"))

    daten = json.loads(gesehen[0].content)
    assert "sendMessage" in str(gesehen[0].url)
    assert daten["parse_mode"] == "HTML"
    assert daten["disable_notification"] is True  # Stufe "leise"
    assert "&lt;Dune&gt; &amp; Co" in daten["text"]
    assert "<b>fett</b>" in daten["text"]


def test_telegram_chats_aus_dem_entwurf(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Chat-Suche laeuft schon vor dem Speichern - mit dem Token aus dem Formular."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "getUpdates" in str(request.url)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {"message": {"chat": {"id": 77, "first_name": "Markus", "type": "private"}}},
                    {"message": {"chat": {"id": -100, "title": "Familie", "type": "supergroup"}}},
                ],
            },
        )

    _mit_attrappe(monkeypatch, handler)
    antwort = admin_client.post(
        "/api/settings/channels/telegram/chats", json={"token": "123:AA"}
    )
    assert antwort.status_code == 200, antwort.text
    daten = {eintrag["chat_id"]: eintrag["name"] for eintrag in antwort.json()}
    assert daten == {"77": "Markus", "-100": "Familie"}


# --- Ein/Aus je Ziel --------------------------------------------------------


def test_stillgelegtes_ziel_bekommt_nichts(admin_client: TestClient) -> None:
    ziel_id = _ziel()
    antwort = admin_client.put(
        f"/api/settings/channels/gotify/targets/{ziel_id}/enabled", json={"enabled": False}
    )
    assert antwort.status_code == 200 and antwort.json()["enabled"] is False

    with SessionLocal() as db:
        _freigabe_anfragen(db)
        assert db.query(ChannelMessage).count() == 0


def test_wieder_in_betrieb_genommen_bekommt_wieder(admin_client: TestClient) -> None:
    ziel_id = _ziel()
    admin_client.put(
        f"/api/settings/channels/gotify/targets/{ziel_id}/enabled", json={"enabled": False}
    )
    admin_client.put(
        f"/api/settings/channels/gotify/targets/{ziel_id}/enabled", json={"enabled": True}
    )
    with SessionLocal() as db:
        _freigabe_anfragen(db)
        assert db.query(ChannelMessage).count() == 1


def test_stillgelegte_instanz_legt_ihre_topics_mit_still(admin_client: TestClient) -> None:
    """Ohne Adresse und Anmeldung koennten sie ohnehin nichts ausrichten."""
    topic_id = _ziel(kanal=ChannelKind.ntfy)
    with SessionLocal() as db:
        instanz_id = db.get(ChannelTarget, topic_id).parent_id

    admin_client.put(
        f"/api/settings/channels/ntfy/targets/{instanz_id}/enabled", json={"enabled": False}
    )
    with SessionLocal() as db:
        _freigabe_anfragen(db)
        assert db.query(ChannelMessage).count() == 0


# --- Meldungsgruppen --------------------------------------------------------


def test_entschieden_deckt_freigabe_und_ablehnung_ab() -> None:
    """Ein Haken fuer beides - fuer den Kanal ist es dieselbe Auskunft."""
    _ziel(events={"request_decided": "low"})
    with SessionLocal() as db:
        empfaenger = _benutzer(db, "nutzer", Role.user)
        request = _anfrage(db, empfaenger)
        for art in (NotificationType.approved, NotificationType.rejected):
            notify.create(
                db, user=empfaenger, kind=art, message_key="notifications.x", request=request
            )
        db.commit()
        assert db.query(ChannelMessage).count() == 2


@pytest.mark.asyncio
async def test_neu_verfuegbar_mit_eigenem_titel(monkeypatch: pytest.MonkeyPatch) -> None:
    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(200))[1])
    _ziel(kanal=ChannelKind.ntfy, events={"download_complete": "normal"})

    with SessionLocal() as db:
        empfaenger = _benutzer(db, "nutzer", Role.user)
        request = _anfrage(db, empfaenger)
        notify.create(
            db,
            user=empfaenger,
            kind=NotificationType.download_complete,
            message_key="notifications.downloadComplete",
            request=request,
        )
        db.commit()
        assert await channel_outbox.process(db, load_settings(db)) == 1

    daten = json.loads(gesehen[0].content)
    assert daten["title"] == "Neu verfügbar"
    assert "The Dark Knight" in daten["message"]


def test_ticket_fuehrt_zum_ticketbereich(admin_client: TestClient) -> None:
    """Der Klick soll dorthin, wo man antworten kann - nicht auf die Freigabeliste."""
    from app.models import NotificationType as NT
    from app.services.channel_outbox import LINKS

    assert LINKS[NT.ticket_new] == "/tickets"


# --- Discord ----------------------------------------------------------------


DISCORD_ENTWURF = {
    "name": "Familie",
    "url": "https://discord.test/api/webhooks/1/geheim",
    "language": "de",
}


def test_discord_ablauf_testen_bestaetigen_speichern(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Derselbe Weg wie bei Gotify - die Webhook-URL ist dabei das Geheimnis."""
    gesendet = _mitschnitt(monkeypatch)

    assert admin_client.post(
        "/api/settings/channels/discord/test", json=DISCORD_ENTWURF
    ).json()["ok"]
    assert admin_client.post(
        "/api/settings/channels/discord/confirm", json={"code": _code_aus_embed(gesendet)}
    ).json()["ok"]

    antwort = admin_client.post("/api/settings/channels/discord/targets", json=DISCORD_ENTWURF)
    assert antwort.status_code == 201, antwort.text
    ziel = antwort.json()
    assert ziel["verified"] is True
    # Die URL ist ein Geheimnis: maskiert raus, nie im Klartext.
    assert "geheim" not in ziel["url"]
    assert ziel["url_set"] is True


@pytest.mark.asyncio
async def test_discord_embed_mit_farbe_und_poster(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.channels import discord

    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(204))[1])

    config = discord.DiscordConfig(
        url="https://discord.test/api/webhooks/1/x", username="Nexview", language="de"
    )
    await discord.send(
        config,
        Notice(
            title="Neue Freigabeanfrage",
            body="**Dune**",
            event="request_pending",
            poster_url="https://bild.test/p.jpg",
            click_url="https://nexview.test/admin/requests",
        ),
    )

    daten = json.loads(gesehen[0].content)
    assert daten["username"] == "Nexview"
    # Der Absender traegt immer das Nexview-Logo von der Projektseite.
    assert daten["avatar_url"] == discord.AVATAR_URL
    embed = daten["embeds"][0]
    assert embed["color"] == discord.COLORS["request_pending"]
    assert embed["thumbnail"] == {"url": "https://bild.test/p.jpg"}
    assert embed["url"] == "https://nexview.test/admin/requests"
    assert embed["description"] == "**Dune**"


def test_discord_erreichbarkeit_prueft_den_webhook(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein GET auf die URL beantwortet Discord mit der Webhook-Beschreibung."""

    # Eine Attrappe, deren Antwort unterwegs getauscht wird - zweimal
    # `_mit_attrappe` geht nicht, die zweite Klasse erbte sonst die erste.
    antwort_json: dict[str, object] = {"channel_id": "123", "name": "nexview"}
    _mit_attrappe(monkeypatch, lambda _r: httpx.Response(200, json=antwort_json))

    antwort = admin_client.post("/api/settings/channels/discord/check", json=DISCORD_ENTWURF)
    assert antwort.json()["ok"] is True, antwort.text

    antwort_json = {"etwas": "anderes"}
    daten = admin_client.post(
        "/api/settings/channels/discord/check", json=DISCORD_ENTWURF
    ).json()
    assert daten["ok"] is False
    assert "Discord" in daten["message"]


def _code_aus_embed(gesendet: list[dict]) -> str:
    """Bei Discord steckt der Code im Embed-Titel, nicht in einer flachen Zeile."""
    import re as _re

    rumpf = gesendet[-1]
    titel = (rumpf.get("embeds") or [{}])[0].get("title") or rumpf.get("title", "")
    treffer = _re.search(r"\d{4}", titel)
    assert treffer, f"kein Code im Titel: {rumpf!r}"
    return treffer.group()


# --- Webhook ----------------------------------------------------------------


WEBHOOK_ENTWURF = {
    "name": "Home Assistant",
    "url": "https://hooks.test/nexview",
    "token": "Bearer geheim123",
    "language": "de",
}


def test_webhook_ablauf_testen_bestaetigen_speichern(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch der Universalanschluss beweist erst, dass etwas ankommt."""
    gesendet = _mitschnitt(monkeypatch)

    assert admin_client.post(
        "/api/settings/channels/webhook/test", json=WEBHOOK_ENTWURF
    ).json()["ok"]
    # Der Code steht im title-Feld des JSON - dort liest ihn ab, wer den
    # Empfaenger eingerichtet hat.
    import re as _re

    treffer = _re.search(r"\d{4}", gesendet[-1]["title"])
    assert treffer, f"kein Code im title: {gesendet[-1]!r}"
    assert admin_client.post(
        "/api/settings/channels/webhook/confirm", json={"code": treffer.group()}
    ).json()["ok"]

    antwort = admin_client.post("/api/settings/channels/webhook/targets", json=WEBHOOK_ENTWURF)
    assert antwort.status_code == 201, antwort.text
    ziel = antwort.json()
    assert ziel["verified"] is True
    # Der Authorization-Header ist das Geheimnis: maskiert raus.
    assert "geheim123" not in ziel["token"]
    assert ziel["token_set"] is True
    # Die Adresse dagegen ist keins.
    assert ziel["url"] == "https://hooks.test/nexview"


@pytest.mark.asyncio
async def test_webhook_rumpf_und_kopfzeile(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.channels import webhook

    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(200))[1])

    config = webhook.WebhookConfig(
        url="https://hooks.test/nexview", token="Bearer abc", language="de"
    )
    await webhook.send(
        config,
        Notice(
            title="Neue Freigabeanfrage",
            body="**Dune** wartet",
            level="high",
            event="request_pending",
            poster_url="https://bild.test/p.jpg",
            click_url="https://nexview.test/admin/requests",
        ),
    )

    anfrage = gesehen[0]
    assert anfrage.headers["Authorization"] == "Bearer abc"
    daten = json.loads(anfrage.content)
    assert daten["source"] == "nexview"
    assert daten["event"] == "request_pending"
    assert daten["level"] == "high"
    # Maschinen lesen keine Hervorhebungen - die Sternchen sind draussen.
    assert daten["body"] == "Dune wartet"
    assert daten["image"] == "https://bild.test/p.jpg"
    assert daten["url"] == "https://nexview.test/admin/requests"


@pytest.mark.asyncio
async def test_webhook_ohne_header_und_testnachricht(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne Token keine Authorization-Zeile; ohne Ereignis heisst es "test"."""
    from app.services.channels import webhook

    gesehen: list[httpx.Request] = []
    _mit_attrappe(monkeypatch, lambda request: (gesehen.append(request), httpx.Response(200))[1])

    config = webhook.WebhookConfig(url="https://hooks.test/nexview", token="", language="de")
    await webhook.send(config, Notice(title="Probe 1234", body="b"))

    anfrage = gesehen[0]
    assert "authorization" not in {k.lower() for k in anfrage.headers}
    assert json.loads(anfrage.content)["event"] == "test"


# --- Apprise ----------------------------------------------------------------


APPRISE_ENTWURF = {
    "name": "Signal-Verteiler",
    "url": "http://apprise.test:8000",
    "topic": "nexview",
    "language": "de",
}


def test_apprise_ablauf_testen_bestaetigen_speichern(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesendet = _mitschnitt(monkeypatch)

    assert admin_client.post(
        "/api/settings/channels/apprise/test", json=APPRISE_ENTWURF
    ).json()["ok"]
    import re as _re

    treffer = _re.search(r"\d{4}", gesendet[-1]["title"])
    assert treffer, f"kein Code im title: {gesendet[-1]!r}"
    assert admin_client.post(
        "/api/settings/channels/apprise/confirm", json={"code": treffer.group()}
    ).json()["ok"]

    antwort = admin_client.post("/api/settings/channels/apprise/targets", json=APPRISE_ENTWURF)
    assert antwort.status_code == 201, antwort.text
    ziel = antwort.json()
    assert ziel["verified"] is True
    # Adresse und Schluessel sind keine Geheimnisse - beide unmaskiert.
    assert ziel["url"] == "http://apprise.test:8000"
    assert ziel["topic"] == "nexview"


@pytest.mark.asyncio
async def test_apprise_rumpf_und_stufen(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.channels import apprise

    gesehen: list[httpx.Request] = []
    _mit_attrappe(
        monkeypatch,
        lambda request: (gesehen.append(request), httpx.Response(200, json={"error": None}))[1],
    )

    config = apprise.AppriseConfig(url="http://apprise.test:8000", topic="nexview", language="de")
    await apprise.send(
        config,
        Notice(
            title="Neue Freigabeanfrage",
            body="**Dune** wartet",
            level="urgent",
            click_url="https://nexview.test/admin/requests",
        ),
    )

    anfrage = gesehen[0]
    assert str(anfrage.url) == "http://apprise.test:8000/notify/nexview"
    daten = json.loads(anfrage.content)
    assert daten["type"] == "failure"  # dringend -> Apprises lauteste Stufe
    assert daten["format"] == "markdown"
    # Apprise kennt kein Link-Feld - der Verweis steht im Text.
    assert "https://nexview.test/admin/requests" in daten["body"]


@pytest.mark.asyncio
async def test_apprise_leerer_schluessel_ist_ein_fehler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """204 heisst bei Apprise "kein Ziel hinterlegt" - kein stiller Erfolg."""
    from app.services.channels import apprise

    # Eine Attrappe mit tauschbarem Status - zweimal `_mit_attrappe` erbte
    # sonst die erste Antwort (siehe Discord-Erreichbarkeitstest).
    status = 204
    _mit_attrappe(monkeypatch, lambda _r: httpx.Response(status))
    config = apprise.AppriseConfig(url="http://apprise.test:8000", topic="tippfehler", language="de")
    with pytest.raises(ChannelError, match="Schlüssel"):
        await apprise.send(config, Notice(title="t", body="b"))

    status = 424
    with pytest.raises(ChannelError, match="zustellen"):
        await apprise.send(config, Notice(title="t", body="b"))


def test_apprise_erreichbarkeit(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    antwort_json: dict[str, object] = {"status": {"details": ["OK"]}, "config_lock": False}
    _mit_attrappe(monkeypatch, lambda _r: httpx.Response(200, json=antwort_json))

    daten = admin_client.post("/api/settings/channels/apprise/check", json=APPRISE_ENTWURF).json()
    assert daten["ok"] is True, daten

    antwort_json = {"etwas": "anderes"}
    daten = admin_client.post("/api/settings/channels/apprise/check", json=APPRISE_ENTWURF).json()
    assert daten["ok"] is False
    assert "Apprise" in daten["message"]
