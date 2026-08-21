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

from ..models import MediaType, QualityTier, Role, User
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
    und - als Rueckfall - der Media-Server. Der dritte Schritt ist der
    heikelste: Wer einen Film aus Radarr entfernt, sobald die Wunschqualitaet
    erreicht ist, hat ihn weiterhin in Plex. Ohne diesen Rueckfall stand er
    dann als "4K nicht angefragt" da, obwohl er in 4K vorliegt. Was der
    Rueckfall dabei **nicht** darf, steht in ``_echte_zweitfassungen``.

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

    # Der Ablageort geht **nur** an Administratoren - dieselbe Regel wie auf
    # der Hauptachse, und ebenfalls hier entschieden statt in der Oberflaeche:
    # Ausblenden hiesse, ihn trotzdem ausgeliefert zu haben.
    fuer_admin = user.role == Role.admin

    # Mit zurueckgesetztem Zustand abgleichen: Sonst faende sich der Status der
    # *Standard*-Fassung in der Antwort wieder, und ein Film, der nur in 1080p
    # vorliegt, saehe faelschlich auch in 4K als vorhanden aus.
    kopien = [eintrag.model_copy(update={"status": "not_requested"}) for eintrag in items]
    ergebnis = await library.apply_status(
        settings, media_type, kopien, "uhd", mit_pfad=fuer_admin
    )
    in_bibliothek = {
        eintrag.tmdb_id: eintrag.status
        for eintrag in ergebnis.items
        if eintrag.status != "not_requested"
    }
    # ⚠️ Nur, wo die 4K-Instanz den Titel wirklich kennt. Die Kopien tragen den
    # Pfad der *Standard*-Fassung mit, und ``apply_status`` ueberschreibt ihn
    # nur bei einem Treffer - ungefiltert stuende hier bei jedem Film der
    # 1080p-Pfad als vermeintlicher 4K-Ablageort.
    pfade = (
        {
            eintrag.tmdb_id: eintrag.path
            for eintrag in ergebnis.items
            if eintrag.tmdb_id in in_bibliothek and eintrag.path
        }
        if fuer_admin
        else {}
    )

    # Was Radarr/Sonarr nicht (mehr) kennt, kann trotzdem im Media-Server
    # liegen - und nur dort steht, in welcher Aufloesung.
    offen = [
        eintrag
        for eintrag in items
        if eintrag.tmdb_id not in in_bibliothek and eintrag.tmdb_id not in eigene
    ]
    gemeldet = mediaserver_library.vorhandene_kennungen(
        db, MediaType(media_type), offen, tier="uhd"
    )
    im_server: set[int] = set()
    # Titel, deren 4K-Datei in der **Standard**-Instanz liegt. Sie zaehlen
    # nicht als eigene Fassung (siehe ``echte_uhd_kennungen``) - aber die
    # Anfragemaske soll darauf hinweisen, bevor jemand eine zweite anlegt.
    verdeckt: set[int] = set()
    if gemeldet:
        im_server = mediaserver_library.echte_uhd_kennungen(
            db,
            MediaType(media_type),
            offen,
            in_standard_instanz=await _in_standard_instanz(settings, media_type, offen),
        )
        verdeckt = gemeldet - im_server

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

        # Nur setzen, wo das Ziel das Feld ueberhaupt kennt: Durch diese
        # Funktion laufen auch Filmografie-Eintraege, und Pydantic laesst kein
        # undeklariertes Feld zu - genau daran ist die Personenseite schon
        # einmal mit 500 gescheitert.
        if fuer_admin and hasattr(eintrag, "path_uhd"):
            eintrag.path_uhd = pfade.get(eintrag.tmdb_id)
        if eintrag.tmdb_id in verdeckt and hasattr(eintrag, "uhd_in_standard"):
            eintrag.uhd_in_standard = True


async def _in_standard_instanz(
    settings: AppSettings, media_type: str, items: list[MediaItem]
) -> set[int]:
    """Was fuehrt die **Standard**-Instanz mit Datei?

    Die eine Angabe, die ``mediaserver_library.echte_uhd_kennungen`` von aussen
    braucht: Nur damit laesst sich eine 4K-Datei, die im normalen Radarr liegt,
    von einer echten Zweitfassung unterscheiden.

    Kostet keine zusaetzliche Abfrage - ``movie_library`` und ``series_library``
    liegen zu diesem Zeitpunkt bereits zwischengespeichert vor, weil die
    Hauptachse sie eben benutzt hat.
    """
    if not items:
        return set()
    kopien = [
        eintrag.model_copy(update={"status": "not_requested"}) for eintrag in items
    ]
    ergebnis = await library.apply_status(settings, media_type, kopien, "standard")
    return {
        eintrag.tmdb_id for eintrag in ergebnis.items if eintrag.status == "downloaded"
    }
