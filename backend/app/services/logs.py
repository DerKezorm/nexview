"""Protokollierung.

Nexview schreibt sein Protokoll in eine Datei unter ``data/logs/``. Die Datei
wird bei Erreichen der Groessengrenze umbenannt und es werden hoechstens drei
alte Staende behalten - so laeuft nichts voll. Zusaetzlich werden Dateien
geloescht, die aelter als 14 Tage sind.

Die Meldungen sind bewusst **englisch**: Log-Zeilen landen in Fehlerberichten
und Suchmaschinen, dort hilft Englisch weiter. ``test_log_sprache.py`` haelt
diese Regel fest, damit sie nicht bei der naechsten Funktion wieder zerfaellt.

Vier Stufen, umschaltbar im laufenden Betrieb
---------------------------------------------

``quiet``
    Nur Warnungen und Fehler. Fuer schwache Systeme.
``normal``
    Standard: Zustandsaenderungen, Warnungen, Fehler.
``detailed``
    Zusaetzlich der *Weg* dorthin - jeder Aufruf nach draussen, jede
    Filterentscheidung. Fuer die Fehlersuche.
``trace``
    Zusaetzlich die Rohdaten der Fremdbibliotheken (HTTP, SQL). Nur auf
    Anweisung - erzeugt sehr viele Zeilen.

``detailed`` und ``trace`` **schalten sich selbst wieder ab**. Grund: Die
Protokolldatei ist ein Ringpuffer. Eine vergessene ``trace``-Stufe ueberschreibt
innerhalb eines Tages genau die Zeilen, die man behalten wollte - und niemand
erinnert sich, sie auszuschalten. Deshalb wird beim Einschalten eine Dauer
mitgegeben, nach der die Anwendung von allein auf ``normal`` zurueckfaellt.

``NEXVIEW_LOG_LEVEL`` uebersteuert die gespeicherte Stufe. Das ist der Notausgang
fuer den Fall "die Anwendung startet gar nicht" - dann kommt man an keine
Oberflaeche, um die Stufe umzustellen.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import get_settings

BACKUP_COUNT = 3
RETENTION_DAYS = 14

# Bei der Fehlersuche ist die Datei schnell voll: eine halbe Stunde ``trace`` in
# einer benutzten Installation schreibt mehr als 5 MB. Dann waere genau der
# interessante Anfang schon weggerollt - deshalb in den tiefen Stufen mehr Platz.
MAX_BYTES_NORMAL = 5 * 1024 * 1024
MAX_BYTES_DEEP = 25 * 1024 * 1024

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(ctx)s] | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Fremdbibliotheken, die sonst jede einzelne HTTP-Anfrage protokollieren.
# Ohne das waere das Protokoll nach kurzer Zeit voller TMDB- und
# Radarr-Aufrufe - und die eigentlichen Meldungen darin nicht mehr zu finden.
NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "urllib3",
    "watchfiles",
    "multipart",
    "sqlalchemy.engine",
    "uvicorn.access",
)

MODES: dict[str, dict[str, int]] = {
    #            eigene Meldungen       alles Uebrige            Fremdbibliotheken
    "quiet": {"app": logging.WARNING, "root": logging.WARNING, "libs": logging.WARNING},
    "normal": {"app": logging.INFO, "root": logging.INFO, "libs": logging.WARNING},
    "detailed": {"app": logging.DEBUG, "root": logging.INFO, "libs": logging.INFO},
    "trace": {"app": logging.DEBUG, "root": logging.DEBUG, "libs": logging.DEBUG},
}

#: Stufen, die sich selbst wieder abschalten.
DEEP_MODES = ("detailed", "trace")
DEFAULT_MODE = "normal"

#: Erlaubte Dauern in Minuten; ``0`` heisst "bis zum Neustart".
ALLOWED_MINUTES = (30, 120, 480, 0)

SETTING_MODE = "log_mode"
SETTING_UNTIL = "log_mode_until"

LEVEL_ORDER = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Fuer das Auslesen. Der Klammerteil ist **optional**: Nach einem Update stehen
# in derselben Datei noch Zeilen im alten Format ohne Vorgangsnummer, und die
# sollen weiter lesbar bleiben.
LINE_PATTERN = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<logger>\S+)\s"
    r"(?:\[(?P<ctx>[^\]]*)\]\s)?"
    r"\|\s(?P<message>.*)$"
)

# ---------------------------------------------------------------------------
# Zusammenhang einer Anfrage
# ---------------------------------------------------------------------------
#
# Beide Werte werden pro Anfrage gesetzt (siehe ``middleware.py`` und
# ``deps.py``) und landen in *jeder* Zeile, die waehrend dieser Anfrage
# entsteht. Damit beantwortet ein einziges Suchen nach der Vorgangsnummer die
# Frage "was ist bei diesem Klick passiert" - vorher blieb nur die Uhrzeit, und
# die kennt der Meldende selten genau.
#
# ⚠️ Im ``ContextVar`` steht ein **veraenderliches** Woerterbuch, kein einfacher
# Text. Der Grund ist unauffaellig, aber entscheidend: FastAPI fuehrt synchrone
# Abhaengigkeiten und Endpunkte in einem Threadpool aus, und der bekommt eine
# *Kopie* des Kontexts. Ein dort gesetzter Wert waere nach der Rueckkehr wieder
# weg - der Benutzername aus ``get_current_user`` hat auf diesem Weg nie eine
# Zeile erreicht. Das Woerterbuch dagegen ist in allen Kopien dasselbe Objekt.

_context: ContextVar[dict[str, str | None] | None] = ContextVar(
    "nexview_log_context", default=None
)


def bind_request(nummer: str) -> object:
    """Eine Anfrage eroeffnen; liefert die Marke fuer ``unbind_request``."""
    return _context.set({"rid": nummer, "actor": None})


def unbind_request(marke: object) -> None:
    _context.reset(marke)  # type: ignore[arg-type]


def set_actor(name: str | None) -> None:
    """Nachtragen, wer die laufende Anfrage stellt."""
    daten = _context.get()
    if daten is not None:
        daten["actor"] = name


def current_request_id() -> str | None:
    daten = _context.get()
    return daten.get("rid") if daten else None


class _ContextFilter(logging.Filter):
    """Vorgangsnummer und Benutzer an jede Zeile haengen."""

    def filter(self, record: logging.LogRecord) -> bool:
        daten = _context.get() or {}
        teile = [wert for wert in (daten.get("rid"),) if wert]
        wer = daten.get("actor")
        if wer:
            teile.append(f"u:{wer}")
        record.ctx = " ".join(teile) if teile else "-"
        return True


class _NoDuplicateAsgiTraceback(logging.Filter):
    """Den doppelten Stacktrace von uvicorn unterdruecken.

    Ein unbehandelter Fehler wuerde zweimal in der Datei stehen: einmal von uns -
    mit Vorgangsnummer, Benutzer, Pfad und Dauer - und einmal von uvicorn als
    "Exception in ASGI application". Der zweite sagt nichts, was der erste nicht
    besser sagt, kostet aber einen vollen Stacktrace.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "Exception in ASGI application"


@dataclass(frozen=True)
class LogLine:
    time: str
    level: str
    logger: str
    message: str
    request_id: str | None = None
    user: str | None = None


@dataclass(frozen=True)
class ModeState:
    """Welche Stufe gilt, bis wann, und laesst sie sich ueberhaupt umstellen?"""

    mode: str
    until: str | None
    fixed_by_env: bool


def log_dir() -> Path:
    directory = get_settings().data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def log_file() -> Path:
    return log_dir() / "nexview.log"


def rotated_files() -> list[Path]:
    """Die aufbewahrten alten Staende - ``nexview.log.1`` ist der neueste."""
    return sorted(p for p in log_dir().glob("nexview.log.*") if p.is_file())


# ---------------------------------------------------------------------------
# Einrichten und Umschalten
# ---------------------------------------------------------------------------

_handler: RotatingFileHandler | None = None
_console: logging.StreamHandler | None = None
_mode = DEFAULT_MODE


def env_mode() -> str | None:
    """Stufe aus ``NEXVIEW_LOG_LEVEL``, falls gesetzt und gueltig."""
    wert = (get_settings().log_level or "").strip().lower()
    if wert in MODES:
        return wert
    # Wer die Stufe technisch benennt ("DEBUG"), soll nicht ins Leere greifen.
    aliase = {"warning": "quiet", "warn": "quiet", "info": "normal", "debug": "detailed"}
    return aliase.get(wert)


def setup() -> None:
    """Protokollierung einrichten - einmal beim Start.

    Die gespeicherte Stufe wird hier noch **nicht** gelesen: Beim allerersten
    Start gibt es die Datenbank noch nicht. ``apply_stored_mode()`` holt das
    nach, sobald sie steht.
    """
    global _handler, _console

    root = logging.getLogger()
    uvicorn_error = logging.getLogger("uvicorn.error")

    # Doppelte Handler vermeiden (z. B. beim automatischen Neustart).
    for logger_ in (root, uvicorn_error):
        for vorhanden in list(logger_.handlers):
            if getattr(vorhanden, "_nexview", False):
                logger_.removeHandler(vorhanden)

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    handler = RotatingFileHandler(
        log_file(), maxBytes=MAX_BYTES_NORMAL, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())
    handler._nexview = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    _handler = handler

    # Zusaetzlich in die Container-Ausgabe. Sonst waere ``docker logs`` nach der
    # Unterdrueckung von uvicorns doppeltem Stacktrace (siehe unten) stiller als
    # vorher - wer die Ausgabe auf dem NAS mitliest, saehe einen Absturz gar
    # nicht mehr. Die Diagnose-Zeilen bleiben hier aussen vor: Die gehoeren in
    # die Datei, nicht in ein Fenster, das jemand mitliest.
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(_ContextFilter())
    console._nexview = True  # type: ignore[attr-defined]
    root.addHandler(console)
    _console = console

    # uvicorn haengt seinen Logger ``uvicorn`` auf ``propagate = False``. Ohne
    # die naechste Zeile landet **kein** Startfehler und kein Absturz aus dem
    # Server selbst in der Datei, die der Administrator herunterladen kann - er
    # stand nur im Docker-Log. Genau das machte Ferndiagnosen bisher unmoeglich:
    # Man bittet um das Protokoll und der Absturz fehlt darin.
    uvicorn_error.addHandler(handler)
    if not any(isinstance(f, _NoDuplicateAsgiTraceback) for f in uvicorn_error.filters):
        uvicorn_error.addFilter(_NoDuplicateAsgiTraceback())

    apply_mode(env_mode() or DEFAULT_MODE)
    purge_old()


def apply_mode(mode: str) -> None:
    """Stufe wirksam machen - ohne Neustart.

    Ein Neustart zerstoert oft genau den Zustand, den man untersuchen will.
    """
    global _mode
    stufen = MODES.get(mode)
    if stufen is None:
        mode, stufen = DEFAULT_MODE, MODES[DEFAULT_MODE]
    _mode = mode

    logging.getLogger().setLevel(stufen["root"])
    logging.getLogger("nexview").setLevel(stufen["app"])
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(stufen["libs"])

    if _handler is not None:
        _handler.maxBytes = MAX_BYTES_DEEP if mode in DEEP_MODES else MAX_BYTES_NORMAL
    if _console is not None:
        # Nie feiner als INFO: Die Container-Ausgabe soll auch waehrend einer
        # Diagnose lesbar bleiben - die Einzelheiten stehen in der Datei.
        _console.setLevel(max(stufen["app"], logging.INFO))


def current_mode() -> str:
    return _mode


def apply_stored_mode() -> None:
    """Die gespeicherte Stufe uebernehmen - nach ``init_db()`` aufzurufen."""
    vorgabe = env_mode()
    if vorgabe:
        apply_mode(vorgabe)
        logging.getLogger("nexview").info(
            "Logging started mode=%s source=NEXVIEW_LOG_LEVEL file=%s", vorgabe, log_file()
        )
        return

    mode, until = _stored()
    if mode in DEEP_MODES and until is not None and until <= _now():
        # Die Frist lief ab, waehrend der Container nicht lief.
        _store(DEFAULT_MODE, None)
        mode, until = DEFAULT_MODE, None

    apply_mode(mode)
    logging.getLogger("nexview").info(
        "Logging started mode=%s until=%s file=%s",
        mode,
        until.isoformat(timespec="seconds") if until else "-",
        log_file(),
    )


def set_mode(mode: str, minutes: int = 0) -> ModeState:
    """Stufe umstellen und speichern.

    ``minutes`` gilt nur fuer die tiefen Stufen; ``0`` heisst "bis zum Neustart".
    """
    if mode not in MODES:
        raise ValueError(f"unknown log mode: {mode}")

    until = _now() + timedelta(minutes=minutes) if mode in DEEP_MODES and minutes else None

    _store(mode, until)
    vorher = _mode
    apply_mode(mode)
    logging.getLogger("nexview").info(
        "Log mode changed from=%s to=%s until=%s",
        vorher,
        mode,
        until.isoformat(timespec="seconds") if until else "-",
    )
    return state()


def state() -> ModeState:
    """Aktueller Stand - beruecksichtigt eine abgelaufene Frist.

    Liest den gespeicherten Stand genau **einmal** und erledigt den
    Ablauf-Check damit; vorher rief er ``enforce_expiry`` und las danach noch
    einmal - zwei Gaenge zur Datenbank fuer dieselben zwei Schluessel.
    """
    vorgabe = env_mode()
    if vorgabe:
        return ModeState(mode=vorgabe, until=None, fixed_by_env=True)

    mode, until = _stored()
    if _ablauf_anwenden(mode, until):
        until = None
    return ModeState(
        mode=_mode,
        until=until.isoformat(timespec="seconds") if until else None,
        fixed_by_env=False,
    )


def enforce_expiry() -> bool:
    """Abgelaufene Diagnose-Stufe auf ``normal`` zuruecknehmen."""
    if env_mode():
        return False

    mode, until = _stored()
    return _ablauf_anwenden(mode, until)


def _ablauf_anwenden(mode: str, until: datetime | None) -> bool:
    """Der gemeinsame Kern von ``state`` und ``enforce_expiry``."""
    if mode not in DEEP_MODES or until is None or until > _now():
        return False

    _store(DEFAULT_MODE, None)
    apply_mode(DEFAULT_MODE)
    logging.getLogger("nexview").info("Log mode %s expired, back to %s", mode, DEFAULT_MODE)
    return True


async def run_forever(stop) -> None:
    """Waechter: nimmt die Diagnose-Stufe nach Ablauf der Frist zurueck.

    Eigene Schleife statt eines Anhaengsels am Status-Abgleich: Der laeuft
    standardmaessig nur alle zwei Minuten und ist per Umgebungsvariable
    abschaltbar - die Selbstabschaltung darf davon nicht abhaengen.
    """
    import asyncio

    while not stop.is_set():
        try:
            enforce_expiry()
        except Exception:  # pragma: no cover - der Waechter darf nie sterben
            logging.getLogger("nexview").exception("Log mode watchdog failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            continue


def _now() -> datetime:
    return datetime.now(UTC)


def _stored() -> tuple[str, datetime | None]:
    """Gespeicherte Stufe und Frist aus der Datenbank lesen.

    Faellt auf den Standard zurueck, wenn die Datenbank (noch) nicht da ist -
    die Protokollierung darf den Start nie verhindern.
    """
    try:
        from sqlalchemy import select

        from ..db import SessionLocal
        from ..models import Setting

        with SessionLocal() as db:
            # Beide Schluessel in einem Gang - sie werden nie einzeln gebraucht.
            werte = dict(
                db.execute(
                    select(Setting.key, Setting.value).where(
                        Setting.key.in_((SETTING_MODE, SETTING_UNTIL))
                    )
                ).all()
            )
            gelesen = (werte.get(SETTING_MODE) or "").strip()
            frist = (werte.get(SETTING_UNTIL) or "").strip()
    except Exception:  # noqa: BLE001 - ohne lesbare Einstellung gilt die Vorgabe, egal warum
        return DEFAULT_MODE, None

    if gelesen not in MODES:
        gelesen = DEFAULT_MODE

    zeitpunkt: datetime | None = None
    if frist:
        try:
            zeitpunkt = datetime.fromisoformat(frist)
        except ValueError:
            zeitpunkt = None
        else:
            if zeitpunkt.tzinfo is None:
                zeitpunkt = zeitpunkt.replace(tzinfo=UTC)
    return gelesen, zeitpunkt


def _store(mode: str, until: datetime | None) -> None:
    """Stufe und Frist ablegen.

    Bewusst an ``settings_service`` vorbei direkt an der Tabelle: Die Log-Stufe
    ist eine Betriebseinstellung und hat in ``AppSettings`` nichts zu suchen -
    dort stehen die fachlichen Einstellungen, die die Oberflaeche anzeigt.
    """
    try:
        from ..db import SessionLocal
        from ..models import Setting

        text_bis = until.isoformat(timespec="seconds") if until else ""
        with SessionLocal() as db:
            for key, text in ((SETTING_MODE, mode), (SETTING_UNTIL, text_bis)):
                row = db.get(Setting, key)
                if row is None:
                    db.add(Setting(key=key, value=text, is_secret=False))
                else:
                    row.value = text
            db.commit()
    except Exception:  # noqa: BLE001 - die Meldung darunter nennt die Folge, der Grund zaehlt nicht  # pragma: no cover
        logging.getLogger("nexview").warning("Could not store log mode %s", mode)


# ---------------------------------------------------------------------------
# Lesen und Aufraeumen
# ---------------------------------------------------------------------------


def purge_old() -> None:
    """Protokolldateien loeschen, die aelter als RETENTION_DAYS sind."""
    grenze = time.time() - RETENTION_DAYS * 86400
    for datei in log_dir().glob("nexview.log*"):
        try:
            if datei.stat().st_mtime < grenze:
                datei.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - Datei gerade in Benutzung
            continue


def _parse(zeile: str) -> LogLine | None:
    treffer = LINE_PATTERN.match(zeile.rstrip("\n"))
    if treffer is None:
        return None  # Fortsetzungszeile eines Stacktrace

    daten = treffer.groupdict()
    ctx = (daten.pop("ctx") or "").strip()
    nummer: str | None = None
    benutzer: str | None = None
    for stueck in ctx.split():
        if stueck == "-":
            continue
        if stueck.startswith("u:"):
            benutzer = stueck[2:]
        else:
            nummer = stueck
    return LogLine(**daten, request_id=nummer, user=benutzer)


def read(limit: int = 200, level: str | None = None, search: str | None = None) -> list[LogLine]:
    """Die neuesten Zeilen lesen - neueste zuerst.

    ``level`` wirkt als **"diese Stufe und hoeher"**. Vorher war es ein
    Gleichheitsvergleich: Wer "WARNING" waehlte, bekam die ERROR-Zeilen nicht zu
    sehen - also genau die, die er suchte.

    Es wird nur die aktuelle Datei gelesen, nicht die alten Staende: fuer
    "was ist gerade schiefgelaufen" reicht das, und es bleibt schnell. Zum
    Weitergeben gibt es den Download, der die alten Staende mitnimmt.
    """
    datei = log_file()
    if not datei.is_file():
        return []

    gewaehlt = (level or "").upper()
    schwelle = LEVEL_ORDER.index(gewaehlt) if gewaehlt in LEVEL_ORDER else 0
    needle = search.casefold() if search else None

    zeilen: list[LogLine] = []
    with datei.open("r", encoding="utf-8", errors="replace") as handle:
        for roh in handle:
            eintrag = _parse(roh)
            if eintrag is None:
                continue
            if LEVEL_ORDER.index(eintrag.level) < schwelle:
                continue
            if needle and not _passt(eintrag, needle):
                continue
            zeilen.append(eintrag)

    zeilen.reverse()
    return zeilen[:limit]


def _passt(eintrag: LogLine, needle: str) -> bool:
    """Suche trifft Text, Vorgangsnummer, Benutzer und Modulnamen.

    Die Vorgangsnummer gehoert dazu, weil das der eigentliche Arbeitsablauf ist:
    Der Nutzer nennt die Nummer aus seiner Fehlermeldung, der Administrator gibt
    sie ein und bekommt genau die Zeilen dieser einen Anfrage.
    """
    if needle in eintrag.message.casefold():
        return True
    if eintrag.request_id and needle in eintrag.request_id.casefold():
        return True
    if eintrag.user and needle in eintrag.user.casefold():
        return True
    return needle in eintrag.logger.casefold()


def clear() -> None:
    """Protokoll leeren - die Datei bleibt bestehen, wird aber geleert."""
    datei = log_file()
    if datei.is_file():
        datei.write_text("", encoding="utf-8")


def kennung(fehler: object) -> str:
    """Was von einem Fehler ins **Protokoll** gehoert - nicht sein Satz.

    ⚠️ **Warum das noetig ist.** Unsere Fehlertexte sind deutsch: Sie sind der
    Rueckfall fuer die Oberflaeche, falls eine Uebersetzung fehlt (siehe
    ``meldungen``). Wer sie ins Protokoll einsetzt, schmuggelt damit Deutsch
    hinein - und zwar an ``test_log_sprache`` vorbei, denn der prueft die
    festen Texte im Quelltext, und die sind englisch. Deutsch wird die Zeile
    erst zur Laufzeit.

    Aufgefallen ist es in Issue #7: Ein Betreiber in Rumaenien las in seinem
    Protokoll "Der Jellyfin-Server hat auch auf kleine Abfragen (25 Titel)
    nicht rechtzeitig geantwortet."

    Geliefert wird die Kennung samt Werten - englisch, kurz und durchsuchbar:
    ``mediaserver_pages_too_slow {'service': 'Jellyfin', 'size': 25}``.

    ⚠️ **Nicht fuer fremde Wortlaute.** Was ein Mailserver oder TMDB selbst
    antwortet, gehoert im Original ins Protokoll - das ist ihre Aussage, nicht
    unsere, und sie ist oft das Einzige, was den Fall erklaert. Solche Stellen
    stehen in ``test_log_sprache.FREMDER_WORTLAUT``.
    """
    code = getattr(fehler, "code", None)
    if not code:
        # Ohne Kennung bleibt die Klasse - immer noch besser als ein deutscher
        # Satz, und ein Hinweis darauf, dass hier eine fehlt.
        #
        # Der HTTP-Code kommt mit, wenn es einen gibt: Bei ``TmdbError`` traegt
        # ihn fast jeder Fall, und er ist genau das, was die Diagnose traegt -
        # 404 heisst "gibt es dort nicht", 429 "zu viele Abfragen", und das ist
        # ein Unterschied, den "TmdbError" allein verschweigt.
        status = getattr(fehler, "status_code", None)
        klasse = type(fehler).__name__
        return f"{klasse} (no code, HTTP {status})" if status else f"{klasse} (no code)"
    zahlen = getattr(fehler, "zahlen", None)
    return f"{code} {zahlen}" if zahlen else str(code)


def adresse(email: str | None) -> str:
    """Eine Mailadresse so, wie sie ins **Protokoll** gehoert.

    ⚠️ **Genug, um den Fall zu erkennen - nicht genug, um jemanden zu kennen.**
    Ein Protokoll laeuft wochenlang mit, wird beim Melden eines Fehlers
    angehaengt und landet damit bei Fremden. Vollstaendige Adressen darin
    machen es zu einem Verzeichnis; ganz ohne Adresse laesst sich dagegen
    nicht sagen, um welches Konto es ging.

    Die Domain bleibt, weil sie die Diagnose traegt (welcher Anbieter, welches
    Haus), der oertliche Teil wird nach zwei Zeichen abgeschnitten:
    ``ma***@beispiel.de``.
    """
    if not email:
        return "none"
    oertlich, trenner, domain = email.partition("@")
    if not trenner:
        return "***"
    return f"{oertlich[:2]}***@{domain}"
