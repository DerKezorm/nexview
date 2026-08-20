"""Einstellungen: Geheimnisse dürfen den Server nie im Klartext verlassen."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Setting

from .conftest import auth_headers, create_user


def test_standardwerte(admin_client: TestClient) -> None:
    body = admin_client.get("/api/settings").json()
    assert body["default_region"] == "DE"
    assert body["default_language"] == "de"
    assert body["tmdb_api_key_set"] is False
    assert body["demo_mode"] == "auto"


def test_key_wird_nur_maskiert_zurueckgegeben(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json={"tmdb_api_key": "geheimer-key-abcd1234"})

    body = admin_client.get("/api/settings").json()
    assert body["tmdb_api_key_set"] is True
    assert body["tmdb_api_key"] == "••••1234"
    assert "geheimer-key" not in admin_client.get("/api/settings").text


def test_key_liegt_verschluesselt_in_der_datenbank(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json={"tmdb_api_key": "geheimer-key-abcd1234"})

    with SessionLocal() as session:
        stored = session.scalar(select(Setting).where(Setting.key == "tmdb_api_key"))

    assert stored is not None
    assert stored.is_secret is True
    assert "geheimer-key" not in (stored.value or "")
    assert (stored.value or "").startswith("enc:")


def test_maskierter_wert_loescht_den_key_nicht(admin_client: TestClient) -> None:
    """Die Oberfläche schickt beim Speichern den maskierten Wert zurück -
    das darf den hinterlegten Key nicht überschreiben."""
    admin_client.put("/api/settings", json={"tmdb_api_key": "geheimer-key-abcd1234"})
    admin_client.put("/api/settings", json={"tmdb_api_key": "••••1234", "default_region": "AT"})

    body = admin_client.get("/api/settings").json()
    assert body["tmdb_api_key_set"] is True
    assert body["default_region"] == "AT"


def test_leerer_wert_laesst_key_unveraendert(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json={"tmdb_api_key": "geheimer-key-abcd1234"})
    admin_client.put("/api/settings", json={"tmdb_api_key": ""})
    assert admin_client.get("/api/settings").json()["tmdb_api_key_set"] is True


def test_demo_modus_endet_wenn_ein_key_gesetzt_ist(admin_client: TestClient) -> None:
    assert admin_client.get("/api/config").json()["using_demo_data"] is True

    admin_client.put("/api/settings", json={"tmdb_api_key": "irgendein-key"})
    config = admin_client.get("/api/config").json()
    assert config["tmdb_configured"] is True
    assert config["using_demo_data"] is False


def test_demo_modus_laesst_sich_erzwingen(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json={"tmdb_api_key": "irgendein-key", "demo_mode": "on"})
    assert admin_client.get("/api/config").json()["using_demo_data"] is True


def test_ungueltiger_demo_modus(admin_client: TestClient) -> None:
    assert admin_client.put("/api/settings", json={"demo_mode": "vielleicht"}).status_code == 422


def test_key_laesst_sich_gezielt_entfernen(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json={"tmdb_api_key": "geheimer-key-abcd1234"})
    assert admin_client.get("/api/settings").json()["tmdb_api_key_set"] is True

    body = admin_client.delete("/api/settings/secret/tmdb_api_key").json()
    assert body["tmdb_api_key_set"] is False
    # Ohne Key greift wieder der Demo-Modus.
    assert admin_client.get("/api/config").json()["using_demo_data"] is True


def test_unbekanntes_geheimnis_kann_nicht_geloescht_werden(admin_client: TestClient) -> None:
    assert admin_client.delete("/api/settings/secret/passwort").status_code == 422


def test_verbindungstest_ohne_key(admin_client: TestClient) -> None:
    result = admin_client.post("/api/settings/test/tmdb", json={}).json()
    assert result["ok"] is False


def test_normaler_benutzer_kommt_nicht_an_die_einstellungen(admin_client: TestClient) -> None:
    create_user(admin_client, "kim")
    headers = auth_headers(admin_client, "kim", "passwort-1234")

    assert admin_client.get("/api/settings", headers=headers).status_code == 403
    assert admin_client.put("/api/settings", json={}, headers=headers).status_code == 403
    # Die allgemeine Konfiguration darf er sehen (für Hinweise in der Oberfläche).
    assert admin_client.get("/api/config", headers=headers).status_code == 200


def test_jede_einstellung_kommt_auch_zurueck(admin_client: TestClient) -> None:
    """Was sich speichern laesst, muss sich auch wieder lesen lassen.

    ``public_settings`` zaehlt jeden Schluessel einzeln auf - wer einen neuen
    ergaenzt und diese Liste vergisst, baut eine besonders gemeine Falle: Die
    Oberflaeche bekommt ``undefined``, zeigt deshalb immer den Vorgabewert an
    und meldet "nicht geaendert". Gespeichert wird trotzdem richtig, nur sieht
    es niemand - es wirkt, als griffe die Einstellung nicht.

    Genau das ist mit den drei Merklisten-Schluesseln passiert: In der
    Datenbank stand "an" und "4K", die Seite zeigte "aus" und "Standard", und
    der Abgleich richtete sich nach dem Gespeicherten.
    """
    from app.routers.settings import SettingsUpdate

    antwort = admin_client.get("/api/settings")
    assert antwort.status_code == 200
    sichtbar = set(antwort.json())

    fehlend = [
        name
        for name in SettingsUpdate.model_fields
        # Geheimnisse erscheinen maskiert plus einem "_set"-Merker.
        if name not in sichtbar and f"{name}_set" not in sichtbar
    ]
    assert not fehlend, (
        "Diese Einstellungen lassen sich speichern, aber nicht zurücklesen: "
        f"{sorted(fehlend)}"
    )


def test_merklisten_einstellung_ueberlebt_das_speichern(admin_client: TestClient) -> None:
    """Der konkrete Fall, damit er nicht ein zweites Mal zurueckkommt."""
    antwort = admin_client.put("/api/settings", json={"watchlist_enabled": True})
    assert antwort.status_code == 200, antwort.text

    assert admin_client.get("/api/settings").json()["watchlist_enabled"] is True


def test_unlesbare_geheimnisse_werden_gemeldet(admin_client: TestClient) -> None:
    """Ein Schluesselwechsel darf nicht wie "Verbindung weg" aussehen.

    ``decrypt`` liefert bei nicht mehr passendem Geheimschluessel ein leeres
    Ergebnis - frueher voellig stumm. Die Folgen sahen aus wie drei
    verschiedene Fehler (Plex-Verbindung verschwunden, TMDB im Demo-Modus,
    Radarr "nicht eingerichtet"), und zwei Betreiber haben genau das als
    Raetsel gemeldet. Die Einstellungsseite bekommt deshalb eine ehrliche
    Auskunft: Es gibt gespeicherte Werte, die sich nicht lesen lassen.
    """
    from app.db import SessionLocal
    from app.models import Setting

    assert admin_client.get("/api/settings").json()["secrets_unreadable"] is False

    # Ein Geheimnis, das mit einem *anderen* Schluessel verschluesselt wurde -
    # nachgestellt durch einen Wert, den der aktuelle Schluessel nicht oeffnet.
    with SessionLocal() as session:
        session.merge(
            Setting(key="mediaserver_token", value="enc:kaputt", is_secret=True)
        )
        session.commit()

    daten = admin_client.get("/api/settings").json()
    assert daten["secrets_unreadable"] is True
    # Und die Verbindung gilt folgerichtig als nicht eingerichtet - aber eben
    # mit sichtbarer Ursache statt stillschweigend.
    assert daten["mediaserver_token_set"] is False
