"""Stückzahl **und** Speicher gelten zusammen - seit 0.20.

Bis 0.19 war das ein haus-weites Entweder-oder: Ein Umschalter entschied, ob
die Anzahl oder der belegte Platz zählt. Die Begründung damals war, dass zwei
Gründe zu scheitern die Verwirrung verdoppeln - die Stückzahl erneuert sich
jeden Montag, der Platz nie.

Der Umschalter ist weg. Was ihn ersetzt, steht hier:

* Beide Grenzen gelten immer; eine Anfrage muss durch beide.
* ⚠️ Die Meldung **muss sagen, welche** gegriffen hat - sonst zwingt ein
  "ich kann nichts anfragen" den Administrator zum Raten.
* Wer nur nach einer Währung begrenzen will, stellt die andere auf
  "unbegrenzt". Das ist eine Einstellung weniger als eine Betriebsart.

Dazu die drei Zustände einer Grenze am Konto - Standard, unbegrenzt, Zahl -
und der einmalige Umzug der Bedeutung der **0**.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaType, User
from app.services import quota
from app.services.settings_service import load_settings

from .conftest import auth_headers, create_user


def _grenzen(client: TestClient, **werte: object) -> None:
    """Die Standardwerte des Hauses setzen."""
    antwort = client.put("/api/settings", json=werte)
    assert antwort.status_code == 200


# --------------------------------------------------- Die drei Zustände


def test_ohne_eigene_zahl_gilt_der_standard(admin_client: TestClient) -> None:
    """NULL am Konto heißt "Standard" - und der Standard ist eine echte Grenze."""
    _grenzen(admin_client, quota_default_movies=2)
    konto = create_user(admin_client, "kim", "passwort-1234")

    with SessionLocal() as db:
        person = db.get(User, konto["id"])
        assert quota._limit_for(person, MediaType.movie, load_settings(db)) == 2


def test_unbegrenzt_am_konto_schlaegt_den_standard(admin_client: TestClient) -> None:
    """Der dritte Zustand - **ausdrücklich** ohne Grenze, nicht "nichts gesetzt".

    Ohne ihn gäbe es keinen Weg, ein einzelnes Konto vom Hauswert auszunehmen:
    Ein leeres Feld hieße ja gerade "nimm den Hauswert".
    """
    _grenzen(admin_client, quota_default_movies=2)
    konto = create_user(admin_client, "kim", "passwort-1234")
    admin_client.patch(
        f"/api/users/{konto['id']}", json={"quota_movies_limit": "unlimited"}
    )

    with SessionLocal() as db:
        person = db.get(User, konto["id"])
        assert quota._limit_for(person, MediaType.movie, load_settings(db)) is None


def test_null_heisst_darf_nichts(admin_client: TestClient) -> None:
    """⚠️ Die **0** ist keine Abkürzung für "unbegrenzt", sondern das Gegenteil.

    Bis 0.19 hieß sie beim Speicher genau das - deshalb zieht
    ``db._kontingente_dreiwertig_machen`` gespeicherte Nullen einmalig um.
    """
    konto = create_user(admin_client, "kim", "passwort-1234")
    admin_client.patch(f"/api/users/{konto['id']}", json={"quota_movies_limit": 0})

    with SessionLocal() as db:
        person = db.get(User, konto["id"])
        assert quota._limit_for(person, MediaType.movie, load_settings(db)) == 0


def test_die_worte_kommen_auch_wieder_heraus(admin_client: TestClient) -> None:
    """Die Oberfläche darf nie eine ``-1`` zu sehen bekommen.

    In der Datenbank stehen ``NULL`` und ``-1``; nach außen sind es Wörter.
    Ein Feld, in dem ``-1`` mal "Standard" und mal "unbegrenzt" bedeutet, wäre
    genau die Verwechslung, die niemand bemerkt.
    """
    konto = create_user(admin_client, "kim", "passwort-1234")
    antwort = admin_client.patch(
        f"/api/users/{konto['id']}",
        json={"quota_movies_limit": "unlimited", "storage_limit_gb": 50},
    )

    daten = antwort.json()
    assert daten["quota_movies_limit"] == "unlimited"
    assert daten["quota_series_limit"] == "standard"
    assert daten["storage_limit_gb"] == 50


def test_admins_bleiben_ueberall_unbegrenzt(admin_client: TestClient) -> None:
    """Dieselbe Regel wie bei Sperrliste, Freigabe und 4K.

    Sie setzen die Grenzen und könnten die eigene jederzeit heraufsetzen -
    bliebe sie bestehen, wäre es eine Hürde, die genau eine Person aufhält:
    die, die sie gerade wegklicken kann.
    """
    _grenzen(admin_client, quota_default_movies=0, storage_default_limit_gb=0)

    with SessionLocal() as db:
        chef = db.query(User).filter(User.username == "admin").one()
        einstellungen = load_settings(db)
        assert quota._limit_for(chef, MediaType.movie, einstellungen) is None


# ------------------------------------------------ Beide Grenzen zugleich


def test_die_stueckzahl_bremst_trotz_freiem_speicher(arr_client: TestClient) -> None:
    _grenzen(arr_client, quota_default_movies=0, storage_default_limit_gb=-1)
    create_user(arr_client, "kim", "passwort-1234")
    kopf = auth_headers(arr_client, "kim", "passwort-1234")

    titel = arr_client.get("/api/discover/movie").json()["items"][0]
    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": titel["media_type"],
            "tmdb_id": titel["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=kopf,
    )

    assert antwort.status_code == 429
    # ⚠️ Die Meldung nennt die Währung. Ohne das müsste der Betroffene raten,
    # ob Warten hilft (Stückzahl) oder nur Aufräumen (Platz).
    assert "Filme" in antwort.json()["detail"]


def test_der_zeitraum_kommt_aus_den_einstellungen(admin_client: TestClient) -> None:
    """Haus-weit, nicht je Konto.

    Drei Konten mit drei verschiedenen Zeiträumen erklären niemandem mehr, was
    "3 Filme" bedeutet - und niemand hat es je unterschiedlich gebraucht.
    """
    _grenzen(admin_client, quota_period="month")

    with SessionLocal() as db:
        assert load_settings(db).quota_period.value == "month"

    assert admin_client.get("/api/settings").json()["quota_period"] == "month"
