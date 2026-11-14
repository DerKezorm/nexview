"""Fernseher koppeln: Code auf dem Bildschirm, Bestaetigen im Browser.

Was hier bewacht wird, steht in ``services/geraete_code.py``. In Kuerze: Der
Schluessel entsteht erst, wenn das Geraet nach der Bestaetigung nachfragt, und
genau einmal; er gehoert dem, der bestaetigt hat (oder seinem Kind); jeder Code
gilt nur einmal; und alle drei offenen Tueren sind gebremst.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import ApiKey, GeraeteCode, User, utcnow
from app.services import geraete_code

from .conftest import auth_headers, create_user

OHNE = {"Authorization": ""}


class Uhr:
    """Die Uhr der Abfrage-Bremse, von Hand gestellt."""

    def __init__(self) -> None:
        self.jetzt = 1000.0

    def __call__(self) -> float:
        return self.jetzt

    def weiter(self, sekunden: float = geraete_code.INTERVALL) -> None:
        self.jetzt += sekunden


@pytest.fixture
def uhr(monkeypatch: pytest.MonkeyPatch) -> Uhr:
    gestellt = Uhr()
    monkeypatch.setattr(geraete_code, "_uhr", gestellt)
    return gestellt


def _erwachsener(client: TestClient, name: str = "jonas", **extra: object) -> dict[str, str]:
    create_user(client, name, "passwort-1234", **extra)
    return auth_headers(client, name, "passwort-1234")


def _start(client: TestClient, **koerper: str) -> dict:
    koerper = {"device": "Wohnzimmer", "app": "nexcinema", **koerper}
    antwort = client.post("/api/app/1/device/code", json=koerper, headers=OHNE)
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


def _fragen(client: TestClient, code: dict, uhr: Uhr | None = None):
    if uhr is not None:
        uhr.weiter()
    return client.post("/api/app/1/device/token", json={"device_code": code["device_code"]}, headers=OHNE)


def _bestaetigen(client: TestClient, kopf: dict[str, str], code: str, **extra: object):
    return client.post("/api/geraete-code/bestaetigen", json={"code": code, **extra}, headers=kopf)


def _schluesselzahl() -> int:
    with SessionLocal() as db:
        return len(db.scalars(select(ApiKey)).all())


# --- Der Ablauf --------------------------------------------------------------


def test_der_ganze_ablauf_bringt_einen_schluessel_fuer_den_der_bestaetigt(
    admin_client: TestClient, uhr: Uhr
) -> None:
    kopf = _erwachsener(admin_client)
    vorher = _schluesselzahl()
    code = _start(admin_client)

    benutzer_code = code["user_code"]
    assert len(benutzer_code) == 9 and benutzer_code[4] == "-"
    assert all(z in geraete_code.ZEICHEN for z in benutzer_code.replace("-", ""))
    assert code["verification_uri"].endswith("/link")
    assert code["verification_uri_complete"] == f"{code['verification_uri']}?code={benutzer_code}"
    assert code["expires_in"] == 600 and code["interval"] == geraete_code.INTERVALL
    assert len(code["device_code"]) >= 40

    assert _fragen(admin_client, code).json() == {
        "status": "pending", "interval": geraete_code.INTERVALL, "key": None, "account": None,
    }

    gefunden = admin_client.post("/api/geraete-code/pruefen", json={"code": benutzer_code}, headers=kopf)
    assert gefunden.status_code == 200, gefunden.text
    assert gefunden.json()["geraet"] == "Wohnzimmer"
    assert gefunden.json()["app"] == "nexcinema"
    assert gefunden.json()["kinder"] == []

    # Bestaetigt, aber noch nicht abgeholt: Es gibt noch keinen Schluessel.
    bestaetigt = _bestaetigen(admin_client, kopf, benutzer_code)
    assert bestaetigt.status_code == 200, bestaetigt.text
    assert bestaetigt.json() == {"geraet": "Wohnzimmer", "konto": "jonas"}
    assert _schluesselzahl() == vorher

    fertig = _fragen(admin_client, code, uhr)
    assert fertig.status_code == 200, fertig.text
    assert fertig.headers["cache-control"] == "no-store"
    daten = fertig.json()
    assert daten["status"] == "approved"
    assert daten["account"] == {"name": "jonas", "role": "user"}
    assert daten["key"].startswith("nxv_")
    assert _schluesselzahl() == vorher + 1

    ich = admin_client.get("/api/app/1/me", headers={"Authorization": f"Bearer {daten['key']}"})
    assert ich.status_code == 200 and ich.json()["konto"]["name"] == "jonas"

    # In der Liste der Schluessel, unter dem Namen des Geraets, widerrufbar.
    liste = admin_client.get("/api/auth/me/schluessel", headers=kopf).json()
    assert [s["name"] for s in liste] == ["Wohnzimmer"]
    assert admin_client.delete(f"/api/auth/me/schluessel/{liste[0]['id']}", headers=kopf).status_code == 204
    assert admin_client.get("/api/app/1/me", headers={"Authorization": f"Bearer {daten['key']}"}).status_code == 401


def test_jeder_code_gilt_nur_einmal(admin_client: TestClient, uhr: Uhr) -> None:
    kopf = _erwachsener(admin_client)
    code = _start(admin_client)
    assert _bestaetigen(admin_client, kopf, code["user_code"]).status_code == 200

    # Ein zweites Mal bestaetigen geht nicht, auch nicht von jemand anderem.
    noch_mal = _bestaetigen(admin_client, _erwachsener(admin_client, "lena"), code["user_code"])
    assert noch_mal.status_code == 404
    assert noch_mal.json()["detail"]["code"] == "device_code_not_found"

    assert _fragen(admin_client, code, uhr).json()["status"] == "approved"
    vorher = _schluesselzahl()
    # Abgeholt ist abgeholt: kein zweiter Schluessel, der Code ist "expired".
    zweites = _fragen(admin_client, code, uhr)
    assert zweites.json() == {"status": "expired", "interval": None, "key": None, "account": None}
    assert _schluesselzahl() == vorher
    assert admin_client.post("/api/geraete-code/pruefen", json={"code": code["user_code"]}, headers=kopf).status_code == 404


def test_zwei_gleichzeitige_abfragen_bringen_nur_einen_schluessel(
    admin_client: TestClient, uhr: Uhr, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwei Abfragen, derselbe Augenblick: Beide lesen "bestaetigt", nur eine kommt durch.

    Nachgestellt wird der Moment zwischen Lesen und Beanspruchen: Gleich nach
    dem Lesen der Zeile holt sich eine andere Abfrage den Schluessel. Die erste
    haelt dann noch den alten Stand im Speicher - durchkommen darf sie nicht.
    """
    kopf = _erwachsener(admin_client)
    code = _start(admin_client)
    assert _bestaetigen(admin_client, kopf, code["user_code"]).status_code == 200

    echte_bremse = geraete_code._abfrage_bremsen

    def dazwischen(pruefsumme: str) -> None:
        echte_bremse(pruefsumme)
        with SessionLocal() as db:
            db.get(GeraeteCode, pruefsumme).stand = "abgeholt"
            db.commit()

    monkeypatch.setattr(geraete_code, "_abfrage_bremsen", dazwischen)
    vorher = _schluesselzahl()
    assert _fragen(admin_client, code, uhr).json()["status"] == "expired"
    assert _schluesselzahl() == vorher


def test_zwei_gleichzeitige_antworten_im_browser_nur_eine_gilt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jemand lehnt ab, waehrend ein anderer bestaetigt: Die spaetere Antwort gilt nicht."""
    kopf = _erwachsener(admin_client)
    code = _start(admin_client)
    echt = geraete_code.finden
    aufrufe: list[str] = []

    # Der Router sucht den Code einmal, der Dienst noch einmal vor dem
    # Schreiben. Dazwischengefunkt wird beim zweiten Mal, nach dem Lesen.
    def dazwischen(db, eingabe):
        zeile = echt(db, eingabe)
        aufrufe.append(eingabe)
        if zeile is not None and len(aufrufe) == 2:
            with SessionLocal() as andere:
                andere.get(GeraeteCode, zeile.geraet_hash).stand = "abgelehnt"
                andere.commit()
        return zeile

    monkeypatch.setattr(geraete_code, "finden", dazwischen)
    assert _bestaetigen(admin_client, kopf, code["user_code"]).status_code == 404
    assert len(aufrufe) == 2
    with SessionLocal() as db:
        assert db.scalar(select(GeraeteCode)).stand == "abgelehnt"


def test_abgelehnt_bringt_keinen_schluessel(admin_client: TestClient, uhr: Uhr) -> None:
    kopf = _erwachsener(admin_client)
    code = _start(admin_client)
    vorher = _schluesselzahl()
    assert admin_client.post(
        "/api/geraete-code/ablehnen", json={"code": code["user_code"]}, headers=kopf
    ).status_code == 204
    assert _fragen(admin_client, code, uhr).json()["status"] == "denied"
    assert _fragen(admin_client, code, uhr).json()["status"] == "denied"
    assert _schluesselzahl() == vorher
    # Abgelehnt ist erledigt: Nachtraeglich bestaetigen geht nicht.
    assert _bestaetigen(admin_client, kopf, code["user_code"]).status_code == 404


def test_abgelaufen_unbekannt_und_leer_sehen_gleich_aus(admin_client: TestClient, uhr: Uhr) -> None:
    kopf = _erwachsener(admin_client)
    code = _start(admin_client)
    with SessionLocal() as db:
        zeile = db.scalar(select(GeraeteCode))
        zeile.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert _fragen(admin_client, code, uhr).json()["status"] == "expired"
    assert _fragen(admin_client, {"device_code": "gibt-es-nicht"}).json()["status"] == "expired"
    assert _fragen(admin_client, {"device_code": ""}).json()["status"] == "expired"
    antwort = _bestaetigen(admin_client, kopf, code["user_code"])
    assert antwort.status_code == 404


def test_ein_abgelaufener_bestaetigter_code_bringt_nichts(admin_client: TestClient, uhr: Uhr) -> None:
    kopf = _erwachsener(admin_client)
    code = _start(admin_client)
    assert _bestaetigen(admin_client, kopf, code["user_code"]).status_code == 200
    with SessionLocal() as db:
        db.scalar(select(GeraeteCode)).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert _fragen(admin_client, code, uhr).json()["status"] == "expired"


def test_der_geheime_code_steht_nur_als_pruefsumme_in_der_datenbank(admin_client: TestClient) -> None:
    code = _start(admin_client)
    with SessionLocal() as db:
        zeile = db.scalar(select(GeraeteCode))
        werte = [str(getattr(zeile, spalte.key)) for spalte in GeraeteCode.__table__.columns]
    assert all(code["device_code"] not in wert for wert in werte)
    assert zeile.geraet_hash == geraete_code._pruefsumme(code["device_code"])


def test_der_code_laesst_sich_klein_und_ohne_bindestrich_eingeben(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    code = _start(admin_client)
    lose = code["user_code"].replace("-", " ").lower()
    antwort = admin_client.post("/api/geraete-code/pruefen", json={"code": lose}, headers=kopf)
    assert antwort.status_code == 200
    assert antwort.json()["code"] == code["user_code"]


def test_ein_anderer_name_landet_am_schluessel(admin_client: TestClient, uhr: Uhr) -> None:
    kopf = _erwachsener(admin_client)
    code = _start(admin_client, device="")
    assert _bestaetigen(admin_client, kopf, code["user_code"], name="  Apple TV   oben ").json()["geraet"] == "Apple TV oben"
    _fragen(admin_client, code, uhr)
    assert [s["name"] for s in admin_client.get("/api/auth/me/schluessel", headers=kopf).json()] == ["Apple TV oben"]


def test_ohne_namen_heisst_das_geraet_tv(admin_client: TestClient, uhr: Uhr) -> None:
    kopf = _erwachsener(admin_client)
    code = _start(admin_client, device="  ")
    assert _bestaetigen(admin_client, kopf, code["user_code"]).json()["geraet"] == "TV"
    _fragen(admin_client, code, uhr)
    assert [s["name"] for s in admin_client.get("/api/auth/me/schluessel", headers=kopf).json()] == ["TV"]


def test_ein_stillgelegtes_konto_bekommt_keinen_schluessel(admin_client: TestClient, uhr: Uhr) -> None:
    kopf = _erwachsener(admin_client)
    code = _start(admin_client)
    assert _bestaetigen(admin_client, kopf, code["user_code"]).status_code == 200
    with SessionLocal() as db:
        db.scalar(select(User).where(User.username == "jonas")).is_active = False
        db.commit()
    vorher = _schluesselzahl()
    assert _fragen(admin_client, code, uhr).json()["status"] == "expired"
    assert _schluesselzahl() == vorher


def test_mit_zwanzig_schluesseln_gibt_es_409_und_der_code_ist_verbraucht(
    admin_client: TestClient, uhr: Uhr
) -> None:
    kopf = _erwachsener(admin_client)
    for nummer in range(20):
        assert admin_client.post(
            "/api/auth/me/schluessel", json={"name": f"s{nummer}"}, headers=kopf
        ).status_code == 201
    code = _start(admin_client)
    assert _bestaetigen(admin_client, kopf, code["user_code"]).status_code == 200
    antwort = _fragen(admin_client, code, uhr)
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "apikey_too_many"
    assert _fragen(admin_client, code, uhr).json()["status"] == "expired"


def test_die_oeffentliche_adresse_geht_vor(admin_client: TestClient) -> None:
    assert _start(admin_client, address="https://kino.example.com")["verification_uri"] == "https://kino.example.com/link"
    assert admin_client.put("/api/settings", json={"public_url": "https://nexview.example.org"}).status_code == 200
    code = _start(admin_client, address="https://kino.example.com")
    assert code["verification_uri"] == "https://nexview.example.org/link"


def test_ohne_adresse_nimmt_der_start_die_der_anfrage(admin_client: TestClient) -> None:
    code = admin_client.post("/api/app/1/device/code", headers=OHNE).json()
    assert code["verification_uri"] == "http://testserver/link"


# --- Wer bestaetigen darf ---------------------------------------------------


def test_ohne_anmeldung_kein_bestaetigen(client: TestClient) -> None:
    for pfad in ("pruefen", "bestaetigen", "ablehnen"):
        assert client.post(f"/api/geraete-code/{pfad}", json={"code": "BCDF-GHJK"}).status_code == 401


def _elternteil_mit_kind(client: TestClient, name: str = "sabine", kind: str = "mia") -> tuple[dict, int]:
    kopf = _erwachsener(client, name, can_manage_children=True)
    antwort = client.post(
        "/api/children", json={"username": kind, "password": "kind-passwort", "age": 8}, headers=kopf
    )
    assert antwort.status_code in (200, 201), antwort.text
    return kopf, antwort.json()["id"]


def test_ein_kind_kann_nicht_bestaetigen(admin_client: TestClient) -> None:
    _elternteil_mit_kind(admin_client)
    kind = auth_headers(admin_client, "mia", "kind-passwort")
    code = _start(admin_client)
    for pfad in ("pruefen", "bestaetigen", "ablehnen"):
        antwort = admin_client.post(f"/api/geraete-code/{pfad}", json={"code": code["user_code"]}, headers=kind)
        assert antwort.status_code == 403, pfad


def test_eltern_koppeln_den_fernseher_ihres_kindes(admin_client: TestClient, uhr: Uhr) -> None:
    eltern, kind_id = _elternteil_mit_kind(admin_client)
    code = _start(admin_client, device="Kinderzimmer")
    gefunden = admin_client.post("/api/geraete-code/pruefen", json={"code": code["user_code"]}, headers=eltern)
    assert gefunden.json()["kinder"] == [{"id": kind_id, "name": "mia"}]

    bestaetigt = _bestaetigen(admin_client, eltern, code["user_code"], child_id=kind_id)
    assert bestaetigt.json() == {"geraet": "Kinderzimmer", "konto": "mia"}

    daten = _fragen(admin_client, code, uhr).json()
    assert daten["account"]["role"] == "child"
    kind = {"Authorization": f"Bearer {daten['key']}"}
    # Der Schluessel kann, was das Kind kann - und nicht mehr.
    assert admin_client.get("/api/app/1/kids/wishes", headers=kind).status_code == 200
    assert admin_client.get("/api/app/1/requests/mine", headers=kind).status_code == 403
    # Bei den Eltern steht das Geraet und laesst sich trennen.
    geraete = admin_client.get(f"/api/children/{kind_id}/geraete", headers=eltern).json()
    assert [g["name"] for g in geraete] == ["Kinderzimmer"]
    assert admin_client.get("/api/auth/me/schluessel", headers=eltern).json() == []


def test_fremde_kinder_bleiben_unerreichbar(admin_client: TestClient) -> None:
    _, kind_id = _elternteil_mit_kind(admin_client)
    fremd = _erwachsener(admin_client, "lena", can_manage_children=True)
    code = _start(admin_client)
    assert admin_client.post("/api/geraete-code/pruefen", json={"code": code["user_code"]}, headers=fremd).json()["kinder"] == []
    antwort = _bestaetigen(admin_client, fremd, code["user_code"], child_id=kind_id)
    assert antwort.status_code == 404
    # Der Code ist nicht verbraucht: Die richtige Person kann ihn noch bestaetigen.
    assert _bestaetigen(admin_client, fremd, code["user_code"]).status_code == 200


def test_ohne_recht_auf_kinderkonten_kein_fernseher_fuers_kind(admin_client: TestClient) -> None:
    eltern, kind_id = _elternteil_mit_kind(admin_client)
    with SessionLocal() as db:
        db.scalar(select(User).where(User.username == "sabine")).can_manage_children = False
        db.commit()
    code = _start(admin_client)
    antwort = _bestaetigen(admin_client, eltern, code["user_code"], child_id=kind_id)
    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "children_not_allowed"


def test_ein_stillgelegtes_elternteil_bringt_dem_kind_keinen_schluessel(admin_client: TestClient, uhr: Uhr) -> None:
    eltern, kind_id = _elternteil_mit_kind(admin_client)
    code = _start(admin_client)
    assert _bestaetigen(admin_client, eltern, code["user_code"], child_id=kind_id).status_code == 200
    with SessionLocal() as db:
        db.scalar(select(User).where(User.username == "sabine")).is_active = False
        db.commit()
    assert _fragen(admin_client, code, uhr).json()["status"] == "expired"


# --- Bremsen ----------------------------------------------------------------


def test_zu_schnelles_nachfragen_bekommt_429(admin_client: TestClient, uhr: Uhr) -> None:
    code = _start(admin_client)
    assert _fragen(admin_client, code).status_code == 200
    uhr.weiter(1)
    zu_schnell = _fragen(admin_client, code)
    assert zu_schnell.status_code == 429
    assert zu_schnell.json()["detail"]["code"] == "device_poll_too_fast"
    assert zu_schnell.headers["retry-after"] == str(geraete_code.INTERVALL)
    # Zu schnelles Fragen schiebt das Fenster nicht hinaus: Vom ersten Fragen
    # an gerechnet, ist nach dem Intervall wieder frei.
    uhr.weiter(geraete_code.INTERVALL - 1)
    assert _fragen(admin_client, code).status_code == 200


def test_jedes_geraet_hat_sein_eigenes_intervall(admin_client: TestClient, uhr: Uhr) -> None:
    erstes, zweites = _start(admin_client), _start(admin_client)
    assert _fragen(admin_client, erstes).status_code == 200
    assert _fragen(admin_client, zweites).status_code == 200


def test_hoechstens_so_viele_offene_codes(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geraete_code, "OFFEN_HOECHSTENS", 3)
    for _ in range(3):
        _start(admin_client)
    voll = admin_client.post("/api/app/1/device/code", json={}, headers=OHNE)
    assert voll.status_code == 429
    assert voll.json()["detail"]["code"] == "device_code_busy"
    assert int(voll.headers["retry-after"]) > 0
    with SessionLocal() as db:
        assert len(db.scalars(select(GeraeteCode)).all()) == 3
        # Abgelaufene zaehlen nicht mehr mit.
        db.scalars(select(GeraeteCode)).first().expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert admin_client.post("/api/app/1/device/code", json={}, headers=OHNE).status_code == 200


def test_starts_je_adresse_sind_begrenzt(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "client_ip", "proxy")
    eine = {**OHNE, "X-Forwarded-For": "203.0.113.7"}
    andere = {**OHNE, "X-Forwarded-For": "203.0.113.8"}
    for _ in range(geraete_code.START_JE_ADRESSE):
        assert admin_client.post("/api/app/1/device/code", json={}, headers=eine).status_code == 200
    zu_viel = admin_client.post("/api/app/1/device/code", json={}, headers=eine)
    assert zu_viel.status_code == 429
    assert zu_viel.json()["detail"]["code"] == "device_code_busy"
    assert admin_client.post("/api/app/1/device/code", json={}, headers=andere).status_code == 200


def _raten(client: TestClient, kopf: dict[str, str], mal: int) -> list[int]:
    return [
        client.post("/api/geraete-code/pruefen", json={"code": "BBBB-BBBB"}, headers=kopf).status_code
        for _ in range(mal)
    ]


def test_falsche_codes_werden_gebremst(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    assert _raten(admin_client, kopf, 4) == [404, 404, 404, 404]
    gebremst = admin_client.post("/api/geraete-code/pruefen", json={"code": "BBBB-BBBB"}, headers=kopf)
    assert gebremst.status_code == 429
    assert gebremst.json()["detail"]["code"] == "too_many_attempts"
    # Auch Bestaetigen und Ablehnen stehen hinter derselben Bremse.
    assert _bestaetigen(admin_client, kopf, "BBBB-BBBB").status_code == 429
    assert admin_client.post("/api/geraete-code/ablehnen", json={"code": "BBBB-BBBB"}, headers=kopf).status_code == 429


def test_ein_eigener_gueltiger_code_leert_die_bremse_nicht(admin_client: TestClient) -> None:
    """Jeder kann sich gueltige Codes machen. Leerte ein Treffer den Zaehler,
    liesse sich zwischen zwei Rateversuchen immer ein eigener eingeben."""
    kopf = _erwachsener(admin_client)
    assert _raten(admin_client, kopf, 3) == [404, 404, 404]
    eigener = _start(admin_client)
    assert admin_client.post(
        "/api/geraete-code/pruefen", json={"code": eigener["user_code"]}, headers=kopf
    ).status_code == 200
    assert _raten(admin_client, kopf, 2) == [404, 429]


def test_unsinn_als_code_zaehlt_als_fehlversuch(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    for eingabe in ("", "AEIO-UAEI", "2345-6789"):
        assert admin_client.post("/api/geraete-code/pruefen", json={"code": eingabe}, headers=kopf).status_code == 404
    assert _raten(admin_client, kopf, 2) == [404, 429]


# --- Kleinteile -------------------------------------------------------------


@pytest.mark.parametrize(
    ("eingabe", "erwartet"),
    [
        ("BCDF-GHJK", "BCDFGHJK"),
        ("bcdf ghjk", "BCDFGHJK"),
        (" b-c-d-f-g-h-j-k ", "BCDFGHJK"),
        ("BCDF-GHJ", None),
        ("BCDF-GHJKL", None),
        ("BCDF-GHJA", None),
        ("BCDF-GHJ1", None),
        (None, None),
    ],
)
def test_normalisieren(eingabe: str | None, erwartet: str | None) -> None:
    assert geraete_code.normalisieren(eingabe) == erwartet


def test_codes_kommen_nur_aus_den_erlaubten_zeichen(admin_client: TestClient) -> None:
    gesehen = set()
    for _ in range(20):
        gesehen |= set(_start(admin_client)["user_code"].replace("-", ""))
    assert gesehen <= set(geraete_code.ZEICHEN)
    assert not gesehen & set("AEIOU01")


# --- Erkennen im Heimnetz ---------------------------------------------------


def test_hello_sagt_ohne_anmeldung_wer_hier_antwortet(client: TestClient) -> None:
    from app import __version__

    antwort = client.get("/api/app/1/hello", headers=OHNE)
    assert antwort.status_code == 200, antwort.text
    assert antwort.json() == {
        "app": "nexview", "version": __version__, "name": None, "url": None, "device_code": True,
    }


def test_hello_nennt_die_oeffentliche_adresse(admin_client: TestClient) -> None:
    assert admin_client.put("/api/settings", json={"public_url": "https://nexview.example.org"}).status_code == 200
    assert admin_client.get("/api/app/1/hello", headers=OHNE).json()["url"] == "https://nexview.example.org"


def test_hello_verraet_nichts_ueber_konten(admin_client: TestClient) -> None:
    """Genau diese fuenf Felder, mit oder ohne Anmeldung: keine Konten, keine Zahlen."""
    _erwachsener(admin_client)
    ohne = admin_client.get("/api/app/1/hello", headers=OHNE).json()
    mit = admin_client.get("/api/app/1/hello").json()
    assert set(ohne) == set(mit) == {"app", "version", "name", "url", "device_code"}
    assert ohne == mit


def test_hello_ist_je_adresse_locker_gebremst(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "client_ip", "proxy")
    monkeypatch.setattr(geraete_code, "HALLO_JE_ADRESSE", 3)
    eine = {**OHNE, "X-Forwarded-For": "203.0.113.7"}
    for _ in range(3):
        assert admin_client.get("/api/app/1/hello", headers=eine).status_code == 200
    zu_oft = admin_client.get("/api/app/1/hello", headers=eine)
    assert zu_oft.status_code == 429
    assert zu_oft.json()["detail"]["code"] == "device_poll_too_fast"
    assert int(zu_oft.headers["retry-after"]) > 0
    assert admin_client.get("/api/app/1/hello", headers={**OHNE, "X-Forwarded-For": "203.0.113.8"}).status_code == 200


def test_hello_ohne_bekannte_adresse_ungebremst(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geraete_code, "HALLO_JE_ADRESSE", 3)
    assert all(client.get("/api/app/1/hello").status_code == 200 for _ in range(10))
