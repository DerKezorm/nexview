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

⚠️ **Abmelden beendet die Sitzung auf dem Server, nicht nur im Browser.**
Bis 1.0.0 nahm es nur das Cookie weg; wer vorher eine Kopie davon gezogen
hatte, kam damit dreissig Tage lang weiter herein (Befund aus dem grossen
Pruefgang). Jedes Token traegt seitdem die Kennung seiner Sitzung, die beim
Erneuern mitwandert, und ``beenden`` traegt sie in ``beendete_sitzungen``
ein. Danach gilt kein Token dieser Sitzung mehr: nicht die Kopie des
Cookies und auch nicht das Zugangs-Token, das gerade noch laeuft
(``beendet``, gefragt von ``deps.get_current_user`` und ``/refresh``). Andere
Geraete desselben Kontos bleiben angemeldet.
"""

from __future__ import annotations

import logging
from datetime import UTC, timedelta

from fastapi import Request, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import BeendeteSitzung, User, utcnow
from ..schemas import TokenPair
from ..security import (
    TokenInhalt,
    access_token_expires_in,
    create_access_token,
    create_refresh_token,
    decode_token,
    neue_sitzung,
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


def starten(
    response: Response, request: Request, user: User, *, fortsetzen: str | None = None
) -> TokenPair:
    """Eine Sitzung beginnen: Cookie setzen, Zugangs-Token zurueckgeben.

    Der einzige Ort, an dem ein Erneuerungs-Token entsteht.

    ``fortsetzen`` ist die Kennung der Sitzung, die gerade erneuert wird - nur
    ``/refresh`` gibt sie mit. ⚠️ **Sie muss mitwandern.** Bekaeme jede
    Erneuerung eine neue, beendete das Abmelden nur das letzte Glied der
    Kette: Eine Kopie des Cookies von gestern truege eine andere Kennung und
    kaeme weiter herein - genau der Befund, fuer den es die Kennung gibt.
    """
    kennung = fortsetzen or neue_sitzung()
    response.set_cookie(
        COOKIE_NAME,
        create_refresh_token(user.id, kennung),
        max_age=get_settings().refresh_token_days * 24 * 60 * 60,
        path=cookie_pfad(),
        httponly=True,
        samesite="lax",
        secure=_secure(request),
    )
    return TokenPair(
        access_token=create_access_token(user.id, kennung),
        expires_in=access_token_expires_in(),
    )


def beenden(response: Response, request: Request, db: Session) -> None:
    """Die Sitzung dieses Browsers beenden - auf dem Server und im Browser.

    Beendet wird, was das Cookie nennt, und zusaetzlich, was ein mitgeschicktes
    Zugangs-Token nennt (fuer ein Programm, das ohne Cookie arbeitet). Ein
    abgelaufenes, gefaelschtes oder fehlendes Token beendet nichts; das Cookie
    geht trotzdem weg. Ein abgelaufenes Erneuerungs-Token muss auch nichts
    beenden: Jedes Zugangs-Token seiner Sitzung ist frueher abgelaufen.

    Pfad und ``Secure`` muessen beim Loeschen dieselben sein wie beim Setzen,
    sonst loescht der Browser ein anderes (nicht vorhandenes) Cookie und das
    echte bleibt liegen.
    """
    kandidaten = []
    roh = gelesen(request)
    if roh:
        kandidaten.append(decode_token(roh, "refresh"))
    kopf = request.headers.get("authorization", "")
    if kopf[:7].lower() == "bearer ":
        kandidaten.append(decode_token(kopf[7:].strip(), "access"))

    jetzt = utcnow().replace(tzinfo=None)
    # Aufgeraeumt wird hier und nur hier: Neue Zeilen entstehen nur beim
    # Abmelden, und was nach ``bis`` noch steht, schuetzt vor nichts mehr.
    db.execute(delete(BeendeteSitzung).where(BeendeteSitzung.bis < jetzt))
    # Laenger kann kein Token leben, das vor diesem Moment ausgestellt wurde.
    bis = jetzt + timedelta(days=get_settings().refresh_token_days)
    for inhalt in kandidaten:
        if inhalt is None or db.get(BeendeteSitzung, inhalt.sitzung) is not None:
            continue
        if db.get(User, inhalt.benutzer_id) is None:
            continue
        db.add(
            BeendeteSitzung(
                sitzung=inhalt.sitzung, user_id=inhalt.benutzer_id, beendet_am=jetzt, bis=bis
            )
        )
        # Sofort, damit ein zweites Token derselben Sitzung (Cookie und Kopf
        # zugleich) oben schon als beendet gefunden wird.
        db.flush()
    db.commit()

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


def beendet(db: Session, inhalt: TokenInhalt) -> bool:
    """Wurde die Sitzung dieses Tokens mit "Abmelden" beendet?

    Eine Abfrage ueber den Primaerschluessel je Anfrage - der Preis dafuer,
    dass Abmelden auch das laufende Zugangs-Token beendet und nicht erst das
    naechste Erneuern.
    """
    return db.get(BeendeteSitzung, inhalt.sitzung) is not None


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
        gewechselt = gewechselt.replace(tzinfo=UTC)
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
            ab = ab.replace(tzinfo=UTC)
        grenze = max(grenze, ab)

    return inhalt.ausgestellt >= int(grenze.timestamp() * 1000)
