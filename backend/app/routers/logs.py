"""Protokoll einsehen - nur fuer Administratoren."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel

from ..deps import AdminUser
from ..services import logs

router = APIRouter(prefix="/api/logs", tags=["logs"])


class LogEntry(BaseModel):
    time: str
    level: str
    logger: str
    message: str


@router.get("", response_model=list[LogEntry])
def read_logs(
    admin: AdminUser,
    level: Annotated[Literal["INFO", "WARNING", "ERROR"] | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[logs.LogLine]:
    return logs.read(limit=limit, level=level, search=search)


@router.get("/download", include_in_schema=False)
def download_logs(admin: AdminUser) -> Response:
    """Vollstaendige Protokolldatei zum Herunterladen.

    Bewusst die ganze Datei, nicht die gefilterte Ansicht - zum Weitergeben
    bei einer Fehlersuche ist der ungekuerzte Verlauf das Nuetzlichere.
    """
    datei = logs.log_file()
    inhalt = datei.read_text(encoding="utf-8", errors="replace") if datei.is_file() else ""
    name = f"nexview-log-{datetime.now().strftime('%Y-%m-%d')}.txt"

    return Response(
        content=inhalt,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_logs(admin: AdminUser) -> None:
    logs.clear()
