"""E-Mail-Versand ueber einen eigenen SMTP-Server.

Bewusst mit der Standardbibliothek (``smtplib``) statt einer zusaetzlichen
Abhaengigkeit - das haelt das Docker-Abbild klein. Da ``smtplib`` blockiert,
laeuft der Versand in einem Hintergrund-Thread; sonst stuende der ganze Server
still, solange ein langsamer Mailserver antwortet.
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

logger = logging.getLogger("nexview.mail")

# Laenger zu warten hilft niemandem - die Oberflaeche soll antworten.
TIMEOUT_SECONDS = 15

# Absichtlich nachsichtig: die endgueltige Pruefung macht der Mailserver.
# Hier geht es nur darum, offensichtliche Tippfehler frueh zu melden.
ADDRESS_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SECURITY_MODES = ("none", "starttls", "ssl")


class MailError(Exception):
    """Versand nicht moeglich - mit einer Meldung, die weiterhilft."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    security: str  # "none" | "starttls" | "ssl"
    username: str
    password: str
    from_address: str
    from_name: str

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_address)


def valid_address(address: str) -> bool:
    return bool(ADDRESS_PATTERN.match(address.strip()))


def _connect(config: MailConfig) -> smtplib.SMTP:
    """Verbindung aufbauen und - falls Zugangsdaten hinterlegt sind - anmelden.

    Die Zertifikatspruefung bleibt eingeschaltet. Ein selbst ausgestelltes
    Zertifikat faellt dadurch auf, statt still eine unverschluesselte
    Verbindung vorzutaeuschen.
    """
    kontext = ssl.create_default_context()

    try:
        if config.security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                config.host, config.port, timeout=TIMEOUT_SECONDS, context=kontext
            )
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=TIMEOUT_SECONDS)
            if config.security == "starttls":
                server.starttls(context=kontext)
                server.ehlo()
    except (OSError, smtplib.SMTPException) as fehler:
        raise MailError(_lesbar(fehler, config)) from fehler

    if config.username:
        try:
            server.login(config.username, config.password)
        except smtplib.SMTPException as fehler:
            server.close()
            raise MailError(
                "Anmeldung am Mailserver fehlgeschlagen. Stimmen Benutzername und Passwort?"
            ) from fehler

    return server


def _lesbar(fehler: Exception, config: MailConfig) -> str:
    """Technische Fehler in etwas uebersetzen, mit dem man etwas anfangen kann."""
    if isinstance(fehler, ssl.SSLError):
        return (
            "Die verschlüsselte Verbindung kam nicht zustande. Passt die Einstellung "
            "zur Verschlüsselung zum Port? (587 meist STARTTLS, 465 meist SSL)"
        )
    if isinstance(fehler, TimeoutError):
        return f"{config.host}:{config.port} hat nicht rechtzeitig geantwortet."
    if isinstance(fehler, ConnectionRefusedError):
        return f"{config.host}:{config.port} nimmt keine Verbindung an. Stimmt der Port?"
    if isinstance(fehler, OSError) and "getaddrinfo" in str(fehler):
        return f"Der Servername {config.host!r} ist nicht auflösbar."
    return f"Verbindung zum Mailserver nicht möglich: {fehler}"


def _pruefe(config: MailConfig) -> None:
    server = _connect(config)
    try:
        server.noop()
    finally:
        server.quit()


def _sende(config: MailConfig, nachricht: EmailMessage) -> None:
    server = _connect(config)
    try:
        server.send_message(nachricht)
    except smtplib.SMTPRecipientsRefused as fehler:
        raise MailError("Der Mailserver hat den Empfänger abgelehnt.") from fehler
    except smtplib.SMTPSenderRefused as fehler:
        raise MailError(
            "Der Mailserver hat die Absenderadresse abgelehnt. Viele Anbieter verlangen, "
            "dass sie zum angemeldeten Konto gehört."
        ) from fehler
    except smtplib.SMTPException as fehler:
        raise MailError(f"Der Versand wurde abgelehnt: {fehler}") from fehler
    finally:
        server.quit()


async def verify(config: MailConfig) -> None:
    """Verbindung und Anmeldung pruefen, ohne eine Mail zu verschicken."""
    if not config.host:
        raise MailError("Es ist noch kein Mailserver hinterlegt.")
    await asyncio.to_thread(_pruefe, config)


async def send(config: MailConfig, to: str, subject: str, html: str, text: str) -> None:
    """Eine Nachricht verschicken - immer mit Text- *und* HTML-Fassung.

    Reines HTML landet bei manchen Anbietern eher im Spam, und Textleser sehen
    sonst gar nichts.
    """
    if not config.configured:
        raise MailError("Mailserver und Absenderadresse müssen zuerst eingetragen werden.")
    if not valid_address(to):
        raise MailError("Das ist keine gültige E-Mail-Adresse.")

    nachricht = EmailMessage()
    nachricht["Subject"] = subject
    nachricht["From"] = formataddr((config.from_name or "Nexview", config.from_address))
    nachricht["To"] = to.strip()
    # Eigene Message-ID mit der Absender-Domain: ohne die vergeben manche
    # Server eine eigene, was Spamfilter misstrauisch macht.
    nachricht["Message-ID"] = make_msgid(domain=config.from_address.split("@")[-1])
    nachricht["Auto-Submitted"] = "auto-generated"
    nachricht.set_content(text)
    nachricht.add_alternative(html, subtype="html")

    await asyncio.to_thread(_sende, config, nachricht)
    logger.info("Test mail sent to %s via %s", to.strip(), config.host)
