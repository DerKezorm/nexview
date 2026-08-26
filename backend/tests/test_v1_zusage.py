"""Die Wächter über `/api/v1`.

⚠️ **Ein Versprechen ohne Test ist ein Vorsatz.** Genau das ist der Punkt
dieser Datei: Unter ``/api/v1`` steht eine Zusage, und Zusagen bricht man nicht
absichtlich, sondern aus Versehen - beim Aufraeumen, beim Umbenennen, beim
"das heisst doch besser so".

Heute ist mir genau das passiert: In der Kontoaufloesung wurde aus
``storniert: list[str]`` ein ``offen: list[OffeneZeile]``, weil der neue Name
besser passt und die Liste mehr tragen musste. Richtig so - und unter einer
Zusage waere es ein Bruch gewesen, den niemand bemerkt haette.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app
from app.routers.v1 import ZUGESAGT

#: Die festgehaltene Form der Antworten. Liegt als Datei daneben, damit eine
#: Aenderung im Diff auftaucht statt in einer Fehlermeldung.
ABDRUCK = Path(__file__).with_name("v1_abdruck.json")


def _v1_pfade() -> set[str]:
    return {r.path for r in app.routes if getattr(r, "path", "").startswith("/api/v1")}


def test_die_flaeche_ist_genau_die_zugesagte() -> None:
    """⚠️ Nichts rutscht versehentlich hinein - und nichts heraus.

    Beides waere schlecht. Ein Pfad zu viel ist ein Versprechen, das niemand
    geben wollte; einer zu wenig ist ein gebrochenes.
    """
    da = _v1_pfade()
    zugesagt = set(ZUGESAGT)

    assert da - zugesagt == set(), (
        f"Unter /api/v1 liegt etwas, das nicht in ZUGESAGT steht: {sorted(da - zugesagt)}. "
        "Entweder gehoert es dort nicht hin - oder es ist eine bewusste Zusage und muss "
        "in die Liste in routers/v1.py."
    )
    assert zugesagt - da == set(), (
        f"Zugesagt, aber nicht erreichbar: {sorted(zugesagt - da)}."
    )


def _felder_je_pfad() -> dict[str, list[str]]:
    """Aus dem OpenAPI-Dokument die Antwortfelder der v1-Pfade ziehen."""
    dokument = app.openapi()
    schemas = dokument.get("components", {}).get("schemas", {})

    def felder(schema: dict) -> list[str]:
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            return sorted(schemas.get(name, {}).get("properties", {}))
        if schema.get("type") == "array":
            return felder(schema.get("items", {}))
        return sorted(schema.get("properties", {}))

    ergebnis: dict[str, list[str]] = {}
    for pfad, operationen in dokument.get("paths", {}).items():
        if not pfad.startswith("/api/v1"):
            continue
        for methode, operation in operationen.items():
            antwort = (
                operation.get("responses", {})
                .get("200", operation.get("responses", {}).get("201", {}))
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            ergebnis[f"{methode.upper()} {pfad}"] = felder(antwort)
    return ergebnis


def test_die_antwortform_bleibt(request) -> None:
    """⚠️ **Der eigentliche Waechter.**

    Er haelt fest, welche Felder die zugesagten Antworten haben. Verschwindet
    eines oder wird es umbenannt, schlaegt er an - und zwar **bevor** es
    jemandem draussen auffaellt.

    Ein Feld **hinzuzufuegen** ist erlaubt und bricht nichts; wer nur die
    Felder liest, die er kennt, merkt davon nichts. Deshalb wird geprueft, dass
    nichts **fehlt**, nicht dass alles gleich ist.

    Passt die Aenderung wirklich, wird der Abdruck neu erzeugt:
    ``pytest tests/test_v1_zusage.py --abdruck-neu``. Das ist Absicht ein
    eigener Handgriff - so bleibt es eine Entscheidung.
    """
    jetzt = _felder_je_pfad()

    if request.config.getoption("--abdruck-neu") or not ABDRUCK.exists():
        ABDRUCK.write_text(
            json.dumps(jetzt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not request.config.getoption("--abdruck-neu"):
            # Beim allerersten Lauf gibt es noch nichts zu vergleichen.
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
        "Aus einer zugesagten Antwort sind Felder verschwunden:\n"
        + "\n".join(f"  {k}: {', '.join(v)}" for k, v in sorted(verschwunden.items()))
        + "\n\nUnter /api/v1 gilt eine Zusage. Entweder das Feld bleibt (auch wenn es "
        "innen anders heisst), oder es braucht ein /api/v2 daneben. Ist die Aenderung "
        "wirklich in Ordnung, den Abdruck mit --abdruck-neu erneuern."
    )
