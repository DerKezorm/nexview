"""Was passiert mit einer bestehenden Datenbank nach einem Update?

Das ist der gefaehrlichste Moment im Betrieb: Auf dem NAS laeuft eine
Datenbank voller Konten und Anfragen, und der Container bringt ploetzlich ein
Schema mit, das mehr Tabellen und Spalten kennt. Geht dabei etwas schief,
merkt es der Nutzer erst, wenn nichts mehr startet.

Die Tests hier bauen deshalb absichtlich eine *aeltere* Datenbank nach und
lassen ``init_db()` darauf los - genau wie beim echten Update.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import db as db_modul
from app.models import (
    Base,
    ChannelKind,
    ChannelMessage,
    ChannelTarget,
    Notification,
    NotificationType,
    Role,
    User,
)


def _alte_datenbank(pfad: Path) -> None:
    """Eine Datenbank im Stand einer aelteren Version erzeugen.

    Nachgebildet wird der Zustand vor der Konten-Ueberarbeitung: die
    Benutzertabelle kennt weder E-Mail-Adresse noch Bestaetigung, und die
    Tabelle fuer Einladungs- und Passwortlinks gibt es ueberhaupt nicht.
    """
    engine = create_engine(f"sqlite:///{pfad}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER NOT NULL PRIMARY KEY,
                    username VARCHAR(50) NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL,
                    display_name VARCHAR(100),
                    language VARCHAR(5) NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    auto_approve BOOLEAN NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users
                    (username, password_hash, role, display_name, language,
                     is_active, auto_approve, created_at)
                VALUES
                    ('altbenutzer', 'egal', 'user', 'Alter Hase', 'de',
                     1, 0, '2026-01-01 12:00:00')
                """
            )
        )
        # Eine Anfrage im alten Stand: ohne Stufe, denn es gab nur eine
        # Radarr-Instanz. Genau daran haengt der Test weiter unten.
        connection.execute(
            text(
                """
                CREATE TABLE media_requests (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    media_type VARCHAR(10) NOT NULL,
                    tmdb_id INTEGER NOT NULL,
                    title VARCHAR(300) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    requested_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO media_requests
                    (user_id, media_type, tmdb_id, title, status, requested_at)
                VALUES (1, 'movie', 4711, 'Alter Film', 'downloaded',
                        '2026-01-02 12:00:00')
                """
            )
        )
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_db():
    """Die gemeinsame Vorbereitung aushebeln.

    ``conftest.clean_db`` ruft ``init_db()`` auf der echten Testdatenbank auf.
    Hier soll ausschliesslich die nachgebaute alte Datenbank angefasst werden -
    sonst zaehlen die Tests Sicherungen, die gar nicht von ihnen stammen.
    """
    yield


@pytest.fixture
def alte_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``init_db()`` auf eine alte Datenbank in einem eigenen Verzeichnis richten."""
    datenverzeichnis = tmp_path / "data"
    datenverzeichnis.mkdir()
    db_pfad = datenverzeichnis / "nexview.db"
    _alte_datenbank(db_pfad)

    engine = create_engine(f"sqlite:///{db_pfad}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_modul, "engine", engine)
    # ``db_path`` und der Ort der Sicherungen leiten sich beide aus
    # ``data_dir`` ab - ein Umbiegen genuegt also.
    monkeypatch.setattr(db_modul._settings, "data_dir", datenverzeichnis)

    yield db_pfad
    engine.dispose()


def test_update_ergaenzt_fehlende_spalten_und_tabellen(alte_installation: Path) -> None:
    """Nach dem Update kennt die alte Datenbank das komplette Schema."""
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        tabellen = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        spalten = db_modul._existing_columns(connection, "users")

    # Neue Tabelle ist dazugekommen ...
    assert "auth_tokens" in tabellen
    # ... und die neuen Spalten stecken in der alten Tabelle.
    assert {"email", "email_verified", "quota_reset_at"} <= spalten

    # Jede im Modell definierte Tabelle muss existieren.
    fehlend = {t.name for t in Base.metadata.sorted_tables} - tabellen
    assert not fehlend, f"Nach dem Update fehlen noch Tabellen: {sorted(fehlend)}"


def test_update_ergaenzt_die_merkliste_beendeter_sitzungen(alte_installation: Path) -> None:
    """Die Tabelle fuers Abmelden (1.0.0) entsteht auf einer alten Datenbank mit."""
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        spalten = db_modul._existing_columns(connection, "beendete_sitzungen")
        indizes = db_modul._existing_indexes(connection, "beendete_sitzungen")

    assert spalten == {"sitzung", "user_id", "beendet_am", "bis"}
    assert {"ix_beendete_sitzungen_user_id", "ix_beendete_sitzungen_bis"} <= indizes


def test_update_ergaenzt_die_media_server_verknuepfung(alte_installation: Path) -> None:
    """Spalten *und* der eindeutige Index muessen nachgezogen werden.

    Der Index ist die heikle Haelfte: SQLite kann einer bestehenden Tabelle
    keine Constraints nachtragen, einen Index dagegen schon. Genau deshalb ist
    die Regel "ein Media-Server-Konto gehoert zu genau einem Nexview-Konto" als
    Index formuliert - waere sie ein ``UniqueConstraint``, gaelte sie auf jeder
    aktualisierten Installation stillschweigend nicht. Auffallen wuerde das
    nie, weil die uebrigen Tests immer auf frischen Tabellen laufen.
    """
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        spalten = db_modul._existing_columns(connection, "users")
        indizes = db_modul._existing_indexes(connection, "users")

    assert {"mediaserver_provider", "mediaserver_account_id", "mediaserver_linked_at"} <= spalten
    assert "ix_users_mediaserver_konto" in indizes

    # Und er muss auch wirklich greifen.
    with db_modul.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE users SET mediaserver_provider='plex', mediaserver_account_id='4711'"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO users (username, password_hash, role, language, is_active,
                               auto_approve, created_at, mediaserver_provider,
                               mediaserver_account_id)
            VALUES ('zweiter', 'egal', 'user', 'de', 1, 0, '2026-01-01 12:00:00',
                    'plex', 'anderes')
            """
        )

    with (
        pytest.raises(Exception),  # noqa: B017 - SQLite meldet IntegrityError
        db_modul.engine.begin() as connection,
    ):
            connection.exec_driver_sql(
                "UPDATE users SET mediaserver_account_id='4711' WHERE username='zweiter'"
            )


def test_update_ergaenzt_den_merklisten_zwischenspeicher(alte_installation: Path) -> None:
    """Die Tabelle fuer die Zuordnung und ihr eindeutiger Index kommen mit.

    Derselbe Grund wie eine Ebene hoeher: Der Schluessel (Anbieter, Kennung)
    ist bewusst ein **Index** und kein ``UniqueConstraint`` - nur so gilt er
    auch auf einer aktualisierten Installation.
    """
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        indizes = db_modul._existing_indexes(connection, "watchlist_lookup")
        spalten = db_modul._existing_columns(connection, "users")

    assert "ix_watchlist_lookup_guid" in indizes
    assert "watchlist_token" in spalten


def test_update_behaelt_vorhandene_daten(alte_installation: Path) -> None:
    """Der Bestand darf beim Update nicht verlorengehen."""
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        zeilen = connection.exec_driver_sql(
            "SELECT username, display_name, email, email_verified FROM users"
        ).fetchall()

    assert len(zeilen) == 1
    name, anzeige, email, bestaetigt = zeilen[0]
    assert name == "altbenutzer"
    assert anzeige == "Alter Hase"
    # Neue Spalten stehen auf ihrem Standardwert, nicht auf Unsinn.
    assert email is None
    assert not bestaetigt


def test_update_legt_sicherung_an(alte_installation: Path) -> None:
    """Vor der ersten Aenderung entsteht eine Kopie der Datenbank."""
    db_modul.init_db()

    sicherungen = list((alte_installation.parent / "sicherungen").glob("nexview-automatisch-*.db"))
    assert len(sicherungen) == 1

    # Die Kopie muss den alten Stand enthalten - also lesbar sein und den
    # Benutzer von vorher fuehren.
    kopie = create_engine(f"sqlite:///{sicherungen[0]}")
    with kopie.connect() as connection:
        namen = [row[0] for row in connection.exec_driver_sql("SELECT username FROM users")]
    kopie.dispose()
    assert namen == ["altbenutzer"]


def _datenbank_von_0_35_2(pfad: Path, *, quittiert: str | None) -> None:
    """Der echte Fall: Buch nur mit aelterer Fassung, Daten von 0.35.2.

    Das Wanderungsbuch gibt es seit 0.26.2, und bis 0.35.2 kam kein
    Einmal-Schritt dazu - eine Installation, die ueber 0.30.0 kam, traegt dort
    nur 0.30.0. ``quittiert`` ist das "Was ist neu", das ein Administrator unter
    0.35.2 weggeklickt hat (``users.changelog_gesehen``).
    """
    from app.models import Wanderung

    alt = create_engine(f"sqlite:///{pfad}")
    Wanderung.__table__.create(bind=alt)
    with alt.begin() as verbindung:
        for name in ("_verbindung_in_die_tabelle", "_bewertungen_in_die_tabelle"):
            verbindung.exec_driver_sql(
                "INSERT INTO wanderungen (wanderung_name, wanderung_am, wanderung_herkunft, "
                "wanderung_version) VALUES (?, '2026-08-01 00:00:00', 'vorgefunden', '0.30.0')",
                (name,),
            )
        verbindung.exec_driver_sql("ALTER TABLE users ADD COLUMN changelog_gesehen VARCHAR(20)")
        verbindung.exec_driver_sql("UPDATE users SET changelog_gesehen = ?", (quittiert,))
    alt.dispose()


def _sicherung_vor_dem_update(pfad: Path) -> tuple[Path, dict]:
    import json

    sicherungen = list((pfad.parent / "sicherungen").glob("nexview-automatisch-*.db"))
    assert len(sicherungen) == 1
    return sicherungen[0], json.loads(sicherungen[0].with_suffix(".json").read_text(encoding="utf-8"))


def test_die_sicherung_vor_dem_update_behauptet_keine_fassung_die_sie_nicht_kennt(
    alte_installation: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Pruefer zu #note-22: Aus dem Buch allein wurde "0.30.0" - die Daten waren von 0.35.2.

    Vor 1.0.0 schrieb keine Fassung sich selbst in die Datenbank. Genau ist die
    Angabe deshalb nie; sie sagt "oder spaeter" und nennt die hoechste Spur.
    """
    from app import __version__
    from app.services import sicherung

    _datenbank_von_0_35_2(alte_installation, quittiert=None)

    with caplog.at_level("INFO", logger="nexview.db"):
        db_modul.init_db()

    datei, brief = _sicherung_vor_dem_update(alte_installation)
    assert datei.name.startswith("nexview-automatisch-0.30.0-or-later-")
    assert brief["version"] == "0.30.0 or later"
    assert brief["kommentar"] == f"{sicherung.VOR_UPDATE}{__version__}"
    # So prueft 0.35.2 selbst, ob es einspielen darf: nicht neuer als es.
    assert sicherung._als_zahlen(brief["version"]) <= (0, 35, 2)

    # Das Protokoll nennt den Rueckweg samt Datei, ohne eine Fassung zu behaupten.
    nennung = [
        eintrag.getMessage()
        for eintrag in caplog.records
        if eintrag.getMessage().startswith("Database updated from")
    ]
    assert len(nennung) == 1, nennung
    assert "Nexview 0.30.0 or later" in nennung[0]
    assert datei.name in nennung[0]
    assert "nexview.db-wal" in nennung[0]

    # Ein zweiter Start ohne Schemaaenderung sagt nichts mehr.
    caplog.clear()
    with caplog.at_level("INFO", logger="nexview.db"):
        db_modul.init_db()
    assert not [e for e in caplog.records if e.getMessage().startswith("Database updated from")]


def test_das_quittierte_was_ist_neu_kommt_naeher_an_die_fassung(alte_installation: Path) -> None:
    from app.services import sicherung

    _datenbank_von_0_35_2(alte_installation, quittiert="0.35.2")

    db_modul.init_db()

    datei, brief = _sicherung_vor_dem_update(alte_installation)
    assert brief["version"] == "0.35.2 or later"
    assert datei.name.startswith("nexview-automatisch-0.35.2-or-later-")
    assert sicherung._als_zahlen(brief["version"]) <= (0, 35, 2)


def _fremde_sicherung(pfad: Path, name: str, version: str) -> None:
    """Eine Sicherung im Ordner, wie eine fruehere Fassung sie hinterlassen hat."""
    import json

    ordner = pfad.parent / "sicherungen"
    ordner.mkdir(exist_ok=True)
    (ordner / f"{name}.db").write_text("x", encoding="utf-8")
    (ordner / f"{name}.json").write_text(
        json.dumps({"version": version, "schema": "", "erstellt": "", "art": "manuell",
                    "kommentar": ""}),
        encoding="utf-8",
    )


def test_die_steckbriefe_im_ordner_zaehlen_als_spur(alte_installation: Path) -> None:
    """Die regelmaessige Sicherung von 0.35.2 sagt mehr als das Buch mit 0.30.0."""
    _datenbank_von_0_35_2(alte_installation, quittiert=None)
    _fremde_sicherung(alte_installation, "takt-von-0.35.2", "0.35.2")

    db_modul.init_db()

    datei, brief = _sicherung_vor_dem_update(alte_installation)
    assert brief["version"] == "0.35.2 or later"
    assert datei.name.startswith("nexview-automatisch-0.35.2-or-later-")


def test_nach_dem_rueckweg_zaehlen_spuren_ab_1_0_0_nicht(alte_installation: Path) -> None:
    """Pruefer: Update, Sicherung unter 1.0.0, Dateitausch auf 0.35.2, erneutes Update.

    Die Datenbank ist dann wieder die von 0.35.2 (Merker 0), aber im Ordner liegt
    der Steckbrief einer Sicherung von 1.0.0. Er zaehlte mit, und die neue
    Sicherung hiess "1.0.0 or later" - 0.35.2 haette sie nicht mehr eingespielt.
    Jede Fassung ab 1.0.0 haette den Merker gesetzt; steht er auf 0, zaehlt
    nur, was darunter liegt.
    """
    from app.services import sicherung

    _datenbank_von_0_35_2(alte_installation, quittiert="0.35.2")
    _fremde_sicherung(alte_installation, "von-hand-unter-1.0.0", "1.0.0")
    _fremde_sicherung(alte_installation, "von-hand-unter-1.2.0", "1.2.0 or later")

    db_modul.init_db()

    _, brief = _sicherung_vor_dem_update(alte_installation)
    assert brief["version"] == "0.35.2 or later"
    assert sicherung._als_zahlen(brief["version"]) <= (0, 35, 2)


def test_ab_1_0_0_ist_die_fassung_der_daten_genau(alte_installation: Path) -> None:
    """Der Merker haelt sie fest; eine Sicherung vor dem naechsten Update heisst danach."""
    db_modul.init_db()
    merker = create_engine(f"sqlite:///{alte_installation}")
    with merker.begin() as verbindung:
        verbindung.exec_driver_sql(f"PRAGMA user_version = {db_modul._fassungszahl('0.99.1')}")
    merker.dispose()

    assert db_modul._datenstand() == "0.99.1"


def _sicherung_scheitert(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import sicherung

    def voll(**_: object) -> Path:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sicherung, "anlegen", voll)


def test_ohne_sicherung_wandert_nichts_ohne_rueckweg(
    alte_installation: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Scheitert die Sicherung vor einer Wanderung, die Daten umdeutet, haelt der Start an.

    Sie ist der einzige Rueckweg (#note-22). Frueher lief der Start weiter und
    entfernte die Stufen-Spalten ohne Kopie.
    """
    _sicherung_scheitert(monkeypatch)

    with caplog.at_level("CRITICAL", logger="nexview.db"), pytest.raises(db_modul.SicherungFehlt):
        db_modul.init_db()

    kritisch = [e.getMessage() for e in caplog.records if e.levelname == "CRITICAL"]
    assert len(kritisch) == 1
    assert "_fassungen_einfuehren" in kritisch[0]
    assert "sicherungen/" in kritisch[0]
    pruefen = create_engine(f"sqlite:///{alte_installation}")
    with pruefen.connect() as verbindung:
        spalten = db_modul._existing_columns(verbindung, "media_requests")
    pruefen.dispose()
    assert "fassung_kennung" not in spalten, "die Wanderung lief trotzdem"


def test_ohne_sicherung_laeuft_eine_reine_ergaenzung_weiter(
    alte_installation: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nur eine fehlende Spalte: Da ist nichts umzudeuten, der Start geht weiter."""
    db_modul.init_db()
    with db_modul.engine.begin() as verbindung:
        verbindung.exec_driver_sql("ALTER TABLE download_haenger DROP COLUMN aktionen")
    _sicherung_scheitert(monkeypatch)

    db_modul.init_db()

    with db_modul.engine.connect() as verbindung:
        assert "aktionen" in db_modul._existing_columns(verbindung, "download_haenger")


def test_zweiter_start_aendert_nichts_mehr(alte_installation: Path) -> None:
    """Ohne Schemaaenderung darf keine weitere Sicherung entstehen.

    Sonst liefe bei jedem Neustart des Containers eine Kopie mit - auf einem
    NAS mit grosser Datenbank waere das schnell unangenehm.
    """
    db_modul.init_db()
    assert db_modul._pending_changes() == []

    db_modul.init_db()
    sicherungen = list((alte_installation.parent / "sicherungen").glob("nexview-automatisch-*.db"))
    assert len(sicherungen) == 1


def test_zweiter_start_schreibt_keine_zweite_zeile_ins_wanderungsbuch(
    alte_installation: Path,
) -> None:
    """Dasselbe eine Ebene tiefer: Das Buch darf nicht mitwachsen.

    ⚠️ **Hier ist die richtige Datei dafuer**, denn hier ist ``clean_db``
    ausgehebelt. In der gemeinsamen Vorbereitung wird das Buch vor jedem Test
    geleert; ein Test dort wuerde also nie den Zustand sehen, um den es geht -
    naemlich ein Buch, das einen Start ueberlebt hat.

    ⚠️ **Die Zeilen allein beweisen nichts.** Ein Buch, das beim zweiten Start
    unveraendert dasteht, kann trotzdem wirkungslos sein - die Eintraege
    aendern sich naemlich auch dann nicht, wenn jeder Schritt sie ignoriert
    und einfach wieder losrennt. Deshalb haengt hier eine Wirkung dran: Nach
    der Wanderung wird eine 0 gesetzt ("darf nichts anfragen"), und die muss
    den zweiten Start ueberleben. Kippt sie nach -1, hat das Buch nichts
    gesperrt - das ist exakt der Fehler, wegen dem es das Buch gibt.
    """
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        erster_stand = connection.exec_driver_sql(
            "SELECT wanderung_name, wanderung_am, wanderung_herkunft FROM wanderungen"
            " ORDER BY wanderung_name"
        ).all()

    assert {zeile[0] for zeile in erster_stand} == set(db_modul.EINMAL_SCHRITTE)

    # Eine bewusste 0 nach der Wanderung - die neue Bedeutung des Wertes.
    with db_modul.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE users SET storage_limit_gb = 0 WHERE username = 'altbenutzer'"
        )

    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        zweiter_stand = connection.exec_driver_sql(
            "SELECT wanderung_name, wanderung_am, wanderung_herkunft FROM wanderungen"
            " ORDER BY wanderung_name"
        ).all()
        grenze = connection.exec_driver_sql(
            "SELECT storage_limit_gb FROM users WHERE username = 'altbenutzer'"
        ).scalar()

    # Nicht nur gleich viele Zeilen: **dieselben**, mit demselben Zeitstempel.
    # Eine neu geschriebene Zeile mit derselben Anzahl waere genau der Fall,
    # den eine reine Zaehlung durchgehen liesse.
    assert zweiter_stand == erster_stand
    assert grenze == 0, "die nach der Wanderung gesetzte 0 wurde wieder umgedeutet"


def test_frische_installation_ohne_sicherung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Beim allerersten Start gibt es nichts zu sichern."""
    datenverzeichnis = tmp_path / "data"
    datenverzeichnis.mkdir()
    engine = create_engine(f"sqlite:///{datenverzeichnis / 'nexview.db'}")
    monkeypatch.setattr(db_modul, "engine", engine)
    monkeypatch.setattr(db_modul._settings, "data_dir", datenverzeichnis)

    db_modul.init_db()
    engine.dispose()

    assert not (datenverzeichnis / "sicherungen").exists()


def test_alte_sicherungen_werden_aufgeraeumt(tmp_path: Path) -> None:
    """Es bleiben hoechstens die juengsten Kopien liegen."""
    ordner = tmp_path / "sicherungen"
    ordner.mkdir()
    for nummer in range(8):
        datei = ordner / f"nexview-automatisch-0.{nummer}.0-2026-01-0{nummer + 1}_120000.db"
        datei.write_text("x", encoding="utf-8")
        # Klar unterscheidbare Zeitstempel, damit die Reihenfolge eindeutig ist.
        import os

        os.utime(datei, (1_700_000_000 + nummer * 60, 1_700_000_000 + nummer * 60))

    db_modul._prune_backups(ordner, behalten=3)

    uebrig = sorted(p.name for p in ordner.glob("*.db"))
    assert len(uebrig) == 3
    # Die drei juengsten muessen es sein.
    assert all("0.5.0" in n or "0.6.0" in n or "0.7.0" in n for n in uebrig), uebrig


def test_die_letzte_sicherung_vor_einem_update_bleibt_liegen(tmp_path: Path) -> None:
    """Sie ist der Rueckweg auf die alte Fassung und faellt nicht der Rotation zum Opfer.

    Mit dem woechentlichen Takt und fuenf Plaetzen war sie nach rund fuenf
    Wochen weg. Aeltere Sicherungen vor Updates laufen dagegen normal mit.
    """
    import json
    import os

    from app.services import sicherung

    ordner = tmp_path / "sicherungen"
    ordner.mkdir()

    def anlegen(name: str, minute: int, kommentar: str = "") -> None:
        datei = ordner / f"{name}.db"
        datei.write_text("x", encoding="utf-8")
        (ordner / f"{name}.json").write_text(
            json.dumps(
                {"version": "1.0.0", "schema": "", "erstellt": "", "art": "automatisch",
                 "kommentar": kommentar}
            ),
            encoding="utf-8",
        )
        os.utime(datei, (1_700_000_000 + minute * 60,) * 2)

    anlegen("vor-update-alt", 0, f"{sicherung.VOR_UPDATE}0.35.0")
    anlegen("vor-update-neu", 1, f"{sicherung.VOR_UPDATE}1.0.0")
    for nummer in range(6):
        anlegen(f"takt-{nummer}", 10 + nummer)

    sicherung.aufraeumen(behalten=3, ordner_=ordner)

    uebrig = sorted(p.stem for p in ordner.glob("*.db"))
    assert uebrig == ["takt-3", "takt-4", "takt-5", "vor-update-neu"]
    assert sicherung.vor_update(ordner).stem == "vor-update-neu"


def test_eine_aeltere_fassung_verweigert_den_start(alte_installation: Path) -> None:
    """Hat eine neuere Fassung schon hier gearbeitet, startet eine aeltere nicht.

    Sie wuerde die Daten lautlos veraendern, wie 0.35.2 auf einer Datenbank von
    1.0.0 (#note-22). Die Meldung nennt den Rueckweg samt Sicherung.
    """
    from app import __version__

    db_modul.init_db()
    assert _merker(alte_installation) == db_modul._fassungszahl(__version__)
    rueckweg = next((alte_installation.parent / "sicherungen").glob("*.db"))

    # Eine spaetere Fassung ist hier gelaufen.
    neuer = create_engine(f"sqlite:///{alte_installation}")
    with neuer.begin() as verbindung:
        verbindung.exec_driver_sql(f"PRAGMA user_version = {db_modul._fassungszahl('1.2.0')}")
    neuer.dispose()
    buch_vorher = _buch(alte_installation)

    with pytest.raises(db_modul.NeuereDatenbank) as abbruch:
        db_modul.init_db()

    meldung = str(abbruch.value)
    assert "1.2.0" in meldung
    assert __version__ in meldung
    assert rueckweg.name in meldung
    # Nichts angefasst: weder Buch noch Merker noch eine neue Sicherung.
    assert _buch(alte_installation) == buch_vorher
    assert _merker(alte_installation) == db_modul._fassungszahl("1.2.0")
    assert len(list((alte_installation.parent / "sicherungen").glob("*.db"))) == 1


def test_der_merker_steigt_nur(alte_installation: Path) -> None:
    from app import __version__

    db_modul.init_db()
    aelter = create_engine(f"sqlite:///{alte_installation}")
    with aelter.begin() as verbindung:
        verbindung.exec_driver_sql(f"PRAGMA user_version = {db_modul._fassungszahl('0.35.2')}")
    aelter.dispose()

    db_modul.init_db()

    assert _merker(alte_installation) == db_modul._fassungszahl(__version__)
    assert db_modul._fassung_aus_zahl(db_modul._fassungszahl("1.2.3")) == "1.2.3"


def _merker(pfad: Path) -> int:
    lesen = create_engine(f"sqlite:///{pfad}")
    with lesen.connect() as verbindung:
        zahl = verbindung.exec_driver_sql("PRAGMA user_version").scalar()
    lesen.dispose()
    return zahl


def _buch(pfad: Path) -> list:
    lesen = create_engine(f"sqlite:///{pfad}")
    with lesen.connect() as verbindung:
        zeilen = verbindung.exec_driver_sql("SELECT * FROM wanderungen ORDER BY 1").all()
    lesen.dispose()
    return [tuple(zeile) for zeile in zeilen]


def test_update_ordnet_bestandsanfragen_der_standard_stufe_zu(alte_installation: Path) -> None:
    """Was vor der 4K-Instanz angefragt wurde, gehoert zur Standard-Fassung.

    Bliebe die Kennung leer, wuerde der Poller diese Anfragen gegen die falsche
    Bibliothek pruefen - und eine 1080p-Datei koennte eine 4K-Anfrage
    abschliessen. Deshalb ist die Vorgabe hier keine Kosmetik.

    Seit dem Fassungsmodell ist das die Kennung ``radarr-standard``; die
    4K-Haken am Konto sind in ``fassung_rechte`` aufgegangen, die
    Profil-Sperren je Stufe bleiben Spalten.
    """
    db_modul.init_db()

    engine = create_engine(f"sqlite:///{alte_installation}")
    with engine.begin() as connection:
        fassungen = [
            zeile[0]
            for zeile in connection.execute(text("SELECT fassung_kennung FROM media_requests"))
        ]
        spalten = {
            zeile[1] for zeile in connection.execute(text("PRAGMA table_info(users)"))
        }
        tabellen = {
            zeile[0]
            for zeile in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
    engine.dispose()

    assert fassungen == ["radarr-standard"]
    assert {"blocked_movie_uhd_profiles", "blocked_series_uhd_profiles"} <= spalten
    assert "fassung_rechte" in tabellen


def test_update_behaelt_die_automatische_freigabe(alte_installation: Path) -> None:
    """Ein Konto mit Auto-Freigabe behaelt sie nach dem Update.

    Die Freigabe ist jetzt je Medienart getrennt. Bekaemen die neuen Spalten
    schlicht ``false``, muessten alle bisher automatisch freigegebenen
    Benutzer ploetzlich warten - eine Verhaltensaenderung, die niemand
    angeordnet hat und die erst auffiele, wenn sich jemand beschwert.
    """
    from app.models import MediaType, User

    engine = create_engine(f"sqlite:///{alte_installation}")
    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET auto_approve = 1"))
    engine.dispose()

    db_modul.init_db()

    engine = create_engine(f"sqlite:///{alte_installation}")
    with engine.begin() as connection:
        zeile = connection.execute(
            text(
                "SELECT auto_approve, auto_approve_movies, auto_approve_series "
                "FROM users WHERE username = 'altbenutzer'"
            )
        ).one()
    engine.dispose()

    gemeinsam, filme, serien = zeile
    assert gemeinsam == 1
    # Nicht eigens gesetzt - genau das laesst den alten Wert weitergelten.
    assert filme is None
    assert serien is None

    # Und die Ableitung macht daraus wieder "ja".
    benutzer = User(auto_approve=True, auto_approve_movies=None, auto_approve_series=None)
    assert benutzer.auto_approve_for(MediaType.movie) is True
    assert benutzer.auto_approve_for(MediaType.tv) is True


def test_update_haelt_bestehende_plex_titel_fuer_vorhanden(alte_installation: Path) -> None:
    """Beim Update gilt jeder bekannte Media-Server-Titel weiter als vorhanden.

    Neu ist, dass Nexview die Aufloesung mitfuehrt (``has_standard`` /
    ``has_uhd``), um eine Plex-Kopie einer Instanz zuordnen zu koennen. Fuer
    Bestandszeilen ist sie unbekannt: Sie stammen aus einem Abgleich, der noch
    gar nicht danach gefragt hat.

    ``has_standard`` muss deshalb auf ``1`` landen. Stuende dort ``0``, waeren
    nach einem Update auf einen Schlag alle Titel wieder "anfragbar" - und die
    Leute wuerden herunterladen, was sie laengst haben. Bis zum naechsten
    Abgleich mit dem Media-Server bleibt es beim Verhalten von vorher.
    """
    # Eine Installation, die die Tabelle schon hat - aber noch ohne die
    # beiden Spalten. Nur so wird wirklich ein ALTER TABLE geprueft und nicht
    # ein frisches CREATE TABLE.
    engine = create_engine(f"sqlite:///{alte_installation}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE media_server_library (
                    id INTEGER PRIMARY KEY,
                    provider VARCHAR(20) NOT NULL,
                    media_type VARCHAR(5) NOT NULL,
                    guid VARCHAR(255) NOT NULL,
                    rating_key VARCHAR(40),
                    owner_watched BOOLEAN NOT NULL DEFAULT 0,
                    tmdb_id INTEGER,
                    tvdb_id INTEGER,
                    imdb_id VARCHAR(20),
                    title VARCHAR(500) NOT NULL,
                    title_key VARCHAR(500) NOT NULL DEFAULT '',
                    year INTEGER,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO media_server_library "
                "(provider, media_type, guid, tmdb_id, title, title_key, year) "
                "VALUES ('plex', 'movie', 'plex://film/1', 603, 'Matrix', 'matrix', 1999)"
            )
        )
    engine.dispose()

    db_modul.init_db()

    engine = create_engine(f"sqlite:///{alte_installation}")
    with engine.begin() as connection:
        zeile = connection.execute(
            text("SELECT has_standard, has_uhd FROM media_server_library WHERE tmdb_id = 603")
        ).one()
    engine.dispose()

    assert zeile[0] == 1, "Bestandstitel gelten sonst schlagartig als verschwunden"
    assert zeile[1] == 0, "4K darf nie geraten werden"


def test_update_ergaenzt_die_speicher_belegung(alte_installation: Path) -> None:
    """Ein Update bringt die Posten-Tabelle mit - samt der Groessen in Plex.

    Zwei verschiedene Wege, und beide muessen sitzen: ``storage_entries`` ist
    eine **neue Tabelle** (``create_all``), die beiden Groessen an
    ``media_server_library`` sind **neue Spalten** an einer vorhandenen Tabelle
    (``_add_missing_columns``). Nur der zweite Weg kann stillschweigend
    scheitern, deshalb wird die Tabelle hier vorher von Hand angelegt - sonst
    prueft der Test ein CREATE TABLE statt eines ALTER TABLE.
    """
    engine = create_engine(f"sqlite:///{alte_installation}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE media_server_library (
                    id INTEGER PRIMARY KEY,
                    provider VARCHAR(20) NOT NULL,
                    media_type VARCHAR(5) NOT NULL,
                    guid VARCHAR(255) NOT NULL,
                    title VARCHAR(500) NOT NULL,
                    title_key VARCHAR(500) NOT NULL DEFAULT ''
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO media_server_library "
                "(provider, media_type, guid, title, title_key) "
                "VALUES ('plex', 'movie', 'plex://movie/1', 'Alt', 'alt')"
            )
        )
    engine.dispose()

    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        tabellen = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indizes = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        spalten = db_modul._existing_columns(connection, "media_server_library")
        groesse = connection.exec_driver_sql(
            "SELECT size_standard, size_uhd FROM media_server_library"
        ).one()

    assert "storage_entries" in tabellen
    assert {"size_standard", "size_uhd"} <= spalten

    # Der eindeutige Index muss mitkommen, sonst koennte derselbe Titel
    # doppelt verbucht werden - und niemand saehe es.
    assert "ix_storage_schluessel" in indizes

    # Bestandszeilen bekommen 0 = "unbekannt", nicht etwa NULL: Die Spalte ist
    # NOT NULL, und ohne brauchbaren Standardwert waere die Migration
    # gescheitert.
    assert groesse == (0, 0)


def test_update_ergaenzt_den_abgelaufen_zeitpunkt(alte_installation: Path) -> None:
    """Die Spalte fuer das abgelehnte Merklisten-Token kommt beim Update mit.

    Sie ist nullable und braucht keinen Standardwert - "noch nie abgelehnt"
    ist genau das, was NULL hier bedeutet.
    """
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        spalten = db_modul._existing_columns(connection, "users")

    assert "watchlist_token_invalid_at" in spalten


def test_update_ergaenzt_die_speicher_grenze(alte_installation: Path) -> None:
    """Die Grenze je Konto kommt beim Update mit - und bleibt leer.

    Leer heisst hier **"Vorgabe des Hauses"**, nicht "unbegrenzt" wie bei den
    Stueckzahl-Spalten daneben. Bestandskonten sollen die Hausvorgabe
    bekommen, sobald der Betreiber eine setzt - nicht stillschweigend
    unbegrenzt bleiben.
    """
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        spalten = db_modul._existing_columns(connection, "users")
        werte = connection.exec_driver_sql(
            "SELECT storage_limit_gb FROM users"
        ).fetchall()

    assert "storage_limit_gb" in spalten
    assert all(zeile[0] is None for zeile in werte)


def test_update_raeumt_meldungen_mit_verschwundener_art_weg(alte_installation: Path) -> None:
    """Eine Meldungsart, die es nicht mehr gibt, blockiert sonst die ganze Glocke.

    ``Notification.type`` ist eine strikte Aufzaehlung. Steht in der Datenbank
    ein Wert, den ``NotificationType`` nicht kennt, wirft SQLAlchemy beim
    Auspacken ``LookupError`` - und zwar fuer die **ganze Abfrage**, nicht nur
    fuer die eine Zeile. Das Konto sieht danach ueberhaupt keine
    Benachrichtigungen mehr, auch die gueltigen nicht. Der Zaehler daneben
    laeuft weiter, weil er ueber ``func.count`` geht und nie eine Zeile
    auspackt - der Fehler sieht deshalb aus wie ein spinnender Zaehler.

    Genau das ist in einer echten Datenbank passiert: vier Zeilen der Art
    ``watchlist_imported`` haben die Glocke eines Kontos blockiert und dabei
    eine echte, ungelesene Rueckmeldung mit verdeckt.

    Der Test prueft beide Haelften - die kaputte Zeile muss weg, und die
    gueltige daneben muss **bleiben**. Ein Aufraeumschritt, der einfach alles
    loescht, waere schliesslich auch "erfolgreich".

    Die gueltigen Zeilen entstehen ueber das Modell, die kaputte ueber rohes
    SQL: Einen unbekannten Wert kann das Modell gar nicht erzeugen - das ist ja
    der Sinn der Aufzaehlung. Nur so bleibt der Test von neuen Pflichtspalten
    unberuehrt.
    """
    db_modul.init_db()

    with Session(db_modul.engine) as sitzung:
        sitzung.add(User(username="opfer", password_hash="egal", role=Role.user))
        ziel = ChannelTarget(channel=ChannelKind.ntfy, name="Test")
        sitzung.add(ziel)
        sitzung.flush()
        sitzung.add(
            Notification(
                user_id=2, type=NotificationType.feedback, message_key="echt"
            )
        )
        sitzung.add(
            ChannelMessage(
                channel=ChannelKind.ntfy,
                target_id=ziel.id,
                type=NotificationType.approved,
            )
        )
        sitzung.commit()

    with db_modul.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE notifications SET type = 'watchlist_imported' WHERE type = 'feedback'"
        )
        connection.exec_driver_sql(
            "INSERT INTO notifications (user_id, type, message_key, is_read, created_at,"
            " mail_pending, mail_attempts)"
            " VALUES (2, 'feedback', 'echt', 0, '2026-01-03 12:00:00', 0, 0)"
        )
        connection.exec_driver_sql(
            "UPDATE channel_outbox SET type = 'watchlist_imported' WHERE type = 'approved'"
        )
        connection.exec_driver_sql(
            "INSERT INTO channel_outbox (channel, target_id, type, attempts, created_at)"
            " VALUES ('ntfy', 1, 'approved', 0, '2026-01-03 12:00:00')"
        )

    # Ein zweiter Start - genau das passiert nach einem Update.
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        meldungen = [
            zeile[0] for zeile in connection.exec_driver_sql("SELECT type FROM notifications")
        ]
        postausgang = [
            zeile[0] for zeile in connection.exec_driver_sql("SELECT type FROM channel_outbox")
        ]

    assert meldungen == ["feedback"], "die verschwundene Art muss weg, die gueltige bleiben"
    assert postausgang == ["approved"], "im Postausgang gilt dasselbe"


def test_gueltige_meldungen_ueberleben_jeden_start(alte_installation: Path) -> None:
    """Der Aufraeumschritt darf im Normalfall nichts anfassen.

    Er laeuft bei **jedem** Start. Wenn er dabei auch nur gelegentlich eine
    gueltige Zeile mitnaehme, waere er schlimmer als das Problem, das er loest.
    """
    db_modul.init_db()

    arten = [
        NotificationType.approved,
        NotificationType.rejected,
        NotificationType.download_complete,
        NotificationType.feedback,
    ]
    with Session(db_modul.engine) as sitzung:
        sitzung.add(User(username="opfer", password_hash="egal", role=Role.user))
        sitzung.flush()
        for art in arten:
            sitzung.add(Notification(user_id=2, type=art, message_key="k"))
        sitzung.commit()

    db_modul.init_db()
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        anzahl = connection.exec_driver_sql("SELECT COUNT(*) FROM notifications").scalar()

    assert anzahl == len(arten)


def test_update_traegt_den_anbieter_an_bestehenden_gesehen_markern_nach(
    alte_installation: Path,
) -> None:
    """Marker aus der Zeit vor der Spalte bekommen ihre Herkunft.

    Vor der Spalte gab es genau einen Medienserver, also ist die Antwort
    eindeutig: Alles, was dasteht, kam von dem, der verbunden ist.

    Ohne diesen Schritt waeren die Marker herrenlos - und beim ersten Lauf
    eines zweiten Anbieters wuesste der Abgleich nicht, ob "der andere Server
    sagt gesehen" gilt oder ob die Zeile einfach nur alt ist.
    """
    db_modul.init_db()

    with db_modul.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO settings (key, value, is_secret, updated_at)"
            " VALUES ('mediaserver_provider', 'plex', 0, '2026-01-01 12:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO user_watched (user_id, media_type, tmdb_id, providers)"
            " VALUES (1, 'movie', 4711, '')"
        )
        connection.exec_driver_sql(
            "INSERT INTO user_watched_seasons (user_id, tmdb_id, season, providers)"
            " VALUES (1, 4711, 1, '')"
        )

    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        filme = connection.exec_driver_sql("SELECT providers FROM user_watched").scalar()
        staffeln = connection.exec_driver_sql(
            "SELECT providers FROM user_watched_seasons"
        ).scalar()

    assert filme == "plex"
    assert staffeln == "plex"


def test_ohne_verbundenen_server_wird_nichts_geraten(alte_installation: Path) -> None:
    """Ist kein Server verbunden, bleibt die Herkunft leer.

    Dann gibt es niemanden, dem man die Marker zuschreiben koennte - und eine
    falsche Zuschreibung waere schlimmer als eine fehlende: Der Abgleich wuerde
    spaeter glauben, ein Server habe etwas gemeldet, was er nie gesagt hat.
    """
    db_modul.init_db()

    with db_modul.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO user_watched (user_id, media_type, tmdb_id, providers)"
            " VALUES (1, 'movie', 4711, '')"
        )

    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT providers FROM user_watched").scalar() == ""
        )


def test_update_holt_die_verbindung_in_ihre_tabelle(alte_installation: Path) -> None:
    """Die eine Verbindung aus den Einstellungen wird zur Zeile.

    Das ist der Schritt, der auf jedem bestehenden System klappen muss - dort
    steht die Verbindung seit jeher in fuenf flachen Werten.

    ⚠️ **Die Werte muessen vor dem Update dastehen, nicht danach.** Frueher
    schrieb dieser Test sie zwischen zwei ``init_db``-Laeufe, weil der alten
    Datenbank aus der Vorrichtung die Tabelle ``settings`` fehlt. Das war ein
    Zustand, den es im Betrieb nicht gibt - und seit das Wanderungsbuch die
    Wanderung nach ihrem ersten Lauf zuschliesst, wuerde er auch nichts mehr
    beweisen. Die Tabelle wird deshalb hier von Hand angelegt, im Stand von
    damals.
    """
    with db_modul.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE settings ("
            " key VARCHAR(100) NOT NULL PRIMARY KEY,"
            " value TEXT,"
            " is_secret BOOLEAN NOT NULL,"
            " updated_at DATETIME NOT NULL)"
        )
        for schluessel, wert in (
            ("mediaserver_provider", "plex"),
            ("mediaserver_machine_id", "maschine-1"),
            ("mediaserver_name", "Wohnzimmer"),
            ("mediaserver_url", "http://127.0.0.1:32400"),
            ("mediaserver_token", "enc:egal"),
        ):
            connection.exec_driver_sql(
                "INSERT INTO settings (key, value, is_secret, updated_at)"
                " VALUES (?, ?, 0, '2026-01-01 12:00:00')",
                (schluessel, wert),
            )

    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        zeilen = connection.exec_driver_sql(
            "SELECT provider, machine_id, name, url, token FROM media_server_connections"
        ).all()
        alt = dict(
            connection.exec_driver_sql(
                "SELECT key, value FROM settings WHERE key LIKE 'mediaserver_%'"
            ).all()
        )

    assert zeilen == [
        ("plex", "maschine-1", "Wohnzimmer", "http://127.0.0.1:32400", "enc:egal")
    ]
    # Das Token wandert **verschluesselt und ungeoeffnet** - dieser Schritt
    # braucht den Schluessel gar nicht, also kann ein Schluesselproblem ihn
    # auch nicht kaputtmachen.
    assert alt["mediaserver_token"] == "", "die alten Werte muessen leer sein"
    assert alt["mediaserver_provider"] == ""


def test_die_wanderung_laeuft_nur_einmal(alte_installation: Path) -> None:
    """Ein zweiter Start darf keine zweite Zeile anlegen.

    ``init_db`` laeuft bei **jedem** Start. Ein Schritt, der dabei jedes Mal
    zuschlaegt, waere schlimmer als gar keiner.
    """
    db_modul.init_db()
    with db_modul.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO media_server_connections"
            " (provider, machine_id, name, url, token, account_id, connected_at)"
            " VALUES ('plex', 'maschine-1', 'Da', '', '', '', '2026-01-01 12:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO settings (key, value, is_secret, updated_at)"
            " VALUES ('mediaserver_provider', 'plex', 0, '2026-01-01 12:00:00')"
        )

    db_modul.init_db()
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        anzahl = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM media_server_connections"
        ).scalar()

    assert anzahl == 1


def test_ohne_verbindung_entsteht_keine_zeile(alte_installation: Path) -> None:
    """Wer nie einen Server verbunden hatte, bekommt auch keine leere Zeile."""
    db_modul.init_db()
    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM media_server_connections"
            ).scalar()
            == 0
        )


def test_erster_start_legt_keine_sicherung_an(tmp_path: Path) -> None:
    """⚠️ Eine Sicherung einer Datenbank, die es noch gar nicht gibt.

    Beim allerersten Start meldet die Schema-Pruefung zwangslaeufig "alles
    fehlt". Nexview legte daraufhin gehorsam eine Kopie einer leeren Datenbank
    an - sie schuetzt nichts, verbraucht aber einen der fuenf Plaetze. Wer nach
    dem ersten Start in die Liste sieht, fragt sich zu Recht, wovor die
    schuetzen soll.
    """
    from sqlalchemy import create_engine

    frisch = create_engine(f"sqlite:///{tmp_path / 'neu.db'}", future=True)
    try:
        assert db_modul._leere_installation(frisch) is True

        # Sobald eine einzige Tabelle steht, ist es keine frische Installation
        # mehr - auch wenn noch kein Konto angelegt wurde.
        with frisch.connect() as verbindung:
            verbindung.exec_driver_sql("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
            verbindung.commit()
        assert db_modul._leere_installation(frisch) is False
    finally:
        frisch.dispose()


def test_update_ergaenzt_die_einladungsrechte(alte_installation: Path) -> None:
    """Die neuen Spalten an ``auth_tokens`` kommen per ALTER TABLE dazu.

    ⚠️ **Die Tabelle wird vorher von Hand angelegt.** Sonst entstuende sie per
    CREATE TABLE neu, und eine Pflichtspalte ohne Vorgabe fiele erst beim Update
    einer echten Installation auf - als Start, der abbricht. Die offene
    Einladung von vorher muss danach noch mit sinnvollen Werten dastehen.
    """
    engine = create_engine(f"sqlite:///{alte_installation}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE auth_tokens (
                    id INTEGER NOT NULL PRIMARY KEY,
                    purpose VARCHAR(18) NOT NULL,
                    token_hash VARCHAR(64) NOT NULL,
                    user_id INTEGER,
                    email VARCHAR(255) NOT NULL,
                    expires_at DATETIME NOT NULL,
                    used_at DATETIME,
                    created_at DATETIME NOT NULL,
                    created_by INTEGER,
                    invite_role VARCHAR(8),
                    invite_quota_movies INTEGER,
                    invite_quota_series INTEGER,
                    invite_quota_period VARCHAR(5),
                    invite_blocked_movie_profiles VARCHAR(255) NOT NULL DEFAULT '',
                    invite_blocked_series_profiles VARCHAR(255) NOT NULL DEFAULT '',
                    mediaserver_ref TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO auth_tokens "
                "(purpose, token_hash, email, expires_at, created_at, invite_role) "
                "VALUES ('invitation', 'pruefsumme', 'alt@example.com', "
                "'2099-01-01 00:00:00', '2026-09-01 12:00:00', 'user')"
            )
        )
    engine.dispose()

    db_modul.init_db()

    with db_modul.engine.connect() as connection:
        spalten = db_modul._existing_columns(connection, "auth_tokens")
        zeile = connection.execute(
            text(
                "SELECT invite_auto_approve_movies, invite_hausordnung, invite_dropped, "
                "invite_storage_limit_gb, redeemed_by FROM auth_tokens "
                "WHERE email = 'alt@example.com'"
            )
        ).one()

    assert {
        "invite_auto_approve_movies",
        "invite_auto_approve_series",
        "invite_fassung_rechte",
        "invite_hausordnung",
        "invite_storage_limit_gb",
        "invite_dropped",
        "redeemed_by",
    } <= spalten
    assert tuple(zeile) == (0, 0, "", None, None)
