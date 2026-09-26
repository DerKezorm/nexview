"""Ein falsch erkannter Ordner im Medienserver macht keinen fremden Titel "vorhanden".

Grosser Pruefgang, 25.09.2026: Jellyfin hielt den Ordner der Anime-Serie
"One Piece" (TMDB 37854, bei nexcrate mit Dateien) fuer die gleichnamige
Realserie (TMDB 111110). Nexview uebernahm die Kennung ungeprueft: Die
Realserie, von der keine Datei existierte, stand als "In der Bibliothek" da
und liess sich nicht anfragen.

⚠️ **Nur im NEX-Betrieb.** Im ARR-Betrieb bleibt der Treffer wie bisher.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import MediaServerLibraryItem, MediaType, StorageEntry, StorageState
from app.schemas_media import MediaItem
from app.services import requests_service
from app.services.beschaffung import ARR, NEX, normalize_title
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, KEY, SERIE_HD, URL, FakeNexcrate

GB = 1024**3
ANIME = 37854
REALSERIE = 111110


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    attrappe = FakeNexcrate()
    nex_client.use_transport(attrappe.transport())
    system.merken(attrappe._system())
    nex_fassungen.vergessen()
    try:
        yield attrappe
    finally:
        nex_client.use_transport(None)
        system.vergessen()
        nex_fassungen.vergessen()


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as sitzung:
        yield sitzung


@pytest.fixture
def nex(db: Session, nexcrate: FakeNexcrate) -> Any:
    save_settings(db, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY})
    nex_fassungen.schreiben(db, nexcrate.versions)
    db.commit()
    return load_settings(db, frisch=True)


#: Der Ordner, den Jellyfin fuer die Realserie hielt: Er traegt das Jahr des Anime.
ANIME_ORDNER = "/data/tv/One Piece (1999)"


def _im_server(
    db: Session,
    tmdb_id: int,
    titel: str,
    tvdb_id: int | None = None,
    jahr: int = 2023,
    *,
    pfad: str | None = None,
    art: MediaType = MediaType.tv,
    groesse: int = 0,
) -> None:
    db.add(
        MediaServerLibraryItem(
            provider="jellyfin",
            media_type=art,
            guid=f"jellyfin://{art.value}/{tmdb_id}",
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            title=titel,
            title_key=normalize_title(titel),
            year=jahr,
            file_paths=pfad,
            size_standard=groesse,
        )
    )
    db.commit()


def _bei_der_quelle(
    db: Session,
    tmdb_id: int,
    titel: str,
    kennung: str = SERIE_HD,
    *,
    art: MediaType = MediaType.tv,
    groesse: int = GB,
) -> None:
    """Ein Posten, den die Quelle fuehrt: Dort liegen Dateien dieses Titels."""
    db.add(
        StorageEntry(
            key=f"{art.value}:{kennung}:tmdb:{tmdb_id}" + (":s1" if art == MediaType.tv else ""),
            media_type=art,
            fassung_kennung=kennung,
            tmdb_id=tmdb_id,
            season=1 if art == MediaType.tv else None,
            title=titel,
            size_bytes=groesse,
            state=StorageState.house,
            arr_managed=True,
        )
    )
    db.commit()


def _serie(tmdb_id: int, titel: str) -> MediaItem:
    return MediaItem(media_type="tv", tmdb_id=tmdb_id, title=titel, release_date="2023-08-31")


async def _treffer(db: Session, settings: Any) -> set[int]:
    return await requests_service.im_medienserver(
        db, settings, "tv", [_serie(REALSERIE, "ONE PIECE"), _serie(ANIME, "One Piece")]
    )


async def test_der_falsch_erkannte_ordner_zaehlt_nicht_fuer_die_andere_serie(
    nex: Any, db: Session
) -> None:
    _im_server(db, REALSERIE, "ONE PIECE", tvdb_id=392276, pfad=ANIME_ORDNER)
    _bei_der_quelle(db, ANIME, "One Piece")

    assert await _treffer(db, nex) == set()


@pytest.mark.parametrize(
    ("pfad", "bleibt"),
    [
        ("/data/tv/One Piece [tmdbid-37854]", False),
        # Die Nummer im Ordner entscheidet allein, auch gegen ein fremdes Jahr.
        ("/data/tv/One Piece (1999) [tmdbid-111110]", True),
        ("/data/tv/ONE PIECE (2023)", True),
        # Kein Beleg im Ordner: Im Zweifel bleibt der Treffer.
        ("/data/tv/One Piece", True),
        (None, True),
    ],
)
async def test_nur_ein_beleg_im_ordner_macht_aus_dem_namen_eine_verwechslung(
    nex: Any, db: Session, pfad: str | None, bleibt: bool
) -> None:
    _im_server(db, REALSERIE, "ONE PIECE", tvdb_id=392276, pfad=pfad)
    _bei_der_quelle(db, ANIME, "One Piece")

    assert (REALSERIE in await _treffer(db, nex)) is bleibt


def _film(tmdb_id: int, jahr: int) -> MediaItem:
    return MediaItem(
        media_type="movie", tmdb_id=tmdb_id, title="The Grudge", release_date=f"{jahr}-10-22"
    )


@pytest.mark.parametrize("pfad", ["/data/movies/The Grudge (2004)/The Grudge (2004).mkv", None])
async def test_original_und_remake_gleichen_namens_bleiben_zwei_titel(
    nex: Any, db: Session, pfad: str | None
) -> None:
    """Pruefer, 26.09.2026: Der Server fuehrt das Original (500) richtig, nexcrate
    davon unabhaengig das gleichnamige Remake (600), das der Server nicht kennt.
    Der Name allein durfte das Original nicht aus der Bibliothek nehmen."""
    _im_server(db, 500, "The Grudge", jahr=2004, pfad=pfad, art=MediaType.movie, groesse=5 * GB)
    _bei_der_quelle(db, 600, "The Grudge", FILM_HD, art=MediaType.movie, groesse=7 * GB)

    treffer = await requests_service.im_medienserver(db, nex, "movie", [_film(500, 2004)])

    assert treffer == {500}


async def test_ein_film_mit_der_datei_der_quelle_ist_verwechselt(nex: Any, db: Session) -> None:
    """Bei Filmen belegt die Groesse: Es ist auf das Byte die Datei der Quelle."""
    _im_server(db, 500, "The Grudge", jahr=2004, art=MediaType.movie, groesse=7 * GB + 123)
    _bei_der_quelle(db, 600, "The Grudge", FILM_HD, art=MediaType.movie, groesse=7 * GB + 123)

    treffer = await requests_service.im_medienserver(db, nex, "movie", [_film(500, 2004)])

    assert treffer == set()


async def test_fuehrt_der_server_beide_ist_nichts_verwechselt(nex: Any, db: Session) -> None:
    _im_server(db, REALSERIE, "ONE PIECE", tvdb_id=392276, pfad=ANIME_ORDNER)
    _im_server(db, ANIME, "One Piece", tvdb_id=81797)
    _bei_der_quelle(db, ANIME, "One Piece")

    assert await _treffer(db, nex) == {REALSERIE, ANIME}


async def test_ohne_gleichnamigen_titel_der_quelle_bleibt_der_treffer(
    nex: Any, db: Session
) -> None:
    """Das ist der Sinn der Sache: eine Kopie, die nexcrate nicht fuehrt."""
    _im_server(db, REALSERIE, "ONE PIECE", tvdb_id=392276, pfad=ANIME_ORDNER)
    _bei_der_quelle(db, 1399, "Example Series")

    assert await _treffer(db, nex) == {REALSERIE}


async def test_im_arr_betrieb_bleibt_der_treffer(db: Session) -> None:
    save_settings(db, {"beschaffung": ARR})
    db.commit()
    settings = load_settings(db, frisch=True)
    _im_server(db, REALSERIE, "ONE PIECE", tvdb_id=392276, pfad=ANIME_ORDNER)
    _bei_der_quelle(db, ANIME, "One Piece", kennung="sonarr-standard")

    assert await _treffer(db, settings) == {REALSERIE}


def test_die_entdecken_seite_zeigt_die_andere_serie_nicht_als_vorhanden(
    admin_client: Any, nexcrate: FakeNexcrate
) -> None:
    """Derselbe Fall durch ``_status_for``, wie ihn der Pruefgang sah."""
    with SessionLocal() as sitzung:
        save_settings(
            sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY}
        )
        nex_fassungen.schreiben(sitzung, nexcrate.versions)
        sitzung.commit()
    admin_client.put(
        "/api/settings/fassungen",
        json=[{"kennung": k, "offen_fuer_alle": True} for k in (FILM_HD, SERIE_HD)],
    )
    erste, zweite = admin_client.get("/api/discover/tv").json()["items"][:2]
    with SessionLocal() as sitzung:
        # Der Server fuehrt den Ordner der zweiten Serie unter der Nummer der ersten.
        jahr = int((erste.get("release_date") or "2023")[:4])
        # Sein Ordner traegt ein Jahr, das nicht zur ersten Serie passt.
        _im_server(
            sitzung,
            erste["tmdb_id"],
            zweite["title"],
            jahr=jahr,
            pfad=f"/data/tv/{zweite['title']} ({jahr - 20})",
        )
        _bei_der_quelle(sitzung, zweite["tmdb_id"], zweite["title"])

    nach_nummer = {
        item["tmdb_id"]: item for item in admin_client.get("/api/discover/tv").json()["items"]
    }

    assert nach_nummer[erste["tmdb_id"]]["status"] == "not_requested"
