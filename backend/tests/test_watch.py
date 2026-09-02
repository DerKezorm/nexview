"""„Sag mir Bescheid" - vormerken, beenden und der Waechter dahinter.

Der Kern, den die Tests festhalten: Beim **ersten** Zusammentreffen mit einer
Staffel darf nichts gemeldet werden, sonst bekaeme jeder beim Vormerken sofort
eine Nachricht ueber zwanzig Folgen, die laengst dalagen. Und was gemeldet
wird, ist **eine** Nachricht je Runde, nicht eine je Folge.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaType,
    Notification,
    NotificationType,
    Role,
    SeasonProgress,
    TitleWatch,
    User,
)
from app.services import watch

from .conftest import auth_headers, create_user

# --- Die Bündelung ----------------------------------------------------------


@pytest.mark.parametrize(
    ("nummern", "erwartet"),
    [
        ({9}, "9"),
        ({1, 2, 3}, "1-3"),
        ({1, 2, 3, 7}, "1-3, 7"),
        ({4, 5, 9, 10, 11}, "4-5, 9-11"),
        (set(), ""),
    ],
)
def test_spanne_zieht_zusammen(nummern: set[int], erwartet: str) -> None:
    """Acht Folgen einzeln aufzuzaehlen ergibt eine Meldung, die niemand liest."""
    assert watch._spanne(nummern) == erwartet


def test_nummern_hin_und_zurueck() -> None:
    assert watch._nummern(watch._als_text({7, 1, 3})) == {1, 3, 7}
    assert watch._nummern("") == set()


# --- Vormerken über die API -------------------------------------------------


@pytest.fixture()
def nutzer(admin_client: TestClient) -> dict[str, str]:
    create_user(admin_client, "wartender")
    return auth_headers(admin_client, "wartender", "passwort-1234")


def test_vormerken_und_beenden(admin_client: TestClient, nutzer: dict[str, str]) -> None:
    antwort = admin_client.put(
        "/api/watch/movie/603", json={"title": "The Matrix"}, headers=nutzer
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["watching"] is True

    liste = admin_client.get("/api/watch", headers=nutzer).json()
    assert [(e["media_type"], e["tmdb_id"]) for e in liste] == [("movie", 603)]

    assert admin_client.delete("/api/watch/movie/603", headers=nutzer).json() == {
        "watching": False
    }
    assert admin_client.get("/api/watch", headers=nutzer).json() == []


def test_zweimal_vormerken_ist_ein_doppelklick(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Kein Fehler, und hinterher liegt genau eine Zeile da."""
    for _ in range(2):
        admin_client.put("/api/watch/tv/1399", json={"title": "GoT"}, headers=nutzer)
    assert len(admin_client.get("/api/watch", headers=nutzer).json()) == 1


def test_beenden_ohne_vormerkung_ist_kein_fehler(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Das Ziel ist „ich will davon nichts mehr hoeren" - das ist danach erfuellt."""
    assert admin_client.delete("/api/watch/movie/1", headers=nutzer).status_code == 200


def test_kind_kann_nichts_vormerken(admin_client: TestClient) -> None:
    create_user(admin_client, "kindwatch", role=Role.child)
    kopf = auth_headers(admin_client, "kindwatch", "passwort-1234")
    assert admin_client.get("/api/watch", headers=kopf).status_code == 403


def test_vormerkung_verschwindet_mit_dem_konto(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    admin_client.put("/api/watch/movie/603", json={"title": "x"}, headers=nutzer)
    with SessionLocal() as db:
        db.delete(db.query(User).filter(User.username == "wartender").one())
        db.commit()
        assert db.query(TitleWatch).count() == 0


# --- Der Wächter ------------------------------------------------------------


async def _serie_pruefen(db, vorhanden: dict[int, set[int]], titel: str = "Andor"):
    """Den Serienzweig fahren, ohne Sonarr und ohne TMDB.

    Beide Auskuenfte werden ersetzt: die Detailabfrage (fuer die TVDB-Kennung)
    und der Folgenstand. Was hier geprueft wird, ist der Vergleich mit dem
    Vorlauf - nicht, ob Sonarr antwortet.
    """
    from app.services import library, media
    from app.services.settings_service import load_settings

    class _Detail:
        tvdb_id = 1
        title = titel
        release_date = "2022-09-21"

    echt_detail, echt_folgen = media.full_detail, library.episode_availability

    async def falsches_detail(*_args, **_kwargs):
        return _Detail()

    async def falsche_folgen(*_args, **_kwargs):
        return vorhanden

    media.full_detail = falsches_detail
    library.episode_availability = falsche_folgen
    try:
        return await watch.pruefen(db, load_settings(db))
    finally:
        media.full_detail = echt_detail
        library.episode_availability = echt_folgen


@pytest.mark.anyio
async def test_erste_begegnung_meldet_nichts(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Beim Vormerken darf keine Nachricht ueber laengst Vorhandenes kommen."""
    admin_client.put("/api/watch/tv/200", json={"title": "Andor"}, headers=nutzer)

    with SessionLocal() as db:
        gemeldet = await _serie_pruefen(db, {1: {1, 2, 3}})
        assert gemeldet == 0
        # Der Stand ist trotzdem festgehalten - sonst gaebe es beim naechsten
        # Durchgang nichts zu vergleichen.
        zeile = db.query(SeasonProgress).filter(SeasonProgress.tmdb_id == 200).one()
        assert watch._nummern(zeile.episodes) == {1, 2, 3}


@pytest.mark.anyio
async def test_neue_folgen_ergeben_eine_meldung(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Fuenf neue Folgen sind eine Nachricht, nicht fuenf."""
    admin_client.put("/api/watch/tv/201", json={"title": "Andor"}, headers=nutzer)

    with SessionLocal() as db:
        await _serie_pruefen(db, {1: {1}})
        gemeldet = await _serie_pruefen(db, {1: {1, 2, 3, 4, 5, 6}})
        assert gemeldet == 1

        meldungen = (
            db.query(Notification)
            .filter(Notification.type == NotificationType.watch_episodes)
            .all()
        )
        assert len(meldungen) == 1
        assert "S1: 2-6" in meldungen[0].message_title


@pytest.mark.anyio
async def test_ohne_aenderung_keine_meldung(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    admin_client.put("/api/watch/tv/202", json={"title": "Andor"}, headers=nutzer)
    with SessionLocal() as db:
        await _serie_pruefen(db, {1: {1, 2}})
        assert await _serie_pruefen(db, {1: {1, 2}}) == 0


@pytest.mark.anyio
async def test_serie_bleibt_vorgemerkt(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Eine Serie verfolgt man, bis man aufhoert - anders als beim Film."""
    admin_client.put("/api/watch/tv/203", json={"title": "Andor"}, headers=nutzer)
    with SessionLocal() as db:
        await _serie_pruefen(db, {1: {1}})
        await _serie_pruefen(db, {1: {1, 2}})
        assert (
            db.query(TitleWatch)
            .filter(TitleWatch.media_type == MediaType.tv, TitleWatch.tmdb_id == 203)
            .count()
            == 1
        )
