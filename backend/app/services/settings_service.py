"""Lesen und Schreiben der App-Einstellungen.

Geheime Werte (API-Keys) werden verschluesselt gespeichert und nur maskiert
an die Oberflaeche gegeben - sie verlassen den Server nie im Klartext.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crypto import decrypt, encrypt, mask
from ..models import Setting

# Schluessel, die als Geheimnis behandelt werden.
SECRET_KEYS = frozenset(
    {"tmdb_api_key", "radarr_api_key", "sonarr_api_key", "smtp_password"}
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
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_username: str
    smtp_password: str
    smtp_from_address: str
    smtp_from_name: str
    public_url: str

    @property
    def mail_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from_address)

    def link(self, pfad: str) -> str:
        """Vollstaendige Adresse fuer einen Link in einer E-Mail."""
        return f"{self.public_url.rstrip('/')}/{pfad.lstrip('/')}"

    def default_profile_id(self, media_type: str) -> int | None:
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
    def use_demo_data(self) -> bool:
        """Im Modus ``auto`` wird nur dann auf Demo-Daten ausgewichen, wenn
        noch kein TMDB-Key hinterlegt ist."""
        if self.demo_mode == "on":
            return True
        if self.demo_mode == "off":
            return False
        return not self.tmdb_configured


def _raw_values(db: Session) -> dict[str, str]:
    stored = {row.key: (row.value or "") for row in db.scalars(select(Setting))}
    return {**DEFAULTS, **stored}


def load_settings(db: Session) -> AppSettings:
    raw = _raw_values(db)
    values = {key: (decrypt(value) if key in SECRET_KEYS else value) for key, value in raw.items()}

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
        smtp_host=values["smtp_host"].strip(),
        smtp_port=smtp_port,
        smtp_security=sicherheit if sicherheit in ("none", "starttls", "ssl") else "starttls",
        smtp_username=values["smtp_username"].strip(),
        smtp_password=values["smtp_password"],
        smtp_from_address=values["smtp_from_address"].strip(),
        smtp_from_name=values["smtp_from_name"].strip() or "Nexview",
        public_url=values["public_url"].strip().rstrip("/"),
    )


def public_settings(db: Session) -> dict[str, object]:
    """Darstellung fuer die Einstellungsseite - Geheimnisse nur maskiert."""
    settings = load_settings(db)
    return {
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

        text = str(value).strip()
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
