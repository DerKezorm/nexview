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
    UserMediaServerAccount,
    utcnow,
)
from ..security import has_usable_password, unusable_password
from . import notify, settings_service, tokens
from .mediaserver import (
    PROVIDERS,
    ExternalAccount,
    LoginChallenge,
    new_client_identifier,
)

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
    # ⚠️ **Alle** Verknuepfungen, nicht nur die zuletzt gespiegelte.
    #
    # Sonst haette das Loeschen eines Benutzers mit Plex *und* Jellyfin nur
    # einen der beiden Wege gesperrt - ueber den anderen kaeme dieselbe Person
    # sofort wieder herein. Genau das soll ``block`` verhindern.
    for zeile in user.mediaserver_accounts:
        if not zeile.provider or not zeile.account_id:
            continue
        if is_blocked(db, zeile.provider, zeile.account_id):
            continue
        db.add(
            MediaServerBlock(
                provider=zeile.provider,
                account_id=zeile.account_id,
                username=zeile.username,
                blocked_by=by,
            )
        )


# --------------------------------------------------------------------------
# Verknuepfen und Anlegen
# --------------------------------------------------------------------------


def verknuepfung(user: User, provider: str) -> UserMediaServerAccount | None:
    """Die Verknuepfung dieses Benutzers bei **diesem** Anbieter."""
    for zeile in user.mediaserver_accounts:
        if zeile.provider == provider:
            return zeile
    return None


def _spalten_spiegeln(user: User) -> None:
    """Die Einzelspalten am Benutzer aus der Tabelle nachziehen.

    ⚠️ **Die einzige Stelle, die diese Spalten schreibt** - und der Grund, dass
    es sie gibt: Ein gutes Dutzend Aufrufer liest ``mediaserver_provider`` und
    die vier daneben. Sie fuehren die **zuletzt** verknuepfte Identitaet.

    Wer sie von Hand setzt, laesst Tabelle und Spalten auseinanderlaufen - und
    genau daran hing der rote "Zugang abgelaufen"-Balken fest, nachdem die
    betroffene Zeile laengst wieder in Ordnung war.
    """
    if not user.mediaserver_accounts:
        user.mediaserver_provider = None
        user.mediaserver_account_id = None
        user.mediaserver_username = None
        user.mediaserver_email = None
        user.mediaserver_thumb = None
        user.mediaserver_linked_at = None
        user.watchlist_token = None
        user.watchlist_connected_at = None
        user.watchlist_token_invalid_at = None
        return

    neuste = max(user.mediaserver_accounts, key=lambda z: z.linked_at)
    user.mediaserver_provider = neuste.provider
    user.mediaserver_account_id = neuste.account_id
    user.mediaserver_username = neuste.username
    user.mediaserver_email = neuste.email
    user.mediaserver_thumb = neuste.thumb
    user.mediaserver_linked_at = neuste.linked_at
    user.watchlist_token = neuste.token
    user.watchlist_connected_at = neuste.token_connected_at
    user.watchlist_token_invalid_at = neuste.token_invalid_at


def link(user: User, account: ExternalAccount, token: str | None = None) -> None:
    """Ein Media-Server-Konto an ein Nexview-Konto haengen.

    Schreibt an **zwei** Stellen, und das ist Absicht:

    * ``user_media_server_accounts`` fuehrt *alle* Identitaeten - je Anbieter
      eine. Dort steht die Wahrheit.
    * Die Spalten am Benutzer fuehren die **zuletzt** verknuepfte davon. Ein
      gutes Dutzend Stellen liest sie, und fuer die allermeisten gibt es
      ohnehin nur eine.

    ⚠️ Bis 0.18.0 gab es nur die Spalten - und damit einen stillen Datenverlust:
    Wer Jellyfin verband, waehrend sein Konto an Plex hing, ueberschrieb die
    Plex-Verknuepfung samt persoenlichem Token. Ohne Warnung, mitten im
    Verbinden. Genau so passiert; deshalb die Tabelle.

    ``token`` gehoert hierher und nicht in einen zweiten Aufruf: Es ist *das
    Token dieser Verknuepfung*. Frueher lag es in einer eigenen Spalte am
    Benutzer, konnte also nur einem Anbieter gehoeren - wer beide verband,
    verlor das erste.
    """
    zeile = verknuepfung(user, account.provider)
    if zeile is None:
        zeile = UserMediaServerAccount(
            provider=account.provider, account_id=account.account_id
        )
        user.mediaserver_accounts.append(zeile)

    zeile.account_id = account.account_id
    zeile.username = account.username
    zeile.email = account.email
    zeile.thumb = account.thumb
    zeile.linked_at = utcnow().replace(tzinfo=None)

    if token:
        zeile.token = token
        zeile.token_connected_at = utcnow()
        zeile.token_invalid_at = None

    _spalten_spiegeln(user)

    # Der Anbieter hat die Adresse geprueft - eine eigene Bestaetigung waere
    # eine Formalie, die nur den ersten Anmeldeversuch blockieren wuerde.
    #
    # ⚠️ Nur *setzen*, nie zuruecknehmen: Jellyfin kennt zu einem Konto gar
    # keine Adresse. Wuerde hier bei fehlender Adresse die Bestaetigung
    # geloescht, verloere jemand mit bestaetigter Plex-Adresse sie in dem
    # Moment, in dem er Jellyfin dazunimmt - und damit seinen Weg zurueck ins
    # Konto.
    if account.email:
        user.email_verified = True


def merke_token(
    user: User, verschluesselt: str | None, provider: str | None = None
) -> None:
    """Das persoenliche Anbieter-Token am Konto hinterlegen.

    Wird bei **jeder** Anmeldung und jeder Verknuepfung aufgerufen: Das Token
    gehoert zur Verknuepfung, nicht zu einer einzelnen Funktion. Nur damit
    laesst sich spaeter die persoenliche Merkliste lesen - der Zugang des
    Administrators sieht sie nicht. Und ein abgelaufenes Token erneuert sich
    so beim naechsten Anmelden von selbst.

    Ein leerer Wert wird ignoriert - ein vorhandenes Token soll nicht durch
    nichts ersetzt werden, bloss weil ein Aufrufer keines zur Hand hat.

    ``provider`` sagt, zu **welcher** Verknuepfung das Token gehoert. Ohne
    Angabe gilt die zuletzt verknuepfte - das ist der Fall bei allen Wegen, die
    unmittelbar auf ein ``link`` folgen.
    """
    if not verschluesselt:
        return
    zeile = verknuepfung(user, provider or (user.mediaserver_provider or ""))
    if zeile is None:
        return
    zeile.token = verschluesselt
    zeile.token_connected_at = utcnow()
    zeile.token_invalid_at = None
    _spalten_spiegeln(user)
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


def token_abgelehnt(user: User, provider: str) -> bool:
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
    zeile = verknuepfung(user, provider)
    if zeile is None or zeile.token_invalid_at is not None:
        return False
    zeile.token_invalid_at = utcnow()
    _spalten_spiegeln(user)
    return True


def token_geht_wieder(user: User, provider: str) -> bool:
    """Die Markierung "abgelehnt" wieder wegnehmen.

    Gegenstueck zu ``token_abgelehnt``. Ohne das bliebe der rote Hinweis
    stehen, sobald er einmal gesetzt wurde - auch wenn die Ursache laengst
    behoben ist und der Abgleich wieder laeuft. Der Betroffene wuerde sich
    dann grundlos neu anmelden.

    Gibt zurueck, ob sich etwas geaendert hat.
    """
    zeile = verknuepfung(user, provider)
    if zeile is None or zeile.token_invalid_at is None:
        return False
    zeile.token_invalid_at = None
    _spalten_spiegeln(user)
    return True


def unlink(user: User, provider: str | None = None) -> None:
    """Eine Verknuepfung loesen - oder alle.

    ``provider`` loest genau diesen Anbieter; ohne Angabe faellt die ganze
    Verbindung zum Medienserver weg (das war das Verhalten, als es nur eine
    geben konnte).

    Wer danach weder Passwort noch bestaetigte Adresse haette, kaeme nicht mehr
    herein - das wird abgefangen, bevor hier etwas passiert.
    """
    if kaeme_nicht_mehr_herein(user, ohne=provider):
        raise KontoFehler(
            "mediaserver_would_lock_out",
            "Lege zuerst ein Passwort fest - sonst kämst du nicht mehr hinein.",
            status_code=409,
        )

    # Die betroffenen Zeilen aus der Tabelle nehmen. ``delete-orphan`` am
    # Benutzer raeumt sie beim Speichern weg.
    bleibt = [
        zeile
        for zeile in user.mediaserver_accounts
        if provider is not None and zeile.provider != provider
    ]
    user.mediaserver_accounts[:] = bleibt

    _spalten_spiegeln(user)


def kaeme_nicht_mehr_herein(user: User, ohne: str | None = None) -> bool:
    """Haette dieses Konto ohne den Medienserver noch einen Weg hinein?

    ``ohne`` nennt den Anbieter, der gerade wegfaellt - alle **anderen**
    Verknuepfungen zaehlen dann weiterhin als Weg hinein. Ohne Angabe faellt
    der Medienserver als Ganzes weg.

    Genau die Bedingung, an der ``unlink`` das eigene Trennen abweist - hier
    als eigene Funktion, weil der Administrator dieselbe Frage fuer **alle**
    Konten beantwortet bekommen muss, bevor er die ganze Verbindung trennt.

    Zwei Wege zaehlen: ein nutzbares Passwort, oder eine **bestaetigte**
    Adresse - dieselbe Bedingung wie in ``unlink``, absichtlich Wort fuer Wort.
    Zwei Stellen, die dieselbe Frage verschieden beantworten, waeren genau die
    Art Fehler, die man erst bemerkt, wenn jemand ausgesperrt ist.

    ⚠️ Die Bedingung ist dabei **strenger als noetig**, und das ist Absicht.
    "Passwort vergessen" fragt gar nicht nach der Bestaetigung; es verschickt
    an jede hinterlegte Adresse, und der Link setzt die Bestaetigung hinterher
    selbst. Wer eine unbestaetigte Adresse hat, kaeme also durchaus zurueck -
    er wird hier trotzdem als gefaehrdet gezaehlt. Ein Fehlalarm kostet ein
    ueberfluessiges Passwort, ein Uebersehen sperrt einen Menschen aus.
    """
    # ⚠️ **Ein zweiter Medienserver ist auch ein Weg hinein.** Wer Plex *und*
    # Jellyfin verknuepft hat und nur eines davon loest, meldet sich weiterhin
    # ueber das andere an. Ohne diese Zeile verlangte Nexview im Parallel-
    # betrieb ein Passwort fuer eine Trennung, die niemanden aussperrt.
    #
    # ⚠️⚠️ Nur bei ``ohne``. Ohne Angabe fallen **alle** Verknuepfungen - dann
    # ist keine davon ein Weg hinein, und die Frage beantwortet sich allein
    # ueber Passwort und Adresse. Diese Bedingung hat hier gefehlt, und damit
    # haette der Aussperrschutz genau in dem Fall geschwiegen, fuer den es ihn
    # gibt: beim Trennen des ganzen Medienservers.
    if ohne is not None and any(
        zeile.provider != ohne for zeile in user.mediaserver_accounts
    ):
        return False
    return not has_usable_password(user.password_hash) and not (
        user.email and user.email_verified
    )


def verknuepfte_konten(db: Session, provider: str | None = None) -> list[User]:
    """Alle Konten, die mit einem Medienserver verknuepft sind.

    ``provider`` grenzt auf einen Anbieter ein - gebraucht, bevor der
    Administrator *eine* Verbindung trennt: Betroffen sind dann nur die, die
    ueber genau diesen Server hereinkommen.

    Gefragt wird die Tabelle, nicht die Spalten am Benutzer. Die fuehren nur
    die zuletzt verknuepfte Identitaet - wer Plex und Jellyfin hat, taucht
    dort nur unter einem von beiden auf.
    """
    bedingung = [UserMediaServerAccount.user_id == User.id]
    if provider is not None:
        bedingung.append(UserMediaServerAccount.provider == provider)
    return list(
        db.scalars(
            select(User)
            .where(select(UserMediaServerAccount.id).where(*bedingung).exists())
            .order_by(User.username)
        )
    )


def find_linked(db: Session, account: ExternalAccount) -> User | None:
    """Wem gehoert diese fremde Identitaet?

    ⚠️ Ueber die Tabelle, nicht ueber die Spalten am Benutzer. Sonst faende
    diese Suche jemanden, der Plex *und* Jellyfin verknuepft hat, nur unter
    seinem zuletzt verbundenen Anbieter - und der andere Server legte ihm beim
    naechsten Anmelden ein **zweites** Nexview-Konto an.
    """
    return db.scalar(
        select(User)
        .join(UserMediaServerAccount, UserMediaServerAccount.user_id == User.id)
        .where(
            UserMediaServerAccount.provider == account.provider,
            UserMediaServerAccount.account_id == account.account_id,
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
        #
        # ⚠️ In der **Zeile** nachziehen, nicht in der Spalte: Die Spalte
        # gehoert der zuletzt verknuepften Identitaet. Wer sich mit Plex
        # anmeldet, waehrend Jellyfin die juengere Verknuepfung ist, haette
        # sonst seinen Plex-Namen in die Jellyfin-Anzeige geschrieben.
        zeile = verknuepfung(vorhanden, account.provider)
        if zeile is not None:
            zeile.username = account.username
            zeile.thumb = account.thumb
        _spalten_spiegeln(vorhanden)
        return vorhanden

    if account.email:
        nach_adresse = db.scalar(
            select(User).where(User.email == tokens.normalize_email(account.email))
        )
        if nach_adresse is not None:
            # ⚠️ Nur die Verknuepfung **dieses Anbieters** steht im Weg.
            #
            # Hier stand ``nach_adresse.mediaserver_account_id`` - die
            # Einzelspalte, die im Parallelbetrieb *irgendeine* Identitaet
            # nennt. Wer sein Jellyfin-Konto verknuepft hatte und sich dann
            # erstmals mit Plex anmeldete, wurde damit abgewiesen: "Zu dieser
            # Adresse gehoert bereits ein anderes Konto" - obwohl es sein
            # eigenes war und Plex noch gar nicht verknuepft.
            schon = verknuepfung(nach_adresse, account.provider)
            if schon is not None and schon.account_id != account.account_id:
                raise KontoFehler(
                    "mediaserver_link_conflict",
                    "Zu dieser Adresse gehört bereits ein anderes Konto des Media-Servers.",
                    status_code=409,
                )
            if not nach_adresse.is_active:
                raise KontoFehler("account_disabled", "Dieses Konto ist deaktiviert.")
            link(nach_adresse, account)
            return nach_adresse

    # ⚠️ **Ueber einen Anbieter ohne Adresse entsteht kein Konto.**
    #
    # Der Schritt darueber - "gleiche Adresse, also verknuepfen" - ist die
    # einzige Bruecke zu einem bestehenden Konto. Fehlt sie, kann Nexview
    # nicht unterscheiden, ob hier ein neuer Mensch steht oder jemand, der
    # laengst eines hat. Automatisch anzulegen hiesse, im zweiten Fall ein
    # **zweites** Konto zu erzeugen - ohne Adresse und ohne Passwort, also
    # eines, in das nur der Medienserver hineinfuehrt.
    #
    # Der Weg fuer diese Leute ist umgekehrt: erst anmelden wie gewohnt, dann
    # den Medienserver im Profil verknuepfen. Danach traegt Schritt 2.
    klasse = PROVIDERS.get(account.provider)
    if klasse is not None and not klasse.knows_email:
        raise KontoFehler(
            "mediaserver_no_new_account",
            f"Über {klasse.label} kann kein neues Konto entstehen. Melde dich mit "
            f"deinem Nexview-Konto an und verknüpfe {klasse.label} in deinem "
            "Profil – oder bitte den Administrator um eine Einladung.",
        )

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
        # **Ohne Grenze.** Frueher gab es dafuer drei eigene Einstellungen -
        # sie sind weggefallen, und zwar aus zwei Gruenden.
        #
        # Erstens waren sie im Speicher-Betrieb wirkungslos: Dort zaehlt der
        # belegte Platz, und ``requests_service._kontingent_pruefen`` steigt
        # aus, bevor die Stueckzahl ueberhaupt geprueft wird. Der Administrator
        # trug also Zahlen ein, die nichts taten.
        #
        # Zweitens ist die Stueckzahl nicht die Bremse, fuer die man sie haelt.
        # Die Bremse ist die Freigabe direkt darunter (``auto_approve=False``):
        # Ein neues Konto kann viel *fragen* und nichts holen. Wer einer
        # bestimmten Person Grenzen setzen will, tut das bei der Einladung -
        # die kennt sie weiterhin - oder hinterher in der Benutzerverwaltung.
        quota_movies = None
        quota_series = None
        periode = QuotaPeriod.week
        blocked_movies = ""
        blocked_series = ""

    benutzer = User(
        username=_unique_username(db, account.username),
        # ⚠️ **Hier stand einmal "wer eines will, setzt es spaeter im Profil".
        # Das war falsch.** Der Profil-Endpunkt verlangt das *aktuelle*
        # Passwort - und genau das hat ein so entstandenes Konto per Definition
        # nicht. Es kann sich also nicht selbst eines geben.
        #
        # Damit haengt dieses Konto **allein am Medienserver**. Faellt der weg,
        # gibt es zwei Wege zurueck, und beide liegen ausserhalb seiner
        # Reichweite:
        #
        # 1. **Mit Adresse:** "Passwort vergessen" genuegt. ``link`` setzt
        #    ``email_verified`` gleich mit (der Anbieter hat die Adresse ja
        #    geprueft), und der gemailte Link setzt es ohnehin. Braucht einen
        #    eingerichteten Mailserver.
        # 2. **Ohne Adresse** - Plex-Heimprofile geben keine heraus, und
        #    Jellyfin-Konten haben grundsaetzlich keine: Nur der Administrator
        #    kann helfen, ueber ``POST /api/users/{id}/password``. Danach meldet
        #    sich die Person damit an, bekommt "Adresse nicht bestaetigt" und
        #    traegt ueber den Notausgang selbst eine ein.
        #
        # Wer diesen Pfad anfasst, sollte ``kaeme_nicht_mehr_herein`` kennen:
        # Dort wird genau diese Frage beantwortet, und der Trenn-Knopf des
        # Administrators warnt danach.
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
        # **Kein Alter.** Es gab dafuer einmal eine Vorgabe; sie hat nie
        # gewirkt: ``db._altersgrenzen_aufraeumen`` setzt bei jedem Start das
        # Alter jedes Kontos zurueck, das kein Kinderkonto ist - und per
        # Auto-Import entsteht nie eines. Der Wert ueberlebte also bis zum
        # naechsten Neustart und verschwand dann lautlos.
        #
        # Fuer Kinder gibt es seit 0.16.0 den richtigen Weg: ein eigenes
        # Kinderkonto unter dem Konto der Eltern, wo das Alter auch bleibt.
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
