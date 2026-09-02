"""OIDC, Stufe 2: der Anmeldeweg von aussen - Knopfliste, Hinweg, Rueckweg.

Alles laeuft durch die echten Endpunkte mit dem TestClient; nur der Anbieter
selbst ist die Attrappe aus ``oidc_helfer``. Der TestClient fuehrt einen
Cookie-Speicher wie ein Browser - genau darauf stuetzt sich der Rueckweg.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.crypto import encrypt
from app.db import SessionLocal
from app.models import (
    AuthToken,
    OidcBlock,
    OidcProvider,
    Role,
    TokenPurpose,
    User,
    utcnow,
)
from app.services import oidc as oidc_dienst
from app.services import settings_service

from .conftest import auth_headers, create_user
from .oidc_helfer import (
    CLIENT_ID,
    FREMDER_SCHLUESSEL,
    ISSUER,
    signierte_auskunft,
    transport,
)

SLUG = "firma"


@pytest.fixture
def attrappe(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Der Anbieter als Attrappe; liefert den Draht fuer die Ausweis-Claims."""
    zustand: dict = {}
    oidc_dienst.cache_leeren()
    monkeypatch.setattr(
        oidc_dienst,
        "_client",
        # ⚠️ Die Zeitgrenze **muss** hier stehen und aus dem Dienst kommen.
        # Ohne sie greift httpx' Voreinstellung von 5 s, und die Attrappe
        # bildet die Wirklichkeit nicht mehr ab - dann laesst sich nicht mehr
        # pruefen, dass die Nachfrage frueher aufgibt als der Token-Tausch.
        httpx.AsyncClient(
            transport=transport(zustand), timeout=oidc_dienst.ZEITGRENZE_SEKUNDEN
        ),
    )
    yield zustand
    oidc_dienst.cache_leeren()


def anbieter_anlegen(
    *,
    slug: str = SLUG,
    issuer: str = ISSUER,
    auto_create: bool = False,
    enabled: bool = True,
    public_url: str = "http://testserver",
) -> None:
    with SessionLocal() as db:
        db.add(
            OidcProvider(
                slug=slug,
                label="Firmen-SSO",
                issuer_url=issuer,
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
        {"slug": "firma", "label": "Firmen-SSO", "issuer_url": ISSUER}
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
    assert antwort.headers["location"] == "/profil?oidc=verknuepft&reiter=anmeldung"

    with SessionLocal() as db:
        max_ = db.query(User).filter(User.username == "max").one()
        assert [z.subject for z in max_.oidc_links] == ["max-beim-sso"]

    geloest = admin_client.delete(f"/api/auth/oidc/link?issuer={ISSUER}", headers=kopf)
    assert geloest.status_code == 200, geloest.text
    nochmal = admin_client.delete(f"/api/auth/oidc/link?issuer={ISSUER}", headers=kopf)
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
    assert antwort.headers["location"] == "/profil?oidc_fehler=oidc_link_conflict&reiter=anmeldung"
    with SessionLocal() as db:
        zweite = db.query(User).filter(User.username == "zweite").one()
        assert zweite.oidc_links == []



def _konto_mit_adresse(adresse: str = "oma@beispiel.de", name: str = "oma") -> None:
    """Ein gewoehnliches Konto mit bestaetigter Adresse.

    Direkt in der Datenbank statt ueber ``create_user``: Das laeuft am Ende
    ueber ``/api/users`` und braucht damit einen angemeldeten Administrator -
    den gibt es auf diesem Weg nicht, und er wuerde den Rueckweg stoeren.
    """
    from app.models import Role
    from app.security import hash_password

    with SessionLocal() as db:
        db.add(
            User(
                username=name,
                email=adresse,
                email_verified=True,
                display_name=name,
                role=Role.user,
                password_hash=hash_password("passwort-1234"),
            )
        )
        db.commit()


# ---------------------------------------------------------------------------
# Die Adresse: wann sie als Bruecke taugt - und woher sie kommt
# ---------------------------------------------------------------------------
#
# ⚠️ **Diese Faelle waren nie geprueft.** ``oidc_helfer.ausweis`` setzt
# ``email_verified`` fest auf ``True``, und keine der drei OIDC-Testdateien hat
# das je ueberschrieben. Deshalb ist erst im Betrieb aufgefallen, dass mehrere
# verbreitete Anbieter ab Werk etwas anderes tun.


def test_unbestaetigte_adresse_verknuepft_nicht(client: TestClient, attrappe: dict) -> None:
    """⚠️ **Die Sicherheitseigenschaft, festgenagelt.**

    ``email_verified: false`` heisst ausdruecklich "dafuer buerge ich nicht".
    Wuerde Nexview trotzdem verknuepfen, legte sich jemand bei irgendeinem
    Anbieter ein Konto mit fremder Adresse an und uebernaehme darueber das
    fremde Nexview-Konto. Die Adresse ist hier dieselbe wie am Konto - genau
    das macht den Fall gefaehrlich.

    Der Fall ist seit authentik 2025.10 der Regelfall, nicht die Ausnahme.
    """
    _konto_mit_adresse()
    anbieter_anlegen(auto_create=False)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"], "email_verified": False}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    assert "oidc_fehler=oidc_not_invited" in antwort.headers["location"]
    # Keine Sitzung - und vor allem keine Verknuepfung.
    assert not client.cookies.get("nexview_refresh")
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "oma").one()
        assert konto.oidc_links == []


def test_adresse_kommt_aus_userinfo(client: TestClient, attrappe: dict) -> None:
    """Der Anbieter legt sie nicht in den Ausweis - Authelia und Zitadel ab Werk.

    Ohne die Nachfrage kaeme in Nexview gar keine Adresse an, und die Bruecke
    zu einem bestehenden Konto wuerde nicht etwa falsch bewertet, sondern nie
    betreten.
    """
    _konto_mit_adresse()
    anbieter_anlegen(auto_create=False)
    werte = _hinweg(client)
    # Kein ``email`` im Ausweis - dafuer an ``userinfo``.
    attrappe["claims"] = {"nonce": werte["nonce"], "email": None, "email_verified": None}
    attrappe["userinfo"] = {
        "sub": "person-1",
        "email": "oma@beispiel.de",
        "email_verified": True,
    }

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.status_code == 303, antwort.text
    assert antwort.headers["location"] == "/login?oidc=angemeldet"
    # Mit dem Zugangs-Ausweis gefragt, nicht ohne.
    assert attrappe["userinfo_kopf"] == "Bearer zugang-1"
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "oma").one()
        assert len(konto.oidc_links) == 1


def test_userinfo_mit_fremdem_subject_wird_verworfen(
    client: TestClient, attrappe: dict
) -> None:
    """⚠️ Ohne diese Pruefung liesse sich einer beglaubigten Anmeldung die
    Adresse einer fremden anhaengen. Die Norm verlangt sie (Core 5.3.2)."""
    _konto_mit_adresse()
    anbieter_anlegen(auto_create=False)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"], "email": None, "email_verified": None}
    attrappe["userinfo"] = {
        "sub": "jemand-anderes",
        "email": "oma@beispiel.de",
        "email_verified": True,
    }

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    # Die Antwort wird verworfen: keine Adresse, also keine Bruecke.
    assert "oidc_fehler=oidc_not_invited" in antwort.headers["location"]
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "oma").one()
        assert konto.oidc_links == []


def test_stummes_userinfo_macht_nichts_kaputt(client: TestClient, attrappe: dict) -> None:
    """Ein Fehlschlag beim Nachfragen darf keine Anmeldung umreissen.

    Der Rueckfall ist "keine zusaetzliche Auskunft" - hier fuehrt das zur
    gewoehnlichen Abweisung, nicht zu einem Absturz. Wer die Adresse im
    Ausweis mitschickt, merkt von einem stummen Endpunkt ohnehin nichts.
    """
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"], "email": None, "email_verified": None}
    attrappe["userinfo_status"] = 500

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    # Auto-Anlage ist an: Es entsteht ein Konto - eben ohne Adresse.
    assert antwort.status_code == 303, antwort.text
    assert antwort.headers["location"] == "/login?oidc=angemeldet"
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "oma").one()
        assert konto.email is None


def test_ohne_userinfo_endpunkt_wird_nicht_gefragt(
    client: TestClient, attrappe: dict
) -> None:
    """Nennt die Selbstauskunft keinen Endpunkt, gibt es nichts zu fragen."""
    anbieter_anlegen(auto_create=True)
    attrappe["ohne_userinfo"] = True
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"], "email": None, "email_verified": None}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.status_code == 303, antwort.text
    assert "userinfo_kopf" not in attrappe


def test_der_ausweis_behaelt_das_letzte_wort(client: TestClient, attrappe: dict) -> None:
    """⚠️ Gefragt wird immer - aber die Antwort sticht den Ausweis nicht.

    Der Ausweis ist signiert und geprueft, die Nachfrage haengt allein am
    ``sub``-Abgleich. Widersprechen sich beide, gilt der Ausweis. Sonst liesse
    sich ueber eine schwaecher gesicherte Auskunft aushebeln, wofuer der
    Anbieter unterschrieben hat.
    """
    _konto_mit_adresse()
    # Ohne Auto-Anlage: Hier geht es allein um die Frage, ob verknuepft wird.
    anbieter_anlegen(auto_create=False)
    werte = _hinweg(client)
    # Der Ausweis sagt "nicht bestaetigt", userinfo behauptet das Gegenteil.
    attrappe["claims"] = {"nonce": werte["nonce"], "email_verified": False}
    attrappe["userinfo"] = {
        "sub": "person-1",
        "email": "oma@beispiel.de",
        "email_verified": True,
    }

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert "oidc_fehler=oidc_not_invited" in antwort.headers["location"]
    # Gefragt wurde - der Weg laeuft jetzt bei jeder Anmeldung.
    assert attrappe["userinfo_kopf"] == "Bearer zugang-1"
    # Aber verknuepft wurde nicht: Der Ausweis sagt "nicht bestaetigt".
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "oma").one()
        assert konto.oidc_links == []



def test_belegte_adresse_stuerzt_nicht_ab(client: TestClient, attrappe: dict) -> None:
    """⚠️ **Der Fall, der vorher eine Fehlerseite ergab.**

    Automatische Anlage an, der Anbieter buergt nicht fuer die Adresse (bei
    authentik, Keycloak und Pocket ID die Werkseinstellung), und die Adresse
    gehoert schon einem Konto: Die Bruecke bleibt zu Recht zu, die Anlage
    laeuft in den eindeutigen Index - und die Ausnahme flog bis nach oben.

    Ein Administrator erwartet hier keine Fehlerseite, sondern eine Abweisung,
    deren Grund im Protokoll steht.
    """
    _konto_mit_adresse()
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"], "email_verified": False}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.status_code == 303, antwort.text
    assert "oidc_fehler=oidc_not_invited" in antwort.headers["location"]

    # Kein zweites Konto, keine Sitzung, und das vorhandene bleibt unberuehrt.
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == "oma@beispiel.de").count() == 1
        konto = db.query(User).filter(User.username == "oma").one()
        assert konto.oidc_links == []
    assert not client.cookies.get("nexview_refresh")


def test_kaputte_userinfo_adresse_reisst_nichts_um(
    client: TestClient, attrappe: dict
) -> None:
    """⚠️ Ein Tippfehler in der Anbieter-Beschreibung darf niemanden aussperren.

    Die Nachfrage bei ``userinfo`` ist entbehrlich - ihr Ausbleiben kostet
    hoechstens eine Auskunft. Steht in der Selbstauskunft aber eine Adresse,
    an der sich httpx schon beim Zerlegen verschluckt (``InvalidURL``, erbt
    direkt von ``Exception``), riss das frueher die ganze Anmeldung um: Der
    Faenger hoerte auf ``httpx.HTTPError``, und daran ging es vorbei.

    Wer ohne diese Nachfrage hereinkaeme, muss auch mit ihr hereinkommen.
    """
    anbieter_anlegen(auto_create=True)
    attrappe["kaputte_userinfo"] = True
    werte = _hinweg(client)
    # Ohne Adresse im Ausweis - genau der Fall, in dem nachgefragt wird.
    attrappe["claims"] = {"nonce": werte["nonce"], "email": None, "email_verified": None}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )

    # Angemeldet, nicht abgestuerzt - eben ohne die Auskunft, die nie kam.
    assert antwort.status_code == 303, antwort.text
    assert antwort.headers["location"] == "/login?oidc=angemeldet"
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "oma").one().email is None
    # Der Transport hat die Anfrage nie gesehen: Die Ausnahme entstand beim
    # Zerlegen der Adresse, nicht beim Senden.
    assert "/userinfo" not in attrappe["zeitgrenzen"]


def test_die_nachfrage_gibt_frueher_auf_als_der_token_tausch(
    client: TestClient, attrappe: dict
) -> None:
    """⚠️ Die entbehrliche Frage bekommt die kurze Leine.

    Die Nachfrage haengt an **jeder** Anmeldung, und ihr Ausbleiben ist
    verkraftbar. Haengt sie an derselben Zeitgrenze wie der Token-Tausch,
    verlangsamt ein traeger Anbieter jede Anmeldung im Haus um bis zu zehn
    Sekunden - fuer eine Auskunft, die vielleicht gar nicht kommt. Der
    Token-Tausch ist das Gegenteil: ohne ihn gibt es keine Anmeldung.

    Gemessen wird an ``request.extensions["timeout"]``, nicht an einer Uhr -
    ein Test, der wartet, misst die Testmaschine mit.
    """
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"]}
    attrappe["userinfo"] = {"sub": "person-1"}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.status_code == 303, antwort.text

    grenzen = attrappe["zeitgrenzen"]
    tausch = grenzen["/token"]["read"]
    nachfrage = grenzen["/userinfo"]["read"]
    assert tausch == oidc_dienst.ZEITGRENZE_SEKUNDEN, grenzen
    assert nachfrage == oidc_dienst.NACHFRAGE_SEKUNDEN, grenzen
    # Die eigentliche Zusage - nicht die konkreten Zahlen, sondern ihr
    # Verhaeltnis. Wer die Grenzen spaeter dreht, muss das hier lesen.
    assert nachfrage < tausch


# ---------------------------------------------------------------------------
# Die Unterschrifts-Verfahren am ganzen Weg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verfahren", ["ES512", "EdDSA"])
def test_anmeldelauf_mit_neuem_verfahren(
    client: TestClient, attrappe: dict, verfahren: str
) -> None:
    """⚠️ **Was in ``ALGORITHMEN`` fehlt, sperrt aus - hier vom Knopf bis zur
    Sitzung.**

    ES512 fehlte, obwohl ES256 und ES384 dastanden, EdDSA fehlte ganz; Pocket
    ID laesst beides einstellen. Bei einem so eingestellten Anbieter scheiterte
    **jede** Anmeldung, und im Browser stand nur, der Ausweis lasse sich nicht
    pruefen. Die Einzelpruefung steht in ``test_oidc_dienst``; dieser Lauf
    zeigt, dass auch nichts dazwischen im Weg steht.
    """
    anbieter_anlegen(auto_create=True)
    attrappe["verfahren"] = verfahren
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"]}

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.status_code == 303, antwort.text
    assert antwort.headers["location"] == "/login?oidc=angemeldet"
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == "oma@beispiel.de").count() == 1


# ---------------------------------------------------------------------------
# Signiertes userinfo (application/jwt)
# ---------------------------------------------------------------------------


def test_signiertes_userinfo_baut_die_bruecke(client: TestClient, attrappe: dict) -> None:
    """Authelia und Zitadel koennen die Auskunft unterschrieben liefern.

    Genau die beiden Anbieter, wegen denen die Nachfrage ueberhaupt existiert -
    und genau bei ihnen fiel eine unterschriebene Antwort vorher **still**
    durch. Von aussen sah das aus wie "Nexview holt die Adresse einfach nicht".
    """
    _konto_mit_adresse()
    anbieter_anlegen(auto_create=False)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"], "email": None, "email_verified": None}
    attrappe["userinfo_jwt"] = signierte_auskunft()

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert antwort.status_code == 303, antwort.text
    assert antwort.headers["location"] == "/login?oidc=angemeldet"
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "oma").one()
        assert len(konto.oidc_links) == 1


@pytest.mark.parametrize(
    ("was", "gebaut"),
    [
        ("fremde Unterschrift", {"schluessel": FREMDER_SCHLUESSEL}),
        ("fremder Empfaenger", {"aud": "eine-andere-app"}),
    ],
)
def test_unterschobenes_userinfo_oeffnet_kein_fremdes_konto(
    client: TestClient, attrappe: dict, was: str, gebaut: dict
) -> None:
    """⚠️ **Der Angriff, gegen den die gemeinsame Pruefung steht.**

    Wer sich beim Anbieter beglaubigt anmelden kann, aber die ``userinfo``-
    Antwort faelschen oder aus einer anderen Anwendung mitbringen kann, wuerde
    darueber ein fremdes Nexview-Konto uebernehmen: Die Adresse ist die
    Bruecke, und wer die Adresse setzen darf, waehlt das Konto.

    Deshalb geht die signierte Auskunft durch dieselbe Pruefung wie der
    Ausweis - dieselben Schluessel, dasselbe ``iss``, dasselbe ``aud``. Was sie
    nicht besteht, ist **weg**, nicht etwa "ungeprueft uebernommen".
    """
    _konto_mit_adresse()
    anbieter_anlegen(auto_create=False)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"], "email": None, "email_verified": None}
    attrappe["userinfo_jwt"] = signierte_auskunft(**gebaut)

    antwort = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert "oidc_fehler=oidc_not_invited" in antwort.headers["location"], was
    assert not client.cookies.get("nexview_refresh"), was
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "oma").one()
        assert konto.oidc_links == [], was


# ---------------------------------------------------------------------------
# Jeder Abbruch hinterlaesst eine Zeile
# ---------------------------------------------------------------------------
#
# ⚠️ **Warum das eine Testreihe wert ist.** Ein gescheiterter Rueckweg sieht
# von aussen immer gleich aus: Der Browser steht auf der Anmeldeseite und
# zeigt einen Satz, der absichtlich wenig verraet. Steht dazu nichts im
# Protokoll, hat der Betreiber keinen einzigen Anhaltspunkt - und elf
# Abbruchwege endeten frueher stumm. Geprueft wird deshalb nicht nur die
# Kennung nach aussen, sondern der **Grund** im Protokoll.


def test_fehler_des_anbieters_erreicht_nutzer_und_protokoll(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """``?error=access_denied`` - der haeufigste Rueckweg, der keiner ist.

    ⚠️ Die Pruefung steht **vor** der des ``state``: Ein Rueckweg mit ``error``
    traegt keinen ``code`` und nicht zwingend einen brauchbaren ``state``.
    Weiter unten wuerde daraus ``oidc_state_mismatch`` - ein Nebenbefund, der
    den Anmeldenden zum vergeblichen Wiederholen schickt, statt ihm zu sagen,
    dass der Anbieter abgelehnt hat.
    """
    anbieter_anlegen()
    _hinweg(client)
    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        antwort = client.get(
            f"/api/auth/oidc/{SLUG}/callback"
            "?error=access_denied&error_description=User+cancelled+the+consent",
            follow_redirects=False,
        )
    assert antwort.headers["location"] == "/login?oidc_fehler=oidc_denied"
    assert "access_denied" in caplog.text
    # Der Wortlaut des Anbieters - er nennt oft genau das falsche Feld.
    assert "User cancelled the consent" in caplog.text


def test_fremdtext_schreibt_keine_eigene_protokollzeile(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """⚠️ Die Rueckkehr-Adresse steht offen im Netz.

    Wer sie aufruft, waehlt ``error_description`` frei. Ohne ``%r`` schriebe er
    sich mit einem Zeilenumbruch eigene Zeilen ins Protokoll - und ein
    Protokoll, in das Fremde schreiben duerfen, taugt zu gar nichts mehr.
    Geprueft wird an der **Zeilenzahl**, nicht am Text: ``repr()`` macht aus
    dem Umbruch die zwei Zeichen ``\\n``.
    """
    anbieter_anlegen()
    _hinweg(client)
    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        client.get(
            f"/api/auth/oidc/{SLUG}/callback"
            "?error=access_denied&error_description=harmlos%0AOIDC:+alles+in+Ordnung",
            follow_redirects=False,
        )
    zeilen = [z for z in caplog.messages if "OIDC callback refused" in z]
    assert len(zeilen) == 1
    assert "\\n" in zeilen[0]
    assert "\n" not in zeilen[0]


def test_die_vier_zustands_abbrueche_stehen_getrennt_im_protokoll(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """Nach aussen eine Kennung, im Protokoll vier Saetze.

    ``oidc_state_mismatch`` heisst fuer den Anmeldenden immer dasselbe. Fuer
    den Betreiber sind es vier sehr verschiedene Lagen, und nur eine davon
    riecht nach Angriff: ein abgelaufenes Cookie, ein Rueckweg ohne ``code``,
    ein Cookie von einem anderen Anbieter - oder ein gefaelschter ``state``.
    """
    anbieter_anlegen()
    anbieter_anlegen(slug="zweite", issuer="https://zweite.beispiel.de", public_url="")

    # 1. Gar kein Cookie: ein Browser, der nie losgelaufen ist.
    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=c&state=s", follow_redirects=False
        )
    assert "cookie missing or expired" in caplog.text

    # 2. Cookie da, aber der Anbieter schickte weder code noch state.
    caplog.clear()
    _hinweg(client)
    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        client.get(f"/api/auth/oidc/{SLUG}/callback", follow_redirects=False)
    assert "callback without code or state" in caplog.text

    # 3. Das Cookie gehoert zu einem anderen Anbieter - zwei Knoepfe, zwei
    #    Tabs, der falsche kam zurueck.
    caplog.clear()
    werte = _hinweg(client)
    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        client.get(
            f"/api/auth/oidc/zweite/callback?code=c&state={werte['state']}",
            follow_redirects=False,
        )
    assert "belongs to provider 'firma'" in caplog.text

    # 4. Der state passt nicht - der einzige Fall, der nach Angriff riecht.
    caplog.clear()
    _hinweg(client)
    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=c&state=ein-fremder-wert",
            follow_redirects=False,
        )
    assert "state does not match" in caplog.text


def test_zustands_abbrueche_bleiben_unter_der_warnschwelle(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """⚠️ **Die Stufe folgt einer Regel, nicht dem Gefuehl.**

    Alles **vor** der Anmeldebremse laesst sich ohne jeden Anbieter beliebig
    oft erzeugen - die Rueckkehr-Adresse steht offen im Netz. Stuende das auf
    WARNING, schriebe ein Fremder dem Betreiber das Protokoll voll. Was
    **nach** ihr scheitert, hat einen echten Lauf beim Anbieter hinter sich
    und ist eine Warnung wert.
    """
    anbieter_anlegen(auto_create=True)

    caplog.set_level(logging.INFO, logger="nexview.oidc")
    client.get(f"/api/auth/oidc/{SLUG}/callback?code=c&state=s", follow_redirects=False)
    assert [s.levelno for s in caplog.records if "callback refused" in s.message] == [
        logging.INFO
    ]

    # Und jetzt einer, der beim Anbieter war: derselbe Satz, eine Stufe hoeher.
    caplog.clear()
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": "ein-ganz-anderer-lauf"}
    client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=c&state={werte['state']}",
        follow_redirects=False,
    )
    stufen = [s.levelno for s in caplog.records if "callback refused" in s.message]
    assert stufen == [logging.WARNING]


def test_gescheiterter_lauf_beim_anbieter_nennt_die_kennung(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """Der Fall, den der Betreiber am haeufigsten sucht: falsches Geheimnis,
    abgelaufener Schluessel, Anbieter nicht erreichbar.

    ⚠️ Im Protokoll steht die **Kennung**, nicht der deutsche Satz - unsere
    Fehlertexte sind der Rueckfall fuer die Oberflaeche, und wer sie
    einsetzt, schmuggelt Deutsch am Sprach-Waechter vorbei.
    """
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": "ein-ganz-anderer-lauf"}

    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        antwort = client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
            follow_redirects=False,
        )
    assert antwort.headers["location"] == "/login?oidc_fehler=oidc_token_invalid"
    assert "the run at the provider failed" in caplog.text
    assert "oidc_token_invalid" in caplog.text


def test_abgewiesenes_konto_nennt_grund_und_gekuerzte_adresse(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """⚠️ **Der Fall, der den Betreiber ratlos liess.**

    Der Anbieter meldet ``email_verified: false`` (bei authentik, Keycloak und
    Pocket ID die Werkseinstellung), die Bruecke bleibt zu, der Anmeldende
    sieht "noch kein Konto". Ohne Zeile im Protokoll sucht der Betreiber im
    Anbieter herum, waehrend die Auskunft ein Haken entfernt ist.

    Die Adresse steht **gekuerzt** da: Ein Protokoll laeuft wochenlang mit und
    wird beim Melden eines Fehlers angehaengt.
    """
    _konto_mit_adresse()
    anbieter_anlegen(auto_create=False)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"], "email_verified": False}

    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
            follow_redirects=False,
        )
    # Der Grund aus ``oidc_accounts`` ...
    assert "address not confirmed by the provider" in caplog.text
    # ... samt der Auskunft, die den Unterschied macht: Es gaebe ein Konto.
    assert "account_exists=True" in caplog.text
    # ... und der Zusammenhang aus dem Rueckweg.
    assert "no account for this identity" in caplog.text
    # Gekuerzt, nicht vollstaendig - und trotzdem wiedererkennbar.
    assert "om***@beispiel.de" in caplog.text
    assert "oma@beispiel.de" not in caplog.text


def test_unbekannter_anbieter_hinterlaesst_eine_zeile(
    client: TestClient, caplog
) -> None:
    """Der 404 ist einer der drei Ausgaenge, die keine Weiterleitung sind.

    ⚠️ Nur INFO: Die Adresse steht offen im Netz, und jeder Aufruf mit
    geratenem Kuerzel kaeme sonst als Warnung ins Protokoll. Fuer den
    Betreiber zaehlt der Unterschied trotzdem - ein gerade abgeschalteter
    Anbieter sieht fuer den Benutzer wie ein kaputter Knopf aus.
    """
    anbieter_anlegen(enabled=False)
    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        antwort = client.get(f"/api/auth/oidc/{SLUG}/login", follow_redirects=False)
    assert antwort.status_code == 404
    passend = [s for s in caplog.records if "no such provider" in s.message]
    assert [s.levelno for s in passend] == [logging.INFO]


def test_ein_fremder_sperrt_niemanden_aus(client: TestClient, attrappe: dict) -> None:
    """⚠️ **Die Bremse zaehlt nie auf das Anbieter-Kuerzel.**

    Hier stand einmal ``slug`` als Kennung, und damit war die Bremse eine
    Waffe statt eines Schutzes: Ein Fremder ohne Konto und ohne Passwort holte
    sich ein eigenes Anlauf-Cookie, kehrte zehnmal mit erfundenem ``code``
    zurueck - und danach kam **niemand** mehr ueber diesen Anbieter herein.
    Ohne Angreifer traf es dasselbe nach einem falsch abgetippten
    Client-Geheimnis: Der zehnte ehrliche Versuch sperrte das ganze Haus aus.

    An diesem Endpunkt gibt es kein Geheimnis zu erraten - der ``code`` kommt
    vom Anbieter, ist einmalig und ohne den PKCE-Aufloeser aus dem Cookie
    wertlos. Eine Bremse, die hier alle trifft, richtet an, statt zu schuetzen.

    Der Test bildet zwei Browser nach: Der erste scheitert reihenweise, der
    zweite muss danach ungehindert hereinkommen.
    """
    anbieter_anlegen(auto_create=True)

    for _ in range(12):
        werte = _hinweg(client)
        attrappe["claims"] = {"nonce": "ein-ganz-anderer-lauf"}
        antwort = client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=c&state={werte['state']}",
            follow_redirects=False,
        )
        # Abgewiesen ja - aber nie mit 429, sonst haenge der Zaehler wieder am
        # Kuerzel und der naechste Browser waere schon jetzt ausgesperrt.
        assert antwort.status_code == 303, antwort.text

    # Ein anderer Browser, ein sauberer Lauf: Er kommt herein.
    zweiter = TestClient(client.app, base_url=str(client.base_url))
    antwort = zweiter.get(f"/api/auth/oidc/{SLUG}/login", follow_redirects=False)
    assert antwort.status_code == 302, antwort.text
    werte = {k: v[0] for k, v in parse_qs(urlsplit(antwort.headers["location"]).query).items()}
    attrappe["claims"] = {"nonce": werte["nonce"]}

    fertig = zweiter.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert fertig.status_code == 303, fertig.text
    assert fertig.headers["location"] == "/login?oidc=angemeldet"
    assert zweiter.cookies.get("nexview_refresh")


def test_hinweg_fehler_kommt_ins_protokoll(client: TestClient, caplog) -> None:
    """Der Browser ist gleich wieder auf der Anmeldeseite - ohne diese Zeile
    bliebe der Grund nirgends stehen, und genau er gehoert dem Betreiber."""
    anbieter_anlegen(public_url="")
    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        antwort = client.get(f"/api/auth/oidc/{SLUG}/login", follow_redirects=False)
    assert antwort.headers["location"] == "/login?oidc_fehler=oidc_no_public_url"
    assert "could not be started" in caplog.text
    # ⚠️ Die Kennung, nicht der deutsche Satz.
    assert "oidc_no_public_url" in caplog.text
    assert "öffentliche" not in caplog.text


# ---------------------------------------------------------------------------
# Verknuepfen: die vier stummen Abbrueche und die Zeile beim Gelingen
# ---------------------------------------------------------------------------


def _link_hinweg(client: TestClient, kopf: dict[str, str]) -> dict[str, str]:
    start = client.post(f"/api/auth/oidc/{SLUG}/link/start", headers=kopf)
    assert start.status_code == 200, start.text
    ziel = urlsplit(start.json()["url"])
    return {k: v[0] for k, v in parse_qs(ziel.query).items()}


def test_gelungenes_verknuepfen_steht_im_protokoll(
    admin_client: TestClient, attrappe: dict, caplog
) -> None:
    """Eine neue Verknuepfung ist ein neuer Weg in dieses Konto hinein.

    Und es haelt das Protokoll ehrlich: Staenden dort nur die Fehlschlaege,
    saehe ein Anbieter, an dem alles klappt, aus wie einer, an dem nichts geht.
    """
    anbieter_anlegen()
    kopf = _angemeldet(admin_client, "max")
    werte = _link_hinweg(admin_client, kopf)
    attrappe["claims"] = {"nonce": werte["nonce"], "sub": "max-beim-sso"}

    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        antwort = admin_client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=c&state={werte['state']}",
            follow_redirects=False,
        )
    assert antwort.headers["location"] == "/profil?oidc=verknuepft&reiter=anmeldung"
    assert "linked OIDC provider" in caplog.text
    assert "om***@beispiel.de" in caplog.text


def test_verknuepfen_mit_abgeschaltetem_konto(
    admin_client: TestClient, attrappe: dict, caplog
) -> None:
    """Das Konto wurde abgeschaltet, waehrend der Browser beim Anbieter war.

    Nach aussen bleibt es "diese Anmeldung passt nicht zu diesem Browser" -
    im Protokoll steht, was wirklich war.
    """
    anbieter_anlegen()
    kopf = _angemeldet(admin_client, "max")
    werte = _link_hinweg(admin_client, kopf)
    attrappe["claims"] = {"nonce": werte["nonce"], "sub": "max-beim-sso"}
    with SessionLocal() as db:
        db.query(User).filter(User.username == "max").update({"is_active": False})
        db.commit()

    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        antwort = admin_client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=c&state={werte['state']}",
            follow_redirects=False,
        )
    assert "oidc_fehler=oidc_state_mismatch" in antwort.headers["location"]
    assert "the account is switched off" in caplog.text


def test_verknuepfen_mit_geloeschtem_konto(
    admin_client: TestClient, attrappe: dict, caplog
) -> None:
    """Dieselbe Kennung nach aussen, ein ganz anderer Satz im Protokoll."""
    anbieter_anlegen()
    kopf = _angemeldet(admin_client, "max")
    werte = _link_hinweg(admin_client, kopf)
    attrappe["claims"] = {"nonce": werte["nonce"], "sub": "max-beim-sso"}
    with SessionLocal() as db:
        db.delete(db.query(User).filter(User.username == "max").one())
        db.commit()

    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        admin_client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=c&state={werte['state']}",
            follow_redirects=False,
        )
    assert "no longer exists" in caplog.text


def test_verknuepfen_mit_gesperrter_identitaet(
    admin_client: TestClient, attrappe: dict, caplog
) -> None:
    """Die Sperrliste gilt auch fuer den Weg ueber das Profil.

    Sonst waere sie mit zwei Klicks zu umgehen: Konto anlegen lassen, dort
    anmelden, die gesperrte Identitaet daran haengen.
    """
    anbieter_anlegen()
    kopf = _angemeldet(admin_client, "max")
    with SessionLocal() as db:
        db.add(OidcBlock(issuer=ISSUER, subject="max-beim-sso"))
        db.commit()
    werte = _link_hinweg(admin_client, kopf)
    attrappe["claims"] = {"nonce": werte["nonce"], "sub": "max-beim-sso"}

    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        antwort = admin_client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=c&state={werte['state']}",
            follow_redirects=False,
        )
    assert "oidc_fehler=oidc_blocked" in antwort.headers["location"]
    assert "on the block list" in caplog.text
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "max").one().oidc_links == []


def test_verknuepfungs_konflikt_steht_im_protokoll(
    admin_client: TestClient, attrappe: dict, caplog
) -> None:
    """Was schon jemandem gehoert, wandert nicht auf ein zweites Konto - und
    im Protokoll steht, **wem** es gehoert."""
    anbieter_anlegen()
    for name in ("erste", "zweite"):
        kopf = _angemeldet(admin_client, name)
        werte = _link_hinweg(admin_client, kopf)
        attrappe["claims"] = {
            "nonce": werte["nonce"],
            "sub": "dieselbe-person",
            "email": None,
        }
        with caplog.at_level(logging.INFO, logger="nexview.oidc"):
            admin_client.get(
                f"/api/auth/oidc/{SLUG}/callback?code=c&state={werte['state']}",
                follow_redirects=False,
            )
    assert "already belongs to account 'erste'" in caplog.text


# ---------------------------------------------------------------------------
# Die uebergangene Einladung
# ---------------------------------------------------------------------------


def _einladung(adresse: str = "oma@beispiel.de") -> None:
    with SessionLocal() as db:
        db.add(
            AuthToken(
                purpose=TokenPurpose.invitation,
                token_hash=f"test-einladung-{adresse}",
                email=adresse,
                expires_at=utcnow().replace(tzinfo=None).replace(year=2999),
                invite_role=Role.approver,
                invite_quota_movies=3,
            )
        )
        db.commit()


def test_uebergangene_einladung_wird_gemeldet(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """⚠️ **Der Betreiber hat etwas eingestellt, das lautlos verfiel.**

    Rolle und Kontingent hat er von Hand vergeben. Meldet der Anbieter
    ``email_verified: false``, faellt die Einladung weg - das Konto entsteht
    trotzdem, aber mit den Standardwerten des Hauses. Benutzt wird die
    Einladung ausdruecklich **nicht**: Sie zu verwerten hiesse, eine
    unbeglaubigte Adresse als Ausweis zu nehmen, und genau davor steht die
    Bedingung. Sie bleibt offen und uneingeloest.
    """
    _einladung()
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"], "email_verified": False}

    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        antwort = client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
            follow_redirects=False,
        )
    assert antwort.headers["location"] == "/login?oidc=angemeldet"
    assert "an open invitation exists" in caplog.text
    assert "om***@beispiel.de" in caplog.text
    with SessionLocal() as db:
        konto = db.query(User).filter(User.email == "oma@beispiel.de").one()
        assert konto.role == Role.user
        assert konto.quota_movies_limit is None
        # Die Einladung ist **nicht** verbraucht.
        assert db.query(AuthToken).one().used_at is None


def test_mit_bestaetigter_adresse_gilt_die_einladung_und_niemand_warnt(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """Die Gegenprobe. Eine Warnung, die auch im Normalfall kommt, liest bald
    niemand mehr - und dieser Fall ist der Normalfall."""
    _einladung()
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {"nonce": werte["nonce"]}

    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
            follow_redirects=False,
        )
    assert "an open invitation exists" not in caplog.text
    with SessionLocal() as db:
        konto = db.query(User).filter(User.email == "oma@beispiel.de").one()
        assert konto.role == Role.approver
        assert konto.quota_movies_limit == 3
        assert db.query(AuthToken).one().used_at is not None


def test_anlauf_cookie_traegt_secure_ueber_https(client: TestClient, attrappe: dict) -> None:
    """⚠️ **Dasselbe ``Secure`` wie das Sitzungs-Cookie - hier fehlte es.**

    Im Anlauf-Cookie stehen ``state``, ``nonce`` und der PKCE-Aufloeser, beim
    Verknuepfen dazu die Benutzernummer. Ohne ``Secure`` durfte ein Angreifer
    im Netz ueber eine Klartext-Anfrage unter diesem Pfad ein eigenes, gueltig
    signiertes Anlauf-Cookie **setzen** - und wer dem Browser eines anderen
    seinen Lauf unterschiebt, meldet ihn in *seinem* Konto an. Der Schalter
    ``NEXVIEW_COOKIE_SECURE`` erreichte dieses Cookie gar nicht.
    """
    anbieter_anlegen(public_url="https://testserver")

    ueber_https = TestClient(client.app, base_url="https://testserver")
    antwort = ueber_https.get(f"/api/auth/oidc/{SLUG}/login", follow_redirects=False)
    assert antwort.status_code == 302, antwort.text
    kopf = antwort.headers["set-cookie"]
    assert oidc_dienst.COOKIE_NAME in kopf
    assert "Secure" in kopf, kopf
    # Was schon dastand, bleibt stehen.
    assert "HttpOnly" in kopf
    assert "SameSite=lax" in kopf

    # ⚠️ Und ueber http NICHT - sonst wirft der Browser es weg und sperrt
    # jeden aus, der Nexview ohne HTTPS betreibt. Das ist der Sinn von "auto".
    ueber_http = TestClient(client.app, base_url="http://testserver")
    ohne = ueber_http.get(f"/api/auth/oidc/{SLUG}/login", follow_redirects=False)
    assert "Secure" not in ohne.headers["set-cookie"], ohne.headers["set-cookie"]


def test_bestaetigung_gilt_nur_fuer_die_adresse_die_sie_meint(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """⚠️ **Eine Bestaetigung darf nicht auf eine fremde Adresse abfaerben.**

    Der Ausweis sticht beim Verschmelzen, ``email_verified`` kann aber aus
    ``userinfo`` stammen - und dort stand womoeglich eine **andere** Adresse.
    Nach einem Adresswechsel bei einem selbst gehosteten Anbieter ist genau das
    moeglich. Ohne diese Pruefung buergte die Bestaetigung fuer eine Adresse,
    die sie nie betraf, und die Bruecke zu einem bestehenden Konto haette sich
    mit einer unbestaetigten Adresse oeffnen lassen.

    Der Anmeldelauf bricht deshalb nicht ab - nur die Bruecke bleibt zu.
    """
    _konto_mit_adresse("neu@beispiel.de", "oma")
    anbieter_anlegen(auto_create=False)
    werte = _hinweg(client)
    # Der Ausweis nennt die neue Adresse, sagt aber nichts ueber ihre
    # Bestaetigung. Die Nachfrage buergt - fuer die ALTE.
    attrappe["claims"] = {
        "nonce": werte["nonce"],
        "email": "neu@beispiel.de",
        "email_verified": None,
    }
    attrappe["userinfo"] = {
        "sub": "person-1",
        "email": "alt@beispiel.de",
        "email_verified": True,
    }

    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        antwort = client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
            follow_redirects=False,
        )

    assert antwort.status_code == 303, antwort.text
    assert "oidc_fehler=oidc_not_invited" in antwort.headers["location"]
    assert "treating the address as unconfirmed" in caplog.text
    # Vollstaendige Adressen gehoeren nicht ins Protokoll.
    assert "neu@beispiel.de" not in caplog.text
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "oma").one().oidc_links == []


def test_mehrere_empfaenger_brauchen_azp(client: TestClient, attrappe: dict, caplog) -> None:
    """⚠️ **Mehrere Empfaenger verlangen ``azp``** (OIDC Core 3.1.3.7).

    ``jwt.decode`` prueft nur, ob unsere Client-ID in der Liste *vorkommt*.
    Ein Ausweis, den der Anbieter fuer eine ANDERE Anwendung ausgestellt hat
    und in dem Nexview bloss mitgenannt ist, kam damit durch.
    """
    anbieter_anlegen(auto_create=True)
    werte = _hinweg(client)
    attrappe["claims"] = {
        "nonce": werte["nonce"],
        "aud": [CLIENT_ID, "eine-andere-anwendung"],
        "azp": "eine-andere-anwendung",
    }

    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        antwort = client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
            follow_redirects=False,
        )
    assert "oidc_fehler=oidc_token_invalid" in antwort.headers["location"]
    assert "issued it for a different application" in caplog.text
    assert _konten() == 0

    # Mit passendem ``azp`` geht derselbe Ausweis durch.
    werte = _hinweg(client)
    attrappe["claims"] = {
        "nonce": werte["nonce"],
        "aud": [CLIENT_ID, "eine-andere-anwendung"],
        "azp": CLIENT_ID,
    }
    gut = client.get(
        f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
        follow_redirects=False,
    )
    assert gut.headers["location"] == "/login?oidc=angemeldet", gut.headers["location"]


def test_stacktrace_vom_token_endpunkt_bleibt_eine_zeile(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """⚠️ Dieselbe Falle wie beim Rueckweg, nur eine Ebene tiefer.

    ``error_description`` vom Token-Endpunkt ist die wertvollste Diagnose im
    ganzen Ablauf - und manche Anbieter legen einen Stacktrace hinein. Ohne
    ``repr`` zerfaellt die Protokollzeile, und ``logs._parse`` verwirft jede
    Fortsetzung ("Fortsetzungszeile eines Stacktrace"): Im Protokoll-Fenster
    endet die Auskunft dann genau dort, wo sie interessant wird.
    """
    anbieter_anlegen()
    werte = _hinweg(client)
    # Der Rumpf wird gebaut, nicht getippt: Ein echter Zeilenumbruch muss darin
    # als JSON-Escape stehen, sonst ist die Antwort schon selbst kaputt - und
    # der Test pruefte etwas anderes als das, was er soll.
    stacktrace = (
        "Traceback (most recent call last):\n"
        '  File "idp.py", line 42\n'
        "RuntimeError: boom"
    )
    attrappe["token_antwort"] = (
        400,
        json.dumps({"error": "invalid_client", "error_description": stacktrace}),
        "application/json",
    )

    with caplog.at_level(logging.INFO, logger="nexview.oidc"):
        client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
            follow_redirects=False,
        )

    zeilen = [z for z in caplog.messages if "invalid_client" in z]
    assert zeilen, caplog.messages
    for zeile in zeilen:
        assert "\n" not in zeile, zeile
    # Der Inhalt geht dabei nicht verloren, er ist nur maskiert.
    assert any("RuntimeError: boom" in z for z in zeilen), zeilen


def test_azp_gilt_auch_bei_einem_einzigen_empfaenger(
    client: TestClient, attrappe: dict, caplog
) -> None:
    """⚠️ Die Norm bindet ``azp`` an "is present", nicht an die Zahl der Empfaenger.

    Der erste Bauversuch prueft nur Listen mit mehr als einem Eintrag - und
    liess damit ausgerechnet den Fall durch, fuer den ``azp`` gemacht ist: ein
    Ausweis mit genau einem Empfaenger, den der Anbieter fuer eine ANDERE
    Anwendung ausgestellt hat.
    """
    for empfaenger in (CLIENT_ID, [CLIENT_ID]):
        anbieter_anlegen(auto_create=True)
        werte = _hinweg(client)
        attrappe["claims"] = {
            "nonce": werte["nonce"],
            "aud": empfaenger,
            "azp": "eine-andere-anwendung",
        }
        with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
            antwort = client.get(
                f"/api/auth/oidc/{SLUG}/callback?code=einmal-code&state={werte['state']}",
                follow_redirects=False,
            )
        assert "oidc_fehler=oidc_token_invalid" in antwort.headers["location"], empfaenger
        assert "issued it for a different application" in caplog.text
        assert _konten() == 0, empfaenger
        with SessionLocal() as db:
            db.query(OidcProvider).delete()
            db.commit()
        caplog.clear()


def test_die_bremse_zaehlt_beim_verknuepfen_auf_die_person(
    admin_client: TestClient, attrappe: dict
) -> None:
    """⚠️ Die zweite Haelfte der Bremsen-Reparatur - ohne Test streichbar.

    Beim Anmelden gibt es noch keine Person, beim Verknuepfen schon: Der
    Zaehler haengt dann an der Benutzernummer aus dem Anlauf-Cookie. Ohne
    diesen Test liesse sich die Kennung ersatzlos auf ``None`` setzen und die
    Reihe bliebe gruen - die Bremse waere an beiden Tueren tot.
    """
    anbieter_anlegen()
    kopf = _angemeldet(admin_client, "max")

    gebremst = False
    for _ in range(14):
        start = admin_client.post(f"/api/auth/oidc/{SLUG}/link/start", headers=kopf)
        if start.status_code == 429:
            gebremst = True
            break
        werte = {
            k: v[0] for k, v in parse_qs(urlsplit(start.json()["url"]).query).items()
        }
        attrappe["claims"] = {"nonce": "ein-ganz-anderer-lauf"}
        antwort = admin_client.get(
            f"/api/auth/oidc/{SLUG}/callback?code=c&state={werte['state']}",
            follow_redirects=False,
        )
        if antwort.status_code == 429:
            gebremst = True
            break

    assert gebremst, "Die Bremse hat beim Verknuepfen nie gegriffen"
