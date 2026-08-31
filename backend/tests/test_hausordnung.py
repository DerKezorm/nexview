"""Hausordnung, Stufe 1: Datenmodell und Bilderablage.

Hier ist noch nichts sichtbar. Geprueft wird der Unterbau: dass die Tabelle
mit den richtigen Vorgaben entsteht, und dass die Bilderablage genau das
annimmt, was ein Browser gefahrlos darstellen kann - und sonst nichts.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal
from app.models import Hausordnung, User
from app.services import hausordnung_bilder

# Ein gueltiges Bild muss nur an den ersten Bytes erkennbar sein - der Rest
# interessiert weder die Pruefung noch diesen Test.
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPG = b"\xff\xd8\xff" + b"0" * 64
WEBP = b"RIFF" + b"0000" + b"WEBP" + b"0" * 64


@pytest.fixture(autouse=True)
def leerer_bilderordner():
    """Jeder Test faengt ohne fremde Bilder an - und hinterlaesst keine."""
    ordner = get_settings().data_dir / "hausordnung"
    shutil.rmtree(ordner, ignore_errors=True)
    yield
    shutil.rmtree(ordner, ignore_errors=True)


# ---------------------------------------------------------------------------
# Datenmodell
# ---------------------------------------------------------------------------


def test_die_tabelle_entsteht_mit_den_richtigen_vorgaben(admin_client: TestClient) -> None:
    """⚠️ **``veroeffentlicht`` ist aus, ``quittierbar`` an.**

    Andersherum waere beides falsch: Eine halbfertige Hausordnung darf nicht
    ab dem ersten Speichern fuer alle sichtbar sein, und ein Knopf, der nie
    verschwindet, nervt auf Dauer mehr als einer, den man einmal wegklickt.
    """
    with SessionLocal() as sitzung:
        sitzung.add(Hausordnung(id=1, titel="Bei uns zu Hause", inhalt="## Regeln"))
        sitzung.commit()

        gespeichert = sitzung.get(Hausordnung, 1)
        assert gespeichert is not None
        assert gespeichert.fassung == 1
        assert gespeichert.quittierbar is True
        assert gespeichert.veroeffentlicht is False
        assert gespeichert.aktualisiert_am is not None


def test_konten_starten_ohne_quittung(admin_client: TestClient) -> None:
    """NULL heisst "noch nie gelesen" - dann traegt der Knopf einen Punkt."""
    with SessionLocal() as sitzung:
        konto = sitzung.query(User).first()
        assert konto is not None
        assert konto.hausordnung_gelesen is None
        assert konto.hausordnung_gelesen_am is None


# ---------------------------------------------------------------------------
# Bilder: was hineindarf
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inhalt", "endung"),
    [(PNG, "png"), (JPG, "jpg"), (WEBP, "webp")],
)
def test_gaengige_bildformate_gehen_durch(inhalt: bytes, endung: str) -> None:
    bild = hausordnung_bilder.ablegen(inhalt)
    assert bild.name.endswith(f".{endung}")
    assert bild.bytes == len(inhalt)

    zurueck, inhaltstyp = hausordnung_bilder.lesen(bild.name)
    assert zurueck == inhalt
    assert inhaltstyp.startswith("image/")


def test_der_name_kommt_nie_vom_absender() -> None:
    """Ein mitgelieferter Dateiname kann Pfadanteile tragen - und verraet
    nebenbei, wie die Datei auf fremden Rechnern hiess."""
    erstes = hausordnung_bilder.ablegen(PNG)
    zweites = hausordnung_bilder.ablegen(PNG)
    assert erstes.name != zweites.name
    assert len(erstes.name.split(".")[0]) == 32


def test_svg_wird_abgewiesen() -> None:
    """⚠️ Der wichtigste Fall dieser Datei.

    Eine SVG-Datei darf Skripte enthalten. Sie auszuliefern hiesse, jedem,
    der ein Bild hochladen darf, Code im Kontext der Seite zu erlauben - und
    die Hausordnung sieht jeder.
    """
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(hausordnung_bilder.BildFehler):
        hausordnung_bilder.ablegen(svg)


def test_umbenannte_datei_wird_abgewiesen() -> None:
    """Geprueft werden die ersten Bytes, nicht die Endung."""
    with pytest.raises(hausordnung_bilder.BildFehler):
        hausordnung_bilder.ablegen(b"Das hier ist einfach nur Text.")


def test_leere_datei_wird_abgewiesen() -> None:
    with pytest.raises(hausordnung_bilder.BildFehler):
        hausordnung_bilder.ablegen(b"")


def test_zu_grosses_bild_wird_abgewiesen() -> None:
    zu_gross = PNG + b"0" * hausordnung_bilder.MAX_BYTES
    with pytest.raises(hausordnung_bilder.BildFehler) as fehler:
        hausordnung_bilder.ablegen(zu_gross)
    # Die Meldung nennt beide Zahlen - sonst raet der Betreiber, wie weit er
    # verkleinern muss.
    assert "KB" in fehler.value.message


def test_die_hoechstzahl_greift(monkeypatch) -> None:
    """Nicht, weil dreissig knapp waeren - sondern damit ein versehentlicher
    Stapel-Upload nicht den Datentraeger fuellt."""
    monkeypatch.setattr(hausordnung_bilder, "HOECHSTENS", 3)
    for _ in range(3):
        hausordnung_bilder.ablegen(PNG)

    with pytest.raises(hausordnung_bilder.BildFehler) as fehler:
        hausordnung_bilder.ablegen(PNG)
    assert "3" in fehler.value.message


# ---------------------------------------------------------------------------
# Bilder: lesen und loeschen
# ---------------------------------------------------------------------------


def test_pfad_tricks_im_namen_laufen_ins_leere() -> None:
    """Der Name kommt aus der Adresszeile - er wird auf den reinen Dateinamen
    reduziert, sonst liesse sich damit im Dateisystem spazieren gehen.

    ⚠️ **Der Test legt dafuer ein echtes, gueltiges Bild ausserhalb des
    Ordners ab.** Ohne das war er hohl: Ein Versuch wie ``../../nexview.db``
    scheitert auch ohne jede Pfad-Pruefung - nur eben an "das ist kein Bild"
    statt an "der Pfad zeigt hinaus". Nachgemessen: Die Pruefung liess sich
    entfernen, ohne dass ein einziger Test rot wurde.
    """
    daneben = get_settings().data_dir / "nebenan.png"
    daneben.write_bytes(PNG)
    try:
        # Genau diese Datei darf nicht herauskommen - egal, wie man sie
        # anspricht.
        for versuch in ("../nebenan.png", "..\\nebenan.png", "./../nebenan.png"):
            with pytest.raises(hausordnung_bilder.BildFehler):
                hausordnung_bilder.lesen(versuch)
    finally:
        daneben.unlink(missing_ok=True)


def test_unbekanntes_bild_meldet_sich() -> None:
    with pytest.raises(hausordnung_bilder.BildFehler):
        hausordnung_bilder.lesen("gibtesnicht.png")


def test_loeschen_entfernt_und_meldet_ehrlich() -> None:
    bild = hausordnung_bilder.ablegen(PNG)
    assert hausordnung_bilder.loeschen(bild.name) is True
    # Ein zweites Mal gibt es nichts mehr zu loeschen.
    assert hausordnung_bilder.loeschen(bild.name) is False
    assert hausordnung_bilder.alle() == []


def test_auflisten_zeigt_alle_mit_groesse() -> None:
    """⚠️ **Ohne Aussage ueber die Reihenfolge.**

    Der Dienst sortiert nach Aenderungszeit; zwei Dateien, die in derselben
    Millisekunde entstehen, stehen in zufaelliger Reihenfolge. Ein Test, der
    darauf besteht, ist mal gruen und mal rot - und genau so ist er einmal im
    vollen Lauf umgefallen, waehrend er allein durchlief.
    """
    erstes = hausordnung_bilder.ablegen(PNG)
    zweites = hausordnung_bilder.ablegen(JPG)

    alle = hausordnung_bilder.alle()
    assert {bild.name for bild in alle} == {erstes.name, zweites.name}
    assert all(bild.bytes > 0 for bild in alle)
