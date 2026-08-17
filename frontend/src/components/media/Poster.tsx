import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ratingTone } from '../../lib/format'

type PosterProps = {
  url: string | null
  title: string
  className?: string
}

/** Poster mit Ersatzdarstellung, falls das Bild fehlt oder nicht lädt. */
export function Poster({ url, title, className = '' }: PosterProps) {
  const { t } = useTranslation()
  const [failed, setFailed] = useState(false)

  if (!url || failed) {
    return (
      <div
        className={
          'flex h-full w-full flex-col items-center justify-center gap-1 ' +
          'bg-linear-to-br from-ink-800 to-ink-900 p-3 text-center ' +
          className
        }
      >
        <span className="line-clamp-3 text-xs font-semibold text-mist-500">{title}</span>
        <span className="text-[10px] text-mist-600">{t('media.noPoster')}</span>
      </div>
    )
  }

  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className={'h-full w-full object-cover ' + className}
    />
  )
}

const RATING_TONES = {
  good: 'text-ok-500',
  mid: 'text-warn-500',
  bad: 'text-bad-500',
} as const

/**
 * Bewertung als Zahl mit Stern, farblich abgestuft.
 *
 * Ganz neue Titel haben bei TMDB oft noch keine Stimmen. Statt das Abzeichen
 * dann wegzulassen (dann fehlt es auf den meisten Kacheln), steht dort ein
 * Strich - so ist erkennbar, dass es keine Bewertung gibt, statt dass die
 * Anzeige defekt wirkt.
 */
export function RatingBadge({ vote, count }: { vote: number; count?: number }) {
  const { t } = useTranslation()

  if (!vote || !count) {
    return (
      <span
        title={t('media.unrated')}
        className="inline-flex items-center gap-1 rounded-full bg-ink-950/80 px-2 py-1 text-xs font-semibold text-mist-600 ring-1 ring-ink-700 backdrop-blur-sm"
      >
        <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">
          <path d="M10 1.6l2.6 5.2 5.8.8-4.2 4.1 1 5.7-5.2-2.7-5.2 2.7 1-5.7L1.6 7.6l5.8-.8L10 1.6Z" />
        </svg>
        –
      </span>
    )
  }

  return (
    <span
      className={
        'inline-flex items-center gap-1 rounded-full bg-ink-950/80 px-2 py-1 text-xs ' +
        'font-semibold ring-1 ring-ink-700 backdrop-blur-sm ' +
        RATING_TONES[ratingTone(vote)]
      }
      title={count ? `${vote.toFixed(1)} (${count})` : undefined}
    >
      <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">
        <path d="M10 1.6l2.6 5.2 5.8.8-4.2 4.1 1 5.7-5.2-2.7-5.2 2.7 1-5.7L1.6 7.6l5.8-.8L10 1.6Z" />
      </svg>
      {vote.toFixed(1)}
    </span>
  )
}
