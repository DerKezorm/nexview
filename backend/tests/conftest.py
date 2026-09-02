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
# ⚠️ bcrypt mit der Vorgabe 12 kostet rund 0,3 Sekunden je Hash **und** je
# Pruefung. Die Testreihe legt tausende Konten an und meldet sich tausende Male
# an; damit ging der groessere Teil der Laufzeit fuer bcrypt drauf. Mit 4
# Runden sind es rund 0,001 Sekunden. Geprueft wird dieselbe Mechanik, nur
# billiger: Die Rundenzahl steht im Hash und aendert an ihr nichts.
#
# Die Zeile muss **hier oben** stehen, vor dem ersten ``from app...``. ``app.db``
# ruft beim Import ``get_settings()``, und das Ergebnis ist mit ``lru_cache``
# gepuffert; wer die Variable danach setzt, aendert nichts mehr. Ein
# ``monkeypatch`` am gepufferten Objekt waere noch schlechter: ``test_unterpfad``
# leert den Puffer mitten im Lauf, und die Zahl fiele dort still auf 12
# zurueck. tests/test_bcrypt_runden.py bewacht genau das.
os.environ["NEXVIEW_BCRYPT_ROUNDS"] = "4"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Base,
    MediaRequest,
    User,
    UserMediaServerAccount,
)
from app.security import hash_password  # noqa: E402
from app.services import anmeldebremse, library  # noqa: E402

ADMIN = {
    "username": "admin",
    "password": "admin-passwort-123",
    "email": "admin@beispiel.de",
}


@pytest.fixture(autouse=True)
def clean_db() -> Iterator[None]:
    """Vor jedem Test mit leeren Tabellen starten.

    Bewusst aus den Metadaten abgeleitet statt als Aufzaehlung: eine feste
    Liste muss man bei jeder neuen Tabelle nachziehen, und wer das vergisst,
    bekommt Tests, die einzeln laufen, aber gemeinsam scheitern - weil Reste
    des vorherigen Tests stehen bleiben. Genau das ist mit der Sperrliste
    passiert, deren Eintraege ein Loeschen der Benutzer absichtlich
    ueberleben.

    ``sorted_tables`` steht in Abhaengigkeitsreihenfolge; rueckwaerts geloescht
    verletzt kein Fremdschluessel.
    """
    init_db()
    with SessionLocal() as session:
        for tabelle in reversed(Base.metadata.sorted_tables):
            session.execute(delete(tabelle))
        session.commit()

    # ⚠️ Die Anmeldebremse zaehlt im **Arbeitsspeicher**, nicht in der
    # Datenbank - die Schleife darueber raeumt sie also nicht mit weg. Ohne
    # diese Zeile schleppt ein Test, der absichtlich falsche Passwoerter
    # eingibt, seine Zaehler in den naechsten, und der scheitert dann mit
    # 429 statt mit dem, was er eigentlich prueft.
    anmeldebremse.zuruecksetzen()

    # Der Merker von ``load_settings`` braucht hier bewusst **nichts**: Er
    # haengt an der Sitzung (``Session.info``), nicht am Prozess, und jede
    # Sitzung faengt leer an. Waere er global, muesste er eine Zeile weiter
    # oben stehen - und die Testreihe waere ab sofort reihenfolgeabhaengig.
    # Bewacht von tests/test_einstellungen_merker.py.
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

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict:
        """Was in "Radarr" liegt: alles, was eine erledigte Anfrage hat.

        Frueher war die Antwort einfach leer. Seit "Frisch geladen" den
        Bestand nachprueft, waere das aber ein Zustand, den es echt nicht
        gibt: ein fertig geladener Film, den die Bibliothek nie gesehen hat.
        Die Attrappe spiegelt deshalb die Anfrage-Tabelle - geloescht wird in
        Tests ueber ein monkeypatch auf ``library.movie_library``.
        """
        from app.models import MediaType, RequestStatus
        from app.services.radarr import LibraryEntry

        with SessionLocal() as sitzung:
            kennungen = sitzung.scalars(
                select(MediaRequest.tmdb_id).where(
                    MediaRequest.media_type == MediaType.movie,
                    MediaRequest.status == RequestStatus.downloaded,
                )
            ).all()
        return {
            kennung: LibraryEntry(arr_id=kennung, has_file=True, monitored=True)
            for kennung in kennungen
        }

    async def keine_serien(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
        return {}, {}

    async def optionen(_settings: object, _media_type: str, _tier: str = "standard") -> dict:
        """Qualitaetsprofile und Zielordner, wie Radarr/Sonarr sie liefern wuerden.

        Wird beim Anlegen einer Anfrage gebraucht: der Server prueft dort, ob es
        den gewuenschten Zielordner ueberhaupt gibt. Ohne diese Antwort scheitert
        jede Anfrage schon an der Verbindung.
        """
        return {
            # Bewusst **zwei** Profile: Mit nur einem waere "dieses Profil sperren"
            # dasselbe wie "alle sperren", und das ignoriert der Dienst - sonst
            # koennte der Benutzer gar nichts mehr anfragen.
            "quality_profiles": [
                {"id": 1, "name": "HD-1080p"},
                {"id": 2, "name": "SD-576p"},
            ],
            "root_folders": [
                {"path": "/data/Movies", "free_space": 1_000_000_000},
                {"path": "/data/TV-Shows", "free_space": 1_000_000_000},
            ],
        }

    monkeypatch.setattr(library, "movie_library", bibliothek)
    monkeypatch.setattr(library, "series_library", keine_serien)
    monkeypatch.setattr(library, "options", optionen)
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

        # ⚠️ Wer hier mit ``mediaserver_provider``/``mediaserver_account_id``
        # angelegt wird, meint "dieses Konto ist verknuepft" - und seit es
        # ``user_media_server_accounts`` gibt, gehoert dazu eine Zeile. Ohne
        # sie waere es eine halbe Verknuepfung: Die Anwendung sucht ueber die
        # Tabelle, faende niemanden, und Tests zum Thema "dieselbe Identitaet
        # zweimal" liefen ins Leere statt in ihren Konflikt.
        if vorhanden.mediaserver_provider and vorhanden.mediaserver_account_id:
            zeile = next(
                (
                    z
                    for z in vorhanden.mediaserver_accounts
                    if z.provider == vorhanden.mediaserver_provider
                ),
                None,
            )
            if zeile is None:
                zeile = UserMediaServerAccount(
                    provider=vorhanden.mediaserver_provider,
                    account_id=vorhanden.mediaserver_account_id,
                )
                vorhanden.mediaserver_accounts.append(zeile)
            zeile.account_id = vorhanden.mediaserver_account_id
            zeile.username = vorhanden.mediaserver_username
            zeile.email = vorhanden.mediaserver_email
            zeile.token = vorhanden.watchlist_token

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


def pytest_addoption(parser) -> None:
    """``--abdruck-neu`` erneuert den festgehaltenen Stand der /api/v1-Antworten.

    ⚠️ Bewusst ein eigener Handgriff und keine Automatik: Der Abdruck ist der
    einzige Waechter ueber eine Zusage nach aussen. Wuerde er sich von selbst
    erneuern, waere er kein Waechter, sondern ein Protokoll.
    """
    parser.addoption(
        "--abdruck-neu",
        action="store_true",
        default=False,
        help="Den Abdruck der zugesagten /api/v1-Antworten neu schreiben.",
    )
