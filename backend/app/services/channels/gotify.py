"""Gotify - selbst betriebener Push-Dienst, ein Token je Postfach.

Ein Gotify-Token gehoert genau einer Application eines Gotify-Kontos. Es ist
damit ein Postfach, kein Adressbuch: was hier hineingeht, sieht, wer Zugriff
auf dieses Konto hat. Fuer einen Administrationskanal ist das genau richtig.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import ChannelKind
from .base import DEFAULT_LEVEL, ChannelError, Notice, check_url, get_json, post

KIND = ChannelKind.gotify
LABEL = "Gotify"

# Felder der Verbindung. Der Test prueft genau diese, und genau diese muessen
# beim Speichern noch dieselben sein.
# Nur eine Ebene: Die Application in Gotify ist schon das Postfach.
PARENT_FIELDS = ("url", "token", "language")
CHILD_FIELDS: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
SECRETS = ("token",)

# Gotify zaehlt von 0 bis 10. Die Grenzen sind bewusst nicht ausgereizt: 10
# umgeht auf Android jede Ruhezeit, das gehoert der dringendsten Stufe
# vorbehalten.
PRIORITIES = {"low": 2, "normal": 5, "high": 8, "urgent": 10}


@dataclass(frozen=True)
class GotifyConfig:
    url: str
    token: str
    language: str

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)


def build(werte: dict[str, str]) -> GotifyConfig | None:
    """Aus den gespeicherten Feldern eines Ziels den Zugang bauen."""
    config = GotifyConfig(
        url=werte.get("url", "").rstrip("/"),
        token=werte.get("token", ""),
        language=werte.get("language") if werte.get("language") in ("de", "en") else "de",
    )
    return config if config.configured else None


async def check(config: GotifyConfig) -> None:
    """Antwortet unter dieser Adresse ueberhaupt ein Gotify?"""
    check_url(config.url)
    daten = await get_json(f"{config.url}/version")
    if not isinstance(daten, dict) or "version" not in daten:
        raise ChannelError("Unter dieser Adresse antwortet kein Gotify.")


async def send(config: GotifyConfig, notice: Notice) -> None:
    check_url(config.url)
    if not config.token:
        raise ChannelError("Es ist kein Token hinterlegt.")

    benachrichtigung: dict[str, object] = {}
    if notice.poster_url:
        benachrichtigung["bigImageUrl"] = notice.poster_url
    if notice.click_url:
        benachrichtigung["click"] = {"url": notice.click_url}

    payload: dict[str, object] = {
        "title": notice.title,
        "message": notice.body,
        "priority": PRIORITIES.get(notice.level, PRIORITIES[DEFAULT_LEVEL]),
        "extras": {
            # Ohne diesen Hinweis zeigt die App die Sternchen der
            # Hervorhebungen als Sternchen an.
            "client::display": {"contentType": "text/markdown"},
            **({"client::notification": benachrichtigung} if benachrichtigung else {}),
        },
    }

    await post(f"{config.url}/message?token={config.token}", json=payload)
