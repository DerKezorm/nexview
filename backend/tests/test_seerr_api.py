"""Die Seerr-kompatible Schnittstelle (Issue #17): Zugang, Anfragen, Nutzer, Merkliste.

Was ein bestimmter Client aufruft, steht in ``test_seerr_api_clients.py``;
die Altersgrenze an jeder Titeladresse in ``test_seerr_api_alter.py``. Hier
steht, was fuer alle Clients gilt.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus, Role, User
from app.seerr_api import form
from app.seerr_api.app import app as seerr_app
from app.services import api_schluessel, requests_service

from .conftest import create_user


def schluessel(benutzername: str, **optionen: object) -> dict[str, str]:
    """Einen Nexview-Schluessel fuer dieses Konto, als Seerr-Kopfzeile."""
    with SessionLocal() as db:
        konto = db.scalar(select(User).where(User.username == benutzername))
        assert konto is not None
        _, klartext = api_schluessel.anlegen(db, konto, name="Seerr", **optionen)  # type: ignore[arg-type]
    return {"X-Api-Key": klartext}


def kennung(benutzername: str) -> int:
    with SessionLocal() as db:
        konto = db.scalar(select(User).where(User.username == benutzername))
        assert konto is not None
        return konto.id


@pytest.fixture
def seerr() -> TestClient:
    return TestClient(seerr_app)


@pytest.fixture
def uebergabe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eine freigegebene Anfrage geht durch, statt am Port 9 zu scheitern."""

    async def push(db, settings, request):  # noqa: ANN001
        request.status = RequestStatus.searching
        db.commit()
        return request

    monkeypatch.setattr(requests_service, "push_to_arr", push)


def _erster_demo_titel(client: TestClient, art: str = "movie") -> dict:
    return client.get(f"/api/discover/{art}").json()["items"][0]


# --- Zugang -----------------------------------------------------------------


def test_status_antwortet_ohne_schluessel(seerr: TestClient) -> None:
    antwort = seerr.get("/api/v1/status")
    assert antwort.status_code == 200
    assert "version" in antwort.json()


def test_ohne_oder_mit_falschem_schluessel_401_in_seerrs_form(admin_client: TestClient, seerr: TestClient) -> None:
    ohne = seerr.get("/api/v1/auth/me")
    assert ohne.status_code == 401
    assert "message" in ohne.json()
    assert seerr.get("/api/v1/auth/me", headers={"X-Api-Key": "nxv_falsch"}).status_code == 401
    # Ein Sitzungs-Token ist kein Schluessel.
    token = admin_client.headers["Authorization"].split(" ", 1)[1]
    assert seerr.get("/api/v1/auth/me", headers={"X-Api-Key": token}).status_code == 401


def test_auth_me_nennt_den_besitzer_des_schluessels(admin_client: TestClient, seerr: TestClient) -> None:
    create_user(admin_client, "silo")
    antwort = seerr.get("/api/v1/auth/me", headers=schluessel("silo"))
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["id"] == kennung("silo")
    assert daten["permissions"] & form.RECHT_ANFRAGEN
    assert not daten["permissions"] & form.RECHT_ADMIN

    admin = seerr.get("/api/v1/auth/me", headers=schluessel("admin")).json()
    assert admin["permissions"] == form.RECHT_ADMIN


def test_kinderkonto_und_lesender_schluessel(admin_client: TestClient, seerr: TestClient) -> None:
    create_user(admin_client, "kind", role=Role.child, parent_id=kennung("admin"))
    kind = schluessel("kind", kindergeraet=True)
    assert seerr.get("/api/v1/auth/me", headers=kind).status_code == 403

    create_user(admin_client, "leser")
    lesend = schluessel("leser", nur_lesen=True)
    assert seerr.get("/api/v1/auth/me", headers=lesend).status_code == 200
    assert seerr.post("/api/v1/request", headers=lesend, json={"mediaType": "movie", "mediaId": 1}).status_code == 403


def test_x_api_user_nur_mit_dem_schluessel_eines_administrators(admin_client: TestClient, seerr: TestClient) -> None:
    create_user(admin_client, "anna")
    create_user(admin_client, "ben")
    anna = kennung("anna")

    # Ein gewoehnlicher Schluessel darf nicht fuer andere fragen.
    fremd = seerr.get("/api/v1/auth/me", headers={**schluessel("ben"), "X-Api-User": str(anna)})
    assert fremd.status_code == 403
    # Fuer sich selbst schon.
    selbst = seerr.get("/api/v1/auth/me", headers={**schluessel("ben"), "X-Api-User": str(kennung("ben"))})
    assert selbst.status_code == 200

    # Der eines Administrators fragt im Namen der Person.
    im_auftrag = seerr.get("/api/v1/auth/me", headers={**schluessel("admin"), "X-Api-User": str(anna)})
    assert im_auftrag.status_code == 200
    assert im_auftrag.json()["id"] == anna

    # Unbekannte, stillgelegte und Kinderkonten sehen gleich aus.
    create_user(admin_client, "still", is_active=False)
    create_user(admin_client, "klein", role=Role.child, parent_id=kennung("admin"))
    for ziel in (999_999, kennung("still"), kennung("klein")):
        antwort = seerr.get("/api/v1/auth/me", headers={**schluessel("admin"), "X-Api-User": str(ziel)})
        assert antwort.status_code == 403, ziel
        assert antwort.json() == {"message": "Unknown user in X-Api-User."}


def test_anmelden_mit_passwort_gibt_es_nicht(seerr: TestClient) -> None:
    for pfad in ("/api/v1/auth/local", "/api/v1/auth/jellyfin", "/api/v1/auth/jellyfin/quickconnect/initiate"):
        antwort = seerr.post(pfad, json={"email": "a", "password": "b"})
        assert antwort.status_code == 403, pfad
        assert "API key" in antwort.json()["message"]


def test_unbekannte_adresse_404_in_seerrs_form(seerr: TestClient) -> None:
    antwort = seerr.get("/api/v1/issue")
    assert antwort.status_code == 404
    assert "message" in antwort.json()
    # Und Nexviews eigene Adressen gibt es auf diesem Port nicht.
    assert seerr.get("/api/settings").status_code == 404
    assert seerr.get("/").status_code == 404


# --- Nutzer -------------------------------------------------------------------


def test_nutzerliste_ordnet_ueber_jellyfin_zu_nie_ueber_mail_oder_namen(
    admin_client: TestClient, seerr: TestClient
) -> None:
    create_user(
        admin_client,
        "carla",
        mediaserver_provider="jellyfin",
        mediaserver_account_id="jf-carla-0001",
        mediaserver_username="carla-jf",
    )
    create_user(admin_client, "kind", role=Role.child, parent_id=kennung("admin"))

    antwort = seerr.get("/api/v1/user?take=100", headers=schluessel("admin"))
    assert antwort.status_code == 200
    daten = antwort.json()
    nach_id = {n["id"]: n for n in daten["results"]}
    carla = nach_id[kennung("carla")]
    assert carla["jellyfinUserId"] == "jf-carla-0001"
    # Der Name nicht: Litefin faellt sonst darauf zurueck (Pruefung 14.11.2026).
    assert carla["jellyfinUsername"] is None
    assert carla["userType"] == form.TYP_JELLYFIN
    # ⚠️ Der Kern: nichts, worueber ein Client per Mail oder Name zuordnen koennte.
    for eintrag in daten["results"]:
        assert eintrag["email"] == ""
        assert eintrag["username"] is None
    assert kennung("kind") not in nach_id
    assert daten["pageInfo"]["results"] == len(daten["results"])


def test_nutzerliste_nur_fuer_administratoren_und_anlegen_nie(admin_client: TestClient, seerr: TestClient) -> None:
    create_user(admin_client, "dora", role=Role.approver)
    assert seerr.get("/api/v1/user", headers=schluessel("dora")).status_code == 403
    angelegt = seerr.post("/api/v1/user", headers=schluessel("admin"), json={"email": "neu@example.com", "permissions": 32})
    assert angelegt.status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "neu@example.com")) is None


# --- Anfragen -----------------------------------------------------------------


def test_anfrage_anlegen_lesen_auflisten_wie_silo(arr_client: TestClient, seerr: TestClient) -> None:
    create_user(arr_client, "silo")
    kopf = schluessel("silo")
    titel = _erster_demo_titel(arr_client)

    angelegt = seerr.post(
        "/api/v1/request", headers=kopf, json={"mediaType": "movie", "mediaId": titel["tmdb_id"], "is4k": False}
    )
    assert angelegt.status_code == 201, angelegt.text
    anfrage = angelegt.json()
    assert anfrage["status"] == form.ANFRAGE_WARTET
    assert anfrage["is4k"] is False
    assert anfrage["media"]["tmdbId"] == titel["tmdb_id"]
    assert anfrage["media"]["mediaType"] == "movie"
    assert anfrage["media"]["status"] == form.MEDIUM_WARTET
    assert anfrage["requestedBy"]["id"] == kennung("silo")

    gelesen = seerr.get(f"/api/v1/request/{anfrage['id']}", headers=kopf)
    assert gelesen.status_code == 200
    assert gelesen.json()["id"] == anfrage["id"]

    # Silos Rueckfall, wenn es eine vorhandene Anfrage sucht.
    liste = seerr.get("/api/v1/request?take=100&sort=added", headers=kopf).json()
    assert [a["id"] for a in liste["results"]] == [anfrage["id"]]
    assert liste["results"][0]["media"]["tmdbId"] == titel["tmdb_id"]

    # Ein zweites Mal ist bei Seerr 409, und Silo sucht dann die vorhandene.
    doppelt = seerr.post("/api/v1/request", headers=kopf, json={"mediaType": "movie", "mediaId": titel["tmdb_id"]})
    assert doppelt.status_code == 409
    assert "message" in doppelt.json()


@pytest.mark.usefixtures("uebergabe")
def test_anfrage_eines_administrators_ist_gleich_freigegeben(arr_client: TestClient, seerr: TestClient) -> None:
    titel = _erster_demo_titel(arr_client)
    antwort = seerr.post(
        "/api/v1/request", headers=schluessel("admin"), json={"mediaType": "movie", "mediaId": titel["tmdb_id"]}
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["status"] == form.ANFRAGE_FREIGEGEBEN
    assert antwort.json()["media"]["status"] == form.MEDIUM_IN_ARBEIT


def test_user_id_im_koerper_nur_mit_administrator_schluessel(arr_client: TestClient, seerr: TestClient) -> None:
    create_user(arr_client, "eva")
    create_user(arr_client, "finn")
    titel = _erster_demo_titel(arr_client)

    verboten = seerr.post(
        "/api/v1/request",
        headers=schluessel("finn"),
        json={"mediaType": "movie", "mediaId": titel["tmdb_id"], "userId": kennung("eva")},
    )
    assert verboten.status_code == 403

    erlaubt = seerr.post(
        "/api/v1/request",
        headers=schluessel("admin"),
        json={"mediaType": "movie", "mediaId": titel["tmdb_id"], "userId": kennung("eva")},
    )
    assert erlaubt.status_code == 201, erlaubt.text
    assert erlaubt.json()["requestedBy"]["id"] == kennung("eva")
    # Und es gelten Evas Regeln, nicht die des Administrators: Sie wartet.
    assert erlaubt.json()["status"] == form.ANFRAGE_WARTET


def test_fremde_anfragen_sieht_nur_wer_freigeben_darf(arr_client: TestClient, seerr: TestClient) -> None:
    create_user(arr_client, "gina")
    create_user(arr_client, "hugo")
    titel = _erster_demo_titel(arr_client)
    anfrage = seerr.post(
        "/api/v1/request", headers=schluessel("gina"), json={"mediaType": "movie", "mediaId": titel["tmdb_id"]}
    ).json()

    hugo = schluessel("hugo")
    assert seerr.get(f"/api/v1/request/{anfrage['id']}", headers=hugo).status_code == 404
    assert seerr.get("/api/v1/request?take=100", headers=hugo).json()["results"] == []
    assert seerr.get(f"/api/v1/request?take=100&requestedBy={kennung('gina')}", headers=hugo).json()["results"] == []
    assert seerr.delete(f"/api/v1/request/{anfrage['id']}", headers=hugo).status_code == 404

    admin = schluessel("admin")
    assert seerr.get(f"/api/v1/request/{anfrage['id']}", headers=admin).status_code == 200
    gefiltert = seerr.get(f"/api/v1/request?take=100&requestedBy={kennung('gina')}&filter=pending", headers=admin)
    assert [a["id"] for a in gefiltert.json()["results"]] == [anfrage["id"]]


def test_eigene_offene_anfrage_zuruecknehmen(arr_client: TestClient, seerr: TestClient) -> None:
    create_user(arr_client, "ida")
    kopf = schluessel("ida")
    titel = _erster_demo_titel(arr_client)
    anfrage = seerr.post("/api/v1/request", headers=kopf, json={"mediaType": "movie", "mediaId": titel["tmdb_id"]})
    nummer = anfrage.json()["id"]

    assert seerr.delete(f"/api/v1/request/{nummer}", headers=kopf).status_code == 204
    assert seerr.get(f"/api/v1/request/{nummer}", headers=kopf).status_code == 404


def test_serie_mit_staffeln_wird_eine_seerr_anfrage(arr_client: TestClient, seerr: TestClient) -> None:
    create_user(arr_client, "jan")
    kopf = schluessel("jan")
    serie = _erster_demo_titel(arr_client, "tv")

    antwort = seerr.post(
        "/api/v1/request", headers=kopf, json={"mediaType": "tv", "mediaId": serie["tmdb_id"], "seasons": [1, 2]}
    )
    assert antwort.status_code == 201, antwort.text
    daten = antwort.json()
    assert daten["type"] == "tv"
    assert sorted(s["seasonNumber"] for s in daten["seasons"]) == [1, 2]
    with SessionLocal() as db:
        zeilen = list(db.scalars(select(MediaRequest).where(MediaRequest.tmdb_id == serie["tmdb_id"])))
    assert sorted(z.season for z in zeilen) == [1, 2]


@pytest.mark.usefixtures("uebergabe")
def test_server_sind_die_fassungen_und_server_id_waehlt_sie(arr_client: TestClient, seerr: TestClient) -> None:
    kopf = schluessel("admin")
    server = seerr.get("/api/v1/service/radarr", headers=kopf).json()
    assert server, "Die Standard-Instanz fehlt"
    standard = next(s for s in server if not s["is4k"])
    assert standard["isDefault"] is True
    # Stabil: dieselbe Kennung ergibt immer dieselbe Nummer.
    assert seerr.get("/api/v1/service/radarr", headers=kopf).json() == server

    einzeln = seerr.get(f"/api/v1/service/radarr/{standard['id']}", headers=kopf)
    assert einzeln.status_code == 200
    assert einzeln.json()["server"]["id"] == standard["id"]
    assert seerr.get("/api/v1/service/radarr/1", headers=kopf).status_code == 404

    titel = _erster_demo_titel(arr_client)
    antwort = seerr.post(
        "/api/v1/request",
        headers=kopf,
        json={"mediaType": "movie", "mediaId": titel["tmdb_id"], "serverId": standard["id"]},
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["serverId"] == standard["id"]

    unbekannt = seerr.post(
        "/api/v1/request", headers=kopf, json={"mediaType": "movie", "mediaId": titel["tmdb_id"], "serverId": 7}
    )
    assert unbekannt.status_code == 400


def test_kaputte_anfragen_sind_400_und_kein_absturz(arr_client: TestClient, seerr: TestClient) -> None:
    kopf = schluessel("admin")
    for koerper in (
        {"mediaType": "music", "mediaId": 1},
        {"mediaType": "movie", "mediaId": "abc"},
        {"mediaType": "movie", "mediaId": 1, "serverId": "x"},
        {"mediaType": "movie", "mediaId": 1, "userId": "x"},
    ):
        assert seerr.post("/api/v1/request", headers=kopf, json=koerper).status_code == 400, koerper


# --- Merkliste ----------------------------------------------------------------


def test_merkliste_ist_mag_ich(admin_client: TestClient, seerr: TestClient) -> None:
    create_user(admin_client, "kai")
    kopf = schluessel("kai")
    ich = kennung("kai")

    gemerkt = seerr.post("/api/v1/watchlist", headers=kopf, json={"tmdbId": 603, "mediaType": "movie", "title": "M"})
    assert gemerkt.status_code == 201
    assert gemerkt.json()["tmdbId"] == 603

    liste = seerr.get(f"/api/v1/user/{ich}/watchlist", headers=kopf).json()
    assert [(e["tmdbId"], e["mediaType"]) for e in liste["results"]] == [(603, "movie")]
    assert liste["totalResults"] == 1

    # Fremde Listen nur fuer Administratoren.
    create_user(admin_client, "lea")
    assert seerr.get(f"/api/v1/user/{ich}/watchlist", headers=schluessel("lea")).status_code == 403
    assert seerr.get(f"/api/v1/user/{ich}/watchlist", headers=schluessel("admin")).status_code == 200

    assert seerr.delete("/api/v1/watchlist/603?mediaType=movie", headers=kopf).status_code == 204
    assert seerr.get(f"/api/v1/user/{ich}/watchlist", headers=kopf).json()["results"] == []
    assert seerr.delete("/api/v1/watchlist/603?mediaType=movie", headers=kopf).status_code == 404
    assert seerr.delete("/api/v1/watchlist/603", headers=kopf).status_code == 400


# --- Formen -------------------------------------------------------------------


def test_kamel_benennt_wie_seerr() -> None:
    roh = {
        "poster_path": "/p.jpg",
        "release_dates": {"results": [{"iso_3166_1": "DE", "release_dates": []}]},
        "known_for_department": "Acting",
    }
    assert form.kamel(roh) == {
        "posterPath": "/p.jpg",
        "releaseDates": {"results": [{"iso_3166_1": "DE", "releaseDates": []}]},
        "knownForDepartment": "Acting",
    }


def test_server_id_ist_stabil_und_positiv() -> None:
    assert form.server_id("radarr-standard") == form.server_id("radarr-standard")
    assert form.server_id("radarr-standard") != form.server_id("radarr-uhd")
    assert 0 < form.server_id("radarr-uhd") < 2**31  # type: ignore[operator]
    assert form.server_id(None) is None


@pytest.mark.parametrize(
    ("abzeichen", "zahl"),
    [
        (None, form.MEDIUM_UNBEKANNT),
        ("not_requested", form.MEDIUM_UNBEKANNT),
        ("pending_approval", form.MEDIUM_WARTET),
        ("requested", form.MEDIUM_IN_ARBEIT),
        ("searching", form.MEDIUM_IN_ARBEIT),
        ("partial", form.MEDIUM_TEILWEISE),
        ("downloaded", form.MEDIUM_DA),
        ("in_library", form.MEDIUM_DA),
        ("blocked", form.MEDIUM_GESPERRT),
    ],
)
def test_abzeichen_als_seerrs_medienstatus(abzeichen: str | None, zahl: int) -> None:
    assert form.medienstatus(abzeichen) == zahl
