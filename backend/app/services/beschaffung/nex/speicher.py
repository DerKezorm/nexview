"""Speicherposten bei nexcrate: nachsehen, stilllegen, löschen.

⚠️ **Nichts fasst Dateien an nexcrate vorbei an** (Bauplan 6.4). Die Grenze
kennt keinen Dateipfad, weil nexcrate keinen nennt - gelöscht wird über
`withdraw` mit `delete_files` und dem Umfang. Nicht über `delete-files`
allein: Das ließe die Überwachung an, und nexcrate lüde die Datei gleich
wieder.

⚠️ **Welche Dateien wegfielen, sagt die Größe, nicht der Pfad.** Nexviews
Meldung „dein Posten ist weg, das waren 8 GB" braucht nur die Zahl; der Pfad
stand dort nur, weil Radarr ihn ohnehin mitliefert.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ....models import MediaType
from ..base import NichtsZuLoeschen
from . import bestand, mapping

if TYPE_CHECKING:
    from collections.abc import Callable

    from ....models import StorageEntry
    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")


def _client(settings: AppSettings):
    from .weg import client_fuer

    return client_fuer(settings)


def _umfang(zeile: StorageEntry, folgen: list[int] | None) -> dict:
    """Der Umfang eines Postens in nexcrates Worten.

    Bei Folgen steht ``seasons: []`` ausdruecklich da, wie beim Anfragen
    (``auftraege.umfang``): nexcrate las ein fehlendes ``seasons`` einmal als
    alle Staffeln (Rundgang 2, R2-6).
    """
    if zeile.media_type != MediaType.tv:
        return {}
    if folgen is not None:
        return {
            "series": {
                "episodes": [
                    {"season": zeile.season, "episode": nummer} for nummer in sorted(folgen)
                ],
                "seasons": [],
            }
        }
    if zeile.season is not None:
        return {"series": {"seasons": [zeile.season]}}
    return {"series": {"seasons": "all"}}


async def dateien(
    settings: AppSettings,
    zeile: StorageEntry,
    kennung: int,
    paket_folgen: Callable[[], list[int] | None],
) -> list[tuple[str, int]]:
    """Was an diesem Posten liegt, als ``(Name, Bytes)``. Es wird nichts angefasst.

    Der erste Wert ist bei Arr der Dateipfad; hier steht der Titel samt
    Umfang, denn einen Pfad gibt es nicht. Gezählt wird, was die Antwort
    ohnehin sagt - ein zusätzlicher Aufruf je Datei wäre teuer und brächte
    nichts, das jemand liest.
    """
    art = mapping.kind(zeile.media_type)
    titel = await _client(settings).title(art, mapping.ref(kennung))
    if titel is None:
        return []
    name = str(titel.get("name") or "")

    if zeile.media_type != MediaType.tv:
        stand = bestand.film_stand(titel, zeile.fassung_kennung)
        if stand is None or not stand.has_file:
            return []
        return [(name, stand.size_bytes)]

    folgen = paket_folgen()
    staffeln = (titel.get("series") or {}).get("seasons") or []
    if zeile.season is None:
        stand = bestand.serien_stand(titel, zeile.fassung_kennung)
        return [(name, stand.size_bytes)] if stand and stand.size_bytes else []

    for staffel in staffeln:
        if staffel.get("season") != zeile.season:
            continue
        je_fassung = next(
            (
                f
                for f in staffel.get("versions") or []
                if str(f.get("version_id")) == zeile.fassung_kennung
            ),
            None,
        )
        groesse = int((je_fassung or {}).get("size_bytes") or 0)
        if not groesse:
            return []
        if folgen is None:
            return [(f"{name} - Staffel {zeile.season}", groesse)]
        # ⚠️ **Ein Paket summiert die Folgen nicht.** Gemessen: Eine
        # Doppelfolge meldet ihre Dateigröße bei **jeder** ihrer Folgen -
        # addiert wäre sie doppelt gezählt. Die Staffelgröße ist die Zahl,
        # die stimmt; der Anteil des Pakets steht am Posten selbst.
        return [(f"{name} - Staffel {zeile.season}", zeile.size_bytes or groesse)]
    return []


async def loeschen(
    settings: AppSettings,
    zeile: StorageEntry,
    kennung: int,
    paket_folgen: Callable[[], list[int] | None],
) -> None:
    """Die Dateien eines Postens über nexcrates Papierkorb entfernen.

    Wirft ``NichtsZuLoeschen``, wenn nexcrate für diesen Umfang keine Datei
    meldet - dann ist der Posten zählbar, aber nicht löschbar, wie im
    ARR-Betrieb auch.
    """
    art = mapping.kind(zeile.media_type)
    ref = mapping.ref(kennung)
    koerper = {
        **_umfang(zeile, paket_folgen()),
        "versions": [zeile.fassung_kennung],
        "delete_files": True,
    }
    antwort = await _client(settings).withdraw(art, ref, koerper)
    entfernt = sum(
        int(eintrag.get("files_recycled") or 0) for eintrag in antwort.get("versions") or []
    )
    if not entfernt and not antwort.get("title_removed"):
        raise NichtsZuLoeschen(
            "nexcrate meldet für diesen Posten keine Datei, die sich löschen ließe.",
            409,
            code="storage_nothing_to_delete",
        )
    logger.info("nexcrate recycled %d file(s) for %s", entfernt, zeile.key)


async def stilllegen(settings: AppSettings, zeile: StorageEntry) -> int | None:
    """Überwachung aus, Datei bleibt (N43). Die Kennung, oder ``None``.

    ``None`` heißt: nexcrate führt den Titel nicht mehr - dann ist nichts zu
    tun, und das ist kein Fehler.
    """
    if not zeile.tmdb_id:
        return None
    art = mapping.kind(zeile.media_type)
    ref = mapping.ref(zeile.tmdb_id)
    if await _client(settings).title(art, ref) is None:
        return None
    await _client(settings).monitoring(
        art,
        ref,
        {"monitored": False, "versions": [zeile.fassung_kennung], **_umfang(zeile, None)},
    )
    return int(zeile.tmdb_id)
