"""Der Betreiber: wem die Installation gehoert.

⚠️ **Ein Haken am Konto, keine vierte Rolle.** Der Betreiber darf nicht mehr
als jeder andere Administrator. Kein zusaetzliches Recht haengt an diesem
Modul, und es soll auch keines dazukommen - was hier steht, beantwortet
ausschliesslich die Frage: **was duerfen andere mit ihm nicht tun.**

Warum es das gibt: Bis 0.25 waren alle Administratoren gleich. Wer einen
zweiten ernannte, gab ihm damit auch die Macht, den ersten hinauszuwerfen. Die
einzige Bremse dagegen griff zu spaet - sie schuetzt nur den *letzten* aktiven
Administrator. Solange noch ein zweiter da war, durfte jeder jeden.

Drei Stellen halten die Regel:

* ``deps.betreiberschutz`` haengt an jeder Adresse, die ein **fremdes** Konto
  veraendert. Das ist die Vordertuer.
* ``kaeme_nicht_mehr_herein`` (in ``oidc.py``/``mediaserver.py``) schuetzt die
  Seitentuer: Wer den Anmeldeweg des Betreibers abschaltet, sperrt ihn aus,
  ohne sein Konto anzufassen.
* ``nach_dem_einspielen`` schuetzt die zweite Seitentuer: Eine Sicherung
  kopiert die ganze Datenbank, der Haken reiste sonst mit - und ein zweiter
  Administrator koennte eine Uebergabe rueckgaengig machen, indem er einen
  alten Stand einspielt.

``test_betreiber_waechter.py`` laeuft ueber die ganze Routentabelle und
verlangt zu **jeder** Adresse eine Entscheidung. Wird er rot, ist die Antwort
nicht, einen Ausnahmeeintrag nachzutragen - sondern die Entscheidung zu
treffen, die jemand ausgelassen hat.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Role, User

logger = logging.getLogger("nexview.betreiber")


class BetreiberFehler(Exception):
    """Eine Uebergabe, die nicht geht - mit Kennung und deutschem Rueckfall."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Nachsehen
# ---------------------------------------------------------------------------


def traeger(db: Session) -> User | None:
    """Wer traegt den Haken? ``None`` heisst: niemand - und das ist ein Zustand.

    Er entsteht bei einer frischen Installation vor der Einrichtung und nach
    einem Update ohne einen einzigen aktiven Administrator. Die Datenbank kann
    "hoechstens einer" erzwingen, "mindestens einer" nicht - deshalb muss die
    Oberflaeche diesen Fall **zeigen**, statt still jemanden zu ernennen.
    """
    return db.scalar(select(User).where(User.is_betreiber.is_(True)))


def ist_betreiber(user: User | None) -> bool:
    return bool(user is not None and user.is_betreiber)


def aus_der_umgebung() -> str | None:
    """Der Name aus ``NEXVIEW_BETREIBER``, falls gesetzt.

    Der Nothammer im Glaskasten: Wer sich aussperrt - Passwort weg,
    Anmeldedienst tot, Konto unbrauchbar -, traegt eine Zeile in die
    ``docker-compose.yml`` ein und startet neu.

    ⚠️ **Die Abgrenzung, die ausgesprochen sein muss:** Damit ist der Schutz
    gegen einen *Administrator* gerichtet, nicht gegen jemanden mit Zugriff auf
    den Server. Wer den Behaelter neu starten kann, hat ohnehin die volle
    Kontrolle ueber Anwendung und Datenbank; die Variable gibt ihm nichts, das
    er nicht schon haette. Sie sagt nur ehrlich, wem der Server gehoert.
    Nexview macht das an anderer Stelle genauso (``NEXVIEW_SECRET_KEY``).
    """
    name = (get_settings().betreiber or "").strip()
    return name or None


def festgelegt_in_der_umgebung() -> bool:
    return aus_der_umgebung() is not None


# ---------------------------------------------------------------------------
# Setzen
# ---------------------------------------------------------------------------


def _setzen(db: Session, ziel: User) -> None:
    """Den Haken auf genau ein Konto legen - erst abraeumen, dann setzen.

    ⚠️ **Die Reihenfolge ist nicht egal.** Der teil-eindeutige Index laesst
    keine zwei Traeger zu; wer erst setzt und dann abraeumt, laeuft mitten im
    Vorgang in einen IntegrityError. Das ``flush`` dazwischen sorgt dafuer,
    dass die Datenbank das Abraeumen wirklich schon gesehen hat, wenn das
    Setzen ankommt - ohne es haelt SQLAlchemy beides bis zum Commit zurueck
    und schickt es in einer Reihenfolge, die sie selbst waehlt.
    """
    for bisher in db.scalars(select(User).where(User.is_betreiber.is_(True))):
        if bisher.id != ziel.id:
            bisher.is_betreiber = False
    db.flush()
    ziel.is_betreiber = True
    db.flush()


def ernennen(db: Session, ziel: User, grund: str) -> None:
    """Den Haken vergeben - fuer die Einrichtung, das Update und die Umgebung.

    Ausdruecklich **nicht** der Weg fuer die Uebergabe: Die hat mit
    ``uebergeben`` ihre eigenen Regeln. Hier steht kein "darf er das?", weil
    keiner der drei Aufrufer einen Benutzer vor sich hat, den man fragen
    koennte.
    """
    _setzen(db, ziel)
    logger.info("Owner account is now %r (%s)", ziel.username, grund)


def uebergeben(db: Session, von: User, an: User) -> None:
    """Der Traeger gibt weiter - der einzige Weg, auf dem der Haken wandert.

    ⚠️ **Nur der Traeger selbst.** Kein Administrator kann ihn sich holen, und
    der bisherige Traeger kann ihn sich nach der Uebergabe nicht
    zurueckholen - danach ist er ein gewoehnlicher Administrator und faellt
    unter dieselbe Sperre wie jeder andere. Genau das macht die Warnung vor
    der Bestaetigung noetig.
    """
    if not ist_betreiber(von):
        raise BetreiberFehler(
            "betreiber_not_owner",
            "Nur der Betreiber selbst kann den Betreiber übergeben.",
            403,
        )
    if festgelegt_in_der_umgebung():
        # ⚠️ **Verweigern statt spaeter rueckgaengig machen.** Die Variable
        # wird bei *jedem* Start gelesen. Liesse man die Uebergabe zu, spraenge
        # der Haken beim naechsten Neustart des Behaelters zurueck - die
        # Uebergabe haette gehalten, bis zum naechsten Update, und niemand
        # haette verstanden warum. Lieber vorher sagen, dass Nexview diese
        # Entscheidung gar nicht annehmen kann.
        raise BetreiberFehler(
            "betreiber_von_umgebung",
            "Der Betreiber ist in der Umgebung festgelegt (NEXVIEW_BETREIBER) "
            "und lässt sich hier nicht ändern.",
            409,
        )
    if an.id == von.id:
        raise BetreiberFehler(
            "betreiber_selbst",
            "Du bist bereits der Betreiber.",
        )
    # Kinderkonten faengt die Rollenpruefung mit ab - ein Kind ist nie
    # ``Role.admin``. Trotzdem steht es hier eigens da: Wer die Zeile spaeter
    # liest, soll nicht erst nachschlagen muessen, ob dieser Fall bedacht war.
    if an.is_child or an.role != Role.admin:
        raise BetreiberFehler(
            "betreiber_nur_admin",
            "Der Betreiber kann nur an einen Administrator übergeben werden.",
        )
    if not an.is_active:
        # Ein stillgelegtes Konto kaeme nicht herein und koennte den Haken
        # nie weitergeben - das waere dieselbe Sackgasse wie ein
        # ausgesperrter Betreiber, nur schneller erreicht.
        raise BetreiberFehler(
            "betreiber_ziel_inaktiv",
            "Ein deaktiviertes Konto kann den Betreiber nicht übernehmen.",
        )

    _setzen(db, an)
    logger.info("Owner account handed over from %r to %r", von.username, an.username)


# ---------------------------------------------------------------------------
# Beim Start
# ---------------------------------------------------------------------------


def beim_start(db: Session) -> None:
    """Den Haken beim Hochfahren bestimmen - Umgebung zuerst, dann das Update.

    Zwei Faelle, in dieser Reihenfolge:

    1. ``NEXVIEW_BETREIBER`` ist gesetzt. Dann gewinnt die Variable, immer und
       bei jedem Start. Nennt sie ein Konto, das es nicht gibt, passiert
       **nichts** ausser einer Warnung - Nexview legt daraus kein Konto an.
       Ein Konto aus einer Umgebungsvariable heraus zu erschaffen waere ein
       zweiter Weg, Benutzer anzulegen, und ein Vertipper erzeugte ein
       Geisterkonto mit Administratorrechten.
    2. Niemand traegt den Haken, und es gibt Konten. Das ist die bestehende
       Installation nach dem Update. Sie bekommt den aeltesten aktiven
       Administrator - das ist in aller Regel das Konto aus dem
       Einrichtungsassistenten. Gibt es keinen, bleibt der Haken unvergeben
       und die Uebersicht sagt das sichtbar.

    ⚠️ **Fall 2 laeuft nur, wenn niemand ihn traegt.** Sonst machte jeder
    Neustart eine Uebergabe rueckgaengig.
    """
    name = aus_der_umgebung()
    if name:
        ziel = db.scalar(select(User).where(User.username == name))
        if ziel is None:
            logger.warning(
                "NEXVIEW_BETREIBER names %r, but there is no such account - ignored. "
                "No account is created from this variable.",
                name,
            )
        elif ziel.is_betreiber:
            # Schon richtig - nichts zu tun und nichts zu protokollieren. Sonst
            # stuende die Zeile bei jedem Start im Log und saehe nach einem
            # Vorgang aus, wo keiner ist.
            return
        else:
            ernennen(db, ziel, "set by NEXVIEW_BETREIBER")
            db.commit()
            return

    if traeger(db) is not None:
        return

    aeltester = db.scalar(
        select(User)
        .where(User.role == Role.admin, User.is_active.is_(True))
        .order_by(User.id)
    )
    if aeltester is None:
        # Kein Grund zur Panik und kein Grund zum Raten: Eine frische
        # Installation hat vor der Einrichtung noch niemanden, und ein Haus
        # ohne aktiven Administrator hat groessere Sorgen.
        if db.scalar(select(User.id).limit(1)) is not None:
            logger.warning(
                "No owner account and no active administrator - the owner flag stays "
                "unassigned. It is shown as such in the user list."
            )
        return

    ernennen(db, aeltester, "oldest active administrator, assigned on upgrade")
    db.commit()


def nach_dem_einspielen(db: Session, username: str | None) -> None:
    """Nach einer eingespielten Sicherung: der Haken bleibt beim jetzigen Traeger.

    ⚠️ **Der Haken ist der eine Wert, der die Zeitmaschine nicht mitmacht.**
    Eine Sicherung kopiert die ganze Datenbank, er reiste also mit - und ein
    zweiter Administrator koennte eine Uebergabe rueckgaengig machen, indem er
    einen Stand von vorher einspielt. Genau der Angriff, gegen den der Haken
    steht, ginge dann ueber die Sicherungsseite weiter.

    Deshalb wird vor dem Einspielen gemerkt, wer ihn traegt, und danach wieder
    hergestellt. Das ist eine bewusste Ausnahme von "eine Sicherung stellt den
    Stand von damals her", und sie gehoert erklaert, wo jemand sie sieht.

    ``username`` statt Kennung: Die Kennungen der eingespielten Datenbank sind
    andere. Steht das Konto in der Sicherung nicht (oder ist es dort kein
    aktiver Administrator), bleibt der Haken unvergeben - mit Warnung. Ihn
    einem beliebigen anderen zu geben waere geraten.
    """
    if festgelegt_in_der_umgebung():
        # Die Variable hat schon in ``beim_start`` entschieden und gewinnt
        # ueberall. Haette sie hier nicht Vorrang, koennte ein Einspielen den
        # Nothammer aushebeln - genau in der Lage, in der man ihn braucht.
        return
    if not username:
        # Vor der Einrichtung gibt es keinen Traeger, den man erhalten
        # koennte - dann gilt der Stand aus der Sicherung, und das ist richtig.
        return

    ziel = db.scalar(select(User).where(User.username == username))
    if ziel is None or ziel.role != Role.admin or not ziel.is_active:
        for bisher in db.scalars(select(User).where(User.is_betreiber.is_(True))):
            bisher.is_betreiber = False
        db.commit()
        logger.warning(
            "Owner %r is not an active administrator in the restored backup - the owner "
            "flag stays unassigned. Set NEXVIEW_BETREIBER to reassign it.",
            username,
        )
        return

    if ziel.is_betreiber:
        return
    ernennen(db, ziel, "kept across the restored backup")
    db.commit()
