"""Was passiert, wenn mittendrin etwas wegbricht?

⚠️ **Warum das eigene Tests braucht.** Die uebrigen Tests pruefen, dass es
funktioniert, wenn alles da ist. Im Alltag ist aber genau das nicht der Fall:
Radarr wird neu gestartet, waehrend Nexview schreibt; ein Schluessel wird
widerrufen; ein Reverse Proxy schiebt eine HTML-Fehlerseite dazwischen. Ein
Werkzeug, das dann abstuerzt oder - schlimmer - stillschweigend halb fertige
Arbeit hinterlaesst, ist im Betrieb unbrauchbar.

Gearbeitet wird gegen einen **echten HTTP-Server** auf einem lokalen Port, den
diese Tests gezielt kaputtgehen lassen. Attrappen wuerden hier zu wenig sagen:
Die interessanten Fehler entstehen in der HTTP-Schicht.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Umbenennlauf
from app.services import benennung
from app.services.arr import ArrClient, ArrError


class Launen:
    """Wie sich der gespielte Radarr gerade verhaelt."""

    def __init__(self) -> None:
        self.modus = "gut"          # gut | fehler500 | html | schluessel_weg
        self.titel = 60
        self.auftraege = 0
        #: Ab dem wievielten Auftrag es kaputtgeht (None = nie).
        self.bricht_ab_bei: int | None = None
        #: Nur die Vorschau scheitert - der Rest der Instanz antwortet.
        self.vorschau_kaputt = False
        self.umbenannt: list[int] = []


class Handler(BaseHTTPRequestHandler):
    launen: Launen

    def log_message(self, *_args):  # Ruhe im Testprotokoll
        pass

    def _sende(self, code: int, koerper, typ="application/json"):
        roh = (json.dumps(koerper) if typ == "application/json" else koerper).encode()
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    def _stoerung(self) -> bool:
        """Gilt die eingestellte Laune? Dann ist die Antwort schon raus."""
        laune = self.launen.modus
        if laune == "schluessel_weg":
            self._sende(401, {"message": "Unauthorized"})
            return True
        if laune == "fehler500":
            self._sende(500, {"message": "kaputt"})
            return True
        if laune == "html":
            # ⚠️ Der Klassiker: Ein Reverse Proxy antwortet mit einer
            # Fehlerseite. Status 200, aber kein JSON.
            self._sende(200, "<html><body>502 Bad Gateway</body></html>", "text/html")
            return True
        return False

    def do_GET(self):
        # ⚠️ Die Umbenenn-Vorschau ist ein **GET** mit Abfrageparameter
        # (``/rename?movieId=42``), kein POST. Wer das verwechselt, baut eine
        # Attrappe, die nie etwas zu tun findet - und testet nichts.
        if self.path.startswith("/api/v3/rename"):
            if self.launen.vorschau_kaputt or self._stoerung():
                if not self.launen.vorschau_kaputt:
                    return
                self._sende(500, {"message": "Vorschau kaputt"})
                return
            nummer = 0
            if "=" in self.path:
                try:
                    nummer = int(self.path.rsplit("=", 1)[1])
                except ValueError:
                    nummer = 0
            self._sende(200, [{"newPath": f"/x/{nummer}.mkv"}] if nummer % 2 == 0 else [])
            return
        if self._stoerung():
            return
        if self.path.startswith("/api/v3/movie"):
            self._sende(200, [{"id": n, "hasFile": True} for n in range(1, self.launen.titel + 1)])
        elif self.path.startswith("/api/v3/command/"):
            self._sende(200, {"status": "completed"})
        elif self.path.startswith("/api/v3/qualityprofile") or self.path.startswith("/api/v3/customformat"):
            self._sende(200, [])
        else:
            self._sende(200, [])

    def do_POST(self):
        laenge = int(self.headers.get("Content-Length") or 0)
        roh = self.rfile.read(laenge) if laenge else b"{}"
        if self.path.endswith("/command"):
            self.launen.auftraege += 1
            if (
                self.launen.bricht_ab_bei is not None
                and self.launen.auftraege > self.launen.bricht_ab_bei
            ):
                # Ab hier ist die Instanz weg.
                self._sende(500, {"message": "weg"})
                return
            if self._stoerung():
                return
            self.launen.umbenannt.extend(json.loads(roh).get("movieIds", []))
            self._sende(201, {"id": self.launen.auftraege})
            return
        if self._stoerung():
            return
        self._sende(201, {"id": 1})


@pytest.fixture
def gespielte_instanz():
    """Ein echter HTTP-Server, dessen Verhalten der Test steuert."""
    launen = Launen()
    Handler.launen = launen
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = HTTPServer(("127.0.0.1", port), Handler)
    faden = threading.Thread(target=server.serve_forever, daemon=True)
    faden.start()
    try:
        yield launen, ArrClient(f"http://127.0.0.1:{port}", "k", "Test-Radarr")
    finally:
        server.shutdown()
        server.server_close()


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


def _lauf(kennung="test-arr"):
    with SessionLocal() as db:
        return db.scalar(select(Umbenennlauf).where(Umbenennlauf.kennung == kennung))


# ---------------------------------------------------------------------------
# 1. Die Instanz ist gar nicht da
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_geschlossener_port_gibt_eine_nennbare_meldung():
    """Kein Absturz, kein nackter Verbindungsfehler - eine Kennung."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        toter_port = s.getsockname()[1]
    client = ArrClient(f"http://127.0.0.1:{toter_port}", "k", "Test-Radarr")

    with pytest.raises(ArrError) as fehler:
        await client.quality_profiles()
    assert fehler.value.code == "arr_unreachable"
    assert "Test-Radarr" in fehler.value.message


@pytest.mark.anyio
async def test_widerrufener_schluessel_wird_benannt(gespielte_instanz):
    """401 heisst "Schluessel", nicht "kaputt" - der Unterschied zaehlt."""
    launen, client = gespielte_instanz
    launen.modus = "schluessel_weg"
    with pytest.raises(ArrError) as fehler:
        await client.quality_profiles()
    assert fehler.value.code == "arr_key_rejected"


@pytest.mark.anyio
async def test_html_statt_json_wird_erkannt(gespielte_instanz):
    """⚠️ Der Reverse-Proxy-Fall: Status 200, aber eine Fehlerseite im Leib.

    Ohne eigene Behandlung flaege hier ein JSONDecodeError durch - eine
    Meldung, mit der niemand etwas anfangen kann.
    """
    launen, client = gespielte_instanz
    launen.modus = "html"
    with pytest.raises(ArrError) as fehler:
        await client.quality_profiles()
    assert fehler.value.code == "arr_unexpected_answer"


# ---------------------------------------------------------------------------
# 2. Die Verbindung bricht MITTEN im Bestandslauf
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_abbruch_mittendrin_haelt_den_rest_fest(gespielte_instanz):
    """⚠️ Der eigentliche Ernstfall.

    Bricht die Instanz nach dem ersten Haeppchen weg, darf **nicht** eine halb
    umbenannte Bibliothek ohne Spur zurueckbleiben. Der Eintrag muss stehen und
    genau die noch offenen Titel nennen.
    """
    launen, client = gespielte_instanz
    launen.bricht_ab_bei = 1  # das zweite Haeppchen scheitert

    with pytest.raises(ArrError):
        await benennung.bestand_umbenennen(
            client, "radarr", benennung.Umbenennstand(), "test-arr"
        )

    lauf = _lauf()
    assert lauf is not None, "Ein Abbruch darf nicht spurlos verschwinden"
    assert lauf.schritt == "umbenennen"
    assert len(launen.umbenannt) == benennung.HAEPPCHEN
    # 30 betroffene Titel, 25 erledigt -> 5 offen.
    assert len(lauf.offen) == 30 - benennung.HAEPPCHEN
    assert set(lauf.offen).isdisjoint(launen.umbenannt)


@pytest.mark.anyio
async def test_nach_dem_abbruch_wird_der_rest_erledigt(gespielte_instanz):
    """Und beim naechsten Anlauf ist genau der Rest dran - keiner doppelt."""
    launen, client = gespielte_instanz
    launen.bricht_ab_bei = 1
    with pytest.raises(ArrError):
        await benennung.bestand_umbenennen(
            client, "radarr", benennung.Umbenennstand(), "test-arr"
        )
    zuerst = list(launen.umbenannt)
    offen = list(_lauf().offen)

    # Die Instanz ist wieder da.
    launen.bricht_ab_bei = None
    launen.umbenannt.clear()
    stand = benennung.Umbenennstand()
    await benennung.bestand_umbenennen(
        client, "radarr", stand, "test-arr", weiter_mit=offen
    )

    assert stand.fortgesetzt is True
    assert sorted(launen.umbenannt) == sorted(offen)
    assert sorted(zuerst + launen.umbenannt) == sorted(
        n for n in range(1, launen.titel + 1) if n % 2 == 0
    )
    assert _lauf() is None, "Nach dem Abschluss gehoert der Eintrag weg"


@pytest.mark.anyio
async def test_eine_stumme_vorschau_kippt_den_lauf_nicht(gespielte_instanz):
    """Ein einzelner Titel, der nicht antwortet, darf nicht alles aufhalten.

    Die Vorschau laeuft ueber tausende Titel; einer davon kann immer haken.
    Wer dann abbricht, macht den ganzen Lauf von der schwaechsten Antwort
    abhaengig.
    """
    launen, client = gespielte_instanz
    launen.vorschau_kaputt = True  # nur die Vorschau, der Rest lebt
    stand = benennung.Umbenennstand()
    await benennung.bestand_umbenennen(client, "radarr", stand, "test-arr")
    # Nichts zu tun - aber sauber beendet statt geworfen.
    assert stand.schritt == "fertig"
    assert stand.betroffen == 0
    assert _lauf() is None


# ---------------------------------------------------------------------------
# 3. Die Instanz verschwindet aus der Einrichtung
# ---------------------------------------------------------------------------


def test_lauf_einer_geloeschten_instanz_wird_verworfen():
    """⚠️ Sonst versuchte Nexview bei jedem Start eine Instanz zu erreichen,
    die es nicht mehr gibt - und die Zeile bliebe fuer immer liegen.
    """
    with SessionLocal() as db:
        db.add(
            Umbenennlauf(
                kennung="gibt-es-nicht-mehr", dienst="radarr", schritt="umbenennen",
                gesamt=10, erledigt=0, betroffen=10, offen=[1, 2, 3],
            )
        )
        db.commit()

    aufgenommen = benennung.abgebrochene_aufnehmen()

    assert aufgenommen == 0
    assert _lauf("gibt-es-nicht-mehr") is None
