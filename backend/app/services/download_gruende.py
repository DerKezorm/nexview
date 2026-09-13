"""Warum ein Download haengt - gelesen aus dem, was Radarr und Sonarr selbst schreiben.

⚠️ **Radarr und Sonarr liefern keine Grund-Kennung, nur Saetze.** Die API v3
kennt das interne ``ImportRejectionReason`` nicht nach aussen; eine Ablehnung
ist dort ``{reason: "<Satz>", type: "permanent"|"temporary"}``. Wer erkennen
will, was los ist, muss den Satz lesen. Gelesen im Quelltext am 12.09.2026
(Radarr 6.3, Sonarr 4.0.19): Die Import-Saetze stehen fest im Code und sind
englisch. Die Meldung des Download-Programms (``errorMessage``) dagegen geht
durch die Uebersetzung der Instanz und kann in deren Oberflaechensprache
ankommen.

Drei Regeln, und jede hat einen Grund:

* **Gelesen werden die Gruende, nicht die Titel.** Der Titel einer Statuszeile
  ist meist ein Dateiname, und "Film.2010.sample.mkv" ist kein Befund. Nur
  wenn eine Zeile gar keine Gruende traegt, ist ihr Titel selbst die Aussage
  (die Kopfzeile "One or more episodes expected ..." beim Teilimport).
* **Die erste passende Regel gewinnt.** Die Reihenfolge ist Absicht: Ein
  Download mit einem Sample traegt oft zusaetzlich "No files found are
  eligible"; die genauere Aussage ist das Sample.
* **Was keine Regel kennt, heisst "unbekannt" und bleibt im Wortlaut stehen.**
  Geraten wird nicht - eine falsche Erklaerung schickt jemanden an die falsche
  Stelle, und ein falscher Knopf loescht womoeglich das Falsche. Sagen laesst
  sich nur, woher ein unbekannter Satz kommt: Spricht allein das
  Download-Programm, heisst er "programm_unbekannt".

Dieses Modul rechnet nur. Wer abfragt, merkt und handelt, steht in
``download_haenger`` und ``download_aktionen``.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable
from dataclasses import dataclass


class Aktion(str, enum.Enum):
    """Was sich an einem haengenden Download tun laesst - die Knoepfe."""

    manuell_importieren = "manuell_importieren"
    entfernen_neu_suchen = "entfernen_neu_suchen"
    entfernen = "entfernen"
    erneut_pruefen = "erneut_pruefen"


#: Was die Automatik ueberhaupt darf.
#:
#: ⚠️ **Der manuelle Import gehoert nie dazu.** Radarr prueft beim Befehl
#: ``ManualImport`` die Ablehnungen nicht noch einmal (gelesen in
#: ``ManualImportService``): Wer ein Sample mitschickt, bekommt es als Film in
#: die Bibliothek. Das darf nur ein Mensch entscheiden, der die Datei gesehen hat.
AUTOMATISCH_MOEGLICH = frozenset(
    {Aktion.entfernen_neu_suchen, Aktion.entfernen, Aktion.erneut_pruefen}
)


@dataclass(frozen=True)
class Grund:
    """Eine Regel: woran der Grund zu erkennen ist und was dagegen hilft."""

    #: Stabil, traegt die Uebersetzung (``downloads.grund.<kennung>``).
    kennung: str
    #: Teiltexte in Kleinschrift. Passen nur als ganze Woerter, damit
    #: "stalled" nicht in "installed" anschlaegt.
    enthaelt: tuple[str, ...] = ()
    #: Ganze Gruende, verglichen nach Kleinschrift und ohne Leerraum.
    genau: tuple[str, ...] = ()
    #: Teiltexte, die **alle** vorkommen muessen, verteilt ueber die Gruende.
    #: Fuer Aussagen, die erst zusammen stimmen: "nicht alles importiert"
    #: plus "schon importiert" heisst, ein Teil liegt schon da.
    alle: tuple[str, ...] = ()
    #: Die Knoepfe, das Wahrscheinlichste zuerst.
    aktionen: tuple[Aktion, ...] = ()
    #: Was die Automatik bei diesem Grund tun darf - immer eine Teilmenge von
    #: ``aktionen`` und von ``AUTOMATISCH_MOEGLICH``.
    automatik: tuple[Aktion, ...] = ()
    #: Kommt vom Download-Programm statt vom Import. Solche Gruende brauchen
    #: laenger, bis sie als haengend gelten: Ein Torrent ohne Gegenstelle
    #: findet oft nach ein paar Minuten doch noch eine.
    vom_programm: bool = False


_ENS = Aktion.entfernen_neu_suchen
_ENT = Aktion.entfernen
_MAN = Aktion.manuell_importieren
_ERN = Aktion.erneut_pruefen

#: Die Regeln in der Reihenfolge, in der sie gefragt werden.
REGELN: tuple[Grund, ...] = (
    Grund(
        "gefaehrliche_datei",
        enthaelt=(
            "caution: found potentially dangerous file",
            "caution: found executable file",
        ),
        aktionen=(_ENS, _ENT),
        automatik=(_ENS, _ENT),
    ),
    Grund(
        "pfadzuordnung",
        enthaelt=("is not a valid local path", "remote path mapping"),
        aktionen=(_ERN,),
    ),
    Grund(
        "pfad_ohne_unterordner",
        enthaelt=("doesn't contain intermediate path", "does not contain intermediate path"),
        aktionen=(_ERN, _ENT),
    ),
    Grund(
        "pfad_in_bibliothek",
        enthaelt=("import path is mapped to a",),
        aktionen=(_ERN,),
    ),
    Grund(
        "nicht_gegriffen",
        enthaelt=("wasn't grabbed by", "was not grabbed by"),
        aktionen=(_MAN, _ENT),
    ),
    Grund(
        "titel_passt_nicht",
        enthaelt=("title mismatch",),
        aktionen=(_MAN, _ENS),
    ),
    Grund(
        "zuordnung_per_kennung",
        enthaelt=("matched to movie by id", "matched to series by id"),
        aktionen=(_MAN,),
    ),
    Grund(
        "mehrere_treffer",
        enthaelt=("found multiple series", "found multiple movies"),
        aktionen=(_MAN,),
    ),
    Grund(
        "unbekannter_titel",
        enthaelt=("unknown series", "unknown movie", "invalid movie"),
        aktionen=(_MAN, _ENT),
    ),
    Grund(
        "nicht_lesbar",
        enthaelt=("unable to parse",),
        aktionen=(_MAN, _ENS),
    ),
    Grund(
        "wird_entpackt",
        enthaelt=("file is still being unpacked",),
        aktionen=(_ERN,),
        automatik=(_ERN,),
    ),
    Grund(
        "datei_gesperrt",
        enthaelt=("locked file",),
        aktionen=(_ERN,),
        automatik=(_ERN,),
    ),
    # ⚠️ **Radarr 6.3 meldet ein Archiv nicht als Archiv.** Am Pruefstand
    # (13.09.2026) kam fuer .zip und .rar "Invalid video file, unsupported
    # extension: '.zip'", nicht "Found archive file". Ohne die Endungen hier
    # hiesse es "kein Video" und "Release sperren" - fuer ein Release, das nur
    # entpackt werden muss.
    Grund(
        "archiv",
        enthaelt=(
            "found archive file",
            "unsupported extension: '.zip'",
            "unsupported extension: '.zipx'",
            "unsupported extension: '.rar'",
            "unsupported extension: '.r00'",
            "unsupported extension: '.7z'",
            "unsupported extension: '.tar'",
            "unsupported extension: '.gz'",
            "unsupported extension: '.tgz'",
            "unsupported extension: '.bz2'",
            "unsupported extension: '.tbz2'",
            "unsupported extension: '.tb2'",
        ),
        aktionen=(_ERN, _MAN, _ENS),
    ),
    Grund(
        "sample_unklar",
        enthaelt=("unable to determine if file is a sample",),
        aktionen=(_MAN, _ENS),
        automatik=(_ENS,),
    ),
    Grund(
        "sample",
        genau=("sample",),
        aktionen=(_ENS, _MAN),
        automatik=(_ENS,),
    ),
    Grund(
        "falsche_endung",
        enthaelt=("invalid video file",),
        aktionen=(_ENS, _MAN),
        automatik=(_ENS,),
    ),
    Grund(
        "keine_tonspur",
        enthaelt=("no audio tracks detected",),
        aktionen=(_ENS, _MAN),
        automatik=(_ENS,),
    ),
    # Vor "kein_upgrade" und "schon_importiert": Ein Staffelpaket, dessen
    # passende Folgen schon liegen, traegt beide Saetze und dazu die Kopfzeile.
    # Am Pruefstand gemessen (13.09.2026, Sonarr 4.0) - vorher hiess das "ist
    # erledigt", obwohl eine Folge fehlte.
    Grund(
        "teilweise_importiert",
        alle=("expected in this release were not imported", "already imported at"),
        aktionen=(_ENT, _ENS),
        automatik=(_ENT,),
    ),
    Grund(
        "kein_upgrade",
        enthaelt=(
            "not an upgrade for existing",
            "not a custom format upgrade",
            "not a quality revision upgrade",
        ),
        aktionen=(_ENT, _MAN),
        automatik=(_ENT,),
    ),
    Grund(
        "schon_importiert",
        enthaelt=("already imported at",),
        aktionen=(_ENT,),
        automatik=(_ENT,),
    ),
    # Gemessen (13.09.2026): Fuer einen Film geholt, im Download steckt ein
    # anderer - Radarr sagt "Movie [...] was not found in the grabbed release".
    # Das ist kein fehlender Teil, sondern falscher Inhalt.
    Grund(
        "falscher_inhalt",
        enthaelt=("was not found in the grabbed release", "was unexpected considering the"),
        aktionen=(_ENS, _MAN),
        automatik=(_ENS,),
    ),
    Grund(
        "teilpaket",
        enthaelt=("partial season packs are not supported", "extras are not supported"),
        aktionen=(_MAN, _ENS),
    ),
    Grund(
        "folgenzuordnung",
        enthaelt=("individual episode mappings on thexem", "invalid season or episode"),
        aktionen=(_MAN, _ENS),
    ),
    Grund(
        "unvollstaendig",
        enthaelt=("expected in this release were not imported",),
        aktionen=(_MAN, _ENS),
    ),
    Grund(
        "mehrteilig",
        enthaelt=("suspected multi-part file",),
        aktionen=(_MAN,),
    ),
    Grund(
        "kein_platz",
        enthaelt=("not enough free space",),
        aktionen=(_ERN,),
    ),
    # ⚠️ **Ohne Automatik, und das mit Grund.** Derselbe Satz kommt, wenn der
    # Download leer ist, und wenn Radarr den Ordner gar nicht sieht: Am
    # Pruefstand (13.09.2026) meldete eine fehlende Pfadzuordnung genau das,
    # nicht "not a valid local path". Automatisch sperren hiesse dann, jedes
    # gute Release zu verwerfen, solange die Einstellung fehlt.
    Grund(
        "keine_datei",
        enthaelt=("no files found are eligible for import",),
        aktionen=(_ENS, _ERN),
    ),
    Grund(
        "programm_haengt",
        enthaelt=("stalled", "no connections"),
        aktionen=(_ENS, _ENT),
        automatik=(_ENS,),
        vom_programm=True,
    ),
    Grund(
        "magnet",
        enthaelt=("cannot resolve magnet",),
        # Ohne Automatik: Radarr schreibt den Satz, wenn DHT im Download-Programm
        # aus ist. Liegt es daran, hilft kein anderes Release.
        aktionen=(_ENS, _ENT),
        vom_programm=True,
    ),
    Grund(
        "programm_fehler",
        enthaelt=("is reporting an error", "missing files", "par status", "unpack status"),
        # ⚠️ **Ohne Automatik.** Hinter diesen Saetzen steckt nicht immer ein
        # kaputtes Release: NZBGet meldet vollen Speicher beim Entpacken als
        # Warnung (``UnpackStatus == "SPACE"``, gelesen in Radarrs Nzbget.cs am
        # 13.09.2026). Automatisch sperren hiesse dann, ein gutes Release nach
        # dem anderen zu verwerfen.
        aktionen=(_ENS, _ENT),
        vom_programm=True,
    ),
    Grund(
        "programm_unerreichbar",
        enthaelt=("unable to communicate with",),
        aktionen=(_ERN,),
        vom_programm=True,
    ),
)

#: Ohne Muster - fuer das, was keine Regel trifft.
AUFFANG: tuple[Grund, ...] = (
    Grund("fehlgeschlagen", aktionen=(_ENS, _ENT), automatik=(_ENS,), vom_programm=True),
    # Ohne Automatik wie "programm_fehler", nur mit mehr Grund: Bekannt ist
    # allein, dass der Satz vom Download-Programm kommt (``_nur_vom_programm``).
    Grund("programm_unbekannt", aktionen=(_ENS, _ENT), vom_programm=True),
    Grund("unbekannt", aktionen=(_ERN, _ENT)),
)

#: Alle Gruende nach Kennung - auch fuer die Waechter, die je Grund einen Text
#: in beiden Sprachen verlangen.
GRUENDE: dict[str, Grund] = {grund.kennung: grund for grund in REGELN + AUFFANG}


@dataclass(frozen=True)
class Einordnung:
    grund: Grund
    #: Die Gruende im Wortlaut der Instanz, ohne Dopplung.
    wortlaut: tuple[str, ...]


def _klein(wert: object) -> str:
    return str(wert or "").strip().lower()


#: Zustaende, in denen ein Download nicht von selbst weitergeht.
_ZUSTAND_HAENGT = frozenset({"importblocked", "failedpending", "failed"})
#: Meldestufen der Instanz, die etwas zu sagen haben.
_MELDUNG_HAENGT = frozenset({"warning", "error"})
#: Was das Download-Programm selbst als Stoerung meldet.
_PROGRAMM_HAENGT = frozenset({"failed", "warning", "downloadclientunavailable"})


def ist_gestoert(satz: dict) -> bool:
    """Meldet diese Zeile der Warteschlange eine Stoerung?

    ⚠️ **``importPending`` allein ist keine.** Es ist der kurze, normale Schritt
    zwischen fertig geladen und importiert. Erst mit einer Warnung daneben
    heisst es "versucht es jede Minute neu und scheitert". Ebenso ``delay``
    (eine Verzoegerung aus dem Profil) und ``paused``: Das hat jemand so
    eingestellt.
    """
    return (
        _klein(satz.get("trackedDownloadState")) in _ZUSTAND_HAENGT
        or _klein(satz.get("trackedDownloadStatus")) in _MELDUNG_HAENGT
        or _klein(satz.get("status")) in _PROGRAMM_HAENGT
    )


def wortlaut(saetze: Iterable[dict]) -> list[str]:
    """Die Gruende aller Zeilen eines Downloads, in ihrer Reihenfolge, ohne Dopplung."""
    texte: list[str] = []
    for satz in saetze:
        for zeile in satz.get("statusMessages") or []:
            if not isinstance(zeile, dict):
                continue
            gruende = [
                str(text).strip()
                for text in zeile.get("messages") or []
                if isinstance(text, str) and text.strip()
            ]
            if gruende:
                texte.extend(gruende)
            elif isinstance(zeile.get("title"), str) and zeile["title"].strip():
                # Ohne Gruende ist der Titel selbst die Aussage - siehe oben.
                texte.append(zeile["title"].strip())
        fehler = satz.get("errorMessage")
        if isinstance(fehler, str) and fehler.strip():
            texte.append(fehler.strip())
    return list(dict.fromkeys(texte))


def _als_wort(muster: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z])" + re.escape(muster) + r"(?![a-z])")


_MUSTER: dict[str, tuple[re.Pattern[str], ...]] = {
    grund.kennung: tuple(_als_wort(m) for m in grund.enthaelt) for grund in REGELN
}


def _passt(grund: Grund, texte: list[str]) -> bool:
    if grund.alle:
        return all(any(teil in text for text in texte) for teil in grund.alle)
    if any(text in grund.genau for text in texte):
        return True
    return any(muster.search(text) for muster in _MUSTER.get(grund.kennung, ()) for text in texte)


def einordnen(saetze: list[dict]) -> Einordnung | None:
    """Der Grund fuer einen Download - ``None``, wenn nichts gestoert ist.

    ``saetze`` sind alle Zeilen **eines** Downloads. Sonarr fuehrt eine Zeile
    je Folge; ein Staffelpaket kommt also mit zwanzig Zeilen, die zusammen
    eine Aussage ergeben.
    """
    if not any(ist_gestoert(satz) for satz in saetze):
        return None
    gelesen = wortlaut(saetze)
    klein = [" ".join(text.lower().split()) for text in gelesen]
    for grund in REGELN:
        if _passt(grund, klein):
            return Einordnung(grund, tuple(gelesen))

    programme = {_klein(satz.get("status")) for satz in saetze}
    zustaende = {_klein(satz.get("trackedDownloadState")) for satz in saetze}
    if "downloadclientunavailable" in programme:
        return Einordnung(GRUENDE["programm_unerreichbar"], tuple(gelesen))
    if zustaende & {"failed", "failedpending"} or "failed" in programme:
        return Einordnung(GRUENDE["fehlgeschlagen"], tuple(gelesen))
    if "warning" in programme and _nur_vom_programm(saetze):
        return Einordnung(GRUENDE["programm_unbekannt"], tuple(gelesen))
    return Einordnung(GRUENDE["unbekannt"], tuple(gelesen))


def _nur_vom_programm(saetze: list[dict]) -> bool:
    """Spricht allein das Download-Programm?

    ⚠️ **Hier entscheidet die Herkunft, nicht die Sprache.** ``errorMessage``
    ist die Meldung des Download-Programms und kommt uebersetzt an, sobald
    Radarr bzw. Sonarr auf eine andere Sprache gestellt ist. Am Pruefstand
    gemessen (13.09.2026, Radarr 6.3.0, Sonarr 4.0.19): Aus "The download is
    stalled with no connections" wurden auf Deutsch zwei verschiedene Saetze,
    und keine Regel traf mehr. Die Saetze in ``statusMessages`` blieben
    englisch. Muster je Sprache liefen jeder Uebersetzung hinterher; woher ein
    Satz kommt, bleibt gleich.
    """
    spricht = any(
        isinstance(satz.get("errorMessage"), str) and satz["errorMessage"].strip()
        for satz in saetze
    )
    return spricht and not any(satz.get("statusMessages") for satz in saetze)
