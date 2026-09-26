"""Konto anlegen, einladen und Passwort über den Link aus der Mail setzen."""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import AuthToken, Role, TokenPurpose, User
from app.services import mail, mail_templates

from .conftest import ADMIN, auth_headers, create_user

ZUGANG = {
    "smtp_host": "smtp.beispiel.de",
    "smtp_from_address": "nexview@beispiel.de",
    "public_url": "https://nexview.beispiel.de",
}


@pytest.fixture
def postfach(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    """Eingerichteter Mailversand, der die Nachrichten nur einsammelt."""
    admin_client.put("/api/settings", json=ZUGANG)
    gesendet: list[EmailMessage] = []

    def _sende(_config: mail.MailConfig, nachricht: EmailMessage) -> None:
        gesendet.append(nachricht)

    monkeypatch.setattr(mail, "_sende", _sende)
    return gesendet


def _link_aus(nachricht: EmailMessage, pfad: str) -> str:
    """Den Einmal-Link aus der Textfassung ziehen."""
    text = nachricht.get_body(preferencelist=("plain",)).get_content()
    for wort in text.split():
        if pfad in wort:
            return wort.strip()
    raise AssertionError(f"Kein Link mit {pfad!r} in der Nachricht:\n{text}")


def _token_aus(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1]


# --- Einladung --------------------------------------------------------------


def test_der_link_zum_weitergeben_kommt_auch_bei_verschickter_mail(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Viele geben den Link lieber selbst weiter, etwa per Messenger.

    Bis zum 13.09.2026 kam er nur, wenn die Mail scheiterte. Es ist derselbe
    Link wie in der Mail.
    """
    antwort = admin_client.post(
        "/api/users/invitations", json={"email": "neu@example.com", "role": "user"}
    ).json()

    assert antwort["mail_sent"] is True
    assert antwort["manual_link"] == _link_aus(postfach[0], "/einladung/")


def test_einladung_verschicken_und_einloesen(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    antwort = admin_client.post(
        "/api/users/invitations", json={"email": "neu@beispiel.de", "role": "approver"}
    ).json()
    assert antwort["mail_sent"] is True

    roh = _token_aus(_link_aus(postfach[0], "/einladung/"))
    info = admin_client.get(f"/api/onboarding/invitation/{roh}").json()
    assert info["email"] == "neu@beispiel.de"
    assert info["role"] == "approver"

    angelegt = admin_client.post(
        f"/api/onboarding/invitation/{roh}",
        json={"username": "neuer", "display_name": "Der Neue", "password": "eigenes-pw-123"},
    )
    assert angelegt.status_code == 201

    kopf = auth_headers(admin_client, "neuer", "eigenes-pw-123")
    ich = admin_client.get("/api/auth/me", headers=kopf).json()
    assert ich["email"] == "neu@beispiel.de"
    assert ich["email_verified"] is True
    assert ich["display_name"] == "Der Neue"
    assert ich["role"] == "approver"


def test_einladung_uebernimmt_das_kontingent(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Die Einladung traegt die Grenzen ans neue Konto - und nur die.

    Der **Zeitraum** steht nicht mehr dabei: Er gilt haus-weit und wird in den
    Kontingenten eingestellt.
    """
    admin_client.post(
        "/api/users/invitations",
        json={
            "email": "neu@beispiel.de",
            "quota_movies_limit": 3,
            "quota_series_limit": "unlimited",
        },
    )
    roh = _token_aus(_link_aus(postfach[0], "/einladung/"))
    admin_client.post(
        f"/api/onboarding/invitation/{roh}", json={"username": "neuer", "password": "eigenes-pw"}
    )

    eintrag = next(u for u in admin_client.get("/api/users").json() if u["username"] == "neuer")
    assert eintrag["quota_movies_limit"] == 3
    assert eintrag["quota_series_limit"] == "unlimited"


def test_vergebener_benutzername_wird_abgelehnt(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Der Eingeladene wählt selbst - also muss er hier auffliegen."""
    admin_client.post("/api/users/invitations", json={"email": "neu@beispiel.de"})
    roh = _token_aus(_link_aus(postfach[0], "/einladung/"))

    antwort = admin_client.post(
        f"/api/onboarding/invitation/{roh}", json={"username": "ADMIN", "password": "eigenes-pw"}
    )
    assert antwort.status_code == 409
    # Und die Einladung bleibt gültig, damit er es nochmal versuchen kann.
    assert admin_client.get(f"/api/onboarding/invitation/{roh}").status_code == 200


def test_namensvorschau_waehrend_der_eingabe(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    assert admin_client.get("/api/onboarding/username-available?username=admin").json() == {
        "available": False
    }
    assert admin_client.get("/api/onboarding/username-available?username=neuer").json() == {
        "available": True
    }
    # Zu kurz zählt als nicht verfügbar.
    assert admin_client.get("/api/onboarding/username-available?username=ab").json() == {
        "available": False
    }


def test_einladung_an_bekannte_adresse(admin_client: TestClient, postfach: list[EmailMessage]) -> None:
    from .conftest import create_user

    create_user(admin_client, "kim")
    antwort = admin_client.post("/api/users/invitations", json={"email": "KIM@beispiel.de"})
    assert antwort.status_code == 409


def test_zweite_einladung_an_dieselbe_adresse(admin_client: TestClient, postfach: list[EmailMessage]) -> None:
    admin_client.post("/api/users/invitations", json={"email": "neu@beispiel.de"})
    zweite = admin_client.post("/api/users/invitations", json={"email": "neu@beispiel.de"})
    assert zweite.status_code == 409


def test_offene_einladungen_auflisten_und_zuruecknehmen(admin_client: TestClient, postfach: list[EmailMessage]) -> None:
    angelegt = admin_client.post(
        "/api/users/invitations", json={"email": "neu@beispiel.de"}
    ).json()

    offen = admin_client.get("/api/users/invitations").json()
    assert [e["email"] for e in offen] == ["neu@beispiel.de"]

    assert admin_client.delete(f"/api/users/invitations/{angelegt['id']}").status_code == 204
    assert admin_client.get("/api/users/invitations").json() == []


def test_zurueckgezogene_einladung_ist_wertlos(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    angelegt = admin_client.post(
        "/api/users/invitations", json={"email": "neu@beispiel.de"}
    ).json()
    roh = _token_aus(_link_aus(postfach[0], "/einladung/"))

    admin_client.delete(f"/api/users/invitations/{angelegt['id']}")
    assert admin_client.get(f"/api/onboarding/invitation/{roh}").status_code == 404


def test_einladen_darf_nur_der_admin(admin_client: TestClient) -> None:
    from .conftest import create_user

    create_user(admin_client, "kim")
    kim = auth_headers(admin_client, "kim", "passwort-1234")

    assert (
        admin_client.post(
            "/api/users/invitations", json={"email": "neu@beispiel.de"}, headers=kim
        ).status_code
        == 403
    )
    assert admin_client.get("/api/users/invitations", headers=kim).status_code == 403


def test_erfundener_link_fuehrt_ins_leere(admin_client: TestClient) -> None:
    assert admin_client.get("/api/onboarding/invitation/ausgedacht").status_code == 404
    assert admin_client.get("/api/onboarding/password/ausgedacht").status_code == 404
    assert (
        admin_client.post(
            "/api/onboarding/password/ausgedacht", json={"password": "irgendwas-123"}
        ).status_code
        == 404
    )


def test_der_link_steht_nicht_in_der_datenbank(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    admin_client.post("/api/users/invitations", json={"email": "neu@beispiel.de"})
    roh = _token_aus(_link_aus(postfach[0], "/einladung/"))

    with SessionLocal() as session:
        gespeichert = session.query(AuthToken).filter(
            AuthToken.purpose == TokenPurpose.invitation
        ).one()
        assert roh not in gespeichert.token_hash


def test_geloeschter_benutzer_nimmt_seine_links_mit(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Fremdschluessel mit CASCADE - sonst blieben tote Links liegen."""
    from .conftest import create_user

    kim = create_user(admin_client, "kim")
    admin_client.post("/api/onboarding/forgot-password", json={"email": "kim@beispiel.de"})
    roh = _token_aus(_link_aus(postfach[0], "/passwort/"))

    admin_client.delete(f"/api/users/{kim['id']}")

    assert admin_client.get(f"/api/onboarding/password/{roh}").status_code == 404
    with SessionLocal() as session:
        assert session.query(User).filter(User.username == "kim").count() == 0


# --- Voraussetzungen fürs Einladen ------------------------------------------


def test_ohne_mailserver_und_adresse_kein_einladen(admin_client: TestClient) -> None:
    """Eine Einladung, die niemand einlösen kann, hilft niemandem.

    ⚠️ Die Meldung traegt seit der Umstellung eine Kennung und **zwei
    Schalter** statt einer fertigen Aufzaehlung: Ein "und" mitten im Satz
    laesst sich nicht durch Einsetzen uebersetzen, und so stand der deutsche
    Satz auch in einer englischen Oberflaeche. Den Satz baut jetzt die
    Oberflaeche (``client.ts``, ``MIT_EIGENER_LOGIK``).
    """
    antwort = admin_client.post("/api/users/invitations", json={"email": "neu@beispiel.de"})
    assert antwort.status_code == 409
    detail = antwort.json()["detail"]
    assert detail["code"] == "invite_needs_setup"
    assert detail["needs_public_url"] is True
    assert detail["needs_mail"] is True
    # Der deutsche Rueckfall bleibt - fuer alles, was die Schnittstelle ohne
    # diese Oberflaeche benutzt.
    assert "öffentliche Adresse" in detail["message"]
    assert "Mailserver" in detail["message"]


def test_ohne_adresse_allein_kein_einladen(admin_client: TestClient) -> None:
    admin_client.put(
        "/api/settings",
        json={"smtp_host": "smtp.beispiel.de", "smtp_from_address": "nexview@beispiel.de"},
    )
    antwort = admin_client.post("/api/users/invitations", json={"email": "neu@beispiel.de"})
    assert antwort.status_code == 409
    detail = antwort.json()["detail"]
    assert detail["needs_public_url"] is True
    assert detail["needs_mail"] is False
    assert "öffentliche Adresse" in detail["message"]
    assert "Mailserver" not in detail["message"]


def test_ohne_mailserver_allein_kein_einladen(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json={"public_url": "https://nexview.beispiel.de"})
    antwort = admin_client.post("/api/users/invitations", json={"email": "neu@beispiel.de"})
    assert antwort.status_code == 409
    detail = antwort.json()["detail"]
    assert detail["needs_mail"] is True
    assert detail["needs_public_url"] is False
    assert "Mailserver" in detail["message"]


def test_die_oberflaeche_erfaehrt_es_ueber_config(admin_client: TestClient) -> None:
    """Damit der Knopf gesperrt werden kann, bevor jemand draufdrückt."""
    vorher = admin_client.get("/api/config").json()
    assert vorher["mail_configured"] is False
    assert vorher["public_url_set"] is False

    admin_client.put("/api/settings", json=ZUGANG)

    nachher = admin_client.get("/api/config").json()
    assert nachher["mail_configured"] is True
    assert nachher["public_url_set"] is True


# --- Anmeldung erst nach bestätigter Adresse --------------------------------


def _unbestaetigt(client: TestClient, username: str = "kim") -> None:
    """Konto mit unbestätigter Adresse - wie der Admin nach dem Assistenten."""
    from app.security import hash_password

    with SessionLocal() as session:
        session.add(
            User(
                username=username,
                password_hash=hash_password("passwort-1234"),
                email=f"{username}@beispiel.de",
                email_verified=False,
                display_name=username,
            )
        )
        session.commit()


def test_ohne_bestaetigung_keine_anmeldung(admin_client: TestClient) -> None:
    _unbestaetigt(admin_client)

    antwort = admin_client.post(
        "/api/auth/login",
        json={"username": "kim", "password": "passwort-1234"},
        headers={"Authorization": ""},
    )
    assert antwort.status_code == 403
    detail = antwort.json()["detail"]
    assert detail["code"] == "email_unverified"
    assert detail["email"] == "kim@beispiel.de"


# --- Der Betreiber sperrt sich nicht mit seiner eigenen Adresse aus ----------
#
# Entscheidung vor 1.0.0: Ohne Mailserver kommt der Bestaetigungslink nie an.
# Aendert der Betreiber dann seine Adresse im Profil, stuende er nach dem
# Abmelden vor der eigenen Installation und kaeme ohne Datenbank nicht mehr
# hinein. Fuer alle anderen Konten bleibt die Sperre.


def test_der_betreiber_kommt_mit_unbestaetigter_adresse_wieder_herein(
    admin_client: TestClient,
) -> None:
    assert admin_client.get("/api/config").json()["mail_configured"] is False
    antwort = admin_client.put("/api/auth/me/email", json={"email": "neu@example.com"})
    assert antwort.status_code == 200
    assert antwort.json()["email_verified"] is False

    admin_client.post("/api/auth/logout")
    admin_client.cookies.clear()

    assert auth_headers(admin_client, ADMIN["username"], ADMIN["password"])


def test_ein_zweiter_administrator_bleibt_ohne_bestaetigung_draussen(
    admin_client: TestClient,
) -> None:
    create_user(admin_client, "zweiter", role=Role.admin, email_verified=False)

    antwort = admin_client.post(
        "/api/auth/login",
        json={"username": "zweiter", "password": "passwort-1234"},
        headers={"Authorization": ""},
    )

    assert antwort.status_code == 403
    assert antwort.json()["detail"]["code"] == "email_unverified"


def _rumpf(nachricht: EmailMessage) -> str:
    return nachricht.get_body(preferencelist=("plain",)).get_content()


def test_die_bestaetigungsmail_nennt_die_gesperrte_anmeldung(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Frueher stand dort nur, man koenne nichts anfragen - gesperrt ist die Anmeldung."""
    _unbestaetigt(admin_client)
    admin_client.post(
        "/api/onboarding/pending/resend",
        json={"username": "kim", "password": "passwort-1234"},
    )

    text = _rumpf(postfach[-1])
    assert "kannst du dich nicht mit Benutzername und Passwort anmelden" in text
    assert "Titel anfragen" not in text


def test_die_bestaetigungsmail_des_betreibers_droht_keine_sperre_an(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    admin_client.put("/api/auth/me/email", json={"email": "neu@example.com"})

    # Die alte Adresse bekommt danach noch ihre Warnung; gemeint ist die neue.
    (nachricht,) = [n for n in postfach if n["To"] == "neu@example.com"]
    text = _rumpf(nachricht)
    assert "Deine Anmeldung hängt nicht daran" in text
    assert "nicht mit Benutzername und Passwort anmelden" not in text


@pytest.mark.parametrize("betreiber", [False, True])
def test_die_bestaetigungsmail_sagt_es_auch_auf_englisch(betreiber: bool) -> None:
    text = mail_templates.verification_mail(
        "https://nexview.example.com/bestaetigen/x", "en", sperrt_anmeldung=not betreiber
    ).text
    if betreiber:
        assert "Your sign-in does not depend on it" in text
        assert "cannot sign in" not in text
    else:
        assert "you cannot sign in with your username and password" in text
    assert "request" not in text


def test_nach_bestaetigung_geht_die_anmeldung(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    _unbestaetigt(admin_client)
    admin_client.post(
        "/api/onboarding/pending/resend",
        json={"username": "kim", "password": "passwort-1234"},
    )
    roh = _token_aus(_link_aus(postfach[-1], "/bestaetigen/"))

    assert admin_client.post(f"/api/onboarding/verify/{roh}").status_code == 204
    assert auth_headers(admin_client, "kim", "passwort-1234")


def test_notausgang_braucht_das_richtige_passwort(admin_client: TestClient) -> None:
    _unbestaetigt(admin_client)

    for pfad, methode in (("/api/onboarding/pending/resend", "post"),):
        antwort = getattr(admin_client, methode)(
            pfad, json={"username": "kim", "password": "falsch"}
        )
        assert antwort.status_code == 401


def test_adresse_vor_der_anmeldung_korrigieren(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Der häufigste Grund für eine nie ankommende Mail ist ein Tippfehler."""
    _unbestaetigt(admin_client)

    antwort = admin_client.put(
        "/api/onboarding/pending/email",
        json={
            "username": "kim",
            "password": "passwort-1234",
            "email": "richtig@beispiel.de",
        },
    )
    assert antwort.status_code == 200
    assert antwort.json()["sent"] is True
    assert postfach[-1]["To"] == "richtig@beispiel.de"

    # Und der Link aus der neuen Mail schaltet frei.
    roh = _token_aus(_link_aus(postfach[-1], "/bestaetigen/"))
    assert admin_client.post(f"/api/onboarding/verify/{roh}").status_code == 204
    assert auth_headers(admin_client, "kim", "passwort-1234")


def test_notausgang_nur_solange_unbestaetigt(admin_client: TestClient) -> None:
    from .conftest import create_user

    create_user(admin_client, "kim")  # bereits bestätigt
    antwort = admin_client.post(
        "/api/onboarding/pending/resend",
        json={"username": "kim", "password": "passwort-1234"},
    )
    assert antwort.status_code == 409


def test_korrektur_auf_eine_vergebene_adresse(admin_client: TestClient) -> None:
    from .conftest import create_user

    create_user(admin_client, "alex")
    _unbestaetigt(admin_client)

    antwort = admin_client.put(
        "/api/onboarding/pending/email",
        json={
            "username": "kim",
            "password": "passwort-1234",
            "email": "alex@beispiel.de",
        },
    )
    assert antwort.status_code == 409


# Der erste Administrator gilt seit 1.0.0 sofort als bestaetigt, wie der
# Assistent es sagt; dazu ``test_setup.py``. Hier stand der Test fuer das
# Gegenteil, das den Betreiber ohne Mailserver aussperrte.
