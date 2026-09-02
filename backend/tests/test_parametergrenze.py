"""Die Sammelabfragen gegen SQLites Parametergrenze.

SQLAlchemy rendert jedes IN-Element als eigene gebundene Variable, und SQLite
deckelt die: 32766 in heutigen Builds, 999 in alten. Vor dem Schnitt in
Scheiben (``db.scheiben``) warfen ``ratings.fuer_anfragen``,
``storage.kontostaende`` und ``quota.uebersichten`` ab dieser Menge
``OperationalError: too many SQL variables`` - die Adresse antwortete 500, wo
der alte Je-Zeile-Weg nur langsam war. Die Anfragetabelle waechst monoton und
wird nie aufgeraeumt; die Grenze ist also erreichbar, nur nicht bald.

Die Mengen hier liegen bewusst ueber 32766 gebundenen Parametern, damit jeder
Test am ungeschnittenen Weg wirklich braeche (nachgewiesen am 02.09.2026:
alle drei rot mit "too many SQL variables", ehe der Schnitt kam). Die
Tabellen bleiben dabei fast leer - die Grenze reisst schon das **Vorbereiten**
der Abfrage, nicht erst ihr Ergebnis.

Neben "faellt nicht um" prueft jeder Test an einem echten Datensatz mitten in
der grossen Menge, dass das Zusammenfuehren der Scheiben die Zahlen nicht
verfaelscht.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    QualityTier,
    RequestStatus,
    Role,
    StorageEntry,
    StorageState,
    TitleRating,
    User,
)
from app.services import quota, ratings, storage
from app.services.settings_service import load_settings

#: Mehr als die 32766 gebundenen Parameter eines heutigen SQLite.
VIELE = 33_000

GB = 1024**3


def _echter_nutzer() -> int:
    """Ein Konto in der Datenbank - die Fremdschluessel verlangen eines."""
    with SessionLocal() as db:
        nutzer = User(
            username="grenzfall",
            # Meldet sich nie an - der Hash muss nur die NOT-NULL-Regel erfuellen.
            password_hash="ungenutzt",
            role=Role.user,
        )
        db.add(nutzer)
        db.commit()
        return nutzer.id


def test_fuer_anfragen_uebersteht_die_parametergrenze() -> None:
    """500 Nutzer und 33000 Titel: zusammen weit ueber der Grenze."""
    kennung = _echter_nutzer()
    with SessionLocal() as db:
        db.add(
            TitleRating(
                user_id=kennung,
                media_type=MediaType.movie,
                tmdb_id=7,
                rating=4,
                title="Grenzfall",
            )
        )
        db.commit()

    anfragen = [SimpleNamespace(user_id=kennung, tmdb_id=7)]
    anfragen += [
        SimpleNamespace(user_id=kennung + 1 + (i % 500), tmdb_id=1_000_000 + i)
        for i in range(VIELE)
    ]

    with SessionLocal() as db:
        ergebnis = ratings.fuer_anfragen(db, anfragen)

    assert len(ergebnis) == 1
    assert ergebnis[(kennung, MediaType.movie, 7, None)].rating == 4


def test_kontostaende_uebersteht_die_parametergrenze() -> None:
    kennung = _echter_nutzer()
    with SessionLocal() as db:
        for nummer, zustand in ((1, StorageState.owned), (2, StorageState.pending)):
            db.add(
                StorageEntry(
                    key=f"movie:standard:tmdb:{nummer}",
                    user_id=kennung,
                    media_type=MediaType.movie,
                    tier=QualityTier.standard,
                    tmdb_id=nummer,
                    title=f"Grenzposten {nummer}",
                    size_bytes=GB,
                    state=zustand,
                )
            )
        db.commit()

    kennungen = [kennung, *range(kennung + 1, kennung + 1 + VIELE)]
    with SessionLocal() as db:
        staende = storage.kontostaende(db, kennungen)

    assert len(staende) == VIELE + 1
    assert staende[kennung].used_bytes == 2 * GB
    assert staende[kennung].items == 2
    assert staende[kennung].pending_bytes == GB
    # Irgendein Konto ohne Posten mitten in der Menge: Nullen, kein Fehlen.
    assert staende[kennung + VIELE // 2].used_bytes == 0


def test_uebersichten_uebersteht_die_parametergrenze() -> None:
    kennung = _echter_nutzer()
    with SessionLocal() as db:
        db.add(
            MediaRequest(
                user_id=kennung,
                media_type=MediaType.movie,
                tier=QualityTier.standard,
                tmdb_id=7,
                title="Grenzfall",
                status=RequestStatus.approved,
                requested_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        db.commit()

    # Fluechtige Konten reichen: ``uebersichten`` liest nur Kennung und
    # Kontingentfelder, und die Zaehl-Abfrage bindet je Kennung einen
    # Parameter - genau die Achse, um die es hier geht.
    konten = [User(id=kennung)]
    konten += [User(id=kennung + 1 + i) for i in range(VIELE)]

    with SessionLocal() as db:
        staende = quota.uebersichten(db, konten, load_settings(db))

    assert len(staende) == VIELE + 1
    assert staende[kennung]["movie"].used == 1
    assert staende[kennung]["tv"].used == 0
    assert staende[kennung + VIELE // 2]["movie"].used == 0
