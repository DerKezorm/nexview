"""Bestand aufnehmen und aufraeumen.

⚠️ **Warum das genau geprueft gehoert.** Das ist der einzige Weg, auf dem
Nexview etwas loescht, das es nicht selbst angelegt hat. Die Berechtigung dazu
kommt allein aus der Auswahl des Betreibers - also muss die Pruefung stimmen,
die ihm sagt, was gefahrlos weg kann.

Der teuerste Irrtum waere ein Profil, das noch benutzt wird und trotzdem
verschwindet: Radarr haengt die betroffenen Medien dann an ein anderes Profil,
und niemand kann hinterher sagen, welche das waren.
"""

from __future__ import annotations

import pytest

from app.services import arr_bestand
from app.services.arr import ArrError


class GespieltesArr:
    """Eine Instanz mit Profilen, Mustern, Medien, Listen und Sammlungen."""

    label = "Test-Arr"

    def __init__(self, profile=None, formate=None, medien=None, listen=None,
                 sammlungen=None, verweigert=()):
        self._profile = list(profile or [])
        self._formate = list(formate or [])
        self._medien = list(medien or [])
        self._listen = list(listen or [])
        self._sammlungen = list(sammlungen or [])
        self.verweigert = set(verweigert)
        self.geloescht_profile: list[int] = []
        self.geloescht_muster: list[int] = []

    async def quality_profiles(self):
        return [dict(p) for p in self._profile]

    async def custom_formats(self):
        return [dict(f) for f in self._formate]

    async def get(self, pfad, params=None):
        if pfad in ("/movie", "/series"):
            return list(self._medien)
        if pfad == "/importlist":
            return list(self._listen)
        if pfad == "/collection":
            return list(self._sammlungen)
        return []

    async def quality_profile_loeschen(self, nummer):
        if nummer in self.verweigert:
            raise ArrError("nein", 500, code="arr_http_error")
        self.geloescht_profile.append(nummer)
        self._profile = [p for p in self._profile if p["id"] != nummer]

    async def custom_format_loeschen(self, nummer):
        if nummer in self.verweigert:
            raise ArrError("nein", 500, code="arr_http_error")
        self.geloescht_muster.append(nummer)
        self._formate = [f for f in self._formate if f["id"] != nummer]


def _profil(nummer, name, punkte=None):
    """Ein Profil; ``punkte`` bildet Musternamen auf Punkte ab."""
    return {
        "id": nummer,
        "name": name,
        "formatItems": [
            {"name": n, "score": p} for n, p in (punkte or {}).items()
        ],
    }


def _muster(nummer, name, im_dateinamen=False):
    return {"id": nummer, "name": name,
            "includeCustomFormatWhenRenaming": im_dateinamen}


# ---------------------------------------------------------------------------
# Aufnehmen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_alle_drei_bindungen_werden_gezaehlt():
    """⚠️ Medien, Importlisten **und** Sammlungen.

    Die Sammlung ist der Fall, an den niemand denkt - und genau sie hat einmal
    ein Loeschen blockiert, ohne dass Radarr sagte warum.
    """
    arr = GespieltesArr(
        profile=[_profil(1, "In Gebrauch"), _profil(2, "Frei")],
        medien=[{"qualityProfileId": 1}, {"qualityProfileId": 1}],
        listen=[{"qualityProfileId": 1}],
        sammlungen=[{"qualityProfileId": 1}, {"qualityProfileId": 1}, {"qualityProfileId": 1}],
    )
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    benutzt = next(p for p in bestand.profile if p.id == 1)
    frei = next(p for p in bestand.profile if p.id == 2)
    assert (benutzt.medien, benutzt.importlisten, benutzt.sammlungen) == (2, 1, 3)
    assert benutzt.loeschbar is False
    assert benutzt.grund() == "medien:2,importlisten:1,sammlungen:3"
    assert frei.loeschbar is True and frei.grund() == ""


@pytest.mark.anyio
async def test_unser_verlangt_nummer_und_namen():
    """⚠️ Die Nummer allein genuegt nicht.

    Nach dem Einspielen einer Sicherung auf einer anderen Instanz zeigt die
    gemerkte Nummer moeglicherweise auf ein fremdes Profil. Wer dann "unser"
    behauptet, laedt zum Ueberschreiben ein.
    """
    arr = GespieltesArr(profile=[_profil(1, "Wohnzimmer"), _profil(2, "Fremdes")])
    bestand = await arr_bestand.aufnehmen(
        arr, "radarr-standard", "radarr", {1: "Wohnzimmer", 2: "Wohnzimmer"}
    )
    assert next(p for p in bestand.profile if p.id == 1).unser is True
    # Nummer passt, Name nicht -> nicht unseres.
    assert next(p for p in bestand.profile if p.id == 2).unser is False


@pytest.mark.anyio
async def test_nur_bepunktete_muster_gelten_als_benutzt():
    """⚠️ ``formatItems`` fuehrt **jedes** Muster in **jedem** Profil auf.

    Die meisten mit 0 Punkten. Wer das als Benutzung zaehlt, haelt den ganzen
    Bestand fuer gebunden - und kann nie etwas aufraeumen.
    """
    arr = GespieltesArr(
        profile=[_profil(1, "P", {"Wichtig": 100, "Egal": 0})],
        formate=[_muster(10, "Wichtig"), _muster(11, "Egal")],
    )
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    wichtig = next(m for m in bestand.muster if m.name == "Wichtig")
    egal = next(m for m in bestand.muster if m.name == "Egal")
    assert wichtig.benutzt_von == ["P"] and wichtig.loeschbar is False
    assert egal.benutzt_von == [] and egal.loeschbar is True


@pytest.mark.anyio
async def test_sonarr_wird_nicht_nach_sammlungen_gefragt():
    """Sonarr kennt keine - die Frage waere sinnlos und der Fehler unnoetig."""
    arr = GespieltesArr(profile=[_profil(1, "P")], sammlungen=[{"qualityProfileId": 1}])
    bestand = await arr_bestand.aufnehmen(arr, "sonarr-standard", "sonarr", {})
    assert bestand.profile[0].sammlungen == 0


@pytest.mark.anyio
async def test_stumme_instanz_gibt_einen_leeren_bestand():
    """Kein Absturz und keine erfundenen Zahlen - nur "nicht erreichbar"."""

    class Stumm(GespieltesArr):
        async def quality_profiles(self):
            raise ArrError("weg", code="arr_unreachable")

    bestand = await arr_bestand.aufnehmen(Stumm(), "radarr-standard", "radarr", {})
    assert bestand.erreichbar is False
    assert bestand.profile == [] and bestand.muster == []


@pytest.mark.anyio
async def test_fehlende_importlisten_kippen_die_aufnahme_nicht():
    """Aeltere Fassungen antworten dort nicht - dann fehlt eine Zahl, nicht alles."""

    class OhneListen(GespieltesArr):
        async def get(self, pfad, params=None):
            if pfad == "/importlist":
                raise ArrError("kennt den Weg nicht", 404, code="arr_path_unknown")
            return await GespieltesArr.get(self, pfad, params)

    arr = OhneListen(profile=[_profil(1, "P")], medien=[{"qualityProfileId": 1}])
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    assert bestand.erreichbar is True
    assert bestand.profile[0].medien == 1
    assert bestand.profile[0].importlisten == 0


# ---------------------------------------------------------------------------
# Aufraeumen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_gebundenes_profil_wird_abgelehnt():
    """⚠️ Der teuerste Irrtum, deshalb der wichtigste Test.

    Verschwindet ein benutztes Profil, haengt Radarr die Medien an ein anderes -
    und niemand kann hinterher sagen, welche das waren.
    """
    arr = GespieltesArr(
        profile=[_profil(1, "In Gebrauch")],
        medien=[{"qualityProfileId": 1}] * 42,
    )
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [1], [])
    assert ergebnis.geloescht_profile == []
    assert ergebnis.abgelehnt == {"In Gebrauch": "medien:42"}
    assert arr.geloescht_profile == [], "es darf nichts hinausgegangen sein"


@pytest.mark.anyio
async def test_freies_profil_wird_geloescht():
    arr = GespieltesArr(profile=[_profil(1, "Frei")])
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [1], [])
    assert ergebnis.geloescht_profile == ["Frei"]
    assert arr.geloescht_profile == [1]


@pytest.mark.anyio
async def test_benutztes_muster_wird_abgelehnt():
    arr = GespieltesArr(
        profile=[_profil(1, "P", {"Wichtig": 100})], formate=[_muster(10, "Wichtig")]
    )
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [], [10])
    assert ergebnis.abgelehnt == {"Wichtig": "in_gebrauch"}
    assert arr.geloescht_muster == []


@pytest.mark.anyio
async def test_erst_profile_dann_muster():
    """⚠️ Die Reihenfolge entscheidet, ob in einem Zug aufgeraeumt werden kann.

    Ein Muster gilt als benutzt, solange ein Profil ihm Punkte gibt. Wer das
    Profil zuerst wegnimmt, kann danach auch sein Muster loeschen - andersherum
    bliebe es liegen und der Betreiber muesste zweimal aufraeumen.
    """
    arr = GespieltesArr(
        profile=[_profil(1, "Weg damit", {"Nur hier": 50})],
        formate=[_muster(10, "Nur hier")],
    )
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [1], [10])
    assert ergebnis.geloescht_profile == ["Weg damit"]
    assert ergebnis.geloescht_muster == ["Nur hier"], (
        "nach dem Profil ist das Muster frei - es muss mitgehen"
    )
    assert ergebnis.abgelehnt == {}


@pytest.mark.anyio
async def test_ein_muster_das_woanders_gebraucht_wird_bleibt():
    """Auch wenn ein Profil geloescht wird - ein zweites kann es noch brauchen."""
    arr = GespieltesArr(
        profile=[
            _profil(1, "Weg damit", {"Gemeinsam": 50}),
            _profil(2, "Bleibt", {"Gemeinsam": 50}),
        ],
        formate=[_muster(10, "Gemeinsam")],
        medien=[{"qualityProfileId": 2}],
    )
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [1], [10])
    assert ergebnis.geloescht_profile == ["Weg damit"]
    assert ergebnis.abgelehnt == {"Gemeinsam": "in_gebrauch"}
    assert arr.geloescht_muster == []


@pytest.mark.anyio
async def test_die_instanz_darf_das_letzte_wort_haben():
    """⚠️ Radarr weiss manchmal mehr - etwa eine Bindung, die wir nicht sehen.

    Dann gilt seine Antwort, und der Betreiber erfaehrt es, statt dass Nexview
    einen Erfolg meldet, den es nicht gab.
    """
    arr = GespieltesArr(profile=[_profil(1, "Sieht frei aus")], verweigert={1})
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [1], [])
    assert ergebnis.geloescht_profile == []
    assert ergebnis.abgelehnt == {"Sieht frei aus": "instanz_verweigert"}


@pytest.mark.anyio
async def test_unbekannte_nummern_werden_benannt():
    arr = GespieltesArr(profile=[_profil(1, "P")])
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [99], [98])
    assert ergebnis.abgelehnt == {"99": "unbekannt", "98": "unbekannt"}


@pytest.mark.anyio
async def test_leere_auswahl_tut_nichts():
    arr = GespieltesArr(profile=[_profil(1, "P")])
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [], [])
    assert (ergebnis.geloescht_profile, ergebnis.geloescht_muster) == ([], [])
    assert arr.geloescht_profile == [] and arr.geloescht_muster == []


@pytest.mark.anyio
async def test_das_loeschbare_steht_oben():
    """⚠️ Die Reihenfolge ist eine Aussage darueber, wofuer die Liste da ist.

    Dieser Bereich dient dem Aufraeumen - gehandelt wird auf das, was weg kann.
    Standen die gebundenen Eintraege oben, musste man bei 186 Mustern eine
    halbe Bildschirmseite scrollen, um ueberhaupt etwas anklicken zu koennen.
    """
    arr = GespieltesArr(
        profile=[_profil(1, "P", {"Benutzt": 100})],
        formate=[_muster(10, "Benutzt"), _muster(11, "Frei"), _muster(12, "Auch frei")],
    )
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    namen = [m.name for m in bestand.muster]
    assert namen == ["Auch frei", "Frei", "Benutzt"], (
        "erst das Loeschbare (alphabetisch), dann das Gebundene"
    )


@pytest.mark.anyio
async def test_profile_stehen_nach_gewicht():
    """Bei Profilen zaehlt umgekehrt, was am meisten traegt.

    Ein Profil mit 3929 Filmen ist die wichtigste Auskunft der Seite und
    gehoert nicht ans Ende.
    """
    arr = GespieltesArr(
        profile=[_profil(1, "Klein"), _profil(2, "Gross")],
        medien=[{"qualityProfileId": 2}] * 50 + [{"qualityProfileId": 1}],
    )
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    assert [p.name for p in bestand.profile] == ["Gross", "Klein"]


@pytest.mark.anyio
async def test_muster_aus_einem_bauplan_gelten_nicht_als_ungenutzt():
    """⚠️ Der Fehler, der den Betreiber in eine Schleife geschickt hat.

    Ein Bauplan bringt Muster mit, die er bewusst mit **null Punkten**
    bewertet - Streaming-Kennungen etwa. Ohne diese Unterscheidung standen sie
    als "ungenutzt" auf der Aufraeumliste, wurden geloescht, und das naechste
    Verteilen legte sie wieder an. Gemessen an einer echten Instanz: 15 von 22
    vermeintlich freien Mustern waren solche.
    """
    arr = GespieltesArr(
        profile=[_profil(1, "P", {"Bepunktet": 100})],
        formate=[_muster(10, "Bepunktet"), _muster(11, "Im Plan, null Punkte"),
                 _muster(12, "Echte Altlast")],
    )
    bestand = await arr_bestand.aufnehmen(
        arr, "radarr-standard", "radarr", {}, {"Bepunktet", "Im Plan, null Punkte"}
    )
    nach_name = {m.name: m for m in bestand.muster}
    assert nach_name["Im Plan, null Punkte"].gehoert_zu_plan is True
    assert nach_name["Im Plan, null Punkte"].loeschbar is False
    assert nach_name["Echte Altlast"].loeschbar is True, (
        "was zu keinem Plan gehoert und nichts bepunktet, darf weg"
    )


@pytest.mark.anyio
async def test_ein_bauplan_muster_wird_auch_beim_loeschen_geschuetzt():
    """⚠️ Nicht nur in der Anzeige.

    Ein veralteter Browser-Stand koennte eine Nummer schicken, die inzwischen
    zu einem Plan gehoert. Dann muss der Server sie abweisen - sonst loescht er
    etwas, das gleich wiederkommt.
    """
    arr = GespieltesArr(
        profile=[_profil(1, "P")], formate=[_muster(10, "Gehoert zum Plan")]
    )
    bestand = await arr_bestand.aufnehmen(
        arr, "radarr-standard", "radarr", {}, {"Gehoert zum Plan"}
    )
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [], [10])
    assert ergebnis.geloescht_muster == []
    assert ergebnis.abgelehnt == {"Gehoert zum Plan": "gehoert_zu_profil"}
    assert arr.geloescht_muster == []


# ---------------------------------------------------------------------------
# Medien umhaengen
# ---------------------------------------------------------------------------


class ArrMitEditor(GespieltesArr):
    """Zusaetzlich der Sammel-Endpunkt, mit dem Medien umgehaengt werden."""

    def __init__(self, *a, editor_scheitert_ab=None, **k):
        super().__init__(*a, **k)
        self.editor_scheitert_ab = editor_scheitert_ab
        self.aufrufe = 0

    async def put(self, pfad, payload):
        self.aufrufe += 1
        if (
            self.editor_scheitert_ab is not None
            and self.aufrufe > self.editor_scheitert_ab
        ):
            raise ArrError("weg", 500, code="arr_http_error")
        feld = "movieIds" if "movieIds" in payload else "seriesIds"
        ziel = payload["qualityProfileId"]
        for m in self._medien:
            if m.get("id") in payload[feld]:
                m["qualityProfileId"] = ziel
        return {}


@pytest.mark.anyio
async def test_umhaengen_verschiebt_keine_dateien():
    """⚠️ ``moveFiles: False`` ist Absicht.

    Es geht um die Zuordnung, nicht um die Platte. Alles andere waere ein
    Verschieben von Terabytes, und danach hat niemand gefragt.
    """
    arr = ArrMitEditor(medien=[{"id": 1, "qualityProfileId": 7}])
    gesehen = {}

    async def merken(pfad, payload):
        gesehen.update(payload)
        return await ArrMitEditor.put(arr, pfad, payload)

    arr.put = merken
    await arr_bestand.umhaengen(arr, "radarr", 7, 9)
    assert gesehen["moveFiles"] is False


@pytest.mark.anyio
async def test_umhaengen_erfasst_nur_das_quellprofil():
    arr = ArrMitEditor(
        medien=[
            {"id": 1, "qualityProfileId": 7},
            {"id": 2, "qualityProfileId": 7},
            {"id": 3, "qualityProfileId": 8},
        ]
    )
    ergebnis = await arr_bestand.umhaengen(arr, "radarr", 7, 9)
    assert ergebnis.umgehaengt == 2 and ergebnis.grund == ""
    assert [m["qualityProfileId"] for m in arr._medien] == [9, 9, 8]


@pytest.mark.anyio
async def test_umhaengen_ohne_treffer_ist_folgenlos():
    arr = ArrMitEditor(medien=[{"id": 1, "qualityProfileId": 8}])
    ergebnis = await arr_bestand.umhaengen(arr, "radarr", 7, 9)
    assert ergebnis.umgehaengt == 0
    assert arr.aufrufe == 0, "ohne Titel darf gar nichts hinausgehen"


@pytest.mark.anyio
async def test_umhaengen_laeuft_in_haeppchen():
    """Bei mehreren tausend Titeln waere ein einziger Aufruf zu lang."""
    viele = [
        {"id": n, "qualityProfileId": 7}
        for n in range(arr_bestand.UMHAENGE_HAEPPCHEN * 2 + 5)
    ]
    arr = ArrMitEditor(medien=viele)
    ergebnis = await arr_bestand.umhaengen(arr, "radarr", 7, 9)
    assert ergebnis.umgehaengt == len(viele)
    assert arr.aufrufe == 3, "zwei volle Haeppchen und ein Rest"


@pytest.mark.anyio
async def test_ein_abbruch_meldet_die_wahre_zahl():
    """⚠️ Kein "alles oder nichts" vortaeuschen.

    Was schon umgehaengt ist, bleibt es. Wer hier eine runde Zahl meldet oder
    einen Erfolg behauptet, laesst den Betreiber im Unklaren, wo er steht.
    """
    viele = [
        {"id": n, "qualityProfileId": 7}
        for n in range(arr_bestand.UMHAENGE_HAEPPCHEN * 3)
    ]
    arr = ArrMitEditor(medien=viele, editor_scheitert_ab=1)
    ergebnis = await arr_bestand.umhaengen(arr, "radarr", 7, 9)
    assert ergebnis.grund == "abgebrochen"
    assert ergebnis.umgehaengt == arr_bestand.UMHAENGE_HAEPPCHEN


@pytest.mark.anyio
async def test_sonarr_benutzt_das_richtige_feld():
    """Radarr kennt ``movieIds``, Sonarr ``seriesIds`` - vertauscht passiert nichts."""
    arr = ArrMitEditor(medien=[{"id": 1, "qualityProfileId": 7}])
    gesehen = {}

    async def merken(pfad, payload):
        gesehen["pfad"] = pfad
        gesehen.update(payload)
        return await ArrMitEditor.put(arr, pfad, payload)

    arr.put = merken
    await arr_bestand.umhaengen(arr, "sonarr", 7, 9)
    assert gesehen["pfad"] == "/series/editor"
    assert "seriesIds" in gesehen


@pytest.mark.anyio
async def test_stumme_instanz_haengt_nichts_um():
    class Stumm(ArrMitEditor):
        async def get(self, pfad, params=None):
            raise ArrError("weg", code="arr_unreachable")

    ergebnis = await arr_bestand.umhaengen(Stumm(), "radarr", 7, 9)
    assert ergebnis.grund == "unerreichbar" and ergebnis.umgehaengt == 0


# ---------------------------------------------------------------------------
# Was die Instanz ablehnt, darf Nexview nicht vergessen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_abgelehnte_profile_stehen_nicht_bei_den_geloeschten():
    """⚠️ **Die Nummern der wirklich geloeschten, nicht der angefragten.**

    Der Merkposten im Besitzbuch haengt an der Nummer, die das Profil auf der
    Instanz hat. Wer stattdessen nimmt, was *angefragt* war, vergisst auch die
    Profile, die die Instanz gar nicht loeschen wollte.
    """
    arr = GespieltesArr(
        profile=[_profil(1, "Geht weg"), _profil(2, "Bleibt")],
        verweigert={2},
    )
    bestand = await arr_bestand.aufnehmen(arr, "radarr-standard", "radarr", {})
    ergebnis = await arr_bestand.aufraeumen(arr, bestand, [1, 2], [])

    assert ergebnis.geloescht_profile == ["Geht weg"]
    assert ergebnis.geloescht_profil_ids == [1], "nur die Nummer, die wirklich weg ist"
    assert ergebnis.abgelehnt == {"Bleibt": "instanz_verweigert"}


REZEPT = {
    "name": "Pruefprofil", "typ": "radarr", "aufloesung": "1080p",
    "sofortNehmen": True, "quelle": "remux", "sprachen": ["de"],
    "sprachRollen": {"de": "pflicht"}, "mehrerePflicht": "alle",
    "hdr": "netz", "schlusspunkt": "trash",
}


def test_aufraeumen_vergisst_nur_was_die_instanz_hergegeben_hat(
    arr_client, monkeypatch
) -> None:
    """⚠️ **Der ganze Weg, ueber HTTP - denn der Fehler sass im Router.**

    Zwei Profile werden zum Aufraeumen ausgewaehlt, die Instanz gibt nur eines
    her (am zweiten haengt etwas). Vorher strich Nexview den Merkposten fuer
    **beide**: Das ueberlebende Profil stand danach weiter in Radarr, galt hier
    aber als fremd, und der Abgleich meldete dauerhaft "fehlt".
    """
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import QualitaetsprofilInstallation
    from app.routers import qualitaetsprofile as router

    stamm = "/api/settings/qualitaetsprofile"
    weg = arr_client.post(stamm, json={"name": "Weg", "dienst": "radarr", "rezept": REZEPT})
    bleibt = arr_client.post(
        stamm, json={"name": "Bleibt", "dienst": "radarr", "rezept": REZEPT}
    )
    assert weg.status_code in (200, 201), weg.text
    assert bleibt.status_code in (200, 201), bleibt.text

    with SessionLocal() as db:
        for profil_id, extern in ((weg.json()["id"], 1), (bleibt.json()["id"], 2)):
            db.add(
                QualitaetsprofilInstallation(
                    profil_id=profil_id, kennung="radarr-standard",
                    profil_id_extern=extern, fingerabdruck="x", trash_stand="2026-01-01",
                )
            )
        db.commit()

    arr = GespieltesArr(
        profile=[_profil(1, "Weg"), _profil(2, "Bleibt")],
        verweigert={2},
    )
    monkeypatch.setattr(router, "ArrClient", lambda *a, **k: arr)

    antwort = arr_client.post(
        f"{stamm}/bestand/radarr-standard/aufraeumen",
        json={"profil_ids": [1, 2], "muster_ids": []},
    )
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["geloescht_profile"] == ["Weg"]
    assert "Bleibt" in daten["abgelehnt"]

    with SessionLocal() as db:
        uebrig = {
            zeile.profil_id_extern
            for zeile in db.scalars(select(QualitaetsprofilInstallation))
        }
    assert uebrig == {2}, (
        "Der Merkposten des abgelehnten Profils muss stehenbleiben - "
        f"uebrig: {uebrig}"
    )
