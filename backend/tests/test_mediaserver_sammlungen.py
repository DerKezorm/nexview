"""Sammlungen duerfen keine Filme verschlucken.

Der Anlass steht in Issue #10: Jellyfin und Emby des Melders zeigten dieselbe
Bibliothek, Nexview las aber 845 Filme aus Emby und 595 aus Jellyfin - und 104
der 595 hiessen "Alien Collection", "Die Hard Collection" und so weiter.
Jellyfins Einstellung "Filme zu Sammlungen gruppieren" klappt in ``/Items``
jeden Film einer Sammlung in einen ``BoxSet``-Eintrag ein, und Nexview nahm
die Sammlung als Film.

Die Doppel unten verhalten sich wie am 18.09.2026 gemessen (Jellyfin 10.11.11,
Emby 4.9.5.0, eine Sammlung aus zwei von drei Filmen):

* Jellyfin mit der Einstellung: ``BoxSet`` plus der dritte Film - ausser mit
  ``CollapseBoxSetItems=false``. ``GroupItemsIntoCollections`` aendert nichts.
* Emby: klappt nur mit ``GroupItemsIntoCollections=true`` ein.
  ``CollapseBoxSetItems`` aendert nichts.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.mediaserver.emby import EmbyServer
from app.services.mediaserver.jellyfin import JellyfinServer, _als_werk

EINSTELLUNGEN = SimpleNamespace(
    mediaserver_url="http://jelly.test",
    mediaserver_token="token",
    mediaserver_machine_id="maschine",
    mediaserver_client_identifier="nexview-test",
    mediaserver_account_id="konto-1",
)

FILME = [
    {
        "Id": f"film-{n}",
        "Type": "Movie",
        "Name": name,
        "ProductionYear": 2000 + n,
        "ProviderIds": {"Tmdb": str(100 + n)},
        "UserData": {"Played": n == 0},
    }
    for n, name in enumerate(["Alpha", "Beta", "Gamma"])
]
SAMMLUNG = {
    "Id": "sammlung-1",
    "Type": "BoxSet",
    "Name": "Alpha Collection",
    "ProductionYear": 2000,
    "ProviderIds": {"Tmdb": "9999"},
    "UserData": {"Played": False},
}
# Alpha und Beta stecken in der Sammlung, Gamma nicht.
EINGEKLAPPT = [SAMMLUNG, FILME[2]]


class Doppel:
    """Antwortet auf ``/Items`` wie der jeweilige Server und schreibt mit."""

    def __init__(self) -> None:
        super().__init__(EINSTELLUNGEN)  # type: ignore[call-arg]
        self.abfragen: list[dict[str, Any]] = []

    def klappt_ein(self, params: dict[str, Any]) -> bool:
        raise NotImplementedError

    async def _eigene_konto_id(self, token: str | None = None) -> str:
        return "konto-1"

    async def _anfrage(self, methode: str, pfad: str, **kwargs: Any) -> Any:
        params = dict(kwargs.get("params") or {})
        self.abfragen.append(params)
        art = params.get("IncludeItemTypes")
        if art != "Movie":
            return {"Items": [], "TotalRecordCount": 0}
        filme = EINGEKLAPPT if self.klappt_ein(params) else FILME
        if params.get("Filters") == "IsPlayed":
            filme = [f for f in filme if f["UserData"]["Played"]]
        start = int(params.get("StartIndex", 0))
        ende = start + int(params.get("Limit", len(filme)))
        return {"Items": filme[start:ende], "TotalRecordCount": len(filme)}


class GruppierenderJellyfin(Doppel, JellyfinServer):
    """Jellyfin mit eingeschaltetem "Filme zu Sammlungen gruppieren"."""

    def klappt_ein(self, params: dict[str, Any]) -> bool:
        return params.get("CollapseBoxSetItems") != "false"


class GruppierendesEmby(Doppel, EmbyServer):
    def klappt_ein(self, params: dict[str, Any]) -> bool:
        return params.get("GroupItemsIntoCollections") == "true"


@pytest.mark.asyncio
@pytest.mark.parametrize("server_klasse", [GruppierenderJellyfin, GruppierendesEmby])
async def test_jeder_film_kommt_einzeln(server_klasse: type[Doppel]) -> None:
    server = server_klasse()

    werke = await server.library_index()

    assert sorted(w.title for w in werke if w.media_type == "movie") == ["Alpha", "Beta", "Gamma"]
    film_abfragen = [p for p in server.abfragen if p.get("IncludeItemTypes") == "Movie"]
    assert film_abfragen, "keine Film-Abfrage gezaehlt"


@pytest.mark.asyncio
@pytest.mark.parametrize("server_klasse", [GruppierenderJellyfin, GruppierendesEmby])
async def test_gesehener_film_in_einer_sammlung_bleibt_gesehen(
    server_klasse: type[Doppel],
) -> None:
    """Der Gesehen-Stand fragt ``/Items`` getrennt - er braucht dieselbe Angabe."""
    server = server_klasse()

    stand = await server.watched_index("token", "konto-1")

    assert [s.item_key for s in stand if s.media_type == "movie"] == ["film-0"]


def test_eine_sammlung_ist_kein_film() -> None:
    """Das zweite Netz: Kommt doch eine Sammlung, wird sie nicht zum Film."""
    assert _als_werk(SAMMLUNG, "movie") is None
    assert _als_werk({**FILME[0], "Type": "Movie"}, "tv") is None
    assert _als_werk(FILME[0], "movie") is not None
    # Ohne Typ gilt die angefragte Art.
    ohne_typ = {k: v for k, v in FILME[0].items() if k != "Type"}
    assert _als_werk(ohne_typ, "movie") is not None
