"""Wer hat was schon gesehen?

Der Media-Server weiss es, Nexview zeigt es als kleines Abzeichen an der
Kachel. Mehr nicht - kein Filter, keine "Weiterschauen"-Reihe; das waere ein
eigenes Thema.

**Drei Quellen, in dieser Rangfolge:**

1. Das **persoenliche Token** der Person (liegt seit der Merkliste ohnehin
   verschluesselt am Konto): Die Bibliothek wird mit ihrem Zugang gelesen, der
   Zaehler am Titel gilt dann fuer ihr Konto. Vollstaendig, erfasst auch von
   Hand Abgehaktes - und gilt in **beide** Richtungen: Ein in Plex entfernter
   Haken nimmt auch hier das Auge weg.
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
    MediaServerLibraryItem,
    MediaType,
    Notification,
    NotificationType,
    User,
    UserWatched,
)
from . import mediaserver_accounts as konten, notify
from .mediaserver import MediaServerError, WatchedRecord, get_media_server
from .settings_service import AppSettings

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


def _token(user: User) -> str:
    """Das persoenliche Token entschluesseln - leer, wenn keines taugt."""
    if not user.watchlist_token:
        return ""
    try:
        return decrypt(user.watchlist_token)
    except Exception:  # noqa: BLE001 - Schluesselwechsel, beschaedigter Wert
        logger.warning("Media-Server-Token von %s ist nicht lesbar", user.username)
        return ""


async def _konto_zuordnung(db: Session, server, benutzer: list[User]) -> dict[str, int]:
    """Welche Konto-Kennung des Servers gehoert zu welchem Nexview-Benutzer?

    Zwei Wege, und beide werden gebraucht: Geteilte Nutzer erscheinen im
    Verlauf unter ihrer plex.tv-Nummer, die genauso im Nexview-Konto steht.
    Der **Eigentuemer** dagegen laeuft unter der 1 - fuer ihn hilft nur der
    Name aus der Kontenliste des Servers.
    """
    nach_kennung = {u.mediaserver_account_id: u.id for u in benutzer}
    nach_name = {
        _vergleichbar(u.mediaserver_username or ""): u.id
        for u in benutzer
        if u.mediaserver_username
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


def _neu_verbinden_hinweis(db: Session, user: User, anbieter: str) -> None:
    """Der Person sagen, dass ihr Zugang eine neue Anmeldung braucht.

    Nur sie selbst kann das beheben - der Weg dorthin ist die einmalige
    Anmeldung auf der Profilseite, dieselbe wie bei der Merkliste.

    **Genau ein Hinweis je hinterlegtem Token.** Der Abgleich laeuft stuendlich;
    eine Glocke, die jede Stunde dasselbe meldet, wird abgestellt statt
    beachtet. Nach einer neuen Anmeldung (``watchlist_connected_at`` rueckt
    vor) darf ein spaeterer Ausfall wieder melden.
    """
    logger.warning("%s nimmt das Token von %s nicht mehr an", anbieter, _wer(user))

    # Merken, dass das Token abgelehnt wurde - **ohne es zu loeschen**.
    # Geloescht saehe es aus wie "nie verbunden"; so kann die Oberflaeche
    # gezielt der betroffenen Person sagen, dass sie sich neu anmelden muss.
    konten.token_abgelehnt(user)
    stichtag = user.watchlist_connected_at or datetime.min
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


def _vollstaendig_uebernehmen(
    db: Session,
    benutzer_id: int,
    stand: list[WatchedRecord],
    werke: dict[str | None, MediaServerLibraryItem],
    im_bestand: set[tuple[MediaType, int]],
    vorhanden: dict[tuple[int, MediaType, int], UserWatched],
) -> int:
    """Einen vollstaendigen Gesehen-Stand uebernehmen - in beide Richtungen.

    Neues kommt dazu, und was der Server nicht (mehr) als gesehen fuehrt, wird
    entfernt - der Stand des Servers gilt. Entfernt wird aber **nur innerhalb
    des aktuellen Bestands**: Verschwindet ein Titel ganz aus der Bibliothek,
    kann der Abgleich ihn nicht mehr sehen - das macht die Wiedergabe nicht
    ungeschehen, also bleibt das Auge.
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
                user_id=benutzer_id, media_type=art, tmdb_id=tmdb_id, watched_at=wann
            )
            db.add(neu)
            vorhanden[(benutzer_id, art, tmdb_id)] = neu
            geschrieben += 1
        elif wann and (alt.watched_at is None or wann > alt.watched_at):
            alt.watched_at = wann

    entfernt = 0
    for (user_id, art, tmdb_id), zeile in list(vorhanden.items()):
        if user_id != benutzer_id:
            continue
        if (art, tmdb_id) in gesehen or (art, tmdb_id) not in im_bestand:
            continue
        db.delete(zeile)
        del vorhanden[(user_id, art, tmdb_id)]
        entfernt += 1
    if entfernt:
        logger.info("Gesehenes: %d Marker fuer Benutzer %d entfernt", entfernt, benutzer_id)
    return geschrieben


async def refresh(db: Session, settings: AppSettings) -> int:
    """Gesehenes einlesen. Gibt die Zahl der neuen Zuordnungen zurueck."""
    server = get_media_server(settings)
    if server is None:
        return 0

    benutzer = list(db.scalars(select(User).where(User.mediaserver_account_id.isnot(None))))
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
        logger.info("Gesehenes: Bibliothek noch nicht eingelesen")
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
        token = _token(user)
        if not token:
            continue
        try:
            stand = await server.watched_index(token)
        except NotImplementedError:
            # Dieser Anbieter kennt den Weg nicht - alle laufen ueber den
            # Verlauf.
            break
        except MediaServerError as fehler:
            if fehler.status_code == 401:
                _neu_verbinden_hinweis(db, user, server.label)
            else:
                logger.warning(
                    "Gesehen-Stand von %s nicht lesbar: %s", user.username, fehler.message
                )
            continue
        # Es geht wieder - einen frueheren Hinweis wegnehmen. Sonst bliebe der
        # rote Balken stehen, obwohl die Ursache behoben ist.
        konten.token_geht_wieder(user)

        geschrieben += _vollstaendig_uebernehmen(
            db, user.id, stand, werke, im_bestand, vorhanden
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
                db, eigentuemer_id, stand, werke, im_bestand, vorhanden
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
            logger.warning("Wiedergabe-Verlauf nicht lesbar: %s", fehler.message)

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
        logger.info("Gesehenes: %d neue Zuordnungen", geschrieben)
    return geschrieben


def gesehene_kennungen(
    db: Session, user_id: int, media_type: MediaType, tmdb_ids: list[int]
) -> set[int]:
    """Welche dieser Titel hat *dieser* Benutzer schon gesehen?

    **Immer nur die eigenen - auch fuer Administratoren.** Das ist eine
    ausdrueckliche Entscheidung des Betreibers und kein Versaeumnis: Zwischendurch
    war eine Admin-Auswertung ueber alle Benutzer vorgesehen, sie wurde bewusst
    wieder verworfen. Wer sie doch einmal einbaut, sollte wissen, dass er damit
    eine getroffene Entscheidung umkehrt - und dass die Betroffenen davon nichts
    mitbekaemen.
    """
    if not tmdb_ids:
        return set()
    return set(
        db.scalars(
            select(UserWatched.tmdb_id).where(
                UserWatched.user_id == user_id,
                UserWatched.media_type == media_type,
                UserWatched.tmdb_id.in_(tmdb_ids),
            )
        )
    )
