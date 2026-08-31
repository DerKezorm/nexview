"""Die Bilder in der Hausordnung: ablegen, auflisten, ausliefern, loeschen.

Dieselben Pruefungen wie bei Profilbildern (``bilddateien``), nur mit eigener
Ablage und eigenen Grenzen. Was hier zusaetzlich gilt:

⚠️ **Nur Hochgeladenes wird angezeigt.** Die Auszeichnung im Text lautet
``![Text](bild:name)`` und kennt keine Adressen. Eine ``https://…``-Quelle
waere ein Zaehlpixel: Jeder Aufruf der Hausordnung meldete die IP-Adresse
jedes Nutzers an einen Dritten - und weil die Inhaltsregeln der Seite fremde
Bildquellen ohnehin abweisen, saehe der Betreiber nur ein kaputtes Bild und
wuesste nicht warum. Durchgesetzt wird das beim Anzeigen, nicht hier; dieser
Dienst kennt nur Dateien.

⚠️ **Der Ordner gehoert in die Sicherung.** In der Datenbank steht nur der
Name des Bildes. Fehlt ``hausordnung`` in ``sicherung.BEILAGEN``, ueberlebt
kein einziges Bild eine Wiederherstellung - und das faellt erst auf, wenn es
zu spaet ist. ``test_sicherung_waechter`` besteht deshalb darauf, dass dieser
Ordner dort benannt ist.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from . import bilddateien

#: Groesse je Bild. Grosszuegiger als bei Profilbildern: Hier stehen
#: Bildschirmfotos, keine Briefmarken.
MAX_BYTES = 4 * 1024 * 1024  # 4 MB

#: Wie viele Bilder die Hausordnung fuehren darf.
#:
#: Nicht, weil dreissig knapp waeren - sondern weil eine Hausordnung mit
#: hundert Bildern niemand mehr liest, und weil eine Obergrenze verhindert,
#: dass ein versehentlicher Stapel-Upload den Datentraeger fuellt.
HOECHSTENS = 30


class BildFehler(bilddateien.BildFehler):
    """Eigene Ausnahme, damit Aufrufer sie von anderen unterscheiden koennen."""


@dataclass
class Bild:
    """Ein abgelegtes Bild, wie die Verwaltung es auflistet."""

    name: str
    bytes: int


def ordner() -> Path:
    verzeichnis = get_settings().data_dir / "hausordnung"
    verzeichnis.mkdir(parents=True, exist_ok=True)
    return verzeichnis


def alle() -> list[Bild]:
    """Alle abgelegten Bilder - aeltestes zuerst."""
    return [
        Bild(name=datei.name, bytes=datei.stat().st_size)
        for datei in sorted(ordner().iterdir(), key=lambda d: d.stat().st_mtime)
        if datei.is_file()
    ]


def ablegen(inhalt: bytes) -> Bild:
    """Bild pruefen und ablegen. Gibt den vergebenen Namen zurueck.

    Der Name ist Zufall, nicht der mitgelieferte: Ein Dateiname vom Absender
    kann Pfadanteile enthalten, und er verraet nebenbei, wie die Datei auf
    dessen Rechner hiess.
    """
    vorhanden = alle()
    if len(vorhanden) >= HOECHSTENS:
        raise BildFehler(
            f"Es sind schon {len(vorhanden)} Bilder hinterlegt - mehr als "
            f"{HOECHSTENS} sind nicht vorgesehen. Lösche zuerst eines, das du "
            "nicht mehr brauchst."
        )

    try:
        endung, _ = bilddateien.pruefen(inhalt, MAX_BYTES)
    except bilddateien.BildFehler as fehler:
        raise BildFehler(fehler.message) from fehler

    name = f"{secrets.token_hex(16)}.{endung}"
    (ordner() / name).write_bytes(inhalt)
    return Bild(name=name, bytes=len(inhalt))


def lesen(name: str) -> tuple[bytes, str]:
    """Bild laden.

    Der Name kommt aus der Adresszeile und wird deshalb auf den reinen
    Dateinamen reduziert - gegen Pfad-Tricks wie ``../``.
    """
    datei = ordner() / Path(name).name
    if not datei.is_file():
        raise BildFehler("Bild nicht gefunden.")

    inhalt = datei.read_bytes()
    # Der Inhaltstyp kommt aus der Datei, nicht aus ihrer Endung: Ein
    # umbenanntes Etwas soll auch beim Ausliefern noch auffallen.
    _, inhaltstyp = bilddateien.erkennen(inhalt)
    return inhalt, inhaltstyp


def loeschen(name: str) -> bool:
    """Bild entfernen. ``False``, wenn es das gar nicht gab."""
    datei = ordner() / Path(name).name
    if not datei.is_file():
        return False
    datei.unlink(missing_ok=True)
    return True
