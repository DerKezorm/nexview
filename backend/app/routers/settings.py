"""Einstellungen: TMDB-, Radarr-, Sonarr- und Mailzugang. Nur fuer Administratoren."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field, field_validator

from ..deps import AdminUser, CurrentUser, DbSession
from ..schemas import MIN_PASSWORD_LENGTH
from ..services import cache, library, mail, mail_templates
from ..services.arr import ArrError
from ..services.radarr import RadarrClient
from ..services.sonarr import SonarrClient
from ..services.settings_service import (
    SECRET_KEYS,
    clear_secret,
    load_settings,
    public_settings,
    save_settings,
)
from ..services.tmdb import TmdbClient, TmdbError

router = APIRouter(prefix="/api", tags=["settings"])

logger = logging.getLogger("nexview.settings")


class SettingsUpdate(BaseModel):
    """Leere Felder bei Geheimnissen bedeuten: unveraendert lassen."""

    tmdb_api_key: str | None = None
    radarr_url: str | None = None
    radarr_api_key: str | None = None
    sonarr_url: str | None = None
    sonarr_api_key: str | None = None
    default_region: str | None = Field(default=None, min_length=2, max_length=2)
    default_language: str | None = Field(default=None, min_length=2, max_length=5)
    poll_interval_seconds: int | None = Field(default=None, ge=30, le=3600)
    demo_mode: str | None = None
    # Vorausgewaehltes Qualitaetsprofil; leerer String hebt die Vorauswahl auf.
    default_movie_profile_id: str | None = Field(default=None, max_length=12)
    default_series_profile_id: str | None = Field(default=None, max_length=12)
    # Duerfen Benutzer den Zielordner selbst waehlen? Wenn nicht, gilt der hier
    # hinterlegte fuer alle.
    # "user" | "fixed" | "approver" - wer waehlt den Zielordner?
    movie_root_folder_mode: str | None = None
    series_root_folder_mode: str | None = None
    movie_profile_mode: str | None = None
    series_profile_mode: str | None = None
    default_movie_root: str | None = Field(default=None, max_length=500)
    default_series_root: str | None = Field(default=None, max_length=500)
    # --- Zweite Instanz fuer 4K ---------------------------------------------
    radarr_uhd_url: str | None = Field(default=None, max_length=255)
    radarr_uhd_api_key: str | None = Field(default=None, max_length=255)
    sonarr_uhd_url: str | None = Field(default=None, max_length=255)
    sonarr_uhd_api_key: str | None = Field(default=None, max_length=255)
    default_movie_uhd_profile_id: str | None = Field(default=None, max_length=12)
    default_series_uhd_profile_id: str | None = Field(default=None, max_length=12)
    default_movie_uhd_root: str | None = Field(default=None, max_length=500)
    default_series_uhd_root: str | None = Field(default=None, max_length=500)
    # Mailversand
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: str | None = None
    smtp_username: str | None = Field(default=None, max_length=255)
    smtp_password: str | None = None
    smtp_from_address: str | None = Field(default=None, max_length=255)
    smtp_from_name: str | None = Field(default=None, max_length=120)
    # Adresse, unter der Nexview von aussen erreichbar ist.
    public_url: str | None = Field(default=None, max_length=255)
    # Taegliche Nachfrage bei GitHub nach einer neueren Version.
    update_check: bool | None = None
    # --- Media-Server ------------------------------------------------------
    # Server, Adresse und Token stehen hier bewusst *nicht*: Die setzt allein
    # der Verbindungsvorgang (`/api/admin/mediaserver/connect/...`), damit die
    # Maschinenkennung immer zu einem tatsaechlich geprueften Server gehoert.
    mediaserver_auto_import: bool | None = None
    mediaserver_default_role: str | None = None
    # Leerer String bedeutet "unbegrenzt" bzw. "keine Altersgrenze".
    mediaserver_default_quota_movies: str | None = Field(default=None, max_length=6)
    mediaserver_default_quota_series: str | None = Field(default=None, max_length=6)
    mediaserver_default_quota_period: str | None = None
    mediaserver_default_age: str | None = Field(default=None, max_length=3)
    # --- Merkliste ----------------------------------------------------------
    watchlist_enabled: bool | None = None

    @field_validator("mediaserver_default_role")
    @classmethod
    def _keine_admin_vorgabe(cls, wert: str | None) -> str | None:
        """"Administrator" als Vorgabe waere eine Rechte-Falle.

        Ein automatisch angelegtes Konto darf niemals volle Rechte bekommen -
        wer Zugriff auf die Bibliothek hat, ist damit noch lange nicht
        berechtigt, andere Konten zu verwalten.
        """
        if wert is not None and wert not in ("user", "approver"):
            raise ValueError("Als Vorgabe sind nur 'user' und 'approver' erlaubt.")
        return wert


class ConnectionTest(BaseModel):
    """Optional noch nicht gespeicherte Daten, um sie vorab zu pruefen."""

    api_key: str | None = None
    url: str | None = None


class TestResult(BaseModel):
    ok: bool
    message: str


class AppConfig(BaseModel):
    """Was die Oberflaeche ueber die Konfiguration wissen muss."""

    default_region: str
    default_language: str
    tmdb_configured: bool
    radarr_configured: bool
    sonarr_configured: bool
    using_demo_data: bool
    # Kommt bewusst vom Server: sonst koennen Formular und Pruefung
    # auseinanderlaufen - genau das ist schon einmal passiert.
    min_password_length: int
    # Ohne beides sind Einladungen sinnlos: der Link zeigt ins Leere bzw. die
    # Mail kommt nicht an. Die Oberflaeche sperrt den Knopf entsprechend.
    mail_configured: bool
    public_url_set: bool
    # Entscheidet der Entscheider erst bei der Freigabe ueber Zielordner und
    # Profil? Je Dienst, weil es je Dienst eingestellt wird. Gehoert hierher
    # und nicht in die Admin-Einstellungen: auch ein gewoehnlicher Benutzer
    # muss es wissen, um sein Formular richtig zu zeichnen.
    approver_picks_target_movie: bool
    approver_picks_target_tv: bool
    # Gibt es eine zweite Instanz fuer 4K? Ohne sie bleibt die ganze Funktion
    # in der Oberflaeche unsichtbar.
    radarr_uhd_configured: bool
    sonarr_uhd_configured: bool
    # Ist ueberhaupt ein Media-Server verbunden? Daran haengt, ob es den
    # Merklisten-Bereich geben kann.
    mediaserver_configured: bool
    # Duerfen Benutzer ihre Merkliste sehen und daraus anfragen? Die
    # Oberflaeche blendet daran den Menuepunkt und den Filter "Über Merkliste
    # angefragt" ein.
    watchlist_enabled: bool


@router.get("/config", response_model=AppConfig)
def read_config(user: CurrentUser, db: DbSession) -> AppConfig:
    settings = load_settings(db)
    return AppConfig(
        default_region=settings.default_region,
        default_language=settings.default_language,
        tmdb_configured=settings.tmdb_configured,
        radarr_configured=settings.radarr_configured,
        sonarr_configured=settings.sonarr_configured,
        using_demo_data=settings.use_demo_data,
        min_password_length=MIN_PASSWORD_LENGTH,
        mail_configured=settings.mail_configured,
        public_url_set=bool(settings.public_url),
        approver_picks_target_movie=settings.approver_picks_target("movie"),
        approver_picks_target_tv=settings.approver_picks_target("tv"),
        radarr_uhd_configured=settings.radarr_uhd_configured,
        sonarr_uhd_configured=settings.sonarr_uhd_configured,
        mediaserver_configured=settings.mediaserver_configured,
        watchlist_enabled=settings.watchlist_enabled,
    )


@router.get("/settings")
def read_settings(admin: AdminUser, db: DbSession) -> dict[str, object]:
    return public_settings(db)


def _gleiche_adresse_ablehnen(db: DbSession, payload: SettingsUpdate) -> None:
    """Standard- und 4K-Instanz duerfen nicht auf denselben Server zeigen."""
    gespeichert = load_settings(db)
    paare = (
        ("Radarr", payload.radarr_url, gespeichert.radarr_url,
         payload.radarr_uhd_url, gespeichert.radarr_uhd_url),
        ("Sonarr", payload.sonarr_url, gespeichert.sonarr_url,
         payload.sonarr_uhd_url, gespeichert.sonarr_uhd_url),
    )
    for name, neu_standard, alt_standard, neu_uhd, alt_uhd in paare:
        standard = (neu_standard if neu_standard is not None else alt_standard).strip().rstrip("/")
        uhd = (neu_uhd if neu_uhd is not None else alt_uhd).strip().rstrip("/")
        if standard and uhd and standard.lower() == uhd.lower():
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Die 4K-Instanz von {name} hat dieselbe Adresse wie die normale. "
                    "Es müssen zwei getrennte Server sein."
                ),
            )


@router.put("/settings")
def update_settings(payload: SettingsUpdate, admin: AdminUser, db: DbSession) -> dict[str, object]:
    if payload.demo_mode is not None and payload.demo_mode not in {"auto", "on", "off"}:
        raise HTTPException(
            status_code=422,
            detail="Demo-Modus muss 'auto', 'on' oder 'off' sein.",
        )
    for feld in (
        "movie_root_folder_mode",
        "series_root_folder_mode",
        "movie_profile_mode",
        "series_profile_mode",
    ):
        wert = getattr(payload, feld)
        if wert is not None and wert not in ("user", "fixed", "approver"):
            raise HTTPException(
                status_code=422,
                detail="Regel muss 'user', 'fixed' oder 'approver' sein.",
            )

    # Zielordner und Qualitaetsprofil haengen zusammen: Sobald **eines** von
    # beiden der Entscheider setzt, wartet die ganze Anfrage auf ihn - sie
    # waere sonst unvollstaendig bei Radarr gelandet (siehe
    # ``AppSettings.approver_picks_target``). Das andere auf "der Benutzer
    # waehlt" stehen zu lassen waere eine Einstellung ohne Wirkung: Der
    # Betreiber setzt sie, und nichts passiert.
    #
    # Beide ziehen deshalb gemeinsam um - und zwar in beide Richtungen, sonst
    # kaeme man aus "Entscheider" nie wieder heraus. Das steht hier und nicht
    # nur in der Oberflaeche, damit die Datenbank keine Kombination enthaelt,
    # die es in Wirklichkeit gar nicht gibt.
    aktuell = load_settings(db)
    for ordner_feld, profil_feld, art in (
        ("movie_root_folder_mode", "movie_profile_mode", "movie"),
        ("series_root_folder_mode", "series_profile_mode", "tv"),
    ):
        neu_ordner = getattr(payload, ordner_feld)
        neu_profil = getattr(payload, profil_feld)
        if neu_ordner is None and neu_profil is None:
            continue

        # Schickt der Aufrufer beide mit, muss er sie auch stimmig schicken.
        # Stillschweigend etwas anderes zu speichern, als verlangt wurde, ist
        # genau der Fehler, den diese Regel beheben soll.
        if neu_ordner is not None and neu_profil is not None:
            if (neu_ordner == "approver") != (neu_profil == "approver"):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Zielordner und Qualitätsprofil gehören zusammen: „Der "
                        "Entscheider wählt“ gilt entweder für beide oder für keines."
                    ),
                )
            continue

        # Nur eines mitgeschickt: Der Aufrufer hat zum anderen keine Meinung -
        # dann wird es nachgezogen.
        gesetzt, offen = (
            (neu_ordner, profil_feld) if neu_ordner is not None else (neu_profil, ordner_feld)
        )
        vorher = aktuell.profile_mode(art) if offen.endswith("profile_mode") else             aktuell.root_folder_mode(art)
        if gesetzt == "approver":
            setattr(payload, offen, "approver")
        elif vorher == "approver":
            # Weg von "Entscheider": Das Gegenstueck darf nicht dort haengen
            # bleiben, sonst waere die neue Einstellung wieder wirkungslos.
            setattr(payload, offen, gesetzt)

    if payload.smtp_security is not None and payload.smtp_security not in mail.SECURITY_MODES:
        raise HTTPException(
            status_code=422,
            detail="Verschlüsselung muss 'none', 'starttls' oder 'ssl' sein.",
        )
    # Dieselbe Adresse fuer beide Stufen ist immer ein Versehen: Man traegt die
    # 4K-Instanz ein, schreibt in Wahrheit weiter in die alte, und wundert sich,
    # warum 4K nie ankommt. Lieber jetzt widersprechen als still danebengehen.
    _gleiche_adresse_ablehnen(db, payload)
    if payload.smtp_from_address:
        if not mail.valid_address(payload.smtp_from_address):
            raise HTTPException(status_code=422, detail="Die Absenderadresse ist ungültig.")
    if payload.public_url and not payload.public_url.strip().startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422,
            detail="Die öffentliche Adresse muss mit http:// oder https:// beginnen.",
        )

    save_settings(db, payload.model_dump(exclude_unset=True))
    # Alte Ergebnisse verwerfen: Region, Sprache oder Key koennten sich
    # geaendert haben.
    cache.clear_all(db)
    library.invalidate()
    return public_settings(db)


@router.delete("/settings/secret/{name}")
def delete_secret(
    name: Annotated[
        Literal[
            "tmdb_api_key",
            "radarr_api_key",
            "radarr_uhd_api_key",
            "sonarr_api_key",
            "sonarr_uhd_api_key",
            "smtp_password",
            "mediaserver_token",
        ],
        Path(),
    ],
    admin: AdminUser,
    db: DbSession,
) -> dict[str, object]:
    """Einen hinterlegten API-Key entfernen.

    Beim Speichern bedeutet ein leeres Feld "unveraendert" - sonst wuerde der
    maskierte Wert aus der Oberflaeche den Key ueberschreiben. Zum bewussten
    Loeschen braucht es deshalb diesen eigenen Weg.
    """
    if name not in SECRET_KEYS:  # pragma: no cover - durch Literal abgesichert
        raise HTTPException(status_code=404, detail="Unbekannte Einstellung.")

    clear_secret(db, name)
    cache.clear_all(db)
    library.invalidate()
    return public_settings(db)


@router.post("/settings/test/tmdb", response_model=TestResult)
async def test_tmdb(payload: ConnectionTest, admin: AdminUser, db: DbSession) -> TestResult:
    settings = load_settings(db)
    api_key = (payload.api_key or "").strip() or settings.tmdb_api_key

    if not api_key or api_key.startswith("•"):
        return TestResult(ok=False, message="Es ist noch kein TMDB API-Key hinterlegt.")

    client = TmdbClient(api_key, settings.default_language, settings.default_region)
    try:
        await client.verify()
    except TmdbError as error:
        return TestResult(ok=False, message=error.message)

    return TestResult(ok=True, message="Verbindung zu TMDB erfolgreich.")


class UrlTest(BaseModel):
    url: str = Field(min_length=4, max_length=255)


@router.post("/settings/test/public-url", response_model=TestResult)
async def test_public_url(payload: UrlTest, admin: AdminUser, db: DbSession) -> TestResult:
    """Antwortet unter dieser Adresse tatsaechlich Nexview?

    Der Server ruft sich dafuer selbst von aussen auf. Bewusst mit einem
    eigenen, kurzlebigen Client: das hier ist eine seltene Handbewegung auf
    eine jedes Mal andere Adresse - ein Verbindungspool braeuchte es dafuer
    nicht.
    """
    adresse = payload.url.strip().rstrip("/")
    if not adresse.startswith(("http://", "https://")):
        return TestResult(
            ok=False, message="Die Adresse muss mit http:// oder https:// beginnen."
        )

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            antwort = await client.get(f"{adresse}/api/health")
    except httpx.HTTPError as fehler:
        logger.info("Public URL check failed for %s: %s", adresse, fehler)
        return TestResult(
            ok=False,
            message=(
                f"Unter {adresse} war nichts erreichbar. Bitte die Schreibweise prüfen. "
                "Möglich ist auch, dass der Server sich selbst nicht von außen erreicht – "
                "dann stimmt die Adresse trotzdem, solange ihr sie im Browser verwendet."
            ),
        )

    if antwort.status_code != 200:
        return TestResult(
            ok=False,
            message=f"Unter {adresse} antwortete etwas mit HTTP {antwort.status_code}.",
        )

    try:
        daten = antwort.json()
    except ValueError:
        daten = {}

    if daten.get("status") != "ok":
        return TestResult(
            ok=False,
            message=f"Unter {adresse} antwortet zwar etwas, aber es ist kein Nexview.",
        )

    return TestResult(
        ok=True, message=f"Nexview {daten.get('version', '')} ist unter {adresse} erreichbar."
    )


class SmtpTest(BaseModel):
    """Optional noch nicht gespeicherte Zugangsdaten, um vorab zu pruefen."""

    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    security: str | None = None
    username: str | None = Field(default=None, max_length=255)
    password: str | None = None


class TestMail(BaseModel):
    recipient: str = Field(min_length=3, max_length=255)


def _mail_config(db: DbSession, entwurf: SmtpTest | None = None) -> mail.MailConfig:
    """Gespeicherte Einstellungen, ueberschrieben von noch nicht Gespeichertem.

    So laesst sich testen, bevor man speichert - und ein maskiertes Passwort
    aus der Oberflaeche darf das echte natuerlich nicht ersetzen.
    """
    settings = load_settings(db)
    passwort = settings.smtp_password
    if entwurf and entwurf.password and not entwurf.password.startswith("•"):
        passwort = entwurf.password

    sicherheit = (entwurf.security if entwurf else None) or settings.smtp_security
    return mail.MailConfig(
        host=((entwurf.host if entwurf else None) or settings.smtp_host).strip(),
        port=(entwurf.port if entwurf and entwurf.port else settings.smtp_port),
        security=sicherheit if sicherheit in mail.SECURITY_MODES else "starttls",
        username=((entwurf.username if entwurf else None) or settings.smtp_username).strip(),
        password=passwort,
        from_address=settings.smtp_from_address,
        from_name=settings.smtp_from_name,
    )


@router.post("/settings/test/smtp", response_model=TestResult)
async def test_smtp(payload: SmtpTest, admin: AdminUser, db: DbSession) -> TestResult:
    """Verbindung und Anmeldung pruefen, ohne eine Mail zu verschicken."""
    config = _mail_config(db, payload)
    if not config.host:
        return TestResult(ok=False, message="Es ist noch kein Mailserver hinterlegt.")

    try:
        await mail.verify(config)
    except mail.MailError as error:
        return TestResult(ok=False, message=error.message)

    art = {"none": "unverschlüsselt", "starttls": "mit STARTTLS", "ssl": "über SSL"}
    angemeldet = " und angemeldet" if config.username else ""
    return TestResult(
        ok=True,
        message=(
            f"Verbindung zu {config.host}:{config.port} "
            f"{art[config.security]} hergestellt{angemeldet}."
        ),
    )


@router.post("/settings/test-mail", response_model=TestResult)
async def send_test_mail(payload: TestMail, admin: AdminUser, db: DbSession) -> TestResult:
    """Eine gestaltete Testnachricht an die angegebene Adresse schicken."""
    empfaenger = payload.recipient.strip()
    if not mail.valid_address(empfaenger):
        return TestResult(ok=False, message="Das ist keine gültige E-Mail-Adresse.")

    config = _mail_config(db)
    if not config.configured:
        return TestResult(
            ok=False,
            message="Bitte zuerst Mailserver und Absenderadresse eintragen und speichern.",
        )

    betreff, html, text = mail_templates.test_mail(admin.language)
    try:
        await mail.send(config, empfaenger, betreff, html, text)
    except mail.MailError as error:
        return TestResult(ok=False, message=error.message)

    return TestResult(ok=True, message=f"Testnachricht an {empfaenger} verschickt.")


@router.post("/settings/test/{service}", response_model=TestResult)
async def test_arr(
    service: Annotated[
        Literal["radarr", "sonarr", "radarr_uhd", "sonarr_uhd"], Path()
    ],
    payload: ConnectionTest,
    admin: AdminUser,
    db: DbSession,
) -> TestResult:
    """Verbindung zu Radarr bzw. Sonarr pruefen - Standard- oder 4K-Instanz.

    Nutzt die uebergebenen Daten, falls sie noch nicht gespeichert sind -
    so kann man testen, bevor man speichert.
    """
    settings = load_settings(db)
    is_radarr = service.startswith("radarr")
    tier = "uhd" if service.endswith("_uhd") else "standard"
    label = ("Radarr" if is_radarr else "Sonarr") + (" 4K" if tier == "uhd" else "")

    gespeicherte_url, gespeicherter_key = settings.arr_endpoint(
        "movie" if is_radarr else "tv", tier
    )
    url = (payload.url or "").strip() or gespeicherte_url
    api_key = (payload.api_key or "").strip()
    if not api_key or api_key.startswith("•"):
        api_key = gespeicherter_key

    if not url or not api_key:
        return TestResult(ok=False, message=f"Adresse und API-Key für {label} fehlen noch.")

    client = RadarrClient(url, api_key) if is_radarr else SonarrClient(url, api_key)
    try:
        info = await client.system_status()
    except ArrError as error:
        return TestResult(ok=False, message=error.message)

    # Verwechslung der beiden Adressen ist der haeufigste Einrichtungsfehler.
    reported = str(info.get("appName") or "").lower()
    erwartet = "radarr" if is_radarr else "sonarr"
    if reported and reported != erwartet:
        return TestResult(
            ok=False,
            message=f"Unter dieser Adresse antwortet {info.get('appName')}, nicht {label}.",
        )

    version = info.get("version")
    suffix = f" (Version {version})" if version else ""
    return TestResult(ok=True, message=f"Verbindung zu {label} erfolgreich{suffix}.")
