import { useTranslation } from 'react-i18next'

import type { MediaItem } from '../../api/types'
import { formatDate, formatRuntime } from '../../lib/format'
import { Poster, RatingBadge } from './Poster'
import { StatusBadge } from './StatusBadge'

type MediaListRowProps = {
  item: MediaItem
  onOpen: (item: MediaItem) => void
}

/** Kompakte Zeile: mehr Text auf einen Blick als in der Kachelansicht. */
export function MediaListRow({ item, onOpen }: MediaListRowProps) {
  const { t, i18n } = useTranslation()
  const runtime = formatRuntime(item.runtime_minutes, i18n.language)

  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      aria-label={`${item.title} – ${t('media.openDetails')}`}
      className="group flex w-full gap-4 rounded-xl border border-ink-700 bg-ink-850 p-3 text-left transition-colors hover:border-accent-600/60 hover:bg-ink-800"
    >
      <div className="aspect-2/3 w-16 shrink-0 overflow-hidden rounded-lg bg-ink-900 sm:w-20">
        <Poster url={item.poster_url} title={item.title} />
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="truncate font-semibold">{item.title}</h3>
          <RatingBadge vote={item.vote_average} count={item.vote_count} />
          <StatusBadge status={item.status} className="ml-auto" />
        </div>

        <p className="text-xs text-mist-500">
          {formatDate(item.release_date, i18n.language)}
          {runtime && <span> · {runtime}</span>}
          {item.certification && <span> · {t('media.certification')} {item.certification}</span>}
          {item.genres.length > 0 && <span> · {item.genres.join(', ')}</span>}
        </p>

        <p className="line-clamp-2 text-sm leading-relaxed text-mist-500">
          {item.overview || t('media.noOverview')}
        </p>
      </div>
    </button>
  )
}
