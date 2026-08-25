import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Region } from '../api/types'

/**
 * Die Länder, unter denen eine Region gewählt werden kann.
 *
 * Kommt von TMDB, nicht aus dem Quelltext: Vorher standen hier acht feste
 * Kürzel, und wer in den Niederlanden oder Polen saß, konnte sein Land nicht
 * angeben. Gebraucht an drei Stellen - im eigenen Profil, in den
 * Einstellungen des Administrators und, nur zur Anzeige, bei den
 * Streaming-Diensten.
 *
 * Ändert sich praktisch nie, deshalb liegt sie eine Stunde still. Scheitert
 * TMDB, kommt eine leere Liste - die Aufrufer zeigen dann den gespeicherten
 * Wert allein.
 */
export function useRegionen() {
  return useQuery({
    queryKey: ['regionen'],
    queryFn: () => api.get<Region[]>('/api/config/regions'),
    staleTime: 60 * 60 * 1000,
  })
}
