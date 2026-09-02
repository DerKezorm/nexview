"""Filmografie einer Person - besonders bei Regie und Drehbuch.

Der Fehler, den das absichert: bei einem Regisseur zog die Seite nur die
Schauspiel-Credits (``combined_credits.cast``) und zeigte damit bloss seine
Gastauftritte. Die eigentliche Arbeit steht in der Crew - genau das wird hier
geprueft. TMDB wird untergeschoben.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.routers import details
from app.services.settings_service import save_settings
from tests.conftest import auth_headers, create_user

BACKEND = Path(__file__).resolve().parent.parent

PFAD = "/api/person/488"  # die Kennung ist beliebig, der Client faelscht ohnehin


class FakeClient:
    """Ein Regisseur, der einen Film inszeniert, einen geschrieben, in einem
    dritten kurz auftritt und in einer Talkshow zu Gast war."""

    async def person(self, person_id: int) -> dict[str, Any]:
        return {
            "id": person_id,
            "name": "Steven Spielberg",
            "known_for_department": "Directing",
            "combined_credits": {
                "crew": [
                    {"id": 100, "media_type": "movie", "job": "Director",
                     "title": "Inszeniert", "popularity": 90, "vote_average": 8.0},
                    {"id": 101, "media_type": "movie", "job": "Screenplay",
                     "title": "Geschrieben", "popularity": 70, "vote_average": 7.0},
                    # Produktion ist kein eigenstaendiges Werk - faellt raus.
                    {"id": 102, "media_type": "movie", "job": "Producer",
                     "title": "Nur produziert", "popularity": 60},
                ],
                "cast": [
                    # Gleicher Titel wie die Regie-Arbeit: die Regie muss gewinnen.
                    {"id": 100, "media_type": "movie", "character": "Mann an der Bar",
                     "title": "Inszeniert", "popularity": 90},
                    # Reiner Gastauftritt in einer Talkshow.
                    {"id": 300, "media_type": "tv", "character": "Self",
                     "name": "Abendshow", "genre_ids": [10767], "popularity": 95},
                ],
            },
        }


@pytest.fixture
def nutzer(admin_client: TestClient) -> dict[str, str]:
    create_user(admin_client, "lena")
    return auth_headers(admin_client, "lena", "passwort-1234")


@pytest.fixture
def mit_tmdb(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    with SessionLocal() as db:
        save_settings(db, {"tmdb_api_key": "test-schluessel"})
    monkeypatch.setattr(details.media, "_client", lambda settings, region=None: FakeClient())


def _nach_id(credits: list[dict[str, Any]], tmdb_id: int) -> dict[str, Any] | None:
    return next((c for c in credits if c["tmdb_id"] == tmdb_id), None)


def test_regie_erscheint_und_schlaegt_den_gastauftritt(
    admin_client: TestClient, nutzer: dict[str, str], mit_tmdb
) -> None:
    credits = admin_client.get(PFAD, headers=nutzer).json()["credits"]

    inszeniert = _nach_id(credits, 100)
    assert inszeniert is not None, "der inszenierte Film fehlt"
    assert inszeniert["kind"] == "movie"
    # Regie hat Vorrang vor der Cameo-Rolle am selben Titel.
    assert inszeniert["character"] == "Regie"


def test_drehbuch_zaehlt_als_werk(
    admin_client: TestClient, nutzer: dict[str, str], mit_tmdb
) -> None:
    credits = admin_client.get(PFAD, headers=nutzer).json()["credits"]
    geschrieben = _nach_id(credits, 101)
    assert geschrieben is not None
    assert geschrieben["kind"] == "movie"
    assert geschrieben["character"] == "Drehbuch"


def test_produktion_ist_kein_werk(
    admin_client: TestClient, nutzer: dict[str, str], mit_tmdb
) -> None:
    """Producer, Kamera, Schnitt gehoeren nicht in die Filmografie."""
    credits = admin_client.get(PFAD, headers=nutzer).json()["credits"]
    assert _nach_id(credits, 102) is None


def test_talkshow_bleibt_auftritt(
    admin_client: TestClient, nutzer: dict[str, str], mit_tmdb
) -> None:
    credits = admin_client.get(PFAD, headers=nutzer).json()["credits"]
    talk = _nach_id(credits, 300)
    assert talk is not None
    assert talk["kind"] == "appearance"


#: Die Funktionen, die auf jedem Eintrag Felder setzen - Datei und Name.
#:
#: ``uhd.anreichern`` steht daneben, weil ``_mit_status`` es als letzten
#: Schritt ruft und es auf denselben Eintraegen schreibt.
STATUS_SETZER = (
    ("app/routers/details.py", "_mit_status"),
    ("app/services/uhd.py", "anreichern"),
)


def _zielnamen(ziel: ast.AST) -> set[str]:
    """Die Attributnamen eines Zuweisungsziels - auch bei ``a.x, a.y = ...``."""
    if isinstance(ziel, ast.Attribute):
        return {ziel.attr}
    if isinstance(ziel, (ast.Tuple, ast.List)):
        namen: set[str] = set()
        for teil in ziel.elts:
            namen |= _zielnamen(teil)
        return namen
    return set()


def _gesetzte_felder(datei: str, funktion: str) -> set[str]:
    """Welche Felder setzt diese Funktion auf den durchgereichten Eintraegen?

    ⚠️ **Abgezogen wird, was der Code selbst mit ``hasattr`` absichert.** Der
    Ablageort (``path``, ``path_uhd``) und ``uhd_in_standard`` werden nur
    gesetzt, wo das Ziel sie kennt - genau deshalb *muss* ``PersonCredit`` sie
    nicht haben. Wer diese Absicherung entfernt, bekommt das Feld hier
    automatisch als Forderung zurueck.
    """
    quelle = (BACKEND / datei).read_text(encoding="utf-8")
    baum = ast.parse(quelle, filename=datei)
    for knoten in ast.walk(baum):
        if not isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if knoten.name != funktion:
            continue
        gesetzt: set[str] = set()
        abgesichert: set[str] = set()
        for inner in ast.walk(knoten):
            if isinstance(inner, ast.Assign):
                for ziel in inner.targets:
                    gesetzt |= _zielnamen(ziel)
            elif isinstance(inner, (ast.AugAssign, ast.AnnAssign)):
                gesetzt |= _zielnamen(inner.target)
            elif (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "hasattr"
                and len(inner.args) == 2
                and isinstance(inner.args[1], ast.Constant)
            ):
                abgesichert.add(inner.args[1].value)
        return gesetzt - abgesichert
    raise AssertionError(f"{funktion} steht nicht mehr in {datei} - Wächter läuft leer.")


def test_personenseite_kennt_jedes_status_feld() -> None:
    """Was ``_mit_status`` setzt, muss es auf *beiden* Modellen geben.

    Der Fehler dahinter ist dreimal passiert - erst mit ``watched``, dann mit
    ``status_uhd``, dann mit ``watched_on``/``watched_not_on``. Jedes Mal
    setzt ``details._mit_status`` ein Feld auf jedem Eintrag, und jedes Mal
    kannte ``PersonCredit`` es nicht: Pydantic laesst kein undeklariertes Feld
    zu, die Personenseite antwortete mit 500.

    Auffallen konnte das jeweils nur unter einer Zusatzbedingung (ein
    gesehener Titel bzw. eine eingerichtete 4K-Instanz) - deshalb hier ein
    Abgleich der Felder statt eines weiteren Einzelfalls.

    ⚠️ **Und die Feldmenge steht nicht mehr von Hand da.** Sie stand es, und
    sie hing bereits zwei Felder hinterher: ``watched_on`` und
    ``watched_not_on`` fehlten, gefangen hat den Fall stattdessen ein Test,
    der zufaellig die Zusatzbedingung herstellte. Jetzt wird sie aus dem
    Quelltext der setzenden Funktionen abgeleitet, damit der vierte Fall
    derselben Sorte von selbst mit drin ist.
    """
    from app.schemas_media import MediaItem, PersonCredit

    gesetzt: set[str] = set()
    for datei, funktion in STATUS_SETZER:
        gesetzt |= _gesetzte_felder(datei, funktion)
    # Nur die Felder, die ueberhaupt auf einem Eintrag stehen - lokale
    # Zuweisungen an ``settings.x`` und dergleichen gehen hier nicht ein.
    gesetzt &= set(MediaItem.model_fields)

    # ⚠️ Ohne diese Probe koennte die Ableitung leer laufen und der Test waere
    # wieder hohl, nur eleganter. Die drei Namen sind genau die Faelle, an
    # denen die Personenseite schon einmal mit 500 gescheitert ist.
    for pflicht in ("status", "watched", "status_uhd", "watched_on", "watched_not_on"):
        assert pflicht in gesetzt, (
            f"{pflicht!r} fällt aus der Ableitung heraus - der Wächter läuft leer."
        )

    fehlt = gesetzt - set(PersonCredit.model_fields)
    assert not fehlt, f"PersonCredit fehlen Felder aus _mit_status: {sorted(fehlt)}"
