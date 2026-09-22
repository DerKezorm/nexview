"""Haengende Downloads merken: zusammenfassen, beobachten, als haengend erkennen, abraeumen.

Die Zeit wird hier von Hand gefuehrt (``abgleichen`` nimmt ``jetzt``), damit
die Wartezeiten ohne Schlaf pruefbar sind.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    DownloadHaenger,
    DownloadVerlauf,
    MediaRequest,
    MediaType,
    QualityTier,
    RequestStatus,
)
from app.schemas_requests import RequestPublic, RequestWithUser
from app.services import status_poller
from app.services.beschaffung.arr import download_haenger
from app.services.fassungen import arr_kennung
from app.services.settings_service import ArrInstanz, load_settings

from .conftest import create_user
from .download_attrappe import RADARR_HOST, SONARR_HOST, ArrAttrappe, einrichten, film, folge

T0 = datetime(2026, 9, 12, 20, 0)


@pytest.fixture
def arr(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ArrAttrappe:
    return einrichten(admin_client, monkeypatch)


def _instanz(kennung: str) -> ArrInstanz:
    with SessionLocal() as db:
        return next(i for i in load_settings(db).arr_instanzen() if i.kennung == kennung)


def _abgleich(kennung: str, zeilen: list[dict], jetzt: datetime) -> None:
    instanz = _instanz(kennung)
    with SessionLocal() as db:
        download_haenger.abgleichen(
            db, instanz, download_haenger.zusammenfassen(instanz, zeilen), jetzt
        )
        db.commit()


def _zeilen() -> list[DownloadHaenger]:
    with SessionLocal() as db:
        return list(db.scalars(select(DownloadHaenger).order_by(DownloadHaenger.id)))


def _verlauf() -> list[DownloadVerlauf]:
    with SessionLocal() as db:
        return list(db.scalars(select(DownloadVerlauf).order_by(DownloadVerlauf.id)))


# --- Zusammenfassen -------------------------------------------------------------


def test_die_folgen_eines_pakets_werden_ein_download(arr: ArrAttrappe) -> None:
    """Sonarr fuehrt eine Zeile je Folge; gezaehlt wird der Download."""
    zeilen = [
        folge(901, folge_nummer=1, folge_id=701),
        folge(902, folge_nummer=2, folge_id=702),
        folge(903, folge_nummer=3, folge_id=703),
    ]
    downloads = download_haenger.zusammenfassen(_instanz("sonarr-standard"), zeilen)

    assert len(downloads) == 1
    paket = downloads[0]
    assert paket.zeilen == [901, 902, 903]
    assert paket.folgen == [[1, 1], [1, 2], [1, 3]]
    assert paket.folgen_ids == [701, 702, 703]
    assert paket.arr_id == 7
    assert paket.titel == "Beispielserie"
    # Jede Zeile traegt die Groesse des ganzen Downloads - nicht dreimal zaehlen.
    assert paket.groesse == 5000


def test_eine_zeile_ohne_download_id_bleibt_fuer_sich(arr: ArrAttrappe) -> None:
    ohne = film(21, download_id="")
    ohne.pop("downloadId")
    downloads = download_haenger.zusammenfassen(
        _instanz("radarr-standard"), [ohne, film(22, download_id="")]
    )
    assert sorted(d.download_id for d in downloads) == ["zeile-21", "zeile-22"]


# --- Beobachten und erkennen ------------------------------------------------------


def test_erst_beobachtet_dann_haengend(arr: ArrAttrappe) -> None:
    _abgleich("radarr-standard", [film()], T0)
    (zeile,) = _zeilen()
    assert zeile.grund == "sample"
    assert zeile.haengt_seit is None
    assert zeile.titel == "Beispielfilm"
    assert zeile.zeilen == [11]

    _abgleich("radarr-standard", [film()], T0 + timedelta(minutes=9, seconds=59))
    assert _zeilen()[0].haengt_seit is None
    assert _verlauf() == []

    _abgleich("radarr-standard", [film()], T0 + timedelta(minutes=10))
    assert _zeilen()[0].haengt_seit == T0 + timedelta(minutes=10)
    assert [(v.was, v.grund, v.arr_id) for v in _verlauf()] == [("erkannt", "sample", 5)]

    # Und nur einmal erkannt, nicht jede Runde wieder.
    _abgleich("radarr-standard", [film()], T0 + timedelta(minutes=12))
    assert len(_verlauf()) == 1


def test_eine_veraenderung_setzt_die_ruhe_zurueck(arr: ArrAttrappe) -> None:
    _abgleich("radarr-standard", [film()], T0)
    _abgleich("radarr-standard", [film(texte=("Not enough free space",))], T0 + timedelta(minutes=6))
    _abgleich("radarr-standard", [film(texte=("Not enough free space",))], T0 + timedelta(minutes=10))
    assert _zeilen()[0].haengt_seit is None

    _abgleich("radarr-standard", [film(texte=("Not enough free space",))], T0 + timedelta(minutes=11))
    assert _zeilen()[0].haengt_seit is not None


def test_das_download_programm_bekommt_laenger(arr: ArrAttrappe) -> None:
    haengt = film(
        zustand="downloading", programm="warning", texte=(), rest=500,
        fehler="The download is stalled with no connections",
    )
    _abgleich("radarr-standard", [haengt], T0)
    _abgleich("radarr-standard", [haengt], T0 + timedelta(minutes=14))
    assert _zeilen()[0].haengt_seit is None

    _abgleich("radarr-standard", [haengt], T0 + timedelta(minutes=15))
    assert _zeilen()[0].grund == "programm_haengt"
    assert _zeilen()[0].haengt_seit is not None


def test_wer_weiterlaedt_haengt_nicht(arr: ArrAttrappe) -> None:
    langsam = {"zustand": "downloading", "programm": "warning", "texte": (), "fehler": "stalled"}
    _abgleich("radarr-standard", [film(rest=500, **langsam)], T0)
    _abgleich("radarr-standard", [film(rest=500, **langsam)], T0 + timedelta(minutes=20))
    assert _zeilen()[0].haengt_seit is not None

    _abgleich("radarr-standard", [film(rest=400, **langsam)], T0 + timedelta(minutes=21))
    (zeile,) = _zeilen()
    assert zeile.haengt_seit is None
    assert zeile.erstmals_gesehen == T0 + timedelta(minutes=21)


def test_ein_laufender_download_legt_keine_zeile_an(arr: ArrAttrappe) -> None:
    _abgleich(
        "radarr-standard",
        [film(zustand="downloading", meldung="ok", programm="downloading", texte=(), rest=400)],
        T0,
    )
    assert _zeilen() == []


def test_ein_erledigter_download_verschwindet(arr: ArrAttrappe) -> None:
    _abgleich("radarr-standard", [film(), film(12, "D2")], T0)
    assert len(_zeilen()) == 2
    _abgleich("radarr-standard", [film(12, "D2")], T0 + timedelta(minutes=1))
    assert [z.download_id for z in _zeilen()] == ["D2"]


# --- Der ganze Abgleich -----------------------------------------------------------


async def test_eine_stumme_instanz_raeumt_nichts_ab(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    with SessionLocal() as db:
        await download_haenger.auffrischen(db, load_settings(db))
    assert len(_zeilen()) == 1

    arr.stumm.add(RADARR_HOST)
    with SessionLocal() as db:
        rundgang = await download_haenger.auffrischen(db, load_settings(db))

    radarr = next(a for a in rundgang.abfragen if a.instanz.kennung == "radarr-standard")
    assert radarr.erreichbar is False
    assert radarr.fehler == "arr_unreachable"
    sonarr = next(a for a in rundgang.abfragen if a.instanz.kennung == "sonarr-standard")
    assert sonarr.erreichbar is True
    assert len(_zeilen()) == 1


async def test_zeilen_einer_entfernten_instanz_verschwinden(arr: ArrAttrappe) -> None:
    with SessionLocal() as db:
        db.add(DownloadHaenger(kennung="radarr-uhd", download_id="X", media_type="movie", grund="sample"))
        db.commit()
        await download_haenger.auffrischen(db, load_settings(db))
    assert _zeilen() == []


async def test_die_warteschlange_wird_mit_allem_geholt(arr: ArrAttrappe) -> None:
    """Ohne ``includeUnknown...`` fehlen genau die Downloads ohne Zuordnung."""
    with SessionLocal() as db:
        await download_haenger.auffrischen(db, load_settings(db))
    radarr = next(a for a in arr.gesendet("GET", "/queue") if a.host == RADARR_HOST)
    sonarr = next(a for a in arr.gesendet("GET", "/queue") if a.host == SONARR_HOST)
    assert radarr.params["includeUnknownMovieItems"] == "true"
    assert radarr.params["includeMovie"] == "true"
    assert sonarr.params["includeUnknownSeriesItems"] == "true"
    assert sonarr.params["includeEpisode"] == "true"


async def test_die_seite_nimmt_einen_frischen_abgleich(arr: ArrAttrappe) -> None:
    with SessionLocal() as db:
        erster = await download_haenger.auffrischen(db, load_settings(db))
        vorher = len(arr.anrufe)
        zweiter = await download_haenger.auffrischen(
            db, load_settings(db), frisch_genug=download_haenger.FRISCH
        )
        assert zweiter is erster
        assert len(arr.anrufe) == vorher

        download_haenger.vergessen()
        dritter = await download_haenger.auffrischen(
            db, load_settings(db), frisch_genug=download_haenger.FRISCH
        )
    assert dritter is not erster
    assert len(arr.anrufe) > vorher


async def test_check_once_holt_die_warteschlange_kein_zweites_mal(
    arr: ArrAttrappe, admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Rundgang reicht sie weiter; ``warteschlange()`` darf nicht mehr gerufen werden."""
    from app.services.beschaffung.arr.radarr import RadarrClient

    async def verboten(self) -> list:  # noqa: ANN001 - Attrappe
        raise AssertionError("Die Warteschlange wurde ein zweites Mal geholt")

    monkeypatch.setattr(RadarrClient, "warteschlange", verboten)
    arr.warteschlange[RADARR_HOST] = [film(zustand="downloading", meldung="ok", programm="downloading", texte=(), rest=250)]
    with SessionLocal() as db:
        rundgang = await download_haenger.auffrischen(db, load_settings(db))
    vorab = rundgang.warteschlangen
    assert set(vorab) == {("movie", "standard"), ("tv", "standard")}

    from app.services.beschaffung.arr import library
    from app.services.beschaffung.arr.radarr import LibraryEntry

    async def bibliothek(_settings: object, _tier: str = "standard") -> dict:
        return {4711: LibraryEntry(arr_id=5, has_file=False, monitored=True)}

    async def keine_serien(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
        return {}, {}

    monkeypatch.setattr(library, "movie_library", bibliothek)
    monkeypatch.setattr(library, "series_library", keine_serien)
    create_user(admin_client, "kim")
    _anfrage(4711, arr_id=5)
    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db), vorab)
    with SessionLocal() as db:
        assert db.scalar(select(MediaRequest.laedt_fortschritt)) == 75


# --- Anfragen ---------------------------------------------------------------------


def _anfrage(
    tmdb_id: int = 4711,
    *,
    arr_id: int | None = 5,
    media_type: MediaType = MediaType.movie,
    season: int | None = None,
    episodes: list[int] | None = None,
    status: RequestStatus = RequestStatus.searching,
) -> int:
    with SessionLocal() as db:
        from app.models import User

        besitzer = db.scalar(select(User).where(User.username == "kim"))
        anfrage = MediaRequest(
            user_id=besitzer.id,
            media_type=media_type,
            fassung_kennung=arr_kennung(media_type, QualityTier.standard),
            tmdb_id=tmdb_id,
            title="Beispiel",
            status=status,
            arr_id=arr_id,
            season=season,
            episodes=episodes,
        )
        db.add(anfrage)
        db.commit()
        return anfrage.id


def _grund(anfrage_id: int) -> str | None:
    with SessionLocal() as db:
        return db.get(MediaRequest, anfrage_id).import_haengt


async def test_die_anfrage_erfaehrt_es_erst_wenn_es_haengt(
    arr: ArrAttrappe, admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_user(admin_client, "kim")
    passend = _anfrage(arr_id=5)
    fremd = _anfrage(4712, arr_id=6)
    arr.warteschlange[RADARR_HOST] = [film()]

    uhr = {"jetzt": T0}
    monkeypatch.setattr(download_haenger, "_jetzt", lambda: uhr["jetzt"])

    with SessionLocal() as db:
        await download_haenger.auffrischen(db, load_settings(db))
    assert _grund(passend) is None

    uhr["jetzt"] = T0 + timedelta(minutes=10)
    with SessionLocal() as db:
        await download_haenger.auffrischen(db, load_settings(db))
    assert _grund(passend) == "sample"
    assert _grund(fremd) is None

    # Erledigt - der Hinweis geht mit.
    arr.warteschlange[RADARR_HOST] = []
    uhr["jetzt"] = T0 + timedelta(minutes=12)
    with SessionLocal() as db:
        await download_haenger.auffrischen(db, load_settings(db))
    assert _grund(passend) is None


@pytest.mark.parametrize(
    ("season", "episodes", "erwartet"),
    [
        pytest.param(None, None, True, id="ganze-serie"),
        pytest.param(1, None, True, id="passende-staffel"),
        pytest.param(2, None, False, id="andere-staffel"),
        pytest.param(1, [2], True, id="passende-folge"),
        pytest.param(1, [3], False, id="andere-folge"),
    ],
)
def test_eine_serienanfrage_nur_mit_passender_folge(
    arr: ArrAttrappe, admin_client: TestClient, season, episodes, erwartet
) -> None:
    create_user(admin_client, "kim")
    anfrage_id = _anfrage(99, arr_id=7, media_type=MediaType.tv, season=season, episodes=episodes)
    zeile = DownloadHaenger(
        kennung="sonarr-standard", download_id="S1", media_type="tv", grund="sample",
        arr_id=7, folgen=[[1, 2]],
    )
    with SessionLocal() as db:
        anfrage = db.get(MediaRequest, anfrage_id)
        assert download_haenger.gehoert_zu(anfrage, _instanz("sonarr-standard"), zeile) is erwartet


def test_ohne_folgenangabe_kein_hinweis_an_der_staffel(arr: ArrAttrappe, admin_client: TestClient) -> None:
    """Lieber kein Hinweis als ein falscher - wie bei der Fortschrittsanzeige."""
    create_user(admin_client, "kim")
    anfrage_id = _anfrage(99, arr_id=7, media_type=MediaType.tv, season=1)
    zeile = DownloadHaenger(
        kennung="sonarr-standard", download_id="S1", media_type="tv", grund="sample", arr_id=7
    )
    with SessionLocal() as db:
        anfrage = db.get(MediaRequest, anfrage_id)
        assert download_haenger.gehoert_zu(anfrage, _instanz("sonarr-standard"), zeile) is False


def test_der_hinweis_steht_nur_in_der_liste_der_entscheider() -> None:
    """``RequestPublic`` ist auch die Antwort von ``/api/v1`` - deren Form ist zugesagt."""
    assert "import_haengt" in RequestWithUser.model_fields
    assert "import_haengt" not in RequestPublic.model_fields


# --- Zaehlen und aufraeumen ---------------------------------------------------------


def test_gezaehlt_wird_nur_was_haengt(arr: ArrAttrappe) -> None:
    with SessionLocal() as db:
        db.add_all(
            [
                DownloadHaenger(kennung="radarr-standard", download_id="A", media_type="movie", grund="sample", haengt_seit=T0),
                DownloadHaenger(kennung="radarr-standard", download_id="B", media_type="movie", grund="sample"),
                DownloadHaenger(kennung="sonarr-standard", download_id="C", media_type="tv", grund="archiv", haengt_seit=T0),
            ]
        )
        db.commit()
        assert download_haenger.zaehlen(db) == {"radarr-standard": 1, "sonarr-standard": 1}


def test_alter_verlauf_verschwindet(arr: ArrAttrappe) -> None:
    jetzt = T0
    with SessionLocal() as db:
        db.add_all(
            [
                DownloadVerlauf(kennung="radarr-standard", was="erkannt", am=jetzt - timedelta(days=91)),
                DownloadVerlauf(kennung="radarr-standard", was="erkannt", am=jetzt - timedelta(days=89)),
            ]
        )
        db.commit()
        assert download_haenger.verlauf_aufraeumen(db, jetzt) == 1
        # Welcher bleibt, nicht nur wie viele: Falsch herum geraeumt, bliebe
        # ebenfalls genau einer uebrig - der alte.
        assert list(db.scalars(select(DownloadVerlauf.am))) == [jetzt - timedelta(days=89)]
