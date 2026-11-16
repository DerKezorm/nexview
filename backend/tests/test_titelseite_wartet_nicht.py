"""Die Titelseite fragt nexcrate jeden Titel einmal, und das gleichzeitig.

Gemessen am 16.11.2026 (Attrappe mit 0,3 bis 0,8 s je nexcrate-Antwort, wie
die Synology): Ein Film brauchte vier Sekunden, eine Serie sechs, und fast
alles davon war Warten auf nexcrate, eins nach dem anderen. Jede Fassung
fragte dieselben Titel noch einmal mit demselben Koerper (die Antwort nennt
alle Fassungen), Empfehlungen und Filmreihe warteten auf den Titel, und eine
Serie las ihre Einzelansicht viermal und jede Staffel zweimal.

Die Attrappe antwortet hier mit einer kleinen Pause, damit sich zeigt, ob
Abfragen nebeneinander laufen.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.routers import details as details_router
from app.schemas_media import MediaDetail, MediaItem, MovieCollection, SeasonInfo
from app.services.beschaffung import NEX
from app.services.beschaffung.nex import bestand as nex_bestand
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import save_settings

from .beschaffung.fake_nexcrate import (
    FILM_HD,
    FILM_UHD,
    KEY,
    SERIE_HD,
    SERIE_UHD,
    URL,
    FakeNexcrate,
)

PAUSE = 0.05


class _Langsam(httpx.AsyncBaseTransport):
    """Die Attrappe, aber jede Antwort braucht ``PAUSE`` - und es wird mitgezaehlt."""

    def __init__(self, attrappe: FakeNexcrate, *, stumm_fuer: set[str] | None = None) -> None:
        self.attrappe = attrappe
        self.stumm_fuer = stumm_fuer or set()
        self.laufend = 0
        self.hoechstens = 0
        #: ``(Methode, Pfad, gefragte refs)`` je Abfrage.
        self.gefragt: list[tuple[str, str, list[str]]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        koerper = json.loads(request.content) if request.content else {}
        refs = [str(item.get("ref")) for item in (koerper or {}).get("items") or []]
        self.gefragt.append((request.method, request.url.path, refs))
        self.laufend += 1
        self.hoechstens = max(self.hoechstens, self.laufend)
        try:
            await asyncio.sleep(PAUSE)
        finally:
            self.laufend -= 1
        if self.stumm_fuer & set(refs):
            raise httpx.ConnectError("nexcrate antwortet nicht", request=request)
        return self.attrappe._handle(request)

    def lookups(self) -> list[list[str]]:
        return [refs for methode, pfad, refs in self.gefragt if pfad.endswith("/titles/lookup")]

    def gelesen(self) -> list[str]:
        return [pfad for methode, pfad, _ in self.gefragt if methode == "GET"]


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    attrappe = FakeNexcrate()
    system.merken(attrappe._system())
    nex_fassungen.vergessen()
    nex_bestand.verwerfen()
    with SessionLocal() as sitzung:
        save_settings(sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY})
        nex_fassungen.schreiben(sitzung, attrappe.versions)
        sitzung.commit()
    try:
        yield attrappe
    finally:
        nex_client.use_transport(None)
        system.vergessen()
        nex_fassungen.vergessen()
        nex_bestand.verwerfen()


def _langsam(attrappe: FakeNexcrate, **optionen: Any) -> _Langsam:
    transport = _Langsam(attrappe, **optionen)
    nex_client.use_transport(transport)
    return transport


def _film(nummer: int) -> MediaItem:
    return MediaItem(tmdb_id=nummer, media_type="movie", title=f"Erfundener Film {nummer}")


def _filmseite(monkeypatch: pytest.MonkeyPatch, nummer: int) -> None:
    """Ein Film mit drei Empfehlungen und zwei weiteren Teilen der Reihe, ohne TMDB."""

    async def _detail(_db, _settings, _art, tmdb_id, **_rest):
        return MediaDetail(
            tmdb_id=tmdb_id,
            media_type="movie",
            title="Erfundener Film",
            recommendations=[_film(nummer + n) for n in (1, 2, 3)],
            collection=MovieCollection(
                id=1, name="Erfundene Reihe", items=[_film(nummer + n) for n in (4, 5)]
            ),
        )

    monkeypatch.setattr(details_router.media, "full_detail", _detail)


def _filme_in_nexcrate(attrappe: FakeNexcrate, nummer: int) -> None:
    for n in range(6):
        attrappe.film(
            nummer + n,
            versionen=[
                attrappe.fassung(FILM_HD, "available", size_bytes=8_000_000_000),
                attrappe.fassung(FILM_UHD, "available" if n % 2 else "wanted"),
            ],
        )


def test_die_filmseite_fragt_jeden_titel_nur_einmal(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HD und 4K stehen in derselben Antwort; die 4K-Achse fragte trotzdem
    jeden Titel ein zweites Mal - auf der Seite eines Films sechs Abfragen
    statt drei, je 0,3 bis 0,8 s auf der Synology."""
    _filme_in_nexcrate(nexcrate, 9800)
    _filmseite(monkeypatch, 9800)
    transport = _langsam(nexcrate)

    antwort = admin_client.get("/api/detail/movie/9800")

    assert antwort.status_code == 200, antwort.text
    gefragt = Counter(ref for refs in transport.lookups() for ref in refs)
    assert gefragt, "die Seite hat nexcrate gar nicht gefragt"
    assert max(gefragt.values()) == 1, gefragt
    # Und dabei steht alles da wie vorher, je Fassung.
    daten = antwort.json()
    assert daten["status"] == "downloaded"
    assert daten["status_unconfirmed"] is False
    vierk = {e["tmdb_id"]: e["status_uhd"] for e in daten["recommendations"]}
    assert vierk == {9801: "downloaded", 9802: "not_requested", 9803: "downloaded"}
    assert {e["tmdb_id"]: e["status"] for e in daten["collection"]["items"]} == {
        9804: "downloaded",
        9805: "downloaded",
    }


def test_titel_empfehlungen_und_reihe_warten_nicht_aufeinander(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keins braucht die Antwort eines anderen. Nacheinander summierten sich
    die Wartezeiten, und auf der Synology stand die Seite vier Sekunden."""
    _filme_in_nexcrate(nexcrate, 9810)
    _filmseite(monkeypatch, 9810)
    transport = _langsam(nexcrate)

    assert admin_client.get("/api/detail/movie/9810").status_code == 200

    assert len(transport.lookups()) == 3, transport.lookups()
    assert transport.hoechstens == 3, "Titel, Empfehlungen und Reihe liefen nacheinander"


def test_schweigt_nexcrate_nur_zu_den_empfehlungen_bleibt_der_titel_bestaetigt(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gleichzeitig heisst nicht gemeinsam: Was die Empfehlungen nicht lesen
    konnten, macht den Stand des Titels nicht unbestaetigt (``lesestand``)."""
    _filme_in_nexcrate(nexcrate, 9820)
    _filmseite(monkeypatch, 9820)
    _langsam(nexcrate, stumm_fuer={"tmdb:9821"})

    antwort = admin_client.get("/api/detail/movie/9820")

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["status"] == "downloaded"
    assert daten["status_unconfirmed"] is False


def _serienseite(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _detail(_db, _settings, _art, tmdb_id, **_rest):
        return MediaDetail(
            tmdb_id=tmdb_id,
            media_type="tv",
            title="Erfundene Serie",
            release_date="2015-01-01",
            seasons=[
                SeasonInfo(season_number=n, name=f"Staffel {n}", episode_count=2) for n in (1, 2, 3)
            ],
        )

    monkeypatch.setattr(details_router.media, "full_detail", _detail)


def test_die_serienseite_liest_einzelansicht_und_staffeln_nur_einmal(
    admin_client: TestClient, nexcrate: FakeNexcrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Je Fassung lasen ``folgen_verfuegbarkeit`` und ``serien_eintrag`` die
    Einzelansicht, und jede Fassung jede Staffel: bei zwei Fassungen und
    sechs Staffeln zwanzig Abfragen statt neun."""
    ref = "tmdb:9830"
    staffeln = [
        nexcrate.staffel_eintrag(
            n,
            [
                nexcrate.staffel_fassung(SERIE_HD, "available", counts={"have": 2, "aired": 2, "expected": 2}),
                nexcrate.staffel_fassung(SERIE_UHD, "wanted"),
            ],
            folgen=2,
            gesendet=2,
        )
        for n in (1, 2, 3)
    ]
    nexcrate.serie(
        9830,
        versionen=[nexcrate.fassung(SERIE_HD, "available"), nexcrate.fassung(SERIE_UHD, "wanted")],
        staffeln=staffeln,
    )
    for n in (1, 2, 3):
        nexcrate.staffel(
            ref,
            n,
            [
                nexcrate.folge(f, versionen=[nexcrate.folgen_fassung(SERIE_HD, "available", files=[{"id": f}])])
                for f in (1, 2)
            ],
        )
    _serienseite(monkeypatch)
    transport = _langsam(nexcrate)

    antwort = admin_client.get("/api/detail/tv/9830")

    assert antwort.status_code == 200, antwort.text
    gelesen = Counter(transport.gelesen())
    assert gelesen, "die Seite hat die Staffeln gar nicht gelesen"
    assert max(gelesen.values()) == 1, gelesen
    gefragt = Counter(r for refs in transport.lookups() for r in refs)
    assert max(gefragt.values()) == 1, gefragt
    # Der Stand des Titels und die Staffeln laufen nebeneinander.
    assert transport.hoechstens >= 2
    daten = antwort.json()
    assert daten["status_unconfirmed"] is False
    for staffel in daten["seasons"]:
        hd = next(f for f in staffel["fassungen"] if f["kennung"] == SERIE_HD)
        assert hd["episodes_available"] == 2
