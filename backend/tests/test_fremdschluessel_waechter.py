"""Der Waechter: Ein abgeschaltetes PRAGMA darf den Verbindungsvorrat nicht ueberleben.

⚠️ **Warum es diese Datei gibt.** ``PRAGMA foreign_keys`` gilt je Verbindung,
und der Vorrat (QueuePool, fuenf Plaetze) reicht dieselbe Verbindung immer
wieder weiter. ``app/db.py`` setzte das PRAGMA lange nur im Ereignis
``connect``, und das feuert ausschliesslich beim **Neuaufbau** einer
Verbindung. Ein einziges ``PRAGMA foreign_keys=OFF``, das jemand nicht
zuruecknimmt, schaltet damit die Fremdschluessel fuer alles ab, was dieselbe
Verbindung spaeter zieht.

Das war kein Gedankenspiel. ``tests/test_child_wishes.py`` musste eine
verwaiste Zeile an der Regel vorbei anlegen und liess das PRAGMA liegen.
Ergebnis: In einem Ausschnitt der Testreihe fiel
``tests/test_tickets.py::test_geloeschtes_konto_nimmt_seine_tickets_mit`` um,
mit ``assert 1 == 0`` und ohne jeden erkennbaren Bezug. Ob es traf, entschied
allein die Reihenfolge der Testdateien. Betroffen waren 37 der 40
Fremdschluessel in ``models.py``, naemlich alle mit ``ON DELETE CASCADE`` oder
``ON DELETE SET NULL``.

Die eine Stelle ist repariert. Dieser Waechter gilt der **naechsten**: Wer
kuenftig ein PRAGMA liegen laesst, soll es hier erfahren und nicht drei Dateien
weiter an einem Test, der mit Fremdschluesseln nichts zu tun hat.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.db import SessionLocal, engine
from app.models import Ticket, User


@pytest.fixture(autouse=True)
def leerer_vorrat() -> Iterator[None]:
    """Vor **und** nach jedem Test dieser Datei einen leeren Verbindungsvorrat.

    **Davor**, weil der Nachweis sonst nicht traegt: Nach dem Leeren ist die
    erste geoeffnete Verbindung die einzige im Vorrat, und die naechste
    Entnahme bekommt zwangslaeufig dieselbe zurueck. Ohne das Leeren
    entschiede die Rotation im Vorrat, ob der Test die vergiftete Verbindung
    ueberhaupt zu sehen bekommt, und ein Waechter, der nur manchmal hinsieht,
    ist keiner.

    **Danach**, damit diese Datei nicht selbst zu dem wird, wovor sie warnt:
    Faellt einer der Tests, liegt seine vergiftete Verbindung noch im Vorrat.
    Ein geplatzter Waechter soll die Datei danach nicht auch noch umwerfen.
    """
    engine.dispose()
    yield
    engine.dispose()


def test_abgeschaltete_fremdschluessel_ueberleben_die_rueckgabe_nicht() -> None:
    """Eine vergiftete Verbindung geht in den Vorrat und kommt geheilt zurueck."""
    # Genau der Griff, der das Leck erzeugt hat: abschalten und zurueckgeben.
    with engine.connect() as verbindung:
        verbindung.exec_driver_sql("PRAGMA foreign_keys=OFF")
        verbindung.commit()
        vergiftet = verbindung.connection.dbapi_connection

    with engine.connect() as verbindung:
        # Erst nachweisen, dass wirklich dieselbe Verbindung zurueckkommt.
        # Sonst waere dieser Test gruen, weil er die vergiftete gar nicht
        # gesehen hat, und das waere schlimmer als rot.
        assert verbindung.connection.dbapi_connection is vergiftet, (
            "Der Vorrat hat eine andere Verbindung herausgegeben; der Test "
            "prueft dann nicht, was er zu pruefen glaubt."
        )
        assert verbindung.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_cascade_haelt_auch_nach_einem_abgeschalteten_pragma() -> None:
    """Und die Folge, an der es sichtbar wurde: Das Konto nimmt sein Ticket mit.

    Der Nachweis oben ist eine Zahl. Dieser hier ist die Wirkung. ``Ticket``
    haengt ueber ``ON DELETE CASCADE`` an ``users``, und daneben gibt es
    keinen ORM-Weg, der einspringen koennte (``models.py`` haelt an ``User``
    keine Sammlung mit ``cascade``). Sind die Fremdschluessel aus, bleibt das
    Ticket eines geloeschten Kontos also einfach liegen.
    """
    with engine.connect() as verbindung:
        verbindung.exec_driver_sql("PRAGMA foreign_keys=OFF")
        verbindung.commit()

    with SessionLocal() as sitzung:
        konto = User(username="vorrat-probe", password_hash="nicht-benutzt")
        sitzung.add(konto)
        sitzung.commit()
        konto_id = konto.id
        sitzung.add(Ticket(user_id=konto_id, subject="Ich komme nicht rein"))
        sitzung.commit()

    with SessionLocal() as sitzung:
        sitzung.query(User).filter(User.id == konto_id).delete()
        sitzung.commit()

    with SessionLocal() as sitzung:
        # Auf das eine Konto eingeschraenkt statt auf die ganze Tabelle: So
        # sagt die Meldung im Ernstfall auch, **wessen** Zeile stehen blieb.
        uebrig = sitzung.query(Ticket).filter(Ticket.user_id == konto_id).count()
    assert uebrig == 0
