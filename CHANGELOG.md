# Changelog

Nexview counts in `MAJOR.MINOR.PATCH`:

- **PATCH** (0.2.**1**) – fixes only, nothing new.
- **MINOR** (0.**3**.0) – new features; everything that worked keeps working.
- **MAJOR** (**1**.0.0) – something behaves differently than before and needs a
  hand when you update.

The topmost number is the one being worked on. It is not released as long as no
tag exists for it.

---

## 0.26.0 – 31.08.2026

### Fixed

- **A child's wish could only be declined once the film had already arrived.** If a
  title turned up in the library while the wish was still waiting for a parent's
  decision — someone added it by hand, or a second Radarr instance fetched it — every
  attempt to release the wish failed with "already there". The reason could not change
  any more, so the only remaining button was "Decline": exactly the answer that tells
  the child the opposite of the truth, because it did get what it asked for. Such a
  wish is now closed as fulfilled, and the child sees "it's here" instead of "not this
  time".

- **A wish whose film arrived through somebody else's request stayed open forever.**
  Open wishes were only ever closed at the moment a request was *created*. If the wish
  came in while a download was already running, nothing ever closed it — and it then
  ran into the dead end above. The regular round now closes them when the download
  finishes.

- **Deleting a child account through the user API left its wishes behind.** The route
  cleared the children *below* an account, but not the account's own wishes when it was
  itself a child. On an upgraded database that leaves a wish pointing at a user who no
  longer exists, and the parent's wish list reads the child's name — so the whole list
  answered with an error instead of one broken row. On a fresh database the deletion
  failed outright. Both are fixed, and rows left behind by the old behaviour are
  cleared away at startup, with a line in the log saying how many.

- **Approving a film that Radarr already held failed for good.** Before creating a
  movie, Nexview never asked whether Radarr knew it — for series that lookup has always
  existed. Radarr answers a second attempt with a plain 400 whose reason only reaches
  the log, so the request was marked "failed" and the operator was left guessing. This
  happens more often than it sounds: a second Radarr instance, a film added by hand, a
  database restored from another installation. The request is now linked to the entry
  that is already there and carries on as usual; if the file exists, the next round
  marks it as downloaded.

- **A failed request had no button left at all.** Approve and reject only appear while
  a request is waiting, cancelling was refused for "failed", and the interface has
  never had a delete button — so the request stayed in the list forever and the person
  who asked kept waiting for something that would never come. Failed requests can now
  be cancelled, which also frees the quota they were holding.

- **"Already in your library" and "already on the media server" arrived in German.**
  Both sentences were written straight into the response with no key attached, so the
  interface had nothing to translate and fell back to the German text — on an English
  interface too. They now carry a key and the title as a placeholder, and are written
  out in both languages. The same applies to the new message when a child's wish turns
  out to be already fulfilled.

- **"Searching for over 14 days" reported films that had not come out yet.** The rule
  only asked how long a request had been searching, never whether the title was
  actually available. Anything ordered months before its release was flagged as a
  broken indexer. It now counts from the later of the two moments — approved *and*
  released — and names the oldest title instead of showing only a number. Titles with
  no release date on record still count: without a date there is no way to tell that
  they are unreleased, and a genuine indexer outage on an older title must not go
  unnoticed.

### Changed

- **The approval list now says which child a request came from.** "From a wish by Lena"
  answers the question a decider would otherwise ask themselves — why an adult is
  ordering a children's film. Nothing about the permissions changes: the request still
  belongs to the parent, with their quota and their rules. The record behind this line
  had been kept since child accounts were introduced and was never shown anywhere.

- **The "test phase" notice above the storage quotas is gone.** The quotas have been
  running in real operation for a while; a warning that never goes away stops being
  read.

### Under the hood

- **The first visit is 47 kB lighter.** "What's new" carried thirteen versions in both
  language files while the window only ever shows the latest five — eight of them were
  downloaded by every visitor and never displayed. They are archived in
  `docs/wasneu-archiv-0.15-0.22.json`. The bundle limit itself is unchanged; the
  headroom below it grew from 50 kB to 97 kB.

## 0.25.1 – 31.08.2026

### Fixed

- **Passwords could be guessed on two addresses that need no sign-in.** The sign-in
  brake named three doors where a password is checked. There was a fourth: resending
  the confirmation mail and correcting an unconfirmed address. Measured: 100 wrong
  passwords, 100 times 401, not a single 429, and the answer told the caller when a
  guess was right. Anyone able to reach Nexview from the network could try passwords
  for every account, the operator's included, without holding one of their own.

- **Access keys survived "sign out everywhere".** They take a second path through the
  sign-in that never asked whether the session was still valid. Somebody whose laptop
  was stolen locked the thief out of sessions but not out of keys. "Sign out
  everywhere" now revokes them and says so beforehand; a routine password change
  leaves them alone and points at where to revoke.

- **A slow Jellyfin or Emby no longer loses the whole library sync.** Every request to
  a media server had fifteen seconds, whatever it asked for. A library big enough, or
  a server slow enough, missed that, and because one missed page takes the entire run
  with it, the card read "Not synced yet." rather than "half done". Reported for
  Jellyfin while Emby on the same installation worked; both run the same code, the
  Jellyfin server was simply the slower of the two.

  Reported by [@ldoctoru](https://github.com/ldoctoru) in
  [#7](https://github.com/DerKezorm/nexview/issues/7). The two screenshots in that
  report are what made it findable: the card saying "Not synced yet." and a log line
  ending in `502 in 15011ms`: three attempts, three times the same figure, which is
  our own clock rather than the server's.

  The time limit is no longer a fixed number. Nexview now asks in chunks whose size
  the server decides: a chunk that misses its time is halved and the same place asked
  again, and what gets through is kept for the rest of the run. A library twice the
  size needs twice as many chunks, but each one fits. Nothing to configure.

  Timeouts also reach the log now, naming the request that died. Media servers were
  the only outward connection that stayed silent about it, so the report that started
  this could not be traced from the logs.

- **A silent Radarr deleted the whole storage accounting.** No answer meant no items,
  and everything the instance would have reported counted as gone. Measured: usage
  fell from 28 GB to 20, the house from 2 GB to 0, and because the rows were deleted,
  deletion deadlines and releases were lost for good. The rule from the status sync now
  holds here too: not answering is not a no.

- **Cleaning up several quality profiles at once forgot the ownership record** for all
  of them, including the ones the instance refused to delete. Those stayed in Radarr
  but counted as foreign here.

- **Sixteen error messages reached everybody in German**, whatever language they had
  chosen, among them the very first contact with Nexview: an expired invitation link.
  All of them are named now, so the interface can say them in the reader's language;
  the German sentence stays as the fallback. Media-server timeouts join them in this
  release.

- **An approver was told "new feedback" and then could not read it.** The bell led to
  an address that required an administrator, so the page said "there is no unanswered
  feedback". Reading is now open to approvers; answering is not.

- **Three pages showed their empty text when loading had failed:** your own requests,
  favourites, and a child's wishes. Somebody with twelve running requests read "you
  have not requested anything yet".

- **The bell sent two kinds of notification to the wrong page.** Where a click leads was
  answered in two places that disagreed; there is now one list, and it covers all 26
  kinds with no fallback.

- **A crash while drawing wiped the whole interface**, navigation included. The likeliest
  trigger is an update with a tab left open. There is a net now, with one visible way out.

- **The heart and the bin swallowed every failed click.** They report it themselves, where
  the eye already is, and a screen reader says it too. Deleting from the cleanup list
  failed silently for the same reason.

- **A child was shown TMDB's technical text.** The reason goes to the log instead.

- **A link to a conditional settings sub-tab always landed on "General".** On the first
  render the configuration is not loaded yet, so every sub-tab with a condition dropped
  out of the visible list and the fallback jumped away before the configuration arrived.
  `?unter=radarr` kept working, which is what hid the cause.

- **"Library touched" read "0 %" above "2 of 0 titles".** Without a stock the share is not
  zero, it is unknown, and a dash says that where a zero asserts something.

- **The health check in `docker-compose.yml` ignored `NEXVIEW_PORT`**, so following our own
  instructions produced a container that runs and counts as unhealthy. ESLint checked
  generated output and therefore always failed.

### Under the hood

- **Backups are now checked by what lives in the data directory**, not by a list of parts
  somebody remembered to name. `data/trash/` could have gone missing unnoticed: it had not
  been forgotten, it had never been asked about. Every name is now either archived or
  excluded with a written reason.

- **Five new guards**, each proven against a mutation: every literal `t('a.b')` exists in
  both languages (2,204 keys), no new error answer without a code, all 26 notification
  kinds know where their click leads, the v1 promise covers nested fields (252 instead of
  the top level), and a media server that only answers to small requests still delivers
  every title.

### Thanks

- **[@ldoctoru](https://github.com/ldoctoru)** for reporting
  [#7](https://github.com/DerKezorm/nexview/issues/7), with screenshots and a log
  excerpt, which is what turned "Jellyfin is not syncing" into something that could be
  measured. If you run into something, please open an issue; that is how this list
  gets shorter.

## 0.25.0 – 30.08.2026

### New

- **Sign in through an external provider.** Nexview can now hang off a sign-in service
  you already run — Authentik, Keycloak, Pocket ID, Google, or anything else that speaks
  OpenID Connect. A button per provider appears on the sign-in page; existing accounts
  link from the profile, and new people can be created automatically with the role and
  quota you set. Your password keeps working — the provider is added, it replaces
  nothing. Setup guides for the four common services are in the README.

- **An admin dashboard.** Everything operational used to be spread across thirty
  settings pages. There is now one place that says: three requests awaiting approval,
  Sonarr has not answered for two hours, the disk is 94 percent full, the last backup is
  overdue.

  Twenty checks run in the background, grouped into six areas. Every **finding** says
  not only what is wrong but **what follows from it**, and the button beside it lands
  exactly where you fix it — *Settings → Services → Sonarr*, not just “Settings”.

  The most valuable check is the quietest: requests that have been searching for over
  two weeks. In the interface “searching” looks like work rather than a standstill, so
  until now it only surfaced when somebody complained.

- **“Statistics” becomes “Statistics & analysis”**, with six tabs: requests, people,
  watching, library, services, operations. Each opens with the findings for its area and
  backs them up with figures.

  New under *library*: **source reconciliation**. Radarr manages the files, the media
  server plays them, Nexview bills them to somebody — and where the three disagree,
  errors appear that nobody looks for, because nothing is red anywhere. Titles with no
  identifier, year conflicts, files the media server never scanned in.

- **Live playback across every connected media server.** Who is watching what, on which
  device, how far in — and how hard the server is working for it.

  Transcoding has **three** states here, not two, and that distinction was measured
  rather than assumed: both Plex and Emby report a transcode while passing the video
  through untouched and only re-encoding the audio, which costs almost nothing. Only
  video transcoding is flagged red. Shown along with the reason and whether hardware
  acceleration is in play.

  Below that: how much gets watched and by whom, how the library grew over the last
  eighteen months, and how many streams ran at the same time.

- **A tile for Homepage, Homarr and friends.** `GET /api/v1/dashboard` answers with
  everything a dashboard tile needs in one call — open approvals, findings, library
  size, instance state. Ready-made snippets are in `docs/dashboard-tile.md`. It needs a
  token belonging to an administrator, and the guide says plainly what that means.

- **Links land where the thing actually is.** Settings and requests are now addressable
  down to the tab: `?reiter=dienste&unter=sonarr` and `?filter=failed`. Previously every
  link dropped you at the top of a page and left you to search.

- **How long people wait for an approval**, on the requests tab — as the median, so a
  single request somebody left lying for months does not distort the everyday picture.
  Alongside it, how long the oldest open request has been waiting.

### Changed

- **“Statistics & analysis” is now for administrators only.** Approvers could see the
  statistics page before. It now carries instance state, disk usage, backups and the
  source reconciliation — operating data, and deciding on requests does not require it.
  This also resolves a long-standing fault: the *Clean-up* tab was shown to approvers
  while the endpoint behind it had always been admin-only and answered 403.

- The disk breakdown separates **media**, **everything else** and **free**. “Used versus
  free” would be a half-truth: backups, photos and the operating system live on the same
  disk, and only media can be cleaned up through Nexview.

## 0.24.0 – 30.08.2026

### New

- **Serve Nexview under a sub-path.** Set `NEXVIEW_URL_BASE=/nexview` and Nexview lives at
  `https://example.com/nexview/` behind your reverse proxy — the setup that simply did not
  work before. Both common proxy styles are supported, passing the path through unchanged
  *and* stripping the prefix; the sign-in cookie, images, downloads and the API docs all
  follow the prefix. Unset means the root, exactly as before. Copy-paste examples for
  nginx, Caddy, Traefik and Nginx Proxy Manager are in the README under *Behind a reverse
  proxy*.
- **The public address suggests the prefix and warns when it is missing.** That address
  goes into every link Nexview sends — invitations, confirmations, password resets. The
  setup wizard and the address settings now prefill it including the sub-path, and show a
  warning when a hand-typed address lacks it; those links would otherwise lead nowhere,
  silently.
- A permanent browser test exercises sub-path operation on every release — sign-in,
  reload, deep links — through a real proxy in both styles.
- **The profile store can travel.** Settings → *Services* → *Quality profiles*: save the
  store as one file — recipes and names only, no credentials — and read it back in on a
  fresh installation. Nexview recognises its own copies on the instances by name and
  adopts them with their id; a preview shows what would happen first, and nothing is
  written to Radarr or Sonarr in the process. Without this, a fresh Nexview pointed at
  the same Radarr stood before its own profiles as before strangers — and cleanup would
  have flagged parts of them as unused.
- **Two instances, one download category — now warned about.** When two Radarr or Sonarr
  instances share a category in the download client (or run without one), each grabs the
  other's downloads: requests hang, files land in the wrong library, and no error appears
  anywhere — so the search starts at the network. Radarr cannot warn, it does not know
  the second instance exists; Nexview does, under Settings → *Services*. Dismissible for
  setups that share on purpose; a third instance on the same category warns again.

---

## 0.23.0 – 29.08.2026

### New

- **Quality profiles, built from the TRaSH Guides.** Settings → *Services* → *Quality
  profiles*. Radarr and Sonarr decide what to download by quality profiles made of custom
  formats and scores; whoever can operate that does not need Nexview for it. So Nexview
  asks about the **purpose of the profile** instead — what resolution should end up on the
  shelf, how good the source has to be, which audio tracks are required — and builds a
  complete profile from the answers, then writes it to as many instances as you like.

  The wizard branches on its first question. **Simple** is six questions and 53 custom
  formats. **Detailed** adds three steps about audio, video and release groups, and lands
  at 109. The simple path is the detailed one *without answers*: both build the same base,
  and the extra questions only put more groups on top — so a profile made before this
  release still matches after it.

  No invented numbers. Every score comes from the guides. Three of their groups were
  measured and **dropped** because they carry nothing but zeros; a question that changes
  nothing is a lie.

- **Inventory: what actually lies on your instances.** Everywhere else Nexview shows only
  what it made itself. Here it shows everything, including profiles it never touched —
  because a foreign profile holding a name, or blocking a delete, was invisible and left
  you searching in Radarr.

  Radarr refuses to delete a profile that is in use **without saying who uses it**; the
  cause turned out once to be a collection nobody had thought of. All three holders are now
  named: media, import lists, collections. Media on a bound profile can be **moved to
  another profile** first — the files stay where they are, only the assignment changes —
  and then the profile is free.

- **Adopt the recommended naming scheme — and bring existing files in line.** File names
  and folder names are offered separately, because their consequences differ: a new file
  name is harmless, a new folder name can cost the watched state in your media server.
  Folder names apply only to titles arriving from now on, and it says so.

  One tickbox goes further and renames **files that are already on disk**. That run
  survives a restart and picks up where it stopped — proven on 3531 files — and shows its
  progress to anyone who opens the page, not only to the browser that started it. It
  refuses to start while old format names would flow into the file names, because that is
  not a blemish but a second full run to undo.

- **Radarr and Sonarr can tell your media server.** After an import or a rename, Plex,
  Jellyfin or Emby learn about it instead of waiting for their own next scan.

  With a **preview of how paths get rewritten**. Radarr names a path from its own point of
  view; if the media server knows a different one, the call arrives, is acknowledged, and
  nothing happens — for years, without an error appearing anywhere. Nexview compares both
  sides and shows the rewriting before you agree to it. Where it cannot work the mapping
  out, it says so and refuses rather than guessing. Existing links are re-checked too,
  because they can stop working in silence.

- **Ask for single episodes.** On a series page you can now pick a season or individual
  episodes instead of the whole show, counted the way Sonarr counts them rather than the
  way we would. Cancelling removes **only what you ordered** — the series stays for
  everybody else who wanted it.

- **Parents can portion a child's wish.** One season, or two episodes to try. A partial
  release no longer closes the other children's wishes, and once a released wish is done
  while the series is not, the child gets an *I want more of this* button instead of a dead
  end.

- **Radarr and Sonarr call you, instead of being asked.** Nexview enters itself as a
  webhook in both — after proving the connection first, and without touching entries
  somebody else made. When the call comes in, the sync round is pulled forward instead of
  waiting for the next tick.

- **Every instance is its own tile.** Its own name, its own Save, its own logo. And with it
  a change worth reading twice: **who picks profile and folder is now decided per
  instance** instead of per service. A rule never set follows the default instance, so an
  existing installation behaves exactly as it did before the update — but a 4K instance can
  now be strict while the standard one stays open, or the other way round.

- **Remove an instance cleanly.** What it means is spelled out before you click, and the
  webhook entry over in Radarr is swept along. Handing storage back keeps working
  afterwards, even though the instance is gone.

- **"Downloading" while it downloads.** The queue is asked, and the word on the title
  changes with it.

- **What the instance complains about reaches you.** Radarr's own health warnings used to
  end up in the log where nobody looked. Upgrade notifications also hurry up now when the
  file on disk is growing.

### Changed

- **The settings open on *System*.** The first tab in the row is now also the tab you land
  on; before, opening settings highlighted an entry nobody had clicked.

- **Quality profiles sit next to Radarr and Sonarr**, before *Media server*, because that
  is what they are about.

- **Deleting a quality profile asks in a Nexview window**, not a browser popup. The browser
  dialog looks like a warning from the browser, ignores every bit of styling, and — the
  point — cannot show **where** the profile currently lies. The new one names the
  instances, and says plainly that nothing happens in Radarr. A test now forbids
  `confirm`, `alert` and `prompt` across the whole interface, because a comment saying
  "those were the last two places" had already failed to stop the next one.

- **The TRaSH Guides are credited** under *Credits* and in the README. Nexview ships a
  snapshot of their data and fetches newer versions on request; the scoring is their work,
  under MIT.

### Fixed

- **A failed request cost twice.** The title showed *Failed* to everyone with no way to ask
  again — the server would have accepted it, only the button was gone — and the attempt
  still counted against the piece quota. Failed now behaves like every other finished
  state.

- **Cancelling could trap a request forever.** Sonarr answers 500, not 404, when the series
  was already removed by hand, and every retry hit the same wall while the dialog swallowed
  the error. Cancel now checks whether the title is still there, and the dialog says what
  went wrong instead of silently staying open.

- **Error messages appear in your language.** Stored failures carry a code, so the
  interface builds the sentence in the configured language instead of always German.

- **"Watched, when is unknown" instead of "never".** Two different things that looked the
  same.

- **4K-only setups were overlooked** when the instances were listed.

- **A series could fail for a whole day** on a stale cache when TMDB did not have the TVDB
  id yet. TMDB is now asked again before giving up.

- **The progress display could topple the whole sync round.**

- **The description now says where the age limit actually lives.**

- **Everything Nexview says outwards is English**, enforced by a test rather than by
  discipline.

---

## 0.22.0 – 26.08.2026

### New

- **Backups you can actually take with you.** Settings has a new entry under *System*:
  *Backups*. Nexview already wrote a copy before every schema change, but that copy sat in
  the same directory as the database — lose the volume and it went with it. Now you can
  make one on demand, give it a note, and **download it**.

  What you get is an **encrypted ZIP** holding the database, the profile pictures and the
  key file `secret.key`. The key has to come along: the credentials for Radarr, Sonarr,
  TMDB and the mail server are encrypted with it, and without it they cannot be read again.
  That is also why the archive needs a password — and why it is a plain AES ZIP rather than
  a format of our own. You can open it with 7-Zip or your file manager; you do not depend
  on Nexview to get at your own data.

  **Caches are left out**, and that is the difference between 180 MB and 3: on a grown
  library over 90 % of the database is cached TMDB responses, and those come back on their
  own within hours.

- **Back up on a schedule.** Daily, weekly or monthly — weekly by default. Until now an
  automatic backup only happened when the schema changed, which in practice meant only on
  update. Between two versions that can be months; whoever deleted something by accident on
  a Tuesday had no copy from Monday. How many automatic copies to keep is now yours to set
  as well. Ones you made by hand are never cleaned up.

- **Restore from a backup — in the setup wizard and later.** A fresh installation now
  starts with a question: *start fresh* or *restore from a backup*. And a running
  installation can be rolled back too, from the same page the backups live on.

  Nexview looks at the archive first and shows you what is in it — which version, from
  when, with which note — before anything is replaced. A backup from a **newer** version is
  refused: Nexview can carry data forward but never back, and restoring one would damage
  the installation.

  **Afterwards nobody is signed in any more**, on purpose. And a warning that is worth
  reading: only Nexview is rolled back. Whatever happened in Radarr, Sonarr or on the media
  server in the meantime stays as it is.

- **Deleting an account no longer cancels approved orders behind your back.** When an
  account is dissolved, Nexview already asks what should happen to its finished items and
  its half-downloaded seasons. Approved orders that had not produced a single file yet were
  the exception: those were cancelled and taken out of Radarr without asking.

  The reasoning — *nothing has downloaded, so nothing is lost* — holds for disk space but
  not for intent. Somebody wanted the title, somebody approved it, it is on its way; the
  requester leaving does not make it less wanted by the household. Now it is the third
  question in the same dialog. Unticked by default: a kept order keeps downloading, and
  that is the operator's disk.

- **API tokens, so something other than a browser can talk to Nexview.** In your profile
  under *Account → Security*: create one, name it, revoke it. It is sent as
  `Authorization: Bearer …`, and the plain text is shown exactly once — afterwards only a
  checksum remains, and not even an administrator can look it up.

  **A token has exactly the rights of the account it belongs to.** That is the whole design,
  and it is deliberately unlike Seerr: there, a single global key is tied to the owner
  account, so anything done through it is auto-approved even when you wanted a limited
  service account — an open complaint there for years. Here, role, quota, approval and
  blocklist apply to a token exactly as they apply to its owner, so there is no second set
  of permissions to maintain. Need a service account with few rights? Create a *user* with
  those rights and give it a token.

  One switch on top: **may only read**. It covers the case that actually comes up —
  dashboards and monitoring — and it is enforced on the HTTP method, which is only sound
  because not one of the 89 GET paths changes anything. That was measured, not assumed.

  Child accounts get none, expired tokens stop working, and every log line says *which*
  token acted, not just who owns it.

- **Administrators can see who holds a token.** Settings, under *System → API tokens*: every
  token in the installation with its owner, when it was made and when it was last used. That
  last column is the useful one — a token nobody has touched in months is visibly dead.

  **You can look, not revoke.** Only the owner can switch off their own token. This is a
  decision, not an omission: there is no protected operator account yet, so an appointed
  administrator could otherwise shut off the tokens of the person who actually runs the
  server. The blunt instrument that does exist today is deactivating the whole account,
  which locks its tokens along with it — all of them, and the person too.

- **A promised interface: `/api/v1`.** Nexview has around 190 addresses, and nearly all of
  them are an inside part of the application - the interface talks to the backend and both
  change together. That is fine, and it should stay that way: this very release renamed a
  field from `storniert` to `offen` because the new name fits better.

  For anyone building against it from outside, that is useless. So thirteen endpoints now
  also live under `/api/v1`, and **for those there is a promise**: as long as `v1` is in the
  address, nothing disappears from these answers. Should something have to break, `/api/v2`
  appears beside it and v1 keeps running.

  What is in: searching, title details, making and tracking your own requests, your quota,
  what was recently downloaded, three counters for dashboards, health and version. What is
  deliberately out: the whole administration - promising that would freeze the very
  configuration model that keeps being worked on. It all stays reachable under `/api/…`,
  just without a promise.

  The handlers are the same ones, registered a second time, so behaviour cannot drift. Two
  tests guard it: one that the surface is exactly those thirteen, and one that holds the
  shape of their answers and complains when a field goes missing.

- **Sign out everywhere.** In your profile, next to the password. Ends every sign-in of this
  account on all other devices while keeping you signed in here.

  This closes a gap that 0.21.0 named openly but did not fix: signing out only takes the
  cookie out of *this* browser. Anyone who copied it beforehand kept getting in until it
  expired — up to 30 days — and the only remedy was to change your password. So you had to
  change a password that was never the problem.

### Changed

- **The settings look the same on every page now.** The bar had grown to ten entries and
  wrapped on narrow screens; *Address*, *Mail*, *Log* and *Backups* moved together under a
  new **System** entry, which sits first. Every tab now carries an icon.

  The real cause of the unevenness was not sloppiness on single pages: there were three
  different implementations of a sub-navigation row, and the component that draws a
  highlighted settings block existed only inside the services page. Both now live in one
  place, so the pages cannot drift apart again.

- **Two browser dialogs are gone.** Deleting a notification target asked through the
  browser's own popup — the one Chrome puts *"localhost says"* above. Both are proper
  dialogs now, like everywhere else in Nexview.

- **Your profile got a sub-menu.** *Account* had grown into six blocks stacked on one page —
  picture, name, email, language, password, and at the very bottom the new API tokens. The
  most useful new thing sat furthest down, behind everything you rarely touch.

  It is now split by the reason you came: **Profile** (who am I — picture, name, email,
  light or dark), **Security** (who gets into this account — password, signed-in devices,
  API tokens, deleting the account) and **Language** (language and region). Old links like
  `?reiter=sicherheit` still land where they should. The profile's tab row also uses the
  same component as the settings pages now, icons and all.

### Fixed

- **`/docs` was a blank white page — in every installation.** The browsable API documentation
  loads Swagger UI, and FastAPI fetches it from a CDN. Version 0.21.0 added a
  Content-Security-Policy that allows scripts only from Nexview itself, so the browser
  refused both the stylesheet and the script and rendered nothing at all. Not broken-looking:
  empty. `/redoc` was dead for the same reason.

  This slipped through because the policy was tested against the *application*, which kept
  working. The documentation pages do not belong to the application — they belong to FastAPI,
  and nobody opens them in day-to-day use.

  Swagger UI now ships **inside the image** (1.7 MB) and is served from Nexview itself. The
  policy stays as strict as it was, and the documentation works on a machine with no internet
  at all — which for something that runs on a NAS in a basement is not an edge case. `/redoc`
  redirects to `/docs`; keeping a second view of the same data would have cost another
  megabyte.

  `/openapi.json` was never affected — it is plain JSON and needs no scripts.

- **The thirteen promised endpoints now describe themselves in English.** Every docstring in
  Nexview is German, because that is the language the project is written in, and FastAPI puts
  those same docstrings on the public documentation page. The `/api/v1` routes now carry an
  explicit English description that takes precedence, while the German reasoning stays in the
  code where it belongs. The remaining 151 descriptions are still German — none of them is
  promised to anyone.

- **A brand-new installation wrote a backup of an empty database.** On the very first start
  the schema check reports that everything is missing, and Nexview dutifully made a copy
  that protected nothing while using up one of the five slots.

---

## 0.21.0 – 26.08.2026

### New

- **Nexview shows you what is lying around unused.** Statistics has a new entry: *What is
  lying around unused*. It lists titles nobody has watched in a while — biggest first — with
  how much room they take, when they were last watched, by whom, and who they are attributed
  to. Filter by movies or series, search by title, and set the period from a few months up
  to several years.

  **Two clocks, not one.** A title only shows up if nobody watched it within the chosen
  period *and* it has been here at least that long. With only the first clock the list would
  effectively be sorted by size, and the 60 GB file that arrived yesterday would sit at the
  top of it.

  Everyone finds the same list for their own titles under *Profile → Storage*, and can hand
  something back from there.

- **Deleting has a grace period.** Marking something for deletion does not delete it — it
  sets a date fourteen days out. Until then it stays, marked, and one click takes the mark
  away again. Anyone who watches the title in the meantime cancels the deletion on their
  own, without ever learning there was one.

- **A monthly mail, if you want it.** Once a month Nexview can send you what is lying
  around: at most thirty lines, biggest first, with a link straight into the list. Off by
  default, and the switch is in your profile. Nothing goes out without a mail server and a
  public address — a mail with a button that leads nowhere is worse than no mail.

- **Requests that were put aside come back.** A request that was deferred stayed deferred,
  and nothing brought it back. It now reappears under open approvals as soon as it fits
  again.

- **Series tiles say which seasons are here.** A tile for a series looked the same whether
  one season had arrived or all of them.

### Changed

- **Staying signed in no longer relies on the browser's storage.** The proof that you are
  signed in used to sit in `localStorage`, where any script on the page could read it. It
  now lives in a cookie that JavaScript cannot read at all, that only travels to the
  sign-in routes, and that does not go along from other sites.

  **Changing your password now really does end every other session.** The docstring had
  been claiming that for a long time; it was not true.

  ⚠️ What this does *not* cover: signing out still only takes the cookie out of *this*
  browser. Anyone who copied it beforehand can keep coming back until it expires. That was
  no different before — but it is worth saying plainly.

- **The browser now refuses foreign scripts.** Nexview sends a Content-Security-Policy: the
  browser runs only code that came from Nexview itself, and loads images only from the
  handful of hosts the covers come from. Should anything ever manage to get a script onto
  the page, the browser turns it away before it runs.

### Fixed

- **Ratings on a movie page returned an error since 0.19.0.** Every single call to the
  ratings of a movie failed — in 0.19.0 and in 0.20.0. A rewrite left one caller behind,
  and nothing noticed. A test now walks the whole codebase for calls into functions that no
  longer exist.

- **Two switches in the profile did nothing.** Two of the mail switches were saved nowhere;
  turning them on or off changed nothing at all. The list of switches is now derived from
  the schema itself, so the next one cannot be forgotten the same way.

- **Storage was counted twice for the same file.** A file that Radarr and the media server
  both report — under different quality labels — was booked twice over. On a grown library
  that adds up to hundreds of gigabytes that were never there.

- **Bold text showed its asterisks.** In several places text came out as `**like this**`,
  most visibly in the *Everything that's new* window.

### Under the hood

- **The interface has tests now.** 37 of them run without a browser in seconds, and one
  runs in a real Chromium against a real server: sign in, reload, still in, sign out, out.
  That last one is the only way to prove that the browser really keeps a sign-in across a
  reload — the session lives in a cookie no script can read, so nothing short of a real
  browser can tell you. All of them run in CI on every push.

---

## 0.20.0 – 25.08.2026

### New

- **Signing in now has a brake.** Until now a machine could try passwords as fast as the
  connection allowed — there was no rate limit and no lock, anywhere. After three wrong
  attempts the next one has to wait a second, then two, then four; after ten the door
  stays shut for fifteen minutes.

  **The lock opens again by itself.** A lock that needs an administrator to open it
  locks out the administrator, sooner or later. And an attempt that is turned away does
  not extend the wait — otherwise anyone could keep a housemate locked out for good just
  by keeping at it.

  Three doors are covered, not one. Besides signing in to Nexview there is signing in
  with a **media-server password**, and that one is the worse of the two: Nexview hands
  the password to Plex, Jellyfin or Emby, and it needs no Nexview account at all. Without
  a brake, Nexview was a comfortable way to guess passwords against your media server,
  with somebody else's return address on it. The links sent by mail are covered too.

  **Nobody who knows their password is affected.** A wrong password counts; a deactivated
  account or an unconfirmed address does not — those are people who know their password
  and are waiting for something else. And a correct password clears the counter, even
  when the sign-in then fails for one of those reasons.

- **Nexview can be told where the caller's address comes from.** Counting always happens
  per account, and that needs no configuration. Counting per address is extra, and it
  needs `NEXVIEW_CLIENT_IP` — `direct`, `proxy` or `proxy:2`.

  Left unset, addresses are not used at all, and that is deliberate. Nexview cannot tell
  on its own whether a reverse proxy sits in front of it, and behind one **every** request
  looks like it comes from the same address. Guessing wrong would mean the first typo
  locks out the whole household, the administrator included. So Nexview does not guess.

- **Error messages now come in the language you picked.** The interface has always been
  bilingual, but the messages from the server never were: switch Nexview to English, mistype
  your password, and up came „Benutzername oder Passwort ist falsch." All 76 of them speak
  both languages now.

  The reason it stayed hidden for so long is that you have to make a mistake to see one.
  And the reason it happened at all is that the interface texts live in the frontend, which
  was bilingual from the first day, while the error texts live in the backend, which had no
  way of translating anything.

  **The server does not translate — it names.** It cannot know which language you picked:
  that choice lives in your browser, and on the sign-in page there is not even an account
  it could hang a language on. A server translating there would have to guess, and would
  guess wrong for exactly the person who switched on purpose. So it sends a name for the
  problem, and the interface writes the sentence. A test walks the whole source and fails
  if any name is missing its text in either language — forgetting one is no longer possible.

  Two kinds of message stay as they are, on purpose: what Plex, Jellyfin or Emby report
  back arrives in *their* language, and translating that would mean inventing it.

- **The port inside the container can be moved.** Nexview could not be installed on host
  networking if something already held port 8000. It listened on a fixed 8000, which stays
  invisible in the normal case because the port mapping hides it. With host networking
  there is no mapping: the port inside the container *is* the port on the server, and there
  was no way around the collision. `NEXVIEW_PORT` now moves it. Left unset, everything
  behaves exactly as before.

  Reported by the TrueNAS catalogue maintainer while Nexview was being taken into their
  catalogue; it applies just as much to Unraid and plain Docker.

### Fixed

- **Error replies were losing their headers.** Whenever a built frontend sat next to the
  backend — which is to say, in every container — one handler caught *every* error and
  rebuilt the reply, quietly dropping whatever headers the endpoint had attached. Nothing
  looked wrong: the status was right, the message was right, only half the reply was
  missing. It cost every "not signed in" reply its `WWW-Authenticate` header, and it would
  have swallowed the new brake's `Retry-After` as well, so the sign-in page could never
  have said how long to wait.

---

## 0.19.0 – 25.08.2026

### New

- **"Tell me when it lands" — waiting for a title without requesting it.** If
  someone else had already requested a title, it could not be requested again —
  and after that the second person never heard another thing about it. They were
  not even told when the title arrived, which was the one thing they wanted to
  know.

  For a **film** the button sits where the status line normally is ("searching"),
  so it appears exactly when there is something to wait for. You are told once,
  and the reminder is then done.

  For a **series** it sits permanently next to the request button and covers the
  whole series. The reason is technical: the state "requested, nothing left to
  do" practically never occurs for a series while any season is incomplete — a
  button tied to a condition that never happens is not an offer. Every new
  episode is reported, because that is usually what you are waiting for.

  **Always bundled.** When a season pack of eight episodes comes down, that is
  *one* message about eight episodes, and consecutive numbers are pulled
  together: "S2: 1-3, 7" instead of four separate notes. Eight messages in the
  same minute would be the fastest way to make somebody switch notifications off.

  The first time a season is seen, the current state is only recorded, not
  reported — otherwise the reminder would immediately be followed by a message
  about twenty episodes that had been there all along.

- **Nexview tells you what your own subscriptions already carry.** Put your
  streaming services in your profile, and when you request something you get the
  one sentence that can change your mind: "Already on your Netflix." It is a
  **hint**, not a block — nothing is prevented, and the request button keeps its
  label.

  For series a different sentence appears, and there is a reason: the source says
  "on Netflix" about the *series*, not about the fourth season that is missing
  there — and that is exactly the case in which somebody requests it. The hint
  says so plainly instead of claiming something it does not know.

  The same comparison appears where the decision is made: in the approvals list
  ("Dilara could already watch this on Netflix") and for parents deciding on a
  child's wish. It is measured against the subscriptions **of the person asking**,
  not those of the person deciding — the decider may have no Netflix, but the
  question is whether the requester could do without the download. In the
  approvals list the hint only shows on requests that are genuinely waiting for a
  decision: with automatic approval the request was never on the decider's desk,
  and the hint would be a reproach with no one to receive it.

  The data costs nothing. TMDB passes it through from JustWatch and attaches it to
  every detail query anyway. The list of services is hand-picked: TMDB lists 194
  providers for Germany and 292 for the United States, with storefronts, niche
  channels and sub-tenants such as "Paramount+ Amazon Channel" ranked alongside
  Netflix. Their own ordering does not help — it puts the Apple TV Store and
  Google Play Movies **above** Disney+.

- **The region picker now knows 139 countries instead of eight.** It comes from
  TMDB rather than from the source code: anyone in the Netherlands, Poland or
  Canada simply could not state their country. The countries offered are those
  with provider data — offering a country that later has nothing to say would be
  a promise without cover.

- **A note for everyone who never picked a region.** The setup wizard does not ask,
  and the field starts empty, so most people quietly inherit the operator's
  default. For cinema dates that is an inaccuracy; for "already on your Netflix"
  it is a false claim about a person, because the catalogue of Netflix
  Switzerland is not the German one. A yellow strip says so, disappears by itself
  once a region is set, and can be dismissed for the current session.

- **Emby, as a third media server next to Plex and Jellyfin.** Connecting, signing
  in, matching the library and the watched state per person — all as before, just
  with one more server. Running them side by side, as introduced in 0.18, is
  unchanged: connect all three if you like, and the watched state is merged across
  them.

  Measured against a real server (Emby 4.9.5.0): Emby and Jellyfin speak the same
  language. The same endpoints, the same fields, even the same identification
  header — all five sign-in variants that were tested answered. The Emby adapter is
  therefore derived from the Jellyfin one rather than being a second copy: two
  nearly identical files side by side would mean every fix has to be made twice —
  and the second time it gets forgotten.

  **Emby accounts have no e-mail address either.** So the same rule as for Jellyfin
  applies: signing in with Emby does not create an account, it is linked from your
  profile. Without an address there is nothing for Nexview to recognise somebody
  by, and an invitation would quietly end in a second account with no password.

  The PIN that Emby optionally keeps per account is no obstacle: it belongs to the
  display settings, not to permissions — a profile PIN for switching on a shared
  device, the kind you know from Netflix. Signing in with username and password is
  unaffected. Measured against an account that has one set.

- **The history of a request.** "Why is this taking so long?" is the most common
  question, and Nexview always knew the full answer — requested when, approved by
  whom, searching since when, last checked when. All of it showed as a single
  status word. A button in "My requests" now opens a timeline: finished steps green
  with a tick, the running one amber, the coming ones grey. A path that was broken
  off ends where it ends — under a rejection there is no grey "done" step that
  would be a promise nobody is going to keep. Two facts reach the requester for the
  first time: who approved it (or that it went through automatically) and when the
  title was last looked for.

- **Ratings go stale when Radarr fetches a better file.** Radarr and Sonarr keep
  going until the quality profile is met. A rating, though, was about the file that
  was there at the time — after that a "this was bad" sits on something that no
  longer exists in that form. For the operator that is the worst kind of feedback:
  about a state they can no longer check.

  When the file grows noticeably, the rating is marked as stale. It **stays** —
  deleting it would lose the information, and empty stars with no explanation would
  look like a fault. It no longer counts in the statistics, because that page
  answers "how happy are people with what is here". The person affected gets a
  message and can rate again.

  This is spotted during the status check that runs anyway — the size is in the same
  reply that answers "is the title still there?". Deliberately **not** in the storage
  measurement: that can be switched off, and a rating goes stale even where nobody
  keeps quotas. It does not apply to season requests — the size on a series entry is
  that of the whole series and also grows when a different season is upgraded.

- **Anyone can rate, not just the person who ordered.** Feedback on quality hung off
  the request. Everything else followed from that: only whoever had ordered saw the
  stars — and someone who watched the same film two weeks later and noticed the
  audio track was missing had no way to say so.

  This is not about taste — that is what the heart is for — but about the **file**,
  and anyone who has seen it can judge it just as well. So the rating now hangs off
  the title. The stars are on every detail page of a title you have; in "My requests"
  they stay as a shortcut, because the moment right after "already downloaded" is
  when somebody rates.

  Deliberately **no** gate on the watched state, although Nexview knows it: it says
  that somebody watched the *title*, not *this file*. After an upgrade by Radarr the
  tick stays, even though it meant the old version — so it is no proof. Instead
  validity hangs off the file itself.

  **Series are judged per season.** The files sit per season, the quality differs per
  season. Rating a series as a whole would mean passing judgement on ten different
  files at once.

  Existing ratings move across on first start. Administrators still do not rate —
  they answer everybody else's feedback.

- **The feedback view now shows every rating.** It showed requests that happened to
  have a rating — a leftover of the old link. Since anyone with a title in front of
  them may rate, that was a view with holes: precisely the judgements of people who
  never ordered anything were missing. On a real database that was two out of seven.
  For the operator it makes no difference whether there is a request behind a piece
  of feedback.

- **A weak rating can become a ticket.** Somebody giving one or two stars is usually
  describing a problem — and a problem disappears into an average, whereas a ticket
  has a state and stays open until somebody has dealt with it. The dialog offers it,
  since the text is written anyway. It is not offered when an open ticket from the
  same person already exists for that title: the operator would otherwise get the
  same thing on their desk twice, and the user would think their first one had been
  lost.

### Changed

- **Quotas: item count and storage now apply together.** Until now this was a
  house-wide either-or — a switch decided whether the number of requests counted or
  the space used. The switch is gone. The tab is simply called **Quotas** and holds
  three defaults: films, series, storage. A request goes through when **both** limits
  have room left; the message says which one stopped it. To limit by only one of
  them, set the other to "unlimited" — that is one setting fewer than a mode.

  Every limit now has **three states** on an account: *default* (the house value),
  *unlimited* (explicitly no limit) and a number of its own, where **0** means "may
  request nothing". Before, "unlimited" was an empty field — once you had entered a
  number, there was no way back.

  ⚠️ **The 0 for storage has changed meaning.** It used to mean "unlimited for this
  account" and now means the opposite. Stored zeros move to "unlimited" automatically
  on first start; without that, an account would have been quietly locked overnight.

  ⚠️ **If the switch was on "count", all holdings move into the house once.** The GB
  limits had no effect in that mode, but the accounting kept running in the
  background. Without this step people would have been abruptly blocked after the
  update — because of a number that had been sitting around for months, and a history
  they knew nothing about. No file is touched, entered limits stay and take effect
  from now on.

- **The period for the item count applies house-wide.** It used to sit on each account
  separately. But three accounts with three different periods no longer explain to
  anyone what "3 films" means. Existing settings move across: where an account
  deviated from the week, the most common value wins.

- **The storage tab, house holdings and "I don't need this any more" are now there for
  everyone.** They hung off the old main switch and were therefore invisible to anyone
  limiting by item count. Measuring always happens, limiting only on request — so the
  chain "hand it back → the operator decides → house, keep or delete" is now open to
  every household. For operators that means: there can now be a handover on your desk
  that never used to come.

- **Resetting is a button instead of a side effect.** Switching the mode used to set
  the accounts quietly to zero. Now there is "move all holdings into the house" in the
  quotas and "reset storage" on each account — both with a confirmation and with
  numbers. The second is also the way out of the case below.

- **Titles that Radarr or Sonarr no longer carry are now recognisable.** Removing a
  title there while keeping the file creates an entry that still counts but can no
  longer be deleted — Nexview only ever deletes through those services. Such rows now
  carry a marker with an explanation. Until now only the operator noticed, and only
  when trying to delete.

- **"Language & region" and "Security" have moved into "Account".** Eight tabs became
  six, and the account page uses the full width in two columns. Instead of five save
  buttons underneath each other there is **one** for the whole page; profile picture,
  password and account deletion keep their own, because those are actions rather than
  settings. Changing the password opens a dialog instead of leaving three permanently
  empty fields in the column. The old addresses `?reiter=sprache` and
  `?reiter=sicherheit` still work.

### Fixed

- **Deferred requests were a dead end.** "Yes in principle, just not now" promises that
  it will be approved later and that nobody has to ask again. The part after the comma
  was never built: approving and rejecting explicitly required "waiting for approval",
  and a deferred request is not. The decider got "no longer waiting for approval", and
  the requester on a second attempt got "already deferred, it can be approved once you
  have room again" — both messages pointed at a path that did not exist. Now both
  decisions accept deferred requests, and the buttons appear on them too. Deliberately
  one at a time only: bulk approval stays with the waiting ones, otherwise "approve
  all" would sweep a single decision back up.

- **The watched-state sync would have crashed for Plex users.** When the account number
  was passed through, Jellyfin and Emby were adjusted and Plex was not — the caller
  handed over two values, the adapter took one. Caught by a test, not in the field.

- **Library entries from the media server carried no release year.** The field list did
  not ask for it, so it did not come, and every entry sat there with `year=None`. That
  sounds like trimming and is not: the title fallback in the library match compares
  title **and** year, so that "The Lion King" from 1994 is not the same as the 2019
  remake. Without a year it does not work at all — titles without a TMDB or TVDB id
  therefore counted as absent. Found while measuring against Emby; it affects Jellyfin
  just as much.

- **The tab for the region was named after a page that no longer exists.** "Default for
  Discover", with the note "you can change it in Discover at any time" — except Discover
  has been out of the menu since 0.17, and the region filter there was removed with it.
  That made the tab the only place where the region could be set at all, and it pointed
  at a path that was gone.

---

## 0.18.0 – 24.08.2026

### New

- **Nexview is now under the AGPL instead of the MIT licence.** The source stays
  public and free, and running Nexview unchanged asks nothing of you. What changes is
  what happens if somebody builds on it: anyone who modifies Nexview and lets others
  use their version — including as a hosted service, where no files ever change hands —
  now has to make their changed source available too. Versions up to 0.17.0 remain
  under MIT; that permission was given for good and is not being taken back.

- **Jellyfin, alongside Plex rather than instead of it.** Both servers can be
  connected at the same time, and each person may link an account on each. The
  watched state is merged across them: a title stays marked as seen while *any*
  connected server still reports it, so adding a second server no longer wipes
  the ticks the first one gave you. Where two servers disagree, Nexview can now
  say so instead of picking one.

  Signing in works with Jellyfin too — but it will not create an account.
  Jellyfin gives no email address for an account, and the address is the only
  thing that lets Nexview recognise somebody who already has an account. Without
  it, a person with an invitation would quietly end up with a *second* account,
  with no password and no way back in. Those who have an account link Jellyfin
  from their profile instead; the sign-in form says so before anyone falls into
  it.

- **The sign-in page shows the ways that exist.** Instead of a fixed "Sign in
  with Plex" button it now says "Sign in with" and offers one logo per connected
  server. An installation running only Jellyfin used to get a Plex button that
  failed on click — the flow behind it is built around plex.tv, which Jellyfin
  has no equivalent for.

- **Media server accounts have their own place in the profile.** They used to
  sit at the bottom of "Security", below the password form, where nobody
  looking to connect Plex would think to search.

- **The disconnect button now says who it would shut out.** Disconnecting the
  media server used to happen on a single click, without a word. For accounts
  that only ever came in through that server — no password of their own — this
  closed their only door. A confirmation now counts them by name beforehand,
  and the server refuses outright unless the administrator explicitly overrides
  it. The same check already guarded people disconnecting *themselves*; it was
  missing one level up. The dialog also appears when nobody is at risk and says
  so — a warning that only ever shows up in an emergency is not read the first
  time it matters.

- **The user list shows who is linked, and who has no password.** Both facts
  were already known to the app and shown nowhere. The link is quiet, in the
  grey summary line; the missing password gets a badge, because it is the one
  thing that turns a disconnect into a lockout.

- **The log finally contains the crashes.** An unhandled server error was
  written by uvicorn to a logger that never reached Nexview's own log file. An
  administrator downloaded the log and precisely the crash was missing from it —
  it only existed in the container output. Every unhandled error now lands in
  the file with its full stack trace, and so do uvicorn's own start-up and
  shutdown errors.

- **A number for every request.** Each incoming request gets a short number
  that appears in every log line it produces. When something breaks, the error
  message shows that number: the user passes on six characters, the
  administrator types them into the log search and sees the complete course of
  that one click — instead of guessing at timestamps. Clicking a number in the
  log view filters to that request.

- **Four recording levels, switchable while running.** *Quiet*, *Normal*,
  *Detailed* and *Everything*, changeable under Settings → Log without a
  restart. The two deep levels switch themselves back off after 30 minutes, 2
  hours or 8 hours — a forgotten diagnostic level would otherwise overwrite the
  very lines it was turned on to capture. `NEXVIEW_LOG_LEVEL` overrides the
  setting for the case where Nexview does not start at all.

- **Every call to the outside is now visible.** TMDB, Radarr, Sonarr and the
  media server wrote nothing of their own; whether a failure showed up depended
  on which caller happened to catch it. Each call now produces one line with
  method, host, path, status and duration — as a warning when the other side
  rejects the key or fails, as a detail line when it worked. API keys are
  masked out of the addresses.

- **Log messages are English throughout.** About sixty messages were German,
  which made the log unsearchable — looking for "not reachable" missed half the
  cases. A test now keeps the rule: a German log message fails the build. The
  user-facing texts stay translated as before.

### Fixed

- **A second media server no longer displaces the first.** Every account held
  exactly one media server identity, in a handful of columns. Connecting
  Jellyfin while linked to Plex overwrote that link — name, address and the
  personal token, without a word. Identities now live in a table of their own,
  one row per provider, each with its own token. The columns remain as the most
  recently linked one, and exactly one function writes them.

- **Each server keeps its own access.** Jellyfin issues tokens per *device*,
  and Nexview announced itself under a single device name for everything. Signing
  in personally therefore revoked the administrator's server connection, and two
  people signing in revoked each other. Every purpose now has its own device
  identity.

- **The library count on a server's page was not that server's.** It counted
  distinct titles across all connected servers and showed the same number on
  every page — and before that it counted rows, so the same film indexed by two
  servers was two titles. The page now shows only when it last synced, and the
  button syncs only the server whose page it is.

- **One unreadable notification no longer silences the whole bell.** A
  notification whose type had been renamed away could not be unpacked, and the
  error took the entire list with it — the bell showed "nothing new" while the
  counter beside it kept claiming unread messages. Measured in a real database:
  four such rows had been hiding a genuine, unread piece of feedback for a
  week. Rows of a vanished type are now cleared away at start-up, with a
  warning naming them, and the same applies to the outgoing channel queue.

- **The comment above passwordless accounts was lying.** It claimed such an
  account could set a password later in its profile. It cannot — that route
  demands the current password, which the account never had. The two routes
  that do work are written down there now.

- **The level filter no longer hides errors.** Choosing "WARNING" in the log
  view compared for equality and therefore left out the ERROR lines — exactly
  the ones being looked for. A level now means "this level and above".

- **The watchlist hint pointed at the wrong tab.** It told people to link their
  Plex account under "Security" — which is where media server accounts used to
  live, until this release moved them into a tab of their own. It now names the
  place they are actually in.

- **Downloading the log includes the rotated files.** During a hunt the
  interesting section has often already rolled over into the previous file.

---

## 0.17.0 – 23.08.2026

### New

- **Browse: the whole catalogue, not just the last few weeks.** Discover only
  ever knew a rolling window of at most a year, so everything older was
  reachable by free-text search alone. The new *Browse* area opens the back
  catalogue in shelves — *Hidden gems*, *Timeless classics*, *Under 90
  minutes*, five decades and every genre. Every shelf works on an empty
  library and without any personal data.

- **"What to watch?" — a few questions instead of a search.** The first menu
  entry opens a guided pick: who is watching, what are you in the mood for,
  does it have to play tonight, how much time is there, something new or
  something familiar. Six questions at most, and the tree adapts — answering
  "only what's already here" drops the "hidden gem?" question, because in your
  own library it has no meaning. It ends in twelve suggestions with a re-roll.

- **"With children" sets an age rating, not a genre.** It means demonstrably
  "FSK 6 at most", using the same translation the child accounts use. No mood
  is taken away for it: with that limit, horror still returns *The House with
  a Clock in Its Walls* and *The Witches* — children like a scare, they need a
  limit, not a nanny.

- **Filtering by six human questions instead of thirteen controls.** How much
  time, what are you after (genres, includable *and* excludable), which era
  (real decades), how well known, does it have to be here already, and the
  order. What is gone was never a question anyone asks: original language,
  region, "released in DE only", "hide unrated", "hide without description",
  "feature films only", "known titles only" — all of them workarounds for
  noisy data, and all of them now silently the default.

- **What you set lives in the address.** A filtered result can be sent to
  someone or bookmarked. The old Discover page forgot every setting the moment
  you left it.

- **Personal shelves, on top and never as a gatekeeper.** *Not seen in a
  while* uses your watch history; *Because you like X* gives every favourite
  its own row instead of blending them into one. At most three of those, one
  slot always held by the newest favourite and the others rotating daily, so
  a hundred favourites do not mean a hundred shelves — and none of them stays
  invisible forever.

- **A list view** on every Browse page, showing runtime, age rating and two
  lines of plot side by side.

### Changed

- **"Discover movies" and "Discover series" have left the menu.** After the
  rebuild, Browse answered every one of their questions better except one —
  "what came out recently?" — and that is now the *Newly released* shelf. The
  addresses `/filme` and `/serien` still work, so old bookmarks do not break.
  The menu is now *What to watch? · Browse · People · Calendar · Search*.

- **The calendar asks both noise questions at once.** "Big studios" and "known
  titles" were offered as alternatives, but measured against a real week
  neither contains the other: the studio list catches brand-new streaming
  series that have no votes yet, the vote floor catches good titles without a
  big distributor. Choosing one always lost the other half. *Sensible
  selection* now asks both — 18 real entries for that week instead of 12 or
  15, with 68 noise entries still filtered out.

- **The calendar separates films from episodes** instead of "everything /
  already requested". Your own titles already sit in their own group at the
  top of each day, so that switch only hid what came below it. "Which episodes
  air this week?" and "which films are released?" are two different questions.

- **The filter labels are questions.** "Selection / Release / Scope" became
  "Show what? / Which date? / How strict?" — three abstract nouns told nobody
  what the control does.

### Fixed

- **"Highest rated" returned noise.** TMDB has no weighted ranking, and the
  floor was five votes: a film with twenty votes and a 9.4 average outranked
  *The Godfather*. The floor is 300 now. This also repairs the sort on the old
  Discover page.

- **A runtime limit was not kept.** TMDB's own `with_runtime` filter is
  unreliable — asking for at most 95 minutes returned a 97-minute and a
  99-minute film. The runtime is checked again on the server, where the real
  value has been fetched anyway.

- **Films between 126 and 129 minutes were unfindable by runtime.** "Up to 2
  hours" ended at 125, "may be long" started at 130. The steps now meet
  exactly. "May be long" was also a lie — it read as "no upper limit" but was
  built as "at least two hours"; it is called *Over 2 hours* now.

- **`watch_region` was sent on every series query and did nothing.** Without
  `with_watch_providers` beside it, TMDB ignores the parameter. Removed.

---

## 0.16.1 – 23.08.2026

### Fixed

- **An age-restricted account could see series rated above its age.** TMDB may
  list the same country more than once in a series' content ratings - Gravity
  Falls appears under DE twice, first "12", then "6". The summary per country
  took the last entry instead of the strictest one, so FSK 12 turned into
  FSK 6 and a six-year-old was shown the series. The movie branch a few lines
  above already did this correctly.

  The filter itself was never at fault: The Simpsons (one DE entry, FSK 12) was
  refused correctly all along. Only the summary was wrong, and only for series.
  This affects every age restriction, not just the child accounts added in
  0.16.0 - the setting has existed since 0.3.0.

---

## 0.16.0 – 23.08.2026

### New

- **Child accounts.** A parent can now create logins for their own children,
  in their profile under "Children" - name and password, no email address, no
  Plex account. Each child has an age, a set of categories and a language, all
  managed by the parent. The account is subordinate: it cannot change its own
  password, cannot add an address, and cannot reach a single adult part of the
  app. Creating them needs a permission the operator grants per account
  (Settings → Users → "May create child accounts"); the tab stays visible
  without it and explains what the feature does, with one button that files
  the request as a ticket. Deleting a parent deletes their children with them.

- **A view built for children.** A child signing in gets a completely separate
  app - its own bright colour world, three destinations, large tap targets, no
  bell, no tickets, no watchlist, no settings. The start page shows the
  enabled categories as picture tiles; behind each one sit all the titles at
  once, split into "You can watch these now" and "You can wish for these".
  Search stays inside the enabled categories. Everything is filtered by the
  child's age - and for movies the filter runs at TMDB itself, which is the
  difference between a full page and an empty one.

- **Wishes instead of requests.** A child does not request, it wishes. Nothing
  happens until the parent decides: approving turns the wish into an ordinary
  request **in the parent's name** - their quota, their storage, their usual
  approval path, with the operator deciding the download as always. Declining
  takes a short note that the child gets to read. The child sees its own list
  with four plain states: waiting, on its way, it is here, not this time.

- **"View as {child}".** A yellow button in every child's settings opens the
  child's app exactly as the child sees it - same component, same rules, only
  read-only. Checking what actually reaches a child should not require
  borrowing their password.

- **What's new, for the last five versions.** The update notice now keeps the
  notes for the last five versions and offers a switch per version, marking
  what has arrived since you last acknowledged one. Skipping a version no
  longer means never learning what was in it. Reachable any time from About →
  Everything that is new, not only in the days after an update.

### Changed

- **The per-account age restriction is gone from user management.** Anyone
  with a full account is treated as an adult; children get a child account,
  and their age is maintained by their parent. Two routes to the same lock
  were two places for it to drift apart. Existing accounts have their age
  cleared on the first start after the update - child accounts keep theirs.

- Notification mails gained a switch for children's wishes. It only appears
  for accounts that actually have an active child account.

### Fixed

- Profile tabs can be addressed directly again (`?reiter=…`), which is how the
  bell now jumps from a child's wish straight to the place it is decided.

---

## 0.15.0 – 23.08.2026

### New

- **Storage quotas: gigabytes instead of counts.** "Three films a week" sounds
  fair until one of them is 60 GB. Quotas can now be measured in occupied
  storage instead, and exactly one of the two currencies applies at a time -
  the choice sits at the top of Settings → Storage quotas, with a default
  limit for every account below it, refinable per account in user management
  (empty = the default, 0 = unlimited). Measuring runs in both modes, so you
  can watch for a while before you limit anything.

  The hard part of any storage quota is that space is not consumed per person,
  it is occupied jointly - so a personal GB account is always a convention.
  Nexview makes that convention explicit: your account covers what you brought
  into the house yourself and still claim. Everything already in the library
  at the first measuring run belongs to the **house inventory**, so every
  account starts at zero, and the operator can move any title there later -
  it then counts against nobody while the file stays exactly where it is.

  Every switch of the mode, in either direction, rebooks all attributed titles
  to the house and resets every account, because attribution runs even in
  count mode and nobody should be overdrawn on day one by a history they never
  knew was counting. A dialog states the real numbers first. The same
  mechanism is the emergency exit: "Reset and turn off" cleans up the whole
  state in one click, without touching code, container or a single file.

  A request is blocked only when the account is **already** overdrawn - if
  there is anything left, it goes through, because nobody can know in advance
  how large a file will be. The operator sees the requester's state in the
  approval list and is warned, not blocked.

  The feature carries an "In Dev" badge on purpose: it is being tried out in
  real operation. It ships switched off; if you never enable it, nothing of
  this appears anywhere.

- **My storage, and the watched eyes.** Everyone sees their own occupied space
  against their limit under Profile → Storage quota, and below it every title,
  largest first - because whoever is asked to make room must first see where
  the room went. An eye on each row answers the question behind every
  clean-up: green means watched, red means not yet, and for seasons green
  explicitly means *every* episode. Without a media-server link there is a
  question-mark eye with the reason, because a red eye would claim "never
  watched" where nobody can check. A "Watched only" filter shows the hand-back
  candidates at a glance.

- **Handing a title back, instead of being stuck with it.** "I don't need
  this" puts a title up for a decision - and nothing happens yet: it stays on
  disk and keeps counting until the operator has decided, otherwise handing
  back would be a free pass. For seasons the person states their wish right
  away: have it deleted, or keep the episodes and only stop the downloading.
  The operator's queue carries that wish on the row and offers three outcomes:
  to the house (file stays, account freed), delete (with the actual file list
  in view - the only step with no way back) or stop following. Whoever handed
  back is told what was decided.

  Because deleting exists now, a recycle bin belongs to it. It can be entered
  directly in Nexview and Radarr and Sonarr do the work; Nexview says before
  every deletion whether the files move there or are gone immediately. And the
  one condition without which none of this adds up: titles must stay in Radarr
  and Sonarr. Removing a title there while keeping the file creates something
  Nexview can measure but never delete again - a red box in the settings warns
  about exactly that.

- **Requesting single seasons.** Instead of only "the whole series", seasons
  can be picked individually - with checkboxes, several at once, plus a
  separate option for future seasons rather than that being silently included.
  Anything already there or requested is greyed out with the reason rather
  than hidden, and 4K and 1080p are kept apart: a running request in one tier
  no longer blocks the other.

- **Deleting an account is now a decision, not a side effect.** Users and
  approvers request deletion in their profile under Security; the request
  reaches the operator as a ticket. When the account is deleted, the operator
  decides about the estate with the list in view: a checkbox per title for
  "to the house" or delete, "mark all" for the common case, keep-or-delete per
  started season including whether to keep downloading, and open orders
  without a single file are cancelled. Nothing keeps downloading ownerless
  afterwards.

- **"Everything that's new" after an update.** Administrators get a strip at
  the top of the app once a new version is installed, and a window behind it
  that says in words what the update brought - each feature with the path to
  where it lives, the small stuff kept small at the bottom. "Got it, don't
  show again" stores the version rather than a flag, so the next update brings
  the hint back by itself. Users never see it: they did not install anything.

### Fixed

- **A season counts as downloaded only when all of its episodes are there.**
  Sonarr's "has file" is a statement about the whole series, which was the
  same thing while only whole series could be requested. With season requests
  it meant that three files in one season marked five seasons as finished at
  once - and sent five completion notices in the same second.

- **Notifications about seasons name the season** - in the bell, in the
  channels and by mail. Five identical "Baywatch" messages answered nothing.

- **Season monitoring repairs itself.** Sonarr's `addOptions.monitor: "none"`
  acts asynchronously and swept away the very season Nexview had just switched
  on, which left a request searching forever. Every poll now checks and
  rebuilds the monitoring from Nexview's own requests.

- **A 4K file in the regular Radarr is no longer mistaken for a separate 4K
  copy.** The media server measures the resolution of a file, not which
  instance manages it.

- **A title that vanished from Radarr or Sonarr no longer stays on
  "searching" forever** - the request is cancelled after a grace period, and
  the title becomes requestable again.

- **Free disk space no longer counts the same volume twice.**

- **Windows in the app have exactly one visible way out** instead of two.

---

## 0.14.0 – 21.08.2026

### New

- **Discord as a notification channel.** A webhook posts straight into one
  channel of your Discord server - no bot, no extra account, just the webhook
  URL from the channel's settings (Edit → Integrations → Webhooks). Messages
  arrive as embeds: the color carries the meaning (orange waiting, green
  available, red rejected), the poster shows as a thumbnail, the title links
  back into Nexview, and the sender wears the Nexview logo as its avatar. The
  URL is the secret here, so it is stored encrypted and only ever shown
  masked. Verification works like every push channel: a four-digit code in
  the test message proves that things really arrive before anything is saved.

- **A universal webhook channel.** Nexview sends every notification as a POST
  with a fixed JSON body - event type, urgency, title, text, poster and link -
  to any address you choose, with an optional Authorization header (stored
  encrypted). Built for Home Assistant, n8n, Node-RED and hand-rolled
  scripts: the receiver picks out whatever fields it needs. The confirmation
  code travels in the JSON's title field, readable wherever your requests
  land.

- **Apprise support - a channel Overseerr and Jellyseerr don't have.** Point
  Nexview at a self-hosted Apprise API and it forwards every notification to
  the services stored there: Signal, Matrix, SMS gateways and over a hundred
  more. Nexview only knows the server's address and a configuration key; the
  credentials of the target services never leave the Apprise server. Nexview's
  urgencies map onto Apprise's message types, and a mistyped or empty key is
  reported as the error it is - Apprise itself would happily answer "OK" to a
  key that delivers to nobody.

---

## 0.13.0 – 21.08.2026

### New

- **"Already watched" now works for everybody, not just the server owner.**
  So far the eye badge was reliable only for the account whose Plex access is
  stored in the settings. Everybody else was read from the server's playback
  history, which Plex caps at roughly 500 entries and which never hears about
  titles marked as watched by hand.

  Since the watchlist arrived, every Plex sign-in stores the person's own
  access, and the sync now reads their complete watched state straight from
  the library counters. The history remains only as a fallback for accounts
  that have never signed in with Plex. And what Plex says counts in both
  directions: removing a checkmark there removes the eye here too - except for
  titles that have left the library entirely, which keep it, because deleting
  a file does not undo having watched it.

  Getting there needed one more thing. Plex keeps two different things that
  both go by the name "token": the account token from signing in, which
  plex.tv accepts, and a separate access token per server, which is the only
  one a server accepts from a *shared* account. Both are the same for the
  owner, so nothing ever looked wrong while only the administrator's access
  was used - and for everybody else this had never worked at all. It was
  reported as "Plex no longer accepts your token", and signing in again did
  not help, because a fresh account token is the same wrong kind.

- **A red banner for whoever's Plex access really has expired**, under the menu
  on every page, with the sign-in happening right there - code and link
  included, because browsers block the popup often and on a phone almost
  always. Only the person affected sees it; nobody else can fix it. It clears
  the moment the sync succeeds again, not only after signing in, so a banner
  never outlives its cause.

- **Nexview now records how much space each person occupies.** Nothing is
  limited yet and nothing is deleted - this first step only measures and will
  only become visible in the interface with the next one. Sizes come from
  Radarr and Sonarr, which report them in answers Nexview already asks for, so
  it costs no extra requests. Shows are counted **per season**, which is the
  finest grain available without asking Sonarr once per series.

  The whole thing is off by default and switched on under *Settings → Storage
  quotas*. With it off, Nexview behaves as it did before: no tab, no card, no
  distribution, and nothing is measured either.

  Once on, everybody sees their own figure under *Profile → Storage*, largest
  item first, because the question anyone asks here is "where is my space?" -
  and a single show can weigh as much as two hundred films. Administrators
  see the distribution across everyone on the statistics page, every account
  listed even at zero, so nobody wonders why a name is missing.

  Everything already in the library when measuring starts belongs to the
  house and counts against nobody, so no account begins in the red. From then
  on whoever requested a title carries it, and a finished download is charged
  within the same minute rather than at the next hourly pass.

  A title removed from Radarr but kept in Plex keeps counting, because the
  space is still occupied - a common way of working is to download until the
  quality is right and then drop the entry from Radarr. For that case the
  file size is read from Plex as well, from a structure the library sync
  already walks through.

- **A bell notice (and, if you opt in, an e-mail) when Plex no longer
  accepts your stored access.** That happens after a password change or a
  "sign out everywhere" – and previously the watchlist and the watched sync
  just silently stopped. The notice arrives once per incident, links to the
  profile page where one new Plex sign-in fixes it, and the e-mail has its
  own opt-in switch under profile → notifications, visible only to linked
  accounts.

### Fixed

- **A request could become impossible to approve.** If the target folder was
  the approver's to choose, and the operator then switched to a fixed folder
  while a request was already waiting, the interface stopped offering the
  choice while the server kept demanding one. The request could then neither
  be approved nor repaired. Whether a folder is needed now depends solely on
  whether the request has one - the approver may always choose, whatever the
  rule says at the time.

- **The log names people, not placeholders.** "Plex no longer accepts the token
  of user" reads like a template; it now says "user (Dilara)" where a display
  name exists. And a rejection from Plex records the actual HTTP status, so it
  is possible to tell "who are you" from "you may not" - that distinction had
  been thrown away, which made this whole class of problem undiagnosable.

- **The ticket and new-account mail switches finally send mail.** Both
  profile switches existed and were saved, and the outbox even recorded
  "mail wanted" – but no template was ever written for these two kinds of
  message, and the outbox silently discarded jobs it had no template for.
  Ticket mails (new ticket, replies, status changes – linking straight into
  the conversation) and the new-account notice for administrators now
  actually arrive; tests follow the delivery all the way to the finished
  mail so a switch without a template can never stay silent again.

---

## 0.12.0 – 20.08.2026

### New

- **Notifications: ntfy, Gotify, Telegram and e-mail as system channels.**
  Under Settings → Notifications an administrator points Nexview at the
  services that should hear about what happens – starting with requests
  waiting for approval, on the phone within seconds, poster attached and a
  link straight to the approval list. These are shared inboxes for the whole
  installation, deliberately separate from the personal ways (bell and
  personal e-mail) that everybody configures in their own profile.

  **As many targets as you like, on tiles.** One tile per inbox, plus a "+"
  tile to add another. Services with two levels get both of them: an ntfy
  server carries any number of topics, a Telegram bot any number of chats –
  the connection is stored once, the inboxes underneath choose their own
  language, urgency and events. Every tile has a power button to take a
  target out of service without deleting it; a disabled instance silences
  its inboxes with it.

  **A four-digit code decides whether a push target counts.** HTTP 200 from
  a push service only means "accepted", never "arrived" – a wrong topic, an
  app with no subscription, a muted notification are all answered politely,
  and nobody notices until the first real message fails to show up weeks
  later. So the test message carries a code, and only somebody who can type
  it back gets to save; setting up ntfy or Telegram creates instance and
  first inbox in one confirmed step. E-mail targets skip the code on
  purpose: behind a shared mailbox or an automation there may be nobody to
  read one.

  **Telegram sets itself up.** A built-in step-by-step guide covers the way
  from creating a bot at @BotFather to the first message; the token check
  fills in the bot's username, and "fetch chats from the bot" lists everyone
  who has written to it – no third-party ID bot, nothing to copy by hand.

  Underneath sits an outbox of its own with its own loop. Push notifications
  get the same reliability the e-mails have – three attempts, then the last
  error stays visible on the tile – but on a ten-second beat instead of the
  status poller's two minutes. And one message per *event* and inbox, not
  per recipient: a request waiting for three administrators appears once,
  not three times.

### Fixed

- **A Radarr/Sonarr timeout no longer counts as failure.** A timeout means
  the answer went missing, not the request: Sonarr had long created the
  series and started searching while Nexview recorded "failed" and refused a
  new request. Such requests now stay at "approved" and the status poller
  resolves the uncertainty on its own within minutes.
- **Failed, cancelled and deleted titles can be requested again.** The
  server accepted a new request all along – the interface just no longer
  offered a button for it, locking a title away forever after one mishap.
- **One failed media-server sync no longer swallows the mails of that
  round.** The database session was left mid-rollback, so the very next step
  in the same pass – sending mail – died of an error it had nothing to do
  with.

---

## 0.11.3 – 20.08.2026

### Fixed

- **The home page finally asks Plex.** Its two suggestion sections
  ("trending" and the curated picks) filtered "already there" using only
  Radarr/Sonarr and your own requests – the media server was never
  consulted. A film whose Radarr entry was removed but which still lives in
  Plex was offered as a suggestion, while search and detail pages correctly
  showed it as in the library. Two pages, two truths – reported for
  "Backrooms".

- **A namesake from the same year no longer inherits the library badge.**
  The title fallback of the media-server match treated "same name + same
  year" as "same film" – which made a four-minute short called "Backrooms"
  (2026) appear as in the library next to the real feature film. The fallback
  now only applies to Plex entries that carry no id at all (old agents);
  where the entry knows its TMDB id, a mere name match no longer counts.

- **A wrong secret key no longer fails silently.** All stored credentials
  are encrypted with NEXVIEW_SECRET_KEY (or the auto-generated
  data/secret.key). If that key changes – typically because the file was
  not inside the mounted volume when the container was recreated –
  decryption used to quietly return nothing: the Plex connection looked
  "gone", TMDB fell back to demo data, and nothing anywhere said why. There
  is now a plain-words warning in the log and a banner on the services page
  naming the cause and the fix.

- **The library sync log names both numbers.** "443 titles" next to 446
  entries in Plex looked like a loss; the log now says how many entries were
  folded because the same film lies in several libraries (1080p and 4K).

- **The mail switch "my request was decided" is hidden for approvers and
  admins** – whoever may approve never waits for a decision, so the switch
  could never do anything for them.

- **The people page now says "Search for a person"** instead of "Search for
  a title".

- **The log now answers the questions debugging kept asking.** A startup
  report states where the secret key comes from, whether data/secret.key
  exists, which stored secrets are readable, and the media-server connection
  state. Unreadable secrets are warned about by name, connecting logs each
  milestone and re-reads the saved settings to catch a save that did not
  stick, and disconnecting names who pressed the button.

---

## 0.11.1 – 20.08.2026

### Fixed

- **Times were shown two hours off.** The server stores timestamps in UTC but
  sent them without a timezone marker – and a date *with* a time but *without*
  a zone is read as local time by the browser, so the UTC number ended up on
  screen unchanged ("last checked 12:01" at 14:01). Timestamps from the server
  are now read as what they are and converted to the viewer's local time.

- **"Check now" for the media server library could disguise a failure as
  success.** If reading the Plex library failed – server unreachable from the
  container, token rejected – the button silently kept showing the old count
  and timestamp. There was no place where the cause could ever become
  visible. The button now reports the actual error in plain words; the hourly
  background sync still swallows failures so one outage cannot stop the rest
  of the run.

---

## 0.11.0 – 20.08.2026

### New

- **Your Plex watchlist, inside Nexview.** Under *Profile → Watchlist → Plex*
  everything you put on your Plex watchlist shows up as the same tiles you
  know from the catalogue, with the same badges – already downloaded,
  requested, 4K, watched. Clicking one opens the ordinary request dialog, so
  quota, approval, blocklist and age limit apply exactly as they do
  everywhere else.

  Nothing happens on its own: there is no background sync and no automatic
  requesting, every title takes a click. A filter narrows the list down to
  what is not there yet.

  An administrator switches the whole thing on under *Settings → Watchlists
  → Plex*; the tab only exists while a media server is connected, and it is
  off by default. Reading a watchlist requires the personal Plex token of the
  person it belongs to – the administrator's own access cannot see it – so
  Nexview now keeps that token (encrypted) whenever somebody links their Plex
  account or signs in with Plex. Installations updating from an earlier
  version have none yet, because it used to be discarded after the access
  check: there the page offers one Plex sign-in, and never asks again. The
  same path repairs a token Plex stops accepting.

  Only the account already linked to the profile may be used, so nobody can
  point Nexview at a stranger's watchlist and spend their quota on it. Plex
  does not name TMDB ids in the watchlist itself; Nexview looks each title up
  once and remembers the result, and says how many titles it could not match
  rather than dropping them silently. Requests started this way carry a
  "watchlist" badge and have their own tab in *My requests* and *All
  requests*.

- **Long request lists now come in pages.** *My requests* and *All requests*
  show twenty entries at a time, with back/next buttons underneath. The
  counts on the filter buttons still cover the whole list, not just the
  visible page, and switching a filter jumps back to page one. Approving all
  of a user's requests at once still covers every waiting request, not just
  the ones on the current page.

- **The watchlist page can switch between tiles and a list**, the same way
  the discover pages do.

- **The request button simply says "Request" now** instead of "Add to
  Radarr"/"Add to Sonarr" – what happens behind it is not the requester's
  concern.

### Fixed

- **A title kept only on the media server could be requested a second
  time.** The pages already showed such titles – files whose entry was
  removed from Radarr/Sonarr but which still live in Plex – as "in your
  library", but the server itself only asked Radarr/Sonarr before accepting
  a request. A stale browser cache was enough to start a duplicate download.
  The server now refuses, with the same per-instance logic the pages use: a
  4K-only copy does not block requesting the 1080p version, and vice versa.

- **Series without a TMDB id confused the calendar.** Sonarr and Radarr
  report `0` when they do not know a TMDB id, not an empty value – and that
  zero slipped through as if it were a real id. Affected entries could not be
  deduplicated against new releases, linked to a dead detail page, and
  disappeared entirely for age-restricted accounts. Measured on a real
  library: two out of 175 series. A zero now counts as "unknown".

---

## 0.10.0 – 20.08.2026

### New

- **A second Radarr/Sonarr for 4K.** Optional: enter a second instance under
  Settings → Services → Radarr (or Sonarr) and the same title can be requested
  once in 1080p and once in 4K — two files, two folders, two requests. Each
  instance keeps its own root folders and quality profiles, and Nexview refuses
  a profile or folder that does not belong to the chosen instance: the ids of
  the two instances collide, and Radarr accepts an unknown one without
  complaint. Per user there are three switches — request 4K movies, request 4K
  shows, 4K without approval — all off by default, and the blocked-profile list
  exists per instance. Cards gain a compact "4K" badge next to the usual one,
  the request dialog a Standard/4K switch. Enter no second address and none of
  this is visible anywhere.

- **The approver can pick the root folder.** A new setting hands the choice of
  root folder and quality profile to whoever approves a request, instead of the
  person making it. Meant for libraries split across several folders — sorted by
  genre, say — where the requester cannot know where a title belongs. Their
  request then arrives without a folder and always waits for approval, even if
  they normally have auto-approval; the approver picks both when approving, and
  a bulk approval asks once per media type. Anyone who may approve still picks
  when requesting: they would be the one deciding later anyway. Off by default —
  with it off, nothing about requesting or approving changes.

### Fixed

- **A show could inherit another show's episodes.** Matching against Sonarr
  fell back to the title alone whenever TMDB had no TVDB id — and a title is
  not unique. Reported for "Countdown" (1982), which picked up a completely
  different show of the same name in Sonarr and appeared as already downloaded,
  green ticks on thirteen episodes included, although the server had never held
  it. The fallback now also requires the year to match, with one year of
  tolerance for first-broadcast dates that differ between databases; without a
  year on either side the match is dropped. A missed title costs a duplicate
  download, a wrong one removes a title from the catalogue for good and nobody
  ever learns why.

- **A file deleted from Radarr stayed "already downloaded" forever.** The state
  of a request outranked the library, so a title removed from Radarr (or from
  Radarr *and* the media server) kept its badge and could never be requested
  again. Requests whose file has vanished now move to a state of their own,
  "deleted again", and the title becomes requestable. Delete it only from
  Radarr while keeping it in Plex and the badge stays — because it is still
  true.

- **A copy in the media server can now be told apart by resolution.** Plex
  reports the resolution of every file it holds; Nexview did not record it and
  therefore could not tell whether a copy outside Radarr was the 1080p or the
  4K version. It is recorded now, which gives the 4K axis the media-server
  fallback it never had. Where no second instance is configured, any copy still
  counts — there is only one axis to answer for.

- **The same film in two Plex libraries broke the whole sync.** Splitting 1080p
  and 4K into separate libraries is a common setup, and Plex gives the same
  film the same guid in both. The unique key rejected the second row and the
  import aborted — leaving Nexview with *no* media-server titles at all and
  showing a well-stocked library as entirely requestable. Both rows are now
  merged into one title carrying both resolutions.

- **A slow Radarr cost every page load fifteen seconds.** Nothing was recorded
  on failure, so each request started the timeout over; with two instances on
  one machine it added up and looked like "Radarr is not responding" although
  Radarr was merely busy. A failed instance is now left alone for 30 seconds.

- **"Recently downloaded" listed titles that were gone**, and the person page
  answered with an error whenever a 4K instance existed.

- **Root folder and quality profile now move together.** Setting one of them to
  "the approver decides" always deferred *both* — a request waits for the
  approver either way, and they then set both. The other setting was therefore
  doing nothing at all, silently: found on a real installation where "root
  folder: the requester picks" had no effect whatsoever for movies. Picking
  "the approver decides" now switches the other one along, with a note saying
  why, and switching back releases both. The server enforces the same pairing,
  so the stored configuration can no longer describe a state that does not
  exist.

---

## 0.9.0 – 19.08.2026

### New

- **A release calendar.** A new "Calendar" entry shows one calendar week at a
  time, grouped by day, with a year and week picker to jump anywhere. Each day
  separates two things: **your titles** — the episodes and movies already in
  your Radarr and Sonarr, marked "still missing" when they aired but the file
  never arrived — and **new releases** you do not have yet. Request and
  favourite straight from the calendar.

  Movie dates switch between **cinema** and **digital & disc**, and both are
  read for *your* region. That distinction matters more than it sounds: a film
  can open in cinemas in March and land digitally in June, and for a media
  server only the second date is actionable. TMDB filters on the regional date
  but reports the worldwide one, so the calendar reads the regional date back
  out of the detail response — which Nexview already fetches for every title,
  so this costs no extra requests.

  New releases are limited to the major studios by default (switchable to
  "known titles" or everything). For shows that means the big streaming
  services, restricted to scripted series, mini-series and documentaries: TMDB
  files every companion podcast, talk show and game show under the same
  network, and they made up roughly half the results. Productions from outside
  a handful of countries are left out as well — the streamers commission
  worldwide, and a Thai original is noise in a German calendar.

### Fixed

- **Watch history is now read per account.** Plex only returns the history of
  the account you ask with, and caps each answer at around 500 entries. A
  household with one heavy viewer therefore pushed everyone else out of the
  result entirely. Note that Plex records *playback* only — a title ticked off
  by hand leaves no trace there.

---

## 0.8.0 – 19.08.2026

### New

- **Titles already on your media server are recognised.** Nexview now reads
  what sits in your Plex library and spots what never came through
  Radarr/Sonarr — a file copied by hand, or a collection that predates the
  *arr setup. Those titles show as "In library" and cannot be requested a
  second time. The sync runs hourly in the background; the settings show how
  many titles are indexed, when it last ran, and offer a "Sync now" button.

  Matching goes by TMDB id, then TVDB id, then title — and always checks the
  year as well. That is not caution for its own sake: in a real library of
  3509 films, exactly one carried a **wrong** TMDB id, pointing at an entirely
  different film. Without the year check, anyone searching for that other film
  would have been told they already own it. A missed title costs a duplicate
  download; a false one takes a title out of the catalogue for good, with no
  visible reason.

- **You can see what you already watched.** Titles you have seen on your media
  server carry a small "Watched" marker next to their status. Everyone sees
  only their own — what one person watched is nobody else's business — and the
  marker sits beside the status rather than replacing it, so "already
  downloaded" stays visible.

  Two sources feed it, and both are needed: for the account whose access is
  stored, the counter Plex keeps on each title, which is complete; for
  everyone else the playback history, which Plex only keeps for a while.
  Measured on a real server: the history held 38 films, the counters 354. For
  shows, one watched episode marks the show.

---

## 0.7.0 – 19.08.2026

### New

- **Sign in with Plex.** Connect your Plex server once and your household can
  sign in with their Plex account instead of a Nexview password. Only people
  who actually have access to your library get in — that is checked against
  the server's own identifier, so a stranger's Plex account is turned away
  even though it authenticates fine.

- **Connect by picking your server.** Setting it up needs no token hunting:
  the administrator signs in with Plex and chooses a server from a list. His
  own account is linked in the same step — otherwise his first Plex sign-in
  would have created a *second*, ordinary account whenever his Plex address
  differs from his Nexview one.

- **Link an existing account.** Everyone already invited can connect their
  Plex account under Profile → Security, and keeps signing in with a password
  as well. Both ways lead into the same account. Accounts created through Plex
  have no password at first; the profile offers to set one and refuses to
  unlink while that would lock you out.

- **New accounts on your terms.** Anyone with library access can get an
  account on first sign-in, with the role, quota and age limit you set in
  advance — or you turn that off and keep invitations mandatory. New accounts
  never get automatic approval, and "administrator" cannot be a default role.
  You are notified in the bell, optionally by mail, whenever an account
  appears.

- **Deleting a user now sticks.** Removing someone who signed in through Plex
  blocks that Plex account from creating a new one. Without it, deleting was
  pointless — they would simply sign in again. The block outlives the account
  and can be lifted in the settings.

- **Directing and writing show up in a filmography.** A person's page used to
  list only what they acted in, so a director's page showed their cameos
  instead of the films they made. Directing, writing, screenplay and story
  credits now appear alongside the acting roles, labelled as such. One entry
  per title: the work itself wins over a cameo in the same film, and directing
  wins over writing when someone did both.

### Changed

- **Settings are sorted by service.** "Services" now has a second row of
  buttons — General, TMDB, Radarr, Sonarr, Plex — instead of five blocks you
  had to scroll past. Region, language and demo data live under "General",
  since they belong to no single service.

- **"Users may choose the folder" is now set per service.** Movies and shows
  have different folder layouts, so wanting fixed paths for shows no longer
  forces them on movies. The switch sits with Radarr and Sonarr respectively,
  right above the folder it governs. Existing installations keep whatever the
  old shared switch was set to.

### Under the hood

- The media-server connection sits behind one interface with Plex as the first
  adapter, so Jellyfin and Emby can be added later without rework. Nothing
  outside that package knows which provider is in use — the database columns
  and settings are provider-neutral too.
- Only servers you own are offered when connecting. Ones merely shared with
  you are hidden and counted, because picking one would tie sign-in to the
  wrong circle of people and break watched-state later. The chosen address is
  probed before it is stored; if nothing answers you are told, and sign-in
  still works, because that runs through Plex rather than the server.
- Prepared but not built: detecting titles added to the server outside
  Radarr/Sonarr, and a per-user "already watched" marker.

---

## 0.6.0 – 18.08.2026

### New

- **People section.** A new "People" entry in the main menu opens a page to
  browse popular actors and search anyone by name, with "Load more" to page
  through. Three buttons — Actors, Directing, Writing — filter the search by
  craft, since TMDB has no "popular directors" list to browse (measured: 1 in
  100). Clicking a person opens the existing page with their photo, bio and
  full filmography.

- **Favourite people.** People can be hearted like titles — on the People
  page, in search results and on a person's page. "Favourites" now has two
  sections, one for movies & shows and one for people. And the curated home
  row now blends in the best films and shows of your hearted actors, alongside
  the recommendations from your favourite titles.

- **Where to stream.** Title pages now show which providers carry a title in
  the viewer's own region — grouped into subscription, free, rent and buy,
  with the provider logos. Each user sees their own region: a US account gets
  the US list, a DE account the German one, from the same TMDB response. The
  data was already being fetched from TMDB and simply not shown. As required,
  it is attributed to JustWatch with a link to their overview.

---

## 0.5.0 – 18.08.2026

### New

- **Light mode.** A moon/sun toggle in the header (also on the sign-in page)
  switches the whole interface between dark and light. The choice belongs to
  the account, not the browser: it is stored in the profile, so everyone in
  the household has their own default and finds it again on every device. It
  can also be set in the profile next to language and region. Not a single one
  of the ~980 colour usages was touched — only the ~25 colour values behind the
  `ink`/`mist`/`accent` roles are swapped, plus a darker red for text on white.
  Backdrop images are dimmed rather than washed out in light mode.

### Fixed

- Three colour steps (`mist-200`, `mist-400`, `accent-300`) were used in ~20
  places but never defined, so Tailwind emitted no rule and the text silently
  inherited its parent's colour. Invisible in dark mode; it made a detail
  page's tagline unreadable. Now defined in both palettes.
- On phones the header wordmark now collapses to the logo mark alone, leaving
  room for the extra toggle without crowding bell, language switch and menu.

---

## 0.4.3 – 18.08.2026

### New

- **"Show me others" under the suggestions.** If nothing under *You might also
  like* appeals, a button fetches the next twelve titles. The supply comes from
  two TMDB lists: the recommendations (what people liked who liked this title)
  and the similar titles (same genres and keywords). Measured against real
  data that gives four to seven rounds per title — even for completely unknown
  films, where TMDB only knows a handful of recommendations. Once the supply
  runs out, it starts over from the beginning.

### Fixed

- **The phone view went through a full pass** (360/390/430 px, all 20 views).
  Until now things were cut off in places where wrapping would have been right:
  - In *My requests* and *All requests* the title shrank to a single character
    ("S."), because the state and the button claimed the same line. On narrow
    screens the title now stands alone on the first line, with the label and
    the button beneath it.
  - Fifteen grids had no explicit base column. The implicit track is as wide as
    its widest content, not as wide as the screen — which is what let the home
    page be pushed sideways.
  - Tile titles on the home page, in the list view and under *Liked* now run
    over two lines instead of breaking off halfway.
  - The state label on the poster is no longer squeezed. If it does not fit
    next to the rating, the rating drops a line — before that, "Already
    downloaded" broke across the middle of the image.
  - Where a poster was missing, the title stood in the box as a substitute —
    but only as wide as its longest word, so it was cut off left and right. It
    wraps now.
- Two missing translations: the *Approved* state (`status.approved`) between
  approval and hand-off to Radarr/Sonarr, and the note under the public address
  in the setup wizard.
- After sending a request, the title page still said *Add to Radarr*, although
  the request had long since started. The same held for filmographies, category
  lists and the home page: until now only the tile lists were reloaded. Which
  views show the state of a request is now written down in exactly one place
  (`lib/refresh.ts`) — and everything listed there is refreshed after every
  change.

---

## 0.4.2 – 18.08.2026

### Fixed

- On a closed ticket the administrator still saw the sentence saying that a
  reply from the user would reopen it — which had stopped being true when the
  rule changed.
- The administrator could write a ticket to themselves. A recipient is now
  required.
- *Report a problem* also appeared for the administrator, although they are the
  person one reports to. Hidden for them.

---

## 0.4.1 – 18.08.2026

### New

- **The administrator can write to a user.** When opening a ticket they pick
  the recipient from a list; the ticket then belongs to the person written to,
  who finds it among their own and can reply. Who wrote it stands in the
  header. Until now the post only went one way.

---

## 0.4.0 – 18.08.2026

### New

- **Ticket centre.** Users open tickets with a subject and a text; everyone
  sees only their own. The administrator sees all of them, is told about them
  through the bell, replies, can amend their replies and can set the state to
  *Open*, *In progress* or *Closed*.

  An **approver is explicitly not an administrator** here: they decide about
  requests, but they see no one else's tickets. Anyone opening someone else's
  ticket is told "does not exist" rather than "forbidden" — a "forbidden" would
  already be the information that this number exists.

  **Closed means closed for the user.** They still see the history, but the
  reply box disappears; anyone who has more to say opens a new ticket. The
  administrator may still leave an addendum afterwards, without having to
  reopen the ticket for it.

  **Tidying up:** the administrator can delete closed tickets, one at a time or
  in a batch. Open ones cannot be deleted — anyone who wants one gone closes it
  first. That way the decision is a deliberate one, taken in two steps.

  Everyone may edit their **own** messages; that something was changed is
  visible afterwards. Nothing is deleted — a history with gaps is no longer
  readable.

  Every title page has *Report a problem*: the ticket then carries the
  reference by itself, nobody has to type the name out.

  The rating with a comment on finished downloads is untouched by all this — it
  sticks to the title, the ticket is for everything else.


- **Blocklist.** The administrator can block titles: nobody can request them
  any more, and they do not go to Radarr or Sonarr. Unlike with the age limit
  they stay **visible** — findable through search and discovery, carrying the
  *Blocked* badge and without a shopping cart. Anyone looking for one should
  get the answer, instead of requesting it three times in vain.

  When rejecting a request Nexview asks whether the title should go on the list
  at the same time — **only for the administrator**. An approver decides about
  the single request; whether a title belongs in the library at all is the
  operator's business. The server turns it away as well, not just the interface.

  The overview sits under *Settings → Blocklist*, complete with the reason and
  a button to release it again. Blocked is blocked for the administrator too:
  anyone who wants the title after all releases it — a deliberate step that can
  be followed afterwards.

- **Age limit per user.** The administrator decides whether an account is
  limited and how old the person is; only what is cleared for at most that age
  is then shown. Blocked titles disappear completely — from discovery, search,
  recommendations, filmographies, the home page and their own favourites.
  Requesting is turned away on the server as well, not just the button hidden.

  What counts is the rating of a country that **only the administrator** sets —
  separate from the region everyone may choose for themselves. Otherwise the
  limited person could simply set a country in which the title is not rated,
  and would be past the block. Where a rating for the chosen country is
  missing, the strictest of all countries applies.

  Titles with **no rating at all** stay hidden by default — "no proof, no
  entry". That can be switched off per user, because new titles are usually not
  rated anywhere yet: measured, the discovery page shrank from 20 entries to 2,
  and with unrated titles allowed it was 10. For a 16-year-old that may be
  acceptable, for a 6-year-old it is not — hence the choice instead of a fixed
  rule.

  Clearances from over 30 countries are translated into a minimum age for this
  — "FSK 12", "PG-13", "MA15+", "K-16" and "M/12" all mean the same thing. The
  mapping covers 97 % of the labels that occur in practice; where it would be
  uncertain, it deliberately does not guess.

  The block only works inside Nexview. Through Plex, Jellyfin or straight off
  the file share, everything stays reachable.
- **The language can be chosen in the profile**, together with the region in
  the *Language & region* tab (previously called *Discover*). The switch at the
  top of the header stays for switching quickly; in the profile the choice
  applies when you save, like every other setting.

### Fixed

- **Switching the language did not change the texts.** Titles and plots stayed
  in the language loaded first until you reloaded the page — the queries in the
  browser remembered their result without the language. They are now refetched
  centrally.
- **The genres stayed in the old language**, even once titles and plots had
  switched over: the cache for the detail data the genre names come from did
  not have the language in its key.

---

## 0.2.0 – 17.08.2026

### New

- **About page** in the footer: installed version, source, licence.
  Administrators also see there when a newer version is available — for that
  Nexview asks GitHub at most once a day. Can be switched off.
- **Email notifications**, four events switchable one by one: download
  finished, request decided, request waiting for approval, new rating or a
  reply to one. Everything is off by default; everyone can switch them on for
  themselves in their profile. The bell in the app is untouched by this.
- **Profile in tabs**: account, notifications, discover, security.
- **Region as a personal default** for the filter bar.
- **Season-by-season series requests.** Instead of the whole series, a single
  season can be requested. If the series is already running it is not created
  again — only the wanted season is added, and exactly that one is searched
  for. Counts as one request against the quota.
- **Root folder:** the administrator decides whether users may choose it when
  requesting. If not, a folder set by them applies to everyone. That is
  enforced on the server, not just hidden in the interface.
- **A detail page per title** instead of only a small window: cast with photos,
  directing, writing, studios, keywords, budget, recommendations — and for
  series the seasons to unfold, with all episodes and a note on which of them
  are already there.
- **Person pages**: a click on an actor shows their photo, biography and their
  best-known titles, each with its state and a direct way to request it.
- **Trailers** straight inside Nexview, where TMDB knows one. Embedded through
  the data-frugal YouTube address; the connection is only made when you play it.
- **Keywords and studios are clickable** and lead to a list of every title
  carrying them — for films as for series.
- A click on a tile or a list row now opens the detail page. For requesting
  quickly there is a shopping cart right on the title.

- **New home page**: a slider of popular suggestions at the top (large
  backdrop, cover, short plot) and smaller tiles with more of them below.
  Titles that have not been released yet carry their release date clearly.
  Anything already in the library or already requested does not appear at all.
  Below that, the most recently downloaded titles as a slider.
- **Acknowledgements on the about page**: data sources (TMDB, Radarr/Sonarr,
  YouTube) and the building blocks used, each with its licence.

- **Ratings from IMDb, Rotten Tomatoes and Metacritic** for films — on the
  tiles, in the list and on the detail page. The values come from Radarr, which
  supplies them anyway; so it needs no further service and no further key. For
  series there are none: Sonarr only supplies a single combined score with no
  breakdown. The badges are clickable and lead to the respective page.

- **Favourites**: every title has a heart — on the tile, in the list and on the
  detail page. The **Liked** menu entry shows every mark and lets you remove
  them again; it only appears once there is something to see.
- **"Curated for you"** on the home page: recommendations built from your own
  favourites, with cover and short plot. Whatever turns up in the
  recommendation lists of several favourites comes first — a single hit is
  chance, a repeated one is a statement. Only what the library is still missing
  is shown. Without favourites, it tells you how to make some.

### Changed

- The **language of the film texts follows the interface language**. Switching
  to English now gets you English titles and descriptions too.
- The administrator's **default language** applies to newly invited accounts
  and to invitation mails. Before, the mail followed the language of the
  inviting administrator.
- `latest` in the registry now points only at released versions. The
  development state sits under `main`.

### Fixed

- **An update from an older version could stop the container from starting.**
  When adding missing columns, Nexview produced invalid SQL as soon as a column
  had an enumeration or a timestamp as its default. Both affected core columns
  of the user table.
- Before every change to the database, Nexview now puts a copy under
  `/data/sicherungen/` (the five most recent are kept).
- The cache did not tell film texts apart by language — one user could end up
  seeing another one's version.
- The container sets up the permissions on the data directory itself at start.
  On a NAS the start used to fail on the folder permissions.
- A root folder sent along with a request was taken over unchecked. Now it has
  to genuinely exist in Radarr or Sonarr.
- The save button on the display name was clickable even without a change.

---

## 0.1.0 – 17.08.2026

First released version: discovery through TMDB, matching against
Radarr/Sonarr, requests with approval and quotas, three roles, status
tracking, notifications, ratings, statistics, accounts by invitation only,
German and English.
