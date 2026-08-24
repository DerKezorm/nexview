<div align="center">

<img src="frontend/public/logo.svg" width="72" alt="Nexview">

# Nexview

**Find something to watch — and request it straight from Radarr and Sonarr.**

[Project site](https://nexview.nexapps.dev) · [Changelog](CHANGELOG.md) · [Report an issue](https://github.com/DerKezorm/nexview/issues/new)

</div>

Nexview is a self-hosted media discovery dashboard for a household — family, flatmates,
a circle of friends. It shows new releases from [TMDB](https://www.themoviedb.org/),
marks what is already in your library, and hands requests to **Radarr** (movies) and
**Sonarr** (shows). Everyone gets their own account, with roles, approvals and quotas.

It downloads nothing itself and stores no media. It is the front door to a setup you
already run — it does not replace any part of it.

Without a TMDB key Nexview starts with sample data, so you can look around before
deciding anything.

---

<div align="center">

<img src="docs/screenshots/discover.webp" alt="The Nexview home page: rows of film posters under headings such as trending and curated for you" width="100%">

<sub>The home page. What each person marks as a favourite shapes what they get suggested.</sub>

<br><br>

<img src="docs/screenshots/request.webp" alt="A film's detail page with ratings, cast and an open request panel offering quality profile and target folder" width="49%">
<img src="docs/screenshots/child-view.webp" alt="The children's view: large colourful category tiles for animation, family, adventure and comedy" width="49%">

<sub>Requesting a title, and the same installation seen by a child.</sub>

</div>

**The full tour is on the [project site](https://nexview.nexapps.dev)** — every area with
its own page and more screenshots.

---

## What it does

**Finding something**

- Separate areas for movies and shows, each with its own logic
- Filter by period, language, region, genre, rating and studio; cards or list
- Detail pages with cast photos, directing and writing credits, studios, keywords,
  similar titles and the trailer in a window
- Ratings from IMDb, Rotten Tomatoes and Metacritic on the title itself
- Where a title is currently streaming in your region, split into subscription, free,
  rent and buy (data from JustWatch)
- Browse people — actors, directors, writers — and mark them as favourites; your
  favourites, titles and people alike, shape what gets suggested on your home page
- A release calendar, one week at a time: your own titles kept apart from new releases,
  with cinema and digital dates read for your region

**Requesting**

- Pick quality profile and target folder, or let the approver pick them instead
- Request whole shows or single seasons, optionally following future ones
- Optionally a second Radarr and Sonarr instance for 4K: the same title once in 1080p
  and once in 4K, with separate folders, profiles and per-user permissions
- The state sits on the poster — not requested, requested, searching, already
  downloaded, in library, blocked — and updates itself as Radarr and Sonarr work

**Who may do what**

- Three roles: administrator, approver, user. An approver decides on requests without
  ever getting near your API keys
- Approve automatically or by hand, set separately for movies, shows and 4K
- Quotas per day, week or month — or, instead of counting titles, **by disk space in
  gigabytes**, which is the thing that actually runs out
- Child accounts: a parent creates a login for their child, with an age, a set of
  categories and a language. The child gets a separate, simpler app and does not
  request but *wishes* — nothing happens until the parent approves, and the request
  then runs in the parent's name
- A block list for titles that should stay out: visible, but not requestable

**With a media server**

Plex is optional. Connect one and four things arrive:

- Sign in with a Plex account, checked against your server — no second password
- Titles already in the library are recognised even if they never came through
  Radarr or Sonarr, and cannot be requested twice
- Your Plex watchlist appears inside the catalogue, requestable with one click.
  Nothing happens on its own; every title takes a click
- What each person has already watched carries a marker, and everyone sees only
  their own

**Staying informed**

- A bell in the app, and per-event e-mail if you want it
- Seven notification channels for the installation as a whole: ntfy, Gotify, Telegram,
  Discord, a plain webhook, [Apprise](https://github.com/caronc/apprise) and e-mail.
  Each inbox picks its own events, language and urgency
- A ticket centre where people report problems and get an answer, with state and history
- Statistics and an error log for the administrator

**And**

- German and English throughout, including titles and descriptions
- A dark interface that works on a phone

## Built with

- **Frontend:** React 19, Vite, Tailwind CSS 4
- **Backend:** Python, FastAPI
- **Database:** SQLite — accounts, settings and requests only, never media files
- **Sessions:** JWT, passwords hashed with bcrypt
- **Deployment:** a single Docker container serving both the API and the interface

---

## Running with Docker

Nexview runs as **one** container: FastAPI serves the API and the built interface
together. No extra web server is needed.

The image is on the GitHub Container Registry and is built for Intel/AMD **and** ARM,
so nothing has to be compiled:

```bash
docker compose up -d
```

Nexview is then reachable at `http://<your-server>:5173`.

To build from source instead, replace `image: ghcr.io/derkezorm/nexview:latest` in
`docker-compose.yml` with `build: .` and add `--build` when starting.

### What to back up

Everything that matters lives in the directory mapped to `/data`:

| File | Contents |
|---|---|
| `nexview.db` | accounts, requests, feedback, settings |
| `secret.key` | the key your stored API keys are encrypted with |
| `avatars/` | profile pictures |
| `logs/` | error log |

**`secret.key` belongs with the database.** Without it the stored TMDB, Radarr, Sonarr
and SMTP credentials cannot be decrypted. Back both up together — and never put that
backup in a public repository.

### On a Synology

In **Container Manager** under *Project → Create*, pick the project folder and use the
`docker-compose.yml`. Two adjustments are worth making:

```yaml
    ports:
      - "5173:8000"        # change the left number if 5173 is taken
    volumes:
      - /volume1/docker/nexview/data:/data
```

A fixed path instead of `./data` makes backing up through Hyper Backup easier.

You do **not** need to sort out permissions on that folder — the container sets them on
start. If you want the files to belong to a particular user, put their numbers in
`PUID`/`PGID` (find them over SSH with `id username`).

### Updating

```bash
docker compose pull
docker compose up -d
```

The database is kept. Nexview brings it up to date itself on start: missing tables,
columns and indexes are added. **Before** anything is changed it writes a copy to
`/data/sicherungen/`, keeping the five most recent — so if something does go wrong, the
previous state is still there.

### Image tags

Nexview follows the usual `MAJOR.MINOR.PATCH` numbering:

| Image | Contents |
|---|---|
| `ghcr.io/derkezorm/nexview:latest` | the latest **released** version — the recommendation |
| `ghcr.io/derkezorm/nexview:0.16.1` | exactly that one version, never changes |
| `ghcr.io/derkezorm/nexview:main` | the current development state, may be broken |

The running version is in the footer and in detail under **About Nexview**, which also
reports when a newer one exists. For that it asks GitHub at most once a day. The check
sends nothing out of Nexview and can be switched off on the same page.

---

## Configuration

Every setting is optional — Nexview runs without a configuration file. For production,
copy `.env.example` to `.env`:

| Variable | Meaning |
|---|---|
| `NEXVIEW_SECRET_KEY` | Secret for sessions and for encrypting the API keys. Generated automatically and stored in `data/secret.key` if unset. |
| `NEXVIEW_DATA_DIR` | Directory for the database and key file (default: `./data`) |
| `NEXVIEW_ACCESS_TOKEN_MINUTES` | How long a sign-in stays valid (default: 30) |
| `NEXVIEW_REFRESH_TOKEN_DAYS` | How long you stay signed in (default: 30) |
| `NEXVIEW_CORS_ORIGINS` | Allowed origins in development |
| `NEXVIEW_STATIC_DIR` | Folder with the built frontend (the container sets this itself) |
| `NEXVIEW_LOG_LEVEL` | Overrides the log level chosen in the app (`quiet`, `normal`, `detailed`, `trace`). Emergency exit for when Nexview does not start. |

**No TMDB, Radarr or Sonarr keys belong in `.env`** — you enter those in the app.

---

## Development

**Requirements:** Python 3.12+ and Node.js 20+

### Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
```

The API then runs on `http://127.0.0.1:8000`, with interactive documentation at
`http://127.0.0.1:8000/docs`.

### Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

The interface opens at `http://localhost:5173`. Requests to `/api` are forwarded to the
backend automatically.

### First run

A one-time wizard appears on first launch:

| Step | Required | For |
|---|---|---|
| Account | yes | administrator with username, e-mail address and password |
| Picture | no | profile picture — without one, Nexview shows initials |
| TMDB | no | titles, posters, descriptions. Without a key you get sample data |
| Radarr | no | movies. Without Radarr, movies cannot be requested |
| Sonarr | no | shows. Without Sonarr, no show requests |
| Address | **yes** | the address Nexview is reachable at; it goes into every link |
| E-mail | **yes** | SMTP server for invitations and password recovery |

The last two cannot be skipped, and *Continue* only unlocks after a successful
connection test. The reason: straight afterwards Nexview sends you a confirmation
mail — and **without a confirmed address you cannot get back in**. A mail server that
does not work would leave a fresh installation unusable.

If the confirmation never arrives, the sign-in page helps: with the right password it
offers **Resend confirmation** and **Correct address**, so a typo is not fatal.

**Further accounts come from invitations** — or through the media server. The
administrator sets only the address and the role; the invitee picks their own username,
display name and password through the link in the mail. Nobody else learns that
password, not even the administrator.

### Tests

```bash
cd backend
.venv/Scripts/python.exe -m pytest
```

### Starting over

To discard all accounts and settings, delete the `data/` directory. The next start runs
the wizard again.

---

## Credits

Metadata comes from **TMDB**. This project is neither endorsed nor certified by TMDB.

Sign-in, library matching and the watched state run through **Plex**, when a server is
connected. Nexview is neither endorsed by nor affiliated with Plex.

Downloads are handled by **Radarr** and **Sonarr**; ratings come from **IMDb**,
**Rotten Tomatoes** and **Metacritic**, streaming availability from **JustWatch**.
Notifications can go through **ntfy**, **Gotify**, **Telegram**, **Discord**, a plain
webhook or **Apprise**.

**[Overseerr](https://overseerr.dev)** and **[Jellyseerr](https://github.com/Fallenbagel/jellyseerr)**
were the model: the idea of what a request interface for Radarr and Sonarr can look
like comes from there, from the second instance for 4K down to matching against the
media server. Nexview is an independent implementation and takes no source code from
either, but owes both a great deal.

---

## Licence

Nexview is under the **MIT licence** — see [LICENSE](LICENSE). The source may be used,
modified and passed on, commercially too, as long as the copyright notice stays. There
is no warranty.

The licence covers this project's source. It does not cover:

- **Metadata, posters and images from TMDB.** Those fall under the
  [TMDB terms of use](https://www.themoviedb.org/terms-of-use); running Nexview needs
  your own API key.
- **Plex, Radarr, Sonarr, Docker, Synology, IMDb, Rotten Tomatoes, Metacritic,
  JustWatch, ntfy, Gotify, Telegram, Discord and Apprise.** Trademarks of their
  respective owners, named here descriptively only.
- The dependencies in `frontend/package.json` and `backend/requirements.txt`, which
  carry their own licences.
