"""Was eine Regel aufs Haus bucht, zaehlt bei niemandem.

⚠️ **Warum diese Datei entstanden ist.** Eine unabhaengige Pruefung am
03.09.2026 hat die Zeile ``if anfrage.hausbestand: continue`` in
``storage._zuordnung`` entfernt - und **kein einziger** der 2.600 Tests wurde
rot. Auch nicht die 183 aus ``test_storage*``, ``test_befunde``,
``test_aufraeumen`` und ``test_freigabe_speicher``.

Der vorhandene Test las nur die Spalte an der Anfrage. Dass sie *wirkt*, hat
niemand gemessen - und genau das ist der Punkt, an dem das Feature dem
Betreiber etwas verspricht: "zaehlt bei niemandem gegen das
Speicher-Kontingent".

Gemessen wird deshalb hier die **Zurechnung**, nicht die Spalte.
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    QualityTier,
    Regel,
    RegelEntscheidung,
    RequestStatus,
    Role,
    User,
)
from app.services import storage

TMDB = 550


class _Gemessen:
    """Ein Posten, wie ihn die Messung liefert - nur die Felder, die zaehlen."""

    def __init__(self) -> None:
        self.key = f"movie:{TMDB}:standard"
        self.media_type = MediaType.movie
        self.tier = QualityTier.standard
        self.tmdb_id = TMDB
        self.tvdb_id = None
        self.season = None
        self.title = "Ein Film"
        self.size_bytes = 8 * 1024**3


def _nutzer(db: SessionLocal, name: str, rolle: Role = Role.user) -> User:
    person = User(username=name, email=f"{name}@beispiel.de", role=rolle)
    person.password_hash = "x"
    db.add(person)
    db.commit()
    return person


def _anfrage(db: SessionLocal, nutzer: User, *, hausbestand: bool) -> MediaRequest:
    regel = Regel(
        name="Blockbuster ins Haus",
        bedingungen=[{"feld": "typ", "werte": ["movie"]}],
        entscheidung=RegelEntscheidung.freigeben,
        hausbestand=hausbestand,
    )
    db.add(regel)
    db.flush()
    anfrage = MediaRequest(
        user_id=nutzer.id,
        media_type=MediaType.movie,
        tier=QualityTier.standard,
        tmdb_id=TMDB,
        title="Ein Film",
        status=RequestStatus.downloaded,
        regel_id=regel.id,
        hausbestand=hausbestand,
    )
    db.add(anfrage)
    db.commit()
    return anfrage


def test_was_eine_regel_aufs_haus_bucht_gehoert_niemandem() -> None:
    """Der Kern: Der Posten taucht in der Zurechnung gar nicht erst auf.

    ``_zuordnung`` liefert nur, was einem Menschen gehoert. Was fehlt, wird
    beim Anlegen zu ``StorageState.house`` - und damit zaehlt es bei
    niemandem gegen sein Speicher-Kontingent.
    """
    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        _anfrage(db, kim, hausbestand=True)

        zuordnung = storage._zuordnung(db, [_Gemessen()])

    assert f"movie:{TMDB}:standard" not in zuordnung, (
        "Der Titel wurde dem Anfragenden zugerechnet, obwohl die Regel ihn "
        "aufs Haus gebucht hat - das Speicher-Kontingent belastet ihn damit."
    )


def test_dieselbe_anfrage_ohne_den_haken_gehoert_dem_anfragenden() -> None:
    """⚠️ **Die Gegenprobe, und sie ist der wichtigere Teil.**

    Ohne sie bewiese der Test oben nur, dass ``_zuordnung`` irgendetwas nicht
    findet - etwa weil der Schluessel nicht passt oder der Zustand nicht
    zaehlt. Erst der Unterschied zwischen beiden zeigt, dass der Haken die
    Ursache ist.
    """
    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        _anfrage(db, kim, hausbestand=False)

        zuordnung = storage._zuordnung(db, [_Gemessen()])

    assert zuordnung.get(f"movie:{TMDB}:standard") == kim.id
