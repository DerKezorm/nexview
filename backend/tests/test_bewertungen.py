"""Rückmeldungen zur Qualität - am Titel, nicht an der Anfrage.

Der Kern, den diese Tests festhalten: **Bewerten darf jeder**, der einen
vorhandenen Titel gesehen hat, nicht nur der Besteller. Bis 0.19 hing die
Bewertung an der Anfrage, und damit hatte niemand sonst eine Stimme.

Und der zweite: Ein Urteil gilt der **Datei**. Schiebt Radarr etwas Besseres
nach, ist es hinfällig - es bleibt stehen, zählt aber nicht mehr mit.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaType,
    Notification,
    NotificationType,
    Role,
    TitleRating,
    User,
)
from app.services import ratings, stats

from .conftest import auth_headers, create_user


@pytest.fixture()
def zwei_konten(admin_client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    create_user(admin_client, "besteller")
    create_user(admin_client, "zuschauer")
    return (
        auth_headers(admin_client, "besteller", "passwort-1234"),
        auth_headers(admin_client, "zuschauer", "passwort-1234"),
    )


# --- Wer darf bewerten ------------------------------------------------------


def test_auch_ohne_eigene_anfrage(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    """Der Kern der Sache: Wer nie angefragt hat, darf trotzdem urteilen.

    Es geht um die Datei, und die beurteilt jeder gleich gut, der sie gesehen
    hat. Bis 0.19 war das unmöglich - die Bewertung hing an der Anfrage.
    """
    _, zuschauer = zwei_konten
    antwort = admin_client.put(
        "/api/feedback/movie/603",
        json={"rating": 4, "comment": "Läuft sauber.", "title": "The Matrix"},
        headers=zuschauer,
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["rating"] == 4


def test_admin_bewertet_nicht(admin_client: TestClient) -> None:
    """Er beantwortet die Rückmeldungen der anderen - ein Urteil über die
    eigene Bibliothek wäre eine Antwort an sich selbst."""
    antwort = admin_client.put(
        "/api/feedback/movie/603", json={"rating": 5, "title": "x"}
    )
    assert antwort.status_code == 403


def test_kind_bewertet_nicht(admin_client: TestClient) -> None:
    create_user(admin_client, "kindbewertung", role=Role.child)
    kopf = auth_headers(admin_client, "kindbewertung", "passwort-1234")
    antwort = admin_client.put(
        "/api/feedback/movie/603", json={"rating": 5, "title": "x"}, headers=kopf
    )
    assert antwort.status_code == 403


# --- Ein Urteil je Person und Titel ----------------------------------------


def test_zweites_urteil_ersetzt_das_erste(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    _, zuschauer = zwei_konten
    for sterne in (2, 5):
        admin_client.put(
            "/api/feedback/movie/603",
            json={"rating": sterne, "title": "The Matrix"},
            headers=zuschauer,
        )
    liste = admin_client.get("/api/feedback/mine", headers=zuschauer).json()
    assert len(liste) == 1
    assert liste[0]["rating"] == 5


def test_zwei_konten_urteilen_getrennt(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    besteller, zuschauer = zwei_konten
    admin_client.put(
        "/api/feedback/movie/603", json={"rating": 2, "title": "M"}, headers=besteller
    )
    admin_client.put(
        "/api/feedback/movie/603", json={"rating": 5, "title": "M"}, headers=zuschauer
    )
    with SessionLocal() as db:
        assert db.query(TitleRating).filter(TitleRating.tmdb_id == 603).count() == 2


def test_serien_werden_je_staffel_beurteilt(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    """Die Dateien liegen staffelweise, die Qualität unterscheidet sich
    staffelweise. Eine Serie als Ganzes zu bewerten hieße, über zehn
    verschiedene Dateien ein Urteil zu fällen."""
    _, zuschauer = zwei_konten
    for staffel, sterne in ((1, 5), (2, 2)):
        admin_client.put(
            "/api/feedback/tv/1399",
            json={"rating": sterne, "season": staffel, "title": "GoT"},
            headers=zuschauer,
        )
    liste = admin_client.get("/api/feedback/mine", headers=zuschauer).json()
    assert sorted((e["season"], e["rating"]) for e in liste) == [(1, 5), (2, 2)]


# --- Die Alterung -----------------------------------------------------------


def test_aufwertung_entwertet_das_urteil(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    _, zuschauer = zwei_konten
    admin_client.put(
        "/api/feedback/movie/603", json={"rating": 2, "title": "M"}, headers=zuschauer
    )
    with SessionLocal() as db:
        # Erst der Ausgangsstand, dann der Sprung.
        assert ratings.entwerten(db, MediaType.movie, 603, 5_000_000_000) == 0
        db.commit()
        assert ratings.entwerten(db, MediaType.movie, 603, 50_000_000_000) == 1
        db.commit()

        eintrag = db.scalar(
            __import__("sqlalchemy").select(TitleRating).where(TitleRating.tmdb_id == 603)
        )
        # ⚠️ Die Sterne bleiben stehen - löschen verlöre die Information.
        assert eintrag.outdated is True
        assert eintrag.rating == 2
        assert (
            db.query(Notification)
            .filter(Notification.type == NotificationType.rating_outdated)
            .count()
            == 1
        )


def test_erster_stand_ist_keine_aufwertung(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    """Sonst gälte beim Einbau jede bestehende Bewertung sofort als veraltet -
    und alle bekämen auf einmal eine Nachricht über etwas, das nie war."""
    _, zuschauer = zwei_konten
    admin_client.put(
        "/api/feedback/movie/604", json={"rating": 3, "title": "M"}, headers=zuschauer
    )
    with SessionLocal() as db:
        assert ratings.entwerten(db, MediaType.movie, 604, 90_000_000_000) == 0


def test_kleiner_zuwachs_entwertet_nicht(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    """Ein paar hundert Megabyte können eine nachgereichte Tonspur sein."""
    _, zuschauer = zwei_konten
    admin_client.put(
        "/api/feedback/movie/605", json={"rating": 3, "title": "M"}, headers=zuschauer
    )
    with SessionLocal() as db:
        ratings.entwerten(db, MediaType.movie, 605, 5_000_000_000)
        db.commit()
        assert ratings.entwerten(db, MediaType.movie, 605, 5_300_000_000) == 0


def test_neu_bewerten_macht_wieder_gueltig(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    """Wer neu urteilt, hat die Datei angesehen, die jetzt dort liegt."""
    _, zuschauer = zwei_konten
    admin_client.put(
        "/api/feedback/movie/606", json={"rating": 1, "title": "M"}, headers=zuschauer
    )
    with SessionLocal() as db:
        ratings.entwerten(db, MediaType.movie, 606, 5_000_000_000)
        db.commit()
        ratings.entwerten(db, MediaType.movie, 606, 50_000_000_000)
        db.commit()

    admin_client.put(
        "/api/feedback/movie/606", json={"rating": 4, "title": "M"}, headers=zuschauer
    )
    liste = admin_client.get("/api/feedback/mine", headers=zuschauer).json()
    eintrag = next(e for e in liste if e["tmdb_id"] == 606)
    assert eintrag["outdated"] is False
    assert eintrag["rating"] == 4


# --- Statistik --------------------------------------------------------------


def test_veraltete_zaehlen_nicht_mit(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    """Die Seite beantwortet "wie zufrieden sind die Leute mit dem, was hier
    liegt" - ein Urteil über eine Datei, die es nicht mehr gibt, verfälscht
    genau diese Antwort."""
    besteller, zuschauer = zwei_konten
    admin_client.put(
        "/api/feedback/movie/700", json={"rating": 1, "title": "A"}, headers=zuschauer
    )
    admin_client.put(
        "/api/feedback/movie/701", json={"rating": 5, "title": "B"}, headers=besteller
    )

    with SessionLocal() as db:
        ratings.entwerten(db, MediaType.movie, 700, 5_000_000_000)
        db.commit()
        ratings.entwerten(db, MediaType.movie, 700, 50_000_000_000)
        db.commit()

        gesamt = stats.collect(db)["totals"]
        assert gesamt.ratings == 1
        assert gesamt.average_rating == 5.0


# --- Antworten --------------------------------------------------------------


def test_admin_antwortet(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    _, zuschauer = zwei_konten
    eintrag = admin_client.put(
        "/api/feedback/movie/800",
        json={"rating": 2, "comment": "Ton fehlt.", "title": "A"},
        headers=zuschauer,
    ).json()

    antwort = admin_client.post(
        f"/api/feedback/{eintrag['id']}/reply", json={"reply": "Neu geholt, sorry."}
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["reply"] == "Neu geholt, sorry."

    with SessionLocal() as db:
        assert (
            db.query(Notification)
            .filter(Notification.type == NotificationType.feedback_reply)
            .count()
            == 1
        )


def test_bewertung_verschwindet_mit_dem_konto(
    admin_client: TestClient, zwei_konten: tuple[dict[str, str], dict[str, str]]
) -> None:
    _, zuschauer = zwei_konten
    admin_client.put(
        "/api/feedback/movie/900", json={"rating": 3, "title": "A"}, headers=zuschauer
    )
    with SessionLocal() as db:
        db.delete(db.query(User).filter(User.username == "zuschauer").one())
        db.commit()
        assert db.query(TitleRating).filter(TitleRating.tmdb_id == 900).count() == 0
