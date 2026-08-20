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
from ..models import Setting

logger = logging.getLogger("nexview.settings")

# Schon gemeldete unlesbare Felder - eine Warnung je Feld und Prozesslauf
# genuegt; ``load_settings`` laeuft bei jeder Anfrage, und tausendfach
# dieselbe Zeile wuerde das Protokoll unbrauchbar machen.
_unlesbar_gemeldet: set[str] = set()

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
    # Einmal taeglich bei GitHub nachsehen, ob es eine neuere Version gibt.
    # Uebertragen wird dabei nichts ausser der Anfrage selbst.
    "update_check": "on",  # "on" | "off"
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
    "mediaserver_default_quota_movies": "",
    "mediaserver_default_quota_series": "",
    "mediaserver_default_quota_period": "week",
    # Altersbeschraenkung fuer neue Konten; leer heisst unbeschraenkt.
    "mediaserver_default_age": "",
    # --- Merkliste ----------------------------------------------------------
    # Ein Schalter, mehr nicht: Er entscheidet, ob Benutzer ihre Merkliste in
    # Nexview sehen und daraus anfragen koennen. Angefragt wird ueber den ganz
    # normalen Weg - deshalb braucht es hier weder Rechte noch ein Ziel.
    "watchlist_enabled": "off",
}


@dataclass(frozen=True)
class AppSettings:
    """Aufbereitete Einstellungen fuer die Verwendung im Code (Klartext)."""

    tmdb_api_key: str
    radarr_url: str
    radarr_api_key: str
    sonarr_url: str
    sonarr_api_key: str
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
    radarr_uhd_url: str
    radarr_uhd_api_key: str
    sonarr_uhd_url: str
    sonarr_uhd_api_key: str
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
    update_check: bool
    mediaserver_provider: str
    mediaserver_machine_id: str
    mediaserver_name: str
    mediaserver_url: str
    mediaserver_token: str
    mediaserver_client_identifier: str
    mediaserver_auto_import: bool
    mediaserver_default_role: str
    mediaserver_default_quota_movies: int | None
    mediaserver_default_quota_series: int | None
    mediaserver_default_quota_period: str
    mediaserver_default_age: int | None
    watchlist_enabled: bool

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

    def root_folder_mode(self, media_type: str) -> str:
        """Wer waehlt den Zielordner? ``user`` / ``fixed`` / ``approver``.

        Bewusst **nicht** je Stufe: Wer den Ordner waehlen darf, ist eine Regel
        des Hauses und keine Eigenschaft der Instanz. Die Ordner selbst sind je
        Stufe verschieden - die Zustaendigkeit ist es nicht.
        """
        return (
            self.movie_root_folder_mode
            if media_type == "movie"
            else self.series_root_folder_mode
        )

    def root_folder_choice(self, media_type: str, tier: str = "standard") -> bool:
        """Darf der Benutzer den Zielordner fuer diese Art selbst waehlen?

        ``tier`` bleibt in der Signatur, damit die Aufrufer unveraendert
        bleiben - die Zustaendigkeit haengt aber nicht an der Stufe.
        """
        return self.root_folder_mode(media_type) == "user"

    def profile_mode(self, media_type: str) -> str:
        """Wer waehlt das Qualitaetsprofil? ``user`` / ``fixed`` / ``approver``."""
        return (
            self.movie_profile_mode if media_type == "movie" else self.series_profile_mode
        )

    def profile_choice(self, media_type: str) -> bool:
        """Darf der Benutzer das Profil selbst waehlen?"""
        return self.profile_mode(media_type) == "user"

    def approver_picks_target(self, media_type: str) -> bool:
        """Entscheidet erst der Entscheider - ueber Ordner **oder** Profil?

        Sobald eines von beidem beim Entscheider liegt, muss die Anfrage warten:
        Sie waere sonst unvollstaendig bei Radarr gelandet. Die Auto-Freigabe
        ist damit fuer diesen Dienst hinfaellig - und genau das steht auch in
        der Oberflaeche.
        """
        return "approver" in (self.root_folder_mode(media_type), self.profile_mode(media_type))

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


def _ordner_modus(values: dict[str, str], dienst: str) -> str:
    """Wer waehlt den Zielordner - aus neuem Schluessel, sonst aus dem alten.

    Bestandsinstallationen kennen nur das Ja/Nein "Benutzer duerfen waehlen".
    Daraus wird ``user`` bzw. ``fixed``; ``approver`` gab es dort nicht. So
    behaelt jede vorhandene Installation genau ihr bisheriges Verhalten, ohne
    dass jemand etwas nachstellen muss.
    """
    roh = (values.get(f"{dienst}_root_folder_mode") or "").strip().lower()
    if roh in ("user", "fixed", "approver"):
        return roh

    alt = _flag(
        values.get(f"{dienst}_root_folder_choice", ""),
        standard=_flag(values.get("root_folder_choice", "on"), standard=True),
    )
    return "user" if alt else "fixed"


def _profil_modus(values: dict[str, str], dienst: str) -> str:
    """Wer waehlt das Qualitaetsprofil?

    Ohne gesetzten Wert gilt "user": So war es immer, und ein Update darf
    niemandem stillschweigend die Auswahl entziehen.
    """
    roh = (values.get(f"{dienst}_profile_mode") or "").strip().lower()
    return roh if roh in ("user", "fixed", "approver") else "user"


def _zahl(wert: str | None) -> int | None:
    """Ganze Zahl aus einer Einstellung lesen; leer heisst "nicht gesetzt".

    Bei Kontingenten bedeutet das "unbegrenzt", beim Alter "unbeschraenkt" -
    in beiden Faellen ist das Fehlen die Aussage, nicht eine Null.
    """
    text = (wert or "").strip()
    return int(text) if text.isdigit() else None


def _raw_values(db: Session) -> dict[str, str]:
    stored = {row.key: (row.value or "") for row in db.scalars(select(Setting))}
    return {**DEFAULTS, **stored}


def load_settings(db: Session) -> AppSettings:
    raw = _raw_values(db)
    values = {key: (decrypt(value) if key in SECRET_KEYS else value) for key, value in raw.items()}

    # Benennen, *welches* Geheimnis unlesbar ist - die allgemeine Warnung aus
    # crypto.py sagt nur "irgendeines". Fuer die Fehlersuche zaehlt der Name:
    # "mediaserver_token" erklaert eine verschwundene Plex-Verbindung,
    # "tmdb_api_key" den ploetzlichen Demo-Modus.
    for name in SECRET_KEYS:
        if raw.get(name, "").startswith("enc:") and not values.get(name):
            if name not in _unlesbar_gemeldet:
                _unlesbar_gemeldet.add(name)
                logger.warning(
                    "Gespeichertes Geheimnis %r ist mit dem aktuellen "
                    "Geheimschlüssel nicht lesbar - der Wert wird wie leer "
                    "behandelt. Wurde NEXVIEW_SECRET_KEY geändert oder "
                    "data/secret.key beim Container-Neubau verloren?",
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

    return AppSettings(
        tmdb_api_key=values["tmdb_api_key"],
        radarr_url=values["radarr_url"].rstrip("/"),
        radarr_api_key=values["radarr_api_key"],
        sonarr_url=values["sonarr_url"].rstrip("/"),
        sonarr_api_key=values["sonarr_api_key"],
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
        radarr_uhd_url=values["radarr_uhd_url"].strip().rstrip("/"),
        radarr_uhd_api_key=values["radarr_uhd_api_key"],
        sonarr_uhd_url=values["sonarr_uhd_url"].strip().rstrip("/"),
        sonarr_uhd_api_key=values["sonarr_uhd_api_key"],
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
        update_check=_flag(values["update_check"], standard=True),
        mediaserver_provider=(
            values["mediaserver_provider"].strip()
            if values["mediaserver_provider"].strip() in ("plex",)
            else ""
        ),
        mediaserver_machine_id=values["mediaserver_machine_id"].strip(),
        mediaserver_name=values["mediaserver_name"].strip(),
        mediaserver_url=values["mediaserver_url"].strip().rstrip("/"),
        mediaserver_token=values["mediaserver_token"],
        mediaserver_client_identifier=values["mediaserver_client_identifier"].strip(),
        mediaserver_auto_import=_flag(values["mediaserver_auto_import"], standard=True),
        # "admin" wird hier abgefangen und nicht erst beim Speichern: eine von
        # Hand verbogene Datenbank soll keine Administratoren erzeugen koennen.
        mediaserver_default_role=(
            values["mediaserver_default_role"]
            if values["mediaserver_default_role"] in ("user", "approver")
            else "user"
        ),
        mediaserver_default_quota_movies=_zahl(values.get("mediaserver_default_quota_movies")),
        mediaserver_default_quota_series=_zahl(values.get("mediaserver_default_quota_series")),
        mediaserver_default_quota_period=(
            values["mediaserver_default_quota_period"]
            if values["mediaserver_default_quota_period"] in ("day", "week", "month")
            else "week"
        ),
        mediaserver_default_age=_zahl(values.get("mediaserver_default_age")),
        watchlist_enabled=_flag(values["watchlist_enabled"], standard=False),
    )


def for_user(settings: AppSettings, user: "User") -> AppSettings:
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
        "sonarr_url": settings.sonarr_url,
        "sonarr_api_key": mask(settings.sonarr_api_key),
        "sonarr_api_key_set": bool(settings.sonarr_api_key),
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
        "default_movie_root": settings.default_movie_root,
        "default_series_root": settings.default_series_root,
        "radarr_uhd_url": settings.radarr_uhd_url,
        "radarr_uhd_api_key": mask(settings.radarr_uhd_api_key),
        "radarr_uhd_api_key_set": bool(settings.radarr_uhd_api_key),
        "sonarr_uhd_url": settings.sonarr_uhd_url,
        "sonarr_uhd_api_key": mask(settings.sonarr_uhd_api_key),
        "sonarr_uhd_api_key_set": bool(settings.sonarr_uhd_api_key),
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
        "update_check": settings.update_check,
        "mediaserver_provider": settings.mediaserver_provider,
        "mediaserver_machine_id": settings.mediaserver_machine_id,
        "mediaserver_name": settings.mediaserver_name,
        "mediaserver_url": settings.mediaserver_url,
        # Das Token selbst verlaesst den Server nie - nur die Auskunft, ob eines
        # hinterlegt ist. Es wird ohnehin nicht von Hand eingetragen, sondern
        # beim Verbinden vom Anbieter geholt.
        "mediaserver_token_set": bool(settings.mediaserver_token),
        "mediaserver_configured": settings.mediaserver_configured,
        "mediaserver_auto_import": settings.mediaserver_auto_import,
        "mediaserver_default_role": settings.mediaserver_default_role,
        "mediaserver_default_quota_movies": settings.mediaserver_default_quota_movies,
        "mediaserver_default_quota_series": settings.mediaserver_default_quota_series,
        "mediaserver_default_quota_period": settings.mediaserver_default_quota_period,
        "mediaserver_default_age": settings.mediaserver_default_age,
        "watchlist_enabled": settings.watchlist_enabled,
    }


def save_settings(db: Session, changes: dict[str, object]) -> None:
    """Geaenderte Werte speichern.

    Ein leerer String bei einem Geheimnis bedeutet "unveraendert lassen" -
    sonst wuerde das Zurueckschicken des maskierten Werts den Key loeschen.
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

    db.commit()


def clear_secret(db: Session, key: str) -> None:
    """Ein hinterlegtes Geheimnis entfernen."""
    row = db.get(Setting, key)
    if row is not None:
        row.value = ""
        db.commit()


def verbindungsbericht() -> None:
    """Beim Start einmal hinschreiben, woran man sonst tagelang raetselt.

    Drei Fragen, deren Antworten bisher nirgends standen:

    * Woher kommt der Geheimschluessel - Umgebungsvariable oder Datei?
      (Die Datei ausserhalb des gemounteten Volumes ist der klassische Weg,
      wie beim Container-Neubau alle Zugangsdaten unlesbar werden.)
    * Ist ein Media-Server eingetragen, und laesst sich sein Token mit dem
      aktuellen Schluessel lesen?
    * Welche weiteren Geheimnisse sind eingetragen, und sind sie lesbar?
    """
    import os

    from ..config import get_settings
    from ..db import SessionLocal

    einstellungen = get_settings()
    if einstellungen.secret_key:
        quelle = "NEXVIEW_SECRET_KEY (Umgebungsvariable)"
    else:
        quelle = f"Datei {einstellungen.key_file}"
    logger.info("Geheimschlüssel: %s", quelle)
    if not einstellungen.secret_key:
        logger.info(
            "secret.key vorhanden: %s - liegt die Datei NICHT im gemounteten "
            "Volume, gehen beim Container-Neubau alle Zugangsdaten verloren.",
            os.path.exists(einstellungen.key_file),
        )

    with SessionLocal() as db:
        raw = _raw_values(db)
        stand: list[str] = []
        for name in sorted(SECRET_KEYS):
            wert = raw.get(name, "")
            if not wert:
                stand.append(f"{name}=leer")
            elif decrypt(wert):
                stand.append(f"{name}=lesbar")
            else:
                stand.append(f"{name}=UNLESBAR")
        logger.info("Geheimnisse: %s", ", ".join(stand))

        settings = load_settings(db)
        if settings.mediaserver_provider or raw.get("mediaserver_machine_id"):
            logger.info(
                "Media-Server: provider=%r name=%r machine_id=%s url=%r "
                "token=%s -> verbunden=%s",
                settings.mediaserver_provider,
                settings.mediaserver_name,
                (settings.mediaserver_machine_id[:12] + "…")
                if settings.mediaserver_machine_id
                else "fehlt",
                settings.mediaserver_url,
                "lesbar" if settings.mediaserver_token else "FEHLT/UNLESBAR",
                settings.mediaserver_configured,
            )
        else:
            logger.info("Media-Server: nicht eingerichtet")
