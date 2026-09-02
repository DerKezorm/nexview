"""Konto aufloesen: Antrag, Vorschau, Entscheidung, Loeschung.

Vorher passierte beim Loeschen eines Kontos zweierlei stillschweigend: Die
Posten fielen per Datenbankregel ans Haus, und laufende Bestellungen liefen
**einfach weiter** - eine ueberwachte Staffel lud herrenlos nach, ohne dass
sie je wieder jemandem gehoerte. Jetzt entscheidet ein Mensch:

* Der Betroffene **beantragt** per Ticket (loeschen kann nur der Admin).
* Der Admin sieht vorher, was das Konto hinterlaesst.
* Je Posten: Haus oder Loeschen. Je angefangener Staffel: behalten oder
  loeschen, und ob weitergeladen wird. Bestellungen ohne Dateien: storniert.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    QualityTier,
    RequestStatus,
    StorageEntry,
    StorageState,
    Ticket,
    TicketStatus,
    User,
)
from app.services import library
from app.services.radarr import LibraryEntry as MovieEntry
from app.services.sonarr import LibraryEntry as SeriesEntry
from app.services.sonarr import Staffelstand

from .conftest import auth_headers, create_user

GB = 1024**3


# --- Der Antrag --------------------------------------------------------------


def test_antrag_landet_als_ticket_bei_den_admins(admin_client: TestClient) -> None:
    create_user(admin_client, "kim", "passwort-1234")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")

    antwort = admin_client.post("/api/tickets/kontoaufloesung", headers=kopf)
    assert antwort.status_code == 201

    with SessionLocal() as db:
        ticket = db.scalars(select(Ticket)).one()
        assert ticket.subject == "Konto löschen"
        assert ticket.status == TicketStatus.open


def test_zweiter_antrag_wird_abgewiesen(admin_client: TestClient) -> None:
    """Ein zweiter Antrag waere nur Laerm in der Warteschlange."""
    create_user(admin_client, "kim", "passwort-1234")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    admin_client.post("/api/tickets/kontoaufloesung", headers=kopf)

    assert admin_client.post("/api/tickets/kontoaufloesung", headers=kopf).status_code == 409


def test_admins_stellen_keinen_antrag(admin_client: TestClient) -> None:
    """Sie loeschen direkt - ein Antrag an sich selbst waere Theater."""
    assert admin_client.post("/api/tickets/kontoaufloesung").status_code == 403


# --- Attrappen ---------------------------------------------------------------


class _Radarr:
    def __init__(self) -> None:
        self.entfernt: list[tuple[int, bool]] = []

    async def remove(self, arr_id: int, delete_files: bool = True) -> None:
        self.entfernt.append((arr_id, delete_files))


class _Sonarr:
    def __init__(self, dateien: dict[int, list[dict]] | None = None) -> None:
        self.dateien = dateien or {}
        self.stillgelegt: list[tuple[int, int]] = []
        self.serien_still: list[int] = []
        self.geloescht: list[int] = []

    async def episode_files(self, arr_id: int, season: int) -> list[dict]:
        return self.dateien.get(season, [])

    async def unmonitor_season(self, arr_id: int, season: int) -> None:
        self.stillgelegt.append((arr_id, season))

    async def serie_stilllegen(self, arr_id: int) -> None:
        self.serien_still.append(arr_id)

    async def delete_episode_files(self, ids: list[int]) -> int:
        self.geloescht.extend(ids)
        return len(ids)

    async def get(self, pfad: str, params: dict | None = None) -> list[dict]:
        # Nur fuer den Ganze-Serie-Loeschpfad gebraucht.
        alle = []
        for staffel, zeilen in self.dateien.items():
            for zeile in zeilen:
                alle.append({**zeile, "seasonNumber": staffel})
        return alle


def _instanzen(
    monkeypatch,
    *,
    filme: dict[int, MovieEntry] | None = None,
    serie: SeriesEntry | None = None,
    sonarr_dateien: dict[int, list[dict]] | None = None,
) -> tuple[_Radarr, _Sonarr]:
    radarr = _Radarr()
    sonarr = _Sonarr(sonarr_dateien)
    monkeypatch.setattr(library, "radarr_client", lambda *_a, **_k: radarr)
    monkeypatch.setattr(library, "sonarr_client", lambda *_a, **_k: sonarr)
    monkeypatch.setattr(library, "invalidate", lambda: None)

    async def filmbibliothek(_s, _tier="standard"):
        return dict(filme or {})

    async def serienbibliothek(_s, _tier="standard"):
        if serie is None:
            return {}, {}
        return {serie_tvdb(serie): serie}, {serie.title_key: serie}

    monkeypatch.setattr(library, "movie_library", filmbibliothek)
    monkeypatch.setattr(library, "series_library", serienbibliothek)
    return radarr, sonarr


def serie_tvdb(_e: SeriesEntry) -> int:
    return 7


def _posten(db, user_id: int, *, tmdb: int, gb: int = 8) -> int:
    zeile = StorageEntry(
        key=f"movie:standard:tmdb:{tmdb}",
        user_id=user_id,
        media_type=MediaType.movie,
        tier=QualityTier.standard,
        tmdb_id=tmdb,
        title=f"Film {tmdb}",
        size_bytes=gb * GB,
        state=StorageState.owned,
    )
    db.add(zeile)
    db.commit()
    return zeile.id


def _anfrage(
    db,
    user_id: int,
    *,
    media_type: MediaType = MediaType.tv,
    tmdb: int = 4386,
    tvdb: int | None = 7,
    season: int | None = 2,
    arr_id: int | None = 99,
    titel: str = "Eine Serie",
) -> int:
    zeile = MediaRequest(
        user_id=user_id,
        media_type=media_type,
        tier=QualityTier.standard,
        tmdb_id=tmdb,
        tvdb_id=tvdb,
        title=titel,
        season=season,
        status=RequestStatus.searching,
        arr_id=arr_id,
        quality_profile_id=1,
        root_folder_path="/data",
    )
    db.add(zeile)
    db.commit()
    return zeile.id


_SERIE = SeriesEntry(
    arr_id=99,
    has_file=True,
    monitored=True,
    episode_file_count=5,
    episode_count=22,
    title_key="eineserie",
    title="Eine Serie",
    staffeln={
        2: Staffelstand(dateien=5, folgen=22),
        3: Staffelstand(dateien=0, folgen=22),
    },
)


# --- Die Vorschau ------------------------------------------------------------


@pytest.mark.anyio
async def test_vorschau_sortiert_den_nachlass(arr_client: TestClient, monkeypatch) -> None:
    """Posten, angefangene Staffeln, leere Bestellungen - drei Toepfe."""
    _instanzen(monkeypatch, serie=_SERIE)
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, konto["id"], tmdb=603)
        _anfrage(db, konto["id"], season=2)  # 5 Dateien -> Entscheidung
        _anfrage(db, konto["id"], season=3)  # 0 Dateien -> stornieren
        _anfrage(
            db,
            konto["id"],
            media_type=MediaType.movie,
            tmdb=604,
            tvdb=None,
            season=None,
            arr_id=41,
            titel="Ein Bestellter",
        )

    antwort = arr_client.get(f"/api/users/{konto['id']}/aufloesung")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert [p["id"] for p in daten["posten"]] == [posten_id]
    assert [(z["season"], z["dateien"]) for z in daten["laufende"]] == [(2, 5)]
    # ⚠️ Seit 0.22 mit Kennung statt nur einem Titel. Ohne die konnte der
    # Administrator ueber diese Bestellungen nicht entscheiden - er sah nur zu,
    # wie sie storniert wurden.
    assert sorted(z["title"] for z in daten["offen"]) == ["Ein Bestellter", "Eine Serie"]
    assert all(z["request_id"] for z in daten["offen"])


# --- Die Aufloesung ----------------------------------------------------------


@pytest.mark.anyio
async def test_aufloesung_fuehrt_jede_entscheidung_aus(
    arr_client: TestClient, monkeypatch
) -> None:
    """⚠️ **Der Kern**: Haus, Loeschen, Staffel-Wahl, Stornieren - und erst
    dann faellt das Konto."""
    radarr, sonarr = _instanzen(
        monkeypatch,
        filme={603: MovieEntry(arr_id=42, has_file=True, monitored=True)},
        serie=_SERIE,
        sonarr_dateien={2: [{"id": 21}, {"id": 22}]},
    )
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        bleibt = _posten(db, konto["id"], tmdb=900)
        faellt = _posten(db, konto["id"], tmdb=603)
        laufend = _anfrage(db, konto["id"], season=2)
        _anfrage(
            db,
            konto["id"],
            media_type=MediaType.movie,
            tmdb=604,
            tvdb=None,
            season=None,
            arr_id=41,
            titel="Ein Bestellter",
        )

    antwort = arr_client.request(
        "DELETE",
        f"/api/users/{konto['id']}",
        json={
            "haus": [bleibt],
            "loeschen": [faellt],
            "staffeln": [{"request_id": laufend, "behalten": False}],
        },
    )
    assert antwort.status_code == 204, antwort.text

    with SessionLocal() as db:
        # Konto weg, Posten entschieden.
        assert db.scalar(select(User).where(User.id == konto["id"])) is None
        haus_zeile = db.get(StorageEntry, bleibt)
        assert haus_zeile.user_id is None and haus_zeile.state == StorageState.house
        assert db.get(StorageEntry, faellt) is None
    # Der geloeschte Film ging ueber Radarr samt Datei.
    assert radarr.entfernt == [(42, True), (41, True)]
    # Die abgelehnte Staffel: stillgelegt und Dateien weg.
    assert (99, 2) in sonarr.stillgelegt
    assert sorted(sonarr.geloescht) == [21, 22]


@pytest.mark.anyio
async def test_behalten_und_weiterladen_laesst_alles_stehen(
    arr_client: TestClient, monkeypatch
) -> None:
    _radarr, sonarr = _instanzen(monkeypatch, serie=_SERIE)
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        laufend = _anfrage(db, konto["id"], season=2)

    antwort = arr_client.request(
        "DELETE",
        f"/api/users/{konto['id']}",
        json={"staffeln": [{"request_id": laufend, "behalten": True, "weiter": True}]},
    )
    assert antwort.status_code == 204
    assert sonarr.stillgelegt == []
    assert sonarr.geloescht == []


@pytest.mark.anyio
async def test_behalten_ohne_weiter_legt_nur_still(
    arr_client: TestClient, monkeypatch
) -> None:
    _radarr, sonarr = _instanzen(monkeypatch, serie=_SERIE)
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        laufend = _anfrage(db, konto["id"], season=2)

    antwort = arr_client.request(
        "DELETE",
        f"/api/users/{konto['id']}",
        json={"staffeln": [{"request_id": laufend, "behalten": True, "weiter": False}]},
    )
    assert antwort.status_code == 204
    assert sonarr.stillgelegt == [(99, 2)]
    assert sonarr.geloescht == []


@pytest.mark.anyio
async def test_unentschiedenes_blockiert_die_loeschung(
    arr_client: TestClient, monkeypatch
) -> None:
    """⚠️ Jeder Posten braucht eine Entscheidung - das faengt auch das
    Wettrennen ab, wenn zwischen Vorschau und Klick noch etwas fertig wird."""
    _instanzen(monkeypatch, serie=_SERIE)
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _posten(db, konto["id"], tmdb=603)

    antwort = arr_client.request("DELETE", f"/api/users/{konto['id']}", json={})
    assert antwort.status_code == 409

    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.id == konto["id"])) is not None


@pytest.mark.anyio
async def test_ohne_nachlass_geht_es_wie_bisher(arr_client: TestClient, monkeypatch) -> None:
    """Kein Bestand, keine Bestellungen: Loeschen ohne Beipackzettel."""
    _instanzen(monkeypatch)
    konto = create_user(arr_client, "kim", "passwort-1234")

    assert arr_client.delete(f"/api/users/{konto['id']}").status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.id == konto["id"])) is None


@pytest.mark.anyio
async def test_offene_bestellung_kann_behalten_werden(
    arr_client: TestClient, monkeypatch
) -> None:
    """⚠️ Der Widerspruch, den das Modul bis 0.22 mit sich herumtrug.

    Sein eigener Kopfkommentar sagt: "Beides sind Entscheidungen, die ein
    Mensch treffen soll, kein Fremdschluessel." Daran hielt es sich bei den
    fertigen Posten und den angefangenen Staffeln - und brach es bei den
    offenen Bestellungen, wo es ohne Rueckfrage stornierte.

    Die Begruendung ("wo keine Datei liegt, ist nichts verloren") stimmt fuer
    den Speicherplatz, aber nicht fuer die Absicht: Jemand hat den Titel
    gewollt, jemand hat ihn genehmigt, er ist unterwegs.
    """
    radarr, _ = _instanzen(monkeypatch, serie=_SERIE)
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _anfrage(
            db,
            konto["id"],
            media_type=MediaType.movie,
            tmdb=604,
            tvdb=None,
            season=None,
            arr_id=41,
            titel="Bleibt bestellt",
        )

    vorschau = arr_client.get(f"/api/users/{konto['id']}/aufloesung").json()
    behalten = [z["request_id"] for z in vorschau["offen"] if z["title"] == "Bleibt bestellt"]
    assert behalten, "Die Bestellung muss in der Vorschau auftauchen."

    antwort = arr_client.request(
        "DELETE",
        f"/api/users/{konto['id']}",
        json={"haus": [], "loeschen": [], "staffeln": [], "offen_behalten": behalten},
    )
    assert antwort.status_code in (200, 204), antwort.text

    # ⚠️ Der eigentliche Beweis: In Radarr wurde **nicht** entfernt. Ohne die
    # Entscheidung stuende hier (41, True).
    assert radarr.entfernt == [], f"Die behaltene Bestellung wurde trotzdem entfernt: {radarr.entfernt}"


@pytest.mark.anyio
async def test_ohne_entscheidung_wird_weiter_storniert(
    arr_client: TestClient, monkeypatch
) -> None:
    """Das bisherige Verhalten bleibt die Voreinstellung.

    Wer nichts angibt, bekommt was er vor 0.22 bekam - sonst laedt eine
    Bestellung herrenlos weiter, nur weil jemand ein Feld nicht gesetzt hat.
    """
    radarr, _ = _instanzen(monkeypatch, serie=_SERIE)
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _anfrage(
            db,
            konto["id"],
            media_type=MediaType.movie,
            tmdb=604,
            tvdb=None,
            season=None,
            arr_id=41,
            titel="Wird storniert",
        )

    antwort = arr_client.request(
        "DELETE",
        f"/api/users/{konto['id']}",
        json={"haus": [], "loeschen": [], "staffeln": []},
    )
    assert antwort.status_code in (200, 204), antwort.text
    assert radarr.entfernt == [(41, True)]
