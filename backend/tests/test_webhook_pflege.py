"""Die Pflege des Rueckkanals: anlegen, nachziehen, aufraeumen - und was tabu ist.

Die vier Grundsaetze aus dem Bauplan, hier festgenagelt:

* **Erst der Beweis, dann der Eintrag** - ohne angekommene Probe wird in
  Radarr/Sonarr nichts angelegt.
* **Fremde Eintraege sind tabu** - der Ruddarr-Fall ist live gesehen, nicht
  ausgedacht. Und ein fremder Eintrag, der zufaellig "Nexview" heisst,
  gehoert uns trotzdem nicht: Erst Name **und** Anruf-Adresse zaehlen.
* **Abwaehlen raeumt rueckstandsfrei auf.**
* **Faehigkeiten werden gemessen** - fehlt Pflichtwerk im Bauplan der
  Instanz, gilt sie als zu alt, mit Ansage.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from app.db import SessionLocal
from app.models import utcnow
from app.services.beschaffung.arr import webhook_pflege, webhooks
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_arr import SCHEMA_MOVIE, FakeArrRueckkanal

RADARR = {
    "radarr_url": "http://127.0.0.1:7878",
    "radarr_api_key": "schluessel-r",
    "public_url": "http://nexview.test",
}

RUDDARR = {
    "id": 1,
    "name": "Ruddarr",
    "implementation": "Webhook",
    "fields": [{"name": "url", "value": "https://ruddarr.com/webhook"}],
}


@pytest.fixture()
def fake(monkeypatch) -> FakeArrRueckkanal:
    fake = FakeArrRueckkanal()
    monkeypatch.setattr(webhook_pflege, "_client", lambda _instanz: fake)
    # Der Fehlerfall soll nicht fuenf echte Sekunden warten.
    monkeypatch.setattr(webhook_pflege, "BEWEIS_WARTEZEIT_SEKUNDEN", 0.6)
    return fake


def _radarr() -> tuple:
    with SessionLocal() as db:
        save_settings(db, RADARR)
        settings = load_settings(db)
    return settings, settings.arr_instanzen()[0]


def _zeile():
    with SessionLocal() as db:
        zeile = webhooks.eintrag(db, "radarr-standard")
        assert zeile is not None
        db.refresh(zeile)
        db.expunge(zeile)
        return zeile


@pytest.mark.anyio
async def test_anlegen_erst_nach_bestandenem_beweis(fake) -> None:
    settings, instanz = _radarr()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert len(fake.angelegt) == 1
    payload = fake.angelegt[0]
    url = next(f["value"] for f in payload["fields"] if f["name"] == "url")
    assert url == "http://nexview.test/api/webhooks/arr/radarr-standard"
    assert payload["onDownload"] and payload["onMovieDelete"]
    # Wuenschenswertes wird abonniert, wenn die Instanz es kann.
    assert payload["onGrab"] and payload["onHealthIssue"]
    zeile = _zeile()
    assert zeile.eintrag_id == 7
    assert zeile.fehler == ""


@pytest.mark.anyio
async def test_ohne_beweis_wird_nichts_angelegt(fake) -> None:
    """Die Kernregel: Kommt die Probe nie an, bleibt Radarr unangetastet -
    sonst stuende dort ein Eintrag, der bei jedem Ereignis fehlschlaegt."""
    fake.probe = "silent"
    settings, instanz = _radarr()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert fake.angelegt == []
    zeile = _zeile()
    assert zeile.fehler == "proof_failed"
    assert zeile.eintrag_id is None


@pytest.mark.anyio
async def test_fremde_eintraege_bleiben_unangetastet(fake) -> None:
    """Der Ruddarr-Fall: In echten Instanzen haengen fremde Webhooks."""
    fake.eintraege = [dict(RUDDARR)]
    settings, instanz = _radarr()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert fake.nachgezogen == [] and fake.geloescht == []
    assert any(e.get("name") == "Ruddarr" for e in fake.eintraege)
    assert len(fake.angelegt) == 1


@pytest.mark.anyio
async def test_der_name_allein_macht_keinen_eintrag_zu_unserem(fake) -> None:
    """"Nexview" kann jeder seinen Webhook nennen - erst Name UND unsere
    Anruf-Adresse zaehlen. Der fremde Namensvetter bleibt unberuehrt."""
    fake.eintraege = [
        {
            "id": 3,
            "name": "Nexview",
            "implementation": "Webhook",
            "fields": [{"name": "url", "value": "https://woanders.example/hook"}],
        }
    ]
    settings, instanz = _radarr()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert fake.nachgezogen == [] and fake.geloescht == []
    assert len(fake.angelegt) == 1
    assert len(fake.eintraege) == 2


#: Der Eintrag einer **anderen** Nexview an derselben Instanz: Bis 1.0.0 hiess
#: jede Nexview dort "Nexview", und der Pfad traegt dieselbe Instanz-Kennung.
#: Nur die Adresse unterscheidet ihn von unserem.
ANDERE_NEXVIEW = {
    "id": 1,
    "name": "Nexview",
    "implementation": "Webhook",
    "fields": [
        {
            "name": "url",
            "value": "http://andere-nexview.test/api/webhooks/arr/radarr-standard",
        }
    ],
}


def _url(eintrag: dict) -> str:
    return next(f["value"] for f in eintrag["fields"] if f["name"] == "url")


@pytest.mark.anyio
async def test_der_eintrag_einer_anderen_nexview_bleibt_unangetastet(fake) -> None:
    """Zwei Nexview an einem Radarr: Die zweite legt ihren eigenen Eintrag an,
    statt den der ersten auf sich umzubiegen - und scheitert dabei nicht am
    gleichen Namen (Radarr verlangt eindeutige Namen)."""
    fake.eintraege = [dict(ANDERE_NEXVIEW)]
    settings, instanz = _radarr()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert fake.nachgezogen == [] and fake.geloescht == []
    fremd = next(e for e in fake.eintraege if e["id"] == 1)
    assert _url(fremd) == _url(ANDERE_NEXVIEW)
    assert len(fake.angelegt) == 1
    assert fake.angelegt[0]["name"] == "Nexview (nexview.test)"
    assert _zeile().fehler == ""


@pytest.mark.anyio
async def test_abwaehlen_loescht_keinen_eintrag_einer_anderen_nexview(fake) -> None:
    """Die gemerkte Nummer zeigt auf einen Eintrag, den inzwischen eine andere
    Nexview beschrieben hat. Abwaehlen (und damit der Umstieg) darf ihn nicht
    loeschen: Die andere Nexview verloere still ihren Rueckkanal."""
    fake.eintraege = [dict(ANDERE_NEXVIEW)]
    settings, instanz = _radarr()
    with SessionLocal() as db:
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.aktiv = False
        zeile.eintrag_id = 1
        db.commit()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert fake.geloescht == []
    assert fake.eintraege == [ANDERE_NEXVIEW]
    assert _zeile().eintrag_id is None


@pytest.mark.anyio
async def test_umstieg_nimmt_nur_den_eigenen_eintrag_heraus(fake) -> None:
    """Der Umstieg verlaesst Radarr ueber dieselbe Pflege: Heraus geht unser
    Eintrag, der der anderen Nexview bleibt - auch wenn unsere gemerkte
    Nummer auf ihn zeigt."""
    from app.services.beschaffung.arr import konten

    eigener = {
        "id": 7,
        "name": "Nexview (nexview.test)",
        "implementation": "Webhook",
        "fields": [
            {"name": "url", "value": "http://nexview.test/api/webhooks/arr/radarr-standard"}
        ],
    }
    fake.eintraege = [dict(ANDERE_NEXVIEW), dict(eigener)]
    settings, _instanz = _radarr()
    with SessionLocal() as db:
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.eintrag_id = 1
        db.commit()

    with SessionLocal() as db:
        bericht = await konten.weg_verlassen(db, settings)

    assert fake.geloescht == [7]
    assert fake.eintraege == [ANDERE_NEXVIEW]
    assert any(a.code == "webhook_entfernt" for a in bericht)


def _als_installation(adresse: str, stand: dict | None) -> tuple:
    """Diese Testdatenbank spielt eine von zwei Nexview: eigene Adresse, eigener
    Webhook-Stand. Radarr (die Attrappe) teilen sich beide."""
    with SessionLocal() as db:
        save_settings(db, {**RADARR, "public_url": adresse})
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.aktiv = True
        zeile.eintrag_id = stand["eintrag_id"] if stand else None
        zeile.eintrag_url = stand["eintrag_url"] if stand else None
        db.commit()
        settings = load_settings(db)
    return settings, settings.arr_instanzen()[0]


def _stand_merken() -> dict:
    zeile = _zeile()
    return {"eintrag_id": zeile.eintrag_id, "eintrag_url": zeile.eintrag_url}


@pytest.mark.anyio
async def test_zwei_nexview_an_einem_radarr_bis_zum_umstieg_der_einen(fake) -> None:
    """Der Ablauf von oben mit beiden Seiten: A traegt sich ein, B traegt
    sich ein, beide pflegen im Wechsel (hier wurde frueher der Eintrag hin und
    her gebogen), dann steigt A um. Danach steht B's Eintrag unveraendert da."""
    from app.services.beschaffung.arr import konten

    settings_a, instanz_a = _als_installation("http://nexview-a.test", None)
    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings_a, instanz_a)
    stand_a = _stand_merken()

    settings_b, instanz_b = _als_installation("http://nexview-b.test", None)
    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings_b, instanz_b)
    stand_b = _stand_merken()

    assert sorted(e["name"] for e in fake.eintraege) == [
        "Nexview (nexview-a.test)",
        "Nexview (nexview-b.test)",
    ]
    assert stand_a["eintrag_id"] != stand_b["eintrag_id"]

    # Stuendliche Pflege auf beiden Seiten: Niemand zieht am Eintrag des anderen.
    for adresse, stand in (("http://nexview-a.test", stand_a), ("http://nexview-b.test", stand_b)):
        settings, instanz = _als_installation(adresse, stand)
        with SessionLocal() as db:
            await webhook_pflege.instanz_pflegen(db, settings, instanz)
    assert fake.nachgezogen == []

    settings_a, _ = _als_installation("http://nexview-a.test", stand_a)
    with SessionLocal() as db:
        await konten.weg_verlassen(db, settings_a)

    assert fake.geloescht == [stand_a["eintrag_id"]]
    assert [(_url(e), e["id"]) for e in fake.eintraege] == [
        ("http://nexview-b.test/api/webhooks/arr/radarr-standard", stand_b["eintrag_id"])
    ]


@pytest.mark.anyio
async def test_umstieg_sagt_ehrlich_wenn_der_eintrag_blieb(fake, monkeypatch) -> None:
    """Die Pflege faengt eine stumme Instanz selbst ab. Der Bericht des
    Umstiegs darf dann nicht "entfernt" sagen."""
    from app.services.beschaffung.arr import konten
    from app.services.beschaffung.arr.client import ArrError

    async def stumm() -> list[dict]:
        raise ArrError("Radarr ist nicht erreichbar", code="arr_unreachable")

    monkeypatch.setattr(fake, "notifications", stumm)
    settings, _instanz = _radarr()
    with SessionLocal() as db:
        webhooks.eintrag_sicherstellen(db, "radarr-standard")

    with SessionLocal() as db:
        bericht = await konten.weg_verlassen(db, settings)

    codes = [a.code for a in bericht]
    assert "webhook_blieb" in codes
    assert "webhook_entfernt" not in codes


@pytest.mark.anyio
async def test_unser_alter_eintrag_wird_uebernommen_und_umbenannt(fake) -> None:
    """Bestehende Installationen: Ihr Eintrag heisst noch "Nexview". Ruft er
    unsere Adresse an, ist er unserer - er bekommt den eindeutigen Namen,
    statt dass ein zweiter daneben entsteht."""
    fake.eintraege = [
        {
            "id": 4,
            "name": "Nexview",
            "implementation": "Webhook",
            "fields": [
                {"name": "url", "value": "http://nexview.test/api/webhooks/arr/radarr-standard"}
            ],
        }
    ]
    settings, instanz = _radarr()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert fake.angelegt == []
    assert [nummer for nummer, _ in fake.nachgezogen] == [4]
    assert fake.eintraege[0]["name"] == "Nexview (nexview.test)"
    zeile = _zeile()
    assert zeile.eintrag_id == 4
    assert zeile.eintrag_url == "http://nexview.test/api/webhooks/arr/radarr-standard"


@pytest.mark.anyio
async def test_testen_faehrt_nicht_mit_der_nummer_eines_fremden_eintrags(fake) -> None:
    """Ohne eigenen Eintrag geht die Probe unter unserem Namen und ohne Nummer
    hinaus. Mit der Nummer der anderen Nexview gaebe Radarr ihr Ergebnis als
    unseres aus."""
    fake.eintraege = [dict(ANDERE_NEXVIEW)]
    settings, instanz = _radarr()
    with SessionLocal() as db:
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.eintrag_id = 1
        db.commit()

    with SessionLocal() as db:
        ergebnis = await webhook_pflege.testen(db, settings, instanz)

    assert ergebnis["angekommen"] is True
    assert "id" not in fake.proben[-1]
    assert fake.proben[-1]["name"] == "Nexview (nexview.test)"


def test_zwei_nexview_heissen_verschieden() -> None:
    """Der Name traegt die Adresse, unter der die Installation angerufen wird."""
    namen = {
        webhook_pflege.eintrag_name("https://nexview.example.com/"),
        webhook_pflege.eintrag_name("http://192.0.2.5:8000"),
        webhook_pflege.eintrag_name("https://example.com/nexview"),
    }
    assert namen == {
        "Nexview (nexview.example.com)",
        "Nexview (192.0.2.5:8000)",
        "Nexview (example.com/nexview)",
    }


@pytest.mark.anyio
async def test_abwaehlen_raeumt_rueckstandsfrei_auf(fake) -> None:
    fake.eintraege = [
        dict(RUDDARR),
        {
            "id": 7,
            "name": "Nexview",
            "implementation": "Webhook",
            "fields": [
                {
                    "name": "url",
                    "value": "http://nexview.test/api/webhooks/arr/radarr-standard",
                }
            ],
        },
    ]
    settings, instanz = _radarr()
    with SessionLocal() as db:
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.aktiv = False
        zeile.eintrag_id = 7
        db.commit()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert fake.geloescht == [7]
    assert any(e.get("name") == "Ruddarr" for e in fake.eintraege)
    zeile = _zeile()
    assert zeile.eintrag_id is None and zeile.fehler == ""


ALT_URL = "http://alt.test/api/webhooks/arr/radarr-standard"


def _frueherer_eintrag() -> dict:
    return {
        "id": 7,
        "name": "Nexview (alt.test)",
        "implementation": "Webhook",
        "fields": [
            {"name": "url", "value": ALT_URL},
            {"name": "method", "value": 1},
            {"name": "username", "value": "nexview"},
        ],
        "onDownload": True,
        "onUpgrade": True,
        "onMovieDelete": True,
        "onMovieFileDelete": True,
        "onGrab": True,
        "onHealthIssue": True,
        "onHealthRestored": True,
        "onManualInteractionRequired": True,
    }


def _alte_adresse(monkeypatch, antwort) -> list[str]:
    """Was unter der frueheren Adresse antwortet: eine Antwort oder ein Fehler."""
    gefragt: list[str] = []

    async def holen(url: str) -> httpx.Response:
        gefragt.append(url)
        if isinstance(antwort, BaseException):
            raise antwort
        return antwort

    monkeypatch.setattr(webhook_pflege, "_gesundheit_holen", holen)
    return gefragt


def _abgewiesen() -> httpx.ConnectError:
    try:
        try:
            raise ConnectionRefusedError(111, "Connection refused")
        except ConnectionRefusedError as grund:
            raise httpx.ConnectError("All connection attempts failed") from grund
    except httpx.ConnectError as fehler:
        return fehler


async def _nach_adresswechsel(fake) -> None:
    """Unser Eintrag von frueher traegt die alte Adresse, public_url ist neu."""
    fake.eintraege = [_frueherer_eintrag()]
    # Radarr vergibt eine Nummer nur einmal; die Attrappe zaehlt sonst ab 7.
    fake._naechste_id = 20
    settings, instanz = _radarr()
    with SessionLocal() as db:
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.eintrag_id = 7
        zeile.eintrag_url = ALT_URL
        db.commit()
    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)


async def _noch_ein_lauf(*, vor_minuten: int | None = None) -> None:
    """Ein weiterer Pflegelauf. ``vor_minuten`` schiebt den ersten Befund
    "tot" so weit zurueck, als laege der erste Lauf so lange zurueck."""
    if vor_minuten is not None:
        with SessionLocal() as db:
            zeile = webhooks.eintrag(db, "radarr-standard")
            zeile.alte_adresse_tot_seit = utcnow().replace(tzinfo=None) - timedelta(
                minutes=vor_minuten
            )
            db.commit()
    settings, instanz = _radarr()
    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)


LEBT = httpx.Response(200, json={"status": "ok", "version": "1.0.0"})


@pytest.mark.anyio
@pytest.mark.parametrize(
    "antwort",
    [_abgewiesen(), httpx.Response(404, text="Not Found")],
    ids=["abgewiesen", "keine-nexview"],
)
async def test_nach_adresswechsel_mit_toter_alter_adresse_ist_der_alte_eintrag_weg(
    fake, monkeypatch, antwort
) -> None:
    """Unter der alten Adresse lauscht nichts mehr (Verbindung abgewiesen), oder
    dort gibt es kein ``/api/health`` - und zwar in zwei Laeufen mit mehr als
    einer halben Stunde Abstand: Der alte Eintrag geht, ein neuer mit unserer
    Adresse ist da. Umgeschrieben wird der alte nicht."""
    gefragt = _alte_adresse(monkeypatch, antwort)

    await _nach_adresswechsel(fake)
    assert fake.geloescht == [], "ein einziger Befund loescht noch nichts"
    assert _zeile().alte_adresse_tot_seit is not None

    await _noch_ein_lauf(vor_minuten=31)

    assert gefragt == ["http://alt.test/api/health"] * 2
    assert fake.nachgezogen == []
    assert fake.geloescht == [7]
    assert [(e["name"], _url(e)) for e in fake.eintraege] == [
        ("Nexview (nexview.test)", "http://nexview.test/api/webhooks/arr/radarr-standard")
    ]
    zeile = _zeile()
    assert zeile.alter_eintrag_id is None and zeile.eintrag_id == fake.eintraege[0]["id"]
    assert zeile.alte_adresse_tot_seit is None


@pytest.mark.anyio
async def test_zweimal_tot_ohne_abstand_bleibt_der_alte_eintrag(fake, monkeypatch) -> None:
    """Zwei Laeufe kurz hintereinander (der Haken, dann gleich der Rundgang)
    sind ein Befund, nicht zwei: Ein Neustart dauert laenger als das."""
    _alte_adresse(monkeypatch, _abgewiesen())

    await _nach_adresswechsel(fake)
    erster = _zeile().alte_adresse_tot_seit
    await _noch_ein_lauf(vor_minuten=5)

    assert fake.geloescht == []
    assert _frueherer_eintrag() in fake.eintraege
    assert _zeile().alter_eintrag_id == 7
    assert erster is not None


@pytest.mark.anyio
async def test_einmal_tot_dann_lebt_bleibt_der_alte_eintrag(fake, monkeypatch) -> None:
    """Die fremde Nexview startete nur neu (Docker-Update): Ihre Antwort
    dazwischen setzt die Zaehlung zurueck. Der naechste Befund "tot" ist
    wieder ein erster."""
    _alte_adresse(monkeypatch, _abgewiesen())
    await _nach_adresswechsel(fake)

    _alte_adresse(monkeypatch, LEBT)
    await _noch_ein_lauf(vor_minuten=31)
    assert _zeile().alte_adresse_tot_seit is None

    _alte_adresse(monkeypatch, _abgewiesen())
    await _noch_ein_lauf()

    assert fake.geloescht == []
    assert _frueherer_eintrag() in fake.eintraege
    zeile = _zeile()
    assert zeile.alter_eintrag_id == 7 and zeile.alte_adresse_tot_seit is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "antwort",
    [
        httpx.Response(200, json={"status": "ok", "version": "1.0.0"}),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("[Errno 11001] getaddrinfo failed"),
        httpx.Response(502, text="<html><title>502 Bad Gateway</title></html>"),
        httpx.Response(302, headers={"location": "https://login.example.com/"}),
    ],
    ids=["nexview", "zeitueberschreitung", "name-unbekannt", "proxy-502", "anmeldung"],
)
async def test_lebt_oder_ist_unklar_bleibt_der_alte_eintrag_stehen(
    fake, monkeypatch, antwort
) -> None:
    """Antwortet unter der alten Adresse eine Nexview, oder laesst es sich nicht
    klaeren: Der alte Eintrag bleibt unveraendert, wir bekommen einen eigenen,
    und die Diensteseite nennt den alten."""
    _alte_adresse(monkeypatch, antwort)

    await _nach_adresswechsel(fake)

    assert fake.nachgezogen == [] and fake.geloescht == []
    assert _frueherer_eintrag() in fake.eintraege
    assert len(fake.angelegt) == 1
    zeile = _zeile()
    assert zeile.alter_eintrag_id == 7 and zeile.alter_eintrag_url == ALT_URL


@pytest.mark.anyio
async def test_eine_kopie_des_datenverzeichnisses_biegt_den_eintrag_des_originals_nicht_um(
    fake, monkeypatch
) -> None:
    """B entsteht aus einer Kopie von A's Datenverzeichnis
    und bekommt eine eigene Adresse. B erbt Nummer und Adresse von A's Eintrag
    und schrieb ihn beim ersten Rundgang auf sich um; A verlor still den
    Rueckkanal. Jetzt bekommt B einen eigenen, und A's bleibt, wie er war."""
    _alte_adresse(monkeypatch, httpx.Response(200, json={"status": "ok", "version": "1.0.0"}))

    settings_a, instanz_a = _als_installation("http://nexview-a.test", None)
    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings_a, instanz_a)
    stand_a = _stand_merken()
    eintrag_a = dict(fake.eintraege[0])

    # B: dieselbe Datenbank, andere Adresse.
    settings_b, instanz_b = _als_installation("http://nexview-b.test", stand_a)
    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings_b, instanz_b)
    stand_b = _stand_merken()

    assert fake.nachgezogen == [] and fake.geloescht == []
    assert eintrag_a in fake.eintraege
    assert stand_b["eintrag_id"] != stand_a["eintrag_id"]
    assert sorted(e["name"] for e in fake.eintraege) == [
        "Nexview (nexview-a.test)",
        "Nexview (nexview-b.test)",
    ]

    # Auch B's Umstieg laesst A's Eintrag stehen.
    from app.services.beschaffung.arr import konten

    with SessionLocal() as db:
        await konten.weg_verlassen(db, settings_b)
    assert fake.eintraege == [eintrag_a]


def test_die_diensteseite_nennt_den_stehen_gelassenen_eintrag(arr_client) -> None:
    with SessionLocal() as db:
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.alter_eintrag_id = 7
        zeile.alter_eintrag_url = ALT_URL
        db.commit()

    antwort = arr_client.get("/api/settings/webhooks")

    zeile = next(z for z in antwort.json()["instanzen"] if z["kennung"] == "radarr-standard")
    assert zeile["alter_eintrag"] == ALT_URL


@pytest.mark.anyio
async def test_fehlende_pflicht_heisst_zu_alt(fake) -> None:
    schema = dict(SCHEMA_MOVIE)
    del schema["supportsOnMovieDelete"]
    fake.schema = schema
    settings, instanz = _radarr()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert fake.angelegt == []
    zeile = _zeile()
    assert zeile.fehler == "too_old"
    assert "onMovieDelete" in zeile.fehler_info


@pytest.mark.anyio
async def test_ohne_adresse_ehrlich_statt_geraten(fake) -> None:
    with SessionLocal() as db:
        save_settings(db, {**RADARR, "public_url": ""})
        settings = load_settings(db)
    instanz = settings.arr_instanzen()[0]

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert fake.angelegt == []
    assert _zeile().fehler == "no_address"


@pytest.mark.anyio
async def test_testen_meldet_angekommen_mit_dauer(fake) -> None:
    settings, instanz = _radarr()

    with SessionLocal() as db:
        ergebnis = await webhook_pflege.testen(db, settings, instanz)

    assert ergebnis["angekommen"] is True
    assert isinstance(ergebnis["dauer_ms"], int)
    # Ohne bestehenden Eintrag faehrt keine Nummer mit.
    assert "id" not in fake.proben[-1]


@pytest.mark.anyio
async def test_testen_sagt_ehrlich_wenn_nichts_ankommt(fake) -> None:
    fake.probe = "silent"
    settings, instanz = _radarr()

    with SessionLocal() as db:
        ergebnis = await webhook_pflege.testen(db, settings, instanz)

    assert ergebnis == {"angekommen": False, "fehler": "proof_failed"}


@pytest.mark.anyio
async def test_testen_faehrt_mit_der_nummer_des_bestehenden_eintrags(fake) -> None:
    """Sonarr prueft die Probe wie ein Speichern: Ohne Nummer gilt der
    gleichnamige Bestand als Duplikat (HTTP 400) - live so gesehen, nachdem
    der erste Beweis laengst stand."""
    fake.eintraege = [
        {
            "id": 7,
            "name": "Nexview",
            "implementation": "Webhook",
            "fields": [
                {
                    "name": "url",
                    "value": "http://nexview.test/api/webhooks/arr/radarr-standard",
                }
            ],
        }
    ]
    settings, instanz = _radarr()

    with SessionLocal() as db:
        ergebnis = await webhook_pflege.testen(db, settings, instanz)

    assert ergebnis["angekommen"] is True
    assert fake.proben[-1].get("id") == 7


def test_haken_endpunkt_speichert_und_meldet_ehrlich(arr_client) -> None:
    """PATCH legt den Haken um und versucht die Tat sofort - die Instanz auf
    Port 9 lehnt Verbindungen ab, also steht danach ehrlich "unreachable"."""
    antwort = arr_client.patch(
        "/api/settings/webhooks/radarr-standard", json={"aktiv": False}
    )
    assert antwort.status_code == 200, antwort.text
    zeile = next(
        z for z in antwort.json()["instanzen"] if z["kennung"] == "radarr-standard"
    )
    assert zeile["aktiv"] is False
    assert zeile["fehler"] == "unreachable"


def test_haken_endpunkt_kennt_nur_eingerichtete(arr_client) -> None:
    antwort = arr_client.patch(
        "/api/settings/webhooks/radarr-uhd", json={"aktiv": False}
    )
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "webhook_unknown_instance"


def test_stand_zeigt_vorgabe_an(arr_client) -> None:
    """Ohne jede Pflege gilt der Haken als gesetzt - Vorgabe an."""
    antwort = arr_client.get("/api/settings/webhooks")
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    kennungen = {z["kennung"]: z for z in daten["instanzen"]}
    assert set(kennungen) == {"radarr-standard", "sonarr-standard"}
    assert all(z["aktiv"] for z in kennungen.values())
    assert all(not z["eingetragen"] for z in kennungen.values())
