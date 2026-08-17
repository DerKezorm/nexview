"""Gemeinsame Test-Vorbereitung.

Wichtig: Die Umgebungsvariablen muessen gesetzt sein, *bevor* ``app`` importiert
wird - sonst legt Nexview seine echte Datenbank unter ``data/`` an statt in
einem temporaeren Testverzeichnis.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="nexview-tests-"))
os.environ["NEXVIEW_DATA_DIR"] = str(_TMP_DIR)
os.environ["NEXVIEW_SECRET_KEY"] = "test-secret-key-nur-fuer-tests"
# Die Hintergrundschleife wuerde in Tests nur stoeren.
os.environ["NEXVIEW_DISABLE_POLLER"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import library  # noqa: E402
from app.models import (  # noqa: E402
    ArrLibraryCache,
    AuthToken,
    MediaRequest,
    Notification,
    Setting,
    TmdbCache,
    User,
)
from app.security import hash_password  # noqa: E402

ADMIN = {
    "username": "admin",
    "password": "admin-passwort-123",
    "email": "admin@beispiel.de",
}


@pytest.fixture(autouse=True)
def clean_db() -> Iterator[None]:
    """Vor jedem Test mit leeren Tabellen starten."""
    init_db()
    with SessionLocal() as session:
        session.execute(delete(Notification))
        session.execute(delete(MediaRequest))
        session.execute(delete(AuthToken))
        session.execute(delete(User))
        session.execute(delete(Setting))
        session.execute(delete(TmdbCache))
        session.execute(delete(ArrLibraryCache))
        session.commit()
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_client(client: TestClient) -> TestClient:
    """Angemeldeter Administrator (ueber die Erst-Einrichtung angelegt)."""
    response = client.post("/api/setup/admin", json=ADMIN)
    assert response.status_code == 201, response.text

    # Der Assistent laesst die Adresse unbestaetigt - im Betrieb hat der Admin
    # den Link laengst geklickt. Tests, die genau diesen Zwischenzustand
    # pruefen, nehmen den rohen ``client``.
    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == ADMIN["username"]).one()
        admin.email_verified = True
        session.commit()

    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture
def arr_client(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Angemeldeter Admin mit eingerichtetem - aber nicht erreichbarem - Radarr/Sonarr.

    Anfragen setzen eingerichtete Dienste voraus. Die Bibliotheks-Abfrage wird
    durch eine leere Antwort ersetzt, damit die Tests ohne Netzwerk laufen.
    Port 9 lehnt Verbindungen sofort ab - so bleibt auch der Fehlerfall schnell.
    """
    admin_client.put(
        "/api/settings",
        json={
            "radarr_url": "http://127.0.0.1:9",
            "radarr_api_key": "test-radarr-key",
            "sonarr_url": "http://127.0.0.1:9",
            "sonarr_api_key": "test-sonarr-key",
        },
    )

    async def keine_filme(_settings: object) -> dict:
        return {}

    async def keine_serien(_settings: object) -> tuple[dict, dict]:
        return {}, {}

    monkeypatch.setattr(library, "movie_library", keine_filme)
    monkeypatch.setattr(library, "series_library", keine_serien)
    return admin_client


def create_user(
    client: TestClient, username: str, password: str = "passwort-1234", **extra: object
) -> dict:
    """Fertiges Konto direkt in der Datenbank anlegen.

    Konten entstehen im Betrieb nur ueber eine Einladung - jedes Mal ueber
    Mail, Link und Formular zu gehen, waere fuer Tests, die einfach nur
    *irgendein* angemeldetes Konto brauchen, reine Zeremonie. Der echte Weg
    wird in ``test_onboarding.py`` geprueft.
    """
    with SessionLocal() as session:
        vorhanden = session.query(User).filter(User.username == username).one_or_none()
        if vorhanden is None:
            vorhanden = User(
                username=username,
                email=f"{username}@beispiel.de",
                email_verified=True,
                display_name=username,
            )
            session.add(vorhanden)
        for feld, wert in extra.items():
            setattr(vorhanden, feld, wert)
        vorhanden.password_hash = hash_password(password)
        session.commit()
        session.refresh(vorhanden)
        kennung = vorhanden.id

    # Dieselbe Darstellung wie aus der API - viele Tests lesen daraus weiter.
    return next(u for u in client.get("/api/users").json() if u["id"] == kennung)


def auth_headers(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"Authorization": ""},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
