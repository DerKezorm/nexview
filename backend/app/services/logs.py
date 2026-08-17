"""Protokollierung.

Nexview schreibt sein Protokoll in eine Datei unter ``data/logs/``. Die Datei
wird bei 5 MB umbenannt und es werden hoechstens drei alte Staende behalten -
so laeuft nichts voll. Zusaetzlich werden Dateien geloescht, die aelter als
14 Tage sind.

Die Meldungen sind bewusst **englisch**: Log-Zeilen landen in Fehlerberichten
und Suchmaschinen, dort hilft Englisch weiter.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import get_settings

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
RETENTION_DAYS = 14

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Fremdbibliotheken, die sonst jede einzelne HTTP-Anfrage protokollieren.
# Ohne das waere das Protokoll nach kurzer Zeit voller TMDB- und
# Radarr-Aufrufe - und die eigentlichen Meldungen darin nicht mehr zu finden.
NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "watchfiles", "multipart")

# Fuer das Auslesen: "2026-08-17 09:12:33 INFO     nexview.poller | Text"
LINE_PATTERN = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<logger>\S+)\s\|\s(?P<message>.*)$"
)


@dataclass(frozen=True)
class LogLine:
    time: str
    level: str
    logger: str
    message: str


def log_dir() -> Path:
    directory = get_settings().data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def log_file() -> Path:
    return log_dir() / "nexview.log"


def setup(level: int = logging.INFO) -> None:
    """Protokollierung einrichten - einmal beim Start."""
    root = logging.getLogger()
    root.setLevel(level)

    # Doppelte Handler vermeiden (z. B. beim automatischen Neustart).
    for handler in list(root.handlers):
        if getattr(handler, "_nexview", False):
            root.removeHandler(handler)

    handler = RotatingFileHandler(
        log_file(), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    handler._nexview = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    purge_old()
    logging.getLogger("nexview").info("Logging started (level=%s)", logging.getLevelName(level))


def purge_old() -> None:
    """Protokolldateien loeschen, die aelter als RETENTION_DAYS sind."""
    grenze = time.time() - RETENTION_DAYS * 86400
    for datei in log_dir().glob("nexview.log*"):
        try:
            if datei.stat().st_mtime < grenze:
                datei.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - Datei gerade in Benutzung
            continue


def read(limit: int = 200, level: str | None = None, search: str | None = None) -> list[LogLine]:
    """Die neuesten Zeilen lesen - neueste zuerst.

    Es wird nur die aktuelle Datei gelesen, nicht die alten Staende: fuer
    "was ist gerade schiefgelaufen" reicht das, und es bleibt schnell.
    """
    datei = log_file()
    if not datei.is_file():
        return []

    wanted = level.upper() if level else None
    needle = search.casefold() if search else None

    zeilen: list[LogLine] = []
    # Von hinten lesen waere sparsamer, aber die Datei ist auf 5 MB begrenzt.
    with datei.open("r", encoding="utf-8", errors="replace") as handle:
        for roh in handle:
            treffer = LINE_PATTERN.match(roh.rstrip("\n"))
            if treffer is None:
                continue  # Fortsetzungszeilen eines Stacktrace
            eintrag = LogLine(**treffer.groupdict())
            if wanted and eintrag.level != wanted:
                continue
            if needle and needle not in eintrag.message.casefold():
                continue
            zeilen.append(eintrag)

    zeilen.reverse()
    return zeilen[:limit]


def clear() -> None:
    """Protokoll leeren - die Datei bleibt bestehen, wird aber geleert."""
    datei = log_file()
    if datei.is_file():
        datei.write_text("", encoding="utf-8")
