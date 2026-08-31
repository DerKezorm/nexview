"""Persoenliche Zugriffs-Schluessel fuer die HTTP-Schnittstelle.

Die Entscheidungen dahinter stehen bei ``models.ApiKey``. Hier steht, wie sie
umgesetzt sind.

⚠️ **Der Klartext existiert genau einmal** - beim Anlegen, in der Antwort.
Danach gibt es nur noch die Pruefsumme. Wer den Schluessel verliert, legt einen
neuen an; wiederherstellen kann ihn niemand, auch der Administrator nicht.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ApiKey, Role, User, utcnow

logger = logging.getLogger(__name__)

#: ⚠️ **Das Praefix ist kein Schmuck.** Es unterscheidet einen Schluessel von
#: einem gewoehnlichen Sitzungs-Token, ohne dass jemand raten muss - und es
#: macht ihn fuer Werkzeuge erkennbar, die Quelltext nach versehentlich
#: veroeffentlichten Geheimnissen absuchen. Ein Schluessel, der in einem
#: oeffentlichen Verzeichnis landet, faellt damit auf, statt still zu wirken.
PRAEFIX = "nxv_"

#: 40 Zeichen aus ``token_urlsafe`` - deutlich mehr, als sich erraten laesst.
LAENGE = 40

#: So viele darf ein Konto haben. Nicht als Sicherheitsmassnahme, sondern
#: damit die Liste im Profil eine Liste bleibt.
HOECHSTZAHL = 20

#: ⚠️ ``last_used_at`` wird **nicht** bei jeder Anfrage geschrieben.
#:
#: Ein Dashboard, das alle zehn Sekunden fragt, erzeugte sonst 8.640
#: Schreibvorgaenge am Tag - fuer eine Angabe, die auf die Minute genau
#: niemanden interessiert. Geschrieben wird erst, wenn der letzte Eintrag
#: aelter ist als das hier.
NUTZUNG_MERKEN_AB = timedelta(minutes=15)


class SchluesselFehler(Exception):
    """Traegt eine Kennung, damit die Oberflaeche den richtigen Satz zeigt."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


def _pruefsumme(klartext: str) -> str:
    return hashlib.sha256(klartext.encode("utf-8")).hexdigest()


def anlegen(
    db: Session, user: User, *, name: str, nur_lesen: bool = False, tage: int | None = None
) -> tuple[ApiKey, str]:
    """Einen Schluessel erzeugen. Gibt den Eintrag **und** den Klartext zurueck.

    Der Klartext ist das einzige Mal hier zu haben - der Aufrufer muss ihn
    weiterreichen, sonst ist er fort.
    """
    # ⚠️ Kinderkonten bekommen keine. Sie sind Unterprofile ihrer Eltern; ein
    # Schluessel darauf waere ein Zugang zu einem Konto, das gar nicht fuer
    # eigenstaendige Nutzung gedacht ist.
    if user.role == Role.child:
        raise SchluesselFehler("apikey_not_for_children", "Kinderkonten bekommen keine Schluessel.")

    sauber = name.strip()
    if not sauber:
        raise SchluesselFehler("apikey_needs_name", "Ein Schluessel braucht einen Namen.")

    vorhanden = db.scalar(
        select(ApiKey).where(ApiKey.user_id == user.id).limit(1).offset(HOECHSTZAHL - 1)
    )
    if vorhanden is not None:
        raise SchluesselFehler(
            "apikey_too_many",
            f"Mehr als {HOECHSTZAHL} Schluessel je Konto sind nicht vorgesehen.",
        )

    klartext = PRAEFIX + secrets.token_urlsafe(LAENGE)
    eintrag = ApiKey(
        user_id=user.id,
        name=sauber[:80],
        token_hash=_pruefsumme(klartext),
        # Nur die ersten Zeichen nach dem Praefix - genug zum Wiedererkennen,
        # zu wenig zum Raten.
        vorschau=klartext[: len(PRAEFIX) + 6],
        nur_lesen=nur_lesen,
        expires_at=(utcnow() + timedelta(days=tage)) if tage else None,
    )
    db.add(eintrag)
    db.commit()
    db.refresh(eintrag)

    logger.info(
        "API key %r created for %r (%s)",
        eintrag.name,
        user.username,
        "read-only" if nur_lesen else "full rights of its owner",
    )
    return eintrag, klartext


def liste(db: Session, user: User) -> list[ApiKey]:
    return list(
        db.scalars(
            select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
        )
    )


def widerrufen(db: Session, user: User, schluessel_id: int) -> None:
    """Einen eigenen Schluessel entfernen.

    ⚠️ Die Bedingung auf ``user_id`` ist der eigentliche Schutz: Ohne sie
    koennte jeder mit einer geratenen Nummer fremde Schluessel widerrufen.
    """
    eintrag = db.scalar(
        select(ApiKey).where(ApiKey.id == schluessel_id, ApiKey.user_id == user.id)
    )
    if eintrag is None:
        raise SchluesselFehler("apikey_unknown", "Diesen Schluessel gibt es nicht.")

    db.delete(eintrag)
    db.commit()
    logger.info("API key %r of %r revoked", eintrag.name, user.username)


def alle_widerrufen(db: Session, user: User) -> int:
    """Alle Schluessel eines Kontos auf einen Schlag entfernen.

    ⚠️ **Gehoert zu "ueberall abmelden", nicht zum Passwortwechsel.**
    Ein Sitzungs-Token und ein Zugriffs-Schluessel sind zwei verschiedene
    Dinge: Die Sitzung ist ein Browser, der Schluessel ist eine Anbindung
    (eine Kachel auf dem Uebersichtsbrett, ein Skript). Wer sein Passwort
    wechselt, will meistens nur ein besseres Passwort - dabei jede Anbindung
    stumm sterben zu lassen waere eine Ueberraschung.

    "Ueberall abmelden" heisst dagegen ausdruecklich "jemand liest mit". Dann
    muss auch der Schluessel weg, sonst hat der Riegel ein Loch: Der
    Sitzungs-Weg prueft ``sitzung.gilt_noch``, der Schluessel-Weg nie - er
    kennt nur Pruefsumme, Ablauf und "Konto aktiv".

    Liefert, wie viele es waren, damit die Oberflaeche es sagen kann.
    """
    eintraege = list(db.scalars(select(ApiKey).where(ApiKey.user_id == user.id)))
    for eintrag in eintraege:
        db.delete(eintrag)
    db.commit()
    if eintraege:
        logger.info("All %d API key(s) of %r revoked", len(eintraege), user.username)
    return len(eintraege)


def sieht_aus_wie_schluessel(wert: str) -> bool:
    """Ist das ueberhaupt ein Schluessel - oder ein Sitzungs-Token?

    Billig und ohne Datenbank: Nur wenn das Praefix stimmt, wird nachgesehen.
    """
    return wert.startswith(PRAEFIX)


def einloesen(db: Session, klartext: str) -> ApiKey | None:
    """Den Schluessel nachschlagen und seine Nutzung vermerken.

    Gibt ``None`` zurueck, wenn er unbekannt, abgelaufen oder sein Konto
    stillgelegt ist - der Aufrufer macht daraus ein 401.
    """
    eintrag = db.scalar(select(ApiKey).where(ApiKey.token_hash == _pruefsumme(klartext)))
    if eintrag is None:
        return None

    if eintrag.expires_at is not None:
        faellig = eintrag.expires_at
        # SQLite gibt Zeitpunkte ohne Zeitzone zurueck - der Vergleich mit
        # einem zeitzonenbehafteten Wert wuerde sonst ``TypeError`` werfen.
        if faellig.tzinfo is None:
            faellig = faellig.replace(tzinfo=timezone.utc)
        if faellig <= datetime.now(timezone.utc):
            return None

    if eintrag.user is None or not eintrag.user.is_active:
        return None

    _nutzung_vermerken(db, eintrag)
    return eintrag


def _nutzung_vermerken(db: Session, eintrag: ApiKey) -> None:
    jetzt = utcnow()
    zuletzt = eintrag.last_used_at
    if zuletzt is not None and zuletzt.tzinfo is None:
        zuletzt = zuletzt.replace(tzinfo=timezone.utc)

    if zuletzt is not None and jetzt - zuletzt < NUTZUNG_MERKEN_AB:
        return

    eintrag.last_used_at = jetzt
    db.commit()


def darf(eintrag: ApiKey, methode: str) -> bool:
    """Darf dieser Schluessel eine Anfrage mit dieser Methode stellen?

    ⚠️ Die Regel haengt an der HTTP-Methode, nicht an einer Liste erlaubter
    Adressen. Das ist bei Nexview zulaessig, weil **kein einziger** GET-Pfad
    etwas veraendert - nachgemessen, nicht angenommen. Eine Liste muesste man
    dagegen bei jedem neuen Endpunkt pflegen, und wer das vergisst, macht ein
    Loch statt einer Sperre.
    """
    if not eintrag.nur_lesen:
        return True
    return methode.upper() in ("GET", "HEAD", "OPTIONS")
