"""Folgen-Pakete: einzelne Folgen einer Staffel anfragen.

Die Abdeckungs-Leiter: Die ganze Serie deckt jede Staffel ab, eine Staffel
jede ihrer Folgen, ein Paket genau seine Folgen. Je Folge gibt es hoechstens
einen laufenden Besitzer je Stufe - sonst waere beim Loeschen und Abrechnen
unklar, wem die Datei gehoert.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    QualityTier,
    RequestStatus,
    StorageEntry,
    StorageState,
    User,
    utcnow,
)
from app.services import library, requests_service, status_poller, storage
from app.services.settings_service import load_settings
from app.services.sonarr import Folge, LibraryEntry, SonarrClient, Staffelstand
from tests.conftest import auth_headers, create_user

TVDB = 77304
ARR_ID = 211


@pytest.fixture
def nutzer(arr_client: TestClient) -> dict[str, str]:
    create_user(arr_client, "lena")
    return auth_headers(arr_client, "lena", "passwort-1234")


def _serie(client: TestClient) -> dict:
    antwort = client.get("/api/discover/tv?page=1")
    assert antwort.status_code == 200, antwort.text
    return antwort.json()["items"][0]


def _anfragen(
    client: TestClient,
    serie: dict,
    season: int | None = None,
    episodes: list[int] | None = None,
    **extra,
):
    nutzlast = {
        "media_type": "tv",
        "tmdb_id": serie["tmdb_id"],
        "quality_profile_id": 1,
    }
    if season is not None:
        nutzlast["season"] = season
    if episodes is not None:
        nutzlast["episodes"] = episodes
    return client.post("/api/requests", json=nutzlast, **extra)


# --- Anlegen -----------------------------------------------------------------


def test_paket_wird_sortiert_und_dedupliziert(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    antwort = _anfragen(arr_client, serie, season=2, episodes=[7, 3, 3], headers=nutzer)
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["season"] == 2
    assert antwort.json()["episodes"] == [3, 7]


def test_paket_folgt_keinem_nachschub(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Ein Paket ist eine feste Liste - "auch kuenftige" gilt fuer es nie."""
    serie = _serie(arr_client)
    nutzlast = {
        "media_type": "tv",
        "tmdb_id": serie["tmdb_id"],
        "quality_profile_id": 1,
        "season": 2,
        "episodes": [1],
        "monitor_future": True,
    }
    antwort = arr_client.post("/api/requests", json=nutzlast, headers=nutzer)
    assert antwort.status_code == 201, antwort.text

    with SessionLocal() as db:
        zeile = db.get(MediaRequest, antwort.json()["id"])
        assert zeile is not None
        assert zeile.monitor_future is False


def test_folgen_ohne_staffel_scheitern(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    antwort = _anfragen(arr_client, serie, season=None, episodes=[1, 2], headers=nutzer)
    assert antwort.status_code == 422


def test_folgen_bei_filmen_werden_verworfen(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Dieselbe stille Nachsicht wie bei der Staffel."""
    film = arr_client.get("/api/discover/movie").json()["items"][0]
    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": film["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
            "season": 2,
            "episodes": [1, 2],
        },
        headers=nutzer,
    )
    assert antwort.status_code == 201, antwort.text

    with SessionLocal() as db:
        zeile = db.get(MediaRequest, antwort.json()["id"])
        assert zeile is not None
        assert zeile.season is None
        assert zeile.episodes is None


# --- Abdeckungs-Leiter -------------------------------------------------------


def test_ganze_serie_deckt_pakete_ab(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, headers=nutzer).status_code == 201

    paket = _anfragen(arr_client, serie, season=2, episodes=[3], headers=nutzer)
    assert paket.status_code == 409
    assert "komplett" in paket.json()["detail"]


def test_volle_staffel_deckt_pakete_ab(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, season=2, headers=nutzer).status_code == 201

    paket = _anfragen(arr_client, serie, season=2, episodes=[3, 7], headers=nutzer)
    assert paket.status_code == 409
    assert "abgedeckt" in paket.json()["detail"]


def test_pakete_ohne_ueberschneidung_laufen_nebeneinander(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, season=2, episodes=[1, 2], headers=nutzer).status_code == 201

    create_user(arr_client, "ben")
    ben = auth_headers(arr_client, "ben", "passwort-1234")
    assert _anfragen(arr_client, serie, season=2, episodes=[5, 6], headers=ben).status_code == 201

    with SessionLocal() as db:
        # Pakete grauen die Staffel nicht aus - "belegt" sind nur die Folgen,
        # jede mit dem Status ihres Besitzers (hier: alle warten auf Freigabe).
        assert 2 not in requests_service.angefragte_staffeln(db, serie["tmdb_id"])
        pakete = requests_service.angefragte_pakete(db, serie["tmdb_id"])
        assert sorted(pakete[2]) == [1, 2, 5, 6]
        assert set(pakete[2].values()) == {"pending_approval"}


def test_ueberschneidung_wird_abgelehnt(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, season=2, episodes=[1, 2], headers=nutzer).status_code == 201

    doppelt = _anfragen(arr_client, serie, season=2, episodes=[2, 3], headers=nutzer)
    assert doppelt.status_code == 409
    assert "Folge 2" in doppelt.json()["detail"]


def test_volle_staffel_nennt_die_belegten_folgen(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Kims Fall aus der Planung: Staffel 2 komplett, aber Folge 3+7 laufen."""
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, season=2, episodes=[3, 7], headers=nutzer).status_code == 201

    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    ganze = _anfragen(arr_client, serie, season=2, headers=kim)
    assert ganze.status_code == 409
    assert "3 und 7" in ganze.json()["detail"]
    assert "übrigen" in ganze.json()["detail"]


def test_paket_in_anderer_staffel_stoert_nicht(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, season=2, episodes=[1], headers=nutzer).status_code == 201
    assert _anfragen(arr_client, serie, season=3, episodes=[1], headers=nutzer).status_code == 201


# --- Haus-Schalter -----------------------------------------------------------


def test_haus_schalter_sperrt_pakete(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Abgeschaltet heisst: nur noch ganze Staffeln - wie vor dem Umbau."""
    antwort = arr_client.put("/api/settings", json={"episode_requests_enabled": False})
    assert antwort.status_code == 200, antwort.text
    assert arr_client.get("/api/config").json()["episode_requests_enabled"] is False

    serie = _serie(arr_client)
    paket = _anfragen(arr_client, serie, season=2, episodes=[3], headers=nutzer)
    assert paket.status_code == 403
    assert "abgeschaltet" in paket.json()["detail"]

    assert _anfragen(arr_client, serie, season=2, headers=nutzer).status_code == 201


def test_angefragte_folgen_nennen_das_belegte(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    with SessionLocal() as db:
        assert requests_service.angefragte_folgen(db, serie["tmdb_id"], 2) == set()

    assert _anfragen(arr_client, serie, season=2, episodes=[3, 7], headers=nutzer).status_code == 201
    with SessionLocal() as db:
        assert requests_service.angefragte_folgen(db, serie["tmdb_id"], 2) == {3, 7}

    assert _anfragen(arr_client, serie, season=3, headers=nutzer).status_code == 201
    with SessionLocal() as db:
        # Volle Staffel: alles belegt - "None" heisst "jede Folge".
        assert requests_service.angefragte_folgen(db, serie["tmdb_id"], 3) is None


def test_betreff_nennt_die_folgen() -> None:
    from app.services import channel_outbox

    paket = MediaRequest(media_type=MediaType.tv, season=2, episodes=[3, 7])
    assert channel_outbox.folgen_zusatz(paket, "de") == " · Folge 3, 7"
    assert channel_outbox.folgen_zusatz(paket, "en") == " · Episode 3, 7"

    staffel = MediaRequest(media_type=MediaType.tv, season=2, episodes=None)
    assert channel_outbox.folgen_zusatz(staffel, "de") == ""


def test_staffel_belegung_nennt_den_status(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Der Vertrag fuer die ehrlichen Worte: "wartet" ist kein "laeuft"."""
    serie = _serie(arr_client)
    angelegt = _anfragen(arr_client, serie, season=3, headers=nutzer).json()

    with SessionLocal() as db:
        assert requests_service.staffel_belegung(db, serie["tmdb_id"]) == {
            3: "pending_approval"
        }
        db.get(MediaRequest, angelegt["id"]).status = RequestStatus.searching
        db.commit()
        assert requests_service.staffel_belegung(db, serie["tmdb_id"]) == {
            3: "searching"
        }


def test_freigabeliste_traegt_die_folgen(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Der Entscheider muss sehen, ob er eine Folge oder die ganze Staffel
    zusagt - live gemeldet, als die Freigabeseite nur "St. 5" zeigte."""
    serie = _serie(arr_client)
    assert _anfragen(arr_client, serie, season=5, episodes=[5], headers=nutzer).status_code == 201
    assert _anfragen(arr_client, serie, season=3, headers=nutzer).status_code == 201

    zeilen = arr_client.get("/api/admin/requests").json()
    nach_staffel = {
        zeile["season"]: zeile.get("episodes")
        for zeile in zeilen
        if zeile["tmdb_id"] == serie["tmdb_id"]
    }
    assert nach_staffel[5] == [5]
    assert nach_staffel[3] is None


# --- Kontingent --------------------------------------------------------------


def test_paket_kostet_einen_platz(arr_client: TestClient) -> None:
    """Folge 3+7 = 1 Platz, wie eine ganze Staffel - so ist es entschieden."""
    created = create_user(arr_client, "lena")
    arr_client.patch(f"/api/users/{created['id']}", json={"quota_series_limit": 1})
    headers = auth_headers(arr_client, "lena", "passwort-1234")
    serie = _serie(arr_client)

    assert _anfragen(arr_client, serie, season=2, episodes=[3, 7], headers=headers).status_code == 201

    stand = arr_client.get("/api/requests/quota", headers=headers).json()
    assert stand["tv"]["used"] == 1

    zweite = _anfragen(arr_client, serie, season=3, episodes=[1], headers=headers)
    assert zweite.status_code == 429


# --- Zurueckgestellte --------------------------------------------------------


def _zurueckgestelltes_paket(
    benutzer: User, serie: dict, episodes: list[int] | None, season: int = 2
) -> MediaRequest:
    return MediaRequest(
        user_id=benutzer.id,
        media_type=MediaType.tv,
        tmdb_id=serie["tmdb_id"],
        title=serie.get("title") or "Testserie",
        season=season,
        episodes=episodes,
        status=RequestStatus.deferred,
    )


def test_eigenes_zurueckgestelltes_paket_blockiert_nur_ueberschneidung(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    serie = _serie(arr_client)
    with SessionLocal() as db:
        lena = db.query(User).filter(User.username == "lena").one()
        db.add(_zurueckgestelltes_paket(lena, serie, [1, 2]))
        db.commit()

    assert _anfragen(arr_client, serie, season=2, episodes=[5, 6], headers=nutzer).status_code == 201

    konflikt = _anfragen(arr_client, serie, season=2, episodes=[2, 9], headers=nutzer)
    assert konflikt.status_code == 409
    assert "zurück" in konflikt.json()["detail"]


def test_freigegebenes_paket_schliesst_nur_gedeckte_zurueckgestellte(
    arr_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Wer mehr wollte als das Paket liefert, bleibt zurueckgestellt."""
    serie = _serie(arr_client)
    angelegt = _anfragen(
        arr_client, serie, season=2, episodes=[1, 2, 3], headers=nutzer
    ).json()

    create_user(arr_client, "ben")
    with SessionLocal() as db:
        ben = db.query(User).filter(User.username == "ben").one()
        teil = _zurueckgestelltes_paket(ben, serie, [2, 3])
        ganze_staffel = _zurueckgestelltes_paket(ben, serie, None)
        groesseres = _zurueckgestelltes_paket(ben, serie, [1, 4])
        db.add_all([teil, ganze_staffel, groesseres])
        db.commit()

        anfrage = db.get(MediaRequest, angelegt["id"])
        geschlossen = requests_service.zurueckgestellte_schliessen(db, anfrage)
        db.commit()

        assert [zeile.id for zeile in geschlossen] == [teil.id]
        assert db.get(MediaRequest, teil.id).status == RequestStatus.cancelled
        assert db.get(MediaRequest, ganze_staffel.id).status == RequestStatus.deferred
        assert db.get(MediaRequest, groesseres.id).status == RequestStatus.deferred


# --- Uebergabe, Abgleich, Abbruch --------------------------------------------


def _paketzeile(
    db,
    user_id: int,
    episodes: list[int],
    status: RequestStatus = RequestStatus.searching,
    arr_id: int | None = ARR_ID,
) -> int:
    zeile = MediaRequest(
        user_id=user_id,
        media_type=MediaType.tv,
        tier=QualityTier.standard,
        tmdb_id=4386,
        tvdb_id=TVDB,
        title="Baywatch",
        release_date="1989-04-23",
        season=2,
        episodes=episodes,
        status=status,
        arr_id=arr_id,
        quality_profile_id=1,
        root_folder_path="/data/TV Shows",
    )
    db.add(zeile)
    db.commit()
    return zeile.id


def _bibliothek(monkeypatch: pytest.MonkeyPatch, vorhanden: bool = True) -> None:
    eintrag = LibraryEntry(
        arr_id=ARR_ID,
        has_file=False,
        monitored=True,
        episode_file_count=0,
        episode_count=22,
        title_key="baywatch",
        year=1989,
        title="Baywatch",
        path="/tv/Baywatch",
        seasons={2: 5000},
        staffeln={2: Staffelstand(dateien=0, folgen=22)},
    )

    async def bibliothek(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
        if not vorhanden:
            return {}, {}
        return {TVDB: eintrag}, {eintrag.title_key: eintrag}

    monkeypatch.setattr(library, "series_library", bibliothek)


def _folgenstand(
    monkeypatch: pytest.MonkeyPatch, folgen: dict[int, Folge]
) -> None:
    async def stand(_self: SonarrClient, _arr_id: int) -> dict:
        return {2: folgen} if folgen else {}

    monkeypatch.setattr(SonarrClient, "folgen_stand", stand)


def _folge(nummer: int, *, monitored: bool = True, has_file: bool = False, datei_id: int | None = None) -> Folge:
    return Folge(
        kennung=500 + nummer,
        nummer=nummer,
        monitored=monitored,
        has_file=has_file,
        datei_id=datei_id,
    )


@pytest.fixture
def sonarr_schalter(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Zeichnet die folgengenauen Sonarr-Aufrufe auf - ohne echten Aufruf."""
    aufrufe: dict[str, list] = {
        "geschaltet": [],
        "gesucht": [],
        "serie_ueberwacht": [],
        "geloeschte_dateien": [],
    }

    async def folgen_schalten(_self: SonarrClient, kennungen: list[int], ueberwachen: bool) -> None:
        aufrufe["geschaltet"].append((list(kennungen), ueberwachen))

    async def folgen_suchen(_self: SonarrClient, kennungen: list[int]) -> None:
        aufrufe["gesucht"].append(list(kennungen))

    async def serie_ueberwachen(_self: SonarrClient, arr_id: int) -> None:
        aufrufe["serie_ueberwacht"].append(arr_id)

    async def delete_episode_files(_self: SonarrClient, datei_ids: list[int]) -> int:
        aufrufe["geloeschte_dateien"].extend(datei_ids)
        return len(datei_ids)

    monkeypatch.setattr(SonarrClient, "folgen_schalten", folgen_schalten)
    monkeypatch.setattr(SonarrClient, "folgen_suchen", folgen_suchen)
    monkeypatch.setattr(SonarrClient, "serie_ueberwachen", serie_ueberwachen)
    monkeypatch.setattr(SonarrClient, "delete_episode_files", delete_episode_files)
    return aufrufe


@pytest.mark.asyncio
async def test_uebergabe_schaltet_genau_die_folgen(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sonarr_schalter: dict,
) -> None:
    """Serie liegt schon in Sonarr: nichts neu anlegen, Folgen an, Suche an."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    _bibliothek(monkeypatch)
    _folgenstand(
        monkeypatch,
        {1: _folge(1), 3: _folge(3, has_file=True), 5: _folge(5)},
    )

    with SessionLocal() as db:
        kennung = _paketzeile(
            db, konto["id"], [1, 3], status=RequestStatus.approved, arr_id=None
        )
        anfrage = db.get(MediaRequest, kennung)
        await requests_service.push_to_arr(db, load_settings(db), anfrage)

    with SessionLocal() as db:
        zeile = db.get(MediaRequest, kennung)
        assert zeile.status == RequestStatus.searching
        assert zeile.arr_id == ARR_ID

    assert sonarr_schalter["serie_ueberwacht"] == [ARR_ID]
    assert sonarr_schalter["geschaltet"] == [([501, 503], True)]
    # Gesucht wird nur, was noch keine Datei hat - Folge 3 liegt schon.
    assert sonarr_schalter["gesucht"] == [[501]]


@pytest.mark.asyncio
async def test_uebergabe_wartet_auf_die_folgenliste(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sonarr_schalter: dict,
) -> None:
    """Frisch angelegte Serie: Folgen noch unbekannt ist kein Fehlschlag."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    _bibliothek(monkeypatch, vorhanden=False)
    _folgenstand(monkeypatch, {})

    angelegt_mit: list[dict] = []

    async def ensure_tag(_self: SonarrClient, _name: str) -> None:
        return None

    async def add(_self: SonarrClient, _tvdb: int, _profil: int, _ordner: str, **kwargs) -> dict:
        angelegt_mit.append(kwargs)
        return {"id": 999}

    monkeypatch.setattr(SonarrClient, "ensure_tag", ensure_tag)
    monkeypatch.setattr(SonarrClient, "add", add)

    with SessionLocal() as db:
        kennung = _paketzeile(
            db, konto["id"], [1, 2], status=RequestStatus.approved, arr_id=None
        )
        anfrage = db.get(MediaRequest, kennung)
        await requests_service.push_to_arr(db, load_settings(db), anfrage)

    with SessionLocal() as db:
        zeile = db.get(MediaRequest, kennung)
        assert zeile.status == RequestStatus.searching
        assert zeile.arr_id == 999
        assert zeile.error_detail is None

    assert angelegt_mit and angelegt_mit[0].get("nur_anlegen") is True
    assert sonarr_schalter["geschaltet"] == []


@pytest.mark.asyncio
async def test_uebergabe_meldet_unbekannte_folge(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sonarr_schalter: dict,
) -> None:
    """Sonarr kennt die Staffel, aber nicht die Nummer - ein echter Fehler."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    _bibliothek(monkeypatch)
    _folgenstand(monkeypatch, {1: _folge(1), 2: _folge(2)})

    with SessionLocal() as db:
        kennung = _paketzeile(
            db, konto["id"], [9], status=RequestStatus.approved, arr_id=None
        )
        anfrage = db.get(MediaRequest, kennung)
        with pytest.raises(requests_service.RequestError):
            await requests_service.push_to_arr(db, load_settings(db), anfrage)

    with SessionLocal() as db:
        zeile = db.get(MediaRequest, kennung)
        assert zeile.status == RequestStatus.failed
        assert zeile.error_detail is not None
        assert zeile.error_detail["code"] == "sonarr_episode_unknown"
        assert zeile.error_detail["episode"] == 9


@pytest.mark.asyncio
async def test_paket_wird_erst_mit_allen_folgen_fertig(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    konto = create_user(arr_client, "kim", "passwort-1234")
    _bibliothek(monkeypatch)
    _folgenstand(monkeypatch, {3: _folge(3, has_file=True), 7: _folge(7)})

    with SessionLocal() as db:
        kennung = _paketzeile(db, konto["id"], [3, 7])

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 0
    with SessionLocal() as db:
        assert db.get(MediaRequest, kennung).status == RequestStatus.searching

    _folgenstand(
        monkeypatch,
        {3: _folge(3, has_file=True), 7: _folge(7, has_file=True)},
    )
    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 1
    with SessionLocal() as db:
        assert db.get(MediaRequest, kennung).status == RequestStatus.downloaded


@pytest.mark.asyncio
async def test_heilung_schaltet_abgeraeumte_folgen_wieder_ein(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sonarr_schalter: dict,
) -> None:
    """Sonarrs asynchrones Abraeumen trifft auch Folgen - der Abgleich heilt."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    _bibliothek(monkeypatch)
    _folgenstand(monkeypatch, {3: _folge(3, monitored=False), 7: _folge(7, monitored=False)})

    with SessionLocal() as db:
        kennung = _paketzeile(db, konto["id"], [3, 7])

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    assert sonarr_schalter["geschaltet"] == [([503, 507], True)]
    assert sonarr_schalter["gesucht"] == [[503, 507]]
    with SessionLocal() as db:
        assert db.get(MediaRequest, kennung).status == RequestStatus.searching


@pytest.mark.asyncio
async def test_geloeschtes_paket_wird_erkannt(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alle eigenen Folgen wieder weg -> "geloescht". Eine liegt noch -> nicht."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    _bibliothek(monkeypatch)
    _folgenstand(monkeypatch, {3: _folge(3, has_file=True), 7: _folge(7)})

    with SessionLocal() as db:
        kennung = _paketzeile(db, konto["id"], [3, 7], status=RequestStatus.downloaded)

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))
    with SessionLocal() as db:
        assert db.get(MediaRequest, kennung).status == RequestStatus.downloaded

    _folgenstand(monkeypatch, {3: _folge(3), 7: _folge(7)})
    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))
    with SessionLocal() as db:
        assert db.get(MediaRequest, kennung).status == RequestStatus.deleted


@pytest.mark.asyncio
async def test_paketabbruch_trifft_nur_die_eigenen_folgen(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sonarr_schalter: dict,
) -> None:
    """Kims Paket faellt, Bens Paket derselben Staffel bleibt unberuehrt."""
    kim = create_user(arr_client, "kim", "passwort-1234")
    ben = create_user(arr_client, "ben", "passwort-1234")
    _folgenstand(
        monkeypatch,
        {
            3: _folge(3, has_file=True, datei_id=91),
            7: _folge(7),
            1: _folge(1, has_file=True, datei_id=81),
        },
    )

    with SessionLocal() as db:
        kims = _paketzeile(db, kim["id"], [3, 7])
        bens = _paketzeile(db, ben["id"], [1])

    antwort = arr_client.post(f"/api/admin/requests/{kims}/cancel")
    assert antwort.status_code == 200, antwort.text

    # Nur Kims Folgen stillgelegt, nur ihre eine Datei geloescht.
    assert sonarr_schalter["geschaltet"] == [([503, 507], False)]
    assert sonarr_schalter["geloeschte_dateien"] == [91]

    with SessionLocal() as db:
        assert db.get(MediaRequest, kims).status == RequestStatus.cancelled
        assert db.get(MediaRequest, bens).status == RequestStatus.searching


# --- Speicher ----------------------------------------------------------------


def _episodendateien(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sonarrs Dateiliste: 91/92 gehoeren Kims Folgen, 81 dem Rest."""

    async def episode_files(_self: SonarrClient, _arr_id: int, season: int) -> list[dict]:
        return [
            {"id": 91, "seasonNumber": season, "size": 1200},
            {"id": 92, "seasonNumber": season, "size": 800},
            {"id": 81, "seasonNumber": season, "size": 3000},
        ]

    async def get(_self: SonarrClient, pfad: str, params: dict | None = None) -> list:
        assert pfad == "/episodefile"
        return [
            {"id": 91, "size": 1200},
            {"id": 92, "size": 800},
            {"id": 81, "size": 3000},
        ]

    monkeypatch.setattr(SonarrClient, "episode_files", episode_files)
    monkeypatch.setattr(SonarrClient, "get", get)


@pytest.mark.asyncio
async def test_fertiges_paket_bucht_nur_die_eigenen_dateien(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sofort-Zurechnung: Kims Zeile traegt 2 000, nicht die 5 000 der Staffel."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    _bibliothek(monkeypatch)
    _folgenstand(
        monkeypatch,
        {
            3: _folge(3, has_file=True, datei_id=91),
            7: _folge(7, has_file=True, datei_id=92),
        },
    )
    _episodendateien(monkeypatch)

    with SessionLocal() as db:
        kennung = _paketzeile(db, konto["id"], [3, 7])

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 1

    with SessionLocal() as db:
        zeile = db.query(StorageEntry).filter(
            StorageEntry.key.like("%:r%")
        ).one()
        assert zeile.key.endswith(f":r{kennung}")
        assert zeile.size_bytes == 2000
        assert zeile.user_id == konto["id"]
        assert zeile.request_id == kennung


@pytest.mark.asyncio
async def test_stundenabgleich_spaltet_die_staffelzeile(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kims Paket bekommt seine eigene Zeile, der Rest der Staffel bleibt Haus."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    _bibliothek(monkeypatch)
    _folgenstand(
        monkeypatch,
        {
            3: _folge(3, has_file=True, datei_id=91),
            7: _folge(7, has_file=True, datei_id=92),
        },
    )
    _episodendateien(monkeypatch)

    async def keine_filme(_settings: object, _tier: str = "standard") -> dict:
        return {}

    monkeypatch.setattr(library, "movie_library", keine_filme)

    with SessionLocal() as db:
        kennung = _paketzeile(db, konto["id"], [3, 7])
        # Ein Alt-Posten, damit der Lauf nicht als allererster zaehlt (dort
        # gehoerte grundsaetzlich alles dem Haus).
        db.add(
            StorageEntry(
                key="movie:standard:tmdb:1",
                media_type=MediaType.movie,
                tier=QualityTier.standard,
                tmdb_id=1,
                title="Altbestand",
                size_bytes=1,
                measured_at=utcnow(),
                state=StorageState.house,
            )
        )
        db.commit()

    with SessionLocal() as db:
        await storage.abgleichen(db, load_settings(db))

    with SessionLocal() as db:
        paket = db.query(StorageEntry).filter(StorageEntry.key.like("%:r%")).one()
        assert paket.key.endswith(f":r{kennung}")
        assert paket.size_bytes == 2000
        assert paket.user_id == konto["id"]

        staffel = db.query(StorageEntry).filter(
            StorageEntry.key == f"tv:standard:tvdb:{TVDB}:s2"
        ).one()
        assert staffel.size_bytes == 3000
        assert staffel.user_id is None


@pytest.mark.asyncio
async def test_teilgeladenes_paket_zahlt_nur_das_vorhandene(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vier von fuenf Folgen da, die fuenfte kommt nie: Der Stunden-Abgleich
    rechnet laufend zu, was liegt - nicht erst bei "fertig", und nicht mehr."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    _bibliothek(monkeypatch)
    _folgenstand(
        monkeypatch,
        {
            3: _folge(3, has_file=True, datei_id=91),
            7: _folge(7),  # kommt nie
        },
    )
    _episodendateien(monkeypatch)

    async def keine_filme(_settings: object, _tier: str = "standard") -> dict:
        return {}

    monkeypatch.setattr(library, "movie_library", keine_filme)

    with SessionLocal() as db:
        kennung = _paketzeile(db, konto["id"], [3, 7], status=RequestStatus.searching)
        db.add(
            StorageEntry(
                key="movie:standard:tmdb:1",
                media_type=MediaType.movie,
                tier=QualityTier.standard,
                tmdb_id=1,
                title="Altbestand",
                size_bytes=1,
                measured_at=utcnow(),
                state=StorageState.house,
            )
        )
        db.commit()

    with SessionLocal() as db:
        await storage.abgleichen(db, load_settings(db))

    with SessionLocal() as db:
        zeile = db.query(StorageEntry).filter(StorageEntry.key.like("%:r%")).one()
        assert zeile.key.endswith(f":r{kennung}")
        # Nur Folge 3 liegt (Datei 91 = 1200 Bytes) - genau die zaehlt.
        assert zeile.size_bytes == 1200
        assert zeile.user_id == konto["id"]
        # Und die Anfrage bleibt ehrlich auf "wird gesucht".
        assert db.get(MediaRequest, kennung).status == RequestStatus.searching


@pytest.mark.asyncio
async def test_loeschen_eines_paket_postens_trifft_nur_das_paket(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sonarr_schalter: dict,
) -> None:
    """Der Speicher-Loeschweg: nur Kims Dateien fallen, Bens Anfrage lebt."""
    kim = create_user(arr_client, "kim", "passwort-1234")
    ben = create_user(arr_client, "ben", "passwort-1234")
    _bibliothek(monkeypatch)
    _folgenstand(
        monkeypatch,
        {
            3: _folge(3, has_file=True, datei_id=91),
            7: _folge(7, has_file=True, datei_id=92),
            1: _folge(1, has_file=True, datei_id=81),
        },
    )
    _episodendateien(monkeypatch)

    with SessionLocal() as db:
        kims = _paketzeile(db, kim["id"], [3, 7], status=RequestStatus.downloaded)
        bens = _paketzeile(db, ben["id"], [1], status=RequestStatus.downloaded)
        posten = StorageEntry(
            key=f"tv:standard:tvdb:{TVDB}:s2:r{kims}",
            user_id=kim["id"],
            media_type=MediaType.tv,
            tier=QualityTier.standard,
            tvdb_id=TVDB,
            season=2,
            title="Baywatch",
            size_bytes=2000,
            measured_at=utcnow(),
            state=StorageState.owned,
            request_id=kims,
        )
        db.add(posten)
        db.commit()
        posten_id = posten.id

    with SessionLocal() as db:
        await storage.loeschen(db, load_settings(db), posten_id, wer="Test")
        # ``loeschen`` ueberlaesst das Commit bewusst dem Aufrufer.
        db.commit()

    # Nur Kims Folgen stillgelegt und nur ihre Dateien geloescht.
    assert sonarr_schalter["geschaltet"] == [([503, 507], False)]
    assert sorted(sonarr_schalter["geloeschte_dateien"]) == [91, 92]

    with SessionLocal() as db:
        assert db.get(StorageEntry, posten_id) is None
        assert db.get(MediaRequest, kims).status == RequestStatus.deleted
        assert db.get(MediaRequest, bens).status == RequestStatus.downloaded
