"""Was Nexview nexcrate auftragen lässt: anfragen, zurücknehmen, einfrieren, löschen.

Vier Dinge sind hier anders als bei Radarr und Sonarr, und jedes davon ist
gemessen oder steht im Vertrag:

1. **Eine Anfrage ist idempotent** (N17). Ein zweiter Aufruf antwortet
   `unchanged`; die ganze Warterei, die nexbeat für Lidarr brauchte
   („zehn Minuten schauen, ob der Künstler angekommen ist"), entfällt. Eine
   Übergabe mit offenem Ausgang wird einfach noch einmal gesendet.
2. **Die Herkunftsmarke bleibt beim Ersten** (nexbeat-Befund 4). Fragt ein
   zweiter Benutzer denselben Titel an, steht dort weiter die Nummer der
   ersten Anfrage. ⚠️ **Der Stand kommt nie aus `origin`**, immer aus
   `lookup`.
3. **Nie dieselbe Anfrage parallel** (nexbeat-Befund 17): Zwei gleichzeitige
   Anfragen auf denselben unbekannten Titel endeten in nexcrate in einem
   `500` aus einem Wettlauf. Eine Sperre je `kind + ref` hält sie
   auseinander, und `500` heißt „noch einmal", nicht „gescheitert".
4. **Gelöscht wird über `withdraw` mit `delete_files`**, nie über
   `delete-files` allein (Bauplan 6.4): Das ließe die Überwachung an, und
   nexcrate lüde die Datei gleich wieder.

⚠️ **Was der Betreiber selbst überwacht, fasst Nexview nicht an** (6.2).
Trägt die Fassung am Titel keine `nexview:`-Marke, gibt es kein `withdraw`
und kein `monitoring` für sie - der Benutzer sieht „vom Betreiber in nexcrate
überwacht, bleibt".
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ....models import MediaRequest, MediaType, RequestStatus
from ..base import BeschaffungError, Korb
from . import mapping

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")

#: Anfragen, die noch etwas von einem Titel wollen. Wie im ARR-Betrieb zählt
#: ``pending_approval`` mit: Einer wartenden Anfrage soll ein fremder Abbruch
#: nicht den Titel unter den Füßen wegziehen.
AKTIV = (
    RequestStatus.pending_approval,
    RequestStatus.approved,
    RequestStatus.searching,
    RequestStatus.downloaded,
    RequestStatus.deferred,
)

#: Eine Sperre je ``kind + ref``, damit zwei Freigaben desselben Titels nicht
#: gleichzeitig bei nexcrate ankommen (nexbeat-Befund 17).
#:
#: ⚠️ **Je Event-Loop, nicht global.** Ein ``asyncio.Lock`` bindet sich beim
#: ersten Gebrauch an den gerade laufenden Loop; ein zweiter Aufruf unter
#: einem anderen Loop wirft dann "is bound to a different event loop". Der
#: Server hat nur einen Loop fuer sein ganzes Leben, aber die Testreihe
#: startet je Testfunktion einen neuen - ein reiner ``dict[(kind, ref)]``
#: liess die Sperren dabei stehen und die Reihe flackern. Der
#: ``WeakKeyDictionary`` haengt die innere Tabelle an den Loop selbst; stirbt
#: der Loop, verschwindet auch seine Tabelle.
_sperren: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, str], asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _sperre(kind: str, ref: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    je_loop = _sperren.get(loop)
    if je_loop is None:
        je_loop = {}
        _sperren[loop] = je_loop
    return je_loop.setdefault((kind, ref), asyncio.Lock())


def umfang(request: MediaRequest) -> dict[str, Any]:
    """Der Umfang einer Anfrage in nexcrates Worten (Form 4 des Vertrags).

    Ein Film hat keinen; eine Serie entweder Folgen, eine Staffel oder alles.
    ⚠️ Der Schlüssel heißt nach der Medienart (`series: {...}`), nie oben -
    so trägt dieselbe Form später auch Musik.

    ⚠️ **``seasons`` und ``future_seasons`` stehen immer ausdrücklich da.**
    Fehlt ``seasons``, schaltet nexcrate jede Staffel ein, auch wenn
    ``episodes`` dabei ist (``_checked_seasons``); fehlt ``future_seasons``,
    gilt ``true``. Nexview schickte bei einem Folgen-Paket nur ``episodes``,
    und aus „Staffel 1, Folgen 1 bis 5“ wurden acht Staffeln (Rundgang 2,
    R2-6, gemessen 25.09.2026). Ein Paket schickt deshalb ``seasons: []``: keine
    ganze Staffel, nur die genannten Folgen (von nexcrate zugesagt). Eine
    Staffel mit dem Haken „künftige Staffeln“ schickt ``true``: nexcrate
    schaltet dann nur diese Staffel ein und holt spätere von selbst.
    """
    if request.media_type != MediaType.tv:
        return {}
    if request.episodes:
        return {
            "series": {
                "episodes": [
                    {"season": request.season, "episode": nummer}
                    for nummer in sorted(request.episodes)
                ],
                "seasons": [],
                "future_seasons": False,
            }
        }
    if request.season is not None:
        return {
            "series": {
                "seasons": [request.season],
                "future_seasons": bool(request.monitor_future),
            }
        }
    # Die ganze Serie; künftige Staffeln nur mit dem Haken, wie im ARR-Betrieb
    # (``arr/sonarr.py``). Ohne ihn will nexcrate alle vorhandenen Staffeln
    # und keine neue.
    return {"series": {"seasons": "all", "future_seasons": bool(request.monitor_future)}}


def _client(settings: AppSettings):
    from .weg import client_fuer

    return client_fuer(settings)


async def anfragen(db: Session, settings: AppSettings, request: MediaRequest) -> int | None:
    """Eine freigegebene Anfrage nexcrate auftragen. Die Kennung dort.

    Im NEX-Betrieb ist das die TMDB-Nummer selbst: Jede Adresse von nexcrate
    nimmt ``tmdb:<n>``.
    """
    kind = mapping.kind(request.media_type)
    ref = mapping.ref(request.tmdb_id)
    koerper: dict[str, Any] = {
        "kind": kind,
        "ref": ref,
        "versions": [request.fassung_kennung],
        # Genau einmal suchen lassen, bei der Freigabe (Bauplan 6.1). nexcrate
        # reiht selbst ein; Nexview stößt nie eine zweite Suche an.
        "search_now": True,
        "origin": mapping.herkunft(request.id),
        **umfang(request),
    }
    if settings.nexcrate_anzeigename and request.user is not None:
        # N19: Der Name des Anfragenden geht nur mit ausdrücklichem Schalter
        # hinaus - ab Werk erfährt nexcrate von Nexviews Benutzern nichts.
        koerper["origin_label"] = request.user.username

    async with _sperre(kind, ref):
        antwort = await _client(settings).request(koerper)

    ausgang = [
        eintrag.get("outcome")
        for eintrag in antwort.get("versions") or []
        if eintrag.get("version_id") == request.fassung_kennung
    ]
    hinweise = [str(h.get("code")) for h in antwort.get("notes") or [] if h.get("code")]
    logger.info(
        "nexcrate took %r (%s, %s): outcome=%s search=%s%s",
        request.title,
        kind,
        request.fassung_kennung,
        ausgang[0] if ausgang else "unknown",
        antwort.get("search"),
        f" notes={','.join(hinweise)}" if hinweise else "",
    )
    return request.tmdb_id


def _weitere_aktive(db: Session, request: MediaRequest) -> list[MediaRequest]:
    """Welche anderen laufenden Anfragen wollen noch etwas von diesem Titel?

    Zeilen-, nicht nutzerbasiert - dieselbe Regel wie im ARR-Betrieb.
    """
    return list(
        db.scalars(
            select(MediaRequest).where(
                MediaRequest.media_type == request.media_type,
                MediaRequest.tmdb_id == request.tmdb_id,
                MediaRequest.fassung_kennung == request.fassung_kennung,
                MediaRequest.status.in_(AKTIV),
                MediaRequest.id != request.id,
            )
        )
    )


def freier_umfang(db: Session, request: MediaRequest) -> dict[str, Any] | None:
    """Was von dieser Anfrage **niemand sonst** will (Bauplan 6.2).

    ``None`` heißt: nichts ist frei - dann endet die Anfrage nur lokal, und
    nexcrate bleibt unberührt. Das ist der Unterschied zu Sonarr, wo Nexview
    die Staffeln selbst gegeneinander rechnen musste.
    """
    andere = _weitere_aktive(db, request)
    if not andere:
        return umfang(request)
    if request.media_type != MediaType.tv:
        # Einen Film will entweder jemand oder niemand.
        return None
    if any(anfrage.season is None for anfrage in andere):
        # Jemand will die ganze Serie - dann ist nichts frei.
        return None
    if request.season is None:
        # Die ganze Serie zurücknehmen, während andere einzelne Staffeln
        # wollen: Nur was niemand will, wird frei - und das kann Nexview hier
        # nicht abzählen, ohne alle Staffeln zu kennen. Lieber nichts.
        return None
    fremde_staffeln = {anfrage.season for anfrage in andere}
    if request.season in fremde_staffeln:
        if not request.episodes:
            return None
        fremde_folgen = {
            nummer
            for anfrage in andere
            if anfrage.season == request.season
            for nummer in (anfrage.episodes or [])
        }
        eigene = sorted(set(request.episodes) - fremde_folgen)
        if not eigene:
            return None
        return {
            "series": {
                "episodes": [
                    {"season": request.season, "episode": nummer} for nummer in eigene
                ]
            }
        }
    return umfang(request)


async def _gehoert_uns(settings: AppSettings, request: MediaRequest) -> bool | None:
    """Hat Nexview diese Fassung angelegt - oder der Betreiber selbst? (6.2)

    ``None`` heisst: Die angefragte Fassung gibt es bei nexcrate gar nicht
    (mehr) - weder der Titel noch, falls er steht, ein Eintrag mit dieser
    ``version_id``. Das ist ein anderer Fall als "der Betreiber ueberwacht sie
    selbst" (``False``): Dort steht etwas, das Nexview nicht anfassen soll;
    hier steht nichts, das sich zuruecknehmen liesse.
    """
    titel = await _client(settings).title(
        mapping.kind(request.media_type), mapping.ref(request.tmdb_id)
    )
    if titel is None:
        return None
    for eintrag in titel.get("versions") or []:
        if str(eintrag.get("version_id")) == request.fassung_kennung:
            return mapping.ist_nexview(eintrag.get("origin"))
    return None


async def zuruecknehmen(
    db: Session,
    settings: AppSettings,
    request: MediaRequest,
    *,
    dateien_loeschen: bool,
) -> str:
    """Eine Anfrage bei nexcrate zurücknehmen. Was geschah, fürs Protokoll."""
    teil = freier_umfang(db, request)
    if teil is None:
        return "left it in place - another request still covers it"
    gehoert = await _gehoert_uns(settings, request)
    if gehoert is None:
        return "left it in place - version unknown to nexcrate, nothing to withdraw"
    if not gehoert:
        return "left it in place - the owner watches this version in nexcrate"

    koerper = {**teil, "versions": [request.fassung_kennung], "delete_files": dateien_loeschen}
    kind = mapping.kind(request.media_type)
    ref = mapping.ref(request.tmdb_id)
    async with _sperre(kind, ref):
        antwort = await _client(settings).withdraw(kind, ref, koerper)

    dateien = sum(
        int(eintrag.get("files_recycled") or 0) for eintrag in antwort.get("versions") or []
    )
    abgebrochen = sum(
        int(eintrag.get("downloads_cancelled") or 0)
        for eintrag in antwort.get("versions") or []
    )
    if antwort.get("title_removed"):
        return "removed the title from nexcrate"
    teile = []
    if dateien:
        teile.append(f"{dateien} file(s) into the recycle bin")
    if abgebrochen:
        teile.append(f"{abgebrochen} download(s) cancelled")
    return "stopped watching it" + (f", {' and '.join(teile)}" if teile else "")


async def einfrieren(
    settings: AppSettings, request: MediaRequest, *, monitored: bool = False
) -> None:
    """Überwachung aus, Datei bleibt (N43). Der dritte Weg neben Behalten und Weg."""
    kind = mapping.kind(request.media_type)
    ref = mapping.ref(request.tmdb_id)
    koerper = {
        "monitored": monitored,
        "versions": [request.fassung_kennung],
        **umfang(request),
    }
    async with _sperre(kind, ref):
        await _client(settings).monitoring(kind, ref, koerper)


def nicht_eingerichtet_text(media_type: str, stufe: str) -> str:
    return "In nexcrate ist für diese Medienart keine Fassung eingerichtet."


def keine_fassung(kennung: str) -> BeschaffungError:
    return BeschaffungError(
        "Diese Fassung gibt es in nexcrate nicht mehr.",
        409,
        code="nexcrate_version_unknown",
        korb=Korb.abgelehnt,
        fassung=kennung,
    )
