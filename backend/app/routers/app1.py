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
from . import (
    admin_requests,
    beschaffung,
    blocklist,
    calendar,
    children,
    dashboard,
    discover,
    downloads,
    favorites,
    feedback,
    hausordnung,
    home,
    kids,
    notifications,
    requests,
    stoebern,
    storage,
    tickets,
    users,
    watchlist,
)
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
        # ⚠️ **Die Wachen der Route wandern mit.** ``update_user`` traegt
        # ``betreiberschutz`` am Dekorator, nicht am Handler; ohne diese Zeile
        # stuende der Betreiber in der App ungeschuetzt da.
        dependencies=list(original.dependencies),
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

# --- Block 2: der taegliche Betrieb fuer alle ---------------------------------

_zweitschrift(router, "/notifications", notifications.router, notifications.list_notifications, "GET",
              "Your notifications")
_zweitschrift(router, "/notifications/unread/count", notifications.router, notifications.unread_count, "GET",
              "Number of unread notifications")
_zweitschrift(router, "/notifications/{notification_id}/read", notifications.router, notifications.mark_read, "POST",
              "Mark one notification read")
_zweitschrift(router, "/notifications/read-all", notifications.router, notifications.mark_all_read, "POST",
              "Mark all notifications read")
# Entdecken wie im Web: die Regale aus "Stoebern" statt einer rohen TMDB-Liste.
_zweitschrift(router, "/browse/shelves/{media_type}", stoebern.router, stoebern.regale, "GET", "Shelves to browse")
_zweitschrift(router, "/browse/shelf/{media_type}/{kennung}", stoebern.router, stoebern.regal, "GET",
              "One shelf of titles")
_zweitschrift(router, "/calendar", calendar.router, calendar.kalender, "GET", "What comes out when")
_zweitschrift(router, "/favorites", favorites.router, favorites.my_favorites, "GET", "Titles you like")
_zweitschrift(router, "/favorites", favorites.router, favorites.add_favorite, "POST", "Like a title")
_zweitschrift(router, "/favorites/{media_type}/{tmdb_id}", favorites.router, favorites.remove_favorite, "DELETE",
              "Stop liking a title")
_zweitschrift(router, "/watchlist/status", watchlist.router, watchlist.verbindung, "GET",
              "Whether the Plex watchlist is available")
_zweitschrift(router, "/watchlist/plex", watchlist.router, watchlist.plex_merkliste, "GET", "Your Plex watchlist")
_zweitschrift(router, "/tickets", tickets.router, tickets.meine_tickets, "GET", "Your tickets")
_zweitschrift(router, "/tickets", tickets.router, tickets.anlegen, "POST", "Open a ticket")
_zweitschrift(router, "/tickets/open-count", tickets.router, tickets.offene_anzahl, "GET", "Number of open tickets")
_zweitschrift(router, "/tickets/{ticket_id}", tickets.router, tickets.einzeln, "GET", "One ticket with its messages")
_zweitschrift(router, "/tickets/{ticket_id}/messages", tickets.router, tickets.antworten, "POST",
              "Answer in a ticket")
_zweitschrift(router, "/ratings/mine", feedback.router, feedback.meine_bewertungen, "GET", "Your quality ratings")
_zweitschrift(router, "/ratings/{media_type}/{tmdb_id}", feedback.router, feedback.bewerten, "PUT",
              "Rate the quality of a title")
_zweitschrift(router, "/storage/entries/{posten_id}/give-up", storage.router, storage.abgeben, "POST",
              "Give a title up")
_zweitschrift(router, "/storage/entries/{posten_id}/keep", storage.router, storage.zuruecknehmen, "POST",
              "Keep a title after all")

# --- Block 2: der Betrieb fuer Administratoren -----------------------------

_zweitschrift(router, "/admin/dashboard", dashboard.router, dashboard.dashboard, "GET", "The operator's overview")
_zweitschrift(router, "/admin/downloads", downloads.router, downloads.uebersicht, "GET",
              "What is stuck and what is loading")
_zweitschrift(router, "/admin/downloads/{haenger_id}/files", downloads.router, downloads.dateien, "GET",
              "Files of a stuck download")
_zweitschrift(router, "/admin/downloads/{haenger_id}/import", downloads.router, downloads.importieren, "POST",
              "Import a chosen file")
_zweitschrift(router, "/admin/downloads/{haenger_id}/retry", downloads.router, downloads.erneut, "POST",
              "Try a stuck download again")
_zweitschrift(router, "/admin/downloads/{haenger_id}/remove", downloads.router, downloads.entfernen, "POST",
              "Remove a stuck download")
_zweitschrift(router, "/admin/users", users.router, users.list_users, "GET", "All accounts")
_zweitschrift(router, "/admin/users/{user_id}", users.router, users.update_user, "PATCH", "Change an account")
_zweitschrift(router, "/admin/users/{user_id}/quota/reset", users.router, users.reset_quota, "POST",
              "Reset an account's request count")
_zweitschrift(router, "/admin/users/{user_id}/storage/reset", users.router, users.reset_storage, "POST",
              "Move an account's titles to the house")
_zweitschrift(router, "/admin/invitations", users.router, users.list_invitations, "GET", "Open invitations")
_zweitschrift(router, "/admin/invitations", users.router, users.invite, "POST", "Invite someone")
_zweitschrift(router, "/admin/invitations/{invitation_id}", users.router, users.withdraw_invitation, "DELETE",
              "Withdraw an invitation")
_zweitschrift(router, "/admin/ratings", feedback.router, feedback.alle, "GET", "All quality ratings")
_zweitschrift(router, "/admin/ratings/{rating_id}/reply", feedback.router, feedback.antworten, "POST",
              "Answer a quality rating")
_zweitschrift(router, "/admin/tickets/{ticket_id}", tickets.router, tickets.status_aendern, "PATCH",
              "Set a ticket's state")
_zweitschrift(router, "/admin/blocklist", blocklist.router, blocklist.alle_gesperrten, "GET", "The blocklist")
_zweitschrift(router, "/admin/blocklist/{media_type}/{tmdb_id}", blocklist.router, blocklist.freigeben, "DELETE",
              "Take a title off the blocklist")
_zweitschrift(router, "/admin/trash", beschaffung.router, beschaffung.papierkorb, "GET", "nexcrate's recycle bin")
_zweitschrift(router, "/admin/trash/{eintrag_id}/restore", beschaffung.router, beschaffung.zurueckholen, "POST",
              "Restore from the recycle bin")
_zweitschrift(router, "/admin/releases", storage.router, storage.abgaben, "GET", "Titles given up, waiting")
_zweitschrift(router, "/admin/releases/{posten_id}/files", storage.router, storage.loeschvorschau, "GET",
              "Files a deletion would remove")
_zweitschrift(router, "/admin/releases/{posten_id}/house", storage.router, storage.in_den_hausbestand, "POST",
              "Move a given-up title to the house")
_zweitschrift(router, "/admin/releases/{posten_id}/delete", storage.router, storage.loeschen, "POST",
              "Delete a given-up title")
_zweitschrift(router, "/admin/releases/{posten_id}/unfollow", storage.router, storage.entfolgen, "POST",
              "Keep a given-up title, stop loading it")

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
    # Block 2 (vertrag-block2.md)
    "/api/app/1/notifications",
    "/api/app/1/notifications/unread/count",
    "/api/app/1/notifications/{notification_id}/read",
    "/api/app/1/notifications/read-all",
    "/api/app/1/browse/shelves/{media_type}",
    "/api/app/1/browse/shelf/{media_type}/{kennung}",
    "/api/app/1/calendar",
    "/api/app/1/favorites",
    "/api/app/1/favorites/{media_type}/{tmdb_id}",
    "/api/app/1/watchlist/status",
    "/api/app/1/watchlist/plex",
    "/api/app/1/tickets",
    "/api/app/1/tickets/open-count",
    "/api/app/1/tickets/{ticket_id}",
    "/api/app/1/tickets/{ticket_id}/messages",
    "/api/app/1/ratings/mine",
    "/api/app/1/ratings/{media_type}/{tmdb_id}",
    "/api/app/1/storage/entries/{posten_id}/give-up",
    "/api/app/1/storage/entries/{posten_id}/keep",
    "/api/app/1/admin/dashboard",
    "/api/app/1/admin/downloads",
    "/api/app/1/admin/downloads/{haenger_id}/files",
    "/api/app/1/admin/downloads/{haenger_id}/import",
    "/api/app/1/admin/downloads/{haenger_id}/retry",
    "/api/app/1/admin/downloads/{haenger_id}/remove",
    "/api/app/1/admin/users",
    "/api/app/1/admin/users/{user_id}",
    "/api/app/1/admin/users/{user_id}/quota/reset",
    "/api/app/1/admin/users/{user_id}/storage/reset",
    "/api/app/1/admin/invitations",
    "/api/app/1/admin/invitations/{invitation_id}",
    "/api/app/1/admin/ratings",
    "/api/app/1/admin/ratings/{rating_id}/reply",
    "/api/app/1/admin/tickets/{ticket_id}",
    "/api/app/1/admin/blocklist",
    "/api/app/1/admin/blocklist/{media_type}/{tmdb_id}",
    "/api/app/1/admin/trash",
    "/api/app/1/admin/trash/{eintrag_id}/restore",
    "/api/app/1/admin/releases",
    "/api/app/1/admin/releases/{posten_id}/files",
    "/api/app/1/admin/releases/{posten_id}/house",
    "/api/app/1/admin/releases/{posten_id}/delete",
    "/api/app/1/admin/releases/{posten_id}/unfollow",
)
