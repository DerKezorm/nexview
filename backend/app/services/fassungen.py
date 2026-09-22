"""Fassungen: in welcher Art ein Titel vorliegen kann.

Eine Fassung ist **eine Art, in der ein Titel vorliegen kann**, je Medienart
(Bauplan NEX-Modus, Abschnitt 2). Im ARR-Betrieb ist sie eine Instanz, spaeter
im NEX-Betrieb eine ``VersionDefinition`` aus nexcrate. Anfragen, Speicherposten
und Rechte haengen an ihrer ``kennung``, nicht mehr an einer der zwei festen
Stufen.

⚠️ **Die Stufe ist ab jetzt eine Ableitung.** ``QualityTier`` gibt es noch, weil
Oberflaeche, ``/api/v1`` und viele Dienste sie lesen. Geschrieben wird sie
nirgends mehr: Wer eine Anfrage oder einen Posten anlegt, setzt die Kennung,
und ``stufe(kennung)`` sagt, welche Stufe das ist (``uhd`` genau dann, wenn die
Fassung die Klasse ``uhd`` hat).

⚠️ **Klasse ist nicht Fassung.** ``hd`` und ``uhd`` sind Klassen: eine grobe
Aussage ueber die Aufloesung, die auch der Medienserver kennt
(``MediaServerLibraryItem.has_uhd``). Eine Fassung ist eine bestimmte Instanz
oder Definition. Im ARR-Betrieb fallen beide zusammen, spaeter nicht mehr: Zwei
Fassungen koennen dieselbe Klasse haben.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Fassung, MediaType, QualityTier, utcnow

if TYPE_CHECKING:
    from ..models import User
    from .settings_service import AppSettings

logger = logging.getLogger(__name__)

#: Unter welchem Betrieb eine Fassung oder Anfrage entstand.
ARR = "arr"
NEX = "nex"

KLASSE_HD = "hd"
KLASSE_UHD = "uhd"


@dataclass(frozen=True)
class ArrFassung:
    """Eine der vier festen Fassungen des ARR-Betriebs."""

    #: Die Instanz-Kennung, die es schon vor den Fassungen gab. An ihr haengt
    #: gespeicherter Zustand (Webhooks, Gesundheit, Downloads); sie darf sich
    #: nie aendern.
    kennung: str
    media_type: str
    stufe: QualityTier
    #: Die Einstellung mit dem frei waehlbaren Anzeigenamen.
    name_schluessel: str
    #: Der Name, wenn die Einstellung leer ist.
    name_vorgabe: str

    @property
    def klasse(self) -> str:
        return KLASSE_UHD if self.stufe == QualityTier.uhd else KLASSE_HD

    @property
    def reihenfolge(self) -> int:
        return 1 if self.stufe == QualityTier.uhd else 0

    @property
    def offen_vorgabe(self) -> bool:
        """Offen fuer alle? Standard ja, 4K nein - wie vor den Fassungen."""
        return self.stufe == QualityTier.standard


#: Die vier Fassungen des ARR-Betriebs, in Anzeigereihenfolge.
ARR_FASSUNGEN: tuple[ArrFassung, ...] = (
    ArrFassung("radarr-standard", "movie", QualityTier.standard, "radarr_name", "Radarr"),
    ArrFassung("radarr-uhd", "movie", QualityTier.uhd, "radarr_uhd_name", "Radarr 4K"),
    ArrFassung("sonarr-standard", "tv", QualityTier.standard, "sonarr_name", "Sonarr"),
    ArrFassung("sonarr-uhd", "tv", QualityTier.uhd, "sonarr_uhd_name", "Sonarr 4K"),
)

_ARR_NACH_KENNUNG = {f.kennung: f for f in ARR_FASSUNGEN}
_ARR_NACH_ART_STUFE = {(f.media_type, f.stufe): f for f in ARR_FASSUNGEN}

#: Alle Kennungen des ARR-Betriebs.
ARR_KENNUNGEN: tuple[str, ...] = tuple(_ARR_NACH_KENNUNG)

#: Die ARR-Fassungen, die ab Werk offen fuer alle sind.
ARR_OFFEN: frozenset[str] = frozenset(f.kennung for f in ARR_FASSUNGEN if f.offen_vorgabe)


@dataclass(frozen=True)
class FassungInfo:
    """Eine Fassung, wie die Einstellungen sie kennen - ohne Datenbank."""

    kennung: str
    media_type: str
    name: str
    klasse: str | None
    reihenfolge: int
    quelle: str


def _art(media_type: MediaType | str) -> str:
    return media_type.value if isinstance(media_type, MediaType) else str(media_type)


def _stufe(tier: QualityTier | str) -> QualityTier:
    return tier if isinstance(tier, QualityTier) else QualityTier(str(tier))


def arr_fassung(kennung: str | None) -> ArrFassung | None:
    return _ARR_NACH_KENNUNG.get(kennung or "")


def arr_kennung(media_type: MediaType | str, tier: QualityTier | str) -> str:
    """Die Kennung der ARR-Fassung fuer diese Art und Stufe.

    Fuer alle Stellen, die heute noch in Stufen denken und an einer Anfrage
    oder einem Posten ankommen: Dort wird aus der Stufe die Kennung.
    """
    return _ARR_NACH_ART_STUFE[(_art(media_type), _stufe(tier))].kennung


def klasse(kennung: str | None) -> str | None:
    """Die Klasse einer Fassung (``hd``, ``uhd``, spaeter auch ``sd`` oder keine)."""
    fassung = arr_fassung(kennung)
    return fassung.klasse if fassung is not None else None


def stufe(kennung: str | None) -> QualityTier:
    """Die Stufe als Ableitung: ``uhd`` genau fuer Fassungen der Klasse ``uhd``."""
    return QualityTier.uhd if klasse(kennung) == KLASSE_UHD else QualityTier.standard


def stufenwort(kennung: str | None) -> str:
    """Die Klasse so, wie eine Regel sie nennt: ``hd`` oder ``uhd``."""
    return KLASSE_UHD if stufe(kennung) == QualityTier.uhd else KLASSE_HD


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


def offen_fuer_alle(db: Session, kennung: str) -> bool:
    """Darf jeder diese Fassung anfragen? (Stufe 1 der Leiter, Abschnitt 2.3.)

    Ohne Zeile gilt die Vorgabe des ARR-Betriebs. Das trifft eine frische
    Datenbank vor dem ersten Abgleich und jede Fassung, die es nicht gibt.
    """
    zeile = db.get(Fassung, kennung)
    if zeile is not None:
        return zeile.offen_fuer_alle
    fassung = arr_fassung(kennung)
    return fassung.offen_vorgabe if fassung is not None else False


def bekannte_kennungen(db: Session) -> frozenset[str]:
    """Jede Kennung, die eine Regel nennen darf: die ARR-Fassungen und jede Zeile."""
    return frozenset(ARR_KENNUNGEN) | frozenset(db.scalars(select(Fassung.kennung)))


def offene_kennungen(db: Session) -> frozenset[str]:
    """Die Kennungen aller Fassungen, die offen fuer alle sind.

    Zeilen gehen vor; wo eine ARR-Fassung noch keine Zeile hat, gilt ihre
    Vorgabe.
    """
    zeilen = {zeile.kennung: zeile.offen_fuer_alle for zeile in db.scalars(select(Fassung))}
    vorgabe = {k: k in ARR_OFFEN for k in ARR_KENNUNGEN}
    return frozenset(k for k, offen in {**vorgabe, **zeilen}.items() if offen)


def darf_anfragen(db: Session, user: User, kennung: str) -> bool:
    """Darf dieses Konto diese Fassung anfragen?

    Die Leiter aus dem Bauplan (Abschnitt 2.3): Wer freigeben darf, darf alles;
    eine offene Fassung darf jeder; sonst entscheidet das Recht am Konto.
    """
    if user.can_approve:
        return True
    if offen_fuer_alle(db, kennung):
        return True
    recht = user.fassung_recht(kennung)
    return recht is not None and recht.anfragen


def auto_freigabe(db: Session, user: User, media_type: MediaType, kennung: str) -> bool:
    """Gilt eine Anfrage auf diese Fassung sofort als freigegeben?

    Bei einer offenen Fassung folgen die Haken am Konto
    (``auto_approve``, ``auto_approve_movies``/``_series``), wie bisher fuer
    Standard. Sonst das Recht je Fassung, wie bisher ``auto_approve_uhd``.
    """
    if user.can_approve:
        return True
    if offen_fuer_alle(db, kennung):
        return user.auto_approve_offen(media_type)
    recht = user.fassung_recht(kennung)
    return recht is not None and recht.auto_freigabe


def beim_start() -> None:
    """Die Tabelle einmal beim Start abgleichen, in einer eigenen Sitzung."""
    from ..db import SessionLocal
    from .settings_service import load_settings

    with SessionLocal() as db:
        abgleichen(db, load_settings(db))
        db.commit()
