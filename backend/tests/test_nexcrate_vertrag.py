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
    ("get", "/api/v1/titles/{kind}/{ref}/why"),
    ("get", "/api/v1/titles/{kind}/{ref}/history"),
    ("post", "/api/v1/titles/{kind}/{ref}/search"),
    ("post", "/api/v1/titles/{kind}/{ref}/withdraw"),
    ("put", "/api/v1/titles/{kind}/{ref}/monitoring"),
    ("post", "/api/v1/titles/{kind}/{ref}/delete-files"),
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


def test_der_client_ruft_nichts_ausserhalb_der_liste() -> None:
    """Die andere Richtung: eine neue Adresse im Client ohne Eintrag hier.

    Sonst wüchse der Client, und der Wächter prüfte weiter die alte Liste.
    """
    quelle = (
        Path(__file__).parent.parent
        / "app"
        / "services"
        / "beschaffung"
        / "nex"
        / "client.py"
    ).read_text(encoding="utf-8")
    # ``self._request("GET", "/system")`` und die f-Strings daneben.
    gefunden = set()
    for methode, pfad in re.findall(
        r'_request\(\s*"([A-Z]+)",\s*f?"([^"]+)"', quelle
    ):
        gefunden.add((methode.lower(), "/api/v1" + re.sub(r"\{[^}]+\}", "{}", pfad)))
    bekannt = {(m, re.sub(r"\{[^}]+\}", "{}", p)) for m, p in GERUFEN}
    # ⚠️ Eine Adresse baut der Client zur Laufzeit zusammen: die Aktion an
    # einem Download steht in ``actions`` und ist keine Konstante. Der Scan
    # sieht deshalb nur die Form; die sieben Aktionen selbst stehen einzeln
    # in ``GERUFEN`` und werden oben gegen den Abzug geprueft.
    bekannt.add(("post", "/api/v1/downloads/{}/{}"))
    assert gefunden <= bekannt, sorted(gefunden - bekannt)


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
