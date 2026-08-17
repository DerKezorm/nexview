"""Protokoll: nur für Admins, mit Filter und Aufräumen."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.services import logs

from .conftest import auth_headers, create_user


def _schreibe_beispielzeilen() -> None:
    logs.setup()
    # Sonst stehen noch die Zeilen der vorherigen Tests in der Datei.
    logs.clear()
    log = logging.getLogger("nexview.test")
    log.info("Movie added to Radarr")
    log.warning("Radarr did not answer in time")
    log.error("Could not add movie: rejected by Radarr")
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_protokoll_wird_geschrieben(admin_client: TestClient) -> None:
    _schreibe_beispielzeilen()

    zeilen = admin_client.get("/api/logs").json()
    meldungen = [z["message"] for z in zeilen]
    assert "Movie added to Radarr" in meldungen
    assert {"INFO", "WARNING", "ERROR"} <= {z["level"] for z in zeilen}


def test_neueste_zeile_zuerst(admin_client: TestClient) -> None:
    _schreibe_beispielzeilen()
    zeilen = admin_client.get("/api/logs").json()
    assert zeilen[0]["message"] == "Could not add movie: rejected by Radarr"


def test_filter_nach_stufe(admin_client: TestClient) -> None:
    _schreibe_beispielzeilen()

    nur_fehler = admin_client.get("/api/logs?level=ERROR").json()
    assert {z["level"] for z in nur_fehler} == {"ERROR"}


def test_suche_im_text(admin_client: TestClient) -> None:
    _schreibe_beispielzeilen()

    treffer = admin_client.get("/api/logs?search=did not answer").json()
    assert len(treffer) == 1
    assert treffer[0]["level"] == "WARNING"


def test_ungueltige_stufe(admin_client: TestClient) -> None:
    assert admin_client.get("/api/logs?level=BLABLA").status_code == 422


def test_fremdbibliotheken_fluten_das_protokoll_nicht(admin_client: TestClient) -> None:
    """httpx protokolliert sonst jede einzelne HTTP-Anfrage - im Betrieb also
    jeden TMDB- und Radarr-Aufruf."""
    _schreibe_beispielzeilen()
    logging.getLogger("httpx").info("HTTP Request: GET https://api.themoviedb.org/3/...")
    for handler in logging.getLogger().handlers:
        handler.flush()

    zeilen = admin_client.get("/api/logs").json()
    assert not [z for z in zeilen if z["logger"].startswith("httpx")]


def test_protokoll_herunterladen(admin_client: TestClient) -> None:
    _schreibe_beispielzeilen()

    response = admin_client.get("/api/logs/download")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    assert ".txt" in response.headers["content-disposition"]
    # Die ganze Datei, nicht die gefilterte Ansicht.
    assert "Movie added to Radarr" in response.text
    assert "Could not add movie" in response.text


def test_download_nur_fuer_admins(admin_client: TestClient) -> None:
    create_user(admin_client, "kim")
    kim = auth_headers(admin_client, "kim", "passwort-1234")
    assert admin_client.get("/api/logs/download", headers=kim).status_code == 403


def test_protokoll_leeren(admin_client: TestClient) -> None:
    _schreibe_beispielzeilen()
    assert admin_client.get("/api/logs").json() != []

    assert admin_client.delete("/api/logs").status_code == 204
    assert admin_client.get("/api/logs").json() == []


def test_alte_dateien_werden_geloescht() -> None:
    """Sonst würde das Verzeichnis mit der Zeit volllaufen."""
    logs.setup()
    alt: Path = logs.log_dir() / "nexview.log.9"
    alt.write_text("alt", encoding="utf-8")
    # Auf 20 Tage zurückdatieren.
    veraltet = time.time() - 20 * 86400
    import os

    os.utime(alt, (veraltet, veraltet))

    logs.purge_old()
    assert not alt.exists()


def test_junge_dateien_bleiben() -> None:
    logs.setup()
    jung: Path = logs.log_dir() / "nexview.log.8"
    jung.write_text("neu", encoding="utf-8")

    logs.purge_old()
    assert jung.exists()
    jung.unlink()


def test_nur_admins_sehen_das_protokoll(admin_client: TestClient) -> None:
    create_user(admin_client, "kim")
    kim = auth_headers(admin_client, "kim", "passwort-1234")

    assert admin_client.get("/api/logs", headers=kim).status_code == 403
    assert admin_client.delete("/api/logs", headers=kim).status_code == 403


def test_entscheider_sieht_das_protokoll_nicht(admin_client: TestClient) -> None:
    created = create_user(admin_client, "eva")
    admin_client.patch(f"/api/users/{created['id']}", json={"role": "approver"})
    eva = auth_headers(admin_client, "eva", "passwort-1234")

    assert admin_client.get("/api/logs", headers=eva).status_code == 403


def test_ohne_anmeldung_kein_protokoll(client: TestClient) -> None:
    assert client.get("/api/logs").status_code == 401
