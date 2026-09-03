/**
 * Was der Umzugsassistent vom Server bekommt, und was der Betreiber entscheidet.
 *
 * ⚠️ **Eigene Datei, damit die Entscheidungsregeln prüfbar sind.** Die Regeln
 * darunter (`vorgabeFuer`, `zusammenfassen`) sind die einzige Stelle, an der
 * steht, was voreingestellt ist - und genau das ist die sicherheitskritische
 * Aussage dieses Features. In der Komponente wären sie nur durch Klicken zu
 * prüfen; hier prüft sie ein Test.
 *
 * Der Anlass ist ein Befund über das Vorbild: Die drei Regeln im Kopf von
 * `MedienserverImport.tsx` („nichts vorausgewählt", „verknüpfen ist nie die
 * Vorgabe", „jedes Konto sagt, woran es hängt") stehen dort als Kommentar und
 * werden von keinem Test gehalten. Wer sie beim Umbau verliert, merkt es nicht.
 */

export type Kontozeile = {
  seerr_id: number
  anzeigename: string
  email: string | null
  herkunft: string
  anbieter_kennung: string | null
  treffer_user_id: number | null
  treffer_grund: string | null
  rolle_seerr: string
  rolle_neu: string
  rolle_verlust: string | null
  kontingent_filme: number | null
  kontingent_serien: number | null
  kontingent_hinweise: string[]
  anfragen: number
  bild: string | null
}

export type Anfragezeile = {
  seerr_id: number
  titel_tmdb: number | null
  titel_tvdb: number | null
  art: string
  staffel: number | null
  ziel_status: string
  besteller_seerr_id: number
  uhd: boolean
  uebersprungen: string | null
}

export type Instanz = {
  art: string
  name: string
  uhd: boolean
  ordner: string | null
  profil: string | null
}

export type Vorschau = {
  fassung: string
  fassung_geprueft: boolean
  fassung_hinweis: string | null
  medienserver: string | null
  konten: Kontozeile[]
  anfragen: Anfragezeile[]
  sperrliste: number
  meldungen: number
  kommt_nicht_mit: Record<string, string>
  instanzen: Instanz[]
  bereiche?: {
    kennung: string
    anbieter: string
    zeilen: { was: string; wert: string }[]
    luecken: string[]
    leer: boolean
    posten: {
      kennung: string
      beschriftung: string
      zeilen: { was: string; wert: string }[]
    }[]
    eintraege: number
    /**
     * Nur beim Medienserver: was das spätere Verbinden vorausfüllen kann.
     *
     * ⚠️ **Nicht aus `zeilen` ablesen.** Dort steht die Server-Kennung
     * abgekürzt und die Adresse in Anzeigeform; ein Formular daraus zu füllen
     * hieße, es irgendwann mit `beispiel…` zu füllen.
     */
    verbindung?: { art: string; name: string; adresse: string; kennung: string }
  }[]
  nie_dabei?: string[]
  konten_neu: number
  konten_verknuepft: number
  anfragen_uebernehmbar: number
  frische_installation: boolean
}

/** Was mit einer Zeile geschehen soll. */
export type Wahl =
  | { was: 'ueberspringen' }
  | { was: 'neu' }
  | { was: 'zuordnen'; zielUserId: number }

/**
 * Die Vorgabe für eine Zeile.
 *
 * ⚠️ **Ohne sicheren Treffer heißt die Vorgabe „überspringen", nicht „neues
 * Konto".** Ein Assistent, der dreißig Konten anlegt, muss dreißig bewusste
 * Häkchen kosten - Nexview kann zwei Konten nicht zusammenführen, und der Weg
 * zurück ist Löschen samt allem, was daran hängt. Genau dieser Fall ist beim
 * ersten Lauf gegen eine echte Installation aufgetreten: zwei Seerr-Konten,
 * ein Mensch, und nur eines davon mit Anker.
 *
 * ⚠️ **Mit sicherem Treffer ist „zuordnen" die Vorgabe, und das ist kein
 * Widerspruch dazu.** Ein Treffer entsteht hier nur über dieselbe
 * Medienserver-Kennung aus derselben Quelle - das ist keine Vermutung über
 * Namen, sondern dieselbe Nummer. Es sind die Zeilen, bei denen es nichts zu
 * entscheiden gibt; im Vorbild stehen sie deshalb unter „Schon da, nichts zu
 * tun". Ändern kann man sie trotzdem.
 */
export function vorgabeFuer(zeile: Kontozeile): Wahl {
  if (zeile.treffer_user_id !== null) {
    return { was: 'zuordnen', zielUserId: zeile.treffer_user_id }
  }
  return { was: 'ueberspringen' }
}

/**
 * Bewirkt diese Zeile überhaupt etwas?
 *
 * ⚠️ **Ein sicherer Treffer ohne Anfragen ist eine Attrappe.** Das Konto gibt
 * es hier schon, die Medienserver-Verknüpfung auch (daran wurde der Treffer ja
 * erkannt), Rolle und Kontingente bleiben unangetastet. Zu übertragen wäre
 * einzig die Anfragehistorie - und wenn die leer ist, geschieht nichts.
 *
 * Solche Zeilen gehören nicht in die Entscheidungsliste. Wer drei Zeilen sieht,
 * von denen zwei folgenlos sind, prüft alle drei gleich sorgfältig und wird
 * dabei bei der einen unaufmerksam, auf die es ankommt. Dieselbe Überlegung
 * wie bei der Gruppe „Schon da, nichts zu tun" im Medienserver-Import.
 */
export function istFolgenlos(zeile: Kontozeile): boolean {
  return zeile.treffer_user_id !== null && zeile.anfragen === 0
}

export type Zusammenfassung = {
  neu: number
  zugeordnet: number
  uebersprungen: number
  /** Anfragen, die kämen: nur die von Konten, die auch kommen. */
  anfragen: number
  /** Anfragen, die wegfallen, weil ihr Konto übersprungen wird. */
  anfragenOhneKonto: number
  /** Nexview-Konten, auf die mehr als eine Seerr-Zeile zeigt. */
  mehrfachZiele: number[]
}

/**
 * Was am Ende dastünde, wenn man es täte.
 *
 * ⚠️ **`mehrfachZiele` ist Absicht und kein Fehler.** Zwei Seerr-Konten dürfen
 * auf dasselbe Nexview-Konto zeigen - beim Seerr-Umzug ist das oft die
 * richtige Antwort, weil derselbe Mensch drüben ein Plex-Konto und daneben ein
 * lokales hat. Das unterscheidet diesen Assistenten von der
 * Medienserver-Übernahme, wo eine zweite Verknüpfung die erste überschriebe
 * und deshalb abgelehnt wird. Hier wird nichts verknüpft, hier werden Anfragen
 * zugerechnet, und das darf mehrfach geschehen.
 *
 * Gezählt wird es trotzdem, damit der Betreiber es im Abschluss sieht statt es
 * zu vermuten.
 */
export function zusammenfassen(
  konten: Kontozeile[],
  anfragen: Anfragezeile[],
  wahlen: Record<number, Wahl>,
): Zusammenfassung {
  let neu = 0
  let zugeordnet = 0
  let uebersprungen = 0
  const jeZiel = new Map<number, number>()
  const kommenMit = new Set<number>()

  for (const zeile of konten) {
    const wahl = wahlen[zeile.seerr_id] ?? vorgabeFuer(zeile)
    if (wahl.was === 'neu') {
      neu += 1
      kommenMit.add(zeile.seerr_id)
    } else if (wahl.was === 'zuordnen') {
      zugeordnet += 1
      kommenMit.add(zeile.seerr_id)
      jeZiel.set(wahl.zielUserId, (jeZiel.get(wahl.zielUserId) ?? 0) + 1)
    } else {
      uebersprungen += 1
    }
  }

  let mitAnfragen = 0
  let ohneKonto = 0
  for (const a of anfragen) {
    if (a.uebersprungen) continue
    if (kommenMit.has(a.besteller_seerr_id)) mitAnfragen += 1
    else ohneKonto += 1
  }

  return {
    neu,
    zugeordnet,
    uebersprungen,
    anfragen: mitAnfragen,
    anfragenOhneKonto: ohneKonto,
    mehrfachZiele: [...jeZiel.entries()].filter(([, n]) => n > 1).map(([id]) => id),
  }
}

/**
 * Aus einem Seerr-Anzeigenamen einen Benutzernamen machen, den Nexview annimmt.
 *
 * ⚠️ **Sonst scheitert genau der Schritt, der nicht scheitern darf.** Nexview
 * lässt für Benutzernamen 3 bis 32 Zeichen zu, davon nur Buchstaben, Ziffern,
 * Punkt, Bindestrich und Unterstrich (`USERNAME_PATTERN` im Backend). Seerr
 * kennt diese Grenze nicht: Dort stehen Leerzeichen, Umlaute und Emojis. Ein
 * unverändert übernommener Name wird vom Server abgelehnt - und zwar erst beim
 * Anlegen des Besitzerkontos, also am Ende des Assistenten, nachdem alles
 * andere schon geschrieben ist.
 *
 * ⚠️ **Das Ergebnis ist ein Vorschlag, kein Zwang.** Es steht in einem Feld,
 * das der Betreiber überschreiben kann. Aus „Dilara Uygun" wird
 * `Dilara.Uygun`, aus „🎬" wird nichts Brauchbares - dann bleibt das Feld leer
 * und fragt.
 */
export function benutzernameAus(anzeigename: string): string {
  const ohneZeichen = anzeigename
    .normalize('NFKD')
    // Zerlegte Akzente wegwerfen: aus „é" wird „e", nicht „e" plus Strich.
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/ß/g, 'ss')
    .replace(/\s+/g, '.')
    .replace(/[^A-Za-z0-9._-]/g, '')
    // Mehrfache Trenner und Trenner an den Rändern sehen nach Fehler aus.
    .replace(/\.{2,}/g, '.')
    .replace(/^[._-]+|[._-]+$/g, '')
  return ohneZeichen.length >= 3 ? ohneZeichen.slice(0, 32) : ''
}
