/**
 * Bewertungen zu einer Liste von Filmen nachladen.
 *
 * Bewusst getrennt vom Laden der Titel selbst: die Werte kommen aus Radarr,
 * und zwanzig Abfragen dorthin würden den Seitenaufbau bremsen. So steht die
 * Liste sofort da und die Zahlen erscheinen kurz darauf.
 *
 * Serien bleiben außen vor, Sonarr liefert keine Aufschlüsselung nach
 * Portalen, sondern nur eine Sammelwertung.
 *
 * Der Haken wohnt neben den Abzeichen, nicht in ihnen: `RatingBadges.tsx`
 * liefert damit nur noch Bauteile aus, und nur solche Dateien tauscht Vite im
 * Entwicklungsbetrieb im laufenden Bild aus.
 */

import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { MovieRatings } from '../../api/types'

export function useMovieRatings(
  items: { media_type: string; tmdb_id: number }[],
): Record<number, MovieRatings> {
  const ids = items
    .filter((item) => item.media_type === 'movie')
    .map((item) => item.tmdb_id)
    .sort((a, b) => a - b)

  const query = useQuery({
    queryKey: ['movie-ratings', ids.join(',')],
    queryFn: () => api.get<Record<number, MovieRatings>>(`/api/ratings/movie?ids=${ids.join(',')}`),
    enabled: ids.length > 0,
    // Wertungen ändern sich langsam; der Server hält sie ohnehin einen Tag vor.
    staleTime: 60 * 60 * 1000,
    retry: false,
  })

  return query.data ?? {}
}
