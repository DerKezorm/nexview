import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useSearchParams } from 'react-router-dom'
import { useInfiniteQuery } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { MediaItem, MediaPage, MediaType } from '../api/types'
import { DetailModal } from '../components/media/DetailModal'
import { MediaItemCard } from '../components/media/MediaCard'
import { useCardData } from '../components/media/useCardData'
import { Button, ErrorBanner, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'

type BrowseResult = { label: string; page: MediaPage }

/**
 * Alle Titel zu einem Schlagwort oder Studio.
 *
 * Bewusst ohne Filterleiste: dort steckt ein voreingestellter Zeitraum, der
 * fast alle Treffer wegschneiden würde. Wer auf „Zeitreise“ klickt, will alles
 * dazu sehen - nicht nur die Titel des letzten Monats.
 */
export function BrowsePage() {
  const { t } = useTranslation()
  const { mediaType, art, id } = useParams<{
    mediaType: MediaType
    art: 'schlagwort' | 'studio'
    id: string
  }>()
  const [params] = useSearchParams()
  const { data: config } = useConfig()

  const [schnellAnfrage, setSchnellAnfrage] = useState<MediaItem | null>(null)

  const parameter = art === 'studio' ? `company_id=${id}` : `keyword_id=${id}`

  const query = useInfiniteQuery({
    queryKey: ['browse', mediaType, art, id],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api.get<BrowseResult>(`/api/browse/${mediaType}?${parameter}&page=${pageParam}`),
    getNextPageParam: (last) =>
      last.page.page < last.page.total_pages ? last.page.page + 1 : undefined,
    enabled: Boolean(mediaType && id),
  })

  const items = query.data?.pages.flatMap((seite) => seite.page.items) ?? []
  const kachelDaten = useCardData(items)
  const gesamt = query.data?.pages[0]?.page.total_results ?? 0
  // Der Name aus der Antwort; beim ersten Rendern hilft der aus der Adresse
  // mitgegebene weiter, damit die Überschrift nicht springt.
  const name = query.data?.pages[0]?.label || params.get('name') || ''

  const arrConfigured =
    mediaType === 'movie'
      ? (config?.radarr_configured ?? false)
      : (config?.sonarr_configured ?? false)

  return (
    <div className="flex flex-col gap-6">
      <header>
        <p className="text-sm text-mist-500">
          {t(art === 'studio' ? 'browse.byStudio' : 'browse.byKeyword')}
        </p>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {name || '…'}
          <span className="text-accent-500">.</span>
        </h1>
        {!query.isPending && (
          <p className="mt-1.5 text-mist-500">
            {t(mediaType === 'movie' ? 'browse.movieResults' : 'browse.seriesResults', {
              count: gesamt,
            })}
          </p>
        )}
      </header>

      {query.error && (
        <ErrorBanner
          message={query.error instanceof ApiError ? query.error.message : t('errors.generic')}
        />
      )}

      {query.isPending ? (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-ink-700 px-4 py-12 text-center text-sm text-mist-500">
          {t('discover.empty')}
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
          {items.map((item) => (
            <MediaItemCard
              key={item.tmdb_id}
              item={item}
              onQuickAdd={setSchnellAnfrage}
              ratings={kachelDaten.ratingsFor(item)}
              favorit={kachelDaten.istFavorit(item)}
            />
          ))}
        </div>
      )}

      {query.hasNextPage && (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="ghost"
            onClick={() => void query.fetchNextPage()}
            loading={query.isFetchingNextPage}
          >
            {t('discover.loadMore')}
          </Button>
        </div>
      )}

      <DetailModal
        item={schnellAnfrage}
        onClose={() => setSchnellAnfrage(null)}
        arrConfigured={arrConfigured}
      />
    </div>
  )
}
