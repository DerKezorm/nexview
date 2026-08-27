"""Abbrechen laufender Anfragen und Sammelfreigabe."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus, User
from app.services import library
from app.services.arr import ArrError
from app.services.radarr import RadarrClient
from app.services.sonarr import SonarrClient

from .conftest import auth_headers, create_user


@pytest.fixture
def geloescht_in_radarr(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, bool]]:
    """Merkt sich, was in Radarr gelöscht worden wäre - ohne echten Aufruf."""
    aufrufe: list[tuple[int, bool]] = []

    async def remove(_self: RadarrClient, arr_id: int, delete_files: bool = True) -> None:
        aufrufe.append((arr_id, delete_files))

    monkeypatch.setattr(RadarrClient, "remove", remove)
    return aufrufe


def _laufende_anfrage(client: TestClient, benutzer: str = "kim") -> dict:
    create_user(client, benutzer)
    headers = auth_headers(client, benutzer, "passwort-1234")
    item = client.get("/api/discover/movie").json()["items"][0]
    angelegt = client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    ).json()

    with SessionLocal() as session:
        request = session.query(MediaRequest).filter(MediaRequest.id == angelegt["id"]).one()
        request.status = RequestStatus.searching
        request.arr_id = 4711
        session.commit()

    return {"id": angelegt["id"], "headers": headers}


def test_benutzer_bricht_eigene_anfrage_ab(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    anfrage = _laufende_anfrage(arr_client)

    antwort = arr_client.post(
        f"/api/requests/{anfrage['id']}/cancel", headers=anfrage["headers"]
    )
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "cancelled"

    # In Radarr wurde gelöscht - samt Dateien, wie vereinbart.
    assert geloescht_in_radarr == [(4711, True)]


def test_abbrechen_gibt_das_kontingent_zurueck(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    created = create_user(arr_client, "kim")
    arr_client.patch(f"/api/users/{created['id']}", json={"quota_movies_limit": 1})
    anfrage = _laufende_anfrage(arr_client)

    stand = arr_client.get("/api/requests/quota", headers=anfrage["headers"]).json()
    assert stand["movie"]["used"] == 1

    arr_client.post(f"/api/requests/{anfrage['id']}/cancel", headers=anfrage["headers"])

    danach = arr_client.get("/api/requests/quota", headers=anfrage["headers"]).json()
    assert danach["movie"]["used"] == 0
    assert danach["movie"]["exhausted"] is False


def test_abgebrochener_titel_kann_neu_angefragt_werden(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    anfrage = _laufende_anfrage(arr_client)
    arr_client.post(f"/api/requests/{anfrage['id']}/cancel", headers=anfrage["headers"])

    item = arr_client.get("/api/discover/movie").json()["items"][0]
    assert item["status"] == "not_requested"


def test_fremde_anfrage_kann_man_nicht_abbrechen(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    anfrage = _laufende_anfrage(arr_client, "kim")
    create_user(arr_client, "alex")
    alex = auth_headers(arr_client, "alex", "passwort-1234")

    assert arr_client.post(f"/api/requests/{anfrage['id']}/cancel", headers=alex).status_code == 404
    assert geloescht_in_radarr == []


def test_wartende_anfrage_wird_nicht_abgebrochen_sondern_zurueckgezogen(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    """Was noch auf Freigabe wartet, liegt gar nicht in Radarr."""
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = arr_client.get("/api/discover/movie").json()["items"][0]
    angelegt = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    ).json()

    assert arr_client.post(f"/api/requests/{angelegt['id']}/cancel", headers=headers).status_code == 409
    assert geloescht_in_radarr == []


def test_admin_bricht_fremde_anfrage_ab(
    arr_client: TestClient, geloescht_in_radarr: list
) -> None:
    anfrage = _laufende_anfrage(arr_client)

    antwort = arr_client.post(f"/api/admin/requests/{anfrage['id']}/cancel")
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "cancelled"

    # Der Anfragende wird informiert.
    with SessionLocal() as session:
        kim = session.query(User).filter(User.username == "kim").one()
        assert "cancelled" in [n.type.value for n in kim.notifications]


def test_sammelfreigabe(arr_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drei Anfragen eines Benutzers auf einen Schlag freigeben."""

    async def push(db, settings, request):  # noqa: ANN001
        request.status = RequestStatus.searching
        request.arr_id = 1000 + request.id
        db.commit()
        return request

    from app.services import requests_service

    monkeypatch.setattr(requests_service, "push_to_arr", push)

    created = create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    for index in range(3):
        item = arr_client.get("/api/discover/movie").json()["items"][index]
        arr_client.post(
            "/api/requests",
            json={
                "media_type": "movie",
                "tmdb_id": item["tmdb_id"],
                "quality_profile_id": 1,
                "root_folder_path": "/data/Movies",
            },
            headers=headers,
        )

    assert arr_client.get("/api/admin/requests/pending/count").json() == {"pending": 3}

    antwort = arr_client.post(f"/api/admin/requests/approve-all/{created['id']}")
    assert antwort.status_code == 200
    assert len(antwort.json()) == 3
    assert {eintrag["status"] for eintrag in antwort.json()} == {"searching"}
    assert arr_client.get("/api/admin/requests/pending/count").json() == {"pending": 0}


def test_sammelfreigabe_ohne_offene_anfragen(arr_client: TestClient) -> None:
    created = create_user(arr_client, "kim")
    assert arr_client.post(f"/api/admin/requests/approve-all/{created['id']}").status_code == 404


def test_abbrechen_braucht_rechte(arr_client: TestClient, geloescht_in_radarr: list) -> None:
    anfrage = _laufende_anfrage(arr_client, "kim")
    create_user(arr_client, "alex")
    alex = auth_headers(arr_client, "alex", "passwort-1234")

    assert (
        arr_client.post(f"/api/admin/requests/{anfrage['id']}/cancel", headers=alex).status_code
        == 403
    )


def test_abbrechen_gelingt_wenn_der_titel_dort_schon_weg_ist(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein 500er beim Löschen darf keine Anfrage einsperren.

    Gemessen an der Live-Instanz: Wer eine Serie in Sonarr von Hand entfernt
    und danach in Nexview abbricht, bekommt **500** statt 404 -

        DELETE /api/v3/series/213?deleteFiles=true -> 500
        POST   /api/requests/6/cancel              -> 502

    Die Anfrage war damit nicht mehr loszuwerden: Jeder weitere Versuch lief
    in denselben Fehler. Jetzt wird nachgesehen, ob der Titel überhaupt noch
    dort liegt - ist er weg, ist das Ziel erreicht.
    """
    anfrage = _laufende_anfrage(arr_client)

    async def remove(_self: RadarrClient, _arr_id: int, delete_files: bool = True) -> None:
        raise ArrError("Radarr meldet einen Fehler (HTTP 500).", 500)

    async def get(_self: RadarrClient, pfad: str, params: dict | None = None) -> None:
        assert pfad == "/movie/4711"
        raise ArrError("Radarr kennt diesen Film nicht.", 404)

    monkeypatch.setattr(RadarrClient, "remove", remove)
    monkeypatch.setattr(RadarrClient, "get", get)

    antwort = arr_client.post(
        f"/api/requests/{anfrage['id']}/cancel", headers=anfrage["headers"]
    )
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "cancelled"


def test_abbrechen_scheitert_weiter_wenn_der_titel_noch_dort_liegt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Gegenprobe wegen: Ein echter Fehler bleibt ein Fehler.

    Sonst hiesse "abgebrochen" irgendwann "die Dateien sind weg", waehrend sie
    weiter auf der Platte liegen - und niemand suchte sie dort noch.
    """
    anfrage = _laufende_anfrage(arr_client)

    async def remove(_self: RadarrClient, _arr_id: int, delete_files: bool = True) -> None:
        raise ArrError("Radarr meldet einen Fehler (HTTP 500).", 500)

    async def get(_self: RadarrClient, _pfad: str, params: dict | None = None) -> dict:
        return {"id": 4711, "title": "Liegt noch da"}

    monkeypatch.setattr(RadarrClient, "remove", remove)
    monkeypatch.setattr(RadarrClient, "get", get)

    antwort = arr_client.post(
        f"/api/requests/{anfrage['id']}/cancel", headers=anfrage["headers"]
    )
    assert antwort.status_code == 502

    with SessionLocal() as session:
        request = session.get(MediaRequest, anfrage["id"])
        assert request is not None
        assert request.status == RequestStatus.searching


# --- Serien: Der Abbruch trifft nur noch das selbst Bestellte -----------------


@pytest.fixture
def sonarr_protokoll(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Merkt sich, was in Sonarr geschehen wäre - ohne echten Aufruf."""
    aufrufe: dict[str, list] = {
        "entfernt": [],
        "stillgelegte_staffeln": [],
        "geloeschte_dateien": [],
        "stillgelegte_serien": [],
    }

    async def remove(_self: SonarrClient, arr_id: int, delete_files: bool = True) -> None:
        aufrufe["entfernt"].append((arr_id, delete_files))

    async def episode_files(_self: SonarrClient, arr_id: int, season: int) -> list[dict]:
        return [
            {"id": 90 + season, "seasonNumber": season},
            {"id": 190 + season, "seasonNumber": season},
        ]

    async def unmonitor_season(_self: SonarrClient, arr_id: int, season: int) -> None:
        aufrufe["stillgelegte_staffeln"].append((arr_id, season))

    async def delete_episode_files(_self: SonarrClient, datei_ids: list[int]) -> int:
        assert datei_ids, "delete_episode_files darf nie mit leerer Liste laufen"
        aufrufe["geloeschte_dateien"].extend(datei_ids)
        return len(datei_ids)

    async def serie_stilllegen(_self: SonarrClient, arr_id: int) -> None:
        aufrufe["stillgelegte_serien"].append(arr_id)

    monkeypatch.setattr(SonarrClient, "remove", remove)
    monkeypatch.setattr(SonarrClient, "episode_files", episode_files)
    monkeypatch.setattr(SonarrClient, "unmonitor_season", unmonitor_season)
    monkeypatch.setattr(SonarrClient, "delete_episode_files", delete_episode_files)
    monkeypatch.setattr(SonarrClient, "serie_stilllegen", serie_stilllegen)
    return aufrufe


def _laufende_serienanfrage(
    client: TestClient,
    benutzer: str = "kim",
    season: int | None = 2,
    serie: dict | None = None,
    headers: dict | None = None,
) -> dict:
    """Serien-Anfrage anlegen und wie eine laufende aussehen lassen."""
    if headers is None:
        create_user(client, benutzer)
        headers = auth_headers(client, benutzer, "passwort-1234")
    if serie is None:
        serie = client.get("/api/discover/tv?page=1").json()["items"][0]

    nutzlast = {"media_type": "tv", "tmdb_id": serie["tmdb_id"], "quality_profile_id": 1}
    if season is not None:
        nutzlast["season"] = season
    angelegt = client.post("/api/requests", json=nutzlast, headers=headers)
    assert angelegt.status_code == 201, angelegt.text

    kennung = angelegt.json()["id"]
    with SessionLocal() as session:
        request = session.get(MediaRequest, kennung)
        assert request is not None
        request.status = RequestStatus.searching
        request.arr_id = 4711
        session.commit()

    return {"id": kennung, "headers": headers, "serie": serie}


def test_letzte_serienanfrage_loescht_die_serie(
    arr_client: TestClient, sonarr_protokoll: dict
) -> None:
    """Will niemand sonst etwas von der Serie, fliegt sie wie bisher ganz raus."""
    anfrage = _laufende_serienanfrage(arr_client)

    antwort = arr_client.post(
        f"/api/requests/{anfrage['id']}/cancel", headers=anfrage["headers"]
    )
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "cancelled"

    assert sonarr_protokoll["entfernt"] == [(4711, True)]
    assert sonarr_protokoll["stillgelegte_staffeln"] == []
    assert sonarr_protokoll["geloeschte_dateien"] == []


def test_abbruch_verschont_fremde_staffeln(
    arr_client: TestClient, sonarr_protokoll: dict
) -> None:
    """Kims Abbruch von Staffel 2 darf Alex' Staffel 3 nicht mitreissen.

    Frueher loeschte der Abbruch die **ganze Serie samt Dateien** - auch die
    Staffeln anderer Nutzer. Jetzt gehen nur Kims Staffel-Dateien, die Staffel
    wird stillgelegt, und die Serie bleibt stehen.
    """
    kim = _laufende_serienanfrage(arr_client, "kim", season=2)
    alex = _laufende_serienanfrage(arr_client, "alex", season=3, serie=kim["serie"])

    antwort = arr_client.post(
        f"/api/requests/{kim['id']}/cancel", headers=kim["headers"]
    )
    assert antwort.status_code == 200

    # Nur Kims Staffel 2: stillgelegt und deren Dateien geloescht - kein remove.
    assert sonarr_protokoll["entfernt"] == []
    assert sonarr_protokoll["stillgelegte_staffeln"] == [(4711, 2)]
    assert sorted(sonarr_protokoll["geloeschte_dateien"]) == [92, 192]

    with SessionLocal() as session:
        assert session.get(MediaRequest, alex["id"]).status == RequestStatus.searching
        assert session.get(MediaRequest, kim["id"]).status == RequestStatus.cancelled


def test_abbruch_verschont_die_eigene_zweite_staffel(
    arr_client: TestClient, sonarr_protokoll: dict
) -> None:
    """Auch die zweite Staffel desselben Nutzers zaehlt als "noch gewollt"."""
    erste = _laufende_serienanfrage(arr_client, "kim", season=2)
    zweite = _laufende_serienanfrage(
        arr_client, season=3, serie=erste["serie"], headers=erste["headers"]
    )

    antwort = arr_client.post(
        f"/api/requests/{zweite['id']}/cancel", headers=erste["headers"]
    )
    assert antwort.status_code == 200

    assert sonarr_protokoll["entfernt"] == []
    assert sonarr_protokoll["stillgelegte_staffeln"] == [(4711, 3)]
    assert sorted(sonarr_protokoll["geloeschte_dateien"]) == [93, 193]


def test_wartende_anfrage_schuetzt_die_serie(
    arr_client: TestClient, sonarr_protokoll: dict
) -> None:
    """Auch wer noch auf Freigabe wartet, will die Serie - sie bleibt stehen."""
    kim = _laufende_serienanfrage(arr_client, "kim", season=2)

    create_user(arr_client, "alex")
    alex = auth_headers(arr_client, "alex", "passwort-1234")
    wartend = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": kim["serie"]["tmdb_id"], "quality_profile_id": 1, "season": 3},
        headers=alex,
    )
    assert wartend.status_code == 201

    antwort = arr_client.post(
        f"/api/requests/{kim['id']}/cancel", headers=kim["headers"]
    )
    assert antwort.status_code == 200

    assert sonarr_protokoll["entfernt"] == []
    assert sonarr_protokoll["stillgelegte_staffeln"] == [(4711, 2)]


def test_abbruch_ohne_dateien_loescht_keine(
    arr_client: TestClient, sonarr_protokoll: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liegt von der eigenen Staffel noch nichts, gibt es nichts zu loeschen -
    und vor allem keinen Fehlschlag wegen einer leeren Loeschliste."""
    kim = _laufende_serienanfrage(arr_client, "kim", season=2)
    _laufende_serienanfrage(arr_client, "alex", season=3, serie=kim["serie"])

    async def keine_dateien(_self: SonarrClient, arr_id: int, season: int) -> list[dict]:
        return []

    monkeypatch.setattr(SonarrClient, "episode_files", keine_dateien)

    antwort = arr_client.post(
        f"/api/requests/{kim['id']}/cancel", headers=kim["headers"]
    )
    assert antwort.status_code == 200
    assert sonarr_protokoll["stillgelegte_staffeln"] == [(4711, 2)]
    assert sonarr_protokoll["geloeschte_dateien"] == []


def test_serienabbruch_bei_altbestand_legt_nur_still(
    arr_client: TestClient, sonarr_protokoll: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Altdaten: eine Ganze-Serie-Anfrage neben einer fremden Staffel.

    Anlegbar ist das laengst nicht mehr, Bestand kann es aber enthalten. Dann
    wird die Serie stillgelegt und es fallen nur die Staffeln, die niemand
    will - Alex' Staffel 3 bleibt liegen.
    """
    kim = _laufende_serienanfrage(arr_client, "kim", season=None)

    create_user(arr_client, "alex")
    with SessionLocal() as session:
        alex = session.query(User).filter(User.username == "alex").one()
        session.add(
            MediaRequest(
                user_id=alex.id,
                media_type=MediaType.tv,
                tmdb_id=kim["serie"]["tmdb_id"],
                title=kim["serie"].get("title") or "Testserie",
                season=3,
                status=RequestStatus.searching,
                arr_id=4711,
            )
        )
        session.commit()

    async def get(_self: SonarrClient, pfad: str, params: dict | None = None) -> list:
        assert pfad == "/episodefile"
        return [{"id": 71, "seasonNumber": 1}, {"id": 73, "seasonNumber": 3}]

    monkeypatch.setattr(SonarrClient, "get", get)

    antwort = arr_client.post(
        f"/api/requests/{kim['id']}/cancel", headers=kim["headers"]
    )
    assert antwort.status_code == 200

    assert sonarr_protokoll["entfernt"] == []
    assert sonarr_protokoll["stillgelegte_serien"] == [4711]
    # Nur die Datei aus Staffel 1 faellt - Staffel 3 ist noch gewollt.
    assert sonarr_protokoll["geloeschte_dateien"] == [71]


def test_serienabbruch_gelingt_wenn_die_serie_schon_weg_ist(
    arr_client: TestClient, sonarr_protokoll: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der 500er-Griff gilt auch im staffelgenauen Pfad: weg ist weg."""
    kim = _laufende_serienanfrage(arr_client, "kim", season=2)
    _laufende_serienanfrage(arr_client, "alex", season=3, serie=kim["serie"])

    async def streikt(_self: SonarrClient, arr_id: int, season: int) -> None:
        raise ArrError("Sonarr meldet einen Fehler (HTTP 500).", 500)

    async def weg(_self: SonarrClient, pfad: str, params: dict | None = None) -> None:
        assert pfad == "/series/4711"
        raise ArrError("Sonarr kennt diese Serie nicht.", 404)

    monkeypatch.setattr(SonarrClient, "unmonitor_season", streikt)
    monkeypatch.setattr(SonarrClient, "get", weg)

    antwort = arr_client.post(
        f"/api/requests/{kim['id']}/cancel", headers=kim["headers"]
    )
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "cancelled"


def test_serienabbruch_scheitert_wenn_sonarr_wirklich_streikt(
    arr_client: TestClient, sonarr_protokoll: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe: Liegt die Serie noch dort, bleibt der Fehler ein Fehler."""
    kim = _laufende_serienanfrage(arr_client, "kim", season=2)
    _laufende_serienanfrage(arr_client, "alex", season=3, serie=kim["serie"])

    async def streikt(_self: SonarrClient, arr_id: int, season: int) -> None:
        raise ArrError("Sonarr meldet einen Fehler (HTTP 500).", 500)

    async def liegt_noch_da(_self: SonarrClient, pfad: str, params: dict | None = None) -> dict:
        return {"id": 4711, "title": "Liegt noch da"}

    monkeypatch.setattr(SonarrClient, "unmonitor_season", streikt)
    monkeypatch.setattr(SonarrClient, "get", liegt_noch_da)

    antwort = arr_client.post(
        f"/api/requests/{kim['id']}/cancel", headers=kim["headers"]
    )
    assert antwort.status_code == 502

    with SessionLocal() as session:
        assert session.get(MediaRequest, kim["id"]).status == RequestStatus.searching
