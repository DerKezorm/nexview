"""Die Flaeche der eigenen iPhone-App: ``/api/app/1``.

⚠️ **Ein Versprechen, aber nur an die eigene App.** Die App im App Store und die
selbst betriebenen Server werden nie gleichzeitig aktualisiert. Deshalb spricht
die App nicht mit dem Innenteil unter ``/api/…`` (der aendert sich ohne Zusage,
siehe ``v1.py``), und auch nicht mit ``/api/v1``: Dort gehoert die Verwaltung
bewusst nicht hin, und die App braucht Freigeben, Wuensche und spaeter den
Betrieb. Solange ``app/1`` in der Adresse steht, bricht hier nichts; muss etwas
brechen, entsteht ``/api/app/2`` daneben, und aeltere Apps laufen weiter.

Die Waechter stehen in ``tests/test_app_zusage.py`` und
``tests/app1_abdruck.json`` - dasselbe Verfahren wie bei ``/api/v1``.

⚠️ **Die Handler sind dieselben wie im Browser**, ein zweites Mal registriert.
Rechte, Kontingente, Hausordnung und Regeln gelten dadurch genau wie dort.
Antwortform, Statuscode und Fehlerangaben werden aus der Originalroute
uebernommen (``_wie``), nicht abgeschrieben - sonst liefe die Zweitschrift beim
naechsten Umbau still auseinander.

⚠️ **Zwei Router, zwei Wachen** (``main.py``):

* ``router`` haengt mit ``NUR_ERWACHSENE``. Ohne das kaeme ein Kinderschluessel
  an jeder Adresse vorbei, die ihren Schutz beim Einhaengen traegt statt am
  Handler - dieselbe Falle, in die ``v1`` beim ersten Anlauf lief.
* ``kids_router`` traegt die Kinderadressen. Ihre Handler verlangen selbst ein
  Kinderkonto (``ChildUser``); ein Erwachsenenschluessel bekommt dort 403.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import select

from .. import __version__
from ..deps import CurrentUser, DbSession
from ..models import User
from ..services.settings_service import load_settings
from . import admin_requests, children, discover, hausordnung, home, kids, requests, storage
from .settings import FassungOeffentlich, _fassungen_oeffentlich, _hausordnung_stand

router = APIRouter(prefix="/api/app/1", tags=["app"])
kids_router = APIRouter(prefix="/api/app/1/kids", tags=["app"])


def _wie(quelle: APIRouter, endpoint: Any, methode: str) -> APIRoute:
    """Die Originalroute zu diesem Handler - mit ihrer Antwortform."""
    for route in quelle.routes:
        if isinstance(route, APIRoute) and route.endpoint is endpoint and methode in route.methods:
            return route
    raise LookupError(f"{endpoint.__name__} ({methode}) is not registered on {quelle.prefix}")


def _zweitschrift(
    ziel: APIRouter, pfad: str, quelle: APIRouter, endpoint: Any, methode: str, summary: str
) -> None:
    original = _wie(quelle, endpoint, methode)
    ziel.add_api_route(
        pfad,
        endpoint,
        methods=[methode],
        response_model=original.response_model,
        status_code=original.status_code,
        responses=original.responses,
        summary=summary,
        description=f"Same as `{methode} {original.path}` in the web interface, for the iPhone app.",
    )


# --- Wer bin ich ------------------------------------------------------------


class AppKonto(BaseModel):
    id: int
    #: Der Anzeigename, sonst der Benutzername.
    name: str
    role: str
    betreiber: bool
    #: Darf freigeben, ablehnen, zurueckstellen.
    entscheidet: bool
    #: Hat Kinderkonten - dann gibt es "Wuensche".
    hat_kinder: bool


class AppHausordnung(BaseModel):
    #: Gibt es eine, die dieses Konto betrifft?
    vorhanden: bool
    titel: str = ""
    fassung: int = 0
    quittierbar: bool = True
    #: Die quittierte Fassung - ist sie kleiner als ``fassung``, fragt die App
    #: vor dem ersten Anfragen.
    gelesen: int | None = None


class AppSelbstauskunft(BaseModel):
    version: str
    konto: AppKonto
    #: Die Fassungen, die die App beim Anfragen anbietet, je mit
    #: ``darf_anfragen`` fuer dieses Konto.
    fassungen: list[FassungOeffentlich]
    hausordnung: AppHausordnung
    #: Der Zeitraum der Stueckzahl fuers ganze Haus: ``day``, ``week``, ``month``.
    kontingent_zeitraum: str
    #: ``arr`` oder ``nex``.
    beschaffung: str


@router.get(
    "/me",
    response_model=AppSelbstauskunft,
    summary="Who am I, and what does the app show",
    description=(
        "The app's first call. Answers 404 on a nexview without this surface: the app then says "
        "the feature comes with the next nexview update."
    ),
)
def selbstauskunft(user: CurrentUser, db: DbSession) -> AppSelbstauskunft:
    settings = load_settings(db)
    hausordnung_stand = _hausordnung_stand(db, user)
    hat_kinder = db.scalar(select(User.id).where(User.parent_id == user.id).limit(1)) is not None
    return AppSelbstauskunft(
        version=__version__,
        konto=AppKonto(
            id=user.id,
            name=user.display_name or user.username,
            role=user.role.value,
            betreiber=user.is_betreiber,
            entscheidet=user.can_approve,
            hat_kinder=hat_kinder,
        ),
        fassungen=_fassungen_oeffentlich(db, settings, user),
        hausordnung=AppHausordnung(
            vorhanden=hausordnung_stand.get("hausordnung_vorhanden", False),
            titel=hausordnung_stand.get("hausordnung_titel", ""),
            fassung=hausordnung_stand.get("hausordnung_fassung", 0),
            quittierbar=hausordnung_stand.get("hausordnung_quittierbar", True),
            gelesen=hausordnung_stand.get("hausordnung_gelesen"),
        ),
        kontingent_zeitraum=settings.quota_period.value,
        beschaffung=settings.beschaffung,
    )


# --- Eigenes Konto, Stoebern, Anfragen --------------------------------------

_zweitschrift(router, "/requests/quota", requests.router, requests.read_quota, "GET", "Your quota")
_zweitschrift(router, "/storage/me", storage.router, storage.eigener_speicher, "GET", "Your storage")
_zweitschrift(router, "/search/{media_type}", discover.router, discover.search, "GET", "Search titles")
_zweitschrift(router, "/discover/{media_type}", discover.router, discover.discover, "GET", "Discover titles")
_zweitschrift(
    router, "/media/{media_type}/{tmdb_id}", discover.router, discover.media_detail, "GET", "One title"
)
_zweitschrift(router, "/home/recent", home.router, home.recent_downloads, "GET", "Recently arrived")
_zweitschrift(router, "/home/trending", home.router, home.trending, "GET", "Trending and missing")
_zweitschrift(router, "/requests", requests.router, requests.create_request, "POST", "Request a title")
_zweitschrift(router, "/requests/mine", requests.router, requests.my_requests, "GET", "Your requests")
_zweitschrift(
    router,
    "/requests/{request_id}/cancel",
    requests.router,
    requests.cancel_own_request,
    "POST",
    "Cancel your request",
)
_zweitschrift(router, "/house-rules", hausordnung.router, hausordnung.lesen, "GET", "House rules")
_zweitschrift(
    router,
    "/house-rules/decision",
    hausordnung.router,
    hausordnung.entscheiden,
    "POST",
    "Accept or refuse the house rules",
)

# --- Freigeben (Entscheider und Administratoren) ----------------------------

_zweitschrift(router, "/approvals", admin_requests.router, admin_requests.list_all, "GET", "All requests")
_zweitschrift(
    router,
    "/approvals/count",
    admin_requests.router,
    admin_requests.pending_count,
    "GET",
    "Requests awaiting a decision",
)
_zweitschrift(
    router,
    "/approvals/{request_id}/approve",
    admin_requests.router,
    admin_requests.approve,
    "POST",
    "Approve a request",
)
_zweitschrift(
    router,
    "/approvals/{request_id}/reject",
    admin_requests.router,
    admin_requests.reject,
    "POST",
    "Reject a request",
)
_zweitschrift(
    router,
    "/approvals/{request_id}/defer",
    admin_requests.router,
    admin_requests.zuruecksetzen,
    "POST",
    "Defer a request",
)
_zweitschrift(
    router,
    "/approvals/all/{user_id}",
    admin_requests.router,
    admin_requests.approve_all,
    "POST",
    "Approve everything one person asked for",
)

# --- Wuensche der Kinder (Eltern) -------------------------------------------

_zweitschrift(router, "/wishes", children.router, children.read_wishes, "GET", "Your children's wishes")
_zweitschrift(
    router,
    "/wishes/{wish_id}/release",
    children.router,
    children.release_wish,
    "POST",
    "Turn a wish into a request",
)
_zweitschrift(
    router,
    "/wishes/{wish_id}/decline",
    children.router,
    children.decline_wish,
    "POST",
    "Decline a wish",
)

# --- Kinder -----------------------------------------------------------------

_zweitschrift(kids_router, "/categories", kids.router, kids.read_categories, "GET", "Categories for a child")
_zweitschrift(kids_router, "/rubrik/{rubrik}", kids.router, kids.read_rubrik, "GET", "One category")
_zweitschrift(kids_router, "/search", kids.router, kids.search, "GET", "Search as a child")
_zweitschrift(
    kids_router, "/title/{media_type}/{tmdb_id}", kids.router, kids.read_title, "GET", "One title"
)
_zweitschrift(kids_router, "/wishes", kids.router, kids.read_wishes, "GET", "My wishes")
_zweitschrift(kids_router, "/wishes", kids.router, kids.create_wish, "POST", "Wish for something")


#: ⚠️ Was hier steht, ist versprochen. ``test_app_zusage.py`` haelt die Liste
#: gegen die wirklichen Routen, ``app1_abdruck.json`` die Antwortfelder.
ZUGESAGT = (
    "/api/app/1/pairing/{pairing_id}/key",
    "/api/app/1/me",
    "/api/app/1/requests/quota",
    "/api/app/1/storage/me",
    "/api/app/1/search/{media_type}",
    "/api/app/1/discover/{media_type}",
    "/api/app/1/media/{media_type}/{tmdb_id}",
    "/api/app/1/home/recent",
    "/api/app/1/home/trending",
    "/api/app/1/requests",
    "/api/app/1/requests/mine",
    "/api/app/1/requests/{request_id}/cancel",
    "/api/app/1/house-rules",
    "/api/app/1/house-rules/decision",
    "/api/app/1/approvals",
    "/api/app/1/approvals/count",
    "/api/app/1/approvals/{request_id}/approve",
    "/api/app/1/approvals/{request_id}/reject",
    "/api/app/1/approvals/{request_id}/defer",
    "/api/app/1/approvals/all/{user_id}",
    "/api/app/1/wishes",
    "/api/app/1/wishes/{wish_id}/release",
    "/api/app/1/wishes/{wish_id}/decline",
    "/api/app/1/kids/categories",
    "/api/app/1/kids/rubrik/{rubrik}",
    "/api/app/1/kids/search",
    "/api/app/1/kids/title/{media_type}/{tmdb_id}",
    "/api/app/1/kids/wishes",
)
