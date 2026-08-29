"""Musternamen aufraeumen und das Benennungsschema setzen.

⚠️ **Warum das hier besonders genau geprueft wird.** Der Vorsatz ``NXV - ``
landet ueber ``includeCustomFormatWhenRenaming`` im **Dateinamen**. Wer ihn
stehen laesst und dann den Bestand umbenennt, schreibt ihn in tausende
Dateien - und bekommt ihn nur mit einem zweiten kompletten Lauf wieder heraus.
Genau das ist am 28.08.2026 an einer echten Bibliothek beinahe passiert.

Die zweite Haelfte betrifft das Schema selbst: Sonarr fuehrt **drei**
Dateischemata (normal, taeglich, Anime). Wer nur das normale setzt, laesst zwei
Drittel der Bibliothek unberuehrt - und merkt es nie, weil das Gesetzte ja
stimmt.
"""

from __future__ import annotations

import pytest

from app.services import benennung
from app.services import qualitaetsprofile as qp


class GespielteInstanz:
    """Eine Instanz, die sich Muster und Benennung merkt."""

    label = "Test-Arr"

    def __init__(self, formate=None, benennung_stand=None, verbindungen=None):
        self.formate = list(formate or [])
        self._benennung = dict(benennung_stand or {"id": 1})
        self._verbindungen = list(verbindungen or [])
        self.gespeichert: dict | None = None
        self.umbenannt: list[tuple[int, str]] = []

    async def custom_formats(self):
        return [dict(f) for f in self.formate]

    async def custom_format_nachziehen(self, format_id, payload):
        for f in self.formate:
            if f["id"] == format_id:
                self.umbenannt.append((format_id, payload["name"]))
                f["name"] = payload["name"]
                return f
        raise AssertionError(f"Muster {format_id} gibt es nicht")

    async def benennung(self):
        return dict(self._benennung)

    async def benennung_speichern(self, payload):
        self.gespeichert = dict(payload)
        self._benennung = dict(payload)
        return payload

    async def notifications(self):
        return list(self._verbindungen)


def _muster(nummer, name, im_dateinamen=True):
    return {
        "id": nummer,
        "name": name,
        "includeCustomFormatWhenRenaming": im_dateinamen,
        "specifications": [],
    }


# ---------------------------------------------------------------------------
# Die Lage erkennen
# ---------------------------------------------------------------------------


def test_nur_dateinamen_wirksame_muster_zaehlen():
    """⚠️ Ein Muster, das nie in einem Dateinamen auftaucht, ist kein Problem.

    Es mitzuzaehlen liesse die Lage schlimmer aussehen, als sie ist - und
    sperrte den Bestandslauf ohne Grund.
    """
    lage = qp._praefix_lage_aus([
        _muster(1, "NXV - German DL", True),
        _muster(2, "NXV - Unwichtig", False),
        _muster(3, "German", True),
    ])
    assert lage.gesamt == 2, "beide NXV-Muster zaehlen zur Gesamtzahl"
    assert lage.im_dateinamen == 1
    assert lage.blockiert == 0
    assert lage.beispiele == ["NXV - German DL"]


def test_blockiert_zaehlt_nur_was_wirklich_im_weg_steht():
    """Blockiert heisst: dateinamenwirksam **und** der schlichte Name ist belegt."""
    lage = qp._praefix_lage_aus([
        _muster(1, "NXV - German", True),
        _muster(2, "German", True),          # belegt den Namen -> blockiert
        _muster(3, "NXV - Repack2", True),   # frei -> umbenennbar
        _muster(4, "NXV - Egal", False),
        _muster(5, "Egal", True),            # belegt, aber nicht dateinamenwirksam
    ])
    assert lage.im_dateinamen == 2
    assert lage.blockiert == 1
    assert lage.blockierte_namen == ["NXV - German"]


def test_ohne_vorsatz_ist_die_lage_leer():
    lage = qp._praefix_lage_aus([_muster(1, "German DL"), _muster(2, "HDR")])
    assert (lage.gesamt, lage.im_dateinamen, lage.blockiert) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Aufraeumen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_aufraeumen_benennt_um_statt_neu_anzulegen():
    """⚠️ Die Nummer muss bleiben.

    Ein zweites Muster mit demselben Inhalt liesse das alte als Waise zurueck -
    es stuende weiter in jeder Profilmaske herum, und die Profile zeigten
    weiter darauf. Umbenennen behaelt die Nummer, also bleibt alles verbunden.
    """
    instanz = GespielteInstanz([
        _muster(11, "NXV - German DL"),
        _muster(12, "NXV - HDR"),
    ])
    anzahl = await qp.praefix_aufraeumen(instanz)
    assert anzahl == 2
    assert instanz.umbenannt == [(11, "German DL"), (12, "HDR")]
    assert {f["name"] for f in instanz.formate} == {"German DL", "HDR"}


@pytest.mark.anyio
async def test_fremde_muster_werden_nicht_ueberschrieben():
    """⚠️ Der schlichte Name ist belegt - dann bleibt alles, wie es war.

    Ein fremdes Muster zu ueberschreiben waere keine Aufraeumarbeit, sondern
    ein Eingriff in etwas, das Nexview nicht gehoert.
    """
    instanz = GespielteInstanz([
        _muster(1, "NXV - German"),
        _muster(2, "German"),
        _muster(3, "NXV - Repack2"),
    ])
    anzahl = await qp.praefix_aufraeumen(instanz)
    assert anzahl == 1, "nur das freie Muster darf umbenannt werden"
    assert instanz.umbenannt == [(3, "Repack2")]
    namen = {f["name"] for f in instanz.formate}
    assert "NXV - German" in namen and "German" in namen


@pytest.mark.anyio
async def test_aufraeumen_ohne_altnamen_tut_nichts():
    instanz = GespielteInstanz([_muster(1, "German DL")])
    assert await qp.praefix_aufraeumen(instanz) == 0
    assert instanz.umbenannt == []


@pytest.mark.anyio
async def test_zweimal_aufraeumen_ist_folgenlos():
    """Wiederholbarkeit: Der zweite Lauf darf nichts mehr finden."""
    instanz = GespielteInstanz([_muster(1, "NXV - German DL")])
    assert await qp.praefix_aufraeumen(instanz) == 1
    assert await qp.praefix_aufraeumen(instanz) == 0


# ---------------------------------------------------------------------------
# Das Schema setzen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sonarr_bekommt_alle_drei_dateischemata():
    """⚠️ Sonarr fuehrt normal, taeglich und Anime getrennt.

    Wer nur das normale setzt, laesst zwei Drittel der Bibliothek unberuehrt -
    und merkt es nie, weil das Gesetzte ja stimmt.
    """
    instanz = GespielteInstanz(benennung_stand={"id": 1, "renameEpisodes": False})
    await benennung.uebernehmen(instanz, "sonarr", datei=True, ordner=False, medienserver="plex")
    gespeichert = instanz.gespeichert
    assert gespeichert["standardEpisodeFormat"]
    assert gespeichert["dailyEpisodeFormat"], "taegliche Formate fehlen"
    assert gespeichert["animeEpisodeFormat"], "Anime-Formate fehlen"
    # ⚠️ Ohne diesen Schalter greift das Schema gar nicht.
    assert gespeichert["renameEpisodes"] is True


@pytest.mark.anyio
async def test_ordner_setzt_auch_das_staffelschema():
    instanz = GespielteInstanz(benennung_stand={"id": 1})
    await benennung.uebernehmen(instanz, "sonarr", datei=False, ordner=True, medienserver="")
    assert instanz.gespeichert["seriesFolderFormat"]
    assert instanz.gespeichert["seasonFolderFormat"], "Staffelordner fehlt"
    # Datei war nicht gewaehlt - also darf sie auch nicht gesetzt sein.
    assert "standardEpisodeFormat" not in instanz.gespeichert


@pytest.mark.anyio
async def test_unbekannte_felder_bleiben_erhalten():
    """⚠️ Gelesen, veraendert, zurueckgeschrieben - nie neu gebaut.

    Radarr und Sonarr fuehren je nach Fassung Felder, die Nexview nicht kennt
    (Doppelpunkt-Ersatz, Mehrteiler-Schema). Ein selbst zusammengestellter
    Datensatz loescht sie still.
    """
    instanz = GespielteInstanz(
        benennung_stand={"id": 1, "colonReplacementFormat": "dash", "eigenartig": 42}
    )
    await benennung.uebernehmen(instanz, "radarr", datei=True, ordner=False, medienserver="")
    assert instanz.gespeichert["colonReplacementFormat"] == "dash"
    assert instanz.gespeichert["eigenartig"] == 42


@pytest.mark.anyio
async def test_die_fassung_richtet_sich_nach_dem_medienserver():
    """Plex will die Kennung in geschweiften, Emby in eckigen Klammern."""
    fuer_plex = GespielteInstanz(benennung_stand={"id": 1})
    await benennung.uebernehmen(fuer_plex, "radarr", True, False, "plex")
    fuer_emby = GespielteInstanz(benennung_stand={"id": 1})
    await benennung.uebernehmen(fuer_emby, "radarr", True, False, "emby")
    plex_datei = fuer_plex.gespeichert["standardMovieFormat"]
    emby_datei = fuer_emby.gespeichert["standardMovieFormat"]
    assert "tmdb" in plex_datei and "tmdb" in emby_datei
    assert plex_datei != emby_datei, (
        "Plex und Emby erwarten die Kennung in verschiedenen Klammern - "
        "ein gemeinsames Schema koennte nur einem von beiden gerecht werden"
    )


@pytest.mark.anyio
async def test_ohne_medienserver_gilt_die_schlichte_fassung():
    """Ein Betreiber ohne Medienserver bekommt trotzdem ein Schema."""
    instanz = GespielteInstanz(benennung_stand={"id": 1})
    vorschlag = await benennung.vorschlag_fuer(instanz, "k", "Radarr", "radarr", "")
    assert vorschlag.datei_soll, "auch ohne Medienserver muss etwas empfohlen werden"
    assert vorschlag.fassung == "standard"


@pytest.mark.anyio
async def test_fehlende_medienserver_verbindung_wird_gemeldet():
    """⚠️ Ohne sie merkt der Medienserver vom Umbenennen erst spaeter etwas.

    Das gehoert **vor** den Knopf, nicht ins Kleingedruckte.
    """
    ohne = GespielteInstanz(benennung_stand={"id": 1}, verbindungen=[])
    mit = GespielteInstanz(
        benennung_stand={"id": 1},
        verbindungen=[{"implementation": "PlexServer", "onRename": True}],
    )
    assert (await benennung.vorschlag_fuer(ohne, "k", "R", "radarr", "")).meldet_medienserver is False
    assert (await benennung.vorschlag_fuer(mit, "k", "R", "radarr", "")).meldet_medienserver is True
