"""Die Schnittstelle der Grenze ``services/beschaffung/``.

Der Waechter (``test_beschaffung_grenze.py``) sagt, wer was importieren darf.
Hier steht, was man an der Grenze bekommt:

* **Fehler in drei Koerben.** Wer eine Anfrage stellt, muss wissen, ob sich
  ein zweiter Versuch lohnt - unabhaengig davon, ob Radarr oder spaeter
  nexcrate geantwortet hat.
* **Eine Liste der Zustaende** (N14), auf die jeder Weg abbildet.
* **Eine Schnittstelle, die der Weg ganz erfuellt** - und keine Methode am
  Weg, die nicht in der Schnittstelle steht. Sonst riefe ein Router eine
  ARR-Eigenheit, und der zweite Weg saehe davon nichts, bis es knallt.
* **Die Adressen des Wegs verdecken keine fremden.** Beim Umzug der
  Arr-Einstellungen hinter die Grenze wanderte ``/settings/test/{service}``
  in einen eigenen Router; eingebunden vor ``routers/settings`` fraesse es
  ``/settings/test/tmdb``.
"""

from __future__ import annotations

import inspect

import pytest

from app.db import SessionLocal
from app.main import app
from app.services import beschaffung
from app.services.beschaffung import (
    ZUSTAENDE,
    Beschaffung,
    BeschaffungError,
    Korb,
    get_beschaffung,
)
from app.services.beschaffung.arr.client import ArrError
from app.services.beschaffung.arr.weg import ArrBeschaffung
from app.services.settings_service import load_settings

# --- Fehler und Koerbe --------------------------------------------------------


@pytest.mark.parametrize(
    ("fehler", "korb"),
    [
        (BeschaffungError("x", ungewiss=True), Korb.voruebergehend),
        (BeschaffungError("x", 500), Korb.voruebergehend),
        (BeschaffungError("x", 503), Korb.voruebergehend),
        (BeschaffungError("x", 429), Korb.voruebergehend),
        (BeschaffungError("x", 400, code="irgendwas"), Korb.abgelehnt),
        (BeschaffungError("x", 404), Korb.abgelehnt),
        (BeschaffungError("x", code="tvdb_id_missing"), Korb.abgelehnt),
        (BeschaffungError("x"), Korb.unbekannt),
        (BeschaffungError("x", 500, korb=Korb.abgelehnt), Korb.abgelehnt),
    ],
)
def test_jeder_fehler_liegt_in_einem_korb(fehler: BeschaffungError, korb: Korb) -> None:
    assert fehler.korb == korb


@pytest.mark.parametrize(
    ("fehler", "korb"),
    [
        # Keine Antwort, oder eine fremde (Proxy-Anmeldeseite statt JSON): nochmal.
        (ArrError("x", ungewiss=True, code="arr_timeout"), Korb.voruebergehend),
        (ArrError("x", code="arr_unreachable"), Korb.voruebergehend),
        (ArrError("x", code="arr_unexpected_answer"), Korb.voruebergehend),
        (ArrError("x", 502, code="arr_http_error", status=502), Korb.voruebergehend),
        # Radarr hat verstanden und nein gesagt.
        (ArrError("x", 401, code="arr_key_rejected"), Korb.abgelehnt),
        (ArrError("x", 404, code="arr_path_unknown"), Korb.abgelehnt),
        (ArrError("x", 400, code="arr_http_error", status=400), Korb.abgelehnt),
        (ArrError("x", code="arr_not_configured"), Korb.abgelehnt),
    ],
)
def test_arr_fehler_landen_im_richtigen_korb(fehler: ArrError, korb: Korb) -> None:
    assert isinstance(fehler, BeschaffungError)
    assert fehler.korb == korb


def test_der_fehler_behaelt_kennung_und_werte() -> None:
    """Dieselbe Form wie ``meldungen.meldung`` - der Satz entsteht im Frontend."""
    fehler = ArrError("Radarr antwortet nicht.", code="arr_timeout", ungewiss=True, service="Radarr")
    assert fehler.als_meldung() == {
        "code": "arr_timeout",
        "message": "Radarr antwortet nicht.",
        "service": "Radarr",
    }
    assert fehler.ungewiss is True


def test_die_zustaende_stehen_einmal_und_vollstaendig() -> None:
    assert ZUSTAENDE == (
        "fehlt",
        "gesucht",
        "laedt",
        "da",
        "vorerst",
        "verbesserbar",
        "problem",
        "unbekannt",
    )


# --- Die Schnittstelle --------------------------------------------------------


def _einstellungen():
    with SessionLocal() as db:
        return load_settings(db)


def test_ohne_umstellung_beschafft_arr() -> None:
    """Eine Installation, die nichts umgestellt hat, laeuft ueber Radarr und Sonarr."""
    weg = get_beschaffung(_einstellungen())
    assert isinstance(weg, ArrBeschaffung)
    assert sorted(beschaffung.providers()) == ["arr", "nex"]


def test_die_betriebsart_waehlt_den_weg() -> None:
    """Die Einstellung entscheidet, und ein unbekannter Wert faellt auf ARR zurueck."""
    from dataclasses import replace

    from app.services.beschaffung.nex.weg import NexBeschaffung

    grund = _einstellungen()
    assert isinstance(get_beschaffung(replace(grund, beschaffung="nex")), NexBeschaffung)
    assert isinstance(get_beschaffung(replace(grund, beschaffung="arr")), ArrBeschaffung)
    assert isinstance(get_beschaffung(replace(grund, beschaffung="was-auch-immer")), ArrBeschaffung)


def test_der_arr_weg_erfuellt_die_ganze_schnittstelle() -> None:
    assert not ArrBeschaffung.__abstractmethods__


def _oeffentlich(klasse: type) -> set[str]:
    return {
        name
        for name, wert in vars(klasse).items()
        if not name.startswith("_") and (callable(wert) or isinstance(wert, classmethod))
    }


def test_der_arr_weg_hat_nichts_ausserhalb_der_schnittstelle() -> None:
    """Jede Methode am Weg steht auch an ``Beschaffung``.

    Eine Methode nur am ARR-Weg waere eine Tuer an der Grenze vorbei: Ein
    Router koennte sie rufen, und der NEX-Weg haette kein Gegenstueck.
    """
    nur_arr = _oeffentlich(ArrBeschaffung) - _oeffentlich(Beschaffung)
    assert not nur_arr, f"Am ARR-Weg, aber nicht in der Schnittstelle: {sorted(nur_arr)}"


def test_klassenmethoden_bleiben_klassenmethoden() -> None:
    """Was ohne Einstellungen gefragt wird, muss es am Weg auch ohne gehen."""
    for name, wert in vars(Beschaffung).items():
        if isinstance(wert, classmethod):
            assert isinstance(vars(ArrBeschaffung).get(name), classmethod), name


def test_asynchron_bleibt_asynchron() -> None:
    for name in _oeffentlich(Beschaffung):
        vorgabe = getattr(Beschaffung, name)
        umsetzung = getattr(ArrBeschaffung, name)
        assert inspect.iscoroutinefunction(vorgabe) == inspect.iscoroutinefunction(umsetzung), name


def test_arr_kann_was_der_bauplan_sagt() -> None:
    """Bauplan Abschnitt 3.1, Zeile ``faehigkeiten()``."""
    f = get_beschaffung(_einstellungen()).faehigkeiten()
    assert f.betreiberwerkzeuge is True
    assert (f.warum, f.vorschau, f.ereignisstrom, f.papierkorb) == (False, False, False, False)
    assert f.anime is True
    assert f.kalender is True
    assert f.wertungen == ("movie",)


# --- Adressen -------------------------------------------------------------------


def _pfade() -> list[tuple[str, str]]:
    return [
        (methode, route.path)
        for route in app.routes
        if hasattr(route, "methods")
        for methode in sorted(route.methods)
    ]


def test_feste_pfade_stehen_vor_dem_platzhalter() -> None:
    pfade = _pfade()
    platzhalter = pfade.index(("POST", "/api/settings/test/{service}"))
    for fest in ("tmdb", "smtp", "public-url"):
        assert pfade.index(("POST", f"/api/settings/test/{fest}")) < platzhalter, fest


def test_die_adressen_des_wegs_sind_eingebunden() -> None:
    """Die umgezogenen Adressen gibt es weiter, unter denselben Pfaden."""
    pfade = set(_pfade())
    for erwartet in [
        ("POST", "/api/settings/test/{service}"),
        ("GET", "/api/settings/recyclebin"),
        ("GET", "/api/settings/webhooks"),
        ("DELETE", "/api/settings/instanzen/{kennung}"),
        ("GET", "/api/settings/qualitaetsprofile"),
        ("POST", "/api/webhooks/arr/{kennung}"),
    ]:
        assert erwartet in pfade, erwartet
