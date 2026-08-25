"""Anmelden und Verbinden ueber den Media-Server.

Drei Wege fuehren hier zusammen, und sie unterscheiden sich nur darin, was am
Ende mit der gepruefte Identitaet geschieht:

* **Anmelden** (ohne Sitzung) - endet in einer Nexview-Sitzung.
* **Verknuepfen** (mit Sitzung) - haengt die Identitaet an das eigene Konto.
* **Verbinden** (Administrator) - waehlt den Server aus und verknuepft dabei
  gleich das eigene Konto.

Der letzte Punkt ist kein Beiwerk: Weil der Administrator sich beim Einrichten
ohnehin beim Anbieter anmeldet, wird sein Konto sofort mitverknuepft. Sonst
entstuende bei seiner ersten Anmeldung ueber Plex ein *zweites* Konto - mit
einfachen Rechten, weil die Adresse eine andere sein kann.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..crypto import decrypt, encrypt
from ..deps import AdminUser, AdultUser, CurrentUser, DbSession
from ..models import AuthToken, MediaServerBlock, MediaServerConnection, User, utcnow
from ..schemas import TokenPair, UserPublic
from ..security import access_token_expires_in, create_access_token, create_refresh_token
from ..services import anmeldebremse
from ..services import mediaserver_accounts as konten
from ..services import mediaserver_library, settings_service
from ..services.mediaserver import (
    ExternalAccount,
    MediaServer,
    MediaServerError,
    PROVIDERS,
    media_server_for_setup,
    verbundene_anbieter,
)
from ..services.mediaserver_accounts import KontoFehler

router = APIRouter(prefix="/api/auth/mediaserver", tags=["mediaserver"])
admin_router = APIRouter(prefix="/api/admin/mediaserver", tags=["mediaserver"])

logger = logging.getLogger("nexview.mediaserver")


# --------------------------------------------------------------------------
# Schemata
# --------------------------------------------------------------------------


class ChallengeStarted(BaseModel):
    """Was der Browser zum Weitermachen braucht.

    ``poll_token`` ist der Merkzettel; PIN und Code des Anbieters bleiben im
    Backend. ``code`` und ``auth_url`` sind nur zum Anzeigen - falls ein
    Popup-Blocker dazwischenkommt, kann man den Link von Hand oeffnen.
    """

    poll_token: str
    code: str
    auth_url: str


class PollRequest(BaseModel):
    poll_token: str = Field(min_length=1, max_length=200)


class LoginResult(BaseModel):
    """Entweder noch offen, oder fertig mit Sitzung."""

    status: str  # "pending" | "ready"
    tokens: TokenPair | None = None


class LinkResult(BaseModel):
    """Entweder noch offen, oder fertig mit aktualisiertem Profil.

    Bewusst dieselbe Form wie beim Anmelden: "noch nicht bestaetigt" ist kein
    Fehler, sondern ein Zwischenstand - als Fehlerantwort muesste die
    Oberflaeche ihn umstaendlich wieder einfangen.
    """

    status: str  # "pending" | "ready"
    user: UserPublic | None = None


class ServerOption(BaseModel):
    machine_id: str
    name: str
    url: str
    owned: bool


class ServerChoices(BaseModel):
    status: str  # "pending" | "ready"
    servers: list[ServerOption] = []
    # Wie viele Server dem Konto zwar zugaenglich sind, ihm aber nicht gehoeren.
    # Nur zur Erklaerung, falls die Liste leerer ist als erwartet.
    shared_hidden: int = 0


class ConnectResult(BaseModel):
    """Ergebnis der Server-Auswahl.

    ``warning`` steht drin, wenn zwar gespeichert wurde, die Adresse aber unter
    keiner ihrer Varianten geantwortet hat. Fuer die Anmeldung ist das
    unerheblich - die laeuft ueber den Anbieter -, fuer alles Spaetere nicht.
    """

    user: UserPublic
    server_name: str
    server_url: str
    reachable: bool
    warning: str | None = None


class SelectServer(BaseModel):
    poll_token: str = Field(min_length=1, max_length=200)
    machine_id: str = Field(min_length=1, max_length=64)


class ConnectStart(BaseModel):
    """Mit welchem Anbieter soll verbunden werden?

    Die Vorbelegung ist ``plex`` - so bleibt der Aufruf ohne Angabe genau der
    von frueher, und keine bestehende Oberflaeche bricht.
    """

    provider: str = Field(default="plex", min_length=1, max_length=20)


class ConnectPassword(BaseModel):
    """Verbinden mit Adresse, Benutzername und Passwort.

    ⚠️ Das Passwort wird ausschliesslich weitergereicht: an den Anbieter, um
    ein Token zu holen. Es wird nicht gespeichert, nicht protokolliert und
    nicht zurueckgegeben. Gespeichert wird nur das Token - verschluesselt, wie
    jeder andere Zugang auch.
    """

    provider: str = Field(min_length=1, max_length=20)
    url: str = Field(min_length=1, max_length=300)
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class LibraryState(BaseModel):
    """Stand des Bibliotheks-Abgleichs - fuer die Einstellungsseite."""

    count: int
    updated_at: datetime | None = None


class BlockEntry(BaseModel):
    id: int
    provider: str
    account_id: str
    username: str | None = None

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------


def _fehler(exc: KontoFehler) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
    )


def _anbieter_fehler(exc: MediaServerError) -> HTTPException:
    """Ein Aussetzer beim Anbieter ist kein Fehler des Benutzers.

    502 statt 500: Nexview selbst funktioniert, nur der Media-Server oder
    plex.tv antwortet nicht wie erwartet.
    """
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"code": "mediaserver_unreachable", "message": exc.message},
    )


def _code_server(db: DbSession) -> tuple[MediaServer, object]:
    """Der Medienserver, bei dem die Anmeldung ueber einen **Code** laeuft.

    ⚠️ Nicht "der erste verbundene". Der Unterschied ist genau der Fehler, der
    eine Installation mit nur Jellyfin unbrauchbar machte: ``begin_login`` und
    ``poll_login`` sind auf einen Vermittler zugeschnitten (plex.tv zeigt einen
    Code, jemand bestaetigt dort). Jellyfin hat keinen - der Adapter wirft dort
    eine Ausnahme. Wer hier blind den ersten Server nimmt, bekommt genau die.

    Anbieter mit Passwort-Anmeldung laufen ueber ``/login/password``.
    """
    settings = konten.ensure_client_identifier(db, settings_service.load_settings(db))
    anbieter = [
        name
        for name in verbundene_anbieter(settings)
        if name in PROVIDERS and PROVIDERS[name].login_kind == "pin"
    ]
    if not anbieter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "mediaserver_no_code_login",
                "message": "Es ist kein Medienserver mit Code-Anmeldung verbunden.",
            },
        )
    return media_server_for_setup(settings, anbieter[0]), settings


async def _identitaet(
    db: DbSession, server: MediaServer, poll_token: str, *, erwarteter_benutzer: int | None
) -> tuple[AuthToken, dict, object | None]:
    """Nachsehen, ob der Vorgang durch ist - und die Identitaet pruefen.

    Gibt ``(eintrag, daten, konto)`` zurueck; ``konto`` ist ``None``, solange
    beim Anbieter noch nichts bestaetigt wurde.

    Hier sitzt die Zugriffspruefung, und zwar **vor** jeder Aenderung an
    Konten. Ein fremdes Konto darf nicht einmal ein Konto anlegen, geschweige
    denn sich an eines haengen.
    """
    eintrag, daten = konten.read_challenge(db, poll_token)

    # Beim Verknuepfen haengt der Vorgang am angemeldeten Konto - sonst koennte
    # jemand mit einem abgefangenen Merkzettel eine fremde Identitaet an sein
    # eigenes Konto binden.
    if erwarteter_benutzer is not None and eintrag.user_id != erwarteter_benutzer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "mediaserver_challenge_foreign",
                "message": "Dieser Anmeldevorgang gehört nicht zu deinem Konto.",
            },
        )

    try:
        anbieter_token = await server.poll_login(daten.get("ref", ""), daten.get("code", ""))
        if anbieter_token is None:
            return eintrag, daten, None

        if not await server.user_has_server_access(anbieter_token):
            raise KontoFehler(
                "mediaserver_no_access",
                "Dieses Konto hat keinen Zugriff auf die Bibliothek. "
                "Bitte den Administrator um Freigabe.",
            )
        konto = await server.account_for_token(anbieter_token)
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    daten["token"] = encrypt(anbieter_token)
    return eintrag, daten, konto


# --------------------------------------------------------------------------
# Anmelden
# --------------------------------------------------------------------------


@router.post("/login/start", response_model=ChallengeStarted)
async def login_start(db: DbSession) -> ChallengeStarted:
    server, settings = _code_server(db)
    try:
        challenge = await server.begin_login()
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc

    poll_token = konten.start_challenge(db, settings.mediaserver_provider, challenge)
    return ChallengeStarted(
        poll_token=poll_token, code=challenge.code, auth_url=challenge.auth_url
    )


class PasswortAnmeldung(BaseModel):
    """Anmelden bei einem Anbieter ohne Vermittler.

    ⚠️ Das Passwort wird nur weitergereicht - an den Medienserver, um ein
    Token zu holen. Es wird nicht gespeichert, nicht protokolliert und nie
    zurueckgegeben.
    """

    provider: str = Field(min_length=1, max_length=20)
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


async def _passwort_identitaet(
    db: DbSession, payload: PasswortAnmeldung, request: Request
) -> tuple[MediaServer, str, ExternalAccount]:
    """Anmelden und die Identitaet pruefen - vor jeder Aenderung an Konten.

    Das Gegenstueck zu ``_identitaet`` fuer Anbieter ohne Code-Ablauf. Die
    Pruefungen sind bewusst dieselben und in derselben Reihenfolge: erst
    anmelden, dann Zugriff pruefen, dann erst wird irgendetwas geschrieben.

    ⚠️ **Die Bremse sitzt hier und nicht in den beiden Routen darueber**, weil
    beide durch diese Funktion muessen - und weil eine vergessene dritte Route
    sonst eine offene Tuer waere. Sie ist an dieser Stelle wichtiger als bei
    der Nexview-Anmeldung: Was hier durchgereicht wird, ist das Passwort des
    **Medienservers**, und ``/login/password`` braucht kein Nexview-Konto.
    Ohne Bremse waere Nexview ein bequemer Durchreiche-Dienst zum Raten gegen
    Plex, Jellyfin oder Emby - mit fremder Absenderadresse obendrein.
    """
    bremse = anmeldebremse.torwaechter(
        request, f"medienserver:{payload.provider}", payload.username
    )
    settings = konten.ensure_client_identifier(db, settings_service.load_settings(db))

    # Nur ein Anbieter, der auch wirklich verbunden ist. Sonst waere das ein
    # Weg, sich gegen einen beliebigen fremden Server anzumelden.
    if payload.provider not in verbundene_anbieter(settings):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "mediaserver_provider_not_linked",
                "message": "Dieser Medienserver ist nicht verbunden.",
            },
        )
    klasse = PROVIDERS.get(payload.provider)
    if klasse is None or not klasse.supports_password_login():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "mediaserver_password_unsupported",
                "message": "Bei diesem Medienserver läuft die Anmeldung anders.",
            },
        )

    server = media_server_for_setup(settings, payload.provider)
    try:
        # Je Konto eine eigene Geraete-Kennung - siehe ``zweck="server"`` beim
        # Verbinden. Zwei Menschen duerfen sich nicht gegenseitig aussperren.
        anbieter_token, konto, _ist_admin = await server.login_with_password(
            payload.username, payload.password, zweck=f"user:{payload.username}"
        )
    except MediaServerError as exc:
        # ⚠️ Ein abgelehntes Passwort ist **kein** Aussetzer des Servers.
        # ``_anbieter_fehler`` macht aus jedem Anbieterfehler ein 502
        # "nicht erreichbar" - beim Code-Ablauf stimmt das, denn dort gibt es
        # nichts falsch zu tippen. Hier hiesse es, jemandem mit vertipptem
        # Passwort zu sagen, sein Server sei kaputt.
        if exc.status_code in (400, 401, 403):
            # Nur das zaehlt als Fehlversuch. Ein Server, der gerade nicht
            # erreichbar ist, darf niemanden aussperren - sonst sperrt ein
            # Neustart des Medienservers den halben Haushalt aus.
            anmeldebremse.gescheitert(bremse)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "mediaserver_bad_credentials",
                    # Bewusst ohne Angabe, *was* nicht stimmte: Sonst liesse
                    # sich hier durchprobieren, welche Konten es gibt.
                    "message": "Benutzername oder Passwort stimmt nicht.",
                },
            ) from exc
        raise _anbieter_fehler(exc) from exc

    # Das Passwort stimmt. Was danach noch schiefgehen kann - kein Zugriff auf
    # die Bibliothek, gesperrtes Konto - ist kein Rateversuch.
    anmeldebremse.geklappt(bremse)

    try:
        # Bei Jellyfin beantwortet sich das mit dem Token selbst - die Pruefung
        # steht trotzdem hier, weil sie fuer jeden Anbieter gelten muss, der
        # diesen Weg spaeter benutzt.
        if not await server.user_has_server_access(anbieter_token):
            raise KontoFehler(
                "mediaserver_no_access",
                "Dieses Konto hat keinen Zugriff auf die Bibliothek. "
                "Bitte den Administrator um Freigabe.",
            )
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    return server, anbieter_token, konto


@router.post("/login/password", response_model=LoginResult)
async def login_password(
    payload: PasswortAnmeldung, request: Request, db: DbSession
) -> LoginResult:
    """Mit Benutzername und Passwort des Medienservers bei Nexview anmelden."""
    settings = settings_service.load_settings(db)
    _server, anbieter_token, konto = await _passwort_identitaet(db, payload, request)

    try:
        # Dieselbe Stelle wie beim Code-Weg: Hier sitzen die Sperrliste, das
        # automatische Anlegen und die Zuordnung zu einer offenen Einladung.
        benutzer = konten.resolve(db, settings, konto)
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    konten.link(benutzer, konto, encrypt(anbieter_token))
    benutzer.last_login_at = utcnow()
    db.commit()

    return LoginResult(
        status="ready",
        tokens=TokenPair(
            access_token=create_access_token(benutzer.id),
            refresh_token=create_refresh_token(benutzer.id),
            # Ohne diese Zeile weiss der Browser nicht, wann er erneuern muss -
            # und Pydantic laesst das Paar gar nicht erst entstehen.
            expires_in=access_token_expires_in(),
        ),
    )


@router.post("/link/password", response_model=LinkResult)
async def link_password(
    payload: PasswortAnmeldung, request: Request, db: DbSession, user: AdultUser
) -> LinkResult:
    """Ein Medienserver-Konto an das eigene, bereits angemeldete Konto haengen."""
    _server, anbieter_token, konto = await _passwort_identitaet(db, payload, request)

    if konten.is_blocked(db, konto.provider, konto.account_id):
        raise _fehler(
            KontoFehler("mediaserver_blocked", "Für dieses Konto ist der Zugang gesperrt.")
        )

    fremd = konten.find_linked(db, konto)
    if fremd is not None and fremd.id != user.id:
        raise _fehler(
            KontoFehler(
                "mediaserver_link_conflict",
                "Dieses Konto des Media-Servers ist bereits mit einem anderen "
                "Nexview-Konto verbunden.",
                status_code=409,
            )
        )

    konten.link(user, konto, encrypt(anbieter_token))
    db.commit()
    db.refresh(user)
    return LinkResult(status="ready", user=UserPublic.model_validate(user))


@router.post("/login/poll", response_model=LoginResult)
async def login_poll(payload: PollRequest, db: DbSession) -> LoginResult:
    server, settings = _code_server(db)
    try:
        eintrag, daten, konto = await _identitaet(
            db, server, payload.poll_token, erwarteter_benutzer=None
        )
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    if konto is None:
        return LoginResult(status="pending")

    try:
        benutzer = konten.resolve(db, settings, konto)
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    # Das Token gehoert zur Verknuepfung, nicht zu einer einzelnen Funktion:
    # Wer sich hier anmeldet, hat gerade zugestimmt - eine zweite Anmeldung
    # spaeter fuer die Merkliste waere reine Schikane. Nebenbei erneuert sich
    # ein abgelaufenes Token damit von selbst.
    konten.merke_token(benutzer, daten.get("token"))
    eintrag.used_at = utcnow().replace(tzinfo=None)
    benutzer.last_login_at = utcnow()
    db.commit()

    return LoginResult(
        status="ready",
        tokens=TokenPair(
            access_token=create_access_token(benutzer.id),
            refresh_token=create_refresh_token(benutzer.id),
            expires_in=access_token_expires_in(),
        ),
    )


# --------------------------------------------------------------------------
# Verknuepfen (eigenes Profil)
# --------------------------------------------------------------------------


@router.post("/link/start", response_model=ChallengeStarted)
async def link_start(db: DbSession, user: AdultUser) -> ChallengeStarted:
    server, settings = _code_server(db)
    try:
        challenge = await server.begin_login()
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc

    poll_token = konten.start_challenge(
        db, settings.mediaserver_provider, challenge, user=user
    )
    return ChallengeStarted(
        poll_token=poll_token, code=challenge.code, auth_url=challenge.auth_url
    )


@router.post("/link/poll", response_model=LinkResult)
async def link_poll(payload: PollRequest, db: DbSession, user: AdultUser) -> LinkResult:
    server, _ = _code_server(db)
    try:
        eintrag, daten, konto = await _identitaet(
            db, server, payload.poll_token, erwarteter_benutzer=user.id
        )
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    if konto is None:
        return LinkResult(status="pending")

    if konten.is_blocked(db, konto.provider, konto.account_id):
        raise _fehler(
            KontoFehler("mediaserver_blocked", "Für dieses Konto ist der Zugang gesperrt.")
        )

    fremd = konten.find_linked(db, konto)
    if fremd is not None and fremd.id != user.id:
        raise _fehler(
            KontoFehler(
                "mediaserver_link_conflict",
                "Dieses Konto des Media-Servers ist bereits mit einem anderen "
                "Nexview-Konto verbunden.",
                status_code=409,
            )
        )

    konten.link(user, konto, daten.get("token"))
    eintrag.used_at = utcnow().replace(tzinfo=None)
    db.commit()
    db.refresh(user)
    return LinkResult(status="ready", user=UserPublic.model_validate(user))


@router.delete("/link", response_model=UserPublic)
def link_delete(db: DbSession, user: AdultUser, provider: str | None = None) -> User:
    """Die eigene Verknuepfung loesen - eine, oder alle.

    ``provider`` loest genau diesen Anbieter. Ohne Angabe fallen alle, wie
    frueher, als es nur einen geben konnte.
    """
    try:
        konten.unlink(user, provider)
    except KontoFehler as exc:
        raise _fehler(exc) from exc
    db.commit()
    db.refresh(user)
    return user


# --------------------------------------------------------------------------
# Verbinden (Administrator)
# --------------------------------------------------------------------------


@admin_router.post("/connect/start", response_model=ChallengeStarted)
async def connect_start(
    db: DbSession, admin: AdminUser, payload: ConnectStart | None = None
) -> ChallengeStarted:
    """Anmeldung beim Anbieter starten - noch ohne ausgewaehlten Server."""
    anbieter = (payload.provider if payload else "plex").strip().lower()
    if anbieter not in PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "mediaserver_unknown_provider",
                "message": f"Diesen Medienserver kennt Nexview nicht: {anbieter}",
            },
        )

    settings = konten.ensure_client_identifier(db, settings_service.load_settings(db))
    try:
        server = media_server_for_setup(settings, anbieter)
        challenge = await server.begin_login()
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc

    # Der Anbieter wandert in den Vermerk - die beiden folgenden Schritte holen
    # ihn von dort, statt ihn noch einmal zu raten. Gespeichert wurde er hier
    # schon immer; gelesen hat ihn nur nie jemand.
    poll_token = konten.start_challenge(db, anbieter, challenge, user=admin)
    return ChallengeStarted(
        poll_token=poll_token, code=challenge.code, auth_url=challenge.auth_url
    )


@admin_router.post("/connect/poll", response_model=ServerChoices)
async def connect_poll(payload: PollRequest, db: DbSession, admin: AdminUser) -> ServerChoices:
    """Nachsehen und - sobald bestaetigt - die Server zur Auswahl anbieten.

    Der Vorgang bleibt danach absichtlich offen: Das Token des Anbieters wird
    im Vermerk hinterlegt, damit es fuer die Auswahl nicht durch den Browser
    laufen muss.
    """
    settings = konten.ensure_client_identifier(db, settings_service.load_settings(db))
    try:
        eintrag, daten = konten.read_challenge(db, payload.poll_token)
        server = media_server_for_setup(settings, daten.get("provider", "plex"))
        if eintrag.user_id != admin.id:
            raise KontoFehler(
                "mediaserver_challenge_foreign",
                "Dieser Vorgang gehört nicht zu deinem Konto.",
            )

        anbieter_token = await server.poll_login(daten.get("ref", ""), daten.get("code", ""))
        if anbieter_token is None:
            return ServerChoices(status="pending")

        auswahl = await server.list_servers(anbieter_token)
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    daten["token"] = encrypt(anbieter_token)
    eintrag.mediaserver_ref = json.dumps(daten)
    db.commit()

    # **Nur eigene Server.** In der Ressourcenliste stehen auch solche, auf die
    # nur geteilt wurde - ein Freundes-Server also, den man selbst nur ansieht.
    # Den zu waehlen waere fast immer ein Fehlgriff, der erst spaet auffiele:
    # Die Zugriffspruefung haenge dann am falschen Personenkreis, und fremde
    # Wiedergabe-Daten darf ohnehin nur der Eigentuemer lesen.
    eigene = [kandidat for kandidat in auswahl if kandidat.owned]

    return ServerChoices(
        status="ready",
        servers=[
            ServerOption(
                machine_id=kandidat.machine_id,
                name=kandidat.name,
                url=kandidat.url,
                owned=kandidat.owned,
            )
            for kandidat in eigene
        ],
        shared_hidden=len(auswahl) - len(eigene),
    )


@admin_router.post("/connect/select", response_model=ConnectResult)
async def connect_select(payload: SelectServer, db: DbSession, admin: AdminUser) -> ConnectResult:
    """Server uebernehmen - und dabei das eigene Konto verknuepfen."""
    settings = konten.ensure_client_identifier(db, settings_service.load_settings(db))
    try:
        eintrag, daten = konten.read_challenge(db, payload.poll_token)
        if eintrag.user_id != admin.id:
            raise KontoFehler(
                "mediaserver_challenge_foreign",
                "Dieser Vorgang gehört nicht zu deinem Konto.",
            )

        anbieter_token = decrypt(daten.get("token", ""))
        if not anbieter_token:
            raise KontoFehler(
                "mediaserver_challenge_expired",
                "Der Vorgang ist abgelaufen. Bitte erneut verbinden.",
                status_code=410,
            )

        server = media_server_for_setup(settings, daten.get("provider", "plex"))
        auswahl = await server.list_servers(anbieter_token)
        # Die Pruefung auf "eigener Server" gehoert hierher und nicht nur in die
        # Anzeige: Sonst liesse sich eine fremde Kennung einfach direkt schicken.
        gewaehlt = next(
            (k for k in auswahl if k.machine_id == payload.machine_id and k.owned), None
        )
        if gewaehlt is None:
            raise KontoFehler(
                "mediaserver_server_unknown",
                "Dieser Server steht für dein Konto nicht zur Verfügung.",
                status_code=404,
            )

        # Die erste Adresse, unter der der Server tatsaechlich antwortet. Die
        # lokale steht vorn, ist aus einem abgeschotteten Netz heraus aber nicht
        # immer erreichbar - stillschweigend die falsche zu speichern faellt
        # erst Monate spaeter auf.
        erreichbar = ""
        for adresse in gewaehlt.urls or ((gewaehlt.url,) if gewaehlt.url else ()):
            if await server.probe(adresse, anbieter_token):
                erreichbar = adresse
                break

        konto = await server.account_for_token(anbieter_token)
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    logger.info(
        "Saving Plex connection: server %r (machine_id %s...), url %r, reachable=%s",
        gewaehlt.name,
        gewaehlt.machine_id[:12],
        erreichbar or gewaehlt.url,
        bool(erreichbar),
    )
    # Eine Zeile je Server statt fuenf flacher Einstellungswerte. Denselben
    # Server zweimal zu verbinden ergaebe zwei Stimmen fuer dieselbe
    # Bibliothek - deshalb wird eine bestehende Zeile aktualisiert statt eine
    # zweite angelegt. Die Adresse aendert sich dabei durchaus: dieselbe
    # Installation ist mal lokal und mal ueber eine Fremdadresse erreichbar.
    vorhanden = db.scalar(
        select(MediaServerConnection).where(
            MediaServerConnection.provider == server.provider,
            MediaServerConnection.machine_id == gewaehlt.machine_id,
        )
    )
    if vorhanden is None:
        vorhanden = MediaServerConnection(
            provider=server.provider, machine_id=gewaehlt.machine_id
        )
        db.add(vorhanden)
    vorhanden.name = gewaehlt.name
    vorhanden.url = erreichbar or gewaehlt.url
    vorhanden.token = encrypt(anbieter_token)
    # Wem das Token gehoert. Emby kennt kein "/Users/Me", also muss es hier
    # stehen - siehe ``MediaServerConnection.account_id``.
    vorhanden.account_id = konto.account_id or ""
    db.commit()

    # Kontrolllesen mit frischer Sitzung: Steht die Verbindung wirklich in der
    # Datenbank, und ist das Token mit dem aktuellen Schluessel lesbar? Ein
    # "Verbunden!"-Haekchen, hinter dem nichts gespeichert wurde, war exakt
    # das gemeldete Raetsel - ab jetzt stuende die Diskrepanz hier im Log.
    kontrolle = settings_service.load_settings(db)
    if kontrolle.mediaserver_configured:
        logger.info("Plex connection saved and read back: fine")
    else:
        logger.error(
            "Plex connection was saved but is incomplete when read back: "
            "provider=%r machine_id=%s token=%s - this is the moment where the "
            "connection 'disappears'.",
            kontrolle.mediaserver_provider,
            "present" if kontrolle.mediaserver_machine_id else "MISSING",
            "readable" if kontrolle.mediaserver_token else "MISSING/UNREADABLE",
        )

    # Das eigene Konto gleich mitverknuepfen - sofern die Identitaet nicht
    # schon an einem anderen Konto haengt.
    fremd = konten.find_linked(db, konto)
    if fremd is None or fremd.id == admin.id:
        # Eine Anmeldung genuegt fuer alles Weitere. Das Token in der
        # Verbindungstabelle ist der Zugang **des Servers**, dieses hier der
        # persoenliche des Administrators - zwei Dinge, die zufaellig denselben
        # Ursprung haben.
        konten.link(admin, konto, encrypt(anbieter_token))

    eintrag.used_at = utcnow().replace(tzinfo=None)
    db.commit()
    db.refresh(admin)

    # Gleich einmal die Bibliothek lesen. Sonst passierte bis zum naechsten
    # Hintergrunddurchlauf eine Stunde lang nichts, und der Administrator
    # koennte nicht erkennen, ob es ueberhaupt funktioniert. Ein Fehlschlag
    # ist hier kein Grund, das Verbinden scheitern zu lassen.
    try:
        await mediaserver_library.refresh(db, settings_service.load_settings(db))
    except MediaServerError as exc:
        logger.warning("Library not readable after connecting: %s", exc.message)

    return ConnectResult(
        user=UserPublic.model_validate(admin),
        server_name=gewaehlt.name,
        server_url=erreichbar or gewaehlt.url,
        reachable=bool(erreichbar),
        warning=(
            None
            if erreichbar
            else (
                "Der Server ist unter keiner seiner Adressen erreichbar. "
                "Die Anmeldung funktioniert trotzdem – sie läuft über Plex."
            )
        ),
    )


@admin_router.post("/connect/password", response_model=ConnectResult)
async def connect_password(
    payload: ConnectPassword, db: DbSession, admin: AdminUser
) -> ConnectResult:
    """Einen Server verbinden, der kein Vermittler-Verfahren kennt.

    Der Gegenstueck-Ablauf zu ``connect/start`` + ``poll`` + ``select``: Dort
    bestaetigt jemand bei plex.tv und Nexview fragt nach, hier gibt es nichts
    zu bestaetigen - Adresse, Benutzername und Passwort kommen in einem Zug.

    Die Reihenfolge der Pruefungen ist Absicht. Erst wird angemeldet, dann die
    Verwaltungsberechtigung geprueft, dann der Server befragt - und **erst
    danach** etwas gespeichert. Bricht es unterwegs ab, steht keine halbe
    Verbindung in der Datenbank.
    """
    settings = konten.ensure_client_identifier(db, settings_service.load_settings(db))

    if payload.provider not in PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "mediaserver_provider_unknown",
                "message": "Diesen Medienserver kennt Nexview nicht.",
            },
        )
    klasse = PROVIDERS[payload.provider]
    if not klasse.supports_password_login():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "mediaserver_password_unsupported",
                "message": f"{klasse.label} wird nicht mit Benutzername und Passwort verbunden.",
            },
        )

    adresse = payload.url.strip().rstrip("/")
    server = media_server_for_setup(settings, payload.provider, adresse)
    try:
        # ⚠️ ``zweck="server"`` trennt diese Anmeldung von den persoenlichen.
        # Jellyfin fuehrt Zugaenge je *Geraet*: Ohne die Trennung loeschte die
        # persoenliche Anmeldung desselben Menschen diesen Server-Zugang - und
        # der Bibliotheks-Abgleich stand still. Genau so passiert.
        anbieter_token, konto, ist_admin = await server.login_with_password(
            payload.username, payload.password, adresse, zweck="server"
        )

        # ⚠️ Ohne Verwaltungsrechte geht es nicht weiter, und zwar *hier* und
        # nicht spaeter: Nexview muss die Konten des Servers lesen koennen, um
        # zu wissen, wer hereindarf. Ein normales Konto darf das nicht - die
        # Verbindung stuende dann da und koennte ihre Hauptaufgabe nicht
        # erfuellen. Jellyseerr/Seerr pruefen an derselben Stelle dasselbe.
        if not ist_admin:
            raise KontoFehler(
                "mediaserver_not_admin",
                f"Dieses Konto ist auf dem {klasse.label}-Server kein Administrator. "
                "Zum Verbinden wird ein Administratorkonto gebraucht.",
                status_code=403,
            )

        auswahl = await server.list_servers(anbieter_token)
        if not auswahl:
            raise KontoFehler(
                "mediaserver_server_unknown",
                "Unter dieser Adresse meldet sich kein Server.",
                status_code=404,
            )
        gewaehlt = auswahl[0]
        erreichbar = adresse if await server.probe(adresse, anbieter_token) else ""
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc
    except KontoFehler as exc:
        raise _fehler(exc) from exc

    logger.info(
        "Saving %s connection: server %r (machine_id %s...), url %r, reachable=%s",
        payload.provider,
        gewaehlt.name,
        gewaehlt.machine_id[:12],
        adresse,
        bool(erreichbar),
    )
    vorhanden = db.scalar(
        select(MediaServerConnection).where(
            MediaServerConnection.provider == server.provider,
            MediaServerConnection.machine_id == gewaehlt.machine_id,
        )
    )
    if vorhanden is None:
        vorhanden = MediaServerConnection(
            provider=server.provider, machine_id=gewaehlt.machine_id
        )
        db.add(vorhanden)
    vorhanden.name = gewaehlt.name
    vorhanden.url = adresse
    vorhanden.token = encrypt(anbieter_token)
    vorhanden.account_id = konto.account_id or ""
    db.commit()

    # Das eigene Konto gleich mitverknuepfen - wie bei ``connect/select``.
    fremd = konten.find_linked(db, konto)
    if fremd is None or fremd.id == admin.id:
        konten.link(admin, konto, encrypt(anbieter_token))
        db.commit()
        db.refresh(admin)

    try:
        await mediaserver_library.refresh(db, settings_service.load_settings(db))
    except MediaServerError as exc:
        logger.warning("Library not readable after connecting: %s", exc.message)

    return ConnectResult(
        user=UserPublic.model_validate(admin),
        server_name=gewaehlt.name,
        server_url=adresse,
        reachable=bool(erreichbar),
        warning=(
            None
            if erreichbar
            else (
                "Der Server ist unter dieser Adresse gerade nicht erreichbar. "
                "Gespeichert wurde die Verbindung trotzdem."
            )
        ),
    )


class BetroffenesKonto(BaseModel):
    """Ein Konto, das ein Trennen aussperren wuerde."""

    id: int
    username: str
    display_name: str | None


class TrennFolgen(BaseModel):
    """Was ein Trennen der Verbindung anrichten wuerde.

    Bewusst **immer** abrufbar, auch wenn niemand gefaehrdet ist: Ein Hinweis,
    der nur im Ernstfall erscheint, wird beim ersten Mal nicht gelesen, weil
    man ihn nicht kennt. Einer, der jedes Mal kommt und meistens Entwarnung
    gibt, wird gelesen.
    """

    verknuepft: int
    gefaehrdet: list[BetroffenesKonto]


def _trenn_folgen(db: DbSession, provider: str | None = None) -> TrennFolgen:
    """Wen trifft es, wenn diese Verbindung faellt?

    ``provider`` grenzt auf einen Anbieter ein. Das ist im Parallelbetrieb der
    entscheidende Unterschied: Wer sein Konto sowohl mit Plex als auch mit
    Jellyfin verknuepft hat, kommt nach dem Trennen des einen weiterhin ueber
    das andere herein - er gehoert also nicht auf die Warnliste.
    """
    konten_ = konten.verknuepfte_konten(db, provider)
    return TrennFolgen(
        verknuepft=len(konten_),
        gefaehrdet=[
            BetroffenesKonto(
                id=user.id, username=user.username, display_name=user.display_name
            )
            for user in konten_
            if konten.kaeme_nicht_mehr_herein(user, ohne=provider)
        ],
    )


@admin_router.get("/connection/folgen", response_model=TrennFolgen)
def connect_impact(
    db: DbSession, admin: AdminUser, provider: str | None = None
) -> TrennFolgen:
    """Wen ein Trennen treffen wuerde - **vor** dem Klick, nicht danach."""
    return _trenn_folgen(db, provider)


@admin_router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
def connect_delete(
    db: DbSession,
    admin: AdminUser,
    provider: str | None = None,
    bestaetigt: bool = False,
) -> None:
    """Verbindung loesen.

    Die Verknuepfungen der Benutzer bleiben bestehen - wer den Server spaeter
    wieder verbindet, findet alles vor.

    ⚠️ **Der Docstring stand hier einmal auf "Anmelden kann sich in der
    Zwischenzeit niemand darueber, weil ohne Server auch nichts zu pruefen
    ist" - als waere das harmlos.** Fuer Konten, die nur ueber den Medienserver
    hereinkamen und kein Passwort haben, ist es das Gegenteil: Ihr einziger Weg
    ist zu. Beim Loesen der *eigenen* Verknuepfung faengt ``unlink`` genau das
    ab; hier fehlte die Pruefung.

    Deshalb jetzt: Sind Konten gefaehrdet, wird mit 409 abgelehnt und die Liste
    mitgeliefert. Der Administrator kann das mit ``bestaetigt=true``
    ueberstimmen - anders als der einzelne Nutzer kann er den Schaden ja
    hinterher beheben, indem er Passwoerter setzt. Die Sperre sitzt bewusst
    hier und nicht nur im Bestaetigungsdialog: Ein Dialog schuetzt nur den Weg,
    der durch ihn hindurchfuehrt.
    """
    folgen = _trenn_folgen(db, provider)
    if folgen.gefaehrdet and not bestaetigt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "mediaserver_would_lock_out_others",
                "message": (
                    f"{len(folgen.gefaehrdet)} Konten haben kein eigenes Passwort und "
                    "kämen nach dem Trennen nicht mehr herein."
                ),
                "gefaehrdet": [konto.model_dump() for konto in folgen.gefaehrdet],
            },
        )

    if folgen.gefaehrdet:
        # Ausdruecklich als Warnung: Hier hat jemand eine Sperre ueberstimmt,
        # und das soll im Protokoll stehen, nicht nur im Gedaechtnis.
        logger.warning(
            "Media server disconnected by %r although %d account(s) lose their only "
            "way in: %s",
            admin.username,
            len(folgen.gefaehrdet),
            ", ".join(konto.username for konto in folgen.gefaehrdet),
        )

    # Die Zeile geht, die Verknuepfungen der Nutzer bleiben - siehe oben.
    #
    # ``provider`` trennt **einen** Server; ohne Angabe fallen alle. Der
    # Unterschied ist kein Feinschliff: Mit zwei verbundenen Servern loeschte
    # ein Klick auf "Jellyfin trennen" sonst auch die Plex-Verbindung.
    abfrage = select(MediaServerConnection)
    if provider is not None:
        abfrage = abfrage.where(MediaServerConnection.provider == provider)
    for zeile in db.scalars(abfrage):
        db.delete(zeile)
    # ⚠️ Ausdruecklich speichern. Vorher endete diese Funktion immer mit
    # ``save_settings``, und das committet nebenbei mit. Seit das Leeren der
    # Altwerte in einer Bedingung steht, gibt es diesen Nebeneffekt nicht mehr:
    # Blieb noch eine Verbindung uebrig, lief gar kein Commit - die Zeile war
    # nach dem Trennen wieder da. Der Test hat es gefangen.
    db.commit()

    # Die alten Einstellungswerte nur leeren, wenn wirklich **nichts** mehr
    # steht. Auf einer Installation, bei der die Wanderung in die Tabelle noch
    # nicht lief, stuenden sie sonst weiterhin da, und der Rueckfall in
    # ``_verbindungen_lesen`` haette die gerade getrennte Verbindung sofort
    # wieder hervorgeholt. Bleibt dagegen noch eine Verbindung, waere das
    # Leeren falsch - dann gehoerten die Werte ja der anderen.
    if not db.scalar(select(func.count()).select_from(MediaServerConnection)):
        settings_service.save_settings(
            db,
            {
                "mediaserver_provider": "",
                "mediaserver_machine_id": "",
                "mediaserver_name": "",
                "mediaserver_url": "",
            },
        )
        settings_service.clear_secret(db, "mediaserver_token")

    # Mit Namen: Sollte eine Verbindung je "von selbst" verschwinden, zeigt
    # diese Zeile, ob doch jemand den Trennen-Knopf gedrueckt hat.
    logger.info(
        "Media server %s disconnected by %r", provider or "(all)", admin.username
    )


# --------------------------------------------------------------------------
# Sperrliste
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Bibliotheks-Abgleich
# --------------------------------------------------------------------------


@admin_router.get("/library", response_model=LibraryState)
def library_state(
    db: DbSession, admin: AdminUser, provider: str | None = None
) -> LibraryState:
    """Stand des Abgleichs - je Anbieter, wenn einer genannt wird.

    Die Karte steht auf der Seite *eines* Servers. Ohne ``provider`` stuende
    dort die Gesamtzahl ueber alle Server, und zwar auf jeder Seite dieselbe.
    """
    return LibraryState(**mediaserver_library.stand(db, provider))  # type: ignore[arg-type]


@admin_router.post("/library/refresh", response_model=LibraryState)
async def library_refresh(
    db: DbSession, admin: AdminUser, provider: str | None = None
) -> LibraryState:
    """Von Hand abgleichen.

    Im Betrieb passiert das im Hintergrund. Der Knopf ist fuer den Moment
    direkt nach dem Verbinden - und um ueberhaupt sehen zu koennen, dass es
    funktioniert.

    ``provider`` gleicht nur diesen einen Server ab - das ist der Knopf auf
    dessen Seite. Ohne Angabe laufen alle.
    """
    settings = settings_service.load_settings(db)
    if not settings.mediaserver_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "mediaserver_none_linked",
                "message": "Es ist kein Media-Server verbunden.",
            },
        )
    try:
        # streng: Wer den Knopf drueckt, will die Wahrheit - auch die
        # unangenehme. Der stille Rueckzug ist nur fuer den Hintergrund.
        await mediaserver_library.refresh(db, settings, streng=True, provider=provider)
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc
    return LibraryState(**mediaserver_library.stand(db, provider))  # type: ignore[arg-type]


@admin_router.get("/blocks", response_model=list[BlockEntry])
def blocks_list(db: DbSession, admin: AdminUser) -> list[MediaServerBlock]:
    return list(
        db.scalars(select(MediaServerBlock).order_by(MediaServerBlock.blocked_at.desc()))
    )


@admin_router.delete("/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def blocks_delete(block_id: int, db: DbSession, admin: AdminUser) -> None:
    """Sperre aufheben - danach darf sich dieses Konto wieder anmelden."""
    eintrag = db.get(MediaServerBlock, block_id)
    if eintrag is not None:
        db.delete(eintrag)
        db.commit()
