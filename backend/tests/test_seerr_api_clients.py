"""Was die zugesagten Clients aufrufen - abgelesen aus ihrem Quelltext.

⚠️ **Das ist die Zusage der Seerr-Schicht, und sie gilt je Client** (Issue #17,
Entscheidung 14.11.2026). Jeder Eintrag unten stammt aus dem Quelltext des
Clients, mit Datei und Stand. Wer einen Client dazunimmt, liest dessen
Quelltext genauso aus und traegt ihn hier ein; was hier nicht steht, ist nicht
zugesagt, auch wenn es antwortet.

Gemessen am 14.11.2026:

* Silo: ``Silo-Community/silo-plugins-requests-seerr``, ``internal/seerr/*.go``
* Litefin: ``MoazSalem/litefin-plugin``, ``Controllers/SeerrController.cs``
  (Stand 08.11.2026). Litefin reicht die meisten Antworten an seine
  Fernseh-App weiter, ohne sie selbst zu lesen; gelesen werden nur die Felder
  der Nutzerzuordnung (``jellyfinUserId``, ``permissions``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Match

from app.seerr_api.app import app as seerr_app

from .conftest import create_user
from .test_seerr_api import kennung, schluessel

SILO: list[tuple[str, str]] = [
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/request"),
    ("GET", "/api/v1/request/12"),
    ("GET", "/api/v1/request"),
    ("GET", "/api/v1/movie/603"),
    ("GET", "/api/v1/tv/1399"),
    ("GET", "/api/v1/user"),
    ("POST", "/api/v1/user"),
    ("GET", "/api/v1/service/radarr"),
    ("GET", "/api/v1/service/sonarr"),
]

LITEFIN: list[tuple[str, str]] = [
    ("GET", "/api/v1/status"),
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/local"),
    ("POST", "/api/v1/auth/jellyfin"),
    ("POST", "/api/v1/auth/jellyfin/quickconnect/initiate"),
    ("GET", "/api/v1/auth/jellyfin/quickconnect/check"),
    ("POST", "/api/v1/auth/jellyfin/quickconnect/authenticate"),
    ("GET", "/api/v1/discover/trending"),
    ("GET", "/api/v1/discover/movies"),
    ("GET", "/api/v1/discover/movies/studio/2"),
    ("GET", "/api/v1/discover/movies/upcoming"),
    ("GET", "/api/v1/discover/tv"),
    ("GET", "/api/v1/discover/tv/network/49"),
    ("GET", "/api/v1/discover/tv/upcoming"),
    ("GET", "/api/v1/discover/genreslider/movie"),
    ("GET", "/api/v1/discover/genreslider/tv"),
    ("GET", "/api/v1/search"),
    ("GET", "/api/v1/collection/10"),
    ("GET", "/api/v1/person/287"),
    ("GET", "/api/v1/person/287/combined_credits"),
    ("GET", "/api/v1/movie/603"),
    ("GET", "/api/v1/movie/603/similar"),
    ("GET", "/api/v1/movie/603/recommendations"),
    ("GET", "/api/v1/movie/603/ratingscombined"),
    ("GET", "/api/v1/tv/1399"),
    ("GET", "/api/v1/tv/1399/similar"),
    ("GET", "/api/v1/tv/1399/recommendations"),
    ("GET", "/api/v1/tv/1399/ratingscombined"),
    ("GET", "/api/v1/request"),
    ("POST", "/api/v1/request"),
    ("DELETE", "/api/v1/request/12"),
    ("GET", "/api/v1/media"),
    ("GET", "/api/v1/service/radarr"),
    ("GET", "/api/v1/service/sonarr"),
    ("GET", "/api/v1/service/radarr/7"),
    ("GET", "/api/v1/user"),
    ("GET", "/api/v1/user/5/watchlist"),
    ("POST", "/api/v1/watchlist"),
    ("DELETE", "/api/v1/watchlist/603"),
]


def _hat_route(methode: str, pfad: str) -> bool:
    scope = {"type": "http", "method": methode, "path": pfad, "root_path": ""}
    return any(route.matches(scope)[0] == Match.FULL for route in seerr_app.routes)


@pytest.mark.parametrize(("methode", "pfad"), SILO + LITEFIN)
def test_jede_zugesagte_adresse_gibt_es(methode: str, pfad: str) -> None:
    assert _hat_route(methode, pfad), f"{methode} {pfad} fehlt"


def test_silo_liest_diese_felder(arr_client: TestClient) -> None:
    """Die Felder, die Silos Go-Typen (``types.go``) tatsaechlich lesen."""
    seerr = TestClient(seerr_app)
    kopf = schluessel("admin")

    assert seerr.get("/api/v1/auth/me", headers=kopf).json()["id"] > 0

    for dienst in ("radarr", "sonarr"):
        for server in seerr.get(f"/api/v1/service/{dienst}", headers=kopf).json():
            assert {"id", "name", "is4k", "isDefault"} <= set(server)
            assert isinstance(server["id"], int)

    for nutzer in seerr.get("/api/v1/user?take=100&skip=0", headers=kopf).json()["results"]:
        assert {"id", "email", "permissions"} <= set(nutzer)


def test_litefin_findet_die_person_ueber_ihre_jellyfin_kennung(admin_client: TestClient) -> None:
    """Litefins ``ResolveSeerrUserIdAsync``: GUID ohne Bindestriche, Gross/Klein egal."""
    create_user(
        admin_client,
        "mia",
        mediaserver_provider="jellyfin",
        mediaserver_account_id="jfmia0003",
    )
    seerr = TestClient(seerr_app)
    liste = seerr.get("/api/v1/user?take=100&skip=0", headers=schluessel("admin")).json()
    gesucht = "JF-MIA-0003".replace("-", "").lower()
    treffer = [
        n["id"]
        for n in liste["results"]
        if (n.get("jellyfinUserId") or "").replace("-", "").lower() == gesucht
    ]
    assert treffer == [kennung("mia")]

    # Danach fragt Litefin mit X-Api-User und liest ``permissions``.
    ich = seerr.get("/api/v1/auth/me", headers={**schluessel("admin"), "X-Api-User": str(treffer[0])})
    assert ich.json()["id"] == kennung("mia")
    assert isinstance(ich.json()["permissions"], int)
