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
