/** Wann darf ein Titel (wieder) angefragt werden? */

import type { MediaStatus } from '../api/types'

/**
 * Zustände, aus denen heraus eine Anfrage möglich ist.
 *
 * `not_requested` ist der Normalfall. Die drei anderen sind **erledigte**
 * Anfragen: Sie laufen nicht mehr, halten keinen Platz mehr besetzt und
 * zählen serverseitig auch nicht mehr (`find_active` kennt nur laufende).
 * Ohne sie war ein Titel nach einem Fehlschlag für immer gesperrt – der
 * Server hätte eine neue Anfrage angenommen, aber es gab keinen Knopf mehr
 * dafür. Gemeldet an einer Serie, die in Sonarr nie ankam.
 *
 * `deleted` gehört ausdrücklich dazu: Die Datei ist weg, der Titel muss
 * neu geholt werden können.
 */
const WIEDER_ANFRAGBAR: ReadonlySet<string> = new Set([
  'not_requested',
  'failed',
  'cancelled',
  'rejected',
  'deleted',
])

export function darfAnfragen(status: MediaStatus | string): boolean {
  return WIEDER_ANFRAGBAR.has(status)
}

/**
 * Zustände, in denen etwas **unterwegs** ist - darauf lässt sich warten.
 *
 * Bewusst nicht das Gegenteil von `darfAnfragen`: Ein Titel kann gleichzeitig
 * unterwegs *und* anfragbar sein, und genau dieser Fall war der Grund für
 * diese Liste. Steht die Standard-Fassung auf „wird gesucht", während 4K noch
 * offen ist, bleibt der Anfrage-Knopf da - und „Sag mir Bescheid" fehlte,
 * obwohl es genau die Lage ist, in der jemand mitwarten will. Gemeldet an
 * „Spider-Man: Brand New Day".
 *
 * `deleted` fehlt hier mit Absicht: Die Datei ist weg und niemand holt sie
 * gerade. Wer sie will, fragt an, statt zu warten.
 */
const UNTERWEGS: ReadonlySet<string> = new Set([
  'pending_approval',
  'requested',
  'searching',
])

/** Ist zu diesem Titel gerade etwas unterwegs? */
export function istUnterwegs(status: MediaStatus | string): boolean {
  return UNTERWEGS.has(status)
}
