"""Gemeinsame Grundlage fuer den Zugriff auf Radarr und Sonarr.

Beide sprechen dieselbe API-Sprache (``/api/v3`` mit ``X-Api-Key``), nur die
Begriffe unterscheiden sich: Radarr kennt Filme und ``tmdbId``, Sonarr kennt
Serien und ``tvdbId``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from . import http_log

TIMEOUT = httpx.Timeout(15.0, connect=6.0)
MAX_PARALLEL_REQUESTS = 6

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


class ArrError(Exception):
    """Fehler beim Zugriff auf Radarr/Sonarr - mit lesbarer Meldung.

    ``ungewiss`` trennt "hat nicht geklappt" von "wir wissen es nicht".
    Eine Zeitueberschreitung heisst **nicht**, dass nichts passiert ist: Der
    Auftrag kann angekommen und ausgefuehrt worden sein, nur die Antwort kam
    nicht mehr an. Genau so gesehen - Nexview vermerkte "fehlgeschlagen",
    waehrend Sonarr die Serie laengst angelegt hatte und suchte.
    """

    def __init__(
        self, message: str, status_code: int | None = None, ungewiss: bool = False
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.ungewiss = ungewiss


async def _http() -> httpx.AsyncClient:
    """Gemeinsame HTTP-Verbindung (siehe tmdb.py: neue Clients kosten Sekunden)."""
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=TIMEOUT,
                    headers={"Accept": "application/json"},
                    # Eine Zeile pro Aufruf ins Protokoll - siehe http_log.py.
                    event_hooks=http_log.event_hooks("arr"),
                    limits=httpx.Limits(
                        max_connections=MAX_PARALLEL_REQUESTS,
                        max_keepalive_connections=MAX_PARALLEL_REQUESTS,
                        keepalive_expiry=60.0,
                    ),
                )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


class ArrClient:
    """Basis-Client. ``label`` erscheint in Fehlermeldungen ("Radarr"/"Sonarr")."""

    def __init__(self, base_url: str, api_key: str, label: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.label = label

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v3{path}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        client = await _http()
        try:
            response = await client.request(
                method,
                self._url(path),
                headers={"X-Api-Key": self.api_key},
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            http_log.unreachable(self.label.lower(), method, self._url(path), exc)
            raise ArrError(
                f"{self.label} antwortet nicht (Zeitüberschreitung).", ungewiss=True
            ) from exc
        except httpx.HTTPError as exc:
            http_log.unreachable(self.label.lower(), method, self._url(path), exc)
            raise ArrError(
                f"{self.label} ist unter {self.base_url} nicht erreichbar. "
                "Stimmen Adresse und Port?"
            ) from exc

        if response.status_code in (401, 403):
            raise ArrError(f"Der API-Key für {self.label} wurde nicht akzeptiert.", 401)
        if response.status_code == 404:
            raise ArrError(
                f"{self.label} antwortet, kennt diese Adresse aber nicht. "
                f"Zeigt die URL wirklich auf {self.label}?",
                404,
            )
        if response.status_code >= 400:
            raise ArrError(
                f"{self.label} meldet einen Fehler (HTTP {response.status_code}).",
                response.status_code,
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            # Passiert typischerweise, wenn die URL auf eine andere Anwendung zeigt.
            raise ArrError(
                f"Die Antwort von {self.label} ist unerwartet. Zeigt die Adresse "
                f"wirklich auf {self.label}?"
            ) from exc

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", path, json=payload)

    async def put(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("PUT", path, json=payload)

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("DELETE", path, params=params)

    async def system_status(self) -> dict[str, Any]:
        """Fuer den Verbindungstest - liefert u. a. Version und App-Name."""
        return await self.get("/system/status")

    async def quality_profiles(self) -> list[dict[str, Any]]:
        return await self.get("/qualityprofile") or []

    async def root_folders(self) -> list[dict[str, Any]]:
        return await self.get("/rootfolder") or []

    async def ensure_tag(self, label: str) -> int | None:
        """Kennung fuer ein Etikett besorgen - und es notfalls anlegen.

        Damit steht in Radarr/Sonarr, wer den Titel angefordert hat. Klappt es
        nicht, ist das kein Grund, das Hinzufuegen scheitern zu lassen -
        dann gibt es eben kein Etikett.
        """
        wanted = label.strip().lower()
        if not wanted:
            return None

        try:
            for tag in await self.get("/tag") or []:
                if str(tag.get("label", "")).lower() == wanted:
                    return tag.get("id")
            created = await self.post("/tag", {"label": wanted})
        except ArrError:
            return None

        return created.get("id") if isinstance(created, dict) else None
