"""Konten aus einer OIDC-Anmeldung: verknuepfen, zuordnen, anlegen.

Das Geschwister von ``mediaserver_accounts``, mit derselben Arbeitsteilung:
Der Weg zur gepruefte Identitaet liegt in ``services/oidc``; hier steht nur
noch die Frage "wer ist das, und bekommt diese Person ein Nexview-Konto?".

Die Kaskade in ``resolve`` ist dieselbe wie beim Media-Server - mit einem
entscheidenden Unterschied bei der Adress-Bruecke: **Die Adresse zaehlt nur,
wenn der Anbieter sie als bestaetigt meldet** (``email_verified``). Beim
Media-Server buergt der Anbieter pauschal fuer jede herausgegebene Adresse;
ein OIDC-Anbieter sagt es ausdruecklich dazu - und bei "nein" waere die
Bruecke eine offene Tuer: Wer sich bei irgendeinem Anbieter ein Konto mit
fremder Adresse anlegt, uebernaehme darueber das fremde Nexview-Konto.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import NotificationType, OidcBlock, OidcLink, Role, User, utcnow
from ..security import unusable_password
from . import logs, notify, tokens
from .mediaserver_accounts import KontoFehler, _unique_username, offene_einladung
from .oidc import OidcIdentitaet

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from .settings_service import AppSettings

logger = logging.getLogger("nexview.oidc")


# ---------------------------------------------------------------------------
# Sperrliste
# ---------------------------------------------------------------------------


def is_blocked(db: Session, issuer: str, subject: str) -> bool:
    return (
        db.scalar(
            select(OidcBlock).where(
                OidcBlock.issuer == issuer, OidcBlock.subject == subject
            )
        )
        is not None
    )


def block(db: Session, user: User, *, by: int | None = None) -> None:
    """Die OIDC-Identitaeten eines Benutzers sperren - beim Loeschen des Kontos.

    Dasselbe Loch wie beim Media-Server: Mit eingeschalteter automatischer
    Anlage waere das Loeschen sonst wirkungslos, die Person meldet sich neu an
    und hat sofort wieder ein Konto. Gesperrt wird an (issuer, subject) - die
    Sperre ueberlebt damit auch ein Loeschen des Anbieter-Eintrags.
    """
    for zeile in user.oidc_links:
        if is_blocked(db, zeile.issuer, zeile.subject):
            continue
        db.add(
            OidcBlock(
                issuer=zeile.issuer,
                subject=zeile.subject,
                display=zeile.display,
                blocked_by=by,
            )
        )


# ---------------------------------------------------------------------------
# Verknuepfen und Zuordnen
# ---------------------------------------------------------------------------


def verknuepfung(user: User, issuer: str) -> OidcLink | None:
    """Die Verknuepfung dieses Benutzers bei **diesem** Anbieter."""
    for zeile in user.oidc_links:
        if zeile.issuer == issuer:
            return zeile
    return None


def find_linked(db: Session, identitaet: OidcIdentitaet) -> User | None:
    """Wem gehoert diese Identitaet?"""
    return db.scalar(
        select(User)
        .join(OidcLink, OidcLink.user_id == User.id)
        .where(
            OidcLink.issuer == identitaet.issuer,
            OidcLink.subject == identitaet.subject,
        )
    )


def link(user: User, identitaet: OidcIdentitaet) -> None:
    """Eine OIDC-Identitaet an ein Nexview-Konto haengen.

    ⚠️ **Die Adress-Bestaetigung wird nur uebernommen, wenn beides stimmt:**
    Der Anbieter meldet die Adresse als bestaetigt, **und** sie ist dieselbe,
    die am Konto steht. Beim Media-Server genuegt die Herausgabe der Adresse;
    hier waere das falsch - ``email_verified: false`` heisst ausdruecklich
    "dafuer buerge ich nicht", und eine *andere* bestaetigte Adresse bestaetigt
    nicht die des Kontos.
    """
    zeile = verknuepfung(user, identitaet.issuer)
    if zeile is None:
        zeile = OidcLink(issuer=identitaet.issuer, subject=identitaet.subject)
        user.oidc_links.append(zeile)

    zeile.subject = identitaet.subject
    zeile.display = identitaet.email or identitaet.username
    zeile.linked_at = utcnow().replace(tzinfo=None)

    if (
        identitaet.email_verified
        and identitaet.email
        and user.email == tokens.normalize_email(identitaet.email)
    ):
        user.email_verified = True


def loesen(user: User, issuer: str) -> None:
    """Eine Verknuepfung loesen - mit demselben Aussperrschutz wie ueberall.

    Wer weder Passwort noch bestaetigte Adresse noch einen anderen Weg hinein
    haette, wird abgewiesen, bevor etwas passiert - Wort fuer Wort dieselbe
    Frage wie ``mediaserver_accounts.kaeme_nicht_mehr_herein``.
    """
    if kaeme_nicht_mehr_herein(user, ohne=issuer):
        raise KontoFehler(
            "oidc_would_lock_out",
            "Lege zuerst ein Passwort fest - sonst kämst du nicht mehr hinein.",
            status_code=409,
        )
    user.oidc_links[:] = [z for z in user.oidc_links if z.issuer != issuer]


def kaeme_nicht_mehr_herein(
    user: User, ohne: str, nutzbare_issuer: set[str] | None = None
) -> bool:
    """Haette dieses Konto ohne diesen Anbieter noch einen Weg hinein?

    Gezaehlt werden: eine andere OIDC-Verknuepfung, eine Medienserver-
    Verknuepfung, ein nutzbares Passwort, eine bestaetigte Adresse. Die
    letzten beiden sind absichtlich dieselbe Bedingung wie beim Media-Server -
    zwei Stellen, die "kaeme er noch herein?" verschieden beantworten, waeren
    genau die Art Fehler, die man erst bemerkt, wenn jemand ausgesperrt ist.
    """
    from ..security import has_usable_password

    # ⚠️ **Eine Verknuepfung zaehlt nur, wenn es den Anbieter noch gibt - und
    # er eingeschaltet ist.** Hier stand einmal schlicht "irgendeine andere
    # Verknuepfung", und das machte den ganzen Aussperrschutz still wirkungslos:
    # Verknuepfungen bleiben beim Loeschen eines Anbieters absichtlich stehen
    # (wer denselben spaeter wieder eintraegt, findet alles vor), und eine
    # geaenderte Anbieter-Adresse laesst sie ebenfalls verwaist zurueck. Wer je
    # einen zweiten Anbieter geloescht, abgeschaltet oder umgezogen hat, hatte
    # danach fuer die betroffenen Konten gar keinen Schutz mehr - weder die
    # Rueckfrage noch den Betreiber-Riegel, und beides ohne einen Ton.
    if nutzbare_issuer is None:
        # Ohne Liste bleibt es beim alten, grosszuegigen Verhalten. Aufrufer mit
        # Datenbank sollen die Liste mitgeben; siehe ``nutzbare_issuer()``.
        if any(zeile.issuer != ohne for zeile in user.oidc_links):
            return False
    elif any(
        zeile.issuer != ohne and zeile.issuer in nutzbare_issuer
        for zeile in user.oidc_links
    ):
        return False
    if user.mediaserver_accounts:
        return False
    return not has_usable_password(user.password_hash) and not (
        user.email and user.email_verified
    )


# ---------------------------------------------------------------------------
# Die Kaskade
# ---------------------------------------------------------------------------


def _abgewiesen(db: Session, identitaet: OidcIdentitaet, grund: str) -> None:
    """Warum diese Anmeldung nicht durchkam - fuer den Betreiber.

    ⚠️ **Ohne diese Zeile ist der Fehlschlag stumm.** Der Anmeldende sieht eine
    Meldung, die absichtlich wenig verraet; im Protokoll stand bisher gar
    nichts. Ein Betreiber, dessen Anbieter ``email_verified: false`` liefert
    (bei authentik, Keycloak und Pocket ID die Werkseinstellung), hatte damit
    keinen einzigen Anhaltspunkt - genau so gemeldet.

    Nachgesehen wird hier **auch dann**, ob es ein Konto zu der Adresse gibt,
    wenn die Bruecke sie nicht benutzen durfte. Das ist der Unterschied
    zwischen "es gibt kein Konto" und "es gaebe eines, aber niemand buergt fuer
    die Adresse" - und ohne ihn sucht der Betreiber an der falschen Stelle.
    Die Auskunft geht ausdruecklich **nur** ins Protokoll, nicht an den
    Anmeldenden.

    Die Adresse steht gekuerzt da (siehe ``logs.adresse``). Auf **WARNING**:
    Hier ist jemand nicht hereingekommen, der es versucht hat.
    """
    konto_da = False
    if identitaet.email:
        konto_da = (
            db.scalar(
                select(User.id).where(User.email == tokens.normalize_email(identitaet.email))
            )
            is not None
        )
    logger.warning(
        "OIDC sign-in refused (%s): issuer=%r email=%s verified=%s account_exists=%s",
        grund,
        identitaet.issuer,
        logs.adresse(identitaet.email),
        identitaet.email_verified,
        konto_da,
    )


def resolve(
    db: Session, settings: AppSettings, identitaet: OidcIdentitaet, *, auto_create: bool
) -> User:
    """Zu welchem Nexview-Konto gehoert diese Anmeldung?

    Dieselbe Reihenfolge wie beim Media-Server, und die ist wichtig:

    1. gesperrt? Dann endet es hier.
    2. schon verknuepft? Der uebliche Fall.
    3. gleiche **bestaetigte** Adresse? Dann verknuepfen statt ein zweites
       Konto anzulegen.
    4. offene Einladung fuer diese Adresse? Deren Vorgaben gelten.
    5. sonst neu anlegen - falls der Administrator das fuer diesen Anbieter
       erlaubt hat (``auto_create`` kommt je Anbieter, nicht haus-weit).
    """
    if is_blocked(db, identitaet.issuer, identitaet.subject):
        _abgewiesen(db, identitaet, "identity is on the block list")
        raise KontoFehler("oidc_blocked", "Für dieses Konto ist der Zugang gesperrt.")

    vorhanden = find_linked(db, identitaet)
    if vorhanden is not None:
        if not vorhanden.is_active:
            # ⚠️ Der eine Abbruch, den ein **bekannter** Benutzer erlebt: Er hat
            # sich hier schon angemeldet, und heute geht es nicht mehr. Ohne
            # Zeile sucht der Betreiber beim Anbieter, statt beim Haken am
            # Konto.
            _abgewiesen(db, identitaet, "linked account is switched off")
            raise KontoFehler("account_disabled", "Dieses Konto ist deaktiviert.")
        # Name oder Adresse koennen sich beim Anbieter geaendert haben - die
        # Anzeige zieht nach, die Identitaet (issuer, subject) bleibt.
        zeile = verknuepfung(vorhanden, identitaet.issuer)
        if zeile is not None:
            zeile.display = identitaet.email or identitaet.username
        return vorhanden

    if identitaet.email and identitaet.email_verified:
        nach_adresse = db.scalar(
            select(User).where(User.email == tokens.normalize_email(identitaet.email))
        )
        if nach_adresse is not None:
            # Nur die Verknuepfung **dieses Anbieters** steht im Weg - dieselbe
            # Lehre wie beim Media-Server im Parallelbetrieb.
            schon = verknuepfung(nach_adresse, identitaet.issuer)
            if schon is not None and schon.subject != identitaet.subject:
                _abgewiesen(
                    db,
                    identitaet,
                    "the account for this address already has a different sign-in "
                    "of this provider",
                )
                raise KontoFehler(
                    "oidc_link_conflict",
                    "Zu dieser Adresse gehört bereits eine andere Anmeldung "
                    "dieses Anbieters.",
                    status_code=409,
                )
            if not nach_adresse.is_active:
                _abgewiesen(db, identitaet, "account for this address is switched off")
                raise KontoFehler("account_disabled", "Dieses Konto ist deaktiviert.")
            if nach_adresse.role == Role.child:
                # Kinderkonten haben keine eigene Anmeldung - auch keine
                # delegierte. Praktisch entsteht der Fall kaum (Kinderkonten
                # haben keine Adresse), aber "kaum" ist kein Riegel.
                _abgewiesen(db, identitaet, "child account")
                raise KontoFehler(
                    "oidc_not_invited",
                    "Für diesen Zugang gibt es noch kein Konto. "
                    "Bitte den Administrator um eine Einladung.",
                )
            link(nach_adresse, identitaet)
            return nach_adresse

    if not auto_create:
        # ⚠️ **Zwei Ursachen, eine Meldung.** Entweder kennt Nexview diese
        # Person wirklich nicht - oder es gaebe ein Konto, aber der Anbieter
        # hat die Adresse nicht beglaubigt und die Bruecke wurde uebersprungen.
        # Fuer den Anmeldenden bleibt es derselbe Satz (er soll nichts ueber
        # fremde Konten erfahren); im Protokoll stehen die Faelle getrennt.
        _abgewiesen(
            db,
            identitaet,
            "no auto-create"
            if identitaet.email_verified or not identitaet.email
            else "no auto-create, address not confirmed by the provider",
        )
        raise KontoFehler(
            "oidc_not_invited",
            "Für diesen Zugang gibt es noch kein Konto. "
            "Bitte den Administrator um eine Einladung.",
        )

    return _anlegen(db, settings, identitaet)


def _anlegen(db: Session, settings: AppSettings, identitaet: OidcIdentitaet) -> User:
    """Ein neues Konto aus einer OIDC-Anmeldung.

    Rolle und Grenzen kommen aus einer offenen Einladung, falls es eine gibt -
    sonst ist es ein gewoehnlicher Benutzer mit den Standardwerten des Hauses.
    **Rollen bleiben lokal**: Was der Anbieter an Gruppen kennt, liest Nexview
    nicht - der Anbieter beglaubigt, *wer* jemand ist, nicht, was er darf.
    """
    einladung = (
        offene_einladung(db, identitaet.email)
        if identitaet.email and identitaet.email_verified
        else None
    )

    # ⚠️ **Eine Einladung, die keiner beglaubigten Adresse gegenuebersteht,
    # faellt weg - und das war bisher stumm.** Der Betreiber hat Rolle und
    # Kontingent von Hand vergeben; meldet der Anbieter ``email_verified:
    # false`` (bei authentik, Keycloak und Pocket ID die Werkseinstellung),
    # entsteht das Konto trotzdem, aber mit den Standardwerten des Hauses.
    # Nachgesehen wird hier nur fuer das Protokoll - benutzt wird die
    # Einladung ausdruecklich **nicht**: Sie zu verwerten hiesse, eine
    # unbeglaubigte Adresse als Ausweis zu nehmen, und genau davor steht die
    # Bedingung oben.
    uebergangen = (
        offene_einladung(db, identitaet.email)
        if einladung is None and identitaet.email and not identitaet.email_verified
        else None
    )

    if einladung is not None:
        rolle = einladung.invite_role or Role.user
        quota_movies = einladung.invite_quota_movies
        quota_series = einladung.invite_quota_series
        blocked_movies = einladung.invite_blocked_movie_profiles
        blocked_series = einladung.invite_blocked_series_profiles
        einladung.used_at = utcnow().replace(tzinfo=None)
    else:
        rolle = Role.user
        quota_movies = None
        quota_series = None
        blocked_movies = ""
        blocked_series = ""

    benutzer = User(
        username=_unique_username(db, identitaet.username or "user"),
        # Kein Passwort - wie beim Media-Server-Import. Der Weg zurueck ohne
        # den Anbieter ist derselbe und in ``mediaserver_accounts._anlegen``
        # ausbuchstabiert: "Passwort vergessen" bei bestaetigter Adresse,
        # sonst der Administrator.
        password_hash=unusable_password(),
        email=tokens.normalize_email(identitaet.email) if identitaet.email else None,
        # Nur eine **bestaetigte** Adresse gilt als bestaetigt - anders als
        # beim Media-Server, wo die Herausgabe genuegt. ``email_verified:
        # false`` heisst ausdruecklich "dafuer buerge ich nicht".
        email_verified=identitaet.email_verified,
        role=rolle,
        display_name=identitaet.username,
        language=settings.default_language,
        # Neue Konten muessen ihre Anfragen freigeben lassen - ein Konto beim
        # Anmeldedienst zu haben heisst nicht, ungefragt herunterladen zu
        # duerfen.
        auto_approve=False,
        quota_movies_limit=quota_movies,
        quota_series_limit=quota_series,
        blocked_movie_profiles=blocked_movies,
        blocked_series_profiles=blocked_series,
    )
    link(benutzer, identitaet)
    db.add(benutzer)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()

        # Zwei gleichzeitige Anmeldungen derselben Identitaet - der eindeutige
        # Index hat den zweiten Versuch abgefangen, das bereits angelegte
        # Konto ist das richtige.
        bereits_da = find_linked(db, identitaet)
        if bereits_da is not None:
            return bereits_da

        # ⚠️ **Die Adresse gehoert schon einem anderen Konto.** Der Fall
        # entsteht ohne jedes Zutun: Der Anbieter meldet
        # ``email_verified: false`` (bei authentik, Keycloak und Pocket ID die
        # Werkseinstellung), die Bruecke zu diesem Konto bleibt deshalb zu -
        # und die automatische Anlage laeuft in den eindeutigen Index.
        #
        # Vorher flog die Ausnahme bis nach oben durch: eine Fehlerseite ohne
        # Erklaerung, im Protokoll nur ein Stapelabzug. Jetzt ist es eine
        # gewoehnliche Abweisung - **nach aussen mit demselben Satz wie sonst**,
        # damit niemand ueber fremde Konten erfaehrt, und im Protokoll mit dem
        # Grund, den der Betreiber braucht.
        _abgewiesen(db, identitaet, "address already belongs to another account")
        raise KontoFehler(
            "oidc_not_invited",
            "Für diesen Zugang gibt es noch kein Konto. "
            "Bitte den Administrator um eine Einladung.",
        ) from None

    if uebergangen is not None:
        # Auf WARNING und erst hier: Das Konto steht jetzt wirklich, und es
        # steht anders da, als der Betreiber es eingestellt hat.
        logger.warning(
            "OIDC: account %r created with the default role and quota although an "
            "open invitation exists for %s - the provider did not confirm the "
            "address (issuer=%r, invited role=%s)",
            benutzer.username,
            logs.adresse(identitaet.email),
            identitaet.issuer,
            getattr(uebergangen.invite_role, "value", uebergangen.invite_role),
        )

    logger.info(
        "OIDC: created account %r from provider %r", benutzer.username, identitaet.issuer
    )
    notify.create_for_admins(
        db,
        kind=NotificationType.user_imported,
        message_key="notifications.userImported",
        title=benutzer.display_name or benutzer.username,
    )
    return benutzer
