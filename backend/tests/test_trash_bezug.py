"""Einen neuen TRaSH-Stand holen - und einen kaputten ablehnen.

⚠️ **Warum das der heikelste Weg im ganzen Bereich ist.** Hier ersetzt Nexview
seine eigene Datengrundlage durch etwas, das von aussen kommt. Geht dabei etwas
schief, faellt es nicht sofort auf: Der alte Stand ist weg, und der Schaden
zeigt sich erst, wenn der Betreiber Wochen spaeter ein Profil verteilen will.

Deshalb gilt: **erst pruefen, dann uebernehmen** - und im Zweifel den neuen
Stand ablehnen. Diese Tests belegen, dass das Netz haelt.

Die Pakete werden aus dem mitgelieferten Schnappschuss gebaut, also aus echten
Daten, und dann gezielt beschaedigt. Ein von Hand erfundenes Paket wuerde nur
beweisen, dass der Code mit erfundenen Daten umgehen kann.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from app.services.beschaffung.arr import trash, trash_bezug


def _paket(schnappschuesse: dict[str, dict], wurzel: str = "Guides-master") -> bytes:
    """Aus Schnappschuessen ein Tar-Paket bauen, wie GitHub es liefert.

    Der Aufbau muss stimmen: ``<wurzel>/docs/json/<dienst>/<bereich>/<datei>``.
    Alles andere ueberspringt ``_aus_paket``, ohne es auszupacken.
    """
    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w:gz") as tar:
        for dienst, daten in schnappschuesse.items():
            bereiche = {
                "cf": {
                    name: daten["formate"][tid]
                    for name, tid in daten.get("formate_nach_datei", {}).items()
                    if tid in daten["formate"]
                },
                "quality-profiles": daten.get("profile", {}),
                "cf-groups": daten.get("gruppen", {}),
                "quality-size": daten.get("groessen", {}),
                "naming": daten.get("namen", {}),
            }
            for bereich, dateien in bereiche.items():
                for name, inhalt in dateien.items():
                    roh = json.dumps(inhalt).encode()
                    info = tarfile.TarInfo(
                        f"{wurzel}/docs/json/{dienst}/{bereich}/{name}.json"
                    )
                    info.size = len(roh)
                    tar.addfile(info, io.BytesIO(roh))
        # Etwas, das nicht dazugehoert - muss uebersprungen werden.
        fremd = b"nur Text"
        info = tarfile.TarInfo(f"{wurzel}/README.md")
        info.size = len(fremd)
        tar.addfile(info, io.BytesIO(fremd))
    return puffer.getvalue()


@pytest.fixture
def echte_daten():
    """Die mitgelieferten Schnappschuesse - unveraendert."""
    return {
        "radarr": json.loads(json.dumps(trash.schnappschuss("radarr"))),
        "sonarr": json.loads(json.dumps(trash.schnappschuss("sonarr"))),
    }


REZEPT = {
    "name": "Pruefprofil", "typ": "radarr", "aufloesung": "1080p",
    "sofortNehmen": True, "quelle": "remux", "sprachen": ["de"],
    "sprachRollen": {"de": "pflicht"}, "mehrerePflicht": "alle",
    "hdr": "netz", "schlusspunkt": "trash",
}


# ---------------------------------------------------------------------------
# Das Paket auspacken
# ---------------------------------------------------------------------------


def test_ein_echtes_paket_wird_vollstaendig_gelesen(echte_daten):
    ergebnis = trash_bezug._aus_paket(_paket(echte_daten))
    for dienst in ("radarr", "sonarr"):
        assert ergebnis[dienst]["formate"], f"{dienst}: keine Muster gelesen"
        assert ergebnis[dienst]["profile"], f"{dienst}: keine Profile gelesen"
        assert ergebnis[dienst]["lizenz"] == "MIT"


def test_paket_ohne_muster_wird_abgelehnt(echte_daten):
    """⚠️ Ein leeres Paket darf nicht als "neuer Stand" durchgehen.

    Genau das passiert, wenn GitHub den Aufbau aendert: Der Download klappt,
    das Auspacken findet nichts - und ohne diese Pruefung laege danach ein
    leerer Schnappschuss da, mit dem sich kein einziges Profil bauen liesse.
    """
    leer = {d: {**v, "formate": {}, "formate_nach_datei": {}} for d, v in echte_daten.items()}
    with pytest.raises(trash_bezug.BezugFehler) as fehler:
        trash_bezug._aus_paket(_paket(leer))
    assert fehler.value.code == "trash_incomplete"


def test_falscher_aufbau_wird_nicht_ausgepackt(echte_daten):
    """Liegt ``docs/json`` woanders, findet sich nichts - und das gilt als leer."""
    with pytest.raises(trash_bezug.BezugFehler):
        trash_bezug._aus_paket(_paket(echte_daten, wurzel="ganz/wo/anders"))


# ---------------------------------------------------------------------------
# Pruefen, bevor uebernommen wird
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ein_stand_der_profile_zerstoert_wird_abgelehnt(
    echte_daten, monkeypatch, tmp_path
):
    """⚠️ **Der eigentliche Zweck der Pruefung.**

    Verschwindet ein Erkennungsmuster aus den Guides, laesst sich ein
    abgelegtes Profil nicht mehr bauen. Wird der Stand trotzdem uebernommen,
    ist der alte weg und der Schaden faellt erst beim naechsten Verteilen auf.
    """
    kaputt = json.loads(json.dumps(echte_daten))
    # Alle Muster des Radarr-Stands entfernen, die Profile aber lassen:
    # Das Paket ist formal vollstaendig, der Bauplan scheitert trotzdem.
    behalten = dict(list(kaputt["radarr"]["formate"].items())[:1])
    kaputt["radarr"]["formate"] = behalten
    kaputt["radarr"]["formate_nach_datei"] = {
        n: t for n, t in kaputt["radarr"]["formate_nach_datei"].items() if t in behalten
    }

    monkeypatch.setattr(trash_bezug, "ordner", lambda: tmp_path)
    monkeypatch.setattr(trash_bezug, "_herkunft_datei", lambda: tmp_path / "herkunft.json")
    async def _commit():
        return ("abc123", "2099-01-01T00:00:00Z")
    async def _holen():
        return _paket(kaputt)
    monkeypatch.setattr(trash_bezug, "neuester_commit", _commit)
    monkeypatch.setattr(trash_bezug, "_paket_holen", _holen)

    with pytest.raises(trash_bezug.BezugFehler) as fehler:
        await trash_bezug.holen_und_pruefen([("radarr", REZEPT)])
    assert fehler.value.code == "trash_breaks_profiles"

    # ⚠️ Und nichts davon darf auf der Platte gelandet sein.
    assert not list(tmp_path.glob("trash-*.json")), (
        "Ein abgelehnter Stand darf keine Datei hinterlassen"
    )
    assert not list(tmp_path.glob("*.neu")), "Auch keine halbe"


@pytest.mark.anyio
async def test_ein_guter_stand_wird_uebernommen(echte_daten, monkeypatch, tmp_path):
    """Der Erfolgsfall - inklusive Herkunft und geleertem Zwischenspeicher."""
    monkeypatch.setattr(trash_bezug, "ordner", lambda: tmp_path)
    monkeypatch.setattr(trash_bezug, "_herkunft_datei", lambda: tmp_path / "herkunft.json")
    async def _commit():
        return ("cafe1234" * 5, "2099-01-01T00:00:00Z")
    async def _holen():
        return _paket(echte_daten)
    monkeypatch.setattr(trash_bezug, "neuester_commit", _commit)
    monkeypatch.setattr(trash_bezug, "_paket_holen", _holen)

    herkunft = await trash_bezug.holen_und_pruefen([("radarr", REZEPT)])

    assert herkunft.commit.startswith("cafe1234")
    assert herkunft.mitgeliefert is False
    for dienst in ("radarr", "sonarr"):
        datei = tmp_path / f"trash-{dienst}.json"
        assert datei.exists(), f"{dienst} wurde nicht geschrieben"
        inhalt = json.loads(datei.read_text(encoding="utf-8"))
        # ⚠️ Der Stand muss mitgeschrieben sein - sonst laesst sich spaeter
        # nicht sagen, welche Fassung in einem Profil steckt.
        assert inhalt["stand"] == "2099-01-01"
        assert inhalt["commit"].startswith("cafe1234")
    assert not list(tmp_path.glob("*.neu")), "Die Zwischendateien gehoeren weg"


@pytest.mark.anyio
async def test_ohne_abgelegte_profile_wird_nichts_geprueft(
    echte_daten, monkeypatch, tmp_path
):
    """Wer noch kein Profil hat, soll trotzdem aktualisieren koennen."""
    monkeypatch.setattr(trash_bezug, "ordner", lambda: tmp_path)
    monkeypatch.setattr(trash_bezug, "_herkunft_datei", lambda: tmp_path / "herkunft.json")
    async def _commit():
        return ("d" * 40, "2099-02-02T00:00:00Z")
    async def _holen():
        return _paket(echte_daten)
    monkeypatch.setattr(trash_bezug, "neuester_commit", _commit)
    monkeypatch.setattr(trash_bezug, "_paket_holen", _holen)

    herkunft = await trash_bezug.holen_und_pruefen([])
    assert herkunft.commit_datum.startswith("2099-02-02")


@pytest.mark.anyio
async def test_ein_unbekannter_dienst_im_rezept_faellt_auf(
    echte_daten, monkeypatch, tmp_path
):
    monkeypatch.setattr(trash_bezug, "ordner", lambda: tmp_path)
    monkeypatch.setattr(trash_bezug, "_herkunft_datei", lambda: tmp_path / "herkunft.json")
    async def _commit():
        return ("e" * 40, "2099-03-03T00:00:00Z")
    async def _holen():
        return _paket(echte_daten)
    monkeypatch.setattr(trash_bezug, "neuester_commit", _commit)
    monkeypatch.setattr(trash_bezug, "_paket_holen", _holen)

    with pytest.raises(trash_bezug.BezugFehler) as fehler:
        await trash_bezug.holen_und_pruefen([("lidarr", REZEPT)])
    assert fehler.value.code == "trash_incomplete"


# ---------------------------------------------------------------------------
# Die Meldung "ein neuerer Stand ist verfuegbar"
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_nach_dem_holen_ist_kein_neuerer_stand_mehr_gemeldet(
    echte_daten, monkeypatch, tmp_path
):
    """⚠️ **Aus einem echten Fehler (13.09.2026).**

    Das taegliche Nachsehen hatte einen neueren Stand gefunden, der Betreiber
    holte ihn, und ueber dem gerade geholten Stand hiess es weiter "Ein neuerer
    Stand der Guides ist verfuegbar". Gemerkt war nur das Ergebnis des
    Nachsehens, und das lief erst am naechsten Tag wieder.
    """
    import asyncio

    monkeypatch.setattr(trash_bezug, "ordner", lambda: tmp_path)
    monkeypatch.setattr(trash_bezug, "_herkunft_datei", lambda: tmp_path / "herkunft.json")
    monkeypatch.setattr(trash_bezug, "_neues", dict(trash_bezug._neues))
    halt = asyncio.Event()

    async def _commit():
        # Ein Durchlauf genuegt, danach soll die Schleife enden.
        halt.set()
        return ("f" * 40, "2099-04-04T00:00:00Z")

    async def _holen():
        return _paket(echte_daten)

    monkeypatch.setattr(trash_bezug, "neuester_commit", _commit)
    monkeypatch.setattr(trash_bezug, "_paket_holen", _holen)

    await trash_bezug.run_forever(halt)
    assert trash_bezug.neues_bekannt()["vorhanden"] is True, "sonst prueft dieser Test nichts"

    await trash_bezug.holen_und_pruefen([("radarr", REZEPT)])
    assert trash_bezug.neues_bekannt()["vorhanden"] is False


# ---------------------------------------------------------------------------
# Regeln frueherer Staende
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_regeln_frueherer_staende_bleiben_bekannt(echte_daten, monkeypatch, tmp_path):
    """Nexview erkennt Muster wieder, die es aus einem frueheren Stand geschrieben hat.

    Gebraucht, um Regeln nachzuziehen, ohne Handarbeit zu ueberschreiben: Nur
    was genau den Regeln eines bekannten TRaSH-Stands entspricht, gilt als
    unveraendert. Dazu gehoeren der mitgelieferte Stand und jeder geholte, auch
    wenn inzwischen ein neuerer gilt.
    """
    import copy

    monkeypatch.setattr(trash_bezug, "ordner", lambda: tmp_path)
    monkeypatch.setattr(trash_bezug, "_herkunft_datei", lambda: tmp_path / "herkunft.json")
    monkeypatch.setattr(trash_bezug, "_neues", dict(trash_bezug._neues))
    kennung = echte_daten["radarr"]["formate_nach_datei"]["web-tier-03"]

    def abdruck(daten: dict) -> str:
        return trash.regelabdruck(daten["radarr"]["formate"][kennung]["specifications"])

    def stand_mit(schalter: str) -> dict:
        daten = copy.deepcopy(echte_daten)
        spez = daten["radarr"]["formate"][kennung]["specifications"][0]
        spez[schalter] = not spez.get(schalter)
        return daten

    stand_a, stand_b = stand_mit("negate"), stand_mit("required")
    for sha, daten in (("a" * 40, stand_a), ("b" * 40, stand_b)):

        async def _commit(sha=sha):
            return (sha, "2099-05-05T00:00:00Z")

        async def _holen(daten=daten):
            return _paket(daten)

        monkeypatch.setattr(trash_bezug, "neuester_commit", _commit)
        monkeypatch.setattr(trash_bezug, "_paket_holen", _holen)
        await trash_bezug.holen_und_pruefen([("radarr", REZEPT)])

    # Es gilt B. A ist weder mitgeliefert noch geltend und muss trotzdem bekannt sein.
    monkeypatch.setattr(trash, "schnappschuss", lambda dienst: stand_b[dienst])
    bekannt = trash_bezug.bekannte_regeln("radarr")["WEB Tier 03"]
    assert abdruck(stand_a) in bekannt
    assert abdruck(stand_b) in bekannt

    # ⚠️ Und ohne Gemerktes, wie bei einem Stand, der vor dieser Fassung geholt
    # wurde: Muster von damals tragen die mitgelieferten Regeln. Die muessen
    # trotzdem bekannt sein, sonst werden sie nie nachgezogen.
    ohne_gemerktes = tmp_path / "ohne-gemerktes"
    ohne_gemerktes.mkdir()
    monkeypatch.setattr(trash_bezug, "ordner", lambda: ohne_gemerktes)
    assert abdruck(echte_daten) in trash_bezug.bekannte_regeln("radarr")["WEB Tier 03"]
