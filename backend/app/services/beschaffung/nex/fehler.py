"""Was nexcrate an Fehlern schickt, und was Nexview daraus macht.

Drei Koerbe (Bauplan Abschnitt 3.1, Erfahrung aus nexbeat):

* ``voruebergehend`` - Zeit, Netz, 5xx, 429 mit ``Retry-After``, eine fremde
  Antwort (HTML eines Proxys). Noch einmal senden lohnt sich; weil nexcrates
  Anfragen idempotent sind, schadet ein zweiter Versuch nie.
* ``abgelehnt`` - nexcrate hat verstanden und nein gesagt (4xx mit Kennung).
  Die Kennung wird gezeigt, nie nexcrates Satz.
* ``unbekannt`` - weder noch: ``nexcrate_refused`` mit nexcrates Kennung im
  Protokoll.

⚠️ **Kein Satz aus nexcrate wird angezeigt.** Nexview uebersetzt nach Kennung
(``errors.byCode``); die englische ``message`` geht nur ins Protokoll. Gelesen
werden beide Fehlerformen: flach (``{code, message, params}``) unter
``/api/v1`` und die der Oberflaeche (``{"detail": {...}}``) daneben - eine
vertippte Adresse antwortet in der zweiten (nexbeat-Befund 9).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ..base import BeschaffungError, Korb

logger = logging.getLogger("nexview.nexcrate")

#: nexcrates Kennungen, die Nexview unter eigenem Namen fuehrt. Alles andere
#: wird ``nexcrate_refused`` und steht mit nexcrates Kennung im Protokoll.
FREMDE_CODES: dict[str, str] = {
    "api_key_missing": "nexcrate_key_rejected",
    "api_key_invalid": "nexcrate_key_rejected",
    "scope_missing": "nexcrate_scope_missing",
    "not_found": "nexcrate_path_unknown",
    "method_not_allowed": "nexcrate_path_unknown",
    "title_not_found": "nexcrate_title_unknown",
    "season_not_found": "nexcrate_season_unknown",
    "download_not_found": "nexcrate_download_unknown",
    "version_not_found": "nexcrate_version_unknown",
    "version_not_available": "nexcrate_version_unknown",
    "ref_invalid": "nexcrate_ref_invalid",
    "ref_source_unknown": "nexcrate_ref_invalid",
    "kind_unsupported": "nexcrate_kind_unsupported",
    "invalid_input": "nexcrate_input_invalid",
    "scope_not_for_kind": "nexcrate_input_invalid",
    "marker_too_old": "nexcrate_marker_too_old",
    "pairing_not_found": "nexcrate_pairing_gone",
    "pairing_limit": "nexcrate_busy",
    "rate_limited": "nexcrate_busy",
    "too_many_streams": "nexcrate_busy",
}

#: Kennungen ohne Antwort der Gegenseite - immer vorübergehend.
VORUEBERGEHEND: frozenset[str] = frozenset(
    {
        "nexcrate_timeout",
        "nexcrate_unreachable",
        "nexcrate_unexpected_answer",
        "nexcrate_unavailable",
        "nexcrate_busy",
    }
)

#: Deutscher Rueckfall je Kennung - er landet in ``MediaRequest.error_message``
#: und steht dort Wochen spaeter ohne die Antwort, die ihn erzeugt hat.
SAETZE: dict[str, str] = {
    "nexcrate_timeout": "nexcrate antwortet nicht (Zeitüberschreitung).",
    "nexcrate_unreachable": "nexcrate ist unter dieser Adresse nicht erreichbar.",
    "nexcrate_unexpected_answer": "Die Antwort ist unerwartet. Zeigt die Adresse wirklich auf nexcrate?",
    "nexcrate_unavailable": "nexcrate ist gerade nicht erreichbar.",
    "nexcrate_busy": "nexcrate ist ausgelastet.",
    "nexcrate_key_rejected": "Der Schlüssel für nexcrate wurde nicht akzeptiert.",
    "nexcrate_scope_missing": "Dem Schlüssel fehlt ein Recht in nexcrate.",
    "nexcrate_path_unknown": "Unter dieser Adresse antwortet kein nexcrate mit Schnittstelle.",
    "nexcrate_title_unknown": "nexcrate kennt diesen Titel nicht.",
    "nexcrate_season_unknown": "nexcrate kennt diese Staffel nicht.",
    "nexcrate_download_unknown": "nexcrate kennt diesen Download nicht mehr.",
    "nexcrate_version_unknown": "Diese Fassung gibt es in nexcrate nicht.",
    "nexcrate_ref_invalid": "nexcrate kann mit dieser Kennung nichts anfangen.",
    "nexcrate_kind_unsupported": "Für diese Medienart antwortet nexcrate nicht.",
    "nexcrate_input_invalid": "nexcrate hat die Anfrage als fehlerhaft zurückgewiesen.",
    "nexcrate_marker_too_old": "Die Marke ist zu alt; der Bestand wird ganz neu gelesen.",
    "nexcrate_pairing_gone": "Die Bitte ums Koppeln ist abgelaufen.",
    "nexcrate_refused": "nexcrate hat abgelehnt.",
    "nexcrate_not_configured": "Für nexcrate sind Adresse und Schlüssel noch nicht hinterlegt.",
}

_TITEL = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


class NexcrateError(BeschaffungError):
    """Ein Fehler von nexcrate - Kennung, Korb und nexcrates eigener Code.

    ``fremd`` ist nexcrates Kennung, fuer Protokoll und Betreiber; angezeigt
    wird sie nur als Zahl in ``nexcrate_refused``.
    """

    def __init__(
        self,
        code: str,
        *,
        status_code: int | None = None,
        fremd: str = "",
        ungewiss: bool = False,
        korb: Korb | None = None,
        **zahlen: object,
    ) -> None:
        super().__init__(
            SAETZE.get(code, SAETZE["nexcrate_refused"]),
            status_code,
            ungewiss=ungewiss,
            code=code,
            korb=korb,
            **zahlen,
        )
        self.fremd = fremd

    def _korb_ableiten(self) -> Korb:
        if self.code in VORUEBERGEHEND:
            return Korb.voruebergehend
        return super()._korb_ableiten()


def seitentext(text: str) -> str:
    """Kurzfassung einer Antwort, die kein JSON ist - eine HTML-Seite wird ihr Titel.

    Ohne das stuende die ganze 502-Seite eines Proxys als Begruendung in den
    Einstellungen (nexbeat, 22.09.2026).
    """
    gefunden = _TITEL.search(text) or (_H1.search(text) if "<" in text else None)
    roh = gefunden.group(1) if gefunden else re.sub(r"<[^>]+>", " ", text)
    return " ".join(roh.split())[:120] or "(leer)"


def koerper(antwort: httpx.Response) -> dict[str, Any]:
    """Der Fehlerkoerper in beiden Formen: flach und unter ``detail``."""
    try:
        gelesen = antwort.json()
    except ValueError:
        return {}
    if isinstance(gelesen, dict) and isinstance(gelesen.get("detail"), dict):
        gelesen = gelesen["detail"]
    return gelesen if isinstance(gelesen, dict) else {}


def aus_antwort(antwort: httpx.Response, pfad: str) -> NexcrateError:
    """Aus nexcrates Antwort einen Fehler mit Kennung und Korb machen."""
    body = koerper(antwort)
    fremd = str(body.get("code") or "")
    status = antwort.status_code
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    if fremd in FREMDE_CODES:
        code = FREMDE_CODES[fremd]
    elif status in (401, 403):
        code = "nexcrate_key_rejected"
    elif status == 404 and not fremd:
        code = "nexcrate_path_unknown"
    elif status == 410 and not fremd:
        code = "nexcrate_marker_too_old"
    elif status >= 500 and not fremd:
        # 22.09.2026 an nexbeat gemessen: Waehrend nexcrate aufgespielt wurde,
        # antwortete der Proxy davor zehn Minuten lang mit 502 und einer
        # HTML-Seite. Das hiesse sonst "abgelehnt", obwohl nexcrate gar nicht lief.
        code = "nexcrate_unavailable"
    else:
        code = "nexcrate_refused"
    if code == "nexcrate_refused":
        grund = f"{fremd}: {body.get('message')}" if fremd else seitentext(antwort.text)
        logger.info("nexcrate answered %s to %s: %s", status, pfad, grund)
    zahlen: dict[str, object] = {"status": status}
    if fremd:
        zahlen["nexcrate_code"] = fremd
    wartezeit = antwort.headers.get("retry-after") or params.get("retry_after")
    if wartezeit:
        zahlen["retry_after"] = wartezeit
    # 5xx heisst "noch einmal", und ob der Auftrag ankam, weiss niemand
    # (nexbeat-Befund 17: eine 500 aus einem Wettlauf, der Titel war angelegt).
    return NexcrateError(code, status_code=status, fremd=fremd, ungewiss=status >= 500, **zahlen)


def nicht_eingerichtet() -> NexcrateError:
    return NexcrateError("nexcrate_not_configured", status_code=409)
