"""Die Konten eines Nexview-Kontos auf den Medienservern, beim Loeschen.

Entschieden am 12. und 13.09.2026:

* Beim Loeschen eines Kontos fragt Nexview, ob der Zugang auf den
  Medienservern mitgeht. Ohne Haken bleibt dort alles, wie es ist.
* **Vorausgewaehlt ist nur, was eine Nexview-Einladung angelegt hat.** Ein
  selbst verknuepftes, aelteres Konto steht unangehakt da: Sein Verlauf gehoert
  der Person schon laenger.
* Wo Nexview Konten anlegt, wird das Konto geloescht. Wo es nur freigibt,
  endet die Freigabe dieses Servers; das Konto dort gehoert dem Anbieter, und
  die Freundschaft bleibt.
* **Scheitert ein Server, bricht das Loeschen ab**, bevor Bestand und Konto
  angefasst werden. Was auf anderen Servern schon entfernt ist, bleibt
  entfernt; der naechste Versuch findet es als "schon weg".

Welcher Weg gilt, sagt der Adapter (``legt_konten_an``, ``gibt_frei``); hier
steht kein Anbietername.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import meldungen
from ..models import AuthToken, EinladungsServer, User
from . import kontorechte
from .mediaserver import PROVIDERS, MediaServerError, media_server_for_setup, verbindung_fuer
from .settings_service import AppSettings

logger = logging.getLogger("nexview.serverkonten")

#: Warum sich ein Zugang gerade nicht entfernen laesst.
NICHT_VERBUNDEN = "nicht_verbunden"
ANDERER_SERVER = "anderer_server"
ADMINISTRATOR = "administrator"
NICHT_ERREICHBAR = "nicht_erreichbar"
SCHON_WEG = "schon_weg"
GRUENDE = (NICHT_VERBUNDEN, ANDERER_SERVER, ADMINISTRATOR, NICHT_ERREICHBAR, SCHON_WEG)

#: Was mit dem Zugang passiert.
KONTO = "konto"
FREIGABE = "freigabe"


class ServerKontoFehler(Exception):
    """Das Entfernen ging nicht, mit der fertigen Antwort dazu."""

    def __init__(self, status_code: int, detail: dict[str, object]) -> None:
        super().__init__(str(detail.get("message", "")))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ServerKonto:
    provider: str
    label: str
    #: Die Kennung beim Anbieter: das Konto dort, bei einer Freigabe das Konto der Person.
    konto: str
    name: str
    #: ``konto`` (wird geloescht) oder ``freigabe`` (wird zurueckgenommen).
    art: str
    aus_einladung: bool
    #: ``None``: laesst sich entfernen. Sonst eine Kennung aus ``GRUENDE``.
    grund: str | None

    @property
    def vorausgewaehlt(self) -> bool:
        return self.aus_einladung and self.grund is None


@dataclass
class _Fund:
    name: str = ""
    aus_einladung: bool = False
    #: Der Server beim Einladen. Nur aus einer Einladung bekannt.
    machine_id: str = ""


def _funde(db: Session, user: User) -> dict[tuple[str, str], _Fund]:
    """Verknuepfte Konten und alles, was eine Einladung dieser Person angelegt hat.

    Beides zusammen: Wer ein Konto aus der Einladung im Profil wieder geloest
    hat, hat es auf dem Server trotzdem noch.
    """
    funde: dict[tuple[str, str], _Fund] = {}
    for zeile in user.mediaserver_accounts:
        if zeile.provider and zeile.account_id:
            funde[(zeile.provider, zeile.account_id)] = _Fund(name=zeile.username or "")
    for token in db.scalars(select(AuthToken).where(AuthToken.redeemed_by == user.id)):
        for ziel in token.server:
            if ziel.zustand != EinladungsServer.FERTIG or not ziel.konto:
                continue
            fund = funde.setdefault((ziel.provider, ziel.konto), _Fund())
            fund.aus_einladung = True
            fund.machine_id = ziel.machine_id or ""
            fund.name = fund.name or ziel.konto_name or ""
    return funde


async def _grund(settings: AppSettings, provider: str, konto: str, fund: _Fund) -> str | None:
    if not kontorechte.server_stand(settings, provider).frei:
        return NICHT_VERBUNDEN
    verbindung = verbindung_fuer(settings, provider)
    if fund.machine_id and verbindung is not None and verbindung.machine_id != fund.machine_id:
        return ANDERER_SERVER
    klasse = PROVIDERS[provider]
    server = media_server_for_setup(settings, provider)
    try:
        if klasse.legt_konten_an():
            administrator = await server.ist_administrator(konto)
            if administrator is None:
                return SCHON_WEG
            return ADMINISTRATOR if administrator else None
        return None if await server.hat_freigabe(konto) else SCHON_WEG
    except MediaServerError as fehler:
        logger.info(
            "Could not check the %s account before deleting (%s)",
            klasse.label,
            fehler.code or fehler.message,
        )
        return NICHT_ERREICHBAR


async def vorschau(db: Session, settings: AppSettings, user: User) -> list[ServerKonto]:
    """Was sich beim Loeschen auf den Medienservern entfernen liesse. Es passiert nichts.

    Die Server werden gleichzeitig gefragt: Ist einer aus, soll der Dialog nicht
    nacheinander auf jede Zeitgrenze warten.
    """
    kandidaten = [
        (provider, konto, fund, klasse)
        for (provider, konto), fund in sorted(_funde(db, user).items())
        if (klasse := PROVIDERS.get(provider)) is not None
        and (klasse.legt_konten_an() or klasse.gibt_frei())
    ]
    gruende = await asyncio.gather(
        *(_grund(settings, provider, konto, fund) for provider, konto, fund, _ in kandidaten)
    )
    return [
        ServerKonto(
            provider=provider,
            label=klasse.label,
            konto=konto,
            name=fund.name,
            art=KONTO if klasse.legt_konten_an() else FREIGABE,
            aus_einladung=fund.aus_einladung,
            grund=grund,
        )
        for (provider, konto, fund, klasse), grund in zip(kandidaten, gruende, strict=True)
    ]


async def entfernen(
    db: Session,
    settings: AppSettings,
    user: User,
    auswahl: list[tuple[str, str]],
    *,
    wer: str,
) -> None:
    """Den gewaehlten Zugang entfernen, **bevor** Bestand und Konto angefasst werden.

    Geprueft wird gegen eine frische Vorschau: Wer etwas angehakt hat, das
    inzwischen nicht mehr geht (Server getrennt, Konto zum Administrator
    geworden), bekommt 409 und entscheidet neu. Scheitert ein Server, bricht es
    mit 502 ab. Das Nexview-Konto bleibt, und die Meldung nennt den Server.
    """
    if not auswahl:
        return
    stand = {(k.provider, k.konto): k for k in await vorschau(db, settings, user)}
    gewaehlt: list[ServerKonto] = []
    for schluessel in dict.fromkeys(auswahl):
        eintrag = stand.get(schluessel)
        if eintrag is None or eintrag.grund not in (None, SCHON_WEG):
            raise ServerKontoFehler(
                409,
                meldungen.meldung(
                    "server_accounts_changed",
                    "Die Konten auf den Medienservern haben sich geändert. "
                    "Bitte neu laden und erneut entscheiden.",
                ),
            )
        gewaehlt.append(eintrag)

    for eintrag in gewaehlt:
        if eintrag.grund == SCHON_WEG:
            continue
        server = media_server_for_setup(settings, eintrag.provider)
        try:
            if eintrag.art == KONTO:
                await server.konto_loeschen(eintrag.konto)
            else:
                await server.freigabe_entfernen(eintrag.konto)
        except MediaServerError as fehler:
            logger.warning(
                "Deleting %r: removing access on %s failed (%s); "
                "the Nexview account and its items were left alone",
                user.username,
                eintrag.label,
                fehler.code or fehler.message,
            )
            raise ServerKontoFehler(
                502,
                meldungen.meldung(
                    "server_account_removal_failed",
                    f"Auf {eintrag.label} ließ sich der Zugang nicht entfernen. "
                    "Das Konto und sein Bestand bleiben, wie sie sind.",
                    service=eintrag.label,
                ),
            ) from fehler
        logger.info(
            "Deleting %r: %s on %s removed by %s",
            user.username,
            "account" if eintrag.art == KONTO else "share",
            eintrag.label,
            wer,
        )
