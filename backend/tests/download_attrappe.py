"""Ein Radarr und ein Sonarr zum Mitschreiben - fuer die Tests rund um haengende Downloads.

Gemockt wird auf HTTP-Ebene (``httpx.MockTransport``), wie in
``test_serverkonten_anbindung.py``: Geprueft wird, welche Adressen, Parameter
und Koerper wirklich an die Instanz gehen. Die Formen der Antworten folgen dem
Quelltext von Radarr 6.3 und Sonarr 4.0.19 (gelesen am 12.09.2026).

Kein Test-Modul (kein ``test_``-Praefix): Die Tests holen sich hier die
Attrappe und bauen ihre Vorrichtung selbst.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services.beschaffung.arr import client as arr
from app.services.beschaffung.arr import download_haenger

RADARR = "http://radarr.example.com"
SONARR = "http://sonarr.example.com"
RADARR_HOST = "radarr.example.com"
SONARR_HOST = "sonarr.example.com"


@dataclass
class Anruf:
    host: str
    methode: str
    #: Ohne ``/api/v3``.
    pfad: str
    params: dict
    koerper: object


@dataclass
class ArrAttrappe:
    warteschlange: dict[str, list[dict]] = field(
        default_factory=lambda: {RADARR_HOST: [], SONARR_HOST: []}
    )
    #: downloadId -> Antwort von ``/manualimport``.
    kandidaten: dict[str, list[dict]] = field(default_factory=dict)
    anrufe: list[Anruf] = field(default_factory=list)
    #: (Methode, Pfad-Anfang) -> HTTP-Status, mit dem geantwortet wird.
    fehler: dict[tuple[str, str], int] = field(default_factory=dict)
    #: Hosts, die gar nicht antworten.
    stumm: set[str] = field(default_factory=set)
    #: Womit ``GET /command/{id}`` antwortet.
    befehl_status: str = "completed"
    _naechster_befehl: int = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        pfad = request.url.path.removeprefix("/api/v3")
        koerper = json.loads(request.content) if request.content else None
        self.anrufe.append(Anruf(host, request.method, pfad, dict(request.url.params), koerper))
        if host in self.stumm:
            raise httpx.ConnectError("stumm", request=request)
        for (methode, anfang), status in self.fehler.items():
            if request.method == methode and pfad.startswith(anfang):
                return httpx.Response(status, text="kaputt")
        if request.method == "GET" and pfad == "/queue":
            zeilen = self.warteschlange.get(host, [])
            return httpx.Response(
                200,
                json={"page": 1, "pageSize": 250, "totalRecords": len(zeilen), "records": zeilen},
            )
        if request.method == "GET" and pfad == "/manualimport":
            return httpx.Response(
                200, json=self.kandidaten.get(request.url.params.get("downloadId", ""), [])
            )
        if request.method == "DELETE" and pfad.startswith("/queue"):
            return httpx.Response(200)
        if request.method == "POST" and pfad == "/command":
            nummer = self._naechster_befehl
            self._naechster_befehl += 1
            name = koerper.get("name") if isinstance(koerper, dict) else None
            return httpx.Response(201, json={"id": nummer, "name": name, "status": "queued"})
        if request.method == "GET" and pfad.startswith("/command/"):
            return httpx.Response(
                200, json={"id": int(pfad.rsplit("/", 1)[-1]), "status": self.befehl_status}
            )
        return httpx.Response(404)

    def gesendet(self, methode: str, anfang: str = "") -> list[Anruf]:
        return [a for a in self.anrufe if a.methode == methode and a.pfad.startswith(anfang)]


def einrichten(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ArrAttrappe:
    """Radarr und Sonarr eintragen und ihre Leitung auf die Attrappe legen."""
    antwort = admin_client.put(
        "/api/settings",
        json={
            "radarr_url": RADARR,
            "radarr_api_key": "test-radarr-key",
            "sonarr_url": SONARR,
            "sonarr_api_key": "test-sonarr-key",
        },
    )
    assert antwort.status_code == 200, antwort.text
    attrappe = ArrAttrappe()
    monkeypatch.setattr(
        arr, "_client", httpx.AsyncClient(transport=httpx.MockTransport(attrappe.handler))
    )
    # Der gemerkte Abgleich stammt sonst womoeglich aus einem anderen Test.
    download_haenger.vergessen()
    return attrappe


def film(
    nummer: int = 11,
    download_id: str = "D1",
    *,
    film_id: int | None = 5,
    texte: tuple[str, ...] = ("Sample",),
    zustand: str = "importPending",
    meldung: str = "warning",
    programm: str = "completed",
    fehler: str | None = None,
    groesse: int = 1000,
    rest: int = 0,
    release: str = "Beispielfilm.2010.1080p.BluRay-GRP",
) -> dict:
    zeile: dict = {
        "id": nummer,
        "downloadId": download_id,
        "title": release,
        "status": programm,
        "trackedDownloadStatus": meldung,
        "trackedDownloadState": zustand,
        "statusMessages": [{"title": release, "messages": list(texte)}] if texte else [],
        "errorMessage": fehler,
        "protocol": "torrent",
        "downloadClient": "qBittorrent",
        "size": groesse,
        "sizeleft": rest,
        "timeleft": "00:10:00" if rest else None,
    }
    if film_id is not None:
        zeile["movieId"] = film_id
        zeile["movie"] = {"id": film_id, "title": "Beispielfilm", "year": 2010}
    return zeile


def folge(
    nummer: int,
    download_id: str = "S1",
    *,
    serie_id: int = 7,
    staffel: int = 1,
    folge_nummer: int = 1,
    folge_id: int = 701,
    texte: tuple[str, ...] = ("Sample",),
    zustand: str = "importPending",
    meldung: str = "warning",
    groesse: int = 5000,
    rest: int = 0,
) -> dict:
    release = "Beispielserie.S01.1080p.WEB-GRP"
    return {
        "id": nummer,
        "downloadId": download_id,
        "title": release,
        "seriesId": serie_id,
        "episodeId": folge_id,
        "seasonNumber": staffel,
        "series": {"id": serie_id, "title": "Beispielserie", "year": 2019},
        "episode": {"id": folge_id, "seasonNumber": staffel, "episodeNumber": folge_nummer},
        "status": "completed",
        "trackedDownloadStatus": meldung,
        "trackedDownloadState": zustand,
        "statusMessages": [{"title": release, "messages": list(texte)}] if texte else [],
        "protocol": "usenet",
        "downloadClient": "SABnzbd",
        "size": groesse,
        "sizeleft": rest,
    }
