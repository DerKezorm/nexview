"""Vorlagen fuer die E-Mails, die Nexview verschickt.

Bewusst schlichtes HTML: Tabellen statt Flexbox, alle Angaben direkt am
Element statt in einem Stylesheet. Mailprogramme koennen wenig davon, was ein
Browser kann - Outlook wirft ``<style>``-Bloecke teilweise sogar weg.
Ausserdem keine externen Bilder: die blockieren die meisten Programme, und das
Logo waere dann ein leeres Kaestchen.

Jede Vorlage liefert Betreff, HTML- und Textfassung. Ohne Textfassung landen
Nachrichten eher im Spam, und wer seine Mail als Text liest, saehe nichts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

HINTERGRUND = "#0b0b0f"
KARTE = "#16161d"
INNEN = "#101016"
RAHMEN = "#26262f"
AKZENT = "#e11d2f"
TEXT = "#f2f2f5"
GEDIMMT = "#9a9aa8"
LEISE = "#6f6f80"

SCHRIFT = "Segoe UI,Helvetica,Arial,sans-serif"


@dataclass(frozen=True)
class Mail:
    subject: str
    html: str
    text: str


def _ist_englisch(sprache: str) -> bool:
    return sprache.startswith("en")


def _zeitpunkt(englisch: bool) -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M" if englisch else "%d.%m.%Y, %H:%M")


def _rahmen(englisch: bool, *, ueberschrift: str, unterzeile: str, inhalt: str) -> str:
    """Gemeinsames Geruest aller Nachrichten."""
    marke = "Media requests for your household" if englisch else "Medienanfragen für deinen Haushalt"
    fuss = (
        f"Sent automatically on {_zeitpunkt(englisch)} · Nexview"
        if englisch
        else f"Automatisch gesendet am {_zeitpunkt(englisch)} · Nexview"
    )

    return f"""\
<!doctype html>
<html lang="{'en' if englisch else 'de'}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background-color:{HINTERGRUND};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:{HINTERGRUND};padding:32px 12px;">
  <tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="max-width:520px;background-color:{KARTE};border:1px solid {RAHMEN};
                  border-radius:16px;overflow:hidden;">

      <tr><td style="height:4px;background-color:{AKZENT};font-size:0;line-height:0;">&nbsp;</td></tr>

      <tr><td style="padding:32px 32px 8px 32px;text-align:center;">
        <div style="font-family:{SCHRIFT};font-size:26px;font-weight:700;
                    letter-spacing:1px;color:{TEXT};">
          NEX<span style="color:{AKZENT};">VIEW</span>
        </div>
        <div style="font-family:{SCHRIFT};font-size:12px;color:{GEDIMMT};
                    padding-top:6px;">{marke}</div>
      </td></tr>

      <tr><td style="padding:24px 32px 0 32px;text-align:center;">
        <div style="font-family:{SCHRIFT};font-size:22px;font-weight:700;
                    color:{TEXT};">{ueberschrift}</div>
        <div style="font-family:{SCHRIFT};font-size:15px;color:{GEDIMMT};
                    padding-top:8px;">{unterzeile}</div>
      </td></tr>

      {inhalt}

      <tr><td style="padding:0 32px 28px 32px;text-align:center;font-family:{SCHRIFT};
                     font-size:12px;color:{LEISE};">{fuss}</td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""


def _knopf(text: str, link: str) -> str:
    """Grosser Knopf plus die Adresse im Klartext.

    Manche Mailprogramme zeigen Knoepfe nicht als solche an - dann bleibt der
    Link darunter zum Kopieren.
    """
    return f"""\
      <tr><td style="padding:28px 32px 8px 32px;text-align:center;">
        <a href="{link}"
           style="display:inline-block;background-color:{AKZENT};color:#ffffff;
                  font-family:{SCHRIFT};font-size:15px;font-weight:600;
                  text-decoration:none;padding:14px 28px;border-radius:999px;">{text}</a>
      </td></tr>
      <tr><td style="padding:4px 32px 24px 32px;text-align:center;font-family:{SCHRIFT};
                     font-size:12px;color:{LEISE};word-break:break-all;">{link}</td></tr>"""


def _kasten(text: str) -> str:
    return f"""\
      <tr><td style="padding:24px 32px 0 32px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="background-color:{INNEN};border:1px solid {RAHMEN};border-radius:12px;">
          <tr><td style="padding:16px 20px;font-family:{SCHRIFT};font-size:14px;
                         line-height:22px;color:{GEDIMMT};">{text}</td></tr>
        </table>
      </td></tr>"""


def _fuss_text(englisch: bool) -> str:
    return (
        f"--\nSent automatically on {_zeitpunkt(englisch)} · Nexview"
        if englisch
        else f"--\nAutomatisch gesendet am {_zeitpunkt(englisch)} · Nexview"
    )


def test_mail(sprache: str = "de") -> tuple[str, str, str]:
    """Testnachricht - beweist nur, dass der Versand funktioniert."""
    englisch = _ist_englisch(sprache)
    if englisch:
        betreff, kopf, unter = "Nexview: test message", "It works.", (
            "Your mail server is set up correctly."
        )
        rumpf = (
            "Nexview was able to reach your SMTP server and deliver this message. "
            "Nothing else to do."
        )
    else:
        betreff, kopf, unter = "Nexview: Testnachricht", "Es funktioniert.", (
            "Dein Mailserver ist richtig eingerichtet."
        )
        rumpf = (
            "Nexview konnte deinen SMTP-Server erreichen und diese Nachricht zustellen. "
            "Mehr ist nicht zu tun."
        )

    html = _rahmen(englisch, ueberschrift=kopf, unterzeile=unter, inhalt=_kasten(rumpf))
    text = f"{kopf}\n\n{unter}\n\n{rumpf}\n\n{_fuss_text(englisch)}"
    return betreff, html, text



def invitation_mail(link: str, sprache: str = "de") -> Mail:
    """Einladung - es gibt noch kein Konto, die Person legt es selbst an."""
    englisch = _ist_englisch(sprache)
    if englisch:
        betreff = "You have been invited to Nexview"
        kopf, unter = "You are invited.", "Discover films and series – and request them."
        rumpf = (
            "Follow the link to set up your account: username, name and password. "
            "The invitation is valid for 7 days."
        )
        knopf = "Set up account"
    else:
        betreff = "Du wurdest zu Nexview eingeladen"
        kopf, unter = "Du bist eingeladen.", "Filme und Serien entdecken – und anfragen."
        rumpf = (
            "Über den Link richtest du dein Konto ein: Benutzername, Name und Passwort. "
            "Die Einladung gilt 7 Tage."
        )
        knopf = "Konto einrichten"

    html = _rahmen(
        englisch, ueberschrift=kopf, unterzeile=unter, inhalt=_kasten(rumpf) + _knopf(knopf, link)
    )
    text = f"{kopf}\n\n{unter}\n\n{rumpf}\n\n{link}\n\n{_fuss_text(englisch)}"
    return Mail(betreff, html, text)


def verification_mail(link: str, sprache: str = "de") -> Mail:
    """Adresse bestaetigen."""
    englisch = _ist_englisch(sprache)
    if englisch:
        betreff = "Nexview: please confirm your address"
        kopf, unter = "One click to go.", "Confirm this address to use Nexview fully."
        rumpf = (
            "Until you confirm, you cannot request any titles. The link is valid for 24 hours. "
            "If you did not expect this message, simply ignore it."
        )
        knopf = "Confirm address"
    else:
        betreff = "Nexview: Bitte bestätige deine Adresse"
        kopf, unter = "Nur noch ein Klick.", "Bestätige diese Adresse, um Nexview voll zu nutzen."
        rumpf = (
            "Solange sie nicht bestätigt ist, kannst du keine Titel anfragen. Der Link gilt "
            "24 Stunden. Falls du diese Nachricht nicht erwartet hast, ignoriere sie einfach."
        )
        knopf = "Adresse bestätigen"

    html = _rahmen(
        englisch, ueberschrift=kopf, unterzeile=unter, inhalt=_kasten(rumpf) + _knopf(knopf, link)
    )
    text = f"{kopf}\n\n{unter}\n\n{rumpf}\n\n{link}\n\n{_fuss_text(englisch)}"
    return Mail(betreff, html, text)


def reset_mail(link: str, sprache: str = "de") -> Mail:
    """Passwort zuruecksetzen."""
    englisch = _ist_englisch(sprache)
    if englisch:
        betreff = "Nexview: reset your password"
        kopf, unter = "Forgot your password?", "Choose a new one with the button below."
        rumpf = (
            "The link is valid for one hour and works only once. If you did not ask for this, "
            "you can ignore this message – your password stays as it is."
        )
        knopf = "Choose new password"
    else:
        betreff = "Nexview: Passwort zurücksetzen"
        kopf, unter = "Passwort vergessen?", "Wähle über den Knopf ein neues."
        rumpf = (
            "Der Link gilt eine Stunde und funktioniert nur einmal. Falls du das nicht "
            "angefordert hast, ignoriere diese Nachricht – dein Passwort bleibt unverändert."
        )
        knopf = "Neues Passwort wählen"

    html = _rahmen(
        englisch, ueberschrift=kopf, unterzeile=unter, inhalt=_kasten(rumpf) + _knopf(knopf, link)
    )
    text = f"{kopf}\n\n{unter}\n\n{rumpf}\n\n{link}\n\n{_fuss_text(englisch)}"
    return Mail(betreff, html, text)


def address_changed_mail(neue_adresse: str, sprache: str = "de") -> Mail:
    """Warnung an die *alte* Adresse - die uebliche Absicherung."""
    englisch = _ist_englisch(sprache)
    if englisch:
        betreff = "Nexview: your address was changed"
        kopf, unter = "Address changed.", f"Your Nexview account now uses {neue_adresse}."
        rumpf = (
            "If that was you, nothing else is needed. If not, contact your administrator "
            "right away – someone else may have access to your account."
        )
    else:
        betreff = "Nexview: Deine Adresse wurde geändert"
        kopf, unter = "Adresse geändert.", f"Dein Nexview-Konto nutzt jetzt {neue_adresse}."
        rumpf = (
            "Warst du das, ist nichts weiter zu tun. Falls nicht, wende dich sofort an deinen "
            "Administrator – möglicherweise hat jemand anderes Zugriff auf dein Konto."
        )

    html = _rahmen(englisch, ueberschrift=kopf, unterzeile=unter, inhalt=_kasten(rumpf))
    text = f"{kopf}\n\n{unter}\n\n{rumpf}\n\n{_fuss_text(englisch)}"
    return Mail(betreff, html, text)
