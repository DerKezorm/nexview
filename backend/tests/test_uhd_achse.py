"""Die 4K-Achse: Wann ist eine 4K-Datei wirklich eine zweite Fassung?

Der Rueckfall auf den Media-Server war der einzige Teil der Achse ohne Test -
und genau dort steckte ein Fehler, der im Betrieb aufgefallen ist: Ein
2160p-Remux in der **Standard**-Instanz liess den Titel als "in 4K vorhanden"
erscheinen, obwohl die 4K-Instanz leer war. In einer echten Bibliothek traf das
auf 33 von 33 Filmen zu, bei denen der Rueckfall ansprang.

Der Schaden war nicht nur ein schiefes Abzeichen: ``status_uhd`` steuert, ob die
4K-Anfrage ueberhaupt anklickbar ist.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import MediaServerLibraryItem, MediaType, Role, User
from app.schemas_media import MediaItem
from app.services import library, uhd
from app.services.radarr import LibraryEntry
from app.services.settings_service import load_settings, save_settings

# **Beide** Instanzen eingetragen. Die Standard-Instanz gehoert dazu, auch wenn
# es hier um die 4K-Achse geht: Ohne sie kann Nexview nicht feststellen, ob eine
# 4K-Datei bloss die Datei der Standard-Instanz ist - und bleibt dann bewusst
# grosszuegig (siehe ``uhd._echte_zweitfassungen``).
UHD_INSTANZ = {
    "radarr_url": "http://127.0.0.1:10",
    "radarr_api_key": "standard-schluessel",
    "radarr_uhd_url": "http://127.0.0.1:11",
    "radarr_uhd_api_key": "uhd-schluessel",
}


def _kachel(tmdb_id: int, titel: str) -> MediaItem:
    return MediaItem(
        tmdb_id=tmdb_id,
        media_type="movie",
        title=titel,
        release_date="1999-03-31",
    )


def _in_plex(db: Session, *, tmdb_id: int, titel: str, standard: bool, vierk: bool) -> None:
    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.movie,
            guid=f"plex://film/{tmdb_id}",
            title=titel,
            title_key=titel.lower(),
            tmdb_id=tmdb_id,
            year=1999,
            has_standard=standard,
            has_uhd=vierk,
        )
    )
    db.commit()


def _instanzen(
    monkeypatch,
    *,
    standard: dict[int, LibraryEntry],
    vierk: dict[int, LibraryEntry],
) -> None:
    """Zwei Radarr-Instanzen vortaeuschen - getrennt nach Stufe."""

    async def bibliothek(_settings: object, tier: str = "standard") -> dict:
        return vierk if tier == "uhd" else standard

    monkeypatch.setattr(library, "movie_library", bibliothek)


def _admin(db: Session) -> User:
    benutzer = User(
        username="chefin",
        email="chefin@beispiel.de",
        password_hash="x",
        role=Role.admin,
    )
    db.add(benutzer)
    db.commit()
    return benutzer


async def _achse(db: Session, items: list[MediaItem], benutzer: User) -> None:
    await uhd.anreichern(db, load_settings(db), "movie", items, benutzer)


# --- Der gemeldete Fehler --------------------------------------------------


@pytest.mark.anyio
async def test_4k_datei_in_der_standard_instanz_ist_keine_zweitfassung(monkeypatch):
    """Ein 2160p-Remux im normalen Radarr macht den Titel nicht 4K-vorhanden.

    Plex misst die Aufloesung der Datei, nicht die Instanz. Es ist **eine**
    Datei - und sie liegt dort, wo die Standard-Achse sie ohnehin meldet.
    """
    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = _admin(db)
        _in_plex(db, tmdb_id=603, titel="Matrix", standard=False, vierk=True)
        _instanzen(
            monkeypatch,
            standard={603: LibraryEntry(arr_id=1, has_file=True, monitored=True)},
            vierk={},  # die 4K-Instanz ist leer
        )

        kacheln = [_kachel(603, "Matrix")]
        await _achse(db, kacheln, benutzer)

    assert kacheln[0].status_uhd == "not_requested"


@pytest.mark.anyio
async def test_nur_im_media_server_bleibt_als_4k_erkannt(monkeypatch):
    """Der Fall, fuer den der Rueckfall gebaut wurde, muss weiter greifen.

    Wer einen Film aus Radarr wirft, sobald die Wunschqualitaet erreicht ist,
    hat ihn nur noch in Plex. Dann ist der Media-Server der einzige Zeuge.
    """
    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = _admin(db)
        _in_plex(db, tmdb_id=603, titel="Matrix", standard=False, vierk=True)
        _instanzen(monkeypatch, standard={}, vierk={})  # in keiner Instanz

        kacheln = [_kachel(603, "Matrix")]
        await _achse(db, kacheln, benutzer)

    assert kacheln[0].status_uhd == "in_library"


@pytest.mark.anyio
async def test_zwei_dateien_in_plex_zaehlen_als_zweitfassung(monkeypatch):
    """1080p **und** 4K im Media-Server sind wirklich zwei Dateien.

    Die Abgrenzung zum ersten Test: Dort meldete Plex eine einzige Datei. Hier
    meldet es zwei - dann ist die 4K-Fassung eine eigene, auch wenn die
    Standard-Instanz den Titel fuehrt.
    """
    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = _admin(db)
        _in_plex(db, tmdb_id=603, titel="Matrix", standard=True, vierk=True)
        _instanzen(
            monkeypatch,
            standard={603: LibraryEntry(arr_id=1, has_file=True, monitored=True)},
            vierk={},
        )

        kacheln = [_kachel(603, "Matrix")]
        await _achse(db, kacheln, benutzer)

    assert kacheln[0].status_uhd == "in_library"


@pytest.mark.anyio
async def test_die_4k_instanz_selbst_zaehlt_immer(monkeypatch):
    """Was in der 4K-Instanz liegt, braucht keinen Media-Server."""
    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = _admin(db)
        _instanzen(
            monkeypatch,
            standard={},
            vierk={603: LibraryEntry(arr_id=7, has_file=True, monitored=True)},
        )

        kacheln = [_kachel(603, "Matrix")]
        await _achse(db, kacheln, benutzer)

    assert kacheln[0].status_uhd == "downloaded"


# --- Der Ablageort der 4K-Fassung ------------------------------------------


@pytest.mark.anyio
async def test_pfad_der_4k_fassung_kommt_aus_der_4k_instanz(monkeypatch):
    """Beide Pfade nebeneinander - und keiner an der falschen Stelle.

    Der eigentliche Fallstrick: Die Kopien, mit denen die 4K-Achse abgleicht,
    tragen den Pfad der *Standard*-Fassung mit. Ohne Filter stuende er bei
    jedem Film als vermeintlicher 4K-Ablageort da.
    """
    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = _admin(db)
        _instanzen(
            monkeypatch,
            standard={603: LibraryEntry(arr_id=1, has_file=True, monitored=True)},
            vierk={
                603: LibraryEntry(
                    arr_id=7,
                    has_file=True,
                    monitored=True,
                    path="/data/Movies-4K/Matrix/matrix.2160p.mkv",
                )
            },
        )

        kachel = _kachel(603, "Matrix")
        kachel.path = "/data/Movies/Matrix/matrix.1080p.mkv"
        await _achse(db, [kachel], benutzer)

    assert kachel.path == "/data/Movies/Matrix/matrix.1080p.mkv"
    assert kachel.path_uhd == "/data/Movies-4K/Matrix/matrix.2160p.mkv"


@pytest.mark.anyio
async def test_ohne_4k_fassung_bleibt_der_zweite_pfad_leer(monkeypatch):
    """Kein Eintrag in der 4K-Instanz heisst: kein zweiter Pfad.

    Ohne diese Pruefung wuerde der 1080p-Pfad aus der Kopie durchgereicht -
    und die Detailseite behauptete zwei Dateien, wo eine liegt.
    """
    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = _admin(db)
        _instanzen(
            monkeypatch,
            standard={603: LibraryEntry(arr_id=1, has_file=True, monitored=True)},
            vierk={},
        )

        kachel = _kachel(603, "Matrix")
        kachel.path = "/data/Movies/Matrix/matrix.1080p.mkv"
        await _achse(db, [kachel], benutzer)

    assert kachel.path_uhd is None


@pytest.mark.anyio
async def test_gewoehnlicher_benutzer_bekommt_keinen_pfad(monkeypatch):
    """Serverpfade gehen nur an Administratoren - auch auf der 4K-Achse."""
    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = User(
            username="gast",
            email="gast@beispiel.de",
            password_hash="x",
            role=Role.user,
            can_request_uhd_movies=True,
        )
        db.add(benutzer)
        db.commit()
        _instanzen(
            monkeypatch,
            standard={},
            vierk={
                603: LibraryEntry(
                    arr_id=7,
                    has_file=True,
                    monitored=True,
                    path="/data/Movies-4K/Matrix/matrix.2160p.mkv",
                )
            },
        )

        kachel = _kachel(603, "Matrix")
        await _achse(db, [kachel], benutzer)

    assert kachel.status_uhd == "downloaded"
    assert kachel.path_uhd is None


# --- Die Sperre beim Anfragen ----------------------------------------------


@pytest.mark.anyio
async def test_4k_anfrage_wird_nicht_vom_standard_remux_blockiert(monkeypatch):
    """Abzeichen und Sperre muessen dasselbe sagen.

    Der zweite Teil des gemeldeten Fehlers: Nachdem das Abzeichen wieder
    "4K noch nicht angefragt" zeigte, wies der Dienst die Anfrage trotzdem mit
    "liegt bereits auf dem Media-Server" ab - dieselbe Verwechslung von
    Aufloesung und Instanz, nur an anderer Stelle. Ein Widerspruch zwischen
    dem, was die Seite anbietet, und dem, was der Server annimmt, ist schlimmer
    als beide Fehler einzeln: Man sieht einen Knopf, der nicht funktioniert.
    """
    from app.models import QualityTier
    from app.services import requests_service

    async def optionen(_settings: object, _media_type: str, _tier: str = "standard") -> dict:
        return {
            "quality_profiles": [{"id": 1, "name": "HD-1080p"}, {"id": 2, "name": "SD-576p"}],
            "root_folders": [{"path": "/data/Movies", "free_space": 1_000_000_000}],
        }

    monkeypatch.setattr(library, "options", optionen)

    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = User(
            username="kim",
            email="kim@beispiel.de",
            password_hash="x",
            role=Role.user,
            can_request_uhd_movies=True,
        )
        db.add(benutzer)
        db.commit()

        _in_plex(db, tmdb_id=603, titel="Matrix", standard=False, vierk=True)
        _instanzen(
            monkeypatch,
            standard={603: LibraryEntry(arr_id=1, has_file=True, monitored=True)},
            vierk={},
        )

        anfrage = await requests_service.create_request(
            db,
            load_settings(db),
            benutzer,
            _kachel(603, "Matrix"),
            quality_profile_id=1,
            root_folder_path="/data/Movies",
            tier=QualityTier.uhd,
        )

    assert anfrage.tier == QualityTier.uhd


@pytest.mark.anyio
async def test_echte_4k_kopie_blockiert_die_anfrage_weiterhin(monkeypatch):
    """Die Gegenprobe - die Sperre darf nicht einfach verschwunden sein.

    Liegt die 4K-Datei in **keiner** Instanz, ist der Media-Server der einzige
    Zeuge, und die Anfrage waere ein zweiter Download derselben Datei. Ohne
    diesen Test koennte die Regel oben versehentlich alles durchlassen, und es
    fiele erst auf, wenn die Platte voll ist.
    """
    from app.models import QualityTier
    from app.services import requests_service

    async def optionen(_settings: object, _media_type: str, _tier: str = "standard") -> dict:
        return {
            "quality_profiles": [{"id": 1, "name": "HD-1080p"}],
            "root_folders": [{"path": "/data/Movies", "free_space": 1_000_000_000}],
        }

    monkeypatch.setattr(library, "options", optionen)

    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = User(
            username="kim",
            email="kim@beispiel.de",
            password_hash="x",
            role=Role.user,
            can_request_uhd_movies=True,
        )
        db.add(benutzer)
        db.commit()

        _in_plex(db, tmdb_id=603, titel="Matrix", standard=False, vierk=True)
        _instanzen(monkeypatch, standard={}, vierk={})  # in keiner Instanz

        with pytest.raises(requests_service.RequestError) as fehler:
            await requests_service.create_request(
                db,
                load_settings(db),
                benutzer,
                _kachel(603, "Matrix"),
                quality_profile_id=1,
                root_folder_path="/data/Movies",
                tier=QualityTier.uhd,
            )

    assert "Media-Server" in str(fehler.value)


@pytest.mark.anyio
async def test_hinweis_wenn_die_4k_datei_im_standard_ordner_liegt(monkeypatch):
    """Erlauben, aber sagen, was passiert.

    Der Titel laesst sich in 4K anfragen - das ist die Entscheidung. Nur soll
    niemand versehentlich eine **zweite** 4K-Datei anlegen, ohne zu wissen,
    dass schon eine im normalen Radarr liegt.
    """
    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = _admin(db)
        _in_plex(db, tmdb_id=603, titel="Matrix", standard=False, vierk=True)
        _instanzen(
            monkeypatch,
            standard={603: LibraryEntry(arr_id=1, has_file=True, monitored=True)},
            vierk={},
        )

        kachel = _kachel(603, "Matrix")
        await _achse(db, [kachel], benutzer)

    assert kachel.status_uhd == "not_requested"  # anfragbar bleibt es
    assert kachel.uhd_in_standard is True  # aber mit Hinweis


@pytest.mark.anyio
async def test_kein_hinweis_ohne_4k_datei(monkeypatch):
    """Ein gewoehnlicher 1080p-Film loest keinen Hinweis aus."""
    with SessionLocal() as db:
        save_settings(db, UHD_INSTANZ)
        benutzer = _admin(db)
        _in_plex(db, tmdb_id=603, titel="Matrix", standard=True, vierk=False)
        _instanzen(
            monkeypatch,
            standard={603: LibraryEntry(arr_id=1, has_file=True, monitored=True)},
            vierk={},
        )

        kachel = _kachel(603, "Matrix")
        await _achse(db, [kachel], benutzer)

    assert kachel.uhd_in_standard is False
