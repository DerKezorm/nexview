"""Verzeichnis der serverseitigen Kanaele.

Jeder Kanal ist ein Modul mit vier Dingen: ``LABEL`` fuer die Anzeige,
``FIELDS`` als Beschreibung seiner Verbindung, ``build(werte)`` fuer den Zugang
und ``send(config, notice)`` fuer den Versand. Wer einen weiteren Dienst
ergaenzt, schreibt ein solches Modul und traegt es unten ein - Postausgang,
Zielverwaltung und Oberflaechen-Ablauf muessen dafuer nicht angefasst werden.
"""

from __future__ import annotations

from typing import Any

from ...models import ChannelKind
from . import email, gotify, ntfy, telegram
from .base import DEFAULT_LEVEL, LEVELS, ChannelError, Notice

__all__ = [
    "DEFAULT_LEVEL",
    "LEVELS",
    "ChannelError",
    "Notice",
    "build",
    "chats",
    "check",
    "child_fields",
    "fields",
    "global_fields",
    "has_children",
    "requires_code",
    "parent_fields",
    "fingerprint",
    "label",
    "parent_required",
    "secrets_of",
    "send",
]

_MODULES = {
    ChannelKind.ntfy: ntfy,
    ChannelKind.gotify: gotify,
    ChannelKind.email: email,
    ChannelKind.telegram: telegram,
}


def label(kind: ChannelKind) -> str:
    """Name des Dienstes, wie er sich selbst schreibt."""
    return _MODULES[kind].LABEL


def fields(kind: ChannelKind) -> tuple[str, ...]:
    """Alle Felder, die dieser Dienst kennt."""
    return _MODULES[kind].FIELDS


def parent_fields(kind: ChannelKind) -> tuple[str, ...]:
    """Felder der oberen Ebene - bei ntfy Adresse und Anmeldung."""
    return _MODULES[kind].PARENT_FIELDS


def child_fields(kind: ChannelKind) -> tuple[str, ...]:
    """Felder der unteren Ebene - bei ntfy Topic und Sprache. Leer, wenn es keine gibt."""
    return _MODULES[kind].CHILD_FIELDS


def parent_required(kind: ChannelKind) -> tuple[str, ...]:
    """Welche Felder der oberen Ebene ausgefuellt sein muessen.

    Bei ntfy die Adresse, bei Telegram das Token - fest verdrahtet waere das
    eine Annahme, die beim naechsten Dienst umfaellt.
    """
    return getattr(_MODULES[kind], "PARENT_REQUIRED", ())


def has_children(kind: ChannelKind) -> bool:
    """Hat dieser Dienst zwei Ebenen?"""
    return bool(_MODULES[kind].CHILD_FIELDS)


async def check(kind: ChannelKind, config: Any) -> dict[str, str] | None:
    """Ist die Instanz ueberhaupt erreichbar? Sonst ``ChannelError``.

    Ohne Bestaetigungscode - der haengt an der Stelle, an der eine Nachricht
    ankommen kann, und das ist bei ntfy erst das Topic.

    Manche Dienste verraten dabei etwas, das man sonst abtippen muesste:
    Telegrams ``getMe`` nennt den Benutzernamen des Bots. Solche Funde kommen
    als Zuordnung zurueck und landen in der Oberflaeche im passenden Feld.
    """
    return await _MODULES[kind].check(config)


def secrets_of(kind: ChannelKind) -> tuple[str, ...]:
    """Welche davon Geheimnisse sind."""
    return _MODULES[kind].SECRETS


def global_fields(kind: ChannelKind) -> tuple[str, ...]:
    """Felder, die aus den allgemeinen Einstellungen kommen statt vom Ziel.

    Bei E-Mail sind das Mailserver und Absender: Die gehoeren zur Installation,
    nicht zur einzelnen Adresse.
    """
    return getattr(_MODULES[kind], "GLOBAL_FIELDS", ())


def kann_chats_suchen(kind: ChannelKind) -> bool:
    """Kann dieser Dienst selbst sagen, welche Postfaecher es gibt?"""
    return hasattr(_MODULES[kind], "chats")


async def chats(kind: ChannelKind, config: Any) -> list[dict[str, str]]:
    """Die Postfaecher, die der Dienst von sich aus kennt."""
    return await _MODULES[kind].chats(config)


def requires_code(kind: ChannelKind) -> bool:
    """Muss ein Bestaetigungscode aus der Testnachricht eingetippt werden?

    Bei Push-Diensten ja - nur so ist bewiesen, dass etwas ankommt. Bei E-Mail
    nicht: Hinter einer allgemeinen Adresse sitzt womoeglich niemand, der einen
    Code ablesen koennte, und ein Verteiler schickte ihn an alle weiter.
    """
    return getattr(_MODULES[kind], "REQUIRES_CODE", True)


def build(kind: ChannelKind, werte: dict[str, str]) -> Any | None:
    """Der Zugang zu einem Ziel - ``None``, wenn er unvollstaendig ist."""
    return _MODULES[kind].build(werte)


def fingerprint(kind: ChannelKind, config: Any) -> tuple:
    """Ein Abdruck der Verbindung, um "getestet" und "gespeichert" zu vergleichen.

    Ohne ihn koennte man eine Adresse testen und danach eine andere speichern -
    die Bestaetigung haette dann fuer etwas gegolten, das nie geprueft wurde.
    """
    return tuple(str(getattr(config, feld, "")) for feld in fields(kind))


async def send(kind: ChannelKind, config: Any, notice: Notice) -> None:
    """Verschicken - oder mit ``ChannelError`` scheitern."""
    await _MODULES[kind].send(config, notice)
