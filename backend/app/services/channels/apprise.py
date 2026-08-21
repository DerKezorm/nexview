"""Apprise - ein Verteiler, der eine Meldung an ueber 100 Dienste weiterreicht.

Nexview spricht die selbst gehostete **Apprise API** an: Dort liegt unter
einem frei gewaehlten Konfigurations-Schluessel die Liste der Zieldienste
(Signal, Matrix, SMS, ...) samt deren Zugangsdaten. Nexview kennt nur Adresse
und Schluessel - die Geheimnisse der Zieldienste bleiben komplett im
Apprise-Server.

Der Bestaetigungscode kommt deshalb nicht bei Apprise an, sondern bei den
dort verbundenen Endgeraeten - genau da, wo spaeter auch die Meldungen landen.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import ChannelKind
from . import base
from .base import DEFAULT_LEVEL, ChannelError, Notice, check_url, get_json

KIND = ChannelKind.apprise
LABEL = "Apprise"

# Nur eine Ebene. Der Konfigurations-Schluessel wohnt im ``topic``-Feld - er
# benimmt sich wie ein ntfy-Topic: frei gewaehlt, steht im Apprise-Webinterface
# im Klartext, ist also kein Geheimnis im Sinne der Verschluesselung.
PARENT_FIELDS = ("url", "topic", "language")
CHILD_FIELDS: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
SECRETS: tuple[str, ...] = ()

REQUIRES_CODE = True

# Apprise kennt vier Sorten Meldung, die Zieldienste faerben danach ein.
TYPES = {"low": "info", "normal": "info", "high": "warning", "urgent": "failure"}


@dataclass(frozen=True)
class AppriseConfig:
    url: str
    topic: str
    language: str

    @property
    def configured(self) -> bool:
        return bool(self.url and self.topic)


def build(werte: dict[str, str]) -> AppriseConfig | None:
    config = AppriseConfig(
        url=werte.get("url", "").rstrip("/"),
        topic=werte.get("topic", "").strip(),
        language=werte.get("language") if werte.get("language") in ("de", "en") else "de",
    )
    return config if config.configured else None


async def check(config: AppriseConfig) -> None:
    """Antwortet unter dieser Adresse ueberhaupt eine Apprise API?"""
    check_url(config.url)
    daten = await get_json(f"{config.url}/status", headers={"Accept": "application/json"})
    if not isinstance(daten, dict) or "status" not in daten:
        raise ChannelError("Unter dieser Adresse antwortet keine Apprise API.")


async def send(config: AppriseConfig, notice: Notice) -> None:
    check_url(config.url)

    zeilen = [notice.body]
    # Apprise kennt kein eigenes Link-Feld - der Verweis wandert in den Text.
    if notice.click_url:
        zeilen.append(notice.click_url)

    rumpf: dict[str, object] = {
        "title": notice.title,
        "body": "\n\n".join(zeilen),
        "type": TYPES.get(notice.level, TYPES[DEFAULT_LEVEL]),
        "format": "markdown",
    }

    # Nicht ``base.post``: Apprise meldet einen unbekannten oder leeren
    # Schluessel als 204 - fuer HTTP ein Erfolg, fuer uns das Gegenteil.
    adresse = f"{config.url}/notify/{config.topic}"
    ziel = adresse.split("//", 1)[-1].split("/", 1)[0]
    try:
        async with base.httpx.AsyncClient(
            timeout=base.TIMEOUT, follow_redirects=False
        ) as client:
            antwort = await client.post(
                adresse, json=rumpf, headers={"Accept": "application/json"}
            )
    except base.httpx.HTTPError as fehler:
        raise ChannelError(base._lesbar(fehler, ziel)) from fehler

    if antwort.status_code == 204:
        raise ChannelError(
            "Unter diesem Schlüssel liegt in Apprise keine Konfiguration."
        )
    if antwort.status_code == 424:
        raise ChannelError("Apprise konnte an mindestens ein Ziel nicht zustellen.")
    if antwort.is_error:
        raise ChannelError(f"{ziel} hat mit HTTP {antwort.status_code} geantwortet.")
