"""Konten auf null setzen: alles ins Haus - haus-weit und je Konto.

Bis 0.19 lief das als **Nebenwirkung** beim Umschalten der Betriebsart
(Anzahl <-> Speicher). Die Betriebsart gibt es nicht mehr, der Vorgang bleibt:
Er ist der Weg, eine gewachsene Zurechnung zu verwerfen, bevor zum ersten Mal
wirklich begrenzt wird - sonst waere jemand schlagartig ueberzogen wegen einer
Historie, von der er nicht wusste, dass sie mitzaehlt.

⚠️ Etwas, das die Zurechnung des ganzen Hauses verwirft, gehoert an einen
**eigenen Knopf** und nicht an das Speichern einer Einstellung.

Genauso wichtig ist, was der Vorgang *nicht* tut - keine Datei wird angefasst,
gespeicherte Grenzen bleiben stehen. Beides ist hier festgenagelt.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaType,
    QualityTier,
    StorageEntry,
    StorageState,
    User,
)
from app.services import storage

from .conftest import create_user

GB = 1024**3


def _posten(
    db,
    *,
    user_id: int | None,
    state: StorageState,
    tmdb: int,
    bytes_: int = 8 * GB,
) -> int:
    zeile = StorageEntry(
        key=f"movie:standard:tmdb:{tmdb}",
        user_id=user_id,
        media_type=MediaType.movie,
        tier=QualityTier.standard,
        tmdb_id=tmdb,
        title=f"Film {tmdb}",
        size_bytes=bytes_,
        state=state,
    )
    db.add(zeile)
    db.commit()
    return zeile.id


def test_alles_ins_haus_leert_jedes_konto(admin_client: TestClient) -> None:
    """⚠️ **Die Regel selbst.**

    Auch die abgegebenen, noch unentschiedenen Posten gehen mit: Wer abgegeben
    hat, wollte die Belastung loswerden - genau das erledigt der Vorgang. Die
    Warteschlange ist danach leer.
    """
    konto = create_user(admin_client, "kim", "passwort-1234", storage_limit_gb=50)
    with SessionLocal() as db:
        eigen = _posten(db, user_id=konto["id"], state=StorageState.owned, tmdb=1)
        abgegeben = _posten(db, user_id=konto["id"], state=StorageState.pending, tmdb=2)
        haus = _posten(db, user_id=None, state=StorageState.house, tmdb=3)

    antwort = admin_client.post("/api/storage/umbuchung")
    assert antwort.status_code == 200
    assert antwort.json() == {"count": 2, "bytes": 16 * GB}

    with SessionLocal() as db:
        for posten_id in (eigen, abgegeben, haus):
            zeile = db.get(StorageEntry, posten_id)
            assert zeile.user_id is None
            assert zeile.state == StorageState.house
            assert zeile.released_at is None
            # **Keine Datei wird angefasst** - der Posten selbst bleibt, mit
            # Groesse und Titel. Es aendert sich nur, wem er zugerechnet wird.
            assert zeile.size_bytes > 0
        assert storage.offene_abgaben(db) == []
        # Die gespeicherte Grenze uebersteht den Vorgang - sie gilt weiter.
        assert db.get(User, konto["id"]).storage_limit_gb == 50


def test_ein_konto_einzeln_zuruecksetzen(admin_client: TestClient) -> None:
    """Der Ausweg aus dem **Geisterposten** - und er trifft nur diesen einen.

    Wer einen ueber Nexview angefragten Titel aus Radarr wirft und die Datei
    behaelt, bleibt dafuer belastet: Nexview loescht ausschliesslich ueber
    Radarr/Sonarr und kommt an die Datei nicht mehr heran. Ohne diesen Knopf
    sitzt der Betroffene auf einer Belastung, die er nie wieder loswird.
    """
    kim = create_user(admin_client, "kim", "passwort-1234", storage_limit_gb=50)
    alex = create_user(admin_client, "alex", "passwort-1234")
    with SessionLocal() as db:
        seiner = _posten(db, user_id=kim["id"], state=StorageState.owned, tmdb=1)
        fremder = _posten(db, user_id=alex["id"], state=StorageState.owned, tmdb=2)

    antwort = admin_client.post(f"/api/users/{kim['id']}/storage/reset")
    assert antwort.status_code == 200

    with SessionLocal() as db:
        assert db.get(StorageEntry, seiner).user_id is None
        assert db.get(StorageEntry, seiner).state == StorageState.house
        # Der andere bleibt unberuehrt - sonst waere es der haus-weite Knopf.
        assert db.get(StorageEntry, fremder).user_id == alex["id"]
        assert db.get(User, kim["id"]).storage_limit_gb == 50


def test_speichern_einer_einstellung_ruehrt_nichts_an(admin_client: TestClient) -> None:
    """Kein Pardon als Nebenwirkung.

    Bis 0.19 setzte ein Wechsel der Betriebsart still alle Konten zurueck.
    Jetzt aendert das Speichern einer Vorgabe nur die Vorgabe - was jemandem
    zugerechnet ist, bleibt es.
    """
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], state=StorageState.owned, tmdb=1)

    admin_client.put("/api/settings", json={"storage_default_limit_gb": 200})
    admin_client.put("/api/settings", json={"quota_default_movies": 3})

    with SessionLocal() as db:
        zeile = db.get(StorageEntry, posten_id)
        assert zeile.user_id == konto["id"]
        assert zeile.state == StorageState.owned


def test_vorschau_nennt_die_zahlen(admin_client: TestClient) -> None:
    """Der Dialog davor nennt Zahlen, keine Allgemeinplaetze.

    Gezaehlt wird nur, was jemandem zugerechnet ist - der Hausbestand geht ja
    nirgendwo hin.
    """
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _posten(db, user_id=konto["id"], state=StorageState.owned, tmdb=1, bytes_=3 * GB)
        _posten(db, user_id=konto["id"], state=StorageState.pending, tmdb=2, bytes_=2 * GB)
        _posten(db, user_id=None, state=StorageState.house, tmdb=3, bytes_=99 * GB)

    antwort = admin_client.get("/api/storage/umbuchung")
    assert antwort.status_code == 200
    assert antwort.json() == {"count": 2, "bytes": 5 * GB}


def test_vorschau_nur_fuer_admins(arr_client: TestClient) -> None:
    from .conftest import auth_headers

    create_user(arr_client, "kim", "passwort-1234")
    kopf = auth_headers(arr_client, "kim", "passwort-1234")
    assert arr_client.get("/api/storage/umbuchung", headers=kopf).status_code == 403
