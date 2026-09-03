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

import math
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

#: Felder, deren Werte feststehen. Was nicht darin steht, ist ein Tippfehler -
#: und ein Tippfehler ergaebe eine Regel, die still nie greift. Genau den
#: Zustand soll ``bedingungen_pruefen`` verhindern.
#:
#: ⚠️ Anfangs stand hier nur ``bestand``. ``typ`` und ``qualitaet`` sind
#: genauso geschlossen, wurden aber nicht geprueft: ``{"feld": "typ",
#: "werte": ["hoerspiel"]}`` ging durch und ergab eine Regel, die nichts tat.
#: ``genre`` und ``sprache`` bleiben offen - deren Werte kommen von TMDB und
#: aendern sich, ohne dass jemand hier etwas nachtraegt.
GESCHLOSSENE_WERTE: dict[str, frozenset[str]] = {
    "typ": frozenset({"movie", "tv"}),
    "qualitaet": frozenset({"hd", "uhd"}),
    # "nichts" ist ein Wert und keine Luecke: Wer eine Regel auf "liegt gar
    # nicht vor" stellen will, soll das koennen.
    "bestand": frozenset({"hd", "uhd", "nichts"}),
}

#: Rueckwaertskompatibler Name - einige Stellen nennen ihn noch einzeln.
BESTAND_WERTE = GESCHLOSSENE_WERTE["bestand"]


#: Der erlaubte Bereich je Zahlenfeld, und - wo sinnvoll - die einzelnen
#: Stufen, aus denen sich waehlen laesst.
#:
#: ⚠️ **Ohne das nahm der Server "Bewertung von 17 bis 14" an.** Die Einheit
#: daneben sagte "von 10", und die Grenze war trotzdem 17. Wer eine Regel so
#: baut, merkt nie etwas: Sie trifft auf nichts zu und tut still gar nichts.
#: Die Oberflaeche baut aus denselben Angaben ihre Auswahlfelder - eine zweite
#: Liste dort waere die naechste, die veraltet.
BEREICHE: dict[str, dict] = {
    # TMDB bewertet von 0 bis 10. Ganze Schritte reichen fuer eine Regel; wer
    # 7,3 von 7,4 unterscheiden will, misst Rauschen.
    "bewertung": {"min": 0, "max": 10, "stufen": [float(n) for n in range(11)]},
    # Die FSK-Stufen, und nur die. Etwas dazwischen gibt es nicht.
    "altersfreigabe": {"min": 0, "max": 18, "stufen": [0.0, 6.0, 12.0, 16.0, 18.0]},
    # Kein "stufen": Jahre und Minuten sind zu viele fuer eine Liste, und man
    # tippt sie ohnehin lieber.
    "jahr": {"min": 1870, "max": 2100},
    "laufzeit": {"min": 0, "max": 1000},
    "stimmen": {"min": 0, "max": 1000000},
}


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
                if wert is None:
                    continue
                if isinstance(wert, bool) or not isinstance(wert, (int, float)):
                    raise RegelFehler(f"{feld}: {wert!r} ist keine Zahl.")
                # ⚠️ **``NaN`` und ``Infinity`` kommen durch JSON herein.**
                # Python nimmt beide klaglos an, ``isinstance(nan, float)`` ist
                # wahr, und **jeder** Vergleich mit ``NaN`` ist falsch - also
                # auch ``von >= bis``. Eine Regel mit ``von: NaN`` ging so
                # durch und traf danach auf **alles** zu, waehrend die
                # Oberflaeche "von: —" anzeigte. Eine Grenze, die auf alles
                # passt und die niemand sehen kann.
                if not math.isfinite(wert):
                    raise RegelFehler(
                        f"{feld}: {wert!r} ist keine Grenze, mit der sich rechnen lässt."
                    )
                bereich = BEREICHE.get(feld)
                if bereich and not (bereich["min"] <= wert <= bereich["max"]):
                    raise RegelFehler(
                        f"{feld}: {wert:g} liegt außerhalb von "
                        f"{bereich['min']:g} bis {bereich['max']:g}."
                    )
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
            erlaubt = GESCHLOSSENE_WERTE.get(feld)
            if erlaubt is not None and not set(werte) <= erlaubt:
                falsch = sorted(set(werte) - erlaubt)
                raise RegelFehler(
                    f"{feld}: {falsch} gibt es nicht - erlaubt sind {sorted(erlaubt)}."
                )
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
