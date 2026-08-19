"""Wer hat was schon gesehen?

Der Media-Server weiss es, Nexview zeigt es als kleines Abzeichen an der
Kachel. Mehr nicht - kein Filter, keine "Weiterschauen"-Reihe; das waere ein
eigenes Thema.

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

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MediaServerLibraryItem, MediaType, User, UserWatched
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


async def _konto_zuordnung(db: Session, server) -> dict[str, int]:
    """Welche Konto-Kennung des Servers gehoert zu welchem Nexview-Benutzer?

    Zwei Wege, und beide werden gebraucht: Geteilte Nutzer erscheinen im
    Verlauf unter ihrer plex.tv-Nummer, die genauso im Nexview-Konto steht.
    Der **Eigentuemer** dagegen laeuft unter der 1 - fuer ihn hilft nur der
    Name aus der Kontenliste des Servers.
    """
    benutzer = list(db.scalars(select(User).where(User.mediaserver_account_id.isnot(None))))
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


async def refresh(db: Session, settings: AppSettings) -> int:
    """Gesehenes einlesen. Gibt die Zahl der neuen Zuordnungen zurueck.

    **Zwei Quellen, und das ist noetig:**

    * Der Zaehler **am Titel** (``owner_watched``) gilt fuer den Eigentuemer des
      hinterlegten Zugangs. Er ist vollstaendig und faellt beim Einlesen der
      Bibliothek ohnehin ab.
    * Der **Verlauf** ist die einzige Quelle fuer alle anderen - aber nur eine
      Notloesung: Plex bewahrt ihn begrenzt auf. Gemessen an einer echten
      Installation kamen 499 Eintraege zurueck, davon 38 Filme, waehrend am
      Titel 354 gesehene Filme vermerkt waren. Wer sich allein auf den Verlauf
      verlaesst, meldet dem Eigentuemer ein Zehntel der Wahrheit.
    """
    server = get_media_server(settings)
    if server is None:
        return 0

    zuordnung = await _konto_zuordnung(db, server)
    if not zuordnung:
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

    vorhanden = {
        (w.user_id, w.media_type, w.tmdb_id): w
        for w in db.scalars(select(UserWatched))
    }

    # Quelle 1: der Zaehler am Titel - gilt fuer den Eigentuemer des Zugangs.
    # Plex fuehrt ihn auf dem Server unter der Konto-Nummer 1.
    eintraege: list[WatchedRecord] = [
        WatchedRecord(
            account_id=EIGENTUEMER_KONTO,
            item_key=z.rating_key or "",
            media_type=z.media_type.value,
        )
        for z in zeilen
        if z.owner_watched
    ]

    # Quelle 2: der Verlauf - fuer alle anderen die einzige Moeglichkeit.
    try:
        eintraege.extend(await server.watched_since(None))
    except NotImplementedError:
        pass
    except MediaServerError as fehler:
        logger.warning("Wiedergabe-Verlauf nicht lesbar: %s", fehler.message)

    geschrieben = 0
    for eintrag in eintraege:
        benutzer_id = zuordnung.get(eintrag.account_id)
        if benutzer_id is None:
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
        elif eintrag.watched_at and (alt.watched_at is None or eintrag.watched_at > alt.watched_at):
            alt.watched_at = eintrag.watched_at

    db.commit()
    if geschrieben:
        logger.info("Wiedergabe-Verlauf: %d neue Zuordnungen", geschrieben)
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
