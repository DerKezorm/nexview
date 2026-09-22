"""Beschaffung ueber nexcrate.

``NexBeschaffung`` ist dieselbe Schnittstelle wie ``ArrBeschaffung``, nur
fuehrt sie zu nexcrate statt zu Radarr und Sonarr. Der Weg kennt eine
Installation (``nexcrate_url``, ``nexcrate_api_key``), nicht vier Instanzen.

⚠️ **Scheibe 4 baut den Client, nicht die Wege.** Was hier ``_spaeter``
aufruft, kommt mit Scheibe 5 (lesen) und Scheibe 6 (schreiben). Es wirft
einen ehrlichen Fehler mit Kennung, statt still nichts zu tun - und ruft in
keinem Fall Radarr oder Sonarr (``tests/test_nex_ohne_arr.py``).

⚠️ **Nexview legt in nexcrate keinen Webhook an** (N32 ist fuer andere
Verbraucher). Der Rueckkanal ist der Ereignisstrom, den Nexview selbst
aufmacht; ``rueckkanal_pflegen`` hat hier nichts zu tun.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn

from ..base import (
    Aktion,
    Beschaffung,
    BeschaffungError,
    Faehigkeiten,
    FassungInfo,
    FilmStand,
    Korb,
    SerienBestand,
    WarteschlangenEintrag,
)
from . import fassungen, fehler, system
from .client import NexcrateClient

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from fastapi import APIRouter
    from sqlalchemy.orm import Session

    from ....models import MediaRequest, StorageEntry, User
    from ....schemas_media import MediaItem
    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")

__all__ = ["NexBeschaffung", "client_fuer"]

#: Der Name, unter dem Nexview sich bei nexcrate koppelt und in Listen steht.
APP_NAME = "Nexview"

#: Der Wecker des Rundgangs: Ein Ereignis aus nexcrates Strom zieht ihn vor.
_weckruf: asyncio.Event | None = None

#: Wie alt ein Download-Rundgang hoechstens sein darf, damit eine Seite ihn
#: nimmt. Wie im ARR-Betrieb - die Zahl haengt an Nexviews Seiten, nicht am Weg.
FRISCH = timedelta(seconds=90)


def client_fuer(settings: AppSettings) -> NexcrateClient:
    """Der Client dieser Installation; wirft, wenn nichts hinterlegt ist."""
    if not settings.nexcrate_configured:
        raise fehler.nicht_eingerichtet()
    return NexcrateClient(settings.nexcrate_url, settings.nexcrate_api_key)


def _spaeter(was: str) -> NoReturn:
    """Ein Weg, den erst eine spaetere Scheibe baut."""
    raise BeschaffungError(
        f"Im NEX-Betrieb gibt es „{was}“ noch nicht.",
        code="nex_noch_nicht_gebaut",
        korb=Korb.abgelehnt,
        weg=was,
    )


def _gibt_es_nicht(was: str) -> NoReturn:
    """Ein Werkzeug des ARR-Betriebs, das es im NEX-Betrieb nicht gibt."""
    raise BeschaffungError(
        f"„{was}“ gehört im NEX-Betrieb nexcrate.",
        409,
        code="not_in_this_mode",
        korb=Korb.abgelehnt,
        weg=was,
    )


class NexBeschaffung(Beschaffung):
    """Eine nexcrate-Installation fuer Filme und Serien, spaeter auch Musik."""

    art: ClassVar[str] = "nex"

    def faehigkeiten(self) -> Faehigkeiten:
        return system.faehigkeiten()

    @property
    def client(self) -> NexcrateClient:
        return client_fuer(self.settings)

    # -- Fassungen ------------------------------------------------------------

    def fassungen(self) -> tuple[FassungInfo, ...]:
        return fassungen.aus_einstellungen(self.settings)

    def fassungen_abgleichen(self, db: Session) -> None:
        fassungen.abgleichen(db, self.settings)

    @classmethod
    def feste_fassungen(cls) -> tuple[Any, ...]:
        """Keine. Die Fassungen stehen bei nexcrate, nicht in den Einstellungen."""
        return ()

    # -- Bestand --------------------------------------------------------------

    def verwaltet(self, media_type: str, stufe: str = "standard") -> bool:
        """Gibt es eine eingerichtete Fassung dieser Art?

        Die Stufe ist im NEX-Betrieb ohne Bedeutung; gefragt wird, ob nexcrate
        fuer diese Medienart ueberhaupt etwas fuehrt.
        """
        art = getattr(media_type, "value", media_type)
        return bool(self.settings.nexcrate_configured) and any(
            f.media_type == art for f in self.fassungen()
        )

    def nicht_eingerichtet(self, media_type: str, stufe: str) -> str:
        if not self.settings.nexcrate_configured:
            return "Für nexcrate sind Adresse und Schlüssel noch nicht hinterlegt."
        return "In nexcrate ist für diese Medienart keine Fassung eingerichtet."

    async def bestand_filme(self, stufe: str = "standard") -> dict[int, FilmStand]:
        _spaeter("Bestand der Filme")

    async def bestand_serien(self, stufe: str = "standard") -> SerienBestand:
        _spaeter("Bestand der Serien")

    @classmethod
    def bestand_verwerfen(cls) -> None:
        """Nichts zu verwerfen: Der Bestand kommt ueber die Marke (Scheibe 5)."""
        return

    async def status_setzen(
        self,
        media_type: str,
        items: list[MediaItem],
        stufe: str = "standard",
        *,
        mit_pfad: bool = False,
    ) -> Any:
        _spaeter("Zustand an den Kacheln")

    async def folgen_verfuegbarkeit(
        self, tvdb_id: int | None, title: str, stufe: str = "standard", jahr: int | None = None
    ) -> dict[int, set[int]]:
        _spaeter("Folgen einer Serie")

    async def serien_eintrag(
        self, tvdb_id: int | None, titel: str, jahr: int | None = None, stufe: str = "standard"
    ):
        _spaeter("Eintrag einer Serie")

    async def folgen_stand(self, stufe: str, arr_id: int):
        _spaeter("Stand der Folgen")

    async def episodendateien(self, stufe: str, arr_id: int, season: int | None = None):
        _spaeter("Dateien der Folgen")

    async def staffel_daten(self, stufe: str, arr_id: int):
        _spaeter("Termine der Staffeln")

    async def warteschlange(self, media_type: str, stufe: str) -> list[WarteschlangenEintrag]:
        _spaeter("Warteschlange")

    def warteschlange_verdichten(
        self, media_type: str, roh: list[dict[str, Any]]
    ) -> list[WarteschlangenEintrag]:
        """nexcrate liefert je Download **eine** Zeile (N26) - nichts zu falten."""
        return []

    # -- Ziele, Platz, Kalender, Wertungen ------------------------------------

    async def optionen(self, media_type: str, stufe: str = "standard") -> dict[str, Any]:
        """Es gibt keine Wahl: Ordner und Profil haengen in nexcrate an der Fassung."""
        _gibt_es_nicht("Zielordner und Qualitätsprofile")

    async def datentraeger(self, media_type: str, stufe: str = "standard") -> list[dict[str, Any]]:
        _spaeter("Freier Platz")

    async def papierkoerbe(self) -> list[tuple[str, str, str, Any]]:
        _spaeter("Papierkorb")

    async def papierkorb_groesse(self, media_type: str, stufe: str, pfad: str) -> tuple[int, bool]:
        _spaeter("Größe des Papierkorbs")

    async def kalender(self, media_type: str, von: str, bis: str) -> list[dict[str, Any]]:
        _spaeter("Kalender")

    async def wertungen_filme(self, tmdb_ids: list[int]) -> dict[int, Any]:
        _spaeter("Wertungen")

    # -- Auftraege ------------------------------------------------------------

    async def anfragen(self, db: Session, anfrage: MediaRequest) -> int | None:
        _spaeter("Anfragen")

    async def abbrechen(self, db: Session, anfrage: MediaRequest) -> str:
        _spaeter("Zurücknehmen")

    async def ueberwachung_heilen(self, db: Session, anfrage: MediaRequest, arr_id: int) -> None:
        """Entfaellt: ``monitored`` ist im Vertrag verbindlich (Bauplan 6.1).

        Sonarr raeumt die Ueberwachung asynchron ab, deshalb heilt Nexview dort
        nach. nexcrate tut das nicht; eine Heilung wuerde hier nur Suchen
        ausloesen, die nexcrate selbst einreiht.
        """
        return

    async def serie_zuordnen(self, tmdb_id: int, stufe: str, *titel: str) -> Any:
        """Entfaellt: nexcrate ankert auf TMDB, die TVDB-Klaerung faellt weg (N15)."""
        return None

    def serien_wahl_erlaubt(self, zuordnung: Any, tvdb_id: int) -> bool:
        return False

    # -- Speicherposten -------------------------------------------------------

    async def posten_kennung(self, zeile: StorageEntry) -> int | None:
        _spaeter("Kennung eines Postens")

    async def posten_dateien(
        self, zeile: StorageEntry, arr_id: int, paket_folgen: Callable[[], list[int] | None]
    ) -> list[tuple[str, int]]:
        _spaeter("Dateien eines Postens")

    async def posten_loeschen(
        self, zeile: StorageEntry, arr_id: int, paket_folgen: Callable[[], list[int] | None]
    ) -> None:
        _spaeter("Löschen eines Postens")

    async def posten_stilllegen(self, zeile: StorageEntry) -> int | None:
        _spaeter("Stilllegen eines Postens")

    # -- Konto aufloesen ------------------------------------------------------

    async def laufende_aufloesen(
        self, db: Session, laufend: Any, *, behalten: bool, weiter: bool
    ) -> bool:
        _spaeter("Konto auflösen")

    async def bestellung_zuruecknehmen(self, anfrage: MediaRequest) -> None:
        _spaeter("Offene Bestellung zurücknehmen")

    # -- Instanzen: Stand, Gesundheit, Rueckkanal ------------------------------

    async def instanz_messen(self, instanz: Any, *, voll: bool) -> Any:
        _spaeter("Stand der Instanz")

    async def gesundheit_pruefen(self, db: Session) -> None:
        _spaeter("Gesundheit")

    async def rueckkanal_pflegen(self, db: Session) -> None:
        """Nichts zu tun: Nexview legt in nexcrate keinen Webhook an (N32)."""
        return

    # -- Haengende Downloads --------------------------------------------------

    async def downloads_auffrischen(
        self, db: Session, *, frisch_genug: timedelta | None = None
    ) -> Any:
        _spaeter("Hängende Downloads")

    def download_anfragen(self, db: Session, zeile: Any) -> list[MediaRequest]:
        _spaeter("Anfragen zu einem Download")

    async def download_entfernen(
        self,
        db: Session,
        zeile_id: int,
        *,
        neu_suchen: bool,
        wer: User | None,
        automatisch: bool = False,
    ) -> Any:
        _spaeter("Download entfernen")

    async def download_erneut_pruefen(
        self, db: Session, zeile_id: int, *, wer: User | None, automatisch: bool = False
    ) -> Any:
        _spaeter("Download erneut prüfen")

    async def download_kandidaten(self, db: Session, zeile_id: int) -> list[Any]:
        _spaeter("Dateien eines Downloads")

    async def download_importieren(
        self, db: Session, zeile_id: int, pfade: list[str], *, trotzdem: bool, wer: User | None
    ) -> Any:
        _spaeter("Von Hand zuordnen")

    # -- Betrieb (ohne Einstellungen) -----------------------------------------

    @classmethod
    def router(cls) -> list[APIRouter]:
        from . import router_einstellungen

        return [router_einstellungen.router]

    @classmethod
    def beim_start(cls) -> None:
        """Nichts aufzunehmen: nexcrate fuehrt seine Laeufe selbst zu Ende."""
        return

    @classmethod
    def hintergrundaufgaben(cls, stop: asyncio.Event) -> list[Coroutine[Any, Any, None]]:
        """Der Ereignisstrom kommt mit Scheibe 5."""
        return []

    @classmethod
    async def schliessen(cls) -> None:
        from .client import close_http_client

        await close_http_client()

    @classmethod
    def rueckkanal_bald_pflegen(cls) -> None:
        """Nichts zu pflegen - siehe ``rueckkanal_pflegen``."""
        return

    @classmethod
    def nach_wiederherstellung(cls) -> None:
        """Gemerkten Stand vergessen: Die Sicherung kann eine andere nexcrate meinen."""
        system.vergessen()
        fassungen.vergessen()

    @classmethod
    def weckruf(cls) -> asyncio.Event:
        global _weckruf
        if _weckruf is None:
            _weckruf = asyncio.Event()
        return _weckruf

    @classmethod
    def gesundheit_je_instanz(cls, db: Session) -> dict[str, Any]:
        """Kommt mit Scheibe 5 aus ``GET /health``."""
        return {}

    @classmethod
    def haenger_je_instanz(cls, db: Session) -> dict[str, int]:
        """Kommt mit Scheibe 5 aus ``GET /problems``."""
        return {}

    @classmethod
    def download_aktionen_moeglich(cls, zeile: Any) -> list[Aktion]:
        """Im NEX-Betrieb entscheidet nexcrates ``actions`` je Problem (Scheibe 5)."""
        return []

    @classmethod
    def download_verlauf_aufraeumen(cls, db: Session) -> int:
        from ..arr import download_haenger

        return download_haenger.verlauf_aufraeumen(db)

    @classmethod
    def download_verlauf(cls, zeile: Any, was: str, **kwargs: Any) -> Any:
        from ..arr import download_haenger

        return download_haenger.verlauf(zeile, was, **kwargs)

    @classmethod
    def download_gruende(cls) -> dict[str, Any]:
        """Im NEX-Betrieb tragen die Probleme ihre Kennung selbst (Scheibe 5)."""
        return {}

    @classmethod
    def download_frisch(cls) -> timedelta:
        return FRISCH
