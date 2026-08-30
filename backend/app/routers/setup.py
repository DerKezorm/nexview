"""Erst-Einrichtung: einmalig den ersten Administrator anlegen.

Dieser Router ist der einzige, der ohne Anmeldung schreiben darf - und auch
nur so lange, wie es ueberhaupt noch keinen Benutzer gibt. Danach antwortet
er dauerhaft mit 409.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status

from ..config import get_settings
from ..deps import DbSession, has_any_user
from ..models import Role, User
from ..schemas import AnmeldeWeg, SetupAdminCreate, SetupStatus, TokenPair
from ..security import hash_password
from ..services import mail, sicherung, sitzung, tokens
from ..services.mediaserver import PROVIDERS, verbundene_anbieter
from ..services.settings_service import load_settings
from .. import meldungen

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/status", response_model=SetupStatus)
def setup_status(db: DbSession) -> SetupStatus:
    # Die Anmeldeseite fragt das ohnehin beim Start ab. Sie muss vor dem
    # Anmelden wissen, ob es den Weg ueber den Media-Server gibt - und genau
    # dort ist noch niemand angemeldet, der die Einstellungen lesen duerfte.
    settings = load_settings(db)
    # Je verbundenem Anbieter ein Weg - mit der Art, wie er funktioniert.
    # Ein Anbieter, den diese Fassung nicht kennt, faellt still weg; er
    # koennte ohnehin nichts anbieten.
    wege = [
        AnmeldeWeg(
            provider=anbieter,
            label=PROVIDERS[anbieter].label,
            kind=PROVIDERS[anbieter].login_kind,
        )
        for anbieter in verbundene_anbieter(settings)
        if anbieter in PROVIDERS
    ]
    return SetupStatus(
        needs_setup=not has_any_user(db),
        mediaserver_login=bool(wege),
        mediaserver_provider=settings.mediaserver_provider or None,
        mediaserver_login_ways=wege,
    )


@router.post("/admin", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def create_first_admin(
    payload: SetupAdminCreate, request: Request, response: Response, db: DbSession
) -> TokenPair:
    if has_any_user(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "setup_already_done",
                "Die Einrichtung wurde bereits abgeschlossen.",
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

    admin = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        email=tokens.normalize_email(payload.email),
        # Auch der erste Administrator bestaetigt seine Adresse richtig.
        # Einen Mailserver gibt es hier noch nicht - die Bestaetigung holt der
        # Assistent am Ende nach, sobald einer eingerichtet ist. Sie einfach
        # als geprueft auszugeben waere eine Behauptung ins Blaue: ein
        # Tippfehler fiele erst auf, wenn er sich aussperrt.
        email_verified=False,
        role=Role.admin,
        display_name=payload.display_name or payload.username,
        language=payload.language,
        auto_approve=True,  # der Admin braucht keine Freigabe von sich selbst
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    return sitzung.starten(response, request, admin)


# ---------------------------------------------------------------------------
# Aus einer Sicherung starten statt bei null
# ---------------------------------------------------------------------------
#
# ⚠️ **Beide Endpunkte hier sind ohne Anmeldung erreichbar** - anders geht es
# nicht, es gibt ja noch kein Konto. Der Schutz ist derselbe wie beim Anlegen
# des ersten Administrators: Sobald **ein** Benutzer existiert, ist der Weg zu.
#
# Das heisst auch: Wer eine frische, aus dem Netz erreichbare Installation vor
# ihrem Besitzer findet, kann sie uebernehmen. Das gilt heute schon fuer
# ``/api/setup/admin`` - Wiederherstellen macht diese Luecke nicht groesser,
# aber wer Nexview vor dem ersten Login offen ins Netz haengt, hat unabhaengig
# davon ein Problem.


def _nur_vor_der_einrichtung(db: DbSession) -> None:
    if has_any_user(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "setup_already_done",
                "Die Einrichtung wurde bereits abgeschlossen.",
            ),
        )


@router.post("/sicherung/pruefen")
async def sicherung_pruefen(
    db: DbSession,
    datei: UploadFile = File(...),
    passwort: str = Form(...),
) -> dict[str, object]:
    """Nachsehen, was in der Sicherung steckt - ohne etwas zu ersetzen."""
    _nur_vor_der_einrichtung(db)
    try:
        befund = sicherung.pruefen(await datei.read(), passwort)
    except sicherung.SicherungFehler as fehler:
        raise HTTPException(
            status_code=400, detail=meldungen.meldung(fehler.code, fehler.text)
        ) from fehler
    return {
        "version": befund.brief.version,
        "erstellt": befund.brief.erstellt,
        "art": befund.brief.art,
        "kommentar": befund.brief.kommentar,
        "einspielbar": befund.einspielbar,
        "grund": befund.grund,
        "schluessel_aus_umgebung": bool(get_settings().secret_key),
        # Die andere Haelfte derselben Frage - siehe ``sicherung.Befund``.
        "schluessel_im_archiv": befund.schluessel_im_archiv,
    }


@router.post("/sicherung/einspielen", status_code=status.HTTP_204_NO_CONTENT)
async def sicherung_einspielen(
    db: DbSession,
    datei: UploadFile = File(...),
    passwort: str = Form(...),
) -> None:
    """Die frische Installation aus einer Sicherung aufbauen.

    Danach ist die Einrichtung beendet: Die Konten stehen in der Sicherung, und
    der Assistent zeigt sich nicht mehr.
    """
    _nur_vor_der_einrichtung(db)

    # ⚠️ **Erst die eigene Verbindung schliessen.** Diese Anfrage haelt selbst
    # eine offene Sitzung auf die Datenbank, die gleich ersetzt wird - und
    # solange sie offen ist, haelt SQLite die Begleitdatei ``-wal`` fest.
    # Unter Windows scheitert das Loeschen dann sichtbar; unter Linux ginge es
    # still durch und liesse einen Schreiber an einer geloeschten Datei
    # zurueck. Das Schliessen hier ist also kein Windows-Zugestaendnis,
    # sondern die Behebung.
    daten = await datei.read()
    db.close()

    try:
        sicherung.wiederherstellen(daten, passwort)
    except sicherung.SicherungFehler as fehler:
        raise HTTPException(
            status_code=400, detail=meldungen.meldung(fehler.code, fehler.text)
        ) from fehler
