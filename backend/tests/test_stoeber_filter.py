"""Die freie Auswahl - sechs menschliche Fragen statt dreizehn Reglern."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.services import stoebern
from app.services.filters import (
    BEKANNTE_TITEL_STIMMEN,
    EGAL_STIMMEN,
    EGAL_STIMMEN_TV,
    FILTER_JAHRZEHNTE,
    LAUFZEITEN,
    MIN_FEATURE_RUNTIME,
    PERLEN_MINDESTALTER_JAHRE,
)

HEUTE = date(2026, 8, 23)


def wahl(**felder) -> stoebern.Wahl:
    return stoebern.Wahl(**felder)


# --- Zeitraum --------------------------------------------------------------


def test_jahrzehnt_wird_zu_echten_jahren() -> None:
    """Der ganze Sinn dieser Seite.

    Die Entdecken-Seite kennt nur relative Fenster (7/30/90/365 Tage). Damit
    ist der Rueckkatalog schlicht unerreichbar - alles vor einem Jahr existiert
    dort nicht.
    """
    assert stoebern.zeitraum("1990", HEUTE) == ("1990-01-01", "1999-12-31")
    assert stoebern.zeitraum("2020", HEUTE) == ("2020-01-01", "2029-12-31")


def test_aelter_endet_vor_dem_aeltesten_jahrzehnt() -> None:
    """Sonst gaebe es eine Luecke zwischen "vor 1970" und den 70ern."""
    _, bis = stoebern.zeitraum("aelter", HEUTE)
    assert bis == f"{FILTER_JAHRZEHNTE[-1] - 1}-12-31"


def test_aktuell_ist_relativ() -> None:
    von, bis = stoebern.zeitraum("aktuell", HEUTE)
    assert von == "2023-01-01"
    assert bis is None


@pytest.mark.parametrize("epoche", ["egal", "", "quatsch", "1955", "99999"])
def test_unbekannte_epoche_schraenkt_nicht_ein(epoche: str) -> None:
    """Der Wert kommt aus der Adresszeile - ein Tippfehler darf keine
    Fehlerseite ergeben, sondern nur den Filter weglassen."""
    assert stoebern.zeitraum(epoche, HEUTE) == (None, None)


# --- Von der Auswahl zum Filter -------------------------------------------


def test_zeit_wird_zur_laufzeit() -> None:
    filter_ = stoebern.filter_aus_wahl(wahl(zeit="kurz"), "movie", heute=HEUTE)
    assert filter_.max_runtime == 90
    assert filter_.min_runtime == MIN_FEATURE_RUNTIME


def test_die_stufen_stossen_lueckenlos_aneinander() -> None:
    """⚠️ Vorher fiel jeder Film zwischen 126 und 129 Minuten durch **beide**
    Optionen und war ueber die Laufzeit gar nicht auffindbar.

    Solche Luecken sieht man nicht - man merkt nur, dass ein Titel "irgendwie
    nie kommt".
    """
    _, bis_mittel = LAUFZEITEN["mittel"]
    ab_lang, _ = LAUFZEITEN["lang"]
    assert bis_mittel is not None and ab_lang is not None
    assert ab_lang == bis_mittel + 1, "zwischen 'mittel' und 'lang' klafft eine Luecke"

    _, bis_kurz = LAUFZEITEN["kurz"]
    assert bis_kurz is not None and bis_kurz < bis_mittel


def test_lang_hat_keine_obergrenze() -> None:
    """Das Etikett verspricht "ueber zwei Stunden" - nach oben offen."""
    assert LAUFZEITEN["lang"][1] is None


def test_leiste_und_assistent_meinen_dasselbe() -> None:
    """Zwei Tabellen liefen beim ersten Feinschliff auseinander.

    Die Leiste verspraeche 90 Minuten und lieferte 95 - und das faellt
    niemandem auf, weil beide Seiten fuer sich stimmig aussehen.
    """
    from app.services import filmabend

    assert filmabend.ZEITEN is LAUFZEITEN


def test_serien_bekommen_keine_laufzeit() -> None:
    """Bei Serien waere das die Folgenlaenge."""
    filter_ = stoebern.filter_aus_wahl(wahl(zeit="kurz"), "tv", heute=HEUTE)
    assert filter_.max_runtime is None
    assert filter_.min_runtime is None


def test_genres_werden_ein_und_ausgeschlossen() -> None:
    filter_ = stoebern.filter_aus_wahl(
        wahl(genres=(35, 18), ohne_genres=(27,)), "movie", heute=HEUTE
    )
    assert filter_.genres_or == "35|18"
    assert filter_.without_genres == "27"


def test_geheimtipp_setzt_fenster_und_altersgrenze() -> None:
    filter_ = stoebern.filter_aus_wahl(wahl(bekanntheit="geheimtipp"), "movie", heute=HEUTE)
    assert filter_.min_votes is not None and filter_.max_votes is not None
    assert filter_.date_to == f"{2026 - PERLEN_MINDESTALTER_JAHRE}-12-31"


def test_geheimtipp_weicht_ein_engeres_jahrzehnt_nicht_auf() -> None:
    """Die schaerfere der beiden Grenzen muss gewinnen."""
    filter_ = stoebern.filter_aus_wahl(
        wahl(bekanntheit="geheimtipp", epoche="1990"), "movie", heute=HEUTE
    )
    assert filter_.date_to == "1999-12-31"


def test_bekannt_setzt_eine_hohe_untergrenze() -> None:
    filter_ = stoebern.filter_aus_wahl(wahl(bekanntheit="bekannt"), "movie", heute=HEUTE)
    assert filter_.min_votes == BEKANNTE_TITEL_STIMMEN
    assert filter_.max_votes is None


def test_egal_ist_nicht_null() -> None:
    """Ohne jede Untergrenze besteht der Rueckkatalog aus Rauschen.

    Aber niedriger als bei den Genre-Regalen, sonst bleibt ein Nischen-Genre
    zusammen mit einem Jahrzehnt leer.
    """
    film = stoebern.filter_aus_wahl(wahl(), "movie", heute=HEUTE)
    serie = stoebern.filter_aus_wahl(wahl(), "tv", heute=HEUTE)
    assert film.min_votes == EGAL_STIMMEN
    assert serie.min_votes == EGAL_STIMMEN_TV
    assert serie.min_votes < film.min_votes


def test_kurzfilme_fliegen_immer_raus() -> None:
    """Kein Schalter, sondern Grundeinstellung.

    Auf der alten Seite ist "Nur Spielfilme" eine Checkbox, die man erst
    finden muss - und die nur existiert, weil TMDBs Neuerscheinungen von
    Zwei-Minuten-Beitraegen ueberschwemmt sind.
    """
    assert stoebern.filter_aus_wahl(wahl(), "movie").min_runtime == MIN_FEATURE_RUNTIME


def test_jede_kombination_bleibt_widerspruchsfrei() -> None:
    epochen = ("egal", "aktuell", "aelter", *(str(j) for j in FILTER_JAHRZEHNTE))
    for zeit in LAUFZEITEN:
        for epoche in epochen:
            for bekanntheit in ("egal", "bekannt", "geheimtipp"):
                for media_type in ("movie", "tv"):
                    f = stoebern.filter_aus_wahl(
                        wahl(zeit=zeit, epoche=epoche, bekanntheit=bekanntheit),
                        media_type,
                        heute=HEUTE,
                    )
                    if f.date_from and f.date_to:
                        assert f.date_from <= f.date_to, (zeit, epoche, bekanntheit)
                    if f.min_votes and f.max_votes:
                        assert f.min_votes < f.max_votes
                    if f.min_runtime and f.max_runtime:
                        assert f.min_runtime < f.max_runtime


# --- Ueber die Schnittstelle ----------------------------------------------


def test_ohne_anmeldung_kein_zugriff(client: TestClient) -> None:
    assert client.get("/api/stoebern/filter/movie").status_code == 401


def test_filter_liefert_eine_seite(admin_client: TestClient) -> None:
    antwort = admin_client.get("/api/stoebern/filter/movie?zeit=kurz&epoche=1990")
    assert antwort.status_code == 200
    seite = antwort.json()
    assert seite["jahrzehnte"] == list(FILTER_JAHRZEHNTE)
    assert isinstance(seite["items"], list)


def test_unsinnige_genreliste_ergibt_keine_fehlerseite(admin_client: TestClient) -> None:
    """Die Liste kommt aus der Adresszeile.

    Ein Tippfehler darf keine 422 ergeben - der Mensch saehe dann eine
    Fehlerseite statt einer Titelliste und wuesste nicht, warum.
    """
    antwort = admin_client.get("/api/stoebern/filter/movie?genres=abc,,35,-4")
    assert antwort.status_code == 200


def test_unbekannte_sortierung_wird_abgelehnt(admin_client: TestClient) -> None:
    """Anders als die Genreliste: Das ist eine feste Auswahl, kein Freitext."""
    assert admin_client.get("/api/stoebern/filter/movie?sortierung=quatsch").status_code == 422


def test_ungueltige_medienart_wird_abgelehnt(admin_client: TestClient) -> None:
    assert admin_client.get("/api/stoebern/filter/buecher").status_code == 422
