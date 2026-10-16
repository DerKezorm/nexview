"""Die iPhone-App koppeln: ein QR-Code im Profil statt eines abgetippten Schluessels.

Ablauf, wie bei nexcrate (S1):

1. Ein angemeldeter Mensch legt im Browser eine Kopplung an (``vorbereiten``).
   Kennung und Geheimnis gehen in einen QR-Code, zusammen mit der Adresse, unter
   der er nexview gerade sieht.
2. Die App scannt ihn und holt mit Kennung und Geheimnis **einmal** ihren
   Schluessel ab (``abholen``). Erst jetzt entsteht er, unter dem Namen des
   Geraets, und steht danach in der Liste der Schluessel wie jeder andere.
3. Der Browser fragt nach, bis der Code abgeholt ist (``stand``), und kann ihn
   vorher zurueckziehen.

⚠️ **Der Schluessel gehoert dem Konto, fuer das gekoppelt wurde.** Bei
Erwachsenen ist das das eigene; beim Kinderkonto erzeugt das Elternteil den
Code, der Schluessel gehoert dem Kind und kann damit nur, was das Kind kann.
Wer einen Code fuer welches Kind erzeugen darf, prueft der Router
(``children.lade_eigenes_kind``), nicht dieser Dienst.

⚠️ **Gleiche Antwort fuer eine unbekannte Kennung und ein falsches Geheimnis.**
Sonst liesse sich ausprobieren, welche Kennungen es gibt.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..models import ApiKey, AppKopplung, Role, User, utcnow
from . import api_schluessel

logger = logging.getLogger(__name__)

#: So lange gilt ein Code. Lang genug, um zum Telefon zu greifen, kurz genug,
#: dass ein Bildschirmfoto davon bald wertlos ist.
LEBENSDAUER = timedelta(minutes=10)

#: Hoechstens so viele offene Codes je Konto, das sie anlegt.
OFFEN_HOECHSTENS = 5

#: Laenge des Gerätenamens, wie ``ApiKey.name``.
GERAET_HOECHSTENS = 80


class KopplungFehler(Exception):
    """Traegt eine Kennung, damit die Oberflaeche den richtigen Satz zeigt."""

    def __init__(self, code: str, text: str, status: int) -> None:
        super().__init__(text)
        self.code = code
        self.text = text
        self.status = status


@dataclass(frozen=True)
class Vorbereitet:
    kennung: str
    #: Nur in dieser einen Antwort im Klartext - danach gibt es die Pruefsumme.
    geheimnis: str
    gilt_bis: datetime


@dataclass(frozen=True)
class Abgeholt:
    schluessel: str
    konto: User


def _pruefsumme(geheimnis: str) -> str:
    return hashlib.sha256(geheimnis.encode("utf-8")).hexdigest()


def _mit_zone(zeit: datetime) -> datetime:
    # SQLite gibt Zeitpunkte ohne Zeitzone zurueck.
    return zeit if zeit.tzinfo is not None else zeit.replace(tzinfo=UTC)


def _abgelaufen(zeile: AppKopplung, jetzt: datetime) -> bool:
    return _mit_zone(zeile.expires_at) < jetzt


def _aufraeumen(db: Session, jetzt: datetime) -> None:
    """Abgelaufene, nie abgeholte Codes verschwinden; abgeholte bleiben eine Weile.

    Abgeholte bleiben stehen, solange ihr Code gilt, damit der Browser, der
    gerade nachfragt, noch "verbunden" lesen kann statt "gibt es nicht".
    """
    grenze = jetzt.replace(tzinfo=None) if jetzt.tzinfo else jetzt
    db.execute(delete(AppKopplung).where(AppKopplung.expires_at < grenze - LEBENSDAUER))
    db.execute(
        delete(AppKopplung).where(
            AppKopplung.stand == "bereit", AppKopplung.expires_at < grenze
        )
    )


def vorbereiten(db: Session, *, fuer: User, von: User) -> Vorbereitet:
    """Einen Code anlegen, der den Schluessel fuer ``fuer`` bringt. Committet."""
    if not fuer.is_active:
        raise KopplungFehler("app_pairing_account_inactive", "Das Konto ist deaktiviert.", 409)
    jetzt = utcnow()
    _aufraeumen(db, jetzt)
    offen = db.scalar(
        select(func.count())
        .select_from(AppKopplung)
        .where(AppKopplung.angelegt_von == von.id, AppKopplung.stand == "bereit")
    )
    if (offen or 0) >= OFFEN_HOECHSTENS:
        raise KopplungFehler(
            "app_pairing_limit",
            f"Es sind schon {OFFEN_HOECHSTENS} Codes offen. Warte, bis sie ablaufen.",
            429,
        )
    geheimnis = secrets.token_urlsafe(32)
    zeile = AppKopplung(
        kennung=secrets.token_urlsafe(16),
        geheimnis_hash=_pruefsumme(geheimnis),
        user_id=fuer.id,
        angelegt_von=von.id,
        stand="bereit",
        created_at=jetzt,
        expires_at=jetzt + LEBENSDAUER,
    )
    db.add(zeile)
    db.commit()
    logger.info(
        "%r prepared an app pairing for %r",
        von.username,
        fuer.username,
    )
    return Vorbereitet(kennung=zeile.kennung, geheimnis=geheimnis, gilt_bis=zeile.expires_at)


def _eigene(db: Session, kennung: str, von: User) -> AppKopplung:
    zeile = db.get(AppKopplung, kennung[:32])
    # ⚠️ Nur wer den Code angelegt hat, sieht seinen Stand - eine fremde
    # Kennung antwortet wie eine, die es nicht gibt.
    if zeile is None or zeile.angelegt_von != von.id:
        raise KopplungFehler(
            "app_pairing_not_found", "Diesen Code gibt es nicht (mehr).", 404
        )
    return zeile


def stand(db: Session, kennung: str, von: User) -> tuple[str, str | None]:
    """``wartet``, ``verbunden`` (mit dem Namen des Geraets), ``fehlgeschlagen`` oder ``abgelaufen``.

    ⚠️ **"Verbunden" nur mit Schluessel.** Ein Code, beim dem das Anlegen des
    Schluessels scheiterte (schon 20 am Konto), ist verbraucht, aber verbunden
    ist nichts - der Browser zeigte sonst "Verbunden", waehrend die App mit
    leeren Haenden dasteht.

    Ein Code, der in ``abholend`` haengen blieb (ein Fehler zwischen
    Beanspruchen und Schluessel), ist nicht mehr abholbar und gilt deshalb als
    abgelaufen, statt einen toten QR-Code als wartend zu zeigen.
    """
    zeile = _eigene(db, kennung, von)
    if zeile.stand == "abgeholt":
        schluessel = db.get(ApiKey, zeile.api_key_id) if zeile.api_key_id else None
        if schluessel is None:
            return "fehlgeschlagen", None
        return "verbunden", schluessel.name
    if zeile.stand != "bereit" or _abgelaufen(zeile, utcnow()):
        return "abgelaufen", None
    return "wartet", None


def zurueckziehen(db: Session, kennung: str, von: User) -> None:
    """Den Code schliessen, bevor ihn jemand gescannt hat. Committet.

    Ein schon abgeholter bleibt stehen: Sein Schluessel wird im Profil
    widerrufen, nicht hier.
    """
    zeile = _eigene(db, kennung, von)
    if zeile.stand in ("bereit", "abholend"):
        db.delete(zeile)
        db.commit()
        logger.info("%r withdrew an app pairing before it was used", von.username)


def _geraetename(geraet: str, konto: User) -> str:
    sauber = " ".join(geraet.split())[:GERAET_HOECHSTENS]
    return sauber or ("iPad" if konto.role == Role.child else "iPhone")


def abholen(db: Session, kennung: str, geheimnis: str | None, geraet: str) -> Abgeholt:
    """Die App holt ihren Schluessel - genau einmal. Committet.

    ⚠️ **Unbekannt, falsches Geheimnis, schon abgeholt und abgelaufen sehen von
    aussen gleich aus**: 404 ``pairing_not_found``. Wer einen Code nicht hat,
    soll nichts ueber ihn erfahren - auch nicht, dass es ihn einmal gab.
    """
    nicht_da = KopplungFehler("pairing_not_found", "Diesen Code gibt es nicht (mehr).", 404)
    zeile = db.get(AppKopplung, kennung[:32])
    if (
        zeile is None
        or not geheimnis
        or not secrets.compare_digest(zeile.geheimnis_hash, _pruefsumme(geheimnis))
    ):
        raise nicht_da
    if zeile.stand != "bereit" or _abgelaufen(zeile, utcnow()):
        raise nicht_da
    konto = db.get(User, zeile.user_id)
    if konto is None or not konto.is_active:
        raise nicht_da
    # Wer den Code erzeugt hat, muss es noch duerfen: Ein Elternteil, das
    # inzwischen stillgelegt ist, bringt dem iPad seines Kindes nichts mehr.
    ersteller = db.get(User, zeile.angelegt_von)
    if ersteller is None or not ersteller.is_active:
        raise nicht_da

    # ⚠️ **Erst den Code fuer sich beanspruchen, dann den Schluessel machen.**
    # Scannen zwei Geraete denselben Code im selben Augenblick, laesen beide
    # oben "bereit" und bekaemen beide einen Schluessel. Die bedingte
    # Aenderung laesst genau eines durch.
    beansprucht = db.execute(
        update(AppKopplung)
        .where(AppKopplung.kennung == zeile.kennung, AppKopplung.stand == "bereit")
        .values(stand="abholend")
    )
    db.commit()
    if beansprucht.rowcount != 1:
        raise nicht_da

    try:
        eintrag, klartext = api_schluessel.anlegen(
            db,
            konto,
            name=_geraetename(geraet, konto),
            kindergeraet=konto.role == Role.child,
        )
    except api_schluessel.SchluesselFehler as fehler:
        # Kein Schluessel entstanden (zum Beispiel schon 20 am Konto): Der Code
        # ist trotzdem verbraucht. Ein zweiter Versuch mit demselben Code
        # scheiterte genauso, und ein neuer ist im Profil schnell gemacht.
        zeile.stand = "abgeholt"
        db.commit()
        raise KopplungFehler(fehler.code, fehler.text, 409) from fehler

    # ``anlegen`` hat committet; die Kopplung ist danach verbraucht.
    db.refresh(zeile)
    zeile.stand = "abgeholt"
    zeile.api_key_id = eintrag.id
    db.commit()
    logger.info(
        "The app fetched key %d for %r through a prepared pairing", eintrag.id, konto.username
    )
    return Abgeholt(schluessel=klartext, konto=konto)


def schluessel_des_kindes(db: Session, kind: User) -> list[ApiKey]:
    """Die Geraete eines Kindes - fuer die Liste bei den Eltern."""
    return list(
        db.scalars(
            select(ApiKey).where(ApiKey.user_id == kind.id).order_by(ApiKey.created_at.desc())
        )
    )
