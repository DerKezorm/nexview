"""Systembenachrichtigungen: die Ziele der serverseitigen Kanaele verwalten.

Serverseitig heisst: vom Administrator eingerichtet, ein geteiltes Postfach
fuer die ganze Installation. Die persoenlichen Wege (Glocke, E-Mail) stellt
jeder in seinem Profil ein und haben mit diesem Router nichts zu tun.

Manche Dienste haben zwei Ebenen. Bei ntfy traegt die **Instanz** Adresse und
Anmeldung, das **Topic** darunter ist das Postfach; bei Gotify ist die
Application schon beides. Der Bestaetigungscode haengt immer am Postfach - nur
dort kann eine Nachricht ankommen.

Der Ablauf ist bewusst eine Einbahnstrasse: eintragen -> testen -> den Code aus
der Testnachricht eintippen -> speichern. Erst danach steht ein Postfach in der
Datenbank, von dem jemand *gesehen* hat, dass etwas ankommt.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from .. import meldungen
from ..deps import AdminUser, DbSession
from ..models import ChannelKind, ChannelTarget
from ..services import channel_outbox, channel_targets, channel_verify, channels
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/settings/channels", tags=["channels"])

logger = logging.getLogger("nexview.channels")

Kanal = Annotated[Literal["ntfy", "gotify", "email", "telegram", "discord", "webhook", "apprise"], Path()]


class TargetDraft(BaseModel):
    """Die eingetippten Daten eines Ziels.

    Alle Felder aller Dienste und beider Ebenen in einem Modell: Gotify kennt
    kein Topic, ntfy kein Anwendungs-Token, und jede Ebene nimmt sich, was zu
    ihr gehoert. Ein Modell je Dienst und Ebene waere sauberer und braeuchte
    fuer jeden neuen Dienst zwei weitere Klassen samt Verzweigung.
    """

    name: str | None = Field(default=None, max_length=80)
    url: str | None = Field(default=None, max_length=255)
    topic: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=200)
    chat_id: str | None = Field(default=None, max_length=64)
    thread_id: str | None = Field(default=None, max_length=32)
    silent: str | None = Field(default=None, max_length=8)
    auth: str | None = None
    username: str | None = Field(default=None, max_length=255)
    password: str | None = None
    token: str | None = None
    language: str | None = Field(default=None, max_length=5)
    # Worueber dieses Postfach informiert. ``None`` heisst "unveraendert".
    events: dict[str, str] | None = None


class EventsUpdate(BaseModel):
    """Worueber dieses Postfach informiert - Meldung auf Dringlichkeit.

    Was nicht drinsteht, ist aus. Bewusst die ganze Auswahl auf einmal statt
    einzelner Schalter: so gibt es keinen Zwischenzustand, in dem die
    Oberflaeche etwas anderes zeigt als die Datenbank enthaelt.
    """

    events: dict[str, str]


class CodeCheck(BaseModel):
    code: str = Field(min_length=1, max_length=8)


class TestResult(BaseModel):
    ok: bool
    message: str
    # Was der Test nebenbei herausgefunden hat - etwa der Benutzername eines
    # Telegram-Bots. Die Oberflaeche traegt es ins passende Feld ein, damit es
    # niemand abtippen muss.
    values: dict[str, str] | None = None


# Der Code steht im **Titel**, nicht im Text: Auf dem Sperrbildschirm ist von
# einer Benachrichtigung oft nur die erste Zeile zu sehen.
_TESTNACHRICHT = {
    "de": (
        "Nexview-Code: {code}",
        "Diesen Code in Nexview eintragen, dann lässt sich das Ziel speichern.",
    ),
    "en": (
        "Nexview code: {code}",
        "Enter this code in Nexview to save this target.",
    ),
}

# Ohne Code - fuer Ziele, an denen niemand sitzt, der ihn ablesen koennte.
_TESTNACHRICHT_OHNE_CODE = {
    "de": ("Nexview: Testnachricht", "Wenn das ankommt, ist dieses Ziel richtig eingerichtet."),
    "en": ("Nexview: test message", "If this arrives, the target is set up correctly."),
}


def _kind(channel: str) -> ChannelKind:
    return ChannelKind(channel)


def _holen(db: DbSession, channel: str, target_id: int) -> ChannelTarget:
    target = db.get(ChannelTarget, target_id)
    if target is None or target.channel != _kind(channel):
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung(
                "target_unknown",
                "Dieses Ziel gibt es nicht.",
            ),
        )
    return target


def _ist_postfach(kind: ChannelKind, parent_id: int | None) -> bool:
    """Kann an dieser Stelle ueberhaupt eine Nachricht ankommen?

    Bei Gotify ja - die Application ist das Postfach. Bei ntfy erst beim Topic;
    die Instanz darueber ist nur Adresse und Anmeldung.
    """
    return not channels.has_children(kind) or parent_id is not None


def _pruefe_entwurf(payload: TargetDraft) -> None:
    if payload.auth is not None and payload.auth not in ("none", "basic", "token"):
        raise HTTPException(
            status_code=422, detail=meldungen.meldung(
                "auth_mode_invalid",
                "Anmeldung muss 'none', 'basic' oder 'token' sein.",
            )
        )
    if payload.language is not None and payload.language not in ("de", "en"):
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "language_invalid",
                "Sprache muss 'de' oder 'en' sein.",
            ),
        )
    # Bei Discord ist die URL ein Geheimnis und kommt maskiert zurueck -
    # ein Punkte-Wert heisst "unveraendert" und wird nicht geprueft.
    url = (payload.url or "").strip()
    if url and not url.startswith("•") and not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422, detail=meldungen.meldung(
                "url_needs_scheme",
                "Die Adresse muss mit http:// oder https:// beginnen.",
            )
        )


def _pruefe_meldungen(events: dict[str, str]) -> None:
    erlaubt = channel_outbox.GROUPS
    for name, stufe in events.items():
        if name not in erlaubt:
            raise HTTPException(
                status_code=422,
                detail=meldungen.meldung(
                    "channel_event_unknown", f"Unbekannte Meldung: {name}", event=name
                ),
            )
        if stufe not in channels.LEVELS:
            raise HTTPException(
                status_code=422,
                detail=meldungen.meldung(
                    "channel_level_unknown",
                    f"Unbekannte Dringlichkeit: {stufe}",
                    level=stufe,
                ),
            )


@router.get("/{channel}/targets")
def list_targets(channel: Kanal, admin: AdminUser, db: DbSession) -> list[dict[str, object]]:
    """Alle eingerichteten Ziele dieses Dienstes - je eine Kachel, Topics inbegriffen."""
    return [
        channel_targets.als_json(db, target)
        for target in channel_targets.alle(db, _kind(channel))
    ]


@router.post("/{channel}/check", response_model=TestResult)
async def check_instance(
    channel: Kanal,
    payload: TargetDraft,
    admin: AdminUser,
    db: DbSession,
    target_id: int | None = None,
) -> TestResult:
    """Ist die Instanz erreichbar? Ohne Code - dort kann noch nichts ankommen.

    Fuer die obere Ebene bei Diensten mit zwei Ebenen: Ein Tippfehler in der
    Adresse soll sofort auffallen und nicht erst beim ersten Topic.
    """
    kind = _kind(channel)
    _pruefe_entwurf(payload)

    settings = load_settings(db)
    vorhanden = _holen(db, channel, target_id) if target_id is not None else None
    werte = channel_targets.entwurf_werte(
        kind, payload.model_dump(), vorhanden, settings=settings
    )
    # Zum Pruefen genuegt die obere Ebene; das Postfach darunter gibt es
    # hier noch nicht, also wird eines vorgegeben - sonst haelt der Kanal die
    # Angaben fuer unvollstaendig.
    werte = {
        **werte,
        "topic": werte.get("topic") or "nexview",
        "chat_id": werte.get("chat_id") or "0",
    }
    config = channels.build(kind, werte)
    if config is None:
        return TestResult(ok=False, message="Es fehlt noch die Adresse.")

    try:
        gefunden = await channels.check(kind, config)
    except channels.ChannelError as fehler:
        return TestResult(ok=False, message=fehler.message)

    return TestResult(
        ok=True, message=f"{channels.label(kind)} ist erreichbar.", values=gefunden
    )


@router.post("/{channel}/test", response_model=TestResult)
async def test_target(
    channel: Kanal,
    payload: TargetDraft,
    admin: AdminUser,
    db: DbSession,
    target_id: int | None = None,
    parent_id: int | None = None,
) -> TestResult:
    """Eine Testnachricht mit Bestaetigungscode verschicken.

    Geprueft werden die **eingetippten** Werte, nicht die gespeicherten - erst
    testen, dann speichern. Andersherum stuenden Zugangsdaten in der Datenbank,
    von denen niemand weiss, ob sie stimmen.

    ``target_id`` beim Bearbeiten, ``parent_id`` beim Anlegen eines Topics -
    dann kommen Adresse und Anmeldung von der Instanz.
    """
    kind = _kind(channel)
    _pruefe_entwurf(payload)

    settings = load_settings(db)
    vorhanden = _holen(db, channel, target_id) if target_id is not None else None
    eltern = _holen(db, channel, parent_id) if parent_id is not None else None
    werte = channel_targets.entwurf_werte(
        kind, payload.model_dump(), vorhanden, eltern, settings=settings
    )
    config = channels.build(kind, werte)
    if config is None:
        # Bei E-Mail fehlt meist nicht die Adresse, sondern der Mailserver -
        # der steht woanders, und "fehlen noch Angaben" schickte einen dann
        # auf die Suche im falschen Formular.
        if kind is ChannelKind.email and not settings.mail_configured:
            return TestResult(
                ok=False,
                message=(
                    "Es ist noch kein Mailserver eingerichtet. "
                    "Das steht unter Einstellungen → E-Mail."
                ),
            )
        return TestResult(ok=False, message=f"Für {channels.label(kind)} fehlen noch Angaben.")

    # In der Sprache des **Ziels**, nicht in der des Administrators: die
    # Testnachricht soll aussehen wie das, was spaeter dort ankommt.
    if channels.requires_code(kind):
        code = channel_verify.start(admin.id, kind, channels.fingerprint(kind, config))
        vorlage = _TESTNACHRICHT
    else:
        code = ""
        vorlage = _TESTNACHRICHT_OHNE_CODE
    betreff, text = vorlage.get(config.language, vorlage["de"])
    try:
        await channels.send(
            kind,
            config,
            channels.Notice(
                title=betreff.format(code=code),
                body=text,
                click_url=settings.link("/") if settings.public_url else None,
            ),
        )
    except channels.ChannelError as fehler:
        channel_verify.vergessen(admin.id, kind)
        return TestResult(ok=False, message=fehler.message)

    if not channels.requires_code(kind):
        return TestResult(
            ok=True, message="Testnachricht verschickt. Kommt sie an?"
        )

    return TestResult(
        ok=True,
        message=(
            f"Testnachricht an {channels.label(kind)} verschickt. "
            "Bitte den vierstelligen Code eintragen, der darin steht."
        ),
    )


@router.post("/{channel}/confirm", response_model=TestResult)
def confirm_code(
    channel: Kanal, payload: CodeCheck, admin: AdminUser, db: DbSession
) -> TestResult:
    """Den Code aus der Testnachricht pruefen. Erst danach laesst sich speichern."""
    geklappt, begruendung = channel_verify.confirm(admin.id, _kind(channel), payload.code)
    return TestResult(ok=geklappt, message=begruendung)


def _speichern(
    db: DbSession, admin_id: int, kind: ChannelKind, target: ChannelTarget, payload: TargetDraft
) -> ChannelTarget:
    """Die ganze Kachel in einem Zug speichern - Verbindung und Meldungen.

    Ein **Postfach** muss bestaetigt sein. Aber nur die neue Verbindung: Wer an
    Adresse oder Token nichts anfasst, soll nicht jedes Mal eine Testnachricht
    verschicken muessen, bloss um einen Haken umzustellen. Aendert sich etwas
    daran, verfaellt die fruehere Bestaetigung - sie galt fuer ein anderes Ziel.

    Eine **Instanz** ohne Postfach-Eigenschaft (die ntfy-Ebene darueber) wird
    ohne Code gespeichert: Dorthin geht nie eine Nachricht, es gibt also nichts
    zu bestaetigen.
    """
    settings = load_settings(db)
    vorher = channel_targets.config(target, settings) if target.verified else None
    abdruck_vorher = channels.fingerprint(kind, vorher) if vorher is not None else None

    channel_targets.anwenden(target, payload.model_dump())
    if payload.name is not None:
        target.name = payload.name.strip()
    if payload.events is not None:
        _pruefe_meldungen(payload.events)
        target.events = dict(payload.events)

    if not _ist_postfach(kind, target.parent_id):
        # Was die obere Ebene braucht, weiss der Dienst - bei ntfy die
        # Adresse, bei Telegram das Token.
        if any(not getattr(target, feld, "") for feld in channels.parent_required(kind)):
            raise HTTPException(
                status_code=422, detail=meldungen.meldung(
                    "connection_incomplete",
                    "Es fehlen noch Angaben zur Verbindung.",
                )
            )
        db.commit()
        db.refresh(target)
        return target

    config = channel_targets.config(target, settings)
    if config is None:
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "connection_incomplete",
                "Es fehlen noch Angaben zur Verbindung.",
            ),
        )

    abdruck = channels.fingerprint(kind, config)
    if (
        channels.requires_code(kind)
        and abdruck != abdruck_vorher
        and not channel_verify.bestaetigt_fuer(admin_id, kind, abdruck)
    ):
        raise HTTPException(
            status_code=409,
            detail=meldungen.meldung(
                "channel_not_verified",
                "Für diese Verbindung liegt keine bestätigte Testnachricht vor. "
                "Bitte zuerst testen und den Code eintragen.",
            ),
        )

    target.verified = True
    channel_verify.vergessen(admin_id, kind)
    db.commit()
    db.refresh(target)
    return target


def _anlegen(
    db: DbSession,
    admin_id: int,
    kind: ChannelKind,
    payload: TargetDraft,
    parent: ChannelTarget | None,
) -> dict[str, object]:
    _pruefe_entwurf(payload)
    if not (payload.name or "").strip():
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "name_required",
                "Bitte einen Namen vergeben.",
            ),
        )

    target = ChannelTarget(
        channel=kind,
        name=(payload.name or "").strip(),
        events={},
        parent_id=parent.id if parent is not None else None,
    )
    db.add(target)
    db.flush()
    return channel_targets.als_json(db, _speichern(db, admin_id, kind, target, payload))


@router.post("/{channel}/targets", status_code=201)
def create_target(
    channel: Kanal, payload: TargetDraft, admin: AdminUser, db: DbSession
) -> dict[str, object]:
    """Eine Instanz anlegen - bei Gotify zugleich das Postfach."""
    return _anlegen(db, admin.id, _kind(channel), payload, None)


@router.post("/{channel}/targets/{parent_id}/children", status_code=201)
def create_child(
    channel: Kanal, parent_id: int, payload: TargetDraft, admin: AdminUser, db: DbSession
) -> dict[str, object]:
    """Ein Topic innerhalb einer Instanz anlegen."""
    kind = _kind(channel)
    if not channels.has_children(kind):
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "channel_no_second_level",
                f"{channels.label(kind)} kennt keine zweite Ebene.",
                channel=channels.label(kind),
            ),
        )
    eltern = _holen(db, channel, parent_id)
    if eltern.parent_id is not None:
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "topic_nested",
                "Ein Topic kann keine Topics enthalten.",
            ),
        )
    return _anlegen(db, admin.id, kind, payload, eltern)


@router.put("/{channel}/targets/{target_id}")
def update_target(
    channel: Kanal,
    target_id: int,
    payload: TargetDraft,
    admin: AdminUser,
    db: DbSession,
) -> dict[str, object]:
    kind = _kind(channel)
    _pruefe_entwurf(payload)
    target = _holen(db, channel, target_id)
    return channel_targets.als_json(db, _speichern(db, admin.id, kind, target, payload))


@router.put("/{channel}/targets/{target_id}/events")
def update_events(
    channel: Kanal,
    target_id: int,
    payload: EventsUpdate,
    admin: AdminUser,
    db: DbSession,
) -> dict[str, object]:
    """Worueber dieses Postfach informiert. Nur bei bestaetigtem Ziel.

    Haken auf einem ungepruefen Ziel fuellten nur still den Postausgang mit
    Nachrichten, die nirgends ankommen.
    """
    target = _holen(db, channel, target_id)
    if not target.verified:
        raise HTTPException(
            status_code=409,
            detail=meldungen.meldung(
                "target_unconfirmed",
                "Dieses Ziel ist noch nicht bestätigt.",
            ),
        )

    _pruefe_meldungen(payload.events)
    target.events = dict(payload.events)
    db.commit()
    db.refresh(target)
    return channel_targets.als_json(db, target)


@router.post("/{channel}/chats")
async def list_chats_draft(
    channel: Kanal, payload: TargetDraft, admin: AdminUser, db: DbSession
) -> list[dict[str, str]]:
    """Dasselbe fuer einen Bot, den es noch gar nicht gibt.

    Beim Einrichten steht das Token im Formular, nicht in der Datenbank -
    gespeichert wird erst, wenn der Weg belegt ist. Die Abfrage braucht es
    aber schon vorher, sonst muesste man zwischendurch etwas speichern, von
    dem noch niemand weiss, ob es taugt.
    """
    kind = _kind(channel)
    _pruefe_entwurf(payload)
    if not channels.kann_chats_suchen(kind):
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "channel_cannot_list_chats",
                f"{channels.label(kind)} kann das nicht von sich aus.",
                channel=channels.label(kind),
            ),
        )

    settings = load_settings(db)
    werte = channel_targets.entwurf_werte(kind, payload.model_dump(), settings=settings)
    config = channels.build(kind, {**werte, "chat_id": werte.get("chat_id") or "0"})
    if config is None:
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "connection_incomplete",
                "Es fehlen noch Angaben zur Verbindung.",
            ),
        )

    try:
        return await channels.chats(kind, config)
    except channels.ChannelError as fehler:
        raise HTTPException(status_code=502, detail=fehler.message) from fehler


@router.post("/{channel}/targets/{target_id}/chats")
async def list_chats(
    channel: Kanal, target_id: int, admin: AdminUser, db: DbSession
) -> list[dict[str, str]]:
    """Wer hat diesem Bot zuletzt geschrieben?

    Erspart den Umweg ueber einen fremden Bot: Wer dem eigenen Bot /start
    geschickt oder ihn in eine Gruppe geholt hat, steht in dessen offenen
    Nachrichten - und damit auch die Nummer des Chats.
    """
    kind = _kind(channel)
    if not channels.kann_chats_suchen(kind):
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "channel_cannot_list_chats",
                f"{channels.label(kind)} kann das nicht von sich aus.",
                channel=channels.label(kind),
            ),
        )

    target = _holen(db, channel, target_id)
    settings = load_settings(db)
    # Zum Abfragen genuegt die obere Ebene; ein Postfach gibt es hier noch
    # nicht, also wird eines vorgegeben.
    werte = {**channel_targets.werte(target, settings), "chat_id": "0"}
    config = channels.build(kind, werte)
    if config is None:
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "connection_incomplete",
                "Es fehlen noch Angaben zur Verbindung.",
            ),
        )

    try:
        return await channels.chats(kind, config)
    except channels.ChannelError as fehler:
        raise HTTPException(status_code=502, detail=fehler.message) from fehler


class EnabledUpdate(BaseModel):
    enabled: bool


@router.put("/{channel}/targets/{target_id}/enabled")
def update_enabled(
    channel: Kanal,
    target_id: int,
    payload: EnabledUpdate,
    admin: AdminUser,
    db: DbSession,
) -> dict[str, object]:
    """Ein Ziel stilllegen oder wieder in Betrieb nehmen.

    Bewusst ein eigener Endpunkt und nicht Teil des Speicherns: Der Schalter
    sitzt auf der Kachel und soll mit einem Klick wirken - ohne den Umweg ueber
    das Formular und ohne erneute Bestaetigung, denn an der Verbindung aendert
    sich dabei nichts.
    """
    target = _holen(db, channel, target_id)
    target.enabled = payload.enabled
    db.commit()
    db.refresh(target)
    logger.info(
        "Target %s/%s switched %s",
        channels.label(target.channel),
        target.name,
        "on" if target.enabled else "off",
    )
    return channel_targets.als_json(db, target)


@router.delete("/{channel}/targets/{target_id}", status_code=204)
def delete_target(channel: Kanal, target_id: int, admin: AdminUser, db: DbSession) -> None:
    """Ein Ziel entfernen - mit allem, was daran haengt.

    Bei einer ntfy-Instanz gehen ihre Topics mit; ohne Adresse und Anmeldung
    haetten sie kein Gegenueber mehr. Offene Auftraege dorthin ebenso.
    """
    target = _holen(db, channel, target_id)
    logger.info("Target %s/%s removed", channels.label(target.channel), target.name)
    db.delete(target)
    db.commit()
