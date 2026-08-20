"""Die zweite Achse: Wie steht ein Titel in der 4K-Instanz da?

Bewusst ein eigenes, zusaetzliches Feld (``status_uhd``) statt neuer Werte in
``status``: Die Hauptachse bleibt damit unveraendert, und ``None`` heisst
schlicht "diese Achse gibt es hier nicht" - der Normalfall fuer alle, die keine
zweite Instanz betreiben.

Zwei Tore muessen offen sein, bevor hier ueberhaupt etwas passiert: Es muss eine
4K-Instanz geben, **und** der Benutzer muss sie nutzen duerfen. Sonst wird keine
einzige zusaetzliche Abfrage gestellt.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import MediaType, QualityTier, User
from ..schemas_media import MediaItem
from . import library, mediaserver_library, requests_service
from .settings_service import AppSettings


async def anreichern(
    db: Session,
    settings: AppSettings,
    media_type: str,
    items: list[MediaItem],
    user: User,
) -> None:
    """``status_uhd`` auf den Karten setzen - an Ort und Stelle.

    Drei Quellen, in dieser Reihenfolge: die eigene Anfrage, die 4K-Instanz,
    und - neu - der Media-Server. Der dritte Schritt ist der entscheidende:
    Wer einen Film aus Radarr entfernt, sobald die Wunschqualitaet erreicht
    ist, hat ihn weiterhin in Plex. Ohne diesen Rueckfall stand er dann als
    "4K nicht angefragt" da, obwohl er in 4K vorliegt.

    Ist die 4K-Instanz gerade nicht erreichbar, bleibt das Feld leer - und
    zwar **ohne** Warnhinweis: Die Standard-Fassung ist ja in Ordnung, und
    eine Warnung wegen einer Zusatzstufe waere nur Laerm.
    """
    if not items:
        return
    if not settings.arr_configured(media_type, "uhd"):
        return
    if not user.may_request_uhd(MediaType(media_type)):
        return

    eigene = requests_service.badges_for(
        db, MediaType(media_type), [eintrag.tmdb_id for eintrag in items], QualityTier.uhd
    )

    # Mit zurueckgesetztem Zustand abgleichen: Sonst faende sich der Status der
    # *Standard*-Fassung in der Antwort wieder, und ein Film, der nur in 1080p
    # vorliegt, saehe faelschlich auch in 4K als vorhanden aus.
    kopien = [eintrag.model_copy(update={"status": "not_requested"}) for eintrag in items]
    ergebnis = await library.apply_status(settings, media_type, kopien, "uhd")
    in_bibliothek = {
        eintrag.tmdb_id: eintrag.status
        for eintrag in ergebnis.items
        if eintrag.status != "not_requested"
    }

    # Was Radarr/Sonarr nicht (mehr) kennt, kann trotzdem im Media-Server
    # liegen - und nur dort steht, in welcher Aufloesung.
    offen = [
        eintrag
        for eintrag in items
        if eintrag.tmdb_id not in in_bibliothek and eintrag.tmdb_id not in eigene
    ]
    im_server = mediaserver_library.vorhandene_kennungen(
        db, MediaType(media_type), offen, tier="uhd"
    )

    for eintrag in items:
        eigen = eigene.get(eintrag.tmdb_id)
        vorhanden = in_bibliothek.get(eintrag.tmdb_id)
        # Wie bei der Hauptachse: "geladen" ist eine Aussage ueber die
        # Bibliothek. Bestaetigt die 4K-Instanz sie nicht mehr, gilt sie nur
        # noch, wenn der Media-Server die Datei bestaetigt.
        if eigen == "downloaded" and vorhanden is None:
            eigen = None
        if eintrag.tmdb_id in im_server:
            vorhanden = vorhanden or "in_library"
        eintrag.status_uhd = eigen or vorhanden or "not_requested"
