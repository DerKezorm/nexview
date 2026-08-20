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
