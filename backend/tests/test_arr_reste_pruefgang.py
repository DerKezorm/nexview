"""Zwei Reste aus dem grossen Pruefgang, beide im Arr-Betrieb (nexbase #job-59, 28.09.2026).

* Ein angekommener Test-Anruf nimmt den gespeicherten Grund zurueck, den er gerade widerlegt hat. Vorher stand
  "Es fehlt eine Adresse" direkt ueber "Anruf kam an", bis die Pflege das naechste Mal lief.
* Die ganze Bibliothek (``/movie``, ``/series``) bekommt eine laengere Frist als jeder andere Aufruf, und ein
  Speicher-Abgleich, bei dem eine Instanz schwieg, wird nach fuenf Minuten nachgeholt statt nach einer Stunde.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services import status_poller, storage
from app.services.beschaffung.arr import client as arr_client
from app.services.beschaffung.arr import library, webhook_pflege, webhooks
from app.services.beschaffung.arr.client import ArrError
from app.services.beschaffung.arr.radarr import RadarrClient
from app.services.beschaffung.arr.sonarr import SonarrClient
from app.services.settings_service import load_settings

from .test_webhook_pflege import _radarr, _zeile, fake  # noqa: F401 - ``fake`` ist eine Fixture


def _grund_setzen(code: str) -> None:
    with SessionLocal() as db:
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.fehler = code
        zeile.fehler_info = ""
        db.commit()


@pytest.mark.anyio
@pytest.mark.parametrize("grund", ["no_address", "unreachable", "proof_failed"])
async def test_ein_angekommener_anruf_nimmt_den_widerlegten_grund_zurueck(fake, monkeypatch, grund) -> None:  # noqa: F811
    settings, instanz = _radarr()
    _grund_setzen(grund)
    monkeypatch.setattr(webhook_pflege, "_zuletzt", 123.0)

    with SessionLocal() as db:
        ergebnis = await webhook_pflege.testen(db, settings, instanz)

    assert ergebnis["angekommen"] is True
    assert _zeile().fehler == ""
    assert webhook_pflege._zuletzt == 0.0, "die Pflege soll bald nachziehen"


@pytest.mark.anyio
@pytest.mark.parametrize("grund", ["too_old", "create_failed"])
async def test_was_der_anruf_nicht_beweist_bleibt_stehen(fake, grund) -> None:  # noqa: F811
    settings, instanz = _radarr()
    _grund_setzen(grund)

    with SessionLocal() as db:
        ergebnis = await webhook_pflege.testen(db, settings, instanz)

    assert ergebnis["angekommen"] is True
    assert _zeile().fehler == grund


@pytest.mark.anyio
async def test_ein_gescheiterter_anruf_laesst_den_grund_stehen(fake) -> None:  # noqa: F811
    fake.probe = "silent"
    settings, instanz = _radarr()
    _grund_setzen("no_address")

    with SessionLocal() as db:
        ergebnis = await webhook_pflege.testen(db, settings, instanz)

    assert ergebnis["angekommen"] is False
    assert _zeile().fehler == "no_address"


@pytest.mark.anyio
@pytest.mark.parametrize(("klasse", "pfad"), [(RadarrClient, "/movie"), (SonarrClient, "/series")])
async def test_die_ganze_bibliothek_bekommt_die_lange_frist(monkeypatch, klasse, pfad) -> None:
    gesehen: list[tuple[str, Any]] = []

    async def aufzeichnen(self, method: str, path: str, timeout=None, **kwargs):
        gesehen.append((path, timeout))
        return []

    monkeypatch.setattr(arr_client.ArrClient, "_request", aufzeichnen)
    instanz = klasse("http://arr.invalid", "x")
    await instanz.library()
    await instanz.get("/system/status")

    assert gesehen == [(pfad, arr_client.LISTE_TIMEOUT), ("/system/status", None)]
    assert arr_client.LISTE_TIMEOUT.read >= 60 > arr_client.TIMEOUT.read


@pytest.mark.anyio
async def test_ein_abgleich_mit_stummer_instanz_ist_unvollstaendig(monkeypatch) -> None:
    with SessionLocal() as db:
        mit_radarr = replace(load_settings(db), radarr_url="http://radarr.invalid", radarr_api_key="x")

        async def leer(*_args, **_kwargs):
            return {}

        monkeypatch.setattr(library, "movie_library", leer)
        assert (await storage.abgleichen(db, mit_radarr)).vollstaendig is True

        async def stumm(*_args, **_kwargs):
            raise ArrError("Radarr antwortet nicht", service="radarr", code="arr_timeout")

        monkeypatch.setattr(library, "movie_library", stumm)
        assert (await storage.abgleichen(db, mit_radarr)).vollstaendig is False


@pytest.mark.anyio
@pytest.mark.parametrize(("vollstaendig", "naechster"), [(True, 10_000.0), (False, 10_000.0 - 3600 + 300)])
async def test_ein_unvollstaendiger_abgleich_kommt_nach_fuenf_minuten_wieder(monkeypatch, vollstaendig, naechster) -> None:
    async def abgleich(_db: Session, _settings) -> storage.Ergebnis:
        return storage.Ergebnis(vollstaendig=vollstaendig)

    monkeypatch.setattr(storage, "abgleichen", abgleich)
    monkeypatch.setattr(status_poller.time, "monotonic", lambda: 10_000.0)
    monkeypatch.setattr(status_poller, "_speicher_zuletzt", 0.0)
    with SessionLocal() as db:
        mit_radarr = replace(load_settings(db), radarr_url="http://radarr.invalid", radarr_api_key="x")
        await status_poller._speicher_vielleicht(db, mit_radarr)

    assert status_poller._speicher_zuletzt == naechster
    assert status_poller.SPEICHER_NACHHOLEN_SEKUNDEN == 300
