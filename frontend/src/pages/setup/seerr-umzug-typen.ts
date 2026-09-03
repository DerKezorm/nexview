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

import type { TokenPair } from '../../api/client'

/**
 * Ein Hinweis des Umzugs, wie das Backend ihn liefert: Kennung und Zahlen.
 *
 * ⚠️ **Den Satz baut die Oberfläche, nicht das Backend.** Bis 0.29.0 kamen
 * fertige deutsche Sätze, und die englische Oberfläche zeigte sie
 * unverändert. `text` ist der deutsche Rückfall für alles, was die
 * Schnittstelle ohne diese Oberfläche liest; angezeigt wird er nur, wenn
 * `setup.seerr.saetze` die Kennung nicht kennt (ein Test im Backend hält
 * beide Sprachdateien vollständig).
 */
export type Satz = { kennung: string; zahlen: Record<string, string | number>; text: string }

/** Der Satz in der eingestellten Sprache. `t` ist die Funktion aus useTranslation. */
export function satzText(
  t: (key: string, options?: Record<string, unknown>) => string,
  satz: Satz,
): string {
  return t(`setup.seerr.saetze.${satz.kennung}`, { ...satz.zahlen, defaultValue: satz.text })
}

/** Ein Zeilenwert: Rohwert (Adresse, Name, Zahl) oder ein Satz ("kommt mit"). */
export function wertText(
  t: (key: string, options?: Record<string, unknown>) => string,
  wert: string | Satz,
): string {
  return typeof wert === 'string' ? wert : satzText(t, wert)
}

export type Kontozeile = {
  seerr_id: number
  anzeigename: string
  email: string | null
  herkunft: string
  anbieter_kennung: string | null
  treffer_user_id: number | null
  treffer_grund: Satz | null
  rolle_seerr: string
  rolle_neu: string
  rolle_verlust: Satz | null
  kontingent_filme: number | null
  kontingent_serien: number | null
  kontingent_hinweise: Satz[]
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
  uebersprungen: Satz | null
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
  fassung_hinweis: Satz | null
  medienserver: string | null
  konten: Kontozeile[]
  anfragen: Anfragezeile[]
  sperrliste: number
  meldungen: number
  kommt_nicht_mit: Record<string, Satz>
  instanzen: Instanz[]
  bereiche?: {
    kennung: string
    anbieter: string
    zeilen: { was: Satz; wert: string | Satz }[]
    luecken: Satz[]
    leer: boolean
    posten: {
      kennung: string
      beschriftung: Satz
      zeilen: { was: Satz; wert: string | Satz }[]
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
  nie_dabei?: Satz[]
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
 * das der Betreiber überschreiben kann. Aus „Kim Beispiel" wird
 * `Kim.Beispiel`, aus „🎬" wird nichts Brauchbares - dann bleibt das Feld leer
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

/** Die Rollen, die ein Umzug in eine frische Installation vergeben darf. */
export type Rolle = 'user' | 'approver' | 'admin'

export type Berichtskonto = {
  seerr_id: number
  anzeigename: string
  username: string
  email: string | null
  rolle: Rolle
  /** Wie die Person hereinkommt: `plex`, `jellyfin`, `emby` oder `kennwort`. */
  zugang: string
  /** `uebernommen`, `nicht_geladen` (Seerr hatte eines) oder `keins`. */
  bild: string
}

/** Was der Abschluss zurückmeldet - neben der Sitzung des Besitzers. */
export type Bericht = {
  besitzer: { username: string; email: string | null; zugang: string; bild: string }
  konten: Berichtskonto[]
  abgelehnt: { seerr_id: number; anzeigename: string; grund: Satz }[]
  bereiche: string[]
  felder: number
  gesperrt: number
  kanaele: number
  bilder: number
  tmdb: boolean
  public_url: boolean
  nie_dabei: Satz[]
}

export type Abschluss = TokenPair & { bericht: Bericht }

/**
 * Welche Konten der Abschluss anlegen soll, und als was.
 *
 * ⚠️ **Die sicherheitskritische Aussage des Benutzer-Schritts, in einer
 * Funktion.** Drei Regeln, und ein Test hält jede:
 *
 * 1. Nur, was ausdrücklich angehakt ist (`{ was: 'neu' }`). Die Vorgabe
 *    einer Zeile ist „überspringen" (`vorgabeFuer`); wer nichts anklickt,
 *    bekommt kein Konto.
 * 2. Der Besitzer ist nie dabei. Er entsteht aus seiner eigenen Zeile mit
 *    Kennwort; noch einmal als gewöhnliches Konto wäre derselbe Mensch
 *    zweimal - und der Server weist genau das ab.
 * 3. Ohne gewählte Rolle ist die Rolle Nutzer. Was drüben galt, steht in
 *    der Zeile als Hinweis; ins Feld kommt es nur durch einen Klick.
 */
export function abschlussKonten(
  konten: Kontozeile[],
  wahlen: Record<number, Wahl>,
  rollen: Record<number, Rolle>,
  besitzer: number | null,
): { seerr_id: number; rolle: Rolle }[] {
  const ergebnis: { seerr_id: number; rolle: Rolle }[] = []
  for (const zeile of konten) {
    if (zeile.seerr_id === besitzer) continue
    const wahl = wahlen[zeile.seerr_id] ?? vorgabeFuer(zeile)
    if (wahl.was !== 'neu') continue
    ergebnis.push({ seerr_id: zeile.seerr_id, rolle: rollen[zeile.seerr_id] ?? 'user' })
  }
  return ergebnis
}

