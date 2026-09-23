"""Wie steht ein Titel in jeder Fassung da?

Frueher ``services/uhd.py``: genau eine zweite Achse, die 4K-Instanz. Seit dem
Fassungsmodell traegt jede Karte eine **Liste** (``MediaItem.fassungen``), und
``status_uhd`` ist nur noch die Ableitung daraus - die Fassung mit der Klasse
``uhd`` (Bauplan NEX-Modus, Abschnitt 2.2 und 12).

Die Hauptachse steht als erster Eintrag darin, mit demselben Zustand wie
``status``. Jede weitere Fassung kostet eigene Abfragen; sie wird nur
angefasst, wenn sie eingerichtet ist **und** der Benutzer sie anfragen darf.
Sonst wird keine einzige zusaetzliche Abfrage gestellt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models import MediaType, Role, User
from ..schemas_media import FassungAchse, MediaItem
from . import fassungen, mediaserver_library, requests_service
from .beschaffung import KLASSE_UHD, FassungInfo, get_beschaffung
from .settings_service import AppSettings


@dataclass
class _Stand:
    """Was eine Fassung ueber diese Titel sagt."""

    status: dict[int, str] = field(default_factory=dict)
    pfade: dict[int, str] = field(default_factory=dict)
    #: Titel, deren 4K-Datei in der **Standard**-Instanz liegt (nur Klasse ``uhd``).
    verdeckt: set[int] = field(default_factory=set)


async def anreichern(
    db: Session,
    settings: AppSettings,
    media_type: str,
    items: list[MediaItem],
    user: User,
) -> None:
    """``fassungen`` auf den Karten setzen - an Ort und Stelle.

    Der erste Eintrag ist die Hauptfassung; ihr Zustand ist der, der schon in
    ``status`` steht. Danach jede weitere eingerichtete Fassung, die dieser
    Benutzer anfragen darf.

    Ist eine dieser Fassungen gerade nicht erreichbar, bleibt ihr Zustand
    ``not_requested`` - und zwar **ohne** Warnhinweis: Die Hauptfassung ist ja
    in Ordnung, und eine Warnung wegen einer Zusatzfassung waere nur Laerm.
    """
    haupt_kennung = fassungen.hauptkennung(media_type)
    # Ohne Hauptfassung (NEX-Betrieb, nichts gelesen) bekommt keine Karte eine
    # Achse - schon gar nicht eine Arr-Fassung, die es hier nicht gibt.
    if not items or haupt_kennung is None:
        return

    haupt = fassungen.info(settings, haupt_kennung)
    weitere = [
        eintrag
        for eintrag in settings.fassungen_fuer(media_type)
        if eintrag.kennung != haupt.kennung
        and fassungen.darf_anfragen(db, user, eintrag.kennung)
    ]
    staende = {
        eintrag.kennung: await _stand(db, settings, media_type, items, user, eintrag)
        for eintrag in weitere
    }

    # Die alten 4K-Felder sind eine Ableitung: die erste Zusatzfassung der
    # Klasse ``uhd``. Sie bleiben, weil ``/api/v1`` sie zusagt.
    vierk = next((eintrag for eintrag in weitere if eintrag.klasse == KLASSE_UHD), None)

    for eintrag in items:
        if "fassungen" not in type(eintrag).model_fields:
            # Durch diese Funktion laufen auch Formen, die die Liste nicht
            # kennen - Pydantic laesst kein undeklariertes Feld zu.
            continue
        achsen = [_achse(haupt, eintrag.status, haupt=True)]
        for zusatz in weitere:
            achsen.append(
                _achse(zusatz, staende[zusatz.kennung].status.get(eintrag.tmdb_id, "not_requested"))
            )
        eintrag.fassungen = achsen

        if vierk is None:
            continue
        stand = staende[vierk.kennung]
        eintrag.status_uhd = stand.status.get(eintrag.tmdb_id, "not_requested")
        if hasattr(eintrag, "path_uhd"):
            eintrag.path_uhd = stand.pfade.get(eintrag.tmdb_id)
        if eintrag.tmdb_id in stand.verdeckt and hasattr(eintrag, "uhd_in_standard"):
            eintrag.uhd_in_standard = True


def _achse(fassung: FassungInfo, status: str, *, haupt: bool = False) -> FassungAchse:
    return FassungAchse(
        kennung=fassung.kennung,
        name=fassung.name,
        klasse=fassung.klasse,
        quelle=fassung.quelle,
        haupt=haupt,
        status=status,
    )


async def _stand(
    db: Session,
    settings: AppSettings,
    media_type: str,
    items: list[MediaItem],
    user: User,
    fassung: FassungInfo,
) -> _Stand:
    """Drei Quellen, in dieser Reihenfolge: eigene Anfrage, Beschaffung, Media-Server.

    Der dritte Schritt ist der heikelste und gilt nur fuer die Klasse ``uhd``:
    Wer einen Film aus Radarr entfernt, sobald die Wunschqualitaet erreicht
    ist, hat ihn weiterhin in Plex. Ohne diesen Rueckfall stand er dann als
    "4K nicht angefragt" da, obwohl er in 4K vorliegt. Was der Rueckfall dabei
    **nicht** darf, steht in ``mediaserver_library.echte_uhd_kennungen``.
    """
    eigene = requests_service.badges_for(
        db, MediaType(media_type), [eintrag.tmdb_id for eintrag in items], fassung.kennung
    )

    # Der Ablageort geht **nur** an Administratoren - dieselbe Regel wie auf
    # der Hauptachse, und ebenfalls hier entschieden statt in der Oberflaeche:
    # Ausblenden hiesse, ihn trotzdem ausgeliefert zu haben.
    fuer_admin = user.role == Role.admin

    # Mit zurueckgesetztem Zustand abgleichen: Sonst faende sich der Status der
    # *Hauptfassung* in der Antwort wieder, und ein Film, der nur in 1080p
    # vorliegt, saehe faelschlich auch in 4K als vorhanden aus.
    kopien = [eintrag.model_copy(update={"status": "not_requested"}) for eintrag in items]
    ergebnis = await get_beschaffung(settings).status_setzen(
        media_type,
        kopien,
        fassungen.stufe(fassung.kennung),
        fassung=fassung.kennung,
        mit_pfad=fuer_admin,
    )
    in_bibliothek = {
        eintrag.tmdb_id: eintrag.status
        for eintrag in ergebnis.items
        if eintrag.status != "not_requested"
    }
    # ⚠️ Nur, wo die Fassung den Titel wirklich kennt. Die Kopien tragen den
    # Pfad der *Hauptfassung* mit, und ``apply_status`` ueberschreibt ihn
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

    im_server: set[int] = set()
    verdeckt: set[int] = set()
    if fassung.klasse == KLASSE_UHD:
        # Was Radarr/Sonarr nicht (mehr) kennt, kann trotzdem im Media-Server
        # liegen - und nur dort steht, in welcher Aufloesung. Eine Fassung
        # ohne Klasse (etwa eine Sprachfassung) laesst sich dort nicht
        # wiedererkennen; fuer die gibt es diesen Rueckfall nicht.
        offen = [
            eintrag
            for eintrag in items
            if eintrag.tmdb_id not in in_bibliothek and eintrag.tmdb_id not in eigene
        ]
        gemeldet = mediaserver_library.vorhandene_kennungen(
            db, MediaType(media_type), offen, tier="uhd"
        )
        if gemeldet:
            im_server = mediaserver_library.echte_uhd_kennungen(
                db,
                MediaType(media_type),
                offen,
                in_standard_instanz=await _in_hauptfassung(settings, media_type, offen),
            )
            # Titel, deren 4K-Datei in der **Standard**-Instanz liegt. Sie
            # zaehlen nicht als eigene Fassung - aber die Anfragemaske soll
            # darauf hinweisen, bevor jemand eine zweite anlegt.
            verdeckt = gemeldet - im_server

    stand = _Stand(pfade=pfade, verdeckt=verdeckt)
    for eintrag in items:
        eigen = eigene.get(eintrag.tmdb_id)
        vorhanden = in_bibliothek.get(eintrag.tmdb_id)
        # Wie bei der Hauptachse: "geladen" ist eine Aussage ueber die
        # Bibliothek. Bestaetigt die Fassung sie nicht mehr, gilt sie nur
        # noch, wenn der Media-Server die Datei bestaetigt.
        if eigen == "downloaded" and vorhanden is None:
            eigen = None
        if eintrag.tmdb_id in im_server:
            vorhanden = vorhanden or "in_library"
        stand.status[eintrag.tmdb_id] = eigen or vorhanden or "not_requested"
    return stand


async def _in_hauptfassung(
    settings: AppSettings, media_type: str, items: list[MediaItem]
) -> set[int]:
    """Was fuehrt die **Hauptfassung** mit Datei?

    Die eine Angabe, die ``mediaserver_library.echte_uhd_kennungen`` von aussen
    braucht: Nur damit laesst sich eine 4K-Datei, die im normalen Radarr liegt,
    von einer echten Zweitfassung unterscheiden.

    Kostet keine zusaetzliche Abfrage - der Bestand liegt zu diesem Zeitpunkt
    bereits zwischengespeichert vor, weil die Hauptachse ihn eben benutzt hat.
    """
    haupt = fassungen.hauptkennung(media_type)
    if not items or haupt is None:
        return set()
    kopien = [eintrag.model_copy(update={"status": "not_requested"}) for eintrag in items]
    ergebnis = await get_beschaffung(settings).status_setzen(
        media_type,
        kopien,
        fassungen.stufe(haupt),
        fassung=haupt,
    )
    return {eintrag.tmdb_id for eintrag in ergebnis.items if eintrag.status == "downloaded"}
