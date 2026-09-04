"""Web Push - Meldungen an einen Browser, auch wenn Nexview gar nicht offen ist.

Der Browser hat sich beim Push-Dienst seines Herstellers (Google, Mozilla,
Apple) eine Adresse geholt und sie Nexview gegeben. Hierhin geht jede Meldung
verschluesselt; der Dienst leitet sie weiter, ohne mitzulesen.

Zwei Normen, und beide sind Pflicht:

* **RFC 8291** - der Rumpf ist mit einem Schluessel verschluesselt, den nur
  dieser Browser hat (``aes128gcm``). Deshalb darf im Rumpf ruhig ein Titel
  stehen.
* **RFC 8292** - Nexview weist sich beim Push-Dienst mit einer Unterschrift
  aus (VAPID). Der Browser hat sein Abonnement mit unserem oeffentlichen
  Schluessel angelegt; eine Meldung ohne die passende Unterschrift nimmt der
  Dienst gar nicht erst an.

⚠️ **Die Felder heissen wie bei den anderen Kanaelen, tragen aber etwas
anderes.** Die Zieltabelle hat feste Spalten, und ein Web-Push-Abonnement
besteht aus drei Werten: ``url`` ist die Adresse beim Push-Dienst, ``token``
der oeffentliche Schluessel des Browsers (``p256dh``, kein Geheimnis - der
Browser gibt ihn jeder Seite dieser Herkunft) und ``password`` das
Ableitungsgeheimnis (``auth``), verschluesselt wie jedes andere Passwort.
Wer die Zuordnung aendert, aendert sie in ``services/webpush.anmelden`` mit.

⚠️ **Der private VAPID-Schluessel kommt aus den Einstellungen, nicht vom
Ziel** (``GLOBAL_FIELDS``, wie der Mailserver bei E-Mail). Er gehoert zur
Installation: Alle Abonnements haengen an seinem oeffentlichen Gegenstueck,
und ein neues Paar hiesse, dass sich jedes Geraet neu anmelden muss.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

import http_ece
import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02

from ...models import ChannelKind
from .base import (
    DEFAULT_LEVEL,
    TIMEOUT,
    ChannelError,
    ChannelGone,
    Notice,
    check_url,
    lesbar,
)

KIND = ChannelKind.webpush
LABEL = "Web Push"

# Nur eine Ebene: Das Abonnement ist schon das Postfach.
PARENT_FIELDS = ("url", "token", "password", "language")
CHILD_FIELDS: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
SECRETS = ("password",)
# Aus den allgemeinen Einstellungen: der private Schluessel der Installation
# und die oeffentliche Adresse, mit der sich Nexview beim Dienst vorstellt.
GLOBAL_FIELDS = ("push_vapid_private", "public_url")

# Kein Bestaetigungscode: Das Abonnement entsteht in genau dem Browser, der
# die Meldungen bekommt, aus einer angemeldeten Sitzung heraus. Der Beweis,
# den der Code beim Webhook liefert, ist hier eingebaut.
REQUIRES_CODE = False

#: Wie lange der Push-Dienst eine Meldung aufhebt, wenn das Geraet gerade aus
#: ist. Ein Tag: "Dein Titel ist da" stimmt morgen noch, und wer sein Handy
#: laenger nicht anschaltet, liest es dann in der Glocke.
TTL_SEKUNDEN = 24 * 3600

#: ⚠️ **Der Rumpf ist gedeckelt.** Die Push-Dienste nehmen rund 4 kB an, und
#: was darueber liegt, weisen sie mit 413 ab. Ein Titel kann beliebig lang
#: sein, deshalb wird gekuerzt und nicht gehofft.
RUMPF_GRENZE = 3000

#: Laenger liest auf einem Sperrbildschirm ohnehin niemand.
TEXT_GRENZE = 120

# Die vier Stufen des Betreibers, in die drei des Standards uebersetzt.
# ``urgent`` gibt es dort nicht; ``high`` ist die hoechste Stufe, die ein
# Geraet aus dem Stromsparen holt.
URGENCY = {"low": "low", "normal": "normal", "high": "high", "urgent": "high"}

#: Der ``sub``-Anspruch ohne oeffentliche Adresse. RFC 8292 verlangt
#: ``mailto:`` oder ``https:``; ein ``http://``-Zugang taugt dafuer nicht.
#: Keine Adresse eines Menschen - der Wert geht an Google und Mozilla.
ABSENDER_OHNE_ADRESSE = "mailto:admin@localhost"


@dataclass(frozen=True)
class WebPushConfig:
    url: str
    token: str
    password: str
    language: str
    push_vapid_private: str
    public_url: str

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token and self.password and self.push_vapid_private)


def build(werte: dict[str, str]) -> WebPushConfig | None:
    config = WebPushConfig(
        url=werte.get("url", "").strip(),
        token=werte.get("token", "").strip(),
        password=werte.get("password", "").strip(),
        language=werte.get("language") if werte.get("language") in ("de", "en") else "de",
        push_vapid_private=werte.get("push_vapid_private", ""),
        public_url=werte.get("public_url", "").strip(),
    )
    return config if config.configured else None


async def check(config: WebPushConfig) -> None:
    """Mehr als die Adresse pruefen geht nicht.

    Ein GET auf die Adresse beim Push-Dienst sagt nichts darueber, ob auf dem
    Geraet etwas ankommt - das sagt nur die Probemeldung.
    """
    check_url(config.url)


def absender(public_url: str) -> str:
    """Der ``sub``-Anspruch der Unterschrift: wer im Zweifel erreichbar ist.

    ⚠️ **Nur die Herkunft, ohne Pfad.** Die Pruefung in ``py_vapid`` verankert
    ihre Regex am Ende des Hostnamens; ``https://beispiel.de/nexview`` faellt
    durch, und die Meldung ("Missing 'sub' from claims") zeigt auf ein
    fehlendes Feld statt auf ein zu langes. Nexview laeuft ausdruecklich auch
    unter einem Unterpfad, das trifft also nicht nur Sonderfaelle.
    """
    if public_url.startswith("https://"):
        herkunft = httpx.URL(public_url)
        if herkunft.host:
            return f"https://{herkunft.netloc.decode()}"
    return ABSENDER_OHNE_ADRESSE


def rumpf(notice: Notice) -> bytes:
    """Was der Service Worker zu lesen bekommt.

    Fertig formuliert: Ein Service Worker lebt ohne Dokument und hat weder
    Uebersetzung noch die eingestellte Sprache. Die Hervorhebungen der
    anderen Kanaele fliegen raus - ein Sperrbildschirm zeigt Sternchen als
    Sternchen.

    ``tag`` fasst gleiche Meldungen zusammen: Dieselbe Aussage zum selben
    Titel ersetzt die vorige, statt sich daneben zu stellen. Verschiedene
    Titel bleiben verschiedene Meldungen.
    """
    titel = notice.title[:TEXT_GRENZE]
    text = notice.body.replace("**", "")[:TEXT_GRENZE]
    daten = {
        "title": titel,
        "body": text,
        "url": notice.click_url,
        "tag": f"{notice.event or 'nexview'}:{text}",
        # Nur ein vollstaendiger Verweis taugt als Bild; ein TMDB-Pfad ohne
        # Herkunft ergaebe auf dem Geraet eine leere Flaeche.
        "image": (notice.poster_url if notice.poster_url and notice.poster_url.startswith("http") else None),
        "code": notice.code,
    }
    return json.dumps(daten, ensure_ascii=False).encode()[:RUMPF_GRENZE]


def _roh(base64url: str) -> bytes:
    """base64url ohne Polster, wie der Browser es liefert."""
    return base64.urlsafe_b64decode(base64url + "=" * (-len(base64url) % 4))


def kopfzeilen(config: WebPushConfig, notice: Notice) -> dict[str, str]:
    """Unterschrift und Rahmendaten einer Meldung.

    ⚠️ **``Vapid02``, nicht ``Vapid01``.** Beide gibt es in ``py_vapid``, und
    nur die zweite Fassung (``Authorization: vapid t=<jwt>,k=<schluessel>``)
    gehoert zu ``aes128gcm``. Die erste schickt ``WebPush <jwt>`` samt eigener
    ``Crypto-Key``-Zeile; im Betrieb waere das ein 401, und der liest sich wie
    ein abgelaufener Schluessel.
    """
    herkunft = httpx.URL(config.url)
    unterschrift = Vapid02.from_pem(config.push_vapid_private.encode())
    kopf = unterschrift.sign(
        {
            "aud": f"{herkunft.scheme}://{herkunft.netloc.decode()}",
            "sub": absender(config.public_url),
            "exp": int(time.time()) + 12 * 3600,
        }
    )
    kopf.update(
        {
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(TTL_SEKUNDEN),
            "Urgency": URGENCY.get(notice.level, URGENCY[DEFAULT_LEVEL]),
        }
    )
    return kopf


def verschluesseln(config: WebPushConfig, notice: Notice) -> bytes:
    """Der Rumpf, wie er ueber die Leitung geht - nur der Browser liest ihn."""
    fluechtig = ec.generate_private_key(ec.SECP256R1())
    return http_ece.encrypt(
        rumpf(notice),
        private_key=fluechtig,
        dh=_roh(config.token),
        auth_secret=_roh(config.password),
        version="aes128gcm",
    )


async def send(config: WebPushConfig, notice: Notice) -> None:
    check_url(config.url)
    ziel = config.url.split("//", 1)[-1].split("/", 1)[0] or config.url
    try:
        inhalt = verschluesseln(config, notice)
        kopf = kopfzeilen(config, notice)
    except ValueError as fehler:
        raise ChannelError(f"Das Abonnement ist unbrauchbar: {fehler}") from fehler

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            antwort = await client.post(config.url, content=inhalt, headers=kopf)
    except httpx.HTTPError as fehler:
        raise ChannelError(lesbar(fehler, ziel)) from fehler

    # ⚠️ Nur diese beiden Nummern heissen "weg". Ein 401 oder 403 heisst
    # "falsche Unterschrift" - das waere ein Fehler bei uns, und wer den
    # genauso behandelt, raeumt bei einem eigenen Fehler die Abonnements aller
    # Benutzer weg, ohne dass jemand herausfaende, warum nichts mehr kommt.
    if antwort.status_code in (404, 410):
        raise ChannelGone(f"Der Push-Dienst kennt dieses Abonnement nicht mehr (HTTP {antwort.status_code}).")
    if antwort.status_code in (401, 403):
        raise ChannelError(
            f"{ziel} hat die Unterschrift abgelehnt (HTTP {antwort.status_code}). "
            "Der VAPID-Schlüssel passt nicht zum Abonnement."
        )
    if antwort.status_code == 413:
        raise ChannelError(f"{ziel} hält die Nachricht für zu groß (HTTP 413).")
    if antwort.status_code >= 400:
        raise ChannelError(f"{ziel} antwortete mit HTTP {antwort.status_code}.")
