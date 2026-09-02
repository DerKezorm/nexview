"""Die Ablage mitnehmen - und drueben wiedererkennen, was zu ihr gehoert.

⚠️ **Was hier auf dem Spiel steht.** Ohne Ablage haelt Nexview die 15
Streaming-Muster eines Radarr-Bauplans (AMZN, NF, DSNP …) fuer ungenutzt - sie
tragen dort null Punkte und dienen nur der Erkennung. Wer dann aufraeumt,
loescht Teile seiner eigenen Profile. Am 29.08.2026 an einer frischen
Installation gemessen: 17 statt 2 "ungenutzt".

⚠️ **Bei Sonarr faellt es nicht auf** - dieselben Muster geben dort 75 Punkte,
also erkennt jede Installation sie als benutzt. Wer nur mit Sonarr prueft, haelt
das Problem fuer nicht vorhanden. Deshalb steht dieser Unterschied hier als
eigener Test.
"""

from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.services import qualitaet_umzug as umzug
from app.services import qualitaetsprofile as dienst
from app.services import trash

REZEPT = {
    "name": "FHD - Deutsch - MidQ", "typ": "radarr", "aufloesung": "1080p",
    "sofortNehmen": True, "quelle": "encodes", "sprachen": ["de"],
    "sprachRollen": {"de": "pflicht"}, "mehrerePflicht": "alle",
    "hdr": "netz", "schlusspunkt": "trash",
}


@pytest.fixture
def db_session():
    """Eine Sitzung auf der (von ``clean_db`` geleerten) Testdatenbank."""
    with SessionLocal() as sitzung:
        yield sitzung


# --------------------------------------------------------------- Datei lesen

def test_datei_traegt_ihr_kennzeichen():
    """Ohne Kennzeichen liesse sich jede JSON-Datei hochladen."""
    datei = {"art": umzug.ART, "fassung": 1, "profile": [
        {"name": "P", "dienst": "radarr", "rezept": REZEPT, "lag_auf": ["radarr-standard"]},
    ]}
    [eins] = umzug.einlesen(datei)
    assert eins.name == "P" and eins.dienst == "radarr"
    assert eins.lag_auf == ["radarr-standard"]


@pytest.mark.parametrize(
    "roh,grund",
    [
        ("kein objekt", "kein_objekt"),
        ({"art": "etwas anderes", "fassung": 1, "profile": [{}]}, "falsche_art"),
        ({"art": umzug.ART, "fassung": 99, "profile": [{}]}, "zu_neu"),
        ({"art": umzug.ART, "fassung": 1, "profile": []}, "leer"),
        ({"art": umzug.ART, "fassung": 1, "profile": [{"name": "", "dienst": "radarr", "rezept": {}}]}, "kaputt"),
        ({"art": umzug.ART, "fassung": 1, "profile": [{"name": "P", "dienst": "plex", "rezept": {}}]}, "kaputt"),
    ],
)
def test_unbrauchbare_dateien_werden_benannt(roh, grund):
    """⚠️ Jeder Fehlerfall braucht einen eigenen Grund.

    Sonst bekommt der Betreiber einen Schluesselfehler aus der Tiefe und weiss
    nicht, ob die Datei falsch, alt oder kaputt ist.
    """
    with pytest.raises(ValueError) as fehler:
        umzug.einlesen(roh)
    assert str(fehler.value) == grund


def test_eine_aeltere_fassung_wird_gelesen():
    """Nur *neuere* Fassungen sind ein Problem, aeltere nicht."""
    datei = {"art": umzug.ART, "fassung": 0, "profile": [
        {"name": "P", "dienst": "sonarr", "rezept": REZEPT},
    ]}
    assert umzug.einlesen(datei)[0].dienst == "sonarr"


# --------------------------------------------------------------- Datei schreiben

def test_ausfuhr_traegt_keine_zugangsdaten(db_session):
    """⚠️ Die Datei soll weitergegeben werden koennen.

    Steht ein Schluessel darin, ist sie kein Profil mehr, sondern ein Leck.
    """
    dienst.anlegen(db_session, "FHD - Deutsch - MidQ", "radarr", REZEPT)
    db_session.commit()

    datei = umzug.ausfuehren(db_session)
    text = repr(datei).lower()
    for verboten in ("api_key", "apikey", "token", "passwor", "secret", "schluessel"):
        assert verboten not in text, f"{verboten!r} steht in der Ausfuhr"
    assert datei["art"] == umzug.ART
    assert [p["name"] for p in datei["profile"]] == ["FHD - Deutsch - MidQ"]


def test_ausfuhr_und_einlesen_passen_zusammen(db_session):
    """⚠️ Ein Rundlauf, sonst merkt niemand, wenn die Formate auseinanderlaufen."""
    dienst.anlegen(db_session, "A", "radarr", REZEPT)
    dienst.anlegen(db_session, "B", "sonarr", dict(REZEPT, typ="sonarr"))
    db_session.commit()

    zurueck = umzug.einlesen(umzug.ausfuehren(db_session))
    assert sorted((e.name, e.dienst) for e in zurueck) == [("A", "radarr"), ("B", "sonarr")]
    assert zurueck[0].rezept == REZEPT


def test_ausfuhr_nennt_die_orte_ohne_nummern(db_session):
    """⚠️ Nummern aus fremdem Radarr waeren schlimmer als keine.

    Sie zeigten auf irgendein Profil. Die Kennung genuegt als Hinweis - der
    Import sieht selbst nach.
    """
    profil = dienst.anlegen(db_session, "A", "radarr", REZEPT)
    dienst.merken(
        db_session, profil, "radarr-standard",
        dienst.Schreibergebnis(
            profil_id_extern=7, fingerabdruck="abc", trash_stand="x",
            formate_neu=0, formate_wiederverwendet=0, hinweise=(),
        ),
    )
    db_session.commit()

    [eins] = umzug.ausfuehren(db_session)["profile"]
    assert eins["lag_auf"] == ["radarr-standard"]
    assert "7" not in repr(eins["lag_auf"])
    assert "profil_id_extern" not in eins


# --------------------------------------------------------------- Der Kern

def test_streaming_muster_tragen_bei_radarr_null_punkte():
    """⚠️ Der Grund, warum es dieses ganze Werkzeug gibt.

    Bei Radarr kommen die Streaming-Kennungen ohne Gewicht mit - sie dienen der
    Erkennung. Ob so ein Muster zu einem Plan gehoert oder ein Ueberbleibsel
    ist, weiss danach allein die Ablage.
    """
    sprachen = {c: n for n, c in enumerate(trash.SPRACHNAMEN, 1)}
    qualitaeten = [
        "Remux-1080p", "Bluray-1080p", "WEBDL-1080p", "WEBRip-1080p",
        "Bluray-720p", "WEBDL-720p", "WEBRip-720p",
    ]
    plan = trash.bauplan(REZEPT, "radarr", sprachen, qualitaeten)
    ohne_punkte = [w.name for w in plan.formate if w.punkte == 0]
    assert ohne_punkte, "Radarr-Plaene bringen Muster mit null Punkten mit"
    assert "AMZN" in ohne_punkte


def test_bei_sonarr_faellt_es_nicht_auf():
    """⚠️ Dieselben Muster geben bei Sonarr Punkte - dort erkennt sie jeder.

    Wer das Problem nur an Sonarr prueft, haelt es faelschlich fuer geloest.
    """
    sprachen = {c: n for n, c in enumerate(trash.SPRACHNAMEN, 1)}
    qualitaeten = [
        "Remux-1080p", "Bluray-1080p", "WEBDL-1080p", "WEBRip-1080p",
        "Bluray-720p", "WEBDL-720p", "WEBRip-720p",
    ]
    plan = trash.bauplan(dict(REZEPT, typ="sonarr"), "sonarr", sprachen, qualitaeten)
    assert [w.name for w in plan.formate if w.punkte == 0] == []


# --------------------------------------------------------------- Uebernehmen

class GespielteInstanz:
    """Eine Instanz, die genau die genannten Profile fuehrt."""

    label = "Test-Arr"

    def __init__(self, profile=None, stumm=False):
        self._profile = list(profile or [])
        self.stumm = stumm

    async def quality_profiles(self):
        if self.stumm:
            raise RuntimeError("antwortet nicht")
        return [dict(p) for p in self._profile]


def _plan(name: str) -> trash.Bauplan:
    return trash.Bauplan(
        profilname=name, basis="x", stand="2026-08-29",
        formate=(), merge=(), min_punkte=0, schluss_punkte=100,
    )


@pytest.fixture
def ohne_echten_plan(monkeypatch):
    """Den Planbau ersetzen - er ist anderswo geprueft.

    ⚠️ Hier geht es um die **Uebernahme**, nicht um den Bauplan: ob die Nummer
    ins Besitzbuch kommt, welcher Abdruck festgehalten wird und was
    uebersprungen wird. Der echte Planbau braeuchte eine vollstaendig
    nachgebaute Instanz und pruefte davon nichts.
    """
    async def gespielt(client, profil, umgebung=None):
        return _plan(profil.name)

    monkeypatch.setattr(dienst, "plan_fuer", gespielt)
    return gespielt


def _datei(*profile):
    return [
        umzug.Ausfuhr(name=n, dienst=d, rezept=dict(REZEPT, name=n))
        for n, d in profile
    ]


@pytest.mark.anyio
async def test_vorgefundenes_profil_wird_uebernommen(db_session, ohne_echten_plan, monkeypatch):
    """⚠️ Der Kern: Nexview traegt die Nummer ins Besitzbuch - und schreibt nichts."""
    monkeypatch.setattr(dienst, "abweichungen", lambda live, plan: [])
    instanz = GespielteInstanz([{"id": 42, "name": "A"}])

    schau = await umzug.uebernehmen(
        db_session, _datei(("A", "radarr")),
        [("radarr-standard", "Radarr FHD", "radarr", instanz)],
    )

    assert schau.neu == ["A"]
    [befund] = schau.befunde
    assert befund.lage == "uebernehmen" and befund.profil_id_extern == 42
    profil = dienst.alle(db_session)[0]
    eintrag_ = dienst.installation(db_session, profil.id, "radarr-standard")
    assert eintrag_ is not None and eintrag_.profil_id_extern == 42


@pytest.mark.anyio
async def test_abweichendes_profil_wird_uebernommen_und_benannt(
    db_session, ohne_echten_plan, monkeypatch
):
    """⚠️ Uebernehmen, nicht angleichen - und der Abdruck entscheidet darueber.

    Festgehalten wird der Abdruck des **Bauplans**. Damit zeigt die Ablage
    danach "angepasst" ("jemand hat drueben nachgebessert") statt "update"
    ("die Quelle hat sich bewegt"). Mit dem Abdruck der vorgefundenen Kopie
    stuende dort das Gegenteil der Wahrheit.
    """
    monkeypatch.setattr(dienst, "abweichungen", lambda live, plan: ["etwas", "anderes"])
    instanz = GespielteInstanz([{"id": 7, "name": "A"}])

    schau = await umzug.uebernehmen(
        db_session, _datei(("A", "radarr")),
        [("radarr-standard", "Radarr FHD", "radarr", instanz)],
    )

    [befund] = schau.befunde
    assert befund.lage == "weicht_ab" and befund.unterschiede == 2
    eintrag_ = dienst.installation(
        db_session, dienst.alle(db_session)[0].id, "radarr-standard"
    )
    assert eintrag_.profil_id_extern == 7
    assert eintrag_.fingerabdruck == dienst.abdruck_von(_plan("A"))


@pytest.mark.anyio
async def test_bekannter_name_wird_uebersprungen(db_session, ohne_echten_plan):
    """⚠️ Zwei Rezepte unter einem Namen waeren ein Namensstreit auf der Instanz.

    Welches das richtige ist, weiss nur der Betreiber - also anfassen wir keins.
    """
    dienst.anlegen(db_session, "A", "radarr", REZEPT)
    db_session.commit()
    instanz = GespielteInstanz([{"id": 42, "name": "A"}])

    schau = await umzug.uebernehmen(
        db_session, _datei(("A", "radarr")),
        [("radarr-standard", "Radarr FHD", "radarr", instanz)],
    )

    assert schau.schon_da == ["A"] and schau.neu == []
    assert len(dienst.alle(db_session)) == 1


@pytest.mark.anyio
async def test_nicht_gefundenes_profil_landet_trotzdem_in_der_ablage(
    db_session, ohne_echten_plan
):
    """Ein Profil ohne Kopie drueben ist kein Fehler - es liegt eben nirgends."""
    instanz = GespielteInstanz([{"id": 1, "name": "etwas anderes"}])

    schau = await umzug.uebernehmen(
        db_session, _datei(("A", "radarr")),
        [("radarr-standard", "Radarr FHD", "radarr", instanz)],
    )

    assert schau.neu == ["A"] and schau.befunde[0].lage == "nicht_gefunden"
    profil = dienst.alle(db_session)[0]
    assert dienst.installation(db_session, profil.id, "radarr-standard") is None


@pytest.mark.anyio
async def test_stumme_instanz_kippt_den_import_nicht(db_session, ohne_echten_plan):
    """⚠️ Eine abgeschaltete Instanz darf den Umzug nicht verhindern.

    Sonst haengt das Wiederherstellen der Ablage daran, dass gerade alles
    laeuft - und genau beim Umzug laeuft selten alles.
    """
    schau = await umzug.uebernehmen(
        db_session, _datei(("A", "radarr")),
        [("radarr-standard", "Radarr FHD", "radarr", GespielteInstanz(stumm=True))],
    )

    assert schau.neu == ["A"] and schau.befunde[0].lage == "unerreichbar"
    assert len(dienst.alle(db_session)) == 1


@pytest.mark.anyio
async def test_sonarr_profil_wird_keiner_radarr_instanz_angeboten(
    db_session, ohne_echten_plan, monkeypatch
):
    """⚠️ Die Bausteine der beiden haben nichts gemeinsam - getrennte Kennungen.

    Ein gleichnamiges Profil drueben waere reiner Zufall, und es zu uebernehmen
    verknuepfte zwei Dinge, die nichts miteinander zu tun haben.
    """
    monkeypatch.setattr(dienst, "abweichungen", lambda live, plan: [])
    radarr = GespielteInstanz([{"id": 5, "name": "A"}])
    sonarr = GespielteInstanz([{"id": 9, "name": "A"}])

    schau = await umzug.uebernehmen(
        db_session, _datei(("A", "sonarr")),
        [("radarr-standard", "Radarr FHD", "radarr", radarr),
         ("sonarr-standard", "Sonarr FHD", "sonarr", sonarr)],
    )

    assert [b.kennung for b in schau.befunde] == ["sonarr-standard"]
    assert schau.befunde[0].profil_id_extern == 9


@pytest.mark.anyio
async def test_vorschau_aendert_nichts(db_session, ohne_echten_plan, monkeypatch):
    """⚠️ Vorschau vor Zugriff - und die Vorschau muss folgenlos sein.

    Sie sagt dasselbe wie der Import, legt aber nichts an.
    """
    monkeypatch.setattr(dienst, "abweichungen", lambda live, plan: [])
    instanz = GespielteInstanz([{"id": 42, "name": "A"}])
    instanzen = [("radarr-standard", "Radarr FHD", "radarr", instanz)]

    schau = await umzug.pruefen(db_session, _datei(("A", "radarr")), instanzen)

    assert schau.neu == ["A"] and schau.uebernommen == 1
    assert dienst.alle(db_session) == [], "die Vorschau darf nichts anlegen"


# --------------------------------------------------------------- Ueber HTTP

def test_ausfuhr_und_einfuhr_ueber_die_schnittstelle(admin_client):
    """⚠️ Der Rundlauf, wie ihn die Oberflaeche geht.

    Die Dienstschicht ist oben geprueft; hier geht es um die Naht: Kommt die
    Datei so heraus, wie der Import sie wieder annimmt?
    """
    admin_client.post(
        "/api/settings/qualitaetsprofile",
        json={"name": "FHD - Deutsch - MidQ", "dienst": "radarr", "rezept": REZEPT},
    ).raise_for_status()

    datei = admin_client.get("/api/settings/qualitaetsprofile/ausfuhr").json()
    assert datei["art"] == umzug.ART
    assert [p["name"] for p in datei["profile"]] == ["FHD - Deutsch - MidQ"]

    # Dasselbe wieder hinein: Der Name ist schon vergeben, also uebersprungen.
    antwort = admin_client.post(
        "/api/settings/qualitaetsprofile/einfuhr", json={"datei": datei}
    )
    assert antwort.status_code == 200
    assert antwort.json()["schon_da"] == ["FHD - Deutsch - MidQ"]
    assert antwort.json()["neu"] == []


@pytest.mark.parametrize(
    "datei,code",
    [
        ({"art": "etwas anderes", "fassung": 1, "profile": [{}]}, "quality_import_falsche_art"),
        ({"art": umzug.ART, "fassung": 99, "profile": [{}]}, "quality_import_zu_neu"),
        ({"art": umzug.ART, "fassung": 1, "profile": []}, "quality_import_leer"),
    ],
)
def test_unbrauchbare_datei_bekommt_ihre_kennung(admin_client, datei, code):
    """⚠️ Die Kennung entscheidet, welchen Satz die Oberflaeche zeigt.

    Ohne sie stuende dort die deutsche Serverfassung - auch bei englischer
    Einstellung.
    """
    antwort = admin_client.post(
        "/api/settings/qualitaetsprofile/einfuhr", json={"datei": datei}
    )
    assert antwort.status_code == 400
    assert antwort.json()["detail"]["code"] == code


def test_vorschau_legt_ueber_die_schnittstelle_nichts_an(admin_client):
    """Die Vorschau ist folgenlos - auch von aussen betrachtet."""
    datei = {
        "art": umzug.ART, "fassung": 1,
        "profile": [{"name": "Neu", "dienst": "radarr", "rezept": REZEPT}],
    }
    antwort = admin_client.post(
        "/api/settings/qualitaetsprofile/einfuhr/vorschau", json={"datei": datei}
    )
    assert antwort.status_code == 200 and antwort.json()["neu"] == ["Neu"]
    assert admin_client.get("/api/settings/qualitaetsprofile").json() == []
