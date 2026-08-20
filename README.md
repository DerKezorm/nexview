<div align="center">

<img src="frontend/public/logo.svg" width="72" alt="Nexview">

# Nexview

**Neue Filme und Serien entdecken — und direkt bei Radarr/Sonarr anfragen.**

</div>

Nexview ist ein persönliches Media-Discovery-Dashboard für Familie und Freundeskreis.
Es zeigt Neuerscheinungen von [TMDB](https://www.themoviedb.org/), zeigt auf einen Blick,
was bereits in deiner Bibliothek liegt, und stößt Downloads über **Radarr** (Filme) und
**Sonarr** (Serien) an — mit Login, Rollen, Freigaben und Kontingenten.

> **Status:** funktionsfähig und als Docker-Container lauffähig. Anmeldung mit Einladungen
> und bestätigter E-Mail-Adresse, Benutzer und Kontingente, Einstellungen, TMDB-Anbindung,
> Radarr-/Sonarr-Abgleich, Anfragen mit Freigabe, Qualitäts-Rückmeldungen, Statistik sowie
> die automatische Status-Verfolgung mit Benachrichtigungen sind fertig.
> Offen: Benachrichtigungen per E-Mail (statt nur in der App).
>
> Ohne TMDB-API-Key startet Nexview mit **Beispieldaten**, sodass sich die Oberfläche
> sofort ausprobieren lässt.

---

## Funktionen

| | |
|---|---|
| 🎬 **Getrennte Bereiche** | Filme und Serien mit jeweils eigener Logik (Radarr bzw. Sonarr) |
| 🔎 **Filtern & suchen** | Nach Zeitraum, Sprache, Region, Genre, Bewertung und Studio; Kachel- oder Listenansicht |
| 📄 **Detailseiten** | Besetzung mit Fotos, Regie, Studios, Schlagworte, Empfehlungen, Trailer — bei Serien alle Staffeln zum Aufklappen |
| ⬇️ **Anfragen** | Qualitätsprofil und Zielordner wählen, dann direkt an Radarr/Sonarr — bei Serien auch einzelne Staffeln |
| 📁 **Ordner erst bei der Freigabe** | Für Mediatheken, die in mehrere Ordner sortiert sind: Auf Wunsch wählt nicht der Anfragende, sondern der Entscheider Zielordner und Profil |
| 🎞️ **Zweite Instanz für 4K** | Optional ein zweites Radarr/Sonarr: derselbe Titel einmal in 1080p und einmal in 4K, mit eigenen Ordnern, eigenen Profilen und eigenen Rechten je Benutzer |
| 🏷️ **Status auf einen Blick** | „Nicht angefragt", „Angefragt", „Wird gesucht", „Bereits geladen", „Gesperrt" |
| ⭐ **Bewertungen** | IMDb, Rotten Tomatoes und Metacritic bei Filmen, anklickbar — ohne weiteren Dienst |
| ❤️ **Favoriten** | Titel mit dem Herz markieren; daraus entsteht „Für dich kuratiert" auf der Startseite |
| 👥 **Benutzer & Rollen** | Administrator, Entscheider und Benutzer; jeder sieht nur seine eigenen Anfragen |
| 🎞️ **Anmeldung mit Plex** | Wer Zugriff auf deine Plex-Bibliothek hat, meldet sich mit seinem Plex-Konto an — wahlweise zusätzlich zum Passwort |
| 📚 **Bibliothek erkennen** | Titel, die schon in Plex liegen, aber nie über Radarr/Sonarr kamen, sind als „In der Bibliothek" markiert und nicht doppelt anfragbar |
| 👁️ **Schon gesehen** | Was du auf deinem Media-Server angesehen hast, trägt ein kleines Auge — jeder sieht dabei nur seine eigenen |
| 🔖 **Plex-Merkliste** | Was auf der eigenen Plex-Merkliste steht, erscheint im Profil — mit einem Klick angefragt, ganz wie im Katalog |
| ✅ **Freigaben & Kontingente** | Pro Benutzer: automatisch freigeben oder manuell, Limits pro Tag/Woche/Monat |
| 🔞 **Altersbeschränkung** | Je Benutzer ein Alter; gezeigt wird nur, was höchstens ab diesem Alter freigegeben ist |
| 🚫 **Sperrliste** | Titel, die nicht in die Bibliothek sollen — sichtbar, aber nicht anfragbar |
| 🎫 **Ticketcenter** | Benutzer melden Anliegen, der Administrator antwortet; mit Zustand und Verlauf |
| 🔔 **Benachrichtigungen** | Glocke in der App, auf Wunsch zusätzlich per E-Mail — jedes Ereignis einzeln schaltbar |
| 📊 **Statistik & Protokoll** | Auswertung der Anfragen und ein Fehlerprotokoll für den Administrator |
| 🌓 **Dunkles Theme** | Cineastische Optik mit rotem Akzent, auch auf dem Smartphone |
| 🇩🇪 🇬🇧 **Zweisprachig** | Oberfläche, Filmtitel und Beschreibungen umschaltbar zwischen Deutsch und Englisch |

## Technik

- **Frontend:** React 19 + Vite + Tailwind CSS 4
- **Backend:** Python + FastAPI
- **Datenbank:** SQLite (nur Benutzer, Einstellungen, Anfragen — keine Mediendateien)
- **Anmeldung:** JWT-Sitzungen, Passwörter mit bcrypt gehasht
- **Betrieb:** ein einzelner Docker-Container, der API und Oberfläche zusammen ausliefert

Die API-Keys für TMDB, Radarr und Sonarr liegen **verschlüsselt in der Datenbank** und werden
über die Einstellungsseite gepflegt — nicht in Konfigurationsdateien. Der Browser spricht
ausschließlich mit dem Nexview-Backend, niemals direkt mit TMDB, Radarr oder Sonarr.

---

## Entwicklung

**Voraussetzungen:** Python 3.12+ und Node.js 20+

### 1. Backend starten

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
```

Die API läuft dann auf `http://127.0.0.1:8000`, die interaktive
API-Dokumentation unter `http://127.0.0.1:8000/docs`.

### 2. Frontend starten

In einem zweiten Terminal:

```bash
cd frontend
npm install
npm run dev
```

Die Oberfläche öffnet sich unter **`http://localhost:5173`**. Anfragen an `/api` werden
automatisch an das Backend weitergeleitet.

### 3. Ersteinrichtung

Beim ersten Aufruf erscheint ein einmaliger Assistent:

| Schritt | Pflicht? | Wozu |
|---|---|---|
| **Konto** | ja | Administrator mit Benutzername, E-Mail-Adresse und Passwort |
| **Bild** | nein | Profilbild – ohne zeigt Nexview die Anfangsbuchstaben |
| **TMDB** | nein | Titel, Poster, Beschreibungen. Ohne Key laufen Beispieldaten |
| **Radarr** | nein | Filme. Ohne Radarr lassen sich keine Filme anfragen |
| **Sonarr** | nein | Serien. Ohne Sonarr keine Serienanfragen |
| **Adresse** | **ja** | Unter welcher Adresse ist Nexview erreichbar? Steckt in jedem Link |
| **E-Mail** | **ja** | SMTP-Server für Einladungen und Passwort-Wiederherstellung |

Die beiden letzten Schritte lassen sich **nicht** überspringen, und „Weiter" wird erst nach
einem erfolgreichen Verbindungstest frei. Der Grund: direkt danach schickt Nexview dir eine
Bestätigungsmail — und **ohne bestätigte Adresse kommst du nicht mehr hinein**. Ein
Mailserver, der nicht funktioniert, würde die frische Installation unbrauchbar machen.

Kommt die Bestätigungsmail nicht an, hilft die Anmeldeseite weiter: mit richtigem Passwort
erscheint dort ein Hinweis mit **„Bestätigungsmail erneut senden"** und **„Adresse
korrigieren"**. Ein Tippfehler in der Adresse ist damit kein Beinbruch.

**Weitere Konten entstehen über Einladungen** — oder über den Media-Server, siehe unten. Der
Administrator gibt nur Adresse und Rolle vor; Benutzername, Anzeigename und Passwort wählt der
Eingeladene selbst über den Link aus der Mail. So kennt niemand sonst das Passwort — auch der
Admin nicht.

### Anmeldung mit Plex (optional)

Unter **Einstellungen → Media-Server** verbindest du deinen Plex-Server: einmal mit Plex
anmelden, Server aus der Liste wählen, fertig. Ein Token musst du nirgends heraussuchen, und
dein eigenes Konto wird dabei gleich verknüpft.

Danach kann sich anmelden, wer **Zugriff auf deine Bibliothek** hat. Das ist die einzige Hürde
und wird beim Verbinden geprüft — ein fremdes Plex-Konto kommt nicht hinein, auch wenn die
Anmeldung bei Plex selbst funktioniert.

| | |
|---|---|
| **Neue Leute** | bekommen beim ersten Anmelden selbst ein Konto, mit der Rolle, dem Kontingent und dem Alter, die du vorgibst. Abschaltbar — dann bleiben Einladungen Pflicht. |
| **Bereits eingeladene** | verbinden ihr Plex-Konto im Profil unter *Sicherheit* und melden sich danach wahlweise mit Plex **oder** Passwort an. |
| **Freigaben** | bleiben nötig: Zugriff auf die Bibliothek heißt nicht, ungefragt herunterladen zu dürfen. |
| **Gelöschte Konten** | können sich nicht neu anlegen. Die Sperre steht in den Einstellungen und lässt sich dort aufheben. |

> Verwaltete Plex-Profile (etwa für Kinder) haben oft keinen eigenen Plex-Login. Sie nutzen
> weiterhin ein normales Nexview-Konto per Einladung. Die Altersbeschränkung wirkt ohnehin nur,
> wenn **jede Person ein eigenes Konto** hat — unabhängig von Plex.

### Bibliothek und Gesehenes

Ist ein Server verbunden, liest Nexview stündlich mit, was dort liegt. Das hat zwei Zwecke:

| | |
|---|---|
| **Doppelte Anfragen verhindern** | Titel, die in Plex liegen, aber nie über Radarr/Sonarr kamen — von Hand kopierte Dateien etwa —, erscheinen als *„In der Bibliothek"* und lassen sich nicht anfragen. |
| **„Schon gesehen"** | Was du angesehen hast, trägt ein kleines Auge in der Leiste unter dem Poster. **Jeder sieht nur seine eigenen Marker**, auch der Administrator. |

Unter *Einstellungen → Dienste → Plex* steht, wie viele Titel erfasst sind und wann zuletzt;
dort gibt es auch **„Jetzt abgleichen"**.

Zwei Dinge, die man wissen sollte:

- Zugeordnet wird über die TMDB-, sonst die TVDB-Kennung, sonst über Titel **und Jahr**.
  Das Jahr ist kein Beiwerk: In einer echten Bibliothek mit 3509 Filmen trug genau einer eine
  falsche TMDB-Nummer, die auf einen ganz anderen Film zeigte. Lieber einen vorhandenen Titel
  übersehen als einen falschen behaupten — ein übersehener kostet einen doppelten Download,
  ein falscher nimmt einen Titel dauerhaft aus dem Angebot.
- Der Gesehen-Stand ist **für das Konto vollständig, dessen Zugang hinterlegt ist**. Für alle
  anderen zählt nur, was im Wiedergabe-Verlauf des Servers steht — und den bewahrt Plex nur
  begrenzt auf. Das liegt an Plex, nicht an Nexview.

Ohne verbundenen Server ist von alldem nichts zu sehen; niemand muss Plex betreiben.

### Die Plex-Merkliste

Unter **Profil → Merkliste → Plex** steht, was jemand in Plex auf seine Merkliste gesetzt hat —
als dieselben Kacheln wie im Katalog, mit denselben Abzeichen (*bereits geladen*, *angefragt*,
*4K*, *gesehen*). Ein Klick auf den Einkaufswagen oder auf das Poster führt in den **gewohnten
Anfrage-Dialog**; es gelten Kontingent, Freigabe, Sperrliste und Altersgrenze wie überall sonst.

**Es passiert nichts von selbst.** Kein Hintergrundabgleich, keine automatischen Anfragen — jeder
Titel braucht einen Klick. Wer nur sehen will, was noch fehlt, schaltet auf *Noch nicht da*.

| | |
|---|---|
| **Freischalten** | *Einstellungen → Merklisten → Plex*. Standardmäßig aus; der Reiter erscheint nur mit verbundenem Media-Server. |
| **Voraussetzung** | Ein **verknüpftes Plex-Konto**. Nexview liest ausschließlich die Merkliste des Kontos, das zum Profil gehört. |
| **Zugang** | Eine Merkliste lässt sich nur mit dem Token **ihres Eigentümers** lesen — der Zugang des Administrators sieht sie nicht. Er entsteht beim Verknüpfen bzw. bei jeder Anmeldung mit Plex. |

**Bestehende Installationen** haben diesen Zugang noch nicht — früher wurde das Token nach der
Prüfung verworfen. Dort steht auf der Seite ein Knopf „Mit Plex anmelden“; einmal genügt.
Dasselbe gilt, wenn Plex einen Zugang nicht mehr annimmt (Gerät abgemeldet).

Zwei Dinge, die man wissen sollte:

- **Plex nennt in der Merkliste keine TMDB-Nummern.** Die schlägt Nexview je Titel einmal nach
  und merkt sich das Ergebnis; beim zweiten Öffnen ist die Seite sofort da. Titel, die sich
  nicht zuordnen lassen, können nicht angezeigt werden — ihre Anzahl steht als Hinweis dabei,
  statt dass sie stillschweigend fehlen.
- **Verwaltete Plex-Profile (etwa für Kinder) haben keine eigene Merkliste**, weil ihnen der
  plex.tv-Login fehlt. Kein Fehler, sondern eine Eigenheit von Plex.

Anfragen, die von der Merkliste kommen, tragen ein kleines Abzeichen *Merkliste* und lassen sich
in *Meine Anfragen* und *Alle Anfragen* über einen eigenen Reiter herausfiltern.


> ⚠️ Wenn du Nexview von außen erreichbar machst (Reverse Proxy), vergib ein **langes
> Passwort**. Die technische Mindestlänge ist absichtlich niedrig, damit auch kurze
> Testkonten möglich sind — sie ist kein Sicherheitsversprechen.

### Tests

```bash
cd backend
.venv/Scripts/python.exe -m pytest
```

### Von vorn beginnen

Um alle Konten und Einstellungen zu verwerfen, lösche das Verzeichnis `data/`.
Beim nächsten Start erscheint wieder der Einrichtungsassistent.

---

## Betrieb mit Docker

Nexview läuft als **ein** Container: FastAPI liefert die API und die gebaute Oberfläche
gemeinsam aus. Es wird kein zusätzlicher Webserver gebraucht.

Das fertige Abbild liegt in der GitHub Container Registry und wird für Intel/AMD **und**
ARM gebaut — es muss also nichts selbst kompiliert werden:

```bash
docker compose up -d
```

Danach ist Nexview unter **`http://<adresse-des-servers>:5173`** erreichbar.

Wer stattdessen aus dem Quelltext bauen möchte, ersetzt in der `docker-compose.yml` die
Zeile `image: ghcr.io/derkezorm/nexview:latest` durch `build: .` und hängt beim Start
`--build` an.

### Was gesichert werden muss

Alles Wichtige liegt im Verzeichnis, das auf `/data` zeigt:

| Datei | Inhalt |
|---|---|
| `nexview.db` | Konten, Anfragen, Bewertungen, Einstellungen |
| `secret.key` | Schlüssel, mit dem die API-Keys verschlüsselt sind |
| `avatars/` | Profilbilder |
| `logs/` | Fehlerprotokoll |

> ⚠️ **`secret.key` gehört zur Datenbank.** Ohne diese Datei lassen sich die gespeicherten
> TMDB-, Radarr-, Sonarr- und SMTP-Zugänge nicht mehr entschlüsseln. Sichere beides
> zusammen — und lege die Sicherung **niemals** in ein öffentliches Repository.

### Auf einer Synology

Im **Container Manager** unter *Projekt → Erstellen* den Projektordner wählen und die
`docker-compose.yml` verwenden. Zwei Anpassungen sind sinnvoll:

```yaml
    ports:
      - "5173:8000"        # linke Zahl ändern, falls 5173 belegt ist
    volumes:
      - /volume1/docker/nexview/data:/data
```

Ein fester Pfad statt `./data` macht das Sichern über Hyper Backup einfacher.

Um die Rechte an diesem Ordner muss man sich **nicht** kümmern: der Container setzt sie beim
Start selbst. Wer möchte, dass die Dateien einem bestimmten Benutzer gehören, trägt dessen
Nummern als `PUID`/`PGID` ein (per SSH mit `id benutzername` herauszufinden).

### Aktualisieren

```bash
docker compose pull
docker compose up -d
```

Die Datenbank bleibt erhalten. Beim Start bringt Nexview sie selbst auf den neuen Stand:
fehlende Tabellen, Spalten und Indizes werden ergänzt. **Bevor** daran etwas geändert wird,
legt Nexview eine Kopie unter `/data/sicherungen/` ab (die fünf jüngsten bleiben liegen) —
falls doch einmal etwas schiefgeht, ist der alte Stand also noch da.

### Versionen

Nexview folgt der üblichen Zählung `HAUPT.NEBEN.KORREKTUR`:

| Abbild | Inhalt |
|---|---|
| `ghcr.io/derkezorm/nexview:latest` | die jeweils neueste **veröffentlichte** Version — das ist die Empfehlung |
| `ghcr.io/derkezorm/nexview:0.4.0` | genau diese eine Version, ändert sich nie |
| `ghcr.io/derkezorm/nexview:main` | der aktuelle Entwicklungsstand, kann kaputt sein |

Welche Version läuft, steht in der Fußzeile und ausführlich unter **Über Nexview**. Dort
meldet Nexview auch, wenn eine neuere vorliegt — dafür fragt es höchstens einmal am Tag bei
GitHub nach. Diese Nachfrage überträgt nichts aus Nexview und lässt sich auf derselben Seite
abschalten.

---

## Konfiguration

Alle Einstellungen sind optional — Nexview läuft ohne Konfigurationsdatei.
Für den Produktivbetrieb kopiere `.env.example` nach `.env`:

| Variable | Bedeutung |
|---|---|
| `NEXVIEW_SECRET_KEY` | Geheimer Schlüssel für Sitzungen und die Verschlüsselung der API-Keys. Wird sonst automatisch erzeugt und in `data/secret.key` abgelegt. |
| `NEXVIEW_DATA_DIR` | Verzeichnis für Datenbank und Schlüsseldatei (Standard: `./data`) |
| `NEXVIEW_ACCESS_TOKEN_MINUTES` | Gültigkeit der Anmeldung (Standard: 30) |
| `NEXVIEW_REFRESH_TOKEN_DAYS` | Wie lange man angemeldet bleibt (Standard: 30) |
| `NEXVIEW_CORS_ORIGINS` | Erlaubte Herkunft im Entwicklungsmodus |
| `NEXVIEW_STATIC_DIR` | Ordner mit dem gebauten Frontend (setzt der Container selbst) |

**In `.env` gehören keine TMDB-, Radarr- oder Sonarr-Keys** — die trägst du in der App ein.

---

## Danksagung

Die Metadaten stammen von **TMDB**. Dieses Projekt ist weder von TMDB unterstützt noch
zertifiziert.

Die Anmeldung, der Bibliotheks-Abgleich und „schon gesehen“ laufen über **Plex** — sofern ein
Server verbunden ist. Nexview ist weder von Plex unterstützt noch mit Plex verbunden.

Die Downloads besorgen **Radarr** und **Sonarr**; die Wertungen kommen von **IMDb**,
**Rotten Tomatoes** und **Metacritic**, die Streaming-Verfügbarkeit von **JustWatch**.

Als Vorbild diente **[Overseerr](https://overseerr.dev)** (und **[Jellyseerr](https://github.com/Fallenbagel/jellyseerr)**):
Von dort stammt die Idee, wie eine Anfrage-Oberfläche für Radarr und Sonarr aussehen kann — von der
zweiten Instanz für 4K bis zum Abgleich mit dem Media-Server. Nexview ist eine eigenständige
Neuentwicklung und übernimmt keinen Quelltext, verdankt beiden Projekten aber viele Ideen.

---

## Lizenz

Nexview steht unter der **MIT-Lizenz** — siehe [LICENSE](LICENSE). Damit darf der
Quelltext genutzt, verändert und weitergegeben werden, auch geschäftlich, solange
der Urheberrechtshinweis erhalten bleibt. Eine Gewährleistung gibt es nicht.

Die Lizenz gilt für den Quelltext dieses Projekts. Nicht davon erfasst sind:

- **Metadaten, Poster und Bilder von TMDB.** Sie unterliegen den
  [Nutzungsbedingungen von TMDB](https://www.themoviedb.org/terms-of-use); für den
  Betrieb brauchst du einen eigenen API-Key.
- **Plex, Radarr, Sonarr, Docker, Synology, IMDb, Rotten Tomatoes, Metacritic und
  JustWatch.** Marken der jeweiligen Rechteinhaber, hier nur beschreibend genannt.
- Die Abhängigkeiten in `frontend/package.json` und `backend/requirements.txt`,
  die unter ihren eigenen Lizenzen stehen.
