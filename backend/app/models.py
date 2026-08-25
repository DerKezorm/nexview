"""Datenbank-Tabellen von Nexview."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
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
    # Ein Kinderkonto: gehoert zu genau einem erwachsenen Konto
    # (``User.parent_id``) und ist ihm untergeordnet.
    #
    # Bewusst eine eigene Rolle und nicht bloss "hat ein Elternteil": Die Frage
    # "was ist dieses Konto" muss an einer Stelle beantwortbar sein - naemlich
    # dort, wo auch die Rechte haengen. Ein Kennzeichen neben der Rolle waere
    # eine zweite Wahrheit, und die erste vergessene Abfrage liesse ein Kind an
    # eine Erwachsenenfunktion.
    #
    # Kinderkonten entstehen und vergehen **ausschliesslich** ueber
    # ``/api/children``; weder eine Einladung noch der Media-Server-Import noch
    # ``update_user`` duerfen diese Rolle vergeben - sonst gaebe es ein Kind
    # ohne Elternteil.
    child = "child"


class MediaType(str, enum.Enum):
    movie = "movie"
    tv = "tv"


class QualityTier(str, enum.Enum):
    """Welche der beiden Radarr-/Sonarr-Instanzen gemeint ist.

    Bewusst genau zwei feste Stufen statt einer Instanz-Tabelle: „derselbe Film
    in 1080p *und* in 4K" ist der Fall, den es gibt. Eine Verwaltung fuer
    beliebig viele Instanzen waere ein zweites Einstellungssystem fuer einen
    Fall, den niemand hat.

    ``standard`` ist ueberall der Vorgabewert. Wer keine zweite Instanz
    eintraegt, bekommt davon nichts zu sehen.
    """

    standard = "standard"
    uhd = "uhd"


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
    # War fertig geladen, aber die Datei ist inzwischen aus der Bibliothek
    # verschwunden - aus Radarr/Sonarr entfernt und auch im Media-Server nicht
    # mehr zu finden. "downloaded" waere ab da eine falsche Behauptung, und der
    # Titel liesse sich nie wieder anfragen. Gesetzt vom Status-Abgleich.
    deleted = "deleted"
    # Zurueckgestellt: "Ja im Prinzip, nur nicht jetzt."
    #
    # Entsteht, wenn ein Konto beim Freigeben schon ueberzogen ist und der
    # Entscheider die Anfrage weder durchwinken noch ablehnen will.
    #
    # ⚠️ **Ausdruecklich kein aktiver Zustand.** Genau darin liegt der Sinn:
    # Eine wartende Anfrage reserviert den Titel fuer alle anderen mit
    # (``find_active``), und der Grund fuers Zurueckstellen liegt **an der
    # Person**, nicht am Titel. Jemand anders darf ihn also holen - dann ist er
    # da, und die zurueckgestellte Anfrage hat sich erledigt.
    #
    # Zaehlt aus demselben Grund weder gegen das Stueck-Kontingent
    # (``quota.COUNTED_STATUSES``) noch bekommt sie einen Speicher-Posten
    # zugerechnet (``storage.ZURECHENBAR``) - es liegt ja keine Datei.
    deferred = "deferred"


class WishState(str, enum.Enum):
    """Lebenslauf eines Kinderwunsches.

    open -> released (daraus wurde eine Anfrage des Elternteils)
         -> declined (das Elternteil hat nein gesagt)
         -> obsolete (jemand anders hat den Titel geholt, der Wunsch hat sich
            erledigt - ausdruecklich **kein** "abgelehnt", sonst laese das Kind
            eine Absage, wo in Wahrheit ein Erfolg steht)
    """

    open = "open"
    released = "released"
    declined = "declined"
    obsolete = "obsolete"


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
    # Der hinterlegte persoenliche Zugang wird vom Media-Server nicht mehr
    # angenommen - geht an die betroffene Person, denn nur sie kann sich neu
    # anmelden. Ohne den Hinweis blieben Merkliste und Gesehen-Stand einfach
    # stehen, und niemand wuesste warum.
    mediaserver_reconnect = "mediaserver_reconnect"
    # --- Speicher ----------------------------------------------------------
    # Ein Administrator hat einen Titel in den Hausbestand uebernommen -
    # geht an denjenigen, dessen Kontingent dadurch frei wird. Ohne den
    # Hinweis saenke die Zahl grundlos, und niemand wuesste warum.
    storage_released = "storage_released"
    # Die Entscheidung "behalten, aber nicht mehr folgen" ist gefallen.
    storage_kept = "storage_kept"
    # Der Administrator hat einen abgegebenen Titel wirklich geloescht.
    # Bewusst getrennt von ``storage_released``: Dort bleibt die Datei liegen,
    # hier ist sie weg. Dieselbe Meldung fuer beides waere die eine
    # Verwechslung, die man bei einer Loeschung nicht haben darf.
    storage_deleted = "storage_deleted"
    # Jemand hat einen Titel abgegeben und wartet auf die Entscheidung.
    # Geht **nur an Administratoren** - Entscheider duerfen hier nicht
    # entscheiden (siehe ``routers/storage.in_den_hausbestand``), also waere
    # eine Meldung an sie nur eine Aufforderung zu etwas, das sie nicht duerfen.
    storage_release_requested = "storage_release_requested"
    # Eine bereits geladene Datei ist gewachsen - Radarr oder Sonarr haben ein
    # besseres Release nachgeschoben. Der belegte Platz steigt dadurch, **ohne
    # dass jemand etwas getan hat**. Ohne Hinweis faende der Betroffene eine
    # still gestiegene Zahl vor und suchte den Fehler bei sich.
    storage_grew = "storage_grew"
    # Die eigene Anfrage wurde zurueckgestellt: nicht abgelehnt, nur vertagt.
    # Ohne Hinweis wechselte sie stillschweigend den Zustand, und das sieht wie
    # ein Fehler aus.
    request_deferred = "request_deferred"
    # --- Kinderkonten ------------------------------------------------------
    # Ein Kind wuenscht sich einen Titel - geht an sein Elternteil.
    #
    # Bewusst **ohne** Mail-Schalter: ``MAIL_SWITCH`` kennt diesen Typ nicht,
    # also verschickt ``notify.wants_mail`` dazu nie etwas. Das ist kein
    # vergessener Baustein, sondern Absicht - Eltern und Kind sitzen in
    # derselben Wohnung, und ein Wunsch ist kein Vorgang, der eine Mail wert
    # waere. Wer das aendern will, braucht **drei** Teile: Eintrag in
    # ``MAIL_SWITCH``, Vorlage in ``mail_templates`` und einen ``case`` in
    # ``mail_outbox._nachricht`` - sonst wird der Auftrag lautlos verworfen.
    child_wish = "child_wish"
    # --- "Sag mir Bescheid" -------------------------------------------------
    # Ein vorgemerkter **Film** ist da. Bewusst getrennt von
    # ``download_complete``: Das ist die Meldung an den, der ihn angefragt hat.
    # Hier wartet jemand, der nichts angefragt hat - ihm "deine Anfrage ist
    # fertig" zu schreiben, waere schlicht falsch.
    watch_ready = "watch_ready"
    # Zu einer vorgemerkten **Serie** sind neue Folgen dazugekommen. Immer
    # gebuendelt: Laedt ein Staffelpaket durch, ist das *eine* Meldung ueber
    # acht Folgen und nicht acht Meldungen.
    watch_episodes = "watch_episodes"
    # Ein bewerteter Titel wurde in besserer Fassung nachgeladen.
    #
    # Radarr und Sonarr laden weiter, bis das Qualitaetsprofil erreicht ist.
    # Eine Bewertung galt aber der Datei, die damals dalag - danach steht ein
    # "war schlecht" an etwas, das es so nicht mehr gibt. Fuer den Betreiber
    # ist das die schlechteste Sorte Rueckmeldung: eine ueber einen Zustand,
    # den er nicht mehr nachpruefen kann.
    rating_outdated = "rating_outdated"
    # Ein zurueckgestellter Titel ist jetzt da - jemand anders hat ihn geholt.
    # Bewusst ein eigener Typ und nicht ``cancelled``: Der wuerde eine Mail
    # "Deine Anfrage wurde abgelehnt" ausloesen, und das waere das Gegenteil
    # der Wahrheit.
    request_fulfilled = "request_fulfilled"


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
        Index("ix_users_parent", "parent_id"),
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
    # Auto-Freigabe je Medienart - wie die Kontingente, die schon lange
    # getrennt sind. Noetig, seit die Zielordner-Regel je Dienst gilt: Steht
    # sie bei Filmen auf "der Entscheider waehlt", kann es dort keine
    # Auto-Freigabe geben, bei Serien aber sehr wohl.
    #
    # ``None`` heisst "nicht eigens gesetzt" - dann gilt der alte gemeinsame
    # Haken darunter. Nur so behaelt ein bestehendes Konto sein Verhalten:
    # Eine neue Spalte bekaeme sonst ``false``, und alle bisher automatisch
    # freigegebenen Benutzer muessten ploetzlich warten, ohne dass es jemand
    # angeordnet haette.
    auto_approve_movies: Mapped[bool | None] = mapped_column(Boolean)
    auto_approve_series: Mapped[bool | None] = mapped_column(Boolean)
    # Der alte gemeinsame Haken - bleibt als Rueckfallwert stehen.
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Die Grenzen am Konto - **dreiwertig**, siehe ``quota.UNBEGRENZT``:
    # NULL = es gilt der Standardwert des Hauses, -1 = ausdruecklich ohne
    # Grenze, 0 und groesser = genau diese Zahl (die 0 heisst "darf nichts").
    quota_movies_limit: Mapped[int | None] = mapped_column(Integer)
    quota_series_limit: Mapped[int | None] = mapped_column(Integer)
    # ⚠️ **Wird seit 0.20 nicht mehr gelesen.** Der Zeitraum gilt haus-weit und
    # steht in den Einstellungen (``AppSettings.quota_period``). Die Spalte
    # bleibt stehen, weil SQLite zum Loeschen die ganze Tabelle neu bauen
    # muesste - und solange sie niemanden stoert, ist der stille Rueckweg mehr
    # wert als die aufgeraeumte Spalte.
    quota_period: Mapped[QuotaPeriod] = mapped_column(
        enum_column(QuotaPeriod), default=QuotaPeriod.week, nullable=False
    )
    # Setzt der Admin das Kontingent zurueck, zaehlt ab diesem Zeitpunkt neu.
    # Die Anfragen selbst bleiben erhalten - nur der Verbrauch beginnt von vorn.
    quota_reset_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Speicher-Kontingent in GB.
    #
    # ⚠️ **NULL heisst hier "Vorgabe des Hauses", nicht "unbegrenzt".** Das
    # weicht bewusst von den beiden Zeilen darueber ab, wo NULL = unbegrenzt
    # bedeutet - und genau deshalb muss die Oberflaeche es beschriften.
    # Unbegrenzt fuer einen einzelnen Nutzer ist die **0**.
    #
    # Der Grund fuer den Unterschied: Beim Speicher gibt es eine sinnvolle
    # Hausvorgabe ("jeder darf 300 GB"), bei Stueckzahlen nicht. Ohne diese
    # Bedeutung muesste der Admin die Zahl an jedem Konto einzeln eintragen -
    # und wer sie vergisst, haette einen unbegrenzten Nutzer.
    storage_limit_gb: Mapped[int | None] = mapped_column(Integer)
    # Bis zu welcher Nexview-Fassung dieser Administrator den "Alles, was
    # neu ist"-Hinweis quittiert hat. NULL = noch nie - der Balken erscheint
    # dann, und nach jedem Update wieder, bis er erneut quittiert wird.
    changelog_gesehen: Mapped[str | None] = mapped_column(String(20))

    # Erlaubte Qualitaetsprofile als Komma-Liste von Radarr-/Sonarr-Kennungen.
    # Leer bedeutet: keine Einschraenkung.
    blocked_movie_profiles: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    blocked_series_profiles: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # --- 4K ---------------------------------------------------------------
    # Alles standardmaessig aus: 4K ist immer eine bewusste Einzelentscheidung
    # des Administrators - eine 4K-Datei ist vier- bis achtmal so gross.
    #
    # Getrennt nach Medienart, weil sich beides sehr verschieden anfuehlt: eine
    # 4K-Serie frisst ein Vielfaches eines 4K-Films.
    can_request_uhd_movies: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_request_uhd_series: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Eigene Auto-Freigabe je Stufe: wer 1080p ohne Rueckfrage bekommt, soll
    # nicht automatisch auch 4K ohne Rueckfrage bekommen.
    auto_approve_uhd: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Eigene Sperrlisten, weil die Profil-Kennungen der beiden Instanzen
    # kollidieren: Profil 1 in der 1080p-Instanz ist ein voellig anderes als
    # Profil 1 in der 4K-Instanz.
    blocked_movie_uhd_profiles: Mapped[str] = mapped_column(
        String(255), default="", nullable=False
    )
    blocked_series_uhd_profiles: Mapped[str] = mapped_column(
        String(255), default="", nullable=False
    )

    avatar_path: Mapped[str | None] = mapped_column(String(255))

    # --- Verknuepfung mit dem Media-Server ---------------------------------
    # Wer sein Konto verbunden hat, meldet sich wahlweise damit oder weiter
    # mit Passwort an - es bleibt dasselbe Nexview-Konto.
    #
    # Bewusst **nicht** ``plex_...`` benannt: Jellyfin und Emby benutzen
    # dieselben Spalten.
    #
    # ⚠️ Hier stand bis 0.18.0: "Anbieter sind Alternativen, keine parallelen
    # Identitaeten - eine Person hat genau eine davon, deshalb reicht ein Satz
    # Spalten statt einer eigenen Tabelle." Das war die Annahme, und sie ist
    # gefallen. Wer im Parallelbetrieb Jellyfin verband, waehrend sein Konto an
    # Plex hing, ueberschrieb damit die Plex-Verknuepfung - Kontoname, Adresse
    # und persoenliches Token weg, ohne Nachfrage.
    #
    # Alle Verknuepfungen stehen deshalb in ``user_media_server_accounts``.
    # Diese Spalten fuehren die **zuletzt** verknuepfte davon und bleiben, weil
    # vieles nur eine braucht: die Anzeige im Profil, die Anmeldung, der
    # Abgleich beim Wiedererkennen. Geschrieben werden sie ausschliesslich von
    # ``mediaserver_accounts.link`` - wer sie von Hand setzt, laesst Tabelle und
    # Spalten auseinanderlaufen.
    #
    # Verglichen wird immer die Kennung, nie Name oder Adresse - beide kann man
    # beim Anbieter jederzeit aendern.
    mediaserver_provider: Mapped[str | None] = mapped_column(String(20))
    mediaserver_account_id: Mapped[str | None] = mapped_column(String(64))
    mediaserver_username: Mapped[str | None] = mapped_column(String(120))
    mediaserver_email: Mapped[str | None] = mapped_column(String(255))
    mediaserver_thumb: Mapped[str | None] = mapped_column(String(500))
    mediaserver_linked_at: Mapped[datetime | None] = mapped_column(DateTime)

    # --- Merkliste ---------------------------------------------------------
    # Das persoenliche Token beim Anbieter - **verschluesselt**, wie jedes
    # andere Geheimnis auch.
    #
    # Es ist die einzige Stelle, an der Nexview dauerhaft ein fremdes
    # Anbieter-Token haelt. Bei der Anmeldung und beim Verknuepfen wird das
    # Token bewusst weggeworfen; hier geht das nicht, denn eine Merkliste
    # laesst sich ausschliesslich mit dem Token ihres Eigentuemers lesen - der
    # Zugang des Administrators sieht sie nicht. Entsteht nur, wenn jemand den
    # Haken setzt.
    #
    # Geloescht wird es beim Trennen des Media-Server-Kontos und mit dem
    # Konto selbst.
    watchlist_token: Mapped[str | None] = mapped_column(Text)
    watchlist_connected_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Wann der Anbieter das Token zuletzt abgelehnt hat (401).
    #
    # **Warum eine eigene Spalte und nicht einfach das Token loeschen:**
    # Geloescht saehe der Zustand aus wie "nie verbunden", und die Oberflaeche
    # koennte nicht zwischen "muss sich neu anmelden" und "will die Merkliste
    # gar nicht" unterscheiden. Nur wer schon einmal verbunden war, soll den
    # roten Hinweis sehen.
    #
    # Und **nicht** aus der ungelesenen Benachrichtigung abgeleitet: Der
    # Hinweis verschwaende, sobald jemand die Glocke leert - ohne dass das
    # Problem behoben waere.
    watchlist_token_invalid_at: Mapped[datetime | None] = mapped_column(DateTime)

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
    # Vorgemerkte Titel ("Sag mir Bescheid"). Film und Serie an einem
    # Schalter: Wer sich etwas vormerkt, will davon hoeren - die Unterscheidung
    # zwischen "ist da" und "neue Folgen" ist keine, die jemand getrennt
    # abbestellen moechte.
    mail_watch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Neues Konto ueber den Media-Server - nur fuer Admins von Belang.
    mail_user_imported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Der eigene Media-Server-Zugang ist abgelaufen und braucht eine neue
    # Anmeldung - nur fuer verknuepfte Konten von Belang.
    # Ein Schalter fuer das ganze Speicher-Thema: abgegeben, entschieden,
    # gewachsen. Getrennte Haken waeren drei Zeilen fuer einen Vorgang - und
    # wer das eine wissen will, will auch das andere.
    mail_storage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mail_mediaserver_reconnect: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Wuenscht sich ein Kind etwas, wartet die Entscheidung auf das Elternteil.
    # Die Glocke sieht es nur, wer die App gerade offen hat - deshalb dieser
    # Schalter. Standard aus, wie alle Mail-Schalter.
    mail_child_wish: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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

    # --- Kinderkonten -------------------------------------------------------
    # Darf dieses Konto Kinderkonten anlegen?
    #
    # Standard **aus**: Ein Kinderkonto ist ein echtes Konto auf dieser
    # Installation, und wer welche anlegen darf, entscheidet der Betreiber -
    # nicht jeder Mitbenutzer fuer sich. Administratoren duerfen es immer, wie
    # ueberall sonst auch (sie koennten sich den Haken ohnehin selbst setzen).
    can_manage_children: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Darf dieses **Kind** Trailer ansehen?
    #
    # Standard **an** - der Trailer ist das, woran ein Kind erkennt, ob es den
    # Film ueberhaupt will; ein Poster sagt ihm wenig. Abschaltbar, weil das
    # Video bei YouTube liegt: Wer eingebettete Videos grundsaetzlich nicht
    # will, dreht es hier ab, und der Knopf verschwindet vollstaendig.
    child_trailers: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Welche Rubriken dieses **Kind** zu sehen bekommt - Komma-Liste der
    # Schluessel aus ``services/children.RUBRIKEN``.
    #
    # Leer heisst "alle Rubriken". Das ist die Vorgabe fuer Konten, die vor
    # dieser Einstellung entstanden sind; ueber die Oberflaeche laesst sich
    # nicht alles abwaehlen, denn ein Kind ohne Rubrik saehe gar nichts.
    child_genres: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # Zu welchem erwachsenen Konto dieses Kind gehoert. NULL bei allen anderen.
    #
    # ⚠️ **Ohne ``ON DELETE CASCADE``, und das ist Absicht.** Die Spalte wird
    # auf bestehenden Datenbanken per ``ALTER TABLE ADD COLUMN`` nachgetragen,
    # und SQLite kann einer vorhandenen Tabelle keine Fremdschluessel-Regel
    # nachreichen. Eine Kaskade waere also auf jeder *aktualisierten*
    # Installation stillschweigend wirkungslos - und keiner der Tests koennte
    # es merken, weil die immer auf frischen Tabellen laufen. Kinder werden
    # deshalb ausdruecklich im Dienst geloescht (``services/children.py``),
    # genau wie ``tickets.loeschen`` seine Benachrichtigungen loescht.
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    def blocked_profiles(
        self, media_type: "MediaType", tier: "QualityTier" = QualityTier.standard
    ) -> list[int]:
        """Gesperrte Profil-Kennungen; leere Liste = nichts gesperrt.

        Bewusst als Sperrliste: so bedeutet jeder Haken genau eine Sperre.
        Als Erlaubnisliste haette der erste Haken alle anderen Profile auf
        einen Schlag verboten - ein ueberraschender Nebeneffekt.

        Je Stufe getrennt, weil die Kennungen der beiden Instanzen kollidieren.
        Bestandswerte gehoeren zur Standard-Stufe - genau richtig, sie wurden
        ja fuer die einzige bisher vorhandene Instanz vergeben.
        """
        if tier == QualityTier.uhd:
            raw = (
                self.blocked_movie_uhd_profiles
                if media_type == MediaType.movie
                else self.blocked_series_uhd_profiles
            )
        else:
            raw = (
                self.blocked_movie_profiles
                if media_type == MediaType.movie
                else self.blocked_series_profiles
            )
        return [int(part) for part in raw.split(",") if part.strip().isdigit()]

    def may_request_uhd(self, media_type: "MediaType") -> bool:
        """Darf dieser Benutzer diese Medienart in 4K anfragen?

        Wer freigeben darf - Administratoren und Entscheider - immer: Sie
        koennten sich das Haekchen ohnehin selbst setzen bzw. jede Anfrage
        selbst freigeben. Es erst zu verlangen waere ein Umweg, der nichts
        schuetzt. Dasselbe Muster wie bei der Auto-Freigabe.
        """
        if self.can_approve:
            return True
        return (
            self.can_request_uhd_movies
            if media_type == MediaType.movie
            else self.can_request_uhd_series
        )

    def auto_approve_for(
        self, media_type: "MediaType", tier: "QualityTier" = QualityTier.standard
    ) -> bool:
        """Gilt eine Anfrage sofort als freigegeben?

        Wer selbst freigeben darf, gibt sich nicht erst selbst frei - sonst
        waere die Trennung eine Zwischenstufe ohne Entscheider.

        Fuer 4K gibt es bewusst **einen** Haken statt zweier: vier Kaestchen
        (Filme/Serien x Standard/4K) waeren mehr Verwaltung als Nutzen, und 4K
        ist ohnehin die Ausnahme.
        """
        if self.can_approve:
            return True
        if tier == QualityTier.uhd:
            return self.auto_approve_uhd
        eigen = (
            self.auto_approve_movies
            if media_type == MediaType.movie
            else self.auto_approve_series
        )
        return self.auto_approve if eigen is None else eigen

    requests: Mapped[list["MediaRequest"]] = relationship(
        back_populates="user",
        foreign_keys="MediaRequest.user_id",
        cascade="all, delete-orphan",
    )
    mediaserver_accounts: Mapped[list["UserMediaServerAccount"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        # Wird fast immer mitgebraucht, sobald ein Benutzer geladen wird -
        # einzeln nachzuladen ergaebe eine Abfrage je Benutzer in jeder Liste.
        lazy="selectin",
    )

    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == Role.admin

    @property
    def is_child(self) -> bool:
        """Ein Kinderkonto - untergeordnet, ohne eigene Mailadresse."""
        return self.role == Role.child

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
    def effective_auto_approve_movies(self) -> bool:
        return self.auto_approve_for(MediaType.movie)

    @property
    def effective_auto_approve_series(self) -> bool:
        return self.auto_approve_for(MediaType.tv)

    @property
    def effective_auto_approve_uhd(self) -> bool:
        """Dasselbe fuer 4K-Anfragen.

        ``effective_auto_approve`` bleibt bewusst unveraendert und meint weiter
        die Standard-Stufe: es steckt in der Benutzerliste und im Kontingent,
        eine Bedeutungsaenderung dort waere eine stille Verhaltensaenderung.
        """
        return self.auto_approve_for(MediaType.movie, QualityTier.uhd)

    @property
    def avatar_url(self) -> str | None:
        """Adresse des Profilbilds fuer die Oberflaeche."""
        return f"/api/users/avatar/{self.avatar_path}" if self.avatar_path else None

    @property
    def mediaserver_linked(self) -> bool:
        return bool(self.mediaserver_provider and self.mediaserver_account_id)

    @property
    def watchlist_connected(self) -> bool:
        """Liegt ueberhaupt ein persoenliches Token vor?

        Nach aussen gibt es nur diese Ja/Nein-Auskunft - das Token selbst
        verlaesst den Server nie.

        ⚠️ Ueber **alle** Verknuepfungen, nicht ueber die Spalte. Die fuehrt nur
        die zuletzt hinzugekommene; wer Plex und Jellyfin hat, haette damit
        gemeldet bekommen, was zufaellig zuletzt verbunden wurde.
        """
        return any(zeile.token for zeile in self.mediaserver_accounts)

    @property
    def watchlist_token_invalid(self) -> bool:
        """Hat ein Anbieter das persoenliche Token abgelehnt?

        Nur wahr, wenn es ueberhaupt eines gibt: Wer nie verbunden war, soll
        keinen Hinweis auf ein abgelaufenes Token bekommen.

        ⚠️ **Hier hing der rote Balken fest.** Vorher stand die Antwort in der
        Spalte ``watchlist_token_invalid_at``. Der stuendliche Abgleich setzte
        sie, wenn *irgendein* Anbieter das Token ablehnte - aufgeraeumt wurde
        beim erneuten Anmelden aber nur die Zeile des betroffenen Anbieters.
        Die Spalte blieb stehen, und mit ihr der Balken: "Dein Zugang ist
        abgelaufen", obwohl beide Verknuepfungen in Ordnung waren. Genau so
        gemeldet.
        """
        return any(
            zeile.token and zeile.token_invalid_at is not None
            for zeile in self.mediaserver_accounts
        )

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
    # ⚠️ Wird seit 0.20 nicht mehr gefuellt und nicht mehr gelesen - der
    # Zeitraum gilt haus-weit. Bleibt stehen wie ``User.quota_period``.
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


class MediaServerConnection(Base):
    """Ein verbundener Medienserver.

    Bis hierher lag genau **eine** Verbindung in flachen Einstellungswerten -
    ``mediaserver_url``, ``mediaserver_token`` und drei weitere. Das ging,
    solange es nur einen geben konnte. Fuer den Parallelbetrieb wird daraus
    eine Zeile je Server.

    ⚠️ **Die Client-Kennung gehoert bewusst nicht hierher.** Sie wird einmal
    je *Installation* erzeugt, nicht je Server: Plex fuehrt angemeldete Geraete
    darueber, und Nexview ist ein Geraet - egal, mit wie vielen Servern es
    spricht. Sie bleibt deshalb eine Einstellung.

    ``machine_id`` ist die dauerhafte Kennung beim Anbieter. Nach ihr wird der
    Zugriff geprueft, ausdruecklich nicht nach der Adresse: Dieselbe
    Installation ist mal ueber die lokale IP und mal ueber eine Fremdadresse
    erreichbar. Sie ist zusammen mit dem Anbieter eindeutig - denselben Server
    zweimal zu verbinden ergaebe zwei Stimmen fuer dieselbe Bibliothek.
    """

    __tablename__ = "media_server_connections"
    __table_args__ = (
        UniqueConstraint("provider", "machine_id", name="uq_media_server_connection"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    machine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nur zur Anzeige - "Bizzy" liest sich besser als eine Kennung.
    name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    # Verschluesselt, wie alle Zugaenge. Steht als Text da, weil die
    # Verschluesselung in ``crypto`` sitzt und nicht im Modell.
    token: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Zu welchem *Konto* auf dem Server das Token gehoert.
    #
    # Die Anmeldung nennt sie und Nexview warf sie bisher weg, weil Jellyfin
    # und Plex jederzeit "wer bin ich" beantworten. Emby nicht: Dort gibt es
    # ``/Users/Me`` schlicht nicht (gemessen an 4.9.5.0, HTTP 500), und ohne
    # die Nummer laesst sich weder die Bibliothek lesen noch ein Konto
    # verknuepfen. Sie hier zu merken beantwortet die Frage fuer alle Anbieter
    # und spart nebenbei einen Aufruf je Abgleich.
    account_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class UserMediaServerAccount(Base):
    """Ein Medienserver-Konto eines Benutzers - eines je Anbieter.

    Der Gegenpol zu ``MediaServerConnection``: Dort steht, welche *Server*
    Nexview kennt, hier, welche *Identitaeten* ein Mensch dort hat.

    ⚠️ **Warum es diese Tabelle gibt.** Bis 0.18.0 lagen diese Angaben als
    einzelne Spalten am Benutzer (``mediaserver_provider`` und die vier
    daneben), mit der ausdruecklichen Begruendung: "Anbieter sind Alternativen,
    keine parallelen Identitaeten - eine Person hat genau eine davon." Im
    Parallelbetrieb stimmt das nicht mehr, und die Folge war kein theoretisches
    Problem: Wer Jellyfin verband, waehrend sein Konto an Plex hing, verlor die
    Plex-Verknuepfung samt persoenlichem Token - stillschweigend, mitten im
    Verbinden. Genau so passiert.

    Die Spalten am Benutzer bleiben trotzdem: Sie fuehren die **zuletzt**
    verknuepfte Identitaet und halten damit alles am Laufen, was nur eine
    braucht (Anzeige im Profil, Anmeldung). Dieselbe Aufteilung wie zwischen
    ``AppSettings`` und seinen Einzelwerten - eine Liste, und daneben die
    erste als bequemer Sonderfall.

    Das Token liegt **hier** und nicht mehr am Benutzer, denn es gehoert zur
    einzelnen Verknuepfung: Ein Plex-Token taugt nicht fuer Jellyfin, und wer
    beide verbindet, hat zwei.
    """

    __tablename__ = "user_media_server_accounts"
    __table_args__ = (
        # Eine fremde Identitaet gehoert genau einem Nexview-Konto. Ohne diese
        # Bedingung koennten sich zwei Menschen dasselbe Plex-Konto teilen -
        # und jeder saehe die Anfragen des anderen.
        UniqueConstraint("provider", "account_id", name="uq_user_ms_identitaet"),
        # Und ein Benutzer hat je Anbieter hoechstens ein Konto. Ein zweites
        # waere nicht "auch noch verbunden", sondern eine zweite Stimme
        # derselben Person beim selben Server.
        UniqueConstraint("user_id", "provider", name="uq_user_ms_anbieter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str | None] = mapped_column(String(120))
    # Jellyfin kennt zu einem Konto **keine** Adresse - das Feld bleibt dort
    # leer. Es hat Folgen: Ohne Adresse greift "Passwort vergessen" nicht.
    email: Mapped[str | None] = mapped_column(String(255))
    thumb: Mapped[str | None] = mapped_column(String(500))
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    # Das persoenliche Token beim Anbieter - verschluesselt, wie jedes andere
    # Geheimnis. Entsteht beim Verknuepfen und beim Anmelden.
    token: Mapped[str | None] = mapped_column(Text)
    token_connected_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Wann der Anbieter das Token zuletzt abgelehnt hat (401).
    token_invalid_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped["User"] = relationship(back_populates="mediaserver_accounts")


class MediaServerLibraryItem(Base):
    """Abbild der Media-Server-Bibliothek.

    Dient dem einen Zweck, Titel zu erkennen, die **nicht** ueber Radarr/Sonarr
    kamen - von Hand kopierte Dateien, oder alles aus der Zeit vor dem
    *arr-Aufbau. Ohne diese Liste zeigt Nexview sie als anfragbar an, und
    jemand laedt sie ein zweites Mal herunter.

    Warum drei Kennungen und dazu der Titel: Welche davon Plex liefert, haengt
    am verwendeten Agenten. Der neue Film-Agent gibt alle drei heraus, aeltere
    Sammlungen oft nur eine einzige - und manche gar keine. Der normalisierte
    Titel ist der letzte Ausweg, genauso wie beim Sonarr-Abgleich.
    """

    __tablename__ = "media_server_library"
    __table_args__ = (
        UniqueConstraint("provider", "guid", name="uq_media_server_library_werk"),
        Index("ix_media_server_library_tmdb", "media_type", "tmdb_id"),
        Index("ix_media_server_library_tvdb", "media_type", "tvdb_id"),
        Index("ix_media_server_library_titel", "media_type", "title_key"),
        Index("ix_media_server_library_ratingkey", "provider", "rating_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    # Dauerhafte Kennung des Titels beim Anbieter - macht den Eintrag eindeutig.
    guid: Mapped[str] = mapped_column(String(255), nullable=False)
    # Die *interne* Nummer beim Anbieter. Sieht ueberfluessig neben ``guid`` aus,
    # ist es aber nicht: Der Wiedergabe-Verlauf von Plex nennt ausschliesslich
    # diese Nummer - ohne sie liesse sich "schon gesehen" keinem Titel zuordnen.
    rating_key: Mapped[str | None] = mapped_column(String(40))
    # Hat der Eigentuemer des hinterlegten Zugangs den Titel gesehen? Faellt
    # beim Einlesen der Bibliothek kostenlos ab und ist die weit vollstaendigere
    # Quelle als der Wiedergabe-Verlauf, den Plex nur begrenzt aufbewahrt.
    owner_watched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # In welcher Stufe liegt der Titel hier?
    #
    # Ohne diese beiden Angaben laesst sich eine Kopie im Media-Server keiner
    # Instanz zuordnen: Wer einen Film nach Erreichen der Wunschqualitaet aus
    # Radarr entfernt, hat ihn weiterhin in Plex - und Nexview konnte bisher
    # nicht sagen, ob das die 1080p- oder die 4K-Fassung ist.
    #
    # ``has_standard`` ist voreingestellt **wahr**, damit Bestandszeilen und
    # Anbieter ohne Aufloesungs-Angabe sich verhalten wie bisher.
    has_standard: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    has_uhd: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Belegter Platz in Bytes, je Stufe getrennt. Null heisst "unbekannt":
    # Bei Serien haengen die Dateien an den Folgen, der Serien-Eintrag traegt
    # keine Groesse.
    #
    # Gebraucht fuer den Fall, dass ein Titel Radarr/Sonarr verlaesst und nur
    # noch hier liegt - dann ist das die einzige Stelle, die seine Groesse
    # ueberhaupt noch kennt.
    size_standard: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    size_uhd: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    tvdb_id: Mapped[int | None] = mapped_column(Integer)
    imdb_id: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # Kleingeschrieben und ohne Sonderzeichen - siehe sonarr.normalize_title.
    title_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    year: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class StorageState(str, enum.Enum):
    """Wem gehoert ein belegter Posten gerade?"""

    owned = "owned"  # einem Nutzer zugerechnet
    pending = "pending"  # abgegeben, wartet auf die Entscheidung des Admins
    house = "house"  # Hausbestand - zaehlt bei niemandem


class StorageWish(str, enum.Enum):
    """Was sich der Abgebende wuenscht - der Admin entscheidet, ob es passiert.

    Nur bei Serien-Staffeln gibt es eine Wahl: ``keep`` heisst "die Folgen
    behalten, aber keine neuen mehr laden" - der Posten bleibt dann belastet,
    er waechst nur nicht weiter. Bei Filmen ist ``delete`` der einzige Wunsch,
    den es geben kann: Ein Film waechst nicht, "behalten und nicht mehr folgen"
    waere dort dasselbe wie gar nichts.
    """

    delete = "delete"  # Datei(en) loeschen, Konto frei
    keep = "keep"  # behalten, Ueberwachung aus - bleibt belastet


class StorageEntry(Base):
    """Ein belegter Posten: ein Titel, eine Stufe, eine Staffel.

    **Warum eine eigene Tabelle und keine Spalten an der Anfrage:**

    1. Eine Serie mit zehn Staffeln braucht zehn Zeilen. Als Spalte geht das nicht.
    2. Der Hausbestand hat oft **gar keine** Anfrage - alles, was schon vor
       Nexview da war oder von Hand kopiert wurde.
    3. Abgeben wechselt den Eigentuemer. Die Anfrage ist ein *historischer*
       Beleg und darf ihren Nutzer nie aendern.
    4. Wird eine Anfrage zurueckgezogen, darf der Posten nicht mitgehen,
       solange die Datei liegt.

    **Der Kontostand** ist die Summe der Posten mit ``owned`` und ``pending``.
    Abgegebenes zaehlt weiter, bis entschieden ist - sonst waere Abgeben ein
    Freifahrtschein.
    """

    __tablename__ = "storage_entries"
    __table_args__ = (
        # Ein Posten je Datei-Gruppe, siehe ``key``.
        #
        # Bewusst als Index und nicht als UniqueConstraint: SQLite kann einer
        # bestehenden Tabelle keine Constraints nachtragen, Indizes dagegen
        # schon. Als Constraint waere die Regel auf jeder *aktualisierten*
        # Installation stillschweigend wirkungslos - und kein Test faende das,
        # weil Tests immer auf frischen Tabellen laufen.
        Index("ix_storage_schluessel", "key", unique=True),
        Index("ix_storage_nutzer", "user_id", "state"),
        Index("ix_storage_tmdb", "media_type", "tmdb_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Was diesen Posten eindeutig macht - eine Zeichenkette statt mehrerer
    # Spalten, und zwar aus einem konkreten Grund:
    #
    # Radarr kennt Filme ueber die TMDB-Nummer, Sonarr Serien **nur** ueber die
    # TVDB-Nummer. Ein zusammengesetzter Index ueber beide Nummern haette bei
    # Serien ein NULL in der TMDB-Spalte - und SQLite haelt zwei NULL fuer
    # verschieden. Die Eindeutigkeit waere damit genau dort wirkungslos, wo sie
    # gebraucht wird.
    #
    # Aufbau (siehe services/storage.schluessel):
    #   Film    "movie:standard:tmdb:603"
    #   Staffel "tv:uhd:tvdb:81189:s3"
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    # NULL = Hausbestand. Wird ein Nutzer geloescht, faellt sein Posten
    # automatisch ans Haus - die Datei bleibt ja liegen.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    # 4K und 1080p sind zwei Dateien und werden getrennt verbucht.
    tier: Mapped[QualityTier] = mapped_column(
        enum_column(QualityTier), default=QualityTier.standard, nullable=False
    )
    # Beide nur zur Anzeige und zum Verknuepfen mit einer Anfrage - eindeutig
    # macht den Posten allein ``key``. Bei Serien fehlt die TMDB-Nummer oft.
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    tvdb_id: Mapped[int | None] = mapped_column(Integer)
    # NULL = ein Film. Sonst die Staffel: Das ist die feinste Koernung, die
    # Sonarr ohne zusaetzliche Abfrage hergibt (siehe sonarr._staffel_groessen).
    season: Mapped[int | None] = mapped_column(Integer)
    # Mitgefuehrt, damit ein Posten anzeigbar bleibt, wenn der Titel aus
    # Radarr/Sonarr verschwindet und nur noch im Media-Server liegt.
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Wo es liegt. Bei Filmen samt Dateiname, bei Staffeln nur der Ordner der
    # Serie - eine Staffel ist keine Datei, sondern zwanzig.
    #
    # ⚠️ **Wird nur an Administratoren ausgeliefert.** Ein gewoehnlicher
    # Benutzer hat mit Serverpfaden nichts zu schaffen, und die Ordnerstruktur
    # ist nichts, was er wissen muss.
    path: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    # ⚠️ Fuehrt Radarr bzw. Sonarr diesen Titel noch?
    #
    # ``False`` heisst: Nur der Media-Server meldet ihn. Das entsteht bei
    # einem verbreiteten Ablauf - laden bis die Qualitaet stimmt, dann den
    # Eintrag aus Radarr werfen und die Datei behalten. Der Posten zaehlt
    # weiter (die Bytes liegen ja auf der Platte), ist aber **nicht mehr
    # loeschbar**: Nexview loescht ausschliesslich ueber Radarr/Sonarr.
    #
    # Steht hier und wird nicht beim Anzeigen ermittelt: Der Abgleich weiss es
    # ohnehin (``_aus_media_server``), und die Liste bliebe sonst auf eine
    # Netzabfrage angewiesen, nur um ein Zeichen zu setzen.
    arr_managed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    state: Mapped[StorageState] = mapped_column(
        enum_column(StorageState), default=StorageState.house, nullable=False
    )
    # Wann der Nutzer den Posten abgegeben hat. NULL, solange er ihn behaelt.
    #
    # Gebraucht fuer die Reihenfolge der Warteschlange - wer zuerst abgegeben
    # hat, wartet am laengsten - und fuer die Anzeige "wartet seit". Eine
    # Warteschlange ohne Alter laesst nicht erkennen, ob sie stockt.
    released_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Der Wunsch des Abgebenden - NULL, solange nichts abgegeben ist.
    release_wish: Mapped[StorageWish | None] = mapped_column(enum_column(StorageWish))
    # Woher der Posten kam. NULL beim Altbestand, der nie angefragt wurde.
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_requests.id", ondelete="SET NULL")
    )


class UserWatched(Base):
    """Was hat wer schon gesehen - laut Media-Server.

    Eine eigene Tabelle und **kein** weiterer Zustand am Titel: "gesehen" ist
    eine andere Achse als "vorhanden" oder "angefragt". Als Zustandswert wuerde
    es "bereits geladen" oder "gesperrt" verdecken, und es gilt ja auch nicht
    fuer alle gleich, sondern je Person.

    Bei Serien zaehlt die Serie, nicht die Folge: Fuer ein Abzeichen an der
    Kachel ist "davon schon etwas gesehen" die brauchbare Auskunft.
    """

    __tablename__ = "user_watched"
    __table_args__ = (
        UniqueConstraint("user_id", "media_type", "tmdb_id", name="uq_user_watched"),
        Index("ix_user_watched_lookup", "user_id", "media_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Wann zuletzt gesehen - fuer "zuletzt geschaut" und den fortlaufenden Abgleich.
    watched_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Welche Medienserver diesen Titel als gesehen fuehren - Kommaliste.
    #
    # ⚠️ **Bewusst eine Eigenschaft der Zeile und kein Teil ihrer Identitaet.**
    # Die Eindeutigkeit oben ist ein echter ``UniqueConstraint``, und den kann
    # SQLite nachtraeglich weder erweitern noch ersetzen - dieselbe Falle, an
    # der schon ``UserWatchedSeason`` als eigene Tabelle endete. Den Anbieter
    # in den Schluessel zu nehmen wuerde also auf jeder bestehenden
    # Installation scheitern.
    #
    # Es ist ausserdem das richtige Modell: "Gesehen" ist eine Aussage ueber
    # Mensch und Titel, nicht ueber einen Server. Laufen zwei Server, sagen
    # vielleicht beide ja, vielleicht nur einer - **die Zeile bleibt eine.**
    # Alle Leser fragen weiterhin nur, ob sie existiert.
    #
    # Leer heisst "Herkunft unbekannt" (Zeilen aus der Zeit vor dieser Spalte,
    # bis ``init_db`` sie nachtraegt).
    providers: Mapped[str] = mapped_column(String(60), default="", nullable=False)

    @property
    def provider_list(self) -> list[str]:
        """Wer sagt "gesehen" - in stabiler Reihenfolge fuer die Anzeige."""
        return sorted({teil.strip() for teil in self.providers.split(",") if teil.strip()})


class UserWatchedSeason(Base):
    """Vollstaendig gesehene **Staffeln** - je Person, laut Media-Server.

    Eine eigene Tabelle neben ``user_watched``, aus einem SQLite-Grund: Die
    dortige Eindeutigkeit ist ein echter ``UniqueConstraint``, und den kann
    SQLite nachtraeglich weder erweitern noch ersetzen. Staffelzeilen in
    derselben Tabelle wuerden auf jeder bestehenden Installation an ihm
    zerschellen.

    **Nur vollstaendig gesehene Staffeln stehen hier** - eine Zeile heisst
    "alle Folgen dieser Staffel gesehen". Halbgesehenes fehlt bewusst: Fuer
    die Frage "kann das weg?" zaehlt nur ganz oder gar nicht, und der Abgleich
    kann eine Zeile wieder entfernen, wenn neue Folgen erscheinen und die
    Staffel damit nicht mehr vollstaendig ist.
    """

    __tablename__ = "user_watched_seasons"
    __table_args__ = (
        # Als Index, nicht als Constraint - siehe die Begruendung oben.
        Index(
            "ix_user_watched_season",
            "user_id",
            "tmdb_id",
            "season",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    watched_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Wie bei ``UserWatched.providers`` - siehe die Begruendung dort.
    providers: Mapped[str] = mapped_column(String(60), default="", nullable=False)

    @property
    def provider_list(self) -> list[str]:
        return sorted({teil.strip() for teil in self.providers.split(",") if teil.strip()})


class WatchlistLookup(Base):
    """Zwischenspeicher: welche TMDB-Nummer steckt hinter einer Plex-Kennung?

    **Kein Gedaechtnis, sondern eine Abkuerzung.** Hier steht nichts darueber,
    was jemand entschieden hat - nur, was ein Titel *ist*. Deshalb gilt die
    Zeile fuer alle Benutzer gemeinsam: Dieselbe Plex-Kennung meint fuer jeden
    denselben Film.

    Ohne diesen Zwischenspeicher kostet jedes Oeffnen der Merklisten-Seite eine
    Abfrage **je Titel** - Plex nennt in der Liste selbst keine fremden
    Kennungen. Bei hundert Eintraegen ist das der Unterschied zwischen "sofort
    da" und "spuerbar warten", und zwar jedes Mal.
    """

    __tablename__ = "watchlist_lookup"
    __table_args__ = (
        Index("ix_watchlist_lookup_guid", "provider", "guid", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    guid: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    # Leer heisst "nachgeschlagen, aber der Anbieter kennt keine" - auch das
    # ist eine Auskunft und wird gemerkt, damit sie nicht staendig neu
    # eingeholt wird.
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    year: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


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
    # An welche Instanz geht diese Anfrage? Bestandsanfragen bekommen beim
    # Update "standard" - richtig, denn es gab nur eine.
    tier: Mapped[QualityTier] = mapped_column(
        enum_column(QualityTier), default=QualityTier.standard, nullable=False
    )
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

    # Kam diese Anfrage von der Merkliste statt von einem Klick? Der
    # Entscheider soll das sehen: Niemand hat sich diesen Titel im Einzelnen
    # ueberlegt, und das aendert, wie genau man hinschaut.
    from_watchlist: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Hat ein Elternteil diese Anfrage fuer eines seiner Kinder gestellt?
    #
    # Reine Auskunft fuer die Anzeige ("Markus (fuer Lena)") - an den Rechten
    # aendert sie nichts. Die Anfrage gehoert dem Elternteil, mit seinem
    # Kontingent und seinem Freigabeweg; das ist der ganze Sinn des Aufbaus.
    #
    # ``SET NULL``: Wird das Kinderkonto geloescht, bleibt die Anfrage stehen -
    # die Datei liegt ja weiter beim Elternteil.
    for_child_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Sollen kuenftige Staffeln automatisch mitkommen?
    #
    # Muss die Freigabe ueberleben: Die Uebergabe an Sonarr passiert erst dort,
    # unter Umstaenden Tage spaeter. Staende der Wunsch nur in der Maske, waere
    # er bis dahin verloren.
    #
    # ⚠️ Standard **false**. Frueher steckte "auch kuenftige" stillschweigend in
    # jeder Anfrage ueber die ganze Serie (Sonarr: ``monitor: "all"``) - ein
    # Blankoscheck ueber Speicher, den zum Zeitpunkt der Anfrage niemand
    # beziffern kann.
    monitor_future: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text)

    # Rueckmeldung des Anfragenden zur Qualitaet des Downloads (0-5 Sterne).
    # Administratoren bewerten nicht - sie antworten nur darauf.
    rating: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[str | None] = mapped_column(Text)
    rated_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Gilt die Bewertung noch der Datei, die jetzt dort liegt?
    #
    # Wird gesetzt, wenn Radarr oder Sonarr ein besseres Release nachschieben.
    # Die Bewertung bleibt bewusst **stehen** - sie zu loeschen verloere die
    # Information. Sie zaehlt nur nicht mehr in der Auswertung mit und traegt
    # in der Liste einen Hinweis.
    rating_outdated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Wie gross die Datei beim letzten Nachsehen war.
    #
    # Daran erkennt der Status-Poller eine Aufwertung: Radarr und Sonarr laden
    # weiter, bis das Qualitaetsprofil erreicht ist, und aus 5 GB werden 50.
    # Bewusst **hier** und nicht in der Speichermessung - die ist abschaltbar,
    # und eine Bewertung veraltet auch dann, wenn niemand Kontingente fuehrt.
    # Kostet keinen zusaetzlichen Aufruf: Der Poller fragt Radarr ohnehin, und
    # die Groesse steht in derselben Antwort.
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    feedback_reply: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime)
    replied_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    user: Mapped[User] = relationship(back_populates="requests", foreign_keys=[user_id])
    approver: Mapped[User | None] = relationship(foreign_keys=[approved_by])

    @property
    def approved_by_name(self) -> str | None:
        """Wer freigegeben hat - als Name, nicht als Nummer.

        Fuer den Verlauf an der eigenen Anfrage. Der Anfragende darf die
        Benutzerliste nicht abrufen, koennte eine Kennung also nicht
        aufloesen; deshalb loest der Server sie auf.

        ``None`` heisst "niemand von Hand" - bei automatischer Freigabe gibt
        es keinen Entscheider, und das ist eine Aussage, keine Luecke.
        """
        if self.approver is None:
            return None
        return self.approver.display_name or self.approver.username


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
    # ⚠️ Nur, damit die **Staffel** mitreist. Ohne sie sind fuenf
    # Staffelanfragen derselben Serie fuenf Meldungen mit identischem Text -
    # gemeldet als "ohne die Info, dass das nur eine Folge ist und welche".
    # ``selectin`` statt Nachladen je Zeile: Die Glocke holt dreissig auf
    # einmal.
    request: Mapped[MediaRequest | None] = relationship(lazy="selectin")

    @property
    def season(self) -> int | None:
        """Staffel der zugehoerigen Anfrage, sofern es eine ist."""
        return self.request.season if self.request is not None else None


class ChannelKind(str, enum.Enum):
    """Serverseitige Benachrichtigungskanaele.

    Serverseitig heisst: vom Administrator eingerichtet, mit genau einem Ziel
    fuer die ganze Installation. Sie haben keinen Empfaenger im Sinne eines
    Benutzers - deshalb haengen sie an einem *Ereignis*, nicht an einer
    ``Notification``. Die persoenlichen Wege (Glocke, E-Mail) bleiben davon
    unberuehrt.
    """

    ntfy = "ntfy"
    gotify = "gotify"
    email = "email"
    telegram = "telegram"
    discord = "discord"
    webhook = "webhook"
    apprise = "apprise"


class ChannelTarget(Base):
    """Ein eingerichtetes Ziel eines serverseitigen Kanals.

    Eine eigene Tabelle statt flacher Einstellungsschluessel, weil es je Dienst
    **mehrere** davon geben soll: ein Gotify-Postfach fuer die Entscheider,
    eines fuer den Betreiber, ein ntfy-Topic fuer die Familie.

    ``name`` ist frei gewaehlt und steht auf der Kachel in den Einstellungen -
    "Handy Markus" sagt mehr als eine Serveradresse.

    Nicht jeder Dienst nutzt jedes Feld: Gotify kennt kein Topic, ntfy kein
    Anwendungs-Token. Eine Tabelle je Dienst waere sauberer und kostete fuer
    jeden neuen Dienst eine weitere Tabelle samt Migration - bei einer Handvoll
    Feldern ueberwiegt das Gemeinsame.

    ``token`` und ``password`` liegen verschluesselt wie die uebrigen
    Geheimnisse und verlassen den Server nie im Klartext.
    """

    __tablename__ = "channel_targets"
    __table_args__ = (Index("ix_channel_targets_kanal", "channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[ChannelKind] = mapped_column(enum_column(ChannelKind), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Manche Dienste haben zwei Ebenen. Bei ntfy ist die Instanz der Server
    # (Adresse und Anmeldung) und das Topic das eigentliche Postfach - eine
    # Instanz traegt beliebig viele davon. Bei Gotify ist die Application
    # schon das Postfach; dort bleibt es bei einer Ebene und ``parent_id``
    # leer.
    #
    # Bewusst dieselbe Tabelle mit Selbstbezug statt zweier: Beide Ebenen
    # haben dieselben Felder, nur an unterschiedlichen Stellen gefuellt, und
    # der Postausgang verweist so weiterhin auf genau eine Tabelle.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("channel_targets.id", ondelete="CASCADE")
    )

    url: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    topic: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    address: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # Telegram: der Chat und - bei Gruppen mit Themen - das Thema darin.
    chat_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    thread_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    # Ja/Nein als Text, weil die Zielverwaltung alle Felder als Text
    # durchreicht. Ein eigener Wahrheitswert waere hier die Ausnahme, die
    # jede Schleife darueber gesondert behandeln muesste.
    silent: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    auth: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    username: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    password: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    token: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    language: Mapped[str] = mapped_column(String(5), default="de", nullable=False)

    # Voruebergehend stillgelegt? Ein Ziel abzuschalten muss moeglich sein,
    # ohne es wegzuwerfen - im Urlaub, waehrend eines Umbaus, zum Eingrenzen
    # eines Fehlers. Wer stattdessen loescht, tippt hinterher alles neu ein.
    #
    # Bei ntfy zieht eine stillgelegte Instanz ihre Topics mit: Ohne Adresse
    # und Anmeldung koennten sie ohnehin nichts ausrichten.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Wurde eine Testnachricht verschickt **und** der Code daraus eingetippt?
    # Erst dann darf hier etwas herausgehen. Ein HTTP 200 vom Push-Dienst
    # heisst nur "angenommen", nicht "angekommen".
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Worueber soll dieses Ziel informieren, und wie dringend?
    #
    # ``{"request_pending": "high"}`` - was nicht drinsteht, ist aus. Bewusst
    # eine Spalte statt zweier je Meldung: die Liste der Meldungen waechst,
    # die Tabelle soll es nicht.
    events: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    parent: Mapped["ChannelTarget | None"] = relationship(
        remote_side=[id], back_populates="children"
    )
    children: Mapped[list["ChannelTarget"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )


class ChannelMessage(Base):
    """Postausgang der serverseitigen Kanaele - eine Zeile je Ereignis und Ziel.

    Warum ueberhaupt eine Warteschlange? Aus demselben Grund wie beim
    Mailversand: ein Push-Dienst kann Sekunden brauchen oder gar nicht
    antworten, und daran darf kein Klick auf "Freigeben" haengen. Steht der
    Auftrag in der Datenbank, ueberlebt er auch einen Neustart mitten im
    Versand.

    Bewusst **eine Zeile je Ereignis**, nicht je Empfaenger: ``notify`` legt
    fuer eine wartende Anfrage eine Meldung pro Entscheider an. Haengte der
    Kanal daran, stuende dieselbe Nachricht bei drei Administratoren dreimal
    im selben Topic.

    Der Inhalt wird nicht gespeichert, sondern beim Versand aus ``request``
    bzw. ``ticket`` erzeugt - so wie es ``mail_outbox`` auch macht. Sonst
    haette man denselben Text zweimal in der Datenbank, einmal davon veraltet.
    """

    # Benannt wie das Modul, das ihn abarbeitet. Der frueher hier stehende
    # Name "channel_messages" stammt aus der Fassung ohne mehrere Ziele; da
    # 0.12.0 nie veroeffentlicht wurde, gibt es ihn nur auf
    # Entwicklungsrechnern - dort bleibt die alte Tabelle ungenutzt liegen und
    # kann bei Gelegenheit von Hand weg.
    __tablename__ = "channel_outbox"
    # Der Postausgang fragt immer dasselbe: was ist noch nicht raus?
    __table_args__ = (Index("ix_channel_outbox_offen", "sent_at", "attempts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[ChannelKind] = mapped_column(enum_column(ChannelKind), nullable=False)
    # An welches der eingerichteten Ziele. Verschwindet das Ziel, verschwinden
    # auch seine offenen Auftraege - sie haetten kein Gegenueber mehr.
    target_id: Mapped[int] = mapped_column(
        ForeignKey("channel_targets.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(enum_column(NotificationType), nullable=False)
    # Titel des Mediums bzw. Betreff des Tickets - fuer den Fall, dass der
    # Bezug zwischenzeitlich geloescht wurde.
    title: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_requests.id", ondelete="CASCADE")
    )
    ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE")
    )

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Der letzte Fehlschlag im Klartext. Steht in den Einstellungen neben dem
    # Test-Knopf - ohne ihn merkt niemand, dass seit Wochen nichts durchgeht.
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


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


class TitleRating(Base):
    """Wie jemand die Qualitaet eines vorhandenen Titels beurteilt.

    ⚠️ **Am Titel, nicht an der Anfrage** - und das ist der ganze Punkt.

    Bis 0.19 hing die Bewertung an ``MediaRequest``. Daraus folgte, dass nur
    der Besteller urteilen durfte: Wer denselben Film zwei Wochen spaeter sah
    und merkte, dass die Tonspur fehlt, hatte keine Moeglichkeit, es zu sagen.
    Dabei geht es hier nicht um Geschmack - dafuer gibt es das Herz -, sondern
    um die **Datei**, und die beurteilt jeder gleich gut, der sie gesehen hat.

    Bewusst **kein** Gatter ueber den Gesehen-Stand. Der sagt aus, dass jemand
    den *Titel* gesehen hat, nicht *diese Datei*: Nach einer Aufwertung durch
    Radarr bleibt der Haken stehen, obwohl die alte Fassung gemeint war. Als
    Nachweis taugt er also nicht. Stattdessen haengt die Gueltigkeit an der
    Datei selbst - siehe ``file_size_bytes``.

    **Je Staffel, nicht je Serie.** Die Dateien liegen staffelweise, die
    Qualitaet unterscheidet sich staffelweise, und die Anfragen sind es schon.
    Eine Serie als Ganzes zu bewerten hiesse, ueber zehn verschiedene Dateien
    ein Urteil zu faellen. ``season`` ist ``None`` bei Filmen.
    """

    __tablename__ = "title_ratings"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "media_type", "tmdb_id", "season", name="uq_title_rating"
        ),
        Index("ix_title_ratings_titel", "media_type", "tmdb_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[int | None] = mapped_column(Integer)

    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    # Nur zur Anzeige in der Uebersicht des Betreibers - erspart eine
    # TMDB-Abfrage je Zeile. Dasselbe Vorgehen wie bei ``Blocked``.
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)

    # Die Antwort des Administrators. Steht hier und nicht in einem Verlauf:
    # Eine Rueckmeldung ist keine Unterhaltung - wer weiterreden will, macht
    # ein Ticket auf.
    reply: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime)
    replied_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    # Wie gross die Datei war, als geurteilt wurde.
    #
    # Daran haengt die Gueltigkeit: Waechst sie spuerbar, hat Radarr etwas
    # Besseres nachgeschoben, und das Urteil galt einer Datei, die es nicht
    # mehr gibt. Die Bewertung bleibt trotzdem stehen - loeschen verloere die
    # Information, und leere Sterne ohne Erklaerung saehen aus wie ein Fehler.
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    outdated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class TitleWatch(Base):
    """„Sag mir Bescheid" - jemand wartet auf einen Titel, ohne ihn anzufragen.

    Der Fall, den es bisher nicht gab: Ein Titel ist schon angefragt, also
    laesst er sich nicht noch einmal anfragen - und wer ihn auch haben will,
    erfaehrt nie, dass er angekommen ist. Bis 0.18 endete das mit „wurde
    bereits angefragt" und danach mit Schweigen.

    **Film und Serie verhalten sich verschieden**, und das ist Absicht:

    * Ein **Film** wird einmal gemeldet und ist damit erledigt - danach gibt es
      nichts mehr zu sagen. Die Zeile faellt weg, sobald sie ihren Zweck
      erfuellt hat.
    * Eine **Serie** wird dauerhaft verfolgt, ueber alle Staffeln hinweg, und
      meldet jede neue Folge. Sie endet erst, wenn jemand sie beendet.

    Bewusst **ohne** Staffelnummer. Wer eine Serie verfolgt, verfolgt sie -
    Staffel fuer Staffel abzuhaken waere Buchhaltung. Und die Stelle, an der
    ein Staffel-Knopf haette stehen muessen, gibt die Oberflaeche gar nicht
    her: Die eingeklappte Staffelzeile ist selbst ein Knopf.
    """

    __tablename__ = "title_watches"
    __table_args__ = (
        UniqueConstraint("user_id", "media_type", "tmdb_id", name="uq_title_watch"),
        Index("ix_title_watches_titel", "media_type", "tmdb_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Nur fuer die Anzeige der eigenen Liste - erspart eine TMDB-Abfrage je
    # Zeile, nur um den Namen zu kennen. Dasselbe Vorgehen wie bei ``Favorite``.
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    poster_url: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SeasonProgress(Base):
    """Wie viele Folgen einer Staffel beim letzten Durchgang vorlagen.

    Der Merkposten, ohne den „neue Folge da" nicht zu haben ist. Der
    Status-Poller haengt an **Anfragen**: Er meldet, wenn *deine* Anfrage
    fertig wird. Ein vorgemerkter Titel hat keine Anfrage dahinter, also
    treibt ihn nichts an - es braucht einen eigenen Vergleich mit dem Vorlauf.

    Eine Zeile je Serie und Staffel, **nicht** je Benutzer: Der Stand einer
    Staffel ist eine Tatsache ueber die Bibliothek, keine ueber eine Person.
    Zehn Wartende teilen sich dieselbe Zeile.

    Gespeichert werden die **Nummern**, nicht nur ihre Anzahl - als Liste wie
    "1,2,3,7". Nur so laesst sich sagen, *welche* Folge dazugekommen ist, und
    genau darauf wartet man ("endlich Folge 9"). Eine blosse Anzahl waere
    ausserdem falsch, sobald eine Folge nachtraeglich geloescht und eine andere
    ergaenzt wird: Die Zahl bliebe gleich, die Staffel waere eine andere.

    Es kostet nichts: ``library.episode_availability`` liefert die Nummern
    ohnehin mit, und die Antwort liegt im Zwischenspeicher.
    """

    __tablename__ = "season_progress"
    __table_args__ = (
        UniqueConstraint("tmdb_id", "season_number", name="uq_season_progress"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Kommagetrennt, aufsteigend: "1,2,3,7". Leer heisst "noch keine".
    episodes: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class StreamingService(Base):
    """Ein Streaming-Abo, das dieser Benutzer nach eigener Angabe hat.

    Eine Zeile je Konto und Dienst. Bewusst eine eigene Tabelle und kein Feld
    am ``User``: die Freigabeliste soll spaeter danach fragen koennen ("hat
    der Anfragende Netflix?"), und dafuer ist eine Zeile das Richtige.

    Gespeichert wird der ``slug`` aus ``services/streaming.py``, **nicht** die
    TMDB-Kennung. Marken werden umbenannt, Tarife kommen dazu, und dieselbe
    Marke hat je nach Region eine andere Kennung - Amazon ist 9 in Deutschland
    und 119 in der Schweiz. Wer die Kennung speichert, muss bei jeder solchen
    Aenderung die Datenbank anfassen.

    Die Angabe ist eine **Selbstauskunft**. Nexview prueft sie nirgends nach
    und kann es nicht; sie dient nur dem Hinweis "das laeuft schon in deinem
    Abo". Nichts wird daran verhindert.

    Kinderkonten bekommen hier nie Zeilen: Sie haben keine eigenen Abos,
    sondern gucken ueber die ihrer Eltern. Der Hinweis erscheint deshalb beim
    Elternteil, wenn es den Wunsch entscheidet.
    """

    __tablename__ = "user_streaming_services"
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_streaming_service"),
        Index("ix_streaming_services_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
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


class ChildWish(Base):
    """Ein Titel, den sich ein Kind wuenscht - **noch keine Anfrage**.

    Bewusst eine eigene Tabelle und kein weiterer ``RequestStatus``. Ein
    zusaetzlicher Zustand muesste in ``quota.COUNTED_STATUSES``,
    ``storage.ZURECHENBAR``, ``requests_service.find_active``, ``badges_for``,
    der Freigabeliste des Administrators, im Status-Abgleich und in der
    Kontoaufloesung jeweils richtig behandelt werden - und genau diese Art
    Leck hat in diesem Projekt schon mehrfach Fehler erzeugt.

    Fachlich stimmt die Trennung ohnehin: Ein Wunsch reserviert den Titel fuer
    niemanden, kostet nichts und der Administrator sieht ihn nie. Erst wenn das
    Elternteil ihn freigibt, entsteht ueber ``requests_service.create_request``
    eine gewoehnliche Anfrage - auf **seinen** Namen.
    """

    __tablename__ = "child_wishes"
    __table_args__ = (
        Index("ix_child_wishes_child", "child_id"),
        Index("ix_child_wishes_parent", "parent_id"),
        Index("ix_child_wishes_lookup", "media_type", "tmdb_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Ohne ``ON DELETE``-Regel, wie ``User.parent_id``: nachgetragene Spalten
    # koennen in SQLite keine tragen. Geloescht wird ausdruecklich im Dienst.
    child_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    parent_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType), nullable=False)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tvdb_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # Wie bei ``MediaRequest.poster_path`` die **fertige Adresse**, nicht der
    # Pfad-Teil von TMDB.
    poster_path: Mapped[str | None] = mapped_column(String(255))
    release_date: Mapped[str | None] = mapped_column(String(10))

    state: Mapped[WishState] = mapped_column(
        enum_column(WishState), default=WishState.open, nullable=False
    )
    # Kurze Nachricht des Elternteils bei einer Absage. Ohne sie steht dort nur
    # "diesmal nicht", und das Kind fragt so lange nach, bis es jemand sagt.
    decline_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Die Bruecke zur entstandenen Anfrage: nur darueber sieht das Kind, dass
    # sein Titel unterwegs ist und wann er da ist.
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_requests.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    child: Mapped[User] = relationship(foreign_keys=[child_id])
    request: Mapped["MediaRequest | None"] = relationship(foreign_keys=[request_id])
