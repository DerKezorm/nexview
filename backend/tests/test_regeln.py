"""Die Entscheidungstabelle der Regeln.

⚠️ **Warum das hier eine Tabelle ist und keine Sammlung Einzelfaelle.** Die
Auswertung ist eine reine Funktion: Regel plus Titel ergibt ja oder nein. Ihr
Eingangsraum ist damit abzaehlbar, und nur deshalb laesst sich ueberhaupt
zusichern, dass sie stimmt - von Hand durchgeklickt waeren es vierstellig viele
Wege, und geprueft wuerden dreissig davon.

Was hier **nicht** geprueft wird, weil es woanders steht: Ob die Regeln
ueberhaupt aufgerufen werden (``test_regeln_anfrage.py``, an der echten
Anfrage), und ob sie an Kontingent, Altersfilter oder Elternentscheidung
vorbeikommen (ebenfalls dort - denn das sind Zusagen ueber den *Ablauf*, nicht
ueber die Funktion).
"""

from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.models import MediaType, Regel, RegelEntscheidung, Role, User
from app.services import regeln

# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """Eine Sitzung auf der Testdatenbank. ``clean_db`` raeumt sie danach ab."""
    with SessionLocal() as db:
        yield db


def regel(bedingungen, entscheidung=RegelEntscheidung.freigeben, **rest) -> Regel:
    return Regel(
        name=rest.pop("name", "Probe"),
        aktiv=rest.pop("aktiv", True),
        position=rest.pop("position", 0),
        bedingungen=bedingungen,
        entscheidung=entscheidung,
        **rest,
    )


def titel(**rest) -> regeln.Titel:
    grund = {"typ": MediaType.movie, "qualitaet": "hd"}
    grund.update(rest)
    return regeln.Titel(**grund)


# ---------------------------------------------------------------------------
# 1. Was beim Speichern durchgeht - und was nicht
# ---------------------------------------------------------------------------


def test_eine_regel_ohne_bedingung_wird_nicht_gespeichert() -> None:
    """Sie traefe auf jeden Titel zu und waere damit ein Schalter, keine Regel."""
    with pytest.raises(regeln.RegelFehler):
        regeln.bedingungen_pruefen([])
    with pytest.raises(regeln.RegelFehler):
        regeln.bedingungen_pruefen(None)


def test_ein_unbekanntes_feld_faellt_beim_speichern_auf() -> None:
    """⚠️ Sonst saehe die Regel vorhanden aus und taete nie etwas.

    Bei der Auswertung wuerde ein unbekanntes Feld schlicht nie zutreffen. Die
    Regel stuende in der Liste, der Administrator verliesse sich auf sie, und
    niemand erfuehre je, dass sie leer laeuft.
    """
    with pytest.raises(regeln.RegelFehler, match="Unbekanntes Feld"):
        regeln.bedingungen_pruefen([{"feld": "regisseur", "werte": ["x"]}])


def test_dasselbe_feld_zweimal_wird_abgelehnt() -> None:
    with pytest.raises(regeln.RegelFehler, match="zweimal"):
        regeln.bedingungen_pruefen(
            [
                {"feld": "bewertung", "von": 5, "bis": None},
                {"feld": "bewertung", "von": 7, "bis": None},
            ]
        )


def test_ein_bereich_der_auf_nichts_zutrifft_wird_abgelehnt() -> None:
    """„von 8 bis 5“ ist leer - und wer das eintippt, meinte etwas anderes."""
    with pytest.raises(regeln.RegelFehler, match="träfe auf nichts"):
        regeln.bedingungen_pruefen([{"feld": "bewertung", "von": 8, "bis": 5}])


def test_ein_bereich_ohne_jede_grenze_wird_abgelehnt() -> None:
    with pytest.raises(regeln.RegelFehler, match="weder Unter- noch Obergrenze"):
        regeln.bedingungen_pruefen([{"feld": "bewertung", "von": None, "bis": None}])


def test_eine_leere_auswahl_wird_abgelehnt() -> None:
    with pytest.raises(regeln.RegelFehler, match="keine Auswahl"):
        regeln.bedingungen_pruefen([{"feld": "genre", "werte": []}])


def test_bestand_kennt_nur_drei_werte() -> None:
    with pytest.raises(regeln.RegelFehler, match="bestand"):
        regeln.bedingungen_pruefen([{"feld": "bestand", "werte": ["4k"]}])
    # Und die drei richtigen gehen durch.
    regeln.bedingungen_pruefen([{"feld": "bestand", "werte": ["hd", "uhd", "nichts"]}])


def test_text_wird_zu_text() -> None:
    """Eine Genre-Nummer darf als Zahl kommen und wird als Text abgelegt.

    Sonst haette dieselbe Bedingung je nach Aufrufer zwei Formen, und der
    Vergleich bei der Auswertung ginge einmal schief und einmal gut.
    """
    sauber = regeln.bedingungen_pruefen([{"feld": "genre", "werte": [99, "28"]}])
    assert sauber == [{"feld": "genre", "werte": ["99", "28"]}]


# ---------------------------------------------------------------------------
# 2. Die Tabelle: trifft die Bedingung zu?
# ---------------------------------------------------------------------------

VOLL = {
    "genres": (99, 18),
    "bewertung": 7.5,
    "stimmen": 640,
    "jahr": 2026,
    "laufzeit": 95,
    "sprache": "en",
    "altersfreigabe": 12,
    "qualitaet": "hd",
    "bestand": "uhd",
}

TABELLE = [
    # (Bedingung, trifft zu?)
    ({"feld": "typ", "werte": ["movie"]}, True),
    ({"feld": "typ", "werte": ["tv"]}, False),
    ({"feld": "typ", "werte": ["movie", "tv"]}, True),
    ({"feld": "genre", "werte": ["99"]}, True),
    ({"feld": "genre", "werte": ["28"]}, False),
    ({"feld": "genre", "werte": ["28", "18"]}, True),
    # ⚠️ Die Grenzen: „von“ schliesst ein, „bis“ schliesst aus. Genau so
    # lesen sich „ab 7,5“ und „unter 7,5“, und nur so ueberschneiden sich
    # zwei solche Regeln nicht.
    ({"feld": "bewertung", "von": 7.5, "bis": None}, True),
    ({"feld": "bewertung", "von": None, "bis": 7.5}, False),
    ({"feld": "bewertung", "von": None, "bis": 7.6}, True),
    ({"feld": "bewertung", "von": 7.6, "bis": None}, False),
    ({"feld": "bewertung", "von": 5, "bis": 8}, True),
    ({"feld": "stimmen", "von": 50, "bis": None}, True),
    ({"feld": "stimmen", "von": 1000, "bis": None}, False),
    ({"feld": "jahr", "von": 2026, "bis": None}, True),
    ({"feld": "jahr", "von": None, "bis": 2026}, False),
    ({"feld": "laufzeit", "von": None, "bis": 90}, False),
    ({"feld": "laufzeit", "von": 90, "bis": None}, True),
    ({"feld": "sprache", "werte": ["en"]}, True),
    ({"feld": "sprache", "werte": ["de"]}, False),
    ({"feld": "altersfreigabe", "von": None, "bis": 16}, True),
    ({"feld": "altersfreigabe", "von": 16, "bis": None}, False),
    ({"feld": "qualitaet", "werte": ["hd"]}, True),
    ({"feld": "qualitaet", "werte": ["uhd"]}, False),
    ({"feld": "bestand", "werte": ["uhd"]}, True),
    ({"feld": "bestand", "werte": ["nichts"]}, False),
]


@pytest.mark.parametrize("bedingung,erwartet", TABELLE)
def test_jede_bedingung_einzeln(bedingung: dict, erwartet: bool) -> None:
    assert regeln.passt(regel([bedingung]), titel(**VOLL)) is erwartet


def test_alle_bedingungen_muessen_zutreffen() -> None:
    """UND, nicht ODER. Eine falsche Bedingung kippt die ganze Regel."""
    gut = [
        {"feld": "typ", "werte": ["movie"]},
        {"feld": "genre", "werte": ["99"]},
        {"feld": "bewertung", "von": 5, "bis": None},
    ]
    assert regeln.passt(regel(gut), titel(**VOLL)) is True
    schlecht = gut + [{"feld": "sprache", "werte": ["de"]}]
    assert regeln.passt(regel(schlecht), titel(**VOLL)) is False


# ---------------------------------------------------------------------------
# 3. Was der Titel nicht weiss
# ---------------------------------------------------------------------------

UNBEKANNT = [
    ({"feld": "bewertung", "von": 5, "bis": None}, {"bewertung": None}),
    ({"feld": "bewertung", "von": None, "bis": 5}, {"bewertung": None}),
    ({"feld": "stimmen", "von": 50, "bis": None}, {"stimmen": None}),
    ({"feld": "jahr", "von": 2000, "bis": None}, {"jahr": None}),
    ({"feld": "laufzeit", "von": 60, "bis": None}, {"laufzeit": None}),
    ({"feld": "altersfreigabe", "von": None, "bis": 18}, {"altersfreigabe": None}),
    ({"feld": "genre", "werte": ["99"]}, {"genres": ()}),
    ({"feld": "sprache", "werte": ["de"]}, {"sprache": None}),
]


@pytest.mark.parametrize("bedingung,luecke", UNBEKANNT)
def test_was_der_titel_nicht_weiss_laesst_die_regel_scheitern(
    bedingung: dict, luecke: dict
) -> None:
    """⚠️ **Im Zweifel passiert nichts.**

    Andersherum waere es gefaehrlich: „Bewertung ab 8 -> freigeben“ wuerde bei
    jedem Titel greifen, dessen Bewertung TMDB gerade nicht kennt - und das
    sind vor allem die neuen. Auch die verneinende Richtung („unter 5
    ablehnen“) darf nicht greifen: Ein unbewerteter Titel ist nicht schlecht,
    er ist unbewertet.
    """
    daten = dict(VOLL)
    daten.update(luecke)
    assert regeln.passt(regel([bedingung]), titel(**daten)) is False


# ---------------------------------------------------------------------------
# 4. Welche Regel gewinnt
# ---------------------------------------------------------------------------


def _nutzer(db, rolle: Role = Role.user) -> User:
    person = User(username="prüfer", email="pruefer@beispiel.de", role=rolle)
    person.password_hash = "x"
    db.add(person)
    db.commit()
    return person


def test_die_erste_passende_regel_gewinnt(db_session) -> None:
    db_session.add_all(
        [
            regel([{"feld": "typ", "werte": ["movie"]}], position=0, name="erste"),
            regel(
                [{"feld": "typ", "werte": ["movie"]}],
                RegelEntscheidung.ablehnen,
                position=1,
                name="zweite",
            ),
        ]
    )
    db_session.commit()
    ergebnis = regeln.entscheiden(db_session, _nutzer(db_session), titel(**VOLL))
    assert ergebnis is not None
    assert ergebnis.regel.name == "erste"
    assert ergebnis.freigeben is True


def test_die_reihenfolge_haengt_an_position_nicht_am_zufall(db_session) -> None:
    """⚠️ Die spaeter angelegte Regel steht vorn, weil ihre Position kleiner ist.

    Ohne ``order_by(position)`` waere es die Reihenfolge der Datenbank - und
    die aendert sich, sobald jemand eine Regel loescht und neu anlegt.
    """
    db_session.add(regel([{"feld": "typ", "werte": ["movie"]}], position=5, name="spaet"))
    db_session.commit()
    db_session.add(
        regel(
            [{"feld": "typ", "werte": ["movie"]}],
            RegelEntscheidung.ablehnen,
            position=1,
            name="frueh",
        )
    )
    db_session.commit()
    ergebnis = regeln.entscheiden(db_session, _nutzer(db_session), titel(**VOLL))
    assert ergebnis is not None
    assert ergebnis.regel.name == "frueh"


def test_eine_abgeschaltete_regel_zaehlt_nicht(db_session) -> None:
    db_session.add_all(
        [
            regel(
                [{"feld": "typ", "werte": ["movie"]}],
                RegelEntscheidung.ablehnen,
                position=0,
                name="aus",
                aktiv=False,
            ),
            regel([{"feld": "typ", "werte": ["movie"]}], position=1, name="an"),
        ]
    )
    db_session.commit()
    ergebnis = regeln.entscheiden(db_session, _nutzer(db_session), titel(**VOLL))
    assert ergebnis is not None
    assert ergebnis.regel.name == "an"


def test_passt_keine_regel_entscheidet_das_konto(db_session) -> None:
    db_session.add(regel([{"feld": "typ", "werte": ["tv"]}], position=0))
    db_session.commit()
    assert regeln.entscheiden(db_session, _nutzer(db_session), titel(**VOLL)) is None


@pytest.mark.parametrize("rolle", [Role.approver, Role.admin])
def test_entscheider_und_administrator_sind_ausgenommen(db_session, rolle: Role) -> None:
    """Wie bei der Sperrliste, und aus demselben Grund.

    Die Regeln sind ihre eigene Entscheidung - sie sollen die anderen bremsen,
    nicht sie selbst. Sonst muesste man die eigene Regel abschalten, um einen
    Titel zu holen, den man bewusst will, und wuerde vergessen, sie wieder
    einzuschalten.
    """
    db_session.add(
        regel([{"feld": "typ", "werte": ["movie"]}], RegelEntscheidung.ablehnen)
    )
    db_session.commit()
    assert regeln.entscheiden(db_session, _nutzer(db_session, rolle), titel(**VOLL)) is None


def test_der_hausbestand_haengt_an_der_freigabe(db_session) -> None:
    """Eine ablehnende Regel bucht nichts aufs Haus - da gibt es nichts zu buchen."""
    db_session.add(
        regel(
            [{"feld": "typ", "werte": ["movie"]}],
            RegelEntscheidung.ablehnen,
            hausbestand=True,
        )
    )
    db_session.commit()
    ergebnis = regeln.entscheiden(db_session, _nutzer(db_session), titel(**VOLL))
    assert ergebnis is not None
    assert ergebnis.freigeben is False
    assert ergebnis.hausbestand is False


def test_trotzdem_fragen_haengt_an_der_ablehnung(db_session) -> None:
    db_session.add(
        regel([{"feld": "typ", "werte": ["movie"]}], trotzdem_fragen=True)
    )
    db_session.commit()
    ergebnis = regeln.entscheiden(db_session, _nutzer(db_session), titel(**VOLL))
    assert ergebnis is not None
    assert ergebnis.freigeben is True
    assert ergebnis.trotzdem_fragen is False


# ---------------------------------------------------------------------------
# Bodenschwelle
# ---------------------------------------------------------------------------

#: Gemessen am 03.09.2026. Kommt ein Feld dazu, ohne dass die Tabelle oben
#: waechst, faellt dieser Test - und genau das soll er.
MINDESTENS_FELDER = 10


def test_die_tabelle_deckt_jedes_feld_ab() -> None:
    """⚠️ **Eine Tabelle, die ein Feld vergisst, sichert nichts ueber dieses Feld zu.**

    Ohne diese Schwelle waere der haeufigste Fehler unsichtbar: ein neues Feld
    im Dienst, und niemand ergaenzt die Tabelle. Alle Tests blieben gruen, und
    das neue Feld waere ungeprueft.
    """
    geprueft = {b["feld"] for b, _ in TABELLE}
    assert geprueft == regeln.FELDER, (
        "Diese Felder stehen im Dienst, aber in keiner Zeile der Tabelle: "
        f"{sorted(regeln.FELDER - geprueft)}"
    )
    assert len(regeln.FELDER) >= MINDESTENS_FELDER

    ohne_luecke = {b["feld"] for b, _ in UNBEKANNT} | {"typ", "qualitaet", "bestand"}
    assert ohne_luecke == regeln.FELDER, (
        "Fuer diese Felder fehlt die Probe „was der Titel nicht weiss“: "
        f"{sorted(regeln.FELDER - ohne_luecke)}"
    )
