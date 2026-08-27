"""Abgleich der TMDB-Titel mit dem, was schon in Radarr/Sonarr liegt.

Die Bibliothek wird kurz im Arbeitsspeicher gehalten (nicht in der Datenbank):
sie aendert sich staendig, ist schnell neu geladen und muss einen Neustart
nicht ueberleben.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from ..schemas_media import MediaItem
from .arr import ArrError
from .radarr import LibraryEntry as MovieEntry
from .radarr import RadarrClient
from .settings_service import AppSettings
from .sonarr import LibraryEntry as SeriesEntry
from .sonarr import SonarrClient, jahre_passen, normalize_title

logger = logging.getLogger("nexview.library")

LIBRARY_TTL_SECONDS = 60
OPTIONS_TTL_SECONDS = 300

_cache: dict[str, tuple[float, Any]] = {}


def _read(key: str, ttl: int) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at > ttl:
        _cache.pop(key, None)
        return None
    return value


def _write(key: str, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)


# Wie lange eine Instanz, die gerade nicht antwortet, in Ruhe gelassen wird.
# Kuerzer als die Haltezeit der Bibliothek: Kommt sie zurueck, soll es schnell
# wieder gehen.
FEHLER_SPERRE_SEKUNDEN = 30


def invalidate() -> None:
    """Nach dem Hinzufuegen eines Titels oder geaenderten Einstellungen."""
    _cache.clear()


async def _mit_fehlersperre(schluessel: str, holen):
    """Eine Instanz, die eben nicht antwortete, kurz nicht erneut fragen.

    Ohne das kostet eine haengende Instanz **jeden** Seitenaufruf das volle
    Zeitlimit von 15 Sekunden: Bei einem Fehlschlag wird nichts
    zwischengespeichert, also faengt der naechste Aufruf von vorn an. Mit zwei
    Stufen (Standard und 4K) auf derselben Maschine summiert sich das, und der
    Benutzer sieht "Radarr antwortet nicht", obwohl Radarr laeuft - es kam nur
    unter der Last nicht hinterher.

    Der gemerkte Fehler wird unveraendert weitergereicht, damit der Hinweis
    derselbe bleibt wie beim ersten Versuch.
    """
    sperre = _read(f"{schluessel}:fehler", FEHLER_SPERRE_SEKUNDEN)
    if sperre is not None:
        raise sperre

    try:
        return await holen()
    except ArrError as fehler:
        _write(f"{schluessel}:fehler", fehler)
        raise


def _stufen_suffix(tier: str) -> str:
    """Zusatz fuer Zwischenspeicher-Schluessel.

    Die Standard-Stufe bekommt bewusst **keinen** Zusatz: So bleiben ihre
    Schluessel zeichengleich zu vorher, und am Diff ist ablesbar, dass sich
    fuer bestehende Installationen nichts aendert.
    """
    return ":uhd" if tier == "uhd" else ""


def radarr_client(settings: AppSettings, tier: str = "standard") -> RadarrClient | None:
    url, key = settings.arr_endpoint("movie", tier)
    if not (url and key):
        return None
    return RadarrClient(url, key)


def sonarr_client(settings: AppSettings, tier: str = "standard") -> SonarrClient | None:
    url, key = settings.arr_endpoint("tv", tier)
    if not (url and key):
        return None
    return SonarrClient(url, key)


@dataclass
class MatchResult:
    """Ergebnis des Abgleichs samt Hinweis, falls er nicht moeglich war."""

    items: list[MediaItem]
    warning: str | None = None


async def movie_library(settings: AppSettings, tier: str = "standard") -> dict[int, MovieEntry]:
    schluessel = f"radarr:library{_stufen_suffix(tier)}"
    cached = _read(schluessel, LIBRARY_TTL_SECONDS)
    if cached is not None:
        return cached

    client = radarr_client(settings, tier)
    if client is None:
        return {}

    library = await _mit_fehlersperre(schluessel, client.library)
    _write(schluessel, library)
    return library


async def series_library(
    settings: AppSettings, tier: str = "standard"
) -> tuple[dict[int, SeriesEntry], dict[str, SeriesEntry]]:
    schluessel = f"sonarr:library{_stufen_suffix(tier)}"
    cached = _read(schluessel, LIBRARY_TTL_SECONDS)
    if cached is not None:
        return cached

    client = sonarr_client(settings, tier)
    if client is None:
        return {}, {}

    library = await _mit_fehlersperre(schluessel, client.library)
    _write(schluessel, library)
    return library


async def movie_calendar(settings: AppSettings, start: str, end: str) -> list[dict[str, Any]]:
    """Radarr-Kalender fuer einen Zeitraum.

    Bewusst dieselbe kurze Haltezeit wie bei der Bibliothek: Der Kalender lebt
    von ``hasFile``, und genau das kippt im Minutentakt, sobald ein Download
    fertig wird. Ein laengerer Zwischenspeicher wuerde "fehlt noch" anzeigen,
    obwohl die Datei laengst da ist.
    """
    schluessel = f"radarr:calendar:{start}:{end}"
    zwischengespeichert = _read(schluessel, LIBRARY_TTL_SECONDS)
    if zwischengespeichert is not None:
        return zwischengespeichert

    client = radarr_client(settings)
    if client is None:
        return []

    entries = await client.calendar(start, end)
    _write(schluessel, entries)
    return entries


async def series_calendar(settings: AppSettings, start: str, end: str) -> list[dict[str, Any]]:
    """Sonarr-Kalender fuer einen Zeitraum (siehe movie_calendar zur Haltezeit)."""
    schluessel = f"sonarr:calendar:{start}:{end}"
    zwischengespeichert = _read(schluessel, LIBRARY_TTL_SECONDS)
    if zwischengespeichert is not None:
        return zwischengespeichert

    client = sonarr_client(settings)
    if client is None:
        return []

    entries = await client.calendar(start, end)
    _write(schluessel, entries)
    return entries


def _aenderung(entry: MovieEntry | SeriesEntry, mit_pfad: bool) -> dict[str, object]:
    """Was an einem Titel geaendert wird - Zustand, und auf Wunsch der Pfad."""
    aenderung: dict[str, object] = {"status": _status_for(entry)}
    if mit_pfad and getattr(entry, "path", ""):
        aenderung["path"] = entry.path
    return aenderung


def _status_for(entry: MovieEntry | SeriesEntry) -> str:
    """"Liegt schon da", "liegt teilweise da" oder "noch nicht geladen".

    Der Mittelweg existiert nur bei Serien. "Bereits geladen" auf einer Serie,
    von der eine einzige Staffel da ist, war schlicht gelogen - wer sie
    anklickte, fand elf Staffeln und eine davon im Regal. Gruen bleibt der
    Zustand trotzdem: Es **gibt** etwas zu sehen.
    """
    if not entry.has_file:
        return "searching"
    return "partial" if _liegt_nur_teilweise_vor(entry) else "downloaded"


def _liegt_nur_teilweise_vor(entry: MovieEntry | SeriesEntry) -> bool:
    """Fehlen einer Serie ausgestrahlte Folgen, obwohl sie Dateien hat?

    Gerechnet ueber die Staffel-Statistik, die ohnehin mitreist: Eine Staffel
    zaehlt als Luecke, wenn ihr ausgestrahlte Folgen fehlen. Staffel 0
    (Extras) bleibt aussen vor - niemand versteht eine Serie als
    unvollstaendig, weil das Bonusmaterial fehlt. Noch nicht ausgestrahlte
    Folgen fehlen in ``folgen`` von vornherein; eine laufende Serie auf
    aktuellem Stand gilt damit als vollstaendig.

    Filme haben keine Staffeln - fuer sie ist die Antwort immer nein.
    """
    staffeln = getattr(entry, "staffeln", None) or {}
    return any(
        stand.folgen > 0 and stand.dateien < stand.folgen
        for nummer, stand in staffeln.items()
        if nummer != 0
    )


async def apply_status(
    settings: AppSettings,
    media_type: str,
    items: list[MediaItem],
    tier: str = "standard",
    *,
    mit_pfad: bool = False,
) -> MatchResult:
    """Jeder Kachel ihren Zustand geben.

    Ist Radarr/Sonarr nicht eingerichtet oder nicht erreichbar, bleiben die
    Titel auf "nicht angefragt" - mit einem Hinweis, damit niemand denkt,
    die Bibliothek sei leer.

    ``mit_pfad`` traegt zusaetzlich den Ablageort ein. **Nur fuer
    Administratoren**, und die Voreinstellung ist bewusst "nein": So kann eine
    neue Aufrufstelle den Pfad nicht versehentlich mitliefern - sie muesste ihn
    ausdruecklich verlangen.
    """
    if not items:
        return MatchResult(items=items)

    if not settings.arr_configured(media_type, tier):
        return MatchResult(items=items)

    try:
        if media_type == "movie":
            library = await movie_library(settings, tier)
            updated = [
                item.model_copy(update=_aenderung(library[item.tmdb_id], mit_pfad))
                if item.tmdb_id in library
                else item
                for item in items
            ]
        else:
            by_tvdb, by_title = await series_library(settings, tier)
            updated = []
            for item in items:
                # Erst ueber die TVDB-Id, sonst ueber den normalisierten Titel.
                entry = by_tvdb.get(item.tvdb_id) if item.tvdb_id else None
                if entry is None:
                    entry = treffer_nach_titel(
                        by_title, item.title, jahr_aus(item.release_date)
                    )
                updated.append(
                    item.model_copy(update=_aenderung(entry, mit_pfad)) if entry else item
                )
    except ArrError as error:
        return MatchResult(items=items, warning=error.message)

    return MatchResult(items=updated)


async def options(
    settings: AppSettings, media_type: str, tier: str = "standard"
) -> dict[str, Any]:
    """Qualitaetsprofile und Zielordner fuer die Auswahl beim Hinzufuegen."""
    key = f"options:{media_type}{_stufen_suffix(tier)}"
    cached = _read(key, OPTIONS_TTL_SECONDS)
    if cached is not None:
        return cached

    client = (
        radarr_client(settings, tier) if media_type == "movie" else sonarr_client(settings, tier)
    )
    if client is None:
        dienst = "Radarr" if media_type == "movie" else "Sonarr"
        raise ArrError(
            f"{dienst} ist noch nicht eingerichtet.",
            code="arr_not_configured",
            service=dienst,
        )

    profiles = await client.quality_profiles()
    folders = await client.root_folders()

    result = {
        "quality_profiles": [
            {"id": profile.get("id"), "name": profile.get("name")}
            for profile in profiles
            if profile.get("id") is not None
        ],
        "root_folders": [
            {
                "path": folder.get("path"),
                "free_space": folder.get("freeSpace"),
            }
            for folder in folders
            if folder.get("path")
        ],
    }
    _write(key, result)
    return result


async def datentraeger(
    settings: AppSettings, media_type: str, tier: str = "standard"
) -> list[dict[str, Any]]:
    """Die Datentraeger einer Instanz - Einhaengepunkt, frei und **gesamt**.

    ``/diskspace`` ist die einzige Stelle, an der Radarr und Sonarr die
    Gesamtgroesse eines Traegers herausgeben; ``/rootfolder`` kennt nur den
    freien Platz. Genau diese Gesamtgroesse wird gebraucht, um zwei Ordner
    **derselben** Platte auseinanderzuhalten - siehe ``storage.freier_platz``.

    Beachte: Die Liste enthaelt alle Einhaengepunkte des Dienstes, auch
    ``/config`` und ``/`` - und bei einem Container zeigen die oft auf
    dasselbe Dateisystem. Wer sie ungefiltert summiert, zaehlt dieselbe Platte
    dreimal. Der Aufrufer muss den Punkt heraussuchen, der zu seinem Ordner
    gehoert.
    """
    schluessel = f"diskspace:{media_type}{_stufen_suffix(tier)}"
    zwischengespeichert = _read(schluessel, OPTIONS_TTL_SECONDS)
    if zwischengespeichert is not None:
        return zwischengespeichert

    client = (
        radarr_client(settings, tier) if media_type == "movie" else sonarr_client(settings, tier)
    )
    if client is None:
        return []

    antwort = await client.get("/diskspace")
    punkte = [
        {
            "path": str(eintrag.get("path") or ""),
            "free_space": eintrag.get("freeSpace"),
            "total_space": eintrag.get("totalSpace"),
        }
        for eintrag in (antwort or [])
        if eintrag.get("path")
    ]
    _write(schluessel, punkte)
    return punkte


# --- Papierkorb -------------------------------------------------------------
#
# ⚠️ **Nexview fuehrt hier keine eigene Einstellung.** Der Stand steht in
# Radarr bzw. Sonarr, und nur dort. Wuerde Nexview ihn zusaetzlich speichern,
# liefen die beiden auseinander, sobald jemand ihn drueben aendert - und dann
# hielte Nexview eine Loeschung fuer umkehrbar, die es nicht ist. Das ist die
# eine Sorte Fehler, die man bei einem Sicherheitsnetz nicht haben darf.


@dataclass(frozen=True)
class Papierkorb:
    """Wie eine Instanz beim Loeschen mit Dateien umgeht.

    ⚠️ **"Nicht erreichbar" ist nicht dasselbe wie "kein Papierkorb".** Wer
    beides gleich behandelt, meldet einen Fehlalarm, sobald Radarr gerade neu
    startet - und Fehlalarme bringen genau die Warnung um ihre Wirkung, auf
    die es spaeter ankommt. Deshalb drei Zustaende und nicht zwei.
    """

    erreichbar: bool
    path: str = ""
    cleanup_days: int | None = None

    @property
    def geschuetzt(self) -> bool:
        """Landet hier Geloeschtes im Korb?

        Nur mit Pfad. **Ohne ihn loescht Radarr sofort und endgueltig** - in
        einem Container gibt es keinen System-Papierkorb, der noch etwas
        auffinge, und die eingestellte Aufbewahrungsfrist ist bedeutungslos.
        """
        return self.erreichbar and bool(self.path)


async def papierkorb(
    settings: AppSettings, media_type: str, tier: str = "standard"
) -> Papierkorb | None:
    """Wie ist der Papierkorb dieser Instanz eingestellt?

    ``None`` heisst "diese Instanz gibt es hier gar nicht" - dann ist auch
    nichts zu pruefen. Eine eingerichtete, aber stumme Instanz kommt dagegen
    mit ``erreichbar=False`` zurueck: Darueber laesst sich nichts sagen, und
    das ist etwas anderes als ein fehlender Papierkorb.

    **Bewusst ohne Zwischenspeicher.** Der Stand steht in Radarr bzw. Sonarr,
    und nur dort; wuerde Nexview ihn aufbewahren, liefen die beiden
    auseinander, sobald jemand ihn drueben aendert. Bei einem Sicherheitsnetz
    ist ein veralteter Zwischenspeicher genau das falsche Werkzeug.
    """
    client = (
        radarr_client(settings, tier) if media_type == "movie" else sonarr_client(settings, tier)
    )
    if client is None:
        return None
    try:
        antwort = await client.get("/config/mediamanagement") or {}
    except ArrError:
        return Papierkorb(erreichbar=False)
    tage = antwort.get("recycleBinCleanupDays")
    return Papierkorb(
        erreichbar=True,
        path=str(antwort.get("recycleBin") or ""),
        cleanup_days=tage if isinstance(tage, int) else None,
    )


async def papierkorb_setzen(
    settings: AppSettings,
    media_type: str,
    tier: str,
    *,
    pfad: str,
    tage: int | None = None,
) -> None:
    """Papierkorb der Instanz eintragen - oder mit leerem Pfad abschalten.

    ⚠️ **Erst holen, dann zurueckschicken.** ``/config/mediamanagement`` traegt
    gut zwanzig Felder, und ein PUT verlangt sie alle. Nur die geaenderten zu
    senden setzt den Rest auf die Vorgabewerte zurueck - unter anderem die
    Umbenennungsregeln und die Berechtigungen der Mediendateien.

    Der Pfad ist der, den **die Instanz** sieht (``/data/...``), nicht der des
    Hosts. Und er sollte auf demselben Dateisystem liegen wie die Medien: Sonst
    wird aus dem Verschieben ein Kopieren ueber Volume-Grenzen, und der Platz
    ist erst nach dem Aufraeumen wirklich frei.
    """
    client = (
        radarr_client(settings, tier) if media_type == "movie" else sonarr_client(settings, tier)
    )
    if client is None:
        raise ArrError(
            "Diese Instanz ist nicht eingerichtet.", 400, code="arr_instance_missing"
        )

    aktuell = await client.get("/config/mediamanagement")
    if not isinstance(aktuell, dict):
        raise ArrError(
            "Die Instanz liefert ihre Medienverwaltung nicht.",
            502,
            code="arr_media_management_missing",
        )

    geaendert = {**aktuell, "recycleBin": pfad}
    if tage is not None:
        geaendert["recycleBinCleanupDays"] = tage
    await client.put(f"/config/mediamanagement/{aktuell.get('id', 1)}", geaendert)
    # Nichts zwischenzuspeichern und nichts zu verwerfen: ``papierkorb()``
    # fragt jedes Mal frisch. Bei einem Sicherheitsnetz ist ein veralteter
    # Zwischenspeicher genau das falsche Werkzeug.


# Alle Stellen, an denen geloescht werden koennte - in Anzeigereihenfolge.
# ``arr_configured`` entscheidet, welche davon es hier ueberhaupt gibt.
INSTANZEN: tuple[tuple[str, str, str], ...] = (
    ("movie", "standard", "Radarr"),
    ("movie", "uhd", "Radarr 4K"),
    ("tv", "standard", "Sonarr"),
    ("tv", "uhd", "Sonarr 4K"),
)


async def papierkoerbe(settings: AppSettings) -> list[tuple[str, str, str, Papierkorb]]:
    """Der Papierkorb-Stand **aller** eingerichteten Instanzen.

    ⚠️ **Die Liste entsteht bei jedem Aufruf neu aus ``arr_configured``.** Wer
    naechste Woche eine zweite Instanz eintraegt, findet sie hier ohne
    Zutun - und ohne Papierkorb faellt der Gesamtzustand dadurch von selbst auf
    "nicht geschuetzt". Genau das soll er: Eine neue Instanz ist eine neue
    Stelle, an der geloescht wird.

    Alle Instanzen werden **gleichzeitig** gefragt. Nacheinander waeren es vier
    Netzwerk-Umlaeufe, bevor die Einstellungsseite ueberhaupt etwas zeigt.

    Zurueck kommen ``(media_type, tier, Anzeigename, Stand)`` - nicht
    eingerichtete Instanzen fehlen ganz.
    """
    vorhanden = [
        (art, stufe, name)
        for art, stufe, name in INSTANZEN
        if settings.arr_configured(art, stufe)
    ]
    if not vorhanden:
        return []

    staende = await asyncio.gather(
        *(papierkorb(settings, art, stufe) for art, stufe, _ in vorhanden)
    )
    return [
        (art, stufe, name, stand)
        for (art, stufe, name), stand in zip(vorhanden, staende, strict=True)
        if stand is not None
    ]


async def ordner(
    settings: AppSettings, media_type: str, tier: str, pfad: str
) -> list[str]:
    """Welche Ordner sieht **diese** Instanz unter diesem Pfad?

    Fuer die Auswahl beim Einrichten. Bewusst je Instanz gefragt und nicht
    einmal fuer alle: Sonarr kann voellig anders eingebunden sein als Radarr,
    und geraten wird hier nichts - der Pfad muss stimmen, sonst loescht Radarr
    spaeter an eine Stelle, die es gar nicht gibt.

    ⚠️ **Der abschliessende Schraegstrich ist Pflicht.** Ohne ihn listet
    ``/filesystem`` den **Elternordner** und benutzt den Rest als Praefix-Filter:
    ``/data`` liefert die Wurzel, ``/data/`` liefert den Inhalt von ``/data``.
    Nachgemessen an einer echten Instanz; ohne diese Zeile zeigt die
    Ordner-Auswahl durchweg die falsche Ebene.
    """
    client = (
        radarr_client(settings, tier) if media_type == "movie" else sonarr_client(settings, tier)
    )
    if client is None:
        return []
    try:
        antwort = await client.get("/filesystem", {"path": pfad.rstrip("/") + "/"}) or {}
    except ArrError:
        return []
    return [
        str(eintrag.get("path") or "")
        for eintrag in (antwort.get("directories") or [])
        if eintrag.get("path")
    ]


# Wieviele Verzeichnis-Abfragen eine Papierkorb-Summe hoechstens kosten darf.
# Jeder Ordner ist ein Netzwerk-Umlauf; ein Papierkorb mit tausend Titeln
# wuerde die Einstellungsseite sonst minutenlang blockieren. Wird die Grenze
# erreicht, sagt die Antwort das - eine zu kleine Zahl ohne Hinweis waere
# schlimmer als keine.
_PAPIERKORB_ABFRAGEN = 300


async def papierkorb_groesse(
    settings: AppSettings, media_type: str, tier: str, pfad: str
) -> tuple[int, bool]:
    """Wieviel Platz belegt dieser Papierkorb? ``(bytes, unvollstaendig)``.

    ⚠️ **``includeFiles=true`` ist Pflicht.** Ohne den Parameter liefert
    ``/filesystem`` ausschliesslich Ordner, und der Papierkorb sieht leer aus,
    obwohl Gigabytes darin liegen. Nachgemessen: derselbe Ordner meldete ohne
    den Parameter null Dateien und mit ihm eine mit 5,5 GB.

    Gerechnet wird ueber die Datei-Groessen, nicht ueber die der Ordner: Die
    melden durchweg null.

    Ebenenweise und gleichzeitig, mit hartem Deckel auf die Zahl der Abfragen.
    Jeder Ordner kostet einen Netzwerk-Umlauf, und niemand wartet Minuten auf
    eine Nebenangabe.
    """
    client = (
        radarr_client(settings, tier) if media_type == "movie" else sonarr_client(settings, tier)
    )
    if client is None or not pfad:
        return 0, False

    async def ebene(ordner: str) -> tuple[int, list[str]]:
        try:
            antwort = await client.get(
                "/filesystem", {"path": ordner.rstrip("/") + "/", "includeFiles": "true"}
            ) or {}
        except ArrError:
            return 0, []
        groesse = sum(
            int(datei.get("size") or 0)
            for datei in (antwort.get("files") or [])
            if isinstance(datei.get("size"), (int, float))
        )
        weiter = [
            str(unter.get("path") or "")
            for unter in (antwort.get("directories") or [])
            if unter.get("path")
        ]
        return groesse, weiter

    gesamt = 0
    offen = [pfad]
    verbraucht = 0
    unvollstaendig = False

    while offen:
        frei = _PAPIERKORB_ABFRAGEN - verbraucht
        if frei <= 0:
            unvollstaendig = True
            break
        naechste = offen[:frei]
        if len(offen) > frei:
            unvollstaendig = True
        verbraucht += len(naechste)

        ergebnisse = await asyncio.gather(*(ebene(ordner) for ordner in naechste))
        offen = []
        for groesse, weiter in ergebnisse:
            gesamt += groesse
            offen.extend(weiter)

    return gesamt, unvollstaendig


def treffer_nach_titel(
    nach_titel: dict[str, SeriesEntry], titel: str, jahr: int | None
) -> SeriesEntry | None:
    """Serie ueber den Titel finden - aber nur bei passendem Jahr.

    Der einzige erlaubte Weg zum Titel-Index. Frueher griff jede Fundstelle
    direkt darauf zu, und Namensgleichheit genuegte; gemeldet wurde eine Serie
    "Countdown" (1982), die dadurch die Folgen einer voellig anderen Serie
    gleichen Namens erbte. Die Regel steht in ``sonarr.jahre_passen``.
    """
    eintrag = nach_titel.get(normalize_title(titel))
    if eintrag is None:
        return None
    return eintrag if jahre_passen(jahr, eintrag.year) else None


def jahr_aus(datum: str | None) -> int | None:
    """Jahr aus einem Datum wie "1982-05-03"."""
    vorn = (datum or "")[:4]
    return int(vorn) if vorn.isdigit() else None


async def serien_eintrag(
    settings: AppSettings,
    tvdb_id: int | None,
    titel: str,
    jahr: int | None = None,
    tier: str = "standard",
) -> SeriesEntry | None:
    """Der Sonarr-Eintrag zu einer Serie - ueber TVDB, ersatzweise den Titel.

    Gebraucht, wo die Oberflaeche **Sonarrs** Zaehlung braucht statt der von
    TMDB: Die beiden zaehlen Folgen gern verschieden (gemessen an Baywatch
    S1: TMDB 22, Sonarr 21), und wer mit der TMDB-Zahl rechnet, haelt eine
    vollstaendige Staffel fuer ewig unvollstaendig.
    """
    client = sonarr_client(settings, tier)
    if client is None:
        return None
    try:
        nach_tvdb, nach_titel = await series_library(settings, tier)
    except ArrError:
        return None
    if tvdb_id and (treffer := nach_tvdb.get(tvdb_id)) is not None:
        return treffer
    return treffer_nach_titel(nach_titel, titel, jahr)


async def episode_availability(
    settings: AppSettings,
    tvdb_id: int | None,
    title: str,
    tier: str = "standard",
    jahr: int | None = None,
) -> dict[int, set[int]]:
    """Welche Folgen dieser Serie liegen schon vor?

    Ergebnis: Staffelnummer -> Menge der vorhandenen Folgennummern. Ist Sonarr
    nicht eingerichtet, kennt es die Serie nicht oder antwortet es nicht, kommt
    eine leere Antwort zurueck - die Staffelliste zeigt dann eben nichts als
    vorhanden an, statt gar nicht zu erscheinen.
    """
    client = sonarr_client(settings, tier)
    if client is None:
        return {}

    schluessel = f"episodes:{tvdb_id or title}{_stufen_suffix(tier)}"
    zwischengespeichert = _read(schluessel, LIBRARY_TTL_SECONDS)
    if zwischengespeichert is not None:
        return {int(staffel): set(folgen) for staffel, folgen in zwischengespeichert.items()}

    try:
        nach_tvdb, nach_titel = await series_library(settings, tier)
        eintrag = nach_tvdb.get(tvdb_id) if tvdb_id else None
        if eintrag is None:
            eintrag = treffer_nach_titel(nach_titel, title, jahr)
        if eintrag is None:
            _write(schluessel, {})
            return {}

        vorhanden = await client.episode_status(eintrag.arr_id)
    except ArrError as fehler:
        logger.warning("Episode status not available: %s", fehler.message)
        return {}

    # Mengen lassen sich nicht als JSON ablegen - fuer den Zwischenspeicher
    # werden daraus Listen.
    _write(schluessel, {str(s): sorted(f) for s, f in vorhanden.items()})
    return vorhanden
