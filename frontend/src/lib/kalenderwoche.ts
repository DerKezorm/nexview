/**
 * Kalenderwochen nach ISO 8601 – die Zählung, die hierzulande auf jedem
 * Kalender steht.
 *
 * Zwei Regeln machen den ganzen Unterschied zu einer naiven „Tag durch 7“:
 *
 * 1. Die Woche beginnt am **Montag**, nicht am Sonntag.
 * 2. Woche 1 ist die Woche, in der der **4. Januar** liegt. Der 1. Januar kann
 *    also noch zur letzten Woche des Vorjahres gehören – und der 31. Dezember
 *    schon zur Woche 1 des Folgejahres.
 *
 * Alles hier rechnet in UTC. Nicht aus Bequemlichkeit, sondern weil
 * `setDate` über Sommerzeitgrenzen hinweg sonst um eine Stunde springt und
 * dabei gelegentlich einen Tag verliert.
 */

const TAG_IN_MS = 86_400_000

/** Datum ohne Uhrzeit, in UTC – die Grundlage aller Rechnungen hier. */
function nurTag(datum: Date): Date {
  return new Date(Date.UTC(datum.getFullYear(), datum.getMonth(), datum.getDate()))
}

/** Montag = 1 … Sonntag = 7 (JavaScript zählt Sonntag als 0). */
function wochentag(datum: Date): number {
  return datum.getUTCDay() || 7
}

export type Woche = { jahr: number; woche: number }

/**
 * In welcher Kalenderwoche liegt dieses Datum?
 *
 * Trick: Vom Datum aus zum Donnerstag derselben Woche springen. Der Donnerstag
 * liegt immer im „richtigen“ Jahr – genau das ist die ISO-Definition.
 */
export function isoWoche(datum: Date): Woche {
  const donnerstag = nurTag(datum)
  donnerstag.setUTCDate(donnerstag.getUTCDate() + 4 - wochentag(donnerstag))

  const jahresbeginn = Date.UTC(donnerstag.getUTCFullYear(), 0, 1)
  const tage = (donnerstag.getTime() - jahresbeginn) / TAG_IN_MS

  return { jahr: donnerstag.getUTCFullYear(), woche: Math.floor(tage / 7) + 1 }
}

/** Der Montag einer Kalenderwoche. */
export function montagDerWoche({ jahr, woche }: Woche): Date {
  // Der 4. Januar liegt per Definition in Woche 1.
  const vierter = new Date(Date.UTC(jahr, 0, 4))
  const montagDerErsten = new Date(vierter)
  montagDerErsten.setUTCDate(vierter.getUTCDate() - wochentag(vierter) + 1)

  const ziel = new Date(montagDerErsten)
  ziel.setUTCDate(montagDerErsten.getUTCDate() + (woche - 1) * 7)
  return ziel
}

/**
 * Wie viele Wochen hat dieses Jahr – 52 oder 53?
 *
 * Der 28. Dezember liegt immer in der letzten Woche des Jahres; das ist der
 * kürzeste Weg zur Antwort.
 */
export function wochenImJahr(jahr: number): number {
  return isoWoche(new Date(Date.UTC(jahr, 11, 28))).woche
}

/** Erster und letzter Tag einer Woche als ISO-Datum (`YYYY-MM-DD`). */
export function wochenSpanne(woche: Woche): { von: string; bis: string } {
  const montag = montagDerWoche(woche)
  const sonntag = new Date(montag)
  sonntag.setUTCDate(montag.getUTCDate() + 6)
  return { von: montag.toISOString().slice(0, 10), bis: sonntag.toISOString().slice(0, 10) }
}

/** Eine Woche vor oder zurück – über Jahresgrenzen hinweg. */
export function verschobeneWoche(woche: Woche, schritte: number): Woche {
  const montag = montagDerWoche(woche)
  montag.setUTCDate(montag.getUTCDate() + schritte * 7)
  return isoWoche(montag)
}

/**
 * Die Kalenderwoche von heute – in der Zeitzone des Benutzers.
 *
 * Bewusst nicht über `toISOString()`: Das rechnet in UTC, und abends um elf
 * käme dort schon der nächste Tag heraus – am Sonntagabend also gleich die
 * nächste Woche.
 */
export function heutigeWoche(): Woche {
  return isoWoche(new Date())
}
