"""Der gefuehrte Filmabend - Fragebaum, Uebersetzung in Filter, Wuerfeln."""

from __future__ import annotations

import ast
import pathlib
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import MediaType, User, UserWatched
from app.services import filmabend
from app.services.filters import BEKANNTE_TITEL_STIMMEN, PERLEN_MINDESTALTER_JAHRE

# --- Der Fragebaum ---------------------------------------------------------


def test_baum_ist_zusammenhaengend() -> None:
    """Jede Bedingung muss sich auf eine **fruehere** Frage beziehen.

    Eine Bedingung, die nach vorn zeigt, waere zum Zeitpunkt der Pruefung
    immer unerfuellt - die Frage entfiele also nie, und niemand merkte es.
    """
    gesehen: set[str] = set()
    for frage in filmabend.FRAGEN:
        for vorher, ausloeser in frage.entfaellt_wenn.items():
            assert vorher in gesehen, (
                f"{frage.kennung} haengt an {vorher}, das erst spaeter kommt"
            )
            erlaubt = filmabend.FRAGEN_NACH_KENNUNG[vorher].antworten
            for wert in ausloeser:
                assert wert in erlaubt, f"{vorher}={wert} gibt es nicht"
        gesehen.add(frage.kennung)


def test_jede_frage_hat_antworten() -> None:
    for frage in filmabend.FRAGEN:
        assert len(frage.antworten) >= 2
        assert len(set(frage.antworten)) == len(frage.antworten)


def test_kennungen_sind_eindeutig() -> None:
    kennungen = [f.kennung for f in filmabend.FRAGEN]
    assert len(kennungen) == len(set(kennungen))


def _alle_wege() -> list[dict[str, str]]:
    """Jede vollstaendige Antwortmenge, die der Baum ueberhaupt zulaesst.

    Muss den Baum wirklich ablaufen und darf **nicht** stumpf das Produkt
    aller Antworten bilden: Ein Teil der Antworten verschwindet je nach
    frueherer Wahl ("mit Kindern" bietet kein "zum Gruseln" an), und solche
    Kombinationen wuerde ``pruefe`` zu Recht zurueckweisen.
    """
    wege: list[dict[str, str]] = []

    def weiter(bisher: dict[str, str]) -> None:
        frage = filmabend.naechste_frage(bisher)
        if frage is None:
            wege.append(bisher)
            return
        for antwort in filmabend.verfuegbare_antworten(frage, bisher):
            weiter({**bisher, frage.kennung: antwort})

    weiter({})
    return wege


def _durchlauf(wahl: dict[str, str]) -> list[str]:
    """Den Baum ablaufen und die tatsaechlich gestellten Fragen sammeln."""
    antworten: dict[str, str] = {}
    gestellt: list[str] = []
    while (frage := filmabend.naechste_frage(antworten)) is not None:
        gestellt.append(frage.kennung)
        antworten[frage.kennung] = wahl.get(frage.kennung, frage.antworten[-1])
    return gestellt


def test_jeder_weg_endet() -> None:
    """Keine Sackgasse und keine Endlosschleife - fuer *jeden* Weg.

    Wird vollstaendig durchprobiert statt stichprobenhaft: Der Baum ist klein
    genug, und eine Schleife faende man sonst erst, wenn jemand genau diesen
    Weg geht.
    """
    wege = _alle_wege()
    assert len(wege) > 100, "der Baum ist duenner als gedacht"
    for weg in wege:
        assert weg, "kein einziger Schritt"
        # Jede beantwortete Frage muss eine echte sein, keine uebersprungene.
        assert filmabend.pruefe(weg) == weg


def test_sofort_ueberspringt_die_bekanntheit() -> None:
    """In der eigenen Bibliothek ist "Geheimtipp" keine sinnvolle Frage mehr."""
    gestellt = _durchlauf({"verfuegbar": "sofort", "vertraut": "egal"})
    assert "bekanntheit" not in gestellt
    assert "epoche" in gestellt


def test_wiedersehen_ueberspringt_epoche_und_bekanntheit() -> None:
    """Wer aus dem eigenen Verlauf waehlt, kennt die Titel und ihren Zeitraum."""
    gestellt = _durchlauf({"verfuegbar": "egal", "vertraut": "wieder"})
    assert "epoche" not in gestellt
    assert "bekanntheit" not in gestellt


def test_der_lange_weg_stellt_alle_fragen() -> None:
    gestellt = _durchlauf({"verfuegbar": "laden", "vertraut": "neu"})
    assert gestellt == [f.kennung for f in filmabend.FRAGEN]


def test_keine_stimmung_wird_weggenommen() -> None:
    """Auch mit Kindern bleibt jede Stimmung waehlbar.

    Der erste Bauversuch blendete "Gruseln" und "fuers Herz" aus, sobald
    Kinder mitschauen. Messbar falsch: Mit FSK 0/6 liefert das Horror-Genre
    30 Treffer, und zwar die richtigen - "Hexen hexen", "Das Haus der
    geheimnisvollen Uhren", "King Kong". Kinder moegen Gruseln; sie brauchen
    eine Grenze, keine Bevormundung.

    Die Grenze zieht die Altersfreigabe, nicht die Auswahl.
    """
    stimmung = filmabend.FRAGEN_NACH_KENNUNG["stimmung"]
    for wer in ("allein", "zu_zweit", "freunde", "familie", "kinder"):
        verfuegbar = filmabend.verfuegbare_antworten(stimmung, {"gesellschaft": wer})
        assert set(verfuegbar) == set(stimmung.antworten), f"bei '{wer}' fehlt etwas"


def test_die_ausblendung_gibt_es_noch_als_mittel() -> None:
    """Der Mechanismus bleibt, auch wenn ihn zurzeit keine Frage nutzt."""
    kuenstlich = filmabend.Frage(
        "test",
        ("a", "b"),
        antworten_entfallen_wenn={"b": {"gesellschaft": ("kinder",)}},
    )
    assert filmabend.verfuegbare_antworten(kuenstlich, {"gesellschaft": "kinder"}) == ("a",)
    assert filmabend.verfuegbare_antworten(kuenstlich, {}) == ("a", "b")


def test_gesellschaft_kommt_zuerst() -> None:
    """Die natuerlichste erste Frage am Filmabend."""
    assert filmabend.FRAGEN[0].kennung == "gesellschaft"
    assert filmabend.naechste_frage({}).kennung == "gesellschaft"


# --- Antworten pruefen -----------------------------------------------------


def test_uebersprungene_antworten_werden_verworfen() -> None:
    """Sonst wirkt eine Antwort, die nie gestellt wurde.

    Die Antworten kommen aus dem Browser. Wer "sofort" waehlt und trotzdem
    ``bekanntheit=geheimtipp`` mitschickt, darf damit das Ergebnis nicht
    verbiegen.
    """
    sauber = filmabend.pruefe(
        {"stimmung": "lachen", "verfuegbar": "sofort", "bekanntheit": "geheimtipp"}
    )
    assert "bekanntheit" not in sauber
    assert sauber["verfuegbar"] == "sofort"


def test_unbekannte_antwort_fliegt_auf() -> None:
    with pytest.raises(filmabend.UngueltigeAntwort):
        filmabend.pruefe({"stimmung": "tanzen"})


def test_unbekannte_frage_fliegt_auf() -> None:
    with pytest.raises(filmabend.UngueltigeAntwort):
        filmabend.pruefe({"lieblingsfarbe": "blau"})


def test_leere_antworten_sind_erlaubt() -> None:
    """Der Assistent muss auch ohne eine einzige Antwort etwas liefern."""
    assert filmabend.pruefe({}) == {}


# --- Von der Antwort zum Filter --------------------------------------------


def test_zeit_wird_zur_laufzeit() -> None:
    """Die Frage, die am Filmabend wirklich bindet.

    Die Zahl muss zur Beschriftung passen: "Hoechstens 90 Minuten" darf nicht
    95 bedeuten. TMDB haelt sich ohnehin nicht genau daran - deshalb prueft
    ``stoebern.laufzeit_pruefer`` hinterher nach.
    """
    kurz = filmabend.filter_aus({"zeit": "kurz"}, "movie")
    assert kurz.max_runtime == 90
    lang = filmabend.filter_aus({"zeit": "lang"}, "movie")
    assert lang.min_runtime == 121
    assert lang.max_runtime is None


def test_serien_bekommen_keine_laufzeit() -> None:
    """Bei Serien waere das die Folgenlaenge - "wir haben zwei Stunden" sagt
    darueber nichts."""
    filter_ = filmabend.filter_aus({"zeit": "kurz"}, "tv")
    assert filter_.max_runtime is None
    assert filter_.min_runtime is None


def test_stimmung_wird_zu_genres() -> None:
    filter_ = filmabend.filter_aus({"stimmung": "lachen"}, "movie")
    assert filter_.genres_or == "35"


def test_ueberraschung_filtert_keine_genres() -> None:
    assert filmabend.filter_aus({"stimmung": "ueberrasch"}, "movie").genres_or == ""


def test_jede_stimmung_ist_fuer_beide_medienarten_hinterlegt() -> None:
    """Sonst wirft eine Serien-Auswahl einen KeyError statt Ergebnisse."""
    stimmungen = set(filmabend.FRAGEN_NACH_KENNUNG["stimmung"].antworten)
    for media_type in ("movie", "tv"):
        assert set(filmabend.STIMMUNGEN[media_type]) == stimmungen


def test_geheimtipp_setzt_fenster_und_altersgrenze() -> None:
    """Dieselbe Lehre wie beim Perlen-Regal.

    Ohne Altersgrenze misst die Stimmenzahl nur, wie neu ein Titel ist -
    "Geheimtipp" lieferte dann die Blockbuster der laufenden Saison.
    """
    filter_ = filmabend.filter_aus(
        {"bekanntheit": "geheimtipp"}, "movie", heute=date(2026, 8, 23)
    )
    assert filter_.min_votes is not None and filter_.max_votes is not None
    assert filter_.date_to is not None
    assert filter_.date_to <= f"{2026 - PERLEN_MINDESTALTER_JAHRE}-12-31"


def test_geheimtipp_verschiebt_eine_engere_epoche_nicht() -> None:
    """Die schaerfere der beiden Grenzen muss gewinnen.

    "Geheimtipp" **und** "aus alten Zeiten" duerfen sich nicht gegenseitig
    aufweichen - sonst zeigte der Assistent Titel, die eine der beiden
    Antworten ausdruecklich ausgeschlossen hat.
    """
    filter_ = filmabend.filter_aus(
        {"bekanntheit": "geheimtipp", "epoche": "alt"}, "movie", heute=date(2026, 8, 23)
    )
    assert filter_.date_to == "2000-12-31"


def test_bekannt_setzt_eine_hohe_untergrenze() -> None:
    filter_ = filmabend.filter_aus({"bekanntheit": "bekannt"}, "movie")
    assert filter_.min_votes == BEKANNTE_TITEL_STIMMEN
    assert filter_.max_votes is None


def test_jede_kombination_ergibt_gueltige_filter() -> None:
    """Kein Weg durch den Baum darf in einem Fehler oder Widerspruch enden."""
    for sauber in _alle_wege():
        for media_type in ("movie", "tv"):
            filter_ = filmabend.filter_aus(sauber, media_type)
            # Ein widerspruechliches Fenster faende bei TMDB garantiert nichts.
            if filter_.min_votes is not None and filter_.max_votes is not None:
                assert filter_.min_votes < filter_.max_votes
            if filter_.date_from and filter_.date_to:
                assert filter_.date_from <= filter_.date_to
            assert filter_.cache_key(media_type)


def test_mit_kindern_setzt_eine_altersfreigabe() -> None:
    """Eine Freigabe ist eine nachpruefbare Zusage - ein Genre ist es nicht.

    "Mit Kindern" heisst belegbar "hoechstens FSK 6". Es heisst **nicht**
    "Zeichentrick": Wer daraus Genres macht, laesst einen FSK-16-Animationsfilm
    durch und sperrt einen harmlosen Realfilm aus.
    """
    filter_ = filmabend.filter_aus(
        {"gesellschaft": "kinder", "stimmung": "lachen"}, "movie", freigaben=("DE", "0|6")
    )
    assert filter_.certification_country == "DE"
    assert filter_.certifications == "0|6"


def test_ohne_kinder_keine_altersgrenze() -> None:
    filter_ = filmabend.filter_aus(
        {"gesellschaft": "freunde"}, "movie", freigaben=("DE", "0|6")
    )
    assert filter_.certification_country is None
    assert filter_.certifications == ""


def test_ohne_freigabetabelle_greifen_kindgerechte_genres() -> None:
    """Der Rueckfall darf nicht "gar kein Schutz" sein.

    Bei Serien bietet TMDB gar keinen Freigabefilter, und bei Filmen kann die
    Tabelle ausfallen. Ohne Rueckfall lieferte "mit Kindern" dann alles.
    """
    for media_type in ("movie", "tv"):
        filter_ = filmabend.filter_aus({"gesellschaft": "kinder"}, media_type)
        assert filter_.certification_country is None
        nummern = {int(n) for n in filter_.genres_or.split("|") if n}
        assert nummern
        assert nummern <= set(filmabend.KINDGERECHTE_GENRES[media_type])


def test_hoechstalter_nur_bei_kindern_und_familie() -> None:
    assert filmabend.hoechstalter({"gesellschaft": "kinder"}) == 6
    assert filmabend.hoechstalter({"gesellschaft": "familie"}) == 12
    for wer in ("allein", "zu_zweit", "freunde"):
        assert filmabend.hoechstalter({"gesellschaft": wer}) is None


def test_verfuegbar_wird_zum_bestandsfilter() -> None:
    assert filmabend._modus({"verfuegbar": "sofort"}) == "nur_vorhanden"
    assert filmabend._modus({"verfuegbar": "laden"}) == "nur_neu"
    assert filmabend._modus({}) == "egal"


# --- Nochmal wuerfeln ------------------------------------------------------


def test_gleiche_runde_ergibt_gleiche_reihenfolge() -> None:
    """Kein echter Zufall.

    Sonst waere der Stapel, den man gerade ansieht, nach einem Neuladen der
    Seite weg - und "nochmal wuerfeln" liesse sich nicht von einem Fehler
    unterscheiden.
    """
    kennungen = list(range(1, 40))
    antworten = {"stimmung": "lachen"}
    assert filmabend.mischen(kennungen, antworten, 0) == filmabend.mischen(
        kennungen, antworten, 0
    )


def test_naechste_runde_ergibt_eine_andere_reihenfolge() -> None:
    kennungen = list(range(1, 40))
    antworten = {"stimmung": "lachen"}
    assert filmabend.mischen(kennungen, antworten, 0) != filmabend.mischen(
        kennungen, antworten, 1
    )


def test_andere_antworten_ergeben_eine_andere_reihenfolge() -> None:
    kennungen = list(range(1, 40))
    assert filmabend.mischen(kennungen, {"stimmung": "lachen"}, 0) != filmabend.mischen(
        kennungen, {"stimmung": "gruseln"}, 0
    )


def test_mischen_verliert_nichts() -> None:
    kennungen = list(range(1, 40))
    assert sorted(filmabend.mischen(kennungen, {}, 3)) == kennungen


# --- Der eigene Sehstand ---------------------------------------------------


def _person(name: str = "seher") -> int:
    with SessionLocal() as db:
        person = User(username=name, email=f"{name}@test.invalid", password_hash="x")
        db.add(person)
        db.commit()
        return person.id


def _gesehen(user_id: int, tmdb_id: int, wann: datetime | None) -> None:
    with SessionLocal() as db:
        db.add(
            UserWatched(
                user_id=user_id,
                media_type=MediaType.movie,
                tmdb_id=tmdb_id,
                watched_at=wann,
            )
        )
        db.commit()


def test_am_laengsten_her_steht_vorn() -> None:
    person = _person()
    jetzt = datetime(2026, 8, 23)
    _gesehen(person, 1, jetzt - timedelta(days=30))
    _gesehen(person, 2, jetzt - timedelta(days=365 * 5))
    _gesehen(person, 3, jetzt - timedelta(days=200))

    with SessionLocal() as db:
        # Der von vor 30 Tagen faellt raus - das ist nicht "lange nicht gesehen".
        assert filmabend.lange_nicht_gesehen(db, person, "movie", jetzt=jetzt) == [2, 3]


def test_frisch_gesehenes_wird_nicht_vorgeschlagen() -> None:
    """⚠️ Zwei Fassungen hatte diese Grenze schon, beide falsch.

    Erst **zwei Jahre**: unbrauchbar, weil ein Media-Server den Verlauf erst
    ab dem Tag der Verbindung sammelt - das Konto mit dem laengsten Verlauf
    hatte 389 Eintraege, der aelteste eineinhalb Jahre alt, die Funktion waere
    fuer jeden leer gewesen. Danach **ohne** Grenze, rein nach "am laengsten
    her": Dann schlug sie bei kurzem Verlauf Titel von letzter Woche vor, und
    "lange nicht gesehen" war schlicht gelogen.

    Sechs Monate ist die Entscheidung des Nutzers. Passt nichts, bleibt die
    Liste leer - und das Regal verschwindet, statt Unpassendes zu zeigen.
    """
    person = _person()
    jetzt = datetime(2026, 8, 23)
    _gesehen(person, 1, jetzt - timedelta(days=4))
    _gesehen(person, 2, jetzt - timedelta(days=40))

    with SessionLocal() as db:
        assert filmabend.lange_nicht_gesehen(db, person, "movie", jetzt=jetzt) == []


def test_die_grenze_liegt_bei_einem_halben_jahr() -> None:
    person = _person()
    jetzt = datetime(2026, 8, 23)
    _gesehen(person, 1, jetzt - timedelta(days=filmabend.LANGE_HER_TAGE - 5))
    _gesehen(person, 2, jetzt - timedelta(days=filmabend.LANGE_HER_TAGE + 5))

    with SessionLocal() as db:
        assert filmabend.lange_nicht_gesehen(db, person, "movie", jetzt=jetzt) == [2]


def test_eintraege_ohne_datum_stehen_vorn() -> None:
    """Ein fehlendes Datum heisst "irgendwann" - laenger her als letzte Woche.

    Sie wegzulassen waere der haeufigere Fehler: Im gemessenen Bestand trugen
    41 von 389 Zeilen kein Datum.
    """
    person = _person()
    jetzt = datetime(2026, 8, 23)
    _gesehen(person, 1, jetzt - timedelta(days=365 * 4))
    _gesehen(person, 2, None)
    _gesehen(person, 3, jetzt - timedelta(days=365 * 9))

    with SessionLocal() as db:
        treffer = filmabend.lange_nicht_gesehen(db, person, "movie", jetzt=jetzt)
    assert treffer[0] == 2
    assert treffer[1:] == [3, 1]


def test_vorrat_nimmt_die_aeltere_haelfte() -> None:
    """Nicht alles - sonst schluege der Assistent auch das von gestern vor."""
    viele = list(range(1, 1001))
    genommen = filmabend.vorrat(viele)
    assert genommen == viele[:500]

    # Bei wenigen Eintraegen bleibt alles drin, sonst waere der Stapel leer.
    wenige = [1, 2, 3]
    assert filmabend.vorrat(wenige) == wenige


def test_jeder_sieht_nur_seinen_eigenen_verlauf() -> None:
    einer, anderer = _person("einer"), _person("anderer")
    _gesehen(einer, 11, None)
    _gesehen(anderer, 22, None)

    with SessionLocal() as db:
        assert filmabend.lange_nicht_gesehen(db, einer, "movie") == [11]
        assert filmabend.gesehene_kennungen(db, anderer, "movie") == {22}


def test_regal_verschwindet_ohne_passende_eintraege(admin_client: TestClient) -> None:
    """Wer seinen Server letzte Woche verbunden hat, sieht das Regal nicht.

    Genau darum geht es bei der Grenze: Lieber kein Regal als eines, dessen
    Ueberschrift nicht stimmt.
    """
    from app.db import SessionLocal as Sitzung
    from app.models import User as Konto

    with Sitzung() as db:
        ich = db.scalars(select(Konto).where(Konto.username == "admin")).first()
        assert ich is not None
        db.add(
            UserWatched(
                user_id=ich.id,
                media_type=MediaType.movie,
                tmdb_id=999,
                watched_at=datetime.now() - timedelta(days=3),
            )
        )
        db.commit()

    regale = admin_client.get("/api/stoebern/regale/movie").json()
    assert all(r["kennung"] != "wieder" for r in regale)


# --- Anbieter-Neutralitaet -------------------------------------------------


def test_filmabend_kennt_keinen_media_server() -> None:
    """Die Zusage, auf der Jellyfin und mehrere Server spaeter aufbauen.

    Der Sehstand darf nur aus der neutralen Tabelle ``UserWatched`` kommen.
    Greift dieses Modul direkt auf einen Anbieter zu, ist "lange nicht gesehen"
    beim naechsten Media-Server wieder kaputt - und das faellt erst auf, wenn
    Jellyfin schon halb gebaut ist.
    """
    verboten = ("mediaserver", "watchlist", "plex")
    datei = pathlib.Path(__file__).resolve().parent.parent / "app" / "services" / "filmabend.py"
    baum = ast.parse(datei.read_text(encoding="utf-8"))

    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom):
            texte = [knoten.module or "", *(a.name for a in knoten.names)]
        elif isinstance(knoten, ast.Import):
            texte = [a.name for a in knoten.names]
        else:
            continue
        for text in texte:
            assert not any(wort in text.lower() for wort in verboten), (
                f"filmabend.py importiert '{text}' - das bindet den Assistenten "
                "an einen einzelnen Media-Server."
            )


# --- Ueber die Schnittstelle ----------------------------------------------


def test_ohne_anmeldung_kein_zugriff(client: TestClient) -> None:
    assert client.get("/api/stoebern/filmabend/fragen/movie").status_code == 401


def test_fragen_kommen_vollstaendig(admin_client: TestClient) -> None:
    fragen = admin_client.get("/api/stoebern/filmabend/fragen/movie").json()
    assert [f["kennung"] for f in fragen] == [f.kennung for f in filmabend.FRAGEN]
    bekanntheit = next(f for f in fragen if f["kennung"] == "bekanntheit")
    assert bekanntheit["entfaellt_wenn"]["verfuegbar"] == ["sofort"]


def test_jede_regel_im_baum_kommt_auch_an(admin_client: TestClient) -> None:
    """Allgemein statt an einem Beispiel - das naechste Feld faellt sonst
    genauso durch."""
    fragen = {f["kennung"]: f for f in admin_client.get(
        "/api/stoebern/filmabend/fragen/movie"
    ).json()}
    for frage in filmabend.fragen_fuer(mit_wiedersehen=False):
        geliefert = fragen[frage.kennung]
        assert geliefert["antworten"] == list(frage.antworten)
        assert set(geliefert["entfaellt_wenn"]) == set(frage.entfaellt_wenn)
        assert set(geliefert["antworten_entfallen_wenn"]) == set(
            frage.antworten_entfallen_wenn
        )


def test_wiedersehen_wird_ohne_verlauf_nicht_angeboten(admin_client: TestClient) -> None:
    """⚠️ Der Nutzer-Vorschlag statt einer besseren Fehlermeldung.

    Eine Antwort, die nie ein Ergebnis liefern kann, gehoert nicht ins Menue -
    dann laeuft niemand hinein. Vorher stand sie immer da und endete in einer
    Sackgasse mit einer obendrein falschen Meldung ("dein Sehverlauf ist
    leer", obwohl er voll war, nur zu frisch).
    """
    fragen = {f["kennung"]: f for f in admin_client.get(
        "/api/stoebern/filmabend/fragen/movie"
    ).json()}
    assert "wieder" not in fragen["vertraut"]["antworten"]
    # Die uebrigen Antworten bleiben.
    assert {"neu", "egal"} <= set(fragen["vertraut"]["antworten"])


def test_wiedersehen_erscheint_mit_altem_verlauf(admin_client: TestClient) -> None:
    from app.db import SessionLocal as Sitzung
    from app.models import User as Konto

    with Sitzung() as db:
        ich = db.scalars(select(Konto).where(Konto.username == "admin")).first()
        assert ich is not None
        db.add(
            UserWatched(
                user_id=ich.id,
                media_type=MediaType.movie,
                tmdb_id=4242,
                watched_at=datetime.now() - timedelta(days=400),
            )
        )
        db.commit()

    fragen = {f["kennung"]: f for f in admin_client.get(
        "/api/stoebern/filmabend/fragen/movie"
    ).json()}
    assert "wieder" in fragen["vertraut"]["antworten"]


def test_frischer_verlauf_meldet_nicht_leer(admin_client: TestClient) -> None:
    """"Gar kein Verlauf" und "nichts lange genug her" sind zwei Auskuenfte."""
    from app.db import SessionLocal as Sitzung
    from app.models import User as Konto

    with Sitzung() as db:
        ich = db.scalars(select(Konto).where(Konto.username == "admin")).first()
        assert ich is not None
        db.add(
            UserWatched(
                user_id=ich.id,
                media_type=MediaType.movie,
                tmdb_id=77,
                watched_at=datetime.now() - timedelta(days=3),
            )
        )
        db.commit()

    stapel = admin_client.post(
        "/api/stoebern/filmabend/ergebnis/movie",
        json={"antworten": {"vertraut": "wieder"}, "runde": 0},
    ).json()
    assert stapel["items"] == []
    # Er hat einen Verlauf - nur nichts Passendes darin.
    assert stapel["quelle_leer"] is False


def test_ergebnis_liefert_einen_stapel(admin_client: TestClient) -> None:
    antwort = admin_client.post(
        "/api/stoebern/filmabend/ergebnis/movie",
        json={"antworten": {"stimmung": "ueberrasch", "verfuegbar": "egal"}, "runde": 0},
    )
    assert antwort.status_code == 200
    stapel = antwort.json()
    assert len(stapel["items"]) <= filmabend.STAPEL_GROESSE
    assert stapel["runde"] == 0


def test_ergebnis_meldet_uebersprungene_antworten_nicht_zurueck(
    admin_client: TestClient,
) -> None:
    stapel = admin_client.post(
        "/api/stoebern/filmabend/ergebnis/movie",
        json={
            "antworten": {"verfuegbar": "sofort", "bekanntheit": "geheimtipp"},
            "runde": 0,
        },
    ).json()
    assert "bekanntheit" not in stapel["antworten"]


def test_unsinnige_antwort_wird_abgelehnt(admin_client: TestClient) -> None:
    antwort = admin_client.post(
        "/api/stoebern/filmabend/ergebnis/movie",
        json={"antworten": {"stimmung": "tanzen"}, "runde": 0},
    )
    assert antwort.status_code == 422


def test_mit_kindern_bleibt_gruseln_erlaubt(admin_client: TestClient) -> None:
    """Die Kombination, die zwischenzeitlich mit 422 abgewiesen wurde."""
    antwort = admin_client.post(
        "/api/stoebern/filmabend/ergebnis/movie",
        json={"antworten": {"gesellschaft": "kinder", "stimmung": "gruseln"}, "runde": 0},
    )
    assert antwort.status_code == 200


def test_die_fehlermeldung_ist_lesbar(admin_client: TestClient) -> None:
    """Kein "stimmung=tanzen" vor den Augen eines Menschen.

    Der haeufigste Grund fuer diesen Fehler ist ein Fenster, das noch offen
    war, als eine neue Fassung ausgeliefert wurde.
    """
    detail = admin_client.post(
        "/api/stoebern/filmabend/ergebnis/movie",
        json={"antworten": {"stimmung": "tanzen"}, "runde": 0},
    ).json()["detail"]

    # ⚠️ Die Meldung traegt seit der Umstellung eine Kennung: Ohne sie kann die
    # Oberflaeche den Satz nicht in der eingestellten Sprache bauen, und ein
    # englischer Nutzer las hier Deutsch.
    assert detail["code"] == "wizard_out_of_step"

    text = detail["message"]
    assert "=" not in text
    assert "von vorn" in text.lower()


def test_wiedersehen_ohne_verlauf_sagt_es_ehrlich(admin_client: TestClient) -> None:
    """Ohne verknuepften Media-Server gibt es keinen Sehverlauf.

    Dann muss die Oberflaeche das sagen koennen, statt eine leere Flaeche zu
    zeigen, die wie ein Defekt aussieht.
    """
    stapel = admin_client.post(
        "/api/stoebern/filmabend/ergebnis/movie",
        json={"antworten": {"vertraut": "wieder"}, "runde": 0},
    ).json()
    assert stapel["items"] == []
    assert stapel["quelle_leer"] is True
