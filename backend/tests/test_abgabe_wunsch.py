"""Der Abgabe-Wunsch: loeschen lassen oder behalten, aber nicht mehr laden.

Der einstufige Ablauf aus dem Plan: Der Nutzer sagt **beim Abgeben**, was er
sich vorstellt, und der Admin entscheidet **einmal** - niemand wird zweimal
gefragt. Neben "Ins Haus" und "Loeschen" gibt es damit ein drittes Ergebnis:

> **Nicht mehr folgen** - die Folgen bleiben liegen, Sonarr laedt keine neuen
> mehr, und der Posten zaehlt **weiter**, weil die Dateien ja noch da sind.

Die Grenze der Wahl: ``keep`` gibt es nur bei Serien-Staffeln. Ein Film
waechst nicht - "behalten und nicht mehr laden" waere dort dasselbe wie gar
nichts, und eine Wahl ohne Unterschied ist keine.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    StorageEntry,
    StorageState,
    StorageWish,
)
from app.services import library

from .conftest import auth_headers, create_user

GB = 1024**3


def _mit_speicher(client) -> None:
    client.put("/api/settings", json={"storage_enabled": True})


def _film(db, user_id: int, tmdb: int = 603) -> int:
    zeile = StorageEntry(
        key=f"movie:standard:tmdb:{tmdb}",
        user_id=user_id,
        media_type=MediaType.movie,
        tier=QualityTier.standard,
        tmdb_id=tmdb,
        title="Ein Film",
        size_bytes=8 * GB,
        state=StorageState.owned,
    )
    db.add(zeile)
    db.commit()
    return zeile.id


def _staffel(db, user_id: int | None, *, tvdb: int = 7, season: int = 2) -> int:
    zeile = StorageEntry(
        key=f"tv:standard:tvdb:{tvdb}:s{season}",
        user_id=user_id,
        media_type=MediaType.tv,
        tier=QualityTier.standard,
        tvdb_id=tvdb,
        season=season,
        title="Eine Serie",
        size_bytes=20 * GB,
        state=StorageState.owned,
    )
    db.add(zeile)
    db.commit()
    return zeile.id


class _Sonarr:
    """Merkt sich, was stillgelegt werden sollte."""

    def __init__(self) -> None:
        self.stillgelegt: list[tuple[int, int]] = []

    async def unmonitor_season(self, arr_id: int, season: int) -> None:
        self.stillgelegt.append((arr_id, season))


def _sonarr_kennt_die_serie(monkeypatch, *, arr_id: int | None = 99) -> _Sonarr:
    from app.services.sonarr import LibraryEntry

    attrappe = _Sonarr()
    monkeypatch.setattr(library, "sonarr_client", lambda *_a, **_k: attrappe)

    async def bibliothek(_s, _tier="standard"):
        if arr_id is None:
            return {}, {}
        return {
            7: LibraryEntry(
                arr_id=arr_id,
                has_file=True,
                monitored=True,
                episode_file_count=10,
                episode_count=10,
                title_key="eineserie",
                title="Eine Serie",
            )
        }, {}

    monkeypatch.setattr(library, "series_library", bibliothek)
    return attrappe


# --- Der Wunsch beim Abgeben ------------------------------------------------


def test_wunsch_reist_mit_der_abgabe(admin_client) -> None:
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _staffel(db, konto["id"])

    antwort = admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben",
        json={"wish": "keep"},
        headers=auth_headers(admin_client, "kim", "passwort-1234"),
    )
    assert antwort.status_code == 200
    assert antwort.json()["release_wish"] == "keep"

    # Und die Warteschlange traegt ihn weiter - der Admin entscheidet ja
    # anhand dessen, was sich die Person vorgestellt hat.
    zeilen = admin_client.get("/api/storage/releases").json()
    assert [z["entry"]["release_wish"] for z in zeilen] == ["keep"]


def test_ohne_angabe_gilt_loeschen(admin_client) -> None:
    """Der bisherige einzige Weg bleibt die Vorgabe."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _film(db, konto["id"])

    antwort = admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben",
        headers=auth_headers(admin_client, "kim", "passwort-1234"),
    )
    assert antwort.status_code == 200
    assert antwort.json()["release_wish"] == "delete"


def test_behalten_gibt_es_bei_filmen_nicht(admin_client) -> None:
    """Ein Film waechst nicht - die Wahl waere eine ohne Unterschied."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _film(db, konto["id"])

    antwort = admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben",
        json={"wish": "keep"},
        headers=auth_headers(admin_client, "kim", "passwort-1234"),
    )
    assert antwort.status_code == 422


def test_zuruecknehmen_raeumt_den_wunsch_ab(admin_client) -> None:
    """"Doch behalten" laesst nichts vom Abgabe-Versuch zurueck."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _staffel(db, konto["id"])

    admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben", json={"wish": "keep"}, headers=kopf
    )
    admin_client.post(f"/api/storage/entries/{posten_id}/behalten", headers=kopf)

    with SessionLocal() as db:
        zeile = db.get(StorageEntry, posten_id)
        assert zeile.release_wish is None
        assert zeile.state == StorageState.owned


# --- Der dritte Ausgang: nicht mehr folgen ----------------------------------


def test_entfolgen_legt_still_und_belastet_weiter(admin_client, monkeypatch) -> None:
    """⚠️ **Der Kern.** Stillgelegt in Sonarr, aber der Posten zaehlt weiter -
    die Dateien liegen ja noch da, und behalten wollte sie der Abgebende
    ausdruecklich."""
    _mit_speicher(admin_client)
    attrappe = _sonarr_kennt_die_serie(monkeypatch)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _staffel(db, konto["id"], season=2)
    admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben",
        json={"wish": "keep"},
        headers=auth_headers(admin_client, "kim", "passwort-1234"),
    )

    antwort = admin_client.post(f"/api/storage/entries/{posten_id}/entfolgen")
    assert antwort.status_code == 200
    assert antwort.json()["state"] == "owned"

    assert attrappe.stillgelegt == [(99, 2)]
    with SessionLocal() as db:
        zeile = db.get(StorageEntry, posten_id)
        assert zeile.user_id == konto["id"]  # zaehlt weiter
        assert zeile.state == StorageState.owned
        assert zeile.release_wish is None
        assert zeile.released_at is None
        # Warteschlange leer, Betroffener informiert.
        arten = [
            n.type
            for n in db.scalars(
                select(Notification).where(Notification.user_id == konto["id"])
            )
        ]
        assert NotificationType.storage_kept in arten


def test_entfolgen_ohne_sonarr_eintrag_geht_trotzdem(admin_client, monkeypatch) -> None:
    """Serie inzwischen aus Sonarr entfernt: nichts stillzulegen - das Ziel
    "keine neuen Folgen" ist damit ohnehin erreicht."""
    _mit_speicher(admin_client)
    _sonarr_kennt_die_serie(monkeypatch, arr_id=None)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _staffel(db, konto["id"])
    admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben",
        json={"wish": "keep"},
        headers=auth_headers(admin_client, "kim", "passwort-1234"),
    )

    antwort = admin_client.post(f"/api/storage/entries/{posten_id}/entfolgen")
    assert antwort.status_code == 200
    assert antwort.json()["state"] == "owned"


def test_entfolgen_nur_fuer_wartende(admin_client, monkeypatch) -> None:
    """Ohne Abgabe gibt es nichts zu entscheiden."""
    _mit_speicher(admin_client)
    _sonarr_kennt_die_serie(monkeypatch)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _staffel(db, konto["id"])

    assert (
        admin_client.post(f"/api/storage/entries/{posten_id}/entfolgen").status_code
        == 409
    )


def test_entfolgen_nur_fuer_admins(admin_client) -> None:
    """Dieselbe Sicherheitsregel wie beim Hausbestand: Entscheider haben selbst
    ein Kontingent und duerfen ueber Abgaben nicht befinden."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _staffel(db, konto["id"])
    admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben",
        json={"wish": "keep"},
        headers=auth_headers(admin_client, "kim", "passwort-1234"),
    )

    antwort = admin_client.post(
        f"/api/storage/entries/{posten_id}/entfolgen",
        headers=auth_headers(admin_client, "kim", "passwort-1234"),
    )
    assert antwort.status_code == 403


# --- Das Auge und der Gesehen-Filter ----------------------------------------


def _plex_verknuepft(client, konto: dict) -> None:
    """Media-Server einschalten und das Konto verknuepfen - die zwei
    Bedingungen, an denen Gesehen-Daten haengen."""
    from app.services.settings_service import save_settings
    from app.models import User

    with SessionLocal() as db:
        save_settings(
            db,
            {
                "mediaserver_provider": "plex",
                "mediaserver_machine_id": "maschine-1",
                "mediaserver_token": "geheim",
            },
        )
        benutzer = db.get(User, konto["id"])
        benutzer.mediaserver_provider = "plex"
        benutzer.mediaserver_account_id = "acc-1"
        db.commit()


def _gesehen_markieren(user_id: int, tmdb: int) -> None:
    from app.models import UserWatched

    with SessionLocal() as db:
        db.add(UserWatched(user_id=user_id, media_type=MediaType.movie, tmdb_id=tmdb))
        db.commit()


def test_gesehen_filter_liefert_nur_gesehene_filme(admin_client) -> None:
    """"Nur Gesehene" sind die Kandidaten fuers Abgeben - Serien bleiben
    draussen, weil "Staffel gesehen" aus Titel-Daten nicht ehrlich zu
    beantworten ist."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    _plex_verknuepft(admin_client, konto)
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        gesehen = _film(db, konto["id"], tmdb=603)
        _film(db, konto["id"], tmdb=604)  # nicht gesehen
        _staffel(db, konto["id"])  # Serie: nie im Filter
    _gesehen_markieren(konto["id"], 603)

    daten = admin_client.get("/api/storage/me?gesehen=true", headers=kopf).json()
    assert daten["watched_available"] is True
    assert [z["id"] for z in daten["entries"]] == [gesehen]
    assert daten["entries"][0]["watched"] is True

    # Ohne Filter ist alles da - und die Augen sagen je Film die Wahrheit.
    alles = admin_client.get("/api/storage/me", headers=kopf).json()
    augen = {z["tmdb_id"]: z["watched"] for z in alles["entries"] if z["tmdb_id"]}
    assert augen[603] is True
    assert augen[604] is False


def test_ohne_verknuepfung_kein_filter_und_kein_auge(admin_client) -> None:
    """Ohne Media-Server-Verknuepfung gibt es keine Daten - der Filter wird
    gar nicht erst angeboten, und statt eines roten Auges ("nie gesehen!")
    bleibt die Angabe leer."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _film(db, konto["id"])

    daten = admin_client.get("/api/storage/me", headers=kopf).json()
    assert daten["watched_available"] is False
    assert daten["entries"][0]["watched"] is None


def test_staffel_auge_und_filter(admin_client) -> None:
    """Gruen heisst bei Staffeln: **alle** Folgen gesehen - und genau die
    tauchen im Gesehen-Filter auf."""
    from app.models import UserWatchedSeason

    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    _plex_verknuepft(admin_client, konto)
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        ganz = _staffel(db, konto["id"], tvdb=7, season=1)
        halb = _staffel(db, konto["id"], tvdb=7, season=2)
        # Staffel-Marker haengen an der TMDB-Kennung - nachtragen wie im Betrieb.
        for posten_id, tmdb in ((ganz, 4386), (halb, 4386)):
            db.get(StorageEntry, posten_id).tmdb_id = tmdb
        db.add(UserWatchedSeason(user_id=konto["id"], tmdb_id=4386, season=1))
        db.commit()

    daten = admin_client.get("/api/storage/me", headers=kopf).json()
    augen = {z["season"]: z["watched"] for z in daten["entries"]}
    assert augen[1] is True
    assert augen[2] is False

    nur = admin_client.get("/api/storage/me?gesehen=true", headers=kopf).json()
    assert [z["id"] for z in nur["entries"]] == [ganz]
