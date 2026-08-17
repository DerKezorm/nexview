import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { Genre, MediaItem, MediaPage, MediaType } from '../../api/types'
import { daysAgo, daysAhead, today } from '../../lib/format'
import { Button, ErrorBanner, Spinner } from '../ui'
import { DetailModal } from './DetailModal'
import { FilterBar } from './FilterBar'
import type { Filters, ViewMode } from './FilterBar'
import { MediaCard } from './MediaCard'
import { MediaListRow } from './MediaListRow'
import { useFavorites } from './FavoriteButton'
import { useMovieRatings } from './RatingBadges'

const DEFAULT_FILTERS: Filters = {
  period: '30',
  language: '',
  region: 'DE',
  genreId: null,
  // "Neueste zuerst" liefert bei TMDB fast nur namenlose Kleinstproduktionen -
  // gemessen 0 von 20 Treffern mit Beschreibung. Deshalb "Beliebteste".
  sort: 'popular',
  hideExisting: false,
  featureLength: false,
  minRating: 0,
  hideUnrated: false,
  releasedInRegion: false,
  hideWithoutOverview: false,
  studioId: null,
  knownOnly: true,
}

/**
 * Zustände, die bedeuten: schon angefragt oder schon da.
 *
 * "Wartet auf Freigabe" gehört dazu - der Titel kann kein zweites Mal
 * angefragt werden und soll deshalb mit ausgeblendet werden.
 */
const ALREADY_HANDLED: ReadonlySet<string> = new Set([
  'downloaded',
  'in_library',
  'searching',
  'requested',
  'pending_approval',
])

/** Zeitraum-Auswahl in konkrete Datumsgrenzen übersetzen. */
function dateRange(period: Filters['period']): { from: string; to: string } {
  if (period === 'upcoming') return { from: today(), to: daysAhead(120) }
  return { from: daysAgo(Number(period)), to: today() }
}

function buildQuery(mediaType: MediaType, filters: Filters, page: number): string {
  const { from, to } = dateRange(filters.period)
  const params = new URLSearchParams({
    date_from: from,
    date_to: to,
    region: filters.region,
    sort: filters.sort,
    page: String(page),
  })
  if (filters.language) params.set('language', filters.language)
  if (filters.genreId !== null) params.set('genre_id', String(filters.genreId))
  if (filters.featureLength) params.set('feature_length', 'true')
  if (filters.minRating > 0) params.set('min_rating', String(filters.minRating))
  if (filters.hideUnrated) params.set('hide_unrated', 'true')
  if (filters.releasedInRegion) params.set('released_in_region', 'true')
  if (filters.studioId !== null) params.set('studio_id', String(filters.studioId))
  if (filters.knownOnly) params.set('known_only', 'true')
  return `/api/discover/${mediaType}?${params.toString()}`
}

function CardSkeleton() {
  return (
    <div className="overflow-hidden rounded-xl border border-ink-700 bg-ink-850">
      <div className="aspect-2/3 animate-pulse bg-ink-800" />
      <div className="space-y-2 p-3">
        <div className="h-3.5 w-4/5 animate-pulse rounded bg-ink-800" />
        <div className="h-3 w-2/5 animate-pulse rounded bg-ink-800" />
      </div>
    </div>
  )
}

type MediaSectionProps = {
  mediaType: MediaType
  titleKey: string
  /** Gesetzt, wenn oben gesucht wird - dann treten die Filter zurück. */
  searchQuery: string
  defaultRegion: string
  /** Ist Radarr (Filme) bzw. Sonarr (Serien) eingerichtet? */
  arrConfigured: boolean
}

/**
 * Ein vollständiger Bereich für genau einen Medientyp.
 *
 * Filme und Serien verwenden dieselbe Darstellung, aber getrennte Zustände
 * und getrennte Endpunkte - später also auch getrennte Radarr-/Sonarr-Logik.
 */
export function MediaSection({
  mediaType,
  titleKey,
  searchQuery,
  defaultRegion,
  arrConfigured,
}: MediaSectionProps) {
  const { t } = useTranslation()
  // Vorbelegung nur für die Region - danach bestimmt der Benutzer die Filter
  // selbst, und ein Nachladen im Hintergrund darf ihm die Auswahl nicht
  // wieder umstellen. Deshalb nur der Startwert.
  //
  // Die Originalsprache startet immer auf "alle": eine vorbelegte Sprache
  // bedeutet "nur in dieser Sprache gedreht" und lieferte praktisch nichts.
  const [filters, setFilters] = useState<Filters>({
    ...DEFAULT_FILTERS,
    region: defaultRegion,
  })
  const [view, setView] = useState<ViewMode>('grid')
  const [selected, setSelected] = useState<MediaItem | null>(null)

  const searching = searchQuery.length >= 2

  const { data: genres = [] } = useQuery({
    queryKey: ['genres', mediaType],
    queryFn: () => api.get<Genre[]>(`/api/genres/${mediaType}`),
    staleTime: 24 * 60 * 60 * 1000,
  })

  // Studios gibt es nur für Filme - die Liste ist fest, also einmal laden reicht.
  const { data: studios = [] } = useQuery({
    queryKey: ['studios'],
    queryFn: () => api.get<Genre[]>('/api/studios'),
    enabled: mediaType === 'movie',
    staleTime: Infinity,
  })

  const query = useInfiniteQuery({
    queryKey: ['discover', mediaType, searching ? searchQuery : filters],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api.get<MediaPage>(
        searching
          ? `/api/search/${mediaType}?q=${encodeURIComponent(searchQuery)}&page=${pageParam}`
          : buildQuery(mediaType, filters, pageParam as number),
      ),
    getNextPageParam: (lastPage) =>
      lastPage.page < lastPage.total_pages ? lastPage.page + 1 : undefined,
  })

  const loaded = query.data?.pages.flatMap((page) => page.items) ?? []
  const total = query.data?.pages[0]?.total_results ?? 0
  const arrWarning = query.data?.pages[0]?.arr_warning ?? null

  // Diese beiden Filter wirken auf die bereits geladenen Titel: TMDB weiß
  // nicht, was in deiner Bibliothek liegt, und kann auch nicht nach
  // "hat eine Beschreibung" filtern. Eine Seite kann dadurch kürzer werden.
  const items = loaded.filter(
    (item) =>
      !(filters.hideExisting && ALREADY_HANDLED.has(item.status)) &&
      !(filters.hideWithoutOverview && !item.overview.trim()),
  )
  const hiddenCount = loaded.length - items.length

  // Nachgelagert: die Liste steht sofort, die Wertungen kommen kurz darauf.
  const ratings = useMovieRatings(items)
  const { markiert } = useFavorites()

  // Welche Filter schränken so stark ein, dass eine leere Liste daran liegen
  // kann? Genau die werden im leeren Zustand benannt.
  const einschraenkendeFilter = [
    filters.language && t('filters.language'),
    filters.genreId !== null && t('filters.genre'),
    filters.minRating > 0 && t('filters.minRating'),
  ].filter(Boolean) as string[]

  // Schlägt eine Aktualisierung fehl, behält React Query die zuletzt geladenen
  // Daten. Ohne diesen Hinweis sähe die Seite aus, als sei alles in Ordnung,
  // obwohl z. B. der TMDB-Key nicht mehr akzeptiert wird.
  const failure = query.error ?? query.failureReason

  return (
    <section className="rounded-2xl border border-ink-700 bg-ink-850/40 p-4 sm:p-5">
      <header className="mb-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-xl font-bold tracking-tight">{t(titleKey)}</h2>
        {!query.isPending && (
          <span className="text-sm text-mist-600">{t('discover.results', { count: total })}</span>
        )}
        {hiddenCount > 0 && (
          <span className="text-sm text-mist-600">
            · {t('filters.hiddenCount', { count: hiddenCount })}
          </span>
        )}
      </header>

      {!searching && (
        <div className="mb-5">
          <FilterBar
            filters={filters}
            onChange={setFilters}
            genres={genres}
            view={view}
            onViewChange={setView}
            showFeatureLength={mediaType === 'movie'}
            arrConfigured={arrConfigured}
            studios={studios}
          />
        </div>
      )}

      {arrWarning && (
        <div className="mb-4 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {arrWarning}
        </div>
      )}

      {failure && (
        <div className="mb-4">
          <ErrorBanner
            message={failure instanceof ApiError ? failure.message : t('errors.generic')}
          />
        </div>
      )}

      {query.isPending && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
          {Array.from({ length: 6 }, (_, index) => (
            <CardSkeleton key={index} />
          ))}
        </div>
      )}

      {!query.isPending && !failure && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-ink-700 px-4 py-12 text-center">
          <p className="text-sm text-mist-500">
            {/* Unterscheiden: gar nichts gefunden, oder alles lokal weggefiltert? */}
            {hiddenCount > 0 ? t('filters.allFiltered') : t('discover.empty')}
          </p>

          {/* Eine leere Liste ohne Erklärung sieht aus wie ein Defekt. Der
              häufigste Grund ist ein Filter, den man selbst gesetzt hat -
              besonders die Originalsprache: auf Deutsch gedrehte Filme sind
              die Ausnahme, also bleibt fast nichts übrig. */}
          {einschraenkendeFilter.length > 0 && (
            <>
              <p className="mx-auto mt-3 max-w-md text-xs leading-relaxed text-mist-600">
                {t('filters.emptyBecause', { filter: einschraenkendeFilter.join(', ') })}
              </p>
              <Button
                type="button"
                variant="ghost"
                onClick={() =>
                  setFilters({ ...filters, language: '', genreId: null, minRating: 0 })
                }
                className="mt-4 !px-4 !py-2"
              >
                {t('filters.resetNarrow')}
              </Button>
            </>
          )}
        </div>
      )}

      {items.length > 0 &&
        (view === 'grid' ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
            {items.map((item) => (
              <MediaCard
                key={item.tmdb_id}
                item={item}
                onQuickAdd={setSelected}
                ratings={ratings[item.tmdb_id]}
                favorit={markiert.has(`${item.media_type}-${item.tmdb_id}`)}
              />
            ))}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {items.map((item) => (
              <MediaListRow
                key={item.tmdb_id}
                item={item}
                onQuickAdd={setSelected}
                ratings={ratings[item.tmdb_id]}
                favorit={markiert.has(`${item.media_type}-${item.tmdb_id}`)}
              />
            ))}
          </div>
        ))}

      {query.hasNextPage && (
        <div className="mt-6 flex justify-center">
          <Button
            variant="ghost"
            onClick={() => void query.fetchNextPage()}
            loading={query.isFetchingNextPage}
          >
            {t('discover.loadMore')}
          </Button>
        </div>
      )}

      {query.isFetching && !query.isFetchingNextPage && !query.isPending && (
        <p className="mt-4 flex items-center justify-center gap-2 text-xs text-mist-600">
          <Spinner className="h-3 w-3" />
          {t('common.loading')}
        </p>
      )}

      <DetailModal
        item={selected}
        onClose={() => setSelected(null)}
        arrConfigured={arrConfigured}
      />
    </section>
  )
}
