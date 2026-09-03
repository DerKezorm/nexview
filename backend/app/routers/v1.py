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
gebrochen wird) oder teuer (weil sie jede Aufraeumarbeit blockiert). Fuenfzehn
sind wenige genug, um sie zu halten, und decken die vier Dinge ab, die man mit
so einer Schnittstelle wirklich tut: **herausfinden was man darf, etwas
anfragen, etwas zaehlen, etwas zeigen.**

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
davon ist zugesagt. Wer von aussen anbindet, liest diese fuenfzehn.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import __version__
from ..deps import AdminUser, CurrentUser, DbSession, schluessel_der_anfrage
from ..models import MediaRequest, RequestStatus, StorageEntry, TicketStatus, utcnow
from ..schemas_media import MediaItem, MediaPage
from ..schemas_requests import QuotaOverview, RequestPublic
from ..services import befunde as befunde_service
from ..services import instanz_gesundheit, instanz_stand
from ..services import tickets as tickets_service
from ..services.settings_service import load_settings
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


# --- Die Kachel -------------------------------------------------------------
#
# ⚠️ **Warum das hier eine eigene Umsetzung ist und keine zweite Registrierung
# von ``/api/admin/dashboard``.**
#
# Der Rest dieser Datei registriert dieselben Handler ein zweites Mal, damit
# Zusage und Innenteil gar nicht auseinanderlaufen koennen. Hier waere das
# genau falsch: Das Admin-Dashboard ist eine **Oberflaechen**-Antwort. Es
# traegt Befund-Kennungen samt Werten, weil die Oberflaeche daraus Saetze baut,
# und es wird sich aendern, sooft eine Pruefung dazukommt. Wuerde es unter
# ``v1`` haengen, waere jede neue Pruefung ein Bruch der Zusage - und die Zusage
# damit die Bremse fuer genau die Arbeit, um die es geht.
#
# Deshalb steht hier eine eigene, absichtlich **schmale und traege** Form: ein
# paar Zahlen, die nicht schrumpfen koennen. Was sich bewegt, bleibt draussen.


class KachelBefunde(BaseModel):
    """Wie viele Befunde es gibt - je Schwere."""

    fehler: int
    warnung: int
    hinweis: int
    #: Die Kennungen der dringendsten, hoechstens drei.
    #:
    #: ⚠️ **Kennungen, kein Freitext.** Ein fertiger Satz waere in der Sprache
    #: des Servers, und er wuerde sich aendern, sobald jemand eine Formulierung
    #: verbessert - unter einer Zusage also nie wieder. Eine Kennung wie
    #: ``dienst.nicht_erreichbar`` ist stabil und laesst sich drueben
    #: uebersetzen oder schlicht anzeigen.
    dringendste: list[str]


class KachelAnfragen(BaseModel):
    wartend: int
    laufend: int
    fehlgeschlagen_7d: int


class KachelBibliothek(BaseModel):
    filme: int
    serien: int
    belegt_bytes: int
    frei_bytes: int


class KachelInstanz(BaseModel):
    name: str
    erreichbar: bool
    probleme: int


class Kachel(BaseModel):
    version: str
    befunde: KachelBefunde
    anfragen: KachelAnfragen
    bibliothek: KachelBibliothek
    instanzen: list[KachelInstanz]
    tickets_offen: int


@router.get(
    "/dashboard",
    response_model=Kachel,
    summary="One tile for your home dashboard",
    description=(
        "Everything a dashboard tile needs, in a single call: how many findings "
        "are open, what is waiting, how full the library is and whether the "
        "instances are answering.\n\n"
        "**Findings come as identifiers, not sentences.** `dringendste` holds up "
        "to three stable keys such as `dienst.nicht_erreichbar`. A ready-made "
        "sentence would be in the server's language, and it would change "
        "whenever somebody improves a wording - which under a promise it never "
        "could.\n\n"
        "⚠️ **This needs a token belonging to an administrator.** Instance state "
        "and disk figures are an operator's business, and a token inherits the "
        "rights of its owner. Mark it *read only* and it is limited to GET - but "
        "an administrator's read-only token can still read the user list, the "
        "log and the settings. Worth knowing before you pin it to a screen "
        "somebody else can see."
    ),
)
def kachel(admin: AdminUser, db: DbSession) -> Kachel:
    """Die Kachel - eine Antwort, wenige Zahlen.

    ⚠️ **Alles kommt aus Gemerktem, nichts wird hier gemessen.** Eine Kachel
    fragt im Minutentakt; jede Messung an dieser Stelle waere eine Radarr-
    Anfrage pro Bildschirm und Minute.
    """
    from ..models import MediaType

    settings = load_settings(db)
    gefunden = befunde_service.sammeln(db, settings)
    gezaehlt = befunde_service.zaehlen(gefunden)

    grenze = utcnow() - timedelta(days=7)

    def zahl(bedingung) -> int:
        return db.scalar(select(func.count(MediaRequest.id)).where(bedingung)) or 0

    # Einmal je Tabelle statt einmal je Instanz - die Kachel fragt im
    # Minutentakt, und drei Instanzen sind drei Zeilen, keine drei Abfragen.
    gesundheit = instanz_gesundheit.alle(db)

    def probleme(kennung: str) -> int:
        zeile = gesundheit.get(kennung)
        return len((zeile.stand if zeile else None) or [])

    staende = instanz_stand.alle(db)

    traeger = None
    for stand in staende.values():
        gefundene = (stand.messwerte or {}).get("traeger")
        if isinstance(gefundene, list) and gefundene:
            traeger = gefundene
            break

    return Kachel(
        version=__version__,
        befunde=KachelBefunde(
            fehler=gezaehlt["fehler"],
            warnung=gezaehlt["warnung"],
            hinweis=gezaehlt["hinweis"],
            dringendste=[b.kennung for b in gefunden[:3]],
        ),
        anfragen=KachelAnfragen(
            wartend=zahl(MediaRequest.status == RequestStatus.pending_approval),
            laufend=zahl(
                MediaRequest.status.in_(
                    (RequestStatus.approved, RequestStatus.searching)
                )
            ),
            fehlgeschlagen_7d=zahl(
                (MediaRequest.status == RequestStatus.failed)
                & (MediaRequest.requested_at >= grenze)
            ),
        ),
        bibliothek=KachelBibliothek(
            filme=db.scalar(
                select(func.count(StorageEntry.id)).where(
                    StorageEntry.media_type == MediaType.movie
                )
            )
            or 0,
            serien=db.scalar(
                select(func.count(func.distinct(StorageEntry.tvdb_id))).where(
                    StorageEntry.media_type == MediaType.tv
                )
            )
            or 0,
            belegt_bytes=int(db.scalar(select(func.sum(StorageEntry.size_bytes))) or 0),
            frei_bytes=sum(
                int(t.get("frei") or 0) for t in (traeger or []) if isinstance(t, dict)
            ),
        ),
        instanzen=[
            KachelInstanz(
                name=instanz.name,
                erreichbar=(
                    staende[instanz.kennung].erreichbar
                    if instanz.kennung in staende
                    else True
                ),
                probleme=probleme(instanz.kennung),
            )
            for instanz in settings.arr_instanzen()
        ],
        tickets_offen=len(
            tickets_service.sichtbare_tickets(db, admin, status=TicketStatus.open)
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


# --- Selbstauskunft ---------------------------------------------------------
#
# ⚠️ **Die erste Adresse, die eine Anbindung aufruft.** Was Nexview hergibt,
# haengt am Schluessel: Ein Konto ohne Verwaltungsrechte sieht keine Instanzen
# und darf nichts entscheiden, und ein Schluessel auf "nur lesen" veraendert
# gar nichts. Eine Anbindung, die das nicht vorher weiss, hat drei
# Moeglichkeiten - alles anlegen und die Haelfte tot stehen lassen, reihum
# probieren und dabei fremde Protokolle mit 403ern fuellen, oder raten.
#
# Deshalb steht hier **eine** Antwort, die beides zusammenrechnet: was das
# Konto darf und was der Schluessel davon uebrig laesst.


class MeinKonto(BaseModel):
    id: int
    username: str
    #: Der Anzeigename, sonst der Benutzername. Nie ``null`` - wer einen Namen
    #: hinschreiben will, soll nicht selbst zurueckfallen muessen.
    name: str
    role: str
    betreiber: bool


class MeinSchluessel(BaseModel):
    name: str
    nur_lesen: bool


class Selbstauskunft(BaseModel):
    version: str
    konto: MeinKonto
    #: ``null``, wenn die Anfrage aus einer angemeldeten Sitzung kam.
    schluessel: MeinSchluessel | None
    #: Was **diese Anfrage** darf - Rolle und Schluessel zusammengerechnet.
    #:
    #: ⚠️ **Kennungen, keine Saetze** - aus demselben Grund wie bei den
    #: Befunden der Kachel. Und bewusst grob: fuenf Kennungen, die sich an
    #: den Wachen in ``deps.py`` orientieren, nicht eine je Adresse. Eine
    #: feinere Liste waere eine zweite Wahrheit neben den Wachen, und zwei
    #: Wahrheiten laufen auseinander.
    darf: list[str]


@router.get(
    "/me",
    response_model=Selbstauskunft,
    summary="Who am I and what may I do",
    description=(
        "**The first call an integration should make.** What Nexview hands "
        "out depends on the key: an account without administrative rights sees "
        "no instances and decides nothing, and a key marked *read only* "
        "changes nothing at all.\n\n"
        "`darf` folds both together and lists what **this request** is allowed "
        "to do, as stable identifiers:\n\n"
        "- `lesen` - read whatever the account can see\n"
        "- `anfragen` - create requests\n"
        "- `entscheiden` - approve, reject or defer other people's requests\n"
        "- `verwalten` - read operator data: instances, users, the dashboard "
        "tile\n"
        "- `einrichten` - change settings and notification targets\n\n"
        "Build against these, not against `role`. A read-only administrator "
        "token has `role: admin` and still cannot approve anything.\n\n"
        "`schluessel` is `null` when the call came from a signed-in browser "
        "session instead of a personal access key."
    ),
)
def selbstauskunft(request: Request, user: CurrentUser) -> Selbstauskunft:
    """Wer klopft an, und was darf er.

    ⚠️ **Rolle und Schluessel getrennt gemeldet, aber zusammen gerechnet.**
    Beides steht in der Antwort, damit man es anzeigen kann - entscheiden soll
    eine Anbindung aber nach ``darf``. Ein Administrator mit einem Nur-Lese-
    Schluessel traegt ``role: admin`` und darf trotzdem nichts genehmigen; wer
    auf die Rolle baut, baut einen Knopf, der immer scheitert.
    """
    schluessel = schluessel_der_anfrage(request)
    schreibt = schluessel is None or not schluessel.nur_lesen

    darf = ["lesen"]
    if schreibt:
        darf.append("anfragen")
    if user.can_approve and schreibt:
        darf.append("entscheiden")
    if user.is_admin:
        darf.append("verwalten")
        if schreibt:
            darf.append("einrichten")

    return Selbstauskunft(
        version=__version__,
        konto=MeinKonto(
            id=user.id,
            username=user.username,
            name=user.display_name or user.username,
            role=user.role.value,
            betreiber=user.is_betreiber,
        ),
        schluessel=(
            MeinSchluessel(name=schluessel.name, nur_lesen=schluessel.nur_lesen)
            if schluessel is not None
            else None
        ),
        darf=darf,
    )


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
    "/api/v1/dashboard",
    "/api/v1/health",
    "/api/v1/me",
)
