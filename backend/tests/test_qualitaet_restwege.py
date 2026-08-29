"""Die letzten ungeprueften Wege - kleine Stellen mit grosser Wirkung.

Was hier steht, hat eines gemeinsam: Es laeuft selten, und wenn es laeuft, ist
etwas Ungewoehnliches im Gange. Genau deshalb faellt ein Fehler dort nicht auf -
bis er im Betrieb passiert.
"""

from __future__ import annotations

import io
import tarfile

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Umbenennlauf
from app.services import benennung, trash_bezug
from app.services import qualitaetsprofile as qp
from app.services.arr import ArrError


class Muster:
    """Eine Instanz, die nur Muster kennt - und auf Wunsch beim Umbenennen zickt."""

    label = "Test-Arr"

    def __init__(self, formate, scheitert_bei=None):
        self.formate = [dict(f) for f in formate]
        self.scheitert_bei = scheitert_bei
        self.umbenannt: list[str] = []

    async def custom_formats(self):
        return [dict(f) for f in self.formate]

    async def custom_format_nachziehen(self, format_id, payload):
        if payload["name"] == self.scheitert_bei:
            raise ArrError("geht nicht", 400, code="arr_http_error")
        self.umbenannt.append(payload["name"])
        for f in self.formate:
            if f["id"] == format_id:
                f["name"] = payload["name"]
        return payload


def _f(nummer, name):
    return {"id": nummer, "name": name, "includeCustomFormatWhenRenaming": True,
            "specifications": []}


# ---------------------------------------------------------------------------
# Aufraeumen beim Schreiben eines Profils
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_alt_umbenennen_fasst_nur_muster_des_bauplans_an():
    """⚠️ Der Unterschied zum grossen Aufraeumen.

    Beim Schreiben eines Profils wird nur zurechtgerueckt, was zu **diesem**
    Bauplan gehoert. Alles andere ist nicht Gegenstand dieses Vorgangs - wer
    es trotzdem anfasst, aendert Namen, die der Betreiber gerade gar nicht
    im Blick hat.
    """
    from app.services.trash import Bauplan, Formatwunsch

    plan = Bauplan(
        profilname="P", basis="test", stand="2026-01-01", merge=(),
        min_punkte=0, schluss_punkte=0,
        formate=(Formatwunsch(name="German DL", spezifikationen=[], punkte=1,
                              beim_umbenennen=True),),
    )
    instanz = Muster([_f(1, "NXV - German DL"), _f(2, "NXV - Fremdes")])
    await qp._alt_umbenennen(instanz, await instanz.custom_formats(), plan)
    assert instanz.umbenannt == ["German DL"]
    assert any(f["name"] == "NXV - Fremdes" for f in instanz.formate)


@pytest.mark.anyio
async def test_ein_scheiterndes_umbenennen_stoppt_die_uebrigen_nicht():
    """Ein Muster, das sich nicht umbenennen laesst, darf den Rest nicht aufhalten."""
    instanz = Muster(
        [_f(1, "NXV - A"), _f(2, "NXV - B"), _f(3, "NXV - C")], scheitert_bei="B"
    )
    anzahl = await qp.praefix_aufraeumen(instanz)
    assert anzahl == 2, "A und C muessen trotzdem durchkommen"
    assert instanz.umbenannt == ["A", "C"]


# ---------------------------------------------------------------------------
# Das Paket beim Holen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ein_zu_grosses_paket_wird_abgebrochen(monkeypatch):
    """⚠️ Ohne Grenze zoege ein falscher Link den Arbeitsspeicher leer.

    Das Paket kommt von aussen; seine Groesse ist nichts, worauf Nexview sich
    verlassen darf. Abgebrochen wird **waehrend** des Ladens, nicht danach.
    """
    monkeypatch.setattr(trash_bezug, "MAX_BYTES", 100)

    class Antwort:
        status_code = 200

        async def aiter_bytes(self):
            for _ in range(10):
                yield b"x" * 50

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class Klient:
        def stream(self, *_a, **_k):
            return Antwort()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(trash_bezug.httpx, "AsyncClient", lambda **_k: Klient())
    with pytest.raises(trash_bezug.BezugFehler) as fehler:
        await trash_bezug._paket_holen()
    assert fehler.value.code == "trash_too_large"


@pytest.mark.anyio
async def test_ein_nicht_erreichbares_paket_wird_benannt(monkeypatch):
    class Antwort:
        status_code = 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class Klient:
        def stream(self, *_a, **_k):
            return Antwort()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(trash_bezug.httpx, "AsyncClient", lambda **_k: Klient())
    with pytest.raises(trash_bezug.BezugFehler) as fehler:
        await trash_bezug._paket_holen()
    assert fehler.value.code == "trash_unreachable"


def test_ein_beschaedigtes_paket_fliegt_nicht_durch():
    """Kein gueltiges Tar-Archiv - das darf keine unbehandelte Ausnahme geben."""
    with pytest.raises(Exception) as fehler:
        trash_bezug._aus_paket(b"das ist kein tar.gz")
    assert not isinstance(fehler.value, (KeyError, AttributeError)), (
        "Ein kaputtes Paket soll an einer benennbaren Stelle scheitern, "
        "nicht an einem Zugriff ins Leere"
    )


# ---------------------------------------------------------------------------
# Laeufe beim Start wieder aufnehmen
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _sauber():
    with SessionLocal() as db:
        for lauf in db.scalars(select(Umbenennlauf)):
            db.delete(lauf)
        db.commit()
    yield
    with SessionLocal() as db:
        for lauf in db.scalars(select(Umbenennlauf)):
            db.delete(lauf)
        db.commit()


def test_ohne_offene_laeufe_passiert_nichts():
    assert benennung.abgebrochene_aufnehmen() == 0


def test_ein_fertiger_lauf_gilt_nicht_als_offen():
    """⚠️ Sonst liefe nach jedem Start derselbe Lauf noch einmal."""
    with SessionLocal() as db:
        db.add(
            Umbenennlauf(kennung="radarr-standard", dienst="radarr", schritt="fertig",
                         gesamt=10, erledigt=10, betroffen=10, offen=[])
        )
        db.commit()
    assert benennung.offene_laeufe() == []
    assert benennung.abgebrochene_aufnehmen() == 0


def test_ein_lauf_im_pruefschritt_gilt_als_offen():
    """Pruefen liest nur - aber der Betreiber hat einen Lauf angestossen.

    ⚠️ Ihn zu verwerfen hiesse, eine Anweisung stillschweigend zu vergessen.
    """
    with SessionLocal() as db:
        db.add(
            Umbenennlauf(kennung="radarr-standard", dienst="radarr", schritt="pruefen",
                         gesamt=100, erledigt=40, betroffen=0, offen=[])
        )
        db.commit()
    offen = benennung.offene_laeufe()
    assert [l["kennung"] for l in offen] == ["radarr-standard"]


def test_der_stand_kommt_auch_aus_der_datenbank():
    """⚠️ Ohne das saehe nur die Sitzung den Lauf, die ihn angestossen hat.

    Wer die Seite neu laedt, saehe einen leeren Balken - waehrend im
    Hintergrund tausende Dateien umbenannt werden.
    """
    with SessionLocal() as db:
        db.add(
            Umbenennlauf(
                kennung="radarr-standard", dienst="radarr", schritt="umbenennen",
                instanz="Radarr FHD", gesamt=3531, erledigt=450, betroffen=3531,
                offen=[1, 2, 3], beispiele=["Film.mkv"], fortgesetzt=True,
            )
        )
        db.commit()
    stand = benennung.umbenennstand("radarr-standard")
    assert stand is not None
    assert stand.instanz == "Radarr FHD", "der Anzeigename, nicht die Kennung"
    assert (stand.erledigt, stand.gesamt) == (450, 3531)
    assert stand.fortgesetzt is True
