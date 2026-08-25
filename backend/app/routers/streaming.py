"""Die eigenen Streaming-Abos - Auswahl und Katalog.

Grundlage fuer den Hinweis "das laeuft schon in deinem Abo" beim Anfragen.
Warum eine handverlesene Markenliste und warum je Marke mehrere TMDB-Kennungen
stehen: siehe ``services/streaming.py``.

Die **Region** gehoert mit in diese Antwort, obwohl sie am Benutzer haengt und
anderswo gesetzt wird. Ohne sie ist die Auswahl sinnlos - sie entscheidet, ob
WOW und RTL+ zur Wahl stehen oder Hulu und Peacock. Und weil der
Einrichtungsassistent nie nach der Region fragt, hat die Mehrheit die Vorgabe
des Betreibers geerbt, ohne davon zu wissen. Deshalb sagt die Antwort
ausdruecklich, ob die Region selbst gewaehlt wurde.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from ..deps import CurrentUser, DbSession
from ..models import StreamingService, User
from ..services import streaming
from ..services.settings_service import for_user, load_settings
from ..services.tmdb import TmdbError

# Kinderkonten haben keine eigenen Abos - sie gucken ueber die ihrer Eltern.
# Ausgesperrt werden sie bei der Registrierung in ``main.py`` (NUR_ERWACHSENE),
# wie bei jedem anderen Bereich, der Kindern verschlossen ist.
router = APIRouter(prefix="/api/streaming", tags=["streaming"])


class DienstOut(BaseModel):
    slug: str
    name: str
    logo_url: str | None


class UebersichtOut(BaseModel):
    """Alles, was der Profilreiter braucht - in einem Aufruf."""

    region: str
    region_selbst_gewaehlt: bool
    dienste: list[DienstOut]
    meine: list[str]


class AuswahlIn(BaseModel):
    slugs: list[str] = Field(default_factory=list, max_length=60)


def _meine_slugs(db: DbSession, user: User) -> list[str]:
    return list(
        db.scalars(
            select(StreamingService.slug)
            .where(StreamingService.user_id == user.id)
            .order_by(StreamingService.slug)
        )
    )


@router.get("", response_model=UebersichtOut)
async def uebersicht(user: CurrentUser, db: DbSession) -> UebersichtOut:
    """Katalog der Region, die eigene Auswahl und woher die Region stammt."""
    settings = for_user(load_settings(db), user)

    try:
        dienste = await streaming.katalog(db, settings, settings.default_region)
    except TmdbError:
        # Ohne TMDB gibt es keine Logos und keine Namen. Das ist kein Grund,
        # den ganzen Reiter zu verweigern - die bereits getroffene Auswahl
        # bleibt sichtbar und aenderbar.
        dienste = []

    return UebersichtOut(
        region=settings.default_region,
        region_selbst_gewaehlt=bool(user.discover_region),
        dienste=[
            DienstOut(slug=d.slug, name=d.name, logo_url=d.logo_url) for d in dienste
        ],
        meine=_meine_slugs(db, user),
    )


@router.put("", response_model=UebersichtOut)
async def auswahl_setzen(
    payload: AuswahlIn, user: CurrentUser, db: DbSession
) -> UebersichtOut:
    """Die eigene Auswahl ersetzen - ganz, nicht einzeln.

    Ein Haken mehr oder weniger ist derselbe Vorgang wie "alle abwaehlen";
    einzelne Endpunkte zum Hinzufuegen und Entfernen waeren zwei Wege zu
    demselben Ziel und muessten beide dieselbe Pruefung tragen.
    """
    gewuenscht = {slug.strip() for slug in payload.slugs if slug.strip()}

    unbekannt = sorted(slug for slug in gewuenscht if not streaming.ist_bekannt(slug))
    if unbekannt:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unbekannter Dienst: {', '.join(unbekannt)}",
        )

    # Ein Dienst, den es in der eigenen Region nicht gibt, bleibt absichtlich
    # erlaubt: Wer die Region wechselt, soll seine Haken wiederfinden, statt
    # sie beim Wechsel zu verlieren.
    vorhanden = set(_meine_slugs(db, user))

    for slug in vorhanden - gewuenscht:
        db.execute(
            delete(StreamingService).where(
                StreamingService.user_id == user.id, StreamingService.slug == slug
            )
        )
    for slug in gewuenscht - vorhanden:
        db.add(StreamingService(user_id=user.id, slug=slug))

    db.commit()
    return await uebersicht(user, db)
