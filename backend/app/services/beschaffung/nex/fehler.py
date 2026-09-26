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
vertippte Adresse antwortet in der zweiten (Befund).
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
    "version_unknown": "nexcrate_version_unknown",
    "version_kind_mismatch": "nexcrate_version_wrong_kind",
    # 26.09.2026 im grossen Pruefgang: Jede Anfrage an einen Titel, den
    # nexcrate aus einem noch verbundenen Radarr oder Sonarr uebernommen hat,
    # endete als nackter 502 - gleich ob Anime oder gewoehnliche Serie.
    "version_fed_by_source": "nexcrate_version_fed_by_source",
    "episode_not_found": "nexcrate_episode_unknown",
    "ref_ambiguous": "nexcrate_ref_ambiguous",
    "download_importing": "nexcrate_download_importing",
    "download_finished": "nexcrate_download_importing",
    "recycle_title_gone": "nexcrate_recycle_title_gone",
    # 26.09.2026: nexcrate legt einen Titel beim Zurueckholen neu an,
    # wenn er die Bibliothek verlassen hat. ``recycle_version_gone`` heisst
    # seither: die Fassung selbst (nicht nur der Titel) gibt es nicht mehr -
    # ein anderer Fall als "der Titel laesst sich nicht wieder anlegen".
    "recycle_version_gone": "nexcrate_recycle_version_gone",
    "recycle_file_gone": "nexcrate_recycle_file_gone",
    "recycle_slot_taken": "nexcrate_recycle_slot_taken",
    "recycle_target_taken": "nexcrate_recycle_target_taken",
    "tmdb_not_configured": "nexcrate_tmdb_missing",
    "tmdb_token_unreadable": "nexcrate_tmdb_missing",
    "tmdb_token_rejected": "nexcrate_tmdb_missing",
    "tmdb_unreachable": "nexcrate_tmdb_unavailable",
    "tmdb_unavailable": "nexcrate_tmdb_unavailable",
    "tmdb_timeout": "nexcrate_tmdb_unavailable",
    "tmdb_rate_limited": "nexcrate_tmdb_unavailable",
    "tmdb_http_error": "nexcrate_tmdb_unavailable",
    # 26.09.2026: Ein zurueckgeholter Titel kann auch ein Album sein
    # (Musik fuehrt Nexview zwar nicht, aber die Adresse ist dieselbe) - seine
    # Kennungen stehen im Vertrag und muessten sonst als "nexcrate_refused"
    # durchfallen.
    "musicbrainz_not_found": "nexcrate_title_unknown",
    "musicbrainz_disabled": "nexcrate_musicbrainz_missing",
    "musicbrainz_busy": "nexcrate_musicbrainz_unavailable",
    "musicbrainz_unavailable": "nexcrate_musicbrainz_unavailable",
    "musicbrainz_unreachable": "nexcrate_musicbrainz_unavailable",
    "musicbrainz_http_error": "nexcrate_musicbrainz_unavailable",
    "musicbrainz_bad_answer": "nexcrate_musicbrainz_unavailable",
    "musicbrainz_timeout": "nexcrate_musicbrainz_unavailable",
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
    # Unter Last antwortet nexcrate mit 503 und ``Retry-After``: Seine
    # Datenbank ist gerade gesperrt. Das ist ein "gleich noch einmal", keine
    # Absage - sonst reichte das Nachreichen die Anfrage nie wieder ein.
    "database_busy": "nexcrate_busy",
}

#: ``not_found`` sagt je nach Adresse Verschiedenes: Unter einer unbekannten
#: Adresse heisst es "hier antwortet keine Schnittstelle", unter einer
#: bekannten "diesen Eintrag gibt es nicht (mehr)". Die bekannten stehen hier.
NICHT_DA_JE_ADRESSE: tuple[tuple[str, str], ...] = (
    ("/recycle-bin/", "nexcrate_recycle_entry_gone"),
)

#: Kennungen ohne Antwort der Gegenseite - immer vorübergehend.
VORUEBERGEHEND: frozenset[str] = frozenset(
    {
        "nexcrate_timeout",
        "nexcrate_unreachable",
        "nexcrate_unexpected_answer",
        "nexcrate_unavailable",
        "nexcrate_busy",
        "nexcrate_tmdb_unavailable",
        "nexcrate_musicbrainz_unavailable",
    }
)

#: Davon die, die heissen "nexcrate ist weg", nicht "nexcrate hakt an einer
#: Stelle": Lesewege hoeren dann auf zu fragen, und was gehalten ist, gilt.
AUSFALL: frozenset[str] = frozenset(
    {"nexcrate_timeout", "nexcrate_unreachable", "nexcrate_unavailable"}
)

#: Begruendete Absagen: nexcrate hat verstanden und aus einem Grund nein
#: gesagt, den der Anfragende oder der Betreiber lesen soll. Nexview
#: antwortet darauf mit diesem Status statt mit ``502`` - ein 502 sagt
#: "die Gegenseite ist kaputt", und das war sie nicht.
ABLEHNUNGEN: dict[str, int] = {
    "nexcrate_version_fed_by_source": 409,
    "nexcrate_version_unknown": 409,
    "nexcrate_version_wrong_kind": 409,
    "nexcrate_season_unknown": 409,
    "nexcrate_episode_unknown": 409,
    "nexcrate_ref_ambiguous": 409,
    "nexcrate_download_importing": 409,
    "nexcrate_recycle_title_gone": 409,
    "nexcrate_recycle_version_gone": 409,
    "nexcrate_recycle_file_gone": 409,
    "nexcrate_recycle_slot_taken": 409,
    "nexcrate_recycle_target_taken": 409,
    "nexcrate_recycle_entry_gone": 404,
    # Keine Antwort von nexcrate, sondern Nexviews eigene Einstellung: Adresse
    # und Schlüssel fehlen noch - der Ausgangszustand jeder Installation, die
    # den Umstieg zum ersten Mal öffnet.
    "nexcrate_not_configured": 409,
}

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
    "nexcrate_version_wrong_kind": "Diese Fassung gehört in nexcrate zu einer anderen Medienart.",
    "nexcrate_version_fed_by_source": (
        "Diesen Titel führt in dieser Fassung noch Radarr oder Sonarr; "
        "anfragen und ändern lässt er sich erst nach der Übernahme in nexcrate."
    ),
    "nexcrate_episode_unknown": "nexcrate kennt diese Folge nicht.",
    "nexcrate_ref_ambiguous": "nexcrate führt unter dieser Kennung mehr als einen Titel.",
    "nexcrate_download_importing": (
        "nexcrate legt einen Download dieses Titels gerade ab oder hat ihn schon abgelegt."
    ),
    # 26.09.2026: Seit nexcrate einen entfernten Titel beim
    # Zurückholen neu anlegt, heißt "gone" hier "lässt sich nicht wieder
    # anlegen" - nicht mehr "ist für immer weg".
    "nexcrate_recycle_title_gone": (
        "Der Titel lässt sich nicht wieder anlegen; die Datei bleibt im Papierkorb."
    ),
    "nexcrate_recycle_version_gone": (
        "Die Fassung, zu der die Datei gehörte, gibt es in nexcrate nicht mehr."
    ),
    "nexcrate_recycle_file_gone": "Die Datei liegt nicht mehr im Papierkorb.",
    "nexcrate_recycle_slot_taken": "Für diese Stelle gibt es inzwischen eine andere Datei.",
    "nexcrate_recycle_target_taken": "Dort, wo die Datei lag, liegt inzwischen etwas anderes.",
    "nexcrate_recycle_entry_gone": "Diesen Eintrag gibt es im Papierkorb nicht mehr.",
    "nexcrate_tmdb_missing": "In nexcrate fehlt ein gültiger TMDB-Zugang.",
    "nexcrate_tmdb_unavailable": "nexcrate erreicht TMDB gerade nicht.",
    "nexcrate_musicbrainz_missing": "In nexcrate ist MusicBrainz nicht eingerichtet.",
    "nexcrate_musicbrainz_unavailable": "nexcrate erreicht MusicBrainz gerade nicht.",
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

    kennung_nach_aussen = True

    def _korb_ableiten(self) -> Korb:
        if self.code in VORUEBERGEHEND:
            return Korb.voruebergehend
        return super()._korb_ableiten()

    @property
    def antwort_status(self) -> int:
        return ABLEHNUNGEN.get(self.code or "", 502)


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
    eintrag_weg = next(
        (code for anfang, code in NICHT_DA_JE_ADRESSE if pfad.startswith(anfang)), None
    )
    if fremd == "not_found" and eintrag_weg:
        code = eintrag_weg
    elif fremd in FREMDE_CODES:
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
    if code == "nexcrate_refused" or code in ABLEHNUNGEN:
        grund = f"{fremd}: {body.get('message')}" if fremd else seitentext(antwort.text)
        logger.info("nexcrate answered %s to %s: %s", status, pfad, grund)
    zahlen: dict[str, object] = {"status": status}
    if fremd:
        zahlen["nexcrate_code"] = fremd
    wartezeit = antwort.headers.get("retry-after") or params.get("retry_after")
    if wartezeit:
        zahlen["retry_after"] = wartezeit
    # 5xx heisst "noch einmal", und ob der Auftrag ankam, weiss niemand
    # (Befund: eine 500 aus einem Wettlauf, der Titel war angelegt).
    return NexcrateError(code, status_code=status, fremd=fremd, ungewiss=status >= 500, **zahlen)


def nicht_eingerichtet() -> NexcrateError:
    return NexcrateError("nexcrate_not_configured", status_code=409)
