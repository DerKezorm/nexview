"""Umzug von Seerr nach Nexview - der lesende Teil.

⚠️ **Dieses Paket schreibt nirgends.** Weder in Nexviews Datenbank noch nach
Seerr. Es liest, bildet ab und legt eine Vorschau vor; das Schreiben ist eine
eigene Stufe und steht heute noch nicht.

Der Zuschnitt ist Absicht: Der teuerste Fehler eines Umzugs ist ein falsch
zugeordnetes Konto, und den sieht man nur, wenn man die Liste vorher zu
Gesicht bekommt. Deshalb ist die Vorschau nicht die Vorstufe des Features,
sondern das Feature.
"""

from __future__ import annotations

from .client import ERLAUBTE_PFADE, SeerrClient, SeerrFehler, Zugang
from .vorschau import Vorschau, vorschau_bauen

__all__ = [
    "ERLAUBTE_PFADE",
    "SeerrClient",
    "SeerrFehler",
    "Vorschau",
    "Zugang",
    "vorschau_bauen",
]
