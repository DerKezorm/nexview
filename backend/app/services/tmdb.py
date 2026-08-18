"""Zugriff auf die TMDB-API.

Der Browser spricht nie direkt mit TMDB - alle Aufrufe laufen ueber diesen
Service, damit der API-Key den Server nicht verlaesst.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .filters import MIN_VOTES_FOR_RATING, DiscoverFilters

BASE_URL = "https://api.themoviedb.org/3"
# Feste Bild-Basis. TMDB liefert sie zwar unter /configuration, sie ist aber
# seit Jahren unveraendert - so sparen wir einen Aufruf pro Seitenaufbau.
IMAGE_BASE = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
BACKDROP_SIZE = "w1280"
# Portraits der Besetzung und Standbilder der Folgen - deutlich kleiner als
# Poster, weil sie nur als Streifen bzw. Vorschau erscheinen.
PROFILE_SIZE = "w185"
STILL_SIZE = "w300"

TIMEOUT = httpx.Timeout(12.0, connect=6.0)

# Wie viele TMDB-Abfragen gleichzeitig laufen duerfen. TMDB vertraegt mehr,
# aber so bleibt der Server auch bei mehreren Nutzern ruhig.
MAX_PARALLEL_REQUESTS = 8

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_request_slots = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)


async def _http() -> httpx.AsyncClient:
    """Gemeinsam genutzte HTTP-Verbindung.

    Wichtig fuer die Geschwindigkeit: Ein neuer Client pro Aufruf muesste
    jedes Mal Verbindung und Verschluesselung neu aushandeln - das kostete
    rund eine Sekunde pro Abfrage. Mit einem wiederverwendeten Client bleibt
    die Verbindung offen und die Folgeaufrufe dauern nur noch Millisekunden.
    """
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    base_url=BASE_URL,
                    timeout=TIMEOUT,
                    headers={"Accept": "application/json"},
                    limits=httpx.Limits(
                        max_connections=MAX_PARALLEL_REQUESTS,
                        max_keepalive_connections=MAX_PARALLEL_REQUESTS,
                        keepalive_expiry=60.0,
                    ),
                )
    return _client


async def close_http_client() -> None:
    """Beim Herunterfahren der Anwendung aufraeumen."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


class TmdbError(Exception):
    """Fehler beim Zugriff auf TMDB - mit einer fuer Menschen lesbaren Meldung."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def image_url(path: str | None, size: str = POSTER_SIZE) -> str | None:
    return f"{IMAGE_BASE}/{size}{path}" if path else None


class TmdbClient:
    def __init__(self, api_key: str, language: str = "de", region: str = "DE") -> None:
        self.api_key = api_key
        # TMDB erwartet Sprachcodes wie "de-DE".
        self.language = f"{language}-{region}" if "-" not in language else language
        self.region = region

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query: dict[str, Any] = {"api_key": self.api_key, "language": self.language}
        query.update({k: v for k, v in (params or {}).items() if v is not None})

        client = await _http()
        try:
            async with _request_slots:
                response = await client.get(path, params=query)
        except httpx.TimeoutException as exc:
            raise TmdbError("TMDB antwortet nicht (Zeitüberschreitung).") from exc
        except httpx.HTTPError as exc:
            raise TmdbError("TMDB ist nicht erreichbar. Besteht eine Internetverbindung?") from exc

        if response.status_code == 401:
            raise TmdbError("Der TMDB API-Key wurde nicht akzeptiert.", 401)
        if response.status_code == 404:
            raise TmdbError("Dieser Titel ist bei TMDB nicht (mehr) vorhanden.", 404)
        if response.status_code == 429:
            raise TmdbError("TMDB bremst gerade zu viele Anfragen aus. Bitte kurz warten.", 429)
        if response.status_code >= 400:
            raise TmdbError(f"TMDB meldet einen Fehler (HTTP {response.status_code}).",
                            response.status_code)

        return response.json()

    # --- Verbindungstest ---------------------------------------------------

    async def verify(self) -> None:
        """Prueft, ob der API-Key funktioniert. Wirft sonst einen TmdbError."""
        await self._get("/configuration")

    # --- Listen ------------------------------------------------------------

    async def discover(
        self, media_type: str, filters: DiscoverFilters, sort_by: str
    ) -> dict[str, Any]:
        is_movie = media_type == "movie"

        # Bei Filmen kann nach dem Datum in der gewaehlten Region gefiltert
        # werden ("release_date" zusammen mit "region"). Ohne den Schalter
        # zaehlt das weltweite Ersterscheinungsdatum.
        if is_movie:
            date_field = "release_date" if filters.released_in_region else "primary_release_date"
        else:
            date_field = "first_air_date"

        params: dict[str, Any] = {
            "page": filters.page,
            "sort_by": sort_by,
            "include_adult": "false",
            f"{date_field}.gte": filters.date_from,
            f"{date_field}.lte": filters.date_to,
            "with_original_language": filters.language,
            "with_genres": filters.genre_id,
            "with_runtime.gte": filters.min_runtime,
            "vote_average.gte": filters.min_rating,
            "vote_count.gte": self._min_votes(filters, sort_by),
        }

        if is_movie:
            params["region"] = filters.region
            # Kino- und digitale Veroeffentlichung
            params["with_release_type"] = "2|3"
            params["with_companies"] = filters.studio_id
        else:
            params["watch_region"] = filters.region

        return await self._get(f"/discover/{media_type}", params)

    @staticmethod
    def _min_votes(filters: DiscoverFilters, sort_by: str) -> int | None:
        """Mindestanzahl Stimmen - haelt Zufallstreffer aus der Liste.

        Es gilt immer die schaerfste der Regeln, damit sich die Filter nicht
        gegenseitig aufheben.
        """
        kandidaten = [
            filters.min_votes,
            MIN_VOTES_FOR_RATING if filters.min_rating is not None else None,
            1 if filters.hide_unrated else None,
            # Beim Sortieren nach Bewertung stuenden sonst 10/10 aus einer
            # einzigen Stimme ganz oben.
            5 if sort_by.startswith("vote_average") else None,
        ]
        gesetzt = [wert for wert in kandidaten if wert is not None]
        return max(gesetzt) if gesetzt else None

    async def search(self, media_type: str, query: str, page: int = 1) -> dict[str, Any]:
        return await self._get(
            f"/search/{media_type}",
            {"query": query, "page": page, "include_adult": "false"},
        )

    async def trending(
        self, media_type: str, zeitraum: str = "week", page: int = 1
    ) -> dict[str, Any]:
        """Was gerade oft angesehen wird.

        Bewusst "trending" statt "popular": beliebt ist bei TMDB eine ueber
        Jahre gewachsene Groesse, in der immer dieselben Klassiker oben stehen.
        Trending zeigt, was diese Woche tatsaechlich gefragt ist - genau das,
        was eine Startseite interessant macht.
        """
        return await self._get(f"/trending/{media_type}/{zeitraum}", {"page": page})

    async def genres(self, media_type: str) -> dict[int, str]:
        data = await self._get(f"/genre/{media_type}/list")
        return {item["id"]: item["name"] for item in data.get("genres", [])}

    # --- Einzeltitel -------------------------------------------------------

    async def detail(
        self, media_type: str, tmdb_id: int, *, ausfuehrlich: bool = False
    ) -> dict[str, Any]:
        """Details inklusive Altersfreigabe - und bei Serien der TVDB-Id.

        ``append_to_response`` buendelt mehrere Abfragen in einem Aufruf.
        Die TVDB-Id wird spaeter fuer Sonarr gebraucht.

        ``ausfuehrlich`` holt zusaetzlich Besetzung, Schlagworte und
        Empfehlungen - alles, was die Detailseite braucht. Bewusst nicht immer:
        die Antwort wird dadurch um ein Vielfaches groesser, und fuer die
        Kacheln in den Listen ist davon nichts noetig.
        """
        if media_type == "movie":
            extras = "release_dates,watch/providers"
        else:
            extras = "content_ratings,external_ids,watch/providers"

        params: dict[str, Any] = {"append_to_response": extras}

        if ausfuehrlich:
            extras += ",credits,keywords,recommendations,videos"
            params["append_to_response"] = extras
            # Ohne diese Zeile liefert TMDB nur Videos in der angefragten
            # Sprache - und deutsche Trailer fehlen bei den meisten Titeln.
            # "null" holt zusaetzlich die sprachneutralen Eintraege.
            params["include_video_language"] = f"{self.language.split('-')[0]},en,null"

        return await self._get(f"/{media_type}/{tmdb_id}", params)

    async def keyword(self, keyword_id: int) -> dict[str, Any]:
        """Name eines Schlagworts - fuer die Ueberschrift der Ergebnisseite."""
        return await self._get(f"/keyword/{keyword_id}")

    async def company(self, company_id: int) -> dict[str, Any]:
        """Name eines Studios - fuer die Ueberschrift der Ergebnisseite."""
        return await self._get(f"/company/{company_id}")

    async def browse(self, media_type: str, params: dict[str, Any], page: int = 1) -> dict[str, Any]:
        """Freie Discover-Abfrage - fuer Schlagwort- und Studio-Listen.

        Bewusst neben ``discover``: dort steckt die ganze Filterleiste mit
        Zeitraum und Mindestbewertung. Hier zaehlt nur ein einziges Merkmal,
        und ein voreingestellter Zeitraum wuerde die meisten Treffer
        wegschneiden.
        """
        return await self._get(
            f"/discover/{media_type}",
            {
                **params,
                "page": page,
                "include_adult": "false",
                "sort_by": "popularity.desc",
            },
        )

    async def recommendations(
        self, media_type: str, tmdb_id: int, page: int = 1
    ) -> dict[str, Any]:
        """Was TMDB Leuten empfiehlt, denen dieser Titel gefaellt.

        Eigene, schlanke Abfrage - die ausfuehrliche Detailabfrage haette die
        Empfehlungen zwar auch dabei, braeuchte aber Besetzung, Schlagworte und
        Videos gleich mit.
        """
        return await self._get(
            f"/{media_type}/{tmdb_id}/recommendations", {"page": page}
        )

    async def similar(self, media_type: str, tmdb_id: int, page: int = 1) -> dict[str, Any]:
        """Titel mit denselben Schlagworten und Genres.

        Eine andere Liste als ``recommendations``: die beruht auf dem
        Verhalten anderer Leute, diese auf den Eigenschaften des Titels.
        Gebraucht wird sie fuer "Neue Auswahl" - viele Titel haben nur eine
        Handvoll Empfehlungen, und ohne zweite Quelle waere nach einem
        Druck auf den Knopf Schluss.
        """
        return await self._get(f"/{media_type}/{tmdb_id}/similar", {"page": page})

    async def person(self, person_id: int) -> dict[str, Any]:
        """Eine Person samt ihrer Filme und Serien."""
        return await self._get(
            f"/person/{person_id}", {"append_to_response": "combined_credits"}
        )

    async def season(self, tmdb_id: int, season_number: int) -> dict[str, Any]:
        """Eine einzelne Staffel mit ihren Folgen."""
        return await self._get(f"/tv/{tmdb_id}/season/{season_number}")

    async def details(self, media_type: str, tmdb_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Mehrere Titel parallel abrufen.

        Einzelne Ausfaelle werden einmal wiederholt: sonst fehlten auf
        vereinzelten Kacheln Laufzeit und Altersfreigabe, ohne dass man den
        Grund saehe.
        """
        if not tmdb_ids:
            return {}

        async def fetch_one(tmdb_id: int) -> dict[str, Any] | None:
            for attempt in (1, 2):
                try:
                    return await self.detail(media_type, tmdb_id)
                except TmdbError as error:
                    # Bei "gibt es nicht" oder abgelehntem Key hilft kein zweiter Versuch.
                    if error.status_code in (401, 404) or attempt == 2:
                        return None
            return None

        results = await asyncio.gather(*(fetch_one(tmdb_id) for tmdb_id in tmdb_ids))
        return {
            tmdb_id: result
            for tmdb_id, result in zip(tmdb_ids, results, strict=True)
            if result is not None
        }


# --- Auswertung der Rohdaten ----------------------------------------------


def extract_certification(detail: dict[str, Any], media_type: str, region: str) -> str | None:
    """Altersfreigabe fuer die eingestellte Region herauslesen (z. B. FSK)."""
    if media_type == "movie":
        for entry in detail.get("release_dates", {}).get("results", []):
            if entry.get("iso_3166_1") != region:
                continue
            for release in entry.get("release_dates", []):
                value = (release.get("certification") or "").strip()
                if value:
                    return value
        return None

    for entry in detail.get("content_ratings", {}).get("results", []):
        if entry.get("iso_3166_1") == region:
            value = (entry.get("rating") or "").strip()
            if value:
                return value
    return None


def extract_runtime(detail: dict[str, Any], media_type: str) -> int | None:
    if media_type == "movie":
        runtime = detail.get("runtime")
        return runtime if isinstance(runtime, int) and runtime > 0 else None

    runtimes = detail.get("episode_run_time") or []
    return runtimes[0] if runtimes and isinstance(runtimes[0], int) else None


def extract_tvdb_id(detail: dict[str, Any]) -> int | None:
    tvdb_id = detail.get("external_ids", {}).get("tvdb_id")
    return tvdb_id if isinstance(tvdb_id, int) else None
