"""Dateien aus ``frontend/public`` werden vor dem Benutzen nachgefragt.

Sie tragen keinen Hash im Namen. Ohne ``Cache-Control`` schaetzt der Browser
selbst, wie lange er sie behaelt - und nach dem neuen Symbol in 1.11.2 nahm
Chrome beim Neuinstallieren der Web-App das alte aus dem eigenen Speicher.
"""

from __future__ import annotations

from pathlib import Path

from app.main import _STETS_NACHFRAGEN

PUBLIC = Path(__file__).resolve().parents[2] / "frontend" / "public"


def test_jede_datei_aus_public_wird_nachgefragt() -> None:
    dateien = {p.name for p in PUBLIC.iterdir() if p.is_file()}

    assert dateien, PUBLIC
    assert dateien <= _STETS_NACHFRAGEN, sorted(dateien - _STETS_NACHFRAGEN)


def test_die_symbole_des_manifests_werden_nachgefragt() -> None:
    import json

    manifest = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    symbole = {Path(icon["src"]).name for icon in manifest["icons"]}

    assert symbole <= _STETS_NACHFRAGEN, sorted(symbole - _STETS_NACHFRAGEN)
