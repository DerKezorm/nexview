"""Regeln, die ueber eine Anfrage entscheiden.

Eine Regel ist eine Liste von Bedingungen, alle mit **UND** verknuepft, und
eine Folge: freigeben oder ablehnen. Nexview geht die Regeln nach ``position``
durch und nimmt die **erste**, die passt.

⚠️ **Wo das hier greift.** Ganz am Ende von ``requests_service.create_request``,
an der Stelle, an der bisher allein ``user.auto_approve_for`` stand. Alles davor
laeuft unveraendert und **vorher**: Folgen-Pakete, 4K-Recht, Sperrliste,
Radarr/Sonarr, laufende Anfragen, Bibliothek, Medienserver, Qualitaetsprofil,
Zielordner und **das Kontingent**. Eine Regel kann also nichts durchwinken, was
aus einem anderen Grund schon gescheitert ist.

Das ist die bewusste Entscheidung und nicht der bequeme Weg: Liesse man eine
Regel am Kontingent vorbei, haette dieselbe Anfrage je nach Regellage ein
anderes Ergebnis, und das Kontingent waere keine Grenze mehr, sondern eine
Empfehlung.

⚠️ **Drei Dinge kann eine Regel nicht erreichen**, und alle drei liegen
ausserhalb dieser Datei:

* **Den Altersfilter.** Der wirkt beim *Anzeigen*. Was ein Konto nicht sehen
  darf, kann es nicht anfragen - es entsteht gar kein Vorgang, den eine Regel
  bewerten koennte.
* **Die Entscheidung der Eltern.** Ein Kinderwunsch ist keine Anfrage. Erst
  wenn ein Elternteil freigibt, laeuft ``create_request`` - mit dem Elternteil
  als Anfragendem, und damit auch durch die Regeln.
* **Das Kontingent.** Siehe oben.

⚠️ **Was fehlt, laesst die Regel scheitern.** Sagt der Titel zu einem Feld
nichts - TMDB kennt keine Bewertung, keine Altersfreigabe -, dann trifft die
Bedingung **nicht** zu. Andersherum waere es gefaehrlich: Eine Regel
"Bewertung ab 8 -> freigeben" wuerde sonst bei jedem Titel greifen, dessen
Bewertung gerade unbekannt ist. So passiert im Zweifel nichts, und die Anfrage
nimmt den gewohnten Weg.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MediaType, QualityTier, Regel, RegelEntscheidung, User

# ---------------------------------------------------------------------------
# Die Felder, ueber die sich Bedingungen stellen lassen
# ---------------------------------------------------------------------------

#: Zahlenfelder: ``von`` schliesst ein, ``bis`` schliesst aus.
#:
#: ⚠️ Das ist nicht Geschmack, sondern die Voraussetzung dafuer, dass sich
#: "unter 5" und "ab 5" **nicht** ueberschneiden. Mit zwei einschliessenden
#: Grenzen meldete die Oberflaeche einen Widerspruch, den es nicht gibt.
ZAHLENFELDER = frozenset({
    "bewertung",
    "stimmen",
    "jahr",
    "laufzeit",
    "altersfreigabe",
})

#: Mengenfelder: die Bedingung trifft zu, wenn der Wert des Titels in der Menge
#: liegt. Mehrere Werte wirken darin wie ein ODER - das ist das einzige ODER,
#: das es gibt, und es bleibt auf ein Feld beschraenkt.
MENGENFELDER = frozenset({
    "typ",
    "genre",
    "sprache",
    "qualitaet",
    "bestand",
})

# ⚠️ **„Laeuft schon bei Netflix" fehlt mit Absicht.** Die Angabe steht nicht
# am Titel, sie kostet je Anfrage eine TMDB-Abfrage (``watch/providers``), und
# vorher waere zu entscheiden, **wessen** Abos zaehlen: die des Anfragenden
# (dann ist es eine Aussage ueber ihn) oder die des Hauses (die es als Begriff
# noch nicht gibt). Ein halb gedachtes Feld in einer Regel, die ablehnt, ist
# schlimmer als kein Feld.

FELDER = ZAHLENFELDER | MENGENFELDER

#: Was ``bestand`` annehmen kann. "nichts" ist ein Wert und keine Luecke: Wer
#: eine Regel auf "liegt gar nicht vor" stellen will, soll das koennen.
BESTAND_WERTE = frozenset({"hd", "uhd", "nichts"})


class RegelFehler(ValueError):
    """Eine Regel ist so nicht speicherbar."""


def bedingungen_pruefen(bedingungen: list | None) -> list[dict]:
    """Die Bedingungen einer Regel auf Form pruefen, bevor sie gespeichert wird.

    ⚠️ **Das gehoert in den Dienst und nicht nur ins Schema.** Eine Bedingung
    mit einem unbekannten Feld wuerde bei der Auswertung stillschweigend nie
    zutreffen - die Regel saehe vorhanden aus und taete nichts. Lieber beim
    Speichern scheitern.
    """
    if not bedingungen:
        raise RegelFehler("Eine Regel ohne Bedingung träfe auf alles zu.")

    sauber: list[dict] = []
    gesehen: set[str] = set()
    for eintrag in bedingungen:
        if not isinstance(eintrag, dict):
            raise RegelFehler("Bedingung ist kein Objekt.")
        feld = eintrag.get("feld")
        if feld not in FELDER:
            raise RegelFehler(f"Unbekanntes Feld: {feld!r}")
        # ⚠️ Zweimal dasselbe Feld waere ein UND zweier Bereiche - und der
        # engere gewinnt immer. Das laesst sich als eine Bedingung schreiben,
        # und zwei davon liest niemand richtig.
        if feld in gesehen:
            raise RegelFehler(f"Das Feld {feld!r} steht zweimal in derselben Regel.")
        gesehen.add(feld)

        if feld in ZAHLENFELDER:
            von = eintrag.get("von")
            bis = eintrag.get("bis")
            for wert in (von, bis):
                if wert is not None and not isinstance(wert, (int, float)):
                    raise RegelFehler(f"{feld}: {wert!r} ist keine Zahl.")
            if von is None and bis is None:
                raise RegelFehler(f"{feld}: weder Unter- noch Obergrenze.")
            if von is not None and bis is not None and von >= bis:
                raise RegelFehler(
                    f"{feld}: „von {von}“ liegt nicht unter „bis {bis}“ - "
                    "die Bedingung träfe auf nichts zu."
                )
            sauber.append({"feld": feld, "von": von, "bis": bis})
        else:
            werte = eintrag.get("werte")
            if not isinstance(werte, list) or not werte:
                raise RegelFehler(f"{feld}: keine Auswahl getroffen.")
            werte = [str(w) for w in werte]
            if feld == "bestand" and not set(werte) <= BESTAND_WERTE:
                raise RegelFehler(f"bestand: erlaubt sind {sorted(BESTAND_WERTE)}.")
            sauber.append({"feld": feld, "werte": werte})

    return sauber


# ---------------------------------------------------------------------------
# Der Titel, gegen den geprueft wird
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Titel:
    """Alles, was eine Regel ueber die Anfrage wissen kann.

    ``None`` und leere Mengen heissen ausdruecklich **unbekannt**, nicht
    "trifft nicht zu" - siehe den Kopf dieser Datei.
    """

    typ: MediaType
    qualitaet: str
    bestand: str = "nichts"
    genres: tuple[int, ...] = ()
    bewertung: float | None = None
    stimmen: int | None = None
    jahr: int | None = None
    laufzeit: int | None = None
    sprache: str | None = None
    altersfreigabe: int | None = None

    def wert(self, feld: str):
        """Was der Titel zu einem Feld sagt. ``None`` heisst: nichts."""
        if feld == "typ":
            return [self.typ.value]
        if feld == "genre":
            return [str(g) for g in self.genres] or None
        if feld == "sprache":
            return [self.sprache] if self.sprache else None
        if feld == "qualitaet":
            return [self.qualitaet]
        if feld == "bestand":
            return [self.bestand]
        return getattr(self, feld, None)


@dataclass(frozen=True)
class Ergebnis:
    """Was die Regeln entschieden haben."""

    regel: Regel
    freigeben: bool
    hausbestand: bool
    begruendung: str
    trotzdem_fragen: bool


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------


def passt(regel: Regel, titel: Titel) -> bool:
    """Trifft jede Bedingung dieser Regel auf den Titel zu?"""
    for bedingung in regel.bedingungen or []:
        feld = bedingung.get("feld")
        wert = titel.wert(feld)
        if wert is None:
            return False

        if feld in ZAHLENFELDER:
            von, bis = bedingung.get("von"), bedingung.get("bis")
            if von is not None and wert < von:
                return False
            if bis is not None and wert >= bis:
                return False
        else:
            erlaubt = set(bedingung.get("werte") or [])
            if not erlaubt & set(wert):
                return False

    return bool(regel.bedingungen)


def geordnet(db: Session, *, nur_aktive: bool = False) -> list[Regel]:
    """Die Regeln in der Reihenfolge, in der sie gelten.

    ⚠️ ``id`` als zweites Kriterium ist kein Beiwerk: Bei gleicher
    ``position`` waere die Reihenfolge sonst die der Datenbank, und die aendert
    sich. Bei Regeln ist die Reihenfolge aber Teil der Bedeutung.
    """
    frage = select(Regel).order_by(Regel.position, Regel.id)
    if nur_aktive:
        frage = frage.where(Regel.aktiv.is_(True))
    return list(db.scalars(frage))


def entscheiden(db: Session, user: User, titel: Titel) -> Ergebnis | None:
    """Welche Regel greift? ``None`` heisst: keine, es gilt das Konto.

    ⚠️ **Entscheider und Administratoren sind ausgenommen** - wie bei der
    Sperrliste, und aus demselben Grund: Die Regeln sind ihre eigene
    Entscheidung, sie sollen die anderen bremsen, nicht sie selbst. Sonst
    muesste man seine eigene Regel abschalten, um einen Titel zu holen, den man
    bewusst will, und wuerde vergessen, sie wieder einzuschalten.
    """
    if user.can_approve or user.is_admin:
        return None

    for regel in geordnet(db, nur_aktive=True):
        if not passt(regel, titel):
            continue
        frei = regel.entscheidung == RegelEntscheidung.freigeben
        return Ergebnis(
            regel=regel,
            freigeben=frei,
            hausbestand=frei and regel.hausbestand,
            begruendung=(regel.begruendung or "").strip(),
            trotzdem_fragen=(not frei) and regel.trotzdem_fragen,
        )
    return None


def stufe_von(tier: QualityTier) -> str:
    """Die Qualitaetsstufe so, wie eine Bedingung sie nennt."""
    return "uhd" if tier == QualityTier.uhd else "hd"
