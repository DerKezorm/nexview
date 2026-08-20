"""Was liegt im Media-Server, das Radarr/Sonarr nicht kennt?

Der Zweck ist eng: Doppel-Anfragen verhindern. Wer eine Datei von Hand
hineinkopiert hat oder seine Sammlung vor dem *arr-Aufbau angelegt hat, findet
sie in Radarr/Sonarr nicht wieder - Nexview zeigte solche Titel deshalb als
anfragbar an, und jemand laedt sie ein zweites Mal herunter.

Der Abgleich laeuft im Hintergrund mit (siehe ``status_poller``); die Anzeige
liest nur die fertige Tabelle. Alles andere waere spuerbar: Eine Bibliothek
mit ein paar tausend Titeln zu lesen dauert Sekunden, und die will niemand
beim Oeffnen der Entdecken-Seite abwarten.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..models import MediaServerLibraryItem, MediaType
from .mediaserver import MediaServerError, get_media_server
from .mediaserver.base import LibraryItem
from .settings_service import AppSettings
from .sonarr import normalize_title

logger = logging.getLogger("nexview.mediaserver")


def _zusammengefasst(werke: list[LibraryItem]) -> list[LibraryItem]:
    """Denselben Titel aus mehreren Bibliotheken zu einer Zeile machen.

    Wer 1080p und 4K in getrennte Plex-Bibliotheken legt - eine verbreitete
    Einrichtung - hat denselben Film zweimal. Plex vergibt dafuer **dieselbe**
    GUID, und die Tabelle laesst sie nur einmal zu. Ohne diese Zusammenfassung
    bricht der Abgleich mit einem UNIQUE-Fehler ab, und zwar vollstaendig:
    Danach kennt Nexview *keinen einzigen* Titel des Media-Servers mehr und
    zeigt alles als "nicht angefragt" an. Nachgemessen an einer echten
    Bibliothek mit den Abschnitten "Filme" und "Filme4K".

    Fachlich ist das Zusammenfassen ohnehin das Richtige: Es ist ein Film, der
    in zwei Fassungen vorliegt. Die Merkmale werden deshalb verodert - was in
    *einer* der Bibliotheken zutrifft, gilt fuer den Titel.
    """
    nach_guid: dict[str, LibraryItem] = {}
    for werk in werke:
        vorhanden = nach_guid.get(werk.guid)
        if vorhanden is None:
            nach_guid[werk.guid] = werk
            continue
        nach_guid[werk.guid] = replace(
            vorhanden,
            has_standard=vorhanden.has_standard or werk.has_standard,
            has_uhd=vorhanden.has_uhd or werk.has_uhd,
            owner_watched=vorhanden.owner_watched or werk.owner_watched,
            # Fehlende Kennungen aus dem zweiten Eintrag uebernehmen: Welche
            # Plex liefert, haengt am Agenten der jeweiligen Bibliothek.
            tmdb_id=vorhanden.tmdb_id or werk.tmdb_id,
            tvdb_id=vorhanden.tvdb_id or werk.tvdb_id,
            imdb_id=vorhanden.imdb_id or werk.imdb_id,
            year=vorhanden.year or werk.year,
        )
    return list(nach_guid.values())


async def refresh(db: Session, settings: AppSettings, streng: bool = False) -> int:
    """Die Bibliothek neu einlesen. Gibt die Anzahl der Titel zurueck.

    Ersetzt den bisherigen Bestand vollstaendig - und zwar nur dann, wenn das
    Lesen geklappt hat. Ein halb gefuellter Abgleich waere schlimmer als ein
    veralteter: Titel wuerden faelschlich wieder als anfragbar erscheinen.

    ``streng`` unterscheidet die beiden Aufrufer. Der Hintergrund-Abgleich
    schluckt Fehler (ein Aussetzer des Servers darf den Durchgang nicht
    beenden). Der **Handknopf** des Administrators dagegen muss sie zeigen:
    Vorher meldete er bei einem unerreichbaren Server kommentarlos den alten
    Zaehler samt Zeitstempel - scheinbarer Erfolg, und niemand konnte je
    herausfinden, warum kein einziger Plex-Titel ein Abzeichen bekam. Genau
    so gemeldet (Issue #2).
    """
    server = get_media_server(settings)
    if server is None:
        return 0

    try:
        werke = await server.library_index()
    except NotImplementedError:
        # Ein Anbieter, der das noch nicht kann - kein Grund fuer einen Fehler.
        return 0
    except MediaServerError as fehler:
        logger.warning("Bibliothek des Media-Servers nicht lesbar: %s", fehler.message)
        if streng:
            raise
        return 0

    db.execute(
        delete(MediaServerLibraryItem).where(
            MediaServerLibraryItem.provider == server.provider
        )
    )
    eindeutig = _zusammengefasst(werke)
    for werk in eindeutig:
        db.add(
            MediaServerLibraryItem(
                provider=server.provider,
                media_type=MediaType(werk.media_type),
                guid=werk.guid[:255],
                rating_key=werk.rating_key,
                owner_watched=werk.owner_watched,
                has_standard=werk.has_standard,
                has_uhd=werk.has_uhd,
                tmdb_id=werk.tmdb_id,
                tvdb_id=werk.tvdb_id,
                imdb_id=werk.imdb_id,
                title=werk.title[:500],
                title_key=normalize_title(werk.title)[:500],
                year=werk.year,
            )
        )
    db.commit()
    # Beide Zahlen nennen: Plex zaehlt Bibliothekseintraege, Nexview Titel.
    # Ein Film, der in zwei Bibliotheken liegt (1080p und 4K), ist bei Plex
    # zwei Eintraege und hier bewusst einer - ohne diese Zeile im Protokoll
    # sieht die Differenz wie ein Verlust aus und wird als Fehler gemeldet.
    zusammengefasst = len(werke) - len(eindeutig)
    if zusammengefasst:
        logger.info(
            "Media-Server-Bibliothek gelesen: %d Titel (%d Eintraege, %d in "
            "mehreren Bibliotheken zusammengefasst)",
            len(eindeutig),
            len(werke),
            zusammengefasst,
        )
    else:
        logger.info("Media-Server-Bibliothek gelesen: %d Titel", len(eindeutig))
    return len(eindeutig)


def stand(db: Session) -> dict[str, object]:
    """Wie viele Titel sind erfasst, und wann zuletzt?

    Ohne diese Auskunft bliebe der Abgleich unsichtbar: Wer alles ueber
    Radarr/Sonarr laufen laesst, sieht bei sich nie ein Abzeichen - und koennte
    nicht unterscheiden, ob nichts zu finden war oder nichts gelesen wurde.
    """
    anzahl = db.scalar(select(func.count()).select_from(MediaServerLibraryItem)) or 0
    zuletzt = db.scalar(select(func.max(MediaServerLibraryItem.updated_at)))
    return {"count": int(anzahl), "updated_at": zuletzt}


def _jahr(item: object) -> int | None:
    """Erscheinungsjahr aus dem Datum, das TMDB liefert ("2019-07-12")."""
    datum = (getattr(item, "release_date", None) or "")[:4]
    return int(datum) if datum.isdigit() else None


def _jahre_passen(gesucht: int | None, gefunden: int | None) -> bool:
    """Gehoeren die beiden Jahresangaben plausibel zusammen?

    Der Grund ist eine echte Beobachtung, keine Vorsichtsmassnahme ins Blaue:
    In einer Bibliothek mit 3509 Filmen trug genau einer eine **falsche**
    TMDB-Nummer - Plex fuehrte "Irenas Geheimnis" (2023) unter der Nummer eines
    chinesischen Films ohne Erscheinungsdatum. Ohne diese Pruefung haette
    Nexview jedem, der jenen Film sucht, gesagt, er habe ihn schon.

    Ein Jahr Abweichung ist erlaubt: Festivalstart und Kinostart fallen oft in
    verschiedene Jahre. Fehlt eine der beiden Angaben, wird der Treffer
    verworfen - lieber einen vorhandenen Titel uebersehen als einen falschen
    behaupten. Ein uebersehener kostet einen doppelten Download, ein falscher
    nimmt einen Titel dauerhaft aus dem Angebot, ohne dass jemand den Grund
    sieht.
    """
    if gesucht is None or gefunden is None:
        return False
    return abs(gesucht - gefunden) <= 1


def vorhandene_kennungen(
    db: Session,
    media_type: MediaType,
    items: list,
    tier: str | None = None,
) -> set[int]:
    """Welche dieser Titel liegen im Media-Server?

    ``tier`` schraenkt auf eine Stufe ein: ``"uhd"`` zaehlt nur Titel, bei
    denen der Media-Server eine 4K-Datei meldet, ``"standard"`` nur die
    uebrigen. ``None`` (die Vorgabe) fragt "ueberhaupt vorhanden" und
    entspricht dem Verhalten von vorher - richtig fuer alle, die gar keine
    zweite Instanz betreiben.

    Geprueft wird in derselben Reihenfolge, in der die Kennungen verlaesslich
    sind: TMDB, dann TVDB, dann Titel **und Jahr**.

    Das Jahr ist beim Titel-Abgleich keine Feinheit, sondern der ganze Punkt.
    Ohne es waeren "The Lion King" von 1994 und das Remake von 2019 derselbe
    Eintrag - und wer das Remake anfragen will, bekaeme zu hoeren, er habe es
    schon. Ein Fehltreffer ist hier die einzige Art, wie diese Funktion
    ueberhaupt schaden kann: Sie nimmt einen Titel aus dem Angebot, den es in
    Wahrheit gar nicht gibt. Deshalb lieber einen alten Eintrag ohne Kennung
    uebersehen als einen falschen behaupten.

    Zurueck kommen die TMDB-Kennungen der Treffer, weil die Anzeige damit
    arbeitet.
    """
    if not items:
        return set()

    tmdb_ids = {item.tmdb_id for item in items if item.tmdb_id}
    tvdb_ids = {getattr(item, "tvdb_id", None) for item in items}
    tvdb_ids.discard(None)
    titel = {normalize_title(item.title) for item in items if item.title}

    bedingungen = [MediaServerLibraryItem.tmdb_id.in_(tmdb_ids)] if tmdb_ids else []
    if tvdb_ids:
        bedingungen.append(MediaServerLibraryItem.tvdb_id.in_(tvdb_ids))
    if titel:
        bedingungen.append(MediaServerLibraryItem.title_key.in_(titel))
    if not bedingungen:
        return set()

    stufe = []
    if tier == "uhd":
        stufe.append(MediaServerLibraryItem.has_uhd.is_(True))
    elif tier == "standard":
        stufe.append(MediaServerLibraryItem.has_standard.is_(True))

    zeilen = db.scalars(
        select(MediaServerLibraryItem).where(
            MediaServerLibraryItem.media_type == media_type,
            or_(*bedingungen),
            *stufe,
        )
    ).all()

    nach_tmdb = {z.tmdb_id: z.year for z in zeilen if z.tmdb_id}
    nach_tvdb = {z.tvdb_id: z.year for z in zeilen if z.tvdb_id}
    # Der Titel-Rueckfall gilt **nur** fuer Eintraege, deren Identitaet
    # unbekannt ist - also ohne jede fremde Kennung (alte Agenten). Traegt die
    # Plex-Zeile eine TMDB- oder TVDB-Nummer, ist geklaert, *welcher* Film das
    # ist; ein Namens-Treffer auf eine andere Nummer waere eine Falschaussage.
    # Der gemeldete Fall: "Backrooms" (2026, Spielfilm, Kennung bekannt) liess
    # ueber den Titel auch "Backrooms" (2026, 4-Minuten-Kurzfilm, andere
    # Kennung) als "In der Bibliothek" erscheinen - gleicher Name, gleiches
    # Jahr, die Jahres-Pruefung kann solche Doppelgaenger nicht trennen.
    nach_titel = {
        (z.title_key, z.year)
        for z in zeilen
        if z.title_key and z.year and not z.tmdb_id and not z.tvdb_id
    }

    treffer: set[int] = set()
    for item in items:
        if not item.tmdb_id:
            continue
        jahr = _jahr(item)
        if item.tmdb_id in nach_tmdb:
            if _jahre_passen(jahr, nach_tmdb[item.tmdb_id]):
                treffer.add(item.tmdb_id)
        elif getattr(item, "tvdb_id", None) and item.tvdb_id in nach_tvdb:
            if _jahre_passen(jahr, nach_tvdb[item.tvdb_id]):
                treffer.add(item.tmdb_id)
        elif jahr and item.title and (normalize_title(item.title), jahr) in nach_titel:
            treffer.add(item.tmdb_id)
    return treffer
