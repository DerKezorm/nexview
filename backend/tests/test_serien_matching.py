"""Serien nur bei passendem Jahr über den Titel zuordnen.

Gemeldet als Issue #1 von DerAachener: "Countdown" (1982, Channel 4) stand in
der Detailansicht als "Bereits geladen" mit 13 von 26 Folgen als vorhanden -
obwohl die Serie weder in Sonarr noch in Plex existierte. In Sonarr lag eine
voellig andere Serie desselben Namens, und der Abgleich verglich nur den
normalisierten Titel.

Der Schaden ist nicht kosmetisch: Ein falscher Treffer nimmt einen Titel
dauerhaft aus dem Angebot, ohne dass jemand den Grund sieht.
"""

from __future__ import annotations

import pytest

from app.services import library
from app.services.sonarr import LibraryEntry as SeriesEntry
from app.services.sonarr import jahre_passen, normalize_title


def _serie(titel: str, jahr: int | None, arr_id: int = 1) -> SeriesEntry:
    return SeriesEntry(
        arr_id=arr_id,
        has_file=True,
        monitored=True,
        episode_file_count=13,
        episode_count=26,
        title_key=normalize_title(titel),
        year=jahr,
    )


# --- Die Regel selbst -------------------------------------------------------


@pytest.mark.parametrize(
    ("gesucht", "gefunden", "erwartet"),
    [
        (1982, 1982, True),
        (1982, 1983, True),    # Erstausstrahlung vs. Zaehlweise der Datenbank
        (1982, 1981, True),
        (1982, 2025, False),   # der gemeldete Fall
        (1982, None, False),   # ohne Angabe kein Treffer
        (None, 1982, False),
        (None, None, False),
    ],
)
def test_jahresregel(gesucht, gefunden, erwartet) -> None:
    assert jahre_passen(gesucht, gefunden) is erwartet


# --- Der gemeldete Fall -----------------------------------------------------


def test_gleicher_titel_anderes_jahr_trifft_nicht() -> None:
    """Der Fall aus Issue #1: zwei Serien "Countdown", 43 Jahre auseinander."""
    nach_titel = {normalize_title("Countdown"): _serie("Countdown", 2025)}

    treffer = library.treffer_nach_titel(nach_titel, "Countdown", 1982)

    assert treffer is None, (
        "Namensgleichheit allein darf nicht reichen - sonst erbt die eine Serie "
        "die Folgen der anderen"
    )


def test_gleicher_titel_gleiches_jahr_trifft() -> None:
    """Der Rueckfallweg muss weiter funktionieren.

    Fuer viele Serien kennt TMDB keine TVDB-Id; ohne den Titel-Rueckfall gaebe
    es dort gar keinen Abgleich mehr.
    """
    nach_titel = {normalize_title("Countdown"): _serie("Countdown", 1982)}

    treffer = library.treffer_nach_titel(nach_titel, "Countdown", 1982)

    assert treffer is not None
    assert treffer.arr_id == 1


def test_ohne_jahr_lieber_kein_treffer() -> None:
    """Fehlt das Jahr, wird der Treffer verworfen.

    Ein uebersehener Titel kostet einen doppelten Download; ein falscher nimmt
    ihn dauerhaft aus dem Angebot. Die Richtung des Zweifels ist damit klar.
    """
    nach_titel = {normalize_title("Countdown"): _serie("Countdown", 1982)}

    assert library.treffer_nach_titel(nach_titel, "Countdown", None) is None


def test_unbekannter_titel_bleibt_ohne_treffer() -> None:
    assert library.treffer_nach_titel({}, "Gibt es nicht", 2020) is None


def test_jahr_aus_datum() -> None:
    assert library.jahr_aus("1982-05-03") == 1982
    assert library.jahr_aus("") is None
    assert library.jahr_aus(None) is None
    assert library.jahr_aus("unbekannt") is None
