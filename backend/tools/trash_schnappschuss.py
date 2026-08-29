"""Erzeugt den TRaSH-Schnappschuss, den Nexview mitliefert.

Die Guides liegen als **630 Einzeldateien** im Repo von TRaSH. So viele Dateien
mitzuschleppen macht jedes Verzeichnis unuebersichtlich, ohne dass irgendjemand
sie einzeln liest - Nexview braucht sie nur gebuendelt. Dieses Skript macht
daraus je eine Datei pro Dienst.

Aufruf::

    git clone --depth 1 --filter=blob:none --sparse https://github.com/TRaSH-Guides/Guides
    cd Guides && git sparse-checkout set docs/json
    python trash_schnappschuss.py <pfad-zum-clone>

⚠️ **Der Schnappschuss wird nicht von Hand bearbeitet.** Wer etwas aendern will,
aendert die Zuordnung in ``services/trash.py`` - der Schnappschuss bleibt eine
unveraenderte Kopie der Quelle, sonst laesst sich nicht mehr sagen, welcher
Stand eigentlich drin ist.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ZIEL = Path(__file__).resolve().parent.parent / "app" / "daten"
DIENSTE = ("radarr", "sonarr")


def _lesen(ordner: Path) -> dict[str, dict]:
    """Alle JSON-Dateien eines Ordners, benannt nach ihrem Dateinamen."""
    if not ordner.is_dir():
        return {}
    return {
        pfad.stem: json.loads(pfad.read_text(encoding="utf-8"))
        for pfad in sorted(ordner.glob("*.json"))
    }


def _stand_der_guides() -> tuple[str, str]:
    """Welcher Stand von ``docs/json`` das ist - Kennung und Datum.

    ⚠️ **Nicht das Erzeugungsdatum nehmen.** Wann *ich* den Schnappschuss gebaut
    habe, sagt nichts darueber, wie alt die Daten sind. Nur die Commit-Kennung
    beantwortet spaeter die Frage "gibt es inzwischen Neues?".
    """
    anfrage = urllib.request.Request(
        "https://api.github.com/repos/TRaSH-Guides/Guides/commits"
        "?path=docs/json&per_page=1",
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(anfrage, timeout=20) as antwort:
        eintrag = json.load(antwort)[0]
    return eintrag["sha"], eintrag["commit"]["committer"]["date"]


def bauen(quelle: Path) -> None:
    wurzel = quelle / "docs" / "json"
    if not wurzel.is_dir():
        raise SystemExit(f"Kein docs/json unter {quelle} - falscher Pfad?")

    sha, datum = _stand_der_guides()
    print(f"Stand der Guides: {sha[:12]} vom {datum}")

    ZIEL.mkdir(parents=True, exist_ok=True)
    for dienst in DIENSTE:
        basis = wurzel / dienst
        formate = _lesen(basis / "cf")
        schnappschuss = {
            "stand": datum[:10],
            "commit": sha,
            "commit_datum": datum,
            "quelle": "https://github.com/TRaSH-Guides/Guides",
            "lizenz": "MIT",
            # Nach trash_id abgelegt: So findet die Uebersetzung ein Format,
            # ohne den Dateinamen kennen zu muessen - Profile verweisen ueber
            # die Kennung, nie ueber den Namen.
            "formate": {f["trash_id"]: f for f in formate.values()},
            "formate_nach_datei": {name: f["trash_id"] for name, f in formate.items()},
            "profile": _lesen(basis / "quality-profiles"),
            "gruppen": _lesen(basis / "cf-groups"),
            "groessen": _lesen(basis / "quality-size"),
            # Die Namensschemata - je Medienserver eine Fassung, weil Plex,
            # Emby und Jellyfin Ordnernamen unterschiedlich lesen.
            "namen": _lesen(basis / "naming"),
        }
        ziel = ZIEL / f"trash-{dienst}.json"
        ziel.write_text(
            json.dumps(schnappschuss, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        print(
            f"{ziel.name}: {len(schnappschuss['formate'])} Formate, "
            f"{len(schnappschuss['profile'])} Profile, "
            f"{len(schnappschuss['gruppen'])} Gruppen, "
            f"{ziel.stat().st_size / 1024:.0f} KB"
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    bauen(Path(sys.argv[1]).resolve())
