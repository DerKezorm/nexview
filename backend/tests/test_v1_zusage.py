"""Die Wächter über `/api/v1`.

⚠️ **Ein Versprechen ohne Test ist ein Vorsatz.** Genau das ist der Punkt
dieser Datei: Unter ``/api/v1`` steht eine Zusage, und Zusagen bricht man nicht
absichtlich, sondern aus Versehen - beim Aufraeumen, beim Umbenennen, beim
"das heisst doch besser so".

Heute ist mir genau das passiert: In der Kontoaufloesung wurde aus
``storniert: list[str]`` ein ``offen: list[OffeneZeile]``, weil der neue Name
besser passt und die Liste mehr tragen musste. Richtig so - und unter einer
Zusage waere es ein Bruch gewesen, den niemand bemerkt haette.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.main import app
from app.routers.v1 import ZUGESAGT

#: Die festgehaltene Form der Antworten. Liegt als Datei daneben, damit eine
#: Aenderung im Diff auftaucht statt in einer Fehlermeldung.
ABDRUCK = Path(__file__).with_name("v1_abdruck.json")


def _v1_pfade() -> set[str]:
    return {r.path for r in app.routes if getattr(r, "path", "").startswith("/api/v1")}


def test_die_flaeche_ist_genau_die_zugesagte() -> None:
    """⚠️ Nichts rutscht versehentlich hinein - und nichts heraus.

    Beides waere schlecht. Ein Pfad zu viel ist ein Versprechen, das niemand
    geben wollte; einer zu wenig ist ein gebrochenes.
    """
    da = _v1_pfade()
    zugesagt = set(ZUGESAGT)

    assert da - zugesagt == set(), (
        f"Unter /api/v1 liegt etwas, das nicht in ZUGESAGT steht: {sorted(da - zugesagt)}. "
        "Entweder gehoert es dort nicht hin - oder es ist eine bewusste Zusage und muss "
        "in die Liste in routers/v1.py."
    )
    assert zugesagt - da == set(), (
        f"Zugesagt, aber nicht erreichbar: {sorted(zugesagt - da)}."
    )


#: Notausgang gegen einen Schema-Kreis, den ``gesehen`` nicht abfaengt. So tief
#: ist keine zugesagte Antwort.
MAX_TIEFE = 8


def _felder_je_pfad() -> dict[str, list[str]]:
    """Aus dem OpenAPI-Dokument die Antwortfelder der v1-Pfade ziehen.

    ⚠️ **Bis in die Tiefe, nicht nur die oberste Ebene.** Vorher wurde genau
    ein ``$ref`` aufgeloest und dann die Namen der Eigenschaften notiert. Fuer
    ``GET /api/v1/dashboard`` - die Kachel fuer Homepage und Homarr - standen
    im Abdruck deshalb nur sechs Namen: ``anfragen``, ``befunde``,
    ``bibliothek``, ``instanzen``, ``tickets_offen``, ``version``. **Alles,
    was eine Kachel wirklich ausliest**, liegt eine Ebene tiefer
    (``anfragen.wartend``, ``bibliothek.belegt_bytes``, ``instanzen[].name``)
    und war damit von der Zusage nicht gedeckt: Wer eines davon umbenannte,
    brach jede angebundene Kachel, und der Waechter blieb gruen.

    Ergebnis sind Pfade wie ``befunde.fehler`` oder ``instanzen[].name``.
    """
    dokument = app.openapi()
    schemas = dokument.get("components", {}).get("schemas", {})

    def felder(
        schema: dict, praefix: str = "", gesehen: frozenset = frozenset(), tiefe: int = 0
    ) -> list[str]:
        if tiefe > MAX_TIEFE:
            return []

        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name in gesehen:  # Selbstbezug - hier ist Schluss.
                return []
            return felder(schemas.get(name, {}), praefix, gesehen | {name}, tiefe + 1)

        # ``str | None`` wird zu ``anyOf``. Der ``null``-Zweig traegt keine Felder.
        for zweig in ("anyOf", "oneOf", "allOf"):
            if zweig in schema:
                aus: list[str] = []
                for teil in schema[zweig]:
                    if teil.get("type") == "null":
                        continue
                    aus += felder(teil, praefix, gesehen, tiefe + 1)
                return sorted(set(aus))

        if schema.get("type") == "array":
            return felder(schema.get("items", {}), praefix + "[]", gesehen, tiefe + 1)

        eigenschaften = schema.get("properties")
        if not eigenschaften:
            if schema.get("additionalProperties"):
                # Ein Beutel ohne benannte Felder (``dict[str, int]``). Was darin
                # steckt, verraet das Schema nicht - deshalb fragt
                # ``test_v1_wirklichkeit.py`` diese vier Adressen ausdruecklich
                # ab, statt sich auf den Abdruck zu verlassen.
                return [f"{praefix}{{*}}"] if praefix else ["{*}"]
            return [praefix] if praefix else []

        ergebnis: list[str] = []
        for name, unter in sorted(eigenschaften.items()):
            pfad = f"{praefix}.{name}" if praefix else name
            tiefer = felder(unter, pfad, gesehen, tiefe + 1)
            ergebnis += tiefer or [pfad]
        return sorted(set(ergebnis))

    ergebnis: dict[str, list[str]] = {}
    for pfad, operationen in dokument.get("paths", {}).items():
        if not pfad.startswith("/api/v1"):
            continue
        for methode, operation in operationen.items():
            antwort = (
                operation.get("responses", {})
                .get("200", operation.get("responses", {}).get("201", {}))
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            ergebnis[f"{methode.upper()} {pfad}"] = felder(antwort)
    return ergebnis


def test_die_antwortform_bleibt(request) -> None:
    """⚠️ **Der eigentliche Waechter.**

    Er haelt fest, welche Felder die zugesagten Antworten haben. Verschwindet
    eines oder wird es umbenannt, schlaegt er an - und zwar **bevor** es
    jemandem draussen auffaellt.

    Ein Feld **hinzuzufuegen** ist erlaubt und bricht nichts; wer nur die
    Felder liest, die er kennt, merkt davon nichts. Deshalb wird geprueft, dass
    nichts **fehlt**, nicht dass alles gleich ist.

    Passt die Aenderung wirklich, wird der Abdruck neu erzeugt:
    ``pytest tests/test_v1_zusage.py --abdruck-neu``. Das ist Absicht ein
    eigener Handgriff - so bleibt es eine Entscheidung.
    """
    jetzt = _felder_je_pfad()

    if request.config.getoption("--abdruck-neu") or not ABDRUCK.exists():
        ABDRUCK.write_text(
            json.dumps(jetzt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not request.config.getoption("--abdruck-neu"):
            # Beim allerersten Lauf gibt es noch nichts zu vergleichen.
            return

    frueher: dict[str, list[str]] = json.loads(ABDRUCK.read_text(encoding="utf-8"))

    verschwunden: dict[str, list[str]] = {}
    for schluessel, felder in frueher.items():
        if schluessel not in jetzt:
            verschwunden[schluessel] = ["(der ganze Endpunkt)"]
            continue
        fehlend = [f for f in felder if f not in jetzt[schluessel]]
        if fehlend:
            verschwunden[schluessel] = fehlend

    assert not verschwunden, (
        "Aus einer zugesagten Antwort sind Felder verschwunden:\n"
        + "\n".join(f"  {k}: {', '.join(v)}" for k, v in sorted(verschwunden.items()))
        + "\n\nUnter /api/v1 gilt eine Zusage. Entweder das Feld bleibt (auch wenn es "
        "innen anders heisst), oder es braucht ein /api/v2 daneben. Ist die Aenderung "
        "wirklich in Ordnung, den Abdruck mit --abdruck-neu erneuern."
    )


# --------------------------------------------------------- Die Zusage im README
#
# ⚠️ **Die Liste steht an zwei Stellen, und die zweite altert lautlos.**
# ``ZUGESAGT`` ist die Wahrheit, aber gelesen wird das README. Zweimal ist genau
# das passiert: Es sagte "Fourteen endpoints" und zaehlte vierzehn auf, waehrend
# im Code laengst sechzehn standen. Im Browser sieht man davon nichts, und
# niemand meldet es - wer sich auf die Zusage stuetzt, kennt zwei Adressen
# einfach nicht.
#
# Ein Diff faellt auf, eine fehlende Zeile nicht. Deshalb dieser Waechter.

README = Path(__file__).resolve().parents[2] / "README.md"

#: Das Zahlwort im Satz ueber der Tabelle. Nur so weit, wie die Zusage
#: realistisch waechst - reicht es nicht mehr, sagt der Test das, statt die
#: Pruefung stillschweigend zu ueberspringen.
ZAHLWOERTER = {
    10: "Ten", 11: "Eleven", 12: "Twelve", 13: "Thirteen", 14: "Fourteen",
    15: "Fifteen", 16: "Sixteen", 17: "Seventeen", 18: "Eighteen",
    19: "Nineteen", 20: "Twenty",
}


def _readme_abschnitt() -> str:
    """Nur der Abschnitt mit der Zusage, nicht das ganze README.

    Sonst faengt die Tabellensuche jede andere Tabelle mit ein, und der Test
    scheitert an etwas, das er gar nicht bewachen soll.
    """
    text = README.read_text(encoding="utf-8")
    auf = text.index("### What is promised, and what is not")
    zu = text.index("###", auf + 5)
    return text[auf:zu]


def _readme_pfade() -> set[str]:
    """Die Pfade aus der Tabelle, ohne die Methoden davor."""
    abschnitt = _readme_abschnitt()
    pfade = set()
    for zelle in re.findall(r"^\| `([^`]+)` \|", abschnitt, re.MULTILINE):
        # "GET /api/v1/about" und "GET/PUT/POST/DELETE /api/v1/me/push"
        pfade.add(zelle.split()[-1])
    return pfade


def test_das_readme_zaehlt_dieselben_adressen_auf() -> None:
    """⚠️ In beide Richtungen, und mit einer Bodenschwelle.

    Ohne die Schwelle bestuende der Test auch dann, wenn die Tabelle
    verschwindet oder das Muster nicht mehr greift: leere Menge gegen leere
    Menge ist gleich. Genau so laeuft ein Waechter jahrelang gruen ins Leere.
    """
    im_readme = _readme_pfade()
    zugesagt = set(ZUGESAGT)

    assert len(im_readme) >= 10, (
        f"In der Zusage-Tabelle des README stehen nur {len(im_readme)} Adressen. "
        "Entweder ist die Tabelle weg, oder ihr Aufbau hat sich geaendert und "
        "dieser Test liest ins Leere."
    )
    assert zugesagt - im_readme == set(), (
        f"Zugesagt, aber im README nicht aufgefuehrt: {sorted(zugesagt - im_readme)}. "
        "Wer sich auf die Zusage stuetzt, erfaehrt von diesen Adressen nichts."
    )
    assert im_readme - zugesagt == set(), (
        f"Im README versprochen, aber nicht in ZUGESAGT: {sorted(im_readme - zugesagt)}. "
        "Entweder gehoert die Adresse in die Liste in routers/v1.py, oder die "
        "Zeile im README ist zu viel."
    )


def test_die_zahl_ueber_der_tabelle_stimmt() -> None:
    """⚠️ Der Satz nennt die Zahl im Wort, und der wird beim Ergaenzen vergessen.

    Eine Zeile fuegt man in die Tabelle ein; den Satz darueber liest man dabei
    nicht.
    """
    treffer = re.search(r"^(\w+) endpoints live under", _readme_abschnitt(), re.MULTILINE)
    assert treffer, (
        "Der Satz 'N endpoints live under ...' steht nicht mehr im "
        "Zusage-Abschnitt des README. Dann kann diese Zahl auch nicht gehuetet "
        "werden - Satz wiederherstellen oder diesen Test anpassen."
    )

    soll = ZAHLWOERTER.get(len(ZUGESAGT))
    assert soll, (
        f"ZUGESAGT hat {len(ZUGESAGT)} Eintraege, und dafuer steht kein Zahlwort "
        "in ZAHLWOERTER. Eines ergaenzen, damit die Pruefung weiterlaeuft."
    )
    assert treffer.group(1) == soll, (
        f"Das README sagt '{treffer.group(1)} endpoints', ZUGESAGT hat aber "
        f"{len(ZUGESAGT)} Eintraege - richtig waere '{soll}'."
    )
