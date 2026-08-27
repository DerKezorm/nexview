"""Sonarr - zustaendig ausschliesslich fuer Serien.

Wichtiger Unterschied zu Radarr: Sonarr kennt keine TMDB-Ids, sondern
arbeitet mit TVDB-Ids. Deshalb wird die TVDB-Id bei Serien schon beim
Laden der Details von TMDB mitgeholt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .arr import ArrClient, ArrError


@dataclass(frozen=True)
class Staffelstand:
    """Wie weit **eine** Staffel geladen ist.

    ⚠️ Gebraucht, weil ``has_file`` eine Aussage ueber die **ganze Serie** ist:
    "mindestens eine Folge liegt vor". Solange nur ganze Serien angefragt
    werden konnten, war das dasselbe. Bei Staffelanfragen ist es das nicht -
    gemeldet wurde eine Serie mit drei Dateien in Staffel 3, worauf **fuenf**
    Staffelanfragen gleichzeitig als "bereits geladen" galten und fuenf
    Fertig-Meldungen in derselben Sekunde hinausgingen.
    """

    dateien: int
    folgen: int
    # Laeuft die Ueberwachung? ``True`` als Vorgabe heisst "kein Anlass zur
    # Heilung" - wo die Angabe fehlt, wird nicht an Sonarr herumgestellt.
    monitored: bool = True

    @property
    def vollstaendig(self) -> bool:
        """Alle Folgen dieser Staffel liegen vor.

        Strenger als ``has_file`` und mit Absicht: Eine Staffel ist eine
        abgeschlossene, abzaehlbare Menge - "fertig" laesst sich hier wirklich
        beantworten. Bei einer ganzen Serie waere dieselbe Frage sinnlos, weil
        eine laufende Serie nie fertig ist.
        """
        return self.folgen > 0 and self.dateien >= self.folgen


@dataclass(frozen=True)
class Folge:
    """Eine Folge, wie Sonarr sie fuehrt - das Noetigste fuer Folgen-Pakete.

    ``kennung`` ist Sonarrs Episoden-Id (fuers Einschalten und Suchen),
    ``datei_id`` die Id der Episodendatei (fuers gezielte Loeschen beim
    Abbruch) - ``None``, solange keine Datei liegt.
    """

    kennung: int
    nummer: int
    monitored: bool
    has_file: bool
    datei_id: int | None = None


@dataclass(frozen=True)
class LibraryEntry:
    """Eine Serie, wie sie Sonarr kennt."""

    arr_id: int
    has_file: bool  # mindestens eine Folge der **ganzen Serie** liegt vor
    monitored: bool
    episode_file_count: int
    episode_count: int
    title_key: str  # normalisierter Titel als Rueckfallweg
    # Nur fuer den Titel-Rueckfall: Ohne Jahr trifft "Countdown" (1982) jede
    # andere Serie desselben Namens - samt deren Folgen. Siehe jahre_passen.
    year: int | None = None
    # Belegter Platz der ganzen Serie in Bytes.
    size_bytes: int = 0
    # Belegter Platz **je Staffel**: {Staffelnummer: Bytes}. Sonarr haengt die
    # Staffel-Statistik an dieselbe Antwort - eine eigene Abfrage waere nur
    # noetig, wollte man bis auf die einzelne Folge hinunter. Die
    # Speicher-Belegung rechnet deshalb staffelweise.
    seasons: dict[int, int] = field(default_factory=dict)
    # Ladestand **je Staffel** - aus derselben Statistik wie die Groessen.
    # Ohne diese Aufschluesselung laesst sich eine Staffelanfrage nicht
    # beantworten; siehe ``Staffelstand``.
    staffeln: dict[int, Staffelstand] = field(default_factory=dict)
    # Letzter bekannter Titel, damit ein Posten anzeigbar bleibt, wenn die
    # Serie spaeter aus Sonarr verschwindet.
    title: str = ""
    # Der **Ordner** der Serie - kein Dateiname. Eine Staffel ist keine Datei,
    # sondern zwanzig; echte Dateinamen braeuchten eine Abfrage je Serie.
    path: str = ""


def normalize_title(title: str) -> str:
    """Titel auf einen vergleichbaren Kern reduzieren."""
    return "".join(character for character in title.casefold() if character.isalnum())


def jahre_passen(gesucht: int | None, gefunden: int | None) -> bool:
    """Gehoeren die beiden Jahresangaben plausibel zusammen?

    Gebraucht ueberall dort, wo ueber den **Titel** abgeglichen wird - und das
    ist bei Serien der Regelfall, weil TMDB fuer viele Serien keine TVDB-Id
    kennt. Ohne diese Pruefung reicht Namensgleichheit: Gemeldet wurde
    "Countdown" (1982), das in Sonarr eine voellig andere Serie traf und samt
    deren Folgenliste als "bereits geladen" erschien.

    Ein Jahr Abweichung ist erlaubt: Erstausstrahlung und Serienstart nach
    Zaehlweise der jeweiligen Datenbank fallen oft auseinander. Fehlt eine der
    beiden Angaben, wird der Treffer verworfen - lieber einen vorhandenen Titel
    uebersehen als einen falschen behaupten. Ein uebersehener kostet einen
    doppelten Download, ein falscher nimmt einen Titel dauerhaft aus dem
    Angebot, ohne dass jemand den Grund sieht.
    """
    if gesucht is None or gefunden is None:
        return False
    return abs(gesucht - gefunden) <= 1


def _zahl(wert: Any) -> int:
    """Bytes-Angabe aus Sonarr, robust gegen Nichts und Unsinn."""
    return int(wert) if isinstance(wert, (int, float)) and wert > 0 else 0


def _staffel_groessen(show: dict[str, Any]) -> dict[int, int]:
    """Belegter Platz je Staffel, aus derselben Antwort wie alles andere.

    Sonarr haengt an jede Staffel in ``/series`` eine eigene Statistik. Damit
    ist die Staffel die feinste Koernung, die **ohne** zusaetzliche Abfrage zu
    haben ist; bis auf die einzelne Folge hinunter braeuchte es
    ``/episode?includeEpisodeFile=true`` je Serie.

    Staffeln ohne Dateien werden weggelassen - ein Posten ueber null Bytes
    waere nur Zeile ohne Aussage. Staffel 0 (Extras) bleibt drin: Sie belegt
    echten Platz, anders als bei der Staffelauswahl, wo sie bewusst fehlt.
    """
    groessen: dict[int, int] = {}
    for staffel in show.get("seasons") or []:
        nummer = staffel.get("seasonNumber")
        if not isinstance(nummer, int):
            continue
        bytes_ = _zahl((staffel.get("statistics") or {}).get("sizeOnDisk"))
        if bytes_ > 0:
            groessen[nummer] = bytes_
    return groessen


def _staffel_stand(show: dict[str, Any]) -> dict[int, Staffelstand]:
    """Ladestand je Staffel - aus derselben Statistik wie die Groessen.

    Anders als dort bleiben Staffeln **ohne** Dateien hier stehen: Genau die
    sind die Antwort auf "laeuft noch". Wer sie weglaesst, kann eine leere
    Staffel nicht von einer unbekannten unterscheiden.
    """
    stand: dict[int, Staffelstand] = {}
    for staffel in show.get("seasons") or []:
        nummer = staffel.get("seasonNumber")
        if not isinstance(nummer, int):
            continue
        zahlen = staffel.get("statistics") or {}
        stand[nummer] = Staffelstand(
            dateien=int(zahlen.get("episodeFileCount") or 0),
            folgen=int(zahlen.get("episodeCount") or 0),
            monitored=bool(staffel.get("monitored")),
        )
    return stand


class SonarrClient(ArrClient):
    def __init__(self, base_url: str, api_key: str) -> None:
        super().__init__(base_url, api_key, "Sonarr")

    async def library(self) -> tuple[dict[int, LibraryEntry], dict[str, LibraryEntry]]:
        """Alle Serien aus Sonarr - einmal nach TVDB-Id, einmal nach Titel.

        Der Titel-Index ist der Rueckfallweg: TMDB kennt fuer viele neue
        Serien noch keine TVDB-Id, dann waere sonst kein Abgleich moeglich.
        """
        series = await self.get("/series") or []
        by_tvdb: dict[int, LibraryEntry] = {}
        by_title: dict[str, LibraryEntry] = {}

        for show in series:
            statistics = show.get("statistics") or {}
            file_count = int(statistics.get("episodeFileCount") or 0)
            entry = LibraryEntry(
                arr_id=show.get("id", 0),
                has_file=file_count > 0,
                monitored=bool(show.get("monitored")),
                episode_file_count=file_count,
                episode_count=int(statistics.get("episodeCount") or 0),
                title_key=normalize_title(show.get("title") or ""),
                year=show.get("year") if isinstance(show.get("year"), int) else None,
                size_bytes=_zahl(statistics.get("sizeOnDisk")),
                seasons=_staffel_groessen(show),
                staffeln=_staffel_stand(show),
                title=str(show.get("title") or ""),
                path=str(show.get("path") or "").rstrip("/"),
            )

            tvdb_id = show.get("tvdbId")
            if isinstance(tvdb_id, int) and tvdb_id > 0:
                by_tvdb[tvdb_id] = entry
            if entry.title_key:
                by_title[entry.title_key] = entry

        return by_tvdb, by_title

    async def calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        """Welche Folgen in diesem Zeitraum laufen.

        ``includeSeries=true`` haengt an jede Folge die zugehoerige Serie an -
        das spart einen zweiten Aufruf je Serie und liefert Titel, TVDB-Id und
        (ab Sonarr 4) sogar die TMDB-Id gleich mit.
        """
        entries = await self.get(
            "/calendar",
            {"start": start, "end": end, "unmonitored": "true", "includeSeries": "true"},
        )
        return entries if isinstance(entries, list) else []

    async def lookup(self, tvdb_id: int) -> dict[str, Any] | None:
        result = await self.get("/series/lookup", {"term": f"tvdb:{tvdb_id}"})
        if isinstance(result, list):
            return result[0] if result else None
        return result or None

    async def add(
        self,
        tvdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        search_now: bool = True,
        tag_ids: list[int] | None = None,
        season: int | None = None,
        monitor_future: bool = False,
        nur_anlegen: bool = False,
    ) -> dict[str, Any]:
        """Serie zu Sonarr hinzufuegen.

        Ohne ``season`` wird die komplette Serie ueberwacht. Mit ``season``
        wird ausschliesslich diese eine Staffel ueberwacht - alle anderen
        bleiben aus, damit Sonarr nicht doch die ganze Serie herunterlaedt.

        ``nur_anlegen`` (fuer Folgen-Pakete): anlegen und schweigen - keine
        Staffel-Ueberwachung, kein Nachschub, keine Suche. Eingeschaltet wird
        danach folgengenau durch den Aufrufer.
        """
        found = await self.lookup(tvdb_id)
        if found is None:
            raise ArrError(
                "Sonarr kennt diese Serie nicht.", 404, code="sonarr_series_unknown"
            )

        payload = {
            **found,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": True,
            "seasonFolder": True,
            # ⚠️ Was mit **kuenftigen** Staffeln geschieht, entscheidet allein
            # dieses Feld - nicht ``monitored`` und nicht die Staffelliste.
            # Steht es auf "all", laedt Sonarr jede neue Staffel von selbst;
            # der Anfragende haette dann Speicher zugesagt, den zum Zeitpunkt
            # der Anfrage niemand beziffern konnte. Deshalb nur, wenn er es
            # ausdruecklich angehakt hat.
            "monitorNewItems": "all" if monitor_future else "none",
            "tags": tag_ids or [],
            "addOptions": {
                "monitor": "all",
                "searchForMissingEpisodes": search_now,
            },
        }

        if season is not None:
            # ⚠️ **``addOptions.monitor`` gewinnt gegen die Staffelliste.**
            #
            # Hier stand zusaetzlich eine ``seasons``-Liste mit genau der
            # gewuenschten Staffel auf ``monitored``, in der Annahme, Sonarr
            # richte sich danach. Es tut es nicht: ``monitor: "none"`` legt nach
            # dem Anlegen **alles** still - die Serie und saemtliche Staffeln.
            # Ergebnis war eine Serie in Sonarr auf "nicht ueberwacht", die nie
            # etwas geladen hat, waehrend Nexview die Anfrage als laufend
            # fuehrte. Nachgemessen an Baywatch: 12 Staffeln, keine ueberwacht.
            #
            # Deshalb wird beim Anlegen bewusst nichts ueberwacht - und die
            # gewuenschte Staffel gleich danach eingeschaltet, ueber denselben
            # Weg, den auch das Nachfordern nimmt.
            payload["addOptions"] = {
                "monitor": "none",
                "searchForMissingEpisodes": False,
            }

        if nur_anlegen:
            # Ein Folgen-Paket folgt keinem Nachschub - und eingeschaltet wird
            # spaeter je Folge, nicht je Staffel.
            payload["monitorNewItems"] = "none"
            payload["addOptions"] = {
                "monitor": "none",
                "searchForMissingEpisodes": False,
            }

        angelegt = await self.post("/series", payload)

        # Erst jetzt die Staffel einschalten - und mit ihr die Serie, denn eine
        # stillgelegte Serie laedt auch einzelne Staffeln nicht. ``monitor_season``
        # stoesst die Suche gleich mit an.
        if season is not None and not nur_anlegen and isinstance(angelegt, dict):
            await self.monitor_seasons(
                angelegt.get("id"), {season}, season if search_now else None
            )

        return angelegt

    async def monitor_seasons(
        self, arr_id: int | None, seasons: set[int], such_staffel: int | None = None
    ) -> None:
        """**Alle** genannten Staffeln ueberwachen - und nichts abschalten.

        ⚠️ **Warum die ganze Menge und nicht die eine neue Staffel:**
        ``addOptions.monitor: "none"`` wirkt bei Sonarr **asynchron**. Es raeumt
        nach dem Anlegen alles ab - auch das, was Nexview unmittelbar danach
        eingeschaltet hat. Nachgemessen: Staffel 3 wurde freigegeben, angelegt
        und geladen; zwei Minuten spaeter kam die Freigabe fuer Staffel 2, las
        den inzwischen abgeraeumten Stand und schrieb ihn samt abgeschalteter
        Staffel 3 zurueck.

        Deshalb ist **Nexview** die Quelle der Wahrheit: Der Aufrufer uebergibt
        alle Staffeln, zu denen eine Anfrage laeuft, und dieser Aufruf stellt
        sie her. Ein abgeraeumter Zustand heilt damit von selbst beim naechsten
        Mal.

        Abgeschaltet wird **nichts**: Wer in Sonarr von Hand eine weitere
        Staffel ueberwacht, soll sie behalten.
        """
        if not arr_id:
            raise ArrError(
                "Sonarr hat keine Kennung fuer diese Serie geliefert.",
                502,
                code="sonarr_no_id",
            )
        if not seasons:
            return
        serie = await self.get(f"/series/{arr_id}")
        if not isinstance(serie, dict):
            raise ArrError(
                "Sonarr liefert diese Serie nicht.", 404, code="sonarr_series_missing"
            )

        staffeln = serie.get("seasons") or []
        bekannt = {eintrag.get("seasonNumber") for eintrag in staffeln}
        fehlend = seasons - bekannt
        if fehlend:
            raise ArrError(
                f"Sonarr kennt Staffel {sorted(fehlend)[0]} dieser Serie nicht.",
                404,
                code="sonarr_season_unknown",
                season=sorted(fehlend)[0],
            )

        serie["seasons"] = [
            {**eintrag, "monitored": True}
            if eintrag.get("seasonNumber") in seasons
            else eintrag
            for eintrag in staffeln
        ]
        # Eine Serie, die als Ganzes nicht ueberwacht wird, laedt auch einzelne
        # Staffeln nicht - deshalb hier mit aktivieren.
        serie["monitored"] = True

        await self.put(f"/series/{arr_id}", serie)

        if such_staffel is not None:
            await self.search_season(arr_id, such_staffel)

    async def search_season(self, arr_id: int | None, season: int) -> None:
        """Sonarr anweisen, genau diese Staffel zu suchen.

        Schlaegt das fehl, ist das kein Beinbruch: die Staffel ist ueberwacht,
        Sonarr findet sie beim naechsten regulaeren Durchlauf von selbst.
        """
        if not arr_id:
            return
        try:
            await self.post(
                "/command", {"name": "SeasonSearch", "seriesId": arr_id, "seasonNumber": season}
            )
        except ArrError:
            pass

    async def episode_status(self, arr_id: int) -> dict[int, set[int]]:
        """Welche Folgen liegen bereits vor? Nach Staffelnummer gebuendelt.

        Sonarr liefert alle Folgen einer Serie in einem Aufruf; feiner
        aufzuteilen waere eine Abfrage pro Staffel, und davon haette niemand
        etwas.
        """
        folgen = await self.get("/episode", {"seriesId": arr_id}) or []
        vorhanden: dict[int, set[int]] = {}
        for folge in folgen:
            if not folge.get("hasFile"):
                continue
            staffel = folge.get("seasonNumber")
            nummer = folge.get("episodeNumber")
            if isinstance(staffel, int) and isinstance(nummer, int):
                vorhanden.setdefault(staffel, set()).add(nummer)
        return vorhanden

    async def folgen_stand(self, arr_id: int) -> dict[int, dict[int, Folge]]:
        """Alle Folgen einer Serie - je Staffel nach Folgennummer.

        Die eine Quelle fuer alles Folgengenaue: fertig?, ueberwacht?, welche
        Datei gehoert dazu? Sonarr liefert saemtliche Folgen einer Serie in
        einem Aufruf; feiner aufzuteilen waere eine Abfrage je Staffel ohne
        Gewinn.

        ⚠️ **Eine fehlende Staffel bedeutet "noch unbekannt", nicht "leer".**
        Direkt nach dem Anlegen einer Serie ist die Folgenliste eine Weile
        leer, weil Sonarr die Metadaten asynchron nachlaedt - dasselbe
        asynchrone Verhalten, das auch die Ueberwachung abraeumt. Der
        Aufrufer behandelt das als "spaeter noch einmal".
        """
        eintraege = await self.get("/episode", {"seriesId": arr_id}) or []
        staffeln: dict[int, dict[int, Folge]] = {}
        for eintrag in eintraege:
            if not isinstance(eintrag, dict):
                continue
            staffel = eintrag.get("seasonNumber")
            nummer = eintrag.get("episodeNumber")
            kennung = eintrag.get("id")
            if not (
                isinstance(staffel, int)
                and isinstance(nummer, int)
                and isinstance(kennung, int)
            ):
                continue
            staffeln.setdefault(staffel, {})[nummer] = Folge(
                kennung=kennung,
                nummer=nummer,
                monitored=bool(eintrag.get("monitored")),
                has_file=bool(eintrag.get("hasFile")),
                datei_id=eintrag.get("episodeFileId") or None,
            )
        return staffeln

    async def folgen_schalten(self, kennungen: list[int], ueberwachen: bool) -> None:
        """Genau diese Folgen ueberwachen bzw. stilllegen.

        ``PUT /episode/monitor`` nimmt die Kennungen gesammelt entgegen - der
        dokumentierte Weg fuer genau diesen Zweck. ⚠️ Vor dem ersten Release
        an der echten Instanz nachmessen (Bauplan-Pruefliste): Sonarrs
        Sammel-Endpunkte haben schon einmal anders geantwortet als
        dokumentiert (``/episodefile/bulk``).

        Eingeschaltet wird nur, was der Aufrufer nennt - nie mehr.
        Ausgeschaltet genauso: Das ist dem Abbruch der eigenen Folgen
        vorbehalten; Nexview raeumt sonst nie ab.
        """
        if not kennungen:
            return
        await self.put(
            "/episode/monitor", {"episodeIds": kennungen, "monitored": ueberwachen}
        )

    async def folgen_suchen(self, kennungen: list[int]) -> None:
        """Sonarr anweisen, genau diese Folgen zu suchen.

        Schlaegt das fehl, ist das kein Beinbruch: Die Folgen sind ueberwacht,
        Sonarr findet sie beim naechsten regulaeren Durchlauf von selbst.
        """
        if not kennungen:
            return
        try:
            await self.post("/command", {"name": "EpisodeSearch", "episodeIds": kennungen})
        except ArrError:
            pass

    async def serie_ueberwachen(self, arr_id: int) -> None:
        """Nur die Serien-Flagge einschalten - Staffeln und Folgen unangetastet.

        Eine Serie, die als Ganzes nicht ueberwacht wird, laedt auch
        ueberwachte Folgen nicht. Fuer Folgen-Pakete darf aber keine
        Staffel-Flagge mit umgelegt werden - sonst zoege Sonarr die ganze
        Staffel, und der Sinn des Pakets waere dahin.

        ⚠️ **Das Einschalten weckt Altlasten.** Live erlebt: In einer global
        stummen Serie waren vier Folgen einzeln als ueberwacht markiert -
        von frueher, ohne Datei, ohne Anfrage. Mit der Serien-Flagge fing
        Sonarr sofort an, sie zu laden. Nexview schaltet grundsaetzlich
        nichts ab, was es nicht selbst bestellt hat (die Markierung koennte
        gewollt sein) - aber es sagt es laut, damit die Ursache im Protokoll
        steht, wenn ploetzlich Unbestelltes eintrudelt.
        """
        serie = await self.get(f"/series/{arr_id}")
        if not isinstance(serie, dict):
            raise ArrError(
                "Sonarr liefert diese Serie nicht.", 404, code="sonarr_series_missing"
            )
        if serie.get("monitored"):
            return
        geweckt = 0
        try:
            stand = await self.folgen_stand(arr_id)
            geweckt = sum(
                1
                for staffel in stand.values()
                for folge in staffel.values()
                if folge.monitored and not folge.has_file
            )
        except ArrError:
            pass
        if geweckt:
            logger.warning(
                "Switching series %s on wakes %s previously monitored episode(s) "
                "without a file - Sonarr may start downloading them",
                arr_id,
                geweckt,
            )
        serie["monitored"] = True
        await self.put(f"/series/{arr_id}", serie)

    async def remove(self, arr_id: int, delete_files: bool = True) -> None:
        """Serie aus Sonarr entfernen - samt bereits geladener Folgen."""
        await self.delete(
            f"/series/{arr_id}",
            {"deleteFiles": str(delete_files).lower(), "addImportListExclusion": "false"},
        )

    async def episode_files(self, arr_id: int, season: int) -> list[dict[str, Any]]:
        """Die Dateien **einer** Staffel - mit Pfad und Groesse.

        Für den Probelauf vor dem Loeschen: Der Administrator soll die
        tatsaechliche Liste sehen, nicht eine Zahl. Ein Fehler beim
        staffelweisen Loeschen trifft Folgen, die jemand behalten wollte, und
        eine Zahl verraet nicht, welche.

        ``/episodefile`` liefert alle Dateien einer Serie in einem Aufruf; die
        Staffel steht an jeder Datei.
        """
        dateien = await self.get("/episodefile", {"seriesId": arr_id}) or []
        return [
            datei
            for datei in dateien
            if isinstance(datei, dict) and datei.get("seasonNumber") == season
        ]

    async def serie_stilllegen(self, arr_id: int) -> None:
        """Die ganze Serie stilllegen: nichts mehr laden, auch kuenftig nicht.

        Gebraucht bei der Konto-Aufloesung fuer Anfragen ueber die **ganze**
        Serie: Deren Ueberwachung gehoert niemandem mehr, und ohne diesen
        Schritt laedt sie herrenlos weiter. Vorhandene Dateien bleiben liegen -
        stillgelegt heisst "es kommt nichts mehr dazu", nicht "weg damit".
        """
        serie = await self.get(f"/series/{arr_id}")
        if not isinstance(serie, dict):
            return
        serie["monitored"] = False
        serie["monitorNewItems"] = "none"
        serie["seasons"] = [
            {**eintrag, "monitored": False} for eintrag in (serie.get("seasons") or [])
        ]
        await self.put(f"/series/{arr_id}", serie)

    async def unmonitor_season(self, arr_id: int, season: int) -> None:
        """Eine Staffel stilllegen - **nach** dem Loeschen ihrer Dateien.

        ⚠️ **Ohne das kommt die Staffel sofort zurueck.** Sonarr sucht fuer
        jede ueberwachte Staffel nach fehlenden Folgen; wer die Dateien
        loescht und die Ueberwachung anlaesst, hat sie beim naechsten Durchlauf
        wieder auf der Platte - und der Nutzer, der abgegeben hat, sieht seinen
        Speicher erneut steigen.

        Die Serie selbst bleibt ueberwacht: Andere Staffeln und kuenftige
        Folgen sollen weiterlaufen.
        """
        serie = await self.get(f"/series/{arr_id}")
        if not isinstance(serie, dict):
            raise ArrError(
                "Sonarr liefert diese Serie nicht.", 404, code="sonarr_series_missing"
            )

        serie["seasons"] = [
            {**eintrag, "monitored": False}
            if eintrag.get("seasonNumber") == season
            else eintrag
            for eintrag in (serie.get("seasons") or [])
        ]
        await self.put(f"/series/{arr_id}", serie)

    async def delete_episode_files(self, datei_ids: list[int]) -> int:
        """Genau diese Dateien entfernen - sonst nichts. Gibt die Anzahl zurueck.

        **Eine Datei je Aufruf, nicht als Sammelbefehl.** Sonarr hat zwar
        ``/episodefile/bulk``, aber eine Probe mit einer nicht existierenden
        Kennung beantwortete es mit einem HTTP 500 - daraus laesst sich nicht
        ablesen, ob die Form stimmt oder nur die Nummer fehlt. Der
        Einzel-Endpunkt antwortet dagegen sauber mit 404. Bei einem
        Loeschbefehl ist "geprueft" mehr wert als "vermutlich richtig", und
        nebenbei kann ein Fehler so hoechstens **eine** Datei treffen.

        ⚠️ **Niemals mit leerer Liste aufrufen.** Der Aufrufer prueft das;
        hier steht die zweite Sperre.

        Geloescht wird ueber die Kennungen einzelner Dateien, nicht ueber
        Staffel oder Serie: So kann der Aufruf nur treffen, was vorher
        aufgelistet und dem Administrator gezeigt wurde.
        """
        if not datei_ids:
            raise ArrError(
                "Ohne Dateien gibt es nichts zu loeschen.", 400, code="arr_nothing_to_delete"
            )

        entfernt = 0
        for kennung in datei_ids:
            try:
                await self.delete(f"/episodefile/{kennung}")
                entfernt += 1
            except ArrError as fehler:
                # 404 heisst: schon weg - dann ist das Ziel ja erreicht.
                if fehler.status_code != 404:
                    raise
        return entfernt


def _zeitpunkt(roh: object) -> datetime | None:
    """ISO-8601 aus Sonarr in einen naiven UTC-Zeitpunkt."""
    if not isinstance(roh, str) or not roh:
        return None
    try:
        return (
            datetime.fromisoformat(roh.replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
    except ValueError:
        return None


async def staffel_daten(client: "SonarrClient", series_id: int) -> dict[int, datetime]:
    """Seit wann die Dateien jeder Staffel dieser Serie da liegen.

    ⚠️ **Eine eigene Abfrage je Serie - deshalb ist sie hier und nicht in
    ``library()``.** Sonarr haengt an ``/series`` zwar Groesse und Folgenzahl
    je Staffel, aber kein Datum. Das steht nur an der einzelnen Datei.

    Der Aufrufer holt sie deswegen **nur fuer Staffeln, deren Datum noch
    fehlt** (siehe ``services/storage``). Der Abgleich laeuft stuendlich; sie
    jedes Mal fuer jede Serie zu stellen hiesse bei zweihundert Serien
    fuenftausend Abfragen am Tag - fuer ein Datum, das sich nie aendert.

    Genommen wird das **aelteste** Datum je Staffel: Gefragt ist, seit wann
    die Staffel Platz belegt, und das faengt bei ihrer ersten Datei an. Das
    juengste zu nehmen hiesse, dass eine einzige nachgeladene Folge eine
    zehn Jahre alte Staffel wieder taufrisch aussehen laesst.
    """
    dateien = await client.get("/episodefile", {"seriesId": series_id}) or []
    if not isinstance(dateien, list):
        return {}

    aeltestes: dict[int, datetime] = {}
    for datei in dateien:
        if not isinstance(datei, dict):
            continue
        nummer = datei.get("seasonNumber")
        wann = _zeitpunkt(datei.get("dateAdded"))
        if not isinstance(nummer, int) or wann is None:
            continue
        if nummer not in aeltestes or wann < aeltestes[nummer]:
            aeltestes[nummer] = wann
    return aeltestes
