"""Gemeinsame Grundlage fuer den Zugriff auf Radarr und Sonarr.

Beide sprechen dieselbe API-Sprache (``/api/v3`` mit ``X-Api-Key``), nur die
Begriffe unterscheiden sich: Radarr kennt Filme und ``tmdbId``, Sonarr kennt
Serien und ``tvdbId``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import logging

import httpx

from . import http_log

logger = logging.getLogger("nexview.arr")

TIMEOUT = httpx.Timeout(15.0, connect=6.0)
MAX_PARALLEL_REQUESTS = 6

# Fuer Schreibzugriffe auf /notification und die Probe: Radarr/Sonarr
# validieren einen Webhook-Eintrag, indem sie das Ziel **sofort selbst
# anrufen** - und antworten erst, wenn dieser Anruf fertig ist. Live gemessen
# (27.08.2026): Bei stumm geschlucktem Ziel (Firewall) dauert das laenger als
# die normalen 15 Sekunden, unser Client brach ab, und ob gespeichert wurde,
# war "ungewiss" (wurde es nicht - die Instanz verwirft bei gescheiterter
# Validierung). Mit laengerer Frist kommt stattdessen ihre ehrliche Antwort.
NOTIFICATION_TIMEOUT = httpx.Timeout(60.0, connect=6.0)

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


class ArrError(Exception):
    """Fehler beim Zugriff auf Radarr/Sonarr - mit lesbarer Meldung.

    ``ungewiss`` trennt "hat nicht geklappt" von "wir wissen es nicht".
    Eine Zeitueberschreitung heisst **nicht**, dass nichts passiert ist: Der
    Auftrag kann angekommen und ausgefuehrt worden sein, nur die Antwort kam
    nicht mehr an. Genau so gesehen - Nexview vermerkte "fehlgeschlagen",
    waehrend Sonarr die Serie laengst angelegt hatte und suchte.

    ``code`` und ``zahlen`` sind dasselbe, was ``meldungen.meldung`` fuer
    HTTP-Antworten liefert: eine **Kennung** und die Werte zum Einsetzen. Das
    Backend uebersetzt nicht, es benennt - den Satz baut das Frontend in der
    eingestellten Sprache (siehe ``app/meldungen.py``).

    ⚠️ **Warum das hier ueberhaupt noetig ist.** Diese Meldungen nehmen einen
    anderen Weg als alle anderen: Sie landen als fertiger Satz in
    ``MediaRequest.error_message`` und stehen von dort im Verlauf - Wochen
    spaeter und ohne die Antwort, die sie erzeugt hat. Deshalb stand dort
    Deutsch, auch wenn die Oberflaeche auf Englisch lief.

    ``message`` bleibt der deutsche Rueckfall: fuer alles, was die API ohne
    die Nexview-Oberflaeche benutzt, und fuer Anfragen, die schon vor dieser
    Aenderung fehlgeschlagen sind.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        ungewiss: bool = False,
        code: str | None = None,
        **zahlen: object,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.ungewiss = ungewiss
        self.code = code
        self.zahlen = zahlen

    def als_meldung(self) -> dict[str, object]:
        """Kennung, deutscher Rueckfall und Werte - wie ``meldungen.meldung``."""
        return {"code": self.code, "message": self.message, **self.zahlen}


async def _http() -> httpx.AsyncClient:
    """Gemeinsame HTTP-Verbindung (siehe tmdb.py: neue Clients kosten Sekunden)."""
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=TIMEOUT,
                    headers={"Accept": "application/json"},
                    # Eine Zeile pro Aufruf ins Protokoll - siehe http_log.py.
                    event_hooks=http_log.event_hooks("arr"),
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


@dataclass(frozen=True)
class WarteschlangenEintrag:
    """Ein laufender Download aus ``/queue`` - aufs Noetigste verdichtet.

    ``arr_id`` ist die movieId bzw. seriesId der Instanz; Staffel und Folge
    gibt es nur bei Sonarr. ``size``/``sizeleft`` tragen die Fortschritts-
    Anzeige: geladen ist, was von ``size`` nicht mehr uebrig ist.
    """

    arr_id: int
    season: int | None
    episode: int | None
    size: int
    sizeleft: int


class ArrClient:
    """Basis-Client. ``label`` erscheint in Fehlermeldungen ("Radarr"/"Sonarr")."""

    def __init__(self, base_url: str, api_key: str, label: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.label = label

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v3{path}"

    async def _request(
        self,
        method: str,
        path: str,
        timeout: httpx.Timeout | None = None,
        **kwargs: Any,
    ) -> Any:
        client = await _http()
        try:
            response = await client.request(
                method,
                self._url(path),
                headers={"X-Api-Key": self.api_key},
                timeout=timeout if timeout is not None else TIMEOUT,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            http_log.unreachable(self.label.lower(), method, self._url(path), exc)
            raise ArrError(
                f"{self.label} antwortet nicht (Zeitüberschreitung).",
                ungewiss=True,
                code="arr_timeout",
                service=self.label,
            ) from exc
        except httpx.HTTPError as exc:
            http_log.unreachable(self.label.lower(), method, self._url(path), exc)
            raise ArrError(
                f"{self.label} ist unter {self.base_url} nicht erreichbar. "
                "Stimmen Adresse und Port?",
                code="arr_unreachable",
                service=self.label,
                url=self.base_url,
            ) from exc

        if response.status_code in (401, 403):
            raise ArrError(
                f"Der API-Key für {self.label} wurde nicht akzeptiert.",
                401,
                code="arr_key_rejected",
                service=self.label,
            )
        if response.status_code == 404:
            raise ArrError(
                f"{self.label} antwortet, kennt diese Adresse aber nicht. "
                f"Zeigt die URL wirklich auf {self.label}?",
                404,
                code="arr_path_unknown",
                service=self.label,
            )
        if response.status_code >= 400:
            # ⚠️ **Den Antwortkoerper ins Protokoll, nicht in die Meldung.**
            # Radarr und Sonarr schreiben ihre eigentliche Begruendung dorthin
            # ("Should be unique", "Quality profile is in use by ..."). Ohne sie
            # bleibt nur "HTTP 409" - und damit laesst sich aus der Ferne nichts
            # entscheiden. In die Oberflaeche gehoert der Text trotzdem nicht:
            # Er ist englisch, technisch und kann Feldnamen der Instanz nennen.
            grund = ""
            try:
                grund = response.text[:400].replace("\n", " ").strip()
            except Exception:  # noqa: BLE001 - eine unlesbare Antwort ist kein Absturz
                grund = ""
            if grund:
                logger.info(
                    "%s rejected %s %s with HTTP %s: %s",
                    self.label,
                    method,
                    path,
                    response.status_code,
                    grund,
                )
            raise ArrError(
                f"{self.label} meldet einen Fehler (HTTP {response.status_code}).",
                response.status_code,
                code="arr_http_error",
                service=self.label,
                status=response.status_code,
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            # Passiert typischerweise, wenn die URL auf eine andere Anwendung zeigt.
            raise ArrError(
                f"Die Antwort von {self.label} ist unerwartet. Zeigt die Adresse "
                f"wirklich auf {self.label}?",
                code="arr_unexpected_answer",
                service=self.label,
            ) from exc

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", path, json=payload)

    async def put(self, path: str, payload: dict[str, Any]) -> Any:
        return await self._request("PUT", path, json=payload)

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("DELETE", path, params=params)

    async def system_status(
        self, timeout: httpx.Timeout | None = None
    ) -> dict[str, Any]:
        """Fuer den Verbindungstest - liefert u. a. Version und App-Name.

        ``timeout`` fuer die Kachel-Anzeige: Eine Statusleuchte, die bei
        stummer Instanz fuenfzehn Sekunden nachdenkt, beruhigt niemanden.
        """
        return await self._request("GET", "/system/status", timeout=timeout)

    async def quality_profiles(self) -> list[dict[str, Any]]:
        return await self.get("/qualityprofile") or []

    async def quality_profile_schema(self) -> dict[str, Any]:
        """Der leere Bauplan eines Profils - die Vorlage zum Ausfuellen.

        ⚠️ **Immer von hier nehmen, nie selbst zusammenstellen.** Der Bauplan
        traegt alle Qualitaetsstufen dieser Fassung mitsamt ihren Nummern und
        ihrer Rangfolge; eine eingebaute Liste veraltet mit dem naechsten
        Radarr und faellt erst auf, wenn ein Profil schief steht.
        """
        return await self.get("/qualityprofile/schema") or {}

    async def quality_profile_anlegen(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/qualityprofile", payload)

    async def quality_profile_nachziehen(
        self, profil_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.put(f"/qualityprofile/{profil_id}", payload)

    async def quality_profile_loeschen(self, profil_id: int) -> None:
        await self.delete(f"/qualityprofile/{profil_id}")

    async def custom_formats(self) -> list[dict[str, Any]]:
        return await self.get("/customformat") or []

    async def custom_format_anlegen(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/customformat", payload)

    async def custom_format_nachziehen(
        self, format_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.put(f"/customformat/{format_id}", payload)

    async def custom_format_loeschen(self, format_id: int) -> None:
        """Ein Erkennungsmuster entfernen.

        ⚠️ **Muster sind instanzweit.** Wird eines geloescht, verschwindet es
        aus **jedem** Profil, das ihm Punkte gab - auch aus fremden. Vor dem
        Aufruf gehoert deshalb geprueft, ob es noch irgendwo bepunktet ist
        (siehe ``arr_bestand.aufraeumen``).

        Bei grossen Bibliotheken dauert die Antwort: Die Instanz bewertet
        danach ihren Bestand neu. Live gemessen (28.08.2026, 3929 Filme) lief
        das mehrfach in die Zeitgrenze - **der Auftrag wurde trotzdem
        ausgefuehrt**. Deshalb die laengere Frist.
        """
        await self._request(
            "DELETE", f"/customformat/{format_id}", timeout=NOTIFICATION_TIMEOUT
        )

    async def benennung(self) -> dict[str, Any]:
        """Wie diese Instanz Dateien und Ordner benennt."""
        return await self.get("/config/naming") or {}

    async def benennung_speichern(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Die Benennung zurueckschreiben.

        ⚠️ **Immer den vollstaendigen Datensatz senden**, also den gelesenen
        veraendern statt einen neuen zu bauen: Radarr und Sonarr fuehren dort
        je nach Fassung unterschiedliche Felder (Doppelpunkt-Ersatz,
        Mehrteiler-Schema, Specials-Ordner). Ein selbst zusammengestellter
        Datensatz loescht still, was er nicht kennt.
        """
        return await self.put(f"/config/naming/{payload.get('id', 1)}", payload)

    async def umbenennen_vorschau(self, feld: str, nummer: int) -> list[dict[str, Any]]:
        """Was an *einem* Titel umbenannt wuerde - leer heisst: nichts zu tun.

        ⚠️ **Einzeln, nicht im Paket.** Live gemessen (28.08.2026): ``/rename``
        verlangt genau ein ``movieId`` und lehnt eine Liste mit 400 ab. Wer
        eine grosse Bibliothek pruefen will, fragt also je Titel - das dauert,
        ist aber die einzige Art, vorher zu wissen, was passieren wuerde.
        """
        return await self.get("/rename", {feld: nummer}) or []

    async def befehl(self, name: str, **felder: Any) -> dict[str, Any]:
        """Einen Auftrag anstossen (Umbenennen, Suche, ...)."""
        return await self.post("/command", {"name": name, **felder})

    async def befehl_stand(self, befehl_id: int) -> dict[str, Any]:
        """Laeuft der Auftrag noch?

        ⚠️ **Radarr und Sonarr melden nur ``queued``/``started``/``completed``,
        keinen Fortschritt.** Ein Balken laesst sich daraus nicht bauen; wer
        einen will, zerlegt die Arbeit selbst in Haeppchen und zaehlt sie.
        """
        return await self.get(f"/command/{befehl_id}") or {}

    async def sprachen(self) -> list[dict[str, Any]]:
        """Welche Sprachen diese Instanz kennt - mit ihren Nummern.

        Die Nummern sind nicht ueber alle Fassungen gleich, deshalb werden sie
        gefragt statt angenommen.
        """
        return await self.get("/language") or []

    async def root_folders(self) -> list[dict[str, Any]]:
        return await self.get("/rootfolder") or []

    async def _warteschlange_roh(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Alle Seiten von ``/queue`` - die Antwort ist seitenweise.

        Form live gemessen (27.08.2026, Radarr 6.3 mit laufendem Download):
        ``{page, pageSize, totalRecords, records: [...]}``.
        """
        seite = 1
        eintraege: list[dict[str, Any]] = []
        while True:
            antwort = (
                await self.get("/queue", {"page": seite, "pageSize": 250, **params})
                or {}
            )
            records = [r for r in antwort.get("records") or [] if isinstance(r, dict)]
            eintraege.extend(records)
            gesamt = antwort.get("totalRecords")
            if not records or not isinstance(gesamt, int) or len(eintraege) >= gesamt:
                return eintraege
            seite += 1

    async def gesundheit(self) -> list[dict[str, Any]]:
        """Die eigenen Probleme der Instanz (``/health``).

        Live gemessen (27.08.2026): Bei gesunden Instanzen eine leere Liste;
        die Feldnamen der Eintraege (``source``, ``type``, ``message``)
        stammen aus der API-Beschreibung - der Verbraucher liest sie deshalb
        nachsichtig.
        """
        antwort = await self.get("/health")
        return antwort if isinstance(antwort, list) else []

    async def notifications(self) -> list[dict[str, Any]]:
        """Alle Benachrichtigungs-Eintraege der Instanz - auch fremde.

        ⚠️ Fremde Eintraege (z. B. Ruddarr) werden grundsaetzlich nur
        angesehen, nie veraendert oder geloescht. Wem ein Eintrag gehoert,
        entscheidet ``webhook_pflege`` - nicht diese Funktion.
        """
        return await self.get("/notification") or []

    async def notification_schema_webhook(self) -> dict[str, Any] | None:
        """Der Bauplan des Webhook-Typs - sagt, was **diese** Fassung kann.

        Gebraucht fuer die Faehigkeits-Pruefung: Statt Versionsnummern zu
        raten, wird nachgesehen, welche Ereignis-Flaggen (``supportsOn...``)
        die Instanz wirklich anbietet. Live gemessen am 27.08.2026 (Radarr
        6.3.0, Sonarr 4.0.19); die Form liegt dem Bauplan "Draht statt Takt"
        bei.
        """
        schema = await self.get("/notification/schema") or []
        for eintrag in schema:
            if isinstance(eintrag, dict) and eintrag.get("implementation") == "Webhook":
                return eintrag
        return None

    async def notification_anlegen(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Laengere Frist: Das Speichern ruft das Ziel zur Pruefung selbst an
        # (siehe NOTIFICATION_TIMEOUT).
        return await self._request(
            "POST", "/notification", timeout=NOTIFICATION_TIMEOUT, json=payload
        )

    async def notification_nachziehen(
        self, eintrag_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/notification/{eintrag_id}",
            timeout=NOTIFICATION_TIMEOUT,
            json={**payload, "id": eintrag_id},
        )

    async def notification_loeschen(self, eintrag_id: int) -> None:
        await self.delete(f"/notification/{eintrag_id}")

    async def notification_probe(self, payload: dict[str, Any]) -> None:
        """Die Instanz bitten, den Eintrag **jetzt** einmal anzurufen.

        Sonarr/Radarr schicken dabei ein Test-Ereignis an die eingetragene
        Adresse - kommt es bei uns an, ist die Strecke bewiesen
        (``routers/webhooks`` setzt ``bewiesen_am``). Der Aufruf speichert
        nichts: Er funktioniert auch mit einem noch nicht angelegten Eintrag.
        """
        await self._request(
            "POST", "/notification/test", timeout=NOTIFICATION_TIMEOUT, json=payload
        )

    async def ensure_tag(self, label: str) -> int | None:
        """Kennung fuer ein Etikett besorgen - und es notfalls anlegen.

        Damit steht in Radarr/Sonarr, wer den Titel angefordert hat. Klappt es
        nicht, ist das kein Grund, das Hinzufuegen scheitern zu lassen -
        dann gibt es eben kein Etikett.
        """
        wanted = label.strip().lower()
        if not wanted:
            return None

        try:
            for tag in await self.get("/tag") or []:
                if str(tag.get("label", "")).lower() == wanted:
                    return tag.get("id")
            created = await self.post("/tag", {"label": wanted})
        except ArrError:
            return None

        return created.get("id") if isinstance(created, dict) else None
