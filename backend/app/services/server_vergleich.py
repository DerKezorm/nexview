"""Welcher Titel liegt auf welchem Medienserver - Titel fuer Titel.

Der Abgleich (``abgleich.py``) sagt "244 Titel fehlen auf mindestens einem
Server". Damit kann niemand etwas anfangen: welche, und wo? Hier entsteht die
Tabelle dahinter - eine Zeile je Titel, eine Spalte je Server.

**Wie Zeilen verschiedener Server zu einem Titel werden:** ueber jede Nummer,
die sie teilen - TMDB, TVDB, IMDb - und zuletzt ueber Titel und Jahr. Teilen
zwei Zeilen irgendeine davon, sind sie derselbe Titel; das gilt auch ueber
Umwege (Plex und Jellyfin teilen die TMDB-Nummer, Jellyfin und Emby nur die
IMDb-Nummer, also sind es alle drei).

⚠️ **Warum nicht nur die TMDB-Nummer.** So zaehlte der Abgleich bis 0.34.0,
und gemeldet wurde es in Issue #10: Fuehrt ein Server einen Film unter einer
anderen TMDB-Nummer, zaehlte er als "fehlt" - und zwar auf **beiden** Seiten.
458 gemeldete Luecken, von denen rund 200 gar keine waren.

⚠️ **Eine andere Nummer ist trotzdem ein Befund, nur ein anderer.** Nexview
erkennt Titel ueber TMDB (Filme) und TVDB (Serien). Fuehrt ein Server die
falsche, sieht Nexview den Titel dort nicht. Die Zelle heisst deshalb nicht
"da", sondern "andere Nummer".

⚠️ **Gelesen wird nur die Datenbank.** Die Bibliothek liegt nach dem
stuendlichen Einlesen in ``media_server_library``; ein Seitenaufruf fragt
keinen Server.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MediaServerLibraryItem, MediaType
from . import abgleich

#: Feste Reihenfolge der Spalten. Unbekannte Anbieter kommen dahinter.
REIHENFOLGE = ("plex", "jellyfin", "emby")

#: Nummern im Ordner- oder Dateinamen, wie Radarr und Sonarr sie schreiben:
#: ``{tmdb-1900}`` (Plex), ``[tmdbid-1900]`` (Jellyfin), ``[tmdbid=1900]`` (Emby).
PFAD_NUMMER = re.compile(r"[\[{](tmdb|tvdb|imdb)(?:id)?[-=](tt\d+|\d+)[\]}]", re.IGNORECASE)

def datei_schluessel(art: str, pfad: str) -> str | None:
    """Woran man dieselbe Datei auf verschiedenen Servern erkennt.

    ⚠️ **Nicht der ganze Pfad.** Jeder Server haengt die Mediathek woanders
    ein - ``/media/Movies`` bei Plex, ``/data/Movies`` bei Jellyfin, gemessen
    an einer echten Anlage. Gleich bleibt nur das Ende: bei Filmen Ordner und
    Datei (``8 (2020) {tmdb-605802}/8 (2020) … .mkv``), bei Serien der Ordner.
    Der Dateiname allein waere zu wenig - ``movie.mkv`` kann in hundert
    Ordnern liegen.
    """
    teile = [t for t in pfad.replace("\\", "/").split("/") if t]
    if not teile:
        return None
    ende = teile[-2:] if art == MediaType.movie.value else teile[-1:]
    if art == MediaType.movie.value and len(ende) < 2:
        return None
    return "/".join(ende).casefold()


#: "Alexander (2004)" als Titel - das Jahr steht im Namen, nicht im Feld.
TITEL_MIT_JAHR = re.compile(r"^(.*?)[\s._-]*\((\d{4})\)")

#: Zustaende, in denen Nexview den Titel auf diesem Server nicht sieht.
#: "anders_erkannt": Die Datei liegt dort, der Server fuehrt sie aber als
#: einen anderen Titel.
FEHLT = ("fehlt", "anders_erkannt")

#: Die Ansichten - dieselben Woerter stehen in den Befund-Zielen.
ANSICHTEN = ("unterschiede", "andere_nummer", "jahr", "ohne_kennung", "nur_arr", "alle")


@dataclass
class _Roh:
    provider: str
    art: str
    tmdb: int | None
    tvdb: int | None
    imdb: str | None
    titel: str
    titel_key: str
    jahr: int | None
    pfade: list[str] = field(default_factory=list)
    schluessel: str | None = None
    #: Nummern aus dem Pfad - nur bei Zeilen, denen der Server keine zuordnete.
    pfad_nummern: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Zelle:
    zustand: str  # "da" | "fehlt" | "andere_nummer" | "anders_erkannt"
    tmdb: list[int] = field(default_factory=list)
    tvdb: list[int] = field(default_factory=list)
    imdb: list[str] = field(default_factory=list)
    jahr: int | None = None
    titel: str | None = None
    #: Wo der Server den Titel liegen hat - leer, solange seit dem Einbau
    #: noch nicht neu eingelesen wurde.
    pfade: list[str] = field(default_factory=list)
    #: Die Nummer beim Anbieter, um fehlende Pfade nachzuschlagen.
    schluessel: str | None = None


@dataclass
class Zeile:
    kennung: str
    titel: str
    jahr: int | None
    art: str
    #: Worueber die Server zusammengefunden haben: tmdb, tvdb, imdb, titel -
    #: "einzeln" bei nur einem Server, "arr" fuer Titel nur in Radarr/Sonarr.
    zuordnung: str
    zellen: dict[str, Zelle]
    jahr_uneinig: bool = False
    ohne_kennung: bool = False

    def fehlt_irgendwo(self) -> bool:
        return any(z.zustand in FEHLT for z in self.zellen.values())

    def nummer_uneinig(self) -> bool:
        return any(z.zustand == "andere_nummer" for z in self.zellen.values())


def anbieter_sortieren(anbieter: set[str] | list[str]) -> list[str]:
    return sorted(
        anbieter,
        key=lambda a: (REIHENFOLGE.index(a) if a in REIHENFOLGE else len(REIHENFOLGE), a),
    )


def _laden(db: Session) -> list[_Roh]:
    t = MediaServerLibraryItem
    ergebnis = db.execute(
        select(
            t.provider,
            t.media_type,
            t.tmdb_id,
            t.tvdb_id,
            t.imdb_id,
            t.title,
            t.title_key,
            t.year,
            t.file_paths,
            t.rating_key,
        )
    )
    zeilen = [
        _Roh(
            provider=p,
            art=m.value if isinstance(m, MediaType) else str(m),
            tmdb=tmdb,
            tvdb=tvdb,
            imdb=(imdb or "").strip().lower() or None,
            titel=titel or "",
            titel_key=key or "",
            jahr=jahr,
            pfade=[z for z in (pfade or "").splitlines() if z.strip()],
            schluessel=schluessel,
        )
        for p, m, tmdb, tvdb, imdb, titel, key, jahr, pfade, schluessel in ergebnis
    ]
    for zeile in zeilen:
        _unerkannte_zeile_deuten(zeile)
    return zeilen


def _unerkannte_zeile_deuten(zeile: _Roh) -> None:
    """Einer Zeile ohne jede Nummer aus Pfad und Namen helfen.

    ⚠️ **Gemessen an einer echten Jellyfin-Bibliothek (17.09.2026).** Radarr
    benennt Ordner fuer Plex: ``Traffic (2000) {tmdb-1900}``. Jellyfin erwartet
    ``[tmdbid-1900]``, erkennt den Film nicht - und fuehrt ihn als "2BA" mit
    dem Jahr **1900**, also der TMDB-Nummer. Ueber Titel und Jahr faende so ein
    Eintrag nie sein Gegenstueck. Die Nummer im Pfad dagegen stimmt.

    Nur fuer die Zuordnung: Die Zeile bleibt "ohne Kennung", denn fuer Nexview
    ist sie das - der Server selbst kennt die Nummer nicht.
    """
    if zeile.tmdb is not None or zeile.tvdb is not None or zeile.imdb:
        return
    gefunden: list[tuple[str, str]] = []
    for pfad in zeile.pfade:
        for quelle, wert in PFAD_NUMMER.findall(pfad):
            eintrag = (quelle.lower(), wert.lower())
            if eintrag not in gefunden:
                gefunden.append(eintrag)
    zeile.pfad_nummern = gefunden

    treffer = TITEL_MIT_JAHR.match(zeile.titel)
    if treffer and treffer.group(1).strip():
        zeile.titel_key = titel_schluessel(treffer.group(1))
        zeile.jahr = int(treffer.group(2))


def gruppieren(zeilen: list[_Roh]) -> list[list[_Roh]]:
    """Zeilen, die sich eine Nummer oder Titel und Jahr teilen, zusammenlegen.

    ⚠️ **Titel und Jahr mit einem Jahr Spielraum**, wie ueberall in Nexview
    (``sonarr.jahre_passen``): Festival- und Kinostart fallen oft in
    verschiedene Jahre. Ohne Jahr kein Titelvergleich - "Fargo" allein ist
    nicht genug.
    """
    eltern = list(range(len(zeilen)))

    def wurzel(i: int) -> int:
        while eltern[i] != i:
            eltern[i] = eltern[eltern[i]]
            i = eltern[i]
        return i

    def verbinden(a: int, b: int) -> None:
        ra, rb = wurzel(a), wurzel(b)
        if ra != rb:
            eltern[max(ra, rb)] = min(ra, rb)

    erste: dict[tuple, int] = {}

    def merken(schluessel: tuple, i: int) -> None:
        if schluessel in erste:
            verbinden(i, erste[schluessel])
        else:
            erste[schluessel] = i

    ohne_nummer: list[int] = []
    for i, z in enumerate(zeilen):
        if z.tmdb is not None:
            merken(("tmdb", z.art, z.tmdb), i)
        if z.tvdb is not None:
            merken(("tvdb", z.art, z.tvdb), i)
        if z.imdb:
            merken(("imdb", z.art, z.imdb), i)
        if z.tmdb is None and z.tvdb is None and not z.imdb:
            ohne_nummer.append(i)
            for quelle, wert in z.pfad_nummern:
                merken((quelle, z.art, int(wert) if wert.isdigit() else wert), i)

    # ⚠️ **Titel und Jahr nur fuer Zeilen ganz ohne Nummer.** Zwei Zeilen mit
    # verschiedenen Nummern sind zwei Titel, auch wenn Name und Jahr gleich
    # lauten - sonst verschmelzen zwei Filme namens "Ein Film" (2020), und ein
    # fehlender faellt nicht mehr auf.
    nach_titel: dict[tuple, list[int]] = defaultdict(list)
    for i, z in enumerate(zeilen):
        if z.titel_key and z.jahr is not None:
            nach_titel[(z.art, z.titel_key, z.jahr)].append(i)
    ohne_nummer_satz = set(ohne_nummer)
    for i in ohne_nummer:
        z = zeilen[i]
        if not z.titel_key or z.jahr is None:
            continue
        treffer = [
            j
            for jahr in (z.jahr - 1, z.jahr, z.jahr + 1)
            for j in nach_titel.get((z.art, z.titel_key, jahr), [])
            if j != i
        ]
        # Nur wenn eindeutig: Passen Titel und Jahr auf zwei verschiedene
        # Titel mit Nummer, raet die Zuordnung nicht.
        mit_nummer = {wurzel(j) for j in treffer if j not in ohne_nummer_satz}
        if len(mit_nummer) <= 1:
            for j in treffer:
                verbinden(i, j)

    gruppen: dict[int, list[_Roh]] = defaultdict(list)
    for i, z in enumerate(zeilen):
        gruppen[wurzel(i)].append(z)
    return list(gruppen.values())


def _mehrheit(werte_je_server: dict[str, set]) -> object | None:
    """Der Wert, den die meisten Server fuehren - bei Gleichstand keiner."""
    zaehler = Counter(w for werte in werte_je_server.values() for w in werte)
    if not zaehler:
        return None
    ((erster, n), *rest) = zaehler.most_common(2) + [(None, 0)]
    return erster if n > rest[0][1] else None


def _zeile_bauen(gruppe: list[_Roh], alle_server: list[str]) -> Zeile:
    je_server: dict[str, list[_Roh]] = defaultdict(list)
    for z in gruppe:
        je_server[z.provider].append(z)
    vorhanden = [s for s in alle_server if s in je_server]
    art = gruppe[0].art

    def werte(attr: str) -> dict[str, set]:
        return {
            s: {getattr(z, attr) for z in je_server[s] if getattr(z, attr) is not None}
            for s in vorhanden
        }

    tmdb, tvdb, imdb = werte("tmdb"), werte("tvdb"), werte("imdb")

    zuordnung = "einzeln" if len(vorhanden) < 2 else "titel"
    if len(vorhanden) >= 2:
        for name, satz in (("tmdb", tmdb), ("tvdb", tvdb), ("imdb", imdb)):
            if set.intersection(*satz.values()):
                zuordnung = name
                break
        else:
            # Kein Server-Paar teilt eine Nummer - aber vielleicht steht sie
            # im Pfad der Zeile, die der Server nicht erkannt hat.
            mit_pfad = {
                s: {
                    (q, int(w) if w.isdigit() else w)
                    for z in je_server[s]
                    for q, w in z.pfad_nummern
                }
                | {("tmdb", n) for n in tmdb[s]}
                | {("tvdb", n) for n in tvdb[s]}
                | {("imdb", n) for n in imdb[s]}
                for s in vorhanden
            }
            if set.intersection(*mit_pfad.values()):
                zuordnung = "pfad"

    # Die Nummer, ueber die Nexview den Titel erkennt: TMDB bei Filmen, bei
    # Serien zusaetzlich TVDB. Weicht ein Server davon ab, sieht Nexview ihn
    # dort nicht - auch wenn eine andere Nummer die Zeilen zusammengebracht hat.
    uneinig: set[str] = set()
    if len(vorhanden) >= 2:
        pruefen = [tmdb] + ([tvdb] if art == MediaType.tv.value else [])
        for satz in pruefen:
            if not any(satz.values()) or set.intersection(*satz.values()):
                continue
            ziel = _mehrheit(satz)
            for s, w in satz.items():
                if ziel is None or ziel not in w:
                    uneinig.add(s)

    jahre = [z.jahr for z in gruppe if z.jahr is not None]
    jahr_uneinig = (
        len(vorhanden) >= 2
        and len(jahre) >= 2
        and max(jahre) - min(jahre) > abgleich.JAHR_TOLERANZ
    )

    zellen: dict[str, Zelle] = {}
    for s in alle_server:
        if s not in je_server:
            zellen[s] = Zelle(zustand="fehlt")
            continue
        erste = je_server[s][0]
        zellen[s] = Zelle(
            zustand="andere_nummer" if s in uneinig else "da",
            tmdb=sorted(tmdb[s]),
            tvdb=sorted(tvdb[s]),
            imdb=sorted(imdb[s]),
            jahr=min((z.jahr for z in je_server[s] if z.jahr is not None), default=None),
            titel=erste.titel or None,
            pfade=list(dict.fromkeys(p for z in je_server[s] for p in z.pfade)),
            schluessel=erste.schluessel,
        )

    kopf = je_server[vorhanden[0]][0]
    kennung = (
        f"{art}:tmdb:{min(w for v in tmdb.values() for w in v)}"
        if any(tmdb.values())
        else f"{art}:{kopf.provider}:{kopf.titel_key}:{kopf.jahr}"
    )
    return Zeile(
        kennung=kennung,
        titel=kopf.titel,
        jahr=min(jahre) if jahre else None,
        art=art,
        zuordnung=zuordnung,
        zellen=zellen,
        jahr_uneinig=jahr_uneinig,
        ohne_kennung=any(z.tmdb is None and z.tvdb is None for z in gruppe),
    )


def _anders_erkannte_markieren(roh: list[_Roh], zeilen: list[Zeile]) -> None:
    """Fehlt ein Titel auf einem Server, liegt seine Datei dort aber - als was?

    ⚠️ **Nicht zusammenlegen, nur benennen.** Der naheliegende Weg waere, Zeilen
    mit derselben Datei zu einer zu verschmelzen. Gemessen an einer echten
    Anlage verkettet das verschiedene Filme: Jellyfin fuehrte die Datei von
    "8 - The Soul Collector" als "The Hateful 8", und ueber dessen Nummer hing
    daran Plex' echtes "The Hateful 8" - zwei Filme in einer Zeile. Deshalb
    bleibt die Zeile, wie sie ist; die Zelle sagt nur, was dort stattdessen
    liegt.
    """
    je_datei: dict[tuple[str, str, str], _Roh] = {}
    for z in roh:
        for pfad in z.pfade:
            datei = datei_schluessel(z.art, pfad)
            if datei:
                je_datei.setdefault((z.provider, z.art, datei), z)

    for zeile in zeilen:
        dateien = {
            d
            for zelle in zeile.zellen.values()
            for pfad in zelle.pfade
            if (d := datei_schluessel(zeile.art, pfad))
        }
        for anbieter, zelle in zeile.zellen.items():
            if zelle.zustand != "fehlt":
                continue
            fremd = next(
                (je_datei[k] for d in sorted(dateien) if (k := (anbieter, zeile.art, d)) in je_datei),
                None,
            )
            if fremd is None:
                continue
            zeile.zellen[anbieter] = Zelle(
                zustand="anders_erkannt",
                tmdb=[fremd.tmdb] if fremd.tmdb is not None else [],
                tvdb=[fremd.tvdb] if fremd.tvdb is not None else [],
                imdb=[fremd.imdb] if fremd.imdb else [],
                jahr=fremd.jahr,
                titel=fremd.titel or None,
                pfade=list(fremd.pfade),
                schluessel=fremd.schluessel,
            )


def zeilen_bauen(db: Session) -> tuple[list[str], list[Zeile]]:
    """Alle Titel mit ihren Zellen, nach Titel sortiert."""
    roh = _laden(db)
    server = anbieter_sortieren({z.provider for z in roh})
    zeilen = [_zeile_bauen(g, server) for g in gruppieren(roh)]
    _anders_erkannte_markieren(roh, zeilen)
    zeilen.sort(key=lambda z: (z.titel.casefold(), z.jahr or 0, z.art))
    return server, zeilen


def luecke_zaehlen(db: Session) -> int:
    """Wie viele Titel die Server uneinig sehen - fuer den Abgleich.

    ⚠️ **Dieselbe Zahl wie die Ansicht "Unterschiede"**, also samt "andere
    Nummer". Fuer Nexview fehlt so ein Titel auf dem abweichenden Server
    ohnehin. Zaehlte der Befund anders als der Knopf, auf den "Ansehen" fuehrt,
    stuende dort eine andere Zahl - und man glaubt keiner von beiden.
    """
    server, zeilen = zeilen_bauen(db)
    if len(server) < 2:
        return 0
    return sum(1 for z in zeilen if passt(z, "unterschiede"))


def arr_zeilen(stand: abgleich.Stand, server: list[str]) -> list[Zeile]:
    """Titel mit Datei in Radarr/Sonarr, die kein Server kennt."""
    ergebnis = []
    for eintrag in stand.arr_ohne_server_titel:
        art = eintrag.get("art", "movie")
        nummer = eintrag.get("nummer")
        ergebnis.append(
            Zeile(
                kennung=f"arr:{art}:{nummer}",
                titel=eintrag.get("titel") or "",
                jahr=None,
                art=art,
                zuordnung="arr",
                zellen={s: Zelle(zustand="fehlt") for s in server},
            )
        )
    ergebnis.sort(key=lambda z: z.titel.casefold())
    return ergebnis


def titel_schluessel(text: str) -> str:
    """Fuer die Suche: dieselbe Vereinfachung wie ``title_key``."""
    return "".join(c for c in text.casefold() if c.isalnum())


def passt(zeile: Zeile, ansicht: str) -> bool:
    if ansicht == "unterschiede":
        return zeile.fehlt_irgendwo() or zeile.nummer_uneinig()
    if ansicht == "andere_nummer":
        return zeile.nummer_uneinig() or any(
            z.zustand == "anders_erkannt" for z in zeile.zellen.values()
        )
    if ansicht == "jahr":
        return zeile.jahr_uneinig
    if ansicht == "ohne_kennung":
        return zeile.ohne_kennung
    return True
