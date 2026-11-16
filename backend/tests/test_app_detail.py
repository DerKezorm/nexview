"""Die ganze Titelseite fuer die Fernseh-App: ``/api/app/1/detail/{type}/{id}``.

nexcinema zeigt in Entdecken auf der Seite eines fehlenden Titels den Trailer
und die Besetzung. Die kurze Fassung ``/api/app/1/media/...`` traegt beides
nicht; die App bekam deshalb nie einen Trailer zu sehen. Vertrag in
``docs/app-discover.md``.

Bewacht wird, was die Zweitschrift nicht verlieren darf: Trailer und Besetzung
kommen mit dem App-Schluessel an, die Altersgrenze gilt wie im Browser.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services import media

from .test_altersfilter_waechter import ERLAUBT, GESPERRT, _FakeTmdb, beschraenkt  # noqa: F401
from .test_app_kopplung import _abholen, _bearer, _code


class _MitTrailer(_FakeTmdb):
    """Die Attrappe des Waechters, dazu eine Besetzung und Videos wie bei TMDB."""

    async def detail(self, media_type: str, tmdb_id: int, **k: Any) -> dict[str, Any]:
        daten = await super().detail(media_type, tmdb_id, **k)
        daten["credits"] = {
            "cast": [
                {"id": 31, "name": "Jane Doe", "character": "Kapitaenin", "profile_path": "/j.jpg"},
                {"id": 32, "name": "John Roe", "character": "", "profile_path": None},
            ],
            "crew": [],
        }
        daten["videos"] = {
            "results": [
                {"site": "YouTube", "key": "teaser1", "type": "Teaser", "official": True, "iso_639_1": "de"},
                {"site": "YouTube", "key": "trailer1", "type": "Trailer", "official": True, "iso_639_1": "de",
                 "name": "Offizieller Trailer"},
            ]
        }
        return daten


@pytest.fixture()
def app_kopf(
    beschraenkt: tuple[TestClient, dict[str, str]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> tuple[TestClient, dict[str, str]]:
    """Das Konto mit Altersgrenze 12 des Waechters, angemeldet mit einem App-Schluessel."""
    client, kopf = beschraenkt
    monkeypatch.setattr(media, "TmdbClient", _MitTrailer)
    monkeypatch.setattr(media, "_client", lambda settings, region=None: _MitTrailer())
    schluessel = _abholen(client, _code(client, kopf)).json()["key"]
    return client, _bearer(schluessel)


def test_trailer_und_besetzung_kommen_in_der_app_an(app_kopf: tuple[TestClient, dict[str, str]]) -> None:
    client, kopf = app_kopf
    antwort = client.get(f"/api/app/1/detail/movie/{ERLAUBT}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    titel = antwort.json()

    assert titel["tmdb_id"] == ERLAUBT
    assert titel["trailer"]["key"] == "trailer1", "der Trailer vor dem Teaser"
    assert titel["trailer"]["site"] == "YouTube"
    besetzung = titel["cast"]
    assert [p["person_id"] for p in besetzung] == [31, 32]
    assert besetzung[0]["character"] == "Kapitaenin"
    assert besetzung[0]["photo_url"].endswith("/j.jpg")


def test_die_kurze_fassung_bleibt_ohne_trailer(app_kopf: tuple[TestClient, dict[str, str]]) -> None:
    """``/media`` aendert sich nicht: Aeltere Apps lesen es weiter so."""
    client, kopf = app_kopf
    antwort = client.get(f"/api/app/1/media/movie/{ERLAUBT}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    assert "trailer" not in antwort.json() and "cast" not in antwort.json()


def test_ueber_der_altersgrenze_404(app_kopf: tuple[TestClient, dict[str, str]]) -> None:
    client, kopf = app_kopf
    assert client.get(f"/api/app/1/detail/movie/{GESPERRT}", headers=kopf).status_code == 404


def test_ohne_schluessel_401(client: TestClient) -> None:
    assert client.get(f"/api/app/1/detail/movie/{ERLAUBT}").status_code == 401
