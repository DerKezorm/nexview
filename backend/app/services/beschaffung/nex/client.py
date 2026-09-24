"""Zugriff auf nexcrates ``/api/v1`` (Vertrag 1, Stand V5).

Nach dem Vorbild von nexbeat (``nexbeat/backend/app/services/nexcrate.py``),
gemessen am 22.09.2026 gegen eine Wegwerf-nexcrate 0.1.0
(``homelab/nexcrate-pruefstand/nexview-anbindung/messung-nexview.md``).

Der Client kennt nur die Adressen und die Fehlerkoerbe. Was die Antworten
bedeuten, steht in ``mapping.py``; was Nexview damit tut, im Weg
(``weg.py``).

⚠️ **Der Schluessel reist nur in der Kopfzeile** (N1), nie in der Adresse und
nie ins Protokoll. ``X-Request-Id`` reicht Nexviews Vorgangsnummer weiter
(N6), damit ein Vorgang ueber beide Anwendungen verfolgbar bleibt.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ... import http_log, logs
from . import fehler
from .fehler import NexcrateError

logger = logging.getLogger("nexview.nexcrate")

TIMEOUT = httpx.Timeout(20.0, connect=6.0)
#: Ein unbekannter Titel kostet nexcrate eine Abfrage bei TMDB; eine Serie mit
#: vielen Staffeln laenger. Nur fuer Anfragen und die Vorschau.
LANGSAM = httpx.Timeout(90.0, connect=6.0)
#: Der Strom schickt alle 15 s ein Lebenszeichen. Kommt eine Minute nichts, ist er tot.
STROM_TIMEOUT = httpx.Timeout(connect=6.0, read=60.0, write=10.0, pool=10.0)
MAX_PARALLEL_REQUESTS = 6

#: Hoechstens so viele Eintraege nimmt ``titles/lookup`` je Aufruf (gemessen).
LOOKUP_STAPEL = 100
#: ``titles/why`` nimmt hoechstens 50 (Bauplan Abschnitt 3.1).
WARUM_STAPEL = 50
#: Der Kalender antwortet fuer hoechstens 100 Tage am Stueck (gemessen:
#: ``invalid_input`` mit ``fields: ["to"]``).
KALENDER_TAGE = 100
#: Die Rechte, um die Nexview beim Koppeln bittet. ``operate`` braucht es fuer
#: die Aktionen an haengenden Downloads und den Papierkorb.
RECHTE = ["read", "request", "operate"]

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

#: Nur fuer Tests: ein Transport, der statt des Netzes antwortet. So laeuft
#: der echte Client - Kopfzeilen, Fehlerform, Stapel, Stromformat - gegen die
#: gemessenen Antworten (``tests/beschaffung/fake_nexcrate.py``).
_transport: httpx.AsyncBaseTransport | None = None


def use_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Den Transport setzen und die offene Verbindung verwerfen."""
    global _transport, _client
    _transport = transport
    _client = None


async def _http() -> httpx.AsyncClient:
    """Gemeinsame Verbindung - ein neuer Client kostet Sekunden (siehe arr/client.py)."""
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    transport=_transport,
                    timeout=TIMEOUT,
                    headers={"Accept": "application/json"},
                    event_hooks=http_log.event_hooks("nexcrate"),
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


class NexcrateClient:
    """Eine nexcrate-Installation unter einer Adresse."""

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    # -- Transport ---------------------------------------------------------

    def _url(self, pfad: str) -> str:
        return f"{self.base_url}/api/v1{pfad}"

    def kopfzeilen(self, weitere: dict[str, str] | None = None) -> dict[str, str]:
        kopf = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        nummer = logs.current_request_id()
        if nummer:
            kopf["X-Request-Id"] = nummer
        return {**kopf, **(weitere or {})}

    async def _request(
        self,
        method: str,
        pfad: str,
        *,
        params: Any = None,
        json_body: Any = None,
        timeout: httpx.Timeout | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        client = await _http()
        url = self._url(pfad)
        optionen: dict[str, Any] = {"params": params, "headers": self.kopfzeilen(headers)}
        if json_body is not None:
            optionen["json"] = json_body
        try:
            antwort = await client.request(
                method, url, timeout=timeout if timeout is not None else TIMEOUT, **optionen
            )
        except httpx.TimeoutException as exc:
            http_log.unreachable("nexcrate", method, url, exc)
            raise NexcrateError("nexcrate_timeout", ungewiss=True) from exc
        except httpx.HTTPError as exc:
            http_log.unreachable("nexcrate", method, url, exc)
            raise NexcrateError("nexcrate_unreachable", url=self.base_url) from exc
        if antwort.status_code >= 400:
            raise fehler.aus_antwort(antwort, pfad)
        if not antwort.content:
            return None
        try:
            return antwort.json()
        except ValueError as exc:
            raise NexcrateError("nexcrate_unexpected_answer") from exc

    # -- Vertrag und Fassungen ---------------------------------------------

    async def system(self) -> dict[str, Any]:
        """Version, Fähigkeiten, ``installation_id``, Sprünge, Update (N4)."""
        return await self._request("GET", "/system") or {}

    async def versions(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Die Fassungen je Medienart, mit Bereitschaft und Gründen (N9, N10)."""
        params = {"kind": kind} if kind else None
        data = await self._request("GET", "/versions", params=params) or {}
        return list(data.get("items") or [])

    async def states(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/states") or {}
        return list(data.get("items") or [])

    # -- Bestand lesen -----------------------------------------------------

    async def titles(self, *, after: int, kind: str, limit: int = 500) -> dict[str, Any]:
        """Der Bestand über die Änderungsmarke (N13).

        ⚠️ ``after`` über ``latest`` antwortet still leer, nicht ``410``
        (nexbeat-Befund 11). Wer die Marke hält, vergleicht selbst mit
        ``latest`` und liest sonst ganz.
        """
        return await self._request(
            "GET", "/titles", params={"after": after, "kind": kind, "limit": limit}
        ) or {}

    async def lookup(self, items: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Viele Titel auf einmal, Antwort in derselben Reihenfolge (N12)."""
        gefunden: list[dict[str, Any]] = []
        for start in range(0, len(items), LOOKUP_STAPEL):
            teil = items[start : start + LOOKUP_STAPEL]
            data = await self._request("POST", "/titles/lookup", json_body={"items": teil}) or {}
            gefunden.extend(data.get("items") or [])
        return gefunden

    async def title(self, kind: str, ref: str) -> dict[str, Any] | None:
        """Ein Titel mit allem, was er hat - bei Serien samt Staffeln.

        ``None``, wenn nexcrate ihn nicht kennt; alles andere fliegt weiter.
        """
        try:
            return await self._request("GET", f"/titles/{kind}/{ref}")
        except NexcrateError as error:
            if error.code == "nexcrate_title_unknown":
                return None
            raise

    async def season(self, ref: str, season: int) -> dict[str, Any] | None:
        """Eine Staffel je Folge; ``None``, wenn es sie nicht gibt."""
        try:
            return await self._request("GET", f"/titles/series/{ref}/seasons/{season}")
        except NexcrateError as error:
            if error.code in ("nexcrate_title_unknown", "nexcrate_season_unknown"):
                return None
            raise

    async def why(self, items: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Warum ein Titel noch nicht da ist, im Stapel (N28)."""
        gefunden: list[dict[str, Any]] = []
        for start in range(0, len(items), WARUM_STAPEL):
            teil = items[start : start + WARUM_STAPEL]
            data = await self._request("POST", "/titles/why", json_body={"items": teil}) or {}
            gefunden.extend(data.get("items") or [])
        return gefunden

    async def history(self, kind: str, ref: str, *, limit: int = 50) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", f"/titles/{kind}/{ref}/history", params={"limit": limit}
        ) or {}
        return list(data.get("items") or [])

    async def calendar(self, von: str, bis: str, kind: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"from": von, "to": bis}
        if kind:
            params["kind"] = kind
        data = await self._request("GET", "/calendar", params=params) or {}
        return list(data.get("items") or [])

    async def ratings(self, items: list[dict[str, str]]) -> dict[str, Any]:
        return await self._request("POST", "/ratings", json_body={"items": items}) or {}

    async def rating(self, kind: str, ref: str) -> dict[str, Any]:
        return await self._request("GET", f"/ratings/{kind}/{ref}") or {}

    # -- Platz, Gesundheit, Downloads --------------------------------------

    async def storage(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/storage") or {}
        return list(data.get("items") or [])

    async def health(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/health") or {}
        return list(data.get("items") or [])

    async def queue(self, kind: str | None = None) -> list[dict[str, Any]]:
        params = {"kind": kind} if kind else None
        data = await self._request("GET", "/queue", params=params) or {}
        return list(data.get("items") or [])

    async def problems(self, kind: str | None = None) -> list[dict[str, Any]]:
        params = {"kind": kind} if kind else None
        data = await self._request("GET", "/problems", params=params) or {}
        return list(data.get("items") or [])

    async def download_files(self, download_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/downloads/{download_id}/files") or {}

    async def download_action(
        self, download_id: int, aktion: str, body: dict[str, Any] | None = None
    ) -> Any:
        return await self._request("POST", f"/downloads/{download_id}/{aktion}", json_body=body or {})

    async def recycle_bin(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/recycle-bin") or {}
        return list(data.get("items") or [])

    async def restore(self, entry_id: int) -> Any:
        return await self._request("POST", f"/recycle-bin/{entry_id}/restore", json_body={})

    # -- Schreiben (Scheibe 6) ---------------------------------------------

    async def request(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/requests", json_body=body, timeout=LANGSAM) or {}

    async def withdraw(self, kind: str, ref: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/titles/{kind}/{ref}/withdraw", json_body=body, timeout=LANGSAM
        ) or {}

    async def monitoring(self, kind: str, ref: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"/titles/{kind}/{ref}/monitoring", json_body=body) or {}

    async def search(self, kind: str, ref: str) -> dict[str, Any]:
        return await self._request("POST", f"/titles/{kind}/{ref}/search", json_body={}) or {}

    # -- Ereignisse --------------------------------------------------------

    async def events(self, *, after: int, limit: int = 200) -> dict[str, Any]:
        return await self._request("GET", "/events", params={"after": after, "limit": limit}) or {}

    async def stream(self, after: int) -> AsyncIterator[dict[str, Any]]:
        """Die Ereignisse als Server-Sent Events, ab ``after`` (N31).

        nexcrate beendet den Strom nach einer Stunde; der Aufrufer verbindet
        dann sofort neu.
        """
        client = await _http()
        url = self._url("/events/stream")
        try:
            async with client.stream(
                "GET",
                url,
                params={"after": after},
                headers=self.kopfzeilen(),
                timeout=STROM_TIMEOUT,
            ) as antwort:
                if antwort.status_code >= 400:
                    await antwort.aread()
                    raise fehler.aus_antwort(antwort, "/events/stream")
                zeilen: list[str] = []
                async for zeile in antwort.aiter_lines():
                    if zeile.startswith("data:"):
                        zeilen.append(zeile[5:].lstrip())
                    elif zeile == "" and zeilen:
                        roh = "\n".join(zeilen)
                        zeilen = []
                        try:
                            ereignis = json.loads(roh)
                        except ValueError:
                            continue
                        if isinstance(ereignis, dict):
                            yield ereignis
        except httpx.TimeoutException as exc:
            raise NexcrateError("nexcrate_timeout", ungewiss=True) from exc
        except httpx.HTTPError as exc:
            raise NexcrateError("nexcrate_unreachable", url=self.base_url) from exc

    # -- Koppeln (N8) ------------------------------------------------------

    async def pairing_ask(self, app: str, scopes: list[str] | None = None) -> dict[str, Any]:
        """Um einen Schlüssel bitten. Ohne Schlüssel erreichbar - die eine Tür."""
        return await self._request(
            "POST", "/pairing", json_body={"app": app, "scopes": scopes or RECHTE}
        ) or {}

    async def pairing_poll(self, pairing_id: str, secret: str) -> dict[str, Any]:
        """Nachfragen, ob der Betreiber bestätigt hat.

        ⚠️ Der Schlüssel kommt **genau einmal** (``state: confirmed``), danach
        sagt nexcrate ``delivered`` ohne ihn (nexbeat). Wer ihn nicht sofort
        speichert, hat in nexcrate einen toten Schlüssel stehen.
        """
        return await self._request(
            "GET", f"/pairing/{pairing_id}", headers={"X-Pairing-Secret": secret}
        ) or {}
