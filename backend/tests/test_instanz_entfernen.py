"""Eine Instanz entfernen: Nexviews Zugang faellt - und sonst nichts.

Die zugesagten Folgen, hier festgenagelt:

* Zugang, Name, Instanz-Vorgaben und (bei 4K) die eigenen Regeln werden
  geleert; die 4K-Regeln **erben** danach wieder von der Standard-Instanz.
* Unser Webhook-Eintrag drueben wird vorher rueckstandsfrei entfernt, samt
  Geheimnis und Zustand hier - sonst riefe er fuer immer ins Leere.
* **Laufende Anfragen bleiben stehen.** Kein Massen-Abbruch, dieselbe Regel
  wie beim Ausfall einer Quelle im Status-Abgleich.
* Eine gerade stumme Instanz haelt das Entfernen nicht auf.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, QualityTier, RequestStatus, StorageEntry, StorageState
from app.services import storage, webhook_pflege, webhooks
from app.services.arr import ArrError
from app.services.settings_service import load_settings, save_settings

from .conftest import auth_headers, create_user
from .test_webhook_pflege import FakeArr


def _wartende_anfrage(client: TestClient) -> None:
    create_user(client, "kim")
    headers = auth_headers(client, "kim", "passwort-1234")
    item = client.get("/api/discover/movie").json()["items"][0]
    client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    )


def test_entfernen_leert_zugang_und_raeumt_den_webhook(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeArr()
    fake.eintraege = [
        {
            "id": 7,
            "name": "Nexview",
            "implementation": "Webhook",
            "fields": [
                {
                    "name": "url",
                    "value": "http://nexview.test/api/webhooks/arr/radarr-standard",
                }
            ],
        }
    ]
    monkeypatch.setattr(webhook_pflege, "_client", lambda _instanz: fake)
    with SessionLocal() as db:
        save_settings(db, {"public_url": "http://nexview.test"})
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.eintrag_id = 7
        db.commit()
    _wartende_anfrage(arr_client)

    antwort = arr_client.delete("/api/settings/instanzen/radarr-standard")
    assert antwort.status_code == 200, antwort.text

    daten = antwort.json()
    assert daten["radarr_url"] == ""
    assert daten["radarr_api_key_set"] is False
    assert daten["radarr_name"] == ""
    # Der Webhook drueben ist weg - samt Geheimnis und Zustand hier.
    assert fake.geloescht == [7]
    with SessionLocal() as db:
        assert webhooks.eintrag(db, "radarr-standard") is None
        # Die wartende Anfrage steht unveraendert - kein Massen-Abbruch.
        anfrage = db.query(MediaRequest).one()
        assert anfrage.status == RequestStatus.pending_approval


def test_nicht_eingerichtet_ist_404(arr_client: TestClient) -> None:
    antwort = arr_client.delete("/api/settings/instanzen/radarr-uhd")
    assert antwort.status_code == 404


def test_4k_regeln_erben_nach_dem_entfernen_wieder(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhook_pflege, "_client", lambda _instanz: FakeArr())
    with SessionLocal() as db:
        save_settings(
            db,
            {
                "radarr_uhd_url": "http://127.0.0.1:19",
                "radarr_uhd_api_key": "test-radarr-4k",
                "movie_profile_mode": "user",
                "movie_uhd_profile_mode": "approver",
            },
        )

    antwort = arr_client.delete("/api/settings/instanzen/radarr-uhd")
    assert antwort.status_code == 200, antwort.text

    with SessionLocal() as db:
        einstellungen = load_settings(db)
    assert einstellungen.radarr_uhd_url == ""
    # Die eigene 4K-Regel ist geleert - es gilt wieder die der Standard-Instanz.
    assert einstellungen.movie_uhd_profile_mode == "user"


def test_stumme_instanz_haelt_das_entfernen_nicht_auf(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Stumm:
        async def notifications(self):
            raise ArrError("Radarr antwortet nicht.", code="arr_timeout")

    monkeypatch.setattr(webhook_pflege, "_client", lambda _instanz: Stumm())
    with SessionLocal() as db:
        webhooks.eintrag_sicherstellen(db, "radarr-standard")

    antwort = arr_client.delete("/api/settings/instanzen/radarr-standard")
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["radarr_url"] == ""
    with SessionLocal() as db:
        assert webhooks.eintrag(db, "radarr-standard") is None


def test_unverwalteter_posten_laesst_sich_abgeben_und_ans_haus_geben(
    arr_client: TestClient,
) -> None:
    """Die Antwort auf "bleibt das dann fuer immer im Kontingent?": Nein.

    Ein Posten, den nach dem Entfernen der Instanz nur noch der Medienserver
    meldet (``arr_managed=False``), zaehlt zwar weiter - aber Abgeben und die
    Haus-Uebernahme sind reine Zurechnungs-Entscheidungen ohne Radarr. Nur
    das Loeschen braucht die Instanz.
    """
    create_user(arr_client, "kim")
    with SessionLocal() as db:
        from app.models import User

        kim = db.query(User).filter(User.username == "kim").one()
        posten = StorageEntry(
            key="movie:standard:tmdb:603",
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=603,
            title="Matrix",
            size_bytes=8 * 1024**3,
            user_id=kim.id,
            state=StorageState.owned,
            arr_managed=False,
        )
        db.add(posten)
        db.commit()
        posten_id = posten.id

        storage.abgeben(db, posten_id, kim)
        db.commit()
        uebernahme = storage.ins_haus(db, posten_id)
        assert uebernahme is not None
        db.commit()

        frisch = db.get(StorageEntry, posten_id)
        assert frisch.state == StorageState.house
        assert frisch.user_id is None
