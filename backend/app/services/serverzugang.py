"""Den Serverzugang gleich mit erneuern, wenn sich ein Administrator neu anmeldet.

Es gibt zwei Zugaenge zu jedem Medienserver:

* den **Serverzugang** in ``media_server_connections`` - damit gleicht Nexview
  die Bibliothek ab, liest Konten und laufende Wiedergaben;
* den **persoenlichen** je Verknuepfung - damit liest es die eigene Merkliste.

⚠️ **Beim Administrator gehoeren oft beide zum selben Konto**, und dann laufen
sie auch gemeinsam ab. Gemeldet am 17.09.2026: Emby lehnte beide ab, der
Betreiber meldete sich ueber den roten Balken neu an - der Balken verschwand,
die Kachel in den Einstellungen blieb bei "Zugang abgelehnt". Aus seiner Sicht
hatte er sich gerade angemeldet. Zwei Anmeldungen fuer dasselbe Konto sind
nichts, was man jemandem erklaeren muesste, sondern ein Umweg, den Nexview
selbst gehen kann.

Erneuert wird nur, wenn **alles** davon zutrifft:

1. die Person ist Administrator in Nexview,
2. es gibt eine Serververbindung zu diesem Anbieter,
3. der Server lehnt deren Zugang gerade ab (401/403) - ein funktionierender
   Zugang wird nie ersetzt, auch nicht durch einen gleichwertigen,
4. die neue Anmeldung erreicht **denselben** Server (Maschinenkennung) und hat
   dort Verwaltungsrechte.

Scheitert irgendetwas davon, bleibt es beim persoenlichen Zugang. Das ist kein
Fehler der Anmeldung, die der Mensch gerade gemacht hat - deshalb wird nichts
davon nach oben gereicht.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crypto import encrypt
from ..models import MediaServerConnection, User
from . import mediaserver as medienserver
from .mediaserver import MediaServerError
from .settings_service import AppSettings

logger = logging.getLogger("nexview.mediaserver")

#: Holt das neue Token fuer den Serverzugang - oder ``None``, wenn das Konto
#: dafuer nicht taugt (keine Verwaltungsrechte).
TokenHolen = Callable[[], Awaitable[str | None]]


async def mit_erneuern(
    db: Session,
    settings: AppSettings,
    user: User,
    provider: str,
    token_holen: TokenHolen,
    *,
    nur_eigene: bool = False,
) -> bool:
    """Den abgelehnten Serverzugang durch eine frische Anmeldung ersetzen.

    ``nur_eigene`` verlangt, dass der Server dem Konto **gehoert** - bei Plex
    kommen ueber plex.tv auch geteilte Server in die Liste.

    Gibt zurueck, ob der Serverzugang ersetzt wurde.
    """
    if not user.is_admin:
        return False
    verbindung = db.scalar(
        select(MediaServerConnection).where(MediaServerConnection.provider == provider)
    )
    if verbindung is None:
        return False

    server = medienserver.media_server_for_setup(settings, provider)
    try:
        await server.list_server_users()
        return False  # Der Serverzugang funktioniert - nichts anfassen.
    except MediaServerError as exc:
        if exc.status_code not in (401, 403):
            # Nicht erreichbar ist nicht abgelehnt. Ein neues Token hilft
            # einem ausgeschalteten Server nicht.
            return False

    try:
        token = await token_holen()
        if not token:
            return False
        kandidaten = await server.list_servers(token)
    except MediaServerError as exc:
        logger.info(
            "Media server %r: server access not renewed alongside the personal one (%s)",
            provider,
            exc.code or exc.status_code,
        )
        return False

    if not any(
        k.machine_id == verbindung.machine_id and (k.owned or not nur_eigene)
        for k in kandidaten
    ):
        logger.info(
            "Media server %r: new sign-in reaches a different server, server access left as is",
            provider,
        )
        return False

    verbindung.token = encrypt(token)
    db.commit()
    logger.info(
        "Media server %r: rejected server access renewed with the administrator's new sign-in",
        provider,
    )
    return True
