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

from sqlalchemy import String, delete, func, or_, select
from sqlalchemy.orm import Session

from ..models import MediaServerLibraryItem, MediaType
from .mediaserver import (
    MediaServer,
    MediaServerError,
    media_server_for_setup,
    verbundene_anbieter,
)
from .mediaserver.base import LibraryItem
from .settings_service import AppSettings, load_settings
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
            # Die Groessen ebenso: Der Eintrag aus der 1080p-Bibliothek traegt
            # ``size_standard``, der aus der 4K-Bibliothek ``size_uhd`` - beim
            # Zusammenfassen kommen beide an einer Zeile zusammen.
            size_standard=max(vorhanden.size_standard, werk.size_standard),
            size_uhd=max(vorhanden.size_uhd, werk.size_uhd),
            owner_watched=vorhanden.owner_watched or werk.owner_watched,
            # Fehlende Kennungen aus dem zweiten Eintrag uebernehmen: Welche
            # Plex liefert, haengt am Agenten der jeweiligen Bibliothek.
            tmdb_id=vorhanden.tmdb_id or werk.tmdb_id,
            tvdb_id=vorhanden.tvdb_id or werk.tvdb_id,
            imdb_id=vorhanden.imdb_id or werk.imdb_id,
            year=vorhanden.year or werk.year,
        )
    return list(nach_guid.values())


async def refresh(
    db: Session, settings: AppSettings, streng: bool = False, provider: str | None = None
) -> int:
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
    gesamt = 0
    gelesen = False
    # ``provider`` grenzt auf einen Server ein - das ist der Handknopf auf
    # dessen Seite. Ohne Angabe laufen alle, das ist der Hintergrunddurchlauf.
    anbieter_liste = [
        a
        for a in verbundene_anbieter(settings)
        if provider is None or a == provider
    ]
    for anbieter in anbieter_liste:
        server = media_server_for_setup(settings, anbieter)
        anzahl = await _einen_server_lesen(db, server, streng)
        if anzahl is not None:
            gelesen = True
            gesamt += anzahl
    if not gelesen:
        return 0
    # Bewusst **nicht** die Summe der einzelnen Server: Die zaehlte jeden Film
    # so oft, wie ihn Server melden. Was hier zurueckkommt, landet in der
    # Meldung "N Titel erfasst" - und die soll die Sammlung beschreiben, nicht
    # die Zahl der Datenbankzeilen.
    return titel_anzahl(db, provider)


async def _einen_server_lesen(
    db: Session, server: MediaServer, streng: bool
) -> int | None:
    """Die Bibliothek **eines** Servers einlesen.

    Gibt die Anzahl zurueck - oder ``None``, wenn nichts geschrieben wurde.
    Der Unterschied zaehlt: Bei zwei verbundenen Servern darf ein Aussetzer des
    einen nicht den Bestand des anderen als "0 Titel" erscheinen lassen.
    """
    try:
        werke = await server.library_index()
    except NotImplementedError:
        # Ein Anbieter, der das noch nicht kann - kein Grund fuer einen Fehler.
        return None
    except MediaServerError as fehler:
        logger.warning(
            "Media server %r library not readable: %s", server.provider, fehler.message
        )
        if streng:
            raise
        return None

    # ⚠️ **Steht der Server ueberhaupt noch?**
    #
    # Zwischen dem Lesen oben und dem Schreiben hier liegt eine vollstaendige
    # HTTP-Abfrage - bei einer grossen Bibliothek Sekunden. In dieser Zeit kann
    # der Administrator die Verbindung trennen; sein Endpunkt laeuft im
    # Threadpool und damit echt nebenlaeufig. Ohne diese Pruefung schriebe der
    # Abgleich danach munter weiter: frische Zeilen mit frischem Zeitstempel,
    # Minuten nachdem die Verbindung weg ist - und jede Aufraeumarbeit des
    # Trennens waere wieder zunichte.
    #
    # Bewusst eine **frische** Abfrage und nicht das ``settings`` von oben:
    # Genau dessen Alter ist ja das Problem.
    if server.provider not in verbundene_anbieter(load_settings(db)):
        logger.info(
            "Media server %r was disconnected while its library was being read - "
            "nothing written",
            server.provider,
        )
        return None

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
                # ⚠️ Ohne diese beiden Zeilen bleibt jede Groesse auf null -
                # der Media-Server liefert sie, ``_dateigroessen`` rechnet sie
                # aus, und hier gingen sie verloren. Fuer die Speicher-Belegung
                # ist das der Unterschied zwischen "gemessen" und "unbekannt":
                # Ein Titel, den jemand nach dem Laden aus Radarr entfernt hat,
                # ist danach **nur** hier noch mit einer Groesse zu finden.
                size_standard=werk.size_standard,
                size_uhd=werk.size_uhd,
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
            "Media server %r library read: %d titles (%d entries, %d merged "
            "across libraries)",
            server.provider,
            len(eindeutig),
            len(werke),
            zusammengefasst,
        )
    else:
        logger.info(
            "Media server %r library read: %d titles", server.provider, len(eindeutig)
        )
    return len(eindeutig)


def _titel_schluessel():
    """Woran sich zwei Zeilen als *derselbe Titel* erkennen.

    Die TMDB-Nummer, wo es eine gibt - sonst der normalisierte Titel. Der
    zweite Weg ist die schlechtere Auskunft, aber die einzige: Ohne Nummer
    laesst sich ein Film nur ueber seinen Namen wiedererkennen.
    """
    return (
        MediaServerLibraryItem.media_type,
        func.coalesce(
            func.cast(MediaServerLibraryItem.tmdb_id, String),
            "k:" + func.coalesce(MediaServerLibraryItem.title_key, ""),
        ),
    )


def titel_anzahl(db: Session, provider: str | None = None) -> int:
    """Wie viele **verschiedene** Titel liegen in den Bibliotheken?

    ⚠️ Nicht die Zahl der Zeilen. Im Parallelbetrieb steht derselbe Film
    zweimal da - einmal aus Plex, einmal aus Jellyfin -, weil beide Server
    dieselben Ordner lesen. Gezaehlt wurden vorher die Zeilen, und die
    Oberflaeche meldete "7409 Titel erfasst" fuer eine Sammlung von gut 3700.
    Genau so gemeldet.

    Doppelt zu *speichern* ist dabei richtig: Jede Zeile gehoert einem Server
    und traegt dessen Gesehen-Stand und Dateigroessen. Nur gezaehlt werden
    darf sie nicht zweimal.
    """
    innen = select(*_titel_schluessel())
    if provider is not None:
        innen = innen.where(MediaServerLibraryItem.provider == provider)
    return int(
        db.scalar(select(func.count()).select_from(innen.distinct().subquery())) or 0
    )


def stand(db: Session, provider: str | None = None) -> dict[str, object]:
    """Wann wurde zuletzt abgeglichen - und wie viele Titel liegen vor?

    Ohne diese Auskunft bliebe der Abgleich unsichtbar: Wer alles ueber
    Radarr/Sonarr laufen laesst, sieht bei sich nie ein Abzeichen - und koennte
    nicht unterscheiden, ob nichts zu finden war oder nichts gelesen wurde.

    ⚠️ **Der Zeitstempel traegt diese Auskunft, nicht die Zahl.** Er stammt aus
    den Titelzeilen selbst: Wurde nichts gelesen, gibt es keine Zeile und damit
    auch kein Datum. Ein Datum heisst also bereits "es wurde etwas gelesen".
    Die Oberflaeche zeigt deshalb nur ihn - die Zahl stand dort auf *beiden*
    Server-Seiten mit demselben Wert (der Gesamtzahl ueber alle Server) und
    behauptete damit auf jeder Seite etwas Falsches ueber diesen einen Server.

    ``provider`` grenzt auf einen Anbieter ein; ohne Angabe gilt es fuer alle.
    """
    bedingung = (
        [MediaServerLibraryItem.provider == provider] if provider is not None else []
    )
    zuletzt = db.scalar(
        select(func.max(MediaServerLibraryItem.updated_at)).where(*bedingung)
    )
    return {"count": titel_anzahl(db, provider), "updated_at": zuletzt}


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


def echte_uhd_kennungen(
    db: Session,
    media_type: MediaType,
    items: list,
    *,
    in_standard_instanz: set[int],
) -> set[int]:
    """4K-Meldungen des Media-Servers, die wirklich eine **eigene** Fassung sind.

    **Ein 4K-Film in der Standard-Instanz ist keine zweite Fassung.** Der
    Media-Server misst die Aufloesung der *Datei*, nicht die Instanz, die sie
    verwaltet. Wer ein 2160p-Remux in das normale Radarr laedt - ein voellig
    gewoehnlicher Fall, sobald ein Qualitaetsprofil 4K zulaesst - hat damit
    **eine** Datei, und Plex meldet sie als 4K.

    Ohne diese Unterscheidung stand ein solcher Titel als "in 4K vorhanden" da,
    obwohl die 4K-Instanz leer war; er liess sich dort nicht mehr anfragen, und
    die Anfrage wurde zusaetzlich serverseitig abgewiesen. Nachgemessen an einer
    echten Bibliothek betraf das **33 von 33** Filmen, bei denen der Rueckfall
    ansprang - kein einziger war der Fall, fuer den er gebaut wurde.

    Als eigene Fassung zaehlt eine 4K-Datei deshalb nur, wenn

    * der Media-Server **daneben** noch eine Standard-Datei fuehrt - dann sind
      es wirklich zwei -, oder
    * die Standard-Instanz den Titel gar nicht hat. Dann ist der Media-Server
      der einzige Zeuge, und genau dafuer ist der Rueckfall da.

    ``in_standard_instanz`` sind die TMDB-Kennungen, die Radarr/Sonarr in der
    **Standard**-Instanz mit Datei fuehren. Bewusst ein Parameter und keine
    eigene Abfrage: Dieses Modul kennt den Media-Server, nicht die *arr-Dienste,
    und die Aufrufer haben die Antwort ohnehin schon vorliegen.

    Ist die Standard-Instanz nicht eingetragen oder nicht erreichbar, ist die
    Menge leer und das Ergebnis damit **grosszuegig**: Ohne Antwort laesst sich
    nicht belegen, dass die Datei dort verwaltet wird, und ein Ausfall soll
    keinen Titel aus dem Bestand nehmen.

    ⚠️ Diese Regel gehoert an **jede** Stelle, die den 4K-Rueckfall benutzt -
    Abzeichen und Sperre duerfen nicht auseinanderlaufen. Genau daran ist es
    aufgefallen: Das Abzeichen sagte "noch nicht angefragt", die Anfrage wurde
    trotzdem mit "liegt bereits auf dem Media-Server" abgewiesen.
    """
    treffer = vorhandene_kennungen(db, media_type, items, tier="uhd")
    if not treffer:
        return treffer

    betroffen = [eintrag for eintrag in items if eintrag.tmdb_id in treffer]
    auch_standard = vorhandene_kennungen(db, media_type, betroffen, tier="standard")

    return {
        tmdb_id
        for tmdb_id in treffer
        if tmdb_id in auch_standard or tmdb_id not in in_standard_instanz
    }
