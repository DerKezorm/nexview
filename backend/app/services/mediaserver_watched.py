"""Wer hat was schon gesehen?

Der Media-Server weiss es, Nexview zeigt es als kleines Abzeichen an der
Kachel. Mehr nicht - kein Filter, keine "Weiterschauen"-Reihe; das waere ein
eigenes Thema.

**Drei Quellen, in dieser Rangfolge:**

1. Das **persoenliche Token** der Person (liegt seit der Merkliste ohnehin
   verschluesselt am Konto): Die Bibliothek wird mit ihrem Zugang gelesen, der
   Zaehler am Titel gilt dann fuer ihr Konto. Vollstaendig, erfasst auch von
   Hand Abgehaktes - und gilt in **beide** Richtungen: Ein dort entfernter
   Haken nimmt auch hier das Auge weg. Mit mehreren verbundenen Servern
   allerdings nur die Stimme *dieses* Servers, siehe unten.
2. Der Zaehler am Titel aus dem Bibliotheks-Abgleich (``owner_watched``) - er
   gilt fuer den **Eigentuemer** des hinterlegten Zugangs und ist ebenso
   vollstaendig. Nur noch noetig, wenn der Eigentuemer kein eigenes Token hat.
3. Der **Wiedergabe-Verlauf** des Servers - die Notloesung fuer Konten ohne
   Token. Gedeckelt (gemessen 499 Eintraege, davon 38 Filme, waehrend am Titel
   354 gesehene Filme standen) und blind fuer manuell Markiertes. Deshalb nur
   **hinzufuegend**: Aus einer unvollstaendigen Quelle etwas zu loeschen hiesse
   raten.

Wichtig: Konten mit vollstaendiger Quelle (1 oder 2) werden vom Verlauf **nicht
mehr angefasst** - sonst setzte ein alter Verlaufseintrag den gerade entfernten
Haken sofort wieder.

**Und die Regel, sobald mehr als ein Server verbunden ist:** Gesehen, wenn
*irgendeiner* es sagt. Jede Zeile fuehrt in ``providers`` mit, wer sie stuetzt;
meldet ein Server einen Titel nicht mehr, faellt nur seine Stimme weg, und die
Zeile geht erst, wenn niemand mehr uebrig ist. Ohne diese Regel raeumte bei
zwei Servern jeder Durchlauf weg, was der andere gerade gesetzt hat - ein
Karussell, dessen Ursache niemand erkennen wuerde.

Zwei Dinge machen das kniffliger, als es klingt, und beide sind hier geloest:

* **Plex fuehrt zwei Nummernraeume in einem Feld.** Der Eigentuemer des Servers
  erscheint im Verlauf als ``1``, alle anderen unter ihrer plex.tv-Nummer.
  Deshalb wird zuerst ueber die Nummer zugeordnet und danach ueber den Namen.
* **Der Verlauf nennt keine TMDB-Kennungen**, nur die internen Nummern des
  Servers. Die Bruecke ist ``MediaServerLibraryItem.rating_key`` - genau dafuer
  wird sie beim Bibliotheks-Abgleich mitgeschrieben.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crypto import decrypt
from ..models import (
    StorageEntry,
    UserWatchedSeason,
    MediaServerLibraryItem,
    MediaType,
    Notification,
    NotificationType,
    User,
    UserWatched,
)
from . import mediaserver_accounts as konten, notify
from .mediaserver import (
    media_server_for_setup,
    verbundene_anbieter,
    MediaServerError,
    SeasonWatchedRecord,
    WatchedRecord,
)
from .settings_service import AppSettings
from . import logs

logger = logging.getLogger("nexview.mediaserver")

# Unter dieser Nummer fuehrt der Server den Eigentuemer des Zugangs. Sie ist
# bei Plex fest; geteilte Nutzer erscheinen dagegen unter ihrer plex.tv-Nummer.
EIGENTUEMER_KONTO = "1"


def _vergleichbar(name: str) -> str:
    """Namen vergleichbar machen.

    Plex fuehrt denselben Menschen mal als "Dilara Uygun-Mrozek" und mal als
    "DilaraUygunMrozek" - je nachdem, ob der Anzeigename oder der Anmeldename
    gemeint ist.
    """
    return "".join(zeichen for zeichen in name.lower() if zeichen.isalnum())


def _konto_id(user: User, provider: str) -> str:
    """Die Kontonummer dieser Person auf diesem Server - leer, wenn unbekannt.

    Steht an der Verknuepfung und muss deshalb nirgends erfragt werden. Fuer
    Emby ist sie die **einzige** Quelle: Dort gibt es kein "/Users/Me", ueber
    das ein Adapter sie sonst nachschlagen koennte.
    """
    zeile = konten.verknuepfung(user, provider)
    return (zeile.account_id or "") if zeile is not None else ""


def _token(user: User, provider: str) -> str:
    """Das persoenliche Token **dieses Anbieters** - leer, wenn keines taugt.

    ⚠️ Der Anbieter ist entscheidend, nicht schmueckendes Beiwerk: Ein
    Plex-Token an Jellyfin geschickt ergibt ein 401, und daraufhin bekaeme die
    Person die Aufforderung, sich neu zu verbinden - obwohl bei ihr alles in
    Ordnung ist.
    """
    zeile = konten.verknuepfung(user, provider)
    if zeile is None or not zeile.token:
        return ""
    try:
        return decrypt(zeile.token)
    except Exception:  # noqa: BLE001 - Schluesselwechsel, beschaedigter Wert
        logger.warning(
            "Media server token of %s for %r is not readable", user.username, provider
        )
        return ""


async def _konto_zuordnung(db: Session, server, benutzer: list[User]) -> dict[str, int]:
    """Welche Konto-Kennung des Servers gehoert zu welchem Nexview-Benutzer?

    Zwei Wege, und beide werden gebraucht: Geteilte Nutzer erscheinen im
    Verlauf unter ihrer plex.tv-Nummer, die genauso im Nexview-Konto steht.
    Der **Eigentuemer** dagegen laeuft unter der 1 - fuer ihn hilft nur der
    Name aus der Kontenliste des Servers.
    """
    # Die Kennung **dieses** Anbieters, nicht die zuletzt verknuepfte am
    # Benutzer: Wer Plex und Jellyfin hat, traegt dort nur eine von beiden.
    zeilen = {u.id: konten.verknuepfung(u, server.provider) for u in benutzer}
    nach_kennung = {
        z.account_id: uid for uid, z in zeilen.items() if z is not None
    }
    nach_name = {
        _vergleichbar(z.username or ""): uid
        for uid, z in zeilen.items()
        if z is not None and z.username
    }

    zuordnung: dict[str, int] = dict(nach_kennung)  # type: ignore[arg-type]
    try:
        for konto in await server.list_server_users():
            if konto.account_id in zuordnung:
                continue
            treffer = nach_name.get(_vergleichbar(konto.username))
            if treffer is not None:
                zuordnung[konto.account_id] = treffer
    except (MediaServerError, NotImplementedError):
        # Ohne Kontenliste bleibt die Zuordnung ueber die Nummer - fuer alle
        # ausser dem Eigentuemer reicht das.
        pass
    return zuordnung


def _wer(user: User) -> str:
    """Wie eine Person im Protokoll heissen soll.

    Der Anzeigename dazu, wenn es einen gibt: Ein Konto namens "user" liest
    sich sonst wie ein Platzhalter statt wie eine Person - und genau das ist
    beim Lesen des Protokolls passiert.
    """
    if user.display_name and user.display_name != user.username:
        return f"{user.username} ({user.display_name})"
    return user.username


def _neu_verbinden_hinweis(
    db: Session, user: User, anbieter: str, anbieter_name: str
) -> None:
    """Der Person sagen, dass ihr Zugang eine neue Anmeldung braucht.

    Nur sie selbst kann das beheben - der Weg dorthin ist die einmalige
    Anmeldung auf der Profilseite, dieselbe wie bei der Merkliste.

    **Genau ein Hinweis je hinterlegtem Token.** Der Abgleich laeuft stuendlich;
    eine Glocke, die jede Stunde dasselbe meldet, wird abgestellt statt
    beachtet. Nach einer neuen Anmeldung (``watchlist_connected_at`` rueckt
    vor) darf ein spaeterer Ausfall wieder melden.
    """
    logger.warning("%s no longer accepts the token of %s", anbieter, _wer(user))

    # Merken, dass das Token abgelehnt wurde - **ohne es zu loeschen**.
    # Geloescht saehe es aus wie "nie verbunden"; so kann die Oberflaeche
    # gezielt der betroffenen Person sagen, dass sie sich neu anmelden muss.
    konten.token_abgelehnt(user, anbieter_name)
    # Der Stichtag gehoert zu **diesem** Anbieter. Aus der gespiegelten Spalte
    # gelesen haette eine frische Jellyfin-Anmeldung den Hinweis zu Plex
    # unterdrueckt - oder umgekehrt einen laengst gemeldeten erneut zugelassen.
    zeile = konten.verknuepfung(user, anbieter_name)
    stichtag = (zeile.token_connected_at if zeile else None) or datetime.min
    schon = db.scalar(
        select(Notification.id).where(
            Notification.user_id == user.id,
            Notification.type == NotificationType.mediaserver_reconnect,
            Notification.created_at > stichtag,
        )
    )
    if schon is not None:
        return
    notify.create(
        db,
        user=user,
        kind=NotificationType.mediaserver_reconnect,
        message_key="notifications.mediaserverReconnect",
        title=anbieter,
        # Eine persoenliche Zugangsfrage gehoert nicht in die serverseitigen
        # Sammelkanaele.
        broadcast=False,
    )


def _staffel_serien(
    db: Session, benutzer_id: int, werke: dict[str | None, MediaServerLibraryItem]
) -> dict[int, str]:
    """Welche Serien brauchen Staffel-Augen? TMDB-Kennung -> Plex-Kennung.

    Nur die Serien aus Speicher-Posten der Person - dort und nirgends sonst
    wird das Staffel-Auge angezeigt, und jede Serie kostet eine Anfrage.
    """
    kennungen = set(
        db.scalars(
            select(StorageEntry.tmdb_id).where(
                StorageEntry.user_id == benutzer_id,
                StorageEntry.media_type == MediaType.tv,
                StorageEntry.tmdb_id.is_not(None),
            )
        )
    )
    if not kennungen:
        return {}
    return {
        werk.tmdb_id: werk.rating_key
        for werk in werke.values()
        if werk.media_type == MediaType.tv
        and werk.tmdb_id in kennungen
        and werk.rating_key
    }


def _staffeln_uebernehmen(
    db: Session,
    benutzer_id: int,
    stand: list[SeasonWatchedRecord],
    werke: dict[str | None, MediaServerLibraryItem],
    gefragte: set[int],
    anbieter: str,
) -> int:
    """Die vollstaendig gesehenen Staffeln uebernehmen - in beide Richtungen.

    Wie bei den Titeln gilt der Stand des Servers, aber nur **innerhalb der
    abgefragten Serien** (``gefragte``, TMDB-Kennungen): Fuer die ist die
    Antwort vollstaendig - eine Staffel, die dort fehlt, ist nicht (mehr)
    komplett gesehen und verliert ihren Marker. Nur so bleibt "gruen = alle
    Folgen gesehen" eine wahre Aussage und keine historische. Serien, nach
    denen gar nicht gefragt wurde, bleiben unangetastet.

    Und wie bei den Titeln zaehlt der Server nur fuer sich: Meldet er eine
    Staffel nicht mehr, faellt seine Stimme weg - nicht der Marker. Der geht
    erst, wenn kein Server mehr uebrig ist.
    """
    ziel: set[tuple[int, int]] = set()
    for eintrag in stand:
        werk = werke.get(eintrag.item_key)
        if werk is None or werk.tmdb_id is None or werk.media_type != MediaType.tv:
            continue
        ziel.add((werk.tmdb_id, eintrag.season))

    vorhanden = {
        (zeile.tmdb_id, zeile.season): zeile
        for zeile in db.scalars(
            select(UserWatchedSeason).where(UserWatchedSeason.user_id == benutzer_id)
        )
    }

    geschrieben = 0
    for tmdb_id, staffel in ziel - set(vorhanden):
        db.add(
            UserWatchedSeason(
                user_id=benutzer_id,
                tmdb_id=tmdb_id,
                season=staffel,
                providers=anbieter,
            )
        )
        geschrieben += 1
    for schluessel, zeile in vorhanden.items():
        if schluessel in ziel:
            _anbieter_dazu(zeile, anbieter)
            continue
        if schluessel[0] not in gefragte:
            continue
        rest = [name for name in zeile.provider_list if name != anbieter]
        if rest:
            zeile.providers = ",".join(rest)
            continue
        db.delete(zeile)
        geschrieben += 1
    return geschrieben


def _anbieter_dazu(zeile: UserWatched | UserWatchedSeason, anbieter: str) -> None:
    """Diesen Server in die Herkunftsliste der Zeile aufnehmen."""
    dabei = set(zeile.provider_list)
    if anbieter in dabei:
        return
    dabei.add(anbieter)
    zeile.providers = ",".join(sorted(dabei))


def _vollstaendig_uebernehmen(
    db: Session,
    benutzer_id: int,
    stand: list[WatchedRecord],
    werke: dict[str | None, MediaServerLibraryItem],
    im_bestand: set[tuple[MediaType, int]],
    vorhanden: dict[tuple[int, MediaType, int], UserWatched],
    anbieter: str,
) -> int:
    """Einen vollstaendigen Gesehen-Stand uebernehmen - in beide Richtungen.

    Neues kommt dazu, und was der Server nicht (mehr) als gesehen fuehrt,
    verliert **seine Stimme**. Entfernt wird nur innerhalb des aktuellen
    Bestands: Verschwindet ein Titel ganz aus der Bibliothek, kann der Abgleich
    ihn nicht mehr sehen - das macht die Wiedergabe nicht ungeschehen, also
    bleibt das Auge.

    ⚠️ **Der Server gilt nur fuer sich selbst, nicht fuer alle.** Frueher stand
    hier "der Stand des Servers gilt", und die Zeile wurde geloescht, sobald er
    sie nicht meldete. Mit zwei verbundenen Servern waere das ein Karussell
    gewesen: Jeder Durchlauf haette weggeraeumt, was der andere gesetzt hat -
    alle paar Minuten, ohne dass jemand die Ursache erkennen koennte.

    Deshalb gilt jetzt: **gesehen, wenn irgendein verbundener Server es sagt.**
    Meldet dieser Server einen Titel nicht mehr, wird nur *er* aus der
    Herkunftsliste gestrichen. Die Zeile faellt erst, wenn niemand mehr uebrig
    ist - dann ist sie dieselbe Loeschung wie frueher.

    Der Preis ist bewusst bezahlt: Wer einen Haken auf *einem* Server wegnimmt,
    sieht das Auge nicht mehr verschwinden, solange ein anderer Server ihn noch
    fuehrt. Die Oberflaeche nennt dafuer die Namen, statt es zu verschweigen.
    """
    gesehen: dict[tuple[MediaType, int], datetime | None] = {}
    for eintrag in stand:
        werk = werke.get(eintrag.item_key)
        if werk is None or werk.tmdb_id is None:
            continue
        schluessel = (werk.media_type, werk.tmdb_id)
        bisher = gesehen.get(schluessel)
        if schluessel not in gesehen or (
            eintrag.watched_at and (bisher is None or eintrag.watched_at > bisher)
        ):
            gesehen[schluessel] = eintrag.watched_at

    geschrieben = 0
    for schluessel, wann in gesehen.items():
        art, tmdb_id = schluessel
        alt = vorhanden.get((benutzer_id, art, tmdb_id))
        if alt is None:
            neu = UserWatched(
                user_id=benutzer_id,
                media_type=art,
                tmdb_id=tmdb_id,
                watched_at=wann,
                providers=anbieter,
            )
            db.add(neu)
            vorhanden[(benutzer_id, art, tmdb_id)] = neu
            geschrieben += 1
        else:
            if wann and (alt.watched_at is None or wann > alt.watched_at):
                alt.watched_at = wann
            # Auch wenn die Zeile schon stand: Dieser Server sagt jetzt
            # ebenfalls "gesehen", und das gehoert vermerkt.
            _anbieter_dazu(alt, anbieter)

    entfernt = 0
    zurueckgenommen = 0
    for (user_id, art, tmdb_id), zeile in list(vorhanden.items()):
        if user_id != benutzer_id:
            continue
        if (art, tmdb_id) in gesehen or (art, tmdb_id) not in im_bestand:
            continue

        # Dieser Server fuehrt den Titel nicht mehr als gesehen. Nur seine
        # Stimme faellt weg - sagt ein anderer noch ja, bleibt das Auge.
        rest = [name for name in zeile.provider_list if name != anbieter]
        if rest:
            zeile.providers = ",".join(rest)
            zurueckgenommen += 1
            continue

        db.delete(zeile)
        del vorhanden[(user_id, art, tmdb_id)]
        entfernt += 1

    if entfernt:
        logger.info("Watched: removed %d marker(s) for user %d", entfernt, benutzer_id)
    if zurueckgenommen:
        logger.info(
            "Watched: %r no longer reports %d marker(s) for user %d, another server still does",
            anbieter,
            zurueckgenommen,
            benutzer_id,
        )
    return geschrieben


async def refresh(db: Session, settings: AppSettings) -> int:
    """Gesehenes einlesen - von **allen** verbundenen Servern.

    Nacheinander und nicht gleichzeitig: Die Zusammenfuehrung in
    ``_vollstaendig_uebernehmen`` liest den vorhandenen Bestand und schreibt
    ihn zurueck. Zwei Server, die sich dabei ins Wort fallen, wuerden sich die
    Herkunftsvermerke gegenseitig ueberschreiben - und genau die entscheiden,
    ob ein Auge gruen bleibt, wenn *ein* Server einen Titel nicht mehr meldet.
    """
    gesamt = 0
    for anbieter in verbundene_anbieter(settings):
        gesamt += await _einen_server_abgleichen(db, settings, anbieter)

    # ⚠️ **Hier, und nicht im Waechter der Loeschfrist.** Wer einen zum
    # Loeschen vorgemerkten Titel angesehen hat, hat damit widersprochen -
    # ohne einen Knopf zu suchen und ohne von der Vormerkung zu wissen. Der
    # Widerspruch muss deshalb genau dann greifen, wenn das Ansehen bekannt
    # wird, nicht erst wenn die Frist ablaeuft: Sonst stuende der Titel bis
    # zuletzt mit Countdown auf der Startseite, obwohl er laengst gerettet ist.
    #
    # Der Import steht hier unten, weil ``loeschfrist`` seinerseits Modelle
    # zieht, die dieses Modul schon geladen hat.
    from . import loeschfrist

    aufgehoben = 0
    for benutzer_id in db.scalars(select(UserWatched.user_id).distinct()):
        aufgehoben += loeschfrist.angesehen_hebt_auf(db, benutzer_id)
    if aufgehoben:
        db.commit()
        logger.info("%d scheduled deletion(s) cancelled - watched during the grace period", aufgehoben)

    return gesamt


async def _einen_server_abgleichen(
    db: Session, settings: AppSettings, anbieter: str
) -> int:
    """Gesehenes **eines** Servers einlesen."""
    server = media_server_for_setup(settings, anbieter)

    # Nur wer bei *diesem* Anbieter ein Konto hat. Frueher stand hier ein
    # Filter auf ``User.mediaserver_account_id`` - der findet im Parallel-
    # betrieb auch Leute, die nur beim anderen Server ein Konto haben, und
    # schickte deren Token an den falschen.
    benutzer = konten.verknuepfte_konten(db, anbieter)
    if not benutzer:
        # Niemand hat sein Konto verknuepft - dann gibt es nichts zuzuordnen.
        return 0

    # Interne Nummer -> Titel. Nur was in der Bibliothek steht, laesst sich
    # ueberhaupt einem TMDB-Titel zuordnen.
    zeilen = list(
        db.scalars(
            select(MediaServerLibraryItem).where(
                MediaServerLibraryItem.provider == server.provider,
                MediaServerLibraryItem.rating_key.isnot(None),
                MediaServerLibraryItem.tmdb_id.isnot(None),
            )
        )
    )
    werke = {z.rating_key: z for z in zeilen}
    if not werke:
        logger.debug("Watched: library not indexed yet")
        return 0
    im_bestand = {(z.media_type, z.tmdb_id) for z in zeilen if z.tmdb_id is not None}

    vorhanden = {
        (w.user_id, w.media_type, w.tmdb_id): w
        for w in db.scalars(select(UserWatched))
    }

    geschrieben = 0
    vollstaendig: set[int] = set()

    # Quelle 1: das persoenliche Token - vollstaendig, gilt in beide Richtungen.
    for user in benutzer:
        token = _token(user, server.provider)
        if not token:
            continue
        try:
            # Die Kontonummer steht an der Verknuepfung - mitgeben spart eine
            # Rueckfrage, und Emby hat keine andere Quelle dafuer.
            stand = await server.watched_index(token, _konto_id(user, server.provider))
        except NotImplementedError:
            # Dieser Anbieter kennt den Weg nicht - alle laufen ueber den
            # Verlauf.
            break
        except MediaServerError as fehler:
            if fehler.status_code == 401:
                _neu_verbinden_hinweis(db, user, server.label, server.provider)
            else:
                logger.warning(
                    "Watched state of %s not readable: %s", user.username, logs.kennung(fehler)
                )
            continue
        # Es geht wieder - einen frueheren Hinweis wegnehmen. Sonst bliebe der
        # rote Balken stehen, obwohl die Ursache behoben ist.
        konten.token_geht_wieder(user, server.provider)

        geschrieben += _vollstaendig_uebernehmen(
            db, user.id, stand, werke, im_bestand, vorhanden, server.provider
        )
        # Die Staffel-Stufe haengt am selben Token - und wird **gezielt**
        # abgefragt: Sie kostet eine Anfrage je Serie und wird nur fuer die
        # Serien gebraucht, die in Speicher-Posten dieser Person stehen.
        # Kann der Anbieter sie nicht liefern, gibt es schlicht keine
        # Staffel-Augen - keine falschen.
        gewuenscht = _staffel_serien(db, user.id, werke)
        staffel_stand = None
        if gewuenscht:
            try:
                staffel_stand = await server.watched_seasons(
                    token, list(gewuenscht.values())
                )
            except NotImplementedError:
                staffel_stand = None
            except MediaServerError as fehler:
                logger.warning(
                    "Season state of %s not readable: %s",
                    user.username,
                    logs.kennung(fehler),
                )
        if staffel_stand is not None:
            geschrieben += _staffeln_uebernehmen(
                db, user.id, staffel_stand, werke, set(gewuenscht), server.provider
            )
        vollstaendig.add(user.id)

    # Quelle 2: der Zaehler am Titel - fuer den Eigentuemer des hinterlegten
    # Zugangs, falls er kein eigenes Token hat. Ebenfalls vollstaendig.
    zuordnung: dict[str, int] = {}
    rest = [u for u in benutzer if u.id not in vollstaendig]
    if rest:
        zuordnung = await _konto_zuordnung(db, server, rest)
        eigentuemer_id = zuordnung.get(EIGENTUEMER_KONTO)
        if eigentuemer_id is not None:
            stand = [
                WatchedRecord(
                    account_id=EIGENTUEMER_KONTO,
                    item_key=z.rating_key or "",
                    media_type=z.media_type.value,
                )
                for z in zeilen
                if z.owner_watched
            ]
            geschrieben += _vollstaendig_uebernehmen(
                db, eigentuemer_id, stand, werke, im_bestand, vorhanden, server.provider
            )
            vollstaendig.add(eigentuemer_id)

    # Quelle 3: der Verlauf - die Notloesung fuer alle uebrigen. Nur
    # hinzufuegend, und nie fuer Konten mit vollstaendiger Quelle.
    rest_ids = {u.id for u in benutzer} - vollstaendig
    if rest_ids:
        try:
            verlauf = await server.watched_since(None)
        except NotImplementedError:
            verlauf = []
        except MediaServerError as fehler:
            verlauf = []
            logger.warning("Playback history not readable: %s", logs.kennung(fehler))

        for eintrag in verlauf:
            benutzer_id = zuordnung.get(eintrag.account_id)
            if benutzer_id is None or benutzer_id not in rest_ids:
                continue
            werk = werke.get(eintrag.item_key)
            if werk is None or werk.tmdb_id is None:
                continue

            schluessel = (benutzer_id, werk.media_type, werk.tmdb_id)
            alt = vorhanden.get(schluessel)
            if alt is None:
                neu = UserWatched(
                    user_id=benutzer_id,
                    media_type=werk.media_type,
                    tmdb_id=werk.tmdb_id,
                    watched_at=eintrag.watched_at,
                )
                db.add(neu)
                vorhanden[schluessel] = neu
                geschrieben += 1
            elif eintrag.watched_at and (
                alt.watched_at is None or eintrag.watched_at > alt.watched_at
            ):
                alt.watched_at = eintrag.watched_at

    db.commit()
    if geschrieben:
        logger.info("Watched: %d new match(es)", geschrieben)
    return geschrieben


def gesehene_staffeln(
    db: Session, user_id: int, paare: list[tuple[int, int]]
) -> set[tuple[int, int]]:
    """Welche dieser Staffeln hat *dieser* Benutzer vollstaendig gesehen?

    ``paare`` sind (TMDB-Kennung, Staffelnummer). Wie bei den Titeln gilt:
    **immer nur die eigenen** - die Gesehen-Daten anderer sind bewusst tabu.
    """
    if not paare:
        return set()
    kennungen = {tmdb for tmdb, _ in paare}
    zeilen = db.scalars(
        select(UserWatchedSeason).where(
            UserWatchedSeason.user_id == user_id,
            UserWatchedSeason.tmdb_id.in_(kennungen),
        )
    )
    gesehen = {(zeile.tmdb_id, zeile.season) for zeile in zeilen}
    return {paar for paar in paare if paar in gesehen}


def herkunft_aufteilen(
    sagt_ja: list[str], verbunden: list[str]
) -> tuple[list[str], list[str]]:
    """Wer sagt "gesehen", und wer widerspricht - oder beides leer.

    ⚠️ **Schweigen ist der Normalfall.** Solange nur ein Server verbunden ist,
    gibt es nichts zu unterscheiden: "gesehen laut Plex" waere an jeder Kachel
    dieselbe Selbstverstaendlichkeit. Erst wenn mindestens zwei verbunden sind
    *und* sie sich uneins sind, kommen Namen ins Spiel.

    Genauso schweigt es, wenn alle verbundenen Server einig sind - dann sagt
    das gruene Auge ja bereits alles.

    Server, die nicht (mehr) verbunden sind, kommen nicht vor: Ihre Stimme
    steht zwar noch in der Zeile, aber ueber sie laesst sich nichts
    Verlaessliches mehr sagen.
    """
    if len(verbunden) < 2:
        return [], []
    ja = sorted(name for name in sagt_ja if name in verbunden)
    nein = sorted(name for name in verbunden if name not in sagt_ja)
    if not nein or not ja:
        return [], []
    return ja, nein


def gesehene_kennungen(
    db: Session, user_id: int, media_type: MediaType, tmdb_ids: list[int]
) -> dict[int, list[str]]:
    """Welche dieser Titel hat *dieser* Benutzer schon gesehen - und laut wem?

    **Immer nur die eigenen - auch fuer Administratoren.** Das ist eine
    ausdrueckliche Entscheidung des Betreibers und kein Versaeumnis: Zwischendurch
    war eine Admin-Auswertung ueber alle Benutzer vorgesehen, sie wurde bewusst
    wieder verworfen. Wer sie doch einmal einbaut, sollte wissen, dass er damit
    eine getroffene Entscheidung umkehrt - und dass die Betroffenen davon nichts
    mitbekaemen.

    Gibt ein Woerterbuch zurueck, keine Menge - **aber alle bisherigen Aufrufer
    bleiben unveraendert**, denn sie fragen nur mit ``in``, und das bedeutet bei
    einem Woerterbuch dasselbe. Der Wert dahinter ist die Herkunft: welche
    Medienserver diesen Titel als gesehen fuehren. Gebraucht wird sie erst,
    wenn mehr als einer verbunden ist - dann kann das gruene Auge naemlich
    heissen "der eine sagt ja, der andere nein", und das soll es dann auch
    sagen duerfen.
    """
    if not tmdb_ids:
        return {}
    return {
        zeile.tmdb_id: zeile.provider_list
        for zeile in db.scalars(
            select(UserWatched).where(
                UserWatched.user_id == user_id,
                UserWatched.media_type == media_type,
                UserWatched.tmdb_id.in_(tmdb_ids),
            )
        )
    }
