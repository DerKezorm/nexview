#!/bin/sh
# Startskript des Containers.
#
# Hintergrund: Nexview soll nicht als Administrator laufen. Sobald das
# Datenverzeichnis aber von aussen eingehaengt wird (NAS, Server), gelten die
# Rechte des Wirtssystems - und die passen praktisch nie zufaellig zu dem
# Benutzer im Abbild. Deshalb startet der Container kurz als Administrator,
# richtet die Rechte am Datenverzeichnis ein und gibt die Kontrolle dann an den
# unprivilegierten Benutzer "nexview" ab.
#
# Ueber PUID/PGID laesst sich einstellen, welchem Benutzer des Wirtssystems die
# Dateien gehoeren sollen - genauso wie bei Radarr, Sonarr und Plex.

set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Auf welchem Port Nexview lauscht. Standard bleibt 8000; ueber NEXVIEW_PORT
# laesst er sich umstellen. Gebraucht wird das im Host-Netzwerk-Betrieb: dort
# gibt es keine Portzuordnung, der Port im Container *ist* der Port des
# Servers - und wenn dort schon etwas auf 8000 liegt, kaeme Nexview sonst gar
# nicht hoch.
#
# Warum hier und nicht im Dockerfile: In der JSON-Form von CMD ersetzt Docker
# keine Variablen. Und warum so weit oben: Weiter unten gibt es zwei Ausgaenge,
# und der erste greift, wenn der Container bereits unprivilegiert startet -
# genau der Fall auf TrueNAS.
#
# Angehaengt wird nur an einen uvicorn-Aufruf und nur, wenn nicht ohnehin ein
# --port dabeisteht. Wer "docker run ... sh" aufruft, soll keine Portangabe
# untergeschoben bekommen.
if [ "$1" = "uvicorn" ]; then
    case " $* " in
        *" --port "*) ;;
        *) set -- "$@" --port "${NEXVIEW_PORT:-8000}" ;;
    esac
fi

# Laeuft der Container bereits ohne Administratorrechte (z. B. weil in der
# compose-Datei "user:" gesetzt ist), gibt es nichts einzurichten.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

# Benutzer und Gruppe im Container auf die gewuenschten Nummern umstellen.
# -o erlaubt Nummern, die es schon gibt (auf manchen Systemen ist 1000 belegt).
if [ "$(id -g nexview)" != "$PGID" ]; then
    groupmod -o -g "$PGID" nexview
fi
if [ "$(id -u nexview)" != "$PUID" ]; then
    usermod -o -u "$PUID" nexview
fi

mkdir -p /data

# Rechte nur anfassen, wenn sie wirklich nicht stimmen: bei vielen Profilbildern
# und Protokolldateien wuerde ein "chown" bei jedem Start unnoetig Zeit kosten.
if [ "$(stat -c %u /data)" != "$PUID" ] || [ "$(stat -c %g /data)" != "$PGID" ]; then
    echo "Nexview: Rechte am Datenverzeichnis werden auf $PUID:$PGID gesetzt."
    chown -R "$PUID:$PGID" /data
fi

exec gosu nexview "$@"
