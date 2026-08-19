"""Plex als Media-Server.

Der Adapter ist bewusst duenn: die Anmeldung liegt bei plex.tv (``plextv.py``),
hier steht nur, was den Server selbst betrifft - und die Uebersetzung in die
anbieter-neutralen Formen aus ``base.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from . import plextv
from .base import (
    ExternalAccount,
    LoginChallenge,
    MediaServer,
    MediaServerError,
    ServerCandidate,
    http_client,
)

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from ..settings_service import AppSettings


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
