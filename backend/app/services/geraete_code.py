"""Fernseher koppeln: Das Geraet zeigt einen Code, ein Mensch bestaetigt ihn im Browser.

Muster ist der OAuth Device Authorization Grant (RFC 8628), zugeschnitten auf
nexview:

1. Das Geraet ruft ``starten`` (ohne Anmeldung) und bekommt einen kurzen Code
   zum Abtippen (``BCDF-GHJK``) und einen langen geheimen Geraete-Code.
2. Ein angemeldeter Erwachsener gibt den kurzen Code im Browser ein (oder
   scannt den QR-Code des Fernsehers, der die Seite mit dem Code oeffnet),
   sieht Geraet und App und bestaetigt (``bestaetigen``) oder lehnt ab.
   Ein Elternteil kann dabei eines seiner Kinder waehlen.
3. Das Geraet fragt mit dem geheimen Code alle paar Sekunden nach
   (``abfragen``). Nach der Bestaetigung entsteht beim naechsten Nachfragen
   **einmal** ein Schluessel, genau wie bei ``app_kopplung.abholen``.

⚠️ **Wer den kurzen Code bestaetigt, gibt dem Geraet seinen Zugang.** Der
kurze Code ist deshalb kein Geheimnis des Geraets, sondern eine Frage an den
Menschen. Die Seite zeigt Geraet und App und sagt ausdruecklich, nur einen
Code zu bestaetigen, der gerade auf dem eigenen Bildschirm steht.

⚠️ **Die Bremsen.** Drei Tueren stehen offen oder fast offen:

* ``starten`` braucht keine Anmeldung und schreibt eine Zeile. Gebremst durch
  hoechstens ``OFFEN_HOECHSTENS`` offene Codes in der ganzen Installation und,
  wenn nexview die Adresse des Absenders kennt (``NEXVIEW_CLIENT_IP``),
  ``START_JE_ADRESSE`` Starts je Adresse in ``START_FENSTER``.
* ``abfragen``: hoechstens alle ``INTERVALL`` Sekunden je Geraete-Code. Ein
  unbekannter Geraete-Code (256 Bit) laesst sich nicht erraten, er kostet nur
  einen Blick in die Tabelle und schreibt nichts.
* Die Code-Eingabe im Browser zaehlt Fehlversuche in der ``anmeldebremse``
  (Router). Ein Treffer setzt den Zaehler **nicht** zurueck: Jeder kann sich
  per ``starten`` gueltige Codes machen und damit sonst die Bremse leeren.

Die Merkposten der Bremsen liegen im Arbeitsspeicher, aus demselben Grund wie
bei der ``anmeldebremse``: Ein Fehlversuch soll keinen Schreibzugriff kosten.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..models import GeraeteCode, Role, User, utcnow
from . import api_schluessel

logger = logging.getLogger(__name__)

#: So lange gilt ein Code. Lang genug, um das Telefon zu holen und sich
#: anzumelden.
LEBENSDAUER = timedelta(minutes=10)

#: So oft darf ein Geraet nachfragen, in Sekunden.
INTERVALL = 5

#: Ein Nachfragen, das so viel frueher kommt, geht noch durch: Uhren und Netze
#: sind nicht auf die Millisekunde genau.
SPIELRAUM = 1.0

#: Hoechstens so viele offene Codes in der ganzen Installation. Ein Haushalt
#: koppelt selten mehr als einen Fernseher zugleich; mehr als das hier ist ein
#: Skript, das Zeilen anlegen will.
OFFEN_HOECHSTENS = 30

#: Je Absenderadresse (nur wenn nexview sie kennt).
START_JE_ADRESSE = 10
START_FENSTER = 600.0

#: ``/api/app/1/hello``: Die Suche im Heimnetz fragt einmal je Oeffnen der
#: App. Locker gebremst, und nur, wenn nexview die Absenderadresse kennt.
HALLO_JE_ADRESSE = 60
HALLO_FENSTER = 60.0

#: Ohne Vokale und Ziffern: keine Woerter, kein 0/O, 1/I/L-Raten. 20 Zeichen,
#: acht Stellen, rund 2,5 * 10^10 Moeglichkeiten.
ZEICHEN = "BCDFGHJKLMNPQRSTVWXZ"
LAENGE = 8

GERAET_HOECHSTENS = 80
APP_HOECHSTENS = 40


class GeraeteCodeFehler(Exception):
    """Traegt eine Kennung, damit die Oberflaeche den richtigen Satz zeigt."""

    def __init__(self, code: str, text: str, status: int, *, warten: int | None = None) -> None:
        super().__init__(text)
        self.code = code
        self.text = text
        self.status = status
        #: Fuer 429: wie lange das Geraet warten soll (``Retry-After``).
        self.warten = warten


@dataclass(frozen=True)
class Gestartet:
    #: Nur in dieser einen Antwort im Klartext.
    geraete_code: str
    benutzer_code: str
    gilt_bis: datetime


@dataclass(frozen=True)
class Abfrage:
    #: ``pending``, ``denied``, ``expired`` oder ``approved``.
    stand: str
    schluessel: str | None = None
    konto: User | None = None


# ---------------------------------------------------------------------------
# Merkposten im Arbeitsspeicher
# ---------------------------------------------------------------------------

#: Wann ein Geraete-Code zuletzt nachgefragt hat (``time.monotonic``).
_zuletzt_gefragt: dict[str, float] = {}
#: Wann eine Adresse zuletzt gestartet hat.
_starts: dict[str, deque[float]] = {}
#: Wann eine Adresse zuletzt "hello" gefragt hat.
_hallos: dict[str, deque[float]] = {}


def _uhr() -> float:
    return time.monotonic()


def zuruecksetzen() -> None:
    """Alles vergessen. Nur fuer Tests."""
    _zuletzt_gefragt.clear()
    _starts.clear()
    _hallos.clear()


def _merkposten_aufraeumen(jetzt: float) -> None:
    alt = LEBENSDAUER.total_seconds() + INTERVALL
    for schluessel in [s for s, z in _zuletzt_gefragt.items() if jetzt - z > alt]:
        _zuletzt_gefragt.pop(schluessel, None)
    for adresse in [a for a, d in _starts.items() if not d or jetzt - d[-1] > START_FENSTER]:
        _starts.pop(adresse, None)
    for adresse in [a for a, d in _hallos.items() if not d or jetzt - d[-1] > HALLO_FENSTER]:
        _hallos.pop(adresse, None)


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------


def _pruefsumme(geraete_code: str) -> str:
    return hashlib.sha256(geraete_code.encode("utf-8")).hexdigest()


def normalisieren(eingabe: str | None) -> str | None:
    """``bcdf-ghjk``, ``BCDF GHJK`` und ``BCDFGHJK`` sind derselbe Code. Sonst ``None``."""
    if not eingabe:
        return None
    sauber = "".join(z for z in eingabe.upper() if z.isalnum())
    if len(sauber) != LAENGE or any(z not in ZEICHEN for z in sauber):
        return None
    return sauber


def anzeigen(benutzer_code: str) -> str:
    """Mit Bindestrich in der Mitte, so wie ihn der Fernseher zeigt."""
    mitte = LAENGE // 2
    return f"{benutzer_code[:mitte]}-{benutzer_code[mitte:]}"


def _mit_zone(zeit: datetime) -> datetime:
    return zeit if zeit.tzinfo is not None else zeit.replace(tzinfo=UTC)


def _abgelaufen(zeile: GeraeteCode, jetzt: datetime) -> bool:
    return _mit_zone(zeile.expires_at) < jetzt


def _ohne_zone(zeit: datetime) -> datetime:
    return zeit.replace(tzinfo=None) if zeit.tzinfo else zeit


def _aufraeumen(db: Session, jetzt: datetime) -> None:
    """Abgelaufene Codes verschwinden, aber erst eine Lebensdauer spaeter.

    So lange liest ein Geraet, das nachfragt, noch "abgelehnt" statt nur
    "abgelaufen", und ein kurzer Code wird nicht gleich wieder vergeben.
    """
    db.execute(delete(GeraeteCode).where(GeraeteCode.expires_at < _ohne_zone(jetzt) - LEBENSDAUER))


def _offen(db: Session, jetzt: datetime) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(GeraeteCode)
            .where(
                GeraeteCode.stand.in_(("wartet", "bestaetigt")),
                GeraeteCode.expires_at >= _ohne_zone(jetzt),
            )
        )
        or 0
    )


def _einzeilig(text: str, laenge: int) -> str:
    return " ".join((text or "").split())[:laenge]


def _start_bremsen(adresse: str | None) -> None:
    if not adresse:
        return
    jetzt = _uhr()
    _merkposten_aufraeumen(jetzt)
    liste = _starts.setdefault(adresse, deque())
    while liste and jetzt - liste[0] > START_FENSTER:
        liste.popleft()
    if len(liste) >= START_JE_ADRESSE:
        warten = max(1, int(START_FENSTER - (jetzt - liste[0])) + 1)
        raise GeraeteCodeFehler(
            "device_code_busy",
            "Gerade sind zu viele Codes offen. Bitte in ein paar Minuten noch einmal versuchen.",
            429,
            warten=warten,
        )
    liste.append(jetzt)


def hallo_bremsen(adresse: str | None) -> None:
    """Hoechstens ``HALLO_JE_ADRESSE`` Mal je ``HALLO_FENSTER`` je bekannter Adresse.

    Ohne bekannte Adresse gar nicht: Die Antwort kostet fast nichts, und eine
    Grenze fuer alle zusammen liesse einen einzelnen Absender jede App im
    Haushalt aussperren.
    """
    if not adresse:
        return
    jetzt = _uhr()
    _merkposten_aufraeumen(jetzt)
    liste = _hallos.setdefault(adresse, deque())
    while liste and jetzt - liste[0] > HALLO_FENSTER:
        liste.popleft()
    if len(liste) >= HALLO_JE_ADRESSE:
        raise GeraeteCodeFehler(
            "device_poll_too_fast",
            "Bitte nicht so oft nachfragen.",
            429,
            warten=max(1, int(HALLO_FENSTER - (jetzt - liste[0])) + 1),
        )
    liste.append(jetzt)


def starten(db: Session, *, geraet: str, app: str, adresse: str | None = None) -> Gestartet:
    """Einen neuen Code fuer ein Geraet anlegen. Committet.

    ``adresse`` ist die Absenderadresse, sofern nexview sie kennt
    (``anmeldebremse.client_ip``), sonst ``None``.
    """
    _start_bremsen(adresse)
    jetzt = utcnow()
    _aufraeumen(db, jetzt)
    if _offen(db, jetzt) >= OFFEN_HOECHSTENS:
        db.commit()
        raise GeraeteCodeFehler(
            "device_code_busy",
            "Gerade sind zu viele Codes offen. Bitte in ein paar Minuten noch einmal versuchen.",
            429,
            warten=60,
        )

    # Ein kurzer Code darf nur einmal in der Tabelle stehen. Bei 2,5 * 10^10
    # Moeglichkeiten und hoechstens ein paar Dutzend Zeilen reicht fast immer
    # der erste Wurf.
    for _ in range(20):
        benutzer_code = "".join(secrets.choice(ZEICHEN) for _ in range(LAENGE))
        if db.scalar(select(GeraeteCode).where(GeraeteCode.benutzer_code == benutzer_code)) is None:
            break
    else:  # pragma: no cover - praktisch unerreichbar
        raise GeraeteCodeFehler("device_code_busy", "Gerade geht es nicht. Bitte gleich noch einmal.", 429, warten=5)

    geraete_code = secrets.token_urlsafe(32)
    zeile = GeraeteCode(
        geraet_hash=_pruefsumme(geraete_code),
        benutzer_code=benutzer_code,
        geraet=_einzeilig(geraet, GERAET_HOECHSTENS),
        app=_einzeilig(app, APP_HOECHSTENS),
        stand="wartet",
        created_at=jetzt,
        expires_at=jetzt + LEBENSDAUER,
    )
    db.add(zeile)
    db.commit()
    logger.info("A device asked for a pairing code (%r, %r)", zeile.geraet, zeile.app)
    return Gestartet(geraete_code=geraete_code, benutzer_code=benutzer_code, gilt_bis=zeile.expires_at)


def finden(db: Session, eingabe: str | None) -> GeraeteCode | None:
    """Der wartende Code zu dieser Eingabe, sonst ``None``.

    ⚠️ Unbekannt, abgelaufen, abgelehnt und schon bestaetigt sehen von aussen
    gleich aus: Wer einen Code nur raet, erfaehrt nichts ueber ihn.
    """
    benutzer_code = normalisieren(eingabe)
    if benutzer_code is None:
        return None
    zeile = db.scalar(select(GeraeteCode).where(GeraeteCode.benutzer_code == benutzer_code))
    if zeile is None or zeile.stand != "wartet" or _abgelaufen(zeile, utcnow()):
        return None
    return zeile


def nicht_da() -> GeraeteCodeFehler:
    return GeraeteCodeFehler(
        "device_code_not_found",
        "Diesen Code gibt es nicht (mehr). Schau auf den Fernseher und tipp ihn noch einmal ab.",
        404,
    )


def geraetename(geraet: str) -> str:
    sauber = _einzeilig(geraet, GERAET_HOECHSTENS)
    return sauber or "TV"


def bestaetigen(
    db: Session, eingabe: str | None, *, fuer: User, von: User, name: str | None = None
) -> GeraeteCode:
    """``von`` bestaetigt den Code; der Schluessel wird ``fuer`` gehoeren. Committet.

    Wer fuer welches Kind bestaetigen darf, prueft der Router
    (``children.kind_von``), nicht dieser Dienst.
    """
    zeile = finden(db, eingabe)
    if zeile is None:
        raise nicht_da()
    if not fuer.is_active:
        raise GeraeteCodeFehler("app_pairing_account_inactive", "Das Konto ist deaktiviert.", 409)
    werte: dict[str, object] = {"stand": "bestaetigt", "user_id": fuer.id, "bestaetigt_von": von.id}
    if name is not None and _einzeilig(name, GERAET_HOECHSTENS):
        werte["geraet"] = _einzeilig(name, GERAET_HOECHSTENS)
    # ⚠️ Bedingt: Bestaetigen zwei Menschen denselben Code im selben
    # Augenblick, gewinnt genau einer. Sonst gehoerte der Schluessel dem, der
    # zuletzt schrieb, und der andere saehe "verbunden".
    geaendert = db.execute(
        update(GeraeteCode)
        .where(GeraeteCode.geraet_hash == zeile.geraet_hash, GeraeteCode.stand == "wartet")
        .values(**werte)
    )
    db.commit()
    if geaendert.rowcount != 1:
        raise nicht_da()
    db.refresh(zeile)
    logger.info(
        "%r confirmed a device pairing code for %r (%r)", von.username, fuer.username, zeile.geraet
    )
    return zeile


def ablehnen(db: Session, eingabe: str | None, *, von: User) -> None:
    """Das Geraet bekommt beim naechsten Nachfragen "abgelehnt". Committet."""
    zeile = finden(db, eingabe)
    if zeile is None:
        raise nicht_da()
    geaendert = db.execute(
        update(GeraeteCode)
        .where(GeraeteCode.geraet_hash == zeile.geraet_hash, GeraeteCode.stand == "wartet")
        .values(stand="abgelehnt", bestaetigt_von=von.id)
    )
    db.commit()
    if geaendert.rowcount != 1:
        raise nicht_da()
    logger.info("%r denied a device pairing code (%r)", von.username, zeile.geraet)


def _abfrage_bremsen(pruefsumme: str) -> None:
    jetzt = _uhr()
    _merkposten_aufraeumen(jetzt)
    zuletzt = _zuletzt_gefragt.get(pruefsumme)
    if zuletzt is not None and jetzt - zuletzt < INTERVALL - SPIELRAUM:
        # ⚠️ Der Merkposten bleibt beim alten Zeitpunkt. Sonst schoebe ein
        # Geraet, das zu schnell fragt, sein eigenes Fenster immer weiter
        # hinaus und kaeme nie mehr durch.
        raise GeraeteCodeFehler(
            "device_poll_too_fast",
            f"Bitte hoechstens alle {INTERVALL} Sekunden nachfragen.",
            429,
            warten=INTERVALL,
        )
    _zuletzt_gefragt[pruefsumme] = jetzt


def abfragen(db: Session, geraete_code: str | None) -> Abfrage:
    """Das Geraet fragt nach. Bringt nach der Bestaetigung **einmal** den Schluessel.

    ⚠️ Unbekannt, abgelaufen und schon abgeholt sehen gleich aus:
    ``expired``. Ein Geraet kann damit nur eines tun, einen neuen Code holen.
    """
    if not geraete_code:
        return Abfrage("expired")
    pruefsumme = _pruefsumme(geraete_code)
    zeile = db.get(GeraeteCode, pruefsumme)
    if zeile is None:
        return Abfrage("expired")
    _abfrage_bremsen(pruefsumme)

    if zeile.stand == "abgelehnt":
        _zuletzt_gefragt.pop(pruefsumme, None)
        return Abfrage("denied")
    if zeile.stand not in ("wartet", "bestaetigt") or _abgelaufen(zeile, utcnow()):
        _zuletzt_gefragt.pop(pruefsumme, None)
        return Abfrage("expired")
    if zeile.stand == "wartet":
        return Abfrage("pending")

    konto = db.get(User, zeile.user_id) if zeile.user_id else None
    bestaetiger = db.get(User, zeile.bestaetigt_von) if zeile.bestaetigt_von else None
    # Wer bestaetigt hat, muss es noch duerfen: Ein Elternteil, das inzwischen
    # stillgelegt ist, bringt dem Fernseher seines Kindes nichts mehr.
    if konto is None or not konto.is_active or bestaetiger is None or not bestaetiger.is_active:
        _zuletzt_gefragt.pop(pruefsumme, None)
        return Abfrage("expired")

    # ⚠️ Erst beanspruchen, dann den Schluessel machen - wie ``app_kopplung.abholen``.
    beansprucht = db.execute(
        update(GeraeteCode)
        .where(GeraeteCode.geraet_hash == pruefsumme, GeraeteCode.stand == "bestaetigt")
        .values(stand="abholend")
    )
    db.commit()
    if beansprucht.rowcount != 1:
        return Abfrage("expired")
    _zuletzt_gefragt.pop(pruefsumme, None)

    try:
        eintrag, klartext = api_schluessel.anlegen(
            db,
            konto,
            name=geraetename(zeile.geraet),
            kindergeraet=konto.role == Role.child,
        )
    except api_schluessel.SchluesselFehler as fehler:
        # Kein Schluessel (zum Beispiel schon 20 am Konto): Der Code ist
        # trotzdem verbraucht. Ein neuer ist am Fernseher schnell geholt.
        db.refresh(zeile)
        zeile.stand = "abgeholt"
        db.commit()
        raise GeraeteCodeFehler(fehler.code, fehler.text, 409) from fehler

    db.refresh(zeile)
    zeile.stand = "abgeholt"
    zeile.api_key_id = eintrag.id
    db.commit()
    logger.info("A device fetched key %d for %r through a pairing code", eintrag.id, konto.username)
    return Abfrage("approved", schluessel=klartext, konto=konto)
