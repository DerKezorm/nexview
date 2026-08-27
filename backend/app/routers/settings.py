"""Einstellungen: TMDB-, Radarr-, Sonarr- und Mailzugang. Nur fuer Administratoren."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field, field_validator

from ..deps import AdminUser, AdultUser, CurrentUser, DbSession
from ..schemas import MIN_PASSWORD_LENGTH
from ..services import (
    cache,
    instanz_gesundheit,
    library,
    mail,
    mail_templates,
    storage,
    webhook_pflege,
    webhooks,
)
from ..services.arr import ArrClient, ArrError
from ..services.radarr import RadarrClient
from ..services.sonarr import SonarrClient
from ..services.settings_service import (
    SECRET_KEYS,
    clear_secret,
    load_settings,
    public_settings,
    save_settings,
)
from ..services.mediaserver import (
    PROVIDERS,
    merklisten_anbieter,
    verbundene_anbieter,
)
from ..services.tmdb import TmdbClient, TmdbError
from .. import meldungen

router = APIRouter(prefix="/api", tags=["settings"])

logger = logging.getLogger("nexview.settings")


class SettingsUpdate(BaseModel):
    """Leere Felder bei Geheimnissen bedeuten: unveraendert lassen."""

    tmdb_api_key: str | None = None
    radarr_url: str | None = None
    radarr_api_key: str | None = None
    sonarr_url: str | None = None
    sonarr_api_key: str | None = None
    # Frei waehlbare Anzeigenamen - leer heisst: der Dienstname gilt.
    radarr_name: str | None = Field(default=None, max_length=60)
    sonarr_name: str | None = Field(default=None, max_length=60)
    radarr_uhd_name: str | None = Field(default=None, max_length=60)
    sonarr_uhd_name: str | None = Field(default=None, max_length=60)
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
    # Dieselben Regeln je 4K-Instanz - seit dem Kachel-Umbau je Instanz.
    movie_uhd_root_folder_mode: str | None = None
    series_uhd_root_folder_mode: str | None = None
    movie_uhd_profile_mode: str | None = None
    series_uhd_profile_mode: str | None = None
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
    # Adresse aus Sicht von Radarr/Sonarr - nur noetig, wenn die oeffentliche
    # fuer den Rueckkanal nicht taugt (Docker-Netz, Proxy-Schutz, Zertifikat).
    webhook_basis_url: str | None = Field(default=None, max_length=255)
    # Taegliche Nachfrage bei GitHub nach einer neueren Version.
    update_check: bool | None = None
    # --- Sicherungen -------------------------------------------------------
    # Regelmaessig sichern. Bis 0.22 entstand eine automatische Sicherung nur
    # bei einer Schemaaenderung - also praktisch nur beim Update.
    backup_schedule: Literal["off", "daily", "weekly", "monthly"] | None = None
    # Untergrenze zwei: Bei einem einzigen Stand wuerde die naechste Sicherung
    # den letzten Rueckweg ueberschreiben.
    backup_keep: int | None = Field(default=None, ge=2, le=50)
    # --- Media-Server ------------------------------------------------------
    # Server, Adresse und Token stehen hier bewusst *nicht*: Die setzt allein
    # der Verbindungsvorgang (`/api/admin/mediaserver/connect/...`), damit die
    # Maschinenkennung immer zu einem tatsaechlich geprueften Server gehoert.
    mediaserver_auto_import: bool | None = None
    mediaserver_default_role: str | None = None
    # --- Merkliste ----------------------------------------------------------
    watchlist_enabled: bool | None = None
    # --- Folgen-Pakete ------------------------------------------------------
    episode_requests_enabled: bool | None = None
    # --- Kontingente --------------------------------------------------------
    # Die drei Standardwerte des Hauses. **-1 setzt auf "unbegrenzt"** - das
    # ``None`` von Pydantic heisst hier "nicht mitgeschickt", kann also nicht
    # gleichzeitig "leeren" bedeuten. Dieselbe Uebereinkunft wie in
    # ``UserUpdate``.
    quota_default_movies: int | None = Field(default=None, ge=-1)
    quota_default_series: int | None = Field(default=None, ge=-1)
    storage_default_limit_gb: int | None = Field(default=None, ge=-1)
    quota_period: Literal["day", "week", "month"] | None = None

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
    approver_picks_target_movie_uhd: bool
    approver_picks_target_tv_uhd: bool
    # Gibt es eine zweite Instanz fuer 4K? Ohne sie bleibt die ganze Funktion
    # in der Oberflaeche unsichtbar.
    radarr_uhd_configured: bool
    sonarr_uhd_configured: bool
    # Ist ueberhaupt ein Media-Server verbunden? Daran haengt, ob es den
    # Merklisten-Bereich geben kann.
    mediaserver_configured: bool
    # **Welche** Server verbunden sind - heute hoechstens einer, die Liste ist
    # trotzdem eine Liste. Die Oberflaeche braucht die Namen an zwei Stellen:
    # um Logos statt des Wortes "Plex" zu zeigen, und um zu entscheiden, ob es
    # ueberhaupt etwas zu unterscheiden gibt. Beides waere mit einem blossen
    # "ja/nein" nicht moeglich.
    mediaserver_providers: list[str]
    # Welche Anbieter **diese Fassung** ueberhaupt kennt - unabhaengig davon, ob
    # einer verbunden ist. Die Oberflaeche zeigt fuer jeden bekannten Anbieter
    # eine Kachel und graut die uebrigen aus.
    #
    # Kommt bewusst vom Server: Sonst muesste die Oberflaeche eine zweite Liste
    # fuehren, und die erste vergessene Zeile waere eine Kachel, die man
    # anklicken kann und die dann nichts tut. Genau die Doppelung, die es hier
    # bis 0.18.0 zwischen ``PROVIDERS`` und der Anbieter-Weissliste gab.
    mediaserver_available: list[str]
    # Welche davon mit Benutzername und Passwort verbunden werden - der Rest
    # ueber den Code-Ablauf. Auch das kommt vom Server, aus demselben Grund wie
    # die Liste darueber: Die Oberflaeche soll nicht raten muessen, welches
    # Formular sie zeigt, und die Antwort steht ohnehin schon im Adapter.
    mediaserver_password_login: list[str]
    # Quellen fuer Merklisten: was diese Fassung kennt, und was davon
    # verbunden ist. Zwei Listen, weil die Oberflaeche beides braucht - eine
    # Quelle soll auch dann dastehen, wenn sie *nicht* verbunden ist, sonst
    # verschwindet der ganze Bereich und niemand weiss, warum.
    #
    # Heute ist das nur Plex; Jellyfin und Emby haben keine Merkliste. Kommt
    # spaeter Trakt dazu, ist es hier eine Zeile.
    mediaserver_watchlist_available: list[str]
    mediaserver_watchlist_connected: list[str]
    # Duerfen Benutzer ihre Merkliste sehen und daraus anfragen? Die
    # Oberflaeche blendet daran den Menuepunkt und den Filter "Über Merkliste
    # angefragt" ein.
    watchlist_enabled: bool
    # Duerfen Benutzer Folgen-Pakete anfragen? Die Oberflaeche blendet daran
    # die Aufklapp-Pfeile im Staffel-Waehler ein.
    episode_requests_enabled: bool


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
        approver_picks_target_movie_uhd=settings.approver_picks_target("movie", "uhd"),
        approver_picks_target_tv_uhd=settings.approver_picks_target("tv", "uhd"),
        radarr_uhd_configured=settings.radarr_uhd_configured,
        sonarr_uhd_configured=settings.sonarr_uhd_configured,
        mediaserver_configured=settings.mediaserver_configured,
        mediaserver_providers=verbundene_anbieter(settings),
        mediaserver_available=sorted(PROVIDERS),
        mediaserver_password_login=sorted(
            name for name, klasse in PROVIDERS.items() if klasse.supports_password_login()
        ),
        mediaserver_watchlist_available=sorted(
            name for name, klasse in PROVIDERS.items() if klasse.supports_watchlist()
        ),
        mediaserver_watchlist_connected=merklisten_anbieter(settings),
        watchlist_enabled=settings.watchlist_enabled,
        episode_requests_enabled=settings.episode_requests_enabled,
    )


class RegionOut(BaseModel):
    code: str
    name: str


@router.get("/config/regions", response_model=list[RegionOut])
async def read_regions(user: AdultUser, db: DbSession) -> list[RegionOut]:
    """Die Laender, unter denen jemand seine Region waehlen kann.

    Bewusst von TMDB geholt statt im Quelltext gepflegt: Vorher standen acht
    feste Kuerzel im Frontend, und wer in den Niederlanden oder Polen sass,
    konnte sein Land schlicht nicht angeben.

    Genommen wird die Liste der Regionen mit **Anbieterdaten** (derzeit 139),
    nicht TMDBs vollstaendige Laenderliste. Die waere fast doppelt so lang und
    enthielte Laender, zu denen es zur Verfuegbarkeit nichts zu sagen gibt -
    ein Eintrag, hinter dem nichts steht, ist ein Versprechen ohne Deckung.

    Aendert sich praktisch nie und liegt deshalb lange im Zwischenspeicher.
    Scheitert TMDB, kommt eine leere Liste: Das Feld zeigt dann nur den
    aktuellen Wert, statt die ganze Seite mitzureissen.

    ⚠️ ``AdultUser`` und nicht ``CurrentUser``, obwohl der Rest von ``/config``
    fuer alle offen ist: Ein Kinderkonto hat keine Einstellungen, in denen es
    eine Region waehlen koennte. ``test_child_permissions`` besteht zu Recht
    darauf, dass jeder Pfad eine Entscheidung traegt.
    """
    settings = load_settings(db)

    async def beschaffen() -> list[dict[str, str]]:
        client = TmdbClient(
            api_key=settings.tmdb_api_key,
            language=settings.default_language,
            region=settings.default_region,
        )
        return [
            {
                "code": eintrag["iso_3166_1"],
                # ``english_name`` statt ``native_name``: Eine Liste, in der
                # "Deutschland" zwischen "Ελλάδα" und "日本" steht, laesst sich
                # weder ueberfliegen noch tippend durchsuchen.
                "name": eintrag.get("english_name") or eintrag["iso_3166_1"],
            }
            for eintrag in await client.watch_provider_regions()
            if eintrag.get("iso_3166_1")
        ]

    try:
        eintraege = await cache.cached(
            db, "config:regions", cache.GENRE_TTL, beschaffen
        )
    except TmdbError:
        return []

    return [RegionOut(**eintrag) for eintrag in eintraege]


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
            detail=meldungen.meldung(
                "demo_mode_invalid",
                "Demo-Modus muss 'auto', 'on' oder 'off' sein.",
            ),
        )
    for feld in (
        "movie_root_folder_mode",
        "series_root_folder_mode",
        "movie_profile_mode",
        "series_profile_mode",
        "movie_uhd_root_folder_mode",
        "series_uhd_root_folder_mode",
        "movie_uhd_profile_mode",
        "series_uhd_profile_mode",
    ):
        wert = getattr(payload, feld)
        if wert is not None and wert not in ("user", "fixed", "approver"):
            raise HTTPException(
                status_code=422,
                detail=meldungen.meldung(
                    "rule_invalid",
                    "Regel muss 'user', 'fixed' oder 'approver' sein.",
                ),
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
    for ordner_feld, profil_feld, art, stufe in (
        ("movie_root_folder_mode", "movie_profile_mode", "movie", "standard"),
        ("series_root_folder_mode", "series_profile_mode", "tv", "standard"),
        ("movie_uhd_root_folder_mode", "movie_uhd_profile_mode", "movie", "uhd"),
        ("series_uhd_root_folder_mode", "series_uhd_profile_mode", "tv", "uhd"),
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
        vorher = (
            aktuell.profile_mode(art, stufe)
            if offen.endswith("profile_mode")
            else aktuell.root_folder_mode(art, stufe)
        )
        if gesetzt == "approver":
            setattr(payload, offen, "approver")
        elif vorher == "approver":
            # Weg von "Entscheider": Das Gegenstueck darf nicht dort haengen
            # bleiben, sonst waere die neue Einstellung wieder wirkungslos.
            setattr(payload, offen, gesetzt)

    if payload.smtp_security is not None and payload.smtp_security not in mail.SECURITY_MODES:
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "encryption_invalid",
                "Verschlüsselung muss 'none', 'starttls' oder 'ssl' sein.",
            ),
        )
    # Dieselbe Adresse fuer beide Stufen ist immer ein Versehen: Man traegt die
    # 4K-Instanz ein, schreibt in Wahrheit weiter in die alte, und wundert sich,
    # warum 4K nie ankommt. Lieber jetzt widersprechen als still danebengehen.
    _gleiche_adresse_ablehnen(db, payload)
    if payload.smtp_from_address:
        if not mail.valid_address(payload.smtp_from_address):
            raise HTTPException(
                status_code=422,
                detail=meldungen.meldung(
                    "sender_address_invalid",
                    "Die Absenderadresse ist ungültig.",
                ),
            )
    if payload.public_url and not payload.public_url.strip().startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "public_url_needs_scheme",
                "Die öffentliche Adresse muss mit http:// oder https:// beginnen.",
            ),
        )
    if payload.webhook_basis_url and not payload.webhook_basis_url.strip().startswith(
        ("http://", "https://")
    ):
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "public_url_needs_scheme",
                "Die öffentliche Adresse muss mit http:// oder https:// beginnen.",
            ),
        )

    save_settings(db, payload.model_dump(exclude_unset=True))

    # Der Rueckkanal haengt an Adressen und Zugaengen von hier: beim naechsten
    # Rundgang pruefen statt erst zur vollen Stunde. Kein direkter Anstoss -
    # dieser Endpunkt ist synchron, siehe webhook_pflege.gleich_wieder.
    webhook_pflege.gleich_wieder()

    # ⚠️ Hier stand bis 0.19 der **Umschalt-Generalpardon**: Beim Wechsel der
    # Betriebsart starteten alle Konten bei null. Die Betriebsart gibt es nicht
    # mehr, also auch keinen Wechsel - das Zuruecksetzen ist jetzt ein
    # ausdruecklicher Knopf (POST /api/storage/konten/zuruecksetzen) und keine
    # Nebenwirkung des Speicherns. Eine Nebenwirkung, die die Zurechnung des
    # ganzen Hauses verwirft, gehoert nicht an eine Einstellungsseite.

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
        raise HTTPException(
            status_code=404,
            detail=meldungen.meldung(
                "setting_unknown",
                "Unbekannte Einstellung.",
            ),
        )

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


class PapierkorbInstanz(BaseModel):
    """Wie eine einzelne Instanz beim Loeschen mit Dateien umgeht."""

    media_type: str
    tier: str
    # "Radarr", "Radarr 4K", "Sonarr", "Sonarr 4K"
    name: str
    # ⚠️ Drei Zustaende, nicht zwei: "nicht erreichbar" ist etwas anderes als
    # "kein Papierkorb". Wer beides gleich behandelt, meldet einen Fehlalarm,
    # sobald Radarr gerade neu startet.
    reachable: bool
    path: str
    cleanup_days: int | None
    protected: bool


class PapierkorbStand(BaseModel):
    """Der Papierkorb aller eingerichteten Instanzen auf einen Blick."""

    # Der abgeleitete Haken: an, wenn **jede** eingerichtete Instanz einen
    # Papierkorb hat. Bewusst gerechnet und nicht gespeichert - so kann er nicht
    # von der Wirklichkeit abweichen, und er kippt von selbst, sobald eine neue
    # Instanz ohne Papierkorb dazukommt.
    enabled: bool
    # Konnte ueberhaupt jede Instanz gefragt werden? Ist das falsch, ist
    # ``enabled`` eine Aussage ueber unvollstaendige Auskunft - die Oberflaeche
    # sagt dann "unbekannt" statt "aus".
    complete: bool
    instances: list[PapierkorbInstanz]


@router.get("/settings/recyclebin", response_model=PapierkorbStand)
async def papierkorb_stand(admin: AdminUser, db: DbSession) -> PapierkorbStand:
    """Wo landen geloeschte Dateien - in jeder eingerichteten Instanz?

    **Der Stand wird bei jedem Aufruf frisch geholt und nirgends gespeichert.**
    Er steht in Radarr bzw. Sonarr, und nur dort; wuerde Nexview ihn
    aufbewahren, liefen die beiden auseinander, sobald jemand ihn drueben
    aendert - und dann hielte Nexview eine Loeschung fuer umkehrbar, die es
    nicht ist.

    Damit ueberlebt die Logik auch das **Hinzufuegen** einer Instanz: Wer
    naechste Woche ein zweites Sonarr eintraegt, findet es hier ohne Zutun, und
    ohne Papierkorb faellt ``enabled`` von selbst auf falsch. Genau das soll es:
    Eine neue Instanz ist eine neue Stelle, an der geloescht wird.
    """
    staende = await library.papierkoerbe(load_settings(db))
    instanzen = [
        PapierkorbInstanz(
            media_type=art,
            tier=stufe,
            name=name,
            reachable=stand.erreichbar,
            path=stand.path,
            cleanup_days=stand.cleanup_days,
            protected=stand.geschuetzt,
        )
        for art, stufe, name, stand in staende
    ]
    return PapierkorbStand(
        # Ohne eine einzige Instanz gibt es nichts zu schuetzen - und "an"
        # waere dann eine Behauptung ueber das Nichts.
        enabled=bool(instanzen) and all(zeile.protected for zeile in instanzen),
        complete=all(zeile.reachable for zeile in instanzen),
        instances=instanzen,
    )


class PapierkorbWunsch(BaseModel):
    """Was fuer **eine** Instanz eingestellt werden soll."""

    media_type: Literal["movie", "tv"]
    tier: Literal["standard", "uhd"]
    # Leer heisst: Papierkorb abschalten. Das ist die gefaehrliche Richtung -
    # ab dann loescht die Instanz sofort und endgueltig.
    path: str = ""


class PapierkorbAenderung(BaseModel):
    """Der ganze Abschnitt auf einmal - alle Instanzen in einem Zug.

    Bewusst nicht je Instanz einzeln: Halb geschuetzt ist kein Zustand, den
    jemand absichtlich haben will, und wer vier Knoepfe nacheinander drueckt,
    hat zwischendurch genau das. Ein Speichern-Knopf, ein Ergebnis.
    """

    instances: list[PapierkorbWunsch]
    # Global, eine Zahl fuer alle. Mindestens ein Tag: Ob Radarr die Null als
    # "nie aufraeumen" oder "sofort" versteht, ist nicht dokumentiert - und bei
    # einem Papierkorb ist diese Verwechslung fatal.
    cleanup_days: Annotated[int, Field(ge=1, le=365)] = 7


class PapierkorbOrdner(BaseModel):
    """Eine Ebene der Ordner-Auswahl, aus Sicht **einer** Instanz."""

    path: str
    directories: list[str]


@router.get("/settings/recyclebin/folders", response_model=PapierkorbOrdner)
async def papierkorb_ordner(
    admin: AdminUser,
    db: DbSession,
    media_type: Literal["movie", "tv"],
    tier: Literal["standard", "uhd"] = "standard",
    path: str = "/",
) -> PapierkorbOrdner:
    """Welche Ordner sieht **diese** Instanz unter diesem Pfad?

    Je Instanz gefragt und nicht einmal fuer alle: Sonarr kann voellig anders
    eingebunden sein als Radarr. Ein geratener Pfad fuehrt dazu, dass die
    Instanz spaeter an eine Stelle loescht, die es bei ihr gar nicht gibt.
    """
    ordner = await library.ordner(load_settings(db), media_type, tier, path)
    return PapierkorbOrdner(path=path, directories=ordner)


@router.put("/settings/recyclebin", response_model=PapierkorbStand)
async def papierkorb_setzen(
    aenderung: PapierkorbAenderung, admin: AdminUser, db: DbSession
) -> PapierkorbStand:
    """Papierkorb in allen genannten Instanzen eintragen.

    ⚠️ **Das schreibt in Radarr und Sonarr, nicht in Nexview.** Die Einstellung
    gilt dort fuer **alles**, auch fuer Loeschungen, die nichts mit Nexview zu
    tun haben - wer einen Film von Hand in Radarr entfernt, findet ihn ab dann
    ebenfalls im Korb. Das ist sicherer, aber es ist eine Verhaltensaenderung
    am fremden Dienst, und die Oberflaeche sagt das dazu.

    Scheitert eine Instanz, bricht der ganze Vorgang ab und meldet **welche**.
    Die uebrigen bleiben, wie sie waren: Ein halb geschriebener Zustand waere
    schlimmer als gar keiner, weil danach niemand mehr weiss, was gilt.
    """
    settings = load_settings(db)

    for wunsch in aenderung.instances:
        if not settings.arr_configured(wunsch.media_type, wunsch.tier):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=meldungen.meldung(
                    "instance_not_configured",
                    "Diese Instanz ist gar nicht eingerichtet.",
                ),
            )
        try:
            await library.papierkorb_setzen(
                settings,
                wunsch.media_type,
                wunsch.tier,
                pfad=wunsch.path.strip(),
                tage=aenderung.cleanup_days,
            )
        except ArrError as fehler:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"{wunsch.media_type}/{wunsch.tier}: {fehler.message}",
            ) from fehler

    # Den frisch gelesenen Stand zurueckgeben, nicht den gewuenschten: Was
    # wirklich gilt, steht in Radarr - und nur das soll die Oberflaeche zeigen.
    return await papierkorb_stand(admin, db)


class PapierkorbInhaltInstanz(BaseModel):
    name: str
    path: str
    # Nur die Ordnernamen, so wie die Instanz sie fuehrt.
    entries: list[str]
    # Wurde die Liste gekuerzt? Dann steht das dabei, statt so zu tun, als
    # waere das alles.
    truncated: bool = False


class PapierkorbInhalt(BaseModel):
    instances: list[PapierkorbInhaltInstanz]


# Wieviele Eintraege je Instanz hoechstens gezeigt werden. Ein Papierkorb mit
# dreihundert Ordnern wird nicht dadurch nuetzlicher, dass man alle auflistet.
_HOECHSTENS = 200


@router.get("/settings/recyclebin/contents", response_model=PapierkorbInhalt)
async def papierkorb_inhalt(
    admin: AdminUser,
    db: DbSession,
    media_type: Literal["movie", "tv"] | None = None,
    tier: Literal["standard", "uhd"] | None = None,
    path: str = "",
) -> PapierkorbInhalt:
    """Was liegt gerade im Papierkorb - je Instanz?

    **Nur die Ordnernamen.** Kein Plakat, kein aufgeraeumter Titel.

    ⚠️ Der naheliegende Weg waere, die TMDB-Nummer aus dem Ordnernamen zu lesen
    (``The Matrix (1999) {tmdb-603}``) und damit ein Plakat zu holen. Das
    funktioniert - **aber nur, wenn das Benennungsschema die Nummer enthaelt.**
    Sie steht dort nicht von Natur aus, sondern weil jemand sein Schema so
    eingerichtet hat. Wer das anders haelt, saehe ueberall "kein Plakat", und
    eine Ansicht, die bei der Haelfte der Installationen leer aussieht, ist
    keine Ansicht.

    Der Ordnername steht dagegen immer da und sagt genug: welcher Titel, welche
    Fassung, wie viele.

    **Nur lesen.** Zurueckholen kann Nexview nicht: Dafuer muessten Dateien
    verschoben werden, und Nexview sieht das Dateisystem gar nicht - es spricht
    ausschliesslich ueber die API mit Radarr und Sonarr.
    """
    settings = load_settings(db)
    instanzen: list[PapierkorbInhaltInstanz] = []

    for art, stufe, name, stand in await library.papierkoerbe(settings):
        # Auf eine Instanz einschraenken, wenn danach gefragt wird.
        if media_type and (art != media_type or stufe != tier):
            continue

        # ``path`` erlaubt es, in einen **noch nicht gespeicherten** Ordner zu
        # schauen. Den gibt es ja bereits - Radarr fuehrt ihn nur noch nicht
        # als Papierkorb. Wer einen Ordner aussucht, will vorher hineinsehen,
        # und ihn dafuer erst speichern zu muessen waere die falsche
        # Reihenfolge.
        gewaehlt = path.strip() or stand.path
        if not gewaehlt or not stand.erreichbar:
            continue

        pfade = await library.ordner(settings, art, stufe, gewaehlt)
        instanzen.append(
            PapierkorbInhaltInstanz(
                name=name,
                path=gewaehlt,
                entries=[voll.rstrip("/").rsplit("/", maxsplit=1)[-1] for voll in pfade[:_HOECHSTENS]],
                truncated=len(pfade) > _HOECHSTENS,
            )
        )

    return PapierkorbInhalt(instances=instanzen)


# --- Rueckkanal (Webhook) je Instanz ----------------------------------------


class WebhookInstanzStand(BaseModel):
    """Der ehrliche Zustand einer Instanz - wie er auf der Diensteseite steht."""

    kennung: str
    name: str
    media_type: str
    tier: str
    aktiv: bool
    # Steht unser Eintrag gerade in Radarr/Sonarr?
    eingetragen: bool
    bewiesen_am: datetime | None
    zuletzt_angerufen_am: datetime | None
    letztes_ereignis: str
    geprueft_am: datetime | None
    # Kennung des Hindernisgrunds ("no_address", "too_old", "proof_failed",
    # "unreachable", "create_failed") - uebersetzt im Frontend; dazu ein roher
    # Zusatz (Version, fehlende Faehigkeiten), der nicht uebersetzt wird.
    fehler: str
    fehler_info: str


class WebhookStand(BaseModel):
    # Von wo aus Radarr/Sonarr anrufen - leer, wenn keine Adresse gesetzt ist.
    basis: str
    instanzen: list[WebhookInstanzStand]


def _webhook_stand(db, settings) -> WebhookStand:
    zeilen = []
    for instanz in settings.arr_instanzen():
        zeile = webhooks.eintrag(db, instanz.kennung)
        zeilen.append(
            WebhookInstanzStand(
                kennung=instanz.kennung,
                name=instanz.name,
                media_type=instanz.media_type,
                tier=instanz.tier,
                # Vorgabe an: Eine Instanz ohne Zustand hat noch nie eine
                # Pflege gesehen - der Haken gilt als gesetzt, ausgefuehrt
                # wird beim naechsten Rundgang.
                aktiv=zeile.aktiv if zeile else True,
                eingetragen=bool(zeile and zeile.eintrag_id is not None),
                bewiesen_am=zeile.bewiesen_am if zeile else None,
                zuletzt_angerufen_am=zeile.zuletzt_angerufen_am if zeile else None,
                letztes_ereignis=zeile.letztes_ereignis if zeile else "",
                geprueft_am=zeile.geprueft_am if zeile else None,
                fehler=zeile.fehler if zeile else "",
                fehler_info=zeile.fehler_info if zeile else "",
            )
        )
    return WebhookStand(basis=settings.webhook_basis, instanzen=zeilen)


def _webhook_instanz(settings, kennung: str):
    instanz = next(
        (i for i in settings.arr_instanzen() if i.kennung == kennung), None
    )
    if instanz is None:
        raise meldungen.fehler(
            "webhook_unknown_instance",
            "Zu dieser Kennung ist keine Instanz eingerichtet.",
            status.HTTP_404_NOT_FOUND,
        )
    return instanz


@router.get("/settings/webhooks", response_model=WebhookStand)
async def webhook_stand(admin: AdminUser, db: DbSession) -> WebhookStand:
    """Der Rueckkanal-Zustand aller eingerichteten Instanzen.

    Nur lesen, nichts anfassen: Die Wahrheit ueber "bewiesen" und "zuletzt
    angerufen" schreibt der Empfaenger (routers/webhooks), die ueber den
    Eintrag selbst die Pflege (services/webhook_pflege).
    """
    return _webhook_stand(db, load_settings(db))


class WebhookHaken(BaseModel):
    aktiv: bool


@router.patch("/settings/webhooks/{kennung}", response_model=WebhookStand)
async def webhook_haken(
    kennung: str, payload: WebhookHaken, admin: AdminUser, db: DbSession
) -> WebhookStand:
    """Den Haken "Webhook fuer Rueckkanal nutzen" umlegen - mit sofortiger Tat.

    Einschalten heisst: Probe, Beweis, Eintrag anlegen. Abwaehlen heisst:
    unseren Eintrag in Radarr/Sonarr rueckstandsfrei entfernen. Beides laeuft
    noch in dieser Anfrage, damit die Antwort den wirklichen Zustand traegt -
    die Sekunden Wartezeit sind hier Ehrlichkeit, keine Traegheit.
    """
    settings = load_settings(db)
    instanz = _webhook_instanz(settings, kennung)
    zeile = webhooks.eintrag_sicherstellen(db, kennung)
    zeile.aktiv = payload.aktiv
    db.commit()
    await webhook_pflege.instanz_pflegen(db, settings, instanz)
    return _webhook_stand(db, load_settings(db))


class WebhookProbe(BaseModel):
    angekommen: bool
    dauer_ms: int | None = None
    fehler: str | None = None
    info: str | None = None


class VerbindungInstanz(BaseModel):
    kennung: str
    name: str
    erreichbar: bool
    version: str = ""


class VerbindungStand(BaseModel):
    instanzen: list[VerbindungInstanz]


@router.get("/settings/instanzen/verbindung", response_model=VerbindungStand)
async def instanzen_verbindung(admin: AdminUser, db: DbSession) -> VerbindungStand:
    """Sind die Instanzen gerade erreichbar? Live gefragt, nichts gespeichert.

    Fuer die Statusleuchte auf den Kacheln - deshalb alle gleichzeitig und
    mit kurzem Atem: Eine stumme Instanz darf die Antwort der anderen nicht
    festhalten, und eine Leuchte, die fuenfzehn Sekunden nachdenkt, beruhigt
    niemanden.
    """
    settings = load_settings(db)
    kurzer_atem = httpx.Timeout(4.0, connect=3.0)

    async def pruefen(instanz) -> VerbindungInstanz:
        client = ArrClient(instanz.url, instanz.api_key, instanz.name)
        try:
            status = await client.system_status(timeout=kurzer_atem)
        except ArrError:
            return VerbindungInstanz(
                kennung=instanz.kennung, name=instanz.name, erreichbar=False
            )
        return VerbindungInstanz(
            kennung=instanz.kennung,
            name=instanz.name,
            erreichbar=True,
            version=str(status.get("version") or ""),
        )

    ergebnisse = await asyncio.gather(
        *(pruefen(instanz) for instanz in settings.arr_instanzen())
    )
    return VerbindungStand(instanzen=list(ergebnisse))


class GesundheitProblem(BaseModel):
    typ: str
    text: str


class GesundheitInstanz(BaseModel):
    kennung: str
    name: str
    probleme: list[GesundheitProblem]
    aktualisiert_am: datetime | None


class GesundheitStand(BaseModel):
    instanzen: list[GesundheitInstanz]


@router.get("/settings/instanzen/gesundheit", response_model=GesundheitStand)
async def instanzen_gesundheit(admin: AdminUser, db: DbSession) -> GesundheitStand:
    """Was die Instanzen selbst als Problem melden - je Instanz.

    Gelesen wird der zuletzt gesehene Stand (der Rundgang holt ihn jede
    Runde frisch); die Texte kommen im Wortlaut der Instanz und werden
    bewusst nicht uebersetzt.
    """
    settings = load_settings(db)
    zeilen = []
    for instanz in settings.arr_instanzen():
        zeile = instanz_gesundheit.eintrag(db, instanz.kennung)
        zeilen.append(
            GesundheitInstanz(
                kennung=instanz.kennung,
                name=instanz.name,
                probleme=[
                    GesundheitProblem(
                        typ=str(p.get("typ") or "warning"),
                        text=str(p.get("text") or ""),
                    )
                    for p in (zeile.stand if zeile else None) or []
                ],
                aktualisiert_am=zeile.aktualisiert_am if zeile else None,
            )
        )
    return GesundheitStand(instanzen=zeilen)


# Welche Einstellungs-Schluessel zu einer Instanz gehoeren - fuers Entfernen.
# Die 4K-Regeln gehen zurueck auf "" (= erben wieder von der Standard-Instanz);
# die Standard-Regeln bleiben stehen, sie sind eine Regel des Hauses.
INSTANZ_FELDGRUPPEN: dict[str, dict[str, object]] = {
    "radarr-standard": {
        "url": "radarr_url", "key": "radarr_api_key", "name": "radarr_name",
        "leeren": ["default_movie_profile_id", "default_movie_root"],
    },
    "radarr-uhd": {
        "url": "radarr_uhd_url", "key": "radarr_uhd_api_key", "name": "radarr_uhd_name",
        "leeren": [
            "default_movie_uhd_profile_id", "default_movie_uhd_root",
            "movie_uhd_profile_mode", "movie_uhd_root_folder_mode",
        ],
    },
    "sonarr-standard": {
        "url": "sonarr_url", "key": "sonarr_api_key", "name": "sonarr_name",
        "leeren": ["default_series_profile_id", "default_series_root"],
    },
    "sonarr-uhd": {
        "url": "sonarr_uhd_url", "key": "sonarr_uhd_api_key", "name": "sonarr_uhd_name",
        "leeren": [
            "default_series_uhd_profile_id", "default_series_uhd_root",
            "series_uhd_profile_mode", "series_uhd_root_folder_mode",
        ],
    },
}


@router.delete("/settings/instanzen/{kennung}")
async def instanz_entfernen(
    kennung: str, admin: AdminUser, db: DbSession
) -> dict[str, object]:
    """Nexviews Zugang zu dieser Instanz entfernen - mehr nicht.

    In Radarr/Sonarr selbst passiert nichts: Downloads und Suchlaeufe dort
    laufen weiter. Eine Ausnahme: Unser Webhook-Eintrag wird vorher
    rueckstandsfrei mit entfernt - sonst riefe er fuer immer ins Leere und
    stuende drueben als krank. Laufende Anfragen der Instanz bleiben bewusst
    stehen (kein Massen-Abbruch, dieselbe Regel wie beim Ausfall einer
    Quelle im Status-Abgleich); die Speicher-Posten uebernimmt der naechste
    Abgleich: Was der Medienserver weiter meldet, bleibt - nur nicht mehr
    ueber Nexview loeschbar -, was allein die Instanz kannte, verschwindet
    aus der Zurechnung.
    """
    settings = load_settings(db)
    instanz = _webhook_instanz(settings, kennung)
    felder = INSTANZ_FELDGRUPPEN[kennung]

    zeile = webhooks.eintrag(db, kennung)
    if zeile is not None:
        # Abwaehlen und einmal pflegen raeumt den Eintrag drueben weg - so
        # gut es geht: Eine gerade stumme Instanz haelt das Entfernen nicht
        # auf, dann bleibt ihr Eintrag eben stehen.
        zeile.aktiv = False
        db.commit()
        try:
            await webhook_pflege.instanz_pflegen(db, settings, instanz)
        except Exception:  # noqa: BLE001 - Aufraeumen ist Beiwerk des Entfernens
            logger.warning(
                "Webhook entry in %s could not be removed while deleting the instance",
                instanz.name,
            )
        rest = webhooks.eintrag(db, kennung)
        if rest is not None:
            db.delete(rest)
    gesund = instanz_gesundheit.eintrag(db, kennung)
    if gesund is not None:
        db.delete(gesund)
    db.commit()

    clear_secret(db, felder["key"])
    save_settings(
        db,
        {felder["url"]: "", felder["name"]: "", **{f: "" for f in felder["leeren"]}},
    )
    library.invalidate()
    logger.info("Instance access removed: %s", instanz.name)
    return public_settings(db)


@router.post("/settings/webhooks/{kennung}/testen", response_model=WebhookProbe)
async def webhook_testen(
    kennung: str, admin: AdminUser, db: DbSession
) -> WebhookProbe:
    """Der Testen-Knopf: die Instanz jetzt einmal anrufen lassen.

    Beweist die ganze Strecke - Nexview bittet Radarr/Sonarr um die Probe,
    die Instanz ruft unsere Anruf-Adresse, der Empfaenger vermerkt den
    Beweis. Die Antwort sagt ehrlich, ob und wie schnell der Anruf ankam,
    oder woran es haengt.
    """
    settings = load_settings(db)
    instanz = _webhook_instanz(settings, kennung)
    ergebnis = await webhook_pflege.testen(db, settings, instanz)
    return WebhookProbe(**ergebnis)
