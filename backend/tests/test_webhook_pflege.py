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

import pytest

from app.db import SessionLocal
from app.models import utcnow
from app.services import webhook_pflege, webhooks
from app.services.arr import ArrError
from app.services.settings_service import load_settings, save_settings

RADARR = {
    "radarr_url": "http://127.0.0.1:7878",
    "radarr_api_key": "schluessel-r",
    "public_url": "http://nexview.test",
}

SCHEMA_MOVIE = {
    "implementation": "Webhook",
    "supportsOnDownload": True,
    "supportsOnUpgrade": True,
    "supportsOnMovieDelete": True,
    "supportsOnMovieFileDelete": True,
    "supportsOnGrab": True,
    "supportsOnHealthIssue": True,
    "supportsOnHealthRestored": True,
    "supportsOnManualInteractionRequired": True,
}

RUDDARR = {
    "id": 1,
    "name": "Ruddarr",
    "implementation": "Webhook",
    "fields": [{"name": "url", "value": "https://ruddarr.com/webhook"}],
}


class FakeArr:
    """Radarr in klein: merkt sich Eintraege und was mit ihnen geschah."""

    def __init__(self, kennung: str = "radarr-standard") -> None:
        self.kennung = kennung
        self.eintraege: list[dict] = []
        self.schema: dict | None = dict(SCHEMA_MOVIE)
        # "arrives" | "silent" | eine ArrError-Instanz
        self.probe = "arrives"
        # Jede Probe-Payload, wie sie bei der Instanz ankaeme.
        self.proben: list[dict] = []
        self.angelegt: list[dict] = []
        self.nachgezogen: list[tuple[int, dict]] = []
        self.geloescht: list[int] = []
        self._naechste_id = 7

    async def notifications(self) -> list[dict]:
        return [dict(eintrag) for eintrag in self.eintraege]

    async def notification_schema_webhook(self) -> dict | None:
        return self.schema

    async def notification_probe(self, payload: dict) -> None:
        self.proben.append(dict(payload))
        if isinstance(self.probe, ArrError):
            raise self.probe
        if self.probe == "arrives":
            # Was im Betrieb der Empfaenger tut, wenn Sonarrs Test ankommt -
            # in einer eigenen Sitzung, wie im echten Leben.
            with SessionLocal() as db:
                zeile = webhooks.eintrag(db, self.kennung)
                zeile.bewiesen_am = utcnow()
                zeile.zuletzt_angerufen_am = utcnow()
                zeile.letztes_ereignis = "Test"
                db.commit()

    async def notification_anlegen(self, payload: dict) -> dict:
        self.angelegt.append(payload)
        eintrag = {**payload, "id": self._naechste_id}
        self.eintraege.append(eintrag)
        return eintrag

    async def notification_nachziehen(self, eintrag_id: int, payload: dict) -> dict:
        self.nachgezogen.append((eintrag_id, payload))
        return {**payload, "id": eintrag_id}

    async def notification_loeschen(self, eintrag_id: int) -> None:
        self.geloescht.append(eintrag_id)
        self.eintraege = [e for e in self.eintraege if e.get("id") != eintrag_id]


@pytest.fixture()
def fake(monkeypatch) -> FakeArr:
    fake = FakeArr()
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


@pytest.mark.anyio
async def test_abweichender_eintrag_wird_nachgezogen(fake) -> None:
    """Alte Adresse im Eintrag (public_url hat sich geaendert): Die Pflege
    erkennt unseren Eintrag an der gemerkten Nummer und zieht ihn nach."""
    fake.eintraege = [
        {
            "id": 7,
            "name": "Nexview",
            "implementation": "Webhook",
            "fields": [
                {"name": "url", "value": "http://alt.test/api/webhooks/arr/radarr-standard"},
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
    ]
    settings, instanz = _radarr()
    with SessionLocal() as db:
        zeile = webhooks.eintrag_sicherstellen(db, "radarr-standard")
        zeile.eintrag_id = 7
        db.commit()

    with SessionLocal() as db:
        await webhook_pflege.instanz_pflegen(db, settings, instanz)

    assert len(fake.nachgezogen) == 1
    nummer, payload = fake.nachgezogen[0]
    assert nummer == 7
    url = next(f["value"] for f in payload["fields"] if f["name"] == "url")
    assert url == "http://nexview.test/api/webhooks/arr/radarr-standard"
    assert fake.angelegt == []
    assert _zeile().fehler == ""


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
