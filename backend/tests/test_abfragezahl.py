"""Abfragezahl der drei in Punkt 5 umgebauten Adressen - feste Zielzahlen.

Vor dem Umbau wuchs die Zahl mit den Zeilen: /api/admin/requests stellte je
Anfrage zwei Bewertungs-Abfragen (an der echten Datenbank 158 Abfragen fuer
145 Zeilen), /api/users zaehlte zweimal je Konto, das Dashboard las denselben
gemerkten Stand bis zu sechsmal. Jetzt laedt jede Adresse ihre Tabellen je
einmal; diese Datei haelt die Zielzahlen fest, mit etwas Luft.

⚠️ Bewusst NUR die Zielzahlen: Die Wachstumsprobe (gleiche Abfragezahl bei
3 wie bei 12 Zeilen, bei einer wie bei drei Instanzen) uebernimmt die
allgemeine Abfragen-Waage in test_abfragen_waage.py aus Punkt 6 - zwei
Waagen fuer dieselbe Frage liefen auseinander.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.db import SessionLocal, engine
from app.models import MediaRequest, RequestStatus

from .conftest import auth_headers, create_user

# Zielzahlen samt Luft. Gemessen nach dem Umbau: 11 fuer die Anfrageliste,
# 9 fuer die Benutzerliste, 26 fuer die Kachel (an der echten Datenbank mit
# drei Instanzen: 11, 9, 27). Zwei Abfragen Luft, damit nicht jede harmlose
# Nebenaenderung anschlaegt - aber ein zurueckgekehrtes N+1 kostet schon bei
# den drei Zeilen dieses Tests mehr als das und faellt sofort auf.
ANFRAGELISTE_HOECHSTENS = 13
BENUTZERLISTE_HOECHSTENS = 11
DASHBOARD_HOECHSTENS = 28


@contextmanager
def _gezaehlt() -> Iterator[list[str]]:
    """Jede Abfrage an der Engine mitschreiben - ueber alle Sitzungen."""
    saetze: list[str] = []

    def horcher(conn, cursor, statement, parameters, context, executemany) -> None:
        saetze.append(statement)

    event.listen(engine, "before_cursor_execute", horcher)
    try:
        yield saetze
    finally:
        event.remove(engine, "before_cursor_execute", horcher)


def _geladene_anfrage_mit_urteil(
    client: TestClient, benutzer: str, sterne: int, stelle: int = 0
) -> None:
    """Ein Konto, eine geladene Film-Anfrage, ein Urteil dazu.

    Geladen statt wartend: Eine wartende Anfrage zieht die Abo-Spalte an,
    und die ist nicht Teil dieses Umbaus. Jeder Benutzer bekommt einen
    eigenen Titel - ein geladener laesst sich kein zweites Mal anfragen.
    """
    create_user(client, benutzer)
    kopf = auth_headers(client, benutzer, "passwort-1234")
    titel = client.get("/api/discover/movie").json()["items"][stelle]
    angelegt = client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": titel["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=kopf,
    )
    assert angelegt.status_code == 201, angelegt.text

    with SessionLocal() as session:
        anfrage = session.get(MediaRequest, angelegt.json()["id"])
        assert anfrage is not None
        anfrage.status = RequestStatus.downloaded
        session.commit()

    bewertet = client.put(
        f"/api/feedback/movie/{titel['tmdb_id']}",
        json={"rating": sterne, "title": titel.get("title") or "T"},
        headers=kopf,
    )
    assert bewertet.status_code == 200, bewertet.text


def test_anfrageliste_hat_feste_abfragezahl(arr_client: TestClient) -> None:
    for stelle, (benutzer, sterne) in enumerate((("kim", 4), ("alex", 2), ("lars", 5))):
        _geladene_anfrage_mit_urteil(arr_client, benutzer, sterne, stelle)

    with _gezaehlt() as saetze:
        antwort = arr_client.get("/api/admin/requests")
    assert antwort.status_code == 200
    assert len(antwort.json()) == 3
    assert len(saetze) <= ANFRAGELISTE_HOECHSTENS, (
        f"{len(saetze)} Abfragen fuer die Anfrageliste:\n" + "\n".join(saetze)
    )


def test_benutzerliste_hat_feste_abfragezahl(arr_client: TestClient) -> None:
    for benutzer in ("kim", "alex", "lars"):
        create_user(arr_client, benutzer)

    with _gezaehlt() as saetze:
        antwort = arr_client.get("/api/users")
    assert antwort.status_code == 200
    assert len(antwort.json()) == 4
    assert len(saetze) <= BENUTZERLISTE_HOECHSTENS, (
        f"{len(saetze)} Abfragen fuer die Benutzerliste:\n" + "\n".join(saetze)
    )


def test_ruecksetz_gruppe_kostet_hoechstens_eine_abfrage_mehr(
    arr_client: TestClient,
) -> None:
    """Ein von Hand zurueckgesetztes Konto bildet eine eigene Zaehl-Gruppe.

    Genau eine Sammelabfrage mehr - und die Zahlen muessen stimmen: Wer alle
    Konten unter einen globalen Zaehlbeginn schoebe, zaehlte beim
    zurueckgesetzten Konto den alten Verbrauch wieder mit.
    """
    kim = create_user(arr_client, "kim")
    kopf = auth_headers(arr_client, "kim", "passwort-1234")
    titel = arr_client.get("/api/discover/movie").json()["items"][:3]
    for eintrag in titel[:2]:
        angelegt = arr_client.post(
            "/api/requests",
            json={
                "media_type": "movie",
                "tmdb_id": eintrag["tmdb_id"],
                "quality_profile_id": 1,
                "root_folder_path": "/data/Movies",
            },
            headers=kopf,
        )
        assert angelegt.status_code == 201, angelegt.text

    assert arr_client.post(f"/api/users/{kim['id']}/quota/reset").status_code == 200

    angelegt = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": titel[2]["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=kopf,
    )
    assert angelegt.status_code == 201, angelegt.text

    with _gezaehlt() as saetze:
        antwort = arr_client.get("/api/users")
    assert antwort.status_code == 200
    assert len(saetze) <= BENUTZERLISTE_HOECHSTENS + 1, (
        f"{len(saetze)} Abfragen fuer die Benutzerliste mit Ruecksetz-Gruppe:\n"
        + "\n".join(saetze)
    )

    eintrag = next(u for u in antwort.json() if u["id"] == kim["id"])
    assert eintrag["quota_movies_used"] == 1
    assert eintrag["quota_series_used"] == 0


def test_dashboard_kachel_hat_feste_abfragezahl(arr_client: TestClient) -> None:
    with _gezaehlt() as saetze:
        antwort = arr_client.get("/api/v1/dashboard")
    assert antwort.status_code == 200
    assert len(saetze) <= DASHBOARD_HOECHSTENS, (
        f"{len(saetze)} Abfragen fuer die Kachel:\n" + "\n".join(saetze)
    )
