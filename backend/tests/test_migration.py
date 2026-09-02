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


def test_update_ordnet_bestandsanfragen_der_standard_stufe_zu(alte_installation: Path) -> None:
    """Was vor der 4K-Instanz angefragt wurde, gehoert zur Standard-Stufe.

    Bliebe die Spalte leer, wuerde der Poller diese Anfragen gegen die falsche
    Bibliothek pruefen - und eine 1080p-Datei koennte eine 4K-Anfrage
    abschliessen. Deshalb ist der Standardwert hier keine Kosmetik.
    """
    db_modul.init_db()

    engine = create_engine(f"sqlite:///{alte_installation}")
    with engine.begin() as connection:
        stufen = [
            zeile[0] for zeile in connection.execute(text("SELECT tier FROM media_requests"))
        ]
        spalten = {
            zeile[1] for zeile in connection.execute(text("PRAGMA table_info(users)"))
        }
    engine.dispose()

    assert stufen == ["standard"]
    assert {
        "can_request_uhd_movies",
        "can_request_uhd_series",
        "auto_approve_uhd",
        "blocked_movie_uhd_profiles",
        "blocked_series_uhd_profiles",
    } <= spalten


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
