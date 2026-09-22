"""Was sich an einem klemmenden Download tun lässt - nach nexcrates Wort.

⚠️ **Nexviews eigene Tabelle gilt hier nicht.** Bei Radarr und Sonarr rät
Nexview aus rund dreißig Textmustern, was los ist, und führt je Grund eine
Liste erlaubter Aktionen samt eigener Freigabe für die Automatik. nexcrate
nennt beides selbst, je Problem (N27), und das ist im NEX-Betrieb die
**einzige** Erlaubnis (Bauplan 6.6). Eine Aktion, die nicht in `actions`
steht, wird nicht angeboten und nicht gesendet.

⚠️ **`erneut_pruefen` hat kein genaues Gegenstück.** Bei Arr heißt es „sieh
noch einmal nach"; nexcrate fragt von selbst nach und bietet stattdessen
`retry` oder `search` an - je nachdem, was bei diesem Problem hilft. Welches
davon gesendet wird, sagt `actions`, nicht Nexview.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ....models import DownloadHaenger
from ..base import Aktion, DownloadFehler
from . import downloads as nex_downloads

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ....models import User
    from ...settings_service import AppSettings

logger = logging.getLogger("nexview.nexcrate")


def _zeile(db: Session, zeile_id: int) -> DownloadHaenger:
    zeile = db.get(DownloadHaenger, zeile_id)
    if zeile is None:
        raise DownloadFehler(
            "Diesen Download gibt es nicht mehr.", code="download_not_found", status_code=404
        )
    return zeile


def _name(zeile: DownloadHaenger, aktion: Aktion) -> str:
    name = nex_downloads.nexcrate_name(zeile, aktion)
    if name is None:
        raise DownloadFehler(
            "nexcrate bietet das an diesem Download nicht an.",
            code="download_action_not_offered",
            aktion=aktion.value,
        )
    return name


async def _ausfuehren(
    db: Session,
    settings: AppSettings,
    zeile: DownloadHaenger,
    aktion: Aktion,
    *,
    wer: User | None,
    automatisch: bool,
    koerper: dict[str, Any] | None = None,
) -> Any:
    from ..arr.download_haenger import verlauf
    from .fehler import NexcrateError
    from .weg import client_fuer

    name = _name(zeile, aktion)
    if automatisch and not nex_downloads.darf_automatik(zeile, aktion):
        raise DownloadFehler(
            "Diese Aktion darf eine Automatik hier nicht auslösen.",
            code="download_action_not_automatic",
            aktion=aktion.value,
        )
    try:
        antwort = await client_fuer(settings).download_action(
            int(zeile.download_id), name, koerper
        )
    except NexcrateError as fehler:
        raise DownloadFehler(
            fehler.message, code=fehler.code or "nexcrate_refused", status_code=502
        ) from fehler

    db.add(
        verlauf(
            zeile,
            aktion.value,
            automatisch=automatisch,
            user_id=wer.id if wer is not None else None,
            ergebnis=name,
        )
    )
    # ⚠️ Die Zeile bleibt stehen: Ob das Problem weg ist, sagt der nächste
    # Rundgang - nicht Nexviews Hoffnung. Bei Arr ist es dieselbe Regel.
    db.commit()
    logger.info(
        "nexcrate was asked to %s download %s%s",
        name,
        zeile.download_id,
        " (automatically)" if automatisch else "",
    )
    return antwort or {}


async def entfernen(
    db: Session,
    settings: AppSettings,
    zeile_id: int,
    *,
    neu_suchen: bool,
    wer: User | None,
    automatisch: bool = False,
) -> Any:
    """Aus der Warteschlange nehmen - mit neuer Suche oder ohne.

    ⚠️ **Die Suche stößt nexcrate an, nicht Nexview** (Bauplan 6.1): Es gibt
    genau eine Aktion `remove_and_search`, und nexcrate setzt den Wunsch
    selbst. Nexview startet nie etwas, was nexcrate nicht selbst einreiht.
    """
    from ..arr.download_aktionen import Ergebnis

    zeile = _zeile(db, zeile_id)
    aktion = Aktion.entfernen_neu_suchen if neu_suchen else Aktion.entfernen
    antwort = await _ausfuehren(
        db, settings, zeile, aktion, wer=wer, automatisch=automatisch
    )
    return Ergebnis(gesucht=neu_suchen, befehl=str(antwort.get("search") or "queued"))


async def erneut_pruefen(
    db: Session, settings: AppSettings, zeile_id: int, *, wer: User | None, automatisch: bool = False
) -> Any:
    from ..arr.download_aktionen import Ergebnis

    zeile = _zeile(db, zeile_id)
    antwort = await _ausfuehren(
        db, settings, zeile, Aktion.erneut_pruefen, wer=wer, automatisch=automatisch
    )
    return Ergebnis(befehl=str(antwort.get("search") or "queued"))


async def kandidaten(db: Session, settings: AppSettings, zeile_id: int) -> list[Any]:
    """Die Dateien eines klemmenden Downloads, in TMDB-Zählung (N42).

    ⚠️ **Die Kandidatenliste kommt aus nexcrates Entscheidung**, nicht aus
    Ablehnungen wie bei Arr: `reading` sagt, was es in der Datei gelesen hat,
    `decision` was daraus folgt. Ein Feld für „dauerhaft abgelehnt" gibt es
    nicht - es gibt nur, was zugeordnet ist und was nicht.
    """
    from ..arr.download_aktionen import Kandidat
    from .fehler import NexcrateError
    from .weg import client_fuer

    zeile = _zeile(db, zeile_id)
    try:
        antwort = await client_fuer(settings).download_files(int(zeile.download_id))
    except NexcrateError as fehler:
        raise DownloadFehler(
            fehler.message, code=fehler.code or "nexcrate_refused", status_code=502
        ) from fehler

    gefunden: list[Any] = []
    for datei in antwort.get("files") or []:
        serie = datei.get("series") or {}
        folgen = tuple(
            (int(paar.get("season")), int(paar.get("episode")))
            for paar in serie.get("episodes") or []
            if paar.get("season") is not None and paar.get("episode") is not None
        )
        gefunden.append(
            Kandidat(
                # nexcrate nennt keinen Pfad (6.4) - der Schlüssel ist die
                # Kennung, mit der auch zugeordnet wird.
                pfad=str(datei.get("key")),
                name=str(datei.get("name") or ""),
                groesse=int(datei.get("size_bytes") or 0),
                qualitaet=str((datei.get("reading") or {}).get("quality") or ""),
                sprachen=tuple(
                    str(s) for s in ((datei.get("reading") or {}).get("languages") or [])
                ),
                zuordnung=str(datei.get("decision") or ""),
                folgen=folgen,
                zuordenbar=bool(folgen) or str(datei.get("decision")) == "open",
                ablehnungen=(),
            )
        )
    return gefunden


async def importieren(
    db: Session,
    settings: AppSettings,
    zeile_id: int,
    pfade: list[str],
    *,
    trotzdem: bool,
    wer: User | None,
) -> Any:
    """Von Hand zuordnen - die gewählten Dateien, in nexcrates Zählung.

    ``trotzdem`` ist nexcrates `confirm: ["not_better"]`: dieselbe Frage wie
    bei Arr („ist schlechter als das Vorhandene - trotzdem?"), nur beim Namen
    genannt statt aus einer Ablehnung geraten.
    """
    from ..arr.download_aktionen import Ergebnis

    zeile = _zeile(db, zeile_id)
    if not pfade:
        raise DownloadFehler(
            "Es ist keine Datei ausgewählt.", code="download_import_nothing_selected"
        )
    koerper: dict[str, Any] = {"files": [{"key": pfad} for pfad in pfade]}
    if trotzdem:
        koerper["confirm"] = ["not_better"]
    await _ausfuehren(
        db,
        settings,
        zeile,
        Aktion.manuell_importieren,
        wer=wer,
        automatisch=False,
        koerper=koerper,
    )
    return Ergebnis(befehl="queued")
