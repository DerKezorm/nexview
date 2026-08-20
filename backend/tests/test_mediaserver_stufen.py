"""Eine Kopie im Media-Server einer Stufe zuordnen.

Der Fall, um den es geht: Wer einen Film aus Radarr entfernt, sobald die
Wunschqualitaet erreicht ist, hat ihn weiterhin in Plex. Nexview wusste
bisher nicht, ob das die 1080p- oder die 4K-Fassung ist - die Aufloesung
wurde beim Abgleich gar nicht erfasst. Ergebnis: Der Titel stand als "nicht
angefragt" da und wurde ein zweites Mal heruntergeladen.

Plex liefert die Angabe an jeder Datei mit (``Media[].videoResolution``);
nachgemessen an einer echten Bibliothek. Bei Serien fehlt sie am Titel, weil
die Dateien dort an den Folgen haengen - dafuer gilt weiter "Standard".
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import MediaServerLibraryItem, MediaType
from app.services import mediaserver_library
from app.services.mediaserver.plex import _als_werk


class Kachel:
    """Das Wenige, das der Abgleich von einem Titel braucht."""

    def __init__(self, tmdb_id: int, title: str, year: int) -> None:
        self.tmdb_id = tmdb_id
        self.title = title
        self.release_date = f"{year}-01-01"
        self.tvdb_id = None


def _eintragen(db: Session, *, tmdb_id: int, titel: str, jahr: int, standard: bool, uhd: bool):
    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.movie,
            guid=f"plex://film/{tmdb_id}",
            title=titel,
            title_key=titel.lower(),
            tmdb_id=tmdb_id,
            year=jahr,
            has_standard=standard,
            has_uhd=uhd,
        )
    )
    db.commit()


# --- Plex-Auswertung -------------------------------------------------------


@pytest.mark.parametrize(
    ("aufloesungen", "erwartet_standard", "erwartet_uhd"),
    [
        (["1080"], True, False),
        (["4k"], False, True),
        (["1080", "4k"], True, True),   # Plex fuehrt beide Dateien am selben Titel
        (["720"], True, False),
        ([], True, False),              # Serien: keine Angabe am Titel
    ],
)
def test_plex_aufloesung_wird_uebersetzt(aufloesungen, erwartet_standard, erwartet_uhd) -> None:
    werk = _als_werk(
        {
            "title": "Testfilm",
            "ratingKey": "1",
            "Media": [{"videoResolution": wert} for wert in aufloesungen],
        },
        "movie",
    )
    assert werk is not None
    assert werk.has_standard is erwartet_standard
    assert werk.has_uhd is erwartet_uhd


def test_ordnername_zaehlt_nicht_nur_die_datei() -> None:
    """Ein Ordner namens "(4K)" macht aus 1080p kein 4K.

    Genau dieser Fall stand in einer echten Bibliothek: ein Film unter
    ``/media/Movies4K/... (4K)/``, die Datei darin aber
    ``[WEBDL-1080p]`` mit 1920x800. Wer den Bibliotheks- oder Ordnernamen als
    Merkmal nimmt, behauptet hier 4K - Plex selbst sagt korrekt "1080".
    """
    werk = _als_werk(
        {
            "title": "Matrix",
            "ratingKey": "1",
            "Media": [
                {
                    "videoResolution": "1080",
                    "width": 1920,
                    "height": 800,
                    "Part": [{"file": "/media/Movies4K/The Matrix (1999) (4K)/... [WEBDL-1080p].mkv"}],
                }
            ],
        },
        "movie",
    )
    assert werk is not None
    assert werk.has_uhd is False
    assert werk.has_standard is True


# --- Abgleich nach Stufe ---------------------------------------------------


def test_abgleich_trennt_die_stufen() -> None:
    kacheln = [Kachel(603, "Matrix", 1999), Kachel(604, "Matrix Reloaded", 2003)]
    with SessionLocal() as db:
        db.query(MediaServerLibraryItem).delete()
        db.commit()
        _eintragen(db, tmdb_id=603, titel="Matrix", jahr=1999, standard=False, uhd=True)
        _eintragen(db, tmdb_id=604, titel="Matrix Reloaded", jahr=2003, standard=True, uhd=False)

        nur_uhd = mediaserver_library.vorhandene_kennungen(db, MediaType.movie, kacheln, "uhd")
        nur_standard = mediaserver_library.vorhandene_kennungen(
            db, MediaType.movie, kacheln, "standard"
        )
        egal = mediaserver_library.vorhandene_kennungen(db, MediaType.movie, kacheln)

        db.query(MediaServerLibraryItem).delete()
        db.commit()

    assert nur_uhd == {603}, "Die 4K-Kopie muss der 4K-Stufe zugeordnet werden"
    assert nur_standard == {604}, "Eine reine 4K-Kopie darf nicht als 1080p durchgehen"
    # Ohne zweite Instanz gibt es nur eine Achse - dann zaehlt jede Kopie.
    assert egal == {603, 604}


# --- Derselbe Film in zwei Bibliotheken ------------------------------------


def test_gleiche_guid_aus_zwei_bibliotheken_wird_zusammengefasst() -> None:
    """1080p und 4K in getrennten Plex-Bibliotheken - ein Film, eine Zeile.

    Der Fehler, den das absichert, hat den Abgleich **vollstaendig**
    lahmgelegt: Plex vergibt fuer denselben Film in beiden Bibliotheken
    dieselbe GUID, die Tabelle laesst sie nur einmal zu, und der Einlesevorgang
    brach mit einem UNIQUE-Fehler ab. Danach kannte Nexview keinen einzigen
    Titel des Media-Servers mehr und zeigte alles als "nicht angefragt" -
    obwohl die halbe Sammlung da war.

    Gefunden an einer echten Bibliothek mit den Abschnitten "Filme" und
    "Filme4K"; nach der Zusammenfassung liefen dort 3686 Titel durch.
    """
    from app.services.mediaserver.base import LibraryItem
    from app.services.mediaserver_library import _zusammengefasst

    guid = "plex://movie/5d776827880197001ec90904"
    aus_filme = LibraryItem(
        media_type="movie",
        guid=guid,
        title="Matrix",
        tmdb_id=603,
        year=1999,
        has_standard=True,
        has_uhd=False,
        owner_watched=True,
    )
    aus_filme_4k = LibraryItem(
        media_type="movie",
        guid=guid,
        title="Matrix",
        tmdb_id=None,  # andere Bibliothek, anderer Agent - Kennung fehlt hier
        year=1999,
        has_standard=False,
        has_uhd=True,
        owner_watched=False,
    )

    ergebnis = _zusammengefasst([aus_filme, aus_filme_4k])

    assert len(ergebnis) == 1, "Zwei Zeilen mit derselben GUID sprengen die Tabelle"
    zusammen = ergebnis[0]
    assert zusammen.has_standard is True
    assert zusammen.has_uhd is True, "Die 4K-Fassung darf nicht verloren gehen"
    assert zusammen.owner_watched is True
    assert zusammen.tmdb_id == 603, "Eine fehlende Kennung wird aus dem Partner ergaenzt"


def test_verschiedene_guids_bleiben_getrennt() -> None:
    """Zusammengefasst wird nur, was wirklich derselbe Titel ist."""
    from app.services.mediaserver.base import LibraryItem
    from app.services.mediaserver_library import _zusammengefasst

    a = LibraryItem(media_type="movie", guid="plex://movie/a", title="Matrix", tmdb_id=603)
    b = LibraryItem(media_type="movie", guid="plex://movie/b", title="Matrix Reloaded", tmdb_id=604)

    assert len(_zusammengefasst([a, b])) == 2
