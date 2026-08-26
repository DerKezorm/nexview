"""Die zugesagte Schnittstelle: `/api/v1`.

⚠️ **Was hier steht, ist ein Versprechen. Alles andere ist es nicht.**

Nexview hat rund 190 Adressen, und die allermeisten davon sind ein Innenteil
der Anwendung: Die Oberflaeche spricht mit dem Backend, und beide aendern sich
gemeinsam. Heute hiess ein Feld ``storniert`` und morgen ``offen``, weil der
neue Name besser passt - das ist richtig so und soll auch so bleiben.

Fuer jemanden, der von aussen etwas anbindet, ist das aber unbrauchbar: Er
baut gegen etwas, das sich unter ihm bewegt. Deshalb gibt es hier eine
**kleine, ausgewaehlte Flaeche** mit einer Zusage - solange ``v1`` in der
Adresse steht, aendert sich an diesen Antworten nichts, was Bestehendes bricht.
Muss doch etwas brechen, entsteht ``/api/v2`` **daneben**, und v1 laeuft
weiter.

Warum klein
-----------
Eine Zusage ueber alle 190 Adressen waere entweder wertlos (weil sie doch
gebrochen wird) oder teuer (weil sie jede Aufraeumarbeit blockiert). Dreizehn
sind wenige genug, um sie zu halten, und decken die drei Dinge ab, die man mit
so einer Schnittstelle wirklich tut: **etwas anfragen, etwas zaehlen, etwas
zeigen.**

Bewusst **nicht** dabei: die gesamte Verwaltung (Einstellungen, Benutzer,
Protokoll, Sicherungen). Die zu versprechen hiesse, das Konfigurationsmodell
einzufrieren - und genau daran wird laufend gearbeitet. Erreichbar bleibt das
alles unter ``/api/…``, nur eben ohne Zusage.

⚠️ **Die Handler sind dieselben.** Hier wird nichts nachgebaut, sondern
dieselbe Funktion ein zweites Mal registriert. Damit kann das Verhalten von v1
und dem Innenteil gar nicht auseinanderlaufen - es *ist* dasselbe. Was sich
aendern kann, ist die Antwort**form**, und dafuer gibt es einen Test, der sie
festhaelt (``test_v1_zusage.py``).

Die Rechte kommen wie ueberall aus dem Konto: Ein API-Token erbt die seines
Besitzers. ``pending/count`` ist deshalb hier genauso Entscheidern vorbehalten
wie im Innenteil - ohne dass dafuer etwas zu tun waere.

⚠️ **Die Beschreibungen hier sind englisch, der Rest der Datei deutsch.**
Das ist kein Ausrutscher. Ein Docstring tut bei uns zwei Jobs: Er begruendet
eine Entscheidung fuer den naechsten, der die Datei liest (deutsch, wie das
ganze Projekt), und FastAPI stellt ihn gleichzeitig als oeffentlichen Text auf
``/docs``. Beides geht nicht - was nach draussen geht, ist englisch.

Deshalb bekommt jede zugesagte Adresse ein ausdrueckliches ``description=``.
Es sticht den Docstring des Handlers, ohne ihn anzufassen: Innen bleibt die
deutsche Begruendung stehen, aussen liest ein fremder Entwickler Englisch.

Der Rest der rund 190 Adressen antwortet auf ``/docs`` weiterhin deutsch. Das
ist bekannt und bewusst so gelassen - es sind 151 Beschreibungen, und keine
davon ist zugesagt. Wer von aussen anbindet, liest diese dreizehn.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas_media import MediaItem, MediaPage
from ..schemas_requests import QuotaOverview, RequestPublic
from . import about as about_router
from . import admin_requests, discover, home, notifications, requests, storage, tickets
from .about import AboutInfo
from .home import RecentItem
from .storage import StorageMine

#: ⚠️ **Dieser Router wird mit ``NUR_ERWACHSENE`` eingehaengt** - siehe
#: ``main.py``. Ohne das waere er ein Loch: Die Originale unter ``/api/…``
#: tragen diesen Schutz beim Einhaengen, nicht am Handler. Eine zweite
#: Registrierung ohne ihn kaeme also an den Kinderschutz **vorbei**, obwohl
#: derselbe Code dahintersteht.
#:
#: Genau das ist beim ersten Anlauf passiert und wurde von
#: ``test_child_permissions.test_jede_route_ist_entschieden`` gefunden - dem
#: Test, der jede Route zwingt, entschieden zu sein.
router = APIRouter(prefix="/api/v1", tags=["v1"])

#: Ohne Anmeldung erreichbar - genau wie ``/api/health``.
#:
#: Eine Ueberwachung, die sich erst anmelden muss, um zu fragen "laeufst du
#: noch", ist keine. Der Healthcheck des Containers ruft die Schwester unter
#: ``/api/health`` auf.
public_router = APIRouter(prefix="/api/v1", tags=["v1"])


# --- Anfragen stellen und verfolgen -----------------------------------------
#
# Der Fall, fuer den die meisten kommen: einen Titel finden, ihn anfragen und
# spaeter nachsehen, was daraus geworden ist.

router.add_api_route(
    "/search/{media_type}", discover.search, methods=["GET"], response_model=MediaPage,
    summary="Search movies or shows",
    description=(
        "Find titles by name. `media_type` is `movie` or `tv`. Results are paged "
        "and come from TMDB in the language of the calling account."
    ),
)
router.add_api_route(
    "/media/{media_type}/{tmdb_id}",
    discover.media_detail,
    methods=["GET"],
    response_model=MediaItem,
    summary="Details for one title",
    description=(
        "Everything about a single title: overview, cast, ratings, runtime, and "
        "whether it is already in the library."
    ),
)
router.add_api_route(
    "/requests", requests.create_request, methods=["POST"], response_model=RequestPublic,
    status_code=201,
    summary="Request a title",
    description=(
        "Ask for a title to be added. The request goes through exactly the same "
        "checks as one made in the browser: quota, blocklist and approval all apply "
        "to the account the token belongs to. A request that needs approval comes "
        "back as pending, not as an error."
    ),
)
router.add_api_route(
    "/requests/mine", requests.my_requests, methods=["GET"], response_model=list[RequestPublic],
    summary="Your own requests",
    description=(
        "Every request made by the calling account, newest first, with its current "
        "state."
    ),
)
router.add_api_route(
    "/requests/quota", requests.read_quota, methods=["GET"], response_model=QuotaOverview,
    summary="How much you may still request",
    description=(
        "What the calling account has used in the current period and what is left. "
        "An account without a limit reports no ceiling rather than a very large "
        "one."
    ),
)
router.add_api_route(
    "/requests/{request_id}/cancel",
    requests.cancel_own_request,
    methods=["POST"],
    response_model=RequestPublic,
    summary="Cancel your own request",
    description=(
        "Withdraw a request you made yourself. Only works while it is still open - "
        "once something has been downloaded there is nothing left to cancel."
    ),
)


# --- Fuers Dashboard ---------------------------------------------------------
#
# Drei Zahlen und eine Liste. Die Zahlen sind besonders gut zuzusagen, weil
# ihre Form nicht schrumpfen kann - es ist eine Zahl.
#
# ⚠️ ``home/recent`` traegt auch ``requested_by`` und ``requester_avatar``.
# Wer die Kachel an eine Wand haengt, zeigt damit, **wer was angefragt hat**.
# Das ist im Haushalt gewollt und anderswo vielleicht nicht - deshalb steht es
# in der Dokumentation, statt dass die Felder fehlen.

router.add_api_route(
    "/home/recent", home.recent_downloads, methods=["GET"], response_model=list[RecentItem],
    summary="Recently arrived",
    description=(
        "Titles that finished downloading recently - the list a dashboard tile "
        "shows. Note that each entry also names **who requested it**. That is "
        "wanted inside a household and may not be wanted on a wall-mounted screen."
    ),
)
router.add_api_route("/tickets/open-count", tickets.offene_anzahl, methods=["GET"],
    summary="Number of open tickets",
    description=(
        "How many tickets are still open. A plain number, meant for a dashboard."
    ),)
router.add_api_route(
    "/admin/requests/pending/count", admin_requests.pending_count, methods=["GET"],
    summary="Number of requests awaiting a decision",
    description=(
        "How many requests are waiting for approval. Restricted to accounts that "
        "may decide - a token inherits that from its owner, so an ordinary account "
        "gets 403 here."
    ),
)
router.add_api_route(
    "/notifications/unread/count", notifications.unread_count, methods=["GET"],
    summary="Number of unread notifications",
    description=(
        "Unread notifications for the calling account."
    ),
)


# --- Betrieb -----------------------------------------------------------------

router.add_api_route("/about", about_router.about, methods=["GET"], response_model=AboutInfo,
    summary="Version and build",
    description=(
        "Which version of Nexview this is. Useful for telling deployments apart and "
        "for deciding whether a feature you rely on exists yet."
    ),)
router.add_api_route(
    "/storage/me", storage.eigener_speicher, methods=["GET"], response_model=StorageMine,
    summary="Your own storage use",
    description=(
        "How much space the titles attributed to the calling account take up, and "
        "against which allowance."
    ),
)


# ⚠️ Eigene Umsetzung statt einer zweiten Registrierung: Das Original steht in
# ``main.py`` und nicht in einem Router, und eine Zusage soll nicht daran
# haengen, wo etwas zufaellig definiert ist.
@public_router.get(
    "/health",
    tags=["v1"],
    summary="Is Nexview running",
    description=(
        "Answers without a token. A monitor that has to sign in before it may "
        "ask \"are you still alive\" is not a monitor. Returns "
        "`{\"status\": \"ok\"}` and nothing else - deliberately no version, no "
        "database state, nothing that would tell an unauthenticated caller "
        "about the installation."
    ),
)
def health() -> dict[str, str]:
    return {"status": "ok"}


#: ⚠️ **Die Liste, gegen die geprueft wird.** Sie steht hier und nicht im Test,
#: damit man an einer Stelle sieht, was zugesagt ist - und damit ein neuer
#: Eintrag eine bewusste Handlung bleibt und kein Nebeneffekt.
ZUGESAGT = (
    "/api/v1/search/{media_type}",
    "/api/v1/media/{media_type}/{tmdb_id}",
    "/api/v1/requests",
    "/api/v1/requests/mine",
    "/api/v1/requests/quota",
    "/api/v1/requests/{request_id}/cancel",
    "/api/v1/home/recent",
    "/api/v1/tickets/open-count",
    "/api/v1/admin/requests/pending/count",
    "/api/v1/notifications/unread/count",
    "/api/v1/about",
    "/api/v1/storage/me",
    "/api/v1/health",
)
