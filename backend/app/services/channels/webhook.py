"""Webhook - der Universalanschluss: ein POST mit festem JSON an eine Adresse.

Fuer alles, wofuer es keinen eigenen Kanal gibt: Home Assistant, n8n,
Node-RED, eigene Skripte. Nexview schickt jede Meldung als POST mit immer
derselben JSON-Struktur - der Empfaenger pickt sich heraus, was er braucht.

Der Bestaetigungscode gilt auch hier: Er steht im Feld ``title`` der
Testnachricht und laesst sich dort ablesen, wo die Anfragen ankommen. Nur so
ist bewiesen, dass unter der Adresse wirklich etwas zuhoert - ein HTTP 200
allein hiesse nur "angenommen", nicht "angekommen".
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import ChannelKind
from .base import Notice, check_url, post

KIND = ChannelKind.webhook
LABEL = "Webhook"

# Nur eine Ebene: Die Adresse ist schon das Postfach. ``token`` ist der
# komplette Authorization-Header (etwa "Bearer abc123") - optional, aber ein
# Geheimnis, sobald er da ist.
PARENT_FIELDS = ("url", "token", "language")
CHILD_FIELDS: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
SECRETS = ("token",)

REQUIRES_CODE = True


@dataclass(frozen=True)
class WebhookConfig:
    url: str
    token: str
    language: str

    @property
    def configured(self) -> bool:
        return bool(self.url)


def build(werte: dict[str, str]) -> WebhookConfig | None:
    config = WebhookConfig(
        url=werte.get("url", "").strip(),
        token=werte.get("token", "").strip(),
        language=werte.get("language") if werte.get("language") in ("de", "en") else "de",
    )
    return config if config.configured else None


async def check(config: WebhookConfig) -> None:
    """Mehr als die Adresse pruefen geht nicht.

    Ein GET auf einen fremden Endpunkt koennte dort schon etwas ausloesen -
    was ankommt, entscheidet allein die Testnachricht mit dem Code.
    """
    check_url(config.url)


async def send(config: WebhookConfig, notice: Notice) -> None:
    check_url(config.url)

    # Maschinen lesen keine Hervorhebungen - die Sternchen der anderen
    # Kanaele fliegen raus, der Text bleibt.
    rumpf: dict[str, object] = {
        "source": "nexview",
        "event": notice.event or "test",
        "level": notice.level,
        "title": notice.title,
        "body": notice.body.replace("**", ""),
        "image": notice.poster_url,
        "url": notice.click_url,
    }

    headers = {"Authorization": config.token} if config.token else None
    await post(config.url, json=rumpf, headers=headers)
