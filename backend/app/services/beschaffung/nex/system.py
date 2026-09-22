"""Was nexcrate ueber sich selbst sagt (``GET /api/v1/system``, N4).

Version, Vertragsstand, Faehigkeiten, die feste Kennung der Installation, die
Adresse nach aussen und die Spruenge in nexcrates Oberflaeche. Der Stand wird
gemerkt: ``faehigkeiten()`` wird an vielen Stellen synchron gefragt und darf
nicht jedes Mal ueber das Netz gehen.

⚠️ **Was fehlt, gilt als nein.** Ein aelteres nexcrate kennt ``anime`` noch
nicht; dann sucht es kein Anime (N44). Dasselbe gilt fuer jede andere
Faehigkeit: Nexview fragt vorher, statt beim Aufruf zu scheitern.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..base import Faehigkeiten

if TYPE_CHECKING:
    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")

#: Die zuletzt gelesene Antwort von ``/system``. Leer heisst "noch nicht gefragt".
_stand: dict[str, Any] = {}


def merken(daten: dict[str, Any]) -> None:
    global _stand
    _stand = dict(daten or {})


def vergessen() -> None:
    merken({})


def stand() -> dict[str, Any]:
    return _stand


def installation_id() -> str:
    return str(_stand.get("installation_id") or "")


def web_url() -> str:
    return str(_stand.get("web_url") or "")


def links() -> dict[str, str]:
    roh = _stand.get("links")
    return {str(k): str(v) for k, v in roh.items()} if isinstance(roh, dict) else {}


def version() -> str:
    return str(_stand.get("version") or "")


def update() -> dict[str, Any]:
    roh = _stand.get("update")
    return dict(roh) if isinstance(roh, dict) else {}


def _kann(name: str, vorgabe: bool = False) -> bool:
    roh = _stand.get("capabilities")
    if not isinstance(roh, dict) or name not in roh:
        return vorgabe
    return bool(roh.get(name))


def faehigkeiten() -> Faehigkeiten:
    """Was dieser Weg kann - aus ``capabilities``, mit sicheren Vorgaben.

    Ohne gelesenen Stand gelten die Zusagen des Vertrags (V1 bis V4): lesen,
    Gruende, Vorschau, Strom, Papierkorb, Kalender und Wertungen fuer beide
    Medienarten. ``anime`` ist die Ausnahme - fehlt es, gilt nein.
    """
    return Faehigkeiten(
        # Profile, TRaSH, Benennung, Pfade, Webhooks gehoeren im NEX-Betrieb
        # nexcrate. Sie sind hier nicht halb da, sondern ganz weg.
        betreiberwerkzeuge=False,
        warum=True,
        vorschau=True,
        ereignisstrom=_kann("stream", True),
        anime=_kann("anime", False),
        papierkorb=True,
        kalender=_kann("calendar", True),
        wertungen=("movie", "tv"),
    )


def wuensche_suchen_sofort() -> bool:
    """Arbeitet nexcrate einen Suchwunsch auch bei ausgeschalteter Automatik ab?"""
    return _kann("wishes_search_at_once", False)


def kann_art(art: str, was: str = "read") -> bool:
    """Antwortet nexcrate fuer diese Medienart? (``capabilities.kinds``.)"""
    from . import mapping

    roh = _stand.get("capabilities")
    kinds = roh.get("kinds") if isinstance(roh, dict) else None
    eintrag = kinds.get(mapping.kind(art)) if isinstance(kinds, dict) else None
    return bool(eintrag.get(was)) if isinstance(eintrag, dict) else True


async def auffrischen(settings: AppSettings) -> dict[str, Any]:
    """``/system`` lesen und merken. Wirft, wenn nexcrate nicht antwortet."""
    from .weg import client_fuer

    daten = await client_fuer(settings).system()
    merken(daten)
    return daten
