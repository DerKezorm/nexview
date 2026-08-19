"""Plex als Media-Server.

Der Adapter ist bewusst duenn: die Anmeldung liegt bei plex.tv (``plextv.py``),
hier steht nur, was den Server selbst betrifft - und die Uebersetzung in die
anbieter-neutralen Formen aus ``base.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from . import plextv
from .base import (
    ExternalAccount,
    LibraryItem,
    LoginChallenge,
    MediaServer,
    MediaServerError,
    ServerCandidate,
    ServerUser,
    WatchedRecord,
    http_client,
)

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from ..settings_service import AppSettings

# Wie viele Titel je Abfrage. Gross genug, dass grosse Bibliotheken nicht in
# hundert Abfragen zerfallen, klein genug fuer eine handliche Antwort.
SEITENGROESSE = 500


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

    return LibraryItem(
        media_type=media_type,
        guid=str(eintrag.get("guid") or eintrag.get("ratingKey") or titel),
        rating_key=str(eintrag["ratingKey"]) if eintrag.get("ratingKey") else None,
        owner_watched=gesehen,
        title=titel,
        tmdb_id=zahl("tmdb"),
        tvdb_id=zahl("tvdb"),
        imdb_id=kennungen.get("imdb") or None,
        year=int(eintrag["year"]) if str(eintrag.get("year") or "").isdigit() else None,
    )


class PlexServer(MediaServer):
    provider = "plex"
    label = "Plex"

    def __init__(self, settings: "AppSettings") -> None:
        self.base_url = settings.mediaserver_url.rstrip("/")
        self.token = settings.mediaserver_token
        self.machine_id = settings.mediaserver_machine_id
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
                headers={"Accept": "application/json", "X-Plex-Token": self.token},
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
                headers={"Accept": "application/json", "X-Plex-Token": provider_token},
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

    # --- Bibliothek ---------------------------------------------------------

    async def _server(self, pfad: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Eine Abfrage an den Server selbst (nicht an plex.tv)."""
        if not self.base_url:
            raise MediaServerError("Es ist kein Plex-Server ausgewählt.")

        client = await http_client()
        try:
            antwort = await client.get(
                f"{self.base_url}{pfad}",
                headers={"Accept": "application/json", "X-Plex-Token": self.token},
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
        abschnitte = (await self._server("/library/sections")).get("Directory") or []

        werke: list[LibraryItem] = []
        for abschnitt in abschnitte:
            art = abschnitt.get("type")
            if art not in ("movie", "show"):
                continue
            schluessel = abschnitt.get("key")
            if not schluessel:
                continue
            werke.extend(await self._abschnitt_lesen(str(schluessel), "movie" if art == "movie" else "tv"))
        return werke

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

        ``since`` wird bewusst **hier** ausgewertet und nicht an Plex
        weitergereicht: Der Server nimmt den entsprechenden Parameter zwar an,
        beachtet ihn aber nicht - gemessen an einer echten Installation kam
        gefiltert genau dieselbe Menge zurueck wie ungefiltert.
        """
        container = await self._server(
            "/status/sessions/history/all", {"sort": "viewedAt:desc"}
        )
        grenze = int(since.timestamp()) if since else None

        gesehen: list[WatchedRecord] = []
        for eintrag in container.get("Metadata") or []:
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

    async def _abschnitt_lesen(self, schluessel: str, media_type: str) -> list[LibraryItem]:
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
