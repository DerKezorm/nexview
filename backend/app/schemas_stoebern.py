"""Antwortformen der Stoeber-Seite."""

from __future__ import annotations

from pydantic import BaseModel

from .schemas_media import MediaItem


class RegalInfo(BaseModel):
    """Ein Regal, wie es die Uebersicht braucht - ohne Titel darin.

    Der **Name** kommt bewusst nicht mit: Das Frontend uebersetzt
    ``stoebern.regal.<kennung>`` selbst. Ein fertiger Text hier waere
    einsprachig, und die App ist zweisprachig.
    """

    kennung: str
    # "reihe" = auf der Uebersicht geladen anzeigen, "kachel" = nur verweisen.
    gruppe: str
    # "jahrzehnt" | "genre" | "persoenlich" | "" - womit gruppiert wird.
    kategorie: str
    persoenlich: bool
    # Fertiger Name statt i18n-Schluessel.
    #
    # Die Ausnahme von der Regel, und zwar eine noetige: "Weil dir *Der Pate*
    # gefaellt" enthaelt einen Filmtitel, und der laesst sich nicht
    # uebersetzen. Bei allen uebrigen Regalen bleibt das Feld leer und das
    # Frontend nimmt ``stoebern.regal.<kennung>.titel``.
    titel: str | None = None


class RegalSeite(BaseModel):
    """Der Inhalt eines Regals."""

    kennung: str
    items: list[MediaItem]
    page: int
    total_pages: int
    # Wie viele TMDB-Seiten dafuer durchgesehen wurden. Bei gesetztem
    # Bestandsfilter koennen das mehrere sein.
    seiten_durchsucht: int
    # True, wenn es nichts mehr nachzuladen gibt.
    #
    # Braucht die Oberflaeche, um ehrlich zu sein: Liefert "nur was schon da
    # ist" bei einer duennen Bibliothek drei Titel, ist das eine endgueltige
    # Auskunft und kein Grund, einen Knopf "mehr laden" anzubieten, hinter dem
    # nichts mehr kommt.
    erschoepft: bool
    demo: bool = False
    arr_warning: str | None = None


class FilterSeite(BaseModel):
    """Ergebnis der freien Auswahl."""

    items: list[MediaItem]
    page: int
    total_pages: int
    seiten_durchsucht: int
    erschoepft: bool
    # Welche Jahrzehnte die Leiste anbieten darf. Kommt vom Server, damit
    # Auswahl und Pruefung nicht auseinanderlaufen.
    jahrzehnte: list[int] = []
    demo: bool = False
    arr_warning: str | None = None


# --- Der gefuehrte Filmabend ----------------------------------------------


class FilmabendFrage(BaseModel):
    """Eine Frage des Assistenten.

    Texte fehlen absichtlich: Das Frontend uebersetzt
    ``stoebern.filmabend.<kennung>.frage`` und
    ``stoebern.filmabend.<kennung>.<antwort>``.
    """

    kennung: str
    antworten: list[str]
    # {frueherer Frage-Schluessel: Antworten, bei denen diese Frage entfaellt}
    #
    # Damit laeuft das Frontend den Baum selbst ab - ohne eine Runde zum Server
    # je Frage. Die Uebersetzung in Filter bleibt trotzdem hier.
    entfaellt_wenn: dict[str, list[str]]
    # Einzelne **Antwortmoeglichkeiten**, die verschwinden:
    # {Antwort: {fruehere Frage: Antworten, bei denen sie wegfaellt}}
    #
    # ⚠️ Ohne dieses Feld bietet die Oberflaeche bei "mit Kindern" weiter
    # "zum Gruseln" an - und der Server weist die Antwort dann mit 422 ab.
    # Genau so ist es passiert: Das Feld gab es im Fragebaum, aber nicht im
    # Antwortschema, und Pydantic verschweigt Unbekanntes stillschweigend.
    antworten_entfallen_wenn: dict[str, dict[str, list[str]]] = {}


class FilmabendStapel(BaseModel):
    """Das Ergebnis: ein kleiner Stapel statt einer Ergebnisliste."""

    items: list[MediaItem]
    runde: int
    # Welche Antworten tatsaechlich gezaehlt haben. Uebersprungene Fragen
    # stehen hier nicht - so sieht man, dass sie nichts bewirkt haben.
    antworten: dict[str, str]
    # Konnte ueberhaupt aus etwas ausgewaehlt werden? Bei "lange nicht gesehen"
    # ohne verknuepften Media-Server ist die Antwort ehrlicherweise nein, und
    # die Oberflaeche muss das sagen statt eine leere Flaeche zu zeigen.
    quelle_leer: bool = False
    erschoepft: bool = False
    demo: bool = False
