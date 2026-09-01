"""Eine Serie ohne TVDB-Kennung trotzdem finden - ueber Sonarrs eigene Suche.

⚠️ **Warum es das braucht.** Sonarr legt Serien ausschliesslich ueber die
TVDB-Kennung an. Nexview leitet die aus TMDB ab, und bei Neuerscheinungen
fehlt sie dort regelmaessig - der Anfragende bekam dann eine Absage, obwohl
Sonarr dieselbe Serie ueber die eigene Suche kennt (Issue #5).

**Was die Messung dazu sagt** (01.09.2026, gegen eine echte Sonarr-Instanz):

* Sonarr fuehrt zu fast jeder Serie die **TMDB-Kennung** mit. Damit ist die
  Zuordnung kein Ratespiel: Wer dieselbe Nummer traegt, ist dieselbe Serie.
  Sechs Fassungen von "The Office" liessen sich so sauber trennen.
* Von vier Serien, denen bei TMDB die TVDB-Kennung wirklich fehlte, fand
  Sonarr **eine**. Bei den anderen dreien hat TheTVDB schlicht keinen
  Eintrag - unter keinem Titel, auch nicht unter dem englischen.
* ⚠️ **Ein Abgleich ueber den Titel waere gefaehrlich.** Zu "Still Water"
  (Thailand, 2026) bietet Sonarr "Still Waters" (Wales, 1995) an - ein
  Buchstabe Unterschied, eine voellig andere Serie. Deshalb ordnet dieses
  Modul **nur** ueber die TMDB-Kennung zu und legt alles Uebrige dem
  Menschen vor, statt es zu entscheiden.

**Die Form ist Seerrs Form**, und zwar bewusst: Wer von dort kommt, kennt das
Fenster mit den Vorschlaegen und weiss sofort, was von ihm erwartet wird. Ein
eigener Weg waere vielleicht klarer gewesen und ganz sicher fremder.

Zwei Dinge macht Nexview trotzdem anders, und beide kosten den Wiedererkennungs-
wert nichts:

* **Wer eindeutig ist, bekommt gar kein Fenster.** Traegt ein Treffer dieselbe
  TMDB-Kennung, ist nichts zu entscheiden.
* **Unter der Liste steht, was ist**, wenn nichts davon passt - und bei einer
  Serie von 1978 etwas anderes als bei einer von vorletzter Woche.
"""

from __future__ import annotations

from dataclasses import dataclass
from .sonarr import SonarrClient

#: Wie viele Vorschlaege hoechstens - in **Sonarrs** Reihenfolge.
#:
#: Sonarr liefert bis zu zwanzig und sortiert sie selbst nach Passgenauigkeit.
#: Diese Reihenfolge zu uebernehmen statt eine eigene zu rechnen, ist keine
#: Bequemlichkeit: Eine eigene Schwelle waere eine geratene Zahl, die
#: entscheidet, was jemand zu sehen bekommt. Sechs ist die Zahl, die auch
#: Seerr zeigt.
HOECHSTENS = 6


@dataclass(frozen=True)
class Kandidat:
    """Ein Vorschlag aus Sonarr, so wie ihn die Oberflaeche zeigt."""

    tvdb_id: int
    title: str
    year: int | None
    overview: str
    poster_url: str | None
    tmdb_id: int | None
    #: Welche Staffeln Sonarr zu dieser Serie **gerade** kennt. Leer heisst
    #: nicht "keine", sondern oft "noch nicht geladen" - deshalb wird darauf
    #: nichts gefiltert, nur angezeigt.
    seasons: tuple[int, ...] = ()


@dataclass(frozen=True)
class Zuordnung:
    """Das Ergebnis: entweder eindeutig, oder eine Vorlage, oder nichts."""

    #: Gesetzt, wenn genau ein Treffer dieselbe TMDB-Kennung traegt. Dann wird
    #: nichts gefragt - die Anfrage laeuft durch, als haette nie etwas gefehlt.
    tvdb_id: int | None = None
    #: Aehnlich benannte Treffer, wenn es keinen eindeutigen gab. Leer heisst:
    #: Es kommt nichts auch nur nahe, ein Auswahlfenster waere eine Zumutung.
    kandidaten: tuple[Kandidat, ...] = ()

    @property
    def eindeutig(self) -> bool:
        return self.tvdb_id is not None


def _poster(bilder: object) -> str | None:
    """Die Posteradresse aus Sonarrs ``images``-Liste."""
    if not isinstance(bilder, list):
        return None
    for bild in bilder:
        if not isinstance(bild, dict) or bild.get("coverType") != "poster":
            continue
        adresse = bild.get("remoteUrl") or bild.get("url")
        if isinstance(adresse, str) and adresse:
            return adresse
    return None


def _als_kandidat(roh: dict) -> Kandidat | None:
    tvdb_id = roh.get("tvdbId")
    if not isinstance(tvdb_id, int) or tvdb_id <= 0:
        return None
    tmdb_id = roh.get("tmdbId")
    jahr = roh.get("year")
    return Kandidat(
        tvdb_id=tvdb_id,
        title=str(roh.get("title") or ""),
        # ⚠️ Sonarr schickt 0 statt null, wenn es die Angabe nicht kennt -
        # sowohl beim Jahr als auch bei der TMDB-Kennung. Eine 0 durchzulassen
        # hiesse, sie fuer echt zu halten; siehe ``calendar._kennung``.
        year=jahr if isinstance(jahr, int) and jahr > 1800 else None,
        overview=str(roh.get("overview") or ""),
        poster_url=_poster(roh.get("images")),
        tmdb_id=tmdb_id if isinstance(tmdb_id, int) and tmdb_id > 0 else None,
        seasons=tuple(
            nummer
            for eintrag in (roh.get("seasons") or [])
            if isinstance(nummer := eintrag.get("seasonNumber"), int)
        ),
    )


async def zuordnen(client: SonarrClient, tmdb_id: int, *titel: str) -> Zuordnung:
    """Die TVDB-Kennung einer Serie ueber Sonarr suchen.

    ⚠️ **Hier wird nicht nach Staffeln gefiltert, und das war ein Umweg wert.**
    Kurz stand hier ein Filter: Vorschlaege ohne die gewuenschte Staffel
    fielen weg. Der Anlass war echt - "Still Waters" meldete null Staffeln und
    liess sich danach nicht bedienen. Die Zahl war aber nur *voruebergehend*
    null: Sonarr laedt die Metadaten einer Serie asynchron, und Minuten
    spaeter meldete dieselbe Serie die Staffeln 0 und 1.

    Ein Filter darauf haette also **richtige** Serien verschwinden lassen,
    je nachdem wie warm Sonarrs Zwischenspeicher gerade ist - und niemand
    haette den Grund gesehen. Der Fehler lag woanders: ``staffeln_ueberwachen``
    hielt "noch keine Staffeln" fuer "kennt diese Staffel nicht". Dort ist er
    behoben; hier bleibt die Liste vollstaendig.

    ``titel`` sind die Namen, unter denen gesucht wird - der angezeigte und,
    falls abweichend, der Originaltitel. Bei einer thailaendischen Serie kennt
    TheTVDB oft nur den englischen Namen, bei einer franzoesischen nur den
    franzoesischen; beide zu versuchen kostet einen zweiten Aufruf und hat in
    der Messung keinen Fall zusaetzlich geloest, aber auch keinen verloren.

    ⚠️ **Eindeutig heisst: genau einer.** Traegen zwei Treffer dieselbe
    TMDB-Kennung, ist das kein Grund, den ersten zu nehmen - dann weiss Sonarr
    selbst nicht, welche Serie gemeint ist, und der Mensch entscheidet.
    """
    gesehen: dict[int, Kandidat] = {}
    namen = [name for name in titel if name and name.strip()]

    for name in dict.fromkeys(namen):  # Reihenfolge halten, Doppelte weg
        for roh in await client.suche(name):
            kandidat = _als_kandidat(roh)
            if kandidat is not None:
                gesehen.setdefault(kandidat.tvdb_id, kandidat)

    passend = [k for k in gesehen.values() if k.tmdb_id == tmdb_id]
    if len(passend) == 1:
        return Zuordnung(tvdb_id=passend[0].tvdb_id)

    # Sonarrs eigene Reihenfolge, oben gekappt. ``gesehen`` ist ein dict und
    # haelt sie seit Python 3.7 ein - der erste Treffer der ersten Suche steht
    # also auch hier vorn.
    return Zuordnung(kandidaten=tuple(gesehen.values())[:HOECHSTENS])


def erlaubt(zuordnung: Zuordnung, tvdb_id: int) -> bool:
    """Steht diese Kennung wirklich auf der Liste, die wir vorgelegt haben?

    ⚠️ **Der Grund, warum die Auswahl nicht einfach uebernommen wird.** Die
    Oberflaeche schickt eine Zahl, und ``RequestCreate`` haelt sich sonst
    streng daran, dass Titel und Kennungen vom Server kommen - "so kann
    niemand ueber den Browser falsche Angaben unterschieben". Eine frei
    waehlbare TVDB-Kennung waere genau diese Luecke: Sie ginge an TMDB vorbei
    und damit auch an der Altersbeschraenkung, die dort haengt.

    Deshalb wird dieselbe Suche noch einmal gefahren und geprueft, ob die
    gewaehlte Kennung darin vorkommt.
    """
    return any(k.tvdb_id == tvdb_id for k in zuordnung.kandidaten)
