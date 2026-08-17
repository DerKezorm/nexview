import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { Favorite, MediaItem } from '../../api/types'

/**
 * Die eigenen Favoriten - einmal geladen und überall verfügbar.
 *
 * Bewusst eine gemeinsame Abfrage statt einer pro Herz: auf einer Seite mit
 * zwanzig Kacheln wären das zwanzig Aufrufe für dieselbe Liste.
 */
export function useFavorites() {
  const query = useQuery({
    queryKey: ['favorites'],
    queryFn: () => api.get<Favorite[]>('/api/favorites'),
    staleTime: 5 * 60 * 1000,
  })

  const markiert = new Set((query.data ?? []).map((f) => `${f.media_type}-${f.tmdb_id}`))
  return { favorites: query.data ?? [], markiert }
}

function HeartIcon({ gefuellt, className = 'h-4 w-4' }: { gefuellt: boolean; className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill={gefuellt ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21.2l7.7-7.7 1.1-1.1a5.5 5.5 0 0 0 0-7.8Z" />
    </svg>
  )
}

/**
 * Herz zum Markieren.
 *
 * Die Markierung ist die Grundlage der kuratierten Empfehlungen - deshalb
 * steht im Titel des Knopfes auch, wozu sie gut ist. Sonst wirkt sie wie eine
 * Merkliste, die nichts bewirkt.
 */
export function FavoriteButton({
  item,
  markiert,
  className = '',
  gross = false,
}: {
  item: MediaItem | { media_type: 'movie' | 'tv'; tmdb_id: number; title: string; poster_url: string | null }
  markiert: boolean
  className?: string
  gross?: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const umschalten = useMutation({
    mutationFn: async () => {
      if (markiert) {
        await api.delete(`/api/favorites/${item.media_type}/${item.tmdb_id}`)
        return
      }
      await api.post('/api/favorites', {
        media_type: item.media_type,
        tmdb_id: item.tmdb_id,
        title: item.title,
        poster_url: item.poster_url,
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['favorites'] })
      // Die kuratierte Liste hängt direkt daran.
      void queryClient.invalidateQueries({ queryKey: ['home-curated'] })
    },
  })

  return (
    <button
      type="button"
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        umschalten.mutate()
      }}
      disabled={umschalten.isPending}
      title={t(markiert ? 'favorites.remove' : 'favorites.add')}
      aria-label={`${item.title} – ${t(markiert ? 'favorites.remove' : 'favorites.add')}`}
      aria-pressed={markiert}
      className={
        'shrink-0 rounded-full border transition-colors ' +
        (gross ? 'p-2.5 ' : 'p-1.5 ') +
        (markiert
          ? 'border-accent-500 bg-accent-500/15 text-accent-400 hover:bg-accent-500/25'
          : 'border-ink-700 bg-ink-900 text-mist-400 hover:border-accent-500 hover:text-accent-400') +
        ' ' +
        className
      }
    >
      <HeartIcon gefuellt={markiert} className={gross ? 'h-5 w-5' : 'h-4 w-4'} />
    </button>
  )
}
