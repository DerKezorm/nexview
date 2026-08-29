"""Die Pfad-Zuordnung zwischen Instanz und Medienserver.

⚠️ **Warum das eigene Tests verdient.** Der Fehler, den diese Rechnung
verhindert, ist der unangenehmste im ganzen Bereich: Ohne ``mapFrom``/``mapTo``
prueft sich die Verbindung **gruen**, steht in der Liste, und tut trotzdem
nichts. Ein falsches Ergebnis faellt also niemandem auf. Deshalb steht hier
nicht nur, was herauskommen soll, sondern auch, wo geschwiegen werden muss.
"""

from app.services.pfad_zuordnung import ableiten


def test_verschiedene_wurzeln_werden_umgeschrieben():
    """Der Normalfall: zwei Container, zwei Sichten auf dieselbe Datei."""
    z = ableiten(["/data/Movies"], ["/media/Movies"])
    assert (z.von, z.nach) == ("/data", "/media")
    assert not z.hindernis
    # Die Belege gehoeren dazu - ohne sie kann niemand die Rechnung pruefen.
    assert z.beispiel_arr == "/data/Movies"
    assert z.beispiel_server == "/media/Movies"


def test_gleiche_wurzeln_brauchen_keine_umschreibung():
    """Sehen beide dasselbe, bleiben die Felder leer - und das ist richtig."""
    z = ableiten(["/media/Movies"], ["/media/Movies"])
    assert (z.von, z.nach) == ("", "")
    assert not z.hindernis


def test_laengstes_gemeinsames_ende_gewinnt():
    """Bei mehreren Kandidaten zaehlt der mit der groessten Uebereinstimmung."""
    z = ableiten(
        ["/data/Filme/Movies"],
        ["/media/Movies", "/srv/Filme/Movies"],
    )
    assert (z.von, z.nach) == ("/data", "/srv")


def test_ohne_gemeinsames_ende_wird_geschwiegen():
    """⚠️ Der wichtigste Fall: lieber nichts sagen als etwas erfinden.

    Hier fuehrt der Medienserver schlicht keine passende Bibliothek. Ein
    geratenes ``/`` waere schlimmer als gar nichts, weil es einen Fehler
    festschreibt, den danach niemand mehr sucht.
    """
    z = ableiten(["/data/Movies4K"], ["/media/Movies"])
    assert z.hindernis == "kein_treffer"
    assert (z.von, z.nach) == ("", "")


def test_leere_seiten_werden_unterschieden():
    """"Weiss ich nicht" hat zwei Ursachen, und beide bekommen einen Namen."""
    # Keine Stammordner auf der Instanz ...
    assert ableiten([], ["/media/Movies"]).hindernis == "keine_wurzeln"
    # ... und keine Bibliothekspfade vom Medienserver.
    assert ableiten(["/data/Movies"], []).hindernis == "keine_pfade"


def test_windows_und_unix_lassen_sich_vergleichen():
    """Die Trennzeichen duerfen sich unterscheiden - die Namen zaehlen."""
    z = ableiten(["C:\\Filme\\Movies"], ["/media/Movies"])
    assert z.von == "C:\\Filme"
    assert z.nach == "/media"


def test_grossschreibung_steht_nicht_im_weg():
    """Windows und manche Freigaben schreiben Pfade unterschiedlich gross."""
    z = ableiten(["/data/movies"], ["/media/Movies"])
    assert (z.von, z.nach) == ("/data", "/media")
