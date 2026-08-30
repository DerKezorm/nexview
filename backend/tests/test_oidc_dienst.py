"""OIDC, Stufe 1: das Protokoll und die Kontologik - ohne echten Anbieter.

Der "Anbieter" ist hier eine Attrappe aus drei Antworten (Selbstauskunft,
Schluessel, Token-Tausch), unterschrieben mit einem im Test erzeugten
RSA-Schluessel. Genau so laesst sich OIDC ohne Netz pruefen - was gegen die
Attrappe besteht und gegen zwei echte Anbieter auf der Spielwiese lief, traegt
der Standard.
"""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.db import SessionLocal
from app.models import AuthToken, OidcBlock, Role, TokenPurpose, User, utcnow
from app.security import has_usable_password, hash_password
from app.services import oidc, oidc_accounts
from app.services.mediaserver_accounts import KontoFehler
from app.services.settings_service import load_settings

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
        "irgendein-geheimnis",
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
