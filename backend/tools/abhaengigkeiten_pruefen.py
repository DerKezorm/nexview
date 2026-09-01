"""Die Pruefung vor dem Bau: Gibt es zu einer gepinnten Fassung eine bekannte Meldung?

Aufruf aus ``backend/``::

    python tools/abhaengigkeiten_pruefen.py

⚠️ **Warum es das gibt.** Ein Pin altert lautlos. ``pyjwt==2.11.0`` stand hier
ueber mehrere Fassungen unveraendert richtig aussehend in der Datei, waehrend
OSV dazu sechs Meldungen fuehrte - darunter eine, die *jedes* ``jwt.decode``
betrifft. Im Browser sieht man davon nichts, und niemand meldet es. Diese
Pruefung fragt bei jedem CI-Lauf nach, und sie **bricht ab** statt zu warnen:
Eine Warnung, die den Bau durchgehen laesst, liest nach dem dritten Mal keiner
mehr. Dieselbe Haltung wie bei der Waage in ``frontend/tools/gewicht-pruefen.mjs``.

⚠️ **UND DIE AUSNAHMELISTE IST KEIN WEG, DEN BAU GRUEN ZU BEKOMMEN.** Das ist
der ganze Sinn der Sache. Ein Eintrag in ``AUSNAHMEN`` ist eine Behauptung, die
dauerhaft wahr bleiben muss: "Diese Meldung braucht X, und Nexview tut X nicht."
Wer sie eintraegt, um den Lauf durchzubekommen, hat ein echtes Loch zugedeckt -
und zwar so, dass der naechste Leser es fuer geprueft haelt. Der billigere Weg
ist fast immer die naechste Fassung des Pakets. Die Liste startet deshalb
**leer**: Der erste Eintrag ist dann eine bewusste Handlung und kein Erben
eines Haufens.

**Was diese Pruefung NICHT abdeckt**, damit niemand mehr fuer geprueft haelt,
als geprueft wird:

* Nur ``backend/requirements.txt``. ``backend/requirements-dev.txt`` bringt
  zusaetzlich pytest und pytest-asyncio mit; die fragt hier niemand ab.
* Das Frontend haengt an ``frontend/package.json`` mit einer eigenen
  Lieferkette. npm ist hier gar nicht abgedeckt.
* OSV antwortet auch fuer Fassungen, die es nie gab: die Abfrage ``pyjwt@2.13.1``
  meldet nichts, obwohl PyPI hoechstens 2.13.0 kennt. Ein Tippfehler im Pin
  sieht hier also sauber aus. Er faellt unmittelbar danach um, weil der Schritt
  "Abhaengigkeiten installieren" ihn nicht findet.
* Ein Ausfall bei api.osv.dev heisst "nicht geprueft", nicht "sauber" - dazu
  unten mehr. Ein langer Ausfall ist ein langer blinder Fleck, den im Repo
  nichts bemerkt. Wenn das je zaehlt, ist die Antwort eine zweite Quelle und
  keine weichere Pruefung.

Es gibt **bewusst keinen Umgehungsschalter** ueber die Umgebung. Gaebe es einen,
gaebe es einen Weg, die Pruefung stumm zu stellen, und der wird benutzt.

Nur Standardbibliothek, wie ``tools/trash_schnappschuss.py``: Das Werkzeug
laeuft im Arbeitsablauf **vor** ``pip install``.

Rueckgabecodes wie bei der Waage: 2 = die Pruefung kann nicht laufen,
1 = ein Fund, 0 = sauber.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"

OSV_ABFRAGE = "https://api.osv.dev/v1/query"
OSV_EINZELN = "https://api.osv.dev/v1/vulns/"

#: Zwei Versuche je Abruf, dann gilt die Quelle als weg. Die Zahlen sind
#: gemessen: elf Abfragen brauchen zusammen rund sechs Sekunden.
VERSUCHE = 2
ZEITGRENZE_SEKUNDEN = 20
PAUSE_SEKUNDEN = 2


#: Meldungen, die zu einer gepinnten Fassung stehen bleiben duerfen - mit Grund.
#:
#: Schluessel ist die **CVE**, ersatzweise die OSV-Kennung. Nicht die
#: GHSA-Kennung: OSV liefert zu derselben Sache je einen GHSA- und einen
#: PYSEC-Datensatz, und dann braeuchte jede Ausnahme zwei Eintraege, von denen
#: einer unbemerkt verrottet.
#:
#: Jeder Eintrag traegt sechs Felder:
#:
#: ``paket``
#:     Der Name, so wie er in ``requirements.txt`` steht.
#: ``gilt_fuer``
#:     Die **exakt gepinnte Fassung**, gegen die entschieden wurde. Passt sie
#:     nicht mehr, bricht die Pruefung mit 2 ab: Die Begruendung wurde gegen
#:     eine andere Fassung geschrieben und wird neu entschieden, nicht geerbt.
#: ``grund``
#:     Was die Meldung braucht, und warum Nexview das nicht tut.
#: ``beleg``
#:     Der Befehl, der es zeigt. Damit der naechste Leser nachrechnet statt zu
#:     glauben.
#: ``geprueft_am`` und ``von``
#:     Wann, und wer es verantwortet.
#:
#: ⚠️ Die Liste ist **absichtlich leer**. Lies den Absatz oben, bevor du sie
#: fuellst. Jeder Lauf druckt sie vollstaendig aus, auch wenn alles gruen ist:
#: Eine Ausnahme, die niemand sieht, sieht niemand nach.
AUSNAHMEN: dict[str, dict[str, str]] = {}


class NichtPruefbar(Exception):
    """Die Pruefung kann so nicht laufen. Fuehrt zu Rueckgabecode 2."""


class NichtErreichbar(Exception):
    """api.osv.dev antwortet nicht. Fuehrt zu Rueckgabecode 0 samt Hinweis."""


# ---------------------------------------------------------------------------
# Einlesen
# ---------------------------------------------------------------------------

#: ``name==fassung``, wahlweise mit Extras: ``uvicorn[standard]==0.38.0``.
ZEILE = re.compile(r"^([A-Za-z0-9._-]+)(\[[A-Za-z0-9,._-]+\])?==([A-Za-z0-9.+!-]+)$")


def pins_lesen(text: str) -> list[tuple[str, str]]:
    """Die Paketnamen samt Fassung aus einer requirements-Datei.

    ⚠️ **Nur ``name==fassung`` wird angenommen.** Eine Zeile mit ``>=``, ``~=``
    oder ganz ohne Fassung bricht ab und wird genannt, statt still uebersprungen
    zu werden: Eine ungepinnte Abhaengigkeit ist nicht pruefbar, und sie
    stillschweigend zu ueberspringen waere genau das Versagen, das diese
    Pruefung verhindern soll. Man saehe eine gruene Zeile und haette nichts.

    Extras werden fuer die Abfrage abgeschnitten - OSV kennt ``uvicorn``, nicht
    ``uvicorn[standard]``. Die Fassung bleibt davon unberuehrt.
    """
    pins: list[tuple[str, str]] = []
    for nummer, roh in enumerate(text.splitlines(), start=1):
        zeile = roh.split("#", 1)[0].strip()
        if not zeile:
            continue
        treffer = ZEILE.match(zeile)
        if not treffer:
            raise NichtPruefbar(
                f"{REQUIREMENTS.name}, Zeile {nummer}: {zeile!r} ist kein Pin der "
                "Form name==fassung. Ohne genaue Fassung laesst sich nicht fragen, "
                "ob es dazu eine Meldung gibt."
            )
        pins.append((treffer.group(1), treffer.group(3)))
    if not pins:
        raise NichtPruefbar(f"{REQUIREMENTS.name} enthaelt keine einzige Zeile mit einem Pin.")
    return pins


# ---------------------------------------------------------------------------
# Abfrage
# ---------------------------------------------------------------------------


def _holen(anfrage: urllib.request.Request, was: str) -> dict:
    """Ein Abruf mit zwei Versuchen, und eine klare Trennung der Fehlerarten.

    ``HTTPError`` ist eine Unterklasse von ``URLError`` und die wiederum von
    ``OSError``. Die Reihenfolge der ``except``-Zweige ist deshalb kein Detail:
    stuende ``OSError`` oben, kaeme ein 404 nie unten an.

    ⚠️ **Ein geglueckter zweiter Versuch wird gesagt.** Sonst hinterlaesst er
    keine Spur: Der Lauf ist gruen, war zwanzig Sekunden laenger unterwegs, und
    niemand kann sagen, ob api.osv.dev gerade wackelt. Erst wenn *beide*
    Versuche scheitern, meldet sich ``nicht_geprueft`` - und dann steht
    nirgends, dass es vorher schon geknirscht hat. Ein "NICHT GEPRUEFT" aus
    heiterem Himmel sieht aus wie ein einmaliger Ausrutscher; drei vorherige
    Laeufe mit je einem zweiten Versuch erzaehlen etwas anderes.
    """
    letzter = ""
    for versuch in range(1, VERSUCHE + 1):
        try:
            with urllib.request.urlopen(anfrage, timeout=ZEITGRENZE_SEKUNDEN) as antwort:
                return json.load(antwort)
        except urllib.error.HTTPError as fehler:
            # 4xx ausser 429 heisst: wir fragen falsch. Das ist unser Fehler und
            # keine Stoerung draussen - abbrechen statt durchwinken.
            if fehler.code < 500 and fehler.code != 429:
                raise NichtPruefbar(
                    f"api.osv.dev antwortet auf {was} mit HTTP {fehler.code}. "
                    "So gefragt kommt keine brauchbare Antwort; die Abfrage gehoert "
                    "angesehen, nicht uebersprungen."
                ) from fehler
            letzter = f"HTTP {fehler.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as fehler:
            letzter = f"{type(fehler).__name__}: {fehler}"
        if versuch < VERSUCHE:
            print(
                f"  Versuch {versuch} von {VERSUCHE} fuer {was} fehlgeschlagen "
                f"({letzter}). Naechster Versuch in {PAUSE_SEKUNDEN} s."
            )
            time.sleep(PAUSE_SEKUNDEN)
    raise NichtErreichbar(f"{was}: {letzter}")


def osv_abfragen(name: str, fassung: str) -> list[dict]:
    """Alle OSV-Datensaetze zu genau dieser Fassung.

    Ein einziger POST je Paket. ``GET /v1/vulns/<id>`` ist **nicht** noetig: Die
    Antwort von ``/v1/query`` bringt ``severity``, ``database_specific``,
    ``aliases``, ``summary`` und die ``fixed``-Angaben schon mit. Gemessen
    kostet das elf Abfragen in 5,6 s statt 51 Abrufen in 20,8 s. Fuer den Fall,
    dass ein Datensatz doch ohne diese Felder ankommt, steht der Einzelabruf als
    Rueckfall bereit.
    """
    daten = json.dumps({"package": {"name": name, "ecosystem": "PyPI"}, "version": fassung})
    anfrage = urllib.request.Request(
        OSV_ABFRAGE,
        data=daten.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    antwort = _holen(anfrage, f"{name}=={fassung}")
    return list(antwort.get("vulns") or [])


def _ist_duenn(datensatz: dict) -> bool:
    """Ein Datensatz ohne die Felder, die ``/v1/query`` sonst mitbringt."""
    return not datensatz.get("affected") and not datensatz.get("summary") and not datensatz.get("details")


def vervollstaendigen(vulns: list[dict], nachschlagen=None) -> list[dict]:
    """Duenne Datensaetze einzeln nachholen, die uebrigen unveraendert lassen."""
    if nachschlagen is None:
        nachschlagen = _einzeln_holen
    fertig = []
    for datensatz in vulns:
        if _ist_duenn(datensatz) and datensatz.get("id"):
            fertig.append(nachschlagen(datensatz["id"]))
        else:
            fertig.append(datensatz)
    return fertig


def _einzeln_holen(osv_id: str) -> dict:
    return _holen(urllib.request.Request(OSV_EINZELN + osv_id), osv_id)


# ---------------------------------------------------------------------------
# Auswerten
# ---------------------------------------------------------------------------


def _fassung_sortierbar(text: str) -> tuple[int, ...]:
    """Eine Fassung als Zahlenfolge, damit 2.13.0 groesser ist als 2.12.1.

    Bewusst nicht PEP 440: In ``fixed``-Angaben von OSV stehen keine
    Vorabfassungen. Was sich nicht als Zahl lesen laesst, wird zu 0 und steht
    damit hinten, statt faelschlich als die hoechste Angabe zu gelten.
    """
    return tuple(int(stueck) if stueck.isdigit() else 0 for stueck in re.split(r"[._-]", text))


def _schluessel(datensatz: dict) -> str:
    """Die CVE, ersatzweise die OSV-Kennung."""
    for alias in datensatz.get("aliases") or []:
        if alias.startswith("CVE-"):
            return alias
    kennung = str(datensatz.get("id") or "")
    return kennung or "ohne Kennung"


def _behoben_ab(datensatz: dict) -> str:
    """Die hoechste ``fixed``-Angabe eines Datensatzes."""
    fassungen = [
        ereignis["fixed"]
        for betroffen in datensatz.get("affected") or []
        for bereich in betroffen.get("ranges") or []
        for ereignis in bereich.get("events") or []
        if "fixed" in ereignis
    ]
    if not fassungen:
        return ""
    return max(fassungen, key=_fassung_sortierbar)


def _schwere(datensatz: dict) -> str:
    besonders = (datensatz.get("database_specific") or {}).get("severity")
    if besonders:
        return str(besonders)
    for eintrag in datensatz.get("severity") or []:
        if eintrag.get("score"):
            return str(eintrag["score"])
    return "ohne Angabe"


def _zusammenfassung(datensatz: dict) -> str:
    text = (datensatz.get("summary") or datensatz.get("details") or "").strip()
    einzeilig = " ".join(text.split())
    return einzeilig[:160] if einzeilig else "ohne Zusammenfassung"


def gruppen_bilden(vulns: list[dict]) -> dict[str, dict]:
    """Die Datensaetze nach **Sache** zusammenfassen, nicht nach Datensatz.

    ⚠️ OSV liefert zu derselben Sache je einen GHSA- und einen PYSEC-Datensatz,
    und die koennen sich widersprechen: Fuer CVE-2026-48523 nennt
    GHSA-jq35-7prp-9v3f die Behebung 2.13.0, PYSEC-2026-176 dagegen 2.12.1. Wer
    den erstbesten nimmt, pinnt zu niedrig. Innerhalb einer Gruppe gilt deshalb
    die **hoechste** Angabe.

    Ohne das meldete das Werkzeug fuer pyjwt 2.11.0 zwoelf Funde statt sechs,
    und die Ausnahmeliste braeuchte je Sache zwei Eintraege.
    """
    gruppen: dict[str, dict] = {}
    for datensatz in vulns:
        schluessel = _schluessel(datensatz)
        gruppe = gruppen.setdefault(
            schluessel,
            {"kennungen": set(), "behoben_ab": "", "schwere": "", "zusammenfassung": ""},
        )
        gruppe["kennungen"].add(str(datensatz.get("id") or ""))
        gruppe["kennungen"].update(datensatz.get("aliases") or [])
        behoben = _behoben_ab(datensatz)
        if behoben and (
            not gruppe["behoben_ab"]
            or _fassung_sortierbar(behoben) > _fassung_sortierbar(gruppe["behoben_ab"])
        ):
            gruppe["behoben_ab"] = behoben
        schwere = _schwere(datensatz)
        if schwere != "ohne Angabe" and (
            not gruppe["schwere"] or gruppe["schwere"] == "ohne Angabe"
        ):
            gruppe["schwere"] = schwere
        if not gruppe["zusammenfassung"] or gruppe["zusammenfassung"] == "ohne Zusammenfassung":
            gruppe["zusammenfassung"] = _zusammenfassung(datensatz)
    for gruppe in gruppen.values():
        gruppe["kennungen"] = sorted(k for k in gruppe["kennungen"] if k)
        gruppe["schwere"] = gruppe["schwere"] or "ohne Angabe"
        gruppe["behoben_ab"] = gruppe["behoben_ab"] or "keine Angabe"
    return gruppen


AUSNAHME_FELDER = ("paket", "gilt_fuer", "grund", "beleg", "geprueft_am", "von")


def ausnahmen_ausgeben(ausnahmen: dict[str, dict[str, str]]) -> None:
    """Die ganze Liste, bei jedem Lauf, auch wenn alles gruen ist."""
    print()
    if not ausnahmen:
        print("Ausnahmen: keine. Jede Meldung zaehlt hier als Fund.")
        return
    print(f"Ausnahmen ({len(ausnahmen)}):")
    for schluessel, eintrag in sorted(ausnahmen.items()):
        print(f"  {schluessel}  {eintrag.get('paket')}=={eintrag.get('gilt_fuer')}")
        print(f"      Grund:   {eintrag.get('grund')}")
        print(f"      Beleg:   {eintrag.get('beleg')}")
        print(f"      Geprueft: {eintrag.get('geprueft_am')} von {eintrag.get('von')}")


def bewerten(
    pins: list[tuple[str, str]],
    antworten: dict[tuple[str, str], list[dict]],
    ausnahmen: dict[str, dict[str, str]],
) -> int:
    """Aus den OSV-Antworten einen Rueckgabecode machen, und dabei alles nennen.

    Drei Ausgaenge, in dieser Rangfolge:

    * **2**, wenn eine Ausnahme tot ist. Tot heisst: Ihr ``gilt_fuer`` passt
      nicht mehr zum Pin, ihr Paket steht gar nicht mehr in der Datei, oder OSV
      meldet ihre Kennung fuer diese Fassung nicht mehr. In allen drei Faellen
      wurde die Begruendung gegen etwas anderes geschrieben als das, was heute
      dasteht - das wird neu entschieden und nicht geerbt. Vor einem Fund, weil
      eine tote Ausnahme heisst, dass die Liste selbst nicht mehr stimmt.
    * **1**, wenn eine Meldung ohne Ausnahme dasteht.
    * **0**, wenn nichts uebrig bleibt.
    """
    gepinnt = dict(pins)
    alle_gruppen: dict[tuple[str, str], dict[str, dict]] = {}
    funde: list[str] = []

    print()
    for name, fassung in pins:
        gruppen = gruppen_bilden(antworten.get((name, fassung), []))
        alle_gruppen[(name, fassung)] = gruppen
        offen = [s for s in gruppen if ausnahmen.get(s, {}).get("paket") != name]
        if not gruppen:
            print(f"  {name}=={fassung}: keine Meldung")
        elif offen:
            print(f"  {name}=={fassung}: {len(gruppen)} Meldungen, {len(offen)} davon ohne Ausnahme")
        else:
            print(f"  {name}=={fassung}: {len(gruppen)} Meldungen, alle mit Ausnahme")
        for schluessel in sorted(offen):
            gruppe = gruppen[schluessel]
            funde.append(
                f"{name}=={fassung}  {schluessel} [{', '.join(gruppe['kennungen'])}]  "
                f"Schwere {gruppe['schwere']}  behoben ab {gruppe['behoben_ab']}  "
                f"{gruppe['zusammenfassung']}"
            )

    tote: list[str] = []
    for schluessel, eintrag in sorted(ausnahmen.items()):
        paket = eintrag.get("paket", "")
        gilt_fuer = eintrag.get("gilt_fuer", "")
        if paket not in gepinnt:
            tote.append(
                f"{schluessel}: {paket!r} steht nicht mehr in {REQUIREMENTS.name}. "
                "Die Ausnahme haengt an einem Paket, das es hier nicht gibt."
            )
            continue
        if gepinnt[paket] != gilt_fuer:
            tote.append(
                f"{schluessel}: entschieden gegen {paket}=={gilt_fuer}, gepinnt ist "
                f"aber {paket}=={gepinnt[paket]}. Die Begruendung gilt fuer eine "
                "andere Fassung."
            )
            continue
        if schluessel not in alle_gruppen[(paket, gilt_fuer)]:
            tote.append(
                f"{schluessel}: OSV meldet das fuer {paket}=={gilt_fuer} nicht mehr. "
                "Die Ausnahme deckt nichts mehr ab und gehoert geloescht."
            )

    if tote:
        print()
        print("::error::Die Ausnahmeliste stimmt nicht mehr.")
        print("TOTE AUSNAHMEN")
        for zeile in tote:
            print(f"  {zeile}")
        print()
        print(
            "Eine Ausnahme wird nicht nachgezogen, sie wird neu entschieden. Wer die\n"
            "Fassung hebt, prueft die Begruendung noch einmal gegen die neue Fassung\n"
            "oder loescht den Eintrag."
        )
        return 2

    if funde:
        print()
        print(f"::error::{len(funde)} Meldung(en) zu gepinnten Fassungen ohne Ausnahme.")
        print("FUNDE")
        for zeile in funde:
            print(f"  {zeile}")
        print()
        print(
            "Der Weg ist die naechste Fassung des Pakets, nicht ein Eintrag in\n"
            "AUSNAHMEN. Lies den Kopf dieser Datei, bevor du die Liste anfasst."
        )
        return 1

    print()
    print("In Ordnung.")
    return 0


def nicht_geprueft(grund: str) -> None:
    """Der Ausfall draussen. Unverwechselbar, damit er nicht wie gruen aussieht.

    ⚠️ Warum das trotzdem mit 0 endet: Ein Bau, den ein fremder Ausfall umwirft,
    wird binnen einer Woche abgeschaltet, und dann prueft gar nichts mehr.
    Zugleich darf ein Ausfall nicht wie ein Ergebnis aussehen - deshalb die
    ::warning::-Zeile fuer die GitHub-Zusammenfassung und die drei Worte, nach
    denen man im Protokoll suchen kann.
    """
    print()
    print("::warning::Abhaengigkeiten NICHT GEPRUEFT: api.osv.dev war nicht erreichbar.")
    print("NICHT GEPRUEFT")
    print(f"  Grund: {grund}")
    print(f"  Nach {VERSUCHE} Versuchen mit je {ZEITGRENZE_SEKUNDEN} s aufgegeben.")
    print("  Dieser Lauf sagt nichts darueber, ob die Pins in Ordnung sind.")


def main() -> int:
    print(f"Geprueft wird: {REQUIREMENTS}")
    try:
        text = REQUIREMENTS.read_text(encoding="utf-8")
    except OSError as fehler:
        print(f"::error::{REQUIREMENTS} ist nicht lesbar: {fehler}")
        return 2

    try:
        pins = pins_lesen(text)
    except NichtPruefbar as fehler:
        print(f"::error::{fehler}")
        return 2

    print(f"{len(pins)} gepinnte Pakete, Quelle der Meldungen: {OSV_ABFRAGE}")
    ausnahmen_ausgeben(AUSNAHMEN)

    antworten: dict[tuple[str, str], list[dict]] = {}
    for name, fassung in pins:
        try:
            antworten[(name, fassung)] = vervollstaendigen(osv_abfragen(name, fassung))
        except NichtErreichbar as fehler:
            nicht_geprueft(str(fehler))
            return 0
        except NichtPruefbar as fehler:
            print(f"::error::{fehler}")
            return 2

    return bewerten(pins, antworten, AUSNAHMEN)


if __name__ == "__main__":
    raise SystemExit(main())
