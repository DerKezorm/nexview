"""Aus Alltagsantworten einen Bauplan fuer Radarr/Sonarr machen.

Hier steckt die eigentliche Arbeit des Qualitaets-Assistenten: **die
Zuordnungstabelle**. Der Code drumherum ist mechanisch - welche Antwort auf
welchen TRaSH-Baustein fuehrt, ist dagegen eine Entscheidung, und sie steht
bewusst als Tabelle hier und nicht verstreut in Bedingungen.

⚠️ **Kein Ermessen zur Laufzeit.** Dieselben Antworten ergeben zwingend
denselben Bauplan. Was nicht in der Tabelle steht, fuehrt zu einem Fehler und
nicht zu einem Notbehelf - ein halbgar gebautes Profil faellt niemandem auf,
bis die falschen Dateien im Regal liegen.

Was die Guides liefern und was hier entschieden wird:

* **Von TRaSH:** die Erkennungsmuster, ihre Punktwerte (je Punkte-Satz, fuer
  deutsche Profile etwa ``german``), die fertigen Profile und die Gruppen.
* **Hier entschieden:** welches Profil zu welchen Antworten passt, welche
  Gruppen dazugehoeren (das steht bei TRaSH nur im Fliesstext der Anleitung,
  nicht in den Daten), und wie eigene Sprachmuster gebaut werden.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

DATEN = Path(__file__).resolve().parent.parent / "daten"

# Unsere Sprachkuerzel -> wie die Sprache in Radarr/Sonarr heisst. Die Nummer
# holen wir uns von der Instanz; sie ist nicht ueber alle Fassungen gleich.
SPRACHNAMEN = {
    "de": "German",
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "tr": "Turkish",
}

# Fuer diese Sprachen hat TRaSH eine eigene Profilfamilie mit Raengen und
# Schrott-Filtern. Alle uebrigen bekommen die einfache Spracherkennung.
#
# ⚠️ **Franzoesisch fehlt hier absichtlich.** TRaSH hat zwar eine franzoesische
# Familie, aber in drei Fassungen (``multi-vf``, ``multi-vo``, ``vostfr``) - sie
# unterscheiden sich darin, ob die franzoesische Synchronfassung, der Originalton
# oder Untertitel gewollt sind. Diese Frage stellt der Assistent nicht, und eine
# der drei blind zu waehlen waere geraten. Bis die Frage da ist, bekommt
# Franzoesisch die einfache Spracherkennung wie jede andere Sprache auch.
FAMILIENSPRACHEN = {"de": "german"}

#: Antwort -> TRaSH-Profil. Der Schluessel ist (Dienst, Familie, Aufloesung, Quelle).
#:
#: ⚠️ **Sonarr hat in der Standard-Familie keine Bluray-Encode-Profile** - die
#: Anleitung ist dort auf WEB ausgelegt. "Beste Encodes" landet deshalb auf dem
#: WEB-Profil; das ist keine Nachlaessigkeit, sondern der Stand der Quelle.
PROFILDATEI: dict[tuple[str, str, str, str], str] = {
    ("radarr", "standard", "1080p", "encodes"): "hd-bluray-web",
    ("radarr", "standard", "1080p", "remux"): "remux-web-1080p",
    ("radarr", "standard", "1080p", "web"): "web-1080p",
    ("radarr", "standard", "2160p", "encodes"): "uhd-bluray-web",
    ("radarr", "standard", "2160p", "remux"): "remux-web-2160p",
    ("radarr", "standard", "2160p", "web"): "web-2160p",
    ("radarr", "german", "1080p", "encodes"): "german-hd-bluray-web",
    ("radarr", "german", "1080p", "remux"): "german-hd-remux-web",
    ("radarr", "german", "1080p", "web"): "german-hd-bluray-web",
    ("radarr", "german", "2160p", "encodes"): "german-uhd-bluray-web",
    ("radarr", "german", "2160p", "remux"): "german-uhd-remux-web",
    ("radarr", "german", "2160p", "web"): "german-uhd-bluray-web",
    ("sonarr", "standard", "1080p", "encodes"): "web-1080p",
    ("sonarr", "standard", "1080p", "remux"): "remux-web-1080p",
    ("sonarr", "standard", "1080p", "web"): "web-1080p",
    ("sonarr", "standard", "2160p", "encodes"): "web-2160p",
    ("sonarr", "standard", "2160p", "remux"): "remux-web-2160p",
    ("sonarr", "standard", "2160p", "web"): "web-2160p",
    ("sonarr", "german", "1080p", "encodes"): "german-hd-bluray-web",
    ("sonarr", "german", "1080p", "remux"): "german-hd-remux-web",
    ("sonarr", "german", "1080p", "web"): "german-hd-bluray-web",
    ("sonarr", "german", "2160p", "encodes"): "german-uhd-bluray-web",
    ("sonarr", "german", "2160p", "remux"): "german-uhd-remux-web",
    ("sonarr", "german", "2160p", "web"): "german-uhd-bluray-web",
}

#: Welche Formatgruppen zu einem Profil gehoeren.
#:
#: ⚠️ **Das ist die von Hand gepflegte Stelle.** Bei TRaSH steht diese Zuordnung
#: nur im Fliesstext der Anleitungsseite, nicht in den Daten - sie laesst sich
#: also nicht ableiten. Wer den Schnappschuss nachzieht, prueft sie mit.
# ---------------------------------------------------------------------------
# Der ausfuehrliche Zweig
#
# ⚠️ **Nur Gruppen, die eigene Punkte mitbringen.** Beim Entwerfen dieser
# Fragen habe ich jede Gruppe nachgerechnet, statt sie nach ihrem Namen
# auszuwaehlen - und drei davon verworfen: ``audio-channels`` und
# ``streaming-services-uk`` tragen durchgehend **null** Punkte, eine Frage
# danach haette also nichts bewirkt und trotzdem eine Wirkung versprochen.
#
# ⚠️ **Keine erfundenen Zahlen.** Die Punkte kommen samt und sonders aus den
# Guides. Wo eine Frage eine eigene Gewichtung braeuchte, gibt es sie hier
# nicht - lieber eine Frage weniger als eine Zahl, die niemand begruenden kann.
# ---------------------------------------------------------------------------

#: Antwort im Rezept -> Gruppe, die dann dazukommt.
#:
#: ``dienst`` und ``aufloesung`` schraenken ein, wo die Frage ueberhaupt gilt:
#: Schnittfassungen gibt es nur bei Filmen, SDR nur dort, wo HDR eine Rolle
#: spielt.
AUSFUEHRLICH: tuple[dict[str, Any], ...] = (
    {
        # TrueHD ATMOS 5000 ... DTS-HD MA 2500 ... MP3 250
        "feld": "ton",
        "wenn": "bevorzugen",
        "gruppe": "audio-formats",
    },
    {
        # x265 (HD) und x265 (no HDR/DV), je -10000: TRaSHs "goldene Regel".
        "feld": "x265",
        "wenn": "meiden",
        "gruppe": "optional-golden-rule-hd",
        "aufloesung": ("720p", "1080p"),
    },
    {
        "feld": "x265",
        "wenn": "meiden",
        "gruppe": "optional-golden-rule-uhd",
        "aufloesung": ("2160p",),
    },
    {
        # SDR und SDR (no WEBDL), je -10000 - nur sinnvoll, wo HDR moeglich ist.
        "feld": "sdr",
        "wenn": "meiden",
        "gruppe": "hdr-formats-sdr",
        "aufloesung": ("2160p",),
    },
    {
        # IMAX 800, Hybrid 100, Remaster/Criterion 25 - es gibt keine Serien
        # mit Schnittfassungen, deshalb nur Radarr.
        "feld": "fassungen",
        "wenn": "bevorzugen",
        "gruppe": "optional-movie-versions",
        "dienst": "radarr",
    },
    {
        # WiTH AD/ASL/BASL/BSL, je -10000: Fassungen mit Audiodeskription oder
        # Gebaerdensprache. Wer sie **braucht**, laesst diese Frage auf "egal" -
        # deshalb ist die Voreinstellung nicht "meiden".
        "feld": "barrierefrei",
        "wenn": "meiden",
        "gruppe": "optional-accessibility",
    },
    {
        # German Remux Tier 01 4000 ... German Scene 1700
        "feld": "regionale_gruppen",
        "wenn": "bevorzugen",
        "gruppe": "release-groups-german",
        "familie": "german",
    },
    {
        # German 2160p Booster 9000, German Subbed 9000, 1080p Booster 650
        "feld": "regionale_gruppen",
        "wenn": "bevorzugen",
        "gruppe": "optional-german-miscellaneous",
        "familie": "german",
    },
    {
        # CPNG/Hami/iQIY je 50, andere 0 - lohnt nur, wer dort sucht.
        "feld": "asiatische_dienste",
        "wenn": "dazu",
        "gruppe": "streaming-services-asian",
    },
)


GRUPPEN_IMMER = ("streaming-services-general",)
GRUPPEN_UNERWUENSCHT = {"german": "unwanted-formats-german", "standard": "unwanted-formats"}
GRUPPEN_HDR = ("hdr-formats-hdr", "hdr-formats-dv-boost", "hdr-formats-hdr10-boost")
#: Das Sicherheitsnetz: Dolby Vision ohne Rueckfallebene sieht auf Geraeten
#: ohne DV farbverfaelscht aus. Bei "frei" faellt die Sperre weg.
GRUPPE_DV_OHNE_NETZ = "hdr-formats-dv-webdl"

#: Punktwerte, die Nexview selbst vergibt - TRaSH kennt sie nicht.
PUNKTE_SPRACHE_PFLICHT = 10_000
PUNKTE_SPRACHE_GERN = 500
#: Fuer "alle Pflichtsprachen im selben Release".
#:
#: ⚠️ **Warum so hoch.** Punkte sind in Radarr additiv: Alles, was auf ein
#: Release passt, zaehlt zusammen. Eine Schwelle in der Groessenordnung der
#: uebrigen Werte liesse sich deshalb auch ohne die gewuenschten Tonspuren
#: erreichen - Raenge, Aufloesung und Booster summieren sich auf gut 25 000.
#: Mit 50 000 kommt nur das eine Muster darueber, das *alle* Sprachen zugleich
#: verlangt. Dazu werden die einzelnen Sprachmuster auf 0 gesetzt, sonst
#: entstuende aus ihnen eine zweite Tuer.
PUNKTE_SPRACHE_ALLE = 50_000
#: Ab dieser Punktzahl darf ein Release ueberhaupt geladen werden. Sie liegt
#: genau auf dem Wert einer Pflichtsprache: Ein Release ohne eine davon kommt
#: nie darueber. So empfiehlt es auch die deutsche TRaSH-Anleitung.
MINDESTPUNKTE_PFLICHT = PUNKTE_SPRACHE_PFLICHT


class TrashFehler(Exception):
    """Der Schnappschuss gibt nicht her, was der Bauplan braucht."""


@dataclass(frozen=True)
class Formatwunsch:
    """Ein Erkennungsmuster mit seinem Punktwert - noch ohne Namenspraefix."""

    name: str
    spezifikationen: list[dict]
    punkte: int
    beim_umbenennen: bool = False


@dataclass(frozen=True)
class Bauplan:
    """Was auf einer Instanz entstehen soll - fertig entschieden."""

    profilname: str
    basis: str
    stand: str
    formate: tuple[Formatwunsch, ...]
    merge: tuple[str, ...]
    min_punkte: int
    schluss_punkte: int
    hinweise: tuple[str, ...] = field(default=())


@lru_cache(maxsize=4)
def schnappschuss(dienst: str) -> dict[str, Any]:
    """Der gerade gueltige TRaSH-Stand.

    ⚠️ **Ein geholter Stand schlaegt den mitgelieferten.** Er liegt im
    Datenverzeichnis und ueberlebt damit einen neu gebauten Container; der
    mitgelieferte ist nur die Grundlage fuer den ersten Start und der Rueckfall,
    falls dort nichts liegt.

    Nach dem Holen muss ``schnappschuss.cache_clear()`` gerufen werden, sonst
    arbeitet das Programm bis zum Neustart mit dem alten Stand weiter.
    """
    from ..config import get_settings

    geholt = get_settings().data_dir / "trash" / f"trash-{dienst}.json"
    pfad = geholt if geholt.is_file() else DATEN / f"trash-{dienst}.json"
    if not pfad.is_file():
        raise TrashFehler(f"snapshot for {dienst} missing")
    return json.loads(pfad.read_text(encoding="utf-8"))


def _familie(sprachen: list[str]) -> str:
    """Welche Profilfamilie - entschieden von der ersten passenden Sprache."""
    for code in sprachen:
        if code in FAMILIENSPRACHEN:
            return FAMILIENSPRACHEN[code]
    return "standard"


def _punkte(format_: dict, satz: str) -> int:
    werte = format_.get("trash_scores") or {}
    return int(werte.get(satz, werte.get("default", 0)))


def _felder_als_liste(felder: Any) -> list[dict]:
    """TRaSH schreibt ``fields`` als Objekt, Radarr erwartet eine Liste.

    ⚠️ Ohne diese Umformung antwortet Radarr mit ``400 Bad Request`` und einer
    Meldung ueber ``System.Collections.Generic.List`` - die auf alles Moegliche
    hindeutet, nur nicht auf die Ursache.
    """
    if isinstance(felder, list):
        return felder
    return [{"name": name, "value": wert} for name, wert in felder.items()]


def _spezifikationen(format_: dict) -> list[dict]:
    return [
        dict(spez, fields=_felder_als_liste(spez["fields"]))
        for spez in format_.get("specifications", [])
    ]


def _sprachformat(code: str, nummer: int, punkte: int) -> Formatwunsch:
    """Ein Erkennungsmuster fuer eine Sprache, die TRaSH nicht ausgearbeitet hat.

    Radarr und Sonarr bringen die Spracherkennung selbst mit; ein Muster ist
    darum nur eine einzige Bedingung. Was fehlt, sind die Raenge guter Gruppen
    und die Schrott-Filter, die es fuer Deutsch und Franzoesisch gibt.
    """
    return Formatwunsch(
        name=f"Sprache: {SPRACHNAMEN.get(code, code)}",
        punkte=punkte,
        spezifikationen=[
            {
                "name": SPRACHNAMEN.get(code, code),
                "implementation": "LanguageSpecification",
                "negate": False,
                "required": True,
                "fields": [{"name": "value", "value": nummer}],
            }
        ],
    )


#: Welche Muster der Familie die Sprache selbst erkennen - nur diese werden bei
#: "alle zugleich" stummgeschaltet. Die Schrott-Filter der Familie bleiben.
FAMILIEN_SPRACHMUSTER = {
    "german": ("german", "german-dl", "german-dl-undefined"),
}


def _familiensprache_stummschalten(
    wuensche: dict[str, Formatwunsch], daten: dict, familie: str
) -> None:
    """Die Sprachmuster der Familie auf 0 setzen.

    Bei "alle Sprachen zugleich" soll allein das gemeinsame Muster die Schwelle
    reissen. Bliebe etwa "German DL" auf 11 000 stehen, kaeme ein rein deutsches
    Release ueber Umwege doch noch durch.
    """
    nach_datei = daten.get("formate_nach_datei", {})
    for datei in FAMILIEN_SPRACHMUSTER.get(familie, ()):
        trash_id = nach_datei.get(datei)
        if trash_id and trash_id in wuensche:
            alt = wuensche[trash_id]
            wuensche[trash_id] = Formatwunsch(
                name=alt.name,
                spezifikationen=alt.spezifikationen,
                punkte=0,
                beim_umbenennen=alt.beim_umbenennen,
            )


def bauplan(
    rezept: dict,
    dienst: str,
    sprachnummern: dict[str, int],
    qualitaeten: set[str] | None = None,
) -> Bauplan:
    """Antworten in einen Bauplan uebersetzen.

    ``sprachnummern`` kommt von der Instanz (``GET /language``): Die Nummern
    sind nicht ueber alle Fassungen gleich, deshalb werden sie gefragt und
    nicht angenommen.

    ``qualitaeten`` sind die Stufen, die diese Instanz **wirklich** kennt.
    ⚠️ Ohne sie erfindet der Bauplan Stufen: "Erst nehmen, was da ist" leitet
    kleinere Aufloesungen aus den vorhandenen ab, und daraus wird bei einem
    Remux-Profil ein ``Remux-720p`` - das es in Radarr nicht gibt. Geschrieben
    wird es dann nicht, der Bauplan behauptet es aber weiter, und der Abgleich
    meldet jedes Mal faelschlich "von dir angepasst".
    """
    return bauplan_aus(rezept, dienst, schnappschuss(dienst), sprachnummern, qualitaeten)


def bauplan_aus(
    rezept: dict,
    dienst: str,
    daten: dict[str, Any],
    sprachnummern: dict[str, int],
    qualitaeten: set[str] | None = None,
) -> Bauplan:
    """Wie ``bauplan``, aber mit ausdruecklich uebergebenen Guide-Daten.

    Gebraucht, um einen **noch nicht uebernommenen** Stand zu pruefen: Laesst
    sich jedes abgelegte Profil damit noch bauen? Erst wenn ja, wird er
    uebernommen - sonst faellt der Schaden erst beim naechsten Verteilen auf,
    und der alte Stand ist dann schon fort.
    """
    sprachen: list[str] = list(rezept.get("sprachen") or [])
    rollen: dict[str, str] = dict(rezept.get("sprachRollen") or {})
    familie = _familie(sprachen)
    aufloesung = rezept["aufloesung"]
    quelle = rezept["quelle"]

    schluessel = (dienst, familie, aufloesung, quelle)
    if schluessel not in PROFILDATEI:
        raise TrashFehler(f"no profile for {schluessel}")
    basis = PROFILDATEI[schluessel]
    profil = daten["profile"].get(basis)
    if profil is None:
        raise TrashFehler(f"profile {basis} missing from snapshot")

    satz = profil.get("trash_score_set") or "default"
    hinweise: list[str] = []

    # ---- Erkennungsmuster einsammeln -------------------------------------
    wuensche: dict[str, Formatwunsch] = {}

    def dazu(trash_id: str, punkte: int | None = None) -> None:
        format_ = daten["formate"].get(trash_id)
        if format_ is None:
            # Ein Format ist aus den Guides verschwunden. Lieber laut abbrechen
            # als ein Profil mit Loch schreiben.
            raise TrashFehler(f"custom format {trash_id} missing from snapshot")
        wuensche[trash_id] = Formatwunsch(
            name=format_["name"],
            spezifikationen=_spezifikationen(format_),
            punkte=_punkte(format_, satz) if punkte is None else punkte,
            beim_umbenennen=bool(format_.get("includeCustomFormatWhenRenaming")),
        )

    for trash_id in profil.get("formatItems", {}).values():
        dazu(trash_id)

    def gruppe(name: str, nur_standard: bool = True) -> None:
        eintrag = daten["gruppen"].get(name)
        if eintrag is None:
            raise TrashFehler(f"group {name} missing from snapshot")
        for cf in eintrag["custom_formats"]:
            if nur_standard and not cf.get("default"):
                continue
            dazu(cf["trash_id"])

    gruppe(GRUPPEN_UNERWUENSCHT.get(familie, "unwanted-formats"))
    for name in GRUPPEN_IMMER:
        gruppe(name, nur_standard=False)

    # ---- Der ausfuehrliche Zweig -----------------------------------------
    # Fehlt ein Feld oder steht es auf "egal", passiert nichts: Der einfache
    # Weg ist damit genau der ausfuehrliche ohne Antworten.
    for regel in AUSFUEHRLICH:
        if rezept.get(regel["feld"]) != regel["wenn"]:
            continue
        if "dienst" in regel and dienst != regel["dienst"]:
            continue
        if "aufloesung" in regel and aufloesung not in regel["aufloesung"]:
            continue
        if "familie" in regel and familie != regel["familie"]:
            continue
        gruppe(regel["gruppe"], nur_standard=False)

    if aufloesung == "2160p" and rezept.get("hdr") != "egal":
        for name in GRUPPEN_HDR:
            gruppe(name, nur_standard=False)
        if rezept.get("hdr") == "netz":
            gruppe(GRUPPE_DV_OHNE_NETZ, nur_standard=False)

    # ---- Sprachen --------------------------------------------------------
    pflicht = [c for c in sprachen if rollen.get(c) == "pflicht"]
    unbekannt = [c for c in sprachen if c not in sprachnummern]
    for code in unbekannt:
        hinweise.append(f"language {code} unknown to this instance")
    sprachen = [c for c in sprachen if c not in unbekannt]
    pflicht = [c for c in pflicht if c not in unbekannt]

    # "Alle im selben Release" braucht EIN Muster mit mehreren Bedingungen -
    # getrennte Muster liessen sich einzeln erfuellen.
    alle_zugleich = len(pflicht) > 1 and rezept.get("mehrerePflicht") == "alle"

    eigene: list[Formatwunsch] = []
    for code in sprachen:
        if alle_zugleich:
            # Auf 0: Die einzige Tuer ist das gemeinsame Muster (siehe
            # PUNKTE_SPRACHE_ALLE). Ein eigener Punktwert waere ein zweiter Weg.
            punkte = 0
        elif code in pflicht:
            punkte = PUNKTE_SPRACHE_PFLICHT
        else:
            punkte = PUNKTE_SPRACHE_GERN
        if FAMILIENSPRACHEN.get(code) == familie:
            # Diese Sprache traegt bereits das Profil - TRaSHs eigene Muster
            # sind feiner als alles, was wir hier bauen koennten.
            if alle_zugleich:
                _familiensprache_stummschalten(wuensche, daten, familie)
            continue
        eigene.append(_sprachformat(code, sprachnummern[code], punkte))

    if alle_zugleich:
        eigene.append(
            Formatwunsch(
                name="Sprachen: "
                + " + ".join(SPRACHNAMEN.get(c, c) for c in pflicht),
                punkte=PUNKTE_SPRACHE_ALLE,
                spezifikationen=[
                    {
                        "name": SPRACHNAMEN.get(code, code),
                        "implementation": "LanguageSpecification",
                        "negate": False,
                        # required=True bei jeder Bedingung heisst UND.
                        "required": True,
                        "fields": [{"name": "value", "value": sprachnummern[code]}],
                    }
                    for code in pflicht
                ],
            )
        )
        min_punkte = PUNKTE_SPRACHE_ALLE
    elif pflicht:
        min_punkte = MINDESTPUNKTE_PFLICHT
    else:
        min_punkte = int(profil.get("minFormatScore", 0))

    # ---- Qualitaeten -----------------------------------------------------
    erlaubt = [e for e in profil.get("items", []) if e.get("allowed")]
    if not erlaubt:
        raise TrashFehler(f"profile {basis} allows no quality")
    merge: list[str] = []
    for eintrag in erlaubt:
        merge.extend(eintrag.get("items") or [eintrag["name"]])

    if quelle == "web":
        # Die deutschen Familien haben kein reines WEB-Profil; also fliegt
        # heraus, was von der Scheibe kommt.
        gefiltert = [q for q in merge if "WEB" in q.upper()]
        if gefiltert and len(gefiltert) != len(merge):
            merge = gefiltert

    if rezept.get("sofortNehmen"):
        # Das ist der Kniff der "Alternative"-Fassung: kleinere Aufloesungen in
        # DIESELBE Gruppe. Fuer Radarr sind sie damit gleichwertig, und allein
        # die Punkte entscheiden - deshalb wird spaeter von selbst getauscht.
        vorhanden = {q.rsplit("-", 1)[-1] for q in merge}
        for kleiner in ("1080p", "720p"):
            if kleiner in vorhanden:
                continue
            for vorlage in list(merge):
                ersatz = vorlage.rsplit("-", 1)[0] + "-" + kleiner
                if ersatz not in merge:
                    merge.append(ersatz)

    if qualitaeten is not None:
        erfunden = [q for q in merge if q not in qualitaeten]
        merge = [q for q in merge if q in qualitaeten]
        if erfunden:
            hinweise.append("qualities not offered by this instance: " + ", ".join(erfunden))
        if not merge:
            raise TrashFehler("instance offers none of the requested qualities")

    return Bauplan(
        profilname=str(rezept.get("name") or basis).strip(),
        basis=basis,
        stand=daten["stand"],
        formate=tuple(wuensche.values()) + tuple(eigene),
        merge=tuple(dict.fromkeys(merge)),
        min_punkte=min_punkte,
        schluss_punkte=int(profil.get("cutoffFormatScore", 10_000)),
        hinweise=tuple(hinweise),
    )
