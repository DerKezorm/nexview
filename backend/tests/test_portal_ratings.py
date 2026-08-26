"""Die Bewertungen von IMDb, Rotten Tomatoes und Metacritic.

⚠️ **Diese Datei gibt es, weil es sie nicht gab.**

In 0.19.0 zog die Haushalts-Bewertung vom Auftrag auf den Titel um. Dabei
wurde ``services/ratings.py`` vollstaendig neu geschrieben - und mit der Datei
verschwanden die Portal-Wertungen, die vorher darin lagen. Der Aufrufer in
``routers/details.py`` blieb stehen und zeigte ins Leere:

    AttributeError: module 'app.services.ratings' has no attribute 'for_movies'

Jede Anfrage an ``/api/ratings/movie`` endete damit in einem 500, in 0.19.0
und 0.20.0. Gemerkt hat es niemand, aus drei Gruenden, die zusammenkamen: die
Oberflaeche zeigt dann einfach keine Abzeichen an statt einer Meldung, zwei
verschiedene Dinge hiessen "ratings", und **kein einziger Test rief den
Endpunkt auf**.

Der erste Test hier haette gereicht. Er braucht kein Radarr, keine Attrappe
und keine Daten - nur einen Aufruf.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import portal_ratings


@pytest.fixture(autouse=True)
def leerer_zwischenspeicher() -> None:
    """Der Zwischenspeicher lebt im Arbeitsspeicher und ueberlebt sonst den Test."""
    portal_ratings.reset_cache()


def test_der_endpunkt_antwortet_ueberhaupt(admin_client: TestClient) -> None:
    """⚠️ Der Test, der den Rueckschritt aus 0.19.0 gefunden haette.

    Ohne eingerichtetes Radarr gibt es nichts zu holen - die richtige Antwort
    darauf ist eine leere Liste, nicht ein Serverfehler.
    """
    antwort = admin_client.get("/api/ratings/movie", params={"ids": "603"})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json() == {}


def test_ohne_kennungen_kommt_nichts(admin_client: TestClient) -> None:
    antwort = admin_client.get("/api/ratings/movie", params={"ids": ""})
    assert antwort.status_code == 200
    assert antwort.json() == {}


def test_unsinnige_kennungen_werden_uebergangen(admin_client: TestClient) -> None:
    """Was keine Zahl ist, faellt weg - statt die Anfrage scheitern zu lassen."""
    antwort = admin_client.get("/api/ratings/movie", params={"ids": "abc,,-5,603"})
    assert antwort.status_code == 200


def test_braucht_eine_anmeldung(client: TestClient) -> None:
    assert client.get("/api/ratings/movie", params={"ids": "603"}).status_code == 401


def test_mit_radarr_kommen_die_wertungen_durch(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der ganze Weg: Radarrs Antwort bis ins JSON der API."""

    class Attrappe:
        async def get(self, pfad: str, params: dict) -> dict:
            assert pfad == "/movie/lookup/tmdb"
            return {
                "imdbId": "tt0133093",
                "ratings": {
                    "imdb": {"value": 8.7, "votes": 1_900_000},
                    "rottenTomatoes": {"value": 83},
                    "metacritic": {"value": 73},
                },
            }

    monkeypatch.setattr(portal_ratings, "radarr_client", lambda _settings: Attrappe())

    antwort = arr_client.get("/api/ratings/movie", params={"ids": "603"})
    assert antwort.status_code == 200, antwort.text
    wert = antwort.json()["603"]
    assert wert["imdb"] == 8.7
    assert wert["imdb_votes"] == 1_900_000
    assert wert["rotten_tomatoes"] == 83
    assert wert["metacritic"] == 73
    # Ohne die Kennung bliebe auf der Oberflaeche nur eine IMDb-Suche statt
    # eines Links auf den Titel.
    assert wert["imdb_id"] == "tt0133093"


def test_eine_null_ist_keine_wertung(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Radarr liefert Eintraege auch ohne Wertung - dann steht dort eine 0.

    "0 von 10" anzuzeigen waere schlicht falsch; der Film hat keine Wertung,
    keine schlechte.
    """

    class Attrappe:
        async def get(self, _pfad: str, _params: dict) -> dict:
            return {
                "imdbId": "tt0000000",
                "ratings": {
                    "imdb": {"value": 0, "votes": 0},
                    "rottenTomatoes": {"value": 0},
                    "metacritic": {"value": 0},
                },
            }

    monkeypatch.setattr(portal_ratings, "radarr_client", lambda _settings: Attrappe())

    antwort = arr_client.get("/api/ratings/movie", params={"ids": "603"})
    assert antwort.status_code == 200
    # Ein Film ganz ohne Wertung taucht gar nicht erst auf.
    assert antwort.json() == {}


def test_ein_ausfall_bei_radarr_laesst_die_seite_stehen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bewertungen sind Beiwerk. Faellt Radarr aus, fehlen sie - mehr nicht."""
    from app.services.arr import ArrError

    class Attrappe:
        async def get(self, _pfad: str, _params: dict) -> dict:
            raise ArrError("Radarr ist nicht erreichbar")

    monkeypatch.setattr(portal_ratings, "radarr_client", lambda _settings: Attrappe())

    antwort = arr_client.get("/api/ratings/movie", params={"ids": "603"})
    assert antwort.status_code == 200
    assert antwort.json() == {}
