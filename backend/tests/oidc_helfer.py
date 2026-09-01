"""Der Attrappen-Anbieter fuer die OIDC-Tests.

Ein OIDC-Anbieter ist aus Testsicht drei Antworten: Selbstauskunft,
Schluessel, Token-Tausch. Dieses Modul stellt sie bereit, unterschrieben mit
beim Import erzeugten Schluesseln - kein Netz, kein Docker.

``test_oidc_dienst.py`` traegt seine eigene, aeltere Kopie dieser Handvoll
Helfer; hier liegt die geteilte Fassung fuer alles ab Stufe 2.
"""

from __future__ import annotations

import time

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

ISSUER = "https://sso.beispiel.de"
CLIENT_ID = "nexview"
KID = "test-schluessel-1"
#: Die Kennungen der beiden Schluessel, die vorher ausgesperrt haben.
KID_ES512 = "test-schluessel-es512"
KID_EDDSA = "test-schluessel-eddsa"


def _pem(schluessel) -> bytes:
    return schluessel.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


_PRIVAT = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVAT_PEM = _pem(_PRIVAT)

#: ⚠️ **Die beiden Verfahren, die vorher jede Anmeldung umrissen.** ES512
#: fehlte in ``ALGORITHMEN``, obwohl ES256 und ES384 dastanden, EdDSA fehlte
#: ganz - Pocket ID laesst beides einstellen. Ein Anbieter, der so
#: unterschreibt, kam bei Nexview nie herein, und im Browser stand nur "Der
#: Ausweis des Anbieters ließ sich nicht prüfen".
#:
#: ES512 verlangt P-521. EdDSA steht hier auf Ed25519, weil das die Kurve ist,
#: die die Anbieter anbieten - und die einzige, die ``PyJWK`` aufmacht: einen
#: Eintrag mit ``crv: Ed448`` lehnt es auch unter PyJWT 2.13 mit
#: "Unsupported crv" ab.
_EC = ec.generate_private_key(ec.SECP521R1())
_EC_PEM = _pem(_EC)
_ED = ed25519.Ed25519PrivateKey.generate()
_ED_PEM = _pem(_ED)

#: Ein Schluessel, der **nicht** im JWKS steht. Damit unterschrieben ist ein
#: Dokument formal heil und trotzdem wertlos - der Fall "jemand schiebt etwas
#: unter".
_FREMD = rsa.generate_private_key(public_exponent=65537, key_size=2048)
FREMDER_SCHLUESSEL = _pem(_FREMD)

#: Verfahren -> (privater Schluessel als PEM, ``kid`` im Kopf).
VERFAHREN: dict[str, tuple[bytes, str]] = {
    "RS256": (_PRIVAT_PEM, KID),
    "ES512": (_EC_PEM, KID_ES512),
    "EdDSA": (_ED_PEM, KID_EDDSA),
}


def jwk() -> dict:
    eintrag = jwt.algorithms.RSAAlgorithm.to_jwk(_PRIVAT.public_key(), as_dict=True)
    eintrag.update(kid=KID, use="sig", alg="RS256")
    return eintrag


def jwks() -> dict:
    """Der veroeffentlichte Schluesselsatz - alle drei Verfahren.

    ⚠️ Die beiden neuen Eintraege tragen **kein** ``alg``-Feld. Das ist kein
    Versehen: Die Norm nennt es optional, ``PyJWK`` leitet das Verfahren aus
    ``kty``/``crv`` ab, und mehrere Anbieter liefern es tatsaechlich nicht mit.
    Ein JWKS, in dem ueberall ``alg`` steht, pruefte den bequemen Fall.
    """
    ec_eintrag = jwt.algorithms.ECAlgorithm.to_jwk(_EC.public_key(), as_dict=True)
    ec_eintrag.update(kid=KID_ES512, use="sig")
    ed_eintrag = jwt.algorithms.OKPAlgorithm.to_jwk(_ED.public_key(), as_dict=True)
    ed_eintrag.update(kid=KID_EDDSA, use="sig")
    return {"keys": [jwk(), ec_eintrag, ed_eintrag]}


#: Eine Adresse, an der sich httpx schon beim **Zerlegen** verschluckt: die
#: eckige Klammer der IPv6-Adresse ist nicht geschlossen. Ein Tippfehler, wie
#: er in einer handgeschriebenen Anbieter-Beschreibung im Heimnetz vorkommt.
#:
#: ⚠️ Sie loest ``httpx.InvalidURL`` aus, und zwar **bevor** eine Anfrage
#: entsteht - der ``MockTransport`` sieht sie nie. Deshalb muss sie ueber die
#: Selbstauskunft hereinkommen, nicht ueber den Transport.
KAPUTTE_ADRESSE = "http://[fd00::1/ui"


def beschreibung(
    issuer: str = ISSUER, *, userinfo: bool = True, kaputte_userinfo: bool = False
) -> dict:
    """Die Selbstauskunft.

    ``userinfo=False`` bildet einen Anbieter nach, der den Endpunkt gar nicht
    anbietet - dann darf Nexview auch nicht nachfragen.

    ``kaputte_userinfo=True`` bildet den Anbieter nach, dessen Betreiber sich
    bei der Adresse vertippt hat.
    """
    daten = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/auth",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
    }
    if kaputte_userinfo:
        daten["userinfo_endpoint"] = KAPUTTE_ADRESSE
    elif userinfo:
        daten["userinfo_endpoint"] = f"{issuer}/userinfo"
    return daten


def ausweis(*, verfahren: str = "RS256", schluessel: bytes | None = None, **ueberschrieben) -> str:
    """Ein ID-Ausweis der Attrappe.

    ``verfahren`` waehlt Unterschrift **und** ``kid``; ``schluessel``
    ueberschreibt nur den Schluessel und laesst den ``kid`` stehen - so
    entsteht der Ausweis, der auf einen bekannten Schluessel zeigt und mit
    einem fremden unterschrieben ist.
    """
    pem, kid = VERFAHREN[verfahren]
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
    return jwt.encode(
        claims, schluessel or pem, algorithm=verfahren, headers={"kid": kid}
    )


def signierte_auskunft(
    *, verfahren: str = "RS256", schluessel: bytes | None = None, **ueberschrieben
) -> str:
    """Eine ``userinfo``-Antwort als unterschriebenes JWT (OIDC Core 5.3.2).

    ⚠️ **Ohne ``exp``, und das ist richtig so.** Die Norm verlangt es fuer den
    ID-Ausweis, fuer die signierte Auskunft nicht - sie ist die Antwort auf
    eine Frage von gerade eben, keine Eintrittskarte. ``iss``, ``aud`` und
    ``sub`` dagegen muessen darin stehen.
    """
    pem, kid = VERFAHREN[verfahren]
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "person-1",
        "email": "oma@beispiel.de",
        "email_verified": True,
    }
    claims.update(ueberschrieben)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(
        claims, schluessel or pem, algorithm=verfahren, headers={"kid": kid}
    )


def transport(zustand: dict | None = None) -> httpx.MockTransport:
    """Die drei Antworten als MockTransport.

    ``zustand`` ist der Draht zwischen Test und Attrappe: Was dort unter
    ``claims`` steht, schreibt der Token-Tausch in den Ausweis - so bekommt
    der Ausweis das ``nonce`` des laufenden Anlaufs, das der Test erst aus
    der Weiterleitungs-Adresse erfaehrt.

    Weitere Schalter im Zustand:

    * ``verfahren`` - womit der Ausweis unterschrieben wird (``RS256``,
      ``ES512``, ``EdDSA``).
    * ``userinfo`` - was der gleichnamige Endpunkt antwortet. Fehlt der
      Schluessel, antwortet er mit 404; das ist der Anbieter, der ihn
      anbietet, aber nichts hergibt.
    * ``userinfo_jwt`` - stattdessen dieses fertige Token, ausgeliefert als
      ``application/jwt``. Gebaut wird es im Test (``signierte_auskunft``),
      damit auch die unterschobenen Fassungen von dort kommen.
    * ``userinfo_inhaltstyp`` - der Inhaltstyp dazu. ``text/plain`` bildet den
      Proxy nach, der Koepfe verbiegt; dann muss die Gestalt des Rumpfs
      genuegen.
    * ``userinfo_status`` - stattdessen dieser HTTP-Code. Fuer den Fall
      "Endpunkt da, antwortet aber nicht".
    * ``ohne_userinfo`` - die Selbstauskunft nennt den Endpunkt gar nicht.
    * ``kaputte_userinfo`` - die Selbstauskunft nennt eine unbrauchbare
      Adresse. Der Transport sieht die Anfrage dann nie (siehe
      ``KAPUTTE_ADRESSE``).
    * ``token_antwort`` - ``(status, rumpf, inhaltstyp)`` statt der
      gewoehnlichen Token-Antwort. Damit laesst sich der Proxy nachbilden, der
      mit Status 200 eine HTML-Seite ausliefert.

    Mitgeschrieben wird ausserdem ``zeitgrenzen``: Pfad -> was httpx unter
    ``request.extensions["timeout"]`` fuehrt. Damit laesst sich pruefen, dass
    die Nachfrage frueher aufgibt als der Token-Tausch, **ohne** im Test auf
    eine Uhr zu warten.
    """
    merker = zustand if zustand is not None else {}

    def antworten(request: httpx.Request) -> httpx.Response:
        pfad = request.url.path
        merker.setdefault("zeitgrenzen", {})[pfad] = request.extensions.get("timeout")
        if pfad == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json=beschreibung(
                    userinfo=not merker.get("ohne_userinfo"),
                    kaputte_userinfo=bool(merker.get("kaputte_userinfo")),
                ),
            )
        if pfad == "/jwks":
            return httpx.Response(200, json=jwks())
        if pfad == "/token":
            merker["token_anfrage"] = dict(
                httpx.QueryParams(request.content.decode("utf-8"))
            )
            if "token_antwort" in merker:
                status, rumpf, typ = merker["token_antwort"]
                return httpx.Response(
                    status, content=rumpf, headers={"content-type": typ}
                )
            return httpx.Response(
                200,
                json={
                    "id_token": ausweis(
                        verfahren=merker.get("verfahren", "RS256"),
                        **merker.get("claims", {}),
                    ),
                    # Ohne ihn kann Nexview nicht nachfragen - jeder echte
                    # Anbieter liefert ihn beim Code-Lauf mit.
                    "access_token": merker.get("access_token", "zugang-1"),
                },
            )
        if pfad == "/userinfo":
            merker["userinfo_kopf"] = request.headers.get("authorization")
            if "userinfo_status" in merker:
                return httpx.Response(merker["userinfo_status"], json={})
            if "userinfo_jwt" in merker:
                return httpx.Response(
                    200,
                    content=merker["userinfo_jwt"],
                    headers={
                        "content-type": merker.get(
                            "userinfo_inhaltstyp", "application/jwt"
                        )
                    },
                )
            if "userinfo" not in merker:
                return httpx.Response(404)
            return httpx.Response(200, json=merker["userinfo"])
        return httpx.Response(404)

    return httpx.MockTransport(antworten)
