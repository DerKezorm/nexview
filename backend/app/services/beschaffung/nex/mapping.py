"""Nexcrates Vertrag in Nexviews Worte.

Eine Stelle, an der uebersetzt wird - Adressen und Felder stehen in
``client.py``, die Bedeutung hier.

Gemessen am 22.09.2026 gegen eine Wegwerf-nexcrate 0.1.0 (Vertrag ``major 1``,
``stage V5``); die Messwerte stehen in
``homelab/nexcrate-pruefstand/nexview-anbindung/messung-nexview.md``.
"""

from __future__ import annotations

from typing import Any

from ..base import ARR, KLASSE_HD, KLASSE_UHD, NEX, FassungInfo

#: Nexview nennt Serien ``tv``, nexcrate ``series``. Filme heissen bei beiden gleich.
ART_NACH_KIND: dict[str, str] = {"movie": "movie", "series": "tv"}
KIND_NACH_ART: dict[str, str] = {"movie": "movie", "tv": "series"}

#: ⚠️ **Was Nexview anfragen kann - mehr nicht.** nexcrate fuehrt auch Musik
#: (``kind: "album"``), und ``art()`` reicht jedes unbekannte ``kind``
#: unveraendert durch. Ohne diese Schranke landete nexcrates Musikfassung in
#: Nexviews Fassungstabelle und stand danach in jeder Liste - in der
#: Benutzerverwaltung, im Abgleich des Umsteigers, auf der nexcrate-Seite.
#: Beim ersten Umstieg an einer echten Anlage ist genau das passiert
#: (23.09.2026). Musik gehoert nicht zu Nexview; das entscheidet nicht die
#: Oberflaeche, sondern diese Zeile.
EIGENE_ARTEN: frozenset[str] = frozenset(ART_NACH_KIND.values())

#: Die groben Klassen, die nexcrate je Fassung liefert (``tier``). ``null``
#: heisst "kein Profil" - dann hat die Fassung keine Klasse, und Nexview
#: zeigt kein Abzeichen.
KLASSEN: frozenset[str] = frozenset({"sd", KLASSE_HD, KLASSE_UHD})

#: nexcrates Zustaende (``GET /states``) in Nexviews Liste (``base.ZUSTAENDE``).
#:
#: ⚠️ ``available`` und ``upgrade`` sagen nichts ueber die Ueberwachung; ob
#: ein Titel "vorerst" liegt (Datei da, nexcrate laesst ihn in Ruhe), steht in
#: ``monitored``. Deshalb wird hier beides gelesen, nicht nur der Zustand.
#: ``incomplete`` gibt es nur bei Alben; ein unbekannter Zustand ist
#: ``unbekannt`` und kein Fehler (N14).
_ZUSTAENDE: dict[str, str] = {
    "problem": "problem",
    "downloading": "laedt",
    "available": "da",
    "upgrade": "verbesserbar",
    "wanted": "gesucht",
    "unmonitored": "fehlt",
}


def kind(media_type: Any) -> str:
    """Nexviews Medienart als nexcrates ``kind``."""
    art = getattr(media_type, "value", media_type)
    return KIND_NACH_ART.get(str(art), str(art))


def art(kind_wert: str) -> str:
    """Nexcrates ``kind`` als Nexviews Medienart."""
    return ART_NACH_KIND.get(kind_wert, kind_wert)


def fuehrt_nexview(eintrag: dict[str, Any]) -> bool:
    """Ist diese Zeile aus ``GET /versions`` eine, die Nexview anbieten darf?"""
    return art(str(eintrag.get("kind") or "")) in EIGENE_ARTEN


def ref(tmdb_id: int) -> str:
    """Die Kennung eines Titels, wie nexcrate sie erwartet.

    ⚠️ Nur klein: ``TMDB:603`` ist ``ref_source_unknown`` (nexbeat-Befund 8,
    an dieser nexcrate nachgemessen).
    """
    return f"tmdb:{int(tmdb_id)}"


def tmdb_aus(wert: str | None) -> int | None:
    """Die TMDB-Nummer aus ``tmdb:603``; ``None`` bei jeder anderen Quelle."""
    text = wert or ""
    quelle, _, nummer = text.partition(":")
    return int(nummer) if quelle == "tmdb" and nummer.isdigit() else None


def refs_nach_quelle(refs: list[str] | None) -> dict[str, str]:
    """``["tmdb:603", "imdb:tt0000603"]`` als ``{"tmdb": "603", ...}``."""
    gefunden: dict[str, str] = {}
    for eintrag in refs or []:
        quelle, _, nummer = str(eintrag).partition(":")
        if quelle and nummer:
            gefunden.setdefault(quelle, nummer)
    return gefunden


def zustand(state: str | None, monitored: bool | None = True) -> str:
    """Nexcrates Zustand einer Fassung als Nexviews Zustand.

    Eine Datei ohne Ueberwachung ist ``vorerst``: Sie liegt, aber nexcrate
    holt nichts Besseres mehr. Das ist Nexviews alter Zustand "unmonitored
    mit Datei" und der Grund, warum die Ueberwachung hier mitgelesen wird.
    """
    treffer = _ZUSTAENDE.get(str(state or ""))
    if treffer is None:
        return "unbekannt"
    if treffer in ("da", "verbesserbar") and monitored is False:
        return "vorerst"
    return treffer


#: Zustaende eines Downloads, in denen er nicht mehr laeuft (nexcrates
#: ``FINISHED_STATES``). ``GET /queue`` liefert gescheiterte mit, und zwar
#: gemessen auch solche ohne ``problem``: 42 an der Live-Instanz am 24.09.2026.
DOWNLOAD_BEENDET = ("imported", "failed", "removed")


def download_laeuft(eintrag: dict[str, Any]) -> bool:
    """Laeuft dieser Eintrag aus ``GET /queue`` noch?

    Ein unbekannter oder fehlender Zustand gilt als laufend, so wie vorher.
    """
    return str(eintrag.get("state") or "") not in DOWNLOAD_BEENDET


def hat_datei(state: str | None) -> bool:
    """Liegt fuer diese Fassung eine Datei? (``available`` und ``upgrade``.)"""
    return str(state or "") in ("available", "upgrade")


def klasse(tier: str | None) -> str | None:
    """Nexcrates grobe Klasse, soweit Nexview sie kennt; sonst nichts."""
    wert = str(tier or "")
    return wert if wert in KLASSEN else None


def fassung_info(eintrag: dict[str, Any], reihenfolge: int) -> FassungInfo:
    """Eine Zeile aus ``GET /versions`` als Fassung der Grenze."""
    return FassungInfo(
        kennung=str(eintrag.get("version_id") or ""),
        media_type=art(str(eintrag.get("kind") or "")),
        name=str(eintrag.get("name") or eintrag.get("version_id") or ""),
        klasse=klasse(eintrag.get("tier")),
        reihenfolge=int(eintrag.get("order") or 0) + reihenfolge,
        quelle=NEX,
    )


def gruende(eintrag: dict[str, Any]) -> list[str]:
    """Warum eine Fassung nicht bereit ist, als Kennungen.

    ⚠️ ``automatic_off`` steht unter den Gruenden, haelt mit
    ``wishes_search_at_once`` aber nichts auf (nexbeat-Befund 13): Ein
    Suchwunsch wird auch bei ausgeschalteter Automatik abgearbeitet. Es bleibt
    deshalb in der Liste, macht eine Fassung aber nicht unbrauchbar - was
    ``bereit`` heisst, entscheidet nexcrate mit ``ready``.
    """
    return [
        str(grund.get("code"))
        for grund in eintrag.get("reasons") or []
        if isinstance(grund, dict) and grund.get("code")
    ]


def herkunft(anfrage_id: int) -> str:
    """Nexviews Herkunftsmarke an einer Anfrage (N19).

    ⚠️ Sie bleibt beim **ersten**, der einen Titel angefragt hat
    (nexbeat-Befund 4). Wer wissen will, ob seine Anfrage angekommen ist,
    liest den Zustand, nie die Marke.
    """
    return f"nexview:request:{int(anfrage_id)}"


def ist_nexview(origin: str | None) -> bool:
    """Hat Nexview diese Fassung angelegt - oder der Betreiber selbst?

    Was der Betreiber selbst ueberwacht, fasst Nexview nicht an (Bauplan 6.2).
    """
    return str(origin or "").startswith("nexview:")


def quelle_der_fassung(kennung: str) -> str:
    """Aus welchem Betrieb eine Fassungskennung stammt.

    Die vier ARR-Kennungen sind Woerter mit Bindestrich, nexcrates sind
    ``v_<acht Zeichen>``. Gebraucht fuer Anfragen und Posten, die aus dem
    ARR-Betrieb stehen geblieben sind.
    """
    return NEX if str(kennung).startswith("v_") else ARR
