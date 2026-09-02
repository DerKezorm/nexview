"""Der Verlauf der Wiedergaben - Spitzen je Viertelstunde.

⚠️ **Die Spitze ist die ganze Aussage.** Wer hier den letzten Stand statt des
hoechsten speichert, bekommt eine Kurve, die immer dann einbricht, wenn jemand
kurz vor der Messung aufhoert - und beantwortet die Frage "reicht meine
Anlage" mit einer Zahl, die zu klein ist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import WiedergabeSpitze
from app.services import wiedergaben
from app.services.mediaserver.base import Umrechnung, Wiedergabe


def _wiedergabe(umrechnung: Umrechnung, bandbreite: int = 1000) -> Wiedergabe:
    return Wiedergabe(
        provider="plex",
        konto="jemand",
        titel="Ein Film",
        media_type="movie",
        umrechnung=umrechnung,
        bandbreite=bandbreite,
    )


def _zeilen() -> list[WiedergabeSpitze]:
    with SessionLocal() as session:
        return list(session.query(WiedergabeSpitze).all())


def test_die_viertelstunde_wird_richtig_bestimmt() -> None:
    """Alles innerhalb einer Viertelstunde landet im selben Abschnitt."""
    fuenf_nach = datetime(2026, 8, 30, 20, 5)
    kurz_vor_halb = datetime(2026, 8, 30, 20, 29, 59)
    halb = datetime(2026, 8, 30, 20, 30)

    assert wiedergaben._abschnitt(fuenf_nach) == "2026-08-30 20:00"
    assert wiedergaben._abschnitt(kurz_vor_halb) == "2026-08-30 20:15"
    assert wiedergaben._abschnitt(halb) == "2026-08-30 20:30"


def test_die_spitze_bleibt_stehen(admin_client: TestClient) -> None:
    """⚠️ **Der wichtigste Test.**

    Innerhalb einer Viertelstunde misst der Rundgang sieben- oder achtmal.
    Wuerde die letzte Messung gewinnen, verschwaende eine Spitze, sobald zwei
    Minuten spaeter jemand aufhoert - und genau die Spitze ist die Antwort.
    """
    with SessionLocal() as session:
        wiedergaben.spitze_merken(
            session,
            [
                _wiedergabe(Umrechnung.bild),
                _wiedergabe(Umrechnung.ton),
                _wiedergabe(Umrechnung.direkt),
            ],
        )
        # Zwei Minuten spaeter schaut nur noch einer.
        wiedergaben.spitze_merken(session, [_wiedergabe(Umrechnung.direkt)])

    zeilen = _zeilen()
    assert len(zeilen) == 1, "eine Viertelstunde ist eine Zeile"
    assert zeilen[0].gleichzeitig == 3
    assert zeilen[0].bild_umrechnungen == 1


def test_leere_messung_wird_trotzdem_geschrieben(admin_client: TestClient) -> None:
    """"Um drei Uhr nachts lief nichts" ist eine Aussage.

    Eine Luecke in der Kurve saehe dagegen aus, als haette die Messung
    ausgesetzt - und dann traut man auch den uebrigen Punkten nicht mehr.
    """
    with SessionLocal() as session:
        wiedergaben.spitze_merken(session, [])

    zeilen = _zeilen()
    assert len(zeilen) == 1
    assert zeilen[0].gleichzeitig == 0


def test_bandbreiten_werden_addiert(admin_client: TestClient) -> None:
    with SessionLocal() as session:
        wiedergaben.spitze_merken(
            session,
            [_wiedergabe(Umrechnung.direkt, 4000), _wiedergabe(Umrechnung.bild, 12000)],
        )
    assert _zeilen()[0].bandbreite_kbit == 16000


def test_alter_verlauf_wird_weggeworfen(admin_client: TestClient) -> None:
    """Zwei Monate reichen - laenger zurueck liegt eine andere Anlage."""
    alt = (
        datetime.now(UTC).replace(tzinfo=None)
        - timedelta(days=wiedergaben.AUFBEWAHREN_TAGE + 5)
    ).strftime("%Y-%m-%d %H:%M")
    jung = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M")

    with SessionLocal() as session:
        session.add(WiedergabeSpitze(abschnitt=alt, gleichzeitig=9))
        session.add(WiedergabeSpitze(abschnitt=jung, gleichzeitig=2))
        session.commit()

        geloescht = wiedergaben.verlauf_aufraeumen(session)

    assert geloescht == 1
    uebrig = _zeilen()
    assert len(uebrig) == 1 and uebrig[0].gleichzeitig == 2


def test_die_tagesspitze_ist_nicht_die_summe(admin_client: TestClient) -> None:
    """Vier um acht und vier um zehn sind nicht acht gleichzeitige.

    Die Frage lautet, wie viele die Anlage **auf einmal** aushalten muss.
    """
    heute = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    with SessionLocal() as session:
        session.add(WiedergabeSpitze(abschnitt=f"{heute} 20:00", gleichzeitig=4))
        session.add(WiedergabeSpitze(abschnitt=f"{heute} 22:00", gleichzeitig=4))
        session.commit()

    antwort = admin_client.get("/api/admin/analyse/wiedergabe").json()
    tag = next(s for s in antwort["spitzen"] if s["tag"] == heute)
    assert tag["gleichzeitig"] == 4
    assert antwort["spitze_gesamt"] == 4
