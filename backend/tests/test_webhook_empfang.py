"""Der Empfaenger des Rueckkanals: Wer darf wecken, und was zaehlt als Beweis?

Festgenagelt wird die Sicherheits-Logik des Anruf-Pfads, bevor irgendetwas
daran haengt:

* Nur ein Anruf mit dem **Geheimnis der Instanz** kommt durch - fehlt es,
  stimmt es nicht oder wurde nie eines vergeben, ist die Antwort dieselbe 401
  (von aussen soll nicht erkennbar sein, welche Instanzen einen Rueckkanal
  haben).
* Ein gueltiger Anruf **weckt nur**: Er setzt das Signal und die Anzeige-
  Zeitstempel, sonst nichts. Dem Inhalt wird nichts geglaubt - ein
  unlesbarer Koerper ist deshalb auch kein Ablehnungsgrund.
* Die **Probe** (eventType "Test") ist der Erreichbarkeits-Beweis. Sie setzt
  ``bewiesen_am`` und weckt ausdruecklich nicht - Sonarrs Test-Knopf soll
  keinen Rundgang ausloesen.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db import SessionLocal
from app.services import webhooks
from app.services.settings_service import save_settings

RADARR = {"radarr_url": "http://127.0.0.1:7878", "radarr_api_key": "schluessel-r"}
PFAD = "/api/webhooks/arr/radarr-standard"


@pytest.fixture(autouse=True)
def _frischer_wecker(monkeypatch):
    """Je Test ein neues Signal - warum, steht in test_webhook_wecker.py."""
    monkeypatch.setattr(webhooks, "_weckruf", asyncio.Event())


def _radarr_eingerichtet(mit_geheimnis: bool = True) -> str:
    """Instanz eintragen und ein Anruf-Geheimnis vergeben; liefert den Klartext."""
    with SessionLocal() as db:
        save_settings(db, RADARR)
        if not mit_geheimnis:
            return ""
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        return webhooks.geheimnis_klartext(zeile)


def _stand() -> tuple:
    with SessionLocal() as db:
        zeile = webhooks.eintrag(db, "radarr-standard")
        assert zeile is not None
        return (zeile.bewiesen_am, zeile.zuletzt_angerufen_am, zeile.letztes_ereignis)


def test_unbekannte_kennung_ist_404(client) -> None:
    """Nicht eingerichtet heisst: gibt es nicht - auch mit gueltigem Geheimnis."""
    geheimnis = _radarr_eingerichtet()

    antwort = client.post(
        "/api/webhooks/arr/sonarr-uhd",
        json={"eventType": "Download"},
        auth=("nexview", geheimnis),
    )

    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "webhook_unknown_instance"
    assert not webhooks.weckruf().is_set()


def test_ohne_geheimnis_kommt_niemand_durch(client) -> None:
    _radarr_eingerichtet()

    antwort = client.post(PFAD, json={"eventType": "Download"})

    assert antwort.status_code == 401
    assert antwort.json()["detail"]["code"] == "webhook_secret_rejected"
    assert not webhooks.weckruf().is_set()
    bewiesen, angerufen, _ = _stand()
    assert bewiesen is None and angerufen is None


def test_falsches_geheimnis_aendert_nichts(client) -> None:
    _radarr_eingerichtet()

    antwort = client.post(
        PFAD, json={"eventType": "Download"}, auth=("nexview", "geraten")
    )

    assert antwort.status_code == 401
    assert not webhooks.weckruf().is_set()
    bewiesen, angerufen, _ = _stand()
    assert bewiesen is None and angerufen is None


def test_nie_vergebenes_geheimnis_heisst_401(client) -> None:
    """Eingerichtete Instanz, aber es wurde nie ein Geheimnis erzeugt.

    Dieselbe Antwort wie beim falschen Geheimnis - wo keines vergeben wurde,
    kann keines stimmen, und der Unterschied geht niemanden etwas an.
    """
    _radarr_eingerichtet(mit_geheimnis=False)

    antwort = client.post(PFAD, json={"eventType": "Download"}, auth=("nexview", ""))

    assert antwort.status_code == 401
    assert not webhooks.weckruf().is_set()


def test_echter_anruf_weckt_und_wird_vermerkt(client) -> None:
    geheimnis = _radarr_eingerichtet()

    antwort = client.post(
        PFAD, json={"eventType": "Download"}, auth=("nexview", geheimnis)
    )

    assert antwort.status_code == 204
    assert webhooks.weckruf().is_set()
    bewiesen, angerufen, ereignis = _stand()
    assert angerufen is not None
    assert ereignis == "Download"
    # Ein echter Anruf ist kein Beweis: Der kommt nur von der Probe.
    assert bewiesen is None


def test_probe_beweist_und_weckt_nicht(client) -> None:
    """Sonarrs Test-Knopf sagt "die Strecke steht" - mehr nicht."""
    geheimnis = _radarr_eingerichtet()

    antwort = client.post(PFAD, json={"eventType": "Test"}, auth=("nexview", geheimnis))

    assert antwort.status_code == 204
    assert not webhooks.weckruf().is_set()
    bewiesen, angerufen, ereignis = _stand()
    assert bewiesen is not None
    assert angerufen is not None
    assert ereignis == "Test"


def test_unlesbarer_inhalt_weckt_trotzdem(client) -> None:
    """Das Geheimnis stimmte, also hat die Instanz angerufen - der Inhalt ist
    ohnehin unglaubwuerdig und darf deshalb auch nicht zum Tuersteher werden."""
    geheimnis = _radarr_eingerichtet()

    antwort = client.post(
        PFAD,
        content=b"kein json",
        headers={"Content-Type": "text/plain"},
        auth=("nexview", geheimnis),
    )

    assert antwort.status_code == 204
    assert webhooks.weckruf().is_set()
    _, angerufen, ereignis = _stand()
    assert angerufen is not None
    assert ereignis == ""
