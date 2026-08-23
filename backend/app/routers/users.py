"""Benutzerverwaltung - ausschliesslich fuer Administratoren."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select

from ..deps import AdminUser, DbSession
from ..models import AuthToken, Role, TokenPurpose, User, utcnow
from pydantic import BaseModel

from ..schemas import (
    InvitationCreate,
    InvitationCreated,
    InvitationPublic,
    PasswordReset,
    UserPublic,
    UserUpdate,
    UserWithUsage,
)
from ..security import hash_password
from ..services import (
    accounts,
    avatars,
    children,
    kontoaufloesung,
    mail,
    mediaserver_accounts,
    quota,
    tokens,
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
            status_code=status.HTTP_404_NOT_FOUND, detail="Benutzer nicht gefunden."
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
    stand = quota.overview(db, user)
    return UserWithUsage(
        **UserPublic.model_validate(user).model_dump(),
        quota_movies_used=stand["movie"].used,
        quota_series_used=stand["tv"].used,
    )


@router.get("", response_model=list[UserWithUsage])
def list_users(admin: AdminUser, db: DbSession) -> list[UserWithUsage]:
    return [
        _mit_verbrauch(db, user)
        for user in db.scalars(select(User).order_by(User.created_at))
    ]


@router.post("/{user_id}/quota/reset", response_model=UserWithUsage)
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
            detail=(
                f"Zum Einladen fehlt noch {' und '.join(fehlt)}. "
                "Nachzutragen unter Einstellungen → E-Mail."
            ),
        )

    if not mail.valid_address(payload.email):
        raise HTTPException(status_code=422, detail="Das ist keine gültige E-Mail-Adresse.")
    if _email_taken(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese Adresse gehört bereits zu einem Konto oder zu einer offenen Einladung.",
        )

    roh, token = tokens.create(
        db,
        TokenPurpose.invitation,
        payload.email,
        created_by=admin.id,
        invite_role=payload.role,
        invite_quota_movies=payload.quota_movies_limit,
        invite_quota_series=payload.quota_series_limit,
        invite_quota_period=payload.quota_period,
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
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden.")
    db.delete(token)
    db.commit()
    logger.info("Invitation for %s withdrawn by %r", token.email, admin.username)


@router.patch("/{user_id}", response_model=UserPublic)
def update_user(user_id: int, payload: UserUpdate, admin: AdminUser, db: DbSession) -> User:
    user = _get_user_or_404(db, user_id)
    data = payload.model_dump(exclude_unset=True)

    # Sich selbst nicht aussperren, und nie den letzten Admin entfernen.
    losing_admin = (data.get("role") == Role.user) or (data.get("is_active") is False)
    if user.role == Role.admin and losing_admin and _count_active_admins(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Der letzte aktive Administrator kann nicht herabgestuft oder deaktiviert werden.",
        )
    if user.id == admin.id and losing_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Du kannst dir nicht selbst die Administratorrechte entziehen.",
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

    # Die Speicher-Grenze zuruecksetzen - nur bedeutet NULL hier "es gilt die
    # Standardgrenze", nicht "unbegrenzt". Unbegrenzt fuer dieses Konto ist
    # die 0, und die geht ohne Umweg durch.
    if data.get("storage_limit_gb") == -1:
        data["storage_limit_gb"] = None

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


@router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
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


class AufloesungsVorschau(BaseModel):
    """Was das Konto hinterlaesst - Grundlage fuer den Loesch-Dialog."""

    posten: list[AufloesungsPosten]
    laufende: list[LaufendeZeile]
    # Titel offener Bestellungen ohne eine einzige Datei - werden storniert.
    storniert: list[str]


class Staffelwahl(BaseModel):
    request_id: int
    behalten: bool
    weiter: bool = False


class Aufloesung(BaseModel):
    """Die Entscheidungen zum hinterlassenen Bestand - Teil des Loeschens."""

    haus: list[int] = []
    loeschen: list[int] = []
    staffeln: list[Staffelwahl] = []


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
        storniert=stand.storniert,
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
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
            detail="Du kannst dein eigenes Konto nicht loeschen.",
        )
    if user.role == Role.admin and _count_active_admins(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Der letzte aktive Administrator kann nicht geloescht werden.",
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

    # Sonst bliebe das Profilbild als verwaiste Datei liegen.
    avatars.remove(user.avatar_path)
    # Ohne diese Sperre waere das Loeschen wirkungslos: Wer Zugriff auf die
    # Bibliothek hat, meldet sich ueber den Media-Server einfach neu an und
    # bekommt sofort wieder ein Konto. Der Administrator kann die Sperre in den
    # Einstellungen jederzeit aufheben.
    mediaserver_accounts.block(db, user, by=admin.id)
    db.delete(user)
    db.commit()
