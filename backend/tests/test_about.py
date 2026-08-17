"""Ueber-Seite und Versionspruefung."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.services import updates
from tests.conftest import auth_headers, create_user


@pytest.fixture(autouse=True)
def frischer_zwischenspeicher() -> None:
    """Kein Ergebnis aus einem vorherigen Test uebernehmen."""
    updates.reset_cache()


# --- Versionsvergleich -------------------------------------------------------


@pytest.mark.parametrize(
    ("neuer", "laufend", "erwartet"),
    [
        ("v0.2.0", "0.1.0", True),
        ("0.2.0", "0.1.0", True),
        ("v1.0.0", "0.9.9", True),
        ("v0.1.1", "0.1.0", True),
        ("v0.1.0", "0.1.0", False),
        ("v0.1.0", "0.2.0", False),  # aeltere Veroeffentlichung: kein Update
        ("v0.10.0", "0.9.0", True),  # 10 ist groesser als 9, nicht kleiner
        ("unfug", "0.1.0", False),  # unlesbar -> lieber gar kein Hinweis
        ("v0.2.0", "unfug", False),
    ],
)
def test_versionsvergleich(neuer: str, laufend: str, erwartet: bool) -> None:
    assert updates.is_newer(neuer, laufend) is erwartet


def test_zusatz_am_versionsnamen_stoert_nicht() -> None:
    assert updates.parse_version("v1.2.3-beta.1") == (1, 2, 3)


# --- Endpunkt ----------------------------------------------------------------


def test_ueber_seite_zeigt_version_und_repo(admin_client: TestClient) -> None:
    daten = admin_client.get("/api/about").json()
    assert daten["version"] == __version__
    assert daten["repo_url"].startswith("https://github.com/")


def test_ohne_anmeldung_gesperrt(client: TestClient) -> None:
    assert client.get("/api/about").status_code == 401


def test_normaler_nutzer_bekommt_keinen_update_hinweis(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer nicht aktualisieren kann, soll auch nicht danach gefragt werden.

    Wichtiger Nebeneffekt: fuer normale Nutzer geht ueberhaupt keine Anfrage
    nach aussen - sonst loeste jeder Seitenaufruf eine GitHub-Abfrage aus.
    """
    create_user(admin_client, "lisa")
    gerufen = False

    async def nicht_rufen() -> str | None:
        nonlocal gerufen
        gerufen = True
        return "v9.9.9"

    monkeypatch.setattr(updates, "_abfragen", nicht_rufen)

    kopf = auth_headers(admin_client, "lisa", "passwort-1234")
    daten = admin_client.get("/api/about", headers=kopf).json()

    assert daten["update_checked"] is False
    assert daten["latest_version"] is None
    assert not gerufen


def test_admin_sieht_neuere_version(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def antwort() -> str | None:
        return "v99.0.0"

    monkeypatch.setattr(updates, "_abfragen", antwort)

    daten = admin_client.get("/api/about").json()
    assert daten["update_checked"] is True
    assert daten["latest_version"] == "v99.0.0"
    assert daten["update_available"] is True
    assert daten["checked_at"] is not None


def test_abgeschaltete_pruefung_fragt_nicht_nach(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gerufen = False

    async def nicht_rufen() -> str | None:
        nonlocal gerufen
        gerufen = True
        return "v99.0.0"

    monkeypatch.setattr(updates, "_abfragen", nicht_rufen)
    admin_client.put("/api/settings", json={"update_check": False})

    daten = admin_client.get("/api/about").json()
    assert daten["update_checked"] is False
    assert not gerufen


def test_schalter_bleibt_aus(admin_client: TestClient) -> None:
    """Ein abgeschalteter Schalter muss abgeschaltet bleiben.

    Ein Wahrheitswert wird als Text gespeichert; wird er dabei nicht sauber
    umgewandelt, landet "False" in der Datenbank - und "False" ist beim
    Auslesen schlicht nicht leer, also wieder an.
    """
    admin_client.put("/api/settings", json={"update_check": False})
    assert admin_client.get("/api/settings").json()["update_check"] is False

    admin_client.put("/api/settings", json={"update_check": True})
    assert admin_client.get("/api/settings").json()["update_check"] is True


def test_ausfall_bei_github_bleibt_folgenlos(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein Netz, kein GitHub - die Seite muss trotzdem funktionieren."""

    async def kracht() -> str | None:
        raise OSError("keine Verbindung")

    monkeypatch.setattr(updates, "_abfragen", kracht)

    antwort = admin_client.get("/api/about")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["version"] == __version__
    assert daten["update_available"] is False


def test_es_wird_nur_einmal_gefragt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Zwischenspeicher verhindert eine Abfrage pro Seitenaufruf."""
    anzahl = 0

    async def zaehlen() -> str | None:
        nonlocal anzahl
        anzahl += 1
        return "v0.1.0"

    monkeypatch.setattr(updates, "_abfragen", zaehlen)

    admin_client.get("/api/about")
    admin_client.get("/api/about")
    admin_client.get("/api/about")
    assert anzahl == 1

    # Der Knopf "jetzt pruefen" umgeht den Zwischenspeicher bewusst.
    admin_client.post("/api/about/check")
    assert anzahl == 2


def test_jetzt_pruefen_ist_admin_sache(admin_client: TestClient) -> None:
    create_user(admin_client, "tom")
    kopf = auth_headers(admin_client, "tom", "passwort-1234")
    assert admin_client.post("/api/about/check", headers=kopf).status_code == 403
