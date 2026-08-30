"""OIDC, Stufe 3: die Verwaltung im Admin-Bereich.

Anlegen, Aendern, Loeschen mit Folgen-Warnung, Pruef-Knopf - und die Regel,
dass ohne oeffentliche Adresse gar nicht erst etwas entsteht.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import OidcLink, OidcProvider, User
from app.services import oidc as oidc_dienst
from app.services import settings_service
from app.security import hash_password, unusable_password

from .conftest import auth_headers, create_user
from .oidc_helfer import ISSUER, transport

NEU = {
    "slug": "firma",
    "label": "Firmen-SSO",
    "issuer_url": ISSUER,
    "client_id": "nexview",
    "client_secret": "sehr-geheim",
}


def _mit_adresse() -> None:
    with SessionLocal() as db:
        settings_service.save_settings(db, {"public_url": "https://nexview.beispiel.de"})
        db.commit()


def test_ohne_oeffentliche_adresse_kein_anlegen(admin_client: TestClient) -> None:
    antwort = admin_client.post("/api/admin/oidc", json=NEU)
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "oidc_no_public_url"


def test_anlegen_aendern_und_liste(admin_client: TestClient) -> None:
    _mit_adresse()
    antwort = admin_client.post("/api/admin/oidc", json=NEU)
    assert antwort.status_code == 201, antwort.text
    eintrag = antwort.json()
    assert eintrag["slug"] == "firma"
    assert eintrag["auto_create"] is False
    assert eintrag["enabled"] is True
    # Das Geheimnis verlaesst die Datenbank nie - auch nicht maskiert.
    assert eintrag["client_secret_vorschau"] == "••••"
    assert "client_secret" not in eintrag
    assert (
        eintrag["rueckkehr_adresse"]
        == "https://nexview.beispiel.de/api/auth/oidc/firma/callback"
    )

    # Und in der Datenbank liegt es verschluesselt, nicht woertlich.
    with SessionLocal() as db:
        roh = db.query(OidcProvider).one().client_secret
        assert roh.startswith("enc:") and "sehr-geheim" not in roh

    geaendert = admin_client.patch(
        f"/api/admin/oidc/{eintrag['id']}",
        json={"label": "Haustuer", "auto_create": True, "client_secret": ""},
    )
    assert geaendert.status_code == 200, geaendert.text
    assert geaendert.json()["label"] == "Haustuer"
    assert geaendert.json()["auto_create"] is True
    # Leeres Geheimnis heisst "behalten".
    with SessionLocal() as db:
        assert db.query(OidcProvider).one().client_secret == roh

    liste = admin_client.get("/api/admin/oidc")
    assert [e["label"] for e in liste.json()] == ["Haustuer"]


@pytest.mark.parametrize(
    ("feld", "wert", "kennung"),
    [
        ("slug", "Böses Kürzel!", "oidc_slug_invalid"),
        ("issuer_url", "sso.beispiel.de", "url_needs_scheme"),
    ],
)
def test_anlegen_weist_unbrauchbares_ab(
    admin_client: TestClient, feld: str, wert: str, kennung: str
) -> None:
    _mit_adresse()
    antwort = admin_client.post("/api/admin/oidc", json={**NEU, feld: wert})
    assert antwort.status_code == 422
    assert antwort.json()["detail"]["code"] == kennung


def test_doppeltes_kuerzel_und_doppelte_adresse(admin_client: TestClient) -> None:
    _mit_adresse()
    admin_client.post("/api/admin/oidc", json=NEU)

    gleiche_kennung = admin_client.post(
        "/api/admin/oidc", json={**NEU, "issuer_url": "https://anderes.beispiel.de"}
    )
    assert gleiche_kennung.status_code == 409
    assert gleiche_kennung.json()["detail"]["code"] == "oidc_slug_taken"

    gleiche_adresse = admin_client.post("/api/admin/oidc", json={**NEU, "slug": "zwei"})
    assert gleiche_adresse.status_code == 409
    assert gleiche_adresse.json()["detail"]["code"] == "oidc_issuer_taken"


def test_nur_fuer_administratoren(admin_client: TestClient) -> None:
    create_user(admin_client, "gast")
    kopf = auth_headers(admin_client, "gast", "passwort-1234")
    assert admin_client.get("/api/admin/oidc", headers=kopf).status_code == 403
    assert admin_client.post("/api/admin/oidc", json=NEU, headers=kopf).status_code == 403


def test_loeschen_warnt_vor_dem_aussperren(admin_client: TestClient) -> None:
    """Ein Konto, dessen einziger Weg dieser Anbieter ist, blockt das Loeschen -
    bis der Administrator es ausdruecklich ueberstimmt."""
    _mit_adresse()
    eintrag = admin_client.post("/api/admin/oidc", json=NEU).json()

    with SessionLocal() as db:
        # Ein Konto wie aus der automatischen Anlage ohne Adresse: kein
        # Passwort, keine bestaetigte Mail - nur die Verknuepfung.
        gefaehrdet = User(
            username="nur-sso",
            password_hash=unusable_password(),
            email=None,
            email_verified=False,
        )
        gefaehrdet.oidc_links.append(OidcLink(issuer=ISSUER, subject="p-1"))
        # Und eines mit Passwort - das darf in der Warnliste nicht auftauchen.
        sicher = User(username="mit-passwort", password_hash=hash_password("passwort-1234"))
        sicher.oidc_links.append(OidcLink(issuer=ISSUER, subject="p-2"))
        db.add_all([gefaehrdet, sicher])
        db.commit()

    folgen = admin_client.get(f"/api/admin/oidc/{eintrag['id']}/folgen").json()
    assert folgen["verknuepft"] == 2
    assert [k["username"] for k in folgen["gefaehrdet"]] == ["nur-sso"]

    abgelehnt = admin_client.delete(f"/api/admin/oidc/{eintrag['id']}")
    assert abgelehnt.status_code == 409
    assert abgelehnt.json()["detail"]["code"] == "oidc_would_lock_out_others"

    ueberstimmt = admin_client.delete(f"/api/admin/oidc/{eintrag['id']}?bestaetigt=true")
    assert ueberstimmt.status_code == 204
    with SessionLocal() as db:
        assert db.query(OidcProvider).count() == 0
        # Die Verknuepfungen bleiben - wer denselben Anbieter wieder eintraegt,
        # findet alles vor.
        assert db.query(OidcLink).count() == 2


def test_pruef_knopf(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _mit_adresse()
    eintrag = admin_client.post("/api/admin/oidc", json=NEU).json()

    oidc_dienst.cache_leeren()
    monkeypatch.setattr(
        oidc_dienst, "_client", httpx.AsyncClient(transport=transport())
    )
    gut = admin_client.post(f"/api/admin/oidc/{eintrag['id']}/pruefen").json()
    assert gut == {"ok": True, "code": None, "aussteller": ISSUER}

    def nichts_da(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("aus")

    monkeypatch.setattr(
        oidc_dienst, "_client", httpx.AsyncClient(transport=httpx.MockTransport(nichts_da))
    )
    schlecht = admin_client.post(f"/api/admin/oidc/{eintrag['id']}/pruefen").json()
    assert schlecht["ok"] is False
    assert schlecht["code"] == "oidc_provider_unreachable"
    oidc_dienst.cache_leeren()
