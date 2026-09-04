"""Der monatliche Aufraeum-Bericht per Mail.

Eine Liste, die man aufrufen muss, ruft niemand auf. Der Bericht ist der
Anstoss: einmal im Monat, an alle, die ihn haben wollen.

**Wer was bekommt.** Der Administrator die ganze Bibliothek **samt
Hausbestand** - der ist auf einer gewachsenen Anlage der Hauptfall. Alle
anderen nur, was ihnen zugerechnet ist; abgeben kann man schliesslich nur, was
einem gehoert.

⚠️ **Opt-in, und das ist keine Hoeflichkeit.** Ein ungefragter monatlicher
Brief ueber das, was man angeblich nicht mehr guckt, ist genau die Sorte Post,
die man wegfiltert - und danach auch alles andere von diesem Absender.

⚠️ **Leere Berichte gehen nicht hinaus.** Bei den meisten Konten wird die
eigene Liste dauerhaft leer sein: Wer Nexview auf eine bestehende Bibliothek
setzt, hat alles im Hausbestand, und zugerechnet wird nur, was danach
bestellt wurde. "Du hast nichts herumliegen" jeden Monat waere eine
Abmeldung mit Ansage.

⚠️ **Und der Stempel am Konto ist der wichtigste Teil.** Ohne ihn schickt ein
Container, der am Ersten fuenfmal neu startet, fuenf Berichte. Das ist der
klassische Fehler bei terminierten Mails, und er faellt nicht hier auf,
sondern beim Empfaenger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Role, User, utcnow
from . import aufraeumen, mail, mail_templates, meldungsziele
from .accounts import _mail_config
from .settings_service import AppSettings

logger = logging.getLogger("nexview.aufraeum_bericht")

# So viele Titel stehen im Bericht. Mehr liest niemand, und die Mail soll ein
# Anstoss sein, kein Datenbankauszug - die vollstaendige Liste steht in der App.
ZEILEN_IM_BERICHT = 30


@dataclass(frozen=True)
class Versand:
    verschickt: int
    uebersprungen_leer: int


def _monat(wert: datetime | None) -> tuple[int, int] | None:
    return None if wert is None else (wert.year, wert.month)


def faellig(person: User, jetzt: datetime) -> bool:
    """Ist fuer dieses Konto in diesem Monat schon einer hinausgegangen?

    Verglichen wird der **Monat**, nicht der Tag. Stand der Server am Ersten
    still, geht der Bericht am Zweiten hinaus - spaeter ist besser als gar
    nicht, und ein Bericht, der einen ganzen Monat ausfaellt, weil der Rechner
    einen Tag aus war, waere die schlechtere Regel.
    """
    if not person.mail_cleanup or not person.email or not person.email_verified:
        return False
    return _monat(person.cleanup_mail_at) != (jetzt.year, jetzt.month)


def _groesse(bytes_: int, englisch: bool) -> str:
    """Kurz und lesbar - dieselbe Staffelung wie in der Oberflaeche."""
    gb = bytes_ / (1024**3)
    if gb >= 1024:
        wert = f"{gb / 1024:.2f}".rstrip("0").rstrip(".")
        return f"{wert} TiB" if englisch else f"{wert.replace('.', ',')} TiB"
    wert = f"{gb:.0f}"
    return f"{wert} GiB"


async def einen_schicken(db: Session, settings: AppSettings, person: User) -> bool:
    """Den Bericht fuer ein Konto bauen und schicken. ``False`` = nichts zu melden."""
    ist_admin = person.role == Role.admin
    ergebnis = aufraeumen.liste(
        db,
        nutzer=None if ist_admin else person,
        grenze=ZEILEN_IM_BERICHT,
    )
    if not ergebnis.kandidaten:
        return False

    englisch = (person.language or "de").startswith("en")

    # "Neu seit dem letzten Bericht" ist die einzige Zeile, die sich zwischen
    # zwei Monaten aendert - ohne sie liest man den zweiten Bericht nicht mehr.
    seit = person.cleanup_mail_at
    neu = (
        sum(1 for k in ergebnis.kandidaten if k.liegt_seit and k.liegt_seit > seit)
        if seit
        else 0
    )

    zeilen = []
    for k in ergebnis.kandidaten:
        art = (
            (f"Season {k.season}" if englisch else f"Staffel {k.season}")
            if k.season is not None
            else ("Movie" if englisch else "Film")
        )
        seit_text = (
            (f" · here since {k.liegt_seit:%b %Y}" if englisch else f" · liegt seit {k.liegt_seit:%m/%Y}")
            if k.liegt_seit
            else ""
        )
        zeilen.append((k.title, f"{art}{seit_text}", _groesse(k.size_bytes, englisch)))

    # ⚠️ ``meldungsziele.MEIN_SPEICHER`` statt eines abgeschriebenen Pfades:
    # Hier stand "?reiter=Speicherkontingent" - ein Wort, das die
    # Reitertabelle gar nicht kennt. Der Link fuehrte ins Profil-Menue
    # statt zum Speicher, und niemand hat es gemerkt.
    ziel = "admin/stats" if ist_admin else meldungsziele.MEIN_SPEICHER.lstrip("/")
    nachricht = mail_templates.aufraeum_mail(
        zeilen=zeilen,
        gesamt=ergebnis.gesamt_anzahl,
        gesamt_platz=_groesse(ergebnis.gesamt_bytes, englisch),
        neu=neu,
        link=settings.link(ziel),
        fuer_admin=ist_admin,
        sprache=person.language or "de",
    )
    await mail.send(
        _mail_config(settings),
        person.email,
        nachricht.subject,
        nachricht.html,
        nachricht.text,
    )
    return True


async def vielleicht_verschicken(db: Session, settings: AppSettings) -> Versand:
    """Alle faelligen Berichte hinausschicken - hoechstens einer je Monat und Konto.

    Ohne eingerichteten Mailserver oder ohne oeffentliche Adresse passiert
    nichts: Eine Mail ohne Absender geht nicht, und eine mit einem Knopf, der
    ins Leere fuehrt, waere schlimmer als keine.
    """
    if not settings.mail_configured or not settings.public_url:
        return Versand(0, 0)

    jetzt = utcnow().replace(tzinfo=None)
    empfaenger = [
        person
        for person in db.scalars(select(User).where(User.is_active.is_(True)))
        if faellig(person, jetzt)
    ]
    if not empfaenger:
        return Versand(0, 0)

    verschickt = leer = 0
    for person in empfaenger:
        try:
            if await einen_schicken(db, settings, person):
                verschickt += 1
            else:
                leer += 1
        except Exception:  # noqa: BLE001 - ein Empfaenger darf die anderen nicht mitnehmen
            logger.exception("Cleanup report for %r could not be sent", person.username)
            continue
        # ⚠️ **Der Stempel faellt auch bei leerer Liste.** Sonst versuchte
        # Nexview es bei jedem Durchgang des ganzen Monats erneut - jede
        # Stunde eine vollstaendige Auswertung fuer eine Mail, die ohnehin
        # nicht hinausgeht.
        person.cleanup_mail_at = jetzt

    db.commit()
    if verschickt or leer:
        logger.info(
            "Cleanup report: %d sent, %d skipped (nothing to report)", verschickt, leer
        )
    return Versand(verschickt, leer)
