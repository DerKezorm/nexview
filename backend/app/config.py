"""Technische Konfiguration von Nexview.

Hier stehen bewusst nur Betriebs-Einstellungen (Pfade, Token-Laufzeiten).
Die API-Keys von TMDB/Radarr/Sonarr liegen verschluesselt in der Datenbank
und werden ueber die Einstellungsseite gepflegt.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXVIEW_",
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = ""
    data_dir: Path = PROJECT_ROOT / "data"
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    static_dir: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nexview.db"

    @property
    def key_file(self) -> Path:
        return self.data_dir / "secret.key"

    def resolved_secret_key(self) -> str:
        """Geheimen Schluessel liefern; beim ersten Start automatisch erzeugen.

        So muss der Nutzer beim Ausprobieren nichts konfigurieren, waehrend ein
        gesetztes ``NEXVIEW_SECRET_KEY`` (Docker/Produktion) immer Vorrang hat.
        """
        if self.secret_key:
            return self.secret_key

        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.key_file.exists():
            existing = self.key_file.read_text(encoding="utf-8").strip()
            if existing:
                return existing

        generated = secrets.token_urlsafe(48)
        self.key_file.write_text(generated, encoding="utf-8")
        return generated


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir = Path(settings.data_dir).expanduser().resolve()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
