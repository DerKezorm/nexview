"""Gemeinsames Handwerkszeug der serverseitigen Kanaele.

Ein Kanal ist hier absichtlich sehr wenig: er nimmt eine ``Notice`` entgegen
und verschickt sie. Alles andere - wann etwas verschickt wird, wie oft es
wiederholt wird, wer sich dafuer interessiert - steht im Postausgang. So
kostet ein weiterer Kanal (Discord, Telegram) spaeter nur noch die Datei, in
der sein Protokoll steht.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

# Wie lange ein Kanal antworten darf. Grosszuegig genug fuer eine Instanz im
# eigenen Netz, kurz genug, dass ein toter Server den Postausgang nicht
# minutenlang blockiert.
TIMEOUT = httpx.Timeout(8.0)

# Dringlichkeitsstufen, wie sie der Administrator je Meldung waehlt.
#
# Bewusst benannt und nicht als Zahl: Gotify zaehlt von 0 bis 10, ntfy von 1
# bis 5, Discord kennt gar keine Prioritaet. Eine Zahl waere je Kanal etwas
# anderes - dieselbe Auswahlliste dagegen passt ueberall, und jeder Kanal
# rechnet sie in das um, was sein Dienst versteht.
LEVELS = ("low", "normal", "high", "urgent")
DEFAULT_LEVEL = "normal"


class ChannelError(Exception):
    """Der Versand hat nicht geklappt - mit einem Text, den man lesen kann."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Notice:
    """Was verschickt werden soll - noch ohne Kenntnis des Kanals.

    ``event`` ist der Meldungstyp (``NotificationType``-Wert). Die meisten
    Kanaele brauchen ihn nicht - Discord waehlt danach die Farbe des Embeds.
    ``None`` heisst: keine besondere Bedeutung, etwa bei der Testnachricht.
    """

    title: str
    body: str
    level: str = DEFAULT_LEVEL
    poster_url: str | None = None
    click_url: str | None = None
    event: str | None = None


def _lesbar(fehler: Exception, ziel: str) -> str:
    """Aus einem HTTP-Fehler einen Satz machen, mit dem man etwas anfangen kann."""
    if isinstance(fehler, httpx.HTTPStatusError):
        code = fehler.response.status_code
        if code in (401, 403):
            return f"{ziel} hat die Anmeldung abgelehnt (HTTP {code}). Zugangsdaten prüfen."
        if code == 404:
            return f"{ziel} kennt diese Adresse nicht (HTTP 404). Adresse und Pfad prüfen."
        if code == 413:
            return f"{ziel} hält die Nachricht für zu groß (HTTP 413)."
        return f"{ziel} antwortete mit HTTP {code}."
    if isinstance(fehler, httpx.ConnectTimeout | httpx.ReadTimeout):
        return f"{ziel} hat nicht rechtzeitig geantwortet."
    if isinstance(fehler, httpx.ConnectError):
        return f"{ziel} war nicht erreichbar. Läuft der Dienst, stimmt der Port?"
    return f"{ziel} war nicht erreichbar: {fehler}"


async def post(url: str, *, json: dict, headers: dict[str, str] | None = None) -> None:
    """Eine Nachricht abschicken - oder mit ``ChannelError`` scheitern.

    Bewusst ein eigener, kurzlebiger Client statt eines Verbindungspools: hier
    faellt alle paar Stunden eine Anfrage an, und die Adresse kann sich
    zwischendurch aendern.

    ``follow_redirects`` bleibt aus. Eine Weiterleitung wuerde die Nachricht an
    ein Ziel schicken, das der Administrator nie eingetragen hat - beim
    Verschicken von Daten ist das keine Bequemlichkeit, sondern ein Risiko.
    """
    ziel = url.split("//", 1)[-1].split("/", 1)[0] or url
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            antwort = await client.post(url, json=json, headers=headers or {})
            antwort.raise_for_status()
    except httpx.HTTPError as fehler:
        raise ChannelError(_lesbar(fehler, ziel)) from fehler


async def get_json(url: str, *, headers: dict[str, str] | None = None) -> object:
    """Etwas abfragen statt verschicken - fuer den Erreichbarkeitstest."""
    ziel = url.split("//", 1)[-1].split("/", 1)[0] or url
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            antwort = await client.get(url, headers=headers or {})
            antwort.raise_for_status()
            return antwort.json()
    except ValueError as fehler:
        raise ChannelError(f"{ziel} hat keine verwertbare Antwort geliefert.") from fehler
    except httpx.HTTPError as fehler:
        raise ChannelError(_lesbar(fehler, ziel)) from fehler


async def post_json(url: str, *, json: dict) -> object:
    """Verschicken und die Antwort auswerten - auch wenn sie ein Fehler ist.

    Telegram antwortet auf eine abgelehnte Nachricht mit HTTP 400 **und** einer
    Begruendung im Rumpf ("chat not found", "message thread not found"). Die
    ist deutlich hilfreicher als die Statuszeile, deshalb wird hier nicht
    vorschnell abgebrochen. Erst wenn gar nichts Lesbares kommt, ist es ein
    Fehler.
    """
    ziel = url.split("//", 1)[-1].split("/", 1)[0] or url
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            antwort = await client.post(url, json=json)
            return antwort.json()
    except ValueError as fehler:
        raise ChannelError(f"{ziel} hat keine verwertbare Antwort geliefert.") from fehler
    except httpx.HTTPError as fehler:
        raise ChannelError(_lesbar(fehler, ziel)) from fehler


def check_url(url: str) -> None:
    """Nur ``http`` und ``https`` - alles andere gar nicht erst versuchen."""
    if not url.startswith(("http://", "https://")):
        raise ChannelError("Die Adresse muss mit http:// oder https:// beginnen.")
