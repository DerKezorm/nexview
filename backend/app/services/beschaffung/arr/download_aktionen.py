"""Was sich an einem haengenden Download tun laesst - von Hand und von der Automatik.

⚠️ **Vor jeder Aktion wird die Warteschlange neu gelesen.** Die Zeile in
``download_haenger`` kann Minuten alt sein; inzwischen hat jemand in Radarr
aufgeraeumt, oder der Import lief doch noch. Entfernt wird nur, was die
Instanz in diesem Moment noch unter derselben ``downloadId`` fuehrt - mit den
Zeilennummern, die sie jetzt nennt, nicht mit den gemerkten.

⚠️ **Neu gesucht wird von Nexview, nicht von Radarr.** Radarr und Sonarr
suchen nach dem Sperren nur von selbst, wenn beim Download-Programm
"Redownload failed" eingeschaltet ist (gelesen in
``RedownloadFailedDownloadService``, 12.09.2026). Nexview schickt deshalb
``skipRedownload`` und stoesst die Suche selbst an; sonst suchte es je nach
Einstellung keiner oder beide.

⚠️ **Der manuelle Import prueft nichts nach.** Der Befehl ``ManualImport``
baut seine Entscheidung ohne die Ablehnungen (``ManualImportService``). Was
mitgeschickt wird, landet in der Bibliothek, auch ein Sample. Deshalb
verlangt eine Datei mit dauerhafter Ablehnung ein ausdrueckliches
``trotzdem``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ....models import DownloadHaenger, User
from ...settings_service import AppSettings, ArrInstanz
from ..base import DownloadFehler
from . import download_haenger
from .client import ArrError
from .download_gruende import Aktion
from .radarr import RadarrClient
from .sonarr import SonarrClient

logger = logging.getLogger("nexview.downloads")

#: So lange wartet ein Import auf seine Antwort, bevor er "laeuft noch" meldet.
ABWARTEN_SEKUNDEN = 20.0
ABWARTEN_TAKT = 1.0

#: Ab so vielen Folgen sucht Nexview die Staffel statt jede Folge einzeln.
STAFFEL_AB_FOLGEN = 3


@dataclass(frozen=True)
class Ergebnis:
    #: Wurde neu gesucht?
    gesucht: bool = False
    #: Wie der Befehl der Instanz ausging: completed, started, queued, failed ...
    befehl: str = ""


@dataclass(frozen=True)
class Ablehnung:
    text: str
    dauerhaft: bool


@dataclass(frozen=True)
class Kandidat:
    """Eine Datei, die ein manueller Import nehmen koennte."""

    pfad: str
    name: str
    groesse: int
    qualitaet: str
    sprachen: tuple[str, ...]
    #: Film oder Serie, wie die Instanz die Datei zuordnet.
    zuordnung: str
    folgen: tuple[tuple[int, int], ...]
    #: Ohne Zuordnung kann der Import nichts damit anfangen.
    zuordenbar: bool
    ablehnungen: tuple[Ablehnung, ...]


def _client(instanz: ArrInstanz) -> RadarrClient | SonarrClient:
    if instanz.media_type == "movie":
        return RadarrClient(instanz.url, instanz.api_key)
    return SonarrClient(instanz.url, instanz.api_key)


def _zeile(db: Session, zeile_id: int) -> DownloadHaenger:
    zeile = db.get(DownloadHaenger, zeile_id)
    if zeile is None:
        raise DownloadFehler(
            "Diesen hängenden Download gibt es nicht mehr.",
            code="download_not_found",
            status_code=404,
        )
    return zeile


def _vermerken(
    db: Session,
    zeile: DownloadHaenger,
    aktion: Aktion,
    *,
    wer: User | None,
    automatisch: bool,
    ergebnis: str = "",
) -> None:
    db.add(
        download_haenger.verlauf(
            zeile,
            aktion.value,
            automatisch=automatisch,
            user_id=wer.id if wer is not None else None,
            ergebnis=ergebnis,
        )
    )


async def _frisch(
    db: Session, settings: AppSettings, zeile: DownloadHaenger
) -> tuple[ArrInstanz, RadarrClient | SonarrClient, download_haenger.Download]:
    """Den Download so, wie die Instanz ihn **jetzt** fuehrt.

    Ist er weg, ist auch die Zeile hinfaellig: Sie wird entfernt, und die
    Aktion endet mit ``download_gone`` statt mit einem Aufruf auf alte Nummern.
    """
    instanz = next((i for i in settings.arr_instanzen() if i.kennung == zeile.kennung), None)
    if instanz is not None:
        client = _client(instanz)
        saetze = await client.warteschlange_voll(instanz.media_type == "movie")
        for download in download_haenger.zusammenfassen(instanz, saetze):
            if download.download_id == zeile.download_id:
                return instanz, client, download

    titel = zeile.titel or zeile.release
    db.delete(zeile)
    # ⚠️ Wegschreiben vor dem Markieren: Die Sitzung tut es nicht von selbst
    # (``autoflush`` aus), und das Markieren saehe die Zeile sonst noch.
    db.flush()
    download_haenger.anfragen_markieren(db, settings)
    db.commit()
    download_haenger.vergessen()
    raise DownloadFehler(
        "Der Download steht nicht mehr in der Warteschlange. Vielleicht hat ihn "
        "jemand in Radarr oder Sonarr schon erledigt.",
        code="download_gone",
        titel=titel,
    )


async def _neu_suchen(
    client: RadarrClient | SonarrClient, instanz: ArrInstanz, download: download_haenger.Download
) -> bool:
    """Die Suche anstossen. ``False``, wenn es nichts zu suchen gibt oder die Instanz ablehnt.

    Bei Serien die Staffel, sobald es mehrere Folgen sind: Zwanzig einzelne
    Folgensuchen fragen jeden Indexer zwanzigmal.
    """
    try:
        if instanz.media_type == "movie":
            if download.arr_id is None:
                return False
            await client.befehl("MoviesSearch", movieIds=[download.arr_id])
            return True
        if not download.folgen_ids:
            return False
        staffeln = sorted({staffel for staffel, _nummer in download.folgen})
        if download.arr_id is not None and len(download.folgen_ids) >= STAFFEL_AB_FOLGEN:
            for staffel in staffeln:
                await client.befehl("SeasonSearch", seriesId=download.arr_id, seasonNumber=staffel)
            return True
        await client.befehl("EpisodeSearch", episodeIds=download.folgen_ids)
        return True
    except ArrError as fehler:
        logger.warning("Search after removing a download in %s failed: %s", instanz.name, fehler.code)
        return False


def moegliche_aktionen(zeile: DownloadHaenger) -> list[Aktion]:
    """Was technisch geht - unabhaengig davon, was der Grund empfiehlt."""
    moeglich: list[Aktion] = []
    if not zeile.download_id.startswith("zeile-"):
        moeglich.append(Aktion.manuell_importieren)
    if zeile.zeilen:
        kann_suchen = (
            zeile.arr_id is not None if zeile.media_type == "movie" else bool(zeile.folgen_ids)
        )
        if kann_suchen:
            moeglich.append(Aktion.entfernen_neu_suchen)
        moeglich.append(Aktion.entfernen)
    moeglich.append(Aktion.erneut_pruefen)
    return moeglich


async def entfernen(
    db: Session,
    settings: AppSettings,
    zeile_id: int,
    *,
    neu_suchen: bool,
    wer: User | None,
    automatisch: bool = False,
) -> Ergebnis:
    """Aus der Warteschlange und dem Download-Programm nehmen - mit Sperre und Suche oder ohne.

    "Nur entfernen" sperrt das Release nicht: Wer einen Download entfernt, der
    kein Upgrade ist, will nicht, dass Radarr dasselbe Release nie wieder nimmt.
    """
    zeile = _zeile(db, zeile_id)
    aktion = Aktion.entfernen_neu_suchen if neu_suchen else Aktion.entfernen
    instanz, client, download = await _frisch(db, settings, zeile)
    if not download.zeilen:
        raise DownloadFehler(
            "Radarr bzw. Sonarr nennt für diesen Download keine Zeile, die sich entfernen ließe.",
            code="download_not_removable",
        )

    try:
        await client.warteschlange_entfernen(download.zeilen, sperren=neu_suchen)
    except ArrError as fehler:
        _vermerken(
            db, zeile, aktion, wer=wer, automatisch=automatisch,
            ergebnis=fehler.code or "arr_http_error",
        )
        db.commit()
        raise

    gesucht = await _neu_suchen(client, instanz, download) if neu_suchen else False
    _vermerken(
        db, zeile, aktion, wer=wer, automatisch=automatisch,
        ergebnis="search_failed" if neu_suchen and not gesucht else "",
    )
    logger.info(
        "%s removed %r from %s (blocklist=%s, searched=%s)",
        "Automation" if automatisch else (wer.username if wer else "?"),
        zeile.titel or zeile.release,
        instanz.name,
        neu_suchen,
        gesucht,
    )
    db.delete(zeile)
    db.flush()  # siehe ``_frisch``
    download_haenger.anfragen_markieren(db, settings)
    db.commit()
    download_haenger.vergessen()
    return Ergebnis(gesucht=gesucht)


def _status(antwort: Any) -> str:
    return str(antwort.get("status") or "").lower() if isinstance(antwort, dict) else ""


async def _abwarten(client: RadarrClient | SonarrClient, antwort: Any) -> str:
    """Den Befehl kurz begleiten - bis er fertig ist oder die Frist um."""
    status = _status(antwort)
    nummer = antwort.get("id") if isinstance(antwort, dict) else None
    if not isinstance(nummer, int):
        return status
    frist = time.monotonic() + ABWARTEN_SEKUNDEN
    while status in ("queued", "started") and time.monotonic() < frist:
        await asyncio.sleep(ABWARTEN_TAKT)
        try:
            status = _status(await client.befehl_stand(nummer))
        except ArrError:
            break
    return status


async def erneut_pruefen(
    db: Session,
    settings: AppSettings,
    zeile_id: int,
    *,
    wer: User | None,
    automatisch: bool = False,
) -> Ergebnis:
    """Radarr bzw. Sonarr die fertigen Downloads noch einmal ansehen lassen.

    ``RefreshMonitoredDownloads`` prueft, was fertig oder blockiert ist, und
    stoesst danach die Verarbeitung an. Das hilft, wenn die Ursache inzwischen
    behoben ist - ein Archiv entpackt, eine Pfadzuordnung eingetragen.
    """
    zeile = _zeile(db, zeile_id)
    _instanz, client, _download = await _frisch(db, settings, zeile)
    try:
        antwort = await client.befehl("RefreshMonitoredDownloads")
    except ArrError as fehler:
        _vermerken(
            db, zeile, Aktion.erneut_pruefen, wer=wer, automatisch=automatisch,
            ergebnis=fehler.code or "arr_http_error",
        )
        db.commit()
        raise
    _vermerken(db, zeile, Aktion.erneut_pruefen, wer=wer, automatisch=automatisch)
    db.commit()
    download_haenger.vergessen()
    return Ergebnis(befehl=_status(antwort))


def _kandidat(instanz: ArrInstanz, eintrag: dict) -> Kandidat:
    pfad = str(eintrag.get("path") or "")
    name = str(eintrag.get("relativePath") or eintrag.get("name") or pfad.rsplit("/", 1)[-1])
    qualitaet = ((eintrag.get("quality") or {}).get("quality") or {}).get("name") or ""
    sprachen = tuple(
        str(sprache.get("name"))
        for sprache in eintrag.get("languages") or []
        if isinstance(sprache, dict) and sprache.get("name")
    )
    ablehnungen = tuple(
        Ablehnung(
            text=str(ablehnung.get("reason") or ""),
            dauerhaft=str(ablehnung.get("type") or "").lower() == "permanent",
        )
        for ablehnung in eintrag.get("rejections") or []
        if isinstance(ablehnung, dict) and ablehnung.get("reason")
    )
    folgen: tuple[tuple[int, int], ...] = ()
    if instanz.media_type == "movie":
        film = eintrag.get("movie") if isinstance(eintrag.get("movie"), dict) else {}
        titel = str(film.get("title") or "")
        jahr = film.get("year")
        zuordnung = f"{titel} ({jahr})" if titel and isinstance(jahr, int) and jahr > 0 else titel
        zuordenbar = isinstance(film.get("id"), int) and film["id"] > 0
    else:
        serie = eintrag.get("series") if isinstance(eintrag.get("series"), dict) else {}
        zuordnung = str(serie.get("title") or "")
        folgen = tuple(
            sorted(
                (folge["seasonNumber"], folge["episodeNumber"])
                for folge in eintrag.get("episodes") or []
                if isinstance(folge, dict)
                and isinstance(folge.get("seasonNumber"), int)
                and isinstance(folge.get("episodeNumber"), int)
            )
        )
        zuordenbar = (
            isinstance(serie.get("id"), int) and serie["id"] > 0 and bool(eintrag.get("episodes"))
        )
    return Kandidat(
        pfad=pfad,
        name=name,
        groesse=int(eintrag.get("size") or 0),
        qualitaet=str(qualitaet),
        sprachen=sprachen,
        zuordnung=zuordnung,
        folgen=folgen,
        zuordenbar=zuordenbar,
        ablehnungen=ablehnungen,
    )


def _braucht_download_id(zeile: DownloadHaenger) -> None:
    if zeile.download_id.startswith("zeile-"):
        raise DownloadFehler(
            "Dieser Download hat noch keine Kennung beim Download-Programm. Ein "
            "manueller Import ist erst möglich, wenn er dort angekommen ist.",
            code="download_import_not_possible",
        )


async def import_kandidaten(
    db: Session, settings: AppSettings, zeile_id: int
) -> list[Kandidat]:
    """Die Dateien des Downloads, wie ein manueller Import sie saehe."""
    zeile = _zeile(db, zeile_id)
    _braucht_download_id(zeile)
    instanz, client, download = await _frisch(db, settings, zeile)
    return [_kandidat(instanz, e) for e in await client.import_kandidaten(download.download_id)]


def _importdatei(instanz: ArrInstanz, eintrag: dict, download_id: str) -> dict[str, Any]:
    """Eine Datei fuer den Befehl ``ManualImport``, aus dem, was die Instanz selbst vorschlaegt.

    ⚠️ **Nichts davon kommt aus dem Browser.** Die Oberflaeche nennt nur Pfade;
    Zuordnung, Qualitaet und Sprachen stammen aus der frischen Antwort von
    ``/manualimport``. Wer die Zuordnung aendern will, tut das in Radarr.
    """
    datei: dict[str, Any] = {
        "path": eintrag.get("path"),
        "folderName": eintrag.get("folderName") or "",
        "quality": eintrag.get("quality"),
        "languages": eintrag.get("languages") or [],
        "releaseGroup": eintrag.get("releaseGroup") or "",
        "indexerFlags": eintrag.get("indexerFlags") or 0,
        "downloadId": download_id,
    }
    if instanz.media_type == "movie":
        datei["movieId"] = (eintrag.get("movie") or {}).get("id")
    else:
        datei["seriesId"] = (eintrag.get("series") or {}).get("id")
        datei["episodeIds"] = [
            folge.get("id")
            for folge in eintrag.get("episodes") or []
            if isinstance(folge, dict) and isinstance(folge.get("id"), int)
        ]
        datei["releaseType"] = eintrag.get("releaseType") or "unknown"
    return datei


async def importieren(
    db: Session,
    settings: AppSettings,
    zeile_id: int,
    pfade: list[str],
    *,
    trotzdem: bool,
    wer: User | None,
) -> Ergebnis:
    """Die gewaehlten Dateien importieren - nur von Hand, nie von der Automatik."""
    zeile = _zeile(db, zeile_id)
    _braucht_download_id(zeile)
    if not pfade:
        raise DownloadFehler(
            "Es ist keine Datei ausgewählt.", code="download_import_nothing_selected"
        )
    instanz, client, download = await _frisch(db, settings, zeile)
    nach_pfad = {
        str(eintrag.get("path")): eintrag
        for eintrag in await client.import_kandidaten(download.download_id)
        if eintrag.get("path")
    }

    gewaehlt: list[dict] = []
    for pfad in dict.fromkeys(pfade):
        eintrag = nach_pfad.get(pfad)
        if eintrag is None:
            raise DownloadFehler(
                "Diese Datei gehört nicht (mehr) zu dem Download.",
                code="download_import_unknown_file",
                datei=pfad.rsplit("/", 1)[-1],
            )
        kandidat = _kandidat(instanz, eintrag)
        if not kandidat.zuordenbar:
            raise DownloadFehler(
                "Radarr bzw. Sonarr kann diese Datei keinem Titel zuordnen. Die "
                "Zuordnung lässt sich dort im manuellen Import festlegen.",
                code="download_import_unmapped",
                datei=kandidat.name,
            )
        if not trotzdem and any(ablehnung.dauerhaft for ablehnung in kandidat.ablehnungen):
            raise DownloadFehler(
                "Radarr bzw. Sonarr hat Einwände gegen diese Datei. Importiert wird "
                "sie nur, wenn du das ausdrücklich bestätigst.",
                code="download_import_needs_confirmation",
                datei=kandidat.name,
            )
        gewaehlt.append(eintrag)

    try:
        antwort = await client.befehl(
            "ManualImport",
            importMode="auto",
            files=[_importdatei(instanz, eintrag, download.download_id) for eintrag in gewaehlt],
        )
        status = await _abwarten(client, antwort)
    except ArrError as fehler:
        _vermerken(
            db, zeile, Aktion.manuell_importieren, wer=wer, automatisch=False,
            ergebnis=fehler.code or "arr_http_error",
        )
        db.commit()
        raise

    _vermerken(
        db, zeile, Aktion.manuell_importieren, wer=wer, automatisch=False,
        ergebnis="command_failed" if status == "failed" else "",
    )
    logger.info(
        "%s imported %d file(s) of %r in %s manually (anyway=%s): %s",
        wer.username if wer else "?",
        len(gewaehlt),
        zeile.titel or zeile.release,
        instanz.name,
        trotzdem,
        status or "no status",
    )
    db.commit()
    download_haenger.vergessen()
    return Ergebnis(befehl=status)
