"""Genau ein Medienserver-Abgleich zur Zeit.

Der Anlass steht im Log zu Issue #7: Der Stundenlauf und der Handknopf
"Sync now" liefen gleichzeitig, sauber verschraenkt, und stellten demselben
Jellyfin dieselben neunzehn Fragen zu je 53 Sekunden - zweimal. Auf einem
gesunden Server faellt das nicht auf. Auf einem langsamen ist es der
Unterschied zwischen 17 und 34 Minuten, jede Stunde.

Und es ist nicht nur Last: Beide Laeufe schreiben dieselben Zeilen. Der
Gesehen-Abgleich liest den Bestand und schreibt ihn zurueck - zwei Laeufe,
die sich dabei ins Wort fallen, ueberschreiben sich gegenseitig die
Herkunftsvermerke.

Was diese Datei zusehen muss:

* Wer den Knopf drueckt, waehrend ein Lauf laeuft, wartet - und liest danach
  **nicht** noch einmal, sondern nimmt das Ergebnis dieses Laufs.
* Ist dieser Lauf fuer seinen Server gescheitert, bekommt er den Fehler.
* Laeufe ueberlappen nie - auch der Stundenlauf wartet auf den Knopf.
* Ein Lauf fuer einen anderen Server ersetzt den eigenen nicht.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.services import mediaserver_library, mediaserver_watched
from app.services.mediaserver import LibraryItem, MediaServerError
from app.services.settings_service import load_settings

from .test_mediaserver_library import BibliotheksServer
from .test_mediaserver_login import verbinde

WERKE = [LibraryItem(media_type="movie", guid="p1", title="Dune", tmdb_id=438631, year=2021)]


class LangsamerServer(BibliotheksServer):
    """Ein Server, der beim Lesen so lange steht, bis der Test ihn freigibt."""

    def __init__(
        self, werke: list[LibraryItem], fehler: MediaServerError | None = None
    ) -> None:
        super().__init__(werke)
        self.freigabe = asyncio.Event()
        self.fehler = fehler
        self.gleichzeitig = 0
        self.hoechstens_gleichzeitig = 0

    async def library_index(self) -> list[LibraryItem]:
        self.abrufe += 1
        self.gleichzeitig += 1
        self.hoechstens_gleichzeitig = max(self.hoechstens_gleichzeitig, self.gleichzeitig)
        try:
            await self.freigabe.wait()
        finally:
            self.gleichzeitig -= 1
        if self.fehler is not None:
            raise self.fehler
        return self.werke


@pytest.fixture(autouse=True)
def frische_sperre(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jeder Test seine eigene Sperre.

    Eine ``asyncio.Lock`` bindet sich an die Schleife, in der zum ersten Mal
    auf sie gewartet wurde - und pytest baut je Test eine neue.
    """
    monkeypatch.setattr(mediaserver_library, "_sperre", asyncio.Lock())


async def _gesehen_stumm(db, settings) -> int:  # noqa: ANN001
    return 0


async def _bis(bedingung: Callable[[], bool], schritte: int = 50) -> None:
    """Der Schleife so lange den Vortritt lassen, bis die Bedingung gilt."""
    for _ in range(schritte):
        if bedingung():
            return
        await asyncio.sleep(0)
    raise AssertionError("die Bedingung ist nicht eingetreten")


async def test_handknopf_waehrend_des_laufs_liest_nicht_noch_einmal(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Der Waechter dieser Datei: ein Server, ein Lesen."""
    verbinde(admin_client)
    server = LangsamerServer(WERKE)
    monkeypatch.setattr(mediaserver_library, "media_server_for_setup", lambda _s, _a: server)
    monkeypatch.setattr(mediaserver_watched, "refresh", _gesehen_stumm)

    with SessionLocal() as db_lauf, SessionLocal() as db_knopf:
        lauf = asyncio.create_task(
            mediaserver_library.voller_abgleich(db_lauf, load_settings(db_lauf))
        )
        await _bis(lambda: server.abrufe == 1)  # der Lauf steht jetzt im Lesen

        knopf = asyncio.create_task(
            mediaserver_library.refresh(
                db_knopf, load_settings(db_knopf), streng=True, provider="plex"
            )
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert server.abrufe == 1, "der Knopf hat nicht gewartet"

        server.freigabe.set()
        await lauf
        assert await knopf == 1

    assert server.abrufe == 1, "der Knopf hat den Server ein zweites Mal gelesen"


async def test_wartender_handknopf_erbt_den_fehler_des_laufs(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer streng fragt, bekommt die Wahrheit - auch die des Laufs, den er nahm.

    Sonst saehe er den alten Zaehler mit frischem Zeitstempel: genau der
    scheinbare Erfolg aus Issue #2.
    """
    verbinde(admin_client)
    server = LangsamerServer(
        WERKE,
        fehler=MediaServerError("Server aus", code="mediaserver_timeout", service="Plex"),
    )
    monkeypatch.setattr(mediaserver_library, "media_server_for_setup", lambda _s, _a: server)
    monkeypatch.setattr(mediaserver_watched, "refresh", _gesehen_stumm)

    with SessionLocal() as db_lauf, SessionLocal() as db_knopf:
        lauf = asyncio.create_task(
            mediaserver_library.voller_abgleich(db_lauf, load_settings(db_lauf))
        )
        await _bis(lambda: server.abrufe == 1)
        knopf = asyncio.create_task(
            mediaserver_library.refresh(
                db_knopf, load_settings(db_knopf), streng=True, provider="plex"
            )
        )
        await asyncio.sleep(0)
        server.freigabe.set()

        await lauf  # der Stundenlauf schluckt den Fehler - das ist sein Auftrag
        with pytest.raises(MediaServerError) as gefangen:
            await knopf

    assert gefangen.value.code == "mediaserver_timeout"
    assert server.abrufe == 1


async def test_laeufe_ueberlappen_nie(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch der Stundenlauf wartet - auf den Knopf, der vor ihm dran war.

    Danach liest er selbst: Der Gesehen-Stand ist faellig, und der haengt an
    der Bibliothek. Zweimal nacheinander ist in Ordnung, gleichzeitig nicht.
    """
    verbinde(admin_client)
    server = LangsamerServer(WERKE)
    monkeypatch.setattr(mediaserver_library, "media_server_for_setup", lambda _s, _a: server)
    monkeypatch.setattr(mediaserver_watched, "refresh", _gesehen_stumm)

    with SessionLocal() as db_knopf, SessionLocal() as db_lauf:
        knopf = asyncio.create_task(
            mediaserver_library.refresh(
                db_knopf, load_settings(db_knopf), streng=True, provider="plex"
            )
        )
        await _bis(lambda: server.abrufe == 1)
        lauf = asyncio.create_task(
            mediaserver_library.voller_abgleich(db_lauf, load_settings(db_lauf))
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert server.abrufe == 1, "der Stundenlauf hat nicht gewartet"

        server.freigabe.set()
        assert await knopf == 1
        await lauf

    assert server.abrufe == 2
    assert server.hoechstens_gleichzeitig == 1


async def test_ein_lauf_fuer_den_anderen_server_ersetzt_den_eigenen_nicht(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer auf einen Lauf wartet, der seinen Server nicht las, liest selbst.

    Der Fall ist das Verbinden: Der Stundenlauf begann, als es den neuen
    Server noch nicht gab. Sein Ergebnis zu nehmen hiesse, den neuen Server
    eine Stunde lang nicht zu kennen.
    """
    verbinde(admin_client)
    plex = LangsamerServer(WERKE)
    jelly = BibliotheksServer(
        [LibraryItem(media_type="movie", guid="j1", title="Heat", tmdb_id=949, year=1995)]
    )
    jelly.provider = "jellyfin"
    monkeypatch.setattr(
        mediaserver_library, "verbundene_anbieter", lambda _s: ["plex", "jellyfin"]
    )
    monkeypatch.setattr(
        mediaserver_library,
        "media_server_for_setup",
        lambda _s, anbieter: {"plex": plex, "jellyfin": jelly}[anbieter],
    )

    with SessionLocal() as db_plex, SessionLocal() as db_alle:
        nur_plex = asyncio.create_task(
            mediaserver_library.refresh(
                db_plex, load_settings(db_plex), streng=True, provider="plex"
            )
        )
        await _bis(lambda: plex.abrufe == 1)
        alle = asyncio.create_task(
            mediaserver_library.refresh(db_alle, load_settings(db_alle))
        )
        await asyncio.sleep(0)
        plex.freigabe.set()

        assert await nur_plex == 1
        assert await alle == 2

    assert jelly.abrufe == 1, "der neue Server wurde nicht gelesen"
