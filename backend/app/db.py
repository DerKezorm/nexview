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
from .models import Base

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
        logger.info("Datenbank wird angepasst: %s", ", ".join(ausstehend))
        _backup_database()

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _add_missing_indexes()


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

        logger.info("Sicherung der Datenbank angelegt: %s", ziel.name)
        _prune_backups(ordner)
    except Exception as fehler:  # noqa: BLE001 - Start darf daran nicht scheitern
        logger.warning("Sicherung der Datenbank fehlgeschlagen: %s", fehler)


def _prune_backups(ordner: Path, behalten: int = BACKUPS_TO_KEEP) -> None:
    """Nur die juengsten Sicherungen behalten - sonst laeuft die Platte voll."""
    dateien = sorted(ordner.glob("nexview-vor-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for alt in dateien[behalten:]:
        try:
            alt.unlink()
        except OSError as fehler:
            logger.warning("Alte Sicherung %s liess sich nicht loeschen: %s", alt.name, fehler)


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

                logger.info("Datenbank wird ergänzt: %s.%s", table.name, column.name)
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
                logger.info("Datenbank wird ergänzt: Index %s", index.name)
                index.create(bind=connection)


def get_db() -> Iterator[Session]:
    """FastAPI-Dependency: eine Datenbank-Session pro Anfrage."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
