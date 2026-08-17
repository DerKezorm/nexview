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
from html import escape

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


# --- Benachrichtigungen zu Anfragen und Downloads ----------------------------
#
# Diese Nachrichten verschickt Nexview nur, wenn der Empfaenger sie im Profil
# ausdruecklich eingeschaltet hat. Deshalb steht in jeder ein Hinweis darauf,
# wo man sie wieder abstellt - sonst sucht man sich zu Recht einen Wolf.


def _abbestellen(englisch: bool, profil_link: str | None) -> str:
    if not profil_link:
        return ""
    text = (
        "You switched this notification on yourself. You can turn it off in your profile."
        if englisch
        else "Diese Benachrichtigung hast du selbst eingeschaltet. Im Profil kannst du sie "
        "wieder abstellen."
    )
    return f"""\
      <tr><td style="padding:20px 32px 0 32px;font-family:{SCHRIFT};font-size:12px;
                     line-height:19px;color:{LEISE};">
        {text} <a href="{profil_link}" style="color:{LEISE};">{profil_link}</a>
      </td></tr>"""


def _abbestellen_text(englisch: bool, profil_link: str | None) -> str:
    if not profil_link:
        return ""
    text = (
        "You switched this notification on yourself. Turn it off in your profile:"
        if englisch
        else "Diese Benachrichtigung hast du selbst eingeschaltet. Abstellen im Profil:"
    )
    return f"\n\n{text}\n{profil_link}"


def _sicher(wert: str) -> str:
    """Fremden Text fuer die Verwendung in HTML entschaerfen.

    Titel kommen von TMDB, Anzeigenamen und Kommentare von den Nutzern selbst.
    Ein "<" darin wuerde die Nachricht sonst ab dieser Stelle zerlegen - und
    ein absichtlich gesetztes Stueck HTML koennte die Mail umschreiben.
    Der Betreff und die Textfassung brauchen das nicht, dort ist "<" harmlos.
    """
    return escape(wert, quote=False)


def _benachrichtigung(
    *,
    englisch: bool,
    betreff: str,
    kopf: str,
    unter: str,
    rumpf: str,
    rumpf_html: str | None = None,
    knopf: str | None = None,
    link: str | None = None,
    profil_link: str | None = None,
) -> Mail:
    """Gemeinsames Geruest der Benachrichtigungen.

    ``rumpf`` ist **Klartext** und wird fuer die HTML-Fassung selbsttaetig
    entschaerft. Das ist Absicht: in diesen Nachrichten stecken Filmtitel,
    Anzeigenamen und Kommentare, also durchweg Text, den jemand anderes
    bestimmt hat. Waere HTML die Voreinstellung, muesste man an jeder einzelnen
    Vorlage daran denken - und beim naechsten Zusatz wieder.

    Nur wer wirklich Auszeichnung braucht, gibt zusaetzlich ``rumpf_html`` an
    und ist dann selbst fuers Entschaerfen zustaendig.
    """
    inhalt = _kasten(rumpf_html if rumpf_html is not None else _sicher(rumpf))
    if knopf and link:
        inhalt += _knopf(knopf, link)
    inhalt += _abbestellen(englisch, profil_link)

    html = _rahmen(englisch, ueberschrift=_sicher(kopf), unterzeile=_sicher(unter), inhalt=inhalt)
    text = f"{kopf}\n\n{unter}\n\n{rumpf}"
    if link:
        text += f"\n\n{link}"
    text += _abbestellen_text(englisch, profil_link)
    text += f"\n\n{_fuss_text(englisch)}"
    return Mail(betreff, html, text)


def download_ready_mail(
    titel: str, sprache: str = "de", *, link: str | None = None, profil_link: str | None = None
) -> Mail:
    """An den Anfragenden: sein Titel ist fertig geladen."""
    englisch = _ist_englisch(sprache)
    if englisch:
        return _benachrichtigung(
            englisch=englisch,
            betreff=f"Ready to watch: {titel}",
            kopf="It is ready.",
            unter=f"“{titel}” has finished downloading.",
            rumpf=(
                f"“{titel}” is now in your library and ready to play. If the quality is off, "
                "you can rate it in Nexview – that is how your administrator finds out."
            ),
            knopf="Open in Nexview",
            link=link,
            profil_link=profil_link,
        )
    return _benachrichtigung(
        englisch=englisch,
        betreff=f"Fertig geladen: {titel}",
        kopf="Es ist da.",
        unter=f"„{titel}“ wurde fertig geladen.",
        rumpf=(
            f"„{titel}“ liegt jetzt in der Bibliothek und lässt sich abspielen. Stimmt die "
            "Qualität nicht, kannst du das in Nexview bewerten – nur so erfährt es dein "
            "Administrator."
        ),
        knopf="In Nexview ansehen",
        link=link,
        profil_link=profil_link,
    )


def request_pending_mail(
    titel: str,
    anfragender: str,
    sprache: str = "de",
    *,
    link: str | None = None,
    profil_link: str | None = None,
) -> Mail:
    """An Admins und Entscheider: eine Anfrage wartet auf Freigabe."""
    englisch = _ist_englisch(sprache)
    if englisch:
        return _benachrichtigung(
            englisch=englisch,
            betreff=f"Waiting for approval: {titel}",
            kopf="Someone is waiting.",
            unter=f"{anfragender} requested “{titel}”.",
            rumpf=(
                f"{anfragender} would like “{titel}”. Nothing is downloaded until you approve "
                "it – until then the request just sits there."
            ),
            knopf="Review request",
            link=link,
            profil_link=profil_link,
        )
    return _benachrichtigung(
        englisch=englisch,
        betreff=f"Wartet auf Freigabe: {titel}",
        kopf="Jemand wartet.",
        unter=f"{anfragender} hat „{titel}“ angefragt.",
        rumpf=(
            f"{anfragender} hätte gern „{titel}“. Geladen wird nichts, solange du nicht "
            "freigibst – bis dahin bleibt die Anfrage einfach liegen."
        ),
        knopf="Anfrage ansehen",
        link=link,
        profil_link=profil_link,
    )


def request_decided_mail(
    titel: str,
    freigegeben: bool,
    sprache: str = "de",
    *,
    link: str | None = None,
    profil_link: str | None = None,
) -> Mail:
    """An den Anfragenden: über seine Anfrage wurde entschieden."""
    englisch = _ist_englisch(sprache)

    if freigegeben and englisch:
        betreff, kopf = f"Approved: {titel}", "Approved."
        unter = f"“{titel}” is on its way."
        rumpf = (
            f"“{titel}” was approved and handed over to the download. That can take a while – "
            "you will hear from Nexview again once it is ready."
        )
        knopf = "View my requests"
    elif freigegeben:
        betreff, kopf = f"Freigegeben: {titel}", "Freigegeben."
        unter = f"„{titel}“ ist unterwegs."
        rumpf = (
            f"„{titel}“ wurde freigegeben und an den Download übergeben. Das kann etwas dauern "
            "– sobald der Titel fertig ist, meldet sich Nexview wieder."
        )
        knopf = "Meine Anfragen ansehen"
    elif englisch:
        betreff, kopf = f"Declined: {titel}", "Not this time."
        unter = f"“{titel}” was declined."
        rumpf = f"Your request for “{titel}” was declined."
        knopf = "View my requests"
    else:
        betreff, kopf = f"Abgelehnt: {titel}", "Diesmal nicht."
        unter = f"„{titel}“ wurde abgelehnt."
        rumpf = f"Deine Anfrage zu „{titel}“ wurde abgelehnt."
        knopf = "Meine Anfragen ansehen"

    return _benachrichtigung(
        englisch=englisch,
        betreff=betreff,
        kopf=kopf,
        unter=unter,
        rumpf=rumpf,
        knopf=knopf,
        link=link,
        profil_link=profil_link,
    )


def feedback_mail(
    titel: str,
    sterne: int,
    kommentar: str | None,
    sprache: str = "de",
    *,
    link: str | None = None,
    profil_link: str | None = None,
) -> Mail:
    """An die Administratoren: jemand hat eine Downloadqualität bewertet."""
    englisch = _ist_englisch(sprache)
    bewertung = "★" * sterne + "☆" * (5 - sterne)

    kopfzeile = (
        f"Rating: {bewertung} ({sterne}/5)" if englisch else f"Bewertung: {bewertung} ({sterne}/5)"
    )

    # Der Kommentar bekommt eine eigene Zeile. Nur deshalb braucht diese
    # Vorlage ueberhaupt HTML - entschaerft wird der fremde Text hier von Hand.
    rumpf = kopfzeile
    rumpf_html = kopfzeile
    if kommentar:
        anfuehrung = f"“{kommentar}”" if englisch else f"„{kommentar}“"
        sicher = f"“{_sicher(kommentar)}”" if englisch else f"„{_sicher(kommentar)}“"
        rumpf += f"\n\n{anfuehrung}"
        rumpf_html += f"<br><br>{sicher}"

    if englisch:
        return _benachrichtigung(
            englisch=englisch,
            betreff=f"Rated {sterne}/5: {titel}",
            kopf="New rating.",
            unter=f"“{titel}” was rated.",
            rumpf=rumpf,
            rumpf_html=rumpf_html,
            knopf="Read and reply",
            link=link,
            profil_link=profil_link,
        )

    return _benachrichtigung(
        englisch=englisch,
        betreff=f"{sterne}/5 für {titel}",
        kopf="Neue Bewertung.",
        unter=f"„{titel}“ wurde bewertet.",
        rumpf=rumpf,
        rumpf_html=rumpf_html,
        knopf="Ansehen und antworten",
        link=link,
        profil_link=profil_link,
    )


def feedback_reply_mail(
    titel: str, sprache: str = "de", *, link: str | None = None, profil_link: str | None = None
) -> Mail:
    """An den Bewertenden: der Administrator hat geantwortet."""
    englisch = _ist_englisch(sprache)
    if englisch:
        return _benachrichtigung(
            englisch=englisch,
            betreff=f"Reply to your rating: {titel}",
            kopf="You got an answer.",
            unter=f"Your rating of “{titel}” was answered.",
            rumpf=(
                "Your administrator replied to your comment. You can read the answer in "
                "Nexview under „My requests“."
            ),
            knopf="Read the answer",
            link=link,
            profil_link=profil_link,
        )
    return _benachrichtigung(
        englisch=englisch,
        betreff=f"Antwort auf deine Rückmeldung: {titel}",
        kopf="Du hast Antwort.",
        unter=f"Deine Bewertung zu „{titel}“ wurde beantwortet.",
        rumpf=(
            "Dein Administrator hat auf deinen Kommentar geantwortet. Die Antwort steht in "
            "Nexview unter „Meine Anfragen“."
        ),
        knopf="Antwort lesen",
        link=link,
        profil_link=profil_link,
    )
