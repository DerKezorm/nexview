"""Was die Oberflaeche ueber Fassungen erfaehrt - und wie sie sie nennt.

Scheibe 3 des NEX-Umbaus (Bauplan Abschnitt 8): Karten, Staffeln, Folgen,
Kontorechte und das Anfrageformular sprechen Fassungen statt der zwei festen
Stufen. Die alten Felder (``tier``, ``status_uhd``, die vier ``*_uhd``-Haken)
bleiben als **Ableitung** stehen, weil ``/api/v1`` sie zusagt - dass beides
dasselbe sagt, steht hier.

⚠️ **ARR sieht gleich aus.** Jeder Test hier haelt eine Antwort fest, die es
auch vorher schon gab; neu ist nur, dass sie aus der Fassung kommt.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import FassungRecht, MediaRequest, User
from app.services.beschaffung.arr import library
from app.services.beschaffung.arr.radarr import LibraryEntry

from .conftest import auth_headers, create_user

#: Zwei weitere Instanzen. Eigene Adressen, weil Nexview dieselbe Adresse
#: zweimal ablehnt - und unerreichbar, wie die des Fixtures.
UHD = {
    "radarr_uhd_url": "http://127.0.0.1:10",
    "radarr_uhd_api_key": "test-radarr-uhd-key",
    "sonarr_uhd_url": "http://127.0.0.1:11",
    "sonarr_uhd_api_key": "test-sonarr-uhd-key",
}


def _fassungen(client: TestClient, headers: dict | None = None) -> list[dict]:
    antwort = client.get("/api/config", headers=headers)
    assert antwort.status_code == 200
    return antwort.json()["fassungen"]


def _demo(client: TestClient, media_type: str = "movie", index: int = 0) -> dict:
    """Ein Titel aus den Demo-Daten - echte TMDB-Abfragen gibt es hier nicht."""
    return client.get(f"/api/discover/{media_type}").json()["items"][index]


def _kim_mit_4k(client: TestClient, *kennungen: str) -> dict[str, str]:
    """Ein Konto mit 4K-Recht, aber **ohne** Sofort-Freigabe.

    Absichtlich kein Administrator: Dessen Anfrage ginge sofort an ein Radarr,
    das es im Test nicht gibt. So bleibt sie vor der Uebergabe stehen.
    """
    create_user(client, "kim")
    with SessionLocal() as db:
        kim = db.query(User).filter(User.username == "kim").one()
        for kennung in kennungen or ("radarr-uhd",):
            if kim.fassung_recht(kennung) is None:
                kim.fassung_rechte.append(
                    FassungRecht(fassung_kennung=kennung, anfragen=True, auto_freigabe=False)
                )
        db.commit()
    return auth_headers(client, "kim", "passwort-1234")


# --- Die Liste der Fassungen ------------------------------------------------


def test_die_konfiguration_nennt_jede_fassung_mit_der_hauptfassung_zuerst(arr_client) -> None:
    arr_client.put("/api/settings", json=UHD)

    fassungen = _fassungen(arr_client)

    assert [f["kennung"] for f in fassungen] == [
        "radarr-standard",
        "radarr-uhd",
        "sonarr-standard",
        "sonarr-uhd",
    ]
    haupt = fassungen[0]
    assert haupt["haupt"] is True
    assert haupt["media_type"] == "movie"
    assert haupt["quelle"] == "arr"
    assert haupt["klasse"] == "hd"
    assert haupt["offen_fuer_alle"] is True
    assert fassungen[1]["klasse"] == "uhd"
    assert fassungen[1]["offen_fuer_alle"] is False


def test_die_hauptfassung_steht_auch_ohne_eingerichtete_instanz_dabei(admin_client) -> None:
    """Ohne Radarr sah das Formular immer schon so aus wie mit."""
    fassungen = _fassungen(admin_client)

    assert [f["kennung"] for f in fassungen] == ["radarr-standard", "sonarr-standard"]
    assert all(f["bereit"] is False for f in fassungen)
    assert all(f["haupt"] for f in fassungen)


def test_darf_anfragen_traegt_die_ganze_leiter(arr_client) -> None:
    """Der Server rechnet, die Oberflaeche nicht - sonst liefe beides auseinander."""
    arr_client.put("/api/settings", json=UHD)
    create_user(arr_client, "kim")

    # Als Administrator: alles erlaubt.
    assert all(f["darf_anfragen"] for f in _fassungen(arr_client))

    kim_kopf = auth_headers(arr_client, "kim", "passwort-1234")
    als_kim = {f["kennung"]: f["darf_anfragen"] for f in _fassungen(arr_client, kim_kopf)}
    assert als_kim == {
        "radarr-standard": True,
        "radarr-uhd": False,
        "sonarr-standard": True,
        "sonarr-uhd": False,
    }

    with SessionLocal() as db:
        kim = db.query(User).filter(User.username == "kim").one()
        kim.fassung_rechte.append(
            FassungRecht(fassung_kennung="radarr-uhd", anfragen=True, auto_freigabe=False)
        )
        db.commit()

    nachher = {f["kennung"]: f["darf_anfragen"] for f in _fassungen(arr_client, kim_kopf)}
    assert nachher["radarr-uhd"] is True


# --- Karten -----------------------------------------------------------------


@pytest.mark.anyio
async def test_eine_karte_traegt_jede_fassung_und_leitet_status_uhd_daraus_ab(
    arr_client, monkeypatch
) -> None:
    from app.models import Role
    from app.schemas_media import MediaItem
    from app.services import fassungsachsen
    from app.services.settings_service import load_settings, save_settings

    async def bibliothek(_settings: object, tier: str = "standard") -> dict:
        return (
            {603: LibraryEntry(arr_id=7, has_file=True, monitored=True)}
            if tier == "uhd"
            else {}
        )

    monkeypatch.setattr(library, "movie_library", bibliothek)

    with SessionLocal() as db:
        save_settings(db, UHD)
        admin = db.query(User).filter(User.role == Role.admin).first()
        assert admin is not None
        kacheln = [MediaItem(tmdb_id=603, media_type="movie", title="Matrix")]
        await fassungsachsen.anreichern(db, load_settings(db), "movie", kacheln, admin)

    karte = kacheln[0]
    assert [(f.kennung, f.haupt) for f in karte.fassungen] == [
        ("radarr-standard", True),
        ("radarr-uhd", False),
    ]
    assert karte.fassungen[0].status == karte.status
    assert karte.fassungen[1].status == "downloaded"
    # Die alte Achse ist genau diese Fassung - nicht mehr und nicht weniger.
    assert karte.status_uhd == karte.fassungen[1].status
    assert karte.fassungen[1].klasse == "uhd"


@pytest.mark.anyio
async def test_ohne_recht_traegt_die_karte_nur_die_hauptfassung(arr_client) -> None:
    from app.schemas_media import MediaItem
    from app.services import fassungsachsen
    from app.services.settings_service import load_settings, save_settings

    arr_client.put("/api/settings", json=UHD)
    create_user(arr_client, "kim")

    with SessionLocal() as db:
        save_settings(db, UHD)
        kim = db.query(User).filter(User.username == "kim").one()
        kacheln = [MediaItem(tmdb_id=603, media_type="movie", title="Matrix")]
        await fassungsachsen.anreichern(db, load_settings(db), "movie", kacheln, kim)

    assert [f.kennung for f in kacheln[0].fassungen] == ["radarr-standard"]
    assert kacheln[0].status_uhd is None


# --- Anfragen ---------------------------------------------------------------


def test_eine_anfrage_nennt_ihre_fassung(arr_client) -> None:
    arr_client.put("/api/settings", json=UHD)
    titel = _demo(arr_client)
    kopf = _kim_mit_4k(arr_client)

    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": titel["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
            "fassung": "radarr-uhd",
        },
        headers=kopf,
    )

    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["fassung"] == "radarr-uhd"
    # Die zugesagte Stufe bleibt - als Ableitung aus der Fassung.
    assert antwort.json()["tier"] == "uhd"
    with SessionLocal() as db:
        zeile = db.query(MediaRequest).filter(MediaRequest.tmdb_id == titel["tmdb_id"]).one()
        assert zeile.fassung_kennung == "radarr-uhd"


def test_die_alte_stufe_waehlt_weiter_dieselbe_fassung(arr_client) -> None:
    """``tier`` ist zugesagt (Bauplan Abschnitt 12) und muss wirken."""
    arr_client.put("/api/settings", json=UHD)

    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": _demo(arr_client)["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
            "tier": "uhd",
        },
        headers=_kim_mit_4k(arr_client),
    )

    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["fassung"] == "radarr-uhd"


def test_eine_fassung_der_falschen_medienart_wird_abgelehnt(arr_client) -> None:
    arr_client.put("/api/settings", json=UHD)

    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": _demo(arr_client)["tmdb_id"],
            "fassung": "sonarr-uhd",
        },
    )

    assert antwort.status_code == 422
    assert antwort.json()["detail"]["code"] == "fassung_unknown"


def test_eine_erfundene_fassung_wird_abgelehnt(arr_client) -> None:
    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": _demo(arr_client)["tmdb_id"],
            "fassung": "v_erfunden",
        },
    )

    assert antwort.status_code == 422
    assert antwort.json()["detail"]["code"] == "fassung_unknown"


# --- Rechte -----------------------------------------------------------------


def test_die_bewertung_nennt_die_schalter_je_fassung(arr_client) -> None:
    arr_client.put("/api/settings", json=UHD)

    antwort = arr_client.post(
        "/api/users/rechte/bewerten",
        json={
            "role": "user",
            "fassungen": [
                {"kennung": "radarr-uhd", "anfragen": True, "auto_freigabe": True}
            ],
        },
    )

    assert antwort.status_code == 200, antwort.text
    fassungen = antwort.json()["fassungen"]
    assert set(fassungen) == {"radarr-uhd", "sonarr-uhd"}
    assert fassungen["radarr-uhd"]["anfragen"] == {"frei": True, "wirkt": True, "grund": None}
    assert fassungen["radarr-uhd"]["auto"]["wirkt"] is True
    # Ohne das Recht zu fragen bringt die Sofort-Freigabe nichts - derselbe
    # Grund wie bisher bei 4K, nur je Fassung.
    assert fassungen["sonarr-uhd"]["auto"]["grund"] == "fassung_needs_permission"


def test_eine_offene_fassung_hat_keinen_schalter(arr_client) -> None:
    antwort = arr_client.post("/api/users/rechte/bewerten", json={"role": "user"})

    assert antwort.json()["fassungen"] == {}


def test_der_kontodialog_setzt_rechte_je_fassung(arr_client) -> None:
    arr_client.put("/api/settings", json=UHD)
    konto = create_user(arr_client, "kim")

    antwort = arr_client.patch(
        f"/api/users/{konto['id']}",
        json={
            "fassung_rechte": [
                {"kennung": "radarr-uhd", "anfragen": True, "auto_freigabe": False}
            ]
        },
    )

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["fassung_rechte"] == [
        {"kennung": "radarr-uhd", "anfragen": True, "auto_freigabe": False}
    ]
    # Und die alte Sicht sagt dasselbe, solange es sie gibt.
    assert antwort.json()["can_request_uhd_movies"] is True

    zurueck = arr_client.patch(
        f"/api/users/{konto['id']}",
        json={
            "fassung_rechte": [
                {"kennung": "radarr-uhd", "anfragen": False, "auto_freigabe": False}
            ]
        },
    )
    assert zurueck.json()["fassung_rechte"] == []


def test_eine_einladung_traegt_die_rechte_je_fassung(arr_client) -> None:
    from app.models import AuthToken, TokenPurpose

    arr_client.put("/api/settings", json=UHD)
    arr_client.put(
        "/api/settings",
        json={
            "public_url": "https://nexview.example.com",
            "smtp_host": "smtp.example.com",
            "smtp_from_address": "nexview@example.com",
        },
    )

    angelegt = arr_client.post(
        "/api/users/invitations",
        json={
            "email": "neu@example.com",
            "role": "user",
            "fassungen": [
                {"kennung": "radarr-uhd", "anfragen": True, "auto_freigabe": True}
            ],
        },
    )
    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["entfallen"] == []
    with SessionLocal() as db:
        token = (
            db.query(AuthToken)
            .filter(AuthToken.purpose == TokenPurpose.invitation)
            .order_by(AuthToken.id.desc())
            .first()
        )
        assert token is not None
        assert token.invite_fassung_rechte == [
            {"kennung": "radarr-uhd", "anfragen": True, "auto_freigabe": True}
        ]


# --- Staffeln und Folgen ----------------------------------------------------


def _serie_vortaeuschen(monkeypatch, tmdb_id: int) -> None:
    """Eine Serie mit zwei Staffeln und drei Folgen - die Demo-Daten haben keine."""
    from app.routers import details as details_router
    from app.schemas_media import EpisodeInfo, MediaDetail, SeasonDetail, SeasonInfo

    detail = MediaDetail(
        tmdb_id=tmdb_id,
        media_type="tv",
        title="Serie mit zwei Staffeln",
        tvdb_id=4242,
        seasons=[
            SeasonInfo(season_number=1, name="Staffel 1", episode_count=3),
            SeasonInfo(season_number=2, name="Staffel 2", episode_count=3),
        ],
    )

    async def _detail(_db, _settings, _art, _tmdb_id, **_rest):
        return detail.model_copy(deep=True)

    monkeypatch.setattr(details_router.media, "full_detail", _detail)

    async def _staffel(_db, _settings, _tmdb_id, nummer, **_rest):
        return SeasonDetail(
            season_number=nummer,
            name=f"Staffel {nummer}",
            episodes=[EpisodeInfo(episode_number=n, name=f"Folge {n}") for n in (1, 2, 3)],
        )

    monkeypatch.setattr(details_router.media, "detail", _detail)
    monkeypatch.setattr(details_router.media, "season_detail", _staffel)


def test_staffeln_tragen_jede_fassung_und_leiten_die_alten_felder_ab(
    arr_client, monkeypatch
) -> None:
    """Eine 4K-Anfrage auf Staffel 2 steht an ihrer Fassung - und nur dort."""
    arr_client.put("/api/settings", json=UHD)
    kopf = _kim_mit_4k(arr_client, "sonarr-uhd")
    tmdb_id = 456123
    _serie_vortaeuschen(monkeypatch, tmdb_id)

    angelegt = arr_client.post(
        "/api/requests",
        json={
            "media_type": "tv",
            "tmdb_id": tmdb_id,
            "season": 2,
            "quality_profile_id": 1,
            "fassung": "sonarr-uhd",
        },
        headers=kopf,
    )
    assert angelegt.status_code == 201, angelegt.text

    detail = arr_client.get(f"/api/detail/tv/{tmdb_id}")
    assert detail.status_code == 200, detail.text
    staffel = next(s for s in detail.json()["seasons"] if s["season_number"] == 2)

    je_fassung = {f["kennung"]: f for f in staffel["fassungen"]}
    assert list(je_fassung) == ["sonarr-standard", "sonarr-uhd"]
    assert je_fassung["sonarr-uhd"]["requested"] is True
    assert je_fassung["sonarr-standard"]["requested"] is False
    # Die alten Felder sind genau diese beiden Eintraege.
    assert staffel["requested"] is False
    assert staffel["requested_uhd"] is True


def test_folgen_tragen_jede_fassung(arr_client, monkeypatch) -> None:
    arr_client.put("/api/settings", json=UHD)
    kopf = _kim_mit_4k(arr_client, "sonarr-uhd")
    tmdb_id = 456124
    _serie_vortaeuschen(monkeypatch, tmdb_id)

    angelegt = arr_client.post(
        "/api/requests",
        json={
            "media_type": "tv",
            "tmdb_id": tmdb_id,
            "season": 1,
            "quality_profile_id": 1,
            "fassung": "sonarr-uhd",
        },
        headers=kopf,
    )
    assert angelegt.status_code == 201, angelegt.text

    antwort = arr_client.get(f"/api/detail/tv/{tmdb_id}/season/1")
    assert antwort.status_code == 200, antwort.text
    folgen = antwort.json()["episodes"]
    assert folgen, "die Staffel hat keine Folgen"
    je_fassung = {f["kennung"]: f for f in folgen[0]["fassungen"]}
    assert list(je_fassung) == ["sonarr-standard", "sonarr-uhd"]
    assert je_fassung["sonarr-uhd"]["requested"] is True
    assert folgen[0]["requested_uhd"] is True
    assert folgen[0]["requested"] is False
