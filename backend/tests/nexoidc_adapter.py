"""nexoidc-Vertragstests: Nexviews Anbindung (Vorlage ``bauplaene/oidc/tests-fuer-apps/adapter_template.py``).

Die Vertragstests in ``tests/nexoidc/`` sind in jeder App gleich und werden nie
hier geaendert; sie rufen nur, was hier und in ``StandardAdapter`` steht.

Nexview hat eine Datenbank je Testlauf (``tests/conftest.py``), die
``clean_db`` vor jedem Test leert. ``start`` legt darauf den Betreiber an.

⚠️ Die Anmeldebremse zaehlt die Adresse des Absenders nur, wenn
``NEXVIEW_CLIENT_IP`` sagt, woher sie kommt. Fuer die Tests steht sie auf
``proxy``, und jeder Testbrowser kommt mit derselben erfundenen Adresse aus
dem Dokumentationsnetz (``X-Forwarded-For``) - so wie hinter einem Proxy.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from fastapi.testclient import TestClient
from oidc_contract import StandardAdapter
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Role, TokenPurpose, User
from app.security import hash_password, unusable_password
from app.services import anmeldebremse, oidc_store, tokens
from app.services.settings_service import load_settings, save_settings

#: Die Adresse, von der jeder Testbrowser kommt (RFC 5737, Dokumentationsnetz).
ABSENDER = "203.0.113.9"
#: Die Rechte der Vertragstests in Nexviews Rollen.
ROLLEN = {"member": Role.user, "editor": Role.approver}
NAMEN = {Role.user: "member", Role.approver: "editor", Role.admin: "operator"}


class Adapter(StandardAdapter):
    package = "app.vendor.nexoidc"
    api = "/api"
    single_account = False
    only_authentik = False
    supports_coupling = True
    # Nexview hat keinen eigenen zweiten Faktor.
    has_second_factor = False
    legacy_model = "list"
    old_callback_paths: tuple[str, ...] = ("/api/auth/oidc/{slug}/callback",)
    operator_name = "betreiber"
    origin = "http://testserver"
    anonymous_status: tuple[int, ...] = (401,)
    member_status: tuple[int, ...] = (403,)
    home = "/"
    login_page = "/login"
    account_page = "/profil"

    def start(self) -> None:
        self._client_ip = get_settings().client_ip
        get_settings().client_ip = "proxy"
        oidc_store.configure()
        anmeldebremse.zuruecksetzen()
        self._operator: TestClient | None = None
        with SessionLocal() as db:
            db.add(
                User(
                    username=self.operator_name,
                    password_hash=hash_password(self.password_for(self.operator_name)),
                    email="betreiber@example.com",
                    email_verified=True,
                    role=Role.admin,
                    is_betreiber=True,
                )
            )
            db.commit()

    def stop(self) -> None:
        get_settings().client_ip = self._client_ip
        anmeldebremse.zuruecksetzen()

    def browser(self) -> TestClient:
        return TestClient(
            app,
            base_url="http://testserver",
            follow_redirects=False,
            headers={"X-Forwarded-For": ABSENDER},
        )

    def operator(self) -> TestClient:
        if self._operator is None:
            self._operator = self.browser()
            self.sign_in_with_password(self._operator, self.operator_name)
        return self._operator

    def make_account(self, name: str, *, password: bool = True, second_factor: bool = False) -> int:
        with SessionLocal() as db:
            row = User(
                username=name,
                password_hash=hash_password(self.password_for(name)) if password else unusable_password(),
                role=Role.user,
                # Ohne Adresse; "bestaetigt" nur, damit die Anmeldung mit
                # Passwort sie nicht verlangt (Nexview prueft das dort).
                email_verified=True,
            )
            db.add(row)
            db.commit()
            return row.id

    def sign_in_with_password(self, client: Any, name: str) -> None:
        response = client.post(
            "/api/auth/login", json={"username": name, "password": self.password_for(name)}
        )
        assert response.status_code == 200, response.text
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    def who(self, client: Any) -> str | None:
        # Nach einer Anmeldung ueber den Anbieter traegt der Browser das
        # Erneuerungs-Cookie; die Oberflaeche holt sich damit ihr Token.
        erneuert = client.post("/api/auth/refresh")
        if erneuert.status_code == 200:
            # Wie die Oberflaeche: das Token bleibt fuer die naechsten Anfragen.
            client.headers["Authorization"] = f"Bearer {erneuert.json()['access_token']}"
            antwort = client.get("/api/auth/me")
        elif "Authorization" in client.headers:
            antwort = client.get("/api/auth/me")
        else:
            return None
        return antwort.json()["username"] if antwort.status_code == 200 else None

    def second_factor_asked(self, client: Any, response: httpx.Response) -> bool:
        return False

    def make_invite(self, rights: str) -> str:
        with SessionLocal() as db:
            roh, _ = tokens.create(
                db,
                TokenPurpose.invitation,
                f"eingeladen-{secrets.token_hex(4)}@example.com",
                invite_role=ROLLEN[rights],
            )
        return roh

    def rights_of(self, name: str) -> str | None:
        with SessionLocal() as db:
            row = db.scalar(select(User).where(User.username == name))
            return NAMEN.get(row.role) if row else None

    def account_names(self) -> list[str]:
        with SessionLocal() as db:
            return sorted(db.scalars(select(User.username)))

    @contextmanager
    def _store(self) -> Iterator[oidc_store.SqlStore]:
        with SessionLocal() as db:
            yield oidc_store.SqlStore(db)

    def store(self) -> Any:
        return self._store()

    def trip_throttle(self, client: Any) -> None:
        for _ in range(anmeldebremse.SPERRE_AB_VERSUCH):
            anmeldebremse.fehlschlag(anmeldebremse.schluessel_adresse("oidc", ABSENDER))

    def set_public_url(self, url: str) -> None:
        with SessionLocal() as db:
            save_settings(db, {"public_url": url})

    def read_public_url(self) -> str:
        with SessionLocal() as db:
            return load_settings(db, frisch=True).public_url

    def save_public_url_route(self, url: str, *, confirm: bool = False) -> httpx.Response:
        """Karte 1 speichert ueber Nexviews eigene Einstellungsroute (``PUT /api/settings``)."""
        return self.operator().put(
            "/api/settings",
            params={"confirm_address": "true"} if confirm else {},
            json={"public_url": url},
        )


__all__ = ["Adapter"]
