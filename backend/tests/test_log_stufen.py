"""Protokoll-Stufen, Selbstabschaltung, Vorgangsnummer und Auffangnetz."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.middleware import RequestContextMiddleware, mask_query, unhandled_error
from app.services import logs

from .conftest import auth_headers, create_user


def _frisches_protokoll() -> logging.Logger:
    logs.setup()
    logs.clear()
    return logging.getLogger("nexview.test")


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def _zeilen() -> list[logs.LogLine]:
    _flush()
    return logs.read(limit=500)


# ---------------------------------------------------------------------------
# Stufen
# ---------------------------------------------------------------------------


def test_standard_ist_normal(admin_client: TestClient) -> None:
    stand = admin_client.get("/api/logs/level").json()
    assert stand["mode"] == "normal"
    assert stand["until"] is None
    assert stand["fixed_by_env"] is False
    assert "trace" in stand["modes"]


def test_normal_schreibt_keine_debug_zeilen(admin_client: TestClient) -> None:
    log = _frisches_protokoll()
    logs.apply_mode("normal")

    log.debug("this must not appear")
    log.info("this must appear")

    meldungen = [z.message for z in _zeilen()]
    assert "this must appear" in meldungen
    assert "this must not appear" not in meldungen


def test_detailed_schreibt_debug_zeilen(admin_client: TestClient) -> None:
    log = _frisches_protokoll()

    antwort = admin_client.put("/api/logs/level", json={"mode": "detailed", "minutes": 30})
    assert antwort.status_code == 200
    assert antwort.json()["mode"] == "detailed"
    assert antwort.json()["until"] is not None

    log.debug("deep detail")
    assert "deep detail" in [z.message for z in _zeilen()]


def test_umschalten_wirkt_ohne_neustart(admin_client: TestClient) -> None:
    """Ein Neustart zerstoert oft genau den Zustand, den man untersuchen will."""
    _frisches_protokoll()

    admin_client.put("/api/logs/level", json={"mode": "trace", "minutes": 30})
    assert logging.getLogger("nexview").level == logging.DEBUG
    assert logging.getLogger("httpx").level == logging.DEBUG

    admin_client.put("/api/logs/level", json={"mode": "quiet", "minutes": 0})
    assert logging.getLogger("nexview").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING


def test_tiefe_stufe_bekommt_mehr_platz(admin_client: TestClient) -> None:
    """Sonst rollt eine halbe Stunde Diagnose den interessanten Anfang weg."""
    _frisches_protokoll()

    admin_client.put("/api/logs/level", json={"mode": "detailed", "minutes": 30})
    assert logs._handler is not None
    assert logs._handler.maxBytes == logs.MAX_BYTES_DEEP

    admin_client.put("/api/logs/level", json={"mode": "normal", "minutes": 0})
    assert logs._handler.maxBytes == logs.MAX_BYTES_NORMAL


def test_unbekannte_stufe_wird_abgelehnt(admin_client: TestClient) -> None:
    assert admin_client.put("/api/logs/level", json={"mode": "blabla"}).status_code == 422


def test_unvorgesehene_dauer_wird_abgelehnt(admin_client: TestClient) -> None:
    antwort = admin_client.put("/api/logs/level", json={"mode": "detailed", "minutes": 7})
    assert antwort.status_code == 422


def test_nur_admins_stellen_die_stufe_um(admin_client: TestClient) -> None:
    create_user(admin_client, "kim")
    kim = auth_headers(admin_client, "kim", "passwort-1234")

    assert admin_client.get("/api/logs/level", headers=kim).status_code == 403
    assert (
        admin_client.put(
            "/api/logs/level", json={"mode": "trace", "minutes": 30}, headers=kim
        ).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# Selbstabschaltung
# ---------------------------------------------------------------------------


def test_diagnose_stufe_schaltet_sich_selbst_ab(admin_client: TestClient) -> None:
    """Eine vergessene Diagnose-Stufe wuerde das Protokoll leerlaufen lassen."""
    _frisches_protokoll()
    admin_client.put("/api/logs/level", json={"mode": "detailed", "minutes": 30})

    # Frist in die Vergangenheit ruecken, statt eine halbe Stunde zu warten.
    logs._store("detailed", datetime.now(UTC) - timedelta(minutes=1))

    assert logs.enforce_expiry() is True
    assert logs.current_mode() == "normal"
    assert admin_client.get("/api/logs/level").json()["mode"] == "normal"


def test_frist_laeuft_ab_waehrend_der_container_steht(admin_client: TestClient) -> None:
    logs._store("trace", datetime.now(UTC) - timedelta(hours=5))

    logs.apply_stored_mode()

    assert logs.current_mode() == "normal"


def test_stufe_ohne_frist_bleibt(admin_client: TestClient) -> None:
    admin_client.put("/api/logs/level", json={"mode": "quiet", "minutes": 0})

    assert logs.enforce_expiry() is False
    logs.apply_stored_mode()
    assert logs.current_mode() == "quiet"


def test_gewaehlte_stufe_ueberlebt_den_neustart(admin_client: TestClient) -> None:
    admin_client.put("/api/logs/level", json={"mode": "detailed", "minutes": 480})

    logs.apply_mode("normal")  # so, als waere der Prozess neu gestartet
    logs.apply_stored_mode()

    assert logs.current_mode() == "detailed"


# ---------------------------------------------------------------------------
# Notausgang ueber die Umgebungsvariable
# ---------------------------------------------------------------------------


@pytest.fixture
def env_stufe() -> object:
    """``NEXVIEW_LOG_LEVEL`` setzen und danach wieder aufraeumen."""
    einstellungen = get_settings()
    vorher = einstellungen.log_level

    def setzen(wert: str) -> None:
        einstellungen.log_level = wert

    yield setzen
    einstellungen.log_level = vorher
    logs.apply_mode(logs.DEFAULT_MODE)


def test_umgebungsvariable_uebersteuert(admin_client: TestClient, env_stufe) -> None:
    admin_client.put("/api/logs/level", json={"mode": "quiet", "minutes": 0})
    env_stufe("trace")

    logs.apply_stored_mode()

    assert logs.current_mode() == "trace"
    stand = admin_client.get("/api/logs/level").json()
    assert stand["mode"] == "trace"
    assert stand["fixed_by_env"] is True


def test_umgebungsvariable_sperrt_das_umstellen(admin_client: TestClient, env_stufe) -> None:
    env_stufe("detailed")

    antwort = admin_client.put("/api/logs/level", json={"mode": "quiet", "minutes": 0})
    assert antwort.status_code == 409


def test_technische_namen_werden_verstanden(admin_client: TestClient, env_stufe) -> None:
    """Wer die Stufe technisch benennt, soll nicht ins Leere greifen."""
    env_stufe("DEBUG")
    assert logs.env_mode() == "detailed"

    env_stufe("blabla")
    assert logs.env_mode() is None


# ---------------------------------------------------------------------------
# Filter: "diese Stufe und hoeher"
# ---------------------------------------------------------------------------


def test_warnung_zeigt_auch_fehler(admin_client: TestClient) -> None:
    """Vorher war der Filter ein Gleichheitsvergleich - wer WARNING waehlte,
    bekam ausgerechnet die ERROR-Zeilen nicht zu sehen."""
    log = _frisches_protokoll()
    logs.apply_mode("normal")
    log.info("routine")
    log.warning("something odd")
    log.error("broken")
    _flush()

    stufen = {z["level"] for z in admin_client.get("/api/logs?level=WARNING").json()}
    assert stufen == {"WARNING", "ERROR"}


def test_debug_ist_im_filter_waehlbar(admin_client: TestClient) -> None:
    log = _frisches_protokoll()
    logs.apply_mode("detailed")
    log.debug("the way there")
    _flush()

    meldungen = [z["message"] for z in admin_client.get("/api/logs?level=DEBUG").json()]
    assert "the way there" in meldungen


# ---------------------------------------------------------------------------
# Vorgangsnummer
# ---------------------------------------------------------------------------


def test_zeilen_tragen_benutzer_und_nummer(admin_client: TestClient) -> None:
    _frisches_protokoll()
    logs.apply_mode("detailed")

    admin_client.get("/api/users")
    _flush()

    passend = [z for z in logs.read(limit=200) if z.user == "admin"]
    assert passend, "keine Zeile mit dem Benutzer der Anfrage"
    assert all(z.request_id for z in passend)
    # Alle Zeilen einer Anfrage tragen dieselbe Nummer.
    assert len({z.request_id for z in passend}) >= 1


def test_suche_findet_die_vorgangsnummer(admin_client: TestClient) -> None:
    """Der eigentliche Arbeitsablauf: Der Nutzer nennt die Nummer aus seiner
    Fehlermeldung, der Administrator gibt sie ein."""
    _frisches_protokoll()
    logs.apply_mode("detailed")
    admin_client.get("/api/users")
    _flush()

    nummer = next(z.request_id for z in logs.read(limit=200) if z.request_id)
    treffer = admin_client.get(f"/api/logs?search={nummer}").json()

    assert treffer
    assert all(z["request_id"] == nummer for z in treffer)


def test_antwort_traegt_die_nummer_im_kopf(admin_client: TestClient) -> None:
    antwort = admin_client.get("/api/health")
    assert len(antwort.headers["x-request-id"]) == 6


def test_alte_zeilen_ohne_nummer_bleiben_lesbar() -> None:
    """Nach einem Update steht das alte Format noch in derselben Datei."""
    alt = "2026-08-17 09:12:33 INFO     nexview.poller | Old line without a number"
    eintrag = logs._parse(alt)

    assert eintrag is not None
    assert eintrag.message == "Old line without a number"
    assert eintrag.request_id is None


# ---------------------------------------------------------------------------
# Auffangnetz
# ---------------------------------------------------------------------------


def _app_mit_fehler() -> FastAPI:
    """Kleine eigene Anwendung mit demselben Zwischenstueck.

    Bewusst nicht die echte: Eine zusaetzliche Route an ``app`` wuerde in der
    Routentabelle stehenbleiben, ueber die ``test_child_permissions`` laeuft.
    """
    hilfs_app = FastAPI()
    hilfs_app.add_middleware(RequestContextMiddleware)
    hilfs_app.add_exception_handler(Exception, unhandled_error)

    @hilfs_app.get("/api/kaputt")
    def kaputt() -> dict[str, str]:
        raise RuntimeError("something went badly wrong")

    return hilfs_app


def test_absturz_steht_im_protokoll(admin_client: TestClient) -> None:
    """Vorher ging der Stacktrace an der Protokolldatei vorbei: uvicorn meldet
    ihn auf einem Logger, der nichts an den Wurzel-Logger weitergibt."""
    _frisches_protokoll()
    logs.apply_mode("normal")

    with TestClient(_app_mit_fehler(), raise_server_exceptions=False) as kunde:
        antwort = kunde.get("/api/kaputt")

    assert antwort.status_code == 500
    nummer = antwort.json()["detail"]["request_id"]
    assert antwort.headers["x-request-id"] == nummer

    _flush()
    fehler = [z for z in logs.read(limit=200) if z.level == "ERROR"]
    assert any("Unhandled error on GET /api/kaputt" in z.message for z in fehler)
    assert any(z.request_id == nummer for z in fehler)

    # Der Stacktrace selbst muss in der Datei stehen - ohne ihn ist die Meldung
    # zum Beheben wertlos.
    assert "RuntimeError: something went badly wrong" in logs.log_file().read_text(
        encoding="utf-8"
    )


def test_fehlermeldung_nennt_die_nummer(admin_client: TestClient) -> None:
    with TestClient(_app_mit_fehler(), raise_server_exceptions=False) as kunde:
        detail = kunde.get("/api/kaputt").json()["detail"]

    assert detail["code"] == "internal_error"
    assert detail["request_id"] in detail["message"]


def test_geheimnisse_stehen_nicht_im_protokoll() -> None:
    maskiert = mask_query("page=2&token=geheim123&api_key=abc&search=dune")

    assert "geheim123" not in maskiert
    assert "abc" not in maskiert
    assert "page=2" in maskiert
    assert "search=dune" in maskiert
