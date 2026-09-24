"""Die Wanderung ``_fassungen_einfuehren``: von zwei Stufen auf Fassungen.

Gebaut wird eine Datenbank, wie sie vor dem Fassungsmodell aussah: das Schema
von heute, dazu die alten Stufen-Spalten (``STUFEN_SPALTEN``) mit Werten, und
die neuen Spalten leer. Danach laeuft ``init_db()`` wie bei einem Update.

⚠️ **Nachgebaut wird das Kennzeichen, nicht die ganze alte Datei** - dieselbe
Ueberlegung wie in ``test_wanderungsbuch.py``: Eine von Hand geschriebene
CREATE-TABLE-Kette waere in einem halben Jahr veraltet und pruefte dann etwas
anderes, als hier steht.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import db as db_modul
from app.models import (
    AuthToken,
    Base,
    MediaRequest,
    MediaType,
    RequestStatus,
    Role,
    StorageEntry,
    TokenPurpose,
    User,
    utcnow,
)

JETZT = "2026-09-01 12:00:00"


@pytest.fixture
def alte_datenbank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., Engine]]:
    motoren: list[Engine] = []

    def bauen(*, mit_stufe: bool = True, ohne_kennung: bool = False) -> Engine:
        verzeichnis = tmp_path / "data"
        verzeichnis.mkdir(exist_ok=True)
        motor = create_engine(
            f"sqlite:///{verzeichnis / 'nexview.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=motor)
        with motor.begin() as v:
            if mit_stufe:
                for tabelle, spalten in db_modul.STUFEN_SPALTEN.items():
                    for spalte in spalten:
                        typ = "VARCHAR(8)" if spalte == "tier" else "BOOLEAN DEFAULT 0"
                        v.exec_driver_sql(f'ALTER TABLE "{tabelle}" ADD COLUMN "{spalte}" {typ}')
            if ohne_kennung:
                # Eine Datenbank aus der Zeit vor 4K: die Kennung gibt es noch nicht.
                v.exec_driver_sql("DROP INDEX ix_media_requests_fassung_kennung")
                v.exec_driver_sql("ALTER TABLE media_requests DROP COLUMN fassung_kennung")
        monkeypatch.setattr(db_modul, "engine", motor)
        monkeypatch.setattr(db_modul._settings, "data_dir", verzeichnis)
        motoren.append(motor)
        return motor

    yield bauen

    for motor in motoren:
        motor.dispose()


def _bestand(motor: Engine) -> None:
    """Konten, Anfragen, Posten und Einladungen im alten Zustand.

    Angelegt ueber das Modell von heute - es kennt alle Pflichtspalten -, danach
    per UPDATE auf den alten Stand gebracht: Stufe gesetzt, Kennung leer.
    """
    with Session(motor) as sitzung:
        for nummer in (1, 2, 3, 4):
            sitzung.add(
                User(
                    id=nummer,
                    username=f"k{nummer}",
                    email=f"k{nummer}@example.com",
                    password_hash="x",
                    role=Role.user,
                )
            )
        for nummer, art, tmdb in ((1, "movie", 603), (2, "movie", 603), (3, "tv", 1399), (4, "tv", 1400)):
            sitzung.add(
                MediaRequest(
                    id=nummer,
                    user_id=1,
                    media_type=MediaType(art),
                    fassung_kennung="platzhalter",
                    tmdb_id=tmdb,
                    title="Titel",
                    status=RequestStatus.searching,
                )
            )
        for nummer, schluessel, art in (
            (1, "movie:standard:tmdb:603", "movie"),
            (2, "movie:uhd:tmdb:603", "movie"),
            (3, "tv:uhd:tvdb:81189:s3", "tv"),
            (4, "tv:standard:tvdb:81189:s3:r4", "tv"),
        ):
            sitzung.add(
                StorageEntry(
                    id=nummer,
                    key=schluessel,
                    media_type=MediaType(art),
                    fassung_kennung="platzhalter",
                    title="Titel",
                )
            )
        for nummer in (1, 2):
            sitzung.add(
                AuthToken(
                    id=nummer,
                    purpose=TokenPurpose.invitation,
                    token_hash=f"h{nummer}",
                    email="neu@example.com",
                    expires_at=utcnow().replace(tzinfo=None),
                )
            )
        sitzung.commit()

    with motor.begin() as v:
        for nummer, filme, serien, auto in ((2, 1, 0, 0), (3, 0, 1, 1), (4, 0, 0, 1)):
            v.exec_driver_sql(
                "UPDATE users SET can_request_uhd_movies = ?, can_request_uhd_series = ?, "
                "auto_approve_uhd = ? WHERE id = ?",
                (filme, serien, auto, nummer),
            )
        # Anfrage 4 stammt von vor 4K: die Spalte kam spaeter und blieb leer.
        for nummer, stufe in ((1, "standard"), (2, "uhd"), (3, "uhd"), (4, None)):
            v.exec_driver_sql(
                "UPDATE media_requests SET tier = ?, fassung_kennung = NULL WHERE id = ?",
                (stufe, nummer),
            )
        for nummer, stufe in ((1, "standard"), (2, "uhd"), (3, "uhd"), (4, "standard")):
            v.exec_driver_sql(
                "UPDATE storage_entries SET tier = ?, fassung_kennung = NULL WHERE id = ?",
                (stufe, nummer),
            )
        v.exec_driver_sql(
            "UPDATE auth_tokens SET invite_can_request_uhd_movies = 1, "
            "invite_auto_approve_uhd = 1 WHERE id = 1"
        )


def _spalten(motor: Engine, tabelle: str) -> set[str]:
    with motor.connect() as v:
        return {z[1] for z in v.exec_driver_sql(f'PRAGMA table_info("{tabelle}")')}


def _abfrage(motor: Engine, sql: str) -> list[tuple]:
    with motor.connect() as v:
        return [tuple(z) for z in v.exec_driver_sql(sql)]


def test_die_stufe_wird_zur_kennung(alte_datenbank) -> None:
    motor = alte_datenbank()
    _bestand(motor)

    db_modul.init_db()

    assert _abfrage(motor, "SELECT id, fassung_kennung, beschaffung FROM media_requests ORDER BY id") == [
        (1, "radarr-standard", "arr"),
        (2, "radarr-uhd", "arr"),
        (3, "sonarr-uhd", "arr"),
        (4, "sonarr-standard", "arr"),
    ]
    assert _abfrage(motor, "SELECT id, key, fassung_kennung FROM storage_entries ORDER BY id") == [
        (1, "movie:radarr-standard:tmdb:603", "radarr-standard"),
        (2, "movie:radarr-uhd:tmdb:603", "radarr-uhd"),
        (3, "tv:sonarr-uhd:tvdb:81189:s3", "sonarr-uhd"),
        (4, "tv:sonarr-standard:tvdb:81189:s3:r4", "sonarr-standard"),
    ]


def test_eine_unbekannte_stufe_bekommt_die_hauptfassung(alte_datenbank) -> None:
    """Eine Stufe, die weder leer noch ``standard``/``uhd`` ist, blieb bisher
    auf ``fassung_kennung = NULL`` stehen - genau die Zeile, an der
    ``RequestPublic.fassung`` (ohne ``None``) jeden Leseweg mit 500 abbrechen
    liess (Befund des Pruefers von R13b). Jetzt bekommt sie dieselbe
    Hauptfassung ihrer Medienart wie eine Zeile ganz ohne Stufe (Zeile 4 in
    ``_bestand``, Anfrage von vor 4K)."""
    motor = alte_datenbank()
    _bestand(motor)
    with motor.begin() as v:
        # Eine krumme Stufe, wie sie eine Zwischenversion oder ein Eingriff von
        # Hand hinterlassen haben koennte - nicht ``standard``/``uhd``.
        v.exec_driver_sql("UPDATE media_requests SET tier = 'riesengross' WHERE id = 1")

    db_modul.init_db()

    zeile = _abfrage(motor, "SELECT fassung_kennung FROM media_requests WHERE id = 1")[0]
    assert zeile[0] == "radarr-standard"


def test_eine_grossgeschriebene_stufe_wird_noch_erkannt(alte_datenbank) -> None:
    """``'UHD'`` oder ``'4k'`` sind keine leeren/unbekannten Stufen - nur
    anders geschrieben. Der Rueckfall normalisierte bisher nicht und zog
    beide still auf die Standard-Fassung, obwohl klar gemeint war: 4K."""
    motor = alte_datenbank()
    _bestand(motor)
    with motor.begin() as v:
        v.exec_driver_sql("UPDATE media_requests SET tier = 'UHD' WHERE id = 1")

    db_modul.init_db()

    zeile = _abfrage(motor, "SELECT fassung_kennung FROM media_requests WHERE id = 1")[0]
    assert zeile[0] == "radarr-uhd"


def test_eine_unbekannte_stufe_bei_serien_bekommt_die_sonarr_hauptfassung(alte_datenbank) -> None:
    """Dieselbe Absage wie bei Filmen, aber fuer Serien: ``sonarr-standard``,
    nicht ``radarr-standard``. Die Hauptfassung folgt der **Medienart der
    Zeile**, nicht einer festen Instanz."""
    motor = alte_datenbank()
    _bestand(motor)
    with motor.begin() as v:
        # Zeile 3 ist eine Serie (siehe ``_bestand``).
        v.exec_driver_sql("UPDATE media_requests SET tier = 'riesengross' WHERE id = 3")

    db_modul.init_db()

    zeile = _abfrage(motor, "SELECT fassung_kennung FROM media_requests WHERE id = 3")[0]
    assert zeile[0] == "sonarr-standard"


def test_die_haken_werden_rechte_je_fassung(alte_datenbank) -> None:
    motor = alte_datenbank()
    _bestand(motor)

    db_modul.init_db()

    assert _abfrage(
        motor,
        "SELECT user_id, fassung_kennung, anfragen, auto_freigabe FROM fassung_rechte "
        "ORDER BY user_id, fassung_kennung",
    ) == [
        (2, "radarr-uhd", 1, 0),
        # auto_approve_uhd galt fuer beide Medienarten.
        (3, "radarr-uhd", 0, 1),
        (3, "sonarr-uhd", 1, 1),
        (4, "radarr-uhd", 0, 1),
        (4, "sonarr-uhd", 0, 1),
    ]
    einladungen = dict(_abfrage(motor, "SELECT id, invite_fassung_rechte FROM auth_tokens"))
    assert json.loads(einladungen[1]) == [
        {"kennung": "radarr-uhd", "anfragen": True, "auto_freigabe": True},
        {"kennung": "sonarr-uhd", "anfragen": False, "auto_freigabe": True},
    ]
    assert einladungen[2] is None


def test_die_stufen_spalten_sind_danach_weg(alte_datenbank) -> None:
    motor = alte_datenbank()
    _bestand(motor)

    db_modul.init_db()

    for tabelle, spalten in db_modul.STUFEN_SPALTEN.items():
        assert not set(spalten) & _spalten(motor, tabelle), tabelle
    buch = dict(_abfrage(motor, "SELECT wanderung_name, wanderung_herkunft FROM wanderungen"))
    assert buch["_fassungen_einfuehren"] == "ausgefuehrt"


def test_ein_zweiter_start_aendert_nichts(alte_datenbank) -> None:
    motor = alte_datenbank()
    _bestand(motor)
    db_modul.init_db()
    vorher = (
        _abfrage(motor, "SELECT * FROM media_requests ORDER BY id"),
        _abfrage(motor, "SELECT * FROM storage_entries ORDER BY id"),
        _abfrage(motor, "SELECT * FROM fassung_rechte ORDER BY id"),
    )

    db_modul.init_db()

    assert (
        _abfrage(motor, "SELECT * FROM media_requests ORDER BY id"),
        _abfrage(motor, "SELECT * FROM storage_entries ORDER BY id"),
        _abfrage(motor, "SELECT * FROM fassung_rechte ORDER BY id"),
    ) == vorher


def test_ein_abbruch_laesst_alles_wie_es_war(alte_datenbank, monkeypatch) -> None:
    """Alles in einer Transaktion: Bricht der Schritt ab, steht nichts halb."""
    motor = alte_datenbank()
    _bestand(motor)
    echt = db_modul._eintragen

    def abbruch(verbindung, name, herkunft):
        if name == "_fassungen_einfuehren" and herkunft == db_modul.AUSGEFUEHRT:
            raise RuntimeError("Stromausfall")
        echt(verbindung, name, herkunft)

    monkeypatch.setattr(db_modul, "_eintragen", abbruch)
    with pytest.raises(RuntimeError, match="Stromausfall"):
        db_modul.init_db()

    assert "tier" in _spalten(motor, "media_requests")
    assert _abfrage(motor, "SELECT fassung_kennung FROM media_requests") == [(None,)] * 4
    assert _abfrage(motor, "SELECT key FROM storage_entries WHERE id = 1") == [
        ("movie:standard:tmdb:603",)
    ]
    assert _abfrage(motor, "SELECT COUNT(*) FROM fassung_rechte") == [(0,)]
    buch = dict(_abfrage(motor, "SELECT wanderung_name, wanderung_herkunft FROM wanderungen"))
    assert buch["_fassungen_einfuehren"] == "offen"

    monkeypatch.setattr(db_modul, "_eintragen", echt)
    db_modul.init_db()
    assert _abfrage(motor, "SELECT fassung_kennung FROM media_requests WHERE id = 2") == [
        ("radarr-uhd",)
    ]


def test_eine_datenbank_von_vor_4k_bekommt_trotzdem_kennungen(alte_datenbank) -> None:
    """Ohne jede Stufen-Spalte gab es nur Standard - und ohne Kennung waere sie verloren."""
    motor = alte_datenbank(mit_stufe=False, ohne_kennung=True)
    with motor.begin() as v:
        # Rohes SQL, weil das Modell von heute die Kennung verlangt, die es in
        # dieser Datenbank noch gar nicht gibt.
        v.exec_driver_sql(
            "INSERT INTO media_requests (id, user_id, media_type, tmdb_id, title, status, "
            "requested_at, from_watchlist, monitor_future, rating_outdated, file_size_bytes, "
            "hausbestand, trotzdem_gefragt, beschaffung) "
            "VALUES (1, 1, 'tv', 1399, 'Titel', 'searching', ?, 0, 0, 0, 0, 0, 0, 'arr')",
            (JETZT,),
        )

    db_modul.init_db()

    assert _abfrage(motor, "SELECT fassung_kennung FROM media_requests") == [("sonarr-standard",)]


def test_eine_frische_datenbank_wandert_nicht(alte_datenbank) -> None:
    """Das Schema von heute ohne Stufen-Spalten gilt als gewandert."""
    motor = alte_datenbank(mit_stufe=False)

    db_modul.init_db()

    buch = dict(_abfrage(motor, "SELECT wanderung_name, wanderung_herkunft FROM wanderungen"))
    assert buch["_fassungen_einfuehren"] == "vorgefunden"
