"""Löschen - der einzige Vorgang in Nexview ohne Rückweg.

Alles andere im Speicher-Kontingent verschiebt nur, wem etwas zugerechnet
wird. Hier wird eine Datei vernichtet, und ohne Papierkorb endgültig.

Entsprechend eng ist alles abgesichert:

* Ein **Probelauf**, der die tatsächliche Dateiliste zeigt, ohne etwas
  anzufassen. Der Administrator bestätigt mit ihr vor Augen, nicht mit einer
  Zahl.
* Bei Staffeln: **erst stilllegen, dann löschen**. Andernfalls holt Sonarr die
  Folgen beim nächsten Durchlauf zurück.
* Ein **Protokolleintrag vor dem Zugriff**. Trifft es das Falsche, ist er der
  einzige Beleg dafür, worum Nexview gebeten hat.

Die Stufensperre ("nur 4K") ist gefallen, nachdem in allen Instanzen ein
Papierkorb eingerichtet war - das Netz liegt jetzt unter der Datei statt in
einer Konstanten. Der Mechanismus bleibt geprüft, falls er je wieder gebraucht
wird.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    RequestStatus,
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    Role,
    StorageEntry,
    StorageState,
)
from app.services import library, storage
from app.services.radarr import LibraryEntry as MovieEntry
from app.services.sonarr import LibraryEntry as SeriesEntry
from app.services.settings_service import load_settings, save_settings

from .conftest import auth_headers, create_user

GB = 1024**3

UHD = {"radarr_uhd_url": "http://127.0.0.1:11", "radarr_uhd_api_key": "vier-k"}


# --- Attrappen --------------------------------------------------------------


class _Radarr:
    """Schreibt mit, was entfernt werden sollte - und entfernt nichts."""

    def __init__(self) -> None:
        self.entfernt: list[tuple[int, bool]] = []

    async def remove(self, arr_id: int, delete_files: bool = True) -> None:
        self.entfernt.append((arr_id, delete_files))


class _Sonarr:
    """Dasselbe für Serien, samt Reihenfolge der Schritte."""

    def __init__(self, dateien: dict[int, list[dict]]) -> None:
        self.dateien = dateien
        self.stillgelegt: list[tuple[int, int]] = []
        self.geloescht: list[int] = []
        self.reihenfolge: list[str] = []

    async def episode_files(self, arr_id: int, season: int) -> list[dict]:
        return self.dateien.get(season, [])

    async def unmonitor_season(self, arr_id: int, season: int) -> None:
        self.stillgelegt.append((arr_id, season))
        self.reihenfolge.append("stilllegen")

    async def delete_episode_files(self, ids: list[int]) -> int:
        self.geloescht.extend(ids)
        self.reihenfolge.append("loeschen")
        return len(ids)


def _ein_film(gb: int = 8) -> MovieEntry:
    return MovieEntry(
        arr_id=42,
        has_file=True,
        monitored=True,
        size_bytes=gb * GB,
        title="Ein Film",
        path="/data/Movies4K/Ein Film/ein.film.mkv",
    )


def _instanz(monkeypatch, *, film: MovieEntry | None = None) -> _Radarr:
    attrappe = _Radarr()
    monkeypatch.setattr(library, "radarr_client", lambda *_a, **_k: attrappe)

    async def bibliothek(_s, _tier="standard"):
        return {603: film} if film else {}

    monkeypatch.setattr(library, "movie_library", bibliothek)
    monkeypatch.setattr(library, "invalidate", lambda: None)
    return attrappe


def _serie(monkeypatch, dateien: dict[int, list[dict]]) -> _Sonarr:
    attrappe = _Sonarr(dateien)
    monkeypatch.setattr(library, "sonarr_client", lambda *_a, **_k: attrappe)

    async def bibliothek(_s, _tier="standard"):
        return {
            7: SeriesEntry(
                arr_id=99,
                has_file=True,
                monitored=True,
                episode_file_count=1,
                episode_count=1,
                title_key="eineserie",
                title="Eine Serie",
            )
        }, {}

    monkeypatch.setattr(library, "series_library", bibliothek)
    monkeypatch.setattr(library, "invalidate", lambda: None)
    return attrappe


def _posten(
    db, *, user_id: int | None, tier: QualityTier, titel: str = "Ein Film", tmdb: int = 603
) -> StorageEntry:
    eintrag = StorageEntry(
        key=f"movie:{tier.value}:tmdb:{tmdb}",
        user_id=user_id,
        media_type=MediaType.movie,
        tier=tier,
        tmdb_id=tmdb,
        title=titel,
        size_bytes=8 * GB,
        state=StorageState.pending if user_id else StorageState.house,
    )
    db.add(eintrag)
    db.commit()
    return eintrag


def _staffelposten(db, staffel: int = 2) -> StorageEntry:
    eintrag = StorageEntry(
        key=f"tv:standard:tvdb:7:s{staffel}",
        media_type=MediaType.tv,
        tier=QualityTier.standard,
        tvdb_id=7,
        season=staffel,
        title="Eine Serie",
        size_bytes=8 * GB,
        state=StorageState.house,
    )
    db.add(eintrag)
    db.commit()
    return eintrag


# --- Filme ------------------------------------------------------------------


@pytest.mark.anyio
async def test_film_wird_samt_datei_entfernt(monkeypatch) -> None:
    attrappe = _instanz(monkeypatch, film=_ein_film())
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _posten(db, user_id=None, tier=QualityTier.uhd)

        bytes_ = await storage.loeschen(db, einstellungen, posten.id)
        db.commit()

        assert bytes_ == 8 * GB
        # Ohne ``delete_files`` bliebe die Datei liegen und Radarr vergäße sie nur.
        assert attrappe.entfernt == [(42, True)]
        # Der Posten ist **sofort** weg, nicht erst beim nächsten Abgleich:
        # Sonst bliebe jemand für eine Datei belastet, die es nicht mehr gibt.
        assert db.get(StorageEntry, posten.id) is None


# --- Die Stufensperre -------------------------------------------------------


@pytest.mark.anyio
async def test_die_stufensperre_greift_wenn_sie_gesetzt_ist(monkeypatch) -> None:
    """Aufgehoben, aber geprüft - falls sie je wieder gebraucht wird.

    Sie stand auf „nur 4K", solange nur die Testinstanz drankommen sollte, und
    ist gefallen, als in **allen** Instanzen ein Papierkorb eingerichtet war.
    """
    monkeypatch.setattr(storage, "LOESCHBARE_STUFEN", (QualityTier.uhd,))
    attrappe = _instanz(monkeypatch, film=_ein_film())
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _posten(db, user_id=None, tier=QualityTier.standard)

        with pytest.raises(storage.Loeschfehler) as fehler:
            await storage.loeschen(db, einstellungen, posten.id)

        assert fehler.value.status_code == 403
        # Nicht ein einziger Aufruf ist bei Radarr angekommen.
        assert attrappe.entfernt == []
        assert db.get(StorageEntry, posten.id) is not None


@pytest.mark.anyio
async def test_ohne_sperre_darf_jede_stufe(monkeypatch) -> None:
    """So steht es jetzt: Das Netz ist der Papierkorb, nicht die Stufe."""
    attrappe = _instanz(monkeypatch, film=_ein_film())
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _posten(db, user_id=None, tier=QualityTier.standard)

        await storage.loeschen(db, einstellungen, posten.id)
        db.commit()

        assert attrappe.entfernt == [(42, True)]
        assert db.get(StorageEntry, posten.id) is None


# --- Staffeln ---------------------------------------------------------------


@pytest.mark.anyio
async def test_nur_die_dateien_der_gewaehlten_staffel(monkeypatch) -> None:
    """⚠️ **Der wichtigste Test überhaupt.**

    Ein Fehler hier trifft Folgen einer Staffel, die jemand behalten wollte -
    und das fällt erst auf, wenn sie jemand sehen will.
    """
    attrappe = _serie(
        monkeypatch,
        {
            1: [{"id": 11}, {"id": 12}],
            2: [{"id": 21}, {"id": 22}, {"id": 23}],
            3: [{"id": 31}],
        },
    )
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _staffelposten(db, staffel=2)

        await storage.loeschen(db, einstellungen, posten.id)
        db.commit()

    assert attrappe.geloescht == [21, 22, 23]


@pytest.mark.anyio
async def test_erst_stilllegen_dann_loeschen(monkeypatch) -> None:
    """⚠️ **Die Reihenfolge ist der Punkt.**

    Sonarr sucht für jede überwachte Staffel nach fehlenden Folgen. Bliebe sie
    an, wäre die Staffel beim nächsten Durchlauf wieder da - und wer abgegeben
    hat, sähe seinen Speicher erneut steigen. Scheitert dagegen das
    Stilllegen, liegen die Dateien noch da und nichts ist verloren.
    """
    attrappe = _serie(monkeypatch, {2: [{"id": 21}]})
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _staffelposten(db, staffel=2)
        await storage.loeschen(db, einstellungen, posten.id)
        db.commit()

    assert attrappe.stillgelegt == [(99, 2)]
    assert attrappe.reihenfolge == ["stilllegen", "loeschen"]


@pytest.mark.anyio
async def test_ohne_gemeldete_dateien_wird_nichts_geloescht(monkeypatch) -> None:
    """Eine leere Liste ist kein Löschbefehl, sondern ein Grund innezuhalten."""
    attrappe = _serie(monkeypatch, {2: []})
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _staffelposten(db, staffel=2)

        with pytest.raises(storage.Loeschfehler):
            await storage.loeschen(db, einstellungen, posten.id)

    assert attrappe.geloescht == []


# --- Was Nexview nicht löschen kann ----------------------------------------


@pytest.mark.anyio
async def test_unverwalteter_titel_wird_ehrlich_abgelehnt(monkeypatch) -> None:
    """Der Fall aus dem roten Hinweis in den Einstellungen.

    Wer einen Titel aus Radarr entfernt und die Datei behält, hat etwas
    geschaffen, das Nexview zwar mitzählt, aber nicht entfernen kann. Das
    gehört gesagt - nicht stillschweigend als Erfolg verbucht.
    """
    attrappe = _instanz(monkeypatch, film=None)  # Radarr kennt ihn nicht
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _posten(db, user_id=None, tier=QualityTier.uhd)

        with pytest.raises(storage.Loeschfehler) as fehler:
            await storage.loeschen(db, einstellungen, posten.id)

        assert fehler.value.status_code == 409
        assert "Hausbestand" in fehler.value.message
        assert attrappe.entfernt == []
        assert db.get(StorageEntry, posten.id) is not None


# --- Der Probelauf ----------------------------------------------------------


@pytest.mark.anyio
async def test_probelauf_zeigt_die_datei_und_faesst_nichts_an(monkeypatch) -> None:
    """Der Administrator bestätigt mit der Liste vor Augen, nicht mit einer Zahl."""
    attrappe = _instanz(monkeypatch, film=_ein_film(12))
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _posten(db, user_id=None, tier=QualityTier.uhd)

        dateien = await storage.dateien_fuer(db, einstellungen, posten.id)

        assert [d.pfad for d in dateien] == ["/data/Movies4K/Ein Film/ein.film.mkv"]
        assert dateien[0].size_bytes == 12 * GB
        assert attrappe.entfernt == []
        assert db.get(StorageEntry, posten.id) is not None


@pytest.mark.anyio
async def test_probelauf_zeigt_nur_die_eine_staffel(monkeypatch) -> None:
    """Was der Administrator sieht, muss dem entsprechen, was passiert."""
    attrappe = _serie(monkeypatch, {1: [{"id": 11}], 2: [{"id": 21}, {"id": 22}]})
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _staffelposten(db, staffel=2)

        dateien = await storage.dateien_fuer(db, einstellungen, posten.id)

    assert len(dateien) == 2
    assert attrappe.geloescht == []
    assert attrappe.stillgelegt == []


# --- Das Protokoll ----------------------------------------------------------


@pytest.mark.anyio
async def test_vor_dem_zugriff_steht_es_im_protokoll(monkeypatch, caplog) -> None:
    """⚠️ **Vor** dem Zugriff, nicht danach.

    Trifft es das Falsche, ist dieser Eintrag der einzige Beleg dafür, worum
    Nexview gebeten hat. Ein Eintrag nach getaner Arbeit erzählt nur von den
    Fällen, die geklappt haben.
    """
    _instanz(monkeypatch, film=_ein_film())
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        posten = _posten(db, user_id=None, tier=QualityTier.uhd, titel="Ein Klassiker")

        with caplog.at_level(logging.INFO, logger="nexview.storage"):
            await storage.loeschen(db, einstellungen, posten.id, wer="chefin")
        db.commit()

    text = caplog.text
    assert "LOESCHEN angefordert von chefin" in text
    # Wer, was, wo und **welche Datei** - alles, was eine Fehlersuche braucht.
    assert "Ein Klassiker" in text
    assert "arr_id=42" in text
    assert "/data/Movies4K/Ein Film/ein.film.mkv" in text
    assert "LOESCHEN erledigt" in text


# --- Über die Schnittstelle -------------------------------------------------


def test_loeschen_ist_nur_fuer_admins(admin_client, monkeypatch) -> None:
    """Dieselbe Sicherheitsregel wie beim Hausbestand - mit härteren Folgen."""
    admin_client.put("/api/settings", json={"storage_enabled": True})
    create_user(admin_client, "entscheider7", "test1234", role=Role.approver)
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=None, tier=QualityTier.uhd).id

    kopf = auth_headers(admin_client, "entscheider7", "test1234")
    assert (
        admin_client.post(
            f"/api/storage/entries/{posten_id}/loeschen", headers=kopf
        ).status_code
        == 403
    )
    assert (
        admin_client.get(
            f"/api/storage/entries/{posten_id}/dateien", headers=kopf
        ).status_code
        == 403
    )


def test_der_betroffene_erfaehrt_vom_loeschen(admin_client, monkeypatch) -> None:
    """Und zwar mit **eigener** Meldung.

    „Gehört jetzt dem Haus" und „wurde gelöscht" dürfen nicht dieselbe
    Nachricht sein - im ersten Fall ist der Titel noch da, im zweiten weg.
    """
    admin_client.put("/api/settings", json={"storage_enabled": True})
    with SessionLocal() as db:
        save_settings(db, UHD)
    konto = create_user(admin_client, "kim", "passwort-1234")
    _instanz(monkeypatch, film=_ein_film())

    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], tier=QualityTier.uhd).id

    antwort = admin_client.post(f"/api/storage/entries/{posten_id}/loeschen")
    assert antwort.status_code == 204

    with SessionLocal() as db:
        arten = [
            n.type
            for n in db.scalars(
                select(Notification).where(Notification.user_id == konto["id"])
            )
        ]
        assert NotificationType.storage_deleted in arten
        assert NotificationType.storage_released not in arten


def test_vorschau_nennt_den_grund_wenn_nicht_geloescht_werden_kann(
    admin_client, monkeypatch
) -> None:
    """Die Oberfläche soll den Knopf nicht anbieten, ohne den Grund zu nennen."""
    admin_client.put("/api/settings", json={"storage_enabled": True})
    _instanz(monkeypatch, film=None)  # Radarr kennt den Titel nicht mehr
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=None, tier=QualityTier.standard).id

    daten = admin_client.get(f"/api/storage/entries/{posten_id}/dateien").json()
    assert daten["deletable"] is False
    assert daten["reason"] == "unmanaged"


# --- Nach dem Loeschen: die Anfrage ist zu ---------------------------------


def _anfrage(db, *, tmdb=603, tvdb=None, season=None,
             media_type=MediaType.movie, status=RequestStatus.downloaded) -> int:
    """Eine Anfrage, wie sie zum geloeschten Posten gehoert."""
    from app.models import User
    from sqlalchemy import select
    benutzer = db.scalars(select(User)).first()
    if benutzer is None:
        # Diese Datei arbeitet ohne Client-Fixture - also auch ohne das
        # Admin-Konto, das jene anlegen.
        benutzer = User(username="kim", password_hash="egal")
        db.add(benutzer)
        db.commit()
    zeile = MediaRequest(
        user_id=benutzer.id,
        media_type=media_type,
        tier=QualityTier.standard,
        tmdb_id=tmdb,
        tvdb_id=tvdb,
        title="Eine Serie" if media_type == MediaType.tv else "Ein Film",
        season=season,
        status=status,
        quality_profile_id=1,
        root_folder_path="/data",
    )
    db.add(zeile)
    db.commit()
    return zeile.id


@pytest.mark.anyio
async def test_loeschen_schliesst_die_anfrage_der_staffel(monkeypatch) -> None:
    """⚠️ **Der gemeldete Fall.**

    Nach dem Loeschen blieb die Anfrage auf "geladen" stehen - die Staffel
    galt weiter als belegt und liess sich nie wieder anfragen. Der Abgleich
    haette es irgendwann gerichtet; wer aber gerade geloescht hat, muss die
    Folge sofort sehen.
    """
    _serie(monkeypatch, {2: [{"id": 21}]})
    with SessionLocal() as db:
        posten = _staffelposten(db, staffel=2)
        betroffen = _anfrage(db, tvdb=7, season=2, media_type=MediaType.tv)
        # Nachbarn bleiben unberuehrt: andere Staffel, ganze Serie.
        andere_staffel = _anfrage(db, tvdb=7, season=1, media_type=MediaType.tv)
        ganze_serie = _anfrage(db, tvdb=7, season=None, media_type=MediaType.tv)

        await storage.loeschen(db, load_settings(db), posten.id)
        db.commit()

    with SessionLocal() as db:
        assert db.get(MediaRequest, betroffen).status == RequestStatus.deleted
        assert db.get(MediaRequest, andere_staffel).status == RequestStatus.downloaded
        assert db.get(MediaRequest, ganze_serie).status == RequestStatus.downloaded


@pytest.mark.anyio
async def test_loeschen_schliesst_die_anfrage_des_films(monkeypatch) -> None:
    _instanz(monkeypatch, film=_ein_film())
    with SessionLocal() as db:
        posten = _posten(db, user_id=None, tier=QualityTier.standard)
        betroffen = _anfrage(db, tmdb=603)

        await storage.loeschen(db, load_settings(db), posten.id)
        db.commit()

    with SessionLocal() as db:
        assert db.get(MediaRequest, betroffen).status == RequestStatus.deleted


@pytest.mark.anyio
async def test_loeschen_laesst_offene_entscheidungen_stehen(monkeypatch) -> None:
    """"Wartet auf Freigabe" ist eine offene Entscheidung, keine Behauptung
    ueber eine Datei - die trifft weiterhin ein Mensch."""
    _instanz(monkeypatch, film=_ein_film())
    with SessionLocal() as db:
        posten = _posten(db, user_id=None, tier=QualityTier.standard)
        wartend = _anfrage(db, tmdb=603, status=RequestStatus.pending_approval)

        await storage.loeschen(db, load_settings(db), posten.id)
        db.commit()

    with SessionLocal() as db:
        assert db.get(MediaRequest, wartend).status == RequestStatus.pending_approval
