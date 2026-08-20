"""Telegram - ein Bot, beliebig viele Chats.

Zwei Ebenen wie bei ntfy: Der **Bot** traegt das Token, der **Chat** darunter
ist das Postfach. Ein Bot kann in beliebig vielen Chats und Gruppen sitzen, und
nur im Chat kommt tatsaechlich etwas an - dort haengt deshalb auch der
Bestaetigungscode.

Anders als seerr wird **HTML** ausgezeichnet, nicht MarkdownV2. Telegrams
MarkdownV2 verlangt, dass ein gutes Dutzend Zeichen einzeln maskiert wird -
``.``, ``-``, ``!``, ``(``, ``)`` und weitere. Genau davon stecken Filmtitel
voll, und ein vergessenes Zeichen quittiert Telegram mit HTTP 400 statt mit
einer Nachricht. Bei HTML sind es drei Zeichen.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from ...models import ChannelKind
from .base import ChannelError, Notice, get_json, post_json

KIND = ChannelKind.telegram
LABEL = "Telegram"

# Der Bot oben, die Chats darunter.
PARENT_FIELDS = ("token", "username")
# Ohne dieses Feld ist die obere Ebene unvollstaendig.
PARENT_REQUIRED = ("token",)
CHILD_FIELDS = ("chat_id", "thread_id", "silent", "language")
FIELDS = PARENT_FIELDS + CHILD_FIELDS
SECRETS = ("token",)

REQUIRES_CODE = True

API = "https://api.telegram.org"


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    username: str
    chat_id: str
    thread_id: str
    silent: bool
    language: str

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def url(self, methode: str) -> str:
        return f"{API}/bot{self.token}/{methode}"


def build(werte: dict[str, str]) -> TelegramConfig | None:
    config = TelegramConfig(
        token=werte.get("token", "").strip(),
        username=werte.get("username", "").strip().lstrip("@"),
        chat_id=werte.get("chat_id", "").strip(),
        thread_id=werte.get("thread_id", "").strip(),
        # Ja/Nein wandert wie alles andere als Text durch die Zielverwaltung;
        # hier wird daraus wieder ein Wahrheitswert.
        silent=werte.get("silent", "").strip().lower() in ("on", "true", "1", "ja"),
        language=werte.get("language") if werte.get("language") in ("de", "en") else "de",
    )
    return config if config.configured else None


async def check(config: TelegramConfig) -> dict[str, str] | None:
    """Stimmt das Token? ``getMe`` beantwortet das - und nennt den Bot beim Namen.

    Der Benutzername wird zurueckgegeben, damit ihn niemand abtippen muss: Er
    steckt spaeter im ``t.me``-Link, mit dem sich ein Chat ueberhaupt erst
    herstellen laesst.
    """
    if not config.token:
        raise ChannelError("Es ist kein Bot-Token hinterlegt.")

    daten = await get_json(f"{API}/bot{config.token}/getMe")
    if not isinstance(daten, dict) or not daten.get("ok"):
        raise ChannelError("Telegram hat das Token abgelehnt.")

    bot = daten.get("result") or {}
    name = str(bot.get("username") or "")
    return {"username": name} if name else None


async def chats(config: TelegramConfig) -> list[dict[str, str]]:
    """Wer hat diesem Bot zuletzt geschrieben?

    ``getUpdates`` liefert die offenen Nachrichten an den Bot. Wer ihm /start
    geschickt oder ihn in eine Gruppe geholt hat, steht darin - und damit auch
    die Nummer des Chats. Das erspart den Umweg ueber einen fremden Bot, der
    dieselbe Auskunft gibt und dafuer ein Kanal-Abo verlangt.

    Telegram haelt die Nachrichten rund einen Tag vor. Kommt nichts zurueck,
    hat noch niemand geschrieben - oder es ist zu lange her.
    """
    if not config.token:
        raise ChannelError("Es ist kein Bot-Token hinterlegt.")

    daten = await get_json(f"{API}/bot{config.token}/getUpdates")
    if not isinstance(daten, dict) or not daten.get("ok"):
        raise ChannelError("Telegram hat die Anfrage abgelehnt.")

    gefunden: dict[str, dict[str, str]] = {}
    for eintrag in daten.get("result") or []:
        # Nachricht, Gruppennachricht, Beitritt - ueberall steckt derselbe Chat.
        for schluessel in ("message", "channel_post", "edited_message", "my_chat_member"):
            chat = (eintrag.get(schluessel) or {}).get("chat")
            if not isinstance(chat, dict) or "id" not in chat:
                continue
            kennung = str(chat["id"])
            name = (
                chat.get("title")
                or " ".join(
                    teil for teil in (chat.get("first_name"), chat.get("last_name")) if teil
                )
                or chat.get("username")
                or kennung
            )
            gefunden[kennung] = {
                "chat_id": kennung,
                "name": str(name),
                "type": str(chat.get("type") or ""),
            }

    return list(gefunden.values())


def _text(notice: Notice, sprache: str) -> str:
    """Die Meldung als Telegram-HTML.

    Der Rumpf kommt in Markdown, weil die Push-Dienste ihn so erwarten. Hier
    wird daraus HTML - und alles andere zuvor entschaerft, denn in den Titeln
    steckt Text, den jemand anderes bestimmt hat.
    """
    zeilen = [f"<b>{html.escape(notice.title)}</b>"]
    if notice.body:
        rumpf = html.escape(notice.body)
        # **fett** ist die einzige Auszeichnung, die in den Meldungen vorkommt.
        rumpf = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", rumpf)
        zeilen.append(rumpf)
    if notice.click_url:
        beschriftung = "Open Nexview" if sprache == "en" else "In Nexview öffnen"
        zeilen.append(f'<a href="{html.escape(notice.click_url, quote=True)}">{beschriftung}</a>')
    return "\n\n".join(zeilen)


async def send(config: TelegramConfig, notice: Notice) -> None:
    # Telegram kennt keine Prioritaet, nur "mit oder ohne Ton". Die leiseste
    # Stufe kommt deshalb lautlos an - alles andere klingelt, sofern der Chat
    # nicht ohnehin auf lautlos steht.
    lautlos = config.silent or notice.level == "low"

    rumpf: dict[str, object] = {
        "chat_id": config.chat_id,
        "parse_mode": "HTML",
        "disable_notification": lautlos,
    }
    if config.thread_id:
        rumpf["message_thread_id"] = config.thread_id

    if notice.poster_url:
        # Mit Bild: Telegram holt es sich selbst von der Adresse. Die
        # Bildunterschrift ist auf 1024 Zeichen begrenzt - fuer diese
        # Meldungen reichlich.
        methode = "sendPhoto"
        rumpf["photo"] = notice.poster_url
        rumpf["caption"] = _text(notice, config.language)
    else:
        methode = "sendMessage"
        rumpf["text"] = _text(notice, config.language)

    antwort = await post_json(config.url(methode), json=rumpf)
    if not isinstance(antwort, dict) or not antwort.get("ok"):
        grund = ""
        if isinstance(antwort, dict):
            grund = str(antwort.get("description") or "")
        raise ChannelError(f"Telegram hat die Nachricht abgelehnt. {grund}".strip())
