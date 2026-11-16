"""``NEXVIEW_SEERR_PORT``: der Seerr-Port von aussen (Issue #17, TrueNAS).

Ein App-Katalog kann einen Port nur veroeffentlichen, wenn er ihn auch
einstellen kann - und das geht dort nur ueber eine Umgebungsvariable. Ist sie
gesetzt, gilt ihr Port, und die Schnittstelle ist ab Werk an. Ein Schalter,
den jemand in der Oberflaeche selbst umgelegt hat, bleibt, wie er ist.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.seerr_api import lauscher
from app.services import settings_service

VARIABLE = "NEXVIEW_SEERR_PORT"


def _stand(client: TestClient) -> dict:
    antwort = client.get("/api/settings")
    assert antwort.status_code == 200
    return antwort.json()


def test_ohne_variable_bleibt_alles_wie_bisher(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    stand = _stand(admin_client)
    assert stand["seerr_api_enabled"] is False
    assert stand["seerr_api_port"] == lauscher.STANDARD_PORT
    assert stand["seerr_api_port_from_env"] is False


def test_variable_setzt_den_port_und_schaltet_ab_werk_ein(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VARIABLE, "5099")
    stand = _stand(admin_client)
    assert stand["seerr_api_enabled"] is True
    assert stand["seerr_api_port"] == 5099
    assert stand["seerr_api_port_from_env"] is True
    # Der Lauscher liest dieselben Einstellungen - er oeffnet genau diesen Port.
    assert lauscher._wunsch() == (True, 5099)


def test_variable_geht_vor_dem_gespeicherten_port_und_der_bleibt_erhalten(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    assert admin_client.put("/api/settings", json={"seerr_api_port": 5060}).status_code == 200

    monkeypatch.setenv(VARIABLE, "5099")
    assert _stand(admin_client)["seerr_api_port"] == 5099

    # Faellt die Variable weg, gilt wieder, was eingestellt war.
    monkeypatch.delenv(VARIABLE)
    stand = _stand(admin_client)
    assert stand["seerr_api_port"] == 5060
    assert stand["seerr_api_port_from_env"] is False


def test_ausgeschaltet_bleibt_aus_auch_mit_variable(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VARIABLE, "5099")
    assert admin_client.put("/api/settings", json={"seerr_api_enabled": False}).status_code == 200
    assert _stand(admin_client)["seerr_api_enabled"] is False
    assert lauscher._wunsch() == (False, 5099)

    assert admin_client.put("/api/settings", json={"seerr_api_enabled": True}).status_code == 200
    assert _stand(admin_client)["seerr_api_enabled"] is True


def test_ein_anderer_port_wird_abgelehnt_derselbe_nicht_gespeichert(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VARIABLE, "5099")

    anders = admin_client.put("/api/settings", json={"seerr_api_enabled": True, "seerr_api_port": 5100})
    assert anders.status_code == 409
    assert anders.json()["detail"]["code"] == "seerr_api_port_from_env"
    assert anders.json()["detail"]["port"] == 5099

    gleich = admin_client.put("/api/settings", json={"seerr_api_enabled": True, "seerr_api_port": 5099})
    assert gleich.status_code == 200
    assert gleich.json()["seerr_api_port"] == 5099

    # Nichts davon steht in der Datenbank: ohne Variable wieder Seerrs Port.
    monkeypatch.delenv(VARIABLE)
    assert _stand(admin_client)["seerr_api_port"] == lauscher.STANDARD_PORT


@pytest.mark.parametrize("wert", ["80", "70000", "abc", "", "  "])
def test_unbrauchbarer_wert_wird_uebergangen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, wert: str
) -> None:
    monkeypatch.setenv(VARIABLE, wert)
    stand = _stand(admin_client)
    assert stand["seerr_api_enabled"] is False
    assert stand["seerr_api_port"] == lauscher.STANDARD_PORT
    assert stand["seerr_api_port_from_env"] is False
    # Ohne Variable wird ein Port wie immer gespeichert.
    assert admin_client.put("/api/settings", json={"seerr_api_port": 5061}).status_code == 200


def test_der_startbericht_nennt_einen_unbrauchbaren_wert(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(VARIABLE, "80")
    with caplog.at_level(logging.INFO, logger="nexview.settings"):
        settings_service.verbindungsbericht()
    assert any(VARIABLE in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)

    caplog.clear()
    monkeypatch.setenv(VARIABLE, "5099")
    with caplog.at_level(logging.INFO, logger="nexview.settings"):
        settings_service.verbindungsbericht()
    assert any("5099" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)
