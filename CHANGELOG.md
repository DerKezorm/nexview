# Änderungen

Nexview zählt nach `HAUPT.NEBEN.KORREKTUR`:

- **KORREKTUR** (0.2.**1**) – nur Fehlerbehebungen, nichts Neues.
- **NEBEN** (0.**3**.0) – neue Funktionen; Bestehendes läuft weiter wie bisher.
- **HAUPT** (**1**.0.0) – etwas verhält sich anders als vorher und braucht
  einen Handgriff beim Aktualisieren.

Die oberste Nummer ist die, an der gerade gearbeitet wird. Sie ist noch nicht
veröffentlicht, solange kein Tag dazu existiert.

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
