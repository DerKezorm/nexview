"""Die Automatik fuer haengende Downloads: ab Werk aus, nur Erlaubtes, mit Obergrenze."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    DownloadHaenger,
    DownloadVerlauf,
    Notification,
    NotificationType,
    Setting,
)
from app.services import download_aktionen, download_automatik, download_haenger
from app.services.download_aktionen import DownloadFehler
from app.services.settings_service import load_settings

from .download_attrappe import RADARR_HOST, SONARR_HOST, ArrAttrappe, einrichten, film, folge


@pytest.fixture
def arr(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ArrAttrappe:
    monkeypatch.setattr(download_aktionen, "ABWARTEN_TAKT", 0)
    return einrichten(admin_client, monkeypatch)


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _merken(kennung: str, zeilen: list[dict], *, haengend: set[str]) -> dict[str, int]:
    """Abgleichen; die genannten Downloads gelten danach als haengend."""
    with SessionLocal() as db:
        instanz = next(i for i in load_settings(db).arr_instanzen() if i.kennung == kennung)
        download_haenger.abgleichen(
            db, instanz, download_haenger.zusammenfassen(instanz, zeilen), _jetzt()
        )
        db.commit()
        kennungen: dict[str, int] = {}
        for zeile in db.scalars(select(DownloadHaenger).where(DownloadHaenger.kennung == kennung)):
            if zeile.download_id in haengend:
                zeile.haengt_seit = _jetzt() - timedelta(minutes=len(kennungen) + 1)
            kennungen[zeile.download_id] = zeile.id
        db.commit()
        return kennungen


def _regeln(an: bool, **regeln: str) -> None:
    with SessionLocal() as db:
        download_automatik.schreiben(db, an=an, regeln=regeln)


async def _runde() -> int:
    with SessionLocal() as db:
        return await download_automatik.ausfuehren(db, load_settings(db))


def _meldungen() -> list[tuple[str, str | None]]:
    with SessionLocal() as db:
        return [
            (m.message_key, m.message_title)
            for m in db.scalars(
                select(Notification).where(Notification.type == NotificationType.download_stuck)
            )
        ]


def _vorher(was: str, anzahl: int, *, vor: timedelta, automatisch: bool = True) -> None:
    with SessionLocal() as db:
        for _ in range(anzahl):
            db.add(
                DownloadVerlauf(
                    kennung="radarr-standard", arr_id=5, media_type="movie", was=was,
                    automatisch=automatisch, am=_jetzt() - vor,
                )
            )
        db.commit()


# --- Einstellungen ----------------------------------------------------------------------


def test_ab_werk_aus_und_ohne_regeln(arr: ArrAttrappe) -> None:
    with SessionLocal() as db:
        einstellung = download_automatik.lesen(db)
    assert einstellung.an is False
    assert einstellung.regeln == {}


@pytest.mark.parametrize(
    ("grund", "aktion"),
    [
        pytest.param("sample", "manuell_importieren", id="nie-importieren"),
        pytest.param("pfadzuordnung", "entfernen", id="grund-erlaubt-nichts"),
        pytest.param("gibt_es_nicht", "entfernen", id="unbekannter-grund"),
    ],
)
def test_was_der_grund_nicht_erlaubt_wird_abgelehnt(
    arr: ArrAttrappe, grund: str, aktion: str
) -> None:
    with SessionLocal() as db, pytest.raises(DownloadFehler) as fehler:
        download_automatik.schreiben(db, an=True, regeln={grund: aktion})
    assert (fehler.value.code, fehler.value.status_code) == ("download_automation_not_allowed", 422)
    with SessionLocal() as db:
        assert download_automatik.lesen(db).an is False


def test_eine_nicht_mehr_erlaubte_regel_faellt_heraus(arr: ArrAttrappe) -> None:
    """Aendert ein Update, was ein Grund darf, laeuft keine alte Regel weiter."""
    with SessionLocal() as db:
        db.add(
            Setting(
                key=download_automatik.SCHLUESSEL,
                value=json.dumps(
                    {
                        "an": True,
                        "regeln": {
                            "sample": "manuell_importieren",
                            "archiv": "entfernen",
                            "kein_upgrade": "entfernen",
                        },
                    }
                ),
            )
        )
        db.commit()
        einstellung = download_automatik.lesen(db)
    assert einstellung.an is True
    assert einstellung.regeln == {"kein_upgrade": "entfernen"}


def test_leere_regeln_bleiben_liegen(arr: ArrAttrappe) -> None:
    with SessionLocal() as db:
        einstellung = download_automatik.schreiben(
            db, an=True, regeln={"sample": "entfernen_neu_suchen", "archiv": None}
        )
    assert einstellung.regeln == {"sample": "entfernen_neu_suchen"}


# --- Handeln ----------------------------------------------------------------------------


async def test_ab_werk_passiert_nichts(arr: ArrAttrappe) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    _merken("radarr-standard", [film()], haengend={"D1"})

    assert await _runde() == 0
    assert arr.gesendet("DELETE") == []
    assert arr.gesendet("POST") == []


async def test_eine_ausgeschaltete_automatik_handelt_trotz_regel_nicht(arr: ArrAttrappe) -> None:
    _regeln(False, sample="entfernen_neu_suchen")
    arr.warteschlange[RADARR_HOST] = [film()]
    _merken("radarr-standard", [film()], haengend={"D1"})

    assert await _runde() == 0
    assert arr.gesendet("DELETE") == []


async def test_die_regel_greift_nur_bei_haengenden_downloads(arr: ArrAttrappe) -> None:
    _regeln(True, sample="entfernen_neu_suchen")
    zeilen = [film(11, "D1"), film(12, "D2", film_id=6)]
    arr.warteschlange[RADARR_HOST] = zeilen
    kennungen = _merken("radarr-standard", zeilen, haengend={"D1"})

    assert await _runde() == 1

    (loeschen,) = arr.gesendet("DELETE", "/queue")
    assert loeschen.pfad == "/queue/11"
    assert loeschen.params["blocklist"] == "true"
    (suche,) = arr.gesendet("POST", "/command")
    assert suche.koerper == {"name": "MoviesSearch", "movieIds": [5]}
    with SessionLocal() as db:
        assert db.get(DownloadHaenger, kennungen["D1"]) is None
        assert db.get(DownloadHaenger, kennungen["D2"]) is not None
        (eintrag,) = db.scalars(select(DownloadVerlauf))
        assert (eintrag.was, eintrag.automatisch, eintrag.user_id) == (
            "entfernen_neu_suchen", True, None,
        )


async def test_nach_der_obergrenze_hoert_sie_auf_und_meldet_sich_einmal(arr: ArrAttrappe) -> None:
    _regeln(True, sample="entfernen_neu_suchen")
    _vorher("entfernen_neu_suchen", download_automatik.OBERGRENZE, vor=timedelta(hours=1))
    arr.warteschlange[RADARR_HOST] = [film()]
    _merken("radarr-standard", [film()], haengend={"D1"})

    assert await _runde() == 0
    assert arr.gesendet("DELETE") == []
    assert _meldungen() == [("notifications.downloadAutomationGaveUp", "Beispielfilm")]

    assert await _runde() == 0
    assert len(_meldungen()) == 1


async def test_aeltere_aktionen_zaehlen_nicht_mehr(arr: ArrAttrappe) -> None:
    _regeln(True, sample="entfernen_neu_suchen")
    _vorher("entfernen_neu_suchen", download_automatik.OBERGRENZE, vor=timedelta(hours=25))
    arr.warteschlange[RADARR_HOST] = [film()]
    _merken("radarr-standard", [film()], haengend={"D1"})

    assert await _runde() == 1


async def test_handarbeit_zaehlt_nicht_zur_obergrenze(arr: ArrAttrappe) -> None:
    """Wer selbst zweimal entfernt hat, soll die Automatik damit nicht abschalten."""
    _regeln(True, sample="entfernen_neu_suchen")
    _vorher("entfernen_neu_suchen", 5, vor=timedelta(hours=1), automatisch=False)
    arr.warteschlange[RADARR_HOST] = [film()]
    _merken("radarr-standard", [film()], haengend={"D1"})

    assert await _runde() == 1


@pytest.mark.parametrize(("erkannt", "gemeldet"), [(2, False), (3, True)])
async def test_ein_titel_der_immer_wieder_haengt_meldet_sich_auch_ohne_automatik(
    arr: ArrAttrappe, erkannt: int, gemeldet: bool
) -> None:
    _vorher("erkannt", erkannt, vor=timedelta(days=2))
    arr.warteschlange[RADARR_HOST] = [film()]
    _merken("radarr-standard", [film()], haengend={"D1"})

    await _runde()
    await _runde()

    erwartet = [("notifications.downloadStuck", "Beispielfilm")] if gemeldet else []
    assert _meldungen() == erwartet
    assert arr.gesendet("DELETE") == []


async def test_ein_fehler_nimmt_die_anderen_nicht_mit(arr: ArrAttrappe) -> None:
    _regeln(True, sample="entfernen_neu_suchen")
    arr.warteschlange[RADARR_HOST] = [film()]
    arr.warteschlange[SONARR_HOST] = [folge(901)]
    _merken("radarr-standard", [film()], haengend={"D1"})
    _merken("sonarr-standard", [folge(901)], haengend={"S1"})
    arr.stumm.add(RADARR_HOST)

    assert await _runde() == 1
    (loeschen,) = arr.gesendet("DELETE", "/queue")
    assert loeschen.host == SONARR_HOST


# --- Folgen einer Serie ---------------------------------------------------------------


def _folgen(*nummern: int) -> list[dict]:
    """Je Folge ein eigener Download derselben Serie, Staffel 1."""
    return [folge(900 + n, f"S{n}", folge_nummer=n, folge_id=700 + n) for n in nummern]


def _erkannt_vermerken() -> None:
    """Fuer jeden haengenden Download ein "erkannt", so wie der Rundgang es schreibt."""
    with SessionLocal() as db:
        for zeile in db.scalars(
            select(DownloadHaenger).where(DownloadHaenger.haengt_seit.is_not(None))
        ):
            db.add(download_haenger.verlauf(zeile, "erkannt", automatisch=True))
        db.commit()


def _vorher_folgen(was: str, folgen_ids: list[int], anzahl: int, *, vor: timedelta) -> None:
    with SessionLocal() as db:
        for _ in range(anzahl):
            db.add(
                DownloadVerlauf(
                    kennung="sonarr-standard", arr_id=7, media_type="tv",
                    folgen_ids=folgen_ids, was=was, automatisch=True, am=_jetzt() - vor,
                )
            )
        db.commit()


async def test_drei_folgen_einer_serie_zugleich_sind_kein_immer_wieder(arr: ArrAttrappe) -> None:
    """⚠️ Am Pruefstand gefunden: Gezaehlt je Serie kam sofort "haengt immer wieder"."""
    zeilen = _folgen(1, 2, 3)
    arr.warteschlange[SONARR_HOST] = zeilen
    _merken("sonarr-standard", zeilen, haengend={"S1", "S2", "S3"})
    _erkannt_vermerken()

    await _runde()

    assert _meldungen() == []


async def test_dieselbe_folge_dreimal_meldet_sich_mit_der_folge(arr: ArrAttrappe) -> None:
    _vorher_folgen("erkannt", [702], 2, vor=timedelta(days=2))
    zeilen = _folgen(2)
    arr.warteschlange[SONARR_HOST] = zeilen
    _merken("sonarr-standard", zeilen, haengend={"S2"})
    _erkannt_vermerken()

    await _runde()

    assert _meldungen() == [("notifications.downloadStuck", "Beispielserie S01E02")]


async def test_die_obergrenze_gilt_je_folge(arr: ArrAttrappe) -> None:
    """Zwei Aktionen an Folge 1 halten die Automatik bei Folge 2 nicht auf."""
    _regeln(True, sample="entfernen_neu_suchen")
    _vorher_folgen(
        "entfernen_neu_suchen", [701], download_automatik.OBERGRENZE, vor=timedelta(hours=1)
    )
    zeilen = _folgen(2)
    arr.warteschlange[SONARR_HOST] = zeilen
    _merken("sonarr-standard", zeilen, haengend={"S2"})

    assert await _runde() == 1
    assert _meldungen() == []


async def test_ein_staffelpaket_zaehlt_fuer_jede_seiner_folgen(arr: ArrAttrappe) -> None:
    _regeln(True, sample="entfernen_neu_suchen")
    _vorher_folgen(
        "entfernen_neu_suchen", [701, 702, 703], download_automatik.OBERGRENZE,
        vor=timedelta(hours=1),
    )
    zeilen = _folgen(2)
    arr.warteschlange[SONARR_HOST] = zeilen
    _merken("sonarr-standard", zeilen, haengend={"S2"})

    assert await _runde() == 0
    assert arr.gesendet("DELETE") == []
    assert _meldungen() == [("notifications.downloadAutomationGaveUp", "Beispielserie S01E02")]


async def test_eintraege_ohne_folgen_zaehlen_fuer_keine_bestimmte_folge(arr: ArrAttrappe) -> None:
    _vorher_folgen("erkannt", [], 2, vor=timedelta(days=2))
    zeilen = _folgen(2)
    arr.warteschlange[SONARR_HOST] = zeilen
    _merken("sonarr-standard", zeilen, haengend={"S2"})
    _erkannt_vermerken()

    await _runde()

    assert _meldungen() == []
