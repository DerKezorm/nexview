"""Seerrs Datenformen: wie Nexviews Zustaende fuer einen Seerr-Client aussehen.

Die Feldnamen stammen aus Seerrs Quelltext (``server/models/*.ts`` und
``server/entity/*.ts``, Stand 13.11.2026), nicht aus einer Dokumentation.

⚠️ **TMDB-Daten werden nicht von Hand abgeschrieben, sondern umbenannt.**
Seerr reicht TMDB fast unveraendert weiter und macht nur aus ``snake_case``
``camelCase`` (``poster_path`` -> ``posterPath``). ``kamel`` tut dasselbe fuer
jedes Feld, auch fuer die, die hier niemand aufzaehlt. Eine Liste von Hand
haette genau die Felder vergessen, nach denen ein Client spaeter fragt. Nur wo
Seerr wirklich umbaut (Videos, Schlagworte, Besetzung bei Serien), steht es
ausdruecklich in ``routen.py``.
"""

from __future__ import annotations

import re
import zlib
from datetime import UTC, datetime
from typing import Any

from ..models import MediaRequest, MediaType, RequestStatus, Role, User

# --- Seerrs Zahlen ------------------------------------------------------------

#: ``MediaRequestStatus`` (server/constants/media.ts).
ANFRAGE_WARTET = 1
ANFRAGE_FREIGEGEBEN = 2
ANFRAGE_ABGELEHNT = 3
ANFRAGE_FEHLGESCHLAGEN = 4
ANFRAGE_FERTIG = 5

#: ``MediaStatus``. ⚠️ Seerr hat ``BLOCKLISTED`` (6) vor ``DELETED`` (7)
#: eingeschoben; Overseerr kannte nur ``DELETED`` als 6. Hier gilt Seerr.
MEDIUM_UNBEKANNT = 1
MEDIUM_WARTET = 2
MEDIUM_IN_ARBEIT = 3
MEDIUM_TEILWEISE = 4
MEDIUM_DA = 5
MEDIUM_GESPERRT = 6
MEDIUM_GELOESCHT = 7

#: ``Permission`` (server/lib/permissions.ts), nur die Bits, die hier vergeben werden.
RECHT_ADMIN = 2
RECHT_ANFRAGEN_VERWALTEN = 16
RECHT_ANFRAGEN = 32
RECHT_AUTO = 128
RECHT_AUTO_FILM = 256
RECHT_AUTO_SERIE = 512
RECHT_4K = 1024
RECHT_FILM = 262144
RECHT_SERIE = 524288

#: ``UserType`` (server/constants/user.ts).
TYP_PLEX = 1
TYP_LOKAL = 2
TYP_JELLYFIN = 3
TYP_EMBY = 4

#: Nexviews Anfragezustand -> Seerrs. ``cancelled`` gibt es bei Seerr nicht:
#: Dort wird eine zurueckgezogene Anfrage geloescht. Am naechsten liegt
#: "abgelehnt", denn sie kommt nicht mehr.
_ANFRAGE = {
    RequestStatus.pending_approval: ANFRAGE_WARTET,
    RequestStatus.approved: ANFRAGE_FREIGEGEBEN,
    RequestStatus.searching: ANFRAGE_FREIGEGEBEN,
    RequestStatus.downloaded: ANFRAGE_FERTIG,
    RequestStatus.rejected: ANFRAGE_ABGELEHNT,
    RequestStatus.cancelled: ANFRAGE_ABGELEHNT,
    RequestStatus.failed: ANFRAGE_FEHLGESCHLAGEN,
    RequestStatus.deleted: ANFRAGE_FERTIG,
}

#: Derselbe Zustand, aber als Zustand des **Titels** (``media.status``).
_MEDIUM_AUS_ANFRAGE = {
    RequestStatus.pending_approval: MEDIUM_WARTET,
    RequestStatus.approved: MEDIUM_IN_ARBEIT,
    RequestStatus.searching: MEDIUM_IN_ARBEIT,
    RequestStatus.downloaded: MEDIUM_DA,
    RequestStatus.deleted: MEDIUM_GELOESCHT,
}

#: Das Abzeichen einer Kachel (``discover._status_for``) -> ``mediaInfo.status``.
_MEDIUM_AUS_ABZEICHEN = {
    "pending_approval": MEDIUM_WARTET,
    "requested": MEDIUM_IN_ARBEIT,
    "searching": MEDIUM_IN_ARBEIT,
    "downloading": MEDIUM_IN_ARBEIT,
    "monitored": MEDIUM_IN_ARBEIT,
    "wanted": MEDIUM_IN_ARBEIT,
    "partial": MEDIUM_TEILWEISE,
    "downloaded": MEDIUM_DA,
    "available": MEDIUM_DA,
    "in_library": MEDIUM_DA,
    "blocked": MEDIUM_GESPERRT,
}


def anfragestatus(status: RequestStatus) -> int:
    return _ANFRAGE.get(status, ANFRAGE_WARTET)


def medienstatus(abzeichen: str | None) -> int:
    """Ein Abzeichen in Seerrs Zahl. Unbekanntes ist ``UNKNOWN`` (1), nicht "da"."""
    if not abzeichen:
        return MEDIUM_UNBEKANNT
    return _MEDIUM_AUS_ABZEICHEN.get(abzeichen, MEDIUM_UNBEKANNT)


# --- camelCase ------------------------------------------------------------------

_UNTERSTRICH = re.compile(r"_([a-z0-9])")

#: Schluessel, die Seerr bewusst in ``snake_case`` laesst: die Laender- und
#: Sprachcodes von TMDB (``iso_3166_1``, ``iso_639_1``).
_BLEIBT = re.compile(r"^iso_\d+_\d+$")


def _kamel_wort(schluessel: str) -> str:
    if _BLEIBT.match(schluessel) or "_" not in schluessel:
        return schluessel
    return _UNTERSTRICH.sub(lambda treffer: treffer.group(1).upper(), schluessel)


def kamel(wert: Any) -> Any:
    """TMDB-Daten so umbenennen, wie Seerr sie weitergibt."""
    if isinstance(wert, dict):
        return {_kamel_wort(str(k)): kamel(v) for k, v in wert.items()}
    if isinstance(wert, list):
        return [kamel(eintrag) for eintrag in wert]
    return wert


# --- Zeitpunkte und Kennungen -------------------------------------------------


def zeit(wert: datetime | None) -> str | None:
    """ISO 8601 mit ``Z``, wie Seerr (TypeORM) es schreibt."""
    if wert is None:
        return None
    if wert.tzinfo is None:
        wert = wert.replace(tzinfo=UTC)
    return wert.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def server_id(kennung: str | None) -> int | None:
    """Eine Fassung als Seerrs ganzzahlige Server-Nummer.

    ⚠️ **Aus der Kennung gerechnet, nicht durchgezaehlt.** Ein Client wie
    Silo speichert die Nummer in seiner Einrichtung. Eine laufende Nummer
    verschoebe sich, sobald eine Fassung dazukommt oder wegfaellt, und die
    Anfragen landeten still in der falschen. CRC32 bleibt fuer dieselbe
    Kennung immer gleich; das obere Bit faellt weg, damit die Zahl in jedem
    Client als positive 32-Bit-Zahl durchgeht.
    """
    if not kennung:
        return None
    return zlib.crc32(kennung.encode("utf-8")) & 0x7FFFFFFF


# --- Nutzer -------------------------------------------------------------------


def _medienserverkonto(user: User) -> tuple[str | None, str | None, str | None]:
    """(Art, Kennung, Name) des Jellyfin- oder Emby-Kontos, sonst Plex, sonst nichts."""
    konten = {konto.provider: konto for konto in getattr(user, "mediaserver_accounts", []) or []}
    for art in ("jellyfin", "emby", "plex"):
        konto = konten.get(art)
        if konto is not None:
            return art, konto.account_id, konto.username
    return None, None, None


def rechte(user: User, *, darf_4k: bool = False) -> int:
    """Nexviews Rolle und Haken als Seerrs Rechte-Bitfeld.

    Ein Administrator bekommt ``ADMIN`` (2), das schliesst bei Seerr alles
    ein. Sonst nur, was Nexview dem Konto wirklich erlaubt. ⚠️ Die Bits sind
    eine **Auskunft** fuer den Client, keine Erlaubnis: Ob eine Anfrage
    durchgeht, entscheidet ``requests_service.create_request`` mit denselben
    Regeln wie in der Oberflaeche.
    """
    if user.role == Role.admin:
        return RECHT_ADMIN
    bits = RECHT_ANFRAGEN | RECHT_FILM | RECHT_SERIE
    if user.can_approve:
        bits |= RECHT_ANFRAGEN_VERWALTEN
    film = user.auto_approve_offen(MediaType.movie)
    serie = user.auto_approve_offen(MediaType.tv)
    if film and serie:
        bits |= RECHT_AUTO
    if film:
        bits |= RECHT_AUTO_FILM
    if serie:
        bits |= RECHT_AUTO_SERIE
    if darf_4k:
        bits |= RECHT_4K
    return bits


def nutzer(user: User, *, darf_4k: bool = False, anfragen: int = 0) -> dict[str, Any]:
    """Ein Konto in Seerrs Form.

    ⚠️ **``email`` ist immer leer, ``username`` ist immer leer.** Beides waere
    ein Weg, ein fremdes Konto zu treffen: Silos Plugin ordnet seine Nutzer
    ueber die Mailadresse zu, Litefin faellt auf ``username`` und
    ``jellyfinUsername`` zurueck (beide bleiben leer).
    Eine Mailadresse oder ein Name, den jemand in Silo oder Jellyfin selbst
    eintragen kann, entscheidet dann, auf wessen Konto eine Anfrage laeuft
    (Bauplan "Anmeldung", Regel "nie ueber die Mailadresse zuordnen").
    Zugeordnet wird nur ueber ``jellyfinUserId``: Die stammt aus Nexviews
    bestaetigter Verknuepfung mit dem Medienserver, nicht aus einem Feld, das
    jemand frei beschreibt.
    """
    art, kennung, name = _medienserverkonto(user)
    typ = {"jellyfin": TYP_JELLYFIN, "emby": TYP_EMBY, "plex": TYP_PLEX}.get(art or "", TYP_LOKAL)
    return {
        "id": user.id,
        "displayName": user.display_name or user.username,
        "email": "",
        "username": None,
        "userType": typ,
        "plexUsername": name if art == "plex" else None,
        # ⚠️ Kein ``jellyfinUsername``: Litefin vergleicht ihn mit dem
        # Anmeldenamen, sobald die Kennung nicht passt, und noch bevor es den
        # naechsten Eintrag ansieht. Ein Konto mit gleichnamigem Emby- oder
        # altem Jellyfin-Namen bekaeme dann die Anfragen eines Fremden.
        "jellyfinUsername": None,
        # Nur aus einer Jellyfin-Verknuepfung: Eine Emby-Kennung ist eine
        # andere Zahl auf einem anderen Server.
        "jellyfinUserId": kennung if art == "jellyfin" else None,
        "permissions": rechte(user, darf_4k=darf_4k),
        "avatar": "",
        "requestCount": anfragen,
        "createdAt": zeit(user.created_at),
        "updatedAt": zeit(user.created_at),
        "warnings": [],
    }


# --- Anfragen -----------------------------------------------------------------


def _laeuft(anfrage: MediaRequest) -> list[dict[str, Any]]:
    """``downloadStatus`` aus Nexviews Fortschritt.

    Nexview kennt den Fortschritt in Prozent, nicht in Bytes. Groesse und Rest
    stehen deshalb als 100 und Rest-Prozent da: Ein Client, der ``sizeLeft /
    size`` rechnet (Silo), bekommt den richtigen Anteil. Byte-Angaben daraus
    abzulesen waere falsch, und das sagt dieser Absatz, nicht die Zahl.
    """
    if anfrage.status != RequestStatus.searching or anfrage.laedt_fortschritt is None:
        return []
    prozent = max(0, min(100, int(anfrage.laedt_fortschritt)))
    return [
        {
            "size": 100,
            "sizeLeft": 100 - prozent,
            "status": "downloading",
            "timeLeft": "",
            "estimatedCompletionTime": "",
            "title": anfrage.title,
            "downloadId": f"nexview-{anfrage.id}",
            "mediaType": anfrage.media_type.value,
            "externalId": anfrage.id,
        }
    ]


def medium_aus_anfrage(
    anfrage: MediaRequest, *, ist_4k: bool, anfragen: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """``media`` einer Anfrage: der Zustand des Titels aus Sicht dieser Anfrage."""
    status = _MEDIUM_AUS_ANFRAGE.get(anfrage.status, MEDIUM_UNBEKANNT)
    laeuft = _laeuft(anfrage)
    medium: dict[str, Any] = {
        "id": anfrage.tmdb_id,
        "mediaType": anfrage.media_type.value,
        "tmdbId": anfrage.tmdb_id,
        "tvdbId": anfrage.tvdb_id,
        "imdbId": None,
        "status": MEDIUM_UNBEKANNT if ist_4k else status,
        "status4k": status if ist_4k else MEDIUM_UNBEKANNT,
        "createdAt": zeit(anfrage.requested_at),
        "updatedAt": zeit(anfrage.completed_at or anfrage.requested_at),
        "mediaAddedAt": zeit(anfrage.completed_at) if anfrage.status == RequestStatus.downloaded else None,
        "downloadStatus": [] if ist_4k else laeuft,
        "downloadStatus4k": laeuft if ist_4k else [],
        "seasons": [],
    }
    if anfragen is not None:
        medium["requests"] = anfragen
    return medium


def anfrage(
    zeile: MediaRequest,
    *,
    ist_4k: bool,
    mit_medium: bool = True,
    staffeln: list[MediaRequest] | None = None,
) -> dict[str, Any]:
    """Eine Anfrage in Seerrs Form (``MediaRequest``).

    ``staffeln`` sind die Nexview-Anfragen, die zusammen **eine** Seerr-Anfrage
    bilden: Seerr fuehrt mehrere Staffeln in einer Anfrage, Nexview legt je
    Staffel eine an. Ohne Angabe steht nur die eigene Staffel darin.
    """
    teile = staffeln if staffeln is not None else [zeile]
    staffelliste = [
        {
            "id": teil.id,
            "seasonNumber": teil.season,
            "status": anfragestatus(teil.status),
            "createdAt": zeit(teil.requested_at),
            "updatedAt": zeit(teil.completed_at or teil.requested_at),
        }
        for teil in teile
        if teil.season is not None
    ]
    daten: dict[str, Any] = {
        "id": zeile.id,
        "status": anfragestatus(zeile.status),
        "createdAt": zeit(zeile.requested_at),
        "updatedAt": zeit(zeile.approved_at or zeile.completed_at or zeile.requested_at),
        "type": zeile.media_type.value,
        "is4k": ist_4k,
        "serverId": server_id(zeile.fassung_kennung),
        "profileId": zeile.quality_profile_id,
        "rootFolder": zeile.root_folder_path,
        "languageProfileId": None,
        "tags": [],
        "isAutoRequest": False,
        "ignoreQuota": False,
        "seasonCount": len(staffelliste),
        "seasons": staffelliste,
        "requestedBy": nutzer(zeile.user),
        "modifiedBy": nutzer(zeile.approver) if zeile.approver is not None else None,
    }
    if mit_medium:
        daten["media"] = medium_aus_anfrage(zeile, ist_4k=ist_4k)
    return daten


# --- Seiten -------------------------------------------------------------------


def seite(ergebnisse: list[Any], *, seite_nr: int, seiten: int, gesamt: int) -> dict[str, Any]:
    """Die Seitenform der TMDB-Listen: ``page``, ``totalPages``, ``totalResults``."""
    return {
        "page": seite_nr,
        "totalPages": seiten,
        "totalResults": gesamt,
        "results": ergebnisse,
    }


def seitenangabe(ergebnisse: list[Any], *, gesamt: int, take: int, skip: int) -> dict[str, Any]:
    """Die Seitenform der eigenen Listen (Anfragen, Nutzer, Medien): ``pageInfo``."""
    groesse = max(1, take)
    return {
        "pageInfo": {
            "pages": (gesamt + groesse - 1) // groesse,
            "pageSize": groesse,
            "results": gesamt,
            "page": skip // groesse + 1,
        },
        "results": ergebnisse,
    }
