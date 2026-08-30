"""Plex als Media-Server.

Der Adapter ist bewusst duenn: die Anmeldung liegt bei plex.tv (``plextv.py``),
hier steht nur, was den Server selbst betrifft - und die Uebersetzung in die
anbieter-neutralen Formen aus ``base.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from . import plextv
from .base import (
    Umrechnung,
    Wiedergabe,
    ExternalAccount,
    LibraryItem,
    LoginChallenge,
    MediaServer,
    MediaServerError,
    ServerCandidate,
    ServerUser,
    SeasonWatchedRecord,
    WatchedRecord,
    WatchlistItem,
    http_client,
)

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from ..settings_service import AppSettings, Verbindung

logger = logging.getLogger("nexview.mediaserver")

# Wie viele Titel je Abfrage. Gross genug, dass grosse Bibliotheken nicht in
# hundert Abfragen zerfallen, klein genug fuer eine handliche Antwort.
SEITENGROESSE = 500

# Unter dieser Nummer fuehrt Plex den Eigentuemer des Servers. Geteilte Nutzer
# erscheinen dagegen unter ihrer plex.tv-Nummer.
EIGENTUEMER_KONTO = "1"


def _nummer(schluessel: str | None) -> str:
    """Aus "/library/metadata/21339" die 21339 herausloesen."""
    return (schluessel or "").rstrip("/").rsplit("/", 1)[-1]


def _als_werk(eintrag: dict[str, Any], media_type: str) -> LibraryItem | None:
    """Einen Plex-Eintrag in die anbieter-neutrale Form uebersetzen.

    Die Kennungen stehen in ``Guid`` als Liste von ``tmdb://123``,
    ``tvdb://456`` und ``imdb://tt789``. Welche davon vorliegt, haengt am
    verwendeten Plex-Agenten: der neue Film-Agent liefert alle drei, aeltere
    Sammlungen oft nur eine einzige. Deshalb wird jede genommen, die da ist -
    und der Titel als letzter Ausweg gemerkt.
    """
    kennungen: dict[str, str] = {}
    for guid in eintrag.get("Guid") or []:
        wert = str(guid.get("id") or "")
        quelle, _, nummer = wert.partition("://")
        if quelle and nummer:
            kennungen[quelle] = nummer

    def zahl(quelle: str) -> int | None:
        roh = kennungen.get(quelle, "")
        return int(roh) if roh.isdigit() else None

    titel = (eintrag.get("title") or "").strip()
    if not titel:
        return None

    # Bei Filmen zaehlt Plex die Wiedergaben, bei Serien die gesehenen Folgen.
    # Beide Felder fehlen ganz, solange nichts gesehen wurde - deshalb "> 0"
    # und nicht "ist vorhanden".
    def zaehler(feld: str) -> int:
        wert = eintrag.get(feld)
        return wert if isinstance(wert, int) else 0

    gesehen = zaehler("viewCount") > 0 or zaehler("viewedLeafCount") > 0
    zuletzt = eintrag.get("lastViewedAt")
    gesehen_am = (
        datetime.fromtimestamp(zuletzt, tz=UTC).replace(tzinfo=None)
        if isinstance(zuletzt, int)
        else None
    )

    # Aufloesung je hinterlegter Datei. Plex nennt sie "4k", "1080", "720" …
    #
    # Nur bei Filmen: Bei Serien haengen die Dateien an den Folgen, der
    # Serien-Eintrag selbst hat gar keine ``Media``-Liste (nachgemessen an
    # einer echten Bibliothek). Fehlt sie, bleibt es bei "Standard vorhanden" -
    # dem Verhalten von vorher.
    aufloesungen = {
        str(medium.get("videoResolution") or "").lower()
        for medium in (eintrag.get("Media") or [])
    }
    aufloesungen.discard("")
    hat_uhd = "4k" in aufloesungen
    # Ohne Angabe gilt "Standard" - siehe LibraryItem. Sonst verschwaende ein
    # Titel ohne Datei-Angaben aus dem Bestand.
    hat_standard = not aufloesungen or bool(aufloesungen - {"4k"})

    groesse_standard, groesse_uhd = _dateigroessen(eintrag)

    return LibraryItem(
        has_standard=hat_standard,
        has_uhd=hat_uhd,
        media_type=media_type,
        guid=str(eintrag.get("guid") or eintrag.get("ratingKey") or titel),
        rating_key=str(eintrag["ratingKey"]) if eintrag.get("ratingKey") else None,
        owner_watched=gesehen,
        watched_at=gesehen_am,
        title=titel,
        tmdb_id=zahl("tmdb"),
        tvdb_id=zahl("tvdb"),
        imdb_id=kennungen.get("imdb") or None,
        year=int(eintrag["year"]) if str(eintrag.get("year") or "").isdigit() else None,
        size_standard=groesse_standard,
        size_uhd=groesse_uhd,
    )


def _dateigroessen(eintrag: dict[str, Any]) -> tuple[int, int]:
    """Belegter Platz in Bytes, getrennt nach Standard und 4K.

    Plex haengt an jede ``Media``-Fassung eine oder mehrere ``Part``-Dateien,
    und erst dort steht die Groesse. Die Schleife laeuft ohnehin schon ueber
    ``Media`` (fuer die Aufloesung) - es kostet also keine einzige zusaetzliche
    Abfrage, eine Ebene tiefer zu schauen.

    Warum getrennt: 1080p und 4K sind zwei Dateien und werden getrennt
    verbucht. Wer beide haelt, belegt beides.

    Bei **Serien** kommt hier null heraus: Der Serien-Eintrag traegt gar keine
    ``Media``-Liste, die Dateien haengen an den Folgen. Das ist bekannt und
    bewusst nicht behoben - es braeuchte eine Abfrage je Serie.
    """
    standard = 0
    uhd = 0
    for medium in eintrag.get("Media") or []:
        summe = 0
        for teil in medium.get("Part") or []:
            groesse = teil.get("size")
            if isinstance(groesse, (int, float)) and groesse > 0:
                summe += int(groesse)
        if summe == 0:
            continue
        if str(medium.get("videoResolution") or "").lower() == "4k":
            uhd += summe
        else:
            standard += summe
    return standard, uhd


class PlexServer(MediaServer):
    provider = "plex"
    label = "Plex"
    login_kind = "pin"

    def __init__(
        self, settings: "AppSettings", verbindung: "Verbindung | None" = None
    ) -> None:
        # ``verbindung`` ist die Zeile **dieses** Anbieters. Ohne sie greifen
        # die Einzelwerte - und die gehoeren immer der *ersten* Verbindung.
        # Solange nur einer verbunden ist, ist das dieselbe Sache; sobald zwei
        # verbunden sind, waere es die Adresse des falschen Servers.
        self.base_url = (verbindung.url if verbindung else settings.mediaserver_url).rstrip("/")
        self.token = verbindung.token if verbindung else settings.mediaserver_token
        self.machine_id = (
            verbindung.machine_id if verbindung else settings.mediaserver_machine_id
        )
        # Die Geraetekennung gehoert der Installation, nicht dem Server - sie
        # steht deshalb weiter in den Einstellungen und nicht an der Verbindung.
        self.client_identifier = settings.mediaserver_client_identifier

    # --- Einrichtung -------------------------------------------------------

    async def verify(self) -> dict[str, Any]:
        """Den Server direkt fragen, wer er ist.

        Faellt der Server aus, ist das kein Grund, die Anmeldung zu sperren -
        die laeuft ueber plex.tv. Deshalb ist das hier nur der Verbindungstest.
        """
        if not self.base_url:
            raise MediaServerError("Es ist kein Plex-Server ausgewählt.")

        client = await http_client()
        try:
            response = await client.get(
                f"{self.base_url}/identity",
                headers=self._kopfzeilen(self.token),
            )
        except httpx.TimeoutException as exc:
            raise MediaServerError("Der Plex-Server antwortet nicht (Zeitüberschreitung).") from exc
        except httpx.HTTPError as exc:
            raise MediaServerError(
                f"Der Plex-Server ist unter {self.base_url} nicht erreichbar."
            ) from exc

        if response.status_code in (401, 403):
            raise MediaServerError("Der Plex-Server hat die Anmeldung nicht akzeptiert.", 401)
        if response.status_code >= 400:
            raise MediaServerError(
                f"Der Plex-Server meldet einen Fehler (HTTP {response.status_code}).",
                response.status_code,
            )

        try:
            container = (response.json() or {}).get("MediaContainer") or {}
        except ValueError as exc:
            raise MediaServerError(
                f"Die Antwort von {self.base_url} ist unerwartet. "
                "Zeigt die Adresse wirklich auf einen Plex-Server?"
            ) from exc

        return {
            "name": container.get("friendlyName") or "Plex",
            "version": container.get("version") or "",
            "machine_id": container.get("machineIdentifier") or "",
        }

    async def list_servers(self, provider_token: str) -> list[ServerCandidate]:
        return await plextv.list_servers(self.client_identifier, provider_token)

    async def probe(self, url: str, provider_token: str) -> bool:
        """Antwortet unter dieser Adresse wirklich ein Plex-Server?

        Kurzer Zeitrahmen mit Absicht: Beim Einrichten werden mehrere Adressen
        nacheinander durchprobiert, und eine unerreichbare soll den Vorgang
        nicht minutenlang aufhalten.
        """
        if not url:
            return False
        client = await http_client()
        try:
            antwort = await client.get(
                f"{url.rstrip('/')}/identity",
                headers=self._kopfzeilen(provider_token),
                timeout=5.0,
            )
        except httpx.HTTPError:
            return False
        return antwort.status_code < 400

    # --- Anmeldung ---------------------------------------------------------

    async def begin_login(self) -> LoginChallenge:
        return await plextv.begin_login(self.client_identifier)

    async def poll_login(self, ref: str, code: str = "") -> str | None:
        return await plextv.poll_login(self.client_identifier, ref, code)

    async def account_for_token(self, provider_token: str) -> ExternalAccount:
        return await plextv.account_for_token(self.client_identifier, provider_token)

    async def user_has_server_access(self, provider_token: str) -> bool:
        return await plextv.has_server_access(
            self.client_identifier, provider_token, self.machine_id
        )

    # --- Merkliste ----------------------------------------------------------
    #
    # Beide Wege gehen ueber plex.tv und **nicht** ueber den Server daheim: Die
    # Merkliste liegt bei Plex in der Cloud und gehoert dem Konto, nicht der
    # Bibliothek. Deshalb auch das fremde Token als Parameter - der hinterlegte
    # Zugang des Administrators taugt hier nicht.

    async def watchlist(self, provider_token: str) -> list[WatchlistItem]:
        return await plextv.watchlist(self.client_identifier, provider_token)

    async def watchlist_ids(
        self, provider_token: str, items: list[WatchlistItem]
    ) -> list[WatchlistItem]:
        return await plextv.watchlist_ids(self.client_identifier, provider_token, items)

    # --- Bibliothek ---------------------------------------------------------

    def _kopfzeilen(self, token: str) -> dict[str, str]:
        """Kopfzeilen fuer eine Abfrage an den Server selbst.

        **Dieselben wie fuer plex.tv**, und das ist der Punkt: Vorher gingen
        hier nur ``Accept`` und ``X-Plex-Token`` raus. Mit dem Zugang des
        Eigentuemers faellt das nicht auf - der darf ohnehin alles. Ein
        **geteiltes** Konto weist der Server ohne Client-Kennung dagegen mit
        401 ab, obwohl das Token gueltig ist und die Bibliotheks-Freigabe
        besteht.

        Gemeldet wurde das als "Plex nimmt das Token nicht mehr an", und eine
        Neuanmeldung half nicht: Das frische Token wurde genauso abgewiesen.
        Nicht das Token war das Problem, sondern dass sich der Aufrufer nicht
        zu erkennen gab.

        ``plextv._headers`` baut dieselbe Liste - bewusst wiederverwendet,
        damit nicht zwei Stellen dasselbe unterschiedlich beantworten.
        """
        return plextv._headers(self.client_identifier, token)

    async def _server(
        self, pfad: str, params: dict[str, Any] | None = None, token: str | None = None
    ) -> dict[str, Any]:
        """Eine Abfrage an den Server selbst (nicht an plex.tv).

        ``token`` erlaubt, mit einem **fremden** Zugang zu fragen - der Zaehler
        am Titel gilt naemlich immer fuer das Konto, dessen Token die Frage
        stellt. Ohne Angabe gilt der hinterlegte Zugang des Administrators.
        """
        if not self.base_url:
            raise MediaServerError("Es ist kein Plex-Server ausgewählt.")

        client = await http_client()
        try:
            antwort = await client.get(
                f"{self.base_url}{pfad}",
                headers=self._kopfzeilen(token or self.token),
                params=params,
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise MediaServerError("Der Plex-Server antwortet nicht (Zeitüberschreitung).") from exc
        except httpx.HTTPError as exc:
            raise MediaServerError(
                f"Der Plex-Server ist unter {self.base_url} nicht erreichbar."
            ) from exc

        if antwort.status_code in (401, 403):
            # **Den echten Kode ins Protokoll**, nicht nur die Ersetzung unten.
            #
            # Ohne diese Zeile war eine Fehlersuche kaum moeglich: Gemeldet
            # wurde "Token abgelaufen", und ob der Server in Wahrheit 401
            # ("wer bist du") oder 403 ("du darfst hier nicht") gesagt hatte,
            # stand nirgends. Das sind zwei verschiedene Probleme mit zwei
            # verschiedenen Loesungen.
            logger.warning(
                "Plex server answered %s with HTTP %s (body: %s)",
                pfad,
                antwort.status_code,
                " ".join(antwort.text[:200].split()),
            )
            # Beide werden bewusst als 401 weitergereicht: Fuer den Aufrufer
            # ist die Handlung dieselbe. **Bekannte Ungenauigkeit** - taucht im
            # Protokoll je ein 403 auf, gehoert das hier auseinandergezogen.
            raise MediaServerError("Der Plex-Server hat den Zugang nicht akzeptiert.", 401)
        if antwort.status_code >= 400:
            raise MediaServerError(
                f"Der Plex-Server meldet einen Fehler (HTTP {antwort.status_code}).",
                antwort.status_code,
            )
        try:
            return (antwort.json() or {}).get("MediaContainer") or {}
        except ValueError as exc:
            raise MediaServerError("Die Antwort des Plex-Servers ist unerwartet.") from exc

    async def library_index(self) -> list[LibraryItem]:
        """Alles, was in den Film- und Serien-Bibliotheken liegt.

        Gebraucht, um Titel zu erkennen, die **nicht** über Radarr/Sonarr kamen -
        von Hand kopierte Dateien etwa, oder alles aus der Zeit vor dem
        *arr-Aufbau. Ohne das zeigt Nexview sie als anfragbar an, und jemand
        laedt sie ein zweites Mal herunter.

        Musik- und Fotosammlungen bleiben aussen vor; nach ihnen fragt hier
        niemand.
        """
        return await self._alle_werke()

    async def _alle_werke(self, token: str | None = None) -> list[LibraryItem]:
        """Film- und Serien-Bibliotheken vollstaendig auslesen.

        Mit ``token`` laeuft die Abfrage unter einem fremden Zugang - dann
        gelten ``owner_watched``/``watched_at`` fuer **dessen** Konto. Genau
        darauf baut ``watched_index``.
        """
        abschnitte = (await self._server("/library/sections", token=token)).get("Directory") or []

        werke: list[LibraryItem] = []
        for abschnitt in abschnitte:
            art = abschnitt.get("type")
            if art not in ("movie", "show"):
                continue
            schluessel = abschnitt.get("key")
            if not schluessel:
                continue
            werke.extend(
                await self._abschnitt_lesen(
                    str(schluessel), "movie" if art == "movie" else "tv", token=token
                )
            )
        return werke

    async def watched_index(
        self, provider_token: str, account_id: str = ""
    ) -> list[WatchedRecord]:
        """Der vollstaendige Gesehen-Stand des Kontos hinter diesem Token.

        ``account_id`` wird hier **nicht gebraucht** und bewusst ignoriert:
        Ein Plex-Token gehoert bereits zu genau einem Konto, der Server
        beantwortet damit von selbst "wessen Stand". Der Parameter steht in
        der Schnittstelle, weil Emby es nicht kann - dort gibt es kein
        "/Users/Me", ueber das ein Adapter die Nummer erfragen koennte.

        Der Zaehler am Titel (``viewCount`` bzw. ``viewedLeafCount``) gilt
        immer fuer das Konto, dessen Token die Bibliothek liest - fuer geteilte
        Nutzer also fuer sie selbst. Anders als der Verlauf ist er nicht
        gedeckelt und erfasst auch von Hand als gesehen Markiertes.
        """
        return [
            WatchedRecord(
                account_id="",
                item_key=werk.rating_key or "",
                media_type=werk.media_type,
                watched_at=werk.watched_at,
            )
            for werk in await self._alle_werke(await self._server_token(provider_token))
            if werk.owner_watched and werk.rating_key
        ]

    async def watched_seasons(
        self, provider_token: str, series_keys: list[str], account_id: str = ""
    ) -> list[SeasonWatchedRecord]:
        """Vollstaendig gesehene Staffeln - **eine Abfrage je genannter Serie**.

        Die Staffel-Zaehler (``viewedLeafCount``/``leafCount``) stehen
        verlaesslich nur unter ``/library/metadata/{serie}/children`` - die
        flache Staffel-Liste eines Abschnitts fuehrt sie je nach Plex-Fassung
        nicht (auf dem Server hier: gar nicht). Deshalb gezielt je Serie, und
        der Aufrufer nennt nur die Serien, um die es geht.

        Vollstaendig heisst: jede Folge gesehen, gemessen am Konto des Tokens.
        """
        token = await self._server_token(provider_token)
        gesehen: list[SeasonWatchedRecord] = []
        for schluessel in series_keys:
            try:
                container = await self._server(
                    f"/library/metadata/{schluessel}/children", token=token
                )
            except MediaServerError as fehler:
                # Eine einzelne verschwundene Serie (404) reisst nicht den
                # ganzen Durchlauf um - sie hat dann eben keine Staffel-Augen.
                if fehler.status_code == 404:
                    continue
                raise
            for eintrag in container.get("Metadata") or []:
                nummer = eintrag.get("index")
                folgen = int(eintrag.get("leafCount") or 0)
                davon = int(eintrag.get("viewedLeafCount") or 0)
                if isinstance(nummer, int) and folgen > 0 and davon >= folgen:
                    gesehen.append(
                        SeasonWatchedRecord(item_key=str(schluessel), season=nummer)
                    )
        return gesehen

    async def _server_token(self, konto_token: str) -> str:
        """Vom Konto-Token auf das Zugriffs-Token **fuer diesen Server**.

        Der Server nimmt von einem *geteilten* Konto nur sein eigenes Token an,
        nicht das Konto-Token von der Anmeldung. Beim Eigentuemer sind beide
        gleich - deshalb ist der Unterschied nie aufgefallen, solange nur mit
        dem Zugang des Administrators gelesen wurde.

        Kostet eine Abfrage bei plex.tv je Konto und Durchlauf. Der
        Gesehen-Abgleich laeuft stuendlich; das faellt nicht ins Gewicht.

        Schlaegt die Abfrage fehl, bleibt es beim Konto-Token: Dann scheitert
        der Aufruf danach mit derselben Meldung wie zuvor, statt hier schon mit
        einer anderen - eine Verschlechterung waere das nicht.
        """
        if not self.machine_id:
            return konto_token
        try:
            eigenes = await plextv.server_access_token(
                self.client_identifier, konto_token, self.machine_id
            )
        except MediaServerError:
            return konto_token
        if eigenes is None:
            # Der Server steht nicht in der Liste des Kontos - dann fehlt
            # wirklich die Bibliotheks-Freigabe, und das ist etwas anderes als
            # ein abgelaufenes Token.
            logger.warning(
                "Account has no access to server %s - the library share is missing",
                self.machine_id,
            )
            return konto_token
        if eigenes != konto_token:
            logger.debug(
                "This server has its own access token (shared account), using it"
            )
        return eigenes

    async def list_server_users(self) -> list[ServerUser]:
        konten = (await self._server("/accounts")).get("Account") or []
        gefunden: list[ServerUser] = []
        for konto in konten:
            kennung = konto.get("id")
            if kennung is None:
                continue
            name = (konto.get("name") or "").strip()
            # Namenlose Eintraege sind verwaltete Profile ohne eigenes Konto -
            # ihnen laesst sich kein Nexview-Benutzer zuordnen.
            if not name:
                continue
            gefunden.append(ServerUser(account_id=str(kennung), username=name))
        return gefunden

    async def watched_since(self, since: datetime | None = None) -> list[WatchedRecord]:
        """Was wurde gesehen - je Konto.

        Plex nennt im Verlauf nur seine internen Nummern, keine TMDB-Kennungen.
        Bei einer Folge steht die Serie in ``grandparentKey``; die zaehlt hier,
        denn fuer ein Abzeichen an der Kachel ist "davon schon etwas gesehen"
        die brauchbare Auskunft.

        **Je Konto einzeln abfragen.** Ohne ``accountID`` liefert Plex nur den
        Verlauf des Zugangs, mit dem gefragt wird - der Eigentuemer saehe also
        nur sich selbst. Gemessen an einer echten Installation: 497 Eintraege,
        alle unter der 1. Dazu kommt, dass Plex je Abfrage bei rund 500
        Eintraegen abschneidet; ohne Aufteilung verdraengt ein vielsehender
        Eigentuemer den Verlauf aller anderen vollstaendig.

        ``since`` wird bewusst **hier** ausgewertet und nicht an Plex
        weitergereicht: Der Server nimmt den entsprechenden Parameter zwar an,
        beachtet ihn aber nicht - gemessen an einer echten Installation kam
        gefiltert genau dieselbe Menge zurueck wie ungefiltert.
        """
        try:
            konten = [konto.account_id for konto in await self.list_server_users()]
        except MediaServerError:
            konten = []
        # Ohne Kontenliste bleibt wenigstens der Eigentuemer.
        konten = konten or [EIGENTUEMER_KONTO]

        roh: list[dict[str, Any]] = []
        for konto in konten:
            container = await self._server(
                "/status/sessions/history/all",
                {"sort": "viewedAt:desc", "accountID": konto},
            )
            roh.extend(container.get("Metadata") or [])

        grenze = int(since.timestamp()) if since else None

        gesehen: list[WatchedRecord] = []
        for eintrag in roh:
            wann = eintrag.get("viewedAt")
            if grenze is not None and isinstance(wann, int) and wann <= grenze:
                continue

            art = eintrag.get("type")
            if art == "movie":
                schluessel = str(eintrag.get("ratingKey") or "")
                media_type = "movie"
            elif art == "episode":
                schluessel = _nummer(eintrag.get("grandparentKey"))
                media_type = "tv"
            else:
                continue
            if not schluessel:
                continue

            konto = eintrag.get("accountID")
            if konto is None:
                continue

            gesehen.append(
                WatchedRecord(
                    account_id=str(konto),
                    item_key=schluessel,
                    media_type=media_type,
                    watched_at=(
                        datetime.fromtimestamp(wann, tz=UTC).replace(tzinfo=None)
                        if isinstance(wann, int)
                        else None
                    ),
                )
            )
        return gesehen

    async def _abschnitt_lesen(
        self, schluessel: str, media_type: str, token: str | None = None
    ) -> list[LibraryItem]:
        """Eine Bibliothek seitenweise auslesen.

        Plex liefert auf einen Schlag alles, was drinsteht - bei ein paar
        tausend Titeln ist das eine unangenehm grosse Antwort. Deshalb in
        Haeppchen ueber ``X-Plex-Container-Start``.
        """
        werke: list[LibraryItem] = []
        start = 0
        while True:
            container = await self._server(
                f"/library/sections/{schluessel}/all",
                params={
                    "includeGuids": 1,
                    "X-Plex-Container-Start": start,
                    "X-Plex-Container-Size": SEITENGROESSE,
                },
                token=token,
            )
            eintraege = container.get("Metadata") or []
            for eintrag in eintraege:
                werk = _als_werk(eintrag, media_type)
                if werk is not None:
                    werke.append(werk)

            start += len(eintraege)
            gesamt = int(container.get("totalSize") or container.get("size") or 0)
            if not eintraege or start >= gesamt:
                return werke

    async def laufende_wiedergaben(self) -> list[Wiedergabe]:
        """Was gerade laeuft - aus ``/status/sessions``.

        ⚠️ **Plex liefert von sich aus nur echte Wiedergaben** (gemessen
        30.08.2026: ``MediaContainer.size`` war 0, solange nichts lief). Anders
        als Jellyfin und Emby braucht es hier keinen Filter auf laufende
        Titel - dafuer sieht die Antwort voellig anders aus.
        """
        try:
            # ⚠️ ``_server`` packt ``MediaContainer`` **schon aus**. Der erste
            # Anlauf packte hier ein zweites Mal aus und lieferte deshalb immer
            # eine leere Liste - waehrend Plex einen laufenden Film meldete.
            # Aufgefallen ist es erst beim Vergleich mit der rohen Antwort;
            # die Umwandlungs-Tests konnten es nicht sehen, weil sie den
            # Abrufweg gar nicht durchlaufen.
            behaelter = await self._server("/status/sessions")
        except MediaServerError:
            return []
        eintraege = (behaelter or {}).get("Metadata") or []

        gefunden: list[Wiedergabe] = []
        for eintrag in eintraege:
            if isinstance(eintrag, dict):
                gefunden.append(self._als_wiedergabe(eintrag))
        return gefunden

    def _als_wiedergabe(self, eintrag: dict) -> Wiedergabe:
        spieler = eintrag.get("Player") or {}
        konto = eintrag.get("User") or {}
        sitzung = eintrag.get("Session") or {}
        umrechnen = eintrag.get("TranscodeSession")

        # ⚠️ **Nicht am Vorhandensein der TranscodeSession festmachen.**
        # Gemessen am 30.08.2026 bei laufendem Film: ``videoDecision: "copy"``
        # neben ``audioDecision: "transcode"`` - das Bild wird durchgereicht.
        # Als "Umrechnung" gemeldet waere das ein falscher Alarm ueber eine
        # CPU-Last, die es nicht gibt.
        art = Umrechnung.direkt
        if isinstance(umrechnen, dict):
            art = (
                Umrechnung.ton
                if str(umrechnen.get("videoDecision") or "").lower() == "copy"
                else Umrechnung.bild
            )

        laenge = eintrag.get("duration")
        stand = eintrag.get("viewOffset")
        fortschritt = None
        if isinstance(laenge, int) and laenge > 0 and isinstance(stand, int):
            fortschritt = max(0.0, min(1.0, stand / laenge))

        # ⚠️ Plex nennt hier **keine** TMDB-Nummer - anders als Jellyfin und
        # Emby, die sie unter ``ProviderIds`` mitliefern. Die Bruecke ist der
        # ``ratingKey``, den Nexview beim Bibliotheks-Abgleich ohnehin
        # mitschreibt; aufgeloest wird sie im Dienst, nicht hier.
        return Wiedergabe(
            provider=self.provider,
            konto=str(konto.get("title") or ""),
            konto_id=str(konto.get("id") or ""),
            titel=str(eintrag.get("title") or ""),
            media_type="tv" if eintrag.get("type") == "episode" else "movie",
            serie=str(eintrag.get("grandparentTitle") or ""),
            fortschritt=fortschritt,
            geraet=str(spieler.get("device") or spieler.get("title") or ""),
            anwendung=str(spieler.get("product") or ""),
            pausiert=str(spieler.get("state") or "").lower() == "paused",
            umrechnung=art,
            grund="",
            beschleunigung="",
            bandbreite=(
                sitzung.get("bandwidth")
                if isinstance(sitzung.get("bandwidth"), int)
                else None
            ),
            tmdb_id=None,
        )
