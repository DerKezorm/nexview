"""Die iPhone-App koppeln: QR-Code im Browser, Schluessel einmal abholen.

Was hier bewacht wird, steht in ``services/app_kopplung.py``. Die wichtigsten
Zusagen in Kuerze: Der Schluessel entsteht erst beim Abholen, er gehoert dem
Konto, fuer das gekoppelt wurde, und ein Kinderschluessel kann nur, was das Kind
kann.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ApiKey, AppKopplung, utcnow
from app.services import app_kopplung

from .conftest import auth_headers, create_user


def _erwachsener(client: TestClient, name: str = "jonas", **extra: object) -> dict[str, str]:
    create_user(client, name, "passwort-1234", **extra)
    return auth_headers(client, name, "passwort-1234")


def _code(client: TestClient, kopf: dict[str, str], pfad: str = "/api/me/app-kopplung") -> dict:
    antwort = client.post(pfad, json={"adresse": "https://kino.example.com"}, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return antwort.json()


def _abholen(client: TestClient, code: dict, geheimnis: str | None = None, geraet: str = "iPhone von Jonas"):
    return client.post(
        f"/api/app/1/pairing/{code['kennung']}/key",
        json={"device": geraet},
        headers={"Authorization": "", "X-Pairing-Secret": geheimnis or code["geheimnis"]},
    )


def _bearer(schluessel: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {schluessel}"}


def _schluesselzahl() -> int:
    with SessionLocal() as db:
        return len(db.scalars(select(ApiKey)).all())


# --- Erwachsene ------------------------------------------------------------


def test_der_code_bringt_einen_schluessel_mit_den_rechten_des_kontos(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    vorher = _schluesselzahl()
    code = _code(admin_client, kopf)

    # ⚠️ Vor dem Abholen gibt es keinen Schluessel - ein ungescannter Code
    # hinterlaesst nichts.
    assert _schluesselzahl() == vorher

    antwort = _abholen(admin_client, code)
    assert antwort.status_code == 200, antwort.text
    assert antwort.headers["cache-control"] == "no-store"
    daten = antwort.json()
    assert daten["account"] == {"name": "jonas", "role": "user"}
    assert daten["key"].startswith("nxv_")
    assert _schluesselzahl() == vorher + 1

    ich = admin_client.get("/api/app/1/me", headers=_bearer(daten["key"]))
    assert ich.status_code == 200, ich.text
    assert ich.json()["konto"]["name"] == "jonas"
    assert ich.json()["konto"]["entscheidet"] is False

    # Der Browser sieht, dass verbunden wurde, und unter welchem Namen.
    stand = admin_client.get(f"/api/me/app-kopplung/{code['kennung']}", headers=kopf).json()
    assert stand == {"stand": "verbunden", "geraet": "iPhone von Jonas"}


def test_der_schluessel_kommt_genau_einmal(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    code = _code(admin_client, kopf)
    assert _abholen(admin_client, code).status_code == 200
    zweites = _abholen(admin_client, code)
    assert zweites.status_code == 404
    assert zweites.json()["detail"]["code"] == "pairing_not_found"


def test_falsches_geheimnis_unbekannt_und_abgelaufen_sehen_gleich_aus(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    code = _code(admin_client, kopf)

    falsch = _abholen(admin_client, code, geheimnis="falsch")
    unbekannt = _abholen(admin_client, {"kennung": "gibt-es-nicht", "geheimnis": code["geheimnis"]})
    ohne = admin_client.post(
        f"/api/app/1/pairing/{code['kennung']}/key", headers={"Authorization": ""}
    )

    with SessionLocal() as db:
        zeile = db.get(AppKopplung, code["kennung"])
        zeile.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    abgelaufen = _abholen(admin_client, code)

    for antwort in (falsch, unbekannt, ohne, abgelaufen):
        assert antwort.status_code == 404
        assert antwort.json() == falsch.json()
    assert admin_client.get(f"/api/me/app-kopplung/{code['kennung']}", headers=kopf).json()["stand"] == "abgelaufen"


def test_ein_falsches_geheimnis_verbraucht_den_code_nicht(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    code = _code(admin_client, kopf)
    assert _abholen(admin_client, code, geheimnis="falsch").status_code == 404
    assert _abholen(admin_client, code).status_code == 200


def test_ein_beanspruchter_code_gibt_keinen_zweiten_schluessel(admin_client: TestClient) -> None:
    """Zwei Geraete, ein Code, derselbe Augenblick: Nur eines kommt durch.

    Nachgestellt wird der Zwischenstand, den das erste Geraet hinterlaesst,
    nachdem es den Code beansprucht und bevor es den Schluessel gemacht hat.
    """
    kopf = _erwachsener(admin_client)
    code = _code(admin_client, kopf)
    with SessionLocal() as db:
        db.get(AppKopplung, code["kennung"]).stand = "abholend"
        db.commit()
    vorher = _schluesselzahl()
    assert _abholen(admin_client, code).status_code == 404
    assert _schluesselzahl() == vorher


def test_zurueckgezogen_ist_weg(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    code = _code(admin_client, kopf)
    assert admin_client.delete(f"/api/me/app-kopplung/{code['kennung']}", headers=kopf).status_code == 204
    assert _abholen(admin_client, code).status_code == 404


def test_fremde_sehen_den_stand_nicht(admin_client: TestClient) -> None:
    code = _code(admin_client, _erwachsener(admin_client, "jonas"))
    fremd = _erwachsener(admin_client, "lena")
    assert admin_client.get(f"/api/me/app-kopplung/{code['kennung']}", headers=fremd).status_code == 404
    assert admin_client.delete(f"/api/me/app-kopplung/{code['kennung']}", headers=fremd).status_code == 404
    # Und zurueckgezogen hat der Fremde damit auch nichts.
    assert _abholen(admin_client, code).status_code == 200


def test_hoechstens_fuenf_offene_codes(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    for _ in range(app_kopplung.OFFEN_HOECHSTENS):
        _code(admin_client, kopf)
    zu_viel = admin_client.post(
        "/api/me/app-kopplung", json={"adresse": "https://kino.example.com"}, headers=kopf
    )
    assert zu_viel.status_code == 429
    assert zu_viel.json()["detail"]["code"] == "app_pairing_limit"


def test_ohne_geraetenamen_heisst_es_iphone(admin_client: TestClient) -> None:
    code = _code(admin_client, _erwachsener(admin_client))
    antwort = admin_client.post(
        f"/api/app/1/pairing/{code['kennung']}/key",
        headers={"Authorization": "", "X-Pairing-Secret": code["geheimnis"]},
    )
    assert antwort.status_code == 200, antwort.text
    with SessionLocal() as db:
        assert db.scalars(select(ApiKey.name)).all()[-1] == "iPhone"


def test_ein_stillgelegtes_konto_bekommt_keinen_schluessel(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    code = _code(admin_client, kopf)
    create_user(admin_client, "jonas", "passwort-1234", is_active=False)
    assert _abholen(admin_client, code).status_code == 404


# --- Kinder ----------------------------------------------------------------


def _elternteil_mit_kind(client: TestClient, name: str = "sabine", kind: str = "mia") -> tuple[dict, int]:
    kopf = _erwachsener(client, name, can_manage_children=True)
    antwort = client.post(
        "/api/children",
        json={"username": kind, "password": "kind-passwort", "age": 8},
        headers=kopf,
    )
    assert antwort.status_code in (200, 201), antwort.text
    return kopf, antwort.json()["id"]


def test_eltern_koppeln_das_geraet_ihres_kindes(admin_client: TestClient) -> None:
    eltern, kind_id = _elternteil_mit_kind(admin_client)
    code = _code(admin_client, eltern, f"/api/children/{kind_id}/app-kopplung")
    antwort = _abholen(admin_client, code, geraet="iPad von Mia")
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["account"]["role"] == "child"
    kind = _bearer(antwort.json()["key"])

    # Die Kinderadressen gehen ...
    assert admin_client.get("/api/app/1/kids/wishes", headers=kind).status_code == 200
    assert admin_client.get("/api/kids/wishes", headers=kind).status_code == 200

    # ... die Erwachsenenadressen nicht, auch nicht mit einem Schluessel.
    for pfad in (
        "/api/app/1/me",
        "/api/app/1/requests/mine",
        "/api/app/1/requests/quota",
        "/api/app/1/approvals",
        "/api/app/1/wishes",
        "/api/app/1/search/movie?q=test",
        "/api/requests/mine",
        "/api/v1/me",
        "/api/auth/me/schluessel",
        "/api/users",
    ):
        assert admin_client.get(pfad, headers=kind).status_code == 403, pfad

    # Und ein Kind koppelt sich nicht selbst weitere Geraete.
    assert (
        admin_client.post(
            "/api/me/app-kopplung", json={"adresse": "https://kino.example.com"}, headers=kind
        ).status_code
        == 403
    )

    # Die Eltern sehen das Geraet und trennen es.
    geraete = admin_client.get(f"/api/children/{kind_id}/geraete", headers=eltern).json()
    assert [g["name"] for g in geraete] == ["iPad von Mia"]
    weg = admin_client.delete(f"/api/children/{kind_id}/geraete/{geraete[0]['id']}", headers=eltern)
    assert weg.status_code == 204
    assert admin_client.get("/api/app/1/kids/wishes", headers=kind).status_code == 401


def test_fremde_kinder_bleiben_unerreichbar(admin_client: TestClient) -> None:
    _, kind_id = _elternteil_mit_kind(admin_client, "sabine", "mia")
    fremd, _ = _elternteil_mit_kind(admin_client, "tom", "ben")
    fremder_code = admin_client.post(
        f"/api/children/{kind_id}/app-kopplung", json={"adresse": "https://kino.example.com"}, headers=fremd
    )
    assert fremder_code.status_code == 404
    assert admin_client.get(f"/api/children/{kind_id}/geraete", headers=fremd).status_code == 404


def test_ein_erwachsener_schluessel_kommt_nicht_in_die_kinderansicht(admin_client: TestClient) -> None:
    code = _code(admin_client, _erwachsener(admin_client))
    erwachsen = _bearer(_abholen(admin_client, code).json()["key"])
    assert admin_client.get("/api/app/1/kids/wishes", headers=erwachsen).status_code == 403


def test_ohne_kopplung_bekommt_ein_kind_weiter_keinen_schluessel(admin_client: TestClient) -> None:
    """Die Ausnahme ist eng: Nur die Kopplung der Eltern macht Kinderschluessel."""
    _elternteil_mit_kind(admin_client)
    kind = auth_headers(admin_client, "mia", "kind-passwort")
    antwort = admin_client.post("/api/auth/me/schluessel", json={"name": "heimlich"}, headers=kind)
    assert antwort.status_code == 403


# --- Die Selbstauskunft ----------------------------------------------------


def test_die_selbstauskunft_kennt_entscheider_und_eltern(admin_client: TestClient) -> None:
    eltern, _ = _elternteil_mit_kind(admin_client)
    ich = admin_client.get("/api/app/1/me", headers=eltern).json()
    assert ich["konto"]["hat_kinder"] is True
    assert ich["konto"]["entscheidet"] is False
    assert ich["kontingent_zeitraum"] in ("day", "week", "month")

    admin = admin_client.get("/api/app/1/me").json()
    assert admin["konto"]["entscheidet"] is True
    assert admin["konto"]["role"] == "admin"


def test_freigeben_nur_fuer_entscheider(admin_client: TestClient) -> None:
    benutzer = _erwachsener(admin_client)
    assert admin_client.get("/api/app/1/approvals", headers=benutzer).status_code == 403
    assert admin_client.get("/api/app/1/approvals/count", headers=benutzer).status_code == 403
    assert admin_client.get("/api/app/1/approvals").status_code == 200
    assert admin_client.get("/api/app/1/approvals/count").status_code == 200


# --- Link und QR-Code -------------------------------------------------------


def test_der_link_traegt_adresse_kennung_und_geheimnis(admin_client: TestClient) -> None:
    code = _code(admin_client, _erwachsener(admin_client))
    assert code["adresse"] == "https://kino.example.com"
    assert code["link"] == (
        "nexview://connect?address=https%3A%2F%2Fkino.example.com"
        f"&pairing={code['kennung']}&secret={code['geheimnis']}"
    )
    assert code["qr_svg"].startswith("<svg") and 'xmlns="http://www.w3.org/2000/svg"' in code["qr_svg"]


def test_die_oeffentliche_adresse_geht_vor(admin_client: TestClient) -> None:
    """Im Heimnetz unter einer IP aufgerufen, soll die App trotzdem die Adresse
    bekommen, unter der nexview auch unterwegs erreichbar ist."""
    assert admin_client.put("/api/settings", json={"public_url": "https://nexview.example.org"}).status_code == 200
    code = _code(admin_client, _erwachsener(admin_client))
    assert code["adresse"] == "https://nexview.example.org"


def test_ohne_adresse_kein_code(admin_client: TestClient) -> None:
    kopf = _erwachsener(admin_client)
    for koerper in ({}, {"adresse": "kein-link"}, {"adresse": "ftp://kino.example.com"}):
        antwort = admin_client.post("/api/me/app-kopplung", json=koerper, headers=kopf)
        assert antwort.status_code == 422, koerper
        assert antwort.json()["detail"]["code"] == "app_pairing_address"
    with SessionLocal() as db:
        assert db.scalars(select(AppKopplung)).all() == []


# --- Befunde der Pruefung vom 16.10.2026 ------------------------------------


def test_ohne_neuen_schluessel_heisst_es_nicht_verbunden(admin_client: TestClient) -> None:
    """Am Konto haengen schon 20 Schluessel: Die App bekommt 409, und der Browser
    darf dann nicht "Verbunden" zeigen."""
    from app.services import api_schluessel

    kopf = _erwachsener(admin_client)
    for nummer in range(api_schluessel.HOECHSTZAHL):
        assert admin_client.post(
            "/api/auth/me/schluessel", json={"name": f"alt {nummer}"}, headers=kopf
        ).status_code == 201
    code = _code(admin_client, kopf)
    antwort = _abholen(admin_client, code)
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "apikey_too_many"
    stand = admin_client.get(f"/api/me/app-kopplung/{code['kennung']}", headers=kopf).json()
    assert stand == {"stand": "fehlgeschlagen", "geraet": None}


def test_ein_haengender_code_gilt_als_abgelaufen_und_laesst_sich_zurueckziehen(
    admin_client: TestClient,
) -> None:
    kopf = _erwachsener(admin_client)
    code = _code(admin_client, kopf)
    with SessionLocal() as db:
        db.get(AppKopplung, code["kennung"]).stand = "abholend"
        db.commit()
    assert admin_client.get(f"/api/me/app-kopplung/{code['kennung']}", headers=kopf).json()["stand"] == "abgelaufen"
    assert admin_client.delete(f"/api/me/app-kopplung/{code['kennung']}", headers=kopf).status_code == 204
    with SessionLocal() as db:
        assert db.get(AppKopplung, code["kennung"]) is None


def test_ohne_recht_auf_kinderkonten_kein_geraet_fuers_kind(admin_client: TestClient) -> None:
    eltern, kind_id = _elternteil_mit_kind(admin_client)
    create_user(admin_client, "sabine", "passwort-1234", can_manage_children=False)
    antwort = admin_client.post(
        f"/api/children/{kind_id}/app-kopplung", json={"adresse": "https://kino.example.com"}, headers=eltern
    )
    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "children_not_allowed"


def test_ein_stillgelegtes_elternteil_bringt_dem_kind_keinen_schluessel(admin_client: TestClient) -> None:
    eltern, kind_id = _elternteil_mit_kind(admin_client)
    code = _code(admin_client, eltern, f"/api/children/{kind_id}/app-kopplung")
    create_user(admin_client, "sabine", "passwort-1234", is_active=False)
    assert _abholen(admin_client, code).status_code == 404
