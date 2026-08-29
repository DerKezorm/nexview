"""Der Bestandslauf muss einen Abbruch ueberleben.

⚠️ **Warum das die wichtigste Eigenschaft dieser Funktion ist.** Ein Lauf ueber
mehrere tausend Titel dauert lange. Faellt der Prozess mittendrin aus -
Container-Neustart, zugeklappter Deckel, Absturz -, bleibt ohne Sicherung eine
**halb umbenannte Bibliothek** zurueck: teils altes, teils neues Schema, und
nirgends steht, wo die Grenze verlaeuft. Genau das entscheidet, ob man das
Werkzeug auf 4000 Filme loslassen kann.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Umbenennlauf
from app.services import benennung


class GefaelschteInstanz:
    """Eine Instanz, die auf Wunsch mitten im Lauf zusammenbricht."""

    label = "Test-Radarr"

    def __init__(self, titel: int = 60, bricht_bei: int | None = None):
        self.titel = titel
        self.bricht_bei = bricht_bei
        self.umbenannt: list[int] = []
        self.auftraege = 0

    async def get(self, pfad: str):
        return [{"id": n} for n in range(1, self.titel + 1)]

    async def umbenennen_vorschau(self, feld: str, nummer: int):
        # Jeder zweite Titel wuerde sich aendern.
        return [{"newPath": f"/media/Film {nummer}.mkv"}] if nummer % 2 == 0 else []

    async def befehl(self, name: str, **felder):
        self.auftraege += 1
        if self.bricht_bei is not None and self.auftraege > self.bricht_bei:
            raise RuntimeError("Instanz weg")
        self.umbenannt.extend(next(iter(felder.values())))
        return {"id": self.auftraege}

    async def befehl_stand(self, nummer: int):
        return {"status": "completed"}


@pytest.fixture(autouse=True)
def _sauber():
    """Kein Lauf darf aus einem Nachbartest stehenbleiben."""
    with SessionLocal() as db:
        for lauf in db.scalars(select(Umbenennlauf)):
            db.delete(lauf)
        db.commit()
    yield
    with SessionLocal() as db:
        for lauf in db.scalars(select(Umbenennlauf)):
            db.delete(lauf)
        db.commit()


def _lauf(kennung: str) -> Umbenennlauf | None:
    with SessionLocal() as db:
        return db.scalar(select(Umbenennlauf).where(Umbenennlauf.kennung == kennung))


@pytest.mark.asyncio
async def test_ein_glatter_lauf_hinterlaesst_keine_spur():
    """Was fertig ist, gehoert nicht mehr in die Datenbank.

    Sonst naehme der naechste Start einen abgeschlossenen Lauf wieder auf.
    """
    instanz = GefaelschteInstanz(titel=60)
    stand = benennung.Umbenennstand()
    await benennung.bestand_umbenennen(instanz, "radarr", stand, "radarr-standard")

    assert stand.schritt == "fertig"
    assert len(instanz.umbenannt) == 30  # jeder zweite
    assert _lauf("radarr-standard") is None


@pytest.mark.asyncio
async def test_ein_abbruch_haelt_fest_was_noch_offen_ist():
    """⚠️ Der eigentliche Punkt: Nach dem Abbruch muss der Rest bekannt sein."""
    instanz = GefaelschteInstanz(titel=60, bricht_bei=1)
    stand = benennung.Umbenennstand()
    with pytest.raises(RuntimeError):
        await benennung.bestand_umbenennen(instanz, "radarr", stand, "radarr-standard")

    lauf = _lauf("radarr-standard")
    assert lauf is not None, "Ein abgebrochener Lauf darf nicht spurlos verschwinden"
    # Das erste Haeppchen ging durch, der Rest steht noch aus.
    assert len(instanz.umbenannt) == benennung.HAEPPCHEN
    assert len(lauf.offen) == 30 - benennung.HAEPPCHEN
    # Und zwar genau die, die noch nicht dran waren.
    assert set(lauf.offen).isdisjoint(instanz.umbenannt)


@pytest.mark.asyncio
async def test_fortsetzen_macht_nur_den_rest_und_prueft_nicht_neu():
    """Der zweite Anlauf erledigt den Rest - ohne die Vorschau zu wiederholen.

    ⚠️ Die Vorschau ueber mehrere tausend Titel kostet Minuten. Sie zu
    wiederholen waere nicht falsch, aber verschwendet - das Ergebnis liegt vor.
    """
    kaputt = GefaelschteInstanz(titel=60, bricht_bei=1)
    with pytest.raises(RuntimeError):
        await benennung.bestand_umbenennen(
            kaputt, "radarr", benennung.Umbenennstand(), "radarr-standard"
        )
    offen = list(_lauf("radarr-standard").offen)

    heil = GefaelschteInstanz(titel=60)
    stand = benennung.Umbenennstand()
    await benennung.bestand_umbenennen(
        heil, "radarr", stand, "radarr-standard", weiter_mit=offen
    )

    assert stand.fortgesetzt is True
    assert stand.schritt == "fertig"
    assert sorted(heil.umbenannt) == sorted(offen)
    # Zusammen mit dem ersten Anlauf sind alle 30 erledigt - keiner doppelt.
    assert sorted(kaputt.umbenannt + heil.umbenannt) == sorted(
        n for n in range(1, 61) if n % 2 == 0
    )
    assert _lauf("radarr-standard") is None


@pytest.mark.asyncio
async def test_ohne_kennung_wird_nichts_festgehalten():
    """Aufrufe ohne Kennung (Tests, Einzelfaelle) schreiben keine Zeile."""
    instanz = GefaelschteInstanz(titel=10)
    await benennung.bestand_umbenennen(instanz, "radarr", benennung.Umbenennstand())
    with SessionLocal() as db:
        assert db.scalars(select(Umbenennlauf)).all() == []


@pytest.mark.asyncio
async def test_nichts_zu_tun_hinterlaesst_auch_nichts():
    """Eine Bibliothek, die schon passt, darf keinen offenen Lauf zuruecklassen."""

    class SchonGut(GefaelschteInstanz):
        async def umbenennen_vorschau(self, feld: str, nummer: int):
            return []

    stand = benennung.Umbenennstand()
    await benennung.bestand_umbenennen(SchonGut(titel=10), "radarr", stand, "radarr-standard")
    assert stand.schritt == "fertig"
    assert _lauf("radarr-standard") is None
