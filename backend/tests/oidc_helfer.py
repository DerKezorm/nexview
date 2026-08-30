"""Der Attrappen-Anbieter fuer die OIDC-Tests.

Ein OIDC-Anbieter ist aus Testsicht drei Antworten: Selbstauskunft,
Schluessel, Token-Tausch. Dieses Modul stellt sie bereit, unterschrieben mit
einem beim Import erzeugten RSA-Schluessel - kein Netz, kein Docker.

``test_oidc_dienst.py`` traegt seine eigene, aeltere Kopie dieser Handvoll
Helfer; hier liegt die geteilte Fassung fuer alles ab Stufe 2.
"""

from __future__ import annotations

import time

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://sso.beispiel.de"
CLIENT_ID = "nexview"
KID = "test-schluessel-1"

_PRIVAT = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVAT_PEM = _PRIVAT.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)


def jwk() -> dict:
    eintrag = jwt.algorithms.RSAAlgorithm.to_jwk(_PRIVAT.public_key(), as_dict=True)
    eintrag.update(kid=KID, use="sig", alg="RS256")
    return eintrag


def beschreibung(issuer: str = ISSUER) -> dict:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/auth",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
    }


def ausweis(**ueberschrieben) -> str:
    jetzt = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "person-1",
        "aud": CLIENT_ID,
        "exp": jetzt + 300,
        "iat": jetzt,
        "nonce": "nonce-1",
        "email": "oma@beispiel.de",
        "email_verified": True,
        "preferred_username": "oma",
    }
    claims.update(ueberschrieben)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, _PRIVAT_PEM, algorithm="RS256", headers={"kid": KID})


def transport(zustand: dict | None = None) -> httpx.MockTransport:
    """Die drei Antworten als MockTransport.

    ``zustand`` ist der Draht zwischen Test und Attrappe: Was dort unter
    ``claims`` steht, schreibt der Token-Tausch in den Ausweis - so bekommt
    der Ausweis das ``nonce`` des laufenden Anlaufs, das der Test erst aus
    der Weiterleitungs-Adresse erfaehrt.
    """
    merker = zustand if zustand is not None else {}

    def antworten(request: httpx.Request) -> httpx.Response:
        pfad = request.url.path
        if pfad == "/.well-known/openid-configuration":
            return httpx.Response(200, json=beschreibung())
        if pfad == "/jwks":
            return httpx.Response(200, json={"keys": [jwk()]})
        if pfad == "/token":
            merker["token_anfrage"] = dict(
                httpx.QueryParams(request.content.decode("utf-8"))
            )
            return httpx.Response(200, json={"id_token": ausweis(**merker.get("claims", {}))})
        return httpx.Response(404)

    return httpx.MockTransport(antworten)
