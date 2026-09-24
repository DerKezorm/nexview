"""Der Vertrag mit nexcrate - festgehalten, damit er nicht still bricht.

Drei Wächter, jeder gegen einen anderen Weg, auf dem die Anbindung kaputtgehen
kann, ohne dass ein Test etwas merkt:

1. **Jede Adresse, die Nexview ruft, gibt es in nexcrates OpenAPI.**
   ``nexcrate_openapi.json`` ist der Abzug einer echten Wegwerf-nexcrate
   (0.1.0, Vertrag ``major 1``, Stand ``V5``, 22.09.2026). Benennt nexcrate
   eine Adresse um, wird dieser Lauf rot - und nicht erst der Durchlauf.
2. **Die Attrappe antwortet in denselben Formen wie die echte Instanz.**
   Die Felder unten stehen so im Messprotokoll
   (``homelab/nexcrate-pruefstand/nexview-anbindung/messung-nexview.md``).
3. **Jeder Zustand, den nexcrate führt, hat eine Entsprechung.** Ein neuer
   Zustand darf nicht stillschweigend als „unbekannt" durchrutschen.

⚠️ **Die OpenAPI allein genügt nicht.** Sie beschreibt einen Titel als
``object`` - über ``ref``, ``versions`` oder ``size_bytes`` sagt sie nichts.
Deshalb prüft Regel 2 die gemessenen Antworten und nicht das Schema.

**Den Abzug erneuern** (dokumentierter Handgriff): eine Wegwerf-nexcrate auf
einem Port starten, ``GET /api/openapi.json`` holen, auf die Pfade unter
``/api/v1`` samt ihrer Formen zuschneiden und hierher legen. Das Protokoll der
Messung gehört in dieselbe Datei wie oben.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.beschaffung.base import ZUSTAENDE
from app.services.beschaffung.nex import mapping

from .beschaffung.fake_nexcrate import ZUSTAENDE as NEXCRATE_ZUSTAENDE
from .beschaffung.fake_nexcrate import FakeNexcrate

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
    assert set(film["versions"][0]) == {
        "version_id",
        "state",
        "monitored",
        "size_bytes",
        "quality",
        "origin",
    }
    serie = attrappe.serie(1399)
    assert set(serie["series"]) == {"type", "status", "next_air_date", "seasons"}
    assert set(serie["versions"][0]["series"]["counts"]) == {"have", "aired", "expected"}
    # ⚠️ In der Liste stehen die Staffeln nicht - nur in der Einzelansicht.
    assert serie["series"]["seasons"] is None


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
