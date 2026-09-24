"""Beschaffung ueber nexcrate.

``NexBeschaffung`` ist dieselbe Schnittstelle wie ``ArrBeschaffung``, nur
fuehrt sie zu nexcrate statt zu Radarr und Sonarr. Der Weg kennt eine
Installation (``nexcrate_url``, ``nexcrate_api_key``), nicht vier Instanzen.

⚠️ **Was es hier nicht gibt, sagt es beim Namen.** Die Betreiberwerkzeuge
fuer Arr (Profile, Benennung, Zielordner, Papierkorb-Ordner) gehoeren im
NEX-Betrieb nexcrate; sie antworten ``409 not_in_this_mode`` statt still eine
leere Liste zu liefern. Und in keinem Fall ruft dieser Weg Radarr oder Sonarr
(``tests/test_nex_ohne_arr.py``).

⚠️ **Nexview legt in nexcrate keinen Webhook an** (N32 ist fuer andere
Verbraucher). Der Rueckkanal ist der Ereignisstrom, den Nexview selbst
aufmacht; ``rueckkanal_pflegen`` hat hier nichts zu tun.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn

from ....models import MediaRequest, utcnow
from ..base import (
    Abschied,
    Aktion,
    Beschaffung,
    BeschaffungError,
    Faehigkeiten,
    FassungInfo,
    FilmStand,
    Folge,
    Kennt,
    Kenntnis,
    Korb,
    Nachschlag,
    Nachschlagen,
    Pruefbefund,
    SerienBestand,
    SerienStand,
    Sprung,
    WarteschlangenEintrag,
    Warum,
)
from . import (
    aktionen,
    auftraege,
    bestand,
    downloads,
    fassungen,
    fehler,
    gesundheit,
    lesen,
    mapping,
    speicher,
    system,
)
from .client import NexcrateClient

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from fastapi import APIRouter
    from sqlalchemy.orm import Session

    from ....models import StorageEntry, User
    from ....schemas_media import MediaItem
    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")

__all__ = ["NexBeschaffung", "client_fuer"]

#: Der Name, unter dem Nexview sich bei nexcrate koppelt und in Listen steht.
APP_NAME = "Nexview"

#: Die Kennung der einen Instanz. An ihr haengt gemerkter Zustand
#: (Erreichbarkeit, Gesundheit, haengende Downloads); sie darf sich nie aendern.
INSTANZ = "nexcrate"
#: Ihr Anzeigename, solange der Betreiber keinen eigenen gesetzt hat.
APP_QUELLE = "nexcrate"

#: Der Wecker des Rundgangs: Ein Ereignis aus nexcrates Strom zieht ihn vor.
_weckruf: asyncio.Event | None = None

#: Wie alt ein Download-Rundgang hoechstens sein darf, damit eine Seite ihn
#: nimmt. Wie im ARR-Betrieb - die Zahl haengt an Nexviews Seiten, nicht am Weg.
FRISCH = timedelta(seconds=90)

#: Hoechstens so viele Titel fragt ``wertungen_filme(einzeln=True)`` einzeln.
#: Jeder Aufruf kostet nexcrate eine OMDb-Abfrage aus einem Tageskontingent;
#: die Titelseite braucht einen, der Rest geht in den Stapel.
WERTUNGEN_EINZELN = 5


def client_fuer(settings: AppSettings) -> NexcrateClient:
    """Der Client dieser Installation; wirft, wenn nichts hinterlegt ist."""
    if not settings.nexcrate_configured:
        raise fehler.nicht_eingerichtet()
    return NexcrateClient(settings.nexcrate_url, settings.nexcrate_api_key)


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

    def instanzen(self) -> tuple[Any, ...]:
        """Genau eine: die nexcrate dieser Installation.

        Sie heisst nach der Einstellung ``nexcrate_name``, sonst nach dem
        Programm. Die Kennung ist fest - an ihr haengt der gemerkte Stand.
        """
        from ...settings_service import ArrInstanz

        if not self.settings.nexcrate_configured:
            return ()
        return (
            ArrInstanz(
                kennung=INSTANZ,
                media_type="",
                tier="",
                name=self.settings.nexcrate_name or APP_QUELLE,
                url=self.settings.nexcrate_url,
                api_key=self.settings.nexcrate_api_key,
            ),
        )

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
        return auftraege.nicht_eingerichtet_text(media_type, stufe)

    async def bestand_filme(
        self, stufe: str = "standard", *, fassung: str = ""
    ) -> dict[int, FilmStand]:
        """Alle Filme einer Fassung - ueber die Marke, nicht ganz (N13).

        ⚠️ **Die Stufe wird nicht gelesen.** Es gibt sie im NEX-Betrieb nicht;
        ohne genannte Fassung gilt die Hauptfassung der Medienart.
        """
        kennung = self._gewaehlt("movie", fassung)
        if kennung is None:
            return {}
        await bestand.auffrischen(self.settings, "movie")
        return lesen.bestand_filme(kennung)

    async def bestand_serien(
        self, stufe: str = "standard", *, fassung: str = ""
    ) -> SerienBestand:
        """Alle Serien einer Fassung, samt Staffeln.

        Seit nexcrate ``39dfc05`` nennt die Liste ueber die Marke die Staffeln
        selbst, und keine Serie kostet einen eigenen Aufruf.

        ⚠️ **Bei einer aelteren nexcrate kosten sie einen Aufruf je Serie mit
        Datei**: Dort nennt nur die Einzelansicht sie. Gelesen wird nur, was
        sich seit dem letzten Mal geaendert hat; nach einem Neustart oder einem
        Wechsel der Installation alles.
        """
        kennung = self._gewaehlt("tv", fassung)
        if kennung is None:
            return ({}, {})
        await bestand.auffrischen(self.settings, "series")
        await bestand.staffeln_lesen(self.settings)
        return lesen.bestand_serien(kennung)

    async def alle_serien(
        self, stufe: str = "standard", *, fassung: str = ""
    ) -> list[tuple[int | None, SerienStand]]:
        """Jede Serie der Fassung, auch ohne ``tvdb:`` in ``refs``.

        ⚠️ Der Anker ist TMDB (``arr_id`` des Stands); ueber den TVDB-Index
        fehlte eine solche Serie dem Speicher-Abgleich, und ihre Posten wurden
        abgeraeumt, samt Besitzer.
        """
        kennung = self._gewaehlt("tv", fassung)
        if kennung is None:
            return []
        await bestand.auffrischen(self.settings, "series")
        await bestand.staffeln_lesen(self.settings)
        return lesen.alle_serien(kennung)

    def _gewaehlt(self, media_type: str, fassung: str) -> str | None:
        """Die genannte Fassung, wenn es sie gibt - sonst die Hauptfassung."""
        bekannt = {eintrag.kennung for eintrag in self.fassungen()}
        return fassung if fassung in bekannt else self._hauptfassung(media_type)

    def _hauptfassung(self, media_type: str) -> str | None:
        """Die erste eingerichtete Fassung einer Medienart, in Anzeigereihenfolge."""
        for eintrag in self.fassungen():
            if eintrag.media_type == media_type:
                return eintrag.kennung
        return None

    async def nachschlagen(self, gesucht: list[Nachschlag]) -> Nachschlagen:
        """Den Stand vieler Titel auf einmal - ``POST /titles/lookup`` (N12).

        ⚠️ **Nicht ueber die Marke.** Die hinkt der Anfrage bis zu zehn
        Sekunden hinterher, ``lookup`` kennt einen frisch entstandenen Titel
        sofort (nexbeat-Befund 12). Fuer „ist meine Anfrage angekommen" gibt es
        deshalb nur diesen Weg.

        Wer Staffeln braucht (``mit_staffeln``), bekommt sie seit nexcrate
        ``39dfc05`` aus ``lookup`` selbst, ohne weiteren Aufruf.

        ⚠️ **Eine aeltere nexcrate nennt dort keine Staffeln**
        (``series.seasons`` ist ``null``, gemessen). Dann kommen sie aus der
        Einzelansicht - ein Aufruf je Serie mit Datei, gemerkt je Marke und
        geteilt mit dem Speicher-Abgleich. Scheitert sie, steht die Frage unter
        ``ungelesen``: nicht geantwortet, nicht "Staffel weg".
        """
        treffer: dict[Nachschlag, FilmStand | SerienStand] = {}
        if not gesucht:
            return Nachschlagen(treffer={}, gelesen=frozenset())
        bekannt = {eintrag.kennung for eintrag in self.fassungen()}
        offen = [w for w in gesucht if w.fassung in bekannt]
        if not offen:
            return Nachschlagen(treffer={}, gelesen=frozenset())
        gefragt = sorted({(mapping.kind(w.media_type), mapping.ref(w.tmdb_id)) for w in offen})
        antworten = await self.client.lookup(
            [{"kind": kind, "ref": ref} for kind, ref in gefragt]
        )
        nach_ref = {
            (str(a.get("kind")), str(a.get("ref"))): a.get("title")
            for a in antworten
            if a.get("known") and a.get("title")
        }
        mit_staffeln: dict[str, dict[str, Any]] = {}
        for wonach in offen:
            if wonach.mit_staffeln and wonach.media_type == "tv":
                ref = mapping.ref(wonach.tmdb_id)
                titel = nach_ref.get(("series", ref))
                if titel is not None:
                    mit_staffeln[ref] = titel
        staffeln, nicht_gelesen = await bestand.gehalten().staffeln_zu(
            self.client, mit_staffeln, system.installation_id()
        )
        ungelesen: set[Nachschlag] = set()
        for wonach in offen:
            ref = mapping.ref(wonach.tmdb_id)
            titel = nach_ref.get((mapping.kind(wonach.media_type), ref))
            if titel is None:
                continue
            if wonach.mit_staffeln and ref in staffeln:
                titel = {**titel, "series": {**(titel.get("series") or {}), "seasons": staffeln[ref]}}
            stand = bestand.stand(titel, wonach.fassung)
            if wonach.mit_staffeln and ref in nicht_gelesen:
                ungelesen.add(wonach)
                if isinstance(stand, SerienStand):
                    stand = replace(stand, staffeln_gelesen=False)
            if stand is not None:
                treffer[wonach] = stand
        # Geantwortet hat nexcrate fuer jede Fassung, die es gibt - ein Titel,
        # der fehlt, ist wirklich weg und nicht nur ungefragt.
        gelesen = frozenset((w.media_type, w.fassung) for w in offen)
        return Nachschlagen(treffer=treffer, gelesen=gelesen, ungelesen=frozenset(ungelesen))

    @classmethod
    def bestand_verwerfen(cls) -> None:
        bestand.verwerfen()

    async def kennt(self, gesucht: list[Kennt]) -> list[Kenntnis]:
        """Fuehrt nexcrate diese Titel - und unter welchen Kennungen?

        ⚠️ **Serien werden notfalls ueber ``tvdb:`` gefragt.** nexcrate nimmt
        beide Quellen und nennt in ``refs``, was es selbst fuehrt; genau daraus
        entsteht die Uebersetzung der Speicherschluessel beim Umstieg. Wer nur
        ueber TMDB fragt, findet eine Serie nicht, die aus Sonarr uebernommen
        wurde und dort keine TMDB-Nummer hatte.
        """
        if not gesucht:
            return []
        antworten = await self.client.lookup(
            [
                {"kind": mapping.kind(wonach.media_type), "ref": mapping.ref(wonach.tmdb_id)}
                for wonach in gesucht
            ]
        )
        nachfrage = [
            (nummer, {"kind": "series", "ref": f"tvdb:{wonach.tvdb_id}"})
            for nummer, (wonach, antwort) in enumerate(zip(gesucht, antworten, strict=False))
            if not antwort.get("known") and wonach.media_type == "tv" and wonach.tvdb_id
        ]
        if nachfrage:
            zweite = await self.client.lookup([eintrag for _, eintrag in nachfrage])
            for (nummer, _), antwort in zip(nachfrage, zweite, strict=False):
                if antwort.get("known"):
                    antworten[nummer] = antwort

        gefunden: list[Kenntnis] = []
        for wonach, antwort in zip(gesucht, antworten, strict=False):
            titel = antwort.get("title") if antwort.get("known") else None
            if titel is None:
                gefunden.append(Kenntnis(bekannt=False, tvdb_id=wonach.tvdb_id))
                continue
            kennungen = mapping.refs_nach_quelle(titel.get("refs"))
            tmdb = kennungen.get("tmdb")
            tvdb = kennungen.get("tvdb")
            serie = titel.get("series")
            gefunden.append(
                Kenntnis(
                    bekannt=True,
                    anime=(
                        str(serie.get("type") or "") == "anime"
                        if isinstance(serie, dict)
                        else False
                    ),
                    fassungen=tuple(
                        str(f.get("version_id")) for f in titel.get("versions") or []
                    ),
                    tmdb_id=int(tmdb) if tmdb and tmdb.isdigit() else None,
                    tvdb_id=int(tvdb) if tvdb and tvdb.isdigit() else wonach.tvdb_id,
                )
            )
        return gefunden

    async def pruefen(self) -> list[Pruefbefund]:
        """Taugt diese nexcrate? Die Antwort kommt frisch, nicht aus dem Merker."""
        from . import pruefung

        daten = await system.auffrischen(self.settings)
        return pruefung.pruefen(daten)

    async def status_setzen(
        self,
        media_type: str,
        items: list[MediaItem],
        stufe: str = "standard",
        *,
        fassung: str = "",
        mit_pfad: bool = False,
    ) -> Any:
        """Kacheln mit dem Stand einer Fassung versehen.

        ⚠️ **Die Stufe wird nicht gelesen.** Im NEX-Betrieb gibt es sie nicht;
        wer eine bestimmte Fassung meint, nennt ihre Kennung. Ohne Angabe gilt
        die Hauptfassung der Medienart.

        ``mit_pfad`` bleibt ohne Wirkung: nexcrate nennt keinen Dateipfad, und
        die Grenze kennt keinen (Bauplan 6.4).
        """
        from ..arr.library import MatchResult

        kennung = self._gewaehlt(media_type, fassung)
        if kennung is None or not items:
            return MatchResult(items=items)
        try:
            gefaerbt = await lesen.kacheln_faerben(self.client, media_type, items, kennung)
        except BeschaffungError as error:
            return MatchResult(items=items, warning=error.message)
        return MatchResult(items=gefaerbt)

    async def folgen_verfuegbarkeit(
        self, tvdb_id: int | None, title: str, stufe: str = "standard", jahr: int | None = None
    ) -> dict[int, set[int]]:
        """Welche Folgen je Staffel schon vorliegen.

        ⚠️ **Die TVDB-Kennung ist hier kein Anker.** nexcrate ankert auf TMDB
        (N15); dieser Weg gibt es nur, weil das Anfrageformular ihn heute so
        ruft. Ohne TMDB-Nummer gibt es keine Auskunft - geraten wird nicht.
        """
        return {}

    async def serien_eintrag(
        self, tvdb_id: int | None, titel: str, jahr: int | None = None, stufe: str = "standard"
    ):
        """Wie oben: ohne TMDB-Nummer keine Auskunft."""
        return

    async def folgen_stand(self, stufe: str, arr_id: int, *, fassung: str = ""):
        """Die Folgen je Staffel und Nummer - eine Abfrage je Staffel.

        ⚠️ **Teurer als bei Sonarr**, das alle Folgen einer Serie in einem
        Aufruf liefert. Gefragt wird deshalb nur fuer Serien, zu denen ein
        Folgen-Paket laeuft - wie im ARR-Betrieb auch.

        ⚠️ **In der Fassung der Anfrage**, nicht in der Hauptfassung: Sonst
        galt ein fertiges 4K-Paket als geloescht, und ein suchendes wurde
        fertig, sobald seine Folgen in HD lagen.
        """
        kennung = self._gewaehlt("tv", fassung)
        if kennung is None:
            return None
        ref = mapping.ref(arr_id)
        titel = await self.client.title("series", ref)
        if titel is None:
            return None
        gefunden: dict[int, dict[int, Folge]] = {}
        for staffel in ((titel.get("series") or {}).get("seasons") or []):
            nummer = staffel.get("season")
            if nummer is None:
                continue
            antwort = await self.client.season(ref, int(nummer))
            if antwort is None:
                # Die Einzelansicht nennt die Staffel, ihre Ansicht kennt sie
                # nicht: ein Widerspruch, kein "Staffel fehlt". Eine fehlende
                # Staffel machte ein fertiges Paket zu "geloescht".
                return None
            gefunden[int(nummer)] = bestand.folgen(antwort, kennung)
        return gefunden

    async def episodendateien(self, stufe: str, arr_id: int, season: int | None = None):
        """Gibt es hier nicht als eigenen Aufruf.

        Die Dateien je Folge stehen schon in der Staffelansicht, die
        ``folgen_stand`` ohnehin liest, und zwar in der Fassung der Anfrage
        (``Folge.dateien``); ein zweiter Aufruf je Staffel waere doppelt und
        kennte die Fassung nicht. Geloescht wird ueber ``withdraw`` mit
        Umfang, nicht ueber einzelne Dateien.
        """
        return

    async def staffel_daten(self, stufe: str, arr_id: int):
        """Seit wann eine Staffel daliegt - hier nicht gefragt, sondern mitgelesen.

        Seit nexcrate ``39dfc05`` steht es als ``imported_at`` an jeder Staffel
        je Fassung und kommt mit dem Bestand an (``bestand.serien_stand``);
        ``storage`` fragt diesen Weg deshalb gar nicht. Wo nexcrate es selbst
        nicht weiss, bleibt das Alter unbekannt, und der Aufraeum-Vorschlag
        uebergeht den Posten, statt es zu raten.
        """
        return

    async def warteschlange(self, media_type: str, stufe: str) -> list[WarteschlangenEintrag]:
        if not self.settings.nexcrate_configured:
            return []
        roh = await self.client.queue(mapping.kind(media_type))
        return lesen.warteschlange(roh, media_type)

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
        return lesen.datentraeger(await self.client.storage())

    async def papierkoerbe(self) -> list[tuple[str, str, str, Any]]:
        """Gibt es nicht: Arrs Papierkorb ist ein **Ordner**, nexcrates eine Liste.

        Den Inhalt liest ``papierkorb()``; die Seite dazu kommt mit Scheibe 8.
        """
        _gibt_es_nicht("Papierkorb-Ordner")

    async def papierkorb_groesse(self, media_type: str, stufe: str, pfad: str) -> tuple[int, bool]:
        _gibt_es_nicht("Papierkorb-Ordner")

    async def warum(self, gefragt: list[Kennt]) -> list[Warum]:
        """``POST /titles/why`` im Stapel, Antwort in derselben Reihenfolge.

        ⚠️ **Gelesen wird ``versions[].because``, nie ``next_search_reason``**
        (nexbeat-Befund 7): Der Titelgrund stand auf ``nothing_wanted``,
        waehrend eine Fassung ``wanted`` war.
        """
        if not gefragt:
            return []
        antworten = await self.client.why(
            [
                {"kind": mapping.kind(wonach.media_type), "ref": mapping.ref(wonach.tmdb_id)}
                for wonach in gefragt
            ]
        )
        return [lesen.warum(antwort) for antwort in antworten]

    def spruenge(self) -> Sprung:
        """Die Vorlagen aus ``/system.links``, gefuellt - soweit es sie gibt.

        ⚠️ **Ohne eingetragene Adresse nach aussen gibt es keinen Sprung.**
        ``web_url`` ist ``null``, solange in nexcrate nichts steht (gemessen);
        die eigene Adresse aus Nexviews Sicht einzusetzen fuehrte einen
        Besucher von draussen ins Leere.
        """
        aussen = system.web_url().rstrip("/")
        if not aussen:
            return Sprung()
        vorlagen = system.links()

        def fertig(name: str) -> str:
            roh = vorlagen.get(name, "")
            # Vorlagen mit Platzhaltern bleiben Vorlagen; die Oberflaeche setzt
            # Kennung und Fassung selbst ein.
            return f"{aussen}{roh}" if roh else ""

        return Sprung(
            titel=fertig("title"),
            fassung=fertig("version"),
            probleme=fertig("problems"),
            papierkorb=fertig("recycle_bin"),
            kalender=fertig("calendar"),
        )

    async def papierkorb(self) -> list[dict[str, Any]]:
        """Was in nexcrates Papierkorb liegt (N22)."""
        return await self.client.recycle_bin()

    async def wiederherstellen(self, eintrag_id: int) -> None:
        """Einen Eintrag aus nexcrates Papierkorb zurückholen."""
        await self.client.restore(eintrag_id)

    async def kalender(self, media_type: str, von: str, bis: str) -> list[dict[str, Any]]:
        """nexcrates Kalender, in Stuecken zu hoechstens hundert Tagen."""
        roh: list[dict[str, Any]] = []
        for anfang, ende in lesen._spannen(von, bis):
            roh += await self.client.calendar(anfang, ende, mapping.kind(media_type))
        return lesen.kalender(roh, media_type)

    async def wertungen_filme(
        self, tmdb_ids: list[int], *, einzeln: bool = False
    ) -> dict[int, Any]:
        """Wertungen im Stapel (N39), fuer die Titelseite aus der Einzelansicht.

        Gefragt wird per TMDB-Nummer; nexcrate uebersetzt selbst nach IMDb und
        nennt die Kennung in ``imdb_ref`` zurueck. Seit nexcrate ``39dfc05``
        auch fuer Filme, die es nicht fuehrt (ueber TMDB); eine aeltere
        nexcrate oder eine ohne TMDB-Token antwortet dort ``imdb_unknown``,
        und der Titel bleibt still ohne Wertung. Nexview fragt deshalb jeden
        Film, nicht nur gefuehrte.

        ⚠️ **Nur die Einzelansicht fragt OMDb** (``GET /ratings/{kind}/{ref}``).
        Der Stapel traegt Rotten Tomatoes und Metacritic seit nexcrate
        ``39dfc05`` auch, aber nur, was schon in nexcrates 30-Tage-Speicher
        liegt; ein Titel, den dort noch niemand angesehen hat, bekaeme im
        Stapel nur IMDb. Die Titelseite bleibt deshalb bei der Einzelansicht:
        Sie liest denselben Speicher zuerst und fragt OMDb nur, wenn er leer
        ist - danach hat der Titel die Werte auch im Stapel. Sie traegt IMDb
        mit, wer sie fragt, braucht fuer diesen Titel keinen Stapel. Ein
        Titel, dessen Einzelansicht scheitert, bleibt leer; die anderen nicht.
        """
        nummern = list(dict.fromkeys(tmdb_ids))
        if not nummern:
            return {}
        je_titel = nummern[:WERTUNGEN_EINZELN] if einzeln else []
        rest = nummern[len(je_titel) :]

        gefunden: dict[int, Any] = {}
        if rest:
            nach_tmdb = {mapping.ref(nummer): nummer for nummer in rest}
            try:
                antwort = await self.client.ratings(
                    [{"kind": "movie", "ref": ref} for ref in nach_tmdb]
                )
            except BeschaffungError as fehler_:
                # Wie ``portal_ratings`` im ARR-Betrieb: Ein gescheiterter
                # Stapel ist leer, kein 500. Bis zum 24.09.2026 flog er bis in
                # den Router und nahm die Einzelwerte der Titelseite mit.
                logger.debug("Ratings batch not read: %s", fehler_.code)
            else:
                gefunden.update(lesen.wertungen(antwort, nach_tmdb))

        async def eine(nummer: int) -> tuple[int, Any]:
            try:
                return nummer, lesen.wertung(
                    await self.client.rating("movie", mapping.ref(nummer))
                )
            except BeschaffungError as fehler_:
                # Wertungen sind Beiwerk: ohne sie steht die Seite trotzdem.
                logger.debug("Ratings for tmdb:%s not read: %s", nummer, fehler_.code)
                return nummer, None

        for nummer, wert in await asyncio.gather(*(eine(nummer) for nummer in je_titel)):
            if wert is not None:
                gefunden[nummer] = wert
        return gefunden

    # -- Auftraege ------------------------------------------------------------

    async def anfragen(self, db: Session, anfrage: MediaRequest) -> int | None:
        return await auftraege.anfragen(db, self.settings, anfrage)

    async def abbrechen(self, db: Session, anfrage: MediaRequest) -> str:
        return await auftraege.zuruecknehmen(
            db, self.settings, anfrage, dateien_loeschen=True
        )

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
        """Die Kennung eines Postens bei der Quelle.

        Im NEX-Betrieb ist das die TMDB-Nummer selbst - jede Adresse von
        nexcrate nimmt ``tmdb:<n>``. Geprueft wird trotzdem, ob nexcrate den
        Titel ueberhaupt fuehrt: Ein Posten ohne Gegenstueck bleibt zaehlbar,
        aber nicht loeschbar (Bauplan 6.4).
        """
        if not zeile.tmdb_id:
            return None
        titel = await self.client.title(mapping.kind(zeile.media_type.value), mapping.ref(zeile.tmdb_id))
        return int(zeile.tmdb_id) if titel is not None else None

    async def posten_dateien(
        self, zeile: StorageEntry, arr_id: int, paket_folgen: Callable[[], list[int] | None]
    ) -> list[tuple[str, int]]:
        return await speicher.dateien(self.settings, zeile, arr_id, paket_folgen)

    async def posten_loeschen(
        self, zeile: StorageEntry, arr_id: int, paket_folgen: Callable[[], list[int] | None]
    ) -> None:
        await speicher.loeschen(self.settings, zeile, arr_id, paket_folgen)

    async def posten_stilllegen(self, zeile: StorageEntry) -> int | None:
        return await speicher.stilllegen(self.settings, zeile)

    # -- Konto aufloesen ------------------------------------------------------

    async def laufende_aufloesen(
        self, db: Session, laufend: Any, *, behalten: bool, weiter: bool
    ) -> bool:
        """Eine angefangene Bestellung nach der Wahl des Administrators.

        ``False`` heisst: nichts zu tun. „Behalten und weiter" ist genau das -
        die Bestellung laeuft zu Ende und faellt ans Haus.
        """
        if laufend.arr_id is None or (behalten and weiter):
            return False
        anfrage = db.get(MediaRequest, laufend.request_id)
        if anfrage is None:
            return False
        if behalten:
            # Einfrieren: Ueberwachung aus, Datei bleibt (N43).
            await auftraege.einfrieren(self.settings, anfrage)
        else:
            await auftraege.zuruecknehmen(
                db, self.settings, anfrage, dateien_loeschen=True
            )
        return True

    async def bestellung_zuruecknehmen(self, anfrage: MediaRequest) -> None:
        """Eine Bestellung ohne Dateien zuruecknehmen.

        ``delete_files`` bleibt an: Sollte in der letzten Sekunde doch eine
        Datei angekommen sein, wandert sie in nexcrates Papierkorb, statt
        verwaist liegenzubleiben. Dieselbe Vorsicht wie im ARR-Betrieb.
        """
        from ....db import SessionLocal

        with SessionLocal() as db:
            frisch = db.get(MediaRequest, anfrage.id) or anfrage
            await auftraege.zuruecknehmen(
                db, self.settings, frisch, dateien_loeschen=True
            )

    # -- Instanzen: Stand, Gesundheit, Rueckkanal ------------------------------

    async def instanz_messen(self, instanz: Any, *, voll: bool) -> Any:
        """Erreichbarkeit und Version jede Runde, der Rest stuendlich.

        Gemessen wird ueber ``GET /system``: Es antwortet in Millisekunden und
        traegt Version und Update-Hinweis in derselben Antwort (N4, N36).
        """
        from ..arr.stand import Messung

        messung = Messung(erreichbar=True)
        try:
            daten = await system.auffrischen(self.settings)
        except BeschaffungError:
            return Messung(erreichbar=False)
        messung.version = str(daten.get("version") or "")
        if voll:
            update = daten.get("update") or {}
            messung.messwerte["aktualisierung"] = (
                str(update.get("latest") or "") if update.get("available") else None
            )
            try:
                messung.messwerte["warteschlange"] = await self._warteschlangen_zustand()
            except BeschaffungError:
                pass
        return messung

    async def _warteschlangen_zustand(self) -> dict[str, int]:
        """Wie viele Downloads laufen, und wie viele davon klemmen."""
        roh = await self.client.queue()
        # Ein gescheiterter zaehlt nur, solange er ein Problem meldet (auf den
        # Betreiber wartet); sonst ist er Verlauf (Rundgang-Befund 9).
        offen = [e for e in roh if mapping.download_laeuft(e) or e.get("problem")]
        return {
            "gesamt": len(offen),
            "gestoert": sum(1 for eintrag in offen if eintrag.get("problem")),
        }

    async def gesundheit_pruefen(self, db: Session) -> None:
        eigene = self.instanzen()
        if not eigene:
            return
        await gesundheit.pruefen(db, self.settings, INSTANZ, eigene[0].name)

    async def rueckkanal_pflegen(self, db: Session) -> None:
        """Nichts zu tun: Nexview legt in nexcrate keinen Webhook an (N32)."""
        return

    async def verlassen(self, db: Session) -> list[Abschied]:
        """Den Zugang zu nexcrate loeschen. Mehr kann Nexview nicht.

        ⚠️ **Der Schluessel bleibt in nexcrate stehen.** Ein Programm kann ihn
        dort nicht widerrufen (nexbeat-Befund 15: Ein zweites Koppeln legt
        einen zweiten an, der erste bleibt in der Liste). Der Bericht sagt es,
        damit der Betreiber ihn von Hand entfernen kann.
        """
        from ...settings_service import clear_secret, save_settings

        bericht: list[Abschied] = []
        if self.settings.nexcrate_configured:
            bericht.append(Abschied("nexcrate_schluessel_entfernt"))
        clear_secret(db, "nexcrate_api_key")
        save_settings(
            db,
            {
                "nexcrate_url": "",
                "nexcrate_installation_id": "",
                "nexcrate_web_url": "",
                "nexcrate_titles_after": "",
                "nexcrate_events_after": "",
            },
        )
        system.vergessen()
        fassungen.vergessen()
        bestand.verwerfen()
        return bericht

    # -- Haengende Downloads --------------------------------------------------

    async def downloads_auffrischen(
        self, db: Session, *, frisch_genug: timedelta | None = None
    ) -> Any:
        """Warteschlange und Probleme in **einem** Rundgang (Entscheidung 11).

        ``frisch_genug`` waere ein Zwischenspeicher fuer die Seite; nexcrate
        antwortet in Millisekunden, und ein zu alter Stand waere hier teurer
        als der Aufruf.
        """
        eigene = self.instanzen()
        if not eigene:
            return downloads.NexRundgang(am=utcnow(), abfragen=[])
        return await downloads.auffrischen(db, self.settings, eigene[0])

    def download_anfragen(self, db: Session, zeile: Any) -> list[MediaRequest]:
        return downloads.anfragen_zu(db, zeile)

    async def download_entfernen(
        self,
        db: Session,
        zeile_id: int,
        *,
        neu_suchen: bool,
        wer: User | None,
        automatisch: bool = False,
    ) -> Any:
        return await aktionen.entfernen(
            db, self.settings, zeile_id, neu_suchen=neu_suchen, wer=wer, automatisch=automatisch
        )

    async def download_erneut_pruefen(
        self, db: Session, zeile_id: int, *, wer: User | None, automatisch: bool = False
    ) -> Any:
        return await aktionen.erneut_pruefen(
            db, self.settings, zeile_id, wer=wer, automatisch=automatisch
        )

    async def download_kandidaten(self, db: Session, zeile_id: int) -> list[Any]:
        return await aktionen.kandidaten(db, self.settings, zeile_id)

    async def download_importieren(
        self, db: Session, zeile_id: int, pfade: list[str], *, trotzdem: bool, wer: User | None
    ) -> Any:
        return await aktionen.importieren(
            db, self.settings, zeile_id, pfade, trotzdem=trotzdem, wer=wer
        )

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
        """Der Ereignisstrom - ein Wecker, keine zweite Wahrheit (Bauplan 6.8).

        Er laeuft auch im ARR-Betrieb mit und prueft die Betriebsart je Runde
        selbst: Eingebundene Aufgaben lassen sich nach dem Start nicht mehr
        tauschen, die Betriebsart aber sehr wohl umstellen.
        """
        from . import ereignisse

        return [ereignisse.run_forever(stop)]

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
        """Der gemerkte Stand - dieselbe Tabelle wie im ARR-Betrieb."""
        from ..arr.instanz_gesundheit import alle

        return alle(db)

    @classmethod
    def haenger_je_instanz(cls, db: Session) -> dict[str, int]:
        from ..arr.download_haenger import zaehlen

        return zaehlen(db)

    @classmethod
    def download_aktionen_moeglich(cls, zeile: Any) -> list[Aktion]:
        """Was nexcrate an diesem Problem erlaubt - und nichts sonst (6.6)."""
        return downloads.erlaubte_aktionen(zeile)

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
