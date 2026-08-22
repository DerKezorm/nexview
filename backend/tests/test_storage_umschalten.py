"""Der Umschalt-Generalpardon: Betriebsartwechsel setzt die Konten zurueck.

Der Kern der Regel steht im Plan und ist bewusst **symmetrisch**:

> Jedes Umschalten (Anzahl <-> Speicher) bucht alle zugerechneten Posten in
> den Hausbestand um, jedes Konto startet bei null. In beide Richtungen -
> eine Regel statt einer Ausnahme.

Ohne den Pardon waere jemand nach dem Einschalten schlagartig ueberzogen -
wegen einer Historie, von der er nicht wusste, dass sie mitzaehlt: Die
Zuordnung laeuft naemlich **immer**, auch im Anzahl-Betrieb.

Genauso wichtig ist, was der Pardon *nicht* tut - keine Datei wird angefasst,
gespeicherte Grenzen bleiben stehen. Beides ist hier festgenagelt.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

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


def _bestand(admin_client: TestClient) -> tuple[int, int, int]:
    """(zugerechnete Posten, Haus-Posten, offene Abgaben) - der Pruefblick."""
    with SessionLocal() as db:
        zugerechnet = len(
            db.scalars(
                select(StorageEntry).where(StorageEntry.user_id.is_not(None))
            ).all()
        )
        haus = len(
            db.scalars(
                select(StorageEntry).where(StorageEntry.user_id.is_(None))
            ).all()
        )
        offen = len(storage.offene_abgaben(db))
    return zugerechnet, haus, offen


def test_abschalten_bucht_alle_konten_ins_haus(admin_client: TestClient) -> None:
    """⚠️ **Die Regel selbst** - Richtung Speicher -> Anzahl.

    Auch die abgegebenen, noch unentschiedenen Posten gehen mit: Wer abgegeben
    hat, wollte die Belastung loswerden - genau das erledigt der Pardon. Die
    Warteschlange ist danach leer.
    """
    admin_client.put("/api/settings", json={"storage_enabled": True})
    konto = create_user(admin_client, "kim", "passwort-1234", storage_limit_gb=50)
    with SessionLocal() as db:
        eigen = _posten(db, user_id=konto["id"], state=StorageState.owned, tmdb=1)
        abgegeben = _posten(db, user_id=konto["id"], state=StorageState.pending, tmdb=2)
        haus = _posten(db, user_id=None, state=StorageState.house, tmdb=3)

    antwort = admin_client.put("/api/settings", json={"storage_enabled": False})
    assert antwort.status_code == 200

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
        # Die gespeicherte Grenze uebersteht den Wechsel - sie gilt wieder,
        # wenn zurueckgeschaltet wird.
        assert db.get(User, konto["id"]).storage_limit_gb == 50


def test_einschalten_setzt_genauso_zurueck(admin_client: TestClient) -> None:
    """Die Gegenrichtung - **symmetrisch, eine Regel statt einer Ausnahme.**

    Die Zuordnung laeuft auch im Anzahl-Betrieb. Ohne Pardon beim Einschalten
    waere jemand am ersten Tag ueberzogen, wegen Wochen von Historie, von der
    er nicht wusste, dass sie einmal zaehlen wuerde.
    """
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _posten(db, user_id=konto["id"], state=StorageState.owned, tmdb=1)

    admin_client.put("/api/settings", json={"storage_enabled": True})

    assert _bestand(admin_client) == (0, 1, 0)


def test_ohne_wechsel_kein_pardon(admin_client: TestClient) -> None:
    """Speichern ohne Betriebsartwechsel laesst die Zuordnung in Ruhe.

    Sonst wuerde jede Aenderung der Vorgabe-Grenze nebenbei alle Konten
    leeren - ein Pardon, den niemand wollte.
    """
    admin_client.put("/api/settings", json={"storage_enabled": True})
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], state=StorageState.owned, tmdb=1)

    # Derselbe Wert nochmal, dazu eine andere Einstellung - beides harmlos.
    admin_client.put("/api/settings", json={"storage_enabled": True})
    admin_client.put("/api/settings", json={"storage_default_limit_gb": 200})

    with SessionLocal() as db:
        zeile = db.get(StorageEntry, posten_id)
        assert zeile.user_id == konto["id"]
        assert zeile.state == StorageState.owned


def test_vorschau_nennt_die_zahlen(admin_client: TestClient) -> None:
    """Der Dialog vor dem Umschalten nennt Zahlen, keine Allgemeinplaetze.

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
