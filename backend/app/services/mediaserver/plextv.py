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

import asyncio
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import (
    MAX_PARALLEL_REQUESTS,
    ExternalAccount,
    LoginChallenge,
    MediaServerError,
    ServerCandidate,
    WatchlistItem,
    http_client,
)

BASE_URL = "https://plex.tv/api/v2"
AUTH_URL = "https://app.plex.tv/auth"
# Die Merkliste liegt bei Plex weder auf plex.tv noch auf dem Server daheim,
# sondern bei zwei eigenen Diensten: ``discover`` fuehrt die Liste, ``metadata``
# kennt zu einem Titel die fremden Kennungen (TMDB, IMDb, TVDB).
DISCOVER_URL = "https://discover.provider.plex.tv"
METADATA_URL = "https://metadata.provider.plex.tv"
# Der kontoweite Seh-Verlauf - was Plex ueber alle Server und Discover hinweg
# als gesehen fuehrt, auch fuer Titel, die in keiner Bibliothek liegen. Eine
# GraphQL-Schnittstelle, nicht die uebliche REST-Form von plex.tv.
COMMUNITY_URL = "https://community.plex.tv/api"
PRODUCT = "Nexview"

# Wie viele Eintraege je Seite geholt werden. Plex deckelt selbst; 100 haelt
# die Zahl der Anfragen klein, ohne eine grosse Antwort zu erzwingen.
WATCHLIST_PAGE_SIZE = 100
# Notbremse gegen eine Liste ohne Ende - 50 Seiten sind 5000 Titel.
WATCHLIST_MAX_PAGES = 50

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
    base: str = BASE_URL,
) -> Any:
    client = await http_client()
    try:
        response = await client.request(
            method,
            f"{base}{path}",
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


async def server_access_token(
    client_identifier: str, token: str, machine_id: str
) -> str | None:
    """Das **server-eigene** Zugriffs-Token dieses Kontos fuer diesen Server.

    Plex fuehrt zwei verschiedene Dinge, die beide "Token" heissen:

    * das **Konto-Token** von der Anmeldung - gilt bei plex.tv (Merkliste,
      Server-Liste),
    * je Server ein **Zugriffs-Token** aus ``/resources`` - nur damit nimmt ein
      Server die Anfrage eines *geteilten* Kontos an.

    Beim Eigentuemer sind beide gleich; deshalb faellt der Unterschied mit dem
    Zugang des Administrators nie auf. Ein geteiltes Konto weist der Server
    dagegen mit **401** ab, wenn man ihm das Konto-Token vorlegt - obwohl es
    gueltig ist und die Bibliotheks-Freigabe besteht. Gemeldet wurde das als
    "Plex nimmt das Token nicht mehr an"; eine Neuanmeldung half nicht, weil
    das frische Konto-Token dieselbe falsche Sorte ist.

    Gibt ``None`` zurueck, wenn dieser Server fuer das Konto nicht dabei ist -
    dann fehlt wirklich die Freigabe.
    """
    data = await _request(
        "GET",
        "/resources",
        client_identifier=client_identifier,
        token=token,
        params={"includeHttps": "1"},
    )
    for eintrag in data or []:
        if (eintrag.get("clientIdentifier") or "").strip() != machine_id:
            continue
        eigenes = (eintrag.get("accessToken") or "").strip()
        # Faellt es weg, gilt das Konto-Token - so macht es Plex beim
        # Eigentuemer.
        return eigenes or token
    return None


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


# --------------------------------------------------------------------------
# Merkliste
#
# Sie gehoert der Person, nicht dem Server: Gelesen werden kann sie nur mit
# ihrem eigenen Token. Der Zugang des Administrators sieht sie nicht - auch
# nicht die Merklisten der Leute, mit denen er seine Bibliothek teilt.
# --------------------------------------------------------------------------

_ARTEN = {"movie": "movie", "show": "tv"}


def _tmdb_aus_guids(eintrag: dict[str, Any]) -> int | None:
    """Die TMDB-Nummer aus der ``Guid``-Liste ziehen, wenn sie dabei ist.

    Plex fuehrt fremde Kennungen als ``tmdb://1234``, ``imdb://tt…``,
    ``tvdb://…``. Welche davon dabei sind, haengt am Agenten - deshalb wird
    gesucht statt geraten, und ein Fehlen ist kein Fehler.
    """
    for kennung in eintrag.get("Guid") or []:
        wert = str(kennung.get("id") or "")
        if wert.startswith("tmdb://"):
            nummer = wert.split("//", 1)[1].strip()
            if nummer.isdigit():
                return int(nummer)
    return None


def _watchlist_item(eintrag: dict[str, Any]) -> WatchlistItem | None:
    art = _ARTEN.get((eintrag.get("type") or "").strip())
    if art is None:
        # Alles ausser Filmen und Serien - Musik etwa - geht Nexview nichts an.
        return None
    guid = (eintrag.get("guid") or "").strip()
    rating_key = str(eintrag.get("ratingKey") or "").strip()
    if not guid and not rating_key:
        return None
    jahr = eintrag.get("year")
    return WatchlistItem(
        # Ohne ``guid`` behilft sich Nexview mit der internen Nummer: Der
        # Schluessel muss dauerhaft sein, sonst gilt derselbe Titel im
        # naechsten Durchgang als neu und wird ein zweites Mal angefragt.
        guid=guid or f"plex-ratingkey://{rating_key}",
        media_type=art,
        title=(eintrag.get("title") or "").strip(),
        year=jahr if isinstance(jahr, int) else None,
        tmdb_id=_tmdb_aus_guids(eintrag),
        rating_key=rating_key or None,
    )


async def watchlist(client_identifier: str, token: str) -> list[WatchlistItem]:
    """Die vollstaendige Merkliste zu diesem Token.

    Bewusst vollstaendig statt der letzten 20, die Overseerr holt: Wer auf
    einen Schlag mehr hinzufuegt, verliert dort den Rest stillschweigend.
    Geblaettert wird in Seiten zu 100; die Notbremse bei 50 Seiten faengt eine
    Antwort ab, die niemals zu Ende geht.
    """
    werke: list[WatchlistItem] = []
    gesehen: set[str] = set()
    start = 0

    for _ in range(WATCHLIST_MAX_PAGES):
        data = await _request(
            "GET",
            "/library/sections/watchlist/all",
            client_identifier=client_identifier,
            token=token,
            base=DISCOVER_URL,
            params={
                "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": WATCHLIST_PAGE_SIZE,
                "includeCollections": 0,
                "includeExternalMedia": 1,
                # Wenn Plex die fremden Kennungen gleich mitliefert, entfaellt
                # das Nachschlagen je Titel vollstaendig. Kennt der Dienst den
                # Wunsch nicht, ignoriert er ihn - dann greift ``watchlist_ids``.
                "includeGuids": 1,
            },
        )
        container = (data or {}).get("MediaContainer") or {}
        eintraege = container.get("Metadata") or []
        for eintrag in eintraege:
            werk = _watchlist_item(eintrag)
            if werk is not None and werk.guid not in gesehen:
                gesehen.add(werk.guid)
                werke.append(werk)

        start += len(eintraege)
        gesamt = container.get("totalSize")
        if not eintraege or (isinstance(gesamt, int) and start >= gesamt):
            break

    return werke


async def _kennungen_nachschlagen(
    client_identifier: str, token: str, werk: WatchlistItem
) -> WatchlistItem | None:
    """Einen einzelnen Titel nachschlagen, um an die TMDB-Nummer zu kommen.

    Gibt ``None`` zurueck, wenn die Abfrage *technisch* scheitert. Das ist ein
    anderer Fall als "nachgeschlagen, aber ohne TMDB-Nummer" (dann kommt das
    Werk unveraendert zurueck): Ersteres darf spaeter erneut versucht werden,
    Letzteres ist eine Auskunft und bleibt es auch morgen.
    """
    if werk.tmdb_id is not None or not werk.rating_key:
        return werk
    try:
        data = await _request(
            "GET",
            f"/library/metadata/{werk.rating_key}",
            client_identifier=client_identifier,
            token=token,
            base=METADATA_URL,
        )
    except MediaServerError as fehler:
        if fehler.status_code == 401:
            raise
        return None

    eintraege = ((data or {}).get("MediaContainer") or {}).get("Metadata") or []
    if not eintraege:
        return werk
    return replace(werk, tmdb_id=_tmdb_aus_guids(eintraege[0]))


async def watchlist_ids(
    client_identifier: str, token: str, items: list[WatchlistItem]
) -> list[WatchlistItem]:
    """Dieselben Titel mit TMDB-Nummer, soweit Plex eine kennt.

    Eine Abfrage je Titel - deshalb wird das nur fuer neue Eintraege gemacht
    und auch dann gedeckelt. Titel, deren Abfrage scheitert, fehlen im
    Ergebnis; der Aufrufer versteht das als "spaeter noch einmal".
    """
    grenze = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)

    async def einer(werk: WatchlistItem) -> WatchlistItem | None:
        async with grenze:
            return await _kennungen_nachschlagen(client_identifier, token, werk)

    ergebnisse = await asyncio.gather(*(einer(werk) for werk in items))
    return [werk for werk in ergebnisse if werk is not None]
