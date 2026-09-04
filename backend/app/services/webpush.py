"""Web Push im Server - der Schluessel der Installation und die Geraete der Menschen.

Ein Web-Push-Ziel ist ein Browser, der sich selbst angemeldet hat. Es ist eine
Zeile in ``channel_targets`` mit Besitzer (``ChannelKind.webpush``), damit der
Postausgang es wie jedes andere Ziel bedient: Wiederholungen, letzter Fehler,
die Betreiber-Uebersicht und die Regel, dass ein gesperrtes Konto nichts mehr
bekommt, laufen von selbst mit. Was hier steht, ist nur das Drumherum:
anmelden, auflisten, abmelden, die Probemeldung - und das Schluesselpaar.

⚠️ **Zwei Sorten Einstellung, und die Trennung ist Absicht.** *Ob* ein Geraet
Meldungen bekommt, entscheidet der Browser dort und steht als Zeile hier.
*Wobei* gemeldet wird, gehoert dem Konto (``User.push_*``) und gilt damit
auf allen Geraeten gleich. Wer beides zusammenlegte, haette entweder "am
Telefon melde ich anderes als am Rechner" oder "ein Geraet abmelden schaltet
alle ab".

⚠️ **Der private VAPID-Schluessel wird einmal erzeugt und danach nie wieder
angefasst.** Alle Abonnements haengen an seinem oeffentlichen Gegenstueck;
ein neues Paar heisst, dass sich jedes Geraet neu anmelden muss - und
niemand erfaehrt, warum sein Handy still geworden ist.
"""

from __future__ import annotations

import base64
import logging
import threading

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crypto import encrypt
from ..models import ChannelKind, ChannelTarget, User
from . import channel_outbox, channel_targets, channels, notify
from .settings_service import load_settings, save_settings

logger = logging.getLogger("nexview.webpush")

#: Der Schluessel in den Einstellungen, unter dem das VAPID-Paar liegt.
SCHLUESSEL = "push_vapid_private"

#: Die Haken am Konto - aus der Zuordnung abgeleitet, nicht abgeschrieben.
HAKEN: tuple[str, ...] = tuple(sorted(set(notify.PUSH_SWITCH.values())))

#: Die Probemeldung, je Sprache des Ziels.
PROBE = {
    "de": ("Probemeldung", "Wenn du das liest, kommt auf diesem Gerät alles an."),
    "en": ("Test message", "If you can read this, everything reaches this device."),
}

_schloss = threading.Lock()


class UnbrauchbareAdresse(ValueError):
    """Die Adresse beim Push-Dienst ist keine."""


# --------------------------------------------------------------------------- #
# Der Schluessel
# --------------------------------------------------------------------------- #


def _paar(db: Session) -> Vapid02:
    """Das VAPID-Paar, beim ersten Aufruf erzeugt.

    ⚠️ **Unter einem Schloss.** Zwei Anfragen zugleich beim allerersten Mal
    erzeugten sonst zwei Paare, und das zweite ueberschriebe das erste - jedes
    Geraet, das sich in der Zwischenzeit angemeldet hat, waere still taub.
    """
    with _schloss:
        pem = load_settings(db).push_vapid_private
        if pem:
            return Vapid02.from_pem(pem.encode())

        frisch = Vapid02()
        frisch.generate_keys()
        save_settings(db, {SCHLUESSEL: frisch.private_pem().decode()}, commit=True)
        logger.info("A new VAPID key pair was generated for web push")
        return frisch


def oeffentlicher_schluessel(db: Session) -> str:
    """Was der Browser als ``applicationServerKey`` braucht.

    ⚠️ **Der rohe Punkt, nicht das PEM.** Die Push-API des Browsers will die
    65 Byte des unkomprimierten P-256-Punktes in base64url ohne Polster. Wer
    hier ein PEM oder DER hinschickt, bekommt vom Browser ein
    ``InvalidCharacterError`` - und das liest sich wie ein Fehler im eigenen
    JavaScript.
    """
    punkt = _paar(db).public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(punkt).rstrip(b"=").decode()


# --------------------------------------------------------------------------- #
# Die Geraete
# --------------------------------------------------------------------------- #


def geraetename(user_agent: str) -> str:
    """ "Chrome, Windows" aus dem User-Agent.

    ⚠️ **Grob und mit Absicht.** Der Name steht nur in der Liste, damit man
    seine Geraete auseinanderhaelt; unterschieden werden sie an der Adresse.
    Eine vollstaendige Auswertung waere eine Bibliothek fuer eine
    Beschriftung, und der ganze User-Agent gehoerte damit in die Datenbank -
    mehr ueber den Menschen, als hier gebraucht wird.
    """
    if not user_agent:
        return ""
    # Reihenfolge zaehlt: Edge nennt sich auch Chrome, Chrome auch Safari.
    browser = ""
    for kennung, name in (
        ("Edg/", "Edge"),
        ("OPR/", "Opera"),
        ("Firefox/", "Firefox"),
        ("Chrome/", "Chrome"),
        ("Safari/", "Safari"),
    ):
        if kennung in user_agent:
            browser = name
            break
    system = ""
    for kennung, name in (
        ("iPhone", "iPhone"),
        ("iPad", "iPad"),
        ("Android", "Android"),
        ("Windows", "Windows"),
        ("Mac OS X", "macOS"),
        ("Linux", "Linux"),
    ):
        if kennung in user_agent:
            system = name
            break
    return ", ".join(teil for teil in (browser, system) if teil)[:80]


def eigene(db: Session, user: User) -> list[ChannelTarget]:
    """Die angemeldeten Browser dieses Menschen, die neuesten zuerst."""
    return list(
        db.scalars(
            select(ChannelTarget)
            .where(
                ChannelTarget.user_id == user.id,
                ChannelTarget.channel == ChannelKind.webpush,
            )
            .order_by(ChannelTarget.created_at.desc(), ChannelTarget.id.desc())
        )
    )


def vorbelegen(user: User) -> bool:
    """Beim ersten Geraet alle Haken setzen - wenn noch keiner steht.

    Sonst erlaubt jemand Meldungen, und es kommt nie eine: Die Haken sind ab
    Werk aus, wie bei der Mail. Bei der Mail ist das richtig, denn dort gibt
    es keinen Moment, in dem jemand gerade ausdruecklich "ja, hierher" gesagt
    hat. Hier gibt es ihn. Wer schon Haken hat, wird nicht angefasst.
    """
    if any(getattr(user, feld) for feld in HAKEN):
        return False
    for feld in HAKEN:
        setattr(user, feld, True)
    return True


def anmelden(
    db: Session,
    user: User,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str,
    language: str,
) -> tuple[ChannelTarget, bool]:
    """Dieser Browser nimmt ab jetzt Meldungen an.

    ⚠️ **Dieselbe Adresse gibt keine zweite Zeile.** Der Browser meldet sich
    bei jedem Start erneut an und bekommt dabei in aller Regel dieselbe
    Adresse zurueck. Ohne diese Stelle wuechse die Tabelle mit jedem
    Seitenaufruf, und jede Meldung ginge vielfach hinaus.

    ⚠️ **Und sie kann den Besitzer wechseln.** Meldet sich an einem geteilten
    Rechner ein zweiter Mensch an, gehoert das Abonnement ab dann ihm - der
    Browser hat nur eines. Andernfalls bekaeme der Vorgaenger weiter die
    Meldungen, und niemand faende heraus, warum.

    Liefert das Ziel und ob dabei die Haken vorbelegt wurden.
    """
    if not endpoint.startswith(("https://", "http://")):
        raise UnbrauchbareAdresse(endpoint)

    # Vor dem Anlegen zaehlen: Die Abfrage schriebe eine schon hinzugefuegte
    # Zeile mit (autoflush), und das erste Geraet saehe aus wie das zweite.
    erstes = not eigene(db, user)

    ziel = db.scalar(
        select(ChannelTarget).where(
            ChannelTarget.channel == ChannelKind.webpush,
            ChannelTarget.url == endpoint,
        )
    )
    if ziel is None:
        ziel = ChannelTarget(channel=ChannelKind.webpush, name="", url=endpoint)
        db.add(ziel)
    ziel.user_id = user.id
    ziel.api_key_id = None
    ziel.token = p256dh
    ziel.password = encrypt(auth)
    ziel.name = geraetename(user_agent)
    ziel.language = language if language in ("de", "en") else "en"
    # Kein Code: Das Abonnement entsteht in genau dem Browser, der die
    # Meldungen bekommt, aus einer angemeldeten Sitzung heraus.
    ziel.verified = True
    ziel.enabled = True
    ziel.events = {}

    vorbelegt = erstes and vorbelegen(user)
    db.commit()
    db.refresh(ziel)
    return ziel, vorbelegt


def zeile(db: Session, ziel: ChannelTarget, eigener_endpunkt: str) -> dict[str, object]:
    """Eine Geraetezeile fuer die Liste - ohne die Adresse selbst."""
    gescheitert = channel_outbox.last_failure(db, ziel)
    gelungen = channel_outbox.last_success(db, ziel)
    return {
        "id": ziel.id,
        "name": ziel.name,
        "this": bool(eigener_endpunkt) and ziel.url == eigener_endpunkt,
        "created_at": ziel.created_at,
        "last_success": gelungen.sent_at if gelungen is not None else None,
        "last_error": gescheitert.last_error if gescheitert is not None else None,
        "last_error_at": gescheitert.created_at if gescheitert is not None else None,
    }


def abmelden(db: Session, user: User, ziel_id: int) -> bool:
    """Ein Geraet abraeumen. Offene Auftraege gehen mit.

    ⚠️ **Der Besitzer wird geprueft, nicht nur die Kennung.** Die Kennungen
    sind fortlaufende Zahlen; ohne die Pruefung meldete jeder die Geraete
    jedes anderen ab.
    """
    ziel = db.get(ChannelTarget, ziel_id)
    if ziel is None or ziel.user_id != user.id or ziel.channel is not ChannelKind.webpush:
        return False
    db.delete(ziel)
    db.commit()
    return True


async def probe(db: Session, user: User, endpoint: str | None) -> tuple[bool, str]:
    """Eine Probemeldung - an dieses Geraet, oder an alle.

    ⚠️ **Sie ist kein Beiwerk.** Zwischen "der Browser hat die Erlaubnis
    erteilt" und "es kommt wirklich etwas an" liegen ein Service Worker, ein
    Push-Dienst und eine Systemeinstellung, die jeder fuer sich stumm schalten
    kann. Ohne diesen Knopf faellt das erst an dem Tag auf, an dem eine echte
    Meldung ausbleibt.

    Direkt, nicht ueber den Postausgang: Wer den Knopf drueckt, will jetzt
    wissen, ob es geht - und will die Fehlermeldung lesen, nicht spaeter auf
    einer Kachel suchen.
    """
    ziele = eigene(db, user)
    if endpoint:
        ziele = [ziel for ziel in ziele if ziel.url == endpoint]
    if not ziele:
        return False, "Auf diesem Gerät ist nichts angemeldet."

    _paar(db)
    settings = load_settings(db)
    fehler: list[str] = []
    for ziel in ziele:
        config = channel_targets.config(ziel, settings)
        if config is None:
            fehler.append("Das Abonnement ist unvollständig.")
            continue
        titel, text = PROBE.get(ziel.language, PROBE["en"])
        nachricht = channels.Notice(
            title=titel,
            body=text,
            click_url=settings.link("/") if settings.public_url else None,
            event="test",
        )
        try:
            await channels.send(ChannelKind.webpush, config, nachricht)
        except channels.ChannelGone as weg:
            # Derselbe Weg wie im Postausgang: Ein erloschenes Abonnement
            # wird weggeraeumt, nicht wiederholt.
            db.delete(ziel)
            db.commit()
            fehler.append(weg.message)
        except channels.ChannelError as problem:
            fehler.append(problem.message)
    if fehler:
        return False, " ".join(fehler)
    return True, ""
