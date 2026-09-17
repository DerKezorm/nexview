"""Ein getrennter Medienserver gehoert nicht mehr in Vergleich und Abgleich.

Gemeldet am 17.09.2026 nach 0.35.0: Auf einer Anlage, die Emby einmal verbunden
und wieder getrennt hatte, stand Emby als dritte Spalte in der
Vergleichstabelle - mit dem Stand von damals. Beim Trennen blieben die
eingelesenen Zeilen liegen, und der Vergleich las alle.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.crypto import encrypt
from app.db import SessionLocal
from app.models import MediaServerConnection, MediaServerLibraryItem, MediaType
from app.services import abgleich, mediaserver_library, server_vergleich
from app.services.settings_service import load_settings

from .test_mediaserver_login import FakeMediaServer, fake_server, verbinde  # noqa: F401

PFAD = "/api/admin/analyse/server-vergleich"


def _titel(provider: str, tmdb: int, titel: str = "Ein Film") -> None:
    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider=provider,
                media_type=MediaType.movie,
                guid=f"{provider}:{tmdb}",
                tmdb_id=tmdb,
                title=titel,
                title_key=server_vergleich.titel_schluessel(titel),
                year=2020,
            )
        )
        session.commit()


def _anbieter_in_der_tabelle() -> set[str]:
    with SessionLocal() as session:
        return set(session.scalars(select(MediaServerLibraryItem.provider)))


def test_vergleich_zeigt_nur_verbundene_server(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_vergleich, "verbundene", lambda _s: {"plex", "jellyfin"})
    _titel("plex", 1)
    _titel("jellyfin", 1)
    _titel("emby", 2, "Nur im alten Emby")

    antwort = admin_client.get(PFAD, params={"ansicht": "alle"}).json()
    assert [s["anbieter"] for s in antwort["server"]] == ["plex", "jellyfin"]
    assert [z["titel"] for z in antwort["zeilen"]] == ["Ein Film"]
    assert antwort["anzahl"]["unterschiede"] == 0


def test_abgleich_zaehlt_getrennte_server_nicht_mit(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_vergleich, "verbundene", lambda _s: {"plex", "jellyfin"})

    async def leer(_settings):
        return set(), set(), {}

    monkeypatch.setattr(abgleich, "_arr_bestand", leer)
    _titel("plex", 1)
    _titel("jellyfin", 1)
    _titel("emby", 2)

    with SessionLocal() as session:
        stand = asyncio.run(abgleich.messen(session, load_settings(session)))
    assert stand.je_anbieter == {"plex": 1, "jellyfin": 1}
    assert stand.anbieter_luecke == 0


def test_alte_zeilen_getrennter_server_werden_aufgeraeumt(admin_client: TestClient) -> None:
    """Was aeltere Fassungen beim Trennen liegen liessen."""
    with SessionLocal() as session:
        session.add(
            MediaServerConnection(
                provider="plex", machine_id="m", name="Plex", url="http://plex", token=encrypt("t")
            )
        )
        session.commit()
    _titel("plex", 1)
    _titel("emby", 2)

    with SessionLocal() as session:
        entfernt = mediaserver_library.verwaiste_entfernen(session, load_settings(session))
    assert entfernt == 1
    assert _anbieter_in_der_tabelle() == {"plex"}


def test_trennen_nimmt_die_bibliothek_mit(
    admin_client: TestClient, fake_server: FakeMediaServer  # noqa: F811
) -> None:
    verbinde(admin_client)
    _titel("plex", 1)
    _titel("jellyfin", 3)

    assert admin_client.delete("/api/admin/mediaserver/connection?provider=plex").status_code == 204
    # Nur die des getrennten Servers - die des anderen bleiben.
    assert _anbieter_in_der_tabelle() == {"jellyfin"}
