"""E-Mail an eine allgemeine Adresse.

Nicht zu verwechseln mit den Mails, die jeder in seinem Profil einschaltet -
die gehen an eine Person und haengen an ihrem Konto. Hier geht es um Adressen
ohne Konto: ein Postfach, an dem mehrere arbeiten, ein Verteiler, oder eine
Adresse, die auf der anderen Seite eine Automatisierung ausloest.

Zwei Dinge sind deshalb anders als bei ntfy und Gotify:

* **Kein Bestaetigungscode.** Hinter der Adresse sitzt womoeglich niemand, der
  einen Code ablesen koennte, und ein Verteiler leitet ihn im Zweifel an alle
  weiter. Getestet wird trotzdem - nur eben ohne Nachweis, dass jemand
  hingeschaut hat.
* **Die Verbindung gehoert nicht zum Ziel.** Mailserver und Absender stehen
  einmal in den Mail-Einstellungen; hier steht nur, an wen es geht.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import ChannelKind
from .. import mail, mail_templates
from .base import ChannelError, Notice

KIND = ChannelKind.email
LABEL = "E-Mail"

# Nur eine Ebene: Eine Adresse ist schon das Postfach.
PARENT_FIELDS = ("address", "subject", "language")
CHILD_FIELDS: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
SECRETS: tuple[str, ...] = ()

# Was aus den Mail-Einstellungen dazukommt. Der Zugang zum Mailserver ist eine
# Eigenschaft der Installation, nicht der einzelnen Adresse.
GLOBAL_FIELDS = (
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_username",
    "smtp_password",
    "smtp_from_address",
    "smtp_from_name",
)

# Kein Code: siehe oben.
REQUIRES_CODE = False

# E-Mail kennt keine Dringlichkeit - eine Mail ist eine Mail. Die Stufe wird
# trotzdem gespeichert, damit die Oberflaeche fuer alle Dienste dieselbe
# bleibt; hier hat sie schlicht keine Wirkung.


# Platzhalter im Betreff. Bewusst genau einer: Wer eine Automatisierung auf
# den Betreff ansetzt, will einen festen Text; wer selbst hineinschaut, will
# wissen, worum es geht. Beides deckt ein einzelner Platzhalter ab.
TITEL_PLATZHALTER = "{titel}"


@dataclass(frozen=True)
class EmailConfig:
    address: str
    subject: str
    language: str
    server: mail.MailConfig

    @property
    def configured(self) -> bool:
        return bool(self.address and self.server.configured)


def build(werte: dict[str, str]) -> EmailConfig | None:
    """Aus Adresse und Mail-Einstellungen den Zugang bauen."""
    try:
        port = int(werte.get("smtp_port") or 587)
    except (TypeError, ValueError):
        port = 587
    sicherheit = werte.get("smtp_security") or "starttls"

    config = EmailConfig(
        address=werte.get("address", "").strip(),
        subject=werte.get("subject", "").strip(),
        language=werte.get("language") if werte.get("language") in ("de", "en") else "de",
        server=mail.MailConfig(
            host=werte.get("smtp_host", "").strip(),
            port=port,
            security=sicherheit if sicherheit in mail.SECURITY_MODES else "starttls",
            username=werte.get("smtp_username", "").strip(),
            password=werte.get("smtp_password", ""),
            from_address=werte.get("smtp_from_address", "").strip(),
            from_name=werte.get("smtp_from_name", "").strip() or "Nexview",
        ),
    )
    return config if config.configured else None


def betreff(config: EmailConfig, titel: str) -> str | None:
    """Der eingestellte Betreff - ``None``, wenn die Vorlage gelten soll.

    Ein leeres Feld heisst "nimm den ueblichen". Steht etwas drin, gilt genau
    das; ``{titel}`` setzt die Meldung ein.
    """
    if not config.subject:
        return None
    return config.subject.replace(TITEL_PLATZHALTER, titel)


async def check(config: EmailConfig) -> None:
    """Nimmt der Mailserver Verbindung und Anmeldung an?"""
    try:
        await mail.verify(config.server)
    except mail.MailError as fehler:
        raise ChannelError(fehler.message) from fehler


async def send(config: EmailConfig, notice: Notice) -> None:
    if not mail.valid_address(config.address):
        raise ChannelError(f"{config.address!r} ist keine gültige E-Mail-Adresse.")

    nachricht = mail_templates.system_notice_mail(
        titel=notice.title,
        rumpf=notice.body,
        link=notice.click_url,
        sprache=config.language,
        betreff=betreff(config, notice.title),
    )
    try:
        await mail.send(
            config.server,
            config.address,
            nachricht.subject,
            nachricht.html,
            nachricht.text,
        )
    except mail.MailError as fehler:
        raise ChannelError(fehler.message) from fehler
