"""Altersbeschraenkung: Normalisierung, Auswahl des Landes und die Sperre selbst."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus, Role, User
from app.services import age_rating, cache, media
from app.services.settings_service import for_user, load_settings

from .conftest import auth_headers, create_user


def film(einstufungen: dict[str, str]) -> dict:
    """Ein Filmdatensatz mit den angegebenen Einstufungen je Land."""
    return {
        "release_dates": {
            "results": [
                {"iso_3166_1": land, "release_dates": [{"certification": wert}]}
                for land, wert in einstufungen.items()
            ]
        }
    }


def serie(einstufungen: dict[str, str]) -> dict:
    return {
        "content_ratings": {
            "results": [
                {"iso_3166_1": land, "rating": wert} for land, wert in einstufungen.items()
            ]
        }
    }


# --- Einzelne Bezeichnungen in eine Zahl ------------------------------------


@pytest.mark.parametrize(
    ("land", "bezeichnung", "erwartet"),
    [
        # Reine Zahlen und Zahlen mit Beiwerk - die Rueckfallebene.
        ("DE", "12", 12),
        ("DE", "0", 0),
        ("GB", "12A", 12),
        ("AU", "MA15+", 15),
        ("FI", "K-16", 16),
        ("IT", "VM14", 14),
        ("RU", "18+", 18),
        ("BR", "14 anos", 14),
        ("PT", "M/6", 6),
        ("SG", "R21", 21),
        # Buchstaben, die je Land etwas anderes bedeuten.
        ("US", "G", 0),
        ("US", "R", 17),
        ("CA", "R", 18),
        ("US", "PG-13", 13),
        ("NL", "AL", 0),
        ("MX", "C", 18),
        ("HK", "III", 18),
        # "Nicht eingestuft" darf niemals als "ab 0" durchgehen.
        ("US", "NR", None),
        ("HU", "Unrated", None),
        ("DE", "", None),
        # Unbekannte Buchstaben bleiben unbekannt, statt geraten zu werden.
        ("BG", "C", None),
    ],
)
def test_bezeichnung_wird_zu_mindestalter(
    land: str, bezeichnung: str, erwartet: int | None
) -> None:
    assert age_rating.stufe(land, bezeichnung) == erwartet


def test_unsinnig_hohe_zahl_gilt_nicht_als_alter() -> None:
    """Sonst wuerde eine Jahreszahl in der Bezeichnung zur Altersangabe."""
    assert age_rating.stufe("XX", "ab 1999") is None


# --- Welches Land entscheidet ------------------------------------------------


def test_eigenes_land_gewinnt_auch_wenn_andere_strenger_sind() -> None:
    daten = film({"DE": "12", "US": "R", "MX": "D"})
    assert age_rating.mindestalter(daten, "movie", "DE") == 12


def test_ohne_eigene_einstufung_zaehlt_die_strengste() -> None:
    daten = film({"US": "PG", "FR": "16", "GB": "12"})
    assert age_rating.mindestalter(daten, "movie", "DE") == 16


def test_ganz_ohne_einstufung_gibt_es_keine_zahl() -> None:
    assert age_rating.mindestalter(film({}), "movie", "DE") is None
    assert age_rating.mindestalter(film({"US": "NR"}), "movie", "DE") is None


def test_serien_werden_aus_content_ratings_gelesen() -> None:
    assert age_rating.mindestalter(serie({"DE": "16", "US": "TV-G"}), "tv", "DE") == 16


def test_strengster_eintrag_innerhalb_eines_landes_zaehlt() -> None:
    """Kino, Datentraeger und Fernsehen koennen sich unterscheiden."""
    daten = {
        "release_dates": {
            "results": [
                {
                    "iso_3166_1": "DE",
                    "release_dates": [{"certification": "12"}, {"certification": "16"}],
                }
            ]
        }
    }
    assert age_rating.mindestalter(daten, "movie", "DE") == 16


def test_strengster_eintrag_auch_bei_serien() -> None:
    """Auch ``content_ratings`` kann ein Land mehrfach nennen.

    Gemessen an echten TMDB-Daten: Gravity Falls steht unter DE zweimal drin,
    erst "12", dann "6". Eine einfache Zuweisung nahm den letzten - aus FSK 12
    wurde FSK 6, und ein Sechsjaehriger bekam die Serie zu sehen. Der Filter
    selbst war in Ordnung; nur diese Zusammenfassung nicht.

    Bewusst mit der **strengeren zuerst**: In der umgekehrten Reihenfolge waere
    der Fehler nie aufgefallen.
    """
    daten = {
        "content_ratings": {
            "results": [
                {"iso_3166_1": "DE", "rating": "12"},
                {"iso_3166_1": "DE", "rating": "6"},
            ]
        }
    }
    assert age_rating.alle_stufen(daten, "tv")["DE"] == 12
    assert age_rating.mindestalter(daten, "tv", "DE") == 12
    # Und damit bleibt die Serie fuer ein sechsjaehriges Kind gesperrt.
    assert age_rating.erlaubt(daten, "tv", 6, "DE") is False
    assert age_rating.erlaubt(daten, "tv", 12, "DE") is True


# --- Die Sperre selbst -------------------------------------------------------


def test_ohne_grenze_ist_alles_erlaubt() -> None:
    assert age_rating.erlaubt(film({"DE": "18"}), "movie", None, "DE") is True
    # Auch dann, wenn es gar keine Daten gibt.
    assert age_rating.erlaubt(None, "movie", None, "DE") is True


def test_grenze_laesst_gleiches_alter_durch_und_hoeheres_nicht() -> None:
    assert age_rating.erlaubt(film({"DE": "12"}), "movie", 12, "DE") is True
    assert age_rating.erlaubt(film({"DE": "6"}), "movie", 12, "DE") is True
    assert age_rating.erlaubt(film({"DE": "16"}), "movie", 12, "DE") is False


def test_ohne_einstufung_bleibt_es_verborgen() -> None:
    """Kein Nachweis, kein Zutritt - sonst waere die Sperre dort wirkungslos,
    wo niemand weiss, was drinsteckt."""
    assert age_rating.erlaubt(film({}), "movie", 12, "DE") is False
    assert age_rating.erlaubt(None, "movie", 12, "DE") is False


def test_unbewertete_koennen_auch_durchgelassen_werden() -> None:
    """Abschaltbar, weil neue Titel meist noch nirgends eingestuft sind -
    gemessen schrumpfte die Entdecken-Seite dadurch von 20 auf 2."""
    assert age_rating.erlaubt(film({}), "movie", 12, "DE", unbewertet_verbergen=False) is True
    assert age_rating.erlaubt(None, "movie", 12, "DE", unbewertet_verbergen=False) is True
    # An der eigentlichen Grenze aendert der Schalter nichts.
    assert (
        age_rating.erlaubt(film({"DE": "18"}), "movie", 12, "DE", unbewertet_verbergen=False)
        is False
    )


def test_vierzehnjaehriger_sieht_bis_zwoelf_aber_nicht_sechzehn() -> None:
    """Ein freies Alter, keine Auswahl der FSK-Stufen."""
    assert age_rating.erlaubt(film({"DE": "12"}), "movie", 14, "DE") is True
    assert age_rating.erlaubt(film({"DE": "16"}), "movie", 14, "DE") is False


# --- Die Pruef-Region darf nicht die selbst gewaehlte sein --------------------


def test_pruef_region_faellt_auf_die_admin_vorgabe_zurueck_nicht_auf_die_eigene(
    admin_client: TestClient,
) -> None:
    """Der Kern der Sperre.

    Wer sein Land selbst umstellen kann, koennte sonst eines waehlen, in dem
    der Titel nicht eingestuft ist - und waere an der Sperre vorbei.
    """
    create_user(admin_client, "kind", age=12, discover_region="US", rating_region=None)

    with SessionLocal() as session:
        benutzer = session.query(User).filter(User.username == "kind").one()
        roh = load_settings(session)
        settings = for_user(roh, benutzer)

    # Die persoenliche Region wirkt weiterhin auf Kinostarts ...
    assert settings.default_region == "US"
    # ... aber die Altersbeurteilung folgt der Vorgabe des Admins.
    assert settings.rating_region == roh.default_region
    assert settings.rating_region != "US"
    assert settings.age_limit == 12


def test_eigene_pruef_region_wird_verwendet_wenn_der_admin_eine_setzt(
    admin_client: TestClient,
) -> None:
    create_user(admin_client, "kind2", age=16, discover_region="US", rating_region="FR")

    with SessionLocal() as session:
        benutzer = session.query(User).filter(User.username == "kind2").one()
        settings = for_user(load_settings(session), benutzer)

    assert settings.rating_region == "FR"


# --- Nur der Admin darf beschraenken -----------------------------------------


def test_benutzer_kann_seine_beschraenkung_nicht_selbst_aufheben(
    client: TestClient, admin_client: TestClient
) -> None:
    """Der wichtigste Test der ganzen Datei."""
    create_user(admin_client, "kind3", age=6)
    headers = auth_headers(client, "kind3", "passwort-1234")

    # Beide Felder werden von ``ProfileUpdate`` schlicht nicht angenommen.
    client.patch("/api/auth/me", json={"age": 18, "rating_region": "XX"}, headers=headers)

    with SessionLocal() as session:
        benutzer = session.query(User).filter(User.username == "kind3").one()
        assert benutzer.age == 6
        assert benutzer.rating_region is None


def test_vollwertige_konten_haben_keine_altersgrenze_mehr(
    admin_client: TestClient,
) -> None:
    """Die Grenze gibt es nur noch am Kinderkonto.

    Frueher konnte der Administrator jedem Konto ein Alter geben. Zwei Wege zu
    derselben Sperre waeren zwei Stellen, an denen sie auseinanderlaeuft - wer
    ein vollwertiges Konto hat, gilt jetzt als volljaehrig.
    """
    benutzer = create_user(admin_client, "kind4")
    assert benutzer["age"] is None

    # Das Feld gibt es in der Benutzerverwaltung nicht mehr.
    abgelehnt = admin_client.patch(f"/api/users/{benutzer['id']}", json={"age": 12})
    assert abgelehnt.status_code == 200
    assert abgelehnt.json()["age"] is None

    # Am Kinderkonto dagegen sehr wohl - dort haengt die Sperre.
    admin_client.patch(
        f"/api/users/{benutzer['id']}", json={"can_manage_children": True}
    )
    kopf = auth_headers(admin_client, "kind4", "passwort-1234")
    kind = admin_client.post(
        "/api/children",
        json={"username": "kind4-kind", "password": "kind-passwort", "age": 12},
        headers=kopf,
    )
    assert kind.status_code == 201, kind.text
    assert kind.json()["age"] == 12


# --- Wirkt die Sperre auf einer Liste? ---------------------------------------


def test_gesperrte_titel_verschwinden_aus_einer_liste(admin_client: TestClient) -> None:
    """Ueber den Zwischenspeicher, damit kein TMDB-Zugriff noetig ist."""
    create_user(admin_client, "kind5", age=12)

    with SessionLocal() as session:
        benutzer = session.query(User).filter(User.username == "kind5").one()
        settings = for_user(load_settings(session), benutzer)

        # Den Schluessel nicht von Hand bauen: Er traegt eine Fassungsnummer,
        # und ein selbst getippter Schluessel wuerde beim naechsten Hochzaehlen
        # still danebenliegen - der Test schluege dann fehl, ohne dass etwas
        # kaputt waere.
        def schluessel(tmdb_id: int) -> str:
            return media._schlanker_schluessel(
                settings, "movie", tmdb_id, settings.default_region
            )

        for tmdb_id, freigabe in ((1, "6"), (2, "18"), (3, "12")):
            cache.write(
                session, schluessel(tmdb_id), film({"DE": freigabe}), cache.DETAIL_TTL
            )
        # Ein vierter ganz ohne Einstufung.
        cache.write(session, schluessel(4), film({}), cache.DETAIL_TTL)
        session.commit()

        erlaubt = asyncio.run(
            media.erlaubte_kennungen(session, settings, "movie", [1, 2, 3, 4])
        )

    assert erlaubt == {1, 3}


def test_startseite_verwechselt_gesperrt_nicht_mit_unauffindbar(
    arr_client: TestClient,
) -> None:
    """Beides ist ein 404 - behandelt werden muss es verschieden.

    Ein Titel, den TMDB nicht kennt, soll mit dem gespeicherten Namen stehen
    bleiben; ein gesperrter muss ganz verschwinden. Als die Startseite nur auf
    den Statuscode sah, war sie im Demo-Modus komplett leer.
    """
    jetzt = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        benutzer = session.query(User).filter(User.role == Role.admin).first()
        assert benutzer is not None
        session.add(
            MediaRequest(
                user_id=benutzer.id,
                media_type=MediaType.movie,
                tmdb_id=987654,
                title="Titel aus der Anfragetabelle",
                status=RequestStatus.downloaded,
                completed_at=jetzt,
            )
        )
        session.commit()

    eintraege = arr_client.get("/api/home/recent").json()
    assert [e["title"] for e in eintraege] == ["Titel aus der Anfragetabelle"]


def test_ohne_beschraenkung_wird_gar_nicht_erst_geprueft(admin_client: TestClient) -> None:
    """Der Normalfall darf nichts kosten - kein Zwischenspeicher, keine Abfrage."""
    create_user(admin_client, "erwachsen")

    with SessionLocal() as session:
        benutzer = session.query(User).filter(User.username == "erwachsen").one()
        settings = for_user(load_settings(session), benutzer)
        erlaubt = asyncio.run(
            media.erlaubte_kennungen(session, settings, "movie", [7, 8, 9])
        )

    assert erlaubt == {7, 8, 9}
