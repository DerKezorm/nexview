"""Die Waechter ueber `/api/app/1`, die Flaeche der eigenen iPhone-App.

Dasselbe Verfahren wie ``test_v1_zusage.py`` und aus demselben Grund: Die App
im App Store und die Server da draussen werden nie gleichzeitig aktualisiert.
Eine Umbenennung, die im Browser niemand bemerkt, legt dort jede App still, die
schon unterwegs ist.

Passt eine Aenderung wirklich, wird der Abdruck neu erzeugt:
``pytest tests/test_app_zusage.py --abdruck-neu``. Ein Feld hinzuzufuegen ist
erlaubt; eines wegzunehmen braucht ``/api/app/2`` daneben.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app
from app.routers.app1 import ZUGESAGT

from .test_v1_zusage import _felder_je_pfad

ABDRUCK = Path(__file__).with_name("app1_abdruck.json")
PRAEFIX = "/api/app/1"


def _pfade() -> set[str]:
    return {r.path for r in app.routes if getattr(r, "path", "").startswith(PRAEFIX)}


def test_die_flaeche_ist_genau_die_zugesagte() -> None:
    da = _pfade()
    zugesagt = set(ZUGESAGT)
    assert da - zugesagt == set(), (
        f"Unter {PRAEFIX} liegt etwas, das nicht in ZUGESAGT steht: {sorted(da - zugesagt)}."
    )
    assert zugesagt - da == set(), f"Zugesagt, aber nicht erreichbar: {sorted(zugesagt - da)}."


def test_jede_zugesagte_adresse_hat_eine_antwortform() -> None:
    """Ohne Antwortform gaebe es nichts festzuhalten, und der Abdruck waere leer.

    Ausgenommen sind Adressen, die nur einen Statuscode zurueckgeben (204).
    """
    felder = _felder_je_pfad(PRAEFIX)
    ohne = sorted(k for k, v in felder.items() if not v)
    erlaubt_leer = {
        "POST /api/app/1/house-rules/decision",
        "POST /api/app/1/wishes/{wish_id}/decline",
        "GET /api/app/1/approvals/count",
    }
    assert set(ohne) <= erlaubt_leer, f"Ohne festgehaltene Felder: {sorted(set(ohne) - erlaubt_leer)}"


def test_die_antwortform_bleibt(request) -> None:
    jetzt = _felder_je_pfad(PRAEFIX)

    if request.config.getoption("--abdruck-neu") or not ABDRUCK.exists():
        ABDRUCK.write_text(
            json.dumps(jetzt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not request.config.getoption("--abdruck-neu"):
            return

    frueher: dict[str, list[str]] = json.loads(ABDRUCK.read_text(encoding="utf-8"))
    verschwunden: dict[str, list[str]] = {}
    for schluessel, felder in frueher.items():
        if schluessel not in jetzt:
            verschwunden[schluessel] = ["(der ganze Endpunkt)"]
            continue
        fehlend = [f for f in felder if f not in jetzt[schluessel]]
        if fehlend:
            verschwunden[schluessel] = fehlend

    assert not verschwunden, (
        "Aus einer Antwort der App-Flaeche sind Felder verschwunden:\n"
        + "\n".join(f"  {k}: {', '.join(v)}" for k, v in sorted(verschwunden.items()))
        + "\n\nUnter /api/app/1 gilt eine Zusage an die App. Entweder das Feld bleibt, "
        "oder es braucht /api/app/2 daneben."
    )


def test_der_abdruck_misst_wirklich_etwas() -> None:
    """⚠️ Ein leerer Abdruck waere still gruen. Er muss die Kernfelder tragen."""
    frueher = json.loads(ABDRUCK.read_text(encoding="utf-8"))
    assert "konto.entscheidet" in frueher["GET /api/app/1/me"]
    assert "key" in frueher["POST /api/app/1/pairing/{pairing_id}/key"]
    assert len(frueher) >= len(ZUGESAGT)
