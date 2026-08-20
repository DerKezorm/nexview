"""ntfy - Push aufs Handy ueber ein Topic, ohne Konto.

Verschickt wird im JSON-Format an die Basisadresse; das Topic steht dabei im
Rumpf. Der Weg ueber ``POST /topic`` mit HTTP-Kopfzeilen kann dasselbe, aber
Titel mit Umlauten muessten dort eigens kodiert werden - JSON nimmt sie so,
wie sie sind.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from ...models import ChannelKind
from .base import DEFAULT_LEVEL, ChannelError, Notice, check_url, get_json, post

KIND = ChannelKind.ntfy
LABEL = "ntfy"

# ntfy hat zwei Ebenen: die Instanz traegt Adresse und Anmeldung, das Topic
# ist das eigentliche Postfach. Eine Instanz kann beliebig viele Topics haben,
# und nur am Topic laesst sich beweisen, dass etwas ankommt.
PARENT_FIELDS = ("url", "auth", "username", "password", "token")
# Ohne dieses Feld ist die obere Ebene unvollstaendig.
PARENT_REQUIRED = ("url",)
CHILD_FIELDS = ("topic", "language")
FIELDS = PARENT_FIELDS + CHILD_FIELDS
SECRETS = ("password", "token")

# ntfy kennt 1 (leise) bis 5 (dringend). 5 laesst das Handy auch in der
# Nachtruhe klingeln - deshalb nur fuer die dringendste Stufe.
PRIORITIES = {"low": 2, "normal": 3, "high": 4, "urgent": 5}


@dataclass(frozen=True)
class NtfyConfig:
    url: str
    topic: str
    auth: str  # "none" | "basic" | "token"
    username: str
    password: str
    token: str
    language: str

    @property
    def configured(self) -> bool:
        return bool(self.url and self.topic)


def build(werte: dict[str, str]) -> NtfyConfig | None:
    """Aus den gespeicherten Feldern eines Ziels den Zugang bauen."""
    config = NtfyConfig(
        url=werte.get("url", "").rstrip("/"),
        topic=werte.get("topic", ""),
        auth=werte.get("auth") if werte.get("auth") in ("none", "basic", "token") else "none",
        username=werte.get("username", ""),
        password=werte.get("password", ""),
        token=werte.get("token", ""),
        language=werte.get("language") if werte.get("language") in ("de", "en") else "de",
    )
    return config if config.configured else None


def _headers(config: NtfyConfig) -> dict[str, str]:
    if config.auth == "basic" and config.username:
        roh = f"{config.username}:{config.password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(roh).decode()}
    if config.auth == "token" and config.token:
        return {"Authorization": f"Bearer {config.token}"}
    return {}


async def check(config: NtfyConfig) -> None:
    """Antwortet unter dieser Adresse ueberhaupt ein ntfy? Sonst ``ChannelError``.

    Fuer die Instanz-Ebene: Ohne Topic laesst sich nichts verschicken, ein
    Tippfehler in der Adresse soll trotzdem sofort auffallen und nicht erst
    beim ersten Topic.
    """
    check_url(config.url)
    daten = await get_json(f"{config.url}/v1/health", headers=_headers(config))
    if not isinstance(daten, dict) or not daten.get("healthy"):
        raise ChannelError("Unter dieser Adresse antwortet kein ntfy.")


async def send(config: NtfyConfig, notice: Notice) -> None:
    check_url(config.url)
    if not config.topic:
        raise ChannelError("Es ist kein Topic hinterlegt.")

    payload: dict[str, object] = {
        "topic": config.topic,
        "title": notice.title,
        "message": notice.body,
        "priority": PRIORITIES.get(notice.level, PRIORITIES[DEFAULT_LEVEL]),
        "markdown": True,
    }
    # Das Poster kommt fertig von TMDB und ist oeffentlich erreichbar - anders
    # als der Link auf Nexview, den es nur mit hinterlegter Adresse gibt.
    if notice.poster_url:
        payload["attach"] = notice.poster_url
    if notice.click_url:
        payload["click"] = notice.click_url

    await post(config.url, json=payload, headers=_headers(config))
