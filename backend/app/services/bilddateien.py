"""Hochgeladene Bilder pruefen - gemeinsam fuer Profilbilder und Hausordnung.

Hochgeladene Dateien sind grundsaetzlich nichts, dem man trauen sollte. Was
dagegen hilft, ist an beiden Stellen dasselbe: Groesse begrenzen, anhand der
ersten Bytes pruefen, ob es wirklich ein Bild ist, und einen eigenen
Dateinamen vergeben - der vom Nutzer gelieferte wird nie verwendet.

⚠️ **Deshalb steht es hier und nicht zweimal.** Die Regeln entstanden fuer
Profilbilder (``avatars``); als die Hausordnung Bilder bekam, waere die
naheliegende Loesung gewesen, sie dort noch einmal hinzuschreiben. Zwei
Kopien derselben Sicherheitspruefung sind aber genau die Sorte Duplikat, bei
der eine von beiden irgendwann nachgebessert wird und die andere nicht -
und niemand merkt, welche.

Unterschiedlich ist nur die erlaubte Groesse: Ein Profilbild ist ein Ausschnitt
in Briefmarkengroesse, ein Bild in der Hausordnung kann ein Bildschirmfoto
sein. Sie kommt deshalb als Parameter herein, alles andere gilt fuer beide.
"""

from __future__ import annotations


class BildFehler(Exception):
    """Die Datei ist kein brauchbares Bild - mit lesbarer Begruendung."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# Nur Bildformate, die der Browser gefahrlos darstellt.
#
# ⚠️ **Bewusst kein SVG.** Eine SVG-Datei darf Skripte enthalten; sie
# auszuliefern hiesse, jedem, der ein Bild hochladen darf, Code im Kontext der
# Seite zu erlauben. Das gilt hier doppelt: Die Hausordnung sieht jeder.
SIGNATUREN: list[tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
]


def erkennen(inhalt: bytes) -> tuple[str, str]:
    """Endung und Inhaltstyp anhand der **ersten Bytes** bestimmen.

    Nicht anhand des Dateinamens und nicht anhand dessen, was der Browser
    als Inhaltstyp behauptet - beides bestimmt der Absender.
    """
    for signatur, endung, inhaltstyp in SIGNATUREN:
        if inhalt.startswith(signatur):
            return endung, inhaltstyp

    # WebP: "RIFF....WEBP"
    if inhalt[:4] == b"RIFF" and inhalt[8:12] == b"WEBP":
        return "webp", "image/webp"

    raise BildFehler("Nur PNG-, JPEG-, GIF- oder WebP-Bilder sind möglich.")


def pruefen(inhalt: bytes, hoechstens: int) -> tuple[str, str]:
    """Leer, zu gross, kein Bild? Sonst Endung und Inhaltstyp."""
    if not inhalt:
        raise BildFehler("Die Datei ist leer.")
    if len(inhalt) > hoechstens:
        raise BildFehler(
            f"Das Bild ist zu groß ({len(inhalt) // 1024} KB). "
            f"Erlaubt sind {hoechstens // 1024} KB."
        )
    return erkennen(inhalt)
