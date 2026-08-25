# Änderungen

Nexview zählt nach `HAUPT.NEBEN.KORREKTUR`:

- **KORREKTUR** (0.2.**1**) – nur Fehlerbehebungen, nichts Neues.
- **NEBEN** (0.**3**.0) – neue Funktionen; Bestehendes läuft weiter wie bisher.
- **HAUPT** (**1**.0.0) – etwas verhält sich anders als vorher und braucht
  einen Handgriff beim Aktualisieren.

Die oberste Nummer ist die, an der gerade gearbeitet wird. Sie ist noch nicht
veröffentlicht, solange kein Tag dazu existiert.

---

## 0.20.0 – unveröffentlicht

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

## 0.19.0 – 25.08.2026

### New

- **„Sag mir Bescheid" — auf einen Titel warten, ohne ihn anzufragen.** Ist ein
  Titel schon von jemand anderem angefragt, ließ er sich bisher nicht noch
  einmal anfragen — und danach hörte der Zweite nie wieder etwas davon. Er
  erfuhr nicht einmal, dass der Titel angekommen war, obwohl genau das seine
  Frage war.

  Bei einem **Film** steht der Knopf dort, wo sonst nur der Zustandssatz steht
  („wird gesucht"), also genau dann, wenn es etwas zu warten gibt. Gemeldet
  wird einmal, danach ist die Vormerkung erledigt.

  Bei einer **Serie** steht er dauerhaft neben dem Anfrage-Knopf und gilt für
  die ganze Serie. Der Grund ist ein technischer: Der Zustand „angefragt,
  nichts mehr zu tun" tritt bei einer Serie praktisch nie ein, solange
  irgendeine Staffel unvollständig ist — ein Knopf an einer Bedingung, die nie
  eintritt, wäre kein Angebot. Gemeldet wird jede neue Folge, weil man meistens
  genau darauf wartet.

  **Immer gebündelt.** Lädt ein Staffelpaket mit acht Folgen durch, ist das
  *eine* Nachricht über acht Folgen, und zusammenhängende Nummern werden
  zusammengezogen: „S2: 1-3, 7" statt vier Meldungen. Acht Nachrichten in
  derselben Minute wären der schnellste Weg, jemanden dazu zu bringen,
  Benachrichtigungen abzuschalten.

  Beim ersten Zusammentreffen mit einer Staffel wird der aktuelle Stand nur
  festgehalten, nicht gemeldet — sonst käme direkt nach dem Vormerken eine
  Nachricht über zwanzig Folgen, die längst dalagen.

- **Was in deinem Abo schon läuft, sagt Nexview beim Anfragen.** Wer seine
  Streaming-Dienste im Profil hinterlegt, bekommt beim Anfragen den Satz zu
  sehen, der die Entscheidung ändern kann: „Läuft in deinem Netflix." Ein
  **Hinweis**, keine Sperre — verhindert wird nichts, und der Anfrage-Knopf
  behält seine Beschriftung.

  Bei Serien steht ein anderer Satz da, und das hat einen Grund: Die Quelle
  sagt „läuft auf Netflix" über die *Serie*, nicht über die vierte Staffel, die
  dort fehlt — und genau in dem Fall fragt jemand an. Der Hinweis sagt das
  ausdrücklich, statt etwas zu behaupten, das er nicht weiß.

  Derselbe Abgleich erscheint dort, wo entschieden wird: in der Freigabeliste
  („Dilara kann das schon über Netflix sehen") und bei den Eltern, wenn sie
  einen Kinderwunsch entscheiden. Gemessen wird an den Abos **des
  Anfragenden**, nicht an denen des Entscheiders — der hat vielleicht kein
  Netflix, aber die Frage ist, ob der Anfragende ohne den Download auskäme. In
  der Freigabeliste erscheint der Hinweis nur bei Anfragen, die wirklich auf
  eine Entscheidung warten: Bei automatischer Freigabe stand die Anfrage nie
  auf dem Tisch des Entscheiders, und der Hinweis wäre ein Vorwurf ohne
  Adressat.

  Die Daten kosten nichts. TMDB reicht sie von JustWatch durch und hängt sie
  ohnehin an jede Detailabfrage. Die Liste der Dienste ist handverlesen: TMDB
  führt 194 Anbieter für Deutschland und 292 für die USA, darin Kaufhäuser,
  Nischenkanäle und Untermieter wie „Paramount+ Amazon Channel" gleichberechtigt
  neben Netflix. Deren eigene Reihung hilft nicht — dort stehen der Apple TV
  Store und Google Play Movies **vor** Disney+.

- **Die Regionsauswahl kennt jetzt 139 Länder statt acht.** Sie stammt von TMDB
  statt aus dem Quelltext: Wer in den Niederlanden, Polen oder Kanada saß,
  konnte sein Land schlicht nicht angeben. Genommen werden die Länder, für die
  es Anbieterdaten gibt — ein Land anzubieten, zu dem es hinterher nichts zu
  sagen gibt, wäre ein Versprechen ohne Deckung.

- **Ein Hinweis für alle, die nie eine Region gewählt haben.** Der
  Einrichtungsassistent fragt nicht danach, und das Feld beginnt leer — die
  Mehrheit erbt also stillschweigend die Vorgabe des Betreibers. Für Kinostarts
  ist das eine Ungenauigkeit; für „läuft in deinem Netflix" ist es eine falsche
  Behauptung über einen Menschen, denn der Katalog von Netflix Schweiz ist nicht
  der deutsche. Ein gelber Streifen sagt es, verschwindet von selbst, sobald die
  Region gesetzt ist, und lässt sich für die laufende Sitzung wegklicken.

- **Emby, als dritter Medienserver neben Plex und Jellyfin.** Verbinden,
  anmelden, Bibliothek abgleichen und der Gesehen-Stand je Person — alles wie
  gehabt, nur mit einem Server mehr. Der Parallelbetrieb aus 0.18 gilt
  unverändert: Wer will, verbindet alle drei, und der Gesehen-Stand wird über
  sie hinweg zusammengeführt.

  Gemessen an einem echten Server (Emby 4.9.5.0): Emby und Jellyfin sprechen
  dieselbe Sprache. Dieselben Endpunkte, dieselben Felder, sogar dieselbe
  Ausweiszeile — alle fünf geprüften Anmeldevarianten antworteten. Der
  Emby-Adapter ist deshalb eine Ableitung des Jellyfin-Adapters und keine
  zweite Kopie: Zwei fast gleiche Dateien nebeneinander hießen, dass jede
  Fehlerbehebung zweimal gemacht werden müsste — und beim zweiten Mal
  vergessen wird.

  **Auch Emby-Konten haben keine E-Mail-Adresse.** Es gilt deshalb dasselbe
  wie bei Jellyfin: Die Anmeldung über Emby legt kein Konto an, sondern wird
  aus dem Profil heraus verknüpft. Ohne Adresse gibt es nichts, woran Nexview
  jemanden wiedererkennt, und eine Einladung endete still in einem zweiten
  Konto ohne Passwort.

  Die PIN, die Emby optional je Konto kennt, ist dabei keine Hürde: Sie steht
  bei den Anzeigeeinstellungen und nicht bei den Rechten — eine Profil-PIN zum
  Umschalten auf einem geteilten Gerät, wie man sie von Netflix kennt. Die
  Anmeldung mit Benutzername und Passwort ist davon unberührt. Nachgemessen an
  einem Konto, das eine gesetzt hat.

- **Der Verlauf einer Anfrage.** „Warum dauert das?" ist die häufigste Frage,
  und Nexview kannte die Antwort immer vollständig — angefragt wann,
  freigegeben von wem, seit wann in Suche, zuletzt nachgesehen wann. Sichtbar
  war davon ein einziges Zustandswort. Ein Knopf in „Meine Anfragen" öffnet
  jetzt eine Zeitleiste: erledigte Schritte grün mit Haken, der laufende gelb,
  kommende grau. Ein abgebrochener Weg endet dort, wo er endet — unter einer
  Ablehnung steht kein grauer „Fertig"-Schritt, der ein Versprechen wäre, das
  niemand mehr einlöst. Zwei Angaben schafften es dabei zum ersten Mal bis zum
  Anfragenden: wer freigegeben hat (oder dass es automatisch ging) und wann
  zuletzt nach dem Titel gesehen wurde.

- **Bewertungen veralten, wenn Radarr nachlädt.** Radarr und Sonarr laden
  weiter, bis das Qualitätsprofil erreicht ist. Eine Bewertung galt aber der
  Datei, die damals dalag — danach steht ein „war schlecht" an etwas, das es so
  nicht mehr gibt. Für den Betreiber ist das die schlechteste Sorte Rückmeldung:
  eine über einen Zustand, den er nicht mehr nachprüfen kann.

  Wächst die Datei spürbar, wird die Bewertung als veraltet gekennzeichnet. Sie
  bleibt **stehen** — löschen verlöre die Information, und leere Sterne ohne
  Erklärung sähen aus wie ein Fehler. In der Statistik zählt sie nicht mehr mit,
  denn die Seite beantwortet „wie zufrieden sind die Leute mit dem, was hier
  liegt". Der Betroffene bekommt eine Nachricht und kann neu bewerten.

  Erkannt wird das im Status-Abgleich, der ohnehin läuft — die Größe steht in
  derselben Antwort, die „ist der Titel noch da?" beantwortet. Ausdrücklich
  **nicht** in der Speichermessung: Die ist abschaltbar, und eine Bewertung
  veraltet auch dann, wenn niemand Kontingente führt. Bei Staffelanfragen
  greift es nicht — die Größe am Serien-Eintrag ist die der ganzen Serie und
  wächst auch, wenn eine andere Staffel aufgewertet wird.

- **Bewerten darf jeder, nicht nur der Besteller.** Die Rückmeldung zur
  Qualität hing an der Anfrage. Daraus folgte alles Weitere: Nur wer bestellt
  hatte, sah die Sterne — und wer denselben Film zwei Wochen später sah und
  merkte, dass die Tonspur fehlt, hatte keine Möglichkeit, es zu sagen.

  Dabei geht es hier nicht um Geschmack — dafür gibt es das Herz —, sondern um
  die **Datei**, und die beurteilt jeder gleich gut, der sie gesehen hat. Die
  Bewertung hängt deshalb jetzt am Titel. Auf jeder Detailseite eines
  vorhandenen Titels stehen die Sterne; in „Meine Anfragen" bleiben sie als
  Abkürzung, weil der Augenblick direkt nach „Bereits geladen" der ist, in dem
  jemand bewertet.

  Bewusst **kein** Gatter über den Gesehen-Stand, obwohl Nexview ihn kennt: Er
  sagt aus, dass jemand den *Titel* gesehen hat, nicht *diese Datei*. Nach
  einer Aufwertung durch Radarr bleibt der Haken stehen, obwohl die alte
  Fassung gemeint war — als Nachweis taugt er also nicht. Stattdessen hängt die
  Gültigkeit an der Datei selbst.

  **Serien werden je Staffel beurteilt.** Die Dateien liegen staffelweise, die
  Qualität unterscheidet sich staffelweise. Eine Serie als Ganzes zu bewerten
  hieße, über zehn verschiedene Dateien ein Urteil zu fällen.

  Bestehende Bewertungen wandern beim ersten Start mit. Administratoren
  bewerten weiterhin nicht — sie beantworten die Rückmeldungen der anderen.

- **Die Rückmeldungs-Ansicht zeigt jetzt jede Bewertung.** Sie zeigte Anfragen,
  die zufällig eine Bewertung hatten — ein Rest der alten Verknüpfung. Seit
  bewerten darf, wer einen Titel vorliegen hat, war das eine Ansicht mit
  Löchern: Genau die Urteile von Leuten, die nie bestellt haben, fehlten. Auf
  einer echten Datenbank waren das zwei von sieben. Für den Betreiber ist es
  ohnehin belanglos, ob hinter einer Rückmeldung eine Anfrage steht.

- **Aus einem schwachen Urteil wird auf Wunsch ein Ticket.** Wer ein oder zwei
  Sterne gibt, beschreibt meistens ein Problem — und ein Problem verschwindet
  in einem Durchschnitt, während ein Ticket einen Zustand hat und offen
  bleibt, bis sich jemand darum gekümmert hat. Das Fenster bietet es an, der
  Text ist ja schon geschrieben. Nicht angeboten wird es, wenn zu diesem Titel
  schon ein offenes Ticket von derselben Person existiert: Der Betreiber
  bekäme sonst zweimal dieselbe Sache auf den Tisch, und der Nutzer glaubte,
  sein erstes sei untergegangen.

### Changed

- **Kontingente: Stückzahl und Speicher gelten jetzt zusammen.** Bis hierher war
  es ein haus-weites Entweder-oder — ein Umschalter entschied, ob die Anzahl
  der Anfragen zählt oder der belegte Platz. Der Umschalter ist weg. Der Reiter
  heißt nur noch **Kontingente** und trägt drei Standardwerte: Filme, Serien,
  Speicher. Eine Anfrage geht durch, wenn **beide** Grenzen noch Luft haben;
  die Meldung sagt, welche gegriffen hat. Wer nur nach einer begrenzen will,
  stellt die andere auf „unbegrenzt" — das ist eine Einstellung weniger als
  eine Betriebsart.

  Jede Grenze hat am Konto jetzt **drei Zustände**: *Standard* (der Wert des
  Hauses), *unbegrenzt* (ausdrücklich ohne Grenze) und eine eigene Zahl, wobei
  die **0** „darf nichts anfragen" bedeutet. Vorher war „unbegrenzt" ein leeres
  Feld — wer einmal eine Zahl eingetragen hatte, fand nicht mehr zurück.

  ⚠️ **Die 0 beim Speicher hat ihre Bedeutung gewechselt.** Sie hieß „für
  dieses Konto unbegrenzt" und heißt jetzt das Gegenteil. Gespeicherte Nullen
  ziehen beim ersten Start automatisch auf „unbegrenzt" um; ohne das wäre ein
  Konto über Nacht still gesperrt gewesen.

  ⚠️ **War der Schalter bisher auf „Anzahl", gehen alle Bestände einmalig ins
  Haus.** Die GB-Grenzen waren in diesem Betrieb wirkungslos, die Zurechnung
  lief aber im Hintergrund weiter. Ohne diesen Schritt wären Leute nach dem
  Update schlagartig gesperrt — wegen einer Zahl, die seit Monaten
  herumlag, und einer Historie, von der sie nichts wussten. Keine Datei wird
  angefasst, eingetragene Grenzen bleiben stehen und greifen ab jetzt.

- **Der Zeitraum der Stückzahl gilt haus-weit.** Er stand bisher an jedem Konto
  einzeln. Drei Konten mit drei verschiedenen Zeiträumen erklären aber
  niemandem mehr, was „3 Filme" bedeutet. Bestehende Einstellungen ziehen um:
  Wich ein Konto von der Woche ab, gewinnt der häufigste Wert.

- **Speicher-Reiter, Hausbestand und „Brauche ich nicht mehr" gibt es jetzt
  überall.** Sie hingen am alten Hauptschalter und waren damit für jeden
  unsichtbar, der nach Stückzahl begrenzte. Gemessen wird immer, begrenzt nur
  auf Wunsch — und die Kette „abgeben → der Betreiber entscheidet → Haus,
  behalten oder löschen" steht damit jedem Haushalt offen. Für Betreiber heißt
  das: Es kann jetzt eine Abgabe auf dem Tisch liegen, die vorher nie kam.

- **Zurücksetzen ist ein Knopf statt einer Nebenwirkung.** Das Umschalten der
  Betriebsart hat die Konten still auf null gesetzt. Jetzt gibt es „Alle
  Bestände ins Haus übernehmen" in den Kontingenten und „Speicher zurücksetzen"
  bei jedem Konto — beide mit Rückfrage und Zahlen. Der zweite ist zugleich der
  Ausweg für den Fall darunter.

- **Titel, die Radarr oder Sonarr nicht mehr führen, sind jetzt erkennbar.** Wer
  einen Titel dort entfernt und die Datei behält, schafft einen Posten, der
  weiter zählt, aber nicht mehr gelöscht werden kann — Nexview löscht
  ausschließlich über diese Dienste. Solche Zeilen tragen jetzt ein Zeichen mit
  Erklärung. Bis hierher merkte es nur der Betreiber, und zwar erst beim
  Löschversuch.

- **„Sprache & Region" und „Sicherheit" sind in „Konto" aufgegangen.** Aus acht
  Reitern wurden sechs, und die Kontoseite nutzt die volle Breite in zwei
  Spalten. Statt fünf Speichern-Knöpfen untereinander gibt es **einen** für die
  ganze Seite; Profilbild, Passwort und Kontolöschung behalten ihre eigenen,
  weil das Handlungen sind und keine Einstellungen. Passwort ändern öffnet ein
  Fenster, statt drei dauerhaft leere Felder in der Spalte stehen zu lassen.
  Die alten Adressen `?reiter=sprache` und `?reiter=sicherheit` funktionieren
  weiter.

### Fixed

- **Zurückgestellte Anfragen waren eine Sackgasse.** „Ja im Prinzip, nur nicht
  jetzt" verspricht, dass später freigegeben wird und niemand neu fragen muss.
  Gebaut war der Teil nach dem Komma nie: Freigeben und Ablehnen verlangten
  ausdrücklich „wartet auf Freigabe", und eine zurückgestellte tut das nicht
  mehr. Der Entscheider bekam „wartet nicht mehr auf eine Freigabe", der
  Anfragende beim zweiten Versuch „steht bereits zurück, sobald du wieder Platz
  hast, kann die Anfrage freigegeben werden" — beide Meldungen verwiesen auf
  einen Weg, den es nicht gab. Jetzt nehmen beide Entscheidungen
  zurückgestellte Anfragen an, und die Knöpfe erscheinen auch bei ihnen.
  Bewusst nur einzeln: Die Sammelfreigabe bleibt bei den wartenden, sonst
  sammelte „alle freigeben" eine Einzelentscheidung wieder ein.

- **Der Gesehen-Abgleich wäre für Plex-Nutzer abgestürzt.** Beim Durchreichen
  der Kontonummer wurden Jellyfin und Emby angepasst, Plex nicht — der Aufrufer
  übergab zwei Angaben, der Adapter nahm eine. Aufgefallen im Test, nicht im
  Betrieb.

- **Bibliothekseinträge vom Medienserver trugen kein Erscheinungsjahr.** Der
  Feldkatalog forderte es nicht an, also kam es nicht mit, und jeder Eintrag
  stand mit `year=None` da. Das klingt nach Beiwerk und ist es nicht: Der
  Titel-Rückfall beim Bibliotheksabgleich vergleicht Titel **und Jahr**, damit
  „The Lion King" von 1994 nicht dasselbe ist wie das Remake von 2019. Ohne
  Jahr greift er gar nicht mehr — Titel ohne TMDB- oder TVDB-Kennung galten
  damit als nicht vorhanden. Aufgefallen beim Messen gegen Emby; betrifft
  Jellyfin genauso.

- **Der Reiter für die Region hieß nach einer Seite, die es nicht mehr gibt.**
  „Voreinstellung beim Entdecken" mit dem Zusatz „ändern kannst du sie beim
  Entdecken jederzeit" — nur ist Entdecken seit 0.17 aus dem Menü, und der
  Regionsfilter dort ebenfalls entfernt. Damit war der Reiter die einzige
  Stelle, an der sich die Region überhaupt setzen ließ, und er verwies auf einen
  Weg, den es nicht mehr gab.

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

### Neu

- **„Andere zeigen" bei den Vorschlägen.** Ist unter *Das könnte dir auch
  gefallen* nichts dabei, holt ein Knopf die nächsten zwölf Titel. Der Vorrat
  kommt aus zwei TMDB-Listen: den Empfehlungen (was Leuten gefiel, denen
  dieser Titel gefiel) und den ähnlichen Titeln (gleiche Genres und
  Schlagworte). Gemessen an echten Daten ergibt das vier bis sieben Runden
  je Titel — auch bei völlig unbekannten Filmen, wo TMDB nur eine Handvoll
  Empfehlungen kennt. Ist der Vorrat durch, geht es wieder von vorn los.

### Behoben

- **Die Ansicht auf dem Telefon durchgesehen** (360/390/430 px, alle 20
  Ansichten). Gekürzt wurde bisher an Stellen, an denen umbrechen richtig
  gewesen wäre:
  - In *Meine Anfragen* und *Alle Anfragen* schrumpfte der Titel auf ein
    Zeichen („S."), weil Zustand und Knopf dieselbe Zeile beanspruchten.
    Auf schmalen Bildschirmen steht der Titel jetzt allein in der ersten
    Zeile, Etikett und Knopf darunter.
  - Fünfzehn Raster hatten keine ausdrückliche Grundspalte. Die
    stillschweigende Spur ist so breit wie ihr breitester Inhalt, nicht wie
    der Bildschirm — die Startseite ließ sich dadurch seitlich schieben.
  - Kacheltitel auf der Startseite, in der Listenansicht und unter *Mag ich*
    laufen jetzt über zwei Zeilen, statt nach der Hälfte abzubrechen.
  - Das Zustands-Etikett auf dem Poster wird nicht mehr zusammengedrückt.
    Passt es nicht neben die Bewertung, rutscht die Bewertung eine Zeile
    tiefer — vorher brach „Bereits geladen" mitten über das Bild um.
  - Fehlt ein Poster, stand der Titel als Ersatz im Kasten — aber so breit
    wie sein längstes Wort, also links und rechts abgeschnitten. Jetzt bricht
    er um.
- Zwei fehlende Übersetzungen: der Zustand *Freigegeben* (`status.approved`)
  zwischen Freigabe und Übergabe an Radarr/Sonarr, und der Hinweis unter der
  öffentlichen Adresse im Einrichtungsassistenten.
- Nach dem Abschicken einer Anfrage stand auf der Titelseite weiterhin
  *Zu Radarr hinzufügen*, obwohl die Anfrage längst lief. Dasselbe galt für
  Filmografien, Kategorielisten und die Startseite: neu geladen wurden bisher
  nur die Kachellisten. Welche Ansichten den Zustand einer Anfrage zeigen,
  steht jetzt an einer einzigen Stelle (`lib/refresh.ts`) — und dort wird
  nach jeder Änderung alles davon aufgefrischt.

---

## 0.4.2 – 18.08.2026

### Behoben

- Am geschlossenen Ticket stand beim Administrator noch der Satz, die
  Antwort des Benutzers öffne es wieder — das galt seit der Regeländerung
  nicht mehr.
- Der Administrator konnte ein Ticket an sich selbst schreiben. Der
  Empfänger ist jetzt Pflicht.
- *Problem melden* erschien auch beim Administrator, obwohl er derjenige
  ist, bei dem man sich meldet. Für ihn ausgeblendet.

---

## 0.4.1 – 18.08.2026

### Neu

- **Der Administrator kann einen Benutzer anschreiben.** Beim Eröffnen eines
  Tickets wählt er den Empfänger aus einer Liste; das Ticket gehört dann dem
  Angeschriebenen, er findet es unter seinen eigenen und kann antworten. Wer
  es verfasst hat, steht in der Kopfzeile. Bisher ging die Post nur in eine
  Richtung.

---

## 0.4.0 – 18.08.2026

### Neu

- **Ticketcenter.** Benutzer eröffnen Tickets mit Betreff und Text; jeder sieht
  nur die eigenen. Der Administrator sieht alle, wird über die Glocke
  informiert, antwortet, kann seine Antworten nachbessern und den Zustand auf
  *Offen*, *In Bearbeitung* oder *Geschlossen* setzen.

  Ein **Entscheider ist hier ausdrücklich kein Administrator**: er entscheidet
  über Anfragen, sieht aber keine fremden Tickets. Wer ein fremdes Ticket
  aufruft, bekommt „gibt es nicht" statt „verboten" – ein „verboten" wäre
  bereits die Auskunft, dass es diese Nummer gibt.

  **Geschlossen heißt für den Benutzer zu.** Er sieht den Verlauf weiterhin,
  das Antwortfeld verschwindet aber; wer noch etwas hat, eröffnet ein neues
  Ticket. Der Administrator darf auch danach noch einen Nachtrag hinterlassen,
  ohne das Ticket dafür wieder aufmachen zu müssen.

  **Aufräumen:** Der Administrator kann geschlossene Tickets löschen, einzeln
  oder als Stapel. Offene lassen sich nicht löschen – wer eines loswerden will,
  schließt es zuerst. So ist die Entscheidung eine bewusste und in zwei
  Schritten getroffen.

  Bearbeiten darf jeder seine **eigenen** Nachrichten; dass etwas geändert
  wurde, steht danach sichtbar dabei. Gelöscht wird nichts – ein Verlauf mit
  Lücken ist nicht mehr lesbar.

  Auf jeder Titelseite gibt es *Problem melden*: das Ticket trägt den Bezug
  dann von selbst, niemand muss den Namen abtippen.

  Die Bewertung mit Kommentar an fertigen Downloads bleibt davon unberührt –
  sie klebt am Titel, das Ticket ist für alles andere.


- **Sperrliste.** Der Administrator kann Titel sperren: sie lassen sich dann
  von niemandem mehr anfragen und gehen nicht an Radarr bzw. Sonarr. Anders als
  bei der Altersbeschränkung bleiben sie **sichtbar** – auffindbar über Suche
  und Entdecken, mit dem Abzeichen *Gesperrt* und ohne Einkaufswagen. Wer
  danach sucht, soll die Antwort bekommen, statt dreimal vergeblich anzufragen.

  Beim Ablehnen einer Anfrage fragt Nexview, ob der Titel gleich mit auf die
  Liste soll – **nur beim Administrator**. Ein Entscheider entscheidet über die
  einzelne Anfrage; ob ein Titel grundsätzlich nicht in die Bibliothek gehört,
  ist Sache des Betreibers. Der Server weist es zusätzlich ab, nicht nur die
  Oberfläche.

  Die Übersicht steht unter *Einstellungen → Sperrliste*, samt Begründung und
  einem Knopf zum Freigeben. Gesperrt ist auch für den Administrator selbst
  gesperrt: wer den Titel doch will, gibt ihn frei – ein bewusster Schritt, der
  hinterher nachvollziehbar ist.

- **Altersbeschränkung je Benutzer.** Der Administrator legt fest, ob ein Konto
  beschränkt ist und wie alt die Person ist; gezeigt wird dann nur, was
  höchstens ab diesem Alter freigegeben ist. Gesperrte Titel verschwinden
  vollständig – aus dem Entdecken, der Suche, den Empfehlungen, den
  Filmografien, der Startseite und den eigenen Favoriten. Auch das Anfragen
  wird serverseitig abgewiesen, nicht nur der Knopf ausgeblendet.

  Maßgeblich ist die Einstufung eines Landes, das **nur der Administrator**
  setzt – getrennt von der Region, die jeder für sich selbst wählen darf.
  Sonst könnte der Beschränkte einfach ein Land einstellen, in dem der Titel
  nicht eingestuft ist, und wäre an der Sperre vorbei. Fehlt für das gewählte
  Land eine Einstufung, gilt die strengste aller Länder.

  Titel **ganz ohne** Einstufung bleiben standardmäßig verborgen – „kein
  Nachweis, kein Zutritt". Das lässt sich je Benutzer abschalten, denn neue
  Titel sind meist noch nirgends eingestuft: gemessen schrumpfte die
  Entdecken-Seite dadurch von 20 auf 2 Einträge, mit erlaubten Unbewerteten
  waren es 10. Bei einem 16-Jährigen mag das vertretbar sein, bei einem
  6-Jährigen nicht – deshalb die Wahl statt einer festen Regel.

  Freigaben aus über 30 Ländern werden dafür in ein Mindestalter übersetzt –
  „FSK 12", „PG-13", „MA15+", „K-16" und „M/12" meinen dasselbe. Die Zuordnung
  deckt 97 % der in der Praxis vorkommenden Bezeichnungen ab; wo sie unsicher
  wäre, rät sie bewusst nicht.

  Die Sperre wirkt nur in Nexview. Über Plex, Jellyfin oder direkt auf der
  Dateifreigabe bleibt alles erreichbar.
- **Sprache im Profil wählbar**, zusammen mit der Region im Reiter *Sprache &
  Region* (hieß vorher *Entdecken*). Der Schalter oben in der Kopfzeile bleibt
  fürs schnelle Umschalten; im Profil gilt die Wahl erst beim Speichern, wie
  bei jeder anderen Einstellung.

### Behoben

- **Ein Sprachwechsel änderte die Texte nicht.** Titel und Handlungen blieben
  in der zuerst geladenen Sprache stehen, bis man die Seite neu lud – die
  Abfragen im Browser merkten sich ihr Ergebnis ohne die Sprache. Sie werden
  jetzt zentral neu geholt.
- **Die Genres blieben in der alten Sprache**, auch wenn Titel und Handlung
  schon umgeschaltet hatten: der Zwischenspeicher für die Detaildaten, aus
  denen die Genrenamen stammen, hatte die Sprache nicht im Schlüssel.

---

## 0.2.0 – 17.08.2026

### Neu

- **Über-Seite** in der Fußzeile: installierte Version, Quelltext, Lizenz.
  Administratoren sehen dort auch, wenn eine neuere Version vorliegt – dafür
  fragt Nexview höchstens einmal am Tag bei GitHub nach. Abschaltbar.
- **E-Mail-Benachrichtigungen**, vier Ereignisse einzeln schaltbar: Download
  fertig, Anfrage entschieden, Anfrage wartet auf Freigabe, neue Bewertung
  bzw. Antwort darauf. Standard ist alles aus; einschalten kann sie jeder für
  sich selbst im Profil. Die Glocke in der App bleibt davon unberührt.
- **Profil in Reitern**: Konto, Benachrichtigungen, Entdecken, Sicherheit.
- **Region als persönliche Voreinstellung** für die Filterleiste.
- **Staffelweise Serien-Anfragen.** Statt der ganzen Serie lässt sich eine
  einzelne Staffel anfragen. Läuft die Serie schon mit, wird sie nicht neu
  angelegt — es kommt nur die gewünschte Staffel dazu, und genau die wird
  gesucht. Zählt als eine Anfrage aufs Kontingent.
- **Zielordner:** Der Administrator legt fest, ob Benutzer ihn beim Anfragen
  wählen dürfen. Wenn nicht, gilt für alle ein von ihm gesetzter Ordner. Das
  wird auf dem Server durchgesetzt, nicht nur in der Oberfläche ausgeblendet.
- **Detailseite je Titel** statt nur eines kleinen Fensters: Besetzung mit
  Fotos, Regie, Drehbuch, Studios, Schlagworte, Budget, Empfehlungen — und bei
  Serien die Staffeln zum Aufklappen mit allen Folgen und der Angabe, welche
  davon schon vorliegen.
- **Personenseiten**: Ein Klick auf einen Schauspieler zeigt Foto, Biografie
  und dessen bekannteste Titel, jeweils mit Status und direkter
  Anfragemöglichkeit.
- **Trailer** direkt in Nexview, sofern TMDB einen kennt. Eingebunden über die
  datensparsame YouTube-Adresse; die Verbindung entsteht erst beim Abspielen.
- **Schlagworte und Studios sind anklickbar** und führen zu einer Liste aller
  Titel damit — für Filme wie für Serien.
- Ein Klick auf eine Kachel oder Listenzeile öffnet jetzt die Detailseite. Zum
  schnellen Anfragen gibt es einen Einkaufswagen direkt am Titel.

- **Startseite neu**: oben ein Slider mit beliebten Vorschlägen (großes
  Hintergrundbild, Cover, kurze Handlung) und darunter kleine Kacheln mit
  weiteren. Titel, die noch nicht erschienen sind, tragen ihr Startdatum gut
  sichtbar. Was schon in der Bibliothek liegt oder angefragt ist, taucht gar
  nicht erst auf. Darunter die zuletzt geladenen Titel als Slider.
- **Danksagung auf der Über-Seite**: Datenquellen (TMDB, Radarr/Sonarr,
  YouTube) und die verwendeten Bausteine samt Lizenz.

- **Bewertungen von IMDb, Rotten Tomatoes und Metacritic** bei Filmen — auf
  den Kacheln, in der Liste und auf der Detailseite. Die Werte kommen aus
  Radarr, das sie ohnehin mitliefert; es braucht also keinen weiteren Dienst
  und keinen weiteren Schlüssel. Bei Serien gibt es sie nicht: Sonarr liefert
  nur eine Sammelwertung ohne Aufschlüsselung. Die Abzeichen sind anklickbar und führen zur jeweiligen Seite.

- **Favoriten**: An jedem Titel sitzt ein Herz — auf der Kachel, in der Liste
  und auf der Detailseite. Der Menüpunkt **Mag ich** zeigt alle Markierungen
  und lässt sie wieder entfernen; er erscheint erst, wenn es etwas zu sehen
  gibt.
- **„Für dich kuratiert"** auf der Startseite: Empfehlungen aus den eigenen
  Favoriten, mit Cover und kurzer Handlung. Was in den Empfehlungslisten
  mehrerer Favoriten auftaucht, steht vorn — ein einzelner Treffer ist Zufall,
  ein mehrfacher eine Aussage. Gezeigt wird nur, was der Bibliothek noch fehlt.
  Ohne Favoriten steht dort, wie man welche anlegt.

### Geändert

- Die **Sprache der Filmtexte folgt der Oberflächensprache**. Wer auf Englisch
  umstellt, bekommt jetzt auch englische Titel und Beschreibungen.
- Die **Standardsprache** des Administrators gilt für neu eingeladene Konten
  und für Einladungsmails. Vorher richtete sich die Mail nach der Sprache des
  einladenden Administrators.
- `latest` in der Registry zeigt nur noch auf veröffentlichte Versionen. Der
  Entwicklungsstand liegt unter `main`.

### Behoben

- **Ein Update von einer älteren Version konnte den Container am Starten
  hindern.** Beim Nachrüsten fehlender Spalten erzeugte Nexview ungültiges SQL,
  sobald die Spalte eine Auswahlliste oder einen Zeitstempel als Standardwert
  hatte. Beides betraf Kernspalten der Benutzertabelle.
- Vor jeder Änderung an der Datenbank legt Nexview jetzt eine Kopie unter
  `/data/sicherungen/` ab (die fünf jüngsten bleiben liegen).
- Der Zwischenspeicher unterschied Filmtexte nicht nach Sprache – ein Benutzer
  konnte die Fassung eines anderen zu sehen bekommen.
- Der Container richtet die Rechte am Datenverzeichnis beim Start selbst ein.
  Auf einem NAS scheiterte der Start vorher an den Ordnerrechten.
- Ein beim Anfragen mitgeschickter Zielordner wurde ungeprüft übernommen.
  Jetzt muss es ihn in Radarr bzw. Sonarr wirklich geben.
- Der Speichern-Knopf beim Anzeigenamen war auch ohne Änderung anklickbar.

---

## 0.1.0 – 17.08.2026

Erste veröffentlichte Fassung: Entdecken über TMDB, Abgleich mit
Radarr/Sonarr, Anfragen mit Freigabe und Kontingenten, drei Rollen,
Status-Verfolgung, Benachrichtigungen, Bewertungen, Statistiken, Konten
ausschließlich über Einladung, Deutsch und Englisch.
