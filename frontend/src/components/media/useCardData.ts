/**
 * Alles, was eine Kachel über ihren Titel hinaus braucht: Wertungen und
 * Favoriten.
 *
 * Es gibt diesen Haken, weil das Vergessen dieser beiden Angaben nicht wie ein
 * Fehler aussieht, sondern wie eine Gestaltungsentscheidung. Genau das ist
 * passiert: „Titel durchstöbern" und die Empfehlungen auf der Detailseite
 * verwendeten dieselbe Kachel wie die Entdecken-Seite, reichten ihr aber weder
 * Wertungen noch Favoritenstand durch. Ergebnis: dort fehlten die
 * IMDb-Abzeichen und jedes Herz war leer – monatelang, ohne dass es als Fehler
 * auffiel.
 *
 * Wer Kacheln zeigt, ruft hier einmal auf und ist fertig.
 */

import { useFavorites } from './useFavorites'
import { useMovieRatings } from './useMovieRatings'

export function useCardData(items: { media_type: string; tmdb_id: number }[]) {
  const ratings = useMovieRatings(items)
  const { markiert } = useFavorites()

  return {
    /** Wertungen zu einem Titel – oder undefined, solange sie fehlen. */
    ratingsFor: (item: { tmdb_id: number }) => ratings[item.tmdb_id],
    /** Ist dieser Titel gemerkt? */
    istFavorit: (item: { media_type: string; tmdb_id: number }) =>
      markiert.has(`${item.media_type}-${item.tmdb_id}`),
  }
}
