"""Protokoll einsehen und die Stufe umstellen - nur fuer Administratoren."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from ..deps import AdminUser
from ..services import logs
from .. import meldungen

router = APIRouter(prefix="/api/logs", tags=["logs"])

#: Obergrenze fuer den Download. Vier volle Staende der Diagnose-Stufe waeren
#: 100 MB - das laedt niemand mehr herunter und keine Mail nimmt es an.
DOWNLOAD_LIMIT = 20 * 1024 * 1024


class LogEntry(BaseModel):
    time: str
    level: str
    logger: str
    message: str
    request_id: str | None = None
    user: str | None = None


class LogMode(BaseModel):
    """Welche Stufe gilt gerade."""

    mode: str
    until: str | None = None
    fixed_by_env: bool = False
    modes: list[str] = Field(default_factory=lambda: list(logs.MODES))
    durations: list[int] = Field(default_factory=lambda: list(logs.ALLOWED_MINUTES))


class LogModeChange(BaseModel):
    mode: Literal["quiet", "normal", "detailed", "trace"]
    #: Nur fuer die tiefen Stufen; 0 heisst "bis zum Neustart".
    minutes: int = 0


@router.get("", response_model=list[LogEntry])
def read_logs(
    admin: AdminUser,
    level: Annotated[
        Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None, Query()
    ] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[logs.LogLine]:
    """Die neuesten Zeilen. ``level`` heisst "diese Stufe und hoeher"."""
    return logs.read(limit=limit, level=level, search=search)


@router.get("/level", response_model=LogMode)
def read_level(admin: AdminUser) -> LogMode:
    stand = logs.state()
    return LogMode(mode=stand.mode, until=stand.until, fixed_by_env=stand.fixed_by_env)


@router.put("/level", response_model=LogMode)
def set_level(admin: AdminUser, change: LogModeChange) -> LogMode:
    """Stufe umstellen - wirkt sofort, ohne Neustart."""
    if logs.env_mode():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Die Protokoll-Stufe ist über die Umgebungsvariable "
                "NEXVIEW_LOG_LEVEL festgelegt und lässt sich hier nicht ändern."
            ),
        )
    if change.minutes not in logs.ALLOWED_MINUTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=meldungen.meldung("duration_not_allowed", "Diese Dauer ist nicht vorgesehen."),
        )

    stand = logs.set_mode(change.mode, change.minutes)
    return LogMode(mode=stand.mode, until=stand.until, fixed_by_env=stand.fixed_by_env)


@router.get("/download", include_in_schema=False)
def download_logs(admin: AdminUser) -> Response:
    """Vollstaendiges Protokoll zum Herunterladen.

    Bewusst ungekuerzt und **einschliesslich der aufbewahrten alten Staende**:
    Bei einer Fehlersuche liegt der interessante Ausschnitt oft schon eine
    Umdrehung weiter, und wer das Protokoll weitergibt, soll nicht erst
    mehrere Dateien einsammeln muessen.
    """
    teile: list[str] = []
    gesamt = 0

    # Aeltester Stand zuerst, damit die Datei chronologisch von oben nach unten
    # gelesen werden kann. ``nexview.log.3`` ist aelter als ``nexview.log.1``.
    dateien = [*reversed(logs.rotated_files()), logs.log_file()]
    aufgenommen: list[str] = []
    for datei in dateien:
        if not datei.is_file():
            continue
        inhalt = datei.read_text(encoding="utf-8", errors="replace")
        if gesamt + len(inhalt) > DOWNLOAD_LIMIT and teile:
            continue
        teile.append(f"===== {datei.name} =====\n{inhalt}")
        aufgenommen.append(datei.name)
        gesamt += len(inhalt)

    fehlend = [d.name for d in dateien if d.is_file() and d.name not in aufgenommen]
    if fehlend:
        teile.insert(0, f"===== ausgelassen (zu groß): {', '.join(fehlend)} =====\n")

    name = f"nexview-log-{datetime.now().strftime('%Y-%m-%d')}.txt"
    return Response(
        content="\n".join(teile),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_logs(admin: AdminUser) -> None:
    logs.clear()
