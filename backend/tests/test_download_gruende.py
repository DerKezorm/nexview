"""Der Regelkatalog fuer haengende Downloads (``services/download_gruende``).

Die Beispielsaetze stammen aus dem Quelltext von Radarr und Sonarr, gelesen am
12.09.2026 (Radarr 6.3, Sonarr 4.0.19). Was am Pruefstand wirklich ankam
(13.09.2026, dieselben Fassungen), steht unten in ``GEMESSEN`` - dort mit
ersetzten Titeln.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.beschaffung import AUTOMATISCH_MOEGLICH
from app.services.beschaffung.arr import download_gruende as gruende
from app.services.beschaffung.arr.download_gruende import Aktion
from app.services.beschaffung.nex import downloads as nex_downloads


def _zeile(
    *texte: str,
    zustand: str = "importPending",
    meldung: str = "warning",
    programm: str = "completed",
    titel: str = "Film.2010.1080p.WEB-GRP",
    fehler: str | None = None,
) -> dict:
    return {
        "trackedDownloadState": zustand,
        "trackedDownloadStatus": meldung,
        "status": programm,
        "statusMessages": [{"title": titel, "messages": list(texte)}] if texte else [],
        "errorMessage": fehler,
    }


#: Je Grund ein Download, der ihn ausloest.
BEISPIELE: dict[str, dict] = {
    "gefaehrliche_datei": _zeile("Caution: Found executable file with extension: '.exe'"),
    "pfadzuordnung": _zeile(
        "[/downloads/Film.2010] is not a valid local path. You may need a Remote Path Mapping."
    ),
    "pfad_ohne_unterordner": _zeile("Download doesn't contain intermediate path, Skipping."),
    "pfad_in_bibliothek": _zeile("Import path is mapped to a series folder"),
    "nicht_gegriffen": _zeile(
        "Download wasn't grabbed by Radarr and not in a category, Skipping."
    ),
    "titel_passt_nicht": _zeile(
        "Movie title mismatch, automatic import is not possible. Manual Import required.",
        zustand="importBlocked",
    ),
    "zuordnung_per_kennung": _zeile(
        "Found matching movie via grab history, but release was matched to movie by ID. "
        "Manual Import required.",
        zustand="importBlocked",
    ),
    "mehrere_treffer": _zeile(
        "Unable to import automatically, found multiple series: Serie (2010), Serie (2011)"
    ),
    "unbekannter_titel": _zeile("Unknown Series"),
    "nicht_lesbar": _zeile(
        "Unable to parse download, automatic import is not possible.", zustand="importBlocked"
    ),
    "wird_entpackt": _zeile("File is still being unpacked"),
    "datei_gesperrt": _zeile("Locked file, try again later"),
    "archiv": _zeile("Found archive file, might need to be extracted"),
    "sample_unklar": _zeile("Unable to determine if file is a sample"),
    "sample": _zeile("Sample"),
    "falsche_endung": _zeile("Invalid video file, unsupported extension: '.iso'"),
    "keine_tonspur": _zeile("No audio tracks detected"),
    "kein_upgrade": _zeile(
        "Not an upgrade for existing movie file. Existing quality: Bluray-2160p. "
        "New Quality WEBDL-1080p."
    ),
    "schon_importiert": _zeile("Episode file already imported at 2026-09-12 20:00:00"),
    "teilweise_importiert": {
        "trackedDownloadState": "importBlocked",
        "trackedDownloadStatus": "warning",
        "status": "completed",
        "statusMessages": [
            {
                "title": "One or more episodes expected in this release were not "
                "imported or missing from the release",
                "messages": [],
            },
            {
                "title": "Beispielserie.S01E01.mkv",
                "messages": ["Episode file already imported at 09/12/2026 23:57:06"],
            },
        ],
    },
    "falscher_inhalt": _zeile(
        "Movie [Beispielfilm (2010)][tt0000001, 5] was not found in the grabbed release: "
        "Anderer.Film.2011.1080p-GRP"
    ),
    "teilpaket": _zeile("Partial season packs are not supported"),
    "folgenzuordnung": _zeile("Invalid season or episode"),
    # Die Kopfzeile beim Teilimport traegt ihre Aussage im Titel, ohne Gruende.
    "unvollstaendig": {
        "trackedDownloadState": "importBlocked",
        "trackedDownloadStatus": "warning",
        "status": "completed",
        "statusMessages": [
            {
                "title": "One or more episodes expected in this release were not "
                "imported or missing from the release",
                "messages": [],
            }
        ],
    },
    "mehrteilig": _zeile("File is suspected multi-part file, Radarr doesn't support this"),
    "kein_platz": _zeile("Not enough free space"),
    "keine_datei": _zeile("No files found are eligible for import in /downloads/Film.2010"),
    "programm_haengt": _zeile(
        zustand="downloading", programm="warning",
        fehler="The download is stalled with no connections",
    ),
    "magnet": _zeile(
        zustand="downloading", programm="warning",
        fehler="qBittorrent cannot resolve magnet link with DHT disabled",
    ),
    "programm_fehler": _zeile(
        zustand="downloading", programm="warning", fehler="qBittorrent is reporting an error"
    ),
    "programm_unerreichbar": _zeile(
        zustand="downloading", programm="downloadClientUnavailable",
        fehler="Unable to communicate with qBittorrent",
    ),
    "fehlgeschlagen": _zeile(
        zustand="failedPending", meldung="error", programm="failed",
        fehler="Aborted, cannot be completed",
    ),
    "programm_unbekannt": _zeile(
        zustand="downloading", programm="warning",
        fehler="Der Download ist ohne Verbindung stehen geblieben",
    ),
    "unbekannt": _zeile("Something nobody has seen before"),
}


def test_zu_jedem_grund_gibt_es_ein_beispiel() -> None:
    """⚠️ Eine Regel ohne Beispiel ist ungeprueft mitgeliefert.

    Kommt ein Grund dazu, faellt dieser Test, bis hier ein Satz steht, der ihn
    ausloest. Die umgekehrte Richtung faengt Beispiele fuer Gruende, die es
    nicht mehr gibt.
    """
    assert set(BEISPIELE) == set(gruende.GRUENDE)


@pytest.mark.parametrize("kennung", sorted(BEISPIELE))
def test_das_beispiel_trifft_seinen_grund(kennung: str) -> None:
    einordnung = gruende.einordnen([BEISPIELE[kennung]])
    assert einordnung is not None
    assert einordnung.grund.kennung == kennung


@pytest.mark.parametrize(
    "satz",
    [
        pytest.param(_zeile(zustand="downloading", meldung="ok", programm="downloading"), id="laedt"),
        pytest.param(_zeile(zustand="importPending", meldung="ok"), id="import-kurz"),
        pytest.param(_zeile(zustand="importing", meldung="ok"), id="importiert-gerade"),
        pytest.param(_zeile(zustand="downloading", meldung="ok", programm="delay"), id="verzoegert"),
        pytest.param(_zeile(zustand="downloading", meldung="ok", programm="paused"), id="pausiert"),
        pytest.param(_zeile(zustand="downloading", meldung="ok", programm="queued"), id="wartet"),
    ],
)
def test_ein_laufender_download_ist_kein_befund(satz: dict) -> None:
    """``importPending`` allein ist der normale kurze Schritt vor dem Import."""
    assert gruende.einordnen([satz]) is None


def test_ein_dateiname_mit_sample_ist_kein_grund() -> None:
    """Gelesen werden die Gruende, nicht der Titel - der ist hier ein Dateiname."""
    satz = _zeile(
        "Not an upgrade for existing movie file. Existing quality: Remux-2160p.",
        titel="Film.2010.1080p.sample.mkv",
    )
    einordnung = gruende.einordnen([satz])
    assert einordnung is not None
    assert einordnung.grund.kennung == "kein_upgrade"
    assert "Film.2010.1080p.sample.mkv" not in einordnung.wortlaut


def test_installed_ist_nicht_stalled() -> None:
    """Muster passen nur als ganze Woerter.

    Der Satz kommt vom Programm, gelesen ist er damit nicht: "haengt" waere geraten.
    """
    einordnung = gruende.einordnen(
        [_zeile(zustand="downloading", programm="warning", fehler="Plugin is not installed")]
    )
    assert einordnung is not None
    assert einordnung.grund.kennung == "programm_unbekannt"


def test_das_sample_geht_vor_keine_datei() -> None:
    """Die genauere Aussage gewinnt, egal in welcher Reihenfolge die Saetze stehen."""
    satz = _zeile("No files found are eligible for import in /downloads/Film.2010", "Sample")
    einordnung = gruende.einordnen([satz])
    assert einordnung is not None
    assert einordnung.grund.kennung == "sample"


def test_zeilen_eines_downloads_ergeben_eine_aussage() -> None:
    """Sonarr: eine Zeile je Folge. Eine gestoerte Folge macht den Download gestoert."""
    ruhig = _zeile(zustand="importPending", meldung="ok")
    gestoert = _zeile("Sample")
    doppelt = _zeile("Sample")

    einordnung = gruende.einordnen([ruhig, gestoert, doppelt])

    assert einordnung is not None
    assert einordnung.grund.kennung == "sample"
    assert einordnung.wortlaut == ("Sample",)


def test_gross_und_leerraum_stoeren_nicht() -> None:
    assert gruende.einordnen([_zeile("  SAMPLE  ")]).grund.kennung == "sample"
    assert (
        gruende.einordnen([_zeile("Not  an   upgrade for existing movie file")]).grund.kennung
        == "kein_upgrade"
    )


def test_ein_stummes_programm_ohne_satz() -> None:
    einordnung = gruende.einordnen(
        [_zeile(zustand="downloading", meldung="ok", programm="downloadClientUnavailable")]
    )
    assert einordnung is not None
    assert einordnung.grund.kennung == "programm_unerreichbar"


def test_unbekannt_behaelt_den_wortlaut() -> None:
    satz = _zeile("Something nobody has seen before", fehler="And a second line")
    einordnung = gruende.einordnen([satz])
    assert einordnung.wortlaut == ("Something nobody has seen before", "And a second line")


@pytest.mark.parametrize("kennung", sorted(gruende.GRUENDE))
def test_jeder_grund_hat_knoepfe_und_die_automatik_bleibt_darin(kennung: str) -> None:
    """Die Automatik darf nur, was auch ein Mensch per Knopf darf - und nie importieren."""
    grund = gruende.GRUENDE[kennung]
    assert grund.aktionen, kennung
    assert len(set(grund.aktionen)) == len(grund.aktionen)
    assert set(grund.automatik) <= set(grund.aktionen)
    assert set(grund.automatik) <= AUTOMATISCH_MOEGLICH
    assert Aktion.manuell_importieren not in grund.automatik


#: Wortlaut vom Pruefstand (13.09.2026, Radarr 6.3.0, Sonarr 4.0.19), Titel ersetzt.
GEMESSEN = [
    ("Invalid video file, unsupported extension: '.zip'", "archiv"),
    ("Invalid video file, unsupported extension: '.rar'", "archiv"),
    ("Invalid video file, unsupported extension: '.nfo'", "falsche_endung"),
    ("Invalid video file, unsupported extension: '.txt'", "falsche_endung"),
    # Laedt der Torrent einen Ordner statt einer einzelnen Datei, lautet der Satz anders.
    ("Found archive file, might need to be extracted", "archiv"),
    (
        "No files found are eligible for import in /data/torrents/radarr/Beispielfilm.2010",
        "keine_datei",
    ),
    ("Caution: Found executable file with extension: '.exe'", "gefaehrliche_datei"),
    ("Caution: Found executable file", "gefaehrliche_datei"),
    ("Unable to determine if file is a sample", "sample_unklar"),
    (
        "Movie title mismatch, automatic import is not possible. Manual Import required.",
        "titel_passt_nicht",
    ),
    (
        (
            "Series title mismatch; automatic import is not possible. Check the download "
            "troubleshooting entry on the wiki for common causes."
        ),
        "titel_passt_nicht",
    ),
    (
        "Not an upgrade for existing movie file. Existing quality: Bluray-480p. New Quality SDTV.",
        "kein_upgrade",
    ),
    # ⚠️ Das war am Pruefstand eine fehlende Pfadzuordnung, kein leerer Download.
    (
        (
            "No files found are eligible for import in "
            "/downloads/radarr-unmapped/Beispielfilm.2010/Beispielfilm.2010.mkv"
        ),
        "keine_datei",
    ),
    (
        (
            "Movie [Beispielfilm (2010)][tt0000001, 19] was not found in the grabbed release: "
            "Anderer.Film.1962.1080p-GRP"
        ),
        "falscher_inhalt",
    ),
]


@pytest.mark.parametrize(("satz", "erwartet"), GEMESSEN)
def test_der_wortlaut_vom_pruefstand(satz: str, erwartet: str) -> None:
    einordnung = gruende.einordnen([_zeile(satz)])
    assert einordnung is not None
    assert einordnung.grund.kennung == erwartet


def test_sample_ohne_tonspur_vom_pruefstand() -> None:
    """Radarr schrieb beide Saetze an dieselbe Datei; die genauere Aussage ist das Sample."""
    assert gruende.einordnen([_zeile("No audio tracks detected", "Sample")]).grund.kennung == "sample"


def test_ein_teilweise_importiertes_paket_vom_pruefstand() -> None:
    """Sonarr, Staffelpaket mit einer falschen Folge: vier Saetze, eine Aussage."""
    satz = {
        "trackedDownloadState": "importBlocked",
        "trackedDownloadStatus": "warning",
        "status": "completed",
        "statusMessages": [
            {
                "title": "One or more episodes expected in this release were not imported "
                "or missing from the release",
                "messages": [],
            },
            {"title": "S02E01.mkv", "messages": ["Episode file already imported at 09/12/2026 23:58:29"]},
            {
                "title": "S03E03.mkv",
                "messages": [
                    "Episode 3x03 was unexpected considering the Beispielserie.S02.1080p-GRP folder name",
                    "Episode 3x03 was not found in the grabbed release: Beispielserie.S02.1080p-GRP",
                ],
            },
        ],
    }
    assert gruende.einordnen([satz]).grund.kennung == "teilweise_importiert"


def test_ein_haengender_torrent_vom_pruefstand() -> None:
    """⚠️ Die Meldestufe steht dabei auf ok - gestoert ist nur das Download-Programm."""
    satz = _zeile(
        zustand="downloading",
        meldung="ok",
        programm="warning",
        fehler="The download is stalled with no connections",
    )
    assert gruende.einordnen([satz]).grund.kennung == "programm_haengt"


@pytest.mark.parametrize(
    "fehler",
    [
        pytest.param("Der Download ist ohne Verbindung stehen geblieben", id="radarr"),
        pytest.param("Der Download ist ohne Verbindung ins Stocken geraten", id="sonarr"),
    ],
)
def test_eine_uebersetzte_programmmeldung_vom_pruefstand(fehler: str) -> None:
    """⚠️ Auf Deutsch gestellt, uebersetzen Radarr und Sonarr den Satz des Programms.

    Derselbe englische Satz wurde am Pruefstand zu zwei verschiedenen deutschen.
    Erkannt wird deshalb, woher er kommt, und der Wortlaut bleibt stehen.
    """
    satz = _zeile(zustand="downloading", meldung="ok", programm="warning", fehler=fehler)
    einordnung = gruende.einordnen([satz])
    assert einordnung.grund.kennung == "programm_unbekannt"
    assert einordnung.wortlaut == (fehler,)


def test_ein_unbekannter_import_satz_ist_nicht_das_programm() -> None:
    """Sagt auch der Import etwas, spricht nicht allein das Programm. Dann raet Nexview nicht."""
    satz = _zeile("Etwas, das niemand kennt", programm="warning", fehler="Und das Programm auch")
    assert gruende.einordnen([satz]).grund.kennung == "unbekannt"


def test_ohne_satz_ist_es_nicht_das_programm() -> None:
    satz = _zeile(zustand="downloading", meldung="ok", programm="warning")
    assert gruende.einordnen([satz]).grund.kennung == "unbekannt"


def test_ohne_warnung_des_programms_ist_es_nicht_das_programm() -> None:
    """Fertig geladen, ohne Warnung des Programms: kein Befund ueber das Programm."""
    satz = _zeile(zustand="importPending", programm="completed", fehler="Irgendetwas")
    assert gruende.einordnen([satz]).grund.kennung == "unbekannt"


@pytest.mark.parametrize(
    "kennung",
    [
        "programm_haengt",
        "magnet",
        "programm_fehler",
        "programm_unerreichbar",
        "fehlgeschlagen",
        "programm_unbekannt",
    ],
)
def test_was_vom_programm_kommt_wartet_laenger(kennung: str) -> None:
    """Ein uebersetzter haengender Torrent wartet so lange wie ein englischer.

    Sonst hiesse derselbe Torrent auf Deutsch nach zehn Minuten "haengt", auf
    Englisch erst nach fuenfzehn.
    """
    assert gruende.GRUENDE[kennung].vom_programm is True


@pytest.mark.parametrize(
    "kennung",
    [
        "keine_datei",
        "pfadzuordnung",
        "programm_unerreichbar",
        "kein_platz",
        "magnet",
        "programm_fehler",
        "programm_unbekannt",
        "unbekannt",
    ],
)
def test_ein_mehrdeutiger_grund_hat_keine_automatik(kennung: str) -> None:
    """Wo die Ursache an der Einstellung liegen kann, sperrt keine Automatik.

    ⚠️ ``keine_datei`` war am Pruefstand eine fehlende Pfadzuordnung. Eine
    Regel "sperren und neu suchen" verwuerfe dann jedes gute Release, solange
    die Einstellung fehlt - und das still.
    """
    assert gruende.GRUENDE[kennung].automatik == ()


def test_die_kennungen_sind_eindeutig() -> None:
    alle = [g.kennung for g in gruende.REGELN + gruende.AUFFANG]
    assert len(alle) == len(set(alle))


SPRACHEN = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"


@pytest.mark.parametrize("sprache", ["de", "en"])
def test_jeder_grund_und_jede_aktion_hat_texte(sprache: str) -> None:
    """Fehlt ein Text, steht auf der Seite der Schluessel - einen Rueckfall gibt es nicht.

    Die Oberflaeche baut die Schluessel zusammen (``downloads.grund.<kennung>``),
    und die sieht ihr eigener Waechter nicht. Deshalb steht die Pruefung hier,
    wo die Kennungen herkommen.
    """
    texte = json.loads((SPRACHEN / f"{sprache}.downloads.json").read_text(encoding="utf-8"))
    downloads = texte["downloads"]
    fehlend = [
        f"grund.{kennung}.{teil}"
        for kennung in gruende.GRUENDE
        for teil in ("titel", "hilfe")
        if not isinstance(downloads.get("grund", {}).get(kennung, {}).get(teil), str)
    ]
    fehlend += [
        f"aktion.{aktion.value}"
        for aktion in Aktion
        if not isinstance(downloads.get("aktion", {}).get(aktion.value), str)
    ]
    fehlend += [
        f"history.was.{was}"
        for was in ["erkannt", "gemeldet", *(aktion.value for aktion in Aktion)]
        if not isinstance(downloads.get("history", {}).get("was", {}).get(was), str)
    ]
    fehlend += [
        f"grund.{kennung}.{teil}"
        for kennung in nex_downloads.PROBLEME
        for teil in ("titel", "hilfe")
        if not isinstance(downloads.get("grund", {}).get(kennung, {}).get(teil), str)
    ]
    assert fehlend == [], f"{sprache}.downloads.json: {fehlend}"
    # Und keine Texte fuer Gruende, die es nicht mehr gibt - in **beiden**
    # Betriebsarten. Radarr und Sonarr melden Saetze, die Nexview zu Kennungen
    # macht (``GRUENDE``); nexcrate meldet Kennungen selbst (``PROBLEME``).
    assert set(downloads["grund"]) == set(gruende.GRUENDE) | nex_downloads.PROBLEME
