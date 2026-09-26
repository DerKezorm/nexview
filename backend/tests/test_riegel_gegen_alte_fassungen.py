"""Der Riegel gegen Fassungen vor 1.0.0 (#note-22).

Wer nach dem Update das alte Abbild wieder startet, beschaedigte seine Daten
lautlos: 0.35.2 legt seine Stufen-Spalten leer wieder an, bricht laufende
4K-Anfragen ab, nimmt jedem Konto das 4K-Recht und baut die Speicherposten neu
auf. Seit 1.0.0 stehen Trigger in der Datenbank, die jedes Schreiben an
Konten, Anfragen und Speicherposten abbrechen, sobald die alten Spalten wieder
da sind. Diese Fassung hat sie nie und schreibt ungehindert.

Die simulierte alte Fassung legt die Spalten so an wie 0.35.2 selbst:
``ALTER TABLE ... ADD COLUMN`` mit Vorgabewert.

Leistung, gemessen am 26.09.2026 (Windows, SQLite 3.50, Datei auf der Platte), mit
der Bedingung ueber ``sqlite_master`` (vorher ``pragma_table_info``, das unter
``trusted_schema=OFF`` scheiterte und doppelt so viel kostete):

* 20.000 Speicherposten mit einem UPDATE ganz geaendert: ohne Riegel 0,04 s,
  mit Riegel 0,27 bis 0,30 s, also gut 11 Mikrosekunden je Zeile
  (``test_der_riegel_kostet_je_zeile_wenig``, dreimal gelaufen).
* So wie der Speicherabgleich schreibt (ORM, ``measured_at`` und Groesse je
  Zeile, 5.000 Posten, dreimal): ohne Riegel 0,12 bis 0,13 s, mit Riegel
  0,20 bis 0,21 s.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DatabaseError

from app import db as db_modul
from app.db import SessionLocal, engine
from app.models import MediaRequest, MediaType, RequestStatus, StorageEntry, StorageState, utcnow
from app.services import storage
from app.services.beschaffung.arr import library
from app.services.beschaffung.arr.radarr import LibraryEntry
from app.services.settings_service import load_settings

from .conftest import auth_headers, create_user

#: Was 0.35.2 beim Start per ALTER TABLE wieder anlegt: Tabelle, Spalte, Typ
#: (``column.type.compile``) und Vorgabe (``_sql_literal``), ausgelesen aus v0.35.2.
ALTE_SPALTEN = (
    ("users", "can_request_uhd_movies", "BOOLEAN", "0"),
    ("users", "can_request_uhd_series", "BOOLEAN", "0"),
    ("users", "auto_approve_uhd", "BOOLEAN", "0"),
    ("media_requests", "tier", "VARCHAR(8)", "'standard'"),
    ("storage_entries", "tier", "VARCHAR(8)", "'standard'"),
)


def _alter_sql(form: str) -> list[str]:
    """Die ALTER-Anweisungen einer alten Fassung.

    ``0.35.2``: genau wie ``_add_missing_columns`` dort, Namen in
    Anfuehrungszeichen - so stehen sie danach auch im Tabellentext, und daran
    haengt der Riegel. ``ohne Anfuehrungszeichen``: wie eine Hand oder eine
    andere Fassung sie schreiben koennte. Beide muessen den Riegel ausloesen.
    """
    if form == "0.35.2":
        return [
            f'ALTER TABLE "{tabelle}" ADD COLUMN "{spalte}" {typ} DEFAULT {vorgabe}'
            for tabelle, spalte, typ, vorgabe in ALTE_SPALTEN
        ]
    return [
        f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ} NOT NULL DEFAULT {vorgabe}"
        for tabelle, spalte, typ, vorgabe in ALTE_SPALTEN
    ]


@pytest.fixture(params=["0.35.2", "ohne Anfuehrungszeichen"])
def form(request: pytest.FixtureRequest) -> str:
    return request.param


def _riegel() -> dict[str, str]:
    with engine.connect() as verbindung:
        return dict(
            verbindung.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND name LIKE 'nexview_riegel_%'"
            ).all()
        )


def _alte_fassung_startet(form: str) -> None:
    with engine.begin() as verbindung:
        for sql in _alter_sql(form):
            verbindung.exec_driver_sql(sql)


def _spalten(tabelle: str) -> set[str]:
    with engine.connect() as verbindung:
        return db_modul._existing_columns(verbindung, tabelle)


def test_der_riegel_steht_nach_jedem_start_und_wird_nicht_jedes_mal_neu_geschrieben() -> None:
    riegel = _riegel()
    assert len(riegel) == 9
    # Wortgleich mit dem Soll: Sonst schriebe jeder Start ihn neu.
    assert riegel == db_modul._riegel_sql()


def test_diese_fassung_schreibt_ungehindert(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anmelden, anfragen, entscheiden, Speicher abgleichen, Konto loeschen."""
    assert _riegel()
    konto = create_user(arr_client, "kim", "passwort-1234")
    kopf = auth_headers(arr_client, "kim", "passwort-1234")
    film = arr_client.get("/api/discover/movie").json()["items"][0]
    anfrage = arr_client.post(
        "/api/requests",
        json={"media_type": "movie", "tmdb_id": film["tmdb_id"], "quality_profile_id": 1,
              "root_folder_path": "/data/Movies"},
        headers=kopf,
    )
    assert anfrage.status_code == 201, anfrage.text
    abgelehnt = arr_client.post(
        f"/api/admin/requests/{anfrage.json()['id']}/reject", json={"reason": "Nein"}
    )
    assert abgelehnt.status_code == 200, abgelehnt.text

    groesse = {"bytes": 1000}

    async def filme(_settings: object, _tier: str = "standard") -> dict:
        return {
            4711: LibraryEntry(
                arr_id=1, has_file=True, monitored=True, size_bytes=groesse["bytes"],
                title="Ein Film", path="/data/Movies/Ein Film",
            )
        }

    async def keine_serien(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
        return {}, {}

    monkeypatch.setattr(library, "movie_library", filme)
    monkeypatch.setattr(library, "series_library", keine_serien)
    with SessionLocal() as sitzung:
        sitzung.add(
            StorageEntry(
                key="movie:radarr-standard:tmdb:1", media_type=MediaType.movie,
                fassung_kennung="radarr-standard", tmdb_id=1, title="Altbestand",
                size_bytes=1, measured_at=utcnow(), state=StorageState.house,
            )
        )
        sitzung.commit()
    for groesse["bytes"] in (1000, 2000):
        with SessionLocal() as sitzung:
            import asyncio

            asyncio.run(storage.abgleichen(sitzung, load_settings(sitzung)))
    with SessionLocal() as sitzung:
        eintrag = sitzung.query(StorageEntry).filter(StorageEntry.tmdb_id == 4711).one()
        assert eintrag.size_bytes == 2000

    assert arr_client.delete(f"/api/users/{konto['id']}").status_code in (200, 204)


def test_eine_alte_fassung_scheitert_beim_schreiben_und_aendert_nichts(
    arr_client: TestClient, form: str
) -> None:
    create_user(arr_client, "kim", "passwort-1234")
    kopf = auth_headers(arr_client, "kim", "passwort-1234")
    film = arr_client.get("/api/discover/movie").json()["items"][0]
    nummer = arr_client.post(
        "/api/requests",
        json={"media_type": "movie", "tmdb_id": film["tmdb_id"], "quality_profile_id": 1,
              "root_folder_path": "/data/Movies"},
        headers=kopf,
    ).json()["id"]
    # Ein Posten, damit das Loeschen ueberhaupt eine Zeile trifft: Der Riegel
    # feuert je Zeile.
    with SessionLocal() as sitzung:
        sitzung.add(
            StorageEntry(
                key="movie:radarr-standard:tmdb:1", media_type=MediaType.movie,
                fassung_kennung="radarr-standard", tmdb_id=1, title="Altbestand",
                size_bytes=1, measured_at=utcnow(), state=StorageState.house,
            )
        )
        sitzung.commit()

    _alte_fassung_startet(form)
    try:
        # Was 0.35.2 als Erstes schreibt: den Abbruch der Anfrage, die
        # Anmeldung (last_login_at), den Neuaufbau der Speicherposten.
        for sql, werte in (
            ("UPDATE media_requests SET status = 'cancelled' WHERE id = ?", (nummer,)),
            ("UPDATE users SET last_login_at = CURRENT_TIMESTAMP", ()),
            ("DELETE FROM storage_entries", ()),
            (
                (
                    "INSERT INTO storage_entries (key, media_type, title, size_bytes, path, "
                    "arr_managed, measured_at, state) VALUES ('movie:standard:tmdb:1', 'movie', "
                    "'x', 1, '', 1, CURRENT_TIMESTAMP, 'house')"
                ),
                (),
            ),
        ):
            with (
                pytest.raises(DatabaseError, match="belongs to Nexview"),
                engine.begin() as verbindung,
            ):
                verbindung.exec_driver_sql(sql, werte)

        with SessionLocal() as sitzung:
            assert sitzung.get(MediaRequest, nummer).status == RequestStatus.pending_approval
    finally:
        # Der naechste Start dieser Fassung raeumt die Spalten wieder ab, und
        # danach schreibt sie wie vorher.
        db_modul.init_db()

    for tabelle, spalte, _, _ in ALTE_SPALTEN:
        assert spalte not in _spalten(tabelle), f"{tabelle}.{spalte} steht noch"
    assert len(_riegel()) == 9
    kopf = auth_headers(arr_client, "kim", "passwort-1234")
    assert arr_client.delete(f"/api/requests/{nummer}", headers=kopf).status_code in (200, 204)


def test_der_riegel_haelt_auch_unter_trusted_schema_off(arr_client: TestClient, form: str) -> None:
    """Pruefer: Mit ``pragma_table_info`` im Trigger scheiterte hier jedes Schreiben.

    SQLite, das mit ``SQLITE_TRUSTED_SCHEMA=0`` gebaut ist, verbietet Triggern
    virtuelle Tabellen ("unsafe use of virtual table") - auch dieser Fassung.
    Der Riegel liest deshalb den Tabellentext aus ``sqlite_master``.
    """
    create_user(arr_client, "kim", "passwort-1234")
    with engine.connect() as verbindung:
        verbindung.exec_driver_sql("PRAGMA trusted_schema=OFF")
        try:
            # Diese Fassung schreibt ungehindert.
            verbindung.exec_driver_sql("UPDATE users SET last_login_at = CURRENT_TIMESTAMP")
            verbindung.exec_driver_sql(
                "INSERT INTO storage_entries (key, media_type, fassung_kennung, title, "
                "size_bytes, path, arr_managed, measured_at, state) VALUES "
                "('movie:radarr-standard:tmdb:9', 'movie', 'radarr-standard', 'x', 1, '', 1, "
                "CURRENT_TIMESTAMP, 'house')"
            )
            verbindung.exec_driver_sql("DELETE FROM storage_entries")
            verbindung.commit()

            # Die alte Fassung nicht.
            for sql in _alter_sql(form):
                verbindung.exec_driver_sql(sql)
            verbindung.commit()
            with pytest.raises(DatabaseError, match="belongs to Nexview"):
                verbindung.exec_driver_sql("UPDATE users SET last_login_at = CURRENT_TIMESTAMP")
            verbindung.rollback()
        finally:
            verbindung.exec_driver_sql("PRAGMA trusted_schema=ON")
    db_modul.init_db()


def test_die_warnung_nennt_verworfene_werte(
    arr_client: TestClient, caplog: pytest.LogCaptureFixture, form: str
) -> None:
    """Hat die alte Fassung doch geschrieben (etwa ohne Riegel), geht das mit der Spalte.

    Die Warnung zaehlt je Spalte die Zeilen, die nicht die Vorgabe tragen, damit
    ein Betreiber merkt, dass dort etwas stand.
    """
    create_user(arr_client, "kim", "passwort-1234")
    create_user(arr_client, "alex", "passwort-1234")
    with engine.begin() as verbindung:
        for name in _riegel():
            verbindung.exec_driver_sql(f'DROP TRIGGER "{name}"')
    _alte_fassung_startet(form)
    with engine.begin() as verbindung:
        verbindung.exec_driver_sql(
            "UPDATE users SET can_request_uhd_movies = 1 WHERE username IN ('kim', 'alex')"
        )

    with caplog.at_level("WARNING", logger="nexview.db"):
        db_modul.init_db()

    warnung = [e.getMessage() for e in caplog.records if "old columns again" in e.getMessage()]
    assert len(warnung) == 1, warnung
    assert "users.can_request_uhd_movies (2 row(s))" in warnung[0]
    assert "users.auto_approve_uhd (0 row(s))" in warnung[0]
    assert "media_requests.tier (0 row(s))" in warnung[0]
    assert len(_riegel()) == 9


def test_der_riegel_kostet_je_zeile_wenig() -> None:
    """Gemessen, nicht geschaetzt: 20.000 Speicherposten einmal ganz aendern.

    Die Grenze ist grosszuegig, damit ein langsamer Rechner nicht rot wird;
    die gemessenen Zahlen stehen oben im Kopf der Datei.
    """
    zeilen = 20_000
    with engine.begin() as verbindung:
        verbindung.exec_driver_sql(
            "INSERT INTO storage_entries (key, media_type, fassung_kennung, title, size_bytes, "
            "path, arr_managed, measured_at, state) "
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?) "
            "SELECT 'movie:radarr-standard:tmdb:' || i, 'movie', 'radarr-standard', 'x', i, "
            "'', 1, CURRENT_TIMESTAMP, 'house' FROM n",
            (zeilen,),
        )

    def messen() -> float:
        anfang = time.perf_counter()
        with engine.begin() as verbindung:
            verbindung.exec_driver_sql("UPDATE storage_entries SET size_bytes = size_bytes + 1")
        return time.perf_counter() - anfang

    mit = messen()
    with engine.begin() as verbindung:
        for name in _riegel():
            verbindung.exec_driver_sql(f'DROP TRIGGER "{name}"')
    try:
        ohne = messen()
    finally:
        db_modul.init_db()
    assert len(_riegel()) == 9
    print(f"Riegel: {zeilen} Zeilen mit {mit:.3f} s, ohne {ohne:.3f} s")
    assert (mit - ohne) / zeilen < 100e-6
