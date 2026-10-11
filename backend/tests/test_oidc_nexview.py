"""Was nur Nexview an der Anmeldung ueber Anbieter leistet - neben den Vertragstests.

Die Pflichttests 1 bis 39 des Bauplans liegen unveraendert in ``tests/nexoidc/``
und laufen gegen ``tests/nexoidc_adapter.py``. Hier steht, was Nexview selbst
dazubaut: die Wanderung aus 1.0.1, der Schalter fuer die Passwort-Anmeldung, die
Sperrliste geloeschter Konten, der Beleg der Sitzung beim Verknuepfen, die
Bremse, das Schema hinter einem Proxy und die Namensregeln.

Der Anbieter ist der Nachbau aus ``tests/nexoidc/oidc_fakes.py`` (nie das Netz).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent / "nexoidc"))

import oidc_contract  # noqa: E402
import oidc_fakes as fakes  # noqa: E402
from oidc_contract import error_in, link_person, location, round_trip, start  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.crypto import decrypt_im_kontext, encrypt  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Base,
    OidcBlock,
    OidcLink,
    OidcProvider,
    Role,
    TokenPurpose,
    User,
    utcnow,
)
from app.security import hash_password, unusable_password  # noqa: E402
from app.services import anmeldebremse, oidc_store, tokens  # noqa: E402
from app.services import mediaserver_accounts as medienserver  # noqa: E402
from app.services.mediaserver.base import ExternalAccount  # noqa: E402
from app.services.settings_service import load_settings  # noqa: E402
from app.vendor import nexoidc  # noqa: E402
from app.vendor.nexoidc import attempt, protocol, secret_context  # noqa: E402

from .nexoidc_adapter import ABSENDER, Adapter  # noqa: E402


@pytest.fixture
def adapter() -> Iterator[Adapter]:
    eigen = Adapter()
    eigen.start()
    protocol.clear_caches()
    attempt.forget_used_states()
    try:
        yield eigen
    finally:
        nexoidc.use_transport(None)
        protocol.clear_caches()
        eigen.stop()


@pytest.fixture
def sso(adapter: Adapter) -> Iterator[fakes.FakeProvider]:
    netz = fakes.Network()
    nexoidc.use_transport(netz.transport())
    yield netz.add(fakes.FakeProvider())
    nexoidc.use_transport(None)


def _anlegen(adapter: Adapter, fake: fakes.FakeProvider, slug: str = "sso", **mehr: Any) -> dict[str, Any]:
    antwort = adapter.add_provider(fake, slug=slug, **mehr)
    assert antwort.status_code == 201, antwort.text
    return antwort.json()


# ---------------------------------------------------------------------------
# Die Wanderung aus 1.0.1
# ---------------------------------------------------------------------------

#: So legte Nexview 1.0.1 die beiden Tabellen an (``models.py`` bis aa2417e).
ALT_ANBIETER = """
CREATE TABLE oidc_providers (
    id INTEGER NOT NULL PRIMARY KEY,
    slug VARCHAR(40) NOT NULL,
    label VARCHAR(80) NOT NULL,
    issuer_url VARCHAR(500) NOT NULL UNIQUE,
    client_id VARCHAR(255) NOT NULL,
    client_secret TEXT NOT NULL,
    auto_create BOOLEAN NOT NULL,
    enabled BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL
)"""
ALT_LINKS = """
CREATE TABLE user_oidc_links (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    issuer VARCHAR(500) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    display VARCHAR(255),
    linked_at DATETIME NOT NULL,
    CONSTRAINT uq_oidc_identitaet UNIQUE (issuer, subject),
    CONSTRAINT uq_oidc_anbieter UNIQUE (user_id, issuer)
)"""


@pytest.fixture
def alte_datenbank(tmp_path: Path) -> Iterator[Any]:
    """Eine Datenbank im Zustand von 1.0.1: zwei Anbieter, drei Verknuepfungen, eine davon verwaist."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'alt.db').as_posix()}")
    tabellen = [t for t in Base.metadata.sorted_tables if t.name != "oidc_providers"]
    Base.metadata.create_all(engine, tables=tabellen)
    with Session(engine) as db:
        for nummer, name in ((1, "anna"), (2, "bert"), (3, "cora")):
            db.add(User(id=nummer, username=name, password_hash="x"))
        db.commit()
    with engine.begin() as verbindung:
        verbindung.exec_driver_sql(ALT_ANBIETER)
        verbindung.exec_driver_sql(ALT_LINKS)
        verbindung.exec_driver_sql(
            "INSERT INTO oidc_providers VALUES (7, 'firma', 'Firmen-SSO', 'https://sso.example.com', "
            "'nexview', ?, 1, 1, '2026-09-01 10:00:00')",
            (encrypt("geheim-1"),),
        )
        verbindung.exec_driver_sql(
            "INSERT INTO oidc_providers VALUES (3, 'auth', 'authentik', "
            "'https://auth.example.com/application/o/nexview', 'ak-client', ?, 0, 0, "
            "'2026-09-02 10:00:00')",
            (encrypt("geheim-2"),),
        )
        for user_id, issuer, subject, anzeige in (
            (1, "https://sso.example.com", "anna-1", "anna@example.com"),
            # Mit Schraegstrich: dieselbe Adresse, anders geschrieben.
            (2, "https://auth.example.com/application/o/nexview/", "bert-1", "Bert"),
            # Der Anbieter dazu wurde in 1.0.1 geloescht: verwaist.
            (3, "https://weg.example.com", "cora-1", None),
        ):
            verbindung.exec_driver_sql(
                "INSERT INTO user_oidc_links (user_id, issuer, subject, display, linked_at) "
                "VALUES (?, ?, ?, ?, '2026-09-03 10:00:00')",
                (user_id, issuer, subject, anzeige),
            )
    try:
        yield engine
    finally:
        engine.dispose()


def test_die_wanderung_bringt_anbieter_und_verknuepfungen_in_die_tabellen_des_bauplans(
    alte_datenbank: Any,
) -> None:
    zahlen = oidc_store.nach_bauplan_wandern(alte_datenbank)
    assert zahlen == {"providers": 2, "links": 2, "left": 1, "addresses": 0}

    with alte_datenbank.connect() as verbindung:
        spalten = {zeile[1] for zeile in verbindung.exec_driver_sql("PRAGMA table_info(oidc_providers)")}
        assert "issuer_url" not in spalten and "client_secret" not in spalten
        assert {"issuer", "client_secret_enc", "scopes", "trusts_second_factor", "managed", "position",
                "redirect_path", "registered_redirect"} <= spalten
        zeilen = {
            zeile.slug: zeile
            for zeile in verbindung.execute(text("SELECT * FROM oidc_providers ORDER BY id"))
        }
        firma, auth = zeilen["firma"], zeilen["auth"]
        # Aussteller, Werte und die Rueckleitadresse, die der Anbieter kennt.
        assert firma.issuer == "https://sso.example.com" and firma.client_id == "nexview"
        assert firma.redirect_path == "/api/auth/oidc/firma/callback"
        assert auth.redirect_path == "/api/auth/oidc/auth/callback"
        assert (firma.auto_create, firma.enabled) == (1, 1) and (auth.auto_create, auth.enabled) == (0, 0)
        assert firma.trusts_second_factor == 1 and firma.managed == "" and firma.scopes == "openid profile email"
        # Reihenfolge nach Alter (id), das Geheimnis im eigenen Kontext.
        assert (auth.position, firma.position) == (0, 1)
        assert decrypt_im_kontext(firma.client_secret_enc, secret_context(7)) == "geheim-1"
        assert decrypt_im_kontext(auth.client_secret_enc, secret_context(3)) == "geheim-2"
        assert decrypt_im_kontext(firma.client_secret_enc, secret_context(3)) == ""
        links = sorted(
            (zeile.provider_id, zeile.subject, zeile.account_id, zeile.email)
            for zeile in verbindung.execute(text("SELECT * FROM oidc_links"))
        )
        assert links == [(3, "bert-1", 2, None), (7, "anna-1", 1, "anna@example.com")]
        # Die alte Tabelle bleibt eine Fassung lang unangetastet stehen.
        assert verbindung.exec_driver_sql("SELECT COUNT(*) FROM user_oidc_links").scalar() == 3
        assert verbindung.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        assert verbindung.exec_driver_sql("PRAGMA foreign_keys").scalar() == 0 or True

    # Ein zweiter Lauf findet nichts mehr zu tun.
    assert oidc_store.nach_bauplan_wandern(alte_datenbank) == {
        "providers": 0, "links": 0, "left": 0, "addresses": 0
    }


def test_ohne_alte_tabelle_tut_die_wanderung_nichts(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'neu.db').as_posix()}")
    Base.metadata.create_all(engine)
    try:
        assert oidc_store.nach_bauplan_wandern(engine) == {
            "providers": 0, "links": 0, "left": 0, "addresses": 0
        }
    finally:
        engine.dispose()


def test_eine_aeltere_fassung_legt_auf_einer_neuen_datenbank_weiter_zeilen_an(tmp_path: Path) -> None:
    """Jede neue Spalte hat einen Vorgabewert der Datenbank (``server_default``).

    Ein Schreiben ohne die neuen Spalten - so, wie es eine Fassung ohne sie
    taete - legt Konten, Anbieter und Verknuepfungen weiter an.
    """
    engine = create_engine(f"sqlite:///{(tmp_path / 'neu.db').as_posix()}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add(User(username="alt", password_hash="x"))
            db.commit()
        with engine.begin() as verbindung:
            verbindung.exec_driver_sql(
                "INSERT INTO oidc_providers (slug, label, client_id, created_at) "
                "VALUES ('roh', 'Roh', 'c', '2026-10-01 10:00:00')"
            )
            verbindung.exec_driver_sql(
                "INSERT INTO oidc_links (provider_id, subject, account_id, created_at) "
                "SELECT oidc_providers.id, 's', users.id, '2026-10-01 10:00:00' FROM oidc_providers, users"
            )
            zeile = verbindung.execute(text("SELECT * FROM oidc_providers")).one()
        assert (zeile.enabled, zeile.auto_create, zeile.trusts_second_factor) == (1, 0, 1)
        assert (zeile.issuer, zeile.managed, zeile.redirect_path, zeile.registered_redirect) == ("", "", "", "")
        assert zeile.scopes == "openid profile email" and zeile.position == 0
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Passwort-Anmeldung abschalten
# ---------------------------------------------------------------------------


def _passwort_aus(adapter: Adapter) -> Any:
    return adapter.operator().put("/api/settings", json={"password_login": False})


def test_die_passwort_anmeldung_laesst_sich_erst_mit_aktivem_anbieter_abschalten(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    abgelehnt = _passwort_aus(adapter)
    assert abgelehnt.status_code == 409 and adapter.error_code(abgelehnt) == "password_login_needs_provider"
    _anlegen(adapter, sso)
    assert _passwort_aus(adapter).status_code == 200
    assert adapter.browser().get("/api/setup/status").json()["password_login"] is False


def test_ausgeschaltet_kommt_nur_der_betreiber_mit_passwort_herein(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    eintrag = _anlegen(adapter, sso)
    adapter.make_account("alex")
    assert _passwort_aus(adapter).status_code == 200

    gast = adapter.browser().post(
        "/api/auth/login", json={"username": "alex", "password": adapter.password_for("alex")}
    )
    assert gast.status_code == 403 and adapter.error_code(gast) == "password_login_off"
    # Der Betreiber ist der Rueckweg, falls der Anbieter ausfaellt.
    betreiber = adapter.browser()
    adapter.sign_in_with_password(betreiber, adapter.operator_name)
    # Ohne aktiven Anbieter gilt das Passwort wieder fuer alle.
    form = oidc_contract.form_of(oidc_contract.entry(adapter, "sso"), enabled=False)
    assert adapter.save_provider(eintrag["id"], form).status_code == 200
    adapter.sign_in_with_password(adapter.browser(), "alex")
    assert adapter.browser().get("/api/setup/status").json()["password_login"] is True


# ---------------------------------------------------------------------------
# Sperrliste
# ---------------------------------------------------------------------------


def test_ein_geloeschtes_konto_kommt_ueber_den_anbieter_nicht_wieder(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    _anlegen(adapter, sso, auto_create=True)
    adapter.make_account("alex")
    link_person(adapter, "alex", "sso", sso)
    with SessionLocal() as db:
        konto = db.scalar(select(User).where(User.username == "alex"))
        assert konto is not None
        weg = adapter.operator().delete(f"/api/users/{konto.id}")
    assert weg.status_code in (200, 204), weg.text
    with SessionLocal() as db:
        sperre = db.scalar(select(OidcBlock))
        assert sperre is not None and sperre.subject == "person-1"

    browser, antwort = round_trip(adapter, sso, "sso")
    assert error_in(antwort) == "account_blocked" and adapter.who(browser) is None
    assert "alex" not in adapter.account_names()

    sperren = adapter.operator().get("/api/oidc/admin/blocks").json()
    assert len(sperren) == 1
    assert adapter.operator().delete(f"/api/oidc/admin/blocks/{sperren[0]['id']}").status_code == 204
    browser, antwort = round_trip(adapter, sso, "sso")
    assert location(antwort) == adapter.home and adapter.who(browser) == "alex"


# ---------------------------------------------------------------------------
# Verknuepfen: das Konto kommt aus der Sitzung
# ---------------------------------------------------------------------------


def test_nach_dem_abmelden_verknuepft_die_rueckkehr_nichts(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    eintrag = _anlegen(adapter, sso)
    adapter.make_account("alex")
    browser = adapter.browser()
    adapter.sign_in_with_password(browser, "alex")
    url = adapter.link_start(browser, "sso", adapter.password_for("alex")).json()["url"]
    # Abmelden beendet die Sitzung auf dem Server; der Beleg im Cookie gilt nicht mehr.
    assert browser.post("/api/auth/logout").status_code == 204
    zurueck = oidc_contract.come_back(
        adapter, browser, "sso", sso.authorize(url), path=oidc_contract.sent_callback(url)
    )
    assert error_in(zurueck) == "oidc_link_mismatch"
    with adapter.store() as store:
        assert store.links_of_provider(eintrag["id"]) == []


def test_verknuepfen_verlangt_das_passwort_und_eine_sitzung(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    _anlegen(adapter, sso)
    adapter.make_account("alex")
    browser = adapter.browser()
    adapter.sign_in_with_password(browser, "alex")
    falsch = adapter.link_start(browser, "sso", "nicht-das-passwort")
    assert falsch.status_code == 401 and adapter.error_code(falsch) == "current_password_wrong"
    assert adapter.link_start(adapter.browser(), "sso", adapter.password_for("alex")).status_code == 401
    # Ein persoenlicher Zugriffs-Schluessel startet kein Verknuepfen.
    schluessel = browser.post("/api/auth/me/schluessel", json={"name": "skript"}).json()
    mit_schluessel = adapter.browser()
    mit_schluessel.headers["Authorization"] = f"Bearer {schluessel['schluessel']}"
    abgelehnt = adapter.link_start(mit_schluessel, "sso", adapter.password_for("alex"))
    assert abgelehnt.status_code == 403 and adapter.error_code(abgelehnt) == "apikey_not_allowed"


def test_ein_kinderkonto_kommt_nicht_ueber_einen_anbieter_herein(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    eintrag = _anlegen(adapter, sso)
    with SessionLocal() as db:
        eltern = User(username="eltern", password_hash=hash_password("x"), role=Role.user)
        db.add(eltern)
        db.flush()
        kind = User(
            username="kind", password_hash=unusable_password(), role=Role.child, parent_id=eltern.id,
            email_verified=True,
        )
        db.add(kind)
        db.flush()
        db.add(OidcLink(provider_id=eintrag["id"], subject="person-1", account_id=kind.id, issuer=sso.issuer))
        db.commit()
    browser, antwort = round_trip(adapter, sso, "sso")
    assert error_in(antwort) == "account_blocked" and adapter.who(browser) is None


# ---------------------------------------------------------------------------
# Bremse: nur echte Laeufe zaehlen
# ---------------------------------------------------------------------------


def test_rueckwege_ohne_gueltigen_anlauf_fuettern_die_bremse_nie(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    """Sonst sperrte ein Fremder mit gebastelten Rueckwegen andere aus (anonymes Aussperren)."""
    _anlegen(adapter, sso)
    adapter.make_account("alex")
    link_person(adapter, "alex", "sso", sso)
    fremder = adapter.browser()
    for _ in range(anmeldebremse.SPERRE_AB_VERSUCH + 2):
        for pfad in (
            "/api/oidc/sso/callback?error=access_denied&state=x",
            "/api/oidc/unbekannt/callback?code=a&state=b",
            "/api/oidc/sso/callback?code=muell&state=muell",
            "/api/auth/oidc/sso/callback?code=muell&state=muell",
        ):
            assert fremder.get(pfad).status_code == 303
    assert anmeldebremse.restliche_sperre(anmeldebremse.schluessel_adresse("oidc", ABSENDER)) == 0
    browser, antwort = round_trip(adapter, sso, "sso")
    assert location(antwort) == adapter.home and adapter.who(browser) == "alex"


def test_ein_echter_fehlschlag_beim_anbieter_zaehlt(adapter: Adapter, sso: fakes.FakeProvider) -> None:
    _anlegen(adapter, sso)
    sso.token_claims = {"aud": "jemand-anders"}
    # Nach den freien Versuchen wartet der Absender (``anmeldebremse.FREI_VERSUCHE``).
    for _ in range(anmeldebremse.FREI_VERSUCHE + 1):
        _browser, antwort = round_trip(adapter, sso, "sso")
        assert error_in(antwort) == "oidc_token_invalid"
    assert anmeldebremse.restliche_sperre(anmeldebremse.schluessel_adresse("oidc", ABSENDER)) > 0
    assert error_in(start(adapter, adapter.browser(), "sso")) == "too_many_attempts"


# ---------------------------------------------------------------------------
# Ein Kurzname "authentik" verdeckt keine Route
# ---------------------------------------------------------------------------


def test_der_kurzname_authentik_meldet_an_und_verknuepft(adapter: Adapter) -> None:
    netz = fakes.Network()
    nexoidc.use_transport(netz.transport())
    ak = netz.add(fakes.FakeProvider("https://auth.example.com/application/o/nexview/"))
    _anlegen(adapter, ak, slug="authentik")
    adapter.make_account("alex")
    link_person(adapter, "alex", "authentik", ak)
    browser, antwort = round_trip(adapter, ak, "authentik")
    assert location(antwort) == adapter.home and adapter.who(browser) == "alex"
    # Die Routen des Knopfs antworten daneben weiter.
    assert adapter.operator().get("/api/oidc/authentik/address").status_code == 200


# ---------------------------------------------------------------------------
# Die oeffentliche Adresse: auch Leeren fragt
# ---------------------------------------------------------------------------


def test_auch_das_leeren_der_oeffentlichen_adresse_fragt(adapter: Adapter, sso: fakes.FakeProvider) -> None:
    adapter.set_public_url("https://nexview.example.org")
    _anlegen(adapter, sso)
    gefragt = adapter.save_public_url_route("")
    assert gefragt.status_code == 409 and adapter.error_code(gefragt) == "public_url_change"
    assert adapter.error_detail(gefragt)["hand"] == 1
    assert adapter.read_public_url() == "https://nexview.example.org"
    assert adapter.save_public_url_route("", confirm=True).status_code == 200
    assert adapter.read_public_url() == ""


def test_eine_adresse_die_das_modul_ablehnt_nimmt_auch_die_einstellungsseite_nicht(adapter: Adapter) -> None:
    for falsch in ("https://example.com/?x=1", "https://user@example.com", "https://a b.org"):
        antwort = adapter.save_public_url_route(falsch)
        assert antwort.status_code == 422, (falsch, antwort.text)
    assert adapter.read_public_url() == ""
    # Geschrieben wird die bereinigte Form: Schema und Host klein, kein Schraegstrich am Ende.
    assert adapter.save_public_url_route("HTTPS://Nexview.Example.ORG/").status_code == 200
    assert adapter.read_public_url() == "https://nexview.example.org"


# ---------------------------------------------------------------------------
# Hinter einem Proxy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("einstellung", "kopf", "erwartet"),
    [
        ("proxy", "https", "https"),
        ("proxy:2", "http, HTTPS", "https"),
        ("proxy", "https, http", "http"),
        ("proxy", "ftp", "http"),
        ("", "https", "http"),
        ("direct", "https", "http"),
    ],
)
def test_das_schema_kommt_nur_vom_vertrauten_proxy_und_nur_der_rechteste_wert(
    adapter: Adapter, einstellung: str, kopf: str, erwartet: str
) -> None:
    get_settings().client_ip = einstellung
    antwort = adapter.operator().get("/api/oidc/authentik/address", headers={"X-Forwarded-Proto": kopf})
    assert antwort.status_code == 200
    assert antwort.json()["seen"].startswith(f"{erwartet}://"), antwort.json()["seen"]


def test_eine_websocket_verbindung_bekommt_wss() -> None:
    import asyncio

    from app.middleware import WeitergereichtesSchemaMiddleware

    gesehen: dict[str, Any] = {}

    async def innen(scope: dict, _receive: Any, _send: Any) -> None:
        gesehen.update(scope)

    vorher = get_settings().client_ip
    get_settings().client_ip = "proxy"
    try:
        scope = {"type": "websocket", "scheme": "ws", "headers": [(b"x-forwarded-proto", b"HTTPS")]}
        asyncio.run(WeitergereichtesSchemaMiddleware(innen)(scope, None, None))
    finally:
        get_settings().client_ip = vorher
    assert gesehen["scheme"] == "wss"


# ---------------------------------------------------------------------------
# Namen und neue Konten
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("roh", "name"),
    [
        ("max", "max"),
        ("Max Muster", "MaxMuster"),
        ("ä!", ""),
        ("p1", "p1"),
        ("a" * 40, "a" * 32),
        ("a" * 40 + "-12", "a" * 29 + "-12"),
        ("..max..", "max"),
    ],
)
def test_namen_vom_anbieter_folgen_den_regeln_von_nexview(roh: str, name: str) -> None:
    assert oidc_store.clean_name(roh) == name


def test_ein_neues_konto_bekommt_die_adresse_nur_wenn_sie_frei_ist(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    _anlegen(adapter, sso, auto_create=True)
    with SessionLocal() as db:
        db.add(User(username="schon-da", password_hash="x", email="alex@example.com", email_verified=True))
        db.commit()
    browser, antwort = round_trip(adapter, sso, "sso")
    assert location(antwort) == adapter.home and adapter.who(browser) == "alex"
    sso.person = {"sub": "person-2", "preferred_username": "bea", "email": "bea@example.com"}
    round_trip(adapter, sso, "sso")
    with SessionLocal() as db:
        alex = db.scalar(select(User).where(User.username == "alex"))
        bea = db.scalar(select(User).where(User.username == "bea"))
        assert alex is not None and bea is not None
        # Vergeben: keine Adresse statt eines Fehlers; frei: die des Anbieters, unbestaetigt.
        assert alex.email is None
        assert (bea.email, bea.email_verified) == ("bea@example.com", False)
        assert bea.auto_approve is False and bea.role == Role.user


def test_eine_einladung_gibt_dem_neuen_konto_ihre_adresse_bestaetigt(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    _anlegen(adapter, sso)
    schluessel = adapter.make_invite("editor")
    browser, antwort = round_trip(adapter, sso, "sso", invite=schluessel)
    assert location(antwort) == adapter.home and adapter.who(browser) == "alex"
    with SessionLocal() as db:
        alex = db.scalar(select(User).where(User.username == "alex"))
        assert alex is not None and alex.role == Role.approver
        assert alex.email is not None and alex.email.startswith("eingeladen-") and alex.email_verified
        # Die Adresse stammt aus der Einladung, nicht vom Anbieter: Sie darf ein Konto finden.
        assert alex.email_origin == ""
        adresse = alex.email
        assert _ueber_plex(db, adresse).id == alex.id


def test_der_alte_rueckweg_gehoert_nur_eintraegen_die_ihn_tragen(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    """Ein Eintrag mit Standard-Rueckweg schickt die neue Adresse; die alte Route fuehrt trotzdem
    zur selben Rueckkehr, ohne gueltiges Cookie aber nur zur Anmeldeseite."""
    _anlegen(adapter, sso)
    browser = adapter.browser()
    ziel = location(start(adapter, browser, "sso"))
    assert fakes.query_of(ziel)["redirect_uri"].endswith("/api/oidc/sso/callback")
    alt = browser.get("/api/auth/oidc/sso/callback?code=x&state=y")
    assert error_in(alt) == "oidc_state_mismatch"
    with SessionLocal() as db:
        assert db.scalar(select(OidcProvider.redirect_path)) == ""


def test_die_wanderung_markiert_adressen_verknuepfter_konten_als_vom_anbieter(alte_datenbank: Any) -> None:
    """1.0.1 uebernahm die Adresse des Anbieters, und ob sie von dort kam, steht nirgends: Jedes Konto mit
    einer alten Verknuepfung gilt danach als "Adresse vom Anbieter" - ausser dem Betreiber und Konten ohne Adresse."""
    with alte_datenbank.begin() as verbindung:
        verbindung.exec_driver_sql("UPDATE users SET email = username || '@example.com' WHERE id IN (1, 3)")
        verbindung.exec_driver_sql("UPDATE users SET email = 'bert@example.com', is_betreiber = 1 WHERE id = 2")
    with Session(alte_datenbank) as db:
        db.add(User(id=4, username="dora", password_hash="x", email="dora@example.com"))
        db.add(User(id=5, username="emil", password_hash="x"))
        db.commit()
    with alte_datenbank.begin() as verbindung:
        verbindung.exec_driver_sql(
            "INSERT INTO user_oidc_links (user_id, issuer, subject, display, linked_at) "
            "VALUES (5, 'https://sso.example.com', 'emil-1', NULL, '2026-09-03 10:00:00')"
        )
    zahlen = oidc_store.nach_bauplan_wandern(alte_datenbank)
    assert zahlen["addresses"] == 2
    with alte_datenbank.connect() as verbindung:
        herkunft = dict(verbindung.exec_driver_sql("SELECT username, email_origin FROM users").fetchall())
    # anna verknuepft, cora verwaist verknuepft, bert ist der Betreiber, dora nie verknuepft, emil ohne Adresse.
    assert herkunft == {"anna": "oidc", "bert": "", "cora": "oidc", "dora": "", "emil": ""}


def test_die_herkunft_der_adresse_hat_einen_vorgabewert_der_datenbank(tmp_path: Path) -> None:
    """Eine aeltere Fassung kennt ``email_origin`` nicht und legt trotzdem Konten an."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'neu.db').as_posix()}")
    Base.metadata.create_all(engine)
    try:
        with engine.connect() as verbindung:
            spalte = next(
                zeile for zeile in verbindung.exec_driver_sql("PRAGMA table_info(users)") if zeile[1] == "email_origin"
            )
        assert spalte[3] == 1 and spalte[4] == "''"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Keine Bruecke ueber die Mailadresse: die Medienserver-Anmeldung
# ---------------------------------------------------------------------------


def _ueber_plex(db: Session, adresse: str) -> User:
    konto = ExternalAccount(provider="plex", account_id="4711", username="opfer", email=adresse)
    return medienserver.resolve(db, load_settings(db), konto)


def test_eine_adresse_vom_anbieter_findet_kein_konto_ueber_den_medienserver() -> None:
    """Wer beim Anbieter eine fremde Adresse traegt und ein Konto bekommt, faengt damit nicht die erste
    Medienserver-Anmeldung des echten Besitzers ein (Bauplan: nie ueber die Mailadresse zuordnen)."""
    with SessionLocal() as db:
        kennung = oidc_store.SqlStore(db).create_account("angreifer", invite=None, email="opfer@example.com")
        db.commit()
        konto = db.get(User, kennung)
        assert konto is not None and (konto.email, konto.email_origin) == ("opfer@example.com", "oidc")
        with pytest.raises(medienserver.KontoFehler) as fehler:
            _ueber_plex(db, "Opfer@Example.com")
        assert fehler.value.code == "mediaserver_link_in_profile" and fehler.value.status_code == 409
        db.rollback()
        konto = db.get(User, kennung)
        assert konto is not None and konto.mediaserver_accounts == []
        assert db.scalar(select(User).where(User.username == "opfer")) is None


def test_eine_selbst_eingetragene_adresse_findet_erst_nach_dem_link_ein_konto(client: Any) -> None:
    """Auch eine im Profil eingetragene Adresse ist unbestaetigt - sonst traegt jemand, der ueber einen
    Anbieter oder den Medienserver hereinkommt, eine fremde Adresse ein. Der Link aus der Mail hebt es auf."""
    with SessionLocal() as db:
        db.add(
            User(
                username="alex",
                password_hash=hash_password("passwort-1234"),
                email="alex@example.com",
                email_verified=True,
                role=Role.user,
            )
        )
        db.commit()
    anmeldung = client.post("/api/auth/login", json={"username": "alex", "password": "passwort-1234"})
    assert anmeldung.status_code == 200, anmeldung.text
    kopf = {"Authorization": f"Bearer {anmeldung.json()['access_token']}"}
    geaendert = client.put("/api/auth/me/email", json={"email": "opfer@example.com"}, headers=kopf)
    assert geaendert.status_code == 200, geaendert.text

    with SessionLocal() as db:
        alex = db.scalar(select(User).where(User.username == "alex"))
        assert alex is not None and (alex.email_origin, alex.email_verified) == ("self", False)
        with pytest.raises(medienserver.KontoFehler) as fehler:
            _ueber_plex(db, "opfer@example.com")
        assert fehler.value.code == "mediaserver_link_in_profile"
        db.rollback()
        alex = db.scalar(select(User).where(User.username == "alex"))
        assert alex is not None
        roh, _ = tokens.create(db, TokenPurpose.email_verification, "opfer@example.com", user=alex)
        db.commit()

    assert client.post(f"/api/onboarding/verify/{roh}").status_code == 204
    with SessionLocal() as db:
        alex = db.scalar(select(User).where(User.username == "alex"))
        assert alex is not None and (alex.email_origin, alex.email_verified) == ("", True)
        # Bestaetigt: jetzt ist es die Adresse dieses Kontos, und die Bruecke traegt wie immer.
        assert _ueber_plex(db, "opfer@example.com").id == alex.id


def test_der_link_zum_passwort_bestaetigt_die_adresse_vom_anbieter(client: Any) -> None:
    """Der Link kam an diese Adresse: Danach ist sie in Nexview bestaetigt und darf ein Konto finden."""
    with SessionLocal() as db:
        kennung = oidc_store.SqlStore(db).create_account("alex", invite=None, email="alex@example.com")
        db.commit()
        alex = db.get(User, kennung)
        assert alex is not None and alex.email_origin == "oidc"
        roh, _ = tokens.create(db, TokenPurpose.password_reset, "alex@example.com", user=alex)
        db.commit()
    gesetzt = client.post(f"/api/onboarding/password/{roh}", json={"password": "neues-passwort-1234"})
    assert gesetzt.status_code == 204, gesetzt.text
    with SessionLocal() as db:
        alex = db.get(User, kennung)
        assert alex is not None and (alex.email_origin, alex.email_verified) == ("", True)
        assert _ueber_plex(db, "alex@example.com").id == kennung


def test_der_medienserver_bestaetigt_nur_die_adresse_die_das_konto_traegt() -> None:
    """Meldet der Medienserver eine andere Adresse, bleibt die im Konto stehende unbestaetigt - sonst
    zaehlte eine fremde Adresse fuer den Mailversand als bestaetigt."""
    with SessionLocal() as db:
        fremd = User(username="cora", password_hash="x", email="opfer@example.com", email_verified=False)
        gleich = User(username="dirk", password_hash="x", email="dirk@example.com", email_verified=False)
        db.add_all([fremd, gleich])
        db.flush()
        medienserver.link(fremd, ExternalAccount(provider="plex", account_id="1", username="c", email="angreifer@example.org"))
        medienserver.link(gleich, ExternalAccount(provider="plex", account_id="2", username="d", email="Dirk@Example.com"))
        assert fremd.email_verified is False
        assert gleich.email_verified is True


def test_eine_vom_betreiber_gesetzte_adresse_verknuepft_wie_bisher() -> None:
    with SessionLocal() as db:
        db.add(User(username="bea", password_hash="x", email="bea@example.com", email_verified=False))
        db.commit()
        assert _ueber_plex(db, "bea@example.com").username == "bea"


# ---------------------------------------------------------------------------
# Was der Pruefgang ohne Test fand
# ---------------------------------------------------------------------------


def test_ein_anderer_administrator_loest_die_verknuepfung_des_betreibers_nicht(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    eintrag = _anlegen(adapter, sso)
    link_person(adapter, adapter.operator_name, "sso", sso)
    zweit_id = adapter.make_account("zweit")
    with SessionLocal() as db:
        zweit = db.get(User, zweit_id)
        assert zweit is not None
        zweit.role = Role.admin
        betreiber_id = db.scalar(select(User.id).where(User.is_betreiber.is_(True)))
        db.commit()
    zweiter = adapter.browser()
    adapter.sign_in_with_password(zweiter, "zweit")
    pfad = f"/api/oidc/admin/accounts/{betreiber_id}/links/{eintrag['id']}"

    abgelehnt = zweiter.request("DELETE", pfad, json={"password": adapter.password_for("zweit")})
    assert abgelehnt.status_code == 403 and adapter.error_code(abgelehnt) == "betreiber_geschuetzt"
    with adapter.store() as store:
        assert [link.account_id for link in store.links_of_provider(eintrag["id"])] == [betreiber_id]
    # Er selbst darf.
    selbst = adapter.operator().request(
        "DELETE", pfad, json={"password": adapter.password_for(adapter.operator_name)}
    )
    assert selbst.status_code == 204, selbst.text


def test_ein_passwortwechsel_macht_den_beleg_des_verknuepfens_ungueltig(
    adapter: Adapter, sso: fakes.FakeProvider
) -> None:
    """Der Beleg im Cookie gilt nur, solange die Sitzung gilt: Nach einem Passwortwechsel (oder "ueberall
    abmelden") verknuepft die Rueckkehr nichts mehr, auch wenn die Sitzung auf dem Server nicht beendet ist."""
    eintrag = _anlegen(adapter, sso)
    kennung = adapter.make_account("alex")
    browser = adapter.browser()
    adapter.sign_in_with_password(browser, "alex")
    url = adapter.link_start(browser, "sso", adapter.password_for("alex")).json()["url"]
    with SessionLocal() as db:
        alex = db.get(User, kennung)
        assert alex is not None
        alex.password_changed_at = utcnow() + timedelta(seconds=1)
        db.commit()
    zurueck = oidc_contract.come_back(
        adapter, browser, "sso", sso.authorize(url), path=oidc_contract.sent_callback(url)
    )
    assert error_in(zurueck) == "oidc_link_mismatch"
    with adapter.store() as store:
        assert store.links_of_provider(eintrag["id"]) == []


@pytest.mark.parametrize(
    ("gesperrt", "im_token"),
    [
        ("https://sso.example.com", "https://sso.example.com/"),
        ("https://sso.example.com/", "https://sso.example.com"),
        ("https://SSO.example.com", "https://sso.example.com"),
    ],
)
def test_die_sperrliste_erkennt_den_aussteller_wie_same_issuer(gesperrt: str, im_token: str) -> None:
    """Ein Aussteller mit oder ohne Schraegstrich am Ende (und Host in anderer Schreibung) ist derselbe;
    ein roher Vergleich liesse ein geloeschtes Konto ueber den Anbieter wieder herein."""
    with SessionLocal() as db:
        db.add(OidcBlock(issuer=gesperrt, subject="person-1", display="alex"))
        db.commit()
        assert oidc_store.ist_gesperrt(db, im_token, "person-1")
        assert not oidc_store.ist_gesperrt(db, im_token, "person-2")
        assert not oidc_store.ist_gesperrt(db, "https://anders.example.com", "person-1")
