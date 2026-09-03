"""Jeder Satz des Seerr-Umzugs hat einen Text in beiden Sprachen.

⚠️ **Warum das ein Test ist.** Bis 0.29.0 baute das Backend die Hinweise des
Umzugs als deutsche Saetze, und die englische Oberflaeche zeigte sie
unveraendert - auf der Projektseite standen englische Bildschirmfotos mit
deutschen Zeilen darin. Seitdem liefert das Backend Kennungen
(``services/seerr/texte.py``), und die Oberflaeche baut die Saetze aus
``setup.seerr.saetze``. Fehlt dort eine Kennung, faellt die Oberflaeche still
auf den deutschen Rueckfall zurueck - und niemand merkt es, bis wieder ein
Foto entsteht. Dieser Test merkt es vorher.

Drei Dinge werden gehalten: jede Kennung aus ``VORLAGEN`` steht in beiden
Sprachdateien; die Platzhalter sind dieselben; und jede Kennung, die der Code
mit ``satz("...")`` anfordert, gibt es in ``VORLAGEN``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.seerr import texte

WURZEL = Path(__file__).resolve().parents[2]
SPRACHEN = WURZEL / "frontend" / "src" / "i18n"
SEERR = WURZEL / "backend" / "app" / "services" / "seerr"
ROUTER = WURZEL / "backend" / "app" / "routers" / "seerr_umzug.py"

#: Bodenschwelle: So viele Saetze gibt es mindestens. Ein Waechter, der eine
#: leere Liste prueft, ist lebenslang gruen.
MINDESTENS = 60


def _saetze(sprache: str) -> dict[str, str]:
    daten = json.loads((SPRACHEN / f"{sprache}.json").read_text(encoding="utf-8"))
    return daten["setup"]["seerr"]["saetze"]


def _platzhalter_vorlage(text: str) -> set[str]:
    return set(re.findall(r"\{([a-z_]+)\}", text))


def _platzhalter_i18n(text: str) -> set[str]:
    return set(re.findall(r"\{\{([a-z_]+)\}\}", text))


def test_jede_kennung_hat_einen_text_in_beiden_sprachen() -> None:
    assert len(texte.VORLAGEN) >= MINDESTENS
    for sprache in ("de", "en"):
        vorhanden = _saetze(sprache)
        fehlend = sorted(k for k in texte.VORLAGEN if not isinstance(vorhanden.get(k), str))
        assert fehlend == [], (
            f"Ohne Text unter setup.seerr.saetze in {sprache}.json: {fehlend}. "
            "Die Oberflaeche zeigte dafuer den deutschen Rueckfall."
        )
        verwaist = sorted(k for k in vorhanden if k not in texte.VORLAGEN)
        assert verwaist == [], f"Texte ohne Kennung im Backend ({sprache}.json): {verwaist}"


def test_die_platzhalter_stimmen_ueberein() -> None:
    for sprache in ("de", "en"):
        vorhanden = _saetze(sprache)
        for kennung, vorlage in texte.VORLAGEN.items():
            erwartet = _platzhalter_vorlage(vorlage)
            ist = _platzhalter_i18n(vorhanden[kennung])
            assert ist == erwartet, (
                f"{sprache}.json {kennung}: Platzhalter {sorted(ist)} statt {sorted(erwartet)}"
            )


def _aufrufe(text: str) -> list[str]:
    """Der Inhalt jedes ``satz(...)``-Aufrufs, mit den Klammern darin.

    Ein regulaerer Ausdruck bis zur ersten schliessenden Klammer haette bei
    ``satz("a" if eintrag.get("is4k") else "b")`` nach ``get("is4k")``
    aufgehoert - und den zweiten Zweig nie gesehen.
    """
    gefunden = []
    start = 0
    while True:
        i = text.find("satz(", start)
        if i < 0:
            return gefunden
        tiefe, j = 0, i + len("satz(") - 1
        while j < len(text):
            if text[j] == "(":
                tiefe += 1
            elif text[j] == ")":
                tiefe -= 1
                if tiefe == 0:
                    break
            j += 1
        gefunden.append(text[i + len("satz("):j])
        start = j + 1


def test_jede_angeforderte_kennung_gibt_es() -> None:
    """``satz("x")`` mit einer Kennung, die VORLAGEN nicht kennt, faellt sonst
    erst im Betrieb als KeyError auf - beim Betreiber, mitten im Umzug."""
    gefunden: set[str] = set()
    for datei in [*SEERR.glob("*.py"), ROUTER]:
        text = datei.read_text(encoding="utf-8")
        # Alle Kennungen innerhalb eines ``satz(...)``-Aufrufs, auch in einem
        # bedingten Ausdruck (``satz("a" if x else "b")``). Ein Praefix wie
        # ``"nicht_dabei_"`` endet auf einen Unterstrich und ist keine Kennung;
        # die zusammengesetzten stehen unten in der Liste.
        for aufruf in _aufrufe(text):
            # Nur das erste Argument - die Zahlen dahinter tragen eigene
            # Woerter. Und kein Vergleichswert (``if art == "plex"``).
            erstes = aufruf.split(",")[0]
            gefunden.update(
                k for k in re.findall(r'(?<!== )"([a-z_]+)"', erstes) if not k.endswith("_")
            )
        # f-Strings wie satz(f"platz_{schluessel}") und satz("nicht_dabei_" + name)
        # decken die Ausnahmen ab; die dazugehoerigen Kennungen stehen unten.
    gefunden.update({
        "platz_radarr", "platz_radarr_uhd", "platz_sonarr", "platz_sonarr_uhd",
        "vorgabe_null_filme", "vorgabe_null_serien",
        "l_filme_je_zeitraum", "l_serien_je_zeitraum",
        "nicht_dabei_watchlist", "nicht_dabei_notification_targets",
        "nicht_dabei_override_rules", "nicht_dabei_discover_sliders",
        "nicht_dabei_passwords",
    })
    assert len(gefunden) >= 40, "Der Scan hat den Code nicht gelesen."
    unbekannt = sorted(k for k in gefunden if k not in texte.VORLAGEN)
    assert unbekannt == [], f"Angefordert, aber nicht in VORLAGEN: {unbekannt}"
    unbenutzt = sorted(k for k in texte.VORLAGEN if k not in gefunden)
    assert unbenutzt == [], f"In VORLAGEN, aber nirgends angefordert: {unbenutzt}"


def test_ein_satz_traegt_zahlen_und_rueckfall() -> None:
    s = texte.satz("kontingent_staffeln", grenze=5)
    assert s.kennung == "kontingent_staffeln"
    assert s.zahlen == {"grenze": 5}
    assert "5 Staffeln" in s.text
    assert s.als_dict() == {"kennung": "kontingent_staffeln", "zahlen": {"grenze": 5}, "text": s.text}
