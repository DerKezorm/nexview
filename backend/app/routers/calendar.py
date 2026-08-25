"""Der Erscheinungs-Kalender.

Eigener Router, weil der Kalender als einzige Ansicht Radarr, Sonarr und TMDB
gleichzeitig befragt und daraus eine Zeitleiste baut.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from ..deps import CurrentUser, DbSession
from ..models import MediaType
from ..schemas_calendar import CalendarEntry, CalendarResult
from ..services import blocklist, calendar as calendar_service
from ..services import library, mediaserver_library, mediaserver_watched, requests_service
from ..services import uhd
from ..services.settings_service import for_user, load_settings
from .. import meldungen

router = APIRouter(prefix="/api", tags=["calendar"])

# Ein ISO-Datum wie 2026-08-16 - dasselbe Muster wie bei den Entdecken-Filtern.
DatePattern = r"^\d{4}-\d{2}-\d{2}$"

DateParam = Annotated[str | None, Query(pattern=DatePattern)]


def _zeitraum(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    """Zeitraum bestimmen und pruefen.

    Ohne Angabe rechnet ihn der Server. Das ist Absicht: Im Browser entstuende
    er aus einer Funktion, die ueber UTC rechnet - ein Aufruf spaetabends
    lieferte dann schon den naechsten Tag, und die Zeitleiste begaenne einen
    Tag zu frueh.
    """
    if date_from is None and date_to is None:
        return calendar_service.standard_zeitraum()

    vorgabe_von, vorgabe_bis = calendar_service.standard_zeitraum()
    von = date_from or vorgabe_von
    bis = date_to or vorgabe_bis

    if von > bis:
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "date_range_reversed",
                "Das Startdatum liegt hinter dem Enddatum.",
            ),
        )

    spanne = (date.fromisoformat(bis) - date.fromisoformat(von)).days
    if spanne > calendar_service.MAX_TAGE:
        raise HTTPException(
            status_code=422,
            detail=f"Der Zeitraum darf höchstens {calendar_service.MAX_TAGE} Tage umfassen.",
        )

    return von, bis


async def _zustaende(db, settings, user, eintraege: list[CalendarEntry]) -> None:
    """Zustand, Sperre und "schon gesehen" ergaenzen - an Ort und Stelle.

    Der Abgleich gegen Radarr/Sonarr laeuft **nur fuer die Neuerscheinungen**.
    Die eigenen Titel kennen ihren Zustand bereits genauer: Sonarr sagt dort je
    Folge, ob die Datei vorliegt, waehrend der Abgleich nur weiss, ob die Serie
    ueberhaupt schon Folgen hat - er wuerde die genauere Auskunft ueberschreiben
    und aus "Folge fehlt" ein "bereits geladen" machen.

    Umgekehrt gilt aber: Ohne diesen Abgleich stuende ein laengst geladener
    Film unter "Neuerscheinungen" als "nicht angefragt" - mit Einkaufswagen,
    obwohl er auf der Platte liegt.
    """
    for art in (MediaType.movie, MediaType.tv):
        betroffen = [e for e in eintraege if e.media_type == art and e.tmdb_id]
        if not betroffen:
            continue
        kennungen = [e.tmdb_id for e in betroffen if e.tmdb_id is not None]

        fremde = [e for e in betroffen if e.source == "neu"]
        if fremde:
            try:
                abgeglichen = await library.apply_status(settings, art.value, list(fremde))
                for ziel, quelle in zip(fremde, abgeglichen.items, strict=True):
                    ziel.status = quelle.status
            except Exception:  # noqa: BLE001 - Badges sind Beiwerk, keine Bedingung
                pass

        eigene = requests_service.badges_for(db, art, kennungen)
        gesperrt = blocklist.gesperrte_kennungen(db, art, kennungen)
        gesehen = mediaserver_watched.gesehene_kennungen(db, user.id, art, kennungen)
        # Was im Media-Server liegt, Radarr/Sonarr aber nicht kennt.
        im_server = mediaserver_library.vorhandene_kennungen(
            db, art, [e for e in betroffen if e.status == "not_requested"]
        )

        for eintrag in betroffen:
            # Eine vorhandene Datei gewinnt gegen den eigenen Anfragezustand.
            if eintrag.status == "not_requested" and eintrag.tmdb_id in eigene:
                eintrag.status = eigene[eintrag.tmdb_id]
            elif eintrag.status == "not_requested" and eintrag.tmdb_id in im_server:
                eintrag.status = "in_library"
            # Die Sperre gewinnt gegen alles - wie in allen anderen Listen.
            if eintrag.tmdb_id in gesperrt:
                eintrag.status = blocklist.BADGE
                eintrag.missing = False
            if eintrag.tmdb_id in gesehen:
                eintrag.watched = True

        # Zweite Achse zuletzt - sie ergaenzt nur, sie ersetzt nichts. Genau
        # wie in allen anderen Listen; dass sie hier gefehlt hat, war eine
        # Luecke und keine Entscheidung: Der Kalender zeigte als einzige Liste
        # kein 4K-Abzeichen.
        try:
            await uhd.anreichern(db, settings, art.value, betroffen, user)
        except Exception:  # noqa: BLE001 - Badges sind Beiwerk, keine Bedingung
            pass


@router.get("/calendar", response_model=CalendarResult)
async def kalender(
    user: CurrentUser,
    db: DbSession,
    date_from: DateParam = None,
    date_to: DateParam = None,
    sources: Annotated[Literal["all", "mine", "new"], Query()] = "all",
    date_type: Annotated[Literal["digital", "kino"], Query()] = "digital",
    # "studios" und "known" bleiben gueltig - sie sind die beiden Haelften,
    # aus denen "sinnvoll" besteht, und alte Lesezeichen sollen weiter gehen.
    noise: Annotated[
        Literal["sinnvoll", "studios", "known", "none"], Query()
    ] = "sinnvoll",
) -> CalendarResult:
    """Was im Zeitraum erscheint - eigener Bestand und Neuerscheinungen.

    Es gibt bewusst **keinen** Regions-Parameter: Die Region kommt aus dem
    Profil des Benutzers. Die Pruef-Region der Altersbeschraenkung bleibt davon
    unberuehrt, sonst liesse sich die Sperre durch einen Laenderwechsel
    aushebeln.
    """
    von, bis = _zeitraum(date_from, date_to)
    settings = for_user(load_settings(db), user)

    ergebnis = await calendar_service.kalender(
        db,
        settings,
        von=von,
        bis=bis,
        quellen=sources,
        datumsart=date_type,
        schaerfe=noise,
    )

    await _zustaende(
        db, settings, user, [eintrag for tag in ergebnis.days for eintrag in tag.entries]
    )
    return ergebnis
