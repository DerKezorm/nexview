"""SQLite-Verbindung und Session-Verwaltung."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from enum import Enum
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from . import __version__
from .config import get_settings
from .models import Base, NotificationType, utcnow

logger = logging.getLogger("nexview.db")

# Wie viele Sicherungen aufgehoben werden. Sie entstehen nur bei einer
# tatsaechlichen Aenderung an der Datenbank, also praktisch nur nach Updates.
BACKUPS_TO_KEEP = 5

_settings = get_settings()

engine = create_engine(
    f"sqlite:///{_settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """WAL fuer parallele Lese-/Schreibzugriffe, Fremdschluessel aktivieren."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Datenbank auf den Stand der laufenden Version bringen.

    Wird bei jedem Start aufgerufen. Nach einem Update fehlen der bestehenden
    Datenbank die Tabellen, Spalten und Indizes, die inzwischen dazugekommen
    sind - die werden hier ergaenzt. Vorher wird gesichert, damit ein
    fehlgeschlagener Schritt nie Daten kostet.
    """
    ausstehend = _pending_changes()
    if ausstehend:
        logger.info("Database schema update: %s", ", ".join(ausstehend))
        _backup_database()

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _add_missing_indexes()
    _altersgrenzen_aufraeumen()
    _verwaiste_meldungsarten_aufraeumen()
    # Muss **vor** dem Nachtragen der Herkunft laufen - das liest den
    # Anbieternamen bevorzugt aus dieser Tabelle.
    _verbindung_in_die_tabelle()
    _bewertungen_in_die_tabelle()
    _gesehen_herkunft_nachtragen()
    _verknuepfungen_in_die_tabelle()
    _kontingente_dreiwertig_machen()


def _kontingente_dreiwertig_machen() -> None:
    """Die Speichergrenze auf die neue Schreibweise bringen - **einmalig**.

    ⚠️ **Dieselbe Zahl hat ihre Bedeutung gewechselt.** Bis 0.19 hiess eine
    ``0`` bei ``storage_limit_gb`` "fuer dieses Konto unbegrenzt". Seit die
    Kontingente dreiwertig sind, heisst sie "darf nichts anfragen" - und
    "unbegrenzt" ist die ``-1``. Ohne diesen Schritt waere jedes so gesetzte
    Konto ueber Nacht **still gesperrt** worden, ohne dass jemand etwas
    geaendert haette.

    ``NULL`` bleibt ``NULL``: Es hiess vorher "es gilt die Standardgrenze" und
    heisst es weiter.

    Ausserdem zieht der Kontingent-Zeitraum von den Konten in die
    Einstellungen um. Er gilt jetzt haus-weit; gab es unter den Konten genau
    einen abweichenden Wert, wird er uebernommen, damit sich fuer eine
    bestehende Installation nichts aendert. Waren mehrere verschiedene im
    Umlauf, gewinnt der haeufigste - eine Entscheidung muss fallen, und die
    Mehrheit trifft am wenigsten Leute.

    Laeuft bei jedem Start und trifft nach dem ersten Mal nichts mehr.
    """
    with engine.begin() as connection:
        ergebnis = connection.exec_driver_sql(
            "UPDATE users SET storage_limit_gb = -1 WHERE storage_limit_gb = 0"
        )
        if ergebnis.rowcount:
            logger.info(
                "Storage limit of %d account(s) migrated: 0 meant 'unlimited' and now "
                "means 'nothing allowed' - they were set to -1 (unlimited)",
                ergebnis.rowcount,
            )

        # ⚠️ **Der Umstellungs-Pardon.** War der alte Hauptschalter *aus*,
        # galten die gespeicherten GB-Grenzen bis eben gar nicht - weder die
        # des Hauses noch die der Konten. Ab jetzt gelten sie. Ohne diesen
        # Schritt waeren Leute nach dem Update schlagartig gesperrt, wegen
        # einer Zahl, die seit Monaten wirkungslos herumlag, und wegen einer
        # Zurechnung, von der sie nicht wussten, dass sie einmal zaehlen wuerde.
        #
        # Also dieselbe Regel wie beim frueheren Umschalten: alles ins Haus,
        # jedes Konto startet bei null. Keine Datei wird angefasst, gespeicherte
        # Grenzen bleiben stehen - sie greifen nur ab jetzt statt rueckwirkend.
        war_aus = connection.exec_driver_sql(
            "SELECT value FROM settings WHERE key = 'storage_enabled'"
        ).scalar()
        if war_aus is not None and (war_aus or "").strip().lower() not in {
            "on",
            "true",
            "1",
            "yes",
            "ja",
        }:
            ergebnis = connection.exec_driver_sql(
                "UPDATE storage_entries SET user_id = NULL, state = 'house', "
                "released_at = NULL, release_wish = NULL WHERE user_id IS NOT NULL"
            )
            if ergebnis.rowcount:
                logger.warning(
                    "Quotas merged: storage limits were inert until now, so %d item(s) "
                    "were moved to the household - every account starts at zero",
                    ergebnis.rowcount,
                )

        # Der alte Hauptschalter wird nicht mehr gelesen. Die Zeile stehen zu
        # lassen waere eine Einstellung, die es nicht mehr gibt - und beim
        # naechsten Blick in die Datenbank eine falsche Faehrte.
        connection.exec_driver_sql("DELETE FROM settings WHERE key = 'storage_enabled'")

        # Der Zeitraum nur, solange niemand ihn haus-weit gesetzt hat: Ein
        # zweiter Lauf duerfte eine bewusste Wahl nicht ueberschreiben.
        schon_da = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM settings WHERE key = 'quota_period'"
        ).scalar()
        if schon_da:
            return
        zeile = connection.exec_driver_sql(
            """
            SELECT quota_period, COUNT(*) AS anzahl
            FROM users
            WHERE quota_period IS NOT NULL AND quota_period != 'week'
            GROUP BY quota_period
            ORDER BY anzahl DESC
            LIMIT 1
            """
        ).first()
        if zeile is None:
            return
        # ⚠️ ``is_secret`` und ``updated_at`` sind Pflichtspalten - beim
        # rohen SQL springt kein Standardwert aus dem Modell ein. Ohne sie
        # scheitert das INSERT beim **Start**, also bei jeder Installation,
        # die gerade aktualisiert.
        connection.exec_driver_sql(
            "INSERT INTO settings (key, value, is_secret, updated_at) "
            "VALUES ('quota_period', ?, 0, ?)",
            (zeile[0], str(utcnow().replace(tzinfo=None))),
        )
        logger.info(
            "Quota period is now house-wide; adopted %r from %d account(s)",
            zeile[0],
            zeile[1],
        )


def _verknuepfungen_in_die_tabelle() -> None:
    """Die eine Medienserver-Identitaet je Benutzer in ihre Tabelle.

    Dasselbe Muster wie ``_verbindung_in_die_tabelle``, eine Ebene tiefer: Dort
    wanderte der *Server* aus flachen Werten in eine Tabelle, hier die
    *Identitaeten der Menschen*. Der Grund ist derselbe - bis 0.18.0 konnte es
    nur eine geben, im Parallelbetrieb sind es zwei.

    Der Schritt laeuft **genau einmal**: Steht schon eine Zeile da, passiert
    nichts.

    ⚠️ **Anders als beim Server bleiben die alten Spalten stehen und behalten
    ihren Wert.** Beim Server wurden sie geleert, damit es nicht zwei
    Wahrheiten gibt. Hier waere genau das falsch: Die Spalten am Benutzer
    fuehren weiterhin die *zuletzt* verknuepfte Identitaet, und ein gutes
    Dutzend Stellen liest sie - Profil, Anmeldung, Wiedererkennen. Sie zu
    leeren hiesse, jeden Benutzer bei laufendem Betrieb als "nicht verbunden"
    dastehen zu lassen. Gepflegt werden sie ab jetzt von
    ``mediaserver_accounts.link``.

    Das Token wandert verschluesselt und ungeoeffnet - wie beim Server auch.
    """
    with engine.begin() as connection:
        schon_da = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM user_media_server_accounts"
        ).scalar()
        if schon_da:
            return

        zeilen = connection.exec_driver_sql(
            "SELECT id, mediaserver_provider, mediaserver_account_id, "
            "mediaserver_username, mediaserver_email, mediaserver_thumb, "
            "mediaserver_linked_at, watchlist_token, watchlist_connected_at, "
            "watchlist_token_invalid_at FROM users "
            "WHERE mediaserver_provider IS NOT NULL "
            "AND mediaserver_provider != '' "
            "AND mediaserver_account_id IS NOT NULL "
            "AND mediaserver_account_id != ''"
        ).fetchall()
        if not zeilen:
            return

        jetzt = str(utcnow().replace(microsecond=0))
        for zeile in zeilen:
            connection.exec_driver_sql(
                "INSERT INTO user_media_server_accounts "
                "(user_id, provider, account_id, username, email, thumb, "
                "linked_at, token, token_connected_at, token_invalid_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    zeile[0],
                    zeile[1],
                    zeile[2],
                    zeile[3],
                    zeile[4],
                    zeile[5],
                    zeile[6] or jetzt,
                    zeile[7],
                    zeile[8],
                    zeile[9],
                ),
            )
        logger.info(
            "Personal media server links moved into their own table: %d account(s)",
            len(zeilen),
        )


def _existing_columns(connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in connection.exec_driver_sql(f"PRAGMA table_info('{table_name}')").fetchall()
    }


def _existing_indexes(connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in connection.exec_driver_sql(f"PRAGMA index_list('{table_name}')").fetchall()
    }


def _pending_changes() -> list[str]:
    """Was muesste an der bestehenden Datenbank geaendert werden?

    Dient nur als Ausloeser fuer die Sicherung. Die Pruefung nutzt bewusst
    dieselben PRAGMA-Abfragen wie die Aenderungsschritte weiter unten: waeren
    es zwei unterschiedliche Verfahren, koennte diese Funktion dauerhaft eine
    Aenderung melden, die dort schon als erledigt gilt - und Nexview wuerde bei
    jedem Start eine neue Sicherung anlegen.
    """
    if not _settings.db_path.exists():
        return []  # frische Installation, es gibt nichts zu sichern

    offen: list[str] = []
    with engine.connect() as connection:
        tabellen = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        for table in Base.metadata.sorted_tables:
            if table.name not in tabellen:
                offen.append(f"Tabelle {table.name}")
                continue

            spalten = _existing_columns(connection, table.name)
            offen.extend(
                f"Spalte {table.name}.{column.name}"
                for column in table.columns
                if column.name not in spalten
            )

            indizes = _existing_indexes(connection, table.name)
            offen.extend(
                f"Index {index.name}" for index in table.indexes if index.name not in indizes
            )

    return offen


def _backup_database() -> None:
    """Kopie der Datenbank anlegen, bevor sie veraendert wird.

    ``VACUUM INTO`` erzeugt eine in sich stimmige Kopie, auch waehrend
    Schreibvorgaenge laufen - ein blosses Kopieren der Datei wuerde im
    WAL-Betrieb die zuletzt gespeicherten Aenderungen verlieren.

    Scheitert die Sicherung, laeuft der Start trotzdem weiter: ein Container,
    der wegen einer nicht schreibbaren Sicherung gar nicht erst hochkommt,
    waere schlimmer als eine fehlende Sicherung.
    """
    ordner = _settings.data_dir / "sicherungen"
    try:
        ordner.mkdir(parents=True, exist_ok=True)
        stempel = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        ziel = ordner / f"nexview-vor-{__version__}-{stempel}.db"
        # VACUUM INTO weigert sich, eine vorhandene Datei zu ueberschreiben.
        # Zwei Anpassungen in derselben Sekunde sind selten, aber moeglich.
        zaehler = 2
        while ziel.exists():
            ziel = ordner / f"nexview-vor-{__version__}-{stempel}-{zaehler}.db"
            zaehler += 1

        with engine.connect() as connection:
            # Der Pfad kommt aus der eigenen Konfiguration, nicht von aussen.
            # Einfache Anfuehrungszeichen darin wuerden das Statement dennoch
            # zerlegen - deshalb werden sie verdoppelt.
            connection.exec_driver_sql(f"VACUUM INTO '{str(ziel).replace(chr(39), chr(39) * 2)}'")

        logger.info("Database backup created: %s", ziel.name)
        _prune_backups(ordner)
    except Exception as fehler:  # noqa: BLE001 - Start darf daran nicht scheitern
        logger.warning("Database backup failed: %s", fehler)


def _prune_backups(ordner: Path, behalten: int = BACKUPS_TO_KEEP) -> None:
    """Nur die juengsten Sicherungen behalten - sonst laeuft die Platte voll."""
    dateien = sorted(ordner.glob("nexview-vor-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for alt in dateien[behalten:]:
        try:
            alt.unlink()
        except OSError as fehler:
            logger.warning("Could not delete old backup %s: %s", alt.name, fehler)


def _sql_literal(wert: object) -> str | None:
    """Standardwert einer Spalte als SQL-Literal - oder ``None``, wenn er sich
    nicht ausdruecken laesst.

    Die Reihenfolge der Pruefungen ist hier entscheidend: Auswahllisten (Rolle,
    Status, Kontingent-Zeitraum) erben in Nexview von ``str``, und ``bool``
    erbt von ``int``. Wer zuerst auf ``str`` bzw. ``int`` prueft, schreibt
    ``DEFAULT <QuotaPeriod.week: 'week'>`` in das ALTER TABLE - und die
    Datenbank eines aktualisierten Nexview laesst sich nicht mehr oeffnen.
    """
    if isinstance(wert, Enum):
        wert = wert.value
    if wert is None:
        return "NULL"
    if isinstance(wert, bool):
        return "1" if wert else "0"
    if isinstance(wert, (int, float)):
        return str(wert)
    if isinstance(wert, datetime):
        # Genau das Format, in dem SQLAlchemy Zeitstempel in SQLite ablegt.
        return "'" + str(wert) + "'"
    if isinstance(wert, str):
        return "'" + wert.replace("'", "''") + "'"
    return None


# Unterscheidet "kein Standardwert vorhanden" von "Standardwert ist NULL".
# Ohne diese Trennung wuerde eine Pflichtspalte ohne Standardwert stillschweigend
# ein ``DEFAULT NULL`` bekommen statt den beabsichtigten deutlichen Fehler.
_KEIN_WERT = object()


def _default_value(column) -> object:
    """Standardwert einer Spalte ermitteln - auch wenn er berechnet wird.

    Zeitstempel wie ``created_at`` haben eine Funktion als Standardwert. In ein
    ALTER TABLE laesst sich keine Funktion schreiben, also wird sie hier einmal
    ausgefuehrt: die bereits vorhandenen Zeilen bekommen damit den Zeitpunkt des
    Updates. Neue Zeilen holen ihren Wert weiterhin wie gewohnt aus dem Modell.
    """
    default = column.default
    if default is None:
        return _KEIN_WERT
    if default.is_scalar:
        return default.arg
    if default.is_callable:
        try:
            # SQLAlchemy verpackt parameterlose Funktionen so, dass sie einen
            # Kontext entgegennehmen; andere Fassungen kommen ohne aus.
            return default.arg(None)
        except TypeError:
            try:
                return default.arg()
            except Exception:  # noqa: BLE001
                return _KEIN_WERT
        except Exception:  # noqa: BLE001
            return _KEIN_WERT
    return _KEIN_WERT


def _add_missing_columns() -> None:
    """Spalten nachruesten, die in neueren Nexview-Versionen dazugekommen sind.

    ``create_all`` legt nur fehlende *Tabellen* an. Ohne diesen Schritt wuerde
    eine bestehende Installation nach einem Update auf fehlende Spalten laufen.
    SQLite kann Spalten problemlos anhaengen, solange sie einen Standardwert
    haben oder NULL erlauben.
    """
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            vorhanden = _existing_columns(connection, table.name)
            if not vorhanden:
                continue  # Tabelle gibt es (noch) nicht

            for column in table.columns:
                if column.name in vorhanden:
                    continue

                typ = column.type.compile(dialect=engine.dialect)
                klausel = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {typ}'

                wert = _default_value(column)
                literal = None if wert is _KEIN_WERT else _sql_literal(wert)

                if literal is not None:
                    klausel += f" DEFAULT {literal}"
                elif not column.nullable:
                    # Ohne Standardwert kann eine Pflichtspalte nicht ergaenzt
                    # werden - dann lieber sichtbar scheitern als still.
                    raise RuntimeError(
                        f"Spalte {table.name}.{column.name} fehlt und hat keinen Standardwert."
                    )

                logger.info("Database extended: %s.%s", table.name, column.name)
                connection.exec_driver_sql(klausel)


def _add_missing_indexes() -> None:
    """Indizes nachruesten, die zu neu ergaenzten Spalten gehoeren.

    ``create_all`` legt Indizes nur zusammen mit einer *neuen* Tabelle an. Bei
    einer bestehenden Installation bliebe die Eindeutigkeit der E-Mail-Adresse
    sonst unerzwungen - und Doppelkonten faenden ihren Weg hinein.
    """
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            vorhanden = _existing_indexes(connection, table.name)
            for index in table.indexes:
                if index.name in vorhanden:
                    continue
                logger.info("Database extended: index %s", index.name)
                index.create(bind=connection)


def get_db() -> Iterator[Session]:
    """FastAPI-Dependency: eine Datenbank-Session pro Anfrage."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _altersgrenzen_aufraeumen() -> None:
    """Altersgrenzen an vollwertigen Konten entfernen.

    Frueher konnte der Administrator jedem Konto ein Alter geben. Seit es
    Kinderkonten gibt, ist das der falsche Weg: Wer ein vollwertiges Konto hat,
    gilt als volljaehrig, und Kinder werden von ihren Eltern gepflegt. Die
    Einstellung ist deshalb aus der Benutzerverwaltung verschwunden - und ohne
    diesen Schritt bliebe ein frueher beschraenktes Konto fuer immer
    beschraenkt, weil es keine Stelle mehr gaebe, an der man es aufhebt.

    Bei Kinderkonten bleibt das Alter selbstverstaendlich stehen; dort ist es
    genau das Feld, an dem die Sperre haengt.

    Laeuft bei jedem Start und trifft nach dem ersten Mal nichts mehr.
    """
    with engine.begin() as connection:
        ergebnis = connection.exec_driver_sql(
            "UPDATE users SET age = NULL WHERE age IS NOT NULL AND role != 'child'"
        )
        if ergebnis.rowcount:
            logger.info(
                "Age limit removed from %d adult account(s), it now applies to children only",
                ergebnis.rowcount,
            )


def _bewertungen_in_die_tabelle() -> None:
    """Bewertungen von den Anfragen an die Titel umhaengen.

    Bis 0.19 hing eine Bewertung an ``MediaRequest``. Damit durfte nur der
    Besteller urteilen - wer denselben Film spaeter sah und merkte, dass die
    Tonspur fehlt, hatte keine Moeglichkeit, es zu sagen. Seit 0.19 haengt sie
    am Titel, und die bestehenden muessen mit.

    ⚠️ **Laeuft genau einmal**, erkennbar an der leeren Zieltabelle - wie bei
    den Medienserver-Verbindungen. Ein zweiter Lauf wuerde vorhandene
    Bewertungen verdoppeln oder am Eindeutigkeitsschluessel scheitern.

    Die Spalten an der Anfrage bleiben vorerst stehen und werden nicht mehr
    gelesen. Sie zu loeschen hiesse, eine Tabelle in SQLite neu zu bauen - und
    solange sie niemanden stoeren, ist der stille Rueckweg mehr wert als die
    aufgeraeumte Spalte.
    """
    with engine.begin() as connection:
        schon_da = connection.exec_driver_sql("SELECT COUNT(*) FROM title_ratings").scalar()
        if schon_da:
            return

        # Dieselbe Person kann denselben Titel zweimal angefragt haben (etwa in
        # 1080p und 4K) und beide Male bewertet. Am Titel gibt es aber nur ein
        # Urteil - genommen wird das juengste.
        connection.exec_driver_sql(
            """
            INSERT INTO title_ratings (
                user_id, media_type, tmdb_id, season, rating, comment, title,
                reply, replied_at, file_size_bytes, outdated, created_at, updated_at
            )
            SELECT
                r.user_id, r.media_type, r.tmdb_id, r.season, r.rating, r.feedback,
                r.title, r.feedback_reply, r.replied_at,
                COALESCE(r.file_size_bytes, 0), COALESCE(r.rating_outdated, 0),
                COALESCE(r.rated_at, r.requested_at), COALESCE(r.rated_at, r.requested_at)
            FROM media_requests r
            WHERE r.rating IS NOT NULL
              AND r.id = (
                  SELECT r2.id FROM media_requests r2
                  WHERE r2.user_id = r.user_id
                    AND r2.media_type = r.media_type
                    AND r2.tmdb_id = r.tmdb_id
                    AND (r2.season IS r.season)
                    AND r2.rating IS NOT NULL
                  ORDER BY r2.rated_at DESC, r2.id DESC
                  LIMIT 1
              )
            """
        )


def _verbindung_in_die_tabelle() -> None:
    """Die eine Medienserver-Verbindung aus den Einstellungen in ihre Tabelle.

    Bis zum Parallelbetrieb lag sie in fuenf flachen Werten - Anbieter,
    Kennung, Name, Adresse, Token. Das ging, solange es nur eine geben konnte.

    Der Schritt laeuft **genau einmal**: Steht schon eine Zeile in der Tabelle,
    passiert nichts. Danach werden die alten Werte geleert, damit es nicht zwei
    Wahrheiten gibt - die zweite waere sonst genau die Art Altlast, die einem
    ein halbes Jahr spaeter als "warum steht da noch Plex" begegnet.

    ⚠️ **Das Token wandert verschluesselt und ungeoeffnet.** Es steht in beiden
    Tabellen als derselbe Text; entschluesselt wird es erst beim Benutzen. So
    braucht dieser Schritt weder den Schluessel noch den Einstellungsdienst -
    und ein Schluesselwechsel macht die Wanderung nicht kaputt, sondern
    hoechstens das Token, und das war vorher genauso.

    Die Client-Kennung bleibt, wo sie ist: Sie gehoert zur Installation, nicht
    zum Server.
    """
    with engine.begin() as connection:
        schon_da = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM media_server_connections"
        ).scalar()
        if schon_da:
            return

        werte = dict(
            connection.exec_driver_sql(
                "SELECT key, value FROM settings WHERE key IN "
                "('mediaserver_provider', 'mediaserver_machine_id', "
                "'mediaserver_name', 'mediaserver_url', 'mediaserver_token')"
            ).all()
        )
        anbieter = (werte.get("mediaserver_provider") or "").strip()
        kennung = (werte.get("mediaserver_machine_id") or "").strip()
        if not anbieter or not kennung:
            # Nie verbunden gewesen, oder eine halbe Verbindung ohne Kennung -
            # daraus laesst sich keine Zeile machen, die den Zugriff pruefen
            # koennte. Dann lieber nichts.
            return

        # ⚠️ **Jede Spalte der Tabelle gehoert in diese Aufzaehlung.**
        #
        # Rohes SQL nennt die Spalten selbst, und Standardwerte aus dem Modell
        # gelten hier nicht - die kennt nur SQLAlchemy, nicht SQLite. Eine
        # vergessene Pflichtspalte laesst diesen Schritt scheitern, und mit ihm
        # den ganzen Start. Genau so passiert, als ``account_id`` dazukam.
        connection.exec_driver_sql(
            "INSERT INTO media_server_connections "
            "(provider, machine_id, name, url, token, account_id, connected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                anbieter,
                kennung,
                werte.get("mediaserver_name") or "",
                werte.get("mediaserver_url") or "",
                werte.get("mediaserver_token") or "",
                # Leer: Eine Verbindung aus der Zeit davor kennt die
                # Kontonummer nicht. Der Adapter fragt dann wie bisher beim
                # Server nach - bei Plex und Jellyfin geht das.
                "",
                str(utcnow().replace(microsecond=0)),
            ),
        )
        connection.exec_driver_sql(
            "UPDATE settings SET value = '' WHERE key IN "
            "('mediaserver_provider', 'mediaserver_machine_id', "
            "'mediaserver_name', 'mediaserver_url', 'mediaserver_token')"
        )
        logger.info(
            "Media server connection moved into its own table: %r on %r",
            anbieter,
            werte.get("mediaserver_name") or kennung,
        )


# Tabellen mit einer Spalte ``providers`` - siehe ``UserWatched.providers``.
GESEHEN_TABELLEN = ("user_watched", "user_watched_seasons")


def _gesehen_herkunft_nachtragen() -> None:
    """Bestehenden Gesehen-Markern den Anbieter eintragen, von dem sie stammen.

    Vor dieser Spalte gab es genau einen Medienserver, also ist die Antwort
    eindeutig: Alles, was dasteht, kam von dem, der gerade verbunden ist.

    ⚠️ **Ohne diesen Schritt waeren die Marker herrenlos** - und der Abgleich
    kann eine herrenlose Zeile nicht zuordnen. Beim ersten Lauf eines zweiten
    Anbieters wuesste er nicht, ob "der andere Server sagt gesehen" gilt oder
    ob die Zeile einfach nur alt ist.

    Bewusst ueber rohes SQL statt ueber die Dienste: Diese Datei laeuft ganz am
    Anfang des Starts, und der Anbietername ist kein Geheimnis. Sie einzubinden
    brauchte ein Sitzungsobjekt und haette hier nichts zu suchen.

    Gelesen wird aus **beiden** Quellen: aus ``media_server_connections``,
    sobald es sie gibt, sonst aus den Einstellungen. Solange die Wanderung dahin
    noch nicht angeschaltet ist, steht der Anbieter naemlich weiterhin dort -
    und dieser Schritt soll von jener Umstellung unabhaengig richtig sein.

    Sind **mehrere** Server verbunden, passiert nichts: Dann liesse sich nicht
    mehr sagen, von welchem ein alter Marker stammt, und Raten waere schlimmer
    als eine leere Angabe.

    Ist gar kein Server verbunden, passiert ebenfalls nichts.

    Laeuft bei jedem Start und trifft nach dem ersten Mal nichts mehr.
    """
    with engine.begin() as connection:
        anbieter_liste = [
            zeile[0]
            for zeile in connection.exec_driver_sql(
                "SELECT provider FROM media_server_connections"
            )
            if zeile[0] and str(zeile[0]).strip()
        ]
        if not anbieter_liste:
            aus_einstellungen = connection.exec_driver_sql(
                "SELECT value FROM settings WHERE key = 'mediaserver_provider'"
            ).scalar()
            if aus_einstellungen and str(aus_einstellungen).strip():
                anbieter_liste = [str(aus_einstellungen).strip()]
        if len(anbieter_liste) != 1:
            return
        anbieter = anbieter_liste[0]

        for tabelle in GESEHEN_TABELLEN:
            ergebnis = connection.exec_driver_sql(
                f"UPDATE {tabelle} SET providers = ? "  # noqa: S608 - feste Namen
                "WHERE providers IS NULL OR providers = ''",
                (str(anbieter).strip(),),
            )
            if ergebnis.rowcount:
                logger.info(
                    "Watched: %d marker(s) in %s attributed to the connected server %r",
                    ergebnis.rowcount,
                    tabelle,
                    str(anbieter).strip(),
                )


# Tabellen, deren Spalte ``type`` eine ``NotificationType`` fuehrt.
MELDUNGSTABELLEN = ("notifications", "channel_outbox")


def _verwaiste_meldungsarten_aufraeumen() -> None:
    """Meldungen wegraeumen, deren Art es nicht mehr gibt.

    ⚠️ **Eine einzige unbekannte Zeile legt die ganze Glocke eines Kontos
    lahm.** ``Notification.type`` ist eine strikte Aufzaehlung; findet
    SQLAlchemy beim Auspacken einen Wert, den ``NotificationType`` nicht kennt,
    wirft es ``LookupError`` - und zwar nicht fuer die eine Zeile, sondern fuer
    die ganze Abfrage. Der Endpunkt antwortet mit 500, die Oberflaeche zeigt
    "Nichts Neues", und **der Zaehler daneben laeuft weiter**, weil er ueber
    ``func.count`` geht und nie eine Zeile auspackt. Genau diese Kombination
    macht den Fehler so tueckisch: Er sieht nicht aus wie ein Fehler, sondern
    wie ein Zaehler, der spinnt.

    Gemessen in einer echten Datenbank: vier Zeilen der Art
    ``watchlist_imported`` - ein Name, den es im Code laengst nicht mehr gibt -
    haben die Glocke eines Kontos dauerhaft blockiert und dabei eine **echte,
    ungelesene** Rueckmeldung mit verdeckt.

    Solche Zeilen entstehen, sobald eine Meldungsart umbenannt oder entfernt
    wird: ``init_db`` ergaenzt Tabellen, Spalten und Indizes, aber es fasst
    **niemals Zeileninhalte** an. Die alten Werte bleiben also liegen.

    Wer hier eine Art umbenennt, muss deshalb wissen: Ohne diesen Schritt ist
    das kein kosmetischer Eingriff, sondern eine kaputte Glocke bei jedem
    Konto, das eine solche Meldung hatte.

    Geloescht statt umgeschrieben, weil sich eine verschwundene Art nicht
    sinnvoll auf eine andere abbilden laesst - der Text stuende dann unter
    einer Ueberschrift, die nicht dazu gehoert. Auf **WARNING**, nicht INFO:
    Hier gehen Daten verloren, und das soll jemand sehen.

    Laeuft bei jedem Start und trifft nach dem ersten Mal nichts mehr.
    """
    bekannt = sorted(art.value for art in NotificationType)
    frage = ", ".join("?" for _ in bekannt)

    with engine.begin() as connection:
        for tabelle in MELDUNGSTABELLEN:
            # Erst nachsehen, was da ist - die Namen sind der eigentliche
            # Hinweis fuer den Betreiber. Eine reine Anzahl sagt ihm nicht,
            # welche Umbenennung ihm das eingebrockt hat.
            fremd = [
                zeile[0]
                for zeile in connection.exec_driver_sql(
                    f"SELECT DISTINCT type FROM {tabelle} "  # noqa: S608 - feste Namen
                    f"WHERE type NOT IN ({frage})",
                    tuple(bekannt),
                )
            ]
            if not fremd:
                continue

            ergebnis = connection.exec_driver_sql(
                f"DELETE FROM {tabelle} WHERE type NOT IN ({frage})",  # noqa: S608
                tuple(bekannt),
            )
            logger.warning(
                "Removed %d row(s) from %s with unknown notification type(s): %s - "
                "these were unreadable and blocked the whole list for the affected users",
                ergebnis.rowcount,
                tabelle,
                ", ".join(sorted(fremd)),
            )
