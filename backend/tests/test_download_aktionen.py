"""Die Knoepfe an einem haengenden Download - was wirklich an Radarr und Sonarr geht."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import DownloadHaenger, DownloadVerlauf, User
from app.services import download_aktionen, download_haenger
from app.services.arr import ArrError
from app.services.download_aktionen import DownloadFehler
from app.services.download_gruende import Aktion
from app.services.settings_service import load_settings

from .download_attrappe import RADARR_HOST, SONARR_HOST, ArrAttrappe, einrichten, film, folge

T0 = datetime(2026, 9, 12, 20, 0)

LOESCHEN_OHNE_SPERRE = {
    "removeFromClient": "true",
    "blocklist": "false",
    "skipRedownload": "true",
    "changeCategory": "false",
}


@pytest.fixture
def arr(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ArrAttrappe:
    monkeypatch.setattr(download_aktionen, "ABWARTEN_TAKT", 0)
    return einrichten(admin_client, monkeypatch)


def _merken(kennung: str, zeilen: list[dict]) -> int:
    """Abgleichen und als haengend markieren - der Stand, an dem jemand einen Knopf drueckt."""
    with SessionLocal() as db:
        instanz = next(i for i in load_settings(db).arr_instanzen() if i.kennung == kennung)
        download_haenger.abgleichen(
            db, instanz, download_haenger.zusammenfassen(instanz, zeilen), T0
        )
        db.commit()
        zeile = db.scalars(select(DownloadHaenger).where(DownloadHaenger.kennung == kennung)).one()
        zeile.haengt_seit = T0
        db.commit()
        return zeile.id


def _admin() -> User:
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.username == "admin"))


def _noch_da(zeile_id: int) -> bool:
    with SessionLocal() as db:
        return db.get(DownloadHaenger, zeile_id) is not None


def _verlauf() -> list[DownloadVerlauf]:
    with SessionLocal() as db:
        return list(db.scalars(select(DownloadVerlauf).order_by(DownloadVerlauf.id)))


# --- Entfernen ----------------------------------------------------------------------


async def test_nur_entfernen_sperrt_nicht_und_sucht_nicht(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])

    with SessionLocal() as db:
        ergebnis = await download_aktionen.entfernen(
            db, load_settings(db), zeile_id, neu_suchen=False, wer=_admin()
        )

    assert ergebnis.gesucht is False
    (loeschen,) = arr.gesendet("DELETE", "/queue")
    assert (loeschen.host, loeschen.pfad) == (RADARR_HOST, "/queue/11")
    assert loeschen.params == LOESCHEN_OHNE_SPERRE
    assert arr.gesendet("POST", "/command") == []
    assert not _noch_da(zeile_id)
    (eintrag,) = _verlauf()
    assert (eintrag.was, eintrag.automatisch, eintrag.ergebnis) == ("entfernen", False, "")
    assert eintrag.user_id == _admin().id
    assert eintrag.titel == "Beispielfilm"


async def test_entfernen_und_neu_suchen_sperrt_und_sucht_den_film(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])

    with SessionLocal() as db:
        ergebnis = await download_aktionen.entfernen(
            db, load_settings(db), zeile_id, neu_suchen=True, wer=_admin()
        )

    assert ergebnis.gesucht is True
    (loeschen,) = arr.gesendet("DELETE", "/queue")
    assert loeschen.params == {**LOESCHEN_OHNE_SPERRE, "blocklist": "true"}
    (suche,) = arr.gesendet("POST", "/command")
    assert suche.koerper == {"name": "MoviesSearch", "movieIds": [5]}
    assert [v.was for v in _verlauf()] == ["entfernen_neu_suchen"]


async def test_ein_staffelpaket_geht_gesammelt_und_sucht_die_staffel(arr: ArrAttrappe) -> None:
    zeilen = [
        folge(901, folge_nummer=1, folge_id=701),
        folge(902, folge_nummer=2, folge_id=702),
        folge(903, folge_nummer=3, folge_id=703),
    ]
    arr.warteschlange[SONARR_HOST] = zeilen
    zeile_id = _merken("sonarr-standard", zeilen)

    with SessionLocal() as db:
        await download_aktionen.entfernen(
            db, load_settings(db), zeile_id, neu_suchen=True, wer=_admin()
        )

    (loeschen,) = arr.gesendet("DELETE", "/queue")
    assert (loeschen.host, loeschen.pfad) == (SONARR_HOST, "/queue/bulk")
    assert loeschen.koerper == {"ids": [901, 902, 903]}
    assert loeschen.params["blocklist"] == "true"
    (suche,) = arr.gesendet("POST", "/command")
    assert suche.koerper == {"name": "SeasonSearch", "seriesId": 7, "seasonNumber": 1}


async def test_eine_einzelne_folge_wird_einzeln_gesucht(arr: ArrAttrappe) -> None:
    zeilen = [folge(901, folge_nummer=4, folge_id=704)]
    arr.warteschlange[SONARR_HOST] = zeilen
    zeile_id = _merken("sonarr-standard", zeilen)

    with SessionLocal() as db:
        await download_aktionen.entfernen(
            db, load_settings(db), zeile_id, neu_suchen=True, wer=_admin()
        )

    assert arr.gesendet("DELETE", "/queue")[0].pfad == "/queue/901"
    (suche,) = arr.gesendet("POST", "/command")
    assert suche.koerper == {"name": "EpisodeSearch", "episodeIds": [704]}


async def test_ist_der_download_weg_wird_nichts_entfernt(arr: ArrAttrappe) -> None:
    zeile_id = _merken("radarr-standard", [film()])
    arr.warteschlange[RADARR_HOST] = []

    with SessionLocal() as db, pytest.raises(DownloadFehler) as fehler:
        await download_aktionen.entfernen(
            db, load_settings(db), zeile_id, neu_suchen=True, wer=_admin()
        )

    assert fehler.value.code == "download_gone"
    assert fehler.value.zahlen == {"titel": "Beispielfilm"}
    assert arr.gesendet("DELETE") == []
    assert not _noch_da(zeile_id)


async def test_es_zaehlen_die_zeilennummern_von_jetzt(arr: ArrAttrappe) -> None:
    """Gemerkt war Zeile 11, inzwischen fuehrt Radarr denselben Download unter 12."""
    zeile_id = _merken("radarr-standard", [film(11)])
    arr.warteschlange[RADARR_HOST] = [film(12)]

    with SessionLocal() as db:
        await download_aktionen.entfernen(
            db, load_settings(db), zeile_id, neu_suchen=False, wer=_admin()
        )

    assert arr.gesendet("DELETE", "/queue")[0].pfad == "/queue/12"


async def test_lehnt_die_instanz_ab_bleibt_die_zeile(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])
    arr.fehler[("DELETE", "/queue")] = 500

    with SessionLocal() as db, pytest.raises(ArrError):
        await download_aktionen.entfernen(
            db, load_settings(db), zeile_id, neu_suchen=True, wer=_admin()
        )

    assert _noch_da(zeile_id)
    assert arr.gesendet("POST", "/command") == []
    (eintrag,) = _verlauf()
    assert (eintrag.was, eintrag.ergebnis) == ("entfernen_neu_suchen", "arr_http_error")


async def test_scheitert_die_suche_ist_der_download_trotzdem_weg(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])
    arr.fehler[("POST", "/command")] = 500

    with SessionLocal() as db:
        ergebnis = await download_aktionen.entfernen(
            db, load_settings(db), zeile_id, neu_suchen=True, wer=_admin()
        )

    assert ergebnis.gesucht is False
    assert not _noch_da(zeile_id)
    assert _verlauf()[0].ergebnis == "search_failed"


async def test_nach_dem_entfernen_verschwindet_der_hinweis_an_der_anfrage(
    arr: ArrAttrappe, admin_client: TestClient
) -> None:
    """⚠️ Die Sitzung schreibt nicht von selbst vor einer Abfrage (``autoflush`` aus).

    Ohne ausdrueckliches Wegschreiben saehe das Markieren die gerade entfernte
    Zeile noch, und die Anfrage behielte "Import haengt" bis zur naechsten Runde.
    """
    from app.models import MediaRequest, MediaType, RequestStatus

    from .conftest import create_user

    besitzer = create_user(admin_client, "kim")
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])
    with SessionLocal() as db:
        anfrage = MediaRequest(
            user_id=besitzer["id"], media_type=MediaType.movie, fassung_kennung="radarr-standard",
            tmdb_id=4711, title="Beispielfilm", status=RequestStatus.searching, arr_id=5,
        )
        db.add(anfrage)
        db.commit()
        download_haenger.anfragen_markieren(db, load_settings(db))
        db.commit()
        assert anfrage.import_haengt == "sample"
        anfrage_id = anfrage.id

    with SessionLocal() as db:
        await download_aktionen.entfernen(
            db, load_settings(db), zeile_id, neu_suchen=False, wer=_admin()
        )

    with SessionLocal() as db:
        assert db.get(MediaRequest, anfrage_id).import_haengt is None


async def test_eine_unbekannte_zeile(arr: ArrAttrappe) -> None:
    with SessionLocal() as db, pytest.raises(DownloadFehler) as fehler:
        await download_aktionen.entfernen(db, load_settings(db), 999, neu_suchen=False, wer=None)
    assert (fehler.value.code, fehler.value.status_code) == ("download_not_found", 404)


# --- Erneut pruefen -------------------------------------------------------------------


async def test_erneut_pruefen_stoesst_die_verarbeitung_an(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])

    with SessionLocal() as db:
        ergebnis = await download_aktionen.erneut_pruefen(
            db, load_settings(db), zeile_id, wer=_admin()
        )

    (befehl,) = arr.gesendet("POST", "/command")
    assert befehl.koerper == {"name": "RefreshMonitoredDownloads"}
    assert ergebnis.befehl == "queued"
    # Ob es geholfen hat, sagt erst der naechste Abgleich.
    assert _noch_da(zeile_id)
    assert [v.was for v in _verlauf()] == ["erneut_pruefen"]


# --- Manueller Import -------------------------------------------------------------------

PFAD = "/downloads/Beispielfilm.2010/Beispielfilm.2010.mkv"


def _radarr_kandidat(
    pfad: str = PFAD, *, film_id: int | None = 5, ablehnungen: tuple = ()
) -> dict:
    return {
        "id": 1,
        "path": pfad,
        "relativePath": pfad.rsplit("/", 1)[-1],
        "folderName": "Beispielfilm.2010",
        "name": "Beispielfilm.2010",
        "size": 1000,
        "movie": {"id": film_id, "title": "Beispielfilm", "year": 2010} if film_id else None,
        "quality": {"quality": {"id": 7, "name": "Bluray-1080p"}, "revision": {"version": 1}},
        "languages": [{"id": 1, "name": "English"}],
        "releaseGroup": "GRP",
        "indexerFlags": 0,
        "downloadId": "D1",
        "rejections": [{"reason": text, "type": art} for text, art in ablehnungen],
    }


async def test_die_kandidaten_tragen_zuordnung_und_einwaende(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])
    arr.kandidaten["D1"] = [
        _radarr_kandidat(
            ablehnungen=(("Sample", "permanent"), ("Locked file, try again later", "temporary"))
        ),
        _radarr_kandidat("/downloads/x/unbekannt.mkv", film_id=None),
    ]

    with SessionLocal() as db:
        erste, zweite = await download_aktionen.import_kandidaten(db, load_settings(db), zeile_id)

    assert erste.zuordnung == "Beispielfilm (2010)"
    assert erste.zuordenbar is True
    assert erste.qualitaet == "Bluray-1080p"
    assert erste.sprachen == ("English",)
    assert [(a.text, a.dauerhaft) for a in erste.ablehnungen] == [
        ("Sample", True),
        ("Locked file, try again later", False),
    ]
    assert zweite.zuordenbar is False
    (abfrage,) = arr.gesendet("GET", "/manualimport")
    assert abfrage.params == {"downloadId": "D1", "filterExistingFiles": "false"}


async def test_eine_datei_mit_einwand_braucht_ein_trotzdem(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])
    arr.kandidaten["D1"] = [_radarr_kandidat(ablehnungen=(("Sample", "permanent"),))]

    with SessionLocal() as db, pytest.raises(DownloadFehler) as fehler:
        await download_aktionen.importieren(
            db, load_settings(db), zeile_id, [PFAD], trotzdem=False, wer=_admin()
        )
    assert fehler.value.code == "download_import_needs_confirmation"
    assert arr.gesendet("POST", "/command") == []

    with SessionLocal() as db:
        ergebnis = await download_aktionen.importieren(
            db, load_settings(db), zeile_id, [PFAD], trotzdem=True, wer=_admin()
        )

    (befehl,) = arr.gesendet("POST", "/command")
    assert befehl.koerper["name"] == "ManualImport"
    assert befehl.koerper["importMode"] == "auto"
    # Alles ausser dem Pfad kommt aus der Antwort der Instanz, nicht vom Browser.
    assert befehl.koerper["files"] == [
        {
            "path": PFAD,
            "folderName": "Beispielfilm.2010",
            "quality": {"quality": {"id": 7, "name": "Bluray-1080p"}, "revision": {"version": 1}},
            "languages": [{"id": 1, "name": "English"}],
            "releaseGroup": "GRP",
            "indexerFlags": 0,
            "downloadId": "D1",
            "movieId": 5,
        }
    ]
    # Der Befehl wurde bis zum Ende begleitet.
    assert arr.gesendet("GET", "/command/")
    assert ergebnis.befehl == "completed"
    assert [(v.was, v.ergebnis) for v in _verlauf()] == [("manuell_importieren", "")]


async def test_fremde_oder_unzugeordnete_dateien_werden_nicht_importiert(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])
    arr.kandidaten["D1"] = [_radarr_kandidat(film_id=None)]

    with SessionLocal() as db, pytest.raises(DownloadFehler) as fehler:
        await download_aktionen.importieren(
            db, load_settings(db), zeile_id, [PFAD], trotzdem=True, wer=_admin()
        )
    assert fehler.value.code == "download_import_unmapped"

    with SessionLocal() as db, pytest.raises(DownloadFehler) as fehler:
        await download_aktionen.importieren(
            db, load_settings(db), zeile_id, ["/etc/passwd"], trotzdem=True, wer=_admin()
        )
    assert fehler.value.code == "download_import_unknown_file"

    with SessionLocal() as db, pytest.raises(DownloadFehler) as fehler:
        await download_aktionen.importieren(
            db, load_settings(db), zeile_id, [], trotzdem=True, wer=_admin()
        )
    assert fehler.value.code == "download_import_nothing_selected"
    assert arr.gesendet("POST", "/command") == []


async def test_ein_serienimport_nennt_serie_und_folgen(arr: ArrAttrappe) -> None:
    zeilen = [folge(901, folge_nummer=2, folge_id=702)]
    arr.warteschlange[SONARR_HOST] = zeilen
    zeile_id = _merken("sonarr-standard", zeilen)
    pfad = "/downloads/Beispielserie.S01E02/folge.mkv"
    arr.kandidaten["S1"] = [
        {
            "path": pfad,
            "folderName": "Beispielserie.S01E02",
            "series": {"id": 7, "title": "Beispielserie"},
            "episodes": [{"id": 702, "seasonNumber": 1, "episodeNumber": 2}],
            "releaseType": "singleEpisode",
            "quality": {"quality": {"id": 3, "name": "WEBDL-1080p"}},
            "languages": [],
            "releaseGroup": "GRP",
            "indexerFlags": 0,
            "rejections": [],
        }
    ]

    with SessionLocal() as db:
        await download_aktionen.importieren(
            db, load_settings(db), zeile_id, [pfad], trotzdem=False, wer=_admin()
        )

    (datei,) = arr.gesendet("POST", "/command")[0].koerper["files"]
    assert datei["seriesId"] == 7
    assert datei["episodeIds"] == [702]
    assert datei["releaseType"] == "singleEpisode"
    assert datei["downloadId"] == "S1"
    assert "movieId" not in datei


async def test_ein_gescheiterter_import_steht_im_verlauf(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    zeile_id = _merken("radarr-standard", [film()])
    arr.kandidaten["D1"] = [_radarr_kandidat()]
    arr.befehl_status = "failed"

    with SessionLocal() as db:
        ergebnis = await download_aktionen.importieren(
            db, load_settings(db), zeile_id, [PFAD], trotzdem=False, wer=_admin()
        )

    assert ergebnis.befehl == "failed"
    assert _verlauf()[0].ergebnis == "command_failed"


# --- Was ueberhaupt geht ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zeile", "erwartet"),
    [
        pytest.param(
            DownloadHaenger(download_id="D1", media_type="movie", zeilen=[1], arr_id=5),
            [Aktion.manuell_importieren, Aktion.entfernen_neu_suchen, Aktion.entfernen, Aktion.erneut_pruefen],
            id="alles",
        ),
        pytest.param(
            DownloadHaenger(download_id="zeile-3", media_type="movie", zeilen=[3], arr_id=None),
            [Aktion.entfernen, Aktion.erneut_pruefen],
            id="ohne-kennung-und-film",
        ),
        pytest.param(
            DownloadHaenger(download_id="S1", media_type="tv", zeilen=[9], folgen_ids=None),
            [Aktion.manuell_importieren, Aktion.entfernen, Aktion.erneut_pruefen],
            id="serie-ohne-folgen",
        ),
        pytest.param(
            DownloadHaenger(download_id="S1", media_type="tv", zeilen=[], folgen_ids=[1]),
            [Aktion.manuell_importieren, Aktion.erneut_pruefen],
            id="ohne-zeilen",
        ),
    ],
)
def test_moegliche_aktionen(zeile: DownloadHaenger, erwartet: list[Aktion]) -> None:
    assert download_aktionen.moegliche_aktionen(zeile) == erwartet
