"""Konto aufloesen: was Radarr und Sonarr dabei zu tun bekommen.

Umgezogen aus ``services/kontoaufloesung.py`` (Scheibe 2 des NEX-Umbaus),
ohne Aenderung im Verhalten. Dort bleibt die Entscheidung (was behalten,
was loeschen, was ans Haus), hier steht, wie sie in Radarr und Sonarr
ausgefuehrt wird.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....models import MediaRequest, MediaType, StorageEntry
from ...settings_service import AppSettings
from . import library


async def laufende_aufloesen(
    db: Session, settings: AppSettings, laufend: Any, *, behalten: bool, weiter: bool
) -> bool:
    """Eine angefangene Staffel bzw. Serie nach der Wahl des Administrators.

    ``False`` heisst: nichts zu tun (keine Instanz, keine Kennung dort, oder
    behalten und weiterladen).
    """
    client = library.sonarr_client(settings, laufend.tier)
    if client is None or laufend.arr_id is None:
        return False
    if behalten and weiter:
        # Laeuft weiter und faellt fertig ans Haus - dieselbe Regel wie
        # bei der Haus-Uebernahme.
        return False
    if laufend.season is not None:
        if not behalten:
            kennungen = [
                int(datei["id"])
                for datei in await client.episode_files(
                    laufend.arr_id, laufend.season
                )
                if datei.get("id")
            ]
            await client.unmonitor_season(laufend.arr_id, laufend.season)
            if kennungen:
                await client.delete_episode_files(kennungen)
        else:
            await client.unmonitor_season(laufend.arr_id, laufend.season)
    else:
        # Ganze Serie: stilllegen deckt "nicht weiter" wie "loeschen" ab -
        # geloescht werden dann zusaetzlich die Dateien der Staffeln, die
        # **nicht** als Posten gebucht waren (die gebuchten haben ihre
        # eigene Entscheidung schon hinter sich).
        await client.serie_stilllegen(laufend.arr_id)
        if not behalten:
            gebucht = {
                z.season
                for z in db.scalars(
                    select(StorageEntry).where(
                        StorageEntry.tvdb_id
                        == db.get(MediaRequest, laufend.request_id).tvdb_id
                    )
                )
            }
            dateien = await client.get(
                "/episodefile", {"seriesId": laufend.arr_id}
            ) or []
            kennungen = [
                int(datei["id"])
                for datei in dateien
                if isinstance(datei, dict)
                and datei.get("id")
                and datei.get("seasonNumber") not in gebucht
            ]
            if kennungen:
                await client.delete_episode_files(kennungen)
    return True


async def bestellung_zuruecknehmen(settings: AppSettings, anfrage: MediaRequest) -> None:
    """Eine Bestellung ohne Dateien aus der Instanz nehmen. Wirft ``ArrError``."""
    if anfrage.media_type == MediaType.movie:
        client = library.radarr_client(settings, anfrage.tier)
        if client is not None and anfrage.arr_id:
            # ``delete_files=True`` als Schutznetz: Sollte in der
            # letzten Sekunde doch eine Datei angekommen sein, wandert
            # sie in den Papierkorb statt verwaist liegenzubleiben.
            await client.remove(anfrage.arr_id, delete_files=True)
    else:
        client = library.sonarr_client(settings, anfrage.tier)
        if (
            client is not None
            and anfrage.arr_id
            and anfrage.season is not None
        ):
            await client.unmonitor_season(anfrage.arr_id, anfrage.season)
        elif client is not None and anfrage.arr_id:
            await client.serie_stilllegen(anfrage.arr_id)
