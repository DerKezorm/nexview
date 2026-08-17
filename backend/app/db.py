"""SQLite-Verbindung und Session-Verwaltung."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

logger = logging.getLogger("nexview.db")

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
    """Tabellen anlegen und fehlende Spalten ergaenzen."""
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _add_missing_indexes()


def _add_missing_columns() -> None:
    """Spalten nachruesten, die in neueren Nexview-Versionen dazugekommen sind.

    ``create_all`` legt nur fehlende *Tabellen* an. Ohne diesen Schritt wuerde
    eine bestehende Installation nach einem Update auf fehlende Spalten laufen.
    SQLite kann Spalten problemlos anhaengen, solange sie einen Standardwert
    haben oder NULL erlauben.
    """
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            vorhanden = {
                row[1]
                for row in connection.exec_driver_sql(
                    f"PRAGMA table_info('{table.name}')"
                ).fetchall()
            }
            if not vorhanden:
                continue  # Tabelle gibt es (noch) nicht

            for column in table.columns:
                if column.name in vorhanden:
                    continue

                typ = column.type.compile(dialect=engine.dialect)
                klausel = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {typ}'
                if column.default is not None and column.default.is_scalar:
                    wert = column.default.arg
                    klausel += f" DEFAULT {wert!r}" if isinstance(wert, str) else f" DEFAULT {wert}"
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
            vorhanden = {
                row[1]
                for row in connection.exec_driver_sql(
                    f"PRAGMA index_list('{table.name}')"
                ).fetchall()
            }
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
