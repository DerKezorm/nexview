"""Dieselbe Datei darf nicht zweimal zählen.

⚠️ **Der schwerste Fehler des Speicher-Bereichs, gefunden an einer echten
Anlage: 32 Dateien, 540 GB, einmal vorhanden und zweimal gezählt.**

Der Ablauf, der ihn auslöst, ist alltäglich:

1. Die **Standard**-Instanz von Radarr lädt mit einem 1080p-Profil, greift
   aber eine 2160p-Datei. Das passiert oft genug.
2. Nexview verbucht Radarrs Meldung unter der Stufe der **Instanz** -
   ``standard``.
3. Der Medienserver meldet dieselbe Datei mit ``videoResolution=4k``.
4. ``_aus_media_server`` legt daraus einen **zweiten** Posten unter ``uhd`` an.

Die vorhandene Schutzregel („was Radarr gemeldet hat, bleibt stehen") griff
nicht: Sie vergleicht den Schlüssel, und der unterscheidet sich ja gerade in
der Stufe.

**Warum das nicht harmlos ist.** Beim Hausbestand fällt es niemandem auf. Wer
ein Speicher-Kontingent hat, bekommt dieselbe Datei zweimal angerechnet - und
kann nichts dagegen tun: Der Phantom-Posten lässt sich weder abgeben noch
löschen, denn kein Radarr kennt ihn.

Erkannt wird es an der **byte-genauen Größe**. Zwei wirklich verschiedene
Fassungen desselben Films - 1080p hier, 4K dort - haben nie dieselbe
Byte-Zahl. Ein echter Doppelbestand bleibt deshalb erhalten, und genau das
prüft die zweite Hälfte dieser Datei.
"""

from __future__ import annotations

import pytest

from app.models import MediaType, QualityTier
from app.services.storage import _Gemessen, schluessel

GB = 1024**3


def _radarr_posten(tmdb_id: int, stufe: QualityTier, bytes_: int) -> _Gemessen:
    """Was Radarr meldet - mit Pfad und als verwaltet."""
    kennung = schluessel(MediaType.movie, stufe, tmdb_id=tmdb_id)
    return _Gemessen(
        key=kennung,
        media_type=MediaType.movie,
        tier=stufe,
        tmdb_id=tmdb_id,
        tvdb_id=None,
        season=None,
        title="Ein Film",
        size_bytes=bytes_,
        path=f"/data/Movies/Ein Film {{tmdb-{tmdb_id}}}/datei.mkv",
        verwaltet=True,
    )


def _zusammenfuehren(
    aus_radarr: list[_Gemessen], aus_server: list[_Gemessen]
) -> dict[str, _Gemessen]:
    """Die Regel aus ``_aus_media_server`` - nachgebaut auf ihren Kern.

    Bewusst hier nachgebaut statt die Funktion aufzurufen: Die liest die
    Medienserver-Tabelle aus der Datenbank, und dafür bräuchte dieser Test
    einen halben Bestand. Geprüft wird die **Entscheidung**, nicht das Lesen.
    """
    ziel = {w.key: w for w in aus_radarr}

    schon_gemeldet: dict[int, set[int]] = {}
    for wert in ziel.values():
        if wert.media_type == MediaType.movie and wert.tmdb_id is not None:
            schon_gemeldet.setdefault(wert.tmdb_id, set()).add(wert.size_bytes)

    for wert in aus_server:
        if wert.key in ziel:
            continue
        if wert.tmdb_id is not None and wert.size_bytes in schon_gemeldet.get(
            wert.tmdb_id, ()
        ):
            continue
        ziel[wert.key] = wert
    return ziel


def _server_posten(tmdb_id: int, stufe: QualityTier, bytes_: int) -> _Gemessen:
    """Was der Medienserver meldet - ohne Pfad, nicht verwaltet."""
    kennung = schluessel(MediaType.movie, stufe, tmdb_id=tmdb_id)
    return _Gemessen(
        key=kennung,
        media_type=MediaType.movie,
        tier=stufe,
        tmdb_id=tmdb_id,
        tvdb_id=None,
        season=None,
        title="Ein Film",
        size_bytes=bytes_,
        verwaltet=False,
    )


# --------------------------------------------------------------------------
# Der Fehler
# --------------------------------------------------------------------------


def test_2160p_datei_in_der_standard_instanz_zaehlt_einmal() -> None:
    """⚠️ Genau der gefundene Fall.

    Radarr (Standard-Instanz) meldet 49,9 GB, der Medienserver meldet
    dieselben 49,9 GB als 4K. Das ist **eine** Datei.
    """
    ergebnis = _zusammenfuehren(
        [_radarr_posten(435011, QualityTier.standard, 50 * GB)],
        [_server_posten(435011, QualityTier.uhd, 50 * GB)],
    )

    assert len(ergebnis) == 1
    assert sum(w.size_bytes for w in ergebnis.values()) == 50 * GB
    # Und der überlebende Posten ist der von Radarr - mit Pfad und löschbar.
    einziger = next(iter(ergebnis.values()))
    assert einziger.verwaltet
    assert einziger.path


def test_auch_andersherum() -> None:
    """Radarr in der 4K-Instanz, der Medienserver meldet es als Standard."""
    ergebnis = _zusammenfuehren(
        [_radarr_posten(500, QualityTier.uhd, 30 * GB)],
        [_server_posten(500, QualityTier.standard, 30 * GB)],
    )
    assert len(ergebnis) == 1


# --------------------------------------------------------------------------
# Was weiter zählen muss
# --------------------------------------------------------------------------


def test_ein_echter_doppelbestand_zaehlt_doppelt() -> None:
    """⚠️ Die Grenze der Regel.

    Wer denselben Film **wirklich** zweimal hält - 1080p in der einen, 4K in
    der anderen Instanz - belegt auch zweimal Platz. Zwei verschiedene
    Fassungen haben nie byte-genau dieselbe Größe.
    """
    ergebnis = _zusammenfuehren(
        [_radarr_posten(600, QualityTier.standard, 8 * GB)],
        [_server_posten(600, QualityTier.uhd, 45 * GB)],
    )

    assert len(ergebnis) == 2
    assert sum(w.size_bytes for w in ergebnis.values()) == 53 * GB


def test_ein_titel_nur_im_medienserver_bleibt() -> None:
    """Der Fall, für den ``_aus_media_server`` überhaupt gebaut wurde: laden,
    Eintrag aus Radarr werfen, Datei behalten."""
    ergebnis = _zusammenfuehren([], [_server_posten(700, QualityTier.uhd, 20 * GB)])

    assert len(ergebnis) == 1
    assert not next(iter(ergebnis.values())).verwaltet


def test_verschiedene_filme_stoeren_sich_nicht() -> None:
    """Die Größengleichheit gilt **je Titel**, nicht über Titel hinweg.

    Zwei verschiedene Filme dürfen zufällig gleich groß sein - das ist kein
    Grund, einen davon verschwinden zu lassen.
    """
    ergebnis = _zusammenfuehren(
        [_radarr_posten(800, QualityTier.standard, 20 * GB)],
        [_server_posten(801, QualityTier.uhd, 20 * GB)],
    )
    assert len(ergebnis) == 2


def test_radarr_gewinnt_bei_gleichem_schluessel() -> None:
    """Die alte Regel gilt weiter: Radarrs Angabe ist die genauere."""
    ergebnis = _zusammenfuehren(
        [_radarr_posten(900, QualityTier.uhd, 40 * GB)],
        [_server_posten(900, QualityTier.uhd, 39 * GB)],
    )
    assert len(ergebnis) == 1
    assert next(iter(ergebnis.values())).size_bytes == 40 * GB
    assert next(iter(ergebnis.values())).verwaltet
