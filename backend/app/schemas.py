"""Datenformate der API (Pydantic)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Role
from .services.quota import UNBEGRENZT

# --- Kontingente: ein Wert, drei Bedeutungen ------------------------------
#
# Eine Grenze am Konto kann dreierlei heissen, und alle drei muessen ueber die
# Leitung passen:
#
# * ``"standard"``  -> es gilt der Standardwert des Hauses
# * ``"unlimited"`` -> ausdruecklich ohne Grenze
# * eine Zahl       -> genau diese; die **0 heisst "darf nichts"**
#
# ⚠️ Bewusst Woerter statt Zahlen-Sentinels. In der Datenbank stehen ``NULL``
# und ``-1``, aber ein Feld, in dem ``-1`` mal "Standard" und mal "unbegrenzt"
# bedeutet, ist genau die Verwechslung, die niemand bemerkt - und in der
# Oberflaeche haette am Ende jemand eine ``-1`` im Eingabefeld stehen.
Kontingentwert = int | Literal["standard", "unlimited"]


def kontingent_aus_wert(wert: Kontingentwert) -> int | None:
    """Wire-Wert -> Datenbank: ``None`` = Standard, ``-1`` = unbegrenzt."""
    if wert == "standard":
        return None
    if wert == "unlimited":
        return UNBEGRENZT
    return max(0, int(wert))


def kontingent_als_wert(wert: int | None) -> Kontingentwert:
    """Datenbank -> Wire. Die Umkehrung von ``kontingent_aus_wert``."""
    if wert is None:
        return "standard"
    if wert == UNBEGRENZT:
        return "unlimited"
    return max(0, wert)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")

# Bewusst niedrig gehalten, damit auch kurze Konten wie "user" moeglich sind.
# Wer Nexview von aussen erreichbar macht, sollte trotzdem lange Passwoerter
# vergeben - siehe README.
MIN_PASSWORD_LENGTH = 4


def _validate_username(value: str) -> str:
    value = value.strip()
    if not USERNAME_PATTERN.match(value):
        raise ValueError(
            "Benutzername: 3-32 Zeichen, erlaubt sind Buchstaben, Ziffern, Punkt, Bindestrich "
            "und Unterstrich."
        )
    return value


def _validate_password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.")
    return value


# --- Erst-Einrichtung -------------------------------------------------------


class SetupStatus(BaseModel):
    needs_setup: bool
    # Kommt vom Server, weil ``/api/config`` eine Anmeldung braucht - der
    # Assistent laeuft aber davor. Sonst koennten Hinweistext und Pruefung
    # auseinanderlaufen, und genau das ist hier schon einmal passiert.
    min_password_length: int = MIN_PASSWORD_LENGTH
    # Ist ein Media-Server verbunden? Nur dann zeigt die Anmeldeseite den
    # zusaetzlichen Knopf - ohne Verbindung soll davon nichts zu sehen sein.
    mediaserver_login: bool = False
    mediaserver_provider: str | None = None
    # ⚠️ **Welche** Anbieter einen Anmeldeweg bieten - und welchen.
    #
    # Die beiden Felder darueber reichten nicht: ``mediaserver_login`` sagt nur
    # "irgendeiner ist verbunden", und die Anmeldeseite machte daraus einen
    # fest beschrifteten "Mit Plex anmelden"-Knopf. Auf einer Installation mit
    # nur Jellyfin war das ein Knopf, der beim Klick scheiterte - der Ablauf
    # dahinter ist auf plex.tv zugeschnitten.
    mediaserver_login_ways: list[AnmeldeWeg] = []


class AnmeldeWeg(BaseModel):
    """Ein Anmeldeweg auf der Anmeldeseite.

    ``kind`` entscheidet, was der Knopf tut: ``"pin"`` oeffnet das Fenster des
    Anbieters, ``"password"`` klappt ein Formular auf.
    """

    provider: str
    label: str
    kind: str


class SetupAdminCreate(BaseModel):
    username: str
    password: str
    email: str = Field(min_length=3, max_length=255)
    display_name: str | None = None
    language: str = "de"

    _check_username = field_validator("username")(_validate_username)
    _check_password = field_validator("password")(_validate_password)


# --- Anmeldung -------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenPair(BaseModel):
    """Was eine Anmeldung zurueckgibt.

    ⚠️ **Das Erneuerungs-Token steht bewusst nicht mehr hier drin.** Es
    verlaesst das Backend seit 0.21 ausschliesslich als HttpOnly-Cookie, damit
    kein Skript im Browser es lesen kann - die Begruendung steht in
    ``services/sitzung.py``. Der Name bleibt trotzdem: Es sind weiterhin zwei
    Token, eines davon nimmt nur einen anderen Weg.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int


# --- Benutzer --------------------------------------------------------------


class VerknuepftesKonto(BaseModel):
    """Ein Medienserver-Konto eines Benutzers - fuer die Anzeige.

    Ohne Token und ohne Adresse: Beides geht den Browser nichts an. Der Name
    steht drin, weil "Verbunden mit Jonas" etwas sagt und eine Kontonummer
    nicht.
    """

    model_config = ConfigDict(from_attributes=True)

    provider: str
    username: str | None = None


class OidcVerknuepfung(BaseModel):
    """Eine OIDC-Anmeldung am eigenen Konto - fuer die Anzeige im Profil.

    Die Anbieter-Adresse statt eines Kuerzels, weil die Verknuepfung an ihr
    haengt: Das Profil ordnet sie ueber die oeffentliche Anbieter-Liste einer
    Beschriftung zu - und zeigt die Adresse selbst, wenn der Eintrag des
    Administrators inzwischen weg ist. ``subject`` bleibt drinnen: Es sagt
    einem Menschen nichts.
    """

    model_config = ConfigDict(from_attributes=True)

    issuer: str
    display: str | None = None


class UserPublic(BaseModel):
    """Eigenes Profil bzw. Benutzer in Admin-Listen."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Role
    display_name: str | None
    # NULL nur bei Konten aus der Zeit vor der Mailpflicht.
    email: str | None
    email_verified: bool
    language: str
    theme: str
    is_active: bool
    # Traegt dieses Konto den Betreiber-Haken?
    #
    # ⚠️ **Nur zur Anzeige - er gibt kein Recht.** Die Oberflaeche braucht ihn
    # an zwei Stellen: fuer das Abzeichen in der Benutzerliste und um die
    # Knoepfe auszugrauen, die an diesem Konto nicht gehen. Wer ihn irgendwo
    # als Erlaubnis liest, hat ihn missverstanden - die Sperren sitzen im
    # Backend (``deps.betreiberschutz``), nicht hier.
    is_betreiber: bool = False
    auto_approve: bool
    # Je Medienart gesetzt; None heisst "es gilt der alte gemeinsame Haken".
    auto_approve_movies: bool | None
    auto_approve_series: bool | None
    # Was tatsaechlich gilt - bei Administratoren immer true.
    effective_auto_approve: bool
    effective_auto_approve_movies: bool
    effective_auto_approve_series: bool
    can_approve: bool
    # Alle drei Grenzen sprechen dieselbe Sprache: "standard", "unlimited"
    # oder eine Zahl. Der Zeitraum steht **nicht** mehr am Konto - er gilt
    # haus-weit und kommt aus den Einstellungen.
    quota_movies_limit: Kontingentwert
    quota_series_limit: Kontingentwert
    storage_limit_gb: Kontingentwert = "standard"

    @field_validator(
        "quota_movies_limit",
        "quota_series_limit",
        "storage_limit_gb",
        mode="before",
    )
    @classmethod
    def _als_wert(cls, wert: object) -> object:
        """Aus der Datenbank kommen ``None`` und ``-1``, nach aussen Woerter."""
        return kontingent_als_wert(wert) if wert is None or isinstance(wert, int) else wert
    # Wann der Admin das Kontingent zuletzt von Hand zurueckgesetzt hat.
    quota_reset_at: datetime | None
    # Wann dieses Konto die Hausordnung zuletzt abgehakt hat. ``None`` heisst
    # "noch nie" - der Betreiber sieht in der Nutzerverwaltung, wen er noch
    # erinnern muss. Die Spalte erscheint dort nur, wenn ueberhaupt eine
    # Hausordnung veroeffentlicht ist.
    hausordnung_gelesen_am: datetime | None = None
    blocked_movie_profiles: list[int]
    blocked_series_profiles: list[int]
    # --- 4K -----------------------------------------------------------------
    can_request_uhd_movies: bool
    can_request_uhd_series: bool
    auto_approve_uhd: bool
    effective_auto_approve_uhd: bool
    blocked_movie_uhd_profiles: list[int]
    blocked_series_uhd_profiles: list[int]
    avatar_url: str | None
    created_at: datetime
    last_login_at: datetime | None

    # Benachrichtigungen per Mail - Standard ist ueberall "aus".
    mail_download_complete: bool
    mail_request_pending: bool
    mail_request_decided: bool
    mail_feedback: bool
    mail_ticket: bool
    mail_watch: bool
    mail_user_imported: bool
    mail_mediaserver_reconnect: bool
    mail_storage: bool
    mail_child_wish: bool
    mail_cleanup: bool

    # *Ob* ein Plex-Zugang hinterlegt ist - das Token selbst verlaesst den
    # Server nie. Ohne ihn laesst sich die Merkliste nicht lesen.
    watchlist_connected: bool
    # Hat Plex den hinterlegten Zugang abgelehnt? Dann hilft nur eine neue
    # Anmeldung - und nur die betroffene Person kann sie machen. Deshalb steht
    # es hier und nicht in einer Admin-Auskunft.
    watchlist_token_invalid: bool = False

    # --- Verknuepfung mit dem Media-Server ---------------------------------
    # Die Kennung selbst wird bewusst nicht ausgeliefert; fuer die Oberflaeche
    # zaehlt nur, *ob* verknuepft ist und mit welchem Namen.
    mediaserver_provider: str | None
    mediaserver_username: str | None
    mediaserver_linked: bool
    # **Alle** Verknuepfungen, je eine Zeile. Die beiden Felder darueber
    # nennen nur die zuletzt hinzugekommene - im Parallelbetrieb also
    # willkuerlich eine von zweien. Wer nach einem bestimmten Anbieter fragt,
    # muss hier suchen.
    mediaserver_accounts: list[VerknuepftesKonto] = []
    # Dieselbe Liste fuer die genormte Anmeldung - je Anbieter eine Zeile.
    oidc_links: list[OidcVerknuepfung] = []
    # Wer sich nur ueber den Media-Server anmeldet, hat kein Passwort. Das
    # Profil braucht die Auskunft, um "Passwort festlegen" anzubieten - und um
    # das Trennen zu verhindern, das aussperren wuerde.
    has_password: bool

    # Vorbelegung der Filterleiste; NULL = nichts Eigenes eingestellt.
    discover_region: str | None

    # Altersbeschraenkung. Wird mit ausgegeben, damit der Betroffene in seinem
    # Profil sieht, dass eine Grenze gilt - sonst wirkte die Anwendung fuer ihn
    # einfach luecken- und grundlos leerer als fuer andere. NULL = keine.
    age: int | None
    rating_region: str | None
    hide_unrated: bool

    # Darf dieses Konto Kinderkonten anlegen? Bei Administratoren immer.
    can_manage_children: bool = False

    # Bei einem Kinderkonto das erwachsene Konto, dem es gehoert; sonst NULL.
    # Nur die Nummer, kein Name: Die Benutzerliste des Administrators enthaelt
    # ohnehin alle Konten, sie kann den Namen selbst nachschlagen.
    parent_id: int | None = None

    @field_validator(
        "blocked_movie_profiles",
        "blocked_series_profiles",
        "blocked_movie_uhd_profiles",
        "blocked_series_uhd_profiles",
        mode="before",
    )
    @classmethod
    def _split_profiles(cls, value: object) -> object:
        """In der Datenbank steht eine Komma-Liste, nach aussen eine echte Liste."""
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip().isdigit()]
        return value


class UserWithUsage(UserPublic):
    """Benutzer in der Admin-Liste - mit dem Verbrauch im laufenden Zeitraum.

    Ohne diese Zahlen waere der Knopf "Kontingent zurücksetzen" ein Blindflug:
    der Admin saehe nicht, ob ueberhaupt etwas zurueckzusetzen ist.
    """

    quota_movies_used: int
    quota_series_used: int


class VerificationSent(BaseModel):
    sent: bool
    error: str | None = None


def _keine_kinderrolle(value: Role | None) -> Role | None:
    """Die Rolle ``child`` darf hier nicht vorkommen.

    Ein Kinderkonto gehoert immer zu genau einem Elternteil. Entstuende es auf
    einem anderen Weg - ueber eine Einladung oder eine Rollenaenderung -, waere
    es elternlos: niemand koennte sein Passwort setzen, seine Wuensche
    freigeben oder es wieder loeschen. Kinder entstehen und vergehen deshalb
    ausschliesslich ueber ``/api/children``.
    """
    if value == Role.child:
        raise ValueError(
            "Kinderkonten werden im Profil unter „Kinder“ angelegt, nicht hier."
        )
    return value


class InvitationCreate(BaseModel):
    """Einladung: der Eingeladene waehlt Benutzername und Namen selbst."""

    email: str = Field(min_length=3, max_length=255)
    role: Role = Role.user
    quota_movies_limit: Kontingentwert = "standard"
    quota_series_limit: Kontingentwert = "standard"

    _check_role = field_validator("role")(_keine_kinderrolle)


class InvitationPublic(BaseModel):
    id: int
    email: str
    role: Role
    created_at: datetime
    expires_at: datetime


class InvitationCreated(InvitationPublic):
    mail_sent: bool
    mail_error: str | None = None
    manual_link: str | None = None




class UserUpdate(BaseModel):
    role: Role | None = None

    # Auch hier keine Kinderrolle - und das ist mehr als Ordnungsliebe:
    # ``update_user`` prueft "verliert das Haus seinen letzten Administrator"
    # an ``role == user``. Waere ``child`` erlaubt, liesse sich der letzte
    # Administrator daran vorbei zum Kind herabstufen, und niemand kaeme mehr
    # an die Verwaltung.
    _check_role = field_validator("role")(_keine_kinderrolle)
    display_name: str | None = None
    language: str | None = None
    is_active: bool | None = None
    auto_approve: bool | None = None
    auto_approve_movies: bool | None = None
    auto_approve_series: bool | None = None
    # ``None`` heisst hier durchgehend "nicht mitgeschickt" - deshalb tragen
    # die drei Grenzen Woerter fuer "Standard" und "unbegrenzt" und keine
    # Zahlen-Sentinels.
    quota_movies_limit: Kontingentwert | None = None
    quota_series_limit: Kontingentwert | None = None
    storage_limit_gb: Kontingentwert | None = None
    # Leere Liste bedeutet: alle Qualitaetsprofile erlaubt.
    blocked_movie_profiles: list[int] | None = None
    blocked_series_profiles: list[int] | None = None
    # --- 4K -----------------------------------------------------------------
    can_request_uhd_movies: bool | None = None
    can_request_uhd_series: bool | None = None
    auto_approve_uhd: bool | None = None
    blocked_movie_uhd_profiles: list[int] | None = None
    blocked_series_uhd_profiles: list[int] | None = None

    # ⚠️ **Die Altersbeschraenkung steht hier nicht mehr.** Wer ein
    # vollwertiges Konto hat, gilt als volljaehrig; Kinder bekommen ein
    # Kinderkonto, und ihr Alter pflegt das Elternteil unter "Kinder".
    # Zwei Wege zu derselben Sperre waeren zwei Stellen, an denen sie
    # auseinanderlaufen kann - und der Administrator muesste raten, welcher
    # gilt. ``User.age`` bleibt in der Datenbank: Fuer Kinderkonten ist es
    # genau das Feld, an dem die Sperre haengt.

    # Wer Kinderkonten anlegen darf, entscheidet der Administrator.
    can_manage_children: bool | None = None


# --- Kinderkonten ----------------------------------------------------------


class ChildPublic(BaseModel):
    """Ein Kinderkonto, wie das Elternteil es sieht.

    Bewusst schmal: Kontingente, Mailschalter, 4K-Rechte und Media-Server
    haben bei einem Kinderkonto keine Bedeutung. ``UserPublic`` hier
    wiederzuverwenden hiesse, zwei Dutzend Felder auszuliefern, die alle
    dasselbe sagen - naemlich nichts.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    username: str
    display_name: str | None
    age: int | None
    is_active: bool
    language: str
    # Darf dieses Kind Trailer ansehen? Standard an.
    child_trailers: bool = True
    # Welche Rubriken dieses Kind sieht. Leere Liste = alle.
    #
    # Die Spalte heisst ``child_genres`` - am Kinderkonto waere "child" doppelt
    # gemoppelt, an einem gemeinsamen Benutzer-Modell dagegen noetig.
    genres: list[str] = Field(validation_alias="child_genres")
    created_at: datetime
    last_login_at: datetime | None

    @field_validator("genres", mode="before")
    @classmethod
    def _split_genres(cls, value: object) -> object:
        """In der Datenbank eine Komma-Liste, nach aussen eine echte Liste."""
        if isinstance(value, str):
            return [teil for teil in value.split(",") if teil]
        return value


# Bis zu welchem Alter ein Kinderkonto reicht.
#
# Darueber ist es kein Kinderkonto mehr, sondern ein gewoehnliches: Wer 17 ist,
# soll eine eigene Mailadresse und ein eigenes Kontingent bekommen, nicht eine
# Unteransicht ohne Benachrichtigungen. Vom Nutzer so festgelegt.
MAX_CHILD_AGE = 16


class ChildCreate(BaseModel):
    username: str
    password: str
    # Pflichtangabe - ein Kinderkonto ohne Alter waere ein gewoehnliches Konto
    # mit weniger Rechten und saehe alles. Genau das soll es nicht sein.
    age: int = Field(ge=0, le=MAX_CHILD_AGE)
    display_name: str | None = None
    # Leer bzw. nicht mitgeschickt heisst "alle Rubriken".
    genres: list[str] | None = None
    child_trailers: bool = True
    # Fehlt sie, gilt die Sprache des Elternteils - der Normalfall.
    language: str | None = Field(default=None, max_length=5)

    _check_username = field_validator("username")(_validate_username)
    _check_password = field_validator("password")(_validate_password)


class ChildUpdate(BaseModel):
    """Was das Elternteil nachtraeglich aendern darf.

    Der Benutzername steht bewusst nicht dabei: Er ist die Anmeldung, und ein
    stiller Wechsel waere fuer das Kind nicht nachvollziehbar.
    """

    display_name: str | None = None
    age: int | None = Field(default=None, ge=0, le=MAX_CHILD_AGE)
    is_active: bool | None = None
    genres: list[str] | None = None
    child_trailers: bool | None = None
    language: str | None = Field(default=None, max_length=5)


class ChildPassword(BaseModel):
    password: str

    _check_password = field_validator("password")(_validate_password)


class PasswordReset(BaseModel):
    """Admin setzt das Passwort eines Benutzers neu."""

    password: str

    _check_password = field_validator("password")(_validate_password)


class ProfileUpdate(BaseModel):
    """Was ein Benutzer an sich selbst aendern darf."""

    display_name: str | None = None
    language: str | None = None
    theme: str | None = None

    # Benachrichtigungen per Mail - jede einzeln.
    mail_download_complete: bool | None = None
    mail_request_pending: bool | None = None
    mail_request_decided: bool | None = None
    mail_feedback: bool | None = None
    mail_ticket: bool | None = None
    mail_watch: bool | None = None
    mail_user_imported: bool | None = None
    mail_mediaserver_reconnect: bool | None = None
    mail_storage: bool | None = None
    mail_child_wish: bool | None = None
    mail_cleanup: bool | None = None

    # Vorbelegung der Filterleiste. Der leere String bedeutet "nichts
    # Eigenes" - anders liesse sich eine einmal gesetzte Wahl nie wieder
    # aufheben, weil ``None`` ja schon "Feld nicht mitgeschickt" heisst.
    discover_region: str | None = Field(default=None, max_length=2)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str

    _check_password = field_validator("new_password")(_validate_password)
