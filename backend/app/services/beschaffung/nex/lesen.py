"""Die Lesewege des NEX-Betriebs: Kacheln, Warteschlange, Platz, Kalender, Wertungen.

⚠️ **Die Formen sind die von heute, nicht die des Plans.** Kalender und
Datenträger geben dieselben Wörterbücher zurück, die Radarr und Sonarr
liefern; `services/calendar.py` und `services/storage.py` bedienen beide
Betriebsarten und bleiben dadurch unangetastet. Die Normalform des Plans
(Abschnitt 3.1) würde bedeuten, 800 Zeilen umzuschreiben, die im ARR-Betrieb
heute richtig laufen - und genau das soll keine Scheibe tun. Wo nexcrates
Antwort nicht in die alte Form passt, steht die Normalform dagegen sehr wohl:
`nachschlagen` (Bauplan Abschnitt 3.1) ersetzt „hol die ganze Bibliothek und
such dir deinen Titel über die TVDB-Kennung".

Gemessen am 22.09.2026, Protokoll in
`homelab/nexcrate-pruefstand/nexview-anbindung/messung-nexview.md`.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from ....schemas_media import MediaItem
from ..base import Grund, WarteschlangenEintrag, Warum
from . import bestand, mapping
from .client import KALENDER_TAGE

if TYPE_CHECKING:
    from .client import NexcrateClient

logger = logging.getLogger("nexview.nexcrate")


# --------------------------------------------------------------------------
# Kacheln


def zustand_der_kachel(eintrag: dict[str, Any], kennung: str) -> str | None:
    """„Liegt da", „liegt teilweise da" oder „noch nicht geladen".

    Dieselben drei Wörter wie im ARR-Betrieb (`library._status_for`): Die
    Oberfläche kennt sie, und die Hauptachse einer Karte heißt weiter
    `status`. Der Mittelweg gibt es nur bei Serien - eine Serie mit einer von
    elf Staffeln ist nicht „bereits geladen".
    """
    fassung = next(
        (f for f in eintrag.get("versions") or [] if str(f.get("version_id")) == kennung),
        None,
    )
    if fassung is None:
        return None
    if not mapping.hat_datei(fassung.get("state")):
        return "searching"
    zahlen = ((fassung.get("series") or {}).get("counts")) or {}
    gesendet = int(zahlen.get("aired") or 0)
    vorhanden = int(zahlen.get("have") or 0)
    if gesendet and vorhanden < gesendet:
        return "partial"
    return "downloaded"


async def kacheln_faerben(
    client: NexcrateClient, media_type: str, items: list[MediaItem], kennung: str
) -> list[MediaItem]:
    """Jeder Kachel ihren Zustand geben - ein Stapel, kein Aufruf je Kachel (N12)."""
    if not items:
        return items
    gefragt = [
        {"kind": mapping.kind(media_type), "ref": mapping.ref(item.tmdb_id)}
        for item in items
        if item.tmdb_id
    ]
    if not gefragt:
        return items
    nach_ref = {
        str(antwort.get("ref")): antwort.get("title")
        for antwort in await client.lookup(gefragt)
        if antwort.get("known") and antwort.get("title")
    }
    gefaerbt: list[MediaItem] = []
    for item in items:
        titel = nach_ref.get(mapping.ref(item.tmdb_id)) if item.tmdb_id else None
        zustand = zustand_der_kachel(titel, kennung) if titel else None
        # ⚠️ Kein Pfad: nexcrate nennt keinen, und die Grenze kennt keinen
        # (Bauplan 6.4). Der Ordner ist im NEX-Betrieb ein Sprung.
        gefaerbt.append(item.model_copy(update={"status": zustand}) if zustand else item)
    return gefaerbt


# --------------------------------------------------------------------------
# Warteschlange


def warteschlange(roh: list[dict[str, Any]], media_type: str) -> list[WarteschlangenEintrag]:
    """nexcrates Warteschlange in Nexviews Form.

    ⚠️ **Ein Eintrag je Download** (N26) - anders als bei Arr, wo ein
    Staffelpaket mehrere Zeilen mit gemeinsamer Download-Kennung hat. Zu
    falten gibt es hier nichts.

    ``remaining_bytes`` ist ``null``, sobald der Download nicht mehr läuft;
    dann bleibt nur die Größe, und „noch übrig" ist null.
    """
    gefunden: list[WarteschlangenEintrag] = []
    for eintrag in roh:
        titel = eintrag.get("title") or {}
        if mapping.art(str(titel.get("kind") or "")) != media_type:
            continue
        nummer = mapping.tmdb_aus(titel.get("ref"))
        if nummer is None:
            continue
        serie = eintrag.get("series") or {}
        folgen = serie.get("episodes") or []
        gefunden.append(
            WarteschlangenEintrag(
                arr_id=nummer,
                season=serie.get("season"),
                # Nur bei genau einer Folge ist die Zuordnung eindeutig; ein
                # Paket gehört der Staffel, nicht einer Folge.
                episode=folgen[0].get("episode") if len(folgen) == 1 else None,
                size=int(eintrag.get("size_bytes") or 0),
                sizeleft=int(eintrag.get("remaining_bytes") or 0),
            )
        )
    return gefunden


# --------------------------------------------------------------------------
# Freier Platz


def datentraeger(roh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """nexcrates Platz in der Form, die `storage.freier_platz` liest.

    ⚠️ **nexcrate meldet den Platz je Fassung, nicht je Datenträger**
    (gemessen: vier Fassungen auf einer Platte, viermal dieselbe Zahl mit
    demselben `volume`). Hier wird über `volume` gefaltet - sonst stünde
    dieselbe Platte vierfach in der Übersicht, und der Befund „Platte fast
    voll" käme vierfach.
    """
    je_traeger: dict[str, dict[str, Any]] = {}
    for eintrag in roh:
        name = str(eintrag.get("volume") or "")
        if not name or eintrag.get("free_bytes") is None:
            continue
        je_traeger.setdefault(
            name,
            {
                "path": name,
                "freeSpace": int(eintrag.get("free_bytes") or 0),
                "totalSpace": int(eintrag.get("total_bytes") or 0),
            },
        )
    return list(je_traeger.values())


# --------------------------------------------------------------------------
# Kalender


def _spannen(von: str, bis: str) -> list[tuple[str, str]]:
    """Den Zeitraum in Stücke zerlegen, die nexcrate annimmt.

    ⚠️ Gemessen: mehr als hundert Tage am Stück sind `422 invalid_input` mit
    `params.fields: ["to"]`. Nexviews Kalender fragt weitere Zeiträume - er
    muss sie selbst zerlegen.
    """
    try:
        start = date.fromisoformat(von[:10])
        ende = date.fromisoformat(bis[:10])
    except ValueError:
        return [(von, bis)]
    if ende < start:
        return []
    stuecke: list[tuple[str, str]] = []
    while start <= ende:
        schluss = min(ende, start + timedelta(days=KALENDER_TAGE - 1))
        stuecke.append((start.isoformat(), schluss.isoformat()))
        start = schluss + timedelta(days=1)
    return stuecke


def _leere_serie(eintrag: dict[str, Any], nummer: int) -> dict[str, Any]:
    """Die Serienangaben, die `calendar.py` an einem Folgen-Eintrag liest.

    Altersfreigabe, Stimmen und Genres kommen im NEX-Betrieb nicht aus der
    Beschaffung (nexcrate liefert sie nicht); Nexview ergänzt sie über TMDB
    wie bei jeder anderen Kachel. Hier stehen sie deshalb leer statt falsch.
    """
    return {
        "id": nummer,
        "tmdbId": nummer,
        "tvdbId": None,
        "title": str(eintrag.get("name") or ""),
        "images": [],
        "ratings": {},
        "genres": [],
        "monitored": bool(eintrag.get("monitored")),
        "firstAired": None,
        "certification": None,
    }


#: Welches Feld ein Filmtermin bei Radarr hätte - nexcrate sagt es in `date_kind`.
FILMTERMIN = {
    "cinema": "inCinemas",
    "theatrical": "inCinemas",
    "digital": "digitalRelease",
    "physical": "physicalRelease",
}


def kalender(roh: list[dict[str, Any]], media_type: str) -> list[dict[str, Any]]:
    """nexcrates Kalender in der Form, die `services/calendar.py` liest."""
    gefunden: list[dict[str, Any]] = []
    for eintrag in roh:
        art = mapping.art(str(eintrag.get("kind") or ""))
        if art != media_type:
            continue
        nummer = mapping.tmdb_aus(eintrag.get("ref"))
        if nummer is None:
            continue
        fassungen = eintrag.get("versions") or []
        hat_datei = any(mapping.hat_datei(f.get("state")) for f in fassungen)
        if art == "tv":
            serie = eintrag.get("series") or {}
            if serie.get("season") is None or serie.get("episode") is None:
                continue
            gefunden.append(
                {
                    "series": _leere_serie(eintrag, nummer),
                    "seriesId": nummer,
                    "seasonNumber": int(serie["season"]),
                    "episodeNumber": int(serie["episode"]),
                    "title": serie.get("name") or "",
                    "overview": "",
                    "airDateUtc": eintrag.get("date"),
                    "hasFile": hat_datei,
                    "monitored": bool(eintrag.get("monitored")),
                }
            )
            continue
        feld = FILMTERMIN.get(str(eintrag.get("date_kind") or ""), "digitalRelease")
        film = {
            "id": nummer,
            "tmdbId": nummer,
            "title": str(eintrag.get("name") or ""),
            "year": eintrag.get("year"),
            "images": [],
            "ratings": {},
            "genres": [],
            "certification": None,
            "monitored": bool(eintrag.get("monitored")),
            "hasFile": hat_datei,
            "inCinemas": None,
            "digitalRelease": None,
            "physicalRelease": None,
        }
        film[feld] = eintrag.get("date")
        gefunden.append(film)
    return gefunden


# --------------------------------------------------------------------------
# Wertungen


def _imdb(eintrag: dict[str, Any]) -> tuple[float | None, int]:
    """IMDb-Wert und Stimmen aus ``{"rating", "votes"}``.

    ⚠️ Das Feld heisst ``rating``, nicht ``value`` wie bei Radarr (nexcrates
    ``ImdbRatingOut``). Bis zum 24.09.2026 las Nexview ``value`` und bekam
    darum von einer nexcrate mit geladener IMDb-Datei keinen einzigen Wert.
    """
    imdb = eintrag.get("imdb")
    if isinstance(imdb, dict):
        wert = imdb.get("rating")
        stimmen = imdb.get("votes") or 0
    else:
        wert, stimmen = imdb, 0
    if not isinstance(wert, (int, float)) or isinstance(wert, bool):
        return None, 0
    return float(wert), int(stimmen) if isinstance(stimmen, int) else 0


def _prozent(wert: Any) -> int | None:
    return int(wert) if isinstance(wert, (int, float)) and not isinstance(wert, bool) else None


def _imdb_kennung(eintrag: dict[str, Any]) -> str | None:
    return str(eintrag.get("imdb_ref") or "").partition(":")[2] or None


def wertungen(antwort: dict[str, Any], nach_tmdb: dict[str, int]) -> dict[int, Any]:
    """`POST /ratings` in Nexviews Form - eine Zeile je TMDB-Nummer.

    ⚠️ Ohne eingerichtete Quelle sind alle Werte `null` und `sources` sagt,
    warum (`{"imdb": "off", "omdb": "no_key"}`). Das ist kein Fehler: Die
    Kachel zeigt dann keine Portal-Wertung, wie im ARR-Betrieb ohne Radarr.

    Seit nexcrate ``39dfc05`` traegt der Stapel auch Rotten Tomatoes und
    Metacritic, aber nur aus nexcrates 30-Tage-Speicher: Er fragt OMDb nie,
    was dort nicht liegt, ist ``null`` (``sources.omdb: not_cached``). Eine
    aeltere nexcrate nennt die Felder gar nicht; dann bleibt es bei IMDb.

    Die Nennung kommt je Quelle in einem Satz fuer alle: ``attribution`` fuer
    IMDb, ``omdb_attribution`` fuer OMDb (``null``, wenn keine Zeile einen
    Wert von dort hat). Jeder Satz haengt nur an Zeilen, die einen Wert
    seiner Quelle zeigen - wie in der Einzelansicht, die OMDb nur nennt, wenn
    von dort etwas kam.
    """
    from ..arr.portal_ratings import Ratings

    def _satz(roh: Any) -> tuple[str, ...]:
        return (roh,) if isinstance(roh, str) and roh else ()

    imdb_satz = _satz(antwort.get("attribution"))
    omdb_satz = _satz(antwort.get("omdb_attribution"))
    gefunden: dict[int, Any] = {}
    for eintrag in antwort.get("items") or []:
        nummer = nach_tmdb.get(str(eintrag.get("ref")))
        if nummer is None:
            continue
        wert, stimmen = _imdb(eintrag)
        tomaten = _prozent(eintrag.get("rotten_tomatoes"))
        metacritic = _prozent(eintrag.get("metacritic"))
        if wert is None and tomaten is None and metacritic is None:
            continue
        gefunden[nummer] = Ratings(
            imdb_id=_imdb_kennung(eintrag),
            imdb=wert,
            imdb_votes=stimmen if wert is not None else None,
            rotten_tomatoes=tomaten,
            metacritic=metacritic,
            attribution=(imdb_satz if wert is not None else ())
            + (omdb_satz if tomaten is not None or metacritic is not None else ()),
        )
    return gefunden


def wertung(antwort: dict[str, Any]) -> Any:
    """`GET /ratings/{kind}/{ref}` in Nexviews Form; ``None``, wenn nichts da ist.

    Nur hier fragt nexcrate OMDb, wenn Rotten Tomatoes und Metacritic noch
    nicht in seinem Speicher liegen; der Stapel liest nur den Speicher. Die
    Nennung kommt als Liste von Saetzen und wird wortwoertlich weitergereicht:
    OMDb verlangt ihren Wortlaut dort, wo die Werte stehen.
    """
    from ..arr.portal_ratings import Ratings

    wert, stimmen = _imdb(antwort)
    tomaten = _prozent(antwort.get("rotten_tomatoes"))
    metacritic = _prozent(antwort.get("metacritic"))
    if wert is None and tomaten is None and metacritic is None:
        return None
    return Ratings(
        imdb_id=_imdb_kennung(antwort),
        imdb=wert,
        imdb_votes=stimmen if wert is not None else None,
        rotten_tomatoes=tomaten,
        metacritic=metacritic,
        attribution=tuple(
            satz for satz in antwort.get("attribution") or [] if isinstance(satz, str) and satz
        ),
    )


# --------------------------------------------------------------------------
# Was der Speicher-Abgleich und die Kacheln aus dem gehaltenen Bestand lesen


def bestand_filme(kennung: str) -> dict[int, Any]:
    """Alle Filme einer Fassung aus dem gehaltenen Bestand, nach TMDB-Nummer."""
    gefunden: dict[int, Any] = {}
    for eintrag in bestand.gehalten().alle("movie").values():
        nummer = mapping.tmdb_aus(eintrag.get("ref"))
        stand = bestand.film_stand(eintrag, kennung) if nummer else None
        if nummer and stand is not None:
            gefunden[nummer] = stand
    return gefunden


def bestand_serien(kennung: str) -> tuple[dict[int, Any], dict[str, Any]]:
    """Alle Serien einer Fassung - nach TVDB-Kennung und nach Titel.

    ⚠️ Die TVDB-Kennung steht nur dabei, wenn nexcrate sie kennt (`refs`). Sie
    ist im NEX-Betrieb **nicht** der Anker; wer einen Titel sucht, nimmt
    `nachschlagen` über TMDB. Diese Form gibt es nur, weil der
    Speicher-Abgleich sie heute so liest.

    Die Staffeln nennt die Liste selbst (seit nexcrate ``39dfc05``); bei einer
    aelteren nexcrate kommen sie aus den Einzelansichten, die
    ``bestand.staffeln_lesen`` vorher geholt hat.
    """
    nach_tvdb: dict[int, Any] = {}
    nach_titel: dict[str, Any] = {}
    for tvdb, stand in alle_serien(kennung):
        if tvdb is not None:
            nach_tvdb[tvdb] = stand
        if stand.title_key:
            nach_titel[stand.title_key] = stand
    return nach_tvdb, nach_titel


def alle_serien(kennung: str) -> list[tuple[int | None, Any]]:
    """Jede Serie einer Fassung genau einmal, samt TVDB-Kennung, soweit bekannt.

    Der Anker ist TMDB (``arr_id`` des Stands). Eine Serie ohne ``tvdb:`` in
    ``refs`` gehoert genauso dazu; der Speicher-Abgleich misst ueber diese
    Form, nicht ueber den TVDB-Index.
    """
    gefunden: list[tuple[int | None, Any]] = []
    gehalten = bestand.gehalten()
    for eintrag in gehalten.alle("series").values():
        titel, gelesen = gehalten.mit_staffeln(eintrag)
        stand = bestand.serien_stand(titel, kennung)
        if stand is None:
            continue
        if not gelesen:
            # Dateien da, Staffeln unbekannt: Wer daraus "keine Staffel"
            # machte, raeumte ihre Posten ab (``storage._schreiben``).
            stand = replace(stand, staffeln_gelesen=False)
        tvdb = mapping.refs_nach_quelle(eintrag.get("refs")).get("tvdb")
        gefunden.append((int(tvdb) if tvdb and tvdb.isdigit() else None, stand))
    return gefunden


def warum(antwort: dict[str, Any]) -> Warum:
    """Eine Antwort von ``/titles/why`` in die Form der Grenze.

    ⚠️ **Der Grund steht je Fassung in ``because``** (nexbeat-Befund 7), nicht
    im ``next_search_reason`` des Titels - der stand im Prüfstand auf
    „nichts gewollt", während eine Fassung sehr wohl gesucht wurde.

    ⚠️ **Der Stapelweg verpackt anders als die Einzelansicht.** Er antwortet je
    Eintrag ``{kind, ref, known, why, error}``; die Einzelansicht liefert den
    Inhalt unmittelbar. Beides kommt hier an.
    """
    if not antwort.get("known", True):
        return Warum(bekannt=False)
    inhalt = antwort["why"] if isinstance(antwort.get("why"), dict) else antwort
    gruende: list[Grund] = []
    for fassung in inhalt.get("versions") or []:
        weil = fassung.get("because")
        if not isinstance(weil, dict) or not weil.get("code"):
            continue
        werte = weil.get("params") if isinstance(weil.get("params"), dict) else {}
        # ``version_not_ready`` traegt die Gruende der Fassung noch einmal
        # darunter (kein Indexer, kein Profil, kein Download-Programm).
        darunter = [
            str(eintrag.get("code"))
            for eintrag in (werte.get("reasons") or [])
            if isinstance(eintrag, dict) and eintrag.get("code")
        ]
        gruende.append(
            Grund(
                fassung=str(fassung.get("version_id") or ""),
                code=str(weil["code"]),
                werte={k: v for k, v in werte.items() if k != "reasons"},
                darunter=tuple(darunter),
            )
        )
    return Warum(
        bekannt=True,
        automatisch=bool(inhalt.get("automatic")),
        suchwunsch=bool(inhalt.get("search_wish")),
        zuletzt_gesucht=inhalt.get("last_search_at") or None,
        naechste_suche=inhalt.get("next_search_at") or None,
        gruende=tuple(gruende),
    )
