"""Der Vertrag mit nexcrate - festgehalten, damit er nicht still bricht.

Drei Wächter, jeder gegen einen anderen Weg, auf dem die Anbindung kaputtgehen
kann, ohne dass ein Test etwas merkt:

1. **Jede Adresse, die Nexview ruft, gibt es in nexcrates OpenAPI.**
   ``nexcrate_openapi.json`` ist der Abzug von nexcrate ``39dfc05``
   (0.2.0, Vertrag ``major 1``, Stand ``V5``, 24.09.2026). Benennt nexcrate
   eine Adresse um, wird dieser Lauf rot - und nicht erst der Durchlauf.
2. **Die Attrappe antwortet in denselben Formen wie die echte Instanz.**
   Die Felder unten stehen so im Messprotokoll
   (``homelab/nexcrate-pruefstand/nexview-anbindung/messung-nexview.md``)
   und in nexcrates Modellen in ``39dfc05`` (``routers/v1.py``:
   ``TitleOut``, ``TitleVersionOut``, ``SeasonOut``, ``SeasonVersionOut``,
   ``SeriesBlock``; ``routers/v1_round.py``: ``RatingItemOut``,
   ``RatingsOut``, ``RatingOut``). Wo der Abzug eine Antwort mit Feldern
   beschreibt, prüft Regel 2 die Attrappe zusätzlich gegen ihn.
3. **Jeder Zustand, den nexcrate führt, hat eine Entsprechung.** Ein neuer
   Zustand darf nicht stillschweigend als „unbekannt" durchrutschen.

⚠️ **Die OpenAPI allein genügt nicht.** Sie beschreibt einen Titel als
``object`` - über ``ref``, ``versions``, ``size_bytes`` oder ``imported_at``
sagt sie nichts, denn ``TitleOut`` schreibt sich über einen eigenen
Serialisierer. Deshalb nennt Regel 2 die Felder der Titel ausdrücklich.

**Den Abzug erneuern** (so am 24.09.2026): den Stand von nexcrate mit
``git archive <commit>`` in ein Wegwerfverzeichnis auspacken, nie den
Arbeitsbaum nehmen. ``NEXCRATE_DATA_DIR`` (und ``NEXCRATE_FRONTEND_DIR``) auf
ein leeres Wegwerfverzeichnis setzen, bevor irgendetwas importiert wird, dann
ohne Server ``app.main.app.openapi()`` aufrufen. Zuschneiden: nur die Pfade
unter ``/api/v1``, aus ``components`` nur die Schemata, die diese Pfade
erreichen (samt allem, was sie ihrerseits nennen); ``info`` bekommt Stand und
Weg in ``x-gemessen``. Geschrieben mit ``json.dumps(..., indent=1,
sort_keys=True, ensure_ascii=False)``, damit der Diff nur echte Änderungen
zeigt. Der Import legt höchstens das Datenverzeichnis an; nachgesehen, dass
danach nichts darin lag.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.beschaffung.base import ZUSTAENDE
from app.services.beschaffung.nex import mapping

from .beschaffung.fake_nexcrate import (
    FILM_HD,
    KEY,
    SERIE_HD,
    STAFFELN_FEHLT,
    STAFFELN_NULL,
    URL,
    FakeNexcrate,
)
from .beschaffung.fake_nexcrate import ZUSTAENDE as NEXCRATE_ZUSTAENDE

ABZUG = Path(__file__).parent / "beschaffung" / "nexcrate_openapi.json"

#: Was der Client ruft: Methode und Pfad, Platzhalter wie in der OpenAPI.
#: Ergänzt wird hier, sobald der Client eine Adresse dazubekommt.
GERUFEN: tuple[tuple[str, str], ...] = (
    ("get", "/api/v1/system"),
    ("get", "/api/v1/versions"),
    ("get", "/api/v1/states"),
    ("get", "/api/v1/titles"),
    ("post", "/api/v1/titles/lookup"),
    ("get", "/api/v1/titles/{kind}/{ref}"),
    ("get", "/api/v1/titles/series/{ref}/seasons/{season}"),
    ("post", "/api/v1/titles/why"),
    ("get", "/api/v1/titles/{kind}/{ref}/history"),
    ("post", "/api/v1/titles/{kind}/{ref}/search"),
    ("post", "/api/v1/titles/{kind}/{ref}/withdraw"),
    ("put", "/api/v1/titles/{kind}/{ref}/monitoring"),
    ("get", "/api/v1/calendar"),
    ("post", "/api/v1/ratings"),
    ("get", "/api/v1/ratings/{kind}/{ref}"),
    ("get", "/api/v1/storage"),
    ("get", "/api/v1/health"),
    ("get", "/api/v1/queue"),
    ("get", "/api/v1/problems"),
    ("get", "/api/v1/downloads/{download_id}/files"),
    ("post", "/api/v1/downloads/{download_id}/retry"),
    ("post", "/api/v1/downloads/{download_id}/remove"),
    ("post", "/api/v1/downloads/{download_id}/clear"),
    ("post", "/api/v1/downloads/{download_id}/search"),
    ("post", "/api/v1/downloads/{download_id}/finish"),
    ("post", "/api/v1/downloads/{download_id}/confirm-mapping"),
    ("post", "/api/v1/downloads/{download_id}/assign"),
    ("get", "/api/v1/recycle-bin"),
    ("post", "/api/v1/recycle-bin/{entry_id}/restore"),
    ("post", "/api/v1/requests"),
    ("get", "/api/v1/events"),
    ("get", "/api/v1/events/stream"),
    ("post", "/api/v1/pairing"),
    ("get", "/api/v1/pairing/{pairing_id}"),
)

#: Unter so vielen Adressen ist der Abzug kaputt und ein grüner Lauf sagt nichts.
MINDESTENS_ADRESSEN = 35


def _abzug() -> dict:
    return json.loads(ABZUG.read_text(encoding="utf-8"))


def test_der_abzug_ist_vollstaendig_genug() -> None:
    """Die Bodenschwelle: ein halber Abzug bestünde jede Prüfung darunter."""
    pfade = _abzug()["paths"]
    assert len(pfade) >= MINDESTENS_ADRESSEN, len(pfade)
    assert all(pfad.startswith("/api/v1") for pfad in pfade)


@pytest.mark.parametrize(("methode", "pfad"), GERUFEN)
def test_jede_gerufene_adresse_gibt_es_in_nexcrate(methode: str, pfad: str) -> None:
    pfade = _abzug()["paths"]
    assert pfad in pfade, f"{pfad} steht nicht in nexcrates Vertrag"
    assert methode in pfade[pfad], f"{methode.upper()} {pfad} gibt es dort nicht"


def _client_quelle() -> str:
    return (
        Path(__file__).parent.parent
        / "app"
        / "services"
        / "beschaffung"
        / "nex"
        / "client.py"
    ).read_text(encoding="utf-8")


def _client_adressen() -> set[tuple[str, str]]:
    """Was ``client.py`` tatsächlich ruft, an der Form von ``_request(...)``."""
    gefunden = set()
    for methode, pfad in re.findall(
        r'_request\(\s*"([A-Z]+)",\s*f?"([^"]+)"', _client_quelle()
    ):
        gefunden.add((methode.lower(), "/api/v1" + re.sub(r"\{[^}]+\}", "{}", pfad)))
    # ⚠️ Der Ereignisstrom geht nicht ueber ``_request`` (der Aufruf haelt die
    # Verbindung offen), sondern direkt ueber ``client.stream("GET", ...)``.
    # Der Scan sieht das Muster nicht; die Adresse gibt es trotzdem wirklich.
    gefunden.add(("get", "/api/v1/events/stream"))
    return gefunden


def test_der_client_ruft_nichts_ausserhalb_der_liste() -> None:
    """Die eine Richtung: eine neue Adresse im Client ohne Eintrag hier.

    Sonst wüchse der Client, und der Wächter prüfte weiter die alte Liste.
    """
    bekannt = {(m, re.sub(r"\{[^}]+\}", "{}", p)) for m, p in GERUFEN}
    # Die sieben Download-Aktionen stehen einzeln in ``GERUFEN``, der Client
    # ruft sie aber ueber eine einzige, zur Laufzeit zusammengesetzte Adresse.
    bekannt.add(("post", "/api/v1/downloads/{}/{}"))
    assert _client_adressen() <= bekannt, sorted(_client_adressen() - bekannt)


def test_jeder_eintrag_in_gerufen_hat_eine_fundstelle_im_client() -> None:
    """Die Gegenrichtung: ein Eintrag in ``GERUFEN``, den der Client gar nicht
    mehr ruft, prüft eine Adresse, die es im Code nicht mehr gibt - so stand
    lange ``GET .../why`` hier, das der Client nie ruft (nur ``POST .../why``)."""
    gefunden = _client_adressen()
    generische_downloadaktion = ("post", "/api/v1/downloads/{}/{}") in gefunden

    def _gerufen(eintrag: tuple[str, str]) -> bool:
        if eintrag in gefunden:
            return True
        # Die sieben Download-Aktionen ruft der Client ueber eine einzige,
        # zusammengesetzte Adresse (``_client_adressen`` sieht davon nur die
        # generische Form) - siehe die Anmerkung dort.
        methode, pfad = eintrag
        if methode == "post" and re.fullmatch(r"/api/v1/downloads/\{\}/[a-z-]+", pfad):
            return generische_downloadaktion
        return False

    bekannt = {(m, re.sub(r"\{[^}]+\}", "{}", p)) for m, p in GERUFEN}
    fehlend = sorted(eintrag for eintrag in bekannt if not _gerufen(eintrag))
    assert not fehlend, fehlend


#: Client-Methoden, die bereitstehen, aber (noch) niemand ruft.
UNGERUFEN_ERLAUBT = frozenset({"states", "history", "problems"})


def test_jede_methode_des_clients_hat_einen_aufrufer() -> None:
    """Eine Adresse im Client, die der Weg nie ruft, ist ein halber Bau.

    So stand ``rating`` (``GET /ratings/{kind}/{ref}``) seit S4 im Client,
    und die Titelseite zeigte im NEX-Betrieb weder Rotten Tomatoes noch
    Metacritic - kein Wächter merkte es, weil die Adresse ja „gerufen" war.
    """
    quelle = _client_quelle()
    methoden = set(re.findall(r"^    async def ([a-z][a-z_]*)\(", quelle, re.MULTILINE))
    assert len(methoden) >= 25, methoden
    nex = Path(__file__).parent.parent / "app" / "services" / "beschaffung" / "nex"
    andere = "\n".join(
        datei.read_text(encoding="utf-8") for datei in nex.glob("*.py") if datei.name != "client.py"
    )
    ungerufen = sorted(m for m in methoden if not re.search(rf"\.{m}\(", andere))
    assert set(ungerufen) <= UNGERUFEN_ERLAUBT, ungerufen


def test_die_attrappe_antwortet_in_den_gemessenen_formen() -> None:
    """Regel 2: Was die Attrappe liefert, trägt die Felder der echten Instanz."""
    attrappe = FakeNexcrate()
    film = attrappe.film(603)
    assert set(film) >= {
        "kind",
        "ref",
        "refs",
        "name",
        "year",
        "poster_path",
        "origin",
        "monitored",
        "versions",
        "tags",
    }
    assert set(film["versions"][0]) == TITEL_FASSUNG
    serie = attrappe.serie(1399)
    assert set(serie["series"]) == {"type", "status", "next_air_date", "seasons"}
    assert set(serie["versions"][0]["series"]["counts"]) == {"have", "aired", "expected"}
    # Seit nexcrate 39dfc05 zeigen Liste und ``lookup`` dieselben Staffeln
    # wie die Einzelansicht; eine aeltere nexcrate schreibt dort ``null``.
    staffeln = [{"season": 1, "name": "Season 1", "versions": []}]
    mit = attrappe.serie(1400, staffeln=staffeln, tvdb=None)
    assert attrappe._ohne_seq(mit)["series"]["seasons"] == staffeln
    attrappe.liste_staffeln = STAFFELN_NULL
    assert attrappe._ohne_seq(mit)["series"]["seasons"] is None
    attrappe.liste_staffeln = STAFFELN_FEHLT
    assert "seasons" not in attrappe._ohne_seq(mit)["series"]


#: Die Felder aus nexcrates Modellen in ``39dfc05`` (``routers/v1.py``,
#: ``routers/v1_round.py``), wie pydantic sie schreibt: jedes Feld, auch als
#: ``null``. Nur die Bloecke einer anderen Art (``series``, ``album``,
#: ``artist``) laesst ``_KindBlocks`` weg, wenn sie leer sind.
TITEL = frozenset(
    {"kind", "ref", "refs", "name", "year", "poster_path", "origin", "monitored", "versions", "tags"}
)
TITEL_FASSUNG = frozenset(
    {"version_id", "state", "monitored", "size_bytes", "quality", "origin", "imported_at"}
)
SERIENBLOCK = frozenset({"type", "status", "next_air_date", "seasons"})
STAFFEL = frozenset({"season", "name", "air_date", "episodes", "aired", "versions"})
STAFFEL_FASSUNG = frozenset(
    {"version_id", "state", "monitored", "counts", "size_bytes", "imported_at"}
)
FOLGEN_FASSUNG = frozenset({"version_id", "state", "monitored", "size_bytes", "quality", "files"})
STAPEL = frozenset({"items", "imdb", "attribution", "omdb_attribution"})
STAPEL_EINTRAG = frozenset(
    {"kind", "ref", "imdb_ref", "imdb", "rotten_tomatoes", "metacritic", "sources", "error"}
)
EINZELWERTUNG = frozenset(
    {"kind", "ref", "imdb_ref", "imdb", "rotten_tomatoes", "metacritic", "sources", "attribution"}
)


def _befuellte_attrappe() -> FakeNexcrate:
    """Eine Attrappe mit je einem Film und einer Serie samt Staffel und Folge."""
    attrappe = FakeNexcrate()
    attrappe.film(603)
    staffel = attrappe.staffel_eintrag(
        1,
        [attrappe.staffel_fassung(SERIE_HD, "available", size_bytes=1_000, imported_at=None)],
        folgen=1,
        gesendet=1,
    )
    attrappe.serie(1399, staffeln=[staffel])
    attrappe.staffel(
        "tmdb:1399",
        1,
        [attrappe.folge(1, versionen=[attrappe.folgen_fassung(SERIE_HD, "available", size_bytes=1_000)])],
    )
    attrappe.wertung(603, imdb=8.7, stimmen=10, tomaten=88, metacritic=73)
    return attrappe


def _fragen(attrappe: FakeNexcrate, methode: str, pfad: str, koerper: Any = None) -> Any:
    """Eine Frage über den Transport - geprüft wird die Antwort, nicht das Innere."""
    with httpx.Client(
        transport=attrappe.transport(), base_url=URL, headers={"Authorization": f"Bearer {KEY}"}
    ) as client:
        antwort = client.request(methode, pfad, json=koerper)
    assert antwort.status_code < 400, (pfad, antwort.status_code, antwort.text)
    return antwort.json()


def _serie_aus(antwort: Any, weg: str) -> dict[str, Any]:
    if weg == "liste":
        return next(t for t in antwort["items"] if t["kind"] == "series")
    if weg == "lookup":
        return antwort["items"][0]["title"]
    return antwort


SERIENWEGE = {
    "liste": ("GET", "/api/v1/titles?kind=series&after=0", None),
    "lookup": ("POST", "/api/v1/titles/lookup", {"items": [{"kind": "series", "ref": "tmdb:1399"}]}),
    "einzelansicht": ("GET", "/api/v1/titles/series/tmdb:1399", None),
}


@pytest.mark.parametrize("weg", sorted(SERIENWEGE))
def test_liste_lookup_und_einzelansicht_tragen_nexcrates_felder(weg: str) -> None:
    """Regel 2 für die Titel: Liste, ``lookup`` und Einzelansicht, bis in die Staffel.

    Seit ``39dfc05`` nennen alle drei die Staffeln, und jede Fassung, auch
    die einer Staffel, trägt ``imported_at`` - als ``null``, wenn nexcrate es
    nicht weiß. Fehlt es in der Attrappe, bestehen Nexviews Tests gegen eine
    Form, die es nicht mehr gibt.
    """
    attrappe = _befuellte_attrappe()
    methode, pfad, koerper = SERIENWEGE[weg]
    serie = _serie_aus(_fragen(attrappe, methode, pfad, koerper), weg)

    assert set(serie) - {"seq"} == TITEL | {"series"}
    assert set(serie["versions"][0]) == TITEL_FASSUNG | {"series"}
    assert set(serie["series"]) == SERIENBLOCK
    staffel = serie["series"]["seasons"][0]
    assert set(staffel) == STAFFEL
    assert set(staffel["versions"][0]) == STAFFEL_FASSUNG
    assert staffel["versions"][0]["imported_at"] is None

    film = _fragen(attrappe, "GET", "/api/v1/titles/movie/tmdb:603")
    assert set(film) == TITEL
    assert set(film["versions"][0]) == TITEL_FASSUNG
    assert film["versions"][0]["imported_at"] is None


def test_folge_und_wertungen_tragen_nexcrates_felder() -> None:
    """Regel 2 für die Folgen einer Staffel und beide Wege der Wertungen."""
    attrappe = _befuellte_attrappe()

    staffel = _fragen(attrappe, "GET", "/api/v1/titles/series/tmdb:1399/seasons/1")
    assert set(staffel["episodes"][0]["versions"][0]) == FOLGEN_FASSUNG
    assert staffel["episodes"][0]["versions"][0]["files"] == []

    stapel = _fragen(attrappe, "POST", "/api/v1/ratings", {"items": [{"kind": "movie", "ref": "tmdb:603"}]})
    assert set(stapel) == STAPEL
    assert set(stapel["items"][0]) == STAPEL_EINTRAG
    assert set(stapel["items"][0]["sources"]) == {"imdb", "omdb"}

    einzeln = _fragen(attrappe, "GET", "/api/v1/ratings/movie/tmdb:603")
    assert set(einzeln) == EINZELWERTUNG


def _schema(eintrag: dict[str, Any]) -> dict[str, Any]:
    schemata = _abzug()["components"]["schemas"]
    while "$ref" in eintrag:
        eintrag = schemata[eintrag["$ref"].rsplit("/", 1)[1]]
    return eintrag


def _gegen_schema(wert: Any, schema: dict[str, Any], ort: str, funde: list[str]) -> int:
    """Jedes Objekt mit Feldern im Schema: genau diese Schlüssel. Zählt die Prüfungen."""
    schema = _schema(schema)
    if "anyOf" in schema:
        if wert is None:
            return 0
        nicht_null = [_schema(s) for s in schema["anyOf"] if _schema(s).get("type") != "null"]
        return _gegen_schema(wert, nicht_null[0], ort, funde) if nicht_null else 0
    if isinstance(wert, list) and "items" in schema:
        return sum(_gegen_schema(w, schema["items"], f"{ort}[]", funde) for w in wert)
    felder = schema.get("properties")
    if not isinstance(wert, dict) or felder is None:
        # ``object`` ohne Felder (die Titel, ``sources``): das deckt die Liste oben.
        return 0
    if set(wert) != set(felder):
        funde.append(f"{ort}: zu viel {sorted(set(wert) - set(felder))}, fehlt {sorted(set(felder) - set(wert))}")
    return 1 + sum(
        _gegen_schema(v, felder[k], f"{ort}.{k}", funde) for k, v in wert.items() if k in felder
    )


#: Was die Attrappe selbst baut, samt Methode und Pfad im Abzug. Warteschlange,
#: Probleme, Platz, Papierkorb und die Download-Aktionen fehlen: deren Einträge
#: schreibt der Test, geprüft würde also das Beispiel und nicht die Attrappe.
ANTWORTEN_DER_ATTRAPPE: tuple[tuple[str, str, str, Any], ...] = (
    ("get", "/api/v1/system", "/api/v1/system", None),
    ("get", "/api/v1/versions", "/api/v1/versions", None),
    ("get", "/api/v1/states", "/api/v1/states", None),
    ("get", "/api/v1/titles", "/api/v1/titles?kind=series&after=0", None),
    ("post", "/api/v1/titles/lookup", "/api/v1/titles/lookup", {"items": [{"kind": "series", "ref": "tmdb:1399"}]}),
    ("get", "/api/v1/titles/series/{ref}/seasons/{season}", "/api/v1/titles/series/tmdb:1399/seasons/1", None),
    ("post", "/api/v1/titles/why", "/api/v1/titles/why", {"items": [{"kind": "movie", "ref": "tmdb:603"}]}),
    ("post", "/api/v1/titles/{kind}/{ref}/search", "/api/v1/titles/movie/tmdb:603/search", {}),
    ("post", "/api/v1/titles/{kind}/{ref}/withdraw", "/api/v1/titles/movie/tmdb:603/withdraw", {}),
    (
        "put",
        "/api/v1/titles/{kind}/{ref}/monitoring",
        "/api/v1/titles/movie/tmdb:603/monitoring",
        {"monitored": False, "versions": [FILM_HD]},
    ),
    ("get", "/api/v1/calendar", "/api/v1/calendar?from=2026-01-01&to=2026-02-01", None),
    (
        "post",
        "/api/v1/ratings",
        "/api/v1/ratings",
        {"items": [{"kind": "movie", "ref": "tmdb:603"}, {"kind": "series", "ref": "tmdb:1399"}]},
    ),
    ("get", "/api/v1/ratings/{kind}/{ref}", "/api/v1/ratings/movie/tmdb:603", None),
    ("post", "/api/v1/requests", "/api/v1/requests", {"kind": "movie", "ref": "tmdb:603", "versions": [FILM_HD]}),
    ("get", "/api/v1/events", "/api/v1/events?after=0", None),
    ("post", "/api/v1/recycle-bin/{entry_id}/restore", "/api/v1/recycle-bin/1/restore", {}),
    ("post", "/api/v1/pairing", "/api/v1/pairing", {"app": "nexview", "scopes": ["read"]}),
)

#: Unter so vielen geprüften Objekten sagt ein grüner Lauf nichts.
MINDESTENS_OBJEKTE = 25


def test_was_der_abzug_beschreibt_traegt_die_attrappe_genau_so() -> None:
    """Regel 2 gegen das Schema: Wo der Abzug Felder nennt, nennt die Attrappe genau diese.

    So fiel beim Erneuern auf, dass die Attrappe an Folgen ``origin`` statt
    ``files`` schrieb und ``POST /pairing`` mit ``200`` statt ``201`` beantwortete.
    """
    attrappe = _befuellte_attrappe()
    attrappe.ereignis("title.added")
    attrappe.recycle = [{"entry_id": 1, "kind": "movie", "ref": "tmdb:603"}]
    pfade = _abzug()["paths"]
    funde: list[str] = []
    geprueft = 0
    with httpx.Client(
        transport=attrappe.transport(), base_url=URL, headers={"Authorization": f"Bearer {KEY}"}
    ) as client:
        for methode, schluessel, pfad, koerper in ANTWORTEN_DER_ATTRAPPE:
            antwort = client.request(methode.upper(), pfad, json=koerper)
            erfolg = {k: v for k, v in pfade[schluessel][methode]["responses"].items() if k.startswith("2")}
            if str(antwort.status_code) not in erfolg:
                funde.append(f"{methode.upper()} {schluessel}: {antwort.status_code}, nexcrate {sorted(erfolg)}")
                continue
            schema = erfolg[str(antwort.status_code)]["content"]["application/json"]["schema"]
            geprueft += _gegen_schema(antwort.json(), schema, f"{methode.upper()} {schluessel}", funde)
        # Das Koppeln ohne Schlüssel, mit dem Geheimnis aus der Bitte.
        client.headers.pop("Authorization")
        bitte = next(iter(attrappe.pairings))
        attrappe.bestaetigen(bitte)
        client.headers["X-Pairing-Secret"] = attrappe.pairings[bitte]["secret"]
        stand = client.get(f"/api/v1/pairing/{bitte}")
        schema = pfade["/api/v1/pairing/{pairing_id}"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        geprueft += _gegen_schema(stand.json(), schema, "GET /api/v1/pairing/{pairing_id}", funde)

    assert not funde, funde
    assert geprueft >= MINDESTENS_OBJEKTE, geprueft


def test_jeder_zustand_von_nexcrate_hat_eine_entsprechung() -> None:
    """Ein neuer Zustand darf nicht still als „unbekannt" durchrutschen.

    ``incomplete`` gibt es nur bei Alben; Nexview kennt dafür nichts und zeigt
    „unbekannt" - das ist ehrlich, solange Musik nicht gebaut ist.
    """
    for zustand, _ in NEXCRATE_ZUSTAENDE:
        gefunden = mapping.zustand(zustand)
        assert gefunden in ZUSTAENDE
        if zustand != "incomplete":
            assert gefunden != "unbekannt", zustand


def test_die_fassungen_der_attrappe_sind_die_gemessenen() -> None:
    eintrag = FakeNexcrate().versions[0]
    assert set(eintrag) == {"version_id", "kind", "name", "order", "tier", "ready", "reasons"}
    assert eintrag["version_id"].startswith("v_")
