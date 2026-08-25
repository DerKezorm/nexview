"""Die Bremse an allen Tueren, hinter denen ein Geheimnis geprueft wird.

Ohne sie kann eine Maschine Passwoerter durchprobieren, so schnell die
Verbindung hergibt. Betroffen sind **drei** Tueren, nicht eine:

1. ``/api/auth/login`` - das Nexview-Passwort.
2. ``/api/mediaserver/login/password`` - das Passwort des **Medienservers**.
   Die unangenehmste von allen: Nexview reicht es an Plex/Jellyfin/Emby
   weiter, ein Angreifer braucht dafuer nicht einmal ein Nexview-Konto, und
   ohne Bremse waere Nexview ein bequemer Durchreiche-Dienst zum Raten
   gegen den Medienserver.
3. ``/api/onboarding/password/{raw}`` - Einladungs- und Ruecksetz-Token,
   die in der Adresszeile stehen.

⚠️ **Hier wird nicht geschlafen.** Der naheliegende Bau waere, die Antwort um
ein paar Sekunden zu verzoegern. Das waere ein neues Loch: Die Anmeldung ist
eine gewoehnliche (synchrone) Funktion, laeuft also in einem Arbeitsfaden aus
einem kleinen Vorrat, und Nexview faehrt mit **einem** Arbeitsprozess. Vierzig
gleichzeitige Anfragen, die alle acht Sekunden schlafen, legen damit den
ganzen Dienst lahm - ohne ein einziges Passwort zu raten. Stattdessen wird
sofort mit 429 und ``Retry-After`` abgewiesen. Fuer den Menschen davor ist das
dasselbe ("warte kurz"), fuer die Maschine auch - nur kostet es nichts.

**Der Merkposten liegt im Arbeitsspeicher**, wie bei ``channel_verify``. Er in
der Datenbank waere schlimmer, nicht besser: Jeder Fehlversuch wuerde dann
einen Schreibzugriff ausloesen, den **jeder ohne Anmeldung** ausloesen kann -
also genau der Hebel, den die Bremse verhindern soll. Der Preis ist, dass ein
Neustart die Zaehler vergisst. Das ist verkraftbar: Neustarts sind selten und
liegen nicht in der Hand des Angreifers.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from ..config import get_settings

logger = logging.getLogger("nexview.anmeldebremse")

# Die ersten Versuche kosten nichts. Drei, weil sich ein Mensch vertippt -
# und weil ein Kind, das sein Passwort neu gelernt hat, nicht beim zweiten
# Anlauf gegen eine Wand laeuft.
FREI_VERSUCHE = 3

# Ab dem vierten Fehlversuch verdoppelt sich die Wartezeit: 1, 2, 4, 8, 16, 32.
WARTE_BASIS_SEKUNDEN = 1.0
WARTE_MAX_SEKUNDEN = 60.0

# Ab hier ist Schluss, und zwar fuer eine Viertelstunde. Sie geht **von
# selbst** wieder auf: Eine Sperre, die den Administrator braucht, sperrt in
# der Praxis den Administrator aus.
SPERRE_AB_VERSUCH = 10
SPERRE_SEKUNDEN = 900.0

# Wer eine Stunde lang nichts falsch macht, faengt bei null an. Sonst summieren
# sich Vertipper ueber Wochen zu einer Sperre.
GEDAECHTNIS_SEKUNDEN = 3600.0


@dataclass
class _Zaehler:
    versuche: int = 0
    zuletzt: float = 0.0
    frei_ab: float = 0.0


_zaehler: dict[str, _Zaehler] = {}


def _jetzt() -> float:
    return time.monotonic()


def _aufraeumen(jetzt: float) -> None:
    """Vergessene Zaehler wegwerfen, damit die Ablage nicht endlos waechst.

    Ohne das waere die Bremse selbst ein Angriffsweg: Wer sich zehntausend
    Benutzernamen ausdenkt, legt zehntausend Zaehler an.
    """
    alt = [s for s, z in _zaehler.items() if jetzt - z.zuletzt > GEDAECHTNIS_SEKUNDEN]
    for schluessel in alt:
        _zaehler.pop(schluessel, None)


def _wartezeit(versuche: int) -> float:
    """Wie lange nach ``versuche`` Fehlschlaegen zu warten ist."""
    if versuche >= SPERRE_AB_VERSUCH:
        return SPERRE_SEKUNDEN
    if versuche <= FREI_VERSUCHE:
        return 0.0
    stufe = versuche - FREI_VERSUCHE - 1
    return min(WARTE_BASIS_SEKUNDEN * (2**stufe), WARTE_MAX_SEKUNDEN)


def restliche_sperre(schluessel: str) -> float:
    """Wie viele Sekunden dieser Schluessel noch warten muss. 0 = frei."""
    jetzt = _jetzt()
    eintrag = _zaehler.get(schluessel)
    if eintrag is None:
        return 0.0
    if jetzt - eintrag.zuletzt > GEDAECHTNIS_SEKUNDEN:
        _zaehler.pop(schluessel, None)
        return 0.0
    return max(0.0, eintrag.frei_ab - jetzt)


def fehlschlag(schluessel: str) -> None:
    """Einen Fehlversuch vermerken und die naechste Wartezeit setzen."""
    jetzt = _jetzt()
    _aufraeumen(jetzt)

    eintrag = _zaehler.get(schluessel)
    if eintrag is None or jetzt - eintrag.zuletzt > GEDAECHTNIS_SEKUNDEN:
        eintrag = _Zaehler()
        _zaehler[schluessel] = eintrag

    eintrag.versuche += 1
    eintrag.zuletzt = jetzt
    eintrag.frei_ab = jetzt + _wartezeit(eintrag.versuche)

    if eintrag.versuche == SPERRE_AB_VERSUCH:
        # Genau einmal laut werden, nicht bei jedem weiteren Versuch - sonst
        # schreibt ein Angreifer dem Betreiber das Protokoll voll.
        logger.warning(
            "Locked after %s failed attempts: %s. It opens again by itself in "
            "%s minutes.",
            eintrag.versuche,
            schluessel,
            int(SPERRE_SEKUNDEN // 60),
        )


def erfolg(schluessel: str) -> None:
    """Geklappt - der Zaehler ist erledigt."""
    _zaehler.pop(schluessel, None)


def zuruecksetzen() -> None:
    """Alles vergessen. Nur fuer Tests."""
    _zaehler.clear()


# --------------------------------------------------------------------------
# Woher die Adresse des Anfragenden kommt
# --------------------------------------------------------------------------

def client_ip(request: Request) -> str | None:
    """Die Adresse, gegen die gezaehlt wird - oder ``None``.

    ⚠️ **``None`` ist der Normalfall und Absicht.** Nexview weiss von sich aus
    nicht, ob es direkt am Netz haengt oder hinter einem Reverse Proxy. Haengt
    es hinter einem, sehen **alle** Anfragen aus wie dieselbe Adresse - die des
    Proxys. Eine Sperre nach Adresse wuerde dann beim ersten Vertipper den
    ganzen Haushalt aussperren, den Administrator eingeschlossen.

    Der Ausweg ist nicht Raten, sondern Fragen: ``NEXVIEW_CLIENT_IP`` sagt es
    ausdruecklich.

    * nicht gesetzt - **Adresse wird nicht benutzt**, es zaehlt nur das Konto
    * ``direct``    - Nexview haengt direkt am Netz, die Gegenstelle ist echt
    * ``proxy``     - genau ein Reverse Proxy davor
    * ``proxy:2``   - zwei davor (z. B. Cloudflare und danach ein eigener)

    Warum die Zahl noetig ist: ``X-Forwarded-For`` ist eine Liste, und jeder
    darf vorne etwas hineinschreiben. Vertrauen kann man nur den Eintraegen,
    die **die eigenen Proxys** hinten angehaengt haben. Deshalb wird von hinten
    gezaehlt. Wer stattdessen den ersten Eintrag naehme, liesse sich vom
    Angreifer jede beliebige Adresse vorsetzen - und die Bremse waere wertlos.
    """
    einstellung = (get_settings().client_ip or "").strip().lower()
    if not einstellung:
        return None

    if einstellung == "direct":
        return _gueltig(request.client.host if request.client else None)

    if einstellung == "proxy" or einstellung.startswith("proxy:"):
        sprunge = 1
        if ":" in einstellung:
            try:
                sprunge = max(1, int(einstellung.split(":", 1)[1]))
            except ValueError:
                sprunge = 1

        kette = [
            teil.strip()
            for teil in (request.headers.get("x-forwarded-for") or "").split(",")
            if teil.strip()
        ]
        # Von hinten: der letzte Eintrag stammt vom eigenen Proxy, der
        # vorletzte vom Proxy davor, und so weiter.
        if len(kette) >= sprunge:
            return _gueltig(kette[-sprunge])

        # Weniger Eintraege als erwartet - die Kette ist nicht die, die
        # eingestellt wurde. Lieber gar keine Adresse als eine falsche.
        return None

    logger.warning(
        "NEXVIEW_CLIENT_IP is set to %r, which is not understood. Allowed: "
        "direct, proxy, proxy:<number>. Rate limiting by address is off.",
        einstellung,
    )
    return None


def _gueltig(wert: str | None) -> str | None:
    if not wert:
        return None
    try:
        return str(ipaddress.ip_address(wert))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Was die Router benutzen
# --------------------------------------------------------------------------

def schluessel_konto(tuer: str, kennung: str) -> str:
    """Zaehler-Schluessel fuer ein Konto.

    Kleingeschrieben, weil die Anmeldung selbst Gross- und Kleinschreibung
    ignoriert - sonst waere ``Anna`` ein anderer Zaehler als ``anna``, und
    zehn Schreibweisen waeren zehn freie Versuche.
    """
    return f"{tuer}|konto|{kennung.strip().lower()}"


def schluessel_adresse(tuer: str, adresse: str) -> str:
    return f"{tuer}|adresse|{adresse}"


def _sperrmeldung(sekunden: float) -> HTTPException:
    """Die 429-Antwort - als **Kennung**, nicht als fertiger Satz.

    ⚠️ **Warum nicht einfach deutscher Text?** Weil diese Meldung auf der
    Anmeldeseite erscheint, und dort ist niemand angemeldet: ``User.language``
    gibt es noch nicht, und die im Kopf gewaehlte Sprache liegt im
    ``localStorage`` des Browsers, die der Server also gar nicht kennt. Ein
    Server, der hier uebersetzt, muss raten - und laege genau bei der Person
    falsch, die bewusst umgeschaltet hat.

    Deshalb schickt Nexview ``code`` und ``retry_after``; den Satz baut das
    Frontend aus seinen Sprachdateien, wo die Sprache ohnehin feststeht.
    ``message`` bleibt als deutscher Rueckfall dabei - fuer alles, was die
    API ohne Nexview-Oberflaeche benutzt.
    """
    rest = max(1, int(sekunden) + (1 if sekunden % 1 else 0))

    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "too_many_attempts",
            "retry_after": rest,
            "message": (
                f"Zu viele Fehlversuche. Bitte in {rest} Sekunden noch einmal versuchen."
            ),
        },
        # Damit die Oberflaeche mitzaehlen kann, statt zu raten.
        headers={"Retry-After": str(rest)},
    )


def torwaechter(request: Request, tuer: str, kennung: str | None) -> list[str]:
    """Vor der Pruefung: darf dieser Versuch ueberhaupt stattfinden?

    Wirft 429, wenn Konto **oder** Adresse noch warten muss. Liefert sonst die
    Schluessel zurueck, die danach ``gescheitert``/``geklappt`` brauchen.

    ``kennung`` darf ``None`` sein - dann zaehlt nur die Adresse. Das ist der
    Fall bei den Token-Adressen, wo es keinen Benutzernamen gibt.
    """
    schluessel: list[str] = []
    if kennung:
        schluessel.append(schluessel_konto(tuer, kennung))
    adresse = client_ip(request)
    if adresse:
        schluessel.append(schluessel_adresse(tuer, adresse))

    laengste = max((restliche_sperre(s) for s in schluessel), default=0.0)
    if laengste > 0:
        raise _sperrmeldung(laengste)

    return schluessel


def gescheitert(schluessel: list[str]) -> None:
    for s in schluessel:
        fehlschlag(s)


def geklappt(schluessel: list[str]) -> None:
    for s in schluessel:
        erfolg(s)
