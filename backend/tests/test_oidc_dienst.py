"""OIDC, Stufe 1: das Protokoll und die Kontologik - ohne echten Anbieter.

Der "Anbieter" ist hier eine Attrappe aus drei Antworten (Selbstauskunft,
Schluessel, Token-Tausch), unterschrieben mit einem im Test erzeugten
RSA-Schluessel. Genau so laesst sich OIDC ohne Netz pruefen - was gegen die
Attrappe besteht und gegen zwei echte Anbieter auf der Spielwiese lief, traegt
der Standard.
"""

from __future__ import annotations

import logging
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.crypto import encrypt
from app.db import SessionLocal
from app.models import AuthToken, OidcBlock, Role, TokenPurpose, User, utcnow
from app.security import has_usable_password, hash_password
from app.services import oidc, oidc_accounts
from app.services.mediaserver_accounts import KontoFehler
from app.services.settings_service import load_settings

from . import oidc_helfer as helfer

ISSUER = "https://sso.beispiel.de"
CLIENT_ID = "nexview"
KID = "test-schluessel-1"


# ---------------------------------------------------------------------------
# Die Attrappe: ein Anbieter aus drei Antworten
# ---------------------------------------------------------------------------

_PRIVAT = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVAT_PEM = _PRIVAT.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
# Ein zweiter Schluessel, der NICHT im JWKS steht - fuer den Faelschungs-Test.
_FREMD = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_FREMD_PEM = _FREMD.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)


def _jwk() -> dict:
    eintrag = jwt.algorithms.RSAAlgorithm.to_jwk(_PRIVAT.public_key(), as_dict=True)
    eintrag["kid"] = KID
    eintrag["use"] = "sig"
    eintrag["alg"] = "RS256"
    return eintrag


def _beschreibung(issuer: str = ISSUER) -> dict:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/auth",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
    }


def _ausweis(
    *,
    schluessel: bytes = _PRIVAT_PEM,
    kid: str | None = KID,
    **ueberschrieben,
) -> str:
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
    # ``None`` heisst im Test "Feld weglassen" - so laesst sich auch ein
    # fehlender Pflicht-Claim bauen.
    claims = {k: v for k, v in claims.items() if v is not None}
    kopf = {"kid": kid} if kid else None
    return jwt.encode(claims, schluessel, algorithm="RS256", headers=kopf)


@pytest.fixture
def anbieter(monkeypatch: pytest.MonkeyPatch):
    """Die drei Antworten des Attrappen-Anbieters ueber einen MockTransport."""

    def antworten(request: httpx.Request) -> httpx.Response:
        pfad = request.url.path
        if pfad == "/.well-known/openid-configuration":
            return httpx.Response(200, json=_beschreibung())
        if pfad == "/jwks":
            return httpx.Response(200, json={"keys": [_jwk()]})
        if pfad == "/token":
            return httpx.Response(200, json={"id_token": _ausweis()})
        return httpx.Response(404)

    oidc.cache_leeren()
    monkeypatch.setattr(
        oidc, "_client", httpx.AsyncClient(transport=httpx.MockTransport(antworten))
    )
    yield antworten
    oidc.cache_leeren()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


async def test_discovery_liefert_und_speichert(anbieter) -> None:
    daten = await oidc.discovery(ISSUER)
    assert daten["token_endpoint"] == f"{ISSUER}/token"
    # Zweiter Abruf kommt aus dem Speicher - auch wenn der Anbieter ab jetzt
    # nicht mehr antwortet.
    oidc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(500))
    )
    daten2 = await oidc.discovery(ISSUER)
    assert daten2 == daten


async def test_discovery_lehnt_fremden_aussteller_ab(monkeypatch) -> None:
    """Ein Anbieter, der sich unter anderer Adresse meldet, fliegt raus."""

    def antworten(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_beschreibung("https://boese.beispiel.de"))

    oidc.cache_leeren()
    monkeypatch.setattr(
        oidc, "_client", httpx.AsyncClient(transport=httpx.MockTransport(antworten))
    )
    with pytest.raises(oidc.OidcFehler) as fehler:
        await oidc.discovery(ISSUER)
    assert fehler.value.code == "oidc_issuer_mismatch"


async def test_discovery_unerreichbar(monkeypatch) -> None:
    def antworten(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nichts da")

    oidc.cache_leeren()
    monkeypatch.setattr(
        oidc, "_client", httpx.AsyncClient(transport=httpx.MockTransport(antworten))
    )
    with pytest.raises(oidc.OidcFehler) as fehler:
        await oidc.discovery(ISSUER)
    assert fehler.value.code == "oidc_provider_unreachable"


# ---------------------------------------------------------------------------
# Ausweis-Pruefung
# ---------------------------------------------------------------------------


async def test_gueltiger_ausweis(anbieter) -> None:
    identitaet = await oidc.ausweis_pruefen(
        _beschreibung(), CLIENT_ID, _ausweis(), "nonce-1"
    )
    assert identitaet.issuer == ISSUER
    assert identitaet.subject == "person-1"
    assert identitaet.email == "oma@beispiel.de"
    assert identitaet.email_verified is True
    assert identitaet.username == "oma"


@pytest.mark.parametrize(
    "kaputt",
    [
        {"schluessel": _FREMD_PEM},  # fremde Unterschrift
        {"iss": "https://boese.beispiel.de"},  # fremder Aussteller
        {"aud": "eine-andere-app"},  # fremder Empfaenger
        {"exp": int(time.time()) - 600},  # abgelaufen (jenseits der Toleranz)
        {"nonce": "ein-anderer-lauf"},  # Ausweis aus einem anderen Lauf
        {"sub": None},  # Pflicht-Claim fehlt
    ],
)
async def test_kaputte_ausweise_fallen_durch(anbieter, kaputt: dict) -> None:
    with pytest.raises(oidc.OidcFehler) as fehler:
        await oidc.ausweis_pruefen(_beschreibung(), CLIENT_ID, _ausweis(**kaputt), "nonce-1")
    assert fehler.value.code == "oidc_token_invalid"


async def test_hs256_wird_nie_akzeptiert(anbieter) -> None:
    """Der klassische JWT-Angriff: ``alg`` auf HS256 umbiegen und mit einem
    erratbaren Geheimnis unterschreiben. Nexview nimmt grundsaetzlich nur
    asymmetrische Verfahren an."""
    jetzt = int(time.time())
    gefaelscht = jwt.encode(
        {
            "iss": ISSUER,
            "sub": "person-1",
            "aud": CLIENT_ID,
            "exp": jetzt + 300,
            "nonce": "nonce-1",
        },
        "irgendein-geheimnis-mit-genug-laenge-fuer-hs256",
        algorithm="HS256",
        headers={"kid": KID},
    )
    with pytest.raises(oidc.OidcFehler):
        await oidc.ausweis_pruefen(_beschreibung(), CLIENT_ID, gefaelscht, "nonce-1")


async def test_email_verified_als_zeichenkette(anbieter) -> None:
    """Manche Anbieter liefern ``"true"``/``"false"`` statt Wahrheitswerten."""
    ident = await oidc.ausweis_pruefen(
        _beschreibung(), CLIENT_ID, _ausweis(email_verified="true"), "nonce-1"
    )
    assert ident.email_verified is True
    ident = await oidc.ausweis_pruefen(
        _beschreibung(), CLIENT_ID, _ausweis(email_verified="false"), "nonce-1"
    )
    assert ident.email_verified is False


async def test_schluesselwechsel_wird_nachgeholt(monkeypatch) -> None:
    """Rotiert der Anbieter seine Schluessel, holt Nexview den Satz frisch,
    statt am alten Speicherstand zu scheitern."""
    abrufe = {"jwks": 0}

    def antworten(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jwks":
            abrufe["jwks"] += 1
            # Erster Abruf: nur ein fremder, alter Schluessel. Ab dem zweiten:
            # der richtige Satz.
            if abrufe["jwks"] == 1:
                alt = jwt.algorithms.RSAAlgorithm.to_jwk(_FREMD.public_key(), as_dict=True)
                alt["kid"] = "alt"
                return httpx.Response(200, json={"keys": [alt]})
            return httpx.Response(200, json={"keys": [_jwk()]})
        return httpx.Response(404)

    oidc.cache_leeren()
    monkeypatch.setattr(
        oidc, "_client", httpx.AsyncClient(transport=httpx.MockTransport(antworten))
    )
    # Speicher fuellen (mit dem alten Satz) ...
    with pytest.raises(oidc.OidcFehler):
        await oidc.ausweis_pruefen(_beschreibung(), CLIENT_ID, _ausweis(), "nonce-1")
    # ... und jetzt gilt der neue: Der zweite Lauf muss durchgehen.
    ident = await oidc.ausweis_pruefen(_beschreibung(), CLIENT_ID, _ausweis(), "nonce-1")
    assert ident.subject == "person-1"
    assert abrufe["jwks"] >= 2


# ---------------------------------------------------------------------------
# Anlauf und Cookie
# ---------------------------------------------------------------------------


def test_anlauf_praegewert() -> None:
    import base64
    import hashlib

    anlauf = oidc.anlauf_erzeugen()
    erwartet = (
        base64.urlsafe_b64encode(hashlib.sha256(anlauf.verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert anlauf.challenge == erwartet
    # Jeder Lauf bekommt frische Werte.
    assert oidc.anlauf_erzeugen().state != anlauf.state


def test_zustand_hin_und_zurueck() -> None:
    anlauf = oidc.anlauf_erzeugen()
    wert = oidc.zustand_verpacken("firma", "login", anlauf)
    inhalt = oidc.zustand_lesen(wert)
    assert inhalt is not None
    assert inhalt["slug"] == "firma"
    assert inhalt["zweck"] == "login"
    assert inhalt["state"] == anlauf.state
    assert inhalt["verifier"] == anlauf.verifier
    assert "uid" not in inhalt

    verknuepfen = oidc.zustand_lesen(
        oidc.zustand_verpacken("firma", "link", anlauf, user_id=7)
    )
    assert verknuepfen is not None and verknuepfen["uid"] == 7


def test_zustand_manipuliert_oder_leer() -> None:
    anlauf = oidc.anlauf_erzeugen()
    wert = oidc.zustand_verpacken("firma", "login", anlauf)
    assert oidc.zustand_lesen(wert[:-2] + "xx") is None
    assert oidc.zustand_lesen(None) is None
    assert oidc.zustand_lesen("") is None


# ---------------------------------------------------------------------------
# Die Kaskade (mit Datenbank)
# ---------------------------------------------------------------------------


def _identitaet(**ueberschrieben) -> oidc.OidcIdentitaet:
    werte = dict(
        issuer=ISSUER,
        subject="person-1",
        email="oma@beispiel.de",
        email_verified=True,
        username="oma",
    )
    werte.update(ueberschrieben)
    return oidc.OidcIdentitaet(**werte)


def test_unbekannt_ohne_auto_anlage_wird_abgewiesen(client) -> None:
    with SessionLocal() as db:
        with pytest.raises(KontoFehler) as fehler:
            oidc_accounts.resolve(
                db, load_settings(db), _identitaet(), auto_create=False
            )
        assert fehler.value.code == "oidc_not_invited"
        # Und es ist **kein** Konto entstanden.
        assert db.query(User).count() == 0


def test_auto_anlage_erzeugt_konto(client) -> None:
    with SessionLocal() as db:
        benutzer = oidc_accounts.resolve(
            db, load_settings(db), _identitaet(), auto_create=True
        )
        db.commit()
        assert benutzer.role == Role.user
        assert benutzer.email == "oma@beispiel.de"
        assert benutzer.email_verified is True
        assert not has_usable_password(benutzer.password_hash)
        assert benutzer.auto_approve is False
        assert len(benutzer.oidc_links) == 1
        assert benutzer.oidc_links[0].subject == "person-1"

        # Zweite Anmeldung derselben Identitaet: dasselbe Konto, kein zweites.
        wieder = oidc_accounts.resolve(
            db, load_settings(db), _identitaet(), auto_create=True
        )
        assert wieder.id == benutzer.id
        assert db.query(User).count() == 1


def test_unbestaetigte_adresse_entsteht_ohne_bestaetigung(client) -> None:
    with SessionLocal() as db:
        benutzer = oidc_accounts.resolve(
            db,
            load_settings(db),
            _identitaet(email_verified=False),
            auto_create=True,
        )
        assert benutzer.email_verified is False


def test_adress_bruecke_verknuepft_bestehendes_konto(client) -> None:
    with SessionLocal() as db:
        vorhanden = User(
            username="oma",
            password_hash=hash_password("passwort-1234"),
            email="oma@beispiel.de",
            email_verified=True,
        )
        db.add(vorhanden)
        db.commit()

        gefunden = oidc_accounts.resolve(
            db, load_settings(db), _identitaet(), auto_create=False
        )
        assert gefunden.id == vorhanden.id
        assert len(gefunden.oidc_links) == 1


def test_adress_bruecke_nur_mit_bestaetigter_adresse(client) -> None:
    """**Der Angriff, den die feste Regel abwehrt:** Wer sich bei einem
    Anbieter ein Konto mit fremder Adresse anlegt (unbestaetigt), darf darueber
    kein fremdes Nexview-Konto uebernehmen."""
    with SessionLocal() as db:
        opfer = User(
            username="opfer",
            password_hash=hash_password("passwort-1234"),
            email="opfer@beispiel.de",
            email_verified=True,
        )
        db.add(opfer)
        db.commit()

        with pytest.raises(KontoFehler) as fehler:
            oidc_accounts.resolve(
                db,
                load_settings(db),
                _identitaet(
                    subject="angreifer", email="opfer@beispiel.de", email_verified=False
                ),
                auto_create=False,
            )
        assert fehler.value.code == "oidc_not_invited"
        db.rollback()
        assert db.get(User, opfer.id).oidc_links == []


def test_deaktiviertes_konto_kommt_nicht_herein(client) -> None:
    with SessionLocal() as db:
        settings = load_settings(db)
        benutzer = oidc_accounts.resolve(db, settings, _identitaet(), auto_create=True)
        benutzer.is_active = False
        db.commit()

        with pytest.raises(KontoFehler) as fehler:
            oidc_accounts.resolve(db, settings, _identitaet(), auto_create=True)
        assert fehler.value.code == "account_disabled"


def test_sperrliste_verhindert_wiederauferstehung(client) -> None:
    """Nach dem Loeschen eines Kontos legt dieselbe Identitaet kein neues an."""
    with SessionLocal() as db:
        settings = load_settings(db)
        benutzer = oidc_accounts.resolve(db, settings, _identitaet(), auto_create=True)
        db.commit()

        oidc_accounts.block(db, benutzer, by=None)
        db.delete(benutzer)
        db.commit()

        with pytest.raises(KontoFehler) as fehler:
            oidc_accounts.resolve(db, settings, _identitaet(), auto_create=True)
        assert fehler.value.code == "oidc_blocked"
        assert db.query(OidcBlock).count() == 1


def test_einladung_bestimmt_rolle_und_grenzen(client) -> None:
    with SessionLocal() as db:
        db.add(
            AuthToken(
                purpose=TokenPurpose.invitation,
                token_hash="test-einladung-hash",
                email="oma@beispiel.de",
                expires_at=(utcnow().replace(tzinfo=None)).replace(year=2999),
                invite_role=Role.approver,
                invite_quota_movies=3,
            )
        )
        db.commit()

        benutzer = oidc_accounts.resolve(
            db, load_settings(db), _identitaet(), auto_create=True
        )
        db.commit()
        assert benutzer.role == Role.approver
        assert benutzer.quota_movies_limit == 3
        eingeloest = db.query(AuthToken).one()
        assert eingeloest.used_at is not None


def test_loesen_mit_aussperrschutz(client) -> None:
    with SessionLocal() as db:
        benutzer = oidc_accounts.resolve(
            db,
            load_settings(db),
            _identitaet(email_verified=False, email=None),
            auto_create=True,
        )
        db.commit()
        # Kein Passwort, keine bestaetigte Adresse, nur diese eine
        # Verknuepfung: Loesen muss abgewiesen werden.
        with pytest.raises(KontoFehler) as fehler:
            oidc_accounts.loesen(benutzer, ISSUER)
        assert fehler.value.code == "oidc_would_lock_out"

        # Mit Passwort geht es.
        benutzer.password_hash = hash_password("neues-passwort-123")
        oidc_accounts.loesen(benutzer, ISSUER)
        db.commit()
        assert benutzer.oidc_links == []


# ---------------------------------------------------------------------------
# Stufe 1b: die Verfahren, die signierte Auskunft und die ehrlichen Gruende
# ---------------------------------------------------------------------------
#
# ⚠️ Ab hier laeuft alles gegen den **geteilten** Attrappen-Anbieter aus
# ``oidc_helfer``: Er ist der einzige, der auch mit ES512 und EdDSA
# unterschreiben und ``userinfo`` unterschrieben ausliefern kann. Die aeltere
# Kopie oben bleibt, wo sie ist - sie traegt die Faelle, die es schon gab.


@pytest.fixture
def helfer_anbieter(monkeypatch: pytest.MonkeyPatch):
    """Der geteilte Attrappen-Anbieter; liefert seinen Zustand als Draht."""
    zustand: dict = {}
    oidc.cache_leeren()
    monkeypatch.setattr(
        oidc, "_client", httpx.AsyncClient(transport=helfer.transport(zustand))
    )
    yield zustand
    oidc.cache_leeren()


# --- Die Unterschrifts-Verfahren -------------------------------------------


@pytest.mark.parametrize("verfahren", ["RS256", "ES512", "EdDSA"])
async def test_jedes_angenommene_verfahren_traegt_eine_anmeldung(
    helfer_anbieter: dict, verfahren: str
) -> None:
    """⚠️ **Was in ``ALGORITHMEN`` fehlt, sperrt aus - vollstaendig.**

    ES512 fehlte, obwohl ES256 und ES384 dastanden; EdDSA fehlte ganz. Bei
    einem Anbieter, der so unterschreibt (Pocket ID laesst beides einstellen),
    scheiterte damit **jede** Anmeldung, und im Browser stand nur "Der Ausweis
    des Anbieters liess sich nicht pruefen" - eine Meldung, die den Betreiber
    zum Schluessel schickt statt zum Verfahren.

    Geprueft wird ueber den echten Weg mit ``PyJWK``, nicht mit einem rohen
    Schluessel: Im Betrieb kommt der Schluessel aus dem JWKS, und die
    Eintraege dort tragen kein ``alg``-Feld (siehe ``oidc_helfer.jwks``).
    """
    ident = await oidc.ausweis_pruefen(
        helfer.beschreibung(),
        helfer.CLIENT_ID,
        helfer.ausweis(verfahren=verfahren),
        "nonce-1",
    )
    assert ident.subject == "person-1"
    assert ident.email == "oma@beispiel.de"


async def test_hs256_bleibt_auch_mit_den_neuen_verfahren_draussen(
    helfer_anbieter: dict, caplog
) -> None:
    """Die Liste ist laenger geworden - der symmetrische Trick bleibt zu.

    Es geht nicht um HS256 als Rechenverfahren, sondern darum, dass ein
    Angreifer den ``alg``-Kopf umbiegt und mit dem Client-Geheimnis
    unterschreibt. Wer die Liste erweitert, muss das hier stehen lassen.
    """
    jetzt = int(time.time())
    gefaelscht = jwt.encode(
        {
            "iss": helfer.ISSUER,
            "sub": "person-1",
            "aud": helfer.CLIENT_ID,
            "exp": jetzt + 300,
        },
        "irgendein-geheimnis-mit-genug-laenge-fuer-hs256",
        algorithm="HS256",
        headers={"kid": helfer.KID},
    )
    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        with pytest.raises(oidc.OidcFehler) as fehler:
            await oidc.ausweis_pruefen(
                helfer.beschreibung(), helfer.CLIENT_ID, gefaelscht, "nonce-1"
            )
    assert fehler.value.code == "oidc_token_invalid"
    # ⚠️ Das Verfahren gehoert in die Zeile: Sonst sieht ein nicht
    # angenommenes ``alg`` aus wie eine falsche Unterschrift.
    assert "'HS256'" in caplog.text


# --- Signiertes userinfo (Core 5.3.2) --------------------------------------


async def test_signiertes_userinfo_liefert_die_adresse(helfer_anbieter: dict) -> None:
    """⚠️ **Der Fall, der vorher still durchfiel.**

    Authelia und Zitadel koennen die Auskunft unterschrieben liefern
    (``Content-Type: application/jwt``). Der Code fragte "ist es ein dict",
    bekam eine Zeichenkette und verwarf sie ohne einen Ton - beim Betreiber
    sah das aus wie "Nexview holt die Adresse einfach nicht", ausgerechnet bei
    den beiden Anbietern, um die es dabei geht.

    Das Token traegt bewusst **kein** ``exp``: Die Norm verlangt es hier
    nicht, und wer es doch verlangte, spraeche genau diesen beiden Anbietern
    ihre Antwort ab.
    """
    helfer_anbieter["userinfo_jwt"] = helfer.signierte_auskunft()
    ident = await oidc.ausweis_pruefen(
        helfer.beschreibung(),
        helfer.CLIENT_ID,
        helfer.ausweis(email=None, email_verified=None),
        "nonce-1",
        "zugang-1",
    )
    assert ident.email == "oma@beispiel.de"
    assert ident.email_verified is True


async def test_signiertes_userinfo_auch_ohne_den_richtigen_inhaltstyp(
    helfer_anbieter: dict,
) -> None:
    """Die Ruecklage fuer den Proxy, der Koepfe verbiegt.

    Massgeblich ist ``application/jwt``; kommt es nicht durch, entscheidet die
    Gestalt des Rumpfs - drei Base64url-Abschnitte, durch Punkte getrennt. So
    sieht kein JSON-Objekt aus, also ist die Verwechslung ungefaehrlich.
    """
    helfer_anbieter["userinfo_jwt"] = helfer.signierte_auskunft()
    helfer_anbieter["userinfo_inhaltstyp"] = "text/plain"
    ident = await oidc.ausweis_pruefen(
        helfer.beschreibung(),
        helfer.CLIENT_ID,
        helfer.ausweis(email=None, email_verified=None),
        "nonce-1",
        "zugang-1",
    )
    assert ident.email == "oma@beispiel.de"


@pytest.mark.parametrize(
    ("was", "gebaut"),
    [
        # Unterschrieben mit einem Schluessel, der nicht im JWKS steht - der
        # ``kid`` zeigt weiter auf den echten.
        ("fremde Unterschrift", {"schluessel": helfer.FREMDER_SCHLUESSEL}),
        ("fremder Empfaenger", {"aud": "eine-andere-app"}),
        ("fremder Aussteller", {"iss": "https://boese.beispiel.de"}),
        ("fremdes subject", {"sub": "jemand-anderes"}),
        ("kein subject", {"sub": None}),
    ],
)
async def test_unterschobenes_userinfo_wird_verworfen(
    helfer_anbieter: dict, was: str, gebaut: dict
) -> None:
    """⚠️ **Die Sicherheitseigenschaft der signierten Auskunft.**

    Das unterschriebene ``userinfo`` geht denselben Weg wie der ID-Ausweis:
    dieselben Schluessel, dieselben Verfahren, dieselben Anforderungen an
    ``iss`` und ``aud`` - und danach gilt der ``sub``-Abgleich weiter. Waere
    die Pruefung hier milder, waere dieser Weg der bequemere fuer jemanden,
    der einer beglaubigten Anmeldung eine fremde Adresse anhaengen will.

    Verworfen heisst: keine Adresse, nicht etwa "Anmeldung kaputt" - der
    Ausweis selbst ist ja in Ordnung.
    """
    helfer_anbieter["userinfo_jwt"] = helfer.signierte_auskunft(
        email="opfer@beispiel.de", **gebaut
    )
    ident = await oidc.ausweis_pruefen(
        helfer.beschreibung(),
        helfer.CLIENT_ID,
        helfer.ausweis(email=None, email_verified=None),
        "nonce-1",
        "zugang-1",
    )
    assert ident.email is None, was
    assert ident.email_verified is False, was
    assert ident.subject == "person-1", was


# --- Ehrliche Gruende beim Token-Tausch ------------------------------------


async def _tauschen(beschreibung: dict | None = None) -> oidc.OidcFehler:
    with pytest.raises(oidc.OidcFehler) as fehler:
        await oidc.code_tauschen(
            beschreibung or helfer.beschreibung(),
            helfer.CLIENT_ID,
            encrypt("sehr-geheim"),
            "einmal-code",
            "http://testserver/callback",
            "verifier",
        )
    return fehler.value


async def test_token_antwort_ohne_json_heisst_nicht_mehr_abgelehnt(
    helfer_anbieter: dict, caplog
) -> None:
    """⚠️ **Ein falscher Grund kostet mehr Zeit als gar keiner.**

    Status 200, im Rumpf eine HTML-Seite: Das ist der Proxy vor dem Anbieter,
    nicht der Anbieter. Frueher lief das in "answered without an id_token" und
    schickte den Betreiber zu den Scopes seines Clients. Jetzt ist es
    ``oidc_provider_invalid``, und im Protokoll steht der Inhaltstyp - das
    Wort, das ihn zum Proxy schickt.
    """
    helfer_anbieter["token_antwort"] = (200, b"<html>Anmelden</html>", "text/html")
    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        fehler = await _tauschen()
    assert fehler.code == "oidc_provider_invalid"
    assert "text/html" in caplog.text


async def test_abgelehnter_tausch_nennt_error_und_beschreibung(
    helfer_anbieter: dict, caplog
) -> None:
    """RFC 6749 5.2: ``error`` und ``error_description`` sind die Diagnose.

    Sie nennen das falsche Feld beim Namen, statt "irgendwas mit 400". Wer sie
    wegwirft, laesst den Betreiber raten - und der haeufigste Fall ist eine
    abgetippte Client-ID.
    """
    helfer_anbieter["token_antwort"] = (
        401,
        b'{"error":"invalid_client","error_description":"client secret mismatch"}',
        "application/json",
    )
    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        fehler = await _tauschen()
    assert fehler.code == "oidc_exchange_failed"
    assert "invalid_client" in caplog.text
    assert "client secret mismatch" in caplog.text


async def test_zweihundert_ohne_id_token_nennt_die_feldnamen(
    helfer_anbieter: dict, caplog
) -> None:
    """Nur ``access_token`` und ``token_type``: der ``openid``-Scope fehlt.

    Der Satz stimmt hier - und erst die **Feldnamen** sagen, woran es liegt.
    Namen, keine Werte: In den Werten staenden Ausweise, und ein Protokoll
    wandert beim Melden eines Fehlers zu Fremden.
    """
    helfer_anbieter["token_antwort"] = (
        200,
        b'{"access_token":"geheim-und-echt","token_type":"Bearer"}',
        "application/json",
    )
    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        fehler = await _tauschen()
    assert fehler.code == "oidc_exchange_failed"
    assert "without an id_token" in caplog.text
    assert "access_token, token_type" in caplog.text
    assert "geheim-und-echt" not in caplog.text


# --- Adressen, an denen sich httpx schon beim Zerlegen verschluckt ----------


@pytest.mark.parametrize("feld", ["jwks_uri", "token_endpoint"])
async def test_unbrauchbare_adresse_ist_ein_einrichtungsfehler(
    helfer_anbieter: dict, feld: str, caplog
) -> None:
    """⚠️ ``httpx.InvalidURL`` erbt direkt von ``Exception``.

    Ein Faenger fuer ``httpx.HTTPError`` geht daran vorbei, und die Ausnahme
    entsteht schon beim **Zerlegen** der Adresse. Ohne den groben Faenger wird
    aus einem Tippfehler in der Anbieter-Beschreibung eine nackte 500 mitten
    im Rueckweg - oder aus dem Pruef-Knopf.
    """
    kaputt = {**helfer.beschreibung(), feld: helfer.KAPUTTE_ADRESSE}
    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        if feld == "jwks_uri":
            with pytest.raises(oidc.OidcFehler) as gefangen:
                await oidc.ausweis_pruefen(
                    kaputt, helfer.CLIENT_ID, helfer.ausweis(), "nonce-1"
                )
            kennung = gefangen.value.code
        else:
            kennung = (await _tauschen(kaputt)).code
    assert kennung == "oidc_provider_unreachable"
    assert "cannot be used at all" in caplog.text


async def test_weiterleitung_gilt_als_unerreichbar(monkeypatch, caplog) -> None:
    """Der gemeinsame Client folgt keiner Weiterleitung - und das ist gut so.

    Ein Portal vor dem Anbieter oder http statt https antwortet mit 302.
    Frueher lief das weiter und scheiterte spaeter als "unverstaendlich"; der
    Hinweis, der die Ursache nennt, ist ``location``.
    """

    def antworten(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://portal.beispiel.de/"})

    oidc.cache_leeren()
    monkeypatch.setattr(
        oidc, "_client", httpx.AsyncClient(transport=httpx.MockTransport(antworten))
    )
    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        with pytest.raises(oidc.OidcFehler) as fehler:
            await oidc.discovery(helfer.ISSUER)
    assert fehler.value.code == "oidc_provider_unreachable"
    assert "portal.beispiel.de" in caplog.text
    oidc.cache_leeren()


# --- Was das Protokoll beim Schluessel und beim nonce sagt ------------------


async def test_fehlender_schluessel_nennt_die_angebotenen_kids(
    helfer_anbieter: dict, caplog
) -> None:
    """Ohne die angebotenen Kennungen weiss der Betreiber nicht, ob er die
    falsche ``jwks_uri`` hat, ob der Satz leer ist oder ob nur der ``kid``
    nicht passt. Schluessel-Kennungen sind oeffentlich - sie stehen im selben
    Dokument."""
    von_gestern = jwt.encode(
        {
            "iss": helfer.ISSUER,
            "sub": "person-1",
            "aud": helfer.CLIENT_ID,
            "exp": int(time.time()) + 300,
        },
        _PRIVAT_PEM,
        algorithm="RS256",
        headers={"kid": "ein-kid-von-gestern"},
    )
    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        with pytest.raises(oidc.OidcFehler) as fehler:
            await oidc.ausweis_pruefen(
                helfer.beschreibung(), helfer.CLIENT_ID, von_gestern, "nonce-1"
            )
    assert fehler.value.code == "oidc_token_invalid"
    assert "ein-kid-von-gestern" in caplog.text
    # Und was der Anbieter stattdessen anbietet.
    assert helfer.KID_ES512 in caplog.text


async def test_ohne_kid_wird_bei_mehreren_schluesseln_nicht_geraten(
    helfer_anbieter: dict, caplog
) -> None:
    """Zu raten waere schlimmer als abzulehnen - und im Protokoll steht der
    Unterschied zum Fall "kid passt nicht"."""
    ohne_kid = jwt.encode(
        {
            "iss": helfer.ISSUER,
            "sub": "person-1",
            "aud": helfer.CLIENT_ID,
            "exp": int(time.time()) + 300,
        },
        _PRIVAT_PEM,
        algorithm="RS256",
    )
    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        with pytest.raises(oidc.OidcFehler):
            await oidc.ausweis_pruefen(
                helfer.beschreibung(), helfer.CLIENT_ID, ohne_kid, "nonce-1"
            )
    assert "names no kid" in caplog.text


@pytest.mark.parametrize(
    ("nonce", "erwartet"),
    [(None, "no nonce at all"), ("ein-anderer-lauf", "a different nonce")],
)
async def test_der_nonce_fehlschlag_unterscheidet_die_faelle(
    helfer_anbieter: dict, nonce: str | None, erwartet: str, caplog
) -> None:
    """Zwei sehr verschiedene Ursachen unter derselben Kennung.

    **Kein** nonce heisst, dass der Anbieter es nicht zurueckspiegelt (manche
    tun das nur mit passender Client-Einstellung) - eine Einstellungsfrage.
    Ein **anderes** heisst, dass der Ausweis aus einem fremden Lauf stammt.
    Die Werte selbst gehoeren nicht ins Protokoll.
    """
    with caplog.at_level(logging.WARNING, logger="nexview.oidc"):
        with pytest.raises(oidc.OidcFehler) as fehler:
            await oidc.ausweis_pruefen(
                helfer.beschreibung(),
                helfer.CLIENT_ID,
                helfer.ausweis(nonce=nonce),
                "nonce-1",
            )
    assert fehler.value.code == "oidc_token_invalid"
    assert erwartet in caplog.text
