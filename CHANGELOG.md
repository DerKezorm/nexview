# Änderungen

Nexview zählt nach `HAUPT.NEBEN.KORREKTUR`:

- **KORREKTUR** (0.2.**1**) – nur Fehlerbehebungen, nichts Neues.
- **NEBEN** (0.**3**.0) – neue Funktionen; Bestehendes läuft weiter wie bisher.
- **HAUPT** (**1**.0.0) – etwas verhält sich anders als vorher und braucht
  einen Handgriff beim Aktualisieren.

Die oberste Nummer ist die, an der gerade gearbeitet wird. Sie ist noch nicht
veröffentlicht, solange kein Tag dazu existiert.

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
