"""Einstellungen: TMDB-, Radarr-, Sonarr- und Mailzugang. Nur fuer Administratoren.

Die Adressen, die nur Radarr und Sonarr betreffen (Verbindung pruefen,
Papierkorb, Rueckkanal, Instanzen), stehen hinter der Grenze in
``services/beschaffung/arr/router_einstellungen.py`` - unter denselben Pfaden.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

from .. import meldungen
from ..deps import AdminUser, AdultUser, CurrentUser, DbSession
from ..models import Hausordnung, User
from ..schemas import MIN_PASSWORD_LENGTH
from ..services import beschaffung, cache, fassungen, mail, mail_templates
from ..services.mediaserver import (
    PROVIDERS,
    merklisten_anbieter,
    verbundene_anbieter,
)
from ..services.settings_service import (
    SECRET_KEYS,
    AppSettings,
    clear_secret,
    load_settings,
    public_settings,
    save_settings,
)
from ..services.tmdb import TmdbClient, TmdbError

router = APIRouter(prefix="/api", tags=["settings"])

#: Die Betriebsarten der Beschaffung, wie die Grenze sie kennt.
BESCHAFFUNGSARTEN = frozenset(beschaffung.providers())

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
    # Die Betriebsart der Beschaffung: ``arr`` oder ``nex`` (Bauplan
    # Abschnitt 4). Der Umstiegsassistent kommt spaeter; bis dahin stellt sie
    # ein Administrator hier um.
    beschaffung: str | None = None
    nexcrate_url: str | None = None
    nexcrate_api_key: str | None = None
    nexcrate_name: str | None = Field(default=None, max_length=60)
    #: Den Namen des Anfragenden in der Herkunftsmarke mitgeben (N19).
    nexcrate_anzeigename: bool | None = None
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


class FassungOeffentlich(BaseModel):
    """Eine Fassung, so wie die Oberflaeche sie anbietet."""

    kennung: str
    media_type: str
    name: str
    #: ``hd``, ``uhd`` oder keine - die Oberflaeche zeigt ``uhd`` als "4K".
    klasse: str | None = None
    #: ``arr`` oder ``nex``.
    quelle: str
    #: Die Hauptachse dieser Medienart - ihr Zustand steht in ``status``.
    haupt: bool = False
    #: Ist die Quelle dahinter eingerichtet? Die Hauptfassung steht auch ohne
    #: sie in der Liste.
    bereit: bool = True
    #: Darf jeder sie anfragen? Dann gibt es am Konto keinen Haken dafuer.
    offen_fuer_alle: bool = False
    #: Waehlt erst der Entscheider Ordner und Profil?
    approver_picks_target: bool = False
    #: Darf **dieses** Konto sie anfragen? Die ganze Leiter aus Bauplan 2.3 -
    #: die Oberflaeche rechnet sie nicht nach.
    darf_anfragen: bool = False


class AppConfig(BaseModel):
    """Was die Oberflaeche ueber die Konfiguration wissen muss."""

    default_region: str
    default_language: str
    tmdb_configured: bool
    #: Ueber welchen Weg diese Installation beschafft: ``arr`` oder ``nex``
    #: (Bauplan NEX-Modus, Abschnitt 4). Die Oberflaeche blendet danach die
    #: Betreiberwerkzeuge der anderen Betriebsart aus.
    beschaffung: str
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
    #: Die Fassungen, in denen ein Titel vorliegen kann - je Medienart die
    #: Hauptfassung zuerst, dann jede weitere eingerichtete (Bauplan
    #: NEX-Modus, Abschnitt 2). Daraus baut die Oberflaeche ihre Auswahl; die
    #: vier Felder darunter sind die alte Form derselben Auskunft.
    fassungen: list[FassungOeffentlich]
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
    # --- Hausordnung --------------------------------------------------------
    #
    # ⚠️ **Hier und nicht in einem eigenen Endpunkt.** Der Knopf unten rechts
    # steht auf jeder Seite; eine eigene Abfrage dafuer waere ein zusaetzlicher
    # Aufruf bei jedem Seitenaufbau. Diese hier laeuft ohnehin.
    #
    # ``hausordnung_gelesen`` traegt die quittierte Fassung des **aufrufenden**
    # Kontos - daraus ergibt sich, ob der Knopf einen Punkt traegt.
    hausordnung_vorhanden: bool = False
    hausordnung_titel: str = ""
    hausordnung_fassung: int = 0
    hausordnung_quittierbar: bool = True
    hausordnung_gelesen: int | None = None


def _fassungen_oeffentlich(
    db: DbSession, settings: AppSettings, user: User
) -> list[FassungOeffentlich]:
    """Jede Fassung, die die Oberflaeche anbieten kann - je Medienart.

    Die Hauptfassung steht immer dabei, auch ohne eingerichtete Instanz: Sie
    ist es, die eine Anfrage ohne Angabe bekommt, und das Formular sah auch
    vor den Fassungen so aus. ``bereit`` sagt, ob ihre Instanz steht.
    """
    eintraege: list[FassungOeffentlich] = []
    for art in ("movie", "tv"):
        haupt = fassungen.hauptkennung(art)
        eingerichtet = settings.fassungen_fuer(art)
        kennungen = [haupt, *(f.kennung for f in eingerichtet if f.kennung != haupt)]
        for kennung in kennungen:
            info = fassungen.info(settings, kennung)
            eintraege.append(
                FassungOeffentlich(
                    kennung=kennung,
                    media_type=art,
                    name=info.name,
                    klasse=info.klasse,
                    quelle=info.quelle,
                    haupt=kennung == haupt,
                    bereit=settings.fassung(kennung) is not None,
                    offen_fuer_alle=fassungen.offen_fuer_alle(db, kennung),
                    approver_picks_target=settings.approver_picks_target(
                        art, fassungen.stufe(kennung)
                    ),
                    darf_anfragen=fassungen.darf_anfragen(db, user, kennung),
                )
            )
    return eintraege


def _hausordnung_stand(db: DbSession, user: User) -> dict:
    """Was die Oberflaeche ueber die Hausordnung wissen muss.

    Ein leeres Ergebnis heisst "es gibt keine" - dann greifen die Vorgaben des
    Schemas, und weder der Knopf noch der Fusszeilen-Verweis erscheinen.

    ⚠️ **Kinderkonten und Administratoren bekommen sie nicht.** Die einen
    sehen die Hausordnung nie - ihr Rahmen hat den Knopf ohnehin nicht -, die
    anderen schreiben sie: Ein Knopf, der den Betreiber an seinen eigenen Text
    erinnert, ist Zeremonie. Wer dazugehoert, steht an genau einer Stelle:
    ``routers/hausordnung.UNBETEILIGT``.
    """
    from .hausordnung import _geht_es_an

    ordnung = db.get(Hausordnung, 1)
    if ordnung is None or not ordnung.veroeffentlicht or not _geht_es_an(user):
        return {}
    return {
        "hausordnung_vorhanden": True,
        "hausordnung_titel": ordnung.titel,
        "hausordnung_fassung": ordnung.fassung,
        "hausordnung_quittierbar": ordnung.quittierbar,
        "hausordnung_gelesen": user.hausordnung_gelesen,
    }


@router.get("/config", response_model=AppConfig)
def read_config(user: CurrentUser, db: DbSession) -> AppConfig:
    settings = load_settings(db)
    return AppConfig(
        default_region=settings.default_region,
        default_language=settings.default_language,
        tmdb_configured=settings.tmdb_configured,
        beschaffung=settings.beschaffung,
        radarr_configured=settings.radarr_configured,
        sonarr_configured=settings.sonarr_configured,
        using_demo_data=settings.use_demo_data,
        min_password_length=MIN_PASSWORD_LENGTH,
        mail_configured=settings.mail_configured,
        public_url_set=bool(settings.public_url),
        fassungen=_fassungen_oeffentlich(db, settings, user),
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
        **_hausordnung_stand(db, user),
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
                detail=meldungen.meldung(
                    "arr_uhd_same_url",
                    f"Die 4K-Instanz von {name} hat dieselbe Adresse wie die normale. "
                    "Es müssen zwei getrennte Server sein.",
                    service=name,
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
    if payload.beschaffung is not None and payload.beschaffung not in BESCHAFFUNGSARTEN:
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(
                "beschaffung_invalid",
                "Die Betriebsart der Beschaffung ist unbekannt.",
                arten=sorted(BESCHAFFUNGSARTEN),
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
                    detail=meldungen.meldung(
                        "target_and_profile_together",
                        "Zielordner und Qualitätsprofil gehören zusammen: „Der "
                        "Entscheider wählt“ gilt entweder für beide oder für keines.",
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
    if payload.smtp_from_address and not mail.valid_address(payload.smtp_from_address):
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
    beschaffung.rueckkanal_bald_pflegen()

    # ⚠️ Hier stand bis 0.19 der **Umschalt-Generalpardon**: Beim Wechsel der
    # Betriebsart starteten alle Konten bei null. Die Betriebsart gibt es nicht
    # mehr, also auch keinen Wechsel - das Zuruecksetzen ist jetzt ein
    # ausdruecklicher Knopf (POST /api/storage/konten/zuruecksetzen) und keine
    # Nebenwirkung des Speicherns. Eine Nebenwirkung, die die Zurechnung des
    # ganzen Hauses verwirft, gehoert nicht an eine Einstellungsseite.

    # Alte Ergebnisse verwerfen: Region, Sprache oder Key koennten sich
    # geaendert haben.
    cache.clear_all(db)
    beschaffung.bestand_verwerfen()
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
    beschaffung.bestand_verwerfen()
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
