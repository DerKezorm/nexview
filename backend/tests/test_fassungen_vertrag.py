"""Der Vertrag des Fassungsmodells: Jedes Stufen-Feld hat einen Nachfolger.

Bauplan NEX-Modus, Abschnitt 10. Die Bestandsaufnahme (H3) fand 14
Datenbankfelder mit Stufe. Jedes ist hier entschieden: Es ist umgezogen (und
sein Nachfolger muss es geben), oder es bleibt mit Grund.

Dazu die zweite Haelfte: **Kein Schreibweg setzt die Stufe.** ``tier`` an
Anfrage und Posten ist eine Ableitung aus der Fassung; wer sie setzt, schreibt
an der Kennung vorbei. Zur Laufzeit scheitert das ohnehin (die Eigenschaft hat
keinen Setter) - aber nur auf dem Weg, der gerade laeuft. Der Scan findet auch
den, den kein Test betritt.

Und die dritte Haelfte, seit Scheibe 3: **Das Aufzaehlungsfeld ``QualityTier``
ist weg.** Die Stufe ist ein Wort (``standard``/``uhd``), abgeleitet aus der
Fassung, und nur noch hinter der Grenze und in ``/api/v1`` zu lesen. Wer es
wieder einfuehrt - im Backend oder in der Oberflaeche -, faellt hier auf.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app import models
from app.db import STUFEN_SPALTEN
from app.models import Base

APP = Path(__file__).resolve().parent.parent / "app"

#: Die 14 Felder aus H3 und was aus ihnen wurde: ``(Tabelle, Spalte)`` ->
#: ``("zieht", Nachfolger)`` oder ``("bleibt", Grund)``. Ein Nachfolger ist
#: ``Tabelle.Spalte`` oder eine ganze Tabelle.
FELDER: dict[tuple[str, str], tuple[str, str]] = {
    ("users", "can_request_uhd_movies"): ("zieht", "fassung_rechte.anfragen"),
    ("users", "can_request_uhd_series"): ("zieht", "fassung_rechte.anfragen"),
    ("users", "auto_approve_uhd"): ("zieht", "fassung_rechte.auto_freigabe"),
    ("users", "blocked_movie_uhd_profiles"): (
        "bleibt",
        "Sperrlisten fuer Profile sind ARR-Sache und ziehen mit hinter die Grenze",
    ),
    ("users", "blocked_series_uhd_profiles"): (
        "bleibt",
        "Sperrlisten fuer Profile sind ARR-Sache und ziehen mit hinter die Grenze",
    ),
    ("auth_tokens", "invite_can_request_uhd_movies"): ("zieht", "auth_tokens.invite_fassung_rechte"),
    ("auth_tokens", "invite_can_request_uhd_series"): ("zieht", "auth_tokens.invite_fassung_rechte"),
    ("auth_tokens", "invite_auto_approve_uhd"): ("zieht", "auth_tokens.invite_fassung_rechte"),
    ("storage_entries", "tier"): ("zieht", "storage_entries.fassung_kennung"),
    ("media_requests", "tier"): ("zieht", "media_requests.fassung_kennung"),
    ("media_server_library", "has_standard"): (
        "bleibt",
        "Klasse aus der Aufloesung des Medienservers, keine Fassung",
    ),
    ("media_server_library", "has_uhd"): ("bleibt", "Klasse, keine Fassung"),
    ("media_server_library", "size_standard"): ("bleibt", "Klasse, keine Fassung"),
    ("media_server_library", "size_uhd"): ("bleibt", "Klasse, keine Fassung"),
}


def _spalten() -> dict[str, set[str]]:
    return {t.name: {s.name for s in t.columns} for t in Base.metadata.sorted_tables}


def test_alle_vierzehn_felder_sind_entschieden() -> None:
    assert len(FELDER) == 14


def test_jeder_nachfolger_existiert_und_das_alte_feld_ist_weg() -> None:
    spalten = _spalten()
    for (tabelle, spalte), (art, ziel) in FELDER.items():
        if art == "bleibt":
            assert spalte in spalten[tabelle], f"{tabelle}.{spalte} sollte bleiben: {ziel}"
            continue
        ziel_tabelle, _, ziel_spalte = ziel.partition(".")
        assert ziel_spalte in spalten[ziel_tabelle], f"Nachfolger {ziel} fehlt"
        assert spalte not in spalten[tabelle], f"{tabelle}.{spalte} steht noch im Modell"


def test_die_wanderung_entfernt_genau_die_umgezogenen_felder() -> None:
    """``STUFEN_SPALTEN`` in ``db.py`` und diese Liste muessen dasselbe sagen."""
    umgezogen = {
        (tabelle, spalte) for (tabelle, spalte), (art, _) in FELDER.items() if art == "zieht"
    }
    entfernt = {(t, s) for t, alle in STUFEN_SPALTEN.items() for s in alle}
    assert entfernt == umgezogen


#: Der Ordner der Oberflaeche - auch dort darf die Stufe kein Typ mehr sein.
FRONTEND = APP.parent.parent / "frontend" / "src"


def test_die_stufe_ist_kein_aufzaehlungsfeld_mehr() -> None:
    assert not hasattr(models, "QualityTier")


def test_kein_leser_von_quality_tier_im_backend() -> None:
    """Der Scan liest den ganzen Quelltext, nicht nur die Wege eines Tests."""
    dateien = [p for p in APP.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(dateien) >= 150, "der Scan liest nicht, was er soll"
    funde = {
        p.relative_to(APP).as_posix(): [
            knoten.lineno
            for knoten in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
            if (isinstance(knoten, ast.Name) and knoten.id == "QualityTier")
            or (isinstance(knoten, ast.Attribute) and knoten.attr == "QualityTier")
            or (isinstance(knoten, ast.alias) and knoten.name == "QualityTier")
        ]
        for p in dateien
    }
    assert not {k: v for k, v in funde.items() if v}, "QualityTier hat wieder Leser"


def test_kein_leser_von_quality_tier_in_der_oberflaeche() -> None:
    """Die Oberflaeche spricht Fassungen - ``QualityTier`` gibt es dort nicht mehr."""
    dateien = [
        p
        for p in FRONTEND.rglob("*.ts*")
        if "node_modules" not in p.parts
    ]
    assert len(dateien) >= 50, "der Scan liest nicht, was er soll"
    funde = [
        p.relative_to(FRONTEND).as_posix()
        for p in dateien
        if "QualityTier" in p.read_text(encoding="utf-8")
    ]
    assert not funde, f"QualityTier steht noch in der Oberflaeche: {funde}"


def test_die_alten_namen_leben_als_sicht_weiter() -> None:
    """Oberflaeche und Kontodialog sprechen sie bis Scheibe 3 - als Eigenschaft, nicht Spalte."""
    for name in ("can_request_uhd_movies", "can_request_uhd_series", "auto_approve_uhd"):
        assert isinstance(getattr(models.User, name), property), name
    for name in (
        "invite_can_request_uhd_movies",
        "invite_can_request_uhd_series",
        "invite_auto_approve_uhd",
    ):
        assert isinstance(getattr(models.AuthToken, name), property), name
    for modell in (models.MediaRequest, models.StorageEntry):
        assert isinstance(modell.__dict__["tier"], property)
    # Und die Kennung unter dem Namen, den die Antwort traegt.
    assert isinstance(models.MediaRequest.__dict__["fassung"], property)


def schreibt_die_stufe(quelle: str) -> list[int]:
    """Zeilen, an denen die Stufe einer Anfrage oder eines Postens gesetzt wird."""
    treffer = []
    for knoten in ast.walk(ast.parse(quelle)):
        if isinstance(knoten, ast.Call):
            name = getattr(knoten.func, "id", None) or getattr(knoten.func, "attr", None)
            if name in ("MediaRequest", "StorageEntry") and any(
                k.arg == "tier" for k in knoten.keywords
            ):
                treffer.append(knoten.lineno)
        elif isinstance(knoten, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            ziele = knoten.targets if isinstance(knoten, ast.Assign) else [knoten.target]
            treffer += [
                knoten.lineno
                for ziel in ziele
                if isinstance(ziel, ast.Attribute) and ziel.attr == "tier"
            ]
    return treffer


def test_kein_schreibweg_setzt_die_stufe() -> None:
    dateien = [p for p in APP.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(dateien) >= 150, "der Scan liest nicht, was er soll"
    funde = {
        p.relative_to(APP).as_posix(): zeilen
        for p in dateien
        if (zeilen := schreibt_die_stufe(p.read_text(encoding="utf-8")))
    }
    assert not funde, f"Hier wird die Stufe geschrieben statt der Kennung: {funde}"


def test_der_scan_sieht_beide_schreibweisen() -> None:
    quelle = (
        "a = MediaRequest(user_id=1, tier='uhd')\n"
        "b = models.StorageEntry(tier=x)\n"
        "anfrage.tier = 'standard'\n"
        "c = MediaRequest(fassung_kennung='radarr-uhd')\n"
        "d = Titel(tier=1)\n"
    )
    assert sorted(schreibt_die_stufe(quelle)) == [1, 2, 3]
