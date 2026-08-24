import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../../api/client'
import type { MediaItem, MediaPage, MediaType } from '../../api/types'
import { Titelliste } from '../stoebern/Titelliste'
import { Button, ErrorBanner, Spinner } from '../ui'
import { DetailModal } from './DetailModal'

type Props = {
  mediaType: MediaType
  /** Überschrift des Blocks — „Filme" bzw. „Serien". */
  titleKey: string
  suche: string
  arrConfigured: boolean
}

function Platzhalter() {
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

/**
 * Die Treffer einer Suche, je Medienart ein Block.
 *
 * Nachfolger von `MediaSection`. Die alte Fassung bediente **beides** —
 * Entdecken und Suchen — und schleppte dafür die ganze Filterleiste, die
 * Genre- und Studio-Abfragen und ein Sieb mit, das im Browser lief. Beim
 * Suchen war davon nichts sichtbar: Die Filterleiste blendete sich aus, die
 * Filter standen auf ihren Vorgabewerten und taten nichts.
 *
 * Seit die Entdecken-Seiten entfernt sind, wäre das alles tot dagelegen —
 * einschließlich des bekannten Fehlers, dass jenes Sieb erst *nach* dem Laden
 * wirkte und eine Seite von zwanzig Kacheln auf zwei zusammenschrumpfen ließ.
 * Toter Code mit einem bekannten Fehler darin ist eine Falle für den
 * Nächsten, also ist er weg.
 */
export function SuchErgebnis({ mediaType, titleKey, suche, arrConfigured }: Props) {
  const { t } = useTranslation()
  const [gewaehlt, setGewaehlt] = useState<MediaItem | null>(null)

  const query = useInfiniteQuery({
    queryKey: ['suche', mediaType, suche],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api.get<MediaPage>(
        `/api/search/${mediaType}?q=${encodeURIComponent(suche)}&page=${pageParam}`,
      ),
    getNextPageParam: (letzte) =>
      letzte.page < letzte.total_pages ? letzte.page + 1 : undefined,
  })

  const items = query.data?.pages.flatMap((seite) => seite.items) ?? []
  const gesamt = query.data?.pages[0]?.total_results ?? 0
  const arrWarning = query.data?.pages[0]?.arr_warning ?? null
  // Schlägt ein Nachladen fehl, behält React Query die alten Daten — dann
  // steht der Fehler nur in `failureReason` und die Seite sähe sonst heil aus,
  // obwohl etwa der TMDB-Schlüssel nicht mehr angenommen wird.
  const fehler = query.error ?? query.failureReason

  return (
    <section className="rounded-2xl border border-ink-700 bg-ink-850/40 p-4 sm:p-5">
      <header className="mb-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-xl font-bold tracking-tight">{t(titleKey)}</h2>
        {!query.isPending && (
          <span className="text-sm text-mist-600">
            {t('discover.results', { count: gesamt })}
          </span>
        )}
      </header>

      {arrWarning && (
        <div className="mb-4 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {arrWarning}
        </div>
      )}

      {fehler && (
        <div className="mb-4">
          <ErrorBanner
            message={fehler instanceof ApiError ? fehler.message : t('errors.generic')}
          />
        </div>
      )}

      {query.isPending && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
          {Array.from({ length: 6 }, (_, index) => (
            <Platzhalter key={index} />
          ))}
        </div>
      )}

      {!query.isPending && !fehler && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-ink-700 px-4 py-12 text-center">
          <p className="text-sm text-mist-500">{t('discover.empty')}</p>
        </div>
      )}

      {items.length > 0 && (
        <Titelliste items={items} ansicht="kacheln" onQuickAdd={setGewaehlt} />
      )}

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
        item={gewaehlt}
        onClose={() => setGewaehlt(null)}
        arrConfigured={arrConfigured}
      />
    </section>
  )
}
