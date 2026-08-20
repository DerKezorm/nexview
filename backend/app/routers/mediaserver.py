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

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..crypto import decrypt, encrypt
from ..deps import AdminUser, CurrentUser, DbSession
from ..models import AuthToken, MediaServerBlock, User, utcnow
from ..schemas import TokenPair, UserPublic
from ..security import access_token_expires_in, create_access_token, create_refresh_token
from ..services import mediaserver_accounts as konten
from ..services import mediaserver_library, settings_service
from ..services.mediaserver import (
    MediaServer,
    MediaServerError,
    get_media_server,
    media_server_for_setup,
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


def _verbundener_server(db: DbSession) -> tuple[MediaServer, object]:
    settings = konten.ensure_client_identifier(db, settings_service.load_settings(db))
    server = get_media_server(settings)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "mediaserver_not_configured",
                "message": "Es ist kein Media-Server verbunden.",
            },
        )
    return server, settings


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
    server, settings = _verbundener_server(db)
    try:
        challenge = await server.begin_login()
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc

    poll_token = konten.start_challenge(db, settings.mediaserver_provider, challenge)
    return ChallengeStarted(
        poll_token=poll_token, code=challenge.code, auth_url=challenge.auth_url
    )


@router.post("/login/poll", response_model=LoginResult)
async def login_poll(payload: PollRequest, db: DbSession) -> LoginResult:
    server, settings = _verbundener_server(db)
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
async def link_start(db: DbSession, user: CurrentUser) -> ChallengeStarted:
    server, settings = _verbundener_server(db)
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
async def link_poll(payload: PollRequest, db: DbSession, user: CurrentUser) -> LinkResult:
    server, _ = _verbundener_server(db)
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

    konten.link(user, konto)
    konten.merke_token(user, daten.get("token"))
    eintrag.used_at = utcnow().replace(tzinfo=None)
    db.commit()
    db.refresh(user)
    return LinkResult(status="ready", user=UserPublic.model_validate(user))


@router.delete("/link", response_model=UserPublic)
def link_delete(db: DbSession, user: CurrentUser) -> User:
    try:
        konten.unlink(user)
    except KontoFehler as exc:
        raise _fehler(exc) from exc
    db.commit()
    db.refresh(user)
    return user


# --------------------------------------------------------------------------
# Verbinden (Administrator)
# --------------------------------------------------------------------------


@admin_router.post("/connect/start", response_model=ChallengeStarted)
async def connect_start(db: DbSession, admin: AdminUser) -> ChallengeStarted:
    """Anmeldung beim Anbieter starten - noch ohne ausgewaehlten Server."""
    settings = konten.ensure_client_identifier(db, settings_service.load_settings(db))
    try:
        server = media_server_for_setup(settings, "plex")
        challenge = await server.begin_login()
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc

    poll_token = konten.start_challenge(db, "plex", challenge, user=admin)
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
        server = media_server_for_setup(settings, "plex")
        eintrag, daten = konten.read_challenge(db, payload.poll_token)
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

        server = media_server_for_setup(settings, "plex")
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
        "Plex-Verbindung wird gespeichert: Server %r (machine_id %s…), "
        "Adresse %r, erreichbar=%s",
        gewaehlt.name,
        gewaehlt.machine_id[:12],
        erreichbar or gewaehlt.url,
        bool(erreichbar),
    )
    settings_service.save_settings(
        db,
        {
            "mediaserver_provider": "plex",
            "mediaserver_machine_id": gewaehlt.machine_id,
            "mediaserver_name": gewaehlt.name,
            "mediaserver_url": erreichbar or gewaehlt.url,
            "mediaserver_token": anbieter_token,
        },
    )

    # Kontrolllesen mit frischer Sitzung: Steht die Verbindung wirklich in der
    # Datenbank, und ist das Token mit dem aktuellen Schluessel lesbar? Ein
    # "Verbunden!"-Haekchen, hinter dem nichts gespeichert wurde, war exakt
    # das gemeldete Raetsel - ab jetzt stuende die Diskrepanz hier im Log.
    kontrolle = settings_service.load_settings(db)
    if kontrolle.mediaserver_configured:
        logger.info("Plex-Verbindung gespeichert und nachgelesen: in Ordnung")
    else:
        logger.error(
            "Plex-Verbindung wurde gespeichert, ist beim Nachlesen aber "
            "unvollständig: provider=%r machine_id=%s token=%s - das ist der "
            "Moment, in dem die Verbindung 'verschwindet'.",
            kontrolle.mediaserver_provider,
            "da" if kontrolle.mediaserver_machine_id else "FEHLT",
            "lesbar" if kontrolle.mediaserver_token else "FEHLT/UNLESBAR",
        )

    # Das eigene Konto gleich mitverknuepfen - sofern die Identitaet nicht
    # schon an einem anderen Konto haengt.
    fremd = konten.find_linked(db, konto)
    if fremd is None or fremd.id == admin.id:
        konten.link(admin, konto)
        # Auch hier: eine Anmeldung genuegt fuer alles Weitere. Das Token in
        # den Einstellungen ist der Zugang **des Servers**, dieses hier der
        # persoenliche des Administrators - zwei Dinge, die zufaellig
        # denselben Ursprung haben.
        konten.merke_token(admin, encrypt(anbieter_token))

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
        logger.info("Bibliothek nach dem Verbinden nicht lesbar: %s", exc.message)

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


@admin_router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
def connect_delete(db: DbSession, admin: AdminUser) -> None:
    """Verbindung loesen.

    Die Verknuepfungen der Benutzer bleiben bestehen - wer den Server spaeter
    wieder verbindet, findet alles vor. Anmelden kann sich in der Zwischenzeit
    niemand darueber, weil ohne Server auch nichts zu pruefen ist.
    """
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
    logger.info("Media-Server-Verbindung getrennt von %r", admin.username)


# --------------------------------------------------------------------------
# Sperrliste
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Bibliotheks-Abgleich
# --------------------------------------------------------------------------


@admin_router.get("/library", response_model=LibraryState)
def library_state(db: DbSession, admin: AdminUser) -> LibraryState:
    return LibraryState(**mediaserver_library.stand(db))  # type: ignore[arg-type]


@admin_router.post("/library/refresh", response_model=LibraryState)
async def library_refresh(db: DbSession, admin: AdminUser) -> LibraryState:
    """Von Hand abgleichen.

    Im Betrieb passiert das im Hintergrund. Der Knopf ist fuer den Moment
    direkt nach dem Verbinden - und um ueberhaupt sehen zu koennen, dass es
    funktioniert.
    """
    settings = settings_service.load_settings(db)
    if not settings.mediaserver_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "mediaserver_not_configured",
                "message": "Es ist kein Media-Server verbunden.",
            },
        )
    try:
        # streng: Wer den Knopf drueckt, will die Wahrheit - auch die
        # unangenehme. Der stille Rueckzug ist nur fuer den Hintergrund.
        await mediaserver_library.refresh(db, settings, streng=True)
    except MediaServerError as exc:
        raise _anbieter_fehler(exc) from exc
    return LibraryState(**mediaserver_library.stand(db))  # type: ignore[arg-type]


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
