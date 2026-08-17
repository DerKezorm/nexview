import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { MediaItem } from '../../api/types'
import { formatDate, formatRuntime } from '../../lib/format'
import { Button } from '../ui'
import { AddRequestForm } from './AddRequestForm'
import { Poster, RatingBadge } from './Poster'
import { StatusBadge } from './StatusBadge'

type DetailModalProps = {
  item: MediaItem | null
  onClose: () => void
  /** Ist Radarr (Filme) bzw. Sonarr (Serien) eingerichtet? */
  arrConfigured: boolean
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] font-medium tracking-wide text-mist-600 uppercase">{label}</dt>
      <dd className="mt-0.5 text-sm text-mist-300">{value}</dd>
    </div>
  )
}

/** Großes Detailfenster mit Hintergrundbild, Handlung und Eckdaten. */
export function DetailModal({ item, onClose, arrConfigured }: DetailModalProps) {
  const { t, i18n } = useTranslation()
  const closeRef = useRef<HTMLButtonElement>(null)
  const [adding, setAdding] = useState(false)

  // Beim Wechsel auf einen anderen Titel das Formular wieder einklappen.
  useEffect(() => setAdding(false), [item?.tmdb_id])

  // Escape schließt, und solange offen wird die Seite dahinter nicht gescrollt.
  useEffect(() => {
    if (!item) return

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }

    document.addEventListener('keydown', onKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [item, onClose])

  if (!item) return null

  const runtime = formatRuntime(item.runtime_minutes, i18n.language)
  const runtimeLabel = item.media_type === 'tv' ? t('media.episodeLength') : t('media.runtime')

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink-950/80 p-4 backdrop-blur-sm sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={item.title}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="relative w-full max-w-3xl overflow-hidden rounded-2xl border border-ink-700 bg-ink-850 shadow-2xl shadow-black/60">
        {item.backdrop_url && (
          <div className="relative h-40 sm:h-56">
            <img src={item.backdrop_url} alt="" className="h-full w-full object-cover" />
            <div className="absolute inset-0 bg-linear-to-t from-ink-850 via-ink-850/50 to-transparent" />
          </div>
        )}

        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label={t('media.close')}
          className="absolute top-3 right-3 rounded-full border border-ink-700 bg-ink-950/80 px-3 py-1.5 text-sm text-mist-300 backdrop-blur transition-colors hover:border-accent-600 hover:text-accent-400"
        >
          ✕
        </button>

        <div className={'flex gap-5 p-5 sm:p-6 ' + (item.backdrop_url ? '-mt-16 sm:-mt-24' : '')}>
          {/* self-start ist wichtig: sonst zieht die Flexbox das Poster auf die
              Höhe des Textes daneben und das Seitenverhältnis 2:3 wird
              überschrieben - je mehr Text, desto verzerrter das Cover. */}
          <div className="relative hidden aspect-2/3 w-32 shrink-0 self-start overflow-hidden rounded-xl border border-ink-700 bg-ink-900 sm:block">
            <Poster url={item.poster_url} title={item.title} />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={item.status} />
              <RatingBadge vote={item.vote_average} count={item.vote_count} />
            </div>

            <h2 className="mt-2 text-2xl leading-tight font-bold tracking-tight">{item.title}</h2>
            {item.original_title && item.original_title !== item.title && (
              <p className="text-sm text-mist-600">{item.original_title}</p>
            )}

            <p className="mt-3 text-sm leading-relaxed text-mist-300">
              {item.overview || t('media.noOverview')}
            </p>

            <dl className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Fact label={t('media.released')} value={formatDate(item.release_date, i18n.language)} />
              {runtime && <Fact label={runtimeLabel} value={runtime} />}
              {item.certification && (
                <Fact label={t('media.certification')} value={item.certification} />
              )}
              {item.genres.length > 0 && (
                <Fact label={t('media.genre')} value={item.genres.join(', ')} />
              )}
            </dl>

            <div className="mt-5">
              {item.status === 'not_requested' ? (
                adding ? (
                  <AddRequestForm item={item} onDone={onClose} />
                ) : (
                  <Button
                    type="button"
                    onClick={() => setAdding(true)}
                    disabled={!arrConfigured}
                    title={arrConfigured ? undefined : t('request.arrMissing')}
                  >
                    {t(item.media_type === 'movie' ? 'request.addMovie' : 'request.addSeries')}
                  </Button>
                )
              ) : (
                <p className="text-sm text-mist-500">{t(`request.state.${item.status}`)}</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
