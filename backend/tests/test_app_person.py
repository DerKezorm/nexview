"""Personen der Fernseh-App: ``/api/app/1/person/{id}`` und ``/api/app/1/people``.

nexcinema zeigt auf der Seite eines Schauspielers eine Reihe "Weitere Filme,
die euch fehlen": die Filmografie aus nexview, jeder Titel mit seinem Zustand.
Vertrag in ``docs/app-person.md``.

Bewacht wird, was die Zweitschrift nicht verlieren darf: Altersgrenze,
Kinderschutz, der Zustand je Titel wie auf der Webseite, die Sprache samt
englischem Rueckfall der Biografie, und dass ein App-Schluessel reicht.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus
from app.services import media
from app.services.fassungen import arr_kennung

from .conftest import auth_headers, create_user
from .test_altersfilter_waechter import ERLAUBT, GESPERRT, _kennungen, beschraenkt  # noqa: F401
from .test_app_entdecken import _mediathek
from .test_app_kopplung import _abholen, _bearer, _code
from .test_child_permissions import _kind

PERSON = 4242

#: Die Biografie je Sprache. Auf Deutsch hat TMDB keine - wie bei vielen Leuten.
BIOGRAFIEN = {"en-US": "Born in a small town.", "fr": "Né dans une petite ville.", "de": ""}


def _film(tmdb_id: int, beliebt: float) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "media_type": "movie",
        "title": f"Film {tmdb_id}",
        "character": "Jemand",
        "poster_path": "/p.jpg",
        "release_date": "2019-05-01",
        "vote_average": 7.1,
        "popularity": beliebt,
    }


class _Tmdb:
    """Eine Person mit drei Filmen und einer Serie; merkt sich jede Sprache."""

    gefragt: list[str]

    def __init__(self, sprache: str, gefragt: list[str]) -> None:
        self.sprache = sprache
        self.gefragt = gefragt

    async def person(self, person_id: int) -> dict[str, Any]:
        self.gefragt.append(self.sprache)
        return {
            "id": person_id,
            "name": "Jane Doe",
            "biography": BIOGRAFIEN.get(self.sprache, ""),
            "profile_path": "/jane.jpg",
            "known_for_department": "Acting",
            "combined_credits": {
                "cast": [
                    _film(1, 90),
                    _film(2, 80),
                    _film(3, 70),
                    {"id": 10, "media_type": "tv", "name": "Serie 10", "character": "Ärztin",
                     "first_air_date": "2021-01-01", "popularity": 60},
                ],
                "crew": [],
            },
        }

    async def search_person(self, query: str, page: int = 1) -> dict[str, Any]:
        return {
            "page": 1,
            "total_pages": 1,
            "results": [
                {"id": PERSON, "name": "Jane Doe", "known_for_department": "Acting",
                 "profile_path": "/jane.jpg", "known_for": [{"title": "Film 1"}]},
                {"id": 99, "name": "Jane Roe", "known_for_department": "Directing"},
            ],
        }


@pytest.fixture()
def tmdb(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """TMDB ersetzt; die Liste sagt, in welchen Sprachen die Person geholt wurde."""
    assert admin_client.put(
        "/api/settings", json={"tmdb_api_key": "test-key", "default_region": "DE"}
    ).status_code == 200
    gefragt: list[str] = []
    monkeypatch.setattr(
        media, "_client", lambda settings, region=None: _Tmdb(settings.default_language, gefragt)
    )
    return gefragt


def _person(client: TestClient, pfad: str = f"/api/app/1/person/{PERSON}", **kopf: Any) -> dict:
    antwort = client.get(pfad, **kopf)
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


# --- Was die App bekommt ------------------------------------------------------


def test_die_person_mit_bild_und_filmografie(admin_client: TestClient, tmdb) -> None:
    daten = _person(admin_client, f"/api/app/1/person/{PERSON}?language=en-US")
    assert daten["person_id"] == PERSON
    assert daten["name"] == "Jane Doe"
    assert daten["photo_url"].endswith("/jane.jpg")
    assert daten["biography"] == "Born in a small town."
    film = next(c for c in daten["credits"] if c["tmdb_id"] == 1)
    assert film["media_type"] == "movie"
    assert film["title"] == "Film 1"
    assert film["release_date"] == "2019-05-01"
    assert film["poster_url"].endswith("/p.jpg")
    serie = next(c for c in daten["credits"] if c["tmdb_id"] == 10)
    assert serie["media_type"] == "tv"
    assert serie["release_date"] == "2021-01-01"


def test_dieselbe_antwort_wie_auf_der_webseite(admin_client: TestClient, tmdb) -> None:
    """Ohne ``language`` ist es die Personenseite des Browsers - bis auf den Rueckfall."""
    web = _person(admin_client, f"/api/person/{PERSON}")
    app = _person(admin_client)
    assert app["credits"] == web["credits"]
    assert {k: v for k, v in app.items() if k != "biography"} == {
        k: v for k, v in web.items() if k != "biography"
    }


def test_der_zustand_je_titel_ist_der_der_webseite(
    arr_client: TestClient, tmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liegt, nur im Medienserver, angefragt, fehlt - dieselbe Rechnung wie im Browser."""
    _mediathek(monkeypatch, {1: "downloaded"}, im_medienserver={2})
    admin = next(u["id"] for u in arr_client.get("/api/users").json() if u["role"] == "admin")
    with SessionLocal() as sitzung:
        sitzung.add(
            MediaRequest(
                user_id=admin,
                media_type=MediaType.movie,
                fassung_kennung=arr_kennung("movie", "standard"),
                tmdb_id=3,
                title="Film 3",
                status=RequestStatus.pending_approval,
            )
        )
        sitzung.commit()

    zustand = {c["tmdb_id"]: c["status"] for c in _person(arr_client)["credits"]}
    assert zustand == {1: "downloaded", 2: "in_library", 3: "pending_approval", 10: "not_requested"}
    web = {c["tmdb_id"]: c["status"] for c in _person(arr_client, f"/api/person/{PERSON}")["credits"]}
    assert zustand == web


# --- Sprache --------------------------------------------------------------------


def test_die_gewuenschte_sprache_geht_an_tmdb(admin_client: TestClient, tmdb: list[str]) -> None:
    daten = _person(admin_client, f"/api/app/1/person/{PERSON}?language=fr")
    assert tmdb == ["fr"]
    assert daten["biography"] == "Né dans une petite ville."


def test_ohne_biografie_in_der_sprache_kommt_die_englische(
    admin_client: TestClient, tmdb: list[str]
) -> None:
    daten = _person(admin_client, f"/api/app/1/person/{PERSON}?language=de")
    assert daten["biography"] == "Born in a small town."
    assert tmdb == ["de", "en-US"]
    # Die Filmografie bleibt die der gewuenschten Sprache - nur der Text faellt zurueck.
    assert {c["tmdb_id"] for c in daten["credits"]} == {1, 2, 3, 10}


def test_die_webseite_bleibt_ohne_rueckfall(admin_client: TestClient, tmdb: list[str]) -> None:
    """Der Rueckfall ist eine Sache der App; die Personenseite des Browsers bleibt, wie sie war."""
    assert admin_client.put("/api/settings", json={"default_language": "de"}).status_code == 200
    assert _person(admin_client, f"/api/person/{PERSON}")["biography"] == ""
    assert tmdb == ["de"]


def test_ohne_language_gilt_die_sprache_des_kontos(admin_client: TestClient, tmdb: list[str]) -> None:
    create_user(admin_client, "claire", "passwort-1234")
    kopf = auth_headers(admin_client, "claire", "passwort-1234")
    assert admin_client.patch("/api/auth/me", json={"language": "en"}, headers=kopf).status_code == 200
    _person(admin_client, headers=kopf)
    assert tmdb[0].startswith("en")


@pytest.mark.parametrize("sprache", ["deutsch", "de_DE", "DE", "de-de", "x"])
def test_eine_unsinnige_sprache_ist_ein_fehler(admin_client: TestClient, tmdb, sprache: str) -> None:
    antwort = admin_client.get(f"/api/app/1/person/{PERSON}?language={sprache}")
    assert antwort.status_code == 422


# --- Wer es sehen darf ----------------------------------------------------------


def test_ohne_schluessel_401(client: TestClient) -> None:
    assert client.get(f"/api/app/1/person/{PERSON}").status_code == 401
    assert client.get("/api/app/1/people?q=Jane").status_code == 401


def test_ein_app_schluessel_reicht(admin_client: TestClient, tmdb) -> None:
    create_user(admin_client, "jonas", "passwort-1234")
    kopf = auth_headers(admin_client, "jonas", "passwort-1234")
    schluessel = _abholen(admin_client, _code(admin_client, kopf)).json()["key"]
    assert _person(admin_client, headers=_bearer(schluessel))["name"] == "Jane Doe"
    treffer = admin_client.get("/api/app/1/people?q=Jane", headers=_bearer(schluessel))
    assert treffer.status_code == 200, treffer.text


def test_ein_kind_bekommt_403(admin_client: TestClient, tmdb) -> None:
    """Wie auf der Webseite: Personenseiten gibt es fuer Kinderkonten nicht."""
    kind = _kind(admin_client)
    assert admin_client.get(f"/api/app/1/person/{PERSON}", headers=kind).status_code == 403
    assert admin_client.get("/api/app/1/people?q=Jane", headers=kind).status_code == 403
    assert admin_client.get(f"/api/person/{PERSON}", headers=kind).status_code == 403


def test_die_altersgrenze_siebt_die_filmografie(
    beschraenkt: tuple[TestClient, dict[str, str]],  # noqa: F811
) -> None:
    """Ein Konto mit Grenze 12 sieht den FSK-18-Titel nicht - auch nicht mit fremder Sprache.

    Beide Zusicherungen, wie im Waechter: Der erlaubte Titel muss da sein,
    sonst haette die Probe nichts gemessen.
    """
    client, kopf = beschraenkt
    for pfad in (f"/api/app/1/person/{PERSON}", f"/api/app/1/person/{PERSON}?language=en-US"):
        antwort = client.get(pfad, headers=kopf)
        assert antwort.status_code == 200, antwort.text
        kennungen = _kennungen(antwort.json())
        assert GESPERRT not in kennungen, pfad
        assert ERLAUBT in kennungen, pfad
    # Gegenprobe: Ohne Grenze sieht der Administrator beide.
    alle = _kennungen(client.get(f"/api/app/1/person/{PERSON}").json())
    assert {ERLAUBT, GESPERRT} <= alle


# --- Suche nach Namen ------------------------------------------------------------


def test_die_namenssuche_wie_auf_der_webseite(admin_client: TestClient, tmdb) -> None:
    app = admin_client.get("/api/app/1/people?q=Jane")
    web = admin_client.get("/api/people?q=Jane")
    assert app.status_code == 200, app.text
    assert app.json() == web.json()
    # Ab Werk nur Schauspiel: die Regisseurin faellt heraus.
    assert [p["person_id"] for p in app.json()["items"]] == [PERSON]
