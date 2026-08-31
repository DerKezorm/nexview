"""Profilbilder: pruefen, ablegen, ausliefern.

Was ein hochgeladenes Bild ueberstehen muss, steht in ``bilddateien`` - die
Regeln gelten fuer die Hausordnung genauso. Hier bleibt, was nur Profilbilder
betrifft: wo sie liegen, wie gross sie sein duerfen und dass beim Austauschen
das alte verschwindet.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from ..config import get_settings
from . import bilddateien

MAX_BYTES = 2 * 1024 * 1024  # 2 MB


class AvatarError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def avatar_dir() -> Path:
    directory = get_settings().data_dir / "avatars"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def detect(content: bytes) -> tuple[str, str]:
    """Endung und Inhaltstyp anhand der ersten Bytes bestimmen.

    ⚠️ **Die Pruefung selbst steht in ``bilddateien``.** Hier wird nur die
    Ausnahme uebersetzt: Die Aufrufer dieses Moduls fangen ``AvatarError``,
    und daran soll das Herausheben des gemeinsamen Kerns nichts geaendert
    haben.
    """
    try:
        return bilddateien.erkennen(content)
    except bilddateien.BildFehler as fehler:
        raise AvatarError(fehler.message) from fehler


def save(content: bytes, previous: str | None = None) -> str:
    """Bild ablegen und den Dateinamen zurueckgeben."""
    try:
        suffix, _ = bilddateien.pruefen(content, MAX_BYTES)
    except bilddateien.BildFehler as fehler:
        raise AvatarError(fehler.message) from fehler

    name = f"{secrets.token_hex(16)}.{suffix}"
    (avatar_dir() / name).write_bytes(content)

    remove(previous)
    return name


def remove(name: str | None) -> None:
    """Altes Bild loeschen - sonst sammeln sich Dateien an."""
    if not name:
        return
    datei = avatar_dir() / Path(name).name  # Pfadanteile abschneiden
    if datei.is_file():
        datei.unlink(missing_ok=True)


def read(name: str) -> tuple[bytes, str]:
    """Bild laden. Der Name kommt aus der Datenbank, wird aber trotzdem
    auf den reinen Dateinamen reduziert - gegen Pfad-Tricks wie ``../``."""
    datei = avatar_dir() / Path(name).name
    if not datei.is_file():
        raise AvatarError("Bild nicht gefunden.")

    content = datei.read_bytes()
    _, media_type = detect(content)
    return content, media_type
