"""Radarr und Sonarr als Attrappen - eine Stelle statt zehn.

Bis Scheibe 2 des NEX-Umbaus baute sich fast jede Testdatei ihr eigenes
Radarr: ``SonarrAttrappe``, ``_FakeRadarr``, ``_FakeSonarr``, ``FakeArr``,
lokale ``Attrappe``-Klassen fuer Papierkorb und Wertungen. Jede konnte genau
das, was ihr Test brauchte, und keine wusste von der anderen. Hier stehen sie
zusammen, damit neben ihnen spaeter ``fake_nexcrate.py`` stehen kann und man
beide Wege nebeneinander sieht.

Zwei Attrappen, weil es zwei Ebenen gibt:

* ``FakeArr`` ersetzt den **Client** (``library.radarr_client`` und
  ``library.sonarr_client`` liefern sie): Auftraege, Warteschlange, Suche,
  Lesen beliebiger Pfade. Jeder Aufruf wird gemerkt.
* ``FakeArrRueckkanal`` ersetzt den Client der **Webhook-Pflege**
  (``webhook_pflege._client``): Eintraege in ``/notification`` samt Probe.

Was hier nicht steht: ``download_attrappe.ArrAttrappe`` (die HTTP-Ebene, fuer
die Download-Tests) und das ``arr_client``-Fixture in ``conftest.py`` - beide
bleiben, wo sie sind (Bauplan, Abschnitt 8, Scheibe 2).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.db import SessionLocal
from app.models import utcnow
from app.services.beschaffung import WarteschlangenEintrag
from app.services.beschaffung.arr import webhooks
from app.services.beschaffung.arr.client import ArrError

#: Was eine Instanz unter ``/notification/schema`` fuer den Webhook meldet.
SCHEMA_MOVIE = {
    "implementation": "Webhook",
    "supportsOnDownload": True,
    "supportsOnUpgrade": True,
    "supportsOnMovieDelete": True,
    "supportsOnMovieFileDelete": True,
    "supportsOnGrab": True,
    "supportsOnHealthIssue": True,
    "supportsOnHealthRestored": True,
    "supportsOnManualInteractionRequired": True,
}


class FakeArr:
    """Radarr oder Sonarr im Kleinen: merkt sich, was Nexview geschickt haette.

    ``art`` entscheidet nur, unter welchem Namen ``add`` die Kennung merkt
    (``tmdb_id`` fuer Radarr, ``tvdb_id`` fuer Sonarr). Antworten stellt der
    Test ein:

    * ``arr_id``: die Nummer, unter der ``add`` anlegt; ``anlege_fehler``
      laesst ``add`` stattdessen scheitern.
    * ``tag_id``: was ``ensure_tag`` liefert (``None`` wie eine Instanz ohne Etikett).
    * ``warteschlange``: die Eintraege aus ``/queue``, veraenderbar ueber ``eintraege``.
    * ``suche``: Treffer der Seriensuche - eine Liste fuer jeden Begriff oder
      ein Woerterbuch je Begriff. ``such_fehler`` laesst sie scheitern.
    * ``lesen``: beantwortet ``get(pfad, params)``.
    """

    def __init__(
        self,
        *,
        art: str = "tv",
        arr_id: int = 4242,
        anlege_fehler: Exception | None = None,
        tag_id: int | None = 1,
        warteschlange: list[WarteschlangenEintrag] | None = None,
        suche: list[dict[str, Any]] | dict[str, list[dict[str, Any]]] | None = None,
        such_fehler: bool = False,
        lesen: Callable[[str, dict], Any] | None = None,
    ) -> None:
        self._kennungsfeld = "tvdb_id" if art == "tv" else "tmdb_id"
        self.arr_id = arr_id
        self.anlege_fehler = anlege_fehler
        self.tag_id = tag_id
        self.eintraege: list[WarteschlangenEintrag] = list(warteschlange or [])
        self.treffer = suche
        self.such_fehler = such_fehler
        self._lesen = lesen

        self.angelegt: list[dict[str, Any]] = []
        self.aktivierte_staffeln: list[tuple[int, list[int]]] = []
        self.gesucht: list[int | None] = []
        #: Jeder Suchbegriff, in der Reihenfolge der Fragen.
        self.gefragt: list[str] = []
        #: Jeder ``get``-Aufruf als ``(pfad, params)``.
        self.gelesen: list[tuple[str, dict]] = []

    async def ensure_tag(self, _label: str) -> int | None:
        return self.tag_id

    async def add(self, kennung: int, *_args: Any, **kwargs: Any) -> dict:
        if self.anlege_fehler is not None:
            raise self.anlege_fehler
        self.angelegt.append({self._kennungsfeld: kennung, "season": kwargs.get("season")})
        return {"id": self.arr_id}

    async def monitor_seasons(
        self, arr_id: int, seasons: set[int], such_staffel: int | None = None
    ) -> None:
        self.aktivierte_staffeln.append((arr_id, sorted(seasons)))
        self.gesucht.append(such_staffel)

    async def warteschlange(self) -> list[WarteschlangenEintrag]:
        return self.eintraege

    async def suche(self, begriff: str) -> list[dict[str, Any]]:
        self.gefragt.append(begriff)
        if self.such_fehler:
            raise ArrError("Sonarr ist nicht erreichbar.", 502, code="arr_unreachable")
        if isinstance(self.treffer, dict):
            return self.treffer.get(begriff, [])
        return list(self.treffer or [])

    async def get(self, pfad: str, params: dict | None = None) -> Any:
        self.gelesen.append((pfad, dict(params or {})))
        return self._lesen(pfad, params or {}) if self._lesen is not None else None


class FakeArrRueckkanal:
    """Radarr in klein fuer die Webhook-Pflege: merkt sich Eintraege und was mit ihnen geschah."""

    def __init__(self, kennung: str = "radarr-standard") -> None:
        self.kennung = kennung
        self.eintraege: list[dict] = []
        self.schema: dict | None = dict(SCHEMA_MOVIE)
        # "arrives" | "silent" | eine ArrError-Instanz
        self.probe = "arrives"
        # Jede Probe-Payload, wie sie bei der Instanz ankaeme.
        self.proben: list[dict] = []
        self.angelegt: list[dict] = []
        self.nachgezogen: list[tuple[int, dict]] = []
        self.geloescht: list[int] = []
        self._naechste_id = 7

    async def notifications(self) -> list[dict]:
        return [dict(eintrag) for eintrag in self.eintraege]

    async def notification_schema_webhook(self) -> dict | None:
        return self.schema

    async def notification_probe(self, payload: dict) -> None:
        self.proben.append(dict(payload))
        if isinstance(self.probe, ArrError):
            raise self.probe
        if self.probe == "arrives":
            # Was im Betrieb der Empfaenger tut, wenn Sonarrs Test ankommt -
            # in einer eigenen Sitzung, wie im echten Leben.
            with SessionLocal() as db:
                zeile = webhooks.eintrag(db, self.kennung)
                zeile.bewiesen_am = utcnow()
                zeile.zuletzt_angerufen_am = utcnow()
                zeile.letztes_ereignis = "Test"
                db.commit()

    async def notification_anlegen(self, payload: dict) -> dict:
        self.angelegt.append(payload)
        eintrag = {**payload, "id": self._naechste_id}
        self.eintraege.append(eintrag)
        return eintrag

    async def notification_nachziehen(self, eintrag_id: int, payload: dict) -> dict:
        self.nachgezogen.append((eintrag_id, payload))
        return {**payload, "id": eintrag_id}

    async def notification_loeschen(self, eintrag_id: int) -> None:
        self.geloescht.append(eintrag_id)
        self.eintraege = [e for e in self.eintraege if e.get("id") != eintrag_id]
