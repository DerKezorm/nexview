"""Stoebern: die Regale und das serverseitige Sieb nach dem eigenen Bestand."""

from __future__ import annotations

import ast
import pathlib
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.schemas_media import MediaItem
from app.services import stoebern
from app.services.filters import (
    GENRE_STIMMEN,
    GENRE_STIMMEN_DOKU,
    KLASSIKER_MINDESTALTER_JAHRE,
    MIN_FEATURE_RUNTIME,
    PERLEN_MINDESTALTER_JAHRE,
    PERLEN_STIMMEN,
)


def _titel(tmdb_id: int, status: str = "not_requested") -> MediaItem:
    return MediaItem(media_type="movie", tmdb_id=tmdb_id, title=f"Film {tmdb_id}", status=status)


# --- Die Regale ------------------------------------------------------------


@pytest.mark.parametrize("media_type", ["movie", "tv"])
def test_jedes_angebotene_regal_liefert_gueltige_filter(media_type: str) -> None:
    """Was die Uebersicht anbietet, muss auch abrufbar sein.

    Sonst fuehrt eine Kachel auf eine 404 - und die Kennungen kommen aus einer
    Liste, nicht aus der Eingabe eines Menschen.
    """
    for regal in stoebern.regale_fuer(media_type):
        filter_ = stoebern.filter_fuer(regal.kennung, media_type)
        assert filter_.page == 1
        # Der Schluessel muss sich je Regal unterscheiden, sonst liefert der
        # Zwischenspeicher einem Regal den Inhalt eines anderen.
        assert filter_.cache_key(media_type)


@pytest.mark.parametrize("media_type", ["movie", "tv"])
def test_kennungen_sind_eindeutig(media_type: str) -> None:
    kennungen = [r.kennung for r in stoebern.regale_fuer(media_type)]
    assert len(kennungen) == len(set(kennungen))


@pytest.mark.parametrize("media_type", ["movie", "tv"])
def test_regale_haben_verschiedene_zwischenspeicher_schluessel(media_type: str) -> None:
    schluessel = {
        stoebern.filter_fuer(r.kennung, media_type).cache_key(media_type)
        for r in stoebern.regale_fuer(media_type)
    }
    assert len(schluessel) == len(stoebern.regale_fuer(media_type))


def test_perlen_setzen_beide_stimmengrenzen() -> None:
    """Erst das **Fenster** macht die Perle aus.

    Nur eine Untergrenze liefert die grossen Blockbuster, nur eine Obergrenze
    liefert Rauschen. Beides zusammen ist der eigentliche Rauschfilter.
    """
    filter_ = stoebern.filter_fuer("perlen", "movie")
    assert filter_.min_votes == PERLEN_STIMMEN[0]
    assert filter_.max_votes == PERLEN_STIMMEN[1]
    assert filter_.min_votes < filter_.max_votes
    assert filter_.min_rating is not None


def test_perlen_haben_eine_altersgrenze() -> None:
    """Der Fehler, der beim ersten Blick auf die echte Seite aufflog.

    Die Stimmenzahl misst **nicht** Unbekanntheit, sondern Alter mal
    Beliebtheit: Ein Kinofilm von letzter Woche hat ein paar hundert Stimmen -
    genau wie eine echte Perle von 2015. Ohne Altersgrenze bestand das Regal
    aus lauter aktuellen Blockbustern; gemessen waren 7 von 8 Treffern aus
    2026, darunter Spider-Man und Toy Story 5.

    Ohne diesen Test kommt der Fehler beim naechsten Feinschliff an den
    Grenzen zurueck, und er sieht nicht wie ein Fehler aus - nur wie eine
    Liste, die man schon kennt.
    """
    filter_ = stoebern.filter_fuer("perlen", "movie", heute=date(2026, 8, 23))
    assert filter_.date_to is not None
    assert filter_.date_to <= f"{2026 - PERLEN_MINDESTALTER_JAHRE}-12-31"


def test_klassiker_liegen_in_der_vergangenheit() -> None:
    filter_ = stoebern.filter_fuer("klassiker", "movie", heute=date(2026, 8, 23))
    assert filter_.date_to == f"{2026 - KLASSIKER_MINDESTALTER_JAHRE}-12-31"
    assert filter_.date_from is None


def test_jahrzehnt_deckt_zehn_jahre_ab() -> None:
    filter_ = stoebern.filter_fuer("jahrzehnt_1990", "movie")
    assert filter_.date_from == "1990-01-01"
    assert filter_.date_to == "1999-12-31"


def test_kurzer_abend_setzt_eine_obergrenze() -> None:
    """Die Frage, die die Entdecken-Seite nie stellen konnte."""
    filter_ = stoebern.filter_fuer("kurz", "movie")
    assert filter_.max_runtime is not None
    assert filter_.max_runtime > MIN_FEATURE_RUNTIME


def test_kurzer_abend_gibt_es_bei_serien_nicht() -> None:
    """Folgenlaenge ist etwas anderes als Filmlaenge."""
    assert all(r.kennung != "kurz" for r in stoebern.regale_fuer("tv"))
    with pytest.raises(stoebern.UnbekanntesRegal):
        stoebern.filter_fuer("kurz", "tv")


def test_serien_bekommen_keine_mindestlaufzeit() -> None:
    """``min_runtime`` bei Serien wuerde die Folgenlaenge meinen.

    Vierzig Minuten Mindestlaenge je Folge wuerfe die halbe Komoedien-Landschaft
    heraus - Sitcoms laufen zwanzig Minuten.
    """
    for regal in stoebern.regale_fuer("tv"):
        assert stoebern.filter_fuer(regal.kennung, "tv").min_runtime is None


def test_filme_blenden_kurzfilme_immer_aus() -> None:
    """Kein Schalter, sondern Grundeinstellung."""
    for regal in stoebern.regale_fuer("movie"):
        if regal.kennung == "kurz":
            continue
        assert stoebern.filter_fuer(regal.kennung, "movie").min_runtime == MIN_FEATURE_RUNTIME


def test_genre_regale_sortieren_nach_note() -> None:
    """Sonst zeigt der Rueckkatalog nur das laufende Jahr.

    Mit "popularity.desc" bestand jedes Genre-Regal ausschliesslich aus Titeln
    von 2026 - fuer eine Seite, deren ganzer Zweck der *Rueckkatalog* ist,
    genau falsch herum. Nach Note kommen Parasite, Pulp Fiction und
    Zurueck in die Zukunft.
    """
    for media_type in ("movie", "tv"):
        for regal in stoebern.regale_fuer(media_type):
            if regal.kategorie == "genre":
                assert stoebern.filter_fuer(regal.kennung, media_type).sort == "rating"


def test_dokumentationen_haben_eine_eigene_untergrenze() -> None:
    """Dokumentarfilme sammeln kaum Stimmen.

    Mit der allgemeinen Grenze von 1000 blieben bei Filmen 26 und bei Serien
    6 Treffer uebrig - ein Regal aus einer halben Zeile, das aussieht, als sei
    es kaputt.
    """
    doku = stoebern.filter_fuer("genre_doku", "movie").min_votes
    anderes = stoebern.filter_fuer("genre_drama", "movie").min_votes
    assert doku == GENRE_STIMMEN_DOKU
    assert anderes == GENRE_STIMMEN
    assert doku < anderes


def test_serien_haben_niedrigere_grenzen_als_filme() -> None:
    """Dieselbe Zahl ergibt bei Serien ein leeres statt eines strengen Regals."""
    for kennung in ("perlen", "klassiker", "jahrzehnt_1990", "genre_drama"):
        film = stoebern.filter_fuer(kennung, "movie").min_votes
        serie = stoebern.filter_fuer(kennung, "tv").min_votes
        assert film is not None and serie is not None
        assert serie < film, f"{kennung}: Serien brauchen eine niedrigere Grenze"


def test_serien_haben_keine_alten_jahrzehnte() -> None:
    """TMDB hat fuer altes Fernsehen schlicht keine Daten.

    Gemessen: Die 1970er ergeben bei Serien selbst mit einer Untergrenze von
    50 Stimmen nur 20 Treffer - eine einzige Seite mit Wrestling, Talkshows
    und Telenovelas. Lieber gar kein Regal als eines, das kaputt wirkt.
    """
    tv = {r.kennung for r in stoebern.regale_fuer("tv")}
    assert "jahrzehnt_1970" not in tv
    assert "jahrzehnt_1980" not in tv
    assert "jahrzehnt_1990" in tv
    # Bei Filmen tragen sie sehr wohl - 244 Treffer in den 1970ern.
    assert "jahrzehnt_1970" in {r.kennung for r in stoebern.regale_fuer("movie")}
    with pytest.raises(stoebern.UnbekanntesRegal):
        stoebern.filter_fuer("jahrzehnt_1970", "tv")


@pytest.mark.parametrize(
    "kennung",
    ["", "quatsch", "jahrzehnt_1950", "jahrzehnt_abc", "genre_gibtsnicht", "genre_"],
)
def test_unbekannte_kennung_fliegt_auf(kennung: str) -> None:
    with pytest.raises(stoebern.UnbekanntesRegal):
        stoebern.filter_fuer(kennung, "movie")


def test_genre_regale_unterscheiden_sich_zwischen_film_und_serie() -> None:
    """"Action" heisst bei Serien 10759, nicht 28.

    Wer dieselbe Nummer nimmt, bekommt eine leere Rubrik - und die sieht aus
    wie "dazu gibt es nichts".
    """
    assert stoebern.GENRE_KENNUNGEN["movie"]["action"] == 28
    assert stoebern.GENRE_KENNUNGEN["tv"]["action"] == 10759


# --- Das Sieb nach dem eigenen Bestand -------------------------------------


async def _sammle(seiten: list[list[MediaItem]], modus: str, ziel: int, **mehr):
    """Sammelt aus vorgegebenen Seiten - ohne TMDB, ohne Datenbank."""

    async def hole_seite(seite: int) -> tuple[list[MediaItem], int]:
        index = seite - 1
        return (seiten[index] if index < len(seiten) else []), len(seiten)

    async def mit_zustand(items: list[MediaItem]) -> list[MediaItem]:
        return items

    return await stoebern.sammle(hole_seite, mit_zustand, modus=modus, ziel=ziel, **mehr)


async def test_egal_siebt_nichts_und_laedt_eine_seite() -> None:
    seiten = [[_titel(1, "downloaded"), _titel(2)], [_titel(3)]]
    ausbeute = await _sammle(seiten, "egal", ziel=10)
    assert [i.tmdb_id for i in ausbeute.items] == [1, 2]
    assert ausbeute.seiten_durchsucht == 1


async def test_nur_vorhanden_behaelt_nur_greifbares() -> None:
    """Angefragt ist nicht vorhanden.

    "Wir wollen heute Abend etwas schauen" heisst: Die Datei muss **da** sein.
    Eine laufende Anfrage hilft heute Abend nicht.
    """
    seite = [
        _titel(1, "downloaded"),
        _titel(2, "in_library"),
        _titel(3, "partial"),
        _titel(4, "searching"),
        _titel(5, "requested"),
        _titel(6, "pending_approval"),
        _titel(7, "not_requested"),
    ]
    ausbeute = await _sammle([seite], "nur_vorhanden", ziel=10)
    assert [i.tmdb_id for i in ausbeute.items] == [1, 2, 3]


async def test_nur_neu_laesst_weg_was_schon_laeuft() -> None:
    seite = [
        _titel(1, "downloaded"),
        _titel(2, "searching"),
        _titel(3, "pending_approval"),
        _titel(4, "not_requested"),
        _titel(5, "blocked"),
    ]
    ausbeute = await _sammle([seite], "nur_neu", ziel=10)
    # Gesperrte Titel bleiben sichtbar - genau wie ueberall sonst. Wer sucht,
    # soll erfahren, dass es den Titel hier nicht geben wird, statt ihn dreimal
    # anzufragen.
    assert [i.tmdb_id for i in ausbeute.items] == [4, 5]


async def test_sieb_laedt_nach_bis_genug_uebrig_ist() -> None:
    """Der Kern der Sache.

    Genau das kann die Entdecken-Seite nicht: Sie siebt erst im Browser, und
    aus 20 Kacheln werden bei gefuellter Bibliothek zwei.
    """
    seiten = [
        [_titel(n, "downloaded") for n in range(1, 21)],
        [_titel(n, "downloaded") for n in range(21, 41)],
        [_titel(41), _titel(42), _titel(43)],
    ]
    ausbeute = await _sammle(seiten, "nur_neu", ziel=3)
    assert [i.tmdb_id for i in ausbeute.items] == [41, 42, 43]
    assert ausbeute.seiten_durchsucht == 3


async def test_leere_bibliothek_gibt_eine_ehrliche_leere_antwort() -> None:
    """Kein Endlosblaettern, sondern ein klares "dazu passt hier nichts"."""
    seiten = [[_titel(n) for n in range(1, 21)] for _ in range(20)]
    ausbeute = await _sammle(seiten, "nur_vorhanden", ziel=12)
    assert ausbeute.items == []
    # Es wird gedeckelt und nicht bis Seite 500 gegraben.
    assert ausbeute.seiten_durchsucht == stoebern.MAX_SEITEN


async def test_sieb_hoert_auf_wenn_tmdb_nichts_mehr_hat() -> None:
    ausbeute = await _sammle([[_titel(1, "downloaded")]], "nur_neu", ziel=12)
    assert ausbeute.items == []
    assert ausbeute.erschoepft is True


async def test_doppelte_titel_erscheinen_nur_einmal() -> None:
    """TMDB liefert bei Seitenwechseln gelegentlich Wiederholungen."""
    seiten = [[_titel(1), _titel(2)], [_titel(2), _titel(3)]]
    ausbeute = await _sammle(seiten, "nur_neu", ziel=10)
    assert [i.tmdb_id for i in ausbeute.items] == [1, 2, 3]


async def test_laufzeit_wird_nachgeprueft() -> None:
    """TMDBs eigener Laufzeitfilter ist unzuverlaessig.

    Gemessen am 23.08.2026: ``with_runtime.lte=95`` lieferte "Young Hearts"
    (97 Min.) und den Miraculous-Film (99 Min.). Wer "hoechstens 90 Minuten"
    waehlt, hat dafuer einen Grund - das ist keine Kleinigkeit, sondern die
    Zusage, die er geprueft hat.
    """
    from app.services.filters import DiscoverFilters

    passt = stoebern.laufzeit_pruefer(DiscoverFilters(min_runtime=40, max_runtime=90))

    def film(dauer: int | None) -> MediaItem:
        return MediaItem(media_type="movie", tmdb_id=1, title="x", runtime_minutes=dauer)

    assert passt(film(88)) is True
    assert passt(film(90)) is True
    assert passt(film(97)) is False
    assert passt(film(20)) is False
    # In einer Zusage ist "unbekannt" nicht "passt schon".
    assert passt(film(None)) is False


async def test_ohne_obergrenze_bleibt_unbekanntes_drin() -> None:
    """Sonst faellt ueberall dort etwas weg, wo es gar nicht um Laufzeit geht."""
    from app.services.filters import DiscoverFilters

    passt = stoebern.laufzeit_pruefer(DiscoverFilters())
    assert passt(MediaItem(media_type="movie", tmdb_id=1, title="x")) is True


async def test_nachpruefung_laedt_ebenfalls_nach() -> None:
    """Sonst waere die Seite nach dem Nachsieben bloss kuerzer."""
    lang = [
        MediaItem(media_type="movie", tmdb_id=n, title=f"F{n}", runtime_minutes=150)
        for n in range(1, 21)
    ]
    kurz = [
        MediaItem(media_type="movie", tmdb_id=n, title=f"F{n}", runtime_minutes=80)
        for n in range(21, 24)
    ]
    ausbeute = await _sammle(
        [lang, kurz], "egal", ziel=3, zusaetzlich=lambda i: (i.runtime_minutes or 0) <= 90
    )
    assert [i.tmdb_id for i in ausbeute.items] == [21, 22, 23]
    assert ausbeute.seiten_durchsucht == 2


async def test_unbekannter_modus_ist_ein_fehler() -> None:
    with pytest.raises(ValueError):
        await _sammle([[]], "irgendwas", ziel=1)


# --- Persoenliche Regale ---------------------------------------------------


@pytest.mark.parametrize(
    ("kennung", "erwartet"),
    [
        ("weil_du_603", 603),
        ("weil_du_1", 1),
        ("weil_du_", None),
        ("weil_du_abc", None),
        ("weil_du_-3", None),
        ("perlen", None),
        ("", None),
    ],
)
def test_kennung_eines_herz_regals(kennung: str, erwartet: int | None) -> None:
    """Die Kennung kommt aus der Adresse - Unsinn muss auffallen."""
    assert stoebern.weil_du_id(kennung) == erwartet


def test_persoenliche_regale_werden_erkannt() -> None:
    assert stoebern.ist_persoenlich("wieder") is True
    assert stoebern.ist_persoenlich("weil_du_603") is True
    assert stoebern.ist_persoenlich("perlen") is False
    assert stoebern.ist_persoenlich("weil_du_abc") is False


def test_persoenliche_regale_brauchen_keine_tmdb_filter() -> None:
    """Sie kommen aus dem Verlauf bzw. aus Empfehlungen, nicht aus /discover.

    ``regal_oder_404`` muss sie trotzdem kennen, sonst antwortet die Seite mit
    404 auf ein Regal, das sie selbst angeboten hat.
    """
    for kennung in ("wieder", "weil_du_603"):
        regal = stoebern.regal_oder_404(kennung, "movie")
        assert regal.persoenlich is True
        assert regal.gruppe == "reihe"


def test_persoenliche_regale_stehen_nicht_in_der_festen_liste() -> None:
    """Sie haengen an Nutzerdaten und werden erst im Router angehaengt.

    ⚠️ Und sie duerfen nie die **einzigen** sein: Eine frische Installation
    ohne Herzen und ohne Media-Server muss dieselbe Seite sehen. Genau daran
    krankt die Startseite, wo zwei von drei Reihen beim ersten Start leere
    Kaesten sind.
    """
    for media_type in ("movie", "tv"):
        kennungen = {r.kennung for r in stoebern.regale_fuer(media_type)}
        assert "wieder" not in kennungen
        assert not any(k.startswith("weil_du_") for k in kennungen)
        # Und es bleibt trotzdem eine volle Seite uebrig.
        assert len([r for r in stoebern.regale_fuer(media_type) if r.gruppe == "reihe"]) >= 2


def test_frisches_konto_bekommt_keine_persoenlichen_regale(admin_client: TestClient) -> None:
    """Ohne Verlauf und ohne Herzen wird nichts angeboten, was leer bliebe."""
    regale = admin_client.get("/api/stoebern/regale/movie").json()
    assert all(r["persoenlich"] is False for r in regale)
    # Die Seite ist trotzdem vollstaendig - mehrere geladene Reihen und die
    # Kacheln fuer Jahrzehnte und Genres.
    assert sum(1 for r in regale if r["gruppe"] == "reihe") >= 3
    assert any(r["kategorie"] == "jahrzehnt" for r in regale)
    assert any(r["kategorie"] == "genre" for r in regale)


def test_herz_regal_erscheint_und_traegt_den_filmtitel(admin_client: TestClient) -> None:
    """Der Name enthaelt einen Filmtitel und kann deshalb nicht uebersetzt werden."""
    admin_client.post(
        "/api/favorites",
        json={"media_type": "movie", "tmdb_id": 603, "title": "Matrix", "poster_url": None},
    )
    regale = admin_client.get("/api/stoebern/regale/movie").json()
    herz = next((r for r in regale if r["kennung"] == "weil_du_603"), None)
    assert herz is not None
    assert herz["titel"] == "Matrix"
    assert herz["persoenlich"] is True
    # Persoenliches steht vorn - es ist die Reihe, die jemand am ehesten sucht.
    assert regale[0]["persoenlich"] is True


def test_wenige_herzen_erscheinen_alle() -> None:
    herzen = [(n, f"Film {n}") for n in range(1, 3)]
    assert stoebern.herzen_fuer_heute(herzen, date(2026, 8, 23)) == herzen


def test_viele_herzen_werden_gedeckelt_und_rotieren() -> None:
    """⚠️ Der Deckel allein reicht nicht.

    Der erste Bauversuch nahm stumpf die zuletzt markierten: Wer hundert
    Favoriten hat, saehe die uebrigen sechsundneunzig **nie**. Jetzt bleibt
    ein Platz fuer das neueste Herz - das frischeste Signal, das es gibt -
    und die uebrigen wandern taeglich weiter.
    """
    herzen = [(n, f"Film {n}") for n in range(1, 21)]
    heute = stoebern.herzen_fuer_heute(herzen, date(2026, 8, 23))
    morgen = stoebern.herzen_fuer_heute(herzen, date(2026, 8, 24))

    assert len(heute) == stoebern.MAX_WEIL_DU
    # Das neueste ist immer dabei.
    assert heute[0] == herzen[0] and morgen[0] == herzen[0]
    # Die uebrigen wechseln.
    assert heute[1:] != morgen[1:]


def test_die_rotation_bleibt_innerhalb_eines_tages_stehen() -> None:
    """Sonst zeigte jedes Neuladen eine andere Seite."""
    herzen = [(n, f"Film {n}") for n in range(1, 21)]
    tag = date(2026, 8, 23)
    assert stoebern.herzen_fuer_heute(herzen, tag) == stoebern.herzen_fuer_heute(herzen, tag)


def test_ueber_die_tage_kommt_jeder_favorit_dran() -> None:
    """Die eigentliche Zusage: Nichts bleibt fuer immer unsichtbar."""
    herzen = [(n, f"Film {n}") for n in range(1, 21)]
    gesehen: set[int] = set()
    for versatz in range(40):
        for kennung, _ in stoebern.herzen_fuer_heute(herzen, date(2026, 8, 23) + timedelta(days=versatz)):
            gesehen.add(kennung)
    assert gesehen == {n for n, _ in herzen}


def test_unsinnige_herz_kennung_ist_ein_404(admin_client: TestClient) -> None:
    assert admin_client.get("/api/stoebern/regal/movie/weil_du_abc").status_code == 404


# --- Anbieter-Neutralitaet -------------------------------------------------


def test_stoebern_kennt_keinen_media_server() -> None:
    """Die Zusage, auf der Jellyfin und mehrere Server spaeter aufbauen.

    Persoenliche Daten duerfen ausschliesslich aus den neutralen Tabellen
    (``UserWatched``, ``Favorite``) kommen. Sobald hier ein Anbieter-Modul
    importiert wird, ist jedes Regal beim naechsten Media-Server wieder kaputt
    - und das faellt sonst erst auf, wenn Jellyfin schon halb gebaut ist.
    """
    verboten = ("mediaserver", "watchlist", "plex")
    wurzel = pathlib.Path(__file__).resolve().parent.parent / "app"

    for datei in (wurzel / "services" / "stoebern.py", wurzel / "routers" / "stoebern.py"):
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            module = ""
            if isinstance(knoten, ast.ImportFrom):
                module = knoten.module or ""
                namen = [a.name for a in knoten.names]
            elif isinstance(knoten, ast.Import):
                namen = [a.name for a in knoten.names]
            else:
                continue
            for text in [module, *namen]:
                assert not any(wort in text.lower() for wort in verboten), (
                    f"{datei.name} importiert '{text}' - das bindet das Stoebern "
                    "an einen einzelnen Media-Server."
                )


# --- Ueber die Schnittstelle (Beispielbetrieb) -----------------------------


def test_ohne_anmeldung_kein_zugriff(client: TestClient) -> None:
    assert client.get("/api/stoebern/regale/movie").status_code == 401


def test_regalliste_kostet_keinen_tmdb_abruf(admin_client: TestClient) -> None:
    antwort = admin_client.get("/api/stoebern/regale/movie")
    assert antwort.status_code == 200
    regale = antwort.json()
    assert [r["kennung"] for r in regale[:4]] == ["neu", "perlen", "klassiker", "kurz"]
    assert all(r["gruppe"] in ("reihe", "kachel") for r in regale)
    # Wenige geladene Reihen - der Rest sind kostenlose Verweise.
    #
    # Die Zahl ist eine Kostenrechnung, keine Geschmacksfrage: Jede geladene
    # Reihe kostet einen Discover-Abruf plus bis zu zwanzig schlanke
    # Detailabrufe. Zwanzig Reihen waeren rund 420 Abrufe fuer eine Seite.
    assert sum(1 for r in regale if r["gruppe"] == "reihe") <= 5


def test_neu_erschienen_steht_vorn(admin_client: TestClient) -> None:
    """Es ersetzt die frueheren Menuepunkte "Filme/Serien entdecken"."""
    for art in ("movie", "tv"):
        regale = admin_client.get(f"/api/stoebern/regale/{art}").json()
        assert regale[0]["kennung"] == "neu"


def test_serien_haben_kein_kurz_regal(admin_client: TestClient) -> None:
    regale = admin_client.get("/api/stoebern/regale/tv").json()
    assert all(r["kennung"] != "kurz" for r in regale)


def test_regal_liefert_titel(admin_client: TestClient) -> None:
    seite = admin_client.get("/api/stoebern/regal/movie/perlen").json()
    assert seite["kennung"] == "perlen"
    assert seite["demo"] is True
    assert isinstance(seite["items"], list)


def test_unbekanntes_regal_ist_ein_404(admin_client: TestClient) -> None:
    assert admin_client.get("/api/stoebern/regal/movie/quatsch").status_code == 404


def test_kurz_regal_gibt_es_bei_serien_nicht(admin_client: TestClient) -> None:
    assert admin_client.get("/api/stoebern/regal/tv/kurz").status_code == 404


def test_ungueltige_medienart_wird_abgelehnt(admin_client: TestClient) -> None:
    assert admin_client.get("/api/stoebern/regale/buecher").status_code == 422
