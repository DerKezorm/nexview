"""Nachsehen, ob es eine neuere Nexview-Version gibt.

Nexview fragt dafuer hoechstens einmal am Tag die oeffentliche GitHub-API nach
der neuesten Veroeffentlichung. Uebertragen wird dabei nichts ausser der
Anfrage selbst - keine Nutzerdaten, keine Einstellungen, keine Titel.

Grundsatz: Diese Pruefung ist Beiwerk. Faellt GitHub aus, ist kein Netz da oder
antwortet die API mit einem Fehler, darf davon nichts in der Oberflaeche
kaputtgehen - dann steht dort einfach nichts.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from .. import __version__

logger = logging.getLogger(__name__)

REPO = "DerKezorm/nexview"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"

# Hoechstens einmal am Tag nachfragen. GitHub erlaubt ohne Anmeldung 60
# Anfragen pro Stunde und IP - davon sind wir damit weit entfernt.
CHECK_INTERVAL = timedelta(hours=24)

# Kurze Zeitgrenze: die Ueber-Seite soll nicht auf GitHub warten muessen.
TIMEOUT = httpx.Timeout(6.0, connect=4.0)

_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class UpdateStatus:
    """Ergebnis der letzten Pruefung."""

    current: str
    latest: str | None = None
    update_available: bool = False
    checked_at: datetime | None = None
    release_url: str = RELEASES_URL


# Zwischenspeicher im Arbeitsspeicher. Ein Neustart des Containers fuehrt zu
# einer neuen Abfrage - das ist selten genug und spart eine Tabelle.
_cached: UpdateStatus | None = None
_lock = asyncio.Lock()


def parse_version(text: str) -> tuple[int, int, int] | None:
    """``"v1.2.3"`` -> ``(1, 2, 3)``; alles Unverstaendliche -> ``None``.

    Zusaetze wie ``-beta`` werden bewusst ignoriert: fuer den Vergleich zaehlen
    nur die drei Zahlen.
    """
    treffer = _VERSION_PATTERN.match(text.strip())
    if treffer is None:
        return None
    return (int(treffer[1]), int(treffer[2]), int(treffer[3]))


def is_newer(latest: str, current: str) -> bool:
    """Ist ``latest`` groesser als ``current``?

    Laesst sich eine der beiden Angaben nicht lesen, lautet die Antwort nein -
    ein falscher Hinweis waere schlimmer als gar keiner.
    """
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


async def _abfragen() -> str | None:
    """Neueste veroeffentlichte Version bei GitHub erfragen."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        antwort = await client.get(
            API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"Nexview/{__version__}",
            },
        )

    # 404 heisst: es gibt noch gar keine Veroeffentlichung. Das ist kein Fehler.
    if antwort.status_code == 404:
        return None
    antwort.raise_for_status()

    name = (antwort.json() or {}).get("tag_name") or ""
    return name.strip() or None


async def status(*, enabled: bool = True, force: bool = False) -> UpdateStatus:
    """Aktueller Stand - aus dem Zwischenspeicher oder frisch von GitHub.

    ``enabled=False`` liefert nur die eigene Version zurueck, ohne jede
    Verbindung nach aussen. ``force=True`` umgeht den Zwischenspeicher (fuer
    den Knopf "jetzt pruefen").
    """
    global _cached

    if not enabled:
        return UpdateStatus(current=__version__)

    jetzt = datetime.now(UTC)
    frisch = (
        _cached is not None
        and _cached.checked_at is not None
        and jetzt - _cached.checked_at < CHECK_INTERVAL
    )
    if frisch and not force:
        return _cached  # type: ignore[return-value]

    async with _lock:
        # Zweite Pruefung: waehrend des Wartens auf die Sperre kann ein
        # paralleler Aufruf die Abfrage bereits erledigt haben.
        if (
            not force
            and _cached is not None
            and _cached.checked_at is not None
            and datetime.now(UTC) - _cached.checked_at < CHECK_INTERVAL
        ):
            return _cached

        try:
            neueste = await _abfragen()
        except Exception as fehler:  # noqa: BLE001 - Ausfall darf nichts kosten
            logger.warning("Version check at GitHub failed: %s", fehler)
            # Alten Stand behalten, falls vorhanden - sonst nur die eigene
            # Version melden. Der Zeitstempel bleibt unveraendert, damit es
            # beim naechsten Aufruf gleich wieder versucht wird.
            return _cached or UpdateStatus(current=__version__)

        _cached = UpdateStatus(
            current=__version__,
            latest=neueste,
            update_available=bool(neueste) and is_newer(neueste, __version__),
            checked_at=datetime.now(UTC),
        )
        return _cached


def gemerkt() -> UpdateStatus | None:
    """Der zuletzt geholte Stand - ohne selbst nachzusehen.

    ⚠️ **Fuer Aufrufer, die nicht warten koennen.** ``status()`` ist eine
    Netzabfrage und damit ``async``; das Befund-Register ist bewusst
    synchron und darf ohnehin nicht nach draussen greifen. Wer hier ``None``
    bekommt, hat einfach noch keinen Stand - das ist kein Fehler, sondern der
    Zustand direkt nach dem Start.
    """
    return _cached


def reset_cache() -> None:
    """Nur fuer Tests."""
    global _cached
    _cached = None
