"""Fassungen: in welcher Art ein Titel vorliegen kann.

Eine Fassung ist **eine Art, in der ein Titel vorliegen kann**, je Medienart
(Bauplan NEX-Modus, Abschnitt 2). Im ARR-Betrieb ist sie eine Instanz, spaeter
im NEX-Betrieb eine ``VersionDefinition`` aus nexcrate. Anfragen, Speicherposten
und Rechte haengen an ihrer ``kennung``, nicht mehr an einer der zwei festen
Stufen.

⚠️ **Die Stufe ist nur noch eine Ableitung.** Das Aufzaehlungsfeld dafuer ist
weg (Scheibe 3); ``stufe(kennung)`` sagt als Wort (``standard``/``uhd``), welche
Instanz des ARR-Wegs eine Fassung ist (``uhd`` genau dann, wenn die Fassung die
Klasse ``uhd`` hat). Gelesen wird sie nur noch hinter der Grenze, die bis
Scheibe 5 in Instanzen spricht, und fuer ``tier`` in ``/api/v1``.

⚠️ **Klasse ist nicht Fassung.** ``hd`` und ``uhd`` sind Klassen: eine grobe
Aussage ueber die Aufloesung, die auch der Medienserver kennt
(``MediaServerLibraryItem.has_uhd``). Eine Fassung ist eine bestimmte Instanz
oder Definition. Im ARR-Betrieb fallen beide zusammen, spaeter nicht mehr: Zwei
Fassungen koennen dieselbe Klasse haben.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Fassung, MediaType
from .beschaffung import (
    ARR,
    KLASSE_HD,
    KLASSE_UHD,
    NEX,
    FassungInfo,
    betriebsart,
    fassungen_gemerkt,
    feste_fassungen,
    get_beschaffung,
)

if TYPE_CHECKING:
    from ..models import User
    from .settings_service import AppSettings

logger = logging.getLogger(__name__)

#: Die festen Fassungen des ARR-Betriebs, in Anzeigereihenfolge. Sie stehen
#: hinter der Grenze (``beschaffung/arr/fassungen.py``); hier nur gelesen.
ARR_FASSUNGEN = feste_fassungen()

_ARR_NACH_KENNUNG = {f.kennung: f for f in ARR_FASSUNGEN}
_ARR_NACH_ART_STUFE = {(f.media_type, f.stufe): f for f in ARR_FASSUNGEN}

#: Alle Kennungen des ARR-Betriebs.
ARR_KENNUNGEN: tuple[str, ...] = tuple(_ARR_NACH_KENNUNG)

#: Die ARR-Fassungen, die ab Werk offen fuer alle sind.
ARR_OFFEN: frozenset[str] = frozenset(f.kennung for f in ARR_FASSUNGEN if f.offen_vorgabe)


def _art(media_type: MediaType | str) -> str:
    return media_type.value if isinstance(media_type, MediaType) else str(media_type)


def arr_fassung(kennung: str | None) -> Any:
    return _ARR_NACH_KENNUNG.get(kennung or "")


def arr_kennung(media_type: MediaType | str, tier: str) -> str:
    """Die Kennung der ARR-Fassung fuer diese Art und Stufe.

    Fuer alle Stellen, die heute noch in Stufen denken und an einer Anfrage
    oder einem Posten ankommen: Dort wird aus der Stufe die Kennung.
    """
    return _ARR_NACH_ART_STUFE[(_art(media_type), str(tier))].kennung


def hauptkennung(media_type: MediaType | str) -> str | None:
    """Die Fassung der Hauptachse, wenn niemand eine nennt.

    Die Hauptachse ist das, was ``status`` an einer Karte meint und was eine
    Anfrage ohne Angabe bekommt. Im ARR-Betrieb die Standard-Instanz, auch
    wenn sie (noch) nicht eingerichtet ist: Dann sagt die Anfrage das mit
    eigenem Satz, statt still eine andere Fassung zu nehmen.

    ⚠️ **Im NEX-Betrieb die erste Fassung dieser Medienart** (Bauplan 2.2),
    und ``None``, solange es keine gibt. Die Arr-Kennung stehenzulassen
    hiesse, jeder Karte und jedem Formular eine Fassung anzubieten, die es in
    dieser Installation gar nicht gibt; eine Anfrage ohne Angabe bekaeme sie.

    Gefragt wird der **Merker**, nicht die Sitzung: Diese Funktion wird an
    Stellen gerufen, die keine Einstellungen zur Hand haben (Karten, Modelle).
    ``load_settings`` setzt ihn bei jeder Anfrage.
    """
    if betriebsart() != ARR:
        art = _art(media_type)
        for eintrag in _betriebsart_fassungen():
            if eintrag.media_type == art:
                return eintrag.kennung
        return None
    return arr_kennung(media_type, "standard")


def _betriebsart_fassungen() -> tuple[FassungInfo, ...]:
    """Die gemerkten Fassungen des eingestellten Wegs - ohne Sitzung, ohne Netz."""
    return fassungen_gemerkt()


def gewaehlt(
    settings: AppSettings, media_type: MediaType | str, fassung: str | None, tier: str = "standard"
) -> str | None:
    """Welche Fassung eine Anfrage meint: die genannte, sonst aus der alten Stufe.

    ``tier`` ist zugesagt (``/api/v1``, Bauplan Abschnitt 12): ``uhd`` waehlt
    die erste eingerichtete Fassung der Klasse ``uhd``, sonst die ARR-Instanz
    fuer 4K (deren Fehlen die Anfrage dann beim Namen nennt); ``standard``
    die Hauptfassung (``None``, wenn es im NEX-Betrieb keine gibt). Ob es die
    genannte Fassung gibt und ob sie zur Medienart passt, prueft
    ``requests_service.create_request``.
    """
    if fassung:
        return fassung
    if tier == "uhd":
        for eintrag in settings.fassungen_fuer(_art(media_type)):
            if eintrag.klasse == KLASSE_UHD:
                return eintrag.kennung
        return arr_kennung(media_type, "uhd")
    return hauptkennung(media_type)


def info(settings: AppSettings, kennung: str) -> FassungInfo:
    """Die Fassung mit Name und Klasse - auch, wenn sie nicht eingerichtet ist.

    Eine Anfrage oder Karte nennt ihre Fassung weiter beim Namen, wenn die
    Instanz fehlt; eine unbekannte Kennung steht fuer sich selbst.
    """
    eingerichtet = settings.fassung(kennung)
    if eingerichtet is not None:
        return eingerichtet
    fest = arr_fassung(kennung)
    if fest is not None:
        return FassungInfo(
            kennung=kennung,
            media_type=fest.media_type,
            name=fest.name_vorgabe,
            klasse=fest.klasse,
            reihenfolge=fest.reihenfolge,
            quelle=ARR,
        )
    return FassungInfo(
        kennung=kennung, media_type="", name=kennung, klasse=None, reihenfolge=99, quelle=""
    )


def art_der(settings: AppSettings, kennung: str) -> str | None:
    """Zu welcher Medienart gehoert diese Fassung? ``None``: unbekannt."""
    eingerichtet = settings.fassung(kennung)
    if eingerichtet is not None:
        return eingerichtet.media_type
    fest = arr_fassung(kennung)
    return fest.media_type if fest is not None else None


def quelle(kennung: str | None) -> str:
    """Aus welchem Betrieb eine Fassungskennung stammt: ``arr`` oder ``nex``.

    Die vier ARR-Kennungen stehen fest (``ARR_KENNUNGEN``); alles andere kann
    nur aus nexcrate stammen. Gebraucht ueberall dort, wo aus einer Kennung
    allein hervorgehen muss, welche Anker gelten - beim Speicherschluessel
    etwa haengen Serien im ARR-Betrieb an TVDB und im NEX-Betrieb an TMDB
    (Bauplan 6.5).
    """
    return ARR if str(kennung or "") in _ARR_NACH_KENNUNG else NEX


def klasse(kennung: str | None) -> str | None:
    """Die Klasse einer Fassung (``hd``, ``uhd``, spaeter auch ``sd`` oder keine).

    ⚠️ **Auch fuer Fassungen aus nexcrate**, ueber den Merker wie bei
    ``hauptkennung``: Ohne ihn hatte jede nexcrate-Fassung keine Klasse,
    ``stufe()`` hielt eine 4K-Fassung fuer ``standard``, und der Speicher
    rechnete HD und 4K desselben Films demselben Anfragenden zu.

    Der Merker fuehrt nur **aktive** Fassungen. Eine, die nexcrate nicht mehr
    kennt, hat hier keine Klasse mehr; gefuellt wird er beim Start aus der
    Tabelle und von ``storage.abgleichen`` vor jedem Speicherlauf.
    """
    fassung = arr_fassung(kennung)
    if fassung is not None:
        return fassung.klasse
    if kennung:
        for eintrag in _betriebsart_fassungen():
            if eintrag.kennung == kennung:
                return eintrag.klasse
    return None


def stufe(kennung: str | None) -> str:
    """Die Stufe als Ableitung: ``uhd`` genau fuer Fassungen der Klasse ``uhd``."""
    return "uhd" if klasse(kennung) == KLASSE_UHD else "standard"


def stufenwort(kennung: str | None) -> str:
    """Die Klasse so, wie eine Regel sie nennt: ``hd`` oder ``uhd``."""
    return KLASSE_UHD if stufe(kennung) == "uhd" else KLASSE_HD


def serverstufe(
    settings: AppSettings, media_type: MediaType | str, kennung: str | None = None
) -> str | None:
    """Welche Kopien des Medienservers zaehlen fuer diese Fassung?

    Ohne Fassung der Klasse ``uhd`` fuer diese Medienart gibt es nur eine
    Achse, und jede Kopie zaehlt (``None``). Gibt es eine, sind es zwei
    Achsen, und es zaehlt nur die Kopie der eigenen Stufe: Eine reine
    4K-Kopie darf die HD-Anfrage nicht sperren.

    ⚠️ **Der Nachfolger von ``arr_configured(art, "uhd")`` ausserhalb der
    Grenze.** Das war im NEX-Betrieb immer ``False``; dort zaehlte jede Kopie,
    und eine reine 4K-Kopie sperrte die HD-Anfrage mit 409. Ohne ``kennung``
    gilt die Hauptfassung.
    """
    kennung = kennung or hauptkennung(media_type)
    if kennung is None:
        return None
    if not any(f.klasse == KLASSE_UHD for f in settings.fassungen_fuer(_art(media_type))):
        return None
    return stufe(kennung)


def hd_kennung(settings: AppSettings, media_type: MediaType | str) -> str | None:
    """Die erste eingerichtete Fassung dieser Art, die **nicht** 4K ist.

    Gebraucht, wo gefragt wird, ob eine 4K-Datei in der HD-Fassung liegt
    (``mediaserver_library.echte_uhd_kennungen``). Die Hauptfassung ist das
    nur, solange sie HD ist; im NEX-Betrieb kann nexcrate 4K vorn fuehren.
    """
    return next(
        (f.kennung for f in settings.fassungen_fuer(_art(media_type)) if f.klasse != KLASSE_UHD),
        None,
    )


def aus_einstellungen(settings: AppSettings) -> tuple[FassungInfo, ...]:
    """Die eingerichteten Fassungen, in Anzeigereihenfolge (aus dem Weg)."""
    return get_beschaffung(settings).fassungen()


def abgleichen(db: Session, settings: AppSettings) -> None:
    """Die Tabelle ``fassungen`` auf den Stand der Quelle bringen.

    Wie, entscheidet der Weg (``Beschaffung.fassungen_abgleichen``). Committet
    nicht; der Aufrufer entscheidet.
    """
    get_beschaffung(settings).fassungen_abgleichen(db)


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
