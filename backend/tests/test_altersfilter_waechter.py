"""Der Waechter: Kommt ein gesperrter Titel doch irgendwo heraus?

⚠️ **Warum es diese Datei gibt.** ``services/age_rating.py`` ist gut geprueft,
zwanzig Tests, Laendertabellen mit ausgeschriebener Begruendung. Die Funktion
war nie das Problem. Das Problem war, dass niemand nachsah, ob jede Liste sie
auch **ruft**.

Nachgewiesen am 02.09.2026: Ein ``if True or`` vor dem Rueckfall in
``routers/favorites.py`` nahm der Favoritenliste das Sieb, und alle 2.491 Tests
blieben gruen. Ein Konto mit Altersgrenze 12 sah danach den FSK-18-Titel in
seinen Favoriten.

⚠️ **Und warum es hier zur Laufzeit misst und nicht im Quelltext.** Der erste
Anlauf war ein Aufrufgraph ueber den Quelltext: Jede Titeladresse muss
``_darf_sehen`` oder ``erlaubte_kennungen`` erreichen. Der fiel durch seine
eigene Gegenprobe. ``my_favorites`` **enthaelt** den Aufruf naemlich weiterhin,
das eingebaute ``if True or`` machte ihn nur unerreichbar, und toten Code sieht
kein Baum-Scan. Ein Waechter, der genau den belegten Fall nicht findet, ist
keiner.

Hier wird deshalb das Ergebnis gemessen, nicht die Absicht: Ein Konto mit
Altersgrenze 12, ein Katalog aus zwei Titeln, und dann jede Adresse einmal
angerufen.

**Jede Probe prueft zwei Dinge, und das zweite ist das wichtigere:**

* Der gesperrte Titel darf in der Antwort **nicht** vorkommen.
* Der erlaubte Titel **muss** vorkommen.

Ohne den zweiten Teil waere der Waechter hohl: Eine Adresse, die mit 500
antwortet oder eine leere Liste liefert, enthaelt den gesperrten Titel
selbstverstaendlich auch nicht und haette die Probe bestanden, ohne je etwas
ausgeliefert zu haben.

⚠️ **Der Beispielbetrieb siebt nicht, und das ist Absicht dieser Datei nicht zu
verdecken.** ``media.discover`` und die Nachbarn kehren bei
``settings.use_demo_data`` zurueck, bevor irgendein Filter laeuft; ein Konto
mit Altersgrenze 6 sieht dort die FSK-16-Eintraege der Attrappe. Betroffen sind
nur die 24 erfundenen Titel aus ``mocks/demo_data.py`` - einen echten Katalog
gibt es in diesem Zustand gar nicht. Die Proben hier laufen deshalb
ausdruecklich **mit** gesetztem TMDB-Schluessel, sonst pruefte diese Datei
einen Weg, den im Betrieb niemand geht.
"""

from __future__ import annotations

import typing
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.main import app
from app.schemas_media import MediaDetail, MediaItem, MediaPage
from app.services import media

from .conftest import auth_headers, create_user

# --- Der Katalog, an dem gemessen wird --------------------------------------

#: Der Titel, den ein Zwoelfjaehriger sehen darf.
ERLAUBT = 700006
#: Und der, den er nicht sehen darf.
GESPERRT = 700018
#: Ein dritter, ebenfalls erlaubt.
#:
#: ⚠️ Nur fuer die Empfehlungen, und das hat einen Grund: Eine Detailseite
#: schlaegt sich nicht selbst vor. Wer die Empfehlungen zu ``ERLAUBT`` abruft,
#: bekommt nur noch ``GESPERRT`` angeboten - der faellt zu Recht heraus, die
#: Liste ist leer, und die Probe haette nichts gemessen. Deshalb wird von einem
#: dritten Titel aus gefragt.
NEUTRAL = 700012

_STUFEN = {ERLAUBT: "6", GESPERRT: "18", NEUTRAL: "12"}


def _roh(tmdb_id: int, media_type: str) -> dict[str, Any]:
    """Ein TMDB-Datensatz, ausfuehrlich genug fuer alle Wege."""
    stufe = _STUFEN[tmdb_id]
    daten: dict[str, Any] = {
        "id": tmdb_id,
        "title": f"Titel {tmdb_id}",
        "name": f"Titel {tmdb_id}",
        "overview": "Eine Beschreibung.",
        "poster_path": "/p.jpg",
        "backdrop_path": "/b.jpg",
        "vote_average": 7.0,
        # Deutlich ueber ``home.SUGGESTION_MIN_VOTES`` (200): Die Startseite
        # laesst einen erschienenen Titel mit weniger Stimmen gar nicht erst
        # als Vorschlag durch, und die Probe haette dort nichts gemessen.
        "vote_count": 5000,
        "genre_ids": [28],
        "genres": [{"id": 28, "name": "Action"}],
        "release_date": "2020-01-01",
        "first_air_date": "2020-01-01",
        "runtime": 100,
        "episode_run_time": [45],
        "status": "Released",
        "popularity": 10.0,
    }
    if media_type == "movie":
        daten["release_dates"] = {
            "results": [
                {"iso_3166_1": "DE", "release_dates": [{"certification": stufe}]}
            ]
        }
    else:
        daten["content_ratings"] = {
            "results": [{"iso_3166_1": "DE", "rating": stufe}]
        }
        daten["external_ids"] = {"tvdb_id": tmdb_id}
        daten["seasons"] = []
    return daten


def _seite(media_type: str) -> dict[str, Any]:
    return {
        "page": 1,
        "total_pages": 1,
        "total_results": 2,
        # Bewusst ohne ``NEUTRAL``: Der dient nur als Ausgangspunkt fuer die
        # Empfehlungen und soll die uebrigen Proben nicht mittragen.
        "results": [_roh(ERLAUBT, media_type), _roh(GESPERRT, media_type)],
    }


class _FakeTmdb:
    """Ein TMDB-Client mit genau zwei Titeln.

    Beantwortet jeden Weg, den die Titeladressen nehmen. Was hier fehlt, faellt
    sofort auf: Die betroffene Adresse liefert dann den erlaubten Titel nicht
    mehr, und die Probe wird rot statt still gruen.
    """

    def __init__(self, api_key: str = "", language: str = "de", region: str = "DE") -> None:
        self.region = region

    async def discover(self, media_type: str, *a: Any, **k: Any) -> dict[str, Any]:
        return _seite(media_type)

    async def browse(self, media_type: str, *a: Any, **k: Any) -> dict[str, Any]:
        return _seite(media_type)

    async def search(self, media_type: str, *a: Any, **k: Any) -> dict[str, Any]:
        return _seite(media_type)

    async def recommendations(self, media_type: str, *a: Any, **k: Any) -> dict[str, Any]:
        return _seite(media_type)

    async def similar(self, media_type: str, *a: Any, **k: Any) -> dict[str, Any]:
        return _seite(media_type)

    async def detail(self, media_type: str, tmdb_id: int, **k: Any) -> dict[str, Any]:
        if tmdb_id not in _STUFEN:
            raise media.TmdbError("Nicht vorhanden.", 404)
        return _roh(tmdb_id, media_type)

    async def details(self, media_type: str, tmdb_ids: list[int]) -> dict[int, dict[str, Any]]:
        return {i: _roh(i, media_type) for i in tmdb_ids if i in _STUFEN}

    async def genres(self, media_type: str) -> dict[int, str]:
        return {28: "Action"}

    async def certification_list(self, media_type: str) -> dict[str, Any]:
        return {"certifications": {"DE": [{"certification": s} for s in ("0", "6", "12", "16", "18")]}}

    async def season(self, tmdb_id: int, season_number: int) -> dict[str, Any]:
        return {"episodes": []}

    async def person(self, person_id: int) -> dict[str, Any]:
        return {
            "id": person_id,
            "name": "Jemand",
            "combined_credits": {
                "cast": [_roh(i, "movie") | {"media_type": "movie"} for i in _STUFEN],
                "crew": [],
            },
        }

    async def search_person(self, *a: Any, **k: Any) -> dict[str, Any]:
        return {"page": 1, "total_pages": 1, "total_results": 0, "results": []}

    async def popular_people(self, *a: Any, **k: Any) -> dict[str, Any]:
        return {"page": 1, "total_pages": 1, "total_results": 0, "results": []}

    async def keyword(self, *a: Any, **k: Any) -> dict[str, Any]:
        return _seite("movie")

    async def company(self, *a: Any, **k: Any) -> dict[str, Any]:
        return _seite("movie")

    async def find_by_tvdb(self, *a: Any, **k: Any) -> dict[str, Any]:
        return {"tv_results": []}


# --- Welche Adressen liefern ueberhaupt Titel aus? --------------------------

TITELMODELLE = {MediaItem, MediaDetail, MediaPage}


def _traegt_titelmodell(annotation: object, tiefe: int = 0) -> bool:
    if tiefe > 4:
        return False
    if isinstance(annotation, type) and annotation in TITELMODELLE:
        return True
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return any(
            _traegt_titelmodell(feld.annotation, tiefe + 1)
            for feld in annotation.model_fields.values()
        )
    return any(
        _traegt_titelmodell(teil, tiefe + 1)
        for teil in (typing.get_args(annotation) or ())
    )


def titeladressen() -> set[str]:
    """Adressen, deren Antwort ein Titelmodell traegt - aus der Routentabelle."""
    gefunden = set()
    for route in app.routes:
        pfad = str(getattr(route, "path", ""))
        if not pfad.startswith("/api"):
            continue
        methoden = sorted(set(getattr(route, "methods", None) or ()) - {"HEAD", "OPTIONS"})
        if not methoden:
            continue
        if _traegt_titelmodell(getattr(route, "response_model", None)):
            gefunden.add(f"{methoden[0]} {pfad}")
    return gefunden


#: Die Adressen, die wirklich angerufen werden, mit fertigem Aufruf.
#:
#: ⚠️ Der Schluessel ist derselbe Name, den ``titeladressen()`` liefert. Wer
#: eine Adresse hinzufuegt, ohne sie hier oder in ``OHNE_PROBE`` einzutragen,
#: macht ``test_jede_titeladresse_ist_entschieden`` rot.
PROBEN: dict[str, str] = {
    "GET /api/discover/{media_type}": "/api/discover/movie",
    "GET /api/browse/{media_type}": "/api/browse/movie?keyword_id=1",
    "GET /api/search/{media_type}": "/api/search/movie?q=Titel",
    "GET /api/home/trending": "/api/home/trending",
    "GET /api/media/{media_type}/{tmdb_id}": f"/api/media/movie/{GESPERRT}",
    "GET /api/detail/{media_type}/{tmdb_id}": f"/api/detail/movie/{GESPERRT}",
    "GET /api/detail/{media_type}/{tmdb_id}/recommendations": (
        f"/api/detail/movie/{NEUTRAL}/recommendations"
    ),
    "GET /api/v1/search/{media_type}": "/api/v1/search/movie?q=Titel",
    "GET /api/v1/media/{media_type}/{tmdb_id}": f"/api/v1/media/movie/{GESPERRT}",
}

#: Titeladressen, die hier **nicht** angerufen werden - jede mit Grund.
#:
#: ⚠️ Keine Ablage fuer alles, was gerade unbequem ist. Ein Eintrag heisst:
#: "Diese Adresse ist anderswo gedeckt, und hier steht wo."
OHNE_PROBE: dict[str, str] = {
    "GET /api/home/curated": (
        "Baut ihre Reihen aus den Vormerkungen und Favoriten des Kontos und "
        "liefert bei leerem Konto nichts. Sie geht durch dieselbe "
        "``_to_items``-Schleife wie /api/home/trending, das hier angerufen wird."
    ),
    "GET /api/stoebern/filter/{media_type}": (
        "Braucht einen eingerichteten Regal-Satz. Der Weg dahinter ist "
        "``media.browse``, und der wird ueber /api/browse/{media_type} gemessen."
    ),
    "GET /api/stoebern/regal/{media_type}/{kennung}": (
        "Dieselbe Ueberlegung wie beim Filter darueber."
    ),
    "POST /api/stoebern/filmabend/ergebnis/{media_type}": (
        "Braucht eine laufende Filmabend-Runde mit Stimmen. Liefert seine "
        "Titel ueber ``media.browse``, gemessen an /api/browse/{media_type}."
    ),
    "GET /api/watchlist/plex": (
        "Braucht einen verbundenen Media-Server samt persoenlichem Token. Ihr "
        "Altersfilter ist ``test_watchlist.py`` und der Abfragen-Waage bekannt; "
        "eine Attrappe dafuer gehoert dorthin, nicht hierher."
    ),
    "GET /api/kids/rubrik/{rubrik}": (
        "Die Kinderansicht siebt nicht ueber ``_darf_sehen``, sondern ueber die "
        "Freigabe-Abfrage an TMDB (``kids.erlaubte_freigaben``) samt Nachfilter. "
        "Das ist ein eigener Mechanismus mit eigenen Tests in "
        "``test_kids_filter``; ihn hier mitzumessen hiesse, zwei verschiedene "
        "Zusagen in einem Waechter zu vermischen."
    ),
    "GET /api/kids/search": "Dieselbe Ueberlegung wie bei der Rubrik darueber.",
    "GET /api/kids/title/{media_type}/{tmdb_id}": (
        "Dieselbe Ueberlegung wie bei der Rubrik darueber."
    ),
    "GET /api/children/{child_id}/preview/rubrik/{rubrik}": (
        "Die Elternvorschau auf die Kinderansicht - derselbe Mechanismus wie "
        "/api/kids/rubrik darueber."
    ),
    "GET /api/children/{child_id}/preview/search": (
        "Dieselbe Ueberlegung wie bei der Vorschau darueber."
    ),
    "GET /api/children/{child_id}/preview/title/{media_type}/{tmdb_id}": (
        "Dieselbe Ueberlegung wie bei der Vorschau darueber."
    ),
}

#: Adressen, die Titel sieben, ohne ein Titelmodell zu tragen.
#:
#: ⚠️ **Hier steht der Fall, der den ganzen Waechter ausgeloest hat.** Die
#: Favoritenliste antwortet mit ``FavoriteOut`` - Kennung, Art, Zeitpunkt -,
#: und damit faellt sie durch das abgeleitete Kriterium oben. Gesiebt wird
#: dort trotzdem, und genau dort war das Sieb ausgebaut worden, ohne dass ein
#: Test etwas sagte.
#:
#: Die Lehre daraus steht in einem Satz: **Was Kennungen aus dem Katalog
#: ausliefert, muss sieben - nicht nur, was sie huebsch verpackt.** Wer eine
#: solche Adresse baut, traegt sie hier ein.
#:
#: Der Wert sagt, wie die Adresse vorher gefuellt wird.
WEITERE_PROBEN: dict[str, str] = {
    "GET /api/favorites": "vormerken",
}


def _favoriten_anlegen(client: TestClient, kopf: dict[str, str]) -> None:
    """Beide Titel vormerken - den erlaubten und den gesperrten."""
    for tmdb_id in (ERLAUBT, GESPERRT):
        antwort = client.post(
            "/api/favorites",
            json={"media_type": "movie", "tmdb_id": tmdb_id},
            headers=kopf,
        )
        assert antwort.status_code in (200, 201), antwort.text


VORBEREITUNG = {"vormerken": _favoriten_anlegen}


#: So viele Adressen werden wirklich angerufen.
#:
#: ⚠️ **Ohne diese Schwelle koennte ``PROBEN`` leer laufen** - etwa weil jemand
#: die Namen der Routen aendert - und der Waechter waere still gruen, ohne je
#: eine Anfrage gestellt zu haben.
MINDESTENS_PROBEN = 9

#: Und so viele Titeladressen muss die Routentabelle hergeben.
MINDESTENS_TITELADRESSEN = 18


@pytest.fixture()
def beschraenkt(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict[str, str]]:
    """Ein Konto mit Altersgrenze 12, und TMDB durch die Attrappe ersetzt.

    ⚠️ **Mit gesetztem Schluessel, nicht im Beispielbetrieb.** Ohne Schluessel
    kehren ``discover`` und die Nachbarn zurueck, bevor ein Filter laeuft; der
    Waechter pruefte dann einen Weg, den im Betrieb niemand geht.
    """
    monkeypatch.setattr(media, "TmdbClient", _FakeTmdb)
    monkeypatch.setattr(media, "_client", lambda settings, region=None: _FakeTmdb())
    assert admin_client.put(
        "/api/settings", json={"tmdb_api_key": "test-key", "default_region": "DE"}
    ).status_code == 200

    create_user(admin_client, "kind12", "kind-passwort-12", age=12)
    kopf = auth_headers(admin_client, "kind12", "kind-passwort-12")
    return admin_client, kopf


def test_jede_titeladresse_ist_entschieden() -> None:
    """Zu jeder Adresse, die Titel ausliefert, gibt es eine Entscheidung."""
    adressen = titeladressen()
    offen = adressen - set(PROBEN) - set(OHNE_PROBE)
    assert not offen, (
        "Diese Adressen liefern Titel aus, ohne dass jemand entschieden hat, ob sie "
        "hier gemessen werden: " + ", ".join(sorted(offen)) + ". Trag sie in PROBEN "
        "ein - oder mit ausgeschriebenem Grund in OHNE_PROBE."
    )
    assert len(adressen) >= MINDESTENS_TITELADRESSEN, (
        f"Nur {len(adressen)} Adressen mit Titelmodell gefunden, erwartet mindestens "
        f"{MINDESTENS_TITELADRESSEN}. Der Wächter sieht offenbar nichts mehr."
    )


def test_keine_verwaisten_eintraege() -> None:
    """Kein Eintrag fuer eine Adresse, die es nicht mehr gibt."""
    verwaist = (set(PROBEN) | set(OHNE_PROBE)) - titeladressen()
    assert not verwaist, f"Einträge ohne Adresse: {sorted(verwaist)}"


def _kennungen(daten: object, gefunden: set[int] | None = None) -> set[int]:
    """Alle ``tmdb_id`` irgendwo in der Antwort, beliebig tief."""
    gefunden = set() if gefunden is None else gefunden
    if isinstance(daten, dict):
        wert = daten.get("tmdb_id")
        if isinstance(wert, int):
            gefunden.add(wert)
        for teil in daten.values():
            _kennungen(teil, gefunden)
    elif isinstance(daten, list):
        for teil in daten:
            _kennungen(teil, gefunden)
    return gefunden


def test_kein_gesperrter_titel_kommt_durch(
    beschraenkt: tuple[TestClient, dict[str, str]],
) -> None:
    """Der eigentliche Waechter, an jeder angerufenen Adresse.

    ⚠️ **Zwei Zusicherungen je Adresse, und die zweite traegt den Test.** Eine
    Adresse, die mit 500 antwortet oder eine leere Liste liefert, enthaelt den
    gesperrten Titel auch nicht - sie haette die halbe Probe bestanden, ohne je
    etwas ausgeliefert zu haben. Deshalb muss der erlaubte Titel da sein.
    """
    client, kopf = beschraenkt
    geprueft = 0
    beschwerden: list[str] = []

    for name, schritt in sorted(WEITERE_PROBEN.items()):
        VORBEREITUNG[schritt](client, kopf)
        pfad = name.split(" ", 1)[1]
        antwort = client.get(pfad, headers=kopf)
        if antwort.status_code != 200:
            beschwerden.append(f"{name}: {antwort.status_code} statt 200")
            continue
        kennungen = _kennungen(antwort.json())
        if GESPERRT in kennungen:
            beschwerden.append(f"{name}: liefert den gesperrten Titel {GESPERRT} aus")
        if ERLAUBT not in kennungen:
            beschwerden.append(
                f"{name}: liefert den erlaubten Titel {ERLAUBT} nicht - die Probe "
                "hätte nichts gemessen"
            )
        geprueft += 1

    for name, aufruf in sorted(PROBEN.items()):
        antwort = client.get(aufruf, headers=kopf)
        # Einzeltitel-Adressen enden auf der Kennung; eine Unterseite wie
        # ".../recommendations" ist eine Liste und wird wie eine geprueft.
        einzeltitel = aufruf.rstrip("/").endswith(str(GESPERRT))
        if einzeltitel:
            # Ein gesperrter Einzeltitel muss sich wie "gibt es nicht"
            # verhalten: gleicher Code, gleiche Meldung. Sonst liesse sich die
            # Sperrliste Stueck fuer Stueck zusammensuchen.
            if antwort.status_code != 404:
                beschwerden.append(
                    f"{name}: gesperrter Titel liefert {antwort.status_code} statt 404"
                )
            geprueft += 1
            continue
        if antwort.status_code != 200:
            beschwerden.append(f"{name}: {antwort.status_code} statt 200")
            continue
        kennungen = _kennungen(antwort.json())
        if GESPERRT in kennungen:
            beschwerden.append(f"{name}: liefert den gesperrten Titel {GESPERRT} aus")
        if ERLAUBT not in kennungen:
            beschwerden.append(
                f"{name}: liefert den erlaubten Titel {ERLAUBT} nicht - die Probe "
                "hätte nichts gemessen"
            )
        geprueft += 1

    assert not beschwerden, "\n".join(beschwerden)
    assert geprueft >= MINDESTENS_PROBEN, (
        f"Nur {geprueft} Adressen wirklich angerufen, erwartet mindestens "
        f"{MINDESTENS_PROBEN}. Der Wächter läuft offenbar leer."
    )


def test_ohne_altersgrenze_kommen_beide_titel(
    beschraenkt: tuple[TestClient, dict[str, str]],
) -> None:
    """Die Gegenprobe: Ohne Grenze darf nichts gesiebt werden.

    ⚠️ **Ohne sie waere der Test darueber nicht zu unterscheiden von "die
    Attrappe liefert den gesperrten Titel gar nicht".** Hier holt derselbe Weg
    beide Titel - der Unterschied kommt also wirklich von der Altersgrenze.
    """
    client, _ = beschraenkt
    kennungen = _kennungen(client.get("/api/discover/movie").json())
    assert {ERLAUBT, GESPERRT} <= kennungen, (
        "Ein Konto ohne Altersgrenze sieht nicht beide Titel - dann misst der "
        f"Wächter darüber nichts. Gefunden: {sorted(kennungen)}"
    )
