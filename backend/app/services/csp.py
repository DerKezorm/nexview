"""Die Inhaltsregeln der Seite - ``Content-Security-Policy``.

Sie sagen dem Browser, woher er etwas laden darf und wohin er etwas schicken
darf. Alles, was nicht dasteht, wird abgelehnt.

⚠️ **Was sie hier wirklich bringt - und was nicht.** Der naheliegende Gedanke
ist "Schutz gegen eingeschleuste Skripte". Der Weg ist bei Nexview aber
ohnehin zu: Das Frontend benutzt nirgends ``dangerouslySetInnerHTML``,
``innerHTML`` oder ``eval``, und React setzt jeden fremden Text als Text.

Der echte Gewinn ist ein anderer, und er ergaenzt genau das Cookie aus 0.21:

* ``connect-src 'self'`` - ein boesartig gewordenes Paket im Buendel kann den
  Zugangs-Token **nicht nach Hause telefonieren**. Das ist die Luecke, die das
  HttpOnly-Cookie offen laesst: Es verhindert, dass der Ausweis mitgenommen
  wird, nicht dass er benutzt wird.
* ``frame-ancestors`` - niemand kann Nexview in seine eigene Seite einbetten
  und Klicks abfangen.
* ``object-src 'none'``, ``base-uri 'self'``, ``form-action 'self'`` - drei
  Zeilen, die nichts kosten und drei alte Angriffswege zumachen.

**Die Regeln sind eng, weil Nexview wenig braucht.** Die ganze Aussenwelt sind
Poster und ein Trailer. Keine Schriften, keine Skripte, keine Zaehlpixel von
Dritten. Wie viele Bild-Adressen dazugehoeren, ist allerdings nicht so
offensichtlich, wie es klingt - siehe ``BILDQUELLEN``.

⚠️ **Der Notausschalter ist kein Zierrat.** Eine zu enge Regel zeigt keine
Fehlermeldung - sie zeigt eine halb geladene oder weisse Seite, und der
Betroffene schreibt kein Issue, der deinstalliert. ``NEXVIEW_CSP=off`` muss
deshalb ohne Nachdenken erreichbar sein, und ``report-only`` erlaubt es, die
Regeln erst einmal nur **melden** zu lassen.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger("nexview.csp")

# Von wo Bilder kommen duerfen.
#
# ⚠️ **Die Liste ist laenger, als der Quelltext vermuten laesst.** TMDB steht
# fest im Code (``services/tmdb.py`` baut die Adressen). Die Kalender-Poster
# dagegen reicht ``services/calendar.py`` **durch**, so wie Radarr und Sonarr
# sie gespeichert haben - und das ist der Metadaten-Anbieter des jeweiligen
# Hauses, nicht unsere Entscheidung. Beim Bau dieser Regeln fiel genau das
# durch: Der Kalender blieb leer, weil dort ``artworks.thetvdb.com`` steht.
#
# Deshalb sind hier die ueblichen Anbieter aufgezaehlt - und deshalb gibt es
# ``NEXVIEW_IMG_SOURCES``. Wessen Aufbau etwas anderes liefert, sieht keine
# Fehlermeldung, sondern leere Poster; das ist die unangenehmste Art zu
# scheitern, und der Ausweg gehoert deshalb in die README.
BILDQUELLEN = (
    "https://image.tmdb.org",
    # Sonarr v4 holt seine Poster von hier ...
    "https://artworks.thetvdb.com",
    # ... aeltere Bestaende noch von den alten Adressen.
    "https://www.thetvdb.com",
    "https://thetvdb.com",
    # Fuer Bibliotheken, die ihre Bilder von fanart.tv beziehen.
    "https://assets.fanart.tv",
    # Das Vorschaubild zum Trailer. Die Adresse steht **nirgends** im
    # Quelltext - sie taucht auf, sobald die YouTube-Einbettung geoeffnet
    # wird, und wird dabei gegen *unsere* Regeln geprueft (nachgemessen).
    # Wer YouTube ohnehin einbettet, gewinnt nichts dadurch, dessen
    # Bilderdienst auszusperren.
    "https://i.ytimg.com",
)

# Der Trailer. Bewusst die datensparsame Adresse - dieselbe, die
# ``TrailerModal`` einbettet.
TRAILERQUELLE = "https://www.youtube-nocookie.com"

# Inline-Skripte in der ausgelieferten ``index.html``. Es ist genau eines: die
# Themen-Vorwegnahme, damit beim Laden nicht kurz die dunkle Seite aufblitzt.
# Es kann nicht in eine eigene Datei wandern, ohne genau das wieder kaputt zu
# machen - deshalb steht es hier als Pruefsumme.
_SKRIPT_MUSTER = re.compile(r"<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>", re.DOTALL)


def _hashes(index_html: Path | str | None) -> list[str]:
    """Pruefsummen aller Inline-Skripte der ausgelieferten Seite.

    ⚠️ **Bewusst beim Start berechnet statt im Quelltext hinterlegt.** Eine
    festgeschriebene Pruefsumme laeuft beim ersten Zeichen, das jemand am
    Skript aendert, auseinander - und der Fehler zeigt sich dann nicht beim
    Bauen, sondern als weisse Seite bei einem Fremden. So kann es gar nicht
    erst passieren.

    Statt eines Pfads darf auch der **fertige Seitentext** kommen: Mit
    gesetztem Unterpfad (``NEXVIEW_URL_BASE``) wird ``index.html`` beim Start
    umgeschrieben und bekommt ein zusaetzliches Inline-Skript - die Summen
    muessen aus genau dieser ausgelieferten Fassung stammen, nicht aus der
    Datei auf der Platte.
    """
    if index_html is None:
        return []
    if isinstance(index_html, Path):
        if not index_html.exists():
            return []
        seitentext = index_html.read_text(encoding="utf-8")
    else:
        seitentext = index_html

    gefunden = []
    for inhalt in _SKRIPT_MUSTER.findall(seitentext):
        summe = base64.b64encode(hashlib.sha256(inhalt.encode("utf-8")).digest()).decode()
        gefunden.append(f"'sha256-{summe}'")
    return gefunden


def _frame_ancestors(einstellung: str) -> str:
    """Wer Nexview in einen Rahmen stecken darf.

    Vorgabe ist ``none``. Wer Nexview in ein Übersichts-Brett wie Organizr
    haengt, stellt ``self`` oder die Adresse des Bretts ein - sonst bleibt der
    Rahmen dort leer, und zwar ohne jede Fehlermeldung.
    """
    wert = (einstellung or "none").strip()
    if wert.lower() in ("none", ""):
        return "'none'"
    if wert.lower() == "self":
        return "'self'"
    # Alles andere sind Adressen, wie der Betreiber sie geschrieben hat.
    return wert


def regeln(
    index_html: Path | str | None,
    frame_ancestors: str = "none",
    zusaetzliche_bildquellen: str = "",
) -> str:
    """Die fertige Regelzeile."""
    skripte = " ".join(["'self'", *_hashes(index_html)])
    bilder = " ".join(
        ["'self'", "data:", *BILDQUELLEN, *(zusaetzliche_bildquellen or "").split()]
    )

    return "; ".join(
        [
            # Alles, was unten nicht ausdruecklich anders steht, kommt von uns.
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "form-action 'self'",
            f"frame-ancestors {_frame_ancestors(frame_ancestors)}",
            f"script-src {skripte}",
            # ⚠️ ``unsafe-inline`` fuer Stile ist Absicht und kein Versaeumnis.
            # React setzt seine ``style``-Angaben zwar ueber das CSSOM, das die
            # Regel gar nicht erfasst - aber ohne diesen Eintrag faellt jedes
            # ``style="..."`` im Markup still aus, und ein Ausfall, den man nur
            # sieht wenn man hinschaut, ist der teuerste. Er kostet hier auch
            # wenig: Ein eingeschleuster Stil kann keinen Code ausfuehren.
            "style-src 'self' 'unsafe-inline'",
            f"img-src {bilder}",
            "font-src 'self'",
            # **Der wichtigste Eintrag der ganzen Liste.** Kein Skript auf
            # dieser Seite kann Daten an eine fremde Adresse schicken.
            "connect-src 'self'",
            f"frame-src {TRAILERQUELLE}",
            # Der Protokoll-Download baut sich eine blob:-Adresse.
            "media-src 'self' blob:",
            "worker-src 'none'",
        ]
    )


def kopfzeile(
    modus: str,
    index_html: Path | str | None,
    frame_ancestors: str = "none",
    zusaetzliche_bildquellen: str = "",
):
    """Name und Inhalt der Kopfzeile - oder ``None``, wenn abgeschaltet.

    ``report-only`` schickt dieselben Regeln unter einem anderen Namen: Der
    Browser haelt sich **nicht** daran, meldet Verstoesse aber in seiner
    Konsole. Der Weg fuer alle, die erst nachsehen und dann scharf schalten
    wollen.
    """
    wert = (modus or "on").strip().lower()
    if wert == "off":
        return None
    if wert not in ("on", "report-only"):
        logger.warning(
            "NEXVIEW_CSP is set to %r, which is not understood. Allowed: on, "
            "report-only, off. Falling back to on.",
            modus,
        )
        wert = "on"

    name = (
        "Content-Security-Policy-Report-Only"
        if wert == "report-only"
        else "Content-Security-Policy"
    )
    return name, regeln(index_html, frame_ancestors, zusaetzliche_bildquellen)
