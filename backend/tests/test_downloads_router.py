"""Die Seite Downloads ueber HTTP: Form der Antworten, Rechte, Fehler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    DownloadHaenger,
    DownloadVerlauf,
    MediaRequest,
    MediaType,
    RequestStatus,
    Role,
    User,
)
from app.services.beschaffung.arr import download_aktionen, download_haenger
from app.services.settings_service import load_settings

from .conftest import auth_headers, create_user
from .download_attrappe import RADARR_HOST, ArrAttrappe, einrichten, film


@pytest.fixture
def arr(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ArrAttrappe:
    monkeypatch.setattr(download_aktionen, "ABWARTEN_TAKT", 0)
    return einrichten(admin_client, monkeypatch)


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _haengend(zeile: dict) -> int:
    """Einen Download so ablegen, als haenge er schon eine Weile."""
    with SessionLocal() as db:
        instanz = next(i for i in load_settings(db).arr_instanzen() if i.kennung == "radarr-standard")
        download_haenger.abgleichen(
            db, instanz, download_haenger.zusammenfassen(instanz, [zeile]), _jetzt() - timedelta(minutes=20)
        )
        db.commit()
        eintrag = db.scalars(
            select(DownloadHaenger).where(DownloadHaenger.download_id == zeile["downloadId"])
        ).one()
        eintrag.haengt_seit = _jetzt() - timedelta(minutes=5)
        db.commit()
        return eintrag.id


def test_die_uebersicht_trennt_haengendes_von_laufendem(
    arr: ArrAttrappe, admin_client: TestClient
) -> None:
    besitzer = create_user(admin_client, "kim")
    with SessionLocal() as db:
        db.add(
            MediaRequest(
                user_id=besitzer["id"], media_type=MediaType.movie, fassung_kennung="radarr-standard",
                tmdb_id=4711, title="Beispielfilm", status=RequestStatus.searching, arr_id=5,
                poster_path="https://image.example.com/p.jpg",
            )
        )
        db.commit()
    haengt = film(11, "D1", release="Beispielfilm.2010.1080p-GRP")
    arr.warteschlange[RADARR_HOST] = [
        haengt,
        film(
            12, "D2", film_id=6, zustand="downloading", meldung="ok", programm="downloading",
            texte=(), rest=600, release="Zweiter.Film.2011.720p-GRP",
        ),
        film(13, "D3", film_id=8, texte=("Found archive file, might need to be extracted",),
             release="Dritter.Film.2012.1080p-GRP"),
    ]
    haenger_id = _haengend(haengt)

    antwort = admin_client.get("/api/admin/downloads")

    assert antwort.status_code == 200, antwort.text
    stand = antwort.json()
    (karte,) = stand["haenger"]
    assert karte["id"] == haenger_id
    assert (karte["grund"], karte["instanz"], karte["titel"]) == ("sample", "Radarr", "Beispielfilm")
    assert karte["wortlaut"] == ["Sample"]
    assert karte["empfohlen"] == ["entfernen_neu_suchen", "manuell_importieren"]
    assert karte["weitere"] == ["entfernen", "erneut_pruefen"]
    assert [b["name"] for b in karte["besteller"]] == ["kim"]
    assert karte["poster"] == "https://image.example.com/p.jpg"

    laufend = {zeile["release"]: zeile for zeile in stand["laufend"]}
    assert set(laufend) == {"Zweiter.Film.2011.720p-GRP", "Dritter.Film.2012.1080p-GRP"}
    assert laufend["Zweiter.Film.2011.720p-GRP"]["fortschritt"] == 40
    assert laufend["Zweiter.Film.2011.720p-GRP"]["restzeit"] == "00:10:00"
    assert laufend["Zweiter.Film.2011.720p-GRP"]["beobachtet"] is None
    # Gestoert, aber gerade erst gesehen: beobachtet, nicht haengend.
    assert laufend["Dritter.Film.2012.1080p-GRP"]["beobachtet"] == "archiv"

    assert [(i["kennung"], i["erreichbar"]) for i in stand["instanzen"]] == [
        ("radarr-standard", True),
        ("sonarr-standard", True),
    ]
    assert stand["automatik_an"] is False

    # Und in der Liste der Entscheider steht der Hinweis an der Anfrage.
    (anfrage,) = admin_client.get("/api/admin/requests").json()
    assert anfrage["import_haengt"] == "sample"


def test_eine_stumme_instanz_steht_mit_ihrem_fehler_da(
    arr: ArrAttrappe, admin_client: TestClient
) -> None:
    arr.stumm.add(RADARR_HOST)
    stand = admin_client.get("/api/admin/downloads").json()
    radarr = next(i for i in stand["instanzen"] if i["kennung"] == "radarr-standard")
    assert (radarr["erreichbar"], radarr["fehler"]) == (False, "arr_unreachable")


PFADE = [
    ("get", "/api/admin/downloads", None),
    ("post", "/api/admin/downloads/1/entfernen", {"neu_suchen": False}),
    ("post", "/api/admin/downloads/1/erneut", None),
    ("get", "/api/admin/downloads/1/dateien", None),
    ("post", "/api/admin/downloads/1/importieren", {"pfade": ["/x"], "trotzdem": False}),
    ("get", "/api/admin/downloads/automatik", None),
    ("put", "/api/admin/downloads/automatik", {"an": True, "regeln": {}}),
    ("get", "/api/admin/downloads/verlauf", None),
]


@pytest.mark.parametrize(("methode", "pfad", "koerper"), PFADE)
@pytest.mark.parametrize("rolle", [Role.approver, Role.user])
def test_nur_administratoren(
    admin_client: TestClient, methode: str, pfad: str, koerper: dict | None, rolle: Role
) -> None:
    """Entscheider sehen den Hinweis an der Anfrage - handeln darf nur der Betrieb."""
    create_user(admin_client, "jemand", role=rolle)
    kopf = auth_headers(admin_client, "jemand", "passwort-1234")
    extra = {"json": koerper} if koerper is not None else {}
    antwort = getattr(admin_client, methode)(pfad, headers=kopf, **extra)
    assert antwort.status_code == 403, antwort.text


def test_ein_verschwundener_download_und_eine_stumme_instanz(
    arr: ArrAttrappe, admin_client: TestClient
) -> None:
    haenger_id = _haengend(film())
    arr.warteschlange[RADARR_HOST] = []

    weg = admin_client.post(f"/api/admin/downloads/{haenger_id}/entfernen", json={"neu_suchen": True})
    assert weg.status_code == 409
    assert weg.json()["detail"]["code"] == "download_gone"
    assert weg.json()["detail"]["titel"] == "Beispielfilm"

    zweiter = _haengend(film())
    arr.fehler[("GET", "/queue")] = 500
    stumm = admin_client.post(f"/api/admin/downloads/{zweiter}/erneut")
    assert stumm.status_code == 502
    assert stumm.json()["detail"]["code"] == "arr_http_error"


def test_ein_import_ohne_datei_ist_ungueltig(admin_client: TestClient) -> None:
    antwort = admin_client.post(
        "/api/admin/downloads/1/importieren", json={"pfade": [], "trotzdem": False}
    )
    assert antwort.status_code == 422


def test_die_dateien_eines_downloads(arr: ArrAttrappe, admin_client: TestClient) -> None:
    arr.warteschlange[RADARR_HOST] = [film()]
    haenger_id = _haengend(film())
    arr.kandidaten["D1"] = [
        {
            "path": "/downloads/Beispielfilm/sample.mkv",
            "relativePath": "sample.mkv",
            "movie": {"id": 5, "title": "Beispielfilm", "year": 2010},
            "quality": {"quality": {"name": "Bluray-1080p"}},
            "languages": [{"name": "German"}],
            "size": 1000,
            "rejections": [{"reason": "Sample", "type": "permanent"}],
        }
    ]

    (datei,) = admin_client.get(f"/api/admin/downloads/{haenger_id}/dateien").json()

    assert datei["name"] == "sample.mkv"
    assert datei["zuordnung"] == "Beispielfilm (2010)"
    assert datei["zuordenbar"] is True
    assert datei["ablehnungen"] == [{"text": "Sample", "dauerhaft": True}]

    ohne = admin_client.post(
        f"/api/admin/downloads/{haenger_id}/importieren",
        json={"pfade": ["/downloads/Beispielfilm/sample.mkv"], "trotzdem": False},
    )
    assert ohne.status_code == 409
    assert ohne.json()["detail"]["code"] == "download_import_needs_confirmation"


def test_automatik_lesen_und_setzen(admin_client: TestClient) -> None:
    stand = admin_client.get("/api/admin/downloads/automatik").json()
    assert stand["an"] is False
    regeln = {regel["grund"]: regel for regel in stand["regeln"]}
    # Wo die Automatik nichts darf, gibt es auch nichts einzustellen.
    assert "pfadzuordnung" not in regeln
    assert regeln["sample"] == {"grund": "sample", "aktion": None, "erlaubt": ["entfernen_neu_suchen"]}
    assert (stand["obergrenze"], stand["fenster_stunden"]) == (2, 24)
    assert (stand["wiederholt_ab"], stand["wiederholt_tage"]) == (3, 7)

    gesetzt = admin_client.put(
        "/api/admin/downloads/automatik",
        json={"an": True, "regeln": {"sample": "entfernen_neu_suchen", "archiv": None}},
    )
    assert gesetzt.status_code == 200, gesetzt.text
    assert gesetzt.json()["an"] is True

    falsch = admin_client.put(
        "/api/admin/downloads/automatik",
        json={"an": True, "regeln": {"sample": "manuell_importieren"}},
    )
    assert falsch.status_code == 422
    assert falsch.json()["detail"]["code"] == "download_automation_not_allowed"

    danach = {r["grund"]: r for r in admin_client.get("/api/admin/downloads/automatik").json()["regeln"]}
    assert danach["sample"]["aktion"] == "entfernen_neu_suchen"


def test_der_verlauf_neueste_zuerst(admin_client: TestClient) -> None:
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        db.add_all(
            [
                DownloadVerlauf(
                    kennung="radarr-standard", titel="Beispielfilm", grund="sample",
                    was="erkannt", automatisch=True, am=_jetzt() - timedelta(hours=2),
                ),
                DownloadVerlauf(
                    kennung="radarr-standard", titel="Beispielfilm", grund="sample",
                    was="entfernen", automatisch=False, user_id=admin.id,
                    am=_jetzt() - timedelta(hours=1),
                ),
            ]
        )
        db.commit()
        name = admin.display_name or admin.username

    zeilen = admin_client.get("/api/admin/downloads/verlauf").json()

    assert [z["was"] for z in zeilen] == ["entfernen", "erkannt"]
    assert zeilen[0]["wer"] == name
    assert zeilen[1]["wer"] is None
    # Ohne eingerichtete Instanz kommt der Name aus der Fassungstabelle
    # (Rundgang-Befund 7); die Kennung steht nur, wo auch dort nichts steht.
    assert zeilen[0]["instanz"] == "Radarr"
