"""Die Wächter über ``/api/v1`` - auch im NEX-Betrieb.

⚠️ **Befund von Prüfer P6.** ``/api/v1`` ist die Zusage nach außen (nexdeck-
Kachel, Home-Assistant-Integration), aber ``test_v1_zusage.py``,
``test_v1_wirklichkeit.py``, ``test_v1_kachel.py``, ``test_v1_selbstauskunft.py``
und ``test_v1_rueckkanal.py`` laufen ausschließlich im ARR-Betrieb - kein Test
ruft je eine v1-Adresse im NEX-Betrieb auf. Ein Bruch der Zusage, der nur dort
auftritt, bliebe grün: genau das ist zweimal passiert (``tier`` war für eine
4K-Fassung ``standard`` statt ``uhd``, ``POST /api/v1/requests`` endete mit
409 oder 502).

Geprüft wird hier bewusst **nicht** Wert für Wert (das tut
``test_v1_wirklichkeit.py`` im ARR-Betrieb), sondern je Adresse Statuscode und
Schlüsselmenge - gegen denselben Abdruck (``v1_abdruck.json``), den auch
``test_v1_zusage.py`` hütet. Ein Feld **hinzuzufügen** ist erlaubt (deshalb
Teilmenge, nicht Gleichheit); fehlt eines, ist die Zusage im NEX-Betrieb
gebrochen, auch wenn sie im ARR-Betrieb hält.

Ausgelassen bleiben ``/search/{media_type}`` und ``/media/{media_type}/{tmdb_id}``
- die brauchen TMDB, das ist eine andere Baustelle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    RequestStatus,
    StorageEntry,
    StorageState,
    User,
)
from app.services.beschaffung import NEX

from .beschaffung.fake_nexcrate import FILM_HD, FILM_UHD, FakeNexcrate
from .conftest import auth_headers, create_user
from .test_nex_lesen import db, nex, nexcrate  # noqa: F401 - Fixtures des NEX-Betriebs

GB = 1024**3

#: Derselbe Abdruck wie in ``test_v1_zusage.py`` - hier nur gelesen, nicht
#: erneuert. Ohne ihn (erster Lauf von ``test_v1_zusage.py``) laeuft diese
#: Datei ins Leere; das ist in der Testreihe nie der Fall.
ABDRUCK = Path(__file__).with_name("v1_abdruck.json")

#: Alle Adressen aus der Zusage, die ohne TMDB und ohne fremden Dienst
#: auskommen - dieselbe Auswahl wie ``OHNE_VORAUSSETZUNG`` in
#: ``test_v1_wirklichkeit.py``, hier im NEX-Betrieb abgefahren.
GET_OHNE_TMDB = (
    "/api/v1/requests/mine",
    "/api/v1/requests/quota",
    "/api/v1/home/recent",
    "/api/v1/tickets/open-count",
    "/api/v1/admin/requests/pending/count",
    "/api/v1/notifications/unread/count",
    "/api/v1/about",
    "/api/v1/storage/me",
    "/api/v1/dashboard",
    "/api/v1/me",
    "/api/v1/health",
)

#: Die vier Adressen, ueber die der Abdruck schweigt (``dict[str, int]``, im
#: Schema "irgendein Objekt"). Namen wie in ``test_v1_wirklichkeit.py``
#: nachgeschlagen, nicht geraten - sie stehen in keinem Schema.
BENANNTER_SCHLUESSEL = {
    "/api/v1/tickets/open-count": "count",
    "/api/v1/admin/requests/pending/count": "pending",
    "/api/v1/notifications/unread/count": "unread",
    "/api/v1/health": "status",
}

#: Warum die restlichen zugesagten Adressen hier nicht laufen.
BEGRUENDUNG = {
    "/api/v1/search/{media_type}": "braucht TMDB",
    "/api/v1/media/{media_type}/{tmdb_id}": "braucht TMDB",
}


def _top_level_felder(schluessel: str) -> set[str]:
    """Die obersten Feldnamen, die der Abdruck fuer diese Adresse festhaelt.

    Der Abdruck haelt Pfade bis in die Tiefe fest (``anfragen.wartend``,
    ``instanzen[].name``, ``[].error_detail{*}``). Hier zaehlt nur die
    oberste Ebene - tiefer zu vergleichen bräuchte dieselbe Antwortform wie im
    ARR-Betrieb, und die hat der NEX-Betrieb an einzelnen Stellen bewusst
    nicht (z. B. keine Instanzen-Liste mit denselben Namen).
    """
    daten = json.loads(ABDRUCK.read_text(encoding="utf-8"))
    ergebnis: set[str] = set()
    for pfad in daten.get(schluessel, []):
        rest = pfad
        if rest.startswith("[]."):
            rest = rest[3:]
        elif rest.startswith("[]"):
            rest = rest[2:]
        for ende, zeichen in enumerate(rest):
            if zeichen in ".[{":
                rest = rest[:ende]
                break
        if rest:
            ergebnis.add(rest)
    return ergebnis


def _oeffnen(client: TestClient, *kennungen: str) -> None:
    """So gibt der Betreiber eine Fassung frei (wie in test_nex_schreiben.py)."""
    antwort = client.put(
        "/api/settings/fassungen",
        json=[{"kennung": kennung, "offen_fuer_alle": True} for kennung in kennungen],
    )
    assert antwort.status_code == 200, antwort.text


def _demo_titel(client: TestClient, attrappe: FakeNexcrate, art: str) -> dict[str, Any]:
    """Ein Titel aus den Demo-Daten, den nexcrate kennt, aber in keiner Fassung hat."""
    item = client.get(f"/api/discover/{art}").json()["items"][0]
    if art == "movie":
        attrappe.film(item["tmdb_id"], versionen=[])
    else:
        attrappe.serie(item["tmdb_id"], versionen=[])
    return item


def _kim(client: TestClient) -> tuple[int, dict[str, str]]:
    kim = create_user(client, "kim")
    return kim["id"], auth_headers(client, "kim", "passwort-1234")


@pytest.mark.usefixtures("nex")
def test_die_lesewege_bleiben_die_zugesagten_auch_im_nex_betrieb(
    admin_client: TestClient,
) -> None:
    """⚠️ **Der eigentliche Wächter.** Holt jede lesende Adresse ohne TMDB
    tatsaechlich im NEX-Betrieb ab und vergleicht die obersten Feldnamen gegen
    den Abdruck. Dazu eine Anfrage und ein Speicherposten in einer NEX-4K-
    Fassung: ``tier`` muss ``uhd`` bleiben, nicht ``standard`` (seit R2
    richtig - hier steht der Wächter dafür, den es vorher nicht gab).
    """
    with SessionLocal() as sitzung:
        admin = sitzung.query(User).filter(User.username == "admin").one()
        sitzung.add(
            MediaRequest(
                user_id=admin.id,
                media_type=MediaType.movie,
                fassung_kennung=FILM_UHD,
                tmdb_id=850401,
                title="Erfundener Film in 4K",
                status=RequestStatus.downloaded,
            )
        )
        sitzung.add(
            StorageEntry(
                key=f"movie:{FILM_UHD}:tmdb:850401",
                user_id=admin.id,
                media_type=MediaType.movie,
                fassung_kennung=FILM_UHD,
                tmdb_id=850401,
                title="Erfundener Film in 4K",
                size_bytes=45 * GB,
                state=StorageState.owned,
            )
        )
        sitzung.commit()

    antworten: dict[str, Any] = {}
    fehlend: list[str] = []
    for pfad in GET_OHNE_TMDB:
        antwort = admin_client.get(pfad)
        assert antwort.status_code == 200, f"{pfad}: {antwort.text[:200]}"
        daten = antwort.json()
        antworten[pfad] = daten

        if pfad in BENANNTER_SCHLUESSEL:
            schluessel = BENANNTER_SCHLUESSEL[pfad]
            if schluessel not in daten:
                fehlend.append(f"GET {pfad}: '{schluessel}' fehlt")
            continue

        erwartet = _top_level_felder(f"GET {pfad}")
        # Eine leere Liste sagt nichts ueber die Form - genau wie in
        # test_v1_wirklichkeit.py wird sie dann nicht geprueft.
        if isinstance(daten, list):
            if not daten:
                continue
            echte = set(daten[0])
        else:
            echte = set(daten)
        for feld in sorted(erwartet - echte):
            fehlend.append(f"GET {pfad}: '{feld}' fehlt")

    assert fehlend == [], (
        "Im NEX-Betrieb fehlt an diesen zugesagten Antworten ein Feld, das "
        "der Abdruck festhaelt - der ARR-Waechter sieht das nicht, weil er "
        "diese Adressen im NEX-Betrieb nie aufruft:\n  " + "\n  ".join(fehlend)
    )

    assert antworten["/api/v1/dashboard"]["beschaffung"] == NEX

    anfragen = antworten["/api/v1/requests/mine"]
    speicher = antworten["/api/v1/storage/me"]
    assert any(a["tier"] == "uhd" and a["tmdb_id"] == 850401 for a in anfragen), (
        "Die Anfrage auf der NEX-4K-Fassung meldet nicht tier=uhd."
    )
    assert any(p["tier"] == "uhd" and p["tmdb_id"] == 850401 for p in speicher["entries"]), (
        "Der Speicherposten auf der NEX-4K-Fassung meldet nicht tier=uhd."
    )


def test_nichts_wird_still_uebergangen() -> None:
    """Jede zugesagte Adresse ist hier entweder geprueft oder begruendet.

    ⚠️ Ohne diesen Test waere die Abdeckung eine Behauptung. Kommt eine
    weitere v1-Adresse dazu und niemand traegt sie ein, sehen die Tests oben
    weiter gruen aus und pruefen sie im NEX-Betrieb einfach nicht mit.
    """
    from app.routers.v1 import ZUGESAGT

    abgedeckt = set(GET_OHNE_TMDB) | {
        "/api/v1/requests",
        "/api/v1/requests/{request_id}/cancel",
        "/api/v1/me/push",
    }
    offen = set(ZUGESAGT) - abgedeckt - set(BEGRUENDUNG)
    assert offen == set(), (
        f"Diese zugesagten Adressen laufen im NEX-Betrieb weder mit noch "
        f"sind sie begruendet uebergangen: {sorted(offen)}."
    )


@pytest.mark.usefixtures("nex")
def test_anfragen_und_abbrechen_bleiben_die_zugesagten_auch_im_nex_betrieb(
    admin_client: TestClient, nexcrate: FakeNexcrate  # noqa: F811 - Fixture, kein Import hier
) -> None:
    """⚠️ Bis zur Reparatur endete ``POST /api/v1/requests`` im NEX-Betrieb mit
    409 oder 502, weil die Hauptfassung zu war (nach dem Koppeln ist jede
    Fassung gesperrt, das ist Absicht) - hier wird sie erst geoeffnet, wie es
    der Betreiber taete.
    """
    _oeffnen(admin_client, FILM_HD)
    kim_id, kopf = _kim(admin_client)
    titel = _demo_titel(admin_client, nexcrate, "movie")

    angelegt = admin_client.post(
        "/api/v1/requests",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kopf,
    )

    assert angelegt.status_code == 201, angelegt.text
    antwort = angelegt.json()
    assert _top_level_felder("POST /api/v1/requests") <= set(antwort), sorted(
        _top_level_felder("POST /api/v1/requests") - set(antwort)
    )
    assert antwort["fassung"] == FILM_HD

    # Direkt eine laufende Anfrage einsetzen statt ueber eine Freigabe zu
    # gehen: Bewacht wird hier die Zusage von ".../cancel", nicht der
    # Freigabeweg - den deckt test_nex_schreiben.py bereits ab. Ohne
    # ``arr_id`` bleibt nexcrate aussen vor, wie bei einer Anfrage, die nie
    # freigegeben wurde.
    with SessionLocal() as sitzung:
        laufend = MediaRequest(
            user_id=kim_id,
            media_type=MediaType.movie,
            fassung_kennung=FILM_HD,
            tmdb_id=titel["tmdb_id"] + 1,
            title="Erfundener Film zum Abbrechen",
            status=RequestStatus.approved,
        )
        sitzung.add(laufend)
        sitzung.commit()
        laufend_id = laufend.id

    abgebrochen = admin_client.post(f"/api/v1/requests/{laufend_id}/cancel", headers=kopf)

    assert abgebrochen.status_code == 200, abgebrochen.text
    fehlend = _top_level_felder("POST /api/v1/requests/{request_id}/cancel") - set(
        abgebrochen.json()
    )
    assert fehlend == set(), sorted(fehlend)


@pytest.fixture
def versand(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Statt zu verschicken: einsammeln (wie in test_v1_rueckkanal.py)."""
    gesammelt: list[Any] = []

    async def merken(kind, config, notice):  # noqa: ANN001, ANN202
        gesammelt.append(notice)

    monkeypatch.setattr("app.routers.v1.channels.send", merken)
    return gesammelt


@pytest.mark.usefixtures("nex")
def test_der_rueckkanal_bleibt_der_zugesagte_auch_im_nex_betrieb(
    admin_client: TestClient, versand: list[Any]
) -> None:
    """``/me/push`` haengt an keinem Beschaffungsweg - hier trotzdem einmal
    im NEX-Betrieb durchgespielt, damit die Zusage vollstaendig steht.
    """
    schluessel_antwort = admin_client.post(
        "/api/auth/me/schluessel", json={"name": "Pruefstand", "nur_lesen": False}
    )
    assert schluessel_antwort.status_code in (200, 201), schluessel_antwort.text
    admin_client.headers["Authorization"] = f"Bearer {schluessel_antwort.json()['schluessel']}"

    put_antwort = admin_client.put(
        "/api/v1/me/push",
        json={"url": "http://192.168.1.50:8123/api/webhook/nex-pruefstand", "language": "de"},
    )
    assert put_antwort.status_code == 200, put_antwort.text
    assert _top_level_felder("PUT /api/v1/me/push") <= set(put_antwort.json())

    code = versand[-1].code
    assert code, "Die Testnachricht traegt keinen Code in ihrem eigenen Feld."

    post_antwort = admin_client.post("/api/v1/me/push", json={"code": code})
    assert post_antwort.status_code == 200, post_antwort.text
    assert _top_level_felder("POST /api/v1/me/push") <= set(post_antwort.json())

    get_antwort = admin_client.get("/api/v1/me/push")
    assert get_antwort.status_code == 200, get_antwort.text
    assert _top_level_felder("GET /api/v1/me/push") <= set(get_antwort.json())

    delete_antwort = admin_client.delete("/api/v1/me/push")
    assert delete_antwort.status_code == 204, delete_antwort.text
