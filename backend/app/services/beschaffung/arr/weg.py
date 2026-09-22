"""Beschaffung ueber Radarr und Sonarr.

``ArrBeschaffung`` ist die Schnittstelle, die der Rest der Anwendung sieht;
sie reicht an die Module in diesem Paket weiter, die vor Scheibe 2 des
NEX-Umbaus unter ``services/`` lagen und unveraendert umgezogen sind.

⚠️ **Die Methoden rufen die Module erst beim Aufruf auf** (``library.x``,
nicht ``from .library import x``). Tests ersetzen einzelne Funktionen dort
(``monkeypatch.setattr(library, "movie_library", ...)``); ein beim Laden
gebundener Name saehe den Ersatz nie.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from ..base import (
    Beschaffung,
    BeschaffungError,
    Faehigkeiten,
    FassungInfo,
    FilmStand,
    Korb,
    Nachschlag,
    Nachschlagen,
    SerienBestand,
    SerienStand,
    WarteschlangenEintrag,
    treffer_nach_titel,
)
from . import (
    auftraege,
    download_aktionen,
    download_gruende,
    download_haenger,
    fassungen,
    instanz_gesundheit,
    konten,
    library,
    portal_ratings,
    serien_zuordnung,
    speicher,
    stand,
    webhook_pflege,
)
from .radarr import RadarrClient
from .sonarr import SonarrClient
from .sonarr import staffel_daten as _staffel_daten

if TYPE_CHECKING:
    import asyncio

    from fastapi import APIRouter
    from sqlalchemy.orm import Session

    from ....models import DownloadHaenger, MediaRequest, StorageEntry, User
    from ....schemas_media import MediaItem
    from ...settings_service import ArrInstanz

__all__ = ["ArrBeschaffung"]


class ArrBeschaffung(Beschaffung):
    """Radarr fuer Filme, Sonarr fuer Serien, je bis zu zwei Instanzen (Stufen)."""

    art: ClassVar[str] = "arr"

    def faehigkeiten(self) -> Faehigkeiten:
        return Faehigkeiten(
            betreiberwerkzeuge=True,
            warum=False,
            vorschau=False,
            ereignisstrom=False,
            anime=True,
            # Radarr und Sonarr haben einen Ordner namens Papierkorb, aber keinen
            # Papierkorb im Sinne des Vertrags (lesen, zurueckholen).
            papierkorb=False,
            kalender=True,
            wertungen=("movie",),
        )

    # -- Fassungen ------------------------------------------------------------

    def fassungen(self) -> tuple[FassungInfo, ...]:
        """Die eingerichteten Fassungen (eine je Instanz), in Anzeigereihenfolge."""
        return fassungen.aus_einstellungen(self.settings)

    def fassungen_abgleichen(self, db: Session) -> None:
        fassungen.abgleichen(db, self.settings)

    @classmethod
    def feste_fassungen(cls) -> tuple[fassungen.ArrFassung, ...]:
        return fassungen.ARR_FASSUNGEN

    # -- Bestand --------------------------------------------------------------

    def _client(self, media_type: str, stufe: str) -> RadarrClient | SonarrClient | None:
        if media_type == "movie":
            return library.radarr_client(self.settings, stufe)
        return library.sonarr_client(self.settings, stufe)

    def instanzen(self) -> tuple[ArrInstanz, ...]:
        """Die eingerichteten Radarr- und Sonarr-Instanzen."""
        return self.settings.arr_instanzen()

    def verwaltet(self, media_type: str, stufe: str = "standard") -> bool:
        """Gibt es fuer diese Art und Stufe eine eingerichtete Instanz?"""
        return self._client(media_type, stufe) is not None

    async def bestand_filme(
        self, stufe: str = "standard", *, fassung: str = ""
    ) -> dict[int, FilmStand]:
        return await library.movie_library(self.settings, self._stufe(stufe, fassung))

    async def bestand_serien(
        self, stufe: str = "standard", *, fassung: str = ""
    ) -> SerienBestand:
        return await library.series_library(self.settings, self._stufe(stufe, fassung))

    @staticmethod
    def _stufe(stufe: str, fassung: str) -> str:
        """Die Fassung geht vor - sie meint dieselbe Instanz wie ihre Stufe."""
        from ...fassungen import stufe as stufe_der_fassung

        return stufe_der_fassung(fassung) if fassung else stufe

    @classmethod
    def bestand_verwerfen(cls) -> None:
        library.invalidate()

    async def nachschlagen(self, gesucht: list[Nachschlag]) -> Nachschlagen:
        """Den Stand vieler Titel auf einmal - aus den Bibliotheken je Fassung.

        ⚠️ **Dieselben Aufrufe wie bisher, an einer Stelle.** Der Takt-Laeufer
        holte die Bibliothek je Stufe selbst und suchte sich jeden Titel
        heraus; hier steht genau das, nur hinter der Grenze. Radarr und Sonarr
        bewegen sich dadurch nicht - die Bibliothek liegt weiter 60 s im
        Speicher, und der Rueckfall ueber den Titel gilt unveraendert (er ist
        noetig, weil TMDB fuer viele Serien keine TVDB-Kennung kennt).
        """
        # ⚠️ Der Import steht hier, nicht oben: ``services/fassungen`` fragt
        # beim Laden die Grenze nach den festen Fassungen, und der Name
        # ``fassungen`` gehoert in dieser Datei schon dem ARR-Modul.
        from ...fassungen import stufe as stufe_der_fassung

        treffer: dict[Nachschlag, FilmStand | SerienStand] = {}
        gelesen: set[tuple[str, str]] = set()
        filme: dict[str, dict[int, FilmStand]] = {}
        serien: dict[str, SerienBestand] = {}

        for wonach in gesucht:
            stufe = stufe_der_fassung(wonach.fassung)
            if not self.settings.arr_configured(wonach.media_type, stufe):
                continue
            if wonach.media_type == "movie":
                if stufe not in filme:
                    filme[stufe] = await library.movie_library(self.settings, stufe)
                gelesen.add((wonach.media_type, wonach.fassung))
                eintrag: FilmStand | SerienStand | None = filme[stufe].get(wonach.tmdb_id)
            else:
                if stufe not in serien:
                    serien[stufe] = await library.series_library(self.settings, stufe)
                gelesen.add((wonach.media_type, wonach.fassung))
                nach_tvdb, nach_titel = serien[stufe]
                eintrag = nach_tvdb.get(wonach.tvdb_id) if wonach.tvdb_id else None
                if eintrag is None:
                    eintrag = treffer_nach_titel(nach_titel, wonach.titel, wonach.jahr)
            if eintrag is not None:
                treffer[wonach] = eintrag
        return Nachschlagen(treffer=treffer, gelesen=frozenset(gelesen))

    async def status_setzen(
        self,
        media_type: str,
        items: list[MediaItem],
        stufe: str = "standard",
        *,
        fassung: str = "",
        mit_pfad: bool = False,
    ) -> library.MatchResult:
        """Die Fassung geht vor, wo eine genannt ist - sie meint dieselbe Instanz."""
        from ...fassungen import stufe as stufe_der_fassung

        gewaehlt = stufe_der_fassung(fassung) if fassung else stufe
        return await library.apply_status(
            self.settings, media_type, items, gewaehlt, mit_pfad=mit_pfad
        )

    async def folgen_verfuegbarkeit(
        self,
        tvdb_id: int | None,
        title: str,
        stufe: str = "standard",
        jahr: int | None = None,
    ) -> dict[int, set[int]]:
        return await library.episode_availability(self.settings, tvdb_id, title, stufe, jahr=jahr)

    async def serien_eintrag(
        self, tvdb_id: int | None, titel: str, jahr: int | None = None, stufe: str = "standard"
    ):
        return await library.serien_eintrag(self.settings, tvdb_id, titel, jahr, stufe)

    async def folgen_stand(self, stufe: str, arr_id: int) -> dict | None:
        """Folgen je Staffel und Nummer - ``None`` ohne eingerichtetes Sonarr."""
        client = library.sonarr_client(self.settings, stufe)
        return await client.folgen_stand(arr_id) if client is not None else None

    async def episodendateien(
        self, stufe: str, arr_id: int, season: int | None = None
    ) -> list[dict[str, Any]] | None:
        """Episodendateien einer Serie (``season=None``: alle Staffeln).

        ``None`` ohne eingerichtetes Sonarr.
        """
        client = library.sonarr_client(self.settings, stufe)
        if client is None:
            return None
        if season is None:
            return await client.get("/episodefile", {"seriesId": arr_id}) or []
        return await client.episode_files(arr_id, season)

    async def staffel_daten(self, stufe: str, arr_id: int):
        """Seit wann jede Staffel da liegt - ``None`` ohne eingerichtetes Sonarr."""
        client = library.sonarr_client(self.settings, stufe)
        return await _staffel_daten(client, arr_id) if client is not None else None

    async def warteschlange(self, media_type: str, stufe: str) -> list[WarteschlangenEintrag]:
        client = self._client(media_type, stufe)
        return await client.warteschlange() if client is not None else []

    def warteschlange_verdichten(
        self, media_type: str, roh: list[dict[str, Any]]
    ) -> list[WarteschlangenEintrag]:
        """Rohe Warteschlange (vom Download-Rundgang schon geholt) verdichten."""
        if media_type == "movie":
            return RadarrClient.eintraege_aus(roh)
        return SonarrClient.eintraege_aus(roh)

    # -- Ziele, Platz, Kalender, Wertungen ------------------------------------

    async def optionen(self, media_type: str, stufe: str = "standard") -> dict[str, Any]:
        return await library.options(self.settings, media_type, stufe)

    async def datentraeger(self, media_type: str, stufe: str = "standard") -> list[dict[str, Any]]:
        return await library.datentraeger(self.settings, media_type, stufe)

    async def papierkoerbe(self) -> list[tuple[str, str, str, library.Papierkorb]]:
        return await library.papierkoerbe(self.settings)

    async def papierkorb_groesse(self, media_type: str, stufe: str, pfad: str) -> tuple[int, bool]:
        return await library.papierkorb_groesse(self.settings, media_type, stufe, pfad)

    async def papierkorb(self) -> list[dict[str, Any]]:
        """Gibt es nicht: Radarr und Sonarr fuehren keine Liste, nur einen Ordner.

        ``faehigkeiten().papierkorb`` sagt es vorher; wer trotzdem fragt,
        bekommt eine ehrliche Antwort statt einer leeren Liste.
        """
        raise BeschaffungError(
            "Radarr und Sonarr führen keinen Papierkorb, aus dem sich etwas zurückholen ließe.",
            409,
            code="not_in_this_mode",
            korb=Korb.abgelehnt,
        )

    async def wiederherstellen(self, eintrag_id: int) -> None:
        await self.papierkorb()

    async def kalender(self, media_type: str, von: str, bis: str) -> list[dict[str, Any]]:
        if media_type == "movie":
            return await library.movie_calendar(self.settings, von, bis)
        return await library.series_calendar(self.settings, von, bis)

    async def wertungen_filme(self, tmdb_ids: list[int]) -> dict[int, portal_ratings.Ratings]:
        return await portal_ratings.for_movies(self.settings, tmdb_ids)

    def nicht_eingerichtet(self, media_type: str, stufe: str) -> str:
        """Der Satz, wenn fuer eine Stufe keine Instanz eingerichtet ist."""
        return auftraege.nicht_eingerichtet_text(media_type, stufe)

    # -- Auftraege ------------------------------------------------------------

    async def anfragen(self, db: Session, anfrage: MediaRequest) -> int | None:
        return await auftraege.anfragen(db, self.settings, anfrage)

    async def abbrechen(self, db: Session, anfrage: MediaRequest) -> str:
        return await auftraege.abbrechen(db, self.settings, anfrage)

    async def ueberwachung_heilen(self, db: Session, anfrage: MediaRequest, arr_id: int) -> None:
        await auftraege.heilen(db, self.settings, anfrage, arr_id)

    async def serie_zuordnen(self, tmdb_id: int, stufe: str, *titel: str):
        """TVDB-Kennung ueber Sonarrs Suche klaeren - ``None`` ohne Sonarr."""
        client = library.sonarr_client(self.settings, stufe)
        if client is None:
            return None
        return await serien_zuordnung.zuordnen(client, tmdb_id, *titel)

    def serien_wahl_erlaubt(self, zuordnung: Any, tvdb_id: int) -> bool:
        return serien_zuordnung.erlaubt(zuordnung, tvdb_id)

    # -- Speicherposten -------------------------------------------------------

    async def posten_kennung(self, zeile: StorageEntry) -> int | None:
        return await speicher.kennung(self.settings, zeile)

    async def posten_dateien(
        self, zeile: StorageEntry, arr_id: int, paket_folgen: speicher.PaketFolgen
    ) -> list[tuple[str, int]]:
        return await speicher.dateien(self.settings, zeile, arr_id, paket_folgen)

    async def posten_loeschen(
        self, zeile: StorageEntry, arr_id: int, paket_folgen: speicher.PaketFolgen
    ) -> None:
        await speicher.loeschen(self.settings, zeile, arr_id, paket_folgen)

    async def posten_stilllegen(self, zeile: StorageEntry) -> int | None:
        return await speicher.staffel_stilllegen(self.settings, zeile)

    # -- Konto aufloesen ------------------------------------------------------

    async def laufende_aufloesen(
        self, db: Session, laufend: Any, *, behalten: bool, weiter: bool
    ) -> bool:
        return await konten.laufende_aufloesen(
            db, self.settings, laufend, behalten=behalten, weiter=weiter
        )

    async def bestellung_zuruecknehmen(self, anfrage: MediaRequest) -> None:
        await konten.bestellung_zuruecknehmen(self.settings, anfrage)

    # -- Instanzen: Stand, Gesundheit, Rueckkanal ------------------------------

    async def instanz_messen(self, instanz: ArrInstanz, *, voll: bool) -> stand.Messung:
        return await stand.messen(self.settings, instanz, voll=voll)

    async def gesundheit_pruefen(self, db: Session) -> None:
        await instanz_gesundheit.pruefen(db, self.settings)

    async def rueckkanal_pflegen(self, db: Session) -> None:
        await webhook_pflege.vielleicht_pflegen(db, self.settings)

    # -- Haengende Downloads --------------------------------------------------

    async def downloads_auffrischen(
        self, db: Session, *, frisch_genug: timedelta | None = None
    ) -> download_haenger.Rundgang:
        return await download_haenger.auffrischen(db, self.settings, frisch_genug=frisch_genug)

    def download_anfragen(self, db: Session, zeile: DownloadHaenger) -> list[MediaRequest]:
        return download_haenger.anfragen_zu(db, self.settings, zeile)

    @classmethod
    def download_aktionen_moeglich(cls, zeile: DownloadHaenger) -> list:
        return download_aktionen.moegliche_aktionen(zeile)

    async def download_entfernen(
        self,
        db: Session,
        zeile_id: int,
        *,
        neu_suchen: bool,
        wer: User | None,
        automatisch: bool = False,
    ):
        return await download_aktionen.entfernen(
            db, self.settings, zeile_id, neu_suchen=neu_suchen, wer=wer, automatisch=automatisch
        )

    async def download_erneut_pruefen(
        self, db: Session, zeile_id: int, *, wer: User | None, automatisch: bool = False
    ):
        return await download_aktionen.erneut_pruefen(
            db, self.settings, zeile_id, wer=wer, automatisch=automatisch
        )

    async def download_kandidaten(self, db: Session, zeile_id: int):
        return await download_aktionen.import_kandidaten(db, self.settings, zeile_id)

    async def download_importieren(
        self, db: Session, zeile_id: int, pfade: list[str], *, trotzdem: bool, wer: User | None
    ):
        return await download_aktionen.importieren(
            db, self.settings, zeile_id, pfade, trotzdem=trotzdem, wer=wer
        )

    # -- Betrieb (ohne Einstellungen, je Weg) ----------------------------------

    @classmethod
    def router(cls) -> list[APIRouter]:
        from . import router_anruf, router_einstellungen, router_werkzeuge

        return [router_einstellungen.router, router_werkzeuge.router, router_anruf.router]

    @classmethod
    def beim_start(cls) -> None:
        """Abgebrochene Umbenennungslaeufe wieder aufnehmen.

        Ein Lauf ueber mehrere tausend Titel dauert lange; faellt der Prozess
        mittendrin aus, bliebe sonst eine halb umbenannte Bibliothek zurueck -
        teils altes, teils neues Schema, ohne erkennbare Grenze. Ohne diesen
        Aufruf waere der gespeicherte Zwischenstand wertlos.
        """
        from . import benennung

        start_log = logging.getLogger("nexview.qualitaet")
        try:
            aufgenommen = benennung.abgebrochene_aufnehmen()
            if aufgenommen:
                start_log.info("Picked up %d unfinished rename run(s)", aufgenommen)
        except Exception:  # noqa: BLE001 - der Start darf daran nicht scheitern
            start_log.exception("Could not pick up unfinished rename runs")

    @classmethod
    def hintergrundaufgaben(cls, stop: asyncio.Event) -> list:
        """Einmal am Tag nachsehen, ob es einen neueren TRaSH-Stand gibt.

        ⚠️ Nur nachsehen - geholt wird nie von selbst. Ein Stand, der sich
        ungefragt aendert, verschoebe stillschweigend die Profile in
        Radarr/Sonarr.
        """
        from . import trash_bezug

        return [trash_bezug.run_forever(stop)]

    @classmethod
    async def schliessen(cls) -> None:
        from .client import close_http_client

        await close_http_client()

    @classmethod
    def rueckkanal_bald_pflegen(cls) -> None:
        webhook_pflege.gleich_wieder()

    @classmethod
    def nach_wiederherstellung(cls) -> None:
        """Gemerkte Staende vergessen, die eine eingespielte Sicherung ueberholt.

        ⚠️ **Der TRaSH-Stand wird gemerkt.** Ohne das Leeren arbeitet der
        laufende Prozess bis zum Neustart mit dem Abzug von vor dem Einspielen
        weiter - und misst die gerade eingespielten Qualitaetsprofile gegen einen
        Stand, den sie nie gesehen haben.
        """
        from .trash import schnappschuss

        schnappschuss.cache_clear()

    @classmethod
    def weckruf(cls) -> asyncio.Event:
        from . import webhooks

        return webhooks.weckruf()

    @classmethod
    def gesundheit_je_instanz(cls, db: Session) -> dict:
        return instanz_gesundheit.alle(db)

    @classmethod
    def haenger_je_instanz(cls, db: Session) -> dict[str, int]:
        return download_haenger.zaehlen(db)

    @classmethod
    def download_verlauf_aufraeumen(cls, db: Session) -> int:
        return download_haenger.verlauf_aufraeumen(db)

    @classmethod
    def download_verlauf(cls, zeile: DownloadHaenger, was: str, **kwargs: Any):
        return download_haenger.verlauf(zeile, was, **kwargs)

    @classmethod
    def download_gruende(cls) -> dict[str, download_gruende.Grund]:
        return download_gruende.GRUENDE

    @classmethod
    def download_frisch(cls) -> timedelta:
        """Wie alt ein Rundgang hoechstens sein darf, damit die Seite ihn nimmt."""
        return download_haenger.FRISCH
