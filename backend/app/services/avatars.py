"""Profilbilder: pruefen, ablegen, ausliefern.

Hochgeladene Dateien sind grundsaetzlich nichts, dem man trauen sollte.
Deshalb: Groesse begrenzen, anhand der ersten Bytes pruefen, ob es wirklich
ein Bild ist, und einen eigenen Dateinamen vergeben - der vom Nutzer gelieferte
Name wird nie verwendet.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from ..config import get_settings

MAX_BYTES = 2 * 1024 * 1024  # 2 MB

# Nur Bildformate, die der Browser gefahrlos darstellt. Bewusst kein SVG:
# das darf Skripte enthalten.
SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
]


class AvatarError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def avatar_dir() -> Path:
    directory = get_settings().data_dir / "avatars"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def detect(content: bytes) -> tuple[str, str]:
    """Endung und Inhaltstyp anhand der ersten Bytes bestimmen."""
    for signature, suffix, media_type in SIGNATURES:
        if content.startswith(signature):
            return suffix, media_type

    # WebP: "RIFF....WEBP"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp", "image/webp"

    raise AvatarError("Nur PNG-, JPEG-, GIF- oder WebP-Bilder sind möglich.")


def save(content: bytes, previous: str | None = None) -> str:
    """Bild ablegen und den Dateinamen zurueckgeben."""
    if not content:
        raise AvatarError("Die Datei ist leer.")
    if len(content) > MAX_BYTES:
        raise AvatarError(
            f"Das Bild ist zu groß ({len(content) // 1024} KB). "
            f"Erlaubt sind {MAX_BYTES // 1024} KB."
        )

    suffix, _ = detect(content)
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
