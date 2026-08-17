"""Entdecken, Suchen und Details - im Demo-Modus (ohne echten TMDB-Zugriff)."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from .conftest import auth_headers, create_user


def test_ohne_anmeldung_kein_zugriff(client: TestClient) -> None:
    assert client.get("/api/discover/movie").status_code == 401


def test_demo_modus_ist_ohne_tmdb_key_aktiv(admin_client: TestClient) -> None:
    config = admin_client.get("/api/config").json()
    assert config["using_demo_data"] is True
    assert config["tmdb_configured"] is False


def test_filme_und_serien_kommen_getrennt(admin_client: TestClient) -> None:
    movies = admin_client.get("/api/discover/movie").json()
    series = admin_client.get("/api/discover/tv").json()

    assert movies["demo"] is True
    assert movies["total_results"] > 0
    assert series["total_results"] > 0
    assert {item["media_type"] for item in movies["items"]} == {"movie"}
    assert {item["media_type"] for item in series["items"]} == {"tv"}


def test_karte_enthaelt_alle_angaben_fuer_das_frontend(admin_client: TestClient) -> None:
    item = admin_client.get("/api/discover/movie").json()["items"][0]

    for field in (
        "title",
        "overview",
        "poster_url",
        "release_date",
        "vote_average",
        "genres",
        "runtime_minutes",
        "certification",
        "status",
    ):
        assert item[field] not in (None, "", []), f"Feld '{field}' fehlt auf der Karte"
    assert item["status"] == "not_requested"


def test_serien_liefern_eine_tvdb_id(admin_client: TestClient) -> None:
    # Die TVDB-Id wird später für Sonarr gebraucht.
    items = admin_client.get("/api/discover/tv").json()["items"]
    assert all(item["tvdb_id"] for item in items)


def test_ungueltiger_medientyp_wird_abgelehnt(admin_client: TestClient) -> None:
    assert admin_client.get("/api/discover/buecher").status_code == 422


def test_zeitraum_filter(admin_client: TestClient) -> None:
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    page = admin_client.get(f"/api/discover/movie?date_from={cutoff}").json()

    assert page["total_results"] > 0
    assert all(item["release_date"] >= cutoff for item in page["items"])
    # Der Filter muss tatsächlich etwas wegnehmen.
    assert page["total_results"] < admin_client.get("/api/discover/movie").json()["total_results"]


def test_sprachfilter(admin_client: TestClient) -> None:
    page = admin_client.get("/api/discover/movie?language=de").json()
    assert page["total_results"] > 0
    assert {item["original_language"] for item in page["items"]} == {"de"}


def test_genrefilter(admin_client: TestClient) -> None:
    genres = admin_client.get("/api/genres/movie").json()
    drama = next(genre for genre in genres if genre["name"] == "Drama")

    page = admin_client.get(f"/api/discover/movie?genre_id={drama['id']}").json()
    assert page["total_results"] > 0
    assert all("Drama" in item["genres"] for item in page["items"])


def test_nur_bekannte_titel(admin_client: TestClient) -> None:
    """Ohne Untergrenze stehen bei TMDB fast nur Kleinstproduktionen oben -
    gemessen 0 von 20 Treffern mit Beschreibung."""
    alle = admin_client.get("/api/discover/movie?date_from=2020-01-01").json()
    bekannt = admin_client.get(
        "/api/discover/movie?date_from=2020-01-01&known_only=true"
    ).json()

    assert bekannt["total_results"] <= alle["total_results"]
    # In den Demo-Daten haben alle Titel genug Stimmen - es darf also nichts
    # verschwinden, aber der Parameter muss angenommen werden.
    assert all(item["vote_count"] >= 20 for item in bekannt["items"])


def test_sortierung_nach_bewertung(admin_client: TestClient) -> None:
    items = admin_client.get("/api/discover/movie?sort=rating").json()["items"]
    ratings = [item["vote_average"] for item in items]
    assert ratings == sorted(ratings, reverse=True)


def test_sortierung_nach_datum_ist_standard(admin_client: TestClient) -> None:
    items = admin_client.get("/api/discover/movie").json()["items"]
    dates = [item["release_date"] for item in items]
    assert dates == sorted(dates, reverse=True)


def test_unbekannte_sortierung_wird_abgelehnt(admin_client: TestClient) -> None:
    assert admin_client.get("/api/discover/movie?sort=egal").status_code == 422


def test_suche_findet_titel(admin_client: TestClient) -> None:
    page = admin_client.get("/api/search/movie?q=nordlicht").json()
    assert page["total_results"] == 1
    assert page["items"][0]["title"] == "Nordlicht"


def test_suche_ohne_treffer(admin_client: TestClient) -> None:
    assert admin_client.get("/api/search/movie?q=zzzznichts").json()["total_results"] == 0


def test_suche_braucht_mindestens_zwei_zeichen(admin_client: TestClient) -> None:
    assert admin_client.get("/api/search/movie?q=a").status_code == 422


def test_detailansicht(admin_client: TestClient) -> None:
    first = admin_client.get("/api/discover/movie").json()["items"][0]
    detail = admin_client.get(f"/api/media/movie/{first['tmdb_id']}").json()

    assert detail["title"] == first["title"]
    assert detail["runtime_minutes"] == first["runtime_minutes"]


def test_detailansicht_unbekannter_titel(admin_client: TestClient) -> None:
    assert admin_client.get("/api/media/movie/12345").status_code == 404


def test_demo_poster_wird_erzeugt(admin_client: TestClient) -> None:
    first = admin_client.get("/api/discover/movie").json()["items"][0]

    # Bilder werden vom <img>-Element ohne Anmelde-Token geladen.
    response = admin_client.get(first["poster_url"], headers={"Authorization": ""})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert first["title"] in response.text


def test_normaler_benutzer_darf_entdecken(admin_client: TestClient) -> None:
    create_user(admin_client, "kim")
    headers = auth_headers(admin_client, "kim", "passwort-1234")

    assert admin_client.get("/api/discover/movie", headers=headers).status_code == 200
    # ... aber nicht an die Einstellungen.
    assert admin_client.get("/api/settings", headers=headers).status_code == 403
