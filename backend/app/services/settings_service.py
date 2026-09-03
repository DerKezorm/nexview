"""Lesen und Schreiben der App-Einstellungen.

Geheime Werte (API-Keys) werden verschluesselt gespeichert und nur maskiert
an die Oberflaeche gegeben - sie verlassen den Server nie im Klartext.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crypto import decrypt, encrypt, mask
from ..models import MediaServerConnection, QuotaPeriod, Setting

logger = logging.getLogger("nexview.settings")

# Schon gemeldete unlesbare Felder - eine Warnung je Feld und Prozesslauf
# genuegt; ``load_settings`` laeuft bei jeder Anfrage, und tausendfach
# dieselbe Zeile wuerde das Protokoll unbrauchbar machen.
_unlesbar_gemeldet: set[str] = set()

#: Unter diesem Namen haengt der Merker an der Sitzung (``Session.info``).
#:
#: Er steht hier als Konstante, damit der Name an genau einer Stelle steht;
#: geleert wird ausschliesslich ueber ``merker_verwerfen``.
MERKER = "nexview_einstellungen"

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from ..models import User

# Schluessel, die als Geheimnis behandelt werden.
SECRET_KEYS = frozenset(
    {
        "tmdb_api_key",
        "radarr_api_key",
        "radarr_uhd_api_key",
        "sonarr_api_key",
        "sonarr_uhd_api_key",
        "smtp_password",
        "mediaserver_token",
    }
)

DEFAULTS: dict[str, str] = {
    "tmdb_api_key": "",
    "radarr_url": "",
    "radarr_api_key": "",
    "sonarr_url": "",
    "sonarr_api_key": "",
    # Frei waehlbare Anzeigenamen der Instanzen ("Filme", "Anime", ...).
    # Leer heisst: der Dienstname gilt ("Radarr", "Sonarr", "... 4K") - so
    # aendert sich fuer Bestandsinstallationen nichts. Der Name schlaegt
    # ueberall durch, wo eine Instanz genannt wird: Er kommt aus
    # ``arr_instanzen()``, und alle Nennungen laufen dort durch.
    "radarr_name": "",
    "sonarr_name": "",
    "default_region": "DE",
    "default_language": "de",
    "poll_interval_seconds": "120",
    # Vorausgewaehltes Qualitaetsprofil beim Hinzufuegen ("" = keines).
    "default_movie_profile_id": "",
    "default_series_profile_id": "",
    # Duerfen Benutzer den Zielordner selbst waehlen? Je Dienst getrennt -
    # Filme und Serien haben unterschiedliche Ordnerstrukturen, und wer bei
    # Serien feste Pfade will, muss das nicht auch bei Filmen wollen.
    #
    # Der alte gemeinsame Schluessel bleibt als Rueckfallwert stehen: Die
    # beiden neuen sind absichtlich **leer** vorbelegt, und ein leerer Wert
    # laesst ``_flag`` den uebergebenen Standard nehmen. So behaelt eine
    # aktualisierte Installation genau die Einstellung, die sie vorher hatte.
    # Wer waehlt den Zielordner? Eine Frage, drei Antworten, je Dienst:
    #
    #   "user"     - der Anfragende waehlt selbst
    #   "fixed"    - fester Standardordner fuer alle
    #   "approver" - erst der Entscheider waehlt, bei der Freigabe
    #
    # Vorher waren das zwei Ja/Nein-Schalter an zwei Stellen, die einander
    # widersprechen konnten: "Benutzer duerfen waehlen" und "der Entscheider
    # waehlt". Beide steuerten dasselbe Feld.
    #
    # Leer heisst "noch nicht gesetzt" - dann gilt der alte Ja/Nein-Schalter,
    # damit eine aktualisierte Installation genau ihr bisheriges Verhalten
    # behaelt.
    "movie_root_folder_mode": "",  # "" | "user" | "fixed" | "approver"
    "series_root_folder_mode": "",
    # Dieselbe Frage fuer das Qualitaetsprofil. Bisher waehlte immer der
    # Anfragende (eingeschraenkt durch die Sperrliste je Benutzer) - deshalb
    # ist "user" hier der Rueckfallwert.
    "movie_profile_mode": "",
    "series_profile_mode": "",
    # Dieselben Regeln fuer die 4K-Instanzen - leer heisst: wie die
    # Standard-Instanz. So verhaelt sich jede bestehende Installation nach
    # dem Update exakt wie vorher, bis jemand die 4K-Regel bewusst trennt.
    # (Bis hierher galten die Regeln je Dienst; dass sie je Instanz gelten,
    # war eine Entscheidung im Kachel-Umbau: "4K waehlt der Entscheider,
    # Standard laeuft frei" ist genau der Zuegel, den es vorher nicht gab.)
    "movie_uhd_root_folder_mode": "",
    "series_uhd_root_folder_mode": "",
    "movie_uhd_profile_mode": "",
    "series_uhd_profile_mode": "",
    # Die beiden alten Schluessel bleiben als Rueckfallwert stehen.
    "root_folder_choice": "on",  # "on" | "off" - nur noch Rueckfallwert
    "movie_root_folder_choice": "",
    "series_root_folder_choice": "",
    "default_movie_root": "",
    "default_series_root": "",
    # --- Zweite Instanz fuer 4K/UHD ----------------------------------------
    # Optional und vollstaendig unsichtbar, solange keine Adresse eingetragen
    # ist. Bewusst flache Schluessel mit Suffix statt einer Instanz-Tabelle: es
    # gibt genau zwei Stufen, und die zweite ist eine Kopie der ersten.
    "radarr_uhd_url": "",
    "radarr_uhd_api_key": "",
    "sonarr_uhd_url": "",
    "sonarr_uhd_api_key": "",
    "radarr_uhd_name": "",
    "sonarr_uhd_name": "",
    "default_movie_uhd_profile_id": "",
    "default_series_uhd_profile_id": "",
    "default_movie_uhd_root": "",
    "default_series_uhd_root": "",
    # Demo-Modus: zeigt Beispieldaten statt echter TMDB-Abfragen. Praktisch,
    # um die Oberflaeche ohne API-Key auszuprobieren.
    "demo_mode": "auto",  # "auto" | "on" | "off"
    # Mailversand. Ohne Server verschickt Nexview nichts.
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_security": "starttls",  # "none" | "starttls" | "ssl"
    "smtp_username": "",
    "smtp_password": "",
    "smtp_from_address": "",
    "smtp_from_name": "Nexview",
    # Adresse, unter der Nexview von aussen erreichbar ist. Steckt in jedem
    # Link, den Nexview verschickt - der Server selbst kann sie nicht kennen,
    # weil er hinter einem Reverse Proxy steht.
    "public_url": "",
    # Adresse, unter der **Radarr/Sonarr** Nexview erreichen - nur noetig,
    # wenn die oeffentliche Adresse dafuer nicht taugt (Docker-Netz, Router
    # ohne Rueckweg ueber die Aussenadresse, Anmeldeschutz oder
    # selbstsigniertes Zertifikat am Reverse Proxy). Leer heisst: die
    # oeffentliche Adresse gilt auch fuer den Rueckkanal.
    "webhook_basis_url": "",
    # Einmal taeglich bei GitHub nachsehen, ob es eine neuere Version gibt.
    # Uebertragen wird dabei nichts ausser der Anfrage selbst.
    "update_check": "on",  # "on" | "off"
    # Regelmaessige Sicherungen. Bis 0.22 entstand eine automatische Sicherung
    # **nur** bei einer Schemaaenderung - also praktisch nur beim Update.
    # Zwischen zwei Fassungen koennen Monate liegen; wer am Dienstag
    # versehentlich etwas loescht, hatte dann keinen Stand vom Montag.
    "backup_schedule": "weekly",  # "off" | "daily" | "weekly" | "monthly"
    # Wie viele automatische Staende liegen bleiben. War fest fuenf - sinnvoll,
    # solange eine Sicherung 170 MB wog. Seit die Zwischenspeicher draussen
    # bleiben, sind es ein paar MB, und zwanzig Staende kosten weniger als
    # frueher einer.
    "backup_keep": "5",
    # --- Media-Server ------------------------------------------------------
    # Bewusst anbieter-neutral benannt: heute Plex, spaeter ebenso Jellyfin
    # oder Emby. Es ist immer genau einer verbunden - ein Haushalt hat eine
    # Bibliothek. Ohne Verbindung bleibt davon in der Oberflaeche nichts
    # sichtbar; niemand muss einen Media-Server betreiben.
    "mediaserver_provider": "",  # "" | "plex"
    # Die dauerhafte Kennung des ausgewaehlten Servers. Nach ihr wird der
    # Zugriff geprueft - nicht nach der Adresse, denn dieselbe Installation ist
    # mal lokal und mal ueber eine Fremdadresse erreichbar.
    "mediaserver_machine_id": "",
    "mediaserver_name": "",
    "mediaserver_url": "",
    "mediaserver_token": "",
    # Einmal je Installation erzeugt; Plex fuehrt angemeldete Geraete darueber.
    "mediaserver_client_identifier": "",
    # Legt ein Media-Server-Konto beim ersten Anmelden selbst ein Nexview-Konto
    # an, oder darf es sich nur mit einem bestehenden verbinden?
    "mediaserver_auto_import": "on",
    # Vorgaben fuer so entstandene Konten. Freigaben bleiben absichtlich
    # noetig - wer neu dazukommt, soll nicht ungefragt herunterladen duerfen.
    "mediaserver_default_role": "user",  # "user" | "approver", niemals "admin"
    # --- Merkliste ----------------------------------------------------------
    # Ein Schalter, mehr nicht: Er entscheidet, ob Benutzer ihre Merkliste in
    # Nexview sehen und daraus anfragen koennen. Angefragt wird ueber den ganz
    # normalen Weg - deshalb braucht es hier weder Rechte noch ein Ziel.
    "watchlist_enabled": "off",
    # --- Folgen-Pakete ------------------------------------------------------
    # Duerfen Benutzer einzelne Folgen statt ganzer Staffeln anfragen? Ein
    # Schalter fuer Haeuser, die es schlicht halten wollen. Standard **an**:
    # Ein Paket kostet einen Platz wie eine Staffel und erzeugt keine
    # Mehrlast - abgeschaltet wird aus Geschmack, nicht aus Not.
    "episode_requests_enabled": "on",
    # --- Kontingente --------------------------------------------------------
    # Drei Standardwerte, eine Regel: Sie gelten fuer jeden, der nichts
    # Eigenes eingetragen hat. **Leer heisst unbegrenzt** - eine frisch
    # aktualisierte Installation begrenzt damit niemanden, und "nur messen,
    # nicht begrenzen" bleibt ausdrueckbar.
    #
    # ⚠️ Es gelten **immer alle drei zugleich**. Bis 0.19 war das ein
    # Entweder-oder ("storage_enabled"), haus-weit umschaltbar; der Schalter
    # ist ersatzlos weg. Wer nur nach Speicher begrenzen will, laesst die
    # Stueckzahlen leer - und umgekehrt.
    "quota_default_movies": "",
    "quota_default_series": "",
    "storage_default_limit_gb": "",
    # Der Zeitraum der Stueckzahl gilt fuer das **ganze Haus**. Er stand bis
    # 0.19 an jedem Konto einzeln (``User.quota_period``) - drei Konten mit
    # drei verschiedenen Zeitraeumen erklaeren aber niemandem mehr, was
    # "3 Filme" bedeutet, und niemand hat es je unterschiedlich gebraucht.
    "quota_period": "week",  # "day" | "week" | "month"
}


@dataclass(frozen=True)
class Verbindung:
    """Ein verbundener Medienserver, fertig zum Benutzen.

    Das Token steht hier im **Klartext** - entschluesselt beim Laden, wie bei
    allen anderen Zugaengen auch. In der Datenbank liegt es verschluesselt.
    """

    provider: str
    machine_id: str
    name: str
    url: str
    token: str
    # Das Konto auf dem Server, zu dem das Token gehoert. Leer bei
    # Verbindungen aus der Zeit vor 0.19 - dann fragt der Adapter wie bisher
    # beim Server nach.
    account_id: str = ""

    @property
    def nutzbar(self) -> bool:
        """Genug, um damit zu arbeiten?

        Die Adresse gehoert bewusst **nicht** dazu: Der Zugriff wird ueber die
        Server-Kennung beim Anbieter geprueft, und die Anmeldung funktioniert
        auch dann noch, wenn der Server daheim gerade aus ist.
        """
        return bool(self.provider and self.machine_id and self.token)


@dataclass(frozen=True)
class ArrInstanz:
    """Eine Radarr-/Sonarr-Instanz, wie neuer Code sie sehen soll: als Listeneintrag.

    ⚠️ Absichtlich eine Liste-von-Instanzen-Sicht, obwohl es heute genau die
    zwei festen Stufen gibt (``QualityTier``): Alles Neue haengt an der
    ``kennung`` statt an vier ausbuchstabierten Faellen. Kommt spaeter der
    Umbau auf beliebig viele Instanzen (Seerr und Ombi koennen das), wechselt
    nur die Quelle dieser Liste - ihre Verbraucher bleiben unberuehrt.
    """

    # Stabil und pfadtauglich (kein Doppelpunkt): An dieser Kennung haengt
    # gespeicherter Zustand, spaeter etwa die Anruf-Adresse des Webhooks.
    # Wer sie umbenennt, verliert diesen Zustand.
    kennung: str
    media_type: str  # "movie" | "tv" - wie ueberall sonst
    tier: str  # "standard" | "uhd"
    name: str  # Anzeigename: "Radarr", "Sonarr 4K", ...
    url: str
    api_key: str


@dataclass(frozen=True)
class AppSettings:
    """Aufbereitete Einstellungen fuer die Verwendung im Code (Klartext)."""

    tmdb_api_key: str
    radarr_url: str
    radarr_api_key: str
    sonarr_url: str
    sonarr_api_key: str
    radarr_name: str
    sonarr_name: str
    default_region: str
    default_language: str
    poll_interval_seconds: int
    demo_mode: str
    default_movie_profile_id: int | None
    default_series_profile_id: int | None
    default_movie_root: str
    default_series_root: str
    # "user" | "fixed" | "approver" - siehe DEFAULTS.
    movie_root_folder_mode: str
    series_root_folder_mode: str
    movie_profile_mode: str
    series_profile_mode: str
    # Effektive Werte der 4K-Instanzen: Die Erbschaft ("" = wie Standard) ist
    # beim Laden bereits aufgeloest - wer hier liest, rechnet nicht mehr.
    movie_uhd_root_folder_mode: str
    series_uhd_root_folder_mode: str
    movie_uhd_profile_mode: str
    series_uhd_profile_mode: str
    radarr_uhd_url: str
    radarr_uhd_api_key: str
    sonarr_uhd_url: str
    sonarr_uhd_api_key: str
    radarr_uhd_name: str
    sonarr_uhd_name: str
    default_movie_uhd_profile_id: int | None
    default_series_uhd_profile_id: int | None
    default_movie_uhd_root: str
    default_series_uhd_root: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_username: str
    smtp_password: str
    smtp_from_address: str
    smtp_from_name: str
    public_url: str
    webhook_basis_url: str
    update_check: bool
    backup_schedule: str
    backup_keep: int
    # Die Verbindungen, wie sie in ``media_server_connections`` stehen. Die vier
    # Einzelwerte darunter sind die **erste** davon - sie bleiben, damit die
    # ueber zwanzig Stellen, die "ist verbunden?" oder "welcher Anbieter?"
    # fragen, unveraendert weiterlaufen. Wer mehrere braucht, nimmt die Liste.
    mediaserver_verbindungen: tuple[Verbindung, ...]
    mediaserver_provider: str
    mediaserver_machine_id: str
    # Das Konto, zu dem das Server-Token gehoert - siehe ``Verbindung``.
    mediaserver_account_id: str
    mediaserver_name: str
    mediaserver_url: str
    mediaserver_token: str
    mediaserver_client_identifier: str
    mediaserver_auto_import: bool
    mediaserver_default_role: str
    watchlist_enabled: bool
    # Duerfen Benutzer Folgen-Pakete anfragen (einzelne Folgen einer Staffel)?
    episode_requests_enabled: bool
    # --- Kontingente: die drei Standardwerte und ihr Zeitraum ---------------
    # ``None`` heisst ueberall **unbegrenzt**. Sie greifen fuer jeden, der
    # nichts Eigenes eingetragen hat, und gelten **immer alle drei zugleich**.
    quota_default_movies: int | None
    quota_default_series: int | None
    storage_default_limit_gb: int | None
    quota_period: QuotaPeriod

    # --- Nur aus Sicht eines Benutzers gefuellt (siehe ``for_user``) --------
    # Alter des Benutzers; None heisst "nicht altersbeschraenkt".
    age_limit: int | None = None
    # Land, nach dessen Einstufung die Altersbeschraenkung urteilt.
    rating_region: str = ""
    # Verbergen, was nirgends eingestuft ist?
    hide_unrated: bool = True

    @property
    def mail_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from_address)

    def link(self, pfad: str) -> str:
        """Vollstaendige Adresse fuer einen Link in einer E-Mail."""
        return f"{self.public_url.rstrip('/')}/{pfad.lstrip('/')}"

    @property
    def webhook_basis(self) -> str:
        """Von wo aus Radarr/Sonarr Nexview anrufen - leer, wenn nirgends.

        Das eigene Feld gewinnt; sonst gilt die oeffentliche Adresse. Beide
        leer heisst: Der Rueckkanal kann nicht eingerichtet werden, und die
        Pflege sagt das ehrlich ("no_address"), statt eine Adresse zu raten.
        """
        return self.webhook_basis_url or self.public_url

    def default_root(self, media_type: str, tier: str = "standard") -> str:
        """Vom Administrator gesetzter Zielordner - leer, wenn keiner gesetzt ist."""
        if tier == "uhd":
            return (
                self.default_movie_uhd_root
                if media_type == "movie"
                else self.default_series_uhd_root
            )
        return (
            self.default_movie_root if media_type == "movie" else self.default_series_root
        )

    def root_folder_mode(self, media_type: str, tier: str = "standard") -> str:
        """Wer waehlt den Zielordner? ``user`` / ``fixed`` / ``approver``.

        Bewusst **nicht** je Stufe: Wer den Ordner waehlen darf, ist eine Regel
        des Hauses und keine Eigenschaft der Instanz. Die Ordner selbst sind je
        Stufe verschieden - die Zustaendigkeit ist es nicht.
        """
        return (
            (self.movie_uhd_root_folder_mode if tier == "uhd" else self.movie_root_folder_mode)
            if media_type == "movie"
            else (self.series_uhd_root_folder_mode if tier == "uhd" else self.series_root_folder_mode)
        )

    def root_folder_choice(self, media_type: str, tier: str = "standard") -> bool:
        """Darf der Benutzer den Zielordner **bei dieser Instanz** waehlen?

        ``tier`` war hier lange ein Platzhalter ("die Zustaendigkeit haengt
        nicht an der Stufe") - seit dem Kachel-Umbau gilt die Regel je
        Instanz, und der Platzhalter traegt.
        """
        return self.root_folder_mode(media_type, tier) == "user"

    def profile_mode(self, media_type: str, tier: str = "standard") -> str:
        """Wer waehlt das Qualitaetsprofil? ``user`` / ``fixed`` / ``approver``."""
        return (
            (self.movie_uhd_profile_mode if tier == "uhd" else self.movie_profile_mode)
            if media_type == "movie"
            else (self.series_uhd_profile_mode if tier == "uhd" else self.series_profile_mode)
        )

    def profile_choice(self, media_type: str, tier: str = "standard") -> bool:
        """Darf der Benutzer das Profil selbst waehlen - bei dieser Instanz?"""
        return self.profile_mode(media_type, tier) == "user"

    def approver_picks_target(self, media_type: str, tier: str = "standard") -> bool:
        """Entscheidet erst der Entscheider - ueber Ordner **oder** Profil?

        Seit dem Kachel-Umbau gilt die Regel **je Instanz**: 4K kann beim
        Entscheider liegen, waehrend Standard frei durchlaeuft - der Zuegel,
        den es je Dienst nicht gab. Eine wartende 4K-Anfrage ueberschreibt
        dabei auch die Sofort-Freigabe des Benutzers, wie bisher je Dienst.

        Sobald eines von beidem beim Entscheider liegt, muss die Anfrage warten:
        Sie waere sonst unvollstaendig bei Radarr gelandet. Die Auto-Freigabe
        ist damit fuer diesen Dienst hinfaellig - und genau das steht auch in
        der Oberflaeche.
        """
        return "approver" in (
            self.root_folder_mode(media_type, tier),
            self.profile_mode(media_type, tier),
        )

    def default_profile_id(self, media_type: str, tier: str = "standard") -> int | None:
        if tier == "uhd":
            return (
                self.default_movie_uhd_profile_id
                if media_type == "movie"
                else self.default_series_uhd_profile_id
            )
        return (
            self.default_movie_profile_id
            if media_type == "movie"
            else self.default_series_profile_id
        )

    @property
    def tmdb_configured(self) -> bool:
        return bool(self.tmdb_api_key)

    @property
    def radarr_configured(self) -> bool:
        return bool(self.radarr_url and self.radarr_api_key)

    @property
    def sonarr_configured(self) -> bool:
        return bool(self.sonarr_url and self.sonarr_api_key)

    @property
    def radarr_uhd_configured(self) -> bool:
        return bool(self.radarr_uhd_url and self.radarr_uhd_api_key)

    @property
    def sonarr_uhd_configured(self) -> bool:
        return bool(self.sonarr_uhd_url and self.sonarr_uhd_api_key)

    def arr_endpoint(self, media_type: str, tier: str = "standard") -> tuple[str, str]:
        """Adresse und API-Key der zustaendigen Instanz.

        **Die einzige Stelle im Code, die entscheidet, welche Instanz gilt.**
        Alles andere reicht nur ``media_type`` und ``tier`` durch - so kann
        keine Abzweigung vergessen werden.
        """
        if media_type == "movie":
            return (
                (self.radarr_uhd_url, self.radarr_uhd_api_key)
                if tier == "uhd"
                else (self.radarr_url, self.radarr_api_key)
            )
        return (
            (self.sonarr_uhd_url, self.sonarr_uhd_api_key)
            if tier == "uhd"
            else (self.sonarr_url, self.sonarr_api_key)
        )

    def arr_configured(self, media_type: str, tier: str = "standard") -> bool:
        """Ist die Instanz fuer diese Art und Stufe vollstaendig eingetragen?"""
        url, key = self.arr_endpoint(media_type, tier)
        return bool(url and key)

    def arr_instanzen(self) -> tuple[ArrInstanz, ...]:
        """Alle **eingerichteten** Instanzen, in Anzeigereihenfolge.

        Das Gegenstueck zu ``arr_endpoint``: dort der Einzelzugriff, hier der
        Ueberblick. Wer "fuer jede Instanz ..." arbeiten will, laeuft ueber
        diese Liste und buchstabiert die Stufen nicht selbst aus - so bleibt
        die Zahl der Instanzen an genau einer Stelle bekannt.
        """
        # Der Anzeigename ist frei waehlbar ("Filme", "Anime", ...); leer
        # gilt der Dienstname. Er schlaegt von hier aus ueberall durch -
        # Papierkoerbe, Webhook-Stand, Gesundheits-Meldungen.
        alle = (
            ("radarr-standard", "movie", "standard", self.radarr_name or "Radarr"),
            ("radarr-uhd", "movie", "uhd", self.radarr_uhd_name or "Radarr 4K"),
            ("sonarr-standard", "tv", "standard", self.sonarr_name or "Sonarr"),
            ("sonarr-uhd", "tv", "uhd", self.sonarr_uhd_name or "Sonarr 4K"),
        )
        ergebnis = []
        for kennung, art, stufe, name in alle:
            url, key = self.arr_endpoint(art, stufe)
            if url and key:
                ergebnis.append(
                    ArrInstanz(
                        kennung=kennung,
                        media_type=art,
                        tier=stufe,
                        name=name,
                        url=url,
                        api_key=key,
                    )
                )
        return tuple(ergebnis)

    @property
    def uhd_available(self) -> bool:
        """Gibt es ueberhaupt eine 4K-Instanz?

        Ist das False, bleibt die gesamte 4K-Funktion unsichtbar - kein Feld,
        kein Abzeichen, keine zusaetzliche Abfrage.
        """
        return self.radarr_uhd_configured or self.sonarr_uhd_configured

    @property
    def mediaserver_configured(self) -> bool:
        """Ist ein Server ausgewaehlt und ein Token hinterlegt?

        Die Adresse gehoert bewusst nicht dazu: Der Zugriff wird ueber die
        Server-Kennung beim Anbieter geprueft, und die Anmeldung funktioniert
        auch dann noch, wenn der Server daheim gerade aus ist.
        """
        return bool(
            self.mediaserver_provider
            and self.mediaserver_machine_id
            and self.mediaserver_token
        )

    @property
    def use_demo_data(self) -> bool:
        """Im Modus ``auto`` wird nur dann auf Demo-Daten ausgewichen, wenn
        noch kein TMDB-Key hinterlegt ist."""
        if self.demo_mode == "on":
            return True
        if self.demo_mode == "off":
            return False
        return not self.tmdb_configured


def _flag(wert: str, *, standard: bool) -> bool:
    """Ja/Nein-Einstellung aus dem gespeicherten Text lesen.

    Bewusst grosszuegig: aeltere Nexview-Fassungen und von Hand bearbeitete
    Datenbanken koennen hier auch "true" oder "1" stehen haben.
    """
    text = (wert or "").strip().lower()
    if text in {"on", "true", "1", "yes", "ja"}:
        return True
    if text in {"off", "false", "0", "no", "nein"}:
        return False
    return standard


def _ordner_modus(
    values: dict[str, str], dienst: str, rueckfall: str | None = None
) -> str:
    """Wer waehlt den Zielordner - aus neuem Schluessel, sonst aus dem alten.

    Bestandsinstallationen kennen nur das Ja/Nein "Benutzer duerfen waehlen".
    Daraus wird ``user`` bzw. ``fixed``; ``approver`` gab es dort nicht. So
    behaelt jede vorhandene Installation genau ihr bisheriges Verhalten, ohne
    dass jemand etwas nachstellen muss.
    """
    roh = (values.get(f"{dienst}_root_folder_mode") or "").strip().lower()
    if roh in ("user", "fixed", "approver"):
        return roh
    if rueckfall is not None:
        # Die 4K-Regel erbt von der Standard-Instanz, solange sie nie
        # ausdruecklich gesetzt wurde.
        return rueckfall

    alt = _flag(
        values.get(f"{dienst}_root_folder_choice", ""),
        standard=_flag(values.get("root_folder_choice", "on"), standard=True),
    )
    return "user" if alt else "fixed"


def _profil_modus(
    values: dict[str, str], dienst: str, rueckfall: str | None = None
) -> str:
    """Wer waehlt das Qualitaetsprofil?

    Ohne gesetzten Wert gilt "user": So war es immer, und ein Update darf
    niemandem stillschweigend die Auswahl entziehen. ``rueckfall`` traegt die
    Erbschaft der 4K-Instanzen: leer heisst dort "wie die Standard-Instanz".
    """
    roh = (values.get(f"{dienst}_profile_mode") or "").strip().lower()
    if roh in ("user", "fixed", "approver"):
        return roh
    return rueckfall if rueckfall is not None else "user"


def _zahl(wert: str | None) -> int | None:
    """Ganze Zahl aus einer Einstellung lesen; leer heisst "nicht gesetzt".

    Bei Kontingenten bedeutet das "unbegrenzt", beim Alter "unbeschraenkt" -
    in beiden Faellen ist das Fehlen die Aussage, nicht eine Null.
    """
    text = (wert or "").strip()
    return int(text) if text.isdigit() else None


def _zeitraum(wert: str | None) -> QuotaPeriod:
    """Den haus-weiten Kontingent-Zeitraum aus der Einstellung lesen.

    Faellt auf die Woche zurueck, wenn dort Unsinn steht: Ein unlesbarer Wert
    darf die Anwendung nicht anhalten, und die Woche ist der Wert, mit dem
    jede Installation startet.
    """
    text = (wert or "").strip().lower()
    try:
        return QuotaPeriod(text)
    except ValueError:
        return QuotaPeriod.week


def _raw_values(db: Session) -> dict[str, str]:
    stored = {row.key: (row.value or "") for row in db.scalars(select(Setting))}
    return {**DEFAULTS, **stored}


def _verbindungen_lesen(db: Session, values: dict[str, str]) -> tuple[Verbindung, ...]:
    """Die verbundenen Medienserver - aus der Tabelle, sonst aus den Altwerten.

    ⚠️ **Der Rueckfall ist der Grund, warum die Wanderung gefahrlos ist.**
    Bis 0.17.0 lag genau eine Verbindung in fuenf flachen Einstellungswerten.
    Steht die Tabelle noch leer - eine Installation, die gerade erst
    aktualisiert wurde, oder eine, bei der die Wanderung aus irgendeinem Grund
    nicht durchlief -, wird weiterhin von dort gelesen. Es gibt also keinen
    Augenblick, in dem eine bestehende Verbindung verschwunden waere.

    Der Rueckfall darf spaeter weg, aber erst, wenn keine Installation mehr
    denkbar ist, die den Sprung nicht gemacht hat. Bis dahin kostet er eine
    Abfrage und erspart einen Anruf.
    """
    aus_tabelle = [
        Verbindung(
            provider=zeile.provider,
            machine_id=zeile.machine_id,
            name=zeile.name,
            url=zeile.url.rstrip("/"),
            token=decrypt(zeile.token) if zeile.token else "",
            account_id=zeile.account_id or "",
        )
        for zeile in db.scalars(
            select(MediaServerConnection).order_by(MediaServerConnection.id)
        )
    ]
    if aus_tabelle:
        return tuple(aus_tabelle)

    alt = Verbindung(
        provider=values.get("mediaserver_provider", "").strip(),
        machine_id=values.get("mediaserver_machine_id", "").strip(),
        name=values.get("mediaserver_name", "").strip(),
        url=values.get("mediaserver_url", "").strip().rstrip("/"),
        token=values.get("mediaserver_token", ""),
    )
    return (alt,) if alt.provider and alt.machine_id else ()


def merker_verwerfen(session: Session) -> None:
    """Den gemerkten Stand dieser Sitzung wegwerfen.

    Der einzige Weg, den Merker zu loeschen. Gerufen wird er vom Horcher in
    ``db.py``, sobald in derselben Sitzung eine Zeile der Tabelle ``settings``
    oder ``media_server_connections`` geschrieben wird.

    ⚠️ **Die Zeile steht auf der Diagnose-Stufe, und man liest sie rueckwaerts.**
    Meldet jemand "ich habe gespeichert und bekam den alten Stand zurueck",
    ist ihr **Fehlen** die Auskunft: Dann hat der Horcher nicht ausgeloest, und
    genau das ist die einzige Art, wie der Merker schaden kann. Auf ``INFO``
    waere sie das nicht wert - dass er gerade gespeichert hat, weiss der
    Betreiber ohnehin. Und nur, wenn wirklich einer dalag: Ein Vermerk ueber
    das Wegwerfen von nichts erklaert nichts.
    """
    if session.info.pop(MERKER, None) is not None:
        logger.debug("Settings memo dropped: this session wrote settings or a media server")


def load_settings(db: Session, *, frisch: bool = False) -> AppSettings:
    """Die Einstellungen - je Sitzung einmal aus der Datenbank geholt.

    ⚠️ **Der Merker haengt an der Sitzung, nicht am Prozess.** Eine Anfrage
    hat genau eine Sitzung (``get_db``), also holt jede Anfrage die
    Einstellungen genau einmal statt bis zu achtmal. Zwei Anfragen teilen
    nichts.

    Geleert wird er nicht von Hand, sondern vom ``after_flush``-Horcher in
    ``db.py``: Wer in derselben Sitzung an ``settings`` oder
    ``media_server_connections`` schreibt, bekommt danach wieder den neuen
    Stand zu sehen. Das ist kein Feinschliff, sondern Bedingung - mehrere
    Endpunkte speichern und lesen im selben Atemzug zurueck (etwa
    ``PUT /api/settings``, das Verbinden eines Medienservers und das Vergeben
    der Geraetekennung beim Anmelden).

    ⚠️ **Was sich damit bewusst aendert:** Liegt in einer Anfrage ein ``await``
    mit einem Netzaufruf, und ein Administrator aendert waehrenddessen die
    Einstellungen, arbeitet diese Anfrage bis zum Ende mit dem Stand von ihrem
    Anfang. Das ist die bessere Haelfte des Tauschs - eine Anfrage, die
    mittendrin die Einstellungen wechselt, ist ihre eigene Art von
    Unstimmigkeit. Die einzige Stelle, an der das Alter wirklich zaehlt, liest
    ausdruecklich mit ``frisch=True`` (``mediaserver_library``).

    ⚠️ **Fuer kuenftige Hintergrundschleifen:** Der Merker ist nur deshalb
    unbedenklich, weil keine Sitzung ausserhalb einer Anfrage laenger lebt als
    ein Rundgang. ``main.py`` startet fuenf solcher Schleifen: Status-Abgleich,
    Nachrichtenausgang, Protokoll-Waechter (``logs``), Sicherungsplan und
    TRaSH-Nachschau (``trash_bezug``); dazu kommt die losgeloeste
    Umbenennungs-Aufgabe aus ``benennung.anstossen``. Alle nachgesehen: Jede
    Sitzung steht in einem ``with SessionLocal() as db:``, das **vor** der
    Wartezeit endet. ``trash_bezug`` fasst gar keine Sitzung an; ``logs`` und
    die Umbenennung oeffnen je Handgriff eine eigene kurze Sitzung und
    schliessen sie sofort wieder. Die langlebigste Sitzung der Anwendung ist
    ein Rundgang des Status-Abgleichs, bei einer grossen Bibliothek Minuten
    statt Sekunden; genau deshalb liest ``mediaserver_library`` dort mit
    ``frisch=True``. Wer eine Sitzung ueber ein ``sleep`` hinweg offen haelt,
    arbeitet ab dann mit veralteten Einstellungen.

    ``frisch=True`` geht am Merker vorbei und legt danach den neuen Stand ab.
    """
    if not frisch:
        gemerkt = db.info.get(MERKER)
        if gemerkt is not None:
            return gemerkt

    raw = _raw_values(db)
    values = {key: (decrypt(value) if key in SECRET_KEYS else value) for key, value in raw.items()}
    verbindungen = _verbindungen_lesen(db, values)
    # Eine leere Verbindung als Platzhalter: So brauchen die Einzelwerte unten
    # keine Fallunterscheidung, und "nicht verbunden" ist schlicht "alles leer".
    erste = verbindungen[0] if verbindungen else Verbindung("", "", "", "", "")

    # Benennen, *welches* Geheimnis unlesbar ist - die allgemeine Warnung aus
    # crypto.py sagt nur "irgendeines". Fuer die Fehlersuche zaehlt der Name:
    # "mediaserver_token" erklaert eine verschwundene Plex-Verbindung,
    # "tmdb_api_key" den ploetzlichen Demo-Modus.
    for name in SECRET_KEYS:
        if raw.get(name, "").startswith("enc:") and not values.get(name):  # noqa: SIM102
            # Bewusst zwei Stufen: oben steht, ob ein Geheimnis kaputt ist, unten,
            # ob wir es schon einmal gemeldet haben. Das sind zwei Fragen.
            if name not in _unlesbar_gemeldet:
                _unlesbar_gemeldet.add(name)
                logger.warning(
                    "Stored secret %r is not readable with the current secret "
                    "key - it is treated as empty. Was NEXVIEW_SECRET_KEY changed, "
                    "or was data/secret.key lost when the container was rebuilt?",
                    name,
                )

    try:
        poll_interval = max(30, int(values["poll_interval_seconds"]))
    except (TypeError, ValueError):
        poll_interval = 120

    def profil(key: str) -> int | None:
        rohwert = (values.get(key) or "").strip()
        return int(rohwert) if rohwert.isdigit() else None

    try:
        smtp_port = int(values["smtp_port"])
        if not 1 <= smtp_port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        smtp_port = 587

    sicherheit = values["smtp_security"]

    einstellungen = AppSettings(
        tmdb_api_key=values["tmdb_api_key"],
        radarr_url=values["radarr_url"].rstrip("/"),
        radarr_api_key=values["radarr_api_key"],
        sonarr_url=values["sonarr_url"].rstrip("/"),
        sonarr_api_key=values["sonarr_api_key"],
        radarr_name=values["radarr_name"].strip(),
        sonarr_name=values["sonarr_name"].strip(),
        default_region=values["default_region"].upper() or "DE",
        default_language=values["default_language"] or "de",
        poll_interval_seconds=poll_interval,
        demo_mode=values["demo_mode"] if values["demo_mode"] in {"auto", "on", "off"} else "auto",
        default_movie_profile_id=profil("default_movie_profile_id"),
        default_series_profile_id=profil("default_series_profile_id"),
        # Leer heisst "nichts Eigenes gesetzt" - dann gilt der alte gemeinsame
        # Schalter, damit ein Update nichts stillschweigend umstellt.
        default_movie_root=values["default_movie_root"].strip(),
        default_series_root=values["default_series_root"].strip(),
        movie_root_folder_mode=_ordner_modus(values, "movie"),
        series_root_folder_mode=_ordner_modus(values, "series"),
        movie_profile_mode=_profil_modus(values, "movie"),
        series_profile_mode=_profil_modus(values, "series"),
        movie_uhd_root_folder_mode=_ordner_modus(
            values, "movie_uhd", rueckfall=_ordner_modus(values, "movie")
        ),
        series_uhd_root_folder_mode=_ordner_modus(
            values, "series_uhd", rueckfall=_ordner_modus(values, "series")
        ),
        movie_uhd_profile_mode=_profil_modus(
            values, "movie_uhd", rueckfall=_profil_modus(values, "movie")
        ),
        series_uhd_profile_mode=_profil_modus(
            values, "series_uhd", rueckfall=_profil_modus(values, "series")
        ),
        radarr_uhd_url=values["radarr_uhd_url"].strip().rstrip("/"),
        radarr_uhd_api_key=values["radarr_uhd_api_key"],
        sonarr_uhd_url=values["sonarr_uhd_url"].strip().rstrip("/"),
        sonarr_uhd_api_key=values["sonarr_uhd_api_key"],
        radarr_uhd_name=values["radarr_uhd_name"].strip(),
        sonarr_uhd_name=values["sonarr_uhd_name"].strip(),
        default_movie_uhd_profile_id=_zahl(values["default_movie_uhd_profile_id"]),
        default_series_uhd_profile_id=_zahl(values["default_series_uhd_profile_id"]),
        default_movie_uhd_root=values["default_movie_uhd_root"].strip(),
        default_series_uhd_root=values["default_series_uhd_root"].strip(),
        smtp_host=values["smtp_host"].strip(),
        smtp_port=smtp_port,
        smtp_security=sicherheit if sicherheit in ("none", "starttls", "ssl") else "starttls",
        smtp_username=values["smtp_username"].strip(),
        smtp_password=values["smtp_password"],
        smtp_from_address=values["smtp_from_address"].strip(),
        smtp_from_name=values["smtp_from_name"].strip() or "Nexview",
        public_url=values["public_url"].strip().rstrip("/"),
        webhook_basis_url=values["webhook_basis_url"].strip().rstrip("/"),
        update_check=_flag(values["update_check"], standard=True),
        backup_schedule=(
            values["backup_schedule"]
            if values["backup_schedule"] in ("off", "daily", "weekly", "monthly")
            else "weekly"
        ),
        # Untergrenze zwei: Bei einem einzigen Stand wuerde die naechste
        # Sicherung den letzten Rueckweg ueberschreiben.
        backup_keep=max(2, min(50, _zahl(values["backup_keep"]) or 5)),
        mediaserver_verbindungen=verbindungen,
        # Die erste Verbindung, damit alles Bestehende weiterlaeuft. Bei genau
        # einer - dem heutigen Normalfall - ist das schlicht *die* Verbindung.
        mediaserver_provider=erste.provider,
        mediaserver_machine_id=erste.machine_id,
        mediaserver_account_id=erste.account_id,
        mediaserver_name=erste.name,
        mediaserver_url=erste.url,
        mediaserver_token=erste.token,
        mediaserver_client_identifier=values["mediaserver_client_identifier"].strip(),
        mediaserver_auto_import=_flag(values["mediaserver_auto_import"], standard=True),
        # "admin" wird hier abgefangen und nicht erst beim Speichern: eine von
        # Hand verbogene Datenbank soll keine Administratoren erzeugen koennen.
        mediaserver_default_role=(
            values["mediaserver_default_role"]
            if values["mediaserver_default_role"] in ("user", "approver")
            else "user"
        ),
        watchlist_enabled=_flag(values["watchlist_enabled"], standard=False),
        episode_requests_enabled=_flag(values["episode_requests_enabled"], standard=True),
        quota_default_movies=profil("quota_default_movies"),
        quota_default_series=profil("quota_default_series"),
        storage_default_limit_gb=profil("storage_default_limit_gb"),
        quota_period=_zeitraum(values["quota_period"]),
    )
    db.info[MERKER] = einstellungen
    return einstellungen


def for_user(settings: AppSettings, user: User) -> AppSettings:
    """Dieselben Einstellungen, aber aus Sicht eines bestimmten Benutzers.

    Zwei Dinge sind persoenlich:

    * **Textsprache.** In welcher Sprache TMDB Titel und Beschreibungen
      liefert, richtet sich nach der Oberflaechensprache. Wer die Oberflaeche
      auf Englisch stellt, will keine deutschen Inhaltsangaben - alles andere
      waere schlicht inkonsequent.
    * **Region.** Sie beeinflusst Kinostarts und Verfuegbarkeit. Wer nichts
      Eigenes eingestellt hat, bekommt die Vorgabe des Administrators.

    Dazu kommt die **Altersbeschraenkung**. Ihre Pruef-Region wird hier
    ausgerechnet, und zwar aus ``settings.default_region`` - der Vorgabe des
    Administrators -, ausdruecklich **nicht** aus ``user.discover_region``.
    Das ist der ganze Punkt: die persoenliche Region darf jeder selbst
    umstellen, und wer die Sperre daran messen wuerde, muesste nur ein Land
    waehlen, in dem der Titel nicht eingestuft ist. Reihenfolge beachten - das
    ``default_region`` unten wird im selben Aufruf ueberschrieben, deshalb muss
    die Pruef-Region vorher feststehen.

    Der Rest - API-Schluessel, Mailserver, Abfrageintervall - bleibt
    unveraendert; das sind Sache des Servers, nicht des Benutzers.
    """
    pruef_region = (user.rating_region or "").upper() or settings.default_region

    return replace(
        settings,
        default_language=user.language or settings.default_language,
        default_region=user.discover_region or settings.default_region,
        age_limit=user.age,
        rating_region=pruef_region,
        hide_unrated=user.hide_unrated,
    )


def public_settings(db: Session) -> dict[str, object]:
    """Darstellung fuer die Einstellungsseite - Geheimnisse nur maskiert."""
    settings = load_settings(db)

    # Gibt es gespeicherte Geheimnisse, die sich mit dem aktuellen Schluessel
    # nicht mehr lesen lassen? Das passiert, wenn NEXVIEW_SECRET_KEY geaendert
    # wurde oder data/secret.key beim Container-Neubau verlorenging. Ohne
    # diese Auskunft sieht der Administrator nur die Folgen - "Verbindung
    # weg", Demo-Daten - und nie die Ursache.
    roh = _raw_values(db)
    unlesbar = any(
        roh.get(name, "").startswith("enc:") and not decrypt(roh[name])
        for name in SECRET_KEYS
    )

    return {
        "secrets_unreadable": unlesbar,
        "tmdb_api_key": mask(settings.tmdb_api_key),
        "tmdb_api_key_set": bool(settings.tmdb_api_key),
        "radarr_url": settings.radarr_url,
        "radarr_api_key": mask(settings.radarr_api_key),
        "radarr_api_key_set": bool(settings.radarr_api_key),
        "radarr_name": settings.radarr_name,
        "sonarr_url": settings.sonarr_url,
        "sonarr_api_key": mask(settings.sonarr_api_key),
        "sonarr_api_key_set": bool(settings.sonarr_api_key),
        "sonarr_name": settings.sonarr_name,
        "default_region": settings.default_region,
        "default_language": settings.default_language,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "demo_mode": settings.demo_mode,
        "using_demo_data": settings.use_demo_data,
        "default_movie_profile_id": settings.default_movie_profile_id,
        "default_series_profile_id": settings.default_series_profile_id,
        "movie_root_folder_mode": settings.movie_root_folder_mode,
        "series_root_folder_mode": settings.series_root_folder_mode,
        "movie_profile_mode": settings.movie_profile_mode,
        "series_profile_mode": settings.series_profile_mode,
        "movie_uhd_root_folder_mode": settings.movie_uhd_root_folder_mode,
        "series_uhd_root_folder_mode": settings.series_uhd_root_folder_mode,
        "movie_uhd_profile_mode": settings.movie_uhd_profile_mode,
        "series_uhd_profile_mode": settings.series_uhd_profile_mode,
        "default_movie_root": settings.default_movie_root,
        "default_series_root": settings.default_series_root,
        "radarr_uhd_url": settings.radarr_uhd_url,
        "radarr_uhd_api_key": mask(settings.radarr_uhd_api_key),
        "radarr_uhd_api_key_set": bool(settings.radarr_uhd_api_key),
        "radarr_uhd_name": settings.radarr_uhd_name,
        "sonarr_uhd_url": settings.sonarr_uhd_url,
        "sonarr_uhd_api_key": mask(settings.sonarr_uhd_api_key),
        "sonarr_uhd_api_key_set": bool(settings.sonarr_uhd_api_key),
        "sonarr_uhd_name": settings.sonarr_uhd_name,
        "default_movie_uhd_profile_id": settings.default_movie_uhd_profile_id,
        "default_series_uhd_profile_id": settings.default_series_uhd_profile_id,
        "default_movie_uhd_root": settings.default_movie_uhd_root,
        "default_series_uhd_root": settings.default_series_uhd_root,
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_security": settings.smtp_security,
        "smtp_username": settings.smtp_username,
        "smtp_password": mask(settings.smtp_password),
        "smtp_password_set": bool(settings.smtp_password),
        "smtp_from_address": settings.smtp_from_address,
        "smtp_from_name": settings.smtp_from_name,
        "mail_configured": settings.mail_configured,
        "public_url": settings.public_url,
        "webhook_basis_url": settings.webhook_basis_url,
        "update_check": settings.update_check,
        "backup_schedule": settings.backup_schedule,
        "backup_keep": settings.backup_keep,
        "mediaserver_provider": settings.mediaserver_provider,
        "mediaserver_machine_id": settings.mediaserver_machine_id,
        "mediaserver_name": settings.mediaserver_name,
        "mediaserver_url": settings.mediaserver_url,
        # Das Token selbst verlaesst den Server nie - nur die Auskunft, ob eines
        # hinterlegt ist. Es wird ohnehin nicht von Hand eingetragen, sondern
        # beim Verbinden vom Anbieter geholt.
        "mediaserver_token_set": bool(settings.mediaserver_token),
        "mediaserver_configured": settings.mediaserver_configured,
        # **Alle** Verbindungen, je eine Zeile. Die Einzelwerte darueber sind
        # immer die der *ersten* - im Parallelbetrieb also nur zufaellig die
        # gesuchte. Die Oberflaeche zeigt je Anbieter eine eigene Seite und
        # braucht deshalb je Anbieter Name und Adresse.
        #
        # Ohne Token: Das verlaesst den Server nie, siehe darueber.
        "mediaserver_connections": [
            {"provider": zeile.provider, "name": zeile.name, "url": zeile.url}
            for zeile in settings.mediaserver_verbindungen
            if zeile.nutzbar
        ],
        "mediaserver_auto_import": settings.mediaserver_auto_import,
        "mediaserver_default_role": settings.mediaserver_default_role,
        "watchlist_enabled": settings.watchlist_enabled,
        "episode_requests_enabled": settings.episode_requests_enabled,
        "quota_default_movies": settings.quota_default_movies,
        "quota_default_series": settings.quota_default_series,
        "storage_default_limit_gb": settings.storage_default_limit_gb,
        "quota_period": settings.quota_period.value,
    }


def save_settings(db: Session, changes: dict[str, object], *, commit: bool = True) -> None:
    """Geaenderte Werte speichern.

    Ein leerer String bei einem Geheimnis bedeutet "unveraendert lassen" -
    sonst wuerde das Zurueckschicken des maskierten Werts den Key loeschen.

    ``commit=False`` laesst die Transaktion offen - fuer einen Aufrufer, der
    Einstellungen und andere Zeilen in **einem** Zug schreiben muss (der
    Abschluss des Seerr-Umzugs). Er ruft ``db.commit()`` selbst, oder gar
    nicht.
    """
    existing = {row.key: row for row in db.scalars(select(Setting))}

    for key, value in changes.items():
        if key not in DEFAULTS or value is None:
            continue

        # Ja/Nein-Schalter kommen aus der Oberflaeche als echter Wahrheitswert.
        # Ohne diese Umwandlung landete "False" als Text in der Datenbank - und
        # "False" ist beim Auslesen nun einmal nicht "off".
        text = ("on" if value else "off") if isinstance(value, bool) else str(value).strip()
        is_secret = key in SECRET_KEYS

        if is_secret:
            if not text or text.startswith("•"):
                continue
            text = encrypt(text)

        row = existing.get(key)
        if row is None:
            db.add(Setting(key=key, value=text, is_secret=is_secret))
        else:
            row.value = text
            row.is_secret = is_secret

    if commit:
        db.commit()


def clear_secret(db: Session, key: str) -> None:
    """Ein hinterlegtes Geheimnis entfernen."""
    row = db.get(Setting, key)
    if row is not None:
        row.value = ""
        db.commit()


def verbindungsbericht() -> None:
    """Beim Start einmal hinschreiben, woran man sonst tagelang raetselt.

    Vier Fragen, deren Antworten bisher nirgends standen:

    * Woher kommt der Geheimschluessel - Umgebungsvariable oder Datei?
      (Die Datei ausserhalb des gemounteten Volumes ist der klassische Weg,
      wie beim Container-Neubau alle Zugangsdaten unlesbar werden.)
    * Ist ein Media-Server eingetragen, und laesst sich sein Token mit dem
      aktuellen Schluessel lesen?
    * Welche weiteren Geheimnisse sind eingetragen, und sind sie lesbar?
    * Mit wie vielen bcrypt-Runden werden Passwoerter gehasht?
    """
    import os

    from ..config import BCRYPT_ROUNDS_DEFAULT, get_settings
    from ..db import SessionLocal

    einstellungen = get_settings()
    if einstellungen.secret_key:
        quelle = "NEXVIEW_SECRET_KEY (environment variable)"
    else:
        quelle = f"file {einstellungen.key_file}"
    logger.info("Secret key source: %s", quelle)
    if not einstellungen.secret_key:
        logger.info(
            "secret.key present: %s - if that file is NOT inside the mounted "
            "volume, every credential is lost when the container is rebuilt.",
            os.path.exists(einstellungen.key_file),
        )

    # ⚠️ Die Warnung aus dem Validator in ``config.py`` entsteht, bevor
    # ``logs.setup()`` gelaufen ist. Sie landet auf stderr und nie in der
    # Protokolldatei. Hier steht sie noch einmal, an der Stelle, an der ein
    # Betreiber ohnehin nach dem Zustand seiner Installation sieht.
    if einstellungen.bcrypt_rounds < BCRYPT_ROUNDS_DEFAULT:
        logger.warning(
            "Password hashing: bcrypt with only %s rounds instead of %s. "
            "NEXVIEW_BCRYPT_ROUNDS is meant for the test suite; in a real "
            "installation it weakens every password set from now on.",
            einstellungen.bcrypt_rounds,
            BCRYPT_ROUNDS_DEFAULT,
        )
    else:
        logger.info(
            "Password hashing: bcrypt with %s rounds.", einstellungen.bcrypt_rounds
        )

    with SessionLocal() as db:
        raw = _raw_values(db)

        def _lage(wert: str) -> str:
            if not wert:
                return "empty"
            return "readable" if decrypt(wert) else "UNREADABLE"

        stand: list[str] = []
        for name in sorted(SECRET_KEYS):
            # ⚠️ ``mediaserver_token`` steht seit 0.18.0 **nicht mehr** in den
            # Einstellungen, sondern je Verbindung in ihrer eigenen Zeile. Der
            # Bericht dort nachzusehen zu vergessen hiesse, bei jedem Start
            # "mediaserver_token=empty" zu melden, obwohl alles in Ordnung ist -
            # und genau dieser Bericht existiert, um eine verschwundene
            # Verbindung erklaeren zu koennen. Eine falsche Auskunft waere hier
            # schlimmer als gar keine.
            if name == "mediaserver_token":
                continue
            stand.append(f"{name}={_lage(raw.get(name, ''))}")

        for zeile in db.scalars(
            select(MediaServerConnection).order_by(MediaServerConnection.id)
        ):
            stand.append(f"mediaserver_token[{zeile.provider}]={_lage(zeile.token)}")

        logger.info("Secrets: %s", ", ".join(sorted(stand)))

        settings = load_settings(db)
        if settings.mediaserver_provider or raw.get("mediaserver_machine_id"):
            logger.info(
                "Media server: provider=%r name=%r machine_id=%s url=%r "
                "token=%s -> connected=%s",
                settings.mediaserver_provider,
                settings.mediaserver_name,
                (settings.mediaserver_machine_id[:12] + "...")
                if settings.mediaserver_machine_id
                else "missing",
                settings.mediaserver_url,
                "readable" if settings.mediaserver_token else "MISSING/UNREADABLE",
                settings.mediaserver_configured,
            )
        else:
            logger.info("Media server: not configured")
