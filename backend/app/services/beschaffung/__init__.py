"""Die Grenze zur Beschaffung.

Hier - und nur hier - steht, welche Wege es gibt. Der Rest der Anwendung holt
sich ueber ``get_beschaffung`` eine ``Beschaffung`` und spricht ausschliesslich
mit dieser Schnittstelle; Radarr, Sonarr und spaeter nexcrate kennt ausserhalb
dieses Pakets niemand (``tests/test_beschaffung_grenze.py``).

Die Wege werden erst beim ersten Aufruf geladen. Sie selbst brauchen Dienste,
die ihrerseits die Grenze rufen (``requests_service``, ``storage``); ein
Import beim Laden liefe im Kreis.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from .base import (
    ARR,
    AUTOMATISCH_MOEGLICH,
    KLASSE_HD,
    KLASSE_UHD,
    NEX,
    SPERRT,
    WARNT,
    ZUSTAENDE,
    Aktion,
    Beschaffung,
    BeschaffungError,
    DownloadFehler,
    Faehigkeiten,
    FassungInfo,
    FilmStand,
    Folge,
    Kennt,
    Kenntnis,
    Korb,
    Nachschlag,
    Nachschlagen,
    NichtsZuLoeschen,
    Pruefbefund,
    SerienBestand,
    SerienStand,
    Staffelstand,
    WarteschlangenEintrag,
    jahr_aus,
    jahre_passen,
    normalize_title,
    treffer_nach_titel,
)

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from fastapi import APIRouter
    from sqlalchemy.orm import Session

    from ..settings_service import AppSettings

__all__ = [
    "ARR",
    "AUTOMATISCH_MOEGLICH",
    "KLASSE_HD",
    "KLASSE_UHD",
    "NEX",
    "SPERRT",
    "WARNT",
    "ZUSTAENDE",
    "Aktion",
    "Beschaffung",
    "BeschaffungError",
    "DownloadFehler",
    "Faehigkeiten",
    "FassungInfo",
    "FilmStand",
    "Folge",
    "Kennt",
    "Kenntnis",
    "Korb",
    "Nachschlag",
    "Nachschlagen",
    "NichtsZuLoeschen",
    "Pruefbefund",
    "SerienBestand",
    "SerienStand",
    "Staffelstand",
    "WarteschlangenEintrag",
    "alle_router",
    "beim_start",
    "bestand_verwerfen",
    "betriebsart",
    "betriebsart_merken",
    "download_aktionen_moeglich",
    "download_frisch",
    "download_gruende",
    "download_verlauf",
    "download_verlauf_aufraeumen",
    "fassungen_auffrischen",
    "feste_fassungen",
    "gesundheit_je_instanz",
    "get_beschaffung",
    "haenger_je_instanz",
    "hintergrundaufgaben",
    "jahr_aus",
    "jahre_passen",
    "nach_wiederherstellung",
    "normalize_title",
    "providers",
    "rueckkanal_bald_pflegen",
    "schliessen",
    "treffer_nach_titel",
    "weckruf",
    "werkzeuge_pruefen",
]


def providers() -> dict[str, type[Beschaffung]]:
    """Die Wege nach ihrer Kennung in den Einstellungen."""
    from .arr.weg import ArrBeschaffung
    from .nex.weg import NexBeschaffung

    return {ArrBeschaffung.art: ArrBeschaffung, NexBeschaffung.art: NexBeschaffung}


def feste_fassungen() -> tuple[Any, ...]:
    """Die Fassungen, die ein Weg fest mitbringt (ARR: eine je Instanz).

    Gebraucht, bevor irgendetwas geladen ist: ``settings_service`` baut daraus
    seine Instanzliste, und die Stufe einer Anfrage wird daraus abgeleitet.
    Deshalb nur die schlanke Datei des Wegs, nicht der Weg selbst.
    """
    from .arr.fassungen import ARR_FASSUNGEN

    return ARR_FASSUNGEN


def get_beschaffung(settings: AppSettings) -> Beschaffung:
    """Der Weg, ueber den diese Installation beschafft.

    Die Einstellung ``beschaffung`` entscheidet: ``arr`` (Radarr und Sonarr)
    oder ``nex`` (nexcrate). Ein Wert, den es nicht gibt, gilt als ``arr`` -
    eine Installation ohne Beschaffung waere schlimmer als die alte.
    """
    art = getattr(settings, "beschaffung", ARR)
    gewaehlt = providers().get(art) or providers()[ARR]
    return gewaehlt(settings)


async def fassungen_auffrischen(db: Session, settings: AppSettings) -> bool:
    """Die Fassungen bei der Quelle nachlesen, wenn der Weg das kennt.

    ``True``, wenn etwas geschrieben wurde (der Aufrufer committet). Im
    ARR-Betrieb gibt es nichts nachzulesen: Die Fassungen stehen in den
    Einstellungen, und ``save_settings`` gleicht sie beim Speichern ab.

    Nachgelesen wird nur, wenn ein Ereignis es verlangt oder noch nie etwas
    gelesen wurde - eine Abfrage je Rundgang waere eine je zwei Minuten fuer
    etwas, das sich im Monat einmal aendert.
    """
    if settings.beschaffung != NEX:
        return False
    from .nex import ereignisse
    from .nex import fassungen as nex_fassungen

    wecker = ereignisse.wecker()
    if not wecker.fassungen and nex_fassungen.aus_einstellungen(settings):
        return False
    wecker.fassungen = False
    await nex_fassungen.auffrischen(db, settings)
    return True


def werkzeuge_pruefen(settings: AppSettings) -> None:
    """Gibt es in dieser Betriebsart die Betreiberwerkzeuge? Sonst ``409``.

    Profile, TRaSH, Benennung, Pfade, Webhooks und Kollisionen sind Werkzeuge
    fuer Radarr und Sonarr. Im NEX-Betrieb gehoeren sie nexcrate und sind
    **ganz** weg, nicht halb (Bauplan Abschnitt 3.2): Die Oberflaeche blendet
    die Reiter aus, und wer die Adresse trotzdem aufruft, bekommt eine
    ehrliche Antwort statt eines Fehlers aus der Tiefe.
    """
    from fastapi import HTTPException

    from ...meldungen import meldung

    if get_beschaffung(settings).faehigkeiten().betreiberwerkzeuge:
        return
    raise HTTPException(
        status_code=409,
        detail=meldung(
            "not_in_this_mode",
            "Dieses Werkzeug gehört zur anderen Betriebsart der Beschaffung.",
            beschaffung=settings.beschaffung,
        ),
    )


def alle_router() -> list[APIRouter]:
    """Die eigenen Adressen aller Wege - beim Start eingebunden.

    Alle, nicht nur die des eingestellten Wegs: Die Betriebsart kann im
    laufenden Betrieb wechseln, eingebundene Router aber nicht mehr.
    """
    return [router for weg in providers().values() for router in weg.router()]


def beim_start() -> None:
    """Was jeder Weg beim Hochfahren einmal erledigt (etwa Liegengebliebenes aufnehmen)."""
    for weg in providers().values():
        weg.beim_start()


def hintergrundaufgaben(stop: asyncio.Event) -> list[Coroutine[Any, Any, None]]:
    """Dauerlaeufer der Wege, als Koroutinen zum Starten."""
    return [aufgabe for weg in providers().values() for aufgabe in weg.hintergrundaufgaben(stop)]


async def schliessen() -> None:
    """Offene Verbindungen der Wege beim Herunterfahren schliessen."""
    for weg in providers().values():
        await weg.schliessen()


# --------------------------------------------------------------------------
# Was ohne Einstellungen gefragt wird: der Wecker des Rundgangs und die
# Tabellen je Instanz (Gesundheit, haengende Downloads). Sie gehoeren dem Weg,
# der sie schreibt; gelesen werden sie ueberall.
#
# ⚠️ Welcher Weg gefragt wird, sagt ``betriebsart()`` - gemerkt beim Laden
# der Einstellungen. Ohne Einstellungen in der Hand gibt es keinen anderen Weg,
# und eine Sitzung nur dafuer aufzumachen waere teurer als ein Merker.


#: Die zuletzt geladene Betriebsart. ``load_settings`` merkt sie bei jeder
#: Anfrage; ohne sie wuesste der Weckruf nicht, wen er weckt.
_betriebsart = ARR


def betriebsart_merken(art: str) -> None:
    """Welcher Weg gilt - gesetzt beim Laden der Einstellungen."""
    global _betriebsart
    _betriebsart = art if art in providers() else ARR


def betriebsart() -> str:
    return _betriebsart


def _weg() -> type[Beschaffung]:
    return providers()[_betriebsart]


def weckruf() -> asyncio.Event:
    """Das Signal, das den Rundgang vorzieht (ein Anruf des Wegs hat geweckt)."""
    return _weg().weckruf()


def bestand_verwerfen() -> None:
    """Zwischengespeicherten Bestand vergessen (nach geaenderten Zugaengen)."""
    _weg().bestand_verwerfen()


def rueckkanal_bald_pflegen() -> None:
    """Den Rueckkanal beim naechsten Rundgang pruefen statt erst zur vollen Stunde."""
    _weg().rueckkanal_bald_pflegen()


def gesundheit_je_instanz(db: Session) -> dict:
    """Was jede Instanz unter ``/health`` fuehrt, nach Kennung."""
    return _weg().gesundheit_je_instanz(db)


def haenger_je_instanz(db: Session) -> dict[str, int]:
    """Haengende Downloads je Instanz-Kennung."""
    return _weg().haenger_je_instanz(db)


def download_verlauf(zeile: Any, was: str, **kwargs: Any) -> Any:
    """Eine Zeile fuer den Verlauf eines haengenden Downloads (noch nicht gespeichert)."""
    return _weg().download_verlauf(zeile, was, **kwargs)


def download_verlauf_aufraeumen(db: Session) -> int:
    return _weg().download_verlauf_aufraeumen(db)


def download_gruende() -> dict[str, Any]:
    """Die Gruende, aus denen ein Download haengt, nach Kennung."""
    return _weg().download_gruende()


def download_aktionen_moeglich(zeile: Any) -> list[Aktion]:
    """Was sich an diesem haengenden Download tun laesst."""
    return _weg().download_aktionen_moeglich(zeile)


def nach_wiederherstellung() -> None:
    """Nach dem Einspielen einer Sicherung: gemerkte Staende aller Wege vergessen."""
    for weg in providers().values():
        weg.nach_wiederherstellung()


def download_frisch() -> Any:
    """Wie alt ein Download-Rundgang hoechstens sein darf, damit eine Seite ihn nimmt."""
    return _weg().download_frisch()
