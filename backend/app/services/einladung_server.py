"""Zugang zu den Medienservern aus einer Einladung.

Auf Jellyfin und Emby legt Nexview beim Einloesen ein Konto an, mit dem Namen und
dem Passwort aus dem Formular. Auf Plex geht das nicht, ein Plex-Konto gehoert
plex.tv: Die Person verknuepft im Onboarding ihr eigenes, und Nexview gibt ihm am
Ende die Bibliotheken frei. Mehr als Bibliotheken vergibt eine Einladung auf
keinem der drei, weil nur das alle gleich koennen. Welchen Weg ein Anbieter
geht, sagt sein Adapter (``legt_konten_an``, ``gibt_frei``); hier steht kein
Anbietername.

⚠️ **Die Reihenfolge beim Einloesen ist Absicht.** Jellyfin und Emby kommen vor
dem Nexview-Konto: Nur solange das Passwort im Formular steht, laesst sich dort
etwas nachholen, und Nexview bewahrt es nicht auf. Scheitert eins, bleibt die
Einladung offen, und der naechste Versuch setzt fort, statt ein zweites Konto
anzulegen. Die Freigabe auf Plex braucht kein Passwort und kommt nach dem
Nexview-Konto; scheitert sie, holt der Administrator sie mit einem Klick nach.

Geloescht wird auf keinem Server etwas.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict

from sqlalchemy.orm import Session

from .. import meldungen
from ..crypto import decrypt, encrypt
from ..models import AuthToken, EinladungsServer, NotificationType, User, utcnow
from . import kontorechte, notify
from . import mediaserver_accounts as konten
from .mediaserver import (
    PROVIDERS,
    ExternalAccount,
    LoginChallenge,
    MediaServer,
    MediaServerError,
    media_server_for_setup,
    verbindung_fuer,
)
from .settings_service import AppSettings

logger = logging.getLogger("nexview.einladung")

#: Die Kennung, unter der ein schon vergebenes Konto am Ziel steht. Daran
#: erkennt ``_schon_vergeben``, dass der Administrator Bescheid weiss.
SCHON_VERGEBEN = "invite_account_linked_elsewhere"


class EinladungsFehler(Exception):
    """Etwas an der Einladung geht so nicht, mit der fertigen Antwort dazu."""

    def __init__(self, status_code: int, detail: dict[str, object]) -> None:
        super().__init__(str(detail.get("message", "")))
        self.status_code = status_code
        self.detail = detail


# --------------------------------------------------------------------------
# Kleinteile
# --------------------------------------------------------------------------


def bibliotheken(ziel: EinladungsServer) -> list[dict[str, str]]:
    """Die Bibliotheken des Ziels, so wie sie beim Einladen gewaehlt wurden."""
    try:
        daten = json.loads(ziel.bibliotheken or "[]")
    except ValueError:
        return []
    if not isinstance(daten, list):
        return []
    return [
        {"kennung": str(b["kennung"]), "name": str(b.get("name") or "")}
        for b in daten
        if isinstance(b, dict) and b.get("kennung")
    ]


def _kennungen(ziel: EinladungsServer) -> list[str]:
    return [b["kennung"] for b in bibliotheken(ziel)]


def _als_text(detail: dict[str, object]) -> str:
    # Die Kontonummer steht schon in ``konto``; in einer Anzeige hat sie nichts verloren.
    return json.dumps({k: v for k, v in detail.items() if k != "konto"}, ensure_ascii=False)


def _fehler(ziel: EinladungsServer) -> dict[str, object] | None:
    if not ziel.fehler:
        return None
    try:
        daten = json.loads(ziel.fehler)
    except ValueError:
        return None
    return daten if isinstance(daten, dict) else None


def _klasse(ziel: EinladungsServer) -> type[MediaServer] | None:
    return PROVIDERS.get(ziel.provider)


def _label(provider: str) -> str:
    klasse = PROVIDERS.get(provider)
    return klasse.label if klasse is not None else provider


def _verbunden(settings: AppSettings, ziel: EinladungsServer) -> bool:
    """Ist noch **derselbe** Server verbunden wie beim Einladen?"""
    verbindung = verbindung_fuer(settings, ziel.provider)
    return (
        kontorechte.server_stand(settings, ziel.provider).frei
        and verbindung is not None
        and verbindung.machine_id == ziel.machine_id
    )


# --------------------------------------------------------------------------
# Einladen
# --------------------------------------------------------------------------


async def auswahl(settings: AppSettings) -> list[dict[str, object]]:
    """Was der Einladungsassistent je Anbieter zeigt: ob es geht, und welche Bibliotheken.

    Alle Anbieter stehen in der Liste, auch die nicht verbundenen. Der Assistent
    zeigt sie ausgegraut mit Grund, statt sie still wegzulassen.
    """

    async def eintrag(provider: str) -> dict[str, object]:
        stand = kontorechte.server_stand(settings, provider)
        verbindung = verbindung_fuer(settings, provider)
        daten: dict[str, object] = {
            "provider": provider,
            "label": _label(provider),
            "art": "freigabe" if PROVIDERS[provider].gibt_frei() else "konto",
            "stand": asdict(stand),
            "name": verbindung.name if stand.frei and verbindung is not None else "",
            "bibliotheken": [],
            "fehler": None,
        }
        if stand.frei:
            try:
                gefunden = await media_server_for_setup(settings, provider).bibliotheken()
                daten["bibliotheken"] = [asdict(b) for b in gefunden]
            except MediaServerError as exc:
                daten["fehler"] = exc.als_meldung()
        return daten

    return list(await asyncio.gather(*(eintrag(provider) for provider in PROVIDERS)))


async def ziele_pruefen(
    settings: AppSettings, wuensche: list[tuple[str, list[str]]]
) -> list[EinladungsServer]:
    """Die Auswahl aus dem Assistenten gegen die Server halten, noch ohne zu speichern.

    Gefragt wird der Server selbst, nicht der Stand, den der Assistent vor ein
    paar Minuten gezeigt hat. Eine Bibliothek, die es nicht mehr gibt, faellt so
    hier auf und nicht erst, wenn die Person vor ihrem Konto steht.
    """
    ziele: list[EinladungsServer] = []
    for provider, gewaehlt in wuensche:
        label = _label(provider)
        if not kontorechte.server_stand(settings, provider).frei:
            raise EinladungsFehler(
                422,
                meldungen.meldung(
                    "invite_server_not_connected", f"{label} ist nicht verbunden.", service=label
                ),
            )
        if not gewaehlt:
            raise EinladungsFehler(
                422,
                meldungen.meldung(
                    "invite_server_no_library",
                    f"Ohne Bibliothek sieht die Person auf {label} nichts.",
                    service=label,
                ),
            )
        try:
            vorhanden = {
                b.kennung: b
                for b in await media_server_for_setup(settings, provider).bibliotheken()
            }
        except MediaServerError as exc:
            raise EinladungsFehler(502, exc.als_meldung()) from exc
        if any(kennung not in vorhanden for kennung in gewaehlt):
            raise EinladungsFehler(
                422,
                meldungen.meldung(
                    "invite_library_unknown",
                    f"Eine gewählte Bibliothek gibt es auf {label} nicht mehr.",
                    service=label,
                ),
            )
        verbindung = verbindung_fuer(settings, provider)
        ziele.append(
            EinladungsServer(
                provider=provider,
                machine_id=verbindung.machine_id if verbindung is not None else "",
                bibliotheken=json.dumps(
                    [
                        {"kennung": kennung, "name": vorhanden[kennung].name}
                        for kennung in dict.fromkeys(gewaehlt)
                    ],
                    ensure_ascii=False,
                ),
            )
        )
    return ziele


# --------------------------------------------------------------------------
# Anzeigen
# --------------------------------------------------------------------------


def fuer_person(token: AuthToken) -> list[dict[str, object]]:
    """Was die eingeladene Person ueber ihre Server erfaehrt."""
    ergebnis: list[dict[str, object]] = []
    for ziel in token.server:
        klasse = _klasse(ziel)
        if klasse is None or ziel.zustand == EinladungsServer.GESEHEN:
            continue
        ergebnis.append(
            {
                "provider": ziel.provider,
                "label": klasse.label,
                # "konto": Nexview legt eines an. "freigabe": Die Person bringt ihr eigenes mit.
                "art": "freigabe" if klasse.gibt_frei() else "konto",
                "bibliotheken": [b["name"] for b in bibliotheken(ziel)],
                "zustand": ziel.zustand,
                "konto_name": ziel.konto_name if ziel.konto else None,
                "fehler": _fehler(ziel),
            }
        )
    return ergebnis


def fuer_admin(token: AuthToken) -> list[dict[str, object]]:
    """Die Server einer Einladung, wie die Einladungsliste sie zeigt."""
    ergebnis: list[dict[str, object]] = []
    for ziel in token.server:
        klasse = _klasse(ziel)
        angefangen = (
            klasse is not None
            and klasse.legt_konten_an()
            and ziel.zustand == EinladungsServer.OFFEN
            and bool(ziel.konto)
        )
        ergebnis.append(
            {
                "provider": ziel.provider,
                "label": _label(ziel.provider),
                "bibliotheken": [b["name"] for b in bibliotheken(ziel)],
                "zustand": "angefangen" if angefangen else ziel.zustand,
                "fehler": _fehler(ziel),
                "nachholbar": bool(
                    klasse is not None
                    and klasse.gibt_frei()
                    and ziel.zustand == EinladungsServer.FEHLT
                    and ziel.konto
                    and token.redeemed_by
                ),
            }
        )
    return ergebnis


def hat_hinweis(token: AuthToken) -> bool:
    """Steht an der Einladung ein Server, der fehlt?"""
    return any(ziel.zustand == EinladungsServer.FEHLT for ziel in token.server)


# --------------------------------------------------------------------------
# Onboarding
# --------------------------------------------------------------------------


def abgleichen(settings: AppSettings, token: AuthToken) -> None:
    """Server, die seit dem Einladen nicht mehr verbunden sind, fallen weg.

    Wie bei den Rechten gilt der Stand von jetzt (``einladungen.bewerten``). Die
    Person wird davon nicht aufgehalten; der Administrator sieht in der Liste,
    was entfallen ist.
    """
    for ziel in token.server:
        if ziel.zustand == EinladungsServer.OFFEN and not _verbunden(settings, ziel):
            label = _label(ziel.provider)
            ziel.zustand = EinladungsServer.FEHLT
            ziel.fehler = _als_text(
                meldungen.meldung(
                    "invite_server_gone",
                    f"{label} ist seit dem Einladen nicht mehr verbunden.",
                    service=label,
                )
            )


def _freigabe_ziel(token: AuthToken, provider: str) -> EinladungsServer:
    for ziel in token.server:
        klasse = _klasse(ziel)
        if ziel.provider == provider and klasse is not None and klasse.gibt_frei():
            return ziel
    raise EinladungsFehler(
        404,
        meldungen.meldung(
            "invite_server_unknown", "Diesen Medienserver sieht die Einladung nicht vor."
        ),
    )


async def verknuepfen_starten(
    db: Session, settings: AppSettings, token: AuthToken, provider: str
) -> tuple[str, LoginChallenge]:
    """Die Anmeldung beim Anbieter beginnen, gebunden an genau diese Einladung."""
    ziel = _freigabe_ziel(token, provider)
    abgleichen(settings, token)
    if ziel.zustand != EinladungsServer.OFFEN:
        db.commit()
        raise EinladungsFehler(409, _fehler(ziel) or {})

    settings = konten.ensure_client_identifier(db, settings)
    try:
        challenge = await media_server_for_setup(settings, provider).begin_login()
    except MediaServerError as exc:
        raise EinladungsFehler(502, exc.als_meldung()) from exc
    return konten.start_challenge(db, provider, challenge, einladung=token.id), challenge


async def verknuepfen_abfragen(
    db: Session, settings: AppSettings, token: AuthToken, provider: str, poll_token: str
) -> str | None:
    """Ist die Anmeldung beim Anbieter durch? Liefert den Kontonamen, solange offen ``None``.

    ⚠️ **Keine Zugriffspruefung wie beim Anmelden.** Die Person hat noch keinen
    Zugang zum Server; genau den soll die Einladung ihr verschaffen.
    """
    ziel = _freigabe_ziel(token, provider)
    try:
        eintrag, daten = konten.read_challenge(db, poll_token)
    except konten.KontoFehler as exc:
        raise EinladungsFehler(exc.status_code, {"code": exc.code, "message": exc.message}) from exc
    if daten.get("einladung") != token.id or daten.get("provider") != provider:
        raise EinladungsFehler(
            403,
            meldungen.meldung(
                "mediaserver_challenge_foreign",
                "Dieser Anmeldevorgang gehört nicht zu dieser Einladung.",
            ),
        )

    server = media_server_for_setup(konten.ensure_client_identifier(db, settings), provider)
    try:
        gast_token = await server.poll_login(daten.get("ref", ""), daten.get("code", ""))
        if gast_token is None:
            return None
        konto = await server.account_for_token(gast_token)
    except MediaServerError as exc:
        raise EinladungsFehler(502, exc.als_meldung()) from exc
    eintrag.used_at = utcnow().replace(tzinfo=None)

    if konten.is_blocked(db, konto.provider, konto.account_id):
        db.commit()
        raise EinladungsFehler(
            403,
            meldungen.meldung("mediaserver_blocked", "Für dieses Konto ist der Zugang gesperrt."),
        )
    if konten.find_linked(db, konto) is not None:
        _schon_vergeben(db, token, ziel)
        db.commit()
        raise EinladungsFehler(
            409,
            meldungen.meldung(
                "invite_account_linked_elsewhere",
                f"Dieses {server.label}-Konto gehört schon zu einem Nexview-Konto. Melde dich "
                "damit an oder verknüpfe ein anderes. Der Administrator weiß Bescheid.",
                service=server.label,
            ),
        )

    ziel.konto = konto.account_id
    ziel.konto_name = konto.username
    ziel.konto_email = konto.email
    ziel.token = encrypt(gast_token)
    ziel.fehler = None
    db.commit()
    logger.info("Invitation for %s: %s account linked", token.email, server.label)
    return konto.username


def _schon_vergeben(db: Session, token: AuthToken, ziel: EinladungsServer) -> None:
    """Festhalten und den Administratoren Bescheid geben, einmal je Einladung und Server.

    Entscheidung vom 12.09.2026: Das Onboarding haelt an, die Person bekommt
    einen Hinweis, die Einladung bleibt offen, und auf das vorhandene Konto
    gehen keine Rechte ueber. Wer da wer ist, klaert der Administrator.

    Ohne Titel: Die Meldung geht auch in die Kanaele, und eine Mailadresse hat
    dort nichts verloren. Welche Einladung es ist, zeigt die Liste.
    """
    if (_fehler(ziel) or {}).get("code") == SCHON_VERGEBEN:
        return
    label = _label(ziel.provider)
    ziel.fehler = _als_text(
        meldungen.meldung(
            SCHON_VERGEBEN, "Das Konto gehört schon zu einem Nexview-Konto.", service=label
        )
    )
    notify.create_for_admins(
        db,
        kind=NotificationType.invitation_on_hold,
        message_key="notifications.invitationAccountTaken",
    )
    logger.warning(
        "Invitation for %s: the %s account already belongs to a Nexview account", token.email, label
    )


async def namen_pruefen(
    settings: AppSettings, token: AuthToken, name: str
) -> dict[str, bool | None]:
    """Ist der Name auf jedem Server frei, auf dem ein Konto entstehen soll?

    ``None`` heisst: Der Server hat nicht geantwortet. Entschieden wird dann
    beim Anlegen, das den Namen noch einmal prueft.
    """
    gesucht = name.strip()
    ergebnis: dict[str, bool | None] = {}
    for ziel in token.server:
        klasse = _klasse(ziel)
        if klasse is None or not klasse.legt_konten_an():
            continue
        if ziel.zustand not in (EinladungsServer.OFFEN, EinladungsServer.FERTIG):
            continue
        if ziel.konto:
            # Das angefangene Konto traegt schon einen Namen; frei ist nur genau der.
            ergebnis[ziel.provider] = (ziel.konto_name or "").casefold() == gesucht.casefold()
            continue
        try:
            vergeben = await media_server_for_setup(settings, ziel.provider).name_vergeben(gesucht)
        except MediaServerError:
            ergebnis[ziel.provider] = None
        else:
            ergebnis[ziel.provider] = not vergeben
    return ergebnis


# --------------------------------------------------------------------------
# Einloesen
# --------------------------------------------------------------------------


def vor_dem_anlegen(settings: AppSettings, token: AuthToken, name: str) -> None:
    """Was vor jedem Aufruf an einen Server feststeht. Scheitert es hier, entsteht nirgends etwas."""
    abgleichen(settings, token)
    for ziel in token.server:
        klasse = _klasse(ziel)
        if klasse is None:
            continue
        if klasse.gibt_frei() and ziel.zustand == EinladungsServer.OFFEN and not ziel.konto:
            raise EinladungsFehler(
                409,
                meldungen.meldung(
                    "invite_link_first",
                    f"Verknüpfe zuerst dein {klasse.label}-Konto.",
                    service=klasse.label,
                ),
            )
        if (
            klasse.legt_konten_an()
            and ziel.zustand in (EinladungsServer.OFFEN, EinladungsServer.FERTIG)
            and ziel.konto
            and (ziel.konto_name or "").casefold() != name.casefold()
        ):
            raise EinladungsFehler(
                409,
                meldungen.meldung(
                    "invite_username_fixed",
                    f"Dein Konto auf {klasse.label} heißt schon „{ziel.konto_name}“. "
                    "Nimm bitte denselben Namen.",
                    service=klasse.label,
                    name=ziel.konto_name,
                ),
            )


async def konten_anlegen(
    db: Session, settings: AppSettings, token: AuthToken, name: str, passwort: str
) -> bool:
    """Die Konten auf Servern anlegen, die eigene Konten fuehren. ``False``, wenn eins fehlt.

    ⚠️ **Nach jedem Server wird festgeschrieben.** Ein Konto, das dort steht,
    darf nach einem Absturz nicht vergessen sein, sonst legte der naechste
    Versuch ein zweites an. Das Passwort geht nur an den Server.
    """
    alles = True
    for ziel in token.server:
        klasse = _klasse(ziel)
        if klasse is None or not klasse.legt_konten_an() or ziel.zustand != EinladungsServer.OFFEN:
            continue
        server = media_server_for_setup(settings, ziel.provider)
        try:
            nummer = await server.konto_anlegen(name, passwort, _kennungen(ziel), konto=ziel.konto)
        except MediaServerError as exc:
            alles = False
            angelegt = exc.zahlen.get("konto")
            if angelegt:
                ziel.konto = str(angelegt)
                ziel.konto_name = name
            ziel.fehler = _als_text(exc.als_meldung())
            logger.warning(
                "Invitation for %s: account on %s not finished (%s)",
                token.email,
                klasse.label,
                exc.code or exc.message,
            )
        else:
            ziel.konto = nummer
            ziel.konto_name = name
            ziel.zustand = EinladungsServer.FERTIG
            ziel.fehler = None
            ziel.erledigt_am = utcnow().replace(tzinfo=None)
            logger.info("Invitation for %s: account on %s created", token.email, klasse.label)
        db.commit()
    return alles


def verknuepfen(db: Session, token: AuthToken, benutzer: User) -> list[EinladungsServer]:
    """Die Server-Konten an das neue Nexview-Konto haengen, vor dem Festschreiben.

    Liefert die Freigaben, die ``freigeben`` danach erteilen soll. Bis dahin
    stehen sie auf "fehlt": Stuerzt Nexview dazwischen ab, sieht der
    Administrator sie in der Liste und holt sie nach, statt dass sie still
    offen bleiben.
    """
    freigaben: list[EinladungsServer] = []
    for ziel in token.server:
        klasse = _klasse(ziel)
        if klasse is None or not ziel.konto:
            continue
        if klasse.legt_konten_an() and ziel.zustand != EinladungsServer.FERTIG:
            continue
        if klasse.gibt_frei() and ziel.zustand != EinladungsServer.OFFEN:
            continue
        konto = ExternalAccount(
            provider=ziel.provider,
            account_id=ziel.konto,
            username=ziel.konto_name or "",
            email=ziel.konto_email,
            thumb=None,
        )
        if konten.find_linked(db, konto) is not None:
            # Seit dem Verknuepfen hat ein anderes Nexview-Konto es genommen.
            ziel.zustand = EinladungsServer.FEHLT
            ziel.fehler = _als_text(
                meldungen.meldung(
                    SCHON_VERGEBEN,
                    "Das Konto gehört schon zu einem Nexview-Konto.",
                    service=klasse.label,
                )
            )
            ziel.token = None
            continue
        konten.link(benutzer, konto, ziel.token)
        ziel.token = None
        if klasse.gibt_frei():
            ziel.zustand = EinladungsServer.FEHLT
            ziel.fehler = _als_text(
                meldungen.meldung(
                    "invite_share_pending",
                    f"Die Freigabe auf {klasse.label} steht noch aus.",
                    service=klasse.label,
                )
            )
            freigaben.append(ziel)
    return freigaben


async def freigeben(
    db: Session, settings: AppSettings, freigaben: list[EinladungsServer], benutzer: User
) -> None:
    """Die Freigaben erteilen, nachdem das Nexview-Konto festgeschrieben ist."""
    for ziel in freigaben:
        await _freigabe(settings, ziel, benutzer)
    db.commit()


async def _freigabe(settings: AppSettings, ziel: EinladungsServer, benutzer: User) -> None:
    server = media_server_for_setup(settings, ziel.provider)
    try:
        # Wer schon Zugang hat, behaelt ihn so, wie er ist. Eine bestehende
        # Freigabe wird nicht ueberschrieben.
        if not await server.hat_freigabe(ziel.konto or ""):
            await server.freigeben(ziel.konto_email or ziel.konto_name or "", _kennungen(ziel))
    except MediaServerError as exc:
        ziel.zustand = EinladungsServer.FEHLT
        ziel.fehler = _als_text(exc.als_meldung())
        logger.warning(
            "Invitation for %r: share on %s failed (%s)",
            benutzer.username,
            server.label,
            exc.code or exc.message,
        )
        return
    ziel.zustand = EinladungsServer.FERTIG
    ziel.fehler = None
    ziel.erledigt_am = utcnow().replace(tzinfo=None)
    logger.info("Invitation for %r: share on %s granted", benutzer.username, server.label)

    # Annehmen mit dem Token der Person. Ohne das muesste sie die Einladung erst
    # selbst beim Anbieter bestaetigen; klappt es nicht, bleibt genau das ihr Weg.
    zeile = konten.verknuepfung(benutzer, ziel.provider)
    if zeile is None or not zeile.token:
        return
    try:
        await server.einladung_annehmen(decrypt(zeile.token))
    except MediaServerError as exc:
        logger.warning(
            "Invitation for %r: could not accept the share on %s (%s)",
            benutzer.username,
            server.label,
            exc.code or exc.message,
        )


async def nachholen(db: Session, settings: AppSettings, token: AuthToken, provider: str) -> None:
    """Eine gescheiterte Freigabe noch einmal versuchen. Fuer den Administrator."""
    ziel = next((z for z in token.server if z.provider == provider), None)
    klasse = PROVIDERS.get(provider)
    benutzer = db.get(User, token.redeemed_by) if token.redeemed_by else None
    if (
        ziel is None
        or klasse is None
        or not klasse.gibt_frei()
        or ziel.zustand != EinladungsServer.FEHLT
        or not ziel.konto
        or benutzer is None
    ):
        raise EinladungsFehler(
            404, meldungen.meldung("invitation_not_found", "Einladung nicht gefunden.")
        )
    if not _verbunden(settings, ziel):
        raise EinladungsFehler(
            409,
            meldungen.meldung(
                "invite_server_gone",
                f"{klasse.label} ist seit dem Einladen nicht mehr verbunden.",
                service=klasse.label,
            ),
        )
    await _freigabe(settings, ziel, benutzer)
    db.commit()
    if ziel.zustand != EinladungsServer.FERTIG:
        raise EinladungsFehler(502, _fehler(ziel) or {})
