/**
 * Adressen der Detailseiten an einer Stelle.
 *
 * Sie werden aus Kacheln, Listenzeilen, Empfehlungen und Filmografien heraus
 * gebaut - ohne gemeinsame Stelle liefe man Gefahr, sie an einer davon zu
 * ändern und die übrigen zu vergessen.
 */

import type { MediaType } from '../api/types'

/**
 * Adresse der Detailseite.
 *
 * `fromWatchlist` haengt eine Herkunftsangabe an. Sie ist keine Kosmetik:
 * Wer von der Merklisten-Seite auf ein Poster klickt, landet auf der ganz
 * gewoehnlichen Detailseite - ohne diese Angabe ginge unterwegs verloren,
 * woher der Klick kam, und die Anfrage waere spaeter nicht mehr zuordenbar.
 */
export function titlePath(
  mediaType: MediaType,
  tmdbId: number,
  fromWatchlist = false,
): string {
  const pfad = `/titel/${mediaType}/${tmdbId}`
  return fromWatchlist ? `${pfad}?von=merkliste` : pfad
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

/** Übersicht der Regale. Serien sind ein eigener Pfad, kein Parameter. */
export function stoeberPath(mediaType: MediaType): string {
  return mediaType === 'movie' ? '/stoebern' : '/stoebern/serien'
}

/** Ein einzelnes Regal in voller Länge. */
export function regalPath(mediaType: MediaType, kennung: string): string {
  return `/stoebern/regal/${mediaType}/${kennung}`
}

/** Freie Auswahl im Rückkatalog. */
export function stoeberFilterPath(mediaType: MediaType): string {
  return `/stoebern/filter/${mediaType}`
}
