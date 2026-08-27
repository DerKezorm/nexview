"""Zielwahl-Regeln je Instanz - und was sie in der Genehmigungskette schlagen.

Seit dem Kachel-Umbau gilt "wer waehlt Profil und Ordner" **je Instanz**
statt je Dienst. Hier haengt die ganze Kette am Nagel:

* **Erbschaft:** Eine nie gesetzte 4K-Regel folgt der Standard-Instanz -
  jede bestehende Installation verhaelt sich nach dem Update exakt wie
  vorher.
* **Unabhaengigkeit:** Eine gesetzte 4K-Regel gilt nur dort - in beide
  Richtungen (4K streng bei freiem Standard, und umgekehrt).
* **Die Ueberschreibung:** "Der Entscheider waehlt" schlaegt die
  Sofort-Freigabe des Benutzers - auch die eigene 4K-Sofort-Freigabe -
  aber nur auf der Stufe, fuer die die Regel gilt.
* **Die Ausnahme:** Entscheider und Administratoren waehlen gleich selbst;
  fuer sie wartet nichts.
* **Die Kinder-Kette:** Die Freigabe eines Kinderwunschs erzeugt die
  Anfrage des Elternteils - und die wartet unter der Entscheider-Regel
  genauso wie jede andere.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus
from app.services import library
from app.services.settings_service import load_settings, save_settings

from .conftest import auth_headers, create_user


class _FakeRadarr:
    """Nimmt die Uebergabe entgegen, ohne ein echtes Radarr zu brauchen."""

    async def add(self, *args, **kwargs) -> dict:
        return {"id": 4242}

    async def ensure_tag(self, label: str) -> int:
        return 7

VIER_K = {"radarr_uhd_url": "http://127.0.0.1:19", "radarr_uhd_api_key": "test-radarr-4k"}


def _regeln(**werte) -> None:
    with SessionLocal() as db:
        save_settings(db, werte)


def _titel(client: TestClient, headers: dict[str, str]) -> list[dict]:
    return client.get("/api/discover/movie", headers=headers).json()["items"]


# --- Die Regel selbst -------------------------------------------------------


def test_nie_gesetzte_4k_regel_erbt_von_der_standard_instanz() -> None:
    _regeln(movie_profile_mode="approver")
    with SessionLocal() as db:
        einstellungen = load_settings(db)

    assert einstellungen.movie_uhd_profile_mode == "approver"
    assert einstellungen.approver_picks_target("movie", "uhd") is True


def test_gesetzte_4k_regel_gilt_nur_dort() -> None:
    _regeln(movie_profile_mode="user", movie_uhd_profile_mode="approver")
    with SessionLocal() as db:
        einstellungen = load_settings(db)
    assert einstellungen.approver_picks_target("movie") is False
    assert einstellungen.approver_picks_target("movie", "uhd") is True

    # Und die Gegenrichtung: Standard streng, 4K bewusst frei.
    _regeln(movie_profile_mode="approver", movie_uhd_profile_mode="user")
    with SessionLocal() as db:
        einstellungen = load_settings(db)
    assert einstellungen.approver_picks_target("movie") is True
    assert einstellungen.approver_picks_target("movie", "uhd") is False


# --- Die Kette: direkte Anfrage --------------------------------------------


def test_4k_wartet_auf_den_entscheider_waehrend_standard_frei_laeuft(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Ueberschreibung je Stufe: Sofort-Freigabe (auch die eigene
    4K-Sofort-Freigabe) verliert gegen die Entscheider-Regel - aber nur auf
    der Stufe, fuer die sie gilt."""
    _regeln(**VIER_K, movie_uhd_profile_mode="approver")
    # Die Sofort-Freigabe uebergibt direkt an Radarr - hier gestubbt.
    monkeypatch.setattr(library, "radarr_client", lambda *_a, **_k: _FakeRadarr())
    create_user(
        arr_client,
        "kim",
        auto_approve=True,
        can_request_uhd_movies=True,
        auto_approve_uhd=True,
    )
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    titel = _titel(arr_client, headers)

    # Standard: die Sofort-Freigabe greift wie immer.
    standard = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": titel[0]["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    )
    assert standard.status_code == 201, standard.text
    assert standard.json()["status"] in ("approved", "searching")

    # 4K: Der Entscheider waehlt - die Anfrage wartet ohne Ziel, trotz
    # auto_approve und auto_approve_uhd am Konto.
    vier_k = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": titel[1]["tmdb_id"],
            "tier": "uhd",
        },
        headers=headers,
    )
    assert vier_k.status_code == 201, vier_k.text
    assert vier_k.json()["status"] == "pending_approval"
    assert vier_k.json()["quality_profile_id"] is None


def test_admin_waehlt_gleich_selbst(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Ausnahme: Wer freigeben darf, gibt sich nicht selbst eine
    Warteschlange - er waehlt sofort."""
    _regeln(**VIER_K, movie_uhd_profile_mode="approver")
    monkeypatch.setattr(library, "radarr_client", lambda *_a, **_k: _FakeRadarr())
    titel = _titel(arr_client, {})

    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": titel[0]["tmdb_id"],
            "tier": "uhd",
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["status"] in ("approved", "searching")


# --- Die Kette: Kinder-Freigabe ---------------------------------------------


def test_kinder_freigabe_wartet_unter_der_entscheider_regel(
    arr_client: TestClient,
) -> None:
    """Die Freigabe der Eltern erzeugt ihre Anfrage - und die wartet unter
    der Entscheider-Regel wie jede andere: Eltern sind keine Entscheider."""
    _regeln(movie_profile_mode="approver")

    create_user(arr_client, "elternteil", "eltern-passwort", can_manage_children=True)
    eltern = auth_headers(arr_client, "elternteil", "eltern-passwort")
    arr_client.post(
        "/api/children",
        json={"username": "kind", "password": "kind-passwort", "age": 16},
        headers=eltern,
    )
    kind = auth_headers(arr_client, "kind", "kind-passwort")

    kategorien = arr_client.get(
        "/api/kids/categories?media_type=movie", headers=kind
    ).json()
    assert kategorien, "Die Kinder-Startseite ist leer - dann testet hier nichts."
    seite = arr_client.get(
        f"/api/kids/rubrik/{kategorien[0]['rubrik']}?media_type=movie",
        headers=kind,
    ).json()
    erster = seite["wuenschbar"][0]
    wunsch = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": erster["tmdb_id"]},
        headers=kind,
    ).json()

    antwort = arr_client.post(
        f"/api/children/wishes/{wunsch['id']}/release",
        json={},
        headers=eltern,
    )
    assert antwort.status_code == 200, antwort.text

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.status == RequestStatus.pending_approval
        assert anfrage.quality_profile_id is None
