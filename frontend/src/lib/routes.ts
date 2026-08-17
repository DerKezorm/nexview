/**
 * Adressen der Detailseiten an einer Stelle.
 *
 * Sie werden aus Kacheln, Listenzeilen, Empfehlungen und Filmografien heraus
 * gebaut - ohne gemeinsame Stelle liefe man Gefahr, sie an einer davon zu
 * ändern und die übrigen zu vergessen.
 */

import type { MediaType } from '../api/types'

export function titlePath(mediaType: MediaType, tmdbId: number): string {
  return `/titel/${mediaType}/${tmdbId}`
}

export function personPath(personId: number): string {
  return `/person/${personId}`
}

/** Ergebnisliste zu einem Schlagwort bzw. Studio. */
export function browsePath(
  mediaType: MediaType,
  art: 'schlagwort' | 'studio',
  id: number,
  name: string,
): string {
  // Der Name reist als Parameter mit, damit die Überschrift sofort steht und
  // nicht erst nach der Antwort vom Server erscheint.
  return `/liste/${mediaType}/${art}/${id}?name=${encodeURIComponent(name)}`
}
