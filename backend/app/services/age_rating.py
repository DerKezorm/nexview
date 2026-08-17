"""Altersfreigaben verschiedener Laender vergleichbar machen.

TMDB liefert die Freigabe so, wie das jeweilige Land sie schreibt: "FSK 12" in
Deutschland, "PG-13" in den USA, "MA15+" in Australien, "K-12" in Finnland.
Vergleichen laesst sich das erst, wenn daraus eine Zahl wird - das
**Mindestalter**.

Die Tabelle unten ist nicht geraten: sie entstand aus den Bezeichnungen, die in
den zwischengespeicherten TMDB-Daten dieser Installation tatsaechlich vorkamen
(1477 Titel, 501 verschiedene Land/Bezeichnung-Paare).

Zwei Wege zur Zahl, in dieser Reihenfolge:

1. **Buchstaben-Tabelle je Land** - fuer alles, worin keine Zahl steckt
   ("G", "PG", "TP", "AL", "Btl"). Sie muss je Land stehen, weil dieselbe
   Bezeichnung anderswo etwas anderes heisst: "R" bedeutet in den USA "ab 17",
   in Kanada "ab 18".
2. **Zahl aus der Bezeichnung** - deckt den grossen Rest ab: "12", "12A",
   "K-12", "M/12", "VM12", "MA15+", "R18+", "13A", "14 anos", "Fraan 15 aar",
   "15세 이상 관람가". Alles Faelle, fuer die eine eigene Tabelle nur eine
   weitere Stelle zum Vergessen waere.

Was nirgends passt, gilt als **unbekannt** - niemals als 0. "NR" heisst "nicht
eingestuft" und darf auf keinen Fall als "ab 0" durchgehen.
"""

from __future__ import annotations

import re
from typing import Any

# Bezeichnungen, die ausdruecklich "keine Einstufung" bedeuten. Sie werden vor
# allen anderen Regeln geprueft, damit sie nicht versehentlich in der
# Zahlen-Rueckfallebene landen.
OHNE_ANGABE = {"NR", "UNRATED", "NOT RATED", "UNKNOWN", "N/A", "-", "?"}

# Hoehere Zahlen sind keine Altersangaben mehr, sondern Zaehlwerke oder
# Jahreszahlen, die versehentlich in der Bezeichnung stehen.
HOECHSTES_ALTER = 21

# Beratende Einstufungen ("Parental Guidance") sind keine Altersgrenze, sondern
# ein Hinweis an die Eltern. Sie hier bei 6 einzuordnen ist eine Setzung - sie
# entspricht in etwa der FSK 6 und sorgt dafuer, dass eine Grenze von 0 wirklich
# nur reine Kinderfilme durchlaesst.
BERATEND = 6

_US = {
    "G": 0,
    "TV-G": 0,
    "TV-Y": 0,
    "PG": BERATEND,
    "TV-PG": BERATEND,
    # In den USA die Grenze, ab der man ohne Begleitung hineinkommt.
    "R": 17,
    "TV-MA": 17,
}

BUCHSTABEN: dict[str, dict[str, int]] = {
    "US": _US,
    # Puerto Rico und die Amerikanischen Jungferninseln verwenden dieselben
    # Bezeichnungen wie die USA - in den Daten stehen dort "PG", "R", "TV-MA".
    "PR": _US,
    "VI": _US,
    "GB": {"U": 0, "PG": BERATEND},
    "IE": {"G": 0, "PG": BERATEND},
    "AU": {"G": 0, "PG": BERATEND, "M": 15},
    "NZ": {"G": 0, "PG": BERATEND, "M": 16},
    "CA": {"G": 0, "C": 0, "EXEMPT": 0, "PG": BERATEND, "R": 18},
    "SG": {"G": 0, "PG": BERATEND},
    "JP": {"G": 0},
    "KR": {"ALL": 0},
    "FR": {"U": 0, "TP": 0},
    "ES": {"APTA": 0, "TP": 0, "A": 0, "AI": 0},
    "NL": {"AL": 0},
    "BR": {"L": 0},
    "IT": {"T": 0, "BA": 0},
    "PT": {"T": 0, "PUBLICOS": 0, "PÚBLICOS": 0},
    "FI": {"S": 0},
    "SE": {"BTL": 0, "BARNTILLATEN": 0, "BARNTILLÅTEN": 0},
    "HU": {"KN": 0, "X": 18},
    "TR": {"GENEL IZLEYICI": 0, "GENEL İZLEYICI": 0, "GENEL İZLEYICI KITLESI": 0},
    # Mexiko stuft rein alphabetisch ein, ohne eine einzige Zahl.
    "MX": {"A": 0, "AA": 0, "B": 12, "C": 18, "D": 18},
    # Hongkong: Kategorie I frei, IIA nichts fuer Kinder, IIB nichts fuer
    # Jugendliche, III nur ab 18.
    "HK": {"I": 0, "IIA": BERATEND, "IIB": 15, "III": 18},
    "IN": {"U": 0, "UA": BERATEND, "A": 18},
    "IL": {"ALL": 0},
    "CZ": {"U": 0},
    "TH": {"G": 0},
    "PH": {"G": 0, "PG": BERATEND},
    "AR": {"ATP": 0},
    "RO": {"AG": 0, "AP": BERATEND},
    "GR": {"K": 0},
    "ID": {"SU": 0, "D": 17},
    "CL": {"TE": 0},
    "TW": {"普遍級": 0, "保護級": BERATEND, "輔導級": 15, "限制級": 18},
}

# Bewusst *nicht* eingetragen: BG (A/B/C/D), MO (A/B/C) und einige weitere
# kleine Maerkte. Deren Buchstaben liessen sich nur raten, und eine falsch
# geratene Stufe waere in einer Alterssperre schlimmer als eine fehlende: die
# fehlende faellt nur bei der "strengste aller Laender"-Regel weg, die falsche
# wuerde aktiv das Falsche erlauben oder sperren.

_ZAHL = re.compile(r"\d+")


def stufe(land: str, bezeichnung: str) -> int | None:
    """Mindestalter einer einzelnen Einstufung, oder None wenn unbekannt."""
    text = (bezeichnung or "").strip().upper()
    if not text or text in OHNE_ANGABE:
        return None

    tabelle = BUCHSTABEN.get((land or "").upper(), {})
    if text in tabelle:
        return tabelle[text]

    treffer = _ZAHL.search(text)
    if treffer:
        alter = int(treffer.group())
        if alter <= HOECHSTES_ALTER:
            return alter

    return None


def alle_stufen(detail: dict[str, Any], media_type: str) -> dict[str, int]:
    """Mindestalter je Land, so weit es sich bestimmen laesst."""
    gefunden: dict[str, int] = {}

    if media_type == "movie":
        for eintrag in detail.get("release_dates", {}).get("results", []):
            land = eintrag.get("iso_3166_1") or ""
            for veroeffentlichung in eintrag.get("release_dates", []):
                alter = stufe(land, veroeffentlichung.get("certification") or "")
                if alter is None:
                    continue
                # Innerhalb eines Landes kann es mehrere Eintraege geben (Kino,
                # Datentraeger, Fernsehen). Die strengste zaehlt.
                gefunden[land] = max(gefunden.get(land, alter), alter)
        return gefunden

    for eintrag in detail.get("content_ratings", {}).get("results", []):
        land = eintrag.get("iso_3166_1") or ""
        alter = stufe(land, eintrag.get("rating") or "")
        if alter is not None:
            gefunden[land] = alter
    return gefunden


def mindestalter(detail: dict[str, Any], media_type: str, region: str) -> int | None:
    """Das Mindestalter, an dem sich die Sperre misst.

    Das eigene Land gewinnt: wer in Deutschland sitzt, soll nach FSK beurteilt
    werden und nicht danach, dass irgendein anderes Land denselben Film
    strenger sieht.

    Fehlt fuer das eigene Land eine Einstufung - was oft vorkommt, gemessen bei
    31 % der Filme und 43 % der Serien -, zaehlt die **strengste** aller
    Laender. Das ist bewusst die vorsichtige Richtung: eine Alterssperre, die
    im Zweifel zu viel durchlaesst, waere keine.

    None heisst "nirgends eingestuft". Was damit geschieht, entscheidet
    ``erlaubt`` - nicht diese Funktion.
    """
    stufen = alle_stufen(detail, media_type)
    if not stufen:
        return None

    eigenes = stufen.get((region or "").upper())
    if eigenes is not None:
        return eigenes

    return max(stufen.values())


def erlaubt(
    detail: dict[str, Any] | None,
    media_type: str,
    grenze: int | None,
    region: str,
    *,
    unbewertet_verbergen: bool = True,
) -> bool:
    """Darf jemand mit dieser Altersgrenze den Titel sehen?

    Ohne Grenze (``None``) ist alles erlaubt - das ist der Normalfall, und die
    Pruefung kostet dann nichts.

    Der zweite Fall sind Titel **ohne jede Einstufung**. Standardmaessig gilt
    "kein Nachweis, kein Zutritt": sie bleiben verborgen, sonst waere die
    Sperre genau dort wirkungslos, wo niemand weiss, was drinsteckt.

    Das hat allerdings einen gemessenen Preis: neue Titel sind meist noch
    nirgends eingestuft, und die Entdecken-Seite schrumpfte fuer ein
    beschraenktes Konto von 20 auf 2 Eintraege. Deshalb laesst sich das je
    Benutzer umstellen - bei einem 16-Jaehrigen mag es vertretbar sein, bei
    einem 6-Jaehrigen nicht.

    Fehlende Detaildaten (``None``) zaehlen wie "keine Einstufung": auch dann
    weiss man nichts ueber den Titel.
    """
    if grenze is None:
        return True

    alter = None if detail is None else mindestalter(detail, media_type, region)
    if alter is None:
        return not unbewertet_verbergen

    return alter <= grenze
