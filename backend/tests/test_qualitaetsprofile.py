"""Qualitaetsprofile: ablegen, verteilen, loeschen.

Der Radarr-Teil wird attrappiert - geprueft wird, **was** Nexview schreiben
wuerde, nicht ob eine echte Instanz antwortet. Genau dort sassen die drei
Fallen, die beim Bauen Zeit gekostet haben.
"""

from __future__ import annotations

import pytest

from app.services.arr import ArrClient
from app.services.trash import TrashFehler, bauplan

REZEPT = {
    "name": "Wohnzimmer 4K",
    "typ": "radarr",
    "aufloesung": "2160p",
    "quelle": "encodes",
    "sofortNehmen": True,
    "sprachen": ["de", "en"],
    "sprachRollen": {"de": "pflicht", "en": "bevorzugt"},
    "mehrerePflicht": "alle",
    "hdr": "netz",
    "schlusspunkt": "trash",
}

SPRACHEN = [
    {"id": -1, "name": "Any"},
    {"id": 1, "name": "English"},
    {"id": 4, "name": "German"},
]

# So sieht der Bauplan einer echten Instanz aus - gekuerzt, aber mit der
# Eigenheit, auf die es ankommt: Radarr buendelt WEB-Stufen bereits selbst.
SCHEMA = {
    "items": [
        {
            "quality": {"id": 19, "name": "Bluray-2160p"},
            "items": [],
            "allowed": False,
        },
        {
            "name": "WEB 2160p",
            "id": 1003,
            "allowed": False,
            "items": [
                {"quality": {"id": 18, "name": "WEBDL-2160p"}, "items": [], "allowed": False},
                {"quality": {"id": 17, "name": "WEBRip-2160p"}, "items": [], "allowed": False},
            ],
        },
        {
            "quality": {"id": 7, "name": "Bluray-1080p"},
            "items": [],
            "allowed": False,
        },
        {
            "name": "WEB 1080p",
            "id": 1002,
            "allowed": False,
            "items": [
                {"quality": {"id": 3, "name": "WEBDL-1080p"}, "items": [], "allowed": False},
                {"quality": {"id": 15, "name": "WEBRip-1080p"}, "items": [], "allowed": False},
            ],
        },
        {"quality": {"id": 6, "name": "Bluray-720p"}, "items": [], "allowed": False},
        {
            "name": "WEB 720p",
            "id": 1001,
            "allowed": False,
            "items": [
                {"quality": {"id": 5, "name": "WEBDL-720p"}, "items": [], "allowed": False},
                {"quality": {"id": 14, "name": "WEBRip-720p"}, "items": [], "allowed": False},
            ],
        },
        {"quality": {"id": 31, "name": "Remux-2160p"}, "items": [], "allowed": False},
    ]
}


def _attrappe(monkeypatch, mitschrift: dict) -> None:
    """Radarr nachstellen und mitschreiben, was ankommt."""
    mitschrift.setdefault("formate", [])
    mitschrift.setdefault("profile", [])
    bestand: list[dict] = []
    zaehler = {"n": 0}

    async def custom_formats(self):
        return list(bestand)

    async def custom_format_anlegen(self, payload):
        zaehler["n"] += 1
        eintrag = {"id": zaehler["n"], **payload}
        bestand.append(eintrag)
        mitschrift["formate"].append(payload)
        return eintrag

    async def quality_profile_schema(self):
        import copy

        return copy.deepcopy(SCHEMA)

    async def quality_profile_anlegen(self, payload):
        mitschrift["profile"].append(payload)
        return {"id": 42, **payload}

    async def quality_profile_nachziehen(self, profil_id, payload):
        mitschrift["profile"].append(payload)
        return {"id": profil_id, **payload}

    async def sprachen(self):
        return list(SPRACHEN)

    for name, funktion in (
        ("custom_formats", custom_formats),
        ("custom_format_anlegen", custom_format_anlegen),
        ("quality_profile_schema", quality_profile_schema),
        ("quality_profile_anlegen", quality_profile_anlegen),
        ("quality_profile_nachziehen", quality_profile_nachziehen),
        ("sprachen", sprachen),
    ):
        monkeypatch.setattr(ArrClient, name, funktion)


def test_bauplan_nimmt_die_deutsche_familie() -> None:
    """Ist Deutsch dabei, gilt die deutsche Profilfamilie samt ihrer Punkte."""
    plan = bauplan(REZEPT, "radarr", {"de": 4, "en": 1})
    assert plan.basis == "german-uhd-bluray-web"
    assert plan.min_punkte == 10_000, "Pflichtsprache muss die Schwelle setzen"
    namen = {f.name: f.punkte for f in plan.formate}
    assert namen["German DL"] == 11_000
    assert namen["Sprache: English"] == 500, "Gern-Sprache nur als Zugabe"


def test_bauplan_sperrt_dolby_vision_ohne_rueckfall() -> None:
    """Das Sicherheitsnetz ist der Unterschied zwischen 'netz' und 'frei'."""
    mit = bauplan(REZEPT, "radarr", {"de": 4, "en": 1})
    ohne = bauplan({**REZEPT, "hdr": "frei"}, "radarr", {"de": 4, "en": 1})
    gesperrt = {f.name for f in mit.formate if f.punkte <= -10_000}
    assert "DV (w/o HDR fallback)" in gesperrt
    assert "DV (w/o HDR fallback)" not in {f.name for f in ohne.formate}


def test_bauplan_zieht_kleinere_aufloesungen_dazu() -> None:
    """'Erst nehmen, was da ist' heisst: alles in EINE Gruppe."""
    sofort = bauplan(REZEPT, "radarr", {"de": 4})
    warten = bauplan({**REZEPT, "sofortNehmen": False}, "radarr", {"de": 4})
    assert "Bluray-1080p" in sofort.merge
    assert "Bluray-1080p" not in warten.merge


def test_bauplan_meldet_unbekannte_kombination() -> None:
    with pytest.raises(TrashFehler):
        bauplan({**REZEPT, "aufloesung": "480p"}, "radarr", {"de": 4})


def test_jede_antwortkombination_laesst_sich_bauen() -> None:
    """Jede Kombination, die der Assistent zulaesst, muss durchgehen.

    ⚠️ **Dieser Test kam nach einem echten Fehlschlag dazu.** Franzoesisch fiel
    beim ersten Bau in eine Familie, fuer die es keinen Tabelleneintrag gab -
    der Nutzer bekam einen Fehler, statt ein Profil. Aufgefallen ist es nicht
    beim Testen, sondern in der Oberflaeche, weil die damalige Probe nur die
    Sprachen durchging, an die ich gedacht hatte. Jetzt geht sie alle durch.
    """
    from itertools import product

    from app.services.trash import SPRACHNAMEN

    nummern = {code: 100 + i for i, code in enumerate(SPRACHNAMEN)}
    for dienst, aufloesung, quelle, sofort, hdr, code in product(
        ("radarr", "sonarr"),
        ("1080p", "2160p"),
        ("encodes", "remux", "web"),
        (True, False),
        ("netz", "frei", "egal"),
        list(SPRACHNAMEN) + [None],
    ):
        rezept = {
            **REZEPT,
            "typ": dienst,
            "aufloesung": aufloesung,
            "quelle": quelle,
            "sofortNehmen": sofort,
            "hdr": hdr,
            "sprachen": [code] if code else [],
            "sprachRollen": {code: "pflicht"} if code else {},
        }
        plan = bauplan(rezept, dienst, nummern)
        assert plan.merge, f"{dienst}/{aufloesung}/{quelle}/{code} ohne Qualitaeten"


@pytest.mark.anyio
async def test_schreiben_ersetzt_die_web_gruppe(monkeypatch) -> None:
    """Falle 2: Radarrs eigene WEB-Gruppe darf nicht danebenstehen."""
    from app.services import qualitaetsprofile as dienst

    mitschrift: dict = {}
    _attrappe(monkeypatch, mitschrift)
    client = ArrClient("http://x", "k", "Radarr")
    plan = bauplan(REZEPT, "radarr", {"de": 4, "en": 1})
    ergebnis = await dienst.schreiben(client, plan)

    profil = mitschrift["profile"][-1]
    erlaubt = [i for i in profil["items"] if i.get("allowed")]
    assert len(erlaubt) == 1, "genau eine Gruppe, nicht mehrere"
    namen = {k["quality"]["name"] for k in erlaubt[0]["items"]}
    assert "WEBDL-2160p" in namen and "Bluray-2160p" in namen
    # Die vorgebaute Gruppe "WEB 2160p" darf nicht zusaetzlich auftauchen.
    assert not any(i.get("name") == "WEB 2160p" for i in profil["items"])
    assert profil["cutoff"] == erlaubt[0]["id"]
    assert ergebnis.profil_id_extern == 42


@pytest.mark.anyio
async def test_schreiben_fuehrt_alle_formate_auf(monkeypatch) -> None:
    """Falle 3: formatItems muss den ganzen Bestand nennen, nicht nur unsere."""
    from app.services import qualitaetsprofile as dienst

    mitschrift: dict = {}
    _attrappe(monkeypatch, mitschrift)
    client = ArrClient("http://x", "k", "Radarr")
    plan = bauplan(REZEPT, "radarr", {"de": 4, "en": 1})
    await dienst.schreiben(client, plan)

    profil = mitschrift["profile"][-1]
    assert len(profil["formatItems"]) == len(mitschrift["formate"])
    # ⚠️ **Ohne Praefix.** 82 der TRaSH-Formate erscheinen im Dateinamen; ein
    # Praefix stuende dort mitten in den Namen der Dateien ("[NXV - German DL]").
    assert all(not f["name"].startswith("NXV - ") for f in mitschrift["formate"])
    assert "German DL" in {f["name"] for f in mitschrift["formate"]}


@pytest.mark.anyio
async def test_felder_kommen_als_liste_an(monkeypatch) -> None:
    """Falle 1: TRaSH schreibt fields als Objekt, Radarr will eine Liste."""
    from app.services import qualitaetsprofile as dienst

    mitschrift: dict = {}
    _attrappe(monkeypatch, mitschrift)
    client = ArrClient("http://x", "k", "Radarr")
    await dienst.schreiben(client, bauplan(REZEPT, "radarr", {"de": 4}))

    for format_ in mitschrift["formate"]:
        for spez in format_["specifications"]:
            assert isinstance(spez["fields"], list), format_["name"]


@pytest.mark.anyio
async def test_alle_pflichtsprachen_brauchen_ein_gemeinsames_muster() -> None:
    """"Alle im selben Release" darf keine zweite Tuer offen lassen.

    ⚠️ Punkte sind additiv. Blieben die einzelnen Sprachmuster stehen, kaeme ein
    Release mit nur einer der Sprachen ueber Umwege doch ueber die Schwelle.
    """
    rezept = {
        **REZEPT,
        "sprachen": ["de", "es"],
        "sprachRollen": {"de": "pflicht", "es": "pflicht"},
        "mehrerePflicht": "alle",
    }
    plan = bauplan(rezept, "radarr", {"de": 4, "es": 3})
    gemeinsam = [f for f in plan.formate if f.name.startswith("Sprachen: ")]
    assert len(gemeinsam) == 1, "genau ein Muster fuer alle Sprachen zugleich"
    assert len(gemeinsam[0].spezifikationen) == 2
    assert all(s["required"] for s in gemeinsam[0].spezifikationen), "required = UND"
    assert plan.min_punkte == gemeinsam[0].punkte

    einzeln = {f.name: f.punkte for f in plan.formate if f is not gemeinsam[0]}
    assert einzeln["German DL"] == 0, "sonst genuegt Deutsch allein"
    assert einzeln["Sprache: Spanish"] == 0
    uebrige = sum(p for p in einzeln.values() if p > 0)
    assert uebrige < plan.min_punkte, (
        f"alle uebrigen Punkte zusammen ({uebrige}) duerfen die Schwelle "
        f"({plan.min_punkte}) nicht reissen"
    )


def test_bauplan_erfindet_keine_qualitaetsstufe() -> None:
    """Was die Instanz nicht kennt, darf nicht im Bauplan stehen.

    ⚠️ **Aus einem echten Fehler.** "Erst nehmen, was da ist" leitete kleinere
    Aufloesungen aus den vorhandenen ab und erzeugte bei einem Remux-Profil ein
    ``Remux-720p`` - das es in Radarr nicht gibt. Geschrieben wurde es nicht,
    der Bauplan behauptete es aber weiter, und der Abgleich meldete deshalb bei
    jedem Aufruf faelschlich "von dir angepasst".
    """
    rezept = {**REZEPT, "aufloesung": "1080p", "quelle": "remux", "sofortNehmen": True}
    ohne_pruefung = bauplan(rezept, "radarr", {"de": 4})
    assert "Remux-720p" in ohne_pruefung.merge, "sonst prueft dieser Test nichts"

    echte = {
        "Remux-1080p",
        "WEBDL-1080p",
        "WEBRip-1080p",
        "WEBDL-720p",
        "WEBRip-720p",
    }
    plan = bauplan(rezept, "radarr", {"de": 4}, echte)
    assert "Remux-720p" not in plan.merge
    assert set(plan.merge) <= echte
    assert any("Remux-720p" in h for h in plan.hinweise), "und es wird gesagt"


def _live_profil(plan, punkte_abweichung: dict | None = None) -> dict:
    """Ein Radarr-Profil so, wie es nach dem Schreiben dort steht."""
    from app.services.qualitaetsprofile import PRAEFIX

    abweichung = punkte_abweichung or {}
    return {
        "id": 42,
        # ⚠️ Der Name gehoert dazu: Nexview erkennt sein Profil an Nummer
        # **und** Name wieder - ein echtes Radarr-Profil hat immer einen.
        "name": "P",
        "minFormatScore": plan.min_punkte,
        "cutoffFormatScore": plan.schluss_punkte,
        "items": [
            {
                "name": "Nexview",
                "allowed": True,
                "items": [
                    {"quality": {"name": name}, "allowed": True} for name in plan.merge
                ],
            }
        ],
        "formatItems": [
            {
                "name": PRAEFIX + w.name,
                "score": abweichung.get(w.name, w.punkte),
            }
            for w in plan.formate
        ],
    }


@pytest.mark.anyio
async def test_abgleich_erkennt_die_vier_zustaende(monkeypatch) -> None:
    """Die Matrix aus "Quelle bewegt?" und "drueben gedreht?"."""
    from app.models import Qualitaetsprofil, QualitaetsprofilInstallation
    from app.services import qualitaetsprofile as dienst

    _attrappe(monkeypatch, {})
    client = ArrClient("http://x", "k", "Radarr")
    profil = Qualitaetsprofil(id=1, name="P", dienst="radarr", rezept=REZEPT)
    plan = bauplan(REZEPT, "radarr", {"de": 4, "en": 1})
    abdruck = dienst._fingerabdruck(plan)

    def abdruck_von(live: dict) -> str:
        """Was Nexview gespeichert haette, wenn es genau DAS geschrieben hat."""
        gestalt = dienst._gestalt_von_instanz(live, plan)
        return dienst._abdruck({k: v for k, v in gestalt.items() if k != "fremd"})

    unveraendert = _live_profil(plan)
    veraendert = _live_profil(plan, {"German DL": 99})
    # "Damals geschrieben" heisst: die Kopie passt zum gespeicherten Abdruck,
    # aber der Bauplan von heute sieht anders aus.
    alter_stand = _live_profil(plan, {"German DL": 7777})
    noch_ein_stand = _live_profil(plan, {"German DL": 5555})

    faelle = [
        ("aktuell", unveraendert, abdruck),
        ("angepasst", veraendert, abdruck),
        ("update", alter_stand, abdruck_von(alter_stand)),
        ("konflikt", veraendert, abdruck_von(noch_ein_stand)),
    ]
    for erwartet, live, gespeichert in faelle:
        eintrag = QualitaetsprofilInstallation(
            profil_id=1,
            kennung="radarr-standard",
            profil_id_extern=42,
            fingerabdruck=gespeichert,
        )
        stand = await dienst.vergleichen(client, profil, eintrag, [live])
        assert stand.stand == erwartet, f"erwartet {erwartet}, war {stand.stand}"

    # ⚠️ Und der Fall, der die Reihenfolge der Pruefung festlegt: Der
    # gespeicherte Abdruck ist veraltet, aber Kopie und heutiger Bauplan sind
    # sich einig. Dann ist nichts zu tun - "Konflikt" ueber einer leeren
    # Unterschiedstabelle waere Unsinn.
    einig = QualitaetsprofilInstallation(
        profil_id=1,
        kennung="radarr-standard",
        profil_id_extern=42,
        fingerabdruck="veraltet",
    )
    stand = await dienst.vergleichen(client, profil, einig, [unveraendert])
    assert stand.stand == "aktuell"
    assert stand.unterschiede == []

    # Und der fuenfte Fall: drueben geloescht.
    eintrag = QualitaetsprofilInstallation(
        profil_id=1, kennung="radarr-standard", profil_id_extern=42, fingerabdruck=abdruck
    )
    weg = await dienst.vergleichen(client, profil, eintrag, [])
    assert weg.stand == "fehlt"


@pytest.mark.anyio
async def test_abgleich_nennt_den_unterschied(monkeypatch) -> None:
    """Wer "von dir angepasst" liest, will wissen, was anders ist."""
    from app.models import Qualitaetsprofil, QualitaetsprofilInstallation
    from app.services import qualitaetsprofile as dienst

    _attrappe(monkeypatch, {})
    client = ArrClient("http://x", "k", "Radarr")
    profil = Qualitaetsprofil(id=1, name="P", dienst="radarr", rezept=REZEPT)
    plan = bauplan(REZEPT, "radarr", {"de": 4, "en": 1})
    live = _live_profil(plan, {"German DL": 99})
    live["minFormatScore"] = 5

    eintrag = QualitaetsprofilInstallation(
        profil_id=1,
        kennung="radarr-standard",
        profil_id_extern=42,
        fingerabdruck=dienst._fingerabdruck(plan),
    )
    stand = await dienst.vergleichen(client, profil, eintrag, [live])
    arten = {u.art for u in stand.unterschiede}
    assert "mindestpunkte" in arten
    punkte = [u for u in stand.unterschiede if u.art == "punkte"]
    assert any(u.was == "German DL" and u.ist == "99" for u in punkte)


@pytest.mark.anyio
async def test_neuer_stand_wird_vor_dem_uebernehmen_geprueft(monkeypatch, tmp_path) -> None:
    """Ein Stand, der ein vorhandenes Profil zerstoerte, wird abgelehnt.

    ⚠️ Und er muss sich ueberhaupt pruefen lassen: Beim ersten Bau trug der
    frisch geholte Stand seine Herkunft noch nicht, und die Pruefung scheiterte
    an einem Feld, das ich selbst vergessen hatte - mit einem 500er statt einer
    Aussage.
    """
    import json as _json

    from app.services import trash, trash_bezug

    echt = trash.schnappschuss("radarr")
    kaputt = _json.loads(_json.dumps(echt))
    # Ein Erkennungsmuster verschwindet, das dieses Profil wirklich braucht -
    # ein beliebiges zu loeschen wuerde nichts beweisen.
    weg = kaputt["formate_nach_datei"]["german-dl"]
    del kaputt["formate"][weg]

    async def commit():
        return "abc123", "2026-09-01T00:00:00Z"

    async def paket():
        return b""

    monkeypatch.setattr(trash_bezug, "neuester_commit", commit)
    monkeypatch.setattr(trash_bezug, "_paket_holen", paket)
    monkeypatch.setattr(
        trash_bezug,
        "_aus_paket",
        lambda _roh: {"radarr": kaputt, "sonarr": trash.schnappschuss("sonarr")},
    )
    monkeypatch.setattr(trash_bezug, "ordner", lambda: tmp_path / "trash")

    with pytest.raises(trash_bezug.BezugFehler) as fehler:
        await trash_bezug.holen_und_pruefen([("radarr", REZEPT)])
    assert fehler.value.code == "trash_breaks_profiles"
    assert not (tmp_path / "trash").exists(), "nichts geschrieben, wenn abgelehnt"

    # Und der gute Fall: unveraenderte Daten gehen durch und werden abgelegt.
    monkeypatch.setattr(
        trash_bezug,
        "_aus_paket",
        lambda _roh: {
            "radarr": _json.loads(_json.dumps(echt)),
            "sonarr": _json.loads(_json.dumps(trash.schnappschuss("sonarr"))),
        },
    )
    neu = await trash_bezug.holen_und_pruefen([("radarr", REZEPT)])
    assert neu.commit == "abc123"
    assert (tmp_path / "trash" / "trash-radarr.json").is_file()


@pytest.mark.anyio
async def test_benennung_setzt_nur_das_gewaehlte(monkeypatch) -> None:
    """Datei und Ordner sind getrennt - ihre Folgen sind es auch.

    ⚠️ Und der gelesene Datensatz wird **veraendert**, nicht ersetzt: Radarr
    fuehrt dort Felder, die wir nicht kennen (Doppelpunkt-Ersatz etwa). Ein
    selbst gebauter Datensatz loeschte sie still.
    """
    from app.services import benennung

    stand = {
        "id": 1,
        "renameMovies": False,
        "colonReplacementFormat": "smart",
        "standardMovieFormat": "meins",
        "movieFolderFormat": "mein Ordner (4K)",
    }
    geschrieben: dict = {}

    async def lesen(self):
        return dict(stand)

    async def speichern(self, payload):
        geschrieben.update(payload)
        stand.update(payload)
        return payload

    monkeypatch.setattr(ArrClient, "benennung", lesen)
    monkeypatch.setattr(ArrClient, "benennung_speichern", speichern)
    client = ArrClient("http://x", "k", "Radarr")

    await benennung.uebernehmen(client, "radarr", datei=True, ordner=False, medienserver="plex")
    assert geschrieben["standardMovieFormat"] != "meins", "Dateischema gesetzt"
    assert geschrieben["movieFolderFormat"] == "mein Ordner (4K)", (
        "Ordner NICHT angefasst - er war nicht gewaehlt"
    )
    assert geschrieben["renameMovies"] is True, "sonst greift das Schema gar nicht"
    assert geschrieben["colonReplacementFormat"] == "smart", (
        "unbekanntes Feld darf nicht verlorengehen"
    )


@pytest.mark.anyio
async def test_bestand_umbenennen_traegt_tausende(monkeypatch) -> None:
    """Eine Bibliothek mit mehreren tausend Titeln muss durchlaufen.

    ⚠️ Zwei Fallen stecken darin, beide aus der Beschraenkung von ``/rename``:
    Es nimmt **keine Liste**, also muss je Titel gefragt werden - nacheinander
    waere das bei 5000 Titeln eine Viertelstunde. Und Radarr meldet zu einem
    Auftrag **keinen Fortschritt**, also muss Nexview selbst zaehlen.
    """
    from app.services import benennung

    ANZAHL = 5000
    # Jeder zwanzigste Titel braucht einen neuen Namen.
    betroffen = {n for n in range(1, ANZAHL + 1) if n % 20 == 0}
    gefragt: list[int] = []
    auftraege: list[list[int]] = []

    async def liste(self, path, params=None):
        assert path == "/movie"
        return [{"id": n} for n in range(1, ANZAHL + 1)]

    async def vorschau(self, feld, nummer):
        gefragt.append(nummer)
        return [{"newPath": f"/neu/{nummer}.mkv"}] if nummer in betroffen else []

    async def befehl(self, name, **felder):
        auftraege.append(list(felder["movieIds"]))
        return {"id": len(auftraege)}

    async def stand(self, befehl_id):
        return {"status": "completed"}

    monkeypatch.setattr(ArrClient, "get", liste)
    monkeypatch.setattr(ArrClient, "umbenennen_vorschau", vorschau)
    monkeypatch.setattr(ArrClient, "befehl", befehl)
    monkeypatch.setattr(ArrClient, "befehl_stand", stand)

    melden = benennung.Umbenennstand()
    ergebnis = await benennung.bestand_umbenennen(
        ArrClient("http://x", "k", "Radarr"), "radarr", melden
    )

    assert len(gefragt) == ANZAHL, "jeder Titel wurde geprueft"
    assert ergebnis.betroffen == len(betroffen)
    # Nur die betroffenen gehen in Auftraege - der Rest wird nicht angefasst.
    assert sorted(n for a in auftraege for n in a) == sorted(betroffen)
    assert all(len(a) <= benennung.HAEPPCHEN for a in auftraege), "in Haeppchen"
    assert len(auftraege) > 1, "sonst gaebe es keinen Fortschritt zu zeigen"
    assert ergebnis.schritt == "fertig"
    assert ergebnis.erledigt == ergebnis.gesamt == len(betroffen)


@pytest.mark.anyio
async def test_ohne_aenderung_kein_auftrag(monkeypatch) -> None:
    """Wo nichts umzubenennen ist, geht auch kein Auftrag hinaus."""
    from app.services import benennung

    losgeschickt: list[str] = []

    async def liste(self, path, params=None):
        return [{"id": 1}, {"id": 2}]

    async def vorschau(self, feld, nummer):
        return []

    async def befehl(self, name, **felder):
        losgeschickt.append(name)
        return {"id": 1}

    monkeypatch.setattr(ArrClient, "get", liste)
    monkeypatch.setattr(ArrClient, "umbenennen_vorschau", vorschau)
    monkeypatch.setattr(ArrClient, "befehl", befehl)

    stand = await benennung.bestand_umbenennen(
        ArrClient("http://x", "k", "Radarr"), "radarr", benennung.Umbenennstand()
    )
    assert losgeschickt == [], "kein Auftrag ohne Grund"
    assert stand.betroffen == 0 and stand.schritt == "fertig"


@pytest.mark.anyio
async def test_verbindung_wird_erst_geprueft_dann_eingetragen(monkeypatch) -> None:
    """Was die Instanz nicht erreicht, wird nicht eingetragen.

    ⚠️ Dieselbe Falle wie beim Webhook: Die Adresse, unter der *Nexview* einen
    Medienserver erreicht, muss nicht die sein, unter der *Radarr* ihn erreicht.
    Ein Eintrag, der nie funktioniert hat, ist schlimmer als keiner.
    """
    from app.services.arr import ArrError
    from app.services import medienserver_verbindung as mv

    angelegt: list[dict] = []
    geprueft: list[dict] = []
    scheitern = {"ja": True}

    async def get(self, path, params=None):
        if path == "/notification/schema":
            return [
                {
                    "implementation": "MediaBrowser",
                    "configContract": "MediaBrowserSettings",
                    "supportsOnRename": True,
                    "supportsOnDownload": True,
                    "fields": [],
                }
            ]
        return []

    async def post(self, path, payload):
        if path == "/notification/test":
            geprueft.append(payload)
            if scheitern["ja"]:
                raise ArrError("Host is unreachable", code="arr_unreachable")
            return {}
        return {"id": 1}

    async def anlegen(self, payload):
        angelegt.append(payload)
        return {"id": 1}

    monkeypatch.setattr(ArrClient, "get", get)
    monkeypatch.setattr(ArrClient, "post", post)
    monkeypatch.setattr(ArrClient, "notification_anlegen", anlegen)
    client = ArrClient("http://x", "k", "Radarr")

    grund = await mv.herstellen(client, "jellyfin", "Bizzy", "http://10.0.0.5:8096", "s")
    assert grund == "unreachable"
    assert geprueft, "es wurde geprueft"
    assert angelegt == [], "und nichts eingetragen"

    scheitern["ja"] = False
    grund = await mv.herstellen(client, "jellyfin", "Bizzy", "http://10.0.0.5:8096", "s")
    assert grund == ""
    assert len(angelegt) == 1
    eintrag = angelegt[0]
    assert eintrag["implementation"] == "MediaBrowser"
    assert eintrag["onRename"] is True, "ohne dieses Ereignis waere alles umsonst"
    felder = {f["name"]: f["value"] for f in eintrag["fields"]}
    assert felder["host"] == "10.0.0.5" and felder["port"] == 8096
    assert felder["apiKey"] == "s" and felder["updateLibrary"] is True


@pytest.mark.anyio
async def test_ohne_schluessel_wird_nichts_versucht(monkeypatch) -> None:
    """Ohne API-Schluessel gar nicht erst anfragen."""
    from app.services import medienserver_verbindung as mv

    async def darf_nicht(self, *a, **k):
        raise AssertionError("es haette gar nicht gefragt werden duerfen")

    monkeypatch.setattr(ArrClient, "get", darf_nicht)
    grund = await mv.herstellen(
        ArrClient("http://x", "k", "R"), "jellyfin", "B", "http://1.2.3.4:8096", ""
    )
    assert grund == "kein_schluessel"


def test_benennung_liefert_immer_text() -> None:
    """Jede Kombination muss ein Schema liefern, kein Objekt.

    ⚠️ **Aus einem echten Fehlschlag.** Sonarr gliedert die Dateischemata eine
    Ebene tiefer als Radarr - nach Art (normal, taeglich, Anime) und darin noch
    einmal nach Fassung. Uebersehen heisst: Nexview schickt ein Objekt los, und
    der Aufruf endet mit einem 500er.
    """
    from itertools import product

    from app.services.benennung import empfehlung

    for dienst, medienserver in product(
        ("radarr", "sonarr"), ("plex", "emby", "jellyfin", "")
    ):
        datei, ordner, fassung = empfehlung(dienst, medienserver)
        for wert, was in ((datei, "Datei"), (ordner, "Ordner"), (fassung, "Fassung")):
            assert isinstance(wert, str), f"{dienst}/{medienserver}: {was} ist kein Text"
        assert datei and ordner, f"{dienst}/{medienserver} ohne Schema"


def test_benennung_waehlt_die_fassung_zum_medienserver() -> None:
    """Plex schreibt die Kennung in geschweifte, Jellyfin in eckige Klammern."""
    from app.services import benennung

    _datei_plex, ordner_plex, fassung_plex = benennung.empfehlung("radarr", "plex")
    _datei_jf, ordner_jf, fassung_jf = benennung.empfehlung("radarr", "jellyfin")
    _datei_ohne, ordner_ohne, _f = benennung.empfehlung("radarr", "")

    assert fassung_plex == "plex-tmdb" and fassung_jf == "jellyfin-tmdb"
    assert ordner_plex != ordner_jf, "sonst waere die Fassung wirkungslos"
    assert "tmdb" not in ordner_ohne, "ohne Medienserver keine Kennung im Ordner"


def test_fortschritt_bleibt_nicht_liegen() -> None:
    """Auch wenn unterwegs etwas schiefgeht, ist der Stand danach weg.

    ⚠️ Ein liegengebliebener Eintrag hiesse fuer die Oberflaeche "laeuft noch" -
    und zwar bis zum Neustart.
    """
    from app.services import qualitaetsprofile as dienst

    assert dienst.fortschritt(7) is None
    with dienst.fortschritt_fuehren(7) as stand:
        stand.instanz = "Radarr"
        assert dienst.fortschritt(7) is stand
    assert dienst.fortschritt(7) is None

    with pytest.raises(RuntimeError):
        with dienst.fortschritt_fuehren(7):
            raise RuntimeError("etwas ging schief")
    assert dienst.fortschritt(7) is None, "auch nach einem Fehler aufgeraeumt"


def test_ablage_und_loeschen(arr_client) -> None:
    """Ein Profil liegt in Nexview, bis es jemand wegnimmt."""
    antwort = arr_client.post(
        "/api/settings/qualitaetsprofile",
        json={"name": "Wohnzimmer 4K", "dienst": "radarr", "rezept": REZEPT},
    )
    assert antwort.status_code == 201, antwort.text
    profil_id = antwort.json()["id"]

    liste = arr_client.get("/api/settings/qualitaetsprofile")
    assert [p["name"] for p in liste.json()] == ["Wohnzimmer 4K"]
    assert liste.json()[0]["installationen"] == [], "frisch heisst: noch nirgends"

    weg = arr_client.delete(f"/api/settings/qualitaetsprofile/{profil_id}")
    assert weg.status_code == 204, weg.text
    assert arr_client.get("/api/settings/qualitaetsprofile").json() == []


def test_loeschen_meldet_unbekanntes_profil(arr_client) -> None:
    antwort = arr_client.delete("/api/settings/qualitaetsprofile/999")
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "quality_profile_unknown"


def test_verteilen_lehnt_fremde_instanz_ab(arr_client) -> None:
    """Eine Kennung, die es nicht gibt, wird benannt statt stillschweigend uebergangen."""
    profil_id = arr_client.post(
        "/api/settings/qualitaetsprofile",
        json={"name": "P", "dienst": "radarr", "rezept": REZEPT},
    ).json()["id"]
    antwort = arr_client.put(
        f"/api/settings/qualitaetsprofile/{profil_id}/instanzen",
        json={"kennungen": ["radarr-gibtsnicht"]},
    )
    assert antwort.status_code == 400, antwort.text
    assert antwort.json()["detail"]["code"] == "quality_instance_unknown"


def test_verteilen_schreibt_und_merkt_sich(arr_client, monkeypatch) -> None:
    """Nach dem Verteilen weiss Nexview, wo das Profil liegt."""
    mitschrift: dict = {}
    _attrappe(monkeypatch, mitschrift)
    profil_id = arr_client.post(
        "/api/settings/qualitaetsprofile",
        json={"name": "Wohnzimmer 4K", "dienst": "radarr", "rezept": REZEPT},
    ).json()["id"]

    antwort = arr_client.put(
        f"/api/settings/qualitaetsprofile/{profil_id}/instanzen",
        json={"kennungen": ["radarr-standard"]},
    )
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert [i["kennung"] for i in daten["installationen"]] == ["radarr-standard"]
    assert daten["formate_neu"] > 0

    # Und wieder herunternehmen - die Kopie drueben bleibt, der Eintrag geht.
    zurueck = arr_client.put(
        f"/api/settings/qualitaetsprofile/{profil_id}/instanzen",
        json={"kennungen": []},
    )
    assert zurueck.status_code == 200, zurueck.text
    assert zurueck.json()["installationen"] == []


def test_beide_schreibweisen_je_profil_erkannt() -> None:
    """Auf einer Instanz koennen alte und neue Musternamen nebeneinander liegen.

    ⚠️ **Der Fall, der Markus zweimal faelschlich beschuldigt hat.** Vor der
    Praefix-Umstellung schrieb Nexview "NXV - AMZN", danach "AMZN". Beide
    Schreibweisen liegen dann auf derselben Instanz, und weil Radarr und Sonarr
    eigene Muster gleichen Namens mitbringen, steht die jeweils andere mit 0
    daneben.

    Wer sich stur fuer eine Seite entscheidet, liest beim jeweils anderen
    Profil lauter Nullen und meldet "von dir angepasst", wo niemand etwas
    angefasst hat. Die Schreibweise gilt **je Profil**.
    """
    from app.services import qualitaetsprofile as dienst

    plan = bauplan(REZEPT, "radarr", {"de": 4, "en": 1})
    soll = {w.name: w.punkte for w in plan.formate}

    def live(vorsatz: str) -> dict:
        """Ein Profil, das NUR unter dieser Schreibweise bepunktet ist."""
        eintraege = [
            {"name": vorsatz + w.name, "score": w.punkte} for w in plan.formate
        ]
        # ... und die jeweils andere Schreibweise liegt mit 0 daneben.
        andere = "" if vorsatz else dienst.ALTER_PRAEFIX
        eintraege += [{"name": andere + w.name, "score": 0} for w in plan.formate]
        return {"items": [], "minFormatScore": plan.min_punkte,
                "cutoffFormatScore": plan.schluss_punkte, "formatItems": eintraege}

    for vorsatz, wie in ((dienst.ALTER_PRAEFIX, "alte"), ("", "neue")):
        gestalt = dienst._gestalt_von_instanz(live(vorsatz), plan)
        gelesen = dict(gestalt["formate"])
        assert gelesen == soll, f"{wie} Schreibweise falsch gelesen"
        # Und nichts davon darf als fremdes Muster durchgehen.
        assert gestalt["fremd"] == [], f"{wie} Schreibweise als fremd gewertet"


def test_gemischte_schreibweisen_in_einem_profil() -> None:
    """Auch **innerhalb** eines Profils koennen beide Schreibweisen vorkommen.

    ⚠️ Genau daran ist eine Mehrheitsregel je Profil gescheitert: Nach dem
    Aufraeumen trugen 66 Muster den schlichten Namen, vier aber weiter den
    alten - die, deren schlichter Name von einem fremden Muster belegt war.
    Die Mehrheit sagte "schlicht", und die vier wurden als entwertet gelesen.
    """
    from app.services import qualitaetsprofile as dienst

    plan = bauplan(REZEPT, "radarr", {"de": 4, "en": 1})
    soll = {w.name: w.punkte for w in plan.formate}

    eintraege = []
    for nummer, w in enumerate(plan.formate):
        # Jedes vierte Muster blieb beim alten Namen haengen.
        alt_geblieben = nummer % 4 == 0
        vorsatz = dienst.ALTER_PRAEFIX if alt_geblieben else ""
        eintraege.append({"name": vorsatz + w.name, "score": w.punkte})
        gegenstueck = "" if alt_geblieben else dienst.ALTER_PRAEFIX
        eintraege.append({"name": gegenstueck + w.name, "score": 0})

    gestalt = dienst._gestalt_von_instanz(
        {"items": [], "minFormatScore": plan.min_punkte,
         "cutoffFormatScore": plan.schluss_punkte, "formatItems": eintraege},
        plan,
    )
    assert dict(gestalt["formate"]) == soll
    assert gestalt["fremd"] == []


@pytest.mark.anyio
async def test_der_profilname_kommt_aus_der_ablage() -> None:
    """Nicht aus dem Rezept - sonst heisst es drueben wie die TRaSH-Vorlage.

    ⚠️ **Live aufgefallen.** ``bauplan`` nimmt den Namen aus ``rezept["name"]``
    und faellt sonst auf den Dateinamen der Vorlage zurueck
    ("german-hd-remux-web"). Damit gab es zwei Quellen fuer dieselbe Sache. Wo
    sie auseinanderliefen, stand in Radarr ein Name, den niemand vergeben hatte
    - und weil mehrere Rezepte auf dieselbe Vorlage zeigen, bekamen zwei
    Profile denselben. Das zweite scheiterte an Radarrs Eindeutigkeit.
    """
    from app.models import Qualitaetsprofil
    from app.services import qualitaetsprofile as dienst

    ohne_namen = {k: v for k, v in REZEPT.items() if k != "name"}
    profil = Qualitaetsprofil(
        id=1, name="Wohnzimmer 4K", dienst="radarr", rezept=ohne_namen
    )
    umgebung = dienst.Umgebung(
        sprachnummern={"de": 4, "en": 1},
        qualitaeten=["Remux-1080p", "Bluray-1080p", "WEBDL-1080p", "WEBRip-1080p",
                     "WEBDL-720p", "WEBRip-720p", "Bluray-720p"],
    )
    plan = await dienst.plan_fuer(
        ArrClient("http://x", "k", "Radarr"), profil, umgebung
    )
    assert plan.profilname == "Wohnzimmer 4K", (
        "Der Name aus der Ablage muss gewinnen - sonst heisst das Profil "
        "drueben nach der TRaSH-Vorlage"
    )
