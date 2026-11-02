"""Die Sprache der TMDB-Texte ist frei, Oberfläche und Mails bleiben Deutsch oder Englisch.

Issue #15: Ein Haus will französische Titel, Listen und Suche, die Oberfläche
darf englisch bleiben. ``default_language`` nimmt deshalb jede TMDB-Sprache;
wo sie als Sprache eines Kontos oder einer Mail dient, geht sie durch
``oberflaechensprache``.
"""

from __future__ import annotations

from dataclasses import replace
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import User
from app.services import mail
from app.services.settings_service import for_user, load_settings, oberflaechensprache

from .conftest import auth_headers

ZUGANG = {
    "smtp_host": "smtp.beispiel.de",
    "smtp_from_address": "nexview@example.com",
    "public_url": "https://nexview.beispiel.de",
}


@pytest.fixture
def postfach(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    """Eingerichteter Mailversand, der die Nachrichten nur einsammelt."""
    admin_client.put("/api/settings", json=ZUGANG)
    gesendet: list[EmailMessage] = []
    monkeypatch.setattr(mail, "_sende", lambda _config, nachricht: gesendet.append(nachricht))
    return gesendet


def _einladen(admin_client: TestClient, postfach: list[EmailMessage]) -> str:
    """Eine Einladung verschicken und das Geheimnis aus dem Link der Mail ziehen."""
    admin_client.post("/api/users/invitations", json={"email": "new@example.com", "role": "user"})
    text = postfach[-1].get_body(preferencelist=("plain",)).get_content()
    link = next(wort for wort in text.split() if "/einladung/" in wort)
    return link.strip().rstrip("/").rsplit("/", 1)[-1]


# --- Speichern ----------------------------------------------------------------


@pytest.mark.parametrize("sprache", ["fr", "pt-BR", "de-AT", "en"])
def test_jede_tmdb_sprache_laesst_sich_speichern(admin_client: TestClient, sprache: str) -> None:
    antwort = admin_client.put("/api/settings", json={"default_language": sprache})

    assert antwort.status_code == 200, antwort.text
    assert admin_client.get("/api/settings").json()["default_language"] == sprache


@pytest.mark.parametrize("sprache", ["xx_bad", "FR", "fr-br", "francais", "f", "pt-BRA"])
def test_ein_kuerzel_ausserhalb_des_musters_wird_abgelehnt(
    admin_client: TestClient, sprache: str
) -> None:
    antwort = admin_client.put("/api/settings", json={"default_language": sprache})

    assert antwort.status_code == 422
    assert antwort.json()["detail"]["code"] == "tmdb_language_invalid"
    assert admin_client.get("/api/settings").json()["default_language"] == "de"


# --- Konten und Mails ---------------------------------------------------------


@pytest.mark.parametrize(
    ("sprache", "erwartet"),
    [("de", "de"), ("de-AT", "de"), ("en", "en"), ("en-GB", "en"), ("fr", "en"), ("pt-BR", "en")],
)
def test_oberflaechensprache(sprache: str, erwartet: str) -> None:
    assert oberflaechensprache(sprache) == erwartet


@pytest.mark.parametrize(("sprache", "erwartet"), [("fr", "en"), ("de-AT", "de")])
def test_ein_neues_konto_startet_in_einer_oberflaechensprache(
    admin_client: TestClient, postfach: list[EmailMessage], sprache: str, erwartet: str
) -> None:
    """``fr`` als Kontosprache kennt die Oberfläche nicht, und ``PATCH /me`` lehnt sie ab."""
    admin_client.put("/api/settings", json={"default_language": sprache})
    roh = _einladen(admin_client, postfach)

    angelegt = admin_client.post(
        f"/api/onboarding/invitation/{roh}",
        json={"username": "neuer", "password": "eigenes-pw-123"},
    )
    assert angelegt.status_code == 201, angelegt.text

    ich = admin_client.get(
        "/api/auth/me", headers=auth_headers(admin_client, "neuer", "eigenes-pw-123")
    ).json()
    assert ich["language"] == erwartet


@pytest.mark.parametrize(
    ("sprache", "betreff"),
    [("fr", "You have been invited to Nexview"), ("de", "Du wurdest zu Nexview eingeladen")],
)
def test_die_einladung_kommt_bei_fremder_sprache_auf_englisch(
    admin_client: TestClient, postfach: list[EmailMessage], sprache: str, betreff: str
) -> None:
    """Die Vorlagen fragen nur ``startswith("en")``; ``fr`` ergab vorher eine deutsche Mail."""
    admin_client.put("/api/settings", json={"default_language": sprache})
    _einladen(admin_client, postfach)

    assert postfach[-1]["Subject"] == betreff


# --- Titel und Texte je Konto -------------------------------------------------


@pytest.mark.parametrize(
    ("haus", "oberflaeche", "erwartet"),
    [
        # Das Haus aus Issue #15: Konten starten auf Englisch und bekommen
        # trotzdem französische Titel.
        ("fr", "en", "fr"),
        # Wer dort auf Deutsch umstellt, bekommt deutsche Titel.
        ("fr", "de", "de"),
        # Die volle Vorgabe bleibt, nicht nur ihre Oberflächensprache.
        ("de-AT", "de", "de-AT"),
        # Wie bisher: Englische Oberfläche heißt englische Texte.
        ("de", "en", "en"),
        ("en", "de", "de"),
        ("de", "de", "de"),
    ],
)
def test_die_textsprache_eines_kontos(
    admin_client: TestClient, haus: str, oberflaeche: str, erwartet: str
) -> None:
    with SessionLocal() as session:
        settings = replace(load_settings(session), default_language=haus)

    assert for_user(settings, User(language=oberflaeche)).default_language == erwartet
