"""Zwei Instanzen, eine Kategorie.

⚠️ **Der Fehler, den heute niemand findet.** Teilen sich zwei Instanzen eine
Kategorie im Download-Programm, greift jede nach den Downloads der anderen.
Anfragen haengen, Dateien landen falsch - und weil nirgends ein Fehler steht,
sucht der Betreiber beim Netz. Radarr kann nicht warnen: Es kennt die zweite
Instanz gar nicht. Nexview kennt beide.

⚠️ **Die Werte stammen aus einer echten Anlage** (29.08.2026): drei Instanzen
auf demselben SABnzbd unter ``10.10.10.109:8080``, mit den Kategorien
``movies``, ``movies-4k`` und ``tv``. Erfundene Beispiele haetten den Fall
verfehlt, dass das Kategoriefeld je Dienst anders heisst.
"""

from __future__ import annotations

import pytest

from app.services import download_kollision as kollision


def programm(art="Sabnzbd", host="10.10.10.109", port=8080, an=True, **felder):
    """Ein Download-Programm, wie es die Instanz beschreibt."""
    liste = [{"name": "host", "value": host}, {"name": "port", "value": port}]
    liste += [{"name": k, "value": v} for k, v in felder.items()]
    return {"enable": an, "implementation": art, "fields": liste}


ECHTE_ANLAGE = [
    ("radarr-standard", "Radarr FHD", [programm(movieCategory="movies")]),
    ("radarr-uhd", "Radarr 4K", [programm(movieCategory="movies-4k")]),
    ("sonarr-standard", "Sonarr FHD", [programm(tvCategory="tv")]),
]


def test_getrennte_kategorien_sind_still():
    """Die gemessene Anlage ist in Ordnung - und darf nichts melden."""
    assert kollision.finden(ECHTE_ANLAGE) == []


def test_zwei_radarr_auf_derselben_kategorie():
    """Der Fall, in den der Betreiber selbst gelaufen ist."""
    anlage = list(ECHTE_ANLAGE)
    anlage[1] = ("radarr-uhd", "Radarr 4K", [programm(movieCategory="movies")])

    [treffer] = kollision.finden(anlage)
    assert treffer.kategorie == "movies"
    assert treffer.instanzen == ["Radarr FHD", "Radarr 4K"]
    assert treffer.programm_name == "Sabnzbd auf 10.10.10.109:8080"


def test_auch_eine_dritte_instanz_kommt_dazu():
    """⚠️ Nicht nur Paare - drei auf einer Kategorie sind ein Treffer, nicht drei."""
    anlage = [
        ("a", "Radarr A", [programm(movieCategory="movies")]),
        ("b", "Radarr B", [programm(movieCategory="movies")]),
        ("c", "Radarr C", [programm(movieCategory="movies")]),
    ]
    [treffer] = kollision.finden(anlage)
    assert treffer.instanzen == ["Radarr A", "Radarr B", "Radarr C"]


def test_radarr_und_sonarr_kollidieren_ueber_verschiedene_feldnamen():
    """⚠️ Der Fall, der bei erfundenen Testdaten durchgerutscht waere.

    Radarr nennt das Feld ``movieCategory``, Sonarr ``tvCategory``. Verglichen
    wird der **Wert**: Steht in beiden ``downloads``, sehen beide dieselbe
    Warteschlange - der verschiedene Feldname aendert daran nichts.
    """
    [treffer] = kollision.finden([
        ("radarr-standard", "Radarr", [programm(movieCategory="downloads")]),
        ("sonarr-standard", "Sonarr", [programm(tvCategory="downloads")]),
    ])
    assert treffer.kategorie == "downloads"
    assert treffer.instanzen == ["Radarr", "Sonarr"]


def test_gross_und_kleinschreibung_zaehlt_nicht():
    """SABnzbd unterscheidet bei Kategorienamen nicht - der Vergleich auch nicht."""
    [treffer] = kollision.finden([
        ("a", "A", [programm(movieCategory="Movies")]),
        ("b", "B", [programm(movieCategory="movies")]),
    ])
    assert treffer.kategorie == "movies"


def test_verschiedene_downloader_kollidieren_nicht():
    """Zwei Programme auf verschiedenen Rechnern teilen sich nichts."""
    assert kollision.finden([
        ("a", "A", [programm(host="10.10.10.109", movieCategory="movies")]),
        ("b", "B", [programm(host="10.10.10.200", movieCategory="movies")]),
    ]) == []


def test_verschiedene_anschluesse_kollidieren_nicht():
    """Zwei SABnzbd auf demselben Rechner, aber verschiedenen Anschluessen."""
    assert kollision.finden([
        ("a", "A", [programm(port=8080, movieCategory="movies")]),
        ("b", "B", [programm(port=8081, movieCategory="movies")]),
    ]) == []


def test_verschiedene_programmarten_kollidieren_nicht():
    """SABnzbd und qBittorrent auf demselben Rechner sind zwei Warteschlangen."""
    assert kollision.finden([
        ("a", "A", [programm(art="Sabnzbd", port=8080, movieCategory="movies")]),
        ("b", "B", [programm(art="QBittorrent", port=8080, movieCategory="movies")]),
    ]) == []


def test_abgeschaltete_programme_zaehlen_nicht():
    """⚠️ Ein abgeschaltetes Programm laedt nichts - es kann nichts wegnehmen.

    Es trotzdem zu melden waere eine Warnung ohne Folge, und die naechste
    echte gliche ihr zum Verwechseln.
    """
    assert kollision.finden([
        ("a", "A", [programm(movieCategory="movies")]),
        ("b", "B", [programm(an=False, movieCategory="movies")]),
    ]) == []


def test_leere_kategorie_auf_beiden_seiten_ist_der_schwerere_fall():
    """⚠️ Radarr selbst: die Kategorie ist "dringend empfohlen".

    Ohne sie greift eine Instanz nach allem, was im Programm liegt - nicht nur
    nach dem einer zweiten. Genau deshalb wird der Leerfall mitgemeldet.
    """
    [treffer] = kollision.finden([
        ("a", "A", [programm()]),
        ("b", "B", [programm()]),
    ])
    assert treffer.ohne_kategorie
    assert treffer.instanzen == ["A", "B"]


def test_eine_leere_und_eine_gesetzte_kategorie_gelten_nicht_als_gleich():
    """⚠️ Hier bleibt die Pruefung bewusst stumm.

    Ob eine Instanz ohne Kategorie der anderen wirklich ins Gehege kommt,
    haengt am Programm - SABnzbd legt Unkategorisiertes woanders ab. Eine
    Warnung, die nur manchmal stimmt, kostet mehr Vertrauen als sie einbringt.
    Der Fall "beide leer" ist dagegen eindeutig.
    """
    assert kollision.finden([
        ("a", "A", [programm()]),
        ("b", "B", [programm(movieCategory="movies")]),
    ]) == []


def test_dieselbe_bahn_zweimal_an_einer_instanz_ist_keine_kollision():
    """Wer zwei Zugaenge zum selben Programm hat, kollidiert nicht mit sich."""
    assert kollision.finden([
        ("a", "A", [programm(movieCategory="movies"), programm(movieCategory="movies")]),
    ]) == []


def test_mehrere_programme_werden_einzeln_verglichen():
    """⚠️ Wer zwei Downloader betreibt und nur bei einem kollidiert, soll das erfahren."""
    treffer = kollision.finden([
        ("a", "A", [
            programm(art="Sabnzbd", port=8080, movieCategory="movies"),
            programm(art="QBittorrent", port=8081, movieCategory="filme"),
        ]),
        ("b", "B", [
            programm(art="Sabnzbd", port=8080, movieCategory="movies-4k"),
            programm(art="QBittorrent", port=8081, movieCategory="filme"),
        ]),
    ])
    assert [t.programm_name for t in treffer] == ["QBittorrent auf 10.10.10.109:8081"]


def test_blackhole_kollidiert_ueber_den_ordner():
    """⚠️ Programme ohne Kategorie arbeiten ueber einen Ordner.

    Zwei Instanzen, die aus demselben Ordner importieren, nehmen sich
    gegenseitig die Dateien weg - dasselbe Problem, andere Bezeichnung.
    """
    def blackhole(ordner):
        return {
            "enable": True, "implementation": "UsenetBlackhole",
            "fields": [{"name": "watchFolder", "value": ordner}],
        }

    [treffer] = kollision.finden([
        ("a", "A", [blackhole("/downloads/fertig")]),
        ("b", "B", [blackhole("/downloads/fertig")]),
    ])
    assert treffer.kategorie == "/downloads/fertig"


def test_die_nach_dem_import_gesetzte_kategorie_zaehlt_nicht():
    """⚠️ Dorthin wandert Fertiges - danach greift keine Instanz mehr zu.

    Sie mitzuvergleichen erzeugte eine Warnung fuer eine voellig uebliche
    Einstellung.
    """
    assert kollision.finden([
        ("a", "A", [programm(movieCategory="movies", movieImportedCategory="fertig")]),
        ("b", "B", [programm(movieCategory="movies-4k", movieImportedCategory="fertig")]),
    ]) == []


def test_ohne_programme_passiert_nichts():
    """Eine Instanz ohne Download-Programm kann nichts wegnehmen."""
    assert kollision.finden([("a", "A", []), ("b", "B", None)]) == []


@pytest.mark.parametrize("wie_oft", [2, 3])
def test_der_schluessel_nennt_die_beteiligten(wie_oft):
    """⚠️ Wer die Warnung wegklickt, soll sie bei einer **dritten** wiedersehen.

    Das ist ein neuer Fehler, kein weggeklickter alter - also gehoeren die
    beteiligten Instanzen in die Kennung.
    """
    anlage = [
        (f"i{n}", f"Instanz {n}", [programm(movieCategory="movies")])
        for n in range(wie_oft)
    ]
    [treffer] = kollision.finden(anlage)
    for n in range(wie_oft):
        assert f"i{n}" in treffer.schluessel
