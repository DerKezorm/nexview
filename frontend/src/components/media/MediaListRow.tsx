import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { MediaItem, MovieRatings } from '../../api/types'
import { formatDate, formatRuntime } from '../../lib/format'
import { titlePath } from '../../lib/routes'
import { CartIcon } from './MediaCard'
import { FavoriteButton } from './FavoriteButton'
import { RatingBadges } from './RatingBadges'
import { Poster, RatingBadge } from './Poster'
import { StatusBadge } from './StatusBadge'

type MediaListRowProps = {
  item: MediaItem
  /** Öffnet das Schnell-Popup zum Anfragen. */
  onQuickAdd: (item: MediaItem) => void
  ratings?: MovieRatings
  favorit?: boolean
}

/**
 * Kompakte Zeile: mehr Text auf einen Blick als in der Kachelansicht.
 *
 * Verhält sich wie die Kachel - Klick führt auf die Detailseite, der Wagen
 * rechts öffnet das Schnell-Popup.
 */
export function MediaListRow({
  item,
  onQuickAdd,
  ratings,
  favorit = false,
}: MediaListRowProps) {
  const { t, i18n } = useTranslation()
  const runtime = formatRuntime(item.runtime_minutes, i18n.language)
  const anfragbar = item.status === 'not_requested'

  return (
    <div className="group flex w-full items-stretch gap-4 rounded-xl border border-ink-700 bg-ink-850 p-3 transition-colors hover:border-accent-600/60 hover:bg-ink-800">
      <Link
        to={titlePath(item.media_type, item.tmdb_id)}
        aria-label={`${item.title} – ${t('media.openDetails')}`}
        className="flex min-w-0 flex-1 gap-4 text-left"
      >
        <div className="aspect-2/3 w-16 shrink-0 self-start overflow-hidden rounded-lg bg-ink-900 sm:w-20">
          <Poster url={item.poster_url} title={item.title} />
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="line-clamp-2 font-semibold">{item.title}</h3>
            <RatingBadge vote={item.vote_average} count={item.vote_count} />
            <RatingBadges ratings={ratings} title={item.title} />
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
      </Link>

      {/* Eine eigene Spalte rechts: der Zustand oben auf Höhe des Titels, der
          Wagen unten in der Ecke. Beide rechtsbündig - vorher saßen sie auf
          zwei verschiedenen Höhen, was zufällig wirkte.

          Beschriftung braucht der Wagen nicht: das Symbol ist auf Kachel und
          Zeile dasselbe, und ein "Schnell anfragen" daneben macht die Zeile nur
          unruhig. */}
      <div className="flex shrink-0 flex-col items-end justify-between gap-3">
        <StatusBadge status={item.status} />

        <div className="flex items-center gap-2">
          <FavoriteButton item={item} markiert={favorit} />

          {anfragbar && (
          <button
            type="button"
            onClick={() => onQuickAdd(item)}
            title={t('media.quickAdd')}
            aria-label={`${item.title} – ${t('media.quickAdd')}`}
            className="rounded-full border border-ink-700 bg-ink-900 p-2 text-mist-300 transition-colors hover:border-accent-500 hover:bg-accent-500 hover:text-white"
          >
            <CartIcon className="h-5 w-5" />
          </button>
          )}
        </div>
      </div>
    </div>
  )
}
