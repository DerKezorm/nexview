/** Kleine Helfer zur Darstellung von Daten und Laufzeiten. */

export function formatDate(value: string | null, locale: string): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString(locale, { day: '2-digit', month: 'short', year: 'numeric' })
}

/** Wie formatDate, aber mit Uhrzeit - für Angaben wie "zuletzt geprüft". */
export function formatDateTime(value: string | null, locale: string): string {
  if (!value) return '—'
  const parsed = new Date(value)
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
