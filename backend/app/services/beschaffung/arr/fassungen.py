"""Die vier festen Fassungen des ARR-Betriebs: eine je Instanz.

Umgezogen aus ``services/fassungen.py`` (Scheibe 2 des NEX-Umbaus): Nur hier
stehen die Instanz-Kennungen woertlich. Was fuer jede Fassung gilt (Klasse,
Stufe als Ableitung, Rechte), bleibt dort.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....models import Fassung, utcnow
from ..base import ARR, KLASSE_HD, KLASSE_UHD, FassungInfo

if TYPE_CHECKING:
    from ...settings_service import AppSettings


@dataclass(frozen=True)
class ArrFassung:
    """Eine der vier festen Fassungen des ARR-Betriebs."""

    #: Die Instanz-Kennung, die es schon vor den Fassungen gab. An ihr haengt
    #: gespeicherter Zustand (Webhooks, Gesundheit, Downloads); sie darf sich
    #: nie aendern.
    kennung: str
    media_type: str
    #: Die Instanz: ``standard`` oder ``uhd``.
    stufe: str
    #: Die Einstellung mit dem frei waehlbaren Anzeigenamen.
    name_schluessel: str
    #: Der Name, wenn die Einstellung leer ist.
    name_vorgabe: str

    @property
    def klasse(self) -> str:
        return KLASSE_UHD if self.stufe == "uhd" else KLASSE_HD

    @property
    def reihenfolge(self) -> int:
        return 1 if self.stufe == "uhd" else 0

    @property
    def offen_vorgabe(self) -> bool:
        """Offen fuer alle? Standard ja, 4K nein - wie vor den Fassungen."""
        return self.stufe == "standard"


#: Die vier Fassungen des ARR-Betriebs, in Anzeigereihenfolge.
ARR_FASSUNGEN: tuple[ArrFassung, ...] = (
    ArrFassung("radarr-standard", "movie", "standard", "radarr_name", "Radarr"),
    ArrFassung("radarr-uhd", "movie", "uhd", "radarr_uhd_name", "Radarr 4K"),
    ArrFassung("sonarr-standard", "tv", "standard", "sonarr_name", "Sonarr"),
    ArrFassung("sonarr-uhd", "tv", "uhd", "sonarr_uhd_name", "Sonarr 4K"),
)

def aus_einstellungen(settings: AppSettings) -> tuple[FassungInfo, ...]:
    """Die eingerichteten Fassungen, in Anzeigereihenfolge."""
    eingerichtet = {instanz.kennung: instanz.name for instanz in settings.arr_instanzen()}
    return tuple(
        FassungInfo(
            kennung=f.kennung,
            media_type=f.media_type,
            name=eingerichtet[f.kennung],
            klasse=f.klasse,
            reihenfolge=f.reihenfolge,
            quelle=ARR,
        )
        for f in ARR_FASSUNGEN
        if f.kennung in eingerichtet
    )


def abgleichen(db: Session, settings: AppSettings) -> None:
    """Die Tabelle ``fassungen`` auf den Stand der Einstellungen bringen.

    Im ARR-Betrieb gibt es immer genau die vier Zeilen; ``aktiv`` sagt, ob die
    Instanz eingerichtet ist. Eine Instanz, die ausgetragen wird, behaelt ihre
    Zeile mit ``aktiv=False``: Anfragen und Posten zeigen weiter ihren Namen.

    ⚠️ **``offen_fuer_alle`` setzt nur die erste Zeile.** Danach gehoert der
    Wert dem Betreiber; ein Abgleich, der ihn zuruecksetzte, naehme eine
    Entscheidung zurueck, die niemand zuruecknehmen wollte.

    Committet nicht; der Aufrufer entscheidet.
    """
    eingerichtet = {instanz.kennung: instanz.name for instanz in settings.arr_instanzen()}
    vorhanden = {zeile.kennung: zeile for zeile in db.scalars(select(Fassung))}
    jetzt = utcnow()
    for f in ARR_FASSUNGEN:
        aktiv = f.kennung in eingerichtet
        name = eingerichtet.get(f.kennung) or getattr(settings, f.name_schluessel, "") or f.name_vorgabe
        zeile = vorhanden.get(f.kennung)
        if zeile is None:
            zeile = Fassung(
                kennung=f.kennung,
                media_type=f.media_type,
                quelle=ARR,
                offen_fuer_alle=f.offen_vorgabe,
                gruende=[],
            )
            db.add(zeile)
        elif zeile.aktiv and not aktiv:
            zeile.verschwunden_am = jetzt
        zeile.name = name
        zeile.klasse = f.klasse
        zeile.reihenfolge = f.reihenfolge
        zeile.aktiv = aktiv
        zeile.bereit = aktiv
        if aktiv:
            zeile.gesehen_am = jetzt
            zeile.verschwunden_am = None
