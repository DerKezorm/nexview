"""nexcrate legt einen entfernten Titel beim Zurückholen neu an.

Nachgestellter Fall aus dem Prüfgang: Ein ganz gewöhnlicher Film wird geladen, sein
Speicherposten gelöscht - nexcrate entfernt dabei den Titel vollständig, nicht
nur aus der Bibliothek (``title_id: null`` im Papierkorb) - und binnen
Sekunden zurückgeholt. Vorher scheiterte das mit 502/``recycle_title_gone``,
obwohl Datei und Papierkorb-Eintrag unverändert dastanden. Seit nexcrate den
Titel dabei neu anlegt, muss Nexview das richtig zeigen (der Knopf
hängt an ``restorable``, nicht an ``im_bestand``) und richtig melden
(``created``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.db import SessionLocal
from app.services.beschaffung import NEX
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import save_settings

from .beschaffung.fake_nexcrate import FILM_HD, KEY, URL, FakeNexcrate


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    attrappe = FakeNexcrate()
    nex_client.use_transport(attrappe.transport())
    system.merken(attrappe._system())
    nex_fassungen.vergessen()
    try:
        yield attrappe
    finally:
        nex_client.use_transport(None)
        system.vergessen()
        nex_fassungen.vergessen()


@pytest.fixture
def nex_admin(admin_client: Any, nexcrate: FakeNexcrate) -> Any:
    with SessionLocal() as sitzung:
        save_settings(
            sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY}
        )
        nex_fassungen.schreiben(sitzung, nexcrate.versions)
        sitzung.commit()
    antwort = admin_client.put(
        "/api/settings/fassungen", json=[{"kennung": FILM_HD, "offen_fuer_alle": True}]
    )
    assert antwort.status_code == 200, antwort.text
    return admin_client


# --- Die Liste: restorable statt im_bestand/datei_da ------------------------


def test_liste_zeigt_restorable_fuer_einen_entfernten_titel(
    nex_admin: Any, nexcrate: FakeNexcrate
) -> None:
    """Der Titel hat den Bestand verlassen, lässt sich aber wieder anlegen."""
    nexcrate.film(603)
    nexcrate.entfernt("movie", "tmdb:603", delete_files=True)

    antwort = nex_admin.get("/api/beschaffung/papierkorb")
    assert antwort.status_code == 200, antwort.text
    zeile = antwort.json()["eintraege"][0]
    assert zeile["datei_da"] is True
    assert zeile["im_bestand"] is False
    assert zeile["restorable"] is True


def test_liste_zeigt_restorable_false_wenn_es_wirklich_nicht_geht(
    nex_admin: Any, nexcrate: FakeNexcrate
) -> None:
    """Datei liegt noch da, aber der Weg kennt den Titel nicht mehr genug,
    um ihn wieder anzulegen - dann ist ``restorable`` falsch, obwohl die
    Datei ``present`` ist."""
    nexcrate.film(603)
    nexcrate.entfernt("movie", "tmdb:603", delete_files=True, restorable=False)

    zeile = nex_admin.get("/api/beschaffung/papierkorb").json()["eintraege"][0]
    assert zeile["datei_da"] is True
    assert zeile["restorable"] is False


# --- Zurückholen: created auswerten ------------------------------------------


def test_zurueckholen_legt_den_titel_neu_an_und_meldet_es(
    nex_admin: Any, nexcrate: FakeNexcrate
) -> None:
    nexcrate.film(603)
    nexcrate.entfernt("movie", "tmdb:603", delete_files=True)
    eintrag_id = nexcrate.recycle[0]["entry_id"]

    antwort = nex_admin.post(f"/api/beschaffung/papierkorb/{eintrag_id}/zurueckholen")

    assert antwort.status_code == 200, antwort.text
    assert antwort.json() == {"created": True}
    # Der Papierkorb ist leer - der Eintrag ist zurück.
    assert nex_admin.get("/api/beschaffung/papierkorb").json()["eintraege"] == []
    # Die neu angelegte Fassung ist unüberwacht, aber liegt vor.
    titel = nexcrate.titles[("movie", "tmdb:603")]
    fassung = titel["versions"][0]
    assert fassung["monitored"] is False
    assert fassung["state"] in ("available", "upgrade")
    # Keine neue Anfrage wurde dabei ausgelöst.
    assert not any(pfad.endswith("/requests") for _, pfad, _, _ in nexcrate.calls)


def test_zurueckholen_ohne_neuanlage_meldet_created_false(
    nex_admin: Any, nexcrate: FakeNexcrate
) -> None:
    """Der gewöhnliche Fall: Der Titel stand die ganze Zeit im Bestand."""
    nexcrate.film(603)
    nexcrate.entfernt("movie", "tmdb:603", delete_files=True)
    # Der Betreiber (oder eine andere Anfrage) hat den Titel inzwischen schon
    # wieder angelegt - das Zurückholen legt nur die Datei zurück.
    nexcrate.film(603)
    eintrag_id = nexcrate.recycle[0]["entry_id"]

    antwort = nex_admin.post(f"/api/beschaffung/papierkorb/{eintrag_id}/zurueckholen")

    assert antwort.status_code == 200, antwort.text
    assert antwort.json() == {"created": False}


def test_ein_neu_angelegter_titel_steht_nicht_als_gesucht_da(
    nex_admin: Any, nexcrate: FakeNexcrate
) -> None:
    """⚠️ Kernanforderung: keine falsche "wird gesucht"-Anzeige.

    ``status_setzen``/``kacheln_faerben`` liest nexcrates ``state`` je
    Fassung - nicht ``monitored`` - fuer die Kachel. Eine unueberwachte, aber
    vorliegende Fassung ist deshalb "downloaded", nie "searching".
    """
    # Der TMDB-Katalog kennt eigene Kennungen; nexcrate bekommt dieselbe, wie
    # es auch im echten Betrieb waere (derselbe Titel, dieselbe TMDB-Nummer).
    item = nex_admin.get("/api/discover/movie").json()["items"][0]
    ref = f"tmdb:{item['tmdb_id']}"
    nexcrate.film(item["tmdb_id"], name=item["title"])
    nexcrate.entfernt("movie", ref, delete_files=True)
    eintrag_id = nexcrate.recycle[0]["entry_id"]
    nex_admin.post(f"/api/beschaffung/papierkorb/{eintrag_id}/zurueckholen")

    karten = nex_admin.get("/api/discover/movie").json()["items"]
    karte = next(k for k in karten if k["tmdb_id"] == item["tmdb_id"])
    assert karte["status"] == "downloaded"

