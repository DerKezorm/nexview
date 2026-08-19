"""Datenbank-Tabellen von Nexview."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def enum_column(enum_class: type[enum.Enum]) -> Enum:
    """Aufzaehlung als Text speichern - und beim Lesen zurueckverwandeln.

    Ohne das liefert SQLAlchemy beim Laden eine reine Zeichenkette. Vergleiche
    funktionieren dann zwar noch (die Klassen erben von ``str``), aber ``.value``
    schlaegt fehl - ein Fehler, der erst spaet auffaellt.
    """
    return Enum(
        enum_class,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    admin = "admin"
    # Darf Anfragen freigeben, ablehnen und abbrechen - sonst nichts.
    approver = "approver"
    user = "user"


class MediaType(str, enum.Enum):
    movie = "movie"
    tv = "tv"


class RequestStatus(str, enum.Enum):
    """Lebenslauf einer Anfrage.

    pending_approval -> approved -> searching -> downloaded
    Seitenwege: rejected (Admin lehnt ab), failed (Radarr/Sonarr-Fehler).
    """

    pending_approval = "pending_approval"
    approved = "approved"
    searching = "searching"
    downloaded = "downloaded"
    rejected = "rejected"
    failed = "failed"
    # Vom Anfragenden oder einem Entscheider abgebrochen - zaehlt nicht
    # mehr gegen das Kontingent.
    cancelled = "cancelled"


class QuotaPeriod(str, enum.Enum):
    day = "day"
    week = "week"
    month = "month"


class NotificationType(str, enum.Enum):
    download_complete = "download_complete"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"
    request_pending = "request_pending"
    # Rueckmeldung zur Qualitaet - geht an die Entscheider.
    feedback = "feedback"
    feedback_poor = "feedback_poor"
    # Antwort des Administrators auf eine Rueckmeldung.
    feedback_reply = "feedback_reply"
    # --- Ticketcenter ------------------------------------------------------
    # Neues Ticket bzw. neue Nachricht des Benutzers - geht an die Admins.
    ticket_new = "ticket_new"
    # Antwort des Admins oder Zustandswechsel - geht an den Eigentuemer.
    ticket_reply = "ticket_reply"
    # --- Media-Server ------------------------------------------------------
    # Jemand hat sich zum ersten Mal ueber den Media-Server angemeldet und
    # dabei ein Konto bekommen - geht an die Admins. Ohne diesen Hinweis
    # taeten neue Konten das lautlos.
    user_imported = "user_imported"


class User(Base):
    __tablename__ = "users"
    # Ein Media-Server-Konto gehoert zu genau einem Nexview-Konto.
    #
    # Bewusst ein eindeutiger **Index** und kein ``UniqueConstraint``: SQLite
    # kann einer bestehenden Tabelle keine Constraints nachtragen, einen Index
    # dagegen schon. Auf einer aktualisierten Datenbank waere die Regel sonst
    # stillschweigend wirkungslos - und genau das faellt niemandem auf, weil
    # die Tests immer auf frischen Tabellen laufen.
    #
    # Der Index faengt nebenbei ab, dass jemand zweimal gleichzeitig auf
    # "Anmelden" drueckt: der zweite Versuch laeuft in einen IntegrityError und
    # verknuepft, statt ein zweites Konto anzulegen.
    __table_args__ = (
        Index(
            "ix_users_mediaserver_konto",
            "mediaserver_provider",
            "mediaserver_account_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Immer klein geschrieben gespeichert - so ist die Eindeutigkeit unabhaengig
    # davon, wie jemand seine Adresse tippt. NULL nur bei Konten aus der Zeit
    # vor der Mailpflicht; die tragen sie beim naechsten Anmelden nach.
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    role: Mapped[Role] = mapped_column(enum_column(Role), default=Role.user, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(5), default="de", nullable=False)
    # Helle oder dunkle Darstellung. Gehoert zum Konto, nicht zum Browser:
    # so findet jeder seine Einstellung auf jedem Geraet wieder.
    theme: Mapped[str] = mapped_column(String(5), default="dark", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Freigabe- und Kontingent-Regeln (vom Admin pro Benutzer einstellbar)
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quota_movies_limit: Mapped[int | None] = mapped_column(Integer)  # NULL = unbegrenzt
    quota_series_limit: Mapped[int | None] = mapped_column(Integer)  # NULL = unbegrenzt
    quota_period: Mapped[QuotaPeriod] = mapped_column(
        enum_column(QuotaPeriod), default=QuotaPeriod.week, nullable=False
    )
    # Setzt der Admin das Kontingent zurueck, zaehlt ab diesem Zeitpunkt neu.
    # Die Anfragen selbst bleiben erhalten - nur der Verbrauch beginnt von vorn.
    quota_reset_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Erlaubte Qualitaetsprofile als Komma-Liste von Radarr-/Sonarr-Kennungen.
    # Leer bedeutet: keine Einschraenkung.
    blocked_movie_profiles: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    blocked_series_profiles: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    avatar_path: Mapped[str | None] = mapped_column(String(255))

    # --- Verknuepfung mit dem Media-Server ---------------------------------
    # Wer sein Konto verbunden hat, meldet sich wahlweise damit oder weiter
    # mit Passwort an - es bleibt dasselbe Nexview-Konto.
    #
    # Bewusst **nicht** ``plex_...`` benannt: Jellyfin und Emby sollen spaeter
    # dieselben Spalten benutzen. Anbieter sind Alternativen, keine parallelen
    # Identitaeten - eine Person hat genau eine davon, deshalb reicht ein Satz
    # Spalten statt einer eigenen Tabelle.
    #
    # Verglichen wird immer die Kennung, nie Name oder Adresse - beide kann man
    # beim Anbieter jederzeit aendern.
    mediaserver_provider: Mapped[str | None] = mapped_column(String(20))
    mediaserver_account_id: Mapped[str | None] = mapped_column(String(64))
    mediaserver_username: Mapped[str | None] = mapped_column(String(120))
    mediaserver_email: Mapped[str | None] = mapped_column(String(255))
    mediaserver_thumb: Mapped[str | None] = mapped_column(String(500))
    mediaserver_linked_at: Mapped[datetime | None] = mapped_column(DateTime)

    # --- Benachrichtigungen per Mail --------------------------------------
    # Bewusst alle auf "aus": ungefragt Mails zu verschicken ist der sicherste
    # Weg, jemandem die Anwendung zu verleiden. Die Glocke in der App ist davon
    # unberuehrt und bleibt immer an.
    mail_download_complete: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Nur fuer Admins und Entscheider von Belang - alle anderen sehen den
    # Schalter gar nicht erst.
    mail_request_pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mail_request_decided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mail_feedback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Tickets: neue Anliegen (fuer Admins) und Antworten darauf (fuer den
    # Eigentuemer) haengen bewusst am selben Schalter - vier Schalter sind
    # ueberschaubar, sechs waeren eine Zumutung.
    mail_ticket: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Neues Konto ueber den Media-Server - nur fuer Admins von Belang.
    mail_user_imported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Vorbelegung der Filterleiste beim Entdecken ----------------------
    # NULL heisst "nichts Eigenes eingestellt", dann gilt die Vorgabe des
    # Admins.
    #
    # Eine Vorbelegung der *Originalsprache* gab es hier kurzzeitig auch. Sie
    # ist wieder entfallen: als Dauereinstellung war sie eine Falle. "Deutsch"
    # bedeutet "auf Deutsch gedreht", und solche Titel sind so selten, dass die
    # Entdecken-Seite praktisch leer blieb - gemessen 23 Titel gegen 0. Die
    # Filterleiste bietet den Filter weiterhin an; dort sieht man ihn und hat
    # ihn gerade selbst gesetzt.
    discover_region: Mapped[str | None] = mapped_column(String(2))

    # --- Altersbeschraenkung ----------------------------------------------
    # Das Alter des Benutzers in Jahren. NULL heisst "nicht altersbeschraenkt"
    # und ist der Normalfall - erwachsene Mitbenutzer bekommen hier nie etwas
    # eingetragen.
    #
    # Gezeigt wird, was hoechstens ab diesem Alter freigegeben ist: bei 12 also
    # FSK 0, 6 und 12; alles darueber verschwindet vollstaendig. Ein freies
    # Alter statt einer Auswahl der FSK-Stufen, weil "wie alt ist das Kind" die
    # Frage ist, die der Admin beantworten kann - ein 14-Jaehriger sieht damit
    # bis FSK 12 und nicht bis 16.
    #
    # Beide Felder darf **nur der Administrator** setzen. Sie stehen deshalb
    # ausschliesslich in ``UserUpdate``, nie in ``ProfileUpdate``: haetten sie
    # in der eigenen Profilaenderung Platz, koennte der Beschraenkte die Sperre
    # mit einem einzigen Aufruf von PATCH /api/auth/me selbst aufheben.
    age: Mapped[int | None] = mapped_column(Integer)

    # Was mit Titeln geschieht, die nirgends eingestuft sind. Standard ist
    # verbergen - "kein Nachweis, kein Zutritt". Abschaltbar, weil neue Titel
    # meist noch keine Einstufung haben: gemessen schrumpfte die
    # Entdecken-Seite dadurch von 20 auf 2 Eintraege. Wirkt nur zusammen mit
    # einer gesetzten Altersbeschraenkung.
    hide_unrated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Nach welchem Land geurteilt wird. NULL = die Vorgabe des Admins.
    #
    # Bewusst getrennt von ``discover_region``: die darf der Benutzer selbst
    # aendern. Wer die Sperre an ihr messen wuerde, muesste nur sein Land auf
    # eines umstellen, in dem der Titel nicht eingestuft ist - und waere
    # vorbei. Pro Benutzer und nicht global, weil ein geteilter Zugang
    # (Plex-Runde) durchaus ueber Laender verteilt sein kann.
    rating_region: Mapped[str | None] = mapped_column(String(2))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    def blocked_profiles(self, media_type: "MediaType") -> list[int]:
        """Gesperrte Profil-Kennungen; leere Liste = nichts gesperrt.

        Bewusst als Sperrliste: so bedeutet jeder Haken genau eine Sperre.
        Als Erlaubnisliste haette der erste Haken alle anderen Profile auf
        einen Schlag verboten - ein ueberraschender Nebeneffekt.
        """
        raw = (
            self.blocked_movie_profiles
            if media_type == MediaType.movie
            else self.blocked_series_profiles
        )
        return [int(part) for part in raw.split(",") if part.strip().isdigit()]

    requests: Mapped[list["MediaRequest"]] = relationship(
        back_populates="user",
        foreign_keys="MediaRequest.user_id",
        cascade="all, delete-orphan",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == Role.admin

    @property
    def can_approve(self) -> bool:
        """Darf ueber fremde Anfragen entscheiden."""
        return self.role in (Role.admin, Role.approver)

    @property
    def effective_auto_approve(self) -> bool:
        """Gilt die Anfrage sofort als freigegeben?

        Wer selbst freigeben darf - Administratoren und Entscheider -, gibt
        sich nicht erst selbst frei. Das waere eine sinnlose Zwischenstufe, und
        der Haken laesst sich fuer sie deshalb gar nicht abschalten.
        """
        return self.can_approve or self.auto_approve

    @property
    def avatar_url(self) -> str | None:
        """Adresse des Profilbilds fuer die Oberflaeche."""
        return f"/api/users/avatar/{self.avatar_path}" if self.avatar_path else None

    @property
    def mediaserver_linked(self) -> bool:
        return bool(self.mediaserver_provider and self.mediaserver_account_id)

    @property
    def has_password(self) -> bool:
        """Kann sich dieses Konto auch mit Passwort anmelden?

        Wer ueber den Media-Server angelegt wurde, hat zunaechst keines. Ohne
        diese Auskunft wuerde das Profil ihm anbieten, die Verknuepfung zu
        loesen - und er kaeme nicht mehr herein.
        """
        # Der Import steht hier, weil ``security`` seinerseits die Modelle
        # nicht braucht - oben waere es ein Ringschluss.
        from .security import has_usable_password

        return has_usable_password(self.password_hash)


class TokenPurpose(str, enum.Enum):
    """Wofuer ein Einmal-Link gilt."""

    invitation = "invitation"
    email_verification = "email_verification"
    password_reset = "password_reset"
    # Ein angefangener Anmeldevorgang beim Media-Server. Kein Link zum
    # Anklicken, sondern der Merkzettel dazu: Der Browser bekommt nur diese
    # Kennung und schickt sie beim Nachfragen zurueck. Die PIN von Plex bleibt
    # dadurch im Backend - sonst koennte jemand, der sie mitliest, eine fremde
    # Anmeldung zu Ende fuehren.
    mediaserver_login = "mediaserver_login"


class AuthToken(Base):
    """Einmal-Links fuer Einladung, Adressbestaetigung und Passwort-Reset.

    Gespeichert wird nur die Pruefsumme des Links, nie der Link selbst: wer die
    Datenbank in die Haende bekommt, kann damit kein Konto uebernehmen.
    """

    __tablename__ = "auth_tokens"
    __table_args__ = (Index("ix_auth_tokens_purpose_email", "purpose", "email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose: Mapped[TokenPurpose] = mapped_column(enum_column(TokenPurpose), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Bei Einladungen gibt es noch kein Konto - dann bleibt user_id leer.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(255), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # Nur bei Einladungen: was fuer das neue Konto voreingestellt sein soll.
    # Den Benutzernamen waehlt der Eingeladene selbst.
    invite_role: Mapped[Role | None] = mapped_column(enum_column(Role))
    invite_quota_movies: Mapped[int | None] = mapped_column(Integer)
    invite_quota_series: Mapped[int | None] = mapped_column(Integer)
    invite_quota_period: Mapped[QuotaPeriod | None] = mapped_column(enum_column(QuotaPeriod))
    invite_blocked_movie_profiles: Mapped[str] = mapped_column(
        String(255), default="", nullable=False
    )
    invite_blocked_series_profiles: Mapped[str] = mapped_column(
        String(255), default="", nullable=False
    )

    # Nur bei ``mediaserver_login``: Anbieter, PIN-Nummer und Code als JSON -
    # bei der Ersteinrichtung zusaetzlich das bereits geholte Token des
    # Anbieters, damit es fuer die Server-Auswahl nicht durch den Browser
    # laufen muss. Es liegt dort verschluesselt und nur fuer wenige Minuten.
    mediaserver_ref: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User | None] = relationship(foreign_keys=[user_id])

    @property
    def expired(self) -> bool:
        return utcnow().replace(tzinfo=None) > self.expires_at

    @property
    def open(self) -> bool:
        """Noch einloesbar - weder verbraucht noch abgelaufen."""
        return self.used_at is None and not self.expired


class MediaServerBlock(Base):
    """Media-Server-Konten, die sich nicht mehr selbst anlegen duerfen.

    Ohne diese Liste waere das Loeschen eines Benutzers wirkungslos: Wer
    Zugriff auf die Bibliothek hat, meldet sich einfach neu an und bekommt
    sofort wieder ein Konto. Der Eintrag ueberlebt den Benutzer deshalb
    absichtlich - der Administrator kann ihn jederzeit wieder aufheben.

    ``provider`` haelt die Liste offen fuer Jellyfin und Emby; die Kennung
    allein waere nicht eindeutig, wenn zwei Anbieter im Spiel sind.
    """

    __tablename__ = "media_server_blocks"
    __table_args__ = (
        UniqueConstraint("provider", "account_id", name="uq_media_server_blocks_konto"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nur zur Anzeige - damit in der Liste nicht bloss Nummern stehen.
    username: Mapped[str | None] = mapped_column(String(120))
    blocked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Die Sperre ueberlebt das Konto, das sie gesetzt hat.
    blocked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Setting(Base):
    """Konfiguration als Schluessel/Wert-Paare (TMDB-, Radarr-, Sonarr-Zugang)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MediaRequest(Base):
    __tablename__ = "media_requests"
    __table_args__ = (
        Index("ix_media_requests_user", "user_id"),
        Index("ix_media_requests_lookup", "media_type", "tmdb_id"),
        Index("ix_media_requests_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tvdb_id: Mapped[int | None] = mapped_column(Integer)  # nur Serien, fuer Sonarr

    # Nur bei Serien: die angefragte Staffel. NULL bedeutet "ganze Serie" - so
    # bleiben alle bisherigen Anfragen unveraendert gueltig.
    season: Mapped[int | None] = mapped_column(Integer)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # Trotz des Namens die **fertige Adresse**, nicht der Pfad-Teil von
    # TMDB: gespeichert wird ``item.poster_url``. Wer hier noch ein
    # ``image_url()`` herumlegt, bekommt das Praefix zweimal - genau das
    # ist zweimal passiert.
    poster_path: Mapped[str | None] = mapped_column(String(255))
    release_date: Mapped[str | None] = mapped_column(String(10))

    status: Mapped[RequestStatus] = mapped_column(
        enum_column(RequestStatus), default=RequestStatus.pending_approval, nullable=False
    )

    quality_profile_id: Mapped[int | None] = mapped_column(Integer)
    quality_profile_name: Mapped[str | None] = mapped_column(String(120))
    root_folder_path: Mapped[str | None] = mapped_column(String(500))
    arr_id: Mapped[int | None] = mapped_column(Integer)  # ID in Radarr/Sonarr

    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text)

    # Rueckmeldung des Anfragenden zur Qualitaet des Downloads (0-5 Sterne).
    # Administratoren bewerten nicht - sie antworten nur darauf.
    rating: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[str | None] = mapped_column(Text)
    rated_at: Mapped[datetime | None] = mapped_column(DateTime)

    feedback_reply: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime)
    replied_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    user: Mapped[User] = relationship(back_populates="requests", foreign_keys=[user_id])
    approver: Mapped[User | None] = relationship(foreign_keys=[approved_by])


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_unread", "user_id", "is_read"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_requests.id", ondelete="CASCADE")
    )
    # Damit die Glocke direkt ins Ticket fuehrt statt nur auf die Liste.
    ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE")
    )
    type: Mapped[NotificationType] = mapped_column(enum_column(NotificationType), nullable=False)
    # Uebersetzungsschluessel + Titel, damit die Meldung in DE/EN dargestellt werden kann
    message_key: Mapped[str] = mapped_column(String(64), nullable=False)
    message_title: Mapped[str | None] = mapped_column(String(300))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    # --- Postausgang -------------------------------------------------------
    # Ob eine Mail rausgehen soll, entscheidet sich beim Anlegen; verschickt
    # wird sie erst danach im Hintergrund. Zwei Gruende: ein Mailserver kann
    # Sekunden brauchen oder gar nicht antworten - daran darf kein Klick
    # haengen. Und ein Neustart mitten im Versand verliert nichts, weil der
    # Auftrag in der Datenbank steht.
    mail_pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mail_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    mail_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped[User] = relationship(back_populates="notifications")


class TicketStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    closed = "closed"


class Ticket(Base):
    """Ein Anliegen eines Benutzers an den Administrator.

    Abgrenzung zur **Rueckmeldung** an einer Anfrage (``MediaRequest.rating`` /
    ``feedback`` / ``feedback_reply``): die klebt an einem heruntergeladenen
    Titel und endet nach einer Antwort. Ein Ticket ist ein Gespraech mit
    beliebig vielen Beitraegen und einem Zustand - fuer alles, was keinen
    fertigen Download betrifft ("ich komme nicht rein", "warum ist X
    gesperrt"). Beide bleiben nebeneinander bestehen; so bewusst entschieden.

    Sehen darf ein Ticket nur, wem es gehoert - und der Administrator. Ein
    Entscheider ist hier ausdruecklich **kein** Administrator: er entscheidet
    ueber Anfragen, liest aber nicht die Post der anderen.
    """

    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        enum_column(TicketStatus), default=TicketStatus.open, nullable=False
    )

    # --- Optionaler Bezug zu einem Titel ----------------------------------
    # Aus "Problem melden" auf der Detailseite. Der Name wird mitgespeichert -
    # wie bei ``Blocked`` -, damit die Uebersicht ohne eine TMDB-Abfrage je
    # Ticket auskommt.
    media_type: Mapped[MediaType | None] = mapped_column(enum_column(MediaType))
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    media_title: Mapped[str | None] = mapped_column(String(300))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Wer zuletzt geschrieben hat. Steht hier, damit die Uebersicht nicht fuer
    # jede Zeile den ganzen Verlauf nachladen muss, nur um "wartet auf dich"
    # anzuzeigen.
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_reply_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    messages: Mapped[list["TicketMessage"]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketMessage.created_at",
    )


class TicketMessage(Base):
    """Ein Beitrag in einem Ticket."""

    __tablename__ = "ticket_messages"
    __table_args__ = (Index("ix_ticket_messages_ticket", "ticket_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL wie bei ``Blocked.blocked_by``: der Verlauf ueberlebt ein
    # geloeschtes Konto. Ein Gespraech mit Luecken waere unlesbar.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Ein Feld statt eines Kennzeichens: es beantwortet "wurde bearbeitet?"
    # *und* "wann?". NULL heisst unveraendert.
    edited_at: Mapped[datetime | None] = mapped_column(DateTime)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")
    author: Mapped[User | None] = relationship(foreign_keys=[user_id])


class Favorite(Base):
    """Ein Titel, den ein Benutzer mit dem Herz markiert hat.

    Grundlage fuer die kuratierten Empfehlungen: was jemand mag, sagt mehr
    ueber seinen Geschmack als das, was er zufaellig angefragt hat.
    """

    __tablename__ = "favorites"
    __table_args__ = (
        # Zweimal dasselbe zu markieren ergibt keinen Sinn - und wuerde die
        # Empfehlungen doppelt gewichten.
        UniqueConstraint("user_id", "media_type", "tmdb_id", name="uq_favorite"),
        Index("ix_favorites_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Nur zur Anzeige der eigenen Favoritenliste - erspart eine TMDB-Abfrage
    # pro Eintrag, nur um den Namen zu kennen.
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    poster_url: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class FavoritePerson(Base):
    """Eine Person, die ein Benutzer mit dem Herz markiert hat.

    Bewusst eine eigene Tabelle statt einer Erweiterung von ``Favorite``:
    Personen haben keine Altersfreigabe und keinen Bibliothekszustand, und die
    ganze Logik dort (Alterspruefung, Radarr/Sonarr-Abgleich) traefe auf sie
    nicht zu. Getrennt bleibt beides klar.

    Grundlage - wie bei den Titel-Favoriten - fuer die kuratierten
    Empfehlungen: aus gemerkten Schauspielern kommen deren bekannteste Filme.
    """

    __tablename__ = "favorite_people"
    __table_args__ = (
        UniqueConstraint("user_id", "person_id", name="uq_favorite_person"),
        Index("ix_favorite_people_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Nur zur Anzeige der eigenen Liste - erspart eine TMDB-Abfrage je Eintrag.
    name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    photo_url: Mapped[str | None] = mapped_column(String(500))
    # Hauptfach laut TMDB (Acting/Directing/Writing) - fuer die Anzeige.
    department: Mapped[str] = mapped_column(String(40), default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Blocked(Base):
    """Ein Titel, den der Administrator gesperrt hat.

    Bewusst etwas ganz anderes als die Altersbeschraenkung am ``User``:

    * Die Altersbeschraenkung gilt **je Benutzer** und macht Titel
      **unsichtbar** - wer beschraenkt ist, soll gar nicht erst wissen, dass es
      sie gibt.
    * Die Sperrliste gilt **fuer alle** und laesst Titel **sichtbar**. Sie
      tragen ein Abzeichen "Gesperrt" und lassen sich nicht anfragen. Wer
      danach sucht, soll die Antwort bekommen - naemlich dass es diesen Titel
      hier nicht geben wird. Sonst fragt derselbe Mensch dreimal nach und
      wundert sich jedes Mal.

    Pflegen darf sie **nur der Administrator**, nicht der Entscheider: der
    entscheidet ueber einzelne Anfragen, trifft aber keine Grundsatzentscheidung
    fuer die ganze Bibliothek.
    """

    __tablename__ = "blocked_titles"
    __table_args__ = (
        UniqueConstraint("media_type", "tmdb_id", name="uq_blocked_title"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Wie bei den Favoriten: erspart eine TMDB-Abfrage je Eintrag, nur um die
    # Uebersicht beschriften zu koennen.
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    poster_url: Mapped[str | None] = mapped_column(String(500))

    # Warum gesperrt wurde. Beim Ablehnen einer Anfrage wandert die Begruendung
    # hierher, damit spaeter nachvollziehbar ist, was den Ausschlag gab.
    reason: Mapped[str | None] = mapped_column(String(500))

    # Wer gesperrt hat. SET NULL statt CASCADE: wird das Konto geloescht, soll
    # die Sperre bleiben - sie war eine Entscheidung ueber den Titel, nicht
    # ueber die Person.
    blocked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class TmdbCache(Base):
    """Zwischenspeicher fuer TMDB-Antworten, damit nicht jeder Klick neu abfragt."""

    __tablename__ = "tmdb_cache"

    cache_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class ArrLibraryCache(Base):
    """Abbild der Radarr-/Sonarr-Bibliothek fuer die Status-Badges."""

    __tablename__ = "arr_library_cache"
    __table_args__ = (UniqueConstraint("media_type", "external_id", name="uq_arr_library_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    external_id: Mapped[int] = mapped_column(Integer, nullable=False)  # tmdbId bzw. tvdbId
    arr_id: Mapped[int] = mapped_column(Integer, nullable=False)
    has_file: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
