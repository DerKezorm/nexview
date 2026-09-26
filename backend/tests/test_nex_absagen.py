"""nexcrates begruendete Absagen kommen als Absage an, nicht als 502.

Grosser Pruefgang, 26.09.2026: nexcrate lehnte eine Anfrage mit 409
``version_fed_by_source`` ab (der Titel kam aus einem noch verbundenen
Sonarr), und Nexview machte daraus "nexcrate hat abgelehnt." mit 502 - ohne
Grund, ohne Kennung, fuer einen englischen Nutzer auf Deutsch. Dasselbe beim
Zurueckholen aus dem Papierkorb (``recycle_title_gone``).

⚠️ **Kein Satz aus nexcrate wird gezeigt.** Die Absage kommt als Nexviews
eigene Kennung; den Satz baut die Oberflaeche in beiden Sprachen.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus
from app.services.beschaffung import NEX
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import fehler, system
from app.services.settings_service import save_settings

from .beschaffung.fake_nexcrate import FILM_HD, KEY, SERIE_HD, URL, FakeNexcrate

VERTRAG = Path(__file__).parent / "beschaffung" / "nexcrate_openapi.json"


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    attrappe = FakeNexcrate()
    nex_client.use_transport(attrappe.transport())
    system.merken(attrappe._system())
    nex_fassungen.vergessen()
    try:
        yield attrappe
    finally:
        nex_client.use_transport(None)
        system.vergessen()
        nex_fassungen.vergessen()


@pytest.fixture
def nex_admin(admin_client: Any, nexcrate: FakeNexcrate) -> Any:
    with SessionLocal() as sitzung:
        save_settings(
            sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY}
        )
        nex_fassungen.schreiben(sitzung, nexcrate.versions)
        sitzung.commit()
    antwort = admin_client.put(
        "/api/settings/fassungen",
        json=[{"kennung": k, "offen_fuer_alle": True} for k in (FILM_HD, SERIE_HD)],
    )
    assert antwort.status_code == 200, antwort.text
    return admin_client


def _absage(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": f"{code} in English", "params": {}})


# --- Anfragen -------------------------------------------------------------------


@pytest.mark.parametrize("art", ["movie", "tv"])
def test_eine_fassung_die_sonarr_fuettert_ist_eine_absage_und_kein_502(
    nex_admin: Any, nexcrate: FakeNexcrate, art: str
) -> None:
    item = nex_admin.get(f"/api/discover/{art}").json()["items"][0]
    if art == "movie":
        nexcrate.film(item["tmdb_id"], versionen=[])
    else:
        nexcrate.serie(item["tmdb_id"], versionen=[])
    nexcrate.next_answer["POST /api/v1/requests"] = _absage(409, "version_fed_by_source")

    antwort = nex_admin.post(
        "/api/requests", json={"media_type": art, "tmdb_id": item["tmdb_id"]}
    )

    assert antwort.status_code == 409, antwort.text
    detail = antwort.json()["detail"]
    assert detail["code"] == "nexcrate_version_fed_by_source"
    assert detail["nexcrate_code"] == "version_fed_by_source"
    # nexcrates englischer Satz bleibt draussen.
    assert "in English" not in json.dumps(detail)
    with SessionLocal() as sitzung:
        anfrage = sitzung.query(MediaRequest).one()
        assert anfrage.status == RequestStatus.failed
        assert anfrage.error_detail["code"] == "nexcrate_version_fed_by_source"


def test_ein_ausfall_bleibt_ein_502_mit_kennung(nex_admin: Any, nexcrate: FakeNexcrate) -> None:
    """Die Grenze in die andere Richtung: Versagt nexcrate, ist 502 richtig."""
    item = nex_admin.get("/api/discover/movie").json()["items"][0]
    nexcrate.film(item["tmdb_id"], versionen=[])
    nexcrate.next_answer["POST /api/v1/requests"] = httpx.Response(
        502, text="<html><title>Bad Gateway</title></html>"
    )

    antwort = nex_admin.post("/api/requests", json={"media_type": "movie", "tmdb_id": item["tmdb_id"]})

    assert antwort.status_code == 502, antwort.text
    assert antwort.json()["detail"]["code"] == "nexcrate_unavailable"


# --- Papierkorb -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "fremd", "erwartet_status", "erwartet_code"),
    [
        (409, "recycle_title_gone", 409, "nexcrate_recycle_title_gone"),
        # #job-43: die Fassung, zu der die Datei gehoerte, gibt es nicht mehr.
        (409, "recycle_version_gone", 409, "nexcrate_recycle_version_gone"),
        (409, "recycle_file_gone", 409, "nexcrate_recycle_file_gone"),
        (409, "version_fed_by_source", 409, "nexcrate_version_fed_by_source"),
        # Eine erfundene Nummer: nicht "hier antwortet kein nexcrate".
        (404, "not_found", 404, "nexcrate_recycle_entry_gone"),
    ],
)
def test_zurueckholen_sagt_warum_es_nicht_geht(
    nex_admin: Any,
    nexcrate: FakeNexcrate,
    status: int,
    fremd: str,
    erwartet_status: int,
    erwartet_code: str,
) -> None:
    nexcrate.next_answer["POST /api/v1/recycle-bin/1/restore"] = _absage(status, fremd)

    antwort = nex_admin.post("/api/beschaffung/papierkorb/1/zurueckholen")

    assert antwort.status_code == erwartet_status, antwort.text
    assert antwort.json()["detail"]["code"] == erwartet_code


# --- Der Vertrag ------------------------------------------------------------------

#: Die Anfrage-Wege, die Nexview bei nexcrate benutzt.
ANFRAGE_WEGE = (
    ("post", "/api/v1/requests"),
    ("post", "/api/v1/titles/{kind}/{ref}/withdraw"),
    ("put", "/api/v1/titles/{kind}/{ref}/monitoring"),
    ("post", "/api/v1/recycle-bin/{entry_id}/restore"),
)

#: Was dort ohne eigenen Namen bleiben darf - je mit Grund.
OHNE_NAMEN = {
    # Ein 500 ist "noch einmal"; er kommt ungewiss an und wird wiederholt.
    "internal_error",
    # Nexview fragt immer mit ``tmdb:``, und das nimmt nexcrate immer auf.
    "ref_not_addable",
    # Musik fuehrt Nexview nicht.
    "track_not_found",
}


def _codes_im_vertrag(methode: str, pfad: str) -> set[str]:
    vertrag = json.loads(VERTRAG.read_text(encoding="utf-8"))
    antworten = vertrag["paths"][pfad][methode]["responses"]
    codes: set[str] = set()
    for status, antwort in antworten.items():
        if status.startswith(("4", "5")):
            codes |= set(re.findall(r"`([a-z_]+)`", json.dumps(antwort)))
    return codes


@pytest.mark.parametrize(("methode", "pfad"), ANFRAGE_WEGE)
def test_jede_absage_der_anfrage_wege_hat_einen_eigenen_namen(methode: str, pfad: str) -> None:
    """Ein neuer Ablehnungsgrund in nexcrate faellt hier auf, nicht beim Nutzer."""
    codes = _codes_im_vertrag(methode, pfad)
    assert codes, f"Keine Fehlerkennungen fuer {methode} {pfad} im Vertrag gefunden"
    ohne = sorted(
        code
        for code in codes - OHNE_NAMEN
        if fehler.aus_antwort(_absage(409, code), pfad.removeprefix("/api/v1")).code
        == "nexcrate_refused"
    )
    assert ohne == []


def test_eine_begruendete_absage_ist_nie_ein_502() -> None:
    for code in fehler.ABLEHNUNGEN:
        assert 400 <= fehler.NexcrateError(code, status_code=409).antwort_status < 500
    assert fehler.NexcrateError("nexcrate_unavailable", status_code=502).antwort_status == 502
    assert fehler.NexcrateError("nexcrate_key_rejected", status_code=401).antwort_status == 502
