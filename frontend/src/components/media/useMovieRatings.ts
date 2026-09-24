/**
 * Bewertungen zu einer Liste von Filmen nachladen.
 *
 * Bewusst getrennt vom Laden der Titel selbst: die Werte kommen aus dem
 * eingestellten Weg (Radarr im ARR-Betrieb, OMDb über nexcrate im
 * NEX-Betrieb), und zwanzig Abfragen dorthin würden den Seitenaufbau bremsen.
 * So steht die Liste sofort da und die Zahlen erscheinen kurz darauf.
 *
 * Serien bleiben außen vor: Keiner der beiden Wege liefert für sie eine
 * Aufschlüsselung nach Portalen, sondern nur eine Sammelwertung.
 *
 * Der Haken wohnt neben den Abzeichen, nicht in ihnen: `RatingBadges.tsx`
 * liefert damit nur noch Bauteile aus, und nur solche Dateien tauscht Vite im
 * Entwicklungsbetrieb im laufenden Bild aus.
 */

import { useSyncExternalStore } from 'react'
import { type QueryCache, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { MovieRatings } from '../../api/types'

const WERTUNGEN = 'movie-ratings'

export function useMovieRatings(
  items: { media_type: string; tmdb_id: number }[],
  /**
   * Nur für die Titelseite: Im NEX-Betrieb fragt dann nexcrates Einzelansicht
   * OMDb nach Rotten Tomatoes und Metacritic, wenn sie noch nicht in seinem
   * Speicher liegen, und das kostet je Titel eine Abfrage. Listen setzen das
   * nie; ihr Stapel liest nur den Speicher.
   */
  { einzeln = false }: { einzeln?: boolean } = {},
): Record<number, MovieRatings> {
  const ids = items
    .filter((item) => item.media_type === 'movie')
    .map((item) => item.tmdb_id)
    .sort((a, b) => a - b)
  const zusatz = einzeln ? '&detail=true' : ''

  const query = useQuery({
    queryKey: [WERTUNGEN, ids.join(','), einzeln],
    queryFn: () =>
      api.get<Record<number, MovieRatings>>(`/api/ratings/movie?ids=${ids.join(',')}${zusatz}`),
    enabled: ids.length > 0,
    // Wertungen ändern sich langsam; der Server hält sie ohnehin einen Tag vor.
    staleTime: 60 * 60 * 1000,
    retry: false,
  })

  return query.data ?? {}
}

/** Alle Sätze der geladenen Wertungen, jeder einmal, als ein Text je Zeile. */
function nennungen(cache: QueryCache): string {
  const saetze = new Set<string>()
  for (const abfrage of cache.findAll({ queryKey: [WERTUNGEN] })) {
    const daten = abfrage.state.data as Record<number, MovieRatings> | undefined
    for (const wertung of Object.values(daten ?? {})) {
      for (const satz of wertung.attribution ?? []) saetze.add(satz)
    }
  }
  return [...saetze].join('\n')
}

/**
 * Die Namensnennung für die Fußzeile: was die Quelle zu den Wertungen
 * verlangt, die gerade geladen sind.
 *
 * Karten und Listenzeilen haben keinen Platz für den Satz; IMDb verlangt ihn
 * trotzdem dort, wo die Werte stehen. Er kommt wörtlich von nexcrate. Im
 * ARR-Betrieb schickt niemand einen, dann bleibt die Liste leer – gefragt
 * wird nach den Sätzen, nicht nach dem Namen des Wegs.
 */
export function useWertungsNennung(): string[] {
  const cache = useQueryClient().getQueryCache()
  const text = useSyncExternalStore(
    (melden) => cache.subscribe(melden),
    () => nennungen(cache),
  )
  return text ? text.split('\n') : []
}
