"""Die persoenliche Merkliste des Media-Servers anzeigen.

Der ganze Dienst tut **eine** Sache: Er liest die Merkliste einer Person und
gibt sie als gewoehnliche Titel-Liste zurueck - dieselben Kacheln wie im
Katalog, mit denselben Abzeichen. Angefragt wird danach ueber den ganz
normalen Weg; hier passiert nichts von selbst.

Das ist bewusst so klein. Eine fruehere Fassung fragte im Hintergrund
selbsttaetig an und brauchte dafuer Rechte, ein Gedaechtnis mit fuenf
Zustaenden, einen Stichtag, eine Warteschlange und eine Zielinstanz - und hat
in der Praxis sechzig Filme in den falschen Ordner gelegt, weil an einer
Stelle geraten wurde. Wer klickt, sieht was er tut.

**Zwei Dinge, die man wissen muss:**

* Eine Merkliste laesst sich nur mit dem Token **ihres Eigentuemers** lesen.
  Der hinterlegte Zugang des Administrators sieht sie nicht. Das Token
  entsteht beim Verknuepfen des Plex-Kontos bzw. bei jeder Anmeldung mit Plex.
* Plex nennt in der Liste selbst **keine TMDB-Nummern**; die kosten eine
  Abfrage je Titel. Deshalb merkt sich ``WatchlistLookup`` die Zuordnung -
  nicht als Gedaechtnis ueber Entscheidungen, sondern als reine Abkuerzung.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crypto import decrypt
from ..models import MediaType, User, WatchlistLookup, utcnow
from ..schemas_media import MediaItem
from . import media
from .media import AgeRestricted, TmdbError
from . import mediaserver_accounts as konten
from .mediaserver import MediaServerError, get_media_server
from .mediaserver.base import WatchlistItem
from .settings_service import AppSettings, for_user

logger = logging.getLogger("nexview.watchlist")

# So viele TMDB-Abfragen laufen gleichzeitig. Dieselbe Groessenordnung wie beim
# Media-Server; hoeher bringt nichts, weil TMDB selbst drosselt.
MAX_PARALLEL = 6


class WatchlistFehler(Exception):
    """Die Merkliste laesst sich gerade nicht lesen - mit lesbarem Grund."""

    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _token(user: User) -> str:
    if not user.watchlist_token:
        return ""
    try:
        return decrypt(user.watchlist_token)
    except Exception:  # noqa: BLE001 - Schluesselwechsel, beschaedigter Wert
        logger.warning("Merklisten-Token von %s ist nicht lesbar", user.username)
        return ""


# --------------------------------------------------------------------------
# Zwischenspeicher fuer die Zuordnung
# --------------------------------------------------------------------------


def _bekannte(db: Session, provider: str, guids: list[str]) -> dict[str, WatchlistLookup]:
    if not guids:
        return {}
    return {
        eintrag.guid: eintrag
        for eintrag in db.scalars(
            select(WatchlistLookup).where(
                WatchlistLookup.provider == provider,
                WatchlistLookup.guid.in_(guids),
            )
        )
    }


def _merken(db: Session, provider: str, werke: list[WatchlistItem]) -> None:
    """Zuordnungen festhalten - auch die erfolglosen.

    Ein Titel ohne TMDB-Nummer wird ebenfalls vermerkt: Sonst wuerde er bei
    jedem Oeffnen der Seite erneut nachgeschlagen, obwohl die Antwort feststeht.
    """
    vorhanden = _bekannte(db, provider, [w.guid for w in werke])
    for werk in werke:
        eintrag = vorhanden.get(werk.guid)
        if eintrag is None:
            eintrag = WatchlistLookup(provider=provider, guid=werk.guid)
            db.add(eintrag)
        eintrag.media_type = MediaType(werk.media_type)
        eintrag.tmdb_id = werk.tmdb_id
        eintrag.title = (werk.title or "")[:300]
        eintrag.year = werk.year
        eintrag.updated_at = utcnow()
    db.commit()


async def _mit_kennungen(
    db: Session, server, provider: str, token: str, werke: list[WatchlistItem]
) -> list[WatchlistItem]:
    """Die TMDB-Nummern ergaenzen - aus dem Zwischenspeicher, sonst vom Anbieter."""
    bekannt = _bekannte(db, provider, [w.guid for w in werke])

    fertig: list[WatchlistItem] = []
    offen: list[WatchlistItem] = []
    for werk in werke:
        if werk.tmdb_id is not None:
            fertig.append(werk)
            continue
        treffer = bekannt.get(werk.guid)
        if treffer is not None:
            # Auch ein leeres Ergebnis ist ein Ergebnis - siehe ``_merken``.
            fertig.append(
                WatchlistItem(
                    guid=werk.guid,
                    media_type=werk.media_type,
                    title=werk.title or treffer.title,
                    year=werk.year or treffer.year,
                    tmdb_id=treffer.tmdb_id,
                    rating_key=werk.rating_key,
                )
            )
            continue
        offen.append(werk)

    if offen:
        logger.info("Merkliste: %d neue Titel werden nachgeschlagen", len(offen))
        nachgeschlagen = await server.watchlist_ids(token, offen)
        _merken(db, provider, nachgeschlagen)
        fertig.extend(nachgeschlagen)

    return fertig


# --------------------------------------------------------------------------
# Die Merkliste als Titel-Liste
# --------------------------------------------------------------------------


async def _als_titel(
    db: Session, settings: AppSettings, art: str, kennungen: list[int]
) -> list[MediaItem]:
    """Aus TMDB-Nummern fertige Kacheln machen.

    Ueber ``media.detail``, weil das drei Dinge auf einmal erledigt: Es
    zwischenspeichert (sieben Tage), es liefert genau die Felder, die eine
    Kachel braucht, und es haelt die **Altersbeschraenkung** ein. Ein Titel
    ueber der Grenze verschwindet hier still - genau wie ueberall sonst.
    """
    grenze = asyncio.Semaphore(MAX_PARALLEL)

    async def einer(tmdb_id: int) -> MediaItem | None:
        async with grenze:
            try:
                return await media.detail(db, settings, art, tmdb_id)
            except AgeRestricted:
                return None
            except TmdbError as fehler:
                logger.info("Merkliste: %s (%d) nicht ladbar - %s", art, tmdb_id, fehler.message)
                return None

    ergebnisse = await asyncio.gather(*(einer(kennung) for kennung in kennungen))
    return [item for item in ergebnisse if item is not None]


async def lesen(
    db: Session, settings: AppSettings, user: User
) -> tuple[list[MediaItem], list[MediaItem], int]:
    """Die Merkliste dieser Person: (Filme, Serien, nicht zuzuordnen).

    Die dritte Zahl ist die Ehrlichkeit: Titel, zu denen Plex keine
    TMDB-Nummer kennt, lassen sich nicht anzeigen und nicht anfragen. Sie
    verschwinden nicht stillschweigend, sondern werden gezaehlt.
    """
    server = get_media_server(settings)
    if server is None:
        raise WatchlistFehler(
            "mediaserver_not_configured", "Es ist kein Media-Server verbunden.", 404
        )

    token = _token(user)
    if not token:
        raise WatchlistFehler(
            "watchlist_not_connected",
            "Für die Merkliste fehlt dein persönlicher Plex-Zugang.",
        )

    try:
        werke = await server.watchlist(token)
        werke = await _mit_kennungen(db, server, settings.mediaserver_provider, token, werke)
    except MediaServerError as fehler:
        if fehler.status_code == 401:
            # Nicht bloss ein Fehler, sondern eine Handlungsanweisung: Das
            # Token taugt nicht mehr, es hilft nur eine neue Anmeldung.
            #
            # Hier faellt es frueher auf als im stuendlichen Abgleich - wer die
            # Seite oeffnet, merkt es sofort. Deshalb auch hier vermerken,
            # damit der Hinweis unter dem Menue erscheint.
            if konten.token_abgelehnt(user):
                db.commit()
            raise WatchlistFehler(
                "watchlist_token_invalid",
                "Plex nimmt den hinterlegten Zugang nicht mehr an. Bitte die "
                "Merkliste neu verbinden.",
            ) from fehler
        raise WatchlistFehler("mediaserver_unreachable", fehler.message, 502) from fehler

    ohne_zuordnung = sum(1 for werk in werke if werk.tmdb_id is None)
    benutzer_settings = for_user(settings, user)

    filme = await _als_titel(
        db,
        benutzer_settings,
        "movie",
        [w.tmdb_id for w in werke if w.media_type == "movie" and w.tmdb_id],
    )
    serien = await _als_titel(
        db,
        benutzer_settings,
        "tv",
        [w.tmdb_id for w in werke if w.media_type == "tv" and w.tmdb_id],
    )
    return filme, serien, ohne_zuordnung
