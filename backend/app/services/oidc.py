"""Das OpenID-Connect-Protokoll: Discovery, Weiterleitung, Tausch, Pruefung.

Hier steht **nur die Norm** - kein Konto, keine Datenbanklogik. Was mit einer
gepruefte Identitaet geschieht, entscheidet ``oidc_accounts``; dieses Modul
liefert sie. Dieselbe Trennung wie zwischen ``services/mediaserver`` und
``mediaserver_accounts``.

Der Ablauf (Authorization Code Flow mit PKCE):

1. **Hinweg:** Nexview erzeugt drei Zufallswerte (``state``, ``nonce``,
   PKCE-``verifier``), packt sie in ein kurzlebiges signiertes Cookie und
   leitet zum Anbieter weiter.
2. **Rueckweg:** Der Anbieter schickt den Browser mit einem Einmal-Code
   zurueck. Nexview prueft ``state`` gegen das Cookie, tauscht den Code
   (samt ``verifier``) beim Anbieter gegen die Ausweise und prueft den
   ID-Ausweis: Unterschrift gegen die veroeffentlichten Schluessel,
   Aussteller, Empfaenger, Ablauf, ``nonce``.

Warum alle drei Werte, obwohl sie sich aehneln: ``state`` verhindert, dass
jemand einem Browser eine **fremde** Antwort unterschiebt (CSRF auf den
Rueckweg). ``nonce`` verhindert, dass ein **abgefangener Ausweis** ein zweites
Mal eingeloest wird - er steht *im* Ausweis, nicht in der Adresse. Der
PKCE-``verifier`` verhindert, dass ein **abgefangener Code** etwas nuetzt:
Einloesen kann ihn nur, wer das Urbild des mitgeschickten Praegewerts kennt.

⚠️ **Keine neue Abhaengigkeit.** httpx holt die Dokumente, PyJWT prueft die
Unterschriften - beides ist laengst an Bord. Eine OIDC-Bibliothek naehme einem
den Ablauf ab, braechte aber einen zweiten HTTP-Stack und eine zweite
JWT-Deutung mit; die Norm ist klein genug, sie auszuschreiben.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from ..config import get_settings
from ..crypto import decrypt
from . import logs

logger = logging.getLogger("nexview.oidc")

#: Unterschrifts-Verfahren, die Nexview annimmt. RS256 ist die Pflicht der
#: Norm und der Standard praktisch aller Anbieter; die uebrigen sind gaengige
#: Varianten. **HS256 fehlt mit Absicht**: Ein symmetrisch unterschriebener
#: Ausweis wuerde mit dem Client-Geheimnis geprueft - und ein Angreifer, der
#: den ``alg``-Kopf umbiegt, koennte sich sonst mit einem selbstgebauten
#: Ausweis anmelden. Genau dieser Trick ist der bekannteste JWT-Angriff.
#:
#: ⚠️ **Was hier fehlt, sperrt aus.** Ein Anbieter unterschreibt mit dem
#: Verfahren, das sein Betreiber eingestellt hat; steht es nicht in dieser
#: Liste, scheitert **jede** Anmeldung bei ihm, und im Browser steht nur "Der
#: Ausweis des Anbieters ließ sich nicht prüfen". ES512 fehlte, obwohl ES256
#: und ES384 dastanden, EdDSA fehlte ganz - Pocket ID laesst beides
#: einstellen. Beide sind gegen das festgenagelte PyJWT (2.13) erprobt, und
#: zwar ueber ``PyJWK`` wie im Betrieb, nicht nur ueber einen rohen Schluessel.
#:
#: ⚠️ EdDSA traegt **nur Ed25519**: ``PyJWK`` lehnt einen JWKS-Eintrag mit
#: ``crv: Ed448`` auch unter PyJWT 2.13 mit "Unsupported crv" ab
#: (nachgemessen gegen 2.13.0). Das ist eine Grenze der Bibliothek und keine
#: Entscheidung von Nexview; die Liste hier nennt nur das Verfahren, nicht
#: die Kurve. Wer beim Anbieter EdDSA auf Ed448 stellt, sperrt jede
#: Anmeldung aus.
ALGORITHMEN = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
)

#: Uhren-Toleranz in Sekunden. Selbst gehostete Anbieter laufen auf Rechnern,
#: deren Uhr schon einmal nachgeht - eine Minute Spielraum lehnt niemanden ab,
#: der gerade eben einen gueltigen Ausweis bekommen hat.
UHREN_TOLERANZ = 60

#: Wie lange ein angefangener Anmeldelauf gilt. Laenger als jeder Mensch zum
#: Anmelden braucht, kurz genug, dass ein liegen gebliebenes Cookie nichts
#: mehr wert ist.
ANLAUF_MINUTEN = 10

#: Anbieter-Beschreibung und Schluessel werden zwischengespeichert - sie
#: aendern sich selten, und ohne Speicher stuenden bei jeder Anmeldung drei
#: Abrufe vor dem ersten eigenen Handgriff.
CACHE_SEKUNDEN = 3600

#: Die gewoehnliche Zeitgrenze fuer Abrufe beim Anbieter. Sie gilt fuer alles,
#: ohne das keine Anmeldung zustande kaeme - Selbstauskunft, Schluessel,
#: Token-Tausch. Selbst gehostete Anbieter auf kleiner Hardware brauchen
#: gelegentlich ein paar Sekunden.
ZEITGRENZE_SEKUNDEN = 10

#: Wie lange die Nachfrage bei ``userinfo`` warten darf - **kuerzer als der
#: Rest**.
#:
#: ⚠️ Diese Nachfrage haengt an **jeder** Anmeldung, und ihr Ausbleiben ist
#: verkraftbar: Der Rueckfall ist "keine zusaetzliche Auskunft". Der
#: Token-Tausch ist das Gegenteil - ohne ihn gibt es keine Anmeldung, er darf
#: sich die vollen zehn Sekunden nehmen. Haengen beide an derselben Grenze,
#: verlangsamt ein traeger ``userinfo``-Endpunkt jede Anmeldung im Haus um bis
#: zu zehn Sekunden, fuer eine Auskunft, die am Ende vielleicht gar nicht
#: kommt.
NACHFRAGE_SEKUNDEN = 5

COOKIE_NAME = "nexview_oidc"


class OidcFehler(Exception):
    """Der Anmeldelauf scheitert - mit Kennung zum Anzeigen.

    ``code`` geht als ``oidc_fehler``-Parameter zurueck an die Anmeldeseite,
    die daraus einen Satz in der eingestellten Sprache macht. Das Backend
    benennt, es uebersetzt nicht - wie ueberall.
    """

    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class OidcIdentitaet:
    """Was nach einem gelungenen Lauf feststeht - mehr braucht niemand.

    ``issuer`` + ``subject`` ist die Identitaet im Sinn der Norm; Name und
    Adresse sind Beiwerk, das sich beim Anbieter jederzeit aendern darf.
    ``email_verified`` kommt woertlich vom Anbieter - **nur wenn es wahr ist**,
    darf die Adresse als Bruecke zu einem bestehenden Konto dienen.
    """

    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    username: str | None


@dataclass(frozen=True)
class Anlauf:
    """Die drei Zufallswerte eines Anmeldelaufs."""

    state: str
    nonce: str
    verifier: str

    @property
    def challenge(self) -> str:
        """Der Praegewert zum ``verifier`` (PKCE, S256)."""
        digest = hashlib.sha256(self.verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def anlauf_erzeugen() -> Anlauf:
    return Anlauf(
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        verifier=secrets.token_urlsafe(48),
    )


# ---------------------------------------------------------------------------
# Abrufe beim Anbieter
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

#: adresse -> (daten, geholt_um). Ein schlichter Speicher im Prozess reicht:
#: Faellt er beim Neustart weg, kostet das genau einen Abruf.
_discovery_cache: dict[str, tuple[dict[str, Any], float]] = {}
_jwks_cache: dict[str, tuple[dict[str, Any], float]] = {}


async def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(timeout=ZEITGRENZE_SEKUNDEN)
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def cache_leeren() -> None:
    """Fuer Tests - und fuer den Moment, in dem ein Admin die Adresse aendert."""
    _discovery_cache.clear()
    _jwks_cache.clear()


def _inhaltstyp(antwort: httpx.Response) -> str:
    """Der Inhaltstyp ohne Zusaetze wie ``; charset=utf-8``, klein geschrieben."""
    return antwort.headers.get("content-type", "").split(";")[0].strip().lower()


def _json_deuten(antwort: httpx.Response, zweck: str, adresse: str) -> dict[str, Any]:
    """Den Rumpf als JSON-Objekt deuten - oder mit dem echten Grund scheitern.

    ⚠️ **Der haeufigste Fall ist nicht kaputtes JSON, sondern gar keines.** Ein
    Reverse Proxy vor dem Anbieter, ein Pfad-Vertipper, eine Anmeldeseite
    davor: Zurueck kommt HTML, oft genug mit Status 200. Deshalb nennt das
    Protokoll den **Inhaltstyp** - ``text/html`` sagt dem Betreiber in einem
    Wort, dass er beim Proxy nachsehen muss und nicht bei der Client-ID.

    ⚠️ Der Rumpf selbst steht **nicht** im Protokoll. Bei der Token-Antwort
    stuenden dort Ausweise, und ein Protokoll wandert beim Melden eines
    Fehlers zu Fremden.
    """
    try:
        daten = antwort.json()
    except ValueError as fehler:
        logger.warning(
            "OIDC: the %s at %r is not JSON (content-type %s, %d bytes)",
            zweck,
            adresse,
            _inhaltstyp(antwort) or "none",
            len(antwort.content),
        )
        raise OidcFehler(
            "oidc_provider_invalid",
            "Die Antwort des Anmelde-Anbieters ist unverständlich.",
        ) from fehler
    if not isinstance(daten, dict):
        logger.warning(
            "OIDC: the %s at %r is JSON, but not an object (%s)",
            zweck,
            adresse,
            type(daten).__name__,
        )
        raise OidcFehler(
            "oidc_provider_invalid",
            "Die Antwort des Anmelde-Anbieters ist unverständlich.",
        )
    return daten


async def _json_holen(adresse: str, zweck: str) -> dict[str, Any]:
    client = await _http()
    try:
        antwort = await client.get(adresse)
    except httpx.HTTPError as fehler:
        logger.warning("OIDC: fetching the %s from %r failed: %r", zweck, adresse, fehler)
        raise OidcFehler(
            "oidc_provider_unreachable",
            "Der Anmelde-Anbieter ist gerade nicht erreichbar.",
        ) from fehler
    except Exception as fehler:  # noqa: BLE001 - Absicht, siehe unten
        # ⚠️ **``httpx.InvalidURL`` erbt direkt von ``Exception``**, nicht von
        # ``httpx.HTTPError`` - ein Faenger fuer HTTP-Fehler geht daran vorbei,
        # und die Ausnahme entsteht schon beim **Zerlegen** der Adresse, bevor
        # eine Anfrage existiert. Die Adressen hier kommen von aussen: die
        # Aussteller-Adresse tippt der Administrator, ``jwks_uri`` liefert die
        # Anbieter-Beschreibung. Ein Tippfehler an einer der beiden ist ein
        # Einrichtungsfehler und muss als solcher zurueckkommen - nicht als
        # nackte 500 aus dem Pruef-Knopf.
        logger.warning(
            "OIDC: the address for the %s cannot be used at all (%r): %r",
            zweck,
            adresse,
            fehler,
        )
        raise OidcFehler(
            "oidc_provider_unreachable",
            "Der Anmelde-Anbieter ist gerade nicht erreichbar.",
        ) from fehler

    if not antwort.is_success:
        # Eine Weiterleitung zaehlt hier dazu: Der gemeinsame Client folgt
        # keiner, und ``location`` ist der Hinweis, der die Ursache nennt -
        # meist http statt https oder ein Portal vor dem Anbieter.
        ziel = antwort.headers.get("location")
        logger.warning(
            "OIDC: fetching the %s from %r answered %d (content-type %s%s)",
            zweck,
            adresse,
            antwort.status_code,
            _inhaltstyp(antwort) or "none",
            f", redirect to {ziel!r}" if ziel else "",
        )
        raise OidcFehler(
            "oidc_provider_unreachable",
            "Der Anmelde-Anbieter ist gerade nicht erreichbar.",
        )

    return _json_deuten(antwort, zweck, adresse)


async def discovery(issuer_url: str, *, frisch: bool = False) -> dict[str, Any]:
    """Die Selbstauskunft des Anbieters - mit Zwischenspeicher.

    ⚠️ **Das ``issuer`` im Dokument muss der angefragten Adresse entsprechen.**
    Das verlangt die Norm, und es ist kein Formalismus: Der Wert wird spaeter
    Zeichen fuer Zeichen gegen das ``iss`` jedes Ausweises gehalten. Ein
    Anbieter, der unter einer Adresse antwortet und eine andere behauptet,
    wuerde diese Pruefung sonst still aushebeln.
    """
    issuer = issuer_url.rstrip("/")
    if not frisch:
        im_speicher = _discovery_cache.get(issuer)
        if im_speicher is not None and time.monotonic() - im_speicher[1] < CACHE_SEKUNDEN:
            return im_speicher[0]

    daten = await _json_holen(
        f"{issuer}/.well-known/openid-configuration", "provider description"
    )

    gemeldet = str(daten.get("issuer") or "").rstrip("/")
    if gemeldet != issuer:
        logger.warning(
            "OIDC: provider at %r calls itself %r - refusing the mismatch", issuer, gemeldet
        )
        raise OidcFehler(
            "oidc_issuer_mismatch",
            "Der Anbieter meldet sich unter einer anderen Adresse als eingetragen.",
        )
    fehlend = [
        pflicht
        for pflicht in ("authorization_endpoint", "token_endpoint", "jwks_uri")
        if not daten.get(pflicht)
    ]
    if fehlend:
        logger.warning(
            "OIDC: the provider description at %r is missing %s - without it no "
            "login can even start",
            issuer,
            ", ".join(fehlend),
        )
        raise OidcFehler(
            "oidc_provider_invalid",
            "Die Antwort des Anmelde-Anbieters ist unverständlich.",
        )

    _discovery_cache[issuer] = (daten, time.monotonic())
    return daten


async def _schluessel(jwks_uri: str, kid: str | None) -> dict[str, Any]:
    """Den Unterschrifts-Schluessel zum ``kid`` finden.

    Steht der ``kid`` nicht im Zwischenspeicher, wird **einmal frisch
    geholt**: Anbieter wechseln ihre Schluessel im Betrieb, und der alte
    Speicherstand wuesste davon nichts. Erst wenn auch der frische Satz den
    ``kid`` nicht kennt, ist der Ausweis wirklich nicht pruefbar.
    """
    im_speicher = _jwks_cache.get(jwks_uri)
    if im_speicher is not None and time.monotonic() - im_speicher[1] < CACHE_SEKUNDEN:
        gefunden = _kid_suchen(im_speicher[0], kid)
        if gefunden is not None:
            return gefunden

    daten = await _json_holen(jwks_uri, "signing keys")
    _jwks_cache[jwks_uri] = (daten, time.monotonic())
    gefunden = _kid_suchen(daten, kid)
    if gefunden is None:
        # Was der Anbieter stattdessen anbietet, gehoert ins Protokoll: Ohne
        # das steht dort "Schluessel nicht gefunden" und der Betreiber weiss
        # nicht, ob er die falsche ``jwks_uri`` hat, ob der Satz leer ist oder
        # ob nur der ``kid`` nicht passt. Schluessel-Kennungen sind
        # oeffentlich - sie stehen im selben Dokument.
        angeboten = [
            str(eintrag.get("kid"))
            for eintrag in daten.get("keys", [])
            if isinstance(eintrag, dict)
        ]
        if kid is None:
            logger.warning(
                "OIDC: the token names no kid and %r offers %d keys - refusing to guess",
                jwks_uri,
                len(angeboten),
            )
        else:
            logger.warning(
                "OIDC: no signing key for kid %r at %r - the provider offers %s",
                kid,
                jwks_uri,
                ", ".join(angeboten) or "no keys at all",
            )
        raise OidcFehler(
            "oidc_token_invalid",
            "Der Ausweis des Anbieters ließ sich nicht prüfen.",
        )
    return gefunden


def _kid_suchen(jwks: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
    schluessel = [k for k in jwks.get("keys", []) if isinstance(k, dict)]
    if kid is None:
        # Ohne ``kid`` im Kopf ist die Lage nur eindeutig, wenn es genau einen
        # Unterschrifts-Schluessel gibt. Zu raten waere schlimmer als abzulehnen.
        brauchbar = [k for k in schluessel if k.get("use") in (None, "sig")]
        return brauchbar[0] if len(brauchbar) == 1 else None
    for eintrag in schluessel:
        if eintrag.get("kid") == kid:
            return eintrag
    return None


# ---------------------------------------------------------------------------
# Hinweg: die Adresse beim Anbieter
# ---------------------------------------------------------------------------


def autorisierungs_adresse(
    beschreibung: dict[str, Any],
    client_id: str,
    redirect_uri: str,
    anlauf: Anlauf,
) -> str:
    basis = str(beschreibung["authorization_endpoint"])
    trenner = "&" if "?" in basis else "?"
    return basis + trenner + urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            # ``openid`` macht den Lauf ueberhaupt erst zu OIDC; ``email`` und
            # ``profile`` bitten um Adresse und Namen - mehr fragt Nexview nie.
            "scope": "openid email profile",
            "state": anlauf.state,
            "nonce": anlauf.nonce,
            "code_challenge": anlauf.challenge,
            "code_challenge_method": "S256",
        }
    )


# ---------------------------------------------------------------------------
# Rueckweg: Tausch und Pruefung
# ---------------------------------------------------------------------------


def _oauth_fehler(antwort: httpx.Response) -> str:
    """Warum der Anbieter abgelehnt hat - in einer Zeile fuers Protokoll.

    ⚠️ **Das ist die wertvollste Diagnose im ganzen Ablauf.** OAuth 2 (RFC
    6749, 5.2) schreibt ``error`` vor und empfiehlt ``error_description``;
    darin steht ``invalid_client`` oder ``invalid_grant`` statt "irgendwas mit
    400", und der Beschreibungstext nennt oft genau das falsche Feld. Wer
    diese beiden Felder wegwirft, laesst den Betreiber raten.

    Kommt gar kein JSON, ist **das** die Auskunft: Dann hat nicht der Anbieter
    geantwortet, sondern etwas davor.
    """
    try:
        daten = antwort.json()
    except ValueError:
        return (
            f"no JSON body (content-type {_inhaltstyp(antwort) or 'none'}, "
            f"{len(antwort.content)} bytes) - that is usually a proxy or an "
            "error page in front of the provider, not the provider itself"
        )
    if not isinstance(daten, dict):
        return "a JSON body that is not an object"
    kennung = str(daten.get("error") or "").strip()
    erklaerung = str(daten.get("error_description") or "").strip()
    if not kennung and not erklaerung:
        return (
            'a JSON body without the "error" field OAuth 2 requires (it carries: '
            f"{', '.join(sorted(daten)) or 'nothing at all'})"
        )
    if not erklaerung:
        # Ohne eigenen Text des Anbieters hilft, was die Kennungen bedeuten -
        # mit Text waere das nur Beiwerk.
        return (
            f"error={kennung} (no error_description) - invalid_client points at "
            "the client id or the secret, invalid_grant at the code or at a "
            "redirect_uri the provider does not have on file"
        )
    # Gekuerzt: Manche Anbieter legen einen Stacktrace in die Beschreibung.
    #
    # ⚠️ **Und deshalb ``!r``, nicht roh.** Ein Stacktrace bringt
    # Zeilenumbrueche mit; ohne ``repr`` zerfaellt die Protokollzeile in
    # mehrere, und ``logs._parse`` verwirft jede, die nicht wie ein Zeilenanfang
    # aussieht ("Fortsetzungszeile eines Stacktrace"). Im Protokoll-Fenster der
    # Oberflaeche endete die wertvollste Diagnose des ganzen Ablaufs dann genau
    # dort, wo sie interessant wird.
    return f"error={kennung or 'none'} error_description={erklaerung[:300]!r}"


async def code_tauschen(
    beschreibung: dict[str, Any],
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
) -> tuple[str, str | None]:
    """Den Einmal-Code gegen die Ausweise tauschen.

    Zurueck kommen **beide**: der ID-Ausweis und der Zugangs-Ausweis. Letzterer
    wurde frueher weggeworfen; er wird gebraucht, um beim Anbieter nachzufragen,
    wenn im ID-Ausweis keine Adresse steht (siehe ``_adresse_nachfragen``).

    Beglaubigt wird per HTTP-Basic (``client_secret_basic``) - das ist die
    Auth-Methode, die die Norm jedem Anbieter vorschreibt. Das Geheimnis liegt
    verschluesselt in der Datenbank und wird erst hier geoeffnet.
    """
    adresse = str(beschreibung["token_endpoint"])
    # ⚠️ Vor dem Faenger: ``decrypt`` scheitert nicht, es meldet sich selbst und
    # liefert "". Stuende es im ``try``, saehe ein Schluesselproblem im
    # Protokoll wie eine unbrauchbare Adresse aus.
    geheimnis = decrypt(client_secret)

    client = await _http()
    try:
        antwort = await client.post(
            adresse,
            auth=(client_id, geheimnis),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
    except httpx.HTTPError as fehler:
        logger.warning(
            "OIDC: the token exchange at %r could not be sent: %r", adresse, fehler
        )
        raise OidcFehler(
            "oidc_provider_unreachable",
            "Der Anmelde-Anbieter ist gerade nicht erreichbar.",
        ) from fehler
    except Exception as fehler:  # noqa: BLE001 - dieselbe Falle wie in ``_json_holen``
        # ⚠️ Auch diese Adresse stammt aus der Anbieter-Beschreibung, und auch
        # hier faengt ``httpx.HTTPError`` ``httpx.InvalidURL`` nicht. Ohne
        # diesen Zweig wird aus einem ``token_endpoint``, an dem sich httpx
        # schon beim Zerlegen verschluckt, eine 500 mitten im Rueckweg.
        logger.warning(
            "OIDC: the token endpoint address %r cannot be used at all: %r",
            adresse,
            fehler,
        )
        raise OidcFehler(
            "oidc_provider_unreachable",
            "Der Anmelde-Anbieter ist gerade nicht erreichbar.",
        ) from fehler

    if antwort.status_code != 200:
        # Der haeufigste Grund ist eine falsch abgetippte Client-ID oder ein
        # falsches Geheimnis - das ist ein Einrichtungsfehler, kein Ausfall.
        # Der Anbieter erklaert sich in ``error``; das gehoert ins Protokoll,
        # nicht in den Browser eines Benutzers.
        logger.warning(
            "OIDC: the token endpoint at %r refused the exchange with %d: %s",
            adresse,
            antwort.status_code,
            _oauth_fehler(antwort),
        )
        raise OidcFehler(
            "oidc_exchange_failed",
            "Der Anbieter hat die Anmeldung nicht angenommen.",
        )

    # ⚠️ **Kein JSON ist ein eigener Grund.** Frueher lief jede Antwort, die
    # sich nicht lesen liess, in dieselbe Meldung "answered without an
    # id_token" - und die schickt den Betreiber zu den Scopes seines Clients,
    # waehrend in Wahrheit ein Proxy eine HTML-Seite ausgeliefert hat. Ein
    # falscher Grund kostet mehr Zeit als gar keiner.
    daten = _json_deuten(antwort, "token response", adresse)

    id_token = daten.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        # Erst hier stimmt der Satz - und die Feldnamen dazu sagen, woran es
        # liegt: Steht dort nur ``access_token`` und ``token_type``, hat der
        # Anbieter den ``openid``-Scope nicht anerkannt. Namen, keine Werte.
        logger.warning(
            "OIDC: the token endpoint at %r answered 200 without an id_token "
            "(the response carries: %s)",
            adresse,
            ", ".join(sorted(daten)) or "nothing at all",
        )
        raise OidcFehler(
            "oidc_exchange_failed",
            "Der Anbieter hat die Anmeldung nicht angenommen.",
        )

    access_token = daten.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        # Kein Abbruch: Ohne ihn entfaellt nur die Nachfrage bei ``userinfo``.
        # Wenn spaeter die Adresse fehlt, steht der Grund hier.
        logger.info(
            "OIDC: the token endpoint at %r answered without an access_token - "
            "userinfo will not be asked",
            adresse,
        )
        access_token = None
    return id_token, access_token


async def _token_pruefen(
    beschreibung: dict[str, Any],
    client_id: str,
    token: str,
    *,
    zweck: str,
    pflicht: tuple[str, ...],
) -> dict[str, Any]:
    """Ein unterschriebenes Dokument des Anbieters pruefen und aufmachen.

    Unterschrift gegen die veroeffentlichten Schluessel, Aussteller, Empfaenger,
    Ablauf - dieselbe Pruefung fuer den ID-Ausweis **und** fuer eine signierte
    ``userinfo``-Antwort.

    ⚠️ **Eine zweite, mildere Fassung waere die Luecke.** Wer Auskuenfte aus
    einem unterschriebenen Dokument zieht, muss davon dasselbe verlangen wie
    vom Ausweis; sonst ist der zweite Weg der bequemere fuer jemanden, der
    etwas unterschieben will.

    ``zweck`` steht im Protokoll, ``pflicht`` nennt die Claims, ohne die das
    Dokument nichts wert ist.
    """
    try:
        kopf = jwt.get_unverified_header(token)
    except jwt.PyJWTError as fehler:
        # ``zweck`` bringt seinen Artikel mit ("the id_token") - hier keinen
        # zweiten davorsetzen.
        logger.warning("OIDC: %s is not a readable JWT: %s", zweck, fehler)
        raise OidcFehler(
            "oidc_token_invalid", "Der Ausweis des Anbieters ließ sich nicht prüfen."
        ) from fehler

    jwks_uri = str(beschreibung["jwks_uri"])
    jwk = await _schluessel(jwks_uri, kopf.get("kid"))
    try:
        schluessel = jwt.PyJWK(jwk).key
    except jwt.PyJWTError as fehler:
        # Hierher fuehrt auch ein Schluessel, den PyJWT nicht aufmachen kann -
        # etwa ein OKP-Eintrag mit einer Kurve, die es nicht kennt. Deshalb
        # stehen kty und crv in der Zeile: Ohne sie sieht das aus wie ein
        # kaputter Anbieter.
        logger.warning(
            "OIDC: unusable signing key from %r (kid %r, kty %r, crv %r): %s",
            jwks_uri,
            jwk.get("kid"),
            jwk.get("kty"),
            jwk.get("crv"),
            fehler,
        )
        raise OidcFehler(
            "oidc_token_invalid", "Der Ausweis des Anbieters ließ sich nicht prüfen."
        ) from fehler

    try:
        inhalt = jwt.decode(
            token,
            key=schluessel,
            algorithms=list(ALGORITHMEN),
            audience=client_id,
            issuer=str(beschreibung["issuer"]),
            leeway=UHREN_TOLERANZ,
            options={"require": list(pflicht)},
        )
        # ⚠️ **Mehrere Empfaenger verlangen ``azp``** (OIDC Core 3.1.3.7).
        # ``jwt.decode`` prueft nur, ob unsere ``client_id`` in der Liste
        # *vorkommt* - ein Dokument, das der Anbieter fuer eine andere
        # Anwendung ausgestellt hat und in dem Nexview bloss mitgenannt ist,
        # kaeme sonst durch. Ein Angriffsweg von aussen ist das nicht (der
        # Ausweis stammt immer aus unserem eigenen, mit Geheimnis und PKCE
        # beglaubigten Tausch), eine Fehlkonfiguration am Anbieter aber sehr
        # wohl - und die Norm verlangt es.
        # ⚠️ **Gilt, sobald ``azp`` dasteht - nicht erst bei mehreren
        # Empfaengern.** Der erste Bauversuch pruefte nur Listen mit mehr als
        # einem Eintrag und liess damit ausgerechnet den Fall durch, fuer den
        # ``azp`` gemacht ist: ein Ausweis mit genau einem Empfaenger, den der
        # Anbieter fuer eine ANDERE Anwendung ausgestellt hat. Die Norm bindet
        # beides an "if present" (Core 2, und 3.1.3.7 Schritt 4/5), nicht an
        # die Zahl der Empfaenger.
        if "azp" in inhalt and inhalt.get("azp") != client_id:
            logger.warning(
                "OIDC: %s carries azp %r, which is not this client - the "
                "provider issued it for a different application",
                zweck,
                inhalt.get("azp"),
            )
            raise OidcFehler(
                "oidc_token_invalid",
                "Der Ausweis des Anbieters ließ sich nicht prüfen.",
            )
        return inhalt
    except jwt.PyJWTError as fehler:
        # ``alg`` gehoert in die Zeile: Ein Verfahren, das nicht in
        # ``ALGORITHMEN`` steht, sieht in der Meldung von PyJWT sonst aus wie
        # eine falsche Unterschrift - und der Betreiber sucht am falschen Ende.
        logger.warning(
            "OIDC: %s was rejected (alg %r, kid %r, expected issuer %r): %s",
            zweck,
            kopf.get("alg"),
            kopf.get("kid"),
            beschreibung.get("issuer"),
            fehler,
        )
        raise OidcFehler(
            "oidc_token_invalid", "Der Ausweis des Anbieters ließ sich nicht prüfen."
        ) from fehler


#: Ein Abschnitt eines JWT: Base64url, ohne Polster.
_ABSCHNITT = re.compile(r"[A-Za-z0-9_-]+")


def _sieht_signiert_aus(antwort: httpx.Response) -> bool:
    """Ist der Rumpf ein JWT statt eines JSON-Objekts?

    Massgeblich ist ``application/jwt`` - der Inhaltstyp, den die Norm dafuer
    vorschreibt (Core 5.3.2). Der Blick auf den Rumpf ist die Ruecklage fuer
    den Proxy, der Koepfe verbiegt: drei Abschnitte, durch Punkte getrennt,
    nur Base64url-Zeichen - so sieht kein JSON-Objekt aus. Erkannt heisst hier
    nur "auf diesem Weg deuten"; geprueft wird danach genauso streng.
    """
    if _inhaltstyp(antwort) == "application/jwt":
        return True
    teile = antwort.text.strip().split(".")
    return len(teile) == 3 and all(_ABSCHNITT.fullmatch(teil) for teil in teile)


async def _userinfo_deuten(
    beschreibung: dict[str, Any], client_id: str, antwort: httpx.Response, adresse: str
) -> dict[str, Any] | None:
    """Die Antwort von ``userinfo`` deuten - JSON **oder** signiertes JWT.

    ⚠️ **Authelia und Zitadel koennen die Auskunft unterschrieben liefern**
    (``Content-Type: application/jwt``, im Rumpf ein JWT statt eines Objekts) -
    bei Zitadel ist es ein Haken in der Anwendung, bei Authelia eine Zeile in
    der Konfiguration. Frueher fiel das **still** durch: Der Code fragte "ist
    es ein dict", bekam eine Zeichenkette und verwarf sie ohne einen Ton. Beim
    Betreiber sah das aus wie "Nexview holt die Adresse einfach nicht" - der
    Fall, gegen den diese ganze Nachfrage gebaut wurde, ausgerechnet bei zwei
    der Anbieter, um die es dabei geht.

    Geprueft wird das Dokument wie der ID-Ausweis: dieselben Schluessel,
    dieselben Verfahren, dieselben Anforderungen an ``iss`` und ``aud``.
    Scheitert das, ist die Antwort **weg** - eine ungepruefte Auskunft ist
    schlechter als keine, denn sie entscheidet ueber die Bruecke zu einem
    bestehenden Konto.

    ``None`` heisst "nichts Brauchbares"; der Grund steht dann schon im
    Protokoll.
    """
    if _sieht_signiert_aus(antwort):
        try:
            return await _token_pruefen(
                beschreibung,
                client_id,
                antwort.text.strip(),
                zweck=f"the signed userinfo from {adresse!r}",
                # ⚠️ Ohne ``exp``: Die Norm verlangt es fuer den ID-Ausweis,
                # fuer eine signierte userinfo-Antwort nicht - die ist die
                # Antwort auf eine Frage von gerade eben, keine Eintrittskarte.
                # ``iss`` und ``aud`` dagegen schreibt Core 5.3.2 vor.
                pflicht=("iss", "aud", "sub"),
            )
        except OidcFehler:
            return None  # ``_token_pruefen`` hat den Grund schon protokolliert

    try:
        daten = antwort.json()
    except ValueError:
        logger.warning(
            "OIDC: userinfo at %r answered neither JSON nor a signed token "
            "(content-type %s, %d bytes)",
            adresse,
            _inhaltstyp(antwort) or "none",
            len(antwort.content),
        )
        return None
    if not isinstance(daten, dict):
        logger.warning(
            "OIDC: userinfo at %r did not answer with an object but with %s",
            adresse,
            type(daten).__name__,
        )
        return None
    return daten


async def _adresse_nachfragen(
    beschreibung: dict[str, Any], client_id: str, access_token: str, subject: str
) -> dict[str, Any]:
    """Beim Anbieter nachfragen, was er ueber diese Person sagt (``userinfo``).

    ⚠️ **Ohne das ist Nexview bei mehreren Anbietern blind.** Nach OIDC Core
    ist im ID-Ausweis allein ``sub`` zugesichert; alles andere darf ein
    Anbieter nur hier bereithalten - und zwei verbreitete tun das ab Werk:

    * **Authelia** legt ``email`` gar nicht in den ID-Ausweis. Den Weg, es doch
      zu tun, nennt seine Doku eine "break-glass measure ... on a best-effort
      basis".
    * **Zitadel** liefert die Adresse im ID-Ausweis nur beim reinen
      ``response_type=id_token``, nicht beim gewoehnlichen Code-Lauf.

    Bei beiden kam in Nexview nie eine Adresse an - die Bruecke zu einem
    bestehenden Konto wurde nicht etwa falsch bewertet, sie wurde nie betreten.

    Beide koennen die Auskunft ausserdem **unterschrieben** liefern statt als
    JSON; wie das gedeutet und geprueft wird, steht in ``_userinfo_deuten``.

    ⚠️ **Das ``sub`` entscheidet.** Antwortet ``userinfo`` mit einer anderen
    Kennung als der ID-Ausweis, wird die Antwort **verworfen** - die Norm
    verlangt das (Core 5.3.2), und ohne die Pruefung liesse sich einer
    beglaubigten Anmeldung die Adresse einer fremden anhaengen.

    ⚠️ **Ein Fehlschlag darf nichts kaputtmachen.** Wer heute ohne diesen
    Aufruf hereinkommt, muss es auch morgen - deshalb faengt hier **jeder**
    Fehler, und der Rueckfall ist "keine zusaetzliche Auskunft", nicht ein
    gescheiterter Anmeldelauf. Was "jeder" bedeutet und was es kostet, steht
    am ``except`` selbst.

    Die Zeitgrenze ist mit ``NACHFRAGE_SEKUNDEN`` **kuerzer** als die des
    gemeinsamen Clients: Diese Nachfrage haengt an jeder Anmeldung und ist
    entbehrlich, der Token-Tausch ist es nicht.
    """
    adresse = beschreibung.get("userinfo_endpoint")
    if not isinstance(adresse, str) or not adresse:
        # ⚠️ **Nicht stumm zurueckkehren.** Ohne diese Zeile sieht der
        # Betreiber spaeter nur die Abweisung mit ``email=none`` und sucht bei
        # den Scopes - waehrend in Wahrheit die Selbstauskunft den Endpunkt gar
        # nicht nennt und Nexview nie gefragt hat. Das ist kein Sonderfall:
        # ADFS kennt ihn nicht, und ein Anbieter darf ihn weglassen.
        logger.info(
            "OIDC: the provider description names no userinfo endpoint - not asking; "
            "whatever is missing from the id_token stays missing"
        )
        return {}

    client = await _http()
    try:
        antwort = await client.get(
            adresse,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=NACHFRAGE_SEKUNDEN,
        )
        antwort.raise_for_status()
        daten = await _userinfo_deuten(beschreibung, client_id, antwort, adresse)
    except Exception as fehler:  # noqa: BLE001 - Absicht, siehe unten
        # ⚠️ **Absichtlich jede Ausnahme, nicht eine Liste von Namen.**
        # Vorher stand hier ``httpx.HTTPError``, und genau daran ging
        # ``httpx.InvalidURL`` vorbei - es erbt direkt von ``Exception``. Eine
        # verhunzte Adresse in der Anbieter-Beschreibung (ein vertipptes
        # ``http://[fd00::1/ui`` genuegt) haette damit eine Anmeldung
        # umgerissen, die ohne diese Nachfrage funktioniert haette. Ein
        # Faenger, der Ausnahmenamen aufzaehlt, altert mit der Bibliothek; das
        # Versprechen "reisst nie eine Anmeldung um" darf das nicht.
        #
        # Der Preis: Auch ein Programmierfehler in dieser Funktion landet hier.
        # Deshalb ``%r`` und nicht ``%s`` - ``repr()`` nennt den Typ mit, und
        # ein ``AttributeError`` in der Zeile sieht anders aus als ein
        # ``ConnectError``. Ohne das bezahlt man den groben Faenger, ohne den
        # Gegenwert zu bekommen.
        #
        # Was hier **nicht** haengenbleibt: ``CancelledError``,
        # ``KeyboardInterrupt`` und ``SystemExit`` erben von ``BaseException``,
        # nicht von ``Exception``. Ein Abbruch oder das Herunterfahren kommt
        # also durch - der Faenger ist grob, aber er haelt den Prozess nicht
        # fest.
        logger.warning("OIDC: userinfo at %r could not be read: %r", adresse, fehler)
        return {}

    if daten is None:
        return {}  # Der Grund steht schon in ``_userinfo_deuten``.

    if str(daten.get("sub") or "") != subject:
        logger.warning(
            "OIDC: userinfo at %r answered for a different subject - discarded",
            adresse,
        )
        return {}
    return daten


async def ausweis_pruefen(
    beschreibung: dict[str, Any],
    client_id: str,
    id_token: str,
    nonce: str,
    access_token: str | None = None,
) -> OidcIdentitaet:
    """Den ID-Ausweis pruefen und die Identitaet herausgeben.

    Geprueft wird alles, was die Norm verlangt: Unterschrift gegen die
    veroeffentlichten Schluessel, Aussteller, Empfaenger, Ablauf - und das
    ``nonce`` aus dem Hinweg, damit ein abgefangener Ausweis kein zweites Mal
    eingeloest werden kann.
    """
    claims = await _token_pruefen(
        beschreibung,
        client_id,
        id_token,
        zweck="the id_token",
        pflicht=("exp", "iss", "aud", "sub"),
    )

    if claims.get("nonce") != nonce:
        # Zwei verschiedene Faelle, eine Meldung waere zu wenig: Ein **fehlendes**
        # nonce heisst, dass der Anbieter es nicht zurueckspiegelt (manche tun
        # das nur mit passender Client-Einstellung); ein **anderes** heisst,
        # dass der Ausweis aus einem anderen Lauf stammt. Die Werte selbst
        # gehoeren nicht ins Protokoll.
        logger.warning(
            "OIDC: the id_token does not belong to this login - it carries %s",
            "no nonce at all" if claims.get("nonce") is None else "a different nonce",
        )
        raise OidcFehler(
            "oidc_token_invalid", "Der Ausweis des Anbieters ließ sich nicht prüfen."
        )

    subject = str(claims["sub"])

    # ⚠️ **Es wird immer nachgefragt, nicht nur bei fehlender Adresse.** Das ist
    # der Weg, den die Selbsthoster-Welt geht: Immich ruft ``userinfo`` bei
    # jeder Anmeldung ab und brauchte einen Schalter fuer den *umgekehrten*
    # Fall (ADFS kennt den Endpunkt nicht); ``django-allauth`` - die Grundlage
    # unter Paperless-ngx und vielen anderen - tut dasselbe ueber
    # ``fetch_userinfo``; und Authelia empfiehlt ihn ausdruecklich als den
    # stabilen Weg.
    #
    # Der erste Bauversuch fragte nur, wenn die Adresse fehlte. Das laesst eine
    # Luecke: Ein Anbieter darf ``email`` mitschicken und ``email_verified``
    # nur hier bereithalten - dann galte die Adresse als unbestaetigt, obwohl
    # die Auskunft einen Aufruf entfernt war.
    #
    # Der Preis ist ein Aufruf **je Anmeldung**, nicht je Seitenaufruf.
    #
    # ⚠️ **Der Ausweis behaelt das letzte Wort.** Er ist signiert und geprueft;
    # die Nachfrage ist es nicht - sie haengt allein am ``sub``-Abgleich.
    # Widersprechen sich beide, gilt der Ausweis. Deshalb steht ``claims``
    # rechts.
    # Was der signierte Ausweis selbst sagt - festgehalten, bevor die Nachfrage
    # daruntergelegt wird. Ohne diese Kopie liesse sich hinterher nicht mehr
    # unterscheiden, welche Quelle welche Angabe geliefert hat.
    ausweis_claims = dict(claims)
    nachfrage: dict[str, Any] = {}
    if access_token:
        nachfrage = await _adresse_nachfragen(
            beschreibung, client_id, access_token, subject
        )
        claims = {**nachfrage, **claims}

    email = str(claims.get("email") or "").strip().lower() or None
    # Manche Anbieter liefern das Feld als Zeichenkette statt als Wahrheitswert.
    #
    # ⚠️ **``email_verified`` aus ``userinfo`` gilt genauso wie aus dem Ausweis.**
    # Die Nachfrage beschafft die Auskunft, sie bewertet sie nicht - ein
    # Anbieter, der nicht fuer die Adresse buergt, tut das an beiden Stellen
    # nicht, und die Bruecke bleibt zu.
    bestaetigt_roh = claims.get("email_verified", False)
    bestaetigt = bestaetigt_roh is True or str(bestaetigt_roh).strip().lower() == "true"

    # ⚠️ **Die Bestaetigung muss zu DIESER Adresse gehoeren.** Der Ausweis
    # sticht beim Verschmelzen; ``email_verified`` kann aber aus ``userinfo``
    # stammen, und dort stand womoeglich eine **andere** Adresse - nach einem
    # Adresswechsel bei einem selbst gehosteten Anbieter ist genau das
    # moeglich. Dann buergte eine Bestaetigung fuer eine Adresse, die sie nie
    # betraf, und die Bruecke zu einem bestehenden Konto (oidc_accounts) waere
    # mit einer unbestaetigten Adresse betreten worden. Widersprechen sich die
    # beiden Quellen, gilt die Adresse als unbestaetigt - der Anmeldelauf
    # bricht deshalb nicht ab, nur die Bruecke bleibt zu.
    nachgefragte_adresse = str(nachfrage.get("email") or "").strip().lower() or None
    if (
        bestaetigt
        and email
        and nachgefragte_adresse
        and nachgefragte_adresse != email
        and "email_verified" not in ausweis_claims
    ):
        logger.warning(
            "OIDC: the provider vouched for %r at userinfo but the id_token carries "
            "%r - treating the address as unconfirmed",
            logs.adresse(nachgefragte_adresse),
            logs.adresse(email),
        )
        bestaetigt = False

    name = str(claims.get("preferred_username") or claims.get("name") or "").strip()
    if not name and email:
        name = email.split("@", 1)[0]

    return OidcIdentitaet(
        issuer=str(claims["iss"]).rstrip("/"),
        subject=subject,
        email=email,
        email_verified=bool(email) and bestaetigt,
        username=name or None,
    )


# ---------------------------------------------------------------------------
# Das Anlauf-Cookie
# ---------------------------------------------------------------------------


def _cookie_schluessel() -> bytes:
    """Eigener Signierschluessel, abgeleitet wie in ``security.py`` - mit
    eigenem Praefix, damit ein Anlauf-Cookie nie als Sitzungs-Token durchgeht
    und umgekehrt."""
    secret = get_settings().resolved_secret_key().encode("utf-8")
    return hashlib.sha256(b"nexview-oidc:" + secret).digest()


def cookie_pfad() -> str:
    """Nur die OIDC-Endpunkte sehen das Cookie - wie beim Sitzungs-Cookie
    schneidet der Pfad es auf seinen Zweck zu, samt Unterpfad-Vorbau."""
    return f"{get_settings().url_base}/api/auth/oidc"


def zustand_verpacken(
    slug: str, zweck: str, anlauf: Anlauf, user_id: int | None = None
) -> str:
    """Den Anmeldelauf in ein signiertes, kurzlebiges Cookie packen.

    Der Zustand liegt damit beim Browser statt in einer Tabelle: Es gibt
    nichts aufzuraeumen, und ein Wert, den niemand einloest, ist nach zehn
    Minuten von selbst wertlos. Faelschen kann ihn ohne den Schluessel
    niemand; **lesen** koennte ihn der Besitzer des Browsers - darum stehen
    nur Werte darin, die ihm ohnehin gehoeren.
    """
    jetzt = int(time.time())
    inhalt: dict[str, Any] = {
        "zweck": zweck,
        "slug": slug,
        "state": anlauf.state,
        "nonce": anlauf.nonce,
        "verifier": anlauf.verifier,
        "iat": jetzt,
        "exp": jetzt + ANLAUF_MINUTEN * 60,
    }
    if user_id is not None:
        inhalt["uid"] = user_id
    return jwt.encode(inhalt, _cookie_schluessel(), algorithm="HS256")


def zustand_lesen(wert: str | None) -> dict[str, Any] | None:
    """Das Anlauf-Cookie pruefen; ``None``, wenn es fehlt oder nicht gilt."""
    if not wert:
        return None
    try:
        return jwt.decode(wert, _cookie_schluessel(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
