"""OIDC, Stufe 2: der Anmeldeweg von aussen - Knopfliste, Hinweg, Rueckweg.

Alles laeuft durch die echten Endpunkte mit dem TestClient; nur der Anbieter
selbst ist die Attrappe aus ``oidc_helfer``. Der TestClient fuehrt einen
Cookie-Speicher wie ein Browser - genau darauf stuetzt sich der Rueckweg.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.crypto import encrypt
from app.db import SessionLocal
from app.models import OidcProvider, User
from app.services import oidc as oidc_dienst
from app.services import settings_service
from .conftest import auth_headers, create_user
from .oidc_helfer import CLIENT_ID, ISSUER, transport

SLUG = "firma"


@pytest.fixture
def attrappe(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Der Anbieter als Attrappe; liefert den Draht fuer die Ausweis-Claims."""
    zustand: dict = {}
    oidc_dienst.cache_leeren()
    monkeypatch.setattr(
        oidc_dienst, "_client", httpx.AsyncClient(transport=transport(zustand))
    )
    yield zustand
    oidc_dienst.cache_leeren()


def anbieter_anlegen(
    *, auto_create: bool = False, enabled: bool = True, public_url: str = "http://testserver"
) -> None:
    with SessionLocal() as db:
        db.add(
            OidcProvider(
                slug=SLUG,
                label="Firmen-SSO",
                issuer_url=ISSUER,
                client_id=CLIENT_ID,
                client_secret=encrypt("sehr-geheim"),
                auto_create=auto_create,
                enabled=enabled,
            )
        )
        if public_url:
            settings_service.save_settings(db, {"public_url": public_url})
        db.commit()


def _hinweg(client: TestClient) -> dict[str, str]:
    """Den Hinweg gehen und die Werte aus der Weiterleitungs-Adresse lesen."""
    antwort = client.get(f"/api/auth/oidc/{SLUG}/login", follow_redirects=False)
    assert antwort.status_code == 302, antwort.text
    ziel = urlsplit(antwort.headers["location"])
    assert f"{ziel.scheme}://{ziel.netloc}{ziel.path}" == f"{ISSUER}/auth"
    return {k: v[0] for k, v in parse_qs(ziel.query).items()}


def _konten() -> int:
    with SessionLocal() as db:
        return db.query(User).count()


# ---------------------------------------------------------------------------
# Die Knopfliste
# ---------------------------------------------------------------------------


def test_liste_zeigt_nur_aktive_anbieter(client: TestClient) -> None:
    assert client.get("/api/auth/oidc").json() == []

    anbieter_anlegen()
    assert client.get("/api/auth/oidc").json() == [
        {"slug": "firma", "label": "Firmen-SSO"}
    ]

    with SessionLocal() as db:
        db.query(OidcProvider).update({"enabled": False})
        db.commit()
    assert client.get("/api/auth/oidc").json() == []


# ---------------------------------------------------------------------------
# Hinweg
# ---------------------------------------------------------------------------


def test_hinweg_traegt_alle_schutzwerte(client: TestClient, attrappe: dict) -> None:
    anbieter_anlegen()
    werte = _hinweg(client)
    assert werte["client_id"] == CLIENT_ID
    assert werte["response_type"] == "code"
    assert werte["redirect_uri"] == f"http://testserver/api/auth/oidc/{SLUG}/callback"
    assert werte["code_challenge_method"] == "S256"
    for pflicht in ("state", "nonce", "code_challenge"):
        assert werte.get(pflicht), f"{pflicht} fehlt in der Weiterleitung"
    assert "openid" in werte["scope"]
    # Das Anlauf-Cookie liegt jetzt im Speicher des "Browsers".
    assert client.cookies.get(oidc_dienst.COOKIE_NAME)


def test_unbekannter_und_abgeschalteter_anbieter_sind_404(client: TestClient) -> None:
    assert client.get("/api/auth/oidc/nix/login", follow_redirects=False).status_code == 404
    anbieter_anlegen(enabled=False)
    assert (
        client.get(f"/api/auth/oidc/{SLUG}/login", follow_redirects=False).status_code
        == 404
    )
    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=x&state=y", follow_redirects=False
    )
    assert antwort.status_code == 404


def test_ohne_oeffentliche_adresse_kommt_die_kennung(
    client: TestClient, attrappe: dict
) -> None:
    anbieter_anlegen(public_url="")
    antwort = client.get(f"/api/auth/oidc/{SLUG}/login", follow_redirects=False)
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/login?oidc_fehler=oidc_no_public_url"


# ---------------------------------------------------------------------------
# Rueckweg: anmelden
# ---------------------------------------------------------------------------


def test_kompletter_anmeldelauf(client: TestClient, attrappe: dict) -> None:
    """Vom Knopf bis zur Sitzung - der Weg, um den es geht."""
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"]}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.status_code == 303, antwort.text
    assert antwort.headers["location"] == "/login?oidc=angemeldet"

    # Der Tausch beim Anbieter trug den PKCE-Aufloeser und den Code.
    anfrage = attrappe["token_anfrage"]
    assert anfrage["grant_type"] == "authorization_code"
    assert anfrage["code"] == "einmal-code"
    assert anfrage["code_verifier"]

    # Das Erneuerungs-Cookie kam mit der Weiterleitung; das Anlauf-Cookie ist
    # weg. Damit holt sich die Oberflaeche ihr Zugangs-Token wie ueberall.
    assert client.cookies.get("nexview_refresh")
    assert not client.cookies.get(oidc_dienst.COOKIE_NAME)

    erneuert = client.post("/api/auth/refresh")
    assert erneuert.status_code == 200, erneuert.text
    token = erneuert.json()["access_token"]

    ich = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert ich.status_code == 200
    assert ich.json()["email"] == "oma@beispiel.de"


def test_falscher_state_wird_abgewiesen(client: TestClient, attrappe: dict) -> None:
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"]}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state=ein-fremder-wert",
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/login?oidc_fehler=oidc_state_mismatch"
    assert _konten() == 0
    assert not client.cookies.get("nexview_refresh")


def test_ohne_cookie_gilt_kein_rueckweg(client: TestClient, attrappe: dict) -> None:
    """Dieselbe Adresse, aber aus einem 'Browser', der nie losgelaufen ist."""
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"]}
    client.cookies.delete(oidc_dienst.COOKIE_NAME, path=oidc_dienst.cookie_pfad())

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.headers["location"] == "/login?oidc_fehler=oidc_state_mismatch"
    assert _konten() == 0


def test_abbruch_beim_anbieter(client: TestClient, attrappe: dict) -> None:
    anbieter_anlegen()
    _hinweg(client)
    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?error=access_denied", follow_redirects=False
    )
    assert antwort.headers["location"] == "/login?oidc_fehler=oidc_denied"


def test_ohne_auto_anlage_bleibt_die_tuer_zu(client: TestClient, attrappe: dict) -> None:
    """Der Standard: Ein Unbekannter weist sich korrekt aus - und bleibt draussen."""
    anbieter_anlegen(auto_create=False)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"]}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.headers["location"] == "/login?oidc_fehler=oidc_not_invited"
    assert _konten() == 0
    assert not client.cookies.get("nexview_refresh")


def test_kaputter_ausweis_scheitert_am_ende_des_rueckwegs(
    client: TestClient, attrappe: dict
) -> None:
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    # Absichtlich NICHT das nonce des Anlaufs - ein Ausweis aus einem anderen Lauf.
    attrappe["claims"] = {"nonce": "ein-ganz-anderer-lauf"}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.headers["location"] == "/login?oidc_fehler=oidc_token_invalid"
    assert _konten() == 0


# ---------------------------------------------------------------------------
# Rueckweg: verknuepfen und trennen
# ---------------------------------------------------------------------------


def _angemeldet(client: TestClient, name: str) -> dict[str, str]:
    create_user(client, name)
    return auth_headers(client, name, "passwort-1234")


def test_verknuepfen_und_trennen(admin_client: TestClient, attrappe: dict) -> None:
    anbieter_anlegen()
    kopf = _angemeldet(admin_client, "max")

    start = admin_client.post(f"/api/auth/oidc/{SLUG}/link/start", headers=kopf)
    assert start.status_code == 200, start.text
    ziel = urlsplit(start.json()["url"])
    werte = {k: v[0] for k, v in parse_qs(ziel.query).items()}
    attrappe["claims"] = {"nonce": werte["nonce"], "sub": "max-beim-sso", "email": None}

    antwort = admin_client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.status_code == 303, antwort.text
    assert antwort.headers["location"] == "/profile?oidc=verknuepft"

    with SessionLocal() as db:
        max_ = db.query(User).filter(User.username == "max").one()
        assert [z.subject for z in max_.oidc_links] == ["max-beim-sso"]

    geloest = admin_client.delete(f"/api/auth/oidc/{SLUG}/link", headers=kopf)
    assert geloest.status_code == 200, geloest.text
    nochmal = admin_client.delete(f"/api/auth/oidc/{SLUG}/link", headers=kopf)
    assert nochmal.status_code == 404


def test_fremde_identitaet_laesst_sich_nicht_verknuepfen(
    admin_client: TestClient, attrappe: dict
) -> None:
    """Was schon jemandem gehoert, wandert nicht auf ein zweites Konto."""
    anbieter_anlegen()

    for name in ("erste", "zweite"):
        kopf = _angemeldet(admin_client, name)
        start = admin_client.post(f"/api/auth/oidc/{SLUG}/link/start", headers=kopf)
        ziel = urlsplit(start.json()["url"])
        werte = {k: v[0] for k, v in parse_qs(ziel.query).items()}
        attrappe["claims"] = {
            "nonce": werte["nonce"],
            "sub": "dieselbe-person",
            "email": None,
        }
        antwort = admin_client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=c&state={werte['state']}",
            follow_redirects=False,
        )

    # Der zweite Versuch endet mit der Konflikt-Kennung im Profil.
    assert antwort.headers["location"] == "/profile?oidc_fehler=oidc_link_conflict"
    with SessionLocal() as db:
        zweite = db.query(User).filter(User.username == "zweite").one()
        assert zweite.oidc_links == []
