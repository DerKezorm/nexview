"""Was an den Instanzen gemessen wird - damit die Befunde nur noch lesen.

⚠️ **Die Trennung ist der ganze Sinn dieser Datei.** Eine Pruefung in
``services/befunde.py`` darf nicht ins Netz greifen: Sonst kostet jeder Aufruf
des Dashboards zwanzig Radarr-Anfragen, bei jedem Neuladen und fuer jeden
Administrator. Hier wird einmal je Rundgang gemessen und abgelegt; dort wird
gelesen.

Zwei Takte, aus einem messbaren Grund:

* **Jede Runde** nur ``/system/status`` - eine winzige Antwort, und
  "erreichbar ja/nein" ist die Frage, die schnell veralten wuerde.
* **Stuendlich** der Rest: Datentraeger, Warteschlange, Aktualisierung. Ein
  Plattenfuellstand aendert sich nicht im Zwei-Minuten-Takt, und die
  Warteschlange holt der Rundgang ohnehin schon fuer die Anfragen ab - sie
  hier ein zweites Mal alle zwei Minuten zu ziehen waere reine Verdopplung.

⚠️ **Erreichbarkeit hat einen eigenen Zeitpunkt.** ``gemessen_am`` sagt "wann
zuletzt nachgesehen", ``erreichbar_seit`` "seit wann in diesem Zustand". Nur
das zweite macht aus "Radarr antwortet nicht" die Aussage "seit zwei Stunden
nicht" - und das ist der Unterschied zwischen einem Neustart und einem
Ausfall. Deshalb wird es **nur beim Wechsel** gesetzt.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import InstanzStand
from . import library, storage
from .arr import ArrClient, ArrError
from .settings_service import AppSettings, ArrInstanz

logger = logging.getLogger("nexview.instanzstand")

#: Kurz, wie auf der Diensteseite: Die Frage ist "antwortet sie ueberhaupt",
#: nicht "wie schnell". Ein langer Timeout wuerde den ganzen Rundgang
#: aufhalten, sobald eine Instanz haengt.
ANTWORTFRIST = httpx.Timeout(4.0, connect=3.0)


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def eintrag(db: Session, kennung: str) -> InstanzStand | None:
    return db.scalar(select(InstanzStand).where(InstanzStand.kennung == kennung))


def alle(db: Session) -> dict[str, InstanzStand]:
    """Der gemerkte Stand aller Instanzen, nach Kennung."""
    return {zeile.kennung: zeile for zeile in db.scalars(select(InstanzStand))}


async def messen(db: Session, settings: AppSettings, *, voll: bool = False) -> None:
    """Alle Instanzen einmal befragen.

    ⚠️ **Fehler je Instanz, nie im Ganzen** - dasselbe Muster wie in
    ``instanz_gesundheit.pruefen``. Eine Instanz, die haengt, darf die
    Messwerte der anderen nicht mitnehmen; sonst ist ausgerechnet bei einem
    Ausfall das ganze Dashboard leer.
    """
    traeger = await _traeger_messen(settings) if voll else None

    for instanz in settings.arr_instanzen():
        try:
            await _instanz_messen(
                db, settings, instanz, voll=voll, traeger=traeger
            )
        except Exception:  # noqa: BLE001 - siehe Docstring
            logger.exception("Measuring %s failed", instanz.name)
            db.rollback()


async def _traeger_messen(settings: AppSettings) -> list[dict] | None:
    """Die Datentraeger hinter den Zielordnern - haus-weit, nicht je Instanz.

    ⚠️ **Bewusst einmal fuer alle.** Filme und Serien liegen fast immer auf
    derselben Platte; je Instanz zu messen hiesse, dieselbe Platte drei- oder
    viermal zu fuehren - und dann stuende sie auch drei- oder viermal als
    Befund da. ``storage.traeger`` entdoppelt ueber die Gesamtgroesse.
    """
    try:
        gefunden = await storage.traeger(settings)
    except ArrError:
        return None
    return [
        {
            "gesamt": t.gesamt,
            "frei": t.frei,
            "ordner": list(t.ordner),
            "belegt_anteil": round(t.belegt_anteil, 4),
        }
        for t in gefunden
    ]


async def _luecken_messen(
    settings: AppSettings, instanz: ArrInstanz
) -> dict | None:
    """Was ueberwacht wird, aber (noch) nicht daliegt.

    ⚠️ **Kostet keinen zusaetzlichen Aufruf.** Radarr und Sonarr liefern
    ``monitored`` und ``has_file`` bei jedem Titel gratis mit, und der Rundgang
    holt die Bibliothek ohnehin - ``library`` haelt sie 60 Sekunden im
    Speicher. Es wird also nur ausgewertet, was schon da ist.

    Bei Serien zaehlen **Folgen**, nicht Serien: Eine Serie, der drei von
    sechzig Folgen fehlen, ist etwas anderes als eine, die ganz fehlt - und
    "eine Serie unvollstaendig" waere in beiden Faellen dieselbe Aussage.
    """
    try:
        if instanz.media_type == "movie":
            bestand = await library.movie_library(settings, instanz.tier)
            fehlend = sum(
                1 for e in bestand.values() if e.monitored and not e.has_file
            )
            return {"fehlend": fehlend, "einheit": "titel"}
        bestand, _ = await library.series_library(settings, instanz.tier)
        fehlend = sum(
            max(0, e.episode_count - e.episode_file_count)
            for e in bestand.values()
            if e.monitored
        )
        return {"fehlend": fehlend, "einheit": "folgen"}
    except ArrError:
        return None


async def _instanz_messen(
    db: Session,
    settings: AppSettings,
    instanz: ArrInstanz,
    *,
    voll: bool,
    traeger: list[dict] | None,
) -> None:
    client = ArrClient(instanz.url, instanz.api_key, instanz.name)

    erreichbar = True
    version = ""
    try:
        antwort = await client.system_status(timeout=ANTWORTFRIST)
        version = str((antwort or {}).get("version") or "")
    except ArrError:
        erreichbar = False

    jetzt = _jetzt()
    zeile = eintrag(db, instanz.kennung)
    if zeile is None:
        zeile = InstanzStand(kennung=instanz.kennung, erreichbar_seit=jetzt)
        db.add(zeile)
    elif zeile.erreichbar != erreichbar:
        # Nur beim Wechsel. Wuerde der Zeitpunkt jede Runde mitwandern, hiesse
        # "nicht erreichbar seit" immer "seit gerade eben".
        zeile.erreichbar_seit = jetzt

    zeile.erreichbar = erreichbar
    if version:
        # Eine stumme Instanz behaelt ihre zuletzt bekannte Fassung. "" waere
        # die Behauptung, sie haette keine.
        zeile.version = version
    zeile.gemessen_am = jetzt

    if voll:
        messwerte = dict(zeile.messwerte or {})
        if traeger is not None:
            messwerte["traeger"] = traeger
        if erreichbar:
            messwerte.update(await _volle_messung(client))
            luecken = await _luecken_messen(settings, instanz)
            if luecken is not None:
                messwerte["luecken"] = luecken
        # Ein neues Wörterbuch zuweisen, nicht in das alte hineinschreiben:
        # SQLAlchemy bemerkt eine Änderung *im* JSON-Wert nicht und würde
        # nichts speichern.
        zeile.messwerte = messwerte

    db.commit()


async def _volle_messung(client: ArrClient) -> dict:
    """Was stuendlich gemessen wird. Jeder Teil einzeln abgesichert."""
    ergebnis: dict = {}

    try:
        neuer = await client.aktualisierung()
        # ``None`` heisst "aktuell **oder** unbekannt" - beides fuehrt zu
        # keinem Befund, und der Unterschied waere ohnehin nicht anzeigbar.
        ergebnis["aktualisierung"] = neuer
    except ArrError:
        pass

    try:
        ergebnis["warteschlange"] = await client.warteschlangen_zustand()
    except ArrError:
        pass

    return ergebnis
