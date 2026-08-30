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
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from ..config import get_settings
from ..crypto import decrypt

logger = logging.getLogger("nexview.oidc")

#: Unterschrifts-Verfahren, die Nexview annimmt. RS256 ist die Pflicht der
#: Norm und der Standard praktisch aller Anbieter; die uebrigen sind gaengige
#: Varianten. **HS256 fehlt mit Absicht**: Ein symmetrisch unterschriebener
#: Ausweis wuerde mit dem Client-Geheimnis geprueft - und ein Angreifer, der
#: den ``alg``-Kopf umbiegt, koennte sich sonst mit einem selbstgebauten
#: Ausweis anmelden. Genau dieser Trick ist der bekannteste JWT-Angriff.
ALGORITHMEN = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")

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
                _client = httpx.AsyncClient(timeout=10)
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


async def _json_holen(adresse: str, zweck: str) -> dict[str, Any]:
    client = await _http()
    try:
        antwort = await client.get(adresse)
        antwort.raise_for_status()
        daten = antwort.json()
    except httpx.HTTPError as fehler:
        logger.warning("OIDC: fetching the %s from %r failed: %s", zweck, adresse, fehler)
        raise OidcFehler(
            "oidc_provider_unreachable",
            "Der Anmelde-Anbieter ist gerade nicht erreichbar.",
        ) from fehler
    except ValueError as fehler:
        logger.warning("OIDC: the %s at %r is not valid JSON", zweck, adresse)
        raise OidcFehler(
            "oidc_provider_invalid",
            "Die Antwort des Anmelde-Anbieters ist unverständlich.",
        ) from fehler
    if not isinstance(daten, dict):
        raise OidcFehler(
            "oidc_provider_invalid",
            "Die Antwort des Anmelde-Anbieters ist unverständlich.",
        )
    return daten


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
    for pflicht in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not daten.get(pflicht):
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
        logger.warning("OIDC: no signing key with kid %r at %r", kid, jwks_uri)
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


async def code_tauschen(
    beschreibung: dict[str, Any],
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
) -> str:
    """Den Einmal-Code gegen die Ausweise tauschen; zurueck kommt der ID-Ausweis.

    Beglaubigt wird per HTTP-Basic (``client_secret_basic``) - das ist die
    Auth-Methode, die die Norm jedem Anbieter vorschreibt. Das Geheimnis liegt
    verschluesselt in der Datenbank und wird erst hier geoeffnet.
    """
    client = await _http()
    try:
        antwort = await client.post(
            str(beschreibung["token_endpoint"]),
            auth=(client_id, decrypt(client_secret)),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
    except httpx.HTTPError as fehler:
        logger.warning("OIDC: token exchange failed to connect: %s", fehler)
        raise OidcFehler(
            "oidc_provider_unreachable",
            "Der Anmelde-Anbieter ist gerade nicht erreichbar.",
        ) from fehler

    if antwort.status_code != 200:
        # Der haeufigste Grund ist eine falsch abgetippte Client-ID oder ein
        # falsches Geheimnis - das ist ein Einrichtungsfehler, kein Ausfall.
        # Der Anbieter erklaert sich in ``error``; das gehoert ins Protokoll,
        # nicht in den Browser eines Benutzers.
        try:
            grund = antwort.json().get("error", "")
        except ValueError:
            grund = ""
        logger.warning(
            "OIDC: token endpoint answered %d (%s) - usually a wrong client id "
            "or secret in the provider settings",
            antwort.status_code,
            grund or "no error code",
        )
        raise OidcFehler(
            "oidc_exchange_failed",
            "Der Anbieter hat die Anmeldung nicht angenommen.",
        )

    try:
        id_token = antwort.json().get("id_token")
    except ValueError:
        id_token = None
    if not id_token or not isinstance(id_token, str):
        logger.warning("OIDC: token endpoint answered without an id_token")
        raise OidcFehler(
            "oidc_exchange_failed",
            "Der Anbieter hat die Anmeldung nicht angenommen.",
        )
    return id_token


async def ausweis_pruefen(
    beschreibung: dict[str, Any],
    client_id: str,
    id_token: str,
    nonce: str,
) -> OidcIdentitaet:
    """Den ID-Ausweis pruefen und die Identitaet herausgeben.

    Geprueft wird alles, was die Norm verlangt: Unterschrift gegen die
    veroeffentlichten Schluessel, Aussteller, Empfaenger, Ablauf - und das
    ``nonce`` aus dem Hinweg, damit ein abgefangener Ausweis kein zweites Mal
    eingeloest werden kann.
    """
    try:
        kopf = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as fehler:
        raise OidcFehler(
            "oidc_token_invalid", "Der Ausweis des Anbieters ließ sich nicht prüfen."
        ) from fehler

    jwk = await _schluessel(str(beschreibung["jwks_uri"]), kopf.get("kid"))
    try:
        schluessel = jwt.PyJWK(jwk).key
    except jwt.PyJWTError as fehler:
        logger.warning("OIDC: unusable signing key: %s", fehler)
        raise OidcFehler(
            "oidc_token_invalid", "Der Ausweis des Anbieters ließ sich nicht prüfen."
        ) from fehler

    try:
        claims = jwt.decode(
            id_token,
            key=schluessel,
            algorithms=list(ALGORITHMEN),
            audience=client_id,
            issuer=str(beschreibung["issuer"]),
            leeway=UHREN_TOLERANZ,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as fehler:
        logger.warning("OIDC: id_token rejected: %s", fehler)
        raise OidcFehler(
            "oidc_token_invalid", "Der Ausweis des Anbieters ließ sich nicht prüfen."
        ) from fehler

    if claims.get("nonce") != nonce:
        logger.warning("OIDC: nonce mismatch - the id_token does not belong to this login")
        raise OidcFehler(
            "oidc_token_invalid", "Der Ausweis des Anbieters ließ sich nicht prüfen."
        )

    email = str(claims.get("email") or "").strip().lower() or None
    # Manche Anbieter liefern das Feld als Zeichenkette statt als Wahrheitswert.
    bestaetigt_roh = claims.get("email_verified", False)
    bestaetigt = bestaetigt_roh is True or str(bestaetigt_roh).strip().lower() == "true"

    name = str(claims.get("preferred_username") or claims.get("name") or "").strip()
    if not name and email:
        name = email.split("@", 1)[0]

    return OidcIdentitaet(
        issuer=str(claims["iss"]).rstrip("/"),
        subject=str(claims["sub"]),
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
