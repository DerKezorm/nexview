"""Nexviews Seite des gemeinsamen Anmeldemoduls ``vendor/nexoidc``.

Das Modul (Bauplan "Anmeldung mit OIDC und authentik", in allen nex-Apps
gleich) fuehrt das Protokoll, entscheidet, wer eine Identitaet ist, und fuehrt
die Anbieterliste. Eine Datenbank sieht es nie. Was Nexview gehoert, steht
hier: der ``Store`` ueber ``oidc_providers`` und ``oidc_links``, Konten samt
Namen, die Rechte einer Einladung, die Verschluesselung der Client-Geheimnisse
und die Sperrliste geloeschter Konten.

Ein ``SqlStore`` arbeitet in einer SQLAlchemy-Sitzung, also einer Anfrage. Das
Modul committet, sobald ein Ergebnis feststeht, und rollt zurueck, wenn es nach
einem Schreiben ablehnt (eine verbrauchte Einladung kommt so zurueck).

⚠️ **Zugeordnet wird nur ueber ``(provider_id, subject)``.** Die Bruecke ueber
eine bestaetigte Mailadresse, die Nexview bis 1.0.1 hatte, gibt es nicht mehr -
auch nicht bei Einladungen. Eine Einladung reist als Schluessel im Anlauf-Cookie
zum Anbieter und zurueck (``/api/oidc/<slug>/start?invite=``).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..crypto import decrypt_im_kontext, encrypt_im_kontext
from ..db import SessionLocal
from ..models import (
    AuthToken,
    FassungRecht,
    NotificationType,
    OidcBlock,
    OidcLink,
    OidcProvider,
    Role,
    TokenPurpose,
    User,
    utcnow,
)
from ..security import has_usable_password, unusable_password
from ..vendor import nexoidc
from ..vendor.nexoidc import AccountState, Invite, Link, Provider, ProviderValues, secret_context
from ..vendor.nexoidc.protocol import same_issuer
from . import einladungen, notify, tokens
from .settings_service import load_settings, save_settings

logger = logging.getLogger("nexview.oidc")

APP_NAME = "nexview"
#: Die Rueckleitadresse, die Anbieter von Nexview bis 1.0.1 kannten. Die
#: Eintraege von damals tragen sie als ``redirect_path``; die Route dazu steht
#: in ``routers/oidc.py`` und fuehrt auf dieselbe Rueckkehr.
ALTER_RUECKWEG = "/api/auth/oidc/{slug}/callback"
#: Benutzernamen wie ueberall in Nexview (``mediaserver_accounts._unique_username``).
_NAMENSZEICHEN = re.compile(r"[^A-Za-z0-9_.-]")
#: Wie ``schemas.USERNAME_PATTERN`` lang sein darf. Kuerzer als dessen drei
#: Zeichen darf ein Name vom Anbieter sein (``p1``): Er ist, wie die Person dort
#: heisst, und das Formular, das drei verlangt, fuellt hier niemand aus.
NAME_MAX = 32
NAME_MIN = 1
#: Rollen in der Reihenfolge, in der sie wachsen (Kinderkonten entstehen nie
#: ueber einen Anbieter).
_ROLLEN = (Role.user, Role.approver, Role.admin)


def _basis() -> str:
    """Der Unterpfad (``NEXVIEW_URL_BASE``), aus Sicht des Browsers."""
    return get_settings().url_base.rstrip("/")


def _public_url() -> str:
    """Die oeffentliche Adresse aus den Einstellungen, frisch gelesen."""
    with SessionLocal() as db:
        return load_settings(db, frisch=True).public_url


def _save_public_url(url: str) -> None:
    """Eine in der authentik-Karte bestaetigte Adresse speichern (Modul 1.0.10).

    Dieselbe Einstellung, die die Seite "Adresse" schreibt und ``_public_url``
    liest - in eigener Transaktion, sofort festgeschrieben.
    """
    with SessionLocal() as db:
        save_settings(db, {"public_url": url})


def _server_secret() -> str:
    return get_settings().resolved_secret_key()


def configure() -> None:
    """Dem Modul sagen, in welcher App es laeuft - beim Start und in den Tests.

    Anmelde- und Kontoseite tragen den Unterpfad, denn das Modul leitet den
    Browser dorthin weiter.
    """
    basis = _basis()
    nexoidc.configure(
        nexoidc.AppConfig(
            app_name=APP_NAME,
            server_secret=_server_secret,
            public_url=_public_url,
            save_public_url=_save_public_url,
            login_page=f"{basis}/login",
            account_page=f"{basis}/profil",
            home=f"{basis}/",
        )
    )


def anfrage_basis(request_base: str) -> str:
    """Die Adresse dieser Anfrage samt Unterpfad - so, wie der Browser sie sieht.

    ``BasisPfadMiddleware`` streift den Unterpfad vom Pfad, ``request.base_url``
    kennt ihn also nicht. Das Modul baut daraus die Rueckleitadresse, solange
    keine oeffentliche Adresse eingestellt ist.
    """
    return request_base.rstrip("/") + _basis()


def clean_name(raw: str) -> str:
    """Ein Benutzername aus dem, was der Anbieter vorschlaegt.

    Dieselben Zeichen wie ueberall in Nexview, hoechstens 32; bleibt nichts
    uebrig, nimmt das Modul "user". Eine Nummer am Ende (``max-2``) ueberlebt
    das Kuerzen.
    """
    sauber = _NAMENSZEICHEN.sub("", raw or "").strip("._-")
    if len(sauber) > NAME_MAX:
        nummer = re.search(r"-\d+$", sauber)
        ende = nummer.group(0) if nummer else ""
        sauber = sauber[: NAME_MAX - len(ende)].rstrip("._-") + ende
    return sauber if len(sauber) >= NAME_MIN else ""


def anderer_weg_hinein(user: User) -> bool:
    """Kommt dieses Konto auch ohne seine Anbieter herein?

    Ein nutzbares Passwort oder eine Medienserver-Verknuepfung - ein Weg, den
    das Konto heute hat. Das Modul nennt die Frage ``has_password`` und haelt
    danach die letzte Verknuepfung fest (Bauplan 01, ``oidc_only_account``).
    Eine bestaetigte Adresse zaehlt nicht mit: "Passwort vergessen" fuehrt erst
    zu einem Weg, und bis dahin waere das Konto zu.
    """
    return has_usable_password(user.password_hash) or bool(user.mediaserver_accounts)


def anbieter_aktiv(db: Session) -> bool:
    """Ist mindestens ein Anmelde-Anbieter eingeschaltet?"""
    return db.scalar(select(OidcProvider.id).where(OidcProvider.enabled.is_(True)).limit(1)) is not None


def passwort_anmeldung(db: Session) -> bool:
    """Gilt die Anmeldung mit Passwort gerade fuer alle?

    Abgeschaltet nur, solange mindestens ein Anbieter aktiv ist (Bauplan 01,
    "Passwort-Anmeldung"): Faellt der letzte Anbieter weg oder geht aus, gilt
    das Passwort wieder, sonst kaeme niemand mehr herein. Der Betreiber kann
    sich immer mit Passwort anmelden - das prueft ``routers/auth.login``.
    """
    return load_settings(db).password_login or not anbieter_aktiv(db)


# ---------------------------------------------------------------------------
# Sperrliste: geloeschte Konten kommen ueber den Anbieter nicht wieder
# ---------------------------------------------------------------------------


def ist_gesperrt(db: Session, issuer: str, subject: str) -> bool:
    """Steht diese Identitaet auf der Sperrliste?

    Verglichen wird mit ``same_issuer``: ein Aussteller mit oder ohne
    Schraegstrich am Ende ist derselbe.
    """
    for zeile in db.scalars(select(OidcBlock).where(OidcBlock.subject == subject)):
        if same_issuer(zeile.issuer, issuer):
            return True
    return False


def sperren(db: Session, user: User, *, by: int | None = None) -> None:
    """Die Identitaeten eines Kontos sperren - beim Loeschen des Kontos.

    Sonst waere das Loeschen wirkungslos: Mit "Neue Personen bekommen ein
    Konto" meldete sich dieselbe Person sofort wieder an. Gesperrt wird an
    Aussteller und Kennung; die Sperre ueberlebt damit auch das Entfernen des
    Anbieters. Aufheben kann sie der Betreiber in der Sperrliste.
    """
    for link in user.oidc_links:
        issuer = link.issuer or (link.provider.issuer if link.provider else "")
        if not issuer or ist_gesperrt(db, issuer, link.subject):
            continue
        db.add(
            OidcBlock(
                issuer=issuer,
                subject=link.subject,
                display=link.email or user.username,
                blocked_by=by,
            )
        )


# ---------------------------------------------------------------------------
# Der Store
# ---------------------------------------------------------------------------


class SqlStore:
    """``nexoidc.Store`` ueber ``oidc_providers`` und ``oidc_links``, in einer Sitzung."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Anbieter ---------------------------------------------------------------------------

    def _provider(self, row: OidcProvider | None) -> Provider | None:
        if row is None:
            return None
        return Provider(
            id=row.id,
            slug=row.slug,
            label=row.label,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret=decrypt_im_kontext(row.client_secret_enc, secret_context(row.id)),
            scopes=row.scopes,
            enabled=row.enabled,
            auto_create=row.auto_create,
            trusts_second_factor=row.trusts_second_factor,
            managed=row.managed,
            position=row.position,
            redirect_path=row.redirect_path,
            created_at=row.created_at,
            registered_redirect=row.registered_redirect or "",
        )

    def list_providers(self) -> list[Provider]:
        rows = self.db.scalars(
            select(OidcProvider).order_by(OidcProvider.position, OidcProvider.id)
        )
        return [eintrag for eintrag in (self._provider(row) for row in rows) if eintrag]

    def get_provider(self, provider_id: int) -> Provider | None:
        return self._provider(self.db.get(OidcProvider, provider_id))

    def provider_by_slug(self, slug: str) -> Provider | None:
        return self._provider(
            self.db.scalar(select(OidcProvider).where(OidcProvider.slug == slug))
        )

    @staticmethod
    def _apply(row: OidcProvider, values: ProviderValues) -> None:
        row.slug, row.label, row.issuer = values.slug, values.label, values.issuer
        row.client_id, row.scopes = values.client_id, values.scopes
        row.enabled, row.auto_create = values.enabled, values.auto_create
        row.trusts_second_factor, row.managed = values.trusts_second_factor, values.managed
        row.position, row.redirect_path = values.position, values.redirect_path
        if values.registered_redirect is not None:
            # None behaelt, was der Knopf zuletzt eingetragen hat; nur er schreibt.
            row.registered_redirect = values.registered_redirect

    def insert_provider(self, values: ProviderValues) -> Provider:
        row = OidcProvider()
        self._apply(row, values)
        self.db.add(row)
        self.db.flush()
        row.client_secret_enc = encrypt_im_kontext(values.client_secret or "", secret_context(row.id))
        self.db.flush()
        eintrag = self._provider(row)
        assert eintrag is not None
        return eintrag

    def update_provider(self, provider_id: int, values: ProviderValues) -> Provider:
        row = self.db.get(OidcProvider, provider_id)
        assert row is not None
        self._apply(row, values)
        if values.client_secret is not None:
            row.client_secret_enc = encrypt_im_kontext(values.client_secret, secret_context(row.id))
        self.db.flush()
        eintrag = self._provider(row)
        assert eintrag is not None
        return eintrag

    def delete_provider(self, provider_id: int) -> None:
        # Fremdschluessel sind an (db.py), die Verknuepfungen gingen ohnehin mit;
        # hier ausdruecklich, damit nichts an einem Pragma haengt.
        self.drop_links(provider_id)
        self.db.execute(delete(OidcProvider).where(OidcProvider.id == provider_id))
        self.db.expire_all()

    # --- Verknuepfungen ---------------------------------------------------------------------

    @staticmethod
    def _link(row: OidcLink) -> Link:
        return Link(
            provider_id=row.provider_id,
            subject=row.subject,
            account_id=row.account_id,
            issuer=row.issuer,
            email=row.email,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
        )

    def find_link(self, provider_id: int, subject: str) -> Link | None:
        row = self.db.scalar(
            select(OidcLink).where(OidcLink.provider_id == provider_id, OidcLink.subject == subject)
        )
        return self._link(row) if row else None

    def links_of_provider(self, provider_id: int) -> list[Link]:
        rows = self.db.scalars(
            select(OidcLink).where(OidcLink.provider_id == provider_id).order_by(OidcLink.id)
        )
        return [self._link(row) for row in rows]

    def links_of_account(self, account_id: int) -> list[Link]:
        rows = self.db.scalars(
            select(OidcLink).where(OidcLink.account_id == account_id).order_by(OidcLink.id)
        )
        return [self._link(row) for row in rows]

    def add_link(self, link: Link) -> None:
        self.db.add(
            OidcLink(
                provider_id=link.provider_id,
                subject=link.subject,
                account_id=link.account_id,
                issuer=link.issuer,
                email=link.email,
            )
        )
        self.db.flush()

    def remove_link(self, provider_id: int, account_id: int) -> bool:
        result = self.db.execute(
            delete(OidcLink).where(
                OidcLink.provider_id == provider_id, OidcLink.account_id == account_id
            )
        )
        self.db.expire_all()
        return bool(result.rowcount)

    def drop_links(self, provider_id: int) -> int:
        result = self.db.execute(delete(OidcLink).where(OidcLink.provider_id == provider_id))
        self.db.expire_all()
        return int(result.rowcount or 0)

    def touch_link(self, provider_id: int, subject: str, email: str | None) -> None:
        self.db.execute(
            update(OidcLink)
            .where(OidcLink.provider_id == provider_id, OidcLink.subject == subject)
            .values(last_used_at=utcnow().replace(tzinfo=None), email=email)
        )

    # --- Konten -----------------------------------------------------------------------------

    def account_state(self, account_id: int) -> AccountState | None:
        user = self.db.get(User, account_id)
        if user is None:
            return None
        return AccountState(
            id=user.id,
            name=user.username,
            # Ein abgeschaltetes Konto kommt nicht herein, auch nicht ueber einen
            # Anbieter; Kinderkonten haben dort kein Gegenstueck.
            blocked=not user.is_active or user.role == Role.child,
            has_password=anderer_weg_hinein(user),
            # Nexview hat keinen eigenen zweiten Faktor.
            has_second_factor=False,
        )

    def clean_name(self, raw: str) -> str:
        return clean_name(raw)

    def name_taken(self, name: str) -> bool:
        return (
            self.db.scalar(select(User.id).where(func.lower(User.username) == name.lower()))
            is not None
        )

    def _adresse_frei(self, adresse: str | None) -> str | None:
        """Eine Adresse fuer ein neues Konto - oder None, wenn sie vergeben ist."""
        if not adresse:
            return None
        normal = tokens.normalize_email(adresse)
        if not normal or "@" not in normal:
            return None
        if self.db.scalar(select(User.id).where(User.email == normal)) is not None:
            return None
        return normal

    def create_account(self, name: str, *, invite: Invite | None, email: str | None) -> int:
        """Ein Konto, das sich nur ueber einen Anbieter anmeldet.

        Mit Einladung bekommt es deren Rechte und deren Adresse (wer den Link
        aus der Mail hat, hat das Postfach - wie beim Formular hinter dem
        Link). Ohne Einladung ist es ein gewoehnlicher Benutzer; die Adresse
        des Anbieters gilt als unbestaetigt, und Anfragen brauchen eine
        Freigabe.
        """
        settings = load_settings(self.db)
        token = self._einladung(invite)
        entfallen: list[str] = []
        if token is not None:
            wunsch, bewertung, _ = einladungen.bewerten(self.db, token)
            vorgaben = einladungen.kontowerte(token, wunsch, bewertung)
            entfallen = bewertung.entfallen(wunsch)
            adresse, bestaetigt = self._adresse_frei(token.email), True
        else:
            vorgaben = {
                "role": Role.user,
                "quota_movies_limit": None,
                "quota_series_limit": None,
                "blocked_movie_profiles": "",
                "blocked_series_profiles": "",
            }
            adresse, bestaetigt = self._adresse_frei(email), False
        user = User(
            username=name,
            password_hash=unusable_password(),
            email=adresse,
            email_verified=bool(adresse) and bestaetigt,
            display_name=name,
            language=settings.default_language,
            auto_approve=False,
            **vorgaben,
        )
        self.db.add(user)
        self.db.flush()
        if token is not None:
            einladungen.abschliessen(self.db, token, user, entfallen)
        else:
            notify.create_for_admins(
                self.db,
                kind=NotificationType.user_imported,
                message_key="notifications.userImported",
                title=user.display_name or user.username,
            )
        return user.id

    # --- Einladungen ------------------------------------------------------------------------

    def _einladung(self, invite: Invite | None) -> AuthToken | None:
        if invite is None:
            return None
        return self.db.get(AuthToken, int(invite.id)) if invite.id else None

    def find_invite(self, key: str) -> Invite | None:
        """Eine offene Einladung zu diesem Schluessel (dem aus dem Link).

        Eine Einladung, die Zugang zu einem Medienserver vergibt, gilt nur ueber
        den Link in der Mail: Nur das Formular dahinter legt die Konten dort an
        (``einladungen.nur_ueber_den_link``). Ueber einen Anbieter ist sie
        deshalb ungueltig, statt still weniger zu geben.
        """
        if not key:
            return None
        token = tokens.find(self.db, key, TokenPurpose.invitation)
        if token is None or einladungen.nur_ueber_den_link(token):
            return None
        return Invite(key=key, id=token.id, rights=token.invite_role or Role.user)

    def consume_invite(self, invite: Invite) -> bool:
        """Verbrauchen mit einer Anweisung, die genau eine Zeile treffen muss."""
        result = self.db.execute(
            update(AuthToken)
            .where(
                AuthToken.id == int(invite.id),
                AuthToken.purpose == TokenPurpose.invitation,
                AuthToken.used_at.is_(None),
            )
            .values(used_at=utcnow().replace(tzinfo=None))
        )
        self.db.expire_all()
        return result.rowcount == 1

    def accept_invite(self, account_id: int, invite: Invite) -> None:
        """Einem bestehenden Konto die Rechte der Einladung dazugeben - nie weniger.

        Die Rolle steigt, wenn die Einladung eine hoehere vergibt; Rechte je
        Fassung und automatische Freigaben kommen dazu. Kontingente und
        Sperren bleiben, wie sie sind: Sie sind Grenzen, keine Rechte, und ein
        bestehendes Konto hat seine eigenen.
        """
        user = self.db.get(User, account_id)
        token = self._einladung(invite)
        if user is None or token is None:
            return
        wunsch, bewertung, _ = einladungen.bewerten(self.db, token)
        werte = einladungen.kontowerte(token, wunsch, bewertung)
        rolle = werte.get("role")
        if (
            isinstance(rolle, Role)
            and rolle in _ROLLEN
            and user.role in _ROLLEN
            and _ROLLEN.index(rolle) > _ROLLEN.index(user.role)
        ):
            user.role = rolle
        for schluessel in ("auto_approve_movies", "auto_approve_series"):
            if werte.get(schluessel) is True:
                setattr(user, schluessel, True)
        vorhanden = {recht.fassung_kennung: recht for recht in user.fassung_rechte}
        for recht in werte.get("fassung_rechte") or []:
            assert isinstance(recht, FassungRecht)
            eigenes = vorhanden.get(recht.fassung_kennung)
            if eigenes is None:
                user.fassung_rechte.append(
                    FassungRecht(
                        fassung_kennung=recht.fassung_kennung,
                        anfragen=recht.anfragen,
                        auto_freigabe=recht.auto_freigabe,
                    )
                )
            else:
                eigenes.anfragen = eigenes.anfragen or recht.anfragen
                eigenes.auto_freigabe = eigenes.auto_freigabe or recht.auto_freigabe
        self.db.flush()
        einladungen.abschliessen(self.db, token, user, bewertung.entfallen(wunsch))

    # --- Transaktion ------------------------------------------------------------------------

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


# ---------------------------------------------------------------------------
# Die Wanderung aus 1.0.1 (einmal, ``db._oidc_nach_bauplan``)
# ---------------------------------------------------------------------------


def _spalten(cursor, tabelle: str) -> set[str]:
    return {zeile[1] for zeile in cursor.execute(f'PRAGMA table_info("{tabelle}")')}


def nach_bauplan_wandern(engine) -> dict[str, int]:
    """Die Anbieter und Verknuepfungen von 1.0.1 in die Tabellen des Bauplans.

    Laeuft genau einmal, nach der Sicherung vor dem Update (``db.init_db``),
    und nur, wenn ``oidc_providers`` noch die Spalte ``issuer_url`` hat.

    * ``oidc_providers`` wird umgebaut (SQLite kann eine Eindeutigkeit nicht
      einzeln entfernen): ``issuer_url`` wird ``issuer``, das Geheimnis zieht
      in ``client_secret_enc`` mit eigenem Kontext um, die neuen Spalten kommen
      mit den Werten, die Nexview bis hierher hatte: keine Codeabfrage nach dem
      Anbieter (Nexview hat keinen zweiten Faktor), ``auto_create`` wie
      eingestellt, Reihenfolge nach Alter. Jeder Eintrag behaelt die
      Rueckleitadresse, die sein Anbieter kennt (``redirect_path``).
    * Jede Zeile aus ``user_oidc_links`` wird eine Verknuepfung des Eintrags mit
      demselben Aussteller (``same_issuer``). Zeilen ohne passenden Eintrag
      (der Anbieter wurde geloescht oder hat den Aussteller gewechselt; in 1.0.1
      blieben sie stehen) kommen nicht mit: Nach dem Bauplan gehen
      Verknuepfungen mit ihrem Eintrag. ``user_oidc_links`` selbst bleibt eine
      Fassung lang unangetastet stehen.

    Zurueck kommen die Zahlen fuers Protokoll.
    """
    from sqlalchemy import MetaData
    from sqlalchemy.schema import CreateIndex, CreateTable

    from ..crypto import decrypt
    from ..vendor.nexoidc import migrate

    zahlen = {"providers": 0, "links": 0, "left": 0}
    roh = engine.raw_connection()
    try:
        verbindung = roh.driver_connection
        vorher = verbindung.isolation_level
        verbindung.isolation_level = None
        cursor = verbindung.cursor()
        try:
            tabellen = {
                zeile[0]
                for zeile in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "oidc_providers" not in tabellen or "issuer_url" not in _spalten(
                cursor, "oidc_providers"
            ):
                return zahlen
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.execute("BEGIN IMMEDIATE")
            alte = cursor.execute(
                "SELECT id, slug, label, issuer_url, client_id, client_secret, auto_create, "
                "enabled, created_at FROM oidc_providers ORDER BY id"
            ).fetchall()
            neu = OidcProvider.__table__.to_metadata(MetaData(), name="oidc_providers_neu")
            cursor.execute(str(CreateTable(neu).compile(dialect=engine.dialect)))
            eintraege: list[tuple[int, str]] = []
            for position, (pid, slug, label, issuer, client_id, geheim, auto, an, seit) in enumerate(
                alte
            ):
                klartext = decrypt(geheim or "")
                cursor.execute(
                    "INSERT INTO oidc_providers_neu (id, slug, label, issuer, client_id, "
                    "client_secret_enc, scopes, enabled, auto_create, trusts_second_factor, "
                    "managed, position, redirect_path, created_at, registered_redirect) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '', ?, ?, ?, '')",
                    (
                        pid,
                        slug,
                        label,
                        (issuer or "").strip().rstrip("/"),
                        client_id,
                        encrypt_im_kontext(klartext, secret_context(pid)),
                        nexoidc.DEFAULT_SCOPES,
                        1 if an else 0,
                        1 if auto else 0,
                        position,
                        migrate.old_callback(ALTER_RUECKWEG, slug),
                        seit,
                    ),
                )
                eintraege.append((pid, (issuer or "").strip()))
            cursor.execute("DROP TABLE oidc_providers")
            cursor.execute("ALTER TABLE oidc_providers_neu RENAME TO oidc_providers")
            for index in OidcProvider.__table__.indexes:
                cursor.execute(str(CreateIndex(index).compile(dialect=engine.dialect)))
            zahlen["providers"] = len(eintraege)

            if "user_oidc_links" in tabellen:
                for user_id, issuer, subject, anzeige, seit in cursor.execute(
                    "SELECT user_id, issuer, subject, display, linked_at FROM user_oidc_links "
                    "ORDER BY id"
                ).fetchall():
                    passend = next(
                        (pid for pid, aussteller in eintraege if same_issuer(aussteller, issuer)),
                        None,
                    )
                    if passend is None or not subject:
                        zahlen["left"] += 1
                        continue
                    adresse = anzeige if anzeige and "@" in anzeige else None
                    cursor.execute(
                        "INSERT OR IGNORE INTO oidc_links (provider_id, subject, account_id, "
                        "issuer, email, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (passend, subject, user_id, issuer, adresse, seit),
                    )
                    if cursor.rowcount == 1:
                        zahlen["links"] += 1
                    else:
                        # Dieselbe Kennung oder dasselbe Konto schon am Eintrag.
                        zahlen["left"] += 1
            kaputt = cursor.execute("PRAGMA foreign_key_check").fetchall()
            if kaputt:
                raise RuntimeError(f"foreign keys broken after the sign-in migration: {kaputt[:5]}")
            cursor.execute("COMMIT")
        except BaseException:
            if verbindung.in_transaction:
                cursor.execute("ROLLBACK")
            raise
        finally:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            verbindung.isolation_level = vorher
    finally:
        roh.close()
    logger.info(
        "Sign-in providers follow the shared blueprint now: %d provider(s), %d link(s) taken "
        "over, %d link(s) without a provider left in user_oidc_links",
        zahlen["providers"],
        zahlen["links"],
        zahlen["left"],
    )
    return zahlen
