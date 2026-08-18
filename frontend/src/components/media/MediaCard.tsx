import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { MediaItem, MovieRatings } from '../../api/types'
import { formatRuntime, formatYear } from '../../lib/format'
import { titlePath } from '../../lib/routes'
import { FavoriteButton } from './FavoriteButton'
import { RatingBadges } from './RatingBadges'
import { Poster, RatingBadge } from './Poster'
import { StatusBadge } from './StatusBadge'

type MediaCardProps = {
  item: MediaItem
  /** Öffnet das Schnell-Popup zum Anfragen. */
  onQuickAdd: (item: MediaItem) => void
  /** Wertungen von IMDb & Co., sobald sie da sind. */
  ratings?: MovieRatings
  /** Ist dieser Titel als Favorit markiert? */
  favorit?: boolean
}

/**
 * Kachel im Netflix-Stil: großes Poster, Status ohne Klick sichtbar.
 *
 * Ein Klick führt auf die Detailseite - stöbern ist der häufigere Fall. Wer
 * nur schnell anfragen will, nimmt den Wagen oben rechts; der öffnet dasselbe
 * kleine Fenster wie früher, ohne die Seite zu verlassen.
 */
export function MediaCard({ item, onQuickAdd, ratings, favorit = false }: MediaCardProps) {
  const { t, i18n } = useTranslation()
  const runtime = formatRuntime(item.runtime_minutes, i18n.language)
  const anfragbar = item.status === 'not_requested'

  return (
    <div className="group relative flex flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-850 transition-all hover:-translate-y-1 hover:border-accent-600/60 hover:shadow-2xl hover:shadow-accent-700/20">
      <Link
        to={titlePath(item.media_type, item.tmdb_id)}
        aria-label={`${item.title} – ${t('media.openDetails')}`}
        className="flex flex-1 flex-col text-left"
      >
        <div className="relative aspect-2/3 overflow-hidden bg-ink-900">
          <Poster
            url={item.poster_url}
            title={item.title}
            className="h-full w-full transition-transform duration-300 group-hover:scale-105"
          />

          {/* Oben teilen sich nur zwei Dinge die Zeile. Vorher drängte sich
              der Wagen dazwischen, und "Nicht angefragt" brach um. */}
          <div className="absolute inset-x-2 top-2 flex flex-wrap items-start justify-between gap-1.5">
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
      </Link>

      {/* Ein Balken unter der Kachel: links die Wertungen, rechts der Wagen.
          Auf dem Poster wurde es zu eng - "Nicht angefragt" brach um, und die
          Abzeichen standen gequetscht in der Ecke. Hier ist Platz, und die
          verlinkten Abzeichen liegen außerhalb des Kachel-Links, wo sie
          hingehören: ein Link im Link ist nicht erlaubt. */}
      {/* Der Balken ist immer da: das Herz soll an jedem Titel stehen,
          auch an einem, der längst in der Bibliothek liegt - mögen kann man
          ihn ja trotzdem. */}
      <div className="flex items-center justify-between gap-2 border-t border-ink-700/60 px-3 py-2">
        <RatingBadges ratings={ratings} title={item.title} />

        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          <FavoriteButton item={item} markiert={favorit} />

          {anfragbar && (
            <button
              type="button"
              onClick={() => onQuickAdd(item)}
              title={t('media.quickAdd')}
              aria-label={`${item.title} – ${t('media.quickAdd')}`}
              className="rounded-full border border-ink-700 bg-ink-900 p-1.5 text-mist-300 transition-colors hover:border-accent-500 hover:bg-accent-500 hover:text-white"
            >
              <CartIcon />
            </button>
          )}
        </div>
      </div>

    </div>
  )
}

/** Einkaufswagen - steht für "schnell anfragen". */
export function CartIcon({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="9" cy="20" r="1.5" />
      <circle cx="18" cy="20" r="1.5" />
      <path d="M2 3h2.5l2.4 12.2a2 2 0 0 0 2 1.6h8.6a2 2 0 0 0 2-1.6L21 7H6" />
    </svg>
  )
}
