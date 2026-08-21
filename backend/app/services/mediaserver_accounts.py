"""Konten aus dem Media-Server: anmelden, verknuepfen, anlegen.

Hier steht die Antwort auf die Frage "wer ist das, und bekommt diese Person ein
Nexview-Konto?". Der Weg dorthin - PIN, Token, Zugriffspruefung - liegt in
``services/mediaserver``; dieses Modul sieht nur noch das fertige
``ExternalAccount``.

**Die tragende Regel:** Der Zugriff auf die Bibliothek wird genau einmal
geprueft, naemlich beim Verbinden. Danach nie wieder. Wer eingeladen wurde,
ist durch die Einladung berechtigt; wer sich neu anmeldet, durch den
Server-Zugriff. Entzieht der Administrator spaeter den Plex-Zugriff, aendert
das nichts am Nexview-Konto - dafuer gibt es den Schalter "aktiv".
"""

from __future__ import annotations

import json
import re
import secrets
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AuthToken,
    MediaServerBlock,
    NotificationType,
    QuotaPeriod,
    Role,
    TokenPurpose,
    User,
    utcnow,
)
from ..security import has_usable_password, unusable_password
from . import notify, settings_service, tokens
from .mediaserver import ExternalAccount, LoginChallenge, new_client_identifier

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from .settings_service import AppSettings


class KontoFehler(Exception):
    """Die Anmeldung fuehrt zu keinem Konto - mit Grund zum Anzeigen.

    ``code`` geht an die Oberflaeche, damit sie einen verstaendlichen Satz
    daraus machen kann statt einer technischen Meldung.
    """

    def __init__(self, code: str, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------
# Angefangene Anmeldevorgaenge
# --------------------------------------------------------------------------


def ensure_client_identifier(db: Session, settings: "AppSettings") -> "AppSettings":
    """Die Geraetekennung dieser Installation - beim ersten Mal erzeugt.

    Plex fuehrt angemeldete Geraete darueber. Wuerde sie bei jeder Anmeldung
    wechseln, saehe der Nutzer in seinen Plex-Einstellungen bald Dutzende
    Nexview-Eintraege.
    """
    if settings.mediaserver_client_identifier:
        return settings

    kennung = new_client_identifier()
    settings_service.save_settings(db, {"mediaserver_client_identifier": kennung})
    return settings_service.load_settings(db)


def start_challenge(
    db: Session,
    provider: str,
    challenge: LoginChallenge,
    *,
    user: User | None = None,
) -> str:
    """Den Vorgang vermerken und den Merkzettel fuer den Browser zurueckgeben.

    Der Browser bekommt **nur** diesen Wert. PIN und Code des Anbieters bleiben
    hier - wer sie mitliest, koennte sonst eine fremde Anmeldung zu Ende
    fuehren.

    Beim Verknuepfen haengt der Vorgang zusaetzlich am angemeldeten Konto, damit
    niemand eine fremde Identitaet an ein anderes Konto haengen kann.
    """
    roh, _ = tokens.create(
        db,
        TokenPurpose.mediaserver_login,
        # Es gibt an dieser Stelle noch keine Adresse; beim Verknuepfen waere
        # sie sogar irrefuehrend, weil die Identitaet ja erst noch kommt.
        email="",
        user=user,
        mediaserver_ref=json.dumps(
            {"provider": provider, "ref": challenge.ref, "code": challenge.code}
        ),
        # Zwei Personen, die sich gleichzeitig anmelden, duerfen einander nicht
        # gegenseitig hinauswerfen - siehe tokens.create.
        invalidate_previous=False,
    )
    return roh


def read_challenge(db: Session, poll_token: str) -> tuple[AuthToken, dict[str, str]]:
    """Den vermerkten Vorgang zu einem Merkzettel holen."""
    eintrag = tokens.find(db, poll_token, TokenPurpose.mediaserver_login)
    if eintrag is None:
        raise KontoFehler(
            "mediaserver_challenge_expired",
            "Der Anmeldevorgang ist abgelaufen. Bitte erneut versuchen.",
            status_code=410,
        )
    try:
        daten = json.loads(eintrag.mediaserver_ref or "{}")
    except ValueError:
        daten = {}
    return eintrag, daten


# --------------------------------------------------------------------------
# Sperrliste
# --------------------------------------------------------------------------


def is_blocked(db: Session, provider: str, account_id: str) -> bool:
    return (
        db.scalar(
            select(MediaServerBlock).where(
                MediaServerBlock.provider == provider,
                MediaServerBlock.account_id == account_id,
            )
        )
        is not None
    )


def block(db: Session, user: User, *, by: int | None = None) -> None:
    """Das verknuepfte Media-Server-Konto sperren.

    Wird beim Loeschen eines Benutzers aufgerufen. Ohne diesen Schritt waere
    das Loeschen wirkungslos: Wer Zugriff auf die Bibliothek hat, meldet sich
    einfach neu an und haette sofort wieder ein Konto.
    """
    if not user.mediaserver_provider or not user.mediaserver_account_id:
        return
    if is_blocked(db, user.mediaserver_provider, user.mediaserver_account_id):
        return
    db.add(
        MediaServerBlock(
            provider=user.mediaserver_provider,
            account_id=user.mediaserver_account_id,
            username=user.mediaserver_username,
            blocked_by=by,
        )
    )


# --------------------------------------------------------------------------
# Verknuepfen und Anlegen
# --------------------------------------------------------------------------


def link(user: User, account: ExternalAccount) -> None:
    """Ein Media-Server-Konto an ein Nexview-Konto haengen."""
    user.mediaserver_provider = account.provider
    user.mediaserver_account_id = account.account_id
    user.mediaserver_username = account.username
    user.mediaserver_email = account.email
    user.mediaserver_thumb = account.thumb
    user.mediaserver_linked_at = utcnow().replace(tzinfo=None)
    # Der Anbieter hat die Adresse geprueft - eine eigene Bestaetigung waere
    # eine Formalie, die nur den ersten Anmeldeversuch blockieren wuerde.
    if account.email:
        user.email_verified = True


def merke_token(user: User, verschluesselt: str | None) -> None:
    """Das persoenliche Anbieter-Token am Konto hinterlegen.

    Wird bei **jeder** Anmeldung und jeder Verknuepfung aufgerufen: Das Token
    gehoert zur Verknuepfung, nicht zu einer einzelnen Funktion. Nur damit
    laesst sich spaeter die persoenliche Merkliste lesen - der Zugang des
    Administrators sieht sie nicht. Und ein abgelaufenes Token erneuert sich
    so beim naechsten Anmelden von selbst.

    Ein leerer Wert wird ignoriert - ein vorhandenes Token soll nicht durch
    nichts ersetzt werden, bloss weil ein Aufrufer keines zur Hand hat.
    """
    if not verschluesselt:
        return
    user.watchlist_token = verschluesselt
    user.watchlist_connected_at = utcnow()
    # Ein frisches Token ist per Definition gueltig - der rote Hinweis muss
    # damit sofort verschwinden, nicht erst nach dem naechsten stuendlichen
    # Durchlauf.
    #
    # Bewusst **hier** und nicht im Merklisten-Endpunkt: Durch diese Funktion
    # laufen alle vier Wege, auf denen ein Token entsteht - Anmeldung mit
    # Plex, Verknuepfen im Profil, Server-Anbindung des Administrators und die
    # Merklisten-Anmeldung. An nur einem davon zurueckzusetzen liesse den
    # Hinweis nach den anderen dreien stehen.
    user.watchlist_token_invalid_at = None


def token_abgelehnt(user: User) -> bool:
    """Merken, dass der Anbieter das persoenliche Token abgelehnt hat.

    **Das Token wird bewusst nicht geloescht.** Geloescht saehe der Zustand aus
    wie "nie verbunden", und die Oberflaeche koennte nicht zwischen "muss sich
    neu anmelden" und "will die Merkliste gar nicht" unterscheiden.

    Zwei Stellen erkennen den Fall: der stuendliche Gesehen-Abgleich und der
    Merklisten-Abruf, wenn jemand die Seite oeffnet. Die Regel steht deshalb
    hier und nicht in einer davon.

    Gibt zurueck, ob sich etwas geaendert hat - der Aufrufer entscheidet, ob er
    dafuer eigens speichert.
    """
    if user.watchlist_token_invalid_at is not None:
        return False
    user.watchlist_token_invalid_at = utcnow()
    return True


def token_geht_wieder(user: User) -> bool:
    """Die Markierung "abgelehnt" wieder wegnehmen.

    Gegenstueck zu ``token_abgelehnt``. Ohne das bliebe der rote Hinweis
    stehen, sobald er einmal gesetzt wurde - auch wenn die Ursache laengst
    behoben ist und der Abgleich wieder laeuft. Der Betroffene wuerde sich
    dann grundlos neu anmelden.

    Gibt zurueck, ob sich etwas geaendert hat.
    """
    if user.watchlist_token_invalid_at is None:
        return False
    user.watchlist_token_invalid_at = None
    return True


def unlink(user: User) -> None:
    """Die Verknuepfung loesen.

    Wer danach weder Passwort noch bestaetigte Adresse haette, kaeme nicht mehr
    herein - das wird abgefangen, bevor hier etwas passiert.
    """
    if not has_usable_password(user.password_hash) and not (
        user.email and user.email_verified
    ):
        raise KontoFehler(
            "mediaserver_would_lock_out",
            "Lege zuerst ein Passwort fest - sonst kämst du nicht mehr hinein.",
            status_code=409,
        )
    user.mediaserver_provider = None
    user.mediaserver_account_id = None
    user.mediaserver_username = None
    user.mediaserver_email = None
    user.mediaserver_thumb = None
    user.mediaserver_linked_at = None
    # Die Merkliste haengt an genau diesem Konto - ohne Verknuepfung gibt es
    # nichts mehr, wozu das Token gehoerte. Es stehen zu lassen hiesse, ein
    # fremdes Zugangstoken ohne Anlass aufzubewahren.
    user.watchlist_token = None
    user.watchlist_connected_at = None
    user.watchlist_token_invalid_at = None


def find_linked(db: Session, account: ExternalAccount) -> User | None:
    return db.scalar(
        select(User).where(
            User.mediaserver_provider == account.provider,
            User.mediaserver_account_id == account.account_id,
        )
    )


def _unique_username(db: Session, wunsch: str) -> str:
    """Aus dem Namen beim Anbieter einen brauchbaren Benutzernamen machen.

    Plex laesst Zeichen zu, die Nexview nicht vergibt, und der Name kann
    laengst vergeben sein. Beides darf die Anmeldung nicht scheitern lassen.
    """
    sauber = re.sub(r"[^A-Za-z0-9_.-]", "", wunsch or "").strip("._-")
    if len(sauber) < 3:
        sauber = f"{sauber}user"[:64] if sauber else "user"

    basis = sauber[:60]
    kandidat = basis
    nummer = 2
    while db.scalar(select(User).where(func.lower(User.username) == kandidat.lower())):
        kandidat = f"{basis}-{nummer}"
        nummer += 1
    return kandidat


def _offene_einladung(db: Session, email: str) -> AuthToken | None:
    """Eine noch nicht eingeloeste Einladung fuer diese Adresse.

    Wer eingeladen wurde und sich dann ueber den Media-Server anmeldet, soll
    die dort vergebene Rolle bekommen - sonst waere die bewusste Entscheidung
    des Administrators stillschweigend verfallen.
    """
    eintrag = db.scalar(
        select(AuthToken)
        .where(
            AuthToken.purpose == TokenPurpose.invitation,
            AuthToken.email == tokens.normalize_email(email),
            AuthToken.used_at.is_(None),
        )
        .order_by(AuthToken.created_at.desc())
    )
    return eintrag if eintrag is not None and eintrag.open else None


def resolve(db: Session, settings: "AppSettings", account: ExternalAccount) -> User:
    """Zu welchem Nexview-Konto gehoert diese Anmeldung?

    Reihenfolge - und die ist wichtig:

    1. gesperrt? Dann endet es hier.
    2. schon verknuepft? Der uebliche Fall.
    3. gleiche Adresse? Dann verknuepfen statt ein zweites Konto anzulegen.
    4. offene Einladung? Deren Vorgaben gelten.
    5. sonst neu anlegen - falls der Administrator das erlaubt.
    """
    if is_blocked(db, account.provider, account.account_id):
        raise KontoFehler(
            "mediaserver_blocked",
            "Für dieses Konto ist der Zugang gesperrt.",
        )

    vorhanden = find_linked(db, account)
    if vorhanden is not None:
        # Ohne diese Pruefung kaeme ein deaktiviertes Konto ueber den
        # Media-Server herein, obwohl die Anmeldung mit Passwort es sperrt.
        if not vorhanden.is_active:
            raise KontoFehler("account_disabled", "Dieses Konto ist deaktiviert.")
        # Name und Bild koennen sich beim Anbieter geaendert haben.
        vorhanden.mediaserver_username = account.username
        vorhanden.mediaserver_thumb = account.thumb
        return vorhanden

    if account.email:
        nach_adresse = db.scalar(
            select(User).where(User.email == tokens.normalize_email(account.email))
        )
        if nach_adresse is not None:
            if nach_adresse.mediaserver_account_id:
                raise KontoFehler(
                    "mediaserver_link_conflict",
                    "Zu dieser Adresse gehört bereits ein anderes Konto des Media-Servers.",
                    status_code=409,
                )
            if not nach_adresse.is_active:
                raise KontoFehler("account_disabled", "Dieses Konto ist deaktiviert.")
            link(nach_adresse, account)
            return nach_adresse

    if not settings.mediaserver_auto_import:
        raise KontoFehler(
            "mediaserver_not_invited",
            "Für diesen Zugang gibt es noch kein Konto. Bitte den Administrator um eine Einladung.",
        )

    return _anlegen(db, settings, account)


def _anlegen(db: Session, settings: "AppSettings", account: ExternalAccount) -> User:
    """Ein neues Konto aus einer Media-Server-Anmeldung."""
    einladung = _offene_einladung(db, account.email) if account.email else None

    if einladung is not None:
        rolle = einladung.invite_role or Role.user
        quota_movies = einladung.invite_quota_movies
        quota_series = einladung.invite_quota_series
        periode = einladung.invite_quota_period or QuotaPeriod.week
        blocked_movies = einladung.invite_blocked_movie_profiles
        blocked_series = einladung.invite_blocked_series_profiles
        einladung.used_at = utcnow().replace(tzinfo=None)
    else:
        rolle = Role(settings.mediaserver_default_role)
        quota_movies = settings.mediaserver_default_quota_movies
        quota_series = settings.mediaserver_default_quota_series
        periode = QuotaPeriod(settings.mediaserver_default_quota_period)
        blocked_movies = ""
        blocked_series = ""

    benutzer = User(
        username=_unique_username(db, account.username),
        # Kein Passwort - wer eines will, setzt es spaeter im Profil.
        password_hash=unusable_password(),
        email=tokens.normalize_email(account.email) if account.email else None,
        role=rolle,
        display_name=account.username,
        language=settings.default_language,
        # Neue Konten muessen ihre Anfragen freigeben lassen. Zugriff auf die
        # Bibliothek zu haben heisst nicht, ungefragt herunterladen zu duerfen.
        auto_approve=False,
        quota_movies_limit=quota_movies,
        quota_series_limit=quota_series,
        quota_period=periode,
        blocked_movie_profiles=blocked_movies,
        blocked_series_profiles=blocked_series,
        age=settings.mediaserver_default_age,
    )
    link(benutzer, account)
    db.add(benutzer)

    try:
        db.flush()
    except IntegrityError:
        # Zwei gleichzeitige Anmeldungen desselben Kontos. Der eindeutige Index
        # hat den zweiten Versuch abgefangen - das bereits angelegte Konto ist
        # das richtige.
        db.rollback()
        bereits_da = find_linked(db, account)
        if bereits_da is None:
            raise
        return bereits_da

    notify.create_for_admins(
        db,
        kind=NotificationType.user_imported,
        message_key="notifications.userImported",
        title=benutzer.display_name or benutzer.username,
    )
    return benutzer
