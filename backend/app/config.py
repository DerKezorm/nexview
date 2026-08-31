"""Technische Konfiguration von Nexview.

Hier stehen bewusst nur Betriebs-Einstellungen (Pfade, Token-Laufzeiten).
Die API-Keys von TMDB/Radarr/Sonarr liegen verschluesselt in der Datenbank
und werden ueber die Einstellungsseite gepflegt.
"""

from __future__ import annotations

import logging
import re
import secrets
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("nexview.config")

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXVIEW_",
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = ""
    data_dir: Path = PROJECT_ROOT / "data"
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    static_dir: str = ""

    # Unterpfad, unter dem Nexview hinter einem Reverse Proxy laeuft -
    # z. B. ``/nexview`` fuer ``https://example.com/nexview/``.
    #
    # Leer (Vorgabe) heisst: Nexview wohnt an der Wurzel, alles bleibt exakt
    # wie bisher. Ist der Pfad gesetzt, beantwortet Nexview jede Adresse
    # **doppelt** - mit und ohne Vorbau. Beides ist noetig, weil Proxys auf
    # zwei Arten eingestellt sind: Die einen reichen den Pfad mitsamt Vorbau
    # durch, die anderen schneiden ihn vorher ab. Ausserdem rufen der
    # Docker-Healthcheck und die Radarr-/Sonarr-Webhooks den Server direkt an
    # der Wurzel an. Details und Proxy-Beispiele stehen in der README unter
    # "Behind a reverse proxy".
    #
    # Der Wert wird beim Einlesen aufgeraeumt (fuehrender Schraegstrich wird
    # ergaenzt, ein abschliessender entfernt, eine versehentlich eingetragene
    # ganze Adresse auf ihren Pfad verkuerzt). ``/api`` ist als Vorbau
    # verboten - er wuerde mit der Schnittstelle selbst zusammenfallen.
    url_base: str = ""

    # Woher die Adresse des Anfragenden kommt - gebraucht von der
    # ``anmeldebremse``, die nach Konto **und** Adresse zaehlt.
    #
    # Leer lassen ist die sichere Vorgabe: Dann zaehlt nur das Konto. Nexview
    # weiss von sich aus naemlich nicht, ob es direkt am Netz haengt oder
    # hinter einem Reverse Proxy - und hinter einem Proxy sehen alle Anfragen
    # aus wie dieselbe Adresse. Eine Sperre nach Adresse wuerde dort beim
    # ersten Vertipper den ganzen Haushalt aussperren.
    #
    # Erlaubt: direct | proxy | proxy:<Zahl>
    client_ip: str = ""

    # Ob das Sitzungs-Cookie das Merkmal ``Secure`` traegt - also nur ueber
    # HTTPS mitgeschickt wird.
    #
    # ``auto`` (Vorgabe) setzt es genau dann, wenn **diese Anfrage** ueber
    # https hereinkam. Das kann nie etwas kaputtmachen: Wer Nexview unter
    # ``http://192.168.0.10:8080`` betreibt, bekommt ein Cookie ohne Secure
    # und bleibt angemeldet - ein Secure-Cookie wuerde der Browser dort
    # wegwerfen, und niemand kaeme mehr hinein.
    #
    # Der Preis der Vorgabe: Steht ein Reverse Proxy davor, der HTTPS
    # abschliesst und intern schlichtes http weiterreicht, sieht Nexview
    # ``http`` und laesst Secure weg. Das Cookie funktioniert trotzdem, ist
    # aber nicht dagegen geschuetzt, versehentlich ueber eine unverschluesselte
    # Verbindung zum selben Rechner zu wandern. Wer HTTPS erzwingt, setzt
    # deshalb ``NEXVIEW_COOKIE_SECURE=on``.
    #
    # ⚠️ Bewusst wird **nicht** ``X-Forwarded-Proto`` geraten. Der Kopf ist
    # genauso faelschbar wie ``X-Forwarded-For``, und bei der Anmeldebremse hat
    # sich Nexview schon einmal gegen das Raten und fuer das Fragen
    # entschieden. Zwei Stellen mit derselben Frage sollen dieselbe Antwort
    # geben.
    #
    # Erlaubt: auto | on | off
    cookie_secure: str = "auto"

    # Die Inhaltsregeln der Seite (Content-Security-Policy). Sie sagen dem
    # Browser, woher er laden und wohin er schicken darf; alles andere lehnt
    # er ab. Was sie bringt und was nicht, steht in ``services/csp.py``.
    #
    #   on           Regeln gelten (Vorgabe)
    #   report-only  Regeln werden nur **gemeldet**, nicht durchgesetzt -
    #                fuer alle, die erst in der Browser-Konsole nachsehen
    #                wollen, bevor sie scharf schalten
    #   off          gar keine Kopfzeile
    #
    # ⚠️ Der Notausschalter ist kein Zierrat: Eine zu enge Regel zeigt keine
    # Fehlermeldung, sondern eine halb geladene Seite.
    csp: str = "on"

    # Wer Nexview in einen Rahmen stecken darf. Vorgabe ``none`` - niemand.
    #
    # Wer Nexview in einem Uebersichts-Brett wie Organizr eingebettet hat,
    # setzt hier ``self`` oder die Adresse des Bretts. Sonst bleibt der Rahmen
    # dort leer, und zwar **ohne jede Fehlermeldung** - der Browser sagt es nur
    # in seiner Konsole.
    frame_ancestors: str = "none"

    # Zusaetzliche Bildquellen, mit Leerzeichen getrennt.
    #
    # Gebraucht, wenn die Poster im Kalender leer bleiben: Nexview reicht dort
    # die Adressen durch, die Radarr/Sonarr gespeichert haben, und die haengen
    # am Metadaten-Anbieter des Hauses. Die ueblichen sind schon erlaubt
    # (``services/csp.py``); wer einen anderen benutzt, traegt ihn hier nach.
    #
    # Beispiel: NEXVIEW_IMG_SOURCES=https://bilder.example.org
    img_sources: str = ""

    # Notausgang fuer die Protokoll-Stufe: Ist ``NEXVIEW_LOG_LEVEL`` gesetzt,
    # gilt sie und die in der Oberflaeche gewaehlte Stufe wird ignoriert.
    # Gebraucht, wenn die Anwendung gar nicht erst startet - dann kommt man an
    # keine Oberflaeche, um die Diagnose einzuschalten.
    # Erlaubt: quiet | normal | detailed | trace (auch WARNING/INFO/DEBUG).
    log_level: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("url_base", mode="before")
    @classmethod
    def _normalize_url_base(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        wert = value.strip()
        if not wert:
            return ""
        if "://" in wert:
            # Wer die ganze Adresse eintraegt, meint den Pfad darin.
            wert = urlsplit(wert).path
            logger.warning(
                "NEXVIEW_URL_BASE looks like a full address; using only its "
                "path %r. The setting takes just the path, e.g. /nexview.",
                wert,
            )
        wert = "/" + wert.strip("/")
        if wert == "/":
            return ""
        # Nur Zeichen, die in einem URL-Pfad nichts Besonderes bedeuten.
        # Alles andere (Leerzeichen, Doppelpunkte, ...) ist kein Unterpfad,
        # sondern ein Versehen - etwa ein von der Windows-Shell in einen
        # Laufwerkspfad verwandelter Wert. Stumm ausgeliefert ergaebe das
        # eine halb kaputte Seite ohne jede Fehlermeldung.
        if not re.fullmatch(r"[A-Za-z0-9/_.-]+", wert):
            logger.warning(
                "NEXVIEW_URL_BASE %r contains characters that do not belong "
                "in a URL path. Ignoring the setting; use something like "
                "/nexview.",
                wert,
            )
            return ""
        if wert == "/api" or wert.startswith("/api/"):
            logger.warning(
                "NEXVIEW_URL_BASE must not be %r - it would collide with the "
                "API itself. Ignoring the setting; pick a prefix like /nexview.",
                wert,
            )
            return ""
        return wert

    # Der Nothammer im Glaskasten: Wer sich als Betreiber aussperrt - Passwort
    # weg, Anmeldedienst tot, Konto unbrauchbar -, traegt hier den
    # **Benutzernamen** ein und startet den Behaelter neu. Dieses Konto traegt
    # danach den Betreiber-Haken.
    #
    # Bei einer neuen Installation braucht es die Variable nicht: Das Konto aus
    # dem Einrichtungsassistenten bekommt den Haken von selbst. Sie steht
    # normalerweise nirgends.
    #
    # ⚠️ **Sie wird bei jedem Start gelesen, nicht einmal.** Bleibt sie stehen,
    # ist der Betreiber dauerhaft von aussen festgelegt - die Uebergabe in der
    # Oberflaeche wird dann ausdruecklich verweigert (siehe
    # ``services/betreiber.py``), statt beim naechsten Neustart still
    # zurueckzuspringen.
    #
    # ⚠️ **Und die Abgrenzung, die ausgesprochen sein muss:** Der Betreiber-
    # Haken schuetzt gegen einen zweiten *Administrator*, nicht gegen jemanden
    # mit Zugriff auf den Server. Wer den Behaelter neu starten kann, hat
    # ohnehin die volle Kontrolle ueber Anwendung und Datenbank.
    betreiber: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nexview.db"

    @property
    def key_file(self) -> Path:
        return self.data_dir / "secret.key"

    def resolved_secret_key(self) -> str:
        """Geheimen Schluessel liefern; beim ersten Start automatisch erzeugen.

        So muss der Nutzer beim Ausprobieren nichts konfigurieren, waehrend ein
        gesetztes ``NEXVIEW_SECRET_KEY`` (Docker/Produktion) immer Vorrang hat.
        """
        if self.secret_key:
            return self.secret_key

        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.key_file.exists():
            existing = self.key_file.read_text(encoding="utf-8").strip()
            if existing:
                return existing

        generated = secrets.token_urlsafe(48)
        self.key_file.write_text(generated, encoding="utf-8")
        return generated


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir = Path(settings.data_dir).expanduser().resolve()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
