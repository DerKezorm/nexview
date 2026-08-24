"""Eine Protokollzeile pro Aufruf nach draussen.

TMDB, Radarr/Sonarr und der Media-Server sind die Stellen, an denen im Betrieb
tatsaechlich etwas schiefgeht - und sie schrieben bisher **nichts**. Was im
Protokoll ankam, hing daran, welcher Aufrufer die Ausnahme zufaellig faengt und
meldet; ein abgelehnter API-Key konnte voellig unsichtbar bleiben.

Statt das an vierzig Aufrufstellen nachzuruesten, haengt es hier an den drei
gemeinsamen HTTP-Verbindungen: ``httpx`` ruft die Haken bei jeder Anfrage auf.

Die Stufe richtet sich nach der Antwort - eine abgelehnte Anmeldung soll auch
im Alltag auffallen, ein normaler Abruf nur bei der Fehlersuche:

* ``DEBUG``   - hat geklappt (2xx/3xx)
* ``INFO``    - 404 und aehnliche: oft voellig normal ("Titel gibt es nicht")
* ``WARNING`` - 401/403/429 und alles ab 500: das fremde System zickt
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

#: Anfrageparameter, deren Wert nie ins Protokoll darf.
SENSITIVE_QUERY = ("token", "key", "password", "secret", "code", "signature", "sig")

LAUT_UND_DEUTLICH = (401, 403, 429)


def mask_query(query: str) -> str:
    """Abfrageparameter uebernehmen, Geheimnisse dabei unkenntlich machen.

    Der TMDB-Aufruf traegt den API-Key in der Adresse. Ohne diese Maske stuende
    er in jeder Zeile - und Protokolle werden weitergegeben.
    """
    if not query:
        return ""
    teile = []
    for paar in query.split("&"):
        name, _, _wert = paar.partition("=")
        if any(wort in name.lower() for wort in SENSITIVE_QUERY):
            teile.append(f"{name}=***")
        else:
            teile.append(paar)
    return "&".join(teile)


def _stufe(status: int) -> int:
    if status >= 500 or status in LAUT_UND_DEUTLICH:
        return logging.WARNING
    if status >= 400:
        return logging.INFO
    return logging.DEBUG


def event_hooks(dienst: str) -> dict[str, list[Any]]:
    """Haken fuer einen ``httpx.AsyncClient``."""
    logger = logging.getLogger(f"nexview.http.{dienst}")

    async def vorher(request: httpx.Request) -> None:
        request.extensions["nexview_start"] = time.perf_counter()

    async def nachher(response: httpx.Response) -> None:
        start = response.request.extensions.get("nexview_start")
        dauer = int((time.perf_counter() - start) * 1000) if start else -1
        url = response.request.url
        pfad = url.path + (f"?{mask_query(url.query.decode())}" if url.query else "")
        logger.log(
            _stufe(response.status_code),
            "%s %s%s -> %s in %dms",
            response.request.method,
            url.host,
            pfad,
            response.status_code,
            dauer,
        )

    return {"request": [vorher], "response": [nachher]}


def unreachable(dienst: str, methode: str, url: str, fehler: BaseException) -> None:
    """Ein Aufruf, der nie eine Antwort bekam - Zeitgrenze oder kein Kontakt.

    Die Haken oben laufen dann nicht: Es gibt keine Antwort, an der sie haengen
    koennten. Gerade dieser Fall ist der haeufigste bei Radarr/Sonarr hinter
    einem Reverse Proxy.
    """
    logging.getLogger(f"nexview.http.{dienst}").warning(
        "%s %s failed: %s: %s", methode, url, type(fehler).__name__, fehler
    )
