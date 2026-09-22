"""Speicherposten in Radarr und Sonarr: finden, Dateien nennen, loeschen.

Umgezogen aus ``services/storage.py`` (Scheibe 2 des NEX-Umbaus), ohne
Aenderung im Verhalten. Dort bleibt, was fuer jeden Weg gilt: Posten,
Protokoll, Anfragen schliessen. Hier steht, wie Radarr und Sonarr einen
Posten kennen und wie man seine Dateien los wird.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ....models import MediaType, StorageEntry
from ...settings_service import AppSettings
from ..base import NichtsZuLoeschen
from . import library

logger = logging.getLogger("nexview.storage")


async def _client_und_kennung(settings: AppSettings, zeile: StorageEntry):
    """Wie heisst dieser Posten in Radarr bzw. Sonarr? ``(client, arr_id)``.

    Ueber die Bibliothek und nicht ueber die Anfrage: Ein Posten kann ganz ohne
    Anfrage entstanden sein (Altbestand), und eine zurueckgezogene Anfrage
    darf das Loeschen nicht unmoeglich machen.
    """
    stufe = zeile.tier.value
    if zeile.media_type == MediaType.movie:
        client = library.radarr_client(settings, stufe)
        if client is None or not zeile.tmdb_id:
            return None, None
        eintrag = (await library.movie_library(settings, stufe)).get(zeile.tmdb_id)
        return client, (eintrag.arr_id if eintrag else None)

    client = library.sonarr_client(settings, stufe)
    if client is None or not zeile.tvdb_id:
        return None, None
    nach_tvdb, _ = await library.series_library(settings, stufe)
    eintrag = nach_tvdb.get(zeile.tvdb_id)
    return client, (eintrag.arr_id if eintrag else None)


def _client(settings: AppSettings, zeile: StorageEntry):
    stufe = zeile.tier.value
    if zeile.media_type == MediaType.movie:
        return library.radarr_client(settings, stufe)
    return library.sonarr_client(settings, stufe)


async def kennung(settings: AppSettings, zeile: StorageEntry) -> int | None:
    """Die Nummer des Postens in der Instanz - ``None``, wenn sie ihn nicht fuehrt.

    Fehler der Instanz kommen ungefiltert heraus: Wer fragt, entscheidet, was
    ein stummes Radarr an dieser Stelle heisst.
    """
    client, arr_id = await _client_und_kennung(settings, zeile)
    return arr_id if client is not None else None


async def staffel_stilllegen(settings: AppSettings, zeile: StorageEntry) -> int | None:
    """Ueberwachung der Staffel eines Postens aus. Die Nummer, oder ``None``,
    wenn die Serie nicht mehr in Sonarr liegt (dann ist nichts zu tun)."""
    client, arr_id = await _client_und_kennung(settings, zeile)
    if client is None or arr_id is None:
        return None
    await client.unmonitor_season(arr_id, zeile.season)
    return arr_id


#: Liefert die Folgen eines Paket-Postens oder ``None``. Erst gefragt, wenn
#: die Instanz geantwortet hat - die Reihenfolge der Fehler bleibt so wie vor
#: dem Umzug (erst die Instanz, dann die Zuordnung).
PaketFolgen = Callable[[], list[int] | None]


async def dateien(
    settings: AppSettings, zeile: StorageEntry, arr_id: int, paket_folgen: PaketFolgen
) -> list[tuple[str, int]]:
    """Welche Dateien fielen weg, als ``(Pfad, Bytes)``? Es wird nichts angefasst.

    ``arr_id`` aus ``kennung``. Bei Paket-Posten nur die Dateien der eigenen
    Folgen.
    """
    client = _client(settings, zeile)
    if zeile.media_type == MediaType.movie:
        filme = await library.movie_library(settings, zeile.tier.value)
        eintrag = filme.get(zeile.tmdb_id or 0)
        if eintrag is None or not eintrag.has_file:
            return []
        return [(eintrag.path, eintrag.size_bytes)]

    gefunden: list[dict[str, Any]] = await client.episode_files(arr_id, zeile.season)
    folgen_nummern = paket_folgen()
    if folgen_nummern is not None:
        # Ein Paket-Posten trifft nur die Dateien seiner eigenen Folgen.
        stand = await client.folgen_stand(arr_id)
        staffel = stand.get(zeile.season) or {}
        eigene_dateien = {
            folge.datei_id
            for nummer in folgen_nummern
            if (folge := staffel.get(nummer)) is not None and folge.datei_id
        }
        gefunden = [datei for datei in gefunden if datei.get("id") in eigene_dateien]
    return [
        (
            str(datei.get("path") or datei.get("relativePath") or ""),
            int(datei.get("size") or 0),
        )
        for datei in gefunden
    ]


async def loeschen(
    settings: AppSettings, zeile: StorageEntry, arr_id: int, paket_folgen: PaketFolgen
) -> None:
    """Die Dateien eines Postens ueber die Instanz entfernen (``arr_id`` aus ``kennung``).

    Wirft ``ArrError`` bei Fehlern der Instanz und ``NichtsZuLoeschen``, wenn
    sie fuer eine Staffel oder ein Paket keine Dateien meldet.
    """
    client = _client(settings, zeile)

    if zeile.media_type == MediaType.movie:
        await client.remove(arr_id, delete_files=True)
        return

    # ⚠️ **Erst stilllegen, dann loeschen.** Sonarr sucht fuer jede
    # ueberwachte Staffel nach fehlenden Folgen; bliebe sie an, waere
    # die Staffel beim naechsten Durchlauf wieder da - und der Nutzer,
    # der abgegeben hat, saehe seinen Speicher erneut steigen.
    #
    # Die Reihenfolge ist der Punkt: Scheitert das Stilllegen, liegen
    # die Dateien noch da und nichts ist verloren. Andersherum waeren
    # sie weg **und** kaemen zurueck.
    folgen_nummern = paket_folgen()
    if folgen_nummern is not None:
        # Ein Paket-Posten: genau die eigenen Folgen stilllegen und
        # nur deren Dateien loeschen - die Staffel gehoert anderen mit.
        stand = await client.folgen_stand(arr_id)
        staffel = stand.get(zeile.season) or {}
        eigene = [
            folge
            for nummer in folgen_nummern
            if (folge := staffel.get(nummer)) is not None
        ]
        if eigene:
            await client.folgen_schalten([folge.kennung for folge in eigene], False)
        datei_ids = [folge.datei_id for folge in eigene if folge.datei_id]
        if not datei_ids:
            raise NichtsZuLoeschen(
                "Sonarr meldet fuer dieses Folgen-Paket keine Dateien.",
                409,
            )
        entfernt = await client.delete_episode_files(datei_ids)
        logger.warning(
            "DELETE: removed %s of %s episode files of the package in season %s",
            entfernt,
            len(datei_ids),
            zeile.season,
        )
        return

    await client.unmonitor_season(arr_id, zeile.season)
    kennungen = [
        int(datei["id"])
        for datei in await client.episode_files(arr_id, zeile.season)
        if datei.get("id")
    ]
    if not kennungen:
        raise NichtsZuLoeschen(
            f"Sonarr meldet fuer Staffel {zeile.season} keine Dateien.", 409
        )
    entfernt = await client.delete_episode_files(kennungen)
    logger.warning(
        "DELETE: removed %s of %s files of season %s",
        entfernt,
        len(kennungen),
        zeile.season,
    )
