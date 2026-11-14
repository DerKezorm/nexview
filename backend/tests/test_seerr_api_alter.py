"""Der Waechter der Altersgrenze, fuer den Seerr-Port.

``test_altersfilter_waechter.py`` geht ueber die Routentabelle der Haupt-App.
Die Seerr-Schicht ist eine eigene App (``app/seerr_api/app.py``) und steht
dort nicht drin. Hier dasselbe Verfahren fuer sie: ein Konto mit Grenze 12,
ein Katalog aus einem erlaubten und einem gesperrten Titel, und jede Adresse,
die Titel ausliefert, einmal angerufen.

Jede Probe prueft zwei Dinge (siehe dort, warum das zweite das wichtigere
ist): Der gesperrte Titel kommt **nicht** vor, der erlaubte **schon**.

⚠️ **Und einmal ueber ``X-Api-User``.** So fragt Litefin: mit dem Schluessel
eines Administrators im Namen der Person. Dann muss die Grenze der Person
gelten, nicht die des Administrators - sonst waere der Seerr-Port der Weg, auf
dem ein Kind am Fernseher alles sieht.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaServerLibraryItem, MediaType
from app.seerr_api.app import app as seerr_app
from app.services import media

from .conftest import create_user
from .test_altersfilter_waechter import ERLAUBT, GESPERRT, NEUTRAL, REIHE, _FakeTmdb, _roh, _seite
from .test_seerr_api import kennung, schluessel


class _SeerrTmdb(_FakeTmdb):
    """Dieselbe Attrappe, dazu die freie Abfrage, ueber die die Seerr-Schicht liest."""

    async def roh(self, pfad: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        teile = pfad.strip("/").split("/")
        if pfad in ("/search/multi",) or pfad.startswith("/trending/"):
            seite = _seite("movie")
            seite["results"] = [r | {"media_type": "movie"} for r in seite["results"]] + [
                {
                    "id": 1,
                    "media_type": "person",
                    "name": "Jemand",
                    "known_for": [_roh(GESPERRT, "movie") | {"media_type": "movie"}],
                }
            ]
            return seite
        if teile[0] == "discover":
            return _seite(teile[1])
        if teile[0] in ("movie", "tv") and teile[-1] in ("similar", "recommendations"):
            return _seite(teile[0])
        if teile[0] == "collection":
            return await self.collection(int(teile[1]))
        if teile[0] == "person" and teile[-1] == "combined_credits":
            return {
                "cast": [_roh(i, "movie") | {"media_type": "movie", "character": "X"} for i in (ERLAUBT, GESPERRT)],
                "crew": [],
            }
        if teile[0] == "person":
            return {"id": int(teile[1]), "name": "Jemand"}
        if teile[0] == "genre":
            return {"genres": [{"id": 28, "name": "Action"}]}
        if teile[0] in ("company", "network"):
            return {"id": int(teile[1]), "name": "Studio"}
        raise AssertionError(f"Die Attrappe kennt {pfad} nicht")


@pytest.fixture()
def kind(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    monkeypatch.setattr(media, "TmdbClient", _SeerrTmdb)
    monkeypatch.setattr(media, "_client", lambda settings, region=None: _SeerrTmdb())
    assert admin_client.put(
        "/api/settings", json={"tmdb_api_key": "test-key", "default_region": "DE"}
    ).status_code == 200
    create_user(admin_client, "zwoelf", age=12)

    # Beides liegt in der Bibliothek - "zuletzt hinzugefuegt" muss trotzdem sieben.
    with SessionLocal() as db:
        for nummer in (ERLAUBT, GESPERRT):
            db.add(
                MediaServerLibraryItem(
                    provider="jellyfin",
                    media_type=MediaType.movie,
                    guid=f"guid-{nummer}",
                    tmdb_id=nummer,
                    title=f"Titel {nummer}",
                )
            )
        db.commit()

    return {
        "eigener Schluessel": schluessel("zwoelf"),
        "X-Api-User": {**schluessel("admin"), "X-Api-User": str(kennung("zwoelf"))},
    }


#: Jede Adresse, die Titel ausliefert, mit fertigem Aufruf.
PROBEN: dict[str, str] = {
    "/api/v1/search": "/api/v1/search?query=Titel",
    "/api/v1/discover/trending": "/api/v1/discover/trending",
    "/api/v1/discover/movies": "/api/v1/discover/movies",
    "/api/v1/discover/tv": "/api/v1/discover/tv",
    "/api/v1/discover/movies/upcoming": "/api/v1/discover/movies/upcoming",
    "/api/v1/discover/tv/upcoming": "/api/v1/discover/tv/upcoming",
    "/api/v1/discover/movies/genre/{genre_id}": "/api/v1/discover/movies/genre/28",
    "/api/v1/discover/tv/genre/{genre_id}": "/api/v1/discover/tv/genre/28",
    "/api/v1/discover/movies/language/{sprache}": "/api/v1/discover/movies/language/de",
    "/api/v1/discover/tv/language/{sprache}": "/api/v1/discover/tv/language/de",
    "/api/v1/discover/movies/studio/{studio_id}": "/api/v1/discover/movies/studio/1",
    "/api/v1/discover/tv/network/{network_id}": "/api/v1/discover/tv/network/1",
    "/api/v1/discover/keyword/{keyword_id}/movies": "/api/v1/discover/keyword/1/movies",
    "/api/v1/movie/{tmdb_id}/similar": f"/api/v1/movie/{NEUTRAL}/similar",
    "/api/v1/movie/{tmdb_id}/recommendations": f"/api/v1/movie/{NEUTRAL}/recommendations",
    "/api/v1/tv/{tmdb_id}/similar": f"/api/v1/tv/{NEUTRAL}/similar",
    "/api/v1/tv/{tmdb_id}/recommendations": f"/api/v1/tv/{NEUTRAL}/recommendations",
    "/api/v1/collection/{sammlung_id}": f"/api/v1/collection/{REIHE}",
    "/api/v1/person/{person_id}/combined_credits": "/api/v1/person/1/combined_credits",
    "/api/v1/media": "/api/v1/media?filter=allavailable&sort=mediaAdded&take=20",
}

#: Adressen ohne Titelliste - mit Begruendung, warum sie hier nicht stehen.
OHNE_PROBE: dict[str, str] = {
    "/api/v1/movie/{tmdb_id}": "Detail - eigene Probe unten (404 statt Liste)",
    "/api/v1/tv/{tmdb_id}": "Detail - eigene Probe unten (404 statt Liste)",
    "/api/v1/discover/genreslider/movie": "nur Bildpfade - eigene Probe unten",
    "/api/v1/discover/genreslider/tv": "nur Bildpfade - eigene Probe unten",
    "/api/v1/user/{user_id}/watchlist": "geht durch favorites.my_favorites, dort bewacht",
    "/api/v1/request": "eigene Anfragen - die hat das Konto selbst gestellt",
    "/api/v1/request/{anfrage_id}": "eigene Anfragen - die hat das Konto selbst gestellt",
}


def _get_pfade() -> set[str]:
    return {
        route.path
        for route in seerr_app.routes
        if isinstance(route, APIRoute) and "GET" in route.methods
    }


def test_jede_titeladresse_ist_entschieden() -> None:
    """Wer eine Seerr-Adresse hinzufuegt, die Titel liefert, muss sie hier eintragen."""
    ohne_titel = {
        "/api/v1/status", "/api/v1/auth/me", "/api/v1/auth/jellyfin/quickconnect/check",
        "/api/v1/service/radarr", "/api/v1/service/sonarr", "/api/v1/service/{dienst}/{server}",
        "/api/v1/request/count", "/api/v1/movie/{tmdb_id}/ratingscombined", "/api/v1/movie/{tmdb_id}/ratings",
        "/api/v1/tv/{tmdb_id}/ratingscombined", "/api/v1/tv/{tmdb_id}/ratings", "/api/v1/genres/movie",
        "/api/v1/genres/tv", "/api/v1/person/{person_id}", "/api/v1/user", "/api/v1/user/{user_id}",
    }
    offen = _get_pfade() - set(PROBEN) - set(OHNE_PROBE) - ohne_titel
    assert not offen, f"Nicht entschieden: {sorted(offen)}"


@pytest.mark.parametrize("weg", ["eigener Schluessel", "X-Api-User"])
@pytest.mark.parametrize("pfad", sorted(PROBEN))
def test_gesperrter_titel_kommt_nicht_heraus(kind: dict[str, dict[str, str]], weg: str, pfad: str) -> None:
    antwort = TestClient(seerr_app).get(PROBEN[pfad], headers=kind[weg])
    assert antwort.status_code == 200, antwort.text
    text = json.dumps(antwort.json())
    assert str(GESPERRT) not in text, f"{pfad} liefert den gesperrten Titel"
    assert str(ERLAUBT) in text, f"{pfad} liefert nicht einmal den erlaubten Titel - die Probe misst nichts"


@pytest.mark.parametrize("weg", ["eigener Schluessel", "X-Api-User"])
@pytest.mark.parametrize("art", ["movie", "tv"])
def test_gesperrtes_detail_ist_nicht_vorhanden(kind: dict[str, dict[str, str]], weg: str, art: str) -> None:
    seerr = TestClient(seerr_app)
    assert seerr.get(f"/api/v1/{art}/{GESPERRT}", headers=kind[weg]).status_code == 404
    erlaubt = seerr.get(f"/api/v1/{art}/{ERLAUBT}", headers=kind[weg])
    assert erlaubt.status_code == 200, erlaubt.text
    assert erlaubt.json()["id"] == ERLAUBT
    # Was einem gesperrten Titel aehnelt, sieht auch niemand.
    assert seerr.get(f"/api/v1/{art}/{GESPERRT}/similar", headers=kind[weg]).status_code == 404


def test_genreleiste_zeigt_keine_bilder_gesperrter_titel(kind: dict[str, dict[str, str]]) -> None:
    """Die Attrappe gibt beiden Titeln dasselbe Bild - gezaehlt wird deshalb die Menge."""
    seerr = TestClient(seerr_app)
    leiste = seerr.get("/api/v1/discover/genreslider/movie", headers=kind["eigener Schluessel"]).json()
    assert leiste[0]["name"] == "Action"
    assert len(leiste[0]["backdrops"]) == 1


def test_der_administrator_selbst_sieht_beides(kind: dict[str, dict[str, str]]) -> None:
    """Gegenprobe: Ohne Grenze kommen beide - sonst siebte die Probe einfach alles weg."""
    seerr = TestClient(seerr_app)
    text = json.dumps(seerr.get("/api/v1/discover/movies", headers=schluessel("admin")).json())
    assert str(GESPERRT) in text
    assert str(ERLAUBT) in text
