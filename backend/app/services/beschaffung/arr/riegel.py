"""Der Riegel vor den Betreiberwerkzeugen: ``409 not_in_this_mode``.

Profile, TRaSH, Benennung, Pfad-Zuordnung, Webhook-Pflege, Kollisionen und
die Medienserver-Verbindung in Arr sind Werkzeuge fuer Radarr und Sonarr. Im
NEX-Betrieb gehoeren sie nexcrate; die Oberflaeche blendet die Reiter aus
(Bauplan Abschnitt 3.2), und die Adressen antworten ``409``.

⚠️ **Der Riegel haengt am ganzen Router, nicht an einzelnen Adressen.** Eine
Adresse, die man einzeln haette vergessen koennen, ist genau die, die im
NEX-Betrieb an einer leeren Instanz scheitert - mit einem Fehler aus der
Tiefe statt einer Antwort.
"""

from __future__ import annotations

from ....deps import DbSession
from ...settings_service import load_settings
from .. import werkzeuge_pruefen


def nur_mit_werkzeugen(db: DbSession) -> None:
    """Abhaengigkeit fuer die Router der Arr-Werkzeuge."""
    werkzeuge_pruefen(load_settings(db))
