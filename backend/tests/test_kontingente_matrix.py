"""Was der Administrator einstellen kann - und was der Benutzer davon merkt.

Diese Datei geht die **Kombinationen** durch, nicht die Einzelteile. Seit 0.20
gelten Stückzahl und Speicher zusammen, und damit gibt es Zustände, die es
vorher gar nicht geben konnte: Stückzahl leer, Platz voll. Platz leer,
Stückzahl voll. Beides voll. Am Konto etwas anderes als im Haus.

Geprüft wird von außen - über die echten Endpunkte, mit einem angemeldeten
Konto, so wie es der Benutzer erlebt. ⚠️ **Die Meldung muss die Währung
nennen**: Gegen eine volle Stückzahl hilft warten, gegen vollen Platz nur
aufräumen. Wer das nicht liest, räumt vergeblich auf.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaType,
    QualityTier,
    Role,
    StorageEntry,
    StorageState,
)
from app.services.settings_service import save_settings

from .conftest import ADMIN, auth_headers, create_user

GB = 1024**3

# Die 4K-Attrappen laufen unter anderen Adressen als die Standard-Instanz -
# sonst greift die Pruefung "beide Stufen duerfen nicht dieselbe Adresse haben".
UHD_EINSTELLUNGEN = {
    "radarr_uhd_url": "http://127.0.0.1:10",
    "radarr_uhd_api_key": "test-radarr-4k",
    "sonarr_uhd_url": "http://127.0.0.1:10",
    "sonarr_uhd_api_key": "test-sonarr-4k",
}


# --------------------------------------------------------------- Werkzeug


def _haus(client: TestClient, **werte: object) -> None:
    """Die Standardwerte des Hauses setzen - wie der Admin es tut."""
    antwort = client.put("/api/settings", json=werte)
    assert antwort.status_code == 200, antwort.text


def _konto(client: TestClient, name: str = "kim", **felder: object) -> dict:
    return create_user(client, name, "passwort-1234", **felder)


def _kopf(client: TestClient, name: str = "kim") -> dict:
    return auth_headers(client, name, "passwort-1234")


def _belegen(user_id: int, gb: int, *, tier: QualityTier = QualityTier.standard) -> None:
    """Diesem Konto ``gb`` Gigabyte zurechnen."""
    with SessionLocal() as db:
        db.add(
            StorageEntry(
                key=f"movie:{tier.value}:tmdb:{9000 + gb}",
                user_id=user_id,
                media_type=MediaType.movie,
                tier=tier,
                tmdb_id=9000 + gb,
                title=f"Belegung {gb}",
                size_bytes=gb * GB,
                state=StorageState.owned,
            )
        )
        db.commit()


def _anfragen(
    client: TestClient,
    kopf: dict,
    *,
    art: str = "movie",
    index: int = 0,
    tier: str = "standard",
):
    """Eine echte Anfrage stellen - denselben Weg wie die Oberfläche."""
    eintrag = client.get(f"/api/discover/{art}").json()["items"][index]
    return client.post(
        "/api/requests",
        json={
            "media_type": eintrag["media_type"],
            "tmdb_id": eintrag["tmdb_id"],
            "tier": tier,
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=kopf,
    )


# ------------------------------------------------------- Die Vier-Felder-Tafel
#
# Stückzahl frei/voll × Speicher frei/voll. Das ist der Kern des Umbaus: Vorher
# konnte immer nur eine der beiden Spalten überhaupt "voll" sein.


def test_beides_frei_geht_durch(arr_client: TestClient) -> None:
    _haus(arr_client, quota_default_movies=5, storage_default_limit_gb=100)
    _konto(arr_client)

    antwort = _anfragen(arr_client, _kopf(arr_client))
    assert antwort.status_code == 201, antwort.text


def test_stueckzahl_voll_bremst_trotz_freiem_platz(arr_client: TestClient) -> None:
    """Der Fall, den es vorher im GB-Betrieb nicht gab."""
    _haus(arr_client, quota_default_movies=1, storage_default_limit_gb=1000)
    konto = _konto(arr_client)
    kopf = _kopf(arr_client)

    assert _anfragen(arr_client, kopf, index=0).status_code == 201
    _belegen(konto["id"], 5)  # reichlich Luft im Speicher

    zweite = _anfragen(arr_client, kopf, index=1)
    assert zweite.status_code == 429
    assert "Filme" in zweite.json()["detail"]


def test_platz_voll_bremst_trotz_freier_stueckzahl(arr_client: TestClient) -> None:
    """Und die Gegenrichtung - vorher im Anzahl-Betrieb unmöglich."""
    _haus(arr_client, quota_default_movies=50, storage_default_limit_gb=10)
    konto = _konto(arr_client)
    _belegen(konto["id"], 10)  # genau auf der Grenze

    antwort = _anfragen(arr_client, _kopf(arr_client))
    assert antwort.status_code == 429
    assert "Speicher" in antwort.json()["detail"]


def test_beides_voll_nennt_die_stueckzahl_zuerst(arr_client: TestClient) -> None:
    """Eine Meldung, nicht zwei - und die, gegen die Warten hilft.

    Zuerst die Stückzahl zu nennen erspart ein vergebliches Aufräumen: Sie
    erneuert sich von selbst, der Platz nie.
    """
    _haus(arr_client, quota_default_movies=0, storage_default_limit_gb=1)
    konto = _konto(arr_client)
    _belegen(konto["id"], 50)

    antwort = _anfragen(arr_client, _kopf(arr_client))
    assert antwort.status_code == 429
    assert "Filme" in antwort.json()["detail"]
    assert "Speicher" not in antwort.json()["detail"]


# ---------------------------------------------------- Filme und Serien getrennt


def test_volle_filme_halten_serien_nicht_auf(arr_client: TestClient) -> None:
    """Zwei Töpfe, wie eh und je - der Speicher ist einer für beide."""
    _haus(arr_client, quota_default_movies=0, quota_default_series=5)
    _konto(arr_client)
    kopf = _kopf(arr_client)

    assert _anfragen(arr_client, kopf, art="movie").status_code == 429
    assert _anfragen(arr_client, kopf, art="tv").status_code == 201


def test_der_platz_gilt_fuer_beide_medienarten(arr_client: TestClient) -> None:
    """Ein voller Speicher stoppt auch die Serie - er ist nicht geteilt."""
    _haus(arr_client, quota_default_series=99, storage_default_limit_gb=10)
    konto = _konto(arr_client)
    _belegen(konto["id"], 12)

    antwort = _anfragen(arr_client, _kopf(arr_client), art="tv")
    assert antwort.status_code == 429
    assert "Speicher" in antwort.json()["detail"]


# ------------------------------------------------- Konto schlägt Hausvorgabe


def test_unbegrenzt_am_konto_hebt_die_hausgrenze_auf(arr_client: TestClient) -> None:
    _haus(arr_client, quota_default_movies=0)
    konto = _konto(arr_client)
    arr_client.patch(
        f"/api/users/{konto['id']}", json={"quota_movies_limit": "unlimited"}
    )

    assert _anfragen(arr_client, _kopf(arr_client)).status_code == 201


def test_null_am_konto_sperrt_trotz_grosszuegigem_haus(arr_client: TestClient) -> None:
    """⚠️ Die 0 heißt "darf nichts" - nicht "unbegrenzt"."""
    _haus(arr_client, quota_default_movies=99, storage_default_limit_gb=1000)
    konto = _konto(arr_client)
    arr_client.patch(f"/api/users/{konto['id']}", json={"quota_movies_limit": 0})

    antwort = _anfragen(arr_client, _kopf(arr_client))
    assert antwort.status_code == 429
    assert "Filme" in antwort.json()["detail"]


def test_eigene_speichergrenze_schlaegt_das_haus(arr_client: TestClient) -> None:
    _haus(arr_client, storage_default_limit_gb=1000)
    konto = _konto(arr_client)
    arr_client.patch(f"/api/users/{konto['id']}", json={"storage_limit_gb": 5})
    _belegen(konto["id"], 6)

    antwort = _anfragen(arr_client, _kopf(arr_client))
    assert antwort.status_code == 429
    assert "Speicher" in antwort.json()["detail"]


def test_zurueck_auf_standard_holt_die_hausgrenze_wieder(arr_client: TestClient) -> None:
    """Der Weg zurück - ohne ihn wäre "Standard" eine Einbahnstraße."""
    _haus(arr_client, quota_default_movies=99)
    konto = _konto(arr_client)
    arr_client.patch(f"/api/users/{konto['id']}", json={"quota_movies_limit": 0})
    assert _anfragen(arr_client, _kopf(arr_client)).status_code == 429

    arr_client.patch(
        f"/api/users/{konto['id']}", json={"quota_movies_limit": "standard"}
    )
    assert _anfragen(arr_client, _kopf(arr_client)).status_code == 201


# --------------------------------------------------------- Wer keine Grenze hat


def test_der_admin_kommt_durch_jede_null(arr_client: TestClient) -> None:
    """Er setzt die Grenzen und könnte die eigene jederzeit wegklicken."""
    _haus(arr_client, quota_default_movies=0, storage_default_limit_gb=0)
    kopf = auth_headers(arr_client, ADMIN["username"], ADMIN["password"])

    # ⚠️ Nicht auf 201 pruefen: Die Anfrage eines Admins ist sofort freigegeben
    # und geht direkt an Radarr - das gibt es im Test nicht, also endet sie mit
    # 502. Genau das ist der Beweis: Sie ist **durch das Kontingent hindurch**.
    # Ein 429 waere hier der Fehler.
    assert _anfragen(arr_client, kopf).status_code != 429


def test_der_entscheider_hat_sehr_wohl_ein_kontingent(arr_client: TestClient) -> None:
    """⚠️ Entscheider sind **nicht** ausgenommen.

    Sie haben dauerhafte Auto-Freigabe. Wären sie zusätzlich unbegrenzt, wäre
    die Kette geschlossen: selbst anfragen, selbst freigeben, kein Halt.
    """
    _haus(arr_client, quota_default_movies=0)
    _konto(arr_client, "chefin", role=Role.approver)

    antwort = _anfragen(arr_client, _kopf(arr_client, "chefin"))
    assert antwort.status_code == 429


# --------------------------------------------------------------- Die 4K-Achse


@pytest.fixture
def mit_uhd(arr_client: TestClient) -> TestClient:
    with SessionLocal() as db:
        save_settings(db, UHD_EINSTELLUNGEN)
    return arr_client


def test_4k_zaehlt_gegen_dasselbe_stueck_kontingent(mit_uhd: TestClient) -> None:
    """Eine 4K-Anfrage ist keine zweite Währung.

    Sonst hätte jeder das doppelte Kontingent, sobald 4K eingerichtet ist -
    und ausgerechnet die großen Dateien wären die ungezählten.
    """
    _haus(mit_uhd, quota_default_movies=1)
    _konto(mit_uhd, can_request_uhd_movies=True)
    kopf = _kopf(mit_uhd)

    assert _anfragen(mit_uhd, kopf, index=0, tier="standard").status_code == 201
    zweite = _anfragen(mit_uhd, kopf, index=1, tier="uhd")
    assert zweite.status_code == 429
    assert "Filme" in zweite.json()["detail"]


def test_4k_zaehlt_gegen_denselben_speicher(mit_uhd: TestClient) -> None:
    """Und die 4K-Belegung zählt gegen dieselbe Grenze.

    Gerade hier ist es wichtig: 4K-Dateien sind die großen. Ein eigener Topf
    dafür wäre die Grenze, die nie greift, wenn sie gebraucht wird.
    """
    _haus(mit_uhd, quota_default_movies=99, storage_default_limit_gb=10)
    konto = _konto(mit_uhd, can_request_uhd_movies=True)
    _belegen(konto["id"], 40, tier=QualityTier.uhd)

    antwort = _anfragen(mit_uhd, _kopf(mit_uhd), tier="uhd")
    assert antwort.status_code == 429
    assert "Speicher" in antwort.json()["detail"]


# ------------------------------------------------------ Die Kante beim Speicher


def test_wer_noch_luft_hat_darf_auch_etwas_grosses(arr_client: TestClient) -> None:
    """⚠️ Gebremst wird, **wer schon drüber ist** - nicht, was als Nächstes käme.

    Die Größe steht beim Anfragen noch gar nicht fest, und eine Schätzung ist
    keine Grundlage für eine Ablehnung. Wer noch Luft hat, darf anfragen -
    auch wenn es danach ins Minus geht. Erst die *nächste* ist gesperrt.
    """
    _haus(arr_client, storage_default_limit_gb=10)
    konto = _konto(arr_client)
    _belegen(konto["id"], 9)
    kopf = _kopf(arr_client)

    assert _anfragen(arr_client, kopf, index=0).status_code == 201

    _belegen(konto["id"], 40)  # jetzt weit im Minus
    assert _anfragen(arr_client, kopf, index=1).status_code == 429


def test_die_meldung_nennt_zahl_und_grenze(arr_client: TestClient) -> None:
    """Wer gesperrt wird, soll sehen **wie weit** - sonst rät er beim Aufräumen."""
    _haus(arr_client, storage_default_limit_gb=10)
    konto = _konto(arr_client)
    _belegen(konto["id"], 25)

    text = _anfragen(arr_client, _kopf(arr_client)).json()["detail"]
    assert "25" in text and "10" in text


# -------------------------------------------------------------- Kinderkonten


def test_das_kind_verbraucht_das_kontingent_der_eltern(arr_client: TestClient) -> None:
    """Kinderkonten sind Unterprofile - sie haben kein eigenes Kontingent.

    Der Weg ist zweistufig: Das Kind **wünscht** sich etwas, die Eltern geben
    frei. Erst dabei entsteht die Anfrage - auf ihren Namen, mit ihrem
    Kontingent. Damit greifen Stückzahl, Speicher und Freigabeweg von selbst,
    ohne dass es dafür eine zweite Regel bräuchte.
    """
    _haus(arr_client, quota_default_movies=1)
    create_user(arr_client, "elternteil", "eltern-passwort", can_manage_children=True)
    eltern = auth_headers(arr_client, "elternteil", "eltern-passwort")
    arr_client.post(
        "/api/children",
        json={"username": "kind", "password": "kind-passwort", "age": 16},
        headers=eltern,
    )
    kind = auth_headers(arr_client, "kind", "kind-passwort")

    # Die Eltern schöpfen ihr Kontingent aus.
    assert _anfragen(arr_client, eltern, index=0).status_code == 201

    # Das Kind wünscht sich etwas - das geht, es kostet ja noch nichts. Der
    # Titel muss aus **seinen** Rubriken kommen: Der Wunsch-Endpunkt holt ihn
    # mit den Einstellungen des Kindes und weist alles andere als 404 ab.
    kategorien = arr_client.get(
        "/api/kids/categories?media_type=movie", headers=kind
    ).json()
    assert kategorien, "Die Kinder-Startseite ist leer - dann testet hier nichts."
    seite = arr_client.get(
        f"/api/kids/rubrik/{kategorien[0]['rubrik']}?media_type=movie", headers=kind
    ).json()
    assert seite["wuenschbar"], "Nichts zu wuenschen - dann testet hier nichts."
    eintrag = seite["wuenschbar"][0]

    wunsch = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": eintrag["tmdb_id"]},
        headers=kind,
    )
    assert wunsch.status_code == 201, wunsch.text

    # Erst beim Freigeben wird daraus eine Anfrage - und die läuft in die
    # Grenze der Eltern. ⚠️ Der Wunsch bleibt dabei offen.
    antwort = arr_client.post(
        f"/api/children/wishes/{wunsch.json()['id']}/release",
        json={"quality_profile_id": 1, "root_folder_path": "/data/Movies"},
        headers=eltern,
    )
    assert antwort.status_code == 429
    assert "Filme" in antwort.json()["detail"]


# ------------------------------------------------- Was der Entscheider darf
#
# Er sitzt zwischen den Stühlen: Über fremde Anfragen darf er bestimmen, über
# den Hausbestand ausdrücklich nicht.


def test_der_entscheider_darf_keine_abgabe_entscheiden(arr_client: TestClient) -> None:
    """⚠️ **Sonst wäre die Kette geschlossen.**

    Ein Entscheider hat ein Kontingent *und* dauerhafte Auto-Freigabe. Dürfte
    er zusätzlich über Abgaben bestimmen, könnte er sich selbst anfragen,
    selbst abgeben und selbst "Hausbestand" wählen - und hätte damit
    unbegrenzten Speicher, ohne je eine Grenze anzufassen.

    Diese Entscheidungen sind deshalb allein Sache des Administrators.
    """
    _konto(arr_client, "chefin", role=Role.approver)
    kopf = _kopf(arr_client, "chefin")

    konto = _konto(arr_client, "kim")
    _belegen(konto["id"], 8)
    with SessionLocal() as db:
        posten_id = db.query(StorageEntry).filter(
            StorageEntry.user_id == konto["id"]
        ).one().id

    # Ansehen: nein. Entscheiden: erst recht nicht.
    assert arr_client.get("/api/storage/releases", headers=kopf).status_code == 403
    assert (
        arr_client.post(f"/api/storage/entries/{posten_id}/haus", headers=kopf).status_code
        == 403
    )
    assert (
        arr_client.post("/api/storage/umbuchung", headers=kopf).status_code == 403
    )
    assert (
        arr_client.post(
            f"/api/storage/entries/{posten_id}/loeschen", headers=kopf
        ).status_code
        == 403
    )


def test_die_freigabe_prueft_das_kontingent_nicht_erneut(arr_client: TestClient) -> None:
    """Geprüft wird beim **Anfragen**, nicht beim Freigeben - mit Absicht.

    Wer im Rahmen angefragt hat, soll nicht daran scheitern, dass in der
    Zwischenzeit jemand anderes Platz belegt hat oder die Woche umgesprungen
    ist. Die Anfrage lag ja bereits berechtigt in der Warteschlange.

    Der Entscheider sieht den Speicherstand des Anfragenden trotzdem an jeder
    Zeile - das ist die Stelle, an der er es merken *kann*, wenn er will.
    """
    _haus(arr_client, quota_default_movies=5, storage_default_limit_gb=100)
    konto = _konto(arr_client, "kim")
    anfrage = _anfragen(arr_client, _kopf(arr_client, "kim"))
    assert anfrage.status_code == 201

    # Nachträglich weit über die Grenze - die Anfrage liegt schon.
    _belegen(konto["id"], 500)

    _konto(arr_client, "chefin", role=Role.approver)
    kopf = _kopf(arr_client, "chefin")

    # Der Stand steht an der Zeile, damit der Entscheider es sehen kann.
    zeile = arr_client.get("/api/admin/requests", headers=kopf).json()[0]
    assert zeile["storage"]["exhausted"] is True

    # Freigeben geht trotzdem - sie scheitert erst an Radarr, nicht am
    # Kontingent. 429 waere hier der Fehler.
    antwort = arr_client.post(
        f"/api/admin/requests/{anfrage.json()['id']}/approve",
        json={"quality_profile_id": 1, "root_folder_path": "/data/Movies"},
        headers=kopf,
    )
    assert antwort.status_code != 429
