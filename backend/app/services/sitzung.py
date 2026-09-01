"""Die Sitzung im Browser - und der eine Ort, an dem sie entsteht.

⚠️ **Das Erneuerungs-Token verlaesst das Backend nur als HttpOnly-Cookie.**
Frueher stand es im Antwortkoerper und lag danach dreissig Tage lang im
``localStorage``. Jedes Skript, das je auf der Seite lief, konnte es lesen,
mitnehmen und dreissig Tage lang benutzen - von einem beliebigen Rechner aus,
ohne je ein Passwort zu kennen.

Was der Umbau **bringt**: Aus einem dauerhaften Diebstahl wird ein Angriff,
der endet, wenn der Reiter zugeht. Der Ausweis laesst sich nicht mehr
mitnehmen.

Was er **nicht** bringt - damit niemand sich in Sicherheit waehnt: Ein Skript
auf der Seite kann weiterhin ``/api/auth/refresh`` aufrufen, das Cookie faehrt
ja automatisch mit, und bekommt einen Zugangs-Token in die Hand. Es kann also
alles tun, was der Benutzer tun kann, solange die Seite offen ist. Und der
Zugangs-Token selbst steht weiter im Antwortkoerper und gilt dreissig Minuten.

Warum der Zugangs-Token trotzdem **kein** Cookie wird: Dann waere die ganze
API cookie-authentifiziert, und jeder schreibende Endpunkt braeuchte einen
CSRF-Schutz. So liegt das Cookie nur an ``/api/auth`` - alles andere laeuft
weiter ueber den ``Authorization``-Kopf, und einen Kopf kann eine fremde Seite
nicht setzen. Die CSRF-Flaeche schrumpft damit auf **einen** Endpunkt, und
der ist doppelt abgesichert: ``SameSite=Lax`` schickt das Cookie bei einem
fremdveranlassten POST gar nicht erst mit, und selbst wenn - die fremde Seite
duerfte die Antwort nicht lesen. Ein CSRF-Token braucht es deshalb nicht.

⚠️ **Alle fuenf Wege, auf denen eine Sitzung entsteht, gehen durch
``starten``.** Es sind: die normale Anmeldung (auch die von Kinderkonten), die
Erneuerung, die beiden Medienserver-Anmeldungen (Code und Passwort) und die
Erst-Einrichtung des Administrators. Wuerde einer davon weiter selbst ein
Erneuerungs-Token bauen und in den Koerper legen, waere die ganze Arbeit
umsonst - deshalb haelt ``test_sitzung.py`` fest, dass
``create_refresh_token`` in keiner anderen Datei mehr vorkommt.

Einladung einloesen und Passwort zuruecksetzen geben uebrigens **gar keine**
Token aus; beide schicken danach auf die normale Anmeldung.
"""

from __future__ import annotations

import logging
from datetime import timezone

from fastapi import Request, Response

from ..config import get_settings
from ..models import User
from ..schemas import TokenPair
from ..security import (
    TokenInhalt,
    access_token_expires_in,
    create_access_token,
    create_refresh_token,
)

logger = logging.getLogger("nexview.sitzung")

COOKIE_NAME = "nexview_refresh"

def cookie_pfad() -> str:
    """Der Pfad des Cookies - er schneidet es auf die Anmeldewege zu.

    Das Cookie faehrt dann nicht bei jedem Poster-Abruf mit, sondern nur dort,
    wo es gebraucht wird. Es passt, dass auch die Medienserver-Anmeldung unter
    ``/api/auth/mediaserver`` haengt - sonst muesste es zwei Cookies geben.
    Ein Sicherheitsriegel ist der Pfad nicht (innerhalb eines Ursprungs trennt
    er nichts), sondern nur eine Verkleinerung der Flaeche.

    Mit gesetztem Unterpfad (``NEXVIEW_URL_BASE``) traegt der Pfad den Vorbau:
    Cookie-Pfade prueft der **Browser**, und aus dessen Sicht liegt die
    Anmeldung unter ``/nexview/api/auth`` - egal, ob der Proxy den Vorbau
    durchreicht oder abschneidet; abgeschnitten wird erst hinter dem Browser.
    """
    return f"{get_settings().url_base}/api/auth"


def _secure(request: Request) -> bool:
    """Traegt das Cookie ``Secure``?

    ``auto`` schaut auf das Schema **dieser** Anfrage. Das ist bewusst
    zurueckhaltend: Ein Secure-Cookie, das der Browser wegwirft, sperrt jeden
    aus, der Nexview ohne HTTPS betreibt - und das sind bei einer
    selbstgehosteten Anwendung viele. Lieber ein Cookie ohne Secure als eine
    Anmeldung, die nicht mehr geht.

    Hinter einem HTTPS-Proxy, der intern http weiterreicht, sieht Nexview
    ``http``. Dafuer gibt es ``NEXVIEW_COOKIE_SECURE=on``. Geraten wird nicht -
    siehe die Begruendung an der Einstellung in ``config.py``.
    """
    einstellung = (get_settings().cookie_secure or "auto").strip().lower()
    if einstellung == "on":
        return True
    if einstellung == "off":
        return False
    if einstellung != "auto":
        logger.warning(
            "NEXVIEW_COOKIE_SECURE is set to %r, which is not understood. "
            "Allowed: auto, on, off. Falling back to auto.",
            einstellung,
        )
    return request.url.scheme == "https"


#: Oeffentlicher Name fuer ``_secure``.
#:
#: ⚠️ **Jedes Cookie, das Nexview setzt, gehoert an dieselbe Entscheidung.**
#: Das OIDC-Anlauf-Cookie hing lange daneben und trug nie ``Secure`` - auch
#: nicht, wenn der Betreiber ``NEXVIEW_COOKIE_SECURE=on`` gesetzt hatte. Ein
#: zweiter Aufruf von ``os.environ`` an anderer Stelle waere derselbe Fehler
#: noch einmal; deshalb gibt es genau diese eine Quelle.
cookie_secure = _secure


def starten(response: Response, request: Request, user: User) -> TokenPair:
    """Eine Sitzung beginnen: Cookie setzen, Zugangs-Token zurueckgeben.

    Der einzige Ort, an dem ein Erneuerungs-Token entsteht.
    """
    response.set_cookie(
        COOKIE_NAME,
        create_refresh_token(user.id),
        max_age=get_settings().refresh_token_days * 24 * 60 * 60,
        path=cookie_pfad(),
        httponly=True,
        samesite="lax",
        secure=_secure(request),
    )
    return TokenPair(
        access_token=create_access_token(user.id),
        expires_in=access_token_expires_in(),
    )


def beenden(response: Response, request: Request) -> None:
    """Cookie loeschen.

    Pfad und ``Secure`` muessen dieselben sein wie beim Setzen, sonst loescht
    der Browser ein anderes (nicht vorhandenes) Cookie und das echte bleibt
    liegen.
    """
    response.delete_cookie(
        COOKIE_NAME,
        path=cookie_pfad(),
        httponly=True,
        samesite="lax",
        secure=_secure(request),
    )


def gelesen(request: Request) -> str | None:
    """Das Erneuerungs-Token aus dem Cookie - oder ``None``."""
    return request.cookies.get(COOKIE_NAME)


def gilt_noch(inhalt: TokenInhalt, user: User) -> bool:
    """Laesst das Konto dieses Token noch gelten?

    ⚠️ **Hier wird der Docstring von ``set_password`` endlich wahr.** Er
    behauptete seit jeher, ein Passwortwechsel mache alle Sitzungen ungueltig.
    ``password_changed_at`` wurde an vier Stellen geschrieben - und an keiner
    einzigen gelesen. Ein gestohlenes Token ueberlebte damit jeden
    Passwortwechsel, und ein Betroffener hatte keinen Ausweg ausser dem Konto
    zu deaktivieren oder ``NEXVIEW_SECRET_KEY`` zu tauschen. Letzteres macht
    alle gespeicherten Radarr-, Sonarr- und TMDB-Schluessel unlesbar - also
    gar kein Ausweg.

    Der Vergleich braucht keine neue Spalte und keine zusaetzliche Abfrage:
    Das ``iat`` liegt schon im Token, und der Benutzer ist an beiden
    Aufrufstellen ohnehin bereits geladen.

    ⚠️ **Verglichen wird in Millisekunden, und dahinter steckt ein behobener
    Fehler.** Zuerst stand hier ein Vergleich gegen die *abgerundete* Sekunde
    des ``iat``. Das schien harmlos - ein Fenster von unter einer Sekunde.
    Fuer den Angreifer, gegen den dieser Riegel gebaut ist, war es aber gerade
    **nicht** vernachlaessigbar: Ein Skript, das im Sekundentakt erneuert,
    haelt immer ein Token aus der laufenden Sekunde, faellt durch das Fenster,
    erneuert sofort wieder - und der Passwortwechsel bewirkt nichts.

    Andersherum aufzurunden war auch keine Loesung: Dann sperrte sich jedes
    frisch angelegte Konto selbst aus, denn sein ``password_changed_at``
    entsteht in derselben Sekunde wie sein erstes Token. Beide Rundungen sind
    falsch - deshalb traegt das Token seit 0.21 einen genauen Zeitstempel
    (``ms``), und hier wird ohne Rundung verglichen.

    Gefunden hat das die volle Testreihe, nicht der Einzellauf: Ob beide in
    dieselbe Sekunde fallen, haengt daran, wie lange bcrypt dazwischen
    braucht.
    """
    gewechselt = user.password_changed_at
    if gewechselt is None:
        return True
    # Aus der Datenbank kommt der Wert ohne Zeitzone zurueck (SQLite kennt
    # keine); gemeint ist immer UTC.
    if gewechselt.tzinfo is None:
        gewechselt = gewechselt.replace(tzinfo=timezone.utc)
    grenze = gewechselt

    # ⚠️ Die spaetere der beiden Grenzen zaehlt. ``sessions_valid_from`` setzt
    # das Wiederherstellen - danach darf keine Sitzung von vorher weitergelten,
    # auch wenn niemand sein Passwort geaendert hat.
    #
    # ⚠️ **Und auch dieser Wert braucht die Zeitzone.** SQLite gibt Zeitpunkte
    # ohne zurueck; ein Vergleich mit einem zeitzonenbehafteten Wert wirft
    # ``TypeError`` - und zwar erst beim Anmelden, nicht beim Schreiben. Genau
    # deshalb steht dieselbe Behandlung schon zwei Zeilen weiter oben.
    ab = user.sessions_valid_from
    if ab is not None:
        if ab.tzinfo is None:
            ab = ab.replace(tzinfo=timezone.utc)
        if ab > grenze:
            grenze = ab

    return inhalt.ausgestellt >= int(grenze.timestamp() * 1000)
