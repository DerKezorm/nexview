/**
 * Die eigenen Merklisten, einmal geladen und überall verfügbar.
 *
 * Bewusst je eine gemeinsame Abfrage statt einer pro Herz: auf einer Seite mit
 * zwanzig Kacheln wären das zwanzig Aufrufe für dieselbe Liste.
 *
 * Die Haken wohnen neben den Herzen, nicht in ihnen: `FavoriteButton.tsx`
 * liefert damit nur noch Bauteile aus, und nur solche Dateien tauscht Vite im
 * Entwicklungsbetrieb im laufenden Bild aus.
 */

import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { Favorite, FavoritePerson } from '../../api/types'

/** Die gemerkten Titel. */
export function useFavorites() {
  const query = useQuery({
    queryKey: ['favorites'],
    queryFn: () => api.get<Favorite[]>('/api/favorites'),
    staleTime: 5 * 60 * 1000,
  })

  const markiert = new Set((query.data ?? []).map((f) => `${f.media_type}-${f.tmdb_id}`))
  // ⚠️ `fehlgeschlagen` gehoert mit heraus. Ohne das gab der Haken bei einer
  // Stoerung dasselbe zurueck wie bei einer wirklich leeren Merkliste - und
  // die Seite darueber konnte gar nicht unterscheiden, was sie anzeigen soll.
  return { favorites: query.data ?? [], markiert, fehlgeschlagen: query.isError }
}

/** Die gemerkten Personen, gebaut wie `useFavorites`. */
export function usePersonFavorites() {
  const query = useQuery({
    queryKey: ['person-favorites'],
    queryFn: () => api.get<FavoritePerson[]>('/api/favorites/people'),
    staleTime: 5 * 60 * 1000,
  })
  const markiert = new Set((query.data ?? []).map((p) => p.person_id))
  return { people: query.data ?? [], markiert, fehlgeschlagen: query.isError }
}
