import { useTranslation } from 'react-i18next'

import type { MediaItem } from '../../api/types'
import { formatRuntime, formatYear } from '../../lib/format'
import { Poster, RatingBadge } from './Poster'
import { StatusBadge } from './StatusBadge'

type MediaCardProps = {
  item: MediaItem
  onOpen: (item: MediaItem) => void
}

/** Kachel im Netflix-Stil: großes Poster, Status ohne Klick sichtbar. */
export function MediaCard({ item, onOpen }: MediaCardProps) {
  const { t, i18n } = useTranslation()
  const runtime = formatRuntime(item.runtime_minutes, i18n.language)

  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      aria-label={`${item.title} – ${t('media.openDetails')}`}
      className="group flex flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-850 text-left transition-all hover:-translate-y-1 hover:border-accent-600/60 hover:shadow-2xl hover:shadow-accent-700/20"
    >
      <div className="relative aspect-2/3 overflow-hidden bg-ink-900">
        <Poster
          url={item.poster_url}
          title={item.title}
          className="h-full w-full transition-transform duration-300 group-hover:scale-105"
        />

        <div className="absolute inset-x-2 top-2 flex items-start justify-between gap-2">
          <StatusBadge status={item.status} />
          <RatingBadge vote={item.vote_average} count={item.vote_count} />
        </div>

        {item.certification && (
          <span className="absolute bottom-2 left-2 rounded-md bg-ink-950/85 px-1.5 py-0.5 text-[11px] font-bold text-mist-300 ring-1 ring-ink-700">
            {item.certification}
          </span>
        )}

        {/* Beschreibung erscheint erst beim Überfahren - hält das Raster ruhig. */}
        <div className="pointer-events-none absolute inset-0 flex items-end bg-linear-to-t from-ink-950 via-ink-950/60 to-transparent p-3 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
          <p className="line-clamp-5 text-xs leading-relaxed text-mist-300">
            {item.overview || t('media.noOverview')}
          </p>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-1 p-3">
        <h3 className="line-clamp-2 text-sm leading-snug font-semibold">{item.title}</h3>
        <p className="mt-auto text-xs text-mist-500">
          {formatYear(item.release_date)}
          {runtime && <span> · {runtime}</span>}
        </p>
        {item.genres.length > 0 && (
          <p className="line-clamp-1 text-xs text-mist-600">{item.genres.join(', ')}</p>
        )}
      </div>
    </button>
  )
}
