"""Benutzerverwaltung - ausschliesslich fuer Administratoren."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import meldungen
from ..deps import AdminUser, AdultUser, DbSession, betreiberschutz
from ..models import ApiKey, AuthToken, Role, TokenPurpose, User, utcnow
from ..schemas import (
    InvitationCreate,
    InvitationCreated,
    InvitationPublic,
    PasswordReset,
    UserPublic,
    UserUpdate,
    UserWithUsage,
    kontingent_aus_wert,
)
from ..security import hash_password
from ..services import (
    accounts,
    avatars,
    child_wishes,
    children,
    kontoaufloesung,
    mail,
    mediaserver_accounts,
    oidc_accounts,
    quota,
    tokens,
)
from ..services import (
    betreiber as betreiber_dienst,
)
from ..services import storage as storage_dienst
from ..services.arr import ArrError
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/users", tags=["users"])

logger = logging.getLogger("nexview.users")


def _username_taken(db: DbSession, username: str) -> bool:
    return (
        db.scalar(select(User.id).where(func.lower(User.username) == username.lower())) is not None
    )


def _get_user_or_404(db: DbSession, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=meldungen.meldung(
                "user_not_found",
                "Benutzer nicht gefunden.",
            )
        )
    return user


def _count_active_admins(db: DbSession) -> int:
    return (
        db.scalar(
            select(func.count(User.id)).where(User.role == Role.admin, User.is_active.is_(True))
        )
        or 0
    )


@router.get("/avatar/{name}", include_in_schema=False)
def read_avatar(name: str) -> Response:
    """Profilbild ausliefern.

    Bewusst ohne Anmeldung: ``<img>``-Elemente schicken keinen Token mit.
    Die Dateinamen sind zufaellig, also nicht erratbar. ``nosniff`` verhindert,
    dass der Browser den Inhalt als etwas anderes als ein Bild deutet.
    """
    try:
        content, media_type = avatars.read(name)
    except avatars.AvatarError as error:
        raise HTTPException(status_code=404, detail=error.message) from error

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _mit_verbrauch(db: DbSession, user: User) -> UserWithUsage:
    stand = quota.overview(db, user, load_settings(db))
    return UserWithUsage(
        **UserPublic.model_validate(user).model_dump(),
        quota_movies_used=stand["movie"].used,
        quota_series_used=stand["tv"].used,
    )


@router.get("", response_model=list[UserWithUsage])
def list_users(admin: AdminUser, db: DbSession) -> list[UserWithUsage]:
    # Gruppiert gezaehlt statt zweimal je Konto - siehe ``quota.uebersichten``.
    konten = list(db.scalars(select(User).order_by(User.created_at)))
    staende = quota.uebersichten(db, konten, load_settings(db))
    return [
        UserWithUsage(
            **UserPublic.model_validate(user).model_dump(),
            quota_movies_used=staende[user.id]["movie"].used,
            quota_series_used=staende[user.id]["tv"].used,
        )
        for user in konten
    ]


class SchluesselZeile(BaseModel):
    """Ein Zugriffs-Schluessel in der Aufsicht des Administrators.

    ⚠️ **Ohne den Schluessel selbst** - der existiert nur einmal, beim Anlegen.
    Ein Administrator soll sehen, *dass* es ihn gibt und ob er noch benutzt
    wird; lesen kann er ihn nicht. Das ist der Unterschied zwischen Aufsicht
    und Zugriff.
    """

    id: int
    user_id: int
    username: str
    name: str
    vorschau: str
    nur_lesen: bool
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


@router.get("/api-schluessel", response_model=list[SchluesselZeile])
def alle_schluessel(admin: AdminUser, db: DbSession) -> list[SchluesselZeile]:
    """Alle Zugriffs-Schluessel der Installation - wer hat welche, seit wann.

    ⚠️ **Vor den ``/{user_id}``-Pfaden eingetragen.** FastAPI vergleicht in der
    Reihenfolge der Definition; stuende das hier weiter unten, versuchte es
    "api-schluessel" als Benutzernummer zu lesen.

    Widerrufen kann der Administrator sie **nicht** - noch nicht. Das haengt am
    geschuetzten Betreiberkonto: Ohne das koennte ein ernannter Administrator
    die Schluessel dessen abschalten, der die Anwendung betreibt. Als grobe
    Notbremse bleibt heute, das Konto stillzulegen; das sperrt seine Schluessel
    automatisch mit.
    """
    zeilen = db.scalars(
        select(ApiKey).join(User, ApiKey.user_id == User.id).order_by(
            User.username, ApiKey.created_at.desc()
        )
    )
    return [
        SchluesselZeile(
            id=z.id,
            user_id=z.user_id,
            username=z.user.username,
            name=z.name,
            vorschau=z.vorschau,
            nur_lesen=z.nur_lesen,
            created_at=z.created_at,
            expires_at=z.expires_at,
            last_used_at=z.last_used_at,
        )
        for z in zeilen
    ]


# ---------------------------------------------------------------------------
# Der Betreiber
# ---------------------------------------------------------------------------
#
# ⚠️ **Beide Pfade stehen vor den ``/{user_id}``-Adressen.** FastAPI vergleicht
# in der Reihenfolge der Definition; weiter unten versuchte es "betreiber" als
# Benutzernummer zu lesen - dieselbe Falle wie bei "api-schluessel" oben.


class BetreiberInfo(BaseModel):
    """Wer die Installation betreibt - fuer das Abzeichen und die Uebergabe."""

    #: ``None`` heisst: niemand traegt ihn. Das ist ein echter Zustand, kein
    #: Fehler - siehe ``services/betreiber.traeger``. Die Oberflaeche muss ihn
    #: **zeigen**, sonst wundert sich jemand ueber ausgegraute Knoepfe, die es
    #: gar nicht gibt.
    user_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    #: In ``NEXVIEW_BETREIBER`` festgelegt. Dann ist die Uebergabe zu, und die
    #: Oberflaeche sagt warum - statt eine Uebergabe anzunehmen, die der
    #: naechste Neustart still zurueckdrehen wuerde.
    aus_umgebung: bool = False


class BetreiberUebergabe(BaseModel):
    user_id: int


def _betreiber_info(db: DbSession) -> BetreiberInfo:
    traeger = betreiber_dienst.traeger(db)
    return BetreiberInfo(
        user_id=traeger.id if traeger else None,
        username=traeger.username if traeger else None,
        display_name=traeger.display_name if traeger else None,
        aus_umgebung=betreiber_dienst.festgelegt_in_der_umgebung(),
    )


@router.get("/betreiber", response_model=BetreiberInfo)
def betreiber_stand(admin: AdminUser, db: DbSession) -> BetreiberInfo:
    """Wer traegt den Haken - und laesst er sich hier ueberhaupt bewegen?

    Fuer **jeden** Administrator lesbar, nicht nur fuer den Traeger: Wer die
    ausgegrauten Knoepfe sieht, soll auch erfahren, an wem es liegt. Ein Schutz,
    den man merkt, aber nicht versteht, sieht wie ein Fehler aus.
    """
    return _betreiber_info(db)


@router.post("/betreiber/uebergeben", response_model=BetreiberInfo)
def betreiber_uebergeben(
    payload: BetreiberUebergabe, user: AdultUser, db: DbSession
) -> BetreiberInfo:
    """Den Betreiber weitergeben - nur der Traeger selbst, und ohne Rueckweg.

    ⚠️ **``AdultUser`` und nicht ``AdminUser``**, obwohl der Betreiber immer
    Administrator ist. Die eigentliche Sperre soll genau **eine** Bedingung
    haben - "bist du der Traeger"; stuende ``AdminUser`` daneben, gaebe es zwei
    Stellen fuer dieselbe Frage, und welche Meldung ein gewoehnlicher Benutzer
    bekommt, hinge davon ab, welche zuerst greift.

    Ganz ohne Wache geht es aber auch nicht: ``test_child_permissions`` verlangt
    zu jedem Pfad eine Entscheidung, und diese Zeile ist sie. Sie behauptet das
    Schwaechste, was wahr ist - "nichts fuer Kinderkonten" - und laesst die
    Frage nach dem Betreiber dort, wo sie hingehoert.

    (Genau dieser Waechter hat den Pfad beim Bauen gefangen, als hier noch
    ``CurrentUser`` stand. Er funktioniert.)

    ⚠️ **Und ausdruecklich ohne ``betreiberschutz``.** Die Wache verbietet
    anderen, das Betreiberkonto anzufassen; hier fasst der Betreiber sein
    eigenes an. Sie haenge hier nur im Weg - sie liest ``user_id`` aus dem
    Pfad, und der steht hier fuer das **Ziel** der Uebergabe, nicht fuer das
    geschuetzte Konto.
    """
    ziel = _get_user_or_404(db, payload.user_id)
    try:
        betreiber_dienst.uebergeben(db, user, ziel)
    except betreiber_dienst.BetreiberFehler as fehler:
        raise HTTPException(
            status_code=fehler.status_code,
            detail=meldungen.meldung(fehler.code, fehler.message),
        ) from fehler
    db.commit()
    return _betreiber_info(db)


@router.post(
    "/{user_id}/quota/reset",
    response_model=UserWithUsage,
    dependencies=[Depends(betreiberschutz)],
)
def reset_quota(user_id: int, admin: AdminUser, db: DbSession) -> UserWithUsage:
    """Den Verbrauch im laufenden Zeitraum auf null setzen.

    Die Anfragen bleiben erhalten - es wird lediglich ab jetzt neu gezaehlt.
    Beim naechsten Zeitraumwechsel greift wieder der Kalender.
    """
    user = _get_user_or_404(db, user_id)
    user.quota_reset_at = utcnow()
    db.commit()
    db.refresh(user)

    logger.info("Quota reset for user %r by %r", user.username, admin.username)
    return _mit_verbrauch(db, user)


@router.post(
    "/{user_id}/storage/reset",
    response_model=UserWithUsage,
    dependencies=[Depends(betreiberschutz)],
)
def reset_storage(user_id: int, admin: AdminUser, db: DbSession) -> UserWithUsage:
    """Die Speicher-Belegung dieses Kontos auf null - alles geht ins Haus.

    Das Gegenstueck zu ``reset_quota``, und der Ausweg aus dem
    **Geisterposten**: Wer einen ueber Nexview angefragten Titel aus Radarr
    wirft und die Datei behaelt, bleibt dafuer belastet, und Nexview kann ihn
    nicht mehr entfernen - es loescht ausschliesslich ueber Radarr/Sonarr.

    ⚠️ **Keine Datei wird angefasst**, die gespeicherte Grenze bleibt stehen,
    offene Abgaben dieses Kontos sind danach erledigt.
    """
    user = _get_user_or_404(db, user_id)
    anzahl, bytes_ = storage_dienst.konto_zuruecksetzen(db, user.id)
    db.commit()
    db.refresh(user)

    logger.info(
        "Storage reset for user %r by %r: %s item(s), %s bytes",
        user.username,
        admin.username,
        anzahl,
        bytes_,
    )
    return _mit_verbrauch(db, user)


def _email_taken(db: DbSession, email: str, ausser: int | None = None) -> bool:
    """Gehoert die Adresse schon zu einem Konto oder zu einer offenen Einladung?"""
    adresse = tokens.normalize_email(email)
    query = select(User.id).where(User.email == adresse)
    if ausser is not None:
        query = query.where(User.id != ausser)
    if db.scalar(query) is not None:
        return True

    offen = db.scalars(
        select(AuthToken).where(
            AuthToken.purpose == TokenPurpose.invitation,
            AuthToken.email == adresse,
            AuthToken.used_at.is_(None),
        )
    )
    return any(token.open for token in offen)


# Konten entstehen ausschliesslich ueber eine Einladung. Ein Weg, auf dem der
# Administrator Benutzername und Passwort vorgibt, gibt es bewusst nicht mehr:
# er muesste das Passwort weitergeben, und damit kennen es zwei. Die einzige
# Ausnahme ist der erste Administrator - den legt der Einrichtungsassistent an,
# bevor es ueberhaupt einen Mailserver gibt.


@router.get("/invitations", response_model=list[InvitationPublic])
def list_invitations(admin: AdminUser, db: DbSession) -> list[InvitationPublic]:
    """Offene Einladungen - verbrauchte und abgelaufene interessieren nicht mehr."""
    offen = db.scalars(
        select(AuthToken)
        .where(
            AuthToken.purpose == TokenPurpose.invitation,
            AuthToken.used_at.is_(None),
        )
        .order_by(AuthToken.created_at.desc())
    )
    return [
        InvitationPublic(
            id=token.id,
            email=token.email,
            role=token.invite_role or Role.user,
            created_at=token.created_at,
            expires_at=token.expires_at,
        )
        for token in offen
        if token.open
    ]


@router.post("/invitations", response_model=InvitationCreated, status_code=201)
async def invite(payload: InvitationCreate, admin: AdminUser, db: DbSession) -> InvitationCreated:
    """Jemanden einladen. Das Konto entsteht erst, wenn er den Link einloest.

    Voraussetzung sind beide Bausteine: ohne oeffentliche Adresse enthaelt die
    Mail einen Link ins Leere, ohne Mailserver kommt sie gar nicht erst an.
    Eine Einladung anzulegen, die niemand einloesen kann, hilft niemandem -
    deshalb wird hier abgebrochen statt es zu versuchen.
    """
    settings = load_settings(db)
    # ⚠️ **Zwei Schalter statt eines fertigen Satzes.** Hier stand eine deutsche
    # Aufzaehlung ("die oeffentliche Adresse **und** ein Mailserver"), und die
    # kam auch bei einer englischen Oberflaeche auf Deutsch an - Aufzaehlungen
    # lassen sich nicht durch Einsetzen uebersetzen, das "und" steckt mitten
    # drin. Die Oberflaeche baut den Satz jetzt selbst (``client.ts``,
    # ``MIT_EIGENER_LOGIK``); der deutsche Satz bleibt als Rueckfall stehen,
    # fuer alles, was die Schnittstelle ohne diese Oberflaeche benutzt.
    fehlt = [
        bezeichnung
        for bezeichnung, vorhanden in (
            ("die öffentliche Adresse", bool(settings.public_url)),
            ("ein Mailserver", settings.mail_configured),
        )
        if not vorhanden
    ]
    if fehlt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "invite_needs_setup",
                f"Zum Einladen fehlt noch {' und '.join(fehlt)}. "
                "Nachzutragen unter Einstellungen → E-Mail.",
                needs_public_url=not settings.public_url,
                needs_mail=not settings.mail_configured,
            ),
        )

    if not mail.valid_address(payload.email):
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "email_invalid",
                "Das ist keine gültige E-Mail-Adresse.",
            ),
        )
    if _email_taken(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "email_taken_or_invited",
                "Diese Adresse gehört bereits zu einem Konto oder zu einer offenen Einladung.",
            ),
        )

    roh, token = tokens.create(
        db,
        TokenPurpose.invitation,
        payload.email,
        created_by=admin.id,
        invite_role=payload.role,
        # Die Einladung traegt die Grenzen so, wie sie am Konto landen sollen -
        # "Standard" als ``None``, "unbegrenzt" als ``UNBEGRENZT``. Der
        # Zeitraum steht nicht mehr dabei: er gilt haus-weit.
        invite_quota_movies=kontingent_aus_wert(payload.quota_movies_limit),
        invite_quota_series=kontingent_aus_wert(payload.quota_series_limit),
    )

    # Bewusst die Standardsprache der Installation, nicht die des einladenden
    # Admins: der Eingeladene hat noch kein Konto und damit keine eigene
    # Einstellung. Die Sprache des Admins waere reiner Zufall.
    zustellung = await accounts.send_invitation(settings, token, roh, settings.default_language)
    logger.info(
        "Invitation for %s created by %r (mail %s)",
        token.email,
        admin.username,
        "sent" if zustellung.sent else "NOT sent",
    )
    return InvitationCreated(
        id=token.id,
        email=token.email,
        role=payload.role,
        created_at=token.created_at,
        expires_at=token.expires_at,
        mail_sent=zustellung.sent,
        mail_error=zustellung.error,
        manual_link=None if zustellung.sent else zustellung.link,
    )


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_invitation(invitation_id: int, admin: AdminUser, db: DbSession) -> None:
    """Einladung zurueckziehen - der Link funktioniert danach nicht mehr."""
    token = db.get(AuthToken, invitation_id)
    if token is None or token.purpose != TokenPurpose.invitation:
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung(
                "invitation_not_found",
                "Einladung nicht gefunden.",
            ),
        )
    db.delete(token)
    db.commit()
    logger.info("Invitation for %s withdrawn by %r", token.email, admin.username)


@router.patch(
    "/{user_id}",
    response_model=UserPublic,
    dependencies=[Depends(betreiberschutz)],
)
def update_user(user_id: int, payload: UserUpdate, admin: AdminUser, db: DbSession) -> User:
    user = _get_user_or_404(db, user_id)
    data = payload.model_dump(exclude_unset=True)

    # Sich selbst nicht aussperren, und nie den letzten Admin entfernen.
    losing_admin = (data.get("role") == Role.user) or (data.get("is_active") is False)
    if user.role == Role.admin and losing_admin and _count_active_admins(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung(
                "last_admin_protected",
                "Der letzte aktive Administrator kann nicht herabgestuft oder deaktiviert werden.",
            ),
        )
    if user.id == admin.id and losing_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung(
                "cannot_demote_self",
                "Du kannst dir nicht selbst die Administratorrechte entziehen.",
            ),
        )

    # Wer selbst freigeben darf, gibt sich nicht erst selbst frei - der Haken
    # hat fuer Administratoren und Entscheider keine Wirkung und laesst sich
    # deshalb auch nicht setzen.
    kuenftige_rolle = data.get("role", user.role)
    if kuenftige_rolle in (Role.admin, Role.approver):
        data.pop("auto_approve", None)
        data.pop("auto_approve_movies", None)
        data.pop("auto_approve_series", None)
        # Dasselbe fuer 4K: wer freigeben darf, gibt sich auch dort selbst frei.
        data.pop("auto_approve_uhd", None)

    # Die drei Grenzen kommen als Wort oder Zahl herein ("standard",
    # "unlimited", n) und werden hier auf die Datenbank-Schreibweise gebracht:
    # ``None`` = Standard, ``UNBEGRENZT`` = ohne Grenze, sonst die Zahl.
    for feld in ("quota_movies_limit", "quota_series_limit", "storage_limit_gb"):
        if feld in data:
            data[feld] = kontingent_aus_wert(data[feld])

    # Wird das Recht entzogen, muessen die vorhandenen Kinder still werden -
    # sonst liefe die Kinderansicht weiter, ohne dass jemand sie verwaltet.
    if data.get("can_manage_children") is False:
        stillgelegt = children.recht_entzogen(db, user)
        if stillgelegt:
            logger.info(
                "Deactivated %d child account(s) of %r after revoking the permission",
                stillgelegt,
                user.username,
            )

    for field, value in data.items():
        # Profil-Sperren liegen als Komma-Liste in der Datenbank.
        if field in (
            "blocked_movie_profiles",
            "blocked_series_profiles",
            "blocked_movie_uhd_profiles",
            "blocked_series_uhd_profiles",
        ):
            value = ",".join(str(int(entry)) for entry in value or [])
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(betreiberschutz)],
)
def reset_password(user_id: int, payload: PasswordReset, admin: AdminUser, db: DbSession) -> None:
    user = _get_user_or_404(db, user_id)
    user.password_hash = hash_password(payload.password)
    user.password_changed_at = utcnow()
    db.commit()


class AufloesungsPosten(BaseModel):
    id: int
    title: str
    tier: str
    season: int | None
    media_type: str
    size_bytes: int


class LaufendeZeile(BaseModel):
    request_id: int
    title: str
    tier: str
    # ``None`` heisst: die ganze Serie wurde bestellt.
    season: int | None
    dateien: int
    folgen: int


class OffeneZeile(BaseModel):
    """Eine genehmigte Bestellung, von der noch keine Datei da ist."""

    request_id: int
    title: str
    tier: str
    # ``None`` heisst: die ganze Serie wurde bestellt.
    season: int | None


class AufloesungsVorschau(BaseModel):
    """Was das Konto hinterlaesst - Grundlage fuer den Loesch-Dialog."""

    posten: list[AufloesungsPosten]
    laufende: list[LaufendeZeile]
    # ⚠️ Bis 0.22 nur Titel, und ohne Rueckfrage storniert. Jetzt mit Kennung,
    # damit der Administrator auch hier entscheiden kann.
    offen: list[OffeneZeile]


class Staffelwahl(BaseModel):
    request_id: int
    behalten: bool
    weiter: bool = False


class Aufloesung(BaseModel):
    """Die Entscheidungen zum hinterlassenen Bestand - Teil des Loeschens."""

    haus: list[int] = []
    loeschen: list[int] = []
    staffeln: list[Staffelwahl] = []
    #: Offene Bestellungen, die weiterlaufen sollen - alles andere wird
    #: storniert. Leer heisst: alle stornieren, wie bis 0.21.
    offen_behalten: list[int] = []


@router.get("/{user_id}/aufloesung", response_model=AufloesungsVorschau)
async def aufloesung_vorschau(
    user_id: int, admin: AdminUser, db: DbSession
) -> AufloesungsVorschau:
    """Was dieses Konto hinterlassen wuerde - **ohne dass etwas passiert**.

    Der Administrator entscheidet mit dieser Liste vor Augen: je Posten Haus
    oder Loeschen, je angefangener Staffel behalten/loeschen und ob weiter
    geladen wird. Scheitert die Sonarr-Abfrage, scheitert die Vorschau -
    eine Aufloesung auf Basis geratener Zahlen waere schlimmer als eine
    vertagte.
    """
    user = _get_user_or_404(db, user_id)
    try:
        stand = await kontoaufloesung.vorschau(db, load_settings(db), user)
    except ArrError as fehler:
        raise HTTPException(502, fehler.message) from fehler
    return AufloesungsVorschau(
        posten=[
            AufloesungsPosten(
                id=z.id,
                title=z.title,
                tier=z.tier,
                season=z.season,
                media_type=z.media_type,
                size_bytes=z.size_bytes,
            )
            for z in stand.posten
        ],
        laufende=[
            LaufendeZeile(
                request_id=z.request_id,
                title=z.title,
                tier=z.tier,
                season=z.season,
                dateien=z.dateien,
                folgen=z.folgen,
            )
            for z in stand.laufende
        ],
        offen=[
            OffeneZeile(
                request_id=b.request_id, title=b.title, tier=b.tier, season=b.season
            )
            for b in stand.offen
        ],
    )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(betreiberschutz)],
)
async def delete_user(
    user_id: int,
    admin: AdminUser,
    db: DbSession,
    entscheidungen: Aufloesung | None = None,
) -> None:
    user = _get_user_or_404(db, user_id)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung(
                "cannot_delete_self",
                "Du kannst dein eigenes Konto nicht loeschen.",
            ),
        )
    if user.role == Role.admin and _count_active_admins(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meldungen.meldung(
                "last_admin_undeletable",
                "Der letzte aktive Administrator kann nicht geloescht werden.",
            ),
        )

    # Erst der hinterlassene Bestand, dann das Konto: Jeder Posten braucht
    # eine Entscheidung, laufende Bestellungen werden storniert - sonst laedt
    # eine ueberwachte Staffel herrenlos weiter (siehe kontoaufloesung).
    wahl = entscheidungen or Aufloesung()
    try:
        await kontoaufloesung.aufloesen(
            db,
            load_settings(db),
            user,
            haus=set(wahl.haus),
            loeschen=set(wahl.loeschen),
            staffeln=[
                kontoaufloesung.Staffelentscheidung(
                    request_id=z.request_id, behalten=z.behalten, weiter=z.weiter
                )
                for z in wahl.staffeln
            ],
            offen_behalten=set(wahl.offen_behalten),
            wer=admin.username,
        )
    except kontoaufloesung.Aufloesungsfehler as fehler:
        raise HTTPException(fehler.status_code, fehler.message) from fehler
    except storage_dienst.Loeschfehler as fehler:
        raise HTTPException(fehler.status_code, fehler.message) from fehler
    except ArrError as fehler:
        raise HTTPException(502, fehler.message) from fehler

    # Erst die Kinder, dann das Elternteil. Ohne diesen Schritt scheiterte das
    # Loeschen an der Fremdschluessel-Regel (frische Datenbank) bzw. hinterliesse
    # ein Kinderkonto mit einem Verweis ins Leere (aktualisierte Datenbank, wo
    # die nachgetragene Spalte keine Regel traegt).
    verwaist = children.alle_kinder_loeschen(db, user)
    if verwaist:
        logger.info("Deleted %d child account(s) of %r", verwaist, user.username)

    # ⚠️ **Und die Wuensche des Kontos selbst, falls es ein Kind ist.** Der
    # Schritt darueber raeumt die Kinder *unterhalb* eines Elternteils ab -
    # ist das geloeschte Konto aber selbst ein Kind, greift er nicht. Ueber die
    # Oberflaeche kommt dieser Fall nicht vor (die Nutzerverwaltung zeigt
    # Kinderkonten bewusst ohne Loeschknopf), ueber diesen Endpunkt sehr wohl.
    #
    # Ohne das bliebe eine Wunsch-Zeile mit einer toten ``child_id`` stehen -
    # ``ChildWish.child_id`` traegt keine Fremdschluessel-Regel, nachgetragene
    # Spalten koennen das in SQLite nicht. Sie waere nicht bloss Ballast: Die
    # Wunschliste des Elternteils liest ``wunsch.child.display_name`` und
    # stuerzte daran ab.
    if user.role == Role.child:
        offene = child_wishes.wuensche_loeschen(db, user)
        if offene:
            logger.info("Deleted %d wish(es) of child account %r", offene, user.username)

    # Sonst bliebe das Profilbild als verwaiste Datei liegen.
    avatars.remove(user.avatar_path)
    # Ohne diese Sperre waere das Loeschen wirkungslos: Wer Zugriff auf die
    # Bibliothek hat, meldet sich ueber den Media-Server einfach neu an und
    # bekommt sofort wieder ein Konto. Der Administrator kann die Sperre in den
    # Einstellungen jederzeit aufheben.
    mediaserver_accounts.block(db, user, by=admin.id)
    # Dieselbe Sperre fuer die OIDC-Wege: Mit eingeschalteter automatischer
    # Anlage legte dieselbe Identitaet sich sonst sofort ein neues Konto an.
    oidc_accounts.block(db, user, by=admin.id)
    db.delete(user)
    db.commit()
