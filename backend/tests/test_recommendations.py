"""„Andere zeigen" bei den Vorschlägen auf der Detailseite.

Der Knopf hat nur einen Zweck: eine *andere* Auswahl. Genau das wird hier
geprüft — dass sich zwei aufeinanderfolgende Runden nicht überschneiden, dass
nach der letzten wieder die erste kommt, und dass ein Titel mit nur einer
Handvoll Vorschlägen gar keinen Knopf bekommt.

Der Vorrat selbst wird untergeschoben: er käme sonst von TMDB, und wie viele
Vorschläge ein Film dort hat, ist keine Eigenschaft dieses Codes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers import details
from app.schemas_media import MediaItem
from tests.conftest import auth_headers, create_user

PFAD = "/api/detail/movie/550/recommendations"


def _vorrat(anzahl: int) -> list[MediaItem]:
    """So viele erfundene Titel, durchnummeriert ab 1000."""
    return [
        MediaItem(media_type="movie", tmdb_id=1000 + nummer, title=f"Titel {nummer}")
        for nummer in range(anzahl)
    ]


@pytest.fixture
def nutzer(admin_client: TestClient) -> dict[str, str]:
    """Ein gewoehnliches Konto - die Auswahl ist keine Admin-Sache."""
    create_user(admin_client, "lena")
    return auth_headers(admin_client, "lena", "passwort-1234")


@pytest.fixture
def vorrat_von(monkeypatch: pytest.MonkeyPatch):
    """Legt fest, wie viele Vorschläge TMDB angeblich kennt."""

    def setzen(anzahl: int) -> None:
        async def gefaelscht(*_args: Any, **_kwargs: Any) -> list[MediaItem]:
            return _vorrat(anzahl)

        monkeypatch.setattr(details.media, "empfehlungs_vorrat", gefaelscht)

    return setzen


def _kennungen(client: TestClient, kopf: dict[str, str], runde: int) -> list[int]:
    antwort = client.get(f"{PFAD}?runde={runde}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return [eintrag["tmdb_id"] for eintrag in antwort.json()["items"]]


def test_zweite_runde_zeigt_andere_titel(
    admin_client: TestClient, nutzer: dict[str, str], vorrat_von
) -> None:
    """Der ganze Sinn des Knopfes."""
    vorrat_von(40)

    erste = _kennungen(admin_client, nutzer, 0)
    zweite = _kennungen(admin_client, nutzer, 1)

    assert len(erste) == 12
    assert len(zweite) == 12
    assert not set(erste) & set(zweite)


def test_jede_runde_ist_neu_bis_der_vorrat_alle_ist(
    admin_client: TestClient, nutzer: dict[str, str], vorrat_von
) -> None:
    vorrat_von(36)

    gesehen: list[int] = []
    for runde in range(3):
        gesehen += _kennungen(admin_client, nutzer, runde)

    assert len(gesehen) == 36
    assert len(set(gesehen)) == 36


def test_nach_der_letzten_runde_geht_es_von_vorn_los(
    admin_client: TestClient, nutzer: dict[str, str], vorrat_von
) -> None:
    """Ein Knopf, der irgendwann nichts mehr tut, wäre ärgerlicher als eine
    Wiederholung."""
    vorrat_von(30)  # drei Runden: 12, 12, 6

    assert _kennungen(admin_client, nutzer, 3) == _kennungen(admin_client, nutzer, 0)
    assert admin_client.get(f"{PFAD}?runde=3", headers=nutzer).json()["runde"] == 0


def test_letzte_runde_darf_kuerzer_sein(
    admin_client: TestClient, nutzer: dict[str, str], vorrat_von
) -> None:
    vorrat_von(20)
    assert len(_kennungen(admin_client, nutzer, 1)) == 8


def test_bei_wenigen_vorschlaegen_gibt_es_nichts_zu_wechseln(
    admin_client: TestClient, nutzer: dict[str, str], vorrat_von
) -> None:
    """Darauf blendet die Oberfläche den Knopf aus."""
    vorrat_von(9)

    daten = admin_client.get(PFAD, headers=nutzer).json()
    assert daten["runden"] == 1
    assert len(daten["items"]) == 9


def test_ohne_vorschlaege_kein_fehler(
    admin_client: TestClient, nutzer: dict[str, str], vorrat_von
) -> None:
    """Im Demo-Betrieb und bei unbekannten Titeln ist der Vorrat leer."""
    vorrat_von(0)

    daten = admin_client.get(PFAD, headers=nutzer).json()
    assert daten == {"items": [], "runde": 0, "runden": 0}


def test_nur_fuer_angemeldete(client: TestClient) -> None:
    assert client.get(PFAD, headers={"Authorization": ""}).status_code == 401
