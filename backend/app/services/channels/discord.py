"""Discord - eine Webhook-URL je Kanal, kein Bot.

Ein Discord-Webhook ist die einfachste Sorte Ziel: Die URL selbst ist die
Berechtigung. Wer sie kennt, kann in genau einen Kanal schreiben - ohne Konto,
ohne Anmeldung, ohne Rueckkanal. Deshalb wird sie wie ein Passwort behandelt
und verschluesselt gespeichert.

Verschickt wird ein **Embed**: Discords Nachrichtenkarte mit Farbbalken,
Poster als Vorschaubild und dem Titel als Verweis. Die Farbe traegt die
Bedeutung - Orange wartet, Gruen ist da, Rot ging schief. Dasselbe Rezept wie
bei Overseerr/Jellyseerr, an denen sich die Zielgruppe orientiert.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import ChannelKind
from .base import ChannelError, Notice, check_url, get_json, post

KIND = ChannelKind.discord
LABEL = "Discord"

# Nur eine Ebene: Der Webhook ist schon das Postfach.
PARENT_FIELDS = ("url", "username", "language")
CHILD_FIELDS: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
# Die URL ist das Geheimnis - es gibt kein getrenntes Token.
SECRETS = ("url",)

REQUIRES_CODE = True

# Das Bild neben dem Absendernamen. Discord laedt es selbst herunter, die
# Adresse muss also oeffentlich erreichbar sein - die eigene Instanz haengt
# meist im Heimnetz, deshalb liegt das Logo auf der Projektseite.
AVATAR_URL = "https://nexview.nexapps.dev/assets/img/icon-192.png"

# Farben je Meldung, wie Discord sie als Zahl erwartet. Was nicht aufgefuehrt
# ist (auch die Testnachricht), bekommt Discords eigenes Blau.
_BLURPLE = 0x5865F2
COLORS: dict[str, int] = {
    "request_pending": 0xE67E22,  # Orange - jemand wartet auf eine Entscheidung
    "approved": 0x9B59B6,  # Lila - freigegeben, wird geholt
    "download_complete": 0x2ECC71,  # Gruen - da
    "rejected": 0xE74C3C,  # Rot - abgelehnt
    "cancelled": 0xE74C3C,  # Rot - storniert (loescht Dateien)
    "feedback_poor": 0xE67E22,  # Orange - da stimmt etwas nicht
}


@dataclass(frozen=True)
class DiscordConfig:
    url: str
    username: str
    language: str

    @property
    def configured(self) -> bool:
        return bool(self.url)


def build(werte: dict[str, str]) -> DiscordConfig | None:
    config = DiscordConfig(
        url=werte.get("url", "").strip(),
        username=werte.get("username", "").strip(),
        language=werte.get("language") if werte.get("language") in ("de", "en") else "de",
    )
    return config if config.configured else None


async def check(config: DiscordConfig) -> None:
    """Zeigt die URL wirklich auf einen Webhook?

    Ein GET auf die Webhook-URL beantwortet Discord ohne weitere Anmeldung mit
    der Beschreibung des Webhooks - die URL ist ja selbst der Schluessel.
    """
    check_url(config.url)
    daten = await get_json(config.url)
    if not isinstance(daten, dict) or "channel_id" not in daten:
        raise ChannelError("Unter dieser Adresse antwortet kein Discord-Webhook.")


async def send(config: DiscordConfig, notice: Notice) -> None:
    check_url(config.url)

    # Discord versteht dieselbe **fett**-Auszeichnung wie die Push-Dienste -
    # der Rumpf kann unveraendert in die Beschreibung.
    embed: dict[str, object] = {
        "title": notice.title,
        "description": notice.body,
        "color": COLORS.get(notice.event or "", _BLURPLE),
    }
    if notice.poster_url:
        embed["thumbnail"] = {"url": notice.poster_url}
    if notice.click_url:
        embed["url"] = notice.click_url

    rumpf: dict[str, object] = {"embeds": [embed], "avatar_url": AVATAR_URL}
    if config.username:
        rumpf["username"] = config.username

    await post(config.url, json=rumpf)
