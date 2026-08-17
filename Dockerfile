# Nexview als ein einziger, schlanker Container.
#
# Zwei Stufen: erst wird das Frontend gebaut, dann landen nur die fertigen
# Dateien im Abbild. Node und die rund 300 MB node_modules bleiben draussen -
# im Betrieb liefert FastAPI die gebauten Dateien einfach mit aus.

# --- Stufe 1: Frontend bauen -------------------------------------------------
FROM node:22-alpine AS frontend

WORKDIR /build

# Erst nur die Abhaengigkeitsdateien kopieren: solange sie sich nicht aendern,
# nutzt Docker beim naechsten Bauen den Zwischenstand und spart die Minuten
# fuer "npm ci".
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# --- Stufe 2: Laufzeit -------------------------------------------------------
FROM python:3.13-slim

# PYTHONUNBUFFERED: Ausgaben landen sofort im Docker-Protokoll statt erst beim
# Beenden. PYTHONDONTWRITEBYTECODE: keine .pyc-Dateien im Abbild.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXVIEW_DATA_DIR=/data \
    NEXVIEW_STATIC_DIR=/app/static

WORKDIR /app

# curl fuer den Healthcheck. --no-install-recommends haelt das Abbild klein.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /build/dist ./static

# Nicht als root laufen. Das Datenverzeichnis gehoert diesem Benutzer, damit
# die Datenbank auch in einem frisch angelegten Volume beschreibbar ist.
RUN useradd --system --create-home --uid 1000 nexview \
    && mkdir -p /data \
    && chown -R nexview:nexview /data /app
USER nexview

VOLUME ["/data"]
EXPOSE 8000

# Der Healthcheck fragt genau das, was auch die Oberflaeche braucht.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# Ein Arbeitsprozess reicht: die Datenbank ist SQLite, und die
# Hintergrund-Abfrage von Radarr/Sonarr soll nicht mehrfach laufen.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
