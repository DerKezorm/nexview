"""Zugriff auf plex.tv - den Ausweis-Schalter von Plex.

Plex trennt zwei Dinge, die man leicht verwechselt:

* **plex.tv** beantwortet "wer bist du" und "auf welche Server darfst du".
  Das passiert hier.
* Der **Plex Media Server** zu Hause beantwortet "was liegt in der Bibliothek".
  Das passiert in ``plex.py``.

Die Anmeldung laeuft ueber eine PIN: Nexview fordert eine an, der Browser
schickt die Person zu Plex, und Nexview fragt so lange nach, bis Plex ein Token
herausgibt. Dieses Token bleibt im Backend - es geht nie an den Browser.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from .base import (
    ExternalAccount,
    LoginChallenge,
    MediaServerError,
    ServerCandidate,
    http_client,
)

BASE_URL = "https://plex.tv/api/v2"
AUTH_URL = "https://app.plex.tv/auth"
PRODUCT = "Nexview"

# Plex fuehrt angemeldete Geraete unter diesen Angaben auf. Sie sind rein
# beschreibend, machen die Liste in den Plex-Kontoeinstellungen aber lesbar.
DEVICE = "Nexview"
PLATFORM = "Web"


def _headers(client_identifier: str, token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT,
        "X-Plex-Client-Identifier": client_identifier,
        "X-Plex-Device": DEVICE,
        "X-Plex-Platform": PLATFORM,
    }
    if token:
        headers["X-Plex-Token"] = token
    return headers


async def _request(
    method: str,
    path: str,
    *,
    client_identifier: str,
    token: str | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    client = await http_client()
    try:
        response = await client.request(
            method,
            f"{BASE_URL}{path}",
            headers=_headers(client_identifier, token),
            params=params,
        )
    except httpx.TimeoutException as exc:
        raise MediaServerError("plex.tv antwortet nicht (Zeitüberschreitung).") from exc
    except httpx.HTTPError as exc:
        raise MediaServerError(
            "plex.tv ist nicht erreichbar. Hat der Server Zugang zum Internet?"
        ) from exc

    if response.status_code in (401, 403):
        raise MediaServerError("Plex hat die Anmeldung nicht akzeptiert.", 401)
    if response.status_code >= 400:
        raise MediaServerError(
            f"plex.tv meldet einen Fehler (HTTP {response.status_code}).",
            response.status_code,
        )

    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise MediaServerError("Die Antwort von plex.tv ist unerwartet.") from exc


async def begin_login(client_identifier: str) -> LoginChallenge:
    """Eine PIN anfordern und die Adresse bauen, die der Browser oeffnet."""
    data = await _request(
        "POST", "/pins", client_identifier=client_identifier, params={"strong": "true"}
    )
    pin_id = data.get("id")
    code = data.get("code")
    if not pin_id or not code:
        raise MediaServerError("Plex hat keine Anmelde-PIN geliefert.")

    frage = urlencode(
        {
            "clientID": client_identifier,
            "code": code,
            "context[device][product]": PRODUCT,
        }
    )
    return LoginChallenge(ref=str(pin_id), code=str(code), auth_url=f"{AUTH_URL}#?{frage}")


async def poll_login(client_identifier: str, ref: str, code: str) -> str | None:
    """Nachsehen, ob die Person bei Plex zugestimmt hat.

    Solange sie das Fenster noch offen hat, liefert Plex ``authToken: null``.
    """
    data = await _request(
        "GET", f"/pins/{ref}", client_identifier=client_identifier, params={"code": code}
    )
    return data.get("authToken") or None


async def account_for_token(client_identifier: str, token: str) -> ExternalAccount:
    """Zu welchem Plex-Konto gehoert dieses Token?"""
    data = await _request("GET", "/user", client_identifier=client_identifier, token=token)

    account_id = data.get("id")
    if account_id is None:
        raise MediaServerError("Plex hat kein Konto zu dieser Anmeldung geliefert.")

    email = (data.get("email") or "").strip() or None
    return ExternalAccount(
        provider="plex",
        account_id=str(account_id),
        username=(data.get("username") or data.get("title") or "").strip() or f"plex-{account_id}",
        email=email,
        thumb=(data.get("thumb") or "").strip() or None,
    )


def _urls(connections: list[dict[str, Any]]) -> tuple[str, ...]:
    """Alle Adressen des Servers, die lokalen zuerst.

    Die lokale ist vorzuziehen - Nexview steht in aller Regel im selben Netz,
    und der Umweg ueber plex.direct kostet nur Zeit. Verlassen darf man sich
    darauf aber nicht: aus einem abgeschotteten Docker-Netz heraus ist sie
    unter Umstaenden gar nicht zu erreichen. Deshalb hier die vollstaendige
    Liste, aus der sich der Aufrufer die erste *funktionierende* aussucht.
    """
    lokal = [str(v["uri"]) for v in connections if v.get("local") and v.get("uri")]
    fern = [str(v["uri"]) for v in connections if not v.get("local") and v.get("uri")]
    return tuple(lokal + fern)


async def list_servers(client_identifier: str, token: str) -> list[ServerCandidate]:
    """Alle Server, auf die dieses Konto Zugriff hat."""
    data = await _request(
        "GET",
        "/resources",
        client_identifier=client_identifier,
        token=token,
        params={"includeHttps": "1"},
    )

    server: list[ServerCandidate] = []
    for eintrag in data or []:
        if "server" not in (eintrag.get("provides") or ""):
            continue
        machine_id = (eintrag.get("clientIdentifier") or "").strip()
        if not machine_id:
            continue
        adressen = _urls(eintrag.get("connections") or [])
        server.append(
            ServerCandidate(
                machine_id=machine_id,
                name=(eintrag.get("name") or "Plex").strip(),
                url=adressen[0] if adressen else "",
                owned=bool(eintrag.get("owned")),
                urls=adressen,
            )
        )
    return server


async def has_server_access(client_identifier: str, token: str, machine_id: str) -> bool:
    """Darf dieses Konto auf genau diesen Server?

    Verglichen wird die dauerhafte Kennung des Servers, nicht seine Adresse.
    Der Eigentuemer findet seinen Server hier genauso wie jemand, mit dem
    geteilt wurde - ein fremdes Konto dagegen nicht.
    """
    if not machine_id:
        return False
    return any(kandidat.machine_id == machine_id for kandidat in await list_servers(client_identifier, token))
