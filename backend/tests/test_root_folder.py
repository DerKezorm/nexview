"""Zielordner: darf der Benutzer ihn wählen - und was gilt, wenn nicht?

Der Kern hier ist die Durchsetzung auf dem Server. Das Feld in der Oberfläche
auszublenden genügt nicht: wer die Anfrage selbst zusammenbaut, könnte sonst
weiterhin jeden beliebigen Ordner mitschicken.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest
from app.services import requests_service
from app.services.settings_service import load_settings
from tests.conftest import auth_headers, create_user

FILME = "/data/Movies"
SERIEN = "/data/TV-Shows"


@pytest.fixture
def nutzer(arr_client: TestClient) -> dict[str, str]:
    """Kopfzeilen eines gewoehnlichen Kontos.

    Bewusst kein Administrator: dessen Anfragen werden sofort freigegeben
    und gehen unmittelbar an Radarr - das scheitert im Test am nicht
    erreichbaren Server. Eine gewoehnliche Anfrage bleibt liegen, und genau
    der gespeicherte Zielordner ist hier interessant.
    """
    create_user(arr_client, "lena")
    return auth_headers(arr_client, "lena", "passwort-1234")


def _erster_titel(client: TestClient, media_type: str = "movie") -> dict:
    antwort = client.get(f"/api/discover/{media_type}?page=1")
    assert antwort.status_code == 200, antwort.text
    return antwort.json()["items"][0]


def _anfragen(client: TestClient, titel: dict, ordner: str | None, **extra) -> object:
    nutzlast = {
        "media_type": titel["media_type"],
        "tmdb_id": titel["tmdb_id"],
        "quality_profile_id": 1,
    }
    if ordner is not None:
        nutzlast["root_folder_path"] = ordner
    return client.post("/api/requests", json=nutzlast, **extra)


def _gespeicherter_ordner(tmdb_id: int) -> str | None:
    with SessionLocal() as db:
        return db.query(MediaRequest).filter(MediaRequest.tmdb_id == tmdb_id).one().root_folder_path


# --- Auswahl erlaubt (Standard) ---------------------------------------------


def test_auswahl_ist_standardmaessig_erlaubt(arr_client: TestClient) -> None:
    optionen = arr_client.get("/api/arr/movie/options").json()
    assert optionen["root_folder_choice"] is True
    assert len(optionen["root_folders"]) == 2


def test_gewaehlter_ordner_wird_uebernommen(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    titel = _erster_titel(arr_client)
    antwort = _anfragen(arr_client, titel, SERIEN, headers=nutzer)
    assert antwort.status_code == 201, antwort.text
    assert _gespeicherter_ordner(titel["tmdb_id"]) == SERIEN


def test_unbekannter_ordner_wird_abgelehnt(arr_client: TestClient) -> None:
    """Sonst liesse sich ueber einen selbstgebauten Aufruf ein beliebiger Pfad
    auf dem Server anlegen."""
    titel = _erster_titel(arr_client)
    antwort = _anfragen(arr_client, titel, "/etc/heimlich")
    assert antwort.status_code == 422
    assert "Zielordner" in antwort.json()["detail"]


def test_ohne_angabe_gilt_der_standard(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    arr_client.put("/api/settings", json={"default_movie_root": SERIEN})
    titel = _erster_titel(arr_client)
    assert _anfragen(arr_client, titel, None, headers=nutzer).status_code == 201
    assert _gespeicherter_ordner(titel["tmdb_id"]) == SERIEN


def test_ohne_angabe_und_ohne_standard_der_erste(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    titel = _erster_titel(arr_client)
    assert _anfragen(arr_client, titel, None, headers=nutzer).status_code == 201
    assert _gespeicherter_ordner(titel["tmdb_id"]) == FILME


# --- Auswahl abgeschaltet ----------------------------------------------------


@pytest.fixture
def ohne_auswahl(arr_client: TestClient) -> TestClient:
    arr_client.put(
        "/api/settings",
        json={
            "movie_root_folder_choice": False,
            "series_root_folder_choice": False,
            "default_movie_root": SERIEN,
            "default_series_root": SERIEN,
        },
    )
    return arr_client


def test_benutzer_bekommt_nur_den_einen_ordner(
    ohne_auswahl: TestClient, nutzer: dict[str, str]
) -> None:
    optionen = ohne_auswahl.get("/api/arr/movie/options", headers=nutzer).json()
    assert optionen["root_folder_choice"] is False
    assert optionen["default_root_folder"] == SERIEN
    # Was nicht zur Wahl steht, wird gar nicht erst mitgeliefert.
    assert [o["path"] for o in optionen["root_folders"]] == [SERIEN]


def test_mitgeschickter_ordner_wird_ignoriert(
    ohne_auswahl: TestClient, nutzer: dict[str, str]
) -> None:
    """Der wichtigste Test: die Einstellung darf nicht bloss Kosmetik sein."""
    titel = _erster_titel(ohne_auswahl)
    antwort = _anfragen(ohne_auswahl, titel, FILME, headers=nutzer)
    assert antwort.status_code == 201, antwort.text

    # Trotz mitgeschicktem /data/Movies gilt die Vorgabe des Admins.
    assert _gespeicherter_ordner(titel["tmdb_id"]) == SERIEN


def test_getrennt_je_dienst(arr_client: TestClient, nutzer: dict[str, str]) -> None:
    """Filme und Serien sind unabhaengig voneinander einstellbar.

    Wer bei Serien feste Pfade will, muss das nicht auch bei Filmen wollen -
    die Ordnerstrukturen unterscheiden sich nun einmal.
    """
    arr_client.put(
        "/api/settings",
        json={
            "movie_root_folder_choice": True,
            "series_root_folder_choice": False,
            "default_series_root": SERIEN,
        },
    )

    filme = arr_client.get("/api/arr/movie/options", headers=nutzer).json()
    serien = arr_client.get("/api/arr/tv/options", headers=nutzer).json()

    assert filme["root_folder_choice"] is True
    assert serien["root_folder_choice"] is False
    assert serien["default_root_folder"] == SERIEN


def test_alte_installation_behaelt_ihre_einstellung(arr_client: TestClient, nutzer: dict[str, str]) -> None:
    """Ein Update darf die Einstellung nicht stillschweigend umstellen.

    Vor der Trennung gab es einen gemeinsamen Schalter. Steht der auf "aus" und
    wurden die neuen Schluessel noch nie gespeichert, muss "aus" fuer beide
    Dienste weitergelten - sonst duerften Benutzer nach einem Update ploetzlich
    Ordner waehlen, die der Administrator ihnen bewusst entzogen hatte.
    """
    from app.models import Setting

    with SessionLocal() as db:
        db.add(Setting(key="root_folder_choice", value="off"))
        db.commit()
        settings = load_settings(db)

    assert settings.movie_root_folder_choice is False
    assert settings.series_root_folder_choice is False


def test_admin_darf_weiterhin_waehlen(ohne_auswahl: TestClient) -> None:
    """Sonst kaeme der Admin an seine eigene Vorgabe nicht mehr heran."""
    optionen = ohne_auswahl.get("/api/arr/movie/options").json()
    assert optionen["root_folder_choice"] is True
    assert len(optionen["root_folders"]) == 2


def test_verschwundener_standardordner_blockiert_nicht(
    ohne_auswahl: TestClient, nutzer: dict[str, str]
) -> None:
    """Wird der Ordner in Radarr geloescht, soll die Anfrage trotzdem laufen."""
    ohne_auswahl.put("/api/settings", json={"default_movie_root": "/gibt/es/nicht/mehr"})
    titel = _erster_titel(ohne_auswahl)
    assert _anfragen(ohne_auswahl, titel, None, headers=nutzer).status_code == 201
    assert _gespeicherter_ordner(titel["tmdb_id"]) == FILME


# --- Reihenfolge der Prüfungen ----------------------------------------------


def test_ohne_radarr_kommt_die_verstaendliche_meldung(admin_client: TestClient) -> None:
    """Nicht der Verbindungsfehler der Ordnerabfrage, sondern der Hinweis, dass
    Radarr noch fehlt - der Nutzer kann mit dem zweiten mehr anfangen."""
    titel = _erster_titel(admin_client)
    antwort = _anfragen(admin_client, titel, None)
    assert antwort.status_code == 409
    assert "Radarr" in antwort.json()["detail"]


# --- Direkt am Dienst --------------------------------------------------------


async def test_aufloesung_ohne_auswahl_erzwingt_den_standard(arr_client: TestClient) -> None:
    arr_client.put(
        "/api/settings",
        json={"movie_root_folder_choice": False, "default_movie_root": SERIEN},
    )
    with SessionLocal() as db:
        settings = load_settings(db)
    assert await requests_service.resolve_root_folder(settings, "movie", FILME) == SERIEN
