/** Kleine Helfer zur Darstellung von Daten und Laufzeiten. */

/**
 * Einen Zeitstempel des Servers lesen – als das, was er ist: UTC.
 *
 * Der Server speichert alle Zeiten in UTC, schickt sie aber ohne
 * Zonen-Kennung ("2026-08-20T12:01:57"). JavaScript hat dafür eine fiese
 * Regel: Ein Datum **mit** Uhrzeit, aber **ohne** Zonenangabe gilt als
 * Ortszeit – die UTC-Zahl stand damit unverwandelt auf dem Bildschirm, in
 * Deutschland zwei Stunden falsch ("zuletzt abgeglichen 12:01" um 14:01).
 * Ein reines Datum ("2026-08-20") liest JavaScript dagegen ohnehin als UTC.
 *
 * Deshalb hängen wir das fehlende "Z" an, wenn eine Uhrzeit dabei ist und
 * keine Zone dransteht – dann rechnet der Browser selbst korrekt in die
 * Ortszeit des Betrachters um.
 */
function parseServerzeit(value: string): Date {
  const hatUhrzeit = value.includes('T')
  const hatZone = /(?:Z|[+-]\d\d:?\d\d)$/.test(value)
  return new Date(hatUhrzeit && !hatZone ? value + 'Z' : value)
}

export function formatDate(value: string | null, locale: string): string {
  if (!value) return '—'
  const parsed = parseServerzeit(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString(locale, { day: '2-digit', month: 'short', year: 'numeric' })
}

/** Wie formatDate, aber mit Uhrzeit - für Angaben wie "zuletzt geprüft". */
export function formatDateTime(value: string | null, locale: string): string {
  if (!value) return '—'
  const parsed = parseServerzeit(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString(locale, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatYear(value: string | null): string {
  return value ? value.slice(0, 4) : '—'
}

/** 128 -> "2 Std. 8 Min." bzw. "2h 8min" */
export function formatRuntime(minutes: number | null, locale: string): string | null {
  if (!minutes || minutes <= 0) return null
  const german = locale.startsWith('de')
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (hours === 0) return german ? `${rest} Min.` : `${rest} min`
  if (rest === 0) return german ? `${hours} Std.` : `${hours}h`
  return german ? `${hours} Std. ${rest} Min.` : `${hours}h ${rest}min`
}

/** Farbe der Bewertung: grün ab 7, gelb ab 5, sonst rot. */
export function ratingTone(vote: number): 'good' | 'mid' | 'bad' {
  if (vote >= 7) return 'good'
  if (vote >= 5) return 'mid'
  return 'bad'
}

/** ISO-Datum von heute minus n Tagen. */
export function daysAgo(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() - days)
  return date.toISOString().slice(0, 10)
}

export function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export function daysAhead(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}

/**
 * Folgennummern kompakt: [1,2,3,5,8] -> „1–3, 5, 8".
 *
 * Für die Karten-Pille und die Glocke: Zehn einzelne Nummern sprengen jede
 * Zeile, und wer 1 bis 8 bestellt hat, liest „1–8" schneller als jede Liste.
 */
export function folgenKompakt(nummern: number[]): string {
  const sortiert = [...nummern].sort((a, b) => a - b)
  const teile: string[] = []
  let start: number | null = null
  let vorher = 0
  for (const nummer of sortiert) {
    if (start === null) {
      start = vorher = nummer
      continue
    }
    if (nummer === vorher + 1) {
      vorher = nummer
      continue
    }
    teile.push(start === vorher ? `${start}` : `${start}–${vorher}`)
    start = vorher = nummer
  }
  if (start !== null) teile.push(start === vorher ? `${start}` : `${start}–${vorher}`)
  return teile.join(', ')
}

/**
 * Bytes als lesbare Groesse - „12,4 GiB".
 *
 * Bewusst nur GiB und TiB: Alles darunter ist bei Filmen und Serien nie die
 * Frage, und eine Einheit, die zwischen MiB und TiB springt, macht Zahlen
 * untereinander unvergleichbar. Erst ab einem Tebibyte lohnt der Wechsel.
 *
 * ⚠️ **GiB, nicht GB - und das ist eine Korrektur, keine Umstellung.**
 * Gerechnet wurde hier immer schon durch 1024³, dranstand aber „GB". Das ist
 * die Einheit, die durch 1000³ teilt, und der Unterschied betraegt sieben
 * Prozent. Aufgefallen ist es im Vergleich mit Home Assistant: Dieselbe
 * Belegung stand dort als „100,3 GB", hier als „93 GB". Beide Zahlen waren
 * richtig, nur eine der beiden Beschriftungen.
 *
 * Der andere Weg waere gewesen, auf 1000³ umzurechnen. Der haette jedem, der
 * eine Grenze eingetragen hat, stillschweigend sieben Prozent davon genommen.
 */
export function formatSize(bytes: number, locale: string): string {
  const gib = bytes / 1024 ** 3
  if (gib >= 1024) {
    return `${(gib / 1024).toLocaleString(locale, { maximumFractionDigits: 2 })} TiB`
  }
  // Unter 10 GiB eine Nachkommastelle, darueber keine: „4,2 GiB" ist eine
  // Aussage, „1.312,5 GiB" nur eine lange Zahl.
  const stellen = gib < 10 ? 1 : 0
  return `${gib.toLocaleString(locale, { maximumFractionDigits: stellen })} GiB`
}
